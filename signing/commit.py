import sqlite3

from capture.ledger import read_evidence_snapshot
from handoff.store import assert_not_frozen, own_issuer
from rounds.channels import ChannelPair
from rounds.registration import get_cycle
from rounds.windows import TickWindow, clip_to_cycle, validate_query_window
from signing.coverage import coverage_for_channel
from signing.evidence import decode_evidence, encode_evidence
from signing.pairing import find_pair
from signing.results import FirstReceipt, SignOutcome, SignedResult, build_signed_result


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f'PRAGMA table_info({table})')}


def _migrate_result_issuer(db: sqlite3.Connection) -> None:
    """把 V1 单端签存升级为带签发端的账，已有成果归入空编号域。"""
    if not _columns(db, 'signed_results') or 'issuer' in _columns(db, 'signed_results'):
        return
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('ALTER TABLE signed_results RENAME TO signed_results_v1')
        db.execute('ALTER TABLE first_receipts RENAME TO first_receipts_v1')
        initialize_signing(db)
        db.execute('''
            INSERT INTO signed_results (issuer, result_id, cycle, start_tick, stop_tick,
                                        channel_a, channel_b, witness_tick, delta,
                                        evidence_a, evidence_b)
            SELECT '', result_id, cycle, start_tick, stop_tick, channel_a, channel_b,
                   witness_tick, delta, evidence_a, evidence_b
            FROM signed_results_v1
        ''')
        db.execute('''
            INSERT INTO first_receipts (issuer, result_id, code)
            SELECT '', result_id, code FROM first_receipts_v1
        ''')
        db.execute('DROP TABLE first_receipts_v1')
        db.execute('DROP TABLE signed_results_v1')


def initialize_signing(db: sqlite3.Connection) -> None:
    _migrate_result_issuer(db)
    db.execute('''
        CREATE TABLE IF NOT EXISTS signed_results (
            issuer TEXT NOT NULL,
            result_id INTEGER NOT NULL,
            cycle TEXT NOT NULL REFERENCES cycle_registry (cycle),
            start_tick TEXT NOT NULL,
            stop_tick TEXT NOT NULL,
            channel_a TEXT NOT NULL,
            channel_b TEXT NOT NULL,
            witness_tick TEXT NOT NULL,
            delta TEXT NOT NULL,
            evidence_a TEXT NOT NULL,
            evidence_b TEXT NOT NULL,
            PRIMARY KEY (issuer, result_id),
            UNIQUE (cycle, start_tick, stop_tick)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS first_receipts (
            issuer TEXT NOT NULL,
            result_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            PRIMARY KEY (issuer, result_id),
            UNIQUE (issuer, code),
            FOREIGN KEY (issuer, result_id) REFERENCES signed_results (issuer, result_id)
        )
    ''')


_RESULT_SELECT = '''
    SELECT r.result_id, r.cycle, r.start_tick, r.stop_tick, r.channel_a, r.channel_b,
           r.witness_tick, r.delta, r.evidence_a, r.evidence_b, receipt.result_id, receipt.code,
           r.issuer
    FROM signed_results AS r JOIN first_receipts AS receipt
      ON receipt.issuer = r.issuer AND receipt.result_id = r.result_id
'''


def _unpack(row) -> SignedResult | None:
    if row is None:
        return None
    (result_id, cycle, start, stop, a, b, witness, delta, proof_a, proof_b,
     receipt_id, code, _issuer) = row
    return SignedResult(result_id, cycle, ChannelPair(a, b), TickWindow(int(start, 16), int(stop, 16)),
                        int(witness, 16), decode_evidence(proof_a), decode_evidence(proof_b),
                        int(delta, 16), FirstReceipt(receipt_id, code))


def _validate_result_id(result_id: int) -> bool:
    if type(result_id) is not int:
        raise ValueError('成果号必须是整数')
    return 1 <= result_id < 2**63


def read_signed_result(db: sqlite3.Connection, result_id: int) -> SignedResult | None:
    """本端原发成果；沿用 V1 的整数领取语义。"""
    if not _validate_result_id(result_id):
        return None
    return read_issued_result(db, own_issuer(db), result_id)


def read_issued_result(db: sqlite3.Connection, issuer: str, result_id: int) -> SignedResult | None:
    if not _validate_result_id(result_id):
        return None
    return _unpack(db.execute(_RESULT_SELECT + '''
        WHERE r.issuer = ? AND r.result_id = ?
    ''', (issuer, result_id)).fetchone())


def read_window_result(db: sqlite3.Connection, cycle: str, start: int, stop: int
                       ) -> SignedResult | None:
    return _unpack(db.execute(_RESULT_SELECT + '''
        WHERE r.cycle = ? AND r.start_tick = ? AND r.stop_tick = ?
    ''', (cycle, hex(start), hex(stop))).fetchone())


def result_issuer(db: sqlite3.Connection, result: SignedResult) -> str | None:
    row = db.execute('''
        SELECT issuer FROM signed_results WHERE cycle = ? AND start_tick = ? AND stop_tick = ?
    ''', (result.cycle, hex(result.window.start), hex(result.window.stop))).fetchone()
    return row[0] if row else None


def next_result_id(db: sqlite3.Connection, issuer: str) -> int:
    return db.execute('SELECT COALESCE(MAX(result_id), 0) + 1 FROM signed_results WHERE issuer = ?',
                      (issuer,)).fetchone()[0]


def commit_signed_result(db: sqlite3.Connection, result: SignedResult, *,
                         issuer: str | None = None, fail_at: str | None = None) -> None:
    if not db.in_transaction:
        raise RuntimeError('成果及首执写入需要已有签存事务')
    issuer = own_issuer(db) if issuer is None else issuer
    db.execute('''
        INSERT INTO signed_results (issuer, result_id, cycle, start_tick, stop_tick, channel_a,
                                    channel_b, witness_tick, delta, evidence_a, evidence_b)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (issuer, result.result_id, result.cycle, hex(result.window.start), hex(result.window.stop),
          result.channels.a, result.channels.b, hex(result.tick), hex(result.delta_kpa),
          encode_evidence(result.a), encode_evidence(result.b)))
    if fail_at == 'before_receipt':
        raise sqlite3.OperationalError('首执写入前演练失败')
    db.execute('INSERT INTO first_receipts (issuer, result_id, code) VALUES (?, ?, ?)',
               (issuer, result.receipt.result_id, result.receipt.code))
    if fail_at == 'before_sign_commit':
        raise sqlite3.OperationalError('签存提交前演练失败')


def sign_window(db: sqlite3.Connection, cycle: str, start: int, stop: int,
                *, fail_at: str | None = None) -> SignOutcome:
    window = validate_query_window(start, stop)
    with db:
        db.execute('BEGIN IMMEDIATE')
        registration = get_cycle(db, cycle)
        existing = read_window_result(db, cycle, start, stop)
        if existing is not None:
            return SignOutcome('signed', existing, result_issuer(db, existing))
        assert_not_frozen(db, cycle)
        search = clip_to_cycle(window, registration.bounds)
        if search.empty:
            return SignOutcome('disjoint')
        a, b = read_evidence_snapshot(db, cycle, registration.channels.a, registration.channels.b)
        decision = find_pair(coverage_for_channel(a, registration.bounds),
                             coverage_for_channel(b, registration.bounds), search)
        if decision.status != 'signed':
            return SignOutcome(decision.status)
        issuer = own_issuer(db)
        result = build_signed_result(next_result_id(db, issuer), registration, window, decision)
        commit_signed_result(db, result, issuer=issuer, fail_at=fail_at)
    return SignOutcome('signed', result, issuer)
