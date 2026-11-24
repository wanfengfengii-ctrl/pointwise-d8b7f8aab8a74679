from dataclasses import dataclass

from audit.provenance import check_interval_provenance
from rounds.channels import validate_pair
from signing.results import FirstReceipt, SignedResult


@dataclass(frozen=True)
class Reconciliation:
    valid: bool
    problems: tuple[str, ...]


def reconcile_result(result: SignedResult) -> Reconciliation:
    problems = []
    try:
        validate_pair(result.channels.a, result.channels.b)
    except ValueError as error:
        problems.append(str(error))
    a = check_interval_provenance(result.a, result.cycle, result.channels.a)
    b = check_interval_provenance(result.b, result.cycle, result.channels.b)
    problems.extend(f'甲表 {problem}' for problem in a.problems)
    problems.extend(f'乙表 {problem}' for problem in b.problems)
    if type(result.tick) is not int or not result.window.contains(result.tick):
        problems.append('见证刻度不在原查询窗内')
    if a.window is not None and b.window is not None:
        if not (a.window.contains(result.tick) and b.window.contains(result.tick)):
            problems.append('见证刻度缺少两表共同覆盖')
        delta = result.b.reading.value - result.a.reading.value
        if type(result.delta_kpa) is not int or result.delta_kpa != delta:
            problems.append('差值与两表原读不符')
    if result.receipt != FirstReceipt.for_result(result.result_id):
        problems.append('首执与成果号不符')
    return Reconciliation(not problems, tuple(problems))
