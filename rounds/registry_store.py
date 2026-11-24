import sqlite3


def initialize_registry(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS cycle_registry (
            cycle TEXT PRIMARY KEY NOT NULL,
            channel_a TEXT NOT NULL,
            channel_b TEXT NOT NULL,
            start_tick TEXT NOT NULL,
            stop_tick TEXT NOT NULL
        )
    ''')


def find_registration(db: sqlite3.Connection, cycle: str) -> tuple[str, str, int, int] | None:
    row = db.execute('''
        SELECT channel_a, channel_b, start_tick, stop_tick FROM cycle_registry WHERE cycle = ?
    ''', (cycle,)).fetchone()
    if row is None:
        return None
    a, b, start, stop = row
    return a, b, int(start, 16), int(stop, 16)


def insert_registration(db: sqlite3.Connection, cycle: str, a: str, b: str,
                        start: int, stop: int) -> None:
    db.execute('''
        INSERT INTO cycle_registry (cycle, channel_a, channel_b, start_tick, stop_tick)
        VALUES (?, ?, ?, ?, ?)
    ''', (cycle, a, b, hex(start), hex(stop)))
