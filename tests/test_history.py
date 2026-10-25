from datetime import datetime

import pytest

from hyperx_battery import battery
from hyperx_battery.history import (
    HistoryStore,
    MinuteAggregator,
    Sample,
    charging_spans,
    estimate_remaining_hours,
    format_remaining,
    split_segments,
    time_ticks,
)


# --------------------------------------------------------------------------
# MinuteAggregator
# --------------------------------------------------------------------------
def test_aggregator_emits_one_median_row_per_minute():
    agg = MinuteAggregator(60)
    out = []
    for i, mv in enumerate([4000, 4002, 4001, 3990, 4001, 4000]):
        out.append(agg.add(600 + i * 5, mv, 90, False))      # 10:00 bucket
    assert out == [None] * 6
    row = agg.add(660, 3999, 89, False)                       # next minute
    assert row == Sample(ts=600.0, mv=4000, percent=90, charging=False)


def test_aggregator_flush_and_empty_flush():
    agg = MinuteAggregator(60)
    assert agg.flush() is None
    agg.add(125, 3900, 83, False)
    assert agg.flush() == Sample(120.0, 3900, 83, False)
    assert agg.flush() is None


def test_aggregator_charging_majority_has_no_percent():
    agg = MinuteAggregator(60)
    agg.add(0, 4200, None, True)
    agg.add(5, 4210, None, True)
    agg.add(10, 4100, 99, False)
    row = agg.flush()
    assert row.charging and row.percent is None


def test_aggregator_uses_the_last_displayed_percent():
    agg = MinuteAggregator(60)
    agg.add(0, 3900, 84, False)
    agg.add(5, 3898, 83, False)
    assert agg.flush().percent == 83


# --------------------------------------------------------------------------
# Remaining-time estimate
# --------------------------------------------------------------------------
def linear_samples(minutes, start_pct=80.0, pct_per_hour=4.0, start_ts=0.0, charging=False):
    """One row per minute, voltage following the curve for a linear discharge."""
    rows = []
    for m in range(minutes + 1):
        pct = start_pct - pct_per_hour * m / 60.0
        rows.append(Sample(start_ts + m * 60.0, round(battery.percent_to_mv(pct)), round(pct), charging))
    return rows


def test_estimate_none_with_less_than_30_minutes():
    assert estimate_remaining_hours(linear_samples(29)) is None
    assert estimate_remaining_hours([]) is None
    assert estimate_remaining_hours(linear_samples(0)) is None


def test_estimate_linear_discharge():
    rows = linear_samples(120, start_pct=80, pct_per_hour=4.0)       # now at 72 %
    hours = estimate_remaining_hours(rows, current_percent=72)
    assert hours == pytest.approx(72 / 4.0, rel=0.05)                # ~18 h


def test_estimate_available_just_after_30_minutes():
    hours = estimate_remaining_hours(linear_samples(31, pct_per_hour=6.0))
    assert hours is not None and hours > 0


def test_estimate_none_when_not_draining():
    flat = [Sample(m * 60.0, 3900, 83, False) for m in range(90)]
    assert estimate_remaining_hours(flat) is None


def test_estimate_none_when_voltage_rises():
    rising = [Sample(m * 60.0, 3800 + m, 60, False) for m in range(90)]
    assert estimate_remaining_hours(rising) is None


def test_estimate_only_uses_data_after_the_last_charge():
    old = linear_samples(120, start_pct=95, pct_per_hour=30.0)        # steep, before the charge
    charge = [Sample(old[-1].ts + 60.0 * (i + 1), 4200, None, True) for i in range(10)]
    t0 = charge[-1].ts + 60.0
    fresh = linear_samples(60, start_pct=100, pct_per_hour=5.0, start_ts=t0)
    hours = estimate_remaining_hours(old + charge + fresh, current_percent=95)
    assert hours == pytest.approx(95 / 5.0, rel=0.1)


def test_estimate_ignores_off_periods():
    """Headset off for 8 h in the middle must not distort the drain rate."""
    a = linear_samples(60, start_pct=80, pct_per_hour=4.0)                           # 80 -> 76
    t_b = a[-1].ts + 8 * 3600
    b = linear_samples(60, start_pct=76, pct_per_hour=4.0, start_ts=t_b)             # 76 -> 72
    hours = estimate_remaining_hours(a + b, current_percent=72)
    assert hours == pytest.approx(72 / 4.0, rel=0.05)


def test_estimate_needs_30_minutes_of_on_time_not_wall_time():
    a = linear_samples(10)
    b = linear_samples(10, start_ts=a[-1].ts + 5 * 3600)
    assert estimate_remaining_hours(a + b) is None


def test_estimate_uses_only_the_recent_window():
    early = linear_samples(300, start_pct=100, pct_per_hour=1.0)           # slow start
    t0 = early[-1].ts + 60
    late = linear_samples(240, start_pct=95, pct_per_hour=10.0, start_ts=t0)
    hours = estimate_remaining_hours(early + late, current_percent=55, window_s=3 * 3600)
    assert hours == pytest.approx(55 / 10.0, rel=0.1)


@pytest.mark.parametrize(
    "hours,text",
    [(None, "calculando…"), (0.5, "~30 min restantes"), (0.001, "~1 min restantes"),
     (4.54, "~4,5 h restantes"), (12.4, "~12 h restantes"), (12.6, "~13 h restantes"),
     (150, "> 99 h restantes")],
)
def test_format_remaining(hours, text):
    assert format_remaining(hours) == text


# --------------------------------------------------------------------------
# Chart helpers
# --------------------------------------------------------------------------
def test_split_segments_breaks_on_gaps_and_skips_charging():
    rows = [Sample(0, 3900, 80, False), Sample(60, 3900, 80, False),
            Sample(120, 3900, 79, False),
            Sample(1000, 3900, 79, False),                        # gap
            Sample(1060, 4200, None, True),                       # charging: not in the line
            Sample(1120, 4200, None, True),
            Sample(1180, 3950, 91, False)]
    segs = split_segments(rows, gap_s=180)
    assert [len(s) for s in segs] == [3, 1, 1]


def test_charging_spans():
    rows = [Sample(0, 3900, 80, False), Sample(60, 4200, None, True), Sample(120, 4200, None, True),
            Sample(180, 3950, 90, False), Sample(2000, 4200, None, True)]
    assert charging_spans(rows, gap_s=180) == [(60, 120), (2000, 2000)]


def test_time_ticks_are_aligned_and_within_range():
    t1 = datetime(2026, 10, 25, 15, 30).timestamp()
    t0 = t1 - 24 * 3600
    ticks = time_ticks(t0, t1)
    assert 3 <= len(ticks) <= 8
    assert all(t0 <= t <= t1 for t in ticks)
    assert all(datetime.fromtimestamp(t).minute == 0 for t in ticks)
    assert ticks == sorted(ticks)


def test_time_ticks_week_are_midnights():
    t1 = datetime(2026, 10, 25, 15, 30).timestamp()
    ticks = time_ticks(t1 - 7 * 86400, t1)
    assert all(datetime.fromtimestamp(t).hour == 0 for t in ticks)
    assert time_ticks(5, 5) == []


# --------------------------------------------------------------------------
# HistoryStore
# --------------------------------------------------------------------------
def test_store_round_trip_and_range():
    store = HistoryStore(":memory:")
    rows = linear_samples(5)
    for r in rows:
        store.add(r)
    got = store.range(60, 180)
    assert [g.ts for g in got] == [60.0, 120.0, 180.0]
    assert got[0].mv == rows[1].mv and got[0].percent == rows[1].percent and got[0].charging is False
    assert store.last().ts == rows[-1].ts
    store.close()


def test_store_replaces_same_timestamp_and_keeps_none_percent():
    store = HistoryStore(":memory:")
    store.add(Sample(60, 3900, 83, False))
    store.add(Sample(60, 4200, None, True))
    (row,) = store.range(0, 100)
    assert row.mv == 4200 and row.percent is None and row.charging is True


def test_store_session_samples_start_after_last_charge():
    store = HistoryStore(":memory:")
    now = 10 * 3600.0
    store.add(Sample(now - 7200, 3800, 60, False))
    store.add(Sample(now - 3600, 4200, None, True))
    store.add(Sample(now - 3540, 4150, None, True))
    store.add(Sample(now - 3000, 4090, 99, False))
    store.add(Sample(now - 60, 4080, 99, False))
    session = store.session_samples(now)
    assert [s.ts for s in session] == [now - 3000, now - 60]


def test_store_session_ignores_rows_older_than_max_age():
    store = HistoryStore(":memory:")
    store.add(Sample(0, 3900, 80, False))
    store.add(Sample(100 * 3600, 3890, 79, False))
    assert [s.ts for s in store.session_samples(100 * 3600 + 10)] == [100 * 3600.0]


def test_store_prune_and_file_persistence(tmp_path):
    path = tmp_path / "h.sqlite"
    store = HistoryStore(path)
    store.add(Sample(100, 3900, 80, False))
    store.add(Sample(200, 3890, 79, False))
    assert store.prune(150) == 1
    store.close()
    again = HistoryStore(path)
    assert [s.ts for s in again.range(0, 1000)] == [200.0]
    again.close()


def test_store_is_usable_from_other_threads():
    import threading

    store = HistoryStore(":memory:")
    errors = []

    def work(n):
        try:
            for i in range(50):
                store.add(Sample(n * 1000 + i, 3900, 80, False))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert len(store.range(0, 10_000)) == 200
