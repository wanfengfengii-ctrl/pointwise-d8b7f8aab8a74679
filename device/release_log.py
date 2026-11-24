import sqlite3
from enum import StrEnum

from device.source_pages import PageRef


class ReleaseOutcome(StrEnum):
    RELEASED = 'released'
    NOT_APPLIED = 'not_applied'
    UNKNOWN = 'unknown'


def initialize_release_log(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS device_releases (
            cycle TEXT NOT NULL,
            channel TEXT NOT NULL,
            sequence TEXT NOT NULL,
            page_id TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('released', 'not_applied')),
            PRIMARY KEY (cycle, channel, sequence, page_id)
        )
    ''')


def record_release(db: sqlite3.Connection, ref: PageRef, outcome: ReleaseOutcome) -> None:
    if outcome is ReleaseOutcome.UNKNOWN:
        raise ValueError('设备只记录确定的实际释放结局')
    db.execute('''
        INSERT INTO device_releases (cycle, channel, sequence, page_id, outcome)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (cycle, channel, sequence, page_id) DO UPDATE SET outcome = excluded.outcome
        WHERE device_releases.outcome <> 'released'
    ''', (*ref.key, outcome.value))


def lookup_release(db: sqlite3.Connection, ref: PageRef) -> ReleaseOutcome:
    row = db.execute('''
        SELECT outcome FROM device_releases
        WHERE cycle = ? AND channel = ? AND sequence = ? AND page_id = ?
    ''', ref.key).fetchone()
    return ReleaseOutcome(row[0]) if row else ReleaseOutcome.UNKNOWN
