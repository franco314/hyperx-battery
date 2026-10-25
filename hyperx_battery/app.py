"""Application controller: glues the device, battery logic, history,
keep-alive and tray together.

All state lives here and is only touched on the tkinter main thread:
worker threads (HID, session poller) and the pystray thread just call
:meth:`Controller.post`, and the main thread drains the queue with
:meth:`Controller.process_pending` (scheduled with ``root.after``).
"""

from __future__ import annotations

import logging
import os
import queue
import time
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from . import autostart
from .battery import BatteryStatus, BatteryTracker, LowBatteryNotifier
from .charge import (
    MAX_SESSION_S,
    STATE_FILE,
    format_time_to_full,
    load_estimator,
    save_estimator,
    start_from_history,
)
from .config import Config
from .device import BatteryEvent, DongleEvent, MuteEvent, PresenceEvent
from .history import (
    HistoryStore,
    MinuteAggregator,
    Sample,
    estimate_remaining_hours,
    format_remaining,
)
from .keepalive import KeepAliveDecision, decide_keepalive
from .session import SessionSnapshot
from .tray import TrayActions, TrayView

log = logging.getLogger(__name__)


class Controller:
    def __init__(
        self,
        cfg: Config,
        cfg_path: Path,
        data_dir: Path,
        store: HistoryStore,
        keepalive: Optional[Any],
        *,
        simulated: bool = False,
        show_history: Callable[[], None] = lambda: None,
        request_quit: Callable[[], None] = lambda: None,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.cfg = cfg
        self._cfg_path = cfg_path
        self._data_dir = data_dir
        self._store = store
        self._keepalive = keepalive
        self._simulated = simulated
        self._show_history = show_history
        self._request_quit = request_quit
        self._wall = wall
        self.ui: Optional[Any] = None           # set by the caller (TrayUI-like)

        self._queue: "queue.Queue[Any]" = queue.Queue()

        self._tracker = BatteryTracker(window_s=cfg.smoothing_window_s)
        self._notifier = LowBatteryNotifier(cfg.low_battery_thresholds)
        self._aggregator = MinuteAggregator(cfg.history_interval_s)

        # state mirrored from the workers
        self._dongle: Optional[bool] = None
        self._headset: Optional[bool] = None
        self._status: Optional[BatteryStatus] = None
        self._muted: Optional[bool] = None
        self._locked = False
        self._idle_s = 0.0
        self._remaining_text: Optional[str] = None
        self._decision = KeepAliveDecision(False, "headset_off")
        self._need_estimate = True

        # charge estimate (see charge.py); simulations never touch the real file
        self._charge_path: Optional[Path] = None if simulated else data_dir / STATE_FILE
        self._charge = load_estimator(self._charge_path, self._wall())
        self._was_charging: Optional[bool] = None
        self._last_shown: Optional[Tuple[float, int]] = None     # (wall, % shown before charging)

    # -- thread-safe entry points ---------------------------------------------------
    def post(self, item: Any) -> None:
        """Queue an event (or a zero-argument callable) for the main thread."""
        self._queue.put(item)

    def tray_actions(self) -> TrayActions:
        return TrayActions(
            toggle_keepalive=lambda: self.post(self.toggle_keepalive),
            keepalive_enabled=lambda: self.cfg.keepalive_enabled,
            show_history=lambda: self.post(self._show_history),
            toggle_autostart=lambda: self.post(self.toggle_autostart),
            autostart_enabled=autostart.is_enabled,
            open_data_folder=lambda: self.post(self.open_data_folder),
            quit=lambda: self.post(self._request_quit),
        )

    # -- main-thread processing -----------------------------------------------------
    def process_pending(self, limit: int = 500) -> None:
        for _ in range(limit):
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                if callable(item):
                    item()
                else:
                    self._handle(item)
            except Exception:  # noqa: BLE001
                log.exception("Error handling %r", item)

    def _handle(self, ev: Any) -> None:
        if isinstance(ev, BatteryEvent):
            self._on_battery(ev)
        elif isinstance(ev, PresenceEvent):
            self._on_presence(ev)
        elif isinstance(ev, DongleEvent):
            self._on_dongle(ev)
        elif isinstance(ev, MuteEvent):
            self._on_mute(ev)
        elif isinstance(ev, SessionSnapshot):
            self._locked, self._idle_s = ev.locked, ev.idle_s
        else:
            log.debug("Ignoring unknown event %r", ev)
            return
        self._apply_keepalive()
        self._refresh()

    # -- event handlers -------------------------------------------------------------
    def _on_dongle(self, ev: DongleEvent) -> None:
        self._dongle = ev.connected
        if not ev.connected and self._headset is not False:
            # Without the dongle the headset cannot be reached, i.e. "off".
            self._headset = False
            self._headset_gone()

    def _on_presence(self, ev: PresenceEvent) -> None:
        was = self._headset
        self._headset = ev.on
        if ev.on:
            # Off -> on: the old smoothing is meaningless, start fresh.
            self._tracker.reset()
            self._status = None
            self._was_charging = None
            self._need_estimate = True
        elif was is not False:
            self._headset_gone()

    def _headset_gone(self) -> None:
        self._flush_history()
        self._tracker.reset()
        self._status = None
        self._muted = None
        self._was_charging = None
        if self._charge.session is None:
            self._charge.cancel()               # unplugged and off: nothing left to learn from

    def _on_mute(self, ev: MuteEvent) -> None:
        changed = self._muted is not ev.muted
        self._muted = ev.muted
        if changed and self.cfg.notify_mute:
            self._notify("Micrófono silenciado" if ev.muted else "Micrófono activo")

    def _on_battery(self, ev: BatteryEvent) -> None:
        first = self._status is None
        status = self._tracker.update(ev.mono, ev.mv)
        self._status = status
        if first:
            log.info("Battery: %d mV -> %s", ev.mv,
                     "charging" if status.charging else f"{status.percent} %")

        if self.cfg.notify_low_battery:
            threshold = self._notifier.update(status.percent, status.charging)
            if threshold is not None:
                log.info("Low battery notification: %s %% (threshold %d %%)", status.percent, threshold)
                self._notify(f"Batería baja: {status.percent} %. Conecta el auricular para cargarlo.")
        elif status.charging:
            self._notifier.rearm()

        self._track_charge(ev.wall, status)

        sample = self._aggregator.add(ev.wall, ev.mv, status.percent, status.charging)
        if sample is not None:
            self._save_sample(sample)
            self._need_estimate = True
        if self._need_estimate:
            self._update_estimate()

    # -- charge estimate ------------------------------------------------------------
    def _track_charge(self, wall: float, status: BatteryStatus) -> None:
        was, self._was_charging = self._was_charging, status.charging
        if status.charging:
            if was is not True:
                self._charge_started(wall)
            return
        if status.percent is not None:
            self._last_shown = (wall, status.percent)
        if was is True:
            self._charge.finish(wall)
            self._save_charge()
        elif was is None and self._charge.session is not None:
            self._charge.cancel()               # the charge ended while we were not looking
            self._save_charge()
        rate = self._charge.settle(wall, status.percent)
        if rate is not None:
            log.info("Charge rate learned: %.1f %%/h", rate)
            self._save_charge()

    def _charge_started(self, wall: float) -> None:
        start = self._pre_charge_percent(wall)
        self._last_shown = None
        if start is None:
            percent, start_ts = None, wall
        else:
            percent, start_ts = start
        if self._charge.start(wall, percent, start_ts):
            log.info("Charge estimate from %s %% at rate %.1f %%/h",
                     self._charge.session.start_percent, self._charge.rate)
        else:
            log.info("Charging with no recent percentage: no estimate")
        self._save_charge()

    def _pre_charge_percent(self, wall: float) -> Optional[Tuple[float, float]]:
        """``(percent, start_ts)`` shown before this charge began, if recent."""
        if self._last_shown is not None and wall - self._last_shown[0] <= MAX_SESSION_S:
            return float(self._last_shown[1]), wall
        try:
            return start_from_history(self._store.range(wall - MAX_SESSION_S, wall), wall)
        except Exception:  # noqa: BLE001
            log.exception("Cannot read history for the charge start")
            return None

    def _save_charge(self) -> None:
        save_estimator(self._charge_path, self._charge)

    # -- history / estimate ---------------------------------------------------------
    def _save_sample(self, sample: Sample) -> None:
        try:
            self._store.add(sample)
            log.debug("History row: %s", sample)
        except Exception:  # noqa: BLE001
            log.exception("Cannot save history row")

    def _flush_history(self) -> None:
        sample = self._aggregator.flush()
        if sample is not None:
            self._save_sample(sample)

    def _update_estimate(self) -> None:
        self._need_estimate = False
        if self._status is None or self._status.charging:
            self._remaining_text = None
            return
        try:
            samples = self._store.session_samples(self._wall())
            hours = estimate_remaining_hours(
                samples,
                current_percent=self._status.percent,
                gap_s=max(180.0, 3 * self.cfg.history_interval_s),
            )
            self._remaining_text = format_remaining(hours)
        except Exception:  # noqa: BLE001
            log.exception("Remaining-time estimate failed")
            self._remaining_text = None

    # -- keep-alive -----------------------------------------------------------------
    def _apply_keepalive(self) -> None:
        self._decision = decide_keepalive(
            self.cfg.keepalive_enabled,
            self._headset is True,
            self._locked,
            self._idle_s,
            self.cfg.idle_pause_min,
        )
        if self._keepalive is not None:
            self._keepalive.set_wanted(self._decision.run)

    def _keepalive_text(self) -> str:
        if self._simulated or self._keepalive is None:
            return "desactivado (simulación)" if self._simulated else "no disponible"
        reason = self._decision.reason
        if reason == "disabled":
            return "desactivado"
        if reason == "headset_off":
            return "esperando auricular"
        if reason == "locked":
            return "en pausa (sesión bloqueada)"
        if reason == "idle":
            return "en pausa (inactividad)"
        state = getattr(self._keepalive, "state", "idle")
        if state == "running":
            return "activo"
        if state == "retrying":
            return "reintentando…"
        return "iniciando…"

    # -- UI -------------------------------------------------------------------------
    def view(self) -> TrayView:
        if self._dongle is False:
            link = "no_dongle"
        elif self._dongle is None or self._headset is None:
            link = "searching"
        elif self._headset:
            link = "on"
        else:
            link = "off"
        status = self._status
        estimate = full_in = None
        if link == "on" and status is not None and status.charging:
            now = self._wall()
            estimate = self._charge.estimate(now)
            if estimate is not None:
                full_in = format_time_to_full(self._charge.hours_to_full(now))
        return TrayView(
            link=link,
            percent=status.percent if status else None,
            charging=bool(status and status.charging),
            estimate=estimate,
            full_in=full_in,
            muted=self._muted,
            remaining=self._remaining_text,
            keepalive=self._keepalive_text(),
            simulated=self._simulated,
        )

    def _refresh(self) -> None:
        if self.ui is not None:
            self.ui.update(self.view())

    def refresh(self) -> None:
        self._refresh()

    def _refresh_menu(self) -> None:
        refresh = getattr(self.ui, "refresh_menu", None)
        if refresh is not None:
            refresh()

    def _notify(self, message: str) -> None:
        if self.ui is not None:
            self.ui.notify(message)

    # -- menu actions (main thread) --------------------------------------------------
    def toggle_keepalive(self) -> None:
        self.cfg.keepalive_enabled = not self.cfg.keepalive_enabled
        self.cfg.save(self._cfg_path)
        log.info("Anti-auto-off switched %s", "on" if self.cfg.keepalive_enabled else "off")
        self._apply_keepalive()
        self._refresh()
        self._refresh_menu()

    def toggle_autostart(self) -> None:
        target = not autostart.is_enabled()
        if not autostart.set_enabled(target):
            self._notify("No se pudo cambiar el inicio con Windows. Mira app.log.")
        self._refresh()
        self._refresh_menu()

    def open_data_folder(self) -> None:
        try:
            os.startfile(str(self._data_dir))  # type: ignore[attr-defined]
        except OSError:
            log.exception("Cannot open %s", self._data_dir)

    # -- shutdown --------------------------------------------------------------------
    def shutdown(self) -> None:
        self._flush_history()
