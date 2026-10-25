"""Battery history window: a line chart drawn on a tkinter Canvas (no
matplotlib). Must only be created / used from the tkinter main thread.
"""

from __future__ import annotations

import bisect
import logging
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Callable, List, Optional, Tuple

from .history import HistoryStore, Sample, charging_spans, split_segments, time_ticks

log = logging.getLogger(__name__)

RANGES = {"24 h": 24 * 3600.0, "7 días": 7 * 24 * 3600.0}
REFRESH_MS = 30_000

BG = "#ffffff"
GRID = "#e5e7eb"
AXIS = "#6b7280"
LINE = "#2563eb"
CHARGE_BAND = "#dcfce7"
LOW_BAND = "#fee2e2"


class HistoryWindow:
    def __init__(
        self,
        root: tk.Tk,
        store: HistoryStore,
        gap_s: float = 180.0,
        now: Callable[[], float] = time.time,
        extra_info: Callable[[], str] = lambda: "",
    ) -> None:
        self._root = root
        self._store = store
        self._gap_s = gap_s
        self._now = now
        self._extra_info = extra_info
        self._win: Optional[tk.Toplevel] = None
        self._canvas: Optional[tk.Canvas] = None
        self._info: Optional[tk.StringVar] = None
        self._range = tk.StringVar(master=root, value="24 h")
        self._after_id: Optional[str] = None
        self._resize_id: Optional[str] = None
        self._points: List[Tuple[float, float, Sample]] = []    # (x pixel, y pixel, sample)
        self._xs: List[float] = []

    # -- lifecycle ------------------------------------------------------------------
    def show(self) -> None:
        if self._win is not None and self._win.winfo_exists():
            self._win.deiconify()
            self._win.lift()
            self._win.focus_force()
            self._redraw()
            return
        win = tk.Toplevel(self._root)
        win.title("Historial de batería · HyperX Cloud Flight")
        win.geometry("780x440")
        win.minsize(420, 280)
        win.configure(bg=BG)
        win.protocol("WM_DELETE_WINDOW", self._close)
        self._win = win

        bar = ttk.Frame(win, padding=(10, 8))
        bar.pack(fill="x")
        for label in RANGES:
            ttk.Radiobutton(bar, text=label, value=label, variable=self._range,
                            command=self._redraw).pack(side="left", padx=(0, 10))
        self._info = tk.StringVar(master=win, value="")
        ttk.Label(bar, textvariable=self._info).pack(side="right")

        canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.bind("<Configure>", self._on_resize)
        canvas.bind("<Motion>", self._on_motion)
        canvas.bind("<Leave>", lambda _e: self._canvas and self._canvas.delete("hover"))
        self._canvas = canvas
        self._schedule_refresh()
        win.after(50, self._redraw)

    def _close(self) -> None:
        if self._after_id is not None and self._win is not None:
            try:
                self._win.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self._after_id = None
        if self._win is not None:
            self._win.destroy()
        self._win = None
        self._canvas = None

    def _schedule_refresh(self) -> None:
        if self._win is None:
            return
        self._after_id = self._win.after(REFRESH_MS, self._tick)

    def _tick(self) -> None:
        if self._win is None or not self._win.winfo_exists():
            return
        self._redraw()
        self._schedule_refresh()

    def _on_resize(self, _event: tk.Event) -> None:
        if self._win is None:
            return
        if self._resize_id is not None:
            self._win.after_cancel(self._resize_id)
        self._resize_id = self._win.after(60, self._redraw)

    # -- drawing --------------------------------------------------------------------
    def _redraw(self) -> None:
        canvas = self._canvas
        if canvas is None or not canvas.winfo_exists():
            return
        self._resize_id = None
        canvas.delete("all")
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if w < 100 or h < 100:
            return
        left, right, top, bottom = 48, 16, 14, 32
        pw, ph = w - left - right, h - top - bottom

        t1 = self._now()
        t0 = t1 - RANGES[self._range.get()]
        try:
            samples = self._store.range(t0, t1)
        except Exception:  # noqa: BLE001
            log.exception("Cannot read history")
            samples = []

        def x_of(ts: float) -> float:
            return left + (ts - t0) / (t1 - t0) * pw

        def y_of(pct: float) -> float:
            return top + (1 - pct / 100.0) * ph

        # background bands: low battery zone, charging spans
        canvas.create_rectangle(left, y_of(20), left + pw, y_of(0), fill=LOW_BAND, outline="")
        for a, b in charging_spans(samples, self._gap_s):
            canvas.create_rectangle(x_of(a), top, max(x_of(b), x_of(a) + 2), top + ph,
                                    fill=CHARGE_BAND, outline="")

        # grid + y labels
        for pct in range(0, 101, 20):
            y = y_of(pct)
            canvas.create_line(left, y, left + pw, y, fill=GRID)
            canvas.create_text(left - 6, y, text=f"{pct} %", anchor="e", fill=AXIS, font=("Segoe UI", 8))
        # x ticks
        fmt = "%H:%M" if t1 - t0 <= 36 * 3600 else "%d/%m"
        for ts in time_ticks(t0, t1):
            x = x_of(ts)
            canvas.create_line(x, top, x, top + ph, fill=GRID)
            canvas.create_text(x, top + ph + 14, text=datetime.fromtimestamp(ts).strftime(fmt),
                               fill=AXIS, font=("Segoe UI", 8))
        canvas.create_rectangle(left, top, left + pw, top + ph, outline=AXIS)

        # data
        self._points = []
        segments = split_segments(samples, self._gap_s)
        stride = max(1, int(sum(len(s) for s in segments) / max(1, pw * 2)))
        for seg in segments:
            pts = seg[::stride]
            if pts[-1] is not seg[-1]:
                pts = pts + [seg[-1]]
            coords: List[float] = []
            for s in pts:
                x, y = x_of(s.ts), y_of(s.percent or 0)
                coords += [x, y]
                self._points.append((x, y, s))
            if len(coords) >= 4:
                canvas.create_line(*coords, fill=LINE, width=2, joinstyle="round")
            elif len(coords) == 2:
                canvas.create_oval(coords[0] - 2, coords[1] - 2, coords[0] + 2, coords[1] + 2,
                                   fill=LINE, outline=LINE)
        self._points.sort(key=lambda p: p[0])
        self._xs = [p[0] for p in self._points]

        if not samples:
            canvas.create_text(left + pw / 2, top + ph / 2, fill=AXIS, font=("Segoe UI", 11),
                               text="Todavía no hay lecturas en este período.")
        self._update_info(samples)

    def _update_info(self, samples: List[Sample]) -> None:
        if self._info is None:
            return
        last = next((s for s in reversed(samples) if s.percent is not None), None)
        text = ""
        if last is not None:
            when = datetime.fromtimestamp(last.ts).strftime("%d/%m %H:%M")
            text = f"Última lectura: {last.percent} % ({when})"
        extra = self._extra_info()
        if extra:
            text = f"{text} · {extra}" if text else extra
        self._info.set(text)

    def _on_motion(self, event: tk.Event) -> None:
        canvas = self._canvas
        if canvas is None or not self._xs:
            return
        canvas.delete("hover")
        i = bisect.bisect_left(self._xs, event.x)
        candidates = [j for j in (i - 1, i) if 0 <= j < len(self._points)]
        if not candidates:
            return
        j = min(candidates, key=lambda k: abs(self._xs[k] - event.x))
        x, y, s = self._points[j]
        if abs(x - event.x) > 30:
            return
        canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=LINE, outline="white", width=2, tags="hover")
        label = f"{datetime.fromtimestamp(s.ts):%d/%m %H:%M} · {s.percent} % · {s.mv} mV"
        tx = min(max(x, 130), canvas.winfo_width() - 130)
        text_id = canvas.create_text(tx, 10, text=label, fill="#111827", font=("Segoe UI", 9, "bold"),
                                     tags="hover", anchor="n")
        x0, y0, x1, y1 = canvas.bbox(text_id)
        box = canvas.create_rectangle(x0 - 5, y0 - 2, x1 + 5, y1 + 2, fill="#ffffff", outline=GRID,
                                      tags="hover")
        canvas.tag_lower(box, text_id)
