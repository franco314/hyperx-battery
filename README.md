# HyperX Battery

**English** · [Español](README.es.md)

*Unofficial — not affiliated with HyperX or HP.*

Windows 11 tray app that shows the battery level of the **HyperX Cloud Flight for PS** (USB dongle `0951:1749`) and keeps the headset from shutting itself off.

- **Tray icon** with the percentage (in 1% steps) on a colored background: green > 50%, yellow 20-50%, red < 20%, grey if the headset is off or the dongle is unplugged, blue lightning bolt if charging (with the estimated number in a faint light blue instead of the bolt, see "Charge estimation"). It is a rounded square in soft colors with the number in white (dark on the yellow), drawn at the exact tray size so it looks crisp; at 100% the number shrinks to fit, and `--` is shown if the headset is off. If the microphone is detected as muted, a red dot appears in the corner.
- **Keep-alive** (menu entry **Anti-apagado**): plays a 10 Hz tone at 5% (inaudible) on the HyperX output while the headset is on. It pauses while the session is locked or after N minutes without keyboard or mouse input.
- **Automatic default audio**: when the headset turns on, the HyperX output and microphone become the Windows defaults; when it turns off they go back to fixed devices (by default "Altavoces (Realtek(R) Audio)" (Speakers) and "Micrófono (Brio 100)" (Microphone), configurable) or, if those aren't available, to whatever was set before (see "Automatic audio switching").
- **Low-battery alerts** (20% and 10%, only once per discharge cycle).
- **History** in SQLite, estimated remaining time and a window with a 24 h / 7 day chart.
- **Start with Windows** from the menu.
- **Windows installer** per user (no administrator rights) or a portable `.exe`.

> **Note:** the app's interface (tray menu, notifications, tooltip) is currently in Spanish; code and identifiers are in English. In this README each Spanish label is shown in **bold** with its English meaning in parentheses, the first time and wherever it helps. The installer wizard is in Spanish or English depending on your Windows language.

## Compatibility

| Headset | USB dongle | Status |
|---|---|---|
| HyperX Cloud Flight Wireless **for PS4/PS5/PC** (PlayStation version) | `0951:1749` | ✅ Tested |
| HyperX Cloud Flight Wireless (PC version) | `0951:16C4`, `0951:1723` | ❌ Not supported: it uses a different ID and isn't detected |
| Other HyperX headsets (Cloud Flight S, Cloud II/III, Alpha Wireless…) | — | ❌ Not supported |

To find out which version you have: Device Manager → Sound, video and game controllers → the dongle should be called **"HyperX Cloud Flight for PS"**. If your model isn't listed, open an *issue* with your dongle's ID.

Windows only (tested on Windows 11).

## Installation

### With the installer (recommended)

1. Download `HyperXBattery-Setup-<version>.exe` from [Releases](https://github.com/franco314/hyperx-battery/releases), or build it yourself (see "Building the installer").
2. Open it and follow the wizard (in Spanish or English, depending on your Windows language). It installs **for your user only**: it asks for no administrator rights and shows no UAC prompt, and puts the program in `%LOCALAPPDATA%\Programs\HyperX Battery`. A Start menu shortcut is always created.
3. Wizard options:

| Option | Default |
|---|---|
| Desktop shortcut | unchecked |
| **Start with Windows** (writes the `Run` key, see "Start with Windows") | checked |
| **Open HyperX Battery** when finished | checked |

**SmartScreen warning**: the installer isn't digitally signed (a code-signing certificate costs money), so Windows may show "Windows protected your PC" the first time. Click **More info → Run anyway**. The installer is built from the script `installer\HyperXBattery.iss` in this repository, so you can review or compile it yourself.

**Updating**: run the newer installer without uninstalling anything; it replaces the old version and keeps your settings and history. If HyperX Battery is open, the installer asks whether to close it and closes it for you (first with a normal close and, if it doesn't respond within 5 s, by force); in a silent install it closes it without asking and reopens it when it finishes. The "Start with Windows" checkbox starts from the real state: if you turned it off from the tray menu, it appears unchecked.

**Uninstalling**: *Settings → Apps → Installed apps → HyperX Battery → Uninstall* (or `unins000.exe` in the install folder). It closes the app if it is running and removes the program, the shortcuts and the start-with-Windows key. At the end it asks whether you also want to delete your data from `%APPDATA%\HyperXBattery` (history and settings): the default answer is **No**, and a silent uninstall always keeps them.

**Silent install** (useful for automation):

```powershell
.\HyperXBattery-Setup-1.3.1.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART                       # with "Start with Windows"
.\HyperXBattery-Setup-1.3.1.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /MERGETASKS="!startup" # without "Start with Windows"
& "$env:LOCALAPPDATA\Programs\HyperX Battery\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES        # uninstalls (keeps the data)
```

### Portable version

The standalone `HyperXBattery.exe` also works without installing anything: put it in a permanent folder and open it. The "Start with Windows" key stores the absolute path of the `.exe`, so if you move it, uncheck and re-check the option. Don't use the portable and the installed versions at the same time: they share the single-instance mutex, the startup key and the data folder.

## Requirements to run from source or build

- Windows 11, Python 3.14 (the project's venv is in `.venv`).
- Runtime dependencies in `requirements.txt` (`hidapi`, `sounddevice`, `numpy`, `pystray`, `Pillow`, `comtypes`); development ones (`pytest`, `pyinstaller`) in `requirements-dev.txt`.

```powershell
python -m venv .venv                                  # first time only
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

## How to run it

```powershell
.\.venv\Scripts\python.exe -m hyperx_battery          # with a console (you see the messages if you use --debug-hid)
.\.venv\Scripts\pythonw.exe run_tray.py               # without a console
```

The generated `.exe` works too (see "Building the .exe and the installer"). Only **one instance** can run at a time (named mutex); if you launch another, it exits on its own.

The icon appears in the tray (sometimes inside the `^` hidden-icons menu: drag it onto the taskbar to keep it always visible).

### Tray menu (right-click)

| Entry | What it does |
|---|---|
| *Estado* (Status, grey) | `Batería: 85 % (~12 h restantes)` (Battery: 85% (~12 h remaining)), `Cargando… ~90 % (estimado) · lleno en ~25 min` (Charging… ~90% (estimated) · full in ~25 min), `Auricular apagado` (Headset off), `Dongle USB no conectado` (USB dongle not connected) |
| **Anti-apagado** (Keep-alive) ✓ | Turns the tone on / off. Saved in `config.json`. |
| **Cambiar audio automáticamente** (Switch audio automatically) ✓ | Turns on / off the switching of the default audio when the headset turns on / off (`auto_switch_audio`). Saved in `config.json`. |
| **Ver historial** (View history) | Opens the window with the chart (also with a left click on the icon). |
| **Iniciar con Windows** (Start with Windows) ✓ | Creates / deletes the `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` key. |
| **Abrir carpeta de datos** (Open data folder) | Opens `%APPDATA%\HyperXBattery\`. |
| **Salir** (Quit) | Closes everything. |

Icon tooltip: `HyperX Cloud Flight · 85 % · ~12 h restantes · Anti-apagado: activo` (… ~12 h remaining · Keep-alive: on); while charging: `HyperX Cloud Flight · cargando · ~90 % (estimado) · lleno en ~25 min · Anti-apagado: activo` (… charging · ~90% (estimated) · full in ~25 min · Keep-alive: on).

## How it works

- **Battery**: every 5 s the report `21 FF 05 00…` (20 bytes) is written to the HID collection `0xFF90`; the reply `21 FF 05 HI LO` carries the voltage in mV `(HI<<8)|LO`. The voltage is converted to a percentage with a lookup table (`battery.py`, constants `CURVE_MV` / `CURVE_PERCENT`, replaceable).
- **Smoothing**: the displayed percentage is the **median** of the readings over the last 2 min. While discharging, the number **only goes down**; it can go back up only after charging, after the headset has been off, or if the new value exceeds the displayed one by 3 points or more for 2 min in a row. In addition, so that ±2 mV of noise doesn't move the number, it only goes down when the smoothed value is clearly below it (1 point of margin) and one point at a time (`BatteryTracker(fall_margin, max_fall_step)`; with `fall_margin=0` it is the plain `min` rule). Consequence: the number can sit up to ~1 point above the "exact" value.
- **Charging**: `HI == 0x10 and LO >= 20` or `HI >= 0x11` (≥ 4116 mV). While charging, the voltage stays flat (~4.3 V) and there is no measured percentage: the icon shows the bolt or, if there is an estimate, the approximate number (in a faint light blue and without the bolt behind it, so it reads crisply). When charging ends, smoothing is reset and the icon goes back to the measured value.
- **Charge estimation**: when charging starts, the time and the last percentage shown before it are saved; the estimated percentage is `start + rate × hours`, capped at 99% (it never says 100%), and "full in" is `(100 − estimated) / rate`. The rate starts at 25 %/h (HyperX's specification says ~4.5 h for a full charge) and is **learned**: about 5 min after unplugging (once the surface charge dissipates) the percentage is measured and the actual rate of that charge is computed, then blended with the stored one (exponential moving average, weight 0.3, clamped to 10-60 %/h). Charges shorter than 10 min, absurd results, and charges that end at 99% or more while being slower than the current rate (it may simply have filled up) are ignored; if the headset turns off before the 5 min are up, nothing is learned. If the app starts while already charging, it uses the last percentage in the history if it is less than 12 h old; if there is no data, it just shows the bolt. The estimate is **not** stored in the history (those rows keep an empty percentage) and does not affect the low-battery alerts. State is kept in `charge_state.json` (the learned rate and the charge in progress, so they survive a restart; if the file is corrupt the defaults are used).
- **On / off**: the headset does **not** announce when it turns off, so it is detected because it stops answering: 2 consecutive polls without a reply (≈ 7-12 s) → grey. The first reply after that → it comes back on its own (≤ 5 s). If the dongle is unplugged, everything is closed and retried every 5 s.
- **Keep-alive**: opens a WASAPI `OutputStream` (shared mode, float32, 48 kHz, stereo) on the output whose name contains "HyperX" (found by name, not the Windows default output) with a 10 Hz sine at 5%. With digital silence the headset shuts off after ~17 min; with this tone, it doesn't. If the output disappears or the stream fails, it is closed and retried every 10 s, searching again by name. It fades in and out over 150 ms to avoid clicks.
  - **Pauses** (it resumes on its own when the condition clears): headset off, menu switch off, Windows session locked, or idle for ≥ `idle_pause_min` (15 by default; `0` disables it). This way Windows can go to sleep.
- **Automatic audio switching**: the dongle stays plugged in while the headset is off, so Windows never switches device on its own. When it detects that the headset **turns on**, the app saves the current default playback and recording devices (id and name) and sets as defaults, in all three roles (console, multimedia and communications), the output and the microphone whose name contains `auto_switch_device_match` (default `HyperX Cloud Flight`; among the active endpoints, "Altavoces (HyperX Cloud Flight for PS)" (Speakers) and "Micrófono (HyperX Cloud Flight for PS)" (Microphone)). When it **turns off**, the output and the microphone are handled separately and are only touched if the current default is still the HyperX (if you chose another one in the meantime, it leaves it alone):
  - **Fixed device on power-off**: if `off_playback_match` / `off_capture_match` are not empty (by default `"Realtek"` and `"Brio"`), the default moves to the first active endpoint of that type whose name contains the text (case-insensitive), in all three roles; with the default values: "Altavoces (Realtek(R) Audio)" (Speakers) and "Micrófono (Brio 100)" (Microphone), the one from the Logitech Brio camera. It never picks an endpoint of the HyperX itself.
  - **If there is no fixed device** (empty text, or that device isn't active, e.g. the camera is unplugged), it uses the one that was saved at power-on; if that doesn't exist either or nothing was saved, it leaves everything as it is and notes it in `app.log`.
  - With the text empty for one of the two, that output or microphone keeps working as before: it saves what was there at power-on and restores it at power-off. If the HyperX was already the default when it turned on, nothing new is saved (what was saved earlier is kept); with a fixed device this no longer matters, because at power-off it goes to the fixed one anyway.
  - **Notification**: one per switch, `Audio: HyperX Cloud Flight` at power-on and, at power-off, the devices that changed, e.g. `Audio: Altavoces (Realtek(R) Audio) · Micrófono (Brio 100)` (only one of the two if the other didn't change).
  - **Delayed power-off**: an explicit power-off (the headset's own `64 03` event) switches instantly; one inferred from unanswered polls (out of range, interference, dongle unplugged) must be confirmed for `auto_switch_off_confirm_s` (20 s) longer before switching, so that a brief dropout doesn't send the audio to the speakers. If the headset turns on again within that window, nothing is changed. **When the app starts with the headset off**, the same rule applies after the same wait: if the HyperX is still the default (e.g. its microphone was left that way while the headset was off), it moves to the fixed device; if the default is already something else, it isn't touched.
  - **Persistence**: the saved devices go in `audio_state.json` (atomic write; if it is corrupt it is ignored), so restarting the app while the headset is on still restores correctly. When the app starts with the headset already on, the same switch is made. **Nothing is restored when the app exits.**
  - **How**: Windows has no public API to change the default device; the app uses the undocumented COM interface `IPolicyConfig::SetDefaultEndpoint` (the same one used by SoundSwitch or AudioDeviceCmdlets) and `IMMDeviceEnumerator` to read the defaults and list the endpoints (`audio_switch.py`, with `comtypes`). The interfaces are declared by hand (no modules are generated on disk, so it works the same in the .exe) and everything runs on its own thread with COM initialized, without blocking the interface. Since it is an undocumented interface, a major Windows update could break it; if that happens, turn the menu option off and open an *issue*.
- **Remaining time**: slope (least squares) of the percentage derived from the voltage over the current discharge session (since the last charge), using the last 4 h of headset use; gaps with the headset off don't count. With less than 30 min of data it says "calculando…" (calculating…).
- **History**: one row per minute in `history.sqlite` (median mV of the minute, displayed percentage, whether it was charging). Rows older than `history_keep_days` days are deleted at startup.
- **Mute** (⚠ unverified on this model): all 4 HID collections are listened to; `65 04` = microphone muted, `65 xx` = active, `64 01` / `64 03` = on / off. If events arrive, the notification "Micrófono silenciado / activo" (Microphone muted / active) and the red dot on the icon are shown. If they never arrive, the feature stays inactive without breaking anything.

## Configuration

It is created automatically at `%APPDATA%\HyperXBattery\config.json` the first time. Edit it with the app closed. Missing keys use the default value; invalid values are replaced or clamped (and a notice is written to `app.log`).

| Option | Default | Description |
|---|---|---|
| `poll_interval_s` | `5` | How many seconds between battery polls |
| `reply_timeout_s` | `2` | Maximum wait for the reply |
| `misses_to_off` | `2` | Consecutive polls without a reply before the headset is considered off |
| `smoothing_window_s` | `120` | Median window |
| `history_interval_s` | `60` | One history row every N seconds |
| `history_keep_days` | `365` | Days of history kept |
| `keepalive_enabled` | `true` | Keep-alive (**Anti-apagado**; toggled from the menu) |
| `tone_hz` | `10` | Tone frequency |
| `tone_amp` | `0.05` | Tone amplitude (0.001 to 0.5) |
| `idle_pause_min` | `15` | Minutes without keyboard/mouse input before the keep-alive pauses (`0` = never) |
| `audio_device_match` | `"HyperX"` | Text the WASAPI output name must contain |
| `keepalive_retry_s` | `10` | Retry interval if the output isn't available |
| `session_poll_s` | `5` | How often the lock state and idle time are checked |
| `auto_switch_audio` | `true` | Automatic switching of the default audio (toggled from the menu) |
| `auto_switch_device_match` | `"HyperX Cloud Flight"` | Text the endpoint name (output and microphone) must contain |
| `auto_switch_off_confirm_s` | `20` | Extra seconds to confirm an inferred power-off before switching the audio (`0` = immediately; an explicit power-off is always immediate) |
| `off_playback_match` | `"Realtek"` | With the headset off, the default output moves to the first active output whose name contains this text (case-insensitive). Empty (`""`) = restore the one that was set at power-on |
| `off_capture_match` | `"Brio"` | Same for the microphone (with the default value, "Micrófono (Brio 100)"). Empty (`""`) = restore the one that was set at power-on |
| `notify_low_battery` | `true` | Low-battery alerts |
| `low_battery_thresholds` | `[20, 10]` | Percentages that trigger the alert |
| `notify_mute` | `true` | Notification when the microphone is muted / unmuted |

## Start with Windows

From the menu: **Iniciar con Windows** (Start with Windows). It writes the value `HyperXBattery` to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, and unchecking it deletes it. The command is:

- packaged: `"…\dist\HyperXBattery.exe"`; with the installer, `"%LOCALAPPDATA%\Programs\HyperX Battery\HyperXBattery.exe"` (the wizard checkbox writes exactly this value, so the tray menu shows it checked and can turn it off)
- from source: `"…\.venv\Scripts\pythonw.exe" "…\run_tray.py"`

(`run_tray.py` puts the project folder in `sys.path` on its own: the `Run` key doesn't define a working directory, so `pythonw -m hyperx_battery` wouldn't find the package.) If you move the folder or the portable `.exe`, uncheck and re-check the option (with the installer this isn't needed: it stays at a fixed path).

To check it: `reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v HyperXBattery`.

## Building the .exe and the installer

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1          # builds dist\HyperXBattery.exe
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Test    # runs pytest first
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Installer   # the .exe plus dist\HyperXBattery-Setup-<version>.exe
```

It uses PyInstaller (`--onefile --noconsole`) with the `.venv` Python, on `run_tray.py`, and applies the icon generated by `tools\make_icon.py`. Before building, it closes any running `HyperXBattery.exe` on its own (a `--onefile` build leaves two processes with that name; to kill it by hand you have to close both). To check that the .exe loads everything (HID, audio, Tk, pystray):

```powershell
Start-Process .\dist\HyperXBattery.exe --selftest -Wait -PassThru | Select-Object ExitCode   # 0 = OK; details in app.log
```

### Building the installer

It needs [Inno Setup](https://jrsoftware.org/isinfo.php) 6.3 or later. If you don't have it:

```powershell
winget install --id JRSoftware.InnoSetup -e --accept-source-agreements --accept-package-agreements
```

`build.ps1 -Installer` looks for `ISCC.exe` in `Program Files (x86)`, `Program Files`, `%LOCALAPPDATA%\Programs\Inno Setup 6` and the `PATH` (if it is missing, it says so before building anything and repeats the command above), builds the `.exe` and then `installer\HyperXBattery.iss`, and leaves `dist\HyperXBattery-Setup-<version>.exe`. The version is read from `__version__` in `hyperx_battery\__init__.py` and passed to Inno as `/DAppVersion=` (it isn't written in the script). By hand: `ISCC.exe /DAppVersion=1.2.0 installer\HyperXBattery.iss` (it needs `dist\HyperXBattery.exe` and `build\HyperXBattery.ico`, which `build.ps1` generates).

Script details:

- **Fixed `AppId`** (don't change it): it is what makes a new version replace the installed one. Save the `.iss` as UTF-8 **with BOM** (it contains accented characters).
- **Running instance**: the script uses `AppMutex=Local\HyperXBatteryTray`, the same mutex the app creates (Inno sees `Local\` mutexes from the same session), and it also looks for the user's `HyperXBattery.exe` processes, because a `--onefile` build takes a few seconds to unpack before it creates the mutex and leaves two processes with that name.
- **Startup key**: `HKCU\…\Run\HyperXBattery` = `"{app}\HyperXBattery.exe"`, the same as `autostart.build_command()`; it is removed with `uninsdeletevalue` and, if the app's menu created it, on uninstall also when it points to this installation.
- `tests\test_installer.py` checks that the mutex, the `Run` key, the `.exe` name and the data folder in the `.iss` match the Python code.

## Debug mode and testing without the headset

| Option | What it's for |
|---|---|
| `--debug-hid` | Prints **all** raw HID reports from the 4 collections to the console (`[time] HID FF90/0303  21 ff 05 …`). Use it to discover the mute events: with the app running, raise and lower the microphone boom and see which new line appears. In the .exe it opens its own console. |
| `--simulate-percent N` | Doesn't touch the headset: simulates an N% battery (tests the icon color and the alerts, e.g. `--simulate-percent 15`). It doesn't use the real history or play the tone. |
| `--simulate-charging` | Together with the above, simulates a charge starting from that percentage (the estimated number instead of the bolt; e.g. `--simulate-percent 82 --simulate-charging`). It doesn't save `charge_state.json`. |
| `--no-keepalive` | This run doesn't play the tone. |
| `--selftest` | Checks that the dependencies load and exits (code 0 if all is well). |
| `python -m hyperx_battery.session` | Prints every second whether the session is locked (the two signals used) and the idle time. Useful for testing Win+L. |

Unknown reports are always logged in `app.log` at DEBUG level (up to 20 repetitions of each). The environment variable `HYPERX_BATTERY_DATA_DIR` changes the data folder (useful for tests).

## Data

Everything lives in `%APPDATA%\HyperXBattery\`: `config.json`, `history.sqlite`, `charge_state.json`, `audio_state.json`, `app.log` (rotating, 1 MB × 4 files). Uninstalling the app doesn't delete them unless you ask for it in the uninstaller's final question.

## Privacy

HyperX Battery makes no network connections and collects no telemetry. Everything stays on your computer, in `%APPDATA%\HyperXBattery`: the app only talks to the USB dongle over HID, to your local audio devices and to the Windows registry.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

They cover the pure logic (mV→% curve, median, hysteresis, ±2 mV noise, charging, remaining time, the keep-alive pause decision, low-battery alerts, charge estimation and rate learning), the HID reader against a fake `hid` (power off/on, unplugging and replugging, mute events), the controller, the audio-switch decision against a fake backend (power on / off, fixed devices on power-off and their fallback, starting with the headset off, a manual change being respected, a device disappearing, the delay and its cancellation, persistence), the configuration, the registry handling (with a fake `winreg`), the history window, and that the installer script matches the code (mutex, `Run` key, `.exe` name). They need neither the headset nor Inno Setup.

## Project structure

```
hyperx_battery/
  __main__.py       startup, single instance, logging, wiring, --selftest
  app.py            Controller: state, alerts, history, keep-alive (main thread)
  config.py         dataclass + JSON with defaults and validation
  protocol.py       constants and decoding of HID reports (pure)
  device.py         HID: find, open the 4 collections, reader threads, polling, reconnection
  battery.py        mV→%, smoothing, hysteresis, charging, presence, alerts (pure)
  charge.py         time-based charge estimate, rate learning, state in JSON
  audio_switch.py   default audio: decisions, state JSON, COM (IPolicyConfig), thread
  keepalive.py      tone, pause decision, WASAPI stream thread with retries
  session.py        session lock and idle time (ctypes)
  history.py        per-minute aggregate, SQLite, remaining time, chart helpers
  tray.py           icon (Pillow), texts, menu and notifications (pystray)
  history_window.py tkinter window with the chart on a Canvas
  autostart.py      Run registry key
run_tray.py         launcher (PyInstaller entry point and Run key target)
installer/
  HyperXBattery.iss Inno Setup 6 script (per-user installer)
tests/              pytest
tools/              probe_battery.py, keepalive_test.py, make_icon.py
build.ps1  requirements.txt  requirements-dev.txt  pytest.ini  PLAN.md
```

### Tools (`tools\`)

- `probe_battery.py`: requests the battery once and shows the raw report (if it doesn't answer, the headset is off).
- `keepalive_test.py silence|tone [minutes] [amp] [hz]`: long keep-alive test; logs to `tools\keepalive_test.log`.
- `make_icon.py`: generates `build\HyperXBattery.ico`.

## Known limitations

- The percentage depends on the mV→% curve (`battery.py`); it is an approximation. The remaining-hours estimate extrapolates a straight line, so at the start of the discharge (the flat part of the curve) it tends to be optimistic.
- The mute and power on/off events over HID are not verified on this model (see `--debug-hid`).
- Low-battery notifications aren't remembered across app restarts: if you restart the app with the battery below the threshold, it alerts again.
- The charge estimate is approximate: it assumes a constant rate, and the real one drops near 100%.
- Audio switching uses an undocumented Windows COM interface (`IPolicyConfig`); it has only been tested on Windows 11. If several devices contain the search text, the first active one is used.
- The fixed power-off device is applied when the headset turns off and when the app starts with the headset off. If Windows later makes the HyperX the default again while the headset is off (e.g. when replugging the dongle), it isn't corrected until the next power-off or app restart.
- With the headset off the dongle is still there, but there is no way to read anything: the icon stays grey until the headset turns on.

## Disclaimer

HyperX Battery is an independent, unofficial project. It is not affiliated with, endorsed by, or sponsored by HyperX or HP Inc. HyperX and Cloud Flight are trademarks of their respective owners.

## License

[MIT](LICENSE).
