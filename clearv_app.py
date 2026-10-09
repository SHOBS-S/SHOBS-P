"""Shiloh Hill Observatory – Photometry (SHOBS-P): Windows desktop GUI for OSC photometry.

Flow: input folders -> calibrate (and debayer) -> aperture photometry -> light curve
and an AAVSO Extended File Format report (filter CV).
"""

from __future__ import annotations

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
APP_VERSION = "2.2.7"
APP_SHORT = "SHOBS-P"
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


# Labels the field scan gives the marked stars (and the 2.2.4 and older names).
MARKED_LABELS = ("Target", "Check", "Comp", "Comp 2") + tuple(f"Comp {i}" for i in range(1, MAX_COMPS + 1))
SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".shobs_p_settings.json")
LOG_FILE = os.path.join(os.path.expanduser("~"), ".shobs_p.log")
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
BLINK_SPEEDS = ("0.5 s/frame", "1 s/frame", "2 s/frame")  # 2.2.3
# Page indices (2.2: the Debayer step is gone; the first frame is debayered as soon as it is calibrated).
STEP_INPUT, STEP_BLINK, STEP_CAL, STEP_PHOTO, STEP_OUTPUT = range(5)


def col_width(widget, label: str, width: int) -> int:
    """A table column wide enough for its heading (bold) and scaled with the screen's DPI setting."""
    try:
        from tkinter import font as tkfont
        heading = tkfont.Font(widget, font=("Segoe UI", 10, "bold"))
        scale = max(float(widget.tk.call("tk", "scaling")) / 1.3333, 1.0)
        return int(max(width * scale, heading.measure(label) + 30))
    except Exception:
        return width


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


class ScrollBody:
    """2.2.2: a body that scrolls (vertically, and sideways when asked) only when it does not fit.
    Build the window's contents into .inner; call .fit() once they are in place, before fit_to_content."""

    def __init__(self, parent, horizontal: bool = False, bg: str = "#ffffff"):
        self.outer = ttk.Frame(parent, style="Card.TFrame")
        self.canvas = Canvas(self.outer, highlightthickness=0, borderwidth=0, background=bg)
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
        self.outer.bind("<Enter>", lambda _e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.outer.bind("<Leave>", self._leave)

    def _leave(self, event):
        # Moving onto a child widget is not leaving the body.
        if str(getattr(event, "detail", "")) == "NotifyInferior":
            return
        try:
            self.canvas.unbind_all("<MouseWheel>")
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

    def _stretch(self, event):
        # Let the contents fill the width when there is room; keep their own width when there is not.
        try:
            need = int(self.inner.winfo_reqwidth())
            self.canvas.itemconfigure(self._item, width=max(int(event.width), need))
        except Exception:
            pass

    def _wheel(self, event):
        try:
            if str(event.widget.winfo_class()) in ("TCombobox", "Text", "Listbox", "Treeview"):
                return
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
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
        self.observer = StringVar()
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
        self.focal_mm = DoubleVar(value=2900)
        self.pixel_um = DoubleVar(value=3.76)
        self.blink_source = StringVar(value="lights")
        self.notes = StringVar(value="OSC reduced; not transformed")
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
        self.scale_approx = False              # the plate scale is only approximate: widen Label chart's search
        self.app_version = APP_VERSION
        self.elevation = StringVar(value=str(self.settings.get("site_elevation", "")))
        for _key, _var in (("site_lat", self.lat), ("site_lon", self.lon), ("site_elevation", self.elevation)):
            _var.trace_add("write", lambda *_a, k=_key, v=_var: self._remember_site(k, v))
        # 2.2.2: telescope aperture for the scintillation term in the error bars (typed once, remembered).
        # 2.2.3: in millimetres; an aperture saved by 2.2.2 (in cm) is converted once on start.
        self.aperture_mm = StringVar(value=core.aperture_mm_from_settings(self.settings))
        self.aperture_hint = StringVar(value=core.aperture_mm_hint(self.aperture_mm.get()))
        self.use_scint = BooleanVar(value=bool(self.settings.get("use_scint", True)))
        self.aperture_mm.trace_add("write", lambda *_: self._remember_scint())
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
        self.planet_source = StringVar(value="Type the planet's name and press Lookup (NASA Exoplanet Archive; "
                                             "TOIs also from ExoFOP), or type the values.")

        self.masters = None
        self.calibrated = None
        self.masters_binning = None
        self.preview_is_debayered = False
        self.preview = None
        self.preview_header = None
        self.preview_path = None
        self.pick_mode = StringVar(value="target")
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
        self._counts = {}
        self.chart_placed = []
        self.comps_used = []
        self.check_used = ""
        self.star_bands = {}
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
        self._night_by_item = {}
        self.comp_catalog_mags = {}
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
        self.period_bins = StringVar(value="10 min bins")
        self.night_bin = {}
        self.legacy_mixed = set()
        self._prog = None
        self.scan_marks = []
        self.chart_solution = None
        self.catalog_period = None
        self.busy = False
        self._ui_queue = queue.Queue()
        self._photo_view = None            # zoomed view of the Photometry image: (xlim, ylim, shape)
        self._photo_press = None           # where a left-button press started (pan or click)
        self.field_centre_needed = False   # Discovery: lights without a plate solution or pointing in the header
        self.scan_excluded = []            # Discovery: (x, y) of stars left out of field scans
        self.suggested_comps = []          # comps near the target's brightness, from Label chart
        self._fit_after_photometry = False
        self._inst_cache = None
        self.mono_filter = None            # (AAVSO code, band) from a monochrome camera's FITS FILTER
        self._catalog_before_discovery = None
        self._closest_dm = None
        self._target_bv = None
        self._field_question_declined = None

        self._build()
        self.debayer_mode.trace_add("write", lambda *_: self._update_filter_hint())
        self.pattern.trace_add("write", lambda *_: self._pattern_changed())
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
        self.variable_marks = []
        self.field_variables = []
        self.field_check_sources = []
        self.chart_solution = None
        self._field_var_cache = None
        if hasattr(self, "field_check_text"):
            self.field_check_text.set("Variable-star check: Label chart to check the field against VSX and SIMBAD.")
        self.scan_marks = []
        self.suggested_comps = []
        self.masters = None
        self.masters_binning = None
        self.calibrated = None
        self.preview = None
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
        if hasattr(self, "pick_label"):
            self._update_pick_label()

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

    def _read_mono_filter(self, header: dict):
        """A monochrome camera's filter from the light's header (2.2.1)."""
        found = core.filter_from_header(header or {})
        if found != self.mono_filter:
            self.mono_filter = found
            if found and self.pattern.get() == core.MONO:
                self.log(f"Filter from the FITS header: {header.get('FILTER', header.get('FILTNAME', ''))} → AAVSO "
                         f"filter {found[0]}, comps in {found[1]}.")
        self._update_filter_hint()

    def _update_filter_hint(self):
        mode = self.debayer_mode.get()
        code, band = self._filter_code(), self._band()
        if self.pattern.get() == core.MONO and self.mono_filter:
            self.filter_hint.set(f"AAVSO filter code {code}\nfilter wheel (FITS FILTER), {band} comps")
        else:
            self.filter_hint.set(f"AAVSO filter code {code}\n{mode.capitalize()} channel, {band} comps")
        self.mag_header.set(f"{band} magnitude")

    def _style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", font=("Segoe UI", 10), background="#e7eef2")
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
        style.configure("Group.TLabelframe.Label", background="#ffffff", foreground="#16324a", font=("Segoe UI", 10, "bold"))
        style.configure("TNotebook.Tab", padding=(12, 6))
        style.configure("Status.TLabel", background="#10283b", foreground="#d5e0e6")
        style.configure("TEntry", fieldbackground="#ffffff")
        # Tall enough rows that text is never clipped on high-DPI Windows displays.
        style.configure("Treeview", rowheight=28, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), padding=(4, 6))
        for key, colour in PICK_COLORS.items():
            style.configure(f"Pick{key}.TRadiobutton", background="#ffffff", foreground=colour,
                            font=("Segoe UI", 10, "bold"))
            style.map(f"Pick{key}.TRadiobutton", background=[("active", "#f2f5f7")], foreground=[("active", colour)])
            style.configure(f"Pick{key}.TLabel", background="#ffffff", foreground=colour, font=("Segoe UI", 10, "bold"))
        style.configure("TCombobox", fieldbackground="#ffffff")
        for key, m in MODES.items():
            style.configure(f"Mode{key}.TButton", anchor=W, padding=(12, 7), background=m["step"], foreground="#ffffff",
                            bordercolor=m["active"], font=("Segoe UI", 10, "bold"))
            style.map(f"Mode{key}.TButton", background=[("active", m["active"])], foreground=[("active", "#ffffff")])
        style.configure("Warn.TLabel", background="#ffffff", foreground="#b35c00")
        style.configure("Good.TLabel", background="#ffffff", foreground="#2e7d32")
        style.configure("Bad.TLabel", background="#ffffff", foreground="#b00020")
        style.configure("Box.TLabel", background="#ffffff", foreground="#1c2b33", font=("Consolas", 9))
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
        style.configure("Banner.TLabel", background=m["step"], foreground="#ffffff", font=("Segoe UI", 11, "bold"))
        style.configure("Group.TLabelframe.Label", background="#ffffff", foreground=m["side"], font=("Segoe UI", 10, "bold"))

    def _build(self):
        outer = ttk.Frame(self)
        outer.pack(fill=BOTH, expand=True)

        self.side = ttk.Frame(outer, style="Side.TFrame", width=230)
        self.side.pack(side=LEFT, fill=Y)
        self.side.pack_propagate(False)
        ttk.Label(self.side, text="Shiloh Hill\nObservatory\nPhotometry\n(SHOBS-P)", font=("Segoe UI", 13, "bold"), style="Side.TLabel", justify=LEFT).pack(
            anchor=W, padx=16, pady=(18, 4)
        )
        self.subtitle = StringVar(value=MODES[self.mode_key]["subtitle"])
        ttk.Label(self.side, textvariable=self.subtitle, style="SideHint.TLabel").pack(anchor=W, padx=16, pady=(0, 10))
        ttk.Label(self.side, text="MODE", style="SideHint.TLabel", font=("Segoe UI", 8, "bold")).pack(anchor=W, padx=16)
        self.mode_buttons = {}
        for key, m in MODES.items():
            btn = ttk.Button(self.side, text=m["name"], style=f"Mode{key}.TButton", command=lambda k=key: self.set_mode(k))
            btn.pack(fill=X, padx=10, pady=1)
            self.mode_buttons[key] = btn
        ttk.Label(self.side, text="STEPS", style="SideHint.TLabel", font=("Segoe UI", 8, "bold")).pack(
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
        self.discovery_page = self._page_discovery_output()
        self.discovery_page.place(relx=0, rely=0, relwidth=1, relheight=1)
        import transit_ui
        self.transit_page = transit_ui.TransitPage(self, self.pages)
        self.transit_page.frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.credits_page = self._page_credits()
        self.credits_page.place(relx=0, rely=0, relwidth=1, relheight=1)

        status = ttk.Frame(self, style="Side.TFrame")
        status.pack(fill=X, side="bottom")
        self.status = StringVar(value="Choose the calibration and light folders to begin.")
        ttk.Label(status, textvariable=self.status, style="Status.TLabel").pack(anchor=W, padx=12, pady=(4, 0))
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
        ttk.Label(card, text="Credits and sources", font=("Segoe UI", 16, "bold"), style="Card.TLabel").pack(anchor=W)
        ttk.Label(
            card,
            text="Every library, catalog, service, and published method this app uses. Quoted sentences are the "
                 "acknowledgements each source asks for; please include them when you publish results made with it.",
            style="Hint.TLabel", wraplength=1200, justify=LEFT,
        ).pack(anchor=W, pady=(2, 8))
        nav = ttk.Frame(card, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", pady=(8, 0))
        ttk.Button(nav, text="Save credits as text…", command=self.save_credits).pack(side=LEFT)
        ttk.Button(nav, text="Back", command=lambda: self.show_step(self._before_credits)).pack(side=RIGHT)
        box = Text(card, wrap="word", font=("Segoe UI", 10), bg="#ffffff", fg="#1c2b33", relief="flat", padx=8, pady=8)
        box.pack(fill=BOTH, expand=True, before=nav)
        box.insert(END, core.credits_text())
        box.configure(state="disabled")
        return page

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
                fh.write(f"{APP_TITLE} {APP_VERSION}\n\n" + core.credits_text())
            self.status.set(f"Wrote {path}")

    def show_step(self, index: int):
        self.current_step = index
        self._on_credits = False
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
        # 2.2.3: once the page is on screen and laid out, make its plot fill its area (the first draw could land
        # small in the top-left corner until something else redrew it, e.g. the mouse wheel).
        self.after_idle(self._fit_visible_canvases)
        self.after(250, self._fit_visible_canvases)
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
            self.observations = []
            self.series_path = ""
            self.series_mode = ""
            self.period = None
            self.draw_output()
        self.mode_key = mode
        m = MODES[mode]
        self._apply_theme(mode)
        self.subtitle.set(m["subtitle"])
        self.banner_text.set(m["banner"])
        for key, btn in self.mode_buttons.items():
            btn.configure(text=("▶  " if key == mode else "     ") + MODES[key]["name"])
        self._layout_photo_for_mode()
        self._layout_input_for_mode()
        # Land on the Photometry page: always for Discovery (its work starts there), and for the other modes when a
        # frame is already loaded. Otherwise stay put (redrawing Output, whose layout follows the mode).
        if not initial and (mode == "discovery" or self.preview is not None):
            self.show_step(STEP_PHOTO)
            if mode == "discovery" and self.preview is not None and self.preview_is_debayered:
                if self.chart_placed:
                    self._field_summary()
                else:
                    self.after(150, self._auto_solve)
        elif getattr(self, "current_step", 0) == STEP_OUTPUT:
            self.show_step(STEP_OUTPUT)
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
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass
        try:
            self.log("Error: " + str(val))
        except Exception:
            pass
        messagebox.showerror(APP_TITLE, f"Something went wrong:\n{val}\n\nDetails were written to {LOG_FILE}.")

    def log(self, text: str):
        """Main thread only. Worker threads use self.call_ui(self.log, text)."""
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
        ttk.Button(row, text="✕", width=3, command=lambda: self._clear_folder(variable, key)).pack(side=LEFT, padx=(4, 0))
        count = StringVar(value="" if key == "Output" else "0 files")
        ttk.Label(row, textvariable=count, width=10, style="Card.TLabel").pack(side=LEFT, padx=(8, 0))
        self._counts[key] = count

    def _browse(self, variable, key):
        path = filedialog.askdirectory(title=f"Select {key} folder")
        if not path:
            return
        variable.set(path)
        if key == "Output":
            self._output_changed()
            return
        n = len(core.list_fits(path))
        self._counts[key].set(f"{n} files")
        self.status.set(f"{key}: {n} FITS files")

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

    def _remember_scint(self):
        """2.2.2: the aperture and the scintillation switch are remembered between sessions."""
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
        return ["".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in n) for n in names]

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
        return (id(obs), len(obs), round(obs[0].jd, 6) if obs else 0, round(obs[-1].jd, 6) if obs else 0,
                tuple(self.active_comps or self.comps_used), self.highlight_night if self._save_one_night else None)

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
        ttk.Label(card, text="Input", font=("Segoe UI", 16, "bold"), style="Card.TLabel").pack(anchor=W)
        ttk.Label(
            card,
            text="Frames, target, site, and where the report goes. Comparison, check, and watch stars are picked on "
                 "the Photometry page. Settings are saved with the series.",
            style="Hint.TLabel",
        ).pack(anchor=W, pady=(2, 8))

        nav = ttk.Frame(card, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", pady=(12, 0))
        ttk.Button(nav, text="Scan folders", command=self.scan_folders).pack(side=LEFT)
        ttk.Button(nav, text="Write demo FITS set", command=self.write_demo).pack(side=LEFT, padx=8)
        ttk.Button(nav, text="Load series", command=self.load_series).pack(side=LEFT, padx=8)
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
        ttk.Entry(cell, textvariable=self.planet_name, width=24).pack(side=LEFT, fill=X, expand=True)
        ttk.Button(cell, text="Lookup", command=self.lookup_planet).pack(side=LEFT, padx=(6, 0))
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

        # Site and optics
        site = self._group(right, "Site and optics")
        site.pack(fill=X, pady=(10, 0))
        self._field(site, 0, "Latitude (°, north +)", self.lat, width=12)
        self._field(site, 1, "Longitude (°, east +)", self.lon, width=12, hint="west is negative")
        ttk.Label(site, text="Binning", style="Card.TLabel").grid(row=2, column=0, sticky=W, padx=(0, 10), pady=3)
        ttk.Combobox(site, textvariable=self.binning, values=(1, 2, 4), width=6, state="readonly").grid(row=2, column=1, sticky=W, pady=3)
        ttk.Label(site, text="same-color pixels only; colors stay separate", style="Hint.TLabel").grid(
            row=2, column=2, sticky=W, padx=(8, 0))
        self._field(site, 3, "Focal length (mm)", self.focal_mm, width=12)
        self._field(site, 4, "Pixel size (µm)", self.pixel_um, width=12)
        self._field(site, 5, "Elevation (m)", self.elevation, width=12, hint="for exoplanet reports and scintillation")
        self._field(site, 6, "Telescope aperture (mm)", self.aperture_mm, width=12, hint="for the scintillation error")
        ttk.Label(site, textvariable=self.aperture_hint, style="Hint.TLabel", foreground="#a32020").grid(
            row=8, column=0, columnspan=3, sticky=W)
        ttk.Checkbutton(site, text="Include scintillation (atmospheric twinkling) in the error bars",
                        variable=self.use_scint).grid(row=7, column=0, columnspan=3, sticky=W, pady=(2, 3))
        site.columnconfigure(1, weight=1)
        return page

    # ---- planet (Transits) ---------------------------------------------------------------
    def lookup_planet(self):
        name = self.planet_name.get().strip()
        if not name:
            messagebox.showinfo(APP_TITLE, "Type the planet's name first, under any of its names (for example TrES-3 b, "
                                           "KOI-217 b or Kepler-71 b, TOI-2046 b, EPIC 220504338 b).")
            return
        self.planet_source.set(f"Looking up {name}…")

        def work():
            try:
                self.call_ui(self._planet_lookup_done, tcore.lookup_planet(name), None)
            except Exception as exc:
                self.call_ui(self._planet_lookup_done, None, exc)

        self._lookup_worker = threading.Thread(target=work, daemon=True)
        self._lookup_worker.start()

    def _planet_lookup_done(self, p, error):
        if isinstance(error, tcore.PlanetChoice):
            # 2.2.6: a star with several planets: ask which one, then look that one up.
            self.planet_source.set(f"{error.system}: choose a planet.")
            self._choose_planet(error.system, error.planets, getattr(error, "transiting", set()))
            return
        if error is not None:
            self.planet_source.set("Lookup failed.")
            messagebox.showerror(APP_TITLE, f"Planet lookup failed:\n{error}")
            return
        self.planet_info = dict(p)
        self._planet_to_fields(p)
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
        if p.get("ra") is not None and (not self.ra_hours.get().strip() or different):
            self.ra_hours.set(f"{p['ra'] / 15.0:.6f}")
            self.dec_deg.set(f"{p['dec']:.5f}")
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

    def clear_target(self, quiet: bool = False):
        """Empty the target (Star ID, RA, Dec) and the marked stars and comp/check fields that belong to it."""
        self.star_id.set("")
        self.ra_hours.set("")
        self.dec_deg.set("")
        self.target_xy = None
        for role in COMP_ROLES + ("check",):
            self.clear_mark(role, redraw=False)
        self.star_bands = {}
        self.suggested_comps = []
        self.catalog_period = None
        self.chart.set("")
        if hasattr(self, "pick_label"):
            self._update_pick_label()
            self.update_star_panel()
        self.refresh_preview()
        if not quiet:
            self.status.set("Target, comps, and check cleared. Type the new Star ID and press Enter to look it up.")

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
        self.clear_target(quiet=True)
        if not generic:
            self.star_id.set(obj)
            self.lookup_star()
        self.log(f"Old target {name} cleared: these lights are a different field."
                 + ("" if generic else f" Looking up {obj}."))

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
        self.catalog_period = found.get("period_days")
        extra = ""
        if self.catalog_period:
            extra = f"   VSX period {self.catalog_period:.6f} d"
            if found.get("var_type"):
                extra += f" ({found['var_type']})"
        if self.mode_key == "transits":
            self._planets_for_star(name or found.get("name", ""), found)
        note = f" ({found['note']})" if found.get("note") else ""
        self.status.set(
            f"{found['name']} from {found.get('source', 'catalog')}{note}: RA {found['ra_hours']:.5f} h, "
            f"Dec {found['dec_deg']:.4f}° at the current epoch{extra}"
        )

    def scan_folders(self):
        for key, var in (
            ("Bias", self.bias_dir),
            ("Dark", self.dark_dir),
            ("Flat", self.flat_dir),
            ("Lights", self.light_dir),
        ):
            n = len(core.list_fits(var.get()))
            other = core.unreadable_files(var.get())
            self._counts[key].set(f"{n} files" + (f" ({len(other)} unreadable)" if other else ""))
            if other and not n:
                self.log(f"The {key} folder holds {len(other)} file(s) SHOBS-P cannot read (for example "
                         f"{other[0]}). It reads FITS and XISF: save or export them in one of those.")
        lights, skipped, _note = core.split_lights(core.list_fits(self.light_dir.get()))
        if skipped:
            self._counts["Lights"].set(f"{len(lights)} files (+{len(skipped)} cal skipped)")
        if lights:
            try:
                _, header = core.read_fits(lights[0])
                self._check_lights_pointing(header)
                self._read_mono_filter(header)
                self._check_target_field(header)
                if not core.is_microobservatory(lights[0], header):
                    self._restore_own_setup()
                pat = core.header_bayer(header)
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
        mono = self.pattern.get() == core.MONO
        core.set_mono(mono)
        if hasattr(self, "filter_hint"):
            self._update_filter_hint()
        if mono and self.debayer_mode.get() != "luminance":
            self.debayer_mode.set("luminance")

    def _setup_microobservatory(self, header: dict, n_lights: int):
        """MicroObservatory frames: monochrome, 12-bit, taken in Arizona with a 560 mm telescope. Offer the
        telescope's camera, site, and optics; your own come back when you scan your own lights."""
        site = core.header_site(header)
        if site and site[1] > 0:
            # MicroObservatory writes the longitude west-positive (110.88 for Arizona); SHOBS-P uses east-positive.
            site = (site[0], -site[1], site[2])
        lat, lon, elev = site if site else (31.68, -110.88, 2340.0)
        where = "from the FITS header" if site else "approximately, Whipple Observatory on Mt. Hopkins, Arizona"
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
            + ("" if scale else "; Label chart refines it") + ")\n"
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
            self._own_setup = {name: getattr(self, name).get() for name in
                               ("lat", "lon", "elevation", "focal_mm", "pixel_um", "pattern", "debayer_mode",
                                "sat_limit", "binning")}
        self.pattern.set(core.MONO)
        self.debayer_mode.set("luminance")
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
        self.log(f"MicroObservatory setup: site {lat:.4f}, {lon:.4f} ({where}); optics {focal:.0f} mm, {pixel:.2f} µm "
                 f"({'from the header scale' if scale else 'approximate'}); monochrome, CV, saturation 4000 ADU. "
                 "Your own setup comes back when you scan your own lights.")
        self.status.set(f"{n_lights} MicroObservatory lights: telescope setup in use.")

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
        self.log("Your own site, optics, and camera settings are back (they were set aside for MicroObservatory frames).")

    def _page_blink(self):
        page = ttk.Frame(self.pages)
        card = ttk.Frame(page, style="Card.TFrame", padding=12)
        card.pack(fill=BOTH, expand=True)
        ttk.Label(card, text="Blink", font=("Segoe UI", 16, "bold"), style="Card.TLabel").pack(anchor=W)
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
        ttk.Combobox(tools, textvariable=self.blink_speed, values=BLINK_SPEEDS, width=9, state="readonly").pack(side=LEFT)
        ttk.Button(tools, text="Reject / restore", command=self.toggle_reject).pack(side=LEFT, padx=6)
        self.blink_label = StringVar(value="No lights loaded.")
        ttk.Label(card, textvariable=self.blink_label, style="Hint.TLabel").pack(anchor=W)
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
        self.blink_paths = core.list_fits(folder)
        if self.blink_source.get() == "lights":
            self.blink_paths = core.split_lights(self.blink_paths)[0]
        self.blink_index = min(self.blink_index, max(0, len(self.blink_paths) - 1))
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

    def _blink_picture(self, path, binning):
        """Read one frame and make the small 8-bit picture Blink shows (about 1000 px wide)."""
        data, header = core.read_fits(path)
        data = core.bin_image(data, binning)
        if data.shape[1] > 1000:
            step = int(math.ceil(data.shape[1] / 1000))
            data = data[::step, ::step]
        sample = data[::4, ::4]
        finite = sample[np.isfinite(sample)]
        lo, hi = (np.percentile(finite, [5, 99.5]) if finite.size else (0.0, 1.0))
        if not hi > lo:
            hi = lo + 1.0
        pic = np.clip((np.nan_to_num(data, nan=lo) - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)
        return pic, core.header_exptime(header)

    def _blink_store(self, key, value):
        self._blink_cache[key] = value
        self._blink_cache_order.append(key)
        # Keep memory bounded (about 700 MB of pictures); the oldest go first.
        limit = max(50, int(7e8 // max(1, value[0].nbytes)))
        while len(self._blink_cache_order) > limit:
            old = self._blink_cache_order.pop(0)
            self._blink_cache.pop(old, None)

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
                self.blink_label.set(f"Reading frame {nxt + 1}/{len(self.blink_paths)} …")
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

    def toggle_reject(self):
        if not self.blink_paths:
            return
        path = self.blink_paths[self.blink_index]
        if path in self.rejected:
            self.rejected.remove(path)
        else:
            self.rejected.add(path)
        self.show_blink()

    def show_blink(self):
        if not self.blink_paths:
            self.blink_label.set("No lights in the lights folder.")
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
        pic, exptime = cached
        mark = "REJECTED" if rejected else "keep"
        self.blink_label.set(
            f"{self.blink_index + 1}/{len(self.blink_paths)}  {mark}  rejected {len(self.rejected)}  {os.path.basename(path)}  "
            f"exp {exptime:.1f}s"
        )
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
        self.blink_ax.set_title("REJECTED" if rejected else os.path.basename(path),
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

    def _page_calibrate(self):
        page = ttk.Frame(self.pages)
        card = ttk.Frame(page, style="Card.TFrame", padding=16)
        card.pack(fill=BOTH, expand=True)
        ttk.Label(card, text="Calibrate", font=("Segoe UI", 16, "bold"), style="Card.TLabel").pack(anchor=W)
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
        ttk.Combobox(row, textvariable=self.pattern, values=("RGGB", "BGGR", "GRBG", "GBRG", "MONO"), width=10,
                     state="readonly").pack(side=LEFT, padx=8)
        ttk.Label(row, text="Channel", style="Card.TLabel").pack(side=LEFT, padx=(12, 0))
        ttk.Combobox(row, textvariable=self.debayer_mode, values=("green", "red", "blue", "luminance"), width=12,
                     state="readonly").pack(side=LEFT, padx=8)
        ttk.Button(row, text="Apply channel to the preview", command=self.apply_debayer).pack(side=LEFT, padx=8)
        ttk.Label(row, textvariable=self.filter_hint, style="Hint.TLabel", justify=LEFT).pack(side=LEFT, padx=(12, 0))
        ttk.Label(
            cam,
            text="Green is the normal choice (AAVSO TG, V comps). Red (TR, R comps) suits a red dwarf; blue is TB; luminance "
                 "is clear, reported as CV. MONO is for monochrome cameras (MicroObservatory and others): no debayer. The "
                 "pattern is read from the FITS header when the camera writes it.",
            style="Hint.TLabel", wraplength=1100, justify=LEFT,
        ).pack(anchor=W, pady=(6, 0))
        cal_row = ttk.Frame(card, style="Card.TFrame")
        cal_row.pack(anchor=W)
        ttk.Button(cal_row, text="Build masters and calibrate", style="Accent.TButton",
                   command=self.run_calibration).pack(side=LEFT)
        ttk.Button(cal_row, text="Cancel", command=self.cancel_job).pack(side=LEFT, padx=8)
        self.debayer_info = StringVar(value="No preview yet.")
        ttk.Label(cal_row, textvariable=self.debayer_info, style="Hint.TLabel").pack(side=LEFT, padx=(12, 0))
        self.log_box = Text(card, height=18, wrap="word", font=("Consolas", 10), bg="#1e1e1e", fg="#dcdcdc",
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
            flat_rot = core.typical_rotator(flat_rots)
            spread = core.rotator_spread(flat_rots)
            if spread is not None and spread > 2.0:
                if not messagebox.askyesno(
                        APP_TITLE,
                        f"The Flat folder mixes camera angles: {core.rotator_summary(flat_rots)}.\n\n"
                        "One master flat is built from all of them, so it matches none exactly. (Readings 180° apart "
                        "are the same camera position across a meridian flip and are not counted as different.)\n\n"
                        "Calibrate with these flats anyway? (No: stop, so you can keep only the flats taken at the "
                        "lights' angle.)"):
                    self.status.set("Calibration not started: the flats were taken at more than one camera angle.")
                    return None
                self.log(f"Flats at several camera angles ({core.rotator_summary(flat_rots)}): used anyway, as chosen.")
            if flat_rot is not None and core.rotator_difference(flat_rot, light_rot) > 2.0:
                if not messagebox.askyesno(
                        APP_TITLE,
                        f"The flats were taken at rotator angle {flat_rot:g}° and the lights at {light_rot:g}°.\n\n"
                        "Vignetting and dust shadows from the optics in front of the rotator turn with the camera, so a "
                        "flat taken at another angle corrects the wrong pattern. Errors of about 1% are typical, more "
                        "when stars move on the sensor during the night (enough to fake or hide a shallow transit).\n\n"
                        "Calibrate with these flats anyway? (No: stop, so you can pick flats taken at the lights' "
                        "angle, or clear the Flat folder.)"):
                    self.status.set("Calibration not started: flats and lights at different rotator angles.")
                    return None
                self.log(f"Flats at rotator {flat_rot:g}°, lights at {light_rot:g}°: used anyway, as chosen.")
        return dark

    @staticmethod
    def _rotator_survey(lights) -> tuple[str, str]:
        """Rotator readings through the night: (meridian flips, real rotator moves), each '' when there were none.
        A reading that changes by 180° is a meridian flip (same camera position on the telescope); any other change
        is the camera turning on the telescope."""
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
                flip = core.rotator_difference(prev, rot) <= 2.0
                if flip and pier is not None and prev_pier is not None and pier == prev_pier:
                    # Same side of the pier: the camera itself turned 180°, it was not a meridian flip.
                    flip = False
                (flips if flip else moves).append(f"before frame {k} ({prev:g}° to {rot:g}°)")
            prev, prev_pier = rot, pier
        return "; ".join(flips), "; ".join(moves)

    def run_calibration(self):
        bias = core.list_fits(self.bias_dir.get())
        dark = core.list_fits(self.dark_dir.get())
        flat = [path for path in core.list_fits(self.flat_dir.get()) if path not in self.rejected]
        lights = self._lights(log_skips=True)
        unreadable = []
        for key, var, found in (("Bias", self.bias_dir, bias), ("Dark", self.dark_dir, dark),
                                ("Flat", self.flat_dir, flat), ("Lights", self.light_dir, lights)):
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
            signs = core.precalibration_signs(lights[0], core.read_fits(lights[0])[1])
        except Exception:
            signs = []
        self.precalibrated = bool(signs)
        if signs:
            reasons = "\n  • ".join(signs)
            if bias or dark or flat:
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
                else:
                    self.precalibrated = False
        if not self.precalibrated:
            checked = self._calibration_checks(bias, dark, flat, lights)
            if checked is None:
                return
            dark = checked
        if not self._start_job("Building calibration masters…"):
            return
        if self.precalibrated:
            self.log("Lights are pre-calibrated: no bias, dark, or flat applied; saturation judged from flat-topped "
                     "star cores, not the raw level. ("
                     + "; ".join(signs) + ")")
        missing = [name for name, paths in (("bias", bias), ("dark", dark), ("flat", flat)) if not paths]
        if missing:
            self.log("Note: no " + ", ".join(missing) + " frames. That step is skipped.")
        self.log("Building calibration masters. The window stays live; this can take several minutes for 100 full frames.")

        cancel = self._cancel

        def progress_log(text):
            if cancel.is_set():
                raise RuntimeError("Cancelled")
            self.call_ui(self.log, text)

        def work():
            try:
                masters = core.build_masters(bias, dark, flat, binning, progress_log, flats_calibrated=flats_calibrated)
                path = lights[0]
                data, header = core.read_fits(path)
                data = core.bin_image(data, binning)
                calibrated = core.calibrate_frame(data, header, masters)
                flips, moves = self._rotator_survey(lights) if len(lights) > 1 else ("", "")
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
            self.log(detail)
            messagebox.showerror(APP_TITLE, f"Calibration failed:\n{message}")
            return
        self.masters = masters
        self.masters_binning = binning
        self.calibrated = calibrated
        self.preview = calibrated
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
        if pat:
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
            self.log("The lights carry a plate solution in their FITS headers: Label chart uses it directly.")
        # 2.2: no separate Debayer step. Debayer the preview now and go straight to Photometry.
        self.apply_debayer(auto=True)

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
        self.preview_is_debayered = True
        if old_shape is not None and tuple(old_shape[:2]) != tuple(mono.shape[:2]):
            self._rescale_marks(old_shape, mono.shape)
        self._marks_shape = tuple(mono.shape[:2])
        self.pick_mode.set("watch" if self.mode_key == "discovery" else "target")
        self.chart_placed = []
        self.chart_solution = None
        self.suggested_comps = []
        self._photo_view = None
        self._photo_drawn_shape = None
        channel = "monochrome" if self.pattern.get() == core.MONO else f"{self.debayer_mode.get()} from {self.pattern.get()}"
        self.debayer_info.set(f"Preview: {channel}  →  {mono.shape[1]}×{mono.shape[0]}"
                              + ("" if self.pattern.get() == core.MONO else " (superpixel)"))
        self.log(f"Preview debayered: {channel}, {mono.shape[1]}×{mono.shape[0]} px. Star positions are on this image.")
        self.refresh_preview()
        self.show_step(STEP_PHOTO)
        if self.mode_key == "discovery":
            self.status.set("Calibrated and debayered. Solving the field…")
            self.after(150, self._auto_solve)
        else:
            self.status.set("Calibrated and debayered. Click the target, then Label chart and pick the comparison stars.")

    def _page_photo(self):
        page = ttk.Frame(self.pages)
        card = ttk.Frame(page, style="Card.TFrame", padding=12)
        card.pack(fill=BOTH, expand=True)
        self.photo_title = StringVar(value="Photometry")
        ttk.Label(card, textvariable=self.photo_title, font=("Segoe UI", 16, "bold"), style="Card.TLabel").pack(anchor=W)
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
                            style=f"Pick{value}.TRadiobutton").pack(side=LEFT, padx=6)
        ttk.Button(self.pick_tools, text="Clear selected", command=self.clear_selected_mark).pack(side=LEFT, padx=(2, 6))
        # Discovery: no target; a click watches a star or leaves it out of the scan.
        self.disc_tools = ttk.Frame(tools, style="Card.TFrame")
        ttk.Label(self.disc_tools, text="Click a star to", style="Card.TLabel").pack(side=LEFT, padx=(4, 6))
        ttk.Radiobutton(self.disc_tools, text="●  Watch it", value="watch", variable=self.pick_mode,
                        style="Pickwatch.TRadiobutton").pack(side=LEFT, padx=6)
        ttk.Radiobutton(self.disc_tools, text="✕  Exclude it", value="exclude", variable=self.pick_mode,
                        style="Pickcheck.TRadiobutton").pack(side=LEFT, padx=6)
        ttk.Label(self.disc_tools, text="(right-click a marker to undo)", style="Hint.TLabel").pack(side=LEFT, padx=6)
        # 2.2.5: one switch to see only the scan candidates and watch stars (the other switches are kept as they are).
        ttk.Checkbutton(self.disc_tools, text="Candidates only", variable=self.candidates_only,
                        command=self.refresh_preview).pack(side=LEFT, padx=(14, 6))
        self.run_button = ttk.Button(tools, text="Run photometry", style="Accent.TButton", command=self.run_photometry)
        self.scan_button = ttk.Button(tools, text="Scan field", style="Accent.TButton", command=self.scan_field)
        self.run_button.pack(side=RIGHT)
        self.label_button = ttk.Button(tools, text="Label chart", command=self.label_aavso)
        self.label_button.pack(side=RIGHT, padx=6)
        ttk.Combobox(tools, textvariable=self.catalog, values=core.CATALOGS, width=15, state="readonly").pack(side=RIGHT)
        ttk.Label(tools, text="Catalog", style="Card.TLabel").pack(side=RIGHT, padx=(12, 4))
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
        ttk.Checkbutton(
            options, text="Show variables (red ○ ×)", variable=self.show_variables, command=self.refresh_preview,
        ).pack(side=LEFT, padx=(0, 14))
        ttk.Button(options, text="Fit view", command=self.reset_photo_view).pack(side=RIGHT)
        ttk.Checkbutton(options, text="Show labels", variable=self.show_labels, command=self.refresh_preview).pack(
            side=RIGHT, padx=(0, 10))
        # 2.2.5: redraw the aperture rings shortly after the sizes are typed (or set by Suggest…).
        for _var in (self.radius, self.sky_in, self.sky_out):
            _var.trace_add("write", lambda *_a: self._aperture_sizes_changed())
        # 2.2.5: overlay switches.
        ttk.Checkbutton(options, text="Show catalog stars", variable=self.show_catalog,
                        command=self.refresh_preview).pack(side=RIGHT, padx=(0, 10))
        ttk.Checkbutton(options, text="Show apertures", variable=self.show_apertures,
                        command=self.refresh_preview).pack(side=RIGHT, padx=(0, 10))

        self.pick_label = StringVar(value="No stars marked.")
        self.pick_label_widget = ttk.Label(card, textvariable=self.pick_label, style="Hint.TLabel")
        self.pick_label_widget.pack(anchor=W)

        body = ttk.Frame(card, style="Card.TFrame")
        body.pack(fill=BOTH, expand=True, pady=6)
        self.photo_body = body
        panel = ttk.Frame(body, style="Card.TFrame", width=520)
        panel.pack(side=RIGHT, fill=Y, padx=(8, 0))
        panel.pack_propagate(False)
        self.star_panel = ttk.Frame(panel, style="Card.TFrame")
        self._build_star_panel(self.star_panel)
        self.disc_panel = ttk.Frame(panel, style="Card.TFrame")
        self._build_discovery_panel(self.disc_panel)
        self.star_panel.pack(fill=BOTH, expand=True)
        plot_host = ttk.Frame(body, style="Card.TFrame")
        plot_host.pack(side=LEFT, fill=BOTH, expand=True)
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
            self.photo_canvas.mpl_connect("button_press_event", self._photo_press_event)
            self.photo_canvas.mpl_connect("button_release_event", self._photo_release_event)
            self.photo_canvas.mpl_connect("scroll_event", self._photo_scroll)
            self.photo_canvas.mpl_connect("motion_notify_event", self._photo_motion)
        nav = ttk.Frame(card, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", before=body, pady=(6, 0))
        ttk.Button(nav, text="Back", command=lambda: self.show_step(STEP_CAL)).pack(side=LEFT)
        ttk.Button(nav, text="Next: Output", command=lambda: self.show_step(STEP_OUTPUT)).pack(side=RIGHT)
        # Live log (the same messages as the Calibrate page log) and a progress bar with an ETA.
        self.photo_logf = ttk.Frame(card, style="Card.TFrame")
        self.photo_log = Text(self.photo_logf, height=7, wrap="none", font=("Consolas", 9), bg="#1e1e1e", fg="#dcdcdc",
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

    def _layout_photo_for_mode(self):
        """The Photometry page follows the mode: Discovery has no target, comps, or check star (2.2)."""
        if not hasattr(self, "run_button"):
            return
        mode = self.mode_key
        self.run_button.pack_forget()
        self.scan_button.pack_forget()
        self.pick_tools.pack_forget()
        self.disc_tools.pack_forget()
        self.star_panel.pack_forget()
        self.disc_panel.pack_forget()
        self.pick_label_widget.pack_forget()
        if mode != "discovery":
            self.pick_label_widget.pack(anchor=W, before=self.photo_body)
        if mode == "discovery":
            self.disc_tools.pack(side=LEFT)
            self.disc_panel.pack(fill=BOTH, expand=True)
            self.scan_button.pack(side=RIGHT, before=self.label_button)
            self.label_button.configure(text="Solve field")
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
            self.label_button.configure(text="Label chart")
            if self.pick_mode.get() == "exclude":
                self.pick_mode.set("target")
            self.photo_title.set("Photometry")
            self.photo_hint.set(
                "Click the image to mark the target, comparison, comp 2, and check star; Watch adds a star to follow. "
                "After Label chart, clicking a circled star fills in its ID and magnitude, shown in the panel at right "
                "with its catalog color and a variable-star check. Right-click a marker to clear it.")

    # ---- Discovery panel (2.2) ------------------------------------------------------------
    def _build_discovery_panel(self, panel):
        field = self._group(panel, "Field")
        field.pack(fill=X)
        self.disc_field_text = StringVar(value="Not solved yet. Calibrate, and the field is solved straight away.")
        ttk.Label(field, textvariable=self.disc_field_text, style="Card.TLabel", wraplength=480, justify=LEFT).pack(
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
        ttk.Checkbutton(settings, text="Leave out crowded stars (neighbor in the sky ring)",
                        variable=self.scan_skip_crowded).grid(row=4, column=0, columnspan=3, sticky=W, pady=(4, 0))
        ttk.Label(settings, text="Saturated and edge stars are measured but never listed as candidates. "
                                 "\"× normal\" compares a star's scatter with stars as bright.",
                  style="Hint.TLabel", wraplength=440, justify=LEFT).grid(
            row=5, column=0, columnspan=3, sticky=W, pady=(4, 0))
        picks = self._group(panel, "Watched and excluded stars")
        picks.pack(fill=BOTH, expand=True, pady=(10, 0))
        self.disc_box = Listbox(picks, height=7, font=("Segoe UI", 9), activestyle="none", exportselection=False)
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
            lines.append(f"{len(self.chart_placed)} {self.catalog.get()} stars placed"
                         + (f", V {min(mags):.1f} to {max(mags):.1f}." if mags else "."))
        else:
            lines.append("Not solved yet. Solve field (or wait for it after calibrating).")
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

    def _auto_solve(self):
        """Discovery: solve the field as soon as the frame is calibrated (2.2)."""
        if self.mode_key != "discovery" or self.busy or self.preview is None or not self.preview_is_debayered:
            return
        self.label_aavso(auto=True)

    # ---- aperture suggestion ------------------------------------------------------------
    def _aperture_sane(self) -> bool:
        """Before a run: is the aperture far larger (or smaller) than the stars on this frame? Typical after
        switching telescopes, for example to MicroObservatory frames with your own aperture still set."""
        if self.preview is None or not self.preview_is_debayered:
            return True
        first_comp = next((getattr(self, f"{r}_xy") for r in self._comp_roles_marked()), None)
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
        if r > 3.0 * fwhm or r < 0.9 * fwhm:
            kind = "much larger" if r > 3.0 * fwhm else "smaller"
            self.log(f"Aperture check: r = {r:g} px is {kind} than the stars (FWHM {fwhm:.1f} px); suggested r = "
                     f"{prop['radius']:g}, sky {prop['sky_in']:g}-{prop['sky_out']:g} px.")
            return messagebox.askyesno(
                APP_TITLE,
                f"The aperture (r = {r:g} px) is {kind} than the stars on this frame (FWHM {fwhm:.1f} px).\n\n"
                + ("A large aperture collects sky noise and neighboring stars; scatter can be several times worse. "
                   if r > 3.0 * fwhm else "A small aperture loses starlight as the seeing changes. ")
                + f"Suggest… would use r = {prop['radius']:g}, sky {prop['sky_in']:g}–{prop['sky_out']:g} px.\n\n"
                "Run with the current aperture anyway?")
        return True

    def _marked_stars(self):
        return [(role_label(role), xy) for role, xy in self._marked_roles()]

    def _fwhm_at(self, img, xy):
        try:
            m = core.measure_aperture(img, xy[0], xy[1], 12.0, 16.0, 24.0)
            fwhm, _e, peak = core.shape_metrics(img, xy[0], xy[1], 12.0, m["sky"])
            return fwhm, peak
        except Exception:
            return float("nan"), float("nan")

    def suggest_apertures(self):
        if self.preview is None or not self.preview_is_debayered:
            messagebox.showinfo(APP_TITLE, "Calibrate first (Calibrate page); star sizes are measured on the calibrated, debayered image.")
            return
        img = self.preview
        stars = self._marked_stars()
        if not stars:
            found = core.detect_sources(img, max_sources=12)
            stars = [(f"star {k + 1}", xy) for k, xy in enumerate(found[:8])]
        sizes = []
        for role, xy in stars:
            fwhm, peak = self._fwhm_at(img, xy)
            if math.isfinite(fwhm) and 0.8 < fwhm < 40:
                sizes.append((role, fwhm, peak))
        if not sizes:
            messagebox.showinfo(APP_TITLE, "Could not measure star sizes on this frame. Mark the target and comps first.")
            return
        fwhm = float(np.median([f for _r, f, _p in sizes]))
        prop = core.suggest_apertures(fwhm)
        notes = self._neighbor_notes(img, prop)
        SuggestWindow(self, fwhm, sizes, prop, notes)

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
        sky_in = float(math.ceil(max(rmax + 3.0, 3.2 * fwhm)))
        sky_out = float(math.ceil(math.sqrt(sky_in ** 2 + 4.0 * rmax ** 2)))
        n = min(frames, len(lights))
        picks = [lights[int(round(k))] for k in np.linspace(0, len(lights) - 1, n)]
        pts = [self.target_xy or test_c1, test_c1, test_c2, self.check_xy]
        xy = np.array([p if p is not None else (np.nan, np.nan) for p in pts], dtype=float)
        binning = max(int(self.binning.get()), 1)
        masters, pattern, mode = self.masters, self.pattern.get(), self.debayer_mode.get()
        ref = self.preview
        ref_sources = core.detect_sources(ref, max_sources=80)
        cancel = self._cancel
        self._progress_start(n, "Aperture test")
        self.log(f"Aperture test: r = {', '.join(f'{r:g}' for r in radii)} px, sky {sky_in:g}–{sky_out:g} px, "
                 f"{n} frames; judged on the {ref_label} against the comps.")

        def work():
            rows = {r: [] for r in radii}
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
                        reg = core.register_frame(ref_sources, src, data.shape)
                        if reg is None and last["sources"] is not None:
                            step = core.register_frame(last["sources"], src, data.shape)
                            if step is not None:
                                reg = core.compose_registration(last["reg"], step, data.shape)
                        if reg is None:
                            raise ValueError("could not align")
                        last["sources"], last["reg"] = src, reg
                        good = np.isfinite(xy[:, 0])
                        pos = np.full_like(xy, np.nan)
                        pos[good] = core.move_points(xy[good], reg, data.shape)
                        f, _p = self._fwhm_at(data, tuple(pos[1]))
                        if math.isfinite(f):
                            seeing.append(f)
                        exp = core.header_exptime(header)
                        frame_rows = {}
                        for r in radii:
                            flux, _sky = core.measure_many(data, pos[good], r, sky_in, sky_out)
                            mags = np.full(4, np.nan)
                            with np.errstate(divide="ignore", invalid="ignore"):
                                mags[good] = np.where(flux > 0, -2.5 * np.log10(flux / exp), np.nan)
                            frame_rows[r] = mags
                        for r in radii:  # all apertures or none, so every aperture has the same frames
                            rows[r].append(frame_rows[r])
                    except Exception as exc:
                        self.call_ui(self.log, f"  aperture test skipped {os.path.basename(path)}: {exc}")
                    self.call_ui(self._progress, i)
                result = core.aperture_test_stats({r: np.array(rows[r]) for r in radii if rows[r]}, ref_label,
                                                  has_comp2=not np.isnan(xy[2, 0]))
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
        if self.photo_canvas is None or self.preview is None:
            return
        img = self.preview
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
        finite = img[np.isfinite(img)]
        if finite.size:
            lo, hi = np.percentile(finite, [5, 99.5])
        else:
            lo, hi = 0, 1
        self.photo_ax.imshow(img, cmap="gray", origin="upper", vmin=lo, vmax=hi, interpolation="nearest")
        self._mark(self.target_xy, PICK_COLORS["target"], "T")
        for role in COMP_ROLES:
            self._mark(getattr(self, f"{role}_xy"), PICK_COLORS[role], f"C{comp_number(role)}")
        self._mark(self.check_xy, PICK_COLORS["check"], "K")
        for x, y, star in self.chart_placed:
            if not self._overlay("variables" if star.get("variable") else "catalog"):
                continue
            if 0 <= x < img.shape[1] and 0 <= y < img.shape[0]:
                colour = "#ff5252" if star.get("variable") else "#ffd24a"
                self.photo_ax.plot(x, y, marker="o", mfc="none", mec=colour, ms=9, lw=1.0 if star.get("variable") else 0.8)
                if self.show_labels.get():
                    self.photo_ax.text(x + 4, y - 4, str(star.get("label", "")), color=colour, fontsize=7)
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
        self.photo_fig.tight_layout()
        view = self._photo_view
        if view is not None and view[2] == img.shape:
            self.photo_ax.set_xlim(view[0])
            self.photo_ax.set_ylim(view[1])
        self._photo_drawn_shape = img.shape
        self.photo_canvas.draw_idle()
        self._update_legend()

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
            label = "Comp C1" if nums == [1] else (f"Comps C{nums[0]}–C{nums[-1]}" if nums == list(range(nums[0], nums[-1] + 1))
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
        if self._overlay("catalog") and any(not st.get("variable") for _x, _y, st in self.chart_placed):
            items.append(("○", "Catalog star" + ("" if self.show_labels.get() else " (labels hidden)"), "#e0b000"))
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
                      font=("Segoe UI", 9, "bold")).pack(side=LEFT, padx=(0, 12))
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
        h, w = self.preview.shape
        factor = 1 / 1.3 if event.button == "up" else 1.3
        x0, x1 = self.photo_ax.get_xlim()
        y0, y1 = self.photo_ax.get_ylim()
        cx, cy = event.xdata, event.ydata
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
        if mags.get("B") is not None and mags.get("V") is not None:
            parts.append(f"(B-V {mags['B'] - mags['V']:.2f})")
        return "  ".join(parts) or "no magnitudes"

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
        self.clear_mark(mode)

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
            self.log("Spare comps cleared with the binning change; Label chart again to pick spares on this image.")
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
        try:
            radius = float(self.radius.get())
            xy = core.recenter(self.preview, xy[0], xy[1], radius)[:2]
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
        if mode == "comp" or comp_number(mode):
            # 2.2.5: each click with Comps adds the next comparison star (C1, C2 ... C10). Clicking a star that is
            # already a comp does nothing new.
            for r in COMP_ROLES:
                old = getattr(self, f"{r}_xy")
                if old is not None and math.hypot(old[0] - xy[0], old[1] - xy[1]) < 2.0:
                    self.status.set(f"That star is already {role_label(r)}.")
                    return
            mode = self._next_comp_role(xy)
            if mode is None:
                messagebox.showinfo(APP_TITLE, f"All {MAX_COMPS} comparison stars are marked. Right-click one to "
                                               "remove it first.")
                return
        if mode == "target":
            self.target_xy = xy
        elif comp_number(mode):
            setattr(self, f"{mode}_xy", xy)
        else:
            self.check_xy = xy
        filled = self._fill_from_chart(mode, xy)
        self._update_pick_label()
        self.refresh_preview()
        self.update_star_panel()
        if filled:
            self.status.set(filled)
        if comp_number(mode) or mode == "check":
            self._vet_choice(mode, xy)

    def _chart_star_at(self, xy, reach: float = 8.0):
        best = None
        for x, y, star in self.chart_placed:
            d = math.hypot(x - xy[0], y - xy[1])
            if d < reach and (best is None or d < best[0]):
                best = (d, star)
        return best[1] if best else None

    def _chart_star_named(self, name: str):
        if not name:
            return None
        for _x, _y, star in self.chart_placed:
            if name in (star.get("auid"), star.get("label")):
                return star
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
            widgets.append(w)
            if mag_var is not None:
                w = ttk.Entry(stars, textvariable=mag_var, width=8)
                w.grid(row=r, column=2, sticky=W, padx=(6, 0), pady=(4, 0))
                widgets.append(w)
            w = ttk.Button(stars, text="✕", width=3, command=lambda k=role: self.clear_mark(k))
            w.grid(row=r, column=3, sticky=W, padx=(4, 0), pady=(4, 0))
            widgets.append(w)
            var = StringVar(value="")
            lab = ttk.Label(stars, textvariable=var, style="Hint.TLabel", wraplength=480, justify=LEFT)
            lab.grid(row=r + 1, column=0, columnspan=4, sticky=W, padx=(14, 0))
            widgets.append(lab)
            self.star_status[role] = var
            self.star_status_labels[role] = lab
            self.star_row_widgets[role] = widgets
            name_var.trace_add("write", lambda *_: self.update_star_panel())
        stars.columnconfigure(1, weight=1)
        self.field_check_text = StringVar(value="Variable-star check: Label chart to check the field against VSX and SIMBAD.")
        ttk.Label(stars, textvariable=self.field_check_text, style="Hint.TLabel", wraplength=480, justify=LEFT).grid(
            row=1 + 2 * len(rows), column=0, columnspan=4, sticky=W, pady=(8, 0))

        watch = self._group(panel, "Watch stars")
        watch.pack(fill=BOTH, expand=True, pady=(10, 0))
        ttk.Label(watch, text="Pick Watch and click a star, or Add a star… by name or RA/Dec. Watch stars are measured "
                              "every run, found again after Label chart on later nights, and have their own light "
                              "curves (Output page).",
                  style="Hint.TLabel", wraplength=480, justify=LEFT).pack(anchor=W)
        self.watch_box = Listbox(watch, height=6, font=("Segoe UI", 9), activestyle="none", exportselection=False)
        self.watch_box.pack(fill=BOTH, expand=True, pady=(4, 4))
        row = ttk.Frame(watch, style="Card.TFrame")
        row.pack(fill=X)
        ttk.Button(row, text="Add a star…", command=self.add_watch_dialog).pack(side=LEFT)
        ttk.Button(row, text="Remove selected", command=self._remove_selected_watch).pack(side=LEFT, padx=6)
        ttk.Button(row, text="Watch light curves…", command=self.show_watch).pack(side=RIGHT)

    def update_star_panel(self):
        """Catalog color, red-color warning, and variable-star check for the target, comps, and check star."""
        if not hasattr(self, "star_status"):
            return
        checked = bool(self.field_check_sources)
        # 2.2.5: show Comp 2 … Comp 10 only when they are in use.
        used = set(self._comp_roles_used())
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
                var.set("")
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
            if role != "target":
                if mags.get("B") is not None and mags.get("V") is not None:
                    bv = mags["B"] - mags["V"]
                    src = (star or {}).get("catalog", "") if star else ""
                    if bv > core.RED_LIMIT_BV:
                        parts.append(f"⚠ very red, B−V {bv:.2f}: red giants often vary, and the color is far from most targets")
                        style = "Warn.TLabel"
                    else:
                        parts.append(f"✓ B−V {bv:.2f}" + (f" ({src})" if src else ""))
                        style = "Good.TLabel"
                else:
                    parts.append("no catalog color (click it on a Tycho-2, APASS, or Gaia label)")
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
                    parts.append("variable check: Label chart first")
                else:
                    parts.append("variable check: mark it on the image to check it by position")
            var.set("   ".join(parts))
            lab.configure(style=style)
        self._refresh_watch_box()

    # ---- watch stars ------------------------------------------------------------------
    def add_watch_at(self, xy):
        star = self._chart_star_at(xy)
        if star is not None:
            name = str(star.get("auid") or star.get("label") or "")
        else:
            name = ""
        if not name:
            name = f"watch {len(self.watch_list) + 1} ({xy[0]:.0f},{xy[1]:.0f})"
        if any(w["name"] == name for w in self.watch_list):
            self.watch_xy[name] = xy
            self.status.set(f"{name} is already a watch star; its position on this frame was updated.")
            self.refresh_preview()
            self._refresh_watch_box()
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
                              "variable": core.variable_text(star)})
            self.watch_list.append(entry)
            self.watch_xy[name] = xy
            self.status.set(f"Watching {name}." + ("" if star is not None or "ra" in entry else
                            " It is not a labeled catalog star, so click it again on later nights."))
        self.refresh_preview()
        self._refresh_watch_box()
        return name

    def add_watch_by_name(self, name: str):
        """Watch a spare comp (from Comp health); its stored measurements become its light curve at once."""
        if any(w["name"] == name for w in self.watch_list):
            self.status.set(f"{name} is already a watch star.")
            return
        info = self.spare_catalog.get(name, {})
        self.watch_list.append({"name": name, "mag": info.get("mag"), "mags": dict(info.get("mags") or {}),
                                "catalog": info.get("catalog", ""), "ra": info.get("ra"), "dec": info.get("dec"),
                                "variable": info.get("variable", "")})
        star = self._chart_star_named(name)
        if star is not None:
            for x, y, st in self.chart_placed:
                if st is star:
                    self.watch_xy[name] = (x, y)
        self._refresh_watch_box()
        self.refresh_preview()
        self.status.set(f"Watching {name}. Its spare-comp measurements already in the series form its light curve.")

    def remove_watch(self, name: str):
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
        self.watch_box.delete(0, END)
        for w in self.watch_list:
            mag = f"  V {w['mag']:.2f}" if isinstance(w.get("mag"), (int, float)) else ""
            where = "on this frame" if w["name"] in self.watch_xy else "not found on this frame"
            self.watch_box.insert(END, f"{w['name']}{mag}  —  {where}")

    def _locate_watch_stars(self):
        """After Label chart: find each watch star on the new frame by catalog name, then by sky position."""
        found = 0
        for w in self.watch_list:
            hit = None
            for x, y, star in self.chart_placed:
                if w["name"] in (star.get("auid"), star.get("label")):
                    hit = (x, y)
                    break
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
                        " (label with the catalog they were picked from, or click the missing ones with Watch)") + ".")
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
        for x, y, star in self.chart_placed:
            names = [str(star.get(k) or "") for k in ("auid", "label")]
            if any(n and " ".join(n.lower().split()) == key for n in names):
                self.add_watch_at((x, y))
                return
        for x, y, v in self.variable_marks:
            if " ".join(str(v.get("name", "")).lower().split()) == key:
                self._add_watch_sky(v["name"], v["ra"], v["dec"], self._variable_hover(v).replace("\n", " "))
                return
        coords = core.parse_coordinates(text)
        if coords is not None:
            self._add_watch_sky(f"watch {coords[0]:.4f} {coords[1]:+.4f}", coords[0], coords[1], "")
            return
        self.status.set(f"Looking up {text} in SIMBAD and VSX…")

        def work():
            try:
                found = core.lookup_target(text)
                self.call_ui(self._add_watch_sky, text, found["ra_hours"] * 15.0, found["dec_deg"],
                             f"{found.get('source', 'SIMBAD')}" + (f" {found['var_type']}" if found.get("var_type") else ""))
            except Exception as exc:
                self.call_ui(self.status.set, f"Could not find {text}.")
                self.call_ui(messagebox.showerror, APP_TITLE, f"Could not find {text}: {exc}")

        self._blink_worker = threading.Thread(target=work, daemon=True)
        self._blink_worker.start()

    def _add_watch_sky(self, name: str, ra: float, dec: float, note: str):
        """Add a watch star at a sky position: on a labeled chart star if one is within 5\", else projected
        onto the image with the chart solution."""
        if any(w["name"] == name for w in self.watch_list):
            self.status.set(f"{name} is already a watch star.")
            return
        best = None
        for x, y, star in self.chart_placed:
            if star.get("ra") is None:
                continue
            d = core.sky_separation_arcsec(ra, dec, star["ra"], star["dec"])
            if d < 5.0 and (best is None or d < best[0]):
                best = (d, x, y, star)
        entry = {"name": name, "ra": ra, "dec": dec}
        hits = [v for _x, _y, v in self.variable_marks if core.sky_separation_arcsec(ra, dec, v["ra"], v["dec"]) < 6.0]
        if best is not None:
            star = best[3]
            entry.update({"mag": star.get("mag"), "mags": dict(star.get("mags") or {}), "catalog": star.get("catalog", "")})
            xy = (best[1], best[2])
        elif self.chart_solution and self.chart_placed:
            xy = self._project_to_frame(ra, dec)
        else:
            xy = None
        if xy is not None:
            for other, oxy in self.watch_xy.items():
                if math.hypot(oxy[0] - xy[0], oxy[1] - xy[1]) < 2.0:
                    self.status.set(f"That star is already watched as {other}.")
                    return
        if hits:
            entry["variable"] = core.variable_text({"variable": hits})
        elif note:
            entry["variable"] = note
        self.watch_list.append(entry)
        if xy is not None:
            self.watch_xy[name] = xy
            self.status.set(f"Watching {name} at pixel ({xy[0]:.0f}, {xy[1]:.0f}).")
        elif not self.chart_placed:
            self.status.set(f"Watching {name}. Label chart to find it on the image.")
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
            return f"{name} has no {star.get('band', 'V')} magnitude in {star.get('catalog', 'the catalog')}. Pick another star."
        mag = f"{star['mag']:.3f}"
        # AAVSO comps carry the chart ID; comps from other catalogs are reported with CHART=na.
        self.chart.set(star.get("chartid") or "na")
        target = (getattr(self, f"{mode}_name"), getattr(self, f"{mode}_mag"))
        target[0].set(name)
        target[1].set(mag)
        self.star_bands[mode] = {"name": name, "mags": dict(star.get("mags") or {})}
        if star.get("ra") is not None and star.get("dec") is not None:
            self.comp_coords[name] = (float(star["ra"]), float(star["dec"]))
        return f"{role_label(mode)}: {star.get('catalog', 'AAVSO')}  {name}  {star.get('band', 'V')} = {mag}"

    def _bands_for(self, role: str, name: str) -> dict:
        """Catalog magnitudes in every band for a comp, if it was picked from a chart."""
        entry = self.star_bands.get(role) or {}
        return dict(entry.get("mags") or {}) if entry.get("name") == name else {}

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
            raise ValueError(f"{name} needs a catalog V magnitude")
        return core.Star(name=name, x=xy[0], y=xy[1], catalog_mag=mag)

    def label_aavso(self, auto: bool = False):
        catalog = self.catalog.get()
        if self.preview is None or not self.preview_is_debayered:
            if not auto:
                messagebox.showinfo(APP_TITLE, "Calibrate first (Calibrate page).")
            return
        field_solve = self.target_xy is None or self.mode_key == "discovery"
        wcs = core.header_wcs(self.preview_header or {})
        superpixel = 1 if self.pattern.get() == core.MONO else 2
        try:
            wcs_factor = max(int(self.binning.get()), 1) * superpixel
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
                self._layout_input_for_mode()
                if auto:
                    self.status.set("Field not solved: these lights do not say where they point. Type the field "
                                    "centre on the Input page, then Solve field.")
                    return
                messagebox.showinfo(
                    APP_TITLE,
                    "Label chart needs to know roughly where the frame is: the target's RA/Dec on the Input page, or the "
                    "field center. These frames' FITS headers have no RA/DEC. Type the field center on the Input page "
                    "(from your planetarium program or the mount; a few arcminutes off is fine) and try again.")
                return
            query = (pointing[0] / 15.0, pointing[1])
            self.log(f"Label chart: field center from the FITS header, RA {pointing[0] / 15.0:.5f} h, Dec "
                     f"{pointing[1]:+.4f}°.")
        if not self.ra_hours.get().strip() or not self.dec_deg.get().strip():
            fill = query
            if wcs is not None and not field_solve and self.target_xy is not None:
                # A marked target with no RA/Dec typed: take its own position from the plate solution.
                sx, sy = core.image_to_wcs(self.target_xy[0], self.target_xy[1], wcs_factor)
                t_ra, t_dec = core.wcs_pixel_to_sky(wcs, sx, sy)
                fill = (float(t_ra) / 15.0, float(t_dec))
            self.ra_hours.set(f"{fill[0]:.6f}")
            self.dec_deg.set(f"{fill[1]:.5f}")
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
                self.log(f"Label chart: the Input page target ({self.star_id.get().strip() or 'RA/Dec'}) is not in these "
                         "lights, according to their plate solution; the chart is placed from the solution alone. "
                         "Update the target on the Input page.")
                field_solve = True
        # The reference point the chart solution is built around: the target when one is marked (its marker is the
        # anchor), else the catalog query's centre.
        if not field_solve and target_radec is not None:
            query = (target_radec[0] / 15.0, target_radec[1])
        try:
            ra_hours, dec = float(query[0]), float(query[1])
            binning = max(int(self.binning.get()), 1)
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
        sources = core.detect_sources(self.preview)
        target_xy = self.target_xy if not field_solve else (tx, ty)
        self._chart_diag = {"scale": scale, "camera_bin": camera_bin, "sources": len(sources), "target": target_xy,
                            "auto": auto, "ref": (ra_hours * 15.0, dec)}
        # An approximate scale (frames from another telescope) gets a wider search.
        slop = 0.15 if self.scale_approx else 0.06
        if not self._start_job(f"Fetching {catalog} stars ({fov:.0f}′ field, {band} band)…"):
            return
        vet_cache = {"field": getattr(self, "_field_var_cache", None)}

        def work():
            try:
                stars, chart_id = core.fetch_comparison_stars(catalog, cone[0], cone[1], fov, band)
                if not stars:
                    raise ValueError(f"{catalog} returned no stars for this field.")
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
                    solved = core.solve_field(sources, usable, ra_hours * 15.0, dec, scale, tolerance_px=tol)
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
                        fs = core.solve_field(sources, usable, ra_hours * 15.0, dec, scale, tolerance_px=tol)
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

    def _label_done(self, catalog, chart_id, stars, match_set, solved, placed, band, error, vet=None):
        self._end_job()
        diag = getattr(self, "_chart_diag", {})
        auto = diag.get("auto", False)
        if error is not None:
            self.status.set(f"{catalog} request failed.")
            if auto:
                self.log(f"Solve field: {catalog} request failed ({error}). Try Solve field again, or another catalog.")
                return
            messagebox.showerror(APP_TITLE, f"{catalog}: {error}")
            return
        n = len(match_set)
        hits = solved["hits"] if solved else 0
        needed = 2 if n <= 2 else (3 if n < 20 else 6)
        if hits < needed or (n <= 2 and hits < n):
            tx, ty = diag.get("target") or (float("nan"), float("nan"))
            hint = ""
            self.status.set(f"{catalog}: no lock ({hits} of {n} matched).")
            if auto:
                self.log(f"Solve field: {catalog} did not lock onto this frame ({hits} of {n} stars matched). Check the "
                         "focal length, pixel size, and binning on the Input page, or try another catalog, then Solve "
                         "field.")
                self._field_summary()
                return
            if catalog == "AAVSO sequence" and n < 6:
                hint = f"\n\nThe AAVSO chart has only {n} star(s) here. Try Catalog = Gaia DR3, which has many more."
            messagebox.showinfo(
                APP_TITLE,
                f"{catalog} did not lock onto this frame ({hits} of {n} stars matched, {needed} needed).\n\n"
                f"Target marker: ({tx:.0f}, {ty:.0f})   stars found in frame: {diag.get('sources', 0)}\n"
                f"Plate scale used: {diag.get('scale', 0):.3f}\"/px (camera bin {diag.get('camera_bin', 1)}, "
                f"app bin {self.binning.get()}, "
                + ("monochrome)" if self.pattern.get() == core.MONO else "superpixel x2)") + "\n\n"
                "Check that the Target marker is on your star, then RA/Dec, focal length, pixel size, and binning.\n"
                "You can always type the comp and check magnitudes in by hand." + hint,
            )
            return
        if diag.get("from_header"):
            self.log(f"Label chart: placed with the plate solution in the FITS header ({solved['hits']} of "
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
            self.log(f"Label chart: field solved from star patterns ({solved['hits']} of {solved.get('match_count', n)} "
                     "bright catalog stars matched); no target needed.")
        if diag.get("moved_from") is not None:
            old = diag["moved_from"]
            self.target_xy = tuple(diag["target"])
            self.log(f"Label chart: the target marker at ({old[0]:.0f}, {old[1]:.0f}) was not on the target; the chart locked "
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
        self._apply_field_check(vet, solved, diag)
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
        title = chart_id or catalog
        self.photo_ax.set_title(
            f"{title}  {hits}/{n} matched  rotation {solved['angle']:.1f}°{flip}  {len(self.chart_placed)} stars labeled"
        )
        self.photo_canvas.draw_idle()
        if n <= 2 and not auto:
            if not messagebox.askyesno(
                APP_TITLE,
                f"This {catalog} chart has only {n} stars, and both lined up. Two stars can line up by chance.\n\n"
                "Look at the yellow circles. Are they on real stars, in the pattern the chart shows?",
            ):
                self.chart_placed = []
                self.refresh_preview()
                return
        with_mag = sum(1 for _, _, star in self.chart_placed if star.get("mag") is not None)
        services = sorted({str(star.get("service", "")) for _, _, star in self.chart_placed if star.get("service")})
        counts = {b: sum(1 for _, _, star in self.chart_placed if b in (star.get("mags") or {})) for b in ("B", "V", "R")}
        self.log(f"Label chart: {catalog} via {', '.join(services) or 'unknown service'}: "
                 f"{len(self.chart_placed)} stars labeled; with B {counts['B']}, V {counts['V']}, R {counts['R']}.")
        if counts["B"] == 0 or counts["V"] == 0:
            self.log("  These labels carry no B magnitudes, so they cannot calibrate color (B-V). "
                     "For color, label with Gaia DR3 (ESA archive) or APASS DR9.")
        elif counts["R"] == 0:
            self.log("  B and V present: B-V can be calibrated. No R, so V-R cannot.")
        if self.mode_key == "discovery":
            self.status.set(f"Field solved: {len(self.chart_placed)} {catalog} stars labeled ({with_mag} with {band} "
                            "magnitudes). Watch or exclude stars if you like, then Scan field.")
        else:
            self.status.set(
                f"{title}: {len(self.chart_placed)} stars labeled ({with_mag} with {band} magnitudes). "
                "Pick Comparison or Check, then click a yellow-circled star to fill its ID and magnitude."
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
        """After Label chart: a comp, comp 2, or check star marked before the chart (so its ID and magnitude are still
        empty) takes them from the labeled star under it. Names already there are left alone (2.2.1)."""
        filled = []
        for role, xy, name_var in ([(r, getattr(self, f"{r}_xy"), getattr(self, f"{r}_name")) for r in COMP_ROLES]
                                   + [("check", self.check_xy, self.check_name)]):
            if xy is None or name_var.get().strip():
                continue
            if self._chart_star_at(xy) is None:
                continue
            text = self._fill_from_chart(role, xy)
            if text and "=" in text:
                filled.append(text)
        for text in filled:
            self.log("Label chart filled in " + text)

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
        picks = core.suggest_comps_near(t_inst, cands, max_dmag=1.0, count=4, target_bv=t_bv)
        self.suggested_comps = [(name, dm, where[name][0], where[name][1]) for name, dm in picks]
        if self.suggested_comps:
            self.log("Suggested comps (near the target in brightness" + (f" and color, target B−V {t_bv:.2f}"
                                                                         if t_bv is not None else "")
                     + "; green rings): "
                     + ", ".join(f"{name} ({dm:+.1f} mag)" for name, dm, _x, _y in self.suggested_comps)
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
                                      "not checked. Label chart again later.")
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
        comp_name = self.comp_name.get().strip()
        comp_mag = None
        try:
            comp_mag = float(self.comp_mag.get()) if self.comp_mag.get().strip() else None
        except ValueError:
            comp_mag = None
        comp_bands = self._bands_for("comp", comp_name)
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
                    mags = star["mags"]
                    cat_color["bv"][k] = mags["B"] - mags["V"]
                    if "R" in mags:
                        cat_color["vr"][k] = mags["V"] - mags["R"]
        saved_cal = core.load_color_calibration() if color else None
        # Catalog magnitudes for the scan stars that sit on a labeled (non-variable) chart star: the ensemble zero
        # point that puts the whole scan on the catalog's scale (2.2).
        cat_mag = np.full(n_xy, np.nan)
        cat_name = [""] * n_xy
        if self.chart_placed:
            tol = max(2.0, 0.5 * radius)
            for cx, cy, star in self.chart_placed:
                if star.get("mag") is None:
                    continue
                d = [math.hypot(px - cx, py - cy) for px, py in xy]
                k = int(np.argmin(d))
                if d[k] <= tol:
                    cat_name[k] = str(star.get("auid") or star.get("label") or "")
                    if not star.get("variable"):
                        cat_mag[k] = float(star["mag"])
        catalog_used = self.catalog.get()
        if not self._start_job(f"Scanning {len(xy)} stars in {len(lights)} frames…"):
            return
        self._progress_start(len(lights), "Field scan")
        self.log(f"Field scan: {len(xy)} stars, {len(lights)} frames, aperture {radius:.1f} px"
                 + (", with R and B" if color else "")
                 + (f"; {crowded} crowded star(s) left out" if crowded else "")
                 + (f"; {excluded} excluded by you" if excluded else ""))
        xy0 = np.asarray(xy, dtype=float)

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
                        reg = core.register_frame(ref_sources, src, img.shape)
                        if reg is None and last["sources"] is not None:
                            step = core.register_frame(last["sources"], src, img.shape)
                            if step is not None:
                                reg = core.compose_registration(last["reg"], step, img.shape)
                        if reg is None:
                            raise ValueError("could not align")
                        last["sources"], last["reg"] = src, reg
                        pos = core.move_points(xy0, reg, img.shape)
                        exp = core.header_exptime(header)
                        flux, _ = core.measure_many(img, pos, radius, sky_in, sky_out)
                        with np.errstate(divide="ignore", invalid="ignore"):
                            row = np.where(flux > 0, -2.5 * np.log10(flux / exp), np.nan)
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
                stats = core.field_variability(inst)
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
                if mag_limit is not None and offset != 0.0:
                    too_faint = {i for i in range(len(labels)) if math.isfinite(result["mag"][i])
                                 and result["mag"][i] > mag_limit}
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
                result["bv"] = result["vr"] = None
                result["color_fits"], result["color_method"] = {}, {}
                if color:
                    stacks = {c: np.vstack(chans[c]) for c in ("green", "red", "blue")}
                    # Leave clouded frames (field zero point more than ~20% faint) out of the color fit.
                    zp = np.asarray(stats["zero_point"], dtype=float)
                    frame_ok = (zp - np.median(zp)) < 0.24
                    ci = labels.index(role_label("comp")) if role_label("comp") in labels else None
                    comp_cat = {"bv": (comp_bands["B"] - comp_bands["V"]) if "B" in comp_bands and "V" in comp_bands else None,
                                "vr": (comp_bands["V"] - comp_bands["R"]) if "V" in comp_bands and "R" in comp_bands else None}
                    for key, _b1, _b2, c1, c2 in core.COLOR_INDICES:
                        inst, fit, calibrated = core.field_star_colors(stacks[c1], stacks[c2], cat_color[key], frame_ok)
                        old = (saved_cal or {}).get(key)
                        result["color_fits"][key] = fit
                        if calibrated is not None:
                            values, method = calibrated, "field"
                        elif ci is not None and comp_cat[key] is not None and old and not old.get("problem"):
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
                    else ", no sky positions (run Label chart for the VSX/SIMBAD check)"))
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
        self.discovery_empty = ttk.Label(
            self.discovery_host,
            text="No field scan yet.\n\nCalibrate: the field is solved straight away (every star gets a position, a catalog "
                 "magnitude, and a VSX/SIMBAD check). Then press Scan field on the Photometry page. The results appear here.",
            style="Hint.TLabel", justify=LEFT, font=("Segoe UI", 11))
        self.discovery_empty.pack(anchor=W, padx=16, pady=16)
        nav = ttk.Frame(card, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", pady=(4, 0))
        ttk.Button(nav, text="Back", command=lambda: self.show_step(STEP_PHOTO)).pack(side=RIGHT, padx=8, pady=4)
        return page

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
            missing = [role_label(r) for r in comp_roles if not getattr(self, f"{r}_mag").get().strip()]
            if missing:
                raise ValueError(f"{', '.join(missing)} need{'s' if len(missing) == 1 else ''} a catalog magnitude "
                                 "(click it on a Label chart circle, or type it)")
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
        if len(comps) > 1 and check is None:
            if not messagebox.askyesno(APP_TITLE, f"With {len(comps)} comps the AAVSO report is an ensemble, and AAVSO "
                                                  "requires a check star for ensemble reports. Continue without one?"):
                return

        mode = self.debayer_mode.get()
        pattern = self.pattern.get()
        masters = self.masters
        precalibrated = self.precalibrated
        measure_color = bool(self.measure_color.get())
        comp_bands = [self._bands_for(role, star.name) for role, star in zip(comp_roles, comps)]
        cat_bv = core.ensemble_color(comp_bands, "B", "V")
        cat_vr = core.ensemble_color(comp_bands, "V", "R")
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
                reasons.append("the comps' catalog colors are unknown (click them on their Label chart circles)")
            if not messagebox.askyesno(
                APP_TITLE,
                "'Also measure R and B' is ticked, but B-V cannot be calibrated on this run:\n  • "
                + "\n  • ".join(reasons)
                + "\n\nLabel chart with Gaia DR3 or APASS DR9 (after clicking the comps on Tycho-2 if they are "
                  "brighter than APASS goes) to fix this.\n\nRun anyway with uncalibrated color?",
            ):
                return

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
                    if moved > radius:
                        late["not found where the catalog puts it"] = late.get("not found where the catalog puts it", 0) + 1
                        continue
                    if not peak_ok(cx, cy):
                        late["near saturation"] = late.get("near saturation", 0) + 1
                        continue
                    spares.append(core.Star(name=name, x=cx, y=cy, catalog_mag=float(star["mag"])))
                    self.spare_catalog[name] = {"mag": float(star["mag"]), "mags": dict(star.get("mags") or {}),
                                                "catalog": star.get("catalog", ""), "ra": star.get("ra"),
                                                "dec": star.get("dec")}
                    taken.add(name)
                if len(spares) >= 3:
                    break
            if widen > 2.5:
                self.log(f"Spare comps: thin field, so the brightness window was widened to {faint_edge:.1f} mag "
                         f"({widen:g} mag fainter than the comps); {len(spares)} spare(s) found.")
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
        if self.observations and self.series_mode and self.series_mode != self.mode_key:
            if not messagebox.askyesno(APP_TITLE, f"The open series is a {MODES[self.series_mode]['name']} series and "
                                                  f"this is {MODES[self.mode_key]['name']} mode. Start a new series?"):
                return
        if not self._start_job(f"Photometry on {len(lights)} lights…"):
            return
        if watch:
            self.log(f"Watch stars measured every frame ({len(watch)}): " + ", ".join(w.name for w in watch))
        missing_watch = [w["name"] for w in self.watch_list if w["name"] not in self.watch_xy]
        if missing_watch:
            self.log("Watch stars not on this frame (Label chart, or click them with Watch): " + ", ".join(missing_watch))
        if measure_color:
            if cat_bv is None and cat_vr is None:
                self.log("Color: comp catalog colors unknown (pick comps from Gaia, APASS, or Tycho-2 with Label chart). "
                         "Only instrumental color differences will be reported.")
            else:
                self.log(f"Color: comp catalog B-V {f'{cat_bv:.3f}' if cat_bv is not None else 'n/a'}, "
                         f"V-R {f'{cat_vr:.3f}' if cat_vr is not None else 'n/a'} (ensemble mean).")
            if cal_stars:
                self.log(f"Color calibration: {len(cal_stars)} labeled field stars with catalog B and V "
                         f"({sum(1 for *_, st in cal_stars if 'R' in st['mags'])} with R), measured on about 40 frames.")
            elif self.chart_placed:
                self.log(f"Color calibration: only {n_cal_found} usable labeled stars with catalog B and V "
                         "(5 needed). Label chart with Gaia DR3 (B, V, R) or APASS DR9 (B, V) for a field fit.")
            if not cal_stars and saved_cal:
                camera = (self.preview_header or {}).get("INSTRUME", "")
                note = ""
                if saved_cal.get("camera") and camera and saved_cal["camera"] != camera:
                    note = f"  Careful: it was made with {saved_cal['camera']}, these frames are {camera}."
                self.log(f"Color: no field fit tonight; the color term saved {saved_cal.get('date', '')[:10]} "
                         f"({saved_cal.get('star', '')}) will be used with the comps' catalog colors.{note}")
        self.spare_marks = [(sp.x, sp.y, sp.name) for sp in spares]
        if spares:
            self.log(f"Spare comps measured every frame ({len(spares)}): "
                     + ", ".join(f"{sp.name} ({sp.catalog_mag:.2f})" for sp in spares))
        elif self.chart_placed:
            self.log("Spare comps: no labeled star near the comps' brightness passed the checks.")
        else:
            self.log("Spare comps: none (Label chart first, so spares can be picked from catalog stars).")
        # The stars were marked on the preview frame; every other frame is aligned to it.
        ref_sources = core.detect_sources(self.preview, max_sources=80)
        last_good = {"sources": None, "reg": None}
        if len(ref_sources) < 3:
            self.log("Warning: too few stars found on the reference frame to align frames. Positions will not follow drift.")
        self._prev_stars = (list(self.comps_used), self.check_used)
        self.comps_used = [comp.name for comp in comps]
        self.check_used = check.name if check is not None else ""
        self.comp_catalog_mags.update({comp.name: float(comp.catalog_mag) for comp in comps})
        self.log(f"Photometry on {len(lights)} lights, {len(self.rejected)} rejected, aperture {radius:.1f} px, {mode} channel")
        cal_xy = np.array([(x, y) for x, y, _ in cal_stars], dtype=float) if cal_stars else None
        cal_catalog = {
            "bv": np.array([st["mags"]["B"] - st["mags"]["V"] for *_, st in cal_stars], dtype=float),
            "vr": np.array([st["mags"]["V"] - st["mags"]["R"] if "R" in st["mags"] else np.nan for *_, st in cal_stars],
                           dtype=float),
        }
        cal_step = max(1, len(lights) // 40)
        cancel = self._cancel
        self._progress_start(len(lights), "Photometry")

        def work():
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
                        reg = core.register_frame(ref_sources, frame_sources, data.shape)
                        if reg is None and last_good["sources"] is not None:
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
                    f_target = core.move_star(target, reg, data.shape)
                    f_comps = [core.move_star(c, reg, data.shape) for c in comps]
                    f_check = core.move_star(check, reg, data.shape) if check is not None else None
                    obs = core.reduce_frame(
                        data, header, f_target, f_comps, f_check, radius, sky_in, sky_out,
                        lat, lon, ra_hours, dec_deg, float("inf"),
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
            color_info = None
            if measure_color and night_points:
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
                        inst, inst_err = core.robust_mean_err(np.vstack([smp[1] for smp in samples]), axis=0)
                        fits[key] = core.fit_color_term(inst, cal_catalog[key], inst_err=inst_err)
                        comp_off = np.array([smp[2] for smp in samples], dtype=float)
                        comp_off = comp_off[np.isfinite(comp_off)]
                        offsets[key] = float(np.median(comp_off)) if comp_off.size else None
                used = core.apply_color_calibration(night_points, fits, offsets, saved_cal,
                                                    {"bv": cat_bv, "vr": cat_vr})
                color_info = {"fits": fits, "used": used, "frames": n_frames, "saved": saved_cal}
            self.call_ui(self._photometry_done, night_points, mode, color_info)

        self._blink_worker = threading.Thread(target=work, daemon=True)
        self._blink_worker.start()

    def _mark_rotator_excursion(self, night_points):
        """2.2.7: a flip the alignment cannot see (the camera turned back to its first angle after the flip) still
        splits the night: frames after a rotator excursion become the "flip" segment, so each star's light curve is
        lined up across it like any flip. Rejected frames count, since the excursion is often only in them."""
        if not night_points or any(o.segment == "flip" for o in night_points):
            return
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

    def _photometry_done(self, night_points, mode, color_info=None, cancelled=False):
        self._end_job()
        if cancelled:
            self._progress_end("Cancelled; nothing was added.")
            self.log("Photometry cancelled. Nothing was added to the series.")
            return
        self._progress_end(f"{len(night_points)} points.")
        if not night_points:
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
            self._record_color_calibration(night, color_info)
        append = False
        same_mode = not self.series_mode or self.series_mode == self.mode_key
        if self.observations and same_mode:
            filters = {obs.filt for obs in self.observations}
            warning = ""
            if filters != {filt}:
                warning = f"\n\nCareful: the series is {', '.join(sorted(filters))} and this night is {filt}."
            prev_comps, prev_check = getattr(self, "_prev_stars", ([], ""))
            if prev_comps and (prev_comps != self.comps_used or prev_check != self.check_used):
                warning += (
                    "\n\nCareful: this night used different stars than the series.\n"
                    f"  Series: comps {', '.join(prev_comps)}; check {prev_check or 'none'}\n"
                    f"  This night: comps {', '.join(self.comps_used)}; check {self.check_used or 'none'}\n"
                    "Different comps put each night on its own zero point."
                )
            # 2.2.3: compare this night's reduction settings with every other night's in the series.
            others = {n: info for n, info in self.night_bin.items() if n != night}
            mine = self._night_setup()
            if "different stars than the series" in warning:
                mine = {k: v for k, v in mine.items() if k not in ("comps", "check")}   # already said above
            diffs = core.night_setup_differences(mine, others)
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
        if append:
            if any(o.night == night for o in self.observations):
                self.observations = [o for o in self.observations if o.night != night]
                self.log(f"Replaced the earlier points of {night} with this run.")
            self.observations = core.merge_nights(self.observations, night_points)
        else:
            replaced = bool(self.observations)
            self.observations = night_points
            kept = self.color_cal.get(night)
            self.color_cal = {night: kept} if kept else {}
            self.night_bin = {}
            self.legacy_mixed = set()
            self.active_comps = []
            self.reports_saved = {}
            if replaced and self.series_path:
                # Never overwrite the file of the series that was just set aside.
                self.log(f"New series started; {os.path.basename(self.series_path)} was left unchanged on disk.")
                self.series_path = ""
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
        self.night_bin[night] = self._night_setup()
        self._refresh_period()
        flagged = sum(1 for obs in night_points if obs.flag)
        clouds = sum(1 for obs in night_points if "cloud" in obs.flag.split())
        self.log(f"Photometry done: {len(night_points)} points, {flagged} flagged ({clouds} cloud).")
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

    def _night_setup(self) -> dict:
        """2.2.3: what a night's reduction used, stored per night in the series so later nights can be compared."""
        def num(var):
            try:
                return float(var.get())
            except Exception:
                return None
        return {"binning": int(self.binning.get() or 1), "method": "bayer", "app_version": APP_VERSION,
                "radius": num(self.radius), "sky_in": num(self.sky_in), "sky_out": num(self.sky_out),
                "comps": list(self.comps_used), "check": self.check_used or ""}

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
        return core.apply_cloud_flags(self.observations, self._cloud_fraction())

    def cancel_job(self):
        if not self.busy:
            self.status.set("Nothing is running.")
            return
        self._cancel.set()
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

    _META_STRINGS = (
        "observer", "star_id", "chart", "comp_name", "comp_mag", "check_name", "check_mag",
        "comp2_name", "comp2_mag", "ra_hours", "dec_deg", "notes", "lat", "lon", "pattern", "debayer_mode", "catalog",
        "time_axis", "bin_choice", "mark_periods",
    ) + tuple(f"{r}_{k}" for r in COMP_ROLES[2:] for k in ("name", "mag"))   # 2.2.5: comps 3-10
    _META_NUMBERS = ("min_period", "max_period", "radius", "sky_in", "sky_out", "focal_mm", "pixel_um", "sat_limit", "cloud_limit")

    def _meta(self) -> dict:
        meta = {key: getattr(self, key).get() for key in self._META_STRINGS}
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
        meta["period_bins"] = self.period_bins.get()
        meta["night_binning"] = self.night_bin
        meta["legacy_mixed_nights"] = sorted(self.legacy_mixed)
        meta["elevation"] = self.elevation.get()
        meta["reports_saved"] = self.reports_saved
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
            self.binning.set(int(meta["binning"]))
        self.comps_used = list(meta.get("comps_used") or [])
        if isinstance(meta.get("star_bands"), dict):
            self.star_bands = meta["star_bands"]
        if isinstance(meta.get("night_notes"), dict):
            self.night_notes = dict(meta["night_notes"])
        if "use_check_zp" in meta:
            self.use_check_zp.set(bool(meta["use_check_zp"]))
        self.check_used = meta.get("check_used") or ""
        self.color_cal = dict(meta.get("color_cal") or {})
        self.series_mode = meta.get("mode") if meta.get("mode") in MODES else "variables"
        if isinstance(meta.get("watch_list"), list):
            known = {w["name"] for w in self.watch_list}
            self.watch_list += [w for w in meta["watch_list"] if isinstance(w, dict) and w.get("name") not in known]
        self.spare_catalog = dict(meta.get("spare_catalog") or {})
        self.comp_catalog_mags = {str(k): float(v) for k, v in (meta.get("comp_catalog_mags") or {}).items()
                                  if v is not None}
        self.active_comps = list(meta.get("active_comps") or [])
        if "align_flip" in meta:
            self.align_flip.set(bool(meta["align_flip"]))
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
        if meta.get("elevation"):
            self.elevation.set(str(meta["elevation"]))
        if isinstance(meta.get("planet"), dict) and meta["planet"].get("period"):
            self.planet_info = dict(meta.get("planet_info") or {})
            self._planet_to_fields(meta["planet"])
        names = set(meta.get("rejected") or [])
        if names:
            restored = []
            for folder in (self.light_dir.get(), self.flat_dir.get()):
                for path in core.list_fits(folder):
                    if os.path.basename(path) in names:
                        restored.append(path)
            self.rejected.update(restored)

    def _write_series(self, path: str):
        core.save_series(path, self._meta(), self.observations)
        self.series_path = path

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
            self._write_series(path)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Could not save: {exc}")
            return
        self.status.set(f"Series saved. Next night, load this file before reducing: {path}")

    def load_series(self):
        kwargs = {"title": "Load observation series",
                  "filetypes": [("SHOBS-P series", "*.cvseries"), ("JSON", "*.json"), ("All", "*.*")]}
        start_dir = self.settings.get("series_dir", "")
        if start_dir and os.path.isdir(start_dir):
            kwargs["initialdir"] = start_dir
        path = filedialog.askopenfilename(**kwargs)
        if not path:
            return
        self._remember_series_dir(path)
        try:
            meta, observations = core.load_series(path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not read {os.path.basename(path)}:\n{exc}")
            return
        self._apply_meta(meta)
        if self.series_mode != self.mode_key and self.series_mode in MODES:
            self.set_mode(self.series_mode, initial=True)
            self.log(f"Switched to {MODES[self.series_mode]['name']} mode to match {os.path.basename(path)}.")
        self._fill_base(observations, meta)
        self.observations = observations
        if getattr(self, "transit_page", None) is not None and self.transit_page.report is not None:
            self.transit_page.close_report(quiet=True)   # 2.2.7: a loaded series replaces a loaded report
        self.legacy_mixed = self._legacy_nights(meta, observations)
        self.series_path = path
        self._refresh_period()
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

    def _page_output(self):
        page = ttk.Frame(self.pages)
        card = ttk.Frame(page, style="Card.TFrame", padding=12)
        card.pack(fill=BOTH, expand=True)
        ttk.Label(card, text="Output", font=("Segoe UI", 16, "bold"), style="Card.TLabel").pack(anchor=W)
        self._build_summary(card)
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill=X)
        ttk.Label(row, text="Period search (days)", style="Card.TLabel").pack(side=LEFT)
        ttk.Entry(row, textvariable=self.min_period, width=6).pack(side=LEFT, padx=4)
        ttk.Label(row, text="to", style="Card.TLabel").pack(side=LEFT)
        ttk.Entry(row, textvariable=self.max_period, width=6).pack(side=LEFT, padx=4)
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
            qc, text="Leave flagged points (sat, drift, cloud) out of the period and report",
            variable=self.skip_flagged, command=self.refit_period,
        ).pack(side=LEFT)
        ttk.Label(qc, text="Cloud flag: comp light more than", style="Card.TLabel").pack(side=LEFT, padx=(14, 4))
        ttk.Entry(qc, textvariable=self.cloud_limit, width=5).pack(side=LEFT)
        ttk.Label(qc, text="% below the night's median", style="Card.TLabel").pack(side=LEFT, padx=(4, 6))
        ttk.Button(qc, text="Apply", command=self.refit_period).pack(side=LEFT)

        tools = ttk.Frame(card, style="Card.TFrame")
        tools.pack(fill=X, pady=(4, 0))
        ttk.Checkbutton(
            tools, text="Check-star correction (fold and period)", variable=self.use_check_zp, command=self.refit_period,
        ).pack(side=LEFT)
        ttk.Checkbutton(
            tools, text="Line up flip only (keep nights)", variable=self.align_flip, command=self.refit_period,
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
        nav = ttk.Frame(card, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", before=host, pady=(4, 0))
        series_row = ttk.Frame(card, style="Card.TFrame")
        series_row.pack(fill=X, side="bottom", before=nav, pady=(6, 0))
        ttk.Label(series_row, text="Series", style="Hint.TLabel", width=8).pack(side=LEFT)
        ttk.Button(series_row, text="Save series", command=self.save_series).pack(side=LEFT)
        ttk.Button(series_row, text="Load series", command=self.load_series).pack(side=LEFT, padx=6)
        ttk.Button(series_row, text="Add series file…", command=self.add_series_file).pack(side=LEFT)
        ttk.Button(series_row, text="Remove night…", command=self.remove_night).pack(side=LEFT, padx=6)
        ttk.Button(series_row, text="Night notes…", command=self.edit_night_note).pack(side=LEFT)
        ttk.Button(series_row, text="Comp health…", command=self.show_comp_health).pack(side=LEFT, padx=6)
        ttk.Button(series_row, text="Use comps…", command=self.choose_comps).pack(side=LEFT)
        ttk.Button(series_row, text="Watch stars…", command=self.show_watch).pack(side=LEFT, padx=6)
        ttk.Label(nav, text="Results", style="Hint.TLabel", width=8).pack(side=LEFT)
        ttk.Button(nav, text="Save AAVSO report", style="Accent.TButton", command=self.save_aavso).pack(side=LEFT)
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

    def show_variables_view(self):
        """Transits mode: the light-curve details page (the Variables Output page) for the open series."""
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
            fits = [f for f in fits if f and not f.get("problem")]
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
            parts.append(text)
        if parts:
            tail = (" Untransformed colors are approximate: tick 'Also measure R and B' and Label chart with Gaia DR3 "
                    "or APASS DR9 to calibrate them." if untransformed else "")
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
                    + " (comp catalog colors unknown; pick comps with Label chart for real B-V).")
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
            self.log(f"  {names[key]}: {text}")
        self.color_cal[night] = {"fits": fits, "used": used}
        good = {k: f for k, f in fits.items() if f and not f.get("problem")}
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

    def _time_values(self, jd):
        """x values for the light-curve plots: JD, or matplotlib dates in UTC or local time."""
        choice = self.time_axis.get()
        if choice == "JD":
            return np.asarray(jd, dtype=float)
        from matplotlib.dates import date2num

        stamps = [core.jd_to_datetime_utc(j) for j in jd]
        if choice == "Local time":
            stamps = [t.astimezone() for t in stamps]
        # date2num works on naive datetimes; strip the zone after converting.
        return np.array(date2num([t.replace(tzinfo=None) for t in stamps]))

    def _format_time_axis(self, ax):
        choice = self.time_axis.get()
        if choice == "JD":
            ax.set_xlabel("Julian Date")
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
        if self.out_canvas is None:
            return
        for ax in (self.out_ax1, self.out_ax2, self.out_ax3, self.out_ax4):
            ax.clear()
        self._hover_sets = []
        self._hover_annots = {}
        self._hover_key = None
        if not self.observations:
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
        if self.norm_segments.get():
            mag = np.array(core.normalize_segments(obs_list))
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
        for i, night in enumerate(nights):
            mask = np.array([obs.night == night for obs in obs_list])
            keep = mask & ~excluded
            ax.errorbar(
                tx[keep], mag[keep], yerr=err[keep], fmt="o", ms=4, alpha=alpha_for(night, raw_alpha),
                color=colors[i % len(colors)], ecolor="#b0b0b0", lw=0.8,
                label=(self.night_label(night, short=True) if night else "night") + (" *" if self.night_notes.get(night) else ""),
            )
            drop = mask & excluded
            if drop.any():
                ax.plot(tx[drop], mag[drop], "x", ms=6, color="#888888", alpha=alpha_for(night, 1.0))
        ax.invert_yaxis()
        self._format_time_axis(ax)
        ax.set_ylabel(f"{filt} magnitude" + (" (segments lined up)" if self.norm_segments.get()
                                             else " (flip lined up)" if self.align_flip.get() and flip_steps else ""))
        labels_raw = [self._point_label(o, float(m)) for o, m in zip(obs_list, mag)]
        self._hover_sets.append((ax, tx, mag, labels_raw, 0.0))
        flip_mask = np.array([o.segment == "flip" for o in obs_list]) & ~excluded
        if flip_mask.any():
            ax.plot(tx[flip_mask], mag[flip_mask], "o", ms=6, mfc="none", mec="#333333", mew=0.6, label="after flip")
        ax.set_title((self.star_id.get() or "Target") + ("   (x = flagged, left out)" if excluded.any() else ""))
        ax.grid(True, alpha=0.3)
        bin_text = ""
        if interval is not False:
            kept = [k for k in range(len(obs_list)) if not excluded[k]]
            bins = core.bin_points([obs_list[k] for k in kept], [float(mag[k]) for k in kept], interval,
                                   self._scint_setup())
            if bins:
                bx = self._time_values(np.array([b["jd"] for b in bins]))
                ax.errorbar(bx, [b["value"] for b in bins], yerr=[b["err"] for b in bins], fmt="D", ms=6,
                            color="#111111", ecolor="#111111", elinewidth=1.4, capsize=3, label=f"binned ({self.bin_choice.get()})")
                self._hover_sets.append((ax, bx, np.array([b["value"] for b in bins]),
                                         [self._bin_label(b) for b in bins], 4.0))
                errs = np.array([b["err"] for b in bins])
                bin_text = (f"  Binned ({self.bin_choice.get()}): {len(bins)} points, typical error "
                            f"{np.median(errs):.4f} mag, spread of binned points {np.std([b['value'] for b in bins]):.4f} mag.")
        if len(nights) > 1 or interval is not False:
            ax.legend(fontsize=8, frameon=False)

        usable = [o for o, ex in zip(obs_list, excluded) if not ex]
        ax = self.out_ax2
        cautions = []
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
                ax.plot(phase[mask], corrected[mask], "o", ms=4, color=colour, alpha=a_main)
                ax.plot(phase[mask] + 1, corrected[mask], "o", ms=4, color=colour, alpha=0.35 * a_main)
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
            ax.set_title(f"Folded  P = {p:.5f} d")
            ax.grid(True, alpha=0.3)
            alias = 1.0 / abs(1.0 / p - 1.0) if p > 0 and abs(1.0 / p - 1.0) > 1e-6 else float("nan")
            fap = self.period.get("fap", float("nan"))
            baseline = self.period.get("baseline_days", float("nan"))
            period_lines = [
                f"Best    {p:.5f} d ({p * 24:.2f} h)" + ("   (one short night: not reliable)" if too_short else ""),
                f"Power   {self.period['power']:.2f}" + (f"   FAP {fap:.1e}" if math.isfinite(fap) else ""),
                f"Basis   {getattr(self, '_period_note', '') or 'every point'}",
                f"Alias   {alias:.5f} d (one-day)",
                f"Epoch   JD {epoch:.5f}",
                "Fold    check-star zero point" if self.use_check_zp.get() else "Fold    check-star correction off",
            ]
            if self.catalog_period:
                period_lines.append(f"VSX     {self.catalog_period:.5f} d")
            if self.period.get("alias_text"):
                period_lines[0] += "   ⚠ near a day alias"
                cautions.append(self.period["alias_text"])
            carried = self.period.get("carried_by") or []
            if carried:
                period_lines.append("Check   ⚠ depends on " + ", ".join(carried) + " alone")
                cautions.append(f"Leave {', '.join(carried)} out and this period disappears: one night carries it "
                                "(often an offset between nights, not the star). Check that night before trusting it.")
            edge_text = core.period_edge_text(self.period)
            if edge_text:
                period_lines[0] += "   ⚠ at the edge"
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
        color_lines, color_cautions = self._color_lines(usable)
        cautions += color_cautions
        check_vals_all = [o.check_std for o, ex in zip(obs_list, excluded)
                          if not ex and o.check_std is not None and math.isfinite(o.check_std)]
        comps = self.active_comps or self.comps_used
        span = (max(jd) - min(jd)) if len(jd) else 0.0
        series_lines = [
            f"Points  {len(obs_list)} ({len(usable)} used)",
            f"Nights  {len(nights)}   span {span:.1f} d",
            "Comps   " + (", ".join(comps) if comps else "—"),
            f"Check   {self.check_used or '—'}" + (f"   σ {np.std(check_vals_all):.3f}" if len(check_vals_all) > 1 else ""),
            f"Filter  {filt}   bin {self.bin_choice.get()}",
        ]
        rows = self._night_rows(obs_list, mag, excluded, nights, flip_steps)
        self._fill_summary(series_lines, period_lines, color_lines, rows, cautions)

        ax = self.out_ax3
        check_vals = np.array([obs.check_std if obs.check_std is not None else np.nan for obs in obs_list])
        if np.isfinite(check_vals).any():
            for i, night in enumerate(nights):
                mask = np.array([obs.night == night for obs in obs_list]) & np.isfinite(check_vals) & ~excluded
                ax.plot(tx[mask], check_vals[mask], "o", ms=3, color=colors[i % len(colors)], alpha=alpha_for(night, 1.0))
            good = check_vals[np.isfinite(check_vals) & ~excluded]
            sel = np.isfinite(check_vals) & ~excluded
            self._hover_sets.append((ax, tx[sel], check_vals[sel],
                                     [f"check {v:.4f}\n{self._time_text(o.jd)}  {o.night}\n{os.path.basename(o.path)}"
                                      for o, v in zip([obs_list[k] for k in np.where(sel)[0]], check_vals[sel])], 0.0))
            ax.invert_yaxis()
            spread = f"  σ = {np.std(good):.3f}" if good.size > 1 else ""
            catalog = ""
            try:
                if self.check_mag.get().strip():
                    catalog = f"  catalog {float(self.check_mag.get()):.3f}"
            except ValueError:
                pass
            ax.set_title(f"Check star{spread}{catalog}")
            ax.set_ylabel("Reduced mag")
        else:
            ax.set_title("No check star")
        self._format_time_axis(ax)
        ax.grid(True, alpha=0.3)

        ax = self.out_ax4
        if self.period:
            period_axis = 1.0 / self.period["freqs"]
            ax.plot(period_axis, self.period["power_spectrum"], color="#c0c6cc" if too_short else "#0067c0", lw=0.8)
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
        self.out_canvas.draw_idle()

    # ---- Output summary (boxes + per-night table) ------------------------------------
    SUMMARY_COLUMNS = (("night_text", "Night", 120), ("night", "JD", 90), ("pts", "Pts", 60), ("used", "used", 60),
                       ("mean", "Mean mag ± err", 145), ("tsig", "Target σ", 85), ("csig", "Check σ", 85),
                       ("flip", "Flip step", 85), ("bv", "B−V", 70), ("flags", "Flags", 170), ("note", "Note", 260))

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
        self.spread_text = StringVar(value="")
        ttk.Label(holder, textvariable=self.spread_text, style="Card.TLabel").pack(anchor=W, pady=(4, 0))
        self.caution_text = StringVar(value="")
        ttk.Label(holder, textvariable=self.caution_text, style="Warn.TLabel", wraplength=1500, justify=LEFT).pack(anchor=W)

    def _on_night_click(self, event):
        """Click a night to highlight it on the plots; click it again to show every night."""
        row = self.night_table.identify_row(event.y)
        night = self._night_by_item.get(row) if row else None
        if night is None:
            return
        self.highlight_night = None if self.highlight_night == night else night
        if self.highlight_night is None:
            self.night_table.selection_remove(self.night_table.selection())
        self.draw_output()

    def _night_rows(self, obs_list, mag, excluded, nights, flip_steps) -> list[dict]:
        from collections import Counter

        rows = []
        for night in nights:
            idx = [k for k, o in enumerate(obs_list) if o.night == night]
            used = [k for k in idx if not excluded[k]]
            first = min(obs_list[k].jd for k in idx)
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
            bv = [obs_list[k].bv for k in used if math.isfinite(obs_list[k].bv)]
            flags = Counter(w for k in idx for w in obs_list[k].flag.split())
            note = self.night_notes.get(night, "")
            rows.append({
                "night": night, "night_text": self.night_label(night, short=True), "date": date,
                "pts": len(idx), "used": len(used), "mean": mean,
                "value": bins[0]["value"] if bins else float("nan"), "err": bins[0]["err"] if bins else float("nan"),
                "tsig": f"{tsig:.3f}" if math.isfinite(tsig) else "—",
                "csig": f"{csig:.3f}" if math.isfinite(csig) else "—",
                "flip": f"{flip_steps[night]['offset']:+.3f}" if night in flip_steps else "—",
                "bv": f"{float(np.median(bv)):.3f}" if bv else "—",
                "flags": ", ".join(f"{n} {w}" for w, n in flags.most_common()) or "—",
                "note": ("* " + note) if note else "",
            })
        return rows

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
        means = [r["value"] for r in rows if math.isfinite(r["value"])]
        errs = [r["err"] for r in rows if math.isfinite(r["err"])]
        if len(means) > 1:
            self.spread_text.set(f"Spread of nightly means {np.std(means):.4f} mag  (typical error {np.median(errs):.4f})"
                                 + ("     Click a night to highlight it; click it again to show all." if len(rows) > 1 else ""))
        else:
            self.spread_text.set("")
        self.caution_text.set("   ".join("⚠ " + c for c in cautions))

    def _color_lines(self, points):
        """Lines for the Color box, plus cautions."""
        def stat(values):
            v = np.array([x for x in values if math.isfinite(x)])
            if v.size == 0:
                return None
            return float(np.median(v)), float(1.4826 * np.median(np.abs(v - np.median(v))) / max(1.0, math.sqrt(v.size)))

        lines, cautions = [], []
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
            fits = [f for f in fits if f and not f.get("problem")]
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
                cautions.append(f"{label} is untransformed (approximate); label with Gaia DR3 or APASS DR9 to calibrate.")
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
            if set(o.flag.split()) & {"cloud", "drift", "noflux", "nocomp"}:
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

    def apply_comp_selection(self, selection: list[str]) -> bool:
        """Re-derive every magnitude from the chosen comps. Returns True when applied."""
        catalog = self._comp_catalog()
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
        result = core.recompute_with_comps(self.observations, selection, self.comps_used, catalog, aliases)
        self.active_comps = [] if set(selection) == set(self.comps_used) else list(selection)
        self._refresh_period()
        self.draw_output()
        used = ", ".join(selection)
        self.log(f"Comps in use: {used}. {result['done']} points re-derived"
                 + (f", {result['missing']} flagged nocomp." if result["missing"] else "."))
        saved = ""
        if self.series_path:
            try:
                self._write_series(self.series_path)
                saved = f" Saved to {os.path.basename(self.series_path)}."
            except OSError as exc:
                messagebox.showwarning(APP_TITLE, f"Could not save the series: {exc}")
        self.status.set(f"Comps in use: {used}.{saved}")
        return True

    def show_comp_health(self):
        if not self.observations:
            messagebox.showinfo(APP_TITLE, "There is no series loaded.")
            return
        names = list(self.comps_used) or ["comp"]
        result = core.comp_health([o for o in self.observations if not (set(o.flag.split()) - {"nocomp"})]
                                  or self.observations, names, self.check_used, self._spares_in_series())
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
        CompHealthWindow(self, result)

    def _star_positions(self, result) -> dict:
        """Median pixel position of each star in the health check, and its median distance from the target
        (the distance does not change with framing, rotation, or the meridian flip)."""
        names = list(self.comps_used)
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
                    p = (pos.get("s") or {}).get(lab)
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

    def refit_period(self):
        if not self.observations:
            return
        if len(self._usable()) < 8:
            messagebox.showinfo(APP_TITLE, "Need at least 8 usable points.")
        self._refresh_period()
        self.draw_output()

    def _report_text(self, observations: list | None = None) -> str:
        # Name the stars the reduction actually used, not whatever is in the form now.
        names = self.active_comps or self.comps_used or [self.comp_name.get().strip() or "comp"]
        comps = [core.Star(name, 0, 0) for name in names]
        check_name = self.check_used or self.check_name.get().strip() or "check"
        check = core.Star(check_name, 0, 0) if any(o.kmag is not None for o in self.observations) else None
        return core.aavso_report(
            self._usable() if observations is None else observations,
            self.observer.get(),
            self.star_id.get() or "target",
            comps,
            check,
            self.chart.get(),
            software=f"{APP_TITLE} {APP_VERSION}",
            notes=self.notes.get(),
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
        if good:
            if messagebox.askyesno(APP_TITLE, f"The Star ID is \u201c{self.star_id.get().strip()}\u201d. AAVSO spells it "
                                              f"\u201c{good}\u201d, and WebObs may not match the other spelling to the "
                                              f"star.\n\nUse \u201c{good}\u201d in the report?"):
                self.star_id.set(good)
        if len({o.filt for o in self._usable()}) > 1:
            problems.append("the series mixes filters")
        # 2.2.5: two or more comps make an ensemble report, which AAVSO accepts only with a check star.
        if len(self.active_comps or self.comps_used or []) > 1 and not any(o.kmag is not None for o in self._usable()):
            problems.append("ensemble report (more than one comp) without a check star; AAVSO requires one")
        if problems:
            if not messagebox.askyesno(APP_TITLE, "Before you upload:\n  • " + "\n  • ".join(problems) + "\n\nSave the report anyway?"):
                return
        scope = self._save_scope("AAVSO report")
        if scope is None:
            return
        points = [o for o in self._usable() if scope == "series" or o.night == self.highlight_night]
        if not points:
            messagebox.showinfo(APP_TITLE, "No usable points to report for that choice.")
            return
        repeat = self._already_reported(points, scope)
        if repeat and not messagebox.askyesno(APP_TITLE, repeat + "\n\nSave this report anyway?"):
            return
        self._save_one_night = scope == "night"
        try:
            path = self.output_path(
                self._family_names()[0], title="Save AAVSO extended report", defaultextension=".txt",
                filetypes=[("Text", "*.txt"), ("All", "*.*")], family=True)
        finally:
            self._save_one_night = False
        if not path:
            return
        with core.AsciiWriter(path) as fh:
            fh.write(self._report_text(points))
        self._note_report(points, path, scope)
        self.saved_notice(path, "Upload with the AAVSO WebObs file tool after you check the observer code, chart, and "
                                "comparison magnitude.")

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

    def _already_reported(self, points: list, scope: str) -> str:
        """A warning when nights in this report were already saved in an AAVSO report of the other scope
        (a night's own report, then the whole series, or the other way round): uploading both duplicates them."""
        lines = []
        for night in sorted({o.night for o in points}):
            for entry in self.reports_saved.get(night, []):
                if entry.get("scope") != scope:
                    kind = "its own report" if entry.get("scope") == "night" else "a whole-series report"
                    lines.append(f"  • {self._night_label(night)}: already saved in {kind}, "
                                 f"{entry.get('file', '?')} ({entry.get('date', '?')})")
                    break
        if not lines:
            return ""
        return ("These nights are already in another AAVSO report:\n" + "\n".join(lines) +
                "\n\nIf that report was uploaded, uploading this one too puts the same observations into the AAVSO "
                "database twice. Upload only one of them for each night.")

    def _note_report(self, points: list, path: str, scope: str):
        stamp = time.strftime("%Y-%m-%d %H:%M")
        for night in sorted({o.night for o in points}):
            self.reports_saved.setdefault(night, []).append(
                {"file": os.path.basename(path), "date": stamp, "scope": scope})
        if self.series_path:
            try:
                self._write_series(self.series_path)
            except OSError:
                pass

    def save_csv(self):
        if not self.observations:
            messagebox.showinfo(APP_TITLE, "Run photometry first.")
            return
        scope = self._save_scope("light-curve CSV")
        if scope is None:
            return
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
        buf = io.BytesIO()
        self.out_fig.savefig(buf, dpi=140, format="png")
        if not snap:
            with open(path, "wb") as fh:
                fh.write(buf.getvalue())
            return
        buf.seek(0)
        import matplotlib.image as mpimg
        plot = mpimg.imread(buf)
        ph, pw = plot.shape[0], plot.shape[1]
        dpi = 140
        width_in = pw / dpi
        mono = {"family": "monospace", "size": 9}
        box_lines = max(len(snap["series"]), len(snap["period"]), len(snap["color"]) or 1)
        rows = snap["rows"]
        cols = [(c, label) for c, label, _w in self.SUMMARY_COLUMNS if c != "note" or any(r.get("note") for r in rows)]
        line_in = 0.17
        box_h = (box_lines + 1.6) * line_in
        table_h = (len(rows) + 1.5) * line_in
        extra = (1 if snap.get("cautions") else 0) * line_in * 1.5 + 0.55
        head_in = box_h + table_h + extra
        fig = Figure(figsize=(width_in, ph / dpi + head_in), dpi=dpi, facecolor="white")
        FigureCanvasAgg(fig)
        total = ph / dpi + head_in
        title = f"{self.star_id.get().strip() or 'Target'}   {APP_TITLE} {APP_VERSION}"
        fig.text(0.01, 1 - 0.25 / total, title, fontsize=11, weight="bold", va="top")
        top = 1 - 0.5 / total
        x = 0.01
        for key, label in (("series", "Series"), ("period", "Period"), ("color", "Color")):
            lines = snap[key] or ["—"]
            fig.text(x, top, label, fontsize=9, weight="bold", va="top")
            fig.text(x, top - line_in * 1.1 / total, "\n".join(lines), va="top", fontdict=mono,
                     bbox={"boxstyle": "square,pad=0.4", "facecolor": "#f7f7f7", "edgecolor": "#bbbbbb"})
            longest = max(len(t) for t in lines)
            x += min(0.45, (longest * 0.075 + 0.5) / width_in)
        y = top - box_h / total
        header = "  ".join(f"{label:<{self._col_chars(c, rows, label)}}" for c, label in cols)
        body = ["  ".join(f"{str(r.get(c, '')):<{self._col_chars(c, rows, label)}}" for c, label in cols) for r in rows]
        fig.text(0.01, y, header, va="top", fontdict={**mono, "weight": "bold"})
        fig.text(0.01, y - line_in * 1.2 / total, "\n".join(body), va="top", fontdict=mono)
        y -= table_h / total
        if self.spread_text.get():
            fig.text(0.01, y + 0.25 * line_in / total, self.spread_text.get().split("     Click")[0], va="top", fontsize=8.5)
        if snap.get("cautions"):
            fig.text(0.01, y - line_in / total, "   ".join("! " + c for c in snap["cautions"])[:400], va="top",
                     fontsize=8, color="#a32020")
        ax = fig.add_axes([0, 0, 1, (ph / dpi) / total])
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
        self.observer.set("TST")
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
        ttk.Label(top, text="Comparison star health", font=("Segoe UI", 15, "bold"), style="Card.TLabel").pack(anchor=W)
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
        cols = ("star", "role", "spread", "scatter", "flip", "nights", "pos", "dist", "verdict")
        table = ttk.Treeview(table_box, columns=cols, show="headings", height=min(max(len(rows), 3), 12))
        hbar = ttk.Scrollbar(table_box, orient="horizontal", command=table.xview)
        table.configure(xscrollcommand=hbar.set)
        hbar.pack(side="bottom", fill=X)
        for col, label, width, stretch in (("star", "Star", 230, True), ("role", "Role", 60, False),
                                           ("spread", "Night offset", 100, False),
                                           ("scatter", "Scatter in night", 120, False),
                                           ("flip", "Flip step", 80, False),
                                           ("nights", "Nights", 60, False), ("pos", "Pixel (x, y)", 100, False),
                                           ("dist", "From target", 100, False), ("verdict", "Verdict", 260, True)):
            table.heading(col, text=label, anchor=W)
            cw = col_width(table, label, width)
            table.column(col, width=cw, minwidth=cw, anchor=W, stretch=stretch)
        for row in rows:
            sp, sc = row["night_spread"], row["scatter"]
            st = row.get("flip_step", float("nan"))
            pos = positions.get(row["label"])
            table.insert("", END, values=(row["label"].replace(" (check)", ""), row.get("kind", ""),
                                          f"{sp:.4f}" if math.isfinite(sp) else "—",
                                          f"{sc:.4f}" if math.isfinite(sc) else "—",
                                          f"{st:+.3f}" if isinstance(st, float) and math.isfinite(st) else "—",
                                          row["nights"],
                                          f"({pos[0]:.0f}, {pos[1]:.0f})" if pos else "—",
                                          f"{pos[2]:.0f}" if pos and math.isfinite(pos[2]) else "—",
                                          row["verdict"]))
        table.pack(fill=X, side="top")
        self.table = table
        self.table_rows = {iid: row for iid, row in zip(table.get_children(), rows)}
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
        fig = Figure(figsize=(10, 4.2), dpi=100, facecolor="#ffffff")
        ax = fig.add_subplot(111)
        kinds = result.get("kinds") or ["comp"] * len(result["labels"])
        if one_night and result.get("resid") is not None and len(result.get("jd", [])):
            self._draw_one_night(ax, result, kinds)
        else:
            xs = np.arange(len(nights))
            for k, lab in enumerate(result["labels"]):
                vals = [result["nightly"][lab].get(n, np.nan) for n in nights]
                finite = [v for v in vals if math.isfinite(v)]
                ref = float(np.median(finite)) if finite else 0.0
                spare = kinds[k] == "spare"
                ax.plot(xs, [v - ref for v in vals], "o--" if spare else "o-", lw=0.9 if spare else 1.6,
                        ms=4 if spare else 6, alpha=0.7 if spare else 1.0, label=lab + (" (spare)" if spare else ""))
            ax.axhline(0, color="#888888", lw=0.8)
            ax.set_xticks(xs)
            ax.set_xticklabels([app.night_label(n, short=True) if hasattr(app, "night_label") else n for n in nights],
                               rotation=20, fontsize=8)
            ax.invert_yaxis()
            ax.set_ylabel("Night median minus its own median (mag)")
            ax.set_title("Night-to-night behaviour of each star (should be flat)")
            ax.grid(True, alpha=0.3)
            ax.legend(fontsize=8, frameon=False, ncol=2 if len(result["labels"]) > 6 else 1)
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=top)
        canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        canvas.draw_idle()
        self.fig = fig
        fit_to_content(win)

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
            ax.plot(bx, by, "o--" if spare else "o-", color=line.get_color(), ms=4, lw=1.0 if spare else 1.6,
                    label=lab + (" (spare)" if spare else "") + (f"  (+{k * step:.2f})" if k else ""))
        flips = np.asarray(result.get("row_flip") or [], dtype=bool)
        if flips.any() and (~flips).any():
            t_flip = 0.5 * (float(np.max(hours[~flips])) + float(np.min(hours[flips])))
            ax.axvline(t_flip, color="#7a5c99", ls="--", lw=1.0, label="meridian flip")
        ax.invert_yaxis()
        ax.set_xlabel(f"Hours from JD {t0:.1f}")
        ax.set_ylabel("Star minus the others (mag, offset for clarity)")
        ax.set_title("Within-night behaviour of each star (one night: should be flat and not jump at the flip)")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8, frameon=False, ncol=2 if len(result["labels"]) > 6 else 1)

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
        ttk.Label(inner, text="Comparison stars in use", font=("Segoe UI", 15, "bold"), style="Card.TLabel").pack(anchor=W)
        ttk.Label(
            inner,
            text="Tick the stars to average as the comparison. Every point is re-derived from the measurements already "
                 "stored, so nothing is reduced again, and you can switch back at any time. Spares were measured on the "
                 "nights reduced in 1.8 or later. The check star cannot be a comp. Colors (B-V) stay tied to the comps "
                 "the nights were reduced with.",
            style="Hint.TLabel", wraplength=950, justify=LEFT,
        ).pack(anchor=W, pady=(2, 8))
        catalog = app._comp_catalog()
        nights = sorted({o.night for o in app.observations})
        health = core.comp_health(app.observations, list(app.comps_used), app.check_used, app._spares_in_series()) or {}
        verdicts = {row["label"]: row for row in health.get("summary", [])}
        current = set(app.active_comps or app.comps_used)
        grid = ttk.Frame(inner, style="Card.TFrame")
        grid.pack(fill=X)
        for c, head in enumerate(("Use", "Star", "Role", "Catalog mag", "Nights measured", "Night offset",
                                  "Scatter in night", "Comp health")):
            ttk.Label(grid, text=head, style="Card.TLabel", font=("Segoe UI", 10, "bold")).grid(
                row=0, column=c, sticky=W, padx=6, pady=(0, 4))
        self.vars = {}
        aliases = app._star_aliases()
        names, listed = [], set()
        for n, role in [(n, "comp") for n in app.comps_used] + [(n, "spare") for n in app._spares_in_series()]:
            if n in listed:
                continue
            # 2.2.3: one star under two catalog names is one row ("TYC … = APASS …"), measured on the nights of both.
            listed.add(n)
            listed.update(aliases.get(n, ()))
            names.append((n, role))
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
            for c, text in enumerate((shown, role, f"{mag:.3f}" if mag is not None else "—",
                                      f"{len(measured)} of {len(nights)}",
                                      f"{sp:.4f}" if isinstance(sp, float) and math.isfinite(sp) else "—",
                                      f"{sc:.4f}" if isinstance(sc, float) and math.isfinite(sc) else "—",
                                      row.get("verdict", "")), 1):
                colour = "#b00020" if c == 7 and text.startswith("SUSPECT") else "#1c2b33"
                ttk.Label(grid, text=text, style="Card.TLabel", foreground=colour).grid(row=r, column=c, sticky=W, padx=6, pady=2)
        if len(names) == len(app.comps_used):
            ttk.Label(inner, text="No spare comps in this series yet. Nights reduced in 1.8 or later after Label chart "
                                  "carry spares.", style="Hint.TLabel").pack(anchor=W, pady=(8, 0))
        elif len(names) < 3:
            ttk.Label(inner, text="Only two stars to choose from. With three or more, a noisy or drifting star stands out "
                                  "and the ensemble averages down the noise; 2.2.2 measures extra spares when the field "
                                  "is thin, on nights reduced again.", style="Hint.TLabel", wraplength=950,
                      justify=LEFT).pack(anchor=W, pady=(8, 0))
        body.fit()
        fit_to_content(win)

    def apply(self):
        selection = [name for name, var in self.vars.items() if var.get()]
        if not selection:
            messagebox.showinfo(APP_TITLE, "Tick at least one comparison star.", parent=self.win)
            return
        if self.app.apply_comp_selection(selection):
            self.win.destroy()

    def reset(self):
        if self.app.apply_comp_selection(list(self.app.comps_used)):
            self.win.destroy()


class SuggestWindow:
    """Aperture and sky ring sizes from the measured star size, with an optional test on real frames."""

    def __init__(self, app, fwhm, sizes, prop, notes):
        self.app = app
        self.fwhm = fwhm
        self.prop = dict(prop)
        win = Toplevel(app)
        self.win = win
        win.title(f"{APP_TITLE} — suggest apertures")
        win.after_idle(lambda: fit_to_content(win))
        top = ttk.Frame(win, style="Card.TFrame", padding=12)
        top.pack(fill=BOTH, expand=True)
        ttk.Label(top, text="Suggested apertures", font=("Segoe UI", 15, "bold"), style="Card.TLabel").pack(anchor=W)
        measured = ", ".join(f"{role} {f:.1f}" for role, f, _p in sizes)
        ttk.Label(top, text=f"Star size (FWHM) on this frame: {fwhm:.2f} px   ({measured})", style="Card.TLabel").pack(
            anchor=W, pady=(6, 0))
        ttk.Label(top, text="These stars were only measured for their size; this window sets the aperture and sky ring, "
                            "not which stars are used.", style="Hint.TLabel").pack(anchor=W)
        ttk.Label(top, text=(f"Aperture r = {prop['radius']:g} px (1.8 × FWHM)    sky in = {prop['sky_in']:g}    "
                             f"sky out = {prop['sky_out']:g} px (sky ring with 4× the aperture's area)"),
                  style="Card.TLabel", font=("Segoe UI", 11, "bold")).pack(anchor=W, pady=(6, 0))
        ttk.Label(top, text="Bright stars do best with 1.5–2 × FWHM; faint stars with 1–1.5 ×. The seeing changes during "
                            "the night, so the test below measures real frames and lets the check star decide.",
                  style="Hint.TLabel", wraplength=780, justify=LEFT).pack(anchor=W, pady=(4, 0))
        for note in notes:
            ttk.Label(top, text=note, style="Warn.TLabel" if note.startswith("⚠") else "Hint.TLabel",
                      wraplength=780, justify=LEFT).pack(anchor=W, pady=(4, 0))
        self.result_text = StringVar(value="")
        ttk.Label(top, textvariable=self.result_text, style="Box.TLabel", justify=LEFT).pack(anchor=W, pady=(10, 0))
        nav = ttk.Frame(top, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", pady=(10, 0))
        ttk.Button(nav, text="Use these aperture sizes", style="Accent.TButton", command=self.use).pack(side=LEFT)
        ttk.Button(nav, text="Test 4 apertures on 30 frames", command=lambda: app.test_apertures(fwhm, self, 30)).pack(
            side=LEFT, padx=8)
        ttk.Button(nav, text="60 frames (slower, surer)", command=lambda: app.test_apertures(fwhm, self, 60)).pack(
            side=LEFT)
        ttk.Button(nav, text="Close", command=win.destroy).pack(side=RIGHT)

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
        lines = [f"Measured on {rows[0]['n']} frames; sky ring {sky_in:g}–{sky_out:g} px. "
                 f"± is one standard error; 'lowest' is how often that aperture stays lowest when the frames are "
                 f"resampled ({result['boot']} times)."]
        if seeing:
            lines.append(f"Seeing (FWHM) in these frames: {min(seeing):.1f} to {max(seeing):.1f} px, "
                         f"median {np.median(seeing):.1f}.")
        lines.append("")
        lines.append(f"{'r (px)':>7}  {ref_label + ' scatter':>23}  {'lowest':>7}  {'target scatter':>17}  {'lowest':>7}")
        for row in rows:
            lines.append(f"{row['r']:>7g}  {row['ref_sigma']:>14.4f} ± {row['ref_err']:.4f}  {row['ref_win']:>6.0%}"
                         f"  {row['target_sigma']:>8.4f} ± {row['target_err']:.4f}  {row['target_win']:>6.0%}")
        lines.append("")
        best = result.get("ref_best")
        tbest = result.get("target_best")
        if best is not None and best["ref_win"] >= 0.8:
            pick = best["r"]
            lines.append(f"Clear winner: r = {pick:g} has the lowest {ref_label} scatter in {best['ref_win']:.0%} "
                         "of resamples.")
        else:
            pick = result.get("suggested_r") or (best or rows[0])["r"]
            lines.append(f"No clear winner: the differences are within the noise (no aperture is lowest in 80% of "
                         f"resamples). Keep the suggested r = {pick:g} (1.8 × FWHM).")
        if tbest is not None and tbest["target_win"] >= 0.8 and abs(tbest["r"] - pick) > 0.05:
            lines.append(f"The target alone prefers r = {tbest['r']:g} ({tbest['target_win']:.0%}). If the target is "
                         "much fainter than the check star, that can be the better choice; if it truly varies, its "
                         "scatter includes the variation.")
        self.prop = {"radius": pick, "sky_in": sky_in, "sky_out": sky_out}
        lines.append(f"Use these aperture sizes now sets r = {pick:g}, sky {sky_in:g}–{sky_out:g}.")
        self.result_text.set("\n".join(lines))


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
        ttk.Label(top, text="Watch stars", font=("Segoe UI", 15, "bold"), style="Card.TLabel").pack(anchor=W)
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
        ttk.Label(top, text="Export", font=("Segoe UI", 15, "bold"), style="Card.TLabel").pack(anchor=W)
        ttk.Label(
            top,
            text="Values follow the Output page: flagged points left out if that box is ticked, check-star "
                 "correction and segment line-up as set there. The file header records every choice.",
            style="Hint.TLabel", wraplength=700, justify=LEFT,
        ).pack(anchor=W, pady=(2, 10))

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
        ttk.Label(top, text="Known variables in the field", font=("Segoe UI", 15, "bold"), style="Card.TLabel").pack(anchor=W)
        known = r.get("known")
        if known is None:
            why = ("VSX and SIMBAD could not be reached during the scan. Scan again when online."
                   if r.get("radec") is not None else
                   "This scan has no sky positions, so it could not be checked against VSX and SIMBAD. "
                   "Label chart before scanning.")
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
        ttk.Label(top, text="Field scan", font=("Segoe UI", 15, "bold"), style="Card.TLabel").pack(anchor=W)
        method = (result.get("color_method") or {}).get("bv", "")
        fit = (result.get("color_fits") or {}).get("bv")
        color_note = {
            "field": (f" B−V is calibrated to catalog colors with {fit['n']} labeled field stars "
                      f"(color term {fit['k']:.2f}, scatter {fit['scatter']:.3f})." if fit else ""),
            "saved": " B−V uses the color term saved from an earlier field fit, anchored on the comp's catalog color.",
            "comps": " B−V is the comp's catalog color plus the raw channel difference (untransformed; Label chart with "
                     "Gaia DR3 or APASS to calibrate).",
            "relative": " B−V is relative to the median star (no catalog colors; Label chart with Gaia DR3 or APASS).",
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
        nav = ttk.Frame(top, style="Card.TFrame")
        nav.pack(fill=X, side="bottom", pady=(6, 0))
        self.mark_button = ttk.Button(nav, text="Clear candidate marks" if app.scan_marks else "Mark candidates on image",
                                      command=self.toggle_marks)
        self.mark_button.pack(side=LEFT)
        ttk.Button(nav, text="Export candidates CSV…", command=self.export_candidates).pack(side=LEFT, padx=6)
        ttk.Button(nav, text="Export all light curves CSV…", command=self.export_curves).pack(side=LEFT)
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
        for col, label, width in (
            ("rank", "#", 40), ("star", "Star / position", 210), ("mag", "Mag", 70), ("scatter", "Scatter", 80),
            ("excess", "× normal", 80), ("period", "Best period (d)", 110),
            ("color", "B−V (rel.)" if method == "relative" else "B−V", 70), ("caution", "Check first", 230),
            ("vsx", "VSX", 260),
        ):
            self.tree.heading(col, text=label)
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
                vsx_text = "no sky positions (run Label chart)"
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
        order = np.argsort(mag[ok])
        ax.plot(mag[ok][order], exp[ok][order], "-", color="#0067c0", lw=1, label="typical scatter")
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
                ls = core.lomb_scargle(r["jd"][good], lc[good], float(self.app.min_period.get()), float(self.app.max_period.get()))
                per = {"period": ls["period_days"], "power": ls["power"], "ls": ls} if ls else None
            except Exception:
                per = None
        if per:
            ls = per["ls"]
            ax.plot(1.0 / ls["freqs"], ls["power_spectrum"], color="#0067c0", lw=0.8)
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
    if sys.stdout is None or sys.stderr is None:
        try:
            log = open(LOG_FILE, "a", encoding="utf-8")
            sys.stdout = sys.stdout or log
            sys.stderr = sys.stderr or log
        except OSError:
            pass
    enable_windows_dpi()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
