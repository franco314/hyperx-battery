"""Windows session state via ctypes: locked workstation and user idle time.

Two independent signals are used for "locked":
  * ``WTSQuerySessionInformationW(WTSSessionInfoEx)``: ``SessionFlags``
    (0 = locked, 1 = unlocked; verified as documented on Windows 11).
  * ``OpenInputDesktop``: fails (access denied) while the secure "Winlogon"
    desktop is active, i.e. when the workstation is locked.
The session counts as locked if either says so. Any API failure is treated as
"not locked" so a failing probe can never switch the anti-auto-off off.

Self-test: ``python -m hyperx_battery.session`` prints both signals every second.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger(__name__)

WTS_CURRENT_SESSION = 0xFFFFFFFF
WTS_SESSION_INFO_EX = 25
WTS_SESSIONSTATE_LOCK = 0
WTS_SESSIONSTATE_UNLOCK = 1
DESKTOP_SWITCHDESKTOP = 0x0100


class _WTSINFOEX_LEVEL1_W(ctypes.Structure):
    _fields_ = [
        ("SessionId", wintypes.ULONG),
        ("SessionState", ctypes.c_int),
        ("SessionFlags", wintypes.LONG),
        ("WinStationName", wintypes.WCHAR * 33),
        ("UserName", wintypes.WCHAR * 21),
        ("DomainName", wintypes.WCHAR * 18),
        ("LogonTime", ctypes.c_longlong),
        ("ConnectTime", ctypes.c_longlong),
        ("DisconnectTime", ctypes.c_longlong),
        ("LastInputTime", ctypes.c_longlong),
        ("CurrentTime", ctypes.c_longlong),
        ("IncomingBytes", wintypes.DWORD),
        ("OutgoingBytes", wintypes.DWORD),
        ("IncomingFrames", wintypes.DWORD),
        ("OutgoingFrames", wintypes.DWORD),
        ("IncomingCompressedBytes", wintypes.DWORD),
        ("OutgoingCompressedBytes", wintypes.DWORD),
    ]


class _WTSINFOEX_W(ctypes.Structure):
    # The union that follows Level is 8-byte aligned (it holds 64-bit fields),
    # ctypes reproduces the padding automatically.
    _fields_ = [("Level", wintypes.DWORD), ("Data", _WTSINFOEX_LEVEL1_W)]


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

_wtsapi32.WTSQuerySessionInformationW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD),
]
_wtsapi32.WTSQuerySessionInformationW.restype = wintypes.BOOL
_wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]
_wtsapi32.WTSFreeMemory.restype = None
_user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_user32.OpenInputDesktop.restype = wintypes.HANDLE
_user32.CloseDesktop.argtypes = [wintypes.HANDLE]
_user32.CloseDesktop.restype = wintypes.BOOL
_user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
_user32.GetLastInputInfo.restype = wintypes.BOOL
_kernel32.GetTickCount.argtypes = []
_kernel32.GetTickCount.restype = wintypes.DWORD


def wts_session_locked() -> Optional[bool]:
    """Locked flag from WTSSessionInfoEx, or None if the query failed."""
    buf = ctypes.c_void_p()
    size = wintypes.DWORD()
    ok = _wtsapi32.WTSQuerySessionInformationW(
        None, WTS_CURRENT_SESSION, WTS_SESSION_INFO_EX, ctypes.byref(buf), ctypes.byref(size)
    )
    if not ok or not buf.value:
        return None
    try:
        if size.value < ctypes.sizeof(_WTSINFOEX_W):
            return None
        info = _WTSINFOEX_W.from_address(buf.value)
        if info.Level != 1:
            return None
        return info.Data.SessionFlags == WTS_SESSIONSTATE_LOCK
    finally:
        _wtsapi32.WTSFreeMemory(buf)


def input_desktop_locked() -> Optional[bool]:
    """True when the input desktop cannot be opened (secure desktop active)."""
    handle = _user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if handle:
        _user32.CloseDesktop(handle)
        return False
    return True


def idle_seconds() -> Optional[float]:
    """Seconds since the last keyboard / mouse input in this session."""
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    elapsed_ms = (_kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
    return elapsed_ms / 1000.0


@dataclass(frozen=True)
class SessionSnapshot:
    locked: bool
    idle_s: float
    wts_locked: Optional[bool] = None
    desktop_locked: Optional[bool] = None


def read_session() -> SessionSnapshot:
    """Sample both lock signals and the idle time. Never raises."""
    try:
        wts = wts_session_locked()
    except Exception:  # noqa: BLE001 - probe must never kill the poller
        log.debug("WTS lock query failed", exc_info=True)
        wts = None
    try:
        desk = input_desktop_locked()
    except Exception:  # noqa: BLE001
        log.debug("OpenInputDesktop failed", exc_info=True)
        desk = None
    try:
        idle = idle_seconds()
    except Exception:  # noqa: BLE001
        log.debug("GetLastInputInfo failed", exc_info=True)
        idle = None
    return SessionSnapshot(
        locked=bool(wts) or bool(desk),
        idle_s=idle if idle is not None else 0.0,
        wts_locked=wts,
        desktop_locked=desk,
    )


class SessionMonitor:
    """Daemon thread that samples the session every ``interval_s`` and calls
    ``on_sample(snapshot)`` (from that thread: the callback must only enqueue)."""

    def __init__(
        self,
        on_sample: Callable[[SessionSnapshot], None],
        interval_s: float = 5.0,
        reader: Callable[[], SessionSnapshot] = read_session,
    ) -> None:
        self._on_sample = on_sample
        self._interval = interval_s
        self._reader = reader
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_locked: Optional[bool] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="session-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                snap = self._reader()
                if snap.locked != self._last_locked:
                    log.info(
                        "Session %s (wts_locked=%s, desktop_locked=%s)",
                        "locked" if snap.locked else "unlocked",
                        snap.wts_locked, snap.desktop_locked,
                    )
                    self._last_locked = snap.locked
                self._on_sample(snap)
            except Exception:  # noqa: BLE001
                log.exception("Session sample failed")
            self._stop.wait(self._interval)


if __name__ == "__main__":  # manual lock / idle check
    print("Ctrl+C para salir. Bloquea la sesión (Win+L) y desbloquéala para ver el cambio.")
    try:
        while True:
            s = read_session()
            print(
                f"{time.strftime('%H:%M:%S')}  locked={s.locked}  "
                f"wts_locked={s.wts_locked}  desktop_locked={s.desktop_locked}  idle={s.idle_s:.1f}s",
                flush=True,
            )
            time.sleep(1)
    except KeyboardInterrupt:
        pass
