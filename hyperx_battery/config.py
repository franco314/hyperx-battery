"""Configuration (dataclass + JSON with defaults) and app data locations."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

APP_NAME = "HyperXBattery"
DATA_DIR_ENV = "HYPERX_BATTERY_DATA_DIR"


def data_dir() -> Path:
    """``%APPDATA%\\HyperXBattery`` (override with HYPERX_BATTERY_DATA_DIR)."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    base = os.environ.get("APPDATA")
    if not base:
        try:
            base = str(Path.home() / "AppData" / "Roaming")
        except RuntimeError:                      # no home directory at all
            base = tempfile.gettempdir()
    return Path(base) / APP_NAME


# name -> (min, max) for numeric options; values are clamped into range.
_LIMITS: Dict[str, tuple] = {
    "poll_interval_s": (1.0, 600.0),
    "reply_timeout_s": (0.2, 30.0),
    "misses_to_off": (1, 20),
    "smoothing_window_s": (10.0, 3600.0),
    "history_interval_s": (10.0, 3600.0),
    "history_keep_days": (1, 3650),
    "tone_hz": (1.0, 40.0),
    "tone_amp": (0.001, 0.5),
    "idle_pause_min": (0.0, 24 * 60.0),
    "keepalive_retry_s": (1.0, 600.0),
    "session_poll_s": (1.0, 60.0),
    "auto_switch_off_confirm_s": (0.0, 600.0),
}

# Text options where an empty string is a valid value (it turns the feature off)
# instead of "invalid, use the default".
_ALLOW_EMPTY = frozenset({"off_playback_match", "off_capture_match"})


@dataclass
class Config:
    # Device polling (see PLAN section 3)
    poll_interval_s: float = 5.0
    reply_timeout_s: float = 2.0
    misses_to_off: int = 2
    # Battery smoothing / history
    smoothing_window_s: float = 120.0
    history_interval_s: float = 60.0
    history_keep_days: int = 365
    # Anti auto-off
    keepalive_enabled: bool = True
    tone_hz: float = 10.0
    tone_amp: float = 0.05
    idle_pause_min: float = 15.0          # 0 disables the idle pause
    audio_device_match: str = "HyperX"    # substring of the WASAPI output name
    keepalive_retry_s: float = 10.0
    session_poll_s: float = 5.0
    # Default audio device switching (see audio_switch.py)
    auto_switch_audio: bool = True
    auto_switch_device_match: str = "HyperX Cloud Flight"   # substring of the endpoint name
    auto_switch_off_confirm_s: float = 20.0                 # confirm an inferred "off" this long before restoring
    off_playback_match: str = "Realtek"   # headset off: default output = first endpoint containing this ("" = remembered one)
    off_capture_match: str = "Brio"       # headset off: default microphone = first endpoint containing this ("" = remembered one)
    # Notifications
    notify_low_battery: bool = True
    low_battery_thresholds: List[int] = field(default_factory=lambda: [20, 10])
    notify_mute: bool = True

    # -- (de)serialisation ---------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Config":
        """Build a Config from user JSON: unknown keys are ignored and
        invalid / out-of-range values fall back to the default or are clamped."""
        cfg = cls()
        for f in fields(cls):
            if f.name not in raw:
                continue
            default = (
                f.default if f.default is not MISSING else f.default_factory()  # type: ignore[misc]
            )
            value = _coerce(f.name, raw[f.name], default)
            setattr(cfg, f.name, value)
        return cfg

    @classmethod
    def load(cls, path: Path) -> "Config":
        """Load ``path``; create it with defaults if missing; keep a ``.bad``
        copy and fall back to defaults if it is corrupt."""
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            cfg = cls()
            cfg.save(path)
            return cfg
        except OSError:
            log.exception("Cannot read %s, using defaults", path)
            return cls()
        try:
            raw = json.loads(text)
            if not isinstance(raw, dict):
                raise ValueError("top level must be an object")
        except ValueError:
            log.exception("Corrupt config %s, using defaults", path)
            try:
                os.replace(path, path.with_name(path.name + ".bad"))
            except OSError:
                pass
            cfg = cls()
            cfg.save(path)
            return cfg
        return cls.from_dict(raw)

    def save(self, path: Path) -> None:
        """Atomic write (temp file + replace)."""
        path = Path(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)
                    fh.write("\n")
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError:
            log.exception("Cannot write %s", path)


def _coerce(name: str, value: Any, default: Any) -> Any:
    """Coerce ``value`` to the type of ``default``; return ``default`` if it
    cannot be done. Numeric values are clamped to ``_LIMITS``."""
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        log.warning("config %s: expected true/false, got %r; using default", name, value)
        return default
    if isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            log.warning("config %s: expected a number, got %r; using default", name, value)
            return default
        result: Any = int(value) if isinstance(default, int) else float(value)
        lo, hi = _LIMITS.get(name, (None, None))
        if lo is not None and result < lo:
            log.warning("config %s: %r below %r; clamped", name, value, lo)
            result = type(default)(lo)
        if hi is not None and result > hi:
            log.warning("config %s: %r above %r; clamped", name, value, hi)
            result = type(default)(hi)
        return result
    if isinstance(default, str):
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, str) and name in _ALLOW_EMPTY:
            return ""
        log.warning("config %s: expected non-empty text, got %r; using default", name, value)
        return default
    if isinstance(default, list):
        if isinstance(value, list) and all(
            isinstance(v, int) and not isinstance(v, bool) and 0 < v <= 100 for v in value
        ):
            return sorted(set(value), reverse=True)
        log.warning("config %s: expected a list of percentages, got %r; using default", name, value)
        return default
    return value


def config_path(directory: Optional[Path] = None) -> Path:
    return (directory or data_dir()) / "config.json"
