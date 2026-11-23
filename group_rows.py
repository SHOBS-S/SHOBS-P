"""2.4.0 (#98): the Output pages' controls in function groups, the same groups in the same order on the Transit fit
page and the Variables Output page.

One row per group: a small grey (Hint) label in a fixed-width left column, the group's controls beside it, and a
hairline between rows. No boxes and no group colours, so the plot keeps its height. A group's controls wrap like
text when the page is narrow (125 % scaling on a 1366-px screen), as the Transit page's tools row did."""
from __future__ import annotations

from tkinter import LEFT, W, X, ttk

# The label column's width in characters ("Corrections" is the longest label); the save rows under the plots use it
# too, so every label on the page lines up.
LABEL_WIDTH = 11

FEEDS = " These settings feed the fit: change one and the fit has to be made again."

# (key, label, what the group holds on each page)
GROUPS = (
    ("data", "Data",
     {"transits": "Data: which night or report the page fits.",
      "variables": "Data: the series and its nights (load, close, add, remove, notes)."}),
    ("points", "Points",
     {"transits": "Points: which frames go into the fit (trim, flagged frames)." + FEEDS,
      "variables": "Points: which points go into the period and the report (flagged points, cloud, transmission "
                   "loss, planet transits, trim)." + FEEDS.replace("has to be made again", "is made again by itself")}),
    ("corrections", "Corrections",
     {"transits": "Corrections: trends taken out of the light curve (detrend, meridian flip)." + FEEDS,
      "variables": "Corrections: how nights and segments are lined up before the period search."
                   + FEEDS.replace("has to be made again", "is made again by itself")}),
    ("fit", "Fit",
     {"transits": "Fit: the transit fit itself. It always waits for the Fit transit button.",
      "variables": "Fit: the period search and its range."}),
    ("comps", "Comps",
     {"transits": "Comps: how the comparison stars behaved, and which ones are used.",
      "variables": "Comps: how the comparison stars behaved, and which ones are used."}),
    ("analysis", "Analysis",
     {"transits": "Analysis: more work on this fit or on several nights. Nothing here changes the fit shown.",
      "variables": "Analysis: more work on the series. Nothing here changes the period shown."}),
    ("view", "View",
     {"transits": "View: how the plot is drawn. Display only: no number changes.",
      "variables": "View: how the plots are drawn. Display only: no number changes."}),
)
GROUP_KEYS = tuple(g[0] for g in GROUPS)
# 2.4.0 (John, 16 Nov): Comps and Analysis share one row, to give the plot back some height. The sharing group's
# label and controls follow the host group's controls in the same wrapping row. {group: host group}
SHARED_ROW = {"analysis": "comps"}
GROUP_LABELS = {g[0]: g[1] for g in GROUPS}


def wrap_rows(widths, avail) -> list:
    """Lay widgets of these widths (px, padding included) out in rows no wider than avail, the way text wraps; each
    row holds at least one widget (the same rule as clearv_app.flow_rows). Returns [[index, ...], ...]."""
    rows, cur, used = [], [], 0.0
    for i, w in enumerate(widths):
        w = max(0.0, float(w))
        if cur and avail is not None and used + w > avail:
            rows.append(cur)
            cur, used = [], 0.0
        cur.append(i)
        used += w
    if cur:
        rows.append(cur)
    return rows


class FlowBox:
    """A frame whose children wrap like text. add() registers a child (made with box.frame as its parent); show()
    takes one in or out of the row without losing its place."""

    def __init__(self, parent, style="Card.TFrame"):
        self.frame = ttk.Frame(parent, style=style)
        self._style = style
        self._lines = []         # 2.4.0 step 8 (#89): one frame per line of the wrapped row
        self.items = []          # [widget, pad, wrapping label?]
        self.hidden = set()
        self._key = None
        self._width = None
        try:
            self.frame.bind("<Configure>", lambda e: self.flow(e.width), add="+")
        except Exception:
            pass

    def add(self, widget, pad: int = 6, wrap: bool = False, shown: bool = True):
        self.items.append((widget, int(pad), bool(wrap)))
        if shown:
            widget.pack(side=LEFT, padx=(0, pad))
        else:
            self.hidden.add(widget)
        self._key = None
        return widget

    def show(self, widget, shown: bool = True):
        """Show or hide a registered child; the row is laid out again."""
        was = widget not in self.hidden
        if bool(shown) == was:
            return
        if shown:
            self.hidden.discard(widget)
        else:
            self.hidden.add(widget)
        self.flow()

    def is_shown(self, widget) -> bool:
        return widget not in self.hidden

    def shown_widgets(self) -> list:
        return [w for w, _p, _wr in self.items if w not in self.hidden]

    def flow(self, width=None):
        """Lay the shown children out in as many lines as the width needs."""
        try:
            if width is None or width <= 1:
                width = self._width if self._width else self.frame.winfo_width()
            real = bool(width and width > 1)
            if real:
                self._width = int(width)
            shown = [(w, p, wr) for w, p, wr in self.items if w not in self.hidden]
            for w, _p, wr in shown:
                if wr and real:
                    try:   # a long hint wraps inside the row instead of widening the page
                        w.configure(wraplength=max(120, int(width) - 16))
                    except Exception:
                        pass
            widths = [int(w.winfo_reqwidth()) + p for w, p, _wr in shown]
            rows = wrap_rows(widths, int(width) if real else None)
            key = (tuple(tuple(r) for r in rows), tuple(id(w) for w, _p, _wr in shown))
            if key == self._key:
                return
            self._key = key
            for w, _p, _wr in self.items:
                w.pack_forget()
                w.grid_forget()
            # 2.4.0 step 8 (#89): each line is laid out on its own (a frame per line), as text wraps. One grid for
            # every line lined the second line's controls up under the first line's columns, which pushed a line
            # with more controls than the one above it past the page's edge (Trim on a 1366-px screen).
            for line in self._lines:
                line.destroy()
            self._lines = []
            for r, row in enumerate(rows):
                line = ttk.Frame(self.frame, style=self._style)
                line.grid(row=r, column=0, sticky=W)
                self._lines.append(line)
                for i in row:
                    shown[i][0].pack(in_=line, side=LEFT, padx=(0, shown[i][1]), pady=(0, 1))
                    shown[i][0].lift()
        except Exception:
            pass

    def rows(self) -> int:
        """How many lines the row takes now (1 before the first layout)."""
        return max(1, len(self._key[0])) if self._key else 1


class GroupView:
    """One group's view of a FlowBox that two groups share (SHARED_ROW): the group keeps its own controls, words and
    show/hide, while the layout is the shared row's. A sharing (non-host) group's label goes into the row just before
    its first control, so it follows the host group's controls however late they are added."""

    def __init__(self, box: FlowBox, lead_label=None, lead_pad: int = 6):
        self.box = box
        self.frame = box.frame
        self.mine = []
        self._lead, self._lead_pad = lead_label, lead_pad

    @property
    def items(self):
        return [it for it in self.box.items if any(it[0] is w for w in self.mine)]

    @property
    def hidden(self):
        return {w for w in self.box.hidden if any(w is m for m in self.mine)}

    def add(self, widget, pad: int = 6, wrap: bool = False, shown: bool = True):
        if self._lead is not None:
            lead, self._lead = self._lead, None
            self.box.add(lead, pad=self._lead_pad)
        self.mine.append(widget)
        return self.box.add(widget, pad=pad, wrap=wrap, shown=shown)

    def show(self, widget, shown: bool = True):
        self.box.show(widget, shown)

    def is_shown(self, widget) -> bool:
        return self.box.is_shown(widget)

    def shown_widgets(self) -> list:
        return [w for w in self.box.shown_widgets() if any(w is m for m in self.mine)]

    def flow(self, width=None):
        self.box.flow(width)

    def rows(self) -> int:
        return self.box.rows()

    @property
    def _key(self):
        return self.box._key

    @_key.setter
    def _key(self, value):
        self.box._key = value


class GroupRows:
    """The seven group rows of an Output page. boxes[key] is the group's FlowBox; put a control in with
    add(key, widget) after making it with parent(key) as its parent."""

    def __init__(self, parent, page: str, tooltip=None, style="Card.TFrame"):
        self.page = page
        self.frame = ttk.Frame(parent, style=style)
        self.boxes, self.labels, self.rows, self.separators, self.tips = {}, {}, {}, [], {}
        for i, (key, label, tips) in enumerate(GROUPS):
            host = SHARED_ROW.get(key)
            if host is not None:
                shared = self.boxes[host].box
                lab = ttk.Label(shared.frame, text=label, style="Hint.TLabel", padding=(12, 3, 0, 0))
                self.rows[key], self.labels[key] = self.rows[host], lab
                self.boxes[key] = GroupView(shared, lead_label=lab)
                self.tips[key] = tips.get(page, "")
                if tooltip is not None and self.tips[key]:
                    try:
                        tooltip(lab, self.tips[key])
                    except Exception:
                        pass
                continue
            if i:
                sep = ttk.Separator(self.frame, orient="horizontal")
                sep.pack(fill=X)   # review: no padding under the hairline (the plot gets the height)
                self.separators.append(sep)
            row = ttk.Frame(self.frame, style=style)
            row.pack(fill=X)
            lab = ttk.Label(row, text=label, style="Hint.TLabel", width=LABEL_WIDTH)
            lab.pack(side=LEFT, anchor="n", pady=(3, 0))
            box = FlowBox(row, style=style)
            box.frame.pack(side=LEFT, fill=X, expand=True)
            if key in SHARED_ROW.values():
                box = GroupView(box)
            self.rows[key], self.labels[key], self.boxes[key] = row, lab, box
            self.tips[key] = tips.get(page, "")
            if tooltip is not None and self.tips[key]:
                try:
                    tooltip(lab, self.tips[key])
                except Exception:
                    pass

    def parent(self, key):
        return self.boxes[key].frame

    def add(self, key, widget, pad: int = 6, wrap: bool = False, shown: bool = True):
        return self.boxes[key].add(widget, pad=pad, wrap=wrap, shown=shown)

    def show(self, key, widget, shown: bool = True):
        self.boxes[key].show(widget, shown)

    def group_of(self, widget):
        """The key of the group a registered control sits in (None when it is in none)."""
        for key, box in self.boxes.items():
            if any(w is widget for w, _p, _wr in box.items):
                return key
        return None

    def texts(self, key) -> list:
        """The words on a group's shown controls, left to right (a compound control gives its children's words)."""
        out = []

        def words(w):
            try:
                t = str(w.cget("text"))
            except Exception:
                t = ""
            if t:
                out.append(t)
            for c in w.winfo_children():
                words(c)
        for w in self.boxes[key].shown_widgets():
            words(w)
        return out
