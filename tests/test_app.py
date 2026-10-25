"""Controller logic with fake UI / keep-alive (no hardware, no tkinter)."""
import pytest

from hyperx_battery import battery
from hyperx_battery.app import Controller
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


class Rig:
    def __init__(self, tmp_path, cfg=None, wall_now=1_000_000.0):
        self.cfg = cfg or Config()
        self.cfg_path = tmp_path / "config.json"
        self.store = HistoryStore(":memory:")
        self.ui = FakeUI()
        self.ka = FakeKeepAlive()
        self.quit_called = False
        self.shown = 0
        self.t = 0.0
        self.wall_now = wall_now
        self.c = Controller(
            self.cfg, self.cfg_path, tmp_path, self.store, self.ka,
            show_history=self._show, request_quit=self._quit, wall=lambda: self.wall_now,
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
