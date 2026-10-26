"""Controller logic with fake UI / keep-alive (no hardware, no tkinter)."""
import pytest

from hyperx_battery import audio_switch as asw
from hyperx_battery import battery
from hyperx_battery.app import Controller
from hyperx_battery.audio_switch import AudioSwitcher, Endpoint
from hyperx_battery.config import Config
from hyperx_battery.device import BatteryEvent, DongleEvent, MuteEvent, PresenceEvent
from hyperx_battery.history import HistoryStore, Sample
from hyperx_battery.session import SessionSnapshot


class FakeUI:
    def __init__(self):
        self.views = []
        self.notes = []

    def update(self, view):
        self.views.append(view)

    def notify(self, message, title=None):
        self.notes.append(message)

    @property
    def view(self):
        return self.views[-1]


class FakeKeepAlive:
    def __init__(self):
        self.wanted = False
        self.state = "idle"

    def set_wanted(self, wanted):
        self.wanted = wanted
        self.state = "running" if wanted else "idle"


class FakeAudio:
    """Stands in for AudioService: records engage / restore calls."""

    def __init__(self):
        self.calls = []

    def engage(self):
        self.calls.append("engage")

    def restore(self):
        self.calls.append("restore")


PLAYBACK, CAPTURE = asw.PLAYBACK, asw.CAPTURE
LG = Endpoint("out-lg", "1 - LG ULTRAGEAR (AMD High Definition Audio Device)")
REALTEK = Endpoint("out-realtek", "Altavoces (Realtek(R) Audio)")
HX_OUT = Endpoint("out-hyperx", "Altavoces (HyperX Cloud Flight for PS)")
BRIO = Endpoint("in-brio", "Micrófono (Brio 100)")
HX_MIC = Endpoint("in-hyperx", "Micrófono (HyperX Cloud Flight for PS)")


class MiniBackend(asw.AudioBackend):
    """Just enough of an audio backend for the Controller + AudioSwitcher tests."""

    def __init__(self, playback, capture):
        self.endpoints = {PLAYBACK: [LG, HX_OUT, REALTEK], CAPTURE: [BRIO, HX_MIC]}
        self.defaults = {PLAYBACK: playback, CAPTURE: capture}

    def default_endpoint(self, flow):
        return self.defaults[flow]

    def active_endpoints(self, flow):
        return list(self.endpoints[flow])

    def set_default(self, endpoint_id):
        for flow, eps in self.endpoints.items():
            for ep in eps:
                if ep.id == endpoint_id:
                    self.defaults[flow] = ep


class Rig:
    def __init__(self, tmp_path, cfg=None, wall_now=1_000_000.0, audio=False):
        self.cfg = cfg or Config()
        self.cfg_path = tmp_path / "config.json"
        self.store = HistoryStore(":memory:")
        self.ui = FakeUI()
        self.ka = FakeKeepAlive()
        self.quit_called = False
        self.shown = 0
        self.t = 0.0
        self.wall_now = wall_now
        self.mono = 5000.0
        self.audio = FakeAudio() if audio is True else (audio or None)
        self.c = Controller(
            self.cfg, self.cfg_path, tmp_path, self.store, self.ka,
            audio=self.audio,
            show_history=self._show, request_quit=self._quit, wall=lambda: self.wall_now,
            clock=lambda: self.mono,
        )
        self.c.ui = self.ui

    def _show(self):
        self.shown += 1

    def _quit(self):
        self.quit_called = True

    def send(self, *events):
        for e in events:
            self.c.post(e)
        self.c.process_pending()

    def battery(self, mv, dt=5.0):
        self.t += dt
        self.wall_now += dt
        self.send(BatteryEvent(mv, self.t, self.wall_now))

    def power_on(self):
        self.send(DongleEvent(True), PresenceEvent(True))


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_initial_state_is_searching_and_keepalive_waits(rig):
    rig.c.refresh()
    assert rig.ui.view.link == "searching"
    assert "esperando" in rig.ui.view.keepalive


def test_battery_reading_shows_percent_and_starts_keepalive(rig):
    rig.power_on()
    rig.battery(3860)
    view = rig.ui.view
    assert view.link == "on" and view.percent == 75 and not view.charging
    assert rig.ka.wanted is True
    assert view.keepalive == "activo"


def test_headset_off_turns_everything_gray_and_stops_keepalive(rig):
    rig.power_on()
    rig.battery(3860)
    rig.send(PresenceEvent(False))
    assert rig.ui.view.link == "off" and rig.ui.view.percent is None
    assert rig.ka.wanted is False
    assert "esperando" in rig.ui.view.keepalive


def test_smoothing_restarts_after_the_headset_was_off(rig):
    rig.power_on()
    rig.battery(3774)
    rig.send(PresenceEvent(False))
    rig.send(PresenceEvent(True))
    rig.battery(3900)                                  # higher than before: allowed after an off period
    assert rig.ui.view.percent == 83


def test_dongle_unplugged_shows_no_dongle_and_stops_keepalive(rig):
    rig.power_on()
    rig.battery(3860)
    rig.send(DongleEvent(False))
    assert rig.ui.view.link == "no_dongle"
    assert rig.ka.wanted is False
    rig.send(DongleEvent(True))
    assert rig.ui.view.link == "off"                   # until the first reply arrives
    rig.send(PresenceEvent(True))
    rig.battery(3860)
    assert rig.ui.view.link == "on" and rig.ka.wanted


# -- notifications -------------------------------------------------------------------
def test_low_battery_notifies_once_per_threshold(rig):
    rig.power_on()
    rig.battery(battery.percent_to_mv(25))
    assert rig.ui.notes == []
    for _ in range(40):                                # the number walks down 1 point per update
        rig.battery(battery.percent_to_mv(15))
    assert len(rig.ui.notes) == 1 and "20 %" in rig.ui.notes[0]
    for _ in range(40):
        rig.battery(battery.percent_to_mv(8))
    assert len(rig.ui.notes) == 2 and "10 %" in rig.ui.notes[1] and "Batería baja" in rig.ui.notes[1]


def test_low_battery_rearms_after_charging(rig):
    rig.power_on()
    for _ in range(5):
        rig.battery(battery.percent_to_mv(15))
    assert len(rig.ui.notes) == 1
    for _ in range(3):
        rig.battery(4250)                              # charging
    assert rig.ui.view.charging and rig.ui.view.percent is None
    for _ in range(5):
        rig.battery(battery.percent_to_mv(15))
    assert len(rig.ui.notes) == 2


def test_low_battery_notifications_can_be_disabled(tmp_path):
    rig = Rig(tmp_path, Config(notify_low_battery=False))
    rig.power_on()
    for _ in range(5):
        rig.battery(battery.percent_to_mv(5))
    assert rig.ui.notes == []


def test_simulated_percent_15_fires_the_20_percent_notice_at_once(tmp_path):
    rig = Rig(tmp_path)
    rig.power_on()
    rig.battery(round(battery.percent_to_mv(15)))
    assert len(rig.ui.notes) == 1


# -- mute ----------------------------------------------------------------------------
def test_mute_events_notify_and_mark_the_icon(rig):
    rig.power_on()
    rig.battery(3860)
    assert rig.ui.view.muted is None                    # never heard of mute: feature inactive
    rig.send(MuteEvent(True))
    assert rig.ui.notes[-1] == "Micrófono silenciado" and rig.ui.view.muted is True
    rig.send(MuteEvent(True))                           # repeated: no second toast
    assert len(rig.ui.notes) == 1
    rig.send(MuteEvent(False))
    assert rig.ui.notes[-1] == "Micrófono activo" and rig.ui.view.muted is False


def test_mute_state_is_forgotten_when_the_headset_goes_off(rig):
    rig.power_on()
    rig.send(MuteEvent(True))
    rig.send(PresenceEvent(False))
    assert rig.ui.view.muted is None


def test_mute_notifications_can_be_disabled(tmp_path):
    rig = Rig(tmp_path, Config(notify_mute=False))
    rig.power_on()
    rig.send(MuteEvent(True))
    assert rig.ui.notes == [] and rig.ui.view.muted is True


# -- keep-alive pause ----------------------------------------------------------------
def test_keepalive_pauses_when_locked_and_resumes(rig):
    rig.power_on()
    rig.battery(3860)
    rig.send(SessionSnapshot(locked=True, idle_s=0))
    assert rig.ka.wanted is False and "bloqueada" in rig.ui.view.keepalive
    rig.send(SessionSnapshot(locked=False, idle_s=0))
    assert rig.ka.wanted is True and rig.ui.view.keepalive == "activo"


def test_keepalive_pauses_after_idle_and_resumes(rig):
    rig.power_on()
    rig.send(SessionSnapshot(locked=False, idle_s=15 * 60))
    assert rig.ka.wanted is False and "inactividad" in rig.ui.view.keepalive
    rig.send(SessionSnapshot(locked=False, idle_s=3))
    assert rig.ka.wanted is True


def test_manual_toggle_persists_and_applies(rig):
    rig.power_on()
    assert rig.ka.wanted is True
    rig.c.toggle_keepalive()
    assert rig.ka.wanted is False and rig.ui.view.keepalive == "desactivado"
    assert Config.load(rig.cfg_path).keepalive_enabled is False
    rig.c.toggle_keepalive()
    assert rig.ka.wanted is True
    assert Config.load(rig.cfg_path).keepalive_enabled is True


def test_keepalive_retrying_is_visible(rig):
    rig.power_on()
    rig.ka.state = "retrying"
    rig.c.refresh()
    assert rig.ui.view.keepalive == "reintentando…"


# -- history / estimate ----------------------------------------------------------------
def test_history_rows_are_written_once_per_minute(rig):
    rig.power_on()
    for _ in range(30):                                # 150 s
        rig.battery(3860)
    rows = rig.store.range(0, 10**9)
    assert 1 <= len(rows) <= 3
    assert all(r.mv == 3860 and r.percent == 75 and not r.charging for r in rows)


def test_shutdown_and_headset_off_flush_the_partial_minute(rig):
    rig.power_on()
    rig.battery(3860)
    assert rig.store.range(0, 10**9) == []
    rig.send(PresenceEvent(False))
    assert len(rig.store.range(0, 10**9)) == 1
    rig.send(PresenceEvent(True))
    rig.battery(3850, dt=120)
    rig.c.shutdown()
    assert len(rig.store.range(0, 10**9)) == 2


def test_remaining_time_shows_calculating_then_a_number(tmp_path):
    rig = Rig(tmp_path)
    rig.power_on()
    rig.battery(3900)
    assert rig.ui.view.remaining == "calculando…"
    # two hours of earlier readings in the database: 4 % per hour
    now = rig.wall_now
    for m in range(121, 0, -1):
        pct = 85 + 4.0 * (m / 60.0)
        rig.store.add(Sample(now - m * 60.0, round(battery.percent_to_mv(pct)), round(pct), False))
    rig.c._need_estimate = True
    rig.battery(3900)
    assert rig.ui.view.remaining.startswith("~") and rig.ui.view.remaining.endswith("restantes")


def test_no_estimate_while_charging(rig):
    rig.power_on()
    rig.battery(4250)
    rig.battery(4250)
    assert rig.ui.view.remaining is None


# -- plumbing ---------------------------------------------------------------------------
def test_callables_run_on_process_pending_and_errors_do_not_stop_the_pump(rig):
    ran = []
    rig.c.post(lambda: 1 / 0)
    rig.c.post(lambda: ran.append("ok"))
    rig.c.process_pending()
    assert ran == ["ok"]


def test_tray_actions_post_to_the_queue(rig):
    actions = rig.c.tray_actions()
    actions.show_history()
    actions.quit()
    assert rig.shown == 0 and not rig.quit_called       # nothing runs on the caller's thread
    rig.c.process_pending()
    assert rig.shown == 1 and rig.quit_called
    assert actions.keepalive_enabled() is True


def test_simulated_flag_reaches_the_view(tmp_path):
    ui = FakeUI()
    c = Controller(Config(), tmp_path / "c.json", tmp_path, HistoryStore(":memory:"), None, simulated=True)
    c.ui = ui
    c.post(DongleEvent(True)); c.post(PresenceEvent(True))
    c.post(BatteryEvent(3860, 1.0, 1.0))
    c.process_pending()
    assert ui.view.simulated and "simulación" in ui.view.keepalive and ui.view.percent == 75


# -- charge estimate ---------------------------------------------------------------------
CHARGING_MV = 4317
H = 3600.0


def charging_rig(tmp_path, percent=60, **kw):
    """Powered-on rig that has shown ``percent`` and has just started charging."""
    rig = Rig(tmp_path, **kw)
    rig.power_on()
    for _ in range(3):
        rig.battery(round(battery.percent_to_mv(percent)))
    for _ in range(2):                                 # the tracker confirms charging on the 2nd reading
        rig.battery(CHARGING_MV)
    assert rig.ui.view.charging
    return rig


def new_controller(tmp_path, wall_now, **kw):
    return Controller(Config(), tmp_path / "config.json", tmp_path, HistoryStore(":memory:"), None,
                      wall=lambda: wall_now, **kw)


def test_estimate_starts_from_the_last_shown_percent_and_grows(tmp_path):
    rig = charging_rig(tmp_path, 60)
    view = rig.ui.view
    assert view.percent is None and view.estimate == 60          # the measured value stays None
    rig.battery(CHARGING_MV, dt=0.4 * H)                         # +10 % at 25 %/h
    assert rig.ui.view.estimate == 70
    assert rig.ui.view.full_in == "lleno en ~1,2 h"
    rig.battery(CHARGING_MV, dt=2 * H)
    assert rig.ui.view.estimate == 99 and rig.ui.view.full_in == "casi lleno"
    rig.battery(CHARGING_MV, dt=10 * H)
    assert rig.ui.view.estimate is None and rig.ui.view.charging  # too old to trust


def test_estimate_is_never_written_to_history(tmp_path):
    rig = charging_rig(tmp_path, 60)
    for _ in range(30):
        rig.battery(CHARGING_MV, dt=60)
    rig.c.shutdown()
    rows = [r for r in rig.store.range(0, 10**9) if r.charging]
    assert rows and all(r.percent is None for r in rows)


def test_without_a_known_start_there_is_no_estimate(tmp_path):
    rig = Rig(tmp_path)
    rig.power_on()
    for _ in range(3):
        rig.battery(CHARGING_MV)                                 # app started while charging, no history
    assert rig.ui.view.charging and rig.ui.view.estimate is None and rig.ui.view.full_in is None


def test_app_started_while_charging_uses_recent_history(tmp_path):
    rig = Rig(tmp_path)
    now = rig.wall_now
    rig.store.add(Sample(now - 2 * H, 3960, 95, False))
    rig.store.add(Sample(now - 1 * H, 3950, 70, False))
    for m in range(40):
        rig.store.add(Sample(now - 1800 + 30 * m, CHARGING_MV, None, True))   # charging since now - 30 min
    rig.power_on()
    for _ in range(2):
        rig.battery(CHARGING_MV)
    assert rig.ui.view.estimate == 83                            # 70 + 25 %/h * 0.5 h (+ the 10 s of this run)


def test_history_that_is_too_old_gives_no_estimate(tmp_path):
    rig = Rig(tmp_path)
    rig.store.add(Sample(rig.wall_now - 13 * H, 3950, 70, False))
    rig.power_on()
    for _ in range(2):
        rig.battery(CHARGING_MV)
    assert rig.ui.view.estimate is None


def test_unplug_switches_back_to_the_measured_value(tmp_path):
    rig = charging_rig(tmp_path, 82)
    rig.battery(CHARGING_MV, dt=0.6 * H)
    assert rig.ui.view.estimate == 97
    shown = []
    for mv in (4025, 4025, 4016, 4016, 4007, 4007):             # real post-unplug readings (surface charge)
        rig.battery(mv)
        shown.append(rig.ui.view)
    assert all(not v.charging and v.estimate is None and v.full_in is None for v in shown[1:])
    percents = [v.percent for v in shown[1:]]
    assert percents[0] == 97                                     # the measurement, not the estimate
    assert percents == sorted(percents, reverse=True)            # and it only goes down from there


def test_rate_is_learned_five_minutes_after_the_unplug_and_persisted(tmp_path):
    rig = charging_rig(tmp_path, 82)
    rig.battery(CHARGING_MV, dt=37 * 60)
    for _ in range(80):                                          # 6.7 min of discharge readings
        rig.battery(4025)
    learned = rig.c._charge.rate
    assert learned != 25.0 and 22.0 < learned < 28.0     # 82 -> 97 % in ~37 min
    assert new_controller(tmp_path, rig.wall_now)._charge.rate == pytest.approx(learned, abs=0.01)


def test_no_learning_when_the_headset_turns_off_before_five_minutes(tmp_path):
    rig = charging_rig(tmp_path, 82)
    rig.battery(CHARGING_MV, dt=37 * 60)
    for _ in range(10):                                          # 50 s
        rig.battery(4025)
    rig.send(PresenceEvent(False))
    rig.send(PresenceEvent(True))
    for _ in range(80):
        rig.battery(4025)
    assert rig.c._charge.rate == 25.0


def test_restart_mid_charge_keeps_the_estimate(tmp_path):
    rig = charging_rig(tmp_path, 60)
    rig.battery(CHARGING_MV, dt=0.2 * H)
    assert rig.ui.view.estimate == 65
    rig.c.shutdown()
    # a new process 0.2 h later, still charging (nothing in history says what the % was)
    rig2 = Rig(tmp_path, wall_now=rig.wall_now + 0.2 * H)
    rig2.power_on()
    rig2.battery(CHARGING_MV)
    assert rig2.ui.view.estimate == 70


def test_restart_after_the_charge_ended_drops_the_stale_session(tmp_path):
    rig = charging_rig(tmp_path, 60)
    rig.c.shutdown()
    rig2 = Rig(tmp_path, wall_now=rig.wall_now + H)
    rig2.power_on()
    rig2.battery(3950)                                           # unplugged while the app was closed
    assert rig2.ui.view.estimate is None and rig2.c._charge.session is None
    assert new_controller(tmp_path, rig2.wall_now)._charge.session is None   # also gone from the file


def test_corrupt_state_file_does_not_break_the_app(tmp_path):
    (tmp_path / "charge_state.json").write_text("{oops", encoding="utf-8")
    rig = charging_rig(tmp_path, 60)
    assert rig.ui.view.estimate == 60 and rig.c._charge.rate == 25.0


def test_simulation_never_touches_the_state_file(tmp_path):
    c = new_controller(tmp_path, 10.0, simulated=True)
    c.ui = FakeUI()
    c.post(DongleEvent(True)); c.post(PresenceEvent(True))
    for i in range(3):
        c.post(BatteryEvent(3950, float(i), 1.0 + i))
    for i in range(3, 6):
        c.post(BatteryEvent(CHARGING_MV, float(i), 1.0 + i))
    c.process_pending()
    assert c.ui.view.estimate is not None
    assert not (tmp_path / "charge_state.json").exists()


def test_low_battery_notifier_ignores_the_estimate(tmp_path):
    rig = charging_rig(tmp_path, 15)
    n = len(rig.ui.notes)
    rig.battery(CHARGING_MV, dt=2 * H)                           # the estimate climbs well above 20
    assert len(rig.ui.notes) == n
    for _ in range(5):
        rig.battery(round(battery.percent_to_mv(15)))            # unplugged at 15 %: warns again
    assert len(rig.ui.notes) == n + 1


# -- automatic default audio device ---------------------------------------------------------------
def audio_rig(tmp_path, **cfg):
    return Rig(tmp_path, Config(**cfg), audio=True)


def test_headset_on_engages_the_audio_switch(tmp_path):
    rig = audio_rig(tmp_path)
    rig.power_on()
    assert rig.audio.calls == ["engage"]


def test_explicit_power_off_restores_immediately(tmp_path):
    rig = audio_rig(tmp_path)
    rig.power_on()
    rig.send(PresenceEvent(False, explicit=True))
    assert rig.audio.calls == ["engage", "restore"]
    rig.mono += 120
    rig.c.process_pending()
    assert rig.audio.calls == ["engage", "restore"]                  # and only once


def test_inferred_off_waits_for_confirmation_then_restores(tmp_path):
    rig = audio_rig(tmp_path)
    rig.power_on()
    rig.send(PresenceEvent(False))
    assert rig.audio.calls == ["engage"]
    rig.mono += 19
    rig.c.process_pending()
    assert rig.audio.calls == ["engage"]                             # not yet
    rig.mono += 2
    rig.c.process_pending()
    assert rig.audio.calls == ["engage", "restore"]
    rig.mono += 100
    rig.c.process_pending()
    assert rig.audio.calls == ["engage", "restore"]


def test_quick_power_on_cancels_the_pending_restore(tmp_path):
    rig = audio_rig(tmp_path)
    rig.power_on()
    rig.send(PresenceEvent(False))                                   # out of range for a moment
    rig.mono += 10
    rig.send(PresenceEvent(True))
    rig.mono += 60
    rig.c.process_pending()
    assert rig.audio.calls == ["engage", "engage"]                   # no restore ever; engage is a no-op for the switcher


def test_explicit_off_during_the_wait_restores_at_once(tmp_path):
    rig = audio_rig(tmp_path)
    rig.power_on()
    rig.send(PresenceEvent(False))
    rig.send(PresenceEvent(False, explicit=True))
    assert rig.audio.calls == ["engage", "restore"]


def test_the_wait_is_not_extended_by_repeated_off_signals(tmp_path):
    rig = audio_rig(tmp_path)
    rig.power_on()
    rig.send(DongleEvent(False))                                     # dongle lost, then the presence follows
    rig.mono += 15
    rig.send(PresenceEvent(False))
    rig.mono += 6
    rig.c.process_pending()
    assert rig.audio.calls == ["engage", "restore"]


def test_zero_confirmation_delay_restores_at_once(tmp_path):
    rig = audio_rig(tmp_path, auto_switch_off_confirm_s=0)
    rig.power_on()
    rig.send(PresenceEvent(False))
    assert rig.audio.calls == ["engage", "restore"]


def test_startup_with_headset_off_still_restores_stale_state_after_the_wait(tmp_path):
    rig = audio_rig(tmp_path)
    rig.send(DongleEvent(True), PresenceEvent(False))                # app started while the headset is off
    assert rig.audio.calls == []
    rig.mono += 21
    rig.c.process_pending()
    assert rig.audio.calls == ["restore"]                            # the switcher fixes a HyperX default, else does nothing


def test_two_cycles(tmp_path):
    rig = audio_rig(tmp_path)
    rig.power_on()
    rig.send(PresenceEvent(False, explicit=True))
    rig.send(PresenceEvent(True))
    rig.send(PresenceEvent(False, explicit=True))
    assert rig.audio.calls == ["engage", "restore", "engage", "restore"]


def test_disabled_option_means_no_switching(tmp_path):
    rig = audio_rig(tmp_path, auto_switch_audio=False)
    rig.power_on()
    rig.send(PresenceEvent(False))
    rig.send(PresenceEvent(False, explicit=True))
    rig.mono += 100
    rig.c.process_pending()
    assert rig.audio.calls == []


def test_toggle_persists_and_enabling_while_on_switches_now(tmp_path):
    rig = audio_rig(tmp_path, auto_switch_audio=False)
    rig.power_on()
    assert rig.audio.calls == []
    rig.c.toggle_audio_switch()
    assert rig.audio.calls == ["engage"]
    assert Config.load(rig.cfg_path).auto_switch_audio is True
    assert rig.c.tray_actions().audio_switch_enabled() is True
    rig.c.toggle_audio_switch()
    assert Config.load(rig.cfg_path).auto_switch_audio is False
    rig.send(PresenceEvent(False, explicit=True))
    assert rig.audio.calls == ["engage"]


def test_disabling_cancels_a_pending_restore(tmp_path):
    rig = audio_rig(tmp_path)
    rig.power_on()
    rig.send(PresenceEvent(False))
    rig.c.toggle_audio_switch()
    rig.mono += 100
    rig.c.process_pending()
    assert rig.audio.calls == ["engage"]


class InlineAudio:
    """AudioService stand-in that runs a real AudioSwitcher on the calling thread,
    handing its toast text to ``on_result`` like the worker does."""

    def __init__(self, switcher):
        self.switcher = switcher
        self.on_result = lambda text: None

    def _report(self, text):
        if text:
            self.on_result(text)

    def engage(self):
        self._report(self.switcher.engage())

    def restore(self):
        self._report(self.switcher.restore())


def switcher_rig(tmp_path, backend, **cfg):
    """Controller wired to a real AudioSwitcher over a fake backend, with the app's default fixed devices."""
    c = Config(**cfg)
    switcher = AudioSwitcher(backend, tmp_path / "audio_state.json", c.auto_switch_device_match,
                             c.off_playback_match, c.off_capture_match)
    audio = InlineAudio(switcher)
    rig = Rig(tmp_path, c, audio=audio)
    audio.on_result = rig.c.audio_switched
    return rig


def test_startup_with_headset_off_and_hyperx_mic_stuck_as_default_moves_it_after_the_wait(tmp_path):
    backend = MiniBackend(playback=REALTEK, capture=HX_MIC)           # the headset is off, its mic is still the default
    rig = switcher_rig(tmp_path, backend)
    rig.send(DongleEvent(True), PresenceEvent(False))                 # app started while the headset is off
    rig.mono += 19
    rig.c.process_pending()
    assert backend.defaults[CAPTURE] == HX_MIC                        # same confirm delay as ever
    rig.mono += 2
    rig.c.process_pending()
    assert backend.defaults == {PLAYBACK: REALTEK, CAPTURE: BRIO}
    assert rig.ui.notes == ["Audio: Micrófono (Brio 100)"]            # one toast, only what changed


def test_full_cycle_with_the_fixed_off_devices_and_one_toast_per_switch(tmp_path):
    backend = MiniBackend(playback=LG, capture=HX_MIC)
    rig = switcher_rig(tmp_path, backend)
    rig.power_on()
    assert backend.defaults == {PLAYBACK: HX_OUT, CAPTURE: HX_MIC}
    rig.send(PresenceEvent(False, explicit=True))
    assert backend.defaults == {PLAYBACK: REALTEK, CAPTURE: BRIO}     # not the LG the user had before
    assert rig.ui.notes == ["Audio: HyperX Cloud Flight",
                            "Audio: Altavoces (Realtek(R) Audio) · Micrófono (Brio 100)"]


def test_audio_toast_is_one_notification_per_switch(tmp_path):
    rig = audio_rig(tmp_path)
    rig.c.audio_switched("Audio: HyperX Cloud Flight")
    assert rig.ui.notes == ["Audio: HyperX Cloud Flight"]


def test_exit_does_not_restore(tmp_path):
    rig = audio_rig(tmp_path)
    rig.power_on()
    rig.c.shutdown()
    assert rig.audio.calls == ["engage"]


def test_no_audio_service_is_fine(rig):
    rig.power_on()
    rig.send(PresenceEvent(False, explicit=True))
    rig.c.toggle_audio_switch()
    assert rig.ui.view.link == "off"
