"""Prueba rápida: pide la batería al dongle Cloud Flight for PS (0951:1749) y muestra la respuesta cruda."""
import time

import hid

VID, PID = 0x0951, 0x1749

paths = {d["usage_page"]: d["path"] for d in hid.enumerate(VID, PID)}
ctrl = hid.device()
ctrl.open_path(paths[0xFF90])
ctrl.set_nonblocking(False)

request = [0x21, 0xFF, 0x05] + [0x00] * 17
print("write ->", ctrl.write(request))

deadline = time.time() + 3
while time.time() < deadline:
    data = ctrl.read(64, timeout_ms=500)
    if data:
        print(f"len={len(data)}", " ".join(f"{b:02x}" for b in data))
        if len(data) >= 5 and data[:3] == [0x21, 0xFF, 0x05]:
            mv = (data[3] << 8) | data[4]
            print(f"voltaje = {mv} mV")
ctrl.close()
