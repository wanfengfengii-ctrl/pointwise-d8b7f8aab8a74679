import sqlite3
from enum import StrEnum

from device.source_pages import PageRef


class ReleaseProgress(StrEnum):
    STAGED = 'staged'
    UNKNOWN = 'unknown'
    NOT_APPLIED = 'not_applied'
    RELEASED = 'released'


def initialize_custody(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS capture_releases (
            cycle TEXT NOT NULL,
            channel TEXT NOT NULL,
            sequence TEXT NOT NULL,
            page_id TEXT NOT NULL,
            progress TEXT NOT NULL CHECK (progress IN ('staged', 'unknown', 'not_applied', 'released')),
            PRIMARY KEY (cycle, channel, sequence, page_id),
            FOREIGN KEY (cycle, channel, sequence, page_id)
                REFERENCES accepted_events (cycle, channel, sequence, page_id)
        )
    ''')


def stage_release(db: sqlite3.Connection, ref: PageRef) -> None:
    db.execute('''
        INSERT INTO capture_releases (cycle, channel, sequence, page_id, progress)
        VALUES (?, ?, ?, ?, ?)
    ''', (*ref.key, ReleaseProgress.STAGED.value))


def get_release_progress(db: sqlite3.Connection, ref: PageRef) -> ReleaseProgress:
    row = db.execute('''
        SELECT progress FROM capture_releases
        WHERE cycle = ? AND channel = ? AND sequence = ? AND page_id = ?
    ''', ref.key).fetchone()
    if row is None:
        raise ValueError('该原页没有已接纳的释放资格')
    return ReleaseProgress(row[0])


def update_release_progress(db: sqlite3.Connection, ref: PageRef,
                            progress: ReleaseProgress) -> ReleaseProgress:
    with db:
        db.execute('''
            UPDATE capture_releases SET progress = ?
            WHERE cycle = ? AND channel = ? AND sequence = ? AND page_id = ?
              AND progress <> 'released'
        ''', (progress.value, *ref.key))
        return get_release_progress(db, ref)
