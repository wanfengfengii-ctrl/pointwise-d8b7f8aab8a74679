"""按 HANDOFF.md 的换端演练：交出、接班、续收与按签发端领取。"""
import json
from collections import deque
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from audit.reconcile import reconcile_result
from audit.channel_history import read_channel_history
from audit.retrieval import retrieve_result
from capture.intake import collect
from handoff import bind_terminal, export_cycle, import_cycle, retrieve_issued
from handoff_fixture import handoff_recording, prepare_before_a2
from signing.commit import sign_window
from workbench import open_archive, open_device

OLD = 'T-OLD'
NEW = 'T-NEW'


def _expect(actual: dict, expected: dict) -> None:
    if actual != expected:
        raise RuntimeError(f'换端演练差异 {actual!r}，预期 {expected!r}')


def run_drill(directory: str | Path) -> dict:
    root = Path(directory)
    recording = handoff_recording()
    with (closing(open_archive(root / 'leaving.sqlite')) as leaving,
          closing(open_archive(root / 'receiving.sqlite')) as receiving,
          closing(open_device(root / 'recorder.sqlite', recording)) as device):
        bind_terminal(leaving, OLD)
        bind_terminal(receiving, NEW)
        receiving_existing = prepare_before_a2(leaving, receiving, device)

        # 甲 A2 实收，放页动作已生效但回包丢失。
        a2 = device.read_current('R', 'T1')
        device.delivery.replies[a2.ref] = deque([False])
        a2_outcome = collect(leaving, device, 'R', 'T1', a2)
        old_result = sign_window(leaving, 'R', 10, 12).result
        if old_result is None:
            raise RuntimeError('旧端未签出 [10,12) 的 4 kPa')

        bundle = export_cycle(leaving, 'R', NEW)
        if not isinstance(bundle, bytes):
            raise RuntimeError(f'交出返回了 {bundle!r}')
        if export_cycle(leaving, 'R', NEW) != bundle:
            raise RuntimeError('交出失答后未重取同一交接件')

        stopped = {}
        for label, operation in (
            ('collect_new', lambda: collect(leaving, device, 'R', 'T1')),
            ('sign_new', lambda: sign_window(leaving, 'R', 12, 14)),
        ):
            try:
                operation()
            except ValueError as error:
                stopped[label] = str(error)
            else:
                stopped[label] = None
        old_reread = sign_window(leaving, 'R', 10, 12)
        old_kept = retrieve_result(leaving, old_result.result_id)

        # 接班时关掉旧巡检库；同一记录器交给接班端。
        leaving.close()
        # 接班端整轮入库，按原记录器的 A2 身份查结，再续收 A3。
        handoff_id = import_cycle(receiving, bundle)
        resumed = collect(receiving, device, 'R', 'T1', a2.ref)
        tail = []
        while (item := collect(receiving, device, 'R', 'T1')) is not None:
            tail.append(item.event.page.ref.page_id)
        old_window_again = sign_window(receiving, 'R', 10, 12)
        new_result = sign_window(receiving, 'R', 12, 14).result
        if new_result is None:
            raise RuntimeError('接班端未签出 [12,14) 的 2 kPa')
        duplicate = import_cycle(receiving, bundle)
        # 重投不倒退：同件复领标识后，已取得的原页与成果仍在。
        progress_kept = [item.ref.page_id for item in read_channel_history(receiving, 'R', 'T1')]
        new_kept = retrieve_issued(receiving, NEW, new_result.result_id)

        # 关设备后按原签发端和原成果号领取。
        device.close()
        with closing(open_archive(root / 'receiving.sqlite')) as check:
            old_copy = retrieve_issued(check, OLD, 1)
            own_copy = retrieve_issued(check, NEW, 1)
            new_copy = retrieve_issued(check, NEW, new_result.result_id)
            wrong_end = retrieve_issued(check, OLD, new_result.result_id)
            local_number = retrieve_result(check, receiving_existing.result_id)
        with closing(open_archive(root / 'leaving.sqlite')) as old_check:
            old_end = retrieve_result(old_check, old_result.result_id)

    return {
        'a2_progress': a2_outcome.progress.value,
        'old_receipt': [old_result.result_id, old_result.receipt.code, old_result.delta_kpa],
        'old_issuer_on_reread': old_reread.issuer,
        'old_retrieved_unchanged': old_kept == old_result,
        'stopped': stopped,
        'handoff_id': handoff_id,
        'duplicate_handoff_id': duplicate,
        'duplicate_kept': [progress_kept, new_kept == new_result],
        'resume_progress': resumed.progress.value,
        'tail': tail,
        'old_window_again': [old_window_again.issuer, old_window_again.result == old_result],
        'new_receipt': [new_result.result_id, new_result.receipt.code,
                        new_result.delta_kpa, new_result.tick],
        'new_issuer': NEW,
        'retrieval': {
            'old_by_issuer': old_copy == old_result,
            'receiving_by_issuer': own_copy == receiving_existing,
            'new_by_issuer': new_copy == new_result,
            'wrong_issuer_none': wrong_end is None,
            'local_number_is_own': local_number == receiving_existing,
            'old_end_number': old_end == old_result,
        },
        'verified': [reconcile_result(item).valid
                     for item in (old_copy, own_copy, new_copy, old_end)],
    }


EXPECTED = {
    'a2_progress': 'unknown',
    'old_receipt': [1, 'PW-000001', 4],
    'old_issuer_on_reread': OLD,
    'old_retrieved_unchanged': True,
    'stopped': {'collect_new': '本轮已交出，旧端不再办理',
                'sign_new': '本轮已交出，旧端不再办理'},
    'resume_progress': 'released',
    'tail': ['A3'],
    'duplicate_kept': [['A1', 'A2'], True],
    'old_window_again': [OLD, True],
    'new_receipt': [2, 'PW-000002', 2, 12],
    'new_issuer': NEW,
    'retrieval': {'old_by_issuer': True, 'receiving_by_issuer': True, 'new_by_issuer': True,
                  'wrong_issuer_none': True, 'local_number_is_own': True, 'old_end_number': True},
    'verified': [True, True, True, True],
}


def main() -> None:
    with TemporaryDirectory(prefix='pump-handoff-drill-') as directory:
        actual = run_drill(directory)
    expected = dict(EXPECTED)
    comparable = {key: value for key, value in actual.items()
                  if key not in ('handoff_id', 'duplicate_handoff_id')}
    _expect(comparable, expected)
    if actual['handoff_id'] != actual['duplicate_handoff_id']:
        raise RuntimeError('重投未复领同一交接标识')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v_handoff',
        'input': {'recording': 'handoff_fixture.handoff_recording()',
                  'cycle': 'R', 'window_old': [10, 12], 'window_new': [12, 14],
                  'lost_ack': 'A2'},
        'expected': expected,
        'actual': comparable,
        'downstream_expected': {'old_reissued': True, 'new_sign_2kPa': True},
        'downstream_actual': {'old_reissued': actual['old_window_again'][1],
                              'new_sign_2kPa': actual['new_receipt'][2] == 2},
        'code_paths': ['handoff/bundle.py', 'handoff/service.py', 'capture/intake.py',
                       'capture/resume.py', 'signing/commit.py', 'audit/retrieval.py'],
        'oracle': 'A1 与 A2 原据为旧端签出 [10,12) 的 4 kPa；接班端补齐 A3 后为 [12,14) 签出 2 kPa。',
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
