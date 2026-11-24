from collections import deque
from dataclasses import replace

from audit.channel_history import read_channel_history
from audit.provenance import check_interval_provenance
from audit.reconcile import reconcile_result
from audit.retrieval import retrieve_result
from capture.custody import ReleaseProgress
from capture.intake import collect
from device.delivery import DeliverySchedule
from device.recording import load_recording
from device.release_log import ReleaseOutcome, lookup_release
from signing.commit import sign_window
from tests.support import WorkbenchCase
from workbench import EXAMPLES


class ReopenTests(WorkbenchCase):
    def test_release_ack_recovery(self):
        recording = load_recording(EXAMPLES / 'shift.json')
        first = recording.page_at('R', 'T1', 0)
        second = recording.page_at('R', 'T1', 1)
        schedule = DeliverySchedule(requests={second.ref: deque([ReleaseOutcome.NOT_APPLIED])},
                                    replies={first.ref: deque([False, False])})
        device = self.start_device(recording, schedule)
        accepted = collect(self.db, device, 'R', 'T1', first)
        self.assertEqual(accepted.progress, ReleaseProgress.UNKNOWN)
        self.assertEqual(lookup_release(device.connection, first.ref), ReleaseOutcome.RELEASED)
        self.assertEqual([entry.ref for entry in read_channel_history(self.db, 'R', 'T1')], [first.ref])
        self.reopen_archive()
        device = self.reopen_device(device)
        self.assertEqual(device.read_current('R', 'T1'), second)
        queried = [collect(self.db, device, 'R', 'T1', first.ref).progress for _ in range(3)]
        self.assertEqual(queried, [ReleaseProgress.UNKNOWN, ReleaseProgress.RELEASED, ReleaseProgress.RELEASED])
        self.assertEqual(device.read_current('R', 'T1'), second)
        self.assertEqual(lookup_release(device.connection, second.ref), ReleaseOutcome.UNKNOWN)
        self.assertEqual(collect(self.db, device, 'R', 'T1', second).progress, ReleaseProgress.NOT_APPLIED)
        self.assertEqual(device.read_current('R', 'T1'), second)
        self.assertEqual(lookup_release(device.connection, second.ref), ReleaseOutcome.NOT_APPLIED)
        self.reopen_archive()
        device = self.reopen_device(device)
        self.assertEqual(collect(self.db, device, 'R', 'T1', second.ref).progress, ReleaseProgress.RELEASED)
        self.assertEqual(self.drain(device, 'R', 'T1')[0].ref.page_id, 'A3')
        releases = device.connection.execute('''
            SELECT COUNT(*) FROM device_releases WHERE cycle = 'R' AND channel = 'T1' AND outcome = 'released'
        ''').fetchone()[0]
        self.assertEqual(releases, 3)
        self.report('v_ack', {'success_ack_lost': 'A1', 'confirmed_not_applied': 'A2'},
                    ['unknown', 'released', 'released'], [status.value for status in queried],
                    ['A1', 'A2'], [item.ref.page_id for item in read_channel_history(self.db, 'R', 'T1')],
                    ['device/slots.py', 'device/release_log.py', 'capture/intake.py', 'capture/resume.py'],
                    'A1 执行一次并按旧身份查结，A2 只在未生效得到确认后续发。')

    def test_offline_old_result_after_later_round(self):
        device = self.start_device()
        self.drain(device, 'R', 'T1')
        self.drain(device, 'R', 'T2')
        earlier = sign_window(self.db, 'R', 10, 14).result
        self.assertIsNotNone(earlier)
        self.drain(device, 'R2', 'T1')
        self.drain(device, 'R2', 'T2')
        later = sign_window(self.db, 'R2', 10, 14).result
        self.assertIsNotNone(later)
        self.assertNotEqual(earlier.result_id, later.result_id)
        self.assertNotEqual(earlier.receipt, later.receipt)
        device.close()
        self.reopen_archive()
        old = retrieve_result(self.db, earlier.result_id)
        new = retrieve_result(self.db, later.result_id)
        self.assertEqual(old, earlier)
        self.assertEqual(new, later)
        self.assertEqual(sign_window(self.db, 'R', 10, 14).result, earlier)
        self.assertIsNone(retrieve_result(self.db, 999))
        self.assertEqual(old.receipt.result_id, old.result_id)
        self.assertEqual((old.a.reading.provenance, old.b.reading.provenance), ('early/t1/2', 'early/t2/1'))
        for evidence, channel in ((old.a, 'T1'), (old.b, 'T2')):
            checked = check_interval_provenance(evidence, 'R', channel)
            self.assertEqual(checked.problems, ())
            self.assertEqual((checked.window.start, checked.window.stop), (12, 14))
            self.assertEqual(evidence.closing.ref.sequence, evidence.reading.ref.sequence + 1)
        self.assertFalse(reconcile_result(replace(old, tick=14)).valid)
        self.assertFalse(reconcile_result(replace(old, receipt=later.receipt)).valid)
        self.assertFalse(reconcile_result(replace(old, a=later.a)).valid)
        self.report('v_audit', {'rounds': ['R', 'R2'], 'source_closed': True}, [2, -4],
                    [old.delta_kpa, new.delta_kpa],
                    {'old_proof': True, 'new_proof': True, 'fixed_old': True},
                    {'old_proof': reconcile_result(old).valid, 'new_proof': reconcile_result(new).valid,
                     'fixed_old': old == earlier},
                    ['audit/retrieval.py', 'audit/provenance.py', 'audit/reconcile.py', 'signing/commit.py'],
                    '原轮 11 减 9 得 2，后轮 12 减 16 得 -4，按各自归档号比对。')
