"""Time-based charge estimate. No battery % can be derived while charging (the
headset reports a flat ~4.3 V), so the percentage is extrapolated from the
last measured value before the charge and a charging rate (% per hour) that is
learned from past charges. Everything except the two file helpers at the
bottom is pure: times are plain wall-clock epoch seconds from the caller.

Lifecycle (driven by the controller):

* :meth:`ChargeEstimator.start`  - charging begins (or the app starts charging)
* :meth:`ChargeEstimator.finish` - charging ends; learning is now *pending*
* :meth:`ChargeEstimator.settle` - ``SETTLE_S`` after the unplug the surface
  charge has faded: the measured % is compared with the start % to learn the rate
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

DEFAULT_RATE = 25.0           # % per hour (HyperX spec: ~4.5 h for a full charge)
MIN_RATE = 10.0
MAX_RATE = 60.0
EMA_WEIGHT = 0.3              # weight of a newly observed rate
ESTIMATE_CAP = 99             # an estimate never claims 100 %
SETTLE_S = 5 * 60.0           # wait after unplug before measuring (surface charge)
MIN_LEARN_S = 10 * 60.0       # shorter charges are too noisy to learn from
MAX_SESSION_S = 12 * 3600.0   # older start data / sessions are discarded
SATURATED_PERCENT = 99        # measured >= this: battery may have been full, rate only a lower bound
MAX_OBSERVED_RATE = 2 * MAX_RATE

STATE_FILE = "charge_state.json"


def clamp_rate(rate: float) -> float:
    return max(MIN_RATE, min(MAX_RATE, rate))


@dataclass(frozen=True)
class ChargeSession:
    start_ts: float           # epoch seconds when charging started
    start_percent: float      # last shown % before charging


@dataclass(frozen=True)
class _Pending:
    session: ChargeSession
    end_ts: float


def observed_rate(start_percent: float, measured: float, hours: float) -> Optional[float]:
    """Rate (% per hour) seen in one charge, or ``None`` if it makes no sense."""
    if hours <= 0 or not math.isfinite(hours):
        return None
    rate = (measured - start_percent) / hours
    if not math.isfinite(rate) or rate <= 0 or rate > MAX_OBSERVED_RATE:
        return None
    return rate


class ChargeEstimator:
    def __init__(
        self,
        rate: float = DEFAULT_RATE,
        session: Optional[ChargeSession] = None,
        ema_weight: float = EMA_WEIGHT,
    ) -> None:
        self.rate = clamp_rate(rate)
        self.session = session
        self.ema_weight = ema_weight
        self._pending: Optional[_Pending] = None

    @property
    def has_state(self) -> bool:
        """A session in progress, or one that just ended and awaits learning."""
        return self.session is not None or self._pending is not None

    # -- estimate ---------------------------------------------------------------
    def raw_estimate(self, now: float) -> Optional[float]:
        """Uncapped extrapolation, or ``None`` without a (recent) start."""
        s = self.session
        if s is None:
            return None
        elapsed = max(0.0, now - s.start_ts)
        if elapsed > MAX_SESSION_S:
            return None
        return s.start_percent + self.rate * elapsed / 3600.0

    def estimate(self, now: float) -> Optional[int]:
        raw = self.raw_estimate(now)
        if raw is None:
            return None
        return int(math.floor(min(float(ESTIMATE_CAP), raw) + 0.5))

    def hours_to_full(self, now: float) -> Optional[float]:
        raw = self.raw_estimate(now)
        if raw is None:
            return None
        return max(0.0, 100.0 - raw) / self.rate

    # -- lifecycle ----------------------------------------------------------------
    def start(
        self, now: float, start_percent: Optional[float], start_ts: Optional[float] = None
    ) -> bool:
        """Charging began. Returns whether an estimate is available.

        A re-plug shortly after an unplug resumes the previous session, and a
        session already in progress (restored after an app restart) is kept.
        """
        if self._pending is not None:
            self.session, self._pending = self._pending.session, None
        elif self.session is None and start_percent is not None:
            ts = now if start_ts is None else min(start_ts, now)
            self.session = ChargeSession(ts, float(start_percent))
        return self.session is not None

    def finish(self, now: float) -> None:
        """Charging ended; learning waits for the surface charge to settle."""
        if self.session is not None:
            self._pending = _Pending(self.session, now)
            self.session = None

    def cancel(self) -> None:
        """Forget the session and any pending learning (headset off, unknown end)."""
        self.session = None
        self._pending = None

    def settle(self, now: float, measured: Optional[int]) -> Optional[float]:
        """Call on every discharging reading. Once ``SETTLE_S`` passed since the
        unplug, learn from ``measured``; returns the new rate when it changed."""
        p = self._pending
        if p is None or measured is None or now - p.end_ts < SETTLE_S:
            return None
        self._pending = None
        hours = (p.end_ts - p.session.start_ts) / 3600.0
        if hours * 3600.0 < MIN_LEARN_S or hours * 3600.0 > MAX_SESSION_S:
            return None
        obs = observed_rate(p.session.start_percent, measured, hours)
        if obs is None:
            return None
        if measured >= SATURATED_PERCENT and obs < self.rate:
            return None                     # probably full: slower only because it stopped
        self.rate = clamp_rate((1 - self.ema_weight) * self.rate + self.ema_weight * obs)
        return self.rate

    # -- persistence --------------------------------------------------------------
    def to_state(self) -> Dict[str, Any]:
        s = self.session
        return {
            "rate": round(self.rate, 3),
            "session": None if s is None else {"start_ts": s.start_ts, "start_percent": s.start_percent},
        }

    @classmethod
    def from_state(cls, state: Any, now: float) -> "ChargeEstimator":
        """Tolerant of anything: wrong types or values fall back to defaults."""
        rate, session = DEFAULT_RATE, None
        if isinstance(state, dict):
            r = state.get("rate")
            if _is_number(r):
                rate = float(r)
            raw = state.get("session")
            if isinstance(raw, dict):
                ts, pct = raw.get("start_ts"), raw.get("start_percent")
                if (_is_number(ts) and _is_number(pct) and 0 <= pct <= 100
                        and -60 <= now - ts <= MAX_SESSION_S):
                    session = ChargeSession(float(ts), float(pct))
        return cls(rate, session)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# --------------------------------------------------------------------------
# Start percent when the app did not see the charge begin
# --------------------------------------------------------------------------
def start_from_history(
    samples: Sequence[Any], now: float, max_age_s: float = MAX_SESSION_S
) -> Optional[Tuple[float, float]]:
    """``(percent, start_ts)`` from history rows (time ordered): the last row
    with a percentage if it is recent enough; charging is taken to start at the
    first charging row after it (else ``now``)."""
    idx = None
    for i in range(len(samples) - 1, -1, -1):
        if samples[i].percent is not None:
            idx = i
            break
    if idx is None or now - samples[idx].ts > max_age_s:
        return None
    start_ts = now
    for s in samples[idx + 1:]:
        if s.charging:
            start_ts = min(s.ts, now)
            break
    return float(samples[idx].percent), start_ts


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------
def format_time_to_full(hours: Optional[float]) -> Optional[str]:
    """Spanish text for the tooltip / menu, e.g. ``lleno en ~25 min``."""
    if hours is None:
        return None
    minutes = hours * 60.0
    if minutes < 2:
        return "casi lleno"
    if minutes < 10:
        return f"lleno en ~{int(round(minutes))} min"
    if minutes < 60:
        return f"lleno en ~{int(round(minutes / 5.0)) * 5} min"
    if hours < 10:
        return "lleno en ~" + f"{hours:.1f}".replace(".", ",") + " h"
    return f"lleno en ~{int(round(hours))} h"


# --------------------------------------------------------------------------
# File I/O (a tiny JSON file; any failure degrades to defaults)
# --------------------------------------------------------------------------
def load_estimator(path: Optional[Path], now: float) -> ChargeEstimator:
    if path is None:
        return ChargeEstimator()
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ChargeEstimator()
    except (OSError, ValueError):
        log.warning("Cannot read %s; using the default charge rate", path, exc_info=True)
        return ChargeEstimator()
    return ChargeEstimator.from_state(state, now)


def save_estimator(path: Optional[Path], estimator: ChargeEstimator) -> None:
    if path is None:
        return
    tmp = Path(str(path) + ".tmp")
    try:
        tmp.write_text(json.dumps(estimator.to_state()), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        log.warning("Cannot save %s", path, exc_info=True)
