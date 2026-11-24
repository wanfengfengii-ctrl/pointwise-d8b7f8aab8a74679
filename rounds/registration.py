import sqlite3
from dataclasses import dataclass

from rounds.channels import ChannelPair, validate_pair
from rounds.registry_store import find_registration, insert_registration
from rounds.windows import TickWindow, validate_cycle_range


@dataclass(frozen=True)
class CycleRegistration:
    cycle: str
    channels: ChannelPair
    bounds: TickWindow


def _validate_cycle_id(cycle: str) -> None:
    if type(cycle) is not str or not cycle:
        raise ValueError('轮号必须是非空字符串')


def open_cycle(db: sqlite3.Connection, cycle: str, a: str, b: str,
               start: int, stop: int) -> CycleRegistration:
    _validate_cycle_id(cycle)
    registration = CycleRegistration(cycle, validate_pair(a, b), validate_cycle_range(start, stop))
    with db:
        db.execute('BEGIN IMMEDIATE')
        existing = find_registration(db, cycle)
        if existing is None:
            insert_registration(db, cycle, a, b, start, stop)
        elif existing != (a, b, start, stop):
            raise ValueError('同一轮号只能沿用原甲乙角色和范围')
    return registration


def get_cycle(db: sqlite3.Connection, cycle: str) -> CycleRegistration:
    _validate_cycle_id(cycle)
    existing = find_registration(db, cycle)
    if existing is None:
        raise ValueError(f'未知巡检轮 {cycle!r}')
    a, b, start, stop = existing
    return CycleRegistration(cycle, ChannelPair(a, b), TickWindow(start, stop))
