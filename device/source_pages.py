from dataclasses import dataclass


@dataclass(frozen=True)
class PageRef:
    cycle: str
    channel: str
    sequence: int
    page_id: str

    def __post_init__(self):
        identities = (self.cycle, self.channel, self.page_id)
        if any(type(identity) is not str or not identity for identity in identities):
            raise ValueError('原页的轮、表和页号必须是非空字符串')
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError('设备原序必须是正整数')

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.cycle, self.channel, hex(self.sequence), self.page_id


@dataclass(frozen=True)
class SourcePage:
    ref: PageRef
    kind: str
    tick: int
    value: int | None
    provenance: str
