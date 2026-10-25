import time

import numpy as np
import pytest

from hyperx_battery.keepalive import (
    KeepAlive,
    decide_keepalive,
    find_output_device,
    ramp_gains,
    render_tone,
)


# --------------------------------------------------------------------------
# Pause decision
# --------------------------------------------------------------------------
def decide(**kw):
    args = dict(enabled=True, headset_on=True, session_locked=False, idle_s=0.0, idle_pause_min=15.0)
    args.update(kw)
    return decide_keepalive(**args)


def test_runs_when_everything_is_fine():
    d = decide()
    assert d.run and d.reason == "active"


def test_manual_switch_off_wins():
    assert decide(enabled=False) == decide(enabled=False, headset_on=False)
    assert not decide(enabled=False).run
    assert decide(enabled=False).reason == "disabled"


def test_paused_while_headset_is_off():
    d = decide(headset_on=False)
    assert not d.run and d.reason == "headset_off"


def test_paused_while_session_is_locked():
    d = decide(session_locked=True)
    assert not d.run and d.reason == "locked"


def test_paused_after_n_minutes_without_input_and_resumes():
    assert decide(idle_s=14 * 60 + 59).run
    d = decide(idle_s=15 * 60)
    assert not d.run and d.reason == "idle"
    assert decide(idle_s=2.0).run                     # user is back


def test_idle_threshold_is_configurable_and_can_be_disabled():
    assert not decide(idle_s=61, idle_pause_min=1).run
    assert decide(idle_s=10 * 3600, idle_pause_min=0).run


def test_lock_is_reported_before_idle():
    assert decide(session_locked=True, idle_s=99999).reason == "locked"


# --------------------------------------------------------------------------
# Tone synthesis
# --------------------------------------------------------------------------
def test_tone_matches_the_verified_signal():
    block, phase = render_tone(48000, 0.0, hz=10, amp=0.05, sample_rate=48000, channels=2)
    assert block.dtype == np.float32 and block.shape == (48000, 2)
    assert abs(block).max() == pytest.approx(0.05, abs=1e-4)
    assert np.array_equal(block[:, 0], block[:, 1])
    # exactly 10 cycles in one second
    zero_crossings = np.sum(np.diff(np.signbit(block[:, 0]).astype(int)) != 0)
    assert zero_crossings in (19, 20)
    assert phase == pytest.approx(0.0, abs=1e-6) or phase == pytest.approx(2 * np.pi, abs=1e-6)


def test_tone_is_continuous_across_blocks():
    whole, _ = render_tone(1000, 0.0, hz=10, amp=0.05, sample_rate=48000, channels=1)
    a, phase = render_tone(400, 0.0, hz=10, amp=0.05, sample_rate=48000, channels=1)
    b, _ = render_tone(600, phase, hz=10, amp=0.05, sample_rate=48000, channels=1)
    assert np.allclose(np.concatenate([a, b]), whole, atol=1e-6)


def test_tone_starts_from_zero_without_a_pop():
    block, _ = render_tone(256, 0.0, hz=10, amp=0.05, sample_rate=48000, channels=2)
    assert block[0, 0] == 0.0


def test_ramp_gains_fade_in_and_out():
    gains, g = ramp_gains(0.0, 1.0, 100, step=0.01)
    assert gains[0] == pytest.approx(0.01) and gains[-1] == pytest.approx(1.0) and g == pytest.approx(1.0)
    gains, g = ramp_gains(1.0, 0.0, 10, step=0.01)
    assert g == pytest.approx(0.9) and gains[-1] == pytest.approx(0.9)
    gains, g = ramp_gains(0.5, 0.5, 4, step=0.1)
    assert list(gains) == [0.5] * 4 and g == 0.5


def test_ramp_never_overshoots():
    gains, g = ramp_gains(0.95, 1.0, 50, step=0.1)
    assert gains.max() == 1.0 and g == 1.0
    gains, g = ramp_gains(0.05, 0.0, 50, step=0.1)
    assert gains.min() == 0.0 and g == 0.0


def test_faded_tone_is_scaled():
    gains = np.linspace(0, 1, 100)
    block, _ = render_tone(100, np.pi / 2, hz=10, amp=0.05, sample_rate=48000, channels=1, gains=gains)
    assert block[0, 0] == 0.0 and block[-1, 0] == pytest.approx(0.05, rel=1e-2)


# --------------------------------------------------------------------------
# Device lookup
# --------------------------------------------------------------------------
HOSTAPIS = [{"name": "MME"}, {"name": "Windows WASAPI"}, {"name": "Windows WDM-KS"}]
DEVICES = [
    {"name": "Altavoces (HyperX Cloud Flight for PS)", "hostapi": 0, "max_output_channels": 2},   # MME
    {"name": "Micrófono (HyperX Cloud Flight for PS)", "hostapi": 1, "max_output_channels": 0},   # input only
    {"name": "Speakers (Realtek)", "hostapi": 1, "max_output_channels": 2},
    {"name": "Altavoces (HyperX Cloud Flight for PS)", "hostapi": 1, "max_output_channels": 2},
]


def test_finds_the_wasapi_output_by_name_not_the_default():
    assert find_output_device(DEVICES, HOSTAPIS, "HyperX") == 3
    assert find_output_device(DEVICES, HOSTAPIS, "hyperx") == 3


def test_no_match_or_no_wasapi():
    assert find_output_device(DEVICES, HOSTAPIS, "Logitech") is None
    assert find_output_device(DEVICES, [{"name": "MME"}], "HyperX") is None


# --------------------------------------------------------------------------
# KeepAlive thread with a fake sounddevice
# --------------------------------------------------------------------------
class FakeStream:
    def __init__(self, owner, **kwargs):
        self.owner = owner
        self.kwargs = kwargs
        self.active = False
        self.closed = False
        self._stop_pump = False

    def start(self):
        if self.owner.fail_open:
            raise OSError("device unavailable")
        self.active = True
        import threading

        def pump():
            buf = np.zeros((480, self.kwargs["channels"]), dtype=np.float32)
            while self.active and not self._stop_pump:
                self.kwargs["callback"](buf, 480, None, None)
                self.owner.last_block = buf.copy()
                time.sleep(0.01)

        self.thread = threading.Thread(target=pump, daemon=True)
        self.thread.start()

    def stop(self):
        self.active = False

    def close(self):
        self.closed = True
        self.active = False


class FakeSd:
    def __init__(self):
        self.fail_open = False
        self.present = True
        self.streams = []
        self.reinit = 0
        self.last_block = None

    def query_hostapis(self):
        return HOSTAPIS

    def query_devices(self, index=None):
        devs = list(DEVICES) if self.present else DEVICES[:3]
        if index is None:
            return devs
        d = dict(devs[index])
        d["default_samplerate"] = 48000.0
        return d

    def OutputStream(self, **kwargs):  # noqa: N802 - mimics sounddevice
        s = FakeStream(self, **kwargs)
        self.streams.append(s)
        return s

    class WasapiSettings:
        def __init__(self, **kwargs):
            pass

    def _terminate(self):
        self.reinit += 1

    def _initialize(self):
        pass


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def keepalive():
    sd = FakeSd()
    ka = KeepAlive(tone_hz=10, tone_amp=0.05, device_match="HyperX", retry_s=0.2, sd_module=sd)
    ka.start()
    yield ka, sd
    ka.stop()


def test_keepalive_starts_plays_and_stops(keepalive):
    ka, sd = keepalive
    assert ka.state == "idle" and not sd.streams
    ka.set_wanted(True)
    assert wait_for(lambda: ka.state == "running")
    assert sd.streams[0].kwargs["device"] == 3
    assert wait_for(lambda: sd.last_block is not None and abs(sd.last_block).max() > 0)
    ka.set_wanted(False)
    assert wait_for(lambda: ka.state == "idle")
    assert sd.streams[0].closed


def test_keepalive_retries_when_the_device_is_missing(keepalive):
    ka, sd = keepalive
    sd.present = False
    ka.set_wanted(True)
    assert wait_for(lambda: ka.state == "retrying")
    assert not sd.streams
    sd.present = True                                  # headset dongle comes back
    assert wait_for(lambda: ka.state == "running", timeout=8)
    assert sd.reinit >= 1                              # the device list was refreshed


def test_keepalive_retries_when_opening_fails(keepalive):
    ka, sd = keepalive
    sd.fail_open = True
    ka.set_wanted(True)
    assert wait_for(lambda: ka.state == "retrying")
    sd.fail_open = False
    assert wait_for(lambda: ka.state == "running", timeout=8)


def test_keepalive_recovers_when_the_stream_dies(keepalive):
    ka, sd = keepalive
    ka.set_wanted(True)
    assert wait_for(lambda: ka.state == "running")
    sd.streams[0].active = False                       # device vanished under the stream
    assert wait_for(lambda: len(sd.streams) >= 2 and ka.state == "running", timeout=8)
    assert sd.streams[0].closed
