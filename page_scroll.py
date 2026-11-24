"""2.4.0 fit-in F1c (#109): main pages that scroll when the window is shorter than they need.

At a large Text size on a smaller screen (150 % on an iMac) the Input page lost its Time zone row and the whole Optics
box below the window's edge, with no way to reach them, and the Output page crushed its plots. A PageScroll is the
page's own frame: the page is built into .body, which fills the window while it fits (plots and other stretchy parts
grow into the room, as before) and is never shorter than the page asks for; when the window is shorter, a scroll bar
appears and the mouse wheel or trackpad scrolls the page.

Plots and images keep a minimum height (min_plot_height), so on a short window the page scrolls instead of squeezing
them; above that height they still grow with the window as before.

The wheel scrolls the page only over its ordinary parts (labels, frames, buttons, boxes). Over a plot or an image, a
list, a table, a text box or a drop-down, the wheel keeps its own job there (zoom, list scrolling), and inside a body
that already scrolls on its own (the Photometry side panel) that body scrolls.
"""

from __future__ import annotations

import sys
from tkinter import Canvas, ttk

# Widgets that keep the wheel for themselves (plots and images are Tk canvases; Treeview, Text, Listbox, Combobox).
WHEEL_KEEPERS = ("Canvas", "Text", "Listbox", "Treeview", "TCombobox", "Scrollbar", "TScrollbar")
POLL_MS = 400


def wheel_units(delta, platform: str | None = None) -> int:
    """Scroll steps for one wheel event: Windows sends multiples of 120 per notch; a Mac sends small numbers (a
    trackpad many of them), each one step. Down the page is positive."""
    try:
        d = float(delta)
    except (TypeError, ValueError):
        return 0
    if d == 0:
        return 0
    plat = platform if platform is not None else sys.platform
    if plat == "darwin":
        return -1 if d > 0 else 1
    n = int(-d / 120)
    if n == 0:
        n = -1 if d > 0 else 1
    return n


def body_height(view_height: int, wanted_height: int) -> int:
    """The body fills the view while the page fits and is never shorter than the page asks for."""
    return max(int(view_height), int(wanted_height), 1)


def needs_scroll(view_height: int, wanted_height: int) -> bool:
    return int(wanted_height) > int(view_height) + 1


class PageScroll(ttk.Frame):
    """One main page. Build the page into .body; place this frame where the page goes (as before)."""

    def __init__(self, parent, bg: str = "#ffffff", body_style: str | None = None, **kw):
        super().__init__(parent, **kw)
        self.canvas = Canvas(self, highlightthickness=0, borderwidth=0, background=bg)
        if sys.platform == "darwin":
            try:   # a trackpad sends many small wheel events (as ScrollBody)
                self.canvas.configure(yscrollincrement=16)
            except Exception:
                pass
        else:
            try:
                self.canvas.configure(yscrollincrement=24)
            except Exception:
                pass
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, style=body_style) if body_style else ttk.Frame(self.canvas)
        self._item = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self._bar)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.refit(), add="+")
        self._last = None
        self._tag = f"PageScrollWheel{id(self)}"
        self._tagged = set()
        self.bind_class(self._tag, "<MouseWheel>", self._wheel)
        if sys.platform.startswith("linux"):
            self.bind_class(self._tag, "<Button-4>", lambda e: self._wheel(e, units=-1))
            self.bind_class(self._tag, "<Button-5>", lambda e: self._wheel(e, units=1))
        self.after(POLL_MS, self._poll)

    # -- size --------------------------------------------------------------------------------------------------------
    def wanted_height(self) -> int:
        try:
            return int(self.body.winfo_reqheight())
        except Exception:
            return 1

    def scrollable(self) -> bool:
        try:
            return needs_scroll(int(self.canvas.winfo_height()), self.wanted_height())
        except Exception:
            return False

    def refit(self):
        """Body as wide as the view, as tall as the view or the page's own need, whichever is more."""
        try:
            w = max(1, int(self.canvas.winfo_width()))
            vh = max(1, int(self.canvas.winfo_height()))
            want = self.wanted_height()
        except Exception:
            return
        key = (w, vh, want)
        if key == self._last:
            return
        self._last = key
        h = body_height(vh, want)
        try:
            self.canvas.itemconfigure(self._item, width=w, height=h)
            self.canvas.configure(scrollregion=(0, 0, w, h))
            if not needs_scroll(vh, want):
                self.canvas.yview_moveto(0)
        except Exception:
            pass

    def _bar(self, first, last):
        try:
            whole = float(first) <= 0.0 and float(last) >= 1.0
        except (TypeError, ValueError):
            whole = True
        try:
            if whole:
                if self.vbar.winfo_manager():
                    self.vbar.pack_forget()
            elif not self.vbar.winfo_manager():
                self.vbar.pack(side="right", fill="y", before=self.canvas)
            self.vbar.set(first, last)
        except Exception:
            pass

    def _poll(self):
        """The page's own need changes with its contents (a pop-up row, a longer note): check it now and then while the
        page is on screen. One size read, cheap."""
        try:
            if self.winfo_ismapped():
                self.refit()
                if self.scrollable():
                    self._tag_children()
        except Exception:
            pass
        try:
            self.after(POLL_MS, self._poll)
        except Exception:
            pass

    # -- wheel -------------------------------------------------------------------------------------------------------
    def _keeps_wheel(self, widget) -> bool:
        """A widget that has its own use for the wheel, or one inside a body that scrolls on its own."""
        try:
            if str(widget.winfo_class()) in WHEEL_KEEPERS:
                return True
        except Exception:
            return True
        return False

    def _tag_children(self):
        """Give the page's ordinary parts the page's wheel binding (new parts as they appear). Stops at widgets that
        keep the wheel and does not go into them (a nested scrolling body is a Canvas)."""
        stack = [self.body]
        while stack:
            w = stack.pop()
            if self._keeps_wheel(w):
                continue
            key = str(w)
            if key not in self._tagged:
                try:
                    tags = list(w.bindtags())
                    if self._tag not in tags:
                        w.bindtags((self._tag,) + tuple(tags))
                    self._tagged.add(key)
                except Exception:
                    pass
            try:
                stack.extend(w.winfo_children())
            except Exception:
                pass

    def _wheel(self, event, units=None):
        if not self.scrollable():
            return None
        n = units if units is not None else wheel_units(getattr(event, "delta", 0))
        if n:
            try:
                self.canvas.yview_scroll(n, "units")
            except Exception:
                pass
        return "break"


def min_plot_height(widget, base_px: int, scale: float = 1.0, width_px: int | None = None):
    """Ask for at least `base_px` (times the text scale) of height for a plot's or image's Tk widget, so a short page
    scrolls instead of squeezing it; it still grows with the window above that.

    Matplotlib sets the widget's requested size to the figure's own size when the screen's pixel ratio changes (on
    the first showing on Windows and Linux, or on a move to another screen). A page that only squeezed did not mind;
    a page that scrolls would then scroll for a plot that asks for its full figure size, so the request is set again
    right after that (and `width_px`, when given, as asked before)."""
    want_h = max(1, int(round(base_px * float(scale))))

    def ask(_event=None):
        try:
            if width_px is not None:
                widget.configure(width=int(width_px), height=want_h)
            else:
                widget.configure(height=want_h)
        except Exception:
            pass

    ask()
    try:
        widget.bind("<Map>", lambda _e: widget.after_idle(ask), add="+")
    except Exception:
        pass
    try:
        widget._shobs_min_height = want_h   # for the tests
    except Exception:
        pass
    return want_h
