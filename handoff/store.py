"""交接件的本地登记：终端编号、已交出轮与已接班轮。"""
import sqlite3


def initialize_handoff(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS terminal_binding (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            terminal TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS handoff_outbound (
            cycle TEXT PRIMARY KEY NOT NULL,
            recipient TEXT NOT NULL,
            handoff_id TEXT NOT NULL,
            digest TEXT NOT NULL,
            bundle BLOB NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS handoff_inbound (
            cycle TEXT PRIMARY KEY NOT NULL,
            handoff_id TEXT NOT NULL UNIQUE,
            sender TEXT NOT NULL,
            digest TEXT NOT NULL,
            bundle BLOB NOT NULL
        )
    ''')


def get_terminal(db: sqlite3.Connection) -> str | None:
    row = db.execute('SELECT terminal FROM terminal_binding WHERE id = 1').fetchone()
    return row[0] if row else None


def require_terminal(db: sqlite3.Connection) -> str:
    terminal = get_terminal(db)
    if terminal is None:
        raise ValueError('本库尚未配置终端编号')
    return terminal


def own_issuer(db: sqlite3.Connection) -> str:
    """本端成果的签发端；未配置终端时使用空编号域。"""
    return get_terminal(db) or ''


def find_outbound(db: sqlite3.Connection, cycle: str):
    return db.execute('''
        SELECT recipient, handoff_id, digest, bundle
        FROM handoff_outbound WHERE cycle = ?
    ''', (cycle,)).fetchone()


def find_inbound(db: sqlite3.Connection, cycle: str):
    return db.execute('''
        SELECT handoff_id, sender, digest
        FROM handoff_inbound WHERE cycle = ?
    ''', (cycle,)).fetchone()


def find_inbound_by_id(db: sqlite3.Connection, handoff_id: str):
    return db.execute('''
        SELECT cycle FROM handoff_inbound WHERE handoff_id = ?
    ''', (handoff_id,)).fetchone()


def is_frozen(db: sqlite3.Connection, cycle: str) -> bool:
    return find_outbound(db, cycle) is not None


def assert_not_frozen(db: sqlite3.Connection, cycle: str) -> None:
    if is_frozen(db, cycle):
        raise ValueError('本轮已交出，旧端不再办理')
