from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelPair:
    a: str
    b: str


def validate_pair(a: str, b: str) -> ChannelPair:
    if any(type(channel) is not str or not channel for channel in (a, b)) or a == b:
        raise ValueError('甲乙须为两个不同的非空表号')
    return ChannelPair(a, b)


def role_for_channel(pair: ChannelPair, channel: str) -> str:
    if channel == pair.a:
        return 'a'
    if channel == pair.b:
        return 'b'
    raise ValueError(f'该轮未登记表号 {channel!r}')
