"""Anti-auto-off: plays a sub-audible 10 Hz tone on the HyperX output.

Verified on the real headset: digital silence does NOT prevent the automatic
power-off (~17 min), a 10 Hz sine at 5 % amplitude (float32, 48 kHz, stereo)
does. The stream is opened on the WASAPI output whose name contains
"HyperX" (it is not the default device), in shared mode so it mixes with
normal audio.

Pure helpers (decision, tone synthesis, device lookup) are separate from the
:class:`KeepAlive` thread so they can be unit-tested.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

log = logging.getLogger(__name__)

TWO_PI = 2.0 * math.pi
FADE_S = 0.15          # fade in / out to avoid clicks when starting / stopping


# --------------------------------------------------------------------------
# Pure: when should the tone play?
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class KeepAliveDecision:
    run: bool
    reason: str     # "active" | "disabled" | "headset_off" | "locked" | "idle"


def decide_keepalive(
    enabled: bool,
    headset_on: bool,
    session_locked: bool,
    idle_s: float,
    idle_pause_min: float,
) -> KeepAliveDecision:
    """Should the tone be playing right now?

    Paused while the manual switch is off, the headset is off, the Windows
    session is locked, or the user has had no keyboard / mouse input for
    ``idle_pause_min`` minutes (0 disables the idle pause) so Windows is
    free to sleep. Resumes by itself when the conditions go away.
    """
    if not enabled:
        return KeepAliveDecision(False, "disabled")
    if not headset_on:
        return KeepAliveDecision(False, "headset_off")
    if session_locked:
        return KeepAliveDecision(False, "locked")
    if idle_pause_min > 0 and idle_s >= idle_pause_min * 60.0:
        return KeepAliveDecision(False, "idle")
    return KeepAliveDecision(True, "active")


# --------------------------------------------------------------------------
# Pure: tone synthesis
# --------------------------------------------------------------------------
def ramp_gains(gain: float, target: float, frames: int, step: float) -> Tuple[np.ndarray, float]:
    """Per-sample gains moving linearly from ``gain`` towards ``target`` by
    ``step`` per sample, never overshooting. Returns (gains, final_gain)."""
    if gain == target:
        return np.full(frames, gain, dtype=np.float64), gain
    direction = 1.0 if target > gain else -1.0
    gains = gain + direction * step * np.arange(1, frames + 1, dtype=np.float64)
    gains = np.minimum(gains, target) if direction > 0 else np.maximum(gains, target)
    return gains, float(gains[-1])


def render_tone(
    frames: int,
    phase: float,
    *,
    hz: float,
    amp: float,
    sample_rate: float,
    channels: int,
    gains: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, float]:
    """One audio block of the sine tone: float32 array ``(frames, channels)``
    and the phase (radians) to continue from."""
    w = TWO_PI * hz / sample_rate
    wave = np.sin(phase + w * np.arange(frames, dtype=np.float64)) * amp
    if gains is not None:
        wave = wave * gains
    block = np.repeat(wave.astype(np.float32)[:, None], channels, axis=1)
    return block, (phase + w * frames) % TWO_PI


# --------------------------------------------------------------------------
# Pure: output device lookup
# --------------------------------------------------------------------------
def find_output_device(
    devices: Sequence[Dict[str, Any]], hostapis: Sequence[Dict[str, Any]], match: str
) -> Optional[int]:
    """Index of the first WASAPI output device whose name contains ``match``
    (case-insensitive), or ``None``."""
    wasapi = next((i for i, h in enumerate(hostapis) if "WASAPI" in h["name"]), None)
    if wasapi is None:
        return None
    needle = match.lower()
    for i, d in enumerate(devices):
        if d["hostapi"] == wasapi and d["max_output_channels"] > 0 and needle in d["name"].lower():
            return i
    return None


# --------------------------------------------------------------------------
# The stream owner thread
# --------------------------------------------------------------------------
class KeepAlive:
    """Owns the sounddevice stream on a daemon thread.

    ``set_wanted(True/False)`` is the only thing other threads call. While
    wanted, the thread keeps (re)opening the stream: if the device is missing
    or the stream dies it closes everything and retries every ``retry_s``,
    searching the device by name again each time.
    """

    def __init__(
        self,
        tone_hz: float = 10.0,
        tone_amp: float = 0.05,
        device_match: str = "HyperX",
        retry_s: float = 10.0,
        sd_module: Any = None,
    ) -> None:
        self.tone_hz = tone_hz
        self.tone_amp = tone_amp
        self.device_match = device_match
        self.retry_s = retry_s
        self._sd = sd_module
        self._wanted = False
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # state shared with the audio callback
        self._phase = 0.0
        self._gain = 0.0
        self._target = 0.0
        self._ramp_step = 0.0
        self._sample_rate = 48000.0
        self._channels = 2
        self._last_callback = 0.0
        self._state = "idle"       # idle | running | retrying

    # -- public API -----------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="keepalive", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def set_wanted(self, wanted: bool) -> None:
        if wanted != self._wanted:
            log.info("Keep-alive %s", "requested" if wanted else "released")
            self._wanted = wanted
            self._wake.set()

    # -- thread -----------------------------------------------------------------
    def _sounddevice(self) -> Any:
        if self._sd is None:
            import sounddevice  # imported lazily: PortAudio init is slow

            self._sd = sounddevice
        return self._sd

    def _run(self) -> None:
        stream = None
        next_attempt = 0.0
        failures = 0
        while not self._stop.is_set():
            try:
                if self._wanted:
                    if stream is None and time.monotonic() >= next_attempt:
                        stream = self._open(failures)
                        if stream is None:
                            failures += 1
                            self._state = "retrying"
                            next_attempt = time.monotonic() + self.retry_s
                        else:
                            if failures:
                                log.info("Keep-alive recovered after %d failed attempt(s)", failures)
                            failures = 0
                            self._state = "running"
                    elif stream is not None and not self._healthy(stream):
                        log.warning("Keep-alive stream died; closing and retrying")
                        self._close(stream, fade=False)
                        stream = None
                        self._state = "retrying"
                        next_attempt = time.monotonic() + self.retry_s
                elif stream is not None:
                    self._close(stream, fade=True)
                    stream = None
                    self._state = "idle"
                    failures = 0
                elif self._state != "idle":
                    self._state = "idle"
                    failures = 0
            except Exception:  # noqa: BLE001 - never let the thread die
                log.exception("Keep-alive loop error")
                if stream is not None:
                    self._close(stream, fade=False)
                    stream = None
                self._state = "retrying"
                next_attempt = time.monotonic() + self.retry_s
            self._wake.wait(1.0)
            self._wake.clear()
        if stream is not None:
            self._close(stream, fade=True)
        self._state = "idle"

    # -- stream management ------------------------------------------------------
    def _healthy(self, stream: Any) -> bool:
        try:
            if not stream.active:
                return False
        except Exception:  # noqa: BLE001
            return False
        # The callback should fire many times per second; silence for >3 s
        # means the device went away without the stream noticing.
        return time.monotonic() - self._last_callback < 3.0

    def _find_device(self, refresh: bool) -> Optional[int]:
        sd = self._sounddevice()
        if refresh:
            # PortAudio caches the device list; re-initialise to see devices
            # that appeared after start-up (only safe: no stream is open).
            try:
                sd._terminate()
                sd._initialize()
            except Exception:  # noqa: BLE001
                log.debug("PortAudio re-initialisation failed", exc_info=True)
        return find_output_device(list(sd.query_devices()), list(sd.query_hostapis()), self.device_match)

    def _open(self, failures: int) -> Any:
        sd = self._sounddevice()
        try:
            index = self._find_device(refresh=failures > 0)
        except Exception:  # noqa: BLE001
            log.exception("Cannot query audio devices")
            return None
        if index is None:
            if failures == 0 or failures % 6 == 0:
                log.warning("No WASAPI output containing %r found; will retry", self.device_match)
            return None
        info = sd.query_devices(index)
        channels = max(1, min(2, int(info["max_output_channels"])))
        default_sr = float(info.get("default_samplerate") or 48000.0)
        candidates: List[Tuple[float, Any]] = [(48000.0, None)]
        if default_sr != 48000.0:
            candidates.append((default_sr, None))
        try:
            candidates.append((48000.0, sd.WasapiSettings(auto_convert=True)))
        except Exception:  # noqa: BLE001
            pass

        last_error: Optional[BaseException] = None
        for sample_rate, extra in candidates:
            self._sample_rate = sample_rate
            self._channels = channels
            self._ramp_step = 1.0 / (FADE_S * sample_rate)
            self._gain = 0.0
            self._target = 1.0
            self._last_callback = time.monotonic()
            stream = None
            try:
                stream = sd.OutputStream(
                    device=index,
                    samplerate=sample_rate,
                    channels=channels,
                    dtype="float32",
                    latency="high",
                    callback=self._callback,
                    extra_settings=extra,
                )
                stream.start()
                log.info(
                    "Keep-alive started on %r (%.0f Hz, %d ch, %.1f Hz tone, amp %.3f)",
                    info["name"], sample_rate, channels, self.tone_hz, self.tone_amp,
                )
                return stream
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if stream is not None:
                    self._close(stream, fade=False)
        log.warning("Cannot open the HyperX audio output: %s", last_error)
        return None

    def _close(self, stream: Any, fade: bool) -> None:
        try:
            if fade and self._healthy(stream):
                self._target = 0.0
                deadline = time.monotonic() + 0.5
                while self._gain > 0.0 and time.monotonic() < deadline:
                    time.sleep(0.01)
        except Exception:  # noqa: BLE001
            pass
        for action in ("stop", "close"):
            try:
                getattr(stream, action)()
            except Exception:  # noqa: BLE001
                log.debug("stream.%s failed", action, exc_info=True)
        log.info("Keep-alive stopped")

    def _callback(self, outdata: np.ndarray, frames: int, _time: Any, status: Any) -> None:
        self._last_callback = time.monotonic()
        if status:
            log.debug("Keep-alive audio status: %s", status)
        gains, self._gain = ramp_gains(self._gain, self._target, frames, self._ramp_step)
        block, self._phase = render_tone(
            frames,
            self._phase,
            hz=self.tone_hz,
            amp=self.tone_amp,
            sample_rate=self._sample_rate,
            channels=outdata.shape[1],
            gains=gains,
        )
        outdata[:] = block
