"""Automatic default audio device switching.

The USB dongle stays plugged in while the headset is off, so Windows never
switches the default playback / recording device by itself. When the headset
turns on, :class:`AudioSwitcher` remembers the current defaults and makes the
HyperX output and microphone the default (all three roles); when it turns off
it puts the remembered devices back, but only if the default is still the
HyperX (a device the user picked meanwhile is respected).

Layers:

* :class:`AudioSwitcher` - the decisions, pure apart from the injected
  backend and the small JSON state file. Fully testable with a fake backend.
* :class:`WindowsAudioBackend` - the COM layer (the only code that touches
  Windows). Windows has no public API to set the default endpoint, so this
  uses the well-known undocumented ``IPolicyConfig`` interface (as SoundSwitch
  and AudioDeviceCmdlets do) plus ``IMMDeviceEnumerator`` to read the defaults.
  The interfaces are declared by hand with ``comtypes`` low-level helpers: no
  type library, no generated wrappers, so nothing is written to disk at run
  time and it works unchanged inside a frozen (PyInstaller) exe.
* :class:`AudioService` - a worker thread that owns COM (initialised on that
  thread) so the tkinter main thread never blocks on audio calls.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

log = logging.getLogger(__name__)

STATE_FILE = "audio_state.json"
HEADSET_LABEL = "HyperX Cloud Flight"     # shown in the notification when switching to the headset

PLAYBACK = "playback"
CAPTURE = "capture"
FLOWS = (PLAYBACK, CAPTURE)


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Endpoint:
    id: str
    name: str


class AudioBackend:
    """What :class:`AudioSwitcher` needs from the OS (see WindowsAudioBackend)."""

    def default_endpoint(self, flow: str) -> Optional[Endpoint]:
        """Current default (console role) of ``flow``, or ``None``."""
        raise NotImplementedError

    def active_endpoints(self, flow: str) -> List[Endpoint]:
        """All active endpoints of ``flow``."""
        raise NotImplementedError

    def set_default(self, endpoint_id: str) -> None:
        """Make ``endpoint_id`` the default for all three roles."""
        raise NotImplementedError


def name_matches(name: str, match: str) -> bool:
    return match.lower() in name.lower()


# --------------------------------------------------------------------------
# Remembered devices (JSON state file)
# --------------------------------------------------------------------------
@dataclass
class SavedDevices:
    """The devices to go back to when the headset turns off."""

    playback: Optional[Endpoint] = None
    capture: Optional[Endpoint] = None

    def get(self, flow: str) -> Optional[Endpoint]:
        return self.playback if flow == PLAYBACK else self.capture

    def put(self, flow: str, endpoint: Optional[Endpoint]) -> None:
        if flow == PLAYBACK:
            self.playback = endpoint
        else:
            self.capture = endpoint

    def to_state(self) -> Dict[str, Any]:
        return {
            flow: ({"id": ep.id, "name": ep.name} if (ep := self.get(flow)) else None)
            for flow in FLOWS
        }

    @classmethod
    def from_state(cls, state: Any) -> "SavedDevices":
        saved = cls()
        if not isinstance(state, dict):
            return saved
        for flow in FLOWS:
            raw = state.get(flow)
            if (
                isinstance(raw, dict)
                and isinstance(raw.get("id"), str) and raw["id"]
                and isinstance(raw.get("name"), str)
            ):
                saved.put(flow, Endpoint(raw["id"], raw["name"]))
        return saved


def load_saved(path: Optional[Path]) -> SavedDevices:
    if path is None:
        return SavedDevices()
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return SavedDevices()
    except (OSError, ValueError):
        log.warning("Cannot read %s; nothing to restore", path, exc_info=True)
        return SavedDevices()
    return SavedDevices.from_state(state)


def save_saved(path: Optional[Path], saved: SavedDevices) -> None:
    """Atomic write (temp file + replace)."""
    if path is None:
        return
    tmp = Path(str(path) + ".tmp")
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(saved.to_state(), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        log.warning("Cannot save %s", path, exc_info=True)


# --------------------------------------------------------------------------
# Decision logic
# --------------------------------------------------------------------------
class AudioSwitcher:
    """Remember / switch / restore. Methods return the text of the single
    notification to show for the switch, or ``None`` if nothing changed."""

    def __init__(self, backend: AudioBackend, state_path: Optional[Path], match: str) -> None:
        self._backend = backend
        self._path = state_path
        self._match = match
        self.saved = load_saved(state_path)

    def _persist(self) -> None:
        save_saved(self._path, self.saved)

    def _find_headset(self, flow: str) -> Optional[Endpoint]:
        for ep in self._backend.active_endpoints(flow):
            if name_matches(ep.name, self._match):
                return ep
        return None

    def engage(self) -> Optional[str]:
        """Headset turned on: remember the current defaults, switch to the headset."""
        switched = False
        for flow in FLOWS:
            try:
                switched |= self._engage_flow(flow)
            except Exception:  # noqa: BLE001
                log.exception("Cannot switch the %s device to the headset", flow)
        self._persist()
        return f"Audio: {HEADSET_LABEL}" if switched else None

    def _engage_flow(self, flow: str) -> bool:
        headset = self._find_headset(flow)
        if headset is None:
            log.info("No active %s endpoint matches %r; leaving it alone", flow, self._match)
            return False
        current = self._backend.default_endpoint(flow)
        if current is not None and (current.id == headset.id or name_matches(current.name, self._match)):
            log.info("Default %s is already the headset (%s); keeping the saved device (%s)",
                     flow, current.name, self.saved.get(flow))
            return False
        if current is not None:
            self.saved.put(flow, current)
            self._persist()                 # before switching: survives a crash in between
            log.info("Remembering default %s: %s", flow, current.name)
        self._backend.set_default(headset.id)
        log.info("Default %s -> %s", flow, headset.name)
        return True

    def restore(self) -> Optional[str]:
        """Headset turned off: put the remembered devices back (if still sensible)."""
        restored: Dict[str, Endpoint] = {}
        for flow in FLOWS:
            try:
                target = self._restore_flow(flow)
            except Exception:  # noqa: BLE001
                log.exception("Cannot restore the %s device", flow)
                continue
            if target is not None:
                restored[flow] = target
        self._persist()
        if not restored:
            return None
        return f"Audio: {(restored.get(PLAYBACK) or restored[CAPTURE]).name}"

    def _restore_flow(self, flow: str) -> Optional[Endpoint]:
        target = self.saved.get(flow)
        if target is None:
            return None
        self.saved.put(flow, None)          # whatever happens below, this is consumed
        current = self._backend.default_endpoint(flow)
        if current is None or not name_matches(current.name, self._match):
            log.info("Default %s is %s, not the headset: leaving it as the user set it",
                     flow, current.name if current else "(none)")
            return None
        if not any(ep.id == target.id for ep in self._backend.active_endpoints(flow)):
            log.warning("Saved %s device %r no longer exists; leaving the default as it is",
                        flow, target.name)
            return None
        self._backend.set_default(target.id)
        log.info("Default %s restored -> %s", flow, target.name)
        return target


# --------------------------------------------------------------------------
# Worker thread
# --------------------------------------------------------------------------
class AudioService:
    """Runs :class:`AudioSwitcher` jobs on a daemon thread that owns COM.

    ``engage()`` / ``restore()`` only enqueue and return at once; the
    notification text (if any) is handed to ``on_result`` on the worker thread
    (the app passes a function that re-posts it to the main thread).
    ``backend_factory`` is called on the worker thread; the default builds the
    Windows backend and initialises COM there.
    """

    def __init__(
        self,
        state_path: Optional[Path],
        match: str,
        on_result: Callable[[str], None],
        backend_factory: Optional[Callable[[], AudioBackend]] = None,
    ) -> None:
        self._state_path = state_path
        self._match = match
        self._on_result = on_result
        self._factory = backend_factory
        self._jobs: "queue.Queue[Optional[str]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="audio-switch", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._jobs.put(None)
        if self._thread:
            self._thread.join(timeout)

    def engage(self) -> None:
        self._jobs.put("engage")

    def restore(self) -> None:
        self._jobs.put("restore")

    def _run(self) -> None:
        com = None
        switcher: Optional[AudioSwitcher] = None
        try:
            if self._factory is None:
                com = _ComApartment()
                factory: Callable[[], AudioBackend] = WindowsAudioBackend
            else:
                factory = self._factory
            while True:
                job = self._jobs.get()
                if job is None:
                    return
                try:
                    if switcher is None:
                        switcher = AudioSwitcher(factory(), self._state_path, self._match)
                    text = switcher.engage() if job == "engage" else switcher.restore()
                    if text:
                        self._on_result(text)
                except Exception:  # noqa: BLE001
                    log.exception("Audio job %r failed", job)
        finally:
            switcher = None
            if com is not None:
                com.close()


# --------------------------------------------------------------------------
# Windows / COM
# --------------------------------------------------------------------------
class _ComApartment:
    """CoInitializeEx on the current thread (multithreaded apartment)."""

    def __init__(self) -> None:
        import ctypes
        import sys

        # comtypes initialises COM on whichever thread first imports it (STA by
        # default). Ask for the same multithreaded apartment we use below, so the
        # two calls agree whatever thread imports it first.
        if not hasattr(sys, "coinit_flags"):
            sys.coinit_flags = 0                                        # COINIT_MULTITHREADED
        import comtypes  # noqa: F401

        self._ole32 = ctypes.OleDLL("ole32")
        hr = ctypes.c_long(self._ole32.CoInitializeEx(None, 0)).value   # COINIT_MULTITHREADED
        # S_OK / S_FALSE: initialised (must be balanced). RPC_E_CHANGED_MODE: the
        # thread already has an apartment of another kind, usable and not ours to undo.
        self._owned = hr in (0, 1)

    def close(self) -> None:
        import gc

        gc.collect()                         # release COM pointers while COM is alive
        if self._owned:
            self._owned = False
            self._ole32.CoUninitialize()


ROLES = (0, 1, 2)             # eConsole, eMultimedia, eCommunications
_FLOW_CODE = {PLAYBACK: 0, CAPTURE: 1}          # eRender, eCapture
_STATE_ACTIVE = 0x1
_CLSID_MM_DEVICE_ENUMERATOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
_CLSID_POLICY_CONFIG_CLIENT = "{870af99c-171d-4f9e-af0d-e63df40c2bc9}"
_IID_POLICY_CONFIG = "{f8679f50-850a-41cf-9c72-430f290290c8}"


def _declare_interfaces() -> Dict[str, Any]:
    """Build the COM interface classes (needs comtypes; imported lazily so the
    rest of the app and the tests work without touching COM)."""
    import ctypes
    from ctypes import HRESULT, POINTER, Structure, Union, c_ubyte, c_uint, c_ushort, c_void_p, c_wchar_p
    from ctypes import wintypes

    from comtypes import GUID, STDMETHOD, IUnknown

    class PROPERTYKEY(Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class _PVData(Union):
        _fields_ = [("pwszVal", c_wchar_p), ("pad", c_ubyte * 16)]

    class PROPVARIANT(Structure):
        _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort),
                    ("data", _PVData)]

    class IPropertyStore(IUnknown):
        _iid_ = GUID("{886d8eeb-8cf2-4446-8d02-cdba1dbdcf99}")
        _methods_ = [
            STDMETHOD(HRESULT, "GetCount", (POINTER(c_uint),)),
            STDMETHOD(HRESULT, "GetAt", (c_uint, POINTER(PROPERTYKEY))),
            STDMETHOD(HRESULT, "GetValue", (POINTER(PROPERTYKEY), POINTER(PROPVARIANT))),
            STDMETHOD(HRESULT, "SetValue", (POINTER(PROPERTYKEY), POINTER(PROPVARIANT))),
            STDMETHOD(HRESULT, "Commit", ()),
        ]

    class IMMDevice(IUnknown):
        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
        _methods_ = [
            STDMETHOD(HRESULT, "Activate", (POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p))),
            STDMETHOD(HRESULT, "OpenPropertyStore", (wintypes.DWORD, POINTER(POINTER(IPropertyStore)))),
            STDMETHOD(HRESULT, "GetId", (POINTER(c_void_p),)),
            STDMETHOD(HRESULT, "GetState", (POINTER(wintypes.DWORD),)),
        ]

    class IMMDeviceCollection(IUnknown):
        _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
        _methods_ = [
            STDMETHOD(HRESULT, "GetCount", (POINTER(c_uint),)),
            STDMETHOD(HRESULT, "Item", (c_uint, POINTER(POINTER(IMMDevice)))),
        ]

    class IMMDeviceEnumerator(IUnknown):
        _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        _methods_ = [
            STDMETHOD(HRESULT, "EnumAudioEndpoints",
                      (c_uint, wintypes.DWORD, POINTER(POINTER(IMMDeviceCollection)))),
            STDMETHOD(HRESULT, "GetDefaultAudioEndpoint", (c_uint, c_uint, POINTER(POINTER(IMMDevice)))),
            STDMETHOD(HRESULT, "GetDevice", (c_wchar_p, POINTER(POINTER(IMMDevice)))),
            STDMETHOD(HRESULT, "RegisterEndpointNotificationCallback", (c_void_p,)),
            STDMETHOD(HRESULT, "UnregisterEndpointNotificationCallback", (c_void_p,)),
        ]

    class IPolicyConfig(IUnknown):
        """Undocumented; only SetDefaultEndpoint is ever called, the others
        just keep the vtable slots in order."""

        _iid_ = GUID(_IID_POLICY_CONFIG)
        _methods_ = [
            STDMETHOD(HRESULT, "GetMixFormat", (c_wchar_p, c_void_p)),
            STDMETHOD(HRESULT, "GetDeviceFormat", (c_wchar_p, ctypes.c_int, c_void_p)),
            STDMETHOD(HRESULT, "ResetDeviceFormat", (c_wchar_p,)),
            STDMETHOD(HRESULT, "SetDeviceFormat", (c_wchar_p, c_void_p, c_void_p)),
            STDMETHOD(HRESULT, "GetProcessingPeriod", (c_wchar_p, ctypes.c_int, c_void_p, c_void_p)),
            STDMETHOD(HRESULT, "SetProcessingPeriod", (c_wchar_p, c_void_p)),
            STDMETHOD(HRESULT, "GetShareMode", (c_wchar_p, c_void_p)),
            STDMETHOD(HRESULT, "SetShareMode", (c_wchar_p, c_void_p)),
            STDMETHOD(HRESULT, "GetPropertyValue", (c_wchar_p, POINTER(PROPERTYKEY), POINTER(PROPVARIANT))),
            STDMETHOD(HRESULT, "SetPropertyValue", (c_wchar_p, POINTER(PROPERTYKEY), POINTER(PROPVARIANT))),
            STDMETHOD(HRESULT, "SetDefaultEndpoint", (c_wchar_p, c_uint)),
            STDMETHOD(HRESULT, "SetEndpointVisibility", (c_wchar_p, ctypes.c_int)),
        ]

    return {
        "PROPERTYKEY": PROPERTYKEY, "PROPVARIANT": PROPVARIANT, "GUID": GUID,
        "IPropertyStore": IPropertyStore, "IMMDevice": IMMDevice,
        "IMMDeviceCollection": IMMDeviceCollection,
        "IMMDeviceEnumerator": IMMDeviceEnumerator, "IPolicyConfig": IPolicyConfig,
    }


class WindowsAudioBackend(AudioBackend):
    """Real backend. Must be created and used on a thread with COM initialised
    (:class:`AudioService` takes care of that)."""

    def __init__(self) -> None:
        import ctypes

        self._x = _declare_interfaces()
        self._ole32 = ctypes.OleDLL("ole32")
        self._ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        self._ole32.CoTaskMemFree.restype = None
        self._ole32.PropVariantClear.argtypes = [ctypes.c_void_p]

    # -- COM helpers ---------------------------------------------------------------
    def _enumerator(self) -> Any:
        from comtypes import CLSCTX_ALL, CoCreateInstance

        x = self._x
        return CoCreateInstance(x["GUID"](_CLSID_MM_DEVICE_ENUMERATOR), x["IMMDeviceEnumerator"], CLSCTX_ALL)

    def _device_info(self, device: Any) -> Endpoint:
        import ctypes
        from ctypes import byref, c_void_p

        x = self._x
        raw = c_void_p()
        device.GetId(byref(raw))
        try:
            dev_id = ctypes.wstring_at(raw.value)
        finally:
            self._ole32.CoTaskMemFree(raw)
        name = dev_id
        try:
            store = ctypes.POINTER(x["IPropertyStore"])()
            device.OpenPropertyStore(0, byref(store))          # STGM_READ
            key = x["PROPERTYKEY"](x["GUID"]("{a45c254e-df1c-4efd-8020-67d146a850e0}"), 14)  # PKEY_Device_FriendlyName
            pv = x["PROPVARIANT"]()
            store.GetValue(byref(key), byref(pv))
            try:
                if pv.vt == 31 and pv.data.pwszVal:            # VT_LPWSTR
                    name = pv.data.pwszVal
            finally:
                self._ole32.PropVariantClear(byref(pv))
        except Exception:  # noqa: BLE001
            log.debug("Cannot read the friendly name of %s", dev_id, exc_info=True)
        return Endpoint(dev_id, name)

    # -- AudioBackend ----------------------------------------------------------------
    def default_endpoint(self, flow: str) -> Optional[Endpoint]:
        from ctypes import POINTER, byref

        from comtypes import COMError

        enum = self._enumerator()
        device = POINTER(self._x["IMMDevice"])()
        try:
            enum.GetDefaultAudioEndpoint(_FLOW_CODE[flow], 0, byref(device))
        except COMError:                         # E_NOTFOUND: no device of that kind
            return None
        return self._device_info(device)

    def active_endpoints(self, flow: str) -> List[Endpoint]:
        from ctypes import POINTER, byref, c_uint

        enum = self._enumerator()
        coll = POINTER(self._x["IMMDeviceCollection"])()
        enum.EnumAudioEndpoints(_FLOW_CODE[flow], _STATE_ACTIVE, byref(coll))
        count = c_uint()
        coll.GetCount(byref(count))
        found: List[Endpoint] = []
        for i in range(count.value):
            device = POINTER(self._x["IMMDevice"])()
            coll.Item(i, byref(device))
            found.append(self._device_info(device))
        return found

    def set_default(self, endpoint_id: str) -> None:
        from comtypes import CLSCTX_ALL, CoCreateInstance

        x = self._x
        policy = CoCreateInstance(x["GUID"](_CLSID_POLICY_CONFIG_CLIENT), x["IPolicyConfig"], CLSCTX_ALL)
        for role in ROLES:
            policy.SetDefaultEndpoint(endpoint_id, role)
