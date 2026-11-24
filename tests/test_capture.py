import sqlite3
from dataclasses import replace

from audit.channel_history import read_channel_history
from capture.custody import ReleaseProgress, get_release_progress
from capture.intake import collect
from device.delivery import DeliverySchedule
from device.recording import Recording
from device.release_log import ReleaseOutcome, lookup_release
from rounds.registration import get_cycle, open_cycle
from tests.support import WorkbenchCase, page
from workbench import EXAMPLES, initialize_from_file


class CaptureTests(WorkbenchCase):
    def test_capture_reinitialize_and_fixed_roles(self):
        a1 = page('T1', 1, 10, 0)
        a2 = page('T1', 2, 11, 0)
        a3 = page('T1', 3, 14, None, kind='terminal')
        b1 = page('T2', 1, 10, -2)
        b2 = page('T2', 2, 14, None, kind='terminal')
        device = self.start_device(Recording([a1, a2, a3, b1, b2]))
        self.assertEqual(self.drain(device, 'R', 'T1'), [a1, a2, a3])
        self.assertEqual(self.drain(device, 'R', 'T2'), [b1, b2])
        self.assertEqual(lookup_release(device.connection, a3.ref), ReleaseOutcome.RELEASED)
        self.assertEqual(lookup_release(device.connection, b2.ref), ReleaseOutcome.RELEASED)
        original = get_cycle(self.db, 'R')
        original_histories = [read_channel_history(self.db, 'R', channel) for channel in ('T1', 'T2')]
        self.reopen_archive()
        self.assertIn(original, initialize_from_file(self.db, EXAMPLES / 'rounds.json'))
        for changed in [('T2', 'T1', 10, 16), ('T1', 'T3', 10, 16), ('T1', 'T2', 10, 17)]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                open_cycle(self.db, 'R', *changed)
        opened = open_cycle(self.db, 'R3', 'T2', 'T1', 11, 15)
        self.assertEqual(opened.channels.a, 'T2')
        self.assertEqual(read_channel_history(self.db, 'R3', 'T1'), ())
        self.assertEqual(get_cycle(self.db, 'R'), original)
        histories = [read_channel_history(self.db, 'R', channel) for channel in ('T1', 'T2')]
        self.assertEqual(histories, original_histories)
        self.assertEqual([(item.tick, item.provenance) for item in histories[0]],
                         [(10, 'bench/R/T1/1'), (11, 'bench/R/T1/2')])
        self.report('v_capture', {'cycle': 'R', 'values': [[0, 0], [-2]]}, [[1, 2], [1]],
                    [[entry.ref.sequence for entry in lane] for lane in histories],
                    [[0, 0], [-2]], [[entry.value for entry in lane] for lane in histories],
                    ['rounds/registration.py', 'capture/ledger.py', 'audit/channel_history.py'],
                    '按夹具列出普通页原序和值，保留零值和等值换页，排除轮末。')
        with self.assertRaises(ValueError):
            read_channel_history(self.db, 'R', 'T3')
        with self.assertRaises(ValueError):
            read_channel_history(self.db, 'missing', 'T1')

    def test_independent_slots(self):
        schedule = DeliverySchedule(visible_counts={('R', 'T2'): 0})
        device = self.start_device(schedule=schedule)
        first = device.read_current('R', 'T1')
        self.assertEqual(device.read_current('R', 'T1'), first)
        self.assertIsNone(device.read_current('R', 'T2'))
        self.assertIsNone(collect(self.db, device, 'R', 'T2'))
        result = collect(self.db, device, 'R', 'T1', first)
        self.assertEqual(result.progress, ReleaseProgress.RELEASED)
        self.assertEqual(device.read_current('R', 'T1').ref.page_id, 'A2')
        self.assertEqual(device.release_page(first.ref), ReleaseOutcome.RELEASED)
        self.assertEqual(device.read_current('R', 'T1').ref.page_id, 'A2')
        self.assertIsNone(device.read_current('R', 'T2'))
        schedule.visible_counts['R', 'T2'] = 1
        self.assertEqual(device.read_current('R', 'T2').ref.page_id, 'B1')
        self.assertEqual(device.read_current('R', 'T1').ref.page_id, 'A2')
        self.assertEqual(read_channel_history(self.db, 'R', 'T2'), ())

    def test_bad_page_keeps_slot(self):
        first = page('T1', 1, 10, 0)
        invalid = page('T1', 2, 11, True)
        device = self.start_device(Recording([first, invalid]))
        collect(self.db, device, 'R', 'T1', first)
        fabricated = replace(invalid, ref=replace(invalid.ref, page_id='fabricated'))
        with self.assertRaises(ValueError):
            device.release_page(fabricated.ref)
        for proposal in (fabricated, replace(invalid, value=1), invalid):
            with self.subTest(proposal=proposal), self.assertRaises(ValueError):
                collect(self.db, device, 'R', 'T1', proposal)
            self.assertEqual(device.read_current('R', 'T1'), invalid)
            self.assertIs(lookup_release(device.connection, invalid.ref), ReleaseOutcome.UNKNOWN)
        with self.assertRaises(ValueError):
            get_release_progress(self.db, invalid.ref)
        self.report('v_bad', {'invalid_value': True, 'page': invalid.ref.page_id},
                    invalid.ref.page_id, device.read_current('R', 'T1').ref.page_id, [0],
                    [item.value for item in read_channel_history(self.db, 'R', 'T1')],
                    ['capture/page_checks.py', 'capture/intake.py', 'device/slots.py'],
                    '布尔值不能作为 kPa 原值。第二页留槽，旧值 0 仍是唯一实收。')

    def test_bad_sequence_values_and_backward_terminal(self):
        first = page('T1', 1, 12, 8)
        bad_pages = [page('T1', 2, 13, None), page('T1', 2, 13, '8'), page('T1', 2, 11, 8),
                     page('T1', 3, 13, 8), page('T1', 2, 11, None, kind='terminal'),
                     page('T1', 2, 17, None, kind='terminal')]
        for index, invalid in enumerate(bad_pages):
            with self.subTest(invalid=invalid):
                cycle = f'bad-{index}'
                open_cycle(self.db, cycle, 'T1', 'T2', 10, 16)
                original = replace(first, ref=replace(first.ref, cycle=cycle))
                bad = replace(invalid, ref=replace(invalid.ref, cycle=cycle))
                device = self.start_device(Recording([original, bad]), name=f'device-{index}.sqlite')
                collect(self.db, device, cycle, 'T1', original)
                with self.assertRaises(ValueError):
                    collect(self.db, device, cycle, 'T1', bad)
                self.assertEqual(device.read_current(cycle, 'T1'), bad)
                self.assertEqual([item.ref for item in read_channel_history(self.db, cycle, 'T1')],
                                 [original.ref])

    def test_intake_commit_failure(self):
        device = self.start_device()
        first = device.read_current('R', 'T1')
        with self.assertRaisesRegex(sqlite3.OperationalError, '实收提交前'):
            collect(self.db, device, 'R', 'T1', first, fail_at='before_intake_commit')
        self.reopen_archive()
        self.assertEqual(device.read_current('R', 'T1'), first)
        self.assertEqual(lookup_release(device.connection, first.ref), ReleaseOutcome.UNKNOWN)
        self.assertEqual(read_channel_history(self.db, 'R', 'T1'), ())
        with self.assertRaises(ValueError):
            get_release_progress(self.db, first.ref)
        result = collect(self.db, device, 'R', 'T1', first)
        self.assertEqual(result.progress, ReleaseProgress.RELEASED)
        self.assertEqual([item.ref for item in read_channel_history(self.db, 'R', 'T1')], [first.ref])
