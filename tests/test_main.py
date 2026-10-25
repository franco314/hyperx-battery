import logging
import uuid

from hyperx_battery import __main__ as entry


def test_parse_args_defaults_and_flags():
    a = entry.parse_args([])
    assert not a.debug_hid and a.simulate_percent is None and not a.no_keepalive
    a = entry.parse_args(["--debug-hid", "--simulate-percent", "15", "--no-keepalive", "--simulate-charging"])
    assert a.debug_hid and a.simulate_percent == 15.0 and a.no_keepalive and a.simulate_charging


def test_single_instance_mutex():
    name = f"Local\\HyperXBatteryTest-{uuid.uuid4()}"
    first = entry.acquire_single_instance(name)
    assert first
    try:
        assert entry.acquire_single_instance(name) is None          # second instance is refused
    finally:
        entry.release_single_instance(first)
    again = entry.acquire_single_instance(name)                    # released: can be taken again
    assert again
    entry.release_single_instance(again)


def test_different_names_do_not_collide():
    a = entry.acquire_single_instance(f"Local\\HyperXBatteryTest-{uuid.uuid4()}")
    b = entry.acquire_single_instance(f"Local\\HyperXBatteryTest-{uuid.uuid4()}")
    assert a and b
    entry.release_single_instance(a)
    entry.release_single_instance(b)


def test_logging_goes_to_a_rotating_app_log(tmp_path, monkeypatch):
    import sys
    import threading

    monkeypatch.setattr(sys, "excepthook", sys.excepthook)             # restored on teardown
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    entry.setup_logging(tmp_path, console=False)
    try:
        logging.getLogger("hyperx_battery.test").info("hello log")
        logging.getLogger("hyperx_battery.hid").debug("raw debug line")
        for h in logging.getLogger("hyperx_battery").handlers:
            h.flush()
        text = (tmp_path / "app.log").read_text(encoding="utf-8")
        assert "hello log" in text and "raw debug line" in text      # DEBUG is kept in the file
    finally:
        for h in list(logging.getLogger("hyperx_battery").handlers):
            h.close()
            logging.getLogger("hyperx_battery").removeHandler(h)
