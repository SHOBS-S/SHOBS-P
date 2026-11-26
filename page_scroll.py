"""2.4.0 fit-in F1c (#109): main pages that scroll when the window is shorter than they need.

At a large Text size on a smaller screen (150 % on an iMac) the Input page lost its Time zone row and the whole Optics
box below the window's edge, with no way to reach them, and the Output page crushed its plots. A PageScroll is the
page's own frame: the page is built into .body, which fills the window while it fits (plots and other stretchy parts
grow into the room, as before) and is never shorter than the page asks for; when the window is shorter, a scroll bar
appears and the mouse wheel or trackpad scrolls the page.

Plots and images keep a minimum height (min_plot_height), so on a short window the page scrolls instead of squeezing
them; above that height they still grow with the window as before.

The wheel scrolls the page over its ordinary parts (labels, frames, buttons, entry boxes, drop-downs). Over a plot or
an image, a list, a table or a text box the wheel keeps its own job there (zoom, list scrolling), and inside a body
that already scrolls on its own (the Photometry side panel) that body scrolls. A drop-down (Binning, Mount, Time zone,
Text size) never changes its value under the wheel: a scroll that passes over one must not change a setting (review
M1); its list, once opened, still scrolls.

A box that gets the keyboard focus below or above the visible part (Tab, or SHOBS-P pointing at the box to fix) is
scrolled into view (review S3).
"""

from __future__ import annotations

import sys
from tkinter import Canvas, ttk

# Widgets that keep the wheel for themselves (plots and images are Tk canvases; Text, Listbox, Treeview, scroll bars).
WHEEL_KEEPERS = ("Canvas", "Text", "Listbox", "Treeview", "Scrollbar", "TScrollbar")
# Widgets whose own wheel binding changes a value: the page's binding takes the wheel from them, always.
WHEEL_NO_VALUE = ("TCombobox", "TSpinbox", "Spinbox")
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


TOUCHPAD_PX_PER_UNIT = 20   # a touchpad's pixels for one scroll step (about one yscrollincrement)


def touchpad_dy(delta) -> int:
    """2.4.0 fit-in F4 (F1c leftover): the vertical part of a Tk 8.7 / 9 <TouchpadScroll> event's %D (two signed
    16-bit numbers packed as dx << 16 | dy; tk::PreciseScrollDeltas). Positive = fingers moving down (content
    down, as a wheel turned up). 0 for anything unreadable."""
    try:
        d = int(delta)
    except (TypeError, ValueError):
        return 0
    dy = d & 0xFFFF
    return dy - 0x10000 if dy >= 0x8000 else dy


def touchpad_units(delta, carry: list) -> int:
    """Scroll steps for one <TouchpadScroll> event; carry is a one-item list holding the pixels not yet spent, so
    many small events add up to whole steps. Down the page is positive (as wheel_units)."""
    carry[0] -= touchpad_dy(delta)
    n = int(carry[0] / TOUCHPAD_PX_PER_UNIT)
    carry[0] -= n * TOUCHPAD_PX_PER_UNIT
    return n


def bind_touchpad(widget, tag_or_all, handler) -> bool:
    """Bind <TouchpadScroll> (Tk 8.7 / 9: a Mac trackpad no longer sends <MouseWheel>) where this Tk knows it.
    tag_or_all: a bind tag, or None for bind_all. False on Tk 8.6 (no such event), where nothing is bound."""
    try:
        if tag_or_all is None:
            widget.bind_all("<TouchpadScroll>", handler)
        else:
            widget.bind_class(tag_or_all, "<TouchpadScroll>", handler)
        return True
    except Exception:
        return False


def body_height(view_height: int, wanted_height: int) -> int:
    """The body fills the view while the page fits and is never shorter than the page asks for."""
    return max(int(view_height), int(wanted_height), 1)


def needs_scroll(view_height: int, wanted_height: int) -> bool:
    return int(wanted_height) > int(view_height) + 1


def scroll_target(top: float, view_h: float, total_h: float, y: float, h: float, margin: float = 8.0):
    """Where the view's top should go (px) so a widget at y..y+h (in the body) is inside it, or None when it is
    already in view. A widget taller than the view shows its top."""
    if total_h <= view_h or h <= 0:
        return None
    if y >= top and y + h <= top + view_h:
        return None
    if y < top or h >= view_h:
        new = y - margin
    else:
        new = y + h + margin - view_h
    return max(0.0, min(float(new), float(total_h - view_h)))


class PageScroll(ttk.Frame):
    """One main page. Build the page into .body; place this frame where the page goes (as before)."""

    def __init__(self, parent, bg: str = "#ffffff", body_style: str | None = None, **kw):
        super().__init__(parent, **kw)
        self.canvas = Canvas(self, highlightthickness=0, borderwidth=0, background=bg)
        try:   # a Mac trackpad sends many small wheel events (as ScrollBody)
            self.canvas.configure(yscrollincrement=16 if sys.platform == "darwin" else 24)
        except Exception:
            pass
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, style=body_style) if body_style else ttk.Frame(self.canvas)
        self._item = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self._bar)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.refit(), add="+")
        self._last = None
        self._hooks = []
        self._tag = f"PageScrollWheel{id(self)}"
        self.bind_class(self._tag, "<MouseWheel>", self._wheel)
        self.bind_class(self._tag, "<FocusIn>", self._show_focus, add="+")
        if sys.platform.startswith("linux"):
            self.bind_class(self._tag, "<Button-4>", lambda e: self._wheel(e, units=-1))
            self.bind_class(self._tag, "<Button-5>", lambda e: self._wheel(e, units=1))
        # 2.4.0 fit-in F4 (F1c leftovers): a Tk 8.7 / 9 trackpad scrolls the page too; and the page's own canvas
        # and frame take the wheel as well (the strips where the canvas shows beside or under the body were dead)
        self._touch_carry = [0]
        bind_touchpad(self, self._tag, lambda e: self._wheel(
            e, units=touchpad_units(getattr(e, "delta", 0), self._touch_carry)))
        for w in (self, self.canvas):
            try:
                w.bindtags((self._tag,) + tuple(w.bindtags()))
            except Exception:
                pass
        self.after(POLL_MS, self._poll)

    # -- size --------------------------------------------------------------------------------------------------------
    def wanted_height(self) -> int:
        try:
            return int(self.body.winfo_reqheight())
        except Exception:
            return 1

    def view_height(self) -> int:
        try:
            return max(1, int(self.canvas.winfo_height()))
        except Exception:
            return 1

    def overflow(self) -> int:
        """How much taller the page is than the window (0 when it fits): a part of the page that sizes itself from
        its own height (the Transit table) takes this off, so it never grows just because the page scrolls."""
        try:
            return max(0, int(self.body.winfo_height()) - self.view_height())
        except Exception:
            return 0

    def scrollable(self) -> bool:
        return needs_scroll(self.view_height(), self.wanted_height())

    def add_refit_hook(self, fn):
        """fn() is called after the page's size against the window changed (window resized, contents changed)."""
        self._hooks.append(fn)

    def refit(self):
        """Body as wide as the view, as tall as the view or the page's own need, whichever is more."""
        try:
            w = max(1, int(self.canvas.winfo_width()))
            vh = self.view_height()
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
        for fn in list(self._hooks):
            try:
                self.after_idle(fn)
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
        page is on screen. One size read and a walk of the page's widgets (a few ms), only while it is shown."""
        try:
            if self.winfo_ismapped():
                self.refit()
                self._tag_children()
        except Exception:
            pass
        try:
            self.after(POLL_MS, self._poll)
        except Exception:
            pass

    # -- wheel and focus ---------------------------------------------------------------------------------------------
    def _tag_children(self):
        """Give the page's ordinary parts the page's wheel and focus bindings (new parts as they appear). Stops at
        widgets that keep the wheel and does not go into them (a nested scrolling body is a Canvas)."""
        stack = [self.body]
        while stack:
            w = stack.pop()
            try:
                cls = str(w.winfo_class())
            except Exception:
                continue
            if cls in WHEEL_KEEPERS:
                continue
            try:
                tags = w.bindtags()
                if self._tag not in tags:
                    w.bindtags((self._tag,) + tuple(tags))
            except Exception:
                pass
            try:
                stack.extend(w.winfo_children())
            except Exception:
                pass

    def _wheel(self, event, units=None):
        if not self.scrollable():
            try:   # no scrolling to do; a drop-down still keeps its value (review M1)
                if str(event.widget.winfo_class()) in WHEEL_NO_VALUE:
                    return "break"
            except Exception:
                pass
            return None
        n = units if units is not None else wheel_units(getattr(event, "delta", 0))
        if n:
            try:
                self.canvas.yview_scroll(n, "units")
            except Exception:
                pass
        return "break"

    def _show_focus(self, event):
        """Scroll a box that got the focus into view (review S3). Not when the same box gets the focus back because
        the window became active again (re-review N1): the page stays where the user scrolled it."""
        w = getattr(event, "widget", None)
        same = w is not None and w is getattr(self, "_last_focus", None)
        self._last_focus = w
        if same or not self.scrollable():
            return None
        try:
            y = int(w.winfo_rooty()) - int(self.body.winfo_rooty())
            h = int(w.winfo_height())
            total = int(self.body.winfo_height())
            top = float(self.canvas.canvasy(0))
        except Exception:
            return None
        new = scroll_target(top, self.view_height(), total, y, h)
        if new is not None and total > 0:
            try:   # the canvas moves in whole scroll steps: round away from the box, so all of it shows
                inc = int(float(self.canvas.cget("yscrollincrement")) or 0)
            except Exception:
                inc = 0
            if inc > 0:
                import math
                new = max(0.0, float((math.ceil(new / inc) if new > top else math.floor(new / inc)) * inc))
            try:
                self.canvas.yview_moveto(new / float(total))
            except Exception:
                pass
        return None


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
