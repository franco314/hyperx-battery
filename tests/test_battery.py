import random

import pytest

from hyperx_battery import battery
from hyperx_battery.battery import (
    BatteryTracker,
    LowBatteryNotifier,
    Presence,
    PresenceTracker,
    is_charging,
    is_charging_raw,
    mv_to_percent,
    percent_to_mv,
    round_percent,
)


# --------------------------------------------------------------------------
# Curve
# --------------------------------------------------------------------------
def test_curve_is_consistent():
    assert len(battery.CURVE_MV) == len(battery.CURVE_PERCENT) == 21
    assert list(battery.CURVE_MV) == sorted(battery.CURVE_MV)
    assert battery.CURVE_PERCENT[0] == 0 and battery.CURVE_PERCENT[-1] == 100


@pytest.mark.parametrize("mv,expected", [(3300, 0), (3584, 10), (3774, 45), (3960, 95), (4100, 100)])
def test_curve_knots_are_exact(mv, expected):
    assert mv_to_percent(mv) == pytest.approx(expected)


def test_interpolation_between_knots():
    # halfway between (3764, 40) and (3774, 45)
    assert mv_to_percent(3769) == pytest.approx(42.5)
    # halfway between (3960, 95) and (4100, 100)
    assert mv_to_percent(4030) == pytest.approx(97.5)


def test_clamped_to_0_100():
    assert mv_to_percent(0) == 0
    assert mv_to_percent(3000) == 0
    assert mv_to_percent(4100.5) == 100
    assert mv_to_percent(9000) == 100


def test_curve_is_monotonic():
    values = [mv_to_percent(mv) for mv in range(3200, 4200)]
    assert values == sorted(values)


def test_percent_to_mv_round_trip():
    for pct in (0, 5, 12, 33.3, 50, 87, 99, 100):
        assert mv_to_percent(percent_to_mv(pct)) == pytest.approx(pct, abs=1e-6)


def test_custom_curve_can_replace_the_default(monkeypatch):
    monkeypatch.setattr(battery, "CURVE_MV", (3000, 4000))
    monkeypatch.setattr(battery, "CURVE_PERCENT", (0, 100))
    assert mv_to_percent(3500) == pytest.approx(50)


def test_round_percent_rounds_half_up():
    assert [round_percent(x) for x in (0.4, 0.5, 1.5, 2.5, 99.49, 99.5)] == [0, 1, 2, 3, 99, 100]


# --------------------------------------------------------------------------
# Charging detection
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "hi,lo,expected",
    [(0x0F, 0xFF, False), (0x10, 0x00, False), (0x10, 0x02, False), (0x10, 19, False),
     (0x10, 20, True), (0x10, 0xFF, True), (0x11, 0x00, True), (0x12, 0x34, True)],
)
def test_is_charging_raw(hi, lo, expected):
    assert is_charging_raw(hi, lo) is expected


def test_is_charging_matches_raw_for_all_voltages():
    for mv in range(3000, 5000):
        assert is_charging(mv) == is_charging_raw(mv >> 8, mv & 0xFF)


def test_observed_full_battery_is_not_charging():
    assert not is_charging(4098)       # 10 02, seen on the real headset


# --------------------------------------------------------------------------
# BatteryTracker: smoothing, hysteresis, noise
# --------------------------------------------------------------------------
def feed(tracker, readings, start=0.0, step=5.0):
    out = []
    for i, mv in enumerate(readings):
        out.append(tracker.update(start + i * step, mv))
    return out


def test_first_reading_is_shown_immediately():
    st = BatteryTracker().update(0.0, 3774)
    assert st.percent == 45 and not st.charging and st.median_mv == 3774


def test_median_ignores_a_voltage_sag():
    """Turning the volume up drops the voltage for a moment: must not show."""
    tracker = BatteryTracker()
    readings = [3860] * 20 + [3790] * 3 + [3860] * 10      # 15 s sag
    shown = [s.percent for s in feed(tracker, readings)]
    assert set(shown) == {75}


def test_window_forgets_old_readings():
    tracker = BatteryTracker(window_s=120.0)
    tracker.update(0.0, 3900)
    st = tracker.update(500.0, 3800)             # first one is long out of the window
    assert st.median_mv == 3800


def test_only_goes_down_on_a_real_discharge():
    tracker = BatteryTracker()
    mvs = [4000 - i for i in range(0, 200)]      # slow, steady discharge
    shown = [s.percent for s in feed(tracker, mvs)]
    assert shown == sorted(shown, reverse=True)
    assert shown[0] > shown[-1]


def test_never_rises_by_itself_below_the_margin():
    tracker = BatteryTracker()
    feed(tracker, [3774] * 30)
    # +2 points for a long time: below the 3-point margin (3778 mV = 47 %)
    later = feed(tracker, [3778] * 100, start=150.0)
    assert {s.percent for s in later} == {45}


def test_rises_only_after_3_points_held_for_2_minutes():
    tracker = BatteryTracker()
    feed(tracker, [3774] * 30)                                   # 45 %
    up = feed(tracker, [3824] * 100, start=150.0)                # 65 %: +20 points
    pcts = [s.percent for s in up]
    assert pcts[0] == 45
    assert pcts[-1] == 65
    # it has to wait the hold time (plus the median window catching up)
    first_up = next(i for i, p in enumerate(pcts) if p > 45)
    assert first_up * 5.0 >= tracker.rise_hold_s


def test_rise_timer_resets_when_the_value_falls_back():
    tracker = BatteryTracker(window_s=1.0)           # effectively unsmoothed
    feed(tracker, [3774] * 5)
    t = 100.0
    tracker.update(t, 3830)                           # high ...
    tracker.update(t + 60, 3830)
    tracker.update(t + 70, 3774)                      # ... falls back: timer cleared
    tracker.update(t + 80, 3830)
    st = tracker.update(t + 190, 3830)                # only 110 s since it rose again
    assert st.percent == 45
    st = tracker.update(t + 201, 3830)
    assert st.percent > 45


def test_reset_allows_a_higher_value():
    tracker = BatteryTracker()
    feed(tracker, [3774] * 10)
    tracker.reset()                                   # e.g. headset was off
    assert tracker.update(100.0, 3900).percent == 83


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("center", [3704, 3725, 3745, 3764, 3774, 3790, 3810, 3830, 3880, 3950, 4040, 4090])
def test_noise_of_2mv_does_not_make_the_number_jump(center, seed):
    """At rest, +-2 mV of noise (the steepest part of the curve is 2 mV per 1 %)
    must neither raise the number nor move it more than 1 point in 10 minutes."""
    rnd = random.Random(seed)
    tracker = BatteryTracker()
    shown = [
        tracker.update(i * 5.0, center + rnd.choice((-2, -1, 0, 1, 2))).percent
        for i in range(121)                           # 10 min at 5 s
    ]
    assert all(b <= a for a, b in zip(shown, shown[1:])), "number went up"
    assert max(shown) - min(shown) <= 1


def test_alternating_plus_minus_2mv_is_rock_solid():
    tracker = BatteryTracker()
    shown = [tracker.update(i * 5.0, 3774 + (2 if i % 2 else -2)).percent for i in range(121)]
    assert max(shown) - min(shown) == 0


def test_plain_min_rule_is_available_and_ratchets_down():
    """With the guard disabled it is exactly shown = min(shown, new)."""
    tracker = BatteryTracker(window_s=1.0, fall_margin=0.0, max_fall_step=100)
    shown = [tracker.update(i * 10.0, mv).percent for i, mv in enumerate([3774, 3764, 3774, 3774, 3774])]
    assert shown == [45, 40, 40, 40, 40]


def test_large_real_drop_is_followed_one_point_per_update():
    tracker = BatteryTracker(window_s=1.0)
    first = tracker.update(0.0, 3860).percent                  # 75
    shown = [tracker.update(10.0 + i, 3764).percent for i in range(40)]
    assert first == 75
    assert shown[0] == 74                                      # max_fall_step = 1
    assert shown[1] == 73
    # the dead-band holds the display one point above the settled value (40)
    assert shown[-1] == 41


# -- charging --------------------------------------------------------------------
def test_charging_has_no_percentage():
    tracker = BatteryTracker()
    st = tracker.update(0.0, 4200)
    assert st.charging and st.percent is None


def test_charging_flag_is_debounced():
    tracker = BatteryTracker()
    feed(tracker, [3900] * 5)
    blip = tracker.update(30.0, 4200)                     # a single spurious reading
    assert not blip.charging and blip.percent == 83
    assert not tracker.update(35.0, 3900).charging        # and back to normal
    assert not tracker.update(40.0, 4200).charging        # flip counter restarted
    assert tracker.update(45.0, 4200).charging            # two in a row: confirmed


def test_smoothing_restarts_when_charging_ends():
    tracker = BatteryTracker()
    feed(tracker, [3774] * 10)
    feed(tracker, [4200] * 3, start=50.0)
    assert tracker.charging
    after = feed(tracker, [3930] * 3, start=70.0)         # unplugged, much higher
    assert after[-1].percent == 88                        # no hysteresis cap from before
    assert not after[-1].charging


def test_first_reading_while_charging_is_accepted_at_once():
    assert BatteryTracker().update(0.0, 4300).charging


# --------------------------------------------------------------------------
# PresenceTracker
# --------------------------------------------------------------------------
def test_presence_two_misses_means_off():
    p = PresenceTracker(misses_to_off=2)
    assert p.on_reply() is Presence.ON
    assert p.on_miss() is None
    assert p.on_miss() is Presence.OFF
    assert p.on_miss() is None                 # already off: no repeat


def test_presence_a_reply_clears_the_miss_count():
    p = PresenceTracker()
    p.on_reply()
    p.on_miss()
    assert p.on_reply() is None                # still on, no change
    assert p.on_miss() is None                 # counter restarted


def test_presence_first_reply_after_off_is_on():
    p = PresenceTracker()
    p.on_reply()
    p.on_miss(), p.on_miss()
    assert p.state is Presence.OFF
    assert p.on_reply() is Presence.ON


def test_presence_from_unknown_two_misses_is_off():
    p = PresenceTracker()
    assert p.state is Presence.UNKNOWN
    p.on_miss()
    assert p.on_miss() is Presence.OFF


def test_presence_force_off():
    p = PresenceTracker()
    p.on_reply()
    assert p.force_off() is Presence.OFF
    assert p.force_off() is None


# --------------------------------------------------------------------------
# LowBatteryNotifier
# --------------------------------------------------------------------------
def test_notifies_once_per_threshold():
    n = LowBatteryNotifier((20, 10))
    assert n.update(25, False) is None
    assert n.update(20, False) == 20
    assert n.update(19, False) is None            # already told
    assert n.update(10, False) == 10
    assert n.update(9, False) is None


def test_crossing_both_thresholds_at_once_reports_the_lowest_only():
    n = LowBatteryNotifier((20, 10))
    assert n.update(8, False) == 10
    assert n.update(7, False) is None             # 20 was marked as told too


def test_charging_rearms():
    n = LowBatteryNotifier((20, 10))
    n.update(15, False)
    assert n.update(15, False) is None
    assert n.update(None, True) is None           # charging
    assert n.update(18, False) == 20


def test_rearms_when_the_display_recovers_well_above_the_threshold():
    n = LowBatteryNotifier((20, 10), rearm_margin=5)
    n.update(19, False)
    n.update(30, False)                           # charge happened unseen
    assert n.update(20, False) == 20


def test_no_percent_no_notification():
    assert LowBatteryNotifier().update(None, False) is None


def test_thresholds_are_configurable_and_unordered():
    n = LowBatteryNotifier([5, 30])
    assert n.update(29, False) == 30
    assert n.update(5, False) == 5
