"""Prueba del anti-apagado para HyperX Cloud Flight for PS (0951:1749).

Reproduce una señal en la salida del auricular y vigila por HID si se apaga.
Registra todo en keepalive_test.log (junto a este script).

Uso:
    python keepalive_test.py silence [minutos]      # stream de ceros
    python keepalive_test.py tone [minutos] [amp] [hz]  # tono grave, ej. amp 0.05 hz 10
"""
import sys
import time
from datetime import datetime
from pathlib import Path

import hid
import numpy as np
import sounddevice as sd

VID, PID = 0x0951, 0x1749
CTRL_USAGE_PAGE = 0xFF90
BATTERY_REQUEST = [0x21, 0xFF, 0x05] + [0x00] * 17
BATTERY_POLL_S = 60
SAMPLE_RATE = 48000
LOG = Path(__file__).with_name("keepalive_test.log")


def log(msg):
    line = f"{datetime.now():%H:%M:%S}  {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def find_output_device():
    wasapi = next(i for i, h in enumerate(sd.query_hostapis()) if "WASAPI" in h["name"])
    for i, d in enumerate(sd.query_devices()):
        if d["hostapi"] == wasapi and d["max_output_channels"] > 0 and "HyperX" in d["name"]:
            return i
    raise SystemExit("No encontré la salida de audio del HyperX.")


def make_callback(mode, amp, hz):
    phase = 0

    def callback(outdata, frames, _time, _status):
        nonlocal phase
        if mode == "silence":
            outdata.fill(0)
            return
        t = (np.arange(frames) + phase) / SAMPLE_RATE
        outdata[:] = (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)[:, None]
        phase += frames

    return callback


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "silence"
    minutes = float(sys.argv[2]) if len(sys.argv) > 2 else 35
    amp = float(sys.argv[3]) if len(sys.argv) > 3 else 0.05
    hz = float(sys.argv[4]) if len(sys.argv) > 4 else 10

    paths = {d["usage_page"]: d["path"] for d in hid.enumerate(VID, PID)}
    ctrl = hid.device()
    ctrl.open_path(paths[CTRL_USAGE_PAGE])

    device = find_output_device()
    desc = "silencio" if mode == "silence" else f"tono {hz:g} Hz amp {amp:g}"
    log(f"=== Inicio: {desc}, {minutes:g} min, salida '{sd.query_devices(device)['name']}' ===")

    stream = sd.OutputStream(device=device, samplerate=SAMPLE_RATE, channels=2,
                             dtype="float32", callback=make_callback(mode, amp, hz))
    stream.start()

    start = time.time()
    next_poll = start
    last_battery = start
    silent_warned = False
    result = "SIGUE PRENDIDO al terminar"
    try:
        while time.time() - start < minutes * 60:
            if time.time() >= next_poll:
                ctrl.write(BATTERY_REQUEST)
                next_poll += BATTERY_POLL_S
            if not silent_warned and time.time() - last_battery > 2.5 * BATTERY_POLL_S:
                log(f"[{(time.time() - start) / 60:5.1f} min] sin respuesta de batería (¿apagado?)")
                result = f"dejó de responder a los {(last_battery - start) / 60:.1f} min"
                silent_warned = True
            data = ctrl.read(64, timeout_ms=1000)
            if not data:
                continue
            elapsed = (time.time() - start) / 60
            raw = " ".join(f"{b:02x}" for b in data)
            if data[:3] == [0x21, 0xFF, 0x05]:
                last_battery = time.time()
                log(f"[{elapsed:5.1f} min] batería {(data[3] << 8) | data[4]} mV")
            elif data[0] == 0x64 and data[1] == 0x03:
                log(f"[{elapsed:5.1f} min] AURICULAR APAGADO  ({raw})")
                result = f"SE APAGÓ a los {elapsed:.1f} min"
                break
            else:
                log(f"[{elapsed:5.1f} min] evento {raw}")
    except KeyboardInterrupt:
        result = "cortado a mano"
    finally:
        stream.stop()
        stream.close()
        ctrl.close()
    log(f"=== Resultado: {result} ===")


if __name__ == "__main__":
    main()
