"""Smoke test of the tkinter history window (skipped if tkinter cannot start)."""
import tkinter as tk

import pytest

from hyperx_battery.history import HistoryStore, Sample
from hyperx_battery.history_window import HistoryWindow


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for tkinter")
    r.withdraw()
    yield r
    r.destroy()


def pump(root, seconds=0.3):
    import time

    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def test_window_draws_line_bands_and_survives_empty_data(root):
    now = 1_800_000_000.0
    store = HistoryStore(":memory:")
    win = HistoryWindow(root, store, now=lambda: now, extra_info=lambda: "~12 h restantes")
    win.show()
    pump(root)                                         # empty database
    canvas = win._canvas
    assert canvas is not None
    assert any("Todavía no hay lecturas" in str(canvas.itemcget(i, "text"))
               for i in canvas.find_all() if canvas.type(i) == "text")

    for m in range(0, 600):                            # 10 h discharge, then a charge
        store.add(Sample(now - (600 - m) * 60.0, 3900 - m // 3, 80 - m // 10, False))
    for m in range(0, 30):
        store.add(Sample(now - 1800 + m * 60.0, 4200, None, True))
    win._range.set("24 h")
    win._redraw()
    pump(root, 0.1)
    kinds = [canvas.type(i) for i in canvas.find_all()]
    assert "line" in kinds and "rectangle" in kinds
    assert "Última lectura" in win._info.get() and "~12 h restantes" in win._info.get()

    win._range.set("7 días")
    win._redraw()
    pump(root, 0.1)
    assert canvas.find_all()


def test_show_twice_reuses_the_window(root):
    store = HistoryStore(":memory:")
    win = HistoryWindow(root, store)
    win.show()
    first = win._win
    win.show()
    assert win._win is first
    win._close()
    assert win._win is None
    win.show()                                         # can be reopened after closing
    assert win._win is not None and win._win is not first
    win._close()
