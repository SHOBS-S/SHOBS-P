"""2.3.0 (#54): the Stack step in the window (all modes): the Photometry page's Stack row (suggestion line, N, the
optional precision goal, Stack), the Points: Per frame / Stacked switch beside the View box with its "Results use: …"
line, and what follows a switch (Output page, transit fit, reports, series).

A mixin of the App (clearv_app.App): it uses the App's own photometry run, job lock, busy bar, log and pages. The
first photometry run (per frame) makes the frames' records; Stack stacks them (stack_core), then runs the same
photometry on the stacks. Both point sets are kept for the night; the Points switch swaps which one is the night's
points in the series, so the Output page, the period tools, the AAVSO report, the transit fit, the CSV and the
saved series all use the chosen one. The transit fit of each set is cached, so switching back is instant.
Nothing here needs a plate solution, header pointing or a color camera.
"""

from __future__ import annotations

import math
import os
import threading
import traceback
from tkinter import LEFT, RIGHT, W, X, StringVar, messagebox, ttk

import numpy as np

import photometry_core as core
import stack_core

TITLE = "SHOBS-P"


def is_stack_path(path: str) -> bool:
    """A stack file made by the Stack step (in the stacks cache)."""
    try:
        p = os.path.normcase(os.path.abspath(str(path)))
        root = os.path.normcase(os.path.abspath(stack_core.STACK_CACHE_DIR))
        return p.startswith(root + os.sep) and os.path.basename(p).startswith("stack_")
    except Exception:
        return False


def load_frame(path: str, binning: int, masters):
    """(raw, header, calibrated binned mosaic) for photometry, the field scan and the aperture test. A stack is
    already calibrated and binned: it comes back as it is, with an empty raw (no raw counts: its frames' raw peaks
    were checked, and are carried by stack_core.apply_member_peaks)."""
    if is_stack_path(path):
        img, header = stack_core.read_stack(path)
        return np.empty((0, 0), dtype=np.float32), header, img
    raw, header = core.read_fits(path)
    return raw, header, core.calibrate_frame(core.bin_image(raw, binning), header, masters)


class StackMixin:
    """The App's stacking methods (see the module text)."""

    # ---- the page parts --------------------------------------------------------------------------------------
    def _stack_build_ui(self, card, options, view_box):
        """Called by _page_photo once the View box is built."""
        self.stack_n = StringVar(value="")
        self.stack_goal = StringVar(value="")
        self.points_choice = StringVar(value="frame")
        self.stack_line = StringVar(value="")
        self.results_use = StringVar(value="Results use: Per frame")
        self._stack_sets = {}       # night -> {"frame": [Obs], "stack": [Obs], "meta": summary, "paths": [...]}
        self._stack_source = None   # the last per-frame run: {"night", "points", "mode"} (or a Discovery scan)
        self._stack_fit_cache = {}
        self._stack_shown = False
        self._stack_run_ctx = None
        self._stack_n_typed = False
        # The Points switch, its own small box beside the View box (Option A, John 3 Nov).
        box = ttk.LabelFrame(options, text="Points", style="Group.TLabelframe", padding=(8, 2, 8, 4))
        row = ttk.Frame(box, style="Card.TFrame")
        row.pack(side="top", anchor=W)
        self.points_frame_button = ttk.Radiobutton(row, text="Per frame", value="frame", variable=self.points_choice,
                                                   command=self._points_switched)
        self.points_frame_button.pack(side=LEFT)
        self.points_stack_button = ttk.Radiobutton(row, text="Stacked", value="stack", variable=self.points_choice,
                                                   command=self._points_switched)
        self.points_stack_button.pack(side=LEFT, padx=(8, 0))
        use = ttk.Label(box, textvariable=self.results_use, style="Hint.TLabel", justify=LEFT)
        use.pack(side="top", anchor=W, pady=(2, 0))
        # the line wraps at the switch's own width, so the box never needs more room than the two buttons
        row.bind("<Configure>", lambda e: use.configure(wraplength=max(int(e.width), 100)), add="+")
        self._points_box, self._points_view_box = box, view_box
        options.bind("<Configure>", lambda _e: self.after_idle(self._stack_place_points_box), add="+")
        # The Stack row: shown under the settings once a run has points.
        srow = ttk.Frame(card, style="Card.TFrame")
        self.stack_line_label = ttk.Label(srow, textvariable=self.stack_line, style="Hint.TLabel", justify=LEFT)
        self.stack_line_label.pack(side="top", anchor=W, fill=X)
        ctl = ttk.Frame(srow, style="Card.TFrame")
        ctl.pack(side="top", anchor=W, pady=(2, 0))
        ttk.Label(ctl, text="Stack N", style="Card.TLabel").pack(side=LEFT, padx=(0, 2))
        n_entry = ttk.Entry(ctl, textvariable=self.stack_n, width=6)
        n_entry.pack(side=LEFT)
        n_entry.bind("<Key>", lambda _e: setattr(self, "_stack_n_typed", True), add="+")
        ttk.Label(ctl, text="frames   Precision goal", style="Card.TLabel").pack(side=LEFT, padx=(8, 2))
        ttk.Entry(ctl, textvariable=self.stack_goal, width=6).pack(side=LEFT)
        ttk.Label(ctl, text="ppt per point (optional)", style="Card.TLabel").pack(side=LEFT, padx=(2, 8))
        self.stack_button = ttk.Button(ctl, text="Stack", command=self.start_stack)
        self.stack_button.pack(side=LEFT)
        srow.bind("<Configure>", lambda e: self.stack_line_label.configure(wraplength=max(int(e.width) - 20, 300)),
                  add="+")
        self._stack_row, self._stack_anchor = srow, options
        self.stack_goal.trace_add("write", lambda *_a: self.after_idle(lambda: self._stack_refresh_line(fill_n=True)))

    def _stack_place_points_box(self):
        """Keep the Points box just left of the View box, wherever _layout_view_box has put the View box."""
        box, vb = getattr(self, "_points_box", None), getattr(self, "_points_view_box", None)
        if box is None or vb is None:
            return
        try:
            if not self._stack_shown:
                if box.winfo_manager():
                    box.pack_forget()
                return
            master = vb.pack_info().get("in")
            slaves = master.pack_slaves()
            if box in slaves and slaves.index(box) > slaves.index(vb):
                return
            box.pack(in_=master, side=RIGHT, padx=(10, 0), after=vb)
        except Exception:
            pass

    def _stack_ui_update(self):
        """Show or hide the Stack row and the Points box, and say what the results use."""
        if not hasattr(self, "_stack_row"):
            return
        src = self._stack_source_live()
        night = src["night"] if src else None
        live = night is not None and self._stack_set_live(night)
        if not live and self.points_choice.get() != "frame":
            self.points_choice.set("frame")
        shown = src is not None
        try:
            if shown and not self._stack_shown:
                self._stack_row.pack(fill=X, padx=6, pady=(0, 4), after=self._stack_anchor)
            elif not shown and self._stack_shown:
                self._stack_row.pack_forget()
        except Exception:
            pass
        self._stack_shown = shown
        self._stack_place_points_box()
        try:
            self.points_stack_button.configure(state="normal" if live else "disabled")
        except Exception:
            pass
        self.results_use.set(self.results_use_text(night))
        lab = getattr(self, "out_points_label", None)
        if lab is not None:
            try:
                lab.configure(text=self.results_use.get() if shown else "")
            except Exception:
                pass

    def results_use_text(self, night=None) -> str:
        """'Results use: Stacked (40 frames)' / 'Results use: Per frame'."""
        if night and self.points_choice.get() == "stack" and self._stack_set_live(night):
            meta = self._stack_sets[night].get("meta") or {}
            return f"Results use: Stacked ({int(meta.get('n') or 0)} frames)"
        return "Results use: Per frame"

    # ---- which night, and is it still the one on screen ------------------------------------------------------
    def _stack_source_live(self):
        src = getattr(self, "_stack_source", None)
        if not src:
            return None
        if src.get("scan"):
            return src if self.mode_key == "discovery" else None
        night = src["night"]
        now = {id(o) for o in self.observations if o.night == night}
        if not now:
            return None
        sets = self._stack_sets.get(night)
        if now == {id(o) for o in src["points"]} or (sets and now == {id(o) for o in sets["stack"]}):
            return src
        return None

    def _stack_set_live(self, night) -> bool:
        sets = getattr(self, "_stack_sets", {}).get(night)
        if not sets:
            return False
        if sets.get("scan"):
            return self.mode_key == "discovery"
        now = {id(o) for o in self.observations if o.night == night}
        return bool(now) and (now == {id(o) for o in sets["frame"]} or now == {id(o) for o in sets["stack"]})

    def stack_meta(self, night) -> dict | None:
        """The night's stacking record (series night setup "stack"), or None when its points are per frame."""
        try:
            info = self._night_info(night) if night else {}
        except Exception:
            info = {}
        meta = info.get("stack") if isinstance(info, dict) else None
        return meta if isinstance(meta, dict) and int(meta.get("n") or 1) > 1 else None

    # ---- after the per-frame run -----------------------------------------------------------------------------
    def _stack_after_run(self, night, night_points):
        """_photometry_done: a new per-frame run is the source of any stacks (earlier stacks of the night go)."""
        if not hasattr(self, "_stack_row"):
            return
        self._stack_source = {"night": night, "points": list(night_points), "mode": self.mode_key}
        self._stack_sets.pop(night, None)
        self._stack_fit_cache = {}
        self.points_choice.set("frame")
        try:
            self._night_info(night, create=True).pop("stack", None)
        except Exception:
            pass
        self._stack_n_typed = False
        self._stack_shown = False   # re-shown below
        self._stack_ui_update()
        self._stack_refresh_line(fill_n=True, log=True)

    def _stack_after_scan(self, result):
        """_scan_done (Discovery): the per-frame scan is the source of any stacks."""
        if not hasattr(self, "_stack_row") or self.mode_key != "discovery":
            return
        if not result.get("stack_frames"):
            return
        self._stack_source = {"night": "scan", "scan": result, "points": [], "mode": "discovery"}
        self._stack_sets.pop("scan", None)
        self.points_choice.set("frame")
        self._stack_n_typed = False
        self._stack_shown = False
        self._stack_ui_update()
        self._stack_refresh_line(fill_n=True, log=True)

    def _stack_records(self, src=None):
        src = src or self._stack_source_live()
        if not src:
            return []
        if src.get("scan"):
            return [dict(r) for r in src["scan"].get("stack_frames") or []]
        return [stack_core.frame_record(o) for o in src["points"]]

    def _stack_goal_value(self):
        text = self.stack_goal.get().strip() if hasattr(self, "stack_goal") else ""
        if not text:
            return None
        try:
            v = float(text)
            return v if math.isfinite(v) and v > 0 else None
        except ValueError:
            return None

    def _stack_cap(self, mode):
        planet = None
        if mode == "transits":
            try:
                planet = self.planet_from_fields()
            except Exception:
                planet = None
        period = getattr(self, "catalog_period", None) if mode == "variables" else None
        try:
            period = float(period) if period else None
        except (TypeError, ValueError):
            period = None
        return stack_core.time_cap(mode, planet, period)

    def _stack_refresh_line(self, fill_n: bool = False, log: bool = False):
        """The suggestion line (and N, unless the user typed one) from the per-frame run's own noise numbers."""
        src = self._stack_source_live() if hasattr(self, "_stack_row") else None
        if not src:
            return None
        mode = src["mode"]
        recs = self._stack_records(src)
        kept = [r for r in recs if r["keep"]]
        cadence = stack_core.cadence_seconds(recs)
        cap = self._stack_cap(mode)
        noise = None
        if not src.get("scan"):
            try:
                noise = stack_core.noise_check(src["points"], self._scint_setup(), core.point_err)
            except Exception:
                noise = None
        goal = self._stack_goal_value()
        sug = stack_core.suggest_n(noise, cadence, cap, len(kept), goal)
        if mode == "discovery":
            # no comps to judge: up to the cap, leaving at least MIN_POINTS stacks (a scan needs 10 frames)
            sug["n"] = max(1, min(sug["n_cap"] or 1, len(kept) // stack_core.MIN_POINTS or 1))
            sug["n_stacks"] = int(math.ceil(len(kept) / sug["n"])) if kept else 0
            sug["span_s"] = sug["n"] * cadence if math.isfinite(cadence) else float("nan")
        text, warn = stack_core.suggest_text(mode, noise, sug, cap, len(kept), goal)
        out = recs and len(kept) < len(recs)
        if out:
            parts = ", ".join(f"{n} {why}" for why, n in sorted(stack_core.left_out_counts(recs).items()))
            text += f" ({len(recs) - len(kept)} frames left out of stacks: {parts}.)"
        self.stack_line.set(text)
        try:
            self.stack_line_label.configure(style="Warn.TLabel" if warn else "Hint.TLabel")
        except Exception:
            pass
        if fill_n and (not self._stack_n_typed or goal):
            self.stack_n.set(str(sug["n"]))
            self._stack_n_typed = False
        self._stack_sug = {"sug": sug, "noise": noise, "cap": cap, "cadence": cadence}
        if log:
            if noise:
                self.log(f"Noise check (per frame): target {noise['sig_t'] * 1000:.1f} mmag (SNR {noise['snr_t']:.0f}), "
                         f"comp ensemble {noise['sig_c'] * 1000:.1f} mmag (SNR {noise['snr_c']:.0f}; each comp "
                         + ", ".join(f"{s:.0f}" for s in noise["comp_snr"]) + ") over "
                         f"{noise['n']} frames; comp noise {noise['ratio']:.2f}× the target's.")
            self.log(text.replace("⚠ ", "⚠ Stacking: ", 1) if warn else
                     (text if text.startswith("Stacking") else "Stacking: " + text))
        return sug

    # ---- Stack -----------------------------------------------------------------------------------------------
    def start_stack(self):
        if self.busy:
            messagebox.showinfo(TITLE, "Another step is still running. Wait for it to finish.")
            return
        src = self._stack_source_live()
        if not src:
            messagebox.showinfo(TITLE, "Run photometry first (Scan field in Discovery): the Stack step uses that "
                                       "run's frames, alignment and noise.")
            return
        try:
            n = int(float(self.stack_n.get().strip()))
        except ValueError:
            messagebox.showerror(TITLE, "Type the stack size N (frames per stack, 2 or more) in the Stack N box.")
            return
        if n < 2:
            messagebox.showerror(TITLE, "N is the number of frames per stack: 2 or more (1 means no stacking: choose "
                                        "Points: Per frame).")
            return
        if self.masters is None:
            messagebox.showerror(TITLE, "Calibrate first (Calibrate page): the stacks are made of calibrated frames.")
            return
        recs = self._stack_records(src)
        kept = [r for r in recs if r["keep"]]
        if len(kept) < 2:
            messagebox.showerror(TITLE, "Fewer than 2 frames can be stacked (the others are flagged, or could not be "
                                        "aligned).")
            return
        try:
            binning = max(int(self.binning.get()), 1)
        except (TypeError, ValueError):
            binning = 1
        if self.masters_binning is not None and binning != self.masters_binning:
            messagebox.showerror(TITLE, "Binning changed since the masters were built. Rebuild the masters first.")
            return
        cadence = stack_core.cadence_seconds(recs)
        cap = self._stack_cap(src["mode"])
        if math.isfinite(cadence) and n * cadence > 1.5 * cap["seconds"]:
            if not messagebox.askyesno(TITLE, f"Stacks of {n} frames last about {stack_core._dur(n * cadence)}, longer "
                                              f"than the time resolution allows ({cap['text']}): they may blur the "
                                              "signal.\n\nStack anyway?"):
                return
        groups = stack_core.plan_groups(recs, n, cadence)
        if src.get("scan") and len(groups) < stack_core.MIN_POINTS:
            messagebox.showerror(TITLE, f"That makes only {len(groups)} stacks; a field scan needs at least "
                                        f"{stack_core.MIN_POINTS}. Use a smaller N.")
            return
        if src["mode"] == "transits" and len(groups) < 20:
            if not messagebox.askyesno(TITLE, f"That makes only {len(groups)} stacked points; the transit fit needs "
                                              "20 or more.\n\nStack anyway?"):
                return
        pattern, mode = self.pattern.get(), self.debayer_mode.get()
        scale = 1 if pattern == core.MONO else 2
        masters = self.masters
        key = [[r["path"] for r in g["members"]] for g in groups] + [binning, pattern, n, stack_core.STACK_VERSION,
                                                                     stack_core.CLIP_SIGMA,
                                                                     stack_core.masters_fingerprint(masters)]
        folder = stack_core.stack_folder(key)
        total = sum(len(g["members"]) for g in groups)
        if not self._start_job(f"Stacking {total} frames into {len(groups)} stacks…"):
            return
        self._progress_start(len(groups), "Stacking")
        cancel = self._cancel
        dropped = stack_core.left_out_counts(recs)
        self.log(f"Stacking {total} frames into {len(groups)} stacks of up to {n} (sigma-clipped mean; whole-pixel "
                 "shifts from the measured drift; never across a meridian flip or a gap)"
                 + (": left out first: " + ", ".join(f"{k} {w}" for w, k in sorted(dropped.items())) if dropped else "")
                 + ".")

        def work():
            paths, meta, turns, clipped, shifts_max = [], {}, [], [], 0
            try:
                os.makedirs(folder, exist_ok=True)
                for gi, g in enumerate(groups, 1):
                    if cancel.is_set():
                        raise RuntimeError("Canceled")
                    members = g["members"]
                    out = os.path.join(folder, f"stack_{gi:04d}.fits")
                    shifts, anchor, turn = stack_core.group_shifts(members, scale)
                    shifts_max = max([shifts_max] + [max(abs(a), abs(b)) // scale for a, b in shifts])
                    turns.append(turn)
                    reuse = False
                    if os.path.isfile(out):
                        try:
                            head = core.read_header(out)
                            reuse = (int(head.get("STACKN", 0)) == len(members)
                                     and int(head.get("SHOBSSTK", 0)) == stack_core.STACK_VERSION)
                            info = {"clipped": float(head.get("STACKREJ", 0.0)), "plain": not head.get("STACKCLP")}
                        except Exception:
                            reuse = False
                    if not reuse:
                        img, info = stack_core.combine(lambda p: load_frame(p, binning, masters)[2], members, shifts,
                                                       cancel=cancel)
                        try:
                            anchor_head = core.read_header(members[anchor]["path"])
                        except Exception:
                            anchor_head = {}
                        labels = core.frame_labels_for([m["path"] for m in members])
                        head = stack_core.stack_header(anchor_head, members, info, [m["jd"] for m in members], gi,
                                                       len(groups), g["partial"], labels)
                        stack_core.write_stack(out, img, head)
                        del img
                    clipped.append(float(info.get("clipped", 0.0)))
                    paths.append(out)
                    meta[out] = {"members": members, "partial": g["partial"]}
                    self.call_ui(self._progress, gi)
                summ = stack_core.summary(groups, n)
                summ.update(turn=float(max(turns) if turns else 0.0), clipped=float(np.mean(clipped) if clipped else 0),
                            shift=int(shifts_max), mode=src["mode"])
                self.call_ui(self._stack_made, src, paths, meta, summ, folder, None)
            except Exception as exc:
                if str(exc) != "Canceled":
                    traceback.print_exc()
                self.call_ui(self._stack_made, src, [], {}, {}, folder, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _stack_made(self, src, paths, meta, summ, folder, error):
        self._end_job()
        if error:
            self._progress_end("Canceled." if error == "Canceled" else "Failed.")
            if error == "Canceled":
                self.log("Stacking canceled; the points stay per frame.")
            else:
                self.log(f"Stacking failed: {error}. The points stay per frame.")
                messagebox.showerror(TITLE, f"Stacking failed: {error}")
            return
        self._progress_end(f"{len(paths)} stacks.")
        stack_core.prune_folders(folder)
        words = stack_core.stack_words(summ)
        self.log(f"Stacks made: {summ['frames']} frames → {summ['stacks']} {words}; {summ['clipped'] * 100:.3f}% of the "
                 f"pixel values clipped (cosmic rays, satellite trails, hot pixels); shifts up to {summ['shift']} px."
                 + (" The last stack of a run (before a flip, a gap, or the end) can be partial." if summ["partial"]
                    else ""))
        if summ.get("turn", 0.0) > 0.05 and self.preview is not None:
            h, w = self.preview.shape[:2]
            px = math.radians(summ["turn"]) * math.hypot(h, w) / 2.0
            if px > 1.0:
                self.log(f"  Note: the field turned by up to {summ['turn']:.2f}° inside a stack (about {px:.1f} px at "
                         "the frame's corners): stars far from the center are slightly smeared in the stacks.")
        if summ.get("mode") == "discovery":
            self.log("  Note: the scan on the stacks cannot see changes faster than one stack "
                     f"({stack_core._dur(summ['exp'])} of exposure).")
        run = {"paths": paths, "meta": meta, "night": src["night"], "summary": summ, "src": src}
        self._stack_run_ctx = run
        if src.get("scan"):
            self.scan_field(stack_run=run)
        else:
            self.run_photometry(stack_run=run)
        if self._stack_run_ctx is run and not self.busy:
            self._stack_run_ctx = None   # the run did not start (a check said no)

    # ---- after photometry on the stacks ----------------------------------------------------------------------
    def _stack_done(self, night_points, mode, color_info=None, cancelled=False, error=None):
        """The photometry worker's end for a stack run (in place of _photometry_done)."""
        self._end_job()
        run, self._stack_run_ctx = self._stack_run_ctx, None
        measured = list(self.comps_used)
        if run is not None and run.get("saved"):
            saved = run["saved"]
            self._prev_stars = saved["prev_stars"]
            self.comps_used = list(saved["comps_used"])
            self.check_used = saved["check_used"]
        if cancelled or run is None:
            self._progress_end("Canceled." if not error else "Stopped by an error.")
            self.log("Photometry on the stacks " + (f"stopped by an error ({error})" if error else "canceled")
                     + "; the points stay per frame.")
            return
        self._progress_end(f"{len(night_points)} stacked points.")
        if not night_points:
            messagebox.showerror(TITLE, "No stack produced a magnitude (the Log says why). The points stay per frame.")
            return
        night = run["night"]
        filt = self._filter_code()
        for o in night_points:
            o.night = night
            o.filt = filt
        layout = list(self.comps_used)
        if measured and measured != layout:
            core.remap_night_comps(night_points, measured, layout)
        frames_now = [o for o in self.observations if o.night == night]
        if not frames_now:
            self.log("Stacks: the night is no longer in the series; nothing changed.")
            return
        # 2.3.0 review fix: the per-frame set is the run's source, never the stacks on screen (pressing Stack again
        # while Points: Stacked is shown used to store the old stacks as the night's "Per frame" points).
        frame_set = self._stack_frame_set(night, run.get("src"), frames_now)
        self._stack_sets[night] = {"frame": frame_set, "stack": night_points, "meta": run["summary"],
                                   "paths": list(run["paths"])}
        self.points_choice.set("stack")
        self._apply_points(night, "stack", announce=False)
        words = stack_core.stack_words(run["summary"])
        flagged = sum(1 for o in night_points if o.flag)
        self.log(f"Photometry on the stacks done: {len(night_points)} points ({words}), {flagged} flagged. "
                 "Points: Stacked is selected; Per frame (beside the View box) switches back.")
        self._stack_seeing_note(run)
        try:
            self.show_step(self.STACK_STEP_OUTPUT)
        except Exception:
            pass

    def _stack_frame_set(self, night, src, frames_now):
        """The night's per-frame points for a new stack set: the earlier set's per-frame points, else the run's
        source points, else the night's points on screen when they are not stacks."""
        prior = self._stack_sets.get(night)
        if prior and not prior.get("scan") and prior.get("frame"):
            return list(prior["frame"])
        if src and src.get("points") and not src.get("scan"):
            return list(src["points"])
        return [o for o in frames_now if not is_stack_path(getattr(o, "path", ""))] or frames_now

    def _stack_seeing_note(self, run):
        """Re-check the aperture against the stacks (stars in a stack are a little wider than in one frame)."""
        try:
            img = core.debayer(load_frame(run["paths"][len(run["paths"]) // 2], 1, None)[2], self.pattern.get(),
                               self.debayer_mode.get())
            f_stack = core.field_fwhm(img)["fwhm"]
            f_ref = core.field_fwhm(self.preview)["fwhm"] if self.preview is not None else float("nan")
        except Exception:
            return
        if not (math.isfinite(f_stack) and math.isfinite(f_ref) and f_ref > 0):
            return
        try:
            r = float(self.radius.get())
        except (TypeError, ValueError):
            r = float("nan")
        prop, prop1 = core.suggest_apertures(f_stack), core.suggest_apertures(f_ref)
        text = (f"Aperture check on the stacks: star FWHM {f_stack:.1f} px (one frame: {f_ref:.1f} px), so the "
                f"suggestion is r {prop['radius']:g} px on the stacks, r {prop1['radius']:g} px on one frame")
        if f_stack > 1.15 * f_ref:
            text = ("⚠ " + text + f". The stacks' stars are {f_stack / f_ref - 1:.0%} wider (drift or seeing inside a "
                    f"stack). Suggest… (with Points: Stacked) tests apertures on the stacks"
                    + (f" (this run used r {r:g})" if math.isfinite(r) else "")
                    + "; with a new aperture, press Stack again (the stacks are reused, only the photometry is redone).")
        else:
            text += "; the aperture chosen for the frames suits the stacks."
        self.log(text)

    # ---- the Points switch -----------------------------------------------------------------------------------
    def _points_switched(self):
        src = self._stack_source_live()
        which = self.points_choice.get()
        if not src or not self._stack_set_live(src["night"]):
            self.points_choice.set("frame")
            self._stack_ui_update()
            return
        if self.busy:
            # a run or a fit is going on: the switch waits (it would change the data under it)
            self.points_choice.set("stack" if which == "frame" else "frame")
            messagebox.showinfo(TITLE, "Another step is still running. Switch the points when it is done.")
            return
        self._apply_points(src["night"], which)

    def _apply_points(self, night, which, announce: bool = True):
        sets = self._stack_sets.get(night)
        if not sets:
            return
        if sets.get("scan"):
            result = sets[which]
            self.last_scan = result
            try:
                self._show_scan_on_output(result)
            except Exception:
                pass
            self._stack_ui_update()
            if announce:
                self.log(self.results_use.get() + ".")
            return
        tp = getattr(self, "transit_page", None)
        before = "stack" if which == "frame" else "frame"
        current = {id(o) for o in self.observations if o.night == night}
        if current == {id(o) for o in sets[which]}:
            before = which
        if tp is not None and getattr(tp, "report", None) is None and getattr(tp, "result", None) is not None \
                and getattr(tp, "night", None) is not None and tp.night.get() == night:
            self._stack_fit_cache[before] = (tp._fit_signature, tp.result, tp.meta_for_files)
        new = sets[which]
        if self.active_comps and set(self.active_comps) != set(self.comps_used):
            try:
                core.recompute_with_comps(new, self.active_comps, self.comps_used, self._comp_catalog(),
                                          self._star_aliases())
            except Exception as exc:
                self.log(f"Could not apply the chosen comps to the {which} points: {exc}")
        self.observations = core.merge_nights([o for o in self.observations if o.night != night], new)
        info = self._night_info(night, create=True)
        if which == "stack":
            info["stack"] = {k: v for k, v in (sets.get("meta") or {}).items() if k != "mode"}
        else:
            info.pop("stack", None)
        self._refresh_period()
        if tp is not None and getattr(tp, "report", None) is None:
            cached = self._stack_fit_cache.get(which)
            try:
                on_screen = bool(getattr(self, "transit_page_on_screen", lambda: False)())
                # a set without a cached fit is fitted at once while the Transit fit page is on screen
                tp.refresh(shown=on_screen and cached is None)
                if cached is not None and cached[0] is not None and tp._signature() == cached[0] \
                        and tp.night.get() == night:
                    import transit_core as tc
                    tp.result, tp._fit_signature, tp.meta_for_files = cached[1], cached[0], cached[2]
                    tp._fill_table()
                    tp._count_text()
                    tp._notes_text(tc.cautions(tp.result["flags"], tp.result.get("verdict")))
                    tp.draw()
                    tp.prog_text.set("✔ Fit of the " + ("stacked" if which == "stack" else "per-frame")
                                     + " points (kept from before).")
            except Exception:
                traceback.print_exc()
        try:
            self.draw_output()
        except Exception:
            traceback.print_exc()
        self._stack_ui_update()
        if announce:
            self.log(self.results_use.get() + f" for {night}.")
        if self.series_path and getattr(self, "_hold_autosave", None) != night:
            try:
                self._write_series(self.series_path)
            except OSError as exc:
                self.log(f"Could not auto-save the series: {exc}")

    # ---- Discovery: the scan on the stacks -------------------------------------------------------------------
    def _stack_scan_done(self, result):
        """_scan_done: a per-frame scan becomes the source of stacks; a scan on stacks is kept beside it."""
        run = getattr(self, "_scan_stack_run", None)
        self._scan_stack_run = None
        if run is None:
            self._stack_after_scan(result)
            return
        self._stack_run_ctx = None
        result["stack"] = dict(run["summary"])
        self._stack_sets["scan"] = {"scan": True, "frame": run["src"]["scan"], "stack": result,
                                    "meta": run["summary"], "paths": list(run["paths"])}
        self.points_choice.set("stack")
        self._stack_ui_update()
        self.log(f"Field scan on the stacks ({stack_core.stack_words(run['summary'])}). Points: Per frame (beside the "
                 "View box) shows the per-frame scan again.")

    def _stack_test_paths(self):
        """The stacks, for Suggest…'s aperture test, while Points: Stacked is chosen; else None (the lights)."""
        src = self._stack_source_live() if hasattr(self, "_stack_row") else None
        if src and self.points_choice.get() == "stack" and self._stack_set_live(src["night"]):
            return list(self._stack_sets[src["night"]].get("paths") or []) or None
        return None

    # ---- reports ---------------------------------------------------------------------------------------------
    def stack_report_note(self, points, lead: bool = True) -> str:
        """The AAVSO report notes part: '; points are stacks of 40 frames (effective exposure 30 s)' when the
        reported points are stacks (one note when the nights agree, else per night). ASCII, no commas."""
        nights = sorted({o.night for o in points or []})
        notes = {}
        for n in nights:
            text = stack_core.report_note(self.stack_meta(n))
            if text:
                notes[n] = text
        if not notes:
            return ""
        if len(set(notes.values())) == 1 and len(notes) == len(nights):
            body = next(iter(notes.values()))
        else:
            body = "; ".join(f"{n}: {t}" for n, t in notes.items())
        body = body.replace(",", ";").replace("|", ";")
        return ("; " if lead else "") + body
