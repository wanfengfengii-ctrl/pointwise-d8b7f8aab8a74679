from dataclasses import dataclass
from typing import Literal

from rounds.registration import CycleRegistration
from rounds.channels import ChannelPair
from rounds.windows import TickWindow
from signing.evidence import IntervalEvidence, freeze_interval_evidence
from signing.pairing import PairDecision


@dataclass(frozen=True)
class FirstReceipt:
    result_id: int
    code: str

    @classmethod
    def for_result(cls, result_id: int) -> 'FirstReceipt':
        return cls(result_id, f'PW-{result_id:06d}')


@dataclass(frozen=True)
class SignedResult:
    result_id: int
    cycle: str
    channels: ChannelPair
    window: TickWindow
    tick: int
    a: IntervalEvidence
    b: IntervalEvidence
    delta_kpa: int
    receipt: FirstReceipt


@dataclass(frozen=True)
class SignOutcome:
    status: Literal['signed', 'pending', 'disjoint']
    result: SignedResult | None = None
    issuer: str | None = None


def build_signed_result(result_id: int, registration: CycleRegistration, window: TickWindow,
                        decision: PairDecision) -> SignedResult:
    tick = decision.tick
    if decision.status != 'signed' or tick is None or decision.a is None or decision.b is None:
        raise ValueError('签存需要两份覆盖同一刻度的闭合依据')
    if not all(span.contains(tick) for span in (window, registration.bounds,
                                                decision.a.window, decision.b.window)):
        raise ValueError('见证刻度超出请求窗或已证范围')
    a = freeze_interval_evidence(decision.a)
    b = freeze_interval_evidence(decision.b)
    return SignedResult(result_id, registration.cycle, registration.channels, window, tick, a, b,
                        b.reading.value - a.reading.value, FirstReceipt.for_result(result_id))
