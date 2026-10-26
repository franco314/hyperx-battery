"""Genera el ícono del .exe (build\\HyperXBattery.ico) con el mismo dibujo de la bandeja.

Uso: python tools\\make_icon.py [ruta_de_salida.ico]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hyperx_battery.tray import GREEN, WHITE, IconSpec, render_icon  # noqa: E402

# The exe shows the tray's soft green "85": it reads at 16 px, where "HX" would have to shrink to fit.
SPEC = IconSpec("percent", "85", GREEN, WHITE)
# Every frame is drawn at its own size (not downscaled from 256), so the number is hinted and crisp in each one.
SIZES = (16, 20, 24, 28, 32, 40, 48, 64, 128, 256)


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "HyperXBattery.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = [render_icon(SPEC, size) for size in SIZES]
    frames[-1].save(out, format="ICO", sizes=[(size, size) for size in SIZES], append_images=frames[:-1])
    print(f"Ícono escrito en {out}")


if __name__ == "__main__":
    main()
