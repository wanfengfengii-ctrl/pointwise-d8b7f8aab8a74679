import sqlite3
from dataclasses import dataclass

from device.source_pages import PageRef, SourcePage


@dataclass(frozen=True)
class AcceptedEvent:
    page: SourcePage


def initialize_intake(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS accepted_events (
            cycle TEXT NOT NULL REFERENCES cycle_registry (cycle),
            channel TEXT NOT NULL,
            sequence TEXT NOT NULL,
            page_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('reading', 'terminal')),
            tick TEXT NOT NULL,
            value TEXT,
            provenance TEXT NOT NULL,
            PRIMARY KEY (cycle, channel, sequence),
            UNIQUE (cycle, channel, sequence, page_id),
            CHECK ((kind = 'terminal') = (value IS NULL))
        )
    ''')


def store_accepted_page(db: sqlite3.Connection, page: SourcePage) -> AcceptedEvent:
    db.execute('''
        INSERT INTO accepted_events (cycle, channel, sequence, page_id, kind, tick, value, provenance)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (*page.ref.key, page.kind, hex(page.tick),
          None if page.value is None else hex(page.value), page.provenance))
    return AcceptedEvent(page)


def _decode(row) -> AcceptedEvent:
    cycle, channel, sequence, page_id, kind, tick, value, provenance = row
    ref = PageRef(cycle, channel, int(sequence, 16), page_id)
    return AcceptedEvent(SourcePage(ref, kind, int(tick, 16),
                                    None if value is None else int(value, 16), provenance))


_EVENT_COLUMNS = 'cycle, channel, sequence, page_id, kind, tick, value, provenance'


def read_accepted_page(db: sqlite3.Connection, ref: PageRef) -> AcceptedEvent | None:
    row = db.execute(f'''
        SELECT {_EVENT_COLUMNS} FROM accepted_events
        WHERE cycle = ? AND channel = ? AND sequence = ? AND page_id = ?
    ''', ref.key).fetchone()
    return _decode(row) if row else None


def read_channel_events(db: sqlite3.Connection, cycle: str, channel: str) -> tuple[AcceptedEvent, ...]:
    rows = db.execute(f'''
        SELECT {_EVENT_COLUMNS} FROM accepted_events WHERE cycle = ? AND channel = ?
    ''', (cycle, channel))
    return tuple(sorted(map(_decode, rows), key=lambda event: event.page.ref.sequence))


def read_evidence_snapshot(db: sqlite3.Connection, cycle: str, a: str, b: str
                           ) -> tuple[tuple[AcceptedEvent, ...], tuple[AcceptedEvent, ...]]:
    rows = db.execute(f'''
        SELECT {_EVENT_COLUMNS} FROM accepted_events WHERE cycle = ? AND channel IN (?, ?)
    ''', (cycle, a, b)).fetchall()
    lanes: dict[str, list[AcceptedEvent]] = {a: [], b: []}
    for row in rows:
        event = _decode(row)
        lanes[event.page.ref.channel].append(event)
    for lane in lanes.values():
        lane.sort(key=lambda event: event.page.ref.sequence)
    return tuple(lanes[a]), tuple(lanes[b])
