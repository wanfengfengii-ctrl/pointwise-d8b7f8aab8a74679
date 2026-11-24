"""栖湾泵房巡检轮换端的对外入口。

入口函数按需从 :mod:`handoff.service` 取用，避免与签发模块的导入环。
"""
_EXPORTS = ('bind_terminal', 'export_cycle', 'import_cycle', 'retrieve_issued')

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name in _EXPORTS:
        from handoff import service
        return getattr(service, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__():
    return sorted(__all__)
