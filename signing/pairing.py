from dataclasses import dataclass
from typing import Literal

from rounds.windows import TickWindow, clip_to_cycle
from signing.coverage import ChannelCoverage, ProvenInterval


@dataclass(frozen=True)
class PairDecision:
    status: Literal['signed', 'pending', 'disjoint']
    tick: int | None = None
    a: ProvenInterval | None = None
    b: ProvenInterval | None = None


def find_pair(a: ChannelCoverage, b: ChannelCoverage, window: TickWindow) -> PairDecision:
    possible = clip_to_cycle(clip_to_cycle(window, a.possible), b.possible)
    if possible.empty:
        return PairDecision('disjoint')
    a_index = b_index = 0
    while a_index < len(a.proven) and b_index < len(b.proven):
        left = a.proven[a_index]
        right = b.proven[b_index]
        start = max(window.start, left.window.start, right.window.start)
        stop = min(window.stop, left.window.stop, right.window.stop)
        if start < stop:
            return PairDecision('signed', start, left, right)
        if left.window.stop <= right.window.stop:
            a_index += 1
        else:
            b_index += 1
    return PairDecision('pending')
