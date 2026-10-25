"""HID side: find the dongle, open all four collections, poll the battery,
detect headset on/off by missed replies and survive unplug / replug.

Everything runs on daemon threads and reports through a ``sink(event)``
callable that must be thread-safe (the app passes ``queue.Queue.put``).
The ``hid`` module, clocks and print function are injectable for tests.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import battery, protocol
from .battery import Presence, PresenceTracker

log = logging.getLogger(__name__)
hid_log = logging.getLogger("hyperx_battery.hid")

READ_TIMEOUT_MS = 250
READ_SIZE = 64


# --------------------------------------------------------------------------
# Events delivered to the sink
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class DongleEvent:
    """The USB dongle was opened (True) or lost / not found (False)."""

    connected: bool


@dataclass(frozen=True)
class PresenceEvent:
    """Headset powered on (True) or off (False), inferred from replies."""

    on: bool


@dataclass(frozen=True)
class BatteryEvent:
    mv: int
    mono: float     # monotonic clock, for smoothing windows
    wall: float     # epoch seconds, for history


@dataclass(frozen=True)
class MuteEvent:
    muted: bool


Event = Any  # one of the dataclasses above


@dataclass
class _Collection:
    usage_page: int
    usage: int
    path: bytes
    dev: Any = None
    thread: Optional[threading.Thread] = None

    @property
    def label(self) -> str:
        return f"{self.usage_page:04X}/{self.usage:04X}"

    @property
    def is_control(self) -> bool:
        return self.usage_page == protocol.CONTROL_USAGE_PAGE


class _DongleLost(Exception):
    """Raised inside a session when the dongle must be re-enumerated."""


class HeadsetMonitor:
    """Owns the HID connection on a daemon thread."""

    def __init__(
        self,
        sink: Callable[[Event], None],
        *,
        poll_interval_s: float = 5.0,
        reply_timeout_s: float = 2.0,
        misses_to_off: int = 2,
        retry_s: float = 5.0,
        debug_hid: bool = False,
        hid_module: Any = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        out: Callable[[str], None] = lambda text: print(text, flush=True),
    ) -> None:
        self._sink = sink
        self.poll_interval_s = poll_interval_s
        self.reply_timeout_s = reply_timeout_s
        self.retry_s = retry_s
        self.debug_hid = debug_hid
        self._hid = hid_module
        self._clock = clock
        self._wall = wall
        self._out = out
        self._presence = PresenceTracker(misses_to_off)
        self._stop = threading.Event()
        self._poke = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._dongle: Optional[bool] = None
        self._seen_unknown: Dict[bytes, int] = {}

    # -- public API ----------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="hid-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def poll_now(self) -> None:
        """Ask for an immediate battery query (e.g. after a power-on event)."""
        self._poke.set()

    # -- main loop ------------------------------------------------------------------
    def _hid_module(self) -> Any:
        if self._hid is None:
            import hid  # lazy: lets the module import without the DLL for tests

            self._hid = hid
        return self._hid

    def _emit(self, event: Event) -> None:
        try:
            self._sink(event)
        except Exception:  # noqa: BLE001
            log.exception("Event sink failed for %r", event)

    def _set_dongle(self, connected: bool) -> None:
        """Emit DongleEvent only on change (the very first call always emits)."""
        if self._dongle is not connected:
            self._dongle = connected
            self._emit(DongleEvent(connected))

    def _run(self) -> None:
        missing_logged = False
        while not self._stop.is_set():
            was_connected = False
            try:
                was_connected = self._session()
            except _DongleLost as exc:
                was_connected = True
                log.warning("Dongle lost: %s", exc)
            except Exception:  # noqa: BLE001
                log.exception("HID session crashed")
            if self._stop.is_set():
                break
            # Session ended (or never started): the headset cannot be on.
            self._set_dongle(False)
            if self._presence.force_off() is Presence.OFF:
                self._emit(PresenceEvent(False))
            if was_connected:
                missing_logged = False
            if not missing_logged:
                log.info("Waiting for the HyperX dongle (retrying every %.0f s)", self.retry_s)
                missing_logged = True
            self._stop.wait(self.retry_s)

    def _enumerate(self) -> List[_Collection]:
        infos = self._hid_module().enumerate(protocol.VID, protocol.PID)
        return [
            _Collection(int(i["usage_page"]), int(i.get("usage", 0)), i["path"]) for i in infos
        ]

    def _session(self) -> bool:
        """One connected session. Returns False if the dongle was not found."""
        collections = self._enumerate()
        if not any(c.is_control for c in collections):
            return False
        hidmod = self._hid_module()
        reports: "queue.Queue[Tuple[_Collection, Any]]" = queue.Queue()
        session_stop = threading.Event()
        try:
            for col in collections:
                try:
                    dev = hidmod.device()
                    dev.open_path(col.path)
                    col.dev = dev
                except Exception as exc:  # noqa: BLE001
                    if col.is_control:
                        raise _DongleLost(f"cannot open control collection: {exc}") from exc
                    log.warning("Cannot open collection %s: %s", col.label, exc)
                    continue
                log.info("Opened HID collection %s", col.label)
            ctrl = next(c for c in collections if c.is_control)
            for col in collections:
                if col.dev is None:
                    continue
                col.thread = threading.Thread(
                    target=self._reader, args=(col, reports, session_stop),
                    name=f"hid-read-{col.label}", daemon=True,
                )
                col.thread.start()
            self._set_dongle(True)
            log.info("Dongle connected (%d collection(s) open)", sum(c.dev is not None for c in collections))
            self._poll_loop(ctrl, reports)
            return True
        finally:
            self._teardown(collections, session_stop)

    def _teardown(self, collections: List[_Collection], session_stop: threading.Event) -> None:
        session_stop.set()
        for col in collections:
            if col.thread is not None:
                col.thread.join(timeout=2.0)
        for col in collections:
            if col.dev is None:
                continue
            if col.thread is not None and col.thread.is_alive():
                log.warning("Reader %s did not stop; leaking its handle", col.label)
                continue
            try:
                col.dev.close()
            except Exception:  # noqa: BLE001
                log.debug("close failed for %s", col.label, exc_info=True)

    def _reader(self, col: _Collection, reports: "queue.Queue", stop: threading.Event) -> None:
        """Blocking reads with a short timeout so the thread can be stopped."""
        while not stop.is_set():
            try:
                data = col.dev.read(READ_SIZE, READ_TIMEOUT_MS)
            except Exception as exc:  # noqa: BLE001
                if not stop.is_set():
                    reports.put((col, exc))
                return
            if data:
                reports.put((col, bytes(data)))

    # -- polling / state machine ----------------------------------------------------
    def _poll_loop(self, ctrl: _Collection, reports: "queue.Queue") -> None:
        awaiting_since: Optional[float] = None
        next_poll = self._clock()
        while not self._stop.is_set():
            now = self._clock()
            if self._poke.is_set():
                self._poke.clear()
                next_poll = min(next_poll, now)

            if awaiting_since is not None and now - awaiting_since >= self.reply_timeout_s:
                awaiting_since = None
                self._miss()

            if now >= next_poll:
                if awaiting_since is not None:       # previous query never answered
                    awaiting_since = None
                    self._miss()
                try:
                    written = ctrl.dev.write(protocol.BATTERY_REQUEST)
                    if written is not None and written < 0:
                        raise OSError("write returned an error")
                    awaiting_since = now
                except Exception as exc:  # noqa: BLE001
                    if not self._dongle_present():
                        raise _DongleLost(f"write failed and dongle is gone: {exc}") from exc
                    log.debug("Battery request write failed: %s", exc)
                    self._miss()
                next_poll = now + self.poll_interval_s

            deadline = next_poll
            if awaiting_since is not None:
                deadline = min(deadline, awaiting_since + self.reply_timeout_s)
            wait = max(0.01, min(deadline - self._clock(), 0.25))
            try:
                col, payload = reports.get(timeout=wait)
            except queue.Empty:
                continue
            if isinstance(payload, Exception):
                if col.is_control:
                    raise _DongleLost(f"read error on {col.label}: {payload}")
                log.warning("Collection %s stopped reading: %s", col.label, payload)
                continue
            if self._handle_report(col, payload):
                awaiting_since = None

    def _miss(self) -> None:
        if self._presence.on_miss() is Presence.OFF:
            log.info("Headset is OFF (no battery replies)")
            self._emit(PresenceEvent(False))

    def _dongle_present(self) -> bool:
        try:
            return any(c.is_control for c in self._enumerate())
        except Exception:  # noqa: BLE001
            return False

    def _handle_report(self, col: _Collection, data: bytes) -> bool:
        """Process one raw report. Returns True if it was a battery reply."""
        report = protocol.parse_report(data)
        if self.debug_hid:
            self._out(f"[{time.strftime('%H:%M:%S')}] HID {col.label}  {protocol.hex_dump(data)}")
        if isinstance(report, protocol.BatteryReport):
            if self._presence.on_reply() is Presence.ON:
                log.info("Headset is ON")
                self._emit(PresenceEvent(True))
            self._emit(BatteryEvent(report.mv, self._clock(), self._wall()))
            return True
        if isinstance(report, protocol.MuteReport):
            log.info("Mute event: %s (raw %s on %s)", "muted" if report.muted else "active",
                     protocol.hex_dump(data, 8), col.label)
            self._emit(MuteEvent(report.muted))
        elif isinstance(report, protocol.PowerReport):
            log.info("Power event: %s (raw %s on %s)", "on" if report.on else "off",
                     protocol.hex_dump(data, 8), col.label)
            if report.on:
                self.poll_now()
            elif self._presence.force_off() is Presence.OFF:
                self._emit(PresenceEvent(False))
        else:
            self._log_unknown(col, data)
        return False

    def _log_unknown(self, col: _Collection, data: bytes) -> None:
        """DEBUG-log unknown reports (these are how mute / power events get
        discovered). Identical reports are logged at most 20 times."""
        key = bytes([col.usage_page & 0xFF]) + data
        count = self._seen_unknown.get(key, 0)
        if count < 20:
            self._seen_unknown[key] = count + 1
            hid_log.debug("Unknown report on %s: %s", col.label, protocol.hex_dump(data))


# --------------------------------------------------------------------------
# Hardware-free stand-in for --simulate-percent
# --------------------------------------------------------------------------
class SimulatedMonitor:
    """Emits a steady battery level as if the headset were on."""

    def __init__(
        self,
        sink: Callable[[Event], None],
        percent: float,
        poll_interval_s: float = 5.0,
        charging: bool = False,
    ) -> None:
        self._sink = sink
        self._interval = poll_interval_s
        self._mv = int(round(battery.percent_to_mv(percent)))
        if charging:
            self._mv = max(self._mv, battery.CHARGING_MV_THRESHOLD + 30)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="simulated-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def poll_now(self) -> None:  # API parity with HeadsetMonitor
        pass

    def _run(self) -> None:
        self._sink(DongleEvent(True))
        self._sink(PresenceEvent(True))
        while not self._stop.is_set():
            self._sink(BatteryEvent(self._mv, time.monotonic(), time.time()))
            self._stop.wait(self._interval)
