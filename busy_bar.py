"""2.2.20 (busy-bar house rule, all SHOBS apps): the ONE indicator for every delay.

Copied from SHOBS-SCHED 1.2.0 (style.BusyBar) so all SHOBS apps look and behave alike: a back-and-forth
(indeterminate) ttk bar with words beside it, at the left end of the bottom status strip (dialogs keep their own
bar in their own lower left). Multi-step jobs keep the same bar and change the words; when the count is known the
words carry it ("Calibrating frames… 12 of 38"), still back-and-forth, never a filling bar.

    tok = bar.begin("Looking up TOI-2046 at SIMBAD and VSX…")
    bar.words(tok, "Looking up planet TOI-2046 b at the NASA Exoplanet Archive…")
    bar.end(tok)          # idempotent; the caller then writes its ✔ / ✖ line

Several jobs at once: the newest one's words show; the bar goes when none is left. A job shorter than
SHOW_AFTER_MS never flashes the bar. All calls must come from the Tk thread (use widget.after from workers).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def _px(widget, n: float) -> int:
    """n pixels at 96 dpi, scaled for the display (high-DPI Windows)."""
    try:
        scale = max(float(widget.tk.call("tk", "scaling")) / 1.3333, 1.0)
    except Exception:
        scale = 1.0
    return int(round(n * scale))


class BusyBar(ttk.Frame):
    """The window's (or a dialog's) busy bar. begin(words) → token; words(token, text) changes them; end(token)
    ends that job (idempotent). Several jobs at once: the newest one's words show; the bar goes when none is left."""

    SHOW_AFTER_MS = 250          # a job shorter than this never flashes the bar

    def __init__(self, parent, label_style: str = "Card.TLabel", frame_style: str = "Card.TFrame",
                 bar_style: str = "Card.Horizontal.TProgressbar"):
        super().__init__(parent, style=frame_style)
        self.bar = ttk.Progressbar(self, mode="indeterminate", length=_px(parent, 110), style=bar_style)
        self.var = tk.StringVar(master=self)
        self.label = ttk.Label(self, textvariable=self.var, style=label_style)
        self.jobs: dict[int, str] = {}           # token → words, oldest first
        self._next = 1
        self._show_job = None
        self.shown = False

    def begin(self, words: str) -> int:
        tok = self._next
        self._next += 1
        self.jobs[tok] = words
        self._refresh()
        return tok

    def words(self, tok, text: str) -> None:
        if tok in self.jobs:
            self.jobs[tok] = text
            self._refresh()

    def end(self, tok) -> None:
        if self.jobs.pop(tok, None) is not None:
            self._refresh()

    def end_all(self) -> None:
        self.jobs.clear()
        self._refresh()

    @property
    def busy(self) -> bool:
        return bool(self.jobs)

    @property
    def text(self) -> str:
        """The words now showing ("" when idle); tests read this."""
        return list(self.jobs.values())[-1] if self.jobs else ""

    def _refresh(self) -> None:
        try:
            if self.jobs:
                self.var.set(self.text)
                if not self.shown and self._show_job is None:
                    self._show_job = self.after(self.SHOW_AFTER_MS, self._show)
            else:
                if self._show_job is not None:
                    self.after_cancel(self._show_job)
                    self._show_job = None
                self._hide()
        except tk.TclError:
            pass

    def _show(self) -> None:
        self._show_job = None
        if not self.jobs or self.shown:
            return
        try:
            self.bar.pack(side=tk.LEFT, padx=(0, _px(self, 6)))
            self.label.pack(side=tk.LEFT, padx=(0, _px(self, 12)))
            self.bar.start(15)
            self.shown = True
        except tk.TclError:
            pass

    def _hide(self) -> None:
        if not self.shown:
            return
        try:
            self.bar.stop()
            self.bar.pack_forget()
            self.label.pack_forget()
            self.var.set("")
        except tk.TclError:
            pass
        self.shown = False
