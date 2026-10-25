"""Charge estimate: pure logic, restart restore and the state file."""
import json

import pytest

from hyperx_battery import charge
from hyperx_battery.charge import (
    ChargeEstimator,
    ChargeSession,
    format_time_to_full,
    load_estimator,
    observed_rate,
    save_estimator,
    start_from_history,
)
from hyperx_battery.history import Sample

H = 3600.0
T0 = 1_000_000.0


def started(percent=80, rate=25.0, at=T0):
    est = ChargeEstimator(rate)
    assert est.start(at, percent)
    return est


# -- estimate ----------------------------------------------------------------------
def test_estimate_grows_with_time_at_the_rate():
    est = started(60)
    assert est.estimate(T0) == 60
    assert est.estimate(T0 + 0.4 * H) == 70                    # +10 %
    assert est.estimate(T0 + 1 * H) == 85
    assert est.estimate(T0 + 1.2 * H) > est.estimate(T0 + 1 * H)


def test_estimate_caps_at_99_and_never_claims_100():
    est = started(95)
    assert est.estimate(T0 + 5 * H) == 99
    assert est.raw_estimate(T0 + 5 * H) > 100                   # the raw value is not capped
    assert started(100).estimate(T0) == 99


def test_no_start_percent_means_no_estimate():
    est = ChargeEstimator()
    assert est.start(T0, None) is False
    assert est.estimate(T0 + H) is None and est.hours_to_full(T0 + H) is None


def test_estimate_ignores_clock_going_backwards_and_very_old_sessions():
    est = started(70)
    assert est.estimate(T0 - 100) == 70
    assert est.estimate(T0 + charge.MAX_SESSION_S + 1) is None


def test_time_to_full_uses_the_rate():
    est = started(50, rate=25.0)
    assert est.hours_to_full(T0) == pytest.approx(2.0)
    assert est.hours_to_full(T0 + 1 * H) == pytest.approx(1.0)
    assert est.hours_to_full(T0 + 9 * H) == 0.0


@pytest.mark.parametrize(
    "hours,text",
    [
        (0.0, "casi lleno"), (1 / 60, "casi lleno"), (5 / 60, "lleno en ~5 min"),
        (25 / 60, "lleno en ~25 min"), (37 / 60, "lleno en ~35 min"),
        (1.5, "lleno en ~1,5 h"), (12.0, "lleno en ~12 h"),
    ],
)
def test_format_time_to_full(hours, text):
    assert format_time_to_full(hours) == text
    assert format_time_to_full(None) is None


def test_start_keeps_a_session_in_progress_and_can_backdate():
    est = ChargeEstimator()
    est.start(T0, 40, start_ts=T0 - 600)
    assert est.session == ChargeSession(T0 - 600, 40.0)
    est.start(T0 + 60, 90)                                      # e.g. the headset blinked off and on
    assert est.session.start_percent == 40.0


# -- learning ----------------------------------------------------------------------
def charge_and_unplug(est, start_pct, hours):
    est.start(T0, start_pct)
    est.finish(T0 + hours * H)
    return T0 + hours * H


def test_learning_uses_the_settled_value_after_the_unplug():
    est = ChargeEstimator(25.0)
    end = charge_and_unplug(est, 82, 37 / 60)
    assert est.settle(end + 30, 98) is None                     # surface charge not settled yet
    assert est.settle(end + 60, 98) is None
    rate = est.settle(end + charge.SETTLE_S, 98)                # 16 % in 37 min = 25.9 %/h
    assert rate == pytest.approx(0.7 * 25.0 + 0.3 * (16 / (37 / 60)))
    assert est.rate == rate
    assert est.settle(end + 2 * charge.SETTLE_S, 50) is None     # learned once, nothing pending


def test_learning_blends_and_clamps():
    est = ChargeEstimator(25.0)
    end = charge_and_unplug(est, 20, 1.0)                       # observed 60 %/h
    est.settle(end + charge.SETTLE_S, 80)
    assert est.rate == pytest.approx(0.7 * 25 + 0.3 * 60)
    est = ChargeEstimator(charge.MAX_RATE)
    end = charge_and_unplug(est, 0, 1.0)
    est.settle(end + charge.SETTLE_S, 100 - 1)
    assert est.rate <= charge.MAX_RATE
    est = ChargeEstimator(charge.MIN_RATE)
    end = charge_and_unplug(est, 10, 2.0)                       # observed 2.5 %/h
    est.settle(end + charge.SETTLE_S, 15)
    assert est.rate == charge.MIN_RATE
    assert ChargeEstimator(500).rate == charge.MAX_RATE and ChargeEstimator(-3).rate == charge.MIN_RATE


@pytest.mark.parametrize("hours", [0.05, 9 / 60])
def test_short_sessions_are_ignored(hours):
    est = ChargeEstimator(25.0)
    end = charge_and_unplug(est, 50, hours)
    assert est.settle(end + charge.SETTLE_S, 90) is None
    assert est.rate == 25.0


@pytest.mark.parametrize("measured", [40, 50, 10])             # no gain or a loss
def test_nonsensical_results_are_ignored(measured):
    est = ChargeEstimator(25.0)
    end = charge_and_unplug(est, 50, 1.0)
    assert est.settle(end + charge.SETTLE_S, measured) is None
    assert est.rate == 25.0


def test_absurdly_fast_charge_is_ignored():
    est = ChargeEstimator(25.0)
    end = charge_and_unplug(est, 10, 0.2)                       # +80 % in 12 min
    assert est.settle(end + charge.SETTLE_S, 90) is None
    assert est.rate == 25.0


def test_a_full_result_cannot_slow_the_rate_down():
    est = ChargeEstimator(25.0)
    end = charge_and_unplug(est, 50, 4.0)                       # reads full: 12.3 %/h is only a lower bound
    assert est.settle(end + charge.SETTLE_S, 99) is None
    assert est.rate == 25.0
    est = ChargeEstimator(25.0)
    end = charge_and_unplug(est, 50, 1.5)                       # full, but at least 33 %/h
    assert est.settle(end + charge.SETTLE_S, 99) > 25.0
    est = ChargeEstimator(25.0)
    end = charge_and_unplug(est, 50, 2.5)                       # 98 %: still measuring, a slow rate counts
    assert est.settle(end + charge.SETTLE_S, 98) < 25.0


def test_no_percentage_at_settle_time_keeps_waiting_and_cancel_skips_learning():
    est = ChargeEstimator(25.0)
    end = charge_and_unplug(est, 60, 1.0)
    assert est.settle(end + 400, None) is None
    assert est.settle(end + 500, 80) is not None                # still pending until a value arrives
    est = ChargeEstimator(25.0)
    end = charge_and_unplug(est, 60, 1.0)
    est.cancel()                                                # headset turned off before 5 min
    assert est.settle(end + 400, 80) is None and est.rate == 25.0 and not est.has_state


def test_replug_before_settling_resumes_the_session_without_learning():
    est = ChargeEstimator(25.0)
    est.start(T0, 50)
    est.finish(T0 + H)
    assert est.estimate(T0 + H + 60) is None                    # not charging: no estimate
    est.start(T0 + H + 120, 75)                                 # the new start value is ignored
    assert est.session == ChargeSession(T0, 50.0)
    assert est.settle(T0 + 3 * H, 90) is None and est.rate == 25.0


def test_observed_rate():
    assert observed_rate(50, 75, 1.0) == 25.0
    assert observed_rate(50, 50, 1.0) is None
    assert observed_rate(50, 75, 0) is None
    assert observed_rate(0, 100, 0.5) is None                   # 200 %/h


# -- restart restore / state ---------------------------------------------------------
def test_state_round_trip():
    est = ChargeEstimator(31.5)
    est.start(T0, 64, start_ts=T0 - 300)
    again = ChargeEstimator.from_state(json.loads(json.dumps(est.to_state())), T0 + 600)
    assert again.rate == pytest.approx(31.5)
    assert again.session == est.session
    assert again.estimate(T0 + 600) == est.estimate(T0 + 600)


def test_a_finished_session_is_not_persisted():
    est = started(50)
    est.finish(T0 + H)
    assert ChargeEstimator.from_state(est.to_state(), T0 + H).session is None


@pytest.mark.parametrize(
    "state",
    [
        None, [], "x", 42, {}, {"rate": "fast"}, {"rate": float("nan")}, {"rate": True},
        {"session": 5}, {"session": {}}, {"session": {"start_ts": "a", "start_percent": 5}},
        {"session": {"start_ts": T0, "start_percent": 150}},
        {"session": {"start_ts": T0 - 20 * H, "start_percent": 50}},     # too old
        {"session": {"start_ts": T0 + 5 * H, "start_percent": 50}},      # from the future
    ],
)
def test_garbage_state_falls_back_to_defaults(state):
    est = ChargeEstimator.from_state(state, T0)
    assert est.session is None and est.rate == charge.DEFAULT_RATE


def test_rate_from_state_is_clamped():
    assert ChargeEstimator.from_state({"rate": 900}, T0).rate == charge.MAX_RATE


def test_file_round_trip_and_missing_file(tmp_path):
    path = tmp_path / charge.STATE_FILE
    assert load_estimator(path, T0).rate == charge.DEFAULT_RATE         # no file yet
    est = ChargeEstimator(33.0)
    est.start(T0, 70)
    save_estimator(path, est)
    assert not (tmp_path / (charge.STATE_FILE + ".tmp")).exists()
    again = load_estimator(path, T0 + 60)
    assert again.rate == pytest.approx(33.0) and again.session == est.session


@pytest.mark.parametrize("content", ["", "{not json", "\x00\x01", "[1, 2]", '{"rate": null}'])
def test_corrupt_file_falls_back_to_defaults(tmp_path, content):
    path = tmp_path / charge.STATE_FILE
    path.write_text(content, encoding="utf-8")
    est = load_estimator(path, T0)
    assert est.rate == charge.DEFAULT_RATE and est.session is None


def test_undecodable_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / charge.STATE_FILE
    path.write_bytes(b"\xff\xfe\xfa{")
    assert load_estimator(path, T0).rate == charge.DEFAULT_RATE


def test_save_failure_is_swallowed(tmp_path):
    save_estimator(tmp_path / "missing_dir" / "state.json", ChargeEstimator())
    save_estimator(None, ChargeEstimator())
    assert load_estimator(None, T0).rate == charge.DEFAULT_RATE


# -- start percent from history ------------------------------------------------------
def row(ts, percent, charging=False):
    return Sample(ts, 4300 if charging else 3900, None if charging else percent, charging)


def test_history_start_uses_last_percent_and_first_charging_row_after_it():
    now = T0
    rows = [row(now - 3 * H, 90), row(now - 2 * H, 82), row(now - 1.5 * H, None, True),
            row(now - 1 * H, None, True), row(now - 60, None, True)]
    assert start_from_history(rows, now) == (82.0, now - 1.5 * H)


def test_history_start_is_now_without_charging_rows_and_none_when_stale():
    assert start_from_history([row(T0 - 600, 70)], T0) == (70.0, T0)
    assert start_from_history([row(T0 - 13 * H, 70)], T0) is None
    assert start_from_history([row(T0 - 60, None, True)], T0) is None
    assert start_from_history([], T0) is None
