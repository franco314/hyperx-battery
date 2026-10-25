"""Pure battery logic: voltage -> percent, smoothing, hysteresis, charging,
headset presence and low-battery notification rules. No I/O, no threads.

All time arguments are plain ``float`` seconds supplied by the caller (use a
monotonic clock), which keeps everything deterministic and unit-testable.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# mV -> % curve. Replaceable constants: a future auto-calibration only needs
# to reassign these two tuples (functions read them at call time).
# Base: HyperHeadset table, extended at the top because the real headset
# measures 4030-4100 mV when almost full.
# --------------------------------------------------------------------------
CURVE_MV: Tuple[int, ...] = (
    3300, 3328, 3584, 3674, 3704, 3732, 3744, 3754, 3764, 3774, 3784,
    3794, 3804, 3824, 3840, 3860, 3890, 3910, 3940, 3960, 4100,
)
CURVE_PERCENT: Tuple[int, ...] = (
    0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50,
    55, 60, 65, 70, 75, 80, 85, 90, 95, 100,
)

# Charging: HI == 0x10 and LO >= 20, or HI >= 0x11  ->  mV >= 0x1014.
CHARGING_MV_THRESHOLD = 0x1000 + 20


def mv_to_percent(
    mv: float,
    curve_mv: Optional[Sequence[float]] = None,
    curve_percent: Optional[Sequence[float]] = None,
) -> float:
    """Piecewise-linear interpolation of the curve, clamped to 0..100."""
    xs = CURVE_MV if curve_mv is None else curve_mv
    ys = CURVE_PERCENT if curve_percent is None else curve_percent
    if mv <= xs[0]:
        return float(ys[0])
    if mv >= xs[-1]:
        return float(ys[-1])
    i = bisect.bisect_right(xs, mv)
    x0, x1 = xs[i - 1], xs[i]
    y0, y1 = ys[i - 1], ys[i]
    return y0 + (y1 - y0) * (mv - x0) / (x1 - x0)


def percent_to_mv(
    percent: float,
    curve_mv: Optional[Sequence[float]] = None,
    curve_percent: Optional[Sequence[float]] = None,
) -> float:
    """Inverse of :func:`mv_to_percent` (used by ``--simulate-percent``)."""
    xs = CURVE_MV if curve_mv is None else curve_mv
    ys = CURVE_PERCENT if curve_percent is None else curve_percent
    if percent <= ys[0]:
        return float(xs[0])
    if percent >= ys[-1]:
        return float(xs[-1])
    i = bisect.bisect_right(ys, percent)
    x0, x1 = xs[i - 1], xs[i]
    y0, y1 = ys[i - 1], ys[i]
    return x0 + (x1 - x0) * (percent - y0) / (y1 - y0)


def round_percent(value: float) -> int:
    """Round half up (Python's round() is banker's rounding)."""
    return int(math.floor(value + 0.5))


def is_charging_raw(hi: int, lo: int) -> bool:
    """Charging flag straight from the two reply bytes."""
    return (hi == 0x10 and lo >= 20) or hi >= 0x11


def is_charging(mv: float) -> bool:
    """Charging flag from the voltage (equivalent to :func:`is_charging_raw`)."""
    return mv >= CHARGING_MV_THRESHOLD


# --------------------------------------------------------------------------
# Smoothing + hysteresis
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class BatteryStatus:
    """Result of feeding one reading to :class:`BatteryTracker`."""

    mv: int
    charging: bool
    percent: Optional[int]          # displayed value; None while charging / no data
    median_mv: Optional[float] = None


class BatteryTracker:
    """Turns raw voltage readings into a stable integer percentage.

    * Displayed % = curve(median of the readings in the last ``window_s``).
    * Hysteresis: while discharging the displayed value can only go down.
      It may go up again only (a) after :meth:`reset` (charge cycle or the
      headset having been off) or (b) when the new value stays at least
      ``rise_margin`` points above the displayed one for ``rise_hold_s``.
    * Noise guard (refinement of ``shown = min(shown, new)``): the display
      only steps down when the smoothed value is ``fall_margin`` points
      below the half-way point to the next integer, and by at most
      ``max_fall_step`` per update. Where the curve is steepest (~2 mV per 1 %)
      +-2 mV of ADC noise is +-1 %, and a plain ``min`` would ratchet the
      number down on the noise floor. ``fall_margin=0, max_fall_step=100``
      gives the plain rule.
    * Charging is debounced: a change needs ``charge_confirm`` consecutive
      readings (the very first reading is accepted at once). The window is
      cleared when charging starts and when it ends.
    """

    def __init__(
        self,
        window_s: float = 120.0,
        rise_margin: int = 3,
        rise_hold_s: float = 120.0,
        charge_confirm: int = 2,
        fall_margin: float = 1.0,
        max_fall_step: int = 1,
    ) -> None:
        self.window_s = window_s
        self.rise_margin = rise_margin
        self.rise_hold_s = rise_hold_s
        self.fall_margin = fall_margin
        self.max_fall_step = max(1, max_fall_step)
        self.charge_confirm = max(1, charge_confirm)
        self.reset()

    # -- state ------------------------------------------------------------
    def reset(self) -> None:
        """Forget everything (call after the headset was off / charge ended)."""
        self._window: Deque[Tuple[float, int]] = deque()
        self._shown: Optional[int] = None
        self._rise_since: Optional[float] = None
        self._charging = False
        self._established = False
        self._flip_count = 0

    @property
    def shown(self) -> Optional[int]:
        return self._shown

    @property
    def charging(self) -> bool:
        return self._charging

    # -- main entry point ---------------------------------------------------
    def update(self, now: float, mv: int) -> BatteryStatus:
        raw_charging = is_charging(mv)

        if not self._established:
            self._established = True
            self._charging = raw_charging
            self._flip_count = 0
        elif raw_charging != self._charging:
            self._flip_count += 1
            if self._flip_count >= self.charge_confirm:
                # Confirmed transition: start the smoothing from scratch.
                self._charging = raw_charging
                self._flip_count = 0
                self._clear_smoothing()
        else:
            self._flip_count = 0

        if self._charging:
            return BatteryStatus(mv=mv, charging=True, percent=None)

        if raw_charging:
            # Unconfirmed charging blip while discharging: keep the last value
            # and do not let this reading pollute the median.
            return BatteryStatus(mv=mv, charging=False, percent=self._shown)

        self._window.append((now, mv))
        cutoff = now - self.window_s
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

        median_mv = statistics.median(m for _, m in self._window)
        exact = mv_to_percent(median_mv)
        self._apply_hysteresis(now, exact, round_percent(exact))
        return BatteryStatus(
            mv=mv, charging=False, percent=self._shown, median_mv=median_mv
        )

    # -- internals ----------------------------------------------------------
    def _clear_smoothing(self) -> None:
        self._window.clear()
        self._shown = None
        self._rise_since = None

    def _apply_hysteresis(self, now: float, exact: float, new: int) -> None:
        if self._shown is None:
            self._shown = new
            self._rise_since = None
            return
        if new <= self._shown:
            self._rise_since = None
            if new < self._shown and exact <= self._shown - 0.5 - self.fall_margin:
                self._shown = max(new, self._shown - self.max_fall_step)
            return
        if new - self._shown >= self.rise_margin:
            if self._rise_since is None:
                self._rise_since = now
            elif now - self._rise_since >= self.rise_hold_s:
                self._shown = new
                self._rise_since = None
        else:
            self._rise_since = None


# --------------------------------------------------------------------------
# Headset presence (on / off) from missed battery replies
# --------------------------------------------------------------------------
class Presence(Enum):
    UNKNOWN = "unknown"
    ON = "on"
    OFF = "off"


class PresenceTracker:
    """``misses_to_off`` consecutive unanswered requests -> OFF; any reply -> ON.

    Methods return the new :class:`Presence` when it changed, else ``None``.
    """

    def __init__(self, misses_to_off: int = 2) -> None:
        self.misses_to_off = max(1, misses_to_off)
        self.state = Presence.UNKNOWN
        self._misses = 0

    def on_reply(self) -> Optional[Presence]:
        self._misses = 0
        return self._set(Presence.ON)

    def on_miss(self) -> Optional[Presence]:
        self._misses += 1
        if self._misses >= self.misses_to_off:
            return self._set(Presence.OFF)
        return None

    def force_off(self) -> Optional[Presence]:
        """Dongle unplugged or a power-off event was received."""
        self._misses = self.misses_to_off
        return self._set(Presence.OFF)

    def _set(self, new: Presence) -> Optional[Presence]:
        if new is self.state:
            return None
        self.state = new
        return new


# --------------------------------------------------------------------------
# Low-battery notifications: once per discharge cycle per threshold
# --------------------------------------------------------------------------
class LowBatteryNotifier:
    """Decides when to fire a low-battery notification.

    Each threshold fires once. All thresholds re-arm when charging is
    detected, or when the displayed % climbs ``rearm_margin`` points above a
    threshold (covers a charge that happened while the app was not looking).
    If several thresholds are crossed at once only the lowest one is reported.
    """

    def __init__(self, thresholds: Sequence[int] = (20, 10), rearm_margin: int = 5) -> None:
        self.thresholds = tuple(sorted(set(int(t) for t in thresholds), reverse=True))
        self.rearm_margin = rearm_margin
        self._fired: set = set()

    def rearm(self) -> None:
        self._fired.clear()

    def update(self, percent: Optional[int], charging: bool) -> Optional[int]:
        """Return the threshold to announce now, or ``None``."""
        if charging:
            self.rearm()
            return None
        if percent is None:
            return None
        for t in self.thresholds:
            if percent >= t + self.rearm_margin:
                self._fired.discard(t)
        crossed = [t for t in self.thresholds if percent <= t and t not in self._fired]
        if not crossed:
            return None
        self._fired.update(crossed)
        return min(crossed)
