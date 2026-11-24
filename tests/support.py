import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from capture.intake import collect
from device.recording import Recording, load_recording
from device.source_pages import PageRef, SourcePage
from workbench import EXAMPLES, initialize_from_file, open_archive, open_device


class WorkbenchCase(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix='pump-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.archive_path = self.directory / 'station.sqlite'
        self.db = open_archive(self.archive_path)
        self.addCleanup(self.db.close)
        initialize_from_file(self.db, EXAMPLES / 'rounds.json')

    def reopen_archive(self):
        self.db.close()
        self.db = open_archive(self.archive_path)
        self.addCleanup(self.db.close)
        return self.db

    def start_device(self, fixture='shift.json', schedule=None, name='recorder.sqlite'):
        recording = fixture if isinstance(fixture, Recording) else load_recording(EXAMPLES / fixture)
        device = open_device(self.directory / name, recording, schedule)
        self.addCleanup(device.close)
        return device

    def reopen_device(self, device, name='recorder.sqlite'):
        recording, schedule = device.recording, device.delivery
        device.close()
        return self.start_device(recording, schedule, name)

    def drain(self, device, cycle, channel):
        collected = []
        while device.read_current(cycle, channel) is not None:
            result = collect(self.db, device, cycle, channel)
            self.assertIsNotNone(result)
            self.assertEqual(result.progress, 'released')
            collected.append(result.event.page)
        return collected

    def archive_counts(self):
        return {
            'results': self.db.execute('SELECT COUNT(*) FROM signed_results').fetchone()[0],
            'receipts': self.db.execute('SELECT COUNT(*) FROM first_receipts').fetchone()[0],
        }

    def report(self, case_id, data, expected, actual, downstream_expected, downstream_actual,
               code_paths, oracle):
        self.assertEqual(actual, expected)
        self.assertEqual(downstream_actual, downstream_expected)
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': case_id, 'input': data, 'expected': expected, 'actual': actual,
            'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
            'code_paths': code_paths, 'oracle': oracle,
        }, ensure_ascii=False))


def page(channel, sequence, tick, value, *, kind='reading', cycle='R'):
    return SourcePage(PageRef(cycle, channel, sequence, f'{channel}-{sequence}'),
                      kind, tick, value, f'bench/{cycle}/{channel}/{sequence}')
