"""autostart against a fake `winreg` (the real registry is never touched)."""
from pathlib import Path

import pytest

from hyperx_battery import autostart


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}                       # (key_path, name) -> value
        self.keys = set()
        self.fail = False

    class _Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, root, path, reserved=0, access=0):  # noqa: N802
        if self.fail:
            raise PermissionError("denied")
        if path not in self.keys:
            raise FileNotFoundError(path)
        return self._Key(path)

    def CreateKeyEx(self, root, path, reserved=0, access=0):  # noqa: N802
        if self.fail:
            raise PermissionError("denied")
        self.keys.add(path)
        return self._Key(path)

    def SetValueEx(self, key, name, reserved, kind, value):  # noqa: N802
        self.values[(key.path, name)] = value

    def QueryValueEx(self, key, name):  # noqa: N802
        try:
            return self.values[(key.path, name)], self.REG_SZ
        except KeyError:
            raise FileNotFoundError(name) from None

    def DeleteValue(self, key, name):  # noqa: N802
        try:
            del self.values[(key.path, name)]
        except KeyError:
            raise FileNotFoundError(name) from None


@pytest.fixture
def reg(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setattr(autostart, "winreg", fake)
    return fake


def test_enable_creates_and_disable_removes_the_run_value(reg):
    assert not autostart.is_enabled()
    assert autostart.enable('"C:\\x\\app.exe"')
    assert autostart.is_enabled()
    assert reg.values[(autostart.RUN_KEY, autostart.VALUE_NAME)] == '"C:\\x\\app.exe"'
    assert autostart.disable()
    assert not autostart.is_enabled()
    assert (autostart.RUN_KEY, autostart.VALUE_NAME) not in reg.values


def test_run_key_is_the_documented_one():
    assert autostart.RUN_KEY == r"Software\Microsoft\Windows\CurrentVersion\Run"


def test_disable_when_absent_is_a_success(reg):
    assert autostart.disable()
    reg.keys.add(autostart.RUN_KEY)
    assert autostart.disable()


def test_set_enabled_toggles(reg):
    assert autostart.set_enabled(True) and autostart.is_enabled()
    assert autostart.set_enabled(False) and not autostart.is_enabled()


def test_registry_errors_are_reported_not_raised(reg):
    reg.fail = True
    assert autostart.enable("x") is False
    assert autostart.disable() is False


def test_command_for_the_frozen_exe():
    cmd = autostart.build_command(frozen=True, executable=r"C:\Apps\HyperXBattery.exe")
    assert cmd == r'"C:\Apps\HyperXBattery.exe"'


def test_command_from_source_uses_pythonw_and_the_launcher(tmp_path):
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_text("")
    (scripts / "pythonw.exe").write_text("")
    cmd = autostart.build_command(frozen=False, executable=str(scripts / "python.exe"), project_root=tmp_path)
    assert cmd == f'"{scripts / "pythonw.exe"}" "{tmp_path / "run_tray.py"}"'


def test_command_from_source_falls_back_to_the_project_venv(tmp_path):
    cmd = autostart.build_command(frozen=False, executable=str(tmp_path / "nowhere" / "python.exe"),
                                  project_root=tmp_path)
    assert str(tmp_path / ".venv" / "Scripts" / "pythonw.exe") in cmd
    assert cmd.endswith(f'"{tmp_path / "run_tray.py"}"')


def test_launcher_exists_in_the_project_root():
    assert (autostart.PROJECT_ROOT / autostart.LAUNCHER).is_file()
    assert Path(autostart.PROJECT_ROOT / "hyperx_battery" / "__main__.py").is_file()
