"""Start with Windows: HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run."""

from __future__ import annotations

import logging
import sys
import winreg
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "HyperXBattery"

# Folder that contains the ``hyperx_battery`` package (the project root).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = "run_tray.py"


def build_command(
    frozen: Optional[bool] = None,
    executable: Optional[str] = None,
    project_root: Optional[Path] = None,
) -> str:
    """Command line stored in the Run key.

    * Packaged (PyInstaller): the .exe itself.
    * From source: the venv's ``pythonw.exe`` (no console window) running the
      ``run_tray.py`` launcher by absolute path. ``-m hyperx_battery`` would
      not work here: the Run key gives no working directory, so Python would
      not find the package.
    """
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    exe = executable or sys.executable
    root = project_root or PROJECT_ROOT
    if frozen:
        return f'"{exe}"'
    pythonw = Path(exe).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    return f'"{pythonw}" "{root / LAUNCHER}"'


def current_value() -> Optional[str]:
    """The stored command, or None if autostart is off."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value)
    except FileNotFoundError:
        return None
    except OSError:
        log.exception("Cannot read the Run key")
        return None


def is_enabled() -> bool:
    return current_value() is not None


def enable(command: Optional[str] = None) -> bool:
    """Create the Run value. Returns True on success."""
    cmd = command or build_command()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, cmd)
        log.info("Autostart enabled: %s", cmd)
        return True
    except OSError:
        log.exception("Cannot enable autostart")
        return False


def disable() -> bool:
    """Delete the Run value (already absent counts as success)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        log.info("Autostart disabled")
        return True
    except FileNotFoundError:
        return True
    except OSError:
        log.exception("Cannot disable autostart")
        return False


def set_enabled(enabled: bool) -> bool:
    return enable() if enabled else disable()
