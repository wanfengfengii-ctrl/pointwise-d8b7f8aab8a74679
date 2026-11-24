from device.source_pages import PageRef, SourcePage
from rounds.channels import role_for_channel
from rounds.closure import validate_channel_open, validate_terminal_tick
from rounds.registration import CycleRegistration


def validate_current_page(submitted: SourcePage, actual: SourcePage | None) -> None:
    if actual is None or not isinstance(submitted.ref, PageRef) or submitted.ref != actual.ref:
        raise ValueError('提交原页与实际原页身份不符')
    for attribute in ('kind', 'tick', 'value', 'provenance'):
        reported = getattr(submitted, attribute)
        original = getattr(actual, attribute)
        if type(reported) is not type(original) or reported != original:
            raise ValueError(f'提交原页的 {attribute} 与实际载荷不符')


def validate_event_order(registration: CycleRegistration, page: SourcePage,
                         previous: SourcePage | None) -> None:
    if page.ref.cycle != registration.cycle:
        raise ValueError('原页属于另一巡检轮')
    role_for_channel(registration.channels, page.ref.channel)
    validate_channel_open(previous.kind if previous is not None else None)
    expected_sequence = previous.ref.sequence + 1 if previous is not None else 1
    if page.ref.sequence != expected_sequence:
        raise ValueError('原页原序未接续实际已实收位置')
    if type(page.provenance) is not str or not page.provenance:
        raise ValueError('原页缺少实际出处')
    if page.kind == 'terminal':
        if page.value is not None:
            raise ValueError('轮末事件不携带压力读数')
        validate_terminal_tick(page.tick, registration.bounds, previous.tick if previous else None)
        return
    if page.kind != 'reading' or type(page.value) is not int:
        raise ValueError('普通原页必须携带整数 kPa 读数')
    if type(page.tick) is not int or not registration.bounds.contains(page.tick):
        raise ValueError('普通原页刻度超出登记范围')
    if previous is not None and page.tick < previous.tick:
        raise ValueError('普通原页刻度不能回退')
