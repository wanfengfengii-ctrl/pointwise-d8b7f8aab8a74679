import sqlite3

from capture.ledger import read_evidence_snapshot
from rounds.channels import ChannelPair
from rounds.registration import get_cycle
from rounds.windows import TickWindow, clip_to_cycle, validate_query_window
from signing.coverage import coverage_for_channel
from signing.evidence import decode_evidence, encode_evidence
from signing.pairing import find_pair
from signing.results import FirstReceipt, SignOutcome, SignedResult, build_signed_result


def initialize_signing(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS signed_results (
            result_id INTEGER PRIMARY KEY,
            cycle TEXT NOT NULL REFERENCES cycle_registry (cycle),
            start_tick TEXT NOT NULL,
            stop_tick TEXT NOT NULL,
            channel_a TEXT NOT NULL,
            channel_b TEXT NOT NULL,
            witness_tick TEXT NOT NULL,
            delta TEXT NOT NULL,
            evidence_a TEXT NOT NULL,
            evidence_b TEXT NOT NULL,
            UNIQUE (cycle, start_tick, stop_tick)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS first_receipts (
            result_id INTEGER PRIMARY KEY REFERENCES signed_results (result_id),
            code TEXT NOT NULL UNIQUE
        )
    ''')


_RESULT_SELECT = '''
    SELECT r.result_id, r.cycle, r.start_tick, r.stop_tick, r.channel_a, r.channel_b,
           r.witness_tick, r.delta, r.evidence_a, r.evidence_b, receipt.result_id, receipt.code
    FROM signed_results AS r JOIN first_receipts AS receipt USING (result_id)
'''


def _unpack(row) -> SignedResult | None:
    if row is None:
        return None
    result_id, cycle, start, stop, a, b, witness, delta, proof_a, proof_b, receipt_id, code = row
    return SignedResult(result_id, cycle, ChannelPair(a, b), TickWindow(int(start, 16), int(stop, 16)),
                        int(witness, 16), decode_evidence(proof_a), decode_evidence(proof_b),
                        int(delta, 16), FirstReceipt(receipt_id, code))


def read_signed_result(db: sqlite3.Connection, result_id: int) -> SignedResult | None:
    if type(result_id) is not int:
        raise ValueError('成果号必须是整数')
    if not 1 <= result_id < 2**63:
        return None
    return _unpack(db.execute(_RESULT_SELECT + ' WHERE r.result_id = ?', (result_id,)).fetchone())


def commit_signed_result(db: sqlite3.Connection, result: SignedResult,
                         *, fail_at: str | None = None) -> None:
    if not db.in_transaction:
        raise RuntimeError('成果及首执写入需要已有签存事务')
    db.execute('''
        INSERT INTO signed_results (result_id, cycle, start_tick, stop_tick, channel_a, channel_b,
                                    witness_tick, delta, evidence_a, evidence_b)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (result.result_id, result.cycle, hex(result.window.start), hex(result.window.stop),
          result.channels.a, result.channels.b, hex(result.tick), hex(result.delta_kpa),
          encode_evidence(result.a), encode_evidence(result.b)))
    if fail_at == 'before_receipt':
        raise sqlite3.OperationalError('首执写入前演练失败')
    db.execute('INSERT INTO first_receipts (result_id, code) VALUES (?, ?)',
               (result.receipt.result_id, result.receipt.code))
    if fail_at == 'before_sign_commit':
        raise sqlite3.OperationalError('签存提交前演练失败')


def sign_window(db: sqlite3.Connection, cycle: str, start: int, stop: int,
                *, fail_at: str | None = None) -> SignOutcome:
    window = validate_query_window(start, stop)
    with db:
        db.execute('BEGIN IMMEDIATE')
        registration = get_cycle(db, cycle)
        existing = _unpack(db.execute(_RESULT_SELECT + '''
            WHERE r.cycle = ? AND r.start_tick = ? AND r.stop_tick = ?
        ''', (cycle, hex(start), hex(stop))).fetchone())
        if existing is not None:
            return SignOutcome('signed', existing)
        search = clip_to_cycle(window, registration.bounds)
        if search.empty:
            return SignOutcome('disjoint')
        a, b = read_evidence_snapshot(db, cycle, registration.channels.a, registration.channels.b)
        decision = find_pair(coverage_for_channel(a, registration.bounds),
                             coverage_for_channel(b, registration.bounds), search)
        if decision.status != 'signed':
            return SignOutcome(decision.status)
        result_id = db.execute('SELECT COALESCE(MAX(result_id), 0) + 1 FROM signed_results').fetchone()[0]
        result = build_signed_result(result_id, registration, window, decision)
        commit_signed_result(db, result, fail_at=fail_at)
    return SignOutcome('signed', result)
