import sqlite3
from collections import deque

from audit.channel_history import read_channel_history
from audit.reconcile import reconcile_result
from audit.retrieval import retrieve_result
from capture.custody import ReleaseProgress
from capture.intake import collect
from device.delivery import DeliverySchedule
from device.recording import Recording, load_recording
from device.release_log import ReleaseOutcome, lookup_release
from signing.commit import sign_window
from tests.support import WorkbenchCase, page
from workbench import EXAMPLES


class SigningTests(WorkbenchCase):
    def test_pair_terminal_evidence_and_retry(self):
        device = self.start_device()
        self.drain(device, 'R', 'T1')
        self.drain(device, 'R', 'T2')
        outcome = sign_window(self.db, 'R', 12, 14)
        self.assertEqual(outcome.status, 'signed')
        result = outcome.result
        self.assertIn(result.tick, (12, 13))
        self.assertEqual((result.a.reading.value, result.b.reading.value), (9, 11))
        self.assertEqual((result.a.reading.provenance, result.b.reading.provenance),
                         ('early/t1/2', 'early/t2/1'))
        for evidence, closing_page, provenance in [(result.a, 'A3', 'early/t1/end'),
                                                   (result.b, 'B2', 'early/t2/end')]:
            with self.subTest(closing_page=closing_page):
                self.assertEqual(evidence.closing.ref.page_id, closing_page)
                self.assertEqual(evidence.closing.provenance, provenance)
                self.assertEqual((evidence.closing.kind, evidence.closing.tick), ('terminal', 14))
                for source_page in (evidence.reading, evidence.closing):
                    self.assertEqual(lookup_release(device.connection, source_page.ref), ReleaseOutcome.RELEASED)
        self.assertEqual(sign_window(self.db, 'R', 12, 14), outcome)
        self.assertEqual(retrieve_result(self.db, result.result_id), result)
        self.assertTrue(reconcile_result(result).valid)
        self.report('v_pair', {'window': [12, 14], 'pressures_kpa': [9, 11]},
                    {'delta_kpa': 2, 'eligible_tick': True, 'right_pages': ['A3', 'B2']},
                    {'delta_kpa': result.delta_kpa, 'eligible_tick': result.tick in (12, 13),
                     'right_pages': [result.a.closing.ref.page_id, result.b.closing.ref.page_id]},
                    {'results': 1, 'receipts': 1}, self.archive_counts(),
                    ['signing/coverage.py', 'signing/pairing.py', 'signing/commit.py'],
                    '两路轮末均为 14，整数同刻为 12 或 13，11 减 9 得 2。')

    def test_pending_then_late_completion(self):
        recording = load_recording(EXAMPLES / 'shift.json')
        first = recording.page_at('R', 'T1', 0)
        schedule = DeliverySchedule(visible_counts={('R', 'T1'): 1}, replies={first.ref: deque([False])})
        device = self.start_device(recording, schedule)
        accepted = collect(self.db, device, 'R', 'T1', first)
        self.assertEqual(accepted.progress, ReleaseProgress.UNKNOWN)
        self.drain(device, 'R', 'T2')
        self.assertIsNone(device.read_current('R', 'T1'))
        pending = sign_window(self.db, 'R', 10, 14)
        self.assertEqual(pending.status, 'pending')
        self.assertIsNone(pending.result)
        self.assertEqual(self.archive_counts(), {'results': 0, 'receipts': 0})
        self.assertEqual(sign_window(self.db, 'R', 10, 12).status, 'disjoint')
        self.reopen_archive()
        device = self.reopen_device(device)
        self.assertEqual(collect(self.db, device, 'R', 'T1', first.ref).progress, ReleaseProgress.RELEASED)
        self.assertEqual([item.value for item in read_channel_history(self.db, 'R', 'T1')], [7])
        schedule.visible_counts['R', 'T1'] = 3
        self.drain(device, 'R', 'T1')
        after = sign_window(self.db, 'R', 10, 14)
        self.assertEqual(after.status, 'signed')
        self.assertIn(after.result.tick, (12, 13))
        self.assertEqual((after.result.a.reading.ref.page_id, after.result.b.reading.ref.page_id), ('A2', 'B1'))
        self.assertEqual((after.result.a.closing.ref.page_id, after.result.b.closing.ref.page_id), ('A3', 'B2'))
        device.close()
        self.reopen_archive()
        saved = retrieve_result(self.db, after.result.result_id)
        self.assertEqual(saved, after.result)
        self.report('v_pending', {'schedule': ['A1', 'B1', 'B2', 'query', 'reopen', 'A2', 'A3'],
                                   'window': [10, 14]},
                    ['pending', 'signed', 2], [pending.status, after.status, after.result.delta_kpa],
                    True, reconcile_result(saved).valid,
                    ['capture/resume.py', 'capture/ledger.py', 'signing/coverage.py', 'signing/commit.py',
                     'audit/reconcile.py'],
                    '甲旧读在 12 前终止，甲新读与乙读共同覆盖 12 和 13。')

    def test_disjoint_touch_at_twelve(self):
        device = self.start_device('touch.json')
        self.drain(device, 'R', 'T1')
        self.drain(device, 'R', 'T2')
        outcome = sign_window(self.db, 'R', 10, 14)
        self.assertIsNone(outcome.result)
        self.assertEqual(sign_window(self.db, 'R', 10, 12).status, 'disjoint')
        self.report('v_disjoint', {'a': [10, 12], 'b': [12, 14], 'query': [10, 14]},
                    'disjoint', outcome.status, {'results': 0, 'receipts': 0}, self.archive_counts(),
                    ['signing/coverage.py', 'signing/pairing.py', 'signing/commit.py'],
                    '甲只覆盖 10、11，乙只覆盖 12、13。')

    def test_equal_value_has_new_source(self):
        device = self.start_device('equal.json')
        self.drain(device, 'R', 'T1')
        self.drain(device, 'R', 'T2')
        result = sign_window(self.db, 'R', 12, 14).result
        self.assertIsNotNone(result)
        self.assertIn(result.tick, (12, 13))
        self.assertEqual(result.a.reading.provenance, 'equal/t1/2')
        self.assertEqual([entry.value for entry in read_channel_history(self.db, 'R', 'T1')], [7, 7])
        saved = retrieve_result(self.db, result.result_id)
        self.report('v_equal', {'a_reads': [['A1', 10, 7], ['A2', 12, 7]], 'window': [12, 14]},
                    ['A2', 'A3', 4], [result.a.reading.ref.page_id, result.a.closing.ref.page_id, result.delta_kpa],
                    True, reconcile_result(saved).valid,
                    ['signing/coverage.py', 'signing/evidence.py', 'audit/provenance.py'],
                    '压力值相等，A1 在刻度 12 已失效，故只能用 A2 和 A3。')

    def test_window_scope(self):
        device = self.start_device()
        self.drain(device, 'R', 'T1')
        self.drain(device, 'R', 'T2')
        large = sign_window(self.db, 'R', 1, 18)
        self.assertEqual(large.status, 'signed')
        self.assertEqual((large.result.window.start, large.result.window.stop), (1, 18))
        self.assertIn(large.result.tick, (12, 13))
        narrow = sign_window(self.db, 'R', 10, 16)
        self.assertEqual(narrow.status, 'signed')
        self.assertNotEqual(large.result.result_id, narrow.result.result_id)
        for start, stop in ((12, 12), (0, 10), (16, 20)):
            with self.subTest(window=(start, stop)):
                self.assertEqual(sign_window(self.db, 'R', start, stop).status, 'disjoint')
        for cycle, start, stop in (('R', 14, 12), ('R', True, 14), ('R', 10, 14.0), ('unknown', 10, 14)):
            with self.subTest(request=(cycle, start, stop)), self.assertRaises(ValueError):
                sign_window(self.db, cycle, start, stop)
        self.assertEqual(self.archive_counts(), {'results': 2, 'receipts': 2})

    def test_receipt_failure_atomic(self):
        device = self.start_device()
        self.drain(device, 'R', 'T1')
        self.drain(device, 'R', 'T2')
        for failure in ('before_receipt', 'before_sign_commit'):
            with self.subTest(failure=failure), self.assertRaises(sqlite3.OperationalError):
                sign_window(self.db, 'R', 12, 14, fail_at=failure)
            self.reopen_archive()
            self.assertIsNone(retrieve_result(self.db, 1))
            self.assertEqual(self.archive_counts(), {'results': 0, 'receipts': 0})
        rolled_back = self.archive_counts()
        device.close()
        result = sign_window(self.db, 'R', 12, 14).result
        self.assertIsNotNone(result)
        self.assertEqual(result.delta_kpa, 2)
        self.assertEqual(retrieve_result(self.db, result.result_id), result)
        self.report('v_commit', {'window': [12, 14], 'failures': ['before_receipt', 'before_sign_commit']},
                    {'results': 0, 'receipts': 0}, rolled_back,
                    {'results': 1, 'receipts': 1}, self.archive_counts(),
                    ['signing/commit.py', 'audit/retrieval.py'],
                    '失败点都在一次事务内，重试使用原据只生成一套可见成果。')

    def test_same_tick_replacement_and_exact_zero(self):
        reading = 2**80
        device = self.start_device(Recording([
            page('T1', 1, 12, 3), page('T1', 2, 12, reading), page('T1', 3, 14, None, kind='terminal'),
            page('T2', 1, 12, reading), page('T2', 2, 14, None, kind='terminal'),
        ]))
        self.drain(device, 'R', 'T1')
        self.drain(device, 'R', 'T2')
        result = sign_window(self.db, 'R', 12, 13).result
        self.assertIsNotNone(result)
        self.assertEqual((result.tick, result.a.reading.ref.sequence, result.delta_kpa), (12, 2, 0))
        self.assertEqual(retrieve_result(self.db, result.result_id).b.reading.value, reading)
        self.assertTrue(reconcile_result(result).valid)

    def test_terminal_as_first_page_excludes_coverage(self):
        device = self.start_device(Recording([page('T1', 1, 12, None, kind='terminal')]))
        self.assertEqual(sign_window(self.db, 'R', 10, 16).status, 'pending')
        self.drain(device, 'R', 'T1')
        self.assertEqual(sign_window(self.db, 'R', 10, 16).status, 'disjoint')
        self.assertEqual(self.archive_counts(), {'results': 0, 'receipts': 0})
