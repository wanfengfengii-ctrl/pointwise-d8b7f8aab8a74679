import argparse
import json
import sqlite3
from collections import deque
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from audit.reconcile import reconcile_result
from audit.retrieval import retrieve_result
from capture.custody import ReleaseProgress, initialize_custody
from capture.intake import collect
from capture.ledger import initialize_intake
from device.delivery import DeliverySchedule
from device.recording import Recording, load_recording
from device.slots import DeviceSlots, initialize_slots
from rounds.registration import CycleRegistration, open_cycle
from rounds.registry_store import initialize_registry
from signing.commit import initialize_signing, sign_window


EXAMPLES = Path(__file__).resolve().parent / 'examples'


def open_archive(path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    try:
        db.execute('PRAGMA foreign_keys = ON')
        initialize_registry(db)
        initialize_intake(db)
        initialize_custody(db)
        initialize_signing(db)
    except BaseException:
        db.close()
        raise
    return db


def open_device(path: str | Path, recording: Recording,
                delivery: DeliverySchedule | None = None) -> DeviceSlots:
    db = sqlite3.connect(path)
    try:
        initialize_slots(db)
    except BaseException:
        db.close()
        raise
    return DeviceSlots(db, recording, delivery)


def initialize_from_file(db: sqlite3.Connection, path: str | Path) -> tuple[CycleRegistration, ...]:
    with Path(path).open(encoding='utf-8') as stream:
        items = json.load(stream)
    if not isinstance(items, list):
        raise ValueError('初始化输入必须是巡检轮数组')
    return tuple(open_cycle(db, item['cycle'], item['a'], item['b'], item['start'], item['stop'])
                 for item in items)


def run_demo() -> None:
    recording = load_recording(EXAMPLES / 'shift.json')
    first = recording.page_at('R', 'T1', 0)
    if first is None:
        raise RuntimeError('演示录制缺少甲表首读')
    schedule = DeliverySchedule(visible_counts={('R', 'T1'): 1}, replies={first.ref: deque([False])})
    with TemporaryDirectory(prefix='pump-inspection-') as directory:
        archive_path = Path(directory) / 'station.sqlite'
        device_path = Path(directory) / 'recorder.sqlite'
        with closing(open_archive(archive_path)) as db, closing(open_device(device_path, recording, schedule)) as device:
            initialize_from_file(db, EXAMPLES / 'rounds.json')
            old_page = collect(db, device, 'R', 'T1')
            if old_page is None or old_page.progress is not ReleaseProgress.UNKNOWN:
                raise RuntimeError('期望甲表首读已接纳且释放待查结')
            while collect(db, device, 'R', 'T2') is not None:
                pass
            before = sign_window(db, 'R', 10, 14)
            if before.status != 'pending' or before.result is not None:
                raise RuntimeError(f'待证窗口返回了 {before!r}')
        with closing(open_archive(archive_path)) as db, closing(open_device(device_path, recording, schedule)) as device:
            resumed = collect(db, device, 'R', 'T1', first.ref)
            if resumed is None or resumed.progress is not ReleaseProgress.RELEASED:
                raise RuntimeError('甲表首读未按原身份续办')
            schedule.visible_counts[('R', 'T1')] = 3
            while collect(db, device, 'R', 'T1') is not None:
                pass
            after = sign_window(db, 'R', 10, 14)
            if after.result is None:
                raise RuntimeError(f'齐备窗口返回了 {after!r}')
            signed = after.result
        with closing(open_archive(archive_path)) as db:
            retrieved = retrieve_result(db, signed.result_id)
            downstream = {
                'unchanged': retrieved == signed,
                'reconciled': retrieved is not None and reconcile_result(retrieved).valid,
            }
    expected = {'before': 'pending', 'after': 'signed', 'tick': 12, 'delta_kpa': 2,
                'reading_pages': ['A2', 'B1'], 'closing_pages': ['A3', 'B2']}
    actual = {'before': before.status, 'after': after.status, 'tick': signed.tick,
              'delta_kpa': signed.delta_kpa,
              'reading_pages': [signed.a.reading.ref.page_id, signed.b.reading.ref.page_id],
              'closing_pages': [signed.a.closing.ref.page_id, signed.b.closing.ref.page_id]}
    downstream_expected = {'unchanged': True, 'reconciled': True}
    if actual != expected or downstream != downstream_expected:
        raise RuntimeError(f'同刻签存核对差异 {actual!r} {downstream!r}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v_pending.late_completion',
        'input': {'cycle': 'R', 'window': [10, 14], 'recording': 'examples/shift.json'},
        'expected': expected, 'actual': actual,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream,
        'code_paths': ['capture/intake.py', 'capture/resume.py', 'signing/coverage.py',
                       'signing/commit.py', 'audit/retrieval.py', 'audit/reconcile.py'],
        'oracle': '手算甲 A2 与乙 B1 均覆盖 [12,14)，最早见证为 12，乙减甲为 2。',
    }, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description='栖湾泵房双表巡检')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('demo', help='演练实收、签发与断源领取')
    initializer = commands.add_parser('init', help='初始化巡检库和固定轮登记')
    initializer.add_argument('archive', type=Path)
    initializer.add_argument('registrations', type=Path)
    args = parser.parse_args()
    if args.command == 'demo':
        run_demo()
    elif args.command == 'init':
        with closing(open_archive(args.archive)) as db:
            registrations = initialize_from_file(db, args.registrations)
        print(json.dumps({'registered': [item.cycle for item in registrations]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
