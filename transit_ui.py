"""Transits mode (2.1): the Transit fit page, the planner, the nearby-eclipsing-binary check, and the
ExoFOP package. Built by the App in clearv_app.py; everything here reaches the App through `app`."""
from __future__ import annotations

import csv
import math
import os
import threading
import traceback
import zipfile
from tkinter import BOTH, END, LEFT, RIGHT, VERTICAL, W, X, Y, BooleanVar, DoubleVar, StringVar, Text, Toplevel, \
    filedialog, messagebox, ttk

import numpy as np

import photometry_core as core
import transit_core as tc

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
        ttk.Button(results, text="Save ExoFOP package…", command=self.save_package).pack(side=LEFT, padx=6)
        ttk.Button(results, text="Save light-curve CSV…", command=app.save_csv).pack(side=LEFT)
        # 2.2.7: the plot as shown, with the verdict and results above it; works for report fits too.
        ttk.Button(results, text="Save plot PNG…", command=self.save_png).pack(side=LEFT, padx=6)
        # Bottom right, the same place as "Transit View" on the Variables page (2.2.1).
        ttk.Button(results, text="Variable View  ▶", style="Accent.TButton", command=app.show_variables_view).pack(
            side=RIGHT)
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
        self.table.tag_configure("grey", foreground="#9aa7b0")
        self.table.tag_configure("greywarn", foreground="#d9b38c")
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
        self._check_stale(auto=shown)
        self._count_text()

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
                target = app.planet_name.get().strip() or app.star_id.get().strip() or "your"
                kept = (f" Your {target} photometry is kept; choose its night above (or Close report) to go back.")
            self.banner.set(f"Fitting an EXOTIC report: {name}, {self._report_date()}, {len(r['t'])} points from "
                            f"{os.path.basename(r.get('path', ''))}." + kept)
            try:
                self.close_report_button.pack(side=LEFT, padx=(0, 6), after=self.night_box)
            except Exception:
                pass
            self._show_flip_check(True)
            app.set_report_note(f"The Transit fit page is showing an EXOTIC report for {name}, not the photometry "
                                "set up here.")
        else:
            self.banner.set("")
            try:
                self.close_report_button.pack_forget()
            except Exception:
                pass
            self._show_flip_check(False)
            app.set_report_note("")

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
        self.source_text.set(text)

    def _signature(self):
        """What a fit depends on: the night's usable points (after comps, flags, and cloud cuts) and the planet."""
        if self.report is not None:
            return ("report", id(self.report))
        night = self.night.get()
        obs = [o for o in self.app._usable() if o.night == night]
        if not obs:
            return None
        mags = [o.mag for o in obs if math.isfinite(o.mag)]
        return (night, len(obs), round(float(sum(mags)), 6), round(float(sum(o.jd for o in obs)), 6),
                tuple(self.app.active_comps or self.app.comps_used), planet_summary(self.current_planet()),
                str(self.app._scint_setup() if hasattr(self.app, "_scint_setup") else None))

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
        self._clear_progress()
        self.report = rep
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
        self.draw(placeholder=f"{name}, {self._report_date()}, {len(rep['t'])} points from EXOTIC: {what}")
        self._notes_text([("info", "Report loaded and being fitted. SHOBS-P refits its light curve and lists "
                                   "EXOTIC's results beside SHOBS-P's. Cancel stops the fit; Fit transit runs it "
                                   "again with other settings.")])
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
            self.draw(placeholder=f"{name}, {self._report_date()}, {len(rep['t'])} points from EXOTIC: fitting…")
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
                "exposure": f"{np.median(exps):.1f}" if exps else "", "binning": f"{app.binning.get()}x{app.binning.get()}",
                "filter": obs[0].filt or "CV", "notes": app.notes.get().strip(),
                "aperture": f"r {app.radius.get():g} px, sky {app.sky_in.get():g}-{app.sky_out.get():g} px",
                "comps": ", ".join(app.active_comps or app.comps_used), "source": f"night {night}",
                "scint": app._scint_note() if hasattr(app, "_scint_note") else ""}
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
                 "flip_source": "the frames' flip segment", "flagged": None}
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
        self._data_extra = extra
        return t, flux, err, am, meta

    # ---- fit -------------------------------------------------------------------------------
    def fit(self, auto: bool = False, with_flagged: bool = False):
        # 2.2.12: a report's ephemeris is still being looked up: the fit starts by itself when it is in.
        if self.report is not None and getattr(self, "_eph_pending", None) is self.report:
            if not auto:
                messagebox.showinfo("SHOBS-P", "Still looking up this planet's ephemeris; the fit starts by itself "
                                               "as soon as it is in.")
            return
        planet = self.current_planet()
        if not planet:
            if not auto:
                messagebox.showinfo("SHOBS-P", "Enter the planet on the Input page first (name, then Lookup), or open "
                                               "an exoplanet report.")
            return
        try:
            self._data_extra = {}
            t, flux, err, am, meta = self._data()
        except Exception as exc:
            if auto:
                self._notes_text([("info", str(exc))])
            else:
                messagebox.showerror("SHOBS-P", str(exc))
            return
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
        """A new source: the last fit's message no longer applies (unless a fit is still running)."""
        if not self._fitting:
            self.prog_text.set("")
            self._show_progress()

    def _show_progress(self):
        """2.2.10 (John): the bar only while a fit runs; the row only while it has something to say."""
        try:
            self.bar.pack_forget()
            self.prog_label.pack_forget()
            if self._fitting:
                self.bar.pack(side=LEFT)
            if self._fitting or self.prog_text.get():
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

    def _finish_fit(self, result, meta, error):
        if error is not None:
            self.prog_text.set("Cancelled." if error[0] == "Cancelled" else "Fit failed.")
            self._show_progress()     # before any dialog, so the bar is not left showing behind it
            self.draw(placeholder="Not fitted yet: press Fit transit." if error[0] == "Cancelled" else "Fit failed.")
            if error[0] != "Cancelled":
                self.app.log(error[1])
                messagebox.showerror("SHOBS-P", f"Transit fit failed: {error[0]}")
            return
        if self._signature() != getattr(self, "_pending_signature", None):
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

    # ---- display ---------------------------------------------------------------------------
    def _set_columns(self):
        """The EXOTIC column only when a report is loaded."""
        cols = ["param", "shobs", "free", "pub"] + (["exotic"] if self.report is not None else [])
        self._display_cols = cols
        self.table.configure(displaycolumns=cols)

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
                  "exotic": "EXOTIC report"}
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
            self.fig.subplots_adjust(left=left, right=right, top=1 - min(top / h, 0.2), bottom=bottom)
            if getattr(self, "_single_panel", False):
                # The light curve alone (no residuals): the flux panel takes the whole plot.
                self.ax.set_position([left, bottom, right - left, (1 - min(top / h, 0.2)) - bottom])
        except Exception:
            pass

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
             num((r or {}).get("tc_pred"), "{:.5f}"), exo("Tc", "{:.5f}")),
            ("Rp/R*", val("k", "{:.4f}"), num(ff.get("k"), "{:.4f}"), num(p.get("k"), "{:.4f}"), exo("Rp/R*", "{:.4f}")),
            # 2.2.13: the published depth is (Rp/R*)² when Rp/R* is known, the same value the checks compare with.
            ("Depth (%)", val("depth", "{:.2f}", 100), num(ff.get("k", float("nan")) ** 2, "{:.2f}", 100),
             num(tc.published_depth(p)[0], "{:.2f}", 100), exo("Radius-ratio area depth (Rp/R*)^2", "{:.2f}")),
            ("a/R*", val("a_rs", "{:.2f}"), num(ff.get("a_rs"), "{:.2f}"), num(p.get("a_rs"), "{:.2f}"), exo("a/R*", "{:.2f}")),
            ("Impact parameter b", val("b", "{:.3f}"), num(ff.get("b"), "{:.3f}"), num(p.get("b"), "{:.3f}"),
             exo("Impact Parameter (b)", "{:.3f}")),
            ("Inclination (°)", val("inc", "{:.2f}"), "", num(p.get("inc"), "{:.2f}"), exo("inc", "{:.2f}")),
            ("Duration T14 (h)", val("t14", "{:.3f}", 24), num((r or {}).get("free_fit", {}).get("t14") if r and r.get("free_fit") else None, "{:.3f}", 24),
             num(p.get("t14"), "{:.3f}", 24), exo("Duration", "{:.3f}", 24)),
            ("Limb darkening u1", val("u1", "{:.2f}"), "", num((r or {}).get("ld_prior", [None])[0] if r else None, "{:.2f}"), ""),
            ("Limb darkening u2", val("u2", "{:.2f}"), "", num((r or {}).get("ld_prior", [None, None])[1] if r else None, "{:.2f}"), ""),
            ("Airmass slope", val("c_am", "{:.4f}"), "", "", ""),
        ]
        if r:
            oc = ((r["best"]["tc"] - r["tc_pred"]) * 1440
                  if r.get("tc_pred") is not None and r.get("tc_pred_err") is not None else None)
            sig = math.hypot(s["tc"][1], r["tc_pred_err"]) * 1440 if oc is not None else None
            rows.insert(1, ("O − C (min)", f"{oc:+.2f} ± {sig:.2f}" if oc is not None else "", "", "0", ""))
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
                tags = ("greywarn",) if row[0] in warn_names else ("grey",)
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
        """The fit; or, with no fit, a labelled empty frame with the placeholder; or (2.2.11) preview=(t, flux,
        note): the night's light curve alone, when no transit is predicted during it."""
        if self.fig is None:
            return
        r = self.result
        ax, axr = self.ax, self.ax_r
        ax.clear()
        axr.clear()
        self._has_title = bool(r)
        self._single_panel = False
        axr.set_visible(True)
        ax.tick_params(axis="x", labelbottom=False)
        if not r and preview is not None:
            self._draw_preview(*preview)
            return
        if not r:
            # 2.2.9 (John): an empty frame labelled like the fitted plot, no overlapping default ticks.
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
                    label=f"EXOTIC: Rp/R* {ex['Rp/R*'][0]:.3f}, b {b_ex:.2f}")
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
        ax.set_ylabel("Relative flux")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, loc="lower left", frameon=False)
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
        if t14 / 2 >= xd_lo and -t14 / 2 <= xd_hi:
            x_lo, x_hi = min(xd_lo, -t14 / 2) - 0.25, max(xd_hi, t14 / 2) + 0.25
        else:
            # "Fit anyway" pulled to a Tc away from the data: show the data, not the empty hours between.
            x_lo, x_hi = xd_lo - 0.25, xd_hi + 0.25
        ax.set_xlim(x_lo, x_hi)
        for label in ax.get_xticklabels():
            label.set_visible(False)
        self._set_margins()
        self.canvas.draw_idle()

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
        fnote = tc.flip_report_note(self.result or {})
        if fnote:
            meta["notes"] = (meta.get("notes", "") + " | " if meta.get("notes") else "") + ascii_(fnote)
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
        meta = self.meta_for_files or {}
        name = ((self.result.get("planet") or {}).get("name") or "planet").replace(" ", "")
        filt = str(meta.get("filter") or "CV").strip() or "CV"
        date = str(meta.get("obsdate") or "").strip()
        fname = f"{name}_{filt}_{date}_SHOBS-P_transit.png" if date else f"{name}_{filt}_SHOBS-P_transit.png"
        path = self.app.output_path(fname, title="Save transit plot PNG", defaultextension=".png",
                                    filetypes=[("PNG", "*.png")], parent=self.app)
        if not path:
            return
        try:
            self._write_png(path)
        except Exception as exc:
            messagebox.showerror("SHOBS-P", f"Could not save the PNG: {exc}")
            return
        self.app.status.set(f"Wrote {path}")
        self.app.log(f"Transit plot PNG: {path}")

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
                  "exotic": "EXOTIC report"}
        heads = [labels[c] for c in cols]
        rows = [heads]
        for iid in self.table.get_children():
            try:
                vals = list(self.table.item(iid).get("values") or [])
            except Exception:
                vals = []
            by_col = dict(zip(all_cols, [str(v) for v in vals]))
            rows.append([by_col.get(c, "") for c in cols])
        notes = [ln.strip() for ln in self.notes.get("1.0", END).split("\n") if ln.strip()]
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
        if not self._confirm_export():
            return
        meta = self._file_meta()
        name = (self.result["planet"].get("name") or "planet").replace(" ", "")
        path = self.app.output_path(f"AAVSO_{name}_{meta.get('obsdate', '')}_SHOBS-P.txt",
                                    title="Save AAVSO exoplanet report", defaultextension=".txt",
                                    filetypes=[("Text", "*.txt")], parent=self.app)
        if not path:
            return
        with core.AsciiWriter(path, newline="\n") as fh:
            fh.write(tc.aavso_exoplanet_report(self.result, meta))
        self.app.status.set(f"Saved {os.path.basename(path)}. Check AAVSO's current exoplanet instructions before "
                            "uploading.")

    def save_package(self):
        if not self.result:
            messagebox.showinfo("SHOBS-P", "Fit a transit first.")
            return
        if not self._confirm_export():
            return
        folder = self.app.output_directory("Folder for the ExoFOP package", parent=self.app)
        if not folder:
            return
        r = self.result
        meta = self._file_meta()
        planet = (r["planet"].get("name") or "planet").replace(" ", "")
        from datetime import datetime, timezone
        date = datetime.fromtimestamp((float(r["t"].min()) - 2440587.5) * 86400.0, tz=timezone.utc).strftime("%Y%m%d")
        base = f"{planet}_{date}_{meta.get('obscode') or 'obs'}_SHOBS-P"
        # Never write into an earlier package: a second save of the same night becomes ..._2 (2.2).
        out, k = os.path.join(folder, base), 2
        while os.path.exists(out) or os.path.exists(out + ".zip"):
            out, k = os.path.join(folder, f"{base}_{k}"), k + 1
        base = os.path.basename(out)
        os.makedirs(out, exist_ok=True)
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
            fh.write("\nFor ExoFOP (time-series upload)\n"
                     f"  Telescope/camera: {meta.get('notes', '')}\n"
                     f"  Filter: {meta.get('filter', 'CV')} ("
                     + ("monochrome camera" if self.app.pattern.get() == core.MONO else "one-shot color camera")
                     + ", untransformed)\n"
                     f"  Photometric aperture: {meta.get('aperture', '')}\n"
                     f"  Coverage: {r['t'].min():.5f} to {r['t'].max():.5f} BJD_TDB\n"
                     f"  Result: Tc {r['summary']['tc'][0]:.5f} ± {r['summary']['tc'][1]:.5f}, depth "
                     f"{r['summary']['depth'][0] * 1e6:.0f} ± {r['summary']['depth'][1] * 1e6:.0f} ppm\n")
        files.append(txt)
        rep = os.path.join(out, f"AAVSO_{planet}_{meta.get('obsdate', '')}_SHOBS-P.txt")
        with core.AsciiWriter(rep, newline="\n") as fh:
            fh.write(tc.aavso_exoplanet_report(r, meta))
        files.append(rep)
        if self.fig is not None:
            png = os.path.join(out, f"{base}_plot.png")
            self._save_fig(png, stamp=True, dpi=150)
            files.append(png)
        zpath = out + ".zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                z.write(f, arcname=os.path.join(base, os.path.basename(f)))
        self.app.log(f"ExoFOP package: {out} (and {os.path.basename(zpath)})")
        self.app.saved_notice(out, f"Package folder and {os.path.basename(zpath)}: light curve (CSV), fit and ExoFOP "
                                   "notes (text), plot, and an AAVSO exoplanet report.")


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
        self.min_alt = DoubleVar(value=25)
        self.sun_alt = DoubleVar(value=-12)
        self.baseline = DoubleVar(value=60)
        self.full_only = BooleanVar(value=False)
        for label, var, width in (("Days ahead", self.days, 6), ("Min altitude (°)", self.min_alt, 6),
                                  ("Sun below (°)", self.sun_alt, 6), ("Baseline each side (min)", self.baseline, 6)):
            ttk.Label(opts, text=label, style="Card.TLabel").pack(side=LEFT, padx=(0, 4))
            ttk.Entry(opts, textvariable=var, width=width).pack(side=LEFT, padx=(0, 12))
        ttk.Checkbutton(opts, text="full only", variable=self.full_only).pack(side=LEFT)
        ttk.Button(opts, text="Update", style="Accent.TButton", command=self.update).pack(side=LEFT, padx=10)
        ttk.Button(opts, text="Export CSV…", command=self.export).pack(side=LEFT)
        cols = ("date", "t1", "tc", "t4", "alt", "sun", "moon", "sigma", "status")
        self.tree = ttk.Treeview(top, columns=cols, show="headings", height=18)
        for col, label, width in (("date", "Night of", 150), ("t1", "Ingress", 80), ("tc", "Mid", 80), ("t4", "Egress", 80),
                                  ("alt", "Altitude in / mid / out", 190), ("sun", "Sun at mid", 100),
                                  ("moon", "Moon (lit, distance)", 170), ("sigma", "± ephemeris", 110),
                                  ("status", "Coverage", 140)):
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
                f"{e['moon_illum'] * 100:.0f}%, {e['moon_sep']:.0f}°", f"{e['sigma_min']:.1f} min", e["status"]))
        if not events:
            self.tree.insert("", END, values=("No observable transits in this range.", "", "", "", "", "", "", "", ""))

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
                        "alt_out", "sun_alt_mid", "moon_illum", "moon_sep_deg", "sigma_min", "coverage"])
            for e in self.events:
                w.writerow([e["epoch"]] + [tc.jd_to_local(e[k]).strftime("%Y-%m-%d %H:%M") for k in ("t1_jd", "tc_jd", "t4_jd")]
                           + [f"{e['tc_bjd']:.5f}", f"{e['alt_t1']:.1f}", f"{e['alt_tc']:.1f}", f"{e['alt_t4']:.1f}",
                              f"{e['sun_tc']:.1f}", f"{e['moon_illum']:.2f}", f"{e['moon_sep']:.0f}",
                              f"{e['sigma_min']:.1f}", e["status"]])


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

    DEFAULTS = {"min_alt": 30.0, "sun_alt": -18.0, "baseline": 60.0, "vmin": 9.0, "vmax": 13.5, "depth": 0.5,
                "moon": 0.0, "after": "", "before": "", "avoid_meridian": False}

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
                                  ("moon", "Moon (lit, distance)", 150), ("flip", "Meridian", 90)):
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
                "avoid_meridian": bool(self.avoid_meridian.get())}

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
                    min_moon_deg=lim["moon"], after_jd=after, before_jd=before, avoid_meridian=lim["avoid_meridian"])
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
                f"{e['moon_illum'] * 100:.0f}%, {e['moon_sep']:.0f}°", "flip" if e["meridian"] else ""))
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
                        "end_local", "alt_min", "alt_max", "moon_illum", "moon_sep_deg", "meridian_flip"])
            for e in self.events:
                w.writerow([e["name"], f"{e['vmag']:.2f}", f"{e['depth'] * 100:.3f}", f"{e['t14'] * 24:.3f}",
                            fmt(e["start"]), fmt(e["t1"]), fmt(e["tc"]), fmt(e["t4"]), fmt(e["end"]),
                            f"{e['alt_min']:.1f}", f"{e['alt_max']:.1f}", f"{e['moon_illum']:.2f}",
                            f"{e['moon_sep']:.0f}", int(e["meridian"])])
