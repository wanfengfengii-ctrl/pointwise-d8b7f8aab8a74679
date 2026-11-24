from dataclasses import dataclass
from itertools import pairwise

from capture.ledger import AcceptedEvent
from rounds.windows import TickWindow


@dataclass(frozen=True)
class ProvenInterval:
    reading: AcceptedEvent
    closing: AcceptedEvent
    window: TickWindow


@dataclass(frozen=True)
class ChannelCoverage:
    proven: tuple[ProvenInterval, ...]
    gaps: tuple[TickWindow, ...]
    unresolved: tuple[TickWindow, ...]
    possible: TickWindow


def coverage_for_channel(events: tuple[AcceptedEvent, ...], bounds: TickWindow) -> ChannelCoverage:
    if not events:
        return ChannelCoverage((), (), (bounds,), bounds)

    first = events[0].page
    last = events[-1].page
    start = first.tick if first.ref.sequence == 1 else bounds.start
    stop = last.tick if last.kind == 'terminal' else bounds.stop
    possible = TickWindow(start, stop)
    if possible.empty:
        return ChannelCoverage((), (bounds,), (), possible)

    gaps = []
    if bounds.start < start:
        gaps.append(TickWindow(bounds.start, start))
    if stop < bounds.stop:
        gaps.append(TickWindow(stop, bounds.stop))

    proven = []
    unresolved = []
    cursor = start
    for reading, closing in pairwise(events):
        left = reading.page
        right = closing.page
        if left.kind != 'reading' or right.ref.sequence != left.ref.sequence + 1:
            continue
        if left.tick >= right.tick:
            continue
        if cursor < left.tick:
            unresolved.append(TickWindow(cursor, left.tick))
        proven.append(ProvenInterval(reading, closing, TickWindow(left.tick, right.tick)))
        cursor = right.tick
    if cursor < stop:
        unresolved.append(TickWindow(cursor, stop))
    return ChannelCoverage(tuple(proven), tuple(gaps), tuple(unresolved), possible)
