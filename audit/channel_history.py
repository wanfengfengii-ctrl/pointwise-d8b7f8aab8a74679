import sqlite3
from dataclasses import dataclass

from capture.ledger import read_channel_events
from device.source_pages import PageRef
from rounds.channels import role_for_channel
from rounds.registration import get_cycle


@dataclass(frozen=True)
class ChannelReading:
    ref: PageRef
    tick: int
    value: int
    provenance: str


def read_channel_history(db: sqlite3.Connection, cycle: str, channel: str) -> tuple[ChannelReading, ...]:
    registration = get_cycle(db, cycle)
    role_for_channel(registration.channels, channel)
    readings = []
    for event in read_channel_events(db, cycle, channel):
        page = event.page
        if page.kind == 'reading':
            readings.append(ChannelReading(page.ref, page.tick, page.value, page.provenance))
    return tuple(readings)
