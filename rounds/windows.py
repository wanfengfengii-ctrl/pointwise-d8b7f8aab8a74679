from dataclasses import dataclass


@dataclass(frozen=True)
class TickWindow:
    start: int
    stop: int

    @property
    def empty(self) -> bool:
        return self.start >= self.stop

    def contains(self, tick: int) -> bool:
        return self.start <= tick < self.stop


def validate_query_window(start: int, stop: int) -> TickWindow:
    if type(start) is not int or type(stop) is not int or start > stop:
        raise ValueError('查询窗需要非倒置的整数半开端点')
    return TickWindow(start, stop)


def validate_cycle_range(start: int, stop: int) -> TickWindow:
    window = validate_query_window(start, stop)
    if window.empty:
        raise ValueError('巡检轮范围不能为空')
    return window


def clip_to_cycle(window: TickWindow, cycle_range: TickWindow) -> TickWindow:
    start = max(window.start, cycle_range.start)
    stop = min(window.stop, cycle_range.stop)
    return TickWindow(start, max(start, stop))
