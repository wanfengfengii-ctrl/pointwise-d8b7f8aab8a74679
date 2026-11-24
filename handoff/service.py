"""交出入库、接手与按原签发端领取。"""
import sqlite3

from capture.custody import get_release_progress
from capture.ledger import read_channel_events
from handoff.bundle import Bundle, encode_bundle_from_parts, parse_bundle
from handoff.store import (find_inbound, find_inbound_by_id, find_outbound, own_issuer,
                           require_terminal)
from rounds.registration import get_cycle
from rounds.registry_store import insert_registration
from signing.commit import commit_signed_result, read_issued_result
from signing.results import SignedResult


def bind_terminal(db: sqlite3.Connection, terminal: str) -> None:
    if type(terminal) is not str or not terminal:
        raise ValueError('终端编号必须是非空字符串')
    with db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT terminal FROM terminal_binding WHERE id = 1').fetchone()
        if row is not None:
            if row[0] != terminal:
                raise ValueError('本库已绑定另一终端编号')
            return
        db.execute('INSERT INTO terminal_binding (id, terminal) VALUES (1, ?)', (terminal,))
        # 本库已有的签件归入本端编号域（未配置时签发端为空编号）。
        db.execute('PRAGMA defer_foreign_keys = ON')
        db.execute("UPDATE signed_results SET issuer = ? WHERE issuer = ''", (terminal,))
        db.execute("UPDATE first_receipts SET issuer = ? WHERE issuer = ''", (terminal,))


def _handoff_id_for(sender: str, recipient: str, cycle: str) -> str:
    return f'{sender}->{recipient}:{cycle}'


def _collect_bundle(db: sqlite3.Connection, cycle: str, sender: str, recipient: str) -> Bundle:
    registration = get_cycle(db, cycle)
    events = []
    for channel in (registration.channels.a, registration.channels.b):
        events.append((channel, tuple(event.page for event in read_channel_events(db, cycle, channel))))
    releases = []
    for _channel, pages in events:
        for page in pages:
            releases.append((page.ref, get_release_progress(db, page.ref)))
    results = []
    rows = db.execute('''
        SELECT result_id FROM signed_results WHERE cycle = ? AND issuer = ? ORDER BY result_id
    ''', (cycle, own_issuer(db))).fetchall()
    for (result_id,) in rows:
        result = read_issued_result(db, own_issuer(db), result_id)
        if result is not None:
            results.append((own_issuer(db), result))
    raw = encode_bundle_from_parts(sender=sender, recipient=recipient,
                                   registration=registration, events=events,
                                   releases=releases, results=results)
    parsed = parse_bundle(raw)
    return Bundle(_handoff_id_for(sender, recipient, cycle), parsed.digest, parsed.sender,
                  parsed.recipient, parsed.registration, parsed.events, parsed.releases,
                  parsed.results)


def export_cycle(db: sqlite3.Connection, cycle: str, recipient: str, *,
                 fail_at: str | None = None) -> bytes | str:
    terminal = require_terminal(db)
    if type(recipient) is not str or not recipient:
        raise ValueError('接收端编号必须是非空字符串')
    if recipient == terminal:
        raise ValueError('交出端与接收端不能是同一终端')
    with db:
        db.execute('BEGIN IMMEDIATE')
        existing = find_outbound(db, cycle)
        if existing is not None:
            stored_recipient, _stored_id, _stored_digest, stored_bundle = existing
            if stored_recipient != recipient:
                raise ValueError('同一轮只能交给原定接收端')
            return stored_bundle
        if find_inbound(db, cycle) is not None:
            raise ValueError('已接手的轮不能再次交出')
        get_cycle(db, cycle)
        bundle = _collect_bundle(db, cycle, terminal, recipient)
        db.execute('''
            INSERT INTO handoff_outbound (cycle, recipient, handoff_id, digest, bundle)
            VALUES (?, ?, ?, ?, ?)
        ''', (cycle, recipient, bundle.handoff_id, bundle.digest, bundle.raw()))
        if fail_at == 'before_handoff_commit':
            raise sqlite3.OperationalError('交出提交前演练失败')
    return bundle.raw()


def _restore(db: sqlite3.Connection, bundle: Bundle, *, fail_at: str | None = None) -> None:
    registration = bundle.registration
    insert_registration(db, bundle.registration.cycle, registration.channels.a,
                        registration.channels.b, registration.bounds.start, registration.bounds.stop)
    for _channel, pages in bundle.events:
        for page in pages:
            db.execute('''
                INSERT INTO accepted_events
                    (cycle, channel, sequence, page_id, kind, tick, value, provenance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (*page.ref.key, page.kind, hex(page.tick),
                  None if page.value is None else hex(page.value), page.provenance))
    for ref, progress in bundle.releases:
        db.execute('''
            INSERT INTO capture_releases (cycle, channel, sequence, page_id, progress)
            VALUES (?, ?, ?, ?, ?)
        ''', (*ref.key, progress.value))
    for issuer, result in bundle.results:
        commit_signed_result(db, result, issuer=issuer)
    if fail_at == 'before_import_commit':
        raise sqlite3.OperationalError('接班提交前演练失败')


def import_cycle(db: sqlite3.Connection, bundle: bytes, *, fail_at: str | None = None) -> str:
    terminal = require_terminal(db)
    parsed = parse_bundle(bundle)
    if parsed.recipient != terminal:
        raise ValueError('交接件的接收端不是本终端')
    with db:
        db.execute('BEGIN IMMEDIATE')
        previous = find_inbound_by_id(db, parsed.handoff_id)
        if previous is not None:
            stored = find_inbound(db, previous[0])
            if stored is not None and stored[0] == parsed.handoff_id and stored[2] == parsed.digest:
                return parsed.handoff_id
            raise ValueError('同一交接标识对应不同内容')
        existing = find_inbound(db, parsed.registration.cycle)
        if existing is not None:
            raise ValueError('本库已经接手的同号轮与交接件冲突')
        registration_row = db.execute(
            'SELECT 1 FROM cycle_registry WHERE cycle = ?',
            (parsed.registration.cycle,)).fetchone()
        if registration_row is not None:
            raise ValueError('目标已有独立登记的同号轮')
        try:
            _restore(db, parsed, fail_at=fail_at)
        except sqlite3.IntegrityError as error:
            raise ValueError(f'交接件与本地成果冲突：{error}') from error
        db.execute('''
            INSERT INTO handoff_inbound (cycle, handoff_id, sender, digest, bundle)
            VALUES (?, ?, ?, ?, ?)
        ''', (parsed.registration.cycle, parsed.handoff_id, parsed.sender, parsed.digest,
              parsed.raw()))
    return parsed.handoff_id


def retrieve_issued(db: sqlite3.Connection, issuer: str, result_id: int) -> SignedResult | None:
    require_terminal(db)
    if type(issuer) is not str:
        raise ValueError('签发端编号必须是字符串')
    if type(result_id) is not int:
        raise ValueError('成果号必须是整数')
    if not issuer or not 1 <= result_id < 2**63:
        return None
    return read_issued_result(db, issuer, result_id)
