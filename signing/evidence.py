import json
from dataclasses import dataclass

from device.source_pages import PageRef, SourcePage
from signing.coverage import ProvenInterval


@dataclass(frozen=True)
class IntervalEvidence:
    reading: SourcePage
    closing: SourcePage


def freeze_interval_evidence(interval: ProvenInterval) -> IntervalEvidence:
    return IntervalEvidence(interval.reading.page, interval.closing.page)


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
    ref = PageRef(data['cycle'], data['channel'], int(data['sequence'], 16), data['page_id'])
    value = data['value']
    return SourcePage(ref, data['kind'], int(data['tick'], 16),
                      None if value is None else int(value, 16), data['provenance'])


def encode_evidence(evidence: IntervalEvidence) -> str:
    return json.dumps({'reading': _encode_page(evidence.reading), 'closing': _encode_page(evidence.closing)},
                      ensure_ascii=False, separators=(',', ':'))


def decode_evidence(document: str) -> IntervalEvidence:
    data = json.loads(document)
    return IntervalEvidence(_decode_page(data['reading']), _decode_page(data['closing']))
