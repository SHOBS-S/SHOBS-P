"""Check neighbors: is a neighbor quiet at the star's period? Headless and testable.

A star that varies because of the setup (a passing cloud, a flat-field pattern, the light of a variable next to it)
takes its neighbors with it. Check neighbors on the Field scan page puts the selected star beside its close
companions, its look-alikes (stars of about the same brightness) and the comps, and asks one question of each: does
it carry the star's period too?

The quiet test (null_test_row): the neighbor's periodogram power at the star's period P as a share of the star's own
power, and its amplitude at P against max(2 sigma_A, a third of the star's amplitude). Quiet = both small. Each
night's median is taken out first.

2.4.0 step 7d (John, 22 Nov 2026): the promotion path (Candidate, Campaign, gates, Registry) and the measuring on a
series' older frames (reach-back, look-alikes on demand, the blend test) were removed; this module keeps the quiet
test and the small helpers the Check neighbors window needs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

try:
    from astropy.timeseries import LombScargle
except Exception:   # pragma: no cover - astropy is a requirement
    LombScargle = None

LOOKALIKE_MAG = 0.5      # John, 15 Nov: look-alikes within +-0.5 mag until real use says otherwise
LOOKALIKE_MAX = 6
NULL_POWER_FRAC = 0.5    # quiet: power at P under half the star's own
NULL_AMP_SIGMA = 2.0     # ... and amplitude at P under 2 sigma_A
NULL_AMP_FRAC = 1.0 / 3.0   # ... or under a third of the star's amplitude, whichever is larger
MIN_NIGHT_POINTS = 8


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tangent(ra, dec, ra0: float, dec0: float):
    """Gnomonic projection about (ra0, dec0), all degrees; returns (xi, eta) in arcsec (arrays or floats)."""
    ra = np.radians(np.asarray(ra, float))
    dec = np.radians(np.asarray(dec, float))
    a0, d0 = math.radians(ra0), math.radians(dec0)
    cosc = np.sin(d0) * np.sin(dec) + np.cos(d0) * np.cos(dec) * np.cos(ra - a0)
    xi = np.cos(dec) * np.sin(ra - a0) / cosc
    eta = (np.cos(d0) * np.sin(dec) - np.sin(d0) * np.cos(dec) * np.cos(ra - a0)) / cosc
    k = 180.0 / math.pi * 3600.0
    return xi * k, eta * k


def sep_arcsec(ra1, dec1, ra2, dec2) -> float:
    xi, eta = tangent(ra2, dec2, float(ra1), float(dec1))
    return float(math.hypot(float(xi), float(eta)))


# ---- a light curve -----------------------------------------------------------------------------------------------
@dataclass
class Curve:
    """One star's light curve (one row per frame, all arrays the same length). night: each point's night label.
    role: "target", "neighbor" or "comp"."""
    name: str
    jd: np.ndarray
    mag: np.ndarray
    night: list
    role: str = ""

    def take(self, keep: np.ndarray) -> "Curve":
        return Curve(self.name, self.jd[keep], self.mag[keep], [n for n, k in zip(self.night, keep) if k], self.role)


def make_curve(name, jd, mag, night, role: str = "") -> Curve:
    """A Curve from lists or arrays; points without a finite time or magnitude are left out."""
    jd = np.asarray(jd, float)
    mag = np.asarray(mag, float)
    c = Curve(str(name), jd, mag, [str(n) for n in night], str(role or ""))
    keep = np.isfinite(c.jd) & np.isfinite(c.mag)
    return c.take(keep) if not keep.all() else c


def remove_medians(values, groups) -> np.ndarray:
    """Each group's (night's, or night and flip side's) median taken out."""
    v = np.asarray(values, float).copy()
    g = np.asarray([str(x) for x in groups])
    for key in set(g.tolist()):
        sel = g == key
        if sel.any():
            v[sel] -= np.nanmedian(v[sel])
    return v


def night_groups(c: Curve) -> dict:
    out = {}
    for i, n in enumerate(c.night):
        out.setdefault(n, []).append(i)
    return {n: np.asarray(ix) for n, ix in out.items()}


def max_search_period(c: Curve, max_period: float) -> float:
    """Nightly medians are removed, so a claim stays below about one night: the longest night's span caps the
    search (at least 0.05 d)."""
    spans = [float(c.jd[ix].max() - c.jd[ix].min()) for ix in night_groups(c).values() if ix.size > 1]
    cap = max(spans) if spans else max_period
    return float(max(0.05, min(max_period, cap)))


def sine_fit(t, y, period: float, w=None):
    """Least-squares sinusoid + constant at a fixed period. Returns (A semi-amplitude, phase in cycles of the
    maximum light (minimum magnitude) relative to t=0, residuals, constant)."""
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    ph = 2.0 * math.pi * t / period
    X = np.column_stack([np.sin(ph), np.cos(ph), np.ones_like(t)])
    if w is not None:
        sw = np.sqrt(np.asarray(w, float))
        coef, *_ = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)
    else:
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    a, b, c0 = (float(v) for v in coef)
    amp = math.hypot(a, b)
    # y = R sin(ph + phi) with a = R cos(phi), b = R sin(phi); brightest (smallest mag) at ph + phi = -pi/2
    phi = math.atan2(b, a)
    phase_max = ((-math.pi / 2.0 - phi) / (2.0 * math.pi)) % 1.0
    resid = y - X @ coef
    return amp, phase_max, resid, c0


class Periodogram:
    def __init__(self, t, y, min_period: float, max_period: float):
        self.t = np.asarray(t, float)
        self.y = np.asarray(y, float)
        self.min_period, self.max_period = float(min_period), float(max_period)
        self.ls = LombScargle(self.t, self.y, normalization="standard")
        self.freq, self.power = self.ls.autopower(minimum_frequency=1.0 / self.max_period,
                                                  maximum_frequency=1.0 / self.min_period, samples_per_peak=10)
        k = int(np.argmax(self.power))
        self.best_f = float(self.freq[k])
        self.best_power = float(self.power[k])

    @property
    def period(self) -> float:
        return 1.0 / self.best_f


def lookup_power(t, y, f: float) -> float:
    try:
        return float(LombScargle(np.asarray(t, float), np.asarray(y, float), normalization="standard").power(
            np.atleast_1d(float(f)))[0])
    except Exception:
        return float("nan")


# ---- the quiet test ----------------------------------------------------------------------------------------------
def null_test_row(nc: Curve, period: float, target_power: float, target_amp: float, bad_nights=(),
                  min_points: int | None = None) -> dict | None:
    """One other star at the target's period P: its power at P as a share of the target's best power, and its
    amplitude at P against max(2 sigma_A, a third of the target's amplitude). quiet = both small. None when the star
    has too few points (min_points, default 3 x MIN_NIGHT_POINTS) once bad nights are left out."""
    need = 3 * MIN_NIGHT_POINTS if min_points is None else int(min_points)
    if nc.jd.size < need or not period or period <= 0:
        return None
    bad = set(bad_nights or ())
    nc2 = nc.take(np.array([n not in bad for n in nc.night], bool)) if bad else nc
    if nc2.jd.size < need:
        return None
    f = 1.0 / period
    yn = remove_medians(nc2.mag, nc2.night)
    pw = lookup_power(nc2.jd, yn, f)
    an, _, rn, _ = sine_fit(nc2.jd, yn, period)
    san = float(np.std(rn, ddof=3)) * math.sqrt(2.0 / nc2.jd.size) if nc2.jd.size > 3 else float("nan")
    share = pw / target_power if target_power and target_power > 0 else float("nan")
    lim_a = max(NULL_AMP_SIGMA * san, target_amp * NULL_AMP_FRAC) if math.isfinite(san) else target_amp * NULL_AMP_FRAC
    quiet = bool(math.isfinite(share) and share < NULL_POWER_FRAC and math.isfinite(an) and an < lim_a)
    return {"name": nc.name, "role": nc.role, "power_share": round(share, 3) if math.isfinite(share) else None,
            "amp": round(an, 5) if math.isfinite(an) else None, "amp_limit": round(lim_a, 5),
            "sigma_amp": round(san, 6) if math.isfinite(san) else None, "quiet": quiet, "n_points": int(nc2.jd.size)}


def neighbor_check(target: Curve, others: list[Curve], *, period: float | None = None, min_period: float = 0.02,
                   max_period: float = 20.0, bad_nights=(), min_points: int | None = None) -> dict:
    """The Check neighbors window's numbers. The target's period (given, e.g. the field scan's, or found here),
    power and amplitude, then null_test_row for every other star. Returns {"period", "power",
    "amp", "rows": [...], "n_quiet", "n_measured", "bottom": "Neighbors quiet at P: N of M measured"}. A row that
    could not be judged (too few points) comes back with "quiet": None."""
    bad = set(bad_nights or ())
    c = target.take(np.array([n not in bad for n in target.night], bool)) if bad else target
    out = {"period": None, "power": None, "amp": None, "rows": [], "n_quiet": 0, "n_measured": 0, "bottom": ""}
    need = 3 * MIN_NIGHT_POINTS if min_points is None else int(min_points)
    if c.jd.size < need or LombScargle is None:
        out["bottom"] = "The star has too few points to judge its neighbors yet."
        return out
    y = remove_medians(c.mag, c.night)
    pmax = max_search_period(c, max_period)
    pmin = float(min(min_period, pmax / 2.0))
    if period and period > 0:
        P = float(period)
        power = lookup_power(c.jd, y, 1.0 / P)
    else:
        pg = Periodogram(c.jd, y, pmin, pmax)
        P, power = pg.period, pg.best_power
    amp, *_ = sine_fit(c.jd, y, P)
    out.update({"period": P, "power": power, "amp": amp})
    for nc in others:
        row = null_test_row(nc, P, power, amp, bad, min_points=need)
        if row is None:
            out["rows"].append({"name": nc.name, "role": nc.role, "quiet": None, "power_share": None, "amp": None,
                                "n_points": int(nc.jd.size)})
            continue
        out["rows"].append(row)
        out["n_measured"] += 1
        out["n_quiet"] += int(row["quiet"])
    n_wait = sum(1 for r in out["rows"] if r["quiet"] is None)
    out["bottom"] = (f"Neighbors quiet at P: {out['n_quiet']} of {out['n_measured']} measured"
                     + (f" ({n_wait} not judged: too few points)" if n_wait else ""))
    return out
