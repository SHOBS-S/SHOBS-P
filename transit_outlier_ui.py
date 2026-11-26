"""2.4.0 fit-in F3 (#117): single-point outliers on the Transit fit page (TransitPage's OutlierMixin).

Joanna's WASP-39 b night (MicroObservatory, 31 Mar 2022) had one frame at mid-transit +8.9 % high, about 6 sigma: a
cosmic ray or a satellite. SHOBS-P kept it, so its 10-minute bin stood out with a huge error bar and the point made up
much of the night's scatter (and so of the error bars the fit scales up).

John's call (decision 9, 26 Nov 2026): FLAG + OFFER. After every fit each point's residual is tested against the
robust (MAD x 1.4826) scatter of the residuals (transit_core.find_point_outliers: beyond 5 sigma, single points only;
two or more in a row are real structure and are left alone). A point that stands out gets

- a plain note (never a caution) naming the frame, its time and how far out it is,
- a red ring on the plot, and its bin is drawn without it (the legend says so),
- the button "Leave it out and refit". Nothing is dropped unless it is pressed; "Put it back" undoes it.

The one rule for everything else: a left-out point belongs to its night or report, exactly like a trim. It stays out
of every refit of that source (Trim and refit, Clear trim, a detrend change, Use comps, Refit with flagged frames) and
so out of the comp check, the Robustness check, the saved files and reports (they all use the fit's own points),
until Put it back. It goes back by itself, with a log line, when another night or report is chosen, the report is
closed, or the series changes. Left-out frames are named in the notes, the settings file, the report's NOTES and the
fit CSV (flag "trimmed outlier": Multi-night skips "trimmed" rows, so a saved fit CSV carries the choice there too).
"""
from __future__ import annotations

import math
import os
from tkinter import ttk

import numpy as np

import transit_core as tc

LEAVE_ONE, LEAVE_MANY = "Leave it out and refit", "Leave them out and refit"
PUT_ONE, PUT_MANY = "Put it back", "Put them back"
LEAVE_TIP = ("Refits without the point the notes name as standing far from the fit. Nothing is left out unless you "
             "press this; Put it back undoes it.")
PUT_TIP = "Refits with every point that was left out as a single-point outlier."
RED = "#c62828"
MATCH_DAYS = 1e-7    # two times this close are the same frame (9 ms), as for trimmed frames
MIN_POINTS = 20      # a transit fit needs at least this many points


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _ascii(text: str) -> str:
    return (text.replace("±", "+/-").replace("σ", "sigma").replace("−", "-").replace("×", "x").replace("–", "-"))


class OutlierMixin:
    # ---- the two buttons ---------------------------------------------------------------------------------------
    def _build_outlier_buttons(self, g, groups) -> None:
        """Both in the Points group, shown only while they have something to do; greyed during a fit or check."""
        import transit_ui as tui
        self._left_out = []        # [{"t": BJD_TDB, "label": words, "pct": % from the fit, "nsigma": robust sigmas}]
        self._left_out_gen = None  # the series the left-out points belong to
        self.leave_out_button = groups.add("points", ttk.Button(g, text=LEAVE_ONE, command=self.leave_out_and_refit),
                                           shown=False)
        self.put_back_button = groups.add("points", ttk.Button(g, text=PUT_ONE, command=self.put_back_and_refit),
                                          shown=False)
        tui._gate(self.app, self.leave_out_button, self._job_why, tip=LEAVE_TIP, busy=False)
        tui._gate(self.app, self.put_back_button, self._job_why, tip=PUT_TIP, busy=False)

    def _show_outlier_buttons(self) -> None:
        """Leave it out: only while the fit on the page names an outlier. Put it back: while a point is left out."""
        try:
            found = (self.result or {}).get("outliers") or {}
            n = len(found.get("t", []))
            self.leave_out_button.configure(text=_plural(n, LEAVE_ONE, LEAVE_MANY))
            self.groups.show("points", self.leave_out_button, n > 0)
            m = len(getattr(self, "_left_out", None) or [])
            self.put_back_button.configure(text=_plural(m, PUT_ONE, PUT_MANY))
            self.groups.show("points", self.put_back_button, m > 0)
        except Exception:
            pass

    # ---- bookkeeping -------------------------------------------------------------------------------------------
    def _reset_left_out(self, why: str = "") -> None:
        """A left-out point belongs to its source: another night or report puts it back (said in the log)."""
        had = len(getattr(self, "_left_out", None) or [])
        self._left_out = []
        if had and why:
            try:
                self.app.log(f"Transit fit: {why}, so the {had} point{'s' if had != 1 else ''} left out as "
                             f"{_plural(had, 'a single-point outlier is', 'single-point outliers are')} back in.")
            except Exception:
                pass
        self._show_outlier_buttons()

    def _left_out_source_check(self) -> None:
        if self.report is not None:
            return
        gen = getattr(self.app, "_series_gen", 0)
        if getattr(self, "_left_out", None) and self._left_out_gen != gen:
            self._reset_left_out("the series changed")
        self._left_out_gen = gen

    def _left_out_mask(self, t) -> np.ndarray:
        """True for the points that are left out."""
        t = np.asarray(t, dtype=float)
        cut = np.zeros(t.shape, dtype=bool)
        for rec in getattr(self, "_left_out", None) or []:
            cut |= np.abs(t - float(rec["t"])) < MATCH_DAYS
        return cut

    def _frame_labels(self, t, meta: dict) -> list:
        """What each point is called: "frame 27 (wasp39_0027.fits)" for a night's own frames (the frame numbers of
        the settings file), "point 26 of 51" for a report's rows (in time order)."""
        t = np.asarray(t, dtype=float)
        if self.report is not None:
            try:
                full = np.sort(np.asarray(self.report["t"], dtype=float))
                full = full[np.isfinite(full)]
                return [f"point {int(np.argmin(np.abs(full - tv))) + 1} of {full.size}" for tv in t]
            except Exception:
                return [f"point {k + 1} of {len(t)}" for k in range(len(t))]
        nums = {}
        ft = meta.get("frame_t")
        if ft is not None:
            ft_t, ft_n = np.asarray(ft[0], dtype=float), list(ft[1])
            for k, tv in enumerate(t):
                j = int(np.argmin(np.abs(ft_t - tv))) if ft_t.size else -1
                if j >= 0 and abs(ft_t[j] - tv) < MATCH_DAYS:
                    nums[k] = int(ft_n[j])
        obs = (self._data_extra or {}).get("obs")
        names = [os.path.basename(str(getattr(o, "path", "") or "")) for o in obs] \
            if obs is not None and len(obs) == len(t) else [""] * len(t)
        return [f"frame {nums.get(k, k + 1)}" + (f" ({names[k]})" if names[k] else "") for k in range(len(t))]

    def _point_words(self, label: str, bjd: float) -> str:
        """"frame 27 (name.fits), 21:48 local time (UTC−4), BJD_TDB 2459669.95019"."""
        try:
            clock, utc = self._bjd_to_clock(bjd), self._bjd_offset(bjd)
            exact = bool(self._time_map[2]) if self._time_map else False
            when = f"{'' if exact else 'about '}{clock} local time{' (' + utc + ')' if utc else ''}, "
        except Exception:
            when = ""
        return f"{label}, {when}BJD_TDB {bjd:.5f}"

    def _apply_left_out(self, t, flux, err, am, meta: dict):
        """Called by fit() right after the trim: names every point (meta["frame_names"]) and leaves the left-out
        points out of the arrays, and of the flip flags, comp matrix and frames that go with them (as the trim
        does). meta["left_out"] says what was left out. Returns (t, flux, err, am)."""
        self._left_out_source_check()
        t = np.asarray(t, dtype=float)
        labels = self._frame_labels(t, meta)
        meta["frame_names"] = {"t": t.copy(), "label": labels}
        meta.pop("left_out", None)
        # 2.4.0 fit-in F3 (review nit): a left-out point that is no longer among the fit's points (it now lies in a
        # trimmed part, or another setting dropped it) is not "left out" any more. Put it back would refit and say "back in" for
        # a point still trimmed away, so the record goes and the Log says why.
        gone = [rec for rec in getattr(self, "_left_out", None) or []
                if not np.any(np.abs(t - float(rec["t"])) < MATCH_DAYS)]
        if gone:
            self._left_out = [rec for rec in self._left_out if not any(rec is g for g in gone)]
            try:
                names = "; ".join(str(g.get("label") or f"BJD_TDB {float(g['t']):.5f}") for g in gone)
                self.app.log(f"Transit fit: {names} {_plural(len(gone), 'is', 'are')} no longer among the points "
                             "being fit (trimmed away, or left out by another setting), so "
                             f"{_plural(len(gone), 'it is', 'they are')} no longer counted as left out as "
                             f"{_plural(len(gone), 'an outlier', 'outliers')}.")
            except Exception:
                pass
            self._show_outlier_buttons()
        cut = self._left_out_mask(t)
        if not cut.any():
            return t, flux, err, am
        keep = ~cut
        n_out = int(cut.sum())
        if int(keep.sum()) < MIN_POINTS:
            raise ValueError(f"Leaving {_plural(n_out, 'the outlier', 'the outliers')} out leaves {int(keep.sum())} "
                             f"points; a transit fit needs at least {MIN_POINTS}. Press "
                             f"{_plural(n_out, PUT_ONE, PUT_MANY)} or trim less.")
        flux, err, am = np.asarray(flux, dtype=float), np.asarray(err, dtype=float), np.asarray(am, dtype=float)
        ex = self._data_extra if self._data_extra is not None else {}
        for key in ("after_flip", "comp_mags", "jd", "obs"):
            v = ex.get(key)
            if v is not None and len(v) == len(t):
                ex[key] = np.asarray(v)[keep]
        af = ex.get("after_flip")
        if af is not None and not (np.any(af) and not np.all(af)):
            ex["after_flip"] = None
        recs = []
        for k in np.flatnonzero(cut):
            rec = next(r_ for r_ in self._left_out if abs(float(r_["t"]) - t[k]) < MATCH_DAYS)
            recs.append(dict(rec, t=float(t[k]), label=labels[k], words=self._point_words(labels[k], float(t[k]))))
        meta["left_out"] = self._left_out_info(recs, t[cut], flux[cut], err[cut])
        return t[keep], flux[keep], err[keep], am[keep]

    @staticmethod
    def _how_far(rec: dict, ascii_only: bool = False) -> str:
        try:
            pct, ns = float(rec["pct"]), abs(float(rec["nsigma"]))
        except (KeyError, TypeError, ValueError):
            return ""
        if ascii_only:
            return f"{pct:+.1f} %, {ns:.1f} sigma"
        return f"{abs(pct):.1f} % {'above' if pct > 0 else 'below'} the fit it was taken out of, {ns:.1f}σ"

    def _left_out_info(self, recs, t, flux, err) -> dict:
        n = len(recs)
        each = "; ".join(r_["words"] + (f" ({self._how_far(r_)})" if self._how_far(r_) else "") for r_ in recs)
        text = (f"Left out of the fit as {_plural(n, 'a single-point outlier', 'single-point outliers')}: {each}. "
                f"{_plural(n, 'This point is', f'These {n} points are')} left out of the fit, the verdict, the rms, "
                f"the comp check and the saved files; {_plural(n, PUT_ONE, PUT_MANY)} refits with "
                f"{_plural(n, 'it', 'them')}.")
        short = "; ".join(f"{r_['label']} BJD_TDB {r_['t']:.5f}"
                          + (f" ({self._how_far(r_, True)})" if self._how_far(r_, True) else "") for r_ in recs)
        return {"n": n, "t": np.asarray(t, dtype=float), "flux": np.asarray(flux, dtype=float),
                "err": np.asarray(err, dtype=float), "labels": [r_["label"] for r_ in recs], "text": text,
                "frames": _ascii(short),
                "report_note": _ascii("SHOBS-P: left out of the fit as single-point outlier"
                                      f"{'s' if n != 1 else ''} (the user's choice): {short}")}

    def left_out_settings_line(self, meta: dict, r: dict) -> str:
        """The settings file's line (always there, so 'none' is on record too)."""
        info = (r or {}).get("left_out") or (meta or {}).get("left_out")
        return ("Single points left out of the fit as outliers (the user's choice): "
                + (info["frames"] if info else "none"))

    # ---- after a fit: look, note, offer ------------------------------------------------------------------------
    def _find_outliers(self, r: dict, meta: dict) -> dict | None:
        if r.get("residuals") is None or r.get("baseline") is None or r.get("t") is None:
            return None
        t = np.asarray(r["t"], dtype=float)
        rel = np.asarray(r["residuals"], dtype=float) / np.asarray(r["baseline"], dtype=float)
        skip = r.get("flagged")
        skip = np.asarray(skip, dtype=bool) if skip is not None and len(skip) == len(t) else None
        got = tc.find_point_outliers(t, rel, skip=skip)
        idx = np.asarray(got["index"], dtype=int)
        if not idx.size or len(t) - idx.size < MIN_POINTS:
            return None   # leaving them out would leave too few points to fit: no offer
        names = meta.get("frame_names") or {}
        nt, nl = np.asarray(names.get("t", []), dtype=float), list(names.get("label", []))
        labels = []
        for k in idx:
            j = int(np.argmin(np.abs(nt - t[k]))) if nt.size else -1
            labels.append(nl[j] if j >= 0 and abs(nt[j] - t[k]) < MATCH_DAYS else f"point {int(k) + 1} of {len(t)}")
        pct = rel[idx] * 100
        n = int(idx.size)
        rest = np.ones(len(t), dtype=bool)
        rest[idx] = False
        rms_all, rms_rest = float(np.std(rel) * 100), float(np.std(rel[rest]) * 100)
        each = "; ".join(f"{self._point_words(labels[j], float(t[k]))} is {abs(pct[j]):.1f} % "
                         f"{'above' if pct[j] > 0 else 'below'} the fit, {abs(got['nsigma'][j]):.1f}σ"
                         for j, k in enumerate(idx))
        it, It = _plural(n, "it", "them"), _plural(n, "It is", "They are")
        text = (f"{_plural(n, 'One point stands', f'{n} points stand')} far from the fit: {each} (σ is the night's "
                f"typical scatter about the fit, {got['sigma'] * 100:.2f} %, from the median so that one bad point "
                f"cannot widen it; the limit is {got['limit']:g}σ). A cosmic ray, a satellite or a plane on the target "
                f"or a comp can do this. {It} still in this fit. The plot rings {it} in red and draws the bins without "
                f"{it}. Without {it} the scatter about this fit would be {rms_rest:.2f} % instead of {rms_all:.2f} %. "
                f"{_plural(n, LEAVE_ONE, LEAVE_MANY)} takes {it} out; nothing is left out unless you press it.")
        return {"index": idx, "t": t[idx], "pct": pct, "nsigma": np.asarray(got["nsigma"], dtype=float),
                "sigma_pct": got["sigma"] * 100, "limit": got["limit"], "labels": labels, "rms_pct": rms_all,
                "rms_without_pct": rms_rest, "text": text}

    def _outliers_after_fit(self, result: dict, meta: dict) -> None:
        """Called by _finish_fit: the left-out points' note first, then the look for new single-point outliers.
        Both are plain notes. Neither can cost the fit."""
        result["outliers"] = None
        flags = result.setdefault("flags", [])
        try:
            if meta.get("left_out"):
                result["left_out"] = meta["left_out"]
                flags.append(("info", meta["left_out"]["text"]))
                self.app.log("Transit fit: " + meta["left_out"]["text"])
            found = self._find_outliers(result, meta)
            if found:
                result["outliers"] = found
                flags.append(("info", found["text"]))
                self.app.log("Transit fit: " + found["text"])
                self.app.status.set(f"{_plural(len(found['t']), 'One point stands', 'Some points stand')} far from "
                                    f"the fit (see the notes): {_plural(len(found['t']), LEAVE_ONE, LEAVE_MANY)} "
                                    "takes it out.")
        except Exception as exc:
            result["outliers"] = None
            try:
                self.app.log(f"Transit fit: the single-point outlier check was skipped ({type(exc).__name__}: {exc}).")
            except Exception:
                pass

    def _busy_words(self, what: str) -> bool:
        if self._fitting or getattr(self, "_checking", False) or self.app.busy:
            self.app.status.set(f"Wait for the current fit or check to end, then {what}.")
            return True
        return False

    def leave_out_and_refit(self):
        found = (self.result or {}).get("outliers")
        if not found or not len(found.get("t", [])):
            self.app.status.set("No point stands far enough from the fit to leave out.")
            return
        n = len(found["t"])
        if self._busy_words(_plural(n, LEAVE_ONE, LEAVE_MANY)):
            return
        self._left_out_source_check()
        self._left_out_gen = getattr(self.app, "_series_gen", 0)
        have = self._left_out_mask(found["t"])
        for k in range(n):
            if not have[k]:
                self._left_out.append({"t": float(found["t"][k]), "label": found["labels"][k],
                                       "pct": float(found["pct"][k]), "nsigma": float(found["nsigma"][k])})
        self.app.log(f"Transit fit: leaving {n} single-point outlier{'s' if n != 1 else ''} out "
                     f"({'; '.join(found['labels'])}) and refitting.")
        self._show_outlier_buttons()
        self.fit(with_flagged=bool((self.result or {}).get("with_flagged")))

    def put_back_and_refit(self):
        n = len(getattr(self, "_left_out", None) or [])
        if not n:
            self.app.status.set("No point is left out as an outlier.")
            self._show_outlier_buttons()
            return
        if self._busy_words(_plural(n, PUT_ONE, PUT_MANY)):
            return
        with_flagged = bool((self.result or {}).get("with_flagged"))
        self._left_out = []
        self.app.log(f"Transit fit: the {n} point{'s' if n != 1 else ''} left out as "
                     f"{_plural(n, 'a single-point outlier is', 'single-point outliers are')} back in; refitting.")
        self._show_outlier_buttons()
        self.fit(with_flagged=with_flagged)

    # ---- the plot ----------------------------------------------------------------------------------------------
    def _outlier_bin_values(self, r: dict, y) -> np.ndarray:
        """The values the display bins are made from: the named single-point outliers are blanked, so a bin never
        shows one as if it were real data (the point itself stays on the plot, ringed)."""
        y = np.asarray(y, dtype=float)
        found = (r or {}).get("outliers")
        if not found:
            return y
        idx = np.asarray(found["index"], dtype=int)
        if not idx.size or idx.max() >= y.size:
            return y
        y = y.copy()
        y[idx] = np.nan
        return y

    def _draw_outliers(self, r: dict, best: dict, x, y, lo: float, hi: float) -> None:
        """Ring the named outliers (both panels) and show the left-out points as red crosses. A point above or
        below the panel is drawn as an arrow at its edge. Called by draw() once the axes' ranges are set."""
        ax, axr = self.ax, self.ax_r
        x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        drew = False

        def edge(xv, yv, ring: bool, label: str, words: str):
            inside = lo <= yv <= hi
            if inside:
                ax.plot([xv], [yv], "o" if ring else "x", ms=9 if ring else 6, mfc="none", mec=RED, mew=1.3,
                        color=RED, ls="none", zorder=6, label=label)
            else:
                up = yv > hi
                ax.plot([xv], [hi - 0.03 * (hi - lo) if up else lo + 0.03 * (hi - lo)], "^" if up else "v", ms=7,
                        mfc=RED if not ring else "none", mec=RED, mew=1.3, ls="none", zorder=6, label=label)
                ax.annotate(words, (xv, hi - 0.03 * (hi - lo) if up else lo + 0.03 * (hi - lo)),
                            textcoords="offset points", xytext=(7, -3 if up else 0), fontsize=7, color=RED)
        found = r.get("outliers")
        if found and len(found.get("index", [])):
            idx = np.asarray(found["index"], dtype=int)
            n = int(idx.size)
            label = (f"single-point outlier{'s' if n != 1 else ''}: in the fit, not in the bins "
                     f"({_plural(n, LEAVE_ONE, LEAVE_MANY)})")
            for j, k in enumerate(idx):
                edge(float(x[k]), float(y[k]), True, label if j == 0 else None,
                     f"{float(found['pct'][j]):+.1f} % from the fit")
            rr = np.asarray(r["residuals"], dtype=float) / np.asarray(r["baseline"], dtype=float) * 100
            axr.plot(x[idx], rr[idx], "o", ms=8, mfc="none", mec=RED, mew=1.2, ls="none", zorder=6)
            drew = True
        left = r.get("left_out")
        if left and len(left.get("t", [])):
            try:
                tt = np.asarray(left["t"], dtype=float)
                ft = np.asarray(left["flux"], dtype=float) / float(r.get("flux_median") or 1.0)
                order = np.argsort(r["t"])
                base = np.interp(tt, np.asarray(r["t"])[order], np.asarray(r["baseline"])[order])
                xs = (tt - best["tc"]) * 24
                n = int(tt.size)
                for j in range(n):
                    edge(float(xs[j]), float(ft[j] / base[j]), False,
                         f"left out as {_plural(n, 'a single-point outlier', 'single-point outliers')} (not fitted)"
                         if j == 0 else None, "above the plot" if ft[j] / base[j] > hi else "below the plot")
                x0, x1 = ax.get_xlim()
                if xs.min() < x0 or xs.max() > x1:
                    ax.set_xlim(min(x0, float(xs.min()) - 0.25), max(x1, float(xs.max()) + 0.25))
                drew = True
            except Exception:
                pass
        if drew:
            self._legend_items = ax.get_legend_handles_labels()
