# HyperX Battery — plan de implementación

App de bandeja para Windows 11 que muestra la batería del **HyperX Cloud Flight for PS** (versión PS4/PS5/PC) y evita que se apague solo.
Interfaz en español; código e identificadores en inglés.

## 1. Hechos verificados en esta PC (25/10/2026)

**Dispositivo**
- Dongle USB `VID 0x0951`, `PID 0x1749`, nombre "HyperX Cloud Flight for PS". Interfaz HID 3 con cuatro colecciones:
  - `usage_page 0x000C` (consumer)
  - `0xFFC0`
  - **`0xFF90` / usage `0x0303`**: canal de control
  - `0xFF00`
- La salida de audio en WASAPI se llama "Altavoces (HyperX Cloud Flight for PS)". **No** es la salida predeterminada (es Realtek), así que hay que abrirla por nombre.

**Batería**
- Pedido: escribir en la colección `0xFF90` el reporte de 20 bytes `21 FF 05 00 … 00`.
- Respuesta (20 bytes): `21 FF 05 HI LO 00…`. El voltaje es `(HI << 8) | LO` en mV. Visto: `10 02` = 4098 mV con carga casi completa.
- Descarga medida: 4085 → 4032 mV en 30 min con el tono activo.

**Apagado automático**
- Con un stream de **silencio digital** se apagó a los ~17-18 min. Las consultas HID de batería **no** reinician el temporizador.
- Con un **tono senoidal de 10 Hz al 5 % de amplitud** (float32, 48 kHz, estéreo) siguió prendido 30 min. El usuario no lo escucha ni lo siente.

**Detección de apagado**
- Al apagarse **no** llegó el evento `64 03` por la colección `0xFF90`. Hay que detectarlo porque deja de contestar el pedido de batería.

**Referencias del protocolo**
- [HyperHeadset](https://github.com/LennardKittner/HyperHeadset) (MIT, Rust), `src/devices/cloud_flight_wireless.rs`, ya incluye el PID `0x1749`.
- [HeadsetControl](https://github.com/Sapd/HeadsetControl), `lib/devices/hyperx_cloud_flight.hpp`.

**Sin verificar todavía (diseñar para que falle con elegancia)**
- Eventos de mute: `65 04` = silenciado; `65 xx` con otro valor = activo.
- Eventos de encendido: `64 01` = prendido; `64 03` = apagado.
- No se sabe por cuál colección llegan, así que hay que escuchar las cuatro.

## 2. Funciones

1. **Batería en la bandeja, de a 1 %** (suavizado, ver §3). El ícono muestra el número del porcentaje sobre un fondo de color:
   - verde > 50 %, amarillo 20-50 %, rojo < 20 %
   - gris si el auricular está apagado o desconectado
   - rayo si está cargando

   Tooltip: `HyperX Cloud Flight · 85 % · ~12 h restantes · Anti-apagado: activo`.
2. **Anti-apagado.** Tono de 10 Hz al 5 % en la salida del HyperX, solo mientras el auricular está prendido.
   - Se pausa con la sesión de Windows bloqueada **o** cuando el usuario lleva N minutos sin tocar teclado ni mouse (por defecto 15). Así Windows puede suspenderse.
   - Se retoma solo al volver.
   - En el menú hay un interruptor manual para activarlo o desactivarlo.
3. **Aviso de batería baja.** Notificación de Windows al cruzar 20 % y 10 % (configurable), una sola vez por ciclo de descarga. Se rearma al cargar.
4. **Indicador de mute.** Si llegan eventos de mute: notificación breve "Micrófono silenciado / activo" y una marca en el ícono. Si nunca llegan, la función queda inactiva sin romper nada.
5. **Historial y tiempo restante.**
   - Guardar cada lectura en SQLite.
   - Estimar las horas restantes con la pendiente del porcentaje (o del voltaje) en la sesión de descarga actual. Si hay menos de 30 min de datos, mostrar "calculando…".
   - "Ver historial" abre una ventana tkinter con un gráfico de línea de las últimas 24 h o 7 días, dibujado en Canvas, sin matplotlib.
6. **Iniciar con Windows.** Interruptor en el menú que escribe o borra la clave `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.

**Menú de la bandeja**
- Estado (texto no clickeable)
- Anti-apagado ✓
- Ver historial
- Iniciar con Windows ✓
- Abrir carpeta de datos
- Salir

## 3. Diseño técnico

**Dependencias**
- Python 3.14, con el venv existente en `.venv`.
- `hidapi`, `sounddevice` y `numpy`, que ya están instalados.
- Se agregan `pystray` y `Pillow`.
- Notificaciones con `pystray.Icon.notify()`, sin dependencias extra.
- Bloqueo de sesión e inactividad con `ctypes`: `WTSQuerySessionInformationW`/`WTSSessionInfoEx` (flag de sesión bloqueada) y `GetLastInputInfo`, consultados cada 5 s.
- Instancia única: mutex con nombre vía `CreateMutexW`.

**Datos**
- Todo en `%APPDATA%\HyperXBattery\`: `config.json`, `history.sqlite`, `app.log` (rotativo).

**Estructura**
```
hyperx_battery/
  __main__.py      # arranque, instancia única, cableado
  config.py        # dataclass + JSON con valores por defecto
  device.py        # HID: buscar, abrir las 4 colecciones, hilo lector, consulta periódica, reconexión
  battery.py       # funciones puras: mV -> %, detección de carga
  keepalive.py     # stream de sounddevice con el tono; busca la salida por nombre y reintenta si falla
  session.py       # bloqueo de sesión e inactividad (ctypes)
  history.py       # SQLite + estimación del tiempo restante (función pura testeable)
  tray.py          # pystray: dibujo del ícono con Pillow, menú, notificaciones
  history_window.py# ventana tkinter con el gráfico
  autostart.py     # clave Run del registro
tests/             # pytest para battery, estimación, lógica de pausa del anti-apagado
tools/             # probe_battery.py y keepalive_test.py (mover los existentes acá)
requirements.txt  README.md  build.ps1
```

**Batería: de voltaje a porcentaje (de a 1 %)**
- **Curva:** interpolación lineal entre los puntos `(mV, %)` de abajo, recortada a 0-100. Parte de la tabla de HyperHeadset, extendida en los extremos porque el auricular real mide 4030-4100 mV con carga casi completa:
  - mV: `3300 3328 3584 3674 3704 3732 3744 3754 3764 3774 3784 3794 3804 3824 3840 3860 3890 3910 3940 3960 4100`
  - %:  `0    5    10   15   20   25   30   35   40   45   50   55   60   65   70   75   80   85   90   95   100`
  - La curva va como constante en `battery.py`, para que una futura autocalibración pueda reemplazarla.
- **Suavizado:** el porcentaje mostrado sale de la **mediana de las lecturas de los últimos 2 min** (unas 24 a 5 s), no de la lectura suelta. La mediana ignora las caídas de voltaje cuando sube el volumen o se prenden las luces.
- **Histéresis:** mientras se descarga, el porcentaje mostrado **solo puede bajar** (`shown = min(shown, nuevo)`). Puede volver a subir solo:
  - después de un ciclo de carga, o
  - después de que el auricular estuvo apagado, o
  - si el valor nuevo supera al mostrado por 3 puntos o más durante 2 min seguidos.
- Mostrar entero, sin decimales. Los avisos de batería baja y la estimación de tiempo usan este valor suavizado.
- **Carga:** `HI == 0x10 and LO >= 20` o `HI >= 0x11`. Mientras carga no hay porcentaje confiable: mostrar "cargando". Al terminar de cargar, reiniciar el suavizado.
- Todo esto son funciones o clases puras, sin E/S, con tests: interpolación, mediana con ventana, histéresis y casos de ruido de ±2 mV que no deben hacer saltar el número.

**Ciclo de estado (`device.py`)**
- Consultar la batería **cada 5 s** (configurable, `poll_interval_s=5`). Esperar la respuesta hasta 2 s.
- **Dos consultas seguidas sin respuesta → estado `off`** (detección en ~10-20 s). Mientras está `off`, seguir consultando cada 5 s.
- La primera respuesta después de `off` → `on` (se nota en ≤ 5 s): se avisa al anti-apagado y se reinicia el suavizado.
- **Historial:** guardar una fila por minuto, no cada lectura: mediana de mV del minuto, % mostrado y si está cargando.
- Si se desenchufa el dongle (error de E/S): cerrar todo, reintentar `hid.enumerate` cada 5 s y reabrir.
- Registrar cada reporte crudo desconocido en `app.log` a nivel DEBUG. Una opción `--debug-hid` lo imprime en consola: sirve para descubrir los eventos de mute.

**Hilos**
- tkinter vive en el hilo principal. pystray corre con `run_detached()`.
- El lector HID y el sondeo de sesión van en hilos daemon.
- La comunicación es por `queue.Queue` o callbacks protegidos con lock.
- Ninguna llamada a tkinter fuera del hilo principal (usar `root.after`).

**Anti-apagado (`keepalive.py`)**
- Abrir el `OutputStream` de WASAPI en modo compartido, así se mezcla con el audio normal.
- Si el dispositivo desaparece o el stream tira error: cerrar y reintentar cada 10 s, buscando de nuevo por nombre.
- Parámetros configurables: `tone_hz=10`, `tone_amp=0.05`, `idle_pause_min=15`.

**Empaquetado**
- `build.ps1` genera con PyInstaller (`--onefile --noconsole`) el archivo `dist\HyperXBattery.exe`.
- La clave de autoarranque apunta al .exe si corre empaquetado, y si no a `.venv\Scripts\pythonw.exe -m hyperx_battery`.

## 4. Criterios de terminado

- `pytest` pasa.
- `python -m hyperx_battery` muestra el ícono con el porcentaje correcto en menos de 5 s.
- Al apagar el auricular, el ícono pasa a gris en menos de 20 s. Al prenderlo, vuelve solo en menos de 10 s.
- El porcentaje de a 1 % no oscila: con el auricular en reposo no sube ni baja más de 1 punto en 10 min.
- Con el anti-apagado activo, el auricular no se apaga en 30 min. Con la sesión bloqueada, el stream se detiene.
- Las notificaciones de batería baja se pueden probar con un valor simulado (`--simulate-percent 15`).
- El interruptor de autoarranque crea y borra la clave del registro.
- `build.ps1` genera un .exe que arranca.

## 5. Flujo de trabajo

1. `git init` en esta carpeta, con `.gitignore` para `.venv`, `dist`, `build` y `__pycache__`.
2. Un subagente **Sonnet 5.5 (esfuerzo extra high)** implementa todo este plan.
3. Opus revisa el código, corre los tests y prueba con el auricular real. Con el usuario verifica el mute (levantar el brazo del micrófono con `--debug-hid`) y el bloqueo de sesión.
4. Las correcciones vuelven al mismo subagente.

## 6. Segunda etapa (fuera de este alcance)

- **Autocalibración de la curva:** después de una descarga completa registrada en el historial (de lleno a apagado), armar una curva mV → % propia de esta batería y reemplazar la constante.
