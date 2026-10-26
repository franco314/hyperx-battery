"""installer\\HyperXBattery.iss must agree with the Python code on the names it cannot import:
the single-instance mutex, the autostart Run value and the data folder."""
import re
from pathlib import Path

from hyperx_battery import __main__ as entry
from hyperx_battery import autostart, config

ROOT = Path(__file__).resolve().parent.parent
ISS = (ROOT / "installer" / "HyperXBattery.iss").read_text(encoding="utf-8-sig")


def define(name: str) -> str:
    m = re.search(rf'^#define\s+{name}\s+"([^"]*)"', ISS, re.M)
    assert m, f"#define {name} not found in the .iss"
    return m.group(1)


def registry_line() -> str:
    lines = [ln for ln in ISS.splitlines() if ln.startswith("Root: HKCU")]
    assert len(lines) == 1
    return lines[0]


def test_app_mutex_is_the_one_the_app_creates():
    assert define("AppMutex") == entry.MUTEX_NAME
    assert re.search(r"^AppMutex=\{#AppMutex\}$", ISS, re.M)


def test_run_value_is_what_autostart_writes():
    line = registry_line()
    assert re.search(r'Subkey: "([^"]+)"', line).group(1) == autostart.RUN_KEY
    assert re.search(r'ValueName: "([^"]+)"', line).group(1) == autostart.VALUE_NAME
    assert "uninsdeletevalue" in line and "Tasks: startup" in line

    # ValueData uses Inno's doubled quotes: """{app}\HyperXBattery.exe"""
    raw = re.search(r'ValueData: "((?:[^"]|"")*)"', line).group(1).replace('""', '"')
    fake_app = r"C:\Users\someone\AppData\Local\Programs\HyperX Battery"
    command = raw.replace("{#AppExe}", define("AppExe")).replace("{app}", fake_app)
    assert command == autostart.build_command(
        frozen=True, executable=fake_app + "\\" + define("AppExe")
    )


def test_exe_name_matches_the_build_script():
    build = (ROOT / "build.ps1").read_text(encoding="utf-8-sig")
    assert f"--name {Path(define('AppExe')).stem}" in build


def test_data_folder_prompt_targets_the_app_data_dir():
    assert "{userappdata}\\" + config.APP_NAME in ISS


def test_version_is_not_hardcoded():
    assert not re.search(r"^(AppVersion|VersionInfoVersion|OutputBaseFilename)=.*\d+\.\d+\.\d+", ISS, re.M)
    assert re.search(r"^AppVersion=\{#AppVersion\}$", ISS, re.M)
