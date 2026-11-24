from rounds.windows import TickWindow


def validate_terminal_tick(tick: int, cycle_range: TickWindow, previous_tick: int | None) -> None:
    if type(tick) is not int or not cycle_range.start <= tick <= cycle_range.stop:
        raise ValueError('轮末刻度超出登记范围')
    if previous_tick is not None and tick < previous_tick:
        raise ValueError('轮末刻度不能早于本路已实收刻度')


def validate_channel_open(previous_kind: str | None) -> None:
    if previous_kind == 'terminal':
        raise ValueError('本路轮末之后不能接纳新事件')
