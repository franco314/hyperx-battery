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
USB_MIC = Endpoint("in-usb", "Micrófono (USB Podcast)")
BOTH = "Audio: Altavoces (Realtek(R) Audio) · Micrófono (Brio 100)"     # one toast naming both devices


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
    """Switcher without fixed "off" devices: the remember / restore behaviour."""
    backend = backend or FakeBackend()
    path = tmp_path / "audio_state.json"
    return backend, AudioSwitcher(backend, path, MATCH), path


def make_fixed(tmp_path, backend=None, playback="Realtek", capture="Brio"):
    """Switcher with fixed "off" devices (the app defaults)."""
    backend = backend or FakeBackend()
    path = tmp_path / "audio_state.json"
    return backend, AudioSwitcher(backend, path, MATCH, playback, capture), path


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
    assert text == BOTH                                            # one toast naming both devices
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


# -- fixed "off" devices ------------------------------------------------------------------------
def test_off_goes_to_the_fixed_devices_not_the_remembered_ones(tmp_path):
    b, sw, path = make_fixed(tmp_path)
    b.endpoints[asw.CAPTURE].append(USB_MIC)
    b.defaults = {asw.PLAYBACK: LG, asw.CAPTURE: USB_MIC}           # what the user had before the headset
    sw.engage()
    assert sw.saved == SavedDevices(LG, USB_MIC)                    # still remembered, as a fallback
    assert sw.restore() == BOTH
    assert b.defaults == {asw.PLAYBACK: REALTEK, asw.CAPTURE: BRIO}
    assert sw.saved == SavedDevices()
    assert load_saved(path) == SavedDevices()


def test_headset_already_default_on_on_still_ends_on_the_fixed_devices(tmp_path):
    b, sw, _ = make_fixed(tmp_path)
    b.defaults = {asw.PLAYBACK: HX_OUT, asw.CAPTURE: HX_MIC}
    assert sw.engage() is None                                      # nothing remembered: nothing to restore...
    assert sw.saved == SavedDevices()
    assert sw.restore() == BOTH                                     # ...but the fixed rule does not need it
    assert b.defaults == {asw.PLAYBACK: REALTEK, asw.CAPTURE: BRIO}


def test_only_the_mic_was_stuck_on_the_headset(tmp_path):
    # The real-life case: the HyperX mic stays the Windows default with the headset off.
    b, sw, _ = make_fixed(tmp_path)
    b.defaults[asw.CAPTURE] = HX_MIC
    assert sw.engage() == "Audio: HyperX Cloud Flight"
    assert sw.restore() == BOTH
    assert b.defaults == {asw.PLAYBACK: REALTEK, asw.CAPTURE: BRIO}


def test_startup_with_the_headset_off_fixes_a_stuck_headset_default(tmp_path):
    b, sw, _ = make_fixed(tmp_path)                                 # fresh process: no saved state
    b.defaults[asw.CAPTURE] = HX_MIC
    assert sw.restore() == "Audio: Micrófono (Brio 100)"            # only the flow that changed
    assert b.defaults == {asw.PLAYBACK: REALTEK, asw.CAPTURE: BRIO}
    assert b.calls == [BRIO.id]
    b.calls.clear()
    assert sw.restore() is None                                     # already fine: idempotent
    assert b.calls == []


def test_startup_with_the_headset_off_leaves_other_defaults_alone(tmp_path):
    b, sw, _ = make_fixed(tmp_path)
    b.defaults = {asw.PLAYBACK: LG, asw.CAPTURE: BRIO}              # nothing on the headset
    assert sw.restore() is None
    assert b.calls == []


def test_manual_non_headset_pick_is_respected_with_fixed_devices(tmp_path):
    b, sw, _ = make_fixed(tmp_path)
    sw.engage()
    b.user_picks(asw.PLAYBACK, LG)                                  # the user chose another output
    assert sw.restore() == "Audio: Micrófono (Brio 100)"
    assert b.defaults[asw.PLAYBACK] == LG                           # not forced to Realtek
    assert b.defaults[asw.CAPTURE] == BRIO


def test_manual_pick_of_every_device_means_no_action_with_fixed_devices(tmp_path):
    b, sw, _ = make_fixed(tmp_path)
    b.endpoints[asw.CAPTURE].append(USB_MIC)
    sw.engage()
    b.user_picks(asw.PLAYBACK, LG)
    b.user_picks(asw.CAPTURE, USB_MIC)
    b.calls.clear()
    assert sw.restore() is None
    assert b.calls == []
    assert b.defaults == {asw.PLAYBACK: LG, asw.CAPTURE: USB_MIC}


def test_missing_fixed_device_falls_back_to_the_remembered_one(tmp_path, caplog):
    b, sw, _ = make_fixed(tmp_path)
    b.endpoints[asw.CAPTURE].append(USB_MIC)
    b.defaults = {asw.PLAYBACK: LG, asw.CAPTURE: USB_MIC}
    sw.engage()
    b.endpoints[asw.CAPTURE].remove(BRIO)                           # the webcam is unplugged
    with caplog.at_level("WARNING"):
        text = sw.restore()
    assert b.defaults == {asw.PLAYBACK: REALTEK, asw.CAPTURE: USB_MIC}
    assert text == "Audio: Altavoces (Realtek(R) Audio) · Micrófono (USB Podcast)"
    assert "matches 'Brio'" in caplog.text and "remembered device" in caplog.text


def test_missing_fixed_device_and_nothing_remembered_leaves_the_default(tmp_path, caplog):
    b, sw, _ = make_fixed(tmp_path)
    b.defaults[asw.CAPTURE] = HX_MIC                                # nothing remembered for the mic
    sw.engage()
    b.endpoints[asw.CAPTURE].remove(BRIO)
    b.calls.clear()
    with caplog.at_level("WARNING"):
        text = sw.restore()
    assert b.defaults[asw.CAPTURE] == HX_MIC                        # left as it is
    assert text == "Audio: Altavoces (Realtek(R) Audio)"            # the other flow still switched
    assert b.calls == [REALTEK.id]
    assert "leaving the default as it is" in caplog.text


def test_missing_fixed_device_and_missing_remembered_device_leaves_the_default(tmp_path):
    b, sw, _ = make_fixed(tmp_path)
    b.endpoints[asw.CAPTURE].append(USB_MIC)
    b.defaults[asw.CAPTURE] = USB_MIC
    sw.engage()
    b.endpoints[asw.CAPTURE].remove(BRIO)
    b.endpoints[asw.CAPTURE].remove(USB_MIC)
    assert sw.restore() == "Audio: Altavoces (Realtek(R) Audio)"
    assert b.defaults[asw.CAPTURE] == HX_MIC


def test_empty_match_keeps_the_old_remember_restore_behaviour(tmp_path):
    b, sw, _ = make_fixed(tmp_path, playback="", capture="")
    b.defaults = {asw.PLAYBACK: LG, asw.CAPTURE: BRIO}
    sw.engage()
    assert sw.restore() == "Audio: 1 - LG ULTRAGEAR (AMD High Definition Audio Device) · Micrófono (Brio 100)"
    assert b.defaults == {asw.PLAYBACK: LG, asw.CAPTURE: BRIO}      # remembered, not Realtek


def test_empty_match_with_nothing_remembered_does_nothing(tmp_path):
    b, sw, _ = make_fixed(tmp_path, playback="", capture="  ")
    b.defaults = {asw.PLAYBACK: HX_OUT, asw.CAPTURE: HX_MIC}
    assert sw.engage() is None
    assert sw.restore() is None                                     # the old gap, by choice
    assert b.calls == [] and b.defaults == {asw.PLAYBACK: HX_OUT, asw.CAPTURE: HX_MIC}


def test_each_flow_decides_on_its_own_match(tmp_path):
    b, sw, _ = make_fixed(tmp_path, playback="", capture="Brio")     # output: remembered, mic: fixed
    b.endpoints[asw.CAPTURE].append(USB_MIC)
    b.defaults = {asw.PLAYBACK: LG, asw.CAPTURE: USB_MIC}
    sw.engage()
    assert sw.restore() == "Audio: 1 - LG ULTRAGEAR (AMD High Definition Audio Device) · Micrófono (Brio 100)"
    assert b.defaults == {asw.PLAYBACK: LG, asw.CAPTURE: BRIO}


def test_fixed_match_is_case_insensitive_and_never_picks_the_headset(tmp_path):
    b = FakeBackend()
    b.endpoints[asw.CAPTURE] = [HX_MIC, BRIO]                       # the headset mic comes first
    b.defaults = {asw.PLAYBACK: HX_OUT, asw.CAPTURE: HX_MIC}
    _, sw, _ = make_fixed(tmp_path, b, playback="REALTEK", capture="micr")   # "micr" fits both mics
    assert sw.restore() == BOTH
    assert b.defaults == {asw.PLAYBACK: REALTEK, asw.CAPTURE: BRIO}


def test_fixed_match_naming_only_the_headset_is_ignored(tmp_path):
    b, sw, _ = make_fixed(tmp_path, playback="HyperX", capture="HyperX")
    b.defaults = {asw.PLAYBACK: HX_OUT, asw.CAPTURE: HX_MIC}
    assert sw.restore() is None                                     # no usable target, nothing remembered
    assert b.calls == []


def test_toast_names_only_what_changed(tmp_path):
    b, sw, _ = make_fixed(tmp_path)
    b.defaults[asw.PLAYBACK] = HX_OUT
    assert sw.restore() == "Audio: Altavoces (Realtek(R) Audio)"
    b.defaults[asw.CAPTURE] = HX_MIC
    assert sw.restore() == "Audio: Micrófono (Brio 100)"
    b.defaults = {asw.PLAYBACK: HX_OUT, asw.CAPTURE: HX_MIC}
    assert sw.restore() == BOTH
    assert sw.restore() is None


def test_backend_error_on_one_flow_while_turning_off_does_not_stop_the_other(tmp_path):
    class Flaky(FakeBackend):
        def set_default(self, endpoint_id):
            if endpoint_id == REALTEK.id:
                raise OSError("boom")
            super().set_default(endpoint_id)

    b, sw, _ = make_fixed(tmp_path, Flaky())
    b.defaults = {asw.PLAYBACK: HX_OUT, asw.CAPTURE: HX_MIC}
    assert sw.restore() == "Audio: Micrófono (Brio 100)"
    assert b.defaults == {asw.PLAYBACK: HX_OUT, asw.CAPTURE: BRIO}


# -- persistence ------------------------------------------------------------------------------
def test_restart_while_on_still_restores(tmp_path):
    b, sw, path = make(tmp_path)
    sw.engage()
    sw2 = AudioSwitcher(b, path, MATCH)                             # new process, same state file
    assert sw2.saved == SavedDevices(REALTEK, BRIO)
    assert sw2.engage() is None                                     # startup with the headset on: keeps the memory
    assert sw2.restore() == BOTH
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
    assert texts == ["Audio: HyperX Cloud Flight", BOTH]
    assert set(threads) == {"audio-switch"}
    assert b.defaults == {asw.PLAYBACK: REALTEK, asw.CAPTURE: BRIO}


def test_service_passes_the_fixed_off_devices_to_the_switcher(tmp_path):
    b = FakeBackend()
    b.endpoints[asw.CAPTURE].append(USB_MIC)
    b.defaults = {asw.PLAYBACK: LG, asw.CAPTURE: USB_MIC}
    texts = []
    svc = AudioService(tmp_path / "s.json", MATCH, texts.append, backend_factory=lambda: b,
                       off_playback_match="Realtek", off_capture_match="Brio")
    svc.start()
    try:
        svc.engage()
        svc.restore()
        assert wait_for(lambda: len(texts) == 2)
    finally:
        svc.stop()
    assert texts == ["Audio: HyperX Cloud Flight", BOTH]
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
