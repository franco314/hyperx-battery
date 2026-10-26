"""System tray: icon drawing (Pillow), tooltip / menu texts and notifications
(pystray). The view model and the text / colour decisions are pure functions.
"""

from __future__ import annotations

import ctypes
import logging
import math
import threading
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from PIL import Image, ImageDraw

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
    approx: bool = False                # label is an estimate: drawn in the lighter APPROX_FG, no bolt


def percent_color(percent: int) -> Color:
    """Green > 50 %, yellow 20-50 %, red < 20 %."""
    if percent > 50:
        return GREEN
    if percent >= 20:
        return YELLOW
    return RED


def _digits(value: int) -> str:
    """Icon label of a percentage: two digits at most, so 100 % is shown as ``F`` (full)."""
    return "F" if value >= 100 else str(max(0, value))


def icon_spec(view: TrayView) -> IconSpec:
    muted = view.muted is True and view.link == "on"
    if view.link != "on":
        return IconSpec("off", "-", GRAY, WHITE)            # headset off / no dongle: middle bar
    if view.charging:
        if view.estimate is not None:
            return IconSpec("charging", _digits(view.estimate), BLUE, APPROX_FG, muted, approx=True)
        return IconSpec("charging", "", BLUE, WHITE, muted)
    if view.percent is None:
        return IconSpec("off", "_", GRAY, WHITE, muted)     # on, battery not read yet: bottom bar
    bg = percent_color(view.percent)
    fg = DARK if bg == YELLOW else WHITE
    return IconSpec("percent", _digits(view.percent), bg, fg, muted)


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
#
# The background (rounded square, bolt) is drawn supersampled; the digits and
# the mute badge are drawn straight on the pixel grid, with solid rectangles
# and no antialiasing, so they stay crisp at any tray size.
# --------------------------------------------------------------------------
_BOLT = ((0.60, 0.05), (0.20, 0.57), (0.44, 0.57), (0.37, 0.95), (0.80, 0.41), (0.55, 0.41))
_SUPERSAMPLE = 8

# Seven-segment glyphs; segments are named as usual:
#    aaa
#   f   b
#    ggg
#   e   c
#    ddd
_SEGMENTS = {
    "0": "abcdef", "1": "bc", "2": "abdeg", "3": "abcdg", "4": "bcfg",
    "5": "acdfg", "6": "acdefg", "7": "abc", "8": "abcdefg", "9": "abcdfg",
    "F": "aefg",                        # 100 % (three digits do not fit)
    "-": "g",                           # headset off / unknown
    "_": "d",                           # headset on, battery not read yet
}

Rect = Tuple[int, int, int, int]        # x0, y0, x1, y1; x1 / y1 exclusive


def tray_icon_size() -> int:
    """Size of a small tray icon in pixels (SM_CXSMICON; 16 at 100 % scale)."""
    try:
        size = int(ctypes.windll.user32.GetSystemMetrics(49))
        if 12 <= size <= 64:
            return size
    except Exception:  # noqa: BLE001
        pass
    return 16


def _half_up(value: float) -> int:
    return int(value + 0.5)


@dataclass(frozen=True)
class _SegMetrics:
    stroke: int         # thickness of every segment
    width: int          # width of one digit
    gap: int            # blank columns between two digits
    run: int            # length of one vertical segment between two bars
    height: int         # 3 bars + 2 runs


def _seg_metrics(size: int) -> _SegMetrics:
    """Digit geometry for an icon of ``size`` px.

    At 16 px each digit is 7 x 12 with 2-px strokes and a 1-px gap (two digits
    take 15 columns); exact multiples of 16 are that design scaled by an
    integer. Other sizes keep the proportions, rounded to whole pixels: stroke
    round(size / 8) (ties go to even; never below 2 px unless the digit is too
    narrow for that), gap ceil(size / 16), and digits 12 / 7 as tall as wide.
    """
    k = size / 16
    gap = max(1, math.ceil(k))
    width = max(3, (_half_up(15 * k) - gap) // 2)
    stroke = max(1, min(max(2, round(size / 8)), (width - 2) // 2))
    run = max(1, _half_up((width * 12 / 7 - 3 * stroke) / 2))
    return _SegMetrics(stroke, width, gap, run, 3 * stroke + 2 * run)


def _segment_rect(seg: str, x: int, y: int, m: _SegMetrics) -> Rect:
    t, w, h, r = m.stroke, m.width, m.height, m.run
    return {
        "a": (x, y, x + w, y + t),
        "b": (x + w - t, y, x + w, y + 2 * t + r),
        "c": (x + w - t, y + t + r, x + w, y + h),
        "d": (x, y + h - t, x + w, y + h),
        "e": (x, y + t + r, x + t, y + h),
        "f": (x, y, x + t, y + 2 * t + r),
        "g": (x, y + t + r, x + w, y + 2 * t + r),
    }[seg]


def label_rects(label: str, size: int) -> List[Rect]:
    """Solid rectangles (x0, y0, x1, y1; end exclusive) that draw ``label`` in a
    ``size`` x ``size`` icon: the glyphs are centred on the pixel grid, using
    the ink (so a leading ``1``, which is right-aligned in its cell, does not
    leave a hole on the left)."""
    m = _seg_metrics(size)
    rects = []
    for i, ch in enumerate(label):
        try:
            segments = _SEGMENTS[ch]
        except KeyError:
            raise ValueError(f"no seven-segment glyph for {ch!r}") from None
        rects += [_segment_rect(s, i * (m.width + m.gap), 0, m) for s in segments]
    if not rects:
        return []
    left, right = min(r[0] for r in rects), max(r[2] for r in rects)
    dx = (size - (right - left)) // 2 - left
    dy = (size - m.height) // 2
    return [(x0 + dx, y0 + dy, x1 + dx, y1 + dy) for x0, y0, x1, y1 in rects]


def _draw_mute_badge(img: Image.Image) -> None:
    """Red dot with a white ring in the bottom-right corner, drawn pixel by
    pixel (no antialiasing); a white slash is added when it is big enough."""
    size = img.width
    d = max(5, _half_up(size * 0.3))                # 5 px at 16, 6 at 20, 7 at 24, 10 at 32
    ring = max(1, size // 16)
    first = size - d
    c = first + d / 2
    outer, inner = (d / 2) ** 2, (d / 2 - ring) ** 2
    px = img.load()
    for y in range(first, size):
        for x in range(first, size):
            dist = (x + 0.5 - c) ** 2 + (y + 0.5 - c) ** 2
            if dist <= outer:
                px[x, y] = (WHITE if dist > inner else MUTE_RED) + (255,)
    if d >= 7:
        mid = c - 0.5                               # pixel index of the badge centre
        reach = d / 2 - ring - 0.5                  # integer end points, ending at the ring
        ImageDraw.Draw(img).line((mid - reach, mid + reach, mid + reach, mid - reach),
                                 fill=WHITE + (255,), width=max(1, size // 24))


def render_icon(spec: IconSpec, size: int = 16) -> Image.Image:
    """Render the tray icon at exactly ``size`` x ``size`` pixels."""
    s = size * _SUPERSAMPLE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=spec.bg + (255,))
    if spec.kind == "charging" and not spec.label:
        draw.polygon([(x * s, y * s) for x, y in _BOLT], fill=spec.fg + (255,))
    img = img.resize((size, size), Image.BOX)        # exact area coverage: flat interior, no ringing

    for rect in label_rects(spec.label, size):
        img.paste(spec.fg + (255,), rect)           # replaces the pixels, no blending

    if spec.muted:
        _draw_mute_badge(img)
    return img


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
    toggle_audio_switch: Callable[[], None] = lambda: None
    audio_switch_enabled: Callable[[], bool] = lambda: False


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
            item("Cambiar audio automáticamente", lambda _icon, _i: a.toggle_audio_switch(),
                 checked=lambda _i: a.audio_switch_enabled()),
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
