"""2.4.0 step 7c (#92): the App's side of the promotion path's last pieces: Watch reach-back, the Check neighbors window
(look-alikes, close companions, comps and check, the blend test) and the outside-data check (ZTF, TESS, ATLAS).

The measuring and the numbers live in neighbors.py, promotion.py and outside_data.py (headless, tested); this module
gathers the series' settings on the Tk thread, runs the work in a worker thread with the busy bar, and shows it.
"""
from __future__ import annotations

import math
import os
import threading
from tkinter import BOTH, END, LEFT, RIGHT, W, X, BooleanVar, Label, StringVar, Toplevel, messagebox, ttk

import numpy as np

import busy_bar
import neighbors as nb
import outside_data as od
import photometry_core as core
import promotion
import registry

try:
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
except Exception:   # pragma: no cover
    Figure = None

SECONDS_PER_FRAME = 1.2   # reach-back / measuring on John's PC (6248 x 4176 frames, calibrated): ~1.1 s each
ROLE_WORDS = {"comp": "comp", "check": "check", "spare": "spare comp", "watch": "Watch star",
              "target": "series target"}
GROUP_WORDS = {"target": "Target", "close": "Close companions (inside the aperture + sky ring)",
               "look": "Look-alikes", "comps": "Comps and check"}


def _ca():
    import clearv_app as ca   # the App module (imported lazily: it imports this one)
    return ca


def _f(v, default=None):
    try:
        v = float(str(v).strip().replace(",", ""))
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


class NeighborsMixin:
    """Methods of the App (clearv_app.App) for 7c."""

    # ---- the series' stars on the sky ----------------------------------------------------------------------------
    def neighbor_sky(self) -> dict:
        """{name: (ra, dec)} of every star the series measures (comps, check, spares, Watch stars, target)."""
        sky = {k: tuple(v) for k, v in (self.comp_coords or {}).items() if v}
        for n, info in (self.spare_catalog or {}).items():
            if info.get("ra") is not None and info.get("dec") is not None:
                sky.setdefault(n, (info["ra"], info["dec"]))
        for w in self.watch_list or []:
            if w.get("ra") is not None and w.get("dec") is not None:
                sky.setdefault(w["name"], (w["ra"], w["dec"]))
        try:
            radec = self.target_radec_deg()
        except ValueError:
            radec = None
        return nb.series_sky(sky, self.star_id.get().strip(), radec)

    def _series_star_mags(self) -> dict:
        """{name: catalog V} for the series' stars (comps, spares, check, Watch stars)."""
        mags = {}
        mags.update({k: v for k, v in (self.comp_catalog_mags or {}).items() if v is not None})
        for n, info in (self.spare_catalog or {}).items():
            if info.get("mag") is not None:
                mags.setdefault(n, info["mag"])
        for w in self.watch_list or []:
            if isinstance(w.get("mag"), (int, float)):
                mags.setdefault(w["name"], w["mag"])
        chk = self.check_used or self.check_name.get().strip()
        if chk:
            v = _f(self.check_mag.get())
            if v is not None:
                mags.setdefault(chk, v)
        return mags

    def _measure_cfg(self) -> dict:
        """What measuring on the series' frames needs, read on the Tk thread."""
        pattern = self.pattern.get()
        binning = int(self.binning.get() or 1)
        total_bin = binning * (1 if pattern == core.MONO else 2)
        nights = {}
        for n, nb_ in (self.night_bin or {}).items():
            if isinstance(nb_, dict):
                nights[n] = (_f(nb_.get("radius")), _f(nb_.get("sky_in")), _f(nb_.get("sky_out")),
                             int(nb_.get("binning") or binning))
        return {"pattern": pattern, "mode": self.debayer_mode.get(), "binning": binning, "total_bin": total_bin,
                "precalibrated": bool(getattr(self, "precalibrated", False)),
                "bias": self.bias_dir.get().strip(), "dark": self.dark_dir.get().strip(),
                "darkflat": self.darkflat_dir.get().strip(), "flat": self.flat_dir.get().strip(),
                "radius": float(self.radius.get()), "sky_in": float(self.sky_in.get()),
                "sky_out": float(self.sky_out.get()), "nights": nights,
                "comps": list(self.comps_used), "check": self.check_used or self.check_name.get().strip(),
                "target": self.star_id.get().strip(), "sky": self.neighbor_sky(),
                "series": self.series_path or ""}

    @staticmethod
    def night_flat_folder(light_path: str, fallback: str) -> str:
        """A night's own flats: the Flats folder beside the night's Lights folder (John's layout:
        <field>/<night>/Lights and <field>/<night>/Flats), else the Input page's Flat folder."""
        if os.sep != "\\":
            light_path = str(light_path).replace("\\", os.sep)   # a series saved on Windows, read elsewhere
        night_dir = os.path.dirname(os.path.dirname(os.path.abspath(light_path)))
        for name in ("Flats", "Flat", "flats", "flat"):
            p = os.path.join(night_dir, name)
            if os.path.isdir(p):
                return p
        return fallback or ""

    @staticmethod
    def make_frame_loader(cfg: dict, used: dict, log=None):
        """load(path) -> (raw, header, calibrated debayered image) for measuring on the series' frames, with each
        night's own saved masters (prepare_masters reuses them; built and saved when missing). used[night folder] =
        the calibration in words, for the result."""
        cache = {}

        def masters_for(path):
            flat_dir = NeighborsMixin.night_flat_folder(path, cfg["flat"])
            key = (cfg["bias"], cfg["dark"], cfg["darkflat"], flat_dir)
            if key in cache:
                return cache[key]
            if cfg["precalibrated"]:
                m = core.Masters()
                used[os.path.dirname(path)] = "lights pre-calibrated (no masters applied)"
            else:
                def src(d, kind):
                    """The folder's raw frames (their saved master is reused), else a saved SHOBS-P master alone
                    (raw frames deleted after the master was built)."""
                    if not d:
                        return [], None
                    frames, master = core.calibration_source(d)
                    if frames or master:
                        return frames, master
                    folder = os.path.join(d, core.MASTER_DIR)
                    stem = core.master_file_name(kind, cfg["binning"], 1.0).split("_1s")[0].replace(".fits", "")
                    try:
                        hits = sorted(f for f in os.listdir(folder) if f.startswith(stem) and f.endswith(".fits"))
                    except OSError:
                        hits = []
                    return [], (os.path.join(folder, hits[0]) if hits else None)
                bias, fb = src(cfg["bias"], "bias")
                dark, fd = src(cfg["dark"], "dark")
                dflat, fdf = src(cfg["darkflat"], "darkflat")
                flat, ff = src(flat_dir, "flat")
                files = {k: v for k, v in (("bias", fb), ("dark", fd), ("darkflat", fdf), ("flat", ff)) if v}
                m = core.prepare_masters(bias, dark, flat, cfg["binning"], log, files=files, save=True,
                                         darkflat_paths=dflat or None)
                words = [m.used.get(k) for k in ("bias", "dark", "darkflat", "flat") if m.used.get(k)]
                if m.flat is None:
                    words.append("NO FLAT")
                used[os.path.dirname(path)] = "; ".join(words) or "no masters"
            cache[key] = m
            return m

        def load(path):
            masters = masters_for(path)
            raw, header = core.read_fits(path)
            cal = core.calibrate_frame(core.bin_image(raw, cfg["binning"]), header, masters)
            return raw, header, core.debayer(cal, cfg["pattern"], cfg["mode"])
        return load

    @staticmethod
    def aperture_function(cfg: dict):
        def aperture_for(o):
            r, si, so, _b = cfg["nights"].get(o.night, (None, None, None, None))
            return (r or cfg["radius"], si or cfg["sky_in"], so or cfg["sky_out"])
        return aperture_for

    def _bump_watch_revision(self):
        """The series changed in a way the points' fingerprint does not see (Watch values, neighbor checks)."""
        self._watch_revision = int(getattr(self, "_watch_revision", 0)) + 1
        try:
            self.update_series_status()
        except Exception:
            pass

    # ---- Watch reach-back --------------------------------------------------------------------------------------
    def reach_back(self, name: str, parent=None, on_done=None) -> bool:
        """Measure a Watch star on the series' frames that do not have it yet (frames already on disk), tied to each
        frame's stored comp values; a few frames that have it are measured too, as a check."""
        ca = _ca()
        parent = parent or self
        entry = next((w for w in self.watch_list if w["name"] == name), None)
        if entry is None:
            return False
        if entry.get("ra") is None or entry.get("dec") is None:
            messagebox.showinfo(ca.APP_TITLE, f"{name} has no sky position (it was watched by its pixel position on an "
                                              "unsolved image), so it cannot be placed on other nights' frames. "
                                              "Solve the field and watch it again.", parent=parent)
            return False
        missing = nb.missing_frames(self.observations, name)
        if not missing:
            messagebox.showinfo(ca.APP_TITLE, f"{name} is measured on every frame of the series already: nothing to "
                                              "reach back for.", parent=parent)
            return False
        on_disk = [i for i in missing if os.path.isfile(self.observations[i].path)]
        gone = len(missing) - len(on_disk)
        if not on_disk:
            messagebox.showinfo(ca.APP_TITLE, f"None of the {len(missing)} frames without {name} is on disk any more "
                                              "(moved or deleted), so there is nothing to measure.", parent=parent)
            return False
        nights = sorted({self.observations[i].night for i in on_disk})
        sample = [i for i in nb.sample_measured(self.observations, name) if os.path.isfile(self.observations[i].path)]
        n = len(on_disk) + len(sample)
        mins = max(1, round(n * SECONDS_PER_FRAME / 60))
        cfg = self._measure_cfg()
        if not cfg["precalibrated"] and not (cfg["bias"] or cfg["dark"] or cfg["darkflat"]):
            cal_words = ("no bias or dark on the Input page: only each night's flat is applied (the tie to the comps "
                         "takes out most of the difference)")
        else:
            cal_words = "the Input page's bias and dark, and each night's own flat (its Flats folder)"
        if not messagebox.askyesno(
                ca.APP_TITLE,
                f"Reach back: measure {name} on {len(on_disk)} frame(s) of {len(nights)} night(s) that do not have it "
                f"yet" + (f" ({gone} more are no longer on disk)" if gone else "") + ".\n\n"
                f"Calibration: {cal_words}. Each value is tied to that frame's stored comp values, so it sits on the "
                f"same scale as the rest of the series. {len(sample)} frame(s) that have the star already are measured "
                f"again as a check.\n\nAbout {mins} min. Measure now?", parent=parent):
            return False
        if not self._start_job(f"Reach-back: {name}…"):
            return False
        cancel = self._cancel
        obs = self.observations
        used = {}
        star = {"key": name, "name": name, "ra": float(entry["ra"]), "dec": float(entry["dec"])}
        gen = getattr(self, "_series_gen", 0)

        def progress(k, total, o):
            if k == 1 or k % 5 == 0 or k == total:
                self.call_ui(self._job_words, f"Reach-back: {name}, frame {k} of {total}…")

        def work():
            res, error = None, ""
            try:
                load = self.make_frame_loader(cfg, used, log=lambda t: self.call_ui(self.log, "  " + str(t)))
                res = nb.measure_series(core, obs, [star], sky=cfg["sky"], comp_names=cfg["comps"],
                                        target_name=cfg["target"], check_name=cfg["check"], load=load,
                                        aperture_for=self.aperture_function(cfg), total_bin=cfg["total_bin"],
                                        indices=sorted(set(on_disk) | set(sample)), progress=progress, cancel=cancel)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            self.call_ui(self._reach_back_done, name, res, error, on_disk, sample, used, gen, on_done)

        threading.Thread(target=work, daemon=True).start()
        return True

    def _reach_back_done(self, name, res, error, on_disk, sample, used, gen, on_done):
        ca = _ca()
        self._end_job()
        if gen != getattr(self, "_series_gen", 0):
            self.log("Reach-back: the series changed while it ran; its values were dropped.")
            return
        if error or res is None:
            self.log(f"✖ Reach-back for {name} stopped: {error}")
            messagebox.showerror(ca.APP_TITLE, f"Reach-back for {name} stopped:\n\n{error}")
            return
        vals = res["values"].get(name) or {}
        check = nb.consistency(self.observations, {i: v for i, v in vals.items() if i in set(sample)}, name)
        new = {i: v for i, v in vals.items() if i in set(on_disk)}
        filled = nb.fill_watch(self.observations, name, new)
        nights = sorted({self.observations[i].night for i, v in new.items() if math.isfinite(v.get("inst", math.nan))})
        entry = next((w for w in self.watch_list if w["name"] == name), None)
        rec = {"utc": nb.utc_now(), "frames": filled, "asked": len(on_disk), "nights": nights,
               "check": check, "calibration": dict(used), "failed": len(res["failed"]),
               "cancelled": bool(res.get("cancelled"))}
        if entry is not None:
            entry.setdefault("reach_back", []).append(rec)
        if filled:
            self._bump_watch_revision()
        for folder, words in used.items():
            self.log(f"  Reach-back calibration for {folder}: {words}")
        for path, why in res["failed"][:5]:
            self.log(f"  not measured: {os.path.basename(str(path))}: {why}")
        chk = (f" Check on {check['n']} frame(s) that had it: {check['median'] * 1000:+.1f} mmag (rms "
               f"{check['rms'] * 1000:.1f})." if check["n"] else "")
        line = (f"Reach-back: {name} measured on {filled} of {len(on_disk)} frame(s), {len(nights)} night(s)."
                + chk + (" Stopped early." if res.get("cancelled") else "")
                + (" Save series to keep them." if filled else ""))
        big = check["n"] and abs(check["median"] or 0) > 0.01
        nflat = sum(1 for w in used.values() if "NO FLAT" in w)
        self.log(line)
        if big:
            self.log(f"⚠ Reach-back: the check frames differ from the stored values by {check['median'] * 1000:+.1f} "
                     "mmag; the older nights may have been calibrated differently (see the calibration lines).")
        if nflat:
            self.log(f"⚠ Reach-back: {nflat} night folder(s) had no flat; those values carry the flat-field pattern.")
        self.status.set(line)
        for win_attr in ("_candidates_win",):
            win = getattr(self, win_attr, None)
            if win is not None:
                try:
                    win.refresh()
                except Exception:
                    pass
        if on_done is not None:
            try:
                on_done()
            except Exception:
                pass

    # ---- Check neighbors ---------------------------------------------------------------------------------------
    def check_neighbors(self, name: str, is_target: bool = False, parent=None):
        """Open the Check neighbors window for a Watch star (or the target) of the open series."""
        ca = _ca()
        if not self.observations:
            messagebox.showinfo(ca.APP_TITLE, "Load or run a series first.", parent=parent or self)
            return None
        return NeighborsWindow(self, name=name, is_target=is_target)

    def check_neighbors_suspect(self, result, i: int, parent=None):
        """Open the Check neighbors window for a star of one night's field scan (a Suspect)."""
        return NeighborsWindow(self, name=str(result["labels"][i]), scan=result, scan_index=i)

    # ---- the outside-data check --------------------------------------------------------------------------------
    def outside_check(self, name: str, ra: float, dec: float, period: float, *, gaia: str = "", on_done=None,
                      quiet: bool = False) -> bool:
        """ZTF, TESS and ATLAS at the star's position, in the background; the result goes into the star's Registry
        record ("outside") and clears the gates it may clear (decision 10) on the Registry evidence and the open
        gate report."""
        running = getattr(self, "_outside_running", None)
        if not isinstance(running, set):
            running = self._outside_running = set()
        if name in running:
            return False
        running.add(name)
        tok = self.busy_begin(f"Outside data: ZTF, TESS and ATLAS for {name}…")
        try:
            pmin = float(self.min_period.get())
            pmax = float(self.max_period.get())
        except (TypeError, ValueError):
            pmin, pmax = 0.02, 20.0

        def work():
            res, error = None, ""
            try:
                res = od.check(float(ra), float(dec), float(period), min_period=pmin, max_period=pmax)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            self.call_ui(self._outside_done, tok, name, ra, dec, gaia, res, error, on_done, quiet)

        threading.Thread(target=work, daemon=True).start()
        return True

    def _outside_done(self, tok, name, ra, dec, gaia, res, error, on_done, quiet):
        ca = _ca()
        self._outside_running.discard(name)
        self.busy_end(tok)
        if error or res is None:
            self.log(f"✖ Outside data for {name}: {error}")
            if not quiet:
                messagebox.showerror(ca.APP_TITLE, f"The outside-data check for {name} failed:\n\n{error}")
            return
        saved = ""
        try:
            doc = registry.load()
            rec = registry.find(doc, gaia, ra, dec)
            if rec is not None:
                apply_outside_to_record(rec, res)
                registry.save(doc)
                saved = rec["key"]
        except Exception as exc:
            self.log(f"⚠ Outside data for {name}: the Registry was not updated ({type(exc).__name__}: {exc}).")
        rep = (self.gate_reports or {}).get(name)
        if rep and rep.get("gates"):
            promotion.clear_with_outside(rep, od.for_gates(res))
            rep["outside"] = res
        for s, r in res["surveys"].items():
            self.log(f"  {s}: {r.get('detail') or r.get('status')}" + (f" {r['note']}" if r.get("note") else ""))
        self.log(f"Outside data for {name}: {res['summary']}" + ("" if saved else " (not in the Registry: kept "
                                                                              "with tonight's gate report only)"))
        self.status.set(f"Outside data for {name}: " + res["summary"])
        win = getattr(self, "_candidates_win", None)
        if win is not None:
            try:
                win.refresh()
            except Exception:
                pass
        if on_done is not None:
            try:
                on_done(res)
            except Exception:
                pass


def apply_outside_to_record(rec: dict, res: dict) -> None:
    """Keep the outside-data result in a Registry record and clear the gates it may clear on its evidence."""
    rec["outside"] = {k: res.get(k) for k in ("utc", "period", "surveys", "agree", "alias", "summary")}
    ev = rec.get("evidence") or {}
    P = ev.get("period_days")
    agree = od.for_gates(res)
    if not (P and agree):
        return
    names = [s for s, v in agree.items() if abs(float(v["period"]) - float(P)) / float(P) <= od.AGREE_TOL]
    if not names:
        return
    for g in ev.get("gates") or []:
        if g.get("key") in ("alias_pm1", "span", "volume") and not g.get("pass"):
            g["pass"] = True
            g["cleared_by"] = " and ".join(names)


# ---- the window --------------------------------------------------------------------------------------------------
class NeighborsWindow:
    """Check neighbors: the star's light curve with its neighbors' on one shared scale, power and amplitude at P,
    and the blend test. Series mode (a Watch star or the target, every night it has) or scan mode (a Suspect, that
    night's field scan). Mockup: "SHOBS-P Neighbor check window" (15 Nov 2026)."""

    TIME_BIN_MIN = 6.0
    PHASE_BINS = 40

    def __init__(self, app, name: str, is_target: bool = False, scan=None, scan_index: int | None = None):
        ca = _ca()
        self.app, self.name, self.is_target = app, name, is_target
        self.scan, self.scan_index = scan, scan_index
        self.mode = "scan" if scan is not None else "series"
        self.result = None
        self.picks = {"lookalikes": [], "close": []}
        self.blend = None
        self.catalog_note = ""
        win = Toplevel(app)
        self.win = win
        win.title(f"{ca.APP_TITLE} — check neighbors")
        win.protocol("WM_DELETE_WINDOW", self.close)
        top = ttk.Frame(win, style="Card.TFrame", padding=10)
        top.pack(fill=BOTH, expand=True)
        ttk.Label(top, text=f"Check neighbors: {name}", font=(core.UI_FONT, 15, "bold"),
                  style="Card.TLabel").pack(anchor=W)
        self.sub = StringVar(value="")
        ttk.Label(top, textvariable=self.sub, style="Hint.TLabel", wraplength=ca.text_wrap_px(win, chars=130),
                  justify=LEFT).pack(anchor=W, pady=(2, 6))
        bottom = ttk.Frame(top, style="Card.TFrame")
        bottom.pack(fill=X, side="bottom", pady=(8, 0))
        self.busy = busy_bar.BusyBar(bottom, label_style="Hint.TLabel", frame_style="Card.TFrame")
        self.busy.pack(side=LEFT)
        self.bottom_line = StringVar(value="")
        ttk.Label(bottom, textvariable=self.bottom_line, style="Card.TLabel", font=(core.UI_FONT, 10, "bold")).pack(
            side=LEFT, padx=(8, 0))
        ttk.Button(bottom, text="Close", command=self.close).pack(side=RIGHT)
        self.save_btn = ttk.Button(bottom, text="Save to star record", style="Accent.TButton", command=self.save)
        self.save_btn.pack(side=RIGHT, padx=6)
        ctl = ttk.Frame(top, style="Card.TFrame")
        ctl.pack(fill=X, pady=(0, 6))
        ttk.Label(ctl, text="View", style="Card.TLabel").pack(side=LEFT)
        self.view = StringVar(value="fold")
        ttk.Radiobutton(ctl, text="Folded at P", variable=self.view, value="fold", command=self.draw).pack(
            side=LEFT, padx=(6, 0))
        ttk.Radiobutton(ctl, text="Time (last night)", variable=self.view, value="time", command=self.draw).pack(
            side=LEFT, padx=(6, 12))
        ttk.Label(ctl, text="Look-alikes ±", style="Card.TLabel").pack(side=LEFT)
        self.window = StringVar(value=f"{nb.LOOKALIKE_MAG:.1f}")
        ent = ttk.Entry(ctl, textvariable=self.window, width=5)
        ent.pack(side=LEFT)
        ent.bind("<Return>", lambda _e: self.recompute(pick=True))
        ttk.Label(ctl, text="mag", style="Card.TLabel").pack(side=LEFT, padx=(2, 12))
        self.show_comps = BooleanVar(value=True)
        ttk.Checkbutton(ctl, text="Show comps and check", variable=self.show_comps, command=self.recompute).pack(
            side=LEFT)
        self.measure_btn = ttk.Button(ctl, text="Measure look-alikes", command=self.measure)
        self.blend_btn = ttk.Button(ctl, text="Run blend test", command=self.run_blend)
        if self.mode == "series":
            self.blend_btn.pack(side=RIGHT)
            self.measure_btn.pack(side=RIGHT, padx=6)
        self.fig = None
        body = ttk.Frame(top, style="Card.TFrame")
        body.pack(fill=BOTH, expand=True)
        self.thumb_fig = None
        if Figure is not None:
            self.thumb_fig = Figure(figsize=(3.3, 3.5), dpi=96, facecolor="#ffffff")
            self.thumb_canvas = FigureCanvasTkAgg(self.thumb_fig, master=body)
            self.thumb_canvas.get_tk_widget().pack(side=LEFT, anchor="n", padx=(0, 8))
            self.fig = Figure(figsize=(11, 7.2), dpi=96, facecolor="#ffffff")
            self.canvas = FigureCanvasTkAgg(self.fig, master=body)
            self.canvas.get_tk_widget().pack(side=LEFT, fill=BOTH, expand=True)
        self.note = StringVar(value="")
        ttk.Label(top, textvariable=self.note, style="Hint.TLabel", wraplength=ca.text_wrap_px(win, chars=150),
                  justify=LEFT).pack(anchor=W, pady=(4, 0), before=bottom)
        try:
            win.after_idle(lambda: ca.fit_to_content(win))
        except Exception:
            pass
        self.side = nb.sidecar_load(app.series_path) if self.mode == "series" else {"stars": {}, "checks": {}}
        old = (self.side.get("checks") or {}).get(name) or {}
        if old.get("blend"):
            self.blend = old["blend"]
        self.recompute(pick=False)
        if self.mode == "series":
            self.find_lookalikes()

    # -- data --------------------------------------------------------------------------------------------------
    def _window_mag(self) -> float:
        v = _f(self.window.get(), nb.LOOKALIKE_MAG)
        return max(0.05, min(3.0, v))

    def _series_curves(self):
        """(target curve, [(group, curve, info)], sky, mags) from the open series and the sidecar."""
        app = self.app
        obs = app.observations
        cfg = app._gate_settings()
        tname = cfg["target"]
        target = promotion.star_curve(obs, self.name, is_target=self.is_target, target_name=tname)
        sky = app.neighbor_sky()
        mags = app._series_star_mags()
        if self.is_target:
            mags.setdefault(self.name, float(np.nanmedian(target.mag)) if target.jd.size else None)
        rows = []
        nulls, chk = promotion.null_curves(obs, cfg["comp_names"], cfg["check_name"],
                                           [n for n in cfg["watch_names"] + cfg["spare_names"]
                                            if n != self.name and n not in cfg["comp_names"]], cfg["aliases"])
        present = {c.name for c in nulls}
        for wn in cfg["watch_names"]:
            # a Watch star that is also in the series' comp layout but was never measured as a comp
            if wn != self.name and wn not in present:
                wc = promotion.star_curve(obs, wn)
                if wc.jd.size:
                    wc.role = "watch"
                    nulls.append(wc)
        if not self.is_target:
            tc = promotion.star_curve(obs, tname, is_target=True, target_name=tname)
            if tc.jd.size:
                tc.role = "target"
                nulls.append(tc)
                mags.setdefault(tname, float(np.nanmedian(tc.mag)))
        for c in nulls:
            rows.append(("series", c, {"name": c.name, "role": c.role, "mag": mags.get(c.name)}))
        for nm in (self.side.get("stars") or {}):
            vals = nb.sidecar_values(self.side, obs, nm)
            if vals:
                c = nb.curve_from_values(promotion, obs, nm, vals, role="neighbor")
                s = self.side["stars"][nm]
                rows.append(("measured", c, {"name": nm, "role": "neighbor", "mag": s.get("mag"),
                                             "ra": s.get("ra"), "dec": s.get("dec")}))
        return target, rows, sky, mags

    def _scan_curves(self):
        r, i = self.scan, self.scan_index
        jd = np.asarray(r["jd"], float)
        lc = np.asarray(r["light_curves"], float)
        night = "scan"

        def curve(j, role):
            return promotion.make_curve(str(r["labels"][j]), jd, lc[:, j], [night] * jd.size, [""] * jd.size,
                                        role=role)
        target = curve(i, "target")
        rows = []
        mags = np.asarray(r["mag"], float)
        xy = np.asarray(r["xy"], float)
        sat = set(r.get("saturated") or [])
        d = np.hypot(*(xy - xy[i]).T)
        so = float(self.app.sky_out.get())
        w = self._window_mag()
        close = [j for j in np.argsort(d) if j != i and d[j] <= so and r["usable"][j]]
        look = [j for j in np.argsort(d) if j != i and d[j] > so and r["usable"][j] and j not in sat
                and math.isfinite(mags[j]) and abs(mags[j] - mags[i]) <= w][:nb.LOOKALIKE_MAX]
        roles = {"Check", "Comp"} | {f"Comp {k}" for k in range(1, 11)}
        comps = [k for k, lab in enumerate(r["labels"]) if str(lab) in roles and k != i and r["usable"][k]]
        for j in close:
            rows.append(("close", curve(j, "neighbor"), {"name": str(r["labels"][j]), "mag": mags[j],
                                                       "sep_px": float(d[j])}))
        for j in look:
            rows.append(("look", curve(j, "neighbor"), {"name": str(r["labels"][j]), "mag": mags[j],
                                                      "sep_px": float(d[j])}))
        if self.show_comps.get():
            for j in comps:
                if j not in close and j not in look:
                    rows.append(("comps", curve(j, "comp"), {"name": str(r["labels"][j]), "mag": mags[j],
                                                           "sep_px": float(d[j])}))
        return target, rows, close, look

    def recompute(self, pick: bool = False):
        if pick and self.mode == "series":
            self.find_lookalikes()
            return
        try:
            if self.mode == "series":
                self._recompute_series()
            else:
                self._recompute_scan()
        except Exception as exc:
            self.note.set(f"✖ {type(exc).__name__}: {exc}")
            return
        self.draw()

    def _recompute_series(self):
        app = self.app
        target, rows, sky, mags = self._series_curves()
        rep = (app.gate_reports or {}).get(self.name) or {}
        P = rep.get("period_days")
        cfg = app._gate_settings()
        bad, _ = promotion.check_bad_nights(next((c for _g, c, _i in rows if c.role == "check"), None))
        here = sky.get(self.name)
        if here is None and self.is_target:
            here = sky.get(cfg["target"])
        mymag = mags.get(self.name)
        w = self._window_mag()
        groups = []
        show = []
        for kind, c, info in rows:
            sep = (nb.sep_arcsec(here[0], here[1], *sky[c.name]) if here and c.name in sky
                   else (nb.sep_arcsec(here[0], here[1], info["ra"], info["dec"]) if here and info.get("ra") is not None
                         else None))
            info["sep"] = sep
            close_r = self._close_arcsec()
            m = info.get("mag")
            dm = (m - mymag) if (m is not None and mymag is not None) else None
            info["dmag"] = dm
            if sep is not None and close_r and sep <= close_r:
                groups.append(("close", c, info))
            elif dm is not None and abs(dm) <= w:
                groups.append(("look", c, info))
            elif c.role in ("comp", "check") and self.show_comps.get():
                groups.append(("comps", c, info))
            elif c.role in ("comp", "check"):
                continue
            elif kind == "measured":
                groups.append(("look", c, info))
        order = {"close": 0, "look": 1, "comps": 2}
        groups.sort(key=lambda g: (order[g[0]], g[2]["sep"] if g[2].get("sep") is not None else 1e9))
        res = promotion.neighbor_check(target, [c for _g, c, _i in groups], period=P,
                                       min_period=cfg["min_period"], max_period=cfg["max_period"], bad_nights=bad)
        for (g, c, info), row in zip(groups, res["rows"]):
            row.update({"group": g, "sep": info.get("sep"), "dmag": info.get("dmag"), "mag": info.get("mag")})
        measured_names = {c.name for _g, c, _i in groups}
        pending = [p for p in self.picks["lookalikes"] + self.picks["close"] if p["name"] not in measured_names]
        self.result = res
        self.curves = {"target": target, "rows": [(g, c) for g, c, _i in groups]}
        self.pending = pending
        nights = sorted(set(target.night))
        P_txt = f"P {res['period']:.5f} d" if res.get("period") else "no period yet"
        self.sub.set(f"{'Target' if self.is_target else 'Watch star'} · V {mymag:.2f} · {P_txt} · {len(nights)} "
                     f"night(s), {target.jd.size} points"
                     + (f" · nights left out (bad check-star night): {', '.join(bad)}" if bad else "")
                     if mymag is not None else f"{P_txt} · {len(nights)} night(s), {target.jd.size} points")
        self.bottom_line.set(("✓ " if res["n_measured"] and res["n_quiet"] == res["n_measured"] else
                              ("⚠ " if res["n_measured"] else "")) + res["bottom"]
                             + (f" · {len(pending)} not measured yet" if pending else ""))
        close = [p for p in self.picks["close"]]
        self.blend_btn.state(["!disabled"] if close and res.get("period") else ["disabled"])
        self.measure_btn.state(["!disabled"] if pending else ["disabled"])
        notes = []
        if self.catalog_note:
            notes.append(self.catalog_note)
        if not close and self.picks.get("searched"):
            notes.append(f"No star within the aperture plus sky ring ({self._close_arcsec():.0f}″), so no blend test is "
                         "needed for this star.")
        if self.blend:
            notes.append(self.blend.get("text", ""))
        notes.append("Power at P is each star's periodogram power at the star's period as a share of its own; a star "
                     "is quiet when that is under 50% and its amplitude under a third of the star's (or 2σ). Same code "
                     "as the gate runner's null tests, so this window and the gates agree.")
        self.note.set(" ".join(n for n in notes if n))

    def _recompute_scan(self):
        target, rows, close, look = self._scan_curves()
        res = promotion.neighbor_check(target, [c for _g, c, _i in rows], min_period=0.02,
                                       max_period=max(0.05, float(np.ptp(target.jd))), min_points=8)
        for (g, c, info), row in zip(rows, res["rows"]):
            row.update({"group": g, "mag": info.get("mag"), "sep": None, "sep_px": info.get("sep_px")})
        self.result = res
        self.curves = {"target": target, "rows": [(g, c) for g, c, _i in rows]}
        self.pending = []
        r, i = self.scan, self.scan_index
        self.sub.set(f"Suspect · {r['labels'][i]} · mag {float(r['mag'][i]):.2f} · one night, {target.jd.size} frames · "
                     + (f"P {res['period']:.5f} d" if res.get("period") else "no period"))
        self.bottom_line.set(("✓ " if res["n_measured"] and res["n_quiet"] == res["n_measured"] else
                              ("⚠ " if res["n_measured"] else "")) + res["bottom"])
        self.note.set(("Close companions: " + str(len(close)) + " inside the sky ring. " if close else
                       "No star inside the aperture plus sky ring. ")
                      + "A Suspect is judged on its one night; Watch it to measure it night after night, then Check "
                        "neighbors again on the series (with look-alikes from the catalog and the blend test).")

    def _close_arcsec(self) -> float:
        """The aperture plus sky ring in arcsec (the outer sky radius on this series' frames)."""
        app = self.app
        so = _f(app.sky_out.get(), 0.0) or 0.0
        scale = getattr(self, "_scale", None)
        if scale is None:
            scale = float("nan")
            sky = app.neighbor_sky()
            cfg = app._gate_settings()
            for o in app.observations[:30]:
                fm = nb.FrameMap(o, sky, cfg["comp_names"], cfg["target"], cfg["check_name"])
                if fm.ok:
                    scale = fm.scale_arcsec_per_px()
                    break
            self._scale = scale
        return so * scale if math.isfinite(scale) else 0.0

    def find_lookalikes(self):
        """Look-alikes and close companions from the catalogs at the star's position (background)."""
        app = self.app
        sky = app.neighbor_sky()
        here = sky.get(self.name) if not self.is_target else sky.get(app.star_id.get().strip())
        if here is None:
            self.catalog_note = "The star has no sky position, so look-alikes come from the series' own stars only."
            self.recompute()
            return
        mags = app._series_star_mags()
        mymag = mags.get(self.name)
        if mymag is None:
            t = promotion.star_curve(app.observations, self.name, is_target=self.is_target,
                                     target_name=app.star_id.get().strip())
            mymag = float(np.nanmedian(t.mag)) if t.jd.size else None
        close_r = self._close_arcsec()
        w = self._window_mag()
        known = dict(sky)
        tok = self.busy.begin("Looking up the field's stars (Gaia DR3, APASS)…")
        fov = 30.0
        scale = getattr(self, "_scale", float("nan"))
        try:
            shape = app._gate_settings().get("frame_shape")
            if shape and math.isfinite(scale):
                fov = max(10.0, min(90.0, max(shape) * scale / 60.0))
        except Exception:
            pass

        def work():
            stars, note = [], ""
            try:
                found, failed = core.fetch_field_catalogs(here[0] / 15.0, here[1], fov, "V",
                                                          catalogs=("Gaia DR3", "APASS DR9"))
                stars = core.merge_catalogs(found, "V")
                if failed:
                    note = "Not reached: " + ", ".join(failed) + "."
            except Exception as exc:
                note = f"The catalogs were not reached ({type(exc).__name__}: {exc}); look-alikes come from the series' own stars."
            picks = nb.pick_neighbors(here[0], here[1], mymag, stars, known, window=w, close_arcsec=close_r,
                                      field_arcsec=fov * 60.0 / 2.0)
            app.call_ui(self._found, tok, picks, note)

        threading.Thread(target=work, daemon=True).start()

    def _found(self, tok, picks, note):
        self.busy.end(tok)
        try:
            if not self.win.winfo_exists():
                return
        except Exception:
            return
        # only stars the series' frames cover: placed on the first mapped frame with room for the sky ring
        app = self.app
        sky = app.neighbor_sky()
        cfg = app._gate_settings()
        fm = None
        for o in app.observations[:30]:
            fm = nb.FrameMap(o, sky, cfg["comp_names"], cfg["target"], cfg["check_name"])
            if fm.ok:
                break
        shape = cfg.get("frame_shape")
        so = _f(app.sky_out.get(), 0.0) or 0.0

        def on_frame(p):
            if fm is None or not fm.ok or not shape:
                return True
            x, y = fm.xy(p["ra"], p["dec"])
            return so <= x <= shape[1] - 1 - so and so <= y <= shape[0] - 1 - so
        picks["lookalikes"] = [p for p in picks["lookalikes"] if on_frame(p)]
        for p in picks["lookalikes"] + picks["close"]:
            p["key"] = p["name"]
        picks["searched"] = True
        self.picks = picks
        self.catalog_note = note
        self.recompute()

    # -- measuring on demand -----------------------------------------------------------------------------------
    def measure(self, stars=None, blend=False):
        ca = _ca()
        app = self.app
        todo = stars or list(self.pending)
        if not todo:
            return
        on_disk = [i for i, o in enumerate(app.observations) if os.path.isfile(o.path)]
        if not on_disk:
            messagebox.showinfo(ca.APP_TITLE, "The series' frames are no longer on disk, so its neighbors cannot be "
                                              "measured.", parent=self.win)
            return
        mins = max(1, round(len(on_disk) * SECONDS_PER_FRAME / 60))
        what = "the blend test (three apertures)" if blend else ", ".join(p["name"] for p in todo)
        if not messagebox.askyesno(ca.APP_TITLE, f"Measure {what} on {len(on_disk)} frame(s) of the series? About "
                                                 f"{mins} min (one pass over the frames for all of them).",
                                   parent=self.win):
            return
        if not app._start_job("Measuring neighbors…", bar=False):
            return
        tok = self.busy.begin("Measuring on the series' frames…")
        cfg = app._measure_cfg()
        used = {}
        cancel = app._cancel
        obs = app.observations
        gen = getattr(app, "_series_gen", 0)

        def progress(k, total, o):
            if k == 1 or k % 5 == 0 or k == total:
                app.call_ui(self._busy_words, tok, f"Measuring on the series' frames: {k} of {total}…")

        def work():
            res, error = None, ""
            try:
                load = app.make_frame_loader(cfg, used)
                res = nb.measure_series(core, obs, todo, sky=cfg["sky"], comp_names=cfg["comps"],
                                        target_name=cfg["target"], check_name=cfg["check"], load=load,
                                        aperture_for=app.aperture_function(cfg), total_bin=cfg["total_bin"],
                                        indices=on_disk, progress=progress, cancel=cancel)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            app.call_ui(self._measured, tok, todo, res, error, used, blend, gen)

        threading.Thread(target=work, daemon=True).start()

    def _busy_words(self, tok, words):
        try:
            self.busy.words(tok, words)
        except Exception:
            pass

    def _measured(self, tok, todo, res, error, used, blend, gen):
        ca = _ca()
        app = self.app
        app._end_job()
        try:
            self.busy.end(tok)
        except Exception:
            pass
        if gen != getattr(app, "_series_gen", 0):
            app.log("Check neighbors: the series changed while measuring; the values were dropped.")
            return
        if error or res is None:
            app.log(f"✖ Check neighbors: measuring stopped: {error}")
            messagebox.showerror(ca.APP_TITLE, f"Measuring stopped:\n\n{error}", parent=self.win)
            return
        cal = "; ".join(sorted(set(used.values())))
        if blend:
            obs = app.observations
            curves = {k: nb.curve_from_values(promotion, obs, k, res["values"].get(k) or {}, role="blend")
                      for k in ("blend:c", "blend:t", "blend:a")}
            P = (self.result or {}).get("period")
            v = nb.blend_verdict(promotion, curves["blend:c"], curves["blend:t"], curves["blend:a"], P)
            v.update({"utc": nb.utc_now(), "companion": todo[1].get("companion"), "period": P})
            self.blend = v
            self.side.setdefault("checks", {}).setdefault(self.name, {})["blend"] = v
            app.log(v["text"])
        else:
            for p in todo:
                nb.sidecar_put(self.side, app.observations, p, res["values"].get(p["key"]) or {}, cal)
            app.log(f"Check neighbors: measured {', '.join(p['name'] for p in todo)} on {res['frames']} frame(s)"
                    + (f" ({len(res['failed'])} not measured)" if res["failed"] else "") + ".")
        try:
            path = nb.sidecar_save(app.series_path, self.side)
            app.log(f"  kept in {os.path.basename(path)}")
        except Exception as exc:
            app.log(f"⚠ Check neighbors: the measurements are not kept on disk ({type(exc).__name__}: {exc}); "
                    "they last until this window closes.")
        self.recompute()

    def run_blend(self):
        close = self.picks.get("close") or []
        sky = self.app.neighbor_sky()
        here = sky.get(self.name) if not self.is_target else sky.get(self.app.star_id.get().strip())
        if not close or here is None:
            return
        comp = close[0]
        base = {"name": self.name, "ra": here[0], "dec": here[1]}
        stars = [dict(base, key="blend:c"),
                 dict(base, key="blend:t", toward=(comp["ra"], comp["dec"]), sign=+1, companion=comp["name"]),
                 dict(base, key="blend:a", toward=(comp["ra"], comp["dec"]), sign=-1)]
        self.measure(stars=stars, blend=True)

    # -- drawing -----------------------------------------------------------------------------------------------
    def _binned(self, c, P, view):
        if c is None or c.jd.size == 0:
            return np.array([]), np.array([])
        y = promotion.remove_medians(c.mag, c.night) * 1000.0
        if view == "fold" and P:
            ph = ((c.jd - c.jd.min()) / P) % 1.0
            edges = np.linspace(0, 1, self.PHASE_BINS + 1)
            k = np.digitize(ph, edges) - 1
            xs = 0.5 * (edges[1:] + edges[:-1])
            ys = np.array([np.median(y[k == j]) if np.any(k == j) else np.nan for j in range(self.PHASE_BINS)])
            return np.r_[xs, xs + 1], np.r_[ys, ys]
        last = c.night[int(np.argmax(c.jd))]
        m = np.asarray(c.night) == last
        t = (c.jd[m] - c.jd[m].min()) * 24.0
        yy = y[m]
        w = self.TIME_BIN_MIN / 60.0
        k = np.floor(t / w).astype(int)
        xs, ys = [], []
        for j in np.unique(k):
            xs.append(np.mean(t[k == j]))
            ys.append(np.median(yy[k == j]))
        return np.asarray(xs), np.asarray(ys)

    def _lines(self):
        """The rows to draw, in order, with a group heading before each group: ("head", text) or
        ("row", group, curve or None, row dict or None, pending pick or None)."""
        res = self.result
        rows = [("target", self.curves["target"], None, None)]
        rows += [(g, c, r, None) for (g, c), r in zip(self.curves["rows"], res["rows"])]
        close_names = {p["name"] for p in self.picks.get("close") or []}
        for p in self.pending:
            rows.append(("close" if p["name"] in close_names else "look", None, None, p))
        order = {"target": 0, "close": 1, "look": 2, "comps": 3}
        rows.sort(key=lambda x: (order.get(x[0], 9), (x[3] or {}).get("sep", 0) if x[3] else
                                 ((x[2] or {}).get("sep") or (x[2] or {}).get("sep_px") or 0)))
        out, prev = [], None
        for g, c, r, p in rows:
            if g != prev:
                head = GROUP_WORDS.get(g, g)
                if g == "look":
                    head = f"Look-alikes (±{self._window_mag():.1f} mag)"
                out.append(("head", head))
                prev = g
            out.append(("row", g, c, r, p))
        return out

    def draw(self):
        if self.fig is None or self.result is None:
            return
        fig = self.fig
        fig.clear()
        res = self.result
        lines = self._lines()
        view = self.view.get()
        P = res.get("period")
        if view == "fold" and not P:
            view = "time"
        tx, ty = self._binned(self.curves["target"], P, view)
        span = float(np.nanmax(np.abs(ty))) if ty.size and np.isfinite(ty).any() else 10.0
        lim = max(10.0, 1.25 * span)
        units = sum(0.45 if ln[0] == "head" else 1.0 for ln in lines) + 0.6
        top = 1.0 - 0.6 / units
        y = top
        x0, x1 = 0.25, 0.70
        # the field thumbnail: where each star sits on the first frame
        try:
            self._thumb([ln for ln in lines if ln[0] == "row"])
        except Exception:
            pass
        for ln in lines:
            if ln[0] == "head":
                y -= 0.45 / units
                fig.text(0.005, y + 0.12 / units, ln[1].upper(), fontsize=7.5, fontweight="bold",
                         color="#14294a", va="bottom")
                fig.lines.append(__import__("matplotlib").lines.Line2D([0.005, 0.99], [y + 0.1 / units] * 2,
                                                                       transform=fig.transFigure, color="#e3e8f0",
                                                                       lw=0.8))
                continue
            _k, g, c, r, p = ln
            h = 1.0 / units
            y -= h
            yc = y + 0.5 * h
            name = c.name if c is not None else p["name"]
            if c is not None:
                ax = fig.add_axes([x0, y + 0.08 * h, x1 - x0, 0.84 * h])
                xs, ys = self._binned(c, P, view)
                ax.plot(xs, ys, "-o", ms=2.3, lw=1.6 if g == "target" else 1.0,
                        color="#1f4f96" if g == "target" else "#5b6676")
                ax.axhline(0, color="#c8d1de", lw=0.8, ls=":")
                ax.set_ylim(lim, -lim)   # fainter is down
                ax.set_yticks([])
                ax.set_xticks([])
                ax.set_facecolor("#eef4fd" if g == "target" else "#f7f9fc")
                for sp in ax.spines.values():
                    sp.set_visible(False)
            else:
                fig.text(x0 + 0.01, yc, "not measured yet: press Measure look-alikes", fontsize=8.5, color="#6b7780",
                         va="center")
            bits = []
            src = r if r is not None else p
            if src is not None:
                if src.get("sep") is not None:
                    bits.append(f"{src['sep'] / 60:.1f}′" if src["sep"] >= 60 else f"{src['sep']:.0f}″")
                elif src.get("sep_px") is not None:
                    bits.append(f"{src['sep_px']:.0f} px")
                if src.get("dmag") is not None:
                    bits.append(f"ΔV {src['dmag']:+.2f}")
                role = (r or {}).get("role")
                if role in ROLE_WORDS:
                    bits.append(ROLE_WORDS[role])
                if p is not None:
                    bits.append("not in the series yet")
            fig.text(0.005, yc + 0.18 * h, name[:34], fontsize=8.5, fontweight="bold", va="center")
            fig.text(0.005, yc - 0.22 * h, " · ".join(bits), fontsize=7.5, color="#4a5568", va="center")
            if g == "target":
                amp = res.get("amp")
                fig.text(0.73, yc, "—", fontsize=8.5, va="center")
                fig.text(0.815, yc, f"{amp * 1000:.1f} mmag" if amp else "—", fontsize=8.5, va="center")
                fig.text(0.905, yc, "Target", fontsize=8.5, color="#1f4f96", fontweight="bold", va="center")
            elif r is not None:
                ps = r.get("power_share")
                fig.text(0.73, yc, f"{ps * 100:.1f}%" if ps is not None else "—", fontsize=8.5, va="center")
                fig.text(0.815, yc, f"{r['amp'] * 1000:.1f} mmag" if r.get("amp") is not None else "—",
                         fontsize=8.5, va="center")
                q = r.get("quiet")
                fig.text(0.905, yc, "✓ Quiet" if q else ("⚠ Signal at P" if q is False else "too few points"),
                         fontsize=8.5, va="center", fontweight="bold",
                         color="#146c2e" if q else ("#b23b2b" if q is False else "#6b7780"))
        fig.text(0.73, 0.995, "Power at P", fontsize=8, color="#4a5568", va="top")
        fig.text(0.815, 0.995, "Amp at P", fontsize=8, color="#4a5568", va="top")
        fig.text(x0, 0.995, (f"One shared scale: ±{lim:.0f} mmag, fainter is down. "
                             + (f"Folded at {P:.5f} d, {self.PHASE_BINS} phase bins." if view == "fold" else
                                f"Time: the last night, {self.TIME_BIN_MIN:.0f}-min bins.")),
                 fontsize=8, color="#4a5568", va="top")
        self.canvas.draw_idle()

    def _thumb(self, rows):
        """A small map of the field: the star, its neighbors and the comps, where they sit on one frame."""
        if self.mode == "scan":
            xy = np.asarray(self.scan["xy"], float)
            labels = [str(x) for x in self.scan["labels"]]
            pos = {lab: tuple(xy[k]) for k, lab in enumerate(labels)}
        else:
            app = self.app
            cfg = app._gate_settings()
            sky = app.neighbor_sky()
            fm = None
            for o in app.observations[:30]:
                fm = nb.FrameMap(o, sky, cfg["comp_names"], cfg["target"], cfg["check_name"])
                if fm.ok:
                    break
            if fm is None or not fm.ok:
                return
            pos = dict(fm.points)
            for side in (self.side.get("stars") or {}).items():
                pos.setdefault(side[0], fm.xy(side[1]["ra"], side[1]["dec"]))
            for pk in (self.picks.get("lookalikes") or []) + (self.picks.get("close") or []):
                pos.setdefault(pk["name"], fm.xy(pk["ra"], pk["dec"]))
            if self.is_target:
                pos.setdefault(self.name, pos.get(cfg["target"]))
        me = pos.get(self.name)
        if not me or not all(map(math.isfinite, me)):
            return
        fig = self.thumb_fig
        if fig is None:
            return
        fig.clear()
        ax = fig.add_axes([0.02, 0.1, 0.96, 0.84])
        ax.set_facecolor("#0b1424")
        ax.set_xticks([])
        ax.set_yticks([])
        pts = np.asarray([v for v in pos.values() if v and all(map(math.isfinite, v))], float)
        ax.plot(pts[:, 0], pts[:, 1], ".", color="#9fb3cf", ms=2)
        style = {"close": ("#e8710a", "o"), "look": ("#2fa84f", "o"), "comps": ("#7a8aa3", "s")}
        for _k, g, c, r, p in rows:
            nm = c.name if c is not None else p["name"]
            v = pos.get(nm)
            if not v or not all(map(math.isfinite, v)):
                continue
            col, mk = style.get(g, ("#1f6fd1", "o"))
            ax.plot([v[0]], [v[1]], mk, mfc="none", mec=col if g != "target" else "#1f6fd1", ms=9 if g == "target"
                    else 7, mew=2 if g == "target" else 1.3, ls="none")
        half = max(np.nanmax(np.abs(pts - np.asarray(me)), axis=0)) * 1.05 if pts.size else 100
        ax.set_xlim(me[0] - half, me[0] + half)
        ax.set_ylim(me[1] + half, me[1] - half)
        ax.set_title("Field around the star", fontsize=8, color="#14294a")
        fig.text(0.02, 0.02, "blue: the star · green: look-alikes\norange: close companions · grey: comps, check",
                 fontsize=6.5, color="#4a5568")
        self.thumb_canvas.draw_idle()

    # -- saving ------------------------------------------------------------------------------------------------
    def summary(self) -> dict:
        res = self.result or {}
        return {"utc": nb.utc_now(), "mode": self.mode, "period": res.get("period"), "bottom": res.get("bottom"),
                "n_quiet": res.get("n_quiet"), "n_measured": res.get("n_measured"), "window_mag": self._window_mag(),
                "rows": [{k: r.get(k) for k in ("name", "role", "group", "power_share", "amp", "quiet", "sep",
                                                 "dmag")} for r in res.get("rows") or []],
                "not_measured": [p["name"] for p in getattr(self, "pending", [])],
                "blend": self.blend}

    def save(self):
        ca = _ca()
        app = self.app
        s = self.summary()
        where = []
        if self.mode == "scan":
            r = self.scan
            r.setdefault("neighbor_checks", {})[self.scan_index] = s
            path = app._save_suspects(r) if hasattr(app, "_save_suspects") else ""
            where.append(os.path.basename(path) if path else "this night's results (no suspects file)")
        else:
            entry = next((w for w in app.watch_list if w["name"] == self.name), None)
            if entry is not None:
                entry["neighbors"] = s
                app._bump_watch_revision()
                where.append("the series (Save series to keep it)")
            self.side.setdefault("checks", {})[self.name] = s
            try:
                nb.sidecar_save(app.series_path, self.side)
                where.append(os.path.basename(nb.sidecar_path(app.series_path)))
            except Exception:
                pass
            try:
                doc = registry.load()
                sky = app.neighbor_sky()
                here = sky.get(self.name) if not self.is_target else sky.get(app.star_id.get().strip())
                rec = registry.find(doc, (entry or {}).get("gaia_dr3") or "", *(here or (None, None)))
                if rec is not None:
                    rec["neighbors"] = s
                    registry.save(doc)
                    where.append("its Registry record")
            except Exception as exc:
                app.log(f"⚠ Check neighbors: the Registry was not updated ({type(exc).__name__}: {exc}).")
        app.log(f"Check neighbors for {self.name}: {s['bottom']}. Saved to " + ", ".join(where) + ".")
        app.status.set(f"Check neighbors for {self.name} saved: {s['bottom']}.")
        self.note.set(f"Saved to {', '.join(where)}.")

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass
