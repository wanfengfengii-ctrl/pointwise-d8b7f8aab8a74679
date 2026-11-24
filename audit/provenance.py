from dataclasses import dataclass

from rounds.windows import TickWindow
from signing.evidence import IntervalEvidence


@dataclass(frozen=True)
class IntervalCheck:
    window: TickWindow | None
    problems: tuple[str, ...]


def check_interval_provenance(evidence: IntervalEvidence, cycle: str, channel: str) -> IntervalCheck:
    reading = evidence.reading
    closing = evidence.closing
    problems = []
    if any((page.ref.cycle, page.ref.channel) != (cycle, channel) for page in (reading, closing)):
        problems.append('区间原据不属于指定轮表')
    if closing.ref.sequence != reading.ref.sequence + 1:
        problems.append('闭合页不是读数的直接后继')
    if reading.kind != 'reading' or type(reading.value) is not int:
        problems.append('区间左端缺少整数原读')
    if closing.kind == 'reading':
        if type(closing.value) is not int:
            problems.append('普通闭合页没有整数原读')
    elif closing.kind != 'terminal' or closing.value is not None:
        problems.append('右端缺少有效的闭合事件')
    if any(type(page.provenance) is not str or not page.provenance for page in (reading, closing)):
        problems.append('缺少原页出处')
    if type(reading.tick) is not int or type(closing.tick) is not int or reading.tick >= closing.tick:
        problems.append('半开区间没有有效整数刻度')
    if problems:
        return IntervalCheck(None, tuple(problems))
    return IntervalCheck(TickWindow(reading.tick, closing.tick), ())
