import sqlite3
from dataclasses import dataclass

from capture.custody import ReleaseProgress, stage_release
from capture.ledger import AcceptedEvent, read_accepted_page, read_channel_events, store_accepted_page
from capture.page_checks import validate_current_page, validate_event_order
from capture.resume import attempt_release, resume_release
from device.slots import DeviceSlots
from device.source_pages import PageRef, SourcePage
from handoff.store import assert_not_frozen
from rounds.channels import role_for_channel
from rounds.registration import get_cycle


@dataclass(frozen=True)
class CollectionResult:
    event: AcceptedEvent
    progress: ReleaseProgress


def collect(db: sqlite3.Connection, device: DeviceSlots, cycle: str, channel: str,
            page: SourcePage | PageRef | None = None, *, fail_at: str | None = None
            ) -> CollectionResult | None:
    with db:
        db.execute('BEGIN IMMEDIATE')
        registration = get_cycle(db, cycle)
        role_for_channel(registration.channels, channel)
        assert_not_frozen(db, cycle)
        proposed = device.read_current(cycle, channel) if page is None else page
        if proposed is None:
            return None
        if not isinstance(proposed, (SourcePage, PageRef)):
            raise ValueError('实收请求需要原页或原页身份')
        ref = proposed.ref if isinstance(proposed, SourcePage) else proposed
        if not isinstance(ref, PageRef) or (ref.cycle, ref.channel) != (cycle, channel):
            raise ValueError('原页身份不属于请求轮表')
        existing = read_accepted_page(db, ref)
        if existing is not None:
            if isinstance(proposed, SourcePage):
                validate_current_page(proposed, existing.page)
            event = existing
        else:
            if not isinstance(proposed, SourcePage):
                raise ValueError('只有已接纳的原页身份可以续办')
            validate_current_page(proposed, device.read_current(cycle, channel))
            events = read_channel_events(db, cycle, channel)
            validate_event_order(registration, proposed, events[-1].page if events else None)
            event = store_accepted_page(db, proposed)
            stage_release(db, ref)
            if fail_at == 'before_intake_commit':
                raise sqlite3.OperationalError('实收提交前演练失败')
    if existing is None:
        progress = attempt_release(db, device, ref)
    else:
        progress = resume_release(db, device, ref)
    return CollectionResult(event, progress)
