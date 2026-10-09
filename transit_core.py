"""Transit tools for SHOBS-P (2.1): model, fit (least squares + MCMC), red noise, checks, ephemeris lookups,
planner, nearby-eclipsing-binary arithmetic, and report files.

NumPy only, plus SciPy's least_squares for the starting fit. No Astropy here, so everything in this
module also runs on a report loaded from disk (an EXOTIC / AAVSO exoplanet file) without frames.
"""
from __future__ import annotations

import json
import math
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

PARAMS = ("tc", "k", "a_rs", "b", "q1", "q2", "c0", "c_am", "c_t")
PARAM_LABELS = {"tc": "Mid-transit Tc (BJD_TDB)", "k": "Rp/R*", "a_rs": "a/R*", "b": "Impact parameter b",
                "q1": "q1 (limb darkening)", "q2": "q2 (limb darkening)", "c0": "Baseline",
                "c_am": "Airmass slope", "c_t": "Time slope (per day)"}


class TransitProblem:
    """Data, fixed values, and priors for one transit light curve.

    Model: flux = transit(t) * (c0 + c_am * (airmass - mean) + c_t * (t - t_mid)).
    priors: {name: (mean, sigma)} Gaussian priors. Bounds keep everything physical."""

    def __init__(self, t, flux, err, airmass, period, priors: dict, free: tuple, start: dict,
                 detrend_airmass=True, detrend_time=False, ld_prior=None):
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
        self.free = names
        self.fixed = {p: float(start.get(p, 0.0)) for p in PARAMS}
        self.fixed.setdefault("c0", 1.0)

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
        t = self.t if t is None else np.asarray(t, dtype=float)
        am = self.am if am is None else am
        return p["c0"] + p["c_am"] * am + p["c_t"] * (t - self.t_mid)

    def in_bounds(self, p: dict) -> bool:
        if not (0.005 < p["k"] < 0.6 and 1.2 < p["a_rs"] < 200 and 0.0 <= p["b"] < 1.0 + p["k"]):
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
             "c_t": 1e-3}
        return np.array([s[p] for p in self.free])

    def bounds(self):
        lo = {"tc": self.t.min() - 0.2, "k": 0.006, "a_rs": 1.3, "b": 0.0, "q1": 1e-4, "q2": 1e-4, "c0": 0.5,
              "c_am": -1.0, "c_t": -1.0}
        hi = {"tc": self.t.max() + 0.2, "k": 0.59, "a_rs": 199.0, "b": 1.5, "q1": 0.9999, "q2": 0.9999, "c0": 1.5,
              "c_am": 1.0, "c_t": 1.0}
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
    p = problem.full(best.x)
    r = (problem.flux - problem.model(p)) / problem.err
    return best.x, float(np.dot(r, r))


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
        if progress is not None and (step % 25 == 0 or step == total - 1):
            progress(step + 1, total)
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


def fit_transit(t, flux, err, airmass, planet: dict, detrend_airmass=True, detrend_time=False,
                mcmc_steps: int = 3000, progress=None, cancel=None, log=None) -> dict:
    """The full SHOBS-P transit fit.

    1. Least squares with the published shape as Gaussian priors (a/R*, b) and broad limb-darkening priors.
    2. Errors rescaled so the reduced chi-square is 1, then multiplied by the red-noise beta.
    3. MCMC (always) with the rescaled errors, for the reported values and uncertainties.
    4. A second, free fit (no priors on a/R* and b) as a check of the shape.
    """
    say = log or (lambda _m: None)
    t = np.asarray(t, dtype=float)
    flux = np.asarray(flux, dtype=float)
    err = np.asarray(err, dtype=float)
    am = np.asarray(airmass, dtype=float) if airmass is not None else np.full_like(t, np.nan)
    good = np.isfinite(t) & np.isfinite(flux) & np.isfinite(err) & (err > 0)
    if detrend_airmass and np.isfinite(am).any():
        good &= np.isfinite(am)
    t, flux, err, am = t[good], flux[good], err[good], am[good]
    order = np.argsort(t)
    t, flux, err, am = t[order], flux[order], err[order], am[order]
    if len(t) < 20:
        raise ValueError(f"only {len(t)} usable points; a transit fit needs at least 20")
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
    tc0 = planet.get("t0")
    if tc0:
        n = round((np.mean(t_abs) - tc0) / period)
        tc_pred = tc0 + n * period
        tc_pred_err = math.hypot(planet.get("t0_err") or 0.0, abs(n) * (planet.get("period_err") or 0.0))
    else:
        n, tc_pred, tc_pred_err = None, float(t_abs[np.argmin(flux)]), None
    tc_rel = tc_pred - t_ref
    k0 = planet.get("k") or (math.sqrt(planet["depth"]) if planet.get("depth") else 0.1)
    k0 = min(max(k0, 0.01), 0.5)
    b0 = planet.get("b") if planet.get("b") is not None else 0.3
    b0 = min(max(b0, 0.0), 1.0 + k0 - 0.01)
    a0 = planet.get("a_rs") or _a_rs_from_duration(period, planet.get("t14"), k0, min(b0, 0.9))
    a0 = max(a0, b0 + 0.5, 1.5)
    start = {"tc": tc_rel, "k": k0, "a_rs": a0, "b": b0, "q1": q1, "q2": q2, "c0": 1.0, "c_am": 0.0, "c_t": 0.0}
    priors = prepare_priors(planet, a0)
    free = ("tc", "k", "a_rs", "b", "q1", "q2", "c0", "c_am", "c_t")
    ld = (u1g, u2g, 0.1)
    cadence = float(np.median(np.diff(t))) if len(t) > 1 else 1e-3
    dur = total_duration(period, a0, min(b0, 0.99), k0)
    starts = [{"tc": tc_rel + d} for d in (-0.25 * dur, 0.25 * dur)] + [{"k": k0 * 1.2}, {"k": k0 * 0.8}]

    prob = TransitProblem(t, flux, err, am, period, priors, free, start, detrend_airmass, detrend_time, ld)
    say("Transit fit: least squares with the published shape as priors…")
    x, chi2 = best_fit(prob, starts)
    dof = max(len(t) - len(prob.free), 1)
    rchi = chi2 / dof
    resid = flux - prob.model(prob.full(x))
    beta = red_noise_beta(t, resid, cadence)
    scale = math.sqrt(max(rchi, 1.0)) * beta
    say(f"  reduced chi-square {rchi:.2f}; red-noise beta {beta:.2f}; error bars x{scale:.2f} for the MCMC")
    prob_s = TransitProblem(t, flux, err * scale, am, period, priors, free, prob.full(x), detrend_airmass,
                            detrend_time, ld)
    x_s, _ = best_fit(prob_s)
    n_burn = max(500, mcmc_steps // 3)
    say(f"  MCMC: {max(2 * len(prob_s.free) + 2, 20)} walkers, {n_burn} burn-in + {mcmc_steps} steps…")
    chain = run_mcmc(prob_s, x_s, n_walkers=20, n_burn=n_burn, n_steps=mcmc_steps, progress=progress, cancel=cancel)
    summary = summarize(prob_s, chain["flat"])
    best = prob_s.full(np.array([summary[p][0] for p in prob_s.free]))
    # Free shape check (no priors on a/R* and b), least squares only.
    free_fit = None
    try:
        prob_f = TransitProblem(t, flux, err * scale, am, period, {}, free, best, detrend_airmass, detrend_time, ld)
        xf, chi2f = best_fit(prob_f, [{"b": 0.1}, {"b": 0.5}, {"b": 0.8}, {"b": 0.95, "k": best["k"] * 1.3},
                                      {"b": min(1.0 + best["k"] * 1.5, 1.4), "k": best["k"] * 2.0}])
        pf = prob_f.full(xf)
        pf["tc"] += t_ref
        free_fit = {"p": pf, "chi2": chi2f, "depth": pf["k"] ** 2,
                    "t14": total_duration(period, pf["a_rs"], min(pf["b"], pf["a_rs"] * 0.999), pf["k"])}
    except Exception as exc:
        say(f"  free-shape check skipped: {exc}")
    model = prob_s.model(best)
    base = prob_s.baseline(best)
    resid = flux - model
    best["tc"] += t_ref
    summary["tc"] = (summary["tc"][0] + t_ref, summary["tc"][1])
    result = {
        "t": t_abs, "t_ref": t_ref, "flux": flux, "err": err, "err_scaled": err * scale, "airmass": am, "model": model,
        "baseline": base, "detrended": flux / base, "residuals": resid, "best": best, "summary": summary,
        "free_params": list(prob_s.free), "priors": priors, "ld_prior": ld, "rchi2": rchi, "beta": beta,
        "error_scale": scale, "rms": float(np.std(resid)), "cadence_days": cadence, "chain": chain,
        "tc_pred": tc_pred, "tc_pred_err": tc_pred_err, "epoch": n, "period": period, "planet": dict(planet),
        "free_fit": free_fit, "n_points": len(t), "flux_median": float(med),
        "detrend": [name for name, on in (("airmass", detrend_airmass and prob_s.has_airmass),
                                          ("time", detrend_time)) if on],
    }
    result["verdict"] = transit_verdict(result, prob_s)
    result["flags"] = transit_flags(result)
    v = result["verdict"]
    if v["state"] == "none":
        result["flags"].insert(0, ("warn", "NO TRANSIT MEASURED: " + v["reason"] + " The numbers are shown for "
                                           "reference only; do not report them."))
    elif v["state"] == "inconclusive":
        result["flags"].insert(0, ("warn", "INCONCLUSIVE. " + v["reason"].replace("Inconclusive. ", "", 1)
                                   + " Tc may still be useful; the depth is not. A report from this fit carries the "
                                   "verdict in its notes."))
    return result


def transit_verdict(r: dict, prob) -> dict:
    """Was a transit actually measured? Two tests, both on the noise-scaled errors:
    1. A flat line (the same baseline and detrending, no transit) must fit clearly worse: delta BIC >= 10
       (BIC of the flat line minus BIC of the transit model; the transit model pays for Tc and Rp/R*).
    2. Tc must be pinned down: its uncertainty at most a quarter of the transit duration."""
    from scipy.optimize import least_squares

    t, flux, err = prob.t, prob.flux, prob.err
    am = prob.am
    names = [p for p in ("c0", "c_am", "c_t") if p in prob.free]
    t_mid = prob.t_mid

    def flat(x):
        p = dict(zip(names, x))
        base = p.get("c0", 1.0) + p.get("c_am", 0.0) * am + p.get("c_t", 0.0) * (t - t_mid)
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
        reasons.append(f"a flat line fits about as well as a transit (ΔBIC {dbic:.1f}; a detection needs 10 or more)")
    if tc_err > 0.25 * t14:
        reasons.append(f"Tc is not pinned down (± {tc_err * 24:.1f} h for a {t14 * 24:.1f}-hour transit)")
    # 2.2: is the dip believable? (1) Its depth against the published one.
    plaus = depth_plausibility(r)
    if plaus:
        reasons.append(plaus)
    # (2) A plain jump in the light curve at a gap (a mount problem, a rotator move, a re-centring) imitates a
    # transit. If a baseline with a step after the gap fits as well as the transit, the dip is not measured.
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

    reason = {"detected": f"Transit detected: ΔBIC {dbic:.0f} against a flat line, Tc ± {tc_err * 1440:.1f} min.",
              "inconclusive": f"Inconclusive. The dip beats a flat line (ΔBIC {dbic:.0f}), but "
                              + "; ".join(reasons) + ".",
              "none": sentence(reasons) if reasons else ""}[state]
    return {"detected": state == "detected", "state": state, "dbic": dbic, "chi2_flat": chi2_flat,
            "chi2_transit": chi2_tr, "step": step, "reason": reason}


def depth_plausibility(r: dict) -> str:
    """'' when the fitted depth is believable against the published depth, else the reason it is not: more than
    3x deeper (or shallower) than published AND more than 3 sigma away."""
    pub = (r.get("planet") or {}).get("depth")
    s = r.get("summary") or {}
    if not pub or pub <= 0 or "depth" not in s:
        return ""
    depth, err = s["depth"]
    if depth <= 0:
        return ""
    ratio = depth / pub
    sigma = abs(depth - pub) / max(err, 1e-6)
    if (ratio > 3.0 or ratio < 1.0 / 3.0) and sigma > 3.0:
        return (f"the depth, {depth * 100:.2f}%, is {ratio:.1f}x the published {pub * 100:.2f}% ({sigma:.0f}σ away); a "
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
        flags.append(("warn", f"Little or no baseline {side} ({pre_min:.0f} min before, {post_min:.0f} min after). "
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
    if r.get("tc_pred_err") is not None:
        oc = (best["tc"] - r["tc_pred"]) * 1440
        sig = math.hypot(s["tc"][1], r["tc_pred_err"]) * 1440 if "tc" in s else float("nan")
        level = "warn" if (math.isfinite(sig) and sig > 0 and abs(oc) > 3 * sig) else "info"
        flags.append((level, f"O − C {oc:+.1f} ± {sig:.1f} min against the published ephemeris (epoch {r['epoch']})."))
    if r["beta"] > 1.5:
        flags.append(("info", f"Red noise: beta {r['beta']:.2f}. Correlated noise (seeing, airmass, guiding) is "
                              "included in the uncertainties."))
    pub = r["planet"].get("depth")
    if pub and "depth" in s:
        dd = s["depth"][0] - pub
        if abs(dd) > 3 * max(s["depth"][1], 1e-5):
            flags.append(("info", f"Depth {s['depth'][0] * 100:.2f}% vs published {pub * 100:.2f}% "
                                  f"({dd * 100:+.2f}%). A clear-filter depth can differ a little from TESS's red "
                                  "band; a large difference suggests a diluting or contaminating neighbor."))
    return flags


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
               "pl_orbincl,st_teff,st_logg,st_rad,sy_vmag,sy_tmag,ra,dec,pl_orbper_reflink,pl_tranmid_reflink")
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

    return re.sub(r"<[^>]+>", "", text or "").replace("&amp;", "&").strip()


def _nea_query(adql: str) -> list[dict]:
    import csv
    import io
    import urllib.parse

    url = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync?" + urllib.parse.urlencode({"query": adql, "format": "csv"})
    return list(csv.DictReader(io.StringIO(_http_get(url).decode("utf-8", "replace"))))


class PlanetChoice(ValueError):
    """2.2.6: a star name with several planets: the UI asks which one (planets holds their Archive names)."""

    def __init__(self, system: str, planets: list):
        self.system = system
        self.planets = list(planets)
        super().__init__(f"{system} has {len(self.planets)} planets ({', '.join(self.planets)}). Choose one.")


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
        raise PlanetChoice(info["system"] or clean, info["planets"])
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


def complete_planet(p: dict) -> dict:
    """Fill what can be derived: k from depth, b from inclination, a/R* from duration."""
    if p.get("period") is None:
        raise ValueError("the catalog entry has no period")
    if p.get("k") is None and p.get("depth"):
        p["k"] = math.sqrt(p["depth"])
    if p.get("depth") is None and p.get("k"):
        p["depth"] = p["k"] ** 2
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
        "#DATE,DIFF,ERR,DETREND_1",
    ]
    for t, f, e, a in zip(result["t"], result["flux"], result["err_scaled"], result["airmass"]):
        lines.append(f"{t:.8f},{f:.7f},{e:.7f},{a:.7f}" if math.isfinite(a) else f"{t:.8f},{f:.7f},{e:.7f},na")
    return "\n".join(lines) + "\n"


def fit_text(result: dict, meta: dict) -> str:
    """Plain-text summary of a transit fit (ExoFOP package, and a copy for the observer's records)."""
    s, pl = result["summary"], result["planet"]
    ch = result["chain"]
    lines = [f"SHOBS-P {meta.get('version', '')} transit fit", "",
             f"Planet         {pl.get('name', '')}   host {pl.get('host', '')}",
             f"Observer       {meta.get('obscode', '')}   site {meta.get('lat', '')}, {meta.get('lon', '')}",
             f"Night          {meta.get('obsdate', '')}   {result['n_points']} points, cadence "
             f"{result['cadence_days'] * 86400:.0f} s, filter {meta.get('filter', 'CV')}",
             f"Equipment      {meta.get('notes', '')}",
             f"Photometry     aperture {meta.get('aperture', '')}; comps {meta.get('comps', '')}"
             + (f"; {meta['scint']}" if meta.get("scint") else ""),
             f"Ephemeris      P {result['period']:.8f} d, published values from {pl.get('source', '')} "
             f"{pl.get('reference', '')}".rstrip(), "",
             "Fitted values (MCMC median and 68% interval):"]
    for name in ("tc", "k", "depth", "a_rs", "b", "inc", "t14", "u1", "u2", "c_am", "c_t"):
        if name in s:
            label = {"tc": "Tc (BJD_TDB)", "k": "Rp/R*", "depth": "Depth", "a_rs": "a/R*", "b": "b", "inc": "inc (deg)",
                     "t14": "T14 (days)", "u1": "u1", "u2": "u2", "c_am": "airmass slope", "c_t": "time slope"}[name]
            v, e = s[name]
            fmt = "{:.6f}" if name == "tc" else "{:.5f}"
            lines.append(f"  {label:<16}{fmt.format(v)} ± {fmt.format(e)}")
    if result.get("tc_pred_err") is not None:
        oc = (result["best"]["tc"] - result["tc_pred"]) * 1440
        lines.append(f"  O − C           {oc:+.2f} min (epoch {result['epoch']}, predicted {result['tc_pred']:.6f} "
                     f"± {result['tc_pred_err'] * 1440:.2f} min)")
    lines += ["", "Priors           " + ", ".join(f"{k} {m:.4g} ± {sd:.2g}" for k, (m, sd) in result["priors"].items())
              + f"; limb darkening u1 {result['ld_prior'][0]:.2f}, u2 {result['ld_prior'][1]:.2f} ± {result['ld_prior'][2]:.2f}",
              f"Noise            rms {result['rms'] * 100:.3f}% per point, reduced chi-square {result['rchi2']:.2f}, "
              f"red-noise beta {result['beta']:.2f}, error bars x{result['error_scale']:.2f}",
              "Detrending       " + (", ".join(result["detrend"]) or "none"),
              f"MCMC             {ch['walkers']} walkers, {ch['burn']} burn-in + {ch['steps']} steps, "
              f"acceptance {ch['acceptance']:.2f}"]
    ff = result.get("free_fit")
    if ff:
        lines.append(f"Free-shape check Rp/R* {ff['p']['k']:.4f}, a/R* {ff['p']['a_rs']:.2f}, b {ff['p']['b']:.3f}")
    if result["flags"]:
        lines += ["", "Notes"] + [("  ! " if lvl == "warn" else "  - ") + text for lvl, text in result["flags"]]
    return "\n".join(lines) + "\n"


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
        if p["depth"] is None and p["k"]:
            p["depth"] = p["k"] ** 2
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
