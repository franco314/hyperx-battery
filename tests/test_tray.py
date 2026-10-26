import numpy as np
import pytest

from hyperx_battery.tray import (
    APPROX_FG,
    BLUE,
    GRAY,
    GREEN,
    MUTE_RED,
    RED,
    WHITE,
    YELLOW,
    IconSpec,
    TrayView,
    _seg_metrics,
    icon_spec,
    label_rects,
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
        assert spec.bg == GRAY and spec.kind == "off" and spec.label == "-"


def test_icon_spec_on_without_a_reading_yet_is_a_distinct_gray():
    spec = icon_spec(TrayView(link="on"))
    assert spec.kind == "off" and spec.bg == GRAY and spec.label == "_"
    assert spec != icon_spec(TrayView(link="off"))


def test_icon_labels_never_need_more_than_two_glyphs():
    assert icon_spec(TrayView(link="on", percent=100)).label == "F"
    assert icon_spec(TrayView(link="on", percent=7)).label == "7"
    assert icon_spec(TrayView(link="on", percent=0)).label == "0"
    assert icon_spec(TrayView(link="on", percent=100)).bg == GREEN
    assert icon_spec(TrayView(link="on", charging=True, estimate=99)).label == "99"
    for pct in range(0, 101):
        assert len(icon_spec(TrayView(link="on", percent=pct)).label) <= 2


def test_icon_spec_charging_shows_the_bolt():
    spec = icon_spec(TrayView(link="on", charging=True))
    assert spec.kind == "charging" and spec.bg == BLUE and spec.label == ""


def test_icon_spec_charging_with_an_estimate_shows_dimmed_digits():
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


@pytest.mark.parametrize("size", [16, 20, 24, 32, 48])
@pytest.mark.parametrize(
    "view",
    [TrayView(link="on", percent=p) for p in (100, 85, 7, 0)]
    + [TrayView(link="on", charging=True, estimate=e) for e in (99, 41, 8)]
    + [TrayView(link="on", charging=True), TrayView(link="off"), TrayView(link="on", percent=42, muted=True)],
)
def test_render_icon_has_exact_size_and_content(view, size):
    img = render_icon(icon_spec(view), size)
    assert img.size == (size, size) and img.mode == "RGBA"
    lo, hi = img.getchannel("A").getextrema()
    assert hi == 255                                               # opaque pixels exist (not blank)
    assert lo < 255 or size < 20                                   # rounded corners are transparent


def test_render_icon_uses_the_background_color():
    img = render_icon(icon_spec(TrayView(link="on", percent=85)), 32)
    assert img.getpixel((16, 2)) == GREEN + (255,)                  # above the digits


def test_estimated_icon_differs_from_the_plain_bolt_and_digits_are_dimmer_than_measured():
    plain = render_icon(icon_spec(TrayView(link="on", charging=True)), 32)
    est = render_icon(icon_spec(TrayView(link="on", charging=True, estimate=90)), 32)
    assert plain.tobytes() != est.tobytes()
    px = np.asarray(est).astype(int)
    brightest = px[px[..., 3] == 255][:, :3].sum(axis=1).max()
    assert brightest < 3 * 255                                      # no pure-white pixel: reads as approximate


# --------------------------------------------------------------------------
# Seven-segment digits are drawn on the pixel grid
# --------------------------------------------------------------------------
def _digit_mask(label, size):
    mask = np.zeros((size, size), dtype=bool)
    for x0, y0, x1, y1 in label_rects(label, size):
        assert 0 <= x0 < x1 <= size and 0 <= y0 < y1 <= size      # inside the icon
        mask[y0:y1, x0:x1] = True
    return mask


CRISP_VIEWS = (
    [TrayView(link="on", percent=p) for p in (100, 87, 45, 20, 12, 7, 1, 0)]
    + [TrayView(link="on", charging=True, estimate=e) for e in (99, 60, 8)]
    + [TrayView(link="off"), TrayView(link="on")]
)


@pytest.mark.parametrize("size", [16, 20, 24, 28, 32, 48])
@pytest.mark.parametrize("view", CRISP_VIEWS)
def test_digits_are_opaque_solid_pixels_with_nothing_blurred_around_them(view, size):
    spec = icon_spec(view)
    px = np.asarray(render_icon(spec, size))
    mask = _digit_mask(spec.label, size)
    assert mask.any()
    assert (px[mask] == np.array(spec.fg + (255,))).all()          # pure colour, alpha 255: no antialiasing
    outside = px[~mask]
    opaque = outside[outside[..., 3] == 255]
    assert (opaque[:, :3] == np.array(spec.bg)).all()              # no fringe: just the background colour


@pytest.mark.parametrize("size", [16, 20, 24, 28, 32, 48])
def test_digits_stay_inside_the_rounded_square(size):
    for pct in (0, 8, 88, 100):
        spec = icon_spec(TrayView(link="on", percent=pct))
        empty = IconSpec(spec.kind, "", spec.bg, spec.fg)
        bg_alpha = np.asarray(render_icon(empty, size))[..., 3]
        assert bg_alpha[_digit_mask(spec.label, size)].min() >= 192   # never in the transparent corners


def test_sixteen_pixel_design():
    # 7 x 12 digits, 2-px strokes, 1-px gap: two digits take 15 columns and rows 2..13
    m = _seg_metrics(16)
    assert (m.stroke, m.width, m.height, m.gap) == (2, 7, 12, 1)
    rects = label_rects("88", 16)
    assert min(r[0] for r in rects) == 0 and max(r[2] for r in rects) == 15
    assert min(r[1] for r in rects) == 2 and max(r[3] for r in rects) == 14
    assert _digit_mask("88", 16).sum() == 2 * (7 * 12 - 2 * 3 * 3)  # each 8: 7 x 12 minus its two 3 x 3 holes


@pytest.mark.parametrize("k", [2, 3, 4, 16])
def test_multiples_of_16_scale_the_16_px_design_exactly(k):
    base, m = _seg_metrics(16), _seg_metrics(16 * k)
    assert (m.stroke, m.width, m.height, m.gap) == (base.stroke * k, base.width * k, base.height * k, base.gap * k)

    def from_left_edge(rects):                                     # the margins are centred, not scaled
        left = min(r[0] for r in rects)
        return sorted((x0 - left, y0, x1 - left, y1) for x0, y0, x1, y1 in rects)

    scaled = [(x0 * k, y0 * k, x1 * k, y1 * k) for x0, y0, x1, y1 in label_rects("2F", 16)]
    assert from_left_edge(label_rects("2F", 16 * k)) == from_left_edge(scaled)


@pytest.mark.parametrize("size", range(12, 65))
def test_digit_geometry_fits_every_tray_size(size):
    m = _seg_metrics(size)
    assert m.stroke >= (2 if size >= 16 else 1)
    assert m.width - 2 * m.stroke >= 2                             # the holes of 0, 6, 8, 9 stay open
    assert m.height == 3 * m.stroke + 2 * m.run <= size - 2
    for label in ("88", "F", "-", "_", "1", "11"):
        ink = np.flatnonzero(_digit_mask(label, size).any(axis=0))  # also asserts the bounds
        assert 0 <= (size - 1 - ink[-1]) - ink[0] <= 1             # centred (the odd pixel goes to the right)


def test_seven_segment_glyphs():
    def ink(ch):
        return {tuple(r) for r in label_rects(ch, 16)}

    assert len({frozenset(ink(c)) for c in "0123456789F-_"}) == 13  # every glyph is different
    assert ink("8") > ink("0") and ink("8") > ink("6") and ink("8") > ink("9")
    with pytest.raises(ValueError):
        label_rects("H", 16)


@pytest.mark.parametrize("size", [16, 20, 24, 28, 32, 48])
def test_mute_badge_is_visible_and_keeps_the_digits_readable(size):
    worst = 1.0
    for pct in range(0, 101):
        spec = icon_spec(TrayView(link="on", percent=pct, muted=True))
        assert spec.muted
        px = np.asarray(render_icon(spec, size))
        assert (px[..., :3] == MUTE_RED).all(axis=-1).any()        # the red dot is there
        mask = _digit_mask(spec.label, size)
        shown = (px[mask][:, :3] == np.array(spec.fg)).all(axis=1).mean()
        worst = min(worst, shown)
    assert worst >= 0.85                                           # the badge hides at most ~15 % of any number


def test_estimated_icon_has_no_bolt_behind_the_digits():
    est = icon_spec(TrayView(link="on", charging=True, estimate=60))
    px = np.asarray(render_icon(est, 32))
    rest = ~_digit_mask(est.label, 32)
    opaque = px[rest & (px[..., 3] == 255)]
    assert (opaque[:, :3] == BLUE).all()                           # only the blue background around the digits
    bolt = np.asarray(render_icon(icon_spec(TrayView(link="on", charging=True)), 32))
    assert (bolt[..., :3] == WHITE).all(axis=-1).any()             # the plain charging icon keeps its bolt
