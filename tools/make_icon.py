"""Genera el ícono del .exe (build\\HyperXBattery.ico) con el mismo dibujo de la bandeja.

Uso: python tools\\make_icon.py [ruta_de_salida.ico]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hyperx_battery.tray import GREEN, WHITE, IconSpec, render_icon  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "HyperXBattery.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    big = render_icon(IconSpec("percent", "HX", GREEN, WHITE), 256)
    big.save(out, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Ícono escrito en {out}")


if __name__ == "__main__":
    main()
