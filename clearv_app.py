"""Shiloh Hill Observatory – Photometry (SHOBS-P): Windows desktop GUI for OSC photometry.

Flow: input folders -> calibrate (and debayer) -> aperture photometry -> light curve
and an AAVSO Extended File Format report (filter CV).
"""

from __future__ import annotations

import io
import json
import math
import os
import queue
import sys
import threading
import time
import traceback
from tkinter import (
    BOTH,
    END,
    E,
    LEFT,
    RIGHT,
    VERTICAL,
    W,
    X,
    Y,
    BooleanVar,
    Canvas,
    DoubleVar,
    IntVar,
    Listbox,
    PhotoImage,
    StringVar,
    Text,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    simpledialog,
    ttk,
)

import numpy as np

import photometry_core as core
import transit_core as tcore

try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
except ImportError:
    FigureCanvasTkAgg = None
    Figure = None


APP_TITLE = "Shiloh Hill Observatory – Photometry (SHOBS-P)"
APP_VERSION = "2.2.17"
OSC_NOTE = "OSC reduced; not transformed"
MONO_NOTE = "Monochrome camera; not transformed"
APP_SHORT = "SHOBS-P"
# 2.2.18 (item 15): how to cite SHOBS-P (Credits page). The concept DOI resolves to the newest Zenodo release; the
# authors follow CITATION.cff. ASCL_ID: the Astrophysics Source Code Library ID once it is assigned (e.g. "2610.012");
# when filled it is added to the citation as "ascl:<ID>".
CITATION_DOI = "10.5281/zenodo.23417064"
ASCL_ID = ""


def citation_text(version: str = None) -> str:
    """2.2.18 (item 15): the suggested citation, with this build's version (CITATION.cff: authors, title, DOI)."""
    text = (f"Armbrust, J. W., and Shiloh Hill Observatory, SHOBS-P: Shiloh Hill Observatory Photometry, "
            f"version {version or APP_VERSION}, Zenodo, doi:{CITATION_DOI}")
    if ASCL_ID.strip():
        text += f", ascl:{ASCL_ID.strip()}"
    return text
# Marker colours on the image, shared by the pick buttons so each button matches its marker.
PICK_COLORS = {"target": "#ff7f0e", "comp": "#2ca02c", "comp2": "#2ca02c", "check": "#1f77b4", "watch": "#17becf"}
# 2.2.5: up to ten comparison stars. "comp" is C1 and "comp2" C2 (the names older series files use); C3-C10 follow.
MAX_COMPS = 10
COMP_ROLES = ("comp",) + tuple(f"comp{i}" for i in range(2, MAX_COMPS + 1))
for _role in COMP_ROLES:
    PICK_COLORS[_role] = PICK_COLORS["comp"]


# 2.2.5: Discovery candidate marks say which candidates matter. "new": not in VSX/SIMBAD and nothing to check first;
# "known": already in VSX/SIMBAD; "caution": a "Check first" note (near saturation, starts at a flip or gap);
# "unchecked": no sky positions, so the catalog check could not run.
SCAN_MARK_STYLES = {
    "new": {"color": "#ff2fd0", "ms": 15, "mew": 2.4, "alpha": 1.0, "fs": 10, "weight": "bold",
            "legend": "Possible new variable"},
    "known": {"color": "#c49ab8", "ms": 11, "mew": 0.9, "alpha": 0.75, "fs": 8, "weight": "normal",
              "legend": "Known variable (VSX/SIMBAD)"},
    "caution": {"color": "#d9a066", "ms": 11, "mew": 0.9, "alpha": 0.8, "fs": 8, "weight": "normal",
                "legend": "Candidate to check first"},
    "unchecked": {"color": "#e8e8e8", "ms": 12, "mew": 1.2, "alpha": 0.9, "fs": 9, "weight": "normal",
                  "legend": "Unchecked (no sky positions)"},
}


def comp_number(role: str) -> int:
    """1 for "comp", 2 for "comp2" ... 10 for "comp10"; 0 for anything else."""
    if role == "comp":
        return 1
    if role.startswith("comp") and role[4:].isdigit():
        return int(role[4:])
    return 0


def role_label(role: str) -> str:
    """Human name of a marked-star role: Target, Comp 1 … Comp 10, Check."""
    n = comp_number(role)
    if n:
        return f"Comp {n}"
    return {"target": "Target", "check": "Check", "watch": "Watch"}.get(role, role.title())


def series_prev_path(path: str) -> str:
    """2.2.14 (item 17): where Save series keeps the version before: X.cvseries -> X_prev.cvseries. '' for a file
    that is itself a _prev copy (saving onto it keeps no copy of the copy)."""
    stem, ext = os.path.splitext(path)
    if stem.lower().endswith("_prev"):
        return ""
    return f"{stem}_prev{ext or '.cvseries'}"


def placeholder_star_name(name) -> bool:
    """2.2.14 (item 7): a name SHOBS-P gives a star that never got a real one ("check", "comp2", "watch 3 (…)")."""
    import re
    n = " ".join(str(name or "").lower().split())
    if n in ("", "check", "check star", "comp", "comp star", "comparison", "target", "star", "na", "k", "kname"):
        return True
    return bool(re.fullmatch(r"(comp|check)\s*\d{0,2}", n) or re.fullmatch(r"watch \d+ \(.*\)", n))


# 2.2.14 (item 14): catalog prefixes a typed name may carry or leave out ("APASS J2311…" = "J2311…").
_NAME_PREFIXES = ("gaiadr3", "gaiadr2", "gaia", "dr3", "apass", "tycho-2", "tycho2", "tycho", "tyc", "2mass", "ucac4",
                  "auid")


def name_key(text) -> str:
    """2.2.14 (item 14): a star name for matching: case, spaces and underscores ignored ("TYC4006-1310-1")."""
    return "".join(str(text or "").lower().replace("_", " ").split())


def name_core(text) -> str:
    """name_key without a leading catalog prefix ("apassj231144.7+570632" -> "j231144.7+570632")."""
    key = name_key(text)
    for p in _NAME_PREFIXES:
        if key.startswith(p) and len(key) > len(p) + 2:
            return key[len(p):].lstrip(":-")
    return key


def designation_coords(text):
    """2.2.14 (item 14): RA/Dec (degrees) from a J-designation such as "APASS J231144.7+570632" or "2MASS
    J23114470+5706321"; None when the name carries no coordinates. Accurate to about a second of arc."""
    import re
    m = re.search(r"J(\d{2})(\d{2})(\d{2}\.?\d*)([+-])(\d{2})(\d{2})(\d{2}\.?\d*)(?![\d.])", str(text or ""))
    if not m:
        return None
    h, mi, s, sign, d, dm, ds = m.groups()

    def secs(t):   # "44.7", or 2MASS's "4470" (= 44.70)
        t = t.replace(".", "")
        return float(t[:2] + "." + t[2:]) if len(t) > 2 else float(t)
    ra = 15.0 * (int(h) + int(mi) / 60.0 + secs(s) / 3600.0)
    dec = (int(d) + int(dm) / 60.0 + secs(ds) / 3600.0) * (-1.0 if sign == "-" else 1.0)
    if not (0.0 <= ra < 360.0 and -90.0 <= dec <= 90.0):
        return None
    return ra, dec


# Labels the field scan gives the marked stars (and the 2.2.4 and older names).
MARKED_LABELS = ("Target", "Check", "Comp", "Comp 2") + tuple(f"Comp {i}" for i in range(1, MAX_COMPS + 1))
SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".shobs_p_settings.json")
LOG_FILE = os.path.join(os.path.expanduser("~"), ".shobs_p.log")
LOG_MAX_BYTES = 5 * 1024 * 1024   # 2.2.18 (item 24): past this the log moves to .shobs_p.log.1 (one old file kept)
_LOG_LOCK = threading.Lock()


def log_to_file(text, stamp: bool = True) -> None:
    """2.2.18 (item 24): append to LOG_FILE. stamp=True puts the date and time before every line (the Log window's
    lines); stamp=False writes the text as it is (tracebacks, Python warnings). Safe from any thread (one lock), never
    touches Tk, never raises. The file is opened for each write and never held open, so it can be rotated on Windows."""
    try:
        text = str(text)
        if stamp:
            when = time.strftime("%Y-%m-%d %H:%M:%S")
            # review nit: trailing blank lines are not written (one stamped empty line for an empty message)
            text = "".join(f"{when}  {line}\n" for line in (text.rstrip("\r\n").splitlines() or [""]))
        if not text:
            return
        with _LOG_LOCK:
            try:
                if os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
                    os.replace(LOG_FILE, LOG_FILE + ".1")
            except OSError:
                pass
            with open(LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(text)
    except Exception:
        pass


class _LogFileStream(io.TextIOBase):
    """2.2.18 (item 24): sys.stdout / sys.stderr under pythonw (a desktop shortcut has no console): tracebacks and
    Python warnings go into the log file as they are, through log_to_file (same lock and rotation). A TextIOBase, so
    fileno() raises UnsupportedOperation as callers expect of a stream without a file descriptor (review nit)."""

    @property
    def encoding(self):
        return "utf-8"

    def writable(self):
        return True

    def write(self, text):
        log_to_file(text, stamp=False)
        return len(text) if isinstance(text, str) else 0

    def isatty(self):
        return False


APP_DIR = os.path.dirname(os.path.abspath(__file__))
# The three modes. Each recolors the whole frame (sidebar, mode banner, status bar).
MODES = {
    "variables": {"name": "Variables", "side": "#16324a", "step": "#1f4464", "active": "#2a5a80", "status": "#10283b",
                  "hint": "#b7c7d1", "subtitle": "Light curves, periods,\nand AAVSO reports",
                  "banner": "VARIABLES  ·  light curves, periods, color, and AAVSO reports"},
    "transits": {"name": "Transits", "side": "#1d4a2f", "step": "#2a6340", "active": "#357a50", "status": "#133321",
                 "hint": "#bcd6c4", "subtitle": "Exoplanet transits:\nfit, planner, ExoFOP",
                 "banner": "TRANSITS  ·  transit fit (MCMC), planner, nearby-binary check, and ExoFOP package"},
    "discovery": {"name": "Discovery", "side": "#4a1d47", "step": "#63295f", "active": "#7a3576", "status": "#331331",
                  "hint": "#d8bfd6", "subtitle": "Every star in the field,\nlooking for variables",
                  "banner": "DISCOVERY  ·  measure every star in the field and flag the ones that vary"},
}
PERIOD_BIN_CHOICES = ("Every point", "5 min bins", "10 min bins", "30 min bins")
BIN_CHOICES = ("None", "Night", "60 min", "30 min", "15 min", "10 min")
STEPS = ("Input", "Blink", "Calibrate", "Photometry", "Output")
BLINK_SPEEDS = ("0.25 s/frame", "0.5 s/frame", "1 s/frame", "2 s/frame")  # 2.2.3; 0.25 in 2.2.11 (John)
# 2.2.17 (item 3): N/E compass sizes in points (scale with the figure's dpi, i.e. with Windows display scaling).
COMPASS_ARROW_PT = 24.0
COMPASS_FONT_PT = 8.0
COMPASS_MARGIN_PT = 5.0
# 2.2.15 (GitHub #1): display stretch. The sliders step through these black / white points (percentiles).
STRETCH_PAGES = ("blink", "photo")
STRETCH_BLACK_STEPS = (0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0)
STRETCH_WHITE_STEPS = (90.0, 95.0, 97.0, 98.0, 99.0, 99.5, 99.7, 99.8, 99.9, 99.95, 99.99, 100.0)
STRETCH_DEBOUNCE_MS = 250
BLINK_CACHE_BYTES = 1_500_000_000   # 2.2.15: pictures plus the small float16 copies kept for re-stretching
# Page indices (2.2: the Debayer step is gone; the first frame is debayered as soon as it is calibrated).
STEP_INPUT, STEP_BLINK, STEP_CAL, STEP_PHOTO, STEP_OUTPUT = range(5)


def col_width(widget, label: str, width: int) -> int:
    """A table column wide enough for its heading (bold) and scaled with the screen's DPI setting."""
    try:
        from tkinter import font as tkfont
        heading = tkfont.Font(widget, font=(core.UI_FONT, 10, "bold"))
        scale = max(float(widget.tk.call("tk", "scaling")) / 1.3333, 1.0)
        return int(max(width * scale, heading.measure(label) + 30))
    except Exception:
        return width


def fit_tree_columns(tree, headings: dict, cap: int = 480, firm: bool = False) -> None:
    """2.2.12: size each table column to its heading or its widest value, measured in the fonts the table is
    drawn in (bold heading, Treeview body font), scaled for the screen. A column never goes over `cap` points
    (scaled), so one long VSX name cannot take half the table. The widths are minimums; spare room is shared."""
    try:
        from tkinter import font as tkfont
        head = tkfont.Font(tree, font=(core.UI_FONT, 10, "bold"))
        body = None
        try:
            spec = ttk.Style(tree).lookup("Treeview", "font")
            if isinstance(spec, (str, tuple)) and spec:
                body = tkfont.Font(tree, font=spec)
        except Exception:
            body = None
        if body is None:
            body = tkfont.Font(tree, font=(core.UI_FONT, 10))
        try:
            scale = max(float(tree.tk.call("tk", "scaling")) / 1.3333, 1.0)
        except Exception:
            scale = 1.0
        cols = list(tree["columns"])
        rows = []
        for iid in tree.get_children():
            try:
                rows.append([str(v) for v in (tree.item(iid).get("values") or [])])
            except Exception:
                pass
        for k, col in enumerate(cols):
            widest = head.measure(headings.get(col, "")) + 24
            for row in rows:
                if k < len(row):
                    widest = max(widest, body.measure(row[k]) + 24)
            width = int(min(widest, cap * scale))
            # firm=True (2.2.13, Comp health): a column never shrinks below its contents; a narrow window scrolls.
            tree.column(col, width=width, minwidth=width if firm else min(width, int(60 * scale)), stretch=True)
    except Exception:
        pass


SEQ_COLOR = "#38d9e8"       # 2.2.13: AAVSO sequence stars on the image
WATCH_SAME_ARCSEC = 4.0     # 2.2.13: a watch star within this of an existing one is the same star
CIRCLE_LABELS_MAX = 400     # 2.2.14 (item 5): catalog circles labelled on the image (the brightest; hover shows all)
CIRCLES_MAX = 3000          # 2.2.14 (item 5): catalog circles drawn (the brightest; clicks and hover find every star)


def _fainter_text(dm: float) -> str:
    """2.2.14 (item 6): '2.8 mag fainter than the target' (or brighter), from the stars' instrumental magnitudes."""
    if dm is None or not math.isfinite(float(dm)):
        return ""
    dm = float(dm)
    if abs(dm) < 0.05:
        return "as bright as the target"
    return f"{abs(dm):.1f} mag {'fainter' if dm > 0 else 'brighter'} than the target"


def make_constrained_figure(size_in, dpi: int = 100):
    """2.2.13: a Figure whose margins follow its contents (axis labels, rotated date ticks, a legend outside the
    axes) at every draw, so nothing is clipped at any window size or display scaling. Older matplotlib: the
    constrained_layout keyword; without either, a plain Figure."""
    try:
        return Figure(figsize=size_in, dpi=dpi, facecolor="#ffffff", layout="constrained")
    except TypeError:
        try:
            return Figure(figsize=size_in, dpi=dpi, facecolor="#ffffff", constrained_layout=True)
        except TypeError:
            return Figure(figsize=size_in, dpi=dpi, facecolor="#ffffff")


def fit_figure_to_widget(fig, canvas) -> bool:
    """2.2.3: when a Tk-embedded figure's size differs from its widget's, resize the figure and redraw.
    Returns True when it redrew. Safe to call often; a hidden or not-yet-laid-out widget is left alone."""
    try:
        widget = canvas.get_tk_widget()
        if not widget.winfo_ismapped():
            return False
        w, h = int(widget.winfo_width()), int(widget.winfo_height())
    except Exception:
        return False
    if w < 50 or h < 50:
        return False
    dpi = fig.get_dpi() or 100
    fw, fh = fig.get_size_inches() * dpi
    if abs(fw - w) <= 2 and abs(fh - h) <= 2:
        return False
    # Go through the canvas's own resize, which also resizes the Tk photo image the figure is drawn into (setting
    # the figure size alone would draw the bigger figure cropped into the old, smaller image).
    import types
    resize = getattr(canvas, "resize", None)
    try:
        if callable(resize):
            resize(types.SimpleNamespace(width=w, height=h))
        else:
            raise AttributeError
    except Exception:
        fig.set_size_inches(w / dpi, h / dpi, forward=True)
    try:
        fig.tight_layout()
    except Exception:
        pass
    canvas.draw_idle()
    return True


def fit_to_content(win, min_w: int = 480, min_h: int = 320) -> None:
    """2.2.2: open a pop-up at the size its contents ask for, but no bigger than 90% of the screen.
    Each window packs its button row first (side=bottom), so when space runs short the body shrinks or
    scrolls and the buttons stay in view."""
    try:
        win.update_idletasks()
        sw, sh = int(win.winfo_screenwidth()), int(win.winfo_screenheight())
        w = min(max(int(win.winfo_reqwidth()), min_w), int(0.9 * sw))
        h = min(max(int(win.winfo_reqheight()), min_h), int(0.9 * sh))
        win.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        win.minsize(min(w, 420), min(h, 280))
    except Exception:
        pass


def ui_scale(widget) -> float:
    """2.2.16 (item 12, review): the display's scaling against 96 dpi (1.0 at least), for pixel offsets and padding."""
    try:
        return max(float(widget.tk.call("tk", "scaling")) / 1.3333, 1.0)
    except Exception:
        return 1.0


def _pad_total(pad) -> int:
    """2.2.18 (item 23): a pack/grid padx as Tk reports it (14, "14", (6, 2), "6 2") -> both sides in px."""
    import re
    try:
        if isinstance(pad, (int, float)):
            vals = [float(pad)]
        elif isinstance(pad, (tuple, list)):
            vals = [float(v) for v in pad]
        else:
            vals = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", str(pad))]
    except (TypeError, ValueError):
        return 0
    if not vals:
        return 0
    return int(round(2 * vals[0] if len(vals) == 1 else vals[0] + vals[1]))


def flow_rows(widths, avail) -> list:
    """2.2.18 (item 23): lay widgets of these widths (px, padding included) out in rows no wider than avail (px),
    left to right, the way text wraps; each row holds at least one widget. Returns [[index, ...], ...]."""
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


def text_wrap_px(widget, chars: int = 95, size: int = 10) -> int:
    """2.2.14 (item 12): a wrap width in screen pixels for about `chars` characters of the UI font, so it scales with
    the display (a fixed 780 px is a narrow column on a high-DPI screen); at most 60% of the screen."""
    try:
        from tkinter import font as tkfont
        px = int(tkfont.Font(root=widget, family=core.UI_FONT, size=size).measure("n" * chars))
        return max(320, min(px, int(0.6 * int(widget.winfo_screenwidth()))))
    except Exception:
        return 780


def ask_choice(parent, title: str, message: str, choices, safe):
    """2.2.18 (item 4): a small modal question whose buttons say what they do (a Yes/No could be read either way).
    choices: [(value, button text)], left to right. Escape and closing the window answer `safe`; Enter presses the
    button that has the focus (the safe one at first). The window is sized from its contents and centered on the parent. Returns the value chosen."""
    result = {"value": safe}
    win = Toplevel(parent)
    win.title(title)
    try:
        win.transient(parent.winfo_toplevel())
    except Exception:
        pass
    frame = ttk.Frame(win, style="Card.TFrame", padding=14)
    frame.pack(fill=BOTH, expand=True)
    row = ttk.Frame(frame, style="Card.TFrame")
    row.pack(side="bottom", fill=X, pady=(12, 0))
    ttk.Label(frame, text=message, style="Card.TLabel", wraplength=text_wrap_px(win, 70), justify=LEFT).pack(
        anchor=W, fill=X)

    def pick(value):
        result["value"] = value
        try:
            win.grab_release()
        except Exception:
            pass
        win.destroy()
    safe_button = None
    by_widget = {}
    for value, text in reversed(list(choices)):
        button = ttk.Button(row, text=text, command=lambda v=value: pick(v),
                            style="Accent.TButton" if value == safe else "TButton")
        button.pack(side=RIGHT, padx=(6, 0))
        by_widget[str(button)] = value
        if value == safe:
            safe_button = button

    def enter(event):
        # review: Enter presses the focused button (Tab moves the focus), else the safe one.
        widget = getattr(event, "widget", None)
        pick(by_widget.get(str(widget), safe) if widget is not None else safe)
    win.protocol("WM_DELETE_WINDOW", lambda: pick(safe))
    win.bind("<Escape>", lambda _e: pick(safe))
    for key in ("<Return>", "<KP_Enter>"):
        win.bind(key, enter)
    try:
        win.update_idletasks()
        w, h = int(win.winfo_reqwidth()), int(win.winfo_reqheight())
        top = parent.winfo_toplevel()
        x = int(top.winfo_rootx()) + max(0, (int(top.winfo_width()) - w) // 2)
        y = int(top.winfo_rooty()) + max(0, (int(top.winfo_height()) - h) // 3)
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        win.resizable(False, False)
    except Exception:
        pass
    try:
        if safe_button is not None:
            safe_button.focus_set()
        win.grab_set()
    except Exception:
        pass
    try:
        win.wait_window()
    except Exception:
        pass
    return result["value"]


def grow_to_content(win) -> None:
    """2.2.8: after a pop-up's contents grow (results arriving later), make the window big enough for them again,
    never smaller than it is now and never more than 90% of the screen; keep it on screen."""
    try:
        win.update_idletasks()
        sw, sh = int(win.winfo_screenwidth()), int(win.winfo_screenheight())
        cur_w, cur_h = int(win.winfo_width()), int(win.winfo_height())
        w = min(max(int(win.winfo_reqwidth()), cur_w), int(0.9 * sw))
        h = min(max(int(win.winfo_reqheight()), cur_h), int(0.9 * sh))
        if w <= cur_w and h <= cur_h:
            return
        x = min(max(0, int(win.winfo_x())), max(0, sw - w))
        y = min(max(0, int(win.winfo_y())), max(0, sh - h - 40))
        win.geometry(f"{w}x{h}+{x}+{y}")
    except Exception:
        pass


class ScrollBody:
    """2.2.2: a body that scrolls (vertically, and sideways when asked) only when it does not fit.
    Build the window's contents into .inner; call .fit() once they are in place, before fit_to_content."""

    def __init__(self, parent, horizontal: bool = False, bg: str = "#ffffff"):
        self.outer = ttk.Frame(parent, style="Card.TFrame")
        self.canvas = Canvas(self.outer, highlightthickness=0, borderwidth=0, background=bg)
        if sys.platform == "darwin":
            # 2.2.12: a trackpad sends many small wheel events; one unit each is a tenth of the view without this.
            try:
                self.canvas.configure(yscrollincrement=16)
            except Exception:
                pass
        self.vbar = ttk.Scrollbar(self.outer, orient=VERTICAL, command=self.canvas.yview)
        self.hbar = ttk.Scrollbar(self.outer, orient="horizontal", command=self.canvas.xview) if horizontal else None
        self.inner = ttk.Frame(self.canvas, style="Card.TFrame")
        self._item = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=lambda a, b: self._bar(self.vbar, a, b, side=RIGHT, fill=Y))
        if self.hbar is not None:
            self.canvas.configure(xscrollcommand=lambda a, b: self._bar(self.hbar, a, b, side="bottom", fill=X))
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._stretch)
        self.canvas.pack(side=LEFT, fill=BOTH, expand=True)
        self.outer.bind("<Enter>", self._enter)
        self.outer.bind("<Leave>", self._leave)

    def _enter(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        if sys.platform.startswith("linux"):
            # 2.2.12: X11 sends the wheel as buttons 4 and 5.
            self.canvas.bind_all("<Button-4>", lambda e: self._wheel(e, units=-1))
            self.canvas.bind_all("<Button-5>", lambda e: self._wheel(e, units=1))

    def _leave(self, event):
        # Moving onto a child widget is not leaving the body.
        if str(getattr(event, "detail", "")) == "NotifyInferior":
            return
        try:
            self.canvas.unbind_all("<MouseWheel>")
            if sys.platform.startswith("linux"):
                self.canvas.unbind_all("<Button-4>")
                self.canvas.unbind_all("<Button-5>")
        except Exception:
            pass

    def _bar(self, bar, first, last, **pack):
        try:
            whole = float(first) <= 0.0 and float(last) >= 1.0
        except (TypeError, ValueError):
            whole = True
        if whole:
            bar.pack_forget()
        elif not bar.winfo_manager():
            bar.pack(before=self.canvas, **pack)
        bar.set(first, last)

    def follow_width(self, labels, pad: int = 40):
        """2.2.14 (item 12): wrapped text follows the window's width (wraplength = the body's width less `pad`), so
        a wider window shows longer lines and a narrower one wraps instead of clipping."""
        self._followers = list(labels)
        self._follow_pad = int(pad)

    def _stretch(self, event):
        # Let the contents fill the width when there is room; keep their own width when there is not.
        for lab in getattr(self, "_followers", ()):   # 2.2.14 (item 12)
            try:
                lab.configure(wraplength=max(240, int(event.width) - self._follow_pad))
            except Exception:
                pass
        try:
            need = int(self.inner.winfo_reqwidth())
            self.canvas.itemconfigure(self._item, width=max(int(event.width), need))
        except Exception:
            pass

    def _wheel(self, event, units=None):
        try:
            if str(event.widget.winfo_class()) in ("TCombobox", "Text", "Listbox", "Treeview"):
                return
            n = units if units is not None else core.wheel_units(getattr(event, "delta", 0))
            if n:
                self.canvas.yview_scroll(n, "units")
        except Exception:
            pass

    def fit(self):
        """Ask for the contents' full size; fit_to_content then trims to the screen."""
        try:
            self.inner.update_idletasks()
            self.canvas.configure(width=int(self.inner.winfo_reqwidth()), height=int(self.inner.winfo_reqheight()))
        except Exception:
            pass

    def pack(self, **kw):
        self.outer.pack(**kw)


def shade_long_periods(ax, span_days: float, periods) -> bool:
    """2.2.12: grey out the periods longer than about half the data span on a periodogram (log period axis), labelled
    "longer than the data can test". The search still runs there (a multi-night series can test them). Returns True
    when something was shaded."""
    try:
        p = np.asarray(periods, dtype=float)
        p = p[np.isfinite(p) & (p > 0)]
        half = float(span_days) / 2.0
        if not p.size or not (half > 0) or p.max() <= half:
            return False
        lo, hi = max(half, float(p.min())), float(p.max())
        ax.axvspan(lo, hi, color="#9aa7b0", alpha=0.18, lw=0, zorder=0)
        ax.text(math.sqrt(lo * hi), 0.97, "longer than the data\ncan test", transform=ax.get_xaxis_transform(),
                ha="center", va="top", fontsize=7, color="#5d6d76")
        return True
    except Exception:
        return False


def enable_windows_dpi():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


class App(Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.configure(bg="#e7eef2")
        self.settings = self._load_settings()
        self.mode_key = self.settings.get("mode") if self.settings.get("mode") in MODES else "variables"
        self.series_mode = ""
        self._set_window_icon()
        self._style()
        # Open as a normal window at about 80% of the screen, centered. Maximize it yourself if you like.
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = int(screen_w * 0.8)
        height = int(screen_h * 0.8)
        self.geometry(f"{width}x{height}+{(screen_w - width) // 2}+{max((screen_h - height) // 2 - 20, 0)}")
        self.minsize(min(1200, width), min(800, height))

        self.bias_dir = StringVar()
        self.dark_dir = StringVar()
        self.flat_dir = StringVar()
        self.light_dir = StringVar()
        self.output_dir = StringVar()
        self.observer = StringVar(value=str(self.settings.get("observer_code", "") or ""))   # 2.2.18 (item 11)
        self.star_id = StringVar()
        self.chart = StringVar()
        self.comp_name = StringVar()
        self.comp_mag = StringVar()
        self.check_name = StringVar()
        self.check_mag = StringVar()
        # 2.2.4: the site starts blank for a new user and is remembered in the settings file on this PC once typed
        # (it is never written into the program files).
        self.lat = StringVar(value=str(self.settings.get("site_lat", "")))
        self.lon = StringVar(value=str(self.settings.get("site_lon", "")))
        self.pattern = StringVar(value="RGGB")
        self.debayer_mode = StringVar(value="green")
        self.binning = IntVar(value=1)
        self.binning.trace_add("write", lambda *_: self._binning_changed())
        self.radius = DoubleVar(value=8.0)
        self.sky_in = DoubleVar(value=14.0)
        self.sky_out = DoubleVar(value=22.0)
        self.min_period = DoubleVar(value=0.05)
        self.max_period = DoubleVar(value=20.0)
        self.comp2_name = StringVar()
        self.comp2_mag = StringVar()
        for _role in COMP_ROLES[2:]:
            setattr(self, f"{_role}_name", StringVar())
            setattr(self, f"{_role}_mag", StringVar())
        self.ra_hours = StringVar()
        self.dec_deg = StringVar()
        # 2.2.18 (item 11): focal length and pixel size are remembered (settings file) like the site; the built-in
        # values below are used until your own are typed or read from your lights' headers.
        self.focal_mm = DoubleVar(value=self._saved_optics("focal_mm"))
        self.pixel_um = DoubleVar(value=self._saved_optics("pixel_um"))
        self.blink_source = StringVar(value="lights")
        self.notes = StringVar(value=OSC_NOTE)
        # 2.2.8: the default report note follows the camera (a mono camera is not "OSC reduced"); a note the user
        # typed is left alone.
        self.pattern.trace_add("write", lambda *_a: self._sync_camera_note())
        self.sat_limit = DoubleVar(value=60000)
        self.cloud_limit = DoubleVar(value=20.0)
        self.use_check_zp = BooleanVar(value=True)
        self.bin_choice = StringVar(value="None")
        self.mark_periods = StringVar(value="")
        self.night_notes = {}
        self.time_axis = StringVar(value="JD")
        self.measure_color = BooleanVar(value=False)
        self.precalibrated = False
        self.catalog = StringVar(value=core.CATALOGS[0])
        self.skip_flagged = BooleanVar(value=True)
        self.norm_segments = BooleanVar(value=False)
        self.filter_hint = StringVar()
        self.mag_header = StringVar(value="V magnitude")
        self.show_variables = BooleanVar(value=True)
        self.show_labels = BooleanVar(value=True)
        self.show_catalog = BooleanVar(value=True)      # 2.2.5: catalog circles (and their labels)
        self.show_apertures = BooleanVar(value=True)    # 2.2.5: aperture and sky ring around the marked stars
        self.candidates_only = BooleanVar(value=False)  # 2.2.5: Discovery: only scan candidates and watch stars
        self._own_setup = None                 # your site/optics/camera, set aside while using MicroObservatory frames
        self.scale_approx = False              # the plate scale is only approximate: widen Solve's search
        self.app_version = APP_VERSION
        self.elevation = StringVar(value=str(self.settings.get("site_elevation", "")))
        for _key, _var in (("site_lat", self.lat), ("site_lon", self.lon), ("site_elevation", self.elevation)):
            _var.trace_add("write", lambda *_a, k=_key, v=_var: self._remember_site(k, v))
        # 2.2.18 (item 11): the same guards as the site (a series' or a borrowed setup is never saved as yours).
        for _key, _var in (("focal_mm", self.focal_mm), ("pixel_um", self.pixel_um), ("observer_code", self.observer)):
            _var.trace_add("write", lambda *_a, k=_key, v=_var: self._remember_own_value(k, v))
        # 2.2.2: telescope aperture for the scintillation term in the error bars (typed once, remembered).
        # 2.2.3: in millimetres; an aperture saved by 2.2.2 (in cm) is converted once on start.
        self.aperture_mm = StringVar(value=core.aperture_mm_from_settings(self.settings))
        self.aperture_hint = StringVar(value=core.aperture_mm_hint(self.aperture_mm.get()))
        self.use_scint = BooleanVar(value=bool(self.settings.get("use_scint", True)))
        self.aperture_mm.trace_add("write", lambda *_: self._remember_scint())
        # 2.2.16 (item 5): the telescope's name for the ExoFOP file and the night setup (ASIAIR's TELESCOP is the
        # mount). Blank = from the header (unless it names a mount) and the aperture / focal length.
        self.telescope_name = StringVar(value=str(self.settings.get("telescope_name", "") or ""))
        self.telescope_name.trace_add("write", lambda *_: self._remember_telescope())
        # 2.2.18 (item 8): the mount type (German equatorial / Alt-az), remembered; the lights' headers win only when
        # they say it unambiguously (core.detect_mount / resolve_mount).
        _mount = self.settings.get("mount") if self.settings.get("mount") in core.MOUNT_LABELS else core.MOUNT_GEM
        self.mount_choice = StringVar(value=core.mount_label(_mount))
        self.mount_choice.trace_add("write", lambda *_: self._remember_mount())
        self.mount_hint = StringVar(value="")
        self._mount_cache = {}
        self.use_scint.trace_add("write", lambda *_: self._remember_scint())
        self.planet_name = StringVar()
        self.pl_period = StringVar()
        self.pl_t0 = StringVar()
        self.pl_t14 = StringVar()
        self.pl_depth = StringVar()
        self.pl_k = StringVar()
        self.pl_a_rs = StringVar()
        self.pl_b = StringVar()
        self.pl_teff = StringVar()
        self.planet_info = {}          # the last lookup (errors, source, magnitudes) for the values above
        self.planet_source = StringVar(value="Type the planet's name and press Enter (NASA Exoplanet Archive; "
                                             "TOIs also from ExoFOP), or type the values.")
        self._last_planet_lookup = None   # 2.2.14 (item 2): the planet name last looked up (auto lookup on Enter)

        self.masters = None
        self.calibrated = None
        self.masters_binning = None
        self.preview_is_debayered = False
        self.preview = None
        self.preview_header = None
        self.preview_path = None
        self.pick_mode = StringVar(value="target")
        self._pick_user_chosen = False   # 2.2.18 (item 7): the user picked a step (page or pop-out) on this image
        self._pick_auto_advanced = False   # 2.2.18 (item 7): the solve's Target marker moved the pick to Comps
        self.target_xy = None
        self.comp_xy = None
        self.comp2_xy = None
        for _role in COMP_ROLES[2:]:
            setattr(self, f"{_role}_xy", None)
        self.check_xy = None
        self.observations = []
        self.series_path = ""
        self.period = None
        self.rejected = set()
        self.blink_paths = []
        # 2.2.17 (item 6): {path: local clock time of the exposure}; also written by the Blink preload thread (single
        # dict stores, atomic under the GIL; Tk is never touched from there).
        self._blink_times = {}
        self._blink_labels = {}   # 2.2.17 (round 2): {path: frame label} for the Blink list, set when it is loaded
        self.blink_index = 0
        self.blink_playing = False
        self._blink_loaded_from = None
        # 2.2.3: frames are read once in the background and kept as small 8-bit pictures, so blinking is just
        # swapping pictures; the speed is chosen on the Blink page and remembered.
        self._blink_cache = {}
        self._blink_cache_order = []
        self._blink_gen = 0
        self._blink_artist = None
        self.blink_speed = StringVar(value=self._blink_speed_text(self.settings.get("blink_seconds", 1.0)))
        self.blink_speed.trace_add("write", lambda *_: self._remember_blink_speed())
        # 2.2.15 (GitHub #1): display stretch of the Blink and Photometry images, one each, remembered.
        self.stretch = {page: core.stretch_settings(self.settings.get(f"stretch_{page}")) for page in STRETCH_PAGES}
        self._stretch_vars = {}
        self._stretch_jobs = {}
        self._blink_restretch_gen = 0
        self._photo_disp = None          # (image, token, stretch, display array) of the last Photometry drawing
        self._radec_filled = None        # 2.2.15: (RA, Dec) texts a solve filled in from the field centre
        self.show_tips = BooleanVar(value=bool(self.settings.get("photo_tips", True)))   # 2.2.15 (GitHub #3)
        self._counts = {}
        self.chart_placed = []
        self.comps_used = []
        self.check_used = ""
        self.report_names = {}        # 2.2.14 (item 7): placeholder name ("check") -> the name typed for reports
        self._slot_notes = {}         # 2.2.14 (item 14): role -> (typed name, outcome) shown on the slot's hint line
        self._slot_typed = {}         # 2.2.16 (item 8): role -> the name as last typed by a key in its box
        self._slot_keying = None      # 2.2.16 (item 8): the slot a key was just pressed in (its text changes next)
        self._prev_kept = set()       # 2.2.14 (item 17): series files whose _prev copy was made this session
        self.series_mode_unknown = False   # 2.2.14 (item 19): the loaded series file does not say its mode (old)
        self.target_var_type = ""     # 2.2.14 (item 19): VSX type of the looked-up target ("" = none known)
        self.star_bands = {}
        self.seq_auids = {}           # 2.2.13: catalog name -> AAVSO AUID for sequence comps (reports)
        self.color_cal = {}
        self.spare_catalog = {}
        self.watch_list = []
        self.watch_xy = {}
        self.field_variables = []
        self.field_check_sources = []
        self.variable_marks = []
        self.last_scan = None
        self.highlight_night = None
        self._marks_shape = None         # 2.2.3: shape of the debayered image the markers belong to
        self.comp_coords = {}            # 2.2.3: {comp/check name: (ra, dec)} to recognize one star under two names
        self._save_one_night = False     # 2.2.3: the current Save is for the highlighted night only
        self.reports_saved = {}          # 2.2.3: {night: [{"file":..., "date":..., "scope": "night"|"series"}]}
        # 2.2.18 (item 10): {night: [{"to": "webobs"|"exoplanet"|"exofop", "file":..., "date":...}]}: nights marked
        # "sent" when an upload page was opened for the report that held them (kept in the series).
        self.reports_sent = {}
        self._night_by_item = {}
        self.comp_catalog_mags = {}
        self.comp_band_sources = {}   # 2.2.17 (item 1): a Sloan run's comp/check magnitude sources (report notes)
        self._hover_sets = []
        self._hover_annots = {}
        self._hover_key = None
        self._period_note = ""
        self._photo_hover_sets = []
        self._photo_hover_annots = {}
        self._photo_hover_key = None
        self.spare_marks = []
        self.active_comps = []
        self._cancel = threading.Event()
        self.align_flip = BooleanVar(value=False)
        self.airmass_view = BooleanVar(value=False)   # 2.2.17 (item 10): airmass-corrected night means (display only)
        self.pier_segments = BooleanVar(value=False)   # 2.2.14 (item 20): each pier side normalised on its own
        self.period_bins = StringVar(value="10 min bins")
        self.night_bin = {}
        self.legacy_mixed = set()
        self._prog = None
        self.scan_marks = []
        self.chart_solution = None
        self.catalog_period = None
        self.catalog_epoch = None       # 2.2.14 (item 13): VSX epoch (HJD) and type, for times of max/min and O-C
        self.catalog_var_type = ""
        self.extremum_csv = ""          # 2.2.14 (item 13): the times-of-extremum CSV chosen this session
        self.extremum_csv_star = ""     # ... and the star it was chosen for (review: not reused for another star)
        self._loss_report = {}          # 2.2.14 (item 18): {night: light-loss episodes} from the last cloud check
        self._loss_failed_logged = set()
        self._pier_cache = {}           # 2.2.14 (item 20): pier sides of nights saved without them
        self._planet_eph_cache = {}     # 2.2.15 (item 1): {star key: (time, transiting planets or None)} per session
        self._zp_caution_cache = None   # 2.2.15 (item 2): (key, caution text) of the last zero-point check
        self.busy = False
        self._ui_queue = queue.Queue()
        self._photo_view = None            # zoomed view of the Photometry image: (xlim, ylim, shape)
        self._photo_press = None           # where a left-button press started (pan or click)
        self.field_centre_needed = False   # Discovery: lights without a plate solution or pointing in the header
        self.scan_excluded = []            # Discovery: (x, y) of stars left out of field scans
        self.suggested_comps = []          # comps near the target's brightness, from Solve
        self._fit_after_photometry = False
        self._inst_cache = None
        self.mono_filter = None            # (AAVSO code, band) from a monochrome camera's FITS FILTER
        # 2.2.11: a monochrome camera's filter set by hand (Calibrate page Filter box), used when the header has none.
        self.mono_filter_choice = StringVar(value=self.settings.get("mono_filter_choice", "") or "")
        self._mono_filter_manual = bool(self.mono_filter_choice.get() in core.MONO_FILTER_CHOICES)
        if self._mono_filter_manual:
            self.mono_filter = core.MONO_FILTER_CHOICES[self.mono_filter_choice.get()]
        self._mono_filter_source = "hand" if self._mono_filter_manual else None   # header / hand / unknown / mobs
        self._pattern_user_set = False     # the Bayer pattern was picked by hand (cleared when the folders change)
        # 2.2.18 (item 1): lights that are colour images already debayered elsewhere (three channels): the channel
        # measured, the lights folder it was chosen for, and the Bayer pattern to go back to after them.
        self._rgb_channel = None
        self._rgb_lights_dir = None
        self._pattern_before_rgb = None
        core.set_rgb_channel(None)
        self._sensor_asked_for = None      # 2.2.18 (item 2): the lights folder "Color or monochrome camera?" was asked for
        self._debayer_before_mono = None   # the colour channel to go back to when leaving MONO
        self._catalog_before_discovery = None
        self._closest_dm = None
        self._target_bv = None
        self._field_question_declined = None

        self._build()
        self.debayer_mode.trace_add("write", lambda *_: self._update_filter_hint())
        self.pattern.trace_add("write", lambda *_: self._pattern_changed())
        self._pattern_changed()
        for var in (self.bias_dir, self.dark_dir, self.flat_dir, self.light_dir):
            var.trace_add("write", lambda *_: self._folders_changed())
        self.light_dir.trace_add("write", lambda *_: self._clear_marks())
        self._update_filter_hint()
        # 2.2.2: the Output folder starts blank every launch, like the frame folders (no longer remembered).
        if "output_dir" in self.settings:
            self.settings.pop("output_dir", None)
            self._save_settings()
        self.output_dir.trace_add("write", lambda *_: self._output_typed())
        self.set_mode(self.mode_key, initial=True)
        self.show_step(0)
        self.after(50, self._drain_ui_queue)
        # 2.2.18 (item 9): closing the main window with an unsaved series asks first, as Close series does.
        self._closing = False
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---- thread-safe UI calls -------------------------------------------------
    def call_ui(self, func, *args):
        """Queue a UI update from a worker thread. Tk is only touched on the main thread."""
        self._ui_queue.put((func, args))

    def _drain_ui_queue(self):
        try:
            while True:
                func, args = self._ui_queue.get_nowait()
                try:
                    func(*args)
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        self.after(50, self._drain_ui_queue)

    def _start_job(self, label: str) -> bool:
        if self.busy:
            messagebox.showinfo(APP_TITLE, "Another step is still running. Wait for it to finish.")
            return False
        self.busy = True
        self._cancel = threading.Event()
        self.config(cursor="watch")
        self.status.set(label)
        return True

    def _end_job(self):
        self.busy = False
        self.config(cursor="")

    def _folders_changed(self):
        """A new folder means the old masters and preview no longer apply."""
        self.precalibrated = False
        self._pattern_user_set = False    # 2.2.11: a new camera may come with new folders
        self.variable_marks = []
        self.field_variables = []
        self.field_check_sources = []
        self.chart_solution = None
        self._field_var_cache = None
        if hasattr(self, "field_check_text"):
            self.field_check_text.set("Variable-star check: Solve to check the field against VSX and SIMBAD.")
        self.scan_marks = []
        self.suggested_comps = []
        self.masters = None
        self.masters_binning = None
        self.calibrated = None
        self.preview = None
        self._photo_disp = None   # 2.2.15: the stretched copy belongs to the old image
        self.preview_is_debayered = False
        self.chart_placed = []
        self.blink_playing = False

    def _clear_marks(self):
        """New lights mean new star positions; drop the old markers."""
        self.target_xy = self.check_xy = None
        for _role in COMP_ROLES:
            setattr(self, f"{_role}_xy", None)
        self._marks_shape = None
        self.scan_marks = []
        self.spare_marks = []
        self.scan_excluded = []
        self.suggested_comps = []
        self._photo_view = None
        self._photo_drawn_shape = None
        self.watch_xy = {}
        self.variable_marks = []
        self.pick_mode.set("watch" if self.mode_key == "discovery" else "target")
        self._pick_user_chosen = False   # 2.2.18 (item 7)
        self._pick_auto_advanced = False
        if hasattr(self, "pick_label"):
            self._update_pick_label()
        self._refresh_watch_box()   # 2.2.14 (item 15): the box said "on this frame" for the old frame's positions

    def _filter_code(self) -> str:
        """AAVSO filter code for reports: the filter wheel's (FITS FILTER) on a monochrome camera, else the OSC
        channel's (TG, TR, TB, CV)."""
        if self.pattern.get() == core.MONO and self.mono_filter:
            return self.mono_filter[0]
        return core.filter_for_mode(self.debayer_mode.get())

    def _band(self) -> str:
        """Catalog band the comps are taken in."""
        if self.pattern.get() == core.MONO and self.mono_filter:
            return self.mono_filter[1]
        return {"red": "R", "blue": "B"}.get(self.debayer_mode.get(), "V")

    def _band_word(self) -> str:
        """2.2.17 (item 1): the comps' band in messages that always said "V": "V" unless this is a Sloan run (then
        "i′", "g′"…), so V/B/R and OSC runs read as before."""
        return core.band_label(self._band()) if self._sloan_run() else "V"

    def _sloan_run(self) -> bool:
        """2.2.17 (item 1): the comps are taken in a Sloan band (mono camera with a Sloan g′ r′ i′ z′ filter)."""
        return core.is_sloan(self._band())

    def _band_source_counts(self, band: str) -> dict:
        """2.2.17 (item 1): how many labeled stars take their magnitude in `band` from each catalog."""
        out: dict = {}
        for _x, _y, st in self.chart_placed or []:
            src = (st.get("mag_source") or {}).get(band)
            if src and (st.get("mags") or {}).get(band) is not None:
                out[src] = out.get(src, 0) + 1
        return out

    def _band_source_text(self, role: str, name: str) -> str:
        """2.2.17 (item 1): where a marked comp's magnitude in the run's band came from: "APASS DR9", or
        "Gaia DR3, from Gaia G and BP-RP, ±0.04"; '' when unknown (typed by hand, or not on a labeled star)."""
        band = self._band()
        xy = getattr(self, f"{role}_xy", None)
        star = self._chart_star_named(name) or (self._chart_star_at(xy) if xy is not None else None)
        if not star:
            return ""
        src = (star.get("mag_source") or {}).get(band) or (star.get("catalog") if star.get("band") == band else "")
        note = (star.get("mag_notes") or {}).get(band)
        return f"{src}, {note}" if src and note else (src or "")

    def _read_mono_filter(self, header: dict):
        """A monochrome camera's filter from the light's header (2.2.1). 2.2.11: a known FILTER value wins; with no
        FILTER keyword the filter set by hand (Calibrate page) is used; an unknown value (I, Ha, Sloan…) is never
        replaced by the hand-set one: it is reported as clear, with a caution."""
        header = header or {}
        raw = core.header_filter_value(header)
        found = core.filter_from_header(header)
        hand = self._mono_filter_hand()
        rgb = getattr(self, "_rgb_channel", None)
        if rgb:
            # 2.2.18 (item 1): colour images: the chosen channel's code (TG/TR/TB/CV), never the hand-set filter of a
            # monochrome camera or a FILTER keyword.
            found, source = core.RGB_CHANNELS[rgb], "rgb"
            if hasattr(self, "mono_filter_choice"):
                self.mono_filter_choice.set(core.mono_filter_label(found) or self.mono_filter_choice.get())
        elif found is not None:
            source = "header"
            if hasattr(self, "mono_filter_choice"):
                self.mono_filter_choice.set(core.mono_filter_label(found) or self.mono_filter_choice.get())
        elif getattr(self, "_mono_filter_source", None) == "mobs" and self._own_setup is not None:
            # MicroObservatory frames with no FILTER stay clear, never the hand-set filter of your own camera.
            found, source = ("CV", "V"), "mobs"
        elif raw:
            source = "unknown"
            if hasattr(self, "mono_filter_choice"):
                self.mono_filter_choice.set("Clear / L (CV)")
            if self.pattern.get() == core.MONO and raw != getattr(self, "_unknown_filter_logged", None):
                self._unknown_filter_logged = raw
                self.log(f"Caution: the FITS FILTER is '{raw}', which SHOBS-P cannot calibrate (the comp catalogs carry "
                         "B, V, R and Sloan g\u2032 r\u2032 i\u2032 z\u2032 only). These frames are reported as clear (CV) "
                         "with V comps.")   # 2.2.17 (item 1): Sloan is known now
        elif hand is not None:
            found, source = hand, "hand"
            self.mono_filter_choice.set(self.settings.get("mono_filter_choice", ""))
        else:
            source = None
        self._mono_filter_source = source
        if found != self.mono_filter:
            old_band = self._band()
            self.mono_filter = found
            self._comp_band_changed(old_band)   # 2.2.17 (review M2)
            if found and source == "header" and self.pattern.get() == core.MONO:
                self.log(f"Filter from the FITS header: {raw} → AAVSO filter {found[0]}, comps in "
                         f"{core.band_label(found[1])}.")   # 2.2.17 (item 1): "i′", not "SI"
        self._update_filter_hint()

    def _mono_filter_hand(self):
        """The filter set by hand (saved in the settings), as (code, band), or None. The Filter box may be showing a
        header's filter, so the saved choice is read, not the box."""
        if not getattr(self, "_mono_filter_manual", False):
            return None
        return core.MONO_FILTER_CHOICES.get(self.settings.get("mono_filter_choice", ""))

    def _update_filter_hint(self):
        mode = self.debayer_mode.get()
        code, band = self._filter_code(), core.band_label(self._band())   # 2.2.17 (item 1): i′, g′ for Sloan
        if self.pattern.get() == core.MONO and self.mono_filter:
            where = {"hand": "filter set here", "header": "filter wheel (FITS FILTER)",
                     "mobs": "MicroObservatory, clear",
                     "rgb": "color image, one channel"}.get(getattr(self, "_mono_filter_source", None),
                                                           "filter wheel (FITS FILTER)")
            self.filter_hint.set(f"AAVSO filter code {code}\n{where}, {band} comps")
        elif self.pattern.get() == core.MONO:
            what = ("filter in the header not supported" if getattr(self, "_mono_filter_source", None) == "unknown"
                    else "no filter in the header")
            self.filter_hint.set(f"AAVSO filter code {code}\n{what}: clear, {band} comps")
        else:
            self.filter_hint.set(f"AAVSO filter code {code}\n{mode.capitalize()} channel, {band} comps")
        self.mag_header.set(f"{band} magnitude")

    def _style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", font=(core.UI_FONT, 10), background="#e7eef2")
        style.configure("TFrame", background="#e7eef2")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#e7eef2", foreground="#1c2b33")
        style.configure("Card.TLabel", background="#ffffff", foreground="#1c2b33")
        style.configure("Hint.TLabel", background="#ffffff", foreground="#5d6d76")
        style.configure("Side.TFrame", background="#16324a")
        style.configure("Side.TLabel", background="#16324a", foreground="#f4f7f8")
        style.configure("SideHint.TLabel", background="#16324a", foreground="#b7c7d1")
        style.configure("Step.TButton", anchor=W, padding=(12, 8), background="#1f4464", foreground="#f4f7f8")
        style.map("Step.TButton", background=[("active", "#2a5a80")], foreground=[("active", "#ffffff")])
        style.configure("Accent.TButton", padding=(12, 6), background="#c47a2c", foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", "#a86622")], foreground=[("active", "#ffffff")])
        style.configure("TNotebook", background="#e7eef2")
        style.configure("Group.TLabelframe", background="#ffffff", bordercolor="#c9d6de")
        style.configure("Group.TLabelframe.Label", background="#ffffff", foreground="#16324a", font=(core.UI_FONT, 10, "bold"))
        style.configure("TNotebook.Tab", padding=(12, 6))
        style.configure("Status.TLabel", background="#10283b", foreground="#d5e0e6")
        style.configure("TEntry", fieldbackground="#ffffff")
        # Tall enough rows that text is never clipped on high-DPI Windows displays.
        style.configure("Treeview", rowheight=28, font=(core.UI_FONT, 10))
        style.configure("Treeview.Heading", font=(core.UI_FONT, 10, "bold"), padding=(4, 6))
        for key, colour in PICK_COLORS.items():
            style.configure(f"Pick{key}.TRadiobutton", background="#ffffff", foreground=colour,
                            font=(core.UI_FONT, 10, "bold"))
            style.map(f"Pick{key}.TRadiobutton", background=[("active", "#f2f5f7")], foreground=[("active", colour)])
            style.configure(f"Pick{key}.TLabel", background="#ffffff", foreground=colour, font=(core.UI_FONT, 10, "bold"))
        style.configure("TCombobox", fieldbackground="#ffffff")
        for key, m in MODES.items():
            style.configure(f"Mode{key}.TButton", anchor=W, padding=(12, 7), background=m["step"], foreground="#ffffff",
                            bordercolor=m["active"], font=(core.UI_FONT, 10, "bold"))
            style.map(f"Mode{key}.TButton", background=[("active", m["active"])], foreground=[("active", "#ffffff")])
        style.configure("Warn.TLabel", background="#ffffff", foreground="#b35c00")
        style.configure("Good.TLabel", background="#ffffff", foreground="#2e7d32")
        style.configure("Bad.TLabel", background="#ffffff", foreground="#b00020")
        style.configure("Box.TLabel", background="#ffffff", foreground="#1c2b33", font=(core.MONO_FONT, 9))
        # 2.2.15 (GitHub #3): the Photometry page's step hint.
        style.configure("StepOn.TLabel", background="#c47a2c", foreground="#ffffff", font=(core.UI_FONT, 9, "bold"),
                        padding=(6, 1))
        style.configure("StepDone.TLabel", background="#ffffff", foreground="#2e7d32", font=(core.UI_FONT, 9),
                        padding=(6, 1))
        style.configure("StepTodo.TLabel", background="#ffffff", foreground="#8a979e", font=(core.UI_FONT, 9),
                        padding=(6, 1))
        # 2.2.16 (item 10): "1 Not solved" in amber.
        style.configure("StepWarn.TLabel", background="#fff1dc", foreground="#b35c00", font=(core.UI_FONT, 9, "bold"),
                        padding=(6, 1))
        self._apply_theme(getattr(self, "mode_key", "variables"))

    def _apply_theme(self, mode: str):
        """Recolor the frame (sidebar, step buttons, banner, status bar, group titles) for a mode."""
        m = MODES[mode]
        style = ttk.Style(self)
        style.configure("Side.TFrame", background=m["side"])
        style.configure("Side.TLabel", background=m["side"], foreground="#f4f7f8")
        style.configure("SideHint.TLabel", background=m["side"], foreground=m["hint"])
        style.configure("Step.TButton", anchor=W, padding=(12, 8), background=m["step"], foreground="#f4f7f8")
        style.map("Step.TButton", background=[("active", m["active"])], foreground=[("active", "#ffffff")])
        style.configure("Status.TLabel", background=m["status"], foreground="#d5e0e6")
        style.configure("Banner.TFrame", background=m["step"])
        style.configure("Banner.TLabel", background=m["step"], foreground="#ffffff", font=(core.UI_FONT, 11, "bold"))
        style.configure("Group.TLabelframe.Label", background="#ffffff", foreground=m["side"], font=(core.UI_FONT, 10, "bold"))

    def _build(self):
        outer = ttk.Frame(self)
        outer.pack(fill=BOTH, expand=True)

        self.side = ttk.Frame(outer, style="Side.TFrame", width=230)
        self.side.pack(side=LEFT, fill=Y)
        self.side.pack_propagate(False)
        ttk.Label(self.side, text="Shiloh Hill\nObservatory\nPhotometry\n(SHOBS-P)", font=(core.UI_FONT, 13, "bold"), style="Side.TLabel", justify=LEFT).pack(
            anchor=W, padx=16, pady=(18, 4)
        )
        self.subtitle = StringVar(value=MODES[self.mode_key]["subtitle"])
        ttk.Label(self.side, textvariable=self.subtitle, style="SideHint.TLabel").pack(anchor=W, padx=16, pady=(0, 10))
        ttk.Label(self.side, text="MODE", style="SideHint.TLabel", font=(core.UI_FONT, 8, "bold")).pack(anchor=W, padx=16)
        self.mode_buttons = {}
        for key, m in MODES.items():
            btn = ttk.Button(self.side, text=m["name"], style=f"Mode{key}.TButton", command=lambda k=key: self.set_mode(k))
            btn.pack(fill=X, padx=10, pady=1)
            self.mode_buttons[key] = btn
        ttk.Label(self.side, text="STEPS", style="SideHint.TLabel", font=(core.UI_FONT, 8, "bold")).pack(
            anchor=W, padx=16, pady=(12, 0))
        self.step_buttons = []
        for i, name in enumerate(STEPS):
            btn = ttk.Button(self.side, text=f"{i + 1}   {name}", style="Step.TButton", command=lambda n=i: self.show_step(n))
            btn.pack(fill=X, padx=10, pady=2)
            self.step_buttons.append(btn)
        ttk.Label(self.side, text=f"Version {APP_VERSION}", style="SideHint.TLabel").pack(side="bottom", anchor=W, padx=16, pady=10)
        ttk.Button(self.side, text="Credits and sources", style="Step.TButton", command=self.show_credits).pack(
            side="bottom", fill=X, padx=10, pady=(0, 4)
        )

        right = ttk.Frame(outer)
        right.pack(side=LEFT, fill=BOTH, expand=True)
        banner = ttk.Frame(right, style="Banner.TFrame")
        banner.pack(fill=X)
        self.banner_text = StringVar(value=MODES[self.mode_key]["banner"])
        ttk.Label(banner, textvariable=self.banner_text, style="Banner.TLabel").pack(anchor=W, padx=14, pady=6)
        self.pages = ttk.Frame(right)
        self.pages.pack(fill=BOTH, expand=True, padx=12, pady=12)
        self.page_frames = [
            self._page_input(),
            self._page_blink(),
            self._page_calibrate(),
            self._page_photo(),
            self._page_output(),
        ]
        for frame in self.page_frames:
            frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._responsive_input_boxes(self.page_frames[0])   # 2.2.18 (item 23)
        self.discovery_page = self._page_discovery_output()
        self.discovery_page.place(relx=0, rely=0, relwidth=1, relheight=1)
        import transit_ui
        self.transit_page = transit_ui.TransitPage(self, self.pages)
        self.transit_page.frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.credits_page = self._page_credits()
        self.credits_page.place(relx=0, rely=0, relwidth=1, relheight=1)

        status = ttk.Frame(self, style="Side.TFrame")
        # 2.2.10 (John): packed ahead of the main area, so a window shorter than the pages ask for squeezes the
        # pages, not the status bar (its credits line was cut in half at the bottom edge).
        status.pack(fill=X, side="bottom", before=outer)
        self.status = StringVar(value="Choose the calibration and light folders to begin.")
        # 2.2.15 (item 8): after a report save the status line offers the upload page (a button beside the text,
        # gone with the next status message).
        status_row = ttk.Frame(status, style="Side.TFrame")
        status_row.pack(fill=X, padx=12, pady=(4, 0))
        self.status_label = ttk.Label(status_row, textvariable=self.status, style="Status.TLabel")
        self.status_label.pack(side=LEFT)
        self.status_link = ttk.Button(status_row, text="", command=self._open_status_link)
        self._status_offer = ("", "")   # (status text it belongs to, URL)
        self.status.trace_add("write", lambda *_: self._status_changed())
        ttk.Label(
            status,
            text="© Shiloh Hill Observatory    Built on Astropy, photutils, ccdproc, NumPy, SciPy, Matplotlib    "
            "Data: Gaia (ESA), APASS, Tycho-2, AAVSO VSX/VSP, SIMBAD, VizieR (CDS)    "
            "Full citations and acknowledgements: Credits and sources (sidebar)",
            style="Status.TLabel",
            foreground="#b7c7d1",
        ).pack(anchor=W, padx=12, pady=(0, 4))

    def _page_credits(self):
        page = ttk.Frame(self.pages)
        card = ttk.Frame(page, style="Card.TFrame", padding=16)
        card.pack(fill=BOTH, expand=True)
        ttk.Label(card, text="Credits and sources", font=(core.UI_FONT, 16, "bold"), style="Card.TLabel").pack(anchor=W)
        ttk.Label(
            card,
            text="Every library, catalog, service, and published method this app uses. Quoted sentences are the "
                 "acknowledgements each source asks for; please include them when you publish results made with it.",
            style="Hint.TLabel", wraplength=1200, justify=LEFT,
        ).pack(anchor=W, pady=(2, 8))
        # 2.2.18 (item 15): how to cite SHOBS-P itself, with a Copy button.
        cite = ttk.Frame(card, style="Card.TFrame")
        cite.pack(fill=X, pady=(0, 8))
        ttk.Label(cite, text="How to cite SHOBS-P", font=(core.UI_FONT, 11, "bold"), style="Card.TLabel").pack(anchor=W)
        cite_row = ttk.Frame(cite, style="Card.TFrame")
        cite_row.pack(fill=X, pady=(2, 0))
        ttk.Button(cite_row, text="Copy", command=self.copy_citation).pack(side=RIGHT, anchor="n", padx=(8, 0))
        self.citation_label = ttk.Label(cite_row, text=citation_text(), style="Card.TLabel", wraplength=900,
                                        justify=LEFT)
        self.citation_label.pack(side=LEFT, fill=X, expand=True, anchor=W)
        cite_row.bind("<Configure>", lambda e: self.citation_label.configure(wraplength=max(e.width - 120, 200)),
                      add="+")
        ttk.Label(cite, text="The DOI always leads to the newest release on Zenodo; each release also has its own DOI "
                             "there. Please cite the version you used.", style="Hint.TLabel", wraplength=900,
                  justify=LEFT).pack(anchor=W, pady=(2, 0))
        nav = ttk.Frame(card, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", pady=(8, 0))
        ttk.Button(nav, text="Save credits as text…", command=self.save_credits).pack(side=LEFT)
        ttk.Button(nav, text="Back", command=lambda: self.show_step(self._before_credits)).pack(side=RIGHT)
        box = Text(card, wrap="word", font=(core.UI_FONT, 10), bg="#ffffff", fg="#1c2b33", relief="flat", padx=8, pady=8)
        box.pack(fill=BOTH, expand=True, before=nav)
        box.insert(END, core.credits_text())
        box.configure(state="disabled")
        return page

    def copy_citation(self):
        """2.2.18 (item 15): the suggested citation onto the clipboard (Tk's clipboard; it stays after SHOBS-P closes
        on Windows)."""
        text = citation_text()
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
        except Exception as exc:
            self.status.set(f"Could not copy to the clipboard ({exc}). The citation: {text}")
            return
        self.status.set("Citation copied to the clipboard.")

    def show_credits(self):
        if not getattr(self, "_on_credits", False):
            self._before_credits = getattr(self, "current_step", 0)
        self._on_credits = True
        self.credits_page.lift()
        for i, btn in enumerate(self.step_buttons):
            btn.configure(text=f"    {i + 1}   {STEPS[i]}")

    def save_credits(self):
        path = self.output_path("SHOBS-P_credits.txt", title="Save credits", defaultextension=".txt",
                                filetypes=[("Text", "*.txt")])
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"{APP_TITLE} {APP_VERSION}\n\nHow to cite SHOBS-P:\n  {citation_text()}\n\n"
                         + core.credits_text())   # 2.2.18 (item 15)
            self.status.set(f"Wrote {path}")

    def show_step(self, index: int):
        if index != STEP_PHOTO and getattr(self, "photo_popout", None) is not None:
            self.photo_bring_back()   # 2.2.16 (item 16): leaving the Photometry step brings the image back
        self.current_step = index
        self._on_credits = False
        self._variable_view = False   # 2.2.17 (item 4)
        if hasattr(self, "back_to_transit"):
            self.back_to_transit.pack_forget()
        if index == STEP_OUTPUT and self.mode_key == "discovery":
            self.discovery_page.lift()
        elif index == STEP_OUTPUT and self.mode_key == "transits":
            self.transit_page.frame.lift()
            self.transit_page.refresh(shown=True)
        else:
            self.page_frames[index].lift()
        if index == STEP_BLINK and self._blink_folder() != self._blink_loaded_from:
            self.load_blink_list()
        elif index == STEP_BLINK:
            self._update_rejected_list()   # 2.2.17 (item 6): frames set aside elsewhere (two fields, a series)
        # 2.2.3: once the page is on screen and laid out, make its plot fill its area (the first draw could land
        # small in the top-left corner until something else redrew it, e.g. the mouse wheel).
        self.after_idle(self._fit_visible_canvases)
        self.after(250, self._fit_visible_canvases)
        if index == STEP_PHOTO and self.preview is not None and self.preview_is_debayered:
            # The image is solved when its Photometry page is first shown for this frame (Discovery 2.2.9; every
            # mode 2.2.13, so stars can be picked without pressing anything).
            if self.chart_placed:
                if self.mode_key == "discovery" and getattr(self, "_summary_due", False):
                    self._summary_due = False   # after a mode switch only, not on every visit
                    self._field_summary()
            elif getattr(self, "_auto_solved_for", None) != getattr(self, "_preview_token", 0):
                self.after(150, self._auto_solve)
        for i, btn in enumerate(self.step_buttons):
            btn.state(["!disabled"])
            btn.configure(text=("●  " if i == index else "    ") + f"{i + 1}   {STEPS[i]}")

    def _fit_visible_canvases(self):
        """2.2.3: size each plot's figure to its widget and redraw when they disagree (first show, a resize, or
        Windows display scaling reporting the real size late)."""
        for fig_name, canvas_name in (("photo_fig", "photo_canvas"), ("blink_fig", "blink_canvas"),
                                      ("out_fig", "out_canvas")):
            fig = getattr(self, fig_name, None)
            canvas = getattr(self, canvas_name, None)
            if fig is None or canvas is None:
                continue
            fit_figure_to_widget(fig, canvas)

    # ---- modes --------------------------------------------------------------------------
    def set_mode(self, mode: str, initial: bool = False):
        """Switch between Variables, Transits, and Discovery. Calibrated frames and marked stars are kept."""
        if mode not in MODES:
            return
        if not initial and mode == self.mode_key:
            return
        if not initial and self.busy:
            messagebox.showinfo(APP_TITLE, "Wait for the current run to finish (or cancel it) before switching modes.")
            return
        series_modes = ("variables", "transits")
        if (not initial and self.observations and mode in series_modes and self.series_mode
                and self.series_mode != mode):
            name = os.path.basename(self.series_path) if self.series_path else "The open series"
            if not messagebox.askyesno(
                APP_TITLE,
                f"{name} is a {MODES[self.series_mode]['name']} series. Switching to {MODES[mode]['name']} closes it "
                "here (the file on disk is not changed), so nights reduced next start a new series.\n\nSwitch?",
            ):
                return
            if not self._offer_series_save("switch modes"):   # 2.2.15 review 2: unsaved points are offered a save
                return
            self.observations = []
            self._clear_series_state()   # 2.2.15 review
            self._take_series_snapshot()
            self.draw_output()
            tp = getattr(self, "transit_page", None)
            if tp is not None:
                try:
                    tp.refresh()
                except Exception:
                    pass
        if getattr(self, "photo_popout", None) is not None:
            self.photo_bring_back()   # 2.2.16 (item 16)
        self.mode_key = mode
        m = MODES[mode]
        self._apply_theme(mode)
        self.subtitle.set(m["subtitle"])
        self.banner_text.set(m["banner"])
        for key, btn in self.mode_buttons.items():
            btn.configure(text=("▶  " if key == mode else "     ") + MODES[key]["name"])
        self._layout_photo_for_mode()
        self._layout_input_for_mode()
        # 2.2.9: stay on the step you were on (John): the page is redrawn for the new mode. A page the new mode does
        # not have (the Transit fit page) is its Output step. Discovery's field solve runs when its Photometry page
        # is on screen (see show_step).
        if not initial:
            self._summary_due = mode == "discovery"
            self.show_step(getattr(self, "current_step", 0))
        self.settings["mode"] = mode
        self._save_settings()
        if not initial:
            self.status.set(f"{m['name']} mode. Calibrated frames and marked stars are kept.")

    def _load_settings(self) -> dict:
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
                json.dump(self.settings, fh, indent=2)
        except OSError:
            pass

    def _set_window_icon(self):
        """SHOBS-P icon in the title bar and taskbar (Windows groups the taskbar button under our own ID)."""
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ShilohHill.SHOBS-P")
            except Exception:
                pass
        ico = os.path.join(APP_DIR, "shobs_p.ico")
        png = os.path.join(APP_DIR, "shobs_p.png")
        try:
            if sys.platform == "win32" and os.path.exists(ico):
                self.iconbitmap(default=ico)
            elif os.path.exists(png):
                self._icon_image = PhotoImage(file=png)
                self.iconphoto(True, self._icon_image)
        except Exception:
            pass

    def report_callback_exception(self, exc, val, tb):
        """Errors inside the window: log them and say so, since a desktop shortcut has no console to show them."""
        text = "".join(traceback.format_exception(exc, val, tb))
        log_to_file("Error in the window; traceback follows.")   # 2.2.18 (item 24): when it happened
        log_to_file(text + "\n", stamp=False)
        try:
            self.log("Error: " + str(val))
        except Exception:
            pass
        messagebox.showerror(APP_TITLE, f"Something went wrong:\n{val}\n\nDetails were written to {LOG_FILE}.")

    def log(self, text: str):
        """Main thread only. Worker threads use self.call_ui(self.log, text)."""
        log_to_file(text)   # 2.2.18 (item 24): every Log line, with the time, so a run can be traced afterwards
        for box in (self.log_box, getattr(self, "photo_log", None)):
            if box is None:
                continue
            box.insert(END, text + "\n")
            if int(box.index("end-1c").split(".")[0]) > 6000:
                box.delete("1.0", "1001.0")
            box.see(END)
        self.status.set(text.strip().splitlines()[0] if text.strip() else "")

    def _folder_row(self, parent, label, variable, key):
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill=X, pady=4)
        ttk.Label(row, text=label, width=12, style="Card.TLabel").pack(side=LEFT, padx=(0, 6))
        ttk.Entry(row, textvariable=variable).pack(side=LEFT, fill=X, expand=True)
        ttk.Button(row, text="Browse", command=lambda: self._browse(variable, key)).pack(side=LEFT, padx=(6, 0))
        if key in ("Bias", "Dark", "Flat"):
            # 2.2.8: a calibration entry can also be one SHOBS-P master file.
            ttk.Button(row, text="Master file…", command=lambda: self._browse_master(variable, key)).pack(
                side=LEFT, padx=(4, 0))
        ttk.Button(row, text="✕", width=3, command=lambda: self._clear_folder(variable, key)).pack(side=LEFT, padx=(4, 0))
        count = StringVar(value="" if key == "Output" else "0 files")
        ttk.Label(row, textvariable=count, width=16, style="Card.TLabel").pack(side=LEFT, padx=(8, 0))
        self._counts[key] = count

    def _browse(self, variable, key):
        path = filedialog.askdirectory(title=f"Select {key} folder")
        if not path:
            return
        variable.set(path)
        if key == "Output":
            self._output_changed()
            return
        self._counts[key].set(self._count_text(key, path))
        self.status.set(f"{key}: {self._counts[key].get()}")

    def _count_text(self, key, path) -> str:
        """'51 files', '51 files + master', or 'master file' (2.2.8: saved masters are not counted as frames)."""
        if key in ("Bias", "Dark", "Flat"):
            frames, master = core.calibration_source(path)
            if master:
                return "master file"
            saved = any(core.is_shobsp_master_name(f) for f in core.list_fits(path)
                        + core.list_fits(os.path.join(path or "", core.MASTER_DIR)))
            return f"{len(frames)} files" + (" + master" if saved else "")
        return f"{len(core.list_fits(path))} files"

    def _browse_master(self, variable, key):
        path = filedialog.askopenfilename(title=f"Select a SHOBS-P master {key.lower()}",
                                          filetypes=[("FITS", "*.fits *.fit *.fts"), ("All files", "*.*")])
        if not path:
            return
        problem = core.check_master_file(path, key.lower())
        if problem:
            messagebox.showerror(APP_TITLE, problem)
            return
        variable.set(path)
        self._counts[key].set("master file")
        self.status.set(f"{key}: SHOBS-P master {os.path.basename(path)}")

    def _clear_folder(self, variable, key):
        variable.set("")
        if key == "Output":
            self._output_changed()
            return
        self._counts[key].set("0 files")
        self.status.set(f"{key} folder cleared." + (" Calibration will skip it." if key in ("Bias", "Dark", "Flat")
                                                     else ""))

    def _remember_site(self, key: str, var):
        """2.2.4: latitude, longitude and elevation are remembered between sessions (settings file only)."""
        if getattr(self, "_own_setup", None) is not None or getattr(self, "_loading_meta", False):
            return   # a borrowed site (MicroObservatory frames) or a series file's site is not the user's own
        value = var.get().strip()
        if self.settings.get(key, "") != value:
            self.settings[key] = value
            self._save_settings()

    OPTICS_DEFAULTS = {"focal_mm": 2900.0, "pixel_um": 3.76}   # 2.2.18 (item 11): until your own are known

    def _saved_optics(self, key: str) -> float:
        """2.2.18 (item 11): the saved focal length / pixel size, else the built-in starting value."""
        try:
            v = float(self.settings.get(key))
            if math.isfinite(v) and v > 0:
                return v
        except (TypeError, ValueError):
            pass
        return self.OPTICS_DEFAULTS[key]

    def _optics_unset(self, key: str) -> bool:
        """2.2.18 (item 11): the box still holds the built-in starting value, never saved as yours: the lights' header
        fills it like an empty box (before 2.2.18 it asked whether to replace "your" 2900 mm)."""
        if key not in self.OPTICS_DEFAULTS or key in self.settings:
            return False
        try:
            return abs(float(getattr(self, key).get()) - self.OPTICS_DEFAULTS[key]) < 1e-9
        except Exception:
            return True

    def _remember_own_value(self, key: str, var):
        """2.2.18 (item 11): focal length, pixel size and observer code are remembered between sessions (settings
        file only). Not while a series is being loaded or another telescope's setup is in use (MicroObservatory,
        frames from another site), and never the demo set's. Review: the observer code is not part of a borrowed
        setup, so it is remembered then too; the value is captured now and saved after a short pause (not at every
        keystroke)."""
        if getattr(self, "_loading_meta", False) or not hasattr(self, "settings"):
            return
        if key != "observer_code" and getattr(self, "_own_setup", None) is not None:
            return
        try:
            value = var.get()
        except Exception:
            return   # an empty or half-typed number box (real Tk raises TclError)
        if key == "observer_code":
            value = str(value or "").strip()
            if self.settings.get(key, "") == value:
                return
        else:
            try:
                value = float(value)
            except (TypeError, ValueError):
                return
            if not (math.isfinite(value) and value > 0):
                return
            if key not in self.settings and abs(value - self.OPTICS_DEFAULTS[key]) < 1e-9:
                return   # the starting value put back is not a value of yours
        pending = self.__dict__.setdefault("_own_value_after", {})
        if pending.get(key):
            try:
                self.after_cancel(pending[key])
            except Exception:
                pass
        pending[key] = self.after(700, lambda k=key, v=value: self._store_own_value(k, v))

    def _store_own_value(self, key: str, value):
        """2.2.18 (item 11, review nit): the debounced save of _remember_own_value."""
        self.__dict__.setdefault("_own_value_after", {}).pop(key, None)
        if self.settings.get(key) != value:
            self.settings[key] = value
            self._save_settings()

    def _remember_telescope(self):
        """2.2.16 (item 5): the Optics box's Telescope name is remembered between sessions (your own setup only)."""
        if getattr(self, "_own_setup", None) is not None or getattr(self, "_loading_meta", False):
            return
        value = self.telescope_name.get().strip()
        if self.settings.get("telescope_name", "") != value:
            self.settings["telescope_name"] = value
            self._save_settings()

    # ---- 2.2.18 (item 8): mount type ------------------------------------------------------------------------
    def _mount_choice_key(self) -> str:
        """The Optics box's Mount choice as core.MOUNT_GEM / core.MOUNT_ALTAZ."""
        label = self.mount_choice.get()
        return next((k for k, v in core.MOUNT_LABELS.items() if v == label), core.MOUNT_GEM)

    def _remember_mount(self):
        key = self._mount_choice_key()
        self._mount_cache = {}
        if self.settings.get("mount") != key:
            self.settings["mount"] = key
            self._save_settings()
        self._update_mount_hint()

    def _mount_info(self, lights=None) -> dict:
        """The mount these lights were taken with: {"mount", "note", "detected"}. From the headers when they are sure
        (core.detect_mount), else the Mount choice. Reads up to 12 headers spread over the night (review: kept short,
        this runs on the UI thread); cached per folder and choice."""
        if lights is None:
            try:
                lights = self._lights()
            except Exception:
                lights = []
        lights = list(lights or [])
        choice = self._mount_choice_key()
        key = (tuple(lights[:1] + lights[-1:]), len(lights), choice)
        cached = self._mount_cache.get(key)
        if cached is not None:
            return cached
        headers = []
        if lights:
            for k in sorted({int(round(i)) for i in np.linspace(0, len(lights) - 1, min(12, len(lights)))}):
                try:
                    headers.append(core.read_header(lights[k]))
                except Exception:
                    continue
        detected = core.detect_mount(headers)
        mount, note = core.resolve_mount(detected, choice)
        info = {"mount": mount, "note": note, "detected": detected}
        self._mount_cache = {key: info}
        return info

    def _update_mount_hint(self, lights=None, log: bool = False):
        """The hint under the Mount choice: what the lights' headers say. log=True also writes it to the log, with
        a zenith-keyhole note for an alt-az night that went very high."""
        try:
            info = self._mount_info(lights)
        except Exception:
            self.mount_hint.set("")
            return None
        d = info["detected"]
        if d.get("sure") and (info["mount"] == core.MOUNT_ALTAZ or info["mount"] != self._mount_choice_key()):
            hint = f"These lights: {core.mount_label(info['mount'])} from the headers ({d.get('why')})."
            if info["mount"] != self._mount_choice_key():
                hint += f" Set Mount to {core.mount_label(info['mount'])} so the transit planners use it."
        elif d.get("mount") and d["mount"] != info["mount"]:
            hint = f"Headers: {d.get('why')}; set Mount if that is right."
        else:
            hint = ""
        self.mount_hint.set(hint)
        if log:
            # review: a note that only hints at the mount (a PWI4 name) is said once per folder
            folder = os.path.dirname(str((list(lights or []) or [""])[0]))
            seen = self.__dict__.setdefault("_mount_notes_seen", set())
            if info["note"] and (d.get("sure") or info["mount"] != core.MOUNT_GEM or (folder, info["note"]) not in seen):
                self.log(info["note"])
                seen.add((folder, info["note"]))
            top = d.get("max_alt")
            if info["mount"] == core.MOUNT_ALTAZ and top is not None and top >= tcore.ZENITH_KEYHOLE_ALT:
                self.log(f"Zenith keyhole: the field reached altitude {top:.1f}° (above {tcore.ZENITH_KEYHOLE_ALT:g}°), "
                         "where an alt-az mount's azimuth speed and the field rotation get extreme; frames there can "
                         "trail or smear. Check them on the Blink page.")
        return info

    def _telescope_text(self, header: dict | None = None) -> str:
        """2.2.16 (item 5): the telescope as written out: the Telescope field, else a TELESCOP that is not a mount,
        with the Optics box's aperture and focal length ("0.36 m, f 2900 mm" when the header names only the mount)."""
        def box(var):
            try:
                return var.get()
            except Exception:
                return None
        if header is None:
            try:
                lights = self._lights()
                header = core.read_header(lights[0]) if lights else {}
            except Exception:
                header = {}
        return core.telescope_text(self.telescope_name.get(), str((header or {}).get("TELESCOP") or ""),
                                   box(self.aperture_mm), box(self.focal_mm))

    def _remember_scint(self):
        """2.2.2: the aperture and the scintillation switch are remembered between sessions."""
        # 2.2.9: a borrowed aperture (another telescope's frames) is not the user's own: not remembered.
        if getattr(self, "_own_setup", None) is None:
            self.settings["aperture_mm"] = self.aperture_mm.get().strip()
            self.settings.pop("aperture_cm", None)
        self.settings["use_scint"] = bool(self.use_scint.get())
        self.aperture_hint.set(core.aperture_mm_hint(self.aperture_mm.get()))
        self._save_settings()
        if hasattr(self, "out_canvas") and self.observations:
            try:
                self.draw_output()
            except Exception:
                pass

    def _scint_setup(self) -> dict | None:
        """2.2.2: what the scintillation term needs, or None when it is switched off or the aperture is blank.
        Young (1967) as modified by Osborn et al. (2015); see photometry_core.scintillation_sigma."""
        if not self.use_scint.get():
            return None
        try:
            d_mm = float(self.aperture_mm.get())
        except (TypeError, ValueError):
            return None
        if not d_mm > 0:
            return None
        try:
            h = float(self.elevation.get())
        except (TypeError, ValueError):
            h = 0.0
        n = len(self.active_comps or self.comps_used) or 1
        return {"aperture_m": d_mm / 1000.0, "altitude_m": h if math.isfinite(h) else 0.0, "n_comps": n}

    def _scint_note(self) -> str:
        setup = self._scint_setup()
        if setup is not None and self.observations and not any(
                math.isfinite(core.scint_mag(o, setup)) for o in self.observations):
            return "scintillation not included (no airmass: RA/Dec or site unknown)"
        if setup is None:
            return ("scintillation not included (" + ("switched off" if not self.use_scint.get()
                                                       else "telescope aperture not set") + ")")
        return (f"scintillation included (Osborn et al. 2015; aperture {setup['aperture_m'] * 1000:g} mm, "
                f"elevation {setup['altitude_m']:.0f} m)")

    def _output_typed(self):
        """Typed (or pasted) into the Output box: used for this session only."""
        folder = self.output_dir.get().strip()
        self._counts["Output"].set("in use" if folder else "")

    def _output_changed(self):
        folder = self.output_dir.get().strip()
        if folder:
            self._counts["Output"].set("in use")
            self.status.set(f"Save buttons now write into {folder} (never over an existing file).")
        else:
            self._counts["Output"].set("")
            self.status.set("No output folder: each Save asks where to put the file.")

    # ---- output folder (2.2) --------------------------------------------------------------
    @staticmethod
    def _unique_path(path: str) -> str:
        """path, or path with _2, _3 … before the extension when a file (or folder) of that name exists."""
        if not os.path.exists(path):
            return path
        root, ext = os.path.splitext(path)
        if root.endswith(".fits"):
            root, ext = root[:-5], ".fits" + ext
        k = 2
        while os.path.exists(f"{root}_{k}{ext}"):
            k += 1
        return f"{root}_{k}{ext}"

    @staticmethod
    def _suffixed(path: str, k: int) -> str:
        if k <= 1:
            return path
        root, ext = os.path.splitext(path)
        return f"{root}_{k}{ext}"

    def _family_names(self) -> list[str]:
        """The file names the three result saves would use now (report, CSV, PNG). 2.2.5: one pattern, modeled on
        the Transits report: AAVSO_<star>_<filter>_<date>_SHOBS-P.txt, and <star>_<filter>_<date>_SHOBS-P_lightcurve
        .csv / .png, so the three files of one save sort together. The date is the UTC date of the first point saved;
        several nights give the first and last (16-SEP-2026_to_06-OCT-2026)."""
        star = (self.star_id.get() or "").strip() or self.planet_name.get().strip() or "target"
        stem = f"{star.replace(' ', '_')}_{self._filter_code()}"
        dates = self._save_dates()
        if dates:
            stem += f"_{dates}"
        stem += "_SHOBS-P"
        names = [f"AAVSO_{stem}.txt", f"{stem}_lightcurve.csv", f"{stem}_lightcurve.png"]
        names = ["".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in n) for n in names]
        tp = getattr(self, "transit_page", None)
        if self.mode_key == "transits" and tp is not None and tp.result is not None:
            # 2.2.17 (item 9 nit): the Transit fit page's report and PNG join the set, so one fit's report, CSV and
            # PNG share one number (the PNG had its own counter: _5 beside _4).
            try:
                names += tp.family_names()
            except Exception:
                pass
        return names

    def _save_dates(self) -> str:
        """2.2.5: DD-MON-YYYY of the first point being saved, or first_to_last when they fall on different UTC dates."""
        points = self._usable() if self.observations else []
        if self._save_one_night and self.highlight_night:
            points = [o for o in points if o.night == self.highlight_night]
        jds = [o.jd for o in points if o.jd is not None and math.isfinite(o.jd)]
        if not jds:
            return ""

        def day(jd):
            return core.jd_to_datetime_utc(jd).strftime("%d-%b-%Y").upper()
        first, last = day(min(jds)), day(max(jds))
        return first if first == last else f"{first}_to_{last}"

    def _results_key(self):
        """2.2.3: identifies the results on screen; a new run, an added night or other comps make a new key."""
        obs = self.observations or []
        tp = getattr(self, "transit_page", None)
        fit = None
        if self.mode_key == "transits" and tp is not None and tp.result is not None:
            import transit_ui as _tu
            fit = _tu.fit_serial(tp.result)   # 2.2.17 review: a fit counter (id() can be reused)
        # 2.2.17: id of the list itself (an empty `or []` made a new key on every call)
        return (id(self.observations), len(obs), round(obs[0].jd, 6) if obs else 0,
                round(obs[-1].jd, 6) if obs else 0,
                tuple(self.active_comps or self.comps_used), self.highlight_night if self._save_one_night else None,
                fit)   # 2.2.17 (item 9 nit): a new transit fit starts a new numbered set

    def _family_path(self, folder: str, filename: str) -> str:
        """2.2.3: one number for one save. The first of the three files saved for these results picks the lowest
        number free for all three names; the others reuse it. Saving the same kind again starts a new set."""
        key = (self._results_key(), os.path.normcase(os.path.abspath(folder)))
        state = getattr(self, "_save_set", None)
        names = self._family_names()
        if filename not in names:
            names = names + [filename]
        if state and state["key"] == key and filename not in state["used"]:
            path = self._suffixed(os.path.join(folder, filename), state["k"])
            if not os.path.exists(path):
                state["used"].add(filename)
                return path
        k = 1
        while any(os.path.exists(self._suffixed(os.path.join(folder, n), k)) for n in names):
            k += 1
        self._save_set = {"key": key, "k": k, "used": {filename}}
        return self._suffixed(os.path.join(folder, filename), k)

    def _out_folder(self) -> str:
        folder = self.output_dir.get().strip()
        if not folder:
            return ""
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            messagebox.showwarning(APP_TITLE, f"The output folder could not be used ({exc}); choose where to save.")
            return ""
        return folder

    def output_path(self, filename: str, title: str = "Save", defaultextension: str = "", filetypes=None, parent=None,
                    family: bool = False):
        """Where a Save button writes: into the Input page's output folder (never over an existing file), or, with no
        output folder, wherever the save dialog says. '' when the dialog is cancelled.
        family: the file is one of the AAVSO report / light-curve CSV / plot PNG set (2.2.3), which share one
        number (_2, _3 ...) for the same results, so the three files of one save always match."""
        folder = self._out_folder()
        safe = "".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in filename)
        if folder and family:
            return self._family_path(folder, safe)
        if folder:
            return self._unique_path(os.path.join(folder, safe))
        kwargs = {"title": title, "initialfile": safe}
        if defaultextension:
            kwargs["defaultextension"] = defaultextension
        if filetypes:
            kwargs["filetypes"] = filetypes
        if parent is not None:
            kwargs["parent"] = parent
        return filedialog.asksaveasfilename(**kwargs) or ""

    def saved_notice(self, path: str, advice: str = ""):
        """After a save: with an output folder the status bar says where it went (no dialog to click away);
        without one, the usual dialog."""
        if self.output_dir.get().strip():
            folder = os.path.basename(os.path.dirname(path)) or os.path.dirname(path)
            self.status.set(f"Saved {os.path.basename(path)} to {folder}." + (f" {advice}" if advice else ""))
            self.log(f"Saved {path}")
        else:
            self.status.set(f"Saved {path}")
            messagebox.showinfo(APP_TITLE, f"Saved\n{path}" + (f"\n\n{advice}" if advice else ""))

    # ---- upload links (2.2.15, item 8) -------------------------------------------------------
    def open_link(self, url: str, what: str = ""):
        """Open a web page in the default browser; the status line says where, or what to type if it fails."""
        try:
            import webbrowser
            ok = webbrowser.open(url)
        except Exception:
            ok = False
        if ok is False:
            self.status.set(f"Could not open a browser: go to {url}")
            self.log(f"Could not open a browser for {url}")
            return False
        self.status.set(f"Opened {what or url} in your browser.")
        self.log(f"Opened {url}")
        return True   # 2.2.18 (item 10): the upload buttons mark "sent" only after this

    def offer_upload(self, path: str, button: str, url: str, where: str):
        """After a report save: the status line names the saved file and offers the upload page."""
        # 2.2.15 review: short, and the button has its own slot on the right, so a long line never hides it.
        text = f"Saved {os.path.basename(path)} — upload to {where}:"
        self.status.set(text)
        self._status_offer = (text, url)
        try:
            self.status_link.configure(text=button)
            self.status_link.pack(side=RIGHT, padx=(8, 0), before=self.status_label)
        except Exception:
            pass

    def _open_status_link(self):
        text, url = self._status_offer
        if url:
            # 2.2.18 (item 10): the status line's upload button is an upload button too.
            page = {core.AAVSO_WEBOBS_URL: "webobs", core.AAVSO_EXOPLANET_URL: "exoplanet"}.get(url)
            plan = self._upload_check(page) if page else {}
            if plan is None:
                return
            if self.open_link(url) and page:
                self._mark_sent(page, plan)

    def _status_changed(self):
        text, url = getattr(self, "_status_offer", ("", ""))
        if url and self.status.get() != text:
            self._status_offer = ("", "")
            try:
                self.status_link.pack_forget()
            except Exception:
                pass

    def open_webobs(self):
        plan = self._upload_check("webobs")   # 2.2.18 (item 10)
        if plan is None:
            return
        if self.open_link(core.AAVSO_WEBOBS_URL, "AAVSO WebObs (upload the report with its file tool)"):
            self._mark_sent("webobs", plan)

    # ---- 2.2.18 (item 10): "sent" marks, and the duplicate warning at the upload buttons -----------------------
    UPLOAD_PAGES = {"webobs": ("AAVSO WebObs", "AAVSO", "WebObs"),
                    "exoplanet": ("the AAVSO Exoplanet Database", "AAVSO", "AAVSO exo"),
                    "exofop": ("ExoFOP", "ExoFOP", "ExoFOP")}

    @staticmethod
    def _report_id(entry: dict) -> str:
        """A saved report's identity: its full path (normalized), or its file name for entries saved before 2.2.18."""
        path = entry.get("path")
        if path:
            return os.path.normcase(os.path.abspath(str(path)))
        return str(entry.get("file") or "")

    def _remember_report(self, page: str, nights, path: str) -> None:
        """A report (or ExoFOP package) just saved for an upload page: noted for its nights in reports_saved (kept in
        the series), so opening that page later, in this session or the next, marks them sent."""
        stamp = time.strftime("%Y-%m-%d %H:%M")
        entry = {"file": os.path.basename(os.path.normpath(path)), "date": stamp, "scope": "night", "to": page,
                 "path": os.path.abspath(path), "stamp": time.time()}
        rid = self._report_id(entry)
        for night in sorted({str(n) for n in nights if n}):
            have = self.reports_saved.setdefault(night, [])
            if not any(e.get("to", "webobs") == page and self._report_id(e) == rid for e in have):
                have.append(dict(entry))
        if self.series_path:
            try:
                self._write_series(self.series_path)
            except OSError:
                pass

    def _last_report(self, page: str) -> dict | None:
        """The last report saved for an upload page, from reports_saved: {"id", "file", "nights"}; None if none."""
        best = None
        have = {o.night for o in self.observations}
        for night, entries in self.reports_saved.items():
            for e in entries:
                if not isinstance(e, dict) or e.get("to", "webobs") != page:
                    continue
                try:
                    when = float(e["stamp"])
                except (KeyError, TypeError, ValueError):
                    try:
                        when = time.mktime(time.strptime(str(e.get("date") or "")[:16], "%Y-%m-%d %H:%M"))
                    except ValueError:
                        when = 0.0
                if best is None or when > best[0]:
                    best = (when, self._report_id(e), str(e.get("file") or "?"))
        if best is None:
            return None
        nights = sorted(n for n, entries in self.reports_saved.items() if n in have and any(
            isinstance(e, dict) and e.get("to", "webobs") == page and self._report_id(e) == best[1] for e in entries))
        return {"id": best[1], "file": best[2], "nights": nights}

    def _upload_check(self, page: str):
        """Before an upload page opens: the nights of the last report saved for it that were already sent in
        another report get one question (open anyway?). Returns None when the page must not open, else a plan for
        _mark_sent once it has opened ({} when there is nothing to mark: nothing saved for this page, or every
        night of the last report already marked for it, e.g. Open ExoFOP… used to browse)."""
        last = self._last_report(page)
        if not last or not last["nights"]:
            return {}
        where, who, _short = self.UPLOAD_PAGES[page]
        before, todo = [], []
        for night in last["nights"]:
            entries = [e for e in self.reports_sent.get(night, []) if e.get("to") == page]
            if any(self._report_id(e) == last["id"] for e in entries):
                continue   # this very report is already marked for this night
            todo.append(night)
            if entries:
                before.append((night, entries[-1]))
        if not todo:
            return {}
        if before:
            dates = sorted({str(e.get("date") or "?").split(" ")[0] for _n, e in before})
            files = sorted({str(e.get("file") or "?") for _n, e in before})
            names = ", ".join(self.night_label(n, short=True) for n, _e in before)
            word = "Nights" if len(before) > 1 else "Night"
            verb = "were" if len(before) > 1 else "was"
            if not messagebox.askyesno(
                    APP_TITLE,
                    f"{word} {names} {verb} already sent on {' and '.join(dates)} (in {', '.join(files)}); "
                    f"{who} will hold duplicates. Open anyway?\n\n"
                    f"{last['file']} holds {'them' if len(before) > 1 else 'it'} again. Upload only the nights not "
                    "sent yet (save a report of just one night: highlight it first), or delete the earlier upload "
                    f"at {who}. A night that was never really sent can be unmarked: double-click it in the night "
                    "table."):
                self.log(f"{where}: not opened ({names} already sent).")
                return None
        return {"id": last["id"], "file": last["file"], "nights": todo}

    def _mark_sent(self, page: str, plan: dict) -> None:
        """After the upload page opened: the plan's nights are marked sent to it (stored in the series)."""
        if not plan or not plan.get("nights"):
            return
        where = self.UPLOAD_PAGES[page][0]
        stamp = time.strftime("%Y-%m-%d %H:%M")
        for night in plan["nights"]:
            self.reports_sent.setdefault(night, []).append(
                {"to": page, "file": plan["file"], "path": plan["id"], "date": stamp})
        self.log(f"Marked sent to {where}: " + ", ".join(self.night_label(n, short=True) for n in plan["nights"])
                 + f" ({plan['file']}).")
        self._sent_changed()

    def _sent_changed(self) -> None:
        if self.series_path:
            try:
                self._write_series(self.series_path)
            except OSError:
                pass
        try:
            self.draw_output()
        except Exception:
            traceback.print_exc()

    def _sent_text(self, night: str) -> str:
        """The night table's Sent column: 'WebObs 18 Oct', or '—'."""
        parts = []
        for e in self.reports_sent.get(night, []):
            short = self.UPLOAD_PAGES.get(e.get("to"), ("", "", str(e.get("to") or "?")))[2]
            part = f"{short} {str(e.get('date') or '')[:10]}".strip()   # review: ISO date (2026-10-18)
            if part not in parts:
                parts.append(part)
        return ("✓ " + ", ".join(parts)) if parts else "—"

    def unmark_sent(self, night: str, ask: bool = True) -> bool:
        """A night marked sent by hand back to not sent (the upload never went through, or was deleted)."""
        entries = self.reports_sent.get(night) or []
        if not entries:
            return False
        if ask:
            pages = sorted({self.UPLOAD_PAGES.get(e.get("to"), (str(e.get("to")),))[0] for e in entries})
            if not messagebox.askyesno(
                    APP_TITLE,
                    f"{self.night_label(night, short=True)} is marked sent to {' and '.join(pages)} "
                    f"({', '.join(sorted({str(e.get('file')) for e in entries}))}).\n\nMark it as not sent? Do this "
                    "only when that upload did not go through or you deleted it there; the next upload of a report "
                    "with this night will then not warn."):
                return False
        self.reports_sent.pop(night, None)
        self.log(f"{self.night_label(night, short=True)}: marked as not sent.")
        self._sent_changed()
        return True

    def _on_night_double(self, event):
        job = getattr(self, "_night_click_job", None)   # review: the double-click's single clicks do nothing
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
            self._night_click_job = None
        row = self.night_table.identify_row(event.y)
        night = self._night_by_item.get(row) if row else None
        if night is not None and self.reports_sent.get(night):
            self.unmark_sent(night)

    def output_directory(self, title: str, parent=None) -> str:
        folder = self._out_folder()
        if folder:
            return folder
        kwargs = {"title": title}
        if parent is not None:
            kwargs["parent"] = parent
        return filedialog.askdirectory(**kwargs) or ""

    def _group(self, parent, title):
        box = ttk.LabelFrame(parent, text=title, style="Group.TLabelframe", padding=(12, 6, 12, 10))
        return box

    # ---- 2.2.18 (item 23): boxes that keep their entry boxes at half-screen width ---------------------------------
    RESPONSIVE_BOXES = ("AAVSO report", "Site", "Optics")

    def _responsive_input_boxes(self, page):
        """2.2.18 (item 23): the Input page's right-hand boxes (label | entry | hint grids). Before, only the entry
        column had a weight, so when the window was narrower than the boxes asked for (snapped to half a 4K screen) Tk
        took all the missing width from that column and the entry boxes vanished, while the labels and hints kept
        their full width. Now the entry column keeps a minimum width (about 8 digits), the hint column shares the
        shrinking and its hints wrap onto more lines, and the full-width rows (checkbox, red hint) wrap to the box."""
        found = []

        def walk(widget, depth=0):
            if depth > 6:
                return
            try:
                kids = list(widget.winfo_children())
            except Exception:
                return
            for kid in kids:
                try:
                    title = str(kid.cget("text"))
                except Exception:
                    title = ""
                if title in self.RESPONSIVE_BOXES and isinstance(kid, ttk.LabelFrame):
                    found.append(kid)
                else:
                    walk(kid, depth + 1)
        walk(page)
        for box in found:
            self._responsive_grid_box(box)
        return found

    def _responsive_grid_box(self, box, entry_col: int = 1, hint_col: int = 2, min_chars: int = 8):
        scale = ui_scale(self)
        try:
            from tkinter import font as tkfont
            px = int(tkfont.Font(root=self, family=core.UI_FONT, size=10).measure("0" * min_chars))
        except Exception:
            px = 8 * min_chars
        box.columnconfigure(entry_col, weight=1, minsize=int(px + 12 * scale))
        box.columnconfigure(hint_col, weight=1)
        hints, spans = [], []
        for kid in list(box.winfo_children()):
            try:
                info = kid.grid_info()
                col, span = int(info.get("column", -1)), int(info.get("columnspan", 1))
            except Exception:
                continue
            if col == hint_col and span == 1 and isinstance(kid, ttk.Label):
                kid.grid_configure(sticky="ew")   # fills its column, so it can wrap to it and unwrap again
                hints.append(kid)
            elif col == 0 and span > 1 and isinstance(kid, (ttk.Label, ttk.Checkbutton)):
                spans.append(kid)
        pad = int(4 * scale)
        for lab in hints:
            lab.bind("<Configure>", lambda e, w=lab: w.configure(wraplength=max(int(e.width) - pad, 40)), add="+")
        style = ttk.Style(self)
        for k, kid in enumerate(spans):
            if isinstance(kid, ttk.Checkbutton):
                # ttk checkbuttons take their wrap length from their style (the label element's -wraplength).
                name = f"Wrap{id(box) % 100000}_{k}.TCheckbutton"
                try:
                    style.configure(name, wraplength=0)
                    kid.configure(style=name)
                except Exception:
                    continue
                spans[k] = (kid, name)
            else:
                spans[k] = (kid, None)

        def wrap_spans(event):
            width = max(int(event.width) - int(30 * scale), 120)
            for kid, name in spans:
                try:
                    if name:
                        style.configure(name, wraplength=width)
                    else:
                        kid.configure(wraplength=width)
                except Exception:
                    pass
        if spans:
            box.bind("<Configure>", wrap_spans, add="+")

    def _field(self, parent, row, label, var, width=24, hint=""):
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=0, sticky=W, padx=(0, 10), pady=3)
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        if hint:
            ttk.Label(parent, text=hint, style="Hint.TLabel").grid(row=row, column=2, sticky=W, padx=(8, 0))
        return entry

    def _page_input(self):
        page = ttk.Frame(self.pages)
        card = ttk.Frame(page, style="Card.TFrame", padding=16)
        card.pack(fill=BOTH, expand=True)
        ttk.Label(card, text="Input", font=(core.UI_FONT, 16, "bold"), style="Card.TLabel").pack(anchor=W)
        ttk.Label(
            card,
            text="Frames, target, site, and where the report goes. Comparison, check, and watch stars are picked on "
                 "the Photometry page. Settings are saved with the series.",
            style="Hint.TLabel",
        ).pack(anchor=W, pady=(2, 8))

        # 2.2.15 (item 7): which series this run joins, with Load series… / Close series beside it.
        srow = ttk.Frame(card, style="Card.TFrame")
        srow.pack(fill=X, pady=(0, 8))
        self.series_status = StringVar(value="")
        # 2.2.16 (item 12): the line wraps to the room left beside the buttons (no fixed width).
        self.close_series_button = ttk.Button(srow, text="Close series", command=self.close_series)
        self.close_series_button.pack(side=RIGHT, padx=(6, 0))
        load_in = ttk.Button(srow, text="Load series…", command=self.load_series)
        load_in.pack(side=RIGHT, padx=(12, 0))
        self._add_tooltip(load_in, self.LOAD_SERIES_TIP)
        status_label = ttk.Label(srow, textvariable=self.series_status, style="Card.TLabel", justify=LEFT)
        status_label.pack(side=LEFT, fill=X, expand=True)
        status_label.bind("<Configure>", lambda e: status_label.configure(
            wraplength=max(int(200 * ui_scale(status_label)), e.width - int(8 * ui_scale(status_label)))), add="+")
        self.update_series_status()

        nav = ttk.Frame(card, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", pady=(12, 0))
        ttk.Button(nav, text="Scan folders", command=self.scan_folders).pack(side=LEFT)
        ttk.Button(nav, text="Try it with sample data…", command=self.write_demo).pack(side=LEFT, padx=8)
        ttk.Button(nav, text="Next: Blink", command=lambda: self.show_step(STEP_BLINK)).pack(side=RIGHT)

        # Frames
        frames = self._group(card, "Frames")
        frames.pack(fill=X)
        self._folder_row(frames, "Bias", self.bias_dir, "Bias")
        self._folder_row(frames, "Dark", self.dark_dir, "Dark")
        self._folder_row(frames, "Flat", self.flat_dir, "Flat")
        self._folder_row(frames, "Lights", self.light_dir, "Lights")
        self._folder_row(frames, "Output", self.output_dir, "Output")
        ttk.Label(frames, text="Output: where every Save button writes (reports, light curves, plots, packages). Leave "
                               "it empty to be asked each time. Files already there are never overwritten; a new "
                               "copy gets _2, _3 ….", style="Hint.TLabel", wraplength=1100, justify=LEFT).pack(anchor=W)

        grid = ttk.Frame(card, style="Card.TFrame")
        grid.pack(fill=X, pady=(10, 0))
        grid.columnconfigure(0, weight=3, uniform="col")
        grid.columnconfigure(1, weight=2, uniform="col")
        left = ttk.Frame(grid, style="Card.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = ttk.Frame(grid, style="Card.TFrame")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        # Target
        target = self._group(left, "Target")
        target.pack(fill=X)
        self.target_group = target
        ttk.Label(target, text="Star ID", style="Card.TLabel").grid(row=0, column=0, sticky=W, padx=(0, 10), pady=3)
        cell = ttk.Frame(target, style="Card.TFrame")
        cell.grid(row=0, column=1, columnspan=2, sticky="ew", pady=3)
        star_entry = ttk.Entry(cell, textvariable=self.star_id, width=24)
        star_entry.pack(side=LEFT, fill=X, expand=True)
        # 2.2.5: the target is looked up by itself when you press Enter or leave the box with a new name.
        star_entry.bind("<Return>", lambda _e: self._auto_lookup())
        star_entry.bind("<FocusOut>", lambda _e: self._auto_lookup())
        ttk.Button(cell, text="Look up again", command=self.lookup_star).pack(side=LEFT, padx=(6, 0))
        ttk.Button(cell, text="Clear target", command=self.clear_target).pack(side=LEFT, padx=(6, 0))
        # How AAVSO spells the name, when the typed one differs only in spacing or capitals (2.2).
        self.name_hint = StringVar(value="")
        self.name_row = ttk.Frame(target, style="Card.TFrame")
        ttk.Label(self.name_row, textvariable=self.name_hint, style="Warn.TLabel").pack(side=LEFT)
        ttk.Button(self.name_row, text="Use it", command=self._use_suggested_name).pack(side=LEFT, padx=(8, 0))
        self.name_row.grid(row=1, column=1, columnspan=2, sticky=W)
        self.name_row.grid_remove()
        self.ra_sexa = StringVar(value="")
        self.dec_sexa = StringVar(value="")
        self._field(target, 2, "RA (hours)", self.ra_hours)
        ttk.Label(target, textvariable=self.ra_sexa, style="Card.TLabel", width=17).grid(row=2, column=2, sticky=W,
                                                                                          padx=(8, 0))
        self._field(target, 3, "Dec (degrees)", self.dec_deg)
        ttk.Label(target, textvariable=self.dec_sexa, style="Card.TLabel", width=17).grid(row=3, column=2, sticky=W,
                                                                                           padx=(8, 0))
        ttk.Label(target, text="J2000 frame, proper motion to today", style="Hint.TLabel").grid(
            row=4, column=1, columnspan=2, sticky=W)
        target.columnconfigure(1, weight=1)
        # Discovery has no target: only a field centre, and only when the lights' headers do not say where they point.
        self.field_group = self._group(left, "Field centre (only needed when the headers do not say)")
        fg = self.field_group
        ttk.Label(fg, text="These lights have no plate solution or pointing in their FITS headers. Type roughly where "
                           "the frame points (a few arcminutes off is fine) so the field can be solved.",
                  style="Hint.TLabel", wraplength=560, justify=LEFT).grid(row=0, column=0, columnspan=3, sticky=W)
        self._field(fg, 1, "RA (hours)", self.ra_hours)
        ttk.Label(fg, textvariable=self.ra_sexa, style="Card.TLabel", width=17).grid(row=1, column=2, sticky=W, padx=(8, 0))
        self._field(fg, 2, "Dec (degrees)", self.dec_deg)
        ttk.Label(fg, textvariable=self.dec_sexa, style="Card.TLabel", width=17).grid(row=2, column=2, sticky=W, padx=(8, 0))
        fg.columnconfigure(1, weight=1)
        self.ra_hours.trace_add("write", lambda *_: self._update_sexagesimal())
        self.dec_deg.trace_add("write", lambda *_: self._update_sexagesimal())
        self.star_id.trace_add("write", lambda *_: self._update_name_hint())

        # Planet (Transits mode only).
        self.planet_group = self._group(left, "Planet (Transits)")
        pg = self.planet_group
        ttk.Label(pg, text="Planet", style="Card.TLabel").grid(row=0, column=0, sticky=W, padx=(0, 10), pady=3)
        cell = ttk.Frame(pg, style="Card.TFrame")
        cell.grid(row=0, column=1, columnspan=3, sticky="ew", pady=3)
        planet_entry = ttk.Entry(cell, textvariable=self.planet_name, width=24)
        self._planet_entry = planet_entry
        planet_entry.pack(side=LEFT, fill=X, expand=True)
        # 2.2.14 (item 2): like the Star ID, the planet is looked up by itself on Enter or on leaving the box when
        # the name changed since the last lookup; the button repeats the lookup.
        for seq in ("<Return>", "<KP_Enter>", "<FocusOut>"):
            planet_entry.bind(seq, lambda _e: self._auto_planet_lookup())
        ttk.Button(cell, text="Look up again", command=self.lookup_planet).pack(side=LEFT, padx=(6, 0))
        ttk.Button(cell, text="Tonight's transits…", command=self._open_finder).pack(side=LEFT, padx=(6, 0))
        for i, (label, var, hint, label2, var2, hint2) in enumerate((
                ("Period (d)", self.pl_period, "", "T0 (BJD_TDB)", self.pl_t0, ""),
                ("Duration T14 (h)", self.pl_t14, "", "Depth (%)", self.pl_depth, ""),
                ("Rp/R*", self.pl_k, "", "a/R*", self.pl_a_rs, ""),
                ("Impact parameter b", self.pl_b, "", "Star Teff (K)", self.pl_teff, "for limb darkening"))):
            r = i + 1
            ttk.Label(pg, text=label, style="Card.TLabel").grid(row=r, column=0, sticky=W, padx=(0, 10), pady=3)
            ttk.Entry(pg, textvariable=var, width=16).grid(row=r, column=1, sticky="ew", pady=3)
            ttk.Label(pg, text=label2, style="Card.TLabel").grid(row=r, column=2, sticky=W, padx=(14, 10), pady=3)
            ttk.Entry(pg, textvariable=var2, width=16).grid(row=r, column=3, sticky="ew", pady=3)
        ttk.Label(pg, textvariable=self.planet_source, style="Hint.TLabel", wraplength=560, justify=LEFT).grid(
            row=5, column=0, columnspan=4, sticky=W, pady=(4, 0))
        self.report_note = StringVar(value="")
        ttk.Label(pg, textvariable=self.report_note, style="Warn.TLabel", wraplength=560, justify=LEFT).grid(
            row=6, column=0, columnspan=4, sticky=W, pady=(2, 0))
        pg.columnconfigure(1, weight=1)
        pg.columnconfigure(3, weight=1)
        self.planet_group.pack(fill=X, pady=(10, 0))


        # Report
        report = self._group(right, "AAVSO report")
        report.pack(fill=X)
        self._field(report, 0, "Observer code", self.observer, width=14)
        self._field(report, 1, "Chart ID", self.chart, width=14, hint="na for non-AAVSO comps")
        report.columnconfigure(1, weight=1)

        # 2.2.9: Site and Optics are two boxes.
        site = self._group(right, "Site")
        site.pack(fill=X, pady=(10, 0))
        self._field(site, 0, "Latitude (°, north +)", self.lat, width=12)
        self._field(site, 1, "Longitude (°, east +)", self.lon, width=12, hint="west is negative")
        self._field(site, 2, "Elevation (m)", self.elevation, width=12, hint="for exoplanet reports and scintillation")
        site.columnconfigure(1, weight=1)
        optics = self._group(right, "Optics")
        optics.pack(fill=X, pady=(10, 0))
        # 2.2.16 (item 5): the telescope's name (row 0; the header's TELESCOP is often the mount).
        self._field(optics, 0, "Telescope", self.telescope_name, width=12, hint="blank = from the header")
        self._field(optics, 1, "Telescope aperture (mm)", self.aperture_mm, width=12, hint="for the scintillation error")
        self._field(optics, 2, "Focal length (mm)", self.focal_mm, width=12)
        self._field(optics, 3, "Pixel size (µm)", self.pixel_um, width=12)
        ttk.Label(optics, text="Binning", style="Card.TLabel").grid(row=4, column=0, sticky=W, padx=(0, 10), pady=3)
        ttk.Combobox(optics, textvariable=self.binning, values=(1, 2, 4), width=6, state="readonly").grid(
            row=4, column=1, sticky=W, pady=3)
        ttk.Label(optics, text="same-color pixels only; colors stay separate", style="Hint.TLabel").grid(
            row=4, column=2, sticky=W, padx=(8, 0))
        ttk.Checkbutton(optics, text="Include scintillation (atmospheric twinkling) in the error bars",
                        variable=self.use_scint).grid(row=5, column=0, columnspan=3, sticky=W, pady=(2, 3))
        ttk.Label(optics, textvariable=self.aperture_hint, style="Hint.TLabel", foreground="#a32020").grid(
            row=6, column=0, columnspan=3, sticky=W)
        # 2.2.18 (item 8): mount type; the lights' headers can override it for their night (shown in the hint).
        ttk.Label(optics, text="Mount", style="Card.TLabel").grid(row=7, column=0, sticky=W, padx=(0, 10), pady=3)
        # Spans the entry and hint columns, so its width never pins the entry column (item 23's minimum sizes).
        ttk.Combobox(optics, textvariable=self.mount_choice, values=tuple(core.MOUNT_LABELS.values()),
                     width=max(len(v) for v in core.MOUNT_LABELS.values()) + 2, state="readonly").grid(
            row=7, column=1, columnspan=2, sticky=W, pady=3)
        ttk.Label(optics, text="Alt-az: no meridian flips; the field rotation is followed.", style="Hint.TLabel",
                  wraplength=text_wrap_px(optics, 55), justify=LEFT).grid(row=8, column=0, columnspan=3, sticky=W)
        ttk.Label(optics, textvariable=self.mount_hint, style="Hint.TLabel", wraplength=text_wrap_px(optics, 55),
                  justify=LEFT).grid(row=9, column=0, columnspan=3, sticky=W)
        optics.columnconfigure(1, weight=1)
        return page

    # ---- planet (Transits) ---------------------------------------------------------------
    def _auto_planet_lookup(self):
        """2.2.14 (item 2): look the planet up when its name changed since the last lookup (not on every keystroke).
        Names set by the planet chooser or Tonight's transits were looked up already, so they count as done."""
        name = self.planet_name.get().strip()
        if not name or name == getattr(self, "_last_planet_lookup", None):
            return
        self.lookup_planet(quiet=True)

    def _planet_box_has_focus(self) -> bool:
        try:
            entry = getattr(self, "_planet_entry", None)
            return entry is not None and self.focus_get() is entry
        except Exception:
            return False

    def lookup_planet(self, quiet: bool = False):
        name = self.planet_name.get().strip()
        if not name:
            if not quiet:
                messagebox.showinfo(APP_TITLE, "Type the planet's name first, under any of its names (for example "
                                               "TrES-3 b, KOI-217 b or Kepler-71 b, TOI-2046 b, EPIC 220504338 b).")
            return
        self._last_planet_lookup = name   # 2.2.14 (item 2)
        self.planet_source.set(f"Looking up {name}…")

        def work():
            try:
                self.call_ui(self._planet_lookup_done, tcore.lookup_planet(name), None, name, quiet)
            except Exception as exc:
                self.call_ui(self._planet_lookup_done, None, exc, name, quiet)

        self._lookup_worker = threading.Thread(target=work, daemon=True)
        self._lookup_worker.start()

    def _planet_lookup_done(self, p, error, name: str = "", quiet: bool = False):
        if name and name != self.planet_name.get().strip():
            # 2.2.14 (item 2): the name changed while this lookup ran: look up the one that is there now instead, but
            # not a half-typed name while the box still has the focus (review): Enter or leaving the box does that.
            if not self._planet_box_has_focus():
                self._auto_planet_lookup()
            return
        if isinstance(error, tcore.PlanetChoice):
            # 2.2.6: a star with several planets: ask which one, then look that one up.
            self.planet_source.set(f"{error.system}: choose a planet.")
            self._choose_planet(error.system, error.planets, getattr(error, "transiting", set()))
            return
        if error is not None:
            if quiet:
                # 2.2.14 (item 2): an automatic lookup fails quietly, on the line under the box (no error box). The
                # name still counts as looked up, so leaving the box again does not ask again; Look up again does.
                self.planet_source.set(f"Could not look up {name or 'the planet'} ({error}). Check the spelling and "
                                       "press Look up again, or type the values.")
                return
            self.planet_source.set("Lookup failed.")
            messagebox.showerror(APP_TITLE, f"Planet lookup failed:\n{error}")
            return
        self.planet_info = dict(p)
        self._planet_to_fields(p)
        self._last_planet_lookup = self.planet_name.get().strip()   # 2.2.14 (item 2): the resolved name counts too
        filled = []
        # 2.2.7: a planet whose host is a different star from the Star ID (a target left from an earlier planet)
        # replaces the Star ID and RA/Dec, and says so; the same star is left as typed.
        old_star = self.star_id.get().strip()
        different = False
        if p.get("ra") is not None and self.ra_hours.get().strip() and self.dec_deg.get().strip():
            try:
                sep = core.sky_separation_arcsec(float(self.ra_hours.get()) * 15.0, float(self.dec_deg.get()),
                                                 p["ra"], p["dec"])
                different = sep > 60.0
            except (TypeError, ValueError):
                different = True
        if different and old_star and p.get("host") and not core.same_object_name(old_star, p["host"]):
            # 2.2.16 (item 11): a planet of another star: the old field's comps, check and watch stars go too.
            self.clear_target(quiet=True, why=f"the planet {p.get('name') or name} orbits {p['host']}, not {old_star}")
        if p.get("ra") is not None and (not self.ra_hours.get().strip() or different):
            self.ra_hours.set(f"{p['ra'] / 15.0:.6f}")
            self.dec_deg.set(f"{p['dec']:.5f}")
            self._radec_filled = None   # 2.2.15
            filled.append("RA/Dec")
        if p.get("host") and (not old_star or different):
            self.star_id.set(p["host"])
            self._last_lookup_name = p["host"]
            filled.append("Star ID" + (f" (was {old_star}, a different star)" if old_star and different else ""))
        mags = ", ".join(f"{b} {p[k]:.2f}" for b, k in (("V", "vmag"), ("TESS", "tmag")) if p.get(k))
        # 2.2.6: say what the typed name resolved to, and the planet's other names.
        typed = (p.get("typed") or "").strip()
        aka = ""
        if typed and typed.lower().replace(" ", "") != str(p["name"]).lower().replace(" ", ""):
            aka = f" (you typed {typed})"
        others = [a for a in (p.get("aliases") or []) if not a.startswith(("Gaia ", "WISE ", "2MASS "))][:4]
        if others:
            aka += "; also known as " + ", ".join(others)
        self.planet_source.set(
            f"{p['name']}{aka} from {p.get('source', '')}" + (f" ({p['reference']})" if p.get("reference") else "")
            + (f"; star {mags}" if mags else "") + (f"; filled {' and '.join(filled)}" if filled else "")
            + (". a/R* derived from the duration." if p.get("a_rs_derived") else "."))
        self.status.set(f"{p['name']}: P {p['period']:.6f} d.")
        if hasattr(self, "transit_page"):
            self.transit_page.refresh()
        if "RA/Dec" in filled:
            self._try_auto_target("lookup")   # 2.2.15 (review): the image may be solved already

    def _planets_for_star(self, star: str, found: dict):
        """2.2.7 (Transits): a Star ID lookup also finds the star's planets: one transiting planet is looked up and
        filled in; several open the chooser (transiting first). A planet already typed for this star is kept."""
        star = (star or "").strip()
        if not star:
            return
        current = self.planet_name.get().strip()
        info = getattr(self, "planet_info", None) or {}
        if current and info.get("host") and info.get("ra") is not None and found.get("ra_hours") is not None:
            try:
                if core.sky_separation_arcsec(info["ra"], info["dec"], found["ra_hours"] * 15.0,
                                              found["dec_deg"]) < 60.0:
                    return   # the planet box already holds a planet of this star
            except (TypeError, ValueError, KeyError):
                pass

        def work():
            try:
                res = tcore.star_planets(star)
            except Exception:
                res = None
            self.call_ui(self._planets_for_star_done, star, res)

        threading.Thread(target=work, daemon=True).start()

    def _planets_for_star_done(self, star: str, res):
        if star != self.star_id.get().strip():
            return
        if not res:
            self.planet_source.set(f"No known planets for {star} in the NASA Exoplanet Archive (type the planet's name "
                                   "if it has another).")
            return
        tr = [n for n in res["planets"] if n in res["transiting"]]
        if len(tr) == 1 or (len(res["planets"]) == 1):
            self.planet_name.set(tr[0] if tr else res["planets"][0])
            self.lookup_planet()
        else:
            self.planet_source.set(f"{res['system']}: choose a planet.")
            self._choose_planet(res["system"], res["planets"], res["transiting"])

    def _choose_planet(self, system: str, planets: list, transiting=None):
        """2.2.6: one button per planet of a multi-planet system; the choice is looked up. 2.2.7: planets that
        transit first; the others are marked, since a transit fit needs a transiting planet."""
        transiting = set(transiting or [])
        win = Toplevel(self)
        win.title(f"{APP_TITLE} — choose a planet")
        win.transient(self)
        frame = ttk.Frame(win, style="Card.TFrame", padding=14)
        frame.pack(fill=BOTH, expand=True)
        ttk.Label(frame, text=f"{system} has {len(planets)} known planets. Which one did you observe?",
                  style="Card.TLabel").pack(anchor=W, pady=(0, 8))

        def pick(name):
            win.destroy()
            self.planet_name.set(name)
            self.lookup_planet()
        def cancel():
            win.destroy()
            self.planet_source.set("Lookup cancelled.")
        for name in planets:
            text = name if (not transiting or name in transiting) else f"{name}   (does not transit)"
            ttk.Button(frame, text=text, command=lambda n=name: pick(n)).pack(fill=X, pady=2)
        ttk.Button(frame, text="Cancel", command=cancel).pack(anchor=E, pady=(8, 0))
        win.protocol("WM_DELETE_WINDOW", cancel)
        win.after_idle(lambda: fit_to_content(win))
        try:
            win.grab_set()
        except Exception:
            pass

    def _planet_to_fields(self, p: dict):
        def put(var, value, fmt):
            var.set(fmt.format(value) if isinstance(value, (int, float)) and math.isfinite(value) else "")
        self.planet_name.set(p.get("name") or self.planet_name.get())
        put(self.pl_period, p.get("period"), "{:.8f}")
        put(self.pl_t0, p.get("t0"), "{:.6f}")
        put(self.pl_t14, (p.get("t14") or float("nan")) * 24, "{:.4f}")
        put(self.pl_depth, (p.get("depth") or float("nan")) * 100, "{:.4f}")
        put(self.pl_k, p.get("k"), "{:.5f}")
        put(self.pl_a_rs, p.get("a_rs"), "{:.3f}")
        put(self.pl_b, p.get("b"), "{:.3f}")
        put(self.pl_teff, p.get("teff"), "{:.0f}")

    def _open_finder(self):
        import transit_ui
        transit_ui.FinderWindow(self)

    def planet_from_fields(self) -> dict | None:
        """The planet as typed on the Input page; uncertainties come from the last lookup when a value is unchanged."""
        def num(var):
            try:
                v = float(var.get())
                return v if math.isfinite(v) else None
            except (TypeError, ValueError):
                return None
        period = num(self.pl_period)
        if not period:
            return None
        info = self.planet_info or {}
        p = {"name": self.planet_name.get().strip(), "host": info.get("host") or self.star_id.get().strip(),
             "period": period, "t0": num(self.pl_t0), "k": num(self.pl_k), "a_rs": num(self.pl_a_rs),
             "b": num(self.pl_b), "teff": num(self.pl_teff), "source": info.get("source") or "Input page",
             "reference": info.get("reference", ""), "vmag": info.get("vmag"), "tmag": info.get("tmag"),
             "ra": info.get("ra"), "dec": info.get("dec")}
        t14 = num(self.pl_t14)
        p["t14"] = t14 / 24.0 if t14 else None
        depth = num(self.pl_depth)
        p["depth"] = depth / 100.0 if depth else None
        from_lookup = [v for v in (info.get("depth"), info.get("depth_listed")) if v]
        if p["depth"] and not any(abs(v - p["depth"]) <= 1e-4 * max(v, 1e-6) for v in from_lookup):
            p["depth_typed"] = True   # 2.2.13: not the lookup's value; the depth note must not call it the Archive's
        if info.get("a_rs_derived") and p["a_rs"] is not None and info.get("a_rs") is not None \
                and abs(info["a_rs"] - p["a_rs"]) <= 1e-3 * max(abs(info["a_rs"]), 1.0):
            p["a_rs_derived"] = True
        for key, err in (("period", "period_err"), ("t0", "t0_err"), ("a_rs", "a_rs_err"), ("b", "b_err"),
                         ("k", "k_err")):
            old = info.get(key)
            if old is not None and p.get(key) is not None and abs(old - p[key]) <= 1e-6 * max(abs(old), 1.0):
                p[err] = info.get(err)
        try:
            return tcore.complete_planet(p)
        except Exception:
            return p

    def target_radec_deg(self, planet: dict | None = None) -> tuple[float, float]:
        """Target RA/Dec in degrees: the Input page's, else the planet lookup's."""
        try:
            return float(self.ra_hours.get()) * 15.0, float(self.dec_deg.get())
        except (TypeError, ValueError):
            pass
        p = planet or self.planet_info or {}
        if p.get("ra") is not None and p.get("dec") is not None:
            return float(p["ra"]), float(p["dec"])
        raise ValueError("enter the target's RA and Dec on the Input page (type the Star ID and press Enter)")

    def _layout_input_for_mode(self):
        if not hasattr(self, "planet_group"):
            return
        # Forget and re-pack in a fixed order: Target (or Field centre in Discovery), then Planet in Transits.
        for group in (self.target_group, self.field_group, self.planet_group):
            group.pack_forget()
        if self.mode_key == "discovery":
            if self.field_centre_needed:
                self.field_group.pack(fill=X)
        else:
            self.target_group.pack(fill=X)
        if self.mode_key == "transits":
            self.planet_group.pack(fill=X, pady=(10, 0))

    def _update_sexagesimal(self):
        try:
            self.ra_sexa.set(core.sexagesimal_ra(float(self.ra_hours.get())))
        except (TypeError, ValueError):
            self.ra_sexa.set("")
        try:
            dec = float(self.dec_deg.get())
            self.dec_sexa.set(core.sexagesimal_dec(dec) if -90 <= dec <= 90 else "")
        except (TypeError, ValueError):
            self.dec_sexa.set("")

    def _update_name_hint(self):
        good = core.suggest_star_name(self.star_id.get())
        if good:
            self.name_hint.set(f"AAVSO spells this \u201c{good}\u201d")
            self.name_row.grid()
        else:
            self.name_hint.set("")
            self.name_row.grid_remove()

    def _use_suggested_name(self):
        good = core.suggest_star_name(self.star_id.get())
        if good:
            self.star_id.set(good)
            self.status.set(f"Star ID set to {good}.")
            self._auto_lookup()

    def clear_target(self, quiet: bool = False, why: str = ""):
        """Empty the target (Star ID, RA, Dec) and the marked stars and comp/check fields that belong to it.
        2.2.16 (item 11): the whole old field goes: every comp C1-C10 and the check (names, magnitudes, markers),
        suggested comps, spare marks, the watch stars added this session, and the slots' hint lines. why: logged in one
        line with what was cleared (a new series, new lights, a new target)."""
        old = self.star_id.get().strip()
        named = [getattr(self, f"{r}_name").get().strip() for r in COMP_ROLES]
        n_comps = sum(1 for r, n in zip(COMP_ROLES, named) if n or getattr(self, f"{r}_xy") is not None)
        had_check = bool(self.check_name.get().strip() or self.check_xy is not None)
        series_watch = set(getattr(self, "_series_watch_names", ()) or ()) if self.observations else set()
        session_watch = [w.get("name") for w in self.watch_list if w.get("name") not in series_watch]
        self.star_id.set("")
        self.ra_hours.set("")
        self.dec_deg.set("")
        self._radec_filled = None   # 2.2.15
        self.target_xy = None
        for role in COMP_ROLES + ("check",):
            self.clear_mark(role, redraw=False)
        self.star_bands = {}
        self.suggested_comps = []
        # 2.2.16 (item 11): the rest of the old field (15 Oct: a stale "✓ Comp 5 … Marker at (747, 169)" showed with
        # no marker, and TOI-2046's comp names went into HD 219134's series).
        self.spare_marks = []
        if not self.observations:
            self.spare_catalog = {}   # an open series keeps its own spares (they are saved with it)
        if session_watch:
            self.watch_list = [w for w in self.watch_list if w.get("name") not in session_watch]
            for name in session_watch:
                self.watch_xy.pop(name, None)
            try:
                self._refresh_watch_box()
            except Exception:
                pass
        if hasattr(self, "_slot_notes"):
            self._slot_notes = {}
        self._slot_typed = {}   # 2.2.16 (item 8, review): typed names belong to the image they were typed on
        # review: AUIDs added this session belong to the old field (an open series keeps its own).
        series_auids = set(getattr(self, "_series_auid_keys", ()) or ()) if self.observations else set()
        for key in [k for k in self.seq_auids if k not in series_auids]:
            self.seq_auids.pop(key, None)
        if why:
            what = ([f"target {old}"] if old else []) + ([f"{n_comps} comp{'s' if n_comps != 1 else ''}"] if n_comps
                                                         else []) \
                + (["check star"] if had_check else []) + (["suggested comps and spare marks"]) \
                + ([f"{len(session_watch)} watch star{'s' if len(session_watch) != 1 else ''} added this session"]
                   if session_watch else [])
            self.log(f"Cleared the old field ({', '.join(what)}): {why}.")
        self.catalog_period = None
        self.target_var_type = ""   # 2.2.14 (item 19)
        self.catalog_epoch = None   # 2.2.14 (item 13)
        self.catalog_var_type = ""
        self.extremum_csv = self.extremum_csv_star = ""   # review: a new target starts its own times CSV
        self.chart.set("")
        if hasattr(self, "pick_label"):
            self._update_pick_label()
            self.update_star_panel()
        self.refresh_preview()
        if not quiet:
            self.status.set("Target, comps, and check cleared. Type the new Star ID and press Enter to look it up.")

    def _other_target_why(self, star, ra_hours=None, dec_deg=None) -> str:
        """2.2.16 (item 11): why a loaded series' target is another star than the Input page's, or "" (see
        _other_target)."""
        return self._other_target(star, ra_hours, dec_deg)[0]

    def _other_target(self, star, ra_hours=None, dec_deg=None):
        """2.2.16 (item 11): (why, sure): why a loaded series' target is another star than the Input page's ("" for
        the same star or nothing to compare), and whether that is certain. With RA/Dec on both sides the positions
        decide (over 1° apart): sure. Without them (no header pointing, no lookup) the Star IDs are compared, ignoring
        spacing and case: not sure (HIP 114622 is HD 219134), so the caller asks. No Star ID on the Input page but
        comps or the check named or marked: those belong to no known target, so they are another field's: sure."""
        cur = self.star_id.get().strip()
        new = str(star or "").strip()
        if not new:
            return "", False
        if not cur:
            n = sum(1 for r in list(COMP_ROLES) + ["check"]
                    if getattr(self, f"{r}_name").get().strip() or getattr(self, f"{r}_xy") is not None)
            if n or self.target_xy is not None:
                return (f"{new}, and the Input page had no Star ID for the {n} comp/check star"
                        f"{'s' if n != 1 else ''} marked or named"), True
            return "", False
        try:
            a = (float(self.ra_hours.get()) * 15.0, float(self.dec_deg.get()))
            b = (float(ra_hours) * 15.0, float(dec_deg))
        except (TypeError, ValueError):
            a = b = None
        if a is not None and b is not None and all(map(math.isfinite, a + b)):
            sep = core.sky_separation_arcsec(a[0], a[1], b[0], b[1]) / 3600.0
            return (f"{new}, {sep:.0f}° from {cur}", True) if sep > 1.0 else ("", False)
        return ("", False) if core.same_object_name(cur, new) else (f"{new}, not {cur}", False)

    def _check_target_field(self, header: dict):
        """Lights pointing far from the Input page's target (a target left over from another star): offer to clear
        it, and suggest the header's OBJECT name (2.2.1). Same star on a new night: no question."""
        if self.mode_key == "discovery" or not header:
            return
        try:
            t_ra, t_dec = float(self.ra_hours.get()) * 15.0, float(self.dec_deg.get())
        except (TypeError, ValueError):
            return
        wcs = core.header_wcs(header)
        try:
            shape = (int(header.get("NAXIS2")), int(header.get("NAXIS1")))
        except (TypeError, ValueError):
            shape = None
        if wcs is not None and shape:
            c_ra, c_dec = core.wcs_center(wcs, shape)
            half_diag = math.hypot(*shape) / 2.0 * wcs["scale_arcsec"] / 3600.0
        else:
            pointing = core.header_pointing(header)
            if pointing is None:
                return
            c_ra, c_dec = pointing
            try:
                half_diag = math.hypot(*shape) / 2.0 * float(self.pixel_um.get()) / float(self.focal_mm.get()) \
                    * 206.265 / 3600.0 if shape else 0.5
            except Exception:
                half_diag = 0.5
        sep = core.sky_separation_arcsec(t_ra, t_dec, c_ra, c_dec) / 3600.0
        if sep <= max(1.0, 2.0 * half_diag):
            return
        key = (round(t_ra, 3), round(t_dec, 3), round(c_ra, 2), round(c_dec, 2))
        if key == getattr(self, "_field_question_declined", None):
            return
        obj = str(header.get("OBJECT") or "").strip()
        import re
        obj = re.sub(r"\s+[b-h]$", "", obj)      # "HD 219134 b" (a planet) -> its star, "HD 219134"
        generic = obj.lower() in ("", "fov", "light", "object", "target", "unknown", "none")
        name = self.star_id.get().strip() or "the target"
        ask = (f"These lights point at {core.sexagesimal_ra(c_ra / 15.0)}  {core.sexagesimal_dec(c_dec)}"
               + ("" if generic else f" (FITS OBJECT: {obj})")
               + f", {sep:.0f}° from the Input page's target, {name} at {core.sexagesimal_ra(t_ra / 15.0)}  "
               f"{core.sexagesimal_dec(t_dec)}.\n\nClear the old target, comps, and check star"
               + ("" if generic else f", and look up {obj}") + "?")
        if not messagebox.askyesno(APP_TITLE, ask):
            self._field_question_declined = key
            self.log(f"Kept {name} as the target although the lights point {sep:.0f}° away.")
            return
        # 2.2.16 (item 11): one log line says what was cleared and why.
        self.clear_target(quiet=True, why=f"these lights are a different field, {sep:.0f}° from {name}"
                                          + ("" if generic else f"; looking up {obj}"))
        if not generic:
            self.star_id.set(obj)
            self.lookup_star()

    def _check_lights_targets(self, lights: list, first_header: dict):
        """2.2.13 (John: "there are more observers like me out there"): when the lights are scanned,
        (1) one folder pointing at two places (12-13 Oct: HD 219134 frames saved under HAT-P-23 b) -> say so and offer
        to use only the frames near the target; (2) a FITS OBJECT naming another object than the Input target ->
        warn, unless it is another spelling or (looked up) another name of the same star. Frames with no pointing and
        no OBJECT: nothing to check, silent (mono cameras, no plate solution, no header pointing all fine)."""
        if self.mode_key == "discovery" or not lights:
            return None
        try:
            target = (float(self.ra_hours.get()) * 15.0, float(self.dec_deg.get()))
        except (TypeError, ValueError):
            target = None
        name = self.star_id.get().strip()
        first = lights[0]
        lights = [p for p in lights if p not in self.rejected] or lights   # frames already set aside stay aside
        # Review 2.2.13: reading every header on a network drive froze the window; a sample of about 40 frames
        # finds a second field, and only then is every header read.
        cache = getattr(self, "_header_cache", None)
        if cache is None or cache.get("_folder") != self.light_dir.get():
            cache = self._header_cache = {"_folder": self.light_dir.get()}

        def header_of(path):
            if path == first:
                return first_header
            try:
                key = (path, os.path.getmtime(path))
            except OSError:
                key = None   # cannot tell whether it changed: read it again
            if key is None:
                try:
                    return core.read_header(path)
                except Exception:
                    return {}
            if key not in cache:
                try:
                    cache[key] = core.read_header(path)
                except Exception:
                    cache[key] = {}
            return cache[key]

        step = max(1, len(lights) // 40)
        sample = list(range(0, len(lights), step))
        if sample[-1] != len(lights) - 1:
            sample.append(len(lights) - 1)
        if len(core.pointing_groups([core.frame_pointing(header_of(lights[i])) for i in sample])) > 1:
            idx = list(range(len(lights)))
        else:
            idx = sample
        lights = [lights[i] for i in idx]
        headers = [header_of(p) for p in lights]
        pointings = [core.frame_pointing(h) for h in headers]
        groups = core.pointing_groups(pointings)
        keep = None
        if len(groups) > 1:
            def centre(g):
                return pointings[g[0]]
            if target is not None:
                near = min(groups, key=lambda g: core.sky_separation_arcsec(target[0], target[1], *centre(g)))
            else:
                near = groups[0]
            others = [i for g in groups if g is not near for i in g]
            key = (self.light_dir.get(), tuple(sorted((round(centre(g)[0], 1), round(centre(g)[1], 1))
                                                      for g in groups)))
            if key != getattr(self, "_two_fields_asked", None):
                self._two_fields_asked = key
                def describe(g):
                    objs = sorted({str(headers[i].get("OBJECT") or "").strip() for i in g} - {""})
                    c = centre(g)
                    return (f"{len(g)} frames at {core.sexagesimal_ra(c[0] / 15.0)} {core.sexagesimal_dec(c[1])}"
                            + (f" (OBJECT {', '.join(objs[:2])})" if objs else ""))
                where = "near the Input target " + (name or "") if target is not None else "in the larger group"
                text = ("This Lights folder holds frames pointing at more than one place:\n  • "
                        + "\n  • ".join(describe(g) for g in groups)
                        + f"\n\nUse only the {len(near)} frames {where.strip()}? (The others are set aside as "
                          "rejected frames; Blink can bring any of them back.)")
                self.log("Lights: " + "; ".join(describe(g) for g in groups) + ".")
                if messagebox.askyesno(APP_TITLE, text):
                    for i in others:
                        self.rejected.add(lights[i])
                    self.log(f"Using the {len(near)} frames {where.strip()}; {len(others)} frames set aside.")
                    keep = near
                    try:
                        self._counts["Lights"].set(f"{len(near)} files ({len(others)} set aside: another field)")
                    except Exception:
                        pass
        # (2) OBJECT against the target name.
        use = keep if keep is not None else (groups[0] if groups else list(range(len(headers))))
        kept_header = headers[use[0]] if use else None
        if not name:
            return kept_header
        from collections import Counter
        counts = Counter(str(headers[i].get("OBJECT") or "").strip() for i in use)
        counts.pop("", None)
        import re
        # One check (one dialog at most) for the most common OBJECT that is not the target's name.
        objects = [o for o, _n in counts.most_common() if o.lower() not in core.GENERIC_OBJECTS
                   and not core.same_object_name(o, name)]
        if objects:
            obj = objects[0]
            key = (obj.lower(), name.lower())
            if key not in getattr(self, "_object_checked", set()):
                self._object_checked = getattr(self, "_object_checked", set()) | {key}
                pointing = pointings[use[0]] if use and pointings[use[0]] is not None else None

                def work(obj=obj, pointing=pointing):
                    try:
                        found = core.lookup_target(re.sub(r"\s+[b-h]$", "", obj))
                        where = (found["ra_hours"] * 15.0, found["dec_deg"])
                    except Exception:
                        where = None
                    self.call_ui(self._object_verdict, obj, name, target, where, pointing)

                threading.Thread(target=work, daemon=True).start()
        return kept_header

    def _object_verdict(self, obj, name, target, obj_where, pointing):
        """The OBJECT check's answer (after a lookup): silent for another name of the same star."""
        current = self.star_id.get().strip()
        if current != name or core.same_object_name(obj, current):
            return   # the target changed meanwhile (e.g. replaced by the field check's question)
        if pointing is not None and target is not None and \
                core.sky_separation_arcsec(target[0], target[1], *pointing) / 3600.0 >= 1.0:
            return   # the frames point elsewhere: the field check has already said so
        if target is not None and obj_where is not None and \
                core.sky_separation_arcsec(target[0], target[1], obj_where[0], obj_where[1]) < 120.0:
            return   # another name of the target (HIP / HD / TYC …)
        on_target = (pointing is not None and target is not None
                     and core.sky_separation_arcsec(target[0], target[1], *pointing) / 3600.0 < 1.0)
        if on_target:
            text = (f"The lights' FITS OBJECT says '{obj}', but they point at the Input target {name}. The OBJECT "
                    "keyword looks mislabelled (the capture program's plan name); the frames themselves are fine.")
            self.log("Lights: " + text)
            self.status.set(text)
        else:
            text = (f"The lights' FITS OBJECT says '{obj}', not the Input target {name}"
                    + ("" if obj_where is None else f" ('{obj}' is {core.sky_separation_arcsec(target[0], target[1], *obj_where) / 3600.0:.1f}° away)"
                       if target is not None else "")
                    + ". Check that these are the right lights, or update the target on the Input page.")
            self.log("Lights: " + text)
            # 2.2.16 (item 11): without pointing, OBJECT is what says the field changed: offer the same clearing as a
            # field change (target, every comp and the check, suggested comps, spares, session watch stars).
            import re
            star = re.sub(r"\s+[b-h]$", "", obj)
            if self.busy:   # review: never offer to wipe the field during a run: a warning, as before 2.2.16
                messagebox.showwarning(APP_TITLE, text)
            elif messagebox.askyesno(APP_TITLE, text + f"\n\nClear the old target, comps, and check star, and look "
                                                       f"up {star}?", default="no"):
                self.clear_target(quiet=True, why=f"the lights' FITS OBJECT is {obj}, not {name}; looking up {star}")
                self.star_id.set(star)
                self.lookup_star()

    def _check_lights_pointing(self, header: dict):
        """Discovery shows the Field centre box only when the lights cannot say where they point."""
        needed = core.header_wcs(header or {}) is None and core.header_pointing(header or {}) is None
        if needed != self.field_centre_needed:
            self.field_centre_needed = needed
            self._layout_input_for_mode()

    def _auto_lookup(self):
        """2.2.5: look the target up when its name has changed since the last lookup (not on every keystroke)."""
        name = self.star_id.get().strip()
        if not name or name == getattr(self, "_last_lookup_name", None):
            return
        self.lookup_star(quiet=True)

    def lookup_star(self, quiet: bool = False):
        name = self.star_id.get().strip()
        if not name:
            if not quiet:
                messagebox.showinfo(APP_TITLE, "Enter a Star ID or designation first.")
            return
        self._last_lookup_name = name
        self.status.set(f"Looking up {name} in SIMBAD and VSX…")

        def work():
            try:
                found = core.lookup_target(name)
                self.call_ui(self._lookup_done, found, None, name, quiet)
            except Exception as exc:
                self.call_ui(self._lookup_done, None, exc, name, quiet)

        # A separate thread from the blink and photometry workers, so a lookup never waits on them (2.2.5).
        self._lookup_worker = threading.Thread(target=work, daemon=True)
        self._lookup_worker.start()

    def _lookup_done(self, found, error, name: str = "", quiet: bool = False):
        if name and name != self.star_id.get().strip():
            # The name changed while this lookup ran (typed again, or "Use it" on the spelling hint): look up the
            # name that is there now instead of reporting the old one.
            self._auto_lookup()
            return
        if error is not None:
            if quiet:
                # 2.2.5: an automatic lookup that fails says so quietly; RA/Dec can come from the plate solution.
                self.status.set(f"Could not look up {name or 'the target'} ({error}). Type RA/Dec, or let the plate "
                                "solution fill them, or press Look up again.")
                self._last_lookup_name = None
                return
            messagebox.showerror(APP_TITLE, str(error))
            return
        self.ra_hours.set(f"{found['ra_hours']:.6f}")
        self.dec_deg.set(f"{found['dec_deg']:.5f}")
        self._radec_filled = None   # 2.2.15: the target's own RA/Dec now
        self.catalog_period = found.get("period_days")
        self.target_var_type = str(found.get("var_type") or "")   # 2.2.14 (item 19)
        self.catalog_epoch = found.get("epoch_jd")   # 2.2.14 (item 13): for O-C on the Output page
        self.catalog_var_type = found.get("var_type") or ""
        extra = ""
        if self.catalog_period:
            extra = f"   VSX period {self.catalog_period:.6f} d"
            if found.get("var_type"):
                extra += f" ({found['var_type']})"
        if self.mode_key == "transits":
            self._planets_for_star(name or found.get("name", ""), found)
        elif self.mode_key == "variables" and self.star_id.get().strip():
            self._prefetch_target_planets(self.star_id.get().strip())   # 2.2.18 (item 20): ready before Run
        note = f" ({found['note']})" if found.get("note") else ""
        self.status.set(
            f"{found['name']} from {found.get('source', 'catalog')}{note}: RA {found['ra_hours']:.5f} h, "
            f"Dec {found['dec_deg']:.4f}° at the current epoch{extra}"
        )
        # 2.2.15 (GitHub #3): the image was solved before the lookup answered: put the Target marker on it now.
        self._try_auto_target("lookup")

    def scan_folders(self):
        if getattr(self, "photo_popout", None) is not None:
            self.photo_bring_back()   # 2.2.17 (item 2): new lights: the image (and its buttons) back on the page
        for key, var in (
            ("Bias", self.bias_dir),
            ("Dark", self.dark_dir),
            ("Flat", self.flat_dir),
            ("Lights", self.light_dir),
        ):
            n = len(core.calibration_source(var.get())[0]) if key != "Lights" else len(core.list_fits(var.get()))
            other = core.unreadable_files(var.get()) if not os.path.isfile(var.get().strip()) else []
            self._counts[key].set(self._count_text(key, var.get()) + (f" ({len(other)} unreadable)" if other else ""))
            if other and not n:
                self.log(f"The {key} folder holds {len(other)} file(s) SHOBS-P cannot read (for example "
                         f"{other[0]}). It reads FITS and XISF: save or export them in one of those.")
        lights, skipped, _note = core.split_lights(core.list_fits(self.light_dir.get()))
        if skipped:
            self._counts["Lights"].set(f"{len(lights)} files (+{len(skipped)} cal skipped)")
        if lights:
            try:
                _, header = core.read_fits(lights[0])
                # 2.2.18 (item 1): colour images already debayered (three channels): a channel is chosen, no debayer.
                rgb = core.is_rgb_header(header)
                if rgb:
                    self._setup_rgb_lights(header, len(lights))
                elif self._rgb_channel:
                    self._leave_rgb_lights()
                self._check_lights_pointing(header)
                self._read_mono_filter(header)
                # 2.2.13: frames pointing at two places first; the field check then uses a frame that is kept.
                kept = self._check_lights_targets(lights, header)
                self._check_target_field(kept if kept is not None else header)
                if not core.is_microobservatory(lights[0], header):
                    self._header_setup(header)   # 2.2.9: site and optics from the header (fills, asks, borrows)
                self._update_mount_hint(lights, log=True)   # 2.2.18 (item 8)
                pat = core.header_bayer(header)
                if rgb:
                    self.status.set(f"{len(lights)} lights: color images, "
                                    f"{core.RGB_CHANNEL_WORDS.get(self._rgb_channel, '')} channel measured.")
                    return
                if not pat and not core.is_microobservatory(lights[0], header):
                    was_mono = self.pattern.get() == core.MONO
                    self._detect_sensor()        # 2.2.11: mono or colour from a raw flat
                    if not was_mono and self.pattern.get() == core.MONO:
                        self._read_mono_filter(header)   # now that it is MONO, read the filter again
                if pat:
                    self.pattern.set(pat)
                    self.status.set(f"{len(lights)} lights. Bayer pattern from header: {pat}")
                    return
                if core.is_microobservatory(lights[0], header):
                    self._setup_microobservatory(header, len(lights))
                    return
            except Exception as exc:
                self.status.set(f"Could not read first light: {exc}")
                return
        self.status.set("Folders scanned.")

    def _pattern_changed(self):
        if (getattr(self, "_rgb_channel", None) and self.pattern.get() != core.MONO
                and not getattr(self, "_rgb_reverting", False)):
            # 2.2.18 (item 1): colour images are already debayered; a Bayer pattern would debayer them again.
            picked = self.pattern.get()
            self._pattern_before_rgb = picked   # review S5: the pattern you picked comes back after these lights
            self._rgb_reverting = True
            try:
                self.pattern.set(core.MONO)   # (real Tk does not run this trace again for a write made inside it)
            finally:
                self._rgb_reverting = False
            self.log(f"Caution: these lights are color images (three channels, already debayered); Bayer pattern "
                     f"{picked} would debayer them a second time, so it stays MONO. Choose the channel in the Filter "
                     "box instead.")
        mono = self.pattern.get() == core.MONO
        core.set_mono(mono)
        if mono and self.debayer_mode.get() != "luminance":
            self._debayer_before_mono = self.debayer_mode.get()
            self.debayer_mode.set("luminance")
        elif not mono and getattr(self, "_debayer_before_mono", None):
            # 2.2.11: back to a colour pattern: the channel chosen before MONO (green, usually) comes back.
            self.debayer_mode.set(self._debayer_before_mono)
            self._debayer_before_mono = None
        self._show_channel_or_filter(mono)
        if hasattr(self, "filter_hint"):
            self._update_filter_hint()

    def _show_channel_or_filter(self, mono: bool):
        """2.2.11: on MONO the Channel box and 'Apply channel' give way to a Filter box."""
        if not hasattr(self, "channel_box"):
            return
        try:
            self.channel_box.pack_forget()
            self.filter_box.pack_forget()
            self.apply_button.pack_forget()
            if mono:
                self.channel_label.configure(text="Filter")
                if not self.mono_filter_choice.get():
                    self.mono_filter_choice.set(core.mono_filter_label(self.mono_filter) or "Clear / L (CV)")
                self.filter_box.pack(side=LEFT, padx=8, before=self.filter_hint_label)
            else:
                self.channel_label.configure(text="Channel")
                self.channel_box.pack(side=LEFT, padx=8, before=self.filter_hint_label)
                self.apply_button.pack(side=LEFT, padx=8, before=self.filter_hint_label)
        except Exception:
            pass

    def _mono_filter_picked(self):
        """A monochrome camera's filter chosen by hand: used for the report code and the comps' band."""
        label = self.mono_filter_choice.get()
        pair = core.MONO_FILTER_CHOICES.get(label)
        if not pair:
            return
        if getattr(self, "_rgb_channel", None):
            self._rgb_channel_picked(pair)   # 2.2.18 (item 1): colour images: the box picks the channel
            return
        old_band = self._band()
        self.mono_filter = pair
        self._mono_filter_manual = True
        self._mono_filter_source = "hand"
        self.settings["mono_filter_choice"] = label
        self._save_settings()
        self.log(f"Filter set by hand: {label} → AAVSO filter {pair[0]}, comps in {core.band_label(pair[1])}.")
        self._update_filter_hint()
        self._comp_band_changed(old_band, resolve=True)   # 2.2.17 (review M2)

    def _rgb_channel_picked(self, pair):
        """2.2.18 (item 1): on colour images the Filter box chooses the channel measured (Green / Red / Blue imaging,
        Clear = luminance). Other filters are not a channel of the image. Not saved as a monochrome camera's filter."""
        channel = next((ch for ch, p in core.RGB_CHANNELS.items() if p[0] == pair[0]), None)
        if channel != self._rgb_channel and getattr(self, "busy", False):
            # review S1: the channel is read by the running job's frames (process-wide): never switched mid-run
            now = core.RGB_CHANNELS[self._rgb_channel]
            self.mono_filter_choice.set(core.mono_filter_label(now) or "")
            self.log("The channel of the color images cannot change while a step is running; choose it again when "
                     "it has finished.")
            return
        if channel is None:
            now = core.RGB_CHANNELS[self._rgb_channel]
            self.mono_filter_choice.set(core.mono_filter_label(now) or "")
            self.log(f"These lights are color images: measure one of their channels (Green imaging TG, Red imaging TR, "
                     f"Blue imaging TB, or Clear CV for luminance). Still {core.RGB_CHANNEL_WORDS[self._rgb_channel]}.")
            return
        if channel == self._rgb_channel:
            return
        old_band = self._band()
        self._rgb_channel = channel
        core.set_rgb_channel(channel)
        self.__dict__.setdefault("_rgb_answers", {})[self._rgb_lights_dir or self.light_dir.get().strip()] = channel
        self.mono_filter = core.RGB_CHANNELS[channel]
        self._mono_filter_source = "rgb"
        self._update_filter_hint()
        self._comp_band_changed(old_band)
        self.log(f"Color images: the {core.RGB_CHANNEL_WORDS[channel]} channel is measured from now on (AAVSO filter "
                 f"{pair[0]}, comps in {core.band_label(pair[1])}). Run Calibrate again to see it in the preview.")

    def _wrong_band_star(self, xy) -> bool:
        """2.2.17 (review M2): the labeled star under a marker carries another band than the comps', with a Sloan band
        on either side (V/B/R and OSC channel changes behave as before)."""
        band = (self._chart_star_at(xy) or {}).get("band") if xy is not None else None
        return bool(band) and band != self._band() and (core.is_sloan(band) or core.is_sloan(self._band()))

    def _comp_band_changed(self, old_band: str, resolve: bool = False):
        """2.2.17 (item 1, review M2): the comps' band changed to or from a Sloan band after a solve. The labeled
        chart holds the old band's magnitudes, so it is dropped, with the magnitudes filled from it (names filled from
        it too, so a new solve fills both again; names typed by hand stay, without a magnitude). resolve=True (a
        filter picked by hand on a calibrated image) solves again at once; otherwise the log says to."""
        new_band = self._band()
        if new_band == old_band or not (core.is_sloan(new_band) or core.is_sloan(old_band)):
            return
        chart_band = next((st.get("band") for _x, _y, st in self.chart_placed or [] if st.get("band")), None)
        if not self.chart_placed or chart_band == new_band:
            return
        for role in list(COMP_ROLES) + ["check"]:
            if getattr(self, f"{role}_xy", None) is None:
                continue
            if role in self.star_bands:
                getattr(self, f"{role}_name").set("")
                self.star_bands.pop(role, None)
            getattr(self, f"{role}_mag").set("")
        self.chart_placed = []
        label = core.band_label(new_band)
        can = (resolve and self.preview is not None and self.preview_is_debayered and not getattr(self, "busy", False))
        self.log(f"Comps are now taken in {label}: the labeled catalog stars (in {core.band_label(chart_band)}) were "
                 f"dropped with the magnitudes filled from them. "
                 + (f"Solving again to fetch {label} comps." if can else f"Solve again to fetch {label} comps."))
        try:
            self.refresh_preview()
            self.update_star_panel()
        except Exception:
            pass
        if can:
            self.label_aavso(auto=True)

    def _detect_sensor(self):
        """2.2.11: no Bayer keyword in the lights: look at the raw flats (up to five, flats before flat-darks). No
        colour mask -> MONO (unless the pattern was picked by hand); a colour mask while MONO is set -> a caution.
        2.2.18 (item 2): no flats, or flats that cannot tell (calibrated, too faint): the lights' sky background is
        tested the same way (stars masked); still unsure: "Color or monochrome camera?" is asked once for these lights,
        instead of keeping whatever pattern was set last."""
        try:
            frames, _master = core.calibration_source(self.flat_dir.get())
            frames = sorted(frames, key=lambda f: ("dark" in os.path.basename(f).lower(), f))[:5]
            kind = contrast = None
            source = "flat"
            for path in frames:
                kind, contrast = core.detect_sensor_from_flat(path)
                if kind is not None:
                    break
            check = frames[:1]
            if kind is None:
                # 2.2.18 (item 2): the lights' sky (up to three frames that are not rejected).
                lights = [p for p in core.split_lights(core.list_fits(self.light_dir.get()))[0]
                          if p not in self.rejected][:3]
                pedestal = self._sensor_pedestal()   # review M1: the bias level, when the Bias/Dark rows have one
                cache = self.__dict__.setdefault("_light_sensor_cache", {})   # review nit: per frame and pedestal
                for path in lights:
                    key = (path, pedestal)
                    if key not in cache:
                        cache[key] = core.detect_sensor_from_light(path, pedestal)
                    kind, contrast = cache[key]
                    if kind is not None:
                        source = "lights"
                        break
                check = check or lights[:1]
            if not check:
                return
            binned = ""
            try:
                hdr = core.read_header(check[0])
                if int((hdr or {}).get("XBINNING", 1) or 1) > 1:
                    binned = " (binned in the camera: a colour sensor binned 2x2 also looks like this)"
            except Exception:
                pass
            what = "the flat" if source == "flat" else "the lights' sky background"
            if kind == "mono" and self.pattern.get() != core.MONO:
                if self._pattern_user_set:
                    self.log(f"{what[0].upper() + what[1:]} shows no colour mask (its 2x2 pixel planes agree to "
                             f"{contrast * 100:.1f}%), so the camera looks monochrome{binned}, but the Bayer pattern "
                             "was set by hand; left as it is.")
                    return
                self.pattern.set(core.MONO)
                self.log(f"No Bayer pattern in the headers and {what} shows no colour mask (its 2x2 pixel planes agree "
                         f"to {contrast * 100:.1f}%){binned}: monochrome, Bayer pattern set to MONO. Pick the camera's "
                         "pattern on the Calibrate page if this is a colour camera.")
                self.status.set(f"Monochrome camera found (no colour mask in {what}): Bayer pattern MONO.")
            elif kind == "cfa" and self.pattern.get() == core.MONO:
                more = f" (its 2x2 pixel planes differ by {contrast * 100:.0f}%)" if contrast else ""
                self.log(f"Caution: {what} shows a colour mask{more}, but the Bayer pattern is MONO. For a colour "
                         "camera, pick its pattern (often RGGB) on the Calibrate page.")
            elif kind is None and not self._pattern_user_set:
                self._ask_sensor(contrast)
        except Exception as exc:
            self.log(f"Could not check the flat or lights for a colour mask ({exc}).")

    def _sensor_pedestal(self):
        """2.2.18 (review M1): the camera's bias level for the lights' sky test: the median of a SHOBS-P master bias or
        the first raw bias frame, else the first raw dark frame (bias plus a little dark current). None when there is
        neither (the test then needs a bright sky to call a camera monochrome)."""
        for var, kind in ((self.bias_dir, "bias"), (self.dark_dir, "dark")):
            try:
                frames, master = core.calibration_source(var.get())
                path = frames[0] if frames else (master if kind == "bias" else None)
                if not path:
                    continue
                data = np.asarray(core.read_fits(path)[0], dtype=np.float64)
                level = float(np.nanmedian(data))
                if math.isfinite(level):
                    return round(level, 1)
            except Exception:
                continue
        return None

    def _ask_sensor(self, contrast=None):
        """2.2.18 (item 2): neither the flats nor the lights tell a colour camera from a monochrome one: ask, once for
        these lights. The answer counts as a pattern picked by hand."""
        folder = self.light_dir.get().strip()
        if not folder or self._sensor_asked_for == folder:
            return
        self._sensor_asked_for = folder
        planes = (f" (the lights' 2x2 pixel planes differ by {contrast * 100:.1f}%)"
                  if contrast is not None and math.isfinite(contrast) else "")
        answer = self._choose_one(
            "Color or monochrome camera?",
            "The headers name no Bayer pattern, and SHOBS-P cannot tell from the flats or the lights whether these "
            f"frames come from a color camera or a monochrome one{planes}.\n\nColor or monochrome camera?",
            [("Color camera", "color"), ("Monochrome camera", "mono")], cancel=None)
        if answer == "mono":
            self.pattern.set(core.MONO)
            self.log("Camera: monochrome (your answer). Bayer pattern set to MONO.")
        elif answer == "color":
            if self.pattern.get() == core.MONO:
                self.pattern.set("RGGB")
            self.log(f"Camera: color (your answer). Bayer pattern {self.pattern.get()}: check that it is your "
                     "camera's pattern on the Calibrate page.")
        else:
            self.log(f"Camera type not answered: Bayer pattern left at {self.pattern.get()}. Check it on the "
                     "Calibrate page (MONO for a monochrome camera).")
            return
        self._pattern_user_set = True

    def _choose_one(self, heading: str, message: str, choices, cancel=None):
        """2.2.18 (items 1, 2): a question with one labeled button per answer; returns the chosen value, or `cancel`
        when the window is closed. Sizes itself to its text (no fixed widths)."""
        result = {"value": cancel}
        win = Toplevel(self)
        win.title(f"{APP_TITLE} — {heading}")
        win.transient(self)
        frame = ttk.Frame(win, style="Card.TFrame", padding=14)
        frame.pack(fill=BOTH, expand=True)
        ttk.Label(frame, text=heading, style="Card.TLabel", font=(core.UI_FONT, 11, "bold")).pack(anchor=W, pady=(0, 6))
        ttk.Label(frame, text=message, style="Card.TLabel", justify=LEFT,
                  wraplength=text_wrap_px(self)).pack(anchor=W, pady=(0, 10))   # review S3
        row = ttk.Frame(frame, style="Card.TFrame")
        row.pack(anchor=E)

        def pick(value):
            result["value"] = value
            win.destroy()
        for label, value in choices:
            ttk.Button(row, text=label, command=lambda v=value: pick(v)).pack(side=LEFT, padx=(6, 0))
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        if choices:   # review S3: Return = the first answer, Escape = closed
            win.bind("<Return>", lambda _e: pick(choices[0][1]))
        win.bind("<Escape>", lambda _e: win.destroy())
        win.after_idle(lambda: fit_to_content(win))
        try:
            win.grab_set()
        except Exception:
            pass
        try:
            self.wait_window(win)
        except Exception:
            pass
        return result["value"]

    def _setup_rgb_lights(self, header: dict, n_lights: int = 0):
        """2.2.18 (item 1): the lights are colour images other software already debayered (three channels). Before
        2.2.18 the first channel (red) was read and, with a Bayer pattern set, debayered a second time. Now the user
        picks the channel (green TG, red TR, blue TB, or luminance CV), the pattern is MONO for these lights (no
        debayer, no superpixel weights) and they count as pre-calibrated (the photon noise comes from the sky).
        Asked once per lights folder."""
        folder = self.light_dir.get().strip()
        if self._rgb_channel and self._rgb_lights_dir == folder:
            core.set_rgb_channel(self._rgb_channel)
            return
        pat = core.header_bayer(header or {})
        answers = self.__dict__.setdefault("_rgb_answers", {})   # review S4: {lights folder: channel} this session
        if folder in answers:
            if self._rgb_channel is None:
                self._pattern_before_rgb = self.pattern.get()
            channel = answers[folder]
            self._rgb_channel, self._rgb_lights_dir = channel, folder
            core.set_rgb_channel(channel)
            self.pattern.set(core.MONO)
            self._sync_camera_note()
            self.log(f"Color images (three channels, already debayered): the {core.RGB_CHANNEL_WORDS[channel]} channel "
                     "is measured, as chosen before for this folder (the Filter box on the Calibrate page changes it).")
            return
        before = self._rgb_channel or "green"
        answer = self._choose_one(
            "Color images",
            (f"These {n_lights} lights" if n_lights else "These lights")
            + " are color images that other software has already debayered (three channels: red, green and blue)."
            + (f"\n\nTheir header still names a Bayer pattern ({pat}); it is not used: debayering them again "
               "would mix pixels of the finished image." if pat else "")
            + "\n\nWhich channel should be measured?\n"
            "  Green (TG): closest to V; the usual choice\n"
            "  Red (TR) / Blue (TB): that channel only\n"
            "  Luminance (CV): all three combined, as a clear filter",
            [("Green (TG)", "green"), ("Red (TR)", "red"), ("Blue (TB)", "blue"), ("Luminance (CV)", "luminance")],
            cancel=None)
        channel = answer or before
        if self._rgb_channel is None:
            self._pattern_before_rgb = self.pattern.get()
        self._rgb_channel = channel
        self._rgb_lights_dir = folder
        answers[folder] = channel
        core.set_rgb_channel(channel)
        self.pattern.set(core.MONO)
        self._sync_camera_note()
        if pat:
            self.log(f"Caution: these lights are color images (three channels, already debayered), but the header "
                     f"names a Bayer pattern ({pat}). It is not used: the {core.RGB_CHANNEL_WORDS[channel]} channel is "
                     "measured as it is, with no second debayer.")
        self.log(f"Color images (three channels, already debayered): the {core.RGB_CHANNEL_WORDS[channel]} channel "
                 + ("is measured" if answer else "is measured (no answer given)")
                 + f", AAVSO filter {core.RGB_CHANNELS[channel][0]}, comps in "
                 f"{core.band_label(core.RGB_CHANNELS[channel][1])}. No debayer; treated as pre-calibrated (no bias, "
                 "dark or flat; photon noise from the sky). Pick another channel in the Filter box on the Calibrate "
                 "page (Green, Red, Blue imaging or Clear).")

    def _leave_rgb_lights(self):
        """2.2.18 (item 1): lights that are not colour images after colour images: the Bayer pattern from before."""
        before = self._pattern_before_rgb
        self._rgb_channel = None
        self._rgb_lights_dir = None
        self._pattern_before_rgb = None
        core.set_rgb_channel(None)
        # review S2: the Filter box no longer shows the colour images' channel; a mono camera's own filter comes back
        hand = self._mono_filter_hand()
        self.mono_filter = hand
        self._mono_filter_source = "hand" if hand else None
        try:
            self.mono_filter_choice.set(self.settings.get("mono_filter_choice", "") if hand else "")
        except Exception:
            pass
        if before and before != self.pattern.get():
            self.pattern.set(before)
        self._sync_camera_note()
        self._update_filter_hint()

    def _sync_camera_note(self):
        try:
            note = self.notes.get().strip()
            # 2.2.18 (item 1): colour images run as MONO (no debayer) but are not from a monochrome camera
            want = MONO_NOTE if self.pattern.get() == core.MONO and not getattr(self, "_rgb_channel", None) else OSC_NOTE
            if note in (OSC_NOTE, MONO_NOTE, "") and note != want:
                self.notes.set(want)
        except Exception:
            pass

    def _setup_microobservatory(self, header: dict, n_lights: int):
        """MicroObservatory frames: monochrome, 12-bit, taken in Arizona with a 560 mm telescope. Offer the
        telescope's camera, site, and optics; your own come back when you scan your own lights."""
        site = core.header_site(header)
        if site and site[1] > 0:
            # MicroObservatory writes the longitude west-positive (110.88 for Arizona); SHOBS-P uses east-positive.
            site = (site[0], -site[1], site[2])
        lat, lon, elev = site if site else (31.68, -110.88, 2340.0)
        where = "from the FITS header" if site else "approximately, Whipple Observatory on Mt. Hopkins, Arizona"
        if site and not elev:
            # 2.2.8: MicroObservatory headers give latitude and longitude but no elevation. Never leave the user's own
            # elevation in place with Arizona's coordinates (CoRoT-1 run: 280 m reported for Mt. Hopkins).
            elev = 2340.0
            where += "; elevation approximate (Mt. Hopkins ridge)"
        scale = core.header_plate_scale(header)
        focal = 560.0
        # 6.8 µm pixels binned 2x2 at 560 mm: about 5"/px, and a 650 x 500 frame covers about 56' x 42'.
        pixel = scale * focal / 206.265 if scale else 13.6
        self.scale_approx = not scale
        use = messagebox.askyesno(
            APP_TITLE,
            f"These {n_lights} lights look like MicroObservatory frames, taken in Arizona with a small telescope and a "
            "monochrome camera.\n\nUse that telescope's setup for these frames?\n"
            f"  Site: latitude {lat:.3f}, longitude {lon:.3f}" + (f", elevation {elev:.0f} m" if elev else "")
            + f" ({where})\n"
            f"  Optics: focal length {focal:.0f} mm, pixel {pixel:.1f} µm (about {pixel / focal * 206.265:.1f}\"/px"
            + ("" if scale else "; Solve refines it") + ")\n"
            "  Camera: monochrome (no debayer), clear filter (CV), binning 1, saturation 4000 ADU\n\n"
            "Your own site, optics, and camera settings are kept and come back when you scan a folder of your own "
            "lights.",
        )
        if not use:
            self.log("MicroObservatory frames: setup not changed (answered No). Set Bayer pattern MONO on the Calibrate page "
                     "for these monochrome frames.")
            return
        if self._own_setup is None:
            # Taken only when the borrowed setup is accepted (2.2.4), so answering No never freezes the saved site.
            self._own_setup = self._setup_snapshot()
        self.pattern.set(core.MONO)
        self.debayer_mode.set("luminance")
        if self._mono_filter_source != "header":
            # 2.2.11: MicroObservatory frames are clear unless their header says otherwise; a filter set by hand for
            # your own camera is not used for them (it comes back with your own setup).
            self.mono_filter = ("CV", "V")
            self._mono_filter_source = "mobs"
            self._update_filter_hint()
        self.sat_limit.set(4000)
        if int(self.binning.get() or 1) > 1:
            self.binning.set(1)
            self.log("  Binning set to 1. Build the masters again if they were built with another binning.")
        self.lat.set(f"{lat:.4f}")
        self.lon.set(f"{lon:.4f}")
        if elev:
            self.elevation.set(f"{elev:.0f}")
        self.focal_mm.set(focal)
        self.pixel_um.set(round(pixel, 2))
        self.aperture_mm.set("152")   # 2.2.9: MicroObservatory's 6-inch telescope, for the scintillation error
        self.telescope_name.set("")   # 2.2.16 (item 5): your telescope's name is not theirs (comes back with yours)
        self.log(f"MicroObservatory setup: site {lat:.4f}, {lon:.4f} ({where}); optics {focal:.0f} mm, {pixel:.2f} µm "
                 f"({'from the header scale' if scale else 'approximate'}); monochrome, CV, saturation 4000 ADU. "
                 "Your own setup comes back when you scan your own lights.")
        self.status.set(f"{n_lights} MicroObservatory lights: telescope setup in use.")

    _SETUP_NAMES = ("lat", "lon", "elevation", "focal_mm", "pixel_um", "aperture_mm", "pattern", "debayer_mode",
                    "sat_limit", "binning", "telescope_name")   # 2.2.16 (item 5): telescope_name

    def _setup_value(self, name: str):
        """A Site/Optics/camera box's value; "" when a number box is empty (real Tk raises TclError on .get())."""
        try:
            return getattr(self, name).get()
        except Exception:
            return ""

    def _setup_snapshot(self) -> dict:
        return {n: self._setup_value(n) for n in self._SETUP_NAMES}

    def _header_setup(self, header: dict):
        """2.2.9 (John): Scan files fills the Site and Optics boxes from the lights' FITS header.
        - Frames from a site far (> 50 km) from yours (another observer, a remote telescope): offer to use that site
          and those optics for these frames; your own come back when you scan your own lights (as for MicroObservatory).
        - Your own frames: empty boxes are filled silently; values that differ are listed in one question.
        Binning is never taken from the header (XBINNING is the camera's; SHOBS-P's Binning is its own)."""
        site = core.header_site(header)
        opt = core.header_optics(header)
        own = self._own_setup
        ref = own if own is not None else self._setup_snapshot()

        def fnum(v):
            try:
                f = float(v)
                return f if math.isfinite(f) else None
            except (TypeError, ValueError):
                return None

        ref_lat, ref_lon = fnum(ref.get("lat")), fnum(ref.get("lon"))
        cam = " / ".join(str(header.get(k) or "").strip() for k in ("TELESCOP", "INSTRUME") if header.get(k))
        if (site and own is not None and fnum(self.lat.get()) == round(site[0], 4)
                and fnum(self.lon.get()) == round(site[1], 4)):
            return   # these frames' setup is already in use
        far = asked = False
        if site and ref_lat is not None and ref_lon is not None:
            dist = core.site_distance_km((ref_lat, ref_lon), site[:2])
            far = dist > 50
            where = f"{dist:.0f} km from your site"
        elif site:
            # No site of your own yet: never save a header's site as yours without asking (it may be a friend's).
            where = "and your Site boxes are empty"
            far = not messagebox.askyesno(
                APP_TITLE,
                f"These lights were taken at latitude {site[0]:.3f}, longitude {site[1]:.3f}"
                + (f" ({cam})" if cam else "") + ".\n\nIs that your own site? Yes saves it as yours; No uses it "
                "for these frames only.")
            asked = True
        if far:
            if not asked and not messagebox.askyesno(
                    APP_TITLE,
                    f"These lights were taken at latitude {site[0]:.3f}, longitude {site[1]:.3f}"
                    + (f" ({cam})" if cam else "") + f", {where}.\n\n"
                    "Use the site and optics in these frames for them? Your own site, optics and camera "
                    "settings are kept and come back when you scan a folder of your own lights."):
                self._restore_own_setup()   # not a previous telescope's setup either
                self.log("Lights from another site: your own site and optics kept (answered No). Check the "
                         "Site and Optics boxes before photometry; the airmass and BJD times use them.")
                return
            if self._own_setup is None:
                self._own_setup = self._setup_snapshot()
            else:
                # Start from your own setup, not the previous telescope's (MicroObservatory's MONO and 4000 ADU,
                # or another eVscope's optics), keeping it set aside.
                for name, value in self._own_setup.items():
                    try:
                        getattr(self, name).set(value)
                    except Exception:
                        pass
                self.scale_approx = False
            self.lat.set(f"{site[0]:.4f}")
            self.lon.set(f"{site[1]:.4f}")
            # Never leave your own elevation with another site's coordinates (2.2.8 MicroObservatory lesson).
            self.elevation.set(f"{site[2]:.0f}" if site[2] is not None else "")
            for key in ("focal_mm", "pixel_um", "aperture_mm"):
                if key in opt:
                    getattr(self, key).set(round(opt[key], 2) if key != "aperture_mm" else f"{opt[key]:.0f}")
            self.telescope_name.set("")   # 2.2.16 (item 5): another telescope; yours comes back with your setup
            got = ", ".join(f"{k.split('_')[0]} from {v}" for k, v in opt["source"].items())
            self.log(f"Using the site and optics in these frames: {site[0]:.4f}, {site[1]:.4f}"
                     + (f", {site[2]:.0f} m" if site[2] is not None else ", elevation unknown")
                     + (f"; {got}" if got else "") + ". Your own setup comes back when you scan your own lights.")
            self.status.set("Lights from another site: their site and optics are in use.")
            return
        # Your own frames (or no site in the header): any borrowed setup goes back first.
        self._restore_own_setup()
        wanted = []
        if site:
            wanted += [("lat", "Latitude", round(site[0], 4), "header site", 0.01),
                       ("lon", "Longitude", round(site[1], 4), "header site", 0.01)]
            if site[2] is not None:
                wanted.append(("elevation", "Elevation (m)", round(site[2]), "header site", 30.0))
        for key, label in (("aperture_mm", "Telescope aperture (mm)"), ("focal_mm", "Focal length (mm)"),
                           ("pixel_um", "Pixel size (µm)")):
            if key in opt:
                v = opt[key]
                wanted.append((key, label, round(v, 2) if key == "pixel_um" else round(v), opt["source"][key],
                               0.02 * v))
        filled, differ = [], []
        # 2.2.18 (review S6): values you said No to before are not filled in silently either
        declined_tokens = {t for d in (self.settings.get("header_declined") or []) for t in str(d).split(";")}
        unset_filled = False
        for key, label, value, src, tol in wanted:
            cur = fnum(self._setup_value(key))
            unset = key in ("focal_mm", "pixel_um") and cur is not None and cur > 0 and self._optics_unset(key)
            if unset and f"{key}={value:g}" in declined_tokens:
                continue
            if cur is None or (key in ("focal_mm", "pixel_um") and (cur <= 0 or unset)):
                getattr(self, key).set(value if key not in ("lat", "lon", "elevation", "aperture_mm") else f"{value:g}")
                filled.append(f"{label} {value:g} ({src})")
                unset_filled = unset_filled or unset
            elif abs(cur - value) > tol:
                differ.append((key, label, cur, value, src))
        if filled:
            self.log("Filled from the lights' FITS header: " + "; ".join(filled) + ".")
            if unset_filled:   # 2.2.18 (item 11, review S6)
                self.log("Caution: the focal length / pixel size from these lights are now saved as your own optics. "
                         "If they come from another telescope, type your own in the Optics box (Input page).")
        if not differ:
            return
        key = ";".join(f"{k}={v:g}" for k, _l, _c, v, _s in differ)
        declined = self.settings.get("header_declined") or []
        if key in declined:
            return
        lines = "\n".join(f"  {label}: yours {cur:g}, header {value:g} ({src})" for _k, label, cur, value, src in differ)
        if messagebox.askyesno(APP_TITLE, "The lights' FITS header differs from the Site and Optics boxes:\n" + lines
                               + "\n\nUse the header values? (No keeps yours and will not ask again for these values.)"):
            for k, _label, _cur, value, _src in differ:
                getattr(self, k).set(value if k not in ("lat", "lon", "elevation", "aperture_mm") else f"{value:g}")
            self.log("Site/Optics updated from the lights' FITS header: "
                     + "; ".join(f"{label} {value:g}" for _k, label, _c, value, _s in differ) + ".")
        else:
            self.settings["header_declined"] = (declined + [key])[-20:]
            self._save_settings()

    def _restore_own_setup(self):
        """Back from someone else's frames (MicroObservatory) to your own site, optics, and camera settings."""
        if self._own_setup is None:
            return
        for name, value in self._own_setup.items():
            try:
                getattr(self, name).set(value)
            except Exception:
                pass
        self._own_setup = None
        self.scale_approx = False
        if getattr(self, "_mono_filter_source", None) == "mobs":
            hand = self._mono_filter_hand()
            self.mono_filter = hand
            self._mono_filter_source = "hand" if hand else None
            if hand:
                self.mono_filter_choice.set(self.settings.get("mono_filter_choice", ""))
            self._update_filter_hint()
        self.log("Your own site, optics, and camera settings are back (they were set aside for another telescope's frames).")

    def _page_blink(self):
        page = ttk.Frame(self.pages)
        card = ttk.Frame(page, style="Card.TFrame", padding=12)
        card.pack(fill=BOTH, expand=True)
        ttk.Label(card, text="Blink", font=(core.UI_FONT, 16, "bold"), style="Card.TLabel").pack(anchor=W)
        ttk.Label(
            card,
            text="Raw lights, before calibration. Reject trails, clouds, and bad guiding. Rejected frames are left on disk and skipped by photometry.",
            style="Hint.TLabel",
        ).pack(anchor=W, pady=(2, 6))
        tools = ttk.Frame(card, style="Card.TFrame")
        tools.pack(fill=X, pady=4)
        ttk.Radiobutton(tools, text="Lights", value="lights", variable=self.blink_source, command=self.load_blink_list).pack(side=LEFT, padx=8)
        ttk.Radiobutton(tools, text="Flats", value="flats", variable=self.blink_source, command=self.load_blink_list).pack(side=LEFT)
        ttk.Button(tools, text="Previous", command=lambda: self.step_blink(-1)).pack(side=LEFT, padx=6)
        ttk.Button(tools, text="Next", command=lambda: self.step_blink(1)).pack(side=LEFT)
        ttk.Button(tools, text="Blink", command=self.toggle_blink).pack(side=LEFT, padx=6)
        ttk.Button(tools, text="Stop", command=self.stop_blink).pack(side=LEFT)
        ttk.Label(tools, text="Speed", style="Card.TLabel").pack(side=LEFT, padx=(10, 2))
        ttk.Combobox(tools, textvariable=self.blink_speed, values=BLINK_SPEEDS, width=12, state="readonly").pack(side=LEFT)
        # 2.2.17 (item 6): Reject only rejects and Keep only brings a frame back (the old "Reject / restore" button
        # toggled, so pressing it on a frame already rejected silently restored it).
        ttk.Button(tools, text="Reject", command=self.reject_frame).pack(side=LEFT, padx=(6, 2))
        ttk.Button(tools, text="Keep", command=self.keep_frame).pack(side=LEFT)
        self._stretch_controls(card, "blink").pack(anchor=W, pady=(0, 4))   # 2.2.15 (GitHub #1)
        self.blink_label = StringVar(value="No lights loaded.")
        ttk.Label(card, textvariable=self.blink_label, style="Hint.TLabel").pack(anchor=W)
        # 2.2.17 (item 6): the rejected frames by their own (file) numbers, and a way to bring one back from the list.
        rej = ttk.Frame(card, style="Card.TFrame")
        rej.pack(anchor=W, fill=X, pady=(2, 0))
        self.blink_rejected_text = StringVar(value="Rejected: none")
        self.blink_rejected_label = ttk.Label(rej, textvariable=self.blink_rejected_text, style="Card.TLabel",
                                              justify=LEFT)
        self.blink_rejected_label.pack(side=LEFT, anchor=W)
        self.blink_rejected_pick = StringVar(value="")
        self.blink_rejected_box = ttk.Combobox(rej, textvariable=self.blink_rejected_pick, values=[], width=8,
                                               state="readonly")
        ttk.Button(rej, text="Keep this one", command=self.keep_listed_frame).pack(side=RIGHT)
        ttk.Button(rej, text="Show", command=self.show_listed_frame).pack(side=RIGHT, padx=(4, 4))
        self.blink_rejected_box.pack(side=RIGHT)
        ttk.Label(rej, text="Rejected frame", style="Card.TLabel").pack(side=RIGHT, padx=(12, 4))
        rej.bind("<Configure>", lambda e: self._wrap_rejected_label(int(e.width)), add="+")
        self._rejected_right = [w for w in rej.winfo_children() if w is not self.blink_rejected_label]
        host = ttk.Frame(card, style="Card.TFrame")
        host.pack(fill=BOTH, expand=True, pady=6)
        if Figure is None:
            self.blink_canvas = None
        else:
            self.blink_fig = Figure(figsize=(8, 4.2), dpi=100, facecolor="#ffffff")
            self.blink_ax = self.blink_fig.add_subplot(111)
            self.blink_canvas = FigureCanvasTkAgg(self.blink_fig, master=host)
            self.blink_canvas.get_tk_widget().configure(width=400, height=300)
            self.blink_canvas.get_tk_widget().pack(fill=BOTH, expand=True)
            # 2.2.5: keep the title band the same height in pixels when the window is resized.
            self.blink_canvas.mpl_connect("resize_event", lambda _e: self._blink_layout())
        nav = ttk.Frame(card, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", before=host, pady=(6, 0))
        ttk.Button(nav, text="Back", command=lambda: self.show_step(STEP_INPUT)).pack(side=LEFT)
        ttk.Button(nav, text="Next: Calibrate", command=lambda: self.show_step(STEP_CAL)).pack(side=RIGHT)
        return page

    def _blink_folder(self):
        return self.light_dir.get() if self.blink_source.get() == "lights" else self.flat_dir.get()

    def _lights(self, log_skips: bool = False) -> list[str]:
        """Light frames to use: the Lights folder minus rejected frames and any bias/dark/flat/master files."""
        paths, skipped, note = core.split_lights(core.list_fits(self.light_dir.get()))
        if note and log_skips:
            self.log("Note: " + note)
        if skipped and log_skips:
            names = ", ".join(os.path.basename(p) for p, _ in skipped[:6]) + (" …" if len(skipped) > 6 else "")
            self.log(f"Skipped {len(skipped)} calibration file(s) found in the Lights folder: {names}")
        return [p for p in paths if p not in self.rejected]

    def load_blink_list(self):
        self.blink_playing = False
        self._cancel_blink_timer()
        folder = self._blink_folder()
        if folder != self._blink_loaded_from:
            self.blink_index = 0
        self._blink_loaded_from = folder
        self.blink_paths = [f for f in core.list_fits(folder) if not core.is_shobsp_master_name(f)]  # 2.2.8
        if self.blink_source.get() == "lights":
            self.blink_paths = core.split_lights(self.blink_paths)[0]
        self.blink_index = min(self.blink_index, max(0, len(self.blink_paths) - 1))
        self._blink_labels = dict(zip(self.blink_paths, core.frame_labels_for(self.blink_paths)))   # 2.2.17 (round 2)
        self._start_blink_preload()
        self.show_blink()

    # ---- 2.2.3: blink cache and speed -------------------------------------------------------
    @staticmethod
    def _blink_speed_text(seconds) -> str:
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            seconds = 1.0
        for text in BLINK_SPEEDS:
            if abs(float(text.split()[0]) - seconds) < 1e-6:
                return text
        return "1 s/frame"

    def _blink_seconds(self) -> float:
        try:
            return float(self.blink_speed.get().split()[0])
        except (ValueError, IndexError):
            return 1.0

    def _remember_blink_speed(self):
        self.settings["blink_seconds"] = self._blink_seconds()
        self._save_settings()

    def _blink_key(self, path):
        try:
            b = max(int(self.binning.get()), 1)
        except Exception:
            b = 1
        return (path, b)

    def _blink_picture(self, path, binning, stretch=None):
        """Read one frame and make the small 8-bit picture Blink shows (about 1000 px wide). 2.2.15: returns
        (picture, exposure, small float16 copy, stretch); the copy (offset and scaled so float16 holds it) lets a new
        stretch be drawn without reading the file again."""
        data, header = core.read_fits(path)
        self._blink_times[path] = self._header_clock(header)   # 2.2.17 (item 6): shown with the frame number
        data = core.bin_image(data, binning)
        if data.shape[1] > 1000:
            step = int(math.ceil(data.shape[1] / 1000))
            data = data[::step, ::step]
        lo, hi = core.stretch_limits(data, 5.0, 99.5, step=4)
        small = np.clip((np.asarray(data, dtype=np.float32) - np.float32(lo)) / np.float32(hi - lo), -6e4, 6e4)
        small = small.astype(np.float16)
        stretch = dict(stretch or self.stretch["blink"])
        pic = core.stretch_to_uint8(small, stretch, step=4)
        return pic, core.header_exptime(header), small, stretch

    def _blink_restretch(self, value, stretch):
        """A cached Blink frame drawn again with another stretch (from its float16 copy: no file read)."""
        pic, exptime, small = value[0], value[1], (value[2] if len(value) > 2 else None)
        if small is None:
            return value
        return core.stretch_to_uint8(small, stretch, step=4), exptime, small, dict(stretch)

    def _blink_store(self, key, value):
        if key not in self._blink_cache:
            self._blink_cache_order.append(key)
        self._blink_cache[key] = value
        # Keep memory bounded (about 1.5 GB of pictures and float16 copies, ~500 Bin 1 frames); the oldest go first.
        size = sum(getattr(v, "nbytes", 0) for v in value if isinstance(v, np.ndarray))
        limit = max(50, int(BLINK_CACHE_BYTES // max(1, size)))
        while len(self._blink_cache_order) > limit:
            old = self._blink_cache_order.pop(0)
            self._blink_cache.pop(old, None)

    def _blink_stretch_changed(self):
        """2.2.15: a new Blink stretch. The frame on screen is redrawn now; the other cached frames are redrawn from
        their float16 copies in the background (a frame still waiting is redrawn when it is shown)."""
        self._blink_restretch_gen += 1
        gen = self._blink_restretch_gen
        stretch = dict(self.stretch["blink"])
        self.show_blink()
        items = [(k, v) for k, v in self._blink_cache.items() if len(v) > 3 and v[3] != stretch]
        if not items:
            return

        def work():
            for key, value in items:
                if gen != self._blink_restretch_gen:
                    return
                try:
                    new = self._blink_restretch(value, stretch)
                except Exception:
                    continue
                self.call_ui(self._blink_put_restretched, key, new, gen)
                time.sleep(0.001)

        threading.Thread(target=work, daemon=True).start()

    def _blink_put_restretched(self, key, value, gen):
        if gen == self._blink_restretch_gen and key in self._blink_cache:
            self._blink_cache[key] = value

    def _start_blink_preload(self):
        """Read the frames ahead of the one on screen in a background thread."""
        self._blink_gen += 1
        gen = self._blink_gen
        paths = list(self.blink_paths)
        if not paths:
            return
        try:
            binning = max(int(self.binning.get()), 1)
        except Exception:
            binning = 1
        start = self.blink_index
        self._blink_preload_bin = binning

        def work():
            order = paths[start:] + paths[:start]
            for path in order:
                if gen != self._blink_gen:
                    return
                key = (path, binning)
                if key in self._blink_cache:
                    continue
                try:
                    value = self._blink_picture(path, binning)
                except Exception:
                    continue
                if gen != self._blink_gen:
                    return
                self.call_ui(self._blink_store, key, value)
                time.sleep(0.001)

        self._blink_worker = threading.Thread(target=work, daemon=True)
        self._blink_worker.start()

    def step_blink(self, delta):
        if not self.blink_paths or self._blink_folder() != self._blink_loaded_from:
            self.load_blink_list()
        if not self.blink_paths:
            return
        self.blink_index = (self.blink_index + delta) % len(self.blink_paths)
        self.show_blink()

    def stop_blink(self):
        self.blink_playing = False
        self._cancel_blink_timer()
        self.status.set("Blink stopped.")

    def _cancel_blink_timer(self):
        job = getattr(self, "_blink_job", None)
        self._blink_job = None
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass

    def toggle_blink(self):
        if not self.blink_paths:
            self.load_blink_list()
        self._cancel_blink_timer()      # never two timer chains (that would double the speed)
        self.blink_playing = not self.blink_playing
        if self.blink_playing:
            self._blink_wait = 0
            self._blink_tick()

    def _blink_tick(self):
        self._blink_job = None
        if not self.blink_playing or not self.blink_paths:
            return
        nxt = (self.blink_index + 1) % len(self.blink_paths)
        key = self._blink_key(self.blink_paths[nxt])
        if key[1] != getattr(self, "_blink_preload_bin", key[1]):
            self._start_blink_preload()          # the binning changed since the frames were read
        if key not in self._blink_cache:
            worker = getattr(self, "_blink_worker", None)
            self._blink_wait = getattr(self, "_blink_wait", 0) + 1
            if worker is not None and worker.is_alive() and self._blink_wait < 10:
                # Still being read in the background: wait a moment instead of stalling the window.
                self.blink_label.set(f"Reading frame {self.frame_label(self.blink_paths[nxt])} …")
                self._blink_job = self.after(100, self._blink_tick)
                return
            # Waited a second, or the reader has finished (an unreadable frame, or one dropped from memory):
            # show it the slow way (show_blink reads it, or says it is unreadable) and carry on; if the reader
            # had finished, start it again from here to refill what was dropped.
            if worker is None or not worker.is_alive():
                self.blink_index = nxt
                self._start_blink_preload()
        self._blink_wait = 0
        self.blink_index = nxt
        self.show_blink()
        self._blink_job = self.after(int(self._blink_seconds() * 1000), self._blink_tick)

    # ---- 2.2.17 (item 6): frame numbers from the file names; explicit reject / keep; the rejected list ----------
    def frame_label(self, path) -> str:
        """The frame's own number from its file name (core.frame_label): the same on Blink, in the log and the CSV."""
        # 2.2.17 (round 2): the Blink list's labels are worked out once when the list is loaded, so a Blink tick
        # never scans the folder; other paths (log, flats folder changes) ask core.
        label = self._blink_labels.get(path)
        return label if label is not None else core.frame_label(path)

    def _frame_time(self, path) -> str:
        """Local clock time of the exposure start (from DATE-OBS / JD in the header), '' when the header has none."""
        times = self._blink_times
        if path in times:
            return times[path]
        try:
            header = core.read_header(path)
        except Exception:
            header = {}
        times[path] = self._header_clock(header)
        return times[path]

    @staticmethod
    def _header_clock(header) -> str:
        # Review: mid_jd first (JD keywords, a DATE-OBS with a UTC offset such as MicroObservatory's
        # "2015-01-25T19:39:40.386-0700", DATE-OBS + TIME-OBS); the exposure start is mid-exposure minus half of it.
        try:
            jd = core.mid_jd(header) - core.header_exptime(header) / 2.0 / 86400.0
            return f"{core.jd_to_datetime_utc(float(jd)).astimezone():%H:%M:%S}"
        except Exception:
            pass
        try:   # without astropy's Time: DATE-OBS read here, its UTC offset (if any) applied
            import re
            from datetime import timedelta
            text = str(header.get("DATE-OBS") or header.get("DATE_OBS") or "").strip()
            if "T" not in text:
                return ""
            off = timedelta(0)
            m = re.search(r"T[0-9:.]+([+-])(\d{2}):?(\d{2})$", text)
            if m:
                off = (1 if m.group(1) == "+" else -1) * timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
                text = text[: m.start(1)]
            return f"{(core._parse_iso(text) - off).astimezone():%H:%M:%S}"
        except Exception:
            return ""

    def _frame_words(self, path) -> str:
        """'frame 222 (21:53:12)': the frame's number and local time, as Blink shows it."""
        t = self._frame_time(path)
        return f"frame {self.frame_label(path)}" + (f" ({t})" if t else "")

    def reject_frame(self):
        """Reject the frame on screen. A frame already rejected stays rejected (and the status line says so)."""
        if not self.blink_paths:
            return
        path = self.blink_paths[self.blink_index]
        if path in self.rejected:
            self.status.set(f"{self._frame_words(path).capitalize()} is already rejected (Keep brings it back).")
        else:
            self.rejected.add(path)
            self.status.set(f"Rejected {self._frame_words(path)}: {os.path.basename(path)}.")
        self.show_blink()

    def keep_frame(self):
        """Bring the frame on screen back (un-reject): only this button, or Keep this one under the list, does that."""
        if not self.blink_paths:
            return
        path = self.blink_paths[self.blink_index]
        self._keep_path(path)
        self.show_blink()

    def _keep_path(self, path):
        if path in self.rejected:
            self.rejected.discard(path)
            self.status.set(f"Kept {self._frame_words(path)}: {os.path.basename(path)} is used again.")
        else:
            self.status.set(f"{self._frame_words(path).capitalize()} is not rejected.")

    def _wrap_rejected_label(self, width: int):
        """Review: the rejected list wraps in what the row's right-hand group (picker and buttons) leaves, measured
        (so it follows Windows display scaling), not a fixed pixel allowance."""
        try:
            right = sum(int(w.winfo_reqwidth()) for w in self._rejected_right) + int(40 * ui_scale(self))
        except Exception:
            right = int(360 * ui_scale(self))
        self.blink_rejected_label.configure(wraplength=max(width - right, int(200 * ui_scale(self))))

    def _rejected_in_list(self) -> list:
        """The rejected frames among the frames Blink lists (lights or flats), in list order."""
        return [p for p in (self.blink_paths or []) if p in self.rejected]

    def _update_rejected_list(self):
        if not hasattr(self, "blink_rejected_text"):
            return
        paths = self._rejected_in_list()
        what = "flats" if self.blink_source.get() == "flats" else "frames"
        labels = [self.frame_label(p) for p in paths]   # from the list's labels (no folder scan per Blink tick)
        sig = (tuple(paths), tuple(labels), self.blink_source.get())
        if sig == getattr(self, "_rejected_list_sig", None):
            return   # nothing changed (Blink playing)
        self._rejected_list_sig = sig
        if paths:
            self.blink_rejected_text.set(f"Rejected {what} ({len(paths)}): {core.frame_list_text(labels)}")
        else:
            self.blink_rejected_text.set(f"Rejected {what}: none")
        self._rejected_choices = {lab: p for lab, p in zip(labels, paths)}
        values = core.frame_list_text(labels, limit=len(labels)).split(", ") if labels else []   # by number
        try:
            self.blink_rejected_box.configure(values=values)
        except Exception:
            pass
        if self.blink_rejected_pick.get() not in self._rejected_choices:
            self.blink_rejected_pick.set(values[0] if values else "")

    def _listed_path(self):
        label = self.blink_rejected_pick.get().strip()
        path = getattr(self, "_rejected_choices", {}).get(label)
        if path is None:
            self.status.set("Pick a rejected frame in the list first." if self._rejected_in_list()
                            else "No rejected frames.")
        return path

    def show_listed_frame(self):
        """Blink shows the frame picked in the rejected list."""
        path = self._listed_path()
        if path is None or path not in self.blink_paths:
            return
        self.blink_playing = False
        self._cancel_blink_timer()
        self.blink_index = self.blink_paths.index(path)
        self.show_blink()

    def keep_listed_frame(self):
        """Un-reject the frame picked in the rejected list (it need not be the one on screen)."""
        path = self._listed_path()
        if path is None:
            return
        self._keep_path(path)
        self.show_blink() if self.blink_paths else self._update_rejected_list()

    def show_blink(self):
        if not self.blink_paths:
            self.blink_label.set("No lights in the lights folder.")
            self._update_rejected_list()
            return
        path = self.blink_paths[self.blink_index]
        rejected = path in self.rejected
        key = self._blink_key(path)
        cached = self._blink_cache.get(key)
        if cached is None:
            try:
                cached = self._blink_picture(path, key[1])
            except Exception as exc:
                self.blink_label.set(f"{os.path.basename(path)}  unreadable: {exc}")
                return
            self._blink_store(key, cached)
        if len(cached) > 3 and cached[3] != self.stretch["blink"]:
            cached = self._blink_restretch(cached, self.stretch["blink"])   # 2.2.15: drawn with another stretch
            self._blink_cache[key] = cached
        pic, exptime = cached[0], cached[1]
        mark = "REJECTED" if rejected else "keep"
        # 2.2.17 (item 6): the frame's own number from its file name and its local time first; the list position
        # (n of N) after them.
        label, clock = self.frame_label(path), self._frame_time(path)
        self.blink_label.set(
            f"Frame {label}" + (f"  {clock}" if clock else "") + f"   {mark}   ({self.blink_index + 1} of "
            f"{len(self.blink_paths)} in the list, {len(self._rejected_in_list())} rejected)   "
            f"{os.path.basename(path)}  exp {exptime:.1f}s"
        )
        self._update_rejected_list()
        if self.blink_canvas is None:
            return
        artist = self._blink_artist
        if artist is None or artist.axes is not self.blink_ax or artist.get_array().shape != pic.shape:
            self.blink_ax.clear()
            self._blink_artist = self.blink_ax.imshow(pic, cmap="gray", origin="upper", vmin=0, vmax=255,
                                                      interpolation="nearest")
            self.blink_ax.set_xticks([])
            self.blink_ax.set_yticks([])
        else:
            artist.set_data(pic)
        # 2.2.5: a fixed band in pixels above the image for the title, worked out for the figure's size right now, so
        # the title is never clipped (tight_layout ran only on the first frame, before the window had its real size).
        self._blink_layout()
        head = f"Frame {label}" + (f"  {clock}" if clock else "")
        self.blink_ax.set_title(f"REJECTED — {head}" if rejected else f"{head}   {os.path.basename(path)}",
                                color="#c62828" if rejected else "#1a1a1a",
                                fontweight="bold" if rejected else "normal", fontsize=13 if rejected else 10)
        self.blink_canvas.draw_idle()

    def _blink_layout(self):
        fig = getattr(self, "blink_fig", None)
        if fig is None:
            return
        h = max(fig.get_figheight() * fig.dpi, 60.0)
        w = max(fig.get_figwidth() * fig.dpi, 60.0)
        pt = fig.dpi / 72.0          # pixels per point: follows Windows display scaling (125%, 150% …)
        top = 1.0 - min(0.4, 26.0 * pt / h)
        bottom = min(0.2, 4.0 * pt / h)
        side = min(0.2, 4.0 * pt / w)
        fig.subplots_adjust(left=side, right=1.0 - side, bottom=bottom, top=top)

    # ---- 2.2.15 (GitHub #1): display stretch controls (Blink and Photometry) ---------------------------
    @staticmethod
    def _nearest_step(steps, value) -> int:
        return min(range(len(steps)), key=lambda k: abs(steps[k] - float(value)))

    @staticmethod
    def _pct_text(value) -> str:
        return f"{float(value):g}%"

    def _layout_view_box(self, force: bool = False):
        """2.2.18 (item 23): the Photometry page's View box at any window width. Wide: beside the measurement
        settings, switches on one line. Narrower: still beside them, the switches wrapped onto two (or more) rows.
        Narrower still: the View box moves to its own line under the settings (and wraps there if it must). Before, the
        row was packed in one line and the View box, packed last, was cut off at half-screen width."""
        switches = [b for b in getattr(self, "_view_switches", [])
                    if b is not self.candidates_only_box or self.mode_key == "discovery"]
        options, box, line2 = getattr(self, "_view_options", None), getattr(self, "_view_box", None), \
            getattr(self, "_view_line2", None)
        if options is None or box is None:
            return
        scale = ui_scale(self)
        gap = int(10 * scale)
        def px(value, default=0):
            try:
                return int(value)
            except Exception:
                return default
        width = px(options.winfo_width())
        sw = [px(b.winfo_reqwidth(), 100) + gap for b in switches]
        try:
            others = [px(c.winfo_reqwidth()) for c in box.winfo_children() if c is not self._view_grid]
        except Exception:
            others = []
        box_pad = px(box.winfo_reqwidth()) - max([px(self._view_grid.winfo_reqwidth())] + others)
        left = 0
        try:
            for w in options.pack_slaves():
                if w is box or w is line2:
                    continue
                left += px(w.winfo_reqwidth()) + _pad_total(w.pack_info().get("padx", 0))
        except Exception:
            left = 0
        box_pad = max(box_pad, int(20 * scale))
        if width <= 1:
            avail, own = None, False   # not laid out yet: one line, beside the settings
        else:
            one_line = max([sum(sw)] + others) + box_pad
            least = max([max(sw or [0])] + others) + box_pad
            room = width - left - gap
            own = room < least
            avail = (width - box_pad - gap if own else room - box_pad) if (own or room < one_line) else None
        rows = flow_rows(sw, avail)
        key = (own, tuple(tuple(r) for r in rows), len(switches))
        if key == self._view_layout_key and not force:
            return
        self._view_layout_key = key
        for b in getattr(self, "_view_switches", []):
            try:
                b.grid_forget()
            except Exception:
                pass
        for r, row in enumerate(rows):
            for c, i in enumerate(row):
                switches[i].grid(row=r, column=c, sticky=W, padx=(0, gap))
        if own != self._view_own_line or force:
            self._view_own_line = own
            try:
                box.pack_forget()
                line2.pack_forget()
                if own:
                    first = next(iter(w for w in options.pack_slaves() if w is not box and w is not line2), None)
                    if first is not None:
                        line2.pack(side="bottom", fill=X, pady=(4, 0), before=first)
                    else:
                        line2.pack(side="bottom", fill=X, pady=(4, 0))
                    box.pack(in_=line2, side=RIGHT)
                else:
                    box.pack(in_=options, side=RIGHT, padx=(10, 0))
            except Exception:
                pass

    def _stretch_controls(self, parent, page: str):
        """A compact 'Stretch' row: linear / log / asinh, black and white points (percentiles of the image), Reset.
        Display only: the measurements never see it."""
        s = self.stretch[page]
        box = ttk.Frame(parent, style="Card.TFrame")
        mode = StringVar(value=s["mode"])
        black = DoubleVar(value=self._nearest_step(STRETCH_BLACK_STEPS, s["black"]))
        white = DoubleVar(value=self._nearest_step(STRETCH_WHITE_STEPS, s["white"]))
        black_text = StringVar(value=self._pct_text(s["black"]))
        white_text = StringVar(value=self._pct_text(s["white"]))
        self._stretch_vars[page] = {"mode": mode, "black": black, "white": white,
                                    "black_text": black_text, "white_text": white_text}
        try:
            from tkinter import font as tkfont
            length = int(tkfont.Font(root=parent, family=core.UI_FONT, size=10).measure("n" * 9))
        except Exception:
            length = 110
        length = max(80, length)
        ttk.Label(box, text="Stretch", style="Card.TLabel").pack(side=LEFT, padx=(0, 4))
        combo = ttk.Combobox(box, textvariable=mode, values=core.STRETCH_MODES, width=7, state="readonly")
        combo.pack(side=LEFT)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._stretch_moved(page))
        ttk.Label(box, text="black", style="Card.TLabel").pack(side=LEFT, padx=(10, 2))
        ttk.Scale(box, from_=0, to=len(STRETCH_BLACK_STEPS) - 1, variable=black, orient="horizontal", length=length,
                  command=lambda _v: self._stretch_moved(page, "black")).pack(side=LEFT)
        ttk.Label(box, textvariable=black_text, style="Hint.TLabel", width=6).pack(side=LEFT, padx=(2, 0))
        ttk.Label(box, text="white", style="Card.TLabel").pack(side=LEFT, padx=(8, 2))
        ttk.Scale(box, from_=0, to=len(STRETCH_WHITE_STEPS) - 1, variable=white, orient="horizontal", length=length,
                  command=lambda _v: self._stretch_moved(page, "white")).pack(side=LEFT)
        ttk.Label(box, textvariable=white_text, style="Hint.TLabel", width=6).pack(side=LEFT, padx=(2, 0))
        ttk.Button(box, text="Reset", command=lambda: self._stretch_reset(page)).pack(side=LEFT, padx=(8, 0))
        return box

    def _stretch_from_vars(self, page: str, moved: str | None = None) -> dict:
        v = self._stretch_vars[page]
        cur = dict((getattr(self, "_pending_stretch", {}) or {}).get(page) or self.stretch[page])
        cur["mode"] = v["mode"].get() if v["mode"].get() in core.STRETCH_MODES else cur["mode"]
        if moved == "black":
            k = int(round(float(v["black"].get())))
            cur["black"] = STRETCH_BLACK_STEPS[min(max(k, 0), len(STRETCH_BLACK_STEPS) - 1)]
        if moved == "white":
            k = int(round(float(v["white"].get())))
            cur["white"] = STRETCH_WHITE_STEPS[min(max(k, 0), len(STRETCH_WHITE_STEPS) - 1)]
        return cur

    def _stretch_moved(self, page: str, moved: str | None = None):
        """A slider or the mode box moved: show the new numbers now, redraw once the user pauses (debounced)."""
        if page not in self._stretch_vars:
            return
        new = self._stretch_from_vars(page, moved)
        v = self._stretch_vars[page]
        v["black_text"].set(self._pct_text(new["black"]))
        v["white_text"].set(self._pct_text(new["white"]))
        self._pending_stretch = getattr(self, "_pending_stretch", {})
        self._pending_stretch[page] = new
        job = self._stretch_jobs.get(page)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._stretch_jobs[page] = self.after(STRETCH_DEBOUNCE_MS, lambda: self._apply_stretch(page))

    def _stretch_reset(self, page: str):
        v = self._stretch_vars.get(page)
        d = dict(core.STRETCH_DEFAULT)
        if v is not None:
            v["mode"].set(d["mode"])
            v["black"].set(self._nearest_step(STRETCH_BLACK_STEPS, d["black"]))
            v["white"].set(self._nearest_step(STRETCH_WHITE_STEPS, d["white"]))
            v["black_text"].set(self._pct_text(d["black"]))
            v["white_text"].set(self._pct_text(d["white"]))
        self._pending_stretch = getattr(self, "_pending_stretch", {})
        self._pending_stretch[page] = d
        self._apply_stretch(page)

    def _apply_stretch(self, page: str, stretch: dict | None = None):
        """Use a new display stretch on one page and remember it in the settings."""
        self._stretch_jobs[page] = None
        if stretch is None:
            stretch = (getattr(self, "_pending_stretch", {}) or {}).pop(page, None)
        if stretch is None:
            return
        stretch = core.stretch_settings(stretch)
        if stretch["black"] >= stretch["white"]:
            return
        if stretch == self.stretch[page]:
            return
        self.stretch[page] = stretch   # a new dict: the Blink reader thread may hold the old one
        self.settings[f"stretch_{page}"] = dict(stretch)
        self._save_settings()
        if page == "blink":
            if self.blink_paths:
                self._blink_stretch_changed()
        elif self.preview is not None:
            self.refresh_preview()

    def _page_calibrate(self):
        page = ttk.Frame(self.pages)
        card = ttk.Frame(page, style="Card.TFrame", padding=16)
        card.pack(fill=BOTH, expand=True)
        ttk.Label(card, text="Calibrate", font=(core.UI_FONT, 16, "bold"), style="Card.TLabel").pack(anchor=W)
        ttk.Label(
            card,
            text="Master bias, bias-subtracted master dark scaled by exposure, normalized master flat. Each light is then "
                 "(light − bias − dark) / flat. The first light is then debayered with the channel below and shown on the "
                 "Photometry page; every light is calibrated and debayered the same way during the run.",
            style="Hint.TLabel",
            wraplength=1100, justify=LEFT,
        ).pack(anchor=W, pady=(2, 8))
        cam = self._group(card, "Camera and channel")
        cam.pack(fill=X, pady=(0, 8))
        row = ttk.Frame(cam, style="Card.TFrame")
        row.pack(fill=X)
        ttk.Label(row, text="Bayer pattern", style="Card.TLabel").pack(side=LEFT)
        pattern_box = ttk.Combobox(row, textvariable=self.pattern, values=("RGGB", "BGGR", "GRBG", "GBRG", "MONO"),
                                   width=10, state="readonly")
        pattern_box.pack(side=LEFT, padx=8)
        pattern_box.bind("<<ComboboxSelected>>", lambda _e: setattr(self, "_pattern_user_set", True))
        # 2.2.11: Channel for a colour camera; on MONO the same place holds Filter (there is nothing to debayer).
        self.channel_label = ttk.Label(row, text="Channel", style="Card.TLabel")
        self.channel_label.pack(side=LEFT, padx=(12, 0))
        self.channel_box = ttk.Combobox(row, textvariable=self.debayer_mode, values=("green", "red", "blue", "luminance"),
                                        width=12, state="readonly")
        self.channel_box.pack(side=LEFT, padx=8)
        self.filter_box = ttk.Combobox(row, textvariable=self.mono_filter_choice, values=list(core.MONO_FILTER_CHOICES),
                                       width=18, state="readonly")
        self.filter_box.bind("<<ComboboxSelected>>", lambda _e: self._mono_filter_picked())
        self.apply_button = ttk.Button(row, text="Apply channel to the preview", command=self.apply_debayer)
        self.apply_button.pack(side=LEFT, padx=8)
        self.filter_hint_label = ttk.Label(row, textvariable=self.filter_hint, style="Hint.TLabel", justify=LEFT)
        self.filter_hint_label.pack(side=LEFT, padx=(12, 0))
        ttk.Label(
            cam,
            text="Green is the normal choice (AAVSO TG, V comps). Red (TR, R comps) suits a red dwarf; blue is TB; luminance "
                 "is clear, reported as CV. MONO is for monochrome cameras (MicroObservatory and others): no debayer, and "
                 "Channel becomes Filter (from the FITS FILTER keyword, or set it here). The pattern is read from the "
                 "FITS header when the camera writes it; without one, a raw flat with no colour mask sets MONO.",
            style="Hint.TLabel", wraplength=1100, justify=LEFT,
        ).pack(anchor=W, pady=(6, 0))
        cal_row = ttk.Frame(card, style="Card.TFrame")
        cal_row.pack(anchor=W)
        ttk.Button(cal_row, text="Build masters and calibrate", style="Accent.TButton",
                   command=self.run_calibration).pack(side=LEFT)
        ttk.Button(cal_row, text="Cancel", command=self.cancel_job).pack(side=LEFT, padx=8)
        # 2.2.8: masters are saved beside their raw frames and reused; this forces a fresh stack.
        self.rebuild_masters = BooleanVar(value=False)
        ttk.Checkbutton(cal_row, text="Rebuild masters (ignore saved ones)", variable=self.rebuild_masters).pack(
            side=LEFT, padx=(4, 8))
        self.debayer_info = StringVar(value="No preview yet.")
        ttk.Label(cal_row, textvariable=self.debayer_info, style="Hint.TLabel").pack(side=LEFT, padx=(12, 0))
        self.log_box = Text(card, height=18, wrap="word", font=(core.MONO_FONT, 10), bg="#1e1e1e", fg="#dcdcdc",
                            insertbackground="#dcdcdc")
        self.log_box.pack(fill=BOTH, expand=True, pady=(10, 0))
        nav = ttk.Frame(card, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", before=self.log_box, pady=(10, 0))
        ttk.Button(nav, text="Back", command=lambda: self.show_step(STEP_BLINK)).pack(side=LEFT)
        ttk.Button(nav, text="Next: Photometry", command=lambda: self.show_step(STEP_PHOTO)).pack(side=RIGHT)
        return page

    def _calibration_checks(self, bias, dark, flat, lights):
        """Before building masters: darks far from the lights' exposure, and flats taken at another rotator angle.
        Returns the dark list to use (possibly empty), or None to stop."""
        try:
            light_header = core.read_header(lights[0])
        except Exception:
            return dark
        def exposure(h):
            # header_exptime falls back to 1 s when the header has none; that must not look like a mismatch.
            return core.header_exptime(h) if any(k in h for k in ("EXPTIME", "EXPOSURE", "EXP_TIME")) else None

        light_exp = exposure(light_header)
        if dark:
            try:
                dark_exp = exposure(core.read_header(dark[0]))
            except Exception:
                dark_exp = None
            problem = core.dark_scale_problem(dark_exp, light_exp)
            if problem:
                folder = os.path.basename(os.path.normpath(self.dark_dir.get())) or "the Dark folder"
                answer = messagebox.askyesnocancel(
                    APP_TITLE,
                    f"Dark folder '{folder}':\n\n{problem}\n\n"
                    "Yes: calibrate without the dark this time (bias only). A cooled camera's dark current is usually "
                    "tiny, and the bias still removes the pedestal.\n"
                    "No: use these darks anyway.\n"
                    "Cancel: stop, and pick darks closer to the lights' exposure on the Input page.")
                if answer is None:
                    self.status.set("Calibration not started: choose a dark folder that matches the lights.")
                    return None
                if answer:
                    self.log(f"Darks left out: {dark_exp:g} s darks for {light_exp:g} s lights would be scaled "
                             f"x{light_exp / dark_exp:.3g}. Bias only for this run.")
                    dark = []
                    if not bias:
                        self.log("  Note: there are no bias frames either, so the pedestal stays in; the sky annulus "
                                 "removes it in photometry, but the flat is a little less exact.")
                else:
                    self.log(f"Darks scaled x{light_exp / dark_exp:.3g} ({dark_exp:g} s to {light_exp:g} s), as chosen.")
        light_rot = core.header_rotator(light_header)
        # 2.2.18 (item 8): on an alt-az mount the readings are compared without the flip (core.flat_angle_difference)
        minfo = self._mount_info(lights) if flat and light_rot is not None else {"mount": core.MOUNT_GEM, "detected": {}}
        mount = minfo["mount"]
        altaz = mount == core.MOUNT_ALTAZ
        derotated = altaz and bool((minfo.get("detected") or {}).get("derotator"))
        if flat and light_rot is not None and derotated:
            # review: a derotated alt-az night: one log caution at most, never a question (core.DEROTATOR_FLAT_NOTE)
            flat_rots = []
            for path in flat[::max(1, len(flat) // 12)]:
                try:
                    r = core.header_rotator(core.read_header(path))
                except Exception:
                    r = None
                if r is not None:
                    flat_rots.append(r)
            flat_rot = core.typical_rotator(flat_rots, mount)
            if flat_rot is not None and core.flat_angle_difference(flat_rot, light_rot, mount) > \
                    core.flat_angle_tolerance(mount):
                self.log(f"Caution: flats taken with the derotator at sky angle {flat_rot:g}°, lights at "
                         f"{light_rot:g}°. That is fine: {core.DEROTATOR_FLAT_NOTE}.")
            return dark
        if flat and light_rot is not None:
            # 2.2.3: look at every flat, not just the first: a Flat folder can hold sets from two camera angles.
            flat_rots = []
            step = max(1, len(flat) // 12)   # a spread sample is enough, and keeps the window responsive
            for path in flat[::step]:
                try:
                    r = core.header_rotator(core.read_header(path))
                except Exception:
                    r = None
                if r is not None:
                    flat_rots.append(r)
            flat_rot = core.typical_rotator(flat_rots, mount)
            spread = core.rotator_spread(flat_rots, mount)
            if spread is not None and spread > core.flat_angle_tolerance(mount):
                if not messagebox.askyesno(
                        APP_TITLE,
                        f"The Flat folder mixes camera angles: {core.rotator_summary(flat_rots, mount)}.\n\n"
                        "One master flat is built from all of them, so it matches none exactly. "
                        + ("(Alt-az mount: readings are the sky angle the derotator held; small drifts are not "
                           "counted.)" if altaz else "(Readings 180° apart "
                           "are the same camera position across a meridian flip and are not counted as different.)")
                        + "\n\nCalibrate with these flats anyway? (No: stop, so you can keep only the flats taken at "
                        "the lights' angle.)"):
                    self.status.set("Calibration not started: the flats were taken at more than one camera angle.")
                    return None
                self.log(f"Flats at several camera angles ({core.rotator_summary(flat_rots, mount)}): used anyway, as "
                         "chosen.")
            if flat_rot is not None and core.flat_angle_difference(flat_rot, light_rot, mount) > \
                    core.flat_angle_tolerance(mount):
                if altaz:
                    # 2.2.18 (item 8): alt-az without a derotator: a mechanical camera angle, no flip (modulo 360°).
                    text = (f"The flats were taken at rotator angle {flat_rot:g}° and the lights at {light_rot:g}° "
                            "(alt-az mount, so no meridian flip explains it).\n\n"
                            f"The camera sat turned on the telescope by about "
                            f"{core.angle_difference(flat_rot, light_rot):.0f}°, so dust shadows and vignetting from "
                            "the optics in front of the rotator fall elsewhere on the sensor than in the lights. "
                            "Errors of about 1% are possible (enough to fake or hide a shallow transit).\n\n"
                            "Calibrate with these flats anyway? (No: stop, so you can pick flats taken at the lights' "
                            "angle, or clear the Flat folder.)")
                else:
                    text = (f"The flats were taken at rotator angle {flat_rot:g}° and the lights at {light_rot:g}°.\n\n"
                            "Vignetting and dust shadows from the optics in front of the rotator turn with the camera, "
                            "so a flat taken at another angle corrects the wrong pattern. Errors of about 1% are "
                            "typical, more when stars move on the sensor during the night (enough to fake or hide a "
                            "shallow transit).\n\n"
                            "Calibrate with these flats anyway? (No: stop, so you can pick flats taken at the lights' "
                            "angle, or clear the Flat folder.)")
                if not messagebox.askyesno(APP_TITLE, text):
                    self.status.set("Calibration not started: flats and lights at different rotator angles.")
                    return None
                self.log(f"Flats at rotator {flat_rot:g}°, lights at {light_rot:g}°: used anyway, as chosen.")
        return dark

    @staticmethod
    def _rotator_survey(lights, mount: str | None = None) -> tuple[str, str]:
        """Rotator readings through the night: (meridian flips, real rotator moves), each '' when there were none.
        A reading that changes by 180° is a meridian flip (same camera position on the telescope); any other change
        is the camera turning on the telescope. 2.2.18 (item 8): on an alt-az mount there is no flip: every change
        over 2° between frames is a move (a derotator's smooth drift stays far below that)."""
        flips, moves = [], []
        prev = prev_pier = None
        for k, path in enumerate(lights, 1):
            try:
                header = core.read_header(path)
            except Exception:
                continue
            rot = core.header_rotator(header)
            if rot is None:
                continue
            pier = str(header.get("PIERSIDE") or "").strip().upper() or None
            if prev is not None and core.angle_difference(prev, rot) > 2.0:
                flip = core.rotator_difference(prev, rot) <= 2.0 and mount != core.MOUNT_ALTAZ
                if flip and pier is not None and prev_pier is not None and pier == prev_pier:
                    # Same side of the pier: the camera itself turned 180°, it was not a meridian flip.
                    flip = False
                # 2.2.17 (item 6): the frame's own number from its file name (as on Blink), not its list position
                (flips if flip else moves).append(f"before frame {core.frame_label(path)} ({prev:g}° to {rot:g}°)")
            prev, prev_pier = rot, pier
        return "; ".join(flips), "; ".join(moves)

    def run_calibration(self):
        # 2.2.8: a calibration entry is a folder of raw frames (SHOBS-P masters in it are not frames) or a master file.
        bias, bias_file = core.calibration_source(self.bias_dir.get())
        dark, dark_file = core.calibration_source(self.dark_dir.get())
        flat, flat_file = core.calibration_source(self.flat_dir.get())
        flat = [path for path in flat if path not in self.rejected]
        files = {"bias": bias_file, "dark": dark_file, "flat": flat_file}
        for kind, path in files.items():
            if path:
                problem = core.check_master_file(path, kind)
                if problem:
                    self._calibration_failed(problem)   # 2.2.18 (item 22): the normal failure path, into the log
                    return
        lights = self._lights(log_skips=True)
        unreadable = []
        for key, var, found in (("Bias", self.bias_dir, bias or bias_file), ("Dark", self.dark_dir, dark or dark_file),
                                ("Flat", self.flat_dir, flat or flat_file), ("Lights", self.light_dir, lights)):
            other = core.unreadable_files(var.get()) if var.get().strip() and not found else []
            if other:
                unreadable.append(f"{key}: {len(other)} file(s) such as {other[0]}")
        if unreadable:
            text = ("These folders hold files SHOBS-P cannot read (it reads FITS and XISF):\n  • "
                    + "\n  • ".join(unreadable))
            if not lights:
                messagebox.showerror(APP_TITLE, text + "\n\nNo light frames can be read.")
                return
            if not messagebox.askyesno(APP_TITLE, text + "\n\nThose steps would be skipped. Calibrate anyway?"):
                return
        if not lights:
            messagebox.showerror(APP_TITLE, "No light frames found.")
            return
        # Flats already calibrated by other software (PixInsight's _c files, floating-point masters) are used as
        # they are: subtracting the bias again would wreck them.
        flats_calibrated = False
        if flat:
            try:
                flat_header = core.read_header(flat[0])
                flat_signs = core.precalibration_signs(flat[0], flat_header)
            except Exception:
                flat_header, flat_signs = {}, []
            # A file name alone is not enough for flats: also floating-point pixels or a calibration history.
            header_says = any(not sign.startswith("file name") for sign in flat_signs)
            if flat_signs and header_says:
                flats_calibrated = True
                self.log("Flats look already calibrated (" + "; ".join(flat_signs) + "): used as they are, without "
                         "subtracting bias or dark again.")
        try:
            binning = max(int(self.binning.get()), 1)
        except Exception:
            binning = 1
        # Already-calibrated lights must not be calibrated a second time.
        try:
            first_header = core.read_header(lights[0])
        except Exception:
            first_header = {}
        # 2.2.18 (item 1): colour images (three channels) found here too, when the folder was typed, not scanned.
        rgb = core.is_rgb_header(first_header)
        if rgb and not (self._rgb_channel and self._rgb_lights_dir == self.light_dir.get().strip()):
            self._setup_rgb_lights(first_header, len(lights))
            self._read_mono_filter(first_header)
        elif not rgb and self._rgb_channel:
            self._leave_rgb_lights()
            self._read_mono_filter(first_header)
        try:
            signs = core.precalibration_signs(lights[0], core.read_fits(lights[0])[1])
        except Exception:
            signs = []
        if rgb:
            signs = signs + ["three color channels: already debayered by other software"]
        self.precalibrated = bool(signs)
        if signs and rgb:
            # 2.2.18 (item 1): a debayered image cannot take a raw bias, dark or flat: always skipped, no question.
            if bias or dark or flat or any(files.values()):
                self.log("These lights are color images (already debayered and calibrated): the bias, dark and flat "
                         "entries are not used for them.")
            bias, dark, flat = [], [], []
            files = {"bias": None, "dark": None, "flat": None}
        elif signs:
            reasons = "\n  • ".join(signs)
            if bias or dark or flat or any(files.values()):
                skip = messagebox.askyesno(
                    APP_TITLE,
                    "These lights look already calibrated:\n  • " + reasons + "\n\n"
                    "Calibrating them again subtracts the bias and dark twice and divides by the flat twice.\n\n"
                    "Skip bias, dark, and flat for this run? (Recommended)\n"
                    "The raw saturation level no longer applies (these values are not camera counts); saturation is "
                    "judged from flat-topped star cores instead.",
                )
                if skip:
                    bias, dark, flat = [], [], []
                    files = {"bias": None, "dark": None, "flat": None}
                else:
                    self.precalibrated = False
        if not self.precalibrated:
            # The checks read headers: a chosen master file stands in for its frames (it carries their keywords).
            checked = self._calibration_checks(bias or ([bias_file] if files["bias"] else []),
                                               dark or ([dark_file] if files["dark"] else []),
                                               flat or ([flat_file] if files["flat"] else []), lights)
            if checked is None:
                return
            if not checked and (dark or files["dark"]):
                dark, files["dark"] = [], None   # darks left out (exposure far from the lights')
            elif dark:
                dark = checked
        if not self._start_job("Building calibration masters…"):
            return
        if self.precalibrated:
            self.log("Lights are pre-calibrated: no bias, dark, or flat applied; saturation judged from flat-topped "
                     "star cores, not the raw level. ("
                     + "; ".join(signs) + ")")
        missing = [name for name, paths in (("bias", bias), ("dark", dark), ("flat", flat))
                   if not paths and not files.get(name)]
        if missing:
            self.log("Note: no " + ", ".join(missing) + " frames. That step is skipped.")
        self.log("Building calibration masters. The window stays live; this can take several minutes for 100 full frames.")

        cancel = self._cancel
        rebuild = bool(self.rebuild_masters.get())
        _minfo = self._mount_info(lights)   # 2.2.18 (item 8): read on the UI thread, used in the worker
        mount = _minfo["mount"]
        derotated = mount == core.MOUNT_ALTAZ and bool(_minfo["detected"].get("derotator"))

        def progress_log(text):
            if cancel.is_set():
                raise RuntimeError("Cancelled")
            self.call_ui(self.log, text)

        def work():
            try:
                masters = core.prepare_masters(bias, dark, flat, binning, progress_log, flats_calibrated=flats_calibrated,
                                               files=files, save=True, rebuild=rebuild, version=APP_VERSION)
                path = lights[0]
                data, header = core.read_fits(path)
                data = core.bin_image(data, binning)
                # 2.2.8: a saved or chosen master must match the lights before it is used.
                problems, cautions = [], []
                for kind in ("bias", "dark", "flat"):
                    arr = getattr(masters, kind)
                    if arr is None:
                        continue
                    errs, warns = core.master_mismatches(masters.headers.get(kind) or {}, kind, header, data.shape,
                                                         arr.shape, binning, mount=mount,
                                                         derotator=derotated)
                    problems += errs
                    cautions += warns
                if problems:
                    raise ValueError("The masters do not fit these lights: " + "; ".join(problems)
                                     + ". Pick the matching frames, or tick Rebuild masters.")
                for text in cautions:
                    self.call_ui(self.log, "Caution: " + text + ".")
                calibrated = core.calibrate_frame(data, header, masters)
                flips, moves = self._rotator_survey(lights, mount) if len(lights) > 1 else ("", "")
                if flips:
                    self.call_ui(self.log, f"Meridian flip {flips} (the rotator reading changed by 180°). The camera "
                                           "keeps its place on the telescope, so the same flats fit both sides; the "
                                           "field turns 180° on the sensor, so each star can show a small step at the "
                                           "flip. (If your rotator really turned the camera 180°, the flats fit only "
                                           "one side.)")
                if moves:
                    self.call_ui(self.log, f"Note: the camera rotator moved during these lights: {moves}. One set of "
                                           "flats can only match one angle; expect steps in the light curve there.")
                self.call_ui(self._calibration_done, masters, calibrated, header, path, binning, None)
            except Exception as exc:
                self.call_ui(self._calibration_done, None, None, None, None, binning, (str(exc), traceback.format_exc()))

        self._blink_worker = threading.Thread(target=work, daemon=True)
        self._blink_worker.start()

    def _calibration_done(self, masters, calibrated, header, path, binning, error):
        self._end_job()
        if error is not None and error[0] == "Cancelled":
            self.log("Calibration cancelled. The old masters (if any) are unchanged.")
            return
        if error is not None:
            message, detail = error
            self._calibration_failed(message, detail)
            return
        self.masters = masters
        self.masters_binning = binning
        self.calibrated = calibrated
        self.preview = calibrated
        self._photo_disp = None   # 2.2.15: the stretched copy belongs to the old image
        self._preview_token = getattr(self, "_preview_token", 0) + 1   # 2.2.13: a new image -> Fit view
        self._seeing_cache = None   # 2.2.8
        self.preview_header = header
        self.preview_path = path
        self.preview_is_debayered = False
        self.chart_placed = []
        finite = calibrated[np.isfinite(calibrated)]
        self.log("Masters ready: " + ", ".join(masters.notes or ["no calibration frames"]))
        self.log(
            f"Calibrated preview: {os.path.basename(path)}  shape {calibrated.shape}  "
            f"median {np.median(finite) if finite.size else float('nan'):.1f}  NaN px {calibrated.size - finite.size}"
        )
        pat = core.header_bayer(header or {})
        if pat and not self._rgb_channel:   # 2.2.18 (item 1): never on colour images (said at Scan/Calibrate)
            self.pattern.set(pat)
        elif core.is_microobservatory(path, header or {}) and self.pattern.get() != core.MONO:
            self._setup_microobservatory(header or {}, len(self._lights()))
        try:
            camera_bin = int(float((header or {}).get("XBINNING", 1)))
        except (TypeError, ValueError):
            camera_bin = 1
        if camera_bin > 1:
            self.log(f"Note: the camera binned these frames (XBINNING={camera_bin}). Some capture programs bin a color "
                     "sensor by mixing neighboring red, green, and blue pixels. If the channels look identical or B-V "
                     "sits exactly on the comps' color, capture unbinned and bin here instead.")
        self._check_lights_pointing(header or {})
        self._read_mono_filter(header or {})
        self._check_target_field(header or {})
        if core.header_wcs(header or {}) is not None:
            self.log("The lights carry a plate solution in their FITS headers: Solve uses it directly.")
        # 2.2: no separate Debayer step. Debayer the preview now and go straight to Photometry.
        self.apply_debayer(auto=True)

    def _calibration_failed(self, message: str, detail: str = ""):
        """2.2.18 (item 22): every calibration failure (a worker error, or a bad Master file… choice found before the
        run) is logged and shown the same way."""
        self.log(f"Calibration failed: {message}")
        if detail:
            self.log(detail)
        messagebox.showerror(APP_TITLE, f"Calibration failed:\n{message}")

    def apply_debayer(self, auto: bool = False):
        if self.calibrated is None:
            messagebox.showinfo(APP_TITLE, "Build masters and calibrate first (Calibrate page).")
            return
        try:
            # Always debayer the calibrated mosaic, so changing the channel and applying again is safe.
            mono = core.debayer(self.calibrated, self.pattern.get(), self.debayer_mode.get())
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        # The shape of the image the markers were placed on (not self.preview, which already holds the new
        # calibrated mosaic here).
        old_shape = getattr(self, "_marks_shape", None)
        self.preview = mono
        self._photo_disp = None   # 2.2.15: the stretched copy belongs to the old image
        self._preview_token = getattr(self, "_preview_token", 0) + 1
        self._seeing_cache = None   # 2.2.8
        self.preview_is_debayered = True
        if old_shape is not None and tuple(old_shape[:2]) != tuple(mono.shape[:2]):
            self._rescale_marks(old_shape, mono.shape)
        self._marks_shape = tuple(mono.shape[:2])
        self.pick_mode.set("watch" if self.mode_key == "discovery" else "target")
        self._pick_user_chosen = False   # 2.2.18 (item 7): a new image; the user has not picked a step on it yet
        self._pick_auto_advanced = False
        self.chart_placed = []
        self.chart_solution = None
        self.suggested_comps = []
        self._seq_names = set()
        self._photo_view = None
        self._photo_drawn_shape = None
        channel = "monochrome" if self.pattern.get() == core.MONO else f"{self.debayer_mode.get()} from {self.pattern.get()}"
        if self._rgb_channel and self.pattern.get() == core.MONO:   # 2.2.18 (item 1)
            channel = f"{core.RGB_CHANNEL_WORDS[self._rgb_channel]} channel of the color images"
        self.debayer_info.set(f"Preview: {channel}  →  {mono.shape[1]}×{mono.shape[0]}"
                              + ("" if self.pattern.get() == core.MONO else " (superpixel)"))
        self.log(f"Preview debayered: {channel}, {mono.shape[1]}×{mono.shape[0]} px. Star positions are on this image.")
        self.refresh_preview()
        self.show_step(STEP_PHOTO)   # in Discovery this starts the field solve (2.2.9: show_step)
        if self.mode_key == "discovery":
            self.status.set("Calibrated and debayered. Solving the field…")
        else:
            self.status.set("Calibrated and debayered. The image is being solved; click the target and the comparison stars "
                            "(their IDs and magnitudes fill in from the catalogs).")

    def _page_photo(self):
        page = ttk.Frame(self.pages)
        card = ttk.Frame(page, style="Card.TFrame", padding=12)
        card.pack(fill=BOTH, expand=True)
        self.photo_title = StringVar(value="Photometry")
        ttk.Label(card, textvariable=self.photo_title, font=(core.UI_FONT, 16, "bold"), style="Card.TLabel").pack(anchor=W)
        self.photo_hint = StringVar()
        hint = ttk.Label(card, textvariable=self.photo_hint, style="Hint.TLabel", wraplength=900, justify=LEFT)
        hint.pack(anchor=W, fill=X, pady=(2, 6))
        card.bind("<Configure>", lambda e: hint.configure(wraplength=max(e.width - 40, 300)), add="+")
        tools = ttk.Frame(card, style="Card.TFrame")
        tools.pack(fill=X, pady=4)
        # Variables and Transits: pick the target, comps, and check star.
        self.pick_tools = ttk.Frame(tools, style="Card.TFrame")
        for label, value in (("Target", "target"), ("Comps (C1–C10)", "comp"), ("Check", "check"), ("Watch", "watch")):
            ttk.Radiobutton(self.pick_tools, text="●  " + label, value=value, variable=self.pick_mode,
                            style=f"Pick{value}.TRadiobutton",
                            command=self._pick_chosen_by_user).pack(side=LEFT, padx=6)   # 2.2.18 (item 7)
        ttk.Button(self.pick_tools, text="Clear selected", command=self.clear_selected_mark).pack(side=LEFT, padx=(2, 6))
        # Discovery: no target; a click watches a star or leaves it out of the scan.
        self.disc_tools = ttk.Frame(tools, style="Card.TFrame")
        ttk.Label(self.disc_tools, text="Click a star to", style="Card.TLabel").pack(side=LEFT, padx=(4, 6))
        ttk.Radiobutton(self.disc_tools, text="●  Watch it", value="watch", variable=self.pick_mode,
                        style="Pickwatch.TRadiobutton").pack(side=LEFT, padx=6)
        ttk.Radiobutton(self.disc_tools, text="✕  Exclude it", value="exclude", variable=self.pick_mode,
                        style="Pickcheck.TRadiobutton").pack(side=LEFT, padx=6)
        ttk.Label(self.disc_tools, text="(right-click a marker to undo)", style="Hint.TLabel").pack(side=LEFT, padx=6)
        self.run_button = ttk.Button(tools, text="Run photometry", style="Accent.TButton", command=self.run_photometry)
        self.scan_button = ttk.Button(tools, text="Scan field", style="Accent.TButton", command=self.scan_field)
        self.run_button.pack(side=RIGHT)
        self.label_button = ttk.Button(tools, text="Solve again", command=self.label_aavso)
        self.label_button.pack(side=RIGHT, padx=6)
        ttk.Combobox(tools, textvariable=self.catalog, values=core.CATALOGS, width=15, state="readonly").pack(side=RIGHT)
        # 2.2.13: the box only chooses which catalog's circles are drawn (every catalog is fetched and merged).
        ttk.Label(tools, text="Circles", style="Card.TLabel").pack(side=RIGHT, padx=(12, 4))
        self.catalog.trace_add("write", lambda *_: self.refresh_preview() if self.chart_placed else None)
        options = ttk.Frame(card, style="Card.TFrame")
        options.pack(fill=X, pady=(0, 4))
        ttk.Label(options, text="Aperture r", style="Card.TLabel").pack(side=LEFT, padx=(6, 2))
        ttk.Entry(options, textvariable=self.radius, width=6).pack(side=LEFT)
        ttk.Label(options, text="sky in", style="Card.TLabel").pack(side=LEFT, padx=(8, 2))
        ttk.Entry(options, textvariable=self.sky_in, width=6).pack(side=LEFT)
        ttk.Label(options, text="sky out", style="Card.TLabel").pack(side=LEFT, padx=(8, 2))
        ttk.Entry(options, textvariable=self.sky_out, width=6).pack(side=LEFT)
        ttk.Button(options, text="Suggest…", command=self.suggest_apertures).pack(side=LEFT, padx=(6, 0))
        ttk.Label(options, text="raw sat ADU", style="Card.TLabel").pack(side=LEFT, padx=(14, 2))
        ttk.Entry(options, textvariable=self.sat_limit, width=8).pack(side=LEFT)
        ttk.Checkbutton(
            options, text="Also measure R and B (color index; slower)", variable=self.measure_color,
        ).pack(side=LEFT, padx=14)
        # 2.2.9: everything that changes what the image shows sits in one "View" box on the right, under the
        # Catalog / Solve row (John); the row on the left keeps the measurement settings.
        view = ttk.LabelFrame(options, text="View", style="Group.TLabelframe", padding=(8, 2, 8, 4))
        view.pack(side=RIGHT, padx=(10, 0))
        # 2.2.15 (review): the switches on one line, the display stretch (GitHub #1) on a second line under them.
        view_box, view = view, ttk.Frame(view, style="Card.TFrame")
        view.pack(side="top", anchor=W)
        # 2.2.18 (item 23): the switches are laid out by _layout_view_box (grid, wrapping onto a second row when the
        # window is narrow), not packed in one line that was cut off at half-screen width.
        self._view_switches = [
            ttk.Checkbutton(view, text="Show variables (red ○ ×)", variable=self.show_variables,
                            command=self.refresh_preview),
            ttk.Checkbutton(view, text="Show apertures", variable=self.show_apertures, command=self.refresh_preview),
            ttk.Checkbutton(view, text="Show catalog stars", variable=self.show_catalog, command=self.refresh_preview),
            ttk.Checkbutton(view, text="Show labels", variable=self.show_labels, command=self.refresh_preview)]
        # 2.2.5: Discovery's switch to see only the scan candidates and watch stars (shown in Discovery only).
        self.candidates_only_box = ttk.Checkbutton(view, text="Candidates only", variable=self.candidates_only,
                                                   command=self.refresh_preview)
        self._view_switches.append(self.candidates_only_box)
        self._view_options, self._view_box, self._view_grid = options, view_box, view
        self._view_line2 = ttk.Frame(options, style="Card.TFrame")   # the View box's own line when the row is narrow
        self._view_own_line = False
        self._view_layout_key = None
        options.bind("<Configure>", lambda _e: self._layout_view_box(), add="+")
        self._layout_view_box(force=True)
        self._stretch_controls(view_box, "photo").pack(side="top", anchor=W, pady=(4, 0))
        # 2.2.16 (item 16, GitHub #2): zoom buttons for a trackpad (no wheel to find), Fit (the old "Fit view", one
        # control), and Pop out: the image in its own resizable window. On their own line under the stretch, so the
        # View box does not get wider.
        zoom = ttk.Frame(view_box, style="Card.TFrame")
        zoom.pack(side="top", anchor=W, pady=(4, 0))
        zoom_label = ttk.Label(zoom, text="Zoom", style="Card.TLabel")
        zoom_label.pack(side=LEFT, padx=(0, 4))
        zoom_out = ttk.Button(zoom, text="−", width=3, command=lambda: self.photo_zoom_step(1.5))
        zoom_out.pack(side=LEFT)
        zoom_in = ttk.Button(zoom, text="+", width=3, command=lambda: self.photo_zoom_step(1 / 1.5))
        zoom_in.pack(side=LEFT, padx=(2, 0))
        self.view_fit_button = ttk.Button(zoom, text="Fit", command=self.reset_photo_view)
        self.view_fit_button.pack(side=LEFT, padx=(2, 0))
        # 2.2.17 (item 2): the label and − + Fit move into the pop-out while the image is out (hidden here); this
        # button then reads "Bring back".
        self._page_zoom_widgets = [(zoom_label, (0, 4)), (zoom_out, 0), (zoom_in, (2, 0)),
                                   (self.view_fit_button, (2, 0))]
        self._page_zoom_shown = True
        self.popout_button = ttk.Button(zoom, text="Pop out", command=self.photo_pop_toggle)
        self.popout_button.pack(side=LEFT, padx=(10, 0))
        # 2.2.5: redraw the aperture rings shortly after the sizes are typed (or set by Suggest…).
        for _var in (self.radius, self.sky_in, self.sky_out):
            _var.trace_add("write", lambda *_a: self._aperture_sizes_changed())

        self.pick_label = StringVar(value="No stars marked.")
        self.pick_label_widget = ttk.Label(card, textvariable=self.pick_label, style="Hint.TLabel")
        self.pick_label_widget.pack(anchor=W)
        # 2.2.15 (GitHub #3): first-time guidance, "1 Solve → 2 Target → 3 Comps → 4 Check → Run" with the current step
        # highlighted (Hide tips / Show tips, remembered); (GitHub #1) the display stretch at the right of the row.
        # Review: the step labels and Hide tips on one line, what to do now on its own line under them (wrapped to the
        # row's width); the stretch sits in the View box.
        self.step_row = ttk.Frame(card, style="Card.TFrame")
        self.step_row.pack(fill=X, pady=(2, 0))
        self.step_strip = ttk.Frame(self.step_row, style="Card.TFrame")
        self.step_line = ttk.Frame(self.step_strip, style="Card.TFrame")
        self.step_line.pack(side="top", fill=X)
        self._step_widgets = []
        self._step_names = None
        self.step_text = StringVar(value="")
        self.step_text_label = ttk.Label(self.step_strip, textvariable=self.step_text, style="Hint.TLabel",
                                         justify=LEFT)
        self.step_text_label.pack(side="top", anchor=W, fill=X)
        # 2.2.16 (item 16, GitHub #2): how to enlarge the image, for anyone who never finds wheel zoom on a trackpad.
        self.step_zoom_tip = ttk.Label(self.step_strip, text="Wheel to zoom, drag to pan. On a trackpad: the − + Fit "
                                                             "buttons in the View box, or Pop out for a bigger window.",
                                       style="Hint.TLabel", justify=LEFT)
        self.step_zoom_tip.pack(side="top", anchor=W, fill=X)
        self.step_strip.bind("<Configure>", lambda e: [lab.configure(wraplength=max(int(e.width) - 20, 200))
                                                       for lab in (self.step_text_label, self.step_zoom_tip)], add="+")
        self.step_hide_button = ttk.Button(self.step_line, text="Hide tips", command=lambda: self._set_tips(False))
        self.step_show_button = ttk.Button(self.step_row, text="Show tips", command=lambda: self._set_tips(True))
        self._place_step_strip()

        body = ttk.Frame(card, style="Card.TFrame")
        body.pack(fill=BOTH, expand=True, pady=6)
        self.photo_body = body
        panel = ttk.Frame(body, style="Card.TFrame", width=520)
        panel.pack(side=RIGHT, fill=Y, padx=(8, 0))
        panel.pack_propagate(False)
        self.photo_panel = panel   # 2.2.9: its width follows its contents (see _fit_photo_panel)
        # 2.2.8: with up to 10 comps the Stars and Watch boxes no longer fit; the whole right-hand panel scrolls
        # (scroll bar only when needed; the mouse wheel scrolls it while the pointer is over it, so the image keeps
        # its own wheel zoom). Each comp's full status lines are kept, never shortened.
        self.panel_scroll = ScrollBody(panel)
        self.panel_scroll.pack(fill=BOTH, expand=True)
        holder = self.panel_scroll.inner
        self.star_panel = ttk.Frame(holder, style="Card.TFrame")
        self._build_star_panel(self.star_panel)
        self.disc_panel = ttk.Frame(holder, style="Card.TFrame")
        self._build_discovery_panel(self.disc_panel)
        self.star_panel.pack(fill=BOTH, expand=True)
        self.after_idle(self._fit_photo_panel)
        plot_host = ttk.Frame(body, style="Card.TFrame")
        plot_host.pack(side=LEFT, fill=BOTH, expand=True)
        self.photo_plot_host = plot_host   # 2.2.16 (item 16)
        self.photo_popout = None           # 2.2.16 (item 16): the pop-out window's parts while the image is in it
        if Figure is None:
            ttk.Label(plot_host, text="matplotlib is not installed.", style="Card.TLabel").pack()
            self.photo_canvas = None
        else:
            # Symbol legend under the image: only the markers drawn right now (2.2.1).
            self.legend_row = ttk.Frame(plot_host, style="Card.TFrame")
            self.legend_row.pack(side="bottom", fill=X, pady=(2, 0))
            self.photo_fig = Figure(figsize=(8, 4.2), dpi=100, facecolor="#ffffff")
            self.photo_ax = self.photo_fig.add_subplot(111)
            self.photo_canvas = FigureCanvasTkAgg(self.photo_fig, master=plot_host)
            self.photo_canvas.get_tk_widget().configure(width=400, height=300)
            self.photo_canvas.get_tk_widget().pack(fill=BOTH, expand=True)
            self.photo_canvas.get_tk_widget().bind(
                "<Map>", lambda _e: self.after(50, self._fit_visible_canvases), add="+")
            self._connect_photo_canvas(self.photo_canvas)   # 2.2.16 (item 16): also for the pop-out's canvas
            self._photo_canvas_home = self.photo_canvas
            # 2.2.16 (item 16): what the page shows while the image is in its own window.
            self.photo_placeholder = ttk.Frame(plot_host, style="Card.TFrame")
            # 2.2.17 (review): one Bring back button on the page (the View box's Pop out button reads Bring back).
            ttk.Label(self.photo_placeholder, text="Image is in its own window (Bring back in the View box).",
                      style="Card.TLabel").pack(side=LEFT, padx=(0, 6))
        nav = ttk.Frame(card, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", before=body, pady=(6, 0))
        ttk.Button(nav, text="Back", command=lambda: self.show_step(STEP_CAL)).pack(side=LEFT)
        ttk.Button(nav, text="Next: Output", command=lambda: self.show_step(STEP_OUTPUT)).pack(side=RIGHT)
        # Live log (the same messages as the Calibrate page log) and a progress bar with an ETA.
        self.photo_logf = ttk.Frame(card, style="Card.TFrame")
        self.photo_log = Text(self.photo_logf, height=7, wrap="none", font=(core.MONO_FONT, 9), bg="#1e1e1e", fg="#dcdcdc",
                              insertbackground="#dcdcdc")
        self.photo_log.pack(fill=X)
        self.photo_logf.pack(fill=X, side="bottom", before=body, pady=(4, 0))
        self.photo_prog_row = ttk.Frame(card, style="Card.TFrame")
        self.photo_prog_row.pack(fill=X, side="bottom", before=body, pady=(4, 0))
        self.progress_bar = ttk.Progressbar(self.photo_prog_row, length=320, mode="determinate", maximum=1)
        self.progress_bar.pack(side=LEFT)
        self.progress_text = StringVar(value="Idle.")
        ttk.Label(self.photo_prog_row, textvariable=self.progress_text, style="Hint.TLabel").pack(side=LEFT, padx=8)
        self.log_toggle = ttk.Button(self.photo_prog_row, text="Hide log", command=self.toggle_photo_log)
        self.log_toggle.pack(side=RIGHT)
        ttk.Button(self.photo_prog_row, text="Cancel run", command=self.cancel_job).pack(side=RIGHT, padx=6)
        return page

    def _fit_photo_panel(self):
        """2.2.9 (John): the right-hand panel was a fixed 520 px, which does not grow with Windows display scaling, so
        at 150-200% its magnitude boxes were cut off. Size it to what the Stars / Discovery panel asks for, plus the
        scroll bar."""
        panel = getattr(self, "photo_panel", None)
        if panel is None:
            return
        try:
            inner = self.panel_scroll.inner
            inner.update_idletasks()
            need = int(inner.winfo_reqwidth()) + int(self.panel_scroll.vbar.winfo_reqwidth()) + 6
            if need > 40 and abs(need - int(panel.cget("width"))) > 2:
                # Never more than 40% of the screen, so the image keeps most of the page.
                panel.configure(width=max(min(need, int(self.winfo_screenwidth() * 0.4)), 300))
        except Exception:
            pass

    def _layout_photo_for_mode(self):
        """The Photometry page follows the mode: Discovery has no target, comps, or check star (2.2)."""
        if not hasattr(self, "run_button"):
            return
        mode = self.mode_key
        pop = getattr(self, "photo_popout", None)
        if pop is not None and pop.get("bar") is not None and getattr(self, "_popout_bar_mode", None) != mode:
            # 2.2.17 (item 2): a guard only: set_mode brings the image back before it lays the page out, so this
            # runs only if a layout change ever happens with the image out.
            self._build_popout_bar(pop["bar"])
        self.run_button.pack_forget()
        self.scan_button.pack_forget()
        self.pick_tools.pack_forget()
        self.disc_tools.pack_forget()
        self.star_panel.pack_forget()
        self.disc_panel.pack_forget()
        try:
            self.panel_scroll.canvas.yview_moveto(0.0)
        except Exception:
            pass
        self.after_idle(self._fit_photo_panel)   # Stars and Discovery panels differ in width
        self.pick_label_widget.pack_forget()
        if mode != "discovery":
            self.pick_label_widget.pack(anchor=W, before=getattr(self, "step_row", self.photo_body))
        self._layout_view_box(force=True)   # 2.2.18 (item 23): Candidates only shows in Discovery only
        if mode == "discovery":
            self.disc_tools.pack(side=LEFT)
            self.disc_panel.pack(fill=BOTH, expand=True)
            self.scan_button.pack(side=RIGHT, before=self.label_button)
            self.label_button.configure(text="Solve again")
            if self.pick_mode.get() not in ("watch", "exclude"):
                self.pick_mode.set("watch")
            if self.catalog.get() == "AAVSO sequence":
                # An AAVSO sequence only covers charted variables; a random field needs a survey catalog.
                self._catalog_before_discovery = "AAVSO sequence"
                self.catalog.set("APASS DR9")
            self.photo_title.set("Photometry: field scan")
            self.photo_hint.set(
                "The field is solved as soon as the frame is calibrated (from the plate solution in the FITS header when "
                "there is one): catalog stars are circled and known variables marked. Scan field then measures every "
                "star on every frame, puts them on the catalog's magnitude scale, and flags the ones that scatter more "
                "than stars of their brightness. Click a star to watch it or to leave it out.")
            self._update_discovery_panel()
        else:
            self.pick_tools.pack(side=LEFT)
            self.star_panel.pack(fill=BOTH, expand=True)
            if getattr(self, "_catalog_before_discovery", None):
                self.catalog.set(self._catalog_before_discovery)
                self._catalog_before_discovery = None
            self.run_button.pack(side=RIGHT, before=self.label_button)
            self.label_button.configure(text="Solve again")
            if self.pick_mode.get() == "exclude":
                self.pick_mode.set("target")
            self.photo_title.set("Photometry")
            self.photo_hint.set(
                "Click the image to mark the target, comps (C1–C10), and check star; Watch adds a star to follow. "
                "The image is solved on its own; clicking any star then fills in its ID and magnitude from every "
                "catalog (AAVSO sequence first), shown in the panel at right with its color and a variable-star "
                "check. Cyan circles are AAVSO sequence stars. Right-click a marker to clear it.")
        self._update_step_hint()

    # ---- 2.2.15 (GitHub #3): step hint on the Photometry page ----------------------------------
    def _solve_step_state(self) -> str:
        """2.2.16 (item 10): the first step's state: 'solved', 'solving', 'failed' (tried on this image, no lock or no
        catalog answer), 'impossible' (the lights do not say where they point and no RA/Dec is given), or 'todo'
        (not calibrated yet, or not tried on this image yet)."""
        if self.chart_placed:
            return "solved"
        if getattr(self, "_solving", False):
            return "solving"
        if self.preview is None or not self.preview_is_debayered:
            return "todo"
        token = getattr(self, "_preview_token", 0)
        if getattr(self, "_solve_tried_for", None) != token:
            return "todo"
        return "impossible" if getattr(self, "_solve_impossible_for", None) == token else "failed"

    def photo_steps(self):
        """The Photometry page's steps for the mode, which are done, which one is current, and what to do now:
        (names, done indexes, current index, text)."""
        solved = bool(self.chart_placed)
        solving = bool(getattr(self, "_solving", False))
        ready = self.preview is not None and self.preview_is_debayered
        state = self._solve_step_state()   # 2.2.16 (item 10)
        if self.mode_key == "discovery":
            names = ("1 Solve", "2 Scan field")
            done = {0} if solved else set()
            if solved:
                return names, done, 1, "Solved. Watch or exclude stars if you like, then Scan field."
            if not ready:
                return names, done, 0, "Calibrate first (Calibrate page); the field is solved straight away."
            if solving:
                return names, done, 0, "Solving the field…"
            if state == "impossible":
                return names, done, 0, ("Not solved: these lights do not say where they point. Type the field centre "
                                        "on the Input page, then press Solve again (top right).")
            if state == "todo":
                return names, done, 0, "The field is solved on its own when this page opens (or press Solve again)."
            return names, done, 0, ("Not solved: check the field centre on the Input page and the Optics box, then "
                                    "press Solve again (top right).")
        names = ("1 Solve", "2 Target", "3 Comps", "4 Check", "Run")
        target = self.target_xy is not None
        comps = bool(self._comp_roles_marked())
        check = self.check_xy is not None
        done = {k for k, flag in enumerate((solved, target, comps, check)) if flag}
        not_solved = "" if solved else " (Not solved: type their magnitudes.)"
        if not ready:
            return names, done, 0, "Calibrate first (Calibrate page); the image is solved straight away."
        if not solved and not target:
            if solving:
                return names, done, 0, "Solving the image… You can click the target meanwhile (Target)."
            # 2.2.16 (item 10): failed -> point at the Solve again button; no solve possible -> click the target.
            if state == "impossible":
                return names, done, 0, ("Not solved: these lights do not say where they point and no RA/Dec is given: "
                                        "click the target on the image (Target), then the comps; or enter the target "
                                        "on the Input page and press Solve again.")
            if state == "todo":
                return names, done, 0, ("The image is solved on its own when this page opens (or press Solve again); "
                                        "you can click the target meanwhile (Target).")
            return names, done, 0, ("Not solved: press Solve again (top right) to retry, or click the target on the "
                                    "image (Target), then the comps.")
        if not target:
            return names, done, 1, "Solved. Pick Target and click the target star."
        if not comps:
            return names, done, 2, ("Pick Comps and click comparison stars"
                                    + (" (green rings: suggested, near the target's brightness)." if
                                       self.suggested_comps else ".") + not_solved)
        if not check:
            return names, done, 3, "Pick Check and click a check star: a steady star that tests the comps." + not_solved
        return names, done, 4, "Ready: Run photometry."

    def _place_step_strip(self):
        if not hasattr(self, "step_strip"):
            return
        self.step_strip.pack_forget()
        self.step_show_button.pack_forget()
        if self.show_tips.get():
            self.step_strip.pack(side=LEFT, fill=X, expand=True)
            self._update_step_hint()
        else:
            self.step_show_button.pack(side=LEFT)

    def _set_tips(self, on: bool):
        self.show_tips.set(bool(on))
        self.settings["photo_tips"] = bool(on)
        self._save_settings()
        self._place_step_strip()

    def _update_step_hint(self):
        if not hasattr(self, "step_strip") or not self.show_tips.get():
            return
        try:
            names, done, current, text = self.photo_steps()
        except Exception:
            return
        if names != self._step_names:
            for w in self._step_widgets:
                w.destroy()
            self._step_widgets = []
            self.step_hide_button.pack_forget()
            # Hide tips first, at the right of the step labels.
            self.step_hide_button.pack(side=RIGHT, padx=(10, 0))
            for k, name in enumerate(names):
                if k:
                    arrow = ttk.Label(self.step_line, text="→", style="StepTodo.TLabel")
                    arrow.pack(side=LEFT)
                    self._step_widgets.append(arrow)
                lab = ttk.Label(self.step_line, text=name, style="StepTodo.TLabel")
                lab.pack(side=LEFT)
                self._step_widgets.append(lab)
            self._step_names = names
        labels = self._step_widgets[0::2]
        solve_state = self._solve_step_state()   # 2.2.16 (item 10): the first step in three states
        for k, lab in enumerate(labels):
            if k == 0 and solve_state == "solved":
                lab.configure(style="StepDone.TLabel", text="✓ 1 Solved")
            elif k == 0 and solve_state in ("failed", "impossible"):
                lab.configure(style="StepWarn.TLabel", text="1 Not solved")
            elif k == 0 and solve_state == "solving":
                lab.configure(style="StepOn.TLabel" if k == current else "StepTodo.TLabel", text="1 Solving…")
            elif k == current:
                lab.configure(style="StepOn.TLabel", text=names[k])
            elif k in done:
                lab.configure(style="StepDone.TLabel", text="✓ " + names[k])
            else:
                lab.configure(style="StepTodo.TLabel", text=names[k])
        self._step_state = (names, done, current, text)
        self.step_text.set(text)

    def _radec_is_fill(self) -> bool:
        """2.2.15 (review): the RA/Dec boxes still hold the field centre a solve filled in (not the target's)."""
        filled = getattr(self, "_radec_filled", None)
        return bool(filled) and (self.ra_hours.get().strip(), self.dec_deg.get().strip()) == tuple(filled)

    def _try_auto_target(self, why: str = "lookup"):
        """The automatic Target marker on an image solved before the RA/Dec arrived; redraws when it placed one."""
        if self.chart_placed and self._auto_target_marker(why):
            self._suggest_comps()
            self._update_pick_label()
            self.update_star_panel()
            self.refresh_preview()

    def _auto_target_marker(self, why: str = "solve") -> bool:
        """2.2.15 (GitHub #3): once the image is solved and the target's RA/Dec are known, put the Target marker on the
        target (centred on the star like a click) unless one is placed already. Returns True when it placed one."""
        if (self.mode_key == "discovery" or self.target_xy is not None or not self.chart_solution
                or self.preview is None or not self.preview_is_debayered or self._radec_is_fill()):
            return False
        try:
            ra, dec = float(self.ra_hours.get()) * 15.0, float(self.dec_deg.get())
        except (TypeError, ValueError):
            return False
        if not (math.isfinite(ra) and math.isfinite(dec)):
            return False
        name = self.star_id.get().strip() or "the target"
        # The catalog star at the target's position first (proper motion is applied to the night), else the plate
        # solution's pixel for its RA/Dec.
        best = None
        for x, y, st in self.chart_placed:
            if st.get("ra") is None or st.get("dec") is None:
                continue
            d = core.sky_separation_arcsec(ra, dec, st["ra"], st["dec"])
            if d < 5.0 and (best is None or d < best[0]):
                best = (d, (float(x), float(y)))
        if best is not None:
            xy = best[1]
        else:
            try:
                x, y = core.sky_to_pixel(ra, dec, self.chart_solution)
            except Exception:
                return False
            xy = (float(x), float(y))
        h, w = self.preview.shape[:2]
        try:
            edge = float(self.sky_out.get()) + 2
        except (TypeError, ValueError):
            edge = 4.0
        where = f"{name} ({core.sexagesimal_ra(ra / 15.0)} {core.sexagesimal_dec(dec)})"
        if not (math.isfinite(xy[0]) and math.isfinite(xy[1]) and 0 <= xy[0] < w and 0 <= xy[1] < h):
            self.log(f"Target: {where} is not on this frame by its plate solution; click the target by hand.")
            return False
        if not (edge <= xy[0] < w - edge and edge <= xy[1] < h - edge):
            self.log(f"Target: {where} is too close to the edge of this frame to measure (at ({xy[0]:.0f}, "
                     f"{xy[1]:.0f})); no marker placed.")
            return False
        # Centred on the star exactly as a click is (the star nearest the point, sized by the seeing). No star there
        # (review): no marker, rather than one on blank sky.
        try:
            radius = float(self.radius.get())
            seeing = self._seeing()
            info = core.recenter_info(self.preview, xy[0], xy[1], radius, seeing if math.isfinite(seeing) else None)
            found = bool(info["found"]) and info["shift"] <= max(3.0, radius)
        except Exception:
            found = False
        if not found:
            self.log(f"Target: no star where the plate solution puts {where}, at ({xy[0]:.0f}, {xy[1]:.0f}); click "
                     "the target.")
            return False
        xy = (float(info["x"]), float(info["y"]))
        self.target_xy = xy
        self._auto_target_xy = xy
        advanced = self._advance_pick_to_comps()   # 2.2.18 (item 7)
        self.log(f"Target: marker placed on {name} at ({xy[0]:.0f}, {xy[1]:.0f}) from its RA/Dec and the plate solution"
                 + (" (after the lookup)" if why == "lookup" else "") + ". Click with Target to move it if it is not "
                 "your star." + (" Next: click the comparison stars (Comps is picked)." if advanced else ""))
        return True

    def _pick_chosen_by_user(self):
        """2.2.18 (item 7): a pick button (page or pop-out) clicked by the user: the automatic step change leaves the
        choice alone from now on, for this image."""
        self._pick_user_chosen = True

    def _advance_pick_to_comps(self) -> bool:
        """2.2.18 (item 7): once the solve has put the Target marker on the target, pick Comps, the next step
        (Variables and Transits; Discovery has no comps). Never when the user has picked a step on this image, and
        only from Target. The page and pop-out buttons share pick_mode, so both follow. Returns True when it moved."""
        if self.mode_key not in ("variables", "transits") or getattr(self, "_pick_user_chosen", False):
            return False
        if self.target_xy is None or self.pick_mode.get() != "target":
            return False
        self.pick_mode.set("comp")
        self._pick_auto_advanced = True
        return True

    # ---- Discovery panel (2.2) ------------------------------------------------------------
    def _build_discovery_panel(self, panel):
        field = self._group(panel, "Field")
        field.pack(fill=X)
        self.disc_field_text = StringVar(value="Not solved yet. Calibrate, and the field is solved straight away.")
        ttk.Label(field, textvariable=self.disc_field_text, style="Card.TLabel", wraplength=460, justify=LEFT).pack(
            anchor=W)
        settings = self._group(panel, "Scan settings")
        settings.pack(fill=X, pady=(10, 0))
        self.scan_mag_limit = StringVar(value="")
        self.scan_sigma = DoubleVar(value=5.0)
        self.scan_excess = DoubleVar(value=3.0)
        self.scan_skip_crowded = BooleanVar(value=True)
        self._field(settings, 0, "Faintest magnitude", self.scan_mag_limit, width=8, hint="blank = all")
        self._field(settings, 1, "Detection threshold (σ)", self.scan_sigma, width=8, hint="lower = fainter")
        self._field(settings, 2, "Candidate at (× normal)", self.scan_excess, width=8, hint="scatter")
        self.scan_max_stars = IntVar(value=800)
        self._field(settings, 3, "Most stars to measure", self.scan_max_stars, width=8, hint="brightest first")
        # 2.2.18 (item 16): Discovery's own period range (the scan used the hidden Variables boxes before).
        ttk.Label(settings, text="Period search (days)", style="Card.TLabel").grid(row=4, column=0, sticky=W,
                                                                               padx=(0, 10), pady=3)
        prow = ttk.Frame(settings, style="Card.TFrame")
        prow.grid(row=4, column=1, columnspan=2, sticky=W, pady=3)
        # review: remembered in the settings (the Variables boxes are kept with each series)
        saved = self.settings.get("disc_period_range") or [0.05, 20.0]
        try:
            lo, hi = float(saved[0]), float(saved[1])
            if not (0 < lo < hi):
                raise ValueError
        except (TypeError, ValueError, IndexError):
            lo, hi = 0.05, 20.0
        self.disc_min_period = DoubleVar(value=lo)
        self.disc_max_period = DoubleVar(value=hi)
        self.disc_period_entries = []
        for k, var in enumerate((self.disc_min_period, self.disc_max_period)):
            if k:
                ttk.Label(prow, text="to", style="Card.TLabel").pack(side=LEFT, padx=4)
            entry = ttk.Entry(prow, textvariable=var, width=6)
            entry.pack(side=LEFT)
            entry.bind("<Return>", lambda _e: self._disc_period_changed(force=True))
            entry.bind("<KP_Enter>", lambda _e: self._disc_period_changed(force=True))
            entry.bind("<FocusOut>", lambda _e: self._disc_period_changed())
            self.disc_period_entries.append(entry)
        ttk.Checkbutton(settings, text="Leave out crowded stars (neighbor in the sky ring)",
                        variable=self.scan_skip_crowded).grid(row=5, column=0, columnspan=3, sticky=W, pady=(4, 0))
        ttk.Label(settings, text="Saturated and edge stars are measured but never listed as candidates. "
                                 "\"× normal\" compares a star's scatter with stars as bright. The period range "
                                 "applies to the candidates' periodograms (Enter refits a finished scan).",
                  style="Hint.TLabel", wraplength=440, justify=LEFT).grid(
            row=6, column=0, columnspan=3, sticky=W, pady=(4, 0))
        picks = self._group(panel, "Watched and excluded stars")
        picks.pack(fill=BOTH, expand=True, pady=(10, 0))
        self.disc_box = Listbox(picks, height=7, font=(core.UI_FONT, 9), activestyle="none", exportselection=False)
        self.disc_box.pack(fill=BOTH, expand=True, pady=(0, 4))
        row = ttk.Frame(picks, style="Card.TFrame")
        row.pack(fill=X)
        ttk.Button(row, text="Remove selected", command=self._remove_disc_selected).pack(side=LEFT)
        ttk.Button(row, text="Known variables in the field…", command=self._show_known_in_field).pack(side=RIGHT)

    def _update_discovery_panel(self):
        if not hasattr(self, "disc_box"):
            return
        self.disc_box.delete(0, END)
        self._disc_rows = []
        for name in self.watch_xy:
            self.disc_box.insert(END, f"●  watch   {name}")
            self._disc_rows.append(("watch", name))
        for k, (x, y) in enumerate(self.scan_excluded):
            star = self._chart_star_at((x, y))
            label = (star or {}).get("auid") or (star or {}).get("label") or f"({x:.0f}, {y:.0f})"
            self.disc_box.insert(END, f"✕  exclude   {label}")
            self._disc_rows.append(("exclude", k))

    def _remove_disc_selected(self):
        sel = self.disc_box.curselection()
        if not sel:
            return
        kind, key = self._disc_rows[sel[0]]
        if kind == "watch":
            self.remove_watch(key)
        else:
            del self.scan_excluded[key]
            self.refresh_preview()
        self._update_discovery_panel()

    def _show_known_in_field(self):
        if not self.variable_marks:
            messagebox.showinfo(APP_TITLE, "No known variables marked yet: the field check runs when the field is "
                                           "solved (VSX and SIMBAD must be reachable).")
            return
        lines = [self._variable_hover(v).replace("\n", "  ") + f"   at ({x:.0f}, {y:.0f})" for x, y, v in self.variable_marks]
        messagebox.showinfo(APP_TITLE, f"{len(lines)} known or suspected variable(s) in the frame:\n\n"
                            + "\n".join(lines[:40]) + ("\n…" if len(lines) > 40 else ""))

    def _field_summary(self):
        """Discovery's Field box after a solve: how it was solved, stars found, magnitudes, saturation, variables."""
        if self.preview is None or not self.preview_is_debayered:
            return
        sol = self.chart_solution or {}
        lines = []
        if self.chart_placed and sol:
            how = "from the plate solution in the FITS header" if sol.get("from_header") else "from star patterns"
            scale = sol.get("scale", float("nan")) * sol.get("scale_factor", 1.0)
            h, w = self.preview.shape
            try:
                ra, dec = core.pixel_to_sky((w - 1) / 2.0, (h - 1) / 2.0, sol)
                centre = f"centre {core.sexagesimal_ra(ra / 15.0)}  {core.sexagesimal_dec(dec)}"
            except Exception:
                centre = ""
            lines.append(f"Solved {how}: {scale:.2f}″/px, {centre}.")
            mags = [st.get("mag") for _x, _y, st in self.chart_placed if st.get("mag") is not None]
            lines.append(f"{len(self.chart_placed)} catalog stars placed (all catalogs merged)"
                         + (f", {self._band_word()} {min(mags):.1f} to {max(mags):.1f}." if mags else "."))
        else:
            lines.append("Not solved yet (it solves after calibrating; Solve again retries).")
        try:
            cap = max(50, int(self.scan_max_stars.get()))
            found = core.detect_sources(self.preview, max_sources=cap, nsigma=float(self.scan_sigma.get()),
                                        min_sep=max(2 * float(self.radius.get()), 8.0))
            lines.append(f"{len(found)} stars detected at {float(self.scan_sigma.get()):g}σ"
                         + (f" (the {cap}-star limit; raise \"Most stars to measure\" for more)." if len(found) >= cap
                            else "."))
        except Exception:
            pass
        sat = self._saturated_count()
        if sat is not None:
            lines.append(f"{sat} saturated on this frame (measured, never candidates).")
        if self.field_check_sources:
            lines.append(f"{len(self.variable_marks)} known or suspected variables in the frame "
                         f"({' + '.join(self.field_check_sources)}).")
        self.disc_field_text.set("\n".join(lines))

    def _saturated_count(self):
        try:
            level = 0.85 * float(self.sat_limit.get())
            r = int(math.ceil(float(self.radius.get())))
            found = core.detect_sources(self.preview, max_sources=400, nsigma=5.0,
                                        min_sep=max(2 * float(self.radius.get()), 8.0))
        except Exception:
            return None
        n = 0
        for x, y in found:
            x0, y0 = int(round(x)), int(round(y))
            box = self.preview[max(0, y0 - r):y0 + r + 1, max(0, x0 - r):x0 + r + 1]
            if box.size and np.nanmax(box) >= level:
                n += 1
        return n

    def _image_binning(self) -> int:
        """The binning the image on the Photometry page was made at (the Binning box may have changed since)."""
        if self.calibrated is not None and getattr(self, "masters_binning", None):
            return int(self.masters_binning)
        try:
            return int(self.binning.get())
        except Exception:
            return 1

    def _binning_changed(self):
        """2.2.13 (John): a binning change after Calibrate makes the image on the Photometry page out of date; say
        so at once instead of letting the solve fail later with a baffling 'no lock'."""
        try:
            new = int(self.binning.get())
        except Exception:
            return
        old = getattr(self, "masters_binning", None)
        if self.calibrated is None or not old or new == int(old):
            self._binning_warned = None
            return
        if getattr(self, "_binning_warned", None) == new:
            return
        self._binning_warned = new
        text = (f"Binning changed from {old} to {new}: run Calibrate again. The image on the Photometry page, its "
                f"solution and the star markers are still at Binning {old}.")
        self.status.set(text)
        self.log(text)

    def _auto_solve(self):
        """Solve the image as soon as the frame is calibrated (Discovery 2.2; every mode 2.2.13). Once per image:
        a new calibration, binning or channel makes a new image and solves again."""
        if self.busy or self.preview is None or not self.preview_is_debayered:
            return   # not marked as solved: the next visit to the Photometry page tries again
        token = getattr(self, "_preview_token", 0)
        if getattr(self, "_auto_solved_for", None) == token or self.chart_placed:
            return
        self._auto_solved_for = token
        self.label_aavso(auto=True)

    # ---- aperture suggestion ------------------------------------------------------------
    def _noise_note(self, night_points):
        """2.2.9: say when scintillation, not photon noise, sets the error bars, and how much exposure headroom the
        brightest marked star leaves (Tessa's 0.04 s frames: photon 0.05 mag, scintillation 0.17, peak at 19%)."""
        scint = self._scint_setup()
        pts = [o for o in night_points if not o.flag]
        if not pts or scint is None:
            return
        phot = [o.merr for o in pts if math.isfinite(o.merr)]
        sc = [core.scint_mag(o, scint) for o in pts]
        sc = [v for v in sc if math.isfinite(v)]
        if not phot or not sc:
            return
        p_med, s_med = float(np.median(phot)), float(np.median(sc))
        if s_med <= 2.0 * p_med:
            return
        peaks = [o.raw_peak for o in pts if math.isfinite(o.raw_peak)]
        head = ""
        if peaks and not getattr(self, "precalibrated", False):
            full = self._num(self.sat_limit, 65535.0) or 65535.0   # the raw saturation level set on the page
            frac = float(np.percentile(peaks, 95)) / full
            if 0 < frac < 0.75:
                head = (f" The target's peak reaches {frac:.0%} of the raw saturation level, so exposures about "
                        f"{max(1.0, 0.75 / frac):.1f}x longer still stay under 75% in focus; defocusing allows more.")
        exps = [o.exptime for o in pts if o.exptime]
        exp_txt = f" ({np.median(exps):g} s exposures)" if exps else ""
        self.log(f"Noise: scintillation sets the error bars{exp_txt}: about {s_med:.3f} mag per point from the "
                 f"atmosphere against {p_med:.3f} mag photon noise. Longer exposures (defocused if needed) or binning "
                 "points reduce it; scintillation falls as the square root of the exposure time." + head)

    def exofop_equipment(self, night: str = "") -> dict:
        """2.2.15 (item 11): the ExoFOP notes' equipment for a night: "0.36 m RC, f 2845 mm / ZWO ASI2600MC Duo" from
        the lights' TELESCOP / INSTRUME and the Optics box (aperture, focal length), the working pixel scale ("/px:
        pixel size / focal length × camera bin × Binning × 2 for a color camera's superpixel), and the photometry
        aperture radius (px). Missing parts say "not recorded"; scale and radius are None when unknown."""
        setup = self._night_info(night) if night else {}

        def num(v):
            try:
                f = float(v)
                return f if math.isfinite(f) and f > 0 else None
            except (TypeError, ValueError):
                return None

        def box(var):
            # 2.2.15 review: an empty Tk DoubleVar/IntVar raises TclError on .get(); an empty box is "not set".
            try:
                return num(var.get())
            except Exception:
                return None
        try:
            lights = self._lights()
            head = core.read_header(lights[0]) if lights else {}
        except Exception:
            head = {}
        head = head or {}
        tel = str(head.get("TELESCOP") or "").strip()
        cam = str(head.get("INSTRUME") or "").strip()
        stored = str(setup.get("instrument") or "").strip()
        binning = int(num(setup.get("binning")) or box(self.binning) or 1)
        try:
            superpixel = 1 if self.pattern.get() == core.MONO else 2
        except Exception:
            superpixel = 2
        radius = num(setup.get("radius")) or box(self.radius)
        sky_in = num(setup.get("sky_in")) or box(self.sky_in)
        sky_out = num(setup.get("sky_out")) or box(self.sky_out)
        out = {"binning": binning, "camera_bin": 1, "superpixel": superpixel, "radius_px": radius,
               "sky": f"{sky_in:g}-{sky_out:g}" if sky_in and sky_out else ""}
        if stored and stored != " / ".join(p for p in (tel, cam) if p):
            # 2.2.15 review: the night was taken with other lights than the ones on the Input page: its own header
            # names, verbatim, and no scale (this setup's optics and binning may not be that night's).
            # 2.2.16 (item 5): its stored telescope text when it has one; a mount-like TELESCOP is left out.
            scope_n = str(setup.get("telescope") or "").strip()
            cam_n = [p for p in core.instrument_without_mount(stored).split(" / ") if p]
            if scope_n:
                cam_last = cam_n[-1] if cam_n else ""
                text = f"{scope_n} / {cam_last}" if cam_last and cam_last not in scope_n else scope_n
            else:
                parts = [p.strip() for p in stored.split(" / ")]
                if len(parts) >= 2 and not core.clean_telescope_name(parts[0]):
                    # 2.2.16 (review): like the live path: the mount is no telescope
                    text = "telescope not recorded / " + " / ".join(p for p in parts[1:] if p)
                else:
                    text = core.instrument_without_mount(stored) or "not recorded"
            return dict(out, telescope=text, scale=None)
        ap, focal, pix = box(self.aperture_mm), box(self.focal_mm), box(self.pixel_um)
        # 2.2.16 (item 5): Telescope field > TELESCOP unless it names the mount > aperture and focal length.
        scope = core.telescope_text(self.telescope_name.get(), tel, ap, focal)
        if scope and cam:
            line = f"{scope} / {cam}"
        elif scope:
            line = f"{scope} / camera not recorded"
        elif cam:
            line = f"telescope not recorded / {cam}"
        else:
            line = "not recorded"
        camera_bin = 1
        for key in ("XBINNING", "BINNING", "XBIN"):   # the keys the solve reads (the first one present counts)
            if head.get(key) in (None, ""):
                continue
            try:
                camera_bin = max(int(float(head.get(key, 1))), 1)
                break
            except (TypeError, ValueError):
                continue
        if pix and focal:
            scale = pix / focal * 206.265 * camera_bin * binning * superpixel
        else:
            # No focal length or pixel size in the Optics box: the header's scale of the frame as saved.
            stored_scale = core.header_plate_scale(head) if head else None
            scale = stored_scale * binning * superpixel if stored_scale else None
        return dict(out, telescope=line, scale=scale, camera_bin=camera_bin)

    def _lights_instrument(self) -> str:
        """'<TELESCOP> / <INSTRUME>' from the first light's header ('' when the header names neither)."""
        try:
            lights = self._lights()
            h = core.read_header(lights[0]) if lights else {}
        except Exception:
            return ""
        parts = [str(h.get(k) or "").strip() for k in ("TELESCOP", "INSTRUME")]
        return " / ".join(p for p in parts if p)

    def _transit_in_lights(self, lights) -> bool:
        """2.2.9: in Transits mode, before photometry: is a transit of the planet predicted during these lights? If
        not, it may be a variability run started in the wrong mode (Tessa's HD 219134 frames, 10 Oct)."""
        if self.mode_key != "transits" or not lights:
            return True
        page = getattr(self, "transit_page", None)
        planet = page.current_planet() if page is not None else None
        if not planet:
            return True
        try:
            j0 = core.mid_jd(core.read_header(lights[0]))
            j1 = core.mid_jd(core.read_header(lights[-1]))
        except Exception:
            return True
        if j0 is None or j1 is None or not (math.isfinite(j0) and math.isfinite(j1)):
            return True
        # JD to BJD differs by up to about 8 minutes: pad the span by 15 minutes so the check never cuts too close.
        chk = tcore.transit_window_check(min(j0, j1) - 0.0105, max(j0, j1) + 0.0105, planet)
        if chk is None or chk["overlap"]:
            return True
        side = "after" if chk["hours"] > 0 else "before"
        return messagebox.askyesno(
            APP_TITLE,
            f"No transit of {planet.get('name') or 'the planet'} is predicted during these lights: the nearest is "
            f"{abs(chk['hours']):.1f} h {side} them.\n\nIf this is a variability run, switch to Variables mode "
            "first (the series and reports are kept per mode).\n\nRun photometry in Transits mode anyway?")

    def _lights_span(self, lights):
        """(first, last) mid-exposure JD of the lights, or None when the headers have no usable time."""
        try:
            j0 = core.mid_jd(core.read_header(lights[0]))
            j1 = core.mid_jd(core.read_header(lights[-1]))
        except Exception:
            return None
        if j0 is None or j1 is None or not (math.isfinite(j0) and math.isfinite(j1)):
            return None
        return min(j0, j1), max(j0, j1)

    def _target_variable_reasons(self) -> list:
        """2.2.14 (item 19): why the target looks like a variability target: a known variable (VSX lookup, or a
        variable at the Target marker after Solve) or an open Variables series. Empty when nothing says so."""
        import re

        def planet_host(var_type) -> bool:
            # 2.2.14 (item 19, review): VSX lists transiting-planet hosts as type EP (TrES-3, WASP-12, HD 189733) with
            # the planet's orbital period: that is not a reason to call the run a variability run.
            return re.split(r"[^A-Za-z]", str(var_type or "").strip().upper())[0] == "EP"
        why = []
        if (self.target_var_type or self.catalog_period) and not planet_host(self.target_var_type):
            why.append(f"{self.star_id.get().strip() or 'the target'} is a known variable"
                       + (f" ({self.target_var_type})" if self.target_var_type else "")
                       + (f", period {self.catalog_period:.5f} d" if self.catalog_period else ""))
        elif self.target_xy is not None:
            star = self._chart_star_at(self.target_xy) if self.chart_placed else None
            hits = (star or {}).get("variable") or self._variables_at(self.target_xy)
            hits = [v for v in hits or [] if not planet_host((v or {}).get("type") if isinstance(v, dict) else "")]
            if hits:
                why.append(f"the target is listed as a variable ({core.variable_text({'variable': hits})})")
        if self.observations and self.series_mode == "variables" and not self.series_mode_unknown:
            why.append(f"a Variables series is open ({os.path.basename(self.series_path) or 'not saved yet'})")
        return why

    def _planet_is_target(self, planet: dict) -> bool:
        """2.2.14 (item 19): the planet in the Planet box orbits the Input page's target (by position, else by name)."""
        try:
            if planet.get("ra") is not None and self.ra_hours.get().strip() and self.dec_deg.get().strip():
                return core.sky_separation_arcsec(float(self.ra_hours.get()) * 15.0, float(self.dec_deg.get()),
                                                  float(planet["ra"]), float(planet["dec"])) < 60.0
        except (TypeError, ValueError, KeyError):
            pass
        host = name_key(planet.get("host") or "")
        return bool(host) and host == name_key(self.star_id.get())

    def _rerun_in_mode(self, mode: str) -> bool:
        """2.2.14 (item 19): switch mode and start Run photometry again there (every check runs again in that mode).
        Returns False, so the run asked for in the old mode stops."""
        self.set_mode(mode)
        if self.mode_key != mode:
            return False
        self.log(f"Switched to {MODES[mode]['name']} mode for this run.")
        self.after(150, self.run_photometry)
        return False

    def _mode_guard(self, lights) -> bool:
        """2.2.14 (item 19): before photometry, catch a run started in the wrong mode (14 Oct: HD 219134 run in
        Transits mode by accident). Transits mode with no planet, or no transit predicted in the lights, on a known
        variable or with a Variables series open: offer Variables mode. Variables mode on the host of the planet in
        the Planet box during one of its predicted transits: offer Transits mode. Needs only the lights' times and
        the Input page (no plate solution, no header pointing; a solve adds the variable-at-the-marker check)."""
        self._series_mode_answered = False   # 2.2.14 (item 19, review): set when this guard already asked about it
        if self.mode_key == "transits" and lights:
            page = getattr(self, "transit_page", None)
            planet = page.current_planet() if page is not None else None
            why = self._target_variable_reasons()
            series_open = self.observations and self.series_mode == "variables" and not self.series_mode_unknown
            span = self._lights_span(lights)
            if why and (planet is None or span is not None):
                chk = None
                if planet is not None:
                    chk = tcore.transit_window_check(span[0] - 0.0105, span[1] + 0.0105, planet)
                if planet is None or (chk is not None and not chk["overlap"]):
                    if planet is None:
                        what = "No planet is entered on the Input page"
                    else:
                        what = (f"No transit of {planet.get('name') or 'the planet'} is predicted during these lights "
                                f"(the nearest is {abs(chk['hours']):.1f} h {'after' if chk['hours'] > 0 else 'before'} "
                                "them)")
                    answer = messagebox.askyesnocancel(
                        APP_TITLE, f"{what}, and {' and '.join(why)}.\n\nRun in Variables mode instead?\n\n"
                                   "Yes: switch to Variables mode and run there.\nNo: run in Transits mode anyway"
                                   + (" (the night starts a new series; the Variables series is not changed)"
                                      if series_open else "") + ".\nCancel: don't run.")
                    if answer is None:
                        return False
                    if answer:
                        return self._rerun_in_mode("variables")
                    # The open-series question would ask the same again: this No answers it (a new series).
                    self._series_mode_answered = bool(series_open)
                    self.log(f"Run in Transits mode as chosen ({what.lower()}; {'; '.join(why)})"
                             + ("; the night starts a new series." if series_open else "."))
                    return True
            return self._transit_in_lights(lights)
        if self.mode_key == "variables" and lights:
            if self.observations and self.series_mode == "variables":
                return True   # a Variables campaign on a planet host (HD 219134) is not asked every night
            span = self._lights_span(lights)
            if span is None:
                return True
            # 2.2.18 (item 20): the Planet box is filled only after a Transits visit, so the target's own transiting
            # planets count too (NASA Exoplanet Archive, looked up in the background after the Star ID lookup, or by
            # an earlier planet-transit check; kept per star). Not known yet or offline: nothing is asked.
            planet, chk = None, None
            typed = self.planet_from_fields()
            candidates = [typed] if typed and self._planet_is_target(typed) else []
            candidates += self._target_planets()
            for p_ in candidates:
                c_ = tcore.transit_window_check(span[0] - 0.0105, span[1] + 0.0105, p_)
                if c_ is not None and c_["overlap"]:
                    planet, chk = p_, c_
                    break
            if planet is None:
                return True
            try:
                when = f" (mid-transit about {tcore.jd_to_local(chk['tc']).strftime('%H:%M')} local)"
            except Exception:
                when = ""
            name = planet.get("name") or "the planet"
            answer = messagebox.askyesnocancel(
                APP_TITLE, f"{self.star_id.get().strip() or 'The target'} hosts {name}, and a transit of {name} is "
                           f"predicted during these lights{when}.\n\nRun in Transits mode instead?\n\n"
                           "Yes: switch to Transits mode and run there.\nNo: run in Variables mode anyway.\n"
                           "Cancel: don't run.")
            if answer is None:
                return False
            if answer:
                if planet is not typed and planet.get("name"):
                    # 2.2.18 (item 20): the planet came from the lookup: its ephemeris goes into the Planet box now
                    # (the Transits run's own check reads it at once), then the full lookup fills the rest.
                    self._planet_to_fields(planet)
                    self.planet_info = {k: v for k, v in planet.items() if k in ("name", "host", "period_err", "t0_err")}
                    self.planet_info["typed"] = planet["name"]
                    try:
                        self.lookup_planet(quiet=True)
                    except Exception:
                        traceback.print_exc()
                return self._rerun_in_mode("transits")
            self.log(f"Run in Variables mode as chosen, during a predicted transit of {name}.")
        return True

    def _aperture_sane(self) -> bool:
        """Before a run: is the aperture far larger (or smaller) than the stars on this frame? Typical after
        switching telescopes, for example to MicroObservatory frames with your own aperture still set."""
        if self.preview is None or not self.preview_is_debayered:
            return True
        first_comp = next((getattr(self, f"{r}_xy") for r in self._comp_roles_marked()), None)
        fwhm = self._seeing()  # 2.2.8: field seeing, not the (possibly faint) marked stars
        if not math.isfinite(fwhm):
            sizes = [self._fwhm_at(self.preview, xy)[0] for xy in (self.target_xy, first_comp) if xy is not None]
            sizes = [f for f in sizes if math.isfinite(f) and 0.8 < f < 40]
            if not sizes:
                return True
            fwhm = float(np.median(sizes))
        try:
            r = float(self.radius.get())
        except (TypeError, ValueError):
            return True
        prop = core.suggest_apertures(fwhm)
        too_big = r > 3.0 * fwhm + 0.05   # 2.2.16 (review): the aperture test's own 3.0 × FWHM is not "too big"
        if too_big or r < 0.9 * fwhm:
            kind = "much larger" if too_big else "smaller"
            self.log(f"Aperture check: r = {r:g} px is {kind} than the stars (FWHM {fwhm:.1f} px); suggested r = "
                     f"{prop['radius']:g}, sky {prop['sky_in']:g}-{prop['sky_out']:g} px.")
            return messagebox.askyesno(
                APP_TITLE,
                f"The aperture (r = {r:g} px) is {kind} than the stars on this frame (FWHM {fwhm:.1f} px).\n\n"
                + ("A large aperture collects sky noise and neighboring stars; scatter can be several times worse. "
                   if too_big else "A small aperture loses starlight as the seeing changes. ")
                + f"Suggest… would use r = {prop['radius']:g}, sky {prop['sky_in']:g}–{prop['sky_out']:g} px.\n\n"
                "Run with the current aperture anyway?")
        return True

    def _marked_stars(self):
        return [(role_label(role), xy) for role, xy in self._marked_roles()]

    def _fwhm_at(self, img, xy):
        """2.2.8: one star's FWHM from a Gaussian fit in a box sized to the star (core.star_fwhm)."""
        try:
            r = core.star_fwhm(img, xy[0], xy[1])
            return (r["fwhm"] if r["ok"] else float("nan")), r["peak"]
        except Exception:
            return float("nan"), float("nan")

    def _star_snr(self, img, xy):
        try:
            return float(core.star_fwhm(img, xy[0], xy[1])["snr"])
        except Exception:
            return float("nan")

    def _seeing(self, img=None):
        """2.2.8: the preview's seeing (FWHM, px), measured on bright, unsaturated, isolated stars; cached per image."""
        img = self.preview if img is None else img
        if img is None:
            return float("nan")
        key = (id(img), getattr(img, "shape", None))
        cache = getattr(self, "_seeing_cache", None)
        if cache is not None and cache[0] == key:
            return cache[1]
        try:
            value = float(core.field_fwhm(img)["fwhm"])
        except Exception:
            value = float("nan")
        self._seeing_cache = (key, value)
        return value

    def suggest_apertures(self):
        if self.preview is None or not self.preview_is_debayered:
            messagebox.showinfo(APP_TITLE, "Calibrate first (Calibrate page); star sizes are measured on the calibrated, debayered image.")
            return
        img = self.preview
        # 2.2.8: the seeing comes from bright, unsaturated, isolated field stars. Faint or crowded marked stars gave
        # sizes 3x too big (CoRoT-1: 10.5 px for 2-3 px stars) and so apertures that swallowed neighbours.
        field = core.field_fwhm(img)
        sizes = []
        for role, xy in self._marked_stars():
            fwhm_i, peak = self._fwhm_at(img, xy)
            if math.isfinite(fwhm_i) and 0.8 < fwhm_i < 40:
                sizes.append((role, fwhm_i, peak))
        if math.isfinite(field["fwhm"]):
            fwhm = float(field["fwhm"])
            basis = f"median of {field['n']} bright, unsaturated, isolated stars"
        elif sizes:
            fwhm = float(np.median([f for _r, f, _p in sizes]))
            basis = "from the marked stars (no bright isolated star to measure)"
        else:
            messagebox.showinfo(APP_TITLE, "Could not measure star sizes on this frame. Mark the target and comps first.")
            return
        self._seeing_cache = ((id(img), getattr(img, "shape", None)), fwhm)
        prop = core.suggest_apertures(fwhm)
        notes = self._neighbor_notes(img, prop)
        if self.target_xy is not None:
            snr = self._star_snr(img, self.target_xy)
            if math.isfinite(snr) and snr < 30:
                lo, hi = max(2.0, round(1.2 * fwhm, 1)), max(2.0, round(1.5 * fwhm, 1))
                notes.insert(0, f"The target is faint (peak about {snr:.0f} × the noise). Faint stars usually do best "
                                f"with 1.2–1.5 × FWHM (r = {lo:g}–{hi:g} px); the test below will tell.")
        SuggestWindow(self, fwhm, sizes, prop, notes, basis)

    def _neighbor_notes(self, img, prop):
        """Stars that fall inside a marked star's aperture or sky ring with the proposed sizes."""
        found = core.detect_sources(img, max_sources=400, nsigma=5.0, min_sep=3.0)
        notes = []
        for role, (x, y) in self._marked_stars():
            inside, close, ring = 0, [], 0
            for sx, sy in found:
                d = math.hypot(sx - x, sy - y)
                if d < 2.0:
                    continue
                if d <= prop["radius"] + 1.0:
                    inside += 1
                elif d < prop["sky_in"]:
                    close.append(d)
                elif d <= prop["sky_out"]:
                    ring += 1
            if inside:
                notes.append(f"⚠ {role}: {inside} other star(s) inside the aperture; its light will be added to "
                             f"{role.lower()}'s. Choose another star, or a smaller aperture.")
            elif close:
                notes.append(f"⚠ {role}: a star {min(close):.0f} px away, just outside the aperture; in poor seeing its "
                             "light spills in. Prefer another star if you have a choice.")
            elif ring:
                notes.append(f"{role}: {ring} faint star(s) in the sky ring; the sigma-clipped sky usually rejects them.")
        return notes

    def test_apertures(self, fwhm: float, window, frames: int = 30):
        """Measure 4 apertures on up to 30 (or 60) frames and report the check star's and target's scatter for each,
        with uncertainties and how often the lowest stays lowest when the frames are resampled."""
        lights = self._lights()
        if not lights or self.masters is None:
            messagebox.showinfo(APP_TITLE, "Calibrate first; the test measures real frames.", parent=window.win)
            return
        # 2.2.5: the test uses the first two marked comps, whichever numbers they have.
        marked_comps = [getattr(self, f"{r}_xy") for r in self._comp_roles_marked()]
        test_c1 = marked_comps[0] if marked_comps else None
        test_c2 = marked_comps[1] if len(marked_comps) > 1 else None
        if test_c1 is None:
            messagebox.showinfo(APP_TITLE, "Mark at least one comparison star; the test compares stars against it.",
                                parent=window.win)
            return
        if self.check_xy is not None:
            ref_label = "check star"
        elif test_c2 is not None:
            ref_label = "comp 2"
        else:
            ref_label = "target (it may truly vary)"
        if not self._start_job("Testing apertures…"):
            return
        radii = [round(f * fwhm, 1) for f in (1.2, 1.5, 1.8, 2.2)]
        radii = sorted({max(r, 2.0) for r in radii})
        rmax = max(radii)
        sky_in = core.sky_inner_radius(rmax, fwhm)   # 2.2.16 (item 1): at least r + 1.5 x FWHM
        sky_out = float(math.ceil(math.sqrt(sky_in ** 2 + 4.0 * rmax ** 2)))
        # 2.2.16 (item 1): 2.6 and 3.0 x FWHM are measured too (cheap: the frames are read anyway) and join the
        # comparison only when the best of the four is the largest, with their own sky ring further out. 3.0 x is
        # rounded down so it never trips the "much larger than the stars" check at Run.
        extra = sorted({r for r in (round(2.6 * fwhm, 1), math.floor(3.0 * fwhm * 10.0 + 1e-6) / 10.0) if r > rmax + 0.05})
        rings = {r: (sky_in, sky_out) for r in radii}
        if extra:
            x_in = core.sky_inner_radius(max(extra), fwhm)
            x_out = float(math.ceil(math.sqrt(x_in ** 2 + 4.0 * max(extra) ** 2)))
            rings.update({r: (x_in, x_out) for r in extra})
        n = min(frames, len(lights))
        picks = [lights[int(round(k))] for k in np.linspace(0, len(lights) - 1, n)]
        pts = [self.target_xy or test_c1, test_c1, test_c2, self.check_xy]
        xy = np.array([p if p is not None else (np.nan, np.nan) for p in pts], dtype=float)
        binning = max(int(self.binning.get()), 1)
        masters, pattern, mode = self.masters, self.pattern.get(), self.debayer_mode.get()
        ref = self.preview
        ref_sources = core.detect_sources(ref, max_sources=80)
        altaz = self._mount_info(lights)["mount"] == core.MOUNT_ALTAZ   # 2.2.18 (item 8): field rotation
        cancel = self._cancel
        self._progress_start(n, "Aperture test")
        self.log(f"Aperture test: r = {', '.join(f'{r:g}' for r in radii)} px, sky {sky_in:g}–{sky_out:g} px, "
                 f"{n} frames; judged on the {ref_label} against the comps."
                 + (f" Also r = {', '.join(f'{r:g}' for r in extra)} px (sky {rings[extra[0]][0]:g}–"
                    f"{rings[extra[0]][1]:g}), used if the largest of the four is best." if extra else ""))

        def work():
            rows = {r: [] for r in radii + extra}
            seeing = []
            last = {"sources": None, "reg": None}
            try:
                for i, path in enumerate(picks, 1):
                    if cancel.is_set():
                        raise RuntimeError("Cancelled")
                    try:
                        raw, header = core.read_fits(path)
                        data = core.debayer(core.calibrate_frame(core.bin_image(raw, binning), header, masters), pattern, mode)
                        src = core.detect_sources(data, max_sources=80)
                        if altaz:   # 2.2.18 (item 8)
                            reg = core.register_rotating(ref_sources, src, data.shape, last["sources"], last["reg"])
                        else:
                            reg = core.register_frame(ref_sources, src, data.shape)
                        if reg is None and last["sources"] is not None and not altaz:
                            step = core.register_frame(last["sources"], src, data.shape)
                            if step is not None:
                                reg = core.compose_registration(last["reg"], step, data.shape)
                        if reg is None:
                            raise ValueError("could not align")
                        last["sources"], last["reg"] = src, reg
                        good = np.isfinite(xy[:, 0])
                        pos = np.full_like(xy, np.nan)
                        pos[good] = core.move_points(xy[good], reg, data.shape)
                        # 2.2.8: seeing from bright isolated stars, on a 600 px box around the target (fast on big frames)
                        cx_, cy_ = (pos[0] if np.isfinite(pos[0, 0]) else pos[1])
                        y0_, x0_ = max(0, int(cy_) - 300), max(0, int(cx_) - 300)
                        f = core.field_fwhm(data[y0_:y0_ + 600, x0_:x0_ + 600], n=6)["fwhm"]
                        if math.isfinite(f):
                            seeing.append(f)
                        exp = core.header_exptime(header)
                        frame_rows = {}
                        for r in radii + extra:
                            flux, _sky = core.measure_many(data, pos[good], r, *rings[r])   # 2.2.16 (item 1)
                            mags = np.full(4, np.nan)
                            with np.errstate(divide="ignore", invalid="ignore"):
                                mags[good] = np.where(flux > 0, -2.5 * np.log10(flux / exp), np.nan)
                            frame_rows[r] = mags
                        for r in radii + extra:  # all apertures or none, so every aperture has the same frames
                            rows[r].append(frame_rows[r])
                    except Exception as exc:
                        self.call_ui(self.log, f"  aperture test skipped {os.path.basename(path)}: {exc}")
                    self.call_ui(self._progress, i)
                result = core.aperture_test_stats({r: np.array(rows[r]) for r in radii if rows[r]}, ref_label,
                                                  has_comp2=not np.isnan(xy[2, 0]))
                best = result.get("ref_best")
                if extra and best is not None and abs(best["r"] - rmax) < 0.05 and rows[rmax]:
                    # 2.2.16 (item 1): the best is the largest aperture tried: let 2.6 and 3.0 x FWHM compete too.
                    four = result
                    result = core.aperture_test_stats({r: np.array(rows[r]) for r in radii + extra if rows[r]},
                                                      ref_label, has_comp2=not np.isnan(xy[2, 0]))
                    result["extended"] = list(extra)
                    # review: a clear winner of the four is kept when the larger ones only split the wins with it
                    result["four_best"] = four.get("ref_best")
                result["rings"] = rings
                result["suggested_r"] = min(radii, key=lambda r: abs(r - 1.8 * fwhm))
                self.call_ui(self._aperture_test_done, window, result, seeing, sky_in, sky_out, ref_label, None)
            except Exception as exc:
                self.call_ui(self._aperture_test_done, window, None, seeing, sky_in, sky_out, ref_label, str(exc))

        self._blink_worker = threading.Thread(target=work, daemon=True)
        self._blink_worker.start()

    def _aperture_test_done(self, window, result, seeing, sky_in, sky_out, ref_label, error):
        self._end_job()
        self._progress_end("" if not error else ("Cancelled." if error == "Cancelled" else "Failed."))
        if error:
            self.log(f"Aperture test: {error}")
            return
        window.show_test(result, seeing, sky_in, sky_out, ref_label)

    def toggle_photo_log(self):
        if self.photo_logf.winfo_ismapped():
            self.photo_logf.pack_forget()
            self.log_toggle.configure(text="Show log")
        else:
            self.photo_logf.pack(fill=X, side="bottom", before=self.photo_prog_row, pady=(4, 0))
            self.log_toggle.configure(text="Hide log")
            self.photo_log.see(END)

    @staticmethod
    def _clock(seconds: float) -> str:
        if not math.isfinite(seconds):
            return "--:--"
        seconds = int(round(seconds))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def _progress_start(self, total: int, label: str):
        self._prog = {"t0": time.monotonic(), "n": max(int(total), 1), "label": label}
        self.progress_bar.configure(maximum=self._prog["n"], value=0)
        self.progress_text.set(f"{label}: starting, {total} frames")

    def _progress(self, done: int):
        p = self._prog
        if not p:
            return
        elapsed = time.monotonic() - p["t0"]
        left = elapsed / done * (p["n"] - done) if done else float("nan")
        self.progress_bar.configure(value=done)
        self.progress_text.set(f"{p['label']}: frame {done}/{p['n']}   elapsed {self._clock(elapsed)}   "
                               f"about {self._clock(left)} left")

    def _progress_end(self, text: str = ""):
        p = self._prog
        if not p:
            return
        self.progress_bar.configure(value=p["n"])
        self.progress_text.set(f"{p['label']} done in {self._clock(time.monotonic() - p['t0'])}. {text}".strip())
        self._prog = None

    def refresh_preview(self):
        self._update_step_hint()   # 2.2.15 (GitHub #3)
        if self.photo_canvas is None or self.preview is None:
            return
        img = self.preview
        # 2.2.13 (John): every new image (a calibration, a binning change, new lights, another channel) opens with
        # the whole frame in view; on 13 Oct a Bin 1 image opened inside the old Bin 2 zoom.
        token = getattr(self, "_preview_token", 0)
        if token != getattr(self, "_photo_drawn_token", None):
            self._photo_view = None
            self._photo_drawn_shape = None
        # 2.2.3: keep the zoom and pan across a redraw (placing or clearing a marker, labels, a new run): take the
        # view from the axes as they are now, not only from the last stored zoom, so no redraw path can reset it.
        # Only a different image (new calibration or binning) starts again from the whole frame.
        if (self._photo_view is None and getattr(self, "_photo_drawn_shape", None) == img.shape
                and self.photo_ax.images):
            cur_x, cur_y = tuple(self.photo_ax.get_xlim()), tuple(self.photo_ax.get_ylim())
            full_x, full_y = (-0.5, img.shape[1] - 0.5), (img.shape[0] - 0.5, -0.5)
            if (abs(cur_x[1] - cur_x[0]) < abs(full_x[1] - full_x[0]) - 1
                    or abs(cur_y[1] - cur_y[0]) < abs(full_y[1] - full_y[0]) - 1):
                self._photo_view = (cur_x, cur_y, img.shape)
        self.photo_ax.clear()
        # 2.2.15 (GitHub #1): the display stretch chosen under the image (default: linear, 5% to 99.5% as before).
        # Kept between redraws of the same image, so placing a marker does not stretch the frame again.
        stretch = self.stretch["photo"]
        cache = self._photo_disp
        if cache is None or cache[0] is not img or cache[1] != token or cache[2] != stretch:
            cache = (img, token, dict(stretch), core.stretch_display(img, stretch))
            self._photo_disp = cache
        self.photo_ax.imshow(cache[3], cmap="gray", origin="upper", vmin=0.0, vmax=1.0, interpolation="nearest")
        self._mark(self.target_xy, PICK_COLORS["target"], "T")
        for role in COMP_ROLES:
            self._mark(getattr(self, f"{role}_xy"), PICK_COLORS[role], f"C{comp_number(role)}")
        self._mark(self.check_xy, PICK_COLORS["check"], "K")
        circles, labels = {}, []
        for x, y, star in self.chart_placed:
            if not (0 <= x < img.shape[1] and 0 <= y < img.shape[0]):
                continue
            seq = star.get("seq") if self.mode_key != "discovery" else None
            if seq and self._overlay("catalog"):
                # 2.2.13 (John): AAVSO sequence stars are always marked, in their own colour with the chart label
                # ("91" = V 9.1), whichever catalog's circles are shown: they are the vetted comps.
                colour = "#ff5252" if star.get("variable") else SEQ_COLOR
                self.photo_ax.plot(x, y, marker="o", mfc="none", mec=colour, ms=11, mew=1.4)
                if self.show_labels.get():
                    self.photo_ax.text(x + 5, y - 5, seq.get("label") or str(star.get("label", "")), color=colour,
                                       fontsize=8, fontweight="bold")
                continue
            if not self._overlay("variables" if star.get("variable") else "catalog"):
                continue
            if not star.get("variable") and not self._circle_shown(star):
                continue
            colour = "#ff5252" if star.get("variable") else "#ffd24a"
            # 2.2.14 (item 5): drawn below in one go per colour (Gaia now brings thousands of stars per frame).
            circles.setdefault(colour, []).append((star.get("mag") if star.get("mag") is not None else 99.0, x, y))
            if self.show_labels.get():
                labels.append((star.get("mag") if star.get("mag") is not None else 99.0, x, y, star, colour))
        # 2.2.14 (item 5): labels for the brightest few hundred circles only (thousands of labels are unreadable and
        # took seconds to draw); hovering shows any star.
        for _m, x, y, star, colour in sorted(labels, key=lambda item: item[0])[:CIRCLE_LABELS_MAX]:
            self.photo_ax.text(x + 4, y - 4, self._circle_label(star), color=colour, fontsize=7)
        for colour, pts in circles.items():
            # review nit: catalog circles capped to the brightest CIRCLES_MAX (variables, in red, are all drawn).
            if colour != "#ff5252":
                pts = sorted(pts, key=lambda p: p[0])[:CIRCLES_MAX]
            self.photo_ax.plot([p[1] for p in pts], [p[2] for p in pts], ls="none", marker="o", mfc="none", mec=colour,
                               ms=9)
        for x, y, v in self._loose_variable_marks():
            if 0 <= x < img.shape[1] and 0 <= y < img.shape[0]:
                self.photo_ax.plot(x, y, marker="x", color="#ff5252", ms=7, mew=1.2)
        for name, (x, y) in self.watch_xy.items():
            self.photo_ax.plot(x, y, marker="D", mfc="none", mec=PICK_COLORS["watch"], ms=13, mew=1.4)
            self.photo_ax.text(x + 9, y + 9, "W", color=PICK_COLORS["watch"], fontsize=9, fontweight="bold")
        for mark in self.scan_marks:
            x, y, label = mark[:3]
            style = SCAN_MARK_STYLES.get(self._scan_kind(mark), SCAN_MARK_STYLES["new"])
            self.photo_ax.plot(x, y, marker="s", mfc="none", mec=style["color"], ms=style["ms"], mew=style["mew"],
                               alpha=style["alpha"], ls="none")
            self.photo_ax.text(x + 7, y + 7, label, color=style["color"], fontsize=style["fs"],
                               fontweight=style["weight"], alpha=style["alpha"])
        if self._overlay("spares"):
            for x, y, name in self.spare_marks:
                self.photo_ax.plot(x, y, marker="D", mfc="none", mec="#7fd6ff", ms=7, lw=0.8)
        for x, y in self.scan_excluded:
            self.photo_ax.plot(x, y, marker="x", color="#ff9800", ms=12, mew=2.0)
        if self.mode_key != "discovery" and self._overlay("suggested"):
            taken = {tuple(getattr(self, f"{r}_xy")) for r in COMP_ROLES if getattr(self, f"{r}_xy") is not None}
            for name, _dm, x, y in self.suggested_comps:
                if (x, y) not in taken:
                    self.photo_ax.plot(x, y, marker="o", mfc="none", mec="#2ca02c", ms=16, mew=1.2, ls="none",
                                       alpha=0.9)
        self._photo_hover_sets = self._photo_hover_data(img.shape)
        self._photo_hover_annots = {}
        self._photo_hover_key = None
        if self.preview_is_debayered:
            what = "watch or exclude stars" if self.mode_key == "discovery" else "mark stars"
            self.photo_ax.set_title(f"Debayered ({self.debayer_mode.get()}) — click to {what}, "
                                    "right-click a marker to clear it")
        else:
            self.photo_ax.set_title("Calibrated mosaic — choose the channel on the Calibrate page")
        # 2.2.13 (John): labels stay inside the image (they spilled outside the axes); markers placed off the frame
        # (an old position, a star beyond the edge) no longer widen the view: the whole frame is set explicitly.
        self._draw_compass(img.shape)
        for txt in self.photo_ax.texts:
            txt.set_clip_on(True)
            txt.set_clip_box(self.photo_ax.bbox)
        self.photo_fig.tight_layout()
        self.photo_ax.set_xlim(-0.5, img.shape[1] - 0.5)
        self.photo_ax.set_ylim(img.shape[0] - 0.5, -0.5)
        view = self._photo_view
        if view is not None and view[2] == img.shape:
            self.photo_ax.set_xlim(view[0])
            self.photo_ax.set_ylim(view[1])
        self._photo_drawn_shape = img.shape
        self._photo_drawn_token = token
        self.photo_canvas.draw_idle()
        self._update_legend()

    def _circles_catalog(self) -> str:
        """2.2.13: the catalog whose circles are drawn: the Circles box, except that 'AAVSO sequence' on a field
        with no sequence (most exoplanet hosts) shows APASS (or Gaia) instead, so a solved image never looks
        unsolved. Cached per star list and choice (it is asked once per star on every redraw)."""
        choice = self.catalog.get()
        key = (id(self.chart_placed), len(self.chart_placed), choice)
        cached = getattr(self, "_circles_cache", None)
        if cached and cached[0] == key:
            return cached[1]
        shown = self._circles_catalog_uncached(choice)
        self._circles_cache = (key, shown)
        return shown

    def _circles_catalog_uncached(self, choice: str) -> str:
        if choice == "AAVSO sequence" and not any(st.get("seq") for _x, _y, st in self.chart_placed):
            have = {c for _x, _y, st in self.chart_placed for c in (st.get("sources") or {})}
            return next((c for c in ("APASS DR9", "Gaia DR3", "Tycho-2") if c in have), choice)
        return choice

    def _circle_shown(self, star) -> bool:
        """2.2.13: the Circles box chooses which catalog's circles are drawn; clicks find any catalog's star."""
        src = star.get("sources")
        return not src or self._circles_catalog() in src

    def _circle_label(self, star) -> str:
        """The label the chosen catalog gives this star (its magnitude to 0.1, or the AAVSO chart label)."""
        own = (star.get("sources") or {}).get(self._circles_catalog())
        return str((own or star).get("label", ""))

    def compass_vectors(self, shape):
        """2.2.13: unit vectors (in image pixels, y down) pointing north and east at the image centre, from the
        image's sky solution (header plate solution or SHOBS-P's own); None when the image is not solved. Follows
        the rotator and meridian flips because it comes from this image's solution."""
        sol = self.chart_solution
        if not sol or not self.chart_placed and not sol.get("auto"):
            return None
        try:
            h, w = shape
            cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
            ra, dec = core.pixel_to_sky(cx, cy, sol)
            step = 60.0 / 3600.0
            nx, ny = core.sky_to_pixel(ra, dec + step, sol)
            ex, ey = core.sky_to_pixel(ra + step / max(math.cos(math.radians(dec)), 0.05), dec, sol)
            vn, ve = np.array([nx - cx, ny - cy]), np.array([ex - cx, ey - cy])
            if np.linalg.norm(vn) < 1e-6 or np.linalg.norm(ve) < 1e-6:
                return None
            return vn / np.linalg.norm(vn), ve / np.linalg.norm(ve)
        except Exception:
            return None

    def _compass_corner(self, shape):
        """The least crowded corner of the frame (fewest marked and catalog stars in it), as axes fractions."""
        h, w = shape
        pts = [xy for xy in [self.target_xy, self.check_xy] + [getattr(self, f"{r}_xy") for r in COMP_ROLES] if xy]
        pts += [(x, y) for x, y, _s in self.chart_placed] + list(self.watch_xy.values())
        corners = {(0.09, 0.91): (0, 0), (0.91, 0.91): (w, 0), (0.09, 0.09): (0, h), (0.91, 0.09): (w, h)}
        def crowd(c):
            px, py = corners[c]
            return sum(1 for x, y in pts if abs(x - px) < 0.2 * w and abs(y - py) < 0.2 * h)
        # Ties: bottom-left first, where the title and hover notes are least likely.
        order = [(0.09, 0.09), (0.91, 0.09), (0.09, 0.91), (0.91, 0.91)]
        return min(order, key=crowd)

    def _draw_compass(self, shape):
        """2.2.13 (John): small N and E arrows in the least crowded corner, tied to Show labels. Drawn in axes
        fractions, so zoom and pan leave it in its corner; the directions come from the image's solution."""
        if not self.show_labels.get():
            return
        ax = self.photo_ax
        vec = self.compass_vectors(shape)
        cx, cy = self._compass_corner(shape)
        if vec is None:
            ax.text(cx, cy, "N/E: orientation unknown\nuntil the image is solved", transform=ax.transAxes,
                    ha="left" if cx < 0.5 else "right", va="bottom" if cy < 0.5 else "top", fontsize=7,
                    color="#cfcfcf", alpha=0.85)
            return
        # 2.2.17 (item 3): laid out in points from the corner (not axes fractions), so the arrows keep their true
        # directions whatever the axes' shape, and both letters stay inside the image: the whole compass (both arrows
        # and both letters, wherever the solution points them) is measured and tucked into the corner with a margin.
        # (2.2.16 put each letter 1.35 × the arrow length out, centred, which clipped "N" at the image edge.)
        from matplotlib.transforms import ScaledTranslation
        try:
            from matplotlib import patheffects
            halo = [patheffects.withStroke(linewidth=2.0, foreground="#000000", alpha=0.7)]   # readable on stars
        except Exception:
            halo = None
        for (px, py), _ha, _va, tag, (tx, ty) in self._compass_layout(vec, cx, cy):
            def at(x, y):
                return ax.transAxes + ScaledTranslation(x / 72.0, y / 72.0, self.photo_fig.dpi_scale_trans)
            corner = (0.0 if cx < 0.5 else 1.0, 0.0 if cy < 0.5 else 1.0)
            ax.annotate("", xy=corner, xycoords=at(tx, ty), xytext=corner, textcoords=at(px, py),
                        arrowprops=dict(arrowstyle="-|>", color="#ffd24a", lw=1.3, mutation_scale=9,
                                        shrinkA=0, shrinkB=0))
            _box, label_xy = self._compass_label_xy
            ax.text(corner[0], corner[1], tag, transform=at(*label_xy[tag]), ha="center", va="center",
                    fontsize=COMPASS_FONT_PT, fontweight="bold", color="#ffd24a", path_effects=halo)

    def _compass_layout(self, vec, cx, cy):
        """2.2.17 (item 3): the compass in points relative to the image corner it sits in (cx, cy are the corner as
        axes fractions from _compass_corner). Returns [((base x, y), ha, va, tag, (tip x, y))] per arrow and sets
        self._compass_label_xy = (box, {tag: (x, y)}): each letter's centre just past its arrow tip, and the box
        (x0, y0, x1, y1) all of it covers, which lies inside the image with a margin from both edges."""
        L, gap = COMPASS_ARROW_PT, COMPASS_FONT_PT * 0.95
        hw, hh = COMPASS_FONT_PT * 0.5, COMPASS_FONT_PT * 0.62   # half width / height of one bold capital
        parts = []
        xs, ys = [0.0], [0.0]
        for v, tag in ((vec[0], "N"), (vec[1], "E")):
            ux, uy = float(v[0]), -float(v[1])                     # image y points down; points point up
            norm = math.hypot(ux, uy) or 1.0
            ux, uy = ux / norm, uy / norm
            tip = (L * ux, L * uy)
            lab = ((L + gap) * ux, (L + gap) * uy)
            parts.append((tag, tip, lab))
            xs += [tip[0], lab[0] - hw, lab[0] + hw]
            ys += [tip[1], lab[1] - hh, lab[1] + hh]
        margin = COMPASS_MARGIN_PT
        # The base point so that the whole box sits `margin` in from the corner's two edges.
        bx = (margin - min(xs)) if cx < 0.5 else (-margin - max(xs))
        by = (margin - min(ys)) if cy < 0.5 else (-margin - max(ys))
        labels, out = {}, []
        for tag, tip, lab in parts:
            labels[tag] = (bx + lab[0], by + lab[1])
            out.append(((bx, by), "center", "center", tag, (bx + tip[0], by + tip[1])))
        box = (bx + min(xs), by + min(ys), bx + max(xs), by + max(ys))
        self._compass_label_xy = (box, labels)
        return out

    def _update_legend(self):
        """The legend strip under the Photometry image: one entry per kind of marker on the image now."""
        row = getattr(self, "legend_row", None)
        if row is None:
            return
        for child in row.winfo_children():
            child.destroy()
        items = []
        if self.target_xy is not None:
            items.append(("⊕", "Target", PICK_COLORS["target"]))
        comps = self._comp_roles_marked()
        if comps:
            nums = [comp_number(r) for r in comps]
            label = f"Comp C{nums[0]}" if len(nums) == 1 else (f"Comps C{nums[0]}–C{nums[-1]}" if nums == list(range(nums[0], nums[-1] + 1))
                                                   else "Comps " + ", ".join(f"C{n}" for n in nums))
            items.append(("⊕", label, PICK_COLORS["comp"]))
        if self.check_xy is not None:
            items.append(("⊕", "Check", PICK_COLORS["check"]))
        if (self.target_xy is not None or comps or self.check_xy is not None) and self.show_apertures.get():
            items.append(("◎", "Aperture and sky ring", "#7a7a7a"))
        if self.suggested_comps and self.mode_key != "discovery" and self._overlay("suggested"):
            items.append(("◯", "Suggested comp", "#2ca02c"))
        if self.spare_marks and self._overlay("spares"):
            items.append(("◇", "Spare comp", "#4fb3e0"))
        if self.watch_xy:
            items.append(("◇ W", "Watch", PICK_COLORS["watch"]))
        if (self._overlay("catalog") and self.mode_key != "discovery"
                and any(st.get("seq") for _x, _y, st in self.chart_placed)):
            # A darker shade of SEQ_COLOR: the legend strip is white, the image black.
            items.append(("○", "AAVSO sequence (label = V, e.g. 91 = 9.1)", "#1aa9b8"))
        seq_drawn = self.mode_key != "discovery"
        if self._overlay("catalog") and any(not st.get("variable") and self._circle_shown(st)
                                            and not (seq_drawn and st.get("seq"))
                                            for _x, _y, st in self.chart_placed):
            shown = self._circles_catalog()
            items.append(("○", f"{shown} star" + ("" if shown == self.catalog.get() else " (no AAVSO sequence here)")
                          + ("" if self.show_labels.get() else " (labels hidden)"), "#e0b000"))
        if self._overlay("variables") and any(st.get("variable") for _x, _y, st in self.chart_placed):
            items.append(("○", "Known variable", "#d32f2f"))
        if self._loose_variable_marks():
            items.append(("×", "Variable, not labeled", "#d32f2f"))
        kinds = {self._scan_kind(m) for m in self.scan_marks}
        for kind in ("new", "known", "caution", "unchecked"):
            if kind in kinds:
                st = SCAN_MARK_STYLES[kind]
                items.append(("□", st["legend"], st["color"]))
        if self.scan_excluded:
            items.append(("×", "Excluded from the scan", "#ff9800"))
        for symbol, text, colour in items:
            ttk.Label(row, text=f"{symbol} {text}", style="Card.TLabel", foreground=colour,
                      font=(core.UI_FONT, 9, "bold")).pack(side=LEFT, padx=(0, 12))
        ttk.Label(row, text="wheel: zoom · drag: pan · right-click a marker: clear", style="Hint.TLabel").pack(
            side=RIGHT)

    # ---- zoom and pan on the Photometry image (2.2) ----------------------------------------
    def reset_photo_view(self):
        self._photo_view = None
        self._photo_drawn_shape = None   # 2.2.3: really start from the whole frame (do not keep the zoom on screen)
        self.refresh_preview()

    def _store_photo_view(self):
        if self.preview is not None:
            self._photo_view = (tuple(self.photo_ax.get_xlim()), tuple(self.photo_ax.get_ylim()), self.preview.shape)

    def _photo_scroll(self, event):
        if self.preview is None or event.inaxes is not self.photo_ax or event.xdata is None:
            return
        self._photo_zoom_about(event.xdata, event.ydata, 1 / 1.3 if event.button == "up" else 1.3)

    def photo_zoom_step(self, factor: float):
        """2.2.16 (item 16, GitHub #2): the View box's − and + buttons: zoom about the middle of the current view
        (×1.5 per click), clamped like the wheel zoom."""
        if self.photo_canvas is None or self.preview is None:
            return
        x0, x1 = self.photo_ax.get_xlim()
        y0, y1 = self.photo_ax.get_ylim()
        self._photo_zoom_about((x0 + x1) / 2.0, (y0 + y1) / 2.0, factor)

    def _photo_zoom_about(self, cx: float, cy: float, factor: float):
        """Zoom the Photometry image by factor (< 1 zooms in) about (cx, cy): the wheel (2.2) and, 2.2.16 (item 16),
        the − / + buttons."""
        h, w = self.preview.shape
        x0, x1 = self.photo_ax.get_xlim()
        y0, y1 = self.photo_ax.get_ylim()
        nx0, nx1 = cx + (x0 - cx) * factor, cx + (x1 - cx) * factor
        ny0, ny1 = cy + (y0 - cy) * factor, cy + (y1 - cy) * factor
        if abs(nx1 - nx0) >= w or abs(ny1 - ny0) >= h:
            # Zoomed all the way out: back to the whole frame.
            self._photo_view = None
            self.photo_ax.set_xlim(-0.5, w - 0.5)
            self.photo_ax.set_ylim(h - 0.5, -0.5)
        elif abs(nx1 - nx0) < 8:
            return
        else:
            self.photo_ax.set_xlim(nx0, nx1)
            self.photo_ax.set_ylim(ny0, ny1)
            self._store_photo_view()
        self.photo_canvas.draw_idle()

    # ---- 2.2.16 (item 16, GitHub #2): the image in its own window ------------------------------------------------
    def _connect_photo_canvas(self, canvas):
        """The Photometry image's mouse handlers (click, drag, wheel, hover) on a canvas of photo_fig."""
        canvas.mpl_connect("button_press_event", self._photo_press_event)
        canvas.mpl_connect("button_release_event", self._photo_release_event)
        canvas.mpl_connect("scroll_event", self._photo_scroll)
        canvas.mpl_connect("motion_notify_event", self._photo_motion)

    def _virtual_screen(self):
        """(x, y, w, h) of the whole desktop (every monitor; x/y can be negative with a monitor on the left or above),
        or the primary screen when Tk cannot say."""
        try:
            sw, sh = int(self.winfo_screenwidth()), int(self.winfo_screenheight())
        except Exception:
            sw, sh = 1920, 1080
        try:
            vx, vy = int(self.winfo_vrootx()), int(self.winfo_vrooty())
            vw, vh = int(self.winfo_vrootwidth()), int(self.winfo_vrootheight())
            if vw >= sw and vh >= sh:
                return vx, vy, vw, vh
        except Exception:
            pass
        return 0, 0, sw, sh

    def _popout_geometry(self) -> str:
        """Where the pop-out opens: the size and place it had last time (settings), on whichever monitor that was;
        moved back to the main screen only when its title bar would be out of reach (review: a window kept on a second
        monitor stays there). The first time about 60% × 75% of the main screen, centred."""
        try:
            sw, sh = int(self.winfo_screenwidth()), int(self.winfo_screenheight())
        except Exception:
            sw, sh = 1920, 1080
        vx, vy, vw, vh = self._virtual_screen()
        saved = self.settings.get("photo_popout") if isinstance(self.settings.get("photo_popout"), dict) else {}
        default = (int(sw * 0.6), int(sh * 0.75))
        try:
            w, h = int(saved["w"]), int(saved["h"])
            x, y = int(saved["x"]), int(saved["y"])
        except (KeyError, TypeError, ValueError):
            w, h = default
            x, y = (sw - w) // 2, (sh - h) // 3
        w, h = min(max(w, 320), vw), min(max(h, 240), vh)
        # The title bar must be reachable: at least 100 px of it across, its top inside the desktop.
        reachable = (x + w - 100 > vx and x + 100 < vx + vw and vy <= y < vy + vh - 40)
        if not reachable:
            w, h = min(w, sw), min(h, sh)
            x, y = (sw - w) // 2, (sh - h) // 3
        return f"{w}x{h}+{x}+{y}"   # "+-1200" = 1200 px left of the main screen (a bare "-" would mean from the right)

    def _save_popout_geometry(self, win):
        import re
        geo = None
        try:
            # review: wm geometry round-trips exactly ("900x700+-1200+50" with a monitor on the left)
            m = re.match(r"^(\d+)x(\d+)\+?(-?\d+)\+?(-?\d+)$", str(win.geometry()).strip())
            if m:
                geo = dict(zip(("w", "h", "x", "y"), (int(v) for v in m.groups())))
        except Exception:
            geo = None
        if geo is None:
            try:
                geo = {"w": int(win.winfo_width()), "h": int(win.winfo_height()), "x": int(win.winfo_x()),
                       "y": int(win.winfo_y())}
            except Exception:
                return
        if geo["w"] < 50 or geo["h"] < 50:
            return   # not laid out (never shown)
        self.settings["photo_popout"] = geo
        self._save_settings()

    def _popout_moved(self, win):
        job = getattr(self, "_popout_save_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._popout_save_job = self.after(700, lambda: self._save_popout_geometry(win)
                                           if self.photo_popout and self.photo_popout["win"] is win else None)

    def photo_pop_out(self):
        """Move the image (one figure, nothing to sync) into its own resizable window: a second Tk canvas on the same
        figure, which becomes self.photo_canvas; clicks there mark stars as on the page. The page shows a Bring back
        line. The image comes back on close, Bring back, a run, a mode switch or leaving the Photometry step."""
        if getattr(self, "_photo_canvas_home", None) is None:
            return
        pop = self.photo_popout
        if pop is not None:
            try:
                pop["win"].deiconify()
                pop["win"].lift()
            except Exception:
                pass
            return
        win = Toplevel(self)
        win.title(f"{APP_TITLE} — Photometry image")
        win.geometry(self._popout_geometry())
        win.minsize(int(320 * ui_scale(self)), int(240 * ui_scale(self)))   # 2.2.16 (round-2 review): scaled
        legend = ttk.Frame(win, style="Card.TFrame", padding=(6, 2))
        legend.pack(side="bottom", fill=X)
        canvas = FigureCanvasTkAgg(self.photo_fig, master=win)   # the figure now draws here
        canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        canvas.get_tk_widget().bind("<Map>", lambda _e: self.after(50, self._fit_visible_canvases), add="+")
        home = self._photo_canvas_home
        if canvas.callbacks is not home.callbacks:
            # Older matplotlib keeps the handlers on the canvas; newer on the figure (already connected: once only).
            self._connect_photo_canvas(canvas)
        self._photo_press = None
        self.photo_popout = {"win": win, "canvas": canvas, "legend": legend}
        self.photo_canvas = canvas
        try:
            home.get_tk_widget().pack_forget()
            if getattr(self, "legend_row", None) is not None:
                self.legend_row.pack_forget()
            self.photo_placeholder.pack(side="top", anchor=W, pady=12)
        except Exception:
            pass
        self._legend_home = self.legend_row
        self.legend_row = legend   # the symbol legend goes with the image
        # 2.2.17 (item 2): the star buttons (same pick_mode variable as the page, so either place sets both), the
        # zoom buttons (moved from the page) and Go back, in a row above the image.
        bar = ttk.Frame(win, style="Card.TFrame", padding=(6, 4))
        bar.pack(side="top", fill=X, before=canvas.get_tk_widget())
        self.photo_popout["bar"] = bar
        self._build_popout_bar(bar)
        self._set_page_zoom(False)
        try:
            self.popout_button.configure(text="Bring back")
        except Exception:
            pass
        for seq, factor in (("<plus>", 1 / 1.5), ("<equal>", 1 / 1.5), ("<KP_Add>", 1 / 1.5),
                            ("<minus>", 1.5), ("<KP_Subtract>", 1.5)):
            win.bind(seq, lambda _e, f=factor: self.photo_zoom_step(f), add="+")   # keyboard zoom in the window
        win.protocol("WM_DELETE_WINDOW", self.photo_bring_back)
        # Remembered as it is moved or resized (shortly after), so quitting the app with the window open keeps it too.
        win.bind("<Configure>", lambda e, w=win: self._popout_moved(w) if e.widget is w else None, add="+")
        self._popout_fit_after(win, canvas)
        self.refresh_preview()
        self.status.set("The image is in its own window: resize it as you like. Close it (or Bring back) to put it "
                        "back on the page.")

    def photo_pop_toggle(self):
        """2.2.17 (item 2): the page's Pop out button; while the image is out it reads Bring back and does that."""
        if self.photo_popout is not None:
            self.photo_bring_back()
        else:
            self.photo_pop_out()

    def _set_page_zoom(self, shown: bool):
        """2.2.17 (item 2): show or hide the page's Zoom label and − + Fit buttons (they live in the pop-out while
        the image is out)."""
        if shown == getattr(self, "_page_zoom_shown", True):
            return
        self._page_zoom_shown = shown
        for widget, padx in getattr(self, "_page_zoom_widgets", []):
            try:
                if shown:
                    widget.pack(side=LEFT, padx=padx, before=self.popout_button)
                else:
                    widget.pack_forget()
            except Exception:
                pass

    def _build_popout_bar(self, bar):
        """2.2.17 (item 2): the pop-out's button row, mirroring the page for the current mode: Target / Comps / Check
        / Watch (Variables, Transits) or Watch it / Exclude it (Discovery) on the same pick_mode variable, Clear
        selected; − + Fit; Go back at the right."""
        for child in list(bar.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        # Go back and the zoom buttons are packed first, so a narrow window squeezes the star buttons, not these.
        ttk.Button(bar, text="Go back", command=self.photo_bring_back).pack(side=RIGHT)
        ttk.Button(bar, text="Fit", command=self.reset_photo_view).pack(side=RIGHT, padx=(2, 12))
        ttk.Button(bar, text="+", width=3, command=lambda: self.photo_zoom_step(1 / 1.5)).pack(side=RIGHT, padx=(2, 0))
        ttk.Button(bar, text="−", width=3, command=lambda: self.photo_zoom_step(1.5)).pack(side=RIGHT)
        ttk.Label(bar, text="Zoom", style="Card.TLabel").pack(side=RIGHT, padx=(12, 4))
        if self.mode_key == "discovery":
            ttk.Label(bar, text="Click a star to", style="Card.TLabel").pack(side=LEFT, padx=(4, 6))
            choices = (("●  Watch it", "watch", "watch"), ("✕  Exclude it", "exclude", "check"))
        else:
            choices = tuple(("●  " + label, value, value) for label, value in (
                ("Target", "target"), ("Comps (C1–C10)", "comp"), ("Check", "check"), ("Watch", "watch")))
        for text, value, style in choices:
            ttk.Radiobutton(bar, text=text, value=value, variable=self.pick_mode,
                            style=f"Pick{style}.TRadiobutton",
                            command=self._pick_chosen_by_user).pack(side=LEFT, padx=6)   # 2.2.18 (item 7)
        if self.mode_key != "discovery":
            ttk.Button(bar, text="Clear selected", command=self.clear_selected_mark).pack(side=LEFT, padx=(2, 6))
        self._popout_bar_mode = self.mode_key
        self._popout_bar_choices = [value for _t, value, _s in choices]

    def _popout_fit_after(self, win, canvas):
        try:
            win.update_idletasks()
            fit_figure_to_widget(self.photo_fig, canvas)
        except Exception:
            pass

    def photo_bring_back(self):
        """The image back on the Photometry page (the pop-out window closes); remembers the window's size and place."""
        pop = self.photo_popout
        if pop is None:
            return
        self.photo_popout = None
        self._save_popout_geometry(pop["win"])
        # 2.2.17 (item 2): the zoom buttons back on the page, the page's button reads Pop out again.
        self._set_page_zoom(True)
        try:
            self.popout_button.configure(text="Pop out")
        except Exception:
            pass
        home = self._photo_canvas_home
        self._photo_press = None
        self.photo_canvas = home
        try:
            self.photo_fig.set_canvas(home)   # the figure draws on the page's canvas again
        except Exception:
            pass
        self.legend_row = getattr(self, "_legend_home", None) or self.legend_row
        try:
            self.photo_placeholder.pack_forget()
            self.legend_row.pack(side="bottom", fill=X, pady=(2, 0))
            home.get_tk_widget().pack(fill=BOTH, expand=True)
        except Exception:
            pass
        try:
            pop["win"].destroy()
        except Exception:
            pass
        # A size or screen-scale change made in the window: fit the figure to the page's widget again, then redraw.
        self.after(50, self._fit_visible_canvases)
        self.refresh_preview()

    def _photo_press_event(self, event):
        if event.inaxes is not self.photo_ax or self.preview is None:
            return
        if event.button == 3:
            self.on_image_click(event)
            return
        if event.button in (1, 2):
            # A press starts a pan; a left press released without moving is a click (marks a star).
            self._photo_press = {"event": event, "x": event.x, "y": event.y, "moved": False,
                                 "xlim": self.photo_ax.get_xlim(), "ylim": self.photo_ax.get_ylim(),
                                 "inv": self.photo_ax.transData.inverted().frozen()}

    def _photo_motion(self, event):
        press = self._photo_press
        if press is not None and event.x is not None:
            if not press["moved"] and math.hypot(event.x - press["x"], event.y - press["y"]) < 6:
                return
            press["moved"] = True
            inv = press["inv"]
            sx, sy = inv.transform((press["x"], press["y"]))
            ex, ey = inv.transform((event.x, event.y))
            dx, dy = sx - ex, sy - ey
            self.photo_ax.set_xlim(press["xlim"][0] + dx, press["xlim"][1] + dx)
            self.photo_ax.set_ylim(press["ylim"][0] + dy, press["ylim"][1] + dy)
            self.photo_canvas.draw_idle()
            return
        self._on_photo_hover(event)

    def _photo_release_event(self, event):
        press = self._photo_press
        self._photo_press = None
        if press is None:
            return
        if press["moved"]:
            self._store_photo_view()
            return
        if press["event"].button == 1:
            self.on_image_click(press["event"])

    def _photo_hover_data(self, shape):
        """Hover sets for the Photometry image: marked stars first (they win ties), then spares, labels, scan marks."""
        h, w = shape
        sets = []
        marked = []
        rows = ([("Target", self.target_xy, self.star_id, None)]
                + [(role_label(r), getattr(self, f"{r}_xy"), getattr(self, f"{r}_name"), getattr(self, f"{r}_mag"))
                   for r in COMP_ROLES]
                + [("Check", self.check_xy, self.check_name, self.check_mag)])
        for role, xy, name_var, mag_var in rows:
            if xy is None:
                continue
            text = f"{role}: {name_var.get().strip() or '(no name)'}"
            if mag_var is not None and mag_var.get().strip():
                text += f"  mag {mag_var.get().strip()}"
            text += f"\npixel ({xy[0]:.1f}, {xy[1]:.1f})\nright-click to clear"
            marked.append((xy[0], xy[1], text))
        if marked:
            sets.append((self.photo_ax, np.array([m[0] for m in marked]), np.array([m[1] for m in marked]),
                         [m[2] for m in marked], 3.0))
        if self.watch_xy:
            names = list(self.watch_xy)
            info = {w["name"]: w for w in self.watch_list}
            sets.append((self.photo_ax, np.array([self.watch_xy[n][0] for n in names]),
                         np.array([self.watch_xy[n][1] for n in names]),
                         [f"Watch: {n}\n{self._star_text(info.get(n, {}))}"
                          + (f"\n{info[n]['variable']}" if info.get(n, {}).get("variable") else "")
                          + f"\npixel ({self.watch_xy[n][0]:.1f}, {self.watch_xy[n][1]:.1f})\nright-click to stop watching"
                          for n in names], 2.0))
        loose = self._loose_variable_marks()
        if loose:
            sets.append((self.photo_ax, np.array([m[0] for m in loose]), np.array([m[1] for m in loose]),
                         [self._variable_hover(v) for _x, _y, v in loose], 0.5))
        if self.spare_marks and self._overlay("spares"):
            sets.append((self.photo_ax, np.array([m[0] for m in self.spare_marks]),
                         np.array([m[1] for m in self.spare_marks]),
                         [f"Spare comp: {name}\n{self._star_text(self.spare_catalog.get(name, {}))}"
                          f"\npixel ({x:.1f}, {y:.1f})" for x, y, name in self.spare_marks], 1.0))
        stars = [(x, y, st) for x, y, st in self.chart_placed if 0 <= x < w and 0 <= y < h
                 and self._overlay("variables" if st.get("variable") else "catalog")]
        if stars:
            sets.append((self.photo_ax, np.array([s_[0] for s_ in stars]), np.array([s_[1] for s_ in stars]),
                         [f"{st.get('auid') or st.get('label') or 'catalog star'}\n"
                          f"{st.get('catalog', '')}: {self._star_text(st)}\npixel ({x:.1f}, {y:.1f})"
                          + (f"\n⚠ {core.variable_text(st)}" if st.get("variable") else "")
                          for x, y, st in stars], 0.0))
        if self.scan_marks:
            sets.append((self.photo_ax, np.array([m[0] for m in self.scan_marks]),
                         np.array([m[1] for m in self.scan_marks]),
                         [f"Scan candidate #{m[2]} — {SCAN_MARK_STYLES[self._scan_kind(m)]['legend'].lower()}"
                          + (f"\n{m[4]}" if len(m) > 4 and m[4] else "")
                          + f"\npixel ({m[0]:.1f}, {m[1]:.1f})" for m in self.scan_marks], 0.0))
        return sets

    def _aperture_sizes_changed(self):
        job = getattr(self, "_aperture_redraw_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._aperture_redraw_job = self.after(400, self._aperture_redraw)

    def _aperture_redraw(self):
        self._aperture_redraw_job = None
        if self.preview is None or not self.show_apertures.get() or not self._marked_roles():
            return
        try:
            if not (0 < float(self.radius.get()) and float(self.sky_in.get()) < float(self.sky_out.get())):
                return
        except Exception:
            return   # half-typed number: wait for the next keystroke
        self.refresh_preview()

    def _overlay(self, kind: str) -> bool:
        """2.2.5: whether a kind of mark is drawn on the Photometry image. "Candidates only" (Discovery) hides
        everything but scan candidates, watch stars and excluded stars, without changing the other switches, so
        turning it off brings back exactly what was shown before."""
        if self.mode_key == "discovery" and self.candidates_only.get():
            return False
        if kind == "catalog":
            return bool(self.show_catalog.get())
        if kind == "variables":
            return bool(self.show_variables.get())
        return True

    @staticmethod
    def _scan_kind(mark) -> str:
        return mark[3] if len(mark) > 3 and mark[3] in SCAN_MARK_STYLES else "new"

    @staticmethod
    def _variable_hover(v: dict) -> str:
        if v["source"] == "SIMBAD":
            return f"{v['source']}: {v['name']}\n{v['type']} ({v['desc']})"
        return f"{v['source']}: {v['name']}\n{v['desc']}"

    def _variable_reach(self) -> float:
        """Pixels matching the sky tolerance used to flag labeled stars (6", or 2 pixels if coarser)."""
        sol = self.chart_solution or {}
        try:
            scale = float(sol["scale"]) * float(sol.get("scale_factor", 1.0))
            return max(6.0, 2.0 * scale) / scale
        except Exception:
            try:
                return max(4.0, 0.6 * float(self.radius.get()))
            except Exception:
                return 5.0

    def _loose_variable_marks(self) -> list:
        """Variables to draw as a red ×: not on a labeled star (those already get a red circle), and only
        when Show variables is on."""
        if not self._overlay("variables"):
            return []
        circled = {id(v) for _x, _y, st in self.chart_placed for v in st.get("variable") or []}
        return [(x, y, v) for x, y, v in self.variable_marks if id(v) not in circled]

    def _variables_at(self, xy) -> list:
        """Known or suspected variables at a pixel position, whatever catalog the chart was labeled with."""
        if xy is None:
            return []
        reach = self._variable_reach()
        hits = [(math.hypot(x - xy[0], y - xy[1]), v) for x, y, v in self.variable_marks]
        return [v for d, v in sorted(hits, key=lambda h: h[0]) if d <= reach]

    @staticmethod
    def _star_text(star: dict) -> str:
        mags = dict(star.get("mags") or {})
        if not mags and star.get("mag") is not None:
            mags = {"V": star["mag"]}
        parts = [f"{b} {mags[b]:.2f}" for b in ("B", "V", "R") if mags.get(b) is not None]
        # 2.2.17 (item 1): Sloan magnitudes (only a Sloan run's catalog stars carry them)
        parts += [f"{core.band_label(b)} {mags[b]:.2f}" for b in core.SLOAN_BANDS if mags.get(b) is not None]
        # 2.2.14 (item 1): the color from one catalog, and which one (B and V above may come from two catalogs).
        bv, src = core.catalog_color(star, "bv")
        if bv is not None:
            parts.append(f"(B-V {bv:.2f}" + (f", {src})" if src else ")"))
        text = "  ".join(parts) or "no magnitudes"
        if star.get("fainter_than_target") is not None and star.get("widened"):
            # 2.2.14 (item 6): a spare from a widened window says how much fainter than the target it is.
            text += "\n" + _fainter_text(star["fainter_than_target"])
        return text

    def clear_mark(self, role: str, redraw: bool = True):
        """Remove a marked star and empty its fields (and its stored catalog colors). redraw=False when clearing
        several in a row; the caller redraws once (2.2.5: a full-frame redraw per role was slow)."""
        if role == "target":
            self.target_xy = None
        elif role in COMP_ROLES or role == "check":
            setattr(self, f"{role}_xy", None)
            getattr(self, f"{role}_name").set("")
            getattr(self, f"{role}_mag").set("")
        self.star_bands.pop(role, None)
        getattr(self, "_slot_typed", {}).pop(role, None)   # 2.2.16 (item 8, review)
        if role != "target":
            self._update_chart_field()
        if not redraw:
            return
        self._update_pick_label()
        self.refresh_preview()
        self.update_star_panel()
        self.status.set(f"{role_label(role)} cleared" + ("" if role == "target" else " (and its fields)") + ".")

    def clear_selected_mark(self):
        mode = self.pick_mode.get()
        if mode == "comp":
            # 2.2.5: the Comps button adds comps one after another; Clear selected removes the last one marked.
            used = self._comp_roles_used()
            if used:
                self.clear_mark(used[-1])
            return
        if mode == "watch":
            self._clear_selected_watch()   # 2.2.18 (item 19)
            return
        if mode == "exclude":
            return
        self.clear_mark(mode)

    def _clear_selected_watch(self):
        """2.2.18 (item 19): Clear selected with Watch picked stops watching the star selected in the Watch stars list
        (Discovery: its row in Watched and excluded stars); with no row selected, the watch star added last that is on
        this frame (as Comps clears the last comp marked). remove_watch updates the lists, the image and the pop-out."""
        name = None
        try:
            sel = self.watch_box.curselection() if hasattr(self, "watch_box") else ()
            if sel and sel[0] < len(self.watch_list):
                name = self.watch_list[sel[0]]["name"]
        except Exception:
            name = None
        if name is None and hasattr(self, "disc_box"):
            try:
                sel = self.disc_box.curselection()
                rows = getattr(self, "_disc_rows", [])
                if sel and sel[0] < len(rows) and rows[sel[0]][0] == "watch":
                    name = rows[sel[0]][1]
            except Exception:
                name = None
        if name is None:
            on_frame = [w["name"] for w in self.watch_list if w["name"] in self.watch_xy]
            name = on_frame[-1] if on_frame else None
        if name is None:
            self.status.set("No watch star on this frame to clear.")
            return
        self.remove_watch(name)
        self._update_pick_label()

    # ---- comparison stars C1-C10 (2.2.5) -------------------------------------------------
    def _comp_roles_used(self) -> list:
        """Comp roles with a marker or a name, in order (C1, C2, ...)."""
        return [r for r in COMP_ROLES if getattr(self, f"{r}_xy") is not None or getattr(self, f"{r}_name").get().strip()]

    def _comp_roles_marked(self) -> list:
        return [r for r in COMP_ROLES if getattr(self, f"{r}_xy") is not None]

    def _next_comp_role(self, xy=None):
        """The comp slot a Comps click fills, or None when all ten are marked. A loaded series (or new lights) leaves
        comp names with no marker: clicking the star with that name puts it back in its own slot (C1 stays C1); any
        other star goes to the first slot with neither marker nor name, then to the first slot without a marker."""
        if not self.chart_placed:
            # 2.2.8: before Solve a click cannot be matched to a saved name, so it takes the first slot without
            # a marker (C1 first), keeping that slot's leftover name until Solve checks it. Skipping a slot
            # that still held last night's name left a ghost C1 and put the real comp in C2 (John, 9 Oct).
            for r in COMP_ROLES:
                if getattr(self, f"{r}_xy") is None:
                    return r
            return None
        if xy is not None:
            star = self._chart_star_at(xy)
            name = str((star or {}).get("auid") or (star or {}).get("label") or "").strip()
            if name:
                for r in COMP_ROLES:
                    if getattr(self, f"{r}_xy") is None and getattr(self, f"{r}_name").get().strip() == name:
                        return r
        for r in COMP_ROLES:
            if getattr(self, f"{r}_xy") is None and not getattr(self, f"{r}_name").get().strip():
                return r
        for r in COMP_ROLES:
            if getattr(self, f"{r}_xy") is None:
                return r
        return None

    def _marked_roles(self) -> list:
        """(role, xy) for the target, every marked comp, and the check star."""
        out = [("target", self.target_xy)] + [(r, getattr(self, f"{r}_xy")) for r in COMP_ROLES] + [("check", self.check_xy)]
        return [(r, xy) for r, xy in out if xy is not None]

    def _mark(self, xy, color, tag):
        if not xy:
            return
        from matplotlib.patches import Circle
        x, y = xy
        try:
            r, s_in, s_out = float(self.radius.get()), float(self.sky_in.get()), float(self.sky_out.get())
        except Exception:
            r, s_in, s_out = 8.0, 0.0, 0.0
        if self.show_apertures.get():
            # 2.2.5: the aperture (solid) and the sky ring (dashed inner, dotted outer) at the sizes in the boxes now.
            self.photo_ax.add_patch(Circle((x, y), r, fill=False, color=color, lw=1.2))
            if 0 < s_in < s_out:
                self.photo_ax.add_patch(Circle((x, y), s_in, fill=False, color=color, lw=0.7, ls="--"))
                self.photo_ax.add_patch(Circle((x, y), s_out, fill=False, color=color, lw=0.7, ls=":"))
        self.photo_ax.plot(x, y, marker="+", color=color, ms=10)
        self.photo_ax.text(x + (s_out if self.show_apertures.get() and s_out > r else r) * 0.72 + 3, y - 3, tag,
                           color=color, fontsize=9, fontweight="bold")

    def _rescale_marks(self, old_shape, new_shape):
        """2.2.3: the image changed size (binning changed): move every marker to the same star on the new image,
        so markers never sit at the old pixel positions."""
        sx = new_shape[1] / float(old_shape[1])
        sy = new_shape[0] / float(old_shape[0])
        if abs(sx - sy) > 0.02 * max(sx, sy):
            # Not a plain binning change (a different camera or crop): the old positions mean nothing here.
            self._clear_marks()
            self.log("The image changed shape, not just size: markers cleared. Mark the stars again.")
            return

        def move(xy):
            return None if xy is None else ((xy[0] + 0.5) * sx - 0.5, (xy[1] + 0.5) * sy - 0.5)
        moved = 0
        for role in ("target",) + COMP_ROLES + ("check",):
            xy = getattr(self, f"{role}_xy")
            if xy is not None:
                setattr(self, f"{role}_xy", move(xy))
                moved += 1
        self.watch_xy = {k: move(v) for k, v in self.watch_xy.items()}
        self.scan_excluded = [move(v) for v in self.scan_excluded]
        # The variable-star × marks too: they were placed on the old image.
        self.variable_marks = [(*move((x, y)), v) for x, y, v in self.variable_marks]
        if self.spare_marks:
            self.log("Spare comps cleared with the binning change; Solve again to pick spares on this image.")
        self.spare_marks = []
        if moved:
            self.log(f"Binning changed: {moved} marker(s) moved to the new image (x{sx:g}). Check them before running.")

    def on_image_click(self, event):
        if event.xdata is None or event.ydata is None or self.preview is None:
            return
        if getattr(event, "button", 1) == 3:
            # Right-click: clear the nearest marked star.
            best = None
            marks = self._marked_roles() + [("watch:" + name, xy) for name, xy in self.watch_xy.items()] \
                + [(f"exclude:{k}", xy) for k, xy in enumerate(self.scan_excluded)]
            for role, xy in marks:
                if xy is None:
                    continue
                d = math.hypot(xy[0] - event.xdata, xy[1] - event.ydata)
                if best is None or d < best[0]:
                    best = (d, role)
            try:
                reach = max(2.0 * float(self.radius.get()), 12.0)
            except Exception:
                reach = 16.0
            if best is not None and best[0] <= reach:
                if best[1].startswith("watch:"):
                    self.remove_watch(best[1][6:])
                elif best[1].startswith("exclude:"):
                    del self.scan_excluded[int(best[1][8:])]
                    self.refresh_preview()
                    self._update_discovery_panel()
                    self.status.set("Star back in the scan.")
                else:
                    self.clear_mark(best[1])
            else:
                self.status.set("Right-click on a marked star (T, C1…C10, K, W) to clear it.")
            return
        if not self.preview_is_debayered:
            messagebox.showinfo(APP_TITLE, "Calibrate first (Calibrate page). Star positions are measured on the calibrated, debayered image.")
            return
        xy = (float(event.xdata), float(event.ydata))
        click_xy = xy
        snap_note = ""
        try:
            radius = float(self.radius.get())
            # 2.2.8: snap to the star nearest the click (not the brightest one nearby), sized by the seeing.
            seeing = self._seeing()
            info = core.recenter_info(self.preview, xy[0], xy[1], radius, seeing if math.isfinite(seeing) else None)
            pick = self.pick_mode.get()
            if not info["found"] and (pick in ("comp", "check") or comp_number(pick)):
                # 2.2.18 (item 5): a comp or check is a star you can see; a click a little further out than 2.5 FWHM
                # (sharp stars, the whole frame in view) still finds it: 1.5 x the aperture radius, as the pre-run check.
                info = core.recenter_info(self.preview, xy[0], xy[1], radius,
                                          seeing if math.isfinite(seeing) else None, search=1.5 * radius)
            xy = (info["x"], info["y"])
            if not info["found"]:
                snap_note = " No star found right at the click; the marker is where you clicked."
            elif info["shift"] >= 1.0:
                snap_note = f" Centred on the star ({info['shift']:.1f} px from the click)."
        except Exception:
            pass
        mode = self.pick_mode.get()
        if mode == "watch":
            self.add_watch_at(xy)
            self._update_discovery_panel()
            return
        if mode == "exclude":
            self.scan_excluded.append(xy)
            self.refresh_preview()
            self._update_discovery_panel()
            star = self._chart_star_at(xy)
            name = (star or {}).get("auid") or (star or {}).get("label") or f"the star at ({xy[0]:.0f}, {xy[1]:.0f})"
            self.status.set(f"{name} is left out of field scans (right-click its orange × to bring it back).")
            return
        if self.mode_key == "discovery":
            return
        # 2.2.14 (item 3): a click on (or right next to) a spare comp's blue diamond adopts that spare: its own record
        # (name, V, bands, colors, catalog), not whatever labeled star happens to be nearest the click.
        spare = self._spare_at(click_xy, xy) if (mode in ("comp", "check") or comp_number(mode)) else None
        if spare is not None:
            xy = spare[1]
            snap_note = ""
        if mode == "comp" or comp_number(mode):
            # 2.2.5: each click with Comps adds the next comparison star (C1, C2 ... C10). Clicking a star that is
            # already a comp does nothing new.
            for r in COMP_ROLES:
                old = getattr(self, f"{r}_xy")
                if old is not None and math.hypot(old[0] - xy[0], old[1] - xy[1]) < 2.0:
                    self.status.set(f"That star is already {role_label(r)}.")
                    return
            mode = self._next_comp_role(xy)
            if spare is not None:
                # A slot left from the series under this spare's name gets it back (as _next_comp_role does by name).
                mode = next((r for r in COMP_ROLES if getattr(self, f"{r}_xy") is None
                             and getattr(self, f"{r}_name").get().strip() == spare[0]), mode)
            if mode is None:
                messagebox.showinfo(APP_TITLE, f"All {MAX_COMPS} comparison stars are marked. Right-click one to "
                                               "remove it first.")
                return
        if mode == "target":
            self.target_xy = xy
            if self.chart_placed:
                self._suggest_comps()   # 2.2.13: the image is usually solved before the target is clicked
        elif comp_number(mode):
            setattr(self, f"{mode}_xy", xy)
        else:
            self.check_xy = xy
        filled = self._fill_from_spare(mode, spare[0]) if spare is not None else self._fill_from_chart(mode, xy)
        self._update_pick_label()
        self.refresh_preview()
        self.update_star_panel()
        if filled:
            self.status.set(filled + snap_note)
        elif snap_note:
            self.status.set(snap_note.strip())
        no_v = self._no_catalog_v_note(mode) if comp_number(mode) else ""
        if no_v:
            # 2.2.14 (item 4): say it when the comp is marked, not only when Run photometry refuses.
            self.status.set(f"{role_label(mode)}: {no_v}")
            self.log(f"{role_label(mode)} at ({xy[0]:.0f}, {xy[1]:.0f}): {no_v}")
        if comp_number(mode) or mode == "check":
            self._vet_choice(mode, xy)

    def _no_catalog_v_note(self, role: str) -> str:
        """2.2.14 (item 4): why a marked comp has no catalog V on a solved image ('' when it has one, or when the image
        is not solved yet: then the magnitude fills in at Solve). Without a solve a typed V works as before."""
        if not comp_number(role) or getattr(self, f"{role}_xy") is None or not self.chart_placed:
            return ""
        if getattr(self, f"{role}_mag").get().strip():
            return ""
        star = self._chart_star_at(getattr(self, f"{role}_xy"))
        band = (star or {}).get("band") or self._band()   # 2.2.14 (review): B or R with a mono filter, not always V
        band = core.band_label(band)   # 2.2.17 (item 1)
        if star is not None and star.get("mag") is None:
            name = star.get("auid") or star.get("label") or "this star"
            return (f"{name} has no {band} in {star.get('catalog', 'the catalogs')}; type its {band} or "
                    "pick another star")
        return f"not in the downloaded catalogs; type its {band} or pick another star"

    def _slot_lookup(self, role: str):
        """2.2.13 (John): a name or RA/Dec typed into a comp or check slot + Enter puts the marker on that star: the
        solved image's catalog stars first (any catalog's name), then coordinates, then SIMBAD/VSX. A slot that
        already has a marker is only relabelled (typing changes the name; nothing moves)."""
        name_var = getattr(self, f"{role}_name")
        text = name_var.get().strip()
        if not text or self.mode_key == "discovery":
            return
        if getattr(self, f"{role}_xy") is not None:
            # 2.2.14 (item 14): say what Enter did here (it only renamed the marked star).
            self._slot_say(role, f"✓ {role_label(role)} renamed to {text}; the marker stays where it is (✕ clears it, "
                                 "then Enter places the name).")   # 2.2.16 (item 8): every outcome ✓ or ⚠
            return
        if self.preview is None or not self.preview_is_debayered:
            self._slot_say(role, f"⚠ Calibrate first, so {text} can be found on the image.", bad=True)
            return
        star = self._chart_star_named(text)
        if star is not None and self._chart_xy_of(star) is not None:
            self._set_slot(role, self._chart_xy_of(star), text)
            return
        coords = core.parse_coordinates(text)
        if coords is not None:
            self._place_slot_sky(role, text, coords[0], coords[1])
            return
        # 2.2.14 (item 14): the merged catalogs' positions before SIMBAD, which does not know "APASS J…" names: a
        # J-designation carries its own coordinates.
        coords = designation_coords(text)
        if coords is not None and self.chart_solution:
            self._place_slot_sky(role, text, coords[0], coords[1], reach=WATCH_SAME_ARCSEC)
            return
        if not self.chart_solution:
            self._slot_not_solved(text, role)
            return
        self._slot_say(role, f"Looking up {text} in SIMBAD and VSX…")

        def work():
            try:
                found = core.lookup_target(text)
                self.call_ui(self._place_slot_sky, role, text, found["ra_hours"] * 15.0, found["dec_deg"])
            except Exception as exc:
                self.call_ui(self._slot_lookup_failed, role, text, exc)

        threading.Thread(target=work, daemon=True).start()

    def _slot_lookup_failed(self, role: str, text: str, exc):
        if getattr(self, f"{role}_name").get().strip() != text:
            return
        self._slot_say(role, f"⚠ {text} not found (not in this image's catalogs, and SIMBAD/VSX said: {exc}). Check "
                             "the name, type RA Dec, or click the star on the image.", bad=True)

    def _slot_say(self, role: str, text: str, bad: bool = False):
        """2.2.14 (item 14): every outcome of a typed name goes to the status bar, the log, and the slot's own hint
        line (until the name in the slot changes)."""
        self.status.set(text)
        self.log(f"{role_label(role)}: {text}")
        self._slot_notes[role] = (getattr(self, f"{role}_name").get().strip(), text, bad)
        self.update_star_panel()

    def _slot_not_solved(self, text, role: str | None = None):
        msg = (f"The image is not solved yet, so {text} cannot be placed from its name or position. Click the star "
               "on the image (Solve again if the solve failed).")
        if role is not None:
            self._slot_say(role, "⚠ " + msg, bad=True)   # 2.2.16 (item 8)
        else:
            self.status.set(msg)

    def _place_slot_sky(self, role: str, text: str, ra: float, dec: float, reach: float = 5.0):
        if getattr(self, f"{role}_xy") is not None or getattr(self, f"{role}_name").get().strip() != text:
            return   # filled or changed meanwhile
        if not self.chart_solution or self.preview is None:
            self._slot_not_solved(text, role)
            return
        best = None
        for x, y, st in self.chart_placed:
            if st.get("ra") is None:
                continue
            d = core.sky_separation_arcsec(ra, dec, st["ra"], st["dec"])
            if d < reach and (best is None or d < best[0]):
                best = (d, (x, y))
        xy = best[1] if best else self._project_to_frame(ra, dec)
        if xy is None:
            self._slot_say(role, f"⚠ {text} is not on this frame ({core.sexagesimal_ra(ra / 15.0)} "
                                 f"{core.sexagesimal_dec(dec)}).", bad=True)
            return
        self._set_slot(role, xy, text)

    def _snap_to_star(self, xy):
        """2.2.18 (item 5): a marker placed from a name or sky position sits where the plate solution puts the star,
        which for a star the solve did not match can be a few px off. Centre it on the star there (as a click is) when
        one is within about 1.5 FWHM; otherwise keep the position."""
        if xy is None or self.preview is None or not self.preview_is_debayered:
            return xy
        try:
            radius = float(self.radius.get())
            seeing = self._seeing()
            fwhm = seeing if math.isfinite(seeing) else None
            info = core.recenter_info(self.preview, float(xy[0]), float(xy[1]), radius, fwhm)
            reach = max(2.0, 1.5 * fwhm) if fwhm else max(2.0, 0.5 * radius)
            if info.get("found") and not info.get("blended") and info["shift"] <= reach:
                return (float(info["x"]), float(info["y"]))
        except Exception:
            pass
        return xy

    def _set_slot(self, role: str, xy, text: str):
        others = [("target", self.target_xy)] + [(o, getattr(self, f"{o}_xy")) for o in list(COMP_ROLES) + ["check"]]
        for other, oxy in others:
            if other != role and oxy is not None and math.hypot(oxy[0] - xy[0], oxy[1] - xy[1]) < 2.0:
                self._slot_say(role, f"⚠ {text} is already marked as {role_label(other)}.", bad=True)   # 2.2.14 (14)
                return
        xy = self._snap_to_star(xy)   # 2.2.18 (item 5): a catalog position is centred on its star like a click
        setattr(self, f"{role}_xy", (float(xy[0]), float(xy[1])))
        filled = self._fill_from_chart(role, xy)
        if not filled or "=" not in filled:
            getattr(self, f"{role}_name").set(text)
        self._update_pick_label()
        self.refresh_preview()
        msg = ((filled if filled else f"{role_label(role)} placed on {text}; type its magnitude.")
               + f"  Marker at ({xy[0]:.0f}, {xy[1]:.0f}).")
        bad = bool(filled) and "=" not in filled   # placed, but its catalog has no magnitude for it
        self._slot_say(role, ("⚠ " if bad else "✓ ") + msg, bad=bad)   # 2.2.14 (item 14)
        self._vet_choice(role, xy)

    def _chart_star_at(self, xy, reach: float = 8.0):
        best = None
        for x, y, star in self.chart_placed:
            d = math.hypot(x - xy[0], y - xy[1])
            if d < reach and (best is None or d < best[0]):
                best = (d, star)
        return best[1] if best else None

    def _chart_star_named(self, name: str):
        """The labeled star answering to this name in any catalog (2.2.13: Tycho, APASS, Gaia, AAVSO AUID or label)."""
        if not name:
            return None
        exact, by_key, by_core = self._chart_name_lookup()
        star = exact.get(" ".join(name.lower().split()))
        if star is not None:
            return star
        # 2.2.14 (item 14): tolerant of spacing ("TYC4006-1310-1") and of a catalog prefix typed or left out
        # ("J231144.7+570632" for "APASS J231144.7+570632"); only a single match counts.
        for keyfn, table in ((name_key, by_key), (name_core, by_core)):
            want = keyfn(name)
            if len(want) < 4:
                continue
            hits = table.get(want) or []
            if len(hits) == 1:
                return hits[0]
        return None

    def _chart_name_lookup(self):
        """2.2.14 (item 14, review): the labeled stars' names, indexed once per set of labeled stars (exact,
        spacing-free, prefix-free), so a name that is not there costs a dict lookup, not three passes over thousands of
        catalog stars on every panel refresh. Rebuilt when chart_placed is replaced or changes length."""
        sig = (self.chart_placed, len(self.chart_placed))
        cache = getattr(self, "_chart_names_cache", None)
        if cache is not None and cache[0][0] is sig[0] and cache[0][1] == sig[1]:   # the list itself, not its id
            return cache[1]
        exact, by_key, by_core = {}, {}, {}
        for _x, _y, star in self.chart_placed:
            for n in core.star_names(star):
                exact.setdefault(" ".join(n.lower().split()), star)
                for keyfn, table in ((name_key, by_key), (name_core, by_core)):
                    hits = table.setdefault(keyfn(n), [])
                    if not any(h is star for h in hits):
                        hits.append(star)
        self._chart_names_cache = (sig, (exact, by_key, by_core))
        return exact, by_key, by_core

    def _chart_xy_of(self, star):
        for x, y, st in self.chart_placed:
            if st is star:
                return (x, y)
        return None

    def _vet_choice(self, role: str, xy):
        """Warn when a star chosen as comp or check is a known or suspected variable."""
        star = self._chart_star_at(xy)
        hits = (star or {}).get("variable") or self._variables_at(xy)
        if not hits:
            return
        what = "check star" if role == "check" else f"comparison star ({role_label(role)})"
        label = (star or {}).get("auid") or (star or {}).get("label") or "This star"
        if not messagebox.askyesno(
            APP_TITLE,
            f"{label} is a known or suspected variable:\n  {core.variable_text({'variable': hits})}\n\n"
            f"A variable {what} shifts every measurement it touches. Use it anyway?",
        ):
            self.clear_mark(role)

    # ---- star panel (Photometry page) ---------------------------------------------------
    def _build_star_panel(self, panel):
        stars = self._group(panel, "Stars")
        stars.pack(fill=X)
        ttk.Label(stars, text="Star ID", style="Hint.TLabel").grid(row=0, column=1, sticky=W)
        ttk.Label(stars, textvariable=self.mag_header, style="Hint.TLabel").grid(row=0, column=2, sticky=W, padx=(6, 0))
        self.star_status = {}
        self.star_status_labels = {}
        self.star_row_widgets = {}
        # 2.2.5: Comp 1 … Comp 10. Comp 1 always shows; the others appear as they are marked.
        rows = ([("target", "Target", self.star_id, None)]
                + [(r, role_label(r), getattr(self, f"{r}_name"), getattr(self, f"{r}_mag")) for r in COMP_ROLES]
                + [("check", "Check", self.check_name, self.check_mag)])
        for i, (role, label, name_var, mag_var) in enumerate(rows):
            r = 1 + 2 * i
            widgets = []
            w = ttk.Label(stars, text="● " + label, style=f"Pick{'comp' if comp_number(role) else role}.TLabel")
            w.grid(row=r, column=0, sticky=W, padx=(0, 6))
            widgets.append(w)
            w = ttk.Entry(stars, textvariable=name_var, width=30)
            w.grid(row=r, column=1, sticky="ew", pady=(4, 0))
            if role != "target":
                # 2.2.13 (John): type a name or RA/Dec in an empty slot and press Enter to put the marker on it.
                w.bind("<Return>", lambda e, k=role: self._slot_lookup(k))
                w.bind("<KP_Enter>", lambda e, k=role: self._slot_lookup(k))   # 2.2.14 (item 14): keypad Enter
                # 2.2.16 (item 8): a key in the box marks the name that follows as typed here (waiting for Enter),
                # not left from an earlier run. The widget binding runs before the Entry inserts the character.
                w.bind("<Key>", lambda e, k=role: self._slot_key(k), add="+")
            widgets.append(w)
            if mag_var is not None:
                w = ttk.Entry(stars, textvariable=mag_var, width=8)
                w.grid(row=r, column=2, sticky=W, padx=(6, 0), pady=(4, 0))
                widgets.append(w)
            w = ttk.Button(stars, text="✕", width=3, command=lambda k=role: self.clear_mark(k))
            w.grid(row=r, column=3, sticky=W, padx=(4, 0), pady=(4, 0))
            widgets.append(w)
            var = StringVar(value="")
            lab = ttk.Label(stars, textvariable=var, style="Hint.TLabel", wraplength=450, justify=LEFT)
            lab.grid(row=r + 1, column=0, columnspan=4, sticky=W, padx=(14, 0))
            widgets.append(lab)
            self.star_status[role] = var
            self.star_status_labels[role] = lab
            self.star_row_widgets[role] = widgets
            name_var.trace_add("write", lambda *_, k=role: self._slot_name_changed(k))   # 2.2.16 (item 8)
        stars.columnconfigure(1, weight=1)
        self.field_check_text = StringVar(value="Variable-star check: Solve to check the field against VSX and SIMBAD.")
        ttk.Label(stars, textvariable=self.field_check_text, style="Hint.TLabel", wraplength=460, justify=LEFT).grid(
            row=1 + 2 * len(rows), column=0, columnspan=4, sticky=W, pady=(8, 0))

        watch = self._group(panel, "Watch stars")
        watch.pack(fill=BOTH, expand=True, pady=(10, 0))
        ttk.Label(watch, text="Pick Watch and click a star, or Add a star… by name or RA/Dec. Watch stars are measured "
                              "every run, found again after Solve on later nights, and have their own light "
                              "curves (Output page).",
                  style="Hint.TLabel", wraplength=460, justify=LEFT).pack(anchor=W)
        self.watch_box = Listbox(watch, height=6, font=(core.UI_FONT, 9), activestyle="none", exportselection=False)
        self.watch_box.pack(fill=BOTH, expand=True, pady=(4, 4))
        row = ttk.Frame(watch, style="Card.TFrame")
        row.pack(fill=X)
        ttk.Button(row, text="Add a star…", command=self.add_watch_dialog).pack(side=LEFT)
        ttk.Button(row, text="Remove selected", command=self._remove_selected_watch).pack(side=LEFT, padx=6)
        ttk.Button(row, text="Watch light curves…", command=self.show_watch).pack(side=RIGHT)

    def _slot_key(self, role: str):
        """2.2.16 (item 8): a key was pressed in a comp/check name box; the change it makes is the user's typing."""
        self._slot_keying = role
        try:
            self.after_idle(self._slot_key_done)
        except Exception:
            pass

    def _slot_key_done(self):
        self._slot_keying = None

    def _slot_name_changed(self, role: str):
        """2.2.16 (item 8): a slot's name changed; remember it as typed (for this image) when a key in that box
        changed it, or (review) the box has the keyboard focus, which also covers a paste from the mouse or the
        context menu. Code that sets a name never focuses the box."""
        if role != "target" and (self._slot_keying == role or self._slot_box_focused(role)):
            self._slot_typed[role] = (getattr(self, "_preview_token", 0), getattr(self, f"{role}_name").get().strip())
            self._slot_keying = None
        self.update_star_panel()

    def _slot_box_focused(self, role: str) -> bool:
        try:
            entry = self.star_row_widgets[role][1]
            return self.focus_get() is entry
        except Exception:
            return False

    def update_star_panel(self):
        """Catalog color, red-color warning, and variable-star check for the target, comps, and check star."""
        if not hasattr(self, "star_status"):
            return
        checked = bool(self.field_check_sources)
        # 2.2.5: show Comp 2 … Comp 10 only when they are in use; 2.2.13: plus one empty slot after the last one, to
        # type a star into.
        used_list = self._comp_roles_used()
        used = set(used_list)
        last = max((COMP_ROLES.index(r) for r in used_list), default=-1)
        next_slot = COMP_ROLES[last + 1] if last + 1 < len(COMP_ROLES) else None
        if next_slot:
            used.add(next_slot)
        for role in COMP_ROLES[1:]:
            for w in self.star_row_widgets.get(role, []):
                try:
                    if role in used:
                        w.grid()
                    else:
                        w.grid_remove()
                except Exception:
                    pass
        roles = ([("target", self.star_id, self.target_xy)]
                 + [(r, getattr(self, f"{r}_name"), getattr(self, f"{r}_xy")) for r in COMP_ROLES]
                 + [("check", self.check_name, self.check_xy)])
        for role, name_var, xy in roles:
            name = name_var.get().strip()
            var, lab = self.star_status[role], self.star_status_labels[role]
            if not name and xy is None:
                var.set("type a name or RA/Dec and press Enter, or click the star"
                        if role == next_slot and self.preview is not None and self.mode_key != "discovery" else "")
                lab.configure(style="Hint.TLabel")
                continue
            # 2.2.14 (item 14): what Enter on a typed name did, until the name in the slot changes.
            slot_note = self._slot_notes.get(role) if hasattr(self, "_slot_notes") else None
            if slot_note and slot_note[0] != name:
                slot_note = None
            if slot_note and xy is None:
                var.set(slot_note[1])
                lab.configure(style="Warn.TLabel" if slot_note[2] else "Hint.TLabel")
                continue
            if (name and xy is None and role != "target" and self.mode_key != "discovery"
                    and getattr(self, "_slot_typed", {}).get(role) == (getattr(self, "_preview_token", 0), name)):
                # 2.2.16 (item 8): typed here and not yet looked up; the "not marked" warning is for names left from
                # an earlier run or series.
                var.set("press Enter to find and mark this star")
                lab.configure(style="Hint.TLabel")
                continue
            if name and xy is None and role != "target" and self.preview is not None:
                # 2.2.3: a name left from an earlier run with no marker on this image.
                var.set("⚠ not marked on this image: click the star, or ✕ to clear the name")
                lab.configure(style="Warn.TLabel")
                continue
            # Find the star by name, then by where it is marked, so the check works whatever catalog was labeled.
            star = self._chart_star_named(name) or (self._chart_star_at(xy) if xy is not None else None)
            hits = (star or {}).get("variable") or self._variables_at(xy)
            mags = self._bands_for(role, name) if role != "target" else {}
            if not mags and star:
                mags = dict(star.get("mags") or {})
            parts, style = [], "Hint.TLabel"
            if role != "target" and star is not None:
                # 2.2.13: where the magnitude came from, and the AAVSO sequence label when the star has one.
                src = (star.get("mag_source") or {}).get(star.get("band", "V")) or star.get("catalog")
                if (star.get("band", "V") == "V"   # 2.2.17 (item 1): the V note only for V (a Sloan run has its own)
                        and "V from G" in str(((star.get("sources") or {}).get(src) or {}).get("service") or "")):
                    src = f"{src} (from G and BP-RP, ±0.03)"   # 2.2.14 (item 5): a faint star without GSPC
                band_note = (star.get("mag_notes") or {}).get(star.get("band", "V"))
                if band_note:
                    src = f"{src} ({band_note})"   # 2.2.17 (item 1): e.g. i′ from Gaia G and BP-RP, ±0.04
                seq = star.get("seq")
                if src or seq:
                    parts.append(f"{core.band_label(star.get('band', 'V'))} from {src}"
                                 + (f" · AAVSO sequence {seq.get('label')}" if seq and seq.get("label") else ""))
            if role != "target":
                # 2.2.14 (item 1): B-V from one catalog (named), not the best B against the best V.
                bv, src = core.catalog_color(self._colors_for(role, name) or star or {}, "bv")
                spare_info = (self.spare_catalog.get(name) or {}) if any(m[2] == name for m in self.spare_marks) else {}
                if spare_info.get("widened") and spare_info.get("fainter_than_target") is not None:
                    # 2.2.14 (item 6): a spare from a widened window, adopted as a comp, shows how faint it is.
                    # (a comp's own brightness note below already gives it, so only the check star repeats it here)
                    parts.append("spare comp (widened window)" + ("" if comp_number(role) and self.target_xy is not None
                                                                  else ", " + _fainter_text(spare_info["fainter_than_target"])))
                if bv is not None:
                    if bv > core.RED_LIMIT_BV:
                        parts.append(f"⚠ very red, B−V {bv:.2f}: red giants often vary, and the color is far from most targets")
                        style = "Warn.TLabel"
                    else:
                        parts.append(f"✓ B−V {bv:.2f}" + (f" ({src})" if src else ""))
                        style = "Good.TLabel"
                else:
                    parts.append("no catalog B−V for this star")
            no_v = self._no_catalog_v_note(role)
            if no_v:
                parts.insert(0, "⚠ " + no_v)   # 2.2.14 (item 4)
                if style != "Bad.TLabel":
                    style = "Warn.TLabel"
            note = self._comp_brightness_note(role, xy)
            if note:
                parts.append(note)
                if note.startswith("⚠") and style != "Bad.TLabel":
                    style = "Warn.TLabel"
            if hits:
                parts.append(("Listed: " if role == "target" else "⚠ ") + core.variable_text({"variable": hits}))
                style = "Hint.TLabel" if role == "target" else "Bad.TLabel"
            elif role != "target":
                if checked and (star or xy is not None):
                    parts.append("not a known or suspected variable (" + " + ".join(self.field_check_sources)
                                 + ("" if star else ", checked by position") + ")")
                elif not checked:
                    parts.append("variable check: Solve first")
                else:
                    parts.append("variable check: mark it on the image to check it by position")
            if slot_note and "  Marker at (" not in slot_note[1]:
                # 2.2.14 (item 14): e.g. "renamed only" (2.2.16 item 8: now with its ✓; a placed star's line is not
                # repeated, the panel shows the star itself)
                parts.insert(0, slot_note[1])
            var.set("   ".join(parts))
            lab.configure(style=style)
        self._refresh_watch_box()

    # ---- watch stars ------------------------------------------------------------------
    def add_watch_at(self, xy, announce: bool = False):
        star = self._chart_star_at(xy)
        if star is not None:
            name = str(star.get("auid") or star.get("label") or "")
            # 2.2.14 (item 11): a known variable's name on the star counts too (V1441 Cas is TYC 4006-670-1).
            names = core.star_names(star) + [str(v.get("name")) for v in star.get("variable") or []
                                             if isinstance(v, dict) and v.get("name")]
            for w in self.watch_list:
                same_pos = (star.get("ra") is not None and w.get("ra") is not None and w.get("dec") is not None
                            and core.sky_separation_arcsec(float(star["ra"]), float(star["dec"]), float(w["ra"]),
                                                           float(w["dec"])) < WATCH_SAME_ARCSEC)
                if (w["name"] in names or same_pos) and w["name"] != name:
                    name = w["name"]   # 2.2.13: watched under another name or by its position: the same star
                    break
        else:
            name = ""
        # 2.2.14 (item 11): a star already watched at this spot of the image is that watch star.
        name = next((n for n, wxy in self.watch_xy.items() if math.hypot(wxy[0] - xy[0], wxy[1] - xy[1]) < 2.0), name)
        if not name:
            name = f"watch {len(self.watch_list) + 1} ({xy[0]:.0f},{xy[1]:.0f})"
        if any(w["name"] == name for w in self.watch_list):
            self.watch_xy[name] = xy
            self.refresh_preview()
            self._already_watching(name, f"{name} is already a watch star; its position on this frame was updated.",
                                   announce)   # 2.2.14 (item 15)
            return name
        else:
            entry = {"name": name}
            if star is None and self.chart_solution and self.chart_placed:
                try:
                    entry["ra"], entry["dec"] = core.pixel_to_sky(xy[0], xy[1], self.chart_solution)
                except Exception:
                    pass
            if star is not None:
                entry.update({"mag": star.get("mag"), "mags": dict(star.get("mags") or {}),
                              "catalog": star.get("catalog", ""), "ra": star.get("ra"), "dec": star.get("dec"),
                              "variable": core.variable_text(star),
                              "colors": core.star_colors(star)})   # 2.2.14 (item 1)
            self.watch_list.append(entry)
            self.watch_xy[name] = xy
            self.status.set(f"Watching {name}." + ("" if star is not None or "ra" in entry else
                            " It is not a labeled catalog star, so click it again on later nights."))
            self._remember_discovery_watch(entry)   # 2.2.18 (item 12)
        self.refresh_preview()
        self._refresh_watch_box()
        return name

    def add_watch_by_name(self, name: str):
        """Watch a spare comp (from Comp health); its stored measurements become its light curve at once."""
        if any(w["name"] == name for w in self.watch_list):
            self._already_watching(name, f"{name} is already a watch star.", True)   # 2.2.14 (item 15)
            return
        info = self.spare_catalog.get(name, {})
        self.watch_list.append({"name": name, "mag": info.get("mag"), "mags": dict(info.get("mags") or {}),
                                "catalog": info.get("catalog", ""), "ra": info.get("ra"), "dec": info.get("dec"),
                                "variable": info.get("variable", ""),
                                "colors": dict(info.get("colors") or {})})   # 2.2.14 (item 1)
        star = self._chart_star_named(name)
        if star is not None:
            for x, y, st in self.chart_placed:
                if st is star:
                    self.watch_xy[name] = (x, y)
        self._refresh_watch_box()
        self.refresh_preview()
        self.status.set(f"Watching {name}. Its spare-comp measurements already in the series form its light curve.")

    # ---- 2.2.18 (item 12): Discovery watch stars remembered across restarts --------------------------------------
    # Kept in the settings file (no Discovery series to hold them), each with its sky position. After a Discovery
    # solve, the remembered stars that fall on the new frame are watched again, so the list is per field without a
    # field key: a star off this frame (another field, a stale entry) simply does not come back. A star without a sky
    # position (clicked on an unsolved frame) cannot be found again and is not remembered.
    DISCOVERY_WATCH_MAX = 200

    def _discovery_watch_saved(self) -> list:
        saved = self.settings.get("discovery_watch")
        return [w for w in saved if isinstance(w, dict) and w.get("name")] if isinstance(saved, list) else []

    def _remember_discovery_watch(self, entry: dict) -> None:
        if self.mode_key != "discovery" or not isinstance(entry, dict) or not entry.get("name"):
            return
        try:
            ra, dec = float(entry["ra"]), float(entry["dec"])
        except (KeyError, TypeError, ValueError):
            return
        if not (math.isfinite(ra) and math.isfinite(dec)):
            return
        keep = {k: entry[k] for k in ("name", "mag", "mags", "catalog", "variable", "colors") if k in entry}
        keep.update({"ra": ra, "dec": dec, "saved": time.strftime("%Y-%m-%d")})
        saved = [w for w in self._discovery_watch_saved() if w.get("name") != keep["name"] and not (
            w.get("ra") is not None and w.get("dec") is not None
            and core.sky_separation_arcsec(ra, dec, float(w["ra"]), float(w["dec"])) < WATCH_SAME_ARCSEC)]
        saved.append(keep)
        self.settings["discovery_watch"] = saved[-self.DISCOVERY_WATCH_MAX:]
        self._save_settings()

    def _forget_discovery_watch(self, name: str, entry: dict | None = None) -> None:
        if self.mode_key != "discovery":
            return
        saved = self._discovery_watch_saved()
        rd = None
        try:
            rd = (float(entry["ra"]), float(entry["dec"])) if entry else None
        except (KeyError, TypeError, ValueError):
            rd = None
        left = [w for w in saved if w.get("name") != name and not (
            rd is not None and w.get("ra") is not None and w.get("dec") is not None
            and core.sky_separation_arcsec(rd[0], rd[1], float(w["ra"]), float(w["dec"])) < WATCH_SAME_ARCSEC)]
        if len(left) != len(saved):
            self.settings["discovery_watch"] = left
            self._save_settings()

    def _restore_discovery_watch(self) -> int:
        """After a Discovery solve: watch again the remembered stars that are on this frame. Returns how many."""
        if self.mode_key != "discovery" or not self.chart_solution or self.preview is None:
            return 0
        added = []
        for w in self._discovery_watch_saved():
            try:
                ra, dec = float(w["ra"]), float(w["dec"])
            except (KeyError, TypeError, ValueError):
                continue
            if any(o.get("name") == w["name"] or (o.get("ra") is not None and o.get("dec") is not None
                   and core.sky_separation_arcsec(ra, dec, float(o["ra"]), float(o["dec"])) < WATCH_SAME_ARCSEC)
                   for o in self.watch_list):
                continue
            if self._project_to_frame(ra, dec) is None:
                continue   # another field, or off this frame: harmless, it stays remembered
            entry = {k: v for k, v in w.items() if k != "saved"}
            self.watch_list.append(entry)
            added.append(entry["name"])
        if added:
            self.log(f"Watch stars remembered for this field ({len(added)}): " + ", ".join(added)
                     + ". Right-click one (or Remove selected) to stop watching it.")
        return len(added)

    def remove_watch(self, name: str):
        gone = next((w for w in self.watch_list if w["name"] == name), None)
        self._forget_discovery_watch(name, gone)   # 2.2.18 (item 12)
        self.watch_list = [w for w in self.watch_list if w["name"] != name]
        self.watch_xy.pop(name, None)
        self._refresh_watch_box()
        self._update_discovery_panel()
        self.refresh_preview()
        self.status.set(f"Stopped watching {name}. Its measurements stay in the series.")

    def _remove_selected_watch(self):
        sel = self.watch_box.curselection() if hasattr(self, "watch_box") else ()
        if sel and sel[0] < len(self.watch_list):
            self.remove_watch(self.watch_list[sel[0]]["name"])

    def _refresh_watch_box(self):
        if not hasattr(self, "watch_box"):
            return
        try:
            sel = self.watch_box.curselection()
            keep = self.watch_list[sel[0]]["name"] if sel and sel[0] < len(self.watch_list) else None
        except Exception:
            keep = None
        keep = getattr(self, "_watch_highlight", None) or keep
        self.watch_box.delete(0, END)
        for w in self.watch_list:
            mag = f"  V {w['mag']:.2f}" if isinstance(w.get("mag"), (int, float)) else ""
            where = "on this frame" if w["name"] in self.watch_xy else "not found on this frame"
            self.watch_box.insert(END, f"{w['name']}{mag}  —  {where}")
        # 2.2.14 (item 15): a refresh keeps the selected (or just highlighted) row selected.
        names = [w["name"] for w in self.watch_list]
        if keep in names:
            try:
                i = names.index(keep)
                self.watch_box.selection_clear(0, END)
                self.watch_box.selection_set(i)
                self.watch_box.see(i)
            except Exception:
                pass

    def _already_watching(self, name: str, text: str, announce: bool = False):
        """2.2.14 (item 15): a star that is already watched: highlight its row in the Watch stars box and say so in the
        status bar and the log, and in a message when it came from Add a star… (status bar only was easy to miss)."""
        self._watch_highlight = name
        self._refresh_watch_box()
        self._watch_highlight = None
        self.status.set(text)
        self.log("Watch stars: " + text)
        if announce:
            messagebox.showinfo(APP_TITLE, text + "\n\nIts row is highlighted in the Watch stars box.", parent=self)

    def _locate_watch_stars(self):
        """After Solve: find each watch star on the new frame by catalog name, then by sky position."""
        found = 0
        for w in self.watch_list:
            hit = None
            named = self._chart_star_named(w["name"])
            if named is not None:
                hit = self._chart_xy_of(named)
            if hit is None and w.get("ra") is not None and w.get("dec") is not None:
                best = None
                for x, y, star in self.chart_placed:
                    if star.get("ra") is None:
                        continue
                    d = core.sky_separation_arcsec(w["ra"], w["dec"], star["ra"], star["dec"])
                    if d < 5.0 and (best is None or d < best[0]):
                        best = (d, (x, y))
                hit = best[1] if best else None
            if hit is None and w.get("ra") is not None and self.chart_solution and self.preview is not None:
                hit = self._project_to_frame(w["ra"], w["dec"])
            if hit is not None:
                self.watch_xy[w["name"]] = hit
                found += 1
        if self.watch_list:
            self.log(f"Watch stars: {found} of {len(self.watch_list)} found on this frame"
                     + ("" if found == len(self.watch_list) else
                        " (click the missing ones with Watch)") + ".")
        self._refresh_watch_box()

    def _project_to_frame(self, ra: float, dec: float):
        """Pixel position of a sky position on the current frame (centered on the star), or None if off the frame."""
        try:
            x, y = core.sky_to_pixel(ra, dec, self.chart_solution)
        except Exception:
            return None
        h, w = self.preview.shape
        edge = float(self.sky_out.get()) + 2
        if not (edge <= x < w - edge and edge <= y < h - edge):
            return None
        try:
            cx, cy = core.recenter(self.preview, x, y, float(self.radius.get()))[:2]
            if math.hypot(cx - x, cy - y) <= max(3.0, float(self.radius.get())):
                return (float(cx), float(cy))
        except Exception:
            pass
        return (float(x), float(y))

    def add_watch_dialog(self):
        if self.preview is None or not self.preview_is_debayered:
            messagebox.showinfo(APP_TITLE, "Calibrate first (Calibrate page), so the star can be found on the image.")
            return
        text = simpledialog.askstring(
            APP_TITLE,
            "Star to watch: a name (V1441 Cas, NSV 14464, TYC 4006-946-1, a Gaia ID on the labeled chart),\n"
            "or RA Dec (23 13 48.1 +57 07 34, or degrees: 348.4504 57.1261).",
            parent=self)
        if text and text.strip():
            self.add_watch_lookup(text.strip())

    def add_watch_lookup(self, text: str):
        """Watch a star given by name or coordinates: the labeled chart first, then the field's variables,
        then coordinates, then a SIMBAD/VSX name lookup."""
        key = " ".join(text.lower().split())
        # 2.2.14 (item 15): a name already in the list (a loaded series' watch star) says so and highlights its row.
        known = next((w["name"] for w in self.watch_list if name_key(w["name"]) == name_key(text)), None)
        if known is not None:
            self._already_watching(known, f"{known} is already a watch star.", True)
            return
        named = self._chart_star_named(text)
        if named is not None and self._chart_xy_of(named) is not None:
            self.add_watch_at(self._chart_xy_of(named), announce=True)
            return
        for x, y, v in self.variable_marks:
            if " ".join(str(v.get("name", "")).lower().split()) == key:
                self._add_watch_sky(v["name"], v["ra"], v["dec"], self._variable_hover(v).replace("\n", " "),
                                    announce=True)
                return
        coords = core.parse_coordinates(text)
        if coords is not None:
            self._add_watch_sky(f"watch {coords[0]:.4f} {coords[1]:+.4f}", coords[0], coords[1], "", announce=True)
            return
        self.status.set(f"Looking up {text} in SIMBAD and VSX…")

        def work():
            try:
                found = core.lookup_target(text)
                self.call_ui(self._add_watch_sky, text, found["ra_hours"] * 15.0, found["dec_deg"],
                             f"{found.get('source', 'SIMBAD')}" + (f" {found['var_type']}" if found.get("var_type") else ""),
                             True)
            except Exception as exc:
                self.call_ui(self.status.set, f"Could not find {text}.")
                self.call_ui(messagebox.showerror, APP_TITLE, f"Could not find {text}: {exc}")

        self._blink_worker = threading.Thread(target=work, daemon=True)
        self._blink_worker.start()

    def _add_watch_sky(self, name: str, ra: float, dec: float, note: str, announce: bool = False):
        """Add a watch star at a sky position: on a labeled chart star if one is within 5\", else projected
        onto the image with the chart solution."""
        if any(w["name"] == name for w in self.watch_list):
            self._already_watching(name, f"{name} is already a watch star.", announce)   # 2.2.14 (item 15)
            return
        # 2.2.13 (John): one star, one name. A star typed by RA/Dec (or another name) that is already watched is not
        # added again (13 Oct: APASS J231239.3+565553 became a second watch star 'watch 348.1637 +56.9314').
        for w in self.watch_list:
            if w.get("ra") is not None and w.get("dec") is not None and \
                    core.sky_separation_arcsec(ra, dec, float(w["ra"]), float(w["dec"])) < WATCH_SAME_ARCSEC:
                self._already_watching(w["name"], f"That star is already watched as {w['name']}.", announce)
                return
        best = None
        for x, y, star in self.chart_placed:
            if star.get("ra") is None:
                continue
            d = core.sky_separation_arcsec(ra, dec, star["ra"], star["dec"])
            if d < 5.0 and (best is None or d < best[0]):
                best = (d, x, y, star)
        if best is not None:
            names = core.star_names(best[3])
            for w in self.watch_list:
                if w["name"] in names:
                    self._already_watching(w["name"], f"That star is already watched as {w['name']}.", announce)
                    return
            if name.startswith("watch ") and best[3].get("auid"):
                name = str(best[3]["auid"])   # a catalog star: its catalog name, not the typed coordinates
        entry = {"name": name, "ra": ra, "dec": dec}
        hits = [v for _x, _y, v in self.variable_marks if core.sky_separation_arcsec(ra, dec, v["ra"], v["dec"]) < 6.0]
        if best is not None:
            star = best[3]
            entry.update({"mag": star.get("mag"), "mags": dict(star.get("mags") or {}), "catalog": star.get("catalog", ""),
                          "colors": core.star_colors(star)})   # 2.2.14 (item 1)
            xy = (best[1], best[2])
        elif self.chart_solution and self.chart_placed:
            xy = self._project_to_frame(ra, dec)
        else:
            xy = None
        if xy is not None:
            for other, oxy in self.watch_xy.items():
                if math.hypot(oxy[0] - xy[0], oxy[1] - xy[1]) < 2.0:
                    self._already_watching(other, f"That star is already watched as {other}.", announce)
                    return
        if hits:
            entry["variable"] = core.variable_text({"variable": hits})
        elif note:
            entry["variable"] = note
        self.watch_list.append(entry)
        self._remember_discovery_watch(entry)   # 2.2.18 (item 12)
        if xy is not None:
            self.watch_xy[name] = xy
            self.status.set(f"Watching {name} at pixel ({xy[0]:.0f}, {xy[1]:.0f}).")
        elif not self.chart_placed:
            self.status.set(f"Watching {name}. Solve to find it on the image.")
        else:
            self.status.set(f"Watching {name}, but it is not on this frame.")
        self.refresh_preview()
        self._refresh_watch_box()

    def _update_pick_label(self):
        comps = self._comp_roles_marked()
        comp_text = ("Comps " + ", ".join(f"C{comp_number(r)}" for r in comps)) if comps else "Comps —"
        self.pick_label.set(
            f"Target {self._fmt(self.target_xy)}    {comp_text}    Check {self._fmt(self.check_xy)}"
            + (f"    Watch {len(self.watch_xy)} of {len(self.watch_list)} on this frame" if self.watch_list else "")
        )
        self._update_step_hint()   # 2.2.15 (GitHub #3)

    def _fill_from_chart(self, mode, xy):
        """If the click lands on a star from the AAVSO chart, copy its label and magnitude into the form."""
        if mode == "target" or not self.chart_placed:
            return ""
        best = None
        for x, y, star in self.chart_placed:
            d = math.hypot(x - xy[0], y - xy[1])
            if d < 8 and (best is None or d < best[0]):
                best = (d, star)
        if best is None:
            return ""
        star = best[1]
        name = star.get("auid") or star.get("label") or ""
        if star.get("mag") is None:
            return (f"{name} has no {core.band_label(star.get('band', 'V'))} magnitude in "
                    f"{star.get('catalog', 'the catalog')}. Pick another star.")   # 2.2.17 (item 1): band label
        mag = f"{star['mag']:.3f}"
        target = (getattr(self, f"{mode}_name"), getattr(self, f"{mode}_mag"))
        target[0].set(name)
        target[1].set(mag)
        self.star_bands[mode] = {"name": name, "mags": dict(star.get("mags") or {}),
                                 "source": star.get("catalog", ""), "seq": star.get("seq"),
                                 "colors": core.star_colors(star)}   # 2.2.14 (item 1): one catalog per color
        if star.get("ra") is not None and star.get("dec") is not None:
            self.comp_coords[name] = (float(star["ra"]), float(star["dec"]))
        if seq_auid := (star.get("seq") or {}).get("auid"):
            # 2.2.13 (John): an AAVSO sequence comp or check is named by its AUID in AAVSO reports; the series and
            # the panel keep the catalog name, so night-to-night matching is unchanged.
            self.seq_auids[name] = seq_auid
        self._update_chart_field()
        seq = star.get("seq")
        note = (star.get("mag_notes") or {}).get(star.get("band", "V"))   # 2.2.17 (item 1): Gaia-transformed Sloan
        return (f"{role_label(mode)}: {name}  {core.band_label(star.get('band', 'V'))} = {mag} "
                f"({star.get('catalog', 'catalog')}{', ' + note if note else ''})"
                + (f", AAVSO sequence {seq.get('label')}" if seq else ""))

    def _spare_at(self, click_xy, snapped_xy=None):
        """2.2.14 (item 3): the spare comp whose diamond was clicked: (name, its measured position), or None. A click
        counts when it lands on the diamond (within its drawn size, ~8 px) or when the snapped star is the spare's
        (within ~2 px or the seeing). Only spares with a catalog record (this run's) count."""
        if not self.spare_marks or not self._overlay("spares"):
            return None
        seeing = self._seeing()
        tight = max(2.0, seeing if math.isfinite(seeing) else 2.0)
        snapped_xy = click_xy if snapped_xy is None else snapped_xy
        d_snap_click = math.hypot(snapped_xy[0] - click_xy[0], snapped_xy[1] - click_xy[1])
        best = None
        for x, y, name in self.spare_marks:
            info = self.spare_catalog.get(name) or {}
            if info.get("mag") is None:
                continue
            d_click = math.hypot(x - click_xy[0], y - click_xy[1])
            d_snap = math.hypot(x - snapped_xy[0], y - snapped_xy[1])
            # The snapped star is the spare, or the click is on the diamond and nearer to it than to the star the
            # click snapped to (a neighbour).
            if d_snap <= tight or (d_click <= 8.0 and d_click <= d_snap_click):
                if best is None or d_snap < best[0]:
                    best = (d_snap, name, (float(x), float(y)))
        return (best[1], best[2]) if best is not None else None

    def _fill_from_spare(self, mode: str, name: str) -> str:
        """2.2.14 (item 3): fill a comp or check slot from a spare comp's record (spare_catalog): its name, catalog V,
        every band, the colors (one catalog each), its sky position and AAVSO sequence AUID."""
        info = self.spare_catalog.get(name) or {}
        mag = info.get("mag")
        if mag is None:
            return ""
        getattr(self, f"{mode}_name").set(name)
        getattr(self, f"{mode}_mag").set(f"{float(mag):.3f}")
        seq = info.get("seq") or None
        self.star_bands[mode] = {"name": name, "mags": dict(info.get("mags") or {}), "source": info.get("catalog", ""),
                                 "seq": seq}
        # A spare record from an older series has no colors: take them from the labeled star (an empty pick would be
        # final and hide them), or leave the key out so _colors_for looks the star up later (review S2).
        if isinstance(info.get("colors"), dict):
            self.star_bands[mode]["colors"] = dict(info["colors"])
        elif self._chart_star_named(name) is not None:
            self.star_bands[mode]["colors"] = core.star_colors(self._chart_star_named(name))
        if info.get("ra") is not None and info.get("dec") is not None:
            self.comp_coords[name] = (float(info["ra"]), float(info["dec"]))
        if seq and seq.get("auid"):
            self.seq_auids[name] = seq["auid"]
        self._update_chart_field()
        band = core.band_label(self._band())   # 2.2.17 (item 1)
        return (f"{role_label(mode)}: {name}  {band} = {float(mag):.3f} ({info.get('catalog') or 'catalog'}, spare comp)"
                + (f", AAVSO sequence {seq.get('label')}" if seq and seq.get("label") else ""))

    def _note_sequence(self, role: str, name: str, star: dict, xy):
        """2.2.13 (review): a comp or check that keeps its name from the series (later nights) but is an AAVSO
        sequence star gets its AUID for the report and its chart ID, as a clicked one does. Only when the labeled
        star is right under the marker."""
        seq = star.get("seq") or {}
        if not seq.get("auid"):
            return
        seeing = self._seeing()
        if self._chart_star_at(xy, reach=max(2.0, seeing if math.isfinite(seeing) else 2.0)) is not star:
            return
        self.seq_auids[name] = seq["auid"]
        entry = self.star_bands.get(role) or {}
        if entry.get("name") in (None, name):
            entry.update({"name": name, "seq": seq})
            entry.setdefault("mags", dict(star.get("mags") or {}))
            entry.setdefault("colors", core.star_colors(star))   # 2.2.14 (item 1)
            self.star_bands[role] = entry
        self._update_chart_field()

    def _sequence_note(self):
        """2.2.13 (John): once per set of comps, say when AAVSO sequence stars of the right brightness are on the
        frame but some comps are not sequence stars. Never blocks."""
        seq = [n for n in getattr(self, "_seq_names", set()) if n]
        if not seq or self.mode_key == "discovery":
            return
        roles = self._comp_roles_marked()
        others = []
        for r in roles:
            star = self._chart_star_at(getattr(self, f"{r}_xy"))
            if star is not None and not star.get("seq"):
                others.append(getattr(self, f"{r}_name").get().strip() or role_label(r))
        key = tuple(others)
        if not others or key == getattr(self, "_seq_note_for", None):
            return
        self._seq_note_for = key
        suggested = [n for n, _dm, _x, _y in self.suggested_comps if n in seq]
        self.log("Note: AAVSO sequence stars are on this frame" + (f" (suggested: {', '.join(suggested)})"
                 if suggested else "") + f", but {', '.join(others)} {'is' if len(others) == 1 else 'are'} not in "
                 "the sequence. That is fine; AAVSO reports are more comparable with sequence comps.")

    def _update_chart_field(self):
        """2.2.13 (John): the report's CHART field carries the AAVSO chart ID whenever a marked comp or the check
        star is an AAVSO sequence star (the sequence magnitudes are then what the report used), else 'na'.
        A chart ID typed by hand that is not one of SHOBS-P's is left alone."""
        chart_id = ""
        for role in list(COMP_ROLES) + ["check"]:
            entry = self.star_bands.get(role) or {}
            name = getattr(self, f"{role}_name").get().strip()
            if entry.get("name") == name and entry.get("seq") and getattr(self, f"{role}_xy") is not None:
                chart_id = entry["seq"].get("chartid") or chart_id
        current = self.chart.get().strip()
        auto = getattr(self, "_chart_auto", None)
        if current in ("", "na") or current == auto:
            self.chart.set(chart_id or "na")
            self._chart_auto = chart_id or "na"

    def _bands_for(self, role: str, name: str) -> dict:
        """Catalog magnitudes in every band for a comp, if it was picked from a chart."""
        entry = self.star_bands.get(role) or {}
        return dict(entry.get("mags") or {}) if entry.get("name") == name else {}

    def _colors_for(self, role: str, name: str) -> dict:
        """2.2.14 (item 1): a comp's catalog colors, each from ONE catalog: {"colors": {"bv": [value, catalog], …}}.
        From its pick (star_bands), else from the labeled star by name or under its marker, else its spare record
        (a pick saved before 2.2.14 kept only the best-per-band magnitudes, which are not used for a color)."""
        entry = self.star_bands.get(role) or {}
        if entry.get("name") == name and isinstance(entry.get("colors"), dict):
            return {"colors": dict(entry["colors"])}
        star = self._chart_star_named(name)
        xy = getattr(self, f"{role}_xy", None) if (role in COMP_ROLES or role == "check") else None
        if star is None and xy is not None:
            seeing = self._seeing()
            star = self._chart_star_at(xy, reach=max(2.0, seeing if math.isfinite(seeing) else 2.0))
        if star is not None:
            return {"colors": core.star_colors(star)}
        info = self.spare_catalog.get(name) or {}
        if isinstance(info.get("colors"), dict):
            return {"colors": dict(info["colors"])}
        return {}

    @staticmethod
    def _fmt(xy):
        if not xy:
            return "—"
        return f"({xy[0]:.1f}, {xy[1]:.1f})"

    def _star(self, name, xy, mag_text, required_mag):
        if xy is None:
            raise ValueError(f"{name} is not marked")
        mag = None
        if mag_text.strip():
            mag = float(mag_text)
        elif required_mag:
            raise ValueError(f"{name} needs a catalog {core.band_label(self._band())} magnitude")   # 2.2.17 (item 1)
        return core.Star(name=name, x=xy[0], y=xy[1], catalog_mag=mag)

    def label_aavso(self, auto: bool = False):
        catalog = "Catalogs"   # 2.2.13: every catalog is fetched; the Catalog box only picks which circles show
        if self.preview is None or not self.preview_is_debayered:
            if not auto:
                messagebox.showinfo(APP_TITLE, "Calibrate first (Calibrate page).")
            return
        # 2.2.16 (item 10): this image has had a solve attempt (the step strip says "Not solved" if it fails).
        tried_before = getattr(self, "_solve_tried_for", None)
        self._solve_tried_for = getattr(self, "_preview_token", 0)
        self._solve_impossible_for = None
        field_solve = self.target_xy is None or self.mode_key == "discovery"
        wcs = core.header_wcs(self.preview_header or {})
        superpixel = 1 if self.pattern.get() == core.MONO else 2
        try:
            wcs_factor = max(int(self._image_binning()), 1) * superpixel
        except (TypeError, ValueError):
            wcs_factor = superpixel
        # Where the catalog query and the chart solution are centred. Kept local: the Input page's RA/Dec are the
        # target's and are only filled in when empty (2.2).
        query = None
        if wcs is not None:
            # The header's plate solution says exactly where the frame points: always aim the catalog there (2.2.1),
            # so a target typed for another field cannot send the query to the wrong part of the sky.
            hdr = self.preview_header or {}
            try:
                saved_shape = (int(hdr.get("NAXIS2")), int(hdr.get("NAXIS1")))
            except (TypeError, ValueError):
                saved_shape = (self.preview.shape[0] * wcs_factor, self.preview.shape[1] * wcs_factor)
            c_ra, c_dec = core.wcs_center(wcs, saved_shape)
            query = (c_ra / 15.0, c_dec)
        elif self.mode_key == "discovery" and core.header_pointing(self.preview_header or {}) is not None:
            # Discovery has no target of its own: an RA/Dec left over from another mode would aim the catalog
            # query at the wrong field, so use where the header says the telescope pointed.
            pointing = core.header_pointing(self.preview_header or {})
            query = (pointing[0] / 15.0, pointing[1])
        if query is None:
            try:
                query = (float(self.ra_hours.get()), float(self.dec_deg.get()))
            except (TypeError, ValueError):
                query = None
        if query is None:
            pointing = core.header_pointing(self.preview_header or {})
            if pointing is None:
                self.field_centre_needed = True
                self._solve_impossible_for = self._solve_tried_for   # 2.2.16 (item 10)
                self._update_step_hint()
                self._layout_input_for_mode()
                if auto:
                    self.status.set("Field not solved: these lights do not say where they point. Type the field "
                                    "centre on the Input page, then Solve again.")
                    return
                messagebox.showinfo(
                    APP_TITLE,
                    "Solve needs to know roughly where the frame is: the target's RA/Dec on the Input page, or the "
                    "field center. These frames' FITS headers have no RA/DEC. Type the field center on the Input page "
                    "(from your planetarium program or the mount; a few arcminutes off is fine) and try again.")
                return
            query = (pointing[0] / 15.0, pointing[1])
            self.log(f"Solve: field center from the FITS header, RA {pointing[0] / 15.0:.5f} h, Dec "
                     f"{pointing[1]:+.4f}°.")
        # 2.2.15 (GitHub #3): the target's own RA/Dec (looked up or typed), not a field centre filled in below or by an
        # earlier solve.
        radec_given = bool(self.ra_hours.get().strip() and self.dec_deg.get().strip()) and not self._radec_is_fill()
        if (not self.ra_hours.get().strip() or not self.dec_deg.get().strip()) and \
                (not auto or self.mode_key == "discovery" or self.target_xy is not None):
            # 2.2.13: an automatic solve in Variables/Transits with no target yet leaves the target's RA/Dec alone.
            fill = query
            if wcs is not None and not field_solve and self.target_xy is not None:
                # A marked target with no RA/Dec typed: take its own position from the plate solution.
                sx, sy = core.image_to_wcs(self.target_xy[0], self.target_xy[1], wcs_factor)
                t_ra, t_dec = core.wcs_pixel_to_sky(wcs, sx, sy)
                fill = (float(t_ra) / 15.0, float(t_dec))
            self.ra_hours.set(f"{fill[0]:.6f}")
            self.dec_deg.set(f"{fill[1]:.5f}")
            # 2.2.15 (review): remember what was filled, so a later solve does not take it for the target's RA/Dec.
            self._radec_filled = (self.ra_hours.get().strip(), self.dec_deg.get().strip()) if fill is query else None
        try:
            target_radec = (float(self.ra_hours.get()) * 15.0, float(self.dec_deg.get()))
        except (TypeError, ValueError):
            target_radec = None
        cone = query          # where the catalog is asked for stars
        if wcs is not None and not field_solve and target_radec is not None:
            hdr = self.preview_header or {}
            px_, py_ = core.wcs_sky_to_pixel(wcs, target_radec[0], target_radec[1])
            try:
                w_saved, h_saved = int(hdr.get("NAXIS1")), int(hdr.get("NAXIS2"))
            except (TypeError, ValueError):
                w_saved, h_saved = self.preview.shape[1] * wcs_factor, self.preview.shape[0] * wcs_factor
            if not (np.isfinite(px_) and np.isfinite(py_) and -50 <= px_ <= w_saved + 50 and -50 <= py_ <= h_saved + 50):
                self.log(f"Solve: the Input page target ({self.star_id.get().strip() or 'RA/Dec'}) is not in these "
                         "lights, according to their plate solution; the chart is placed from the solution alone. "
                         "Update the target on the Input page.")
                field_solve = True
        # The reference point the chart solution is built around: the target when one is marked (its marker is the
        # anchor), else the catalog query's centre.
        if not field_solve and target_radec is not None:
            query = (target_radec[0] / 15.0, target_radec[1])
        try:
            ra_hours, dec = float(query[0]), float(query[1])
            binning = max(int(self._image_binning()), 1)
            camera_bin = 1
            for key in ("XBINNING", "BINNING", "XBIN"):
                try:
                    camera_bin = max(int(float((self.preview_header or {}).get(key, 1))), 1)
                    break
                except (TypeError, ValueError):
                    continue
            # Camera binning, app binning, then the superpixel debayer each multiply the pixel size (a monochrome
            # frame has no debayer). Frames from another telescope (MicroObservatory) bring their own scale.
            scale = float(self.pixel_um.get()) / float(self.focal_mm.get()) * 206.265 * camera_bin * binning * superpixel
            if wcs is not None:
                # The header's plate solution knows the true scale of the frame as saved.
                scale = wcs["scale_arcsec"] * binning * superpixel
        except Exception as exc:
            self._update_step_hint()   # 2.2.16 (item 10): "1 Not solved"
            if auto:
                self.status.set(f"Field not solved: check RA, Dec, focal length, and pixel size ({exc}).")
                return
            messagebox.showerror(APP_TITLE, f"Check RA, Dec, focal length, and pixel size: {exc}")
            return
        height_px, width_px = self.preview.shape
        tx, ty = self.target_xy if not field_solve else (width_px / 2.0, height_px / 2.0)
        # The cone must reach every corner of the frame from the target, not just from the frame center. Without a
        # target the center is only the pointing, so allow a few arcminutes for pointing error.
        reach_px = max(math.hypot(cx - tx, cy - ty) for cx in (0, width_px) for cy in (0, height_px))
        fov = 2.0 * reach_px * scale / 60.0 * (1.15 if field_solve else 1.05) + (6.0 if field_solve else 0.0)
        band = self._band()
        # 2.2.14 (item 5): how deep to ask the catalogs. With a plate solution in the header Gaia and APASS are asked
        # for the frame's own footprint (the RA/Dec box around its corners; the ESA archive part of Gaia the circle
        # through them); without one the orientation is unknown, so it stays the circle that reaches every corner
        # from the target. The AAVSO chart and Tycho keep the circle. Gaia is asked deep (G < 19) for about twice the
        # stars seen on the image (scaled to the query area), brightest first, so every star one can click has a
        # magnitude (core.fetch_gaia_dr3).
        cat_fov, polygon = fov, None
        if wcs is not None:
            polygon = core.frame_footprint(wcs, saved_shape)
        sources = core.detect_sources(self.preview)
        # 2.2.9: very short exposures (Tessa's 0.04 s eVscope frames) show too few stars to match; the work below
        # then solves on a quick stack of the first frames instead.
        stack_paths = []
        if len(sources) < 20 and wcs is None:
            stack_paths = [p for p in self._lights()[:30]]
        stack_setup = (self.masters, self.pattern.get(), self.debayer_mode.get(), max(int(self._image_binning() or 1), 1))
        preview_ref, preview_path = self.preview, self.preview_path
        target_xy = self.target_xy if not field_solve else (tx, ty)
        self._chart_diag = {"scale": scale, "camera_bin": camera_bin, "sources": len(sources), "target": target_xy,
                            "auto": auto, "ref": (ra_hours * 15.0, dec), "radec_given": radec_given}
        # An approximate scale (frames from another telescope) gets a wider search.
        slop = 0.15 if self.scale_approx else 0.06
        epoch_year = core.header_epoch_year(self.preview_header or {})
        if not self._start_job(f"Solving: fetching AAVSO, Gaia, APASS and Tycho stars ({fov:.0f}′ field, "
                               f"{core.band_label(band)} band)…"):   # 2.2.17 (item 1)
            self._solve_tried_for = tried_before   # 2.2.16 (item 10): busy, so not tried after all
            return
        self._solving = True          # 2.2.15 (GitHub #3): for the step hint
        self._update_step_hint()
        vet_cache = {"field": getattr(self, "_field_var_cache", None)}
        cancel = self._cancel
        # 2.2.16 (review): progress from this solve's catalog threads only; the lights it was for.
        self._solve_token = solve_token = (getattr(self, "_solve_token", (0, None))[0] + 1, preview_path)

        def work():
            try:
                if len(stack_paths) >= 5 and stack_setup[0] is not None:
                    self.call_ui(self.status.set, f"Only {len(sources)} stars on this frame: stacking "
                                                  f"{len(stack_paths)} frames to find more…")
                    masters_, pattern_, mode_, bin_ = stack_setup
                    frames = [preview_ref]   # the reference, so star positions stay on the preview's pixels
                    for path in stack_paths:
                        if path == preview_path:
                            continue
                        try:
                            raw, header = core.read_fits(path)
                            frames.append(core.debayer(core.calibrate_frame(core.bin_image(raw, bin_), header,
                                                                            masters_), pattern_, mode_))
                        except Exception:
                            continue
                    stacked = core.quick_stack(frames)
                    if stacked is not None and stacked.shape == self.preview.shape:
                        more = core.detect_sources(stacked)
                        if len(more) > len(sources):
                            self._chart_diag["stacked"] = (len(frames), len(sources), len(more))
                            sources[:] = more
                            self._chart_diag["sources"] = len(more)
                # 2.2.13 (John): every catalog at once, merged into one star list, so a click finds the star whatever
                # catalog's circles are shown; the Catalog box now only chooses which circles are drawn.
                # Proper motions to the night's date for this solve only (review 2.2.13: a global left set made later
                # Input-page lookups use an old night's epoch).
                core.PM_EPOCH_YEAR = epoch_year
                # 2.2.14 (item 5): the star count that sets Gaia's depth, counted here (off the window's thread).
                try:
                    n_peaks = core.count_star_peaks(preview_ref)
                except Exception:
                    n_peaks = None
                self._chart_diag["n_peaks"] = n_peaks
                depth = core.field_depth(n_peaks, preview_ref.shape, scale, cat_fov, polygon)
                depth["status"] = {}   # review S3: filled by fetch_gaia_dr3 (which half answered)
                cat_info = {}
                self.call_ui(self._catalog_progress, None, "start", None, solve_token)   # 2.2.16 (item 2)
                try:
                    found, failed = core.fetch_field_catalogs(
                        cone[0], cone[1], cat_fov, band, cancel=cancel, depth=depth,   # 2.2.14 (item 5)
                        progress=lambda c, st, d: self.call_ui(self._catalog_progress, c, st, d, solve_token),
                        info=cat_info)   # 2.2.16 (item 2; review: tagged with this solve)
                    self.call_ui(self.log, self._catalog_times_text(cat_info, found, failed))   # 2.2.16 (item 2)
                    self._chart_diag["cache_on"] = bool(cat_info.get("cache"))
                    self._chart_diag["gaia_depth"] = dict(core.catalog_depth(found.get("Gaia DR3") or [],
                                                                             cat_info.get("max_rows",
                                                                                          depth["max_rows"])),
                                                          max_rows=depth["max_rows"], footprint=bool(polygon),
                                                          asked_rows=cat_info.get("max_rows", depth["max_rows"]),
                                                          status=dict(depth.get("status") or {}))
                    chart_id = next((str(st.get("chartid") or "") for st in found.get("AAVSO sequence") or []
                                     if st.get("chartid")), "")
                    stars = core.merge_catalogs(found, band)
                finally:
                    core.PM_EPOCH_YEAR = None
                if cancel.is_set():
                    raise ValueError("cancelled")
                self._chart_diag["catalogs"] = {c: len(v) for c, v in found.items()}
                self._chart_diag["catalog_errors"] = failed
                if not stars:
                    raise ValueError("No catalog returned stars for this field ("
                                     + "; ".join(f"{c}: {e}" for c, e in failed.items()) + ").")
                usable = [s for s in stars if s.get("bright") is not None]
                usable.sort(key=lambda s: s["bright"])
                match_set = usable[:60]
                # About 3" of sky: 6 px at 0.5"/px, 2 px at 5"/px (wider would let random stars "match").
                tol = max(2.0, min(6.0, 3.2 / scale))
                n = len(match_set)
                needed = 2 if n <= 2 else (3 if n < 20 else 6)
                anchor = target_xy
                header_solved = None
                if wcs is not None:
                    header_solved = core.wcs_solve(wcs, wcs_factor, sources, usable, ra_hours * 15.0, dec, scale,
                                                   (height_px, width_px), tolerance_px=max(tol, 3.0))
                    if header_solved is not None and header_solved["hits"] < min(needed, 6):
                        header_solved = None
                if header_solved is not None:
                    solved = header_solved
                    anchor = solved["target_xy"]
                    self._chart_diag["target"] = anchor
                    self._chart_diag["from_header"] = True
                    if not field_solve and target_radec is not None:
                        # Where the header's solution puts the typed target, against the Target marker.
                        tx_, ty_ = core.wcs_sky_to_pixel(wcs, target_radec[0], target_radec[1])
                        tx_, ty_ = core.wcs_to_image(tx_, ty_, wcs_factor)
                        if np.isfinite(tx_) and np.isfinite(ty_):
                            self._chart_diag["header_target_xy"] = (float(tx_), float(ty_))
                            self._chart_diag["header_target_off"] = math.hypot(float(tx_) - target_xy[0],
                                                                               float(ty_) - target_xy[1])
                elif field_solve:
                    solved = core.solve_field(sources, usable, ra_hours * 15.0, dec, scale, tolerance_px=tol,
                                              frame_shape=(height_px, width_px))   # 2.2.17 (item 1): small fields
                    if solved is not None and solved["hits"] >= 8:
                        anchor = solved["target_xy"]
                        self._chart_diag["target"] = anchor
                        self._chart_diag["field_solved"] = True
                    else:
                        solved = None
                else:
                    solved = core.match_chart(sources, match_set, target_xy, ra_hours * 15.0, dec, scale,
                                              tolerance_px=tol, scale_slop=slop)
                if not field_solve and n >= 12 and (solved is None or solved["hits"] < needed):
                    # The target marker may sit on a neighbor: try the detected stars near the click as the anchor.
                    near = sorted(sources, key=lambda p: math.hypot(p[0] - target_xy[0], p[1] - target_xy[1]))
                    near = [p for p in near if 1.5 < math.hypot(p[0] - target_xy[0], p[1] - target_xy[1]) < 120][:15]
                    self.call_ui(self.status.set, f"No lock at the target marker; trying {len(near)} nearby stars as the "
                                                  "target…")
                    best = None
                    for p in near:
                        trial = core.match_chart(sources, match_set, p, ra_hours * 15.0, dec, scale,
                                                 tolerance_px=tol, scale_slop=slop)
                        if trial and (best is None or trial["hits"] > best[0]["hits"]):
                            best = (trial, p)
                    base_hits = solved["hits"] if solved else 0
                    if best and best[0]["hits"] >= max(needed, 8) and best[0]["hits"] >= 3 * max(base_hits, 1):
                        solved, anchor = best
                        self._chart_diag["moved_from"] = target_xy
                        self._chart_diag["target"] = anchor
                    else:
                        # Last resort: solve the field from star patterns, then find the target by its RA/Dec.
                        self.call_ui(self.status.set, "Solving the field from star patterns…")
                        fs = core.solve_field(sources, usable, ra_hours * 15.0, dec, scale, tolerance_px=tol,
                                              frame_shape=(height_px, width_px))   # 2.2.17 (item 1)
                        if fs is not None and fs["hits"] >= max(needed, 8):
                            solved = fs
                            anchor = fs["target_xy"]
                            if math.hypot(anchor[0] - target_xy[0], anchor[1] - target_xy[1]) > 1.5:
                                self._chart_diag["moved_from"] = target_xy
                            self._chart_diag["target"] = anchor
                placed = core.place_stars(stars, solved, anchor, ra_hours * 15.0, dec, scale) if solved else []
                vet = None
                if solved:
                    # One VSX + SIMBAD query per field: known and suspected variables, to vet comps and spares.
                    key = (round(cone[0], 4), round(cone[1], 3), round(fov))
                    cache = vet_cache.get("field")
                    if cache and cache[0] == key and cache[2]:
                        variables, answered = cache[1], cache[2]
                    else:
                        variables, answered = core.field_variables(cone[0] * 15.0, cone[1], fov / 60.0 / 2.0 * 1.15)
                    core.flag_variables(stars, variables, max(6.0, 2.0 * scale))
                    vet = (key, variables, answered)
                self.call_ui(self._label_done, catalog, chart_id, stars, match_set, solved, placed, band, None, vet)
            except Exception as exc:
                self.call_ui(self._label_done, catalog, "", [], [], None, [], band, exc, None)

        self._blink_worker = threading.Thread(target=work, daemon=True)
        self._blink_worker.start()

    # ---- 2.2.16 (item 2): catalog progress and timings ------------------------------------------------------
    _CAT_ORDER = ("AAVSO sequence", "APASS DR9", "Tycho-2", "Gaia DR3")   # the status line's order (Gaia is slowest)

    def _catalog_progress(self, cat, state, detail, token=None):
        """Main thread (via call_ui): one catalog's progress. The status line reads "Catalogs: AAVSO ✓ APASS ✓
        Tycho ✓ Gaia… 18 s" while the solve waits; Gaia's deep half arriving after the solve is logged.
        2.2.16 (review): token = the solve that asked; progress from an earlier (abandoned) solve is dropped, and a
        late Gaia half for other lights is worded neutrally."""
        current = getattr(self, "_solve_token", None)
        if state == "late":
            self._catalog_late(cat, detail or {}, token is None or token[1] == getattr(self, "preview_path", None))
            return
        if token is not None and token != current:
            return
        if state == "start":
            self._cat_progress = {c: "…" for c in self._CAT_ORDER}
            self._cat_seconds = 0
        elif state == "tick":
            self._cat_seconds = int(detail or 0)
        elif cat is not None:
            prog = getattr(self, "_cat_progress", None)
            if prog is None:
                prog = self._cat_progress = {c: "…" for c in self._CAT_ORDER}
            prog[cat] = {"ok": "✓", "cached": "✓", "error": "✗"}.get(state, "…")
        if not getattr(self, "_solving", False):
            return
        prog = getattr(self, "_cat_progress", None) or {}
        parts = [f"{core.CATALOG_SHORT.get(c, c)}{'' if v == '…' else ' '}{v}" for c, v in prog.items()]
        secs = getattr(self, "_cat_seconds", 0)
        self.status.set("Catalogs: " + " ".join(parts) + (f" {secs} s" if secs else ""))

    def _catalog_late(self, cat, d, same_lights):
        """2.2.16 (item 2; review): Gaia's deep half after the solve, worded by what really happened."""
        secs = f"{d.get('seconds', 0):.0f} s after it was asked"
        if d.get("arrived"):
            text = (f"Gaia DR3: the deep part (VizieR, to G {core.GAIA_DEEP_G:g}) arrived after the solve, {secs}: "
                    f"{d.get('n', 0):,} Gaia stars")
            if d.get("archive_missing"):
                text += f" (the ESA archive part did not answer: {d['archive_missing']}; no Gaia colors from it)"
            cached = d.get("cached")
            if d.get("incomplete"):
                text += f". VizieR says the answer was cut short ({d['incomplete']}), so it is not kept"
            elif cached is True:
                text += ". Kept for this field"
            elif cached is False:
                text += ". It could not be saved for this field (see the next lines)"
            if same_lights:
                text += ("; Solve again to use them" + (" (at once)." if cached is True else "."))
                if not self.busy:
                    self.status.set("Gaia's deep stars arrived after the solve: Solve again to use them.")
            else:
                text += ("; they are used when that field is solved again." if cached is True else
                         " (that solve was for other lights).")
        else:
            text = (f"Gaia DR3: the deep part (VizieR) did not finish ({d.get('error') or 'no answer'}, {secs}); "
                    "stars fainter than the archive's G 17 have no Gaia magnitude from that solve."
                    + (" Solve again to retry." if same_lights else ""))
        self.log(text)
        for err in core.CATALOG_CACHE_ERRORS[:]:
            self.log(f"  Catalog cache: could not save {err}")
            try:
                core.CATALOG_CACHE_ERRORS.remove(err)
            except ValueError:
                pass

    @staticmethod
    def _catalog_times_text(info, found, failed) -> str:
        """2.2.16 (item 2): one log line: each catalog's time (or "saved copy") and star count; Gaia's two halves."""
        times = (info or {}).get("times") or {}
        cached = (info or {}).get("cached") or {}
        parts = []
        for cat in list(core.CATALOGS) + [c for c in times if c not in core.CATALOGS]:
            if cat not in times and cat not in (failed or {}) and cat not in (found or {}):
                continue
            if cached.get(cat):
                text = f"{cat} saved copy ({len((found or {}).get(cat) or []):,} stars)"
            elif cat in (found or {}):
                text = f"{cat} {times.get(cat, 0.0):.1f} s ({len(found[cat]):,} stars)"
            else:
                text = f"{cat} failed after {times.get(cat, 0.0):.1f} s" if cat in times else f"{cat} no answer"
            if cat == "Gaia DR3" and not cached.get(cat) and (info or {}).get("gaia"):
                g = info["gaia"]

                def half(name, ok, secs, n):
                    if ok == "ok":
                        return f"{name} ✓ {secs:.1f} s, {n:,}" if secs is not None and n is not None else f"{name} ✓"
                    return f"{name} ✗ ({ok})" if ok else f"{name} not asked"
                text += (" [" + half("ESA archive", g.get("esa"), g.get("esa_s"), g.get("esa_n")) + "; "
                         + half("VizieR deep", g.get("deep"), g.get("deep_s"), g.get("deep_n")) + "]")
            parts.append(text)
        tag = " (field cache on)" if (info or {}).get("cache") else ""
        text = "Catalogs" + tag + ": " + ("; ".join(parts) if parts else "none asked") + "."
        # 2.2.16 (review): answers VizieR cut short (used tonight, not kept) and cache writes that failed
        inc = dict((info or {}).get("incomplete") or {})
        g_inc = ((info or {}).get("gaia") or {}).get("incomplete") or {}
        for half, why in g_inc.items():
            inc[f"Gaia DR3 ({'ESA archive' if half == 'esa' else 'VizieR deep'})"] = why
        for cat, why in inc.items():
            text += f"\n  {cat}: the server says the answer was cut short ({why}); used, not kept."
        for err in (info or {}).get("cache_errors") or []:
            text += f"\n  Catalog cache: could not save {err}"
        return text

    def _label_done(self, catalog, chart_id, stars, match_set, solved, placed, band, error, vet=None):
        self._end_job()
        self._solving = False
        self._update_step_hint()
        diag = getattr(self, "_chart_diag", {})
        auto = diag.get("auto", False)
        if error is not None:
            self.status.set("Not solved: the catalog request failed.")
            if auto:
                self.log(f"Solve: the catalog request failed ({error}). Click Solve again to retry.")
                return
            messagebox.showerror(APP_TITLE, f"Solve: {error}")
            return
        n = len(match_set)
        hits = solved["hits"] if solved else 0
        needed = 2 if n <= 2 else (3 if n < 20 else 6)
        if hits < needed or (n <= 2 and hits < n):
            tx, ty = diag.get("target") or (float("nan"), float("nan"))
            hint = ""
            self.status.set(f"Not solved: no lock ({hits} of {n} catalog stars matched). Clicks still work; IDs "
                            "fill in once the image is solved.")
            # 2.2.9: say why, in plain words.
            why = []
            n_src = diag.get("sources", 0)
            if n_src < 15:
                why.append(f"Only {n_src} stars were found on this frame"
                           + (f" (even after stacking {diag['stacked'][0]} frames)" if diag.get("stacked") else "")
                           + ": too few to match a catalog. Very short exposures or a small telescope do this; mark "
                             "the stars by hand and type their names and magnitudes.")
            why.append(f"The plate scale used is {diag.get('scale', 0):.3f}\"/px, from the focal length and pixel size in "
                       "the Optics box: for another telescope's frames, enter its values.")
            if auto:
                self.log(f"Solve: the catalog stars did not lock onto this frame ({hits} of {n} matched). "
                         + " ".join(why) + " Check binning too, then Solve again. You can mark stars meanwhile; "
                         "their IDs and magnitudes fill in once the image is solved.")
                if self.mode_key == "discovery":
                    self._field_summary()
                return
            messagebox.showinfo(
                APP_TITLE,
                f"The catalog stars did not lock onto this frame ({hits} of {n} matched, {needed} needed).\n\n"
                f"Target marker: ({tx:.0f}, {ty:.0f})   stars found in frame: {diag.get('sources', 0)}\n"
                f"Plate scale used: {diag.get('scale', 0):.3f}\"/px (camera bin {diag.get('camera_bin', 1)}, "
                f"app bin {self._image_binning()}, "
                + ("monochrome)" if self.pattern.get() == core.MONO else "superpixel x2)") + "\n\n"
                + "\n\n".join(why) + "\n\n"
                "Check that the Target marker is on your star, then RA/Dec, focal length, pixel size, and binning.\n"
                "You can always type the comp and check magnitudes in by hand." + hint,
            )
            return
        if diag.get("stacked"):
            k, before, after = diag["stacked"]
            self.log(f"Solve: only {before} stars on one frame, so {k} frames were stacked first ({after} stars).")
        if diag.get("from_header"):
            self.log(f"Solve: placed with the plate solution in the FITS header ({solved['hits']} of "
                     f"{solved.get('match_count', n)} bright catalog stars on detected stars).")
            off = diag.get("header_target_off")
            if off is not None and off > max(3.0 * self._num(self.radius, 8.0), 8.0):
                tx, ty = diag["header_target_xy"]
                self.log(f"  The header's solution puts {self.star_id.get() or 'the target'} at ({tx:.0f}, {ty:.0f}), "
                         f"{off:.0f} px from the Target marker. Check the marker is on your star.")
                if not auto:
                    messagebox.showinfo(APP_TITLE, f"The plate solution in the FITS header puts "
                                                   f"{self.star_id.get() or 'the target'} {off:.0f} px from the Target "
                                                   f"marker, at ({tx:.0f}, {ty:.0f}). Check the marker is on your star "
                                                   "(the chart labels follow the header).")
        if diag.get("field_solved"):
            self.log(f"Solve: field solved from star patterns ({solved['hits']} of {solved.get('match_count', n)} "
                     "bright catalog stars matched); no target needed.")
        if diag.get("moved_from") is not None:
            old = diag["moved_from"]
            self.target_xy = tuple(diag["target"])
            self.log(f"Solve: the target marker at ({old[0]:.0f}, {old[1]:.0f}) was not on the target; the chart locked "
                     f"with the target at ({self.target_xy[0]:.0f}, {self.target_xy[1]:.0f}), and the marker was moved there.")
            messagebox.showinfo(APP_TITLE, f"The Target marker was on a neighboring star. The chart locked with the target "
                                           f"{math.hypot(self.target_xy[0] - old[0], self.target_xy[1] - old[1]):.0f} px "
                                           "away, and the marker has been moved there. Check it is your star.")
        h, w = self.preview.shape
        in_frame = [(x, y, star) for x, y, star in placed if 4 <= x < w - 4 and 4 <= y < h - 4]
        # Snap matched stars onto their detected centers so clicks land exactly.
        matched = {id(star): (x, y) for x, y, star in solved["placed"]}
        self.chart_placed = [(*(matched.get(id(star)) or (x, y)), star) for x, y, star in in_frame]
        for x, y, star in self.chart_placed:
            star["chartid"] = chart_id
        try:
            self.chart_solution = {
                "angle": solved["angle"], "mirrored": solved.get("mirrored", False),
                "scale_factor": solved.get("scale_factor", 1.0), "target_xy": diag.get("target"),
                "scale": diag.get("scale"), "ra_deg": diag["ref"][0], "dec_deg": diag["ref"][1],
                "from_header": bool(diag.get("from_header")),
            }
        except (TypeError, ValueError):
            self.chart_solution = None
        auto_target = diag.get("radec_given", True) and self._auto_target_marker()   # 2.2.15 (GitHub #3): before
        # the comps are suggested around it
        self._seq_names = {str(st.get("auid") or st.get("label") or "") for _x, _y, st in self.chart_placed
                           if st.get("seq")} if self.mode_key != "discovery" else set()
        self._seq_note_for = None
        self._apply_field_check(vet, solved, diag)
        self._restore_discovery_watch()   # 2.2.18 (item 12): this field's remembered Discovery watch stars
        self._locate_watch_stars()
        self._fill_marked_from_chart()
        self._suggest_comps()
        self._update_pick_label()
        self.update_star_panel()
        self.refresh_preview()
        if self.mode_key == "discovery":
            self._field_summary()
            self._update_discovery_panel()
        flip = ", mirrored" if solved.get("mirrored") else ""
        if abs(solved.get("scale_factor", 1.0) - 1.0) > 0.005:
            flip += f", scale x{solved['scale_factor']:.3f}"
        title = (f"Solved · AAVSO chart {chart_id}" if chart_id else "Solved")
        self.photo_ax.set_title(
            f"{title}  {hits}/{n} matched  rotation {solved['angle']:.1f}°{flip}  {len(self.chart_placed)} stars labeled"
        )
        self.photo_canvas.draw_idle()
        if n <= 2 and not auto:
            if not messagebox.askyesno(
                APP_TITLE,
                f"The catalogs have only {n} stars here, and both lined up. Two stars can line up by chance.\n\n"
                "Look at the circles. Are they on real stars, in the pattern the catalogs show?",
            ):
                self.chart_placed = []
                if auto_target and self.target_xy == getattr(self, "_auto_target_xy", None):
                    # 2.2.15 (review): the Target marker this solve placed goes with it.
                    self.target_xy = None
                    if getattr(self, "_pick_auto_advanced", False) and self.pick_mode.get() == "comp" \
                            and not getattr(self, "_pick_user_chosen", False):
                        self.pick_mode.set("target")   # 2.2.18 (item 7): no marker, so back to the Target step
                    self._update_pick_label()
                    self.update_star_panel()
                    self.log("Target: the marker placed from that solve was removed with it; click the target.")
                self.refresh_preview()
                return
        with_mag = sum(1 for _, _, star in self.chart_placed if star.get("mag") is not None)
        counts = {b: sum(1 for _, _, star in self.chart_placed if b in (star.get("mags") or {})) for b in ("B", "V", "R")}
        sloan = core.is_sloan(band)   # 2.2.17 (item 1)
        band_kind = band   # the catalog key (SI); band below is the label people read (i′)
        band = core.band_label(band)
        per_cat = {c: sum(1 for _, _, st in self.chart_placed if c in (st.get("sources") or {})) for c in core.CATALOGS}
        n_seq = per_cat.get("AAVSO sequence", 0)
        self.log("Solve: " + ", ".join(f"{c} {k}" for c, k in per_cat.items() if k) + f" stars on the frame, merged "
                 f"into {len(self.chart_placed)}; with B {counts['B']}, V {counts['V']}, R {counts['R']}"
                 + (f", {band} {with_mag} (" + ", ".join(
                     f"{core.CATALOG_SHORT.get(c, c)} {k}" for c, k in sorted(self._band_source_counts(band_kind).items(),
                                                                key=lambda ck: -ck[1]))
                    + ")" if sloan else "") + "."
                 + (f" AAVSO chart {chart_id}: {n_seq} sequence stars." if n_seq else " No AAVSO sequence stars here."))
        gd = diag.get("gaia_depth") or {}
        if gd.get("n") and gd.get("faintest") is not None:
            # 2.2.14 (item 5): how deep Gaia went, and whether the frame's star count or the G cut set it.
            if gd.get("max_rows") == core.GAIA_ROWS_MAX:
                why = "the most SHOBS-P asks for"
            elif gd.get("max_rows") == core.GAIA_ROWS_MIN:
                why = "the least SHOBS-P asks for"
            else:
                why = "about twice the stars seen on the frame"
            self.log(f"  Gaia DR3: {gd['n']} stars to G {gd['faintest']:.1f}"
                     + (f" (the brightest {gd.get('asked_rows', gd['max_rows'])}, {why})" if gd.get("capped")
                        else f" (every star to G {core.GAIA_DEEP_G:g})")
                     + ("; the frame's footprint from its plate solution" if gd.get("footprint") else "")
                     + (f". {with_mag} labeled stars with {band} on the frame for about {diag['n_peaks']} star-like "
                        "peaks." if diag.get("n_peaks") else "."))
        gst = gd.get("status") or {}
        if gst.get("deep") == "still running" and gst.get("esa") == "ok":
            # 2.2.16 (item 2): not lost: it finishes in the background (logged when it arrives).
            faint = gst.get("esa_faintest")
            self.log("  Gaia: the deep VizieR part is still running; the solve went on with the archive's stars (to G "
                     + (f"{faint:.1f}" if faint else "about 17") + "). It finishes in the background"
                     + (" and is kept for this field" if diag.get("cache_on") else "")
                     + "; the log says when it arrives.")
        elif gst.get("deep") not in (None, "ok") and gst.get("esa") == "ok":
            # 2.2.14 (review S3): half of Gaia missing; say what that costs tonight.
            faint = gst.get("esa_faintest")
            self.log("  Gaia: the deep VizieR part did not answer (" + str(gst["deep"]) + "); stars fainter than G "
                     + (f"{faint:.1f}" if faint else "about 15") + " have no Gaia magnitude. Solve again to retry.")
        if gst.get("esa") not in (None, "ok") and gst.get("deep") == "ok":
            self.log("  Gaia: the ESA archive's GSPC part did not answer (" + str(gst["esa"]) + "); no Gaia colors "
                     "tonight (V from G and BP-RP only). Solve again to retry.")
        for cat, err in (diag.get("catalog_errors") or {}).items():
            self.log(f"  {cat} did not answer ({err}); its stars are missing this time. Solve again to retry.")
        if sloan:
            # 2.2.17 (item 1): one Sloan band on a mono camera: the B-V / V-R color calibration does not apply.
            self.log(f"  Sloan {band} run: comps and reports use {band} magnitudes; the B-V / V-R color calibration "
                     "is not used.")
        elif counts["B"] == 0 or counts["V"] == 0:
            self.log("  No catalog here gives B magnitudes, so color (B-V) cannot be calibrated on this field.")
        elif counts["R"] == 0:
            self.log("  B and V present: B-V can be calibrated. No R, so V-R cannot.")
        if self.mode_key == "discovery":
            self.status.set(f"Field solved: {len(self.chart_placed)} catalog stars labeled ({with_mag} with {band} "
                            "magnitudes). Watch or exclude stars if you like, then Scan field.")
        else:
            self.status.set(
                f"{title}: {len(self.chart_placed)} stars labeled ({with_mag} with {band} magnitudes). "
                "Pick Comparison or Check, then click any star to fill its ID and magnitude (cyan circles: AAVSO "
                "sequence stars)."
                + (" Green rings: suggested comps near the target's brightness." if self.suggested_comps else "")
            )

    @staticmethod
    def _num(var, default=None):
        """A Tk variable as a float, or default when it is blank or not a number (real Tk raises TclError)."""
        try:
            value = float(var.get())
        except Exception:
            return default
        return value if math.isfinite(value) else default

    def _fill_marked_from_chart(self):
        """After Solve: a comp, comp 2, or check star marked before the chart (so its ID and magnitude are still
        empty) takes them from the labeled star under it. Names already there are left alone (2.2.1), except a
        catalog name from another night that belongs to a different star (2.2.8)."""
        filled = []
        for role, xy, name_var in ([(r, getattr(self, f"{r}_xy"), getattr(self, f"{r}_name")) for r in COMP_ROLES]
                                   + [("check", self.check_xy, self.check_name)]):
            if xy is None:
                continue
            star = self._chart_star_at(xy)
            if star is None:
                continue
            old = name_var.get().strip()
            if old:
                # 2.2.8: a catalog name left over from another night that belongs to a different star is corrected.
                # Left alone: names typed by hand (no sky position known for them), the same star under another
                # catalog's name (within 3"), and any case where the labeled star is not right under the marker.
                new = str(star.get("auid") or star.get("label") or "").strip()
                if not new or new == old or old in core.star_names(star):
                    self._note_sequence(role, old, star, xy)
                    continue
                rd = self.comp_coords.get(old)
                if rd is None or star.get("ra") is None or star.get("dec") is None:
                    continue
                if core.same_star_aliases({old: rd, new: (float(star["ra"]), float(star["dec"]))}):
                    self._note_sequence(role, old, star, xy)
                    continue
                seeing = self._seeing()
                if self._chart_star_at(xy, reach=max(2.0, seeing if math.isfinite(seeing) else 2.0)) is not star:
                    continue
                text = self._fill_from_chart(role, xy)
                if text and "=" in text:
                    self.log(f"{role_label(role)} name updated from {old} to {new}: the marked star is {new}.")
                continue
            text = self._fill_from_chart(role, xy)
            if text and "=" in text:
                filled.append(text)
        for text in filled:
            self.log("Solve filled in " + text)

    # ---- comps near the target's brightness (2.2) -----------------------------------------
    def _inst_at(self, xy) -> float:
        """Instrumental magnitude of the star at xy on the preview (NaN if it cannot be measured)."""
        if xy is None or self.preview is None or not self.preview_is_debayered:
            return float("nan")
        r, si, so = self._num(self.radius), self._num(self.sky_in), self._num(self.sky_out)
        if r is None or si is None or so is None or not (0 < r < si < so):
            return float("nan")
        key = (id(self.preview), round(xy[0], 1), round(xy[1], 1), r, si, so)
        cache = getattr(self, "_inst_cache", None)
        if cache is None or cache.get("_preview") is not self.preview:
            cache = self._inst_cache = {"_preview": self.preview}
        if key in cache:
            return cache[key]
        try:
            flux, _ = core.measure_many(self.preview, np.asarray([xy], dtype=float), key[3], key[4], key[5])
            f = float(flux[0])
        except Exception:
            f = float("nan")
        cache[key] = -2.5 * math.log10(f) if f > 0 else float("nan")
        return cache[key]

    def _suggest_comps(self):
        """Labeled stars within about 1 mag of the target, not variable, not saturated, not crowded at the edge."""
        self.suggested_comps = []
        self._closest_dm = None
        self._target_bv = None
        if self.mode_key == "discovery" or self.target_xy is None or not self.chart_placed:
            return
        t_inst = self._inst_at(self.target_xy)
        if not math.isfinite(t_inst):
            return
        sat, r = self._num(self.sat_limit, float("inf")) * 0.85, self._num(self.radius)
        si, so = self._num(self.sky_in), self._num(self.sky_out)
        if r is None or si is None or so is None or not (0 < r < si < so):
            return
        edge = so + 3
        h, w = self.preview.shape
        cands, where = [], {}
        stars = [(x, y, st) for x, y, st in self.chart_placed
                 if not st.get("variable") and st.get("mag") is not None and edge <= x < w - edge and edge <= y < h - edge
                 and math.hypot(x - self.target_xy[0], y - self.target_xy[1]) > 2 * r]
        if not stars:
            return
        if len(stars) > 1500:
            # 2.2.14 (item 5): with Gaia asked deep there can be ten thousand labeled stars; only those within a few
            # magnitudes of the target can be suggested, so measure those (the target's catalog V, else a zero point
            # from a sample of the field).
            t_star = self._chart_star_at(self.target_xy)
            t_cat = (t_star or {}).get("mag")
            if t_cat is None:
                sample = sorted(stars, key=lambda s_: s_[2]["mag"])[::max(1, len(stars) // 300)]
                try:
                    f_s, _ = core.measure_many(self.preview, np.asarray([(x, y) for x, y, _ in sample], dtype=float),
                                               r, si, so)
                    zp = [st["mag"] + 2.5 * math.log10(f) for (_x, _y, st), f in zip(sample, f_s) if f > 0]
                    t_cat = t_inst + float(np.median(zp)) if len(zp) >= 5 else None
                except Exception:
                    t_cat = None
            if t_cat is not None:
                stars = [s_ for s_ in stars if abs(s_[2]["mag"] - t_cat) <= 2.5] or stars
        xy = np.asarray([(x, y) for x, y, _ in stars], dtype=float)
        try:
            flux, _ = core.measure_many(self.preview, xy, r, si, so)
        except Exception:
            return
        ri = int(math.ceil(r))
        for (x, y, st), f in zip(stars, flux):
            if not (f > 0):
                continue
            x0, y0 = int(round(x)), int(round(y))
            box = self.preview[max(0, y0 - ri):y0 + ri + 1, max(0, x0 - ri):x0 + ri + 1]
            if box.size and np.nanmax(box) >= sat:
                continue
            name = str(st.get("auid") or st.get("label") or "")
            if not name:
                continue
            cands.append((name, -2.5 * math.log10(f), x, y, core.catalog_bv(st)))
            where[name] = (x, y)
        target_star = self._chart_star_at(self.target_xy)
        t_bv = core.catalog_bv(target_star) if target_star else None
        self._target_bv = t_bv
        self._closest_dm = min((abs(c[1] - t_inst) for c in cands), default=None)
        # 2.2.8: a faint target (near the noise floor) gets comps 0.5-2 mag brighter, not equally faint ones.
        t_snr = self._star_snr(self.preview, self.target_xy)
        faint = math.isfinite(t_snr) and t_snr < 30
        def choose(pool):
            if faint:
                got = core.suggest_comps_near(t_inst, pool, count=4, target_bv=t_bv, aim=-1.0, window=(-2.0, -0.5))
                return got or core.suggest_comps_near(t_inst, pool, count=4, target_bv=t_bv, aim=-1.0,
                                                      window=(-2.0, 0.3))
            return core.suggest_comps_near(t_inst, pool, max_dmag=1.0, count=4, target_bv=t_bv)

        # 2.2.13 (John): AAVSO sequence stars of the right brightness come first; other catalog stars fill in when
        # the sequence runs short (a faint transit host, a sparse field).
        seq_names = {str(st.get("auid") or st.get("label") or "") for _x, _y, st in stars if st.get("seq")}
        picks = choose([c for c in cands if c[0] in seq_names])
        if len(picks) < 4:
            have = {n for n, _dm in picks}
            picks += [p for p in choose([c for c in cands if c[0] not in have]) if p[0] not in have][:4 - len(picks)]
        self.suggested_comps = [(name, dm, where[name][0], where[name][1]) for name, dm in picks]
        if self.suggested_comps:
            self.log(("Suggested comps (the target is faint, so 0.5–2 mag brighter than it"
                      if faint else "Suggested comps (near the target in brightness")
                     + (f" and near its color, target B−V {t_bv:.2f}" if t_bv is not None else "")
                     + "; green rings): "
                     + ", ".join(f"{name} ({dm:+.1f} mag" + (", AAVSO sequence" if name in seq_names else "") + ")"
                                 for name, dm, _x, _y in self.suggested_comps)
                     + ". Comps like the target cancel airmass, focus, and calibration errors best.")
        elif self._closest_dm is not None and self._closest_dm <= 1.0:
            self.log("Labeled stars within 1 mag of the target here all differ from it in color (B−V more than 0.6 "
                     "apart); pick the closest in brightness and color you can.")
        elif self._closest_dm is not None:
            self.log(f"No labeled star within 1 mag of the target here (closest: {self._closest_dm:.1f} mag away); "
                     "pick the closest in brightness and color you can.")

    def _comp_brightness_note(self, role: str, xy):
        """How a comp compares with the target in brightness and color (2.2: brightness; 2.2.1: color, and no
        warning when nothing better exists in the field)."""
        if not comp_number(role) or xy is None or self.target_xy is None:
            return ""
        t, c = self._inst_at(self.target_xy), self._inst_at(xy)
        if not (math.isfinite(t) and math.isfinite(c)):
            return ""
        dm = c - t
        parts = []
        closest = getattr(self, "_closest_dm", None)
        best_available = closest is not None and closest > 1.0 and abs(dm) <= closest + 0.5
        if abs(dm) > 1.5 and best_available:
            parts.append(f"closest available: {abs(dm):.1f} mag {'brighter' if dm < 0 else 'fainter'} (no field "
                         "star is near this target's brightness)")
        elif dm < -1.5:
            parts.append(f"⚠ {abs(dm):.1f} mag brighter than the target: a much brighter comp reacts differently to "
                         "airmass, focus, and calibration errors, and can leave steps or trends. Prefer one within "
                         "about 1 mag" + (" — green rings." if self.suggested_comps else "."))
        elif dm > 1.5:
            parts.append(f"⚠ {dm:.1f} mag fainter than the target: its noise will dominate. Prefer one within about "
                         "1 mag.")
        else:
            parts.append(f"✓ {dm:+.1f} mag from the target")
        t_bv = getattr(self, "_target_bv", None)
        star = self._chart_star_at(xy)
        c_bv = core.catalog_bv(star) if star else None
        if t_bv is not None and c_bv is not None and abs(c_bv - t_bv) > 0.5:
            parts.append(f"⚠ B−V {c_bv:.2f} against the target's {t_bv:.2f}: a comp of another color dims differently "
                         "with airmass, which leaves a slow trend. Prefer one within about 0.5 in B−V.")
        return "   ".join(parts)

    def _apply_field_check(self, vet, solved, diag):
        """Keep the field's known and suspected variables, mark them on the image, and say what was checked."""
        self.variable_marks = []
        if not vet:
            return
        key, variables, answered = vet
        self._field_var_cache = vet
        self.field_variables = variables
        self.field_check_sources = list(answered)
        flagged = sum(1 for _x, _y, st in self.chart_placed if st.get("variable"))
        if not answered:
            self.field_check_text.set("Variable-star check: VSX and SIMBAD could not be reached, so the comps were "
                                      "not checked. Solve again later.")
            self.log("Variable-star check: VSX and SIMBAD could not be reached; comps and spares were not checked.")
            return
        h, w = self.preview.shape
        try:
            spots = core.place_stars(variables, solved, diag.get("target"), diag["ref"][0], diag["ref"][1],
                                     diag.get("scale"))
            self.variable_marks = [(x, y, v) for x, y, v in spots if 0 <= x < w and 0 <= y < h]
        except Exception:
            self.variable_marks = []
        src = " + ".join(answered)
        self.field_check_text.set(
            f"Variable-star check ({src}): {len(self.variable_marks)} known or suspected variables in the frame"
            f"; {flagged} of them are labeled stars (red circle), the rest are red × (Show variables). Comps and the "
            "check star are checked by position, whatever catalog you labeled with. Variables are never used as "
            "spares or in the color fit. Hover to see what they are.")
        self.log(f"Variable-star check ({src}): {len(self.variable_marks)} known or suspected variables in the frame; "
                 f"{flagged} labeled star(s) flagged and left out of spares and the color fit.")

    # ---- Discovery mode ---------------------------------------------------------------
    def scan_field(self):
        lights = self._lights(log_skips=True)
        if not lights or self.masters is None or self.preview is None or not self.preview_is_debayered:
            messagebox.showinfo(APP_TITLE, "Calibrate first (Calibrate page). The scan uses the same frames, "
                                           "calibration, alignment, and aperture as photometry.")
            return
        try:
            binning = max(int(self.binning.get()), 1)
            radius = float(self.radius.get())
            sky_in = float(self.sky_in.get())
            sky_out = float(self.sky_out.get())
            if not (0 < radius < sky_in < sky_out):
                raise ValueError("Need aperture r < sky in < sky out")
            min_p, max_p = float(self.min_period.get()), float(self.max_period.get())
            if self.mode_key == "discovery" and hasattr(self, "disc_min_period"):
                # 2.2.18 (item 16): Discovery's own period range, on its own page.
                rng, why = self._read_period_range(self.disc_min_period, self.disc_max_period)
                if rng is None:
                    raise ValueError(f"Period search: {why}.")
                min_p, max_p = rng
                self._disc_range_used = rng
                self._remember_disc_range(rng)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        if self.masters_binning is not None and binning != self.masters_binning:
            messagebox.showerror(APP_TITLE, "Binning changed since the masters were built. Rebuild the masters first.")
            return
        discovery = self.mode_key == "discovery"
        try:
            nsigma = float(self.scan_sigma.get()) if discovery else 5.0
            excess_limit = float(self.scan_excess.get()) if discovery else 3.0
            mag_limit = float(self.scan_mag_limit.get()) if discovery and self.scan_mag_limit.get().strip() else None
        except Exception:
            messagebox.showerror(APP_TITLE, "Check the scan settings (threshold, candidate level, faintest magnitude).")
            return
        ref = self.preview
        h, w = ref.shape
        edge = sky_out + 3
        cap = 400
        if discovery:
            try:
                cap = max(50, int(self.scan_max_stars.get()))
            except Exception:
                cap = 800
        found_all = core.detect_sources(ref, max_sources=cap, nsigma=max(nsigma, 2.0), min_sep=max(2 * radius, 8.0))
        if len(found_all) >= cap:
            self.log(f"Field scan: the {cap}-star limit was reached, so only the brightest {cap} stars are measured"
                     + (" (raise \"Most stars to measure\" to go fainter)." if discovery else "."))
        found = [(x, y) for x, y in found_all if edge <= x < w - edge and edge <= y < h - edge]
        crowded = 0
        if discovery and self.scan_skip_crowded.get() and found:
            # A neighbor inside the sky annulus spoils both the star and its sky: leave such pairs out.
            from scipy.spatial import cKDTree
            tree = cKDTree(np.asarray(found_all, dtype=float))
            counts = tree.query_ball_point(np.asarray(found, dtype=float), r=sky_out, return_length=True)
            keep = [p for p, c in zip(found, counts) if c <= 1]      # itself only
            crowded = len(found) - len(keep)
            found = keep
        excluded = 0
        if self.scan_excluded:
            keep = []
            for x, y in found:
                if any(math.hypot(x - ex, y - ey) <= max(radius, 4.0) for ex, ey in self.scan_excluded):
                    excluded += 1
                else:
                    keep.append((x, y))
            found = keep
        labels, xy = [], []
        marks = [] if discovery else [(role_label(r), xy) for r, xy in self._marked_roles()]
        for label, pos in marks:
            if pos is not None:
                labels.append(label)
                xy.append(pos)
        watched = []
        for name, pos in self.watch_xy.items():
            if all(math.hypot(pos[0] - px, pos[1] - py) > radius for px, py in xy):
                watched.append(len(xy))
                labels.append("W " + name.replace(",", " "))
                xy.append(tuple(pos))
        marked = list(xy)
        n_s = 0
        for x, y in found:
            if all(math.hypot(x - px, y - py) > radius for px, py in marked):
                xy.append((x, y))
                n_s += 1
                labels.append(f"S{n_s:03d}")
        if len(xy) < 8:
            messagebox.showinfo(APP_TITLE, "Fewer than 8 stars found in the frame; a field scan needs more.")
            return
        # Saturated stars (peak near the saturation level on the reference frame) wobble from clipping, not
        # variability: they are measured but never listed as candidates.
        try:
            sat_level = 0.85 * float(self.sat_limit.get())
        except (TypeError, ValueError):
            sat_level = float("inf")
        try:
            near_sat_level = 0.80 * float(self.sat_limit.get())
        except (TypeError, ValueError):
            near_sat_level = float("inf")
        r_box = int(math.ceil(radius))
        saturated = set()
        for k, (sx, sy) in enumerate(xy):
            x0, y0 = int(round(sx)), int(round(sy))
            box = ref[max(0, y0 - r_box):y0 + r_box + 1, max(0, x0 - r_box):x0 + r_box + 1]
            if box.size and np.nanmax(box) >= sat_level:
                saturated.add(k)
        mode = self.debayer_mode.get()
        pattern = self.pattern.get()
        masters = self.masters
        color = bool(self.measure_color.get())
        if color and self._sloan_run():
            color = False   # 2.2.17 (item 1): no B-V / V-R from one Sloan filter
            self.log(f"Field scan: color not measured on a Sloan {core.band_label(self._band())} run.")
        if color and self._rgb_channel:
            color = False   # 2.2.18 (item 1): one channel of the colour images per run
            self.log("Field scan: color not measured on color images (one channel per run).")
        comp_name = self.comp_name.get().strip()
        comp_mag = None
        try:
            comp_mag = float(self.comp_mag.get()) if self.comp_mag.get().strip() else None
        except ValueError:
            comp_mag = None
        comp_colors = self._colors_for("comp", comp_name)   # 2.2.14 (item 1)
        solution = dict(self.chart_solution) if (self.chart_solution and self.chart_placed) else None
        field_known = (list(self.field_variables), list(self.field_check_sources)) if self.field_check_sources else None
        # Catalog colors for scan stars that sit on a labeled chart star (for the color fit).
        n_xy = len(xy)
        cat_color = {"bv": np.full(n_xy, np.nan), "vr": np.full(n_xy, np.nan)}
        if color and self.chart_placed:
            tol = max(2.0, 0.5 * radius)
            for cx, cy, star in core.color_calibration_stars(self.chart_placed, None, radius, sky_out, ref.shape, 400):
                d = [math.hypot(px - cx, py - cy) for px, py in xy]
                k = int(np.argmin(d))
                if d[k] <= tol:
                    # 2.2.14 (item 1): each color from one catalog.
                    for key in ("bv", "vr"):
                        value = core.catalog_color(star, key)[0]
                        if value is not None:
                            cat_color[key][k] = value
        saved_cal = core.load_color_calibration() if color else None
        # Catalog magnitudes for the scan stars that sit on a labeled (non-variable) chart star: the ensemble zero
        # point that puts the whole scan on the catalog's scale (2.2).
        cat_mag = np.full(n_xy, np.nan)
        cat_name = [""] * n_xy
        if self.chart_placed and n_xy:
            # 2.2.13 (review): each scan star takes its CLOSEST labeled star (the brighter one on a tie); with Gaia
            # always merged in, a faint neighbour later in the list used to overwrite a bright star's name and mag.
            tol = max(2.0, 0.5 * radius)
            xy_arr = np.asarray(xy, dtype=float)
            best_d = np.full(n_xy, np.inf)
            best_m = np.full(n_xy, np.inf)
            for cx, cy, star in self.chart_placed:
                if star.get("mag") is None:
                    continue
                d = np.hypot(xy_arr[:, 0] - cx, xy_arr[:, 1] - cy)
                k = int(np.argmin(d))
                m = float(star["mag"])
                if d[k] <= tol and (d[k] < best_d[k] - 0.5 or (abs(d[k] - best_d[k]) <= 0.5 and m < best_m[k])):
                    best_d[k], best_m[k] = d[k], m
                    cat_name[k] = str(star.get("auid") or star.get("label") or "")
                    cat_mag[k] = m if not star.get("variable") else np.nan
        catalog_used = f"catalog {core.band_label(self._band())} (best of AAVSO, APASS, Gaia, Tycho per star)"
        if not self._start_job(f"Scanning {len(xy)} stars in {len(lights)} frames…"):
            return
        self.photo_bring_back()   # 2.2.16 (item 16)
        self._progress_start(len(lights), "Field scan")
        self.log(f"Field scan: {len(xy)} stars, {len(lights)} frames, aperture {radius:.1f} px"
                 + (", with R and B" if color else "")
                 + (f"; {crowded} crowded star(s) left out" if crowded else "")
                 + (f"; {excluded} excluded by you" if excluded else ""))
        xy0 = np.asarray(xy, dtype=float)
        altaz = self._mount_info(lights)["mount"] == core.MOUNT_ALTAZ   # 2.2.18 (item 8)

        cancel = self._cancel

        def work():
            try:
                ref_sources = core.detect_sources(ref, max_sources=80)
                last = {"sources": None, "reg": None}
                rows, jds = [], []
                frame_side = []
                peaks = []
                chans = {"green": [], "red": [], "blue": []} if color else None
                for i, path in enumerate(lights, 1):
                    if cancel.is_set():
                        raise RuntimeError("Cancelled")
                    try:
                        raw, header = core.read_fits(path)
                        cal = core.calibrate_frame(core.bin_image(raw, binning), header, masters)
                        img = core.debayer(cal, pattern, mode)
                        if img.shape != ref.shape:
                            raise ValueError("frame size differs from the reference")
                        src = core.detect_sources(img, max_sources=80)
                        if altaz:   # 2.2.18 (item 8): field rotation, never a meridian flip
                            reg = core.register_rotating(ref_sources, src, img.shape, last["sources"], last["reg"])
                        else:
                            reg = core.register_frame(ref_sources, src, img.shape)
                        if reg is None and last["sources"] is not None and not altaz:
                            step = core.register_frame(last["sources"], src, img.shape)
                            if step is not None:
                                reg = core.compose_registration(last["reg"], step, img.shape)
                        if reg is None:
                            raise ValueError("could not align")
                        last["sources"], last["reg"] = src, reg
                        pos = core.move_points(xy0, reg, img.shape)
                        exp = core.header_exptime(header)
                        try:
                            flux, _, noise = core.measure_many(img, pos, radius, sky_in, sky_out, noise=True)
                        except TypeError:
                            flux, _ = core.measure_many(img, pos, radius, sky_in, sky_out)
                            noise = None
                        # 2.2.12: a frame where the star is not detected is left out of its curve and scatter.
                        row = core.detected_mags(flux, exp, noise)
                        peak_row = core.peak_values(img, pos, radius)
                        frame_jd = core.mid_jd(header)
                        side = (bool(reg.get("flipped")), core.header_rotator(header))
                        crow = {}
                        if color:
                            for ch in ("green", "red", "blue"):
                                cimg = img if ch == mode else core.debayer(cal, pattern, ch)
                                cf, _ = core.measure_many(cimg, pos, radius, sky_in, sky_out)
                                with np.errstate(divide="ignore", invalid="ignore"):
                                    crow[ch] = np.where(cf > 0, -2.5 * np.log10(cf / exp), np.nan)
                        # Everything for this frame is in hand: add it to every list together, so a frame that
                        # fails half-way can never leave the lists out of step (2.2.1).
                        rows.append(row)
                        jds.append(frame_jd)
                        frame_side.append(side)
                        peaks.append(peak_row)
                        if color:
                            for ch in ("green", "red", "blue"):
                                chans[ch].append(crow[ch])
                    except Exception as exc:
                        self.call_ui(self.log, f"  scan skipped {os.path.basename(path)}: {exc}")
                    self.call_ui(self._progress, i)
                    if i % 10 == 0 or i == len(lights):
                        self.call_ui(self.status.set, f"Field scan: {i}/{len(lights)} frames…")
                if len(rows) < 10:
                    raise ValueError(f"only {len(rows)} frames could be measured; a scan needs at least 10")
                inst = np.vstack(rows)
                jd = np.array(jds)
                # 2.2.1: a meridian flip (or a rotator move) puts every star on another part of the sensor and the
                # flat, so each star can step there. Line each star's later segments up with its first one.
                seg = np.zeros(len(frame_side), dtype=int)
                for k in range(1, len(frame_side)):
                    (f0, r0), (f1, r1) = frame_side[k - 1], frame_side[k]
                    moved = f0 != f1 or (r0 is not None and r1 is not None and core.angle_difference(r0, r1) > 2.0)
                    seg[k] = seg[k - 1] + (1 if moved else 0)
                n_segments = int(seg.max()) + 1 if len(seg) else 1
                if n_segments > 1:
                    inst = core.align_segments(inst, seg, min_frames=5)
                    self.call_ui(self.log, f"  {n_segments - 1} meridian flip(s) or rotator move(s): each star's light "
                                           "curve lined up across them before measuring its scatter.")
                stats = core.field_variability(inst, exclude=sorted(saturated))
                offset = 0.0
                zp = None
                c1 = role_label("comp")
                if c1 in labels and comp_mag is not None and math.isfinite(stats["median"][labels.index(c1)]):
                    offset = comp_mag - stats["median"][labels.index(c1)]
                else:
                    use = np.asarray(stats["usable"], dtype=bool).copy()
                    for k in saturated:
                        use[k] = False
                    zp = core.catalog_zero_point(stats["median"], cat_mag, use)
                    if zp is not None:
                        offset = zp["offset"]
                        self.call_ui(self.log, f"  Magnitude scale: {catalog_used} zero point from {zp['n']} field "
                                               f"stars, ± {zp['err']:.3f} (star-to-star scatter {zp['scatter']:.3f}; "
                                               "untransformed).")
                result = {
                    "labels": labels, "xy": [tuple(p) for p in xy0], "jd": jd,
                    "light_curves": stats["corrected"] + offset,
                    "mag": stats["median"] + offset, "scatter": stats["scatter"], "expected": stats["expected"],
                    "excess": stats["excess"], "usable": stats["usable"], "n_good": stats["n_good"],
                    "calibrated": offset != 0.0, "frames": len(rows), "zp": zp, "zp_catalog": catalog_used,
                    "watched": watched, "names": cat_name,
                }
                too_faint = set()
                limit = mag_limit
                if limit is None:
                    # 2.2.12: no faintest magnitude typed: stop where the field's typical scatter reaches 0.25 mag.
                    auto = core.faint_limit(result["mag"], result["expected"], 0.25)
                    result["auto_mag_limit"] = auto
                    if auto is not None:
                        limit = auto
                        self.call_ui(self.log, f"  Faintest magnitude {auto:.1f}"
                                               + ("" if offset != 0.0 else " (instrumental)")
                                               + ": the field's typical scatter reaches 0.25 mag there."
                                               + (" Type a faintest magnitude to choose another." if offset != 0.0
                                                  else " (A typed faintest magnitude needs a catalog zero point: "
                                                       "run Solve first.)"))
                if limit is not None and (offset != 0.0 or mag_limit is None):
                    too_faint = {i for i in range(len(labels)) if math.isfinite(result["mag"][i])
                                 and result["mag"][i] > limit}
                result["too_faint"] = sorted(too_faint)
                cand = [i for i in range(len(labels))
                        if stats["usable"][i] and math.isfinite(stats["excess"][i]) and stats["excess"][i] >= excess_limit
                        and stats["scatter"][i] >= 0.005 and i not in saturated and i not in too_faint]
                result["saturated"] = sorted(saturated)
                if saturated:
                    self.call_ui(self.log, f"  {len(saturated)} saturated star(s) measured but not listed as candidates "
                                           f"(peak at or above {sat_level:.0f} on the reference frame).")
                cand.sort(key=lambda i: -stats["excess"][i])
                result["candidates"] = cand
                result["excess_limit"] = excess_limit
                result["segments"] = n_segments
                near = [i for i in range(len(labels))
                        if i not in cand and i not in saturated and i not in too_faint and stats["usable"][i]
                        and math.isfinite(stats["excess"][i])]
                near.sort(key=lambda i: -stats["excess"][i])
                result["near"] = near[:10]
                # 2.2.2 cautions. Near saturation: the peak pixel reached 80% of the saturation level in some frame
                # (a core that clips now and then adds scatter that looks like variability).
                with np.errstate(all="ignore"):
                    top = np.nanmax(np.vstack(peaks), axis=0)
                result["near_sat"] = sorted(i for i in range(len(labels)) if i not in saturated
                                            and math.isfinite(top[i]) and top[i] >= near_sat_level)
                # Flip boundary: the biggest change starts at a meridian flip, rotator move or long gap.
                result["boundaries"] = core.scan_boundaries(seg, jd)
                at_flip = {}
                for i in set(cand) | set(result["near"]) | set(watched):
                    kind = core.change_at_boundary(result["light_curves"][:, i], result["boundaries"])
                    if kind:
                        at_flip[i] = kind
                result["at_boundary"] = at_flip
                # 2.2.7: also quiet on one side of a flip or gap and noisy on the other.
                noisy = {}
                for i in set(cand) | set(result["near"]) | set(watched):
                    kind = core.noise_at_boundary(result["light_curves"][:, i], result["boundaries"])
                    if kind:
                        noisy[i] = kind
                result["noise_at_boundary"] = noisy
                n_ns = len([i for i in cand if i in set(result["near_sat"])])
                n_fb = len([i for i in cand if i in at_flip or i in noisy])
                if n_ns or n_fb:
                    self.call_ui(self.log, f"  Cautions on candidates: {n_ns} near saturation, {n_fb} with a change or "
                                           "a jump in noise starting at a flip, rotator move or gap.")
                periods = {}
                for i in list(cand[:40]) + [k for k in watched if k not in cand[:40]]:
                    good = np.isfinite(result["light_curves"][:, i])
                    try:
                        ls = core.lomb_scargle(jd[good], result["light_curves"][good, i], min_p, max_p)
                    except Exception:
                        ls = None
                    if ls:
                        periods[i] = {"period": ls["period_days"], "power": ls["power"], "ls": ls}
                result["periods"] = periods
                result["period_range"] = (float(min_p), float(max_p))   # 2.2.18 (item 16)
                result["bv"] = result["vr"] = None
                result["color_fits"], result["color_method"] = {}, {}
                if color:
                    stacks = {c: np.vstack(chans[c]) for c in ("green", "red", "blue")}
                    # Leave clouded frames (field zero point more than ~20% faint) out of the color fit.
                    zp = np.asarray(stats["zero_point"], dtype=float)
                    frame_ok = (zp - np.median(zp)) < 0.24
                    ci = labels.index(role_label("comp")) if role_label("comp") in labels else None
                    # 2.2.14 (item 1): the comp's colors, each from one catalog.
                    comp_cat = {k: core.catalog_color(comp_colors, k)[0] for k in ("bv", "vr")}
                    for key, _b1, _b2, c1, c2 in core.COLOR_INDICES:
                        try:
                            inst, fit, calibrated = core.field_star_colors(stacks[c1], stacks[c2], cat_color[key], frame_ok)
                        except Exception as exc:   # 2.2.16 (item 3): a color failure must not lose the scan
                            self.call_ui(self.log, f"  Scan color: color fit failed: {type(exc).__name__}: {exc}")
                            result[key] = None
                            continue
                        old = (saved_cal or {}).get(key)
                        result["color_fits"][key] = fit
                        if calibrated is not None:
                            values, method = calibrated, "field"
                        elif ci is not None and comp_cat[key] is not None and core.color_fit_usable(old):
                            values, method = comp_cat[key] + old["k"] * (inst - inst[ci]), "saved"
                        elif ci is not None and comp_cat[key] is not None:
                            values, method = comp_cat[key] + (inst - inst[ci]), "comps"
                        else:
                            values, method = inst, "relative"
                        result[key] = values
                        result["color_method"][key] = method
                        self.call_ui(self.log, "  Scan color " + core.color_fit_text(
                            "B-V" if key == "bv" else "V-R", fit) + f"  -> {method}")
                result["radec"] = None
                result["vsx"] = None
                if solution:
                    result["radec"] = [core.pixel_to_sky(x, y, solution) for x, y in result["xy"]]
                    try:
                        half_diag_deg = math.hypot(w, h) / 2.0 * solution["scale"] / 3600.0 * 1.1
                        if field_known is not None and field_known[1]:
                            known, sources = field_known
                        else:
                            known, sources = core.field_variables(solution["ra_deg"], solution["dec_deg"], half_diag_deg)
                        if not sources:
                            raise ConnectionError("VSX and SIMBAD could not be reached")
                        result["vsx_sources"] = list(sources)
                        result["known"] = [k for k in known
                                           if 0 <= core.sky_to_pixel(k["ra"], k["dec"], solution)[0] < w
                                           and 0 <= core.sky_to_pixel(k["ra"], k["dec"], solution)[1] < h]
                        tol = max(8.0, 3.0 * solution["scale"])
                        matches = []
                        for ra, dec in result["radec"]:
                            best = None
                            for k in known:
                                d = core.sky_separation_arcsec(ra, dec, k["ra"], k["dec"])
                                if d < tol and (best is None or d < best[0]):
                                    best = (d, k)
                            matches.append(best[1] if best else None)
                        result["vsx"] = matches
                    except Exception as exc:
                        self.call_ui(self.log, f"  Variable-star check skipped: {exc}")
                self.call_ui(self._scan_done, result, None)
            except Exception as exc:
                self.call_ui(self._scan_done, None, (str(exc), traceback.format_exc()))

        self._blink_worker = threading.Thread(target=work, daemon=True)
        self._blink_worker.start()

    def _scan_done(self, result, error):
        self._end_job()
        if error is not None and error[0] == "Cancelled":
            self._progress_end("Cancelled.")
            self.log("Field scan cancelled.")
            return
        self._progress_end("" if error is None else "Failed.")
        if error is not None:
            self.log(error[1])
            messagebox.showerror(APP_TITLE, f"Field scan failed: {error[0]}")
            return
        n = len(result["candidates"])
        known = sum(1 for i in result["candidates"] if result.get("vsx") and result["vsx"][i])
        self.last_scan = result
        src = " / ".join(result.get("vsx_sources") or ["VSX"])
        self.log(f"Field scan done: {len(result['labels'])} stars, {result['frames']} frames, {n} candidate(s)"
                 + (f", {known} already known ({src})" if result.get("vsx") is not None
                    else ", no sky positions (run Solve for the VSX/SIMBAD check)"))
        self.status.set(f"Field scan: {n} candidate variable(s).")
        if self.mode_key == "discovery":
            self._show_scan_on_output(result)
            self.show_step(STEP_OUTPUT)
        else:
            ScanWindow(self, result)

    def _page_discovery_output(self):
        page = ttk.Frame(self.pages)
        card = ttk.Frame(page, style="Card.TFrame", padding=4)
        card.pack(fill=BOTH, expand=True)
        self.discovery_host = ttk.Frame(card, style="Card.TFrame")
        self.discovery_host.pack(fill=BOTH, expand=True)
        self._discovery_placeholder()
        nav = ttk.Frame(card, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", pady=(4, 0))
        ttk.Button(nav, text="Back", command=lambda: self.show_step(STEP_PHOTO)).pack(side=RIGHT, padx=8, pady=4)
        return page

    def _discovery_placeholder(self):
        """2.2.9: before a scan the Field scan page shows its empty plot frame and table, as Variables and Transits
        show theirs (John)."""
        for child in self.discovery_host.winfo_children():
            child.destroy()
        top = ttk.Frame(self.discovery_host, style="Card.TFrame", padding=10)
        top.pack(fill=BOTH, expand=True)
        ttk.Label(top, text="Output – Field scan", font=(core.UI_FONT, 16, "bold"), style="Card.TLabel").pack(anchor=W)
        self.discovery_empty = ttk.Label(
            top, text="No field scan yet: Scan field on the Photometry page. (Calibrate first; the field is solved "
                      "straight away, so every star gets a position, a catalog magnitude and a VSX/SIMBAD check.)",
            style="Hint.TLabel", justify=LEFT, wraplength=1300)
        self.discovery_empty.pack(anchor=W, pady=(2, 6))
        # The same Results row as after a scan (John, 10 Oct). As on the Variables and Transit fit pages the buttons
        # stay active and say what to do first.
        results = ttk.Frame(top, style="Card.TFrame")
        results.pack(fill=X, side="bottom", pady=(4, 0))
        ttk.Label(results, text="Results", style="Hint.TLabel", width=8).pack(side=LEFT)

        def need_scan():
            messagebox.showinfo(APP_TITLE, "Scan the field first (Scan field on the Photometry page).")
        for i, label in enumerate(("Save plot PNG…", "Save CSV…", "Export candidates CSV…",
                                   "Export all light curves CSV…")):
            ttk.Button(results, text=label, style="Accent.TButton" if i == 0 else "TButton",
                       command=need_scan).pack(side=LEFT, padx=6 if i % 2 else 0)
        ttk.Button(results, text="Back", command=lambda: self.show_step(STEP_PHOTO)).pack(side=RIGHT)
        body = ttk.Frame(top, style="Card.TFrame")
        body.pack(fill=BOTH, expand=True)
        tree = ttk.Treeview(body, columns=("rank", "star", "mag", "scatter", "excess", "period", "color", "caution",
                                           "vsx"), show="headings", height=8)
        for col, label in (("rank", "#"), ("star", "Star / position"), ("mag", "Mag"), ("scatter", "Scatter"),
                           ("excess", "× normal"), ("period", "Best period (d)"), ("color", "B−V"),
                           ("caution", "Check first"), ("vsx", "VSX")):
            tree.heading(col, text=label)
        tree.pack(fill=X, side="bottom", pady=(6, 0))
        if Figure is None:
            return
        fig = Figure(figsize=(11, 5.5), dpi=100, facecolor="#ffffff")
        ax_s = fig.add_subplot(1, 2, 1)
        ax_lc = fig.add_subplot(2, 2, 2)
        ax_ls = fig.add_subplot(2, 2, 4)
        ax_s.set_xlabel("Magnitude")
        ax_s.set_ylabel("Scatter (mag, robust)")
        ax_s.set_title("Scatter vs brightness")
        ax_lc.set_title("Selected star")
        ax_ls.set_xlabel("Period (d)")
        for ax in (ax_s, ax_lc, ax_ls):
            ax.grid(True, alpha=0.3)
        ax_s.text(0.5, 0.5, "No field scan yet", transform=ax_s.transAxes, ha="center", va="center", color="#7a8a94")
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=body)
        canvas.get_tk_widget().configure(width=400, height=300)
        canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        canvas.draw_idle()

    def _show_scan_on_output(self, result):
        for child in self.discovery_host.winfo_children():
            child.destroy()
        self.scan_view = ScanView(self.discovery_host, self, result)

    def mark_scan_candidates(self, result, only=None):
        # 2.2.5: each mark carries its kind (possible new / known / check first / unchecked) and the reason.
        # 2.2.7: only= a list of candidates to mark (the rows selected); their numbers stay as in the table.
        keep = set(only) if only else None
        self.scan_marks = [(result["xy"][i][0], result["xy"][i][1], str(rank), *core.scan_candidate_kind(result, i))
                           for rank, i in enumerate(result["candidates"], 1) if keep is None or i in keep]
        self.show_step(STEP_PHOTO)
        self.refresh_preview()

    def clear_scan_marks(self):
        self.scan_marks = []
        self.refresh_preview()
        self.status.set("Candidate marks cleared.")

    def _warn_marker_offsets(self, radius: float) -> list:
        """2.2.18 (item 5): before photometry, say in the Log when a marked star's marker is off the centre of its
        star by more than about 1.5 FWHM (or more than the frames' centring may move it): every frame is then measured
        at the marker, beside the star, and the star reads faint (HD 219134, 2.2.17 rebuild: a check 9 px off read
        2.2 mag faint). Returns [(label, offset px)] of the markers it warned about. Never blocks the run."""
        if self.preview is None or not self.preview_is_debayered:
            return []
        seeing = self._seeing()
        fwhm = seeing if math.isfinite(seeing) else None
        rows = [("Target", self.target_xy)] + [(role_label(r), getattr(self, f"{r}_xy")) for r in COMP_ROLES] \
            + [("Check", self.check_xy)]
        warned = []
        for label, xy in rows:
            if xy is None:
                continue
            try:
                info = core.marker_offset(self.preview, xy[0], xy[1], radius, fwhm)
            except Exception:
                continue
            if not info["off"]:
                continue
            warned.append((label, info["offset"]))
            self.log(f"⚠ {label} marker at ({xy[0]:.1f}, {xy[1]:.1f}) is {info['offset']:.1f} px from the center of "
                     f"the star there ({info['x']:.1f}, {info['y']:.1f})"
                     + (f"; the stars are {fwhm:.1f} px across (FWHM)" if fwhm else "")
                     + ". The frames are measured at the marker, so this star would read faint. Clear it and click "
                       "the star again (zoom in first) if that is not intended.")
        if warned:
            self.status.set("Check the Log: " + ", ".join(label for label, _o in warned)
                            + (" is" if len(warned) == 1 else " are") + " marked off the center of the star.")
        return warned

    def _markers_in_order(self) -> bool:
        """2.2.3: before photometry, every marked star must be on the image and on a star, and no star may have a
        name in the Stars panel without a marker (a leftover from an earlier run). Returns False to stop."""
        if self.preview is None:
            return True
        h, w = self.preview.shape[:2]
        names = {"target": ("Target", self.star_id)}
        names.update({r: (role_label(r), getattr(self, f"{r}_name")) for r in COMP_ROLES})
        names["check"] = ("Check", self.check_name)
        try:
            radius = float(self.radius.get())
        except Exception:
            radius = 8.0
        for role, (label, _var) in names.items():
            xy = getattr(self, f"{role}_xy")
            if xy is None:
                continue
            x, y = xy
            if not (0 <= x < w and 0 <= y < h):
                messagebox.showerror(APP_TITLE, f"The {label} marker is off the image ({x:.0f}, {y:.0f}; the image is "
                                                f"{w}×{h}). Clear it and click the star again.")
                return False
            if not core.star_under(self.preview, x, y, radius):
                if not messagebox.askyesno(APP_TITLE, f"There is no star under the {label} marker at ({x:.0f}, {y:.0f}). "
                                                      "Measure blank sky there anyway?"):
                    return False
        self._warn_marker_offsets(radius)   # 2.2.18 (item 5)
        ghosts = [(role, label, var.get().strip()) for role, (label, var) in names.items()
                  if role != "target" and var.get().strip() and getattr(self, f"{role}_xy") is None]
        if ghosts:
            listing = "\n".join(f"  • {label}: {name}" for _r, label, name in ghosts)
            answer = messagebox.askyesnocancel(
                APP_TITLE,
                "These have a name in the Stars panel but no marker on this image, so they would not be measured:\n"
                f"{listing}\n\nYes: clear those names and continue.\nNo: continue and leave them as they are.\n"
                "Cancel: stop, so you can mark them.")
            if answer is None:
                return False
            if answer:
                for role, _label, _name in ghosts:
                    self.clear_mark(role, redraw=False)
                self._update_pick_label()
                self.refresh_preview()
                self.update_star_panel()
        return True

    def run_photometry(self):
        self._run_mount = None   # 2.2.18 (item 8): set once the run starts (below)
        lights = self._lights(log_skips=True)
        if not lights:
            messagebox.showerror(APP_TITLE, "No light frames left after the blink reject.")
            return
        if self.masters is None:
            messagebox.showerror(APP_TITLE, "Build calibration masters first.")
            return
        if not self._aperture_sane():
            return
        if not self._markers_in_order():
            return
        if not self._mode_guard(lights):   # 2.2.14 (item 19): includes the 2.2.9 transit-in-the-lights check
            return
        self._sequence_note()
        try:
            binning = max(int(self.binning.get()), 1)
            if self.masters_binning is not None and binning != self.masters_binning:
                raise ValueError(
                    f"Binning is {binning} but the masters were built at {self.masters_binning}. Rebuild the masters or set it back."
                )
            target = self._star(self.star_id.get() or "target", self.target_xy, "", False)
            # 2.2.5: every marked comparison star, C1 … C10, in order. Each needs a catalog magnitude.
            comp_roles = self._comp_roles_marked()
            if not comp_roles:
                raise ValueError("Mark at least one comparison star (Comps, then click the star)")
            # 2.2.17 (item 1, review M2): a comp labeled from a chart in another band (V comps under a Sloan filter
            # picked after the solve) would zero-point the night on the wrong band.
            wrong = [role_label(r) for r in comp_roles + (["check"] if self.check_xy is not None else [])
                     if self._wrong_band_star(getattr(self, f"{r}_xy"))]
            if wrong:
                raise ValueError(f"{', '.join(wrong)}: the labeled catalog is in another band than the comps' "
                                 f"{core.band_label(self._band())}. Solve again so the comps get "
                                 f"{core.band_label(self._band())} magnitudes")
            missing = [role_label(r) for r in comp_roles if not getattr(self, f"{r}_mag").get().strip()]
            if missing and self.chart_placed:
                # 2.2.14 (item 4): the image is solved already, so "click it once solved" was wrong: say why.
                band = core.band_label(self._band())   # 2.2.14 (review): B/R with a mono filter; 2.2.17: i′…
                raise ValueError(f"{', '.join(missing)} {'has' if len(missing) == 1 else 'have'} no catalog {band}: "
                                 f"{'that star is' if len(missing) == 1 else 'those stars are'} not in the downloaded "
                                 f"catalogs (or the catalog has no {band} for it). Type its {band} in the box next to "
                                 "the name, or pick another star")
            if missing:
                raise ValueError(f"{', '.join(missing)} need{'s' if len(missing) == 1 else ''} a catalog magnitude "
                                 "(click it once the image is solved, or type it)")
            comps = [self._star(getattr(self, f"{r}_name").get().strip() or f"comp{comp_number(r)}",
                                getattr(self, f"{r}_xy"), getattr(self, f"{r}_mag").get(), True) for r in comp_roles]
            dupes = {c.name for c in comps if [d.name for d in comps].count(c.name) > 1}
            if dupes:
                raise ValueError(f"Two comps have the same name ({', '.join(sorted(dupes))}). Give each its own name.")
            check = None
            if self.check_xy is not None:
                check = self._star(self.check_name.get() or "check", self.check_xy, self.check_mag.get(), False)
            radius = float(self.radius.get())
            sky_in = float(self.sky_in.get())
            sky_out = float(self.sky_out.get())
            if not (0 < radius < sky_in < sky_out):
                raise ValueError("Need aperture r < sky in < sky out")
            for star in comps + ([check] if check is not None else []):
                if math.hypot(star.x - target.x, star.y - target.y) < radius:
                    raise ValueError(f"{star.name} is marked on top of the target. Select it and click a different star.")
            marked = [(role_label(r), c) for r, c in zip(comp_roles, comps)] + ([("Check", check)] if check is not None else [])
            for i in range(len(marked)):
                for j in range(i + 1, len(marked)):
                    (la, a), (lb, b) = marked[i], marked[j]
                    if math.hypot(a.x - b.x, a.y - b.y) < radius:
                        raise ValueError(
                            f"{la} and {lb} are marked on the same star. Each one must be a different star; "
                            "the check star in particular must not be one of the comparison stars."
                        )
            sat_limit = float(self.sat_limit.get())
            lat = float(self.lat.get()) if self.lat.get().strip() else None
            lon = float(self.lon.get()) if self.lon.get().strip() else None
            if lat is None or lon is None:
                self.log("Note: no site latitude/longitude on the Input page, so no airmass: AAVSO reports show AMASS na "
                         "and the error bars leave out scintillation. Type your site once (it is remembered).")
            ra_hours = float(self.ra_hours.get()) if self.ra_hours.get().strip() else None
            dec_deg = float(self.dec_deg.get()) if self.dec_deg.get().strip() else None
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        # 2.2.14 (GitHub #4): a check star is not usual in transit work, so Transits mode does not ask; the AAVSO
        # ensemble rule is about variable-star reports.
        if len(comps) > 1 and check is None and self.mode_key == "transits":
            self.log("No check star marked: fine for a transit fit (a check star only shows whether trends are shared).")
        elif len(comps) > 1 and check is None:
            if not messagebox.askyesno(APP_TITLE, f"With {len(comps)} comps the AAVSO report is an ensemble, and AAVSO "
                                                  "requires a check star for ensemble reports. Continue without one?"):
                return

        mode = self.debayer_mode.get()
        pattern = self.pattern.get()
        masters = self.masters
        precalibrated = self.precalibrated
        measure_color = bool(self.measure_color.get())
        if measure_color and self._sloan_run():
            # 2.2.17 (item 1): R and B (B-V, V-R) cannot be measured through one Sloan filter on a mono camera.
            measure_color = False
            self.log(f"Color: not measured on a Sloan {core.band_label(self._band())} run (the B-V / V-R color index "
                     "needs B, V and R; one Sloan filter gives one band).")
        if measure_color and self._rgb_channel:
            measure_color = False   # 2.2.18 (item 1): one channel of the colour images is read per run
            self.log("Color: not measured on color images (already debayered; one channel is measured per run).")
        # 2.2.14 (item 1): each comp's catalog color from ONE catalog (not B from Gaia against V from APASS).
        comp_colors = [self._colors_for(role, star.name) for role, star in zip(comp_roles, comps)]
        cat_bv, cat_bv_src = core.ensemble_catalog_color(comp_colors, "B", "V")
        cat_vr, cat_vr_src = core.ensemble_catalog_color(comp_colors, "V", "R")
        cloud_fraction = self._cloud_fraction()
        ref_shape = self.preview.shape

        def peak_ok(cx, cy):
            """Below 80% of the raw saturation limit on the preview (a clipped core spoils a color or a comp)."""
            y0, x0 = int(round(cy)), int(round(cx))
            r0 = int(math.ceil(radius))
            patch = self.preview[max(0, y0 - r0):y0 + r0 + 1, max(0, x0 - r0):x0 + r0 + 1]
            peak = float(np.nanmax(patch)) if patch.size and np.isfinite(patch).any() else float("nan")
            return precalibrated or not math.isfinite(peak) or peak < 0.8 * sat_limit

        # Color calibration: labeled chart stars with catalog colors, measured on a sample of frames.
        cal_stars = []
        saved_cal = core.load_color_calibration() if measure_color else None
        if measure_color and self.chart_placed:
            for x, y, star in core.color_calibration_stars(self.chart_placed, self.target_xy, radius, sky_out, ref_shape):
                cx, cy, moved = core.recenter(self.preview, x, y, radius)
                if moved <= radius and peak_ok(cx, cy):
                    cal_stars.append((cx, cy, star))
        n_cal_found = len(cal_stars)
        if n_cal_found < 5:
            cal_stars = []
        # Warn before the run, not after it, when color is ticked but cannot be calibrated.
        if measure_color and not cal_stars and (saved_cal is None or cat_bv is None):
            reasons = [f"only {n_cal_found} labeled stars have catalog B and V (5 are needed for a field fit)"]
            if saved_cal is None:
                reasons.append("no color term has been saved from an earlier night")
            if cat_bv is None:
                reasons.append("the comps' catalog colors are unknown (no catalog lists B and V for them, or the image is not solved)")
            if not messagebox.askyesno(
                APP_TITLE,
                "'Also measure R and B' is ticked, but B-V cannot be calibrated on this run:\n  • "
                + "\n  • ".join(reasons)
                + "\n\nSolve the image (Solve again) and click the comps again, or choose comps with catalog B "
                  "and V.\n\nRun anyway with uncalibrated color?",
            ):
                return

        # 2.2.14 (item 11): the series' catalog magnitudes as they are BEFORE tonight's picks overwrite them, so a star
        # the series already has keeps the series' magnitude when the night is added (13-14 Oct: TYC 4006-1008-1 came
        # in with Gaia's V 8.704 over the series' 8.728). Taken when a series is loaded or a night added; here only
        # when there is none yet (review: a cancelled run must not make tonight's values "the series'").
        if getattr(self, "_series_catalog_before", None) is None:
            self._take_series_snapshot()
        # Spare comps: labeled stars near the comps' brightness, measured every frame so a comp
        # can be swapped later ("Use comps..." on the Output page) without reducing again.
        spares = []
        if self.chart_placed:
            comp_mags = [c.catalog_mag for c in comps]
            avoid = [xy for _r, xy in self._marked_roles()] + list(self.watch_xy.values())
            taken = {c.name for c in comps} | ({check.name} if check is not None else set())
            # The suggested comps (near the target in brightness and color) first, so Use comps can switch to them
            # later without reducing again (2.2.1).
            preferred = [(x, y, self._chart_star_named(name)) for name, _dm, x, y in self.suggested_comps]
            preferred = [(x, y, st) for x, y, st in preferred if st is not None and st.get("mag") is not None
                         and all(p is None or math.hypot(x - p[0], y - p[1]) >= 2 * radius for p in avoid)]
            # 2.2.2: in a thin field, widen the brightness window (out to 4 mag fainter than the comps) until at
            # least 3 spares pass, and say why the others were turned away.
            seen_ids = set()
            reasons = {}
            late = {}          # turned away after the catalog checks (kept across passes: those stars are not seen again)
            faint_edge = max(comp_mags) + 2.5
            # 2.2.14 (item 3): a spare is kept only when the image's star is where the catalog puts it (within ~2 px,
            # or the seeing): 2.2.13 kept any peak within the aperture radius, so a diamond could sit on a
            # neighbour, and clicking it named nothing (C4/C5 on HD 219134).
            seeing_now = self._seeing()
            spare_tol = min(max(2.0, seeing_now if math.isfinite(seeing_now) else 2.0), radius)
            t_inst_now = self._inst_at(self.target_xy)
            for widen in (2.5, 3.25, 4.0):
                reasons = {}
                picked = core.pick_spare_comps(self.chart_placed, avoid, ref_shape, radius, sky_out,
                                               (min(comp_mags) - 1.0, max(comp_mags) + widen), max_spares=200,
                                               reasons=reasons)
                faint_edge = max(comp_mags) + widen
                ordered = []
                for x, y, star in (preferred if widen == 2.5 else []) + picked:
                    if id(star) not in seen_ids:
                        seen_ids.add(id(star))
                        ordered.append((x, y, star))
                for x, y, star in ordered:
                    if len(spares) >= 8:
                        break
                    name = str(star.get("auid") or star.get("label") or f"spare {len(spares) + 1}")
                    if name in taken:
                        continue
                    cx, cy, moved = core.recenter(self.preview, x, y, radius)
                    if moved > spare_tol:
                        late["not found where the catalog puts it"] = late.get("not found where the catalog puts it", 0) + 1
                        continue
                    if not peak_ok(cx, cy):
                        late["near saturation"] = late.get("near saturation", 0) + 1
                        continue
                    spares.append(core.Star(name=name, x=cx, y=cy, catalog_mag=float(star["mag"])))
                    # 2.2.14 (items 1, 3, 6): the spare's whole record, so a click on its diamond adopts it (name, V,
                    # every band, the colors from one catalog, other names, AAVSO sequence label), and how much fainter
                    # than the target it is (instrumental, on this frame) when it came from a widened window.
                    s_inst = self._inst_at((cx, cy))
                    dm = s_inst - t_inst_now if math.isfinite(s_inst) and math.isfinite(t_inst_now) else None
                    self.spare_catalog[name] = {"mag": float(star["mag"]), "mags": dict(star.get("mags") or {}),
                                                "catalog": star.get("catalog", ""), "ra": star.get("ra"),
                                                "dec": star.get("dec"), "colors": core.star_colors(star),
                                                "aka": [n for n in core.star_names(star) if n != name],
                                                "seq": star.get("seq"), "mag_source": dict(star.get("mag_source") or {}),
                                                "fainter_than_target": round(dm, 2) if dm is not None else None,
                                                "widened": widen > 2.5}
                    taken.add(name)
                if len(spares) >= 3:
                    break
            if widen > 2.5:
                self.log(f"Spare comps: thin field, so the brightness window was widened to {faint_edge:.1f} mag "
                         f"({widen:g} mag fainter than the comps); {len(spares)} spare(s) found.")
                # 2.2.14 (item 6): the spares the widened window let in say how much fainter than the target they are
                # (hover on the diamond, the star panel when one is made a comp, and the log below).
            for why, n in late.items():
                reasons[why] = reasons.get(why, 0) + n
            if reasons:
                self.log("  Stars in the spare window turned away: "
                         + ", ".join(f"{n} {why}" for why, n in sorted(reasons.items(), key=lambda kv: -kv[1])) + ".")
        # Watch stars on this frame: measured every frame, like spares, but never used as comps.
        watch = []
        for name, (wx, wy) in self.watch_xy.items():
            cx, cy, moved = core.recenter(self.preview, wx, wy, radius)
            watch.append(core.Star(name=name, x=cx if moved <= radius else wx, y=cy if moved <= radius else wy))
        # 2.2.14 (item 19): the mode check when a night would go into the open series. The series file records its
        # mode; a series from before that has none, and is not checked.
        if self.observations and self.series_mode and self.series_mode != self.mode_key \
                and not self.series_mode_unknown and not getattr(self, "_series_mode_answered", False):
            name = os.path.basename(self.series_path) if self.series_path else "The open series"
            answer = messagebox.askyesnocancel(
                APP_TITLE, f"{name} is a {MODES[self.series_mode]['name']} series and this is "
                           f"{MODES[self.mode_key]['name']} mode.\n\n"
                           f"Yes: switch to {MODES[self.series_mode]['name']} mode and run there (the night can then "
                           "be added to the series).\n"
                           f"No: run in {MODES[self.mode_key]['name']} mode; the night starts a new series.\n"
                           "Cancel: don't run.")
            if answer is None:
                self._restore_series_catalog()   # 2.2.14 (item 11): tonight's spare picks are not the series'
                return
            if answer:
                self._restore_series_catalog()
                self._rerun_in_mode(self.series_mode)
                return
        self._series_mode_answered = False
        if not self._start_job(f"Photometry on {len(lights)} lights…"):
            self._restore_series_catalog()
            return
        self.photo_bring_back()   # 2.2.16 (item 16): the run's progress and results show on the page
        if watch:
            self.log(f"Watch stars measured every frame ({len(watch)}): " + ", ".join(w.name for w in watch))
        missing_watch = [w["name"] for w in self.watch_list if w["name"] not in self.watch_xy]
        if missing_watch:
            self.log("Watch stars not on this frame (Solve, or click them with Watch): " + ", ".join(missing_watch))
        if measure_color:
            if cat_bv is None and cat_vr is None:
                self.log("Color: comp catalog colors unknown (no catalog lists B and V for them). "
                         "Only instrumental color differences will be reported.")
            else:
                # 2.2.14 (item 1): which catalog each color came from (both bands from that one catalog).
                self.log(f"Color: comp catalog B-V "
                         + (f"{cat_bv:.3f} ({core.color_sources_text(cat_bv_src)})" if cat_bv is not None else "n/a")
                         + ", V-R "
                         + (f"{cat_vr:.3f} ({core.color_sources_text(cat_vr_src)})" if cat_vr is not None else "n/a")
                         + " (ensemble mean; each color from one catalog).")
            if cal_stars:
                bv_src = [core.catalog_color(st, "bv")[1] for *_, st in cal_stars]
                vr_src = [c for c in (core.catalog_color(st, "vr")[1] for *_, st in cal_stars) if c]
                self.log(f"Color calibration: {len(cal_stars)} labeled field stars with catalog B-V "
                         f"({core.color_sources_text(bv_src)}), {len(vr_src)} with V-R"
                         + (f" ({core.color_sources_text(vr_src)})" if vr_src else "")
                         + ", each color from one catalog, measured on about 40 frames.")
            elif self.chart_placed:
                self.log(f"Color calibration: only {n_cal_found} usable labeled stars with catalog B and V "
                         "(5 needed) for a field fit.")
            if not cal_stars and saved_cal:
                camera = (self.preview_header or {}).get("INSTRUME", "")
                note = ""
                if saved_cal.get("camera") and camera and saved_cal["camera"] != camera:
                    note = f"  Careful: it was made with {saved_cal['camera']}, these frames are {camera}."
                self.log(f"Color: no field fit tonight; the color term saved {saved_cal.get('date', '')[:10]} "
                         f"({saved_cal.get('star', '')}) will be used with the comps' catalog colors.{note}")
        self.spare_marks = [(sp.x, sp.y, sp.name) for sp in spares]
        if spares:
            def spare_words(sp):
                info = self.spare_catalog.get(sp.name) or {}
                if info.get("widened") and info.get("fainter_than_target") is not None:
                    return f"{sp.name} ({sp.catalog_mag:.2f}, {_fainter_text(info['fainter_than_target'])})"   # item 6
                return f"{sp.name} ({sp.catalog_mag:.2f})"
            self.log(f"Spare comps measured every frame ({len(spares)}): " + ", ".join(spare_words(sp) for sp in spares))
        elif self.chart_placed:
            self.log("Spare comps: no labeled star near the comps' brightness passed the checks.")
        else:
            self.log("Spare comps: none (Solve first, so spares can be picked from catalog stars).")
        # The stars were marked on the preview frame; every other frame is aligned to it.
        ref_sources = core.detect_sources(self.preview, max_sources=80)
        last_good = {"sources": None, "reg": None}
        # 2.2.18 (item 8): the mount (headers if sure, else the Mount choice): an alt-az field may rotate.
        mount_info = self._mount_info(lights)
        self._run_mount = mount_info["mount"]
        altaz = self._run_mount == core.MOUNT_ALTAZ
        seen = self.__dict__.setdefault("_mount_notes_seen", set())
        folder = os.path.dirname(str(lights[0])) if lights else ""
        if mount_info["note"] and (mount_info["detected"].get("sure") or self._run_mount != core.MOUNT_GEM
                                   or (folder, mount_info["note"]) not in seen):
            self.log(mount_info["note"])   # review: a mere name hint once per folder
            seen.add((folder, mount_info["note"]))
        turns = []   # 2.2.18 (item 8): each aligned frame's rotation against the reference (not flipped)
        if len(ref_sources) < 3:
            self.log("Warning: too few stars found on the reference frame to align frames. Positions will not follow drift.")
        self._prev_stars = (list(self.comps_used), self.check_used)
        self.comps_used = [comp.name for comp in comps]
        self.check_used = check.name if check is not None else ""
        self.comp_catalog_mags.update({comp.name: float(comp.catalog_mag) for comp in comps})
        if self._sloan_run():
            # 2.2.17 (item 1): which catalog each comp's (and the check's) Sloan magnitude came from, for the log and
            # the AAVSO report notes.
            label = core.band_label(self._band())
            srcs = {comp.name: self._band_source_text(role, comp.name) for role, comp in zip(comp_roles, comps)}
            if check is not None:
                srcs[check.name] = self._band_source_text("check", check.name)
            self.comp_band_sources = {"band": self._band(), "sources": srcs, "comps": [c.name for c in comps],
                                      "check": check.name if check is not None else ""}
            self.log(f"Comps in {label}: " + "; ".join(
                f"{comp.name} {label} {float(comp.catalog_mag):.3f} ({srcs.get(comp.name) or 'typed'})" for comp in comps)
                + (f"; check {check.name} {label} " + (f"{float(check.catalog_mag):.3f}" if check.catalog_mag is not None
                                                        and math.isfinite(float(check.catalog_mag)) else "n/a")
                   + f" ({srcs.get(check.name) or 'typed'})" if check is not None else ""))
        else:
            self.comp_band_sources = {}
        self.log(f"Photometry on {len(lights)} lights, {len(self.rejected)} rejected, aperture {radius:.1f} px, {mode} channel")
        rej_lights = [p for p in self.rejected if os.path.dirname(p) == os.path.dirname(lights[0])] if lights else []
        if rej_lights:   # 2.2.17 (item 6): by the frames' own numbers, as on the Blink page and in the CSV
            self.log(f"Rejected frames ({len(rej_lights)}): "
                     + core.frame_list_text(core.frame_labels_for(rej_lights)))
        # 2.2.17 (item 8): photon noise in electrons (EGAIN, binning, superpixel, flat); said once in the log.
        noise_setup = self._photon_noise_setup(lights, binning, pattern, mode, masters, precalibrated,
                                               [target] + comps, radius, sky_in, sky_out)
        electrons = noise_setup.get("electrons")
        cal_xy = np.array([(x, y) for x, y, _ in cal_stars], dtype=float) if cal_stars else None
        # 2.2.14 (item 1): each star's B-V and V-R from one catalog (catalog_color), NaN where none has both bands.
        cal_catalog = {
            key: np.array([np.nan if core.catalog_color(st, key)[0] is None else core.catalog_color(st, key)[0]
                           for *_, st in cal_stars], dtype=float)
            for key in ("bv", "vr")
        }
        # 2.2.14 (item 1): which catalogs the colors came from, for the color log and the Output panel.
        color_catalogs = {}
        for key in core.COLOR_BANDS:
            stars_src = [core.catalog_color(st, key)[1] for *_, st in cal_stars if core.catalog_color(st, key)[0] is not None]
            comp_src = cat_bv_src if key == "bv" else cat_vr_src
            color_catalogs[key] = {"field": core.color_sources_text(stars_src) if stars_src else "",
                                   "comps": core.color_sources_text(comp_src) if comp_src else ""}
        cal_step = max(1, len(lights) // 40)
        cancel = self._cancel
        self._progress_start(len(lights), "Photometry")

        def work_inner():
            night_points = []
            cal_samples = {"bv": [], "vr": []}   # (obs, relative colors, comps' relative color)
            cancelled = False
            for i, path in enumerate(lights, 1):
                if cancel.is_set():
                    cancelled = True
                    break
                try:
                    raw, header = core.read_fits(path)
                    cal = core.calibrate_frame(core.bin_image(raw, binning), header, masters)
                    data = core.debayer(cal, pattern, mode)
                    header = dict(header)
                    header["_PATH"] = path
                    reg = None
                    if len(ref_sources) >= 3 and data.shape == ref_shape:
                        frame_sources = core.detect_sources(data, max_sources=80)
                        if altaz:   # 2.2.18 (item 8): field rotation, never a meridian flip
                            reg = core.register_rotating(ref_sources, frame_sources, data.shape,
                                                         last_good["sources"], last_good["reg"])
                        else:
                            reg = core.register_frame(ref_sources, frame_sources, data.shape)
                        if reg is None and last_good["sources"] is not None and not altaz:
                            # Fall back to the previous aligned frame and chain the two alignments.
                            step = core.register_frame(last_good["sources"], frame_sources, data.shape)
                            if step is not None:
                                reg = core.compose_registration(last_good["reg"], step, data.shape)
                        if reg is None:
                            raise ValueError(
                                f"could not align this frame ({len(frame_sources)} stars found; clouds? trailing?)"
                            )
                        last_good["sources"] = frame_sources
                        last_good["reg"] = reg
                        if not reg.get("flipped"):
                            turns.append(float(reg.get("angle", 0.0)))
                    f_target = core.move_star(target, reg, data.shape)
                    f_comps = [core.move_star(c, reg, data.shape) for c in comps]
                    f_check = core.move_star(check, reg, data.shape) if check is not None else None
                    obs = core.reduce_frame(
                        data, header, f_target, f_comps, f_check, radius, sky_in, sky_out,
                        lat, lon, ra_hours, dec_deg, float("inf"), aligned=reg is not None,
                        electrons=electrons if (np.ndim(electrons) == 0 or np.shape(electrons) == data.shape) else
                        float(np.nanmedian(electrons)),   # 2.2.17 (item 8)
                    )
                    # Saturation is judged on the raw frame, where binning cannot hide a clipped core.
                    total_bin = binning * (1 if pattern == core.MONO else 2)
                    obs.raw_peak = core.raw_peak_near(raw, f_target, radius, total_bin)
                    comp_raw = max(core.raw_peak_near(raw, c, radius, total_bin) for c in f_comps)
                    flags = [f for f in obs.flag.split() if f not in ("sat", "compsat")]
                    if precalibrated:
                        comp_raw = float("nan")
                    if not precalibrated and math.isfinite(obs.raw_peak) and obs.raw_peak >= sat_limit:
                        flags.insert(0, "sat")
                        obs.saturated = True
                    if precalibrated:
                        # 2.2.3: no fixed saturation level in pre-calibrated values; look for a flat-topped core.
                        is_cfa = pattern != core.MONO
                        if core.flat_topped_core(raw, f_target, radius, total_bin, cfa=is_cfa):
                            flags.insert(0, "sat")
                            obs.saturated = True
                        if any(core.flat_topped_core(raw, c, radius, total_bin, cfa=is_cfa) for c in f_comps):
                            flags.insert(0, "compsat")
                            obs.saturated = True
                    if math.isfinite(comp_raw) and comp_raw >= sat_limit:
                        flags.insert(0, "compsat")
                        obs.saturated = True
                    obs.flag = " ".join(flags)
                    if watch:
                        f_watch = [core.move_star(wst, reg, data.shape) for wst in watch]
                        wvals = core.channel_inst_mags(data, f_watch, radius, sky_in, sky_out, core.header_exptime(header))
                        obs.watch_insts = {wst.name: float(v) for wst, v in zip(watch, wvals) if math.isfinite(v)}
                        obs.pos["w"] = {wst.name: [round(float(fw.x), 1), round(float(fw.y), 1)]
                                        for wst, fw in zip(watch, f_watch)}
                    if spares:
                        f_spares = [core.move_star(sp, reg, data.shape) for sp in spares]
                        spare_vals = core.channel_inst_mags(data, f_spares, radius, sky_in, sky_out,
                                                            core.header_exptime(header))
                        obs.spare_insts = {sp.name: float(v) for sp, v in zip(spares, spare_vals) if math.isfinite(v)}
                        obs.pos["s"] = {sp.name: [round(float(fs.x), 1), round(float(fs.y), 1)]
                                        for sp, fs in zip(spares, f_spares)}
                    if measure_color:
                        exp = core.header_exptime(header)
                        stars_tc = [f_target] + f_comps
                        chan = {}
                        chan_imgs = {}
                        for ch in ("green", "red", "blue"):
                            img = data if ch == mode else core.debayer(cal, pattern, ch)
                            chan_imgs[ch] = img
                            chan[ch] = core.channel_inst_mags(img, stars_tc, radius, sky_in, sky_out, exp)
                        if cal_xy is not None and (i - 1) % cal_step == 0:
                            try:
                                pos = core.move_points(cal_xy, reg, data.shape)
                                fm = {}
                                for ch, img in chan_imgs.items():
                                    flux, _ = core.measure_many(img, pos, radius, sky_in, sky_out)
                                    with np.errstate(divide="ignore", invalid="ignore"):
                                        fm[ch] = np.where(flux > 0, -2.5 * np.log10(flux / exp), np.nan)
                                for key, _b1, _b2, c1, c2 in core.COLOR_INDICES:
                                    rel, ref = core.relative_colors(fm[c1], fm[c2])
                                    comp_col = chan[c1][1:] - chan[c2][1:]
                                    comp_col = comp_col[np.isfinite(comp_col)]
                                    cal_samples[key].append((obs, rel, float(comp_col.mean() - ref)
                                                             if comp_col.size and math.isfinite(ref) else float("nan")))
                            except Exception as exc:
                                self.call_ui(self.log, f"  color calibration stars skipped on this frame: {exc}")
                        obs.bv_diff = core.differential_color(chan["blue"], chan["green"])
                        obs.vr_diff = core.differential_color(chan["green"], chan["red"])
                        if cat_bv is not None and math.isfinite(obs.bv_diff):
                            obs.bv = cat_bv + obs.bv_diff
                        if cat_vr is not None and math.isfinite(obs.vr_diff):
                            obs.vr = cat_vr + obs.vr_diff
                    if reg is not None and reg.get("flipped"):
                        obs.segment = "flip"
                    if reg is not None:
                        shift_text = (
                            f"  shift ({reg['dx']:+.0f}, {reg['dy']:+.0f}) rot {reg.get('angle', 0.0):+.2f}°"
                            f"{' flipped' if reg['flipped'] else ''}{' chained' if reg.get('chained') else ''}"
                        )
                    else:
                        shift_text = ""
                    night_points.append(obs)
                    self.call_ui(
                        self.log,
                        f"  {i}/{len(lights)}  JD {obs.jd:.5f}  mag {obs.mag:.3f} ± {obs.merr:.3f}  "
                        f"FWHM {obs.fwhm:.1f}  "
                        + (f"peak {obs.raw_peak:.0f} (pre-calibrated file; not camera counts)" if precalibrated
                           else f"raw peak {obs.raw_peak:.0f}")
                        + f"{shift_text}  {obs.flag}",
                    )
                except Exception as exc:
                    self.call_ui(self.log, f"  skipped {os.path.basename(path)}: {exc}")
                self.call_ui(self._progress, i)
            if cancelled:
                self.call_ui(self._photometry_done, [], mode, None, True)
                return
            if turns:
                # 2.2.18 (item 8): how far the field turned on the sensor through the night.
                unwrapped = np.degrees(np.unwrap(np.radians(turns)))   # review: a turn across ±180° is not 360°
                spread = float(unwrapped.max() - unwrapped.min())
                if altaz and spread > 1.0:
                    self.call_ui(self.log, f"  Field rotation: the field turned by {spread:.1f}° on the sensor "
                                           "through the night (alt-az mount); the apertures followed it.")
                elif not altaz and spread > 5.0:
                    self.call_ui(self.log, f"  Note: the field turned by {spread:.1f}° on the sensor through the "
                                           "night. If this is an alt-az mount without a derotator, set Mount to "
                                           "Alt-az in the Optics box (Input page) so the alignment follows the "
                                           "rotation.")
            color_info = None
            if measure_color and night_points:
                # 2.2.16 (item 3): the color step can never stop the run; on any error the night is kept without
                # the color term (the frames' raw colors stay as measured) and _photometry_done always runs.
                try:
                    # Use only frames that pass QC (sat, drift, and tonight's cloud flag) for the color fit.
                    core.apply_cloud_flags(night_points, cloud_fraction)
                    fits, offsets = {}, {}
                    n_frames = 0
                    for key, *_ in core.COLOR_INDICES:
                        samples = cal_samples[key]
                        good = [smp for smp in samples if not smp[0].flag]
                        if len(good) >= 8:
                            samples = good
                        if samples:
                            n_frames = max(n_frames, len(samples))
                            try:
                                inst, inst_err = core.robust_mean_err(np.vstack([smp[1] for smp in samples]), axis=0)
                                fits[key] = core.fit_color_term(inst, cal_catalog[key], inst_err=inst_err)
                            except Exception as exc:
                                fits[key] = core.color_fit_failure(exc)
                            comp_off = np.array([smp[2] for smp in samples], dtype=float)
                            comp_off = comp_off[np.isfinite(comp_off)]
                            offsets[key] = float(np.median(comp_off)) if comp_off.size else None
                    used = core.apply_color_calibration(night_points, fits, offsets, saved_cal,
                                                        {"bv": cat_bv, "vr": cat_vr})
                    color_info = {"fits": fits, "used": used, "frames": n_frames, "saved": saved_cal,
                                  "catalogs": color_catalogs}   # 2.2.14 (item 1)
                except Exception as exc:
                    self.call_ui(self.log, f"Color calibration: color fit failed: {type(exc).__name__}: {exc} — "
                                           "continuing without the color term.")
                    color_info = None
            self.call_ui(self._photometry_done, night_points, mode, color_info)

        def work():
            # 2.2.16 (item 3): whatever goes wrong in the worker, the job ends (buttons back, cursor back).
            try:
                work_inner()
            except Exception as exc:
                traceback.print_exc()
                self.call_ui(self._photometry_done, [], mode, None, True, f"{type(exc).__name__}: {exc}")

        self._blink_worker = threading.Thread(target=work, daemon=True)
        self._blink_worker.start()

    def _mark_rotator_excursion(self, night_points):
        """2.2.7: a flip the alignment cannot see (the camera turned back to its first angle after the flip) still
        splits the night: frames after a rotator excursion become the "flip" segment, so each star's light curve is
        lined up across it like any flip. Rejected frames count, since the excursion is often only in them."""
        if not night_points or any(o.segment == "flip" for o in night_points):
            return
        if getattr(self, "_run_mount", None) == core.MOUNT_ALTAZ:
            return   # 2.2.18 (item 8): an alt-az mount has no meridian flip; a drifting derotator is not one either
        try:
            paths, _skipped, _note = core.split_lights(core.list_fits(self.light_dir.get()))
        except Exception:
            return
        used = {os.path.normcase(os.path.abspath(o.path)) for o in night_points}
        if not paths or not used:
            return
        angles = []
        for p in paths:
            try:
                angles.append(core.header_rotator(core.read_header(p)))
            except Exception:
                angles.append(None)
        start = core.rotator_excursion_start(angles)
        if start is None:
            return
        after = {os.path.normcase(os.path.abspath(p)) for p in paths[start:]}
        before_used = any(os.path.normcase(os.path.abspath(p)) in used for p in paths[:start])
        moved = [o for o in night_points if os.path.normcase(os.path.abspath(o.path)) in after]
        if not before_used or not moved:
            return
        for o in moved:
            o.segment = "flip"
        self.log(f"Flip found from the rotator readings (it left its angle and came back, so the images look "
                 f"unflipped): {len(moved)} frames from {os.path.basename(paths[start])} on are treated as after "
                 "the flip.")

    def _photometry_done(self, night_points, mode, color_info=None, cancelled=False, error=None):
        self._end_job()
        if cancelled:
            if error:   # 2.2.16 (item 3): the worker hit an error outside the per-frame guard
                self._progress_end("Stopped by an error; nothing was added.")
                self.log(f"Photometry stopped by an error ({error}). Nothing was added to the series.")
                self._restore_series_catalog(stars=True)
                return
            self._progress_end("Cancelled; nothing was added.")
            self.log("Photometry cancelled. Nothing was added to the series.")
            self._restore_series_catalog(stars=True)   # 2.2.14 (item 11, review)
            return
        self._progress_end(f"{len(night_points)} points.")
        if not night_points:
            self._restore_series_catalog(stars=True)
            messagebox.showerror(APP_TITLE, "No frames produced a magnitude. The Calibrate page log says why each frame was skipped.")
            return
        night = f"JD{math.floor(night_points[0].jd)}"
        # 2.2.7: new photometry is what the Transit fit page should show: drop an EXOTIC report left loaded there.
        if getattr(self, "transit_page", None) is not None and self.transit_page.report is not None:
            self.transit_page.close_report(quiet=True)
            self.log("Transit fit: the EXOTIC report that was loaded was closed; the page now uses this photometry.")
        filt = self._filter_code()
        for obs in night_points:
            obs.night = night
            obs.filt = filt
        self._mark_rotator_excursion(night_points)
        if color_info:
            try:   # 2.2.16 (item 3): a bad color record must not lose the night
                self._record_color_calibration(night, color_info)
            except Exception as exc:
                self.log(f"  color fit failed: {type(exc).__name__}: {exc} — continuing without the color term.")
        append = False
        plan = None
        same_mode = not self.series_mode or self.series_mode == self.mode_key or self.series_mode_unknown   # 2.2.14
        if self.observations and same_mode:
            filters = {obs.filt for obs in self.observations}
            warning = ""
            if filters != {filt}:
                warning = f"\n\nCareful: the series is {', '.join(sorted(filters))} and this night is {filt}."
            prev_comps, prev_check = getattr(self, "_prev_stars", ([], ""))
            # 2.2.8: the same star under another catalog's name (Gaia DR3 tonight, TYC in the series) is the same
            # star. It is compared as the series' star; its name and magnitude change only if the night is added.
            plan = self._same_star_plan(prev_comps, prev_check)
            cmp_comps = plan["comps"] if plan else list(self.comps_used)
            cmp_check = plan["check"] if plan else self.check_used
            if prev_comps and (prev_comps != cmp_comps or prev_check != cmp_check):
                warning += (
                    "\n\nCareful: this night used different stars than the series.\n"
                    f"  Series: comps {', '.join(prev_comps)}; check {prev_check or 'none'}\n"
                    f"  This night: comps {', '.join(cmp_comps)}; check {cmp_check or 'none'}\n"
                    "Different comps put each night on its own zero point."
                )
            if plan:
                # 2.2.14 (item 11): names AND catalog magnitudes, watch stars and spares too.
                warning += ("\n\nOne star, one name: " + "; ".join(self._same_star_words(plan))
                            + (". Yes uses the series' names for this night." if plan.get("band_mismatch") else
                               ". Yes uses the series' names and catalog magnitudes for this night."))
                if plan.get("remag") or plan.get("check_mag"):
                    # review S4: say that the series' magnitude wins, and where to change it for every night.
                    warning += (" The series' catalog magnitude wins, so every night stays on one zero point; Use "
                                "comps… (Output page) can re-derive the whole series with other comps afterwards.")
            # 2.2.3: compare this night's reduction settings with every other night's in the series.
            others = {n: info for n, info in self.night_bin.items() if n != night}
            mine = dict(self._night_setup(), comps=cmp_comps, check=cmp_check or "")
            if "different stars than the series" in warning:
                mine = {k: v for k, v in mine.items() if k not in ("comps", "check")}   # already said above
            other_kit = [d for d in core.night_setup_differences(
                {k: mine.get(k) for k in ("instrument", "observer")}, others)]
            diffs = core.night_setup_differences({k: v for k, v in mine.items() if k not in ("instrument", "observer")},
                                                 others)
            if others and bool(mine.get("borrowed")) != any(isinstance(i, dict) and i.get("borrowed")
                                                            for i in others.values()):
                other_kit.append("site and optics: " + ("another telescope's (from its frames) on this night, your "
                                                        "own on the series' nights" if mine.get("borrowed") else
                                                        "your own on this night, another telescope's on the "
                                                        "series' nights"))
            if other_kit:
                # 2.2.9: Tessa's eVscope night sat 0.33 mag from John's: untransformed instruments do not mix.
                warning += ("\n\nCareful: this night comes from other equipment or another observer:\n  • "
                            + "\n  • ".join(self.label_nights(d) for d in other_kit)
                            + "\nUntransformed nights from different instruments sit on their own zero points (tenths "
                              "of a magnitude). A separate series is usually better: answer No.")
            if diffs:
                warning += ("\n\nCareful: this night was reduced differently from the series:\n  • "
                            + "\n  • ".join(self.label_nights(d) for d in diffs) + "\nNights reduced differently can sit on different zero points "
                            "(a step between nights that is not the star).")
            already = any(o.night == night for o in self.observations)
            if already:
                warning += (f"\n\nThe night of {self.night_label(night, short=True, jd=night_points[0].jd)} ({night}) is already in the series. Yes replaces that night's points with this run "
                            "(the other nights stay).")
            saved_note = (f"\n\nNo also leaves {os.path.basename(self.series_path)} on disk untouched; use Save series "
                          "to keep the new one under a new name.") if self.series_path else ""
            append = messagebox.askyesno(
                APP_TITLE,
                f"Add these {len(night_points)} points as the night of "
                f"{self.night_label(night, short=True, jd=night_points[0].jd)} ({night}) to the {len(self.observations)} points already in the series?\n\n"
                f"Yes keeps every earlier night. No starts a new series with this night only.{saved_note}{warning}",
            )
        replaced = False
        self.highlight_night = None   # 2.2.15 review: a night added or replaced shows the whole series, unzoomed
        if append and plan:
            self._apply_same_star_plan(plan, night_points)
        if append:
            if any(o.night == night for o in self.observations):
                self.observations = [o for o in self.observations if o.night != night]
                self.log(f"Replaced the earlier points of {night} with this run.")
            self.observations = core.merge_nights(self.observations, night_points)
        else:
            replaced = bool(self.observations)
            old_path = self.series_path
            self.observations = night_points
            # 2.2.15 review: one clean-up for every way a series is set aside (tonight's color calibration stays).
            self._clear_series_state(keep_night=night)
            if replaced and old_path:
                # Never overwrite the file of the series that was just set aside.
                self.log(f"New series started; {os.path.basename(old_path)} was left unchanged on disk.")
        # Keep a comp selection made with "Use comps..." for the nights added after it.
        if self.active_comps and set(self.active_comps) != set(self.comps_used):
            try:
                result = core.recompute_with_comps(night_points, self.active_comps, self.comps_used, self._comp_catalog(),
                                                   self._star_aliases())
                self.log(f"Applied the chosen comps ({', '.join(self.active_comps)}) to {night}"
                         + (f"; {result['missing']} frame(s) lack one of them and are flagged nocomp." if result["missing"] else "."))
            except ValueError as exc:
                self.log(f"Could not apply the chosen comps to {night}: {exc}")
        self.legacy_mixed.discard(night)
        self.series_mode = self.mode_key
        self.series_mode_unknown = False   # 2.2.14 (item 19): the file records the mode from now on
        self._series_check = (self.check_used, self.check_mag.get())   # 2.2.8
        self._take_series_snapshot()   # 2.2.14 (item 11): the series (added to or new) as it now is
        self.night_bin[night] = self._night_setup()
        # 2.2.18 (item 8): the night's mount goes with it; an alt-az night has no pier sides ("—").
        night_mount = getattr(self, "_run_mount", None) or core.MOUNT_GEM
        if night_mount == core.MOUNT_ALTAZ:
            self.night_bin[night]["mount"] = night_mount   # GEM nights stay as saved before
        self.night_bin[night]["pier"] = self._night_pier(night_points, night_mount)   # 2.2.14 (item 20)
        if (self.comp_band_sources or {}).get("band") == filt:
            # 2.2.17 (round 2): the night's Sloan comp/check sources go with the series (report notes after Load).
            self.comp_band_sources["night"] = night
            self.night_bin[night]["band_sources"] = {k: v for k, v in self.comp_band_sources.items() if k != "night"}
        self._refresh_period()
        flagged = sum(1 for obs in night_points if obs.flag)
        clouds = sum(1 for obs in night_points if "cloud" in obs.flag.split())
        edges = sum(1 for obs in night_points if "cloudedge" in obs.flag.split())   # 2.2.14 (item 10)
        hazy = sum(1 for obs in night_points if core.HAZE_FLAG in obs.flag.split())   # 2.2.14 (item 18)
        self.log(f"Photometry done: {len(night_points)} points, {flagged} flagged ({clouds} cloud"
                 + (f", {edges} at a cloud edge" if edges else "")
                 + (f", {hazy} {core.HAZE_LABEL}" if hazy else "") + ").")
        if edges:
            self.log("  'cloudedge': a point within two frames of a cloud or haze run and far off the night's level "
                     "(over 5× the night's scatter, at least 0.05 mag); left out like the cloud frames.")
        if hazy:
            self._offer_keep_transmission(night)
            self._refresh_period()
        self._log_zero_point_check(night)   # 2.2.14 (item 10)
        self._noise_note(night_points)
        self._warn_edges(night, night_points)
        self.draw_output()
        self.show_step(STEP_OUTPUT)
        nights = sorted({obs.night for obs in self.observations})
        self.status.set(f"{len(self.observations)} points across {len(nights)} night(s). Save the series to add more later.")
        if self.series_path:
            try:
                self._write_series(self.series_path)
                self.status.set(self.status.get() + f"  Auto-saved to {os.path.basename(self.series_path)}.")
            except OSError as exc:
                messagebox.showwarning(APP_TITLE, f"Could not auto-save the series: {exc}")
        self._check_planet_transits(night, source="run")   # 2.2.15 (item 1): in the background; flags arrive later

    # ---- 2.2.15 (item 1): planet transits in a Variables campaign -------------------------------------------
    PLANET_RETRY_S = 600.0   # a failed lookup (offline, unknown star) is tried again after this long

    def _check_planet_transits(self, nights, user: bool = False, source: str = "") -> None:
        """2.2.15 (item 1): John's campaign rule: no campaign data while a planet of the target transits. In
        Variables mode, look up the Star ID's transiting planets (NASA Exoplanet Archive) in a background thread
        (once per star and session), then count each night's frames in each predicted transit window. nights: one
        night (after photometry) or several (a loaded series; Planet transits…), answered with one question. Nothing
        is said when there is no Star ID, no lookup, no planet, no transit in the frames, or no network (user=True,
        from the button: the status line says so). Needs only the frames' times and the Star ID (no plate solution,
        no header pointing); RA/Dec, when known, put the frames on BJD_TDB like the ephemeris.
        2.2.16 (item 9): source says where the frames come from ("run": tonight's run, "loaded": a series just loaded,
        "series": the open series); the question names it, and is dropped when the series, mode or Star ID changed
        while the lookup ran."""
        if isinstance(nights, str):
            nights = [nights]
        if source not in ("run", "loaded", "series"):
            source = "run" if len(nights) == 1 and not self.series_path else "series"
        if self.mode_key != "variables":
            return
        star = self.star_id.get().strip()
        if not star:
            return
        points = {n: [o for o in self.observations if o.night == n] for n in nights}
        points = {n: p for n, p in points.items() if p}
        if not points:
            return
        key = name_key(star)
        ctx = self._planet_check_context(source, star)   # 2.2.16 (item 9)
        cached = self._planet_eph_cache.get(key)
        if cached is not None and (cached[1] is not None or time.time() - cached[0] < self.PLANET_RETRY_S):
            self.call_ui(self._planet_transits_found, points, cached[1], user, ctx)
            return
        if not user:   # (Planet transits… has said so already)
            self._planet_check_status(ctx)

        def work():
            try:
                planets = tcore.transiting_planet_ephemerides(star)
            except Exception:
                planets = None   # offline or an unexpected reply: silent
            self.call_ui(self._campaign_planets_done, key, points, planets, user, ctx)

        threading.Thread(target=work, daemon=True).start()

    def _target_planets(self) -> list:
        """2.2.18 (item 20): the Star ID's transiting planets from the per-session cache (the same NASA Exoplanet
        Archive lookup as the planet-transit check), [] when not known yet."""
        star = self.star_id.get().strip()
        if not star:
            return []
        key = name_key(star)
        cached = self._planet_eph_cache.get(key)
        if cached is not None and (cached[1] is not None or time.time() - cached[0] < self.PLANET_RETRY_S):
            return [dict(p, name=p.get("name") or star, host=star) for p in (cached[1] or [])]
        return []

    def _prefetch_target_planets(self, star: str) -> None:
        """2.2.18 (item 20): look the Star ID's transiting planets up in the background, at most once per star and
        session (called after a successful Star ID lookup)."""
        key = name_key(star)
        busy = getattr(self, "_planet_prefetch", None)
        if busy is None:
            busy = self._planet_prefetch = set()
        if not key or key in busy:
            return
        cached = self._planet_eph_cache.get(key)
        if cached is not None and (cached[1] is not None or time.time() - cached[0] < self.PLANET_RETRY_S):
            return
        busy.add(key)

        def work():
            try:
                planets = tcore.transiting_planet_ephemerides(star)
            except Exception:
                planets = None   # offline or an unexpected reply: silent
            self.call_ui(self._prefetch_done, key, planets)

        threading.Thread(target=work, daemon=True).start()

    def _prefetch_done(self, key: str, planets) -> None:
        if key not in self._planet_eph_cache or self._planet_eph_cache[key][1] is None:
            self._planet_eph_cache[key] = (time.time(), planets)

    def _campaign_planets_done(self, key: str, points: dict, planets, user: bool = False, ctx=None) -> None:
        self._planet_eph_cache[key] = (time.time(), planets)
        self._planet_transits_found(points, planets, user, ctx)

    # 2.2.16 (item 9): where the checked frames come from, so the question cannot pass for the lights just picked.
    def _planet_check_context(self, source: str, star: str) -> dict:
        lights = self.light_dir.get().strip()
        series = os.path.basename(self.series_path) if self.series_path else ""
        if source == "run":
            where = f"Tonight's run ({star}" + (f", {self._folder_text(lights)}" if lights else "") + ")"
            status = f"Checking {star}'s planets for tonight's run…"
        else:
            name = f"Series {series}" if series else "The open series (not saved yet)"
            where = name + (" (just loaded)" if source == "loaded" else "")
            status = f"Checking {star}'s planets for the {'loaded ' if source == 'loaded' else ''}series…"
        return {"source": source, "star": name_key(star), "star_text": star, "mode": self.mode_key,
                "series": self.series_path, "gen": getattr(self, "_series_gen", 0), "lights": lights,
                "where": where, "status": status, "status_before": self.status.get()}

    @staticmethod
    def _folder_text(folder: str) -> str:
        """A lights folder in a few words: its name, with the folder above when the name alone says little
        ('TOI-2046/Lights')."""
        norm = os.path.normpath(folder)
        base = os.path.basename(norm)
        parent = os.path.basename(os.path.dirname(norm))
        if parent and base.lower().rstrip("0123456789_- ") in ("light", "lights", "lightframes", "light frames", ""):
            return f"{parent}/{base}"
        return base or folder

    def _planet_check_status(self, ctx: dict) -> None:
        if ctx:
            self.status.set(ctx["status"])

    def _planet_check_stale(self, ctx) -> str:
        """Why the context of a planet check changed while the lookup ran ("" when it did not)."""
        if not ctx:
            return ""
        if getattr(self, "_series_gen", 0) != ctx["gen"]:
            return "the series was closed or replaced"
        if self.mode_key != ctx["mode"]:
            return "the mode was switched"
        if name_key(self.star_id.get().strip()) != ctx["star"]:
            return "the Star ID changed"
        return ""

    def _frame_bjd_offsets(self, pts):
        """2.2.15 (item 1, review): BJD_TDB minus JD (days) per frame when the target's RA/Dec are known (the site
        if known, else the geocentre: a few milliseconds), else None (the windows are then padded 15 minutes)."""
        try:
            ra = float(self.ra_hours.get()) * 15.0
            dec = float(self.dec_deg.get())
        except (TypeError, ValueError):
            return None
        try:
            lat, lon = float(self.lat.get()), float(self.lon.get())
        except (TypeError, ValueError):
            lat, lon = 0.0, 0.0
        try:
            jd = np.array([o.jd for o in pts], dtype=float)
            off = np.asarray(core.convert_times(jd, "BJD_TDB", ra, dec, lat, lon), dtype=float) - jd
        except Exception:
            return None
        if off.shape != jd.shape or not np.isfinite(off).all() or np.abs(off).max() > 0.01:
            return None
        return off

    @staticmethod
    def _local_hm(jd) -> str:
        try:
            return f"{core.jd_to_datetime_utc(float(jd) + 0.5 / 1440).astimezone():%H:%M}"   # to the nearest minute
        except (OverflowError, ValueError, OSError, TypeError):
            return ""

    def _planet_hits_text(self, hit: dict) -> str:
        """'9 frames fall in planet b's transit (frames 05:02–05:25 local; transit 05:17–06:14 ±1 min, plus 15 min
        for JD vs BJD)', with what was assumed. Every time is in the frames' own system (JD UTC, shown local)."""
        n = int(hit["frames"])
        parts = []
        f0, f1 = (hit.get("jd") or [None, None])[:2]
        a, b = self._local_hm(f0), self._local_hm(f1)
        if a and b:
            parts.append(f"frames {a}–{b} local")
        t1, t4 = self._local_hm(hit.get("t1")), self._local_hm(hit.get("t4"))
        if t1 and t4:
            unc = float(hit.get("unc") or 0.0) * 1440
            tr = f"transit {t1}–{t4}" + (f" ±{unc:.0f} min" if unc >= 0.5 else "")
            pad = float(hit.get("pad") or 0.0)
            if not hit.get("bjd", False) and pad > 0.002:
                tr += f", plus {pad * 1440:.0f} min for JD vs BJD"
            parts.append(tr)
        notes = []
        if hit.get("t14_assumed"):
            notes.append(f"T14 not published; {tcore.T14_ASSUMED * 24:.1f} h assumed")
        if hit.get("unc_known") is False:
            notes.append("ephemeris uncertainty not published")
        inner = "; ".join(parts + notes)
        return (f"{n} frame{'s' if n != 1 else ''} fall{'s' if n == 1 else ''} in planet {hit['letter']}'s transit"
                + (f" ({inner})" if inner else ""))

    def _planet_transits_found(self, points: dict, planets, user: bool = False, ctx=None) -> None:
        """2.2.15 (item 1): the lookup is back (on the UI thread). A night may have been replaced, the series closed
        or saved meanwhile: only the nights' points still in the series are looked at, and an open series file is
        written again when flags change. Nights not asked before get one question together."""
        # 2.2.16 (item 9): the "Checking …" status goes back to what it said; a changed context drops the question.
        if ctx and self.status.get() == ctx["status"]:
            self.status.set(ctx["status_before"])
        stale = self._planet_check_stale(ctx)
        if stale:
            self.log(f"Planet-transit check for {ctx['where']} dropped: {stale} while {ctx['star_text']}'s planets "
                     "were looked up. (Planet transits… on the Output page checks the open series.)")
            if user:   # review: the button's "looking up…" line must not stay
                self.status.set(f"Planet transits: check dropped ({stale}). Press Planet transits… again to check "
                                "the open series.")
            return
        if not planets or self.series_mode != "variables":
            if user:
                self.status.set("Planet transits: no transiting planet with an ephemeris was found for the Star ID "
                                "(or the NASA Exoplanet Archive could not be reached).")
            return
        changed, asks, found = False, [], False
        for night, pts in points.items():
            current = [o for o in self.observations if o.night == night]
            ids = {id(o) for o in current}
            if not current or not any(id(o) in ids for o in pts):
                continue   # the night was removed or replaced, or the series closed, before the lookup returned
            offsets = self._frame_bjd_offsets(current)
            try:
                hits = tcore.planet_transit_frames(current, planets, offsets=offsets,
                                                   pad=tcore.BJD_PAD if offsets is not None else tcore.JD_BJD_PAD)
            except Exception:
                continue
            # A night without hits is not touched (a loaded series stays as saved); one with hits gets its record.
            info = self._night_info(night, create=bool(hits))
            if not hits:
                old = info.pop("planet_transits", None)
                info.pop("transit_flagged", None)
                if old:   # an earlier check found some (another ephemeris): put those frames back
                    windows = [w for h in old for w in h.get("windows", [])]
                    before = [o.flag for o in current]
                    core.set_transit_flags(current, windows, False)
                    changed = changed or before != [o.flag for o in current]
                continue
            found = True
            info["planet_transits"] = [
                {k: h.get(k) for k in ("planet", "letter", "frames", "jd", "windows", "t1", "t4", "unc", "pad", "bjd",
                                       "t14_assumed", "unc_known")} for h in hits]
            label = self.label_nights(night)
            for h in hits:
                self.log(f"{label}: {self._planet_hits_text(h)} (campaign rule: no data while a planet transits).")
            if "transit_flagged" in info:   # asked already for this night: keep that answer
                changed = self._set_transit_flags(night, bool(info["transit_flagged"]), quiet=True) or changed
            else:
                asks.append(night)
        if asks:
            # 2.2.16 (item 9): say where the frames come from (15 Oct: a loaded series' question arrived while new
            # lights were being picked and looked like theirs).
            where = (ctx or {}).get("where") or (f"Series {os.path.basename(self.series_path)}" if self.series_path
                                                 else "The open series")
            lights_now = self.light_dir.get().strip()
            if ctx and lights_now and lights_now != ctx["lights"]:
                where += (f" — not the lights now picked ({self._folder_text(lights_now)}), which "
                          "have not been run yet")
            if len(asks) == 1:
                what = "; ".join(self._planet_hits_text(h) for h in self._night_info(asks[0])["planet_transits"])
                head = f"{where}:\n{self.night_label(asks[0], short=True)}: {what}."
            else:
                head = (f"{where}:\n{len(asks)} nights have frames in a predicted planet transit:\n  • "
                        + "\n  • ".join(f"{self.night_label(n, short=True)}: "
                                        + "; ".join(self._planet_hits_text(h)
                                                    for h in self._night_info(n)["planet_transits"])
                                        for n in asks))
            flag = messagebox.askyesno(
                APP_TITLE,
                f"{head}\n\nLeave those frames out (flagged \"transit\", like cloud)?\n\nYes (the default) follows the "
                "campaign rule: no data while a planet transits" + (" (every night listed)" if len(asks) > 1 else "")
                + ". The frames stay in the series and Planet transits… on the Output page puts them back; they never "
                "go in a report.\nNo keeps them.", default="yes")
            for n in asks:
                self._set_transit_flags(n, flag)
            changed = True
        if changed:
            self._after_transit_flags()
        elif user and not found:
            self.status.set("Planet transits: no frame of the series falls in a predicted transit.")

    def _set_transit_flags(self, night: str, flag: bool, quiet: bool = False) -> bool:
        """Flag or unflag a night's frames in its stored transit windows. True when a flag changed."""
        info = self._night_info(night, create=True)
        hits = info.get("planet_transits") or []
        windows = [w for h in hits for w in h.get("windows", [])]
        pts = [o for o in self.observations if o.night == night]
        before = [o.flag for o in pts]
        n = core.set_transit_flags(pts, windows, flag)
        info["transit_flagged"] = bool(flag)
        if not quiet:
            self.log(f"{self.label_nights(night)}: frames in a planet transit "
                     + (f"left out (flagged transit, {n})." if flag else "kept (not flagged)."))
        return before != [o.flag for o in pts]

    def _after_transit_flags(self) -> None:
        self._refresh_period()
        self.draw_output()
        if self.series_path:
            try:
                self._write_series(self.series_path)
                self.status.set(f"Planet transit flags saved to {os.path.basename(self.series_path)}.")
            except OSError as exc:
                self.log(f"Could not save the series with the planet transit flags: {exc}")

    def edit_planet_transits(self):
        """2.2.15 (item 1): leave out or keep a night's frames that fell in a planet transit (the highlighted night,
        or the only night that has some). With no night checked yet, check the series now."""
        all_nights = sorted({o.night for o in self.observations})
        nights = [n for n in all_nights if self._night_info(n).get("planet_transits")]
        if not nights:
            if all_nights and self.mode_key == "variables" and self.star_id.get().strip():
                self.status.set("Planet transits: looking up the Star ID's planets and checking every night…")
                self._check_planet_transits(all_nights, user=True, source="series")
                return
            messagebox.showinfo(APP_TITLE, "No night has frames in a predicted planet transit. (The check runs in "
                                           "Variables mode when the Star ID has transiting planets in the NASA "
                                           "Exoplanet Archive.)")
            return
        night = self.highlight_night if self.highlight_night in nights else (nights[0] if len(nights) == 1 else None)
        if night is None:
            messagebox.showinfo(APP_TITLE, "Nights with frames in a planet transit: "
                                + ", ".join(self.night_label(n, short=True) for n in nights)
                                + ".\n\nClick one in the night table first, then Planet transits… again.")
            return
        info = self._night_info(night)
        what = "; ".join(self._planet_hits_text(h) for h in info["planet_transits"])
        now = bool(info.get("transit_flagged"))
        flag = messagebox.askyesno(
            APP_TITLE, f"{self.night_label(night, short=True)}: {what}; now " + ("left out" if now else "kept")
            + ".\n\nLeave them out (campaign rule: no data while a planet transits)?", default="yes" if now else "no")
        if flag != now or "transit_flagged" not in info:
            self._set_transit_flags(night, flag)
            self._after_transit_flags()

    def _warn_edges(self, night: str, night_points: list):
        """2.2.3: tell when a comp, spare or check star sat near the frame edge (or in a weak part of the flat)."""
        shape = getattr(self.preview, "shape", None) if self.preview is not None else None
        if not shape:
            return
        try:
            margin = 3.0 * float(self.sky_out.get())
        except Exception:
            margin = 60.0
        flat = getattr(self.masters, "flat", None) if self.masters is not None else None
        scale = 1.0 if self.pattern.get() == core.MONO else 2.0
        try:
            problems = core.edge_problems(night_points, shape, list(self.comps_used), self.check_used, margin,
                                          flat, scale)
        except Exception:
            problems = []
        if not problems:
            return
        for p in problems:
            self.log(f"⚠ {night}: {p}")
        messagebox.showwarning(APP_TITLE, f"{self.label_nights(night)}: near the frame edge or a weak part of the flat:\n\n  • "
                               + "\n  • ".join(problems))

    def _photon_noise_setup(self, lights, binning, pattern, mode, masters, precalibrated, stars, radius, sky_in,
                            sky_out) -> dict:
        """2.2.17 (item 8): electrons per image unit for this run's photon noise (core.photon_noise_setup), from the
        first light's EGAIN, else the sky noise of the reference frame, else 1 e-/ADU; logged once."""
        try:
            header = core.read_header(lights[0]) if lights else {}
        except Exception:
            header = {}
        flat = getattr(masters, "flat", None) if masters is not None else None
        bias_removed = bool(precalibrated or getattr(masters, "bias", None) is not None
                            or getattr(masters, "dark", None) is not None)
        sky_k, sky_level = float("nan"), float("nan")
        try:   # review: always measured, as the fallback or as a cross-check of EGAIN
            sky_k, sky_level = core.sky_electrons_estimate(self.preview, stars, radius, sky_in, sky_out, with_level=True)
        except Exception:
            pass
        try:
            setup = core.photon_noise_setup(header, binning, pattern, mode, flat, bias_removed, precalibrated, sky_k,
                                            sky_level)
        except Exception as exc:
            setup = {"electrons": None, "source": "old", "note": f"Photon noise: gain not worked out ({exc}); "
                                                                  "the pre-2.2.17 error model is used."}
        self._noise_model = {"source": setup.get("source", ""), "e_per_adu": setup.get("e_per_adu"),
                             "k": setup.get("k_median")}
        self.log(setup.get("note", ""))
        if setup.get("warning"):
            self.log("⚠ " + setup["warning"])
        return setup

    def _night_setup(self) -> dict:
        """2.2.3: what a night's reduction used, stored per night in the series so later nights can be compared."""
        def num(var):
            try:
                return float(var.get())
            except Exception:
                return None
        out = {"binning": int(self.binning.get() or 1), "method": "bayer", "app_version": APP_VERSION,
               "radius": num(self.radius), "sky_in": num(self.sky_in), "sky_out": num(self.sky_out),
               "comps": list(self.comps_used), "check": self.check_used or ""}
        # 2.2.9: which telescope/camera and observer took the night, so nights from other equipment are recognized.
        inst = self._lights_instrument()
        if inst:
            out["instrument"] = inst
        # 2.2.16 (item 5): the telescope as written out (Telescope field / TELESCOP unless a mount / aperture and
        # focal length). "instrument" stays the raw header identity: nights are compared on it (series saved before
        # 2.2.16 have it), so changing it would call John's own earlier nights "other equipment".
        try:
            scope = self._telescope_text()
        except Exception:
            scope = ""
        if scope:
            out["telescope"] = scope
        # The header's OBSERVER names who took the frames; the Input page box names who reduces them.
        try:
            lights = self._lights()
            who = str(core.read_header(lights[0]).get("OBSERVER") or "").strip() if lights else ""
        except Exception:
            who = ""
        if who or self.observer.get().strip():
            out["observer"] = who or self.observer.get().strip()
        if getattr(self, "_own_setup", None) is not None:
            out["borrowed"] = True   # this night used another telescope's site and optics
        if getattr(self, "_rgb_channel", None):
            out["rgb_channel"] = self._rgb_channel   # 2.2.18 (review S4): colour images, the channel measured
        used = getattr(self.masters, "used", None) if self.masters is not None else None
        if used:
            out["masters"] = [used[k] for k in ("bias", "dark", "flat") if used.get(k)]   # 2.2.8
        model = getattr(self, "_noise_model", None)
        if model and model.get("source"):
            out["photon_noise"] = dict(model)   # 2.2.17 (item 8): how the photon noise was converted to electrons
        return out

    @staticmethod
    def _night_pier(night_points, mount: str | None = None) -> dict:
        """2.2.14 (item 20): a night's pier sides from the first readable header of each segment (before / after the
        meridian flip): ROTATOR (camera angle), else PIERSIDE, else the flip seen in the image orientation.
        2.2.18 (item 8): an alt-az night has no pier sides (NINA writes PIERSIDE 'East' all night even there)."""
        if mount == core.MOUNT_ALTAZ:
            return {"source": "altaz", "before": "", "after": "", "note": "alt-az mount: no pier sides"}
        def first_header(points):
            for o in points[:5]:
                try:
                    return core.read_header(o.path)
                except Exception:
                    continue
            return None

        pts = sorted(night_points, key=lambda o: o.jd)
        before = [o for o in pts if o.segment != "flip"]
        after = [o for o in pts if o.segment == "flip"]
        hb, ha = first_header(before), first_header(after)
        return core.night_pier_info(core.header_rotator(hb) if hb else None, core.header_rotator(ha) if ha else None,
                                    (hb or {}).get("PIERSIDE"), (ha or {}).get("PIERSIDE"), has_flip=bool(after))

    def _night_info(self, night: str, create: bool = False) -> dict:
        """2.2.14 review: a night's setup dict. Series saved before 2.2.3 hold a bare binning number there; it is read
        as {"binning": n} and, with create=True, stored back in that form so new keys can be added without losing it."""
        info = self.night_bin.get(night)
        if isinstance(info, dict):
            return info
        out = {"binning": info} if isinstance(info, int) and not isinstance(info, bool) else {}
        if create:
            self.night_bin[night] = out
        return out

    def _pier_by_night(self) -> dict:
        """2.2.14 (item 20): {night: pier info} for the series. Nights saved before 2.2.14 get theirs from their
        frames' headers when the files are still there (then stored with the series), else from the flip alone."""
        out = {}
        by_night: dict = {}
        for o in self.observations:
            by_night.setdefault(o.night, []).append(o)
        for night, pts in by_night.items():
            info = self.night_bin.get(night)
            if isinstance(info, dict) and isinstance(info.get("pier"), dict):
                out[night] = info["pier"]
                continue
            # Headers are read once per night and session (the files may be on a drive that is not there).
            cache = self._pier_cache
            key = (night, len(pts), sum(1 for o in pts if o.segment == "flip"))
            if key not in cache:
                cache[key] = self._night_pier(pts, (info or {}).get("mount") if isinstance(info, dict) else None)
            pier = cache[key]
            out[night] = pier
            if pier.get("source") in ("ROTATOR", "PIERSIDE", "altaz"):   # 2.2.18 (item 8): altaz
                self._night_info(night, create=True)["pier"] = pier
        # 2.2.15 (item 10): angles within ±5° are one side (29° and 30°), their 180° partners the other; the stored
        # per-night angles stay as read, the grouping is made here for every use (table, caution, segments, health).
        out = core.group_pier_sides(out)
        # A night known only from its flip: before the flip is the same pier side every night on a German mount, so
        # it takes the side names the campaign's ROTATOR / PIERSIDE nights give the two halves.
        pairs = [(p["before"], p["after"]) for p in out.values()
                 if p.get("source") in ("ROTATOR", "PIERSIDE") and p.get("before") and p.get("after")]
        if pairs:
            usual = max(set(pairs), key=pairs.count)
            for night, p in list(out.items()):
                if p.get("source") == "flip":
                    out[night] = dict(p, before=usual[0], after=usual[1],
                                      note=p.get("note", "") + f"; named {usual[0]}/{usual[1]} like the other nights")
        return out

    def _pier_values(self, points, values):
        """2.2.14 (item 20): 'Pier side as segments': each side moved onto the reference side's level.
        Returns (values, side_offsets result)."""
        sides = core.point_sides(points, self._pier_by_night())
        offs = core.side_offsets(points, values, sides)
        return core.apply_side_offsets(sides, values, offs["offsets"]), offs

    def _usable(self) -> list:
        """Observations that go into the period search and the AAVSO report."""
        if not self.skip_flagged.get():
            return [obs for obs in self.observations if math.isfinite(obs.mag)]
        return [obs for obs in self.observations if not obs.flag and math.isfinite(obs.mag)]

    def _cloud_fraction(self) -> float:
        try:
            return max(0.0, min(95.0, float(self.cloud_limit.get()))) / 100.0
        except Exception:
            return 0.20

    def _apply_cloud(self) -> int:
        # 2.2.14 (item 18): smooth, even fades under a flat sky are transmission loss (dew/haze), flagged "haze";
        # nights where the user chose to keep them stay unflagged. The episodes are kept for the night table.
        keep = {n for n, info in self.night_bin.items() if isinstance(info, dict) and info.get("keep_transmission")}
        self._loss_report = {}
        n = core.apply_cloud_flags(self.observations, self._cloud_fraction(), keep_nights=keep,
                                   report=self._loss_report)
        for night, eps in self._loss_report.items():
            failed = [e for e in eps if e.get("failed")]
            if failed and night not in self._loss_failed_logged:
                self._loss_failed_logged.add(night)   # 2.2.14 review: once per night and session
                self.log(f"{self.label_nights(night)}: the cloud / transmission-loss check failed "
                         f"({failed[0].get('error', '')}); every frame below the cloud limit is flagged cloud.")
        # 2.2.14 (item 10): outliers right next to a cloud run (see core.apply_cloud_edge_flags for the rule).
        self._cloud_edge_count = core.apply_cloud_edge_flags(self.observations)
        return n

    def _transmission_summary(self, night: str) -> tuple[int, str]:
        """2.2.14 (item 18): (frames, words) of a night's transmission-loss stretches, e.g. (120, '00:15–02:25, down
        to 0.94 mag'); (0, '') when there are none."""
        eps = [e for e in (self._loss_report or {}).get(night, []) if e["kind"] == "transmission"]
        if not eps:
            return 0, ""
        parts = []
        for e in eps:
            try:
                a = core.jd_to_datetime_utc(e["jd"][0]).astimezone()
                b = core.jd_to_datetime_utc(e["jd"][1]).astimezone()
                when = f"{a:%H:%M}–{b:%H:%M}"
            except (OverflowError, ValueError, OSError):
                when = f"JD {e['jd'][0]:.3f}–{e['jd'][1]:.3f}"
            parts.append(f"{when}, down to {e['max_depth']:.2f} mag")
        return sum(len(e["frames"]) for e in eps), "; ".join(parts)

    def _transmission_excess(self, night: str) -> float:
        """2.2.14 review: the largest star-to-star scatter beyond photon noise measured in the night's
        transmission-loss stretches (mag); it is added to each kept point's error."""
        eps = [e for e in (self._loss_report or {}).get(night, []) if e["kind"] == "transmission"]
        return max([float(e.get("excess") or 0.0) for e in eps] + [0.0])

    KEEP_USE, KEEP_LEAVE = "Use these points", "Leave them out"   # 2.2.18 (item 4): the Keep question's buttons

    def _keep_question(self, night: str) -> str:
        # 2.2.18 (item 4): labeled buttons instead of Yes/No (16 Oct: "now left out. Keep these points?" was read as
        # "keep as is", and a Yes put dew points back into the HD 219134 series).
        ex = self._transmission_excess(night)
        return ("Use these points in the light curve (errors scaled for the lost light), or leave them out?\n\n"
                f"\u201c{self.KEEP_USE}\u201d: each frame's photon error already comes from its own (reduced) fluxes, "
                "and scintillation is a fraction of the light that arrives, so the error bars grow where the light is "
                "lost. "
                + (f"On top of that, every point used gets the {ex:.3f} mag star-to-star scatter measured in that "
                   "stretch (in quadrature), so the error bars stay honest even if this was a smooth cloud bank. "
                   if ex > 0 else "")
                + f"\u201c{self.KEEP_LEAVE}\u201d (the default; also Escape or closing this window) leaves them "
                  "flagged, like cloud.")

    def _ask_keep(self, night: str, message: str, where: str) -> bool:
        """2.2.18 (item 4): the Keep question with labeled buttons; the answer is logged with the night and the time.
        True = use the points."""
        keep = ask_choice(self, APP_TITLE, message, [(True, self.KEEP_USE), (False, self.KEEP_LEAVE)], safe=False)
        keep = keep is True
        self.log(f"{self.label_nights(night)}: {core.HAZE_LABEL} question ({where}) answered "
                 f"\u201c{self.KEEP_USE if keep else self.KEEP_LEAVE}\u201d at {time.strftime('%Y-%m-%d %H:%M:%S')}.")
        return keep

    def _offer_keep_transmission(self, night: str) -> None:
        """2.2.14 (item 18): after photometry, a night whose lost light is transmission loss (all stars fading
        together, smoothly, flat sky) is offered back instead of the blanket cloud cut."""
        n, words = self._transmission_summary(night)
        if not n:
            return
        # 2.2.14 review 2: with one measured star the evenness across the field cannot be tested and no star-to-star
        # scatter can be added to the errors, so the points are not offered back: they stay flagged like cloud.
        eps = [e for e in (self._loss_report or {}).get(night, []) if e["kind"] == "transmission"]
        if any("one star only" in " ".join(e.get("reasons", [])) for e in eps):
            self.log(f"{self.label_nights(night)}: {n} frames lost light smoothly ({words}), but only one star was "
                     "measured, so cloud cannot be told from dew/haze: left out (flagged). Transmission loss… on the "
                     "Output page can still keep them.")
            return
        self.log(f"{self.label_nights(night)}: {n} frames with {core.HAZE_LABEL} ({words}): every measured star "
                 "faded together, smoothly, with a flat sky; the differential magnitudes hold, only the noise grows.")
        keep = self._ask_keep(
            night,
            f"{n} frames of {self.night_label(night, short=True)} lost light ({words}), but every measured star "
            "(comps, check, spares) faded by the same amount, smoothly, with a flat sky: "
            f"{core.HAZE_LABEL}, not cloud.\n\n" + self._keep_question(night)
            + " Transmission loss… on the Output page changes this later.", "after photometry")
        self._set_keep_transmission(night, keep)

    def _set_keep_transmission(self, night: str, keep: bool) -> None:
        info = self._night_info(night, create=True)
        if keep:
            info["keep_transmission"] = True
        else:
            info.pop("keep_transmission", None)
        self.log(f"{self.label_nights(night)}: {core.HAZE_LABEL} points "
                 + ("kept (errors follow the reduced light, plus the star-to-star scatter measured in that stretch)."
                    if keep else "left out (flagged)."))

    def edit_transmission(self):
        """2.2.14 (item 18): keep or leave out a night's transmission-loss points (the highlighted night, or the
        only night that has some)."""
        self._apply_cloud()
        nights = [n for n in sorted({o.night for o in self.observations}) if self._transmission_summary(n)[0]]
        if not nights:
            messagebox.showinfo(APP_TITLE, f"No night has {core.HAZE_LABEL} at the current cloud limit. (Lost light "
                                           "that is patchy, fast, or comes with a brighter sky stays flagged cloud.)")
            return
        night = self.highlight_night if self.highlight_night in nights else (nights[0] if len(nights) == 1 else None)
        if night is None:
            messagebox.showinfo(APP_TITLE, "Nights with " + core.HAZE_LABEL + ": "
                                + ", ".join(self.night_label(n, short=True) for n in nights)
                                + ".\n\nClick one in the night table first, then Transmission loss… again.")
            return
        n, words = self._transmission_summary(night)
        kept = bool(self._night_info(night).get("keep_transmission"))
        keep = self._ask_keep(
            night,
            f"{self.night_label(night, short=True)}: {n} frames with {core.HAZE_LABEL} ({words}). They are now "
            + ("used in the light curve" if kept else "left out (flagged)") + ".\n\n" + self._keep_question(night),
            "Transmission loss…")
        if keep != kept:
            self._set_keep_transmission(night, keep)
            self.refit_period()

    def cancel_job(self):
        if not self.busy:
            self.status.set("Nothing is running.")
            return
        self._cancel.set()
        if getattr(getattr(self, "transit_page", None), "_checking", False):
            self.log("Cancel requested; stopping the comp check after the current refit…")   # 2.2.16 (item 6)
        else:
            self.log("Cancel requested; stopping after the current frame…")

    def _refresh_period(self):
        self._apply_cloud()
        usable = self._usable()
        self.period = None
        if len(usable) < 8:
            return
        corrected = self._analysis_values(usable)
        jd = np.array([o.jd for o in usable])
        values = np.array(corrected, dtype=float)
        self._period_note = f"every point ({len(usable)})"
        point_nights = [o.night for o in usable]
        minutes = {"5 min bins": 5.0, "10 min bins": 10.0, "30 min bins": 30.0}.get(self.period_bins.get())
        if minutes:
            # Bins make each night count by the hours observed, not by how many frames were taken,
            # and the false-alarm probability is no longer inflated by thousands of correlated frames.
            bins = core.bin_points(usable, list(corrected), minutes)
            if len(bins) >= 8:
                jd = np.array([b["jd"] for b in bins])
                values = np.array([b["value"] for b in bins])
                point_nights = [b["night"] for b in bins]
                self._period_note = f"{len(bins)} {minutes:g}-min bins"
        try:
            self._period_range_used = (float(self.min_period.get()), float(self.max_period.get()))   # 2.2.18 (item 16)
        except Exception:
            self._period_range_used = None
        try:
            self.period = core.lomb_scargle(jd, values, float(self.min_period.get()), float(self.max_period.get()))
        except Exception as exc:
            self.status.set(f"Period search: {exc}")
            self.period = None
        if self.period:
            # 2.2.3: sanity checks on the best period (one-day aliases; a period carried by one night alone).
            self.period["alias_text"] = core.period_alias_text(self.period["period_days"])
            fap = self.period.get("fap", float("nan"))
            loo = {"carried_by": [], "checked": 0}
            if not (math.isfinite(fap) and fap > 0.05):
                try:
                    loo = core.period_leave_one_out(jd, values, point_nights, self.period,
                                                    float(self.min_period.get()), float(self.max_period.get()))
                except Exception:
                    pass
            self.period["carried_by"] = loo["carried_by"]
            # 2.2.17 (item 10): a FAP that counts nights, and the airmass / color check of the nightly means.
            # review: any failure there leaves the period as it was without the check, with one log line.
            try:
                self._period_airmass_checks(usable, corrected, jd, values, point_nights)
            except Exception as exc:
                for key in ("night_fap", "airmass_trend", "night_means", "airmass_check"):
                    self.period.pop(key, None)
                self.log(f"Airmass check of the period not done ({exc}).")

    def _period_airmass_checks(self, usable, corrected, jd, values, point_nights):
        """2.2.17 (item 10): before a best period is shown as significant. (b) its FAP counting nights
        (core.period_night_fap); (a) the nightly means of the fold's values (check-star corrected when that is on)
        against each night's median airmass (core.airmass_trend); (c) when they follow airmass, the period is
        demoted if it disappears with the trend taken out or without the high-airmass nights
        (core.period_airmass_check). Airmass: the points' own, else from the target's RA/Dec and the site; without
        either the check is skipped with one log line."""
        try:
            lo, hi = float(self.min_period.get()), float(self.max_period.get())
        except (TypeError, ValueError):
            return
        try:
            self.period["night_fap"] = core.period_night_fap(jd, values, point_nights, self.period, lo, hi)
        except Exception:
            self.period["night_fap"] = None
        night_am = self._night_airmasses(usable)
        means = core.nightly_means(corrected, [o.night for o in usable])
        for m in means:
            m["airmass"] = night_am.get(m["night"], float("nan"))
        trend = core.airmass_trend(means)
        self.period["airmass_trend"] = trend
        self.period["night_means"] = means
        key = (trend.get("skip") or "", len(means))
        if trend.get("skip") and getattr(self, "_airmass_skip_logged", None) != key:
            self._airmass_skip_logged = key
            if trend["skip"] == "no airmass":
                self.log("Airmass check of the period skipped: no airmass (type the site on the Input page and the "
                         "target's RA/Dec, or use frames with pointing in the headers).")
        try:
            self.period["airmass_check"] = core.period_airmass_check(
                jd, values, point_nights, night_am, self.period, lo, hi, trend,
                name=lambda n: self.night_label(n, short=True))
        except Exception:
            self.period["airmass_check"] = None

    def _draw_airmass_corrected(self, ax, usable, zoom):
        """2.2.17 (item 10): with "Airmass-corrected night means" ticked, each night's mean of the fold's values with
        the airmass trend taken out (hollow orange diamonds) beside the uncorrected mean (small filled ones), on the
        whole-series light curve. Display only: the corrected values never go into a report, CSV or the period."""
        if not self.airmass_view.get() or zoom is not None or not self.period or not usable:
            return
        trend = self.period.get("airmass_trend") or {}
        means = self.period.get("night_means") or []
        slope, ref = trend.get("slope", float("nan")), trend.get("ref_airmass", float("nan"))
        if not (math.isfinite(slope) and math.isfinite(ref)):
            ax.text(0.01, 0.02, "Airmass-corrected night means: not available ("
                    + (trend.get("skip") or "no airmass trend fitted") + ")", transform=ax.transAxes, fontsize=8,
                    color="#5d6d76", ha="left", va="bottom")
            return
        t_by = {}
        for o in usable:
            if math.isfinite(o.jd):
                t_by.setdefault(o.night, []).append(o.jd)
        rows = [(float(np.mean(t_by[m["night"]])), m["mean"], m["mean"] - slope * (m["airmass"] - ref), m)
                for m in means if m["night"] in t_by and math.isfinite(m.get("airmass", float("nan")))]
        if not rows:
            return
        x = self._time_values(np.array([r[0] for r in rows]))
        y0 = np.array([r[1] for r in rows])
        y1 = np.array([r[2] for r in rows])
        ax.plot(x, y0, "D", ms=4, color="#b86e00", alpha=0.45, zorder=6)
        ax.plot(x, y1, "D", ms=8, mfc="none", mec="#b86e00", mew=1.6, zorder=7)
        lo, hi = sorted(ax.get_ylim())
        both = np.concatenate([y0, y1])
        pad = 0.1 * max(float(both.max() - both.min()), 0.005)
        lo, hi = min(lo, float(both.min()) - pad), max(hi, float(both.max()) + pad)
        ax.set_ylim(hi, lo)   # magnitudes: bright at the top
        ax.text(0.01, 0.02, "◇ airmass-corrected night means", transform=ax.transAxes, fontsize=8, color="#b86e00",
                ha="left", va="bottom")
        self._hover_sets.append((ax, x, y1, [f"{self.night_label(r[3]['night'], short=True)} night mean\n"
                                             f"{r[1]:.4f} → {r[2]:.4f} airmass-corrected\n(airmass "
                                             f"{r[3]['airmass']:.2f}, {slope:+.3f} mag per airmass; display only)"
                                             for r in rows], 6.0))

    def _night_airmasses(self, points) -> dict:
        """2.2.17 (item 10): {night: median airmass}: from the points' airmass, else (no pointing in the headers)
        from the target's catalog RA/Dec and the site at up to 7 times through the night (cached)."""
        out = {}
        by_night = {}
        for o in points:
            by_night.setdefault(o.night, []).append(o)
        try:
            ra = float(self.ra_hours.get()) if self.ra_hours.get().strip() else None
            dec = float(self.dec_deg.get()) if self.dec_deg.get().strip() else None
            lat = float(self.lat.get()) if self.lat.get().strip() else None
            lon = float(self.lon.get()) if self.lon.get().strip() else None
        except (TypeError, ValueError):
            ra = dec = lat = lon = None
        cache = getattr(self, "_night_am_cache", None)
        if cache is None:
            cache = self._night_am_cache = {}
        for night, pts in by_night.items():
            own = [o.airmass for o in pts if o.airmass is not None and math.isfinite(o.airmass)]
            if len(own) >= max(1, len(pts) // 2):
                out[night] = float(np.median(own))
                continue
            if None in (ra, dec, lat, lon):
                continue
            jds = sorted(o.jd for o in pts if math.isfinite(o.jd))
            if not jds:
                continue
            ck = (night, round(ra, 4), round(dec, 3), round(lat, 3), round(lon, 3), round(jds[0], 4), len(jds))
            if ck not in cache:
                picks = [jds[int(i)] for i in np.linspace(0, len(jds) - 1, min(7, len(jds)))]
                vals = []
                for t in picks:
                    try:
                        a = core.airmass(ra, dec, lat, lon, t)
                    except Exception:
                        continue
                    if math.isfinite(a) and a > 0:
                        vals.append(a)
                cache[ck] = float(np.median(vals)) if vals else float("nan")
            if math.isfinite(cache[ck]):
                out[night] = cache[ck]
        return out

    _META_STRINGS = (
        "observer", "star_id", "chart", "comp_name", "comp_mag", "check_name", "check_mag",
        "comp2_name", "comp2_mag", "ra_hours", "dec_deg", "notes", "lat", "lon", "pattern", "debayer_mode", "catalog",
        "time_axis", "bin_choice", "mark_periods",
    ) + tuple(f"{r}_{k}" for r in COMP_ROLES[2:] for k in ("name", "mag"))   # 2.2.5: comps 3-10
    _META_NUMBERS = ("min_period", "max_period", "radius", "sky_in", "sky_out", "focal_mm", "pixel_um", "sat_limit", "cloud_limit")

    def _meta(self) -> dict:
        meta = {key: getattr(self, key).get() for key in self._META_STRINGS}
        # 2.2.16 (item 11): a comp or check name is saved only from a slot with a marker, or one the series used
        # (15 Oct: an auto-save wrote TOI-2046's comp2-comp6 names and V into HD 219134's series, from slots left
        # with names but no markers). Every save (Save series, auto-save, a night added) comes through here.
        # review: the keep-set is every name the series knows (its file's comp/check names, the comps and check of
        # any run, catalog magnitudes, report names), so a night added with fewer comps or no check never blanks
        # the series' own; only names of another field are left out.
        known = set(getattr(self, "_series_names", ()) or ())
        series_check = (getattr(self, "_series_check", None) or ("",))[0]
        known |= {name_key(n) for n in list(self.comps_used or ()) + [self.check_used, series_check]
                  + list(self.comp_catalog_mags or {}) + list(self.active_comps or ())
                  + [x for kv in (self.report_names or {}).items() for x in kv]}
        known.discard("")
        for role in list(COMP_ROLES) + ["check"]:
            name = str(meta.get(f"{role}_name") or "").strip()
            if name and getattr(self, f"{role}_xy", None) is None and name_key(name) not in known:
                meta[f"{role}_name"] = ""
                meta[f"{role}_mag"] = ""
        for key in self._META_NUMBERS:
            try:
                meta[key] = float(getattr(self, key).get())
            except Exception:
                pass
        try:
            meta["binning"] = int(self.binning.get())
        except Exception:
            pass
        meta["rejected"] = sorted(os.path.basename(path) for path in self.rejected)
        meta["comps_used"] = list(self.comps_used)
        meta["star_bands"] = self.star_bands
        meta["seq_auids"] = dict(self.seq_auids)   # 2.2.13
        meta["report_names"] = dict(self.report_names)   # 2.2.14 (item 7)
        meta["night_notes"] = self.night_notes
        meta["use_check_zp"] = bool(self.use_check_zp.get())
        meta["check_used"] = self.check_used
        meta["app_version"] = APP_VERSION
        meta["color_cal"] = self.color_cal
        meta["mode"] = self.series_mode or self.mode_key
        meta["watch_list"] = self.watch_list
        meta["spare_catalog"] = self.spare_catalog
        meta["comp_catalog_mags"] = self.comp_catalog_mags
        meta["active_comps"] = list(self.active_comps)
        meta["align_flip"] = bool(self.align_flip.get())
        meta["pier_segments"] = bool(self.pier_segments.get())   # 2.2.14 (item 20)
        meta["period_bins"] = self.period_bins.get()
        meta["night_binning"] = self.night_bin
        meta["legacy_mixed_nights"] = sorted(self.legacy_mixed)
        meta["elevation"] = self.elevation.get()
        if getattr(self, "_rgb_channel", None):
            meta["rgb_channel"] = self._rgb_channel   # 2.2.18 (review S4): for information (the night setup has it)
        meta["reports_saved"] = self.reports_saved
        meta["reports_sent"] = self.reports_sent   # 2.2.18 (item 10): a new key; older versions ignore it
        meta["comp_coords"] = {k: list(v) for k, v in self.comp_coords.items()}
        planet = self.planet_from_fields()
        if planet:
            meta["planet"] = {k: v for k, v in planet.items() if isinstance(v, (int, float, str)) or v is None}
            meta["planet_info"] = {k: v for k, v in (self.planet_info or {}).items()
                                   if isinstance(v, (int, float, str)) or v is None}
        return meta

    def _apply_meta(self, meta: dict):
        # 2.2.4: a series file's site is shown for that series but never saved as your own site.
        self._loading_meta = True
        try:
            self._apply_meta_values(meta)
        finally:
            self._loading_meta = False
        self._take_series_snapshot()   # 2.2.14 (item 11): the loaded series' catalog magnitudes

    def _take_series_snapshot(self):
        """2.2.14 (item 11): remember the series' catalog magnitudes (comps, spares) and spare positions as they are
        now, so tonight's catalog values never pass for the series'. Taken when a series is loaded or added to, a
        night is added or a new series starts; restored after a cancelled or empty run."""
        self._series_catalog_before = {
            "comps": dict(self.comp_catalog_mags),
            "spares": {n: float(i["mag"]) for n, i in self.spare_catalog.items() if i.get("mag") is not None},
            "spare_coords": {n: (float(i["ra"]), float(i["dec"])) for n, i in self.spare_catalog.items()
                             if i.get("ra") is not None and i.get("dec") is not None},
        }

    def _restore_series_catalog(self, stars: bool = False):
        """2.2.14 (item 11, review): a run that added nothing leaves the series' catalog magnitudes as they were
        (tonight's comp and spare values are put back to the snapshot's); stars=True also puts back the comps and
        check star the series used (run_photometry had already switched them to tonight's)."""
        snap = getattr(self, "_series_catalog_before", None)
        if snap:
            self.comp_catalog_mags = dict(snap.get("comps") or {})
            for name, mag in (snap.get("spares") or {}).items():
                if name in self.spare_catalog:
                    self.spare_catalog[name]["mag"] = mag
        if stars and getattr(self, "_prev_stars", None) is not None:
            self.comps_used, self.check_used = list(self._prev_stars[0]), self._prev_stars[1]

    def _apply_meta_values(self, meta: dict):
        for key in self._META_STRINGS:
            if meta.get(key) not in (None, ""):
                getattr(self, key).set(meta[key])
        # 2.2.5: a series' target already has its RA/Dec; don't look it up again when the Star ID box loses focus.
        if meta.get("star_id") and meta.get("ra_hours"):
            self._last_lookup_name = str(meta["star_id"]).strip()
        # 2.2.5: the series says which comps 3-10 it uses; slots it does not name are emptied (a 2.2.4 or older
        # series has at most two comps).
        for r in COMP_ROLES[2:]:
            if meta.get(f"{r}_name") in (None, "") and getattr(self, f"{r}_xy") is None:
                getattr(self, f"{r}_name").set("")
                getattr(self, f"{r}_mag").set("")
        for key in self._META_NUMBERS:
            if meta.get(key) not in (None, ""):
                try:
                    getattr(self, key).set(float(meta[key]))
                except (TypeError, ValueError):
                    pass
        if meta.get("binning"):
            want = int(meta["binning"])
            have = self.masters_binning if self.calibrated is not None else None
            if have and want != int(have):
                # 2.2.13 (John): never switch silently away from the binning the images were calibrated at (13 Oct:
                # Load series set Bin 2 over a Bin 1 calibration, and the solve then failed with a baffling no-lock).
                if messagebox.askyesno(
                        APP_TITLE,
                        f"This series was reduced at Binning {want}, but the images calibrated now are Binning "
                        f"{have}.\n\nYes: switch to Binning {want} (then Calibrate again, so the new night matches "
                        f"the series).\nNo: keep Binning {have} (the night will be flagged as a different setup "
                        "when you add it)."):
                    self.binning.set(want)
                else:
                    self.log(f"Binning kept at {have}; the series was reduced at {want}.")
            else:
                self.binning.set(want)
        self.comps_used = list(meta.get("comps_used") or [])
        if isinstance(meta.get("star_bands"), dict):
            self.star_bands = meta["star_bands"]
        if isinstance(meta.get("seq_auids"), dict):
            self.seq_auids = {**self.seq_auids, **meta["seq_auids"]}
        given = meta.get("report_names")   # 2.2.14 (item 7)
        self.report_names = {str(k): str(v) for k, v in given.items() if v} if isinstance(given, dict) else {}
        if isinstance(meta.get("night_notes"), dict):
            self.night_notes = dict(meta["night_notes"])
        if "use_check_zp" in meta:
            self.use_check_zp.set(bool(meta["use_check_zp"]))
        self.check_used = meta.get("check_used") or ""
        self._series_check = (self.check_used, str(meta.get("check_mag") or ""))   # 2.2.8
        # 2.2.16 (item 11, review): the comp and check names this series' file holds (kept by _meta's rule).
        self._series_names = {name_key(meta.get(f"{r}_name")) for r in list(COMP_ROLES) + ["check"]} - {""}
        self.color_cal = dict(meta.get("color_cal") or {})
        self.series_mode = meta.get("mode") if meta.get("mode") in MODES else "variables"
        self.series_mode_unknown = meta.get("mode") not in MODES   # 2.2.14 (item 19): an old file: no mode check
        if isinstance(meta.get("watch_list"), list):
            self._drop_session_watch_aliases([w for w in meta["watch_list"] if isinstance(w, dict) and w.get("name")])
            known = {w["name"] for w in self.watch_list}
            self.watch_list += [w for w in meta["watch_list"] if isinstance(w, dict) and w.get("name") not in known]
            # 2.2.14 (item 15): show them at once (14 Oct: the box stayed empty after Load series): found on an
            # image that is solved already, else listed as not found on this frame until Solve.
            if self.chart_placed and self.preview is not None:
                self._locate_watch_stars()
            else:
                self._refresh_watch_box()
            if hasattr(self, "pick_label"):
                self._update_pick_label()
        self.spare_catalog = dict(meta.get("spare_catalog") or {})
        self.comp_catalog_mags = {str(k): float(v) for k, v in (meta.get("comp_catalog_mags") or {}).items()
                                  if v is not None}
        self.active_comps = list(meta.get("active_comps") or [])
        if "align_flip" in meta:
            self.align_flip.set(bool(meta["align_flip"]))
        self.pier_segments.set(bool(meta.get("pier_segments")))   # 2.2.14 (item 20)
        if meta.get("period_bins") in PERIOD_BIN_CHOICES:
            self.period_bins.set(meta["period_bins"])
        self.night_bin = dict(meta.get("night_binning") or {})
        # 2.2.3: these belong to the series being loaded; never carry another series' over.
        self.reports_saved = {}
        current = {getattr(self, f"{r}_name").get().strip() for r in COMP_ROLES} | {self.check_name.get().strip()}
        self.comp_coords = {k: v for k, v in self.comp_coords.items() if k in current}   # tonight's picks stay
        if isinstance(meta.get("comp_coords"), dict):
            for k, v in meta["comp_coords"].items():
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    self.comp_coords.setdefault(str(k), (float(v[0]), float(v[1])))
        if isinstance(meta.get("reports_saved"), dict):
            self.reports_saved = {str(k): list(v) for k, v in meta["reports_saved"].items() if isinstance(v, list)}
        self.reports_sent = {}   # 2.2.18 (item 10)
        if isinstance(meta.get("reports_sent"), dict):
            self.reports_sent = {str(k): [e for e in v if isinstance(e, dict)]
                                 for k, v in meta["reports_sent"].items() if isinstance(v, list)}
        if meta.get("elevation"):
            self.elevation.set(str(meta["elevation"]))
        if isinstance(meta.get("planet"), dict) and meta["planet"].get("period"):
            self.planet_info = dict(meta.get("planet_info") or {})
            self._planet_to_fields(meta["planet"])
            self._last_planet_lookup = self.planet_name.get().strip()   # 2.2.14 (item 2): the series has its values
        names = set(meta.get("rejected") or [])
        if names:
            restored = []
            for folder in (self.light_dir.get(), self.flat_dir.get()):
                for path in core.list_fits(folder):
                    if os.path.basename(path) in names:
                        restored.append(path)
            self.rejected.update(restored)

    def _drop_session_watch_aliases(self, series_watch: list):
        """2.2.14 (item 11): loading a series never keeps a session watch star that is one of the series' watch stars
        under another name (13 Oct: TYC 4006-1310-1 next to the series' APASS J231239.3+565553, 0.3" apart, which came
        back with 0 points). The session entry gives way to the series' (its spot on this frame moves over); logged."""
        if not self.watch_list or not series_watch:
            return
        series_names = [w["name"] for w in series_watch]
        coords = {w["name"]: (float(w["ra"]), float(w["dec"])) for w in list(series_watch) + list(self.watch_list)
                  if w.get("ra") is not None and w.get("dec") is not None}
        index = self._name_index() if self.chart_placed else {}
        keep = []
        for w in self.watch_list:
            name = w.get("name")
            match = None if name in series_names else self._same_series_star(name, series_names, coords, index)
            if match is None:
                keep.append(w)
                continue
            xy = self.watch_xy.pop(name, None)
            if xy is not None:
                self.watch_xy.setdefault(match, xy)
            self.log(f"Watch star {name} is {match} in the series (same star): the series' name is kept, "
                     f"{name} dropped from the watch list.")
        self.watch_list = keep

    def _write_series(self, path: str):
        # 2.2.14 (item 17): keep one version back. The first write to a file in a session (Save series, or the
        # automatic save when a night is added, removed, or comps change) moves the file on disk to X_prev.cvseries
        # (replacing an older _prev); later writes in the same session leave that copy alone, so it stays the version
        # from before this session. The new file is written completely (X.part) before anything is renamed.
        part = path + ".part"
        meta = self._meta()
        core.save_series(part, meta, self.observations)
        # 2.2.16 (item 11, review): the names now in the file are the series' own from here on.
        self._series_names = {name_key(meta.get(f"{r}_name")) for r in list(COMP_ROLES) + ["check"]} - {""}
        prev = series_prev_path(path)
        key = os.path.normcase(os.path.abspath(path))
        kept = ""
        if prev and os.path.isfile(path) and key not in self._prev_kept:
            try:
                os.replace(path, prev)
                kept = os.path.basename(prev)
                self.log(f"Series: the previous version was kept as {kept}.")
            except OSError as exc:
                self.log(f"Series: could not keep the previous version as {os.path.basename(prev)} ({exc}); "
                         "the file is overwritten.")
        self._prev_kept.add(key)   # a new file has nothing to keep; later saves this session keep the first _prev
        os.replace(part, path)
        self.series_path = path
        self._series_saved_fp = self._series_fingerprint()   # 2.2.15 (item 7)
        self.update_series_status()
        return kept

    # ---- series status line (2.2.15, item 7) ------------------------------------------------
    def _series_fingerprint(self):
        """What the open series holds now (points, flags, nights' setup, chosen comps), to tell unsaved changes."""
        pts = tuple((o.night, round(o.jd, 6) if o.jd is not None and math.isfinite(o.jd) else None, o.flag or "",
                     round(o.mag, 6) if o.mag is not None and math.isfinite(o.mag) else None)
                    for o in self.observations)
        return hash((pts, tuple(self.active_comps or ()), repr(sorted(self.night_bin.items(), key=lambda kv: kv[0]))))

    def series_unsaved(self) -> bool:
        """True when the open series has points that are not in its file (never saved, or changed since)."""
        if not self.observations:
            return False
        if not self.series_path:
            return True
        return getattr(self, "_series_saved_fp", None) != self._series_fingerprint()

    def series_status_text(self) -> str:
        """The Input page's series line: "Series: HD_219134.cvseries — 8 nights, last 13–14 Oct"."""
        obs = [o for o in self.observations if o.night]
        if not obs:
            # 2.2.16 (item 12): say how to add the night to a series instead.
            return ("No series loaded — this run will be a single night. To add a night to a series, Load series… "
                    "before running.")
        name = os.path.basename(self.series_path) if self.series_path else "not saved yet"
        nights = {o.night for o in obs}
        timed = [o for o in obs if o.jd is not None and math.isfinite(o.jd)]
        last_obs = max(timed, key=lambda o: o.jd) if timed else None
        last_night = last_obs.night if last_obs else max(nights)
        label = self.night_label(last_night, short=True, jd=min(o.jd for o in timed if o.night == last_night)
                                 if last_obs else None)
        from datetime import datetime
        year = f" {datetime.now().year}"
        if label.endswith(year):
            label = label[:-len(year)]
        return (f"Series: {name} — {len(nights)} night{'s' if len(nights) != 1 else ''}, last {label}. "
                "After the run you'll be asked to add the night.")   # 2.2.16 (item 12)

    LOAD_SERIES_TIP = ("Load series… replaces what is open: the open series is set aside (you are offered a save "
                       "first if it has unsaved changes), and a series of another star clears this target's comps, "
                       "check and watch stars.")

    def _add_tooltip(self, widget, text: str):
        """2.2.16 (item 12): a small hover note on a widget (shown after a short pause, gone when the pointer leaves).
        Wraps at about 50 characters of the UI font, so it sizes itself on any screen."""
        state = {"tip": None, "job": None}

        def show():
            state["job"] = None
            if state["tip"] is not None:
                return
            tip = None
            try:
                # review: everything measured before the window exists; pixel offsets scaled with the display.
                wrap = text_wrap_px(widget, 50)
                k = ui_scale(widget)
                x = widget.winfo_rootx() + int(12 * k)
                y = widget.winfo_rooty() + widget.winfo_height() + int(4 * k)
                tip = Toplevel(widget)
                tip.wm_overrideredirect(True)
                tip.wm_geometry(f"+{x}+{y}")
                ttk.Label(tip, text=text, style="Hint.TLabel", justify=LEFT, padding=int(6 * k), relief="solid",
                          borderwidth=1, wraplength=wrap).pack()
                state["tip"] = tip
            except Exception:
                state["tip"] = None
                if tip is not None:   # never leave an empty borderless window behind
                    try:
                        tip.destroy()
                    except Exception:
                        pass

        def enter(_e=None):
            if state["job"] is None and state["tip"] is None:
                state["job"] = widget.after(500, show)

        def leave(_e=None):
            if state["job"] is not None:
                try:
                    widget.after_cancel(state["job"])
                except Exception:
                    pass
                state["job"] = None
            if state["tip"] is not None:
                try:
                    state["tip"].destroy()
                except Exception:
                    pass
                state["tip"] = None

        widget.bind("<Enter>", enter, add="+")
        widget.bind("<Leave>", leave, add="+")
        widget.bind("<ButtonPress>", leave, add="+")

    def update_series_status(self):
        """Refresh the Input page's series line (load, close, save, a night added or removed, a new series)."""
        if not hasattr(self, "series_status"):
            return
        self.series_status.set(self.series_status_text())
        # 2.2.16 (item 15): the Input and Output pages' Close series buttons are enabled together.
        for button in (getattr(self, "close_series_button", None), getattr(self, "close_series_button_out", None)):
            if button is None:
                continue
            try:
                button.configure(state="normal" if self.observations else "disabled")
            except Exception:
                pass

    def _offer_series_save(self, then: str) -> bool:
        """2.2.15 (item 7, review): before closing or replacing an open series with unsaved points: Yes saves first,
        No goes on without saving, Cancel stops. True when the caller may go on."""
        if not self.series_unsaved():
            return True
        name = os.path.basename(self.series_path) if self.series_path else "The series"
        what = ("has changes that are not saved" if self.series_path
                else f"({len({o.night for o in self.observations})} night(s)) has not been saved")
        answer = messagebox.askyesnocancel(
            APP_TITLE, f"{name} {what}.\n\nYes: save the series first, then {then}.\n"
                       f"No: {then} without saving.\nCancel: keep it open.")
        if answer is None:
            return False
        if answer:
            self.save_series()
            if self.series_unsaved():
                return False   # the save dialog was cancelled or failed: keep it open
        return True

    def _clear_series_state(self, keep_night: str = ""):
        """2.2.15 review: forget everything that belongs to the open series (not its points: the caller sets those),
        so none of it is written into the next series. Used by Close series, a new series started from a run
        (keep_night: tonight's night keeps its color calibration), a mode switch that closes the series, and Load
        series. Watch stars and AUIDs that came from a loaded series go (except into a new series started from tonight's
        run, which used them); ones added this session stay."""
        self._series_gen = getattr(self, "_series_gen", 0) + 1   # 2.2.16 (item 9): a pending planet check is stale
        kept = self.color_cal.get(keep_night) if keep_night else None
        self.color_cal = {keep_night: kept} if kept else {}
        self.series_path = ""
        self.series_mode = ""
        self.series_mode_unknown = False
        self.period = None
        self.night_bin = {}            # with it the nights' setup, pier sides and planet-transit info (item 1)
        self.legacy_mixed = set()
        self.active_comps = []
        self.reports_saved = {}
        self.reports_sent = {}       # 2.2.18 (item 10)
        self.night_notes = {n: v for n, v in self.night_notes.items() if n == keep_night} if keep_night else {}
        self.highlight_night = None
        self._loss_report = {}
        if hasattr(self, "_loss_failed_logged"):
            self._loss_failed_logged = set()
        self._pier_cache = {k: v for k, v in self._pier_cache.items()
                            if keep_night and isinstance(k, tuple) and k and k[0] == keep_night}
        self._series_check = ("", "")
        self._series_names = set()   # 2.2.16 (item 11, review)
        self._zp_caution_cache = None
        self._series_saved_fp = None
        self.extremum_csv = self.extremum_csv_star = ""
        # A new series started from tonight's run keeps the watch stars and AUIDs that run used (they are now its).
        from_series = set(getattr(self, "_series_watch_names", ()) or ()) if not keep_night else set()
        if from_series:
            self.watch_list = [w for w in self.watch_list if w.get("name") not in from_series]
            for name in from_series:
                self.watch_xy.pop(name, None)
            if hasattr(self, "_refresh_watch_box"):
                try:
                    self._refresh_watch_box()
                except Exception:
                    pass
        for key in (getattr(self, "_series_auid_keys", ()) or ()) if not keep_night else ():
            self.seq_auids.pop(key, None)
        self._series_watch_names = set()
        self._series_auid_keys = set()
        if not keep_night:
            self.report_names = {}   # 2.2.15 review 2: the closed series' report names stay with it

    def on_close(self):
        """2.2.18 (item 9): the main window's close button. A series file with unsaved changes, or a series of 2+
        nights never saved, is offered a save first (Yes saves, No closes without saving, Cancel keeps SHOBS-P open);
        otherwise (nothing unsaved, or one night never saved as a series) it closes at once.
        Asked once: a second close click while the question is up does nothing."""
        if getattr(self, "_closing", False):
            return
        self._closing = True
        try:
            try:
                # review: a single night never saved as a series (a transit run whose reports are saved) closes
                # without the question; a series file with changes, or an unsaved series of 2+ nights, asks.
                nights = {o.night for o in self.observations}
                worth = bool(self.series_path) or len(nights) >= 2
                go = self._offer_series_save("close SHOBS-P") if worth else True
            except Exception:
                traceback.print_exc()
                go = True   # never trap the user in the app because the question failed
            if not go:
                self.log("Close cancelled: SHOBS-P stays open with the series.")
                return
        finally:
            self._closing = False
        self._closing = True   # from here on the window goes; nothing asks again
        self.destroy()

    def close_series(self):
        """2.2.15 (item 7): set the open series aside (the file on disk is not changed), as a new series starts: the
        next run is a single night. Unsaved points are offered a save first."""
        if not self.observations:
            self.update_series_status()
            self.status.set("No series is open.")
            return
        if self.busy:
            messagebox.showinfo(APP_TITLE, "Wait for the current run to finish (or cancel it) before closing the series.")
            return
        if not self._offer_series_save("close it"):
            return
        had_file = bool(self.series_path)
        name = os.path.basename(self.series_path) if self.series_path else "The series"
        self.observations = []
        self._clear_series_state()
        self._take_series_snapshot()
        self.draw_output()
        self.update_series_status()
        tp = getattr(self, "transit_page", None)
        if tp is not None:
            try:
                tp.refresh()   # 2.2.15 review: its night list and any fit of the closed series go
            except Exception:
                pass
        self.log(f"Series closed: {name} (the file on disk is unchanged)." if had_file else "Series closed.")
        self.status.set("Series closed. The next run is a single night (a new series).")

    def _remember_series_dir(self, path: str):
        """2.2.3: the Save/Load series dialogs start in the folder used last time."""
        folder = os.path.dirname(os.path.abspath(path))
        if folder and self.settings.get("series_dir") != folder:
            self.settings["series_dir"] = folder
            self._save_settings()

    def save_series(self):
        if not self.observations:
            messagebox.showinfo(APP_TITLE, "Run photometry first.")
            return
        # 2.2.3: always ask where (a series spans many nights, so it does not belong in one night's output folder).
        # The dialog starts where the open series lives, or where a series was last saved or loaded, with the
        # current name filled in, so saving back to the same file is one click.
        start_dir = os.path.dirname(self.series_path) if self.series_path else ""
        if not start_dir or not os.path.isdir(start_dir):
            start_dir = self.settings.get("series_dir", "")
        name = (os.path.basename(self.series_path) if self.series_path
                else f"{(self.star_id.get() or 'variable').replace(' ', '_')}.cvseries")
        kwargs = {"title": "Save observation series", "initialfile": name, "defaultextension": ".cvseries",
                  "filetypes": [("SHOBS-P series", "*.cvseries"), ("JSON", "*.json")]}
        if start_dir and os.path.isdir(start_dir):
            kwargs["initialdir"] = start_dir
        path = filedialog.asksaveasfilename(**kwargs)
        if not path:
            return
        self._remember_series_dir(path)
        try:
            kept = self._write_series(path)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Could not save: {exc}")
            return
        self.status.set(f"Series saved" + (f" (the version before is kept as {kept})" if kept else "")
                        + f". Next night, load this file before reducing: {path}")

    def load_series(self):
        # 2.2.15 (review): an open series with unsaved points is offered a save first, as Close series does.
        if self.observations and not self._offer_series_save("load another"):
            return
        kwargs = {"title": "Load observation series",
                  "filetypes": [("SHOBS-P series", "*.cvseries"), ("JSON", "*.json"), ("All", "*.*")]}
        start_dir = self.settings.get("series_dir", "")
        if start_dir and os.path.isdir(start_dir):
            kwargs["initialdir"] = start_dir
        path = filedialog.askopenfilename(**kwargs)
        if not path:
            return
        self._remember_series_dir(path)
        self.extremum_csv = self.extremum_csv_star = ""   # 2.2.14 review: the next times CSV follows this series
        try:
            meta, observations = core.load_series(path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not read {os.path.basename(path)}:\n{exc}")
            return
        if self.observations or self.series_path:
            self._clear_series_state()   # 2.2.15 review: nothing of the series set aside goes into this one
        self._slot_typed = {}   # 2.2.16 (item 8, review): the series' names are not typed here
        self._series_gen = getattr(self, "_series_gen", 0) + 1   # 2.2.16 (item 9)
        # 2.2.16 (item 11): a series of another star never inherits this field's target, comps, check or watch stars.
        why, sure = self._other_target(meta.get("star_id"), meta.get("ra_hours"), meta.get("dec_deg"))
        if why and not sure:
            # review: names alone cannot see aliases (HIP 114622 is HD 219134): ask; No (the default) keeps the field.
            cur = self.star_id.get().strip()
            sure = messagebox.askyesno(
                APP_TITLE, f"{os.path.basename(path)} is a series of {meta.get('star_id')}; the Input page's target is "
                           f"{cur}. Without positions to compare, SHOBS-P cannot tell whether they are the same star "
                           f"(another catalog's name?).\n\nYes: they are different stars: clear {cur}'s comps, check "
                           "star and watch stars.\nNo: the same star: keep what is marked.", default="no")
            if not sure:
                self.log(f"Kept the marked stars: {meta.get('star_id')} taken as another name of {cur}.")
        kept_marked = {}
        if why and not sure:
            # 2.2.16 (round-2 review): "keep what is marked" keeps the marked slots' names and V too; the file's
            # names fill only the slots without a marker.
            for role in COMP_ROLES + ("check",):
                if getattr(self, f"{role}_xy", None) is not None:
                    kept_marked[role] = (getattr(self, f"{role}_name").get(), getattr(self, f"{role}_mag").get())
        if why and sure:
            self.clear_target(quiet=True, why=f"{os.path.basename(path)} is {why}")
        watch_before = {w.get("name") for w in self.watch_list}
        auids_before = set(self.seq_auids)
        self._apply_meta(meta)
        for role, (name, mag) in kept_marked.items():
            getattr(self, f"{role}_name").set(name)
            getattr(self, f"{role}_mag").set(mag)
        self.highlight_night = None   # 2.2.15 review: a loaded series opens unzoomed
        # 2.2.15 review: remember what the file brought, so Close series takes it away again.
        self._series_watch_names = {w.get("name") for w in self.watch_list} - watch_before
        self._series_auid_keys = set(self.seq_auids) - auids_before
        if self.series_mode != self.mode_key and self.series_mode in MODES:
            self.set_mode(self.series_mode, initial=True)
            self.log(f"Switched to {MODES[self.series_mode]['name']} mode to match {os.path.basename(path)}.")
        self._fill_base(observations, meta)
        self._take_series_snapshot()   # 2.2.14 review 2: again, now that a pre-1.8 series' comps are filled in
        self.observations = observations
        if getattr(self, "transit_page", None) is not None and self.transit_page.report is not None:
            self.transit_page.close_report(quiet=True)   # 2.2.7: a loaded series replaces a loaded report
        self.legacy_mixed = self._legacy_nights(meta, observations)
        self.series_path = path
        # 2.2.14 (item 17): the next save of this file keeps it, as loaded now, as its _prev copy.
        self._prev_kept.discard(os.path.normcase(os.path.abspath(path)))
        self._refresh_period()
        self._series_saved_fp = self._series_fingerprint()   # 2.2.15 (item 7): the series as on disk
        self.draw_output()
        self._warn_legacy(self.legacy_mixed, os.path.basename(path))
        nights = sorted({obs.night for obs in observations})
        self.status.set(f"Loaded {len(observations)} points from {len(nights)} night(s). Reduce the new night, then choose Add.")
        messagebox.showinfo(
            APP_TITLE,
            f"Loaded {len(observations)} points, {len(nights)} night(s).\n\n"
            "Point Input at the new night's bias, dark, flat, and lights, run the flow, and choose Yes when asked to add the night.\n\n"
            "The star positions are not stored, so mark the target and comps again on the new night's frame.",
        )
        # 2.2.15 (item 1, review): nights without a planet-transit check get one now (one lookup in the background,
        # one question for all nights with frames in a transit; silent otherwise).
        unchecked = [n for n in nights if not self._night_info(n).get("planet_transits")]
        if unchecked:
            self._check_planet_transits(unchecked, source="loaded")   # 2.2.16 (item 9): names the file

    def _page_output(self):
        page = ttk.Frame(self.pages)
        card = ttk.Frame(page, style="Card.TFrame", padding=12)
        card.pack(fill=BOTH, expand=True)
        ttk.Label(card, text="Output – Variability", font=(core.UI_FONT, 16, "bold"), style="Card.TLabel").pack(anchor=W)
        self._build_summary(card)
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill=X)
        ttk.Label(row, text="Period search (days)", style="Card.TLabel").pack(side=LEFT)
        self.period_entries = []
        for k, var in enumerate((self.min_period, self.max_period)):
            if k:
                ttk.Label(row, text="to", style="Card.TLabel").pack(side=LEFT)
            entry = ttk.Entry(row, textvariable=var, width=6)
            entry.pack(side=LEFT, padx=4)
            # 2.2.18 (item 16): Enter refits; leaving the box refits only when the range changed.
            entry.bind("<Return>", lambda _e: self._period_box_changed(force=True))
            entry.bind("<KP_Enter>", lambda _e: self._period_box_changed(force=True))
            entry.bind("<FocusOut>", lambda _e: self._period_box_changed())
            self.period_entries.append(entry)
        ttk.Checkbutton(
            row, text="Line up segments (nights, meridian flip)",
            variable=self.norm_segments, command=self.refit_period,
        ).pack(side=LEFT, padx=(14, 4))
        ttk.Label(row, text="Time axis", style="Card.TLabel").pack(side=LEFT, padx=(14, 4))
        axis_box = ttk.Combobox(row, textvariable=self.time_axis, values=("JD", "UTC", "Local time"), width=10, state="readonly")
        axis_box.pack(side=LEFT)
        axis_box.bind("<<ComboboxSelected>>", lambda _e: self.draw_output())
        ttk.Label(row, text="Notes", style="Card.TLabel").pack(side=LEFT, padx=(14, 4))
        ttk.Entry(row, textvariable=self.notes, width=42).pack(side=LEFT, fill=X, expand=True)

        qc = ttk.Frame(card, style="Card.TFrame")
        qc.pack(fill=X, pady=(4, 0))
        ttk.Checkbutton(
            qc, text="Leave flagged points (sat, drift, cloud) out of the period and report (planet-transit frames "
                     "never go in a report)",
            variable=self.skip_flagged, command=self.refit_period,
        ).pack(side=LEFT)
        ttk.Label(qc, text="Cloud flag: comp light more than", style="Card.TLabel").pack(side=LEFT, padx=(14, 4))
        ttk.Entry(qc, textvariable=self.cloud_limit, width=5).pack(side=LEFT)
        ttk.Label(qc, text="% below the night's median", style="Card.TLabel").pack(side=LEFT, padx=(4, 6))
        ttk.Button(qc, text="Apply", command=self.refit_period).pack(side=LEFT)
        # 2.2.14 (item 18): keep or leave out a night's transmission-loss (dew/haze) points.
        ttk.Button(qc, text="Transmission loss…", command=self.edit_transmission).pack(side=LEFT, padx=(6, 0))
        # 2.2.15 (item 1): leave out or keep a night's frames taken during a planet's predicted transit.
        ttk.Button(qc, text="Planet transits…", command=self.edit_planet_transits).pack(side=LEFT, padx=(6, 0))

        tools = ttk.Frame(card, style="Card.TFrame")
        tools.pack(fill=X, pady=(4, 0))
        ttk.Checkbutton(
            tools, text="Check-star correction (fold and period)", variable=self.use_check_zp, command=self.refit_period,
        ).pack(side=LEFT)
        ttk.Checkbutton(
            tools, text="Line up flip only (keep nights)", variable=self.align_flip, command=self.refit_period,
        ).pack(side=LEFT, padx=(14, 0))
        # 2.2.14 (item 20): each pier side (camera angle) normalised on its own across the campaign.
        ttk.Checkbutton(
            tools, text="Pier side as segments", variable=self.pier_segments, command=self.refit_period,
        ).pack(side=LEFT, padx=(14, 0))
        ttk.Label(tools, text="Bin", style="Card.TLabel").pack(side=LEFT, padx=(14, 4))
        bin_box = ttk.Combobox(tools, textvariable=self.bin_choice, values=BIN_CHOICES, width=10, state="readonly")
        bin_box.pack(side=LEFT)
        bin_box.bind("<<ComboboxSelected>>", lambda _e: self.draw_output())
        ttk.Label(tools, text="Mark periods (days, comma separated)", style="Card.TLabel").pack(side=LEFT, padx=(14, 4))
        ttk.Entry(tools, textvariable=self.mark_periods, width=26).pack(side=LEFT)
        ttk.Button(tools, text="Show", command=self.draw_output).pack(side=LEFT, padx=(4, 0))
        ttk.Label(tools, text="Period from", style="Card.TLabel").pack(side=LEFT, padx=(14, 4))
        pbin_box = ttk.Combobox(tools, textvariable=self.period_bins, values=PERIOD_BIN_CHOICES, width=12, state="readonly")
        pbin_box.pack(side=LEFT)
        pbin_box.bind("<<ComboboxSelected>>", lambda _e: self.refit_period())
        # 2.2.17 (item 10; review: its own line, so the period-search row keeps its width on a high-DPI screen):
        # each night's mean with the airmass trend taken out, on the plot only.
        view = ttk.Frame(card, style="Card.TFrame")
        view.pack(fill=X, pady=(2, 0))
        ttk.Checkbutton(view, text="Airmass-corrected night means (plot only, never in reports)",
                        variable=self.airmass_view, command=self.draw_output).pack(side=LEFT)
        host = ttk.Frame(card, style="Card.TFrame")
        host.pack(fill=BOTH, expand=True, pady=6)
        if Figure is None:
            self.out_canvas = None
        else:
            self.out_fig = Figure(figsize=(8, 5.2), dpi=100, facecolor="#ffffff")
            self.out_ax1 = self.out_fig.add_subplot(221)
            self.out_ax2 = self.out_fig.add_subplot(222)
            self.out_ax3 = self.out_fig.add_subplot(223)
            self.out_ax4 = self.out_fig.add_subplot(224)
            self.out_canvas = FigureCanvasTkAgg(self.out_fig, master=host)
            self.out_canvas.get_tk_widget().configure(width=400, height=300)
            self.out_canvas.get_tk_widget().pack(fill=BOTH, expand=True)
            self.out_canvas.mpl_connect("motion_notify_event", self._on_hover)
            self.out_canvas.mpl_connect("resize_event", self._refit_lc_title)   # 2.2.14 (item 16)
        nav = ttk.Frame(card, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", before=host, pady=(4, 0))
        series_row = ttk.Frame(card, style="Card.TFrame")
        series_row.pack(fill=X, side="bottom", before=nav, pady=(6, 0))
        ttk.Label(series_row, text="Series", style="Hint.TLabel", width=8).pack(side=LEFT)
        ttk.Button(series_row, text="Save series", command=self.save_series).pack(side=LEFT)
        load_out = ttk.Button(series_row, text="Load series", command=self.load_series)
        load_out.pack(side=LEFT, padx=(6, 0))
        self._add_tooltip(load_out, self.LOAD_SERIES_TIP)   # 2.2.16 (item 12)
        # 2.2.16 (item 15): Close series here too (same as the Input page's), enabled only with a series open.
        self.close_series_button_out = ttk.Button(series_row, text="Close series", command=self.close_series)
        self.close_series_button_out.pack(side=LEFT, padx=6)
        self.update_series_status()
        ttk.Button(series_row, text="Add series file…", command=self.add_series_file).pack(side=LEFT)
        ttk.Button(series_row, text="Remove night…", command=self.remove_night).pack(side=LEFT, padx=6)
        ttk.Button(series_row, text="Night notes…", command=self.edit_night_note).pack(side=LEFT)
        ttk.Button(series_row, text="Comp health…", command=self.show_comp_health).pack(side=LEFT, padx=6)
        ttk.Button(series_row, text="Use comps…", command=self.choose_comps).pack(side=LEFT)
        ttk.Button(series_row, text="Watch stars…", command=self.show_watch).pack(side=LEFT, padx=6)
        # 2.2.14 (item 13): time of maximum / minimum with O-C, saved to a CSV that grows into an O-C diagram.
        ttk.Button(series_row, text="Time of max/min…", command=self.show_extremum).pack(side=LEFT)
        ttk.Label(nav, text="Results", style="Hint.TLabel", width=8).pack(side=LEFT)
        ttk.Button(nav, text="Save AAVSO report", style="Accent.TButton", command=self.save_aavso).pack(side=LEFT)
        # 2.2.15 (item 8): AAVSO's upload page for the saved report.
        ttk.Button(nav, text="Open AAVSO WebObs…", command=self.open_webobs).pack(side=LEFT, padx=(6, 0))
        ttk.Button(nav, text="Save light-curve CSV", command=self.save_csv).pack(side=LEFT, padx=6)
        ttk.Button(nav, text="Export…", command=self.open_export).pack(side=LEFT)
        ttk.Button(nav, text="Save plot PNG", command=self.save_png).pack(side=LEFT, padx=6)
        ttk.Button(nav, text="Refit period", command=self.refit_period).pack(side=LEFT)
        self.output_back = ttk.Button(nav, text="Back", command=lambda: self.show_step(STEP_PHOTO))
        self.output_back.pack(side=RIGHT)
        # Transits mode reaches this page from the transit fit (light-curve details); this takes you back.
        self.back_to_transit = ttk.Button(nav, text="◀  Transit View", style="Accent.TButton",
                                          command=lambda: self.show_step(STEP_OUTPUT))
        return page

    def transit_page_on_screen(self) -> bool:
        """2.2.17 (item 4): the Transit fit page is the page shown (Transits mode, Output step, not Variable View)."""
        return (self.mode_key == "transits" and getattr(self, "current_step", None) == STEP_OUTPUT
                and not getattr(self, "_on_credits", False) and not getattr(self, "_variable_view", False))

    def show_variables_view(self):
        """Transits mode: the light-curve details page (the Variables Output page) for the open series."""
        self._variable_view = True   # 2.2.17 (item 4)
        self.page_frames[STEP_OUTPUT].lift()
        self.back_to_transit.pack(side=RIGHT, padx=(8, 0), before=self.output_back)
        self.draw_output()
        self.status.set("Variable View: light-curve details, comps, and check star. ◀ Transit View (bottom right) "
                        "returns to the fit.")

    def _color_summary(self, points) -> str:
        def stat(values):
            v = np.array([x for x in values if math.isfinite(x)])
            if v.size == 0:
                return None
            return float(np.median(v)), float(1.4826 * np.median(np.abs(v - np.median(v))) / max(1.0, math.sqrt(v.size)))

        parts = []
        untransformed = False
        for key, label in (("bv", "B-V"), ("vr", "V-R")):
            vals = [o for o in points if math.isfinite(getattr(o, key))]
            st = stat(getattr(o, key) for o in vals)
            if not st:
                continue
            methods = [core.color_method(o, key) for o in vals]
            n_cal = sum(1 for m in methods if m)
            field_nights = {o.night for o, m in zip(vals, methods) if m == "field"}
            fits = [((self.color_cal.get(n) or {}).get("fits") or {}).get(key) for n in field_nights]
            fits = [f for f in fits if core.color_fit_usable(f)]   # 2.2.16 (item 3 review)
            # Calibration uncertainty: the fit scatter over the number of stars that set the line.
            sys_err = float(np.median([f["scatter"] / math.sqrt(f["n"]) for f in fits])) if fits else 0.0
            text = f"{label} {st[0]:.3f} ± {math.hypot(st[1], sys_err):.3f}"
            if key == "bv":
                temp = core.bv_temperature(st[0])
                if math.isfinite(temp):
                    text += f", about {temp:,.0f} K, {core.bv_spectral_class(st[0])}-type"
            per_night = {}
            for o in vals:
                per_night.setdefault(o.night, []).append(getattr(o, key))
            if len(per_night) > 1:
                text += " [per night: " + ", ".join(
                    f"{(n[-4:] if n.startswith('JD') else n)} {float(np.median(v)):.3f}"
                    for n, v in sorted(per_night.items())) + "]"
            if n_cal == len(vals):
                if fits:
                    text += f" (calibrated with field stars, color term {np.median([f['k'] for f in fits]):.2f})"
                else:
                    text += " (calibrated with the saved color term)"
            elif n_cal:
                text += f" (calibrated on {n_cal} of {len(vals)} points)"
                untransformed = True
            else:
                text += " (untransformed)"
                untransformed = True
            # 2.2.14 (item 1): which catalogs the catalog colors came from (each star's two bands from one catalog).
            cats = []
            for n in sorted(per_night):
                c = (((self.color_cal.get(n) or {}).get("catalogs") or {}).get(key) or {})
                for part in ((f"field {c['field']}" if c.get("field") else ""), (f"comps {c['comps']}" if c.get("comps") else "")):
                    if part and part not in cats:
                        cats.append(part)
            if cats:
                text += " [catalog colors: " + "; ".join(cats) + "]"
            parts.append(text)
        if parts:
            tail = (" Untransformed colors are approximate: tick 'Also measure R and B' (with the image solved) "
                    "to calibrate them." if untransformed else "")
            return "  Color: " + "; ".join(parts) + "." + tail
        dbv = stat(o.bv_diff for o in points)
        dvr = stat(o.vr_diff for o in points)
        if dbv or dvr:
            rel = []
            if dbv:
                rel.append(f"B-V {dbv[0]:+.3f}")
            if dvr:
                rel.append(f"V-R {dvr[0]:+.3f}")
            return ("  Color relative to the comps: " + ", ".join(rel)
                    + " (comp catalog colors unknown; comps with catalog B and V give real B-V).")
        return ""

    def _record_color_calibration(self, night: str, info: dict):
        """Log tonight's color fit, keep it with the series, and save a good color term for later nights."""
        fits = info.get("fits") or {}
        used = info.get("used") or {}
        names = {"bv": "B-V", "vr": "V-R"}
        if fits:
            self.log(f"Color calibration from labeled field stars ({info.get('frames', 0)} frames sampled):")
            for key, *_ in core.COLOR_INDICES:
                if key in fits:
                    self.log("  " + core.color_fit_text(names[key], fits[key]))
        for key, *_ in core.COLOR_INDICES:
            method = used.get(key, "")
            if method == "field":
                text = "calibrated with tonight's field stars"
            elif method == "saved":
                saved = info.get("saved") or {}
                text = f"calibrated with the color term saved {str(saved.get('date', ''))[:10]}"
            else:
                text ="not calibrated (comps' catalog color plus the raw channel difference, or relative to the comps)"
            cats = (info.get("catalogs") or {}).get(key) or {}
            if cats.get("field") or cats.get("comps"):
                # 2.2.14 (item 1): each star's color from one catalog; say which.
                text += " — catalog colors: " + "; ".join(
                    part for part in ((f"field stars {cats['field']}" if cats.get("field") else ""),
                                      (f"comps {cats['comps']}" if cats.get("comps") else "")) if part)
            self.log(f"  {names[key]}: {text}")
        self.color_cal[night] = {"fits": fits, "used": used}
        if info.get("catalogs"):
            self.color_cal[night]["catalogs"] = info["catalogs"]
        good = {k: f for k, f in fits.items() if core.color_fit_usable(f)}
        if good:
            saved = core.load_color_calibration() or {}
            saved.update(good)
            saved.update({
                "date": core.jd_to_datetime_utc(float(night[2:])).strftime("%Y-%m-%d") if night[2:].isdigit() else "",
                "night": night, "star": self.star_id.get(), "camera": str((self.preview_header or {}).get("INSTRUME", "")),
                "pattern": self.pattern.get(), "binning": int(self.binning.get() or 1), "app_version": APP_VERSION,
            })
            try:
                core.save_color_calibration(saved)
                self.log(f"  Saved the color term for nights without enough labeled stars ({core.COLOR_CAL_FILE}).")
            except OSError as exc:
                self.log(f"  Could not save the color term: {exc}")

    @staticmethod
    def _version_tuple(text) -> tuple:
        try:
            return tuple(int(x) for x in str(text).split(".")[:2])
        except ValueError:
            return (0, 0)

    def _legacy_nights(self, meta: dict, observations: list) -> set:
        """Nights reduced before 1.7 with app binning, which mixed the Bayer colors."""
        nights = set(meta.get("legacy_mixed_nights") or [])
        try:
            binning = int(meta.get("binning") or 1)
        except (TypeError, ValueError):
            binning = 1
        if binning > 1 and self._version_tuple(meta.get("app_version") or "0") < (1, 7):
            known = set((meta.get("night_binning") or {}).keys())
            nights |= {o.night for o in observations if o.night not in known}
        return nights

    def _warn_legacy(self, nights: set, source: str):
        if not nights:
            return
        messagebox.showwarning(
            APP_TITLE,
            f"{source} has night(s) reduced with app binning before version 1.7:\n  {', '.join(sorted(nights))}\n\n"
            "Those versions binned the raw color frame before the debayer, which averaged red, green, and blue "
            "pixels together. Their TG/TR/TB points are really a clear-like blend, and their colors (B-V, V-R) are "
            "not real. The light-curve shape is still usable for a look, but re-reduce those nights in 1.7 "
            "(and use Remove night...) before reporting them.",
        )

    @staticmethod
    def title_key_text(name: str, key: list, fits) -> str:
        """2.2.14 (item 16): "Name (× flagged, left out · ○ after flip · ◆ night mean)" when fits(text) says it fits
        on one line, else the name over the key, the key wrapped between its items."""
        if not key:
            return name
        one = f"{name} ({' · '.join(key)})"
        if fits(one):
            return one
        lines, line = [name], ""
        for item in key:
            nxt = f"{line} · {item}" if line else item
            if line and not fits(nxt):
                lines.append(line)
                nxt = item
            line = nxt
        lines.append(line)
        return "\n".join(lines)

    def _title_with_key(self, ax, name: str, key: list):
        """2.2.14 (item 16): set the light-curve title with its symbol key, wrapped to the axes' current width
        (measured with the figure's renderer, so it follows the screen's scaling and the PNG's size)."""
        fig = ax.figure
        try:
            renderer = fig.canvas.get_renderer()
            limit = 0.97 * ax.get_window_extent(renderer).width

            def fits(text):
                ax.set_title(text)
                return ax.title.get_window_extent(renderer).width <= limit
        except Exception:
            width_pt = ax.get_position().width * fig.get_figwidth() * 72.0

            def fits(text):
                return len(text) * 0.6 * 12 <= width_pt     # about 0.6 em per character
        text = self.title_key_text(name, key, fits)
        ax.set_title(text)
        self._lc_title = (ax, name, list(key), text.count("\n"))

    def _refit_lc_title(self, _event=None):
        """2.2.14 (item 16): the window was resized: re-wrap the title key; re-run the layout if its lines changed."""
        held = getattr(self, "_lc_title", None)
        if not held or self.out_canvas is None:
            return
        ax, name, key, lines = held
        # 2.2.14 review: the layout moves the axes, so wrap, lay out, and measure once more (at most two passes).
        for _pass in range(2):
            self._title_with_key(ax, name, key)
            if self._lc_title[3] == lines and _pass:
                break
            lines = self._lc_title[3]
            try:
                self.out_fig.tight_layout()
            except Exception:
                break

    def _jd_offset(self) -> int:
        """2.2.14 (item 9): the whole JD subtracted on a JD time axis (the series' first day), 0 without points."""
        jds = [o.jd for o in self.observations if math.isfinite(o.jd)] if self.observations else []
        return int(math.floor(min(jds))) if jds else 0

    def _time_values(self, jd):
        """x values for the light-curve plots: JD, or matplotlib dates in UTC or local time."""
        choice = self.time_axis.get()
        if choice == "JD":
            # 2.2.14 (item 9): JD minus a whole-day offset, named in the axis label, instead of matplotlib's
            # "+2.461326e6" corner offset.
            return np.asarray(jd, dtype=float) - self._jd_offset()
        from matplotlib.dates import date2num

        stamps = [core.jd_to_datetime_utc(j) for j in jd]
        if choice == "Local time":
            stamps = [t.astimezone() for t in stamps]
        # date2num works on naive datetimes; strip the zone after converting.
        return np.array(date2num([t.replace(tzinfo=None) for t in stamps]))

    @staticmethod
    def _fit_ylim(ax, y, err, use, invert: bool = True) -> bool:
        """2.2.16 (item 13): y-limits from the used points with their error bars and a margin (8 %, at least
        0.005 mag), never from flagged or left-out points. False (limits untouched) without a used point."""
        y = np.asarray(y, dtype=float)
        use = np.asarray(use, dtype=bool) & np.isfinite(y)
        if not use.any():
            return False
        e = np.zeros(int(use.sum()))
        if err is not None:
            e = np.asarray(err, dtype=float)[use]
            e = np.where(np.isfinite(e), np.abs(e), 0.0)
        lo, hi = float(np.min(y[use] - e)), float(np.max(y[use] + e))
        pad = max((hi - lo) * 0.08, 0.005)
        ax.set_ylim((hi + pad, lo - pad) if invert else (lo - pad, hi + pad))
        return True

    @staticmethod
    def _pin_off_scale(ax, x, y, mask, alpha: float = 1.0) -> int:
        """2.2.16 (item 13): points in mask that fall outside the y-limits (but inside the x-limits) drawn pinned at
        the plot's edge as small amber × (not clipped), so they show without flattening the curve. Returns how many."""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        lo, hi = sorted(float(v) for v in ax.get_ylim())
        xlo, xhi = sorted(float(v) for v in ax.get_xlim())
        sel = (np.asarray(mask, dtype=bool) & np.isfinite(x) & np.isfinite(y) & (x >= xlo) & (x <= xhi)
               & ((y < lo) | (y > hi)))
        if sel.any():
            inset = (hi - lo) * 0.01   # 2.2.16 (review): just inside the frame, so clip_on=False never spills out
            ax.plot(x[sel], np.clip(y[sel], lo + inset, hi - inset), "x", ms=4, mew=1.1, color="#c26a00", alpha=alpha,
                    clip_on=False, zorder=5)
        return int(sel.sum())

    def _zoom_axes(self, ax, obs_list, x, mag, err, excluded, night: str, t0: float):
        """2.2.15 (item 6): the light curve zoomed to one night: x in hours from the night's first point (its local
        start time in the label), y fitted to the night's points with their error bars (flagged ones too when the
        night has no other)."""
        sel = np.array([o.night == night for o in obs_list]) & np.isfinite(mag) & np.isfinite(x)
        use = sel & ~excluded if (sel & ~excluded).any() else sel
        if use.any():
            e = np.where(np.isfinite(err[use]), err[use], 0.0)
            lo, hi = float(np.min(mag[use] - e)), float(np.max(mag[use] + e))
            pad = max((hi - lo) * 0.08, 0.005)
            ax.set_ylim(hi + pad, lo - pad)   # magnitudes: bright at the top
            xs = x[sel]
            span = float(xs.max() - xs.min())
            xpad = max(span * 0.03, 0.05)
            ax.set_xlim(float(xs.min()) - xpad, float(xs.max()) + xpad)
        try:
            ax.ticklabel_format(axis="x", useOffset=False, style="plain")
        except AttributeError:
            pass
        try:
            local = core.jd_to_datetime_utc(t0).astimezone()
            ax.set_xlabel(f"Hours from the night's first point ({local:%H:%M} local, {local:%b} {local.day})")
        except (OverflowError, ValueError, OSError):
            ax.set_xlabel("Hours from the night's first point")

    def _format_time_axis(self, ax):
        choice = self.time_axis.get()
        if choice == "JD":
            off = self._jd_offset()   # 2.2.14 (item 9)
            try:
                ax.ticklabel_format(axis="x", useOffset=False, style="plain")
            except AttributeError:
                pass   # not a plain number axis
            ax.set_xlabel(f"JD − {off}" if off else "Julian Date")
            return
        from matplotlib.dates import AutoDateLocator, ConciseDateFormatter

        locator = AutoDateLocator()
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(ConciseDateFormatter(locator))
        if choice == "UTC":
            ax.set_xlabel("UTC")
        else:
            zone = core.jd_to_datetime_utc(self.observations[0].jd).astimezone().tzname() if self.observations else ""
            ax.set_xlabel(f"Local time ({zone})")

    def draw_output(self):
        self.update_series_status()   # 2.2.15 (item 7): every change to the series ends in a redraw
        if self.out_canvas is None:
            return
        for ax in (self.out_ax1, self.out_ax2, self.out_ax3, self.out_ax4):
            ax.clear()
        self._hover_sets = []
        self._hover_annots = {}
        self._hover_key = None
        self._lc_title = None   # 2.2.14 (item 16)
        if not self.observations:
            tp = getattr(self, "transit_page", None)
            if self.mode_key == "transits" and tp is not None and getattr(tp, "report", None) is not None:
                # 2.2.12 (John's wording): a report alone has no SHOBS-P photometry to show here.
                self._fill_summary(["Variable View not available from an EXOTIC report."], ["—"], ["—"], [], [])
                for ax in (self.out_ax1, self.out_ax2, self.out_ax3, self.out_ax4):
                    ax.set_xticks([])
                    ax.set_yticks([])
                    ax.grid(False)
                self.out_ax1.text(0.5, 0.56, "Variable View not available from an EXOTIC report.",
                                  transform=self.out_ax1.transAxes, ha="center", va="center", fontsize=12,
                                  color="#33424d", fontweight="bold")
                self.out_ax1.text(0.5, 0.40, "It shows SHOBS-P's own photometry; the report has only its final "
                                             "light curve.", transform=self.out_ax1.transAxes, ha="center",
                                  va="center", fontsize=10, color="#5d6d76", wrap=True)
                self.out_canvas.draw_idle()
                return
            self._fill_summary(["No series yet: run photometry", "or Load series."], ["—"], ["—"], [], [])
            self.out_canvas.draw_idle()
            return
        obs_list = self.observations
        jd = np.array([o.jd for o in obs_list])
        mag = np.array([o.mag for o in obs_list])
        scint = self._scint_setup()
        err = np.array([core.point_err(o, scint) for o in obs_list])
        flagged = np.array([bool(o.flag) for o in obs_list])
        tx = self._time_values(jd)
        not_finite = ~np.isfinite(mag)
        excluded = (flagged | not_finite) if self.skip_flagged.get() else not_finite
        # Each night's meridian-flip step, measured on the plotted magnitudes (always reported).
        flip_steps = core.flip_offsets(obs_list, list(mag), use=list(~excluded))
        # 2.2.14 (item 20): pier side of every point, and how the sides compare across the campaign.
        pier_by_night = self._pier_by_night()
        sides = core.point_sides(obs_list, pier_by_night)
        side_offs = core.side_offsets(obs_list, list(mag), sides, use=list(~excluded))
        if self.norm_segments.get():
            mag = np.array(core.normalize_segments(obs_list))
        elif self.pier_segments.get():
            mag = np.array(core.apply_side_offsets(sides, list(mag), side_offs["offsets"]))
        elif self.align_flip.get():
            mag = np.array(core.align_flips(obs_list, list(mag), flip_steps))
        nights = []
        for obs in obs_list:
            if obs.night not in nights:
                nights.append(obs.night)
        colors = ["#0067c0", "#c43b3b", "#2e7d32", "#b86e00", "#6b4c9a", "#00838f", "#5d4037"]
        filt = "/".join(sorted({o.filt for o in obs_list})) or "CV"

        highlight = self.highlight_night if self.highlight_night in nights else None

        def alpha_for(night, base):
            """A night picked in the summary table stays bright; the others fade."""
            return base if highlight is None or night == highlight else base * 0.12

        ax = self.out_ax1
        interval = self._bin_interval()
        raw_alpha = 0.25 if interval is not False else 1.0
        # 2.2.15 (item 6): a night clicked in a series zooms the light curve to it (hours from its first point, the
        # y-axis fitted to its points); clicking it again shows the whole series. The other nights stay drawn (faded)
        # on the same hours scale, off to the sides. 2.2.16 (item 14): the check plot follows the zoom.
        zoom = highlight if len(nights) > 1 else None
        zoom_jd = [o.jd for o in obs_list if o.night == zoom and math.isfinite(o.jd)] if zoom is not None else []
        if zoom is not None and not zoom_jd:
            zoom = None   # 2.2.15 review: a night without a usable time cannot be zoomed
        zoom_t0 = min(zoom_jd) if zoom_jd else 0.0
        lc_x = (jd - zoom_t0) * 24.0 if zoom is not None else tx
        in_zoom = np.array([zoom is None or o.night == zoom for o in obs_list], dtype=bool)
        for i, night in enumerate(nights):
            mask = np.array([obs.night == night for obs in obs_list])
            keep = mask & ~excluded
            ax.errorbar(
                lc_x[keep], mag[keep], yerr=err[keep], fmt="o", ms=4, alpha=alpha_for(night, raw_alpha),
                color=colors[i % len(colors)], ecolor="#b0b0b0", lw=0.8,
            )   # 2.2.14 (item 16): no legend; the time axis and the night table name the nights
            drop = mask & excluded
            if drop.any():
                ax.plot(lc_x[drop], mag[drop], "x", ms=6, color="#888888", alpha=alpha_for(night, 1.0))
        ax.invert_yaxis()
        if zoom is not None:
            self._zoom_axes(ax, obs_list, lc_x, mag, err, excluded, zoom, zoom_t0)
        else:
            self._format_time_axis(ax)
        filt_label = "/".join(f"Sloan {core.band_label(f)}" if core.is_sloan(f) else f
                              for f in filt.split("/"))   # 2.2.17 (item 1): "Sloan i′ magnitude", not "SI"
        ax.set_ylabel(f"{filt_label} magnitude" + (" (segments lined up)" if self.norm_segments.get()
                                                   else " (pier sides lined up)" if self.pier_segments.get()   # 2.2.14
                                                   else " (flip lined up)" if self.align_flip.get() and flip_steps
                                                   else ""))
        labels_raw = [self._point_label(o, float(m)) for o, m in zip(obs_list, mag)]
        self._hover_sets.append((ax, lc_x[in_zoom], mag[in_zoom], [t for t, z in zip(labels_raw, in_zoom) if z], 0.0))
        flip_mask = np.array([o.segment == "flip" for o in obs_list]) & ~excluded
        if flip_mask.any():
            for night in nights:   # 2.2.14 (item 16): rings fade with their night when another night is clicked
                fm = flip_mask & np.array([o.night == night for o in obs_list])
                if fm.any():
                    ax.plot(lc_x[fm], mag[fm], "o", ms=6, mfc="none", mec="#333333", mew=0.6, alpha=alpha_for(night, 1.0))
        # 2.2.14 (item 16): a symbol key in the title, only for the symbols drawn (8 nights overflowed a legend).
        # 2.2.14 review: only drawn × count (a NaN point is left out but not plotted).
        key = ((["× flagged, left out"] if (excluded & np.isfinite(mag) & in_zoom).any() else [])
               + (["○ after flip"] if (flip_mask & in_zoom).any() else []))
        ax.grid(True, alpha=0.3)
        bin_text = ""
        if interval is not False:
            kept = [k for k in range(len(obs_list)) if not excluded[k]]
            bins = core.bin_points([obs_list[k] for k in kept], [float(mag[k]) for k in kept], interval,
                                   self._scint_setup())
            if bins:
                bx = ((np.array([b["jd"] for b in bins]) - zoom_t0) * 24.0 if zoom is not None
                      else self._time_values(np.array([b["jd"] for b in bins])))
                # 2.2.15 (item 3): the means fade with their night when another night is clicked, like the points.
                for night in nights:
                    sel = [k for k, b in enumerate(bins) if b["night"] == night]
                    if sel:
                        ax.errorbar(bx[sel], [bins[k]["value"] for k in sel], yerr=[bins[k]["err"] for k in sel],
                                    fmt="D", ms=6, color="#111111", ecolor="#111111", elinewidth=1.4, capsize=3,
                                    alpha=alpha_for(night, 1.0))
                key.append("◆ night mean" if interval is None else f"◆ {self.bin_choice.get()} mean")
                self._hover_sets.append((ax, bx, np.array([b["value"] for b in bins]),
                                         [self._bin_label(b) for b in bins], 4.0))
                errs = np.array([b["err"] for b in bins])
                bin_text = (f"  Binned ({self.bin_choice.get()}): {len(bins)} points, typical error "
                            f"{np.median(errs):.4f} mag, spread of binned points {np.std([b['value'] for b in bins]):.4f} mag.")
        # 2.2.16 (item 13): the y-axis from the used points (zoomed: _zoom_axes did that night); flagged points beyond
        # it are pinned at the edge as small × and counted in the title key (12-13 Oct's cloud points from 4.9 to
        # 6.0 flattened all nine nights).
        if zoom is None:
            self._fit_ylim(ax, mag, err, ~excluded)
        off_scale = 0
        for night in nights:
            nm = np.array([o.night == night for o in obs_list], dtype=bool)
            off_scale += self._pin_off_scale(ax, lc_x, mag, excluded & nm & in_zoom, alpha_for(night, 1.0))
        if off_scale:
            key.append(f"{off_scale} flagged off scale")
        self._title_with_key(ax, (self.star_id.get() or "Target")
                             + (f" · {self.night_label(zoom, short=True)}" if zoom is not None else ""), key)

        usable = [o for o, ex in zip(obs_list, excluded) if not ex]
        self._draw_airmass_corrected(self.out_ax1, usable, zoom)   # 2.2.17 (item 10): display only
        ax = self.out_ax2
        cautions = []
        old_model = core.error_model_text(obs_list, lambda n: self.night_label(n, short=True))   # 2.2.17 (item 8)
        if old_model:
            cautions.append(old_model)
        period_lines = []
        # 2.2: one short night cannot pin a period down; show the fold and periodogram greyed, with a note.
        span_used = (max(o.jd for o in usable) - min(o.jd for o in usable)) if usable else 0.0
        too_short = bool(self.period) and len(nights) == 1 and span_used < 2.0 * self.period["period_days"]
        self._period_too_short = too_short
        if self.period and usable:
            p = self.period["period_days"]
            epoch = self.period["epoch_jd"]
            u_jd = np.array([o.jd for o in usable])
            corrected = np.array(self._analysis_values(usable))
            phase = np.mod(u_jd - epoch, p) / p
            for i, night in enumerate(nights):
                mask = np.array([o.night == night for o in usable])
                a_main = alpha_for(night, 0.25 if too_short else 1.0)
                colour = "#9aa7b0" if too_short else colors[i % len(colors)]
                # 2.2.14 (item 16): with a night clicked, it keeps its colour on top; the others fade to grey.
                faded = highlight is not None and night != highlight
                if faded:
                    colour, a_main = "#b9c2c9", 0.35
                z = 3 if highlight is not None and night == highlight else 2
                ax.plot(phase[mask], corrected[mask], "o", ms=4, color=colour, alpha=a_main, zorder=z)
                ax.plot(phase[mask] + 1, corrected[mask], "o", ms=4, color=colour, alpha=0.35 * a_main, zorder=z)
            if too_short:
                ax.text(0.5, 0.5, f"One night of {span_used * 24:.1f} h covers under two cycles\nof the best period "
                                  f"({p * 24:.1f} h): the fold is not meaningful yet.\nAdd nights.",
                        transform=ax.transAxes, ha="center", va="center", fontsize=9, color="#5d6d76",
                        bbox=dict(boxstyle="round", fc="#ffffff", ec="#c9d6de", alpha=0.9))
            fold_labels = [f"{o.night}  phase {ph:.3f}\n{v:.4f}  {self._time_text(o.jd)}"
                           for o, ph, v in zip(usable, phase, corrected)]
            self._hover_sets.append((ax, np.concatenate([phase, phase + 1]), np.concatenate([corrected, corrected]),
                                     fold_labels + fold_labels, 0.0))
            ax.invert_yaxis()
            ax.set_xlim(0, 2)
            ax.set_xlabel("Phase")
            ax.set_ylabel("Zero-pointed mag")
            alias = 1.0 / abs(1.0 / p - 1.0) if p > 0 and abs(1.0 / p - 1.0) > 1e-6 else float("nan")
            fap = self.period.get("fap", float("nan"))
            baseline = self.period.get("baseline_days", float("nan"))
            # 2.2.8: say plainly when no period is significant (FAP above 1%, or one night carries it), so a best
            # peak is never read as the star's period (HD 219134: 'periods' from night offsets and day aliases).
            carried_now = self.period.get("carried_by") or []
            # 2.2.17 (item 10): the FAP counting nights must agree, and a period from the nights' airmass is demoted.
            night_fap = (self.period.get("night_fap") or {}).get("fap", float("nan"))
            nights_say_no = math.isfinite(night_fap) and night_fap > 0.01
            am_check = self.period.get("airmass_check") or {}
            am_trend = self.period.get("airmass_trend") or {}
            significant = (math.isfinite(fap) and fap <= 0.01 and not carried_now and not too_short
                           and not nights_say_no and not am_check.get("demoted"))
            ax.set_title(f"Folded  P = {p:.5f} d" if significant else f"Best peak (not significant)  P = {p:.5f} d")
            ax.grid(True, alpha=0.3)
            period_lines = []
            if not significant:
                if too_short:
                    period_lines.append("No reliable period yet: one short night covers under two cycles of the "
                                        "best peak. Add nights.")
                elif carried_now and not (math.isfinite(fap) and fap > 0.01):
                    period_lines.append(f"No reliable period: the peak depends on {', '.join(carried_now)} alone "
                                        "(often an offset between nights, not the star).")
                elif am_check.get("demoted") and not (math.isfinite(fap) and fap > 0.01):
                    period_lines.append("No reliable period: the peak follows the nights' airmass; it is not shown "
                                        "to be the star.")
                elif nights_say_no and not (math.isfinite(fap) and fap > 0.01):
                    period_lines.append(f"No significant period: counting nights, the peak's false-alarm "
                                        f"probability is {night_fap:.1e} (a night's points share offsets).")
                else:
                    in_night = [float(np.std(corrected[np.array([o.night == n for o in usable])]))
                                for n in nights if sum(o.night == n for o in usable) >= 3]
                    night_meds = [float(np.median(corrected[np.array([o.night == n for o in usable])]))
                                  for n in nights if sum(o.night == n for o in usable) >= 3]
                    per_point = f"scatter {np.median(in_night):.3f} mag per point" if in_night else ""
                    n2n = f"{np.std(night_meds):.3f} mag night to night" if len(night_meds) > 1 else ""
                    detail = ", ".join(x for x in (per_point, n2n) if x)
                    period_lines.append("No significant period: the star looks constant at this precision"
                                        + (f" ({detail})." if detail else "."))
            period_lines += [
                ("Best    " if significant else "Peak    ")
                + f"{p:.5f} d ({p * 24:.2f} h)" + ("" if significant else "   (not significant)")
                + ("   (one short night: not reliable)" if too_short else ""),
                f"Power   {self.period['power']:.2f}" + (f"   FAP {fap:.1e}" if math.isfinite(fap) else "")
                + (f" (points), {night_fap:.1e} counting nights ("
                   + ("from the nightly means" if (self.period.get("night_fap") or {}).get("method") == "nightly means"
                      else "night offsets taken out") + ")"
                   if math.isfinite(night_fap) and math.isfinite(fap) else ""),   # 2.2.17 (item 10)
                f"Basis   {getattr(self, '_period_note', '') or 'every point'}",
                f"Alias   {alias:.5f} d (one-day)",
                f"Epoch   JD {epoch:.5f}",
                "Fold    check-star zero point" if self.use_check_zp.get() else "Fold    check-star correction off",
            ]
            if self.catalog_period:
                period_lines.append(f"VSX     {self.catalog_period:.5f} d")
            head = 0 if significant else 1
            if self.period.get("alias_text"):
                period_lines[head] += "   ⚠ near a day alias"
                cautions.append(self.period["alias_text"])
            carried = self.period.get("carried_by") or []
            if carried:
                period_lines.append("Check   ⚠ depends on " + ", ".join(carried) + " alone")
                cautions.append(f"Leave {', '.join(carried)} out and this period disappears: one night carries it "
                                "(often an offset between nights, not the star). Check that night before trusting it.")
            # 2.2.17 (item 10): nightly means that follow airmass, and what that does to the period.
            am_text = core.airmass_trend_text(am_trend)
            if am_text:
                period_lines.append(f"Airmass ⚠ nightly means follow airmass ({am_trend['slope']:+.3f} mag per "
                                    "airmass)" + ("; period demoted" if am_check.get("demoted") else ""))
                cautions.append(am_text + (" " + am_check["why"] if am_check.get("why") else
                                           " The period survives with the trend taken out and without the "
                                           "high-airmass nights."))
            edge_text = core.period_edge_text(self.period)
            if edge_text:
                period_lines[head] += "   ⚠ at the edge"
                cautions.append(edge_text)
            if math.isfinite(baseline) and p > baseline / 1.5:
                cautions.append(f"The data span only {baseline:.2f} d, under 1.5 cycles of the best period; it is not "
                                "pinned down yet.")
            marked = self._parse_marks()
            if marked:
                freqs = np.asarray(self.period["freqs"])
                power = np.asarray(self.period["power_spectrum"])
                order = np.argsort(freqs)
                for m in marked:
                    f = 1.0 / m
                    if freqs.min() <= f <= freqs.max():
                        period_lines.append(f"Marked  {m:g} d → power {np.interp(f, freqs[order], power[order]):.2f}")
                    else:
                        period_lines.append(f"Marked  {m:g} d (outside the search)")
        else:
            ax.set_title("Not enough points for a period")
            period_lines = ["Needs at least 8 usable points."]
        legacy = [n for n in nights if n in self.legacy_mixed]
        if legacy:
            cautions.append(f"Re-reduce {', '.join(legacy)}: binned before 1.7, so the channels were mixed "
                            "(the filter and colors are not real).")
        if len(nights) == 1:
            cautions.append("One night: the night-to-night spread needs more nights.")
        cautions += self._pier_cautions(side_offs, pier_by_night, nights)   # 2.2.14 (item 20)
        cautions += self._zero_point_cautions()   # 2.2.15 (item 2)
        color_lines, color_cautions = self._color_lines(usable)
        cautions += color_cautions
        # 2.2.14 (item 8): a check without a catalog magnitude still has its instrumental differential magnitude.
        check_vals, check_kind = self._check_values(obs_list)
        check_vals_all = [float(v) for v, ex in zip(check_vals, excluded) if not ex and math.isfinite(v)]
        comps = self.active_comps or self.comps_used
        span = (max(jd) - min(jd)) if len(jd) else 0.0
        series_lines = [
            f"Points  {len(obs_list)} ({len(usable)} used)",
            f"Nights  {len(nights)}   span {span:.1f} d",
            *self._comps_lines(comps),   # 2.2.14 (item 9): wrapped, so 6+ comps do not run off the box
            f"Check   {self.check_used or '—'}" + (f"   σ {np.std(check_vals_all):.3f}" if len(check_vals_all) > 1 else "")
            + ("   (instrumental)" if check_kind == "instrumental" and check_vals_all else ""),
            # 2.2.14 (item 9): the camera binning ("bin None" was the plot's binning box); plot bins only when on.
            f"Filter  {filt}   {self._binning_text(nights)}"
            + (f"   plot bins {self.bin_choice.get()}" if self.bin_choice.get() != "None" else ""),
        ]
        rows = self._night_rows(obs_list, mag, excluded, nights, flip_steps, pier_by_night)
        self._fill_summary(series_lines, period_lines, color_lines, rows, cautions)

        ax = self.out_ax3
        # 2.2.16 (item 14): with a night zoomed on the light curve, the check star shows the same night on the same
        # hours axis, its y fitted to that night's used points; clicking the night again restores both.
        cx = lc_x if zoom is not None else tx
        if np.isfinite(check_vals).any():
            for i, night in enumerate(nights):
                nm = np.array([obs.night == night for obs in obs_list])
                mask = nm & np.isfinite(check_vals) & ~excluded
                ax.plot(cx[mask], check_vals[mask], "o", ms=3, color=colors[i % len(colors)], alpha=alpha_for(night, 1.0))
                # 2.2.16 (item 13): flagged frames' check values drawn as × like the light curve's (left out).
                fmask = nm & np.isfinite(check_vals) & excluded
                if fmask.any():
                    ax.plot(cx[fmask], check_vals[fmask], "x", ms=5, color="#888888", alpha=alpha_for(night, 1.0))
            sel = np.isfinite(check_vals) & ~excluded
            self._hover_sets.append((ax, cx[sel & in_zoom], check_vals[sel & in_zoom],
                                     [f"check {v:.4f}\n{self._time_text(o.jd)}  {o.night}\n{os.path.basename(o.path)}"
                                      for o, v in zip([obs_list[k] for k in np.where(sel & in_zoom)[0]],
                                                      check_vals[sel & in_zoom])], 0.0))
            ax.invert_yaxis()
            in_view = sel & in_zoom
            good = check_vals[in_view]   # σ only from used points (2.2.16 item 14, review)
            only_flagged = False
            if zoom is not None and not in_view.any():
                in_view = np.isfinite(check_vals) & in_zoom   # the night has only flagged check points
                only_flagged = bool(in_view.any())
            self._fit_ylim(ax, check_vals, None, in_view)
            if zoom is not None and not in_view.any():
                # 2.2.16 (item 14, review): the check star was measured on other nights only.
                ax.text(0.5, 0.5, "No check points this night", transform=ax.transAxes, ha="center",
                        va="center", fontsize=9, color="#5d6d76")
            if zoom is not None:
                ax.set_xlim(self.out_ax1.get_xlim())
            c_off = 0
            for night in nights:
                nm = np.array([o.night == night for o in obs_list], dtype=bool)
                c_off += self._pin_off_scale(ax, cx, check_vals, nm & excluded & in_zoom, alpha_for(night, 1.0))
            spread = f"  σ = {np.std(good):.3f}" if good.size > 1 else ""
            if zoom is not None:
                spread = (f" · {self.night_label(zoom, short=True)}"
                          + (f" σ {np.std(good):.3f}" if good.size > 1 else "")
                          + (" (flagged points only)" if only_flagged else ""))
            off_txt = f"  ({c_off} flagged off scale)" if c_off else ""
            catalog = ""
            try:
                if self.check_mag.get().strip() and check_kind == "reduced":
                    bw = f"{self._band_word()} " if self._sloan_run() else ""   # 2.2.17 (item 1): "catalog i′ 11.23"
                    catalog = f"  catalog {bw}{float(self.check_mag.get()):.3f}"
            except ValueError:
                pass
            if check_kind == "instrumental":
                # 2.2.14 (item 8): no catalog V for the check, so it is shown against the comps, instrumental.
                ax.set_title(f"Check star, instrumental{spread}{off_txt}")
                ax.set_ylabel("Check − comps (instr.)")
            else:
                ax.set_title(f"Check star{spread}{catalog}{off_txt}")
                ax.set_ylabel("Reduced mag")
        else:
            ax.set_title("No check star")
        if zoom is not None and np.isfinite(check_vals).any():
            # 2.2.16 (item 14): the light curve's hours axis and label.
            try:
                ax.ticklabel_format(axis="x", useOffset=False, style="plain")
            except AttributeError:
                pass
            ax.set_xlabel(self.out_ax1.get_xlabel())
        else:
            self._format_time_axis(ax)
        ax.grid(True, alpha=0.3)

        ax = self.out_ax4
        if self.period:
            period_axis = 1.0 / self.period["freqs"]
            ax.plot(period_axis, self.period["power_spectrum"], color="#c0c6cc" if too_short else "#0067c0", lw=0.8)
            shade_long_periods(ax, span_used, period_axis)
            if too_short:
                ax.text(0.5, 0.5, "Periods longer than half the night\ncannot be told apart yet",
                        transform=ax.transAxes, ha="center", va="center", fontsize=9, color="#5d6d76",
                        bbox=dict(boxstyle="round", fc="#ffffff", ec="#c9d6de", alpha=0.9))
            pw = np.asarray(self.period["power_spectrum"])
            self._hover_sets.append((ax, period_axis, pw, [f"P = {pp:.5f} d ({pp * 24:.3f} h)\npower {vv:.3f}"
                                                           for pp, vv in zip(period_axis, pw)], 0.0))
            ax.axvline(self.period["period_days"], color="#c43b3b", lw=0.8)
            show_legend = False
            if self.catalog_period and period_axis.min() <= self.catalog_period <= period_axis.max():
                ax.axvline(self.catalog_period, color="#2e7d32", lw=0.8, ls="--", label="VSX")
                show_legend = True
            for k, m in enumerate(self._parse_marks()):
                if period_axis.min() <= m <= period_axis.max():
                    ax.axvline(m, color="#7b2cbf", lw=0.9, ls=":", label="marked" if k == 0 else None)
                    ax.text(m, ax.get_ylim()[1] * 0.95, f" {m:g}", color="#7b2cbf", fontsize=7, rotation=90, va="top")
                    show_legend = True
            if show_legend:
                ax.legend(fontsize=8, frameon=False)
            ax.set_xscale("log")
            ax.set_title("Lomb-Scargle")
            ax.set_xlabel("Period (days)")
            ax.set_ylabel("Power")
        else:
            ax.set_title("No periodogram")
        ax.grid(True, alpha=0.3)
        self.out_fig.tight_layout()
        self._refit_lc_title()   # 2.2.14 (item 16): re-measure the title key on the laid-out axes
        self.out_canvas.draw_idle()

    # ---- Output summary (boxes + per-night table) ------------------------------------
    SUMMARY_COLUMNS = (("night_text", "Night", 120), ("night", "JD", 90), ("pts", "Pts", 60), ("used", "used", 60),
                       ("mean", "Mean mag ± err", 145), ("tsig", "Target σ", 85), ("csig", "Check σ", 85),
                       ("side", "Side", 80), ("flip", "Flip step", 85), ("bv", "B−V", 70), ("flags", "Flags", 170),
                       ("sent", "Sent", 70), ("note", "Note", 260))   # 2.2.18 (item 10): Sent

    def _build_summary(self, card):
        holder = ttk.Frame(card, style="Card.TFrame")
        holder.pack(fill=X, pady=(4, 4))
        boxes = ttk.Frame(holder, style="Card.TFrame")
        boxes.pack(fill=X)
        self.summary_vars = {}
        for key, title in (("series", "Series"), ("period", "Period"), ("color", "Color")):
            box = self._group(boxes, title)
            box.pack(side=LEFT, fill=Y, padx=(0, 8))
            var = StringVar(value="—")
            ttk.Label(box, textvariable=var, style="Box.TLabel", justify=LEFT).pack(anchor=W)
            self.summary_vars[key] = var
        table_row = ttk.Frame(holder, style="Card.TFrame")
        table_row.pack(fill=X, pady=(6, 0))
        cols = [c for c, _l, _w in self.SUMMARY_COLUMNS]
        self.night_table = ttk.Treeview(table_row, columns=cols, show="headings", height=3, selectmode="browse")
        for col, label, width in self.SUMMARY_COLUMNS:
            self.night_table.heading(col, text=label, anchor=W)
            cw = col_width(self.night_table, label, width)
            self.night_table.column(col, width=cw, minwidth=cw, anchor=W, stretch=(col == "note"))
        scroll = ttk.Scrollbar(table_row, orient=VERTICAL, command=self.night_table.yview)
        self.night_table.configure(yscrollcommand=scroll.set)
        self.night_table.pack(side=LEFT, fill=X, expand=True)
        scroll.pack(side=LEFT, fill=Y)
        self.night_table.bind("<ButtonRelease-1>", self._on_night_click)
        self.night_table.bind("<Double-1>", self._on_night_double)   # 2.2.18 (item 10): unmark "sent"
        self.spread_text = StringVar(value="")
        ttk.Label(holder, textvariable=self.spread_text, style="Card.TLabel").pack(anchor=W, pady=(4, 0))
        self.caution_text = StringVar(value="")
        ttk.Label(holder, textvariable=self.caution_text, style="Warn.TLabel", wraplength=1500, justify=LEFT).pack(anchor=W)

    def _on_night_click(self, event):
        """Click a night to highlight it on the plots (2.2.15: the light curve zooms to it); click it again to show
        every night."""
        row = self.night_table.identify_row(event.y)
        night = self._night_by_item.get(row) if row else None
        if night is None:
            return
        # 2.2.18 (item 10, review): wait a moment, so a double-click (unmark "sent") does not zoom twice first.
        job = getattr(self, "_night_click_job", None)
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._night_click_job = self.after(250, lambda: self._night_click_apply(night))

    def _night_click_apply(self, night: str):
        self._night_click_job = None
        self.highlight_night = None if self.highlight_night == night else night
        if self.highlight_night is None:
            self.night_table.selection_remove(self.night_table.selection())
        self.draw_output()

    @staticmethod
    def _check_values(obs_list):
        """2.2.14 (item 8): the check star's values for the plot and σ: the reduced magnitude (check_std) when the
        check has a catalog magnitude, else its instrumental differential magnitude (check minus the comps).
        Returns (array, "reduced" | "instrumental")."""
        reduced = np.array([o.check_std if o.check_std is not None else np.nan for o in obs_list], dtype=float)
        if np.isfinite(reduced).any():
            # 2.2.14 review: a mixed series (the check had no catalog magnitude on some nights): those points get
            # the same reduction, comps' catalog mean + check − comps, so every night sits on one scale.
            for k, o in enumerate(obs_list):
                if not math.isfinite(reduced[k]) and math.isfinite(o.comp_cat):
                    reduced[k] = o.comp_cat + core.check_instrumental(o)
            return reduced, "reduced"
        return np.array([core.check_instrumental(o) for o in obs_list], dtype=float), "instrumental"

    @staticmethod
    def _comps_lines(comps, width: int = 50) -> list[str]:
        """2.2.14 (item 9): the Series box's Comps line, wrapped between names (continuation lines indented)."""
        if not comps:
            return ["Comps   —"]
        lines, line = [], ""
        for k, name in enumerate(comps):
            piece = name + ("," if k < len(comps) - 1 else "")
            if line and len(line) + 1 + len(piece) > width - 8:
                lines.append(line)
                line = piece
            else:
                line = f"{line} {piece}" if line else piece
        lines.append(line)
        return ["Comps   " + lines[0]] + ["        " + x for x in lines[1:]]

    def _binning_text(self, nights) -> str:
        """2.2.14 (item 9): "Bin 2" (or "Bin 1/2" for mixed nights) from each night's recorded reduction; a night
        that did not record it (series saved before 2.2.3) is left out, and with none recorded the Binning box
        (which Load series sets from the series) is used."""
        seen = set()
        for night in nights:
            info = self.night_bin.get(night)
            b = info.get("binning") if isinstance(info, dict) else info
            try:
                if b is not None and int(b) >= 1:
                    seen.add(int(b))
            except (TypeError, ValueError):
                pass
        if not seen:
            try:
                seen.add(max(int(self.binning.get() or 1), 1))
            except Exception:
                return "Bin ?"
        return "Bin " + "/".join(str(b) for b in sorted(seen))


    def _pier_cautions(self, side_offs: dict, pier_by_night: dict, nights: list) -> list[str]:
        """2.2.14 (item 20): warn when the series mixes pier sides without 'Pier side as segments', and say when
        some nights' sides are unknown."""
        out = []
        sides = [s for s in side_offs.get("counts", {}) if s]
        offs = [abs(side_offs["offsets"][s]) for s in sides if s != side_offs.get("ref")]
        varies = self._varies_within_night(max(offs) if offs else 0.0)
        if len(sides) > 1 and not self.pier_segments.get() and not self.norm_segments.get():
            ref = side_offs["ref"]
            parts = [f"{s} reads {side_offs['offsets'][s]:+.3f} against {ref} ({side_offs['how'][s]})"
                     for s in sides if s != ref]
            # 2.2.15 review: a side grouped from several readings names them ("30° (29°/30°)").
            read: dict = {}
            for info in pier_by_night.values():
                for key in ("before", "after"):
                    label = (info or {}).get(key)
                    if label:
                        read.setdefault(label, set()).add(((info or {}).get("read") or {}).get(key) or label)

            def named(s):
                got = sorted(read.get(s, ()), key=lambda t: (len(t), t))
                return f"{s} ({'/'.join(got)})" if len(got) > 1 or (got and got[0] != s) else s
            head = ("Nights on both pier sides (" + ", ".join(f"{named(s)}: {side_offs['nights'].get(s, 0)} night(s)"
                                                             for s in sides) + "): ")
            if varies:
                # 2.2.14 review: on a short-period or large-amplitude star the "offset" is mostly the star changing.
                out.append(head + f"the star itself changes within a night ({varies}), so a side offset read from "
                           "its light curve means little. Comp health shows each comp's step at the flip.")
            else:
                out.append(head + "; ".join(parts)
                           + ". Tick 'Pier side as segments' to normalise each side on its own.")
        unknown = [n for n in nights if not (pier_by_night.get(n) or {}).get("source")]
        if unknown and len(unknown) < len(nights) and (self.pier_segments.get() or len(sides) > 1):
            out.append(f"Pier side unknown on {len(unknown)} night(s) (no ROTATOR, no PIERSIDE, no flip): left as "
                       "they are.")
        if self.pier_segments.get() and len(sides) > 1 and varies and not self.norm_segments.get():
            out.append(f"Pier side as segments on a star that changes within a night ({varies}): the side offsets "
                       "may come from the star, not the pier side.")
        if self.pier_segments.get() and len(sides) < 2 and not self.norm_segments.get():
            altaz = nights and all((pier_by_night.get(n) or {}).get("source") == "altaz" for n in nights)
            out.append("Pier side as segments has nothing to do: " + (
                "every night is on one side." if sides else
                "the mount is alt-az (no pier sides)." if altaz else   # 2.2.18 (item 8)
                "no night has ROTATOR or PIERSIDE in its headers or a meridian flip, so the sides are unknown."))
        return out

    def _varies_within_night(self, offset: float) -> str:
        """2.2.14 review: '' for a star steady within a night; otherwise why not (a catalog period short against a
        night, or 10-minute means that move by more than the pier-side offset)."""
        by: dict = {}
        for o in self.observations:
            if not o.flag and math.isfinite(o.mag):
                by.setdefault(o.night, []).append(o)
        spans, ranges = [], []
        for pts in by.values():
            jd = np.array([o.jd for o in pts])
            mg = np.array([o.mag for o in pts])
            if jd.size < 10:
                continue
            spans.append(float(jd.max() - jd.min()))
            k = np.floor((jd - jd.min()) * 144.0).astype(int)   # 10-minute bins
            means = [float(np.median(mg[k == b])) for b in np.unique(k) if (k == b).sum() >= 3]
            if len(means) >= 3:
                ranges.append(float(np.percentile(means, 90) - np.percentile(means, 10)))
        span = float(np.median(spans)) if spans else 0.0
        p = self.catalog_period   # only a catalog period: a period found in noise would hide a real pier offset
        if p and span > 0 and p < 3.0 * span:
            return f"period {p:.4g} d against {span * 24:.1f} h nights"
        rng = float(np.median(ranges)) if ranges else 0.0
        if rng > max(0.03, 1.5 * abs(offset)):
            return f"10-minute means move by {rng:.2f} mag"
        return ""

    def _night_rows(self, obs_list, mag, excluded, nights, flip_steps, pier_by_night=None) -> list[dict]:
        from collections import Counter

        rows = []
        for night in nights:
            idx = [k for k, o in enumerate(obs_list) if o.night == night]
            used = [k for k in idx if not excluded[k]]
            first = min((obs_list[k].jd for k in idx if math.isfinite(obs_list[k].jd)), default=float("nan"))
            try:
                local = core.jd_to_datetime_utc(first).astimezone()
                date = f"{local:%b} {local.day}"
            except (OverflowError, ValueError, OSError):
                date = ""
            bins = core.bin_points([obs_list[k] for k in used], [float(mag[k]) for k in used], None,
                                   self._scint_setup())
            mean = f"{bins[0]['value']:.3f} ± {bins[0]['err']:.4f}" if bins else "—"
            tsig = core.robust_sigma([mag[k] for k in used])
            csig = core.robust_sigma([obs_list[k].check_std for k in used])
            if not math.isfinite(csig):   # 2.2.14 (item 8): check without a catalog mag: instrumental σ
                csig = core.robust_sigma([core.check_instrumental(obs_list[k]) for k in used])
            bv = [obs_list[k].bv for k in used if math.isfinite(obs_list[k].bv)]
            flags = Counter(w for k in idx for w in obs_list[k].flag.split())
            # 2.2.14 (item 18): transmission loss in words, and how many such points were kept.
            if core.HAZE_FLAG in flags:   # 2.2.15 (item 4): the short form; the log keeps the full wording
                flags[core.HAZE_SHORT] = flags.pop(core.HAZE_FLAG)
            if "cloudedge" in flags:   # 2.2.14 (item 10): in words too
                flags[core.CLOUD_EDGE_LABEL] = flags.pop("cloudedge")
            kept_haze = self._transmission_summary(night)[0] \
                if self._night_info(night).get("keep_transmission") else 0
            if kept_haze:
                flags[f"kept, {core.HAZE_SHORT}"] = kept_haze
            # 2.2.15 (item 1): frames in a planet's predicted transit, flagged or kept.
            planet_hits = self._night_info(night).get("planet_transits") or []
            if planet_hits:
                letters = "/".join(str(h.get("letter") or "?") for h in planet_hits)
                if core.TRANSIT_FLAG in flags:
                    flags[f"in planet {letters} transit"] = flags.pop(core.TRANSIT_FLAG)
                elif self._night_info(night).get("transit_flagged") is False:
                    # review: frames in the union of the windows (b and c overlapping count once)
                    wins = [w for h in planet_hits for w in h.get("windows", [])]
                    flags[f"kept, in planet {letters} transit"] = sum(
                        1 for k in idx if any(a <= obs_list[k].jd <= b for a, b in wins))
            if core.TRANSIT_FLAG in flags:
                flags["in a planet transit"] = flags.pop(core.TRANSIT_FLAG)
            note = self.night_notes.get(night, "")
            rows.append({
                "night": night, "night_text": self.night_label(night, short=True), "date": date,
                "pts": len(idx), "used": len(used), "mean": mean,
                "value": bins[0]["value"] if bins else float("nan"), "err": bins[0]["err"] if bins else float("nan"),
                "tsig": f"{tsig:.3f}" if math.isfinite(tsig) else "—",
                "csig": f"{csig:.3f}" if math.isfinite(csig) else "—",
                # 2.2.14 (item 20): pier side(s), and a flip step over 0.02 mag marked.
                "side": core.side_text((pier_by_night or {}).get(night)),
                "flip": (f"{flip_steps[night]['offset']:+.3f}"
                         + (" ⚠" if abs(flip_steps[night]["offset"]) > core.FLIP_STEP_MARK else "")
                         if night in flip_steps else "—"),
                "bv": f"{float(np.median(bv)):.3f}" if bv else "—",
                "flags": ", ".join(f"{n} {w}" for w, n in flags.most_common()) or "—",
                "sent": self._sent_text(night),   # 2.2.18 (item 10)
                "note": ("* " + note) if note else "",
            })
        return rows

    def _fit_night_column(self, col: str, values: list[str]) -> None:
        """2.2.14 review: a night-table column as wide as its heading or widest value ('30°/210° flipped')."""
        try:
            from tkinter import font as tkfont
            body = tkfont.Font(self.night_table, font=(core.UI_FONT, 10))
            label = next(lab for c, lab, _w in self.SUMMARY_COLUMNS if c == col)
            width = max([col_width(self.night_table, label, 40)] + [body.measure(v) + 24 for v in values])
            self.night_table.column(col, width=width, minwidth=width)
        except Exception:
            pass

    def _fill_summary(self, series_lines, period_lines, color_lines, rows, cautions):
        # 2.2.3: kept so Save plot PNG can draw the same blocks above the plots.
        self._summary_snapshot = {"series": list(series_lines), "period": list(period_lines),
                                  "color": list(color_lines), "rows": [dict(r) for r in rows], "cautions": list(cautions)}
        self.summary_vars["series"].set("\n".join(series_lines))
        self.summary_vars["period"].set("\n".join(period_lines))
        self.summary_vars["color"].set("\n".join(color_lines) or "—")
        table = self.night_table
        table.delete(*table.get_children())
        self._night_by_item = {}
        for row in rows:
            iid = table.insert("", END, values=[row[c] for c, _l, _w in self.SUMMARY_COLUMNS])
            self._night_by_item[iid] = row["night"]
            if row["night"] == self.highlight_night:
                table.selection_set(iid)
        table.configure(height=max(1, min(len(rows), 8)))
        self._fit_night_column("side", [str(r.get("side", "")) for r in rows])
        self._fit_night_column("sent", [str(r.get("sent", "")) for r in rows])   # 2.2.18 (item 10)
        means = [r["value"] for r in rows if math.isfinite(r["value"])]
        errs = [r["err"] for r in rows if math.isfinite(r["err"])]
        if len(means) > 1:
            self.spread_text.set(f"Spread of nightly means {np.std(means):.4f} mag  (typical error {np.median(errs):.4f})"
                                 + ("     Click a night to zoom the light curve to it; click it again to show all." if len(rows) > 1 else ""))
        else:
            self.spread_text.set("")
        if any(str(r.get("sent", "—")) != "—" for r in rows):   # 2.2.18 (item 10)
            self.spread_text.set((self.spread_text.get() + "     " if self.spread_text.get() else "")
                                 + "Double-click a night marked sent to unmark it.")
        self.caution_text.set("   ".join("⚠ " + c for c in cautions))

    def _color_lines(self, points):
        """Lines for the Color box, plus cautions."""
        def stat(values):
            v = np.array([x for x in values if math.isfinite(x)])
            if v.size == 0:
                return None
            return float(np.median(v)), float(1.4826 * np.median(np.abs(v - np.median(v))) / max(1.0, math.sqrt(v.size)))

        lines, cautions = [], []
        filts = {getattr(o, "filt", "") for o in points}
        if filts and all(core.is_sloan(f) for f in filts) and not any(
                math.isfinite(getattr(o, k)) for o in points for k in ("bv", "vr")):
            # 2.2.17 (item 1): one Sloan filter on a mono camera gives no B−V / V−R; say so instead of "not measured".
            return [f"Sloan {', '.join(core.band_label(f) for f in sorted(filts))}: one band, no B−V / V−R color "
                    "index"], []
        for key, label in (("bv", "B−V"), ("vr", "V−R")):
            vals = [o for o in points if math.isfinite(getattr(o, key))]
            st = stat(getattr(o, key) for o in vals)
            if not st:
                diff = stat(getattr(o, key + "_diff") for o in points)
                lines.append(f"{label:5s} " + (f"{diff[0]:+.3f} relative to the comps" if diff else "not measured"))
                continue
            methods = [core.color_method(o, key) for o in vals]
            field_nights = {o.night for o, m in zip(vals, methods) if m == "field"}
            fits = [((self.color_cal.get(n) or {}).get("fits") or {}).get(key) for n in field_nights]
            fits = [f for f in fits if core.color_fit_usable(f)]   # 2.2.16 (item 3 review)
            sys_err = float(np.median([f["scatter"] / math.sqrt(f["n"]) for f in fits])) if fits else 0.0
            lines.append(f"{label:5s} {st[0]:.3f} ± {math.hypot(st[1], sys_err):.3f}")
            if key == "bv":
                temp = core.bv_temperature(st[0])
                if math.isfinite(temp):
                    lines.append(f"Temp  ≈ {temp:,.0f} K, {core.bv_spectral_class(st[0])}-type")
            n_cal = sum(1 for m in methods if m)
            if n_cal == len(vals):
                lines.append("Cal   " + (f"field stars, k = {np.median([f['k'] for f in fits]):.2f}" if fits
                                         else "saved color term"))
            elif n_cal:
                lines.append(f"Cal   {n_cal} of {len(vals)} points")
                cautions.append(f"{label} is calibrated on only part of the series.")
            else:
                lines.append("Cal   untransformed")
                cautions.append(f"{label} is untransformed (approximate); calibrate it with 'Also measure R and B' on a solved image.")
        if all(line.endswith("not measured") for line in lines):
            lines = ["Not measured.", "Tick 'Also measure R and B'", "on the Photometry page."]
        return lines, cautions

    @staticmethod
    def _night_scatter_text(obs_list, mag, excluded, nights) -> str:
        """Robust scatter (1.4826 x MAD) of the target and the check star within each night."""
        def robust(values):
            v = np.asarray([x for x in values if x is not None and math.isfinite(x)], dtype=float)
            if v.size < 5:
                return None
            return float(1.4826 * np.median(np.abs(v - np.median(v))))

        parts = []
        for night in nights:
            idx = [k for k, o in enumerate(obs_list) if o.night == night and not excluded[k]]
            t = robust(mag[k] for k in idx)
            if t is None:
                continue
            c = robust(obs_list[k].check_std for k in idx)
            label = night[-4:] if night.startswith("JD") else night
            parts.append(f"{label} {t:.3f}" + (f" / {c:.3f}" if c is not None else ""))
        if not parts:
            return ""
        return "  Scatter per night (robust σ, target / check): " + "; ".join(parts) + "."

    def _analysis_values(self, points):
        """Values for the fold, period search, and export: check-star zero point (if on), then
        either every segment lined up, or only each night's flip step lined up."""
        vals = self._zero_pointed(points)
        if self.norm_segments.get():
            return core.normalize_segments(points, vals)
        if self.pier_segments.get():   # 2.2.14 (item 20)
            return self._pier_values(points, list(vals))[0]
        if self.align_flip.get():
            return core.align_flips(points, vals, core.flip_offsets(points, vals))
        return list(vals)

    def _time_text(self, jd: float) -> str:
        try:
            local = core.jd_to_datetime_utc(jd).astimezone()
            return f"JD {jd:.5f} ({local.strftime('%b %d %H:%M')} local)"
        except (OverflowError, ValueError, OSError):
            return f"JD {jd:.5f}"

    def _point_label(self, obs, value: float) -> str:
        err = f" ± {obs.merr:.4f}" if math.isfinite(obs.merr) else ""
        return (f"{value:.4f}{err}\n{self._time_text(obs.jd)}\n{obs.night}"
                + (f"  [{obs.flag}]" if obs.flag else "") + f"\n{os.path.basename(obs.path)}")

    def _bin_label(self, b: dict) -> str:
        return (f"{b['night']} binned\n{b['value']:.4f} ± {b['err']:.4f}  (n = {b['n']})\n"
                f"{self._time_text(b['jd'])}")

    def _on_hover(self, event):
        """Output plots: show the value of the nearest point under the mouse."""
        self._hover_common(event, "_hover_", self.out_canvas)

    def _on_photo_hover(self, event):
        """Photometry image: show the name, catalog magnitudes, and pixel position of the star under the mouse."""
        self._hover_common(event, "_photo_hover_", self.photo_canvas)

    def _hover_common(self, event, prefix: str, canvas):
        """Shared hover logic. State lives in <prefix>sets, <prefix>annots, <prefix>key."""
        sets = getattr(self, prefix + "sets", [])
        annots = getattr(self, prefix + "annots", {})
        best = None
        if event.inaxes is not None and event.x is not None:
            for ax, xs, ys, labels, bonus in sets:
                if ax is not event.inaxes or len(xs) == 0:
                    continue
                try:
                    pts = ax.transData.transform(np.column_stack([np.asarray(xs, float), np.asarray(ys, float)]))
                except Exception:
                    continue
                d = np.hypot(pts[:, 0] - event.x, pts[:, 1] - event.y)
                d = np.where(np.isfinite(d), d - bonus, np.inf)
                j = int(np.argmin(d))
                if d[j] < 8 and (best is None or d[j] < best[0]):
                    best = (d[j], ax, float(xs[j]), float(ys[j]), labels[j])
        key = None if best is None else (id(best[1]), best[2], best[3])
        if key == getattr(self, prefix + "key", None):
            return
        setattr(self, prefix + "key", key)
        for annot in annots.values():
            annot.set_visible(False)
        if best is not None:
            _, ax, x, y, text = best
            annot = annots.get(ax)
            if annot is None:
                annot = ax.annotate("", xy=(0, 0), xytext=(12, 12), textcoords="offset points", fontsize=8,
                                    bbox={"boxstyle": "round", "fc": "#fffff0", "ec": "#888888", "alpha": 0.95},
                                    zorder=20)
                annots[ax] = annot
                setattr(self, prefix + "annots", annots)
            annot.xy = (x, y)
            annot.set_text(text)
            # Keep the label inside the plot near the right and top edges.
            left = event.x > ax.bbox.x0 + 0.6 * ax.bbox.width
            low = event.y > ax.bbox.y0 + 0.6 * ax.bbox.height
            annot.set_position((-12 if left else 12, -12 if low else 12))
            annot.set_ha("right" if left else "left")
            annot.set_va("top" if low else "bottom")
            annot.set_visible(True)
        if canvas is not None:
            canvas.draw_idle()

    def _zero_pointed(self, points):
        return core.apply_night_zero_point(points) if self.use_check_zp.get() else [o.mag for o in points]

    def _bin_interval(self):
        """False = no binning, None = by night, number = minutes."""
        choice = self.bin_choice.get()
        if choice == "None":
            return False
        if choice == "Night":
            return None
        try:
            return float(choice.split()[0])
        except (ValueError, IndexError):
            return False

    def _parse_marks(self) -> list[float]:
        out = []
        for part in self.mark_periods.get().replace(";", ",").split(","):
            try:
                v = float(part.strip())
            except ValueError:
                continue
            if v > 0:
                out.append(v)
        return out

    def add_series_file(self):
        path = filedialog.askopenfilename(
            title="Add a saved series to the one that is open",
            filetypes=[("SHOBS-P series", "*.cvseries"), ("JSON", "*.json"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            meta, obs = core.load_series(path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not read {os.path.basename(path)}:\n{exc}")
            return
        their_mode = meta.get("mode") if meta.get("mode") in MODES else "variables"
        our_mode = self.series_mode or self.mode_key
        if self.observations and their_mode != our_mode:
            messagebox.showerror(APP_TITLE, f"{os.path.basename(path)} is a {MODES[their_mode]['name']} series; the open "
                                            f"series is {MODES[our_mode]['name']}. Series from different modes cannot "
                                            "be merged.")
            return
        theirs = (list(meta.get("comps_used") or []), meta.get("check_used") or "")
        ours = (list(self.comps_used), self.check_used)
        warn = ""
        if self.observations and theirs[0] and ours[0] and theirs != ours:
            warn = (f"\n\nCareful: that file used comps {', '.join(theirs[0])} and check {theirs[1] or 'none'}; "
                    f"this series uses comps {', '.join(ours[0])} and check {ours[1] or 'none'}.")
        # 2.2.3: per-night reduction settings (binning, aperture, comps) compared with the open series.
        if self.observations:
            seen = set()
            for their_night, info in sorted((meta.get("night_binning") or {}).items()):
                if not isinstance(info, dict):
                    continue
                for line in core.night_setup_differences(
                        {**info, "comps": info.get("comps") or theirs[0], "check": info.get("check") or theirs[1]},
                        {n: v for n, v in self.night_bin.items() if n != their_night}):
                    if line not in seen:
                        seen.add(line)
                        warn += f"\n  • {self.label_nights(their_night)}: {self.label_nights(line)}"
            if seen:
                warn = warn.replace("\n  • ", "\n\nReduced differently from the open series:\n  • ", 1)
        new_nights = sorted({o.night for o in obs})
        clash = sorted({o.night for o in obs} & {o.night for o in self.observations})
        if clash:
            warn += f"\n\nThese nights are already in the series and will be merged point by point: {', '.join(clash)}."
        if not messagebox.askyesno(
            APP_TITLE, f"Add {len(obs)} points ({', '.join(new_nights)}) from {os.path.basename(path)}?{warn}"
        ):
            return
        if not self.observations and not self.comps_used:
            self._apply_meta(meta)
        legacy = self._legacy_nights(meta, obs)
        self.legacy_mixed |= legacy
        self.highlight_night = None   # 2.2.15 review: nights added from a file show the whole series
        self._fill_base(obs, meta)
        for name, info in (meta.get("spare_catalog") or {}).items():
            self.spare_catalog.setdefault(name, info)
        for name, mag in (meta.get("comp_catalog_mags") or {}).items():
            if mag is not None:
                self.comp_catalog_mags.setdefault(name, float(mag))
        for name, rd in (meta.get("comp_coords") or {}).items():
            if isinstance(rd, (list, tuple)) and len(rd) == 2:
                self.comp_coords.setdefault(str(name), (float(rd[0]), float(rd[1])))
        for night, entries in (meta.get("reports_saved") or {}).items():
            if isinstance(entries, list):
                have = self.reports_saved.setdefault(str(night), [])
                have.extend(e for e in entries if isinstance(e, dict) and e not in have)
        for night, entries in (meta.get("reports_sent") or {}).items():   # 2.2.18 (item 10)
            if isinstance(entries, list):
                have = self.reports_sent.setdefault(str(night), [])
                have.extend(e for e in entries if isinstance(e, dict) and e not in have)
        if self.active_comps and set(self.active_comps) != set(self.comps_used):
            try:
                core.recompute_with_comps(obs, self.active_comps, self.comps_used, self._comp_catalog(),
                                          self._star_aliases())
            except ValueError as exc:
                self.log(f"Could not apply the chosen comps to the added file: {exc}")
        for night, info in (meta.get("color_cal") or {}).items():
            self.color_cal.setdefault(night, info)
        for night, info in (meta.get("night_binning") or {}).items():
            self.night_bin.setdefault(night, info)
        self.observations = core.merge_nights(self.observations, obs)
        self._take_series_snapshot()   # 2.2.14 (item 11)
        for night, note in (meta.get("night_notes") or {}).items():
            if note and not self.night_notes.get(night):
                self.night_notes[night] = note
        self._refresh_period()
        self.draw_output()
        saved = ""
        if self.series_path:
            try:
                self._write_series(self.series_path)
                saved = f" Saved to {os.path.basename(self.series_path)}."
            except OSError as exc:
                messagebox.showwarning(APP_TITLE, f"Could not save the series: {exc}")
        nights = sorted({o.night for o in self.observations})
        self.status.set(f"Added {os.path.basename(path)}: {len(self.observations)} points, {len(nights)} night(s).{saved}")
        self._warn_legacy(legacy, os.path.basename(path))

    def edit_night_note(self):
        nights = []
        for obs in self.observations:
            if obs.night not in nights:
                nights.append(obs.night)
        if not nights:
            messagebox.showinfo(APP_TITLE, "There is no series loaded.")
            return
        listing = "\n".join(f"  {i + 1})  {self.night_label(n, short=True)} ({n})" + (f"   note: {self.night_notes[n][:60]}" if self.night_notes.get(n) else "")
                             for i, n in enumerate(nights))
        answer = simpledialog.askstring(APP_TITLE, f"Which night is the note for?\n\n{listing}\n\nType its number:", parent=self)
        if not answer:
            return
        try:
            night = nights[int(answer.strip()) - 1]
        except (ValueError, IndexError):
            messagebox.showerror(APP_TITLE, f"'{answer}' is not one of the numbers listed.")
            return
        text = simpledialog.askstring(
            APP_TITLE,
            f"Note for the night of {self.night_label(night, short=True)} (stays with the series and goes into the CSV and exports; leave empty to remove):",
            initialvalue=self.night_notes.get(night, ""), parent=self,
        )
        if text is None:
            return
        text = text.strip()
        if text:
            self.night_notes[night] = text
        else:
            self.night_notes.pop(night, None)
        self.draw_output()
        if self.series_path:
            try:
                self._write_series(self.series_path)
            except OSError as exc:
                messagebox.showwarning(APP_TITLE, f"Could not save the series: {exc}")
        self.status.set(f"Note for {night} " + ("saved." if text else "removed."))

    def _name_index(self) -> dict:
        """2.2.14 (item 11): {normalised name: labeled star} for every name a labeled star answers to (its catalogs'
        IDs, AAVSO AUID and label) and the known variables on it (VSX/SIMBAD: V1441 Cas is TYC 4006-670-1)."""
        index, var_best = {}, {}
        for _x, _y, star in self.chart_placed:
            for n in core.star_names(star):
                index.setdefault(" ".join(n.lower().split()), star)
            for v in star.get("variable") or []:
                if not (isinstance(v, dict) and v.get("name")):
                    continue
                # review nit: a variable flagged on two neighbours goes to the one nearest its own position.
                try:
                    d = core.sky_separation_arcsec(float(star["ra"]), float(star["dec"]), float(v["ra"]), float(v["dec"]))
                except (KeyError, TypeError, ValueError):
                    d = float("inf")
                key = " ".join(str(v["name"]).lower().split())
                if key not in var_best or d < var_best[key][0]:
                    var_best[key] = (d, star)
        for key, (_d, star) in var_best.items():
            index.setdefault(key, star)
        return index

    @staticmethod
    def _names_of(name: str, index: dict) -> set:
        star = index.get(" ".join(str(name).lower().split()))
        if star is None:
            return {name}
        names = set(core.star_names(star)) | {str(v.get("name")) for v in star.get("variable") or []
                                              if isinstance(v, dict) and v.get("name")}
        return names | {name}

    def _series_star_coords(self) -> dict:
        """2.2.14 (item 11): sky positions of every star the series and tonight know by name: comps and check
        (comp_coords), spares (spare_catalog, as before tonight's picks too) and watch stars."""
        coords = {name: (float(rd[0]), float(rd[1])) for name, rd in self.comp_coords.items()
                  if rd and rd[0] is not None and rd[1] is not None}
        before = getattr(self, "_series_catalog_before", None) or {}
        for name, rd in (before.get("spare_coords") or {}).items():
            coords.setdefault(name, rd)
        for name, info in self.spare_catalog.items():
            if info.get("ra") is not None and info.get("dec") is not None:
                coords.setdefault(name, (float(info["ra"]), float(info["dec"])))
        for w in self.watch_list:
            if w.get("ra") is not None and w.get("dec") is not None:
                coords.setdefault(w["name"], (float(w["ra"]), float(w["dec"])))
        return coords

    def _same_series_star(self, name: str, candidates, coords: dict, index: dict, xy_of: dict | None = None):
        """2.2.14 (item 11): the series star (one of `candidates`) that `name` is, or None: the same name; one of the
        labeled star's other names (either way round, known variables included); within WATCH_SAME_ARCSEC on the
        sky; or (xy_of, tonight's pixel positions) within 2 px on this frame."""
        candidates = [c for c in candidates if c]
        if name in candidates:
            return name
        mine = self._names_of(name, index)
        for c in candidates:
            if c in mine or name in self._names_of(c, index):
                return c
        best = None
        rd = coords.get(name)
        if rd is not None:
            for c in candidates:
                rc = coords.get(c)
                if rc is None:
                    continue
                d = core.sky_separation_arcsec(rd[0], rd[1], rc[0], rc[1])
                if d <= WATCH_SAME_ARCSEC and (best is None or d < best[0]):
                    best = (d, c)
        if best is None and xy_of and xy_of.get(name) is not None:
            p = xy_of[name]
            for c in candidates:
                q = xy_of.get(c)
                if q is not None and math.hypot(p[0] - q[0], p[1] - q[1]) < 2.0:
                    return c
        return best[1] if best else None

    def _same_star_plan(self, prev_comps, prev_check):
        """2.2.8: tonight's comps and check matched to the series' stars by sky position (3", as same_star_aliases).
        Returns {"comps", "check", "renamed": {tonight's name: series name}} or None when nothing matches. A comp is
        matched only when the series knows its catalog magnitude. Without sky positions names are compared as
        before.
        2.2.14 (item 11, one star, one name per series): a comp, check, watch star or spare that is a series star (by
        any of its catalog names, a known variable's name on it, or within WATCH_SAME_ARCSEC) takes the SERIES' name
        and the series' catalog magnitude, whatever tonight's catalogs say. Extra keys: "remag" {series name:
        (tonight's V, series V)}, "check_mag" (tonight's, series'), "watch" and "spares" {tonight's name: series
        name}, "spare_remag" {name: (tonight's, series')}."""
        series_spares, series_watch = [], []
        for o in self.observations:
            for n in o.spare_insts:
                if n not in series_spares:
                    series_spares.append(n)
            for n in getattr(o, "watch_insts", {}) or {}:
                if n not in series_watch:
                    series_watch.append(n)
        if not prev_comps and not prev_check and not series_spares and not series_watch:
            return None
        before = getattr(self, "_series_catalog_before", None) or {}
        tonight_cat = self._comp_catalog()

        def series_mag(name):
            for src in (before.get("comps") or {}, before.get("spares") or {}, tonight_cat):
                if src.get(name) is not None:
                    return float(src[name])
            return None

        aliases = self._star_aliases()
        coords = self._series_star_coords()
        index = self._name_index() if self.chart_placed else {}
        renamed = {}
        new_comps = list(self.comps_used)
        comp_candidates = list(prev_comps) + [s for s in series_spares if s not in prev_comps]
        for i, name in enumerate(new_comps):
            if name in prev_comps:
                continue
            # 2.2.8: within 3" (same_star_aliases) of a series comp the series knows the magnitude of …
            match = next((p for p in prev_comps if p in aliases.get(name, ()) and p not in new_comps
                          and series_mag(p) is not None), None)
            if not match:
                # … 2.2.14: or any series comp or spare under another of its names / within 4".
                match = self._same_series_star(name, [p for p in comp_candidates if p not in new_comps
                                                      and series_mag(p) is not None], coords, index)
            if match:
                new_comps[i] = match
                renamed[name] = match
        remag = {}
        for orig, final in zip(self.comps_used, new_comps):
            if final not in prev_comps and final not in series_spares:
                continue
            sm, tm = series_mag(final), tonight_cat.get(orig)
            if sm is not None and tm is not None and abs(float(sm) - float(tm)) > 0.0005:
                remag[final] = (float(tm), float(sm))
        new_check = self.check_used
        if prev_check and new_check and new_check != prev_check and (
                prev_check in aliases.get(new_check, ())
                or self._same_series_star(new_check, [prev_check], coords, index) == prev_check):
            renamed[new_check] = prev_check
            new_check = prev_check
        check_mag = None
        series_check = getattr(self, "_series_check", ("", ""))
        if new_check and new_check == series_check[0] and str(series_check[1]).strip():
            try:
                s_k, t_k = float(series_check[1]), float(self.check_mag.get())
                if abs(s_k - t_k) > 0.0005:
                    check_mag = (t_k, s_k)
            except (TypeError, ValueError):
                pass
        watch = {}
        tonight_watch = list(self.watch_xy)
        for name in tonight_watch:
            if name in series_watch:
                continue
            match = self._same_series_star(name, series_watch, coords, index, self.watch_xy)
            if match:
                watch[name] = match
        spares, spare_remag = {}, {}
        tonight_spares = [m[2] for m in self.spare_marks]
        for name in tonight_spares:
            if name in series_spares:
                continue
            match = self._same_series_star(name, [s for s in series_spares if s not in tonight_spares
                                                  and s not in new_comps], coords, index)
            if match:
                spares[name] = match
        for name in tonight_spares:
            final = spares.get(name, name)
            if final not in series_spares:
                continue
            sm = (before.get("spares") or {}).get(final)
            tm = (self.spare_catalog.get(name) or {}).get("mag")
            if sm is not None and tm is not None and abs(float(sm) - float(tm)) > 0.0005:
                spare_remag[final] = (float(tm), float(sm))
        # 2.2.17 (item 1, review M1): the series' catalog magnitudes are only valid in the series' band. An i′ night
        # added to a V (or g′) series keeps tonight's own magnitudes (only the names follow the series); a remag
        # across bands would zero-point the night on the wrong band (0.3-1 mag off, reported as SI).
        band_mismatch = None
        series_bands = {core.filter_band(o.filt) for o in self.observations if getattr(o, "filt", "")}
        tonight_band = core.filter_band(self._filter_code())
        if series_bands and series_bands != {tonight_band}:
            band_mismatch = ("/".join(sorted(core.band_label(b) for b in series_bands)), core.band_label(tonight_band))
            remag, check_mag, spare_remag = {}, None, {}
        if not (renamed or remag or check_mag or watch or spares or spare_remag):
            return None
        return {"band_word": self._band_word(),   # 2.2.17 (item 1): "V", or "i′" on a Sloan run
                "band_mismatch": band_mismatch,
                "comps": new_comps, "check": new_check, "renamed": renamed, "remag": remag, "check_mag": check_mag,
                "watch": watch, "spares": spares, "spare_remag": spare_remag}

    @staticmethod
    def _same_star_words(plan) -> list[str]:
        """2.2.14 (item 11): the plan in words, for the add-night question and the log."""
        out = [f"{a} is {b}" for a, b in plan["renamed"].items()]
        out += [f"watch star {a} is {b}" for a, b in (plan.get("watch") or {}).items()]
        out += [f"spare {a} is {b}" for a, b in (plan.get("spares") or {}).items()]
        bw = plan.get("band_word") or "V"   # 2.2.17 (item 1)
        if plan.get("band_mismatch"):
            sb, tb = plan["band_mismatch"]
            out.append(f"the series' catalog magnitudes are in {sb}, this night's in {tb}, so this night keeps its "
                       f"own {tb} magnitudes (only the names follow the series)")
        out += [f"{n}: the series' {bw} {s:.3f}, not tonight's {t:.3f}" for n, (t, s) in (plan.get("remag") or {}).items()]
        if plan.get("check_mag"):
            t, s = plan["check_mag"]
            out.append(f"check {plan['check']}: the series' {bw} {s:.3f}, not tonight's {t:.3f}")
        return out

    def _apply_same_star_plan(self, plan, night_points):
        """Give tonight's stars the series' names and catalog magnitudes, and re-derive the night with them (Gaia's
        synthetic V and Tycho's V differ by tenths, which would shift this night's zero point).
        2.2.14 (item 11): also the series' magnitude for a comp of the same name, the check star's series magnitude,
        and watch stars and spares measured under another name are moved to the series' name (one star, one name);
        every change is logged."""
        bw = plan.get("band_word") or "V"   # 2.2.17 (item 1)
        if plan.get("band_mismatch"):
            self.log(f"Same-star names: the series' catalog magnitudes are in {plan['band_mismatch'][0]}, this "
                     f"night's in {plan['band_mismatch'][1]}: names follow the series, magnitudes stay this night's.")
        catalog = self._comp_catalog()
        before = getattr(self, "_series_catalog_before", None) or {}
        for name, (_t, s) in (plan.get("remag") or {}).items():
            catalog[name] = s
        for old, new in plan["renamed"].items():
            if plan.get("band_mismatch") and catalog.get(old) is not None:
                catalog[new] = catalog[old]   # 2.2.17 (review M1): tonight's own band, under the series' name
                continue
            if new not in catalog:
                for src in (before.get("comps") or {}, before.get("spares") or {}):
                    if src.get(new) is not None:
                        catalog[new] = float(src[new])
                        break
        aliases = self._star_aliases()
        series_check = getattr(self, "_series_check", ("", ""))
        for old, new in plan["renamed"].items():
            for r in COMP_ROLES:
                if getattr(self, f"{r}_name").get().strip() == old:
                    getattr(self, f"{r}_name").set(new)
                    if catalog.get(new) is not None:
                        getattr(self, f"{r}_mag").set(f"{catalog[new]:.3f}")
            if self.check_name.get().strip() == old:
                self.check_name.set(new)
                if (series_check[0] == new and str(series_check[1]).strip()
                        and not plan.get("band_mismatch")):   # 2.2.17 (review M1): not across bands
                    self.check_mag.set(str(series_check[1]).strip())
            for _role, entry in list(self.star_bands.items()):
                if isinstance(entry, dict) and entry.get("name") == old:
                    entry["name"] = new
            if old in self.comp_coords and new not in self.comp_coords:
                self.comp_coords[new] = self.comp_coords[old]
            if old in self.seq_auids and new not in self.seq_auids:
                self.seq_auids[new] = self.seq_auids[old]
        # 2.2.14 (item 11): a comp of the series' own name takes the series' catalog magnitude.
        for name, (_t, s) in (plan.get("remag") or {}).items():
            for r in COMP_ROLES:
                if getattr(self, f"{r}_name").get().strip() == name:
                    getattr(self, f"{r}_mag").set(f"{s:.3f}")
        for name in plan["comps"]:
            if plan.get("band_mismatch"):
                break   # 2.2.17 (review M1): never overwrite the series' magnitudes with another band's
            if catalog.get(name) is not None and (name in (plan.get("remag") or {}) or name in plan["renamed"].values()):
                self.comp_catalog_mags[name] = float(catalog[name])
        if plan.get("check_mag"):
            self.check_mag.set(str(series_check[1]).strip())
        cbs = self.comp_band_sources if isinstance(getattr(self, "comp_band_sources", None), dict) else {}
        if cbs.get("sources"):
            # 2.2.17 (round 2): the Sloan sources follow the stars to their series names
            ren = plan["renamed"]
            cbs["sources"] = {ren.get(n, n): v for n, v in cbs["sources"].items()}
            cbs["comps"] = [ren.get(n, n) for n in cbs.get("comps") or []]
            if cbs.get("check"):
                cbs["check"] = ren.get(cbs["check"], cbs["check"])
        comps_changed = plan["comps"] != self.comps_used
        self.comps_used = list(plan["comps"])
        self.check_used = plan["check"]
        if comps_changed or plan.get("remag"):
            try:
                core.recompute_with_comps(night_points, self.comps_used, self.comps_used, catalog, aliases)
            except ValueError as exc:
                self.log(f"Same-star names: could not re-derive this night with the series' magnitudes: {exc}")
        for old, new in plan["renamed"].items():
            mag = catalog.get(new)
            self.log(f"{old} is {new} (same star): series name"
                     + (f" and {bw} {mag:.3f}" if mag is not None and new in self.comps_used
                        and not plan.get("band_mismatch") else "") + " used.")
        for name, (t, s) in (plan.get("remag") or {}).items():
            if name not in plan["renamed"].values():
                self.log(f"{name}: the series' catalog {bw} {s:.3f} used for this night, not tonight's {t:.3f} "
                         f"(the night moves {s - t:+.3f} mag with it).")
        if plan.get("check_mag"):
            t, s = plan["check_mag"]
            self.log(f"Check star {plan['check']}: the series' catalog {bw} {s:.3f} used, not tonight's {t:.3f}.")
        if plan.get("remag") or plan.get("check_mag"):
            self.log("  (The series' catalog magnitude wins, so every night stays on one zero point; Use comps… on the "
                     "Output page can re-derive the whole series with other comps.)")
        # 2.2.14 (item 11): watch stars and spares measured under another name join the series' star.
        for kind, mapping, key in (("Watch star", plan.get("watch") or {}, "w"), ("Spare", plan.get("spares") or {}, "s")):
            for old, new in mapping.items():
                for o in night_points:
                    store = o.watch_insts if key == "w" else o.spare_insts
                    if old in store:
                        value = store.pop(old)
                        store.setdefault(new, value)
                    pos = (o.pos or {}).get(key)
                    if isinstance(pos, dict) and old in pos:
                        p = pos.pop(old)
                        pos.setdefault(new, p)
                if key == "w":
                    if any(w["name"] == new for w in self.watch_list):
                        self.watch_list = [w for w in self.watch_list if w["name"] != old]
                    else:
                        for w in self.watch_list:
                            if w["name"] == old:
                                w["name"] = new
                    if old in self.watch_xy:
                        xy = self.watch_xy.pop(old)
                        self.watch_xy.setdefault(new, xy)
                else:
                    info = self.spare_catalog.pop(old, None)
                    if info is not None and new not in self.spare_catalog:
                        self.spare_catalog[new] = info
                    self.spare_marks = [(x, y, new if n == old else n) for x, y, n in self.spare_marks]
                self.log(f"{kind} {old} is {new} in the series (same star): measured under the series' name.")
        for name, (t, s) in (plan.get("spare_remag") or {}).items():
            if name in self.spare_catalog:
                self.spare_catalog[name]["mag"] = s
            self.log(f"Spare {name}: the series' catalog {self._band_word()} {s:.3f} kept, not tonight's {t:.3f}.")
        if plan.get("watch"):
            self._refresh_watch_box()

    def _adopt_series_names(self, night_points, prev_comps, prev_check):
        """Plan and apply at once (kept for callers and tests)."""
        plan = self._same_star_plan(prev_comps, prev_check)
        if plan:
            self._apply_same_star_plan(plan, night_points)
        return plan

    def _comp_catalog(self) -> dict:
        """Catalog magnitude of every comp and spare comp the series knows about."""
        cat = dict(self.comp_catalog_mags)
        for name, mag_text in [(getattr(self, f"{r}_name").get().strip(), getattr(self, f"{r}_mag").get())
                               for r in COMP_ROLES]:
            if name and name in self.comps_used and name not in cat:
                try:
                    cat[name] = float(mag_text)
                except ValueError:
                    pass
        for name, info in self.spare_catalog.items():
            if info.get("mag") is not None:
                cat.setdefault(name, float(info["mag"]))
        return cat

    def _fill_base(self, observations, meta: dict):
        """Give points from series saved before 1.8 the base values "Use comps..." needs."""
        names = list(meta.get("comps_used") or [])
        cat = dict(meta.get("comp_catalog_mags") or {})
        for name_key, mag_key in [(f"{r}_name", f"{r}_mag") for r in COMP_ROLES]:
            name = str(meta.get(name_key) or "").strip()
            if name and name in names and name not in cat:
                try:
                    cat[name] = float(meta.get(mag_key))
                except (TypeError, ValueError):
                    pass
        mags = [cat[n] for n in names if cat.get(n) is not None]
        mean = float(np.mean(mags)) if names and len(mags) == len(names) else None
        filled = core.fill_base_values(observations, mean)
        if filled:
            for name in names:
                if cat.get(name) is not None:
                    self.comp_catalog_mags.setdefault(name, float(cat[name]))

    def _star_aliases(self) -> dict:
        """2.2.3: {name: [other names]} for stars labeled from two catalogs (matched by position within 3")."""
        coords = {name: tuple(rd) for name, rd in self.comp_coords.items()}
        for name, info in self.spare_catalog.items():
            if info.get("ra") is not None and info.get("dec") is not None:
                coords.setdefault(name, (info["ra"], info["dec"]))
        return core.same_star_aliases(coords)

    def _spares_in_series(self) -> list[str]:
        seen = []
        for obs in self.observations:
            for name in obs.spare_insts:
                if name not in seen:
                    seen.append(name)
        return sorted(seen, key=lambda n: (self.spare_catalog.get(n, {}).get("mag") or 99.0, n))

    def watch_series(self, name: str):
        """A watch star's light curve from the series: the comp ensemble's catalog mean plus the star minus the
        ensemble, frame by frame (so it follows any Use comps… choice). Returns (obs, mags)."""
        out_obs, out_mag = [], []
        for o in self.observations:
            inst = o.watch_insts.get(name)
            if inst is None:
                inst = o.spare_insts.get(name)
            if inst is None or not math.isfinite(inst) or not math.isfinite(o.cmag) or not math.isfinite(o.comp_cat):
                continue
            if set(o.flag.split()) & {"cloud", "cloudedge", core.HAZE_FLAG, "drift", "noflux", "nocomp"}:   # 2.2.14
                continue
            out_obs.append(o)
            out_mag.append(o.comp_cat + inst - o.cmag)
        return out_obs, out_mag

    def show_watch(self):
        if not self.watch_list:
            messagebox.showinfo(APP_TITLE, "No watch stars yet. On the Photometry page pick Watch and click a star, "
                                           "or use Watch in Comp health or the field scan.")
            return
        WatchWindow(self)

    def choose_comps(self):
        if not self.observations:
            messagebox.showinfo(APP_TITLE, "There is no series loaded.")
            return
        if not self.comps_used:
            messagebox.showinfo(APP_TITLE, "This series does not record which comps it used; reduce it again in 1.8.")
            return
        UseCompsWindow(self)

    def apply_comp_selection(self, selection: list[str], confirmed: bool = False) -> bool:
        """Re-derive every magnitude from the chosen comps. Returns True when applied. confirmed (2.2.18 item 3,
        review): _comp_selection_ok has already asked."""
        if not confirmed and not self._comp_selection_ok(selection):
            return False
        catalog = self._comp_catalog()
        aliases = self._star_aliases()
        result = core.recompute_with_comps(self.observations, selection, self.comps_used, catalog, aliases)
        return self._comp_selection_applied(selection, result)

    def _comp_selection_ok(self, selection: list[str], extra_catalog: dict | None = None) -> bool:
        """The checks and questions before a comp selection is applied (True: go on). extra_catalog: catalog values
        typed in Use comps… and not applied yet (2.2.18 item 3, review: asked before anything is changed)."""
        catalog = {**self._comp_catalog(), **(extra_catalog or {})}
        aliases = self._star_aliases()
        missing = [n for n in selection if catalog.get(n) is None]
        if missing:
            messagebox.showerror(APP_TITLE, f"No catalog magnitude for {', '.join(missing)}.")
            return False
        no_base = sum(1 for o in self.observations if not math.isfinite(o.target_inst))
        if no_base:
            if not messagebox.askyesno(APP_TITLE, f"{no_base} point(s) lack the stored values needed to change comps "
                                                  "(their comp magnitudes were not saved) and would be left out. Continue?"):
                return False
        trial = []
        for o in self.observations:
            if not math.isfinite(o.target_inst) or not all(
                    math.isfinite(core.star_inst(o, n, self.comps_used, aliases)) for n in selection):
                trial.append(o.night)
        if trial:
            nights = sorted(set(trial))
            if not messagebox.askyesno(
                APP_TITLE,
                f"{len(trial)} point(s) on {', '.join(nights)} lack one of the chosen stars (it was not measured that "
                "night, or not in those frames). They will be flagged 'nocomp' and left out. Continue?"):
                return False
        return True

    def _comp_selection_applied(self, selection: list[str], result: dict) -> bool:
        self.active_comps = [] if set(selection) == set(self.comps_used) else list(selection)
        self._refresh_period()
        self.draw_output()
        used = ", ".join(selection)
        self.log(f"Comps in use: {used}. {result['done']} points re-derived"
                 + (f", {result['missing']} flagged nocomp." if result["missing"] else "."))
        self._log_zero_point_check()   # 2.2.14 (item 10)
        saved = ""
        if self.series_path:
            try:
                self._write_series(self.series_path)
                saved = f" Saved to {os.path.basename(self.series_path)}."
            except OSError as exc:
                messagebox.showwarning(APP_TITLE, f"Could not save the series: {exc}")
        self.status.set(f"Comps in use: {used}.{saved}")
        # 2.2.17 (item 4): in Transits mode the transit fit follows the new comps (refits on its page); after the status
        # line, so "Refitting…" is what it says (review).
        tp = getattr(self, "transit_page", None)
        if self.mode_key == "transits" and tp is not None:
            try:
                tp.comps_changed()
            except Exception:
                traceback.print_exc()
        return True

    def set_catalog_mags(self, changes: dict, check: str | None = None) -> bool:
        """2.2.18 (item 3): catalog magnitudes typed in Use comps… for the whole series ({star: V}); check: the check
        star's, or None. The series' values win over the Photometry boxes, so a wrong catalog value (HD 219134's
        rebuild took Gaia V 8.711 for the campaign's 8.728) is corrected here: every point is re-derived from its
        stored instrumental magnitudes (core.apply_catalog_mags), the boxes of the same stars follow, and an open
        series file is written. True when something changed."""
        changes = {str(k): float(v) for k, v in (changes or {}).items()}
        old_cat = self._comp_catalog()
        lines = []
        for name, mag in changes.items():
            was = old_cat.get(name)
            self.comp_catalog_mags[name] = mag
            if name in self.spare_catalog:
                self.spare_catalog[name]["mag"] = mag
            for role in COMP_ROLES:
                if getattr(self, f"{role}_name").get().strip() == name:
                    getattr(self, f"{role}_mag").set(f"{mag:.3f}")
            lines.append(f"{name} {was:.3f} → {mag:.3f}" if was is not None else f"{name} {mag:.3f}")
        result = {"changed": 0, "shift": {}}
        if changes and self.observations:
            # review: each night shifts by the catalog deltas of the comps IT was reduced with (night setup "comps",
            # stored since 2.2.3); a Use comps… selection applies to every night.
            pairs = {n: (old_cat[n], m) for n, m in changes.items() if old_cat.get(n) is not None}
            if self.active_comps:
                sets, default = {}, list(self.active_comps)
            else:
                default = list(self.comps_used)
                sets = {}
                for night in {o.night for o in self.observations}:
                    info = self.night_bin.get(night)
                    comps = info.get("comps") if isinstance(info, dict) else None
                    if isinstance(comps, list) and comps:
                        sets[night] = [str(c) for c in comps]
            result = core.apply_catalog_mags(self.observations, pairs, sets, default, self._star_aliases())
        if check is not None:
            used = self.check_used or self.check_name.get().strip()
            was = (getattr(self, "_series_check", None) or ("", ""))[1] or self.check_mag.get()
            self.check_mag.set(check)
            self._series_check = (used, check)
            lines.append(f"check {used or 'star'} {was or '—'} → {check}")
        if not lines:
            return False
        self._take_series_snapshot()   # a cancelled run later puts back these values, not the old ones
        self._zp_caution_cache = None
        shifts = sorted({round(v, 4) for v in result["shift"].values()})
        self.log("Catalog magnitudes changed for the whole series: " + "; ".join(lines)
                 + (f". {result['changed']} points re-derived (shift "
                    + ", ".join(f"{v:+.4f}" for v in shifts) + " mag)." if result["changed"] else "."))
        self._refresh_period()
        self.draw_output()
        saved = ""
        if self.series_path:
            try:
                self._write_series(self.series_path)
                saved = f" Saved to {os.path.basename(self.series_path)}."
            except OSError as exc:
                messagebox.showwarning(APP_TITLE, f"Could not save the series: {exc}")
        self.status.set("Catalog magnitudes changed." + saved)
        # review: a uniform shift of a night cannot change its relative flux, so the transit fit is not redone.
        return True

    def _zero_point_check(self):
        """2.2.14 (item 10): core.ensemble_zero_point_check for the comps in use (None when nothing to compare)."""
        selection = list(self.active_comps or self.comps_used)
        if len(selection) < 2 or not self.observations:
            return None
        try:
            return core.ensemble_zero_point_check(self.observations, selection, list(self.comps_used),
                                                  self._comp_catalog(), self._star_aliases())
        except Exception:
            return None

    def _zero_point_cautions(self) -> list[str]:
        """2.2.15 (item 2): the ensemble zero-point warning (logged and in Comp health since 2.2.14) as one Output
        caution, so it is also on the Output page and the saved PNG. Cached on what the check depends on."""
        selection = list(self.active_comps or self.comps_used)
        obs = self.observations
        if len(selection) < 2 or not obs:
            return []
        try:
            cat = self._comp_catalog()
            key = (id(obs), len(obs), obs[0].jd, obs[-1].jd, tuple(selection), tuple(self.comps_used),
                   tuple(sorted((n, cat.get(n)) for n in set(selection) | set(self.comps_used))),
                   hash(tuple(o.flag for o in obs)),   # review: any flag change (cloud -> haze too) re-checks
                   round(sum(o.mag for o in obs if math.isfinite(o.mag)), 6))
        except Exception:
            key = None
        if key is not None and self._zp_caution_cache and self._zp_caution_cache[0] == key:
            return list(self._zp_caution_cache[1])
        zp = self._zero_point_check()
        out = []
        if zp and zp.get("warn"):
            ref = ", ".join(zp["reference"])
            shifts = "; ".join(
                f"{self.night_label(n, short=True)}: {abs(zp['nights'][n]['shift']):.3f} mag "
                + ("fainter" if zp["nights"][n]["shift"] > 0 else "brighter") for n in sorted(zp["warn"]))
            out.append(f"Ensemble zero point: the {len(zp['selection'])} comps in use put the target off the level "
                       f"the comp(s) nearest its brightness give ({ref}) by more than {core.ZP_WARN_MAG:.3f} mag "
                       f"({shifts}). Compare both sets in Use comps… before reporting.")
        if key is not None:
            self._zp_caution_cache = (key, list(out))
        return out

    def _log_zero_point_check(self, night: str | None = None):
        """2.2.14 (item 10): log a warning when the comp ensemble moves a night's level by more than a few
        hundredths against the comps nearest the target's brightness (all nights, or just `night`)."""
        zp = self._zero_point_check()
        if not zp:
            return
        for night_key, line in zip(sorted(zp["warn"]), zp["text"]):
            if night is None or night_key == night:
                self.log("⚠ Ensemble zero point, " + self.label_nights(line))
    def show_extremum(self):
        """2.2.14 (item 13): the time of maximum / minimum window for the loaded series."""
        if not self.observations:
            messagebox.showinfo(APP_TITLE, "There is no light curve yet: run photometry or load a series.")
            return
        ExtremumWindow(self)

    def show_comp_health(self):
        if not self.observations:
            messagebox.showinfo(APP_TITLE, "There is no series loaded.")
            return
        names = list(self.comps_used) or ["comp"]
        aliases = self._star_aliases()
        # 2.2.15 (item 1): a planet transit dims the target only, so those frames still count for the comps.
        result = core.comp_health([o for o in self.observations
                                   if not (set(o.flag.split()) - {"nocomp", core.TRANSIT_FLAG})]
                                  or self.observations, names, self.check_used, self._spares_in_series(), aliases,
                                  night_sign=self._pier_night_sign())
        if result is None:
            messagebox.showinfo(
                APP_TITLE,
                "This series has no per-comp measurements yet. They are saved from version 1.6 on, "
                "so reduce the nights again to use the comp health check.",
            )
            return
        if result.get("too_few"):
            messagebox.showinfo(APP_TITLE, "The health check needs at least two stars (two comps, or a comp and a check star).")
            return
        result["positions"] = self._star_positions(result)
        result["zero_point"] = self._zero_point_check()   # 2.2.14 (item 10)
        CompHealthWindow(self, result)

    def _pier_night_sign(self) -> dict:
        """2.2.14 (item 20): +1 for nights whose before-flip side is the campaign's usual one, -1 when the night ran the
        other way round, so every night's flip step reads (other side) minus (usual side)."""
        pier = self._pier_by_night()
        befores = [p.get("before") for p in pier.values() if p.get("before") and p.get("after")]
        if not befores:
            return {}
        usual = max(set(befores), key=befores.count)
        return {n: (1.0 if p.get("before") == usual else -1.0 if p.get("after") == usual else 1.0)
                for n, p in pier.items() if p.get("before") and p.get("after")}

    def _star_positions(self, result) -> dict:
        """Median pixel position of each star in the health check, and its median distance from the target
        (the distance does not change with framing, rotation, or the meridian flip)."""
        names = list(self.comps_used)
        aliases = self._star_aliases()  # 2.2.8: a star's position under its other catalog name counts too
        out = {}
        for lab, kind in zip(result["labels"], result.get("kinds") or []):
            xs, ys, ds = [], [], []
            for o in self.observations:
                pos = o.pos or {}
                p = None
                if kind == "comp" and lab in names:
                    k = names.index(lab)
                    comps = pos.get("c") or []
                    p = comps[k] if k < len(comps) else None
                elif kind == "spare":
                    spares = pos.get("s") or {}
                    p = spares.get(lab)
                    if p is None:
                        p = next((spares[a] for a in aliases.get(lab, ()) if a in spares), None)
                elif kind == "check":
                    p = pos.get("k")
                t = pos.get("t")
                if p is None:
                    continue
                try:
                    xs.append(float(p[0]))
                    ys.append(float(p[1]))
                    if t is not None:
                        ds.append(math.hypot(float(p[0]) - float(t[0]), float(p[1]) - float(t[1])))
                except (TypeError, ValueError, IndexError):
                    continue
            if xs:
                out[lab] = (float(np.median(xs)), float(np.median(ys)), float(np.median(ds)) if ds else float("nan"))
        return out

    def open_export(self):
        if not self.observations:
            messagebox.showinfo(APP_TITLE, "Run photometry or load a series first.")
            return
        ExportWindow(self)

    def remove_night(self):
        if not self.observations:
            messagebox.showinfo(APP_TITLE, "There is no series loaded.")
            return
        nights = []
        for obs in self.observations:
            if obs.night not in nights:
                nights.append(obs.night)
        counts = {n: sum(1 for o in self.observations if o.night == n) for n in nights}
        listing = "\n".join(f"  {i + 1})  {n}   ({counts[n]} points)" for i, n in enumerate(nights))
        answer = simpledialog.askstring(APP_TITLE, f"Which night should be removed from the series?\n\n{listing}\n\nType its number:", parent=self)
        if not answer:
            return
        try:
            night = nights[int(answer.strip()) - 1]
        except (ValueError, IndexError):
            messagebox.showerror(APP_TITLE, f"'{answer}' is not one of the numbers listed.")
            return
        if not messagebox.askyesno(APP_TITLE, f"Remove the night of {self.night_label(night, short=True)} ({night}, {counts[night]} points) from the series? This cannot be undone."):
            return
        self.observations = [o for o in self.observations if o.night != night]
        self.legacy_mixed.discard(night)
        self.color_cal.pop(night, None)
        self.night_bin.pop(night, None)
        self._refresh_period()
        self.draw_output()
        saved = ""
        if self.series_path:
            try:
                self._write_series(self.series_path)
                saved = f" Saved to {os.path.basename(self.series_path)}."
            except OSError as exc:
                messagebox.showwarning(APP_TITLE, f"Could not save the series: {exc}")
        self.status.set(f"Removed the night of {self.night_label(night, short=True)}. {len(self.observations)} points left.{saved}")

    @staticmethod
    def _read_period_range(lo_var, hi_var):
        """2.2.18 (item 16): (min, max) days from two period boxes, or (None, reason)."""
        try:
            lo, hi = float(lo_var.get()), float(hi_var.get())
        except Exception:
            return None, "type the shortest and longest period in days"
        if not (math.isfinite(lo) and math.isfinite(hi) and 0 < lo < hi):
            return None, "the shortest period must be above 0 and below the longest"
        return (lo, hi), ""

    def _period_box_changed(self, force: bool = False):
        """2.2.18 (item 16): the Output page's Period search boxes refit on Enter, and on leaving a box when the
        range is not the one the period was last searched over (an unchanged box does nothing)."""
        rng, why = self._read_period_range(self.min_period, self.max_period)
        if rng is None:
            self.status.set(f"Period search: {why}.")
            return
        if not force and rng == getattr(self, "_period_range_used", None):
            return
        self._period_range_used = rng
        if not self.observations:
            return
        self.refit_period()
        self.log(f"Period search {rng[0]:g}–{rng[1]:g} d.")

    def _disc_period_changed(self, force: bool = False):
        """2.2.18 (item 16): Discovery's Period search boxes: Enter (or leaving a changed box) searches the finished
        scan's candidates again over the new range, in a background thread; no scan yet: kept for the next scan."""
        rng, why = self._read_period_range(self.disc_min_period, self.disc_max_period)
        if rng is None:
            self.status.set(f"Period search: {why}.")
            return
        self._remember_disc_range(rng)
        r = self.last_scan
        if self.mode_key != "discovery" or not r or r.get("light_curves") is None or r.get("jd") is None:
            return
        if not force and rng == getattr(self, "_disc_range_used", None):
            return
        self._disc_range_used = rng
        keys = list(dict.fromkeys(list(r.get("candidates") or [])[:40] + list((r.get("periods") or {}).keys())))
        self._disc_refit_gen = getattr(self, "_disc_refit_gen", 0) + 1
        gen = self._disc_refit_gen
        self.status.set(f"Searching the candidates' periods over {rng[0]:g}–{rng[1]:g} d…")
        jd = np.asarray(r["jd"], dtype=float)
        curves = np.asarray(r["light_curves"], dtype=float)

        def work():
            periods = {}
            for i in keys:
                try:
                    good = np.isfinite(curves[:, i])
                    ls = core.lomb_scargle(jd[good], curves[good, i], rng[0], rng[1])
                except Exception:
                    ls = None
                if ls:
                    periods[i] = {"period": ls["period_days"], "power": ls["power"], "ls": ls}
            self.call_ui(self._disc_periods_done, r, gen, rng, periods)

        threading.Thread(target=work, daemon=True).start()

    def _remember_disc_range(self, rng) -> None:
        if list(self.settings.get("disc_period_range") or []) != [rng[0], rng[1]]:
            self.settings["disc_period_range"] = [rng[0], rng[1]]
            try:
                self._save_settings()
            except Exception:
                pass

    def _disc_periods_done(self, r, gen, rng, periods):
        if gen != getattr(self, "_disc_refit_gen", 0) or r is not self.last_scan:
            return   # a newer range or another scan since
        r["periods"] = periods
        r["period_range"] = rng
        view = getattr(self, "scan_view", None)
        if view is not None and getattr(view, "r", None) is r:
            try:
                view._fill_table()
                view.draw()
            except Exception:
                traceback.print_exc()
        self.log(f"Field scan: candidates' periods searched again over {rng[0]:g}–{rng[1]:g} d.")
        self.status.set(f"Period search {rng[0]:g}–{rng[1]:g} d done.")

    def refit_period(self):
        if not self.observations:
            return
        if len(self._usable()) < 8:
            messagebox.showinfo(APP_TITLE, "Need at least 8 usable points.")
        self._refresh_period()
        self.draw_output()

    @staticmethod
    def _report_points(points: list) -> list:
        """2.2.15 (item 1, review): report points without the frames flagged in a planet transit (always)."""
        return [o for o in points if core.TRANSIT_FLAG not in o.flag.split()]

    def _sloan_report_note(self, points=None) -> str:
        """2.2.17 (item 1): for a Sloan run, the AAVSO report notes say where the comp/check magnitudes came from
        ("; comp/check mags SI from APASS DR9 2; Gaia DR3 G/BP-RP (+/-0.04) 1"), ASCII. '' otherwise.
        Review S6: only when every reported point has the same Sloan filter. Round 2: the sources are kept per night in
        the series (night setup "band_sources"), so a closed and reloaded series still gets its note; nights with the
        same sources are described once, otherwise night by night. A night with no record (a series saved before
        2.2.17, or a comp set changed with Use comps) leaves the note out rather than guess."""
        pts = list(points or [])
        filts = {getattr(o, "filt", "") for o in pts}
        if len(filts) != 1:
            return ""
        filt = next(iter(filts))
        if not core.is_sloan(filt):
            return ""
        used = set(self.active_comps or self.comps_used or [])
        live = self.comp_band_sources if isinstance(getattr(self, "comp_band_sources", None), dict) else {}
        per_night = {}
        for night in sorted({o.night for o in pts}):
            info = self._night_info(night).get("band_sources")
            if not isinstance(info, dict) and live.get("night") == night:
                info = live
            if not isinstance(info, dict) or info.get("band") != filt:
                return ""
            srcs = dict(info.get("sources") or {})
            comps = set(info.get("comps") or [n for n in srcs if n != info.get("check")])
            if used and comps != used:
                return ""   # the comps used now are not the ones these sources describe
            per_night[night] = srcs

        def describe(srcs):
            counts: dict = {}
            for text in srcs.values():
                if not text:
                    key = "typed"
                elif "from Gaia G and BP-RP" in text:
                    key = "Gaia DR3 G/BP-RP (" + text.rsplit(",", 1)[-1].strip().replace("\u00b1", "+/-") + ")"
                elif "GSPC" in text:
                    key = "Gaia DR3 GSPC"
                else:
                    key = text.split(",")[0]
                counts[key] = counts.get(key, 0) + 1
            return "; ".join(f"{k} {n}" for k, n in counts.items())

        texts = {n: describe(srcs) for n, srcs in per_night.items()}
        if not texts or not all(texts.values()):
            return ""
        if len(set(texts.values())) == 1:
            what = next(iter(texts.values()))
        else:
            what = " / ".join(f"{n}: {t}" for n, t in texts.items())
        lead = "; " if self.notes.get().strip() else ""
        return f"{lead}comp/check mags {filt} from {what}"

    def _report_text(self, observations: list | None = None) -> str:
        # Name the stars the reduction actually used, not whatever is in the form now.
        names = self.active_comps or self.comps_used or [self.comp_name.get().strip() or "comp"]
        # 2.2.13 (John: "keep AAVSO happy"): AAVSO sequence stars appear under their AUID in the report.
        auid = self.seq_auids
        # 2.2.14 (item 7): a star the run named only "check"/"comp1" carries the name typed when the report was saved.
        given = getattr(self, "report_names", {}) or {}
        comps = [core.Star(auid.get(name, given.get(name, name)), 0, 0) for name in names]
        check_name = self.check_used or self.check_name.get().strip() or "check"
        check_name = given.get(check_name, check_name)
        check = (core.Star(auid.get(check_name, check_name), 0, 0)
                 if any(o.kmag is not None for o in self.observations) else None)
        # 2.2.15 (item 1, review): frames in a planet transit never go in a report (campaign rule), even with
        # "Leave flagged points out" unticked.
        points = self._report_points(self._usable() if observations is None else observations)
        return core.aavso_report(
            points,
            self.observer.get(),
            self.star_id.get() or "target",
            comps,
            check,
            self.chart.get(),
            software=f"{APP_TITLE} {APP_VERSION}",
            notes=self.notes.get() + self._sloan_report_note(points),   # 2.2.17 (item 1): '' unless Sloan
            filt=self._filter_code(),
            scint=self._scint_setup(),
        )

    def save_aavso(self):
        if not self.observations:
            messagebox.showinfo(APP_TITLE, "Run photometry first.")
            return
        problems = []
        if not self.observer.get().strip():
            problems.append("AAVSO observer code is empty")
        if not self.chart.get().strip():
            problems.append("Chart ID is empty")
        if not self.star_id.get().strip():
            problems.append("Star ID is empty")
        good = core.suggest_star_name(self.star_id.get())
        before = self.star_id.get()
        rename = False
        if good:
            if messagebox.askyesno(APP_TITLE, f"The Star ID is \u201c{self.star_id.get().strip()}\u201d. AAVSO spells it "
                                              f"\u201c{good}\u201d, and WebObs may not match the other spelling to the "
                                              f"star.\n\nUse \u201c{good}\u201d in the report?"):
                # 2.2.18 (item 21): the report and its file name use AAVSO's spelling, but the Star ID keeps it only
                # when the report is saved; a cancel anywhere below puts the old name back.
                rename = True
                self.star_id.set(good)
        saved = False
        try:
            saved = self._save_aavso_report(problems)
        finally:
            if rename and not saved:
                self.star_id.set(before)
            elif rename:
                self.log(f"Star ID set to \u201c{good}\u201d (AAVSO's spelling), as used in the saved report.")

    def _save_aavso_report(self, problems: list) -> bool:
        """The rest of save_aavso (2.2.18 item 21: split off so a cancel can undo the Star ID spelling). True when
        the report was written."""
        if len({o.filt for o in self._usable()}) > 1:
            problems.append("the series mixes filters")
        # 2.2.5: two or more comps make an ensemble report, which AAVSO accepts only with a check star.
        if len(self.active_comps or self.comps_used or []) > 1 and not any(o.kmag is not None for o in self._usable()):
            problems.append("ensemble report (more than one comp) without a check star; AAVSO requires one")
        # 2.2.14 (item 7): a comp without a real name (the run called it comp1) goes out as that placeholder.
        given = getattr(self, "report_names", {}) or {}
        for name in self.active_comps or self.comps_used or []:
            if placeholder_star_name(given.get(name, name)) and not self.seq_auids.get(name):
                problems.append(f"a comp has no star name (the report would call it “{name}”)")
        if problems:
            if not messagebox.askyesno(APP_TITLE, "Before you upload:\n  • " + "\n  • ".join(problems) + "\n\nSave the report anyway?"):
                return False
        if not self._name_report_check():
            return False
        scope = self._save_scope("AAVSO report")
        if scope is None:
            return False
        points = [o for o in self._usable() if scope == "series" or o.night == self.highlight_night]
        in_transit = len(points) - len(self._report_points(points))
        points = self._report_points(points)   # 2.2.15 (item 1, review)
        if in_transit:
            self.log(f"AAVSO report: {in_transit} frame{'s' if in_transit != 1 else ''} in a planet transit left out "
                     "of the report.")
        if not points:
            messagebox.showinfo(APP_TITLE, "No usable points to report for that choice.")
            return False
        # 2.2.18 (item 10): saving never warns about duplicates any more; Open AAVSO WebObs… does (an upload is what
        # duplicates, and many reports are never uploaded: HD 219134's go to Dr. Elliott).
        self._save_one_night = scope == "night"
        try:
            path = self.output_path(
                self._family_names()[0], title="Save AAVSO extended report", defaultextension=".txt",
                filetypes=[("Text", "*.txt"), ("All", "*.*")], family=True)
        finally:
            self._save_one_night = False
        if not path:
            return False
        with core.AsciiWriter(path) as fh:
            fh.write(self._report_text(points))
        self._note_report(points, path, scope)
        self.saved_notice(path, "Upload with the AAVSO WebObs file tool after you check the observer code, chart, and "
                                "comparison magnitude.")
        self.offer_upload(path, "Open AAVSO WebObs…", core.AAVSO_WEBOBS_URL, "AAVSO WebObs")   # 2.2.15 (item 8)
        return True

    def _name_report_check(self) -> bool:
        """2.2.14 (item 7): an AAVSO report must not go out with KNAME = "check" (13 Oct: the check marker never got
        its catalog name). Ask for the star's name; no name, no report. True when the report may be saved."""
        if not any(o.kmag is not None for o in self.observations):
            return True
        used = self.check_used or self.check_name.get().strip() or "check"
        if not hasattr(self, "report_names"):
            self.report_names = {}
        shown = self.report_names.get(used, used)
        if not placeholder_star_name(shown) or self.seq_auids.get(shown):
            return True
        guess = ""
        star = self._chart_star_at(self.check_xy) if self.check_xy is not None and self.chart_placed else None
        if star is not None:
            guess = str((star.get("seq") or {}).get("auid") or star.get("auid") or star.get("label") or "")
        if not guess and not placeholder_star_name(self.check_name.get()):
            guess = self.check_name.get().strip()
        self.log(f"AAVSO report: the check star has no name (the run called it “{used}”).")
        answer = simpledialog.askstring(
            APP_TITLE,
            f"The check star has no name in this series (the report would say KNAME = {used}), and AAVSO needs to "
            "know which star it is.\n\nType its AUID or catalog name (for example 000-BJR-417 or TYC 4006-956-1):",
            initialvalue=guess, parent=self)
        name = " ".join((answer or "").split())
        if not name or placeholder_star_name(name):
            messagebox.showinfo(APP_TITLE, "Report not saved: it needs the check star's name (its AUID or catalog "
                                           "name). Save it again and type the name.")
            return False
        self.report_names[used] = name
        self.log(f"AAVSO report: check star {used} reported as {name}.")
        return True

    # ---- 2.2.3: save just the highlighted night, and duplicate-upload warnings --------------
    def _save_scope(self, what: str) -> str | None:
        """'night' or 'series' for a Save, or None when cancelled. Asks only when a night is highlighted in a
        series of more than one night."""
        nights = {o.night for o in self.observations}
        if not self.highlight_night or self.highlight_night not in nights or len(nights) < 2:
            return "series"
        answer = messagebox.askyesnocancel(
            APP_TITLE,
            f"{self._night_label(self.highlight_night)} is highlighted.\n\n"
            f"Yes: save the {what} for just that night.\n"
            f"No: save the {what} for the whole series ({len(nights)} nights).\n"
            "Cancel: don't save.")
        if answer is None:
            return None
        return "night" if answer else "series"

    def _night_label(self, night: str) -> str:
        return self.night_label(night)

    def night_label(self, night: str, short: bool = False, with_target: bool = True, jd: float | None = None) -> str:
        """2.2.7: a night as an observer says it: "K2-113 b · night of 5–6 Oct 2026" (short: "5–6 Oct 2026"). The
        evening date is local, from the site longitude (UTC when the site is blank), so a night that runs past
        midnight is one night. The JD key ("JD2461319") stays the key everywhere else."""
        if not night or not str(night).startswith("JD"):
            return str(night or "")
        if jd is None:
            pts = [o.jd for o in self.observations if o.night == night and o.jd is not None and math.isfinite(o.jd)]
            try:
                jd = min(pts) if pts else float(str(night)[2:]) + 0.5
            except ValueError:
                return str(night)
        try:
            lon = float(self.lon.get()) if self.lon.get().strip() else 0.0
        except ValueError:
            lon = 0.0
        if not math.isfinite(lon) or abs(lon) > 360:
            lon = 0.0
        from datetime import timedelta
        try:
            local = core.jd_to_datetime_utc(jd) + timedelta(hours=lon / 15.0)
        except (OverflowError, ValueError):
            return str(night)
        evening = local.date() if local.hour >= 12 else (local - timedelta(days=1)).date()
        morning = evening + timedelta(days=1)
        if evening.month == morning.month:
            span = f"{evening.day}–{morning.day} {morning:%b} {morning.year}"
        elif evening.year == morning.year:
            span = f"{evening.day} {evening:%b}–{morning.day} {morning:%b} {morning.year}"
        else:
            span = f"{evening.day} {evening:%b} {evening.year}–{morning.day} {morning:%b} {morning.year}"
        if short:
            return span
        target = ""
        if with_target:
            target = (self.planet_name.get().strip() if self.mode_key == "transits" else "") or self.star_id.get().strip()
        return (f"{target} · " if target else "") + f"night of {span}"

    def label_nights(self, text: str) -> str:
        """2.2.7: replace the JD keys in a message with human night labels (the JD in brackets)."""
        import re
        return re.sub(r"\bJD\d{7}\b", lambda m: f"night of {self.night_label(m.group(0), short=True)} ({m.group(0)})",
                      str(text))

    def set_report_note(self, text: str):
        """2.2.7: a line on the Input page when the Transit fit page shows an EXOTIC report, not this setup."""
        if hasattr(self, "report_note"):
            self.report_note.set(text)

    def _note_report(self, points: list, path: str, scope: str):
        stamp = time.strftime("%Y-%m-%d %H:%M")
        for night in sorted({o.night for o in points}):
            # 2.2.18 (item 10): which upload page it is for, its full path and the save time (the last report saved
            # for a page is found here, also in a later session).
            self.reports_saved.setdefault(night, []).append(
                {"file": os.path.basename(path), "date": stamp, "scope": scope, "to": "webobs",
                 "path": os.path.abspath(path), "stamp": time.time()})
        if self.series_path:
            try:
                self._write_series(self.series_path)
            except OSError:
                pass

    def save_csv(self):
        if not self.observations:
            messagebox.showinfo(APP_TITLE, "Run photometry first.")
            return ""
        scope = self._save_scope("light-curve CSV")
        if scope is None:
            return ""
        self._save_one_night = scope == "night"
        try:
            path = self.output_path(self._family_names()[1], title="Save light-curve CSV", defaultextension=".csv",
                                    filetypes=[("CSV", "*.csv")], family=True)
        finally:
            self._save_one_night = False
        if path:
            obs = [o for o in self.observations if scope == "series" or o.night == self.highlight_night]
            core.write_curve_csv(path, obs, self.night_notes, self._scint_setup())
            self.status.set(f"Wrote {path}")
            self._last_csv_scope = scope   # 2.2.18 (item 18): the Transit page's fit CSV joins this numbered set
        return path or ""

    def save_png(self):
        if self.out_canvas is None or not self.observations:
            messagebox.showinfo(APP_TITLE, "Run photometry first.")
            return
        # The PNG is the view on screen: with a night highlighted in a series it is that night's picture, and it
        # joins that night's set of files.
        nights = {o.night for o in self.observations}
        self._save_one_night = bool(self.highlight_night and self.highlight_night in nights and len(nights) > 1)
        try:
            path = self.output_path(self._family_names()[2], title="Save plot PNG", defaultextension=".png",
                                    filetypes=[("PNG", "*.png")], family=True)
        finally:
            self._save_one_night = False
        if path:
            self._save_png_with_header(path)
            self.status.set(f"Wrote {path}")

    def _save_png_with_header(self, path: str):
        """2.2.3: the Output plots with the summary above them, laid out as on the page: the Series, Period and Color
        boxes side by side, the per-night table under them, then the plots."""
        import io
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg

        snap = getattr(self, "_summary_snapshot", None)
        stamp = f"Created {core.dtg()} · {APP_TITLE} {APP_VERSION}"
        buf = io.BytesIO()
        if not snap:
            mark = self.out_fig.text(0.99, 0.005, stamp, fontsize=7, color="#7a8a94", ha="right", va="bottom")
            try:
                self.out_fig.savefig(path, dpi=140, format="png")
            finally:
                mark.remove()
                self.out_canvas.draw_idle()
            return
        self.out_fig.savefig(buf, dpi=140, format="png")
        buf.seek(0)
        import matplotlib.image as mpimg
        plot = mpimg.imread(buf)
        ph, pw = plot.shape[0], plot.shape[1]
        dpi = 140
        # 2.2.14 (item 9): the three boxes side by side at their own widths (a 9 pt monospace character is about
        # 0.075 in); the picture widens when they need more room than the plots, instead of overlapping.
        box_w = [max(len(t) for t in (snap[k] or ["—"])) * 0.075 + 0.5 for k in ("series", "period", "color")]
        mono = {"family": "monospace", "size": 9}
        box_lines = max(len(snap["series"]), len(snap["period"]), len(snap["color"]) or 1)
        rows = snap["rows"]
        cols = [(c, label) for c, label, _w in self.SUMMARY_COLUMNS
                if c != "sent" and (c != "note" or any(r.get("note") for r in rows))]   # 2.2.18 (item 10): not in the PNG
        # 2.2.14 review: the night table counts too (its right-hand columns were clipped), up to 16 in for long notes.
        table_in = min(16.0, (sum(self._col_chars(c, rows, label) + 2 for c, label in cols)) * 0.075 + 0.2)
        width_in = max(pw / dpi, 0.1 + sum(box_w), table_in)
        line_in = 0.17
        box_h = (box_lines + 1.6) * line_in
        table_h = (len(rows) + 1.5) * line_in
        # 2.2.14 review: the cautions wrapped to the picture's width (one long line was clipped at the right).
        caution_lines = []
        if snap.get("cautions"):
            from transit_ui import wrap_to_width
            for c in snap["cautions"]:
                caution_lines += wrap_to_width("! " + c, 0.97 * width_in * dpi, 8, dpi=dpi)
            caution_lines = caution_lines[:14]
        extra = (len(caution_lines) * 0.8 + 0.7 if caution_lines else 0) * line_in + 0.55
        head_in = box_h + table_h + extra
        fig = Figure(figsize=(width_in, ph / dpi + head_in), dpi=dpi, facecolor="white")
        FigureCanvasAgg(fig)
        total = ph / dpi + head_in
        title = f"{self.star_id.get().strip() or 'Target'}   {APP_TITLE} {APP_VERSION}   Created {core.dtg()}"
        fig.text(0.01, 1 - 0.25 / total, title, fontsize=11, weight="bold", va="top")
        top = 1 - 0.5 / total
        x = 0.01
        for (key, label), w_in in zip((("series", "Series"), ("period", "Period"), ("color", "Color")), box_w):
            lines = snap[key] or ["—"]
            fig.text(x, top, label, fontsize=9, weight="bold", va="top")
            fig.text(x, top - line_in * 1.1 / total, "\n".join(lines), va="top", fontdict=mono,
                     bbox={"boxstyle": "square,pad=0.4", "facecolor": "#f7f7f7", "edgecolor": "#bbbbbb"})
            x += w_in / width_in
        y = top - box_h / total
        header = "  ".join(f"{label:<{self._col_chars(c, rows, label)}}" for c, label in cols)
        body = ["  ".join(f"{str(r.get(c, '')):<{self._col_chars(c, rows, label)}}" for c, label in cols) for r in rows]
        fig.text(0.01, y, header, va="top", fontdict={**mono, "weight": "bold"})
        fig.text(0.01, y - line_in * 1.2 / total, "\n".join(body), va="top", fontdict=mono)
        y -= table_h / total
        if self.spread_text.get():
            fig.text(0.01, y + 0.25 * line_in / total, self.spread_text.get().split("     Click")[0], va="top", fontsize=8.5)
        if caution_lines:
            fig.text(0.01, y - line_in / total, "\n".join(caution_lines), va="top", fontsize=8, color="#a32020",
                     linespacing=1.15)
        ax = fig.add_axes([0, 0, (pw / dpi) / width_in, (ph / dpi) / total])
        ax.imshow(plot)
        ax.axis("off")
        fig.savefig(path, dpi=dpi)

    @staticmethod
    def _col_chars(col, rows, label) -> int:
        return max([len(label)] + [len(str(r.get(col, ""))) for r in rows])

    def write_demo(self):
        folder = filedialog.askdirectory(title="Folder for a small synthetic FITS set")
        if not folder:
            return
        try:
            demo = core.write_demo_set(folder)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not write the demo set: {exc}")
            return
        self.bias_dir.set(demo["bias"])
        self.dark_dir.set(demo["dark"])
        self.flat_dir.set(demo["flat"])
        self.light_dir.set(demo["lights"])
        self.star_id.set("DEMO VAR")
        self.comp_name.set("DEMO COMP")
        self.comp_mag.set("11.50")
        self.check_name.set("DEMO CHECK")
        self.check_mag.set("12.20")
        for r in COMP_ROLES[1:]:
            getattr(self, f"{r}_name").set("")
            getattr(self, f"{r}_mag").set("")
        self.chart.set("DEMO")
        self._loading_meta = True   # 2.2.18 (item 11): the demo's observer code is not saved as yours
        try:
            self.observer.set("TST")
        finally:
            self._loading_meta = False
        self.ra_hours.set("0.75")
        self.dec_deg.set("40.0")
        self.binning.set(1)
        self.pattern.set("RGGB")
        self.min_period.set(0.03)
        self.max_period.set(1.0)
        self.rejected.clear()
        self.blink_paths = []
        self._blink_loaded_from = None
        self.scan_folders()
        self.target_xy = demo["target_xy"]
        self.comp_xy = demo["comp_xy"]
        for r in COMP_ROLES[1:]:
            setattr(self, f"{r}_xy", None)
        self.check_xy = demo["check_xy"]
        self.radius.set(4)
        self.sky_in.set(7)
        self.sky_out.set(11)
        self._update_pick_label()
        self.status.set("Demo set written. On the debayered frame: target (35, 45), comp (70, 50), check (55, 75).")
        messagebox.showinfo(
            APP_TITLE,
            "Demo FITS written and folders filled in. The stars are already marked.\n\n"
            "After Calibrate (which also debayers), they sit at\n"
            "target (35, 45), comparison (70, 50), check (55, 75)\n"
            "because superpixel debayer halves the coordinates.\n\n"
            f"The demo variable has a {demo['period_days']} d period.",
        )


class CompHealthWindow:
    """Each comparison star (and the check star) measured against the others, night by night; on a single night,
    frame by frame through the night (2.2.2)."""

    def __init__(self, app, result):
        win = Toplevel(app)
        win.title(f"{APP_TITLE} — comparison star health")
        self.app = app
        nights = result["nights"]
        one_night = len(nights) < 2
        top = ttk.Frame(win, style="Card.TFrame", padding=10)
        top.pack(fill=BOTH, expand=True)
        # Buttons first, so they stay in view however small the window gets (2.2.2).
        nav = ttk.Frame(top, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", pady=(6, 0))
        ttk.Button(nav, text="Close", command=win.destroy).pack(side=RIGHT)
        ttk.Button(nav, text="Watch selected spare", command=self.watch_selected).pack(side=LEFT)
        ttk.Label(top, text="Comparison star health", font=(core.UI_FONT, 15, "bold"), style="Card.TLabel").pack(anchor=W)
        ttk.Label(
            top,
            text="Each star is measured against the average of the others in the same frame, so clouds and airmass "
                 "cancel. A steady star stays flat from night to night. A star that drifts or jumps is quietly "
                 "variable or affected by something local (a neighbour, a flat-field defect) and should be replaced."
                 + ("\nOnly one night is loaded, so this shows each star through the night instead; the night-to-night "
                    "check needs two or more nights." if one_night else ""),
            style="Hint.TLabel", wraplength=1050, justify=LEFT,
        ).pack(anchor=W, pady=(2, 8))
        rows = result["summary"]
        positions = result.get("positions") or {}
        table_box = ttk.Frame(top, style="Card.TFrame")
        table_box.pack(fill=X, side="bottom", pady=(6, 0))
        cols = ("star", "role", "spread", "scatter", "flip", "pier", "nights", "pos", "dist", "verdict")
        headings = {"star": "Star", "role": "Role", "spread": "Night offset", "scatter": "Scatter in night",
                    "flip": "Flip step", "pier": "Both pier sides", "nights": "Nights", "pos": "Pixel (x, y)",
                    "dist": "From target", "verdict": "Verdict"}
        table = ttk.Treeview(table_box, columns=cols, show="headings", height=min(max(len(rows), 3), 12))
        # 2.2.13 (John): the horizontal scroll bar shows only when the columns do not fit (as on the Transit fit page).
        self.hbar = ttk.Scrollbar(table_box, orient="horizontal", command=table.xview)
        table.configure(xscrollcommand=self._table_xview)
        for col in cols:
            table.heading(col, text=headings[col], anchor=W)
            table.column(col, anchor=W)
        for row in rows:
            sp, sc = row["night_spread"], row["scatter"]
            st = row.get("flip_step", float("nan"))
            pos = positions.get(row["label"])
            table.insert("", END, values=(row["label"].replace(" (check)", ""), row.get("kind", ""),
                                          f"{sp:.4f}" if math.isfinite(sp) else "—",
                                          f"{sc:.4f}" if math.isfinite(sc) else "—",
                                          f"{st:+.3f}" if isinstance(st, float) and math.isfinite(st) else "—",
                                          row.get("pier") or "—",   # 2.2.14 (item 20)
                                          row["nights"],
                                          f"({pos[0]:.0f}, {pos[1]:.0f})" if pos else "—",
                                          f"{pos[2]:.0f}" if pos and math.isfinite(pos[2]) else "—",
                                          row["verdict"]))
        # 2.2.13 (John): columns sized to their contents (heading or widest value).
        fit_tree_columns(table, headings, firm=True)
        table_box.columnconfigure(0, weight=1)
        table.grid(row=0, column=0, sticky="ew")
        self.hbar.grid(row=1, column=0, sticky="ew")
        self.hbar.grid_remove()
        self.table = table
        # 2.2.13 (John): selecting a row highlights that star's curve in the plot.
        self.artists = {}
        table.bind("<<TreeviewSelect>>", lambda e: self.highlight())
        self.table_rows = {iid: row for iid, row in zip(table.get_children(), rows)}
        zp = result.get("zero_point")
        if zp and zp.get("warn"):
            # 2.2.14 (item 10): the ensemble moves the night's level against the comps nearest the target's brightness.
            ttk.Label(top, text="⚠ Ensemble zero point: " + "\n⚠ ".join(app.label_nights(t) for t in zp["text"]),
                      style="Warn.TLabel", wraplength=1050, justify=LEFT).pack(side="bottom", anchor=W, pady=(4, 0))
        pier_text = self._pier_text(rows)
        if pier_text:
            ttk.Label(top, text=pier_text, style="Hint.TLabel", wraplength=1050,
                      justify=LEFT).pack(side="bottom", anchor=W, pady=(4, 0))
        if result.get("excluded"):
            ttk.Label(top, text="Left out of the reference so their drift does not echo in the steady stars: "
                                + ", ".join(result["excluded"]) + ".", style="Hint.TLabel").pack(side="bottom", anchor=W)
        if positions:
            ttk.Label(top, text="Pixel (x, y) is the median position over all frames; framing and the meridian flip move it. "
                                "'From target' does not change with framing, so it locates a star in the field.",
                      style="Hint.TLabel", wraplength=1050, justify=LEFT).pack(side="bottom", anchor=W)
        if any(r.get("kind") == "spare" for r in rows):
            ttk.Label(top, text="Spares are extra stars measured every frame. Use comps… (Output page) can swap a "
                                "steady spare in for a SUSPECT comp without reducing again.",
                      style="Hint.TLabel", wraplength=1050, justify=LEFT).pack(side="bottom", anchor=W, pady=(4, 0))
        self.result = result
        if Figure is None:
            fit_to_content(win)
            return
        fig = make_constrained_figure((10, 4.2))
        ax = fig.add_subplot(111)
        self.ax = ax
        kinds = result.get("kinds") or ["comp"] * len(result["labels"])
        if one_night and result.get("resid") is not None and len(result.get("jd", [])):
            self.artists = self._draw_one_night(ax, result, kinds)
        else:
            xs = np.arange(len(nights))
            for k, lab in enumerate(result["labels"]):
                vals = [result["nightly"][lab].get(n, np.nan) for n in nights]
                finite = [v for v in vals if math.isfinite(v)]
                ref = float(np.median(finite)) if finite else 0.0
                spare = kinds[k] == "spare"
                line, = ax.plot(xs, [v - ref for v in vals], "o--" if spare else "o-", lw=0.9 if spare else 1.6,
                                ms=4 if spare else 6, alpha=0.7 if spare else 1.0,
                                label=lab + (" (spare)" if spare else ""))
                self.artists[lab] = [line]
            ax.axhline(0, color="#888888", lw=0.8)
            ax.set_xticks(xs)
            ax.set_xticklabels([app.night_label(n, short=True) if hasattr(app, "night_label") else n for n in nights],
                               rotation=20, fontsize=8, ha="right")
            ax.invert_yaxis()
            ax.set_ylabel("Night median minus\nits own median (mag)")
            ax.set_title("Night-to-night behaviour of each star (should be flat)")
            ax.grid(True, alpha=0.3)
        self._legend()
        # Remember each artist's look, so a highlight can be undone exactly.
        self._looks = {a: (a.get_linewidth(), a.get_alpha(), a.get_zorder(), a.get_markersize())
                       for arts in self.artists.values() for a in arts}
        canvas = FigureCanvasTkAgg(fig, master=top)
        canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        canvas.draw_idle()
        self.fig = fig
        self.canvas = canvas
        fit_to_content(win)

    @staticmethod
    def _pier_text(rows) -> str:
        """2.2.14 (item 20): which stars read the same on both pier sides (good comps for a campaign that flips) and
        which step at the flip, from every night with both sides."""
        steady = [r["label"].replace(" (check)", "") for r in rows if r.get("pier_kind") == "steady"]
        steps = [f"{r['label'].replace(' (check)', '')} ({r['pier'].split(' ', 1)[1]})" for r in rows
                 if r.get("pier_kind") == "steps"]
        if not steady and not steps:
            return ""
        text = "Both pier sides (flip step after minus before, median over the nights with both sides): "
        if steady:
            text += "steady on both sides, good comps for the campaign: " + ", ".join(steady) + ". "
        if steps:
            text += ("steps at the flip, better not as comps (or use 'Pier side as segments'): " + ", ".join(steps)
                     + ". A target that steps while steady comps do not points at the field position (flat, a ghost "
                       "or the star's halo), not the comps.")
        return text

    def _legend(self, selected: str | None = None):
        """2.2.13 (John): the legend in a column on the right of the plot, off the curves; the highlighted star's
        entry in bold."""
        ax = self.ax
        handles, labels = ax.get_legend_handles_labels()
        if not handles:
            return
        leg = ax.legend(handles, labels, fontsize=8, frameon=False, loc="upper left", bbox_to_anchor=(1.01, 1.0),
                        borderaxespad=0.0, ncol=1 if len(labels) <= 14 else 2)
        for text in leg.get_texts():
            name = text.get_text().split(" (spare)")[0].split("  (+")[0]
            text.set_fontweight("bold" if selected and name == selected else "normal")

    def _table_xview(self, first, last):
        try:
            self.hbar.set(first, last)
            if float(first) > 0.0 or float(last) < 1.0:
                self.hbar.grid()
            else:
                self.hbar.grid_remove()
        except Exception:
            pass

    def highlight(self):
        """2.2.13 (John): the selected table row's star drawn thicker and fully opaque, the others faded; no
        selection, all normal. Comps, spares and the check star alike."""
        if not getattr(self, "artists", None) or not hasattr(self, "canvas"):
            return
        sel = self.table.selection()
        row = self.table_rows.get(sel[0]) if sel else None
        name = row["label"] if row else None
        if name not in self.artists:
            name = None
        for lab, arts in self.artists.items():
            for a in arts:
                lw, alpha, z, ms = self._looks[a]
                if name is None:
                    a.set_linewidth(lw); a.set_alpha(alpha); a.set_zorder(z); a.set_markersize(ms)
                elif lab == name:
                    a.set_linewidth(lw * 2.0 if lw else lw); a.set_alpha(1.0); a.set_zorder(z + 10)
                    a.set_markersize(ms * 1.4)
                else:
                    a.set_linewidth(lw); a.set_alpha(0.15 if (alpha or 1.0) >= 0.5 else 0.06); a.set_zorder(z)
                    a.set_markersize(ms)
        self._legend(name)
        try:
            self.canvas.draw_idle()
        except Exception:
            pass

    @staticmethod
    def _draw_one_night(ax, result, kinds):
        """Within-night behaviour (2.2.2): each star minus the others, raw points faint, binned medians as lines,
        offset so the stars do not sit on top of each other, with the meridian flip marked."""
        jd = np.asarray(result["jd"], dtype=float)
        t0 = math.floor(float(np.nanmin(jd)) - 0.5) + 0.5 if jd.size else 0.0
        hours = (jd - t0) * 24.0
        order = np.argsort(hours)
        n_bins = max(1, min(30, len(jd) // 6))
        edges = np.linspace(np.nanmin(hours), np.nanmax(hours) + 1e-9, n_bins + 1) if len(jd) else np.array([0, 1])
        step = 0.0
        artists = {}
        for k, lab in enumerate(result["labels"]):
            v = np.asarray(result["resid"][lab], dtype=float)
            ok = np.isfinite(v)
            if ok.sum() < 3:
                continue
            v = v - float(np.median(v[ok]))
            spread = 1.4826 * float(np.median(np.abs(v[ok])))
            step = max(step, 4.0 * spread, 0.02)
        for k, lab in enumerate(result["labels"]):
            v = np.asarray(result["resid"][lab], dtype=float)
            ok = np.isfinite(v)
            if ok.sum() < 3:
                continue
            v = v - float(np.median(v[ok])) + k * step
            spare = kinds[k] == "spare"
            line, = ax.plot(hours[order][ok[order]], v[order][ok[order]], ".", ms=3, alpha=0.25)
            bx, by = [], []
            for a, b in zip(edges[:-1], edges[1:]):
                sel = ok & (hours >= a) & (hours < b)
                if sel.sum() >= 2:
                    bx.append(float(np.mean(hours[sel])))
                    by.append(float(np.median(v[sel])))
            binned, = ax.plot(bx, by, "o--" if spare else "o-", color=line.get_color(), ms=4, lw=1.0 if spare else 1.6,
                              label=lab + (" (spare)" if spare else "") + (f"  (+{k * step:.2f})" if k else ""))
            artists[lab] = [line, binned]
        flips = np.asarray(result.get("row_flip") or [], dtype=bool)
        if flips.any() and (~flips).any():
            t_flip = 0.5 * (float(np.max(hours[~flips])) + float(np.min(hours[flips])))
            ax.axvline(t_flip, color="#7a5c99", ls="--", lw=1.0, label="meridian flip")
        ax.invert_yaxis()
        ax.set_xlabel(f"Hours from JD {t0:.1f}")
        ax.set_ylabel("Star minus the others\n(mag, offset for clarity)")
        ax.set_title("Within-night behaviour of each star\n(one night: should be flat and not jump at the flip)", fontsize=10)
        ax.grid(True, alpha=0.3)
        return artists

    def watch_selected(self):
        sel = self.table.selection()
        row = self.table_rows.get(sel[0]) if sel else None
        if not row:
            messagebox.showinfo(APP_TITLE, "Select a spare in the table first.")
            return
        if row.get("kind") != "spare":
            messagebox.showinfo(APP_TITLE, "Only spare comps can be added here; comps and the check star are already "
                                           "measured every run.")
            return
        self.app.add_watch_by_name(row["label"])


class UseCompsWindow:
    """Choose which measured stars (the run's comps and the spare comps) form the comparison ensemble."""

    def __init__(self, app):
        self.app = app
        win = Toplevel(app)
        self.win = win
        win.title(f"{APP_TITLE} — comparison stars in use")
        top = ttk.Frame(win, style="Card.TFrame", padding=12)
        top.pack(fill=BOTH, expand=True)
        nav = ttk.Frame(top, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", pady=(10, 0))
        ttk.Button(nav, text="Apply", style="Accent.TButton", command=self.apply).pack(side=RIGHT)
        ttk.Button(nav, text="Back to the original comps", command=self.reset).pack(side=RIGHT, padx=8)
        ttk.Button(nav, text="Close", command=win.destroy).pack(side=LEFT)
        body = ScrollBody(top, horizontal=True)
        body.pack(fill=BOTH, expand=True)
        inner = body.inner
        wrap = text_wrap_px(win)   # 2.2.14 (item 12): font units, not a fixed 950 px
        ttk.Label(inner, text="Comparison stars in use", font=(core.UI_FONT, 15, "bold"), style="Card.TLabel").pack(anchor=W)
        intro = ttk.Label(
            inner,
            text="Tick the stars to average as the comparison. Every point is re-derived from the measurements already "
                 "stored, so nothing is reduced again, and you can switch back at any time. Spares were measured on the "
                 "nights reduced in 1.8 or later. The check star cannot be a comp. Colors (B-V) stay tied to the comps "
                 "the nights were reduced with.",
            style="Hint.TLabel", wraplength=wrap, justify=LEFT,
        )
        intro.pack(anchor=W, pady=(2, 8))
        wrapped = [intro]
        catalog = app._comp_catalog()
        nights = sorted({o.night for o in app.observations})
        health = core.comp_health(app.observations, list(app.comps_used), app.check_used, app._spares_in_series()) or {}
        verdicts = {row["label"]: row for row in health.get("summary", [])}
        current = set(app.active_comps or app.comps_used)
        grid = ttk.Frame(inner, style="Card.TFrame")
        grid.pack(fill=X)
        for c, head in enumerate(("Use", "Star", "Role", "Catalog mag", "Nights measured", "Night offset",
                                  "Scatter in night", "Comp health")):
            ttk.Label(grid, text=head, style="Card.TLabel", font=(core.UI_FONT, 10, "bold")).grid(
                row=0, column=c, sticky=W, padx=6, pady=(0, 4))
        self.vars = {}
        self.mag_vars = {}   # 2.2.18 (item 3): {star: (StringVar, text shown at first)}
        self.check_var = None
        aliases = app._star_aliases()
        names, listed = [], set()
        for n, role in [(n, "comp") for n in app.comps_used] + [(n, "spare") for n in app._spares_in_series()]:
            if n in listed:
                continue
            # 2.2.3: one star under two catalog names is one row ("TYC … = APASS …"), measured on the nights of both.
            listed.add(n)
            listed.update(aliases.get(n, ()))
            names.append((n, role))
        widened_rows = 0
        for r, (name, role) in enumerate(names, 1):
            var = BooleanVar(value=name in current or bool(set(aliases.get(name, ())) & current))
            self.vars[name] = var
            ttk.Checkbutton(grid, variable=var).grid(row=r, column=0, sticky=W, padx=6)
            measured = sorted({o.night for o in app.observations
                               if math.isfinite(core.star_inst(o, name, app.comps_used, aliases))})
            row = verdicts.get(name, {})
            sp = row.get("night_spread", float("nan"))
            sc = row.get("scatter", float("nan"))
            mag = catalog.get(name)
            shown = name + "".join(f" = {a}" for a in aliases.get(name, ()))
            # 2.2.15 (item 5): a spare from a widened window (thin field) says how much fainter than the target it is,
            # as on the Photometry panel and the diamond's hover (2.2.14 item 6).
            spare_info = next((app.spare_catalog.get(n) for n in (name, *aliases.get(name, ()))
                               if (app.spare_catalog.get(n) or {}).get("widened")), None) or {}
            faint = _fainter_text(spare_info["fainter_than_target"]) \
                if spare_info.get("fainter_than_target") is not None else ""
            if faint:
                role = f"{role} (widened window): {faint}"
                widened_rows += 1
            # 2.2.18 (item 3): the catalog magnitude can be typed (the series' value wins over the Photometry box).
            mag_var = StringVar(value=f"{mag:.3f}" if mag is not None else "")
            self.mag_vars[name] = (mag_var, mag_var.get())
            ttk.Entry(grid, textvariable=mag_var, width=8).grid(row=r, column=3, sticky=W, padx=6, pady=2)
            for c, text in enumerate((shown, role, None,
                                      f"{len(measured)} of {len(nights)}",
                                      f"{sp:.4f}" if isinstance(sp, float) and math.isfinite(sp) else "—",
                                      f"{sc:.4f}" if isinstance(sc, float) and math.isfinite(sc) else "—",
                                      row.get("verdict", "")), 1):
                if text is None:
                    continue
                colour = "#b00020" if c == 7 and text.startswith("SUSPECT") else "#1c2b33"
                ttk.Label(grid, text=text, style="Card.TLabel", foreground=colour).grid(row=r, column=c, sticky=W, padx=6, pady=2)
        # 2.2.18 (item 3): the check star's catalog magnitude (its plot and the next nights' check), typed here too.
        check = app.check_used or ""
        if check:
            r = len(names) + 1
            ttk.Label(grid, text="", style="Card.TLabel").grid(row=r, column=0)
            ttk.Label(grid, text=check, style="Card.TLabel").grid(row=r, column=1, sticky=W, padx=6, pady=2)
            ttk.Label(grid, text="check (not a comp)", style="Card.TLabel").grid(row=r, column=2, sticky=W, padx=6)
            shown_k = (getattr(app, "_series_check", None) or ("", ""))[1] or app.check_mag.get().strip()
            try:
                shown_k = f"{float(shown_k):.3f}"
            except (TypeError, ValueError):
                shown_k = ""
            self.check_var = (StringVar(value=shown_k), shown_k)
            ttk.Entry(grid, textvariable=self.check_var[0], width=8).grid(row=r, column=3, sticky=W, padx=6, pady=2)
        wrapped.append(ttk.Label(inner, text="Catalog mag: type a star's catalog magnitude to correct it for the whole "
                                             "series (for example a value from another catalog than the campaign's). "
                                             "Apply shifts every point by the change in its comps' catalog mean; "
                                             "nothing is measured again.",
                                 style="Hint.TLabel", wraplength=wrap, justify=LEFT))
        wrapped[-1].pack(anchor=W, pady=(8, 0))
        if len(names) == len(app.comps_used):
            ttk.Label(inner, text="No spare comps in this series yet. Nights reduced in 1.8 or later after Solve "
                                  "carry spares.", style="Hint.TLabel").pack(anchor=W, pady=(8, 0))
        elif len(names) < 3:
            wrapped.append(ttk.Label(inner, text="Only two stars to choose from. With three or more, a noisy or drifting "
                                                 "star stands out and the ensemble averages down the noise; 2.2.2 "
                                                 "measures extra spares when the field is thin, on nights reduced "
                                                 "again.", style="Hint.TLabel", wraplength=wrap, justify=LEFT))
            wrapped[-1].pack(anchor=W, pady=(8, 0))
        if widened_rows:
            # 2.2.15 (item 5)
            wrapped.append(ttk.Label(inner, text="Spares from a widened window (a thin field) are much fainter than "
                                                 "the target: their noise can dominate the ensemble and they can shift "
                                                 "its zero point. Prefer comps within about a magnitude of the target.",
                                     style="Hint.TLabel", wraplength=wrap, justify=LEFT))
            wrapped[-1].pack(anchor=W, pady=(8, 0))
        body.fit()
        fit_to_content(win)
        body.follow_width(wrapped, pad=16)   # 2.2.14 (item 12): the text wraps to the window's width from now on

    def _typed_mags(self):
        """2.2.18 (item 3): ({star: new catalog mag}, new check mag or None) from the Catalog mag boxes, only where
        the text was changed; None when a box does not hold a magnitude (the user is told)."""
        changes, check = {}, None
        boxes = [(name, var, first) for name, (var, first) in self.mag_vars.items()]
        if self.check_var is not None:
            boxes.append((None, *self.check_var))
        for name, var, first in boxes:
            text = var.get().strip()
            if text == first:
                continue
            try:
                mag = float(text)
                if not (math.isfinite(mag) and -2.0 < mag < 30.0):
                    raise ValueError
            except ValueError:
                messagebox.showerror(APP_TITLE, f"\u201c{text}\u201d is not a catalog magnitude"
                                     f" ({name or 'check star'}).", parent=self.win)
                return None
            try:
                if first and abs(float(first) - mag) < 5e-4:
                    continue   # 8.73 typed over 8.728 shown as 8.728: the same value
            except ValueError:
                pass
            if name is None:
                check = f"{mag:.3f}"
            else:
                changes[name] = mag
        return changes, check

    def apply(self):
        selection = [name for name, var in self.vars.items() if var.get()]
        if not selection:
            messagebox.showinfo(APP_TITLE, "Tick at least one comparison star.", parent=self.win)
            return
        typed = self._typed_mags()
        if typed is None:
            return
        changes, check = typed
        current = list(self.app.active_comps or self.app.comps_used)
        new_selection = set(selection) != set(current)
        # review: the comp-selection questions first; a No changes nothing (no catalog edit, no file written).
        if new_selection and not self.app._comp_selection_ok(selection, changes):
            return
        if changes or check is not None:
            self.app.set_catalog_mags(changes, check)   # 2.2.18 (item 3)
            for name, (var, _first) in self.mag_vars.items():
                self.mag_vars[name] = (var, var.get().strip())
            if self.check_var is not None:
                self.check_var = (self.check_var[0], self.check_var[0].get().strip())
        if not new_selection:
            if changes or check is not None:
                self.win.destroy()
            elif self.app.apply_comp_selection(selection):
                self.win.destroy()
            return
        if self.app.apply_comp_selection(selection, confirmed=True):
            self.win.destroy()

    def reset(self):
        if self.app.apply_comp_selection(list(self.app.comps_used)):
            self.win.destroy()


class SuggestWindow:
    """Aperture and sky ring sizes from the measured star size, with an optional test on real frames."""

    def __init__(self, app, fwhm, sizes, prop, notes, basis=""):
        self.app = app
        self.fwhm = fwhm
        self.prop = dict(prop)
        self.prop_seeing = dict(prop)   # 2.2.16 (item 1): the seeing-only suggestion, shown as "Before the test" later
        win = Toplevel(app)
        self.win = win
        win.title(f"{APP_TITLE} — suggest apertures")
        # 2.2.8: buttons first (bottom), contents in a scrolling body so the test table that arrives later fits.
        # 2.2.14 (item 12): the window is sized once everything is in it. 2.2.8 queued the sizing first, and
        # body.fit()'s update_idletasks ran it while the window was still empty, so it opened at the 480 px minimum
        # and clipped the buttons (14 Oct, John's screen).
        nav = ttk.Frame(win, style="Card.TFrame", padding=(12, 6, 12, 12))
        nav.pack(fill=X, side="bottom")
        self.body = ScrollBody(win, horizontal=True)
        self.body.pack(fill=BOTH, expand=True)
        top = ttk.Frame(self.body.inner, style="Card.TFrame", padding=12)
        top.pack(fill=BOTH, expand=True)
        wrap = text_wrap_px(win)   # 2.2.14 (item 12): in font units, not a fixed 780 px
        wrapped = []
        ttk.Label(top, text="Suggested apertures", font=(core.UI_FONT, 15, "bold"), style="Card.TLabel").pack(anchor=W)
        measured = ", ".join(f"{role} {f:.1f}" for role, f, _p in sizes)
        ttk.Label(top, text=f"Star size (FWHM) on this frame: {fwhm:.2f} px" + (f"   ({basis})" if basis else ""),
                  style="Card.TLabel").pack(anchor=W, pady=(6, 0))
        if measured:
            wrapped.append(ttk.Label(top, text=f"Marked stars, for comparison: {measured} px. Faint or crowded stars "
                                               "measure noisier; the aperture follows the field's seeing.",
                                     style="Hint.TLabel", wraplength=wrap, justify=LEFT))
            wrapped[-1].pack(anchor=W)
        ttk.Label(top, text="This window sets the aperture and sky ring, not which stars are used.",
                  style="Hint.TLabel").pack(anchor=W)
        # 2.2.16 (item 1): the bold line always shows what "Use these aperture sizes" sets (self.prop): the seeing-only
        # suggestion first, the tested sizes after a test (the seeing-only one then moves to a grey "Before the test").
        self.headline = StringVar(value=self._sizes_text(self.prop))
        wrapped.append(ttk.Label(top, textvariable=self.headline,
                                 style="Card.TLabel", font=(core.UI_FONT, 11, "bold"), wraplength=wrap, justify=LEFT))
        wrapped[-1].pack(anchor=W, pady=(6, 0))
        self.headline_label = wrapped[-1]
        self.before_text = StringVar(value="")
        self.before_label = ttk.Label(top, textvariable=self.before_text, style="Hint.TLabel", wraplength=wrap,
                                      justify=LEFT)
        wrapped.append(self.before_label)   # packed under the bold line once a test has run
        wrapped.append(ttk.Label(top, text="Bright stars do best with 1.5–2 × FWHM; faint stars with 1–1.5 ×. The seeing "
                                           "changes during the night, so the test below measures real frames and lets "
                                           "the check star decide.",
                                 style="Hint.TLabel", wraplength=wrap, justify=LEFT))
        wrapped[-1].pack(anchor=W, pady=(4, 0))
        for note in notes:
            wrapped.append(ttk.Label(top, text=note, style="Warn.TLabel" if note.startswith("⚠") else "Hint.TLabel",
                                     wraplength=wrap, justify=LEFT))
            wrapped[-1].pack(anchor=W, pady=(4, 0))
        self.result_text = StringVar(value="")
        ttk.Label(top, textvariable=self.result_text, style="Box.TLabel", justify=LEFT).pack(anchor=W, pady=(10, 0))
        ttk.Button(nav, text="Use these aperture sizes", style="Accent.TButton", command=self.use).pack(side=LEFT)
        ttk.Button(nav, text="Test 4 apertures on 30 frames", command=lambda: app.test_apertures(fwhm, self, 30)).pack(
            side=LEFT, padx=8)
        ttk.Button(nav, text="60 frames (slower, surer)", command=lambda: app.test_apertures(fwhm, self, 60)).pack(
            side=LEFT)
        ttk.Button(nav, text="Close", command=win.destroy).pack(side=RIGHT)
        self.body.fit()
        fit_to_content(win)
        # 2.2.14 (item 12): from now on the text wraps to the window's width (resizing it rewraps); 28 = the 12 px
        # padding on each side and a little air. Only after the sizing, so the empty first layout cannot narrow it.
        self.body.follow_width(wrapped, pad=28)

    def _sizes_text(self, prop) -> str:
        """2.2.16 (item 1): "Aperture r = 8.4 px (2.2 × FWHM)    sky in = 15    sky out = 23 px (…)" for these sizes."""
        r, s_in, s_out = float(prop["radius"]), float(prop["sky_in"]), float(prop["sky_out"])
        mult = f" ({r / self.fwhm:.1f} × FWHM)" if self.fwhm and math.isfinite(self.fwhm) and self.fwhm > 0 else ""
        area = (s_out ** 2 - s_in ** 2) / r ** 2 if r > 0 else float("nan")
        ring = f" (sky ring with {area:.0f}× the aperture's area)" if math.isfinite(area) and area > 0 else ""
        return f"Aperture r = {r:g} px{mult}    sky in = {s_in:g}    sky out = {s_out:g} px{ring}"

    def use(self):
        self.app.radius.set(self.prop["radius"])
        self.app.sky_in.set(self.prop["sky_in"])
        self.app.sky_out.set(self.prop["sky_out"])
        self.app.refresh_preview()
        self.app.status.set(f"Aperture r {self.prop['radius']:g}, sky {self.prop['sky_in']:g}–{self.prop['sky_out']:g} px. "
                            "Use the same sizes for every night of a series.")
        try:
            self.win.destroy()
        except Exception:
            pass

    def show_test(self, result, seeing, sky_in, sky_out, ref_label):
        rows = (result or {}).get("rows") or []
        if not rows:
            self.result_text.set("The test measured no frames.")
            return
        ring_x = (result.get("rings") or {}).get((result.get("extended") or [None])[0])
        lines = [f"Measured on {rows[0]['n']} frames; sky ring {sky_in:g}–{sky_out:g} px"
                 + (f" ({ring_x[0]:g}–{ring_x[1]:g} px for r ≥ {result['extended'][0]:g})" if ring_x else "") + ". "
                 f"± is one standard error; 'lowest' is how often that aperture stays lowest when the frames are "
                 f"resampled ({result['boot']} times)."]
        if seeing:
            lines.append(f"Seeing (FWHM) in these frames: {min(seeing):.1f} to {max(seeing):.1f} px, "
                         f"median {np.median(seeing):.1f}.")
        lines.append("")
        lines.append(f"{'r (px)':>7}  {ref_label + ' scatter':>23}  {'lowest':>7}  {'target scatter':>17}  {'lowest':>7}")
        for row in rows:
            lines.append(f"{row['r']:>7g}  {row['ref_sigma']:>14.4f} ± {row['ref_err']:.4f}  {row['ref_win']:>6.0%}"
                         f"  {row['target_sigma']:>8.4f} ± {row['target_err']:.4f}  {row['target_win']:>6.0%}"
                         + (f"  (sky {ring_x[0]:g}–{ring_x[1]:g})" if ring_x and row["r"] in result["extended"]
                            else ""))   # 2.2.16 (item 1, review): their own, wider ring
        lines.append("")
        best = result.get("ref_best")
        tbest = result.get("target_best")
        if result.get("extended"):
            # 2.2.16 (item 1): the largest of the four apertures was best, so 2.6 and 3.0 × FWHM were compared too.
            lines.insert(len(lines) - 1, f"The largest of the first four apertures was best, so the test went on to r = "
                                         f"{', '.join(f'{r:g}' for r in result['extended'])} px (2.6 and 3.0 × FWHM"
                                         + (f"; their sky ring {ring_x[0]:g}–{ring_x[1]:g} px" if ring_x else "")
                                         + ").")
        four = result.get("four_best")
        if best is not None and best["ref_win"] >= 0.8:
            pick = best["r"]
            lines.append(f"Clear winner: r = {pick:g} has the lowest {ref_label} scatter in {best['ref_win']:.0%} "
                         "of resamples.")
        elif result.get("extended") and four is not None and four["ref_win"] >= 0.8:
            # 2.2.16 (item 1, review): the first four had a clear winner (the largest); the larger apertures only share
            # its plateau. Never fall back to a size the four-way test beat.
            pick = four["r"]
            lines.append(f"The larger apertures were no better than r = {pick:g} (the differences are within the "
                         f"noise); r = {pick:g} stays, the clear winner of the first four ({four['ref_win']:.0%} of "
                         "resamples).")
        else:
            pick = result.get("suggested_r") or (best or rows[0])["r"]
            lines.append(f"No clear winner: the differences are within the noise (no aperture is lowest in 80% of "
                         f"resamples). Keep the suggested r = {pick:g} (1.8 × FWHM).")
        if tbest is not None and tbest["target_win"] >= 0.8 and abs(tbest["r"] - pick) > 0.05:
            lines.append(f"The target alone prefers r = {tbest['r']:g} ({tbest['target_win']:.0%}). If the target is "
                         "much fainter than the check star, that can be the better choice; if it truly varies, its "
                         "scatter includes the variation.")
        tried = [row["r"] for row in rows]
        if best is not None and best["ref_win"] >= 0.8 and len(tried) > 1:
            # 2.2.16 (item 1): say it plainly when the winner sits at either end of what was tried.
            if abs(pick - max(tried)) < 0.05:
                lines.append(f"Best at the edge of the tried range (r = {pick:g}, the largest tried); a larger aperture "
                             "may be better. A close neighbour, trailing or defocus can do this; check the image.")
            elif abs(pick - min(tried)) < 0.05:
                lines.append(f"Best at the edge of the tried range (r = {pick:g}, the smallest tried); a smaller "
                             "aperture may be better (faint stars often prefer 1–1.2 × FWHM).")
        # 2.2.16 (item 1): the sky ring that was measured with the picked aperture (2.6/3.0 × FWHM have their own).
        p_in, p_out = (result.get("rings") or {}).get(pick, (sky_in, sky_out))
        self.prop = {"radius": pick, "sky_in": p_in, "sky_out": p_out}
        lines.append(f"Use these aperture sizes now sets r = {pick:g}, sky {p_in:g}–{p_out:g}.")
        self.result_text.set("\n".join(lines))
        # 2.2.16 (item 1): the bold line follows what the button sets; the seeing-only suggestion moves under it.
        self.headline.set(self._sizes_text(self.prop))
        s = self.prop_seeing
        mult = (f" ({float(s['radius']) / self.fwhm:.1f} × FWHM)"
                if self.fwhm and math.isfinite(self.fwhm) and self.fwhm > 0 else "")
        self.before_text.set(f"Before the test: r = {s['radius']:g} px{mult}, sky {s['sky_in']:g}–{s['sky_out']:g} px "
                             "(from the seeing alone).")
        if not getattr(self, "_before_shown", False):
            try:
                self.before_label.pack(anchor=W, after=self.headline_label)
            except Exception:
                pass
            self._before_shown = True
        self._refit()

    def _refit(self):
        # 2.2.8: the results table arrives after the window opened; grow the window to show it (John, CoRoT-1 run).
        try:
            self.body.fit()
            grow_to_content(self.win)
        except Exception:
            pass


class ExtremumWindow:
    """2.2.14 (item 13): time of maximum / minimum for one night, its O−C against VSX (or typed) elements, and a CSV of
    times that grows night by night into an O−C diagram (plotted here once it has rows)."""

    def __init__(self, app):
        self.app = app
        win = Toplevel(app)
        self.win = win
        win.title(f"{APP_TITLE} — time of maximum / minimum")
        top = ttk.Frame(win, style="Card.TFrame", padding=10)
        top.pack(fill=BOTH, expand=True)
        nav = ttk.Frame(top, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", pady=(6, 0))
        ttk.Button(nav, text="Measure", style="Accent.TButton", command=self.measure).pack(side=LEFT)
        self.save_button = ttk.Button(nav, text="Save to times CSV", command=self.save, state="disabled")
        self.save_button.pack(side=LEFT, padx=6)
        ttk.Button(nav, text="Choose times CSV…", command=self.choose_csv).pack(side=LEFT)
        ttk.Button(nav, text="Close", command=win.destroy).pack(side=RIGHT)
        ttk.Label(top, text="Time of maximum / minimum", font=(core.UI_FONT, 15, "bold"),
                  style="Card.TLabel").pack(anchor=W)
        self.hint = ttk.Label(
            top, style="Hint.TLabel", justify=LEFT,
            text="The extremum is timed with a local polynomial. Its error comes from 500 bootstrap refits with "
                 "stretches of the night's own noise (residuals about a Fourier series at the period, so noise that "
                 "lasts minutes counts), times the residuals' red-noise factor β, and is never less than half the "
                 "difference from the Fourier series (and, for a minimum, from Kwee & van Woerden 1956, which is also "
                 "shown). O−C is against the elements below "
                 "(VSX epochs are HJD). Saved times go into one CSV per star next to the series file; it grows "
                 "night by night into an O−C diagram.")
        self.hint.pack(anchor=W, fill=X, pady=(2, 6))
        self.hint.bind("<Configure>", lambda e: self.hint.configure(wraplength=max(200, e.width - 8)))
        form = ttk.Frame(top, style="Card.TFrame")
        form.pack(anchor=W, fill=X)
        nights = sorted({o.night for o in app.observations})
        self.night_names = {app.night_label(n, short=True): n for n in nights}
        night_default = app.highlight_night if app.highlight_night in nights else (nights[-1] if nights else "")
        self.night = StringVar(value=next((k for k, v in self.night_names.items() if v == night_default), ""))
        self.kind = StringVar(value="minimum" if core.extremum_kind_for(app.catalog_var_type) == "min" else "maximum")
        self.window_min = StringVar(value="")
        period = app.catalog_period
        epoch = app.catalog_epoch
        self.period = StringVar(value=f"{period:.8f}" if period else "")
        self.epoch = StringVar(value=f"{epoch:.5f}" if epoch else "")
        self.system = StringVar(value="HJD")
        self.source = StringVar(value="VSX" if period and epoch else ("typed" if period or epoch else ""))
        ttk.Label(form, text="Night", style="Card.TLabel").grid(row=0, column=0, sticky=W, padx=(0, 6), pady=2)
        ttk.Combobox(form, textvariable=self.night, values=list(self.night_names), state="readonly",
                     width=max([len(k) for k in self.night_names] + [12]) + 2).grid(row=0, column=1, sticky=W, pady=2)
        ttk.Label(form, text="Type", style="Card.TLabel").grid(row=0, column=2, sticky=W, padx=(14, 6))
        ttk.Combobox(form, textvariable=self.kind, values=("maximum", "minimum"), state="readonly",
                     width=10).grid(row=0, column=3, sticky=W)
        ttk.Label(form, text="Half-window (min)", style="Card.TLabel").grid(row=0, column=4, sticky=W, padx=(14, 6))
        ttk.Entry(form, textvariable=self.window_min, width=7).grid(row=0, column=5, sticky=W)
        ttk.Label(form, text="blank = automatic", style="Hint.TLabel").grid(row=0, column=6, sticky=W, padx=(6, 0))
        ttk.Label(form, text="Period (d)", style="Card.TLabel").grid(row=1, column=0, sticky=W, padx=(0, 6), pady=2)
        ttk.Entry(form, textvariable=self.period, width=14).grid(row=1, column=1, sticky=W, pady=2)
        ttk.Label(form, text="Epoch", style="Card.TLabel").grid(row=1, column=2, sticky=W, padx=(14, 6))
        ttk.Entry(form, textvariable=self.epoch, width=16).grid(row=1, column=3, columnspan=2, sticky=W)
        ttk.Combobox(form, textvariable=self.system, values=("HJD", "BJD"), state="readonly",
                     width=5).grid(row=1, column=5, sticky=W)
        ttk.Button(form, text="Elements from VSX", command=self.lookup).grid(row=1, column=6, sticky=W, padx=(6, 0))
        ttk.Label(form, textvariable=self.source, style="Hint.TLabel").grid(row=1, column=7, sticky=W, padx=(6, 0))
        self.result_text = StringVar(value="Choose the night and press Measure.")
        ttk.Label(top, textvariable=self.result_text, font=(core.MONO_FONT, 10), style="Card.TLabel",
                  justify=LEFT).pack(anchor=W, pady=(6, 4))
        self.csv_text = StringVar(value="")
        ttk.Label(top, textvariable=self.csv_text, style="Hint.TLabel", justify=LEFT).pack(anchor=W)
        self.results = []
        # 2.2.14 review: the CSV chosen earlier is reused only for the same star.
        self.csv_path = app.extremum_csv if app.extremum_csv and (
            core.same_star_text(app.extremum_csv_star, self._star())
            or os.path.basename(app.extremum_csv) == core.extremum_csv_name(self._star())) else ""
        self.fig = None
        if Figure is not None:
            self.fig = make_constrained_figure((10.5, 4.0))
            self.ax_lc = self.fig.add_subplot(121)
            self.ax_oc = self.fig.add_subplot(122)
            self.canvas = FigureCanvasTkAgg(self.fig, master=top)
            self.canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        self._show_csv()
        self.draw()
        fit_to_content(win)

    # -- inputs ---------------------------------------------------------------------------
    def _elements(self):
        """(period, epoch) as floats, or (None, None) when either is missing or unreadable."""
        try:
            p = float(self.period.get())
            e = float(self.epoch.get())
        except ValueError:
            return None, None
        if not (p > 0 and 2400000.0 < e < 2600000.0):
            return None, None
        return p, e

    def _star(self) -> str:
        return self.app.star_id.get().strip() or "target"

    def _elements_source(self, period, epoch) -> str:
        """2.2.14 review: 'VSX' when the elements are the ones VSX gave, else 'typed' (never a lookup message)."""
        cp, ce = self.app.catalog_period, self.app.catalog_epoch
        if cp and ce and abs(period - cp) < 1e-9 and abs(epoch - ce) < 1e-5:
            return "VSX"
        return "typed"

    def _coords(self):
        app = self.app

        def num(var):
            try:
                return float(var.get()) if var.get().strip() else None
            except ValueError:
                return None
        ra = num(app.ra_hours)
        return (ra * 15.0 if ra is not None else None, num(app.dec_deg), num(app.lat), num(app.lon))

    def lookup(self):
        name = self._star()
        if name == "target":
            messagebox.showinfo(APP_TITLE, "Type the star's name in Star ID on the Input page first.", parent=self.win)
            return
        self.source.set(f"looking up {name} in VSX…")

        def work():
            try:
                found = core._lookup_vsx(name)
                self.app.call_ui(self._lookup_done, found, None)
            except Exception as exc:
                self.app.call_ui(self._lookup_done, None, exc)
        threading.Thread(target=work, daemon=True).start()

    def _lookup_done(self, found, error):
        try:
            if not self.win.winfo_exists():
                return
        except Exception:
            return
        if error is not None or not found:
            self.source.set(f"VSX: {error}")
            return
        p, e = found.get("period_days"), found.get("epoch_jd")
        if p:
            self.period.set(f"{p:.8f}")
            self.app.catalog_period = p
        if e:
            self.epoch.set(f"{e:.5f}")
            self.app.catalog_epoch = e
        self.system.set("HJD")
        self.app.catalog_var_type = found.get("var_type") or self.app.catalog_var_type
        self.kind.set("minimum" if core.extremum_kind_for(found.get("var_type")) == "min" else "maximum")
        self.source.set("VSX " + (found.get("var_type") or "") + ("" if p and e else " (no period/epoch there; type them)"))

    # -- measuring ------------------------------------------------------------------------
    def _night_points(self):
        night = self.night_names.get(self.night.get())
        pts = [o for o in self.app._usable() if o.night == night]
        vals = np.asarray(self.app._analysis_values(pts), dtype=float) if pts else np.array([])
        return night, pts, vals

    def measure(self):
        night, pts, vals = self._night_points()
        if len(pts) < 12:
            messagebox.showinfo(APP_TITLE, "That night has too few usable points (12 or more needed).", parent=self.win)
            return
        kind = "max" if self.kind.get() == "maximum" else "min"
        period, epoch = self._elements()
        hw = None
        if self.window_min.get().strip():
            try:
                hw = float(self.window_min.get()) / 1440.0
            except ValueError:
                messagebox.showerror(APP_TITLE, "Half-window: minutes, or blank for automatic.", parent=self.win)
                return
        t = np.array([o.jd for o in pts], dtype=float)
        guesses = [None]
        if period and epoch:
            # Every extremum the elements predict in this night (the night may hold two maxima of a short-period star).
            guesses = core.predicted_extrema(float(t.min()), float(t.max()), period, epoch,
                                             margin=0.1 * period) or [None]
        results, problems = [], []
        for g in guesses:
            try:
                r = core.measure_extremum(t, vals, kind, period=period, t_guess=g, half_width=hw)
            except ValueError as exc:
                problems.append(str(exc))
                continue
            if any(abs(r["t"] - q["t"]) < 0.05 * (period or 1.0) for q in results):
                continue
            results.append(r)
        ra, dec, lat, lon = self._coords()
        for r in results:
            try:
                r["times"] = core.extremum_times(r["t"], ra, dec, lat, lon)
            except Exception as exc:
                r["times"] = {"jd_utc": r["t"], "hjd": float("nan"), "bjd_tdb": float("nan"),
                              "note": f"time conversion failed: {exc}"}
            r["oc"] = None
            if period and epoch:
                t_sys = r["times"]["bjd_tdb" if self.system.get() == "BJD" else "hjd"]
                if not math.isfinite(t_sys):
                    t_sys = r["t"]   # no RA/Dec: JD against the elements (said in the text)
                # A minimum may be a secondary eclipse (half a cycle).
                r["oc"] = core.o_minus_c(t_sys, period, epoch, half_cycles=kind == "min")
        self.results = results
        self.night_key = night
        self.save_button.configure(state="normal" if results else "disabled")
        self.result_text.set(self._result_lines(results, problems, kind, period, epoch) if results or problems
                             else "No extremum found.")
        if not results:
            self.app.log("Time of " + self.kind.get() + f" ({night}): " + "; ".join(problems or ["none found"]))
        self.draw()
        grow_to_content(self.win)

    def _result_lines(self, results, problems, kind, period, epoch) -> str:
        word = "max" if kind == "max" else "min"
        lines = []
        for r in results:
            tm = r["times"]
            err_min = r["err"] * 1440.0
            if math.isfinite(tm["hjd"]):
                lines.append(f"T{word}  HJD {tm['hjd']:.5f} ± {r['err']:.5f}  ({err_min:.1f} min)   "
                             f"BJD_TDB {tm['bjd_tdb']:.5f}   JD {tm['jd_utc']:.5f}")
            else:
                lines.append(f"T{word}  JD {tm['jd_utc']:.5f} ± {r['err']:.5f}  ({err_min:.1f} min)")
            if tm.get("note"):
                lines.append(f"       {tm['note']}")
            poly, kvw, fo = r["poly"], r["kvw"], r["fourier"]
            lines.append(f"       polynomial deg {poly['degree']}: JD {poly['t']:.5f} ± {poly['err'] * 1440:.2f} min "
                         f"({poly['n']} points, ±{r['half_width'] * 1440:.0f} min)")
            if kvw:
                lines.append(f"       Kwee-van Woerden: JD {kvw['t']:.5f} ± {kvw['err'] * 1440:.2f} min")
            if fo:
                lines.append(f"       Fourier ({fo['n_harm']} harmonics, rms {fo['rms']:.3f}): JD {fo['t']:.5f} "
                             f"± {fo['err'] * 1440:.2f} min")
            if r.get("oc"):
                oc = r["oc"]
                sys_word = self.system.get() if math.isfinite(tm["hjd"]) else "JD"
                lines.append(f"       O−C {oc['oc_d']:+.5f} d = {oc['oc_min']:+.1f} ± {err_min:.1f} min   cycle "
                             f"{oc['cycle']:g}   (C = {sys_word} {oc['calc']:.5f}; P {period:.8f}, E {epoch:.5f})")
            for n in r.get("notes", []):
                lines.append(f"       note: {n}")
            for w in r.get("warnings", []):
                lines.append(f"       ⚠ {w}")
        for p in problems if not results else []:
            lines.append(f"⚠ {p}")
        if results and not (period and epoch):
            lines.append("No elements: type the period and epoch (or Elements from VSX) for the O−C.")
        return "\n".join(lines)

    # -- the times CSV --------------------------------------------------------------------
    def _default_csv(self) -> str:
        name = core.extremum_csv_name(self._star())
        app = self.app
        if self.csv_path:
            return self.csv_path
        if app.series_path:
            return os.path.join(os.path.dirname(app.series_path), name)
        folder = app._out_folder()
        return os.path.join(folder, name) if folder else ""

    def choose_csv(self):
        start = self._default_csv()
        path = filedialog.asksaveasfilename(
            parent=self.win, title="Times of maximum / minimum CSV (new or existing)", defaultextension=".csv",
            initialfile=os.path.basename(start) or core.extremum_csv_name(self._star()),
            initialdir=os.path.dirname(start) or None, filetypes=[("CSV", "*.csv")], confirmoverwrite=False)
        if path:
            self.csv_path = path
            self.app.extremum_csv, self.app.extremum_csv_star = path, self._star()
            self._show_csv()
            self.draw()

    def _show_csv(self):
        path = self._default_csv()
        if not path:
            self.csv_text.set("Times CSV: not chosen yet (no series file or output folder); Save asks where.")
            return
        rows = [r for r in core.read_extremum_csv(path) if core.same_star_text(r.get("star"), self._star())]
        self.csv_text.set(f"Times CSV: {path}  ({len(rows)} time{'s' if len(rows) != 1 else ''} of {self._star()})")

    def save(self):
        if not self.results:
            return
        path = self._default_csv()
        if not path:
            self.choose_csv()
            path = self.csv_path
            if not path:
                return
        period, epoch = self._elements()
        kind = "max" if self.kind.get() == "maximum" else "min"
        done = []
        for r in self.results:
            tm, oc = r["times"], r.get("oc") or {}
            kvw, poly, fo = r.get("kvw") or {}, r["poly"], r.get("fourier") or {}
            row = {"star": self._star(), "type": kind, "hjd": tm["hjd"], "bjd_tdb": tm["bjd_tdb"],
                   "jd_utc": tm["jd_utc"], "error_d": r["err"], "error_min": r["err"] * 1440.0,
                   "method": r["method"], "cycle": oc.get("cycle", float("nan")),
                   "o_c_d": oc.get("oc_d", float("nan")), "o_c_min": oc.get("oc_min", float("nan")),
                   "period_d": period if period else float("nan"), "epoch": epoch if epoch else float("nan"),
                   "elements": f"{self._elements_source(period, epoch)} {self.system.get()}" if period and epoch
                   else "",
                   "night": self.night_key, "n_points": poly["n"], "window_min": r["half_width"] * 1440.0,
                   "kvw_jd": kvw.get("t", float("nan")), "kvw_err": kvw.get("err", float("nan")),
                   "poly_jd": poly["t"], "poly_err": poly["err"], "fourier_jd": fo.get("t", float("nan")),
                   "fourier_err": fo.get("err", float("nan")), "time_note": tm.get("note", ""),
                   "note": "; ".join(r.get("warnings", []))}
            try:
                done.append(core.save_extremum_row(path, row))
            except OSError as exc:
                messagebox.showerror(APP_TITLE, f"Could not write {path}: {exc}", parent=self.win)
                return
        self.csv_path = path
        self.app.extremum_csv, self.app.extremum_csv_star = path, self._star()
        self.app.log(f"Time of {self.kind.get()} saved to {os.path.basename(path)} ({', '.join(done)}).")
        self.app.status.set(f"Saved to {path}")
        self._show_csv()
        self.draw()

    # -- plots ----------------------------------------------------------------------------
    def draw(self):
        if self.fig is None:
            return
        ax = self.ax_lc
        ax.clear()
        night, pts, vals = self._night_points()
        if pts:
            t = np.array([o.jd for o in pts])
            t0 = math.floor(float(t.min()) - 0.5) + 0.5
            hours = (t - t0) * 24.0
            ax.plot(hours, vals, ".", ms=3, color="#7f8c99", alpha=0.6)
            for r in self.results:
                hw = r["half_width"]
                tc = r["poly"]["t"]
                sel = np.abs(t - tc) <= hw
                if sel.sum() > r["poly"]["degree"] + 1:
                    x = (t[sel] - tc) / hw
                    coef = np.polyfit(x, vals[sel], r["poly"]["degree"])
                    xs = np.linspace(-1, 1, 200)
                    ax.plot((tc + xs * hw - t0) * 24.0, np.polyval(coef, xs), "-", color="#c43b3b", lw=1.6)
                ax.axvline((r["t"] - t0) * 24.0, color="#c43b3b", lw=1.0)
                if r.get("kvw"):
                    ax.axvline((r["kvw"]["t"] - t0) * 24.0, color="#0067c0", lw=0.9, ls="--")
                if r.get("fourier"):
                    ax.axvline((r["fourier"]["t"] - t0) * 24.0, color="#2e7d32", lw=0.9, ls=":")
                if r.get("oc"):
                    # The predicted time on the JD axis (the light-time correction is under 9 minutes).
                    t_sys = r["times"]["bjd_tdb" if self.system.get() == "BJD" else "hjd"]
                    calc_jd = r["oc"]["calc"] - (t_sys - r["t"] if math.isfinite(t_sys) else 0.0)
                    ax.axvline((calc_jd - t0) * 24.0, color="#7b2cbf", lw=0.9, ls="-.")
            ax.invert_yaxis()
            ax.set_xlabel(f"Hours from JD {t0:.1f}")
            ax.set_ylabel("Magnitude")
            key = []
            if self.results:
                key.append("— polynomial")
                if any(r.get("kvw") for r in self.results):
                    key.append("‒‒ KvW")
                if any(r.get("fourier") for r in self.results):
                    key.append("··· Fourier")
                if any(r.get("oc") for r in self.results):
                    key.append("‒· predicted (C)")
            ax.set_title(f"{self._star()}  {self.app.night_label(night, short=True)}"
                         + ("\n" + " · ".join(key) if key else ""), fontsize=9)
            ax.grid(True, alpha=0.3)
        ax = self.ax_oc
        ax.clear()
        period, epoch = self._elements()
        path = self._default_csv()
        rows = [r for r in core.read_extremum_csv(path)
                if core.same_star_text(r.get("star"), self._star())] if path else []
        pts_oc = []
        for r in rows:
            tt = r.get("bjd_tdb" if self.system.get() == "BJD" else "hjd")
            if not (isinstance(tt, float) and math.isfinite(tt)):
                tt = r.get("jd_utc")
            if not (isinstance(tt, float) and math.isfinite(tt)):
                continue
            if period and epoch:
                oc = core.o_minus_c(tt, period, epoch, half_cycles=r.get("type") == "min")
                pts_oc.append((oc["cycle"], oc["oc_min"], r.get("error_min", float("nan")), r.get("type"), False))
            elif isinstance(r.get("cycle"), float) and math.isfinite(r["cycle"]):
                pts_oc.append((r["cycle"], r.get("o_c_min", float("nan")), r.get("error_min", float("nan")),
                               r.get("type"), False))
        for r in self.results:
            if r.get("oc") and not any(abs(p[0] - r["oc"]["cycle"]) < 0.25 for p in pts_oc):
                pts_oc.append((r["oc"]["cycle"], r["oc"]["oc_min"], r["err"] * 1440.0, r["kind"], True))
        if pts_oc:
            for kind, marker in (("max", "o"), ("min", "s")):
                for unsaved in (False, True):
                    sel = [p for p in pts_oc if p[3] == kind and p[4] == unsaved]
                    if not sel:
                        continue
                    ax.errorbar([p[0] for p in sel], [p[1] for p in sel],
                                yerr=[p[2] if math.isfinite(p[2]) else 0.0 for p in sel], fmt=marker, ms=6,
                                capsize=3, color="#c43b3b" if unsaved else "#0067c0",
                                mfc="none" if unsaved else None)
            ax.axhline(0.0, color="#888888", lw=0.8)
            ax.set_xlabel("Cycle E")
            ax.set_ylabel("O−C (min)")
            ax.set_title("O−C" + (f" against P {period:.8f}, E {epoch:.5f}" if period and epoch
                                  else " as saved (each row against its own elements)")
                         + "\n● saved max · ■ saved min · open red = this night, not saved yet", fontsize=9)
            ax.grid(True, alpha=0.3)
        else:
            ax.set_axis_off()
            ax.text(0.5, 0.5, "The O−C diagram appears here once\ntimes are saved to the CSV"
                    + ("" if period and epoch else "\n(and elements are given)"),
                    ha="center", va="center", transform=ax.transAxes, color="#5d6d76")
        self.canvas.draw_idle()


class WatchWindow:
    """Light curves of the watch stars: every frame, and one averaged point per night."""

    def __init__(self, app):
        self.app = app
        win = Toplevel(app)
        self.win = win
        win.title(f"{APP_TITLE} — watch stars")
        win.after_idle(lambda: fit_to_content(win))
        top = ttk.Frame(win, style="Card.TFrame", padding=10)
        top.pack(fill=BOTH, expand=True)
        ttk.Label(top, text="Watch stars", font=(core.UI_FONT, 15, "bold"), style="Card.TLabel").pack(anchor=W)
        ttk.Label(top, text="Each watch star is measured against the comparison stars in use, frame by frame. Cloud, drift, "
                            "and frames without the star are left out. Magnitudes are untransformed, like the target's.",
                  style="Hint.TLabel", wraplength=1250, justify=LEFT).pack(anchor=W, pady=(2, 6))
        nav = ttk.Frame(top, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", pady=(6, 0))
        ttk.Button(nav, text="Export watch light curves CSV…", command=self.export).pack(side=LEFT)
        ttk.Button(nav, text="Stop watching selected", command=self.remove).pack(side=LEFT, padx=6)
        ttk.Button(nav, text="Close", command=win.destroy).pack(side=RIGHT)
        cols = ("star", "catalog", "points", "nights", "spread", "trend", "info")
        self.tree = ttk.Treeview(top, columns=cols, show="headings", height=min(max(len(app.watch_list), 3), 8))
        for col, label, width, stretch in (("star", "Star", 260, True), ("catalog", "Catalog mag", 120, False),
                                           ("points", "Points", 80, False), ("nights", "Nights", 70, False),
                                           ("spread", "Night-to-night spread", 170, False),
                                           ("trend", "First → last night", 150, False), ("info", "Known as", 380, True)):
            self.tree.heading(col, text=label, anchor=W)
            cw = col_width(self.tree, label, width)
            self.tree.column(col, width=cw, minwidth=cw, anchor=W, stretch=stretch)
        self.tree.pack(fill=X, side="bottom", pady=(6, 0))
        self.rows = {}
        for w in app.watch_list:
            obs, mags = app.watch_series(w["name"])
            nightly = self._nightly(obs, mags)
            means = [b["value"] for b in nightly]
            spread = f"{np.std(means):.4f}" if len(means) > 1 else "—"
            trend = f"{means[-1] - means[0]:+.3f}" if len(means) > 1 else "—"
            mag = f"{w['mag']:.2f}" if isinstance(w.get("mag"), (int, float)) else "—"
            item = self.tree.insert("", END, values=(w["name"], mag, len(obs), len(nightly), spread, trend,
                                                     w.get("variable") or ""))
            self.rows[item] = w["name"]
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.draw())
        self.fig = None
        if Figure is not None:
            self.fig = Figure(figsize=(11, 5), dpi=100, facecolor="#ffffff")
            self.ax = self.fig.add_subplot(111)
            self.canvas = FigureCanvasTkAgg(self.fig, master=top)
            self.canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        items = self.tree.get_children()
        if items:
            self.tree.selection_set(items[0])
        self.draw()

    @staticmethod
    def _nightly(obs, mags):
        return core.bin_points(obs, mags, None) if obs else []

    def _selected(self):
        sel = self.tree.selection()
        return self.rows.get(sel[0]) if sel else None

    def draw(self):
        if self.fig is None:
            return
        name = self._selected()
        ax = self.ax
        ax.clear()
        if name:
            obs, mags = self.app.watch_series(name)
            if obs:
                tx = self.app._time_values(np.array([o.jd for o in obs]))
                ax.plot(tx, mags, "o", ms=3, alpha=0.3, color=PICK_COLORS["watch"], label="every frame")
                bins = self._nightly(obs, mags)
                if bins:
                    bx = self.app._time_values(np.array([b["jd"] for b in bins]))
                    ax.errorbar(bx, [b["value"] for b in bins], yerr=[b["err"] for b in bins], fmt="D", ms=6,
                                color="#111111", capsize=3, label="night mean")
                ax.invert_yaxis()
                ax.legend(fontsize=8, frameon=False)
                ax.set_title(f"{name}   ({len(obs)} points, {len(bins)} nights)")
            else:
                ax.set_title(f"{name}: no measurements yet (it is measured from the next run on)")
            self.app._format_time_axis(ax)
            ax.set_ylabel("Magnitude (untransformed)")
            ax.grid(True, alpha=0.3)
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def remove(self):
        name = self._selected()
        if name:
            self.app.remove_watch(name)
            self.win.destroy()
            if self.app.watch_list:
                WatchWindow(self.app)

    def export(self):
        path = self.app.output_path("watch_stars.csv", title="Save CSV", defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")], parent=self.win)
        if not path:
            return
        rows = 0
        with core.AsciiWriter(path) as fh:
            fh.write(f"# {APP_TITLE} {APP_VERSION}: watch stars, measured against the comps in use; untransformed\n")
            fh.write(core.created_line() + "\n")
            fh.write("star,jd,mag,night,file\n")
            for w in self.app.watch_list:
                obs, mags = self.app.watch_series(w["name"])
                for o, m in zip(obs, mags):
                    fh.write(f"{w['name'].replace(',', ' ')},{o.jd:.6f},{m:.4f},{o.night},"
                             f"{os.path.basename(o.path).replace(',', '_')}\n")
                    rows += 1
        self.app.status.set(f"Wrote {rows} rows to {path}")


EXPORT_SETUPS_FILE = os.path.join(os.path.expanduser("~"), ".shiloh_photometry_exports.json")
COLUMN_LABELS = (("err", "Error"), ("night", "Night"), ("segment", "Segment (flip)"), ("flag", "QC flag"),
                 ("airmass", "Airmass"), ("check", "Check star"), ("comp", "Comp instrumental"),
                 ("fwhm", "FWHM"), ("color", "B-V"), ("note", "Night note"))


def load_export_setups() -> dict:
    try:
        with open(EXPORT_SETUPS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_export_setups(setups: dict) -> None:
    with open(EXPORT_SETUPS_FILE, "w", encoding="utf-8") as fh:
        json.dump(setups, fh, indent=2)


class ExportWindow:
    """Choose the time system, values, binning, and columns; save named setups for reuse."""

    def __init__(self, app):
        self.app = app
        win = Toplevel(app)
        self.win = win
        win.title(f"{APP_TITLE} — export")
        self.time = StringVar(value="JD_UTC")
        self.values = StringVar(value="mag")
        self.points = StringVar(value="all")
        self.bin = StringVar(value="Night")
        self.setup_name = StringVar(value="")
        self.cols = {key: BooleanVar(value=key in ("err", "night", "flag", "airmass")) for key, _ in COLUMN_LABELS}
        outer = ttk.Frame(win, style="Card.TFrame", padding=14)
        outer.pack(fill=BOTH, expand=True)
        # Buttons first, so they stay in view; the rest scrolls if the screen is short (2.2.2).
        nav = ttk.Frame(outer, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", pady=(10, 0))
        ttk.Button(nav, text="Export…", style="Accent.TButton", command=self.export).pack(side=LEFT)
        ttk.Button(nav, text="Close", command=win.destroy).pack(side=RIGHT)
        body = ScrollBody(outer)
        body.pack(fill=BOTH, expand=True)
        top = body.inner
        ttk.Label(top, text="Export", font=(core.UI_FONT, 15, "bold"), style="Card.TLabel").pack(anchor=W)
        intro = ttk.Label(
            top,
            text="Values follow the Output page: flagged points left out if that box is ticked, check-star "
                 "correction and segment line-up as set there. The file header records every choice.",
            style="Hint.TLabel", wraplength=text_wrap_px(win, chars=85), justify=LEFT,   # 2.2.14 (item 12)
        )
        intro.pack(anchor=W, pady=(2, 10))

        setups = ttk.Frame(top, style="Card.TFrame")
        setups.pack(fill=X, pady=(0, 8))
        ttk.Label(setups, text="Saved setup", style="Card.TLabel").pack(side=LEFT)
        self.setup_box = ttk.Combobox(setups, textvariable=self.setup_name, values=sorted(load_export_setups()), width=24)
        self.setup_box.pack(side=LEFT, padx=6)
        ttk.Button(setups, text="Load", command=self.load_setup).pack(side=LEFT)
        ttk.Button(setups, text="Save as this name", command=self.save_setup).pack(side=LEFT, padx=6)
        ttk.Button(setups, text="Delete", command=self.delete_setup).pack(side=LEFT)

        def group(title):
            box = ttk.LabelFrame(top, text=title, style="Group.TLabelframe", padding=(10, 4, 10, 8))
            box.pack(fill=X, pady=4)
            return box

        g = group("Time")
        for value, label in (("JD_UTC", "JD (UTC, geocentric) — AAVSO"), ("HJD_UTC", "HJD (UTC, heliocentric)"),
                             ("BJD_TDB", "BJD_TDB (barycentric) — exoplanet and timing work")):
            ttk.Radiobutton(g, text=label, value=value, variable=self.time).pack(anchor=W)
        g = group("Values")
        ttk.Radiobutton(g, text="Magnitude", value="mag", variable=self.values).pack(anchor=W)
        ttk.Radiobutton(g, text="Relative flux (median = 1)", value="flux", variable=self.values).pack(anchor=W)
        g = group("Points")
        row = ttk.Frame(g, style="Card.TFrame")
        row.pack(anchor=W)
        ttk.Radiobutton(row, text="Every point", value="all", variable=self.points).pack(side=LEFT)
        ttk.Radiobutton(row, text="Binned:", value="binned", variable=self.points).pack(side=LEFT, padx=(14, 4))
        ttk.Combobox(row, textvariable=self.bin, values=BIN_CHOICES[1:], width=10, state="readonly").pack(side=LEFT)
        g = group("Extra columns (every-point export)")
        grid = ttk.Frame(g, style="Card.TFrame")
        grid.pack(anchor=W)
        for k, (key, label) in enumerate(COLUMN_LABELS):
            ttk.Checkbutton(grid, text=label, variable=self.cols[key]).grid(row=k // 4, column=k % 4, sticky=W, padx=(0, 16))
        body.fit()
        fit_to_content(win)
        body.follow_width([intro], pad=8)   # 2.2.14 (item 12): the text wraps to the window's width from now on

    def current_setup(self) -> dict:
        binning = None
        if self.points.get() == "binned":
            binning = "night" if self.bin.get() == "Night" else self.bin.get().split()[0]
        return {"time": self.time.get(), "values": self.values.get(), "binning": binning,
                "bin_label": self.bin.get(), "points": self.points.get(),
                "columns": [k for k, v in self.cols.items() if v.get()]}

    def export_setup(self) -> dict:
        """current_setup plus the scintillation settings from the app (not saved with a named setup)."""
        setup = self.current_setup()
        setup["scint"] = self.app._scint_setup()
        return setup

    def load_setup(self):
        setup = load_export_setups().get(self.setup_name.get())
        if not setup:
            messagebox.showinfo(APP_TITLE, "Pick a saved setup first.", parent=self.win)
            return
        self.time.set(setup.get("time", "JD_UTC"))
        self.values.set(setup.get("values", "mag"))
        self.points.set(setup.get("points", "all"))
        self.bin.set(setup.get("bin_label", "Night"))
        for key, var in self.cols.items():
            var.set(key in (setup.get("columns") or []))

    def save_setup(self):
        name = self.setup_name.get().strip()
        if not name:
            messagebox.showinfo(APP_TITLE, "Type a name for this setup in the box first.", parent=self.win)
            return
        setups = load_export_setups()
        setups[name] = self.current_setup()
        try:
            save_export_setups(setups)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Could not save setups: {exc}", parent=self.win)
            return
        self.setup_box.configure(values=sorted(setups))
        self.app.status.set(f"Saved export setup '{name}'.")

    def delete_setup(self):
        setups = load_export_setups()
        if self.setup_name.get() in setups:
            del setups[self.setup_name.get()]
            save_export_setups(setups)
            self.setup_box.configure(values=sorted(setups))
            self.setup_name.set("")

    def export(self, path: str | None = None):
        app = self.app
        setup = self.export_setup()
        points = app._usable()
        values = app._analysis_values(points)
        try:
            ra = float(app.ra_hours.get()) * 15.0 if app.ra_hours.get().strip() else None
            dec = float(app.dec_deg.get()) if app.dec_deg.get().strip() else None
            lat = float(app.lat.get()) if app.lat.get().strip() else None
            lon = float(app.lon.get()) if app.lon.get().strip() else None
            times = core.convert_times(np.array([o.jd for o in points]), setup["time"], ra, dec, lat, lon)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Time conversion: {exc}", parent=self.win)
            return
        if path is None:
            base = (app.star_id.get() or "target").replace(" ", "_")
            path = self.app.output_path(f"{base}_{setup['time']}_{setup['values']}.csv", title="Save CSV", defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")], parent=self.win)
            if not path:
                return
        comps = ", ".join(app.active_comps or app.comps_used) or app.comp_name.get()
        meta = {
            "software": f"{APP_TITLE} {APP_VERSION}",
            "target": app.star_id.get(), "observer": app.observer.get(),
            "filter": "/".join(sorted({o.filt for o in points})),
            "comps": comps, "check": app.check_used or app.check_name.get(),
            "processing": (f"aperture r={app.radius.get()} sky {app.sky_in.get()}-{app.sky_out.get()} px; "
                           f"check-star correction {'on' if app.use_check_zp.get() else 'off'}; "
                           f"segments lined up {'yes' if app.norm_segments.get() else 'no'}; "
                           f"flip lined up {'yes' if app.align_flip.get() and not app.norm_segments.get() else 'no'}; "
                           f"flagged points {'left out' if app.skip_flagged.get() else 'included'}; "
                           f"cloud flag at {app.cloud_limit.get()}%; {app._scint_note()}"),
        }
        n = core.write_export(path, points, values, setup, meta, times, app.night_notes)
        app.status.set(f"Exported {n} rows to {path}")
        return path


class ScanWindow:
    """Field-scan results in their own window (used outside Discovery mode)."""

    def __init__(self, app, result):
        win = Toplevel(app)
        win.title(f"{APP_TITLE} — field scan")
        win.after_idle(lambda: fit_to_content(win))
        self.view = ScanView(win, app, result, on_close=win.destroy)


class KnownVariablesWindow:
    """Every known or suspected variable (VSX + SIMBAD) in the scanned frame, and what the scan saw for each."""

    def __init__(self, view):
        self.view = view
        r = view.r
        win = Toplevel(view.win)
        self.win = win
        win.title(f"{APP_TITLE} — known variables in the field")
        win.after_idle(lambda: fit_to_content(win))
        top = ttk.Frame(win, style="Card.TFrame", padding=10)
        top.pack(fill=BOTH, expand=True)
        ttk.Label(top, text="Known variables in the field", font=(core.UI_FONT, 15, "bold"), style="Card.TLabel").pack(anchor=W)
        known = r.get("known")
        if known is None:
            why = ("VSX and SIMBAD could not be reached during the scan. Scan again when online."
                   if r.get("radec") is not None else
                   "This scan has no sky positions, so it could not be checked against VSX and SIMBAD. "
                   "Solve before scanning.")
            ttk.Label(top, text=why, style="Hint.TLabel").pack(anchor=W, pady=(6, 0))
            ttk.Button(top, text="Close", command=win.destroy).pack(anchor=E, pady=(10, 0))
            return
        ttk.Label(top, text=(f"{len(known)} known or suspected variables ({' + '.join(r.get('vsx_sources') or [])}) fall "
                             "in the frame. A known variable that did not vary in the scan is not a mistake: many vary "
                             "slowly, by less than this night's noise, or only now and then. Click a measured star to "
                             "see its light curve in the scan window."),
                  style="Hint.TLabel", wraplength=1300, justify=LEFT).pack(anchor=W, pady=(2, 6))
        nav = ttk.Frame(top, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", pady=(6, 0))
        ttk.Button(nav, text="Watch selected", command=self.watch_selected).pack(side=LEFT)
        ttk.Button(nav, text="Close", command=win.destroy).pack(side=RIGHT)
        cols = ("name", "source", "type", "star", "mag", "scatter", "excess", "seen")
        tree = ttk.Treeview(top, columns=cols, show="headings", height=14)
        for col, label, width, stretch in (("name", "Name", 220, True), ("source", "Source", 80, False),
                                           ("type", "Type", 300, True), ("star", "Scan star", 90, False),
                                           ("mag", "Mag", 70, False), ("scatter", "Scatter", 80, False),
                                           ("excess", "× normal", 90, False), ("seen", "In this scan", 280, True)):
            tree.heading(col, text=label, anchor=W)
            cw = col_width(tree, label, width)
            tree.column(col, width=cw, minwidth=cw, anchor=W, stretch=stretch)
        scroll = ttk.Scrollbar(top, orient=VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.pack(side=LEFT, fill=Y)
        self.tree = tree
        self.rows = {}
        self.known_rows = {}
        matches = r.get("vsx") or []
        cand = set(r.get("candidates") or [])
        entries = []
        for k in known:
            idx = next((i for i, m in enumerate(matches) if m is k), None)
            if idx is None:
                entries.append((1, k, None, ("", "", "", "", "not measured (faint, saturated, blended, or not detected)")))
                continue
            sc, ex = r["scatter"][idx], r["excess"][idx]
            if idx in set(r.get("saturated") or []):
                seen = "saturated: clipped, so not usable tonight"
            elif idx in cand:
                seen = "VARIED: a scan candidate"
            elif not r["usable"][idx]:
                seen = "measured on too few frames"
            elif math.isfinite(ex) and ex >= 1.5:
                seen = "somewhat more scatter than normal"
            else:
                seen = "steady tonight, within the noise"
            entries.append((0 if idx in cand else 0.5, k, idx,
                            (r["labels"][idx], f"{r['mag'][idx]:.2f}", f"{sc:.3f}" if math.isfinite(sc) else "",
                             f"{ex:.1f}" if math.isfinite(ex) else "", seen)))
        entries.sort(key=lambda e: (e[0], -(r["excess"][e[2]] if e[2] is not None and math.isfinite(r["excess"][e[2]]) else 0)))
        for _order, k, idx, cells in entries:
            kind = f"{k.get('type', '')} ({k.get('desc', '')})" if k.get("source") == "SIMBAD" else k.get("desc", "")
            item = tree.insert("", END, values=(k["name"], k.get("source", ""), kind, *cells))
            self.rows[item] = idx
            self.known_rows[item] = k
        tree.bind("<<TreeviewSelect>>", self._on_row)

    def _view_alive(self) -> bool:
        try:
            return bool(self.view.frame.winfo_exists())
        except Exception:
            return False

    def _on_row(self, _event):
        sel = self.tree.selection()
        idx = self.rows.get(sel[0]) if sel else None
        if idx is not None and self._view_alive():
            self.view.selected = idx
            self.view.draw()

    def watch_selected(self):
        sel = self.tree.selection()
        idx = self.rows.get(sel[0]) if sel else None
        if idx is None:
            k = self.known_rows.get(sel[0]) if sel else None
            if k:
                self.view.app._add_watch_sky(k["name"], k["ra"], k["dec"], self.view.app._variable_hover(k).replace("\n", " "))
            return
        if not self._view_alive():
            return
        self.view.selected = idx
        self.view.watch_selected()


class ScanView:
    """Field-scan results: scatter vs brightness, candidate list, and the selected star's light curve."""

    def __init__(self, parent, app, result, on_close=None):
        self.app = app
        self.r = result
        sat = set(result.get("saturated") or [])
        # Show a candidate first, else the nearest miss, else any measured star that is not saturated (2.2.1).
        first = list(result["candidates"]) + list(result.get("near") or [])
        first += [i for i in range(len(result["labels"])) if i not in sat and result["usable"][i]]
        self.selected = first[0] if first else 0
        self.win = parent.winfo_toplevel()
        top = ttk.Frame(parent, style="Card.TFrame", padding=10)
        top.pack(fill=BOTH, expand=True)
        self.frame = top
        n = len(result["candidates"])
        zp = result.get("zp")
        if zp:
            cal = (f"on the {result.get('zp_catalog', 'catalog')} scale (zero point from {zp['n']} field stars, "
                   f"± {zp['err']:.3f}; untransformed)")
        elif result["calibrated"]:
            cal = "calibrated to the comp star"
        else:
            cal = "instrumental (solve the field first to put them on a catalog scale)"
        # 2.2.9: on the Output page the title names the step ("Output – Field scan"); the pop-up keeps "Field scan".
        ttk.Label(top, text="Field scan" if on_close is not None else "Output – Field scan",
                  font=(core.UI_FONT, 16 if on_close is None else 15, "bold"), style="Card.TLabel").pack(anchor=W)
        method = (result.get("color_method") or {}).get("bv", "")
        fit = (result.get("color_fits") or {}).get("bv")
        color_note = {
            "field": (f" B−V is calibrated to catalog colors with {fit['n']} labeled field stars "
                      f"(color term {fit['k']:.2f}, scatter {fit['scatter']:.3f})." if core.color_fit_usable(fit)
                      else ""),   # 2.2.16 (item 3 review): a failed fit has no k / scatter
            "saved": " B−V uses the color term saved from an earlier field fit, anchored on the comp's catalog color.",
            "comps": " B−V is the comp's catalog color plus the raw channel difference (untransformed; too few "
                     "field stars with catalog B and V to calibrate).",
            "relative": " B−V is relative to the median star (no catalog colors).",
        }.get(method, "")
        if fit and fit.get("problem") and method != "field":
            color_note += f" The field color fit was not used: {fit['problem']}."
        ttk.Label(
            top,
            text=f"{len(result['labels'])} stars, {result['frames']} frames. Magnitudes are {cal}. "
                 f"{n} star(s) scatter at least {result.get('excess_limit', 3.0):g}x more than stars of the same "
                 "brightness" + (f"; the {len(result.get('near') or [])} nearest misses are listed as ~1, ~2 …"
                                 if not n and result.get("near") else "") + ". "
                 + (f"Each star's light curve was lined up across {result['segments'] - 1} meridian flip(s) or rotator "
                    "move(s) first. " if result.get("segments", 1) > 1 else "")
                 + 
                 "Click a point or a row to see its light curve. A candidate is a lead, not a discovery: "
                 "check it on another night and look for a nearby bright star or a bad pixel first. A period "
                 "marked (edge) sits at the end of the search range, so the true period may lie outside it."
                 + color_note,
            style="Hint.TLabel", wraplength=1300, justify=LEFT,
        ).pack(anchor=W, pady=(2, 6))
        # 2.2.9: a "Results" save row at the bottom, as on the Variables Output page; the actions sit above it.
        results = ttk.Frame(top, style="Card.TFrame")
        results.pack(fill=X, side="bottom", pady=(4, 0))
        ttk.Label(results, text="Results", style="Hint.TLabel", width=8).pack(side=LEFT)
        ttk.Button(results, text="Save plot PNG…", style="Accent.TButton", command=self.save_png).pack(side=LEFT)
        ttk.Button(results, text="Save CSV…", command=self.save_csv).pack(side=LEFT, padx=6)
        ttk.Button(results, text="Export candidates CSV…", command=self.export_candidates).pack(side=LEFT)
        ttk.Button(results, text="Export all light curves CSV…", command=self.export_curves).pack(side=LEFT, padx=6)
        if on_close is None:   # on the Output page: Back to Photometry, as on the Variables Output page
            ttk.Button(results, text="Back", command=lambda: app.show_step(STEP_PHOTO)).pack(side=RIGHT)
        nav = ttk.Frame(top, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", pady=(6, 0))
        self.mark_button = ttk.Button(nav, text="Clear candidate marks" if app.scan_marks else "Mark candidates on image",
                                      command=self.toggle_marks)
        self.mark_button.pack(side=LEFT)
        ttk.Button(nav, text="Watch selected star", command=self.watch_selected).pack(side=LEFT, padx=6)
        ttk.Button(nav, text="Known variables measured…", command=lambda: KnownVariablesWindow(self)).pack(side=LEFT)
        # 2.2.5: leave the candidates already in VSX/SIMBAD out of the plot and the table (their numbers are kept, so
        # they still match the marks on the image).
        self.hide_known = BooleanVar(value=False)
        self.n_known = sum(1 for i in result["candidates"] if result.get("vsx") and result["vsx"][i])
        hide_box = ttk.Checkbutton(nav, text="Hide known variables", variable=self.hide_known, command=self._hide_changed)
        hide_box.pack(side=LEFT, padx=(12, 0))
        if result.get("vsx") is None:
            hide_box.state(["disabled"])
        self.hidden_text = StringVar(value="")
        ttk.Label(nav, textvariable=self.hidden_text, style="Hint.TLabel").pack(side=LEFT, padx=6)
        if on_close is not None:
            ttk.Button(nav, text="Close", command=on_close).pack(side=RIGHT)

        body = ttk.Frame(top, style="Card.TFrame")
        body.pack(fill=BOTH, expand=True)
        cols = ("rank", "star", "mag", "scatter", "excess", "period", "color", "caution", "vsx")
        self.tree = ttk.Treeview(body, columns=cols, show="headings", height=8, selectmode="extended")
        self._scan_heads = {}
        for col, label, width in (
            ("rank", "#", 40), ("star", "Star / position", 210), ("mag", "Mag", 70), ("scatter", "Scatter", 80),
            ("excess", "× normal", 80), ("period", "Best period (d)", 110),
            ("color", "B−V (rel.)" if method == "relative" else "B−V", 70), ("caution", "Check first", 230),
            ("vsx", "VSX", 260),
        ):
            self.tree.heading(col, text=label)
            self._scan_heads[col] = label
            cw = col_width(self.tree, label, width)
            self.tree.column(col, width=cw, minwidth=cw, anchor=W, stretch=(col == "vsx"))
        self.tree.pack(fill=X, side="bottom", pady=(6, 0))
        self.rows = {}
        self._fill_table()
        self.tree.bind("<<TreeviewSelect>>", self._on_row)
        self.tree.bind("<<TreeviewSelect>>", self._update_mark_button, add="+")

        if Figure is None:
            return
        self.fig = Figure(figsize=(11, 5.5), dpi=100, facecolor="#ffffff")
        self.ax_s = self.fig.add_subplot(1, 2, 1)
        self.ax_lc = self.fig.add_subplot(2, 2, 2)
        self.ax_ls = self.fig.add_subplot(2, 2, 4)
        self.canvas = FigureCanvasTkAgg(self.fig, master=body)
        self.canvas.get_tk_widget().configure(width=400, height=300)
        self.canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        self.canvas.mpl_connect("button_press_event", self._on_click)
        self.draw()

    def _is_known(self, i) -> bool:
        return bool(self.r.get("vsx") and self.r["vsx"][i])

    def _hide_changed(self):
        n = self.n_known if self.hide_known.get() else 0
        self.hidden_text.set(f"{n} known variable(s) hidden" if n else "")
        if self.hide_known.get() and self._is_known(self.selected):
            rest = [i for i in self.r["candidates"] if not self._is_known(i)]
            if rest:
                self.selected = rest[0]
        self._fill_table()
        if getattr(self, "fig", None) is not None:
            self.draw()

    def _fill_table(self):
        result = self.r
        method = (result.get("color_method") or {}).get("bv", "")
        for item in list(self.tree.get_children()):
            self.tree.delete(item)
        self.rows = {}
        hide = bool(getattr(self, "hide_known", None) and self.hide_known.get())
        listed = [(rank, i) for rank, i in enumerate(result["candidates"], 1) if not (hide and self._is_known(i))]
        if not result["candidates"]:
            # Nothing reached the candidate level: show the closest misses, marked as such (2.2.1).
            listed += [(f"~{k}", i) for k, i in enumerate(result.get("near") or [], 1)]
        listed += [("W", i) for i in result.get("watched", []) if i not in result["candidates"]]
        for rank, i in listed:
            per = result["periods"].get(i, {})
            vsx = result["vsx"][i] if result.get("vsx") else None
            if result.get("vsx") is None:
                vsx_text = "no sky positions (run Solve)"
            elif vsx:
                vsx_text = f"KNOWN ({vsx.get('source', 'VSX')}): {vsx['name']} {vsx['type']}".strip()
            else:
                vsx_text = "not in " + (" or ".join(result.get("vsx_sources") or ["VSX"])) + (
                    " (watched)" if rank == "W" else " (near miss: under the candidate level)"
                    if str(rank).startswith("~") else " — possible new variable")
            where = result["labels"][i]
            names = result.get("names") or []
            if i < len(names) and names[i] and not where.startswith("W "):
                where += f" ({names[i]})"
            if result.get("radec"):
                where += "  " + core.format_radec(*result["radec"][i])
            bv = result["bv"][i] if result.get("bv") is not None else float("nan")
            item = self.tree.insert("", END, values=(
                rank, where, f"{result['mag'][i]:.2f}", f"{result['scatter'][i]:.3f}", f"{result['excess'][i]:.1f}",
                (f"{per['period']:.4f}" + (" (edge)" if per["ls"].get("edge") else "")) if per else "",
                f"{bv:.2f}" if math.isfinite(bv) else "", "; ".join(self.cautions(i)), vsx_text,
            ))
            self.rows[item] = i
        # 2.2.12 (John): columns sized to what they hold, like the Transit fit table.
        fit_tree_columns(self.tree, getattr(self, "_scan_heads", {}))

    def cautions(self, i) -> list[str]:
        """2.2.2: reasons to doubt a candidate before celebrating."""
        return core.scan_cautions(self.r, i)

    def draw(self):
        r = self.r
        mag, sc, exp = np.asarray(r["mag"]), np.asarray(r["scatter"]), np.asarray(r["expected"])
        ok = np.asarray(r["usable"]) & np.isfinite(sc) & (sc > 0)
        ax = self.ax_s
        ax.clear()
        sat = np.zeros(len(mag), dtype=bool)
        for k in r.get("saturated") or []:
            sat[k] = True
        ax.plot(mag[ok & ~sat], sc[ok & ~sat], ".", color="#9aa7b0", ms=5, label="stars")
        if (ok & sat).any():
            ax.plot(mag[ok & sat], sc[ok & sat], "x", color="#5d6d76", ms=7, mew=1.4, label="saturated (never candidates)")
        # 2.2.12: no line across magnitude ranges with no stars.
        for k_seg, (sm, se) in enumerate(core.scatter_curve_segments(mag[ok], exp[ok])):
            ax.plot(sm, se, "-", color="#0067c0", lw=1, label="typical scatter" if k_seg == 0 else None)
        if r.get("auto_mag_limit") is not None:
            ax.axvline(r["auto_mag_limit"], color="#9aa7b0", lw=0.8, ls="--", label="faintest (scatter 0.25 mag)")
        cand = r["candidates"]
        hide = bool(getattr(self, "hide_known", None) and self.hide_known.get())
        if cand:
            known = [] if hide else [i for i in cand if r.get("vsx") and r["vsx"][i]]
            new = [i for i in cand if not self._is_known(i)]
            if new:
                ax.plot(mag[new], sc[new], "o", mfc="none", mec="#d62828", ms=10, mew=1.4, label="candidate")
            if known:
                ax.plot(mag[known], sc[known], "s", mfc="none", mec="#f08c00", ms=10, mew=1.4, label="known in VSX")
            for rank, i in enumerate(cand, 1):
                if hide and self._is_known(i):
                    continue
                ax.annotate(str(rank), (mag[i], sc[i]), textcoords="offset points", xytext=(6, 4), fontsize=8, color="#d62828")
        for name in MARKED_LABELS:
            if name in r["labels"]:
                i = r["labels"].index(name)
                if ok[i]:
                    ax.annotate(name, (mag[i], sc[i]), textcoords="offset points", xytext=(6, -10), fontsize=8, color="#16324a")
        i = self.selected
        if 0 <= i < len(mag) and np.isfinite(sc[i]):
            ax.plot(mag[i], sc[i], "*", color="#ffb000", ms=14, mec="#333333")
        ax.set_yscale("log")
        ax.set_xlabel("Magnitude" if r["calibrated"] else "Instrumental magnitude")
        ax.set_ylabel("Scatter (mag, robust)")
        ax.set_title("Scatter vs brightness")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8, frameon=False)
        self._draw_star(i)
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def _draw_star(self, i):
        r = self.r
        lc = r["light_curves"][:, i]
        good = np.isfinite(lc)
        ax = self.ax_lc
        ax.clear()
        ax.plot(r["jd"][good], lc[good], "o", ms=3, color="#d62828" if i in r["candidates"] else "#0067c0")
        for b, kind in r.get("boundaries") or []:
            if 0 < b < len(r["jd"]):
                ax.axvline(0.5 * (r["jd"][b - 1] + r["jd"][b]), color="#7a5c99", ls="--", lw=0.9,
                           label="flip / rotator move" if kind == "flip" else "gap")
        ax.invert_yaxis()
        # 2.2.16 (item 13): y from the curve's measured points with a margin, as on the Variables light curve. A
        # field scan flags no frames (clouds are taken out by each frame's zero point), so nothing is pinned here.
        App._fit_ylim(ax, lc, None, good)
        name = r["labels"][i]
        if r.get("radec"):
            name += "  " + core.format_radec(*r["radec"][i])
        saturated = i in set(r.get("saturated") or [])
        notes = self.cautions(i)
        ax.set_title(f"{name}   scatter {r['scatter'][i]:.3f}  ({r['excess'][i]:.1f}× normal)"
                     + ("   SATURATED, not a candidate" if saturated else "")
                     + (("\n⚠ " + "; ".join(notes)) if notes and not saturated else ""), fontsize=10,
                     color="#b00020" if (saturated or notes) else "black")
        handles, labs = ax.get_legend_handles_labels()
        if handles:
            seen = {}
            for h_, l_ in zip(handles, labs):
                seen.setdefault(l_, h_)
            ax.legend(list(seen.values()), list(seen.keys()), fontsize=7, frameon=False, loc="best")
        ax.set_xlabel("Julian Date")
        ax.grid(True, alpha=0.3)
        ax = self.ax_ls
        ax.clear()
        per = r["periods"].get(i)
        if per is None and good.sum() >= 8:
            try:
                lo_hi = r.get("period_range")
                if not lo_hi:   # review: a scan without its range uses Discovery's boxes, not the hidden Variables ones
                    src = (self.app.disc_min_period, self.app.disc_max_period) if hasattr(self.app, "disc_min_period") \
                        else (self.app.min_period, self.app.max_period)
                    lo_hi = (float(src[0].get()), float(src[1].get()))
                ls = core.lomb_scargle(r["jd"][good], lc[good], float(lo_hi[0]), float(lo_hi[1]))   # 2.2.18 (16)
                per = {"period": ls["period_days"], "power": ls["power"], "ls": ls} if ls else None
            except Exception:
                per = None
        if per:
            ls = per["ls"]
            ax.plot(1.0 / ls["freqs"], ls["power_spectrum"], color="#0067c0", lw=0.8)
            jd_ok = np.asarray(r["jd"], dtype=float)[good]
            shade_long_periods(ax, float(np.ptp(jd_ok)) if jd_ok.size else 0.0, 1.0 / np.asarray(ls["freqs"]))
            ax.axvline(per["period"], color="#d62828", lw=0.8)
            ax.set_xscale("log")
            edge = ls.get("edge")
            ax.set_title(f"Lomb-Scargle   best {per['period']:.4f} d"
                         + ("   ⚠ at the edge of the search: widen the range" if edge else ""),
                         fontsize=10, color="#b00020" if edge else "black")
            ax.set_xlabel("Period (days)")
            ax.set_ylabel("Power")
        else:
            ax.set_title("Not enough points for a period", fontsize=10)
        ax.grid(True, alpha=0.3)

    def _on_click(self, event):
        if event.inaxes is not self.ax_s or event.xdata is None:
            return
        r = self.r
        mag, sc = np.asarray(r["mag"]), np.asarray(r["scatter"])
        ok = np.where(np.asarray(r["usable"]) & np.isfinite(sc) & (sc > 0))[0]
        if not ok.size:
            return
        # Distance in plot units: magnitude on x, log scatter on y.
        d = np.hypot((mag[ok] - event.xdata) / 0.3, (np.log10(sc[ok]) - math.log10(max(event.ydata, 1e-6))) / 0.15)
        self.selected = int(ok[int(np.argmin(d))])
        self.draw()

    def toggle_marks(self):
        if self.app.scan_marks:
            self.app.clear_scan_marks()
            self._update_mark_button()
        else:
            # 2.2.7: rows selected in the table → only those candidates (numbers kept); none → all of them.
            chosen = [self.rows[iid] for iid in self.tree.selection() if iid in self.rows]
            chosen = [i for i in chosen if i in self.r["candidates"]]
            self.app.mark_scan_candidates(self.r, only=chosen or None)
            self.mark_button.configure(text="Clear candidate marks")

    def _update_mark_button(self, _event=None):
        if self.app.scan_marks:
            self.mark_button.configure(text="Clear candidate marks")
            return
        n = len([iid for iid in self.tree.selection() if iid in self.rows and self.rows[iid] in self.r["candidates"]])
        self.mark_button.configure(text=(f"Mark {n} selected on image" if n else "Mark candidates on image"))

    def _on_row(self, _event):
        sel = self.tree.selection()
        if sel and sel[0] in self.rows:
            self.selected = self.rows[sel[0]]
            self.draw()

    def watch_selected(self):
        """Add the selected scan star to the watch list, so photometry runs follow it from now on."""
        i = self.selected
        r = self.r
        if not (0 <= i < len(r["labels"])):
            return
        label = r["labels"][i]
        if label in MARKED_LABELS:
            self.app.status.set(f"{label} is already measured every run.")
            return
        xy = r["xy"][i]
        name = self.app.add_watch_at(tuple(xy))
        vsx = r["vsx"][i] if r.get("vsx") else None
        if vsx and name:
            for w in self.app.watch_list:
                if w["name"] == name:
                    w.setdefault("variable", f"{vsx.get('source', 'VSX')}: {vsx['name']} {vsx['type']}")

    def _scan_stem(self) -> str:
        """<field>_<filter>_<date>_SHOBS-P_scan, the 2.2.5 file-name pattern (2.2.9)."""
        app = self.app
        field = (app.star_id.get() or "").strip() or "field"
        jds = self._scan_jds()
        date = ""
        if jds:
            a, b = (core.jd_to_datetime_utc(min(jds)).strftime("%d-%b-%Y").upper(),
                    core.jd_to_datetime_utc(max(jds)).strftime("%d-%b-%Y").upper())
            date = "_" + (a if a == b else f"{a}_to_{b}")
        stem = f"{field.replace(' ', '_')}_{app._filter_code()}{date}_SHOBS-P_scan"
        return "".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in stem)

    def _scan_jds(self) -> list:
        jd = self.r.get("jd")   # a numpy array: never test it with "or"
        if jd is None:
            return []
        return [float(j) for j in np.ravel(np.asarray(jd, dtype=float)) if math.isfinite(float(j))]

    def _summary_line(self) -> str:
        r = self.r
        zp = r.get("zp")
        jds = self._scan_jds()
        night = self.app.night_label(f"JD{math.floor(min(jds))}", jd=min(jds)) if jds else ""
        return (f"{night}   {len(r['labels'])} stars measured, {r['frames']} frames, {len(r['candidates'])} candidate(s)"
                + (f"   zero point from {zp['n']} {r.get('zp_catalog', 'catalog')} stars ± {zp['err']:.3f}" if zp else "")
                + f"   SHOBS-P {APP_VERSION}")

    def save_png(self):
        """2.2.9: the scan plots as shown, with a one-line summary above them."""
        if getattr(self, "fig", None) is None:
            return
        path = self.app.output_path(self._scan_stem() + ".png", title="Save plot PNG", defaultextension=".png",
                                    filetypes=[("PNG", "*.png")], parent=self.win)
        if not path:
            return
        title = self.fig.suptitle(self._summary_line() + f"   Created {core.dtg()}", fontsize=9, x=0.01, ha="left")
        try:
            self.fig.savefig(path, dpi=150, facecolor="#ffffff", bbox_inches="tight")
        finally:
            title.remove()
            self.canvas.draw_idle()
        self.app.status.set(f"Wrote {path}")

    def save_csv(self):
        """2.2.9: what the plot shows, one row per measured star."""
        path = self.app.output_path(self._scan_stem() + ".csv", title="Save CSV", defaultextension=".csv",
                                    filetypes=[("CSV", "*.csv")], parent=self.win)
        if path:
            core.write_scan_stars(path, self.r)
            self.app.status.set(f"Wrote {path}")

    def export_candidates(self):
        path = self.app.output_path("field_scan_candidates.csv", title="Save CSV", defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")], parent=self.win)
        if path:
            core.write_scan_candidates(path, self.r)
            self.app.status.set(f"Wrote {path}")

    def export_curves(self):
        path = self.app.output_path("field_scan_lightcurves.csv", title="Save CSV", defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")], parent=self.win)
        if path:
            core.write_scan_lightcurves(path, self.r)
            self.app.status.set(f"Wrote {path}")


def main():
    # A desktop shortcut runs the app with pythonw, which has no console; keep messages in a log file instead.
    # 2.2.18 (item 24): through log_to_file (rotation, one lock), not a file held open.
    if sys.stdout is None or sys.stderr is None:
        stream = _LogFileStream()
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    log_to_file(f"{APP_TITLE} {APP_VERSION} started (Python {sys.version.split()[0]}).")
    enable_windows_dpi()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
