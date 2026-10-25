"""System tray: icon drawing (Pillow), tooltip / menu texts and notifications
(pystray). The view model and the text / colour decisions are pure functions.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

APP_TITLE = "HyperX Cloud Flight"

Color = Tuple[int, int, int]
GREEN: Color = (22, 163, 74)
YELLOW: Color = (250, 204, 21)
RED: Color = (220, 38, 38)
GRAY: Color = (107, 114, 128)
BLUE: Color = (37, 99, 235)
WHITE: Color = (255, 255, 255)
DARK: Color = (24, 24, 27)
MUTE_RED: Color = (239, 68, 68)
APPROX_FG: Color = (191, 219, 254)      # light blue: estimated digits read as "not measured"

TOOLTIP_LIMIT = 127     # NOTIFYICONDATA.szTip is 128 UTF-16 units including NUL


# --------------------------------------------------------------------------
# Pure view model
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class TrayView:
    """Everything the tray needs to draw itself."""

    link: str = "searching"             # searching | no_dongle | off | on
    percent: Optional[int] = None
    charging: bool = False
    estimate: Optional[int] = None      # time-based % while charging (not a measurement)
    full_in: Optional[str] = None       # e.g. "lleno en ~25 min"
    muted: Optional[bool] = None        # None = no mute event ever received
    remaining: Optional[str] = None     # e.g. "~12 h restantes" / "calculando…"
    keepalive: str = "esperando auricular"
    simulated: bool = False


@dataclass(frozen=True)
class IconSpec:
    kind: str                           # percent | charging | off
    label: str
    bg: Color
    fg: Color
    muted: bool = False
    approx: bool = False                # label is an estimate: drawn dimmer over a faint bolt


def percent_color(percent: int) -> Color:
    """Green > 50 %, yellow 20-50 %, red < 20 %."""
    if percent > 50:
        return GREEN
    if percent >= 20:
        return YELLOW
    return RED


def icon_spec(view: TrayView) -> IconSpec:
    muted = view.muted is True and view.link == "on"
    if view.link != "on":
        return IconSpec("off", "--", GRAY, WHITE)
    if view.charging:
        if view.estimate is not None:
            return IconSpec("charging", str(view.estimate), BLUE, APPROX_FG, muted, approx=True)
        return IconSpec("charging", "", BLUE, WHITE, muted)
    if view.percent is None:
        return IconSpec("off", "..", GRAY, WHITE, muted)
    bg = percent_color(view.percent)
    fg = DARK if bg == YELLOW else WHITE
    return IconSpec("percent", str(view.percent), bg, fg, muted)


def tooltip_text(view: TrayView) -> str:
    parts = [APP_TITLE]
    if view.link == "searching":
        parts.append("buscando auricular…")
    elif view.link == "no_dongle":
        parts.append("dongle no conectado")
    elif view.link == "off":
        parts.append("apagado")
    else:
        if view.charging:
            parts.append("cargando")
            if view.estimate is not None:
                parts.append(f"~{view.estimate} % (estimado)")
                if view.full_in:
                    parts.append(view.full_in)
        elif view.percent is not None:
            parts.append(f"{view.percent} %")
            if view.remaining:
                parts.append(view.remaining)
        else:
            parts.append("leyendo batería…")
        if view.muted:
            parts.append("micrófono silenciado")
    parts.append(f"Anti-apagado: {view.keepalive}")
    text = " · ".join(parts)
    if view.simulated:
        text = "[SIMULADO] " + text
    return text[:TOOLTIP_LIMIT]


def status_text(view: TrayView) -> str:
    """First (disabled) menu line."""
    if view.link == "searching":
        base = "Buscando auricular…"
    elif view.link == "no_dongle":
        base = "Dongle USB no conectado"
    elif view.link == "off":
        base = "Auricular apagado"
    elif view.charging:
        base = "Cargando…"
        if view.estimate is not None:
            base += f" ~{view.estimate} % (estimado)"
            if view.full_in:
                base += f" · {view.full_in}"
    elif view.percent is not None:
        base = f"Batería: {view.percent} %"
        if view.remaining:
            base += f" ({view.remaining})"
    else:
        base = "Leyendo batería…"
    if view.link == "on" and view.muted:
        base += " · micrófono silenciado"
    if view.simulated:
        base = "[SIMULADO] " + base
    return base


# --------------------------------------------------------------------------
# Icon drawing
# --------------------------------------------------------------------------
_FONT_CANDIDATES = ("segoeuib.ttf", "arialbd.ttf", "seguisb.ttf", "arial.ttf", "DejaVuSans-Bold.ttf")
_BOLT = ((0.60, 0.05), (0.20, 0.57), (0.44, 0.57), (0.37, 0.95), (0.80, 0.41), (0.55, 0.41))
_SUPERSAMPLE = 4


def tray_icon_size() -> int:
    """Size of a small tray icon in pixels (SM_CXSMICON; 16 at 100 % scale)."""
    try:
        size = int(ctypes.windll.user32.GetSystemMetrics(49))
        if 12 <= size <= 64:
            return size
    except Exception:  # noqa: BLE001
        pass
    return 16


def _load_font(px: int) -> ImageFont.ImageFont:
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default(px)


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int) -> ImageFont.ImageFont:
    px = max_h
    while px > 6:
        font = _load_font(px)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        if right - left <= max_w and bottom - top <= max_h:
            return font
        px -= 2
    return _load_font(6)


def _blend(a: Color, b: Color, t: float) -> Color:
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t), int(a[2] + (b[2] - a[2]) * t))


def render_icon(spec: IconSpec, size: int = 16) -> Image.Image:
    """Render the tray icon at exactly ``size`` x ``size`` pixels."""
    s = size * _SUPERSAMPLE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=spec.bg + (255,))

    if spec.kind == "charging":
        # Estimated digits go over a faint bolt (still reads as "charging").
        bolt = _blend(spec.bg, WHITE, 0.22) if spec.label else spec.fg
        draw.polygon([(x * s, y * s) for x, y in _BOLT], fill=bolt + (255,))
    if spec.label:
        max_w = int(s * (0.96 if len(spec.label) <= 2 else 0.98))
        max_h = int(s * (0.80 if len(spec.label) <= 2 else 0.60))
        font = _fit_font(draw, spec.label, max_w, max_h)
        draw.text((s / 2, s / 2), spec.label, font=font, fill=spec.fg + (255,), anchor="mm")

    if spec.muted:
        r = s * 0.22
        cx, cy = s - r - s * 0.02, s - r - s * 0.02
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=MUTE_RED + (255,), outline=WHITE + (255,),
                     width=max(1, s // 24))
        draw.line((cx - r * 0.55, cy + r * 0.55, cx + r * 0.55, cy - r * 0.55),
                  fill=WHITE + (255,), width=max(1, s // 22))

    return img.resize((size, size), Image.LANCZOS)


# --------------------------------------------------------------------------
# pystray wrapper
# --------------------------------------------------------------------------
@dataclass
class TrayActions:
    """Callbacks used by the menu. They are invoked on pystray's thread and
    must therefore only enqueue work for the main thread."""

    toggle_keepalive: Callable[[], None]
    keepalive_enabled: Callable[[], bool]
    show_history: Callable[[], None]
    toggle_autostart: Callable[[], None]
    autostart_enabled: Callable[[], bool]
    open_data_folder: Callable[[], None]
    quit: Callable[[], None]


class TrayUI:
    def __init__(self, actions: TrayActions, size: Optional[int] = None) -> None:
        import pystray  # imported lazily so the pure parts work without a GUI

        self._pystray = pystray
        self._actions = actions
        self._size = size or tray_icon_size()
        self._view = TrayView()
        self._spec: Optional[IconSpec] = None
        self._menu_text: Optional[str] = None
        self._lock = threading.Lock()
        self._icon = pystray.Icon(
            "HyperXBattery",
            icon=render_icon(icon_spec(self._view), self._size),
            title=tooltip_text(self._view),
            menu=self._build_menu(),
        )

    def _build_menu(self):
        item = self._pystray.MenuItem
        a = self._actions
        return self._pystray.Menu(
            item(lambda _i: status_text(self._view), lambda _icon, _i: None, enabled=False),
            self._pystray.Menu.SEPARATOR,
            item("Anti-apagado", lambda _icon, _i: a.toggle_keepalive(),
                 checked=lambda _i: a.keepalive_enabled()),
            item("Ver historial", lambda _icon, _i: a.show_history(), default=True),
            item("Iniciar con Windows", lambda _icon, _i: a.toggle_autostart(),
                 checked=lambda _i: a.autostart_enabled()),
            item("Abrir carpeta de datos", lambda _icon, _i: a.open_data_folder()),
            self._pystray.Menu.SEPARATOR,
            item("Salir", lambda _icon, _i: a.quit()),
        )

    def start(self) -> None:
        """Show the icon (pystray runs its own thread)."""
        self._icon.run_detached()

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:  # noqa: BLE001
            log.debug("icon.stop failed", exc_info=True)

    def update(self, view: TrayView) -> None:
        """Refresh icon, tooltip and menu texts. Thread-safe."""
        with self._lock:
            self._view = view
            spec = icon_spec(view)
            try:
                if spec != self._spec:
                    self._icon.icon = render_icon(spec, self._size)
                    self._spec = spec
                title = tooltip_text(view)
                if self._icon.title != title:
                    self._icon.title = title
                # pystray builds the popup menu in update_menu(); rebuilding it
                # while it is on screen is risky, so only do it when the status
                # line really changed (check marks are refreshed by refresh_menu()).
                text = status_text(view)
                if text != self._menu_text:
                    self._menu_text = text
                    self._icon.update_menu()
            except Exception:  # noqa: BLE001
                log.debug("tray update failed", exc_info=True)

    def refresh_menu(self) -> None:
        """Rebuild the menu (check marks); call after a menu action."""
        try:
            self._icon.update_menu()
        except Exception:  # noqa: BLE001
            log.debug("update_menu failed", exc_info=True)

    def notify(self, message: str, title: str = APP_TITLE) -> None:
        try:
            self._icon.notify(message, title)
        except Exception:  # noqa: BLE001
            log.warning("Notification failed", exc_info=True)
