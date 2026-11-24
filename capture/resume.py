import sqlite3

from capture.custody import ReleaseProgress, get_release_progress, update_release_progress
from device.release_log import ReleaseOutcome
from device.slots import DeviceSlots
from device.source_pages import PageRef


def attempt_release(db: sqlite3.Connection, device: DeviceSlots, ref: PageRef) -> ReleaseProgress:
    if get_release_progress(db, ref) is ReleaseProgress.RELEASED:
        return ReleaseProgress.RELEASED
    if db.in_transaction:
        raise RuntimeError('必须先提交已接纳原据，才能释放设备页')
    outcome = device.release_page(ref)
    return update_release_progress(db, ref, ReleaseProgress(outcome.value))


def resume_release(db: sqlite3.Connection, device: DeviceSlots, ref: PageRef) -> ReleaseProgress:
    if get_release_progress(db, ref) is ReleaseProgress.RELEASED:
        return ReleaseProgress.RELEASED
    outcome = device.query_release(ref)
    if outcome is ReleaseOutcome.NOT_APPLIED:
        current = device.read_current(ref.cycle, ref.channel)
        if current is not None and current.ref == ref:
            return attempt_release(db, device, ref)
        outcome = ReleaseOutcome.UNKNOWN
    return update_release_progress(db, ref, ReleaseProgress(outcome.value))
