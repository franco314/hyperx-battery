"""Entry point: ``python -m hyperx_battery`` (or the packaged .exe).

Wires everything together: single instance, logging, config, HID monitor,
session poller, keep-alive, history and tray. tkinter owns the main thread
(hidden root window, used for the history window and to pump the event
queue); pystray runs detached on its own thread.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import logging.handlers
import signal
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from typing import Any, List, Optional

from . import __version__
from .app import Controller
from .config import Config, config_path, data_dir
from .device import HeadsetMonitor, SimulatedMonitor
from .history import HistoryStore
from .history_window import HistoryWindow
from .keepalive import KeepAlive
from .session import SessionMonitor
from .tray import TrayUI

log = logging.getLogger("hyperx_battery")

MUTEX_NAME = "Local\\HyperXBatteryTray"
ERROR_ALREADY_EXISTS = 183
PUMP_MS = 100


# --------------------------------------------------------------------------
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="hyperx_battery",
        description="Batería del HyperX Cloud Flight for PS en la bandeja de Windows, con anti-apagado.",
    )
    p.add_argument("--debug-hid", action="store_true",
                   help="imprime en la consola los reportes HID crudos de las 4 colecciones")
    p.add_argument("--simulate-percent", type=float, metavar="N",
                   help="no usa el auricular: simula una batería de N %% (prueba íconos y avisos)")
    p.add_argument("--simulate-charging", action="store_true",
                   help="junto con --simulate-percent: simula una carga que empieza desde ese %% (prueba la estimación)")
    p.add_argument("--no-keepalive", action="store_true",
                   help="no reproduce el tono anti-apagado en esta ejecución")
    p.add_argument("--selftest", action="store_true",
                   help="comprueba que cargan todas las dependencias (HID, audio, Tk, pystray) y sale")
    p.add_argument("--run-seconds", type=float, metavar="S", help=argparse.SUPPRESS)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


# --------------------------------------------------------------------------
def acquire_single_instance(name: str = MUTEX_NAME) -> Optional[int]:
    """Create the named mutex. Returns its handle, or ``None`` if another
    instance already owns it. The handle must stay referenced while running."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, name)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    if error == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return handle


def release_single_instance(handle: Optional[int]) -> None:
    if handle:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(handle)


def enable_dpi_awareness() -> None:
    """Crisp tray icon and tkinter text on scaled displays (process-level)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def ensure_console() -> None:
    """The packaged exe has no console; --debug-hid needs one."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        ctypes.windll.kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout
    except Exception:  # noqa: BLE001
        pass


def setup_logging(directory: Path, console: bool) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("hyperx_battery")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(
        directory / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    root.addHandler(fh)
    if console and sys.stderr is not None:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
        ch.setLevel(logging.INFO)
        root.addHandler(ch)

    def excepthook(exc_type, exc, tb):  # noqa: ANN001
        log.critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    def thread_hook(args):  # noqa: ANN001
        log.critical("Uncaught exception in thread %s", args.thread.name if args.thread else "?",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_hook


# --------------------------------------------------------------------------
def selftest() -> int:
    """Load every runtime dependency and report to app.log / stdout.
    Exit code 0 = all fine. Used to validate the packaged .exe."""
    problems: List[str] = []

    def check(name: str, probe: Any) -> None:
        try:
            detail = probe()
            log.info("selftest %-10s OK  %s", name, detail)
        except Exception:  # noqa: BLE001
            log.exception("selftest %s FAILED", name)
            problems.append(name)

    def probe_hid() -> str:
        import hid

        found = hid.enumerate(0x0951, 0x1749)
        return f"{len(found)} HID collection(s) of the dongle"

    def probe_audio() -> str:
        import sounddevice as sd

        from .keepalive import find_output_device

        idx = find_output_device(list(sd.query_devices()), list(sd.query_hostapis()), "HyperX")
        return f"HyperX WASAPI output index: {idx}"

    def probe_icon() -> str:
        from .tray import IconSpec, GREEN, WHITE, render_icon, _load_font

        img = render_icon(IconSpec("percent", "85", GREEN, WHITE), 16)
        return f"icon {img.size}, font {getattr(_load_font(12), 'path', 'default')}"

    def probe_tk() -> str:
        root = tk.Tk()
        root.withdraw()
        version = root.tk.call("info", "patchlevel")
        root.destroy()
        return f"Tk {version}"

    def probe_pystray() -> str:
        import pystray

        return f"backend {pystray.Icon.__module__}"

    def probe_registry() -> str:
        from . import autostart

        return f"autostart currently {'on' if autostart.is_enabled() else 'off'}"

    check("hid", probe_hid)
    check("audio", probe_audio)
    check("icon", probe_icon)
    check("tk", probe_tk)
    check("pystray", probe_pystray)
    check("registry", probe_registry)
    print("selftest:", "FAILED " + ", ".join(problems) if problems else "OK", file=sys.stderr)
    return 1 if problems else 0


# --------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    simulated = args.simulate_percent is not None
    if args.debug_hid:
        ensure_console()

    directory = data_dir()
    setup_logging(directory, console=args.debug_hid or simulated)
    log.info("=== HyperX Battery %s starting (%s) ===", __version__,
             "frozen" if getattr(sys, "frozen", False) else "source")

    if args.selftest:
        return selftest()

    enable_dpi_awareness()
    mutex_name = MUTEX_NAME + ("Sim" if simulated else "")
    mutex = acquire_single_instance(mutex_name)
    if mutex is None:
        log.warning("Another instance is already running; exiting.")
        print("HyperX Battery ya está en ejecución (mira el ícono de la bandeja).", file=sys.stderr)
        return 0

    cfg = Config.load(config_path(directory))
    if args.no_keepalive:
        cfg.keepalive_enabled = False
    log.info("Config: %s", cfg.to_dict())

    store = HistoryStore(":memory:" if simulated else directory / "history.sqlite")
    if not simulated:
        try:
            store.prune(time.time() - cfg.history_keep_days * 86400)
        except Exception:  # noqa: BLE001
            log.exception("History prune failed")

    root = tk.Tk()
    root.withdraw()

    keepalive: Optional[KeepAlive] = None
    if not simulated:
        keepalive = KeepAlive(
            tone_hz=cfg.tone_hz,
            tone_amp=cfg.tone_amp,
            device_match=cfg.audio_device_match,
            retry_s=cfg.keepalive_retry_s,
        )

    controller: Controller
    history_window = HistoryWindow(
        root, store, gap_s=max(180.0, 3 * cfg.history_interval_s),
        extra_info=lambda: controller.view().remaining or "",
    )
    controller = Controller(
        cfg, config_path(directory), directory, store, keepalive,
        simulated=simulated,
        show_history=history_window.show,
        request_quit=root.quit,
    )
    tray = TrayUI(controller.tray_actions())
    controller.ui = tray

    session = SessionMonitor(controller.post, interval_s=cfg.session_poll_s)
    monitor: Any
    if simulated:
        monitor = SimulatedMonitor(controller.post, args.simulate_percent,
                                   cfg.poll_interval_s, charging=args.simulate_charging,
                                   precharge_readings=2)
        log.info("SIMULATION: battery fixed at %.0f %%", args.simulate_percent)
    else:
        monitor = HeadsetMonitor(
            controller.post,
            poll_interval_s=cfg.poll_interval_s,
            reply_timeout_s=cfg.reply_timeout_s,
            misses_to_off=cfg.misses_to_off,
            debug_hid=args.debug_hid,
        )

    def pump() -> None:
        try:
            controller.process_pending()
        except Exception:  # noqa: BLE001 - keep the UI loop alive no matter what
            log.exception("Event pump failed")
        finally:
            root.after(PUMP_MS, pump)

    def on_signal(_signum: int, _frame: Any) -> None:
        controller.post(root.quit)

    signal.signal(signal.SIGINT, on_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, on_signal)

    try:
        tray.start()
        controller.refresh()
        if keepalive is not None:
            keepalive.start()
        session.start()
        monitor.start()
        root.after(PUMP_MS, pump)
        if args.run_seconds:
            root.after(int(args.run_seconds * 1000), root.quit)
        root.mainloop()
    finally:
        log.info("Shutting down")
        for stopper in (monitor.stop, session.stop,
                        keepalive.stop if keepalive else (lambda: None)):
            try:
                stopper()
            except Exception:  # noqa: BLE001
                log.exception("Error while stopping a worker")
        controller.process_pending()
        controller.shutdown()
        tray.stop()
        store.close()
        try:
            root.destroy()
        except tk.TclError:
            pass
        release_single_instance(mutex)
        log.info("=== bye ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
