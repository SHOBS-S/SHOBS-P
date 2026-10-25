"""Multi-night transit analysis (2.2.19, GitHub #18).

Several nights of one planet fitted together: one transit shape (Rp/R*, a/R*, b and limb darkening; optionally
Rp/R* and limb darkening per filter) with each night's own mid-transit time and baseline (the same detrending
choices as the single-night fit, and the flip step where a night has one). Uncertainties from the same MCMC sampler
as the single-night fit (transit_core.run_mcmc). Then the timing: O − C of every epoch against the input ephemeris,
an optional typed one (e.g. Exoplanet Watch's) and the nights' own weighted least-squares linear ephemeris.

Sources: SHOBS-P/EXOTIC AAVSO exoplanet reports (#TYPE=EXOPLANET), SHOBS-P transit-fit CSVs (<stem>_transit_fit.csv),
and the fit shown on the Transit fit page. The fitting and timing functions use NumPy/SciPy only; the window
(MultiNightWindow, at the end) is opened from the Transit fit page's Multi-night… button.
"""
from __future__ import annotations

import csv
import math
import os
import re
import threading
import traceback

import numpy as np

import photometry_core as core
import transit_core as tc

# ---- Fast model for the joint fit -------------------------------------------------------------------------------
# The occultation depends on z alone once Rp/R* and the limb darkening are set, and those are shared by all nights.
# So each model evaluation computes the exact transit_core.occultation on a grid of 126 separations (denser near the
# limb and the contact points) and interpolates every night's points on it. Checked against the direct model: max
# error 2e-6 in flux for Rp/R* 0.12 (5e-4 of the depth at worst over Rp/R* 0.02-0.5), far below any night's noise;
# 3-6x faster than the direct model for 2-6 nights.
_S_IN = np.sin(np.linspace(0.0, math.pi / 2, 48))[:-1]
_S_EDGE = 0.5 * (1.0 - np.cos(np.linspace(0.0, math.pi, 80)))
MIN_POINTS = 20
OUTLIER_SIGMA = 3.0
DEFAULT_STEPS = 1500


def occ_curve(k: float, u1: float, u2: float):
    lo = max(1.0 - k, 0.0)
    g = np.concatenate([lo * _S_IN, lo + (1.0 + k - lo) * _S_EDGE])
    return g, tc.occultation(g, k, u1, u2)


def fast_occultation(z, k: float, u1: float, u2: float) -> np.ndarray:
    g, f = occ_curve(k, u1, u2)
    return np.interp(np.asarray(z, dtype=float), g, f, right=1.0)


def norm_name(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


# ---- Loading nights ---------------------------------------------------------------------------------------------
class NightError(ValueError):
    pass


FIT_CSV_NAME = re.compile(r"^(?P<planet>.+?)_(?P<filter>[^_]+)_(?P<date>\d{1,2}-[A-Za-z]{3}-\d{4})_SHOBS-P_transit_fit"
                          r"(?:_\d+)?\.csv$", re.I)


def to_bjd_tdb(t, date_type: str, radec=None, lat=None, lon=None):
    """(times in BJD_TDB, note) from a file's time system. Reuses the single-night page's rules (transit_ui
    _report_time_system): HJD_UTC + 69.184 s (TDB − UTC; the helio/bary difference is a few seconds); JD_UTC needs
    the star's RA/Dec (Astropy's conversion with the file's site when given, else the planner's approximation, good
    to a few seconds). Raises NightError when the conversion is impossible, so a night is never fitted in the wrong
    time system."""
    t = np.asarray(t, dtype=float)
    dt = str(date_type or "").strip().upper().replace(" ", "_")
    if not dt or dt.startswith("BJD"):
        return t, ""
    if dt.startswith("HJD"):
        return t + 69.184 / 86400.0, "HJD_UTC moved to BJD_TDB (+69.184 s; good to a few seconds)"
    if dt.startswith("JD"):
        if radec is None:
            raise NightError("times are JD_UTC and the star's RA/Dec is not known, so they cannot be put on BJD_TDB: "
                             "look the planet up on the Input page (or in this window), then add the file again")
        out = None
        if lat is not None and lon is not None:
            try:
                out = np.asarray(core.convert_times(t, "BJD_TDB", radec[0], radec[1], lat, lon), dtype=float)
            except Exception:
                out = None
        if out is None:
            out = t + np.asarray(tc.bjd_minus_jd_days(t, radec[0], radec[1]), dtype=float)
            return out, "JD_UTC converted to BJD_TDB (approximate, good to a few seconds: no site in the file)"
        return out, "JD_UTC converted to BJD_TDB"
    raise NightError(f"times are {dt}, which SHOBS-P does not convert")


def _float(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _finish_night(n: dict) -> dict:
    t = np.asarray(n["t"], dtype=float)
    flux = np.asarray(n["flux"], dtype=float)
    err = np.asarray(n["err"], dtype=float)
    am = n.get("airmass")
    am = np.asarray(am, dtype=float) if am is not None else None
    ok = np.isfinite(t) & np.isfinite(flux) & np.isfinite(err) & (err > 0)
    t, flux, err = t[ok], flux[ok], err[ok]
    am = am[ok] if am is not None and len(am) == len(ok) else None
    if am is not None and not (np.isfinite(am).all() and np.ptp(am) > 1e-4):
        am = None
    order = np.argsort(t)
    n.update(t=t[order], flux=flux[order], err=err[order], airmass=am[order] if am is not None else None)
    if len(t):
        med = float(np.median(n["flux"]))
        if med > 0:
            n["flux"], n["err"] = n["flux"] / med, n["err"] / med
    n.setdefault("notes", [])
    if am is None:
        n["notes"].append(("info", "no airmass column: this night's baseline cannot follow airmass (tick time "
                                   "slope if it drifts)"))
    return n


def load_report(path: str, radec=None) -> dict:
    """A night from an AAVSO exoplanet report (SHOBS-P or EXOTIC)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        rep = tc.parse_exoplanet_report(fh.read())
    h = rep["header"]
    t_old = np.asarray(rep["t"], dtype=float)
    t, note = to_bjd_tdb(t_old, h.get("DATE_TYPE", ""), radec, _float(h.get("OBSLAT")), _float(h.get("OBSLON")))
    shift = float(np.nanmedian(t - t_old)) if len(t) else 0.0
    single = rep["results"].get("Tc")
    flip = tc.report_flip_time(h)
    night = {"kind": "report", "path": path, "source": os.path.basename(path), "planet": h.get("EXOPLANET_NAME", ""),
             "host": h.get("STAR_NAME", ""), "filter": h.get("FILTER", "") or "CV", "obsdate": h.get("OBSDATE", ""),
             "software": h.get("SOFTWARE", ""), "t": t, "flux": rep["flux"], "err": rep["err"],
             "airmass": rep["airmass"], "t_flip": (flip + shift) if flip is not None else None,
             "single_tc": ((single[0] + shift, single[1]) if single and single[0] is not None else None),
             "time_note": note, "report_planet": tc.planet_from_report(rep), "notes": []}
    if "NO TRANSIT MEASURED" in str(h.get("NOTES", "")).upper():
        night.update(user_off=True, off_reason="its single-night fit found no transit")
    return _finish_night(night)


def load_fit_csv(path: str, radec=None) -> dict:
    """A night from a SHOBS-P transit-fit CSV (time, rel_flux, rel_flux_err, model, residual, flag). Trimmed rows
    (no error; never fitted) stay out; frames put back from a flag keep their (doubled) errors."""
    comments, rows, head = [], [], None
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        for row in csv.reader(fh):
            if not row or not any(c.strip() for c in row):
                continue
            if row[0].lstrip().startswith("#"):
                comments.append(",".join(row))
                continue
            if head is None:
                head = [c.strip() for c in row]
                continue
            rows.append(row)
    if head is None or len(head) < 3:
        raise NightError("not a SHOBS-P transit-fit CSV (no time, rel_flux, rel_flux_err columns)")
    low = [c.lower() for c in head]
    if "rel_flux" not in low or "rel_flux_err" not in low:
        raise NightError("not a SHOBS-P transit-fit CSV (no rel_flux / rel_flux_err columns)")
    i_t, i_f, i_e = 0, low.index("rel_flux"), low.index("rel_flux_err")
    i_flag = low.index("flag") if "flag" in low else None
    t, f, e = [], [], []
    for row in rows:
        if i_flag is not None and len(row) > i_flag and "trimmed" in row[i_flag]:
            continue
        vals = [_float(row[i]) if len(row) > i else None for i in (i_t, i_f, i_e)]
        if None in vals:
            continue
        t.append(vals[0]); f.append(vals[1]); e.append(vals[2])
    if not t:
        raise NightError("the CSV has no fitted rows")
    t_old = np.asarray(t)
    tcol = head[0].strip().upper()
    t_new, note = to_bjd_tdb(t_old, tcol if tcol in ("BJD_TDB", "JD_UTC", "HJD_UTC") else "BJD_TDB", radec)
    shift = float(np.median(t_new - t_old))
    planet = filt = date = ""
    single = None
    verdict = ""
    for c in comments:
        m = re.search(r"Transit fit: planet ([^;]*);\s*filter ([^;]*);\s*Tc ([0-9.]+) \+/- ([0-9.]+)", c)
        if m:
            planet, filt = m.group(1).strip(), m.group(2).strip()
            single = (float(m.group(3)) + shift, float(m.group(4)))
            v = re.search(r"verdict (\w+)", c)
            verdict = v.group(1) if v else ""
    m = FIT_CSV_NAME.match(os.path.basename(path))
    if m:
        planet = planet or m.group("planet")
        filt = filt if filt and filt != "unknown" else m.group("filter")
        date = m.group("date").upper()
    night = {"kind": "csv", "path": path, "source": os.path.basename(path), "planet": planet, "host": "",
             "filter": filt or "", "obsdate": date, "software": "SHOBS-P", "t": t_new, "flux": f, "err": e,
             "airmass": None, "t_flip": None, "single_tc": single, "time_note": note, "notes": []}
    if verdict == "none":
        night.update(user_off=True, off_reason="its single-night fit found no transit")
    return _finish_night(night)


def load_night(path: str, radec=None) -> dict:
    """A report (.txt) or a fit CSV (.csv), by content."""
    if path.lower().endswith(".csv"):
        return load_fit_csv(path, radec)
    try:
        return load_report(path, radec)
    except NightError:
        raise
    except ValueError as exc:
        raise NightError(str(exc)) from exc


def night_from_result(result: dict, meta: dict | None = None, label: str = "current fit") -> dict:
    """The night of the fit shown on the Transit fit page (its points, the fit's scaled errors, airmass and flip)."""
    r = result
    meta = meta or {}
    flip = r.get("flip") or {}
    am = np.asarray(r.get("airmass"), dtype=float) if r.get("airmass") is not None else None
    night = {"kind": "current", "path": "", "source": label, "planet": (r.get("planet") or {}).get("name", ""),
             "host": (r.get("planet") or {}).get("host", ""), "filter": meta.get("filter") or "",
             "obsdate": meta.get("obsdate", ""), "software": "SHOBS-P", "t": np.asarray(r["t"], dtype=float),
             "flux": np.asarray(r["flux"], dtype=float), "err": np.asarray(r.get("err_scaled", r["err"]), dtype=float),
             "airmass": am, "t_flip": flip.get("t_flip"),
             "single_tc": tuple(r["summary"]["tc"]) if "tc" in (r.get("summary") or {}) else None, "time_note": "",
             "notes": []}
    return _finish_night(night)


def night_label(n: dict) -> str:
    """'15-OCT-2026' (or the first point's UT date) for tables, plots and files."""
    if n.get("obsdate"):
        return str(n["obsdate"])
    if len(n.get("t", [])):
        return tc.jd_to_local(float(n["t"][0])).strftime("%d-%b-%Y").upper()
    return n.get("source", "")


def check_nights(nights: list, planet_name: str, eph: dict | None = None) -> None:
    """Sets each night's "use" and "status" (level, text): ✖ another planet, the same night twice, too few points or
    no predicted transit in the data (left out); ⚠ a filter other than most nights' (depth and limb darkening may
    differ). Nights the user left out by hand ("user_off") stay out."""
    want = norm_name(planet_name)
    filters = [str(n.get("filter") or "").upper() for n in nights if not n.get("error")]
    main_filter = max(set(filters), key=filters.count) if filters else ""
    kept = []
    for n in nights:
        n["status"], n["use"] = ("ok", "✔ ready"), True
        if n.get("error"):
            n["status"], n["use"] = ("bad", "✖ " + n["error"]), False
            continue
        if want and n.get("planet") and norm_name(n["planet"]) != want:
            n["status"], n["use"] = ("bad", f"✖ another planet ({n['planet']}), left out"), False
            continue
        if len(n["t"]) < MIN_POINTS:
            n["status"], n["use"] = ("bad", f"✖ only {len(n['t'])} points (needs {MIN_POINTS})"), False
            continue
        lo, hi = float(n["t"].min()), float(n["t"].max())
        dup = next((k for k in kept if not (hi < k["t"].min() or lo > k["t"].max())), None)
        if dup is not None:
            n["status"], n["use"] = ("bad", f"✖ same night as {dup['source']}, left out"), False
            continue
        if eph and eph.get("t0") and eph.get("period"):
            chk = tc.transit_window_check(lo, hi, {"t0": eph["t0"], "t0_err": eph.get("t0_err"),
                                                   "period": eph["period"], "period_err": eph.get("period_err"),
                                                   "t14": eph.get("t14")})
            if chk is not None and not chk["overlap"]:
                n["status"], n["use"] = ("bad", "✖ no transit predicted during these data, left out"), False
                continue
        if n.get("user_off"):
            why = n.get("off_reason")
            n["status"], n["use"] = ("off", f"— left out: {why} (Leave out / put back includes it)" if why else
                                     "— left out by you"), False
            continue
        if main_filter and str(n.get("filter") or "").upper() != main_filter:
            n["status"] = ("warn", f"⚠ filter {n.get('filter') or '?'} (most nights {main_filter}): depth and limb "
                                   "darkening may differ; consider Depth per filter")
        elif any(lvl == "info" for lvl, _t in n.get("notes", [])) or n.get("time_note"):
            n["status"] = ("ok", "✔ ready (" + "; ".join([n["time_note"]] if n.get("time_note") else []
                                                         + [t for lvl, t in n.get("notes", []) if lvl == "info"]) + ")")
        kept.append(n)


# ---- Joint model ------------------------------------------------------------------------------------------------
class JointProblem:
    """Several nights, one shape. Parameters: a_rs, b, and per filter group k, q1, q2 (Kipping); per night tc
    (days from that night's t_ref), c0, c_am, c_t and the flip step, as in transit_core.TransitProblem. Gaussian
    priors on a_rs and b (from the planet, as the single-night fit) and on u1, u2 per group (sigma ld_sigma)."""

    def __init__(self, nights: list, period: float, priors: dict, ld_priors: dict, groups: list, start: dict,
                 detrend_airmass: bool = True, detrend_time: bool = False):
        self.period = float(period)
        self.priors = dict(priors)
        self.ld_priors = dict(ld_priors)
        self.groups = list(groups)
        self.nights = []
        names = ["a_rs", "b"]
        for g in self.groups:
            names += [f"k:{g}", f"q1:{g}", f"q2:{g}"]
        for j, n in enumerate(nights):
            t_ref = float(math.floor(n["t"].min()))
            t = n["t"] - t_ref
            am = n.get("airmass")
            has_am = am is not None and detrend_airmass
            amc = (am - float(np.mean(am))) if has_am else np.zeros_like(t)
            t_flip = n.get("t_flip")
            after = None
            if t_flip is not None and (n["t"] < t_flip).any() and (n["t"] >= t_flip).any():
                after = (n["t"] >= t_flip).astype(float)
            d = {"t": t, "t_ref": t_ref, "flux": n["flux"], "err": n["err"].copy(), "am": amc,
                 "t_mid": float(np.mean(t)), "after": after, "group": n.get("group", self.groups[0])}
            names += [f"tc:{j}", f"c0:{j}"]
            if has_am:
                names.append(f"c_am:{j}")
            if detrend_time:
                names.append(f"c_t:{j}")
            if after is not None:
                names.append(f"step:{j}")
            self.nights.append(d)
        self.free = names
        self.index = {nm: i for i, nm in enumerate(names)}
        self.start = np.array([float(start.get(nm, 0.0)) for nm in names])
        self._ig = {g: (self.index[f"k:{g}"], self.index[f"q1:{g}"], self.index[f"q2:{g}"]) for g in self.groups}
        self._in = []
        for j, d in enumerate(self.nights):
            self._in.append(tuple(self.index.get(f"{p}:{j}") for p in ("tc", "c0", "c_am", "c_t", "step")))

    def full(self, x) -> dict:
        return {nm: float(v) for nm, v in zip(self.free, x)}

    def set_errors(self, scales):
        for d, s in zip(self.nights, scales):
            d["err"] = d["err"] * float(s)

    def _shape(self, x):
        out = {}
        for g, (ik, i1, i2) in self._ig.items():
            u1, u2 = tc.kipping_to_u(x[i1], x[i2])
            out[g] = (x[ik], u1, u2, occ_curve(x[ik], u1, u2))   # one grid per shape set and evaluation
        return out

    def in_bounds(self, x) -> bool:
        a, b = x[0], x[1]
        if not (1.2 < a < 200 and 0.0 <= b < a):
            return False
        for g, (ik, i1, i2) in self._ig.items():
            k = x[ik]
            if not (0.005 < k < 0.6 and b < 1.0 + k and 0.0 < x[i1] < 1.0 and 0.0 < x[i2] < 1.0):
                return False
        for d, (itc, _c0, _cam, _ct, ist) in zip(self.nights, self._in):
            if not (d["t"][0] - 0.2 < x[itc] < d["t"][-1] + 0.2):
                return False
            if ist is not None and abs(x[ist]) >= 0.5:
                return False
        return True

    def night_model(self, x, j: int, t=None, shape=None, transit_only: bool = False, exact: bool = False):
        d = self.nights[j]
        itc, ic0, icam, ict, ist = self._in[j]
        shape = shape or self._shape(x)
        k, u1, u2, (grid, curve) = shape[d["group"]]
        tt = d["t"] if t is None else np.asarray(t, dtype=float)
        z = tc.separation(tt, x[itc], self.period, x[0], x[1])
        occ = tc.occultation(z, k, u1, u2) if exact else np.interp(z, grid, curve, right=1.0)
        if transit_only:
            return occ
        return occ * self.baseline(x, j, t)

    def baseline(self, x, j: int, t=None):
        d = self.nights[j]
        _itc, ic0, icam, ict, ist = self._in[j]
        own = t is None
        tt = d["t"] if own else np.asarray(t, dtype=float)
        base = x[ic0] + np.zeros_like(tt)
        if icam is not None:
            am = d["am"] if own else np.interp(tt, d["t"], d["am"])
            base = base + x[icam] * am
        if ict is not None:
            base = base + x[ict] * (tt - d["t_mid"])
        if ist is not None:
            after = d["after"] if own else (tt >= d["t"][d["after"].argmax()]).astype(float)
            base = base * (1.0 + x[ist] * after)
        return base

    def _prior_resid(self, x) -> list:
        out = []
        for name, idx in (("a_rs", 0), ("b", 1)):
            if name in self.priors:
                mean, sigma = self.priors[name]
                if sigma and sigma > 0:
                    out.append((x[idx] - mean) / sigma)
        for g, (ik, i1, i2) in self._ig.items():
            if g in self.ld_priors:
                m1, m2, s = self.ld_priors[g]
                u1, u2 = tc.kipping_to_u(x[i1], x[i2])
                out += [(u1 - m1) / s, (u2 - m2) / s]
        return out

    def log_prob(self, x) -> float:
        x = np.asarray(x, dtype=float)
        if not self.in_bounds(x):
            return -np.inf
        shape = self._shape(x)
        chi2 = 0.0
        for j, d in enumerate(self.nights):
            r = (d["flux"] - self.night_model(x, j, shape=shape)) / d["err"]
            chi2 += float(np.dot(r, r))
        pr = self._prior_resid(x)
        return -0.5 * (chi2 + float(np.dot(pr, pr)))

    def night_chi2(self, x) -> list:
        x = np.asarray(x, dtype=float)
        shape = self._shape(x)
        out = []
        for j, d in enumerate(self.nights):
            r = (d["flux"] - self.night_model(x, j, shape=shape)) / d["err"]
            out.append(float(np.dot(r, r)))
        return out

    def residual_vector(self, x) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        shape = self._shape(x)
        parts = [(d["flux"] - self.night_model(x, j, shape=shape)) / d["err"] for j, d in enumerate(self.nights)]
        parts.append(np.asarray(self._prior_resid(x), dtype=float))
        return np.concatenate(parts)

    def scales(self) -> np.ndarray:
        s = {"a_rs": 0.3, "b": 0.03, "k": 0.01, "q1": 0.05, "q2": 0.05, "tc": 1e-3, "c0": 1e-3, "c_am": 1e-3,
             "c_t": 1e-3, "step": 1e-3}
        return np.array([s[nm.split(":")[0]] for nm in self.free])

    def bounds(self):
        lo = {"a_rs": 1.3, "b": 0.0, "k": 0.006, "q1": 1e-4, "q2": 1e-4, "c0": 0.5, "c_am": -1.0, "c_t": -1.0,
              "step": -0.49}
        hi = {"a_rs": 199.0, "b": 1.5, "k": 0.59, "q1": 0.9999, "q2": 0.9999, "c0": 1.5, "c_am": 1.0, "c_t": 1.0,
              "step": 0.49}
        los, his = [], []
        for nm in self.free:
            base, _sep, j = nm.partition(":")
            if base == "tc":
                d = self.nights[int(j)]
                los.append(d["t"][0] - 0.2)
                his.append(d["t"][-1] + 0.2)
            else:
                los.append(lo[base])
                his.append(hi[base])
        return np.array(los), np.array(his)


def _least_squares(prob: JointProblem, x0, extra_starts=()):
    from scipy.optimize import least_squares
    lo, hi = prob.bounds()
    best = None
    for trial in [x0] + list(extra_starts):
        xs = np.clip(np.asarray(trial, dtype=float), lo + 1e-9, hi - 1e-9)
        try:
            sol = least_squares(prob.residual_vector, xs, bounds=(lo, hi), x_scale=prob.scales(), max_nfev=6000)
        except Exception:
            continue
        if not prob.in_bounds(sol.x):
            continue
        if best is None or sol.cost < best.cost:
            best = sol
    if best is None:
        raise ValueError("the joint least-squares fit did not converge")
    return best.x


def _ld_band_key(filt) -> str:
    return str(filt or "").strip().upper() or "CV"


def joint_fit(nights: list, planet: dict, detrend_airmass: bool = True, detrend_time: bool = False,
              per_filter: bool = False, mcmc_steps: int = DEFAULT_STEPS, progress=None, cancel=None, log=None,
              seed: int = 11) -> dict:
    """The joint fit of the nights marked "use". planet: the period (and the shape priors, as for one night: a/R*
    and b from the published values, limb darkening from Teff and the filter). Steps:
    1. each night alone (transit_core.lsq_fit) for start values;
    2. the joint least-squares fit on the files' errors;
    3. each night's errors scaled by max(1, sqrt(chi2_nu) * beta) from its joint residuals (beta = red-noise
       time-averaging factor). The files already carry the single-night fit's scaled errors, so the usual
       max(1, chi2_nu) * beta would count beta twice; this way an already-scaled night stays at x1;
    4. least squares again, then MCMC (transit_core.run_mcmc) for the values and uncertainties."""
    say = log or (lambda _m: None)
    use = [n for n in nights if n.get("use")]
    if len(use) < 2:
        raise ValueError("a joint fit needs at least two usable nights")
    if not planet or not planet.get("period"):
        raise ValueError("the planet's period is not known: look the planet up first")
    period = float(planet["period"])
    groups = sorted({_ld_band_key(n.get("filter")) for n in use}) if per_filter else ["all"]
    for n in use:
        n["group"] = _ld_band_key(n.get("filter")) if per_filter else "all"
    # 1. Each night alone, least squares (start values only).
    say(f"Multi-night fit: {len(use)} nights of {planet.get('name') or 'the planet'}; each night alone first…")
    singles = []
    for i, n in enumerate(use):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Canceled")
        after = (n["t"] >= n["t_flip"]) if n.get("t_flip") is not None else None
        am = n["airmass"] if n.get("airmass") is not None else np.full(len(n["t"]), np.nan)
        try:
            s = tc.lsq_fit(n["t"], n["flux"], n["err"], am, planet, detrend_airmass, detrend_time, after_flip=after)
        except Exception as exc:
            raise ValueError(f"{n['source']}: the night alone could not be fitted ({exc})") from exc
        singles.append(s)
        if progress is not None:
            progress("prep", i + 1, len(use))
    ks = [s["p"]["k"] for s in singles]
    start = {"a_rs": float(np.median([s["p"]["a_rs"] for s in singles])),
             "b": float(np.median([s["p"]["b"] for s in singles]))}
    priors = tc.prepare_priors(planet, start["a_rs"])
    if "a_rs" in priors:
        start["a_rs"] = float(priors["a_rs"][0])
    if "b" in priors:
        start["b"] = float(min(priors["b"][0], 0.95))
    ld_priors = {}
    for g in groups:
        band = (use[[n["group"] for n in use].index(g)].get("filter")) if per_filter else _majority_filter(use)
        u1, u2 = tc.limb_darkening_guess(planet.get("teff"), band)
        ld_priors[g] = (u1, u2, 0.1)
        q1, q2 = tc.u_to_kipping(u1, u2)
        kg = [k for k, n in zip(ks, use) if n["group"] == g] or ks
        start.update({f"k:{g}": float(np.median(kg)), f"q1:{g}": q1, f"q2:{g}": q2})
    for j, (n, s) in enumerate(zip(use, singles)):
        t_ref = float(math.floor(n["t"].min()))
        start[f"tc:{j}"] = s["p"]["tc"] - t_ref
        start[f"c0:{j}"] = s["p"]["c0"]
        start[f"c_am:{j}"] = s["p"].get("c_am", 0.0)
        start[f"c_t:{j}"] = s["p"].get("c_t", 0.0)
        start[f"step:{j}"] = s["p"].get("step", 0.0)
    prob = JointProblem(use, period, priors, ld_priors, groups, start, detrend_airmass, detrend_time)
    # 2. Joint least squares on the files' errors.
    if progress is not None:
        progress("joint", 0, 1)
    say(f"  joint least squares: {len(prob.free)} parameters ({len(groups)} shape set"
        f"{'s' if len(groups) > 1 else ''}, {len(use)} nights)…")
    alt = []
    for bb in (0.1, 0.5, 0.8):
        x = prob.start.copy()
        x[1] = bb
        alt.append(x)
    x = _least_squares(prob, prob.start, alt)
    # 3. Each night's errors from its joint residuals.
    scales, rchis, betas = [], [], []
    for j, d in enumerate(prob.nights):
        resid = d["flux"] - prob.night_model(x, j)
        npar = 1 + sum(1 for i in prob._in[j][1:] if i is not None)
        rchi = float(np.sum((resid / d["err"]) ** 2) / max(len(resid) - npar - 1, 1))
        cad = float(np.median(np.diff(d["t"]))) if len(d["t"]) > 1 else 1e-3
        beta = tc.red_noise_beta(d["t"], resid, cad)
        rchis.append(rchi)
        betas.append(beta)
        scales.append(max(1.0, math.sqrt(rchi) * beta))
    prob.set_errors(scales)
    say("  error bars x" + ", x".join(f"{s:.2f}" for s in scales) + " (χ²ν " + ", ".join(f"{r:.2f}" for r in rchis)
        + "; β " + ", ".join(f"{b:.2f}" for b in betas) + ")")
    x = _least_squares(prob, x)
    # 4. MCMC.
    n_burn = max(500, mcmc_steps // 3)
    say(f"  MCMC: {max(2 * len(prob.free) + 2, 20)} walkers, {n_burn} burn-in + {mcmc_steps} steps…")
    chain = tc.run_mcmc(prob, x, n_walkers=20, n_burn=n_burn, n_steps=mcmc_steps, seed=seed, progress=progress,
                        cancel=cancel)
    flat = chain["flat"]
    summ = {}
    for i, nm in enumerate(prob.free):
        lo, med, hi = np.percentile(flat[:, i], [15.865, 50, 84.135])
        summ[nm] = (float(med), float((hi - lo) / 2.0))
    best = np.array([summ[nm][0] for nm in prob.free])
    pick = flat[np.random.default_rng(3).integers(0, len(flat), min(len(flat), 4000))]
    shape = {"a_rs": summ["a_rs"], "b": summ["b"]}
    inc = np.degrees(np.arccos(np.clip(pick[:, 1] / pick[:, 0], 0, 1)))
    shape["inc"] = _med_err(inc)
    for g in groups:
        ik, i1, i2 = prob._ig[g]
        k = pick[:, ik]
        u = np.array([tc.kipping_to_u(a, b) for a, b in zip(pick[:, i1], pick[:, i2])])
        t14 = np.array([tc.total_duration(period, a, min(b, a * 0.999), kk) for a, b, kk in
                        zip(pick[:, 0], pick[:, 1], k)])
        shape[g] = {"k": summ[f"k:{g}"], "depth": _med_err(k ** 2), "u1": _med_err(u[:, 0]), "u2": _med_err(u[:, 1]),
                    "t14": _med_err(t14)}
    chi2s = prob.night_chi2(best)
    out_nights = []
    for j, (n, d) in enumerate(zip(use, prob.nights)):
        itc = prob._in[j][0]
        model = prob.night_model(best, j, exact=True)
        base = prob.baseline(best, j)
        resid = d["flux"] - model
        out_nights.append({
            "source": n["source"], "label": night_label(n), "filter": n.get("filter", ""), "kind": n.get("kind"),
            "tc": (summ[f"tc:{j}"][0] + d["t_ref"], summ[f"tc:{j}"][1]), "single_tc": n.get("single_tc"),
            "t": d["t"] + d["t_ref"], "flux": d["flux"], "err": d["err"], "model": model, "baseline": base,
            "detrended": d["flux"] / base, "residuals": resid, "rms": float(np.std(resid)),
            "rms_bin10": tc.binned_rms(d["t"], resid, 10.0), "n_points": len(d["t"]), "error_scale": scales[j],
            "rchi2_raw": rchis[j], "beta": betas[j], "chi2": chi2s[j], "group": d["group"],
            "airmass": n.get("airmass") is not None and prob._in[j][2] is not None,
            "flip": prob._in[j][4] is not None, "time_note": n.get("time_note", ""), "index": j})
        _ = itc
    n_data = sum(len(d["t"]) for d in prob.nights)
    say("  joint fit done: " + ", ".join(f"{o['label']} Tc {o['tc'][0]:.5f} ± {o['tc'][1]:.5f}" for o in out_nights))
    return {"nights": out_nights, "shape": shape, "groups": groups, "summary": summ, "best": best,
            "free": list(prob.free), "chain": chain, "problem": prob, "period": period, "priors": priors,
            "ld_priors": ld_priors, "planet": dict(planet), "chi2": float(sum(chi2s)), "n_data": n_data,
            "rchi2": float(sum(chi2s)) / max(n_data - len(prob.free), 1),
            "detrend": [nm for nm, on in (("airmass", detrend_airmass), ("time", detrend_time)) if on],
            "per_filter": per_filter}


def _majority_filter(nights) -> str:
    f = [str(n.get("filter") or "") for n in nights]
    return max(set(f), key=f.count) if f else ""


def _med_err(v):
    lo, med, hi = np.percentile(np.asarray(v, dtype=float), [15.865, 50, 84.135])
    return float(med), float((hi - lo) / 2.0)


# ---- Timing: O − C and linear ephemerides -----------------------------------------------------------------------
def parse_ephemeris(t0, t0_err, period, period_err, source: str = "") -> dict:
    """A typed ephemeris (strings or numbers). T0 must be a BJD_TDB (2,4xx,xxx.x); a reduced JD (5x,xxx) or MJD is
    accepted and said so. Uncertainties may be blank (then not known: O − C sigma has the fit's part only)."""
    def num(v, what, required=True):
        s = str(v if v is not None else "").strip().replace("±", "").replace("+/-", "").replace(",", ".")
        if not s:
            if required:
                raise ValueError(f"{what} is empty")
            return None
        try:
            return float(s)
        except ValueError:
            raise ValueError(f"{what} is not a number: {s!r}") from None
    t0v = num(t0, "T0")
    pv = num(period, "Period")
    note = ""
    if 40000 < t0v < 100000:
        t0v += 2400000.0
        note = "T0 read as a reduced JD (+2,400,000)"
    if not 2.3e6 < t0v < 2.6e6:
        raise ValueError(f"T0 {t0v} is not a BJD_TDB (expected 2,4xx,xxx.x)")
    if not 0.05 < pv < 1000:
        raise ValueError(f"Period {pv} d is not a planet's period")
    te, pe = num(t0_err, "T0 ±", False), num(period_err, "Period ±", False)
    return {"t0": t0v, "t0_err": abs(te) if te is not None else None, "period": pv,
            "period_err": abs(pe) if pe is not None else None, "source": (source or "typed ephemeris").strip(),
            "note": note}


def oc_rows(tcs, sigs, eph: dict) -> list:
    """O − C of each Tc against one ephemeris: epoch n (that ephemeris's own count), the predicted Tc and its
    sqrt(sigma_T0² + (n sigma_P)²), O − C in minutes, sigma = fit and ephemeris in quadrature, and in units of it."""
    out = []
    for t, s in zip(tcs, sigs):
        p = tc.predicted_tc(t, eph["t0"], eph["period"], eph.get("t0_err"), eph.get("period_err"))
        oc = (t - p["tc"]) * 1440.0
        sig = math.hypot(s, p["err"]) * 1440.0
        out.append({"epoch": p["epoch"], "pred": p["tc"], "pred_err_min": p["err"] * 1440.0, "oc_min": oc,
                    "fit_err_min": s * 1440.0, "sigma_min": sig, "nsigma": oc / sig if sig > 0 else float("nan")})
    return out


def linear_ephemeris(epochs, tcs, sigs, prior: dict | None = None) -> dict | None:
    """Weighted least-squares Tc = T0 + (n − n_ref) P. n_ref is the epoch nearest the weighted centre of the data,
    so T0 and P are nearly uncorrelated there. With prior (an ephemeris dict), its T0 (at its epoch 0 in the same
    count) and P enter as two more measurements (treated as independent). Errors are scaled by sqrt(chi2_nu) when
    chi2_nu > 1 (the scatter is then larger than the error bars say). None with fewer than 2 points."""
    n = np.asarray(epochs, dtype=float)
    y = np.asarray(tcs, dtype=float)
    s = np.asarray(sigs, dtype=float)
    if len(n) < 2 or len(np.unique(n)) < 2:
        if prior is None or len(n) < 1:
            return None
    w = 1.0 / s ** 2
    n_ref = int(round(float(np.sum(w * n) / np.sum(w))))
    y0 = float(np.round(np.median(y), 1))
    rows_a, rows_y, rows_s = [[1.0, v - n_ref] for v in n], list(y - y0), list(s)
    used_prior = []
    if prior is not None:
        if prior.get("t0_err"):
            rows_a.append([1.0, 0.0 - n_ref])
            rows_y.append(prior["t0"] - y0)
            rows_s.append(prior["t0_err"])
            used_prior.append("T0")
        if prior.get("period_err"):
            rows_a.append([0.0, 1.0])
            rows_y.append(prior["period"])
            rows_s.append(prior["period_err"])
            used_prior.append("P")
    A = np.asarray(rows_a)
    Y = np.asarray(rows_y)
    S = np.asarray(rows_s)
    if np.linalg.matrix_rank(A) < 2:
        return None
    Wm = 1.0 / S ** 2
    ata = A.T @ (A * Wm[:, None])
    cov = np.linalg.inv(ata)
    par = cov @ (A.T @ (Wm * Y))
    resid = Y - A @ par
    chi2 = float(np.sum(resid ** 2 * Wm))
    dof = len(Y) - 2
    rchi = chi2 / dof if dof > 0 else float("nan")
    scaled = bool(dof > 0 and rchi > 1.0)
    cov_out = cov * rchi if scaled else cov
    t0, per = float(par[0] + y0), float(par[1])
    e0, ep = math.sqrt(cov_out[0, 0]), math.sqrt(cov_out[1, 1])
    return {"t0": t0, "t0_err": e0, "period": per, "period_err": ep, "cov": float(cov_out[0, 1]),
            "corr": float(cov_out[0, 1] / (e0 * ep)) if e0 > 0 and ep > 0 else float("nan"), "n_ref": n_ref,
            "chi2": chi2, "dof": dof, "chi2_red": rchi, "scaled": scaled, "n_points": len(n), "prior": used_prior,
            "cov_matrix": cov_out}


def linear_prediction(lin: dict, n) -> tuple:
    """(Tc, sigma) of a linear_ephemeris at epoch n (same count), with the full covariance."""
    dn = np.asarray(n, dtype=float) - lin["n_ref"]
    pred = lin["t0"] + dn * lin["period"]
    c = lin["cov_matrix"]
    var = c[0, 0] + 2 * dn * c[0, 1] + dn * dn * c[1, 1]
    return pred, np.sqrt(np.clip(var, 0, None))


def timing_analysis(nights: list, eph_in: dict | None, eph_alt: dict | None = None, period: float | None = None) -> dict:
    """Per-epoch timing table and the plain-words reading of it. nights: joint_fit's "nights" (tc = (value,
    sigma)). Epochs are counted from the input ephemeris's T0 (negative before it)."""
    tcs = np.array([n["tc"][0] for n in nights])
    sig = np.array([n["tc"][1] for n in nights])
    order = np.argsort(tcs)
    tcs, sig = tcs[order], sig[order]
    labels = [nights[i]["label"] for i in order]
    out = {"tc": tcs, "sig": sig, "labels": labels, "order": order, "input": None, "alt": None, "own": None,
           "combined": None, "statements": []}
    if eph_in and eph_in.get("t0") and eph_in.get("period"):
        rows = oc_rows(tcs, sig, eph_in)
        epochs = np.array([r["epoch"] for r in rows])
        out["input"] = {"eph": eph_in, "rows": rows}
        out["epochs"] = epochs
    else:
        # No input ephemeris: count from the first night with the joint fit's period.
        p = period
        epochs = np.round((tcs - tcs[0]) / p).astype(int) if p else np.arange(len(tcs))
        out["epochs"] = epochs
    if eph_alt and eph_alt.get("t0") and eph_alt.get("period"):
        out["alt"] = {"eph": eph_alt, "rows": oc_rows(tcs, sig, eph_alt)}
    lin = linear_ephemeris(out["epochs"], tcs, sig)
    if lin is not None:
        pred, psig = linear_prediction(lin, out["epochs"])
        rows = []
        for t, s, p_, ps in zip(tcs, sig, pred, psig):
            oc = (t - p_) * 1440.0
            rows.append({"oc_min": oc, "fit_err_min": s * 1440.0, "sigma_min": s * 1440.0,
                         "nsigma": oc / (s * 1440.0) if s > 0 else float("nan"), "pred_err_min": ps * 1440.0})
        lin["source"] = "this joint fit (weighted least squares)"
        out["own"] = {"eph": lin, "rows": rows}
    if eph_in and eph_in.get("t0") and eph_in.get("period"):
        comb = linear_ephemeris(out["epochs"], tcs, sig, prior=eph_in)
        if comb is not None and comb["prior"]:
            comb["source"] = "this joint fit with the input ephemeris's T0 and P as two more measurements"
            out["combined"] = comb
    out["statements"] = timing_statements(out)
    return out


def _wmean(v, s):
    w = 1.0 / np.asarray(s, dtype=float) ** 2
    m = float(np.sum(w * v) / np.sum(w))
    return m, float(1.0 / math.sqrt(np.sum(w)))


def timing_statements(a: dict) -> list:
    """Plain words (level, text): on time or not against each ephemeris, and whether an offset is the ephemeris's
    own error (stale ephemeris) or the data's; the scatter about the nights' own linear ephemeris; outlier nights."""
    out = []
    n = len(a["tc"])
    for key in ("input", "alt"):
        block = a.get(key)
        if not block:
            continue
        eph, rows = block["eph"], block["rows"]
        oc = np.array([r["oc_min"] for r in rows])
        fit = np.array([r["fit_err_min"] for r in rows])
        epe = np.array([r["pred_err_min"] for r in rows])
        m, sm = _wmean(oc, fit)
        chi_c = float(np.sum(((oc - m) / fit) ** 2))
        rchi_c = chi_c / (n - 1) if n > 1 else float("nan")
        name = f"the {'input' if key == 'input' else 'alternative'} ephemeris ({eph.get('source') or 'no source'})"
        eph_txt = (f"±{np.mean(epe):.1f} min" if eph.get("t0_err") is not None else "an unstated uncertainty")
        agree = (f"the nights agree with each other (scatter about their mean χ²ν {rchi_c:.2f})" if n > 1 and
                 rchi_c < 3 else f"the nights scatter about their mean more than their error bars (χ²ν {rchi_c:.1f})")
        if abs(m) <= 3 * sm:
            out.append(("ok", f"✔ On time against {name}: mean O − C {m:+.1f} ± {sm:.1f} min (fit errors only); "
                              f"{agree}."))
        elif eph.get("t0_err") is not None and abs(m) <= 2.0 * float(np.mean(epe)):
            out.append(("info", f"Against {name} all {n} nights run {'late' if m > 0 else 'early'} by "
                                f"{m:+.1f} ± {sm:.1f} min on average (fit errors only), and {agree}. That ephemeris "
                                f"predicts these epochs only to {eph_txt}, so the offset is the ephemeris's own "
                                "error (it is out of date), not timing variations in the data. Use an up-to-date "
                                "ephemeris; the joint fit's own is below."))
        elif eph.get("t0_err") is None:
            out.append(("warn", f"⚠ Against {name} the nights are {'late' if m > 0 else 'early'} by {m:+.1f} ± "
                                f"{sm:.1f} min (fit errors only), and {agree}. That ephemeris states no uncertainty, "
                                "so this cannot tell an out-of-date ephemeris from a timing problem; compare with an "
                                "ephemeris that gives its ± (the input one, or the joint fit's own)."))
        else:
            out.append(("warn", f"⚠ Against {name} the nights are {'late' if m > 0 else 'early'} by {m:+.1f} ± "
                                f"{sm:.1f} min (fit errors only), more than that ephemeris's own uncertainty "
                                f"({eph_txt} at these epochs). Check that every night is in BJD_TDB and that the ephemeris is in "
                                "BJD_TDB too; a real period change needs many more epochs to show."))
    own = a.get("own")
    if own:
        lin = own["eph"]
        if lin["dof"] <= 0:
            out.append(("info", "Two nights: their own linear ephemeris passes through both exactly (no check of the "
                                "scatter; a third night gives one)."))
        elif lin["chi2_red"] > 3.0:
            out.append(("warn", f"⚠ The nights scatter about their own linear ephemeris more than their error bars "
                                f"allow (χ²ν {lin['chi2_red']:.1f}): look at the outlier nights, or the Tc errors "
                                "are too small (red noise, a partial transit). Its errors are scaled by "
                                f"√χ²ν = {math.sqrt(lin['chi2_red']):.2f}."))
        else:
            out.append(("ok", f"✔ The nights fit a linear ephemeris (χ²ν {lin['chi2_red']:.2f}, {lin['dof']} degree"
                              f"{'' if lin['dof'] == 1 else 's'} of freedom): no sign of timing variations at this "
                              "precision."))
        bad = [a["labels"][i] for i, r in enumerate(own["rows"]) if lin["dof"] > 0
               and abs(r["oc_min"]) > OUTLIER_SIGMA * r["sigma_min"]]
        if bad:
            out.append(("warn", f"⚠ Outlier night{'s' if len(bad) > 1 else ''} (|O − C| > {OUTLIER_SIGMA:g}σ from "
                                f"the nights' own ephemeris): {', '.join(bad)}."))
    return out


def outlier_flags(a: dict) -> list:
    """Per epoch (time order): '' or '⚠ outlier' (|O − C| > 3σ against the nights' own linear ephemeris)."""
    own = a.get("own")
    if not own or own["eph"]["dof"] <= 0:
        return [""] * len(a["tc"])
    return ["⚠ outlier" if abs(r["oc_min"]) > OUTLIER_SIGMA * r["sigma_min"] else "" for r in own["rows"]]


# ---- Files ------------------------------------------------------------------------------------------------------
def eph_line(e: dict) -> str:
    te = f" +/- {e['t0_err']:.7f}" if e.get("t0_err") is not None else " (no uncertainty given)"
    pe = f" +/- {e['period_err']:.9f}" if e.get("period_err") is not None else " (no uncertainty given)"
    return f"T0 {e['t0']:.7f}{te} BJD_TDB, P {e['period']:.9f}{pe} d"


def lin_line(lin: dict) -> str:
    return (f"T0 {lin['t0']:.7f} +/- {lin['t0_err']:.7f} BJD_TDB (epoch {lin['n_ref']} of the input count), "
            f"P {lin['period']:.9f} +/- {lin['period_err']:.9f} d, cov(T0,P) {lin['cov']:.4e} d^2 (correlation "
            f"{lin['corr']:+.3f}), chi2_red {lin['chi2_red']:.2f} ({lin['dof']} dof)"
            + (", errors scaled by sqrt(chi2_red)" if lin["scaled"] else ""))


def write_timing_csv(path: str, fit: dict, a: dict, meta: dict | None = None) -> int:
    """Per-epoch timings with the ephemerides in the header (ExoClock-ready: BJD_TDB mid-times with 1-sigma errors).
    First line '# Created <DTG> by SHOBS-P' as every SHOBS-P CSV. Returns the number of rows."""
    meta = meta or {}
    pl = fit.get("planet") or {}
    lines = [core.created_line(),
             f"# SHOBS-P {meta.get('version', '')} multi-night transit timing: {pl.get('name', '')}, "
             f"{len(fit['nights'])} nights, joint fit (common shape, per-night Tc and baseline; MCMC)"]
    sh = fit["shape"]
    for g in fit["groups"]:
        s = sh[g]
        lines.append(f"# Shape{'' if g == 'all' else ' ' + g}: Rp/R* {s['k'][0]:.5f} +/- {s['k'][1]:.5f}, depth "
                     f"{s['depth'][0] * 100:.3f} +/- {s['depth'][1] * 100:.3f} %, T14 {s['t14'][0] * 24:.4f} +/- "
                     f"{s['t14'][1] * 24:.4f} h")
    lines.append(f"# a/R* {sh['a_rs'][0]:.3f} +/- {sh['a_rs'][1]:.3f}, b {sh['b'][0]:.3f} +/- {sh['b'][1]:.3f}, inc "
                 f"{sh['inc'][0]:.2f} +/- {sh['inc'][1]:.2f} deg; detrending: {', '.join(fit['detrend']) or 'none'}")
    if a.get("input"):
        e = a["input"]["eph"]
        lines.append(f"# Input ephemeris (oc_input columns): {eph_line(e)}; {e.get('source', '')}")
    if a.get("alt"):
        e = a["alt"]["eph"]
        lines.append(f"# Alternative ephemeris (oc_alt columns; epoch_alt is its own count): {eph_line(e)}; "
                     f"{e.get('source', '')}")
    if a.get("own"):
        lines.append(f"# Updated ephemeris from these nights (oc_own columns; weighted least squares): "
                     f"{lin_line(a['own']['eph'])}")
    if a.get("combined"):
        lines.append(f"# Updated ephemeris, these nights + input T0 and P as measurements: {lin_line(a['combined'])}")
    lines.append("# O-C sigma: oc_*_err_min = Tc error and the ephemeris's propagated error "
                 "sqrt(sigma_T0^2 + (n sigma_P)^2) in quadrature (oc_own: Tc error only)")
    lines = [core.ascii_safe(x) for x in lines]
    head = ["epoch", "tc_bjd_tdb", "tc_err_d", "tc_err_min", "oc_input_min", "oc_input_err_min", "oc_input_sigma",
            "epoch_alt", "oc_alt_min", "oc_alt_err_min", "oc_alt_sigma", "oc_own_min", "oc_own_err_min",
            "oc_own_sigma", "flag", "night", "filter", "n_points", "rms_pct", "single_night_tc", "source"]
    flags = outlier_flags(a)
    nights = [fit["nights"][i] for i in a["order"]]

    def f(v, fmt):
        return fmt.format(v) if v is not None and isinstance(v, (int, float)) and math.isfinite(v) else ""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        for x in lines:
            fh.write(x + "\r\n")
        w = csv.writer(fh)
        w.writerow(head)
        for i, n in enumerate(nights):
            ri = a["input"]["rows"][i] if a.get("input") else {}
            ra = a["alt"]["rows"][i] if a.get("alt") else {}
            ro = a["own"]["rows"][i] if a.get("own") else {}
            w.writerow([int(a["epochs"][i]), f"{a['tc'][i]:.6f}", f"{a['sig'][i]:.6f}", f(a["sig"][i] * 1440, "{:.2f}"),
                        f(ri.get("oc_min"), "{:+.2f}"), f(ri.get("sigma_min"), "{:.2f}"), f(ri.get("nsigma"), "{:+.2f}"),
                        ra.get("epoch", ""), f(ra.get("oc_min"), "{:+.2f}"), f(ra.get("sigma_min"), "{:.2f}"),
                        f(ra.get("nsigma"), "{:+.2f}"), f(ro.get("oc_min"), "{:+.2f}"), f(ro.get("sigma_min"), "{:.2f}"),
                        f(ro.get("nsigma"), "{:+.2f}"), core.ascii_safe(flags[i].replace("⚠ ", "")), n["label"],
                        n.get("filter", ""), n["n_points"], f(n["rms"] * 100, "{:.3f}"),
                        f((n.get("single_tc") or (None,))[0], "{:.6f}"), core.ascii_safe(n["source"])])
    return len(nights)


# ---- Plots ------------------------------------------------------------------------------------------------------
OC_COLORS = {"input": "#1565c0", "alt": "#c2185b", "own": "#0b6e4f"}


def draw(fig, fit: dict, a: dict, title: str = "") -> None:
    """O − C panels (one per ephemeris: input, alternative, the nights' own) on the left; the nights stacked with the
    joint model, and all nights folded and binned at the bottom, on the right. Sizes in points, so it reads at any
    display scaling."""
    fig.clear()
    panels = [k for k in ("input", "alt", "own") if a.get(k)]
    gs = fig.add_gridspec(max(len(panels), 1), 2, width_ratios=(1.0, 1.15))
    ep = np.asarray(a["epochs"], dtype=float)
    span = max(ep.max() - ep.min(), 1.0)
    xs = np.linspace(ep.min() - 0.08 * span, ep.max() + 0.08 * span, 200)
    flags = outlier_flags(a)
    ax0 = None
    for i, key in enumerate(panels):
        ax = fig.add_subplot(gs[i, 0], sharex=ax0)
        ax0 = ax0 or ax
        block = a[key]
        rows = block["rows"]
        eph = block["eph"]
        oc = np.array([r["oc_min"] for r in rows])
        fe = np.array([r["fit_err_min"] for r in rows])
        color = OC_COLORS[key]
        if key == "own":
            pred, psig = linear_prediction(eph, xs)
            ax.fill_between(xs, -psig * 1440, psig * 1440, color=color, alpha=0.12, lw=0,
                            label="ephemeris ±1σ")
            lab = "vs the nights' own ephemeris"
        else:
            if eph.get("t0_err") is not None or eph.get("period_err") is not None:
                # the ephemeris's propagated ±1σ, in its own epoch count (an alternative counts from its own T0)
                off = rows[0]["epoch"] - ep[0]
                band = np.hypot(eph.get("t0_err") or 0.0, (xs + off) * (eph.get("period_err") or 0.0)) * 1440
                ax.fill_between(xs, -band, band, color=color, alpha=0.12, lw=0, label="ephemeris ±1σ")
            lab = "vs " + ("input" if key == "input" else "alternative") + f": {eph.get('source', '')}"
        ax.axhline(0, color="#555", lw=0.8)
        ax.errorbar(ep, oc, yerr=fe, fmt="o", color=color, ms=5, capsize=3, lw=1.2, label="Tc ± fit σ")
        if key == "own":
            for x_, y_, fl in zip(ep, oc, flags):
                if fl:
                    ax.plot(x_, y_, "x", color="#c62828", ms=11, mew=2)
        ax.set_title(_short(lab, 70), fontsize=9, loc="left")
        ax.set_ylabel("O − C (min)", fontsize=9)
        ax.tick_params(labelsize=8)
        lim = max(float(np.max(np.abs(oc) + fe)) * 1.3, 1.0)
        if key != "own" and eph.get("t0_err") is not None:
            off = rows[0]["epoch"] - ep[0]
            bmax = float(np.max(np.hypot(eph.get("t0_err") or 0.0, (xs + off) * (eph.get("period_err") or 0.0)))) * 1440
            lim = max(lim, min(1.1 * bmax, 3 * lim))
        ax.set_ylim(-lim, lim)
        ax.legend(fontsize=7, loc="best", framealpha=0.8)
        if i < len(panels) - 1:
            ax.tick_params(labelbottom=False)
        else:
            ax.set_xlabel("Epoch (input ephemeris count)", fontsize=9)
    # Stacked light curves.
    axl = fig.add_subplot(gs[:, 1])
    nights = [fit["nights"][i] for i in a["order"]]
    g0 = fit["shape"][fit["groups"][0]]
    depth = g0["depth"][0]
    rms = max(n["rms"] for n in nights)
    step = max(1.6 * depth, 4 * rms, 0.005)
    prob = fit["problem"]
    best = fit["best"]
    hours_all, flux_all = [], []
    for row, n in enumerate(nights + [None]):
        off = -row * step
        if n is None:
            if len(nights) < 2:
                break
            h = np.concatenate(hours_all)
            f = np.concatenate(flux_all)
            o = np.argsort(h)
            hb, fb = _bin(h[o], f[o], 5.0 / 60.0)
            axl.plot(hb, fb + off, "o", color="#0b6e4f", ms=3.5)
            j = nights[0]["index"]
            hh = np.linspace(h.min(), h.max(), 600)
            tc_rel = best[prob._in[j][0]]
            mod = prob.night_model(best, j, t=tc_rel + hh / 24.0, transit_only=True, exact=True)
            axl.plot(hh, mod + off, color="#c2185b", lw=1.6)
            axl.text(1.0, 1.0 + off + 0.25 * step, "all nights, 5-min bins", transform=axl.get_yaxis_transform(),
                     ha="right", va="bottom", fontsize=8, color="#0b6e4f")
            continue
        j = n["index"]
        h = (n["t"] - n["tc"][0]) * 24.0
        hours_all.append(h)
        flux_all.append(n["detrended"])
        axl.plot(h, n["detrended"] + off, ".", color="#9aa7b0", ms=2.5)
        hb, fb = _bin(h, n["detrended"], 5.0 / 60.0)
        axl.plot(hb, fb + off, "o", color="#33424d", ms=2.8)
        hh = np.linspace(h.min(), h.max(), 400)
        tc_rel = best[prob._in[j][0]]
        mod = prob.night_model(best, j, t=tc_rel + hh / 24.0, transit_only=True, exact=True)
        axl.plot(hh, mod + off, color="#c2185b", lw=1.3)
        ep_i = int(a["epochs"][row])
        axl.text(0.01, 1.0 + off + 0.25 * step, f"{n['label']} · epoch {ep_i} · {n.get('filter') or ''}",
                 transform=axl.get_yaxis_transform(), ha="left", va="bottom", fontsize=8, color="#33424d")
    axl.set_xlabel("Hours from each night's Tc", fontsize=9)
    axl.set_ylabel("Detrended relative flux (offset per night)", fontsize=9)
    axl.tick_params(labelsize=8)
    k = g0["k"]
    axl.set_title(_short(title or "", 80) + (" — " if title else "") + f"joint: Rp/R* {k[0]:.4f} ± {k[1]:.4f}, "
                  f"a/R* {fit['shape']['a_rs'][0]:.2f}, b {fit['shape']['b'][0]:.2f}", fontsize=9, loc="left")


def _short(text: str, n: int) -> str:
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def _bin(x, y, width):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if not len(x):
        return x, y
    idx = np.floor((x - x.min()) / width).astype(int)
    xs, ys = [], []
    for k in np.unique(idx):
        m = idx == k
        if m.sum() >= 2:
            xs.append(x[m].mean())
            ys.append(y[m].mean())
    return np.array(xs), np.array(ys)


# ---- The window (Transit fit page → Multi-night…) ---------------------------------------------------------------
from tkinter import BOTH, END, LEFT, RIGHT, W, X, BooleanVar, StringVar, Text, Toplevel, filedialog, messagebox, ttk  # noqa: E402

import transit_ui as tui  # noqa: E402

try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
except ImportError:      # pragma: no cover
    FigureCanvasTkAgg = None
    Figure = None

LEVEL_COLORS = {"ok": "#2e7d32", "info": "#33424d", "warn": "#b35c00", "bad": "#c62828", "off": "#7a8a94"}


def _eph_from_planet(p: dict | None) -> dict | None:
    if not p or not p.get("t0") or not p.get("period"):
        return None
    return {"t0": float(p["t0"]), "t0_err": p.get("t0_err"), "period": float(p["period"]),
            "period_err": p.get("period_err"), "t14": p.get("t14"), "source": tc.ephemeris_source(p)}


class MultiNightWindow:
    """Several nights of one planet: joint fit, timing per epoch, O − C against the input, a typed and the nights'
    own ephemeris, plots and an ExoClock-ready timing CSV."""

    def __init__(self, app, page):
        self.app = app
        self.page = page
        self.nights = []          # loaded files (dicts from load_night; "error" when a file could not be used)
        self.fit = None
        self.analysis = None
        self.eph_alt = None
        self._running = False
        self._token = None
        p = page.current_planet() if page is not None else None
        self.planet = dict(p) if p else None
        win = Toplevel(app)
        self.win = win
        win.title("SHOBS-P — multi-night transit analysis")
        top = ttk.Frame(win, style="Card.TFrame", padding=10)
        top.pack(fill=BOTH, expand=True)
        self.title = StringVar(value="")
        ttk.Label(top, textvariable=self.title, font=(core.UI_FONT, 15, "bold"), style="Card.TLabel").pack(anchor=W)
        hint = ttk.Label(top, style="Hint.TLabel", justify=LEFT, wraplength=900, text=(
            "Fits several nights of one planet together: one transit shape, each night's own mid-transit time and "
            "baseline. Add AAVSO exoplanet reports (SHOBS-P or EXOTIC, .txt) or SHOBS-P transit-fit CSVs "
            "(_transit_fit.csv); the night fitted on the Transit fit page can join them. Then Fit nights."))
        hint.pack(anchor=W, fill=X, pady=(2, 6))
        top.bind("<Configure>", lambda e: hint.configure(wraplength=max(e.width - 30, 300)), add="+")

        row = ttk.Frame(top, style="Card.TFrame")
        row.pack(fill=X)
        ttk.Button(row, text="Add reports or fit CSVs…", command=self.add_files).pack(side=LEFT)
        ttk.Button(row, text="Leave out / put back", command=self.toggle_selected).pack(side=LEFT, padx=(6, 0))
        ttk.Button(row, text="Remove", command=self.remove_selected).pack(side=LEFT, padx=(6, 0))
        self.use_current = BooleanVar(value=bool(page is not None and page.result is not None))
        self.current_check = ttk.Checkbutton(row, text="Include the Transit fit page's night", variable=self.use_current,
                                             command=self.refresh_nights)
        self.current_check.pack(side=LEFT, padx=(14, 0))

        cols = ("use", "night", "source", "points", "filter", "single", "status")
        self.tree = ttk.Treeview(top, columns=cols, show="headings", height=4, selectmode="extended")
        for col, label, width, stretch in (("use", "Use", 44, False), ("night", "Night", 110, False),
                                           ("source", "File", 260, False), ("points", "Points", 60, False),
                                           ("filter", "Filter", 60, False),
                                           ("single", "Single-night Tc (BJD_TDB)", 190, False),
                                           ("status", "Status", 320, True)):
            tui._col(self.tree, col, label, width, stretch=stretch)
        for lvl, color in LEVEL_COLORS.items():
            self.tree.tag_configure(lvl, foreground=color)
        self.tree.pack(fill=X, pady=(6, 4))
        try:
            self.tree.bind("<Double-1>", lambda _e: self.toggle_selected())
        except Exception:
            pass

        eph = ttk.Frame(top, style="Card.TFrame")
        eph.pack(fill=X, pady=(2, 2))
        self.eph_text = StringVar(value="")
        ttk.Label(eph, text="Input ephemeris", style="Card.TLabel", font=(core.UI_FONT, 10, "bold")).pack(side=LEFT)
        ttk.Label(eph, textvariable=self.eph_text, style="Card.TLabel").pack(side=LEFT, padx=(6, 6))
        ttk.Button(eph, text="Look up (NASA Exoplanet Archive)", command=self.lookup).pack(side=LEFT)
        alt = ttk.Frame(top, style="Card.TFrame")
        alt.pack(fill=X, pady=(2, 2))
        ttk.Label(alt, text="Alternative ephemeris", style="Card.TLabel", font=(core.UI_FONT, 10, "bold")).pack(side=LEFT)
        self.alt_vars = {k: StringVar(value="") for k in ("t0", "t0_err", "period", "period_err", "source")}
        for key, label, width in (("t0", "T0 (BJD_TDB)", 16), ("t0_err", "±", 10), ("period", "P (d)", 14),
                                  ("period_err", "±", 11), ("source", "Source", 18)):
            ttk.Label(alt, text=label, style="Card.TLabel").pack(side=LEFT, padx=(8 if label != "±" else 2, 2))
            box = ttk.Entry(alt, textvariable=self.alt_vars[key], width=width)
            box.pack(side=LEFT)
            try:
                box.bind("<Return>", lambda _e: self.apply_alt())
            except Exception:
                pass
        ttk.Button(alt, text="Apply", command=self.apply_alt).pack(side=LEFT, padx=(8, 0))
        ttk.Button(alt, text="Clear", command=self.clear_alt).pack(side=LEFT, padx=(4, 0))
        self.alt_text = StringVar(value="Optional: e.g. Exoplanet Watch's or ExoClock's current ephemeris, typed or "
                                        "pasted; blank ± = not known.")
        ttk.Label(top, textvariable=self.alt_text, style="Hint.TLabel").pack(anchor=W)

        opts = ttk.Frame(top, style="Card.TFrame")
        opts.pack(fill=X, pady=(6, 2))
        self.detrend_airmass = BooleanVar(value=bool(page.detrend_airmass.get()) if page is not None else True)
        self.detrend_time = BooleanVar(value=bool(page.detrend_time.get()) if page is not None else False)
        ttk.Label(opts, text="Detrend", style="Card.TLabel").pack(side=LEFT)
        ttk.Checkbutton(opts, text="airmass", variable=self.detrend_airmass).pack(side=LEFT, padx=(4, 0))
        ttk.Checkbutton(opts, text="time slope", variable=self.detrend_time).pack(side=LEFT, padx=(6, 0))
        self.per_filter = BooleanVar(value=False)
        self.per_filter_check = ttk.Checkbutton(opts, text="Depth and limb darkening per filter",
                                                variable=self.per_filter)
        self.per_filter_check.pack(side=LEFT, padx=(12, 0))
        ttk.Label(opts, text="MCMC", style="Card.TLabel").pack(side=LEFT, padx=(14, 4))
        self.mcmc = StringVar(value="Quick (1,500 steps)")
        ttk.Combobox(opts, textvariable=self.mcmc, values=list(tui.MCMC_CHOICES), width=22,
                     state="readonly").pack(side=LEFT)
        ttk.Button(opts, text="Fit nights", style="Accent.TButton", command=self.run_fit).pack(side=LEFT, padx=10)
        ttk.Button(opts, text="Cancel", command=self.cancel).pack(side=LEFT)
        prog = ttk.Frame(top, style="Card.TFrame")
        prog.pack(fill=X, pady=(4, 2))
        self.bar = ttk.Progressbar(prog, length=280, mode="determinate", maximum=1)
        self.bar.pack(side=LEFT)
        self.prog_text = StringVar(value="")
        ttk.Label(prog, textvariable=self.prog_text, style="Hint.TLabel").pack(side=LEFT, padx=8)

        tcols = ("epoch", "night", "tc", "oc_in", "oc_alt", "oc_own", "flag")
        self.ttree = ttk.Treeview(top, columns=tcols, show="headings", height=3)
        for col, label, width, stretch in (("epoch", "Epoch", 60, False), ("night", "Night", 110, False),
                                           ("tc", "Tc (BJD_TDB) ± min", 190, False),
                                           ("oc_in", "O − C input (min, σ)", 170, False),
                                           ("oc_alt", "O − C alternative", 160, False),
                                           ("oc_own", "O − C own fit", 150, False),
                                           ("flag", "Flag", 80, True)):
            tui._col(self.ttree, col, label, width, stretch=stretch)
        self.ttree.tag_configure("warn", foreground="#b35c00")
        self.ttree.pack(fill=X, pady=(4, 2))
        # The plain-words reading (on time / the ephemeris is out of date / outliers) right under the table.
        self.reading = Text(top, height=3, wrap="word", font=(core.UI_FONT, 10), relief="flat", bg="#ffffff")
        for lvl, color in LEVEL_COLORS.items():
            self.reading.tag_configure(lvl, foreground=color)
        self.reading.pack(fill=X, pady=(2, 2))

        bottom = ttk.Frame(top, style="Card.TFrame")
        bottom.pack(fill=X, side="bottom", pady=(6, 0))
        ttk.Label(bottom, text="Results", style="Hint.TLabel").pack(side=LEFT, padx=(0, 6))
        ttk.Button(bottom, text="Save timing CSV…", style="Accent.TButton", command=self.save_csv).pack(side=LEFT)
        ttk.Button(bottom, text="Save plot PNG…", command=self.save_png).pack(side=LEFT, padx=6)
        ttk.Button(bottom, text="Close", command=self.close).pack(side=RIGHT)
        # Plots and the full summary (shape, the ephemerides each O − C column uses, notes) in two tabs, so the
        # window fits a 1080-line screen at 150 % scaling.
        tabs = ttk.Notebook(top)
        tabs.pack(fill=BOTH, expand=True, pady=(4, 0))
        self.tabs = tabs
        plot_tab = ttk.Frame(tabs, style="Card.TFrame")
        text_tab = ttk.Frame(tabs, style="Card.TFrame")
        tabs.add(plot_tab, text="Plots")
        tabs.add(text_tab, text="Summary and ephemerides")
        self.summary = Text(text_tab, height=10, width=60, wrap="word", font=(core.UI_FONT, 10), relief="flat",
                            bg="#ffffff")
        for lvl, color in LEVEL_COLORS.items():
            self.summary.tag_configure(lvl, foreground=color)
        self.summary.tag_configure("head", font=(core.UI_FONT, 10, "bold"))
        sscroll = ttk.Scrollbar(text_tab, orient="vertical", command=self.summary.yview)
        self.summary.configure(yscrollcommand=sscroll.set)
        sscroll.pack(side=RIGHT, fill="y")
        self.summary.pack(fill=BOTH, expand=True)
        self.fig = None
        if Figure is not None:
            self.fig = Figure(figsize=(11, 5.4), dpi=100, facecolor="#ffffff", layout="constrained")
            self.canvas = FigureCanvasTkAgg(self.fig, master=plot_tab)
            self.canvas.get_tk_widget().configure(width=520, height=300)
            self.canvas.get_tk_widget().pack(fill=BOTH, expand=True)
            self._placeholder("Add nights, then Fit nights.")
        try:
            win.protocol("WM_DELETE_WINDOW", self.close)
        except Exception:
            pass
        self._show_planet()
        self.refresh_nights()
        win.after_idle(lambda: tui.fit_to_content(win, 760, 560))
        if self.planet and not _eph_from_planet(self.planet):
            self.lookup()

    # ---- planet and ephemerides ---------------------------------------------------------------------------------
    def planet_name(self) -> str:
        if self.planet and self.planet.get("name"):
            return self.planet["name"]
        named = [n for n in self.nights if n.get("planet")]
        return named[0]["planet"] if named else ""

    def eph_in(self) -> dict | None:
        return _eph_from_planet(self.planet)

    def _show_planet(self):
        name = self.planet_name()
        self.title.set(f"Multi-night transit analysis: {name}" if name else "Multi-night transit analysis")
        e = self.eph_in()
        if e:
            te = f" ± {e['t0_err'] * 1440:.2f} min" if e.get("t0_err") is not None else " (± not given)"
            pe = f" ± {e['period_err']:.7f}" if e.get("period_err") is not None else " (± not given)"
            self.eph_text.set(f"T0 {e['t0']:.5f}{te}, P {e['period']:.7f}{pe} d — {e['source']}")
        elif self.planet and self.planet.get("period"):
            self.eph_text.set(f"P {self.planet['period']:.7f} d; no T0, so no O − C against it (Look up)")
        else:
            self.eph_text.set("none yet: Look up, or enter the planet on the Input page")

    def lookup(self):
        name = self.planet_name()
        if not name:
            messagebox.showinfo("SHOBS-P", "Add a night first (its report names the planet), or enter the planet on "
                                           "the Input page.", parent=self.win)
            return
        self.prog_text.set(f"Looking up {name} (NASA Exoplanet Archive)…")
        app = self.app

        def work():
            try:
                got = tc.lookup_planet(name)
                app.call_ui(self._looked_up, name, got, "")
            except Exception as exc:
                app.call_ui(self._looked_up, name, None, str(exc).splitlines()[0] if str(exc) else type(exc).__name__)

        threading.Thread(target=work, daemon=True).start()

    def _looked_up(self, name, got, why):
        if got and got.get("period"):
            if self.planet and norm_name(self.planet.get("name")) == norm_name(got.get("name")) \
                    and self.planet.get("period"):
                # keep the shape priors the page used; take the (most precise) ephemeris and position
                self.planet.update({k: got.get(k) for k in ("t0", "t0_err", "period", "period_err", "ra", "dec",
                                                              "reference", "source") if got.get(k) is not None})
                self.planet.pop("eph_source", None)
            else:
                self.planet = dict(got)
            self.prog_text.set(f"Ephemeris: {tc.ephemeris_source(self.planet)}.")
        else:
            self.prog_text.set(f"✖ No ephemeris found for {name}" + (f" ({why})" if why else "")
                               + "; type one as the alternative to see O − C.")
        self._show_planet()
        self.refresh_nights()

    def apply_alt(self):
        v = self.alt_vars
        if not v["t0"].get().strip() and not v["period"].get().strip():
            self.clear_alt()
            return
        try:
            self.eph_alt = parse_ephemeris(v["t0"].get(), v["t0_err"].get(), v["period"].get(), v["period_err"].get(),
                                           v["source"].get())
        except ValueError as exc:
            self.alt_text.set(f"✖ Alternative ephemeris not used: {exc}.")
            return
        e = self.eph_alt
        self.alt_text.set("✔ Alternative ephemeris: " + eph_line(e).replace("+/-", "±") + f" — {e['source']}"
                          + (f" ({e['note']})" if e.get("note") else "")
                          + ("" if e.get("t0_err") is not None else "; no T0 ± given, so its O − C σ is the fit's only"))
        self._analyse()

    def clear_alt(self):
        for var in self.alt_vars.values():
            var.set("")
        self.eph_alt = None
        self.alt_text.set("No alternative ephemeris.")
        self._analyse()

    # ---- nights -------------------------------------------------------------------------------------------------
    def _radec(self):
        p = self.planet or {}
        if p.get("ra") is not None and p.get("dec") is not None:
            return float(p["ra"]), float(p["dec"])
        info = getattr(self.app, "planet_info", None) or {}
        names = {norm_name(self.planet_name())} - {""}
        if names & ({norm_name(info.get("name")), norm_name(info.get("host"))} - {""}) and info.get("ra") is not None:
            return float(info["ra"]), float(info["dec"])
        mine = {norm_name(self.app.planet_name.get()), norm_name(self.app.star_id.get())} - {""} \
            if hasattr(self.app, "planet_name") else set()
        host = norm_name((self.planet or {}).get("host"))
        if names & mine or (host and host in mine):
            try:
                return self.app.target_radec_deg()
            except Exception:
                return None
        return None

    def add_files(self, paths=None):
        if paths is None:
            paths = filedialog.askopenfilenames(parent=self.win, title="AAVSO exoplanet reports or transit-fit CSVs",
                                                filetypes=[("Reports and fit CSVs", "*.txt *.csv"),
                                                           ("All files", "*.*")])
        for path in list(paths or []):
            if any(os.path.abspath(n.get("path", "")) == os.path.abspath(path) for n in self.nights):
                continue
            try:
                night = load_night(path, self._radec())
            except Exception as exc:
                night = {"kind": "file", "path": path, "source": os.path.basename(path), "error": str(exc),
                         "t": np.array([]), "notes": []}
            self.nights.append(night)
            if self.planet is None and night.get("planet"):
                rp = night.get("report_planet") or {"name": night["planet"]}
                self.planet = dict(rp) if rp.get("period") else None
                if self.planet is None:
                    self.planet = {"name": night["planet"]}
        self._show_planet()
        self.refresh_nights()
        if self.planet and not self.eph_in() and self.planet_name():
            self.lookup()

    def _all_nights(self) -> list:
        out = []
        r = getattr(self.page, "result", None) if self.page is not None else None
        if self.use_current.get() and r is not None:
            try:
                if (self.page.report or {}).get("date_type") == "JD_UTC":
                    raise NightError("the page's times are JD_UTC (star position unknown)")
                cur = night_from_result(r, getattr(self.page, "meta_for_files", {}), "Transit fit page")
                if tui._state(r.get("verdict")) == "none":
                    raise NightError("the page's fit found no transit")
            except Exception as exc:
                cur = {"kind": "current", "source": "Transit fit page", "error": str(exc), "t": np.array([]),
                       "notes": []}
            cur["user_off"] = getattr(self, "_current_off", False)
            out.append(cur)
        return out + self.nights

    def refresh_nights(self):
        nights = self._all_nights()
        check_nights(nights, self.planet_name(), self.eph_in())
        self._shown = nights
        self.tree.delete(*self.tree.get_children())
        self._iids = {}
        for n in nights:
            single = n.get("single_tc")
            iid = self.tree.insert("", END, tags=(n["status"][0],), values=(
                "✔" if n.get("use") else "—", night_label(n) if len(n.get("t", [])) else "",
                n.get("source", ""), len(n.get("t", [])) or "", n.get("filter", ""),
                f"{single[0]:.5f} ± {single[1] * 1440:.2f} min" if single else "", n["status"][1]))
            self._iids[iid] = n
        try:
            self.tree.configure(height=min(max(len(nights), 3), 8))
        except Exception:
            pass
        filters = {str(n.get("filter") or "").upper() for n in nights if n.get("use")}
        try:
            if len(filters) > 1:
                self.per_filter_check.state(["!disabled"])
            else:
                self.per_filter_check.state(["disabled"])
        except Exception:
            pass
        n_use = sum(1 for n in nights if n.get("use"))
        if not self._running:
            self.prog_text.set(f"{n_use} night{'s' if n_use != 1 else ''} ready" + (": add at least two to fit."
                                                                                     if n_use < 2 else "."))

    def _selected(self) -> list:
        try:
            return [self._iids[i] for i in self.tree.selection() if i in self._iids]
        except Exception:
            return []

    def toggle_selected(self):
        for n in self._selected():
            if n.get("kind") == "current":
                self._current_off = not getattr(self, "_current_off", False)
            else:
                n["user_off"] = not n.get("user_off", False)
        self.refresh_nights()

    def remove_selected(self):
        sel = self._selected()
        for n in sel:
            if n.get("kind") == "current":
                self.use_current.set(False)
            elif n in self.nights:
                self.nights.remove(n)
        self.refresh_nights()

    # ---- fit ----------------------------------------------------------------------------------------------------
    def run_fit(self):
        if self._running:
            return
        nights = self._all_nights()
        check_nights(nights, self.planet_name(), self.eph_in())
        use = [n for n in nights if n.get("use")]
        if len(use) < 2:
            messagebox.showinfo("SHOBS-P", "A joint fit needs at least two usable nights of the same planet "
                                           "(see the Status column).", parent=self.win)
            return
        if not self.planet or not self.planet.get("period"):
            messagebox.showinfo("SHOBS-P", "The planet's period is not known yet: press Look up first.",
                                parent=self.win)
            return
        if not self.app._start_job("Multi-night transit fit…"):
            return
        self._running = True
        token = object()
        self._token = token
        steps = tui.MCMC_CHOICES.get(self.mcmc.get(), DEFAULT_STEPS)
        da, dt, pf = bool(self.detrend_airmass.get()), bool(self.detrend_time.get()), bool(self.per_filter.get())
        filt = _majority_filter(use)
        planet = dict(self.planet, ld_band=filt)
        cancel = self.app._cancel
        app = self.app
        self.bar.configure(value=0, maximum=len(use))
        self.prog_text.set(f"Fitting each night alone (1 of {len(use)})…")
        self._placeholder(f"Fitting {len(use)} nights together…")

        def progress(phase, done, total):
            app.call_ui(self._progress, token, phase, done, total)

        def work():
            try:
                fit = joint_fit(use, planet, da, dt, pf, steps, progress=progress, cancel=cancel,
                                log=lambda m: app.call_ui(app.log, m))
                app.call_ui(self._done, token, fit, None)
            except Exception as exc:
                app.call_ui(self._done, token, None, (str(exc), traceback.format_exc()))

        threading.Thread(target=work, daemon=True).start()

    def _progress(self, token, phase, done, total):
        if token is not self._token:
            return
        if phase == "joint":
            self.bar.configure(mode="indeterminate")
            try:
                self.bar.start(60)
            except Exception:
                pass
            self.prog_text.set("Joint least-squares fit (a few seconds)…")
            return
        try:
            self.bar.stop()
        except Exception:
            pass
        self.bar.configure(mode="determinate", maximum=total, value=done)
        label = {"prep": "Fitting each night alone", "burn-in": "MCMC burn-in", "step": "MCMC step"}.get(phase, phase)
        self.prog_text.set(f"{label} {done:,} of {total:,}")

    def cancel(self):
        if self._running:
            self.app.cancel_job()

    def _done(self, token, fit, error):
        if token is not self._token:
            return
        self._running = False
        self.app._end_job()
        try:
            self.bar.stop()
            self.bar.configure(mode="determinate")
        except Exception:
            pass
        if error is not None:
            if error[0] == "Canceled":
                self.prog_text.set("Canceled.")
                self._placeholder("Canceled: Fit nights runs it again.")
            else:
                self.prog_text.set("✖ Fit failed: " + error[0])
                self.app.log(error[1])
                self._placeholder("The joint fit failed: " + error[0])
            return
        self.fit = fit
        self.prog_text.set(f"Done: {len(fit['nights'])} nights, MCMC acceptance {fit['chain']['acceptance']:.2f}.")
        self.app.status.set("Multi-night transit fit done.")
        self._analyse()

    def _analyse(self):
        if not self.fit:
            return
        self.analysis = timing_analysis(self.fit["nights"], self.eph_in(), self.eph_alt, self.fit["period"])
        self._fill_results()
        self._draw()

    # ---- display ------------------------------------------------------------------------------------------------
    def _fill_results(self):
        a, fit = self.analysis, self.fit
        self.ttree.delete(*self.ttree.get_children())
        flags = outlier_flags(a)
        nights = [fit["nights"][i] for i in a["order"]]

        def cell(block, i):
            if not block:
                return ""
            r = block["rows"][i]
            ns = f" ({r['nsigma']:+.1f}σ)" if math.isfinite(r["nsigma"]) else ""
            return f"{r['oc_min']:+.2f} ± {r['sigma_min']:.2f}{ns}"
        for i, n in enumerate(nights):
            self.ttree.insert("", END, tags=(("warn",) if flags[i] else ()), values=(
                int(a["epochs"][i]), n["label"], f"{a['tc'][i]:.5f} ± {a['sig'][i] * 1440:.2f}",
                cell(a.get("input"), i), cell(a.get("alt"), i), cell(a.get("own"), i), flags[i]))
        try:
            self.ttree.configure(height=min(max(len(nights), 3), 8))
        except Exception:
            pass
        t = self.summary
        t.configure(state="normal")
        t.delete("1.0", END)
        for lvl, text in summary_lines(fit, a):
            t.insert(END, text + "\n", lvl)
        t.configure(state="disabled")
        t = self.reading
        t.configure(state="normal")
        t.delete("1.0", END)
        st = list(a.get("statements") or [])
        for lvl, text in st:
            t.insert(END, text + "\n", lvl)
        t.configure(state="disabled", height=min(max(len(st) + 1, 2), 6))

    def _placeholder(self, text):
        if self.fig is None:
            return
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.axis("off")
        ax.text(0.5, 0.5, text, ha="center", va="center", fontsize=11, color="#7a8a94", transform=ax.transAxes)
        try:
            self.canvas.draw_idle()
        except Exception:
            pass

    def _draw(self):
        if self.fig is None or not self.fit:
            return
        draw(self.fig, self.fit, self.analysis, self.planet_name())
        try:
            self.canvas.draw_idle()
        except Exception:
            pass

    # ---- files --------------------------------------------------------------------------------------------------
    def _stem(self) -> str:
        nights = [self.fit["nights"][i] for i in self.analysis["order"]]
        first, last = nights[0]["label"], nights[-1]["label"]
        return f"{tui._file_planet(self.planet_name())}_{first}_to_{last}_SHOBS-P_multinight"

    def save_csv(self):
        if not self.fit:
            messagebox.showinfo("SHOBS-P", "Fit the nights first.", parent=self.win)
            return
        path = self.app.output_path(self._stem() + "_timings.csv", title="Save multi-night timing CSV",
                                    defaultextension=".csv", filetypes=[("CSV", "*.csv")], parent=self.win)
        if not path:
            return
        try:
            n = write_timing_csv(path, self.fit, self.analysis, {"version": getattr(self.app, "app_version", "")})
        except Exception as exc:
            messagebox.showerror("SHOBS-P", f"Could not save the CSV: {exc}", parent=self.win)
            return
        self.app.log(f"Multi-night timing CSV: {path} ({n} epochs)")
        self.app.saved_notice(path, "Per-epoch mid-times (BJD_TDB) with the ephemerides in the header.")

    def save_png(self):
        if not self.fit or self.fig is None:
            messagebox.showinfo("SHOBS-P", "Fit the nights first.", parent=self.win)
            return
        path = self.app.output_path(self._stem() + ".png", title="Save multi-night plot PNG",
                                    defaultextension=".png", filetypes=[("PNG", "*.png")], parent=self.win)
        if not path:
            return
        try:
            save_figure(self.fig, path, self.fit, self.analysis, self.planet_name(),
                        getattr(self.app, "app_version", ""))
        except Exception as exc:
            messagebox.showerror("SHOBS-P", f"Could not save the PNG: {exc}", parent=self.win)
            return
        self._draw()
        self.app.log(f"Multi-night plot PNG: {path}")
        self.app.saved_notice(path)

    def close(self):
        if self._running:
            self.app.cancel_job()
        self._token = None
        if self._running:
            self._running = False
            self.app._end_job()
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.page is not None and getattr(self.page, "_multinight", None) is self:
            self.page._multinight = None


def save_figure(fig, path: str, fit: dict, a: dict, name: str, version: str = "") -> None:
    """The plot at a fixed 13 × 6.5 in, 130 dpi, with a DTG stamp, whatever its size on screen."""
    old = fig.get_size_inches().copy()
    draw(fig, fit, a, name)
    mark = None
    try:
        fig.set_size_inches(13, 6.5, forward=False)
        mark = fig.text(0.995, 0.002, f"Created {core.dtg()} · SHOBS-P {version}", fontsize=6, color="#7a8a94",
                        ha="right", va="bottom")
        fig.savefig(path, dpi=130)
    finally:
        if mark is not None:
            mark.remove()
        fig.set_size_inches(old, forward=False)


def summary_lines(fit: dict, a: dict) -> list:
    """(level, text) lines for the window's summary: the shape, the ephemerides (which O − C column uses which), the
    plain-words reading, and per-night notes."""
    out = [("head", "Joint shape (MCMC median ± 68 %)")]
    sh = fit["shape"]
    for g in fit["groups"]:
        s = sh[g]
        out.append(("info", f"{'' if g == 'all' else g + ': '}Rp/R* {s['k'][0]:.4f} ± {s['k'][1]:.4f}, depth "
                            f"{s['depth'][0] * 100:.2f} ± {s['depth'][1] * 100:.2f} %, T14 {s['t14'][0] * 24:.3f} ± "
                            f"{s['t14'][1] * 24:.3f} h"))
    out.append(("info", f"a/R* {sh['a_rs'][0]:.2f} ± {sh['a_rs'][1]:.2f}, b {sh['b'][0]:.3f} ± {sh['b'][1]:.3f}, "
                        f"inc {sh['inc'][0]:.2f} ± {sh['inc'][1]:.2f}°; χ²ν {fit['rchi2']:.2f} over {fit['n_data']} "
                        f"points; detrending {', '.join(fit['detrend']) or 'none'}"))
    out.append(("head", "Ephemerides (O − C σ = Tc error and the ephemeris's propagated error in quadrature)"))
    if a.get("input"):
        e = a["input"]["eph"]
        out.append(("info", "O − C input: " + eph_line(e).replace("+/-", "±") + f" — {e.get('source', '')}"))
    else:
        out.append(("warn", "⚠ No input ephemeris (no T0): epochs are counted from the first night; Look up."))
    if a.get("alt"):
        e = a["alt"]["eph"]
        out.append(("info", "O − C alternative: " + eph_line(e).replace("+/-", "±") + f" — {e.get('source', '')} "
                            "(its own epoch count)"))
    if a.get("own"):
        out.append(("info", "O − C own fit (Tc error only): updated ephemeris " + lin_line(a["own"]["eph"])
                    .replace("+/-", "±")))
    if a.get("combined"):
        out.append(("info", "With the input T0 and P as measurements: " + lin_line(a["combined"]).replace("+/-", "±")))
    out.append(("head", "Reading"))
    out += list(a.get("statements") or [])
    notes = []
    for n in fit["nights"]:
        bits = []
        if n["error_scale"] > 1.0001:
            bits.append(f"error bars ×{n['error_scale']:.2f} (χ²ν {n['rchi2_raw']:.2f}, β {n['beta']:.2f})")
        if n.get("time_note"):
            bits.append(n["time_note"])
        if not n.get("airmass") and "airmass" in fit["detrend"]:
            bits.append("no airmass in the file")
        if n.get("single_tc"):
            d = (n["tc"][0] - n["single_tc"][0]) * 86400
            bits.append(f"joint Tc {d:+.0f} s from the single-night fit")
        if bits:
            notes.append(("info", f"{n['label']}: " + "; ".join(bits)))
    if notes:
        out.append(("head", "Nights"))
        out += notes
    return out
