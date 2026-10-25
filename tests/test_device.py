"""HeadsetMonitor against a fake `hid` module (no hardware)."""
import logging
import queue
import threading
import time

import pytest

from hyperx_battery import protocol
from hyperx_battery.device import (
    BatteryEvent,
    DongleEvent,
    HeadsetMonitor,
    MuteEvent,
    PresenceEvent,
    SimulatedMonitor,
)

USAGE_PAGES = [0x000C, 0xFFC0, 0xFF90, 0xFF00]


class FakeHid:
    """Mimics the parts of the `hid` module the monitor uses."""

    def __init__(self):
        self.present = True
        self.headset_on = True
        self.mv = 4047
        self.devices = []
        self.fail_open_usage = set()
        self.write_raises = False

    # module API
    def enumerate(self, vid, pid):
        if not self.present:
            return []
        return [
            {"usage_page": up, "usage": 0x0303 if up == 0xFF90 else 1, "path": f"path-{up:04X}".encode()}
            for up in USAGE_PAGES
        ]

    def device(self):
        dev = FakeDevice(self)
        self.devices.append(dev)
        return dev

    # test helpers
    def inject(self, usage_page, data):
        for d in self.devices:
            if d.usage_page == usage_page and not d.closed:
                d.inbox.put(bytes(data))

    def open_devices(self):
        return [d for d in self.devices if not d.closed and d.opened]


class FakeDevice:
    def __init__(self, owner):
        self.owner = owner
        self.inbox = queue.Queue()
        self.usage_page = None
        self.opened = False
        self.closed = False
        self.writes = 0

    def open_path(self, path):
        up = int(path.decode().split("-")[1], 16)
        if not self.owner.present:
            raise OSError("open failed")
        if up in self.owner.fail_open_usage:
            raise OSError("access denied")
        self.usage_page = up
        self.opened = True

    def write(self, data):
        if not self.owner.present or self.owner.write_raises:
            raise OSError("write error")
        self.writes += 1
        if self.owner.headset_on and bytes(data)[:3] == protocol.BATTERY_REQUEST[:3]:
            mv = self.owner.mv
            reply = bytes([0x21, 0xFF, 0x05, mv >> 8, mv & 0xFF]) + bytes(15)
            self.owner.inject(0xFF90, reply)
        return len(data)

    def read(self, size, timeout_ms):
        if not self.owner.present:
            raise OSError("read error")
        try:
            return list(self.inbox.get(timeout=timeout_ms / 1000.0))
        except queue.Empty:
            return []

    def close(self):
        self.closed = True


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


class Collector:
    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def __call__(self, ev):
        with self.lock:
            self.events.append(ev)

    def of(self, kind):
        with self.lock:
            return [e for e in self.events if isinstance(e, kind)]

    def last_presence(self):
        p = self.of(PresenceEvent)
        return p[-1].on if p else None


@pytest.fixture
def rig():
    fake = FakeHid()
    sink = Collector()
    mon = HeadsetMonitor(
        sink, poll_interval_s=0.1, reply_timeout_s=0.05, misses_to_off=2, retry_s=0.1,
        hid_module=fake, out=lambda text: sink.events.append(("print", text)),
    )
    yield fake, sink, mon
    mon.stop()


def test_connects_opens_all_four_collections_and_reads_battery(rig):
    fake, sink, mon = rig
    mon.start()
    assert wait_for(lambda: sink.of(BatteryEvent))
    assert len(fake.open_devices()) == 4
    assert sink.of(DongleEvent)[0] == DongleEvent(True)
    kinds = [type(e) for e in sink.events]
    assert kinds.index(PresenceEvent) < kinds.index(BatteryEvent)      # ON comes before the first value
    assert sink.of(BatteryEvent)[0].mv == 4047
    assert sink.last_presence() is True


def test_headset_off_after_two_missed_replies_then_on_again(rig):
    fake, sink, mon = rig
    mon.start()
    assert wait_for(lambda: sink.last_presence() is True)
    fake.headset_on = False
    assert wait_for(lambda: sink.last_presence() is False)
    n_batteries = len(sink.of(BatteryEvent))
    time.sleep(0.4)                                                   # keeps polling while off
    assert len(sink.of(BatteryEvent)) == n_batteries
    assert fake.devices[2].writes > 4
    fake.headset_on = True
    assert wait_for(lambda: sink.last_presence() is True)
    assert len(sink.of(BatteryEvent)) > n_batteries


def test_off_headset_from_the_start_is_reported_off(rig):
    fake, sink, mon = rig
    fake.headset_on = False
    mon.start()
    assert wait_for(lambda: sink.last_presence() is False)
    assert not sink.of(BatteryEvent)
    assert sink.of(DongleEvent) == [DongleEvent(True)]


def test_off_needs_two_misses_not_one(rig):
    fake, sink, mon = rig
    mon.poll_interval_s = 0.3
    mon.start()
    assert wait_for(lambda: sink.last_presence() is True)
    fake.headset_on = False
    time.sleep(0.15)                                  # one poll missed, second not yet
    assert sink.last_presence() is True
    assert wait_for(lambda: sink.last_presence() is False)


def test_unplug_and_replug_the_dongle(rig):
    fake, sink, mon = rig
    mon.start()
    assert wait_for(lambda: sink.of(BatteryEvent))
    first_session = list(fake.devices)
    fake.present = False                               # yank the USB dongle
    assert wait_for(lambda: DongleEvent(False) in sink.of(DongleEvent))
    assert wait_for(lambda: sink.last_presence() is False)
    assert all(d.closed for d in first_session)        # everything was closed
    count = len(sink.of(BatteryEvent))
    fake.present = True                                # plug it back
    assert wait_for(lambda: len(sink.of(BatteryEvent)) > count)
    assert sink.of(DongleEvent)[-1] == DongleEvent(True)
    assert sink.last_presence() is True


def test_dongle_absent_at_start_is_retried(rig):
    fake, sink, mon = rig
    fake.present = False
    mon.start()
    assert wait_for(lambda: sink.of(DongleEvent) == [DongleEvent(False)])
    assert not sink.of(BatteryEvent)
    fake.present = True
    assert wait_for(lambda: sink.of(BatteryEvent))


def test_write_error_with_dongle_present_counts_as_a_miss(rig):
    fake, sink, mon = rig
    mon.start()
    assert wait_for(lambda: sink.last_presence() is True)
    fake.write_raises = True
    assert wait_for(lambda: sink.last_presence() is False)
    assert sink.of(DongleEvent) == [DongleEvent(True)]            # not treated as an unplug


def test_non_control_collection_that_cannot_be_opened_is_tolerated(rig):
    fake, sink, mon = rig
    fake.fail_open_usage = {0x000C}
    mon.start()
    assert wait_for(lambda: sink.of(BatteryEvent))
    assert len(fake.open_devices()) == 3


def test_mute_events_from_any_collection(rig):
    fake, sink, mon = rig
    mon.start()
    assert wait_for(lambda: sink.of(BatteryEvent))
    fake.inject(0xFF00, [0x65, 0x04])
    assert wait_for(lambda: MuteEvent(True) in sink.of(MuteEvent))
    fake.inject(0xFFC0, [0x65, 0x00])
    assert wait_for(lambda: MuteEvent(False) in sink.of(MuteEvent))


def test_power_off_event_marks_off_immediately(rig):
    fake, sink, mon = rig
    mon.poll_interval_s = 30                       # no poll will rescue us in time
    mon.start()
    assert wait_for(lambda: sink.last_presence() is True)
    fake.inject(0xFF90, [0x64, 0x03])
    assert wait_for(lambda: sink.last_presence() is False, timeout=1.0)


def test_power_on_event_triggers_an_immediate_poll(rig):
    fake, sink, mon = rig
    mon.poll_interval_s = 30
    fake.headset_on = False
    mon.start()
    assert wait_for(lambda: fake.devices and fake.devices[2].writes >= 1)     # first query unanswered
    assert sink.last_presence() is None
    fake.headset_on = True
    fake.inject(0xFF90, [0x64, 0x01])                  # next regular poll is 30 s away
    assert wait_for(lambda: sink.last_presence() is True, timeout=1.5)


def test_unknown_reports_are_logged_at_debug_but_do_not_break_anything(rig, caplog):
    fake, sink, mon = rig
    caplog.set_level(logging.DEBUG, logger="hyperx_battery.hid")
    mon.start()
    assert wait_for(lambda: sink.of(BatteryEvent))
    fake.inject(0xFF90, [0x77, 0x01, 0x02])
    assert wait_for(lambda: any("77 01 02" in r.getMessage() for r in caplog.records))
    rec = next(r for r in caplog.records if "77 01 02" in r.getMessage())
    assert rec.levelno == logging.DEBUG


def test_repeated_unknown_reports_are_rate_limited(rig, caplog):
    fake, sink, mon = rig
    caplog.set_level(logging.DEBUG, logger="hyperx_battery.hid")
    mon.start()
    assert wait_for(lambda: sink.of(BatteryEvent))
    for _ in range(60):
        fake.inject(0xFF90, [0x77, 0x01])
    time.sleep(0.5)
    assert sum("77 01" in r.getMessage() for r in caplog.records) == 20


def test_debug_hid_prints_every_report_from_every_collection(rig):
    fake, sink, mon = rig
    mon.debug_hid = True
    mon.start()
    assert wait_for(lambda: sink.of(BatteryEvent))
    fake.inject(0xFFC0, [0x01, 0x02, 0x03])
    assert wait_for(lambda: any(isinstance(e, tuple) and "FFC0/0001" in e[1] and "01 02 03" in e[1]
                                for e in sink.events))
    assert any(isinstance(e, tuple) and "FF90/0303" in e[1] and "21 ff 05" in e[1] for e in sink.events)


def test_stop_closes_all_handles(rig):
    fake, sink, mon = rig
    mon.start()
    assert wait_for(lambda: sink.of(BatteryEvent))
    mon.stop()
    assert all(d.closed for d in fake.devices)


def test_simulated_monitor_reports_requested_percent():
    from hyperx_battery.battery import mv_to_percent

    sink = Collector()
    sim = SimulatedMonitor(sink, 15, poll_interval_s=0.05)
    sim.start()
    assert wait_for(lambda: sink.of(BatteryEvent))
    sim.stop()
    assert sink.events[0] == DongleEvent(True) and sink.events[1] == PresenceEvent(True)
    assert round(mv_to_percent(sink.of(BatteryEvent)[0].mv)) == 15


def test_simulated_charging_is_above_the_charging_threshold():
    from hyperx_battery.battery import is_charging

    sink = Collector()
    sim = SimulatedMonitor(sink, 50, poll_interval_s=0.05, charging=True)
    sim.start()
    assert wait_for(lambda: sink.of(BatteryEvent))
    sim.stop()
    assert is_charging(sink.of(BatteryEvent)[0].mv)


def test_simulated_charge_can_start_from_a_discharging_level():
    from hyperx_battery.battery import is_charging, mv_to_percent

    sink = Collector()
    sim = SimulatedMonitor(sink, 82, poll_interval_s=0.05, charging=True, precharge_readings=2)
    sim.start()
    assert wait_for(lambda: len(sink.of(BatteryEvent)) >= 4)
    sim.stop()
    readings = sink.of(BatteryEvent)
    assert [is_charging(r.mv) for r in readings[:4]] == [False, False, True, True]
    assert round(mv_to_percent(readings[0].mv)) == 82
