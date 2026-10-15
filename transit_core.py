"""Transit tools for SHOBS-P (2.1): model, fit (least squares + MCMC), red noise, checks, ephemeris lookups,
planner, nearby-eclipsing-binary arithmetic, and report files.

NumPy only, plus SciPy's least_squares for the starting fit. No Astropy here, so everything in this
module also runs on a report loaded from disk (an EXOTIC / AAVSO exoplanet file) without frames.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone

import numpy as np

# ---- Transit model ------------------------------------------------------------------------------
# Limb-darkened transit by direct integration over the stellar disk: the star is cut into thin
# annuli (denser near the limb, where the intensity changes fastest), and for each annulus the
# fraction of its circumference covered by the planet is exact. This gives the Mandel & Agol (2002)
# light curve for any limb-darkening law, including grazing transits, to better than 1e-5 in flux.

_NR = 200
_theta_edges = np.linspace(0.0, math.pi / 2, _NR + 1)
_RE = np.sin(_theta_edges)                     # annulus edges, dense near the limb
_RM = 0.5 * (_RE[1:] + _RE[:-1])


def _overlap_area(r, z, k):
    """Area of overlap of a disk of radius r (centered on the star) with the planet disk (radius k, at z)."""
    r = np.asarray(r, dtype=float)
    z = np.asarray(z, dtype=float)
    out = np.zeros(np.broadcast(r, z).shape)
    r_b, z_b = np.broadcast_arrays(r, z)
    inside = z_b <= np.abs(r_b - k)
    out[inside] = math.pi * np.minimum(r_b[inside], k) ** 2
    part = (~inside) & (z_b < r_b + k) & (r_b > 0)
    if part.any():
        rr, zz = r_b[part], z_b[part]
        c1 = np.clip((zz * zz + rr * rr - k * k) / (2 * zz * rr), -1, 1)
        c2 = np.clip((zz * zz + k * k - rr * rr) / (2 * zz * k), -1, 1)
        sq = np.clip((-zz + rr + k) * (zz + rr - k) * (zz - rr + k) * (zz + rr + k), 0, None)
        out[part] = rr * rr * np.arccos(c1) + k * k * np.arccos(c2) - 0.5 * np.sqrt(sq)
    return out


def occultation(z: np.ndarray, k: float, u1: float, u2: float) -> np.ndarray:
    """Relative flux for planet-star separation z (in stellar radii), radius ratio k, quadratic limb darkening.

    The star is cut into 200 annuli (denser near the limb); the planet's overlap with each is the exact
    circle-overlap area, so only the smooth limb-darkening profile is approximated (Mandel & Agol 2002
    light curve to about 1e-5 in flux, including grazing transits)."""
    z = np.asarray(z, dtype=float)
    out = np.ones_like(z)
    hit = z < 1.0 + k
    if not hit.any() or k <= 0:
        return out
    mu = np.sqrt(np.clip(1.0 - _RM * _RM, 0.0, 1.0))
    intensity = 1.0 - u1 * (1.0 - mu) - u2 * (1.0 - mu) ** 2
    total = np.sum(intensity * math.pi * (_RE[1:] ** 2 - _RE[:-1] ** 2))
    area = _overlap_area(_RE[None, :], z[hit][:, None], k)
    out[hit] = 1.0 - np.sum(intensity * (area[:, 1:] - area[:, :-1]), axis=1) / total
    return out


def separation(t: np.ndarray, tc: float, period: float, a_rs: float, b: float) -> np.ndarray:
    """Sky-projected separation in stellar radii for a circular orbit."""
    cos_i = min(max(b / a_rs, 0.0), 1.0)
    phase = 2.0 * math.pi * (np.asarray(t, dtype=float) - tc) / period
    z = a_rs * np.sqrt(np.sin(phase) ** 2 + (cos_i * np.cos(phase)) ** 2)
    return np.where(np.cos(phase) > 0, z, 1e3)   # the planet behind the star blocks nothing


def kipping_to_u(q1: float, q2: float) -> tuple[float, float]:
    """Kipping (2013) q1, q2 in [0, 1] -> quadratic u1, u2 (always a physical intensity profile)."""
    s = math.sqrt(max(q1, 0.0))
    return 2.0 * s * q2, s * (1.0 - 2.0 * q2)


def u_to_kipping(u1: float, u2: float) -> tuple[float, float]:
    q1 = (u1 + u2) ** 2
    q2 = u1 / (2.0 * (u1 + u2)) if (u1 + u2) > 0 else 0.5
    return min(max(q1, 1e-4), 0.9999), min(max(q2, 1e-4), 0.9999)


def total_duration(period: float, a_rs: float, b: float, k: float) -> float:
    """First to fourth contact (days), circular orbit (Seager & Mallen-Ornelas 2003)."""
    inc = math.acos(min(max(b / a_rs, 0.0), 0.9999))
    arg = math.sqrt(max((1.0 + k) ** 2 - b * b, 0.0)) / (a_rs * math.sin(inc))
    return period / math.pi * math.asin(min(arg, 1.0))


def limb_darkening_guess(teff: float | None) -> tuple[float, float]:
    """Approximate quadratic limb darkening for a broad green / V / clear band, by temperature.

    Rough values in the range of the Claret (2000) V-band tables for dwarfs (log g about 4.5). They are
    used only as the centers of broad priors (0.1 wide), so the light curve, not this table, decides."""
    table = ((3500, 0.75, 0.05), (4500, 0.70, 0.08), (5000, 0.60, 0.15), (5500, 0.50, 0.22),
             (6000, 0.43, 0.27), (6500, 0.36, 0.30), (7000, 0.30, 0.33), (8000, 0.25, 0.33))
    if teff is None or not math.isfinite(teff):
        teff = 5800.0
    ts = [row[0] for row in table]
    return float(np.interp(teff, ts, [row[1] for row in table])), float(np.interp(teff, ts, [row[2] for row in table]))


# ---- Parameters and priors ----------------------------------------------------------------------

PARAMS = ("tc", "k", "a_rs", "b", "q1", "q2", "c0", "c_am", "c_t", "step")
PARAM_LABELS = {"tc": "Mid-transit Tc (BJD_TDB)", "k": "Rp/R*", "a_rs": "a/R*", "b": "Impact parameter b",
                "q1": "q1 (limb darkening)", "q2": "q2 (limb darkening)", "c0": "Baseline",
                "c_am": "Airmass slope", "c_t": "Time slope (per day)", "step": "Flip step"}

# ---- 2.2.13: a meridian flip inside the data -----------------------------------------------------
# After a flip every star sits on another part of the sensor and the flat, so the target/comp ratio can jump by a
# few percent. The fit then gets ONE extra free parameter: a multiplicative baseline step that starts at the flip.
# The flip time comes from the data (the frames' flip segment, or for a report the flip SHOBS-P wrote into it, or
# the largest gap when the user says the report has a flip); it is never fitted. The step is fitted jointly with Tc,
# Rp/R* and the rest, in the least squares and in the MCMC, so its uncertainty is part of the depth's.
# (AstroImageJ instead removes a fixed meridian-flip offset from the baseline before the transit fit, Collins et al.
# 2017; TRAPPIST normalizes each side of the flip separately. Fitting the step together with the transit lets the
# MCMC show how much the depth and the step trade off.)
# 2.2.13: OFF until the validation passes in full (see flipval2213.py and the release notes): the step model is
# validated on synthetic nights and on KOI-217 b, but a TrES-3 b night with a 55-minute cut at the bottom (only 16 min
# of baseline after the cut) did not reproduce the untouched Tc (3.8 sigma). fit_transit(flip_step=True) still runs
# it for tests.
FLIP_STEP_ENABLED = False
FLIP_MIN_OOT_MIN = 30.0       # out-of-transit baseline needed on EACH side of the flip to trust the depth (minutes)
FLIP_MIN_OOT_FRAMES = 15      # ... and at least this many out-of-transit frames on each side
FLIP_CORR_WARN = 0.9          # |corr(step, depth)| above this: the night cannot separate the two
FLIP_GAP_MIN_MIN = 20.0       # a report's largest gap counts as the flip only if at least this long (and 5 cadences)
FLAGGED_ERR_FACTOR = 2.0      # 2.2.13: flagged (cloud) frames put back into a refit have their error bars doubled
# When one side of the flip has no out-of-transit baseline of its own: "inconclusive" (timing not trusted either) or
# "depth" (the original plan: timing usable, depth unreliable). Validation (flipval2213.py) found the timing biased
# too in that case: with the flip gap over ingress and no baseline after the flip, 2 of 6 detected synthetic nights
# had Tc off by more than 2 sigma, and a real TrES-3 b night cut that way moved Tc by 3.8 sigma.
FLIP_NO_BASELINE_VERDICT = "inconclusive"


class TransitProblem:
    """Data, fixed values, and priors for one transit light curve.

    Model: flux = transit(t) * (c0 + c_am * (airmass - mean) + c_t * (t - t_mid)) * (1 + step * [t >= t_flip]).
    The step term exists only when after (points after a meridian flip) is given with points on both sides.
    priors: {name: (mean, sigma)} Gaussian priors. Bounds keep everything physical."""

    def __init__(self, t, flux, err, airmass, period, priors: dict, free: tuple, start: dict,
                 detrend_airmass=True, detrend_time=False, ld_prior=None, t_flip=None):
        self.t = np.asarray(t, dtype=float)
        self.flux = np.asarray(flux, dtype=float)
        self.err = np.asarray(err, dtype=float)
        am = np.asarray(airmass, dtype=float) if airmass is not None else np.full_like(self.t, np.nan)
        self.has_airmass = bool(np.isfinite(am).all()) and np.ptp(am) > 1e-4
        self.am = am - (np.nanmean(am) if self.has_airmass else 0.0)
        if not self.has_airmass:
            self.am = np.zeros_like(self.t)
        self.t_mid = float(np.mean(self.t))
        self.period = float(period)
        self.priors = dict(priors)
        self.ld_prior = ld_prior          # (u1, u2, sigma) or None
        names = [p for p in free if p in PARAMS]
        if not (detrend_airmass and self.has_airmass):
            names = [p for p in names if p != "c_am"]
        if not detrend_time:
            names = [p for p in names if p != "c_t"]
        # 2.2.13: the flip step, only with points on both sides of the flip.
        self.t_flip = None
        if t_flip is not None and (self.t < t_flip).any() and (self.t >= t_flip).any():
            self.t_flip = float(t_flip)
        self.after = (self.t >= self.t_flip).astype(float) if self.t_flip is not None else None
        if self.t_flip is None:
            names = [p for p in names if p != "step"]
        self.free = names
        self.fixed = {p: float(start.get(p, 0.0)) for p in PARAMS}
        self.fixed.setdefault("c0", 1.0)
        if self.t_flip is None:
            self.fixed["step"] = 0.0

    def full(self, x) -> dict:
        p = dict(self.fixed)
        for name, value in zip(self.free, x):
            p[name] = float(value)
        return p

    def model(self, p: dict, t=None, am=None) -> np.ndarray:
        t = self.t if t is None else np.asarray(t, dtype=float)
        am = self.am if am is None else am
        u1, u2 = kipping_to_u(p["q1"], p["q2"])
        z = separation(t, p["tc"], self.period, p["a_rs"], p["b"])
        return occultation(z, p["k"], u1, u2) * self.baseline(p, t, am)

    def baseline(self, p: dict, t=None, am=None) -> np.ndarray:
        own = t is None
        t = self.t if t is None else np.asarray(t, dtype=float)
        am = self.am if am is None else am
        base = p["c0"] + p["c_am"] * am + p["c_t"] * (t - self.t_mid)
        if self.t_flip is not None:
            after = self.after if own else (t >= self.t_flip).astype(float)
            base = base * (1.0 + p.get("step", 0.0) * after)
        return base

    def in_bounds(self, p: dict) -> bool:
        if not (0.005 < p["k"] < 0.6 and 1.2 < p["a_rs"] < 200 and 0.0 <= p["b"] < 1.0 + p["k"]):
            return False
        if abs(p.get("step", 0.0)) >= 0.5:
            return False
        if not (0.0 < p["q1"] < 1.0 and 0.0 < p["q2"] < 1.0):
            return False
        if not (self.t.min() - 0.2 < p["tc"] < self.t.max() + 0.2):
            return False
        return p["b"] < p["a_rs"]

    def log_prior(self, p: dict) -> float:
        if not self.in_bounds(p):
            return -np.inf
        lp = 0.0
        for name, (mean, sigma) in self.priors.items():
            if name in p and sigma and sigma > 0:
                lp -= 0.5 * ((p[name] - mean) / sigma) ** 2
        if self.ld_prior is not None and ("q1" in self.free or "q2" in self.free):
            u1, u2 = kipping_to_u(p["q1"], p["q2"])
            m1, m2, s = self.ld_prior
            lp -= 0.5 * (((u1 - m1) / s) ** 2 + ((u2 - m2) / s) ** 2)
        return lp

    def log_prob(self, x) -> float:
        p = self.full(x)
        lp = self.log_prior(p)
        if not np.isfinite(lp):
            return -np.inf
        r = (self.flux - self.model(p)) / self.err
        return lp - 0.5 * float(np.dot(r, r))

    def residual_vector(self, x) -> np.ndarray:
        p = self.full(x)
        r = (self.flux - self.model(p)) / self.err
        pri = []
        for name, (mean, sigma) in self.priors.items():
            if name in self.free and sigma and sigma > 0:
                pri.append((p[name] - mean) / sigma)
        if self.ld_prior is not None and "q1" in self.free:
            u1, u2 = kipping_to_u(p["q1"], p["q2"])
            m1, m2, s = self.ld_prior
            pri += [(u1 - m1) / s, (u2 - m2) / s]
        return np.concatenate([r, np.asarray(pri, dtype=float)])

    def scales(self) -> np.ndarray:
        s = {"tc": 1e-3, "k": 0.01, "a_rs": 0.3, "b": 0.03, "q1": 0.05, "q2": 0.05, "c0": 1e-3, "c_am": 1e-3,
             "c_t": 1e-3, "step": 1e-3}
        return np.array([s[p] for p in self.free])

    def bounds(self):
        lo = {"tc": self.t.min() - 0.2, "k": 0.006, "a_rs": 1.3, "b": 0.0, "q1": 1e-4, "q2": 1e-4, "c0": 0.5,
              "c_am": -1.0, "c_t": -1.0, "step": -0.49}
        hi = {"tc": self.t.max() + 0.2, "k": 0.59, "a_rs": 199.0, "b": 1.5, "q1": 0.9999, "q2": 0.9999, "c0": 1.5,
              "c_am": 1.0, "c_t": 1.0, "step": 0.49}
        return [lo[p] for p in self.free], [hi[p] for p in self.free]


def best_fit(problem: TransitProblem, starts: list[dict] | None = None):
    """Least-squares fit (with the Gaussian priors as extra residuals), from a few starting points.
    Returns (x, chi2 of the light curve alone)."""
    from scipy.optimize import least_squares

    lo, hi = problem.bounds()
    base = [problem.fixed[p] for p in problem.free]
    trials = [base]
    for extra in starts or []:
        trial = list(base)
        for name, value in extra.items():
            if name in problem.free:
                trial[problem.free.index(name)] = value
        trials.append(trial)
    best = None
    for trial in trials:
        x0 = np.clip(np.asarray(trial, dtype=float), np.asarray(lo) + 1e-9, np.asarray(hi) - 1e-9)
        try:
            sol = least_squares(problem.residual_vector, x0, bounds=(lo, hi), x_scale=problem.scales(), max_nfev=4000)
        except Exception:
            continue
        p = problem.full(sol.x)
        if p["b"] >= 1.0 + p["k"]:
            continue
        if best is None or sol.cost < best.cost:
            best = sol
    if best is None:
        raise ValueError("the least-squares fit did not converge")
    problem.last_jac = best.jac     # 2.2.13: for least-squares uncertainties (lsq_errors)
    p = problem.full(best.x)
    r = (problem.flux - problem.model(p)) / problem.err
    return best.x, float(np.dot(r, r))


def lsq_errors(problem: TransitProblem) -> np.ndarray:
    """2.2.13: 1-sigma errors of the free parameters from the last best_fit's Jacobian (Gaussian approximation;
    the priors are part of the residuals, so they are included). NaN where the matrix cannot be inverted."""
    jac = getattr(problem, "last_jac", None)
    if jac is None:
        return np.full(len(problem.free), np.nan)
    try:
        cov = np.linalg.pinv(np.asarray(jac).T @ np.asarray(jac))
        return np.sqrt(np.clip(np.diag(cov), 0, None))
    except Exception:
        return np.full(len(problem.free), np.nan)


def binned_rms(t, resid, minutes: float = 10.0, min_bins: int = 3):
    """2.2.14 (GitHub #5): rms of the residuals averaged in fixed time bins (as AstroImageJ's "RMS 10-min bins").
    Bins start at the first point; a bin counts with at least 2 points. None when fewer than min_bins bins (or when the
    cadence is longer than the bin, so binning would not average anything)."""
    t = np.asarray(t, dtype=float)
    r = np.asarray(resid, dtype=float)
    ok = np.isfinite(t) & np.isfinite(r)
    t, r = t[ok], r[ok]
    if t.size < 2 * min_bins:
        return None
    idx = np.floor((t - t.min()) * 1440.0 / minutes).astype(int)
    means = [float(np.mean(r[idx == k])) for k in np.unique(idx) if np.count_nonzero(idx == k) >= 2]
    if len(means) < min_bins:
        return None
    return float(np.std(means, ddof=1))   # ddof=1: few bins on a short run would read ~15% low


def red_noise_beta(t, resid, cadence_days: float, max_minutes: float = 30.0) -> float:
    """Time-averaging beta (Pont et al. 2006; Winn et al. 2008): how much faster than white noise the
    residuals average down. 1 means white; 2 means binned errors are twice what white noise predicts."""
    resid = np.asarray(resid, dtype=float)
    n_tot = len(resid)
    sigma1 = np.std(resid)
    if n_tot < 20 or sigma1 <= 0:
        return 1.0
    betas = []
    max_n = max(2, int(max_minutes / 1440.0 / max(cadence_days, 1e-6)))
    for n in range(2, min(max_n, n_tot // 4) + 1):
        m = n_tot // n
        if m < 4:
            break
        means = resid[: m * n].reshape(m, n).mean(axis=1)
        expected = sigma1 / math.sqrt(n) * math.sqrt(m / (m - 1.0))
        betas.append(np.std(means) / expected)
    return float(max(1.0, np.median(betas))) if betas else 1.0


def run_mcmc(problem: TransitProblem, x0, n_walkers: int = 0, n_burn: int = 1000, n_steps: int = 2000,
             seed: int = 7, progress=None, cancel=None) -> dict:
    """Affine-invariant ensemble sampler (Goodman & Weare 2010, the stretch move used by emcee)."""
    rng = np.random.default_rng(seed)
    ndim = len(x0)
    n_walkers = max(n_walkers, 2 * ndim + 2)
    n_walkers += n_walkers % 2
    scale = problem.scales() * 0.1
    if not np.isfinite(problem.log_prob(x0)):
        raise ValueError("the starting fit is outside the priors or bounds")
    walkers = []
    tries = 0
    while len(walkers) < n_walkers:
        tries += 1
        if tries > 200 * n_walkers:
            raise ValueError("could not start the MCMC walkers around the best fit")
        trial = x0 + scale * rng.normal(size=ndim)
        if np.isfinite(problem.log_prob(trial)):
            walkers.append(trial)
    pos = np.array(walkers)
    lp = np.array([problem.log_prob(w) for w in pos])
    a = 2.0
    total = n_burn + n_steps
    chain = np.empty((n_steps, n_walkers, ndim))
    accepted = 0
    half = n_walkers // 2
    for step in range(total):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Cancelled")
        for first, second in ((slice(0, half), slice(half, n_walkers)), (slice(half, n_walkers), slice(0, half))):
            idx = np.arange(n_walkers)[first]
            others = pos[second]
            zs = ((a - 1.0) * rng.random(len(idx)) + 1.0) ** 2 / a
            partners = others[rng.integers(0, len(others), len(idx))]
            for j, w in enumerate(idx):
                proposal = partners[j] + zs[j] * (pos[w] - partners[j])
                lp_new = problem.log_prob(proposal)
                if np.log(rng.random()) < (ndim - 1) * np.log(zs[j]) + lp_new - lp[w]:
                    pos[w], lp[w] = proposal, lp_new
                    if step >= n_burn:
                        accepted += 1
        if step >= n_burn:
            chain[step - n_burn] = pos
        if progress is not None and (step % 25 == 0 or step in (n_burn - 1, total - 1)):
            # 2.2.12: the two phases are counted separately (burn-in, then the steps the menu chose).
            if step < n_burn:
                progress("burn-in", step + 1, n_burn)
            else:
                progress("step", step + 1 - n_burn, n_steps)
    flat = chain.reshape(-1, ndim)
    return {"flat": flat, "acceptance": accepted / float(n_steps * n_walkers), "walkers": n_walkers,
            "steps": n_steps, "burn": n_burn}


def summarize(problem: TransitProblem, flat: np.ndarray) -> dict:
    """Median and 68% interval for every free parameter and the derived ones."""
    out = {}
    for i, name in enumerate(problem.free):
        lo, med, hi = np.percentile(flat[:, i], [15.865, 50, 84.135])
        out[name] = (float(med), float((hi - lo) / 2.0))
    derived = {"depth": [], "inc": [], "t14": [], "u1": [], "u2": []}
    pick = flat[np.random.default_rng(3).integers(0, len(flat), min(len(flat), 4000))]
    for x in pick:
        p = problem.full(x)
        derived["depth"].append(p["k"] ** 2)
        derived["inc"].append(math.degrees(math.acos(min(p["b"] / p["a_rs"], 1.0))))
        derived["t14"].append(total_duration(problem.period, p["a_rs"], p["b"], p["k"]))
        u1, u2 = kipping_to_u(p["q1"], p["q2"])
        derived["u1"].append(u1)
        derived["u2"].append(u2)
    for name, values in derived.items():
        lo, med, hi = np.percentile(values, [15.865, 50, 84.135])
        out[name] = (float(med), float((hi - lo) / 2.0))
    return out


# ---- Whole fit, with checks ---------------------------------------------------------------------

def prepare_priors(planet: dict, a_start: float | None = None) -> dict:
    """Gaussian priors from the planet's published values, only for values that were measured.

    Sigmas are floored so a tight catalog value still lets the data speak a little: a/R* at least 3%, b at least
    0.04. An a/R* derived from the duration (no published value) gets a loose 25% prior; a missing b gets none."""
    priors = {}
    a_rs, b = planet.get("a_rs"), planet.get("b")
    if a_rs and not planet.get("a_rs_derived"):
        priors["a_rs"] = (a_rs, max(planet.get("a_rs_err") or 0.0, 0.03 * a_rs))
    elif a_start:
        priors["a_rs"] = (a_start, 0.25 * a_start)
    if b is not None and not planet.get("b_assumed"):
        priors["b"] = (b, max(planet.get("b_err") or 0.0, 0.04))
    return priors


def transit_window_check(t_min: float, t_max: float, planet: dict) -> dict | None:
    """2.2.9: does a predicted transit (± T14/2 and 3x the ephemeris uncertainty) overlap the data's time span?
    Returns {"overlap": bool, "tc": nearest predicted mid-transit, "hours": hours from the data to it (negative =
    before the data), "epoch": n}, or None when the planet has no usable ephemeris."""
    try:
        period = float(planet["period"])
        t0 = float(planet["t0"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(period) and period > 0 and math.isfinite(t0)):
        return None
    if not (math.isfinite(t_min) and math.isfinite(t_max)):
        return None
    t14 = float(planet.get("t14") or 0.1)
    mid = 0.5 * (t_min + t_max)
    best = None
    for n in (math.floor((mid - t0) / period), math.ceil((mid - t0) / period)):
        tc = t0 + n * period
        unc = math.hypot(float(planet.get("t0_err") or 0.0), abs(n) * float(planet.get("period_err") or 0.0))
        half = t14 / 2 + 3 * unc
        overlap = (tc + half) >= t_min and (tc - half) <= t_max
        dist = 0.0 if overlap else min(abs(tc - half - t_max), abs(tc + half - t_min))
        cand = {"overlap": overlap, "tc": tc, "epoch": n,
                "hours": 0.0 if overlap else ((tc - t_max) * 24 if tc > t_max else (tc - t_min) * 24), "_d": dist}
        if best is None or (cand["overlap"] and not best["overlap"]) or (cand["overlap"] == best["overlap"]
                                                                          and cand["_d"] < best["_d"]):
            best = cand
    best.pop("_d", None)
    return best


def transit_windows(t_min: float, t_max: float, planet: dict, pad: float = 0.0) -> list[dict]:
    """2.2.15 (item 1): every predicted transit of the planet that overlaps [t_min, t_max], with the same half-width
    as transit_window_check (T14/2 + 3x the propagated ephemeris uncertainty) plus `pad` days on each side (JD
    against BJD: pad about 15 minutes). Each: {"lo", "hi" (window incl. the pad), "tc", "epoch", "t1", "t4" (the
    transit itself, tc -/+ T14/2), "unc" (1 sigma of tc, days)}. Empty without a usable ephemeris."""
    try:
        period = float(planet["period"])
        t0 = float(planet["t0"])
    except (KeyError, TypeError, ValueError):
        return []
    if not (math.isfinite(period) and period > 0 and math.isfinite(t0)):
        return []
    if not (math.isfinite(t_min) and math.isfinite(t_max)) or t_max < t_min:
        return []
    t14 = float(planet.get("t14") or 0.1)
    out = []
    n0 = math.floor((t_min - t0) / period) - 1
    n1 = math.ceil((t_max - t0) / period) + 1
    if n1 - n0 > 10000:   # a span of thousands of orbits: not a night of frames
        return []
    for n in range(n0, n1 + 1):
        tc = t0 + n * period
        unc = math.hypot(float(planet.get("t0_err") or 0.0), abs(n) * float(planet.get("period_err") or 0.0))
        half = t14 / 2 + 3 * unc + pad
        if tc + half >= t_min and tc - half <= t_max:
            out.append({"lo": tc - half, "hi": tc + half, "tc": tc, "epoch": n, "t1": tc - t14 / 2,
                        "t4": tc + t14 / 2, "unc": unc})
    return out


def planet_letter(name: str) -> str:
    """2.2.15 (item 1): 'HD 219134 b' -> 'b' (the name itself when it does not end in a planet letter)."""
    m = re.search(r"\s([b-z])$", str(name or "").strip())
    return m.group(1) if m else str(name or "").strip()


def planet_transit_frames(observations, planets, pad: float = 0.0105) -> list[dict]:
    """2.2.15 (item 1): which frames fall in a predicted transit of each planet. observations need only .jd
    (geocentric JD; the default pad of 15 minutes covers JD against BJD). Returns one entry per planet with frames
    inside a window: {"planet", "letter", "frames": n, "jd": [first, last frame inside], "windows": [[lo, hi], ...]
    (incl. pad), "t1", "t4" (the first such transit, no pad), "unc" (days)}."""
    jds = [float(o.jd) for o in observations if o.jd is not None and math.isfinite(float(o.jd))]
    if not jds or not planets:
        return []
    t_min, t_max = min(jds), max(jds)
    out = []
    for p in planets:
        wins = transit_windows(t_min, t_max, p, pad)
        if not wins:
            continue
        inside = [t for t in jds if any(w["lo"] <= t <= w["hi"] for w in wins)]
        if not inside:
            continue
        first = next(w for w in wins if any(w["lo"] <= t <= w["hi"] for t in inside))
        out.append({"planet": p.get("name") or "", "letter": planet_letter(p.get("name") or ""),
                    "frames": len(inside), "jd": [min(inside), max(inside)],
                    "windows": [[w["lo"], w["hi"]] for w in wins], "t1": first["t1"], "t4": first["t4"],
                    "unc": first["unc"]})
    return out


def transiting_planet_ephemerides(star: str) -> list[dict] | None:
    """2.2.15 (item 1): the transiting planets of a star typed as the Star ID, with what a transit window needs:
    [{"name", "period", "period_err", "t0", "t0_err", "t14" (days)}], using the most precise published ephemeris
    now (as lookup_nea does). [] when the Archive knows the star but it has no transiting planet with an
    ephemeris; None when the star is unknown or the Archive cannot be reached."""
    info = nea_resolve(star)
    if info is None:
        return None
    system = info.get("system") or ""
    if not info.get("planets") or not system or "'" in system:
        return []
    rows = _nea_query("select pl_name,pl_orbper,pl_orbpererr1,pl_tranmid,pl_tranmiderr1,pl_trandur,tran_flag "
                      f"from pscomppars where hostname = '{system}'")
    out = []
    now = now_jd()
    for row in rows:
        if str(row.get("tran_flag", "")).strip() not in ("1", "1.0"):
            continue
        name = row.get("pl_name") or ""
        period, t0 = _f(row.get("pl_orbper")), _f(row.get("pl_tranmid"))
        if not period or not t0:
            continue
        p = {"name": name, "period": period, "period_err": _f(row.get("pl_orbpererr1")), "t0": t0,
             "t0_err": _f(row.get("pl_tranmiderr1")), "t14": (_f(row.get("pl_trandur")) or 0) / 24.0 or None}
        try:
            best = most_precise_ephemeris(name, now, (p["period"], p["period_err"], p["t0"], p["t0_err"], ""))
        except Exception:
            best = None
        if best is not None:
            p.update(period=best["period"], period_err=best["period_err"], t0=best["t0"], t0_err=best["t0_err"])
        out.append(p)
    return sorted(out, key=lambda q: q["name"])


def _setup_fit(t, flux, err, airmass, planet: dict, detrend_airmass=True, after_flip=None, flip_step=None,
               flagged_mask=None) -> dict:
    """2.2.13: the data, start values, priors and flip time shared by fit_transit and lsq_fit."""
    t = np.asarray(t, dtype=float)
    flux = np.asarray(flux, dtype=float)
    err = np.asarray(err, dtype=float)
    am = np.asarray(airmass, dtype=float) if airmass is not None else np.full_like(t, np.nan)
    aflip = np.asarray(after_flip, dtype=bool) if after_flip is not None else np.zeros(t.shape, dtype=bool)
    fmask = np.asarray(flagged_mask, dtype=bool) if flagged_mask is not None else np.zeros(t.shape, dtype=bool)
    good = np.isfinite(t) & np.isfinite(flux) & np.isfinite(err) & (err > 0)
    if detrend_airmass and np.isfinite(am).any():
        good &= np.isfinite(am)
    t, flux, err, am, aflip, fmask = t[good], flux[good], err[good], am[good], aflip[good], fmask[good]
    order = np.argsort(t)
    t, flux, err, am, aflip, fmask = t[order], flux[order], err[order], am[order], aflip[order], fmask[order]
    if len(t) < 20:
        raise ValueError(f"only {len(t)} usable points; a transit fit needs at least 20")
    # 2.2.13: flagged frames put back by the user count with doubled error bars (FLAGGED_ERR_FACTOR): they are
    # measured, but through cloud, so they should not weigh like the clear frames. The usual rescaling to reduced
    # chi-square 1 and the red-noise beta then apply to all points on top of this.
    if fmask.any():
        err = err * np.where(fmask, FLAGGED_ERR_FACTOR, 1.0)
    period = float(planet["period"])
    # Fit in days from a nearby reference time: an absolute BJD (2.46e6) makes the optimizer's finite-difference
    # steps and stopping test far too coarse for Tc.
    t_abs = t
    t_ref = float(math.floor(t.min()))
    t = t_abs - t_ref
    med = np.median(flux)
    flux, err = flux / med, err / med
    u1g, u2g = limb_darkening_guess(planet.get("teff"))
    q1, q2 = u_to_kipping(u1g, u2g)
    # Predicted mid-transit nearest the data.
    # 2.2.12: without an ephemeris the start guess (the lowest point) is only a start; it is never reported as a
    # published or predicted time, and no O − C is computed.
    tc0 = planet.get("t0")
    eph_err_known = False
    if tc0:
        n = round((np.mean(t_abs) - tc0) / period)
        tc_pred = tc0 + n * period
        eph_err_known = planet.get("t0_err") is not None
        tc_pred_err = math.hypot(planet.get("t0_err") or 0.0, abs(n) * (planet.get("period_err") or 0.0))
        tc_start = tc_pred
    else:
        n, tc_pred, tc_pred_err = None, None, None
        tc_start = float(t_abs[np.argmin(flux)])
    tc_rel = tc_start - t_ref
    k0 = planet.get("k") or (math.sqrt(planet["depth"]) if planet.get("depth") else 0.1)
    k0 = min(max(k0, 0.01), 0.5)
    b0 = planet.get("b") if planet.get("b") is not None else 0.3
    b0 = min(max(b0, 0.0), 1.0 + k0 - 0.01)
    a0 = planet.get("a_rs") or _a_rs_from_duration(period, planet.get("t14"), k0, min(b0, 0.9))
    a0 = max(a0, b0 + 0.5, 1.5)
    start = {"tc": tc_rel, "k": k0, "a_rs": a0, "b": b0, "q1": q1, "q2": q2, "c0": 1.0, "c_am": 0.0, "c_t": 0.0,
             "step": 0.0}
    cadence = float(np.median(np.diff(t))) if len(t) > 1 else 1e-3
    dur = total_duration(period, a0, min(b0, 0.99), k0)
    starts = [{"tc": tc_rel + d} for d in (-0.25 * dur, 0.25 * dur)] + [{"k": k0 * 1.2}, {"k": k0 * 0.8}]
    # 2.2.13: the flip time, from the data: halfway between the last frame before the flip and the first after it.
    use_step = FLIP_STEP_ENABLED if flip_step is None else bool(flip_step)
    t_flip = flip_gap = None
    if use_step and aflip.any() and (~aflip).any():
        first_after = float(t[aflip].min())
        before = t[t < first_after]
        if before.size:
            t_flip = 0.5 * (float(before.max()) + first_after)
            flip_gap = (first_after - float(before.max())) * 1440
            starts += [{"step": 0.02}, {"step": -0.02}]
    return {"t": t, "t_abs": t_abs, "t_ref": t_ref, "flux": flux, "err": err, "am": am, "flagged": fmask,
            "med": float(med), "period": period, "priors": prepare_priors(planet, a0), "start": start,
            "free": ("tc", "k", "a_rs", "b", "q1", "q2", "c0", "c_am", "c_t", "step"), "ld": (u1g, u2g, 0.1),
            "cadence": cadence, "starts": starts, "t_flip": t_flip, "flip_gap_min": flip_gap, "n": n,
            "tc_pred": tc_pred, "tc_pred_err": tc_pred_err, "tc_start": tc_start, "eph_err_known": eph_err_known,
            "has_eph": bool(tc0)}


def flip_sides(t, t_flip: float, tc: float, t14: float, cadence: float, tc_pred: float | None = None) -> dict:
    """2.2.13: out-of-transit baseline on each side of the flip, relative to the fitted first and fourth contacts:
    {"before": (minutes, frames), "after": (minutes, frames)}. Minutes are frames × the median cadence, so a gap
    in the data is not counted as baseline. With a predicted mid-transit (tc_pred), a frame counts only when it is
    outside the predicted transit too: a fit pulled off by the step cannot make its own baseline (validation found
    nights where a wrong Tc turned in-transit frames into "baseline")."""
    t = np.asarray(t, dtype=float)
    oot = (t < tc - t14 / 2) | (t > tc + t14 / 2)
    if tc_pred is not None and math.isfinite(tc_pred):
        oot &= (t < tc_pred - t14 / 2) | (t > tc_pred + t14 / 2)
    out = {}
    for side, mask in (("before", t < t_flip), ("after", t >= t_flip)):
        n = int((oot & mask).sum())
        out[side] = (n * cadence * 1440.0, n)
    return out


def _frames(n: int) -> str:
    return f"{n} frame" + ("" if n == 1 else "s")


def _flip_info(prob_s, flat, summary, best_rel: dict, chi2_s: float, setup: dict, scale: float, source: str) -> dict:
    """2.2.13: what the night says about the flip step: its value and error (MCMC), its correlation with the depth
    (MCMC chain), the same fit without the step (least squares) for comparison, the baseline on each side, and
    whether the depth can be trusted."""
    i_s, i_k = prob_s.free.index("step"), prob_s.free.index("k")
    corr = float("nan")
    if flat.shape[0] > 10 and np.std(flat[:, i_s]) > 0 and np.std(flat[:, i_k]) > 0:
        corr = float(np.corrcoef(flat[:, i_k] ** 2, flat[:, i_s])[0, 1])
    # The same model without the step, on the same (rescaled) errors.
    nostep = None
    try:
        prob_ns = TransitProblem(setup["t"], setup["flux"], prob_s.err, setup["am"], setup["period"], setup["priors"],
                                 tuple(prob_s.free), dict(best_rel, step=0.0), "c_am" in prob_s.free,
                                 "c_t" in prob_s.free, setup["ld"])
        x_ns, chi2_ns = best_fit(prob_ns, [s for s in setup["starts"] if "step" not in s])
        e_ns = lsq_errors(prob_ns)
        p_ns = prob_ns.full(x_ns)
        ek = float(e_ns[prob_ns.free.index("k")])
        n = len(setup["t"])
        nostep = {"depth": p_ns["k"] ** 2, "depth_err": 2 * p_ns["k"] * ek, "tc": p_ns["tc"] + setup["t_ref"],
                  "chi2": chi2_ns,
                  # > 0: the data prefer the step (BIC: the step costs one parameter).
                  "dbic": (chi2_ns + len(prob_ns.free) * math.log(n)) - (chi2_s + len(prob_s.free) * math.log(n))}
    except Exception:
        nostep = None
    t14 = summary["t14"][0]
    pred = (setup["tc_pred"] - setup["t_ref"]) if setup.get("tc_pred") is not None else None
    sides = flip_sides(setup["t"], prob_s.t_flip, best_rel["tc"], t14, setup["cadence"], pred)
    short = [s for s in ("before", "after") if sides[s][0] < FLIP_MIN_OOT_MIN or sides[s][1] < FLIP_MIN_OOT_FRAMES]
    baseline_ok = not short
    reason = ""
    if not baseline_ok:
        which = "either side" if len(short) == 2 else ("before the flip" if short[0] == "before" else "after the flip")
        reason = (f"the meridian flip leaves no out-of-transit baseline of its own {which} "
                  f"({sides['before'][0]:.0f} min, {_frames(sides['before'][1])} before the flip; "
                  f"{sides['after'][0]:.0f} min, {_frames(sides['after'][1])} after; each side needs at least "
                  f"{FLIP_MIN_OOT_MIN:.0f} min and {FLIP_MIN_OOT_FRAMES} frames to tell the flip step from the depth)")
    elif math.isfinite(corr) and abs(corr) > FLIP_CORR_WARN:
        reason = (f"the step at the meridian flip and the depth trade off (correlation {corr:+.2f}); depth not "
                  "measurable from this night")
    return {"t_flip": prob_s.t_flip + setup["t_ref"], "gap_min": setup.get("flip_gap_min"), "source": source,
            "step": summary["step"], "corr": corr, "nostep": nostep, "sides": sides, "baseline_ok": baseline_ok,
            "depth_ok": baseline_ok and not (math.isfinite(corr) and abs(corr) > FLIP_CORR_WARN), "reason": reason,
            "error_scale": scale}


def lsq_fit(t, flux, err, airmass, planet: dict, detrend_airmass=True, detrend_time=False, after_flip=None,
            flip_step=None) -> dict:
    """2.2.13: the fit_transit model by least squares only (no MCMC), with Jacobian errors after the same error
    rescaling (reduced chi-square and red-noise beta). For fast injection-recovery tests of the flip step."""
    S = _setup_fit(t, flux, err, airmass, planet, detrend_airmass, after_flip, flip_step)
    t, flux, err, am = S["t"], S["flux"], S["err"], S["am"]
    prob = TransitProblem(t, flux, err, am, S["period"], S["priors"], S["free"], S["start"], detrend_airmass,
                          detrend_time, S["ld"], S["t_flip"])
    x, chi2 = best_fit(prob, S["starts"])
    rchi = chi2 / max(len(t) - len(prob.free), 1)
    beta = red_noise_beta(t, flux - prob.model(prob.full(x)), S["cadence"])
    scale = math.sqrt(max(rchi, 1.0)) * beta
    prob_s = TransitProblem(t, flux, err * scale, am, S["period"], S["priors"], S["free"], prob.full(x),
                            detrend_airmass, detrend_time, S["ld"], S["t_flip"])
    x_s, chi2_s = best_fit(prob_s)
    e = lsq_errors(prob_s)
    p = prob_s.full(x_s)
    out = {"p": dict(p, tc=p["tc"] + S["t_ref"]), "err": dict(zip(prob_s.free, map(float, e))), "chi2": chi2_s,
           "error_scale": scale, "free": list(prob_s.free), "t_flip": (S["t_flip"] + S["t_ref"]) if prob_s.t_flip
           is not None else None, "cadence": S["cadence"], "t": S["t_abs"]}
    out["depth"] = (p["k"] ** 2, 2 * p["k"] * out["err"]["k"])
    out["t14"] = total_duration(S["period"], p["a_rs"], min(p["b"], p["a_rs"] * 0.999), p["k"])
    if prob_s.t_flip is not None:
        pred = (S["tc_pred"] - S["t_ref"]) if S.get("tc_pred") is not None else None
        sides = flip_sides(t, prob_s.t_flip, p["tc"], out["t14"], S["cadence"], pred)
        out["sides"] = sides
        out["baseline_ok"] = all(sides[s][0] >= FLIP_MIN_OOT_MIN and sides[s][1] >= FLIP_MIN_OOT_FRAMES
                                 for s in sides)
    return out


def fit_transit(t, flux, err, airmass, planet: dict, detrend_airmass=True, detrend_time=False,
                mcmc_steps: int = 3000, progress=None, cancel=None, log=None, after_flip=None, flip_source: str = "",
                flip_step=None, flagged_mask=None, flagged_label: str = "flagged", excluded=None) -> dict:
    """The full SHOBS-P transit fit.

    1. Least squares with the published shape as Gaussian priors (a/R*, b) and broad limb-darkening priors.
    2. Errors rescaled so the reduced chi-square is 1, then multiplied by the red-noise beta.
    3. MCMC (always) with the rescaled errors, for the reported values and uncertainties.
    4. A second, free fit (no priors on a/R* and b) as a check of the shape.

    2.2.13: after_flip (one flag per point, True after a meridian flip) adds the flip step to the baseline, fitted
    jointly (see FLIP_STEP_ENABLED; flip_step=True/False overrides it). flagged_mask marks flagged frames the user put
    back in (their error bars are doubled and the result says so). excluded = {"t": BJD_TDB, "jd": JD UTC, "label":
    "cloud"} lists flagged frames left out, so the cautions can say when they emptied a baseline.
    """
    say = log or (lambda _m: None)
    S = _setup_fit(t, flux, err, airmass, planet, detrend_airmass, after_flip, flip_step, flagged_mask)
    t, t_abs, t_ref, flux, err, am = S["t"], S["t_abs"], S["t_ref"], S["flux"], S["err"], S["am"]
    period, priors, free, ld, cadence, starts = S["period"], S["priors"], S["free"], S["ld"], S["cadence"], S["starts"]
    t_flip = S["t_flip"]

    prob = TransitProblem(t, flux, err, am, period, priors, free, S["start"], detrend_airmass, detrend_time, ld,
                          t_flip)
    say("Transit fit: least squares with the published shape as priors…")
    if prob.t_flip is not None:
        say(f"  meridian flip at BJD_TDB {t_flip + t_ref:.5f} ({flip_source or 'from the data'}): a baseline step "
            "there is fitted together with the transit")
    x, chi2 = best_fit(prob, starts)
    dof = max(len(t) - len(prob.free), 1)
    rchi = chi2 / dof
    resid = flux - prob.model(prob.full(x))
    beta = red_noise_beta(t, resid, cadence)
    scale = math.sqrt(max(rchi, 1.0)) * beta
    say(f"  reduced chi-square {rchi:.2f}; red-noise beta {beta:.2f}; error bars x{scale:.2f} for the MCMC")
    prob_s = TransitProblem(t, flux, err * scale, am, period, priors, free, prob.full(x), detrend_airmass,
                            detrend_time, ld, t_flip)
    x_s, chi2_s = best_fit(prob_s)
    n_burn = max(500, mcmc_steps // 3)
    say(f"  MCMC: {max(2 * len(prob_s.free) + 2, 20)} walkers, {n_burn} burn-in + {mcmc_steps} steps…")
    chain = run_mcmc(prob_s, x_s, n_walkers=20, n_burn=n_burn, n_steps=mcmc_steps, progress=progress, cancel=cancel)
    summary = summarize(prob_s, chain["flat"])
    best = prob_s.full(np.array([summary[p][0] for p in prob_s.free]))
    # Free shape check (no priors on a/R* and b), least squares only.
    free_fit = None
    try:
        prob_f = TransitProblem(t, flux, err * scale, am, period, {}, free, best, detrend_airmass, detrend_time, ld,
                                t_flip)
        xf, chi2f = best_fit(prob_f, [{"b": 0.1}, {"b": 0.5}, {"b": 0.8}, {"b": 0.95, "k": best["k"] * 1.3},
                                      {"b": min(1.0 + best["k"] * 1.5, 1.4), "k": best["k"] * 2.0}])
        pf = prob_f.full(xf)
        pf["tc"] += t_ref
        free_fit = {"p": pf, "chi2": chi2f, "depth": pf["k"] ** 2,
                    "t14": total_duration(period, pf["a_rs"], min(pf["b"], pf["a_rs"] * 0.999), pf["k"])}
    except Exception as exc:
        say(f"  free-shape check skipped: {exc}")
    flip = None
    if prob_s.t_flip is not None:
        flip = _flip_info(prob_s, chain["flat"], summary, best, chi2_s, S, scale, flip_source)
        say(f"  flip step {flip['step'][0] * 100:+.2f} ± {flip['step'][1] * 100:.2f}%, step–depth correlation "
            f"{flip['corr']:+.2f}" + (f"; without the step the depth would be {flip['nostep']['depth'] * 100:.2f}%"
                                      if flip.get("nostep") else ""))
    model = prob_s.model(best)
    base = prob_s.baseline(best)
    resid = flux - model
    best["tc"] += t_ref
    summary["tc"] = (summary["tc"][0] + t_ref, summary["tc"][1])
    fmask = S["flagged"]
    result = {
        "t": t_abs, "t_ref": t_ref, "flux": flux, "err": err, "err_scaled": err * scale, "airmass": am, "model": model,
        "baseline": base, "detrended": flux / base, "residuals": resid, "best": best, "summary": summary,
        "free_params": list(prob_s.free), "priors": priors, "ld_prior": ld, "rchi2": rchi, "beta": beta,
        "error_scale": scale, "rms": float(np.std(resid)), "cadence_days": cadence, "chain": chain,
        # 2.2.14 (GitHub #5): rms of 10-minute bins, as AstroImageJ shows it.
        "rms_bin10": binned_rms(t_abs, resid, 10.0),
        "tc_pred": S["tc_pred"], "tc_pred_err": S["tc_pred_err"], "epoch": S["n"], "period": period,
        "planet": dict(planet), "eph_err_known": S["eph_err_known"],
        "eph_source": ephemeris_source(planet) if S["has_eph"] else "",
        "tc_start": S["tc_start"],
        "free_fit": free_fit, "n_points": len(t), "flux_median": S["med"],
        "detrend": [name for name, on in (("airmass", detrend_airmass and prob_s.has_airmass),
                                          ("time", detrend_time)) if on],
        # 2.2.13
        "flip": flip, "flagged": fmask,
        "with_flagged": ({"n": int(fmask.sum()), "label": flagged_label, "factor": FLAGGED_ERR_FACTOR}
                         if fmask.any() else None),
        "excluded": excluded if excluded and len(np.atleast_1d(excluded.get("t", []))) else None,
    }
    result["verdict"] = transit_verdict(result, prob_s)
    result["flags"] = transit_flags(result)
    v = result["verdict"]
    v["depth_unreliable"] = depth_unreliable(result) if v["state"] == "detected" else ""
    if (flip and not flip["baseline_ok"] and v["state"] == "detected"
            and FLIP_NO_BASELINE_VERDICT == "inconclusive"):
        # 2.2.13: no baseline of its own on one side of the flip: the step trades off against the timing as well as
        # the depth, so the dip is reported, not measured.
        v.update(state="inconclusive", detected=False, depth_unreliable="", flip_inconclusive=True,
                 reason=(f"Inconclusive. The dip beats a flat line with the flip step (ΔBIC {v['dbic']:.0f}), but "
                         + flip["reason"] + "; without that baseline the step trades off against the timing as well "
                         "as the depth."))
    elif flip and not flip["depth_ok"] and v["state"] == "detected":
        # 2.2.13: the flip step and the depth cannot be told apart on this night: timing usable, depth not.
        v["depth_unreliable"] = flip["reason"] + ("; and " + v["depth_unreliable"] if v["depth_unreliable"] else "")
    if result["with_flagged"]:
        wf = result["with_flagged"]
        v["includes_flagged"] = (f"Includes {wf['n']} {wf['label']}-flagged frame{'s' if wf['n'] != 1 else ''} "
                                 f"(error bars ×{wf['factor']:g}).")
    note = ""
    if v["state"] == "none":
        note = ("NO TRANSIT MEASURED: " + v["reason"] + " The numbers are shown for reference only; do not report "
                "them.")
    elif v["state"] == "inconclusive" and v.get("flip_inconclusive"):
        note = ("INCONCLUSIVE. " + v["reason"].replace("Inconclusive. ", "", 1) + " Neither Tc nor the depth is "
                "measured. A report from this fit carries the verdict in its notes.")
    elif v["state"] == "inconclusive":
        note = ("INCONCLUSIVE. " + v["reason"].replace("Inconclusive. ", "", 1) + " Tc may still be useful; the "
                "depth is not. A report from this fit carries the verdict in its notes.")
    if note:
        # Kept in the flags for the text summary and reports. 2.2.12: the Transit fit page shows the verdict above
        # the plot and leaves this copy out of its notes (verdict["note"] marks it).
        result["flags"].insert(0, ("warn", note))
    v["note"] = note
    return result


def cautions(flags, verdict: dict | None) -> list:
    """2.2.12: the flags without the verdict's own copy (the page shows the verdict above the plot)."""
    note = (verdict or {}).get("note")
    return [f for f in (flags or []) if not (note and f[1] == note)]


def ephemeris_source(planet: dict) -> str:
    """2.2.12: where the planet's t0 and period came from, for the O − C line."""
    src = planet.get("eph_source")
    if src:
        return src
    ref = (planet.get("reference") or "").strip()
    if ref.lower().startswith("ephemeris:"):
        ref = ref.split(":", 1)[1].strip()
    ref = ref.lstrip("; ").strip()
    base = planet.get("source") or "the planet's published values"
    return base + (f" ({ref})" if ref else "")


def _norm_planet(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _inits_matches(pp: dict, planet_name: str, host: str) -> bool:
    """The inits file is for this planet: the same planet name (EXOTIC sometimes doubles the letter, 'TOI-2046b b'),
    or, when the report names no planet, the same host. Never a prefix match (WASP-1 b is not WASP-12 b)."""
    rp, rh = _norm_planet(planet_name), _norm_planet(host)
    ip, ih = _norm_planet(pp.get("Planet Name")), _norm_planet(pp.get("Host Star Name"))
    names = {ip}
    if len(ip) > 2 and ip[-1].isalpha() and ip[-1] == ip[-2]:
        names.add(ip[:-1])
    if rp:
        return rp in names or (bool(ih) and rp == ih and (not rh or rh == ih))
    return bool(rh) and rh == ih


def ephemeris_from_inits(folder: str, planet_name: str = "", host: str = "") -> dict | None:
    """2.2.12: the ephemeris EXOTIC used, from an inits*.json in the report's folder. EXOTIC's "Published
    Mid-Transit Time (BJD-UTC)" is the NASA Exoplanet Archive's pl_tranmid, which is BJD_TDB (EXOTIC compares it
    with its BJD_TDB fit as is), so it is used unchanged. The newest file for this planet; None when there is none."""
    import glob
    found = []
    for path in glob.glob(os.path.join(folder or ".", "inits*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                pp = json.load(fh).get("planetary_parameters") or {}
        except Exception:
            continue
        if not _inits_matches(pp, planet_name, host):
            continue
        period = _f(pp.get("Orbital Period (days)"))
        t0 = _f(pp.get("Published Mid-Transit Time (BJD-UTC)"))
        if not period or not t0 or t0 < 2.4e6:
            continue
        found.append((os.path.getmtime(path), path, period, t0, pp))
    if not found:
        return None
    _m, path, period, t0, pp = max(found, key=lambda f: f[0])
    return {"period": period, "period_err": _f(pp.get("Orbital Period Uncertainty")),
            "t0": t0, "t0_err": _f(pp.get("Mid-Transit Time Uncertainty")),
            "eph_source": f"EXOTIC's inits file {os.path.basename(path)}"}


def transit_verdict(r: dict, prob) -> dict:
    """Was a transit actually measured? Two tests, both on the noise-scaled errors:
    1. A flat line (the same baseline and detrending, no transit) must fit clearly worse: delta BIC >= 10
       (BIC of the flat line minus BIC of the transit model; the transit model pays for Tc and Rp/R*).
    2. Tc must be pinned down: its uncertainty at most a quarter of the transit duration."""
    from scipy.optimize import least_squares

    t, flux, err = prob.t, prob.flux, prob.err
    am = prob.am
    # 2.2.13: with a meridian flip in the model the flat line gets the same step, so a plain jump at the flip is
    # never taken for a transit.
    names = [p for p in ("c0", "c_am", "c_t", "step") if p in prob.free]
    t_mid = prob.t_mid
    after = prob.after if prob.after is not None else 0.0

    def flat(x):
        p = dict(zip(names, x))
        base = p.get("c0", 1.0) + p.get("c_am", 0.0) * am + p.get("c_t", 0.0) * (t - t_mid)
        if "step" in p:
            base = base * (1.0 + p["step"] * after)
        return (flux - base) / err

    sol = least_squares(flat, [r["best"].get(n, 0.0) if n != "c0" else 1.0 for n in names])
    chi2_flat = float(np.dot(sol.fun, sol.fun))
    resid = (flux - r["model"]) / err
    chi2_tr = float(np.dot(resid, resid))
    n = len(t)
    extra = 2   # Tc and Rp/R* (the shape is held near the published values by the priors)
    dbic = (chi2_flat + len(names) * math.log(n)) - (chi2_tr + (len(names) + extra) * math.log(n))
    tc_err = r["summary"]["tc"][1]
    t14 = r["summary"].get("t14", (r["planet"].get("t14") or 0.1, 0))[0]
    reasons = []
    if dbic < 10:
        reasons.append(f"{'a flat line with a step at the meridian flip' if 'step' in names else 'a flat line'} fits "
                       f"about as well as a transit (ΔBIC {dbic:.1f}; a detection needs 10 or more)")
    if tc_err > 0.25 * t14:
        reasons.append(f"Tc is not pinned down (± {tc_err * 24:.1f} h for a {t14 * 24:.1f}-hour transit)")
    # 2.2: is the dip believable? (1) Its depth against the published one. 2.2.13: not when a meridian flip makes the
    # depth unmeasurable (the step and the depth trade off); the depth is then reported as unreliable instead.
    flip = r.get("flip")
    plaus = depth_plausibility(r) if not (flip and not flip.get("depth_ok", True)) else ""
    if plaus:
        reasons.append(plaus)
    # (2) A plain jump in the light curve at a gap (a mount problem, a rotator move, a re-centring) imitates a
    # transit. If a baseline with a step after the gap fits as well as the transit, the dip is not measured.
    # 2.2.13: with the flip step in the model, the flat line above already carries that step, so this is skipped.
    step = None
    if "step" not in prob.free:
        step = step_at_gap(t, flux, err, am, t_mid, names, r["best"], chi2_tr, extra,
                           tc_rel=r["best"]["tc"] - r.get("t_ref", 0.0))
    step_reason = ""
    if step and step["explains"]:
        step_reason = (f"a plain {abs(step['step']) * 100:.1f}% jump after the {step['gap_min']:.0f}-minute gap at "
                       f"{step['gap_at_h']:+.1f} h fits as well as the transit (ΔBIC {step['dbic']:+.1f} in favour of "
                       "the jump), so the dip may be the jump, not the planet")
    # 2.2.1: three verdicts. A dip that beats a flat line, with Tc pinned and a believable depth, but that a jump
    # at a gap explains as well, is inconclusive: the planet may be there, the night cannot tell.
    if reasons:
        state = "none"
        if step_reason:
            reasons.append(step_reason)
    elif step_reason:
        state = "inconclusive"
        reasons.append(step_reason)
    else:
        state = "detected"

    def sentence(parts):
        text = "; ".join(parts)
        return text[:1].upper() + text[1:] + "."

    flat_name = "a flat line with the flip step" if "step" in names else "a flat line"
    reason = {"detected": f"Transit detected: ΔBIC {dbic:.0f} against {flat_name}, Tc ± {tc_err * 1440:.1f} min.",
              "inconclusive": f"Inconclusive. The dip beats a flat line (ΔBIC {dbic:.0f}), but "
                              + "; ".join(reasons) + ".",
              "none": sentence(reasons) if reasons else ""}[state]
    return {"detected": state == "detected", "state": state, "dbic": dbic, "chi2_flat": chi2_flat,
            "chi2_transit": chi2_tr, "step": step, "reason": reason}


def published_depth(planet: dict | None) -> tuple:
    """2.2.13: the depth a fit is compared with, and its label: (Rp/R*)² whenever Rp/R* is known, else the listed
    depth. The fit's "Depth" is its (Rp/R*)², so this compares like with like. A catalog's listed depth (the NASA
    Exoplanet Archive's pl_trandep) is a different quantity, or from another paper: the limb-darkened dip at
    mid-transit is deeper than (Rp/R*)² (by 10–30% in a green band), and some listed depths come from other analyses
    (HAT-P-23 b: listed 1.05%, while its Rp/R* 0.1169 gives 1.37%). complete_planet already sets planet["depth"] to
    (Rp/R*)² when both are known; this also covers dicts made before 2.2.13."""
    p = planet or {}
    k = p.get("k")
    if isinstance(k, (int, float)) and math.isfinite(k) and k > 0:
        return k * k, "(Rp/R*)²"
    d = p.get("depth")
    return (d if isinstance(d, (int, float)) and math.isfinite(d) and d > 0 else None), "depth"


def ld_center_depth(planet: dict | None) -> float | None:
    """2.2.13: the limb-darkened dip at mid-transit for the published shape (what the dotted published-shape curve
    on the plot reaches), for the notes only; comparisons use (Rp/R*)²."""
    p = planet or {}
    if not p.get("k"):
        return None
    u1, u2 = limb_darkening_guess(p.get("teff"))
    b = p.get("b") if p.get("b") is not None else 0.0
    return float(1.0 - occultation(np.array([min(b, 1.0 + p["k"] - 1e-6)]), p["k"], u1, u2)[0])


def depth_unreliable(r: dict) -> str:
    """2.2.7: for a detected transit, '' when the depth can be trusted, else why not: the depth is more than 3 sigma
    from the published one AND the night gives a reason a systematic could have made the difference (a jump at a gap
    inside the transit that fits nearly as well, ΔBIC under 10; or a one-sided baseline). Timing stays usable."""
    pub, pub_label = published_depth(r.get("planet"))
    s = r.get("summary") or {}
    if not pub or pub <= 0 or "depth" not in s:
        return ""
    depth, err = s["depth"]
    if abs(depth - pub) <= 3 * max(err, 1e-6):
        return ""
    causes = []
    step = (r.get("verdict") or {}).get("step")
    half_t14_h = s["t14"][0] * 12.0 if "t14" in s else None
    if (step and step.get("dbic") is not None and step["dbic"] > -10
            and (half_t14_h is None or step.get("gap_at_h") is None or abs(step["gap_at_h"]) <= half_t14_h)):
        causes.append(f"a {abs(step['step']) * 100:.1f}% jump at the {step['gap_min']:.0f}-minute gap fits nearly as "
                      f"well (ΔBIC {-step['dbic']:.1f} for the transit)")
    t = np.asarray(r.get("t"))
    best = r.get("best") or {}
    if t.size and "t14" in s and "tc" in best:
        t1, t4 = best["tc"] - s["t14"][0] / 2, best["tc"] + s["t14"][0] / 2
        pre_min = (t1 - t.min()) * 1440 if (t < t1).any() else 0.0
        post_min = (t.max() - t4) * 1440 if (t > t4).any() else 0.0
        if pre_min < 20 or post_min < 20:
            causes.append(f"the baseline is one-sided ({pre_min:.0f} min before, {post_min:.0f} min after)")
    if not causes:
        return ""
    return (f"depth {depth * 100:.2f}% vs published {pub_label} {pub * 100:.2f}% "
            f"({abs(depth - pub) / max(err, 1e-6):.0f}σ), and " + "; ".join(causes))


def depth_plausibility(r: dict) -> str:
    """'' when the fitted depth is believable against the published depth, else the reason it is not: more than
    3x deeper (or shallower) than published AND more than 3 sigma away."""
    pub, pub_label = published_depth(r.get("planet"))
    s = r.get("summary") or {}
    if not pub or pub <= 0 or "depth" not in s:
        return ""
    depth, err = s["depth"]
    if depth <= 0:
        return ""
    ratio = depth / pub
    sigma = abs(depth - pub) / max(err, 1e-6)
    if (ratio > 3.0 or ratio < 1.0 / 3.0) and sigma > 3.0:
        return (f"the depth, {depth * 100:.2f}%, is {ratio:.1f}x the published {pub_label} {pub * 100:.2f}% "
                f"({sigma:.0f}σ away); a "
                "real transit of this planet cannot be that " + ("deep" if ratio > 1 else "shallow") + ", so something "
                "in the light curve (a jump, a trend, a bad comp, calibration) made it")
    return ""


def step_at_gap(t, flux, err, am, t_mid, names, best, chi2_transit, extra, tc_rel: float = 0.0,
                min_gap_min: float = 20.0):
    """The largest gap in the series (at least min_gap_min and 5 cadences): fit the same baseline as the flat-line
    test plus a step after the gap, and compare with the transit model by BIC. Returns None without a gap."""
    from scipy.optimize import least_squares

    t = np.asarray(t, dtype=float)
    if len(t) < 12:
        return None
    dt = np.diff(t)
    cadence = float(np.median(dt)) if len(dt) else 0.0
    k = int(np.argmax(dt))
    gap = float(dt[k])
    if gap * 1440 < max(min_gap_min, 5 * cadence * 1440):
        return None
    after = (t > t[k]).astype(float)
    if after.sum() < 4 or (1 - after).sum() < 4:
        return None

    def stepped(x):
        p = dict(zip(names, x[:-1]))
        base = p.get("c0", 1.0) + p.get("c_am", 0.0) * am + p.get("c_t", 0.0) * (t - t_mid) + x[-1] * after
        return (flux - base) / err

    start = [best.get(n, 0.0) if n != "c0" else 1.0 for n in names] + [0.0]
    sol = least_squares(stepped, start)
    chi2_step = float(np.dot(sol.fun, sol.fun))
    n = len(t)
    bic_step = chi2_step + (len(names) + 1) * math.log(n)
    bic_tr = chi2_transit + (len(names) + extra) * math.log(n)
    dbic = bic_tr - bic_step          # > 0: the step is preferred
    return {"step": float(sol.x[-1]), "gap_min": gap * 1440, "gap_at_h": (t[k] + gap / 2 - tc_rel) * 24,
            "chi2": chi2_step, "dbic": dbic, "explains": dbic > -2.0}


def _a_rs_from_duration(period, t14_days, k, b):
    """a/R* from the total duration when the catalog has none (circular orbit)."""
    if not t14_days:
        return 10.0
    s = math.sin(math.pi * t14_days / period)
    if s <= 0:
        return 10.0
    b = min(max(b, 0.0), 0.95 * (1 + k))
    return math.sqrt(max(((1 + k) ** 2 - b * b) / s ** 2 + b * b, 1.5 ** 2))


def transit_flags(r: dict) -> list[tuple[str, str]]:
    """Cautions about a fit: (level, text), level 'warn' (orange) or 'info'."""
    flags = []
    s = r["summary"]
    best = r["best"]
    period = r["period"]
    k = best["k"]
    for name, label in (("a_rs", "a/R*"), ("b", "b")):
        if name in r["priors"] and name in s:
            mean, sigma = r["priors"][name]
            dev = (s[name][0] - mean) / sigma
            if abs(dev) > 3:
                flags.append(("warn", f"{label} = {s[name][0]:.3g} is {abs(dev):.1f}σ from the published "
                                      f"{mean:.3g} ± {sigma:.2g}. The light curve pulls hard against the known shape: "
                                      "look for a trend, a bad comp, or clouds."))
    if best["b"] > 1.0 - k:
        flags.append(("warn", f"Grazing geometry (b {best['b']:.2f} > 1 − Rp/R* {1 - k:.2f}): the planet's disk "
                              "crosses the star's edge, so size and impact parameter trade off and Rp/R* depends "
                              "heavily on the b prior."))
    ff = r.get("free_fit")
    if ff:
        dk = ff["p"]["k"] - k
        if "k" in s and s["k"][1] > 0 and abs(dk) > 2 * s["k"][1]:
            flags.append(("warn", f"With the shape left free, Rp/R* goes to {ff['p']['k']:.3f} (b {ff['p']['b']:.2f}, "
                                  f"a/R* {ff['p']['a_rs']:.1f}). The data alone do not pin the shape; the reported "
                                  "values lean on the published a/R* and b."))
    t = r["t"]
    t14 = s.get("t14", (total_duration(period, best["a_rs"], best["b"], k), 0))[0]
    t1, t4 = best["tc"] - t14 / 2, best["tc"] + t14 / 2
    pre = (t < t1).sum()
    post = (t > t4).sum()
    pre_min = (t1 - t.min()) * 1440 if pre else 0.0
    post_min = (t.max() - t4) * 1440 if post else 0.0
    if pre_min < 20 or post_min < 20:
        side = "before ingress" if pre_min < 20 else "after egress"
        if pre_min < 20 and post_min < 20:
            side = "before ingress or after egress"
        # 2.2.13: say when flagged frames (cloud) left out of the fit are what emptied that side.
        why = excluded_baseline_text(r.get("excluded"), t1, t4, pre_min < 20, post_min < 20, pre_min, post_min)
        flags.append(("warn", (why + " " if why else "") +
                      f"Little or no baseline {side} ({pre_min:.0f} min before, {post_min:.0f} min after). "
                      "A one-sided baseline lets a trend imitate depth or shift Tc."))
    ts = np.sort(t)
    inside = ts[(ts >= t1) & (ts <= t4)]
    if inside.size >= 2:
        # Gaps between points actually taken; the edges of the window count only when there are data beyond them
        # (time after the last point is "not covered", not a gap).
        seq = list(inside)
        if (ts < t1).any():
            seq = [t1] + seq
        if (ts > t4).any():
            seq = seq + [t4]
        big = float(np.diff(seq).max()) * 1440 if len(seq) > 1 else 0.0
        if big > max(10.0, 0.2 * t14 * 1440):
            flags.append(("warn", f"A {big:.0f}-minute gap in the data inside the transit. Ingress, egress, or the "
                                  "bottom may be missing, so the shape and Tc are less certain."))
        if not (ts < t1).any() and (inside.min() - t1) * 1440 > 10:
            flags.append(("warn", f"The data start {(inside.min() - t1) * 1440:.0f} min after the fitted ingress: "
                                  "ingress was not observed."))
        if not (ts > t4).any() and (t4 - inside.max()) * 1440 > 10:
            flags.append(("warn", f"The data end {(t4 - inside.max()) * 1440:.0f} min before the fitted egress: "
                                  "egress was not observed."))
    else:
        flags.append(("warn", "Almost no points inside the predicted transit window: the transit was missed "
                              "(clouds, a gap, or the ephemeris)."))
    step = (r.get("verdict") or {}).get("step")
    if step and not step["explains"]:
        flags.append(("info", f"Step check: a {abs(step['step']) * 100:.1f}% jump at the {step['gap_min']:.0f}-minute "
                              f"gap fits worse than the transit (ΔBIC {-step['dbic']:.1f} in favour of the transit)."))
    if r.get("rchi2") is not None and r["rchi2"] < 0.3:
        ratio = 1.0 / math.sqrt(max(r["rchi2"], 1e-6))
        flags.append(("warn", f"The per-point error bars are about {ratio:.1f}x larger than the real scatter (reduced "
                              f"χ² {r['rchi2']:.2f}). The photometry's noise estimate is too pessimistic, which usually "
                              "points at calibration: darks scaled far from their exposure, a mismatched bias, or a "
                              "noisy flat. The fit keeps the larger error bars, so its uncertainties are on the safe side."))
    if r.get("tc_pred") is not None and r.get("tc_pred_err") is not None:
        oc = (best["tc"] - r["tc_pred"]) * 1440
        sig = math.hypot(s["tc"][1], r["tc_pred_err"]) * 1440 if "tc" in s else float("nan")
        level = "warn" if (math.isfinite(sig) and sig > 0 and abs(oc) > 3 * sig) else "info"
        src = r.get("eph_source") or "the published ephemeris"
        flags.append((level, f"O − C {oc:+.1f} ± {sig:.1f} min (epoch {r['epoch']}); ephemeris from {src}."))
        # 2.2.12: say when the ephemeris itself is the weak part.
        eph_min = r["tc_pred_err"] * 1440
        fit_min = (s["tc"][1] * 1440) if "tc" in s else float("nan")
        if r.get("eph_err_known") is False:
            flags.append(("warn", "The ephemeris's mid-transit time has no stated uncertainty, so the ± above "
                                  "leaves it out; treat the O − C with care."))
        elif eph_min > 10.0 and (not math.isfinite(fit_min) or eph_min > 2.0 * fit_min):
            flags.append(("warn", f"The predicted mid-transit is uncertain by ±{eph_min:.0f} min (ephemeris "
                                  f"propagated to epoch {r['epoch']}), so this O − C mostly tests the ephemeris."))
    elif r.get("tc_pred") is None:
        flags.append(("info", "No ephemeris (mid-transit time) was available: Tc was started at the lowest "
                              "point and no O − C is given."))
    if r["beta"] > 1.5:
        flags.append(("info", f"Red noise: beta {r['beta']:.2f}. Correlated noise (seeing, airmass, guiding) is "
                              "included in the uncertainties."))
    pub, pub_label = published_depth(r["planet"])
    if r["planet"].get("depth_note"):
        flags.append(("info", r["planet"]["depth_note"]))
    flip = r.get("flip")
    if pub and "depth" in s and not (flip and not flip.get("depth_ok", True)):
        dd = s["depth"][0] - pub
        if abs(dd) > 3 * max(s["depth"][1], 1e-5):
            why = ("a neighbor's light in the aperture dilutes a transit (makes it shallower)" if dd < 0 else
                   "a deeper dip than published usually comes from the light curve itself (a jump, a trend, a comp "
                   "or calibration problem); a neighbor's light cannot make a transit deeper")
            ldc = ld_center_depth(r["planet"]) if pub_label != "depth" else None
            ld_text = (f" (the fit's depth is its (Rp/R*)², compared with the published one; the limb-darkened dip "
                       f"at mid-transit is deeper, about {ldc * 100:.2f}% for the published shape)" if ldc else "")
            flags.append(("info", f"Depth {s['depth'][0] * 100:.2f}% vs published {pub_label} {pub * 100:.2f}% "
                                  f"({dd * 100:+.2f}%){ld_text}. A clear-filter depth can differ a little from "
                                  f"TESS's red band; a large difference: {why}."))
    # 2.2.13: the meridian flip step, and flagged frames put back in.
    if flip:
        rel_h = (flip["t_flip"] - best["tc"]) * 24
        st, se = flip["step"]
        ns = flip.get("nostep")
        text = (f"Meridian flip at BJD_TDB {flip['t_flip']:.5f} ({rel_h:+.2f} h from mid-transit; "
                f"{flip.get('source') or 'from the data'}): a baseline step there was fitted together with the "
                f"transit: step {st * 100:+.2f} ± {se * 100:.2f}%.")
        if ns:
            text += (f" Without the step the depth would be {ns['depth'] * 100:.2f} ± {ns['depth_err'] * 100:.2f}% "
                     f"(least squares); ΔBIC {ns['dbic']:+.0f} "
                     + ("in favour of the step." if ns["dbic"] >= 0 else "against the step."))
        flags.append(("info", text))
        c = flip.get("corr")
        if c is not None and math.isfinite(c):
            if abs(c) > FLIP_CORR_WARN:
                flags.append(("warn", f"Step–depth correlation {c:+.2f} (MCMC): the step and the depth trade off; "
                                      "depth not measurable from this night."))
            else:
                flags.append(("info", f"Step–depth correlation {c:+.2f} (MCMC); its uncertainty is part of the depth's."))
        if not flip.get("baseline_ok", True):
            sd = flip["sides"]
            flags.append(("warn", f"Out-of-transit baseline on each side of the flip: {sd['before'][0]:.0f} min "
                                  f"({_frames(sd['before'][1])}) before, {sd['after'][0]:.0f} min "
                                  f"({_frames(sd['after'][1])}) after. Each side needs at least {FLIP_MIN_OOT_MIN:.0f} min and "
                                  f"{FLIP_MIN_OOT_FRAMES} frames to tell the step from the depth, so "
                                  + ("neither the depth nor the timing is trusted (the step trades off against both)."
                                     if FLIP_NO_BASELINE_VERDICT == "inconclusive" else
                                     "the depth is not trusted; the timing is.")))
    wf = r.get("with_flagged")
    if wf:
        flags.append(("warn", f"This fit includes {wf['n']} frame{'s' if wf['n'] != 1 else ''} flagged {wf['label']}, "
                              f"with their error bars ×{wf['factor']:g}. Compare it with the fit without them (Fit "
                              "transit) before reporting it."))
    return flags


def excluded_baseline_text(excluded, t1, t4, pre_short, post_short, pre_min, post_min) -> str:
    """2.2.13: when a side's baseline is short and flagged frames left out of the fit lie there, say so:
    'No usable baseline after egress: the 31 frames there (00:44–01:20) were flagged cloud.' Times are local
    (from the frames' JD UTC); minutes from the fitted contact when no JD is given."""
    if not excluded:
        return ""
    t = np.atleast_1d(np.asarray(excluded.get("t", []), dtype=float))
    jd = excluded.get("jd")
    jd = np.atleast_1d(np.asarray(jd, dtype=float)) if jd is not None else None
    label = excluded.get("label") or "flagged"
    parts = []
    for short, name, mask, have in ((pre_short, "before ingress", t < t1, pre_min),
                                    (post_short, "after egress", t > t4, post_min)):
        if not short or not mask.any():
            continue
        n = int(mask.sum())
        try:
            if jd is not None and len(jd) == len(t):
                span = (f"{jd_to_local(float(jd[mask].min())).strftime('%H:%M')}–"
                        f"{jd_to_local(float(jd[mask].max())).strftime('%H:%M')}")
            else:
                ref = t1 if name == "before ingress" else t4
                span = (f"{(t[mask].min() - ref) * 1440:+.0f} to {(t[mask].max() - ref) * 1440:+.0f} min from "
                        + ("ingress" if name == "before ingress" else "egress"))
        except Exception:
            span = ""
        head = "No usable baseline" if have <= 0 else f"Only {have:.0f} min of usable baseline"
        parts.append(f"{head} {name}: the {n} frame{'s' if n != 1 else ''} there" + (f" ({span})" if span else "")
                     + f" {'were' if n != 1 else 'was'} flagged {label}.")
    return " ".join(parts)


# ---- Lookups (NASA Exoplanet Archive, ExoFOP) ----------------------------------------------------

def _http_get(url: str, timeout: float = 45) -> bytes:
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "SHOBS-P/2.1.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _f(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# Columns in the Planetary Systems Composite table (pscomppars). It has no reference-name column and may lack
# the uncertainty columns, so those are asked for separately and a failure there never blocks the lookup.
NEA_COLUMNS = ("pl_name,hostname,pl_orbper,pl_tranmid,pl_trandur,pl_trandep,pl_ratror,pl_ratdor,pl_imppar,"
               "pl_orbincl,st_teff,st_logg,st_rad,sy_vmag,sy_tmag,ra,dec,pl_orbper_reflink,pl_tranmid_reflink,tic_id")
NEA_ERR_COLUMNS = "pl_orbpererr1,pl_tranmiderr1,pl_ratrorerr1,pl_ratdorerr1,pl_impparerr1"

# Survey prefixes as the Archive spells them, so "Wasp-12 b" or "tres-3b" still matches.
_PREFIXES = {"wasp": "WASP", "tres": "TrES", "hat-p": "HAT-P", "hats": "HATS", "kelt": "KELT", "xo": "XO",
             "qatar": "Qatar", "kepler": "Kepler", "k2": "K2", "toi": "TOI", "corot": "CoRoT", "gj": "GJ",
             "hd": "HD", "hip": "HIP", "tic": "TIC", "ngts": "NGTS", "wts": "WTS", "kps": "KPS", "mascara": "MASCARA",
             "lhs": "LHS", "gl": "GL", "koi": "KOI", "toi-": "TOI-", "lp": "LP", "kmt": "KMT", "ogle": "OGLE",
             "trappist": "TRAPPIST", "wendelstein": "Wendelstein", "dmpp": "DMPP", "epic": "EPIC"}


def _name_variants(clean: str) -> list[str]:
    import re

    base = [clean]
    if len(clean) > 2 and clean[-1].isalpha() and clean[-2] not in " -" and clean[-2].isdigit():
        base.append(clean[:-1] + " " + clean[-1])            # "TrES-3b" -> "TrES-3 b"
    if clean and not clean[-1].isalpha():
        base.append(clean + " b")
    out = []
    for v in base:
        out.append(v)
        m = re.match(r"^([A-Za-z]+(?:-P)?)([- ]?)(.*)$", v, flags=re.IGNORECASE)
        if m:
            prefix = _PREFIXES.get(m.group(1).lower(), m.group(1).upper())
            rest = m.group(3)
            if rest and rest[-1].isalpha() and rest[-2:-1] == " ":
                rest = rest[:-1] + rest[-1].lower()               # planet letter is lower case
            out.append(prefix + m.group(2) + rest)
    return list(dict.fromkeys(x.replace("'", "") for x in out))


def _strip_tags(text: str) -> str:
    import re

    import html

    # 2.2.7: the Archive sends names double-escaped ("Kab&amp;aacute;th"): unescape twice.
    return html.unescape(html.unescape(re.sub(r"<[^>]+>", "", text or ""))).strip()


def _nea_query(adql: str) -> list[dict]:
    import csv
    import io
    import urllib.parse

    url = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync?" + urllib.parse.urlencode({"query": adql, "format": "csv"})
    return list(csv.DictReader(io.StringIO(_http_get(url).decode("utf-8", "replace"))))


class PlanetChoice(ValueError):
    """2.2.6: a star name with several planets: the UI asks which one (planets holds their Archive names).
    2.2.7: transiting holds the names that transit (empty when unknown)."""

    def __init__(self, system: str, planets: list, transiting=None):
        self.system = system
        self.planets = list(planets)
        self.transiting = set(transiting or [])
        super().__init__(f"{system} has {len(self.planets)} planets ({', '.join(self.planets)}). Choose one.")


def transiting_planets(system: str) -> set | None:
    """2.2.7: the names of a system's planets that transit (pscomppars tran_flag = 1), or None if unknown."""
    if not system or "'" in system:
        return None
    try:
        rows = _nea_query(f"select pl_name, tran_flag from pscomppars where hostname = '{system}'")
    except Exception:
        return None
    return {r.get("pl_name") for r in rows if str(r.get("tran_flag", "")).strip() in ("1", "1.0")}


def star_planets(star: str) -> dict | None:
    """2.2.7: a star typed as the Star ID: its planets from the Archive, transiting ones first.
    {"system": host, "planets": [names], "transiting": set} or None (unknown star, no planets, or offline)."""
    info = nea_resolve(star)
    if info is None or not info.get("planets"):
        return None
    tr = transiting_planets(info.get("system") or "") or set()
    planets = sorted(info["planets"], key=lambda n: (n not in tr, n))
    return {"system": info.get("system") or star, "planets": planets, "transiting": tr}


def nea_resolve(name: str) -> dict | None:
    """2.2.6: ask the NASA Exoplanet Archive's System Aliases service which name it uses now for a planet or star
    typed under any of its names (KOI-217 b, Kepler-71 b, KOI-217.01, TOI-4426.01, TIC/EPIC/Gaia/2MASS numbers,
    WASP-12b, "wasp 12 b"). Archive names change (Kepler-71 b is listed as KOI-217 b in 2026), so this replaces
    guessing spellings. Returns {"resolved": name or None, "system": host, "planets": [names], "aliases": [names of
    the resolved planet]}, or None when the service cannot be reached or does not know the name."""
    import json
    import urllib.parse

    clean = " ".join(name.strip().replace("'", "").split())
    if not clean:
        return None
    url = ("https://exoplanetarchive.ipac.caltech.edu/cgi-bin/Lookup/nph-aliaslookup.py?"
           + urllib.parse.urlencode({"objname": clean}))
    try:
        data = json.loads(_http_get(url, timeout=30).decode("utf-8", "replace"))
        return _parse_alias_reply(data)
    except Exception:
        return None   # unreachable, or a reply in an unexpected shape: fall back to the old name guesses


def _parse_alias_reply(data) -> dict | None:
    def obj(value):
        return value if isinstance(value, dict) else {}
    data = obj(data)
    manifest = obj(data.get("manifest"))
    if str(manifest.get("lookup_status", "")).upper() != "OK":
        return None
    planets_info = obj(obj(obj(obj(data.get("system")).get("objects")).get("planet_set")).get("planets"))
    planets = sorted(k for k in planets_info if isinstance(k, str))
    resolved = manifest.get("resolved_name") or None
    matched_planet = resolved in planets_info
    if not matched_planet:
        # The name is the star (or the system): a planet only when there is exactly one.
        resolved = planets[0] if len(planets) == 1 else None
    aliases = []
    if resolved:
        raw = obj(obj(planets_info.get(resolved)).get("alias_set")).get("aliases")
        aliases = [a for a in (raw if isinstance(raw, list) else []) if isinstance(a, str) and a != resolved]
    return {"resolved": resolved, "system": manifest.get("system_name") or "", "planets": planets,
            "aliases": aliases, "matched_planet": matched_planet}


def lookup_nea(name: str) -> dict:
    """Planet parameters from the NASA Exoplanet Archive (Planetary Systems Composite table). 2.2.6: the name is
    first resolved with the Archive's alias service; the old spelling guesses are kept as the fallback."""
    clean = " ".join(name.strip().replace("'", "").split())
    rows = []
    info = nea_resolve(clean)
    import re
    planet_like = bool(re.search(r"(\d\s?[b-i]|\.\d{1,2})$", clean, flags=re.IGNORECASE))
    if info is not None and planet_like and not info["matched_planet"]:
        # A planet-like name the service matched only to its star (a TOI candidate, say): don't hand back one of the
        # star's confirmed planets in its place, and don't ask; let the spelling guesses and ExoFOP try it.
        info = None
    if info is not None and info["resolved"] is None and len(info["planets"]) > 1:
        tr = transiting_planets(info["system"] or "") or set()
        raise PlanetChoice(info["system"] or clean, sorted(info["planets"], key=lambda n: (n not in tr, n)), tr)
    if info is not None and info["resolved"]:
        rows = _nea_query(f"select {NEA_COLUMNS} from pscomppars where pl_name = '{info['resolved']}'")
    kepler = None
    if not rows:
        kepler = _kepler_name_for_koi(clean)
        # Look for the name as typed AND its Kepler name: the Archive's name table maps KOI-217 b to Kepler-71 b,
        # but its planet tables list that planet as KOI-217 b, so the Kepler name alone found nothing.
        variants = list(dict.fromkeys(_name_variants(clean) + (_name_variants(kepler) if kepler else [])))
        where = " or ".join(f"pl_name = '{v}'" for v in variants)
        rows = _nea_query(f"select {NEA_COLUMNS} from pscomppars where {where}")
        if not rows:
            try:   # the same names in any capitalization
                rows = _nea_query(f"select {NEA_COLUMNS} from pscomppars where "
                                  + " or ".join(f"lower(pl_name) = '{v.lower()}'" for v in variants))
            except Exception:
                rows = []
    if not rows:
        raise ValueError(f"the NASA Exoplanet Archive has no planet called {name}"
                         + (f" (also looked for {kepler}, its Kepler name)" if kepler else "")
                         + ". Check the spelling, or type the planet's values by hand.")
    row = rows[0]
    pl_name = row.get("pl_name") or clean
    # Uncertainties: the composite table if it has them, else the default parameter set in the full table.
    errs = {}
    for table, extra in (("pscomppars", ""), ("ps", " and default_flag = 1")):
        try:
            got = _nea_query(f"select {NEA_ERR_COLUMNS} from {table} where pl_name = '{pl_name}'{extra}")
            if got:
                errs = got[0]
                break
        except Exception:
            continue
    refs = [_strip_tags(row.get(k, "")) for k in ("pl_tranmid_reflink", "pl_orbper_reflink")]
    eph_note = ""
    period, period_err = _f(row.get("pl_orbper")), _f(errs.get("pl_orbpererr1"))
    t0, t0_err = _f(row.get("pl_tranmid")), _f(errs.get("pl_tranmiderr1"))
    # 2.2: the composite table's ephemeris is not always the one that predicts tonight best (K2-113 b: an hour
    # off). Use the published ephemeris with the smallest predicted uncertainty now.
    try:
        best = most_precise_ephemeris(pl_name, now_jd(), (period, period_err, t0, t0_err, refs[0]))
    except Exception:
        best = None
    if best is not None:
        period, period_err, t0, t0_err = best["period"], best["period_err"], best["t0"], best["t0_err"]
        refs[0] = best["reference"] or refs[0]
        eph_note = (f"; most precise of {best['n']} published ephemerides, ±{best['sigma_min']:.0f} min now"
                    if best["n"] > 1 else "")
    planet = {
        "name": pl_name, "host": row.get("hostname") or "",
        "period": period, "period_err": period_err, "t0": t0, "t0_err": t0_err,
        "t14": (_f(row.get("pl_trandur")) or 0) / 24.0 or None,
        "depth": (_f(row.get("pl_trandep")) or 0) / 100.0 or None,
        "k": _f(row.get("pl_ratror")), "k_err": _f(errs.get("pl_ratrorerr1")),
        "a_rs": _f(row.get("pl_ratdor")), "a_rs_err": _f(errs.get("pl_ratdorerr1")),
        "b": _f(row.get("pl_imppar")), "b_err": _f(errs.get("pl_impparerr1")),
        "inc": _f(row.get("pl_orbincl")), "teff": _f(row.get("st_teff")), "logg": _f(row.get("st_logg")),
        "vmag": _f(row.get("sy_vmag")), "tmag": _f(row.get("sy_tmag")),
        "ra": _f(row.get("ra")), "dec": _f(row.get("dec")), "source": "NASA Exoplanet Archive",
        "tic": str(row.get("tic_id") or "").strip(),   # 2.2.15 (item 9): "TIC 468574941", for its ExoFOP page
        "reference": ("ephemeris: " + refs[0] if refs[0] else "") + eph_note,
        # 2.2.6: what was typed, and the planet's other names, so the Input page can say what it resolved to.
        "typed": clean, "aliases": list((info or {}).get("aliases") or []) if info and info.get("resolved") == pl_name else [],
    }
    return complete_planet(planet)


def _predicted_sigma_days(period, period_err, t0, t0_err, when_jd) -> float:
    if not period or not t0 or period_err is None or t0_err is None:
        return float("inf")
    n = round((when_jd - t0) / period)
    return math.hypot(abs(t0_err), abs(n) * abs(period_err))


def most_precise_ephemeris(pl_name: str, when_jd: float, composite=None) -> dict | None:
    """Of all published ephemerides for the planet (NASA Exoplanet Archive 'ps' table), the one whose predicted
    mid-transit time at when_jd has the smallest uncertainty. composite = (period, period_err, t0, t0_err, ref)
    from the composite table, kept when nothing beats it. None when the composite one is best or none is usable."""
    rows = _nea_query("select pl_orbper,pl_orbpererr1,pl_orbpererr2,pl_tranmid,pl_tranmiderr1,pl_tranmiderr2,"
                      f"pl_refname from ps where pl_name = '{pl_name}'")
    cands = []
    for row in rows:
        period, t0 = _f(row.get("pl_orbper")), _f(row.get("pl_tranmid"))
        if not period or not t0 or t0 < 2.4e6:
            continue
        perrs = [abs(v) for v in (_f(row.get("pl_orbpererr1")), _f(row.get("pl_orbpererr2"))) if v is not None]
        terrs = [abs(v) for v in (_f(row.get("pl_tranmiderr1")), _f(row.get("pl_tranmiderr2"))) if v is not None]
        if not perrs or not terrs:
            continue
        perr, terr = max(perrs), max(terrs)
        if perr <= 0 or terr <= 0:
            continue
        sigma = _predicted_sigma_days(period, perr, t0, terr, when_jd)
        cands.append((sigma, {"period": period, "period_err": perr, "t0": t0, "t0_err": terr,
                              "reference": _strip_tags(row.get("pl_refname", ""))}))
    if not cands:
        return None
    cands.sort(key=lambda c: c[0])
    sigma, best = cands[0]
    if composite is not None:
        base = _predicted_sigma_days(composite[0], composite[1], composite[2], composite[3], when_jd)
        if base <= sigma * 1.05:
            return None
    best["sigma_min"] = sigma * 1440
    best["n"] = len(cands)
    return best


def _kepler_name_for_koi(name: str) -> str | None:
    """'KOI-217 b' or 'KOI-217.01' -> 'Kepler-71 b' through the Exoplanet Archive's keplernames table."""
    import re

    m = re.match(r"(?i)^KOI[- ]?(\d+)(?:\.(\d+)|\s*([b-z]))?$", name.strip())
    if not m:
        return None
    sub = int(m.group(2)) if m.group(2) else (ord(m.group(3).lower()) - ord("a") if m.group(3) else 1)
    koi = f"K{int(m.group(1)):05d}.{sub:02d}"
    try:
        rows = _nea_query(f"select kepler_name from keplernames where koi_name = '{koi}'")
    except Exception:
        return None
    found = (rows[0].get("kepler_name") or "").strip() if rows else ""
    return found or None


def lookup_exofop_toi(name: str) -> dict:
    """A TESS Object of Interest from ExoFOP (for candidates not yet in the Exoplanet Archive)."""
    import csv
    import io
    import re

    m = re.search(r"(\d+)(?:\.(\d+))?\s*([b-z])?\s*$", name.strip())
    if not m:
        raise ValueError(f"{name} is not a TOI number")
    if m.group(2):
        want = f"{m.group(1)}.{m.group(2)}"
    else:
        sub = (ord(m.group(3).lower()) - ord("a")) if m.group(3) else 1
        want = f"{m.group(1)}.{sub:02d}"
    url = f"https://exofop.ipac.caltech.edu/tess/download_toi.php?toi={m.group(1)}&output=csv"
    text = _http_get(url).decode("utf-8", "replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError(f"ExoFOP has no TOI {m.group(1)}")
    row = next((r for r in rows if str(r.get("TOI", "")).strip() == want), None)
    if row is None:
        raise ValueError(f"ExoFOP has no TOI {want}")

    def g(*keys):
        for key in keys:
            if key in row and str(row[key]).strip():
                return _f(row[key])
        return None

    depth_ppm = g("Depth (ppm)")
    planet = {
        "name": f"TOI-{row.get('TOI', want)}", "host": f"TIC {row.get('TIC ID', '')}",
        "tic": f"TIC {row.get('TIC ID', '')}" if str(row.get("TIC ID", "")).strip() else "",   # 2.2.15 (item 9)
        "period": g("Period (days)"), "period_err": g("Period (days) err", "Period (days) Error"),
        "t0": g("Epoch (BJD)"), "t0_err": g("Epoch (BJD) err", "Epoch (BJD) Error"),
        "t14": (g("Duration (hours)") or 0) / 24.0 or None,
        "depth": depth_ppm / 1e6 if depth_ppm else None, "k": math.sqrt(depth_ppm / 1e6) if depth_ppm else None,
        "teff": g("Stellar Eff Temp (K)"), "logg": g("Stellar log(g) (cm/s^2)"), "tmag": g("TESS Mag"),
        "ra": None, "dec": None, "source": "ExoFOP TOI", "reference": "ExoFOP-TESS TOI catalog",
    }
    return complete_planet(planet)


def lookup_planet(name: str) -> dict:
    errors = []
    try:
        return lookup_nea(name)
    except PlanetChoice:
        raise
    except Exception as exc:
        errors.append(f"NASA Exoplanet Archive: {exc}")
    if "TOI" in name.upper():
        try:
            return lookup_exofop_toi(name)
        except Exception as exc:
            errors.append(f"ExoFOP: {exc}")
    raise ValueError("\n".join(errors))


def exofop_ids(planet: dict | None, *names: str) -> tuple[str, str]:
    """2.2.15 (item 9): (TOI number, TIC number) for the planet's ExoFOP-TESS page, '' when not known. A TOI named as
    the planet or its host (or typed) comes first, then the TIC (lookup's tic_id, or a "TIC nnn" name), then a TOI
    among the planet's other names (a confirmed planet that was a TOI)."""
    import re

    p = planet or {}
    own = [str(v or "") for v in (p.get("name"), p.get("host"), p.get("typed"), *names)]
    aliases = [str(a or "") for a in (p.get("aliases") or [])]

    def toi_in(texts):
        for t in texts:
            m = re.search(r"(?i)\bTOI[-\s]?(\d+)", t)
            if m and int(m.group(1)) > 0:
                return m.group(1)
        return ""

    def tic_in(texts):
        for t in texts:
            m = re.search(r"(?i)\bTIC[-\s]?(\d+)", t)
            if m and int(m.group(1)) > 0:
                return m.group(1)
        return ""

    toi = toi_in(own)
    tic = tic_in([str(p.get("tic") or "")] + own + aliases)
    if not toi and not tic:
        toi = toi_in(aliases)
    return toi, tic


def complete_planet(p: dict) -> dict:
    """Fill what can be derived: k from depth, b from inclination, a/R* from duration."""
    if p.get("period") is None:
        raise ValueError("the catalog entry has no period")
    if p.get("k") is None and p.get("depth"):
        p["k"] = math.sqrt(p["depth"])
    if p.get("k"):
        # 2.2.13: the reference depth is (Rp/R*)² whenever Rp/R* is known (see published_depth). A listed depth that
        # disagrees by more than 20% is kept as depth_listed, with a note for the fit's cautions.
        k2 = p["k"] ** 2
        listed = p.get("depth")
        if listed and abs(listed / k2 - 1.0) > 0.20 and not p.get("depth_note"):
            src = str(p.get("source") or "")
            who = ("The depth typed on the Input page" if p.get("depth_typed")
                   else "The Archive's listed depth" if "Archive" in src else "The listed depth")
            p["depth_listed"] = listed
            p["depth_note"] = (f"{who}, {listed * 100:.2f}%, disagrees with its Rp/R* ({p['k']:.4f} → "
                               f"{k2 * 100:.2f}%); SHOBS-P compares with (Rp/R*)².")
        p["depth"] = k2
    if p.get("b") is None and p.get("inc") is not None and p.get("a_rs"):
        p["b"] = p["a_rs"] * math.cos(math.radians(p["inc"]))
    if p.get("a_rs") is None and p.get("t14"):
        p["a_rs"] = _a_rs_from_duration(p["period"], p["t14"], p.get("k") or 0.1, p.get("b") or 0.3)
        p["a_rs_derived"] = True
    if p.get("t14") is None and p.get("a_rs"):
        p["t14"] = total_duration(p["period"], p["a_rs"], min(p.get("b") or 0.3, 0.99), p.get("k") or 0.1)
    return p


# ---- Planner (low-precision astronomy, good to a minute or two) -----------------------------------

def _gmst_deg(jd: np.ndarray) -> np.ndarray:
    d = np.asarray(jd) - 2451545.0
    return (280.46061837 + 360.98564736629 * d) % 360.0


def sun_radec(jd):
    """Low-precision Sun (Astronomical Almanac): RA, Dec in degrees, distance in au."""
    n = np.asarray(jd) - 2451545.0
    L = np.radians((280.460 + 0.9856474 * n) % 360)
    g = np.radians((357.528 + 0.9856003 * n) % 360)
    lam = L + np.radians(1.915) * np.sin(g) + np.radians(0.020) * np.sin(2 * g)
    eps = np.radians(23.439 - 4e-7 * n)
    ra = np.degrees(np.arctan2(np.cos(eps) * np.sin(lam), np.cos(lam))) % 360
    dec = np.degrees(np.arcsin(np.sin(eps) * np.sin(lam)))
    dist = 1.00014 - 0.01671 * np.cos(g) - 0.00014 * np.cos(2 * g)
    return ra, dec, dist


def moon_radec(jd):
    """Low-precision Moon (about 0.3 degrees): RA, Dec in degrees, and illuminated fraction."""
    d = np.asarray(jd) - 2451545.0
    L = np.radians((218.316 + 13.176396 * d) % 360)
    M = np.radians((134.963 + 13.064993 * d) % 360)
    F = np.radians((93.272 + 13.229350 * d) % 360)
    lam = L + np.radians(6.289) * np.sin(M)
    beta = np.radians(5.128) * np.sin(F)
    eps = np.radians(23.439)
    ra = np.degrees(np.arctan2(np.sin(lam) * np.cos(eps) - np.tan(beta) * np.sin(eps), np.cos(lam))) % 360
    dec = np.degrees(np.arcsin(np.sin(beta) * np.cos(eps) + np.cos(beta) * np.sin(eps) * np.sin(lam)))
    sra, sdec, _ = sun_radec(jd)
    elong = _sep_deg(ra, dec, sra, sdec)
    illum = (1 - np.cos(np.radians(elong))) / 2
    return ra, dec, illum


def _sep_deg(ra1, dec1, ra2, dec2):
    r1, d1, r2, d2 = map(np.radians, (ra1, dec1, ra2, dec2))
    c = np.sin(d1) * np.sin(d2) + np.cos(d1) * np.cos(d2) * np.cos(r1 - r2)
    return np.degrees(np.arccos(np.clip(c, -1, 1)))


def altitude(ra_deg, dec_deg, lat_deg, lon_deg, jd):
    lst = (_gmst_deg(jd) + lon_deg) % 360
    ha = np.radians(lst - ra_deg)
    lat, dec = math.radians(lat_deg), np.radians(dec_deg)
    s = np.sin(lat) * np.sin(dec) + np.cos(lat) * np.cos(dec) * np.cos(ha)
    return np.degrees(np.arcsin(np.clip(s, -1, 1)))


def bjd_minus_jd_days(jd, ra_deg, dec_deg) -> np.ndarray:
    """Approximate BJD_TDB − JD_UTC (days): Roemer delay from the Earth's position plus TDB − UTC.
    Good to a few seconds; used by the planner only (fits use Astropy's exact conversion)."""
    sra, sdec, dist = sun_radec(jd)
    # Earth's heliocentric vector is minus the Sun's geocentric vector.
    ex = -dist * np.cos(np.radians(sdec)) * np.cos(np.radians(sra))
    ey = -dist * np.cos(np.radians(sdec)) * np.sin(np.radians(sra))
    ez = -dist * np.sin(np.radians(sdec))
    nx = math.cos(math.radians(dec_deg)) * math.cos(math.radians(ra_deg))
    ny = math.cos(math.radians(dec_deg)) * math.sin(math.radians(ra_deg))
    nz = math.sin(math.radians(dec_deg))
    return ((ex * nx + ey * ny + ez * nz) * 499.004784 + 69.184) / 86400.0


def plan_transits(planet: dict, ra_deg: float, dec_deg: float, lat: float, lon: float, start_jd: float,
                  days: float = 30.0, min_alt: float = 25.0, sun_alt: float = -12.0,
                  baseline_min: float = 60.0) -> list[dict]:
    """Transits in a date range, with the target's altitude and the sky darkness at ingress, mid, and egress.

    'full' means ingress through egress plus the baseline on both sides are all above min_alt in the dark;
    'transit only' means the transit is but the baseline is not; 'partial' means part of the transit is."""
    period, t0 = planet["period"], planet["t0"]
    t14 = planet.get("t14") or total_duration(period, planet.get("a_rs") or 10, min(planet.get("b") or 0.3, 0.99),
                                              planet.get("k") or 0.1)
    n0 = math.ceil((start_jd - t0) / period)
    n1 = math.floor((start_jd + days - t0) / period)
    events = []
    for n in range(n0, n1 + 1):
        tc_bjd = t0 + n * period
        tc = tc_bjd - float(bjd_minus_jd_days(tc_bjd, ra_deg, dec_deg))
        sig = math.hypot(planet.get("t0_err") or 0.0, abs(n) * (planet.get("period_err") or 0.0)) * 1440
        base = baseline_min / 1440.0
        grid = np.linspace(tc - t14 / 2 - base, tc + t14 / 2 + base, 61)
        alt = altitude(ra_deg, dec_deg, lat, lon, grid)
        salt = altitude(*sun_radec(grid)[:2], lat, lon, grid)
        ok = (alt >= min_alt) & (salt <= sun_alt)
        in_tr = (grid >= tc - t14 / 2) & (grid <= tc + t14 / 2)
        if ok.all():
            status = "full"
        elif ok[in_tr].all():
            status = "transit only"
        elif ok[in_tr].any():
            status = "partial"
        else:
            continue
        mra, mdec, illum = moon_radec(tc)
        events.append({
            "epoch": n, "tc_bjd": tc_bjd, "tc_jd": tc, "t1_jd": tc - t14 / 2, "t4_jd": tc + t14 / 2,
            "alt_t1": float(altitude(ra_deg, dec_deg, lat, lon, tc - t14 / 2)),
            "alt_tc": float(altitude(ra_deg, dec_deg, lat, lon, tc)),
            "alt_t4": float(altitude(ra_deg, dec_deg, lat, lon, tc + t14 / 2)),
            "sun_tc": float(altitude(*sun_radec(tc)[:2], lat, lon, tc)),
            "moon_illum": float(illum), "moon_sep": float(_sep_deg(mra, mdec, ra_deg, dec_deg)),
            "status": status, "sigma_min": sig, "t14": t14,
        })
    return events


def jd_to_local(jd: float) -> datetime:
    """A JD (UTC) as a local, timezone-aware datetime (the computer's time zone, with daylight saving)."""
    return datetime.fromtimestamp((jd - 2440587.5) * 86400.0, tz=timezone.utc).astimezone()


def now_jd() -> float:
    return datetime.now(tz=timezone.utc).timestamp() / 86400.0 + 2440587.5


# ---- Nearby eclipsing binary (NEB) arithmetic ------------------------------------------------------

def neb_required_depth(depth: float, target_mag: float, neighbor_mag: float) -> float:
    """Fractional depth a neighbor would need to make the whole signal, if the catalog depth was measured
    in an aperture holding both stars (as TESS's does): depth × (F_target + F_neighbor) / F_neighbor."""
    ratio = 10 ** (-0.4 * (neighbor_mag - target_mag))
    return depth * (1.0 + ratio) / ratio


def neb_measure(t, rel_flux, tc, t14, cadence_days=None) -> tuple[float, float]:
    """In-transit drop of a star's light curve (fraction) and its error, from in vs out-of-transit means."""
    t = np.asarray(t, dtype=float)
    f = np.asarray(rel_flux, dtype=float)
    ok = np.isfinite(t) & np.isfinite(f)
    t, f = t[ok], f[ok]
    inside = np.abs(t - tc) < 0.35 * t14
    outside = np.abs(t - tc) > 0.5 * t14 + 10.0 / 1440
    if inside.sum() < 3 or outside.sum() < 5:
        return float("nan"), float("nan")
    a, b = f[outside], f[inside]
    drop = 1.0 - np.median(b) / np.median(a)
    err = math.hypot(1.4826 * np.median(np.abs(a - np.median(a))) / math.sqrt(len(a)),
                     1.4826 * np.median(np.abs(b - np.median(b))) / math.sqrt(len(b))) / np.median(a)
    return float(drop), float(err)


# ---- Report files -----------------------------------------------------------------------------------

def parse_exoplanet_report(text: str) -> dict:
    """An AAVSO exoplanet report (as written by EXOTIC or SHOBS-P): header, priors, results, and the
    light curve (DATE = BJD_TDB, DIFF = relative flux, ERR, DETREND_1 usually airmass)."""
    header, cols, rows = {}, None, []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            body = line[1:]
            if "=" in body and not body.startswith(" "):
                key, value = body.split("=", 1)
                header[key.strip()] = value.strip()
            elif body.upper().startswith("DATE,"):
                cols = [c.strip().upper() for c in body.split(",")]
            continue
        parts = [x.strip() for x in line.split(",")]
        try:
            rows.append([float("nan") if x.lower() in ("", "na", "nan", "null") else float(x) for x in parts])
        except ValueError:
            continue
    if header.get("TYPE", "").upper() != "EXOPLANET":
        raise ValueError("this is not an AAVSO exoplanet report (#TYPE=EXOPLANET); variable-star reports "
                         "have no transit fit to compare")
    if not rows:
        raise ValueError("the report has no data rows")
    width = max(len(r) for r in rows)
    data = np.array([r + [float("nan")] * (width - len(r)) for r in rows], dtype=float)
    cols = cols or ["DATE", "DIFF", "ERR", "DETREND_1", "DETREND_2"][: data.shape[1]]

    def col(name):
        return data[:, cols.index(name)] if name in cols and cols.index(name) < data.shape[1] else None

    out = {"header": header, "t": col("DATE"), "flux": col("DIFF"), "err": col("ERR"),
           "airmass": None, "priors": _parse_pm(header.get("PRIORS", "")),
           "results": _parse_pm(header.get("RESULTS", ""))}
    detrends = header.get("DETREND_PARAMETERS", "").upper()
    if "AIRMASS" in detrends and col("DETREND_1") is not None and np.isfinite(col("DETREND_1")).any():
        out["airmass"] = col("DETREND_1")
    for key in ("RESULTS-XC",):
        if key in header:
            try:
                xc = json.loads(header[key])
                for name, entry in xc.items():
                    if isinstance(entry, dict) and "value" in entry:
                        out["results"].setdefault(name, (_f(entry.get("value")), _f(entry.get("uncertainty"))))
            except Exception:
                pass
    return out


def _parse_pm(text: str) -> dict:
    """'Tc=2461287.64788 +/- 0.00032,Rp/R*=0.357 +/- 0.050,ecc=0' -> {name: (value, error)}."""
    out = {}
    for part in text.split(","):
        if "=" not in part:
            continue
        name, rest = part.split("=", 1)
        bits = rest.split("+/-")
        out[name.strip()] = (_f(bits[0]), _f(bits[1]) if len(bits) > 1 else None)
    return out


def planet_from_report(rep: dict) -> dict:
    """Published-shape priors from a report's #PRIORS line (EXOTIC writes the values it used)."""
    pri = rep["priors"]
    h = rep["header"]

    def v(name):
        return (pri.get(name) or (None, None))[0]

    def e(name):
        return (pri.get(name) or (None, None))[1]

    planet = {"name": h.get("EXOPLANET_NAME", ""), "host": h.get("STAR_NAME", ""), "period": v("Period"),
              "period_err": e("Period"), "k": v("Rp/R*"), "a_rs": v("a/R*"), "a_rs_err": e("a/R*"),
              "inc": v("inc"), "source": "the report's priors"}
    if planet["inc"] is not None and planet["a_rs"]:
        planet["b"] = planet["a_rs"] * math.cos(math.radians(planet["inc"]))
        if e("inc"):
            planet["b_err"] = planet["a_rs"] * math.sin(math.radians(planet["inc"])) * math.radians(e("inc"))
    for key in ("QC-XC",):
        if key in h:
            try:
                qc = json.loads(h[key])
                planet["t0"] = _f(qc.get("expected_tmid"))
                planet["t0_err"] = _f(qc.get("expected_tmid_unc"))
                if planet["t0"]:
                    planet["eph_source"] = "the report's QC-XC line (EXOTIC's predicted mid-transit)"
            except Exception:
                pass
    return complete_planet(planet)


def aavso_exoplanet_report(result: dict, meta: dict) -> str:
    """An AAVSO exoplanet report in the format EXOTIC writes (#TYPE=EXOPLANET), from a SHOBS-P fit.
    Check AAVSO's current exoplanet-section instructions before uploading."""
    s, pl = result["summary"], result["planet"]

    def pm(name, fmt):
        return f"{fmt.format(s[name][0])} +/- {fmt.format(s[name][1])}" if name in s else "na"

    priors = [f"Period={result['period']:.9f} +/- {pl.get('period_err') or 0:.9f}"]
    if "k" in (pl or {}) and pl.get("k"):
        priors.append(f"Rp/R*={pl['k']:.4f}")
    for name, label, fmt in (("a_rs", "a/R*", "{:.3f}"), ("b", "b", "{:.3f}")):
        if name in result["priors"]:
            mean, sigma = result["priors"][name]
            priors.append(f"{label}={fmt.format(mean)} +/- {fmt.format(sigma)}")
    priors.append("ecc=0")
    u1, u2, sld = result["ld_prior"]
    priors.append(f"u1={u1:.3f} +/- {sld:.3f},u2={u2:.3f} +/- {sld:.3f}")
    results = [f"Tc={pm('tc', '{:.5f}')}", f"Rp/R*={pm('k', '{:.4f}')}", f"a/R*={pm('a_rs', '{:.3f}')}",
               f"b={pm('b', '{:.3f}')}", f"inc={pm('inc', '{:.2f}')}", f"Depth={pm('depth', '{:.5f}')}",
               f"Duration={pm('t14', '{:.4f}')}"]
    lines = [
        "#TYPE=EXOPLANET", f"#OBSCODE={meta.get('obscode', '')}", "#SECONDARY_OBSCODES=",
        f"#SOFTWARE=SHOBS-P {meta.get('version', '')}", "#DELIM=,", "#DATE_TYPE=BJD_TDB",
        f"#OBSDATE={meta.get('obsdate', '')}", "#OBSTYPE=CCD", f"#STAR_NAME={pl.get('host') or meta.get('star', '')}",
        f"#EXOPLANET_NAME={pl.get('name', '')}", f"#BINNING={meta.get('binning', '1x1')}",
        f"#EXPOSURE_TIME={meta.get('exposure', '')}", f"#OBSLAT={meta.get('lat', '')}", f"#OBSLON={meta.get('lon', '')}",
        f"#OBSELEV={meta.get('elev', '')}", f"#NOTES={meta.get('notes', '')}",
        "#DETREND_PARAMETERS=" + (", ".join(d.upper() for d in result["detrend"]) or "NONE"),
        "#MEASUREMENT_TYPE=Rnflux", f"#FILTER={meta.get('filter', 'CV')}",
        "#PRIORS=" + ",".join(priors), "#RESULTS=" + ",".join(results),
        _created(),
        "#DATE,DIFF,ERR,DETREND_1",
    ]
    for t, f, e, a in zip(result["t"], result["flux"], result["err_scaled"], result["airmass"]):
        lines.append(f"{t:.8f},{f:.7f},{e:.7f},{a:.7f}" if math.isfinite(a) else f"{t:.8f},{f:.7f},{e:.7f},na")
    return "\n".join(lines) + "\n"


def _dtg() -> str:
    import photometry_core as core
    return core.dtg()


def _created() -> str:
    import photometry_core as core
    return core.created_line()


def transit_coverage(t, tc: float | None, t14: float | None) -> str:
    """2.2.15 (item 11): how much of the transit the data cover, for ExoFOP: "Full" (from before ingress to after
    egress, the fitted Tc ± T14/2), "Ingress" or "Egress" (only that contact), "Partial" (inside the transit but
    neither contact: only the bottom, say), "None (data miss the predicted transit)" when no point falls in it.
    A contact counts as covered only with a point within 2 × the median cadence (at least 10 min) of it, so a gap
    across the contact does not count. '' when Tc or T14 is unknown."""
    t = np.asarray(t, dtype=float)
    t = np.sort(t[np.isfinite(t)])
    if not t.size or tc is None or not t14 or not math.isfinite(tc) or not math.isfinite(t14) or t14 <= 0:
        return ""
    t1, t4 = tc - t14 / 2.0, tc + t14 / 2.0
    start, end = float(t[0]), float(t[-1])
    if not ((t >= t1) & (t <= t4)).any():
        return "None (data miss the predicted transit)"
    cadence = float(np.median(np.diff(t))) if t.size > 1 else 0.0
    reach = max(2.0 * cadence, 10.0 / 1440.0)

    def covered(contact):
        return start < contact < end and bool((np.abs(t - contact) <= reach).any())
    has_ingress, has_egress = covered(t1), covered(t4)
    if has_ingress and has_egress:
        return "Full"
    if has_ingress:
        return "Ingress"
    if has_egress:
        return "Egress"
    return "Partial"


def exofop_notes(result: dict, meta: dict, mono: bool = False) -> str:
    """2.2.15 (item 11): the "For ExoFOP" block of the package's fit file: telescope/camera from the headers and the
    Optics box (meta "telescope"; "not recorded" when neither says), filter, pixel scale ("/px), the photometry
    aperture in px and arcsec, coverage (Full / Ingress / Egress / Partial) and the result."""
    s = result["summary"]
    t = np.asarray(result["t"], dtype=float)
    scale = meta.get("scale_arcsec")
    radius = meta.get("radius_px")
    if scale:
        how = []
        if meta.get("bin"):
            how.append(f"Bin {meta['bin']}")
        if meta.get("camera_bin", 1) and meta.get("camera_bin", 1) > 1:
            how.append(f"camera bin {meta['camera_bin']}")
        if meta.get("superpixel", 1) == 2:
            how.append("color superpixel")
        scale_text = f'{scale:.2f}"/px' + (f" ({', '.join(how)})" if how else "")
    else:
        scale_text = "not recorded"
    if radius:
        ap_text = f"r {radius:g} px" + (f' = {radius * scale:.1f}"' if scale else "")
        if meta.get("sky"):
            ap_text += f", sky {meta['sky']} px"
    else:
        ap_text = meta.get("aperture") or "not recorded"
    tc = s["tc"][0] if "tc" in s else None
    t14 = s["t14"][0] if "t14" in s else (result.get("planet") or {}).get("t14")
    cov = transit_coverage(t, tc, t14) or "not known"
    span = f" ({t.min():.5f} to {t.max():.5f} BJD_TDB)" if t.size else ""
    return ("\nFor ExoFOP (time-series upload)\n"
            f"  Telescope/camera: {meta.get('telescope') or 'not recorded'}\n"
            f"  Filter: {meta.get('filter', 'CV')} ("
            + ("monochrome camera" if mono else "one-shot color camera") + ", untransformed)\n"
            f"  Pixel scale: {scale_text}\n"
            f"  Photometric aperture: {ap_text}\n"
            f"  Coverage: {cov}{span}\n"
            f"  Result: Tc {s['tc'][0]:.5f} ± {s['tc'][1]:.5f}, depth "
            f"{s['depth'][0] * 1e6:.0f} ± {s['depth'][1] * 1e6:.0f} ppm\n")


def fit_text(result: dict, meta: dict) -> str:
    """Plain-text summary of a transit fit (ExoFOP package, and a copy for the observer's records)."""
    s, pl = result["summary"], result["planet"]
    ch = result["chain"]
    lines = [f"SHOBS-P {meta.get('version', '')} transit fit", f"Created        {_dtg()}", "",
             f"Planet         {pl.get('name', '')}   host {pl.get('host', '')}",
             f"Observer       {meta.get('obscode', '')}   site {meta.get('lat', '')}, {meta.get('lon', '')}",
             f"Night          {meta.get('obsdate', '')}   {result['n_points']} points, cadence "
             f"{result['cadence_days'] * 86400:.0f} s, filter {meta.get('filter', 'CV')}",
             f"Equipment      {meta.get('notes', '')}",
             f"Photometry     aperture {meta.get('aperture', '')}; comps {meta.get('comps', '')}"
             + (f"; {meta['scint']}" if meta.get("scint") else ""),
             f"Ephemeris      P {result['period']:.8f} d, "
             + (f"from {result['eph_source']}" if result.get("eph_source") else
                "none available (no O − C)" if result.get("tc_pred") is None else
                f"published values from {pl.get('source', '')} {pl.get('reference', '')}".rstrip()),
             f"Shape priors   from {pl.get('source', '')}", "",
             "Fitted values (MCMC median and 68% interval):"]
    for name in ("tc", "k", "depth", "a_rs", "b", "inc", "t14", "u1", "u2", "c_am", "c_t", "step"):
        if name in s:
            label = {"tc": "Tc (BJD_TDB)", "k": "Rp/R*", "depth": "Depth", "a_rs": "a/R*", "b": "b", "inc": "inc (deg)",
                     "t14": "T14 (days)", "u1": "u1", "u2": "u2", "c_am": "airmass slope", "c_t": "time slope",
                     "step": "flip step"}[name]
            v, e = s[name]
            fmt = "{:.6f}" if name == "tc" else "{:.5f}"
            lines.append(f"  {label:<16}{fmt.format(v)} ± {fmt.format(e)}")
    if result.get("tc_pred") is not None and result.get("tc_pred_err") is not None:
        oc = (result["best"]["tc"] - result["tc_pred"]) * 1440
        lines.append(f"  O − C           {oc:+.2f} min (epoch {result['epoch']}, predicted {result['tc_pred']:.6f} "
                     f"± {result['tc_pred_err'] * 1440:.2f} min)")
    lines += ["", "Priors           " + ", ".join(f"{k} {m:.4g} ± {sd:.2g}" for k, (m, sd) in result["priors"].items())
              + f"; limb darkening u1 {result['ld_prior'][0]:.2f}, u2 {result['ld_prior'][1]:.2f} ± {result['ld_prior'][2]:.2f}",
              f"Noise            rms {result['rms'] * 100:.3f}% per point"
              + (f" ({result['rms_bin10'] * 100:.3f}% per 10-min bin)" if result.get("rms_bin10") is not None else "")
              + f", reduced chi-square {result['rchi2']:.2f}, "
              f"red-noise beta {result['beta']:.2f}, error bars x{result['error_scale']:.2f}",
              "Detrending       " + (", ".join(result["detrend"]) or "none"),
              f"MCMC             {ch['walkers']} walkers, {ch['burn']} burn-in + {ch['steps']} steps, "
              f"acceptance {ch['acceptance']:.2f}"]
    ff = result.get("free_fit")
    if ff:
        lines.append(f"Free-shape check Rp/R* {ff['p']['k']:.4f}, a/R* {ff['p']['a_rs']:.2f}, b {ff['p']['b']:.3f}")
    lines += [f"Meridian flip    {ln}" if i == 0 else f"                 {ln}" for i, ln in enumerate(flip_lines(result))]
    wf = result.get("with_flagged")
    if wf:
        lines.append(f"Flagged frames   INCLUDED: {wf['n']} frames flagged {wf['label']}, error bars x{wf['factor']:g}")
    v = result.get("verdict") or {}
    if v.get("depth_unreliable"):
        lines.append("Verdict          timing usable, depth unreliable: " + v["depth_unreliable"])
    if result["flags"]:
        lines += ["", "Notes"] + [("  ! " if lvl == "warn" else "  - ") + text for lvl, text in result["flags"]]
    return "\n".join(lines) + "\n"


def flip_lines(result: dict) -> list:
    """2.2.13: the flip step in plain lines (fit text, ExoFOP notes): [] without a flip."""
    flip = (result or {}).get("flip")
    if not flip:
        return []
    st, se = flip["step"]
    out = [f"at BJD_TDB {flip['t_flip']:.5f} ({flip.get('source') or 'from the data'}); baseline step fitted jointly "
           f"with the transit: {st * 100:+.2f} +/- {se * 100:.2f}%"]
    c = flip.get("corr")
    if c is not None and math.isfinite(c):
        out.append(f"step-depth correlation {c:+.2f} (MCMC)"
                   + ("; the step and the depth trade off; depth not measurable from this night"
                      if abs(c) > FLIP_CORR_WARN else ""))
    ns = flip.get("nostep")
    if ns:
        out.append(f"without the step: depth {ns['depth'] * 100:.2f} +/- {ns['depth_err'] * 100:.2f}% (least squares), "
                   f"dBIC {ns['dbic']:+.0f} " + ("in favour of the step" if ns["dbic"] >= 0 else "against the step"))
    sd = flip.get("sides") or {}
    if sd:
        out.append(f"out-of-transit baseline {sd['before'][0]:.0f} min ({_frames(sd['before'][1])}) before the flip, "
                   f"{sd['after'][0]:.0f} min ({_frames(sd['after'][1])}) after"
                   + ("" if flip.get("baseline_ok") else f" (each side needs {FLIP_MIN_OOT_MIN:.0f} min and "
                                                         f"{FLIP_MIN_OOT_FRAMES} frames)"))
    return out


def flip_report_note(result: dict) -> str:
    """2.2.13: one ASCII line for an AAVSO report's NOTES. It names the flip time in a fixed form ("meridian flip at
    BJD_TDB 2461326.63467") that report_flip_time() reads back, so a report SHOBS-P wrote can be refitted with
    the step."""
    flip = (result or {}).get("flip")
    if not flip:
        return ""
    st, se = flip["step"]
    text = f"SHOBS-P: meridian flip at BJD_TDB {flip['t_flip']:.5f}; baseline step {st * 100:+.2f}+/-{se * 100:.2f}%"
    if flip.get("corr") is not None and math.isfinite(flip["corr"]):
        text += f", step-depth corr {flip['corr']:+.2f}"
    if flip.get("nostep"):
        text += f", depth without step {flip['nostep']['depth'] * 100:.2f}+/-{flip['nostep']['depth_err'] * 100:.2f}%"
    return text


def report_flip_time(header: dict) -> float | None:
    """2.2.13: the meridian flip time SHOBS-P wrote into a report's NOTES, or None."""
    m = re.search(r"meridian flip at BJD_TDB\s+(\d{7}\.\d+)", str((header or {}).get("NOTES", "")))
    return float(m.group(1)) if m else None


def flip_from_gap(t, min_gap_min: float = FLIP_GAP_MIN_MIN) -> float | None:
    """2.2.13: for a report the user says has a meridian flip (no flip segment in the data): the middle of the
    largest gap, if it is at least min_gap_min minutes and 5 cadences long; else None. Never used on its own:
    a gap can be clouds or a re-centring, so only the user's say-so makes it a flip."""
    t = np.sort(np.asarray(t, dtype=float)[np.isfinite(np.asarray(t, dtype=float))])
    if t.size < 12:
        return None
    dt = np.diff(t)
    k = int(np.argmax(dt))
    if dt[k] * 1440 < max(min_gap_min, 5 * float(np.median(dt)) * 1440):
        return None
    return float(t[k] + dt[k] / 2)


# ---- Tonight's transits (finder) ----------------------------------------------------------------------

CATALOG_FILE = __import__("os").path.join(__import__("os").path.expanduser("~"), ".shobs_p_transit_catalog.csv")
CATALOG_COLUMNS = "pl_name,hostname,pl_orbper,pl_tranmid,pl_trandur,pl_trandep,pl_ratror,ra,dec,sy_vmag,sy_tmag"


def load_transit_catalog(refresh: bool = False, max_age_days: float = 7.0) -> tuple[list[dict], str]:
    """Every known transiting planet with an ephemeris, from the NASA Exoplanet Archive (Planetary Systems
    Composite table). Kept in a local file and refreshed weekly. Returns (planets, note)."""
    import csv
    import io
    import os
    import time as _time

    text, note = None, ""
    fresh = os.path.exists(CATALOG_FILE) and (_time.time() - os.path.getmtime(CATALOG_FILE)) < max_age_days * 86400
    if refresh or not fresh:
        try:
            text = "\n".join(
                [",".join(CATALOG_COLUMNS.split(","))]
                + [",".join(_csv_cell(r.get(c, "")) for c in CATALOG_COLUMNS.split(","))
                   for r in _nea_query(f"select {CATALOG_COLUMNS} from pscomppars where pl_tranmid is not null "
                                       "and pl_orbper is not null")])
            with open(CATALOG_FILE, "w", encoding="utf-8") as fh:
                fh.write(text)
            note = "downloaded today from the NASA Exoplanet Archive"
        except Exception as exc:
            if not os.path.exists(CATALOG_FILE):
                raise ValueError(f"could not download the planet list ({exc}) and there is no saved copy")
            note = f"the Archive could not be reached ({exc}); using the saved copy"
            text = None
    if text is None:
        with open(CATALOG_FILE, encoding="utf-8") as fh:
            text = fh.read()
        if not note:
            age = (_time.time() - os.path.getmtime(CATALOG_FILE)) / 86400
            note = f"saved copy from {age:.0f} day(s) ago"
    planets = []
    for row in csv.DictReader(io.StringIO(text)):
        p = {"name": row.get("pl_name", ""), "host": row.get("hostname", ""), "period": _f(row.get("pl_orbper")),
             "t0": _f(row.get("pl_tranmid")), "t14": (_f(row.get("pl_trandur")) or 0) / 24.0 or None,
             "depth": (_f(row.get("pl_trandep")) or 0) / 100.0 or None, "k": _f(row.get("pl_ratror")),
             "ra": _f(row.get("ra")), "dec": _f(row.get("dec")), "vmag": _f(row.get("sy_vmag")),
             "tmag": _f(row.get("sy_tmag"))}
        if p["k"]:
            p["depth"] = p["k"] ** 2      # 2.2.13: (Rp/R*)², as everywhere else (see published_depth)
        if p["period"] and p["t0"] and p["ra"] is not None and p["dec"] is not None:
            planets.append(p)
    return planets, note


def _csv_cell(value) -> str:
    text = str(value if value is not None else "")
    return '"' + text.replace('"', '""') + '"' if ("," in text or '"' in text) else text


def night_window(date_local, lat: float, lon: float, sun_alt: float = -18.0):
    """Dark interval (JD UTC) for the night that starts on the local calendar date: from local noon to the next
    local noon, the stretch with the Sun below sun_alt. Returns (start, end) or None (no dark time)."""
    from datetime import datetime, time as dtime

    noon = datetime.combine(date_local, dtime(12, 0)).astimezone()
    jd0 = noon.timestamp() / 86400.0 + 2440587.5
    grid = jd0 + np.arange(0, 1.0, 2.0 / 1440)
    salt = altitude(*sun_radec(grid)[:2], lat, lon, grid)
    dark = np.where(salt <= sun_alt)[0]
    if dark.size == 0:
        return None
    return float(grid[dark[0]]), float(grid[dark[-1]]), jd0


def find_transits(planets: list[dict], date_local, lat: float, lon: float, min_alt: float = 30.0,
                  sun_alt: float = -18.0, baseline_min: float = 60.0, vmin: float = 9.0, vmax: float = 13.5,
                  min_depth: float = 0.005, min_moon_deg: float = 0.0, after_jd: float | None = None,
                  before_jd: float | None = None, avoid_meridian: bool = False) -> tuple[list[dict], dict]:
    """Transits on one night that can be observed whole: the window (ingress minus baseline to egress plus
    baseline) inside astronomical dark (or the sun_alt chosen), after/before the times asked, with the star
    above min_alt throughout, V in [vmin, vmax], depth at least min_depth. Returns (events, counts)."""
    win = night_window(date_local, lat, lon, sun_alt)
    counts = {"planets": len(planets), "no_duration": 0, "checked": 0}
    if win is None:
        return [], counts
    dark0, dark1, jd_noon = win
    lo = max(dark0, after_jd) if after_jd else dark0
    hi = min(dark1, before_jd) if before_jd else dark1
    base = baseline_min / 1440.0
    events = []
    mra, mdec, illum = moon_radec(0.5 * (dark0 + dark1))
    for p in planets:
        v = p.get("vmag")
        if v is None or not (vmin <= v <= vmax):
            continue
        if not p.get("depth") or p["depth"] < min_depth:
            continue
        t14 = p.get("t14")
        if not t14:
            counts["no_duration"] += 1
            continue
        counts["checked"] += 1
        corr = float(bjd_minus_jd_days(jd_noon + 0.5, p["ra"], p["dec"]))
        n0 = math.ceil((lo - t14 / 2 - base + corr - p["t0"]) / p["period"])
        n1 = math.floor((hi + t14 / 2 + base + corr - p["t0"]) / p["period"])
        for n in range(n0, n1 + 1):
            tc = p["t0"] + n * p["period"] - corr
            start, end = tc - t14 / 2 - base, tc + t14 / 2 + base
            if start < lo or end > hi:
                continue
            grid = np.linspace(start, end, 25)
            alt = altitude(p["ra"], p["dec"], lat, lon, grid)
            if alt.min() < min_alt:
                continue
            sep = float(_sep_deg(mra, mdec, p["ra"], p["dec"]))
            if min_moon_deg and sep < min_moon_deg:
                continue
            ha = ((_gmst_deg(grid) + lon - p["ra"] + 180.0) % 360.0) - 180.0
            flips = bool(np.any(np.diff(np.sign(ha)) > 0))
            if avoid_meridian and flips:
                continue
            events.append({"name": p["name"], "host": p.get("host", ""), "vmag": v, "depth": p["depth"],
                           "t14": t14, "start": start, "t1": tc - t14 / 2, "tc": tc, "t4": tc + t14 / 2, "end": end,
                           "alt_min": float(alt.min()), "alt_max": float(alt.max()), "moon_sep": sep,
                           "moon_illum": float(illum), "meridian": flips, "epoch": n})
    events.sort(key=lambda e: e["start"])
    counts["dark"] = (dark0, dark1)
    return events, counts


def swarthmore_url(lat: float, lon: float, min_alt: float = 30.0, baseline_hrs: float = 1.0, min_depth_ppt: float = 5.0,
                   vmax: float = 13.5, twilight: int = -18, days: int = 1, start_date: str = "today") -> str:
    """A link to the Swarthmore Transit Finder (E. Jensen, Tapir) with this site and these limits filled in."""
    import time as _time
    import urllib.parse

    std_hours = -_time.timezone / 3600.0
    zones = {-5: "EST5EDT", -6: "CST6CDT", -7: "MST7MDT", -8: "PST8PDT", -10: "HST", -9: "AKST9AKDT"}
    tz = zones.get(int(round(std_hours)))
    params = {"single_object": 0, "observatory_string": "Specified_Lat_Long", "use_utc": 0 if tz else 1,
              "observatory_latitude": f"{lat:.5f}", "observatory_longitude": f"{lon % 360.0:.5f}",
              "timezone": tz or "UTC", "start_date": start_date, "days_to_print": days, "days_in_past": 0,
              "minimum_start_elevation": f"{min_alt:g}", "and_vs_or": "and", "minimum_end_elevation": f"{min_alt:g}",
              "baseline_hrs": f"{baseline_hrs:g}", "show_unc": 1, "minimum_depth": f"{min_depth_ppt:g}",
              "maximum_V_mag": f"{vmax:g}", "print_html": 1, "twilight": int(twilight), "max_airmass": 2.4}
    return "https://astro.swarthmore.edu/transits/print_transits.cgi?" + urllib.parse.urlencode(params)
