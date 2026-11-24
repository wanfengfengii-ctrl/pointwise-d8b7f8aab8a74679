import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from device.source_pages import PageRef, SourcePage


class Recording:
    def __init__(self, pages: Iterable[SourcePage]):
        lanes = defaultdict(list)
        for page in pages:
            lanes[page.ref.cycle, page.ref.channel].append(page)
        self._lanes = {lane: tuple(events) for lane, events in lanes.items()}

    def page_at(self, cycle: str, channel: str, position: int) -> SourcePage | None:
        lane = self._lanes.get((cycle, channel), ())
        if 0 <= position < len(lane):
            return lane[position]
        return None


def load_recording(path: str | Path) -> Recording:
    with Path(path).open(encoding='utf-8') as stream:
        document = json.load(stream)
    if not isinstance(document, dict) or not isinstance(document.get('pages'), list):
        raise ValueError('录制必须包含 pages 数组')
    pages = []
    for item in document['pages']:
        if not isinstance(item, dict):
            raise ValueError('录制页必须是对象')
        try:
            ref = PageRef(item['cycle'], item['channel'], item['sequence'], item['page_id'])
            # The recorder transports raw payloads. Acceptance validates their meaning.
            page = SourcePage(ref, item['kind'], item['tick'], item.get('value'), item['provenance'])
        except KeyError as error:
            raise ValueError(f'录制页缺少信封字段 {error.args[0]}') from error
        pages.append(page)
    return Recording(pages)
