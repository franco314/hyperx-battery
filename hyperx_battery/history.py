"""Reading history: per-minute aggregation, SQLite storage, remaining-time
estimation and chart helpers. Everything except :class:`HistoryStore` is pure.
"""

from __future__ import annotations

import sqlite3
import statistics
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, Union

from . import battery


@dataclass(frozen=True)
class Sample:
    """One history row: the median of a time bucket (a minute by default)."""

    ts: float                 # bucket start, epoch seconds
    mv: int                   # median voltage of the bucket
    percent: Optional[int]    # displayed % (None while charging)
    charging: bool


# --------------------------------------------------------------------------
# Aggregation: one row per minute
# --------------------------------------------------------------------------
class MinuteAggregator:
    """Collects readings and emits one :class:`Sample` per time bucket:
    median mV, last displayed %, charging if most readings were charging."""

    def __init__(self, interval_s: float = 60.0) -> None:
        self.interval_s = interval_s
        self._bucket: Optional[int] = None
        self._mvs: List[int] = []
        self._percent: Optional[int] = None
        self._charging_votes = 0

    def add(
        self, ts: float, mv: int, percent: Optional[int], charging: bool
    ) -> Optional[Sample]:
        """Add a reading; returns the finished sample when ``ts`` falls in a
        new bucket, else ``None``."""
        bucket = int(ts // self.interval_s)
        done = None
        if self._bucket is not None and bucket != self._bucket:
            done = self.flush()
        self._bucket = bucket
        self._mvs.append(int(mv))
        self._percent = percent
        self._charging_votes += 1 if charging else 0
        return done

    def flush(self) -> Optional[Sample]:
        """Close the current bucket (also call this when the headset goes off
        or the app exits)."""
        if self._bucket is None or not self._mvs:
            self._reset()
            return None
        charging = self._charging_votes * 2 > len(self._mvs)
        sample = Sample(
            ts=self._bucket * self.interval_s,
            mv=int(round(statistics.median(self._mvs))),
            percent=None if charging else self._percent,
            charging=charging,
        )
        self._reset()
        return sample

    def _reset(self) -> None:
        self._bucket = None
        self._mvs = []
        self._percent = None
        self._charging_votes = 0


# --------------------------------------------------------------------------
# Remaining time estimate
# --------------------------------------------------------------------------
def _segments(samples: Sequence[Sample], gap_s: float) -> List[List[Sample]]:
    """Split into runs without holes (a hole = headset off / app not running)."""
    runs: List[List[Sample]] = []
    for s in samples:
        if runs and s.ts - runs[-1][-1].ts <= gap_s:
            runs[-1].append(s)
        else:
            runs.append([s])
    return runs


def estimate_remaining_hours(
    samples: Sequence[Sample],
    current_percent: Optional[float] = None,
    *,
    min_span_s: float = 30 * 60,
    gap_s: float = 180.0,
    window_s: float = 4 * 3600.0,
    min_drain_per_hour: float = 0.1,
) -> Optional[float]:
    """Hours of battery left, from the discharge slope of the current session.

    ``samples`` are history rows in time order. Only the rows after the last
    charging row are used. The slope is the pooled least-squares slope of the
    voltage-derived percentage over the most recent ``window_s`` of on-time;
    each hole-free run gets its own intercept, so the time the headset was off
    (or the app closed) does not distort the rate. Returns ``None`` ("still
    calculating") with less than ``min_span_s`` of data or when the battery is
    not draining measurably.
    """
    ordered = sorted(samples, key=lambda s: s.ts)
    start = 0
    for i, s in enumerate(ordered):
        if s.charging:
            start = i + 1
    session = ordered[start:]
    if len(session) < 2:
        return None

    runs = _segments(session, gap_s)

    # Keep only the most recent ``window_s`` of on-time, newest run first.
    kept: List[List[Sample]] = []
    budget = window_s
    for run in reversed(runs):
        span = run[-1].ts - run[0].ts
        if span <= budget:
            kept.append(run)
            budget -= span
        else:
            cutoff = run[-1].ts - budget
            trimmed = [s for s in run if s.ts >= cutoff]
            if len(trimmed) >= 2:
                kept.append(trimmed)
            budget = 0
        if budget <= 0:
            break

    total_span = sum(r[-1].ts - r[0].ts for r in kept)
    if total_span < min_span_s:
        return None

    sxy = sxx = 0.0
    for run in kept:
        xs = [(s.ts - run[0].ts) / 3600.0 for s in run]                 # hours
        ys = [battery.mv_to_percent(s.mv) for s in run]                  # percent
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        sxx += sum((x - mx) ** 2 for x in xs)
        sxy += sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0:
        return None
    slope = sxy / sxx                                                    # percent / hour
    if slope > -min_drain_per_hour:
        return None

    if current_percent is None:
        current_percent = battery.mv_to_percent(session[-1].mv)
    return max(0.0, current_percent) / -slope


def format_remaining(hours: Optional[float]) -> str:
    """Spanish text for the tooltip / menu."""
    if hours is None:
        return "calculando…"
    if hours > 99:
        return "> 99 h restantes"
    if hours < 1:
        return f"~{max(1, int(round(hours * 60)))} min restantes"
    if hours < 10:
        return "~" + f"{hours:.1f}".replace(".", ",") + " h restantes"
    return f"~{int(round(hours))} h restantes"


# --------------------------------------------------------------------------
# Chart helpers (used by the tkinter window, testable without it)
# --------------------------------------------------------------------------
def split_segments(
    samples: Iterable[Sample], gap_s: float = 180.0, include_charging: bool = False
) -> List[List[Sample]]:
    """Runs of consecutive samples with a percentage and no hole bigger than
    ``gap_s``; the line chart draws one polyline per run."""
    runs: List[List[Sample]] = []
    broken = True                       # a skipped row also cuts the line
    for s in samples:
        if s.percent is None or (s.charging and not include_charging):
            broken = True
            continue
        if runs and not broken and s.ts - runs[-1][-1].ts <= gap_s:
            runs[-1].append(s)
        else:
            runs.append([s])
        broken = False
    return runs


def charging_spans(samples: Sequence[Sample], gap_s: float = 180.0) -> List[Tuple[float, float]]:
    """(start, end) time spans where the headset was charging."""
    spans: List[Tuple[float, float]] = []
    for s in samples:
        if not s.charging:
            continue
        if spans and s.ts - spans[-1][1] <= gap_s:
            spans[-1] = (spans[-1][0], s.ts)
        else:
            spans.append((s.ts, s.ts))
    return spans


_TICK_STEPS_H = (1, 2, 3, 6, 12, 24, 48)


def time_ticks(t0: float, t1: float, target: int = 6) -> List[float]:
    """Nicely aligned (local time) tick positions between t0 and t1."""
    if t1 <= t0:
        return []
    span_h = (t1 - t0) / 3600.0
    step_h = next((h for h in _TICK_STEPS_H if span_h / h <= target), _TICK_STEPS_H[-1])
    first = datetime.fromtimestamp(t0).replace(minute=0, second=0, microsecond=0)
    if step_h >= 24:
        first = first.replace(hour=0)
    else:
        first = first.replace(hour=(first.hour // step_h) * step_h)
    ticks: List[float] = []
    cur = first
    while True:
        ts = cur.timestamp()
        if ts > t1:
            break
        if ts >= t0:
            ticks.append(ts)
        cur += timedelta(hours=step_h)
    return ticks


# --------------------------------------------------------------------------
# SQLite store
# --------------------------------------------------------------------------
class HistoryStore:
    """Thread-safe SQLite wrapper. ``path`` may be ``":memory:"``."""

    def __init__(self, path: Union[str, Path]) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS readings ("
            " ts INTEGER PRIMARY KEY,"
            " mv INTEGER NOT NULL,"
            " percent INTEGER,"
            " charging INTEGER NOT NULL DEFAULT 0)"
        )
        self._db.commit()

    def add(self, sample: Sample) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO readings (ts, mv, percent, charging) VALUES (?, ?, ?, ?)",
                (int(sample.ts), int(sample.mv), sample.percent, 1 if sample.charging else 0),
            )
            self._db.commit()

    def range(self, t0: float, t1: float) -> List[Sample]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, mv, percent, charging FROM readings"
                " WHERE ts >= ? AND ts <= ? ORDER BY ts",
                (int(t0), int(t1)),
            ).fetchall()
        return [Sample(float(ts), mv, pct, bool(ch)) for ts, mv, pct, ch in rows]

    def last(self) -> Optional[Sample]:
        with self._lock:
            row = self._db.execute(
                "SELECT ts, mv, percent, charging FROM readings ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        return Sample(float(row[0]), row[1], row[2], bool(row[3])) if row else None

    def session_samples(self, now: float, max_age_s: float = 72 * 3600.0) -> List[Sample]:
        """Rows of the current discharge session: everything after the last
        charging row (within ``max_age_s``)."""
        t0 = now - max_age_s
        with self._lock:
            row = self._db.execute(
                "SELECT MAX(ts) FROM readings WHERE charging = 1 AND ts >= ?", (int(t0),)
            ).fetchone()
        since = row[0] if row and row[0] is not None else None
        samples = self.range(t0, now)
        if since is not None:
            samples = [s for s in samples if s.ts > since]
        return samples

    def prune(self, older_than: float) -> int:
        with self._lock:
            cur = self._db.execute("DELETE FROM readings WHERE ts < ?", (int(older_than),))
            self._db.commit()
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except sqlite3.Error:
                pass
