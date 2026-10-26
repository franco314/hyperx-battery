from dataclasses import replace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from hyperx_battery import tray
from hyperx_battery.tray import (
    APPROX_FG,
    BLUE,
    DARK,
    GRAY,
    GREEN,
    MUTE_RED,
    RED,
    WHITE,
    YELLOW,
    IconSpec,
    TrayView,
    _label_font,
    icon_spec,
    percent_color,
    render_icon,
    status_text,
    tooltip_text,
)


@pytest.mark.parametrize(
    "pct,color",
    [(100, GREEN), (51, GREEN), (50, YELLOW), (35, YELLOW), (20, YELLOW), (19, RED), (0, RED)],
)
def test_color_thresholds(pct, color):
    assert percent_color(pct) == color


def test_icon_spec_percent():
    spec = icon_spec(TrayView(link="on", percent=85))
    assert spec.kind == "percent" and spec.label == "85" and spec.bg == GREEN and not spec.muted


def test_icon_spec_gray_when_off_or_disconnected():
    for link in ("off", "no_dongle", "searching"):
        spec = icon_spec(TrayView(link=link, percent=85, charging=True))
        assert spec.bg == GRAY and spec.kind == "off" and spec.label == "--"


def test_icon_spec_on_without_a_reading_yet_is_a_distinct_gray():
    spec = icon_spec(TrayView(link="on"))
    assert spec.kind == "off" and spec.bg == GRAY and spec.label == ".."
    assert spec != icon_spec(TrayView(link="off"))


def test_icon_label_is_the_whole_number():
    assert icon_spec(TrayView(link="on", percent=100)).label == "100"
    assert icon_spec(TrayView(link="on", percent=7)).label == "7"
    assert icon_spec(TrayView(link="on", percent=0)).label == "0"
    assert icon_spec(TrayView(link="on", percent=100)).bg == GREEN
    assert icon_spec(TrayView(link="on", charging=True, estimate=99)).label == "99"
    for pct in range(0, 101):
        assert icon_spec(TrayView(link="on", percent=pct)).label == str(pct)


def test_icon_number_is_white_except_dark_on_yellow():
    for pct, bg, fg in [(87, GREEN, WHITE), (45, YELLOW, DARK), (12, RED, WHITE)]:
        spec = icon_spec(TrayView(link="on", percent=pct))
        assert (spec.bg, spec.fg) == (bg, fg)
    assert icon_spec(TrayView(link="off")).fg == WHITE


def test_icon_spec_charging_shows_the_bolt():
    spec = icon_spec(TrayView(link="on", charging=True))
    assert spec.kind == "charging" and spec.bg == BLUE and spec.label == "" and spec.fg == WHITE


def test_icon_spec_charging_with_an_estimate_shows_a_dimmed_number():
    spec = icon_spec(TrayView(link="on", charging=True, estimate=90, full_in="lleno en ~25 min"))
    assert spec.kind == "charging" and spec.label == "90" and spec.bg == BLUE
    assert spec.approx and spec.fg == APPROX_FG
    assert not icon_spec(TrayView(link="on", charging=True)).approx
    assert not icon_spec(TrayView(link="on", percent=90)).approx
    assert icon_spec(TrayView(link="off", charging=True, estimate=90)).kind == "off"


def test_icon_spec_mute_marker_only_when_muted_and_on():
    assert icon_spec(TrayView(link="on", percent=60, muted=True)).muted
    assert not icon_spec(TrayView(link="on", percent=60, muted=False)).muted
    assert not icon_spec(TrayView(link="on", percent=60, muted=None)).muted
    assert not icon_spec(TrayView(link="off", muted=True)).muted


def test_tooltip_matches_the_plan_example():
    view = TrayView(link="on", percent=85, remaining="~12 h restantes", keepalive="activo")
    assert tooltip_text(view) == "HyperX Cloud Flight · 85 % · ~12 h restantes · Anti-apagado: activo"


def test_tooltip_states():
    assert "cargando" in tooltip_text(TrayView(link="on", charging=True))
    assert "apagado" in tooltip_text(TrayView(link="off"))
    assert "dongle no conectado" in tooltip_text(TrayView(link="no_dongle"))
    assert "buscando" in tooltip_text(TrayView(link="searching"))
    assert "micrófono silenciado" in tooltip_text(TrayView(link="on", percent=50, muted=True))
    assert "calculando…" in tooltip_text(TrayView(link="on", percent=50, remaining="calculando…"))


def test_tooltip_while_charging_with_an_estimate():
    view = TrayView(link="on", charging=True, estimate=90, full_in="lleno en ~25 min", keepalive="activo")
    assert tooltip_text(view) == (
        "HyperX Cloud Flight · cargando · ~90 % (estimado) · lleno en ~25 min · Anti-apagado: activo"
    )
    assert tooltip_text(TrayView(link="on", charging=True, keepalive="activo")) == (
        "HyperX Cloud Flight · cargando · Anti-apagado: activo"
    )


def test_tooltip_fits_the_windows_limit_and_marks_simulation():
    view = TrayView(link="on", percent=85, remaining="x" * 300, simulated=True)
    text = tooltip_text(view)
    assert len(text) <= 127 and text.startswith("[SIMULADO]")


def test_status_text():
    assert status_text(TrayView(link="on", percent=85, remaining="~12 h restantes")) == "Batería: 85 % (~12 h restantes)"
    assert status_text(TrayView(link="off")) == "Auricular apagado"
    assert status_text(TrayView(link="on", charging=True)) == "Cargando…"
    assert status_text(TrayView(link="no_dongle")) == "Dongle USB no conectado"
    assert status_text(TrayView(link="on", charging=True, estimate=90, full_in="lleno en ~25 min")) == (
        "Cargando… ~90 % (estimado) · lleno en ~25 min"
    )


# --------------------------------------------------------------------------
# Icon drawing: rounded square in the state colour + the number drawn at the
# final size (+ bolt while charging, + mute badge)
# --------------------------------------------------------------------------
SIZES = [16, 20, 24, 32, 48]
VIEWS = (
    [TrayView(link="on", percent=p) for p in (100, 85, 45, 7, 0)]
    + [TrayView(link="on", charging=True, estimate=e) for e in (99, 41, 8)]
    + [TrayView(link="on", charging=True), TrayView(link="off"), TrayView(link="on")]
    + [TrayView(link="on", percent=42, muted=True), TrayView(link="on", charging=True, muted=True)]
)
LABELLED = [v for v in VIEWS if icon_spec(v).label]


def _px(spec, size):
    return np.asarray(render_icon(spec, size)).astype(int)


def _background(spec, size):
    """The same icon without number, bolt or badge."""
    return _px(replace(spec, kind="off", label="", muted=False), size)


def _ink(spec, size):
    """Mask of the pixels where the number clearly shows over the background."""
    plain = _px(replace(spec, muted=False), size)
    return np.abs(plain - _background(spec, size)).max(axis=2) >= 64


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("view", VIEWS)
def test_render_icon_has_exact_size_and_content(view, size):
    img = render_icon(icon_spec(view), size)
    assert img.size == (size, size) and img.mode == "RGBA"
    lo, hi = img.getchannel("A").getextrema()
    assert hi == 255                                               # opaque pixels exist (not blank)
    assert lo < 255 or size < 20                                   # rounded corners are transparent


@pytest.mark.parametrize("size", range(12, 65))
def test_render_icon_works_at_any_tray_size(size):
    for view in (VIEWS[0], VIEWS[5], VIEWS[8], VIEWS[-2], VIEWS[-1]):
        assert render_icon(icon_spec(view), size).size == (size, size)


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize(
    "view,color",
    [
        (TrayView(link="on", percent=87), GREEN),
        (TrayView(link="on", percent=100), GREEN),
        (TrayView(link="on", percent=45), YELLOW),
        (TrayView(link="on", percent=12), RED),
        (TrayView(link="on", charging=True), BLUE),
        (TrayView(link="on", charging=True, estimate=60), BLUE),
        (TrayView(link="on", percent=87, muted=True), GREEN),
        (TrayView(link="off"), GRAY),
        (TrayView(link="on"), GRAY),
    ],
)
def test_render_icon_is_a_square_in_the_color_of_the_state(view, color, size):
    img = render_icon(icon_spec(view), size)
    mid = size // 2
    for xy in ((mid, 0), (0, mid), (size - 1, mid)):                # edge midpoints: no number, bolt or badge there
        assert img.getpixel(xy) == color + (255,)
    assert img.getpixel((0, 0))[3] < 255 or size < 20               # rounded corner


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("view", LABELLED)
def test_number_is_drawn_at_the_final_size_in_the_foreground_color(view, size):
    spec = icon_spec(view)
    px = _px(replace(spec, muted=False), size)
    ink = _ink(spec, size)
    assert ink.any()                                               # the number is there
    near = np.abs(px[ink][:, :3] - np.array(spec.fg)).max(axis=1)
    tiny = len(spec.label) > 2 and size < 20                       # "100" at 16 px is a 7 px font: strokes under 1.5 px
    assert near.min() <= (64 if tiny else 16)                      # glyph cores reach the full colour: not a blur
    # every opaque pixel is the background, the number colour, or a blend of the two (no stray colours)
    bg, fg = np.array(spec.bg), np.array(spec.fg)
    opaque = px[px[..., 3] == 255][:, :3]
    t = np.clip(((opaque - bg) @ (fg - bg)) / ((fg - bg) @ (fg - bg)), 0, 1)
    assert np.abs(opaque - (bg + t[:, None] * (fg - bg))).max() <= 3


@pytest.mark.parametrize("size", range(12, 65))
def test_number_stays_inside_the_rounded_square(size):
    for label in ("0", "7", "42", "87", "100", "--", ".."):
        spec = IconSpec("percent", label, GREEN, WHITE)
        ys, xs = np.nonzero(_ink(spec, size))
        assert min(xs.min(), ys.min(), size - 1 - xs.max(), size - 1 - ys.max()) >= 1
        assert _background(spec, size)[..., 3][ys, xs].min() == 255  # never over the transparent corners


@pytest.mark.parametrize("size", SIZES)
def test_number_is_centred_horizontally(size):
    for label in ("87", "45", "12", "100"):
        _ys, xs = np.nonzero(_ink(IconSpec("percent", label, GREEN, WHITE), size))
        assert abs(xs.min() - (size - 1 - xs.max())) <= 2


def test_font_is_11_px_at_16_and_scales_with_the_icon():
    draw = ImageDraw.Draw(Image.new("RGBA", (64, 64)))
    assert _label_font(draw, "87", 16).size == 11
    assert _label_font(draw, "0", 16).size == 11
    assert _label_font(draw, "87", 32).size == 22
    sizes = [_label_font(draw, "87", s).size for s in SIZES]
    assert sizes == sorted(sizes) and len(set(sizes)) == len(SIZES)


@pytest.mark.parametrize("size", range(12, 65))
def test_font_shrinks_until_the_label_fits(size):
    draw = ImageDraw.Draw(Image.new("RGBA", (64, 64)))
    two, three = _label_font(draw, "88", size), _label_font(draw, "100", size)
    assert three.size < two.size                                   # 100 % shrinks to fit
    for label, font in (("88", two), ("100", three)):
        assert draw.textlength(label, font=font) <= size - 2 or font.size == 6


def test_icon_still_draws_when_no_font_file_is_found(monkeypatch):
    real = tray.ImageFont.truetype

    def no_system_fonts(font, *args, **kwargs):
        if isinstance(font, str):                                  # a file name: none of them exists
            raise OSError("no such font")
        return real(font, *args, **kwargs)                         # Pillow's bundled fallback is a stream

    monkeypatch.setattr(tray.ImageFont, "truetype", no_system_fonts)
    spec = icon_spec(TrayView(link="on", percent=87))
    assert render_icon(spec, 16).size == (16, 16) and _ink(spec, 16).any()


@pytest.mark.parametrize("size", [16, 20, 24, 28, 32, 48])
def test_mute_badge_is_visible_and_keeps_the_number_readable(size):
    worst = 1.0
    for pct in range(0, 101):
        spec = icon_spec(TrayView(link="on", percent=pct, muted=True))
        assert spec.muted
        muted = _px(spec, size)
        assert (muted[..., :3] == MUTE_RED).all(axis=-1).any()      # the red dot is there
        plain = _px(replace(spec, muted=False), size)
        ink = _ink(spec, size)
        worst = min(worst, (muted[ink] == plain[ink]).all(axis=1).mean())
    assert worst >= 0.85                                           # the badge hides at most ~15 % of any number


def test_charging_without_an_estimate_is_a_white_bolt_on_blue():
    px = _px(icon_spec(TrayView(link="on", charging=True)), 32)
    white = (px[..., :3] == WHITE).all(axis=-1)
    assert white.sum() > 32 * 32 // 10                              # a solid bolt, not a speck
    assert not white[:2].any() and not white[-2:].any()             # inside the square
    assert (px[..., :3] == BLUE).all(axis=-1).sum() > white.sum()


def test_estimated_icon_is_a_dimmed_number_without_a_bolt():
    est = icon_spec(TrayView(link="on", charging=True, estimate=60))
    bolt = icon_spec(TrayView(link="on", charging=True))
    assert _px(est, 32).tobytes() != _px(bolt, 32).tobytes()
    px = _px(est, 32)
    assert not (px[..., :3] == WHITE).all(axis=-1).any()           # no pure white: reads as approximate, no bolt
    assert (px[..., :3] == APPROX_FG).all(axis=-1).any()           # the number is in the light blue
    background = _background(est, 32)
    assert (background[..., :3][background[..., 3] == 255] == BLUE).all()
