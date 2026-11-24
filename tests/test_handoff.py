import sqlite3
import threading
from collections import deque
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from audit.channel_history import read_channel_history
from audit.reconcile import reconcile_result
from audit.retrieval import retrieve_result
from capture.custody import ReleaseProgress
from capture.intake import collect
from device.delivery import DeliverySchedule
from device.release_log import ReleaseOutcome, lookup_release
from device.slots import DeviceSlots
from handoff import bind_terminal, export_cycle, import_cycle, retrieve_issued
from handoff_fixture import ObservedDevice, ReleaseGate, handoff_recording, prepare_before_a2
from rounds.registration import open_cycle
from signing.commit import sign_window
from tests.support import WorkbenchCase
from workbench import open_archive, open_device

OLD = 'T-OLD'
NEW = 'T-NEW'


class HandoffCase(WorkbenchCase):
    """换端用例的公共夹具：旧端已收 A1、乙表齐备，甲表停在 A2。"""

    def setUp(self):
        temporary = TemporaryDirectory(prefix='pump-handoff-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.leaving = open_archive(self.directory / 'leaving.sqlite')
        self.addCleanup(self.leaving.close)
        self.receiving = open_archive(self.directory / 'receiving.sqlite')
        self.addCleanup(self.receiving.close)
        self.recording = handoff_recording()
        self.schedule = DeliverySchedule()
        self.device_path = self.directory / 'recorder.sqlite'
        self.device = open_device(self.device_path, self.recording, self.schedule)
        self.addCleanup(self.device.close)
        bind_terminal(self.leaving, OLD)
        bind_terminal(self.receiving, NEW)

    def prepare(self):
        self.receiving_existing = prepare_before_a2(self.leaving, self.receiving, self.device)
        self.a2 = self.device.read_current('R', 'T1')
        return self.a2

    def lose_a2_ack(self):
        self.schedule.replies[self.a2.ref] = deque([False])

    def stop_a2_release(self):
        self.schedule.requests[self.a2.ref] = deque([ReleaseOutcome.NOT_APPLIED])

    def prepare_a2(self, *, lost_ack=True):
        a2 = self.prepare()
        (self.lose_a2_ack if lost_ack else self.stop_a2_release)()
        return a2, collect(self.leaving, self.device, 'R', 'T1', a2)

    def old_receipt(self):
        result = sign_window(self.leaving, 'R', 10, 12).result
        self.assertIsNotNone(result)
        return result


class HandoffTests(HandoffCase):
    def test_lost_ack_handoff_resume_and_reissue(self):
        a2 = self.prepare()
        self.lose_a2_ack()
        collected = collect(self.leaving, self.device, 'R', 'T1', a2)
        self.assertEqual(collected.progress, ReleaseProgress.UNKNOWN)
        old = self.old_receipt()

        bundle = export_cycle(self.leaving, 'R', NEW)
        self.assertIsInstance(bundle, bytes)
        # 交出后旧端停收、停发新放页授权、停发新签。
        def releases():
            return self.device.connection.execute(
                "SELECT COUNT(*) FROM device_releases WHERE cycle = 'R' AND channel = 'T1'"
            ).fetchone()[0]
        before = releases()
        with self.assertRaises(ValueError):
            collect(self.leaving, self.device, 'R', 'T1')
        with self.assertRaises(ValueError):
            collect(self.leaving, self.device, 'R', 'T1', a2.ref)
        with self.assertRaises(ValueError):
            collect(self.leaving, self.device, 'R', 'T2')
        self.assertEqual(releases(), before)
        with self.assertRaises(ValueError):
            sign_window(self.leaving, 'R', 12, 14)
        self.assertEqual(sign_window(self.leaving, 'R', 10, 12).result, old)
        self.assertEqual(retrieve_result(self.leaving, old.result_id), old)
        # 同参开轮不恢复办理权。
        open_cycle(self.leaving, 'R', 'T1', 'T2', 10, 16)
        with self.assertRaises(ValueError):
            collect(self.leaving, self.device, 'R', 'T1')
        # 交出失答后重领同件。
        self.assertEqual(export_cycle(self.leaving, 'R', NEW), bundle)

        handoff_id = import_cycle(self.receiving, bundle)
        self.assertTrue(handoff_id)
        resumed = collect(self.receiving, self.device, 'R', 'T1', a2.ref)
        self.assertEqual(resumed.progress, ReleaseProgress.RELEASED)
        self.assertEqual([item.ref.page_id for item in read_channel_history(self.receiving, 'R', 'T1')],
                         ['A1', 'A2'])
        tail = []
        while (item := collect(self.receiving, self.device, 'R', 'T1')) is not None:
            tail.append(item.event.page.ref.page_id)
        self.assertEqual(tail, ['A3'])
        again = sign_window(self.receiving, 'R', 10, 12)
        self.assertEqual((again.issuer, again.result), (OLD, old))
        fresh = sign_window(self.receiving, 'R', 12, 14).result
        self.assertIsNotNone(fresh)
        self.assertEqual((fresh.delta_kpa, fresh.tick), (2, 12))
        self.assertEqual(sign_window(self.receiving, 'R', 12, 14).issuer, NEW)

        # 重投不倒退：再次导入同件只复领标识，已取得进度继续有效。
        self.assertEqual(import_cycle(self.receiving, bundle), handoff_id)
        self.assertEqual([item.ref.page_id for item in read_channel_history(self.receiving, 'R', 'T1')],
                         ['A1', 'A2'])
        self.assertEqual(collect(self.receiving, self.device, 'R', 'T1', a2.ref).progress,
                         ReleaseProgress.RELEASED)

        # 关设备后按签发端领取：两端各有 1 号成果。
        self.device.close()
        with closing(open_archive(self.directory / 'receiving.sqlite')) as check:
            old_copy = retrieve_issued(check, OLD, 1)
            own_copy = retrieve_issued(check, NEW, 1)
            new_copy = retrieve_issued(check, NEW, fresh.result_id)
            self.assertEqual(old_copy, old)
            self.assertEqual(own_copy, self.receiving_existing)
            self.assertEqual(new_copy, fresh)
            self.assertIsNone(retrieve_issued(check, OLD, fresh.result_id))
            self.assertIsNone(retrieve_issued(check, 'T-OTHER', 1))
            self.assertEqual(retrieve_result(check, self.receiving_existing.result_id),
                             self.receiving_existing)
            self.assertEqual(retrieve_result(check, old.result_id), self.receiving_existing)
            self.assertEqual([reconcile_result(item).valid
                              for item in (old_copy, own_copy, new_copy)], [True, True, True])
        with closing(open_archive(self.directory / 'leaving.sqlite')) as old_check:
            self.assertEqual(retrieve_result(old_check, old.result_id), old)
            self.assertEqual(sign_window(old_check, 'R', 10, 12).result, old)
            self.assertIsNone(retrieve_issued(old_check, NEW, 1))

    def test_confirmed_not_released_resumes_by_identity(self):
        a2, collected = self.prepare_a2(lost_ack=False)
        self.assertEqual(collected.progress, ReleaseProgress.NOT_APPLIED)
        self.old_receipt()
        bundle = export_cycle(self.leaving, 'R', NEW)
        import_cycle(self.receiving, bundle)
        resumed = collect(self.receiving, self.device, 'R', 'T1', a2.ref)
        self.assertEqual(resumed.progress, ReleaseProgress.RELEASED)
        self.assertEqual(lookup_release(self.device.connection, a2.ref), ReleaseOutcome.RELEASED)
        self.assertEqual(self.device.read_current('R', 'T1').ref.page_id, 'A3')
        tail = [item.event.page.ref.page_id
                for item in iter(lambda: collect(self.receiving, self.device, 'R', 'T1'), None)]
        self.assertEqual(tail, ['A3'])
        fresh = sign_window(self.receiving, 'R', 12, 14).result
        self.assertEqual((fresh.tick, fresh.delta_kpa, fresh.a.reading.ref.page_id), (12, 2, 'A2'))
        self.assertEqual(reconcile_result(retrieve_issued(self.receiving, NEW, fresh.result_id)).valid,
                         True)

    def test_import_rollback_leaves_nothing(self):
        self.prepare_a2()
        self.old_receipt()
        bundle = export_cycle(self.leaving, 'R', NEW)
        with self.assertRaises(sqlite3.OperationalError):
            import_cycle(self.receiving, bundle, fail_at='before_import_commit')
        with closing(open_archive(self.directory / 'receiving.sqlite')) as check:
            self.assertEqual(check.execute('SELECT COUNT(*) FROM cycle_registry WHERE cycle = ?',
                                           ('R',)).fetchone()[0], 0)
            self.assertEqual(check.execute('SELECT COUNT(*) FROM handoff_inbound').fetchone()[0], 0)
            self.assertEqual(check.execute(
                "SELECT COUNT(*) FROM accepted_events WHERE cycle = 'R'").fetchone()[0], 0)
            self.assertEqual(check.execute(
                "SELECT COUNT(*) FROM signed_results WHERE cycle = 'R'").fetchone()[0], 0)
        self.assertEqual(import_cycle(self.receiving, bundle), import_cycle(self.receiving, bundle))

    def test_reject_wrong_recipient_and_conflicts(self):
        self.prepare_a2()
        self.old_receipt()
        bundle = export_cycle(self.leaving, 'R', NEW)

        with closing(open_archive(self.directory / 'third.sqlite')) as third:
            bind_terminal(third, 'T-THIRD')
            with self.assertRaises(ValueError):
                import_cycle(third, bundle)

        # 接收端已有独立登记的同号轮：即使甲乙表和范围相同也冲突。
        open_cycle(self.receiving, 'R', 'T1', 'T2', 10, 16)
        with self.assertRaises(ValueError):
            import_cycle(self.receiving, bundle)

    def test_export_argument_guards(self):
        self.prepare_a2()
        self.old_receipt()
        with self.assertRaises(ValueError):
            export_cycle(self.leaving, 'R', OLD)
        with self.assertRaises(ValueError):
            export_cycle(self.leaving, 'missing', NEW)
        bundle = export_cycle(self.leaving, 'R', NEW)
        with self.assertRaises(ValueError):
            export_cycle(self.leaving, 'R', 'T-OTHER')
        with self.assertRaises(ValueError):
            bind_terminal(self.leaving, 'T-CHANGED')
        bind_terminal(self.leaving, OLD)
        # 已接班的轮不能再次交出（换到接收端库上尝试）。
        import_cycle(self.receiving, bundle)
        with self.assertRaises(ValueError):
            export_cycle(self.receiving, 'R', 'T-THIRD')

    def test_export_failure_before_commit_leaves_old_active(self):
        self.prepare_a2()
        self.old_receipt()
        with self.assertRaises(sqlite3.OperationalError):
            export_cycle(self.leaving, 'R', NEW, fail_at='before_handoff_commit')
        with closing(open_archive(self.directory / 'leaving.sqlite')) as check:
            self.assertEqual(check.execute('SELECT COUNT(*) FROM handoff_outbound').fetchone()[0], 0)
        # 未提交时旧端仍可办理。
        a3 = self.device.read_current('R', 'T1')
        self.assertEqual(collect(self.leaving, self.device, 'R', 'T1', a3).progress,
                         ReleaseProgress.RELEASED)
        bundle = export_cycle(self.leaving, 'R', NEW)
        self.assertIsInstance(bundle, bytes)
        # 交出时冻结的是原页和已签成果；续收的 A3 仍是普通读数以外的轮末，不再有新签。
        self.assertEqual([item.ref.page_id for item in read_channel_history(self.leaving, 'R', 'T1')],
                         ['A1', 'A2'])
        self.assertIsNone(self.device.read_current('R', 'T1'))
        with self.assertRaises(ValueError):
            sign_window(self.leaving, 'R', 12, 14)

    def test_lost_export_reply_reselected_after_reopen(self):
        self.prepare_a2()
        self.old_receipt()
        bundle = export_cycle(self.leaving, 'R', NEW)
        # 模拟交出提交成功但返回失答：丢掉返回值并从旧库重取。
        self.leaving.close()
        self.leaving = open_archive(self.directory / 'leaving.sqlite')
        self.addCleanup(self.leaving.close)
        again = export_cycle(self.leaving, 'R', NEW)
        self.assertEqual(again, bundle)
        with self.assertRaises(ValueError):
            collect(self.leaving, self.device, 'R', 'T1')
        self.assertEqual(import_cycle(self.receiving, bundle), import_cycle(self.receiving, again))

    def test_duplicate_import_after_progress_does_not_roll_back(self):
        self.prepare_a2()
        self.old_receipt()
        bundle = export_cycle(self.leaving, 'R', NEW)
        identifier = import_cycle(self.receiving, bundle)
        collect(self.receiving, self.device, 'R', 'T1')
        self.assertEqual(import_cycle(self.receiving, bundle), identifier)
        history = read_channel_history(self.receiving, 'R', 'T1')
        self.assertEqual([item.ref.page_id for item in history], ['A1', 'A2'])
        self.assertEqual([item.ref.page_id for item in read_channel_history(self.receiving, 'R', 'T2')],
                         ['B1'])

    def test_import_rejects_tampered_bundle(self):
        self.prepare_a2()
        self.old_receipt()
        bundle = export_cycle(self.leaving, 'R', NEW)
        tampered = bundle.replace(b'"sender":"T-OLD"', b'"sender":"T-EVIL"')
        self.assertNotEqual(tampered, bundle)
        with self.assertRaises(ValueError):
            import_cycle(self.receiving, tampered)
        with self.assertRaises(ValueError):
            import_cycle(self.receiving, b'not json')

    def test_handoff_takes_over_authorized_in_flight_release(self):
        # 旧端 A2 的放页在切点前已获授权；交出可在其执行中冻结。
        a2 = self.prepare()
        gate = ReleaseGate(a2.ref, 'before_reply')
        observed_connection = sqlite3.connect(self.device_path, check_same_thread=False)
        self.addCleanup(observed_connection.close)
        observed = ObservedDevice(DeviceSlots(observed_connection, self.recording, self.schedule), gate)
        writer = sqlite3.connect(self.directory / 'leaving.sqlite', check_same_thread=False)
        self.addCleanup(writer.close)
        outcome = {}

        def worker():
            try:
                outcome['progress'] = collect(writer, observed, 'R', 'T1', a2).progress
            except BaseException as error:  # pragma: no cover - 诊断用
                outcome['error'] = error

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(gate.reached.wait(10))
        # A2 已在旧库实收，旧端据此签出 [10,12) 的 4 kPa。
        old = self.old_receipt()
        bundle = export_cycle(self.leaving, 'R', NEW)
        self.assertIsInstance(bundle, bytes)
        # 交出后该在途操作仍可完成，且仅作用于交接件内对应的原页。
        gate.proceed.set()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertNotIn('error', outcome)
        self.assertEqual(outcome.get('progress'), ReleaseProgress.RELEASED)
        self.assertEqual(lookup_release(self.device.connection, a2.ref), ReleaseOutcome.RELEASED)
        # 迟到的旧端回包不改变冻结件：旧库仍停在切点，旧成果仍可复领。
        self.assertEqual([item.ref.page_id for item in read_channel_history(self.leaving, 'R', 'T1')],
                         ['A1', 'A2'])
        self.assertEqual(retrieve_result(self.leaving, old.result_id), old)
        with self.assertRaises(ValueError):
            collect(self.leaving, self.device, 'R', 'T1')
        with self.assertRaises(ValueError):
            sign_window(self.leaving, 'R', 12, 14)

        # 接班端用原记录器按 A2 身份查结，再续收 A3。
        handoff_id = import_cycle(self.receiving, bundle)
        self.assertTrue(handoff_id)
        self.assertEqual(collect(self.receiving, self.device, 'R', 'T1', a2.ref).progress,
                         ReleaseProgress.RELEASED)
        self.assertEqual(self.device.read_current('R', 'T1').ref.page_id, 'A3')
        tail = []
        while (item := collect(self.receiving, self.device, 'R', 'T1')) is not None:
            tail.append(item.event.page.ref.page_id)
        self.assertEqual(tail, ['A3'])
        fresh = sign_window(self.receiving, 'R', 12, 14).result
        self.assertEqual((fresh.tick, fresh.delta_kpa), (12, 2))
        self.assertEqual(sign_window(self.receiving, 'R', 10, 12).result, old)

    def test_v1_archive_migrates_into_terminal_domain(self):
        # V1 单端账没有签发端列；打开时应就地升级，已有成果归入空编号域，
        # 绑定终端后归入本端编号域，仍可按原整数入口领取。
        path = self.directory / 'legacy.sqlite'
        legacy = sqlite3.connect(path)
        legacy.executescript('''
            CREATE TABLE signed_results (
                result_id INTEGER PRIMARY KEY, cycle TEXT NOT NULL,
                start_tick TEXT NOT NULL, stop_tick TEXT NOT NULL,
                channel_a TEXT NOT NULL, channel_b TEXT NOT NULL,
                witness_tick TEXT NOT NULL, delta TEXT NOT NULL,
                evidence_a TEXT NOT NULL, evidence_b TEXT NOT NULL,
                UNIQUE (cycle, start_tick, stop_tick)
            );
            CREATE TABLE first_receipts (
                result_id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE
            );
            CREATE TABLE cycle_registry (
                cycle TEXT PRIMARY KEY NOT NULL, channel_a TEXT NOT NULL,
                channel_b TEXT NOT NULL, start_tick TEXT NOT NULL, stop_tick TEXT NOT NULL
            );
            INSERT INTO cycle_registry VALUES ('R', 'T1', 'T2', '0xa', '0x10');
        ''')
        legacy.commit()
        legacy.close()
        with closing(open_archive(path)) as migrated:
            columns = {row[1] for row in migrated.execute('PRAGMA table_info(signed_results)')}
            self.assertIn('issuer', columns)
            bind_terminal(migrated, 'T-LEGACY')
            self.assertEqual(migrated.execute(
                'SELECT issuer FROM signed_results').fetchall(), [])

    def test_bind_terminal_semantics(self):
        with closing(open_archive(self.directory / 'plain.sqlite')) as plain:
            bind_terminal(plain, 'T1')
            bind_terminal(plain, 'T1')
            with self.assertRaises(ValueError):
                bind_terminal(plain, 'T2')
            for invalid in ('', None, 1):
                with self.assertRaises(ValueError):
                    bind_terminal(plain, invalid)

    def test_existing_results_join_terminal_domain(self):
        # 未配置的库仍可作普通单端巡检；绑定后本库已有签件归入本端编号域。
        plain = open_archive(self.directory / 'plain.sqlite')
        self.addCleanup(plain.close)
        open_cycle(plain, 'R', 'T1', 'T2', 10, 16)
        device = open_device(self.directory / 'plain-device.sqlite', self.recording)
        self.addCleanup(device.close)
        while (item := collect(plain, device, 'R', 'T1')) is not None:
            pass
        while (item := collect(plain, device, 'R', 'T2')) is not None:
            pass
        early = sign_window(plain, 'R', 10, 12).result
        self.assertIsNotNone(early)
        self.assertEqual(retrieve_result(plain, early.result_id), early)
        with self.assertRaises(ValueError):
            export_cycle(plain, 'R', NEW)
        bind_terminal(plain, 'T-PLAIN')
        self.assertEqual(retrieve_issued(plain, 'T-PLAIN', early.result_id), early)
        self.assertIsNone(retrieve_issued(plain, OLD, early.result_id))
        self.assertEqual(retrieve_result(plain, early.result_id), early)
