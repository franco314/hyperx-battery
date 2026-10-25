import threading
import time

from hyperx_battery import session
from hyperx_battery.session import SessionMonitor, SessionSnapshot


def test_real_api_calls_do_not_raise_and_are_sane():
    snap = session.read_session()
    assert isinstance(snap.locked, bool)
    assert 0.0 <= snap.idle_s < 10 * 365 * 86400
    idle = session.idle_seconds()
    assert idle is not None and idle >= 0


def test_wts_struct_layout_matches_the_documented_size():
    # WTSINFOEXW is 232 bytes on 64-bit Windows (the API returned exactly this)
    import ctypes

    assert ctypes.sizeof(session._WTSINFOEX_W) == 232


def test_monitor_samples_periodically_and_stops():
    seen = []
    snaps = iter([SessionSnapshot(False, 1.0), SessionSnapshot(True, 2.0)] + [SessionSnapshot(True, 3.0)] * 100)
    done = threading.Event()

    def on_sample(s):
        seen.append(s)
        if len(seen) >= 3:
            done.set()

    mon = SessionMonitor(on_sample, interval_s=0.02, reader=lambda: next(snaps))
    mon.start()
    assert done.wait(3)
    mon.stop()
    n = len(seen)
    time.sleep(0.1)
    assert len(seen) == n                              # really stopped
    assert [s.locked for s in seen[:3]] == [False, True, True]


def test_monitor_survives_a_failing_reader():
    calls = []

    def reader():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return SessionSnapshot(False, 0.0)

    seen = []
    mon = SessionMonitor(seen.append, interval_s=0.02, reader=reader)
    mon.start()
    deadline = time.time() + 3
    while not seen and time.time() < deadline:
        time.sleep(0.01)
    mon.stop()
    assert seen
