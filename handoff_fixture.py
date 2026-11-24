"""换端演练的原始录制、放页观测点及 V1 对照。"""
import json
from collections import deque
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from audit.channel_history import read_channel_history
from audit.reconcile import reconcile_result
from capture.custody import ReleaseProgress
from capture.intake import collect
from device.recording import Recording
from device.slots import DeviceSlots
from device.source_pages import PageRef, SourcePage
from rounds.registration import open_cycle
from signing.commit import sign_window
from workbench import open_archive, open_device


def handoff_recording():
    lanes = {
        ("R", "T1", "A"): [(10, 7), (12, 9), (14, None)],
        ("R", "T2", "B"): [(10, 11), (14, None)],
        ("R2", "T1", "A"): [(12, 16), (14, None)],
        ("R2", "T2", "B"): [(12, 12), (14, None)],
    }
    return Recording(
        SourcePage(PageRef(cycle, channel, seq, f"{prefix}{seq}"),
                   "terminal" if value is None else "reading", tick, value,
                   f"handoff/{cycle}/{channel}/{seq}")
        for (cycle, channel, prefix), readings in lanes.items()
        for seq, (tick, value) in enumerate(readings, 1)
    )


class ReleaseGate:
    def __init__(self, ref, phase):
        if phase not in ("before_request", "before_reply"):
            raise ValueError("未知放页观测点")
        self.ref = ref
        self.phase = phase
        self.reached = Event()
        self.proceed = Event()

    def pause_at(self, ref, phase):
        if (ref, phase) == (self.ref, self.phase) and not self.reached.is_set():
            self.reached.set()
            if not self.proceed.wait(10):
                raise TimeoutError("放页夹具未收到继续信号")


class ObservedDevice(DeviceSlots):
    def __init__(self, device, gate=None):
        super().__init__(device.connection, device.recording, device.delivery)
        self.gate = gate
        self.requests = []
        self.queries = []

    def release_page(self, ref):
        self.requests.append(ref)
        if self.gate is not None:
            self.gate.pause_at(ref, "before_request")
        outcome = super().release_page(ref)
        if self.gate is not None:
            self.gate.pause_at(ref, "before_reply")
        return outcome

    def query_release(self, ref):
        self.queries.append(ref)
        return super().query_release(ref)


def prepare_before_a2(leaving, receiving, device):
    open_cycle(leaving, "R", "T1", "T2", 10, 16)
    open_cycle(receiving, "R2", "T1", "T2", 10, 16)
    first = collect(leaving, device, "R", "T1")
    if first is None or first.progress != ReleaseProgress.RELEASED:
        raise RuntimeError("夹具未放行甲表 A1")
    for db, cycle, channels in ((leaving, "R", ("T2",)), (receiving, "R2", ("T1", "T2"))):
        for channel in channels:
            while (item := collect(db, device, cycle, channel)) is not None:
                if item.progress != ReleaseProgress.RELEASED:
                    raise RuntimeError(f"夹具未放行 {item.event.page.ref}")
    result = sign_window(receiving, "R2", 12, 14).result
    if result is None:
        raise RuntimeError("夹具未签出接班端已有轮的成果")
    return result


def check_baseline(leaving, receiving, device):
    receiving_result = prepare_before_a2(leaving, receiving, device)
    a2 = device.read_current("R", "T1")
    device.delivery.replies[a2.ref] = deque([False])
    collected = collect(leaving, device, "R", "T1", a2)
    old = sign_window(leaving, "R", 10, 12).result
    if old is None:
        raise RuntimeError("夹具未签出交出端的原窗成果")

    open_cycle(receiving, "R", "T1", "T2", 10, 16)
    try:
        collect(receiving, device, "R", "T1")
    except ValueError as error:
        fresh_round_error = str(error)
    else:
        raise RuntimeError("V1 缺少甲表前账时竟能接纳后页")

    actual = {
        "receipts": [[item.cycle, item.result_id, item.receipt.code, item.delta_kpa]
                     for item in (old, receiving_result)],
        "verified": [reconcile_result(item).valid for item in (old, receiving_result)],
        "old_a2_progress": collected.progress.value,
        "device_current": device.read_current("R", "T1").ref.page_id,
        "receiving_r_t1": [item.ref.page_id for item in read_channel_history(receiving, "R", "T1")],
        "fresh_round_error": fresh_round_error,
    }
    expected = {
        "receipts": [["R", 1, "PW-000001", 4], ["R2", 1, "PW-000001", -4]],
        "verified": [True, True],
        "old_a2_progress": "unknown",
        "device_current": "A3",
        "receiving_r_t1": [],
        "fresh_round_error": "原页原序未接续实际已实收位置",
    }
    if actual != expected:
        raise RuntimeError(f"V1 换端对照差异 {actual!r}，预期 {expected!r}")
    return actual


def main():
    with TemporaryDirectory(prefix="pump-handoff-") as directory:
        root = Path(directory)
        with (closing(open_archive(root / "leaving.sqlite")) as leaving,
              closing(open_archive(root / "receiving.sqlite")) as receiving,
              closing(open_device(root / "recorder.sqlite", handoff_recording())) as device):
            actual = check_baseline(leaving, receiving, device)
    print(json.dumps(actual, ensure_ascii=False))


if __name__ == "__main__":
    main()
