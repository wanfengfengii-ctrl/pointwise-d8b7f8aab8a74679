from collections import deque
from dataclasses import dataclass, field

from device.release_log import ReleaseOutcome
from device.source_pages import PageRef


@dataclass
class DeliverySchedule:
    visible_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    requests: dict[PageRef, deque[ReleaseOutcome]] = field(default_factory=dict)
    replies: dict[PageRef, deque[bool]] = field(default_factory=dict)

    def page_available(self, cycle: str, channel: str, position: int) -> bool:
        limit = self.visible_counts.get((cycle, channel))
        return limit is None or position < limit

    def deliver_release_request(self, ref: PageRef) -> ReleaseOutcome:
        planned = self.requests.get(ref)
        return planned.popleft() if planned else ReleaseOutcome.RELEASED

    def deliver_release_reply(self, ref: PageRef, outcome: ReleaseOutcome) -> ReleaseOutcome:
        planned = self.replies.get(ref)
        if planned and not planned.popleft():
            return ReleaseOutcome.UNKNOWN
        return outcome
