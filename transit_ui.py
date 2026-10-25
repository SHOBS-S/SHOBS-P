"""Transits mode (2.1): the Transit fit page, the planner, the nearby-eclipsing-binary check, and the
ExoFOP package. Built by the App in clearv_app.py; everything here reaches the App through `app`."""
from __future__ import annotations

import csv
import hashlib
import io
import math
import os
import re
import shutil
import sys
import threading
import traceback
from tkinter import BOTH, END, LEFT, RIGHT, VERTICAL, W, X, Y, BooleanVar, DoubleVar, StringVar, Text, Toplevel, \
    filedialog, messagebox, ttk

import numpy as np

import photometry_core as core
import transit_core as tc
import transit_posterior as tpost   # 2.2.19 (#31)

try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
except ImportError:
    FigureCanvasTkAgg = None
    Figure = None

MCMC_CHOICES = {"Quick (1,500 steps)": 1500, "Standard (3,000 steps)": 3000, "Thorough (8,000 steps)": 8000}
GREEN = "#0b6e4f"
PINK = "#c2185b"


def fit_to_content(win, min_w: int = 480, min_h: int = 320) -> None:
    """2.2.2: open a pop-up at the size its contents ask for, no bigger than 90% of the screen (as in clearv_app)."""
    try:
        win.update_idletasks()
        sw, sh = int(win.winfo_screenwidth()), int(win.winfo_screenheight())
        w = min(max(int(win.winfo_reqwidth()), min_w), int(0.9 * sw))
        h = min(max(int(win.winfo_reqheight()), min_h), int(0.9 * sh))
        win.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        win.minsize(min(w, 420), min(h, 280))
    except Exception:
        pass


def col_width(widget, label: str, width: int) -> int:
    try:
        from tkinter import font as tkfont
        heading = tkfont.Font(widget, font=(core.UI_FONT, 10, "bold"))
        scale = max(float(widget.tk.call("tk", "scaling")) / 1.3333, 1.0)
        return int(max(width * scale, heading.measure(label) + 30))
    except Exception:
        return width


def _col(tree, col, label, width, stretch=False):
    tree.heading(col, text=label, anchor=W)
    cw = col_width(tree, label, width)
    tree.column(col, width=cw, minwidth=cw, anchor=W, stretch=stretch)


def planet_summary(p: dict | None) -> str:
    if not p or not p.get("period"):
        return "No planet yet: enter one on the Input page (Planet, Lookup) or open an exoplanet report."
    parts = [p.get("name") or "planet", f"P {p['period']:.6f} d"]
    if p.get("t14"):
        parts.append(f"T14 {p['t14'] * 24:.2f} h")
    depth, _label = tc.published_depth(p)     # 2.2.13: (Rp/R*)² when Rp/R* is known
    if depth:
        parts.append(f"depth {depth * 100:.2f}%")
    if p.get("k"):
        parts.append(f"Rp/R* {p['k']:.4f}")
    if p.get("a_rs"):
        parts.append(f"a/R* {p['a_rs']:.2f}")
    if p.get("b") is not None:
        parts.append(f"b {p['b']:.2f}")
    if p.get("source"):
        parts.append(f"({p['source']})")
    return "   ".join(parts)



def quality_text(r) -> str:
    """2.2.14 (GitHub #5): "Fit rms 0.48% per point · 0.21% per 10-min bin · 68 points · β 1.12 · χ²ν 1.05"."""
    if not r or r.get("rms") is None:
        return ""
    parts = [f"Fit rms {r['rms'] * 100:.3f}% per point"]
    if r.get("rms_bin10") is not None:
        parts.append(f"{r['rms_bin10'] * 100:.3f}% per 10-min bin")
    if r.get("n_points") is not None:
        parts.append(f"{r['n_points']} points")
    if r.get("beta") is not None:
        parts.append(f"red-noise β {r['beta']:.2f}")
    if r.get("rchi2") is not None:
        parts.append(f"χ²ν {r['rchi2']:.2f} (raw errors)")
    return " · ".join(parts)

class TransitPage:
    """Output page in Transits mode."""

    def __init__(self, app, parent):
        self.app = app
        self.result = None
        self.report = None          # a loaded AAVSO exoplanet report (EXOTIC), when that is the source
        self.source_night = None
        self.meta_for_files = {}
        self.frame = ttk.Frame(parent)
        card = ttk.Frame(self.frame, style="Card.TFrame", padding=12)
        card.pack(fill=BOTH, expand=True)
        ttk.Label(card, text="Output – Transit fit", font=(core.UI_FONT, 16, "bold"), style="Card.TLabel").pack(anchor=W)
        self.planet_text = StringVar(value=planet_summary(None))
        planet_label = ttk.Label(card, textvariable=self.planet_text, style="Hint.TLabel", wraplength=700, justify=LEFT)
        planet_label.pack(anchor=W, fill=X, pady=(2, 6))
        # Wrap to the window, never wider: a long fixed wrap made the page wider than some screens (2.2).
        card.bind("<Configure>", lambda e: planet_label.configure(wraplength=max(e.width - 40, 300)), add="+")
        self._fit_signature = None        # the data the shown fit was made from
        self._auto_tried = None           # the data an automatic fit was last started for

        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill=X, pady=2)
        ttk.Label(row, text="Night", style="Card.TLabel").pack(side=LEFT)
        self.night = StringVar()
        # 2.2.7: the box shows "K2-113 b · night of 5–6 Oct 2026"; self.night keeps the JD key behind it.
        self.night_label = StringVar()
        self._night_keys = {}
        self.night_box = ttk.Combobox(row, textvariable=self.night_label, width=36, state="readonly")
        self.night_box.pack(side=LEFT, padx=(4, 6))
        self.night_box.bind("<<ComboboxSelected>>", lambda _e: self._night_picked())
        ttk.Button(row, text="Open AAVSO exoplanet report (EXOTIC)…", command=self.open_report).pack(side=LEFT, padx=6)
        self.close_report_button = ttk.Button(row, text="Close report", command=self.close_report)
        self.source_text = StringVar(value="")
        ttk.Label(row, textvariable=self.source_text, style="Hint.TLabel").pack(side=LEFT, padx=8)

        row2 = ttk.Frame(card, style="Card.TFrame")
        row2.pack(fill=X, pady=2)
        self.detrend_airmass = BooleanVar(value=True)
        self.detrend_time = BooleanVar(value=False)
        ttk.Label(row2, text="Detrend", style="Card.TLabel").pack(side=LEFT)
        ttk.Checkbutton(row2, text="airmass", variable=self.detrend_airmass).pack(side=LEFT, padx=(4, 0))
        ttk.Checkbutton(row2, text="time slope", variable=self.detrend_time).pack(side=LEFT, padx=(6, 0))
        ttk.Label(row2, text="MCMC", style="Card.TLabel").pack(side=LEFT, padx=(16, 4))
        self.mcmc = StringVar(value="Standard (3,000 steps)")
        ttk.Combobox(row2, textvariable=self.mcmc, values=list(MCMC_CHOICES), width=22, state="readonly").pack(side=LEFT)
        ttk.Button(row2, text="Fit transit", style="Accent.TButton", command=self.fit).pack(side=LEFT, padx=10)
        ttk.Button(row2, text="Cancel", command=app.cancel_job).pack(side=LEFT)
        # 2.2.13: put the night's cloud-flagged frames back (error bars doubled); shown only when there are some.
        self.refit_flagged_button = ttk.Button(row2, text=f"Refit with flagged frames (errors ×"
                                                          f"{tc.FLAGGED_ERR_FACTOR:g})",
                                               command=lambda: self.fit(with_flagged=True))
        # 2.2.13: a report carries no flip segment: the user can say its largest gap is the meridian flip.
        self.flip_at_gap = BooleanVar(value=False)
        self.flip_at_gap_check = ttk.Checkbutton(row2, text="meridian flip at the largest gap",
                                                 variable=self.flip_at_gap)
        self._data_extra = {}

        row4 = ttk.Frame(card, style="Card.TFrame")
        row4.pack(fill=X, pady=(2, 2))
        ttk.Button(row4, text="Tonight's transits…", command=lambda: FinderWindow(app)).pack(side=LEFT)
        ttk.Button(row4, text="Transit planner…", command=lambda: PlannerWindow(app)).pack(side=LEFT, padx=(6, 0))
        ttk.Button(row4, text="Nearby-binary (NEB) check…", command=lambda: NebWindow(app, self)).pack(side=LEFT, padx=6)
        # 2.2.19 (#18): several nights of the planet fitted together; timing per epoch and an updated ephemeris.
        ttk.Button(row4, text="Multi-night…", command=self.open_multinight).pack(side=LEFT)
        # 2.2.17 (item 4): Variable View's comp windows here too; a Use comps change refits the transit.
        self.comp_health_button = ttk.Button(row4, text="Comp health…", command=self.open_comp_health)
        self.comp_health_button.pack(side=LEFT, padx=(12, 0))
        self.use_comps_button = ttk.Button(row4, text="Use comps…", command=self.open_use_comps)
        self.use_comps_button.pack(side=LEFT, padx=(6, 0))
        # 2.2.19 (#31): the MCMC posterior: corner plot window, and credible bands on the plot (on by default).
        ttk.Button(row4, text="Corner plot…", command=self.open_corner).pack(side=LEFT, padx=(12, 0))
        self.show_bands = BooleanVar(value=True)
        ttk.Checkbutton(row4, text="credible bands", variable=self.show_bands,
                        command=lambda: self.draw() if self.result else None).pack(side=LEFT, padx=(6, 0))
        self.comp_hint = StringVar(value="")
        ttk.Label(row4, textvariable=self.comp_hint, style="Hint.TLabel").pack(side=LEFT, padx=8)
        # 2.2.19 (#18 review): the tools row wraps like text when the page is narrow (125 % on a 1366-px screen).
        self._row4, self._row4_key = row4, None
        try:
            row4.bind("<Configure>", lambda e: self._flow_row4(e.width), add="+")
            self.comp_hint.trace_add("write", lambda *_a: self._flow_row4(self._row4.winfo_width()))
        except Exception:
            pass
        self._refit_pending = False

        # 2.2.17 (item 5): leave out a stretch of time and refit (no new reduction, same comps).
        trim_row = ttk.Frame(card, style="Card.TFrame")
        trim_row.pack(fill=X, pady=(2, 2))
        ttk.Label(trim_row, text="Trim", style="Card.TLabel").pack(side=LEFT)
        ttk.Label(trim_row, text="from", style="Card.TLabel").pack(side=LEFT, padx=(6, 2))
        self.trim_from = StringVar(value="")
        from_box = ttk.Entry(trim_row, textvariable=self.trim_from, width=13)
        from_box.pack(side=LEFT)
        ttk.Label(trim_row, text="to", style="Card.TLabel").pack(side=LEFT, padx=(6, 2))
        self.trim_to = StringVar(value="")
        to_box = ttk.Entry(trim_row, textvariable=self.trim_to, width=13)
        to_box.pack(side=LEFT)
        for box in (from_box, to_box):
            try:
                box.bind("<Return>", lambda _e: self.trim_and_refit())
            except Exception:
                pass
        ttk.Button(trim_row, text="Trim and refit", command=self.trim_and_refit).pack(side=LEFT, padx=(8, 0))
        ttk.Button(trim_row, text="Clear trim", command=self.clear_trim).pack(side=LEFT, padx=(6, 0))
        self.trim_text = StringVar(value="")
        ttk.Label(trim_row, textvariable=self.trim_text, style="Hint.TLabel").pack(side=LEFT, padx=8)
        self._trims = []          # [(BJD_TDB from, to)]; ±inf for an open end
        self._drag = None         # the span last dragged across the plot (exact BJD behind the boxes' clock text)
        self._time_map = None     # (BJD_TDB sorted, BJD − JD UTC there, exact?) of the source shown
        self._span = None
        self._update_trim_text()

        prog = ttk.Frame(card, style="Card.TFrame")
        prog.pack(fill=X, side="bottom", pady=(4, 0))
        self.prog_row = prog
        # 2.2.9: the save buttons sit below the plot in a "Results" row, as on the Variables Output page (John).
        results = ttk.Frame(card, style="Card.TFrame")
        results.pack(fill=X, side="bottom", pady=(6, 0))
        self.results_row = results
        ttk.Label(results, text="Results", style="Hint.TLabel", width=8).pack(side=LEFT)
        ttk.Button(results, text="Save AAVSO exoplanet report…", style="Accent.TButton",
                   command=self.save_aavso).pack(side=LEFT)
        # 2.2.15 (items 8, 9): the upload pages, beside the saves they are for.
        ttk.Button(results, text="Open AAVSO Exoplanet Database…", command=self.open_aavso_exoplanet).pack(
            side=LEFT, padx=(6, 0))
        ttk.Button(results, text="Save ExoFOP package…", command=self.save_package).pack(side=LEFT, padx=(6, 0))
        ttk.Button(results, text="Open ExoFOP…", command=self.open_exofop).pack(side=LEFT, padx=6)
        # 2.2.18 (item 18): with a fit shown, the fit CSV (time, flux, error, model, residual) is saved with it.
        ttk.Button(results, text="Save light-curve CSV…", command=self.save_csv).pack(side=LEFT)
        # 2.2.7: the plot as shown, with the verdict and results above it; works for report fits too.
        ttk.Button(results, text="Save plot PNG…", command=self.save_png).pack(side=LEFT, padx=6)
        # Bottom right, the same place as "Transit View" on the Variables page (2.2.1).
        ttk.Button(results, text="Variable View  ▶", style="Accent.TButton", command=app.show_variables_view).pack(
            side=RIGHT)
        # 2.2.19 (#28): the frame with the target and comps marked, beside the AAVSO chart (after Save plot PNG…).
        ttk.Button(results, text="Save field image…", command=app.save_field_image).pack(side=LEFT)
        self.bar = ttk.Progressbar(prog, length=320, mode="determinate", maximum=1)
        self.bar.pack(side=LEFT)
        self.prog_text = StringVar(value="")
        self.prog_label = ttk.Label(prog, textvariable=self.prog_text, style="Hint.TLabel")
        self.prog_label.pack(side=LEFT, padx=8)
        self._fitting = False
        self._show_progress()

        # 2.2.7: which data the page is fitting, in plain words (a loaded report, or a night of photometry).
        self.banner = StringVar(value="")
        # 2.2.11: the banner and the verdict sit in one frame and take room only when they say something (an empty
        # label still kept a line each above the plot).
        self.heads = ttk.Frame(card, style="Card.TFrame")
        self.heads.pack(fill=X)
        self.banner_label = ttk.Label(self.heads, textvariable=self.banner, style="Card.TLabel", wraplength=1100,
                                      justify=LEFT, foreground="#8a4b00", font=(core.UI_FONT, 10, "bold"))
        card.bind("<Configure>", lambda e: self.banner_label.configure(wraplength=max(e.width - 40, 300)), add="+")
        # 2.2.10 (John): the plot is the page. The verdict sits above it, full width; the parameter table and the
        # notes run in a strip under it (as on the Field scan page), instead of a right-hand pane.
        self.verdict = StringVar(value="")
        self.verdict_label = ttk.Label(self.heads, textvariable=self.verdict, style="Card.TLabel", wraplength=1100,
                                       justify=LEFT, font=(core.UI_FONT, 11, "bold"))
        # 2.2.14 (GitHub #5): the fit's noise in one line under the verdict (it was only at the bottom of the table).
        self.quality = StringVar(value="")
        self.quality_label = ttk.Label(self.heads, textvariable=self.quality, style="Card.TLabel", justify=LEFT,
                                       wraplength=1100, foreground="#33424d", font=(core.UI_FONT, 10))
        card.bind("<Configure>", lambda e: self.quality_label.configure(wraplength=max(e.width - 40, 300)), add="+")
        for var in (self.banner, self.verdict, self.quality):
            var.trace_add("write", lambda *_a: self._show_heads())
        self._show_heads()
        card.bind("<Configure>", lambda e: self.verdict_label.configure(wraplength=max(e.width - 40, 300)), add="+")
        body = ttk.Frame(card, style="Card.TFrame")
        body.pack(fill=BOTH, expand=True, pady=(4, 0))
        self.body = body
        strip = ttk.Frame(body, style="Card.TFrame")
        strip.pack(fill=X, side="bottom", pady=(6, 0))
        self.strip = strip
        # 2.2.11 (John): the table fills the strip; cautions, when there are any, take a third on the right.
        tablebox = ttk.Frame(strip, style="Card.TFrame")
        tablebox.grid(row=0, column=0, sticky="nsew")
        strip.rowconfigure(0, weight=1)
        cols = ("param", "shobs", "free", "pub", "exotic")
        # Eight rows show at once (the scroll bar reaches the rest), so the strip leaves the height to the plot.
        self.table = ttk.Treeview(tablebox, columns=cols, show="headings", height=8)
        for col, label, width in (("param", "Parameter", 150), ("shobs", "SHOBS-P (MCMC)", 170),
                                  ("free", "Free shape", 90), ("pub", "Published / prior", 130),
                                  ("exotic", "EXOTIC report", 150)):
            _col(self.table, col, label, width, stretch=False)
        self.table.tag_configure("warn", foreground="#b35c00")
        self.table.tag_configure("gray", foreground="#9aa7b0")
        self.table.tag_configure("graywarn", foreground="#d9b38c")
        yscroll = ttk.Scrollbar(tablebox, orient="vertical", command=self.table.yview)
        # A sideways scroll bar only when the window is too narrow for the columns.
        self.table_xscroll = ttk.Scrollbar(tablebox, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=yscroll.set, xscrollcommand=self._table_xview)
        self.table.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        self.table_xscroll.grid(row=1, column=0, sticky="ew")
        self.table_xscroll.grid_remove()
        tablebox.rowconfigure(0, weight=1)
        tablebox.columnconfigure(0, weight=1)
        self._set_columns()
        self.notes = Text(strip, height=8, width=20, wrap="word", font=(core.UI_FONT, 9), relief="flat", bg="#ffffff")
        # On a short window the table gives up rows (down to 3) before the plot gives up height.
        body.bind("<Configure>", self._fit_strip, add="+")
        self.notes.tag_configure("warn", foreground="#b35c00")
        self.notes.tag_configure("info", foreground="#33424d")
        # 2.2.17 (item 4): the comp-choice caution's link to Comp health.
        try:
            self.notes.tag_configure("link", foreground="#1565c0", underline=True)
            self.notes.tag_bind("link", "<Button-1>", lambda _e: self.open_comp_health())
            self.notes.tag_bind("link", "<Enter>", lambda _e: self.notes.configure(cursor="hand2"))
            self.notes.tag_bind("link", "<Leave>", lambda _e: self.notes.configure(cursor=""))
        except Exception:
            pass
        self.notes.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self._notes_shown = True
        self.fig = None
        if Figure is not None:
            self.fig = Figure(figsize=(8, 5.4), dpi=100, facecolor="#ffffff")
            gs = self.fig.add_gridspec(2, 1, height_ratios=(3, 1), hspace=0.06)
            self.ax = self.fig.add_subplot(gs[0])
            self.ax_r = self.fig.add_subplot(gs[1], sharex=self.ax)
            self.canvas = FigureCanvasTkAgg(self.fig, master=body)
            # Ask for a small size and let the page stretch it: the figure's own 8-inch width (more on a scaled
            # display) pushed the page past the screen edge (2.2).
            self.canvas.get_tk_widget().configure(width=420, height=320)
            self.canvas.get_tk_widget().pack(fill=BOTH, expand=True)
            # 2.2.10: margins in points, so a big plot does not float in wide fractional margins.
            try:
                self.canvas.mpl_connect("resize_event", lambda _e: self._set_margins())
            except Exception:
                pass
            self.draw(placeholder="No transit fit yet: pick a night or open an exoplanet report, then Fit transit.")
        # 2.2.11: no hint here (the plot's placeholder says what to do); the notes column shows only real notes.
        self._notes_text([])
        self._update_comp_buttons()

    def _flow_row4(self, width: int):
        """Lay the Transit tools row out in as many lines as its width needs (clearv_app.flow_rows)."""
        try:
            from clearv_app import flow_rows
            kids = list(self._row4.winfo_children())
            pads = [12 if w is self.comp_health_button else 8 for w in kids]
            widths = [int(w.winfo_reqwidth()) + p for w, p in zip(kids, pads)]
            rows = flow_rows(widths, max(int(width), 1) if width and width > 1 else None)
            key = tuple(tuple(r) for r in rows)
            if key == self._row4_key:
                return
            self._row4_key = key
            for w in kids:
                w.pack_forget()
                w.grid_forget()
            for r, row in enumerate(rows):
                for c, i in enumerate(row):
                    kids[i].grid(row=r, column=c, sticky=W, padx=(0, pads[i]), pady=(0, 2))
        except Exception:
            pass

    def open_multinight(self):
        """2.2.19 (#18): the multi-night transit window (one at a time; a second click brings it forward)."""
        win = getattr(self, "_multinight", None)
        if win is not None:
            try:
                win.win.deiconify()
                win.win.lift()
                return win
            except Exception:
                self._multinight = None
        import multinight
        self._multinight = multinight.MultiNightWindow(self.app, self)
        return self._multinight

    # ---- source --------------------------------------------------------------------------
    def refresh(self, shown: bool = False):
        """List the nights and show the planet. shown=True when the page has just been opened: then a fit made
        from data that have since changed is cleared, and fresh data are fitted straight away (2.2)."""
        nights = sorted({o.night for o in self.app.observations if o.night})
        labels = [self.app.night_label(n) for n in nights]
        self._night_keys = {(lab if labels.count(lab) == 1 else f"{lab} ({n})"): n for lab, n in zip(labels, nights)}
        self._key_labels = {n: lab for lab, n in self._night_keys.items()}
        self.night_box.configure(values=list(self._night_keys))
        if self.night.get() in nights:
            self.night_label.set(self._key_labels.get(self.night.get(), self.night.get()))
        if nights and (self.night.get() not in nights) and self.report is None:
            self.night.set(nights[-1])
            self.night_label.set(self._key_labels.get(nights[-1], nights[-1]))
            self._use_night(auto=False)
        self.planet_text.set(planet_summary(self.current_planet()))
        if self.report is None:
            self._trim_source_check()   # review: before the stale check, so the signature has no stale trim
        self._check_stale(auto=shown)
        self._count_text()
        self._update_comp_buttons()

    def _night_picked(self):
        key = self._night_keys.get(self.night_label.get())
        if key:
            self.night.set(key)
        self._use_night()

    def close_report(self, quiet: bool = False):
        """2.2.7: back to the photometry (newest night), dropping a loaded EXOTIC report."""
        if self.report is None:
            return
        self._clear_progress()
        self.report = None
        self.night.set("")
        self.result = None
        self._fit_signature = None
        self._reset_trim()      # 2.2.17 (item 5): a trim belongs to its source
        self._show_source()
        self._fill_table()
        self.draw()
        self.refresh(shown=not quiet)
        if not self.app.observations:
            self.source_text.set("")
            self._notes_text([("info", "Report closed. Run photometry in Transits mode, or open another report.")])

    def _show_source(self):
        """2.2.7: say what the page is fitting, here and on the Input page."""
        app = self.app
        if self.report is not None:
            r = self.report
            name = (r.get("planet") or {}).get("name") or "the planet"
            kept = ""
            if app.observations:
                target = app.planet_name.get().strip() or app.star_id.get().strip()
                # 2.2.17 review nit: no "Your your photometry" when the Input page names no target
                kept = (f" Your {target + ' ' if target else ''}photometry is kept; choose its night above (or Close "
                        "report) to go back.")
            # 2.2.17 (item 7): the program that wrote the report (#SOFTWARE), not always "EXOTIC".
            self.banner.set(f"Fitting {report_kind(r.get('header'))}: {name}, {self._report_date()}, {len(r['t'])} "
                            f"points from {os.path.basename(r.get('path', ''))}." + kept
                            + (" " + r["time_note"] if r.get("time_note") else ""))   # 2.2.17 review
            try:
                self.close_report_button.pack(side=LEFT, padx=(0, 6), after=self.night_box)
            except Exception:
                pass
            self._show_flip_check(True)
            app.set_report_note(f"The Transit fit page is showing {report_kind(r.get('header'))} for {name}, not the "
                                "photometry set up here.")
        else:
            self.banner.set("")
            try:
                self.close_report_button.pack_forget()
            except Exception:
                pass
            self._show_flip_check(False)
            app.set_report_note("")
        self._update_comp_buttons()

    def _show_flip_check(self, report_loaded: bool):
        """2.2.13: the "meridian flip at the largest gap" box, only for a loaded report (photometry has its flip
        segment) and only while the flip step is enabled."""
        try:
            if report_loaded and tc.FLIP_STEP_ENABLED:
                self.flip_at_gap_check.pack(side=LEFT, padx=(12, 0))
            else:
                self.flip_at_gap.set(False)
                self.flip_at_gap_check.pack_forget()
        except Exception:
            pass

    def _show_refit_flagged(self):
        """2.2.13: the refit button only when the night has flagged frames the fit left out (never for a report)."""
        try:
            fl = (self._data_extra or {}).get("flagged") if self.report is None else None
            if fl is not None and len(fl["t"]) and self.result is not None:
                self.refit_flagged_button.pack(side=LEFT, padx=(6, 0))
            else:
                self.refit_flagged_button.pack_forget()
        except Exception:
            pass

    def _report_date(self) -> str:
        """The report's OBSDATE, or (2.2.7) the UTC date of its first point when the header has none."""
        r = self.report or {}
        date = str((r.get("header") or {}).get("OBSDATE", "")).strip()
        if date:
            return date
        try:
            return core.jd_to_datetime_utc(float(np.min(r["t"]))).strftime("%d-%b-%Y").upper()
        except Exception:
            return ""

    def _use_night(self, auto: bool = True):
        self._clear_progress()
        if self.report is not None or self.night.get() != self.source_night:
            self._reset_trim()      # 2.2.17 (item 5): a trim belongs to its source
        self.report = None
        self._show_source()
        self.result = None
        self._fit_signature = None
        self._fill_table()
        self.draw()
        self.source_night = self.night.get()
        self._count_text()
        self.planet_text.set(planet_summary(self.current_planet()))
        if auto:
            self._check_stale(auto=True)

    def _count_text(self):
        """2.2.14 (GitHub #7): the night's usable points counted now (the label was counted once, when the night first
        appeared, sometimes while photometry was still running), plus the points the shown fit used."""
        if self.report is not None:
            return
        night = self.night.get()
        if not night:
            self.source_text.set("")
            return
        n = sum(1 for o in self.app._usable() if o.night == night)
        text = f"{n} usable points on {night}"
        r = self.result
        if r and r.get("n_points") is not None:
            m = int(r["n_points"])
            text += f"; {m} in this fit" + (f" ({m - n} flagged put back)" if m > n else "")
            if r.get("trim"):
                text += f", {r['trim']['n']} trimmed"     # 2.2.17 (item 5)
        self.source_text.set(text)

    def _signature(self):
        """What a fit depends on: the night's usable points (after comps, flags, and cloud cuts) and the planet."""
        if self.report is not None:
            return ("report", id(self.report), tuple(self._trims))   # 2.2.17 (item 5): the trim too
        night = self.night.get()
        obs = [o for o in self.app._usable() if o.night == night]
        if not obs:
            return None
        mags = [o.mag for o in obs if math.isfinite(o.mag)]
        return (night, len(obs), round(float(sum(mags)), 6), round(float(sum(o.jd for o in obs)), 6),
                tuple(self.app.active_comps or self.app.comps_used), planet_summary(self.current_planet()),
                str(self.app._scint_setup() if hasattr(self.app, "_scint_setup") else None),
                tuple(self._trims))   # 2.2.17 (item 5)

    def _check_stale(self, auto: bool = False):
        """Clear a fit whose photometry, comps, or planet have changed since; fit fresh data when asked."""
        if self.report is not None:
            return
        sig = self._signature()
        if self.result is not None and sig != self._fit_signature:
            self.result = None
            self._fit_signature = None
            self._fill_table()
            self.draw()
            self._loo_shown = False   # 2.2.16 (round-2 review): a comp check still running belongs to that fit
            self.prog_text.set("")
            self._show_progress()
            self._notes_text([("warn", "The photometry, the comps, or the planet changed since the last fit, so that "
                                       "fit was cleared. Fit transit to fit the data as they are now.")])
        if not auto or self.result is not None or sig is None or sig == self._auto_tried or self.app.busy:
            return
        planet = self.current_planet()
        n = sig[1]
        if not planet:
            self._notes_text([("info", "Photometry is ready. Enter the planet on the Input page (name, then Lookup) "
                                       "and come back: the fit then runs by itself.")])
            return
        if n < 20:
            return
        self._auto_tried = sig
        self.fit(auto=True)

    def current_planet(self) -> dict | None:
        p = self.app.planet_from_fields()
        if self.report is not None:
            rp = self.report.get("planet")
            if not p or not p.get("period") or _norm(p.get("name")) != _norm(rp.get("name")):
                return rp
        if p and p.get("period") and not p.get("depth_note"):
            # 2.2.13: the lookup's note on a listed depth that disagrees with Rp/R* (the Input page keeps only k² as
            # the depth), while the page still shows that planet's Rp/R*.
            info = getattr(self.app, "planet_info", None) or {}
            if (info.get("depth_note") and _norm(info.get("name")) == _norm(p.get("name")) and p.get("k")
                    and info.get("k") and abs(info["k"] - p["k"]) <= 1e-4 * max(info["k"], 1e-3)):
                p["depth_note"] = info["depth_note"]
        return p if p and p.get("period") else None

    def open_report(self):
        path = filedialog.askopenfilename(parent=self.app, title="AAVSO exoplanet report",
                                          filetypes=[("Text", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                rep = tc.parse_exoplanet_report(fh.read())
            rep["planet"] = tc.planet_from_report(rep)
            rep["path"] = path
        except Exception as exc:
            messagebox.showerror("SHOBS-P", f"Could not read {os.path.basename(path)}:\n{exc}")
            return
        self._report_time_system(rep)   # 2.2.17 review: #DATE_TYPE other than BJD_TDB
        self._clear_progress()
        self.report = rep
        self._reset_trim()      # 2.2.17 (item 5)
        self.night.set("")
        self.night_label.set("")
        h = rep["header"]
        self.source_text.set(f"{os.path.basename(path)}: {len(rep['t'])} points, {h.get('SOFTWARE', '')}, "
                             f"{self._report_date()}")
        self.planet_text.set(planet_summary(self.current_planet()))
        self.result = None
        self._fit_signature = None
        self._show_source()
        self._fill_table()
        # 2.2.7: the old plot goes at once, and the report is fitted straight away (as new photometry is).
        name = (rep.get("planet") or {}).get("name") or "the planet"
        # 2.2.12: a report without an ephemeris (no QC-XC line) gets one from the NASA Exoplanet Archive, else from
        # EXOTIC's inits file beside the report, before the fit; never from the lowest point.
        need_eph = not (rep.get("planet") or {}).get("t0") and self.current_planet() is rep.get("planet")
        what = ("looking up the ephemeris…" if need_eph else
                "fitting…" if not self.app.busy else "press Fit transit when the current job ends")
        writer = report_software(h) or "the report"
        self.draw(placeholder=f"{name}, {self._report_date()}, {len(rep['t'])} points from {writer}: {what}")
        self._notes_text([("info", "Report loaded and being fitted. SHOBS-P refits its light curve and lists "
                                   f"the report's own results ({writer}) beside SHOBS-P's. Cancel stops the fit; Fit "
                                   "transit runs it again with other settings.")])
        if need_eph:
            self._eph_pending = rep
            self._find_ephemeris(rep)
        elif not self.app.busy:
            self.fit(auto=True)

    def _find_ephemeris(self, rep):
        """2.2.12: NASA Exoplanet Archive (most precise ephemeris) first, then an EXOTIC inits file beside the
        report. Runs in the background; the fit starts when it is done (if the report is still the one shown)."""
        planet = rep["planet"]
        app = self.app

        def work():
            found, why = None, ""
            try:
                got = tc.lookup_planet(planet.get("name") or planet.get("host") or "")
                if got.get("t0") and got.get("period"):
                    found = {k: got.get(k) for k in ("t0", "t0_err", "period", "period_err")}
                    found["eph_source"] = tc.ephemeris_source(got)
            except Exception as exc:
                why = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            if found is None:
                try:
                    found = tc.ephemeris_from_inits(os.path.dirname(rep.get("path", "")), planet.get("name", ""),
                                                    planet.get("host", ""))
                except Exception:
                    found = None
            app.call_ui(self._ephemeris_found, rep, found, why)

        threading.Thread(target=work, daemon=True).start()

    def _ephemeris_found(self, rep, found, why):
        if getattr(self, "_eph_pending", None) is rep:
            self._eph_pending = None
        if self.report is not rep:
            return
        planet = rep["planet"]
        if found:
            # The period too: the ephemeris's t0 and period belong together.
            planet.update({k: v for k, v in found.items() if v is not None})
            planet.setdefault("eph_source", found.get("eph_source", ""))
            self.app.log(f"Transit fit: the report has no ephemeris; using {found.get('eph_source')}.")
        else:
            self.app.log("Transit fit: the report has no ephemeris and none was found"
                         + (f" ({why})" if why else "") + "; no O − C will be given.")
        self.planet_text.set(planet_summary(self.current_planet()))
        self._fill_table()
        if not self.app.busy:
            name = planet.get("name") or "the planet"
            self.draw(placeholder=f"{name}, {self._report_date()}, {len(rep['t'])} points from "
                                  f"{report_software(rep.get('header')) or 'the report'}: fitting…")
            self.fit(auto=True)
        else:
            self.draw(placeholder="Press Fit transit when the current job ends.")

    def _data(self):
        """(t BJD_TDB, flux, err, airmass, meta for files) from the chosen source."""
        app = self.app
        if self.report is not None:
            r = self.report
            am = r["airmass"] if r["airmass"] is not None else np.full(len(r["t"]), np.nan)
            h = r["header"]
            meta = {"obscode": h.get("OBSCODE", ""), "obsdate": self._report_date(), "lat": h.get("OBSLAT", ""),
                    "lon": h.get("OBSLON", ""), "elev": h.get("OBSELEV", ""), "exposure": h.get("EXPOSURE_TIME", ""),
                    "binning": h.get("BINNING", ""), "filter": h.get("FILTER", "CV"), "notes": h.get("NOTES", ""),
                    "source": os.path.basename(r.get("path", ""))}
            t = np.asarray(r["t"])
            # 2.2.13: a report has no flip segment. Use the flip SHOBS-P wrote into its NOTES, else, when the user ticks
            # "meridian flip at the largest gap", the middle of the largest gap (never guessed on its own).
            self._data_extra = {"after_flip": None, "flip_source": "", "flagged": None}
            t_flip = tc.report_flip_time(h)
            source = "from the report's notes"
            if t_flip is None and self.flip_at_gap.get():
                t_flip = tc.flip_from_gap(t)
                source = "the report's largest gap, marked as the flip by the user"
            if t_flip is not None:
                self._data_extra.update(after_flip=t >= t_flip, flip_source=source)
            return t, np.asarray(r["flux"]), np.asarray(r["err"]), am, meta
        night = self.night.get()
        obs = [o for o in app._usable() if o.night == night]
        if len(obs) < 20:
            raise ValueError(f"{night or 'No night'}: {len(obs)} usable points. Run photometry in Transits mode "
                             "first (or open an exoplanet report).")
        ra, dec = app.target_radec_deg()
        if not (app.lat.get().strip() and app.lon.get().strip()):
            raise ValueError("Enter your site's latitude and longitude on the Input page first (they are needed "
                             "for BJD times).")
        lat, lon = float(app.lat.get()), float(app.lon.get())
        jd = np.array([o.jd for o in obs])
        t = core.convert_times(jd, "BJD_TDB", ra, dec, lat, lon)
        mag = np.array([o.mag for o in obs])
        scint = app._scint_setup() if hasattr(app, "_scint_setup") else None
        merr = np.array([core.point_err(o, scint) for o in obs])
        ref = np.median(mag)
        flux = 10 ** (-0.4 * (mag - ref))
        err = np.abs(flux * merr * 0.4 * math.log(10))
        am = np.array([o.airmass if o.airmass is not None else np.nan for o in obs], dtype=float)
        exps = [o.exptime for o in obs if o.exptime]
        from datetime import datetime, timezone
        utc = datetime.fromtimestamp((float(jd.min()) - 2440587.5) * 86400.0, tz=timezone.utc)
        meta = {"obscode": app.observer.get().strip(), "obsdate": utc.strftime("%d-%b-%Y").upper(),
                "lat": f"{lat:.4f}", "lon": f"{lon:.4f}", "elev": app.elevation.get().strip(),
                "exposure": f"{np.median(exps):.1f}" if exps else "", "binning": "1x1",   # 2.2.19 (#27): _binning_meta
                "filter": obs[0].filt or "CV", "notes": app.notes.get().strip(),
                "aperture": f"r {app.radius.get():g} px, sky {app.sky_in.get():g}-{app.sky_out.get():g} px",
                "comps": ", ".join(app.active_comps or app.comps_used), "source": f"night {night}",
                "scint": app._scint_note() if hasattr(app, "_scint_note") else ""}
        # 2.2.15 (item 11): the ExoFOP notes' telescope/camera, pixel scale and aperture for this night.
        try:
            eq = app.exofop_equipment(night)
        except Exception:
            eq = {}
        if eq:
            meta.update(telescope=eq.get("telescope", ""), scale_arcsec=eq.get("scale"), radius_px=eq.get("radius_px"),
                        bin=eq.get("binning"), camera_bin=eq.get("camera_bin", 1), superpixel=eq.get("superpixel", 1),
                        sky=eq.get("sky", ""))
        # 2.2.19 (#27): #BINNING is how the camera binned the frames; SHOBS-P's own Binning goes into the notes.
        self._binning_meta(meta, obs, eq)
        try:
            self._repro_meta(meta, obs, night, t)   # 2.2.19 (#39, #40): gain and the run's settings
        except Exception as exc:
            meta.pop("repro", None)
            app.log(f"Settings record skipped: {exc}")
        try:
            meta["comp_noise"] = core.comp_noise_warning(obs)  # 2.2.8
        except Exception:
            meta["comp_noise"] = None
        # 2.2.13: the flip segment (frames after a meridian flip or rotator move, as on the Variables side), and the
        # night's cloud-flagged frames that the fit leaves out: the cautions say when they emptied a baseline, and
        # "Refit with flagged frames" can put them back. Only cloud: saturated, drifted or comp-less frames are not
        # measurements to put back. Nothing here needs the images, a plate solution or header pointing.
        after = np.array([o.segment == "flip" for o in obs], dtype=bool)
        extra = {"after_flip": after if (after.any() and (~after).any()) else None,
                 "flip_source": "the frames' flip segment", "flagged": None,
                 "jd": jd}   # 2.2.17 (item 5): JD UTC beside BJD_TDB, for the trim's local clock times
        used = {id(o) for o in obs}
        clouded = [o for o in app.observations if o.night == night and id(o) not in used and math.isfinite(o.mag)
                   and o.flag and set(o.flag.split()) <= {"cloud", "cloudedge", core.HAZE_FLAG}]   # 2.2.14 (items 10, 18)
        if clouded:
            cjd = np.array([o.jd for o in clouded])
            cmag = np.array([o.mag for o in clouded])
            cflux = 10 ** (-0.4 * (cmag - ref))          # the same reference as the clear frames
            cerr = np.abs(cflux * np.array([core.point_err(o, scint) for o in clouded]) * 0.4 * math.log(10))
            cam = np.array([o.airmass if o.airmass is not None else np.nan for o in clouded], dtype=float)
            extra["flagged"] = {"t": core.convert_times(cjd, "BJD_TDB", ra, dec, lat, lon), "jd": cjd, "flux": cflux,
                                "err": cerr, "airmass": cam, "after_flip": np.array([o.segment == "flip" for o in clouded],
                                                                                    dtype=bool), "label": "cloud"}
        # 2.2.16 (item 6): each comp's instrumental magnitude in every frame, for the leave-one-comp-out check
        # (comps only: nothing here needs a plate solution, header pointing or a color camera).
        try:
            sel = list(app.active_comps or app.comps_used)
            if len(sel) >= 2:
                try:
                    aliases = app._star_aliases()
                except Exception:
                    aliases = None

                def insts(points):
                    return np.array([[core.star_inst(o, n, app.comps_used, aliases) for n in sel] for o in points],
                                    dtype=float).reshape(len(points), len(sel))
                extra["comp_mags"], extra["comp_names"] = insts(obs), sel
                if extra.get("flagged") is not None:
                    extra["flagged"]["comp_mags"] = insts(clouded)
        except Exception:
            extra.pop("comp_mags", None)
        self._data_extra = extra
        return t, flux, err, am, meta

    def _binning_meta(self, meta: dict, obs, eq: dict) -> None:
        """2.2.19 (#27): meta["binning"] = the camera's binning (core.camera_binning: XBINNING/YBINNING, MicroObservatory
        2x2, else 1x1) from the night's own frames (the Input page's lights when those cannot be read);
        meta["camera_binning"] for the ExoFOP notes; SHOBS-P's processing bin appended to meta["notes"]."""
        app = self.app
        paths = [o.path for o in obs[:3] if getattr(o, "path", "")]
        found = None
        for path in paths:
            try:
                head = core.read_header(path)
            except Exception:
                head = None
            if head:
                found = core.camera_binning(path, head)
                break
        if found is None:
            # Review: the Input page's lights only when they are this night's camera (the night's stored instrument
            # name, as exofop_equipment checks); a loaded series' night may come from other equipment.
            night = obs[0].night if obs else ""
            stored = str((app._night_info(night) if night else {}).get("instrument") or "").strip()
            try:
                lights = list(app._lights()[:1])
            except Exception:
                lights = []
            for path in lights:
                try:
                    head = core.read_header(path) or {}
                except Exception:
                    continue
                live = " / ".join(p for p in (str(head.get(k) or "").strip() for k in ("TELESCOP", "INSTRUME")) if p)
                if stored and live == stored:
                    found = core.camera_binning(path, head)
        if found is None:
            found = core.camera_binning(paths[0] if paths else "", {})   # a MOBS_ file name still counts
        xb, yb, how = found
        meta["binning"] = f"{xb}x{yb}"
        meta["camera_binning"] = f"{xb}x{yb}" + (" (assumed: no binning keyword in the headers)" if how == "assumed"
                                                 else " (MicroObservatory)" if how == "MicroObservatory" else "")
        try:
            pbin = int((eq or {}).get("binning") or app.binning.get() or 1)
        except Exception:
            pbin = 1
        try:
            superpixel = int((eq or {}).get("superpixel") or (1 if app.pattern.get() == core.MONO else 2))
        except Exception:
            superpixel = 1
        if superpixel == 2:
            note = (f"SHOBS-P processing bin {pbin} (OSC superpixel base: {2 * pbin}x{2 * pbin} camera pixels per "
                    "measured pixel)")
        else:
            note = f"SHOBS-P processing bin {pbin} (mono: {pbin}x{pbin} camera pixels per measured pixel)"
        meta["processing_bin"] = note
        meta["notes"] = (meta.get("notes", "") + " | " if meta.get("notes") else "") + note

    def _repro_meta(self, meta: dict, obs, night: str, t) -> None:
        """2.2.19 (#39, #40): what a problem night needs to be reproduced, from the night's stored setup (what the
        photometry was measured with, not what the boxes say now): meta["gain"] (gain and its source),
        meta["repro"] = {name: text} (aperture, bins, comps, check, frames left out, calibration, gain), and
        meta["frame_t"] = (BJD_TDB of the fitted frames, their frame numbers) for trimmed frames."""
        app = self.app
        setup = app._night_info(night) if night else {}
        pn = setup.get("photon_noise") or getattr(app, "_noise_model", None) or {}
        meta["gain"] = gain_note(pn)
        rep = {}
        radius, s_in, s_out = setup.get("radius"), setup.get("sky_in"), setup.get("sky_out")
        pbin = setup.get("binning") or app.binning.get()
        if radius:
            ap = f"r {radius:g} px, sky {s_in:g}-{s_out:g} px" if s_in and s_out else f"r {radius:g} px"
            rep["aperture"] = ap + f" (at processing bin {pbin})"
            meta["aperture"] = ap      # what was measured (the boxes may have changed since; #41)
        rep["bin"] = (f"camera {meta.get('binning', '?')}; " + str(meta.get("processing_bin") or f"processing bin {pbin}"))
        comps = list(app.active_comps or setup.get("comps") or app.comps_used)
        rep["comps"] = ", ".join(comps) if comps else "none recorded"
        if app.active_comps:
            rep["comps"] += f" (Use comps choice; measured: {', '.join(setup.get('comps') or app.comps_used)})"
        check = setup.get("check") if "check" in setup else app.check_used
        rep["check"] = check or "none"
        # Frame numbers: the number at the end of each light's file name (as Blink shows it), else the frame's place
        # in the night's measured frames.
        night_obs = sorted([o for o in app.observations if o.night == night], key=lambda o: o.jd)
        nums = dict(zip((id(o) for o in night_obs), frame_numbers([getattr(o, "path", "") for o in night_obs])))
        used = {id(o) for o in obs}
        out_by = {}
        for o in night_obs:
            if id(o) not in used:
                why = (o.flag or "no magnitude").split()[0] if (o.flag or not math.isfinite(o.mag)) else "left out"
                out_by.setdefault(why, []).append(nums[id(o)])
        rep["left_out"] = "; ".join(f"{why} {frame_ranges(v)}" for why, v in sorted(out_by.items())) or "none"
        meta["frame_t"] = (np.asarray(t, dtype=float), np.array([nums[id(o)] for o in obs]))
        masters = setup.get("masters") or []
        cal = "; ".join(str(m) for m in masters) if masters else "none recorded"
        dexp = getattr(getattr(app, "masters", None), "dark_exptime", None)
        if dexp and any("dark" in str(m) for m in masters):
            cal += f"; dark EXPTIME {dexp:g} s"
        rep["calibration"] = cal
        rep["gain"] = meta["gain"]
        meta["repro"] = rep

    def settings_lines(self, meta: dict | None = None, result: dict | None = None) -> list:
        """2.2.19 (#40): the full settings block (log and <report stem>_settings.txt): one 'Name: value' per line."""
        meta = meta if meta is not None else (self.meta_for_files or {})
        r = result if result is not None else (self.result or {})
        rep = dict(meta.get("repro") or {})
        if not rep:
            return []
        lines = [f"Source: {meta.get('source', '')}", f"Aperture: {rep.get('aperture', 'not recorded')}",
                 f"Binning: {rep.get('bin', '')}", f"Comps: {rep.get('comps', '')}", f"Check: {rep.get('check', '')}",
                 f"Frames left out (flag: frame numbers): {rep.get('left_out', 'none')}",
                 f"Frames trimmed: {self._trimmed_frames(meta, r) or 'none'}",
                 "Detrend: " + (", ".join(r.get("detrend") or []) or "none"),
                 f"Calibration: {rep.get('calibration', '')}", rep.get("gain") or "Gain: not recorded"]
        wf = r.get("with_flagged")
        if wf:
            lines.append(f"Flagged frames put back: {wf['n']} {wf['label']} (error bars x{wf['factor']:g})")
        return lines

    @staticmethod
    def _trimmed_frames(meta: dict, r: dict) -> str:
        trim = (r or {}).get("trim") or meta.get("trim")
        ft = meta.get("frame_t")
        if not trim or ft is None or not len(trim.get("t", [])):
            return ""
        tt, nums = ft
        cut = np.asarray(trim["t"], dtype=float)
        hit = [int(n) for tv, n in zip(tt, nums) if np.any(np.abs(cut - tv) < 1e-7)]
        return frame_ranges(hit) if hit else f"{len(cut)} frames"

    def settings_note(self, meta: dict, r: dict) -> str:
        """2.2.19 (#40): the compact one-line form for a report's #NOTES (ASCII)."""
        rep = meta.get("repro") or {}
        if not rep:
            return ""
        parts = [rep.get("aperture", "").replace(" (at processing bin", " (bin")]
        parts.append("comps " + rep.get("comps", "").split(" (Use comps choice")[0])
        parts.append("check " + rep.get("check", "none"))
        if rep.get("left_out") and rep["left_out"] != "none":
            parts.append("left out " + rep["left_out"])
        tr = self._trimmed_frames(meta, r)
        if tr:
            parts.append("trimmed " + tr)
        parts.append("detrend " + (", ".join(r.get("detrend") or []) or "none"))
        parts.append(rep.get("gain", ""))
        parts.append("cal " + rep.get("calibration", ""))
        return "SHOBS-P settings: " + "; ".join(p for p in parts if p)

    # ---- fit -------------------------------------------------------------------------------
    def fit(self, auto: bool = False, with_flagged: bool = False):
        if getattr(self, "_checking", False):
            # 2.2.16 (item 6, review): the comp check runs right after "Done"; a status line, not a modal box, and
            # before any question (round 2).
            if not auto:
                self.app.status.set("Still checking the comps (a few seconds); Fit transit again when it is done, "
                                    "or Cancel the check.")
            return
        # 2.2.12: a report's ephemeris is still being looked up: the fit starts by itself when it is in.
        if self.report is not None and getattr(self, "_eph_pending", None) is self.report:
            if not auto:
                messagebox.showinfo("SHOBS-P", "Still looking up this planet's ephemeris; the fit starts by itself "
                                               "as soon as it is in.")
            return
        if self.report is None:
            self._trim_source_check()   # review: a series change drops the trim
        planet = self.current_planet()
        if not planet:
            if not auto:
                messagebox.showinfo("SHOBS-P", "Enter the planet on the Input page first (name, then Lookup), or open "
                                               "an exoplanet report.")
            return
        try:
            self._data_extra = {}
            t, flux, err, am, meta = self._data()
            # 2.2.17 (item 5): trimmed spans are left out of everything below (kept in memory; Clear trim).
            self._time_map = self._time_map_for(t)
            t, flux, err, am, trim = self._apply_trim(t, flux, err, am)
            if trim:
                meta["trim"] = trim
        except Exception as exc:
            if auto:
                self._notes_text([("info", str(exc))])
            else:
                messagebox.showerror("SHOBS-P", str(exc))
            return
        planet = dict(planet, ld_band=meta.get("filter"))   # 2.2.17 (item 1): Sloan limb darkening for SG/SR/SI/SZ
        # 2.2.9: no predicted transit during these data -> say so instead of fitting (Tessa's 10-minute run was
        # fitted 27 h from any transit). "Fit anyway" stays possible.
        tf = np.asarray(t, dtype=float)
        tf = tf[np.isfinite(tf)]
        chk = tc.transit_window_check(float(tf.min()), float(tf.max()), planet) if tf.size else None
        if chk is not None and not chk["overlap"]:
            when = (f"{abs(chk['hours']):.1f} h {'after' if chk['hours'] > 0 else 'before'} the "
                    f"{'last' if chk['hours'] > 0 else 'first'} point (Tc {chk['tc']:.4f} BJD_TDB)")
            text = (f"No transit of {planet.get('name') or 'this planet'} is predicted during these data. "
                    f"The nearest predicted transit is {when}. A transit fit cannot measure anything here; if this "
                    "was a variability run, use Variables mode.")
            arrow = (f"Nearest predicted transit: {abs(chk['hours']):.1f} h after the last point →"
                     if chk["hours"] > 0 else
                     f"← Nearest predicted transit: {abs(chk['hours']):.1f} h before the first point")
            preview = (t, flux, arrow)
            if auto:
                self.result = None
                self._fill_table()
                self.draw(preview=preview)
                self._notes_text([("warn", text), ("info", "Fit transit fits it anyway.")])
                self.app.log("Transit fit: " + text)
                return
            if not messagebox.askyesno("SHOBS-P", text + "\n\nFit anyway?"):
                self.result = None
                self._fill_table()
                self.draw(preview=preview)
                self._notes_text([("warn", text)])
                return
        if auto:
            if self.app.busy:
                return
            self.app.log("Transit fit: starting by itself on the " + ("loaded report" if self.report is not None
                                                                        else "new photometry") + " (Cancel stops it).")
        if not self.app._start_job("Fitting the transit…"):
            return
        self._pending_signature = self._signature()
        steps = MCMC_CHOICES.get(self.mcmc.get(), 3000)
        da, dt = bool(self.detrend_airmass.get()), bool(self.detrend_time.get())
        # 2.2.13: the flip segment, and the flagged frames (left out and listed, or put back with doubled errors).
        extra = dict(self._data_extra or {})
        after = extra.get("after_flip")
        fl = extra.get("flagged")
        flagged_mask, excluded, label = None, None, "flagged"
        if fl is not None and len(fl["t"]):
            label = fl.get("label") or "flagged"
            if with_flagged:
                n0 = len(t)
                t = np.concatenate([np.asarray(t, dtype=float), fl["t"]])
                flux = np.concatenate([np.asarray(flux, dtype=float), fl["flux"]])
                err = np.concatenate([np.asarray(err, dtype=float), fl["err"]])
                am = np.concatenate([np.asarray(am, dtype=float), fl["airmass"]])
                if after is not None or fl["after_flip"].any():
                    base_after = after if after is not None else np.zeros(n0, dtype=bool)
                    after = np.concatenate([base_after, fl["after_flip"]])
                flagged_mask = np.r_[np.zeros(n0, dtype=bool), np.ones(len(fl["t"]), dtype=bool)]
            else:
                excluded = {"t": fl["t"], "jd": fl["jd"], "label": label}
        if after is not None and not (np.any(after) and not np.all(after)):
            after = None
        # 2.2.16 (item 6): the same data and settings for the leave-one-comp-out check after the fit (photometry
        # with 2+ comps only; a report has no per-comp data).
        self._loo_pending = None
        cmags = extra.get("comp_mags")
        if self.report is None and cmags is not None and np.ndim(cmags) == 2 and np.shape(cmags)[1] >= 2:
            if flagged_mask is not None:
                fc = (fl or {}).get("comp_mags")
                cmags = np.vstack([cmags, fc]) if fc is not None and np.shape(fc)[1:] == np.shape(cmags)[1:] else None
            if cmags is not None and len(cmags) == len(t):
                self._loo_pending = {"t": t, "flux": flux, "err": err, "am": am, "after": after,
                                     "flagged_mask": flagged_mask, "comp_mags": cmags,
                                     "names": list(extra.get("comp_names") or []), "planet": planet,
                                     "da": da, "dt": dt}
        flip_source = extra.get("flip_source", "")
        cancel = self.app._cancel
        self.bar.configure(value=0, maximum=1)
        self.prog_text.set("Least-squares fit…")
        self._fitting = True
        self._show_progress()
        app = self.app

        def progress(phase, done, total):
            app.call_ui(self._progress, phase, done, total)

        def work():
            try:
                result = tc.fit_transit(t, flux, err, am, planet, detrend_airmass=da, detrend_time=dt,
                                        mcmc_steps=steps, progress=progress, cancel=cancel,
                                        log=lambda m: app.call_ui(app.log, m), after_flip=after,
                                        flip_source=flip_source, flagged_mask=flagged_mask, flagged_label=label,
                                        excluded=excluded)
                app.call_ui(self._fit_done, result, meta, None)
            except Exception as exc:
                app.call_ui(self._fit_done, None, meta, (str(exc), traceback.format_exc()))

        threading.Thread(target=work, daemon=True).start()

    def _progress(self, phase, done, total):
        """2.2.12: the burn-in and the steps are counted separately ("MCMC burn-in 600 of 1,000", then
        "MCMC step 1,200 of 3,000"), so the second count matches the MCMC menu."""
        self.bar.configure(maximum=total, value=done)
        self.prog_text.set(f"MCMC {'burn-in' if phase == 'burn-in' else 'step'} {done:,} of {total:,}")

    def _clear_progress(self):
        """A new source: the last fit's message no longer applies (unless a fit is still running). 2.2.16 (item 6,
        review): a comp check still running belongs to the old fit: its progress and result lines stay off the page
        from now on (_loo_shown)."""
        if not self._fitting:
            self._loo_shown = False
            self.prog_text.set("")
            self._show_progress()

    def _show_progress(self):
        """2.2.10 (John): the bar only while a fit runs; the row only while it has something to say."""
        try:
            self.bar.pack_forget()
            self.prog_label.pack_forget()
            busy = self._fitting or getattr(self, "_checking", False)   # 2.2.16 (item 6): the comp check too
            if busy:
                self.bar.pack(side=LEFT)
            if busy or self.prog_text.get():
                self.prog_label.pack(side=LEFT, padx=8)
                self.prog_row.pack(fill=X, side="bottom", pady=(4, 0), before=self.results_row)
            else:
                self.prog_row.pack_forget()
        except Exception:
            pass

    def _fit_done(self, result, meta, error):
        self._fitting = False
        self.app._end_job()
        try:
            self._finish_fit(result, meta, error)
        finally:
            self._show_progress()
        # 2.2.16 (item 6): then the leave-one-comp-out check; it can never undo or fail the fit just shown.
        try:
            if error is None and result is not None and result is self.result:
                self._start_comp_check()
            else:
                self._loo_pending = None
        except Exception:
            self._loo_pending = None
        self._run_pending_refit()   # 2.2.17 (item 4): comps changed while this fit ran

    # ---- 2.2.16 (item 6): leave-one-comp-out check -----------------------------------------
    def _start_comp_check(self):
        """Refit once per comp with that comp left out (least squares, same priors and settings), in the
        background as the fit runs; the result adds cautions and the comp-choice spread to the notes and files."""
        job, self._loo_pending = getattr(self, "_loo_pending", None), None
        r = self.result
        if not job or r is None or self.report is not None or _state(r.get("verdict")) == "none":
            return
        if self.app.busy or not self.app._start_job("Checking the comps…"):
            return
        token = object()
        self._loo_token = token
        self._checking = True
        self._loo_shown = True
        self._loo_done_text = self.prog_text.get()
        m = int(np.shape(job["comp_mags"])[1])
        self.bar.configure(value=0, maximum=m)
        self.prog_text.set(f"Checking comps (1 of {m})…")
        self._show_progress()
        app = self.app
        cancel = app._cancel
        summary = dict(r["summary"])

        def work():
            try:
                loo = tc.leave_one_comp_out(job["t"], job["flux"], job["err"], job["am"], job["comp_mags"],
                                            job["names"], job["planet"], summary, detrend_airmass=job["da"],
                                            detrend_time=job["dt"], after_flip=job["after"],
                                            flagged_mask=job["flagged_mask"],
                                            progress=lambda j, n: app.call_ui(self._comp_progress, token, j, n),
                                            cancel=cancel, log=lambda msg: app.call_ui(app.log, msg))
                app.call_ui(self._comp_check_done, token, r, loo, None)
            except Exception as exc:
                app.call_ui(self._comp_check_done, token, r, None, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _comp_progress(self, token, done, total):
        if token is getattr(self, "_loo_token", None) and getattr(self, "_checking", False):
            self.bar.configure(maximum=total, value=done - 1)
            if getattr(self, "_loo_shown", True):
                self.prog_text.set(f"Checking comps ({done} of {total})…")

    def _comp_check_done(self, token, r, loo, error):
        if token is not getattr(self, "_loo_token", None):
            return
        self._checking = False
        self.app._end_job()
        done_text = getattr(self, "_loo_done_text", "")
        try:
            if (r is not self.result or self._signature() != self._fit_signature
                    or not getattr(self, "_loo_shown", True)):
                return   # the page shows another fit (or other data) now: its own text stays
            self.prog_text.set(done_text)
            if error is not None:
                self.app.log(("Comp check canceled." if error == "Canceled" else f"Comp check skipped: {error}"))
                return
            if not loo:
                return
            r["comp_loo"] = loo
            r["flags"] = list(r.get("flags") or []) + list(loo["flags"])
            self._notes_text(tc.cautions(r["flags"], r.get("verdict")))
            self.app.log("Transit fit: " + loo["note"])
            for level, text in loo["flags"]:
                if level == "warn":
                    self.app.log("Transit fit: " + text)
            flagged = [f["name"] for f in loo["fits"] if f.get("flagged")]
            if loo.get("unnamed"):
                flagged.append("comp choice")
            self.prog_text.set(done_text + ("   " if done_text else "") + f"Comps checked: depth spread "
                               f"{loo['depth_spread'] * 100:.2f} %, Tc spread {loo['tc_spread_s']:.0f} s"
                               + (f" (⚠ {', '.join(flagged)})" if flagged else ""))
        except Exception as exc:
            self.app.log(f"Comp check not shown: {exc}")
        finally:
            self._show_progress()
            try:
                # 2.2.16 (round-2 review): app.log sets the status line; end on a short summary instead.
                if error == "Canceled":
                    self.app.status.set("Transit fit done; comp check canceled.")
                elif loo and r is self.result:
                    warned = any(lvl == "warn" for lvl, _x in loo["flags"])
                    self.app.status.set("Transit fit done; comps checked"
                                        + (" — see the caution in the notes." if warned else "."))
                elif self.app.status.get() == "Checking the comps…":
                    self.app.status.set("Transit fit done.")
            except Exception:
                pass
            self._run_pending_refit()   # 2.2.17 (item 4): comps changed while the check ran

    def _finish_fit(self, result, meta, error):
        if error is not None:
            self.prog_text.set("Canceled." if error[0] == "Canceled" else "Fit failed.")
            self._show_progress()     # before any dialog, so the bar is not left showing behind it
            self.draw(placeholder="Not fitted yet: press Fit transit." if error[0] == "Canceled" else "Fit failed.")
            if error[0] != "Canceled":
                self.app.log(error[1])
                messagebox.showerror("SHOBS-P", f"Transit fit failed: {error[0]}")
            return
        if self._signature() != getattr(self, "_pending_signature", None):
            if self._refit_pending:
                # 2.2.17 review: comps changed while this fit ran; the automatic refit follows (_fit_done).
                self.prog_text.set("Comps changed during the fit: refitting with the new comps…")
                self.draw(placeholder="Refitting with the new comps…")
                return
            # 2.2.7: the data changed while this fit ran (a report opened or closed, another night picked): the
            # result belongs to other data, so it is not shown.
            self.prog_text.set("Fit finished for data no longer shown; not used.")
            self._notes_text([("info", "That fit was for data no longer on this page. Fit transit fits what is shown "
                                       "now.")])
            self.draw(placeholder="Not fitted yet: press Fit transit.")
            return
        if meta.get("comp_noise"):
            flags = result.setdefault("flags", [])
            flags.insert(1 if flags else 0, ("warn", meta["comp_noise"]))   # after the verdict line
            self.app.log("Comps: " + meta["comp_noise"])
        if self.report is not None and self.report.get("time_note"):
            # 2.2.17 review: the report's time system, converted or not, in the notes and fit file.
            tn = self.report["time_note"]
            result.setdefault("flags", []).append(("warn" if tn.startswith("⚠") else "info", tn.lstrip("⚠ ")))
        if self.report is None:
            # 2.2.19 (#41): what this night was measured with, and a caution when the Photometry boxes now differ.
            try:
                ap_text, changed = self.app.aperture_state_text(self.night.get())
            except Exception:
                ap_text, changed = "", False
            if ap_text:
                result.setdefault("flags", []).append(("warn" if changed else "info", ap_text.lstrip("⚠ ").rstrip(".") + "."))
        if meta.get("gain"):
            # 2.2.19 (#39): the gain and its source in the notes (page, PNG, fit text); a caution when assumed.
            result.setdefault("flags", []).append(("warn" if "assumed" in meta["gain"] else "info", meta["gain"] + "."))
        if meta.get("trim"):
            # 2.2.17 (item 5): the trimmed span in the notes (page, PNG, fit file) and on the plot.
            result["trim"] = meta["trim"]
            result.setdefault("flags", []).append(("info", meta["trim"]["text"]))
            self.app.log("Transit fit: " + meta["trim"]["text"])
        lines = self.settings_lines(meta, result)
        if lines:   # 2.2.19 (#40): the run's settings, in full, in the log (one line per fit)
            self.app.log("Transit fit settings: " + " | ".join(lines))
        result["_serial"] = next(_FIT_SERIAL)   # 2.2.17 review: names this fit for the numbered save set
        self.result = result
        self._fit_signature = getattr(self, "_pending_signature", None)
        self.meta_for_files = meta
        s = result["summary"]
        self.prog_text.set(f"Done: Tc {s['tc'][0]:.5f} ± {s['tc'][1]:.5f}, Rp/R* {s['k'][0]:.4f} ± {s['k'][1]:.4f}, "
                           f"MCMC acceptance {result['chain']['acceptance']:.2f}")
        self.app.log(f"Transit fit: Tc {s['tc'][0]:.6f} ± {s['tc'][1]:.6f} BJD_TDB, Rp/R* {s['k'][0]:.4f} ± "
                     f"{s['k'][1]:.4f}, depth {s['depth'][0] * 100:.2f}%, b {s['b'][0]:.2f}, beta {result['beta']:.2f}")
        self.app.status.set("Transit fit done.")
        self._fill_table()
        self._count_text()
        self._notes_text(tc.cautions(result["flags"], result.get("verdict")))
        self.draw()
        self._show_refit_flagged()
        self._update_trim_text()

    # ---- display ---------------------------------------------------------------------------
    def _set_columns(self):
        """The EXOTIC column only when a report is loaded."""
        cols = ["param", "shobs", "free", "pub"] + (["exotic"] if self.report is not None else [])
        self._display_cols = cols
        self.table.configure(displaycolumns=cols)
        try:
            self.table.heading("exotic", text=self._report_column(), anchor=W)   # 2.2.17 (item 7)
        except Exception:
            pass

    def _table_xview(self, first, last):
        try:
            self.table_xscroll.set(first, last)
            if float(first) > 0.0 or float(last) < 1.0:
                self.table_xscroll.grid()
            else:
                self.table_xscroll.grid_remove()
        except Exception:
            pass

    def _fit_strip(self, event):
        """Rows in the table: 8, fewer (down to 3) when the page is short, so the plot keeps at least ~300 px."""
        try:
            try:
                rowh = int(ttk.Style(self.table).lookup("Treeview", "rowheight") or 28)
            except Exception:
                rowh = 28
            scale = max(float(self.table.tk.call("tk", "scaling")) / 1.3333, 1.0)
            rowh = max(rowh, int(20 * scale))
            room = event.height - 300 * scale
            try:
                if self.table_xscroll.winfo_ismapped():
                    room -= int(self.table_xscroll.winfo_reqheight())
            except Exception:
                pass
            rows = max(3, min(8, int(room // rowh) - 1))
            if int(self.table.cget("height") or 0) != rows:
                self.table.configure(height=rows)
                self.notes.configure(height=rows)   # 2.2.11: the notes ask no more height than the table
        except Exception:
            pass

    def _fit_columns(self, rows=None):
        """2.2.10: each column as wide as its heading or its widest value, no more, so no column hogs the strip
        and no horizontal scroll bar is needed."""
        labels = {"param": "Parameter", "shobs": "SHOBS-P (MCMC)", "free": "Free shape", "pub": "Published / prior",
                  "exotic": self._report_column()}   # 2.2.17 (item 7)
        try:
            from tkinter import font as tkfont
            head = tkfont.Font(self.table, font=(core.UI_FONT, 10, "bold"))
            # The font the rows are drawn in (the app styles Treeview as Segoe UI 10).
            body = None
            try:
                spec = ttk.Style(self.table).lookup("Treeview", "font")
                if isinstance(spec, (str, tuple)) and spec:
                    body = tkfont.Font(self.table, font=spec)
            except Exception:
                body = None
            if body is None:
                try:
                    body = tkfont.nametofont("TkDefaultFont")
                except Exception:
                    body = tkfont.Font(self.table, font=(core.UI_FONT, 10))
            all_cols = ["param", "shobs", "free", "pub", "exotic"]
            if rows is None:
                rows = []
                for iid in self.table.get_children():
                    try:
                        rows.append([str(v) for v in (self.table.item(iid).get("values") or [])])
                    except Exception:
                        pass
            rows = [[str(v) for v in row] for row in rows]
            for k, col in enumerate(all_cols):
                widest = head.measure(labels[col]) + 24
                for row in rows:
                    if k < len(row):
                        widest = max(widest, body.measure(row[k]) + 24)
                # 2.2.11: content width is the minimum; the columns share any spare width of the strip.
                self.table.column(col, width=int(widest), minwidth=int(widest), stretch=True)
        except Exception:
            pass

    def _save_fig(self, target, stamp: bool = False, **kw):
        """2.2.10: save the plot at a fixed 8 × 5.4 in, whatever its size on screen (the full-width plot would make
        a thin strip of a PNG), then put the screen size back."""
        old = self.fig.get_size_inches().copy()
        mark = None
        try:
            self.fig.set_size_inches(8, 5.4, forward=False)
            self._set_margins()
            if stamp:
                # 2.2.12: the ExoFOP package's plot carries its DTG too.
                mark = self.fig.text(0.99, 0.005, f"Created {core.dtg()} · SHOBS-P "
                                                  f"{getattr(self.app, 'app_version', '')}", fontsize=6,
                                     color="#7a8a94", ha="right", va="bottom")
            self.fig.savefig(target, **kw)
        finally:
            if mark is not None:
                mark.remove()
            self.fig.set_size_inches(old, forward=False)
            self._set_margins()
            try:
                self.canvas.draw_idle()
            except Exception:
                pass

    def _set_margins(self):
        """Plot margins in points (labels, ticks, title), whatever the size of the plot."""
        if self.fig is None:
            return
        try:
            w, h = self.fig.get_size_inches() * 72.0
            if w < 100 or h < 100:
                return
            # 2.2.11: room for a title only when the plot has one.
            top = 24 if getattr(self, "_has_title", False) else 8
            left, right = min(64 / w, 0.3), 1 - min(10 / w, 0.1)
            bottom = min(38 / h, 0.3)
            leg_pt = self._place_fig_legend(w)   # 2.2.19 review: the legend's own room under the residual panel
            if leg_pt:
                bottom = min((42 + leg_pt) / h, 0.42)
            self.fig.subplots_adjust(left=left, right=right, top=1 - min(top / h, 0.2), bottom=bottom)
            if getattr(self, "_single_panel", False):
                # The light curve alone (no residuals): the flux panel takes the whole plot.
                self.ax.set_position([left, bottom, right - left, (1 - min(top / h, 0.2)) - bottom])
        except Exception:
            pass

    def _place_fig_legend(self, width_pt: float) -> float:
        """2.2.19 (nit, review): the fit plot's legend as a figure legend below the residual panel, in as many columns
        as the figure's width holds (measured from the labels' lengths), so it never covers data and never squeezes
        the light curve's y range. Returns its height in points (0 without one); the bottom margin makes that room."""
        items = getattr(self, "_legend_items", None)
        old = getattr(self, "_fig_legend", None)
        if old is not None:
            try:
                old.remove()
            except Exception:
                pass
            self._fig_legend = None
        if not items:
            return 0.0
        handles, labels = items
        size = 8 if width_pt >= 420 else 7

        def col_w(text):
            return len(text) * size * 0.5 + 28

        n = len(labels)
        ncol = 1
        for k in range(n, 0, -1):
            rows = -(-n // k)
            cols = [labels[c * rows:(c + 1) * rows] for c in range(k)]
            if sum(max((col_w(t) for t in c), default=0) for c in cols if c) <= width_pt - 20:
                ncol = k
                break
        # The estimate starts the search; the legend's measured size decides (fewer columns until it fits).
        try:
            renderer = self.fig.canvas.get_renderer()
        except Exception:
            renderer = None
        for k in range(ncol, 0, -1):
            leg = self.fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, 0.0), ncol=k,
                                  fontsize=size, frameon=False, borderaxespad=0.3, handlelength=1.6,
                                  columnspacing=1.2, labelspacing=0.3)
            self._fig_legend = leg
            if renderer is None:
                return -(-n // k) * size * 1.32 + 6
            try:
                box = leg.get_window_extent(renderer)
            except Exception:
                return -(-n // k) * size * 1.32 + 6
            if box.width <= width_pt / 72.0 * self.fig.dpi - 8 or k == 1:
                return box.height / self.fig.dpi * 72.0 + 6
            leg.remove()
            self._fig_legend = None
        return 0.0

    def _draw_preview(self, t, flux, note):
        """2.2.11 (John): no transit predicted during these data, so nothing is fitted; show the night's light curve
        (relative flux against hours from the first point) and where the nearest transit is."""
        ax, axr = self.ax, self.ax_r
        t = np.asarray(t, dtype=float)
        f = np.asarray(flux, dtype=float)
        ok = np.isfinite(t) & np.isfinite(f)
        t, f = t[ok], f[ok]
        self._single_panel = True
        axr.set_visible(False)
        ax.tick_params(axis="x", labelbottom=True)
        med = float(np.nanmedian(f)) if t.size else float("nan")
        if t.size and np.isfinite(med) and med != 0:
            f = f / med
            x = (t - t.min()) * 24
            ax.plot(x, f, ".", color="#9aa7b0", ms=4, label="each frame")
            cadence = float(np.median(np.diff(np.sort(t)))) * 24 if t.size > 1 else 0.0
            bins = _bin(x, f, max(10.0 / 60, 3 * cadence))
            if bins:
                ax.errorbar([b[0] for b in bins], [b[1] for b in bins], [b[2] for b in bins], fmt="o", color="k",
                            ms=4, capsize=2, label="10-minute bins")
            lo, hi = np.percentile(f, 1), np.percentile(f, 99)
            pad = max((hi - lo) * 0.15, 0.002)
            ax.set_ylim(lo - pad, hi + pad)
            ax.legend(fontsize=8, loc="lower left", frameon=False)
            try:
                start = tc.jd_to_local(float(t.min())).strftime("%H:%M")
                ax.set_xlabel(f"Hours from the first point ({start} local)")
            except Exception:
                ax.set_xlabel("Hours from the first point")
        else:
            ax.set_xlabel("Hours from the first point")
        ax.set_ylabel("Relative flux")
        ax.grid(alpha=0.3)
        ax.text(0.99, 0.97, note, transform=ax.transAxes, ha="right", va="top", fontsize=10, color="#b35c00")
        self._set_margins()
        self.canvas.draw_idle()

    def _show_verdict(self):
        v = (self.result or {}).get("verdict")
        self.quality.set(quality_text(self.result) if v else "")
        if not v:
            self.verdict.set("")
            return
        state = _state(v)
        # 2.2.13: a fit with flagged frames put back says so first.
        pre = ("[" + v["includes_flagged"] + "] ") if v.get("includes_flagged") else ""
        if state == "detected" and v.get("depth_unreliable"):
            # 2.2.7: a real dip with good timing, but a depth the night cannot be trusted for.
            self.verdict.set("✓ " + pre + v["reason"] + " Timing usable; depth unreliable: " + v["depth_unreliable"] + ".")
            self.verdict_label.configure(foreground="#c26a00")
        elif state == "detected":
            self.verdict.set("✓ " + pre + v["reason"])
            self.verdict_label.configure(foreground="#2e7d32")
        elif state == "inconclusive":
            tail = (" Neither Tc nor the depth is measured." if v.get("flip_inconclusive") else
                    " Tc may be usable; the depth is not.")
            self.verdict.set("⚠ " + pre + v["reason"] + tail)
            self.verdict_label.configure(foreground="#c26a00")
        else:
            self.verdict.set("✗ " + pre + "No transit measured. " + v["reason"] + " The numbers below are for reference "
                             "only.")
            self.verdict_label.configure(foreground="#b00020")

    def _confirm_export(self) -> bool:
        v = (self.result or {}).get("verdict")
        state = _state(v) if v else "detected"
        if state == "none":
            return messagebox.askyesno(
                "SHOBS-P", "This fit did not measure a transit:\n\n" + v["reason"] + "\n\nA report from it would "
                "carry numbers that mean nothing. Save it anyway (for your records, not for submission)?")
        if state == "inconclusive":
            caveat = ("Neither Tc nor the depth is measured (the flip step trades off against both)."
                      if v.get("flip_inconclusive") else "Tc may be worth reporting with that caveat; the depth is not.")
            return messagebox.askyesno(
                "SHOBS-P", "This fit is inconclusive:\n\n" + v["reason"] + "\n\nThe report will say so in its notes. "
                + caveat + " Save it?")
        return True

    def _fill_table(self):
        self._set_columns()
        self._show_verdict()
        self.table.delete(*self.table.get_children())
        r = self.result
        p = self.current_planet() or {}
        ex = (self.report or {}).get("results") or {}
        s = (r or {}).get("summary") or {}
        ff = ((r or {}).get("free_fit") or {}).get("p") or {}
        # 2.2.19 (nit): a free shape that ran away shows "not constrained", not its numbers.
        free_off = bool(((r or {}).get("free_fit") or {}).get("unconstrained"))
        if free_off:
            ff = {}

        def val(name, fmt, scale=1.0):
            if name not in s:
                return ""
            return f"{fmt.format(s[name][0] * scale)} ± {fmt.format(s[name][1] * scale)}"

        def exo(key, fmt, scale=1.0):
            v = ex.get(key)
            if not v or v[0] is None:
                return ""
            return fmt.format(v[0] * scale) + (f" ± {fmt.format(v[1] * scale)}" if v[1] else "")

        def num(v, fmt, scale=1.0):
            return fmt.format(v * scale) if isinstance(v, (int, float)) and math.isfinite(v) else ""

        rows = [
            ("Tc (BJD_TDB)", val("tc", "{:.5f}"), num(ff.get("tc"), "{:.5f}"),
             num((r or {}).get("tc_pred"), "{:.5f}")
             # 2.2.19 (#29): the predicted Tc with its propagated ephemeris uncertainty
             + ((f" ± {r['tc_pred_err']:.5f}" if r["tc_pred_err"] >= 5e-5 else f" ± {r['tc_pred_err']:.1e}")
                if r and r.get("tc_pred") is not None and r.get("tc_pred_err") is not None else ""), exo("Tc", "{:.5f}")),
            ("Rp/R*", val("k", "{:.4f}"), "not constrained" if free_off else num(ff.get("k"), "{:.4f}"),
             num(p.get("k"), "{:.4f}"), exo("Rp/R*", "{:.4f}")),
            # 2.2.13: the published depth is (Rp/R*)² when Rp/R* is known, the same value the checks compare with.
            ("Depth (%)", val("depth", "{:.2f}", 100),
             "not constrained" if free_off else num(ff.get("k", float("nan")) ** 2, "{:.2f}", 100),
             num(tc.published_depth(p)[0], "{:.2f}", 100), exo("Radius-ratio area depth (Rp/R*)^2", "{:.2f}")),
            ("a/R*", val("a_rs", "{:.2f}"), num(ff.get("a_rs"), "{:.2f}"), num(p.get("a_rs"), "{:.2f}"), exo("a/R*", "{:.2f}")),
            ("Impact parameter b", val("b", "{:.3f}"), num(ff.get("b"), "{:.3f}"), num(p.get("b"), "{:.3f}"),
             exo("Impact Parameter (b)", "{:.3f}")),
            ("Inclination (°)", val("inc", "{:.2f}"), "", num(p.get("inc"), "{:.2f}"), exo("inc", "{:.2f}")),
            ("Duration T14 (h)", val("t14", "{:.3f}", 24), num((r or {}).get("free_fit", {}).get("t14") if r and r.get("free_fit") and not free_off else None, "{:.3f}", 24),
             num(p.get("t14"), "{:.3f}", 24), exo("Duration", "{:.3f}", 24)),
            ("Limb darkening u1", val("u1", "{:.2f}"), "", num((r or {}).get("ld_prior", [None])[0] if r else None, "{:.2f}"), ""),
            ("Limb darkening u2", val("u2", "{:.2f}"), "", num((r or {}).get("ld_prior", [None, None])[1] if r else None, "{:.2f}"), ""),
            ("Airmass slope", val("c_am", "{:.4f}"), "", "", ""),
        ]
        if r:
            # 2.2.19 (#29): O − C in minutes and in sigma (fit and ephemeris sigma in quadrature).
            o = tc.oc_summary(r)
            rows.insert(1, ("O − C (min)", (f"{o['oc_min']:+.2f} ± {o['sigma_min']:.2f} ({o['nsigma']:+.1f}σ)"
                                            if math.isfinite(o["nsigma"]) else f"{o['oc_min']:+.2f}") if o else "", "",
                            f"0 ± {o['pred_err_min']:.2f} (ephemeris)" if o else "0", ""))
            flip = r.get("flip")
            if flip:
                # 2.2.13: the flip step, the depth without it, and how much the two trade off.
                ns = flip.get("nostep") or {}
                c = flip.get("corr")
                step_txt = (f"{s['step'][0] * 100:+.2f} ± {s['step'][1] * 100:.2f}" if "step" in s else "")
                rows += [("Flip step (%)", step_txt, num(ff.get("step"), "{:+.2f}", 100), "", ""),
                         ("Depth without step (%)", (f"{ns['depth'] * 100:.2f} ± {ns['depth_err'] * 100:.2f} (LSQ)"
                                                     if ns else ""), "", "", ""),
                         ("Step–depth correlation", f"{c:+.2f}" if c is not None and math.isfinite(c) else "", "", "",
                          "")]
            rows += [("rms per point (%)", f"{r['rms'] * 100:.3f}", "", "", ""),
                     ("rms per 10-min bin (%)", num(r.get("rms_bin10"), "{:.3f}", 100), "", "", ""),
                     ("Red-noise beta", f"{r['beta']:.2f}", "", "", ""),
                     ("Reduced χ² (raw errors)", f"{r['rchi2']:.2f}", "", "", ""),
                     ("Points", str(r["n_points"]), "", "", str(len(self.report["t"])) if self.report else "")]
        warn_names = set()
        if r and r.get("flip") and not r["flip"].get("depth_ok", True):
            warn_names.update({"Depth (%)", "Rp/R*", "Flip step (%)", "Step–depth correlation"})
        for _lvl, text in (r or {}).get("flags", []):
            for key, label in (("a/R* =", "a/R*"), ("b =", "Impact parameter b"), ("Grazing", "Rp/R*"),
                               ("shape left free", "Rp/R*")):
                if key in text:
                    warn_names.add(label)
        undetected = bool(r) and _state(r.get("verdict")) == "none"
        for row in rows:
            if undetected:
                tags = ("graywarn",) if row[0] in warn_names else ("gray",)
            else:
                tags = ("warn",) if row[0] in warn_names else ()
            self.table.insert("", END, values=row, tags=tags)
        self._fit_columns(rows)
        self._show_refit_flagged()
        self._count_text()   # 2.2.14 (GitHub #7): the label follows every change of the shown fit

    def _notes_text(self, flags):
        self.notes.configure(state="normal")
        self.notes.delete("1.0", END)
        for level, text in flags:
            if level == "warn" and self._comp_link_wanted(text):
                # 2.2.17 (item 4): a comp-choice caution links straight to Comp health.
                self.notes.insert(END, "⚠ " + text + " ", level)
                self.notes.insert(END, COMP_LINK, (level, "link"))
                self.notes.insert(END, "\n\n", level)
                continue
            self.notes.insert(END, ("⚠ " if level == "warn" else "• ") + text + "\n\n", level)
        self.notes.configure(state="disabled")
        self._show_notes(bool(flags))

    def _show_notes(self, show: bool):
        """2.2.11: with notes, table two thirds and notes one third; without, the table takes the whole strip."""
        try:
            if show:
                self.notes.grid()
                self.strip.columnconfigure(0, weight=2, uniform="strip")
                self.strip.columnconfigure(1, weight=1, uniform="strip")
            else:
                self.notes.grid_remove()
                self.strip.columnconfigure(0, weight=1, uniform="")
                self.strip.columnconfigure(1, weight=0, uniform="")
            self._notes_shown = show
        except Exception:
            pass

    def _show_heads(self):
        """The banner and the verdict take a line only when they say something."""
        try:
            self.banner_label.pack_forget()
            self.verdict_label.pack_forget()
            self.quality_label.pack_forget()
            if not (self.banner.get() or self.verdict.get() or self.quality.get()):
                # An emptied frame keeps its old height in Tk, so the frame itself goes too.
                self.heads.pack_forget()
                return
            if getattr(self, "body", None) is not None:
                self.heads.pack(fill=X, before=self.body)
            if self.banner.get():
                self.banner_label.pack(anchor=W, fill=X, pady=(4, 0))
            if self.verdict.get():
                self.verdict_label.pack(anchor=W, fill=X, pady=(4, 0))
            if self.quality.get():
                self.quality_label.pack(anchor=W, fill=X, pady=(2, 0))
        except Exception:
            pass

    def draw(self, placeholder: str = "", preview=None):
        """The fit; or, with no fit, a labeled empty frame with the placeholder; or (2.2.11) preview=(t, flux,
        note): the night's light curve alone, when no transit is predicted during it."""
        if self.fig is None:
            return
        r = self.result
        ax, axr = self.ax, self.ax_r
        ax.clear()
        axr.clear()
        self._make_span_selector(None)   # 2.2.17 (item 5): a drag means something only on a fitted plot
        self._has_title = bool(r)
        self._single_panel = False
        self._legend_items = None   # 2.2.19 (nit): the fit plot's figure legend (_place_fig_legend)
        axr.set_visible(True)
        ax.tick_params(axis="x", labelbottom=False)
        if not r and preview is not None:
            self._draw_preview(*preview)
            return
        if not r:
            # 2.2.9 (John): an empty frame labeled like the fitted plot, no overlapping default ticks.
            ax.set_ylabel("Relative flux")
            axr.set_ylabel("Resid. %")
            axr.set_xlabel("Hours from fitted mid-transit")
            for a_ in (ax, axr):
                a_.set_xticks([])
                a_.set_yticks([])
                a_.grid(False)
            ax.text(0.5, 0.5, placeholder or "No transit fit yet", transform=ax.transAxes, ha="center", va="center",
                    fontsize=12, color="#5d6d76", wrap=True)
            self._set_margins()
            self.canvas.draw_idle()
            return
        best = r["best"]
        t = r["t"]
        x = (t - best["tc"]) * 24
        y = r["detrended"]
        fmask = np.asarray(r.get("flagged")) if r.get("flagged") is not None else np.zeros(len(t), dtype=bool)
        if fmask.shape != x.shape:
            fmask = np.zeros(len(t), dtype=bool)
        ax.plot(x[~fmask], y[~fmask], ".", color="#9aa7b0", ms=4, label="each frame, detrended")
        if fmask.any():
            # 2.2.13: flagged frames put back in the fit, drawn apart.
            wf = r.get("with_flagged") or {}
            ax.plot(x[fmask], y[fmask], "o", ms=4, mfc="none", mec="#d08a2e", mew=0.8,
                    label=f"flagged {wf.get('label', '')} (errors ×{wf.get('factor', 2):g})".replace("  ", " "))
        bins = _bin(x, y, max(10.0 / 60, 3 * r["cadence_days"] * 24))
        if bins:
            ax.errorbar([b[0] for b in bins], [b[1] for b in bins], [b[2] for b in bins], fmt="o", color="k", ms=4,
                        capsize=2, label="10-minute bins")
        bands = r.get("bands") if self.show_bands.get() else None
        if bands:
            # 2.2.19 (#31): 68 % and 95 % envelopes of transit × baseline over posterior draws (the baseline's spread
            # out of transit), and the baseline/detrend line's own spread in the residual panel.
            try:
                xb = (np.asarray(bands["t"]) - best["tc"]) * 24
                ax.fill_between(xb, *bands["m95"], color=GREEN, alpha=0.12, lw=0, zorder=1)
                ax.fill_between(xb, *bands["m68"], color=GREEN, alpha=0.28, lw=0, zorder=1,
                                label="68/95 % credible bands")
                axr.fill_between(xb, *(np.asarray(bands["b95"]) * 100), color=GREEN, alpha=0.12, lw=0, zorder=1)
                axr.fill_between(xb, *(np.asarray(bands["b68"]) * 100), color=GREEN, alpha=0.28, lw=0, zorder=1)
            except Exception:
                pass
        tt = np.linspace(t.min(), t.max(), 1200)
        u1, u2 = tc.kipping_to_u(best["q1"], best["q2"])
        model = tc.occultation(tc.separation(tt, best["tc"], r["period"], best["a_rs"], best["b"]), best["k"], u1, u2)
        s = r["summary"]
        ax.plot((tt - best["tc"]) * 24, model, color=GREEN, lw=2,
                label=f"SHOBS-P: Rp/R* {s['k'][0]:.3f} ± {s['k'][1]:.3f}, depth {s['depth'][0] * 100:.2f}%")
        p = r["planet"]
        if p.get("k") and p.get("a_rs") and p.get("b") is not None:
            pub = tc.occultation(tc.separation(tt, best["tc"], r["period"], p["a_rs"], min(p["b"], p["a_rs"] * 0.99)),
                                 p["k"], u1, u2)
            ax.plot((tt - best["tc"]) * 24, pub, ":", color="#1f77b4", lw=1.3,
                    label=f"published shape: Rp/R* {p['k']:.3f}, b {p['b']:.2f}")
        ex = (self.report or {}).get("results") or {}
        if ex.get("Rp/R*") and ex.get("Tc") and ex["Rp/R*"][0]:
            a_ex = (ex.get("a/R*") or (best["a_rs"], 0))[0] or best["a_rs"]
            inc = (ex.get("inc") or (None, 0))[0]
            b_ex = a_ex * math.cos(math.radians(inc)) if inc else best["b"]
            exm = tc.occultation(tc.separation(tt, ex["Tc"][0], r["period"], a_ex, min(b_ex, a_ex * 0.99)),
                                 ex["Rp/R*"][0], u1, u2)
            ax.plot((tt - best["tc"]) * 24, exm, "--", color=PINK, lw=1.3,
                    label=f"{report_software((self.report or {}).get('header')) or 'Report'}: "
                          f"Rp/R* {ex['Rp/R*'][0]:.3f}, b {b_ex:.2f}")   # 2.2.17 (item 7)
        if r.get("tc_pred") is not None and r.get("tc_pred_err") is not None:
            xp = (r["tc_pred"] - best["tc"]) * 24
            ax.axvline(xp, color="#888", ls="--", lw=0.8)
            ax.axvspan(xp - r["tc_pred_err"] * 24, xp + r["tc_pred_err"] * 24, color="#888", alpha=0.15)
        t14 = s["t14"][0] * 24
        for edge in (-t14 / 2, t14 / 2):
            ax.axvline(edge, color=GREEN, lw=0.6, alpha=0.6)
        flip = r.get("flip")
        if flip:
            # 2.2.13: where the flip step starts (the detrended points have it divided out).
            xf = (flip["t_flip"] - best["tc"]) * 24
            for a_ in (ax, axr):
                a_.axvline(xf, color="#7b1fa2", ls="-.", lw=1.0)
            ax.text(xf, 0.99, f" flip: step {flip['step'][0] * 100:+.1f} ± {flip['step'][1] * 100:.1f}%",
                    transform=ax.get_xaxis_transform(), ha="left", va="top", fontsize=8, color="#7b1fa2")
        trim_x = self._draw_trim(r, best, x)   # 2.2.17 (item 5)
        ax.set_ylabel("Relative flux")
        ax.grid(alpha=0.3)
        # 2.2.19 (nit): the legend sat on the pre-ingress points (lower left); it is now a figure legend under the
        # residual panel (_place_fig_legend, laid out by _set_margins), on screen and in the PNG.
        self._legend_items = ax.get_legend_handles_labels()
        name = p.get("name") or "Transit"
        state = _state(r.get("verdict"))
        title = {"detected": f"{name}   Tc {s['tc'][0]:.5f} ± {s['tc'][1]:.5f} BJD_TDB",
                 "inconclusive": f"{name}   INCONCLUSIVE   Tc {s['tc'][0]:.5f} ± {s['tc'][1]:.5f}",
                 "none": f"{name}   NO TRANSIT MEASURED (model shown for reference)"}[state]
        if r.get("with_flagged"):
            title += f"   incl. {r['with_flagged']['n']} flagged"
        ax.set_title(title + f"   ({self.meta_for_files.get('source', '')})", fontsize=10,
                     color={"detected": "black", "inconclusive": "#c26a00", "none": "#b00020"}[state])
        lo = min(np.percentile(y, 1), model.min()) - 0.004
        hi = max(np.percentile(y, 99), 1.0) + 0.004
        ax.set_ylim(lo, hi)
        axr.plot(x, r["residuals"] / r["baseline"] * 100, ".", color="#9aa7b0", ms=3)
        rb = _bin(x, r["residuals"] / r["baseline"] * 100, max(10.0 / 60, 3 * r["cadence_days"] * 24))
        if rb:
            axr.plot([b[0] for b in rb], [b[1] for b in rb], "o", color="k", ms=3)
        axr.axhline(0, color="k", lw=0.8)
        axr.set_ylabel("Resid. %")
        axr.set_xlabel("Hours from fitted mid-transit")
        axr.grid(alpha=0.3)
        # 2.2.9: the x range is the data plus the fitted transit, never stretched to a predicted Tc far away.
        xd_lo, xd_hi = float(np.nanmin(x)), float(np.nanmax(x))
        if trim_x:
            # 2.2.17 (item 5): the grayed trimmed spans stay in view.
            xd_lo, xd_hi = min(xd_lo, trim_x[0]), max(xd_hi, trim_x[1])
        if t14 / 2 >= xd_lo and -t14 / 2 <= xd_hi:
            x_lo, x_hi = min(xd_lo, -t14 / 2) - 0.25, max(xd_hi, t14 / 2) + 0.25
        else:
            # "Fit anyway" pulled to a Tc away from the data: show the data, not the empty hours between.
            x_lo, x_hi = xd_lo - 0.25, xd_hi + 0.25
        ax.set_xlim(x_lo, x_hi)
        for label in ax.get_xticklabels():
            label.set_visible(False)
        self._make_span_selector(best["tc"])   # 2.2.17 (item 5): drag across the plot to fill the trim boxes
        self._set_margins()
        self.canvas.draw_idle()

    def _report_time_system(self, rep):
        """2.2.17 review: the fit, Tc and the trim's clock times assume BJD_TDB. A report in HJD_UTC is moved to BJD_TDB
        (TDB − UTC; the helio/bary difference is a few seconds); one in JD_UTC is converted when the star's position
        is known, else it stays as it is and the banner and notes say its times are JD_UTC. rep["time_note"] says
        what was done; rep["date_type"] is what the times now are."""
        dt = str((rep.get("header") or {}).get("DATE_TYPE", "")).strip().upper().replace(" ", "_")
        rep["date_type"], rep["time_note"] = "BJD_TDB", ""
        if not dt or dt.startswith("BJD"):
            return
        t = np.asarray(rep["t"], dtype=float)
        if dt.startswith("HJD"):
            rep["t"] = t + 69.184 / 86400.0
            _shift_report_times(rep, t)   # round 2: the report's own Tc and ephemeris move with the points
            rep["time_note"] = ("The report's times are HJD_UTC; SHOBS-P moved them to BJD_TDB (+69.184 s; good to a "
                                "few seconds).")
            return
        if dt.startswith("JD"):
            radec = self._report_radec(rep)
            if radec is not None:
                h = rep.get("header") or {}
                lat, lon = _float_or_none(h.get("OBSLAT")), _float_or_none(h.get("OBSLON"))
                converted = None
                if lat is not None and lon is not None:     # round 2 nit: the site only when the header gives it
                    try:
                        converted = core.convert_times(t, "BJD_TDB", radec[0], radec[1], lat, lon)
                    except Exception:
                        converted = None
                if converted is None:
                    converted = t + np.asarray(tc.bjd_minus_jd_days(t, radec[0], radec[1]), dtype=float)
                rep["t"] = np.asarray(converted, dtype=float)
                _shift_report_times(rep, t)   # round 2: the report's own Tc and ephemeris too
                rep["time_note"] = "The report's times are JD_UTC; SHOBS-P converted them to BJD_TDB."
            else:
                rep["date_type"] = "JD_UTC"
                rep["time_note"] = ("⚠ The report's times are JD_UTC, not BJD_TDB, and the star's position is not known "
                                    "(enter the star on the Input page and open the report again): Tc is in JD_UTC, up "
                                    "to ±8 min from BJD_TDB, with no usable O − C.")
            return
        rep["time_note"] = (f"⚠ The report's times are {dt}, which SHOBS-P does not convert; it fits them as BJD_TDB, so "
                            "Tc may be off.")

    # ---- 2.2.17 (item 4): Comp health and Use comps from the Transit fit page ---------------
    def _comp_windows_ok(self) -> str:
        """'' when Comp health / Use comps can open here, else why not (a report shown, or no photometry)."""
        if self.report is not None and self.app.observations:
            return ("Comp health and Use comps are for this session's photometry: choose its night above (or Close "
                    "report).")
        if self.report is not None or not self.app.observations:
            return "Comp health needs this session's photometry."
        return ""

    def _update_comp_buttons(self):
        why = self._comp_windows_ok()
        for button in (self.comp_health_button, self.use_comps_button):
            try:
                button.configure(state="disabled" if why else "normal")
            except Exception:
                pass
        self.comp_hint.set(why)

    def open_comp_health(self):
        why = self._comp_windows_ok()
        if why:
            self.app.status.set(why)
            return
        self.app.show_comp_health()

    def open_use_comps(self):
        why = self._comp_windows_ok()
        if why:
            self.app.status.set(why)
            return
        self.app.choose_comps()

    def _comp_link_wanted(self, text: str) -> bool:
        """A comp-choice caution (the comp-noise warning, the leave-one-comp-out cautions) gets a Comp health link
        when Comp health can open (this session's photometry is shown)."""
        return str(text).startswith(COMP_CAUTION_PREFIXES) and not self._comp_windows_ok()

    def comps_changed(self):
        """Called by the App after Use comps applied a new comp set in Transits mode: the shown fit is stale; refit
        now when this page is on screen (else when it is next shown), or once the fit or comp check running now
        ends (its result is not shown: _finish_fit and _comp_check_done see the changed signature)."""
        if self.report is not None:
            return       # the page shows a report; the photometry's fit is redone when its night is chosen again
        self._auto_tried = None      # the same comps as an earlier automatic fit still refit
        comps = ", ".join(self.app.active_comps or self.app.comps_used)
        if self._fitting or getattr(self, "_checking", False):
            self._refit_pending = True
            self._check_stale(auto=False)
            # Review: one consistent message: the refit is automatic.
            what = "fit" if self._fitting else "comp check"
            self._notes_text([("info", f"Comps changed (now {comps}): the transit is refitted with them as soon as "
                                       f"the running {what} ends.")])
            self.app.log(f"Transit fit: comps changed; refitting when the running {what} ends.")
            self.app.status.set(f"Comps in use: {comps}. The transit refits when the running {what} ends.")
            return
        on_screen = getattr(self.app, "transit_page_on_screen", lambda: True)()
        self._check_stale(auto=on_screen)
        if self._fitting:
            self._notes_text([("info", f"Comps changed (now {comps}): refitting with the same settings…")])
            self.app.status.set(f"Comps in use: {comps}. Refitting the transit…")

    def _run_pending_refit(self):
        if not self._refit_pending or self._fitting or getattr(self, "_checking", False):
            return      # our own fit or check calls this again when it ends
        if self.report is not None or not getattr(self.app, "transit_page_on_screen", lambda: True)():
            self._refit_pending = False   # review: refitted when the page is next shown (refresh), never unannounced
            return
        if self.app.busy:
            # Review: another job holds the app; look again shortly instead of leaving the flag set for ever.
            self.app.after(1000, self._run_pending_refit)
            return
        self._refit_pending = False
        if self.report is None and getattr(self.app, "transit_page_on_screen", lambda: True)():
            self._auto_tried = None
            self._check_stale(auto=True)

    # ---- 2.2.17 (item 5): trim -------------------------------------------------------------
    def _report_radec(self, report=None):
        """The report's target RA/Dec (degrees) when the Input page or the planet lookup is for the same star."""
        rp = ((report if report is not None else self.report) or {}).get("planet") or {}
        names = {_norm(rp.get("name")), _norm(rp.get("host"))} - {""}
        app = self.app
        mine = {_norm(app.planet_name.get()), _norm(app.star_id.get())} - {""}
        if names & mine:
            try:
                return app.target_radec_deg()
            except Exception:
                pass
        info = getattr(app, "planet_info", None) or {}
        if names & ({_norm(info.get("name")), _norm(info.get("host"))} - {""}) and info.get("ra") is not None \
                and info.get("dec") is not None:
            return float(info["ra"]), float(info["dec"])
        return None

    def _time_map_for(self, t):
        """(BJD_TDB sorted, BJD_TDB − JD_UTC there, exact) for the clock times of the trim. Photometry: from each
        frame's JD and BJD. A report has BJD only: BJD − JD from the target's position when known (a few seconds),
        else TDB − UTC alone (local times then good to about ±8 minutes)."""
        t = np.asarray(t, dtype=float)
        ok = np.isfinite(t)
        jd = (self._data_extra or {}).get("jd") if self.report is None else None
        if jd is not None and len(jd) == len(t):
            off = t - np.asarray(jd, dtype=float)
            exact = True
        elif (self.report or {}).get("date_type") == "JD_UTC":
            off = np.zeros(t.shape)      # 2.2.17 review: the times are JD_UTC already
            exact = True
        else:
            radec = self._report_radec()
            if radec is not None:
                off = np.asarray(tc.bjd_minus_jd_days(t - 69.184 / 86400.0, radec[0], radec[1]), dtype=float)
                exact = True
            else:
                off = np.full(t.shape, 69.184 / 86400.0)
                exact = False
        ok &= np.isfinite(off)
        order = np.argsort(t[ok])
        return t[ok][order], off[ok][order], exact

    def _bjd_to_clock(self, bjd: float, tmap=None) -> str:
        tmap = tmap or self._time_map
        off = float(np.interp(bjd, tmap[0], tmap[1])) if tmap is not None and len(tmap[0]) else 69.184 / 86400.0
        return tc.jd_to_local(bjd - off + 30.0 / 86400.0).strftime("%H:%M")   # nearest minute (review)

    def _clock_to_bjd(self, text: str, tmap) -> float | None:
        """A trim box: local clock time (as parse_clock reads it) or a BJD_TDB number -> BJD_TDB; None when empty.
        A clock time is taken on the date that puts it nearest the middle of the data, so 00:42 on a night that
        started at 20:41 is the next morning's 00:42."""
        import datetime as _dt
        raw = (text or "").strip()
        if not raw:
            return None
        if re.fullmatch(r"24\d{5}(\.\d*)?", raw):
            return float(raw)
        if re.fullmatch(r"[4-9]\d{4}\.\d*", raw):
            return float(raw) + 2400000.0     # review: a reduced JD (61329.699)
        h, m = parse_clock(raw)
        if tmap is None or not len(tmap[0]):
            raise ValueError("no data to trim")
        ts, off = tmap[0], tmap[1]
        jd_mid = float(np.median(ts) - np.median(off))
        mid = tc.jd_to_local(jd_mid)
        best = None
        for d in (-1, 0, 1):
            local = _dt.datetime.combine(mid.date() + _dt.timedelta(days=d), _dt.time(h, m)).astimezone()
            jd = local.timestamp() / 86400.0 + 2440587.5
            if best is None or abs(jd - jd_mid) < abs(best - jd_mid):
                best = jd
        return best + float(np.interp(best, ts - off, off))

    def _trim_mask(self, t, trims=None):
        """True for the points kept (outside every trimmed span)."""
        t = np.asarray(t, dtype=float)
        keep = np.ones(t.shape, dtype=bool)
        for lo, hi in (self._trims if trims is None else trims):
            keep &= ~((t >= lo) & (t <= hi))
        return keep

    def _apply_trim(self, t, flux, err, am):
        """Leave the trimmed points out of the arrays (and the flip flags, comp matrix and flagged frames that go with
        them). Returns (t, flux, err, am, trim info or None)."""
        if not self._trims:
            return t, flux, err, am, None
        t = np.asarray(t, dtype=float)
        keep = self._trim_mask(t)
        if keep.all():
            return t, flux, err, am, None
        if int(keep.sum()) < 20:
            raise ValueError(f"The trim leaves {int(keep.sum())} points; a transit fit needs at least 20. Clear trim "
                             "and trim less.")
        flux, err, am = np.asarray(flux, dtype=float), np.asarray(err, dtype=float), np.asarray(am, dtype=float)
        ex = self._data_extra if self._data_extra is not None else {}
        n = len(t)
        for key in ("after_flip", "comp_mags", "jd"):
            v = ex.get(key)
            if v is not None and len(v) == n:
                ex[key] = np.asarray(v)[keep]
        af = ex.get("after_flip")
        if af is not None and not (np.any(af) and not np.all(af)):
            ex["after_flip"] = None
        fl = ex.get("flagged")
        n_flagged = 0
        flagged_cut = None
        if fl is not None and len(fl.get("t", [])):
            fk = self._trim_mask(fl["t"])
            n_flagged = int((~fk).sum())
            if not fk.all():
                # 2.2.18 (item 18, review): flagged frames in a trimmed span, for the fit CSV's trimmed rows
                flagged_cut = {k: np.asarray(fl[k], dtype=float)[~fk] for k in ("t", "flux", "err")
                               if fl.get(k) is not None and len(fl[k]) == len(fk)}
                ex["flagged"] = ({k: (np.asarray(v)[fk] if isinstance(v, np.ndarray) and len(v) == len(fk) else v)
                                  for k, v in fl.items()} if fk.any() else None)
        info = self._trim_info(t, flux, ~keep, n_flagged, (fl or {}).get("label") or "flagged", err)
        if flagged_cut and len(flagged_cut.get("t", [])) and len(flagged_cut.get("flux", [])):
            info["flagged_cut"] = dict(flagged_cut, label=(fl or {}).get("label") or "flagged")
        return t[keep], flux[keep], err[keep], am[keep], info

    def _trim_info(self, t, flux, cut, n_flagged=0, flag_label="flagged", err=None):
        spans = []
        for lo, hi in self._trims:
            m = cut & (t >= lo) & (t <= hi)
            if m.any():
                first, last = float(t[m].min()), float(t[m].max())
                spans.append({"first": first, "last": last, "n": int(m.sum()),
                              "clock": f"{self._bjd_to_clock(first)}–{self._bjd_to_clock(last)}"})
        spans.sort(key=lambda s_: s_["first"])
        exact = bool(self._time_map[2]) if self._time_map else False
        where = "; ".join(f"{s_['clock']} local (BJD_TDB {s_['first']:.5f}–{s_['last']:.5f}, {s_['n']} point"
                          f"{'s' if s_['n'] != 1 else ''})" for s_ in spans)
        n = int(cut.sum())
        approx = "" if exact else " Local times are approximate (±8 min: the report's star position is not known)."
        fl_text = (f" ({n_flagged} {flag_label}-flagged frame{'s' if n_flagged != 1 else ''} there are left out too)"
                   if n_flagged else "")
        text = (f"Trimmed: {where}{fl_text}. These {n} points are left out of the fit, the verdict, the rms, the comp "
                f"check and the saved files; Clear trim puts them back.{approx}")
        note = ("SHOBS-P: trimmed (left out of the fit) " + "; ".join(
            f"BJD_TDB {s_['first']:.5f}-{s_['last']:.5f} ({s_['clock'].replace('–', '-')} local, {s_['n']} points)"
            for s_ in spans))
        return {"spans": spans, "n": n, "n_flagged": n_flagged, "text": text, "report_note": note, "exact": exact,
                "t": t[cut], "flux": np.asarray(flux, dtype=float)[cut],
                "err": (np.asarray(err, dtype=float)[cut] if err is not None else None)}   # 2.2.18 (item 18)

    def _trim_source_check(self):
        """Review: a trim belongs to its data; a loaded, closed or new series (same night key or not) drops it."""
        gen = getattr(self.app, "_series_gen", 0)
        if self._trims and getattr(self, "_trim_gen", gen) != gen:
            self._reset_trim()
            self.app.log("Transit fit: the series changed, so the trim was cleared.")
        self._trim_gen = gen

    def _reset_trim(self):
        self._trims = []
        self._drag = None
        try:
            self.trim_from.set("")
            self.trim_to.set("")
        except Exception:
            pass
        self._update_trim_text()

    def _update_trim_text(self):
        trim = (self.result or {}).get("trim")
        if trim:
            # Review: short (no wrap needed at high DPI); the notes name every span.
            sp = trim["spans"]
            self.trim_text.set(f"Trimmed: {sp[0]['clock']} ({sp[0]['n']} pts)" if len(sp) == 1 else
                               f"Trimmed: {len(sp)} spans, {trim['n']} pts (see the notes)")
        elif self._trims:
            self.trim_text.set(f"{len(self._trims)} trimmed span{'s' if len(self._trims) != 1 else ''} set; "
                               "Fit transit applies it.")
        else:
            self.trim_text.set("local time (HHMM or HH:MM) or BJD_TDB; or drag across the plot")

    def trim_and_refit(self):
        """Add the From–To span (an empty From = from the start, an empty To = to the end) to the trimmed spans and
        refit the same data (no new reduction; the same comps)."""
        if self._fitting or getattr(self, "_checking", False) or self.app.busy:
            self.app.status.set("Wait for the current fit or check to end, then Trim and refit.")
            return
        a, b = self.trim_from.get().strip(), self.trim_to.get().strip()
        if not a and not b:
            messagebox.showinfo("SHOBS-P", "Type a From and/or To time (local, HHMM or HH:MM, or BJD_TDB), or drag "
                                           "across the plot, then Trim and refit.")
            return
        if self.report is None:
            self._trim_source_check()
        self._trim_gen = getattr(self.app, "_series_gen", 0)
        try:
            self._data_extra = {}
            t = np.asarray(self._data()[0], dtype=float)
            tmap = self._time_map_for(t)
            self._time_map = tmap
            drag = self._drag or {}
            lo = drag["lo"] if drag and a and a == drag.get("from") else self._clock_to_bjd(a, tmap)
            hi = drag["hi"] if drag and b and b == drag.get("to") else self._clock_to_bjd(b, tmap)
        except ValueError as exc:
            messagebox.showerror("SHOBS-P", f"Trim: {exc}")
            return
        lo = -math.inf if lo is None else lo
        hi = math.inf if hi is None else hi
        if lo > hi:
            messagebox.showerror("SHOBS-P", f"Trim: From ({a}) is after To ({b}).")
            return
        trims = self._trims + [(lo, hi)]
        inside = ~self._trim_mask(t, [(lo, hi)])
        if not inside.any():
            messagebox.showinfo("SHOBS-P", "No points fall between From and To: nothing to trim.")
            return
        kept = int(self._trim_mask(t, trims).sum())
        if kept < 20:
            messagebox.showerror("SHOBS-P", f"That trim would leave {kept} points; a transit fit needs at least 20.")
            return
        self._trims = trims
        self._drag = None
        self.app.log(f"Transit fit: trimming {int(inside.sum())} points ({a or 'start'} to {b or 'end'}) and refitting.")
        self._update_trim_text()
        self.fit()

    def clear_trim(self):
        if not self._trims:
            self.trim_from.set("")
            self.trim_to.set("")
            self._drag = None
            self.app.status.set("Nothing is trimmed.")
            return
        if self._fitting or getattr(self, "_checking", False) or self.app.busy:
            self.app.status.set("Wait for the current fit or check to end, then Clear trim.")
            return
        self._reset_trim()
        self.app.log("Transit fit: trim cleared; refitting all points.")
        self.fit()

    def _span_dragged(self, lo: float, hi: float):
        """A drag across the fitted plot: its ends (BJD_TDB) into the From and To boxes, as local clock times; the
        exact times are used unless a box is edited."""
        a, b = self._bjd_to_clock(lo), self._bjd_to_clock(hi)
        self._drag = {"from": a, "to": b, "lo": lo, "hi": hi}
        self.trim_from.set(a)
        self.trim_to.set(b)
        r = self.result or {}
        t = np.asarray(r.get("t", []), dtype=float)
        n = int(((t >= lo) & (t <= hi)).sum()) if t.size else 0
        self.app.status.set(f"Trim span {a}–{b} ({n} point{'s' if n != 1 else ''}) marked: Trim and refit leaves it out.")

    def _make_span_selector(self, tc0):
        """A matplotlib SpanSelector on the fitted plot (hours from the fitted Tc), or none when tc0 is None."""
        old, self._span = self._span, None
        if old is not None:
            try:
                old.disconnect_events()
            except Exception:
                pass
        if tc0 is None or self.fig is None:
            return
        try:
            from matplotlib.widgets import SpanSelector
        except Exception:
            return

        def onselect(x0, x1):
            if not (math.isfinite(x0) and math.isfinite(x1)) or abs(x1 - x0) < 1e-6:
                return
            self._span_dragged(tc0 + min(x0, x1) / 24.0, tc0 + max(x0, x1) / 24.0)
        style = dict(facecolor="#9aa7b0", alpha=0.3)
        try:
            self._span = SpanSelector(self.ax, onselect, "horizontal", useblit=False, props=style, minspan=0.5 / 60)
        except TypeError:
            try:
                self._span = SpanSelector(self.ax, onselect, "horizontal", useblit=False, rectprops=style,
                                          minspan=0.5 / 60)
            except Exception:
                self._span = None
        except Exception:
            self._span = None

    def _draw_trim(self, r, best, x):
        """Gray the trimmed spans (both panels) and show their points faintly; returns the x range they need."""
        trim = r.get("trim")
        if not trim or not trim.get("spans"):
            return None
        ax, axr = self.ax, self.ax_r
        pad = max(float(r.get("cadence_days") or 0) * 24 / 2, 0.5 / 60)
        lo, hi = math.inf, -math.inf
        for k, sp in enumerate(trim["spans"]):
            x0, x1 = (sp["first"] - best["tc"]) * 24 - pad, (sp["last"] - best["tc"]) * 24 + pad
            for a_ in (ax, axr):
                a_.axvspan(x0, x1, color="#c8d0d6", alpha=0.45, lw=0, zorder=0,
                           label="trimmed (not fitted)" if (a_ is ax and k == 0) else None)
            ax.text((x0 + x1) / 2, 0.99, f"trimmed\n{sp['clock']}", transform=ax.get_xaxis_transform(), ha="center",
                    va="top", fontsize=7, color="#5d6d76")
            lo, hi = min(lo, x0), max(hi, x1)
        try:
            # The trimmed points themselves, faint: their raw flux over the fit's baseline at the nearest time.
            tt = np.asarray(trim["t"], dtype=float)
            ft = np.asarray(trim["flux"], dtype=float) / float(r.get("flux_median") or 1.0)
            order = np.argsort(r["t"])
            base = np.interp(tt, np.asarray(r["t"])[order], np.asarray(r["baseline"])[order])
            ax.plot((tt - best["tc"]) * 24, ft / base, "x", color="#b0bac1", ms=3, mew=0.6, zorder=1)
        except Exception:
            pass
        return lo, hi

    # ---- 2.2.17 (items 7, 9): names ---------------------------------------------------------
    def _report_column(self) -> str:
        """The table column of the opened report: "SHOBS-P 2.2.16 report", "EXOTIC 4.3.2 report", else "Report"."""
        sw = report_software((self.report or {}).get("header"))
        return f"{sw} report" if sw else "Report"

    def family_names(self) -> list:
        """The transit report and plot PNG names: AAVSO_<planet>_<date>_SHOBS-P.txt, <planet>_<filter>_<date>
        _SHOBS-P_transit.png. 2.2.17 (item 9): saved as one numbered set with the light-curve CSV (App._family_path)."""
        r = self.result or {}
        meta = self.meta_for_files or {}
        name = _file_planet(((r.get("planet") or {}).get("name")) or (self.current_planet() or {}).get("name"))
        filt = str(meta.get("filter") or "CV").strip() or "CV"
        date = str(meta.get("obsdate") or "").strip()
        png = f"{name}_{filt}_{date}_SHOBS-P_transit.png" if date else f"{name}_{filt}_SHOBS-P_transit.png"
        names = [f"AAVSO_{name}_{date}_SHOBS-P.txt", png, png[:-len(".png")] + "_fit.csv"]   # 2.2.18 (item 18)
        return ["".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in n) for n in names]

    # ---- files -----------------------------------------------------------------------------
    def _file_meta(self) -> dict:
        meta = dict(self.meta_for_files)
        meta["version"] = getattr(self.app, "app_version", "")
        v = (self.result or {}).get("verdict")
        state = _state(v) if v else "detected"

        def ascii_(text):
            return (text.replace("Δ", "d").replace("±", "+/-").replace("σ", "sigma").replace("°", " deg")
                    .replace("−", "-").replace("×", "x").replace("²", "^2").replace("–", "-").replace("→", "->"))
        # 2.2.13: flagged frames put back, and the flip step (its time in a form a later fit can read back).
        wf = (self.result or {}).get("with_flagged")
        if wf:
            note = (f"SHOBS-P: fit INCLUDES {wf['n']} frames flagged {wf['label']} (error bars x{wf['factor']:g})")
            meta["notes"] = (meta.get("notes", "") + " | " if meta.get("notes") else "") + note
        if (self.report or {}).get("date_type") == "JD_UTC" and self.result is not None:
            # 2.2.17 review: never a silent mis-time: the times stayed JD_UTC.
            meta["notes"] = ((meta.get("notes", "") + " | " if meta.get("notes") else "")
                             + "SHOBS-P: times are JD_UTC from the source report (not converted; star position unknown)")
        trim = (self.result or {}).get("trim")
        if trim:
            # 2.2.17 (item 5): the trimmed span(s), ASCII, in the report's NOTES.
            meta["notes"] = (meta.get("notes", "") + " | " if meta.get("notes") else "") + ascii_(trim["report_note"])
        o = tc.oc_summary(self.result or {})
        if o is not None and state != "none" and (self.report or {}).get("date_type") != "JD_UTC":
            # 2.2.19 (#29): Tc against the ephemeris prediction, in the report's NOTES (ASCII).
            meta["notes"] = ((meta.get("notes", "") + " | " if meta.get("notes") else "") + "SHOBS-P "
                             + tc.oc_text(o, ascii_only=True))
        fnote = tc.flip_report_note(self.result or {})
        if fnote:
            meta["notes"] = (meta.get("notes", "") + " | " if meta.get("notes") else "") + ascii_(fnote)
        snote = self.settings_note(meta, self.result or {})   # 2.2.19 (#39, #40): settings and gain, compact
        if snote:
            meta["notes"] = (meta.get("notes", "") + " | " if meta.get("notes") else "") + ascii_(snote)
        for bf in [f for f in (self.result or {}).get("bound_flags") or [] if f.get("level", "warn") == "warn"]:
            # 2.2.19 (#31): a parameter pinned by its allowed range is said in the report too (not b against 0).
            meta["notes"] = (meta.get("notes", "") + " | " if meta.get("notes") else "") + ascii_("SHOBS-P: " + bf["text"])
        if state == "detected" and v and v.get("depth_unreliable"):
            note = ascii_("SHOBS-P: timing usable, depth unreliable: " + v["depth_unreliable"])
            meta["notes"] = (meta.get("notes", "") + " | " if meta.get("notes") else "") + note
        if state != "detected":
            label = "INCONCLUSIVE" if state == "inconclusive" else "NO TRANSIT MEASURED"
            note = f"SHOBS-P verdict {label}: " + v["reason"].replace("\n", " ")
            # AAVSO report headers are plain text: keep them ASCII.
            note = ascii_(note)
            meta["notes"] = (meta.get("notes", "") + " | " if meta.get("notes") else "") + note
        return meta

    def save_png(self):
        """2.2.7: the transit plot as shown, with the planet, source, verdict and results table above it. Works for a
        fit of photometry or of an EXOTIC report."""
        if not self.result or self.fig is None:
            messagebox.showinfo("SHOBS-P", "Fit a transit first.")
            return
        if self._still_checking("plot PNG"):
            return
        fname = self.family_names()[1]
        # 2.2.17 (item 9 nit): one number for the report, CSV and PNG of one fit (the PNG had its own _N counter).
        path = self.app.output_path(fname, title="Save transit plot PNG", defaultextension=".png",
                                    filetypes=[("PNG", "*.png")], parent=self.app, family=True)
        if not path:
            return
        try:
            self._write_png(path)
        except Exception as exc:
            messagebox.showerror("SHOBS-P", f"Could not save the PNG: {exc}")
            return
        self.app.status.set(f"Wrote {path}")
        self.app.log(f"Transit plot PNG: {path}")
        self._save_corner_beside(path)

    def _save_corner_beside(self, png_path: str) -> str:
        """2.2.19 (#31): the corner plot beside the transit PNG, as <stem>_corner.png. A failure here never costs the
        plot PNG (logged only)."""
        if not (self.result or {}).get("chain"):
            return ""
        path = self.app._unique_path(os.path.splitext(png_path)[0] + "_corner.png")   # never over an older one
        try:
            tpost.save_corner(self.result, path)
        except Exception as exc:
            self.app.log(f"Corner plot not saved: {exc}")
            return ""
        self.app.log(f"Corner plot PNG: {path}")
        return path

    def open_corner(self):
        """2.2.19 (#31): the corner plot of the shown fit's MCMC samples, in its own window."""
        if not self.result or ((self.result.get("chain") or {}).get("flat")) is None:
            messagebox.showinfo("SHOBS-P", "Fit a transit first: the corner plot shows that fit's MCMC samples.")
            return
        CornerWindow(self.app, self)

    def save_csv(self):
        """2.2.18 (item 18): Save light-curve CSV on the Transit page: the magnitude CSV (the Output page's), and with a
        fit shown also the fit CSV next to it, numbered with the report, plot and CSV of this fit (one save, one
        suffix). A fit of an opened report (no photometry) saves the fit CSV alone."""
        app = self.app
        path = ""
        if app.observations:
            path = app.save_csv()
            if not path:
                return
        if not self.result:
            if not app.observations:
                messagebox.showinfo("SHOBS-P", "Run photometry or open an exoplanet report, and fit a transit first.")
            return
        name = self.family_names()[2]
        if app._out_folder():
            app._save_one_night = getattr(app, "_last_csv_scope", "") == "night" if path else False
            try:
                fit_path = app.output_path(name, title="Save transit fit CSV", defaultextension=".csv",
                                           filetypes=[("CSV", "*.csv")], parent=app, family=True)
            finally:
                app._save_one_night = False
        elif path:
            # review: named after the CSV the user chose, so the pair matches (mine.csv -> mine_transit_fit.csv)
            stem = os.path.splitext(os.path.abspath(path))[0]
            fit_path = app._unique_path(stem + "_transit_fit.csv")
        else:
            fit_path = app.output_path(name, title="Save transit fit CSV", defaultextension=".csv",
                                       filetypes=[("CSV", "*.csv")], parent=app)
        if not fit_path:
            return
        try:
            n = write_fit_csv(fit_path, self.result, (self.report or {}).get("date_type") == "JD_UTC",
                              meta=self.meta_for_files)
        except Exception as exc:
            messagebox.showerror("SHOBS-P", f"Could not save the fit CSV: {exc}")
            return
        app.log(f"Transit fit CSV: {fit_path} ({n} rows)")
        app.status.set(f"Wrote {os.path.basename(path) + ' and ' if path else ''}{os.path.basename(fit_path)}")

    def _write_png(self, path: str):
        import io

        import matplotlib.image as mpimg
        from matplotlib.figure import Figure as _Fig
        from matplotlib.backends.backend_agg import FigureCanvasAgg

        buf = io.BytesIO()
        self._save_fig(buf, format="png", dpi=130, facecolor="#ffffff")
        buf.seek(0)
        plot = mpimg.imread(buf)
        ph, pw = plot.shape[:2]
        lines = [(self.planet_text.get(), 9, "#33424d", "normal")]
        if self.banner.get():
            lines.append((self.banner.get(), 9, "#8a4b00", "bold"))
        elif self.source_text.get():
            lines.append((self.source_text.get(), 9, "#33424d", "normal"))
        lines.append((self.verdict.get(), 11, {"detected": "#2e7d32", "inconclusive": "#c26a00",
                                              "none": "#b00020"}.get(_state(self.result.get("verdict")), "black"),
                      "bold"))
        if (self.result.get("verdict") or {}).get("depth_unreliable"):
            lines[-1] = (lines[-1][0], 11, "#c26a00", "bold")
        if self.quality.get():
            lines.append((self.quality.get(), 9, "#33424d", "normal"))   # 2.2.14 (GitHub #5)
        all_cols = ["param", "shobs", "free", "pub", "exotic"]
        cols = list(getattr(self, "_display_cols", None) or all_cols[:4])
        labels = {"param": "Parameter", "shobs": "SHOBS-P (MCMC)", "free": "Free shape", "pub": "Published / prior",
                  "exotic": self._report_column()}   # 2.2.17 (item 7)
        heads = [labels[c] for c in cols]
        rows = [heads]
        for iid in self.table.get_children():
            try:
                vals = list(self.table.item(iid).get("values") or [])
            except Exception:
                vals = []
            by_col = dict(zip(all_cols, [str(v) for v in vals]))
            rows.append([by_col.get(c, "") for c in cols])
        # 2.2.17 (item 4): the notes without the on-screen Comp health link.
        notes = [ln.replace(" " + COMP_LINK, "").strip() for ln in self.notes.get("1.0", END).split("\n") if ln.strip()]
        w_in = pw / 130.0
        # 2.2.12: lines are wrapped by their measured width at their own size and weight (the old estimate of
        # 13 characters an inch ran long bold verdicts off the edge), and notes are wrapped in full, not cut.
        avail = (w_in - 0.4) * 130.0
        wrapped = [(part, size, colour, weight) for text, size, colour, weight in lines
                   for part in (wrap_to_width(text, avail, size, weight, 130) or [""])]
        note_lines = []
        for ln in notes:
            colour = "#b35c00" if ln.startswith("⚠") else "#33424d"
            note_lines += [(part, colour) for part in wrap_to_width(ln, avail, 8, "normal", 130)]
        header_h = 0.24 * len(wrapped) + 0.2 * len(rows) + 0.17 * len(note_lines) + 0.5
        fig = _Fig(figsize=(w_in, header_h + ph / 130.0), dpi=130, facecolor="#ffffff")
        FigureCanvasAgg(fig)
        total = header_h + ph / 130.0
        y = 1.0 - 0.25 / total
        for part, size, colour, weight in wrapped:
            fig.text(0.02, y, part, fontsize=size, color=colour, fontweight=weight, va="top")
            y -= 0.24 / total
        y -= 0.06 / total
        width = [max(len(r[k]) if k < len(r) else 0 for r in rows) for k in range(len(cols))]
        xs, acc = [], 0.02
        for k in range(len(cols)):
            xs.append(acc)
            acc += min(0.35, (width[k] + 2) / max(sum(width) + 2 * len(cols), 1) * 0.96)
        for j, r in enumerate(rows):
            for k, cell in enumerate(r):
                fig.text(xs[k], y, cell, fontsize=8, va="top", family="monospace" if k else None,
                         fontweight="bold" if j == 0 else "normal", color="#1a1a1a")
            y -= 0.2 / total
        for part, colour in note_lines:
            fig.text(0.02, y, part, fontsize=8, color=colour, va="top")
            y -= 0.17 / total
        ax = fig.add_axes([0, 0, 1, (ph / 130.0) / total])
        ax.imshow(plot)
        ax.axis("off")
        fig.text(0.98, 1.0 - 0.08 / total, f"Created {core.dtg()} · SHOBS-P {getattr(self.app, 'app_version', '')}",
                 fontsize=7, color="#7a8a94", ha="right", va="top")
        fig.savefig(path, dpi=130, facecolor="#ffffff")

    def save_aavso(self):
        if not self.result:
            messagebox.showinfo("SHOBS-P", "Fit a transit first.")
            return
        if self._still_checking("AAVSO exoplanet report"):
            return
        if not self._confirm_export():
            return
        meta = self._file_meta()
        # 2.2.17 (item 9 nit): one number for the report, CSV and PNG of one fit.
        path = self.app.output_path(self.family_names()[0], title="Save AAVSO exoplanet report",
                                    defaultextension=".txt", filetypes=[("Text", "*.txt")], parent=self.app,
                                    family=True)
        if not path:
            return
        with core.AsciiWriter(path, newline="\n") as fh:
            fh.write(tc.aavso_exoplanet_report(self.result, meta))
        self.app.log(f"AAVSO exoplanet report: {path}")
        self._write_settings(path, meta)
        self.app._remember_report("exoplanet", self._fit_nights(), path)   # 2.2.18 (item 10)
        if self.report is None:
            self.app._field_image_with_report(path)   # 2.2.19 (#28): not for an opened EXOTIC report's fit
        # 2.2.15 (item 8): the status line names the file and offers the upload page.
        self.app.offer_upload(path, "Open AAVSO Exoplanet Database…", core.AAVSO_EXOPLANET_URL,
                              "the AAVSO Exoplanet Database (check its current instructions)")

    def _write_settings(self, report_path: str, meta: dict) -> str:
        """2.2.19 (#40): <report stem>_settings.txt beside the report: the full settings block. Never costs the
        report (logged on failure)."""
        lines = self.settings_lines(meta, self.result)
        if not lines:
            return ""
        path = self.app._unique_path(os.path.splitext(report_path)[0] + "_settings.txt")   # never over an older one
        try:
            with core.AsciiWriter(path, newline="\n") as fh:
                fh.write(f"SHOBS-P {getattr(self.app, 'app_version', '')} settings for "
                         f"{os.path.basename(report_path)}\n" + "\n".join(lines) + "\n")
        except Exception as exc:
            self.app.log(f"Settings file not written: {exc}")
            return ""
        self.app.log(f"Settings file: {path}")
        return path

    def _fit_nights(self) -> list:
        """2.2.18 (item 10): the series night the shown fit is of ([] for a fit of an opened report)."""
        if self.report is not None or not self.result:
            return []
        night = self.night.get()
        return [night] if night else []

    def open_aavso_exoplanet(self):
        """2.2.15 (item 8): AAVSO Exoplanet Database's submit page."""
        plan = self.app._upload_check("exoplanet")   # 2.2.18 (item 10)
        if plan is None:
            return
        if self.app.open_link(core.AAVSO_EXOPLANET_URL, "the AAVSO Exoplanet Database's submit page"):
            self.app._mark_sent("exoplanet", plan)

    def exofop_page(self) -> tuple[str, str]:
        """2.2.15 (item 9): (URL, what) of the target's ExoFOP-TESS page: by TOI, else by TIC, else ExoFOP's home."""
        app = self.app
        names = [app.planet_name.get().strip(), app.star_id.get().strip()]
        info = dict(app.planet_info or {})
        typed = names[0].replace(" ", "").lower()
        known = {str(info.get(k) or "").replace(" ", "").lower() for k in ("name", "typed")}
        if typed and typed not in known:
            info = {}   # the last lookup was for another planet
        if self.report is not None:
            rp = self.report.get("planet") or {}
            names += [str(rp.get("name") or ""), str(rp.get("host") or "")]
        toi, tic = tc.exofop_ids(info, *names)
        url = core.exofop_target_url(toi=toi, tic=tic)
        if url:
            return url, (f"ExoFOP's page for TOI-{toi}" if toi and f"toi={int(toi)}" in url else f"ExoFOP's page for TIC {tic}")
        return core.EXOFOP_TESS_URL, "ExoFOP-TESS"

    def open_exofop(self):
        """2.2.15 (item 9): open the target's ExoFOP-TESS page (TOI or TIC), else ExoFOP-TESS's home. The first time,
        a hint on how ExoFOP uploads work."""
        app = self.app
        plan = app._upload_check("exofop")   # 2.2.18 (item 10): {} when only browsing (nothing new to mark)
        if plan is None:
            return
        url, what = self.exofop_page()
        home = url == core.EXOFOP_TESS_URL
        # The hint once on the first click, and once more the first time ExoFOP's home has to stand in.
        if not app.settings.get("exofop_hint_shown") or (home and not app.settings.get("exofop_home_hint_shown")):
            app.settings["exofop_hint_shown"] = True
            if home:
                app.settings["exofop_home_hint_shown"] = True
            try:
                app._save_settings()
            except Exception:
                pass
            messagebox.showinfo(
                "SHOBS-P",
                ("No TOI or TIC number is known for this target, so ExoFOP-TESS's home page opens: search for the star "
                 "there.\n\n" if home else "")
                + "ExoFOP-TESS is for TESS targets (TOIs and TIC stars) only, and uploading needs an ExoFOP account "
                  "(log in first).\n\n"
                  "Time series: on the target's page, logged in, choose \"Add new\" in the Time Series section and "
                  "upload the light curve (CSV) and fit notes from the ExoFOP package.\n"
                  "Files (plot, report): Files \"Add new\" on the same page, with the same data tag.\n\n"
                  "Data tag: YYYYMMDD_username_description_n (for example 20261015_jsmith_SHOBS-P-transit_1); the "
                  "description may use only letters, numbers and hyphens.",
                parent=app)
        if app.open_link(url, what):
            app._mark_sent("exofop", plan)
        if home:
            app.status.set("Opened ExoFOP-TESS (no TOI or TIC number known for this target): search for the star there.")

    def _still_checking(self, what: str) -> bool:
        """2.2.16 (item 6, review): files written while the comp check runs would miss its comp-choice line and
        cautions; say so and let the user save when it is done."""
        if not getattr(self, "_checking", False):
            return False
        messagebox.showinfo("SHOBS-P", f"The comps are still being checked (a few seconds). Save the {what} when "
                                       "that is done, so it carries the comp-choice result.")
        return True

    def save_package(self):
        if not self.result:
            messagebox.showinfo("SHOBS-P", "Fit a transit first.")
            return
        if self._still_checking("ExoFOP package"):
            return
        if not self._confirm_export():
            return
        folder = self.app.output_directory("Folder for the ExoFOP package", parent=self.app)
        if not folder:
            return
        r = self.result
        meta = self._file_meta()
        planet = _file_planet(r["planet"].get("name"))
        from datetime import datetime, timezone
        t_first = float(r["t"].min())
        if r.get("trim") is not None and len(r["trim"].get("t", [])):
            t_first = min(t_first, float(np.min(r["trim"]["t"])))   # review: a trim never moves the package's date
        date = datetime.fromtimestamp((t_first - 2440587.5) * 86400.0, tz=timezone.utc).strftime("%Y%m%d")
        base = f"{planet}_{date}_{meta.get('obscode') or 'obs'}_SHOBS-P"
        # 2.2.17 (item 9): the folder is ExoFOP_<planet>_<tag>; the files inside keep <planet>_<tag> names.
        dir_prefix = "ExoFOP_"
        # Never write into an earlier package: a second save of the same night becomes ..._2 (2.2).
        # 2.2.16 (item 4): folder only (no zip; an old package's .zip still counts as taken).
        out, k = os.path.join(folder, dir_prefix + base), 2
        while True:
            stale = getattr(self, "_stale_parts", set())
            if os.path.normcase(os.path.abspath(out + ".part")) in stale:
                # 2.2.16 (review): a duplicate .part left by an "unchanged" save whose removal failed then
                if _remove_tree(out + ".part"):
                    stale.discard(os.path.normcase(os.path.abspath(out + ".part")))
            if not (os.path.exists(out) or os.path.exists(out + ".zip") or os.path.exists(out + ".part")):
                break
            out, k = os.path.join(folder, f"{dir_prefix}{base}_{k}"), k + 1
        base = os.path.basename(out)[len(dir_prefix):]
        final = out
        # 2.2.16 (item 4): written into <folder>.part first; kept (renamed) only when it differs from the last package
        # of this fit in this folder (a double-click had made identical _3 and _4).
        out = final + ".part"
        os.makedirs(out, exist_ok=True)
        try:
            files, png_print = self._write_package_files(out, base, r, meta, planet)
            fingerprint = package_fingerprint(files, png_print)
            last = getattr(self, "_last_package", None)
            if (last and last.get("print") == fingerprint and os.path.isdir(last.get("folder", ""))
                    and os.path.normcase(os.path.dirname(os.path.abspath(last["folder"])))
                    == os.path.normcase(os.path.abspath(folder))):
                if not _remove_tree(out):
                    self._stale_parts = getattr(self, "_stale_parts", set()) | {os.path.normcase(os.path.abspath(out))}
                self.app.log(f"ExoFOP package unchanged: {last['folder']} (same fit and files; nothing new written)")
                self.app._remember_report("exofop", self._fit_nights(), last["folder"])   # 2.2.18 (item 10)
                self.app.status.set(f"ExoFOP package unchanged: {os.path.basename(last['folder'])}")
                return
        except Exception:
            shutil.rmtree(out, ignore_errors=True)
            raise
        # 2.2.16 (review): on Windows renaming a folder fails while a file in it is still held open (antivirus,
        # indexer right after writing): retry briefly; if it still fails, keep the .part folder and say so.
        renamed = False
        for k in range(8):
            try:
                os.replace(out, final)
                renamed = True
                break
            except OSError:
                import time as _time
                _time.sleep(0.25 * (k + 1))
        if not renamed:
            self.app.log(f"ExoFOP package written to {out}, but it could not be renamed to {os.path.basename(final)} "
                         "(a file in it was still open). Rename the folder yourself, or save the package again.")
            messagebox.showwarning("SHOBS-P", f"The ExoFOP package is complete in\n{out}\nbut the folder could not be "
                                              f"renamed to {os.path.basename(final)}. Rename it yourself, or save "
                                              "the package again.", parent=self.app)
            return
        self._last_package = {"print": fingerprint, "folder": final}
        out = final
        self.app._remember_report("exofop", self._fit_nights(), out)   # 2.2.18 (item 10)
        self.app.log(f"ExoFOP package folder: {out}")
        # 2.2.17 (item 9): no AAVSO report copy inside (John); Save AAVSO exoplanet report writes that one.
        self.app.saved_notice(out, "Package folder: light curve (CSV), fit and ExoFOP notes (text), and the plot.")

    def _write_package_files(self, out, base, r, meta, planet):
        """2.2.16 (item 4): the package's files in folder out; returns (paths, the plot's PNG bytes without its DTG
        stamp, for the fingerprint, or None without a plot)."""
        files = []
        lc = os.path.join(out, f"{base}_lightcurve.csv")
        with open(lc, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow([core.created_line()])
            w.writerow(["BJD_TDB", "rel_flux", "rel_flux_err", "airmass", "detrended_flux", "model", "residual"])
            for row in zip(r["t"], r["flux"], r["err_scaled"], r["airmass"], r["detrended"], r["model"], r["residuals"]):
                w.writerow([f"{row[0]:.7f}"] + [f"{v:.6f}" if math.isfinite(v) else "" for v in row[1:]])
        files.append(lc)
        txt = os.path.join(out, f"{base}_fit.txt")
        with core.AsciiWriter(txt) as fh:
            fh.write(tc.fit_text(r, meta))
            # 2.2.15 (item 11): equipment from the headers and Optics box, pixel scale, aperture, coverage.
            fh.write(tc.exofop_notes(r, meta, mono=self.app.pattern.get() == core.MONO))
        files.append(txt)
        # 2.2.17 (item 9): the AAVSO exoplanet report is no longer copied into the package (John's choice).
        png_print = None
        if self.fig is not None:
            png = os.path.join(out, f"{base}_plot.png")
            self._save_fig(png, stamp=True, dpi=150)
            files.append(png)
            buf = io.BytesIO()
            self._save_fig(buf, stamp=False, dpi=150, format="png")
            png_print = buf.getvalue()
        return files, png_print


def write_fit_csv(path: str, r: dict, jd_utc: bool = False, meta: dict | None = None) -> int:
    """2.2.18 (item 18): the transit fit as a CSV: time, relative flux, its error (as the fit weighted it), the model,
    the residual, and a flag column, one row per point in time order. Trimmed points (2.2.17 Trim) are rows too,
    flagged "trimmed", with no error, model or residual (they were left out of the fit); frames put back from a flag
    (Fit with flagged) carry that flag ("trimmed cloud" when also trimmed). Returns the number of data rows."""
    t = np.asarray(r["t"], dtype=float)
    flux = np.asarray(r["flux"], dtype=float)
    err = np.asarray(r.get("err_scaled", r.get("err")), dtype=float)
    model = np.asarray(r["model"], dtype=float)
    resid = np.asarray(r["residuals"], dtype=float)
    fmask = r.get("flagged")
    fmask = np.asarray(fmask, dtype=bool) if fmask is not None and len(fmask) == len(t) else np.zeros(len(t), bool)
    label = str((r.get("with_flagged") or {}).get("label") or "flagged")
    rows = [(t[k], flux[k], err[k], model[k], resid[k], label if fmask[k] else "") for k in range(len(t))]
    trim = r.get("trim") or {}
    med = float(r.get("flux_median") or 1.0)
    tt = np.asarray(trim.get("t", []), dtype=float)
    if tt.size:
        # review: trimmed rows have no error either (the fit never weighted them)
        ft = np.asarray(trim.get("flux", []), dtype=float) / med
        rows += [(tt[k], ft[k], float("nan"), float("nan"), float("nan"), "trimmed") for k in range(tt.size)]
    fc = trim.get("flagged_cut") or {}
    if r.get("with_flagged") and len(fc.get("t", [])):
        # review: frames both flagged (put back with Fit with flagged) and trimmed: "trimmed" first, then the flag
        ff = np.asarray(fc["flux"], dtype=float) / med
        rows += [(float(fc["t"][k]), ff[k], float("nan"), float("nan"), float("nan"), f"trimmed {fc.get('label')}")
                 for k in range(len(fc["t"]))]
    rows.sort(key=lambda row: row[0])

    def num(v, fmt):
        return fmt.format(v) if v is not None and math.isfinite(v) else ""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([core.created_line()])
        for line in fit_csv_comments(r, meta, jd_utc):     # 2.2.19 (#29, #18): unquoted, so each starts with "#"
            fh.write(line + "\r\n")
        w.writerow(["JD_UTC" if jd_utc else "BJD_TDB", "rel_flux", "rel_flux_err", "model", "residual", "flag"])
        for row in rows:
            w.writerow([num(row[0], "{:.7f}")] + [num(v, "{:.6f}") for v in row[1:5]] + [row[5]])
    return len(rows)


def fit_csv_comments(r: dict, meta: dict | None = None, jd_utc: bool = False) -> list:
    """2.2.19 (#29, #18): comment lines under the Created line of the fit CSV: the planet, filter and fitted Tc (so a
    multi-night analysis can read the night back), and Tc against the ephemeris prediction (O - C). ASCII only."""
    s = (r or {}).get("summary") or {}
    pl = (r or {}).get("planet") or {}
    filt = str((meta or {}).get("filter") or "").strip()
    out = []
    if "tc" in s:
        out.append(core.ascii_safe(f"# Transit fit: planet {pl.get('name') or ''}; filter {filt or 'unknown'}; Tc "
                                   f"{s['tc'][0]:.6f} +/- {s['tc'][1]:.6f} {'JD_UTC' if jd_utc else 'BJD_TDB'}; "
                                   f"verdict {_state((r or {}).get('verdict'))}"))
    o = tc.oc_summary(r or {})
    if o is not None and not jd_utc:
        out.append("# " + tc.oc_text(o, ascii_only=True))
    return out


# The DTG stamp lines only: "# Created 151200Z OCT 26 by SHOBS-P" (CSV, reports) and "Created        151200Z OCT 26"
# (fit file); a user line that merely starts with "Created" still counts.
def _remove_tree(path: str, tries: int = 5) -> bool:
    """2.2.16 (review): remove a folder, retried briefly (Windows: a file in it may still be open); True when gone."""
    import time as _time

    for k in range(tries):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return True
        _time.sleep(0.2 * (k + 1))
    return not os.path.exists(path)


_CREATED_LINE = re.compile(r"^#?\s*Created\s+\d{6}Z [A-Z]{3} \d{2}( by SHOBS-P)?\s*$")


def package_fingerprint(files, png_print=None) -> str:
    """2.2.16 (item 4): what a package says, ignoring its DTG ("Created ...") lines and the files' names (a new
    package's names carry its _N): the text files' other lines in order, and the plot without its stamp."""
    h = hashlib.sha256()
    for path in files:
        ext = os.path.splitext(path)[1].lower()
        h.update(ext.encode() + b"\0")
        if ext == ".png":
            if png_print is not None:
                h.update(hashlib.sha256(png_print).digest())
            continue
        with open(path, "rb") as fh:
            for line in fh.read().decode("utf-8", "replace").splitlines():
                if not _CREATED_LINE.match(line):
                    h.update(line.encode("utf-8") + b"\n")
    return h.hexdigest()


def wrap_to_width(text: str, width_px: float, size: float, weight: str = "normal", dpi: float = 130) -> list:
    """2.2.12: greedy word wrap by the rendered width (matplotlib's Agg text metrics) at this font size and
    weight. A single word wider than the line is left whole on its own line."""
    from matplotlib.backends.backend_agg import RendererAgg
    from matplotlib.font_manager import FontProperties

    words = str(text or "").split()
    if not words:
        return []
    renderer = RendererAgg(10, 10, dpi)
    prop = FontProperties(size=size, weight=weight)

    def width(t):
        return renderer.get_text_width_height_descent(t, prop, ismath=False)[0]

    out, line = [], words[0]
    for w in words[1:]:
        trial = line + " " + w
        if width(trial) <= width_px:
            line = trial
        else:
            out.append(line)
            line = w
    out.append(line)
    return out


def _state(verdict) -> str:
    """'detected', 'inconclusive', or 'none' (fits made before 2.2.1 have only 'detected')."""
    if not verdict:
        return "detected"
    return verdict.get("state") or ("detected" if verdict.get("detected", True) else "none")


def _norm(name) -> str:
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


# 2.2.17 (item 4): the comp-choice cautions (photometry_core.comp_noise_warning, transit_core.leave_one_comp_out) and
# the link the notes add after them.
COMP_CAUTION_PREFIXES = ("The comparison stars set most of the noise", "Leaving out ", "The comp choice moves")
COMP_LINK = "→ Open Comp health…"
_FIT_SERIAL = __import__("itertools").count(1)   # 2.2.17 review: one number per fit (id() can be reused)


def fit_serial(result) -> int:
    """2.2.17 review: the fit's serial for App._results_key, stamped by _finish_fit (a result set any other way, as
    in tests, falls back to its id)."""
    return result.get("_serial") or id(result)


def _float_or_none(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _shift_report_times(rep, t_old):
    """2.2.17 review round 2: after a report's points moved to BJD_TDB (rep["t"] from t_old), move its own JD-valued
    results (Tc, and any other value that is a full JD) and the ephemeris read from it (t0) by the same offset there."""
    t_old = np.asarray(t_old, dtype=float)
    t_new = np.asarray(rep["t"], dtype=float)
    ok = np.isfinite(t_old) & np.isfinite(t_new)
    if not ok.any():
        return
    order = np.argsort(t_old[ok])
    xs, offs = t_old[ok][order], (t_new[ok] - t_old[ok])[order]

    def moved(v):
        return float(v) + float(np.interp(float(v), xs, offs))
    res = rep.get("results") or {}
    for name, val in list(res.items()):
        if val and isinstance(val[0], (int, float)) and math.isfinite(val[0]) and val[0] > 2.4e6:
            res[name] = (moved(val[0]),) + tuple(val[1:])
    pl = rep.get("planet") or {}
    if isinstance(pl.get("t0"), (int, float)) and pl["t0"] > 2.4e6:
        pl["t0"] = moved(pl["t0"])


def report_software(header) -> str:
    """2.2.17 (item 7): the program that wrote an AAVSO exoplanet report, from #SOFTWARE: "EXOTIC v4.3.2" ->
    "EXOTIC 4.3.2", "SHOBS-P 2.2.16" -> "SHOBS-P 2.2.16"; '' when the header does not say."""
    raw = " ".join(str((header or {}).get("SOFTWARE", "") or "").split())
    if not raw:
        return ""
    words, name, version = raw.split(" "), [], ""
    for w in words:
        # Review: EXOTIC writes "v4.3.2.dev160+gd77c750dc.d20260911": the leading numbers are the version.
        m = re.match(r"[vV]?(\d+(?:\.\d+){0,2})", w)
        if m and name:
            version = m.group(1)
            break
        name.append(w)
    label = " ".join(name)[:40].strip()
    return (label + (f" {version}" if version else "")).strip()


def report_kind(header) -> str:
    """2.2.17 (item 7): "a SHOBS-P 2.2.16 report", "an EXOTIC 4.3.2 report", or "an AAVSO exoplanet report"."""
    sw = report_software(header)
    if not sw:
        return "an AAVSO exoplanet report"
    return ("an " if sw[0].lower() in "aeiou" else "a ") + sw + " report"


def _file_planet(name) -> str:
    """2.2.17 (item 9): the planet in file and folder names, one spelling everywhere: "KELT-16 b" -> "KELT-16b"."""
    return "".join(str(name or "planet").split()) or "planet"


def parse_clock(text):
    """2.2.11 (John): a clock time typed as 23:00, 2300, 23.00, 23h00, 23 or 130 (01:30) -> (hours, minutes), or
    (None, None) for an empty box. Anything else raises ValueError with a plain message."""
    raw = (text or "").strip()
    if not raw:
        return None, None
    t = raw.lower().replace("h", ":").replace(".", ":").replace(" ", "")
    if ":" in t:
        hh, _, mm = t.partition(":")
        if not hh.isdigit() or (mm and (not mm.isdigit() or len(mm) != 2)):
            raise ValueError(f"'{raw}' is not a time; use HHMM or HH:MM (for example 2300 or 23:00)")
        h, m = int(hh), int(mm or 0)
    elif t.isdigit() and len(t) <= 2:
        h, m = int(t), 0
    elif t.isdigit() and len(t) in (3, 4):
        h, m = int(t[:-2]), int(t[-2:])
    else:
        raise ValueError(f"'{raw}' is not a time; use HHMM or HH:MM (for example 2300 or 23:00)")
    if h == 24 and m == 0:
        h = 0
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"'{raw}' is not a time; use HHMM or HH:MM (for example 2300 or 23:00)")
    return h, m


def _bin(x, y, width):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) < 4 or width <= 0:
        return []
    edges = np.arange(x.min(), x.max() + width, width)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (x >= lo) & (x < hi) & np.isfinite(y)
        if m.sum() >= 2:
            out.append((float(x[m].mean()), float(y[m].mean()), float(y[m].std() / math.sqrt(m.sum()))))
    return out


def _altaz(app) -> bool:
    """2.2.18 (item 8): is the user's mount alt-az (the Optics box's Mount choice)? Planning is for the user's own
    telescope, so the choice counts here, not the headers of whatever lights are loaded."""
    try:
        return app._mount_choice_key() == core.MOUNT_ALTAZ
    except Exception:
        return False


def _plan_limits(app) -> dict:
    """2.2.18 (item 17): the limits both planners start from: the ones remembered from Tonight's transits, else the
    shared defaults (tc.PLAN_MIN_ALT, PLAN_SUN_ALT, PLAN_BASELINE_MIN)."""
    out = {"min_alt": tc.PLAN_MIN_ALT, "sun_alt": tc.PLAN_SUN_ALT, "baseline": tc.PLAN_BASELINE_MIN}
    saved = (getattr(app, "settings", None) or {}).get("finder") or {}
    for key in out:
        try:
            value = float(saved[key])
            if math.isfinite(value):
                out[key] = value
        except (KeyError, TypeError, ValueError):
            continue
    return out


class PlannerWindow:
    """Upcoming transits seen from the site: times, altitudes, darkness, Moon."""

    def __init__(self, app):
        self.app = app
        planet = app.planet_from_fields()
        if not planet or not planet.get("period") or not planet.get("t0"):
            messagebox.showinfo("SHOBS-P", "The planner needs the planet's period and T0: enter the planet on the "
                                           "Input page and press Enter.")
            return
        if not (app.lat.get().strip() and app.lon.get().strip()):
            messagebox.showinfo("SHOBS-P", "Enter your site's latitude and longitude on the Input page first.")
            return
        try:
            self.ra, self.dec = app.target_radec_deg(planet)
            self.lat, self.lon = float(app.lat.get()), float(app.lon.get())
        except Exception as exc:
            messagebox.showinfo("SHOBS-P", f"The planner needs the target's RA/Dec and the site: {exc}")
            return
        self.planet = planet
        win = Toplevel(app)
        self.win = win
        win.title(f"SHOBS-P — transit planner: {planet.get('name', '')}")
        win.after_idle(lambda: fit_to_content(win))
        top = ttk.Frame(win, style="Card.TFrame", padding=10)
        top.pack(fill=BOTH, expand=True)
        ttk.Label(top, text=f"Transit planner: {planet.get('name', '')}", font=(core.UI_FONT, 15, "bold"),
                  style="Card.TLabel").pack(anchor=W)
        ttk.Label(top, text=(f"Site {self.lat:.3f}, {self.lon:.3f}. Times are local (this computer's time zone). "
                             "'full' = transit plus the baseline on both sides above the altitude limit in a dark sky; "
                             "'transit only' = no full baseline; 'partial' = part of the transit. ± is the ephemeris "
                             "uncertainty at that date. Planner times are good to about a minute."),
                  style="Hint.TLabel", wraplength=1350, justify=LEFT).pack(anchor=W, pady=(2, 6))
        opts = ttk.Frame(top, style="Card.TFrame")
        opts.pack(fill=X)
        self.days = DoubleVar(value=30)
        lim = _plan_limits(app)   # 2.2.18 (item 17): one set of limits with Tonight's transits
        self.min_alt = DoubleVar(value=lim["min_alt"])
        self.sun_alt = DoubleVar(value=lim["sun_alt"])
        self.baseline = DoubleVar(value=lim["baseline"])
        self.altaz = _altaz(app)   # 2.2.18 (item 8): alt-az: warn about the zenith keyhole
        self.full_only = BooleanVar(value=False)
        for label, var, width in (("Days ahead", self.days, 6), ("Min altitude (°)", self.min_alt, 6),
                                  ("Sun below (°)", self.sun_alt, 6), ("Baseline each side (min)", self.baseline, 6)):
            ttk.Label(opts, text=label, style="Card.TLabel").pack(side=LEFT, padx=(0, 4))
            ttk.Entry(opts, textvariable=var, width=width).pack(side=LEFT, padx=(0, 12))
        ttk.Checkbutton(opts, text="full only", variable=self.full_only).pack(side=LEFT)
        ttk.Button(opts, text="Update", style="Accent.TButton", command=self.update).pack(side=LEFT, padx=10)
        ttk.Button(opts, text="Export CSV…", command=self.export).pack(side=LEFT)
        cols = ("date", "t1", "tc", "t4", "alt", "sun", "moon", "sigma", "status") + (("zenith",) if self.altaz else ())
        self.tree = ttk.Treeview(top, columns=cols, show="headings", height=18)
        for col, label, width in (("date", "Night of", 150), ("t1", "Ingress", 80), ("tc", "Mid", 80), ("t4", "Egress", 80),
                                  ("alt", "Altitude in / mid / out", 190), ("sun", "Sun at mid", 100),
                                  ("moon", "Moon (lit, distance)", 170), ("sigma", "± ephemeris", 110),
                                  ("status", "Coverage", 140), ("zenith", "Zenith keyhole", 150))[:len(cols)]:
            _col(self.tree, col, label, width, stretch=(col == "status"))
        self.tree.tag_configure("full", foreground="#2e7d32")
        self.tree.tag_configure("transit only", foreground="#1c2b33")
        self.tree.tag_configure("partial", foreground="#b35c00")
        scroll = ttk.Scrollbar(top, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True, pady=(8, 0))
        scroll.pack(side=LEFT, fill=Y, pady=(8, 0))
        self.events = []
        self.update()

    def update(self):
        try:
            events = tc.plan_transits(self.planet, self.ra, self.dec, self.lat, self.lon, tc.now_jd() - 0.25,
                                      float(self.days.get()), float(self.min_alt.get()), float(self.sun_alt.get()),
                                      float(self.baseline.get()))
        except Exception as exc:
            messagebox.showerror("SHOBS-P", f"Planner: {exc}", parent=self.win)
            return
        if self.full_only.get():
            events = [e for e in events if e["status"] == "full"]
        self.events = events
        self.tree.delete(*self.tree.get_children())
        for e in events:
            t1, tm, t4 = (tc.jd_to_local(e[k]) for k in ("t1_jd", "tc_jd", "t4_jd"))
            evening = tc.jd_to_local(e["tc_jd"] - 0.5)
            self.tree.insert("", END, tags=(e["status"],), values=(
                evening.strftime("%a %d %b %Y"), t1.strftime("%H:%M"), tm.strftime("%H:%M"), t4.strftime("%H:%M"),
                f"{e['alt_t1']:.0f}° / {e['alt_tc']:.0f}° / {e['alt_t4']:.0f}°", f"{e['sun_tc']:.0f}°",
                f"{e['moon_illum'] * 100:.0f}%, {e['moon_sep']:.0f}°", f"{e['sigma_min']:.1f} min", e["status"])
                + ((f"KEYHOLE: up to {e['alt_max']:.0f}°" if e.get("keyhole") else "",) if self.altaz else ()))
        if not events:
            self.tree.insert("", END, values=("No observable transits in this range.", "", "", "", "", "", "", "", "")
                             + (("",) if self.altaz else ()))

    def export(self):
        if not self.events:
            return
        path = self.app.output_path(f"{(self.planet.get('name') or 'planet').replace(' ', '')}_transits.csv", title="Save CSV", defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")], parent=self.win)
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow([core.created_line()])
            w.writerow(["epoch", "ingress_local", "mid_local", "egress_local", "tc_bjd_tdb", "alt_in", "alt_mid",
                        "alt_out", "sun_alt_mid", "moon_illum", "moon_sep_deg", "sigma_min", "coverage"]
                       + (["alt_max", "zenith_keyhole"] if self.altaz else []))   # 2.2.18 (item 8)
            for e in self.events:
                w.writerow([e["epoch"]] + [tc.jd_to_local(e[k]).strftime("%Y-%m-%d %H:%M") for k in ("t1_jd", "tc_jd", "t4_jd")]
                           + [f"{e['tc_bjd']:.5f}", f"{e['alt_t1']:.1f}", f"{e['alt_tc']:.1f}", f"{e['alt_t4']:.1f}",
                              f"{e['sun_tc']:.1f}", f"{e['moon_illum']:.2f}", f"{e['moon_sep']:.0f}",
                              f"{e['sigma_min']:.1f}", e["status"]]
                           + ([f"{e['alt_max']:.1f}", int(bool(e.get("keyhole")))] if self.altaz else []))


def frame_number(path: str, fallback: int) -> int:
    """2.2.19 (#40): a light's frame number from the digits at the end of its file name (..._0042.fit -> 42), else
    fallback (its place in the night)."""
    m = re.search(r"(\d+)\D*$", os.path.splitext(os.path.basename(str(path or "")))[0])
    return int(m.group(1)) if m and len(m.group(1)) <= 6 else fallback


def frame_numbers(paths) -> list:
    """2.2.19 (#40, review): frame numbers for a night's frames in time order: the file names' trailing digits when
    every frame has them and they are unique and increasing with time (…_0001.fit, …_0002.fit), else 1, 2, 3 … by
    place (time-stamp names such as Qatar-9261020090627.FITS give repeated or unordered numbers)."""
    nums = []
    for k, path in enumerate(paths):
        n = frame_number(path, -1)
        if n < 0:
            break
        nums.append(n)
    if len(nums) == len(paths) and all(b > a for a, b in zip(nums, nums[1:])):
        return nums
    return list(range(1, len(paths) + 1))


def frame_ranges(nums) -> str:
    """2.2.19 (#40): [1, 2, 3, 7, 97, 98, 99] -> '1-3, 7, 97-99'."""
    v = sorted({int(n) for n in nums})
    out, k = [], 0
    while k < len(v):
        j = k
        while j + 1 < len(v) and v[j + 1] == v[j] + 1:
            j += 1
        out.append(f"{v[k]}-{v[j]}" if j > k else f"{v[k]}")
        k = j + 1
    return ", ".join(out)


def gain_note(pn: dict | None) -> str:
    """2.2.19 (#39): the gain the photon-noise error bars used and where it came from (photometry_core.
    photon_noise_setup: header EGAIN, else the sky's own noise, else 1 e-/ADU assumed). ASCII."""
    pn = pn or {}
    src = pn.get("source")
    if src == "EGAIN" and pn.get("e_per_adu"):
        return f"Gain {pn['e_per_adu']:.3g} e-/ADU (header EGAIN)"
    if src == "sky" and pn.get("k"):
        return (f"Gain from the sky's own noise, {pn['k']:.3g} e- per image unit (no EGAIN in the headers; a lower "
                "limit, so error bars are on the safe side)")
    if src == "assumed":
        return ("Gain 1 e-/ADU assumed (no EGAIN in the headers, no usable sky estimate): photon-noise error bars "
                "scale as 1/sqrt(true gain); add EGAIN to the headers if the camera's gain is known")
    if src == "old":
        return "Gain: pre-2.2.17 error model (not in electrons)"
    return "Gain not recorded for this night (photometry from before 2.2.17)"


class CornerWindow:
    """2.2.19 (#31): the corner plot of the transit fit's MCMC samples (transit_posterior.corner_figure), with a Save
    PNG button. Parameters held fixed are left out; ⚠ marks one that piles up against its allowed range. Review: by
    default only the shape parameters (Tc, Rp/R*, a/R*, b, flip step), drawn at their own size in a scrolling body, so
    the panels stay readable on a small or scaled screen; "Show all parameters" adds the baseline and limb darkening."""

    def __init__(self, app, page: TransitPage):
        self.app = app
        self.page = page
        self.result = page.result
        r = self.result
        win = Toplevel(app)
        self.win = win
        name = (r.get("planet") or {}).get("name") or "transit"
        win.title(f"SHOBS-P — corner plot ({name})")
        top = ttk.Frame(win, style="Card.TFrame", padding=10)
        top.pack(fill=BOTH, expand=True)
        buttons = ttk.Frame(top, style="Card.TFrame")
        buttons.pack(fill=X, side="bottom", pady=(6, 0))
        ttk.Button(buttons, text="Save PNG…", command=self.save).pack(side=LEFT)
        self.show_all = BooleanVar(value=False)
        ttk.Checkbutton(buttons, text="Show all parameters (baseline, limb darkening)", variable=self.show_all,
                        command=self.redraw).pack(side=LEFT, padx=(12, 0))
        ttk.Button(buttons, text="Close", command=win.destroy).pack(side=RIGHT)
        intro = ttk.Label(top, text=("Each fitted parameter's MCMC samples: histograms on the diagonal (median and "
                                     "16/84 % lines; the title gives the value and its error), pairs below it (1σ and "
                                     "2σ contours). A round hump is a measurement; a histogram that piles up against "
                                     "an edge (⚠) is set by the allowed range, not by the data. Tc is in minutes from "
                                     "its median."), style="Hint.TLabel", wraplength=700, justify=LEFT)
        intro.pack(anchor=W, fill=X)
        labels = [intro]
        for bf in r.get("bound_flags") or []:
            warn = bf.get("level", "warn") == "warn"
            lab = ttk.Label(top, text=("⚠ " if warn else "• ") + bf["text"], style="Card.TLabel",
                            foreground="#b35c00" if warn else "#33424d", wraplength=700, justify=LEFT)
            lab.pack(anchor=W, fill=X, pady=(2, 0))
            labels.append(lab)
        top.bind("<Configure>", lambda e: [lb.configure(wraplength=max(e.width - 30, 300)) for lb in labels], add="+")
        self.fig = None
        if Figure is None:
            fit_to_content(win)
            return
        scroll_cls = getattr(sys.modules.get(type(app).__module__), "ScrollBody", None)
        if scroll_cls is not None:
            self.body = scroll_cls(top, horizontal=True)
            self.body.outer.pack(fill=BOTH, expand=True, pady=(6, 0))
            host = self.body.inner
        else:
            self.body, host = None, top
        self.fig = Figure(dpi=100, facecolor="#ffffff")
        self.canvas = FigureCanvasTkAgg(self.fig, master=host)
        self.canvas.get_tk_widget().pack(anchor=W)
        self.redraw()
        fit_to_content(win)

    def _shape_only(self) -> bool:
        return not self.show_all.get()

    def redraw(self):
        """Draw the chosen parameters at the figure's own size (in pixels); the body scrolls when it is larger."""
        if self.fig is None:
            return
        n = len(tpost.corner_params(self.result, self._shape_only()))
        tpost.corner_figure(self.result, self.fig, font=9.0 if n <= 6 else 8.0, shape_only=self._shape_only())
        w_in, h_in = self.fig.get_size_inches()
        try:
            self.canvas.get_tk_widget().configure(width=int(w_in * self.fig.dpi), height=int(h_in * self.fig.dpi))
        except Exception:
            pass
        self.canvas.draw_idle()
        if self.body is not None:
            self.body.fit()

    def save(self):
        page = self.page
        stem = os.path.splitext(page.family_names()[1])[0]
        path = self.app.output_path(stem + "_corner.png", title="Save corner plot PNG", defaultextension=".png",
                                    filetypes=[("PNG", "*.png")], parent=self.win)
        if not path:
            return
        try:
            tpost.save_corner(self.result, path, shape_only=self._shape_only())
        except Exception as exc:
            messagebox.showerror("SHOBS-P", f"Could not save the corner plot: {exc}", parent=self.win)
            return
        self.app.log(f"Corner plot PNG: {path}")
        self.app.saved_notice(path)


class NebWindow:
    """Nearby eclipsing binaries: could a neighbor star, blended in TESS's big pixels, make this signal?"""

    def __init__(self, app, page: TransitPage):
        self.app = app
        self.page = page
        planet = page.current_planet() or {}
        depth = tc.published_depth(planet)[0] or (page.result["summary"]["depth"][0] if page.result else None)
        if not depth:
            messagebox.showinfo("SHOBS-P", "The NEB check needs the transit depth: enter the planet on the Input page "
                                           "(Lookup), or fit the transit first.")
            return
        try:
            self.ra, self.dec = app.target_radec_deg(planet)
        except Exception as exc:
            messagebox.showinfo("SHOBS-P", f"The NEB check needs the target's RA/Dec: {exc}")
            return
        if not app.chart_placed:
            messagebox.showinfo("SHOBS-P", "Solve the image first (Photometry page). The check lists the "
                                           "labeled stars near the target.")
            return
        self.depth = depth
        self.planet = planet
        win = Toplevel(app)
        self.win = win
        win.title("SHOBS-P — nearby eclipsing binary check")
        win.after_idle(lambda: fit_to_content(win))
        top = ttk.Frame(win, style="Card.TFrame", padding=10)
        top.pack(fill=BOTH, expand=True)
        ttk.Label(top, text="Nearby eclipsing binary (NEB) check", font=(core.UI_FONT, 15, "bold"),
                  style="Card.TLabel").pack(anchor=W)
        ttk.Label(top, text=(
            f"TESS pixels are 21″ wide, so a faint eclipsing binary near the target can fake a transit. For each labeled "
            f"star within the radius, 'needed' is the eclipse depth it would need to make the whole "
            f"{depth * 100:.2f}% signal. Over 100% means it cannot. Neighbors that could are cleared by measuring them: "
            "Watch them, run photometry on a transit night, fit the transit, then open this check again. A neighbor is "
            "cleared when its measured drop at the transit time is more than 3σ below what it would need."),
            style="Hint.TLabel", wraplength=1300, justify=LEFT).pack(anchor=W, pady=(2, 6))
        opts = ttk.Frame(top, style="Card.TFrame")
        opts.pack(fill=X)
        self.radius = DoubleVar(value=2.5)
        ttk.Label(opts, text="Radius (arcmin)", style="Card.TLabel").pack(side=LEFT)
        ttk.Entry(opts, textvariable=self.radius, width=6).pack(side=LEFT, padx=(4, 12))
        ttk.Button(opts, text="Update", command=self.update).pack(side=LEFT)
        ttk.Button(opts, text="Watch the neighbors that need checking", style="Accent.TButton",
                   command=self.watch_needed).pack(side=LEFT, padx=10)
        self.summary = StringVar(value="")
        ttk.Label(top, textvariable=self.summary, style="Card.TLabel", wraplength=1300, justify=LEFT).pack(anchor=W, pady=(6, 0))
        cols = ("star", "sep", "mag", "dmag", "needed", "measured", "verdict")
        self.tree = ttk.Treeview(top, columns=cols, show="headings", height=16)
        for col, label, width in (("star", "Star", 260), ("sep", "Distance (″)", 100), ("mag", "Mag", 70),
                                  ("dmag", "Δmag", 70), ("needed", "Needed depth", 120),
                                  ("measured", "Measured drop", 150), ("verdict", "Verdict", 300)):
            _col(self.tree, col, label, width, stretch=(col == "verdict"))
        for tag, colour in (("cleared", "#2e7d32"), ("cannot", "#6b7a85"), ("possible", "#b00020"),
                            ("check", "#b35c00"), ("noisy", "#b35c00")):
            self.tree.tag_configure(tag, foreground=colour)
        self.tree.pack(fill=BOTH, expand=True, pady=(6, 0))
        self.rows = []
        self.update()

    def _target_mag(self):
        best = None
        for _x, _y, st in self.app.chart_placed:
            if st.get("ra") is None or st.get("mag") is None:
                continue
            d = core.sky_separation_arcsec(self.ra, self.dec, st["ra"], st["dec"])
            if d < 4 and (best is None or d < best[0]):
                best = (d, st["mag"])
        if best:
            return best[1]
        return self.planet.get("vmag") or self.planet.get("tmag")

    def update(self):
        app = self.app
        tmag = self._target_mag()
        if tmag is None:
            self.summary.set("The target's magnitude is unknown (label the chart with Gaia DR3, or Lookup the planet).")
            return
        limit = float(self.radius.get()) * 60
        res = self.page.result
        night_t = None
        if res is not None and self.page.report is None:
            night_t = (res["best"]["tc"], res["summary"]["t14"][0])
        rows = []
        for x, y, st in app.chart_placed:
            if st.get("ra") is None or st.get("mag") is None:
                continue
            d = core.sky_separation_arcsec(self.ra, self.dec, st["ra"], st["dec"])
            if d < 4 or d > limit:
                continue
            name = str(st.get("auid") or st.get("label") or f"{st['ra']:.5f} {st['dec']:+.5f}")
            needed = tc.neb_required_depth(self.depth, tmag, st["mag"])
            drop, err = float("nan"), float("nan")
            if night_t is not None and any(w["name"] == name for w in app.watch_list):
                drop, err = self._measure(name, *night_t)
            if needed > 1.0:
                verdict, tag = "cannot cause it (would need over 100%)", "cannot"
            elif math.isfinite(drop) and math.isfinite(err) and err > 0:
                if (needed - drop) / err > 3:
                    verdict, tag = "cleared", "cleared"
                elif drop / err > 3 and abs(drop - needed) / err < 3:
                    verdict, tag = "POSSIBLE SOURCE: dims by about the needed depth", "possible"
                else:
                    verdict, tag = "not cleared yet (too noisy); measure again", "noisy"
            else:
                verdict, tag = "needs checking: watch it and measure a transit night", "check"
            rows.append((d, name, st, needed, drop, err, verdict, tag, (x, y)))
        rows.sort(key=lambda r: r[0])
        self.rows = rows
        self.tree.delete(*self.tree.get_children())
        for d, name, st, needed, drop, err, verdict, tag, _xy in rows:
            meas = f"{drop * 100:.2f} ± {err * 100:.2f}%" if math.isfinite(drop) else "—"
            self.tree.insert("", END, tags=(tag,), values=(
                name, f"{d:.0f}", f"{st['mag']:.2f}", f"{st['mag'] - tmag:+.2f}",
                f"{needed * 100:.1f}%" if needed <= 1 else ">100%", meas, verdict))
        counts = {t: sum(1 for r in rows if r[7] == t) for t in ("cleared", "cannot", "possible", "check", "noisy")}
        on_target = ""
        if res is not None:
            s = res["summary"]
            snr = s["depth"][0] / max(s["depth"][1], 1e-9)
            on_target = (f"On target: the target itself shows a {s['depth'][0] * 100:.2f} ± {s['depth'][1] * 100:.2f}% "
                         f"transit ({snr:.0f}σ) at Tc {s['tc'][0]:.4f}. ")
        self.summary.set(on_target + f"{len(rows)} labeled neighbors within {limit / 60:g}′ (target mag {tmag:.2f}): "
                         f"{counts['cannot']} too faint to matter, {counts['cleared']} cleared, "
                         f"{counts['check'] + counts['noisy']} still to check, {counts['possible']} possible source(s).")

    def _measure(self, name, tcen, t14):
        app = self.app
        obs, mags = app.watch_series(name)
        night = self.page.night.get()
        pairs = [(o, m) for o, m in zip(obs, mags) if o.night == night]
        if len(pairs) < 10:
            return float("nan"), float("nan")
        try:
            ra, dec = app.target_radec_deg()
            t = core.convert_times(np.array([o.jd for o, _ in pairs]), "BJD_TDB", ra, dec,
                                   float(app.lat.get()), float(app.lon.get()))
        except Exception:
            return float("nan"), float("nan")
        m = np.array([mm for _, mm in pairs])
        flux = 10 ** (-0.4 * (m - np.median(m)))
        return tc.neb_measure(t, flux, tcen, t14)

    def watch_needed(self):
        added = 0
        for _d, name, _st, _needed, drop, _err, _verdict, tag, xy in self.rows:
            if tag in ("check", "noisy") and not any(w["name"] == name for w in self.app.watch_list):
                self.app.add_watch_at(xy)
                added += 1
        self.app.status.set(f"Watching {added} neighbor(s). They are measured from the next photometry run; fit the "
                            "transit on that night, then open the NEB check again.")
        self.update()


class FinderWindow:
    """Tonight's transits: every known transiting planet that can be observed whole on one night."""

    # 2.2.18 (item 17): altitude, Sun and baseline from the shared planner defaults (transit_core)
    DEFAULTS = {"min_alt": tc.PLAN_MIN_ALT, "sun_alt": tc.PLAN_SUN_ALT, "baseline": tc.PLAN_BASELINE_MIN,
                "vmin": 9.0, "vmax": 13.5, "depth": 0.5, "moon": 0.0, "after": "", "before": "",
                "avoid_meridian": False, "avoid_keyhole": False}

    def __init__(self, app):
        import datetime as _dt

        self.app = app
        try:
            self.lat, self.lon = float(app.lat.get()), float(app.lon.get())
        except (TypeError, ValueError):
            messagebox.showinfo("SHOBS-P", "Enter your site's latitude and longitude on the Input page first.")
            return
        saved = dict(self.DEFAULTS)
        saved.update({k: v for k, v in ((app.settings or {}).get("finder") or {}).items()
                      if k not in ("after", "before")})   # the time window is for one night only
        win = Toplevel(app)
        self.win = win
        win.title("SHOBS-P — tonight's transits")
        win.after_idle(lambda: fit_to_content(win))
        top = ttk.Frame(win, style="Card.TFrame", padding=10)
        top.pack(fill=BOTH, expand=True)
        ttk.Label(top, text="Tonight's transits", font=(core.UI_FONT, 15, "bold"), style="Card.TLabel").pack(anchor=W)
        ttk.Label(top, text=(f"Site {self.lat:.4f}, {self.lon:.4f}. Every known transiting planet (NASA Exoplanet "
                             "Archive) whose whole transit, plus the baseline on both sides, is in the dark with the "
                             "star above the altitude limit. Times are local. 'After' and 'End by' limit the window "
                             "(for example, after 2300 or 23:00 to finish another target first). Double-click a row to use "
                             "that planet."),
                  style="Hint.TLabel", wraplength=1450, justify=LEFT).pack(anchor=W, pady=(2, 6))
        row = ttk.Frame(top, style="Card.TFrame")
        row.pack(fill=X)
        self.date = StringVar(value=_dt.date.today().isoformat())
        self.vars = {}

        def field(label, key, width, value):
            ttk.Label(row, text=label, style="Card.TLabel").pack(side=LEFT, padx=(0, 3))
            var = StringVar(value=str(value))
            ttk.Entry(row, textvariable=var, width=width).pack(side=LEFT, padx=(0, 10))
            self.vars[key] = var

        ttk.Label(row, text="Night of", style="Card.TLabel").pack(side=LEFT, padx=(0, 3))
        ttk.Entry(row, textvariable=self.date, width=11).pack(side=LEFT, padx=(0, 10))
        field("After", "after", 6, saved["after"])
        field("End by", "before", 6, saved["before"])
        field("Min alt (°)", "min_alt", 5, f"{saved['min_alt']:g}")
        field("Sun below (°)", "sun_alt", 5, f"{saved['sun_alt']:g}")
        field("Baseline (min)", "baseline", 5, f"{saved['baseline']:g}")
        field("V from", "vmin", 5, f"{saved['vmin']:g}")
        field("to", "vmax", 5, f"{saved['vmax']:g}")
        field("Min depth (%)", "depth", 5, f"{saved['depth']:g}")
        field("Moon ≥ (°)", "moon", 4, f"{saved['moon']:g}")
        self.avoid_meridian = BooleanVar(value=bool(saved["avoid_meridian"]))
        self.avoid_keyhole = BooleanVar(value=bool(saved.get("avoid_keyhole")))
        self.altaz = _altaz(app)   # 2.2.18 (item 8): an alt-az mount never flips; its trouble is the zenith keyhole
        if self.altaz:
            ttk.Checkbutton(row, text=f"no zenith keyhole (above {tc.ZENITH_KEYHOLE_ALT:g}°)",
                            variable=self.avoid_keyhole).pack(side=LEFT, padx=(0, 10))
        else:
            ttk.Checkbutton(row, text="no meridian flip", variable=self.avoid_meridian).pack(side=LEFT, padx=(0, 10))
        row2 = ttk.Frame(top, style="Card.TFrame")
        row2.pack(fill=X, pady=(6, 0))
        ttk.Button(row2, text="Search", style="Accent.TButton", command=self.search).pack(side=LEFT)
        ttk.Button(row2, text="Refresh planet list", command=lambda: self.search(refresh=True)).pack(side=LEFT, padx=6)
        ttk.Button(row2, text="Same search on Swarthmore…", command=self.open_swarthmore).pack(side=LEFT)
        ttk.Button(row2, text="Export CSV…", command=self.export).pack(side=LEFT, padx=6)
        self.summary = StringVar(value="")
        ttk.Label(row2, textvariable=self.summary, style="Hint.TLabel").pack(side=LEFT, padx=10)
        cols = ("name", "v", "depth", "dur", "start", "t1", "tc", "t4", "end", "alt", "moon", "flip")
        self.tree = ttk.Treeview(top, columns=cols, show="headings", height=20)
        for col, label, width in (("name", "Planet", 170), ("v", "V", 55), ("depth", "Depth (%)", 85),
                                  ("dur", "T14 (h)", 70), ("start", "Start (with baseline)", 150),
                                  ("t1", "Ingress", 75), ("tc", "Mid", 70), ("t4", "Egress", 70),
                                  ("end", "End (with baseline)", 140), ("alt", "Altitude min–max", 130),
                                  ("moon", "Moon (lit, distance)", 150),
                                  ("flip", "Zenith" if self.altaz else "Meridian", 90)):
            _col(self.tree, col, label, width, stretch=(col == "name"))
        scroll = ttk.Scrollbar(top, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True, pady=(8, 0))
        scroll.pack(side=LEFT, fill=Y, pady=(8, 0))
        self.tree.bind("<Double-1>", self.use_selected)
        self.events = []
        self.rows = {}
        self.search()

    # ---- inputs --------------------------------------------------------------------------------
    def _night(self):
        import datetime as _dt
        return _dt.date.fromisoformat(self.date.get().strip())

    def _clock_jd(self, text, night):
        """'23:30' on this night (hours before noon belong to the next morning) -> JD UTC, or None."""
        import datetime as _dt
        h, m = parse_clock(text)
        if h is None:
            return None
        day = night + _dt.timedelta(days=1) if h < 12 else night
        local = _dt.datetime.combine(day, _dt.time(h % 24, m)).astimezone()
        return local.timestamp() / 86400.0 + 2440587.5

    def _limits(self):
        v = {k: var.get().strip() for k, var in self.vars.items()}
        return {"min_alt": float(v["min_alt"]), "sun_alt": float(v["sun_alt"]), "baseline": float(v["baseline"]),
                "vmin": float(v["vmin"]), "vmax": float(v["vmax"]), "depth": float(v["depth"]),
                "moon": float(v["moon"] or 0), "after": v["after"], "before": v["before"],
                "avoid_meridian": bool(self.avoid_meridian.get()), "avoid_keyhole": bool(self.avoid_keyhole.get())}

    # ---- search ---------------------------------------------------------------------------------
    def search(self, refresh=False):
        try:
            night = self._night()
            lim = self._limits()
            after = self._clock_jd(lim["after"], night)
            before = self._clock_jd(lim["before"], night)
        except Exception as exc:
            messagebox.showerror("SHOBS-P", f"Check the search fields: {exc}", parent=self.win)
            return
        self.app.settings["finder"] = lim
        self.app._save_settings()
        self.summary.set("Loading the planet list…")
        app = self.app

        def work():
            try:
                planets, note = tc.load_transit_catalog(refresh=refresh)
                events, counts = tc.find_transits(
                    planets, night, self.lat, self.lon, min_alt=lim["min_alt"], sun_alt=lim["sun_alt"],
                    baseline_min=lim["baseline"], vmin=lim["vmin"], vmax=lim["vmax"], min_depth=lim["depth"] / 100.0,
                    min_moon_deg=lim["moon"], after_jd=after, before_jd=before,
                    avoid_meridian=lim["avoid_meridian"] and not self.altaz,   # 2.2.18 (item 8)
                    avoid_keyhole=lim["avoid_keyhole"] and self.altaz)
                app.call_ui(self._show, events, counts, note, None)
            except Exception as exc:
                app.call_ui(self._show, [], {}, "", exc)

        threading.Thread(target=work, daemon=True).start()

    def _show(self, events, counts, note, error):
        if error is not None:
            self.summary.set("Search failed.")
            messagebox.showerror("SHOBS-P", f"Tonight's transits: {error}", parent=self.win)
            return
        self.events = events
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        fmt = lambda jd: tc.jd_to_local(jd).strftime("%H:%M")
        for e in events:
            item = self.tree.insert("", END, values=(
                e["name"], f"{e['vmag']:.1f}", f"{e['depth'] * 100:.2f}", f"{e['t14'] * 24:.2f}", fmt(e["start"]),
                fmt(e["t1"]), fmt(e["tc"]), fmt(e["t4"]), fmt(e["end"]), f"{e['alt_min']:.0f}°–{e['alt_max']:.0f}°",
                f"{e['moon_illum'] * 100:.0f}%, {e['moon_sep']:.0f}°",
                ("keyhole" if e.get("keyhole") else "") if self.altaz else ("flip" if e["meridian"] else "")))
            self.rows[item] = e
        dark = counts.get("dark")
        dark_text = (f"dark {tc.jd_to_local(dark[0]).strftime('%H:%M')}–{tc.jd_to_local(dark[1]).strftime('%H:%M')}"
                     if dark else "no dark time at this Sun limit")
        self.summary.set(f"{len(events)} transit(s); {dark_text}; {counts.get('checked', 0)} planets in the magnitude "
                         f"and depth range; planet list: {note}.")
        if not events:
            self.tree.insert("", END, values=("No transits match. Loosen a limit (altitude, Sun, magnitude, depth, "
                                              "or the time window).",) + ("",) * 11)

    # ---- actions --------------------------------------------------------------------------------
    def use_selected(self, _event=None):
        sel = self.tree.selection()
        e = self.rows.get(sel[0]) if sel else None
        if not e:
            return
        app = self.app
        if app.mode_key != "transits":
            app.set_mode("transits")
        app.planet_name.set(e["name"])
        app._last_planet_lookup = e["name"]   # 2.2.14 (item 2): looked up below; the box's auto lookup skips it
        if not app.star_id.get().strip() or messagebox.askyesno(
                "SHOBS-P", f"Use {e['name']} as the planet, and clear the Input page's target ({app.star_id.get()}) "
                           "and RA/Dec so the lookup fills them?", parent=self.win):
            # 2.2.16 (item 11): the old target's whole field goes (comps, check, spares, session watch stars).
            if app.star_id.get().strip() and not core.same_object_name(app.star_id.get(), e["name"]):
                # 2.2.19 (#20): a new reduction (the old series and its Use comps choice go too); the planet stays.
                app._switch_star(f"{e['name']} chosen in the transit planner", keep_planet=True)
            app.star_id.set("")
            app.ra_hours.set("")
            app.dec_deg.set("")
        app.lookup_planet()
        app.show_step(0)
        app.status.set(f"{e['name']}: looking it up. Transit {tc.jd_to_local(e['t1']).strftime('%H:%M')}–"
                       f"{tc.jd_to_local(e['t4']).strftime('%H:%M')}, start by {tc.jd_to_local(e['start']).strftime('%H:%M')}.")

    def open_swarthmore(self):
        import webbrowser
        try:
            lim = self._limits()
            night = self._night()
        except Exception as exc:
            messagebox.showerror("SHOBS-P", f"Check the search fields: {exc}", parent=self.win)
            return
        url = tc.swarthmore_url(self.lat, self.lon, min_alt=lim["min_alt"], baseline_hrs=lim["baseline"] / 60.0,
                                min_depth_ppt=lim["depth"] * 10.0, vmax=lim["vmax"],
                                twilight=int(min([-1, -6, -12, -18], key=lambda t: abs(t - lim["sun_alt"]))),
                                start_date=night.strftime("%m-%d-%Y"))
        webbrowser.open(url)
        self.app.log("Opened the Swarthmore Transit Finder with this site and these limits (E. Jensen, Tapir).")

    def export(self):
        if not self.events:
            return
        path = self.app.output_path(f"transits_{self.date.get().strip()}.csv", title="Save CSV", defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")], parent=self.win)
        if not path:
            return
        fmt = lambda jd: tc.jd_to_local(jd).strftime("%Y-%m-%d %H:%M")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow([core.created_line()])
            w.writerow(["planet", "V", "depth_pct", "T14_h", "start_local", "ingress_local", "mid_local", "egress_local",
                        "end_local", "alt_min", "alt_max", "moon_illum", "moon_sep_deg",
                        "zenith_keyhole" if self.altaz else "meridian_flip"])
            for e in self.events:
                w.writerow([e["name"], f"{e['vmag']:.2f}", f"{e['depth'] * 100:.3f}", f"{e['t14'] * 24:.3f}",
                            fmt(e["start"]), fmt(e["t1"]), fmt(e["tc"]), fmt(e["t4"]), fmt(e["end"]),
                            f"{e['alt_min']:.1f}", f"{e['alt_max']:.1f}", f"{e['moon_illum']:.2f}",
                            f"{e['moon_sep']:.0f}", int(bool(e.get("keyhole"))) if self.altaz else int(e["meridian"])])
