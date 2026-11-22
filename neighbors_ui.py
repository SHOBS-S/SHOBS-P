"""The App's side of the two checks on the Field scan page: the Check neighbors window (the selected star beside its
close companions, look-alikes and the comps, on that night) and Outside data (ZTF, TESS and ATLAS at its position).

The numbers live in neighbors.py and outside_data.py (headless, tested); this module runs the outside-data lookups in
a worker thread with the busy bar, and shows both.

2.4.0 step 7d (John, 22 Nov 2026): the promotion path was removed. Both checks are plain tools now: they report, and
the observer decides.
"""
from __future__ import annotations

import math
import os
import threading
from tkinter import BOTH, LEFT, RIGHT, W, X, StringVar, Toplevel, messagebox, ttk

import numpy as np

import neighbors as nb
import outside_data as od
import photometry_core as core

try:
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
except Exception:   # pragma: no cover
    Figure = None

ROLE_WORDS = {"comp": "comp", "check": "check"}
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


def outside_text(name: str, res: dict) -> str:
    """The outside-data result in words, for the message box: the summary, then one line per survey."""
    lines = [f"Outside data for {name}", "", res.get("summary") or "", ""]
    if res.get("period"):
        lines[1:1] = ["", f"Our period: {float(res['period']):.6f} d"]
    for s, r in (res.get("surveys") or {}).items():
        lines.append(f"{s}: {r.get('detail') or r.get('status')}")
        if r.get("note"):
            lines.append(f"    {r['note']}")
    lines += ["", "A survey agrees when it finds the same period within 1%. This is a report: it changes nothing in "
                  "SHOBS-P."]
    return "\n".join(lines)


class NeighborsMixin:
    """Methods of the App (clearv_app.App) for the Field scan page's Checks group."""

    # ---- Check neighbors ---------------------------------------------------------------------------------------
    def check_neighbors_suspect(self, result, i: int, parent=None):
        """Open the Check neighbors window for a star of one night's field scan."""
        return NeighborsWindow(self, name=str(result["labels"][i]), scan=result, scan_index=i)

    # ---- Outside data ------------------------------------------------------------------------------------------
    def outside_check(self, name: str, ra: float, dec: float, period: float, *, period_range=None, parent=None,
                      on_done=None) -> bool:
        """ZTF, TESS and ATLAS at the star's position, in the background. The result is written to the Log and shown
        in a message box: what each survey found, and whether it agrees with our period. Nothing is stored.
        period_range: (min, max) days the surveys' light curves are searched over (the field scan's own range)."""
        running = getattr(self, "_outside_running", None)
        if not isinstance(running, set):
            running = self._outside_running = set()
        if name in running:
            return False
        running.add(name)
        tok = self.busy_begin(f"Outside data: ZTF, TESS and ATLAS for {name}…")
        try:
            pmin, pmax = (float(v) for v in period_range)
            if not (0 < pmin < pmax):
                raise ValueError
        except (TypeError, ValueError):
            pmin, pmax = 0.02, 20.0

        def work():
            res, error = None, ""
            try:
                res = od.check(float(ra), float(dec), float(period), min_period=pmin, max_period=pmax)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            self.call_ui(self._outside_done, tok, name, res, error, parent, on_done)

        threading.Thread(target=work, daemon=True).start()
        return True

    def _outside_done(self, tok, name, res, error, parent=None, on_done=None):
        ca = _ca()
        self._outside_running.discard(name)
        self.busy_end(tok)
        try:
            if parent is None or not parent.winfo_exists():
                parent = self
        except Exception:
            parent = self
        if error or res is None:
            self.log(f"✖ Outside data for {name}: {error}")
            messagebox.showerror(ca.APP_TITLE, f"The outside-data check for {name} failed:\n\n{error}", parent=parent)
            return
        for s_, r in res["surveys"].items():
            mark = "✖ " if r.get("status") == "error" else ""
            self.log(f"  {mark}{s_}: {r.get('detail') or r.get('status')}" + (f" {r['note']}" if r.get("note") else ""))
        self.log(f"Outside data for {name}: {res['summary']}")
        self.status.set(f"Outside data for {name}: " + res["summary"])
        if on_done is not None:
            try:
                on_done(res)
            except Exception:
                pass
        messagebox.showinfo(ca.APP_TITLE, outside_text(name, res), parent=parent)


# ---- the window --------------------------------------------------------------------------------------------------
class NeighborsWindow:
    """Check neighbors: a field-scan star's light curve with its neighbors' on one shared scale, and each neighbor's
    power and amplitude at the star's period. Mockup: "SHOBS-P Neighbor check window" (15 Nov 2026)."""

    TIME_BIN_MIN = 6.0
    PHASE_BINS = 40

    def __init__(self, app, name: str, scan, scan_index: int):
        ca = _ca()
        self.app, self.name = app, name
        self.scan, self.scan_index = scan, scan_index
        self.mode = "scan"
        self.result = None
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
        self.bottom_line = StringVar(value="")
        ttk.Label(bottom, textvariable=self.bottom_line, style="Card.TLabel", font=(core.UI_FONT, 10, "bold")).pack(
            side=LEFT)
        ttk.Button(bottom, text="Close", command=self.close).pack(side=RIGHT)
        self.save_btn = ttk.Button(bottom, text="Save to suspects file", style="Accent.TButton", command=self.save)
        self.save_btn.pack(side=RIGHT, padx=6)
        ctl = ttk.Frame(top, style="Card.TFrame")
        ctl.pack(fill=X, pady=(0, 6))
        ttk.Label(ctl, text="View", style="Card.TLabel").pack(side=LEFT)
        self.view = StringVar(value="fold")
        ttk.Radiobutton(ctl, text="Folded at P", variable=self.view, value="fold", command=self.draw).pack(
            side=LEFT, padx=(6, 0))
        ttk.Radiobutton(ctl, text="Time", variable=self.view, value="time", command=self.draw).pack(
            side=LEFT, padx=(6, 12))
        ttk.Label(ctl, text="Look-alikes ±", style="Card.TLabel").pack(side=LEFT)
        self.window = StringVar(value=f"{nb.LOOKALIKE_MAG:.1f}")
        ent = ttk.Entry(ctl, textvariable=self.window, width=5)
        ent.pack(side=LEFT)
        ent.bind("<Return>", lambda _e: self.recompute())
        ttk.Label(ctl, text="mag", style="Card.TLabel").pack(side=LEFT, padx=(2, 12))
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
        self.recompute()

    # -- data --------------------------------------------------------------------------------------------------
    def _window_mag(self) -> float:
        v = _f(self.window.get(), nb.LOOKALIKE_MAG)
        return max(0.05, min(3.0, v))

    def _scan_curves(self):
        r, i = self.scan, self.scan_index
        jd = np.asarray(r["jd"], float)
        lc = np.asarray(r["light_curves"], float)
        night = "scan"

        def curve(j, role):
            return nb.make_curve(str(r["labels"][j]), jd, lc[:, j], [night] * jd.size, role=role)
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
        roles = {"Check", "Comp"} | {f"Comp {k}" for k in range(1, 11)}   # a Variables-mode scan's marked stars
        names = r.get("names") or []
        radec = r.get("radec")

        def info(j):
            """The catalog name, ΔV and the separation in arcsec when the scan has sky positions; pixels only
            without them."""
            out = {"name": str(r["labels"][j]), "mag": mags[j], "sep_px": float(d[j])}
            if j < len(names) and names[j]:
                out["display"] = f"{names[j]} ({r['labels'][j]})"
            if math.isfinite(mags[j]) and math.isfinite(mags[i]):
                out["dmag"] = float(mags[j] - mags[i])
            if radec:
                try:
                    sep = nb.sep_arcsec(radec[i][0], radec[i][1], radec[j][0], radec[j][1])
                    if math.isfinite(sep):
                        out["sep"] = float(sep)
                except (TypeError, ValueError, IndexError):
                    pass
            return out
        comps = [k for k, lab in enumerate(r["labels"]) if str(lab) in roles and k != i and r["usable"][k]]
        for j in close:
            rows.append(("close", curve(j, "neighbor"), info(j)))
        for j in look:
            rows.append(("look", curve(j, "neighbor"), info(j)))
        for j in comps:
            if j not in close and j not in look:
                rows.append(("comps", curve(j, "comp"), info(j)))
        return target, rows, close, look

    def recompute(self):
        try:
            self._recompute_scan()
        except Exception as exc:
            self.note.set(f"✖ {type(exc).__name__}: {exc}")
            return
        self.draw()

    def _recompute_scan(self):
        target, rows, close, look = self._scan_curves()
        # the star's own period from the scan, when it found one
        P = core.scan_period_value((self.scan.get("periods") or {}).get(self.scan_index))
        res = nb.neighbor_check(target, [c for _g, c, _i in rows], period=P, min_period=0.02,
                                       max_period=max(0.05, float(np.ptp(target.jd))), min_points=8)
        for (g, c, info), row in zip(rows, res["rows"]):
            row.update({"group": g, "mag": info.get("mag"), "sep": info.get("sep"), "sep_px": info.get("sep_px"),
                        "dmag": info.get("dmag"), "display": info.get("display")})
        self.result = res
        self.curves = {"target": target, "rows": [(g, c) for g, c, _i in rows]}
        r, i = self.scan, self.scan_index
        kind = "Suspect" if i in set(r.get("candidates") or []) else "Field-scan star"
        self.sub.set(f"{kind} · {r['labels'][i]} · mag {float(r['mag'][i]):.2f} · one night, {target.jd.size} frames · "
                     + (f"P {res['period']:.5f} d" if res.get("period") else "no period"))
        self.bottom_line.set(("✓ " if res["n_measured"] and res["n_quiet"] == res["n_measured"] else
                              ("⚠ " if res["n_measured"] else "")) + res["bottom"])
        self.note.set(("Close companions: " + str(len(close)) + " inside the sky ring. " if close else
                       "No star inside the aperture plus sky ring. ")
                      + "Power at P is each star's periodogram power at the star's period as a share of the star's "
                        "own; a star is quiet when that is under 50% and its amplitude under a third of the star's (or "
                        "2σ). The star is judged on this one night: Watch it to follow it on other nights.")

    # -- drawing -----------------------------------------------------------------------------------------------
    def _binned(self, c, P, view):
        if c is None or c.jd.size == 0:
            return np.array([]), np.array([])
        y = nb.remove_medians(c.mag, c.night) * 1000.0
        if view == "fold" and P:
            ph = ((c.jd - c.jd.min()) / P) % 1.0
            edges = np.linspace(0, 1, self.PHASE_BINS + 1)
            k = np.digitize(ph, edges) - 1
            xs = 0.5 * (edges[1:] + edges[:-1])
            ys = np.array([np.median(y[k == j]) if np.any(k == j) else np.nan for j in range(self.PHASE_BINS)])
            return np.r_[xs, xs + 1], np.r_[ys, ys]
        t = (c.jd - c.jd.min()) * 24.0
        yy = y
        w = self.TIME_BIN_MIN / 60.0
        k = np.floor(t / w).astype(int)
        xs, ys = [], []
        for j in np.unique(k):
            xs.append(np.mean(t[k == j]))
            ys.append(np.median(yy[k == j]))
        return np.asarray(xs), np.asarray(ys)

    def _lines(self):
        """The rows to draw, in order, with a group heading before each group: ("head", text) or
        ("row", group, curve, row dict or None)."""
        res = self.result
        rows = [("target", self.curves["target"], None)]
        rows += [(g, c, r) for (g, c), r in zip(self.curves["rows"], res["rows"])]
        order = {"target": 0, "close": 1, "look": 2, "comps": 3}
        rows.sort(key=lambda x: (order.get(x[0], 9), (x[2] or {}).get("sep") or (x[2] or {}).get("sep_px") or 0))
        out, prev = [], None
        for g, c, r in rows:
            if g != prev:
                head = GROUP_WORDS.get(g, g)
                if g == "look":
                    head = f"Look-alikes (±{self._window_mag():.1f} mag)"
                out.append(("head", head))
                prev = g
            out.append(("row", g, c, r))
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
        units = sum(0.6 if ln[0] == "head" else 1.0 for ln in lines) + 0.6
        h_px = max(1.0, float(fig.get_size_inches()[1] * fig.dpi))
        top = 1.0 - max(0.6 / units, 30.0 / h_px)   # room for the two-line header
        y = top
        x0, x1 = 0.25, 0.70
        # the field thumbnail: where each star sits on the first frame
        try:
            self._thumb([ln for ln in lines if ln[0] == "row"])
        except Exception:
            pass
        for ln in lines:
            if ln[0] == "head":
                y -= 0.6 / units   # a little air between the row above and the title
                fig.text(0.005, y + 0.12 / units, ln[1].upper(), fontsize=7.5, fontweight="bold",
                         color="#14294a", va="bottom")
                fig.lines.append(__import__("matplotlib").lines.Line2D([0.005, 0.99], [y + 0.1 / units] * 2,
                                                                       transform=fig.transFigure, color="#e3e8f0",
                                                                       lw=0.8))
                continue
            _k, g, c, r = ln
            h = 1.0 / units
            y -= h
            yc = y + 0.5 * h
            name = c.name
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
            bits = []
            src = r
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
            shown = ((r or {}).get("display") or name) if g != "target" else name
            fig.text(0.005, yc + 0.18 * h, shown[:34], fontsize=8.5, fontweight="bold", va="center")
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
        # two lines, so the text stays left of the Power at P / Amp at P titles
        fig.text(x0, 0.995, (f"One shared scale: ±{lim:.0f} mmag, fainter is down.\n"
                             + (f"Folded at {P:.5f} d, {self.PHASE_BINS} phase bins." if view == "fold" else
                                f"Time: this night, {self.TIME_BIN_MIN:.0f}-min bins.")),
                 fontsize=8, color="#4a5568", va="top", linespacing=1.3)
        self.canvas.draw_idle()

    def _thumb(self, rows):
        """A small map of the field: the star, its neighbors and the comps, where they sit on one frame."""
        xy = np.asarray(self.scan["xy"], float)
        labels = [str(x) for x in self.scan["labels"]]
        pos = {lab: tuple(xy[k]) for k, lab in enumerate(labels)}
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
        for _k, g, c, r in rows:
            v = pos.get(c.name)
            if not v or not all(map(math.isfinite, v)):
                continue
            col, mk = style.get(g, ("#1f6fd1", "o"))
            ax.plot([v[0]], [v[1]], mk, mfc="none", mec=col if g != "target" else "#1f6fd1", ms=9 if g == "target"
                    else 7, mew=2 if g == "target" else 1.3, ls="none")
        # frame the field itself (square, the star included) instead of a square centered on the star, which left
        # half the box empty when the star sat near the frame's edge
        allp = np.vstack([pts, np.asarray([me], float)]) if pts.size else np.asarray([me], float)
        lo, hi = np.nanmin(allp, axis=0), np.nanmax(allp, axis=0)
        mid = (lo + hi) / 2.0
        half = max(float(np.max(hi - lo)) / 2.0 * 1.06, 50.0)
        ax.set_xlim(mid[0] - half, mid[0] + half)
        ax.set_ylim(mid[1] + half, mid[1] - half)
        ax.set_title("Field around the star", fontsize=8, color="#14294a")
        fig.text(0.02, 0.02, "blue: the star · green: look-alikes\norange: close · grey: comps, check",
                 fontsize=6.5, color="#4a5568")
        self.thumb_canvas.draw_idle()

    # -- saving ------------------------------------------------------------------------------------------------
    def summary(self) -> dict:
        res = self.result or {}
        return {"utc": nb.utc_now(), "mode": self.mode, "period": res.get("period"), "bottom": res.get("bottom"),
                "n_quiet": res.get("n_quiet"), "n_measured": res.get("n_measured"), "window_mag": self._window_mag(),
                "rows": [{k: r.get(k) for k in ("name", "role", "group", "power_share", "amp", "quiet", "sep",
                                                 "dmag")} for r in res.get("rows") or []]}

    def save(self):
        """Keep the check with the star in the night's suspects file (only Suspects are kept there)."""
        app = self.app
        s = self.summary()
        r = self.scan
        if self.scan_index not in set(r.get("candidates") or []):
            self.note.set("Not saved: this star is not a Suspect. Add as suspect on the Field scan page first, "
                          "then save again.")
            return
        r.setdefault("neighbor_checks", {})[self.scan_index] = s
        path = app._save_suspects(r) if hasattr(app, "_save_suspects") else ""
        where = os.path.basename(path) if path else ("this night's results only (no suspects file was written: see "
                                                     "the log)")
        app.log(f"Check neighbors for {self.name}: {s['bottom']}. Saved to {where}.")
        app.status.set(f"Check neighbors for {self.name} saved: {s['bottom']}.")
        self.note.set(f"Saved to {where}.")

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass
