# HyperX Battery

App de bandeja para Windows 11 que muestra la batería del **HyperX Cloud Flight for PS** (dongle USB `0951:1749`) y evita que el auricular se apague solo.

- **Ícono de bandeja** con el porcentaje (de a 1 %) sobre un fondo de color: verde > 50 %, amarillo 20-50 %, rojo < 20 %, gris si el auricular está apagado o el dongle desconectado, rayo azul si está cargando (con el número estimado en celeste tenue sobre el rayo, ver "Estimación al cargar"). Si se detecta que el micrófono está silenciado aparece un punto rojo en la esquina.
- **Anti-apagado**: reproduce un tono de 10 Hz al 5 % (inaudible) en la salida del HyperX mientras el auricular está prendido. Se pausa con la sesión bloqueada o tras N minutos sin tocar teclado ni mouse.
- **Audio predeterminado automático**: al prender el auricular, la salida y el micrófono del HyperX pasan a ser los predeterminados de Windows; al apagarlo se restauran los que había antes (ver "Cambio automático de audio").
- **Aviso de batería baja** (20 % y 10 %, una sola vez por ciclo de descarga).
- **Historial** en SQLite, tiempo restante estimado y ventana con gráfico de 24 h / 7 días.
- **Iniciar con Windows** desde el menú.

Interfaz en español; código e identificadores en inglés.

## Compatibilidad

| Auricular | Dongle USB | Estado |
|---|---|---|
| HyperX Cloud Flight Wireless **para PS4/PS5/PC** (versión PlayStation) | `0951:1749` | ✅ Probado |
| HyperX Cloud Flight Wireless (versión PC) | `0951:16C4`, `0951:1723` | ❌ No soportado: usa otro ID y no se detecta |
| Otros HyperX (Cloud Flight S, Cloud II/III, Alpha Wireless…) | — | ❌ No soportado |

Para saber qué versión tenés: Administrador de dispositivos → Dispositivos de sonido → el dongle debe llamarse **"HyperX Cloud Flight for PS"**. Si tu modelo no aparece, abrí un *issue* con el ID de tu dongle.

Solo Windows (probado en Windows 11).

## Requisitos

- Windows 11, Python 3.14 (el venv del proyecto está en `.venv`).
- Dependencias de ejecución en `requirements.txt` (`hidapi`, `sounddevice`, `numpy`, `pystray`, `Pillow`, `comtypes`); las de desarrollo (`pytest`, `pyinstaller`) en `requirements-dev.txt`.

```powershell
python -m venv .venv                                  # solo la primera vez
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

## Cómo ejecutarlo

```powershell
.\.venv\Scripts\python.exe -m hyperx_battery          # con consola (ves los mensajes si usas --debug-hid)
.\.venv\Scripts\pythonw.exe run_tray.py               # sin consola
```

También funciona el `.exe` generado (ver "Compilar"). Solo puede haber **una instancia** a la vez (mutex con nombre); si lanzas otra, sale sola.

El ícono aparece en la bandeja (a veces dentro del menú `^` de íconos ocultos: arrástralo a la barra para tenerlo siempre a la vista).

### Menú de la bandeja (clic derecho)

| Entrada | Qué hace |
|---|---|
| *Estado* (gris) | `Batería: 85 % (~12 h restantes)`, `Cargando… ~90 % (estimado) · lleno en ~25 min`, `Auricular apagado`, `Dongle USB no conectado` |
| **Anti-apagado** ✓ | Activa / desactiva el tono. Se guarda en `config.json`. |
| **Cambiar audio automáticamente** ✓ | Activa / desactiva el cambio del audio predeterminado al prender / apagar el auricular (`auto_switch_audio`). Se guarda en `config.json`. |
| **Ver historial** | Abre la ventana con el gráfico (también con clic izquierdo en el ícono). |
| **Iniciar con Windows** ✓ | Crea / borra la clave `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. |
| **Abrir carpeta de datos** | Abre `%APPDATA%\HyperXBattery\`. |
| **Salir** | Cierra todo. |

Tooltip del ícono: `HyperX Cloud Flight · 85 % · ~12 h restantes · Anti-apagado: activo`; cargando: `HyperX Cloud Flight · cargando · ~90 % (estimado) · lleno en ~25 min · Anti-apagado: activo`.

## Cómo funciona

- **Batería**: cada 5 s se escribe el reporte `21 FF 05 00…` (20 bytes) en la colección HID `0xFF90`; la respuesta `21 FF 05 HI LO` trae el voltaje en mV `(HI<<8)|LO`. El voltaje se convierte a % con una tabla (`battery.py`, constantes `CURVE_MV` / `CURVE_PERCENT`, reemplazables).
- **Suavizado**: el % mostrado sale de la **mediana** de las lecturas de los últimos 2 min. Mientras se descarga, el número **solo baja**; puede volver a subir solo tras cargar, tras haber estado apagado, o si el valor nuevo supera al mostrado por 3 puntos o más durante 2 min seguidos. Además, para que el ruido de ±2 mV no mueva el número, solo baja cuando el valor suavizado está claramente por debajo (1 punto de margen) y de a un punto por vez (`BatteryTracker(fall_margin, max_fall_step)`; con `fall_margin=0` es la regla simple `min`). Consecuencia: el número puede quedar hasta ~1 punto por encima del valor "exacto".
- **Cargando**: `HI == 0x10 and LO >= 20` o `HI >= 0x11` (≥ 4116 mV). Mientras carga el voltaje queda plano (~4,3 V) y no hay porcentaje medido: el ícono muestra el rayo y, si hay estimación, el número aproximado. Al terminar se reinicia el suavizado y se vuelve al valor medido.
- **Estimación al cargar**: al empezar a cargar se guarda la hora y el último % mostrado antes; el % estimado es `inicio + tasa × horas`, con tope en 99 % (nunca dice 100 %), y "lleno en" es `(100 − estimado) / tasa`. La tasa arranca en 25 %/h (la especificación de HyperX dice ~4,5 h para cargar a tope) y se **aprende**: unos 5 min después de desenchufar (cuando se disipa la carga superficial) se mide el % y se calcula la tasa real de esa carga, que se mezcla con la guardada (media móvil exponencial, peso 0,3, acotada a 10-60 %/h). Se ignoran las cargas de menos de 10 min, los resultados absurdos, y las que terminan en 99 % o más cuando son más lentas que la tasa actual (puede ser que simplemente se llenó); si el auricular se apaga antes de los 5 min no se aprende nada. Si la app arranca ya cargando, usa el último % del historial si tiene menos de 12 h; si no hay dato, solo muestra el rayo. La estimación **no** se guarda en el historial (esas filas siguen con % vacío) ni afecta los avisos de batería baja. Estado en `charge_state.json` (tasa aprendida y la carga en curso, para sobrevivir a un reinicio; si el archivo está dañado se usan los valores por defecto).
- **Encendido / apagado**: el auricular **no avisa** cuando se apaga, así que se detecta porque deja de contestar: 2 consultas seguidas sin respuesta (≈ 7-12 s) → gris. La primera respuesta después → vuelve solo (≤ 5 s). Si se desenchufa el dongle se cierra todo y se reintenta cada 5 s.
- **Anti-apagado**: abre un `OutputStream` WASAPI (modo compartido, float32, 48 kHz, estéreo) en la salida cuyo nombre contiene "HyperX" (no es la salida predeterminada) con un seno de 10 Hz al 5 %. Con silencio digital el auricular se apaga a los ~17 min; con este tono, no. Si la salida desaparece o el stream falla, se cierra y se reintenta cada 10 s buscando de nuevo por nombre. Entra y sale con un fundido de 150 ms para no hacer clic.
  - **Pausas** (se retoma solo al volver): auricular apagado, interruptor del menú en off, sesión de Windows bloqueada, o inactividad ≥ `idle_pause_min` (15 por defecto; `0` la desactiva). Así Windows puede suspenderse.
- **Cambio automático de audio**: el dongle sigue enchufado con el auricular apagado, así que Windows nunca cambia solo de dispositivo. Al detectar que el auricular **se prende**, la app guarda los dispositivos predeterminados actuales de reproducción y grabación (id y nombre) y pone como predeterminados, en los tres roles (consola, multimedia y comunicaciones), la salida y el micrófono cuyo nombre contiene `auto_switch_device_match` (por defecto `HyperX Cloud Flight`; entre los puntos finales activos, "Altavoces (HyperX Cloud Flight for PS)" y "Micrófono (HyperX Cloud Flight for PS)"). Al **apagarse** restaura los guardados, salida y micrófono por separado y solo si el predeterminado actual sigue siendo el HyperX (si elegiste otro mientras tanto, no lo toca). Si el dispositivo guardado ya no existe, deja todo como está y lo anota en `app.log`. Si el HyperX ya era el predeterminado al prenderse, no guarda nada nuevo (se conserva lo guardado antes). Cada cambio muestra una sola notificación: `Audio: HyperX Cloud Flight` al prender, `Audio: Altavoces (Realtek(R) Audio)` al apagar.
  - **Apagado con espera**: el apagado explícito (evento `64 03` del propio auricular) restaura al instante; uno inferido por consultas sin respuesta (fuera de alcance, interferencia, dongle desenchufado) debe confirmarse `auto_switch_off_confirm_s` (20 s) más antes de restaurar, para que un corte breve no mande el audio a los parlantes. Si el auricular vuelve a prenderse en esa ventana, no se restaura nada. Al arrancar la app con el auricular apagado y algo guardado de antes también se restaura (tras la misma espera).
  - **Persistencia**: los dispositivos guardados van en `audio_state.json` (escritura atómica; si está dañado se ignora), así que reiniciar la app con el auricular prendido sigue restaurando bien. Al arrancar con el auricular ya prendido se hace el mismo cambio. **Al salir de la app no se restaura nada.**
  - **Cómo**: Windows no tiene API pública para cambiar el dispositivo predeterminado; se usa la interfaz COM no documentada `IPolicyConfig::SetDefaultEndpoint` (la misma que SoundSwitch o AudioDeviceCmdlets) y `IMMDeviceEnumerator` para leer los predeterminados y listar los puntos finales (`audio_switch.py`, con `comtypes`). Las interfaces están declaradas a mano (no se generan módulos en disco, así que funciona igual en el .exe) y todo corre en un hilo propio con COM inicializado, sin bloquear la interfaz. Al ser una interfaz no documentada, una actualización grande de Windows podría romperla; en ese caso desactiva la opción del menú y abre un *issue*.
- **Tiempo restante**: pendiente (mínimos cuadrados) del % derivado del voltaje en la sesión de descarga actual (desde la última carga), usando las últimas 4 h de uso del auricular; los huecos con el auricular apagado no cuentan. Con menos de 30 min de datos dice "calculando…".
- **Historial**: una fila por minuto en `history.sqlite` (mediana de mV del minuto, % mostrado, si cargaba). Se borran las filas de más de `history_keep_days` días al iniciar.
- **Mute** (⚠ sin verificar en este modelo): se escuchan las 4 colecciones HID; `65 04` = micrófono silenciado, `65 xx` = activo, `64 01` / `64 03` = prendido / apagado. Si llegan eventos se muestra la notificación "Micrófono silenciado / activo" y el punto rojo en el ícono. Si nunca llegan, la función queda inactiva sin romper nada.

## Configuración

Se crea sola en `%APPDATA%\HyperXBattery\config.json` la primera vez. Edítala con la app cerrada. Las claves que falten usan el valor por defecto; los valores inválidos se reemplazan o se acotan (y se avisa en `app.log`).

| Opción | Defecto | Descripción |
|---|---|---|
| `poll_interval_s` | `5` | Cada cuántos segundos se consulta la batería |
| `reply_timeout_s` | `2` | Espera máxima de la respuesta |
| `misses_to_off` | `2` | Consultas seguidas sin respuesta para dar el auricular por apagado |
| `smoothing_window_s` | `120` | Ventana de la mediana |
| `history_interval_s` | `60` | Una fila de historial cada N segundos |
| `history_keep_days` | `365` | Días de historial que se conservan |
| `keepalive_enabled` | `true` | Anti-apagado (lo cambia el menú) |
| `tone_hz` | `10` | Frecuencia del tono |
| `tone_amp` | `0.05` | Amplitud del tono (0.001 a 0.5) |
| `idle_pause_min` | `15` | Minutos sin teclado/mouse para pausar el anti-apagado (`0` = nunca) |
| `audio_device_match` | `"HyperX"` | Texto que debe contener el nombre de la salida WASAPI |
| `keepalive_retry_s` | `10` | Reintento si la salida no está disponible |
| `session_poll_s` | `5` | Cada cuánto se consulta bloqueo e inactividad |
| `auto_switch_audio` | `true` | Cambio automático del audio predeterminado (lo cambia el menú) |
| `auto_switch_device_match` | `"HyperX Cloud Flight"` | Texto que debe contener el nombre del punto final (salida y micrófono) |
| `auto_switch_off_confirm_s` | `20` | Segundos extra para confirmar un apagado inferido antes de restaurar (`0` = al instante; el apagado explícito siempre es inmediato) |
| `notify_low_battery` | `true` | Avisos de batería baja |
| `low_battery_thresholds` | `[20, 10]` | Porcentajes que disparan el aviso |
| `notify_mute` | `true` | Notificación al silenciar / activar el micrófono |

## Iniciar con Windows

Desde el menú: **Iniciar con Windows**. Escribe el valor `HyperXBattery` en `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, y al destildarlo lo borra. El comando es:

- empaquetado: `"…\dist\HyperXBattery.exe"`
- desde el código: `"…\.venv\Scripts\pythonw.exe" "…\run_tray.py"`

(`run_tray.py` pone la carpeta del proyecto en `sys.path` por su cuenta: la clave `Run` no define carpeta de trabajo, así que `pythonw -m hyperx_battery` no encontraría el paquete.) Si mueves la carpeta o el `.exe`, destilda y vuelve a tildar la opción.

Para comprobarlo: `reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v HyperXBattery`.

## Compilar el .exe

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1          # genera dist\HyperXBattery.exe
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Test    # corre pytest antes
```

Usa PyInstaller (`--onefile --noconsole`) con el Python de `.venv`, sobre `run_tray.py`, y le pone el ícono generado por `tools\make_icon.py`. Antes de compilar cierra por su cuenta cualquier `HyperXBattery.exe` en ejecución (un `--onefile` deja dos procesos con ese nombre; para matarlo a mano hay que cerrar ambos). Para comprobar que el .exe carga todo (HID, audio, Tk, pystray):

```powershell
Start-Process .\dist\HyperXBattery.exe --selftest -Wait -PassThru | Select-Object ExitCode   # 0 = OK; detalle en app.log
```

## Modo debug y pruebas sin auricular

| Opción | Para qué sirve |
|---|---|
| `--debug-hid` | Imprime en la consola **todos** los reportes HID crudos de las 4 colecciones (`[hora] HID FF90/0303  21 ff 05 …`). Úsalo para descubrir los eventos de mute: con la app corriendo, levanta y baja el brazo del micrófono y mira qué línea nueva aparece. En el .exe abre una consola propia. |
| `--simulate-percent N` | No toca el auricular: simula una batería de N % (prueba el color del ícono y los avisos, p. ej. `--simulate-percent 15`). No usa el historial real ni reproduce el tono. |
| `--simulate-charging` | Con lo anterior, simula una carga que empieza desde ese % (rayo con el número estimado; p. ej. `--simulate-percent 82 --simulate-charging`). No guarda `charge_state.json`. |
| `--no-keepalive` | Esta ejecución no reproduce el tono. |
| `--selftest` | Comprueba que cargan las dependencias y sale (código 0 si todo bien). |
| `python -m hyperx_battery.session` | Imprime cada segundo si la sesión está bloqueada (las dos señales usadas) y el tiempo de inactividad. Útil para probar Win+L. |

Los reportes desconocidos se registran siempre en `app.log` a nivel DEBUG (hasta 20 repeticiones de cada uno). Variable de entorno `HYPERX_BATTERY_DATA_DIR` cambia la carpeta de datos (útil para pruebas).

## Datos

Todo en `%APPDATA%\HyperXBattery\`: `config.json`, `history.sqlite`, `charge_state.json`, `audio_state.json`, `app.log` (rotativo, 1 MB × 4 archivos).

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Cubren la lógica pura (curva mV→%, mediana, histéresis, ruido de ±2 mV, carga, tiempo restante, decisión de pausa del anti-apagado, avisos de batería baja, estimación de carga y aprendizaje de la tasa), el lector HID contra un `hid` falso (apagado/encendido, desenchufar y volver a enchufar, eventos de mute), el controlador, la decisión de cambio de audio contra un backend falso (encender / apagar, cambio manual respetado, dispositivo desaparecido, espera y cancelación, persistencia), la configuración, el registro (con un `winreg` falso) y la ventana de historial. No necesitan el auricular.

## Estructura

```
hyperx_battery/
  __main__.py       arranque, instancia única, logging, cableado, --selftest
  app.py            Controller: estado, avisos, historial, anti-apagado (hilo principal)
  config.py         dataclass + JSON con valores por defecto y validación
  protocol.py       constantes y decodificación de reportes HID (pura)
  device.py         HID: buscar, abrir las 4 colecciones, hilos lectores, consulta, reconexión
  battery.py        mV→%, suavizado, histéresis, carga, presencia, avisos (puro)
  charge.py         estimación por tiempo al cargar, aprendizaje de la tasa, estado en JSON
  audio_switch.py   audio predeterminado: decisiones, JSON de estado, COM (IPolicyConfig), hilo
  keepalive.py      tono, decisión de pausa, hilo del stream WASAPI con reintentos
  session.py        bloqueo de sesión e inactividad (ctypes)
  history.py        agregado por minuto, SQLite, tiempo restante, ayudas del gráfico
  tray.py           ícono (Pillow), textos, menú y notificaciones (pystray)
  history_window.py ventana tkinter con el gráfico en Canvas
  autostart.py      clave Run del registro
run_tray.py         lanzador (entrada de PyInstaller y de la clave Run)
tests/              pytest
tools/              probe_battery.py, keepalive_test.py, make_icon.py
build.ps1  requirements.txt  requirements-dev.txt  pytest.ini  PLAN.md
```

### Herramientas (`tools\`)

- `probe_battery.py`: pide la batería una vez y muestra el reporte crudo (si no responde, el auricular está apagado).
- `keepalive_test.py silence|tone [minutos] [amp] [hz]`: prueba larga del anti-apagado; registra en `tools\keepalive_test.log`.
- `make_icon.py`: genera `build\HyperXBattery.ico`.

## Límites conocidos

- El % depende de la curva mV→% (`battery.py`); es una aproximación. La estimación de horas restantes extrapola una recta, así que al principio de la descarga (zona plana de la curva) tiende a ser optimista.
- Los eventos de mute y de encendido/apagado por HID no están verificados en este modelo (ver `--debug-hid`).
- Las notificaciones de batería baja no se recuerdan entre reinicios de la app: si la reinicias con la batería por debajo del umbral, avisa de nuevo.
- La estimación al cargar es aproximada: asume una tasa constante, y la real baja cerca del 100 %.
- El cambio de audio usa una interfaz COM no documentada de Windows (`IPolicyConfig`); solo se probó en Windows 11. Con varios dispositivos que contengan el texto buscado se usa el primero activo.
- Con el auricular apagado el dongle sigue ahí, pero no hay forma de leer nada: el ícono queda gris hasta que se prende.
