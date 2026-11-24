import sqlite3

from device.delivery import DeliverySchedule
from device.recording import Recording
from device.release_log import ReleaseOutcome, initialize_release_log, lookup_release, record_release
from device.source_pages import PageRef, SourcePage


def initialize_slots(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS device_slots (
            cycle TEXT NOT NULL,
            channel TEXT NOT NULL,
            position INTEGER NOT NULL,
            PRIMARY KEY (cycle, channel)
        )
    ''')
    initialize_release_log(db)


class DeviceSlots:
    def __init__(self, db: sqlite3.Connection, recording: Recording,
                 delivery: DeliverySchedule | None = None):
        self.connection = db
        self.recording = recording
        self.delivery = delivery if delivery is not None else DeliverySchedule()

    def close(self) -> None:
        self.connection.close()

    def _position(self, cycle: str, channel: str) -> int:
        row = self.connection.execute('''
            SELECT position FROM device_slots WHERE cycle = ? AND channel = ?
        ''', (cycle, channel)).fetchone()
        return row[0] if row else 0

    def read_current(self, cycle: str, channel: str) -> SourcePage | None:
        position = self._position(cycle, channel)
        if position < 0 or not self.delivery.page_available(cycle, channel, position):
            return None
        return self.recording.page_at(cycle, channel, position)

    def release_page(self, ref: PageRef) -> ReleaseOutcome:
        db = self.connection
        with db:
            db.execute('BEGIN IMMEDIATE')
            outcome = lookup_release(db, ref)
            if outcome is not ReleaseOutcome.RELEASED:
                current = self.read_current(ref.cycle, ref.channel)
                if current is None or current.ref != ref:
                    raise ValueError('释放请求与实际当前原页不符')
                outcome = self.delivery.deliver_release_request(ref)
                if outcome is not ReleaseOutcome.UNKNOWN:
                    record_release(db, ref, outcome)
                if outcome is ReleaseOutcome.RELEASED:
                    next_position = self._position(ref.cycle, ref.channel) + 1
                    if current.kind == 'terminal':
                        next_position = -1
                    db.execute('''
                        INSERT INTO device_slots (cycle, channel, position) VALUES (?, ?, ?)
                        ON CONFLICT (cycle, channel) DO UPDATE SET position = excluded.position
                    ''', (ref.cycle, ref.channel, next_position))
        return self.delivery.deliver_release_reply(ref, outcome)

    def query_release(self, ref: PageRef) -> ReleaseOutcome:
        db = self.connection
        with db:
            db.execute('BEGIN')
            outcome = lookup_release(db, ref)
            if outcome is ReleaseOutcome.UNKNOWN:
                current = self.read_current(ref.cycle, ref.channel)
                if current is not None and current.ref == ref:
                    # Execution in this recorder is synchronous. A retained current page
                    # and the absence of a success record confirm that it was not released.
                    outcome = ReleaseOutcome.NOT_APPLIED
        return self.delivery.deliver_release_reply(ref, outcome)
