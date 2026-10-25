import numpy as np
import pytest

from hyperx_battery.tray import (
    APPROX_FG,
    BLUE,
    GRAY,
    GREEN,
    RED,
    YELLOW,
    TrayView,
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
        assert spec.bg == GRAY and spec.kind == "off"


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
    assert img.getpixel((3, 16))[:3] == GREEN


def test_estimated_icon_differs_from_the_plain_bolt_and_digits_are_dimmer_than_measured():
    plain = render_icon(icon_spec(TrayView(link="on", charging=True)), 32)
    est = render_icon(icon_spec(TrayView(link="on", charging=True, estimate=90)), 32)
    assert plain.tobytes() != est.tobytes()
    px = np.asarray(est).astype(int)
    brightest = px[px[..., 3] == 255][:, :3].sum(axis=1).max()
    assert brightest < 3 * 255                                      # no pure-white pixel: reads as approximate
