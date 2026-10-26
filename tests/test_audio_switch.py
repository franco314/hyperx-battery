"""Audio switching decisions against a fake backend (no COM, no sound devices)."""
import json
import threading
import time

from hyperx_battery import audio_switch as asw
from hyperx_battery.audio_switch import (
    AudioService,
    AudioSwitcher,
    Endpoint,
    SavedDevices,
    load_saved,
    save_saved,
)

MATCH = "HyperX Cloud Flight"
REALTEK = Endpoint("out-realtek", "Altavoces (Realtek(R) Audio)")
LG = Endpoint("out-lg", "1 - LG ULTRAGEAR (AMD High Definition Audio Device)")
HX_OUT = Endpoint("out-hyperx", "Altavoces (HyperX Cloud Flight for PS)")
BRIO = Endpoint("in-brio", "Micrófono (Brio 100)")
HX_MIC = Endpoint("in-hyperx", "Micrófono (HyperX Cloud Flight for PS)")


class FakeBackend(asw.AudioBackend):
    def __init__(self):
        self.endpoints = {
            asw.PLAYBACK: [LG, HX_OUT, REALTEK],
            asw.CAPTURE: [BRIO, HX_MIC],
        }
        self.defaults = {asw.PLAYBACK: REALTEK, asw.CAPTURE: BRIO}
        self.calls = []

    def default_endpoint(self, flow):
        return self.defaults.get(flow)

    def active_endpoints(self, flow):
        return list(self.endpoints[flow])

    def set_default(self, endpoint_id):
        self.calls.append(endpoint_id)
        for flow, eps in self.endpoints.items():
            for ep in eps:
                if ep.id == endpoint_id:
                    self.defaults[flow] = ep
                    return
        raise OSError("unknown endpoint")

    def user_picks(self, flow, ep):
        self.defaults[flow] = ep


def make(tmp_path, backend=None):
    backend = backend or FakeBackend()
    path = tmp_path / "audio_state.json"
    return backend, AudioSwitcher(backend, path, MATCH), path


# -- on / off sequences --------------------------------------------------------------
def test_on_remembers_defaults_and_switches_both_flows(tmp_path):
    b, sw, path = make(tmp_path)
    text = sw.engage()
    assert text == "Audio: HyperX Cloud Flight"
    assert b.defaults == {asw.PLAYBACK: HX_OUT, asw.CAPTURE: HX_MIC}
    assert sw.saved == SavedDevices(REALTEK, BRIO)
    assert load_saved(path) == SavedDevices(REALTEK, BRIO)


def test_off_restores_the_remembered_devices_and_forgets_them(tmp_path):
    b, sw, path = make(tmp_path)
    sw.engage()
    text = sw.restore()
    assert text == "Audio: Altavoces (Realtek(R) Audio)"           # one toast, named after the output
    assert b.defaults == {asw.PLAYBACK: REALTEK, asw.CAPTURE: BRIO}
    assert sw.saved == SavedDevices()
    assert load_saved(path) == SavedDevices()
    assert sw.restore() is None                                    # nothing left to do


def test_full_cycle_twice(tmp_path):
    b, sw, _ = make(tmp_path)
    for _ in range(2):
        assert sw.engage() is not None
        assert sw.restore() is not None
    assert b.defaults == {asw.PLAYBACK: REALTEK, asw.CAPTURE: BRIO}


def test_already_headset_remembers_nothing_new(tmp_path):
    b, sw, _ = make(tmp_path)
    b.defaults = {asw.PLAYBACK: HX_OUT, asw.CAPTURE: HX_MIC}
    assert sw.engage() is None                                     # nothing switched: no toast
    assert b.calls == []
    assert sw.saved == SavedDevices()
    assert sw.restore() is None
    assert b.defaults == {asw.PLAYBACK: HX_OUT, asw.CAPTURE: HX_MIC}


def test_on_again_while_headset_is_default_keeps_the_saved_devices(tmp_path):
    b, sw, _ = make(tmp_path)
    sw.engage()
    b.calls.clear()
    assert sw.engage() is None                                     # headset blipped: still the default
    assert b.calls == []
    assert sw.saved == SavedDevices(REALTEK, BRIO)


def test_only_one_flow_needs_switching(tmp_path):
    b, sw, _ = make(tmp_path)
    b.defaults[asw.CAPTURE] = HX_MIC
    assert sw.engage() == "Audio: HyperX Cloud Flight"
    assert sw.saved == SavedDevices(REALTEK, None)
    assert sw.restore() == "Audio: Altavoces (Realtek(R) Audio)"
    assert b.defaults == {asw.PLAYBACK: REALTEK, asw.CAPTURE: HX_MIC}      # the mic was never ours to move


def test_restoring_only_the_mic_names_the_mic_in_the_toast(tmp_path):
    b, sw, _ = make(tmp_path)
    b.defaults[asw.PLAYBACK] = HX_OUT
    sw.engage()
    assert sw.restore() == "Audio: Micrófono (Brio 100)"


def test_set_default_is_called_once_per_endpoint(tmp_path):
    b, sw, _ = make(tmp_path)
    sw.engage()
    assert b.calls == [HX_OUT.id, HX_MIC.id]                        # the backend sets all three roles itself
    b.calls.clear()
    sw.restore()
    assert b.calls == [REALTEK.id, BRIO.id]


# -- respecting the user ------------------------------------------------------------------
def test_manual_change_while_on_is_respected(tmp_path):
    b, sw, _ = make(tmp_path)
    sw.engage()
    b.user_picks(asw.PLAYBACK, LG)                                  # the user chose another output
    text = sw.restore()
    assert b.defaults[asw.PLAYBACK] == LG                           # untouched
    assert b.defaults[asw.CAPTURE] == BRIO                          # the mic was still ours: restored
    assert text == "Audio: Micrófono (Brio 100)"
    assert sw.saved == SavedDevices()                               # the stale memory is dropped


def test_manual_change_of_everything_means_no_action_and_no_toast(tmp_path):
    b, sw, _ = make(tmp_path)
    sw.engage()
    b.user_picks(asw.PLAYBACK, LG)
    b.user_picks(asw.CAPTURE, BRIO)
    b.calls.clear()
    assert sw.restore() is None
    assert b.calls == []


def test_missing_remembered_device_leaves_things_as_they_are(tmp_path, caplog):
    b, sw, _ = make(tmp_path)
    sw.engage()
    b.endpoints[asw.PLAYBACK].remove(REALTEK)                       # unplugged / disabled meanwhile
    with caplog.at_level("WARNING"):
        text = sw.restore()
    assert b.defaults[asw.PLAYBACK] == HX_OUT                       # left alone
    assert b.defaults[asw.CAPTURE] == BRIO                          # the other flow is independent
    assert text == "Audio: Micrófono (Brio 100)"
    assert "no longer exists" in caplog.text
    assert sw.saved == SavedDevices()


def test_headset_endpoint_missing_means_no_switch(tmp_path):
    b, sw, _ = make(tmp_path)
    b.endpoints[asw.PLAYBACK].remove(HX_OUT)
    b.endpoints[asw.CAPTURE].remove(HX_MIC)
    assert sw.engage() is None
    assert b.calls == [] and sw.saved == SavedDevices()


def test_the_match_string_is_configurable(tmp_path):
    b = FakeBackend()
    sw = AudioSwitcher(b, tmp_path / "x.json", "Brio")
    b.defaults[asw.CAPTURE] = HX_MIC
    sw.engage()
    assert b.defaults[asw.CAPTURE] == BRIO and b.defaults[asw.PLAYBACK] == REALTEK


def test_backend_error_on_one_flow_does_not_stop_the_other(tmp_path):
    class Flaky(FakeBackend):
        def set_default(self, endpoint_id):
            if endpoint_id == HX_OUT.id:
                raise OSError("boom")
            super().set_default(endpoint_id)

    b, sw, _ = make(tmp_path, Flaky())
    assert sw.engage() == "Audio: HyperX Cloud Flight"
    assert b.defaults == {asw.PLAYBACK: REALTEK, asw.CAPTURE: HX_MIC}


# -- persistence ------------------------------------------------------------------------------
def test_restart_while_on_still_restores(tmp_path):
    b, sw, path = make(tmp_path)
    sw.engage()
    sw2 = AudioSwitcher(b, path, MATCH)                             # new process, same state file
    assert sw2.saved == SavedDevices(REALTEK, BRIO)
    assert sw2.engage() is None                                     # startup with the headset on: keeps the memory
    assert sw2.restore() == "Audio: Altavoces (Realtek(R) Audio)"
    assert b.defaults == {asw.PLAYBACK: REALTEK, asw.CAPTURE: BRIO}


def test_state_round_trip(tmp_path):
    path = tmp_path / "s.json"
    saved = SavedDevices(Endpoint("a", "Ñandú"), None)
    save_saved(path, saved)
    assert load_saved(path) == saved
    assert not (tmp_path / "s.json.tmp").exists()                   # atomic write leaves no temp file


def test_corrupt_or_odd_state_means_defaults(tmp_path):
    path = tmp_path / "s.json"
    assert load_saved(path) == SavedDevices()                       # missing
    path.write_text("{not json", encoding="utf-8")
    assert load_saved(path) == SavedDevices()
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_saved(path) == SavedDevices()
    path.write_text(json.dumps({"playback": {"id": 5, "name": "x"}, "capture": {"id": "c", "name": "m"}}),
                    encoding="utf-8")
    assert load_saved(path) == SavedDevices(None, Endpoint("c", "m"))
    assert load_saved(None) == SavedDevices()


# -- worker thread ------------------------------------------------------------------------------
def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_service_runs_jobs_on_its_own_thread_and_reports_once_per_switch(tmp_path):
    b = FakeBackend()
    threads, texts = [], []
    original = b.set_default

    def spy(endpoint_id):
        threads.append(threading.current_thread().name)
        original(endpoint_id)

    b.set_default = spy
    svc = AudioService(tmp_path / "s.json", MATCH, texts.append, backend_factory=lambda: b)
    svc.start()
    try:
        svc.engage()
        svc.restore()
        assert wait_for(lambda: len(texts) == 2)
    finally:
        svc.stop()
    assert texts == ["Audio: HyperX Cloud Flight", "Audio: Altavoces (Realtek(R) Audio)"]
    assert set(threads) == {"audio-switch"}
    assert b.defaults == {asw.PLAYBACK: REALTEK, asw.CAPTURE: BRIO}


def test_service_survives_a_failing_backend(tmp_path):
    calls = []

    def factory():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("COM not ready")
        return FakeBackend()

    texts = []
    svc = AudioService(tmp_path / "s.json", MATCH, texts.append, backend_factory=factory)
    svc.start()
    try:
        svc.engage()                                                # fails (factory raises)
        svc.engage()                                                # retried with a new backend
        assert wait_for(lambda: texts == ["Audio: HyperX Cloud Flight"])
    finally:
        svc.stop()


def test_com_layer_never_uses_comtypes_client():
    # comtypes.client generates wrapper modules on disk at run time, which a
    # frozen exe cannot do; the interfaces are declared by hand instead.
    src = open(asw.__file__, encoding="utf-8").read()
    assert "import comtypes.client" not in src and "from comtypes.client" not in src


def test_hand_declared_com_interfaces_have_the_expected_vtable():
    x = asw._declare_interfaces()
    names = [m[1] for m in x["IPolicyConfig"]._methods_]
    assert names.index("SetDefaultEndpoint") == 10                  # 11th slot after IUnknown
    assert str(x["IPolicyConfig"]._iid_).lower() == "{f8679f50-850a-41cf-9c72-430f290290c8}"
    assert [m[1] for m in x["IMMDeviceEnumerator"]._methods_][:2] == [
        "EnumAudioEndpoints", "GetDefaultAudioEndpoint"]
