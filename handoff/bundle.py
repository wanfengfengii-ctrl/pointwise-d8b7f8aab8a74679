"""交接件的编码、解码与完整性校验。"""
import hashlib
import json
from dataclasses import dataclass

from audit.reconcile import reconcile_result
from capture.custody import ReleaseProgress
from capture.page_checks import validate_event_order
from device.source_pages import PageRef, SourcePage
from rounds.channels import validate_pair
from rounds.registration import CycleRegistration
from rounds.windows import TickWindow, validate_cycle_range
from signing.evidence import decode_evidence, encode_evidence
from signing.results import FirstReceipt, SignedResult

FORMAT = 'pump-handoff/1'
_PROGRESS = {item.value for item in ReleaseProgress}


@dataclass(frozen=True)
class Bundle:
    handoff_id: str
    digest: str
    sender: str
    recipient: str
    registration: CycleRegistration
    events: tuple[tuple[str, tuple[SourcePage, ...]], ...]
    releases: tuple[tuple[PageRef, ReleaseProgress], ...]
    results: tuple[tuple[str, SignedResult], ...]

    def raw(self) -> bytes:
        return encode_bundle(self)


def _encode_page(page: SourcePage) -> dict:
    return {
        'cycle': page.ref.cycle,
        'channel': page.ref.channel,
        'sequence': hex(page.ref.sequence),
        'page_id': page.ref.page_id,
        'kind': page.kind,
        'tick': hex(page.tick),
        'value': None if page.value is None else hex(page.value),
        'provenance': page.provenance,
    }


def _decode_page(data: dict) -> SourcePage:
    if not isinstance(data, dict):
        raise ValueError('交接件原页必须是对象')
    try:
        ref = PageRef(data['cycle'], data['channel'], int(data['sequence'], 16), data['page_id'])
        value = data['value']
        return SourcePage(ref, data['kind'], int(data['tick'], 16),
                          None if value is None else int(value, 16), data['provenance'])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f'交接件原页字段无效：{error}') from error


def _encode_ref(ref: PageRef) -> dict:
    return {'cycle': ref.cycle, 'channel': ref.channel,
            'sequence': hex(ref.sequence), 'page_id': ref.page_id}


def _decode_ref(data: dict) -> PageRef:
    if not isinstance(data, dict):
        raise ValueError('交接件原页身份必须是对象')
    try:
        return PageRef(data['cycle'], data['channel'], int(data['sequence'], 16), data['page_id'])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f'交接件原页身份字段无效：{error}') from error


def _encode_result(issuer: str, result: SignedResult) -> dict:
    return {
        'issuer': issuer,
        'result_id': result.result_id,
        'cycle': result.cycle,
        'start_tick': hex(result.window.start),
        'stop_tick': hex(result.window.stop),
        'channel_a': result.channels.a,
        'channel_b': result.channels.b,
        'witness_tick': hex(result.tick),
        'delta': hex(result.delta_kpa),
        'evidence_a': encode_evidence(result.a),
        'evidence_b': encode_evidence(result.b),
        'receipt_id': result.receipt.result_id,
        'receipt_code': result.receipt.code,
    }


def _decode_result(data: dict) -> tuple[str, SignedResult]:
    if not isinstance(data, dict):
        raise ValueError('交接件成果必须是对象')
    try:
        if type(data['result_id']) is not int or data['result_id'] < 1:
            raise ValueError('成果号必须是正整数')
        window = TickWindow(int(data['start_tick'], 16), int(data['stop_tick'], 16))
        result = SignedResult(
            data['result_id'], data['cycle'],
            validate_pair(data['channel_a'], data['channel_b']),
            window, int(data['witness_tick'], 16),
            decode_evidence(data['evidence_a']), decode_evidence(data['evidence_b']),
            int(data['delta'], 16),
            FirstReceipt(data['receipt_id'], data['receipt_code']),
        )
        issuer = data['issuer']
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f'交接件成果字段无效：{error}') from error
    if type(issuer) is not str or not issuer:
        raise ValueError('交接件成果缺少原签发端')
    return issuer, result


def _payload(registration: CycleRegistration, events, releases, results,
             sender: str, recipient: str) -> dict:
    return {
        'format': FORMAT,
        'sender': sender,
        'recipient': recipient,
        'cycle': registration.cycle,
        'registration': {
            'a': registration.channels.a,
            'b': registration.channels.b,
            'start': hex(registration.bounds.start),
            'stop': hex(registration.bounds.stop),
        },
        'events': [[channel, [_encode_page(page) for page in pages]] for channel, pages in events],
        'releases': [[_encode_ref(ref), progress.value] for ref, progress in releases],
        'results': [_encode_result(issuer, result) for issuer, result in results],
    }


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def _digest(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def encode_bundle_from_parts(*, sender: str, recipient: str, registration: CycleRegistration,
                             events, releases, results) -> bytes:
    payload = _payload(registration, events, releases, results, sender, recipient)
    digest = _digest(payload)
    document = dict(payload)
    document['digest'] = digest
    document['handoff_id'] = f'HO-{digest[:20]}'
    return _canonical(document)


def encode_bundle(bundle: Bundle) -> bytes:
    return encode_bundle_from_parts(sender=bundle.sender, recipient=bundle.recipient,
                                    registration=bundle.registration, events=bundle.events,
                                    releases=bundle.releases, results=bundle.results)


def parse_bundle(raw: bytes) -> Bundle:
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError('交接件必须是字节串')
    try:
        document = json.loads(bytes(raw).decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('交接件不是有效的 UTF-8 JSON') from error
    except (TypeError, ValueError) as error:
        raise ValueError('交接件不是字节串') from error
    if not isinstance(document, dict) or document.get('format') != FORMAT:
        raise ValueError('交接件格式不受支持')

    payload = {key: value for key, value in document.items()
               if key not in ('digest', 'handoff_id')}
    digest = _digest(payload)
    if document.get('digest') != digest or document.get('handoff_id') != f'HO-{digest[:20]}':
        raise ValueError('交接件完整性校验失败')

    sender = document.get('sender')
    recipient = document.get('recipient')
    cycle = document.get('cycle')
    for value in (sender, recipient, cycle):
        if type(value) is not str or not value:
            raise ValueError('交接件缺少参与端或轮号')
    if sender == recipient:
        raise ValueError('交接件的交出端与接收端不能相同')

    registration_data = document.get('registration')
    if not isinstance(registration_data, dict):
        raise ValueError('交接件缺少轮登记')
    try:
        registration = CycleRegistration(
            cycle,
            validate_pair(registration_data['a'], registration_data['b']),
            validate_cycle_range(int(registration_data['start'], 16),
                                 int(registration_data['stop'], 16)),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f'交接件的轮登记无效：{error}') from error

    events_payload = document.get('events')
    if not isinstance(events_payload, list):
        raise ValueError('交接件缺少原页账')
    lanes: dict[str, list[SourcePage]] = {}
    index: dict[tuple[str, str, int, str], SourcePage] = {}
    for lane in events_payload:
        if not isinstance(lane, list) or len(lane) != 2:
            raise ValueError('交接件原页账格式无效')
        channel, pages = lane
        if channel in lanes:
            raise ValueError('交接件原页账重复表号')
        decoded = []
        previous = None
        for item in pages:
            page = _decode_page(item)
            if page.ref.channel != channel:
                raise ValueError('交接件原页与所在表不符')
            if registration.cycle != page.ref.cycle:
                raise ValueError('交接件原页属于另一巡检轮')
            validate_event_order(registration, page, previous)
            decoded.append(page)
            index[page.ref.key] = page
            previous = page
        lanes[channel] = decoded
    if set(lanes) - {registration.channels.a, registration.channels.b}:
        raise ValueError('交接件含未登记表号的原页账')
    lanes.setdefault(registration.channels.a, [])
    lanes.setdefault(registration.channels.b, [])

    releases_payload = document.get('releases')
    if not isinstance(releases_payload, list):
        raise ValueError('交接件缺少放页资格账')
    releases = []
    seen_refs = set()
    for item in releases_payload:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError('交接件放页资格格式无效')
        ref = _decode_ref(item[0])
        progress = item[1]
        if progress not in _PROGRESS:
            raise ValueError('交接件放页进度无效')
        if ref.key not in index:
            raise ValueError('交接件的放页资格没有对应原页')
        if ref.key in seen_refs:
            raise ValueError('交接件的放页资格重复')
        seen_refs.add(ref.key)
        releases.append((ref, ReleaseProgress(progress)))
    missing = set(index) - seen_refs
    if missing:
        raise ValueError('交接件缺少原页的放页资格')

    results_payload = document.get('results')
    if not isinstance(results_payload, list):
        raise ValueError('交接件缺少成果账')
    results = []
    seen_results = set()
    seen_windows = set()
    for item in results_payload:
        issuer, result = _decode_result(item)
        if result.cycle != registration.cycle:
            raise ValueError('交接件成果属于另一巡检轮')
        if result.channels != registration.channels:
            raise ValueError('交接件成果的甲乙角色与轮登记不符')
        if result.receipt.result_id != result.result_id:
            raise ValueError('交接件首执与成果号不符')
        if (issuer, result.result_id) in seen_results:
            raise ValueError('交接件成果重复')
        if (result.window.start, result.window.stop) in seen_windows:
            raise ValueError('交接件成果窗重复')
        for evidence in (result.a, result.b):
            for page in (evidence.reading, evidence.closing):
                stored = index.get(page.ref.key)
                if stored is None or stored != page:
                    raise ValueError('交接件成果原据不在原页账内')
        if not reconcile_result(result).valid:
            raise ValueError('交接件成果与自身原据不符')
        seen_results.add((issuer, result.result_id))
        seen_windows.add((result.window.start, result.window.stop))
        results.append((issuer, result))

    events = ((registration.channels.a, tuple(lanes[registration.channels.a])),
              (registration.channels.b, tuple(lanes[registration.channels.b])))
    return Bundle(document['handoff_id'], digest, sender, recipient, registration,
                  events, tuple(releases), tuple(results))
