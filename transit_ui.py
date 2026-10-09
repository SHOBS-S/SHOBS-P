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
        heading = tkfont.Font(widget, font=("Segoe UI", 10, "bold"))
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
    if p.get("depth"):
        parts.append(f"depth {p['depth'] * 100:.2f}%")
    if p.get("k"):
        parts.append(f"Rp/R* {p['k']:.4f}")
    if p.get("a_rs"):
        parts.append(f"a/R* {p['a_rs']:.2f}")
    if p.get("b") is not None:
        parts.append(f"b {p['b']:.2f}")
    if p.get("source"):
        parts.append(f"({p['source']})")
    return "   ".join(parts)


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
        ttk.Label(card, text="Transit fit", font=("Segoe UI", 16, "bold"), style="Card.TLabel").pack(anchor=W)
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

        row3 = ttk.Frame(card, style="Card.TFrame")
        row3.pack(fill=X, pady=(4, 2))
        ttk.Button(row3, text="Save ExoFOP package…", command=self.save_package).pack(side=LEFT)
        ttk.Button(row3, text="Save AAVSO exoplanet report…", command=self.save_aavso).pack(side=LEFT, padx=6)
        ttk.Button(row3, text="Save light-curve CSV…", command=app.save_csv).pack(side=LEFT)
        # 2.2.7: the plot as shown, with the verdict and results above it; works for report fits too.
        ttk.Button(row3, text="Save plot PNG…", command=self.save_png).pack(side=LEFT, padx=6)
        row4 = ttk.Frame(card, style="Card.TFrame")
        row4.pack(fill=X, pady=(2, 2))
        ttk.Button(row4, text="Tonight's transits…", command=lambda: FinderWindow(app)).pack(side=LEFT)
        ttk.Button(row4, text="Transit planner…", command=lambda: PlannerWindow(app)).pack(side=LEFT, padx=(6, 0))
        ttk.Button(row4, text="Nearby-binary (NEB) check…", command=lambda: NebWindow(app, self)).pack(side=LEFT, padx=6)

        prog = ttk.Frame(card, style="Card.TFrame")
        prog.pack(fill=X, side="bottom", pady=(6, 0))
        # Bottom right, the same place as "Transit View" on the Variables page (2.2.1).
        ttk.Button(prog, text="Variable View  ▶", style="Accent.TButton", command=app.show_variables_view).pack(
            side=RIGHT)
        self.bar = ttk.Progressbar(prog, length=320, mode="determinate", maximum=1)
        self.bar.pack(side=LEFT)
        self.prog_text = StringVar(value="")
        ttk.Label(prog, textvariable=self.prog_text, style="Hint.TLabel").pack(side=LEFT, padx=8)

        # 2.2.7: which data the page is fitting, in plain words (a loaded report, or a night of photometry).
        self.banner = StringVar(value="")
        self.banner_label = ttk.Label(card, textvariable=self.banner, style="Card.TLabel", wraplength=1100,
                                      justify=LEFT, foreground="#8a4b00", font=("Segoe UI", 10, "bold"))
        self.banner_label.pack(anchor=W, fill=X, pady=(4, 0))
        card.bind("<Configure>", lambda e: self.banner_label.configure(wraplength=max(e.width - 40, 300)), add="+")
        # Plot and results side by side; drag the divider to give the table more room.
        body = ttk.PanedWindow(card, orient="horizontal")
        body.pack(fill=BOTH, expand=True, pady=(6, 0))
        plot = ttk.Frame(body, style="Card.TFrame")
        side = ttk.Frame(body, style="Card.TFrame")
        body.add(plot, weight=3)
        body.add(side, weight=2)
        self.verdict = StringVar(value="")
        self.verdict_label = ttk.Label(side, textvariable=self.verdict, style="Card.TLabel", wraplength=300,
                                       justify=LEFT, font=("Segoe UI", 11, "bold"))
        self.verdict_label.pack(anchor=W, fill=X, pady=(0, 6))
        side.bind("<Configure>", lambda e: self.verdict_label.configure(wraplength=max(e.width - 20, 200)))
        tablebox = ttk.Frame(side, style="Card.TFrame")
        tablebox.pack(fill=X)
        cols = ("param", "shobs", "free", "pub", "exotic")
        self.table = ttk.Treeview(tablebox, columns=cols, show="headings", height=15)
        for col, label, width in (("param", "Parameter", 150), ("shobs", "SHOBS-P (MCMC)", 170),
                                  ("free", "Free shape", 90), ("pub", "Published / prior", 130),
                                  ("exotic", "EXOTIC report", 150)):
            _col(self.table, col, label, width, stretch=(col == "param"))
            # Columns may shrink (the scroll bar reaches the rest), so the table never forces the page wider
            # than the screen.
            self.table.column(col, minwidth=40)
        self.table.tag_configure("warn", foreground="#b35c00")
        self.table.tag_configure("grey", foreground="#9aa7b0")
        self.table.tag_configure("greywarn", foreground="#d9b38c")
        xscroll = ttk.Scrollbar(tablebox, orient="horizontal", command=self.table.xview)
        self.table.configure(xscrollcommand=xscroll.set)
        self.table.pack(fill=X)
        xscroll.pack(fill=X)
        self._set_columns()
        self.notes = Text(side, height=12, wrap="word", font=("Segoe UI", 9), relief="flat", bg="#ffffff")
        self.notes.tag_configure("warn", foreground="#b35c00")
        self.notes.tag_configure("info", foreground="#33424d")
        self.notes.pack(fill=BOTH, expand=True, pady=(6, 0))
        self.fig = None
        if Figure is not None:
            self.fig = Figure(figsize=(8, 5.4), dpi=100, facecolor="#ffffff")
            gs = self.fig.add_gridspec(2, 1, height_ratios=(3, 1), hspace=0.06)
            self.ax = self.fig.add_subplot(gs[0])
            self.ax_r = self.fig.add_subplot(gs[1], sharex=self.ax)
            self.canvas = FigureCanvasTkAgg(self.fig, master=plot)
            # Ask for a small size and let the pane stretch it: the figure's own 8-inch width (more on a scaled
            # display) pushed the page past the screen edge (2.2).
            self.canvas.get_tk_widget().configure(width=420, height=320)
            self.canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        self._notes_text([("info", "Pick a night of a Transits series (or open an AAVSO exoplanet report from "
                                   "EXOTIC), then Fit transit. The fit always runs an MCMC, with the published "
                                   "a/R* and impact parameter as priors, airmass detrending, and error bars "
                                   "scaled for red noise.")])

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

    def _night_picked(self):
        key = self._night_keys.get(self.night_label.get())
        if key:
            self.night.set(key)
        self._use_night()

    def close_report(self, quiet: bool = False):
        """2.2.7: back to the photometry (newest night), dropping a loaded EXOTIC report."""
        if self.report is None:
            return
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
            app.set_report_note(f"The Transit fit page is showing an EXOTIC report for {name}, not the photometry "
                                "set up here.")
        else:
            self.banner.set("")
            try:
                self.close_report_button.pack_forget()
            except Exception:
                pass
            app.set_report_note("")

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
        self.report = None
        self._show_source()
        self.result = None
        self._fit_signature = None
        self._fill_table()
        self.draw()
        self.source_night = self.night.get()
        n = sum(1 for o in self.app._usable() if o.night == self.source_night)
        self.source_text.set(f"{n} usable points on {self.source_night}")
        self.planet_text.set(planet_summary(self.current_planet()))
        if auto:
            self._check_stale(auto=True)

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
        what = "fitting…" if not self.app.busy else "press Fit transit when the current job ends"
        self.draw(placeholder=f"{name}, {self._report_date()}, {len(rep['t'])} points from EXOTIC: {what}")
        self._notes_text([("info", "Report loaded and being fitted. SHOBS-P refits its light curve and lists "
                                   "EXOTIC's results beside SHOBS-P's. Cancel stops the fit; Fit transit runs it "
                                   "again with other settings.")])
        if not self.app.busy:
            self.fit(auto=True)

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
            return np.asarray(r["t"]), np.asarray(r["flux"]), np.asarray(r["err"]), am, meta
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
        return t, flux, err, am, meta

    # ---- fit -------------------------------------------------------------------------------
    def fit(self, auto: bool = False):
        planet = self.current_planet()
        if not planet:
            if not auto:
                messagebox.showinfo("SHOBS-P", "Enter the planet on the Input page first (name, then Lookup), or open "
                                               "an exoplanet report.")
            return
        try:
            t, flux, err, am, meta = self._data()
        except Exception as exc:
            if auto:
                self._notes_text([("info", str(exc))])
            else:
                messagebox.showerror("SHOBS-P", str(exc))
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
        cancel = self.app._cancel
        self.bar.configure(value=0, maximum=1)
        self.prog_text.set("Least-squares fit…")
        app = self.app

        def progress(done, total):
            app.call_ui(self._progress, done, total)

        def work():
            try:
                result = tc.fit_transit(t, flux, err, am, planet, detrend_airmass=da, detrend_time=dt,
                                        mcmc_steps=steps, progress=progress, cancel=cancel,
                                        log=lambda m: app.call_ui(app.log, m))
                app.call_ui(self._fit_done, result, meta, None)
            except Exception as exc:
                app.call_ui(self._fit_done, None, meta, (str(exc), traceback.format_exc()))

        threading.Thread(target=work, daemon=True).start()

    def _progress(self, done, total):
        self.bar.configure(maximum=total, value=done)
        self.prog_text.set(f"MCMC step {done} of {total}")

    def _fit_done(self, result, meta, error):
        self.app._end_job()
        if error is not None:
            self.prog_text.set("Cancelled." if error[0] == "Cancelled" else "Fit failed.")
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
        self._notes_text(result["flags"] or [("info", "No cautions.")])
        self.draw()

    # ---- display ---------------------------------------------------------------------------
    def _set_columns(self):
        """The EXOTIC column only when a report is loaded."""
        cols = ["param", "shobs", "free", "pub"] + (["exotic"] if self.report is not None else [])
        self._display_cols = cols
        self.table.configure(displaycolumns=cols)

    def _show_verdict(self):
        v = (self.result or {}).get("verdict")
        if not v:
            self.verdict.set("")
            return
        state = _state(v)
        if state == "detected" and v.get("depth_unreliable"):
            # 2.2.7: a real dip with good timing, but a depth the night cannot be trusted for.
            self.verdict.set("✓ " + v["reason"] + " Timing usable; depth unreliable: " + v["depth_unreliable"] + ".")
            self.verdict_label.configure(foreground="#c26a00")
        elif state == "detected":
            self.verdict.set("✓ " + v["reason"])
            self.verdict_label.configure(foreground="#2e7d32")
        elif state == "inconclusive":
            self.verdict.set("⚠ " + v["reason"] + " Tc may be usable; the depth is not.")
            self.verdict_label.configure(foreground="#c26a00")
        else:
            self.verdict.set("✗ No transit measured. " + v["reason"] + " The numbers below are for reference only.")
            self.verdict_label.configure(foreground="#b00020")

    def _confirm_export(self) -> bool:
        v = (self.result or {}).get("verdict")
        state = _state(v) if v else "detected"
        if state == "none":
            return messagebox.askyesno(
                "SHOBS-P", "This fit did not measure a transit:\n\n" + v["reason"] + "\n\nA report from it would "
                "carry numbers that mean nothing. Save it anyway (for your records, not for submission)?")
        if state == "inconclusive":
            return messagebox.askyesno(
                "SHOBS-P", "This fit is inconclusive:\n\n" + v["reason"] + "\n\nThe report will say so in its notes. "
                "Tc may be worth reporting with that caveat; the depth is not. Save it?")
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
            ("Depth (%)", val("depth", "{:.2f}", 100), num(ff.get("k", float("nan")) ** 2, "{:.2f}", 100),
             num(p.get("depth"), "{:.2f}", 100), exo("Radius-ratio area depth (Rp/R*)^2", "{:.2f}")),
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
            oc = (r["best"]["tc"] - r["tc_pred"]) * 1440 if r.get("tc_pred_err") is not None else None
            sig = math.hypot(s["tc"][1], r["tc_pred_err"]) * 1440 if oc is not None else None
            rows.insert(1, ("O − C (min)", f"{oc:+.2f} ± {sig:.2f}" if oc is not None else "", "", "0", ""))
            rows += [("rms per point (%)", f"{r['rms'] * 100:.3f}", "", "", ""),
                     ("Red-noise beta", f"{r['beta']:.2f}", "", "", ""),
                     ("Reduced χ² (raw errors)", f"{r['rchi2']:.2f}", "", "", ""),
                     ("Points", str(r["n_points"]), "", "", str(len(self.report["t"])) if self.report else "")]
        warn_names = set()
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

    def _notes_text(self, flags):
        self.notes.configure(state="normal")
        self.notes.delete("1.0", END)
        for level, text in flags:
            self.notes.insert(END, ("⚠ " if level == "warn" else "• ") + text + "\n\n", level)
        self.notes.configure(state="disabled")

    def draw(self, placeholder: str = ""):
        if self.fig is None:
            return
        r = self.result
        ax, axr = self.ax, self.ax_r
        ax.clear()
        axr.clear()
        if not r:
            if placeholder:
                ax.text(0.5, 0.5, placeholder, transform=ax.transAxes, ha="center", va="center", fontsize=12,
                        color="#5d6d76", wrap=True)
                ax.set_xticks([])
                ax.set_yticks([])
                axr.set_xticks([])
                axr.set_yticks([])
            self.canvas.draw_idle()
            return
        best = r["best"]
        t = r["t"]
        x = (t - best["tc"]) * 24
        y = r["detrended"]
        ax.plot(x, y, ".", color="#9aa7b0", ms=4, label="each frame, detrended")
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
        if r.get("tc_pred_err") is not None:
            xp = (r["tc_pred"] - best["tc"]) * 24
            ax.axvline(xp, color="#888", ls="--", lw=0.8)
            ax.axvspan(xp - r["tc_pred_err"] * 24, xp + r["tc_pred_err"] * 24, color="#888", alpha=0.15)
        t14 = s["t14"][0] * 24
        for edge in (-t14 / 2, t14 / 2):
            ax.axvline(edge, color=GREEN, lw=0.6, alpha=0.6)
        ax.set_ylabel("Relative flux")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, loc="lower left", frameon=False)
        name = p.get("name") or "Transit"
        state = _state(r.get("verdict"))
        title = {"detected": f"{name}   Tc {s['tc'][0]:.5f} ± {s['tc'][1]:.5f} BJD_TDB",
                 "inconclusive": f"{name}   INCONCLUSIVE   Tc {s['tc'][0]:.5f} ± {s['tc'][1]:.5f}",
                 "none": f"{name}   NO TRANSIT MEASURED (model shown for reference)"}[state]
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
        for label in ax.get_xticklabels():
            label.set_visible(False)
        self.fig.subplots_adjust(left=0.09, right=0.98, top=0.93, bottom=0.1)
        self.canvas.draw_idle()

    # ---- files -----------------------------------------------------------------------------
    def _file_meta(self) -> dict:
        meta = dict(self.meta_for_files)
        meta["version"] = getattr(self.app, "app_version", "")
        v = (self.result or {}).get("verdict")
        state = _state(v) if v else "detected"
        if state == "detected" and v and v.get("depth_unreliable"):
            note = "SHOBS-P: timing usable, depth unreliable: " + v["depth_unreliable"]
            note = note.replace("Δ", "d").replace("±", "+/-").replace("σ", "sigma").replace("°", " deg").replace("−", "-")
            meta["notes"] = (meta.get("notes", "") + " | " if meta.get("notes") else "") + note
        if state != "detected":
            label = "INCONCLUSIVE" if state == "inconclusive" else "NO TRANSIT MEASURED"
            note = f"SHOBS-P verdict {label}: " + v["reason"].replace("\n", " ")
            # AAVSO report headers are plain text: keep them ASCII.
            note = note.replace("Δ", "d").replace("±", "+/-").replace("σ", "sigma").replace("°", " deg").replace("−", "-")
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
        self.fig.savefig(buf, format="png", dpi=130, facecolor="#ffffff")
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
        import textwrap
        wrapped = [(part, size, colour, weight) for text, size, colour, weight in lines
                   for part in (textwrap.wrap(text, width=int(w_in * 13)) or [""])]
        header_h = 0.24 * len(wrapped) + 0.2 * len(rows) + 0.2 * min(len(notes), 8) + 0.5
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
        for ln in notes[:8]:
            fig.text(0.02, y, textwrap.shorten(ln, width=int(w_in * 16), placeholder=" …"), fontsize=8,
                     color="#b35c00" if ln.startswith("⚠") else "#33424d", va="top")
            y -= 0.2 / total
        ax = fig.add_axes([0, 0, 1, (ph / 130.0) / total])
        ax.imshow(plot)
        ax.axis("off")
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
            self.fig.savefig(png, dpi=150)
            files.append(png)
        zpath = out + ".zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                z.write(f, arcname=os.path.join(base, os.path.basename(f)))
        self.app.log(f"ExoFOP package: {out} (and {os.path.basename(zpath)})")
        self.app.saved_notice(out, f"Package folder and {os.path.basename(zpath)}: light curve (CSV), fit and ExoFOP "
                                   "notes (text), plot, and an AAVSO exoplanet report.")


def _state(verdict) -> str:
    """'detected', 'inconclusive', or 'none' (fits made before 2.2.1 have only 'detected')."""
    if not verdict:
        return "detected"
    return verdict.get("state") or ("detected" if verdict.get("detected", True) else "none")


def _norm(name) -> str:
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


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
                                           "Input page and press Lookup.")
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
        ttk.Label(top, text=f"Transit planner: {planet.get('name', '')}", font=("Segoe UI", 15, "bold"),
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
        depth = planet.get("depth") or (page.result["summary"]["depth"][0] if page.result else None)
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
            messagebox.showinfo("SHOBS-P", "Label chart first (Gaia DR3 gives the most neighbors). The check lists the "
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
        ttk.Label(top, text="Nearby eclipsing binary (NEB) check", font=("Segoe UI", 15, "bold"),
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
        ttk.Label(top, text="Tonight's transits", font=("Segoe UI", 15, "bold"), style="Card.TLabel").pack(anchor=W)
        ttk.Label(top, text=(f"Site {self.lat:.4f}, {self.lon:.4f}. Every known transiting planet (NASA Exoplanet "
                             "Archive) whose whole transit, plus the baseline on both sides, is in the dark with the "
                             "star above the altitude limit. Times are local. 'After' and 'End by' limit the window "
                             "(for example, after 23:00 to finish another target first). Double-click a row to use "
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
        text = (text or "").strip()
        if not text:
            return None
        hh, _, mm = text.replace(".", ":").partition(":")
        h, m = int(hh), int(mm or 0)
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
            w.writerow(["planet", "V", "depth_pct", "T14_h", "start_local", "ingress_local", "mid_local", "egress_local",
                        "end_local", "alt_min", "alt_max", "moon_illum", "moon_sep_deg", "meridian_flip"])
            for e in self.events:
                w.writerow([e["name"], f"{e['vmag']:.2f}", f"{e['depth'] * 100:.3f}", f"{e['t14'] * 24:.3f}",
                            fmt(e["start"]), fmt(e["t1"]), fmt(e["tc"]), fmt(e["t4"]), fmt(e["end"]),
                            f"{e['alt_min']:.1f}", f"{e['alt_max']:.1f}", f"{e['moon_illum']:.2f}",
                            f"{e['moon_sep']:.0f}", int(e["meridian"])])
