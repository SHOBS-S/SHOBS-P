"""Transit tools for SHOBS-P (2.1): model, fit (least squares + MCMC), red noise, checks, ephemeris lookups,
nearby-eclipsing-binary arithmetic, and report files.

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
# 2.4.0 fit-in F3: the limb-darkened transit is the exact closed form for quadratic limb darkening (Mandel & Agol
# 2002, ApJ 580, L171), written here from the paper in NumPy. With I(mu) = 1 - u1 (1 - mu) - u2 (1 - mu)^2 and
# mu^2 = 1 - r^2, the intensity is  (1 - u1 - 2 u2) + (u1 + 2 u2) mu + u2 r^2,  so the light the planet blocks is
# the sum of three pieces over the part of the star it covers:
#   uniform disk  -> the area of overlap of two circles (elementary);
#   r^2 term      -> elementary as well (arcs and one square root);
#   mu term       -> complete elliptic integrals of the first, second and third kind.
# The elliptic integrals are computed together by Bulirsch's (1969) "cel" recurrence, a few arithmetic-geometric
# mean passes on the whole array, to full double precision. No step size, no tables: the only error is rounding.
# The model before 2.4.0 fit-in F3 (the star cut into 200 annuli) is kept below as occultation_annulus_reference,
# for the tests only; the two agree to 0.6 ppm for planets up to 0.15 of the star, about 10 ppm at worst for a
# planet half the star's size or larger (the difference is the old model's: it shrinks as annuli are added).

_NR = 200
_theta_edges = np.linspace(0.0, math.pi / 2, _NR + 1)
_RE = np.sin(_theta_edges)                     # annulus edges, dense near the limb (reference model only)
_RM = 0.5 * (_RE[1:] + _RE[:-1])
_OCC_TINY = 1e-12                              # how far z is moved off z = 0 and z = k (flux is continuous there)
_OCC_AGM_TOL = 1e-6                            # arithmetic-geometric mean stop; the error left is its square


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


def occultation_annulus_reference(z: np.ndarray, k: float, u1: float, u2: float, n_annuli: int = _NR) -> np.ndarray:
    """The transit model used before 2.4.0 fit-in F3, kept ONLY as a reference for the tests.

    The star is cut into n_annuli annuli (denser near the limb); the planet's overlap with each is the exact
    circle-overlap area, so only the smooth limb-darkening profile is approximated (about 1e-5 in flux at worst
    with the default 200 annuli; more annuli come closer to the exact curve)."""
    z = np.asarray(z, dtype=float)
    out = np.ones_like(z)
    hit = z < 1.0 + k
    if not hit.any() or k <= 0:
        return out
    if n_annuli == _NR:
        re_, rm_ = _RE, _RM
    else:
        re_ = np.sin(np.linspace(0.0, math.pi / 2, int(n_annuli) + 1))
        rm_ = 0.5 * (re_[1:] + re_[:-1])
    mu = np.sqrt(np.clip(1.0 - rm_ * rm_, 0.0, 1.0))
    intensity = 1.0 - u1 * (1.0 - mu) - u2 * (1.0 - mu) ** 2
    total = np.sum(intensity * math.pi * (re_[1:] ** 2 - re_[:-1] ** 2))
    area = _overlap_area(re_[None, :], z[hit][:, None], k)
    out[hit] = 1.0 - np.sum(intensity * (area[:, 1:] - area[:, :-1]), axis=1) / total
    return out


def occultation(z: np.ndarray, k: float, u1: float, u2: float) -> np.ndarray:
    """Relative flux for planet-star separation z (in stellar radii), radius ratio k, quadratic limb darkening.

    Exact closed form of Mandel & Agol (2002, ApJ 580, L171) for I(mu) = 1 - u1 (1 - mu) - u2 (1 - mu)^2:
    circle-overlap area for the uniform part, an elementary term for the r^2 part, and complete elliptic
    integrals of the first, second and third kind for the mu part (their lambda_e, eta_d and lambda_d). The
    elliptic integrals come from Bulirsch's (1969, Numer. Math. 13, 305) cel recurrence, vectorised over z.
    Covers every geometry: no overlap (1), ingress/egress, planet wholly on the disk, a planet larger than the
    star (down to 0 when it covers the star), z = 0, z = k, z = |1 - k| and z = 1 + k. z may have any shape
    (a negative z counts as |z|); k, u1, u2 are single numbers. Accuracy: rounding only (about 1e-9 in flux)."""
    z = np.abs(np.asarray(z, dtype=float))
    out = np.ones_like(z)
    p = float(k)
    if not p > 1e-8:                                  # no planet (or a dip below double precision)
        return out
    hit = z < 1.0 + p
    if p >= 1.0:                                      # a planet as large as the star can cover it completely
        covered = z <= p - 1.0
        if covered.any():
            out[covered] = 0.0
            hit &= ~covered
    zz = z[hit]
    n = zz.size
    if n == 0:
        return out
    c1 = u1 + 2.0 * u2                                # intensity = c0 + c1 mu + u2 r^2
    c0 = 1.0 - c1
    total = c0 + 2.0 * c1 / 3.0 + 0.5 * u2            # the whole star, over pi
    p2 = p * p
    # z = 0 and z = k are removable singular points of the formulas (the flux itself is smooth there)
    zz = np.maximum(zz, _OCC_TINY)
    d = zz - p
    ad = np.abs(d)
    if ad.min() < _OCC_TINY:
        zz = np.where(ad < _OCC_TINY, p + _OCC_TINY, zz)
        d = zz - p
        ad = np.abs(d)
    z2 = zz * zz
    a = d * d                                         # Mandel & Agol's a = (z - p)^2 and b = (z + p)^2
    one_a = (1.0 - d) * (1.0 + d)                     # 1 - a, without cancellation near the fourth contact
    b_one = (zz + (p - 1.0)) * (zz + (p + 1.0))       # b - 1: below 0 when the planet is wholly on the disk
    q = -d * (zz + p)                                 # p^2 - z^2, without cancellation near z = p
    pz = p * zz
    w = z2 + (7.0 * p2 - 4.0)
    ins = b_one < 0.0
    n_ins = int(np.count_nonzero(ins))
    # The mu term is  [A K + B E - 3 (q / a) Pi] / scale  (+ 2/3 when z < p), with modulus and coefficients that
    # differ between "wholly on the disk" (their lambda_2) and "crossing the limb" (their lambda_1).
    if n_ins == n:
        kc2 = -b_one / one_a                          # complementary modulus squared, 1 - m
        coef_k = (1.0 - 5.0 * z2 + p2) + q * q
        coef_e = one_a * w
        pi_p = (zz + p) / ad                          # sqrt(1 - n) of the third-kind integral
        scale = (4.5 * math.pi) * np.sqrt(one_a)
    else:
        kc2 = b_one / (4.0 * pz)
        coef_k = -b_one * (2.0 * b_one + a - 1.0) - 3.0 * q * (b_one - 1.0)
        coef_e = 4.0 * pz * w
        pi_p = 1.0 / ad
        scale = (9.0 * math.pi) * np.sqrt(pz)
        if n_ins:
            oa = one_a[ins]
            qi = q[ins]
            kc2[ins] = -b_one[ins] / oa
            coef_k[ins] = (1.0 - 5.0 * z2[ins] + p2) + qi * qi
            coef_e[ins] = oa * w[ins]
            pi_p[ins] = (zz[ins] + p) / ad[ins]
            scale[ins] = (4.5 * math.pi) * np.sqrt(oa)
    kc2 = np.minimum(np.maximum(kc2, 1e-24), 1.0)     # exactly at second/third contact K and Pi cancel (finite)
    kc = np.sqrt(kc2)
    # Bulirsch's cel(kc, p, a, b), both integrals in one go: row 0 = coef_k K + coef_e E, row 1 = Pi.
    # The number of passes is set by the smallest kc (the slowest to converge), found with plain floats.
    k_s = float(kc.min())
    e_s, m_s, passes = k_s, 1.0, 1
    while abs(m_s - k_s) > m_s * _OCC_AGM_TOL and passes < 40:
        m_s, k_s = k_s + m_s, 2.0 * math.sqrt(e_s)
        e_s = k_s * m_s
        passes += 1
    pp = np.empty((2, n))
    pp[0] = 1.0
    pp[1] = pi_p
    aa = np.empty((2, n))
    aa[0] = coef_k + coef_e
    aa[1] = 1.0
    bb = np.empty((2, n))
    bb[0] = coef_k + coef_e * kc2
    bb[1] = 1.0 / pi_p
    kk = kc
    e = kc
    m = 1.0
    t = np.empty((2, n))
    g = np.empty((2, n))
    for it in range(passes):                          # in place: aa, bb, t swap roles, nothing is allocated
        np.divide(bb, pp, out=t)
        t += aa                                       # new a = b / p + a
        np.divide(e, pp, out=g)
        aa *= g
        aa += bb
        aa += aa                                      # new b = 2 (a e / p + b)
        pp += g
        m = kk + m
        aa, bb, t = t, aa, bb
        if it == passes - 1:
            break
        kk = 2.0 * np.sqrt(e)
        e = kk * m
    cel = (aa * m + bb) / (m * (m + pp))              # times pi / 2, applied below
    lam_d = (0.5 * math.pi) * (cel[0] - 3.0 * (q / a) * cel[1]) / scale
    lam_d += (2.0 / 3.0) * (d < 0.0)
    h = p2 * (p2 + 2.0 * z2)
    if n_ins == n:
        blocked = (c0 * p2 + c1 * lam_d + (0.5 * u2) * h) / total
    else:
        # crossing the limb: arcs of the two circles, with arctan2 (accurate right up to the contact points)
        root = np.sqrt(np.maximum(one_a * b_one, 0.0))
        k0 = np.arctan2(root, p2 + z2 - 1.0)
        k1 = np.arctan2(root, 1.0 - p2 + z2)
        lam_e = (p2 * k0 + k1 - 0.5 * root) * (1.0 / math.pi)
        eta = (k1 + h * k0 - 0.25 * (1.0 + 5.0 * p2 + z2) * root) * (0.5 / math.pi)
        if n_ins:
            lam_e[ins] = p2
            eta[ins] = 0.5 * h[ins]
        blocked = (c0 * lam_e + c1 * lam_d + u2 * eta) / total
    out[hit] = 1.0 - np.minimum(np.maximum(blocked, 0.0), 1.0)   # rounding only: never above 1 or below 0
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


# 2.2.17 (item 1): quadratic limb darkening for the Sloan filters, Claret (2004, A&A 428, 1001; VizieR J/A+A/428/1001
# table4: ATLAS models, log g 4.5, [M/H] 0, microturbulence 2 km/s), (Teff, a, b) with I(mu)/I(1) = 1 - a(1-mu) - b(1-mu)^2.
LD_SLOAN = {
    "SG": ((3500, 0.4072, 0.4068), (3750, 0.4589, 0.3366), (4000, 0.6912, 0.1203), (4250, 0.9147, -0.0774),
           (4500, 0.9288, -0.0827), (4750, 0.8586, -0.0163), (5000, 0.7787, 0.0549), (5250, 0.6983, 0.1225),
           (5500, 0.6225, 0.1831), (5750, 0.5537, 0.2357), (6000, 0.4911, 0.2813), (6250, 0.4398, 0.3163),
           (6500, 0.3993, 0.3414), (6750, 0.3664, 0.3619), (7000, 0.3384, 0.3798), (7250, 0.3114, 0.3983),
           (7500, 0.2858, 0.4172), (7750, 0.2631, 0.4344), (8000, 0.2542, 0.4401)),
    "SR": ((3500, 0.4397, 0.3376), (3750, 0.4102, 0.3593), (4000, 0.5223, 0.2549), (4250, 0.6519, 0.1332),
           (4500, 0.6485, 0.1242), (4750, 0.5933, 0.1630), (5000, 0.5328, 0.2079), (5250, 0.4706, 0.2523),
           (5500, 0.4128, 0.2904), (5750, 0.3616, 0.3220), (6000, 0.3138, 0.3502), (6250, 0.2762, 0.3696),
           (6500, 0.2467, 0.3821), (6750, 0.2233, 0.3919), (7000, 0.2030, 0.4011), (7250, 0.1832, 0.4110),
           (7500, 0.1665, 0.4196), (7750, 0.1531, 0.4256), (8000, 0.1507, 0.4219)),
    "SI": ((3500, 0.2879, 0.3868), (3750, 0.2965, 0.3780), (4000, 0.3797, 0.3091), (4250, 0.4733, 0.2237),
           (4500, 0.4765, 0.2048), (4750, 0.4434, 0.2205), (5000, 0.4033, 0.2468), (5250, 0.3583, 0.2765),
           (5500, 0.3144, 0.3029), (5750, 0.2743, 0.3249), (6000, 0.2356, 0.3456), (6250, 0.2040, 0.3600),
           (6500, 0.1782, 0.3696), (6750, 0.1562, 0.3780), (7000, 0.1372, 0.3852), (7250, 0.1191, 0.3927),
           (7500, 0.1052, 0.3971), (7750, 0.0958, 0.3977), (8000, 0.0954, 0.3900)),
    "SZ": ((3500, 0.1288, 0.4930), (3750, 0.1518, 0.4516), (4000, 0.2543, 0.3574), (4250, 0.3652, 0.2577),
           (4500, 0.3803, 0.2339), (4750, 0.3535, 0.2469), (5000, 0.3201, 0.2670), (5250, 0.2835, 0.2887),
           (5500, 0.2476, 0.3076), (5750, 0.2140, 0.3242), (6000, 0.1803, 0.3409), (6250, 0.1517, 0.3530),
           (6500, 0.1276, 0.3612), (6750, 0.1066, 0.3684), (7000, 0.0879, 0.3746), (7250, 0.0703, 0.3804),
           (7500, 0.0564, 0.3833), (7750, 0.0469, 0.3820), (8000, 0.0448, 0.3734)),
}


def limb_darkening_band(band) -> str:
    """2.2.17 (item 1): the LD_SLOAN key for a filter code ("SI", "i'", "Sloan i"…), or '' (the V/clear table)."""
    import photometry_core as core
    text = str(band or "").strip()
    key = text.upper() if text.upper() in LD_SLOAN else (core.sloan_band_code(text) or "")
    return key if key in LD_SLOAN else ""


def limb_darkening_guess(teff: float | None, band=None) -> tuple[float, float]:
    """Approximate quadratic limb darkening for a broad green / V / clear band, by temperature.

    Rough values in the range of the Claret (2000) V-band tables for dwarfs (log g about 4.5). They are
    used only as the centers of broad priors (0.1 wide), so the light curve, not this table, decides.
    2.2.17 (item 1): band SG/SR/SI/SZ (a Sloan filter) uses Claret (2004)'s Sloan values (LD_SLOAN) instead."""
    table = ((3500, 0.75, 0.05), (4500, 0.70, 0.08), (5000, 0.60, 0.15), (5500, 0.50, 0.22),
             (6000, 0.43, 0.27), (6500, 0.36, 0.30), (7000, 0.30, 0.33), (8000, 0.25, 0.33))
    sloan = limb_darkening_band(band)
    if sloan:
        table = LD_SLOAN[sloan]
    if teff is None or not math.isfinite(teff):
        teff = 5800.0
    ts = [row[0] for row in table]
    return float(np.interp(teff, ts, [row[1] for row in table])), float(np.interp(teff, ts, [row[2] for row in table]))


# ---- Parameters and priors ----------------------------------------------------------------------

PARAMS = ("tc", "k", "a_rs", "b", "q1", "q2", "c0", "c_am", "c_t", "c_t2", "step")
PARAM_LABELS = {"tc": "Mid-transit Tc (BJD_TDB)", "k": "Rp/R*", "a_rs": "a/R*", "b": "Impact parameter b",
                "q1": "q1 (limb darkening)", "q2": "q2 (limb darkening)", "c0": "Baseline",
                "c_am": "Airmass slope", "c_t": "Time slope (per day)",
                "c_t2": "Time curve (per day²)", "step": "Flip step"}

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
# 2.3.0 (#8): ON again, but gated: on a night with a flip the fit is made with and without the step (least squares,
# same noise-scaled errors) and the step is kept only when it lowers the BIC by at least FLIP_STEP_DBIC (the same
# "strong evidence" bar as a detection, so the step cannot soak up noise; one extra parameter costs ln(n), so the step
# must also beat that). Otherwise the night is fitted with no step term at all, exactly as before. The TrES-3 b case
# above is still covered by the baseline-on-each-side rule (FLIP_NO_BASELINE_VERDICT) when the step is kept.
# fit_transit(flip_step=True) fits the step without the gate (tests); flip_step=False never fits it.
FLIP_STEP_ENABLED = True
FLIP_STEP_DBIC = 10.0         # 2.3.0 (#8): ΔBIC (no step minus with step) needed to keep the flip step
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


def poly_baseline(p: dict, am, dtm):
    """The baseline before the flip step: c0 + c_am·airmass + c_t·(t − t_mid) + c_t2·(t − t_mid)² (2.4.0, #103: the
    one place it is written, so the transit model, the flat-line test, the step-at-gap test and the single-detrend
    check can never disagree)."""
    return (p.get("c0", 1.0) + p.get("c_am", 0.0) * am + p.get("c_t", 0.0) * dtm
            + p.get("c_t2", 0.0) * np.square(dtm))


class TransitProblem:
    """Data, fixed values, and priors for one transit light curve.

    Model: flux = transit(t) * (c0 + c_am * (airmass - mean) + c_t * (t - t_mid) + c_t2 * (t - t_mid)²)
                  * (1 + step * [t >= t_flip]).
    2.4.0 (#103): c_t2 (the time curve, quadratic in time) only with detrend_curve, which always comes with the time
    slope (a curve without its slope is not one of the choices). Nothing above quadratic (ExoClock, 29 Oct 2026).
    The step term exists only when after (points after a meridian flip) is given with points on both sides.
    priors: {name: (mean, sigma)} Gaussian priors. Bounds keep everything physical."""

    def __init__(self, t, flux, err, airmass, period, priors: dict, free: tuple, start: dict,
                 detrend_airmass=True, detrend_time=False, ld_prior=None, t_flip=None, detrend_curve=False):
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
            names = [p for p in names if p not in ("c_t", "c_t2")]
        elif not detrend_curve:
            names = [p for p in names if p != "c_t2"]
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
        base = poly_baseline(p, am, t - self.t_mid)
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
             "c_t": 1e-3, "c_t2": 1e-2, "step": 1e-3}
        return np.array([s[p] for p in self.free])

    def bounds(self):
        lo = {"tc": self.t.min() - 0.2, "k": 0.006, "a_rs": 1.3, "b": 0.0, "q1": 1e-4, "q2": 1e-4, "c0": 0.5,
              "c_am": -1.0, "c_t": -1.0, "c_t2": -20.0, "step": -0.49}
        hi = {"tc": self.t.max() + 0.2, "k": 0.59, "a_rs": 199.0, "b": 1.5, "q1": 0.9999, "q2": 0.9999, "c0": 1.5,
              "c_am": 1.0, "c_t": 1.0, "c_t2": 20.0, "step": 0.49}
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


BETA_MIN_MINUTES = 10.0   # 2.4.0 (#62): the shortest bin the time-averaging beta uses
BETA_MAX_MINUTES = 30.0


def red_noise_beta(t, resid, cadence_days: float, max_minutes: float = BETA_MAX_MINUTES,
                   min_minutes: float = BETA_MIN_MINUTES) -> float:
    """Time-averaging beta (Pont et al. 2006; Winn et al. 2008): how much faster than white noise the
    residuals average down. 1 means white; 2 means binned errors are twice what white noise predicts.
    2.4.0 (#62): bins from min_minutes to max_minutes only (Winn et al. 2008 use bins near the ingress time, 10-30
    min). Before, every bin from 2 points up counted and the median leaned on the shortest bins, which barely see a
    slow wiggle: on a sparse night (WASP-43 b MOBs, 49 points at ~4.4 min) two of five bin sizes were under 10 min,
    and beta came out 1.00 beside residuals with obvious structure. At least 2 points a bin, always."""
    resid = np.asarray(resid, dtype=float)
    n_tot = len(resid)
    sigma1 = np.std(resid)
    if n_tot < 20 or sigma1 <= 0:
        return 1.0
    betas = []
    cad_min = max(cadence_days, 1e-6) * 1440.0
    max_n = max(2, int(max_minutes / cad_min))
    min_n = min(max(2, int(math.ceil(min_minutes / cad_min))), max_n)
    if min_n > min(max_n, n_tot // 4):
        min_n = 2   # Fable N4: a run too short for a 10-min bin: the old 2-point bins, rather than no beta at all
    for n in range(min_n, min(max_n, n_tot // 4) + 1):
        m = n_tot // n
        if m < 4:
            break
        means = resid[: m * n].reshape(m, n).mean(axis=1)
        expected = sigma1 / math.sqrt(n) * math.sqrt(m / (m - 1.0))
        betas.append(np.std(means) / expected)
    return float(max(1.0, np.median(betas))) if betas else 1.0


PB_MAX_SHIFTS = 40   # 2.4.0 (#62): residual shifts the prayer-bead check refits (evenly spread when there are more)
PB_NAMES = ("tc", "k", "step", "c_t2")


def prayer_bead(problem: TransitProblem, best: dict, max_shifts: int = PB_MAX_SHIFTS, cancel=None) -> dict | None:
    """2.4.0 (#62): residual-permutation ("prayer bead") errors, a second opinion on red noise that needs no bin
    sizes (Gillon et al. 2007; Southworth 2008). The best fit's residuals are shifted cyclically around the light
    curve and added back to the model; each shifted light curve is refitted (least squares, from the best fit) and the
    spread of the refits is the error. Correlated residuals (slow wiggles) move the refits; white noise hardly does.
    Returns {"n": refits, "err": {name: std}, "depth_err"} or None with fewer than 20 points or 8 refits."""
    t = problem.t
    n = len(t)
    if n < 20:
        return None
    model = problem.model(best)
    resid = problem.flux - model
    shifts = list(range(1, n))
    if len(shifts) > max_shifts:
        shifts = sorted({int(round(v)) for v in np.linspace(1, n - 1, max_shifts)})
    x0 = np.array([best[p] for p in problem.free])
    lo, hi = problem.bounds()
    from scipy.optimize import least_squares
    vals = {p: [best[p]] for p in problem.free if p in PB_NAMES}
    depths = [best["k"] ** 2]
    flux0 = problem.flux
    try:
        for j in shifts:
            if cancel is not None and cancel.is_set():
                raise RuntimeError("Canceled")
            problem.flux = model + np.roll(resid, j)
            try:
                sol = least_squares(problem.residual_vector, np.clip(x0, np.asarray(lo) + 1e-9, np.asarray(hi) - 1e-9),
                                    bounds=(lo, hi), x_scale=problem.scales(), max_nfev=600)
            except Exception:
                continue
            p = problem.full(sol.x)
            for name in vals:
                vals[name].append(p[name])
            depths.append(p["k"] ** 2)
    finally:
        problem.flux = flux0
    if len(depths) < 8:
        return None
    return {"n": len(depths) - 1, "err": {name: float(np.std(v, ddof=1)) for name, v in vals.items()},
            "depth_err": float(np.std(depths, ddof=1))}


def apply_prayer_bead(summary: dict, pb: dict | None) -> list:
    """2.4.0 (#62): where the prayer-bead error is larger than the MCMC's, it is the one reported (John, 19 Nov: the
    larger of the two). Returns the names whose error was raised."""
    if not pb:
        return []
    raised = []
    for name, e in list(pb["err"].items()) + [("depth", pb["depth_err"])]:
        if name in summary and math.isfinite(e) and e > summary[name][1]:
            summary[name] = (summary[name][0], float(e))
            raised.append(name)
    return raised


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
            raise RuntimeError("Canceled")
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
    t14 = float(planet.get("t14") or T14_ASSUMED)
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


JD_BJD_PAD = 0.0105    # 2.2.15 (item 1): 15 minutes: geocentric JD against BJD_TDB differs by up to 8.3 minutes
BJD_PAD = 0.0007       # ... about 1 minute when the frames' times are converted to BJD_TDB (RA/Dec known)
T14_ASSUMED = 0.1      # days (2.4 h): the window's T14 when the Archive does not publish one


def planet_transit_frames(observations, planets, pad: float = JD_BJD_PAD, offsets=None) -> list[dict]:
    """2.2.15 (item 1): which frames fall in a predicted transit of each planet. observations need only .jd
    (geocentric JD UTC). offsets: BJD_TDB minus JD per frame (RA/Dec known), so the frames are compared with the
    ephemeris in its own time system; without them the default pad of 15 minutes covers JD against BJD.
    Returns one entry per planet with frames inside a window, everything in the frames' JD (UTC), so the flags,
    the stored windows and the labels share one time system: {"planet", "letter", "frames": n, "jd": [first, last
    frame inside], "windows": [[lo, hi], ...] (incl. pad), "t1", "t4" (the first such transit, no pad), "unc"
    (days), "pad" (days), "bjd" (bool), "t14_assumed" (bool), "unc_known" (bool)}."""
    pairs = []
    for k, o in enumerate(observations):
        try:
            jd = float(o.jd)
            off = float(offsets[k]) if offsets is not None else 0.0
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(jd) and math.isfinite(off):
            pairs.append((jd, jd + off))
    if not pairs or not planets:
        return []
    shift = float(np.median([b - a for a, b in pairs]))   # BJD - JD over the night (constant to a second)
    t_min, t_max = min(b for _a, b in pairs), max(b for _a, b in pairs)
    out = []
    for p in planets:
        wins = transit_windows(t_min, t_max, p, pad)
        if not wins:
            continue
        inside = [a for a, b in pairs if any(w["lo"] <= b <= w["hi"] for w in wins)]
        if not inside:
            continue
        first = next(w for w in wins if any(w["lo"] <= b <= w["hi"] for _a, b in pairs))
        out.append({"planet": p.get("name") or "", "letter": planet_letter(p.get("name") or ""),
                    "frames": len(inside), "jd": [min(inside), max(inside)],
                    "windows": [[w["lo"] - shift, w["hi"] - shift] for w in wins],
                    "t1": first["t1"] - shift, "t4": first["t4"] - shift, "unc": first["unc"], "pad": pad,
                    "bjd": offsets is not None, "t14_assumed": not p.get("t14"),
                    "unc_known": p.get("t0_err") is not None and p.get("period_err") is not None})
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
    rows = _nea_query("select pl_name,pl_orbper,pl_tranmid,pl_trandur,tran_flag "
                      f"from pscomppars where hostname = '{system}'")
    # Uncertainties as lookup_nea gets them: the composite table, else the default parameter set; a missing column
    # or table only leaves them unknown.
    errs = {}
    for table, extra in (("pscomppars", ""), ("ps", " and default_flag = 1")):
        try:
            got = _nea_query(f"select pl_name,pl_orbpererr1,pl_tranmiderr1 from {table} where hostname = "
                             f"'{system}'{extra}")
        except Exception:
            continue
        for r in got:
            have = errs.setdefault(r.get("pl_name") or "", {})
            for k in ("pl_orbpererr1", "pl_tranmiderr1"):
                if have.get(k) is None and _f(r.get(k)) is not None:
                    have[k] = abs(_f(r.get(k)))
    out = []
    now = now_jd()
    for row in rows:
        if str(row.get("tran_flag", "")).strip() not in ("1", "1.0"):
            continue
        name = row.get("pl_name") or ""
        period, t0 = _f(row.get("pl_orbper")), _f(row.get("pl_tranmid"))
        if not period or not t0:
            continue
        e = errs.get(name, {})
        p = {"name": name, "period": period, "period_err": e.get("pl_orbpererr1"), "t0": t0,
             "t0_err": e.get("pl_tranmiderr1"), "t14": (_f(row.get("pl_trandur")) or 0) / 24.0 or None}
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
    u1g, u2g = limb_darkening_guess(planet.get("teff"), planet.get("ld_band"))   # 2.2.17 (item 1): band-aware
    q1, q2 = u_to_kipping(u1g, u2g)
    # Predicted mid-transit nearest the data.
    # 2.2.12: without an ephemeris the start guess (the lowest point) is only a start; it is never reported as a
    # published or predicted time, and no O − C is computed.
    tc0 = planet.get("t0")
    eph_err_known = False
    if tc0:
        n = int(round((np.mean(t_abs) - tc0) / period))   # 2.2.19 review: a plain int (NumPy 1.x printed 2362.0)
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
             "c_t2": 0.0, "step": 0.0}
    cadence = float(np.median(np.diff(t))) if len(t) > 1 else 1e-3
    dur = total_duration(period, a0, min(b0, 0.99), k0)
    starts = [{"tc": tc_rel + d} for d in (-0.25 * dur, 0.25 * dur)] + [{"k": k0 * 1.2}, {"k": k0 * 0.8}]
    # 2.2.13: the flip time, from the data: halfway between the last frame before the flip and the first after it.
    # 2.3.0 (#8): flip_step None = the BIC gate decides (flip_step_test), True = always fit it, False = never.
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
            "free": ("tc", "k", "a_rs", "b", "q1", "q2", "c0", "c_am", "c_t", "c_t2", "step"), "ld": (u1g, u2g, 0.1),
            "cadence": cadence, "starts": starts, "t_flip": t_flip, "flip_gap_min": flip_gap, "n": n,
            "tc_pred": tc_pred, "tc_pred_err": tc_pred_err, "tc_start": tc_start, "eph_err_known": eph_err_known,
            "has_eph": bool(tc0), "flip_gated": flip_step is None}


def flip_step_test(S: dict, detrend_airmass=True, detrend_time=False, detrend_curve=False) -> dict | None:
    """2.3.0 (#8): on a night with a meridian flip (S from _setup_fit, with S["t_flip"]), fit the light curve with
    and without the flip step (least squares) on the same errors, scaled as for the MCMC (reduced chi-square and red
    noise of the fit WITH the step, the fuller model), and compare the BICs. kept: ΔBIC >= FLIP_STEP_DBIC.
    Returns {"t_flip" (BJD), "step": (value, error), "dbic", "kept", "threshold", "gap_min", "error_scale"} or None
    without a flip. The step is relative flux (after = before × (1 + step))."""
    if S.get("t_flip") is None:
        return None
    t, flux, err, am = S["t"], S["flux"], S["err"], S["am"]
    starts = S["starts"]
    prob = TransitProblem(t, flux, err, am, S["period"], S["priors"], S["free"], S["start"], detrend_airmass,
                          detrend_time, S["ld"], S["t_flip"], detrend_curve=detrend_curve)
    if prob.t_flip is None:
        return None
    x, chi2 = best_fit(prob, starts)
    rchi = chi2 / max(len(t) - len(prob.free), 1)
    beta = red_noise_beta(t, flux - prob.model(prob.full(x)), S["cadence"])
    scale = math.sqrt(max(rchi, 1.0)) * beta
    p1 = prob.full(x)
    # Without the step: from the usual starts, and from the stepped fit's values.
    prob_ns = TransitProblem(t, flux, err * scale, am, S["period"], S["priors"], S["free"], S["start"],
                             detrend_airmass, detrend_time, S["ld"], None, detrend_curve=detrend_curve)
    x_ns, chi2_ns = best_fit(prob_ns, [s for s in starts if "step" not in s]
                             + [{k: v for k, v in p1.items() if k != "step"}])
    p_ns = prob_ns.full(x_ns)
    # With the step: from the stepped fit, and from the no-step fit with step 0 (so it can never fit worse).
    prob_ws = TransitProblem(t, flux, err * scale, am, S["period"], S["priors"], S["free"], p1, detrend_airmass,
                             detrend_time, S["ld"], S["t_flip"], detrend_curve=detrend_curve)
    x_ws, chi2_ws = best_fit(prob_ws, [dict(p_ns, step=0.0)])
    e_ws = lsq_errors(prob_ws)
    n = len(t)
    dbic = (chi2_ns + len(prob_ns.free) * math.log(n)) - (chi2_ws + len(prob_ws.free) * math.log(n))
    i = prob_ws.free.index("step")
    return {"t_flip": prob_ws.t_flip + S["t_ref"], "step": (float(prob_ws.full(x_ws)["step"]), float(e_ws[i])),
            "dbic": float(dbic), "kept": bool(dbic >= FLIP_STEP_DBIC), "threshold": FLIP_STEP_DBIC,
            "gap_min": S.get("flip_gap_min"), "error_scale": float(scale), "chi2_step": float(chi2_ws),
            "chi2_nostep": float(chi2_ns)}


# ---- 2.4.0 (#103): the detrend choice ------------------------------------------------------------
# The baseline choices, simplest first: (key, label, (airmass, time slope, time curve)). The curve always comes with
# its slope. Nothing above quadratic: ExoClock (29 Oct 2026) accepts a quadratic in time for systematics, higher
# powers start to fit the transit itself.
DETREND_CHOICES = (
    ("none", "none", (False, False, False)),
    ("airmass", "airmass", (True, False, False)),
    ("slope", "time slope", (False, True, False)),
    ("curve", "time curve (quadratic)", (False, True, True)),
    ("airmass+slope", "airmass + time slope", (True, True, False)),
    ("airmass+curve", "airmass + time curve (quadratic)", (True, True, True)),
)
# 2.4.0 step 6b (John 19 Nov): the curve choices say "(quadratic)"; the 6a labels still read back (a choice typed or
# kept from before keeps its meaning)
DETREND_OLD_LABELS = {"time curve": "curve", "airmass + time curve": "airmass+curve"}
DETREND_AUTO = "auto"
# The fit's parameter names in plain words, for the page, the Log and the reports (step 6b: the Log said "k")
PARAM_WORDS = {"tc": "Tc", "k": "Rp/R*", "depth": "depth", "step": "flip step", "c_t2": "time curve"}
AUTO_AIRMASS_DROP_MAX = 10      # Auto leaves out at most this many frames without airmass to try the airmass choices
AUTO_AIRMASS_DROP_FRAC = 0.05   # ... or this share of the night, when that is more
DETREND_AUTO_LABEL = "Auto (BIC)"
# Choices whose BIC is within this of the lowest count as equally good, and the one with the fewest parameters is
# taken (ΔBIC under 2: "not worth more than a bare mention", Kass & Raftery 1995), so Auto never adds a term for nothing.
DETREND_TIE = 2.0


def detrend_key(flags) -> str:
    """The DETREND_CHOICES key for (airmass, time slope, time curve)."""
    da, dt, dq = (bool(x) for x in flags)
    dq = dq and dt
    for key, _label, f in DETREND_CHOICES:
        if f == (da, dt, dq):
            return key
    return "none"


def detrend_label(key: str) -> str:
    if key == DETREND_AUTO:
        return DETREND_AUTO_LABEL
    return {k: lab for k, lab, _f in DETREND_CHOICES}.get(key, key)


def detrend_flags(key: str) -> tuple:
    return {k: f for k, _lab, f in DETREND_CHOICES}.get(key, (False, False, False))


def detrend_names(flags, has_airmass: bool = True) -> list:
    """result["detrend"]: the terms actually fitted, in AAVSO column order ('airmass', 'time', 'time2')."""
    da, dt, dq = flags
    return [nm for nm, on in (("airmass", da and has_airmass), ("time", dt), ("time2", dt and dq)) if on]


def detrend_text(names) -> str:
    """'airmass, time slope' / 'time curve' / 'none' for the page, the Log and the reports."""
    names = list(names or [])
    words = (["airmass"] if "airmass" in names else []) + (
        ["time curve"] if "time2" in names else ["time slope"] if "time" in names else [])
    return ", ".join(words) or "none"


def airmass_usable(airmass) -> bool:
    """Every point has an airmass and it changes over the night (what TransitProblem needs to fit c_am)."""
    if airmass is None:
        return False
    am = np.asarray(airmass, dtype=float)
    return bool(am.size and np.isfinite(am).all() and np.ptp(am) > 1e-4)


def choose_detrend(S: dict, allow_airmass: bool, say=None, cancel=None, progress=None) -> dict:
    """2.4.0 (#103): Auto detrend. Every choice in DETREND_CHOICES (the airmass ones only with allow_airmass) is
    fitted by least squares with the transit, on the same error bars (scaled once, by the reduced chi-square and the
    red-noise beta of the fullest model), and compared by BIC. On a night with a meridian flip the step test (#8)
    runs INSIDE each choice: the choice is fitted with and without the step and keeps the step only when it lowers the
    BIC by FLIP_STEP_DBIC or more (always, when the fit was asked for the step: S["flip_gated"] False with t_flip set),
    so a time curve and a flip step are judged together. The lowest BIC wins; choices within DETREND_TIE of it count
    as equal and the one with the fewest parameters is taken.
    Returns {"key", "label", "flags", "rows": [...], "scale", "flip_test" (the chosen choice's step test, the
    flip_step_test format, or None)}. Each row: key, label, flags, k (free parameters), bic, dbic (row − chosen,
    > 0 worse) and, with a flip, step (value, error), dbic_step, kept."""
    t, flux, err, am = S["t"], S["flux"], S["err"], S["am"]
    n = len(t)
    choices = [c for c in DETREND_CHOICES if allow_airmass or not c[2][0]]
    t_flip = S.get("t_flip")
    gated = t_flip is not None and S.get("flip_gated")
    forced = t_flip is not None and not S.get("flip_gated")
    no_step = [st for st in S["starts"] if "step" not in st]
    # 2.4.0 fit-in F2 (banked nit 9): the busy bar names each step: progress(j, n, label, sub) — j = 0 for the
    # reference fit, then each choice by name, and on a flip night which of its two fits runs. Words only.
    if progress is not None:
        progress(0, len(choices), "reference fit", None)
    # One error scale for every choice (BICs are only comparable on the same errors): the fullest model's (Fable S3:
    # the next fullest when that one does not converge).
    prob = x = None
    for _k, _lab, (fa, ft_, fq) in reversed(choices):
        try:
            prob = TransitProblem(t, flux, err, am, S["period"], S["priors"], S["free"], S["start"], fa, ft_, S["ld"],
                                  t_flip, detrend_curve=fq)
            x, chi2 = best_fit(prob, S["starts"])
            break
        except ValueError:
            prob = x = None
    if prob is None:
        raise ValueError("the least-squares fit did not converge with any detrend choice")
    p_full = prob.full(x)
    rchi = chi2 / max(n - len(prob.free), 1)
    beta = red_noise_beta(t, flux - prob.model(p_full), S["cadence"])
    scale = math.sqrt(max(rchi, 1.0)) * beta
    e = err * scale
    rows, failed = [], []
    for j, (key, label, (da, dt, dq)) in enumerate(choices):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Canceled")
        step_words = None
        if progress is not None:
            progress(j + 1, len(choices), label, None)
            if t_flip is not None:
                step_words = (lambda sub, j_=j + 1, lab=label: progress(j_, len(choices), lab, sub))
        try:
            row = _choice_row(S, key, label, (da, dt, dq), p_full, e, t_flip, forced, no_step, n, step_words)
        except ValueError as exc:
            failed.append(label)   # Fable S3: one choice that does not fit is left out, not the whole fit
            if say:
                say(f"  detrend {label}: no fit ({exc}); left out of the choice")
            continue
        rows.append(row)
    if not rows:
        raise ValueError("the least-squares fit did not converge with any detrend choice")
    return _pick_choice(rows, failed, S, t_flip, gated, scale, allow_airmass, say)


def _choice_row(S, key, label, flags, p_full, e, t_flip, forced, no_step, n, step_words=None) -> dict:
    """One row of choose_detrend: the choice fitted without and (on a flip night) with the step. step_words(sub)
    (2.4.0 fit-in F2): tells the busy bar which of the two fits runs."""
    t, flux, am = S["t"], S["flux"], S["am"]
    da, dt, dq = flags
    st = {nm: v for nm, v in p_full.items() if nm != "step"}
    for nm, on in (("c_am", da), ("c_t", dt), ("c_t2", dt and dq)):
        if not on:
            st[nm] = 0.0
    if forced:
        prob_ns = None
    else:
        if step_words is not None:
            step_words("without the flip step")
        prob_ns = TransitProblem(t, flux, e, am, S["period"], S["priors"], S["free"], st, da, dt, S["ld"], None,
                                 detrend_curve=dq)
        x_ns, chi2_ns = best_fit(prob_ns, no_step[:2])
        bic_ns = chi2_ns + len(prob_ns.free) * math.log(n)
    row = {"key": key, "label": label, "flags": (da, dt, dq)}
    if t_flip is not None:
        if step_words is not None:
            step_words("with the flip step")
        st_ws = dict(prob_ns.full(x_ns), step=0.0) if prob_ns is not None else dict(st, step=p_full.get("step", 0.0))
        prob_ws = TransitProblem(t, flux, e, am, S["period"], S["priors"], S["free"], st_ws, da, dt, S["ld"], t_flip,
                                 detrend_curve=dq)
        x_ws, chi2_ws = best_fit(prob_ws, [{"step": 0.02}, {"step": -0.02}])
        bic_ws = chi2_ws + len(prob_ws.free) * math.log(n)
        try:
            e_ws = lsq_errors(prob_ws)
            s_err = float(e_ws[prob_ws.free.index("step")])
        except Exception:
            s_err = float("nan")
        s_val = float(prob_ws.full(x_ws)["step"])
        if forced:
            row.update(step=(s_val, s_err), dbic_step=float("nan"), kept=True, bic=bic_ws, k=len(prob_ws.free),
                       chi2_step=chi2_ws, chi2_nostep=float("nan"))
        else:
            dbs = bic_ns - bic_ws
            kept = dbs >= FLIP_STEP_DBIC
            row.update(step=(s_val, s_err), dbic_step=float(dbs), kept=bool(kept),
                       bic=bic_ws if kept else bic_ns, k=len(prob_ws.free) if kept else len(prob_ns.free),
                       chi2_step=chi2_ws, chi2_nostep=chi2_ns)
    else:
        row.update(bic=bic_ns, k=len(prob_ns.free))
    # Fable S1: the least-squares depth of the variant this row stands for (with the step when it is kept)
    if t_flip is not None and (forced or row.get("kept")):
        row["depth"] = float(prob_ws.full(x_ws)["k"] ** 2)
    else:
        row["depth"] = float(prob_ns.full(x_ns)["k"] ** 2)
    return row


def _pick_choice(rows, failed, S, t_flip, gated, scale, allow_airmass, say) -> dict:
    bmin = min(r["bic"] for r in rows)
    near = [r for r in rows if r["bic"] <= bmin + DETREND_TIE]
    # fewest parameters among the near-equal ones; at the same count, the lower BIC
    chosen = min(near, key=lambda r: (r["k"], r["bic"], rows.index(r)))
    for r in rows:
        r["dbic"] = float(r["bic"] - chosen["bic"])
    flip_test = None
    if gated:
        flip_test = {"t_flip": t_flip + S["t_ref"], "step": chosen["step"], "dbic": chosen["dbic_step"],
                     "kept": chosen["kept"], "threshold": FLIP_STEP_DBIC, "gap_min": S.get("flip_gap_min"),
                     "error_scale": float(scale), "chi2_step": float(chosen["chi2_step"]),
                     "chi2_nostep": float(chosen["chi2_nostep"]), "detrend": chosen["label"]}
    out = {"key": chosen["key"], "label": chosen["label"], "flags": chosen["flags"], "rows": rows,
           "scale": float(scale), "flip_test": flip_test, "airmass_tried": bool(allow_airmass), "failed": failed,
           "dropped_no_airmass": int(S.get("dropped_no_airmass") or 0)}
    if say:
        say("  " + detrend_choice_text(out))
    return out


def tc_detrend_words(result: dict) -> str:
    """The detrend a fit used, for summaries: 'airmass, time curve (Auto, the lowest BIC)'."""
    r = result or {}
    return detrend_text(r.get("detrend")) + (" (Auto, the lowest BIC)" if r.get("detrend_choice") else "")


def curve_depth_note(result: dict, ascii_only: bool = False) -> str:
    """2.4.0 (John 19 Nov, Fable S1): when Auto chose a time curve, the depth with it and the depth the best choice
    without a curve gives, because a curve can trade against the depth (TrES-3 b 4 Sep: 4.84 % with airmass + time
    curve, 3.56 % with airmass alone). A plain note; '' otherwise."""
    r = result or {}
    ch = r.get("detrend_choice")
    s = r.get("summary") or {}
    if not ch or not ch["flags"][2] or "depth" not in s:
        return ""
    other = [row for row in ch["rows"] if not row["flags"][2] and row.get("depth") is not None]
    if not other:
        return ""
    alt = min(other, key=lambda row: row["bic"])
    d = "dBIC" if ascii_only else "ΔBIC"
    return (f"Auto chose {ch['label']}: depth {s['depth'][0] * 100:.2f} % with the time curve; the best choice "
            f"without it, {alt['label']} ({d} {alt['dbic']:+.1f}), gives {alt['depth'] * 100:.2f} % (least squares). "
            "A time curve can trade against the depth, most of all with little baseline on one side: compare the two "
            "before you rely on the depth.")


def detrend_report_note(result: dict) -> str:
    """2.4.0 (#103): one ASCII line for a report's NOTES when Auto chose the detrend: the choice and the runner-up."""
    ch = (result or {}).get("detrend_choice")
    if not ch:
        return ""
    others = sorted((r for r in ch["rows"] if r["key"] != ch["key"]), key=lambda r: r["dbic"])
    nxt = f" (next {others[0]['label']}, dBIC {others[0]['dbic']:+.1f})" if others else ""
    return f"SHOBS-P: detrend chosen by BIC: {ch['label']}{nxt}"


def detrend_choice_text(choice: dict, ascii_only: bool = False) -> str:
    """'Detrend Auto (BIC): airmass + time curve; the others: none ΔBIC +45, ...' (ΔBIC against the chosen one)."""
    if not choice:
        return ""
    d = "dBIC" if ascii_only else "ΔBIC"   # ΔBIC = that choice's BIC − the chosen one's (> 0: worse)
    others = [f"{r['label']} {d} {r['dbic']:+.1f}" for r in choice["rows"] if r["key"] != choice["key"]]
    flip = ""
    if any("kept" in r for r in choice["rows"]):
        kept = [r["label"] for r in choice["rows"] if r.get("kept")]
        flip = ("; flip step kept with " + (", ".join(kept) if kept else "none of them"))
    simpler = any(r["dbic"] < 0 for r in choice["rows"])
    text = (f"Detrend {DETREND_AUTO_LABEL}: {choice['label']}"
            + (f" (fewer terms; any choice within {DETREND_TIE:g} of the lowest BIC counts as just as good)"
               if simpler else "")
            + ("; the others: " + ", ".join(others) if others else "")
            + flip + ("" if choice.get("airmass_tried", True) else "; airmass not tried (no airmass for every point)")
            + (f"; {_frames(choice['dropped_no_airmass'])} without airmass left out of every choice"
               if choice.get("dropped_no_airmass") else "")
            + (f"; could not be fitted: {', '.join(choice['failed'])}" if choice.get("failed") else ""))
    if ascii_only:
        text = text.replace("ΔBIC", "dBIC")
    return text


def _apply_flip_gate(S: dict, detrend_airmass, detrend_time, say=None, detrend_curve=False) -> dict | None:
    """2.3.0 (#8): run the gate when S asks for it; a step that is not needed is taken out of S (no t_flip, no step
    starts), so the rest of the fit is the plain one. Returns the test (or None)."""
    if S.get("t_flip") is None or not S.get("flip_gated"):
        return None
    try:
        test = flip_step_test(S, detrend_airmass, detrend_time, detrend_curve)
    except Exception as exc:
        # 2.3.0 review fix: a test that cannot be done (a singular Jacobian in lsq_errors, a fit that fails) never
        # stops the transit fit: the fit goes on without a step and says why (S["flip_gate_error"]).
        S["t_flip"] = None
        S["starts"] = [s for s in S["starts"] if "step" not in s]
        S["flip_gate_error"] = str(exc) or type(exc).__name__
        if say:
            say(f"  meridian flip: the step test could not be done ({S['flip_gate_error']}); fitted without a step")
        return None
    if test is None:
        return None
    if say:
        say(f"  meridian flip: step {test['step'][0] * 1000:+.1f} ± {test['step'][1] * 1000:.1f} ppt, ΔBIC "
            f"{test['dbic']:.1f} (kept at {FLIP_STEP_DBIC:g} or more): "
            + ("kept, fitted with the transit" if test["kept"] else "not needed, fitted without a step"))
    if not test["kept"]:
        S["t_flip"] = None
        S["starts"] = [s for s in S["starts"] if "step" not in s]
    return test


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
                                 "c_t" in prob_s.free, setup["ld"], detrend_curve="c_t2" in prob_s.free)
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


def _setup_and_detrend(t, flux, err, airmass, planet, detrend_airmass, detrend_time, detrend_curve, detrend_auto,
                       after_flip, flip_step, flagged_mask, say=None, cancel=None, progress=None):
    """2.4.0 (#103): _setup_fit, then the detrend: Auto (choose_detrend, the flip step tested inside each choice) or
    the given one (with the usual flip gate). Returns (S, (airmass, time, curve), flip_test, choice or None)."""
    if detrend_auto:
        allow = airmass_usable(airmass)
        dropped = 0
        if not allow and airmass is not None:
            # Fable S2: a FEW frames without airmass (at most 10, or 5 % of the night when that is more; re-review
            # R1): as manual airmass does, those frames are left out, so every choice is fitted on the same frames.
            # With more missing, Auto leaves airmass out instead and keeps every frame.
            am_ = np.asarray(airmass, dtype=float)
            fin = np.isfinite(am_)
            few = max(AUTO_AIRMASS_DROP_MAX, int(AUTO_AIRMASS_DROP_FRAC * am_.size))
            if (am_.size and 0 < int((~fin).sum()) <= few and fin.sum() >= 20
                    and np.ptp(am_[fin]) > 1e-4):
                allow, dropped = True, int((~fin).sum())
        S = _setup_fit(t, flux, err, airmass, planet, allow, after_flip, flip_step, flagged_mask)
        S["dropped_no_airmass"] = dropped
        if dropped and say:
            say(f"  {_frames(dropped)} without airmass left out, so every detrend choice is fitted on the same frames")
        choice = choose_detrend(S, allow, say, cancel, progress)
        flags = choice["flags"]
        flip_test = choice["flip_test"]
        if S.get("t_flip") is not None and S.get("flip_gated") and flip_test is not None:
            if say:
                ft = flip_test
                say(f"  meridian flip: step {ft['step'][0] * 1000:+.1f} ± {ft['step'][1] * 1000:.1f} ppt, ΔBIC "
                    f"{ft['dbic']:.1f} with {choice['label']} (kept at {FLIP_STEP_DBIC:g} or more): "
                    + ("kept, fitted with the transit" if ft["kept"] else "not needed, fitted without a step"))
            if not flip_test["kept"]:
                S["t_flip"] = None
                S["starts"] = [st for st in S["starts"] if "step" not in st]
        return S, flags, flip_test, choice
    flags = (bool(detrend_airmass), bool(detrend_time), bool(detrend_time and detrend_curve))
    S = _setup_fit(t, flux, err, airmass, planet, detrend_airmass, after_flip, flip_step, flagged_mask)
    flip_test = _apply_flip_gate(S, flags[0], flags[1], say, flags[2])
    return S, flags, flip_test, None


def lsq_fit(t, flux, err, airmass, planet: dict, detrend_airmass=True, detrend_time=False, after_flip=None,
            flip_step=None, flagged_mask=None, detrend_curve=False, detrend_auto=False, progress=None) -> dict:
    """2.2.13: the fit_transit model by least squares only (no MCMC), with Jacobian errors after the same error
    rescaling (reduced chi-square and red-noise beta). For fast injection-recovery tests of the flip step.
    2.2.16 (item 6): flagged_mask (flagged frames put back, error bars doubled) as in fit_transit.
    progress (2.4.0 fit-in F2): Auto's step words, as choose_detrend gives them (j, n, label, sub)."""
    # 2.3.0 (#8): the step only when BIC wants it. 2.4.0 (#103): the detrend given, or Auto.
    S, (detrend_airmass, detrend_time, detrend_curve), flip_test, choice = _setup_and_detrend(
        t, flux, err, airmass, planet, detrend_airmass, detrend_time, detrend_curve, detrend_auto, after_flip,
        flip_step, flagged_mask, progress=progress)
    t, flux, err, am = S["t"], S["flux"], S["err"], S["am"]
    prob = TransitProblem(t, flux, err, am, S["period"], S["priors"], S["free"], S["start"], detrend_airmass,
                          detrend_time, S["ld"], S["t_flip"], detrend_curve=detrend_curve)
    x, chi2 = best_fit(prob, S["starts"])
    rchi = chi2 / max(len(t) - len(prob.free), 1)
    beta = red_noise_beta(t, flux - prob.model(prob.full(x)), S["cadence"])
    scale = math.sqrt(max(rchi, 1.0)) * beta
    prob_s = TransitProblem(t, flux, err * scale, am, S["period"], S["priors"], S["free"], prob.full(x),
                            detrend_airmass, detrend_time, S["ld"], S["t_flip"], detrend_curve=detrend_curve)
    x_s, chi2_s = best_fit(prob_s)
    e = lsq_errors(prob_s)
    p = prob_s.full(x_s)
    out = {"p": dict(p, tc=p["tc"] + S["t_ref"]), "err": dict(zip(prob_s.free, map(float, e))), "chi2": chi2_s,
           "error_scale": scale, "rchi_raw": float(rchi), "free": list(prob_s.free), "t_flip": (S["t_flip"] + S["t_ref"]) if prob_s.t_flip
           is not None else None, "cadence": S["cadence"], "t": S["t_abs"], "flip_test": flip_test,
           "flip_gate_error": S.get("flip_gate_error"),
           "detrend": detrend_names((detrend_airmass, detrend_time, detrend_curve), prob_s.has_airmass),
           "detrend_flags": (bool(detrend_airmass and prob_s.has_airmass), bool(detrend_time), bool(detrend_curve)),
           "detrend_choice": choice}
    try:   # 2.4.0 (#35): the residual rms (flux units), for the robustness check
        out["rms"] = float(np.std(flux - prob_s.model(p)))
    except Exception:
        out["rms"] = float("nan")
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
                flip_step=None, flagged_mask=None, flagged_label: str = "flagged", excluded=None,
                detrend_curve=False, detrend_auto=False) -> dict:
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
    # 2.3.0 (#8): with a flip, fit with and without the step first; the step stays only when ΔBIC >= FLIP_STEP_DBIC.
    # 2.4.0 (#103): detrend_auto: every detrend choice is fitted (the flip step tested inside each) and the lowest BIC
    # wins; detrend_curve adds the time curve to the time slope.
    if detrend_auto:
        say("Transit fit: choosing the detrend (Auto: the lowest BIC)…")
    S, (detrend_airmass, detrend_time, detrend_curve), flip_test, choice = _setup_and_detrend(
        t, flux, err, airmass, planet, detrend_airmass, detrend_time, detrend_curve, detrend_auto, after_flip,
        flip_step, flagged_mask, say, cancel,
        (lambda j, m, label=None, sub=None: progress("detrend", j, m, label, sub)) if progress is not None else None)
    t, t_abs, t_ref, flux, err, am = S["t"], S["t_abs"], S["t_ref"], S["flux"], S["err"], S["am"]
    period, priors, free, ld, cadence, starts = S["period"], S["priors"], S["free"], S["ld"], S["cadence"], S["starts"]
    t_flip = S["t_flip"]

    prob = TransitProblem(t, flux, err, am, period, priors, free, S["start"], detrend_airmass, detrend_time, ld,
                          t_flip, detrend_curve=detrend_curve)
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
                            detrend_time, ld, t_flip, detrend_curve=detrend_curve)
    x_s, chi2_s = best_fit(prob_s)
    n_burn = max(500, mcmc_steps // 3)
    say(f"  MCMC: {max(2 * len(prob_s.free) + 2, 20)} walkers, {n_burn} burn-in + {mcmc_steps} steps…")
    chain = run_mcmc(prob_s, x_s, n_walkers=20, n_burn=n_burn, n_steps=mcmc_steps, progress=progress, cancel=cancel)
    summary = summarize(prob_s, chain["flat"])
    best = prob_s.full(np.array([summary[p][0] for p in prob_s.free]))
    # 2.4.0 (#62): the prayer-bead check; its error is reported where it is larger than the MCMC's
    pb, pb_raised = None, []
    try:
        if progress is not None:
            progress("prayer", 0, 1)
        pb = prayer_bead(prob_s, best, cancel=cancel)
        mc = {name: summary[name][1] for name in ("tc", "depth", "k") if name in summary}
        pb_raised = apply_prayer_bead(summary, pb)
        if pb:
            say(f"  prayer bead ({pb['n']} shifted refits): Tc ± {pb['err'].get('tc', float('nan')) * 1440:.2f} min "
                f"(MCMC ± {mc.get('tc', float('nan')) * 1440:.2f}), depth ± {pb['depth_err'] * 100:.3f} % "
                f"(MCMC ± {mc.get('depth', float('nan')) * 100:.3f})"
                + (f"; the larger is reported for {', '.join(PARAM_WORDS.get(n_, n_) for n_ in pb_raised)}"
                   if pb_raised else "; MCMC errors kept"))   # step 6b (John 19 Nov): "Rp/R*", not "k"
            pb["mcmc"] = mc
            pb["raised"] = pb_raised
    except Exception as exc:
        if str(exc) == "Canceled":
            raise
        say(f"  prayer-bead check skipped: {exc}")
    # Free shape check (no priors on a/R* and b), least squares only.
    free_fit = None
    try:
        prob_f = TransitProblem(t, flux, err * scale, am, period, {}, free, best, detrend_airmass, detrend_time, ld,
                                t_flip, detrend_curve=detrend_curve)
        xf, chi2f = best_fit(prob_f, [{"b": 0.1}, {"b": 0.5}, {"b": 0.8}, {"b": 0.95, "k": best["k"] * 1.3},
                                      {"b": min(1.0 + best["k"] * 1.5, 1.4), "k": best["k"] * 2.0}])
        pf = prob_f.full(xf)
        pf["tc"] += t_ref
        free_fit = {"p": pf, "chi2": chi2f, "depth": pf["k"] ** 2,
                    "t14": total_duration(period, pf["a_rs"], min(pf["b"], pf["a_rs"] * 0.999), pf["k"])}
        # 2.2.19 (nit): a free shape that runs away (Jeff's TrES-2 b: 16 % depth) is "not constrained", not numbers.
        try:
            import transit_posterior as tpost
            ef = dict(zip(prob_f.free, map(float, lsq_errors(prob_f))))
            pub, pub_label = published_depth(planet)
            ref, ref_label = (pub, f"published {pub_label}") if pub else (summary["depth"][0], "MCMC depth")
            free_fit["err"] = ef
            free_fit["unconstrained"] = tpost.free_shape_unconstrained(free_fit, ef, ref, ref_label)
        except Exception:
            free_fit["unconstrained"] = ""
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
    # 2.2.19 (#31): parameters pinned against their allowed range, and the credible bands for the plot (from the same
    # chain; capped draws, so a second or less). Neither may cost the fit.
    bound_flags, bands = [], None
    try:
        import transit_posterior as tpost
        bound_flags = tpost.prior_bound_flags(prob_s, chain["flat"])
        bands = tpost.credible_bands(prob_s, chain["flat"], best)
        bands["t"] = bands["t"] + t_ref
    except Exception as exc:
        say(f"  posterior checks skipped: {exc}")
    best["tc"] += t_ref
    summary["tc"] = (summary["tc"][0] + t_ref, summary["tc"][1])
    fmask = S["flagged"]
    result = {
        "t": t_abs, "t_ref": t_ref, "flux": flux, "err": err, "err_scaled": err * scale, "airmass": am, "model": model,
        "baseline": base, "detrended": flux / base, "residuals": resid, "best": best, "summary": summary,
        "free_params": list(prob_s.free), "priors": priors, "ld_prior": ld, "rchi2": rchi, "beta": beta,
        "ld_band": limb_darkening_band(planet.get("ld_band")),   # 2.2.17 (item 1): '' = the V / clear table
        "error_scale": scale, "rms": float(np.std(resid)), "cadence_days": cadence, "chain": chain,
        # 2.2.14 (GitHub #5): rms of 10-minute bins, as AstroImageJ shows it.
        "rms_bin10": binned_rms(t_abs, resid, 10.0),
        "tc_pred": S["tc_pred"], "tc_pred_err": S["tc_pred_err"], "epoch": S["n"], "period": period,
        "planet": dict(planet), "eph_err_known": S["eph_err_known"],
        "eph_source": ephemeris_source(planet) if S["has_eph"] else "",
        "tc_start": S["tc_start"],
        "free_fit": free_fit, "n_points": len(t), "flux_median": S["med"],
        "detrend": detrend_names((detrend_airmass, detrend_time, detrend_curve), prob_s.has_airmass),
        # 2.4.0 (#103): what the fit used (airmass, time slope, time curve), and Auto's table (None: chosen by hand)
        "detrend_flags": (bool(detrend_airmass and prob_s.has_airmass), bool(detrend_time), bool(detrend_curve)),
        "detrend_choice": choice,
        # 2.2.13
        "flip": flip, "flagged": fmask,
        "flip_test": flip_test,   # 2.3.0 (#8): the with/without-step BIC test (None: no flip, or not gated)
        "flip_gate_error": S.get("flip_gate_error"),   # 2.3.0 review: the test failed, fitted without a step
        "with_flagged": ({"n": int(fmask.sum()), "label": flagged_label, "factor": FLAGGED_ERR_FACTOR}
                         if fmask.any() else None),
        "excluded": excluded if excluded and len(np.atleast_1d(excluded.get("t", []))) else None,
        "bound_flags": bound_flags, "bands": bands,   # 2.2.19 (#31)
        "prayer_bead": pb,   # 2.4.0 (#62)
    }
    result["verdict"] = transit_verdict(result, prob_s)
    _both_detrend_check(result, prob_s, say)   # 2.2.19 (nit)
    result["flags"] = transit_flags(result)
    v = result["verdict"]
    v["depth_unreliable"] = depth_unreliable(result) if v["state"] == "detected" else ""
    _size_verdict(result)   # 2.4.0 (#70)
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


def _size_verdict(result: dict) -> None:
    """2.4.0 (#70): "Detected" says a transit is there and timed; the size is a separate check. A detected depth
    more than SIZE_SIGMA outside the published (Rp/R*)² range gets v["size_caution"] (shown with ⚠ beside the
    verdict, written to the report's NOTES); inside a range of two or more values, v["size_note"] says so plainly.
    Not when the depth is already called unreliable (that says more)."""
    v = result.get("verdict") or {}
    v["size_caution"], v["size_note"] = "", ""
    if v.get("state") != "detected":
        return
    try:
        sc = size_check(result)
    except Exception:
        sc = None
    v["size_check"] = sc
    if not sc:
        return
    if sc["state"] == "outside" and not v.get("depth_unreliable"):
        v["size_caution"] = sc["text"]
    elif sc["state"] == "inside" and len(sc["range"]["values"]) > 1:
        v["size_note"] = f"Depth within the published range {sc['range_text']}."


def _both_detrend_check(result: dict, prob_s, say) -> None:
    """2.2.19 (nit): detrend "Both" (airmass + time slope) found no transit: refit with each alone (least squares, only
    in this case) and, when one of them detects it, say so in the verdict instead of a plain "no detection"."""
    v = result["verdict"]
    if not (v["state"] == "none" and v["dbic"] < 10 and "c_am" in prob_s.free and "c_t" in prob_s.free):
        return
    try:
        import transit_posterior as tpost
        best_rel = dict(result["best"], tc=result["best"]["tc"] - result["t_ref"])
        # From the Both fit and the published Rp/R* only: ~1-2 s (all of the fit's starts took ~4x as long, same answer
        # on KOI-217 b's Both night).
        k0 = (result.get("planet") or {}).get("k")
        check = tpost.single_detrend_dbic(prob_s, best_rel, [{"k": k0}] if k0 else [])
        text = tpost.both_detrend_text(check)
    except Exception as exc:
        say(f"  single-detrend check skipped: {exc}")
        return
    v["both_check"] = check
    if text:
        v["both_absorbs"] = text
        v["reason"] = text + " " + v["reason"]
        say("  " + text)


RCHI2_LOW = 0.5    # 2.2.17 (item 8): reduced chi-square below this: error bars too large (error model)
RCHI2_HIGH = 2.0   # 2.2.17 (item 8): above this: noise the error model leaves out


# 2.2.16 (item 6): leave-one-comp-out check. A comp is named in a caution only when (1) leaving it out moves the
# depth or Tc by more than COMP_LOO_SIGMA sigma of the main fit, (2) that move is more than COMP_LOO_NOISE times what
# the comp's own frame-to-frame noise would cause, and (3) it is the odd one out: its move is opposite in sign to every
# other comp's and at least COMP_LOO_ODD times the largest of them. Shifts are measured against the full ensemble, so
# one bad comp also moves every good comp's refit the other way (its weight grows); the odd-one-out rule keeps those
# good comps from being named. With 2 comps the two refits are mirror images, so no comp is ever named. A significant
# move with no odd one out gives an unnamed caution. (TOI-2046 b, 14-15 Oct: four comp sets gave depths 1.46-1.65 %.)
COMP_LOO_SIGMA = 1.0
COMP_LOO_NOISE = 3.0
COMP_LOO_ODD = 1.5


def _pct(v: float) -> str:
    return f"{v * 100:.2f} %"


def _p2p(x) -> float:
    """Point-to-point white noise of a series: rms of first differences / sqrt(2) (slow trends cancel)."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if x.size < 3:
        return float("nan")
    d = np.diff(x)
    return float(np.sqrt(np.mean(d * d)) / math.sqrt(2.0))


def _odd_one_out(shifts) -> int | None:
    """Index of the comp whose shift is opposite in sign to all others and >= COMP_LOO_ODD x the largest of them;
    None with fewer than 3 shifts or when no comp stands out."""
    s = np.asarray(shifts, dtype=float)
    if s.size < 3 or not np.all(np.isfinite(s)):
        return None
    j = int(np.argmax(np.abs(s)))
    others = np.delete(s, j)
    if s[j] == 0 or np.any(np.sign(others) == np.sign(s[j])):
        return None
    if abs(s[j]) < COMP_LOO_ODD * float(np.max(np.abs(others))):
        return None
    return j


# 2.3.5 (#88): the same rule as photometry_core.night_ensemble (2.3.0, #50), on a points x comps matrix, so the
# leave-one-comp-out refits use the frames the main fit uses. The two must stay in step (tests/test_235.py).
COMP_TIE_MIN_FRAMES = 5
COMP_NIGHT_SHARE = 0.5


def _omean(values) -> float:
    """2.4.0 step 6c: the order-free mean (photometry_core.omean: math.fsum), so the comps' order does not change
    the bits."""
    vals = [float(v) for v in np.ravel(np.asarray(values, dtype=float))]
    return math.fsum(vals) / len(vals) if vals else float("nan")


def tied_ensemble(cm) -> np.ndarray:
    """2.3.5 (#88): the comp ensemble per frame on the night's scale, from cm (points x comps, NaN = not measured).

    A frame with every comp keeps the plain mean (so a night with no missing comp gives exactly np.mean). A frame
    without some comps gets the mean the full set would have had: each comp's offset a_j (its median above the
    frame level) is taken out of the comps present and the mean offset put back, as night_ensemble does. Comps
    measured in fewer than COMP_TIE_MIN_FRAMES frames, or in fewer than COMP_NIGHT_SHARE of them, are left out of
    the whole night (as the photometry does); when that would leave none, they are kept where measured."""
    m = np.asarray(cm, dtype=float)
    n = m.shape[0]
    if m.ndim != 2 or n == 0 or m.shape[1] == 0:
        return np.full(n, np.nan)
    counts = np.isfinite(m).sum(axis=0)
    keep = [j for j, c in enumerate(counts) if c > 0 and not (c < min(COMP_TIE_MIN_FRAMES, n)
                                                               or c < COMP_NIGHT_SHARE * n)]
    if not keep:
        keep = [j for j, c in enumerate(counts) if c > 0]
    if not keep:
        return np.full(n, np.nan)
    mk = m[:, keep]
    ok = np.isfinite(mk)
    a = np.zeros(len(keep))
    with np.errstate(invalid="ignore"):
        for _ in range(12):
            level = np.array([_omean(mk[f, ok[f]] - a[ok[f]]) if ok[f].any() else np.nan for f in range(n)])
            new_a = np.array([float(np.median(mk[ok[:, j], j] - level[ok[:, j]])) for j in range(len(keep))])
            new_a -= _omean(new_a)
            if np.allclose(new_a, a, atol=1e-7):
                a = new_a
                break
            a = new_a
    ens = np.full(n, np.nan)
    for f in range(n):
        if not ok[f].any():
            continue
        ens[f] = _omean(mk[f]) if ok[f].all() else _omean(mk[f, ok[f]] - a[ok[f]]) + _omean(a)   # step 6c: order-free
    return ens


def leave_one_comp_out(t, flux, err, airmass, comp_mags, names, planet: dict, main_summary: dict,
                       detrend_airmass=True, detrend_time=False, after_flip=None, flagged_mask=None, progress=None,
                       cancel=None, log=None, flip_step=None, detrend_curve=False,
                       flip_gated: bool = False) -> dict | None:
    """2.2.16 (item 6): refit the light curve once per comp with that comp left out of the ensemble.

    flux/err are the main fit's data (target against the mean of all comps); comp_mags (points x comps) holds each
    comp's instrumental magnitude in the same frames and names their names. The ensemble is the mean of the comps'
    instrumental magnitudes, so leaving comp j out multiplies each point by 10^(0.4 (ens_without_j - ens_all)). The
    relative error bars are kept as they are, so each refit's errors are APPROXIMATE (a real rerun would also change
    the ensemble's error term); only the refits' best values are used. Each refit is lsq_fit (least squares, no MCMC)
    with the same planet priors, detrending, flip and flagged frames as the main fit; shifts are measured against a
    least-squares fit of the full ensemble and compared with the main fit's (MCMC) uncertainties, with the noise
    each comp's removal adds by itself, and with the other comps' shifts (see COMP_LOO_*). Returns None with fewer
    than 2 comps or when no refit could be made. cancel: a threading.Event; set, it raises RuntimeError("Canceled")
    between refits."""
    say = log or (lambda _m: None)
    cm = np.asarray(comp_mags, dtype=float)
    if cm.ndim != 2 or cm.shape[1] < 2 or cm.shape[0] != len(np.atleast_1d(flux)):
        return None
    m = cm.shape[1]
    names = [str(n) for n in names][:m] + [f"comp {k + 1}" for k in range(len(names), m)]
    flux = np.asarray(flux, dtype=float)
    err = np.asarray(err, dtype=float)
    # 2.3.0 (#8): flip_step = the main fit's decision (True kept, False not needed), for the reference fit.
    # 2.4.0 step 6c-2 (John 20 Nov): with flip_gated (the main step came from the BIC gate) each refit decides its
    # OWN step by the same gate: on WASP-49 b 19 Nov two comps step ~+-65 ppt at the flip and cancel in the set, so
    # the night without either one needs a step that the full set does not.
    kw = dict(detrend_airmass=detrend_airmass, detrend_time=detrend_time, after_flip=after_flip,
              flagged_mask=flagged_mask, flip_step=None if flip_gated else flip_step, detrend_curve=detrend_curve)
    ref_kw = dict(kw, flip_step=flip_step)
    # 2.3.5 (#88): np.mean made the ensemble NaN in every frame where any comp was missing (a comp off the frame
    # edge), so each refit lost those frames while the main fit kept them.
    ens_all = tied_ensemble(cm)
    try:
        ref = lsq_fit(t, flux, err, airmass, planet, **ref_kw)
    except Exception as exc:
        say(f"  comp check skipped: no least-squares fit of the full ensemble ({exc})")
        return None
    ref_depth, ref_tc = float(ref["depth"][0]), float(ref["p"]["tc"])
    ref_n = int(len(ref["t"]))
    ref_sd, ref_st = float(ref["depth"][1]), float(ref["err"].get("tc", float("nan")))
    # 2.2.16 (round-2 review): lsq_fit never scales error bars DOWN (reduced chi-square < 1, as on TOI-2046 b at
    # 0.34-0.44); the noise gate uses the sigma the scatter supports.
    shrink = math.sqrt(min(float(ref.get("rchi_raw", 1.0)), 1.0)) if math.isfinite(ref.get("rchi_raw", 1.0)) else 1.0
    ref_sd, ref_st = ref_sd * shrink, ref_st * shrink
    sd = float(main_summary["depth"][1]) if "depth" in main_summary else float("nan")
    st = float(main_summary["tc"][1]) if "tc" in main_summary else float("nan")
    used = np.isfinite(flux)
    if flagged_mask is not None:
        try:
            used &= ~np.asarray(flagged_mask, dtype=bool)
        except Exception:
            pass
    noise_main = _p2p(flux[used])
    fits, skipped = [], []
    for j in range(m):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Canceled")
        if progress is not None:
            progress(j + 1, m)
        with np.errstate(invalid="ignore", over="ignore"):
            ens = tied_ensemble(np.delete(cm, j, axis=1))
            factor = 10 ** (0.4 * (ens - ens_all))
        # Shift expected from this comp's own white noise alone: the main fit's (least-squares) sigma scaled by the
        # noise the swap adds relative to the light curve's own point-to-point noise.
        noise_j = _p2p(((flux * factor) - flux)[used])
        ratio = noise_j / noise_main if math.isfinite(noise_main) and noise_main > 0 else float("nan")
        try:
            f = lsq_fit(t, flux * factor, err * factor, airmass, planet, **kw)
        except Exception as exc:
            # No usable points (or no fit) without this comp: skipped silently on the page, noted in the log.
            skipped.append(names[j])
            say(f"  comp check: leaving out {names[j]}: no fit ({exc})")
            continue
        fits.append({"name": names[j], "depth": float(f["depth"][0]), "tc": float(f["p"]["tc"]),
                     "d_depth": float(f["depth"][0]) - ref_depth, "d_tc": float(f["p"]["tc"]) - ref_tc,
                     "n_points": int(len(f["t"])), "exp_depth": ref_sd * ratio, "exp_tc": ref_st * ratio,
                     "step": f.get("t_flip") is not None})   # 6c-2: this refit kept a flip step
    if not fits:
        return None

    def significant(f, key, sig, exp_key):
        d, e = abs(f[key]), f[exp_key]
        return (math.isfinite(sig) and sig > 0 and d > COMP_LOO_SIGMA * sig
                and (not math.isfinite(e) or d > COMP_LOO_NOISE * e))

    for f in fits:
        f["sig_depth"] = significant(f, "d_depth", sd, "exp_depth")
        f["sig_tc"] = significant(f, "d_tc", st, "exp_tc")
        f["flagged"] = False
    complete = len(fits) == m          # the odd-one-out test needs every comp's refit
    flags = []
    unnamed = []
    for key, sig_key, sig in (("d_depth", "sig_depth", sd), ("d_tc", "sig_tc", st)):
        if not any(f[sig_key] for f in fits):
            continue
        j = _odd_one_out([f[key] for f in fits]) if complete and m >= 3 else None
        if j is not None and fits[j][sig_key]:
            fits[j]["flagged"] = True
            fits[j].setdefault("why", []).append(key)
        else:
            unnamed.append(key)
    for f in fits:
        if not f["flagged"]:
            continue
        parts = []
        if "d_depth" in f["why"]:
            parts.append(f"makes the transit {_pct(abs(f['d_depth']))} "
                         f"{'deeper' if f['d_depth'] > 0 else 'shallower'} ({abs(f['d_depth']) / sd:.1f}σ)")
        if "d_tc" in f["why"]:
            parts.append(f"moves Tc {abs(f['d_tc']) * 86400:.0f} s {'later' if f['d_tc'] > 0 else 'earlier'} "
                         f"({abs(f['d_tc']) / st:.1f}σ)")
        if f.get("step") != (ref.get("t_flip") is not None):   # 6c-2: say when its refit decided the step otherwise
            parts[-1] += (" (the night without it needs a flip step of its own)" if f.get("step")
                          else " (the night without it needs no flip step)")
        flags.append(("warn", f"Leaving out {f['name']} " + " and ".join(parts) + ". The comp choice moves this "
                              "result by more than its statistical error: check that comp (variable? a trend?) "
                              "before reporting."))
    if unnamed:
        what = " and ".join({"d_depth": "the depth", "d_tc": "Tc"}[k] for k in unnamed)
        why = ("with 2 comps the two refits mirror each other, so neither can be blamed" if m == 2 else
               "no single comp stands out")
        flags.append(("warn", f"The comp choice moves {what} by more than its statistical error ({why}). Check "
                              "the comps (Use comps… on the Output page) before reporting; the spread below is a "
                              "diagnostic, not an error bar."))
    depths = [f["depth"] for f in fits]
    tcs = [f["tc"] for f in fits]
    out = {"n_comps": m, "names": names, "fits": fits, "skipped": skipped, "single": m == 2,
           "ref": {"depth": ref_depth, "tc": ref_tc, "n_points": ref_n}, "depth_min": min(depths), "depth_max": max(depths),
           "depth_spread": max(depths) - min(depths), "tc_spread_s": (max(tcs) - min(tcs)) * 86400.0,
           "sigma_depth": sd, "sigma_tc": st, "unnamed": unnamed}
    stat = f"; statistical ± {_pct(sd)}" if math.isfinite(sd) else ""
    # 2.3.5 (#88): the full-ensemble depth and points beside the range, so the range can be read against them.
    note = (f"Comp choice check (each of the {m} comps left out in turn, least squares): depth "
            f"{out['depth_min'] * 100:.2f}–{_pct(out['depth_max'])} beside {_pct(ref_depth)} with all comps "
            f"({ref_n} points; spread {_pct(out['depth_spread'])}{stat}), Tc spread {out['tc_spread_s']:.0f} s.")
    fewer = [f"{f['name']} ({f['n_points']})" for f in fits if f["n_points"] < ref_n]
    out["fewer"] = fewer
    if fewer:
        note += (" Fewer points without " + ", ".join(fewer) + ": the other comps do not cover those frames.")
    if out["single"]:
        note += " With 2 comps each refit uses a single comp."
    if skipped:
        note += f" No refit without {', '.join(skipped)} (too few usable points)."
    # step 6c-2 (Fable S3): with its own flip step a refit can hide a comp that jumps at the flip (its shift is
    # small once the step absorbs it), so say which refits decided the step otherwise, flagged or not
    ref_step = ref.get("t_flip") is not None
    other = [f["name"] for f in fits if f.get("step") != ref_step]
    out["step_other"] = other
    if other:
        note += (f" Without {', '.join(other)} the night needs "
                 + ("no flip step" if ref_step else "a flip step of its own")
                 + (": that comp jumps" if len(other) == 1 else ": those comps jump")
                 + " at the meridian flip, so check it before reporting.")
    if not flags and math.isfinite(sd) and math.isfinite(st):
        note += f" No comp moves the depth or Tc by more than {COMP_LOO_SIGMA:g}σ beyond its own noise."
    out["note"] = note
    out["flags"] = flags + [("info", note)]
    out["text"] = comp_systematic_text(out)
    return out


# ---- 2.4.0 step 6b (#35): the robustness check -------------------------------------------------------------
# A beta tester (25 Oct 2026): the depth and Tc can move with reasonable reduction choices; make that visible. The
# check refits the night (least squares, no MCMC, the main fit's priors and flip-step decision) once per reasonable
# variation and reports how far the depth, Tc and rms move, beside the main fit's formal errors. "Spread" is half the
# full range over the main least-squares fit and every variation (± spread), so it reads like the formal ±.
# ⚠ only when a spread is larger than its formal error (John, 19 Nov); otherwise a plain note.
ROBUST_DBIC_MAX = 10.0   # a detrend choice whose BIC is more than this above the best is not a reasonable choice
ROBUST_TRIM = 0.10       # the first and the last 10 % of the points (each its own refit)
ROBUST_APERTURE = 0.20   # aperture r x 0.8 and x 1.2
ROBUST_GROUPS = (("detrend", "detrend choice"), ("trim", "trim first/last 10 %"), ("flagged", "flagged frames"),
                 ("comp", "each comp left out"), ("aperture", "aperture ±20 %"), ("sky", "sky ring shifted"))


def aperture_variants(radius: float, sky_in: float, sky_out: float) -> list:
    """2.4.0 (#35): the measuring variations: (group, label, r, sky in, sky out). r x 0.8 and r x 1.2 (the sky ring
    moved out with r when the bigger aperture would reach it, keeping the same gap), and the sky ring moved out by
    half its width. [] for an aperture that cannot be read."""
    try:
        r, si, so = float(radius), float(sky_in), float(sky_out)
    except (TypeError, ValueError):
        return []
    if not (0 < r < si < so):
        return []
    out = [("aperture", f"aperture r {r * (1 - ROBUST_APERTURE):.1f} px", round(r * (1 - ROBUST_APERTURE), 2), si, so)]
    r_up = round(r * (1 + ROBUST_APERTURE), 2)
    gap = si - r
    s_in = max(si, r_up + gap)
    out.append(("aperture", f"aperture r {r_up:.1f} px", r_up, s_in, so + (s_in - si)))
    half = 0.5 * (so - si)
    out.append(("sky", f"sky ring {si + half:.1f}–{so + half:.1f} px", r, si + half, so + half))
    return out


def _cut(arr, keep):
    return None if arr is None else np.asarray(arr)[keep]


def robustness_check(t, flux, err, airmass, planet: dict, main_summary: dict, detrend_flags=(True, False, False),
                     after_flip=None, flip_step=None, flagged_mask=None, allow_airmass: bool = True,
                     detrend_rows=None, flagged=None, comp_mags=None, comp_names=None, measured=None,
                     cancel=None, progress=None, log=None, flip_gated: bool = False,
                     detrend_progress=None) -> dict | None:
    """2.4.0 step 6b (#35): least-squares refits of one night over reasonable reduction choices.

    t, flux, err, airmass, after_flip, flagged_mask: the main fit's data and settings (flagged_mask: flagged frames
    put back with doubled errors, as in the main fit); detrend_flags: the main fit's detrend (Auto's choice);
    flip_step: its flip-step decision, used for the "as fitted" reference. 2.4.0 step 6c-2: with flip_gated (the
    main fit's step was decided by the BIC gate, not forced) every refit decides its OWN step by the same gate
    (ΔBIC >= FLIP_STEP_DBIC), so a refit whose data want a step (or no step) gets it, instead of inheriting the main
    fit's choice (WASP-49 b 19 Nov: two comps with big opposite flip steps cancelled in the set; leaving either out
    needs a step the main fit did not keep). Each row's "step" says whether that refit kept one. Variations: every other detrend choice (detrend_rows: Auto's table, choices
    more than ROBUST_DBIC_MAX above the lowest BIC are not reasonable and are left out; the airmass choices only
    with allow_airmass), the first and the last ROBUST_TRIM of the points, the flagged frames the other way (out
    when the main fit put them back; in, from `flagged` = {"t", "flux", "err", "airmass", "after_flip"}, when it
    left them out), each comp left out (comp_mags: points x comps, as leave_one_comp_out), and each re-measured
    curve in `measured` = [(group, label, factor)] with factor the per-point flux ratio (NaN where the star could
    not be measured: that point is left out of that refit). Returns {"rows": [{group, label, depth, tc, rms, n}],
    "ref", "spread": {"depth", "tc", "rms_lo", "rms_hi"}, "formal": {"depth", "tc"}, "most": {"depth", "tc"},
    "left_out": [labels], "skipped": [labels], "flags", "note", "groups"} or None without a reference fit.
    cancel: a threading.Event; set, it raises RuntimeError("Canceled") between refits.
    detrend_progress (2.4.0 fit-in F2): the hidden Auto pass's step words (j, n, label, sub), for the busy bar."""
    say = log or (lambda _m: None)
    t = np.asarray(t, dtype=float)
    flux = np.asarray(flux, dtype=float)
    err = np.asarray(err, dtype=float)
    am = None if airmass is None else np.asarray(airmass, dtype=float)
    da, dt, dq = (bool(x) for x in detrend_flags)
    base_kw = dict(detrend_airmass=da, detrend_time=dt, detrend_curve=dq,
                   flip_step=None if flip_gated else flip_step)   # 6c-2: each refit's own gate

    def fit(tt, ff, ee, aa, af, fm, **kw):
        k = dict(base_kw, **kw)
        return lsq_fit(tt, ff, ee, aa, planet, after_flip=af, flagged_mask=fm, **k)

    try:   # the reference is the night as fitted: the main fit's own step decision
        ref = fit(t, flux, err, am, after_flip, flagged_mask, flip_step=flip_step)
    except Exception as exc:
        say(f"  robustness check skipped: no least-squares fit of the night as fitted ({exc})")
        return None

    jobs = []   # (group, label, callable -> lsq_fit result)
    left_out, too_few = [], []
    # every other detrend choice that is a reasonable one
    rows_in = list(detrend_rows or [])
    if not rows_in:
        # Fable S3: a detrend picked by hand has no Auto table: Auto's own BIC comparison, on the same data, decides
        # which choices are reasonable (none of its results is used otherwise)
        try:
            rows_in = (lsq_fit(t, flux, err, am, planet, after_flip=after_flip, flagged_mask=flagged_mask,
                               flip_step=flip_step, detrend_auto=True,
                               progress=detrend_progress).get("detrend_choice") or {}).get("rows") or []
        except Exception as exc:
            say(f"  robustness check: no BIC table for the detrend choices ({exc}); every choice is tried")
            rows_in = []
    dbic = {}
    for row in rows_in:
        if row.get("key") and row.get("dbic") is not None and math.isfinite(float(row["dbic"])):
            dbic[row["key"]] = float(row["dbic"])
    best = min(dbic.values()) if dbic else 0.0   # Fable S3: against the lowest BIC, not the choice used
    here = detrend_key((da, dt, dq))
    for key, label, flags in DETREND_CHOICES:
        if key == here:
            continue
        if flags[0] and not allow_airmass:
            if "the airmass choices (frames without airmass)" not in left_out:
                left_out.append("the airmass choices (frames without airmass)")
            continue
        if dbic and key in dbic and dbic[key] - best > ROBUST_DBIC_MAX:
            left_out.append(f"detrend {label} (ΔBIC {dbic[key] - best:+.0f})")
            continue
        jobs.append(("detrend", f"detrend {label}",
                     lambda f_=flags: fit(t, flux, err, am, after_flip, flagged_mask, detrend_airmass=f_[0],
                                          detrend_time=f_[1], detrend_curve=f_[2])))
    # the first and the last 10 % of the points
    order = np.argsort(t)
    n_cut = int(round(ROBUST_TRIM * t.size))
    if n_cut >= 1 and t.size - n_cut >= 10:
        for label, drop in (("first 10 % of the points trimmed", order[:n_cut]),
                            ("last 10 % of the points trimmed", order[-n_cut:])):
            keep = np.ones(t.size, bool)
            keep[drop] = False
            jobs.append(("trim", label, lambda k_=keep: fit(t[k_], flux[k_], err[k_], _cut(am, k_),
                                                            _cut(after_flip, k_), _cut(flagged_mask, k_))))
    # the flagged frames the other way
    fm = None if flagged_mask is None else np.asarray(flagged_mask, dtype=bool)
    if fm is not None and fm.any() and (~fm).sum() >= 10:
        k_ = ~fm
        jobs.append(("flagged", f"the {int(fm.sum())} flagged frames left out",
                     lambda: fit(t[k_], flux[k_], err[k_], _cut(am, k_), _cut(after_flip, k_), None)))
    elif fm is None and flagged and len(flagged.get("t", [])):
        n0, n1 = t.size, len(flagged["t"])
        tt = np.r_[t, np.asarray(flagged["t"], float)]
        ff = np.r_[flux, np.asarray(flagged["flux"], float)]
        ee = np.r_[err, np.asarray(flagged["err"], float)]
        aa = None if am is None else np.r_[am, np.asarray(flagged.get("airmass", np.full(n1, np.nan)), float)]
        af = None
        if after_flip is not None or flagged.get("after_flip") is not None:
            base_af = np.zeros(n0, bool) if after_flip is None else np.asarray(after_flip, bool)
            fl_af = np.zeros(n1, bool) if flagged.get("after_flip") is None else np.asarray(flagged["after_flip"],
                                                                                              bool)
            af = np.r_[base_af, fl_af]
        mask = np.r_[np.zeros(n0, bool), np.ones(n1, bool)]
        jobs.append(("flagged", f"the {n1} flagged frames put back", lambda: fit(tt, ff, ee, aa, af, mask)))
    # each comp left out (as leave_one_comp_out: the ensemble without it, on the same frames)
    cm = None if comp_mags is None else np.asarray(comp_mags, dtype=float)
    if cm is not None and cm.ndim == 2 and cm.shape[1] >= 2 and cm.shape[0] == t.size:
        names = [str(n_) for n_ in (comp_names or [])][:cm.shape[1]]
        names += [f"comp {k + 1}" for k in range(len(names), cm.shape[1])]
        ens_all = tied_ensemble(cm)
        for j in range(cm.shape[1]):
            def one(j_=j):
                with np.errstate(invalid="ignore", over="ignore"):
                    factor = 10 ** (0.4 * (tied_ensemble(np.delete(cm, j_, axis=1)) - ens_all))
                ok = np.isfinite(factor)
                return fit(t[ok], (flux * factor)[ok], (err * factor)[ok], _cut(am, ok), _cut(after_flip, ok),
                           _cut(flagged_mask, ok))
            jobs.append(("comp", f"{names[j]} left out", one))
    # the re-measured curves (aperture, sky ring)
    for group, label, factor in measured or []:
        fac = np.asarray(factor, dtype=float)
        if fac.size != t.size:
            continue
        ok = np.isfinite(fac) & (fac > 0)
        if ok.sum() < max(10, int(0.5 * t.size)):
            too_few.append(label)
            continue
        jobs.append((group, label, lambda f_=fac, k_=ok: fit(t[k_], (flux * f_)[k_], (err * f_)[k_], _cut(am, k_),
                                                             _cut(after_flip, k_), _cut(flagged_mask, k_))))

    def row_of(group, label, r_):
        return {"group": group, "label": label, "depth": float(r_["depth"][0]), "tc": float(r_["p"]["tc"]),
                "rms": float(r_.get("rms", float("nan"))), "n": int(len(r_["t"])),
                "step": r_.get("t_flip") is not None}   # 6c-2: this fit kept a flip step

    rows, skipped = [], []
    for i, (group, label, job) in enumerate(jobs):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Canceled")
        if progress is not None:
            progress(i + 1, len(jobs))
        try:
            rows.append(row_of(group, label, job()))
        except Exception as exc:
            skipped.append(label)
            say(f"  robustness check: {label}: no fit ({exc})")
    ref_row = row_of("ref", "as fitted", ref)
    if not rows:
        return None
    every = [ref_row] + rows
    dep = np.array([r_["depth"] for r_ in every])
    tcs = np.array([r_["tc"] for r_ in every])
    rms = np.array([r_["rms"] for r_ in every])
    spread = {"depth": 0.5 * float(np.ptp(dep)), "tc": 0.5 * float(np.ptp(tcs)),
              "rms_lo": float(np.nanmin(rms)) if np.isfinite(rms).any() else float("nan"),
              "rms_hi": float(np.nanmax(rms)) if np.isfinite(rms).any() else float("nan")}
    formal = {"depth": float((main_summary.get("depth") or (float("nan"), float("nan")))[1]),
              "tc": float((main_summary.get("tc") or (float("nan"), float("nan")))[1])}
    most = {"depth": max(rows, key=lambda r_: abs(r_["depth"] - ref_row["depth"])),
            "tc": max(rows, key=lambda r_: abs(r_["tc"] - ref_row["tc"]))}
    for k_ in ("depth", "tc"):   # Fable N4: say when the refit that moves it most also decided the step otherwise
        if bool(most[k_].get("step")) != bool(ref_row.get("step")) and "(its flip step differs)" not in \
                most[k_]["label"]:
            most[k_] = dict(most[k_], label=most[k_]["label"] + " (its flip step differs)")
    out = {"rows": rows, "ref": ref_row, "spread": spread, "formal": formal, "most": most, "left_out": left_out,
           "skipped": skipped + [f"{x} (measured on too few frames)" for x in too_few], "groups": [g for g, _w in ROBUST_GROUPS if any(r_["group"] == g for r_ in rows)]}
    flags = []
    for key, what in (("depth", "depth"), ("tc", "Tc")):
        sp, fe = spread[key], formal[key]
        if math.isfinite(fe) and fe > 0 and sp > fe:
            m = most[key]
            move = (f"{_pct(abs(m['depth'] - ref_row['depth']))}" if key == "depth"
                    else f"{abs(m['tc'] - ref_row['tc']) * 1440:.1f} min")
            flags.append(("warn", f"Robustness: the {what} moves more across reasonable reduction choices than its "
                                  f"formal error ({robust_pm(key, sp)} against {robust_pm(key, fe)}); the most is "
                                  f"{m['label']}, which moves it by {move}. Report the larger spread with the "
                                  "result, or find out which choice is right before reporting."))
    out["flags"] = flags
    out["note"] = robustness_text(out)
    out["flags"].append(("info", out["note"]))
    return out


def robust_pm(key: str, v: float, ascii_only: bool = False) -> str:
    pm = "+/-" if ascii_only else "±"
    if not math.isfinite(v):
        return f"{pm} n/a"
    return f"{pm} {v * 100:.3f} %" if key == "depth" else f"{pm} {v * 1440:.2f} min"


def robustness_text(rob: dict | None, ascii_only: bool = False) -> str:
    """2.4.0 (#35): one line for the page notes, the Log, the settings file and (ASCII) the report NOTES."""
    if not rob:
        return ""
    pm = "+/-" if ascii_only else "±"
    sp, fe = rob["spread"], rob["formal"]
    dep = [r_["depth"] for r_ in rob["rows"]] + [rob["ref"]["depth"]]
    names = {g: w for g, w in ROBUST_GROUPS}
    what = ", ".join(names[g] for g in rob.get("groups") or [])
    txt = (f"Robustness check ({len(rob['rows'])} least-squares refits: {what}): depth "
           f"{min(dep) * 100:.2f}-{max(dep) * 100:.2f} %, {robust_pm('depth', sp['depth'], ascii_only)} across "
           f"choices (formal {robust_pm('depth', fe['depth'], ascii_only)}); Tc "
           f"{robust_pm('tc', sp['tc'], ascii_only)} across choices (formal {robust_pm('tc', fe['tc'], ascii_only)})")
    if math.isfinite(sp.get("rms_lo", float("nan"))):
        txt += f"; rms {sp['rms_lo'] * 1000:.2f}-{sp['rms_hi'] * 1000:.2f} ppt"
    m = rob.get("most") or {}
    if m.get("depth") is not None:
        txt += (f". Moves the depth most: {m['depth']['label']}; Tc most: {m['tc']['label']}")
    if rob.get("left_out"):
        txt += ". Not tried (not reasonable choices here): " + "; ".join(rob["left_out"])
    if rob.get("skipped"):
        txt += ". No fit: " + "; ".join(rob["skipped"])
    # 6c-2: each refit decides its own flip step; say when some decide otherwise than the night as fitted
    ref_step = bool(rob["ref"].get("step"))
    other = [r_["label"] for r_ in rob["rows"] if bool(r_.get("step", ref_step)) != ref_step]
    if other:
        how = "keep no flip step, though the night as fitted keeps one" if ref_step else \
            "need a flip step of their own, though the night as fitted has none"
        txt += (f". Flip step: {len(other)} of {len(rob['rows'])} refits {how} "
                f"({'; '.join(other[:4])}{'; …' if len(other) > 4 and not ascii_only else ''}"
                f"{'; ...' if len(other) > 4 and ascii_only else ''})")
    txt += "."
    if rob.get("measure_note"):   # Fable N14: why the aperture and sky ring were not tried, in every file too
        tried = any(r_["group"] in ("aperture", "sky") for r_ in rob["rows"])
        txt += (f" ({rob['measure_note']}.)" if tried else
                f" Aperture and sky ring not tried: {rob['measure_note']}.")
    if ascii_only:
        txt = txt.replace("–", "-").replace("Δ", "d").replace("±", pm)
    return txt


def comp_systematic_text(loo: dict | None) -> str:
    """2.2.16 (item 6): one line for the fit file: the leave-one-comp-out spread. 2.3.5 (#88): a diagnostic (how
    much the comp choice moves the result), not an error bar; the label in the files says so."""
    if not loo:
        return ""
    single = " (2 comps: each refit has a single comp)" if loo.get("single") else ""
    flagged = [f["name"] for f in loo.get("fits", []) if f.get("flagged")]
    return (f"depth spread {loo['depth_spread'] * 100:.2f} % ({loo['depth_min'] * 100:.2f} to "
            f"{loo['depth_max'] * 100:.2f} %), Tc spread {loo['tc_spread_s']:.0f} s; leave-one-comp-out over "
            f"{loo['n_comps']} comps{single}; full ensemble {loo.get('ref', {}).get('depth', float('nan')) * 100:.2f} %"
            + (f" ({loo['ref']['n_points']} points)" if loo.get("ref", {}).get("n_points") else "")
            + ("; fewer points without " + ", ".join(loo["fewer"]) if loo.get("fewer") else "")
            + (f"; moved > {COMP_LOO_SIGMA:g} sigma by leaving out {', '.join(flagged)}" if flagged else "")
            + ("; moved > " + f"{COMP_LOO_SIGMA:g} sigma, no single comp to blame" if loo.get("unnamed") else ""))


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
    cached = f", from cache (fetched {planet['from_cache']})" if planet.get("from_cache") else ""   # 2.2.21 (#21)
    return base + (f" ({ref})" if ref else "") + cached


def predicted_tc(tc_obs: float, t0: float, period: float, t0_err=None, period_err=None) -> dict:
    """2.2.19 (#29, #18): the ephemeris's predicted mid-transit at the epoch nearest tc_obs, with its uncertainty
    propagated as sqrt(sigma_T0^2 + (n * sigma_P)^2). n is negative for data before T0; squaring keeps the period
    term adding to the uncertainty either way (EXOTIC's prior box subtracted it for n < 0). T0 and P are taken as
    independent (published ephemerides rarely give their covariance). err_known is False when T0 has no stated
    uncertainty (the error then counts only what is known)."""
    n = int(round((float(tc_obs) - float(t0)) / float(period)))
    pred = float(t0) + n * float(period)
    err = math.hypot(float(t0_err or 0.0), n * float(period_err or 0.0))
    return {"epoch": n, "tc": pred, "err": err, "err_known": t0_err is not None}


def oc_summary(result: dict) -> dict | None:
    """2.2.19 (#29): the fitted Tc against the ephemeris prediction for its epoch, in one place for the table, notes,
    fit text, report notes and CSV. sigma = the fit's Tc error and the predicted Tc's error in quadrature.
    None without an ephemeris."""
    r = result or {}
    if r.get("tc_pred") is None or r.get("tc_pred_err") is None:
        return None
    s = r.get("summary") or {}
    tc_fit = (r.get("best") or {}).get("tc", (s.get("tc") or (None,))[0])
    if tc_fit is None:
        return None
    fit_err = float(s["tc"][1]) if "tc" in s else float("nan")
    pred_err = float(r["tc_pred_err"])
    oc = (float(tc_fit) - float(r["tc_pred"])) * 1440.0
    sig = math.hypot(fit_err, pred_err) * 1440.0 if math.isfinite(fit_err) else float("nan")
    ep = r.get("epoch")
    return {"epoch": int(ep) if ep is not None else None, "pred": float(r["tc_pred"]), "pred_err": pred_err, "pred_err_min": pred_err * 1440,
            "tc": float(tc_fit), "fit_err_min": fit_err * 1440, "oc_min": oc, "sigma_min": sig,
            "nsigma": oc / sig if math.isfinite(sig) and sig > 0 else float("nan"),
            "source": r.get("eph_source") or "the published ephemeris", "err_known": r.get("eph_err_known") is not False}


def _pm_minutes(m: float) -> str:
    """2.2.19 (#29): '±11.10 min', or '±0.41 s' for an uncertainty under 0.1 min (a precise modern ephemeris)."""
    return f"±{m * 60:.2f} s" if m < 0.1 else f"±{m:.2f} min"


def oc_text(o: dict, ascii_only: bool = False) -> str:
    """2.2.19 (#29): "O − C +5.38 ± 11.15 min (+0.48σ; fit and ephemeris σ in quadrature) vs predicted Tc
    2461328.625780 ± 0.007706 (±11.10 min) at epoch 2362; ephemeris from <source>". ascii_only for report headers."""
    ns = f"{o['nsigma']:+.2f}σ" if math.isfinite(o["nsigma"]) else "σ n/a"
    text = (f"O − C {o['oc_min']:+.2f} ± {o['sigma_min']:.2f} min ({ns}; fit and ephemeris σ in quadrature) vs "
            f"predicted Tc {o['pred']:.6f} ± {o['pred_err']:.6f} BJD_TDB ({_pm_minutes(o['pred_err_min'])}) at epoch "
            f"{o['epoch']}; ephemeris from {o['source']}" + ("" if o["err_known"] else " (T0 with no stated uncertainty)"))
    if ascii_only:
        text = re.sub(r"(\d)σ", r"\1 sigma", text).replace("σ", "sigma").replace("−", "-").replace("±", "+/-")
        import photometry_core as core
        text = core.ascii_safe(text)
    return text


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
    names = [p for p in ("c0", "c_am", "c_t", "c_t2", "step") if p in prob.free]
    t_mid = prob.t_mid
    after = prob.after if prob.after is not None else 0.0

    def flat(x):
        p = dict(zip(names, x))
        base = poly_baseline(p, am, t - t_mid)
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
                       f"{step['gap_at_h']:+.1f} h fits as well as the transit (ΔBIC {step['dbic']:+.1f} in favor of "
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
    u1, u2 = limb_darkening_guess(p.get("teff"), p.get("ld_band"))   # 2.2.17 (item 1)
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
    sc = size_check(r)   # 2.4.0 (#70): inside the published range (or near it) is not "far from published"
    if sc is not None and sc["state"] != "outside":
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
    # 2.4.0 (#70): with published errors the size is judged by size_check (a caution beside "Detected"); this
    # factor-of-3 bound stays only as the sanity bound when no published value has an error.
    rng = published_range(r.get("planet"))
    if rng and rng["has_errors"]:
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
        base = poly_baseline(p, am, t - t_mid) + x[-1] * after
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


TRIM_AIRMASS_DEFAULT = 3.0   # 2.4.0 (#63): frames past this airmass are offered for trimming (settings file:
                             # "transit_trim_airmass")


def high_airmass_spans(t, airmass, limit: float = TRIM_AIRMASS_DEFAULT, tc=None, t14=None) -> dict | None:
    """2.4.0 (#63): the points past `limit` airmass, as trim spans, and what trimming them leaves.
    In very thick air a red star against bluer comps picks up colour-extinction curvature a linear airmass term
    cannot follow (WASP-43 b MOBs: 1.37 to 4.03 at 14° altitude, the after-transit baseline mostly past 3).
    Returns None when no point is past the limit, else {"n", "max_airmass", "limit", "where" ('at the start' /
    'at the end' / 'at both ends' / 'inside the night'), "spans": [(BJD from, to)] (open, ±inf, at an end; between
    points the edge is halfway to the next point, so a trim takes exactly those points), "left": {"before": (min,
    frames), "after": (min, frames)} of out-of-transit baseline after the trim (with tc and t14), "kept"}."""
    t = np.asarray(t, dtype=float)
    am = np.asarray(airmass, dtype=float) if airmass is not None else None
    if am is None or am.shape != t.shape or not np.isfinite(am).any():
        return None
    order = np.argsort(t)
    t, am = t[order], am[order]
    high = np.isfinite(am) & (am > limit)
    if not high.any():
        return None
    spans, i, n = [], 0, len(t)
    while i < n:
        if not high[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and high[j + 1]:
            j += 1
        lo = -math.inf if i == 0 else 0.5 * (t[i - 1] + t[i])
        hi = math.inf if j == n - 1 else 0.5 * (t[j] + t[j + 1])
        spans.append((float(lo), float(hi)))
        i = j + 1
    ends = [s_ for s_ in spans if math.isinf(s_[0]) or math.isinf(s_[1])]
    if len(ends) == len(spans):
        at_start = any(math.isinf(s_[0]) for s_ in spans)
        at_end = any(math.isinf(s_[1]) for s_ in spans)
        where = "at both ends" if at_start and at_end else ("at the start" if at_start else "at the end")
    else:
        where = "inside the night"
    kept = ~high
    out = {"n": int(high.sum()), "max_airmass": float(np.nanmax(am)), "limit": float(limit), "where": where,
           "spans": spans, "kept": int(kept.sum()), "left": None}
    if tc is not None and t14 and n > 1:
        cad = float(np.median(np.diff(t)))
        tk = t[kept]
        nb = int((tk < tc - t14 / 2).sum())
        na = int((tk > tc + t14 / 2).sum())
        out["left"] = {"before": (nb * cad * 1440.0, nb), "after": (na * cad * 1440.0, na)}
    return out


def high_airmass_text(h: dict) -> str:
    """The note beside the offer: '12 frames past airmass 3 (up to 4.03, at the end): trimming them leaves ...'."""
    if not h:
        return ""
    text = (f"{h['n']} frame{'s' if h['n'] != 1 else ''} past airmass {h['limit']:g} (up to {h['max_airmass']:.2f}, "
            f"{h['where']}): in air that thick a red star and bluer comps dim differently, a curve the airmass term "
            "cannot follow.")
    if h.get("left"):
        b, a_ = h["left"]["before"], h["left"]["after"]
        text += (f" Trimming them leaves {b[0]:.0f} min ({_frames(b[1])}) of out-of-transit baseline before the "
                 f"transit and {a_[0]:.0f} min ({_frames(a_[1])}) after.")
    return text


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
        flags.append(("info", f"Grazing geometry (b {best['b']:.2f} > 1 − Rp/R* {1 - k:.2f}): the planet's disk "
                              "crosses the star's edge, so size and impact parameter trade off and Rp/R* depends "
                              "heavily on the b prior."))
    chd = r.get("detrend_choice") or {}
    if chd.get("dropped_no_airmass"):   # re-review R1: on the page too, not only in the Log
        flags.append(("info", f"Auto detrend left out {_frames(chd['dropped_no_airmass'])} without airmass, so every "
                              "detrend choice was fitted on the same frames."))
    cnote = curve_depth_note(r)   # 2.4.0 (John 19 Nov): Auto's time curve and the depth without it
    if cnote:
        flags.append(("info", cnote))
    pb = r.get("prayer_bead") or {}
    if pb.get("raised"):
        # 2.4.0 (#62): which errors are the prayer bead's
        words = PARAM_WORDS
        flags.append(("info", "Prayer-bead check (" + str(pb["n"]) + " shifted refits): its error is larger than "
                              "the MCMC's for " + ", ".join(words.get(n, n) for n in pb["raised"])
                              + ", so that error is the one shown (correlated noise the MCMC errors miss)."))
    ff = r.get("free_fit")
    if ff and ff.get("unconstrained"):
        # 2.2.19 (nit): the free shape ran away; its numbers are not shown.
        # 2.4.0 (#62): ... and the prior-held fit is far too big too: the transit model is soaking up structure
        # (WASP-43 b MOBs: free depth 34.8 %, prior-held Rp/R* 0.268 vs published 0.159, T14 and Tc pulled with it)
        pk = (r.get("planet") or {}).get("k")
        big = (isinstance(pk, (int, float)) and pk > 0 and "k" in s and s["k"][0] > 1.3 * pk
               and s["k"][0] - pk > 3 * max(s["k"][1], 1e-6))
        if big:
            t14p = (r.get("planet") or {}).get("t14")
            flags.append(("warn", f"The free shape ran away AND the fit with the published shape has Rp/R* "
                                  f"{s['k'][0]:.3f}, {s['k'][0] / pk:.1f}× the published {pk:.3f}"
                                  + (f" (T14 {s['t14'][0] * 24:.2f} h vs {t14p * 24:.2f} h)"
                                     if t14p and "t14" in s else "")
                                  + ": the transit model is absorbing structure in the light curve (red noise, a "
                                  "trend, a bad comp, clouds), and Tc is pulled with it. Treat the depth and Tc as "
                                  "suspect."))
        else:
            flags.append(("info", f"With the shape left free the fit is not constrained ({ff['unconstrained']}): "
                                  "this light curve alone cannot fix a/R* and b, so the Free shape column is left "
                                  "empty. The reported values use the published a/R* and b."))
    elif ff:
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
                              f"gap fits worse than the transit (ΔBIC {-step['dbic']:.1f} in favor of the transit)."))
    # 2.2.17 (item 8): what a reduced chi-square far from 1 means. Below 1 the error bars are larger than the scatter:
    # the error model is off (camera gain, values that are not camera units); bad darks, bias or flats would ADD
    # scatter, not shrink it. Above 1 there is noise the error model leaves out.
    if r.get("rchi2") is not None and r["rchi2"] < RCHI2_LOW:
        ratio = 1.0 / math.sqrt(max(r["rchi2"], 1e-6))
        flags.append(("warn", f"The per-point error bars are about {ratio:.1f}x larger than the real scatter (reduced "
                              f"χ² {r['rchi2']:.2f}). That is the error model, not the data: most often the camera "
                              "gain (EGAIN missing or wrong in the headers; the photometry log's Photon noise line says what "
                              "was used), or frames "
                              "calibrated elsewhere whose values are not camera units. Bad darks, bias or flats would "
                              "make the scatter larger, not the error bars. The fit keeps the larger error bars, so "
                              "its uncertainties are on the safe side (too wide)."))
    elif r.get("rchi2") is not None and r["rchi2"] > RCHI2_HIGH:
        ratio = math.sqrt(r["rchi2"])
        flags.append(("info", f"The scatter is about {ratio:.1f}x the per-point error bars (reduced χ² "
                              f"{r['rchi2']:.2f}): noise the error model leaves out, such as thin cloud or dew, "
                              "tracking or seeing changes (red noise), calibration, or a noisy comp; or a camera gain "
                              "(EGAIN) too large for the stored values (12/14-bit data scaled to 16 bits; see the "
                              "photometry log's Photon noise line). The fit scales its error bars up to match, so its "
                              "uncertainties include it."))
    if r.get("tc_pred") is not None and r.get("tc_pred_err") is not None:
        o = oc_summary(r)   # 2.2.19 (#29): predicted Tc with its propagated error, O − C in min and in sigma
        oc, sig = o["oc_min"], o["sigma_min"]
        level = "warn" if (math.isfinite(sig) and sig > 0 and abs(oc) > 3 * sig) else "info"
        flags.append((level, oc_text(o) + "."))
        # 2.2.12: say when the ephemeris itself is the weak part.
        eph_min = r["tc_pred_err"] * 1440
        fit_min = (s["tc"][1] * 1440) if "tc" in s else float("nan")
        if r.get("eph_err_known") is False:
            flags.append(("info", "The ephemeris's mid-transit time has no stated uncertainty, so the ± above "
                                  "leaves it out; treat the O − C with care."))
        elif eph_min > 10.0 and (not math.isfinite(fit_min) or eph_min > 2.0 * fit_min):
            flags.append(("info", f"The predicted mid-transit is uncertain by ±{eph_min:.0f} min (ephemeris "
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
    sc = (r.get("verdict") or {}).get("size_check")
    if sc is None:
        try:
            sc = size_check(r)
        except Exception:
            sc = None
    near_range = sc is not None and sc.get("state") in ("inside", "near")   # Fable S7: the range says it better
    if pub and "depth" in s and not (flip and not flip.get("depth_ok", True)) and not near_range:
        dd = s["depth"][0] - pub
        if abs(dd) > 3 * max(s["depth"][1], 1e-5):
            why = ("a neighbor's light in the aperture dilutes a transit (makes it shallower)" if dd < 0 else
                   "a deeper dip than published usually comes from the light curve itself (a jump, a trend, a comp "
                   "or calibration problem, or too much sky: a neighbor's light in the sky ring, or uncorrected sky with "
                   "no flats, can make the sky estimate too bright and the dip deeper); a neighbor's light in the aperture "
                   "cannot make a transit deeper")
            ldc = ld_center_depth(r["planet"]) if pub_label != "depth" else None
            ld_text = (f" (the fit's depth is its (Rp/R*)², compared with the published one; the limb-darkened dip "
                       f"at mid-transit is deeper, about {ldc * 100:.2f}% for the published shape)" if ldc else "")
            flags.append(("info", f"Depth {s['depth'][0] * 100:.2f}% vs published {pub_label} {pub * 100:.2f}% "
                                  f"({dd * 100:+.2f}%){ld_text}. A clear-filter depth can differ a little from "
                                  f"TESS's red band; a large difference: {why}."))
    # 2.2.13: the meridian flip step, and flagged frames put back in.
    ft = r.get("flip_test")
    if r.get("flip_gate_error"):
        # 2.3.0 review fix: the with/without-step test failed; the fit has no step term
        flags.append(("info", f"Meridian flip: the step test (with/without a step, ΔBIC) could not be done "
                              f"({r['flip_gate_error']}), so this fit has no step term. If the light curve shows a "
                              "jump at the flip, fit again or check the frames around it."))
    if ft and not ft.get("kept"):
        # 2.3.0 (#8): the step was tested (BIC) and left out: the fit has no step term.
        rel_h = (ft["t_flip"] - best["tc"]) * 24
        flags.append(("info", f"Meridian flip at BJD_TDB {ft['t_flip']:.5f} ({rel_h:+.2f} h from mid-transit): "
                              + flip_step_line(r) + f". The step is kept only at ΔBIC {FLIP_STEP_DBIC:g} or more, so "
                              "this fit has no step term."))
    if flip:
        rel_h = (flip["t_flip"] - best["tc"]) * 24
        st, se = flip["step"]
        ns = flip.get("nostep")
        text = (f"Meridian flip at BJD_TDB {flip['t_flip']:.5f} ({rel_h:+.2f} h from mid-transit; "
                f"{flip.get('source') or 'from the data'}): a baseline step there was fitted together with the "
                f"transit: step {st * 1000:+.1f} ± {se * 1000:.1f} ppt"   # 2.3.0 (#8): ppt, and the BIC gate
                + (f" (kept: ΔBIC {ft['dbic']:.0f}, at least {FLIP_STEP_DBIC:g} needed)." if ft else "."))
        if ns:
            text += (f" Without the step the depth would be {ns['depth'] * 100:.2f} ± {ns['depth_err'] * 100:.2f}% "
                     f"(least squares); ΔBIC {ns['dbic']:+.0f} "
                     + ("in favor of the step." if ns["dbic"] >= 0 else "against the step."))
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
    for bf in r.get("bound_flags") or []:
        flags.append((bf.get("level", "warn"), bf["text"]))   # 2.2.19 (#31); b against 0 is a note (review)
    wf = r.get("with_flagged")
    if wf:
        flags.append(("warn", f"This fit includes {wf['n']} frame{'s' if wf['n'] != 1 else ''} flagged {wf['label']}, "
                              f"with their error bars ×{wf['factor']:g}. Compare it with the fit without them (Fit "
                              "transit) before reporting it."))
    return flags


def excluded_baseline_text(excluded, t1, t4, pre_short, post_short, pre_min, post_min) -> str:
    """2.2.13: when a side's baseline is short and flagged frames left out of the fit lie there, say so:
    'No usable baseline after egress: the 31 frames there (00:44–01:20) were flagged cloud.' Times are the
    site's local time (from the frames' JD UTC; 2.2.21 (#22): labeled 'local time (UTC−5)'); minutes from the fitted contact when no JD is given."""
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
                import site_time   # 2.2.21 (#22): the site's zone, named
                span += " " + site_time.tag(float(jd[mask].min()))
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


def unescape_name(text: str) -> str:
    """2.3.2 (item 9): a name as sent by the Archive's services, HTML entities undone (twice: "&amp;auml;" too)."""
    import html

    return html.unescape(html.unescape(str(text or ""))).strip()


def _strip_tags(text: str) -> str:
    import re

    import html

    # 2.2.7: the Archive sends names double-escaped ("Kab&amp;aacute;th"): unescape twice.
    return html.unescape(html.unescape(re.sub(r"<[^>]+>", "", text or ""))).strip()


def _nea_query(adql: str, timeout: float = 45) -> list[dict]:
    import csv
    import io
    import urllib.parse

    url = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync?" + urllib.parse.urlencode({"query": adql, "format": "csv"})
    return list(csv.DictReader(io.StringIO(_http_get(url, timeout=timeout).decode("utf-8", "replace"))))


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
        # 2.3.2 (item 9): the alias service sends names HTML-escaped like the Archive ("Umb&auml;&auml;ssa")
        aliases = [unescape_name(a) for a in (raw if isinstance(raw, list) else []) if isinstance(a, str)]
        aliases = [a for a in dict.fromkeys(aliases) if a and a != resolved]
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
        eph_note = ephemeris_note(best["n"], best["sigma_min"])
    # 2.4.0 (#70): every published Rp/R* (the range a fit's depth is judged against) and the listed depths (shown)
    k_pub, depths_listed = published_rp_cached(pl_name)
    planet = {
        "name": pl_name, "host": row.get("hostname") or "",
        "k_published": k_pub, "depths_listed": depths_listed,
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


def published_rp_values(pl_name: str, timeout: float = 15) -> tuple:
    """2.4.0 (#70): every published solution's Rp/R* in the NASA Exoplanet Archive's 'ps' table, and every listed
    depth. Returns ([[k, k_err or None, reference], ...], [[depth (fraction), reference], ...]), each sorted. The
    listed depths (pl_trandep, ExoFOP's TESS-band dips among them) are a different quantity from (Rp/R*)² (limb-
    darkened, other bands, other analyses), so they are shown, never used to judge a fit."""
    # Fable N8: the spellings lookup_nea tries ('WASP-12b', 'wasp-12 b'), any capitalization, no quotes
    variants = _name_variants(str(pl_name or "").strip().replace("'", ""))
    where = " or ".join(f"lower(pl_name) = '{v.lower()}'" for v in variants)
    rows = _nea_query("select pl_ratror,pl_ratrorerr1,pl_ratrorerr2,pl_trandep,pl_refname from ps "
                      f"where {where}", timeout=timeout)
    ks, ds = [], []
    for row in rows:
        ref = unescape_name(_strip_tags(row.get("pl_refname", ""))).strip()
        k = _f(row.get("pl_ratror"))
        if k and 0 < k < 1:
            errs = [abs(v) for v in (_f(row.get("pl_ratrorerr1")), _f(row.get("pl_ratrorerr2"))) if v]
            ks.append([float(k), float(max(errs)) if errs else None, ref])
        d = _f(row.get("pl_trandep"))
        if d and 0 < d < 100:
            ds.append([float(d) / 100.0, ref])
    return sorted(ks, key=lambda x: x[0]), sorted(ds, key=lambda x: x[0])


def published_rp_cached(pl_name: str, timeout: float = 15, with_status: bool = False) -> tuple:
    """2.4.0 (#70): published_rp_values with the lookup cache (service 'nea_rp'): a live answer refreshes it, a
    failed request falls back to the last good answer. ([], []) when there is neither. with_status: a third value,
    True when the answer is real (live or cached), False when nothing could be had (Fable S4: not to be remembered
    as "no published values")."""
    import lookup_cache
    key = lookup_cache.norm_key(pl_name)
    if not key:
        return ([], [], False) if with_status else ([], [])
    try:
        ks, ds = published_rp_values(pl_name, timeout=timeout)
        lookup_cache.put("nea_rp", key, {"k": ks, "depths": ds})
        return (ks, ds, True) if with_status else (ks, ds)
    except Exception:
        cached, _when = lookup_cache.get("nea_rp", key)
        if isinstance(cached, dict):
            out = (list(cached.get("k") or []), list(cached.get("depths") or []))
            return out + (True,) if with_status else out
        return ([], [], False) if with_status else ([], [])


SIZE_SIGMA = 3.0   # 2.4.0 (#70): a depth this many sigma outside the published (Rp/R*)² range gets the size caution


def published_range(planet: dict | None) -> dict | None:
    """2.4.0 (#70): the published (Rp/R*)² values a fit's depth is judged against: every Archive solution's Rp/R*
    (planet["k_published"]), or the planet's one Rp/R* when there is no list. {"values": [(depth, error or None,
    reference)], "lo", "hi" (depths), "has_errors"} or None without any Rp/R*."""
    p = planet or {}
    vals = []
    for item in p.get("k_published") or []:
        try:
            k, ke, ref = float(item[0]), item[1], str(item[2] if len(item) > 2 else "")
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(k) and k > 0:
            vals.append((k * k, (2 * k * float(ke)) if isinstance(ke, (int, float)) and ke > 0 else None, ref))
    if not vals:
        k = p.get("k")
        if not (isinstance(k, (int, float)) and math.isfinite(k) and k > 0):
            return None
        ke = p.get("k_err")
        vals = [(k * k, (2 * k * ke) if isinstance(ke, (int, float)) and math.isfinite(ke) and ke > 0 else None,
                 str(p.get("reference") or p.get("source") or ""))]
    vals.sort(key=lambda v: v[0])
    return {"values": vals, "lo": vals[0][0], "hi": vals[-1][0], "has_errors": any(v[1] for v in vals)}


def range_text(rng: dict | None, pct: bool = True) -> str:
    """'1.47 %' or '1.43–1.72 % (5 values)' for one or several published (Rp/R*)²."""
    if not rng:
        return ""
    f = (lambda v: f"{v * 100:.2f}") if pct else (lambda v: f"{v:.5f}")
    unit = " %" if pct else ""
    distinct = len({round(v[0], 6) for v in rng["values"]})
    if distinct <= 1:
        return f(rng["lo"]) + unit
    return f"{f(rng['lo'])}–{f(rng['hi'])}{unit} ({len(rng['values'])} values)"


def size_check(r: dict) -> dict | None:
    """2.4.0 (#70): the fit's depth against the published (Rp/R*)² range. Inside the range, or within SIZE_SIGMA of
    its nearest value (the fit's error and that value's in quadrature): {"state": "inside" / "near"}. Further out:
    {"state": "outside", "sigma", "nearest", "text"} for the caution. None when the range has no error at all (the
    factor-of-3 bound in depth_plausibility is the only check then) or there is no depth."""
    rng = published_range(r.get("planet"))
    s = r.get("summary") or {}
    if not rng or "depth" not in s or not rng["has_errors"]:
        return None
    depth, err = s["depth"]
    out = {"range": rng, "range_text": range_text(rng)}
    if rng["lo"] <= depth <= rng["hi"]:
        return dict(out, state="inside", sigma=0.0)
    best = None
    for v, ve, ref in rng["values"]:
        sig = abs(depth - v) / max(math.hypot(err, ve or 0.0), 1e-9)
        if best is None or sig < best[0]:
            best = (sig, v, ve, ref)
    sig, v, ve, ref = best
    out.update(sigma=float(sig), nearest=(v, ve, ref))
    if sig <= SIZE_SIGMA:
        return dict(out, state="near")
    which = "deeper" if depth > v else "shallower"
    where = (f"the published (Rp/R*)² {v * 100:.2f} %" + (f" ({ref})" if ref else "")
             if len(rng["values"]) == 1 else f"the nearest published (Rp/R*)² {v * 100:.2f} %" + (f" ({ref})" if ref else "")
             + f"; range {range_text(rng)}")
    out.update(state="outside", text=(f"depth {depth * 100:.2f} ± {err * 100:.2f} % is {sig:.1f}σ {which} than "
                                      f"{where}: check the comps, dilution by a neighbour, and the detrend"))
    return out


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
    """The planet from the NASA Exoplanet Archive, else (a TOI) from ExoFOP.
    2.2.21 (#21): every Archive answer is kept in the lookup cache under the typed name; when the Archive does not
    answer (and ExoFOP has nothing live), the last good answer comes back with cache_note / from_cache set. A star with
    several planets (PlanetChoice) and an Archive that says "no such planet" are always live."""
    import lookup_cache
    errors = []
    key = lookup_cache.norm_key(name)
    no_answer = None
    try:
        planet = lookup_nea(name)
        lookup_cache.put("nea", key, _json_ready(planet))
        planet["looked_up"] = _lookup_stamp()   # 2.4.0 (#93): for the fit report's inputs (not cached)
        return planet
    except PlanetChoice:
        raise
    except Exception as exc:
        errors.append(f"NASA Exoplanet Archive: {exc}")
        if not isinstance(exc, ValueError):
            no_answer = exc   # not reached (timeout, no connection, 5xx), as opposed to "no planet called …"
    if "TOI" in name.upper():
        try:
            planet = lookup_exofop_toi(name)
            planet["looked_up"] = _lookup_stamp()   # 2.4.0 (#93)
            return planet
        except Exception as exc:
            errors.append(f"ExoFOP: {exc}")
    if no_answer is not None:
        cached, when = lookup_cache.get("nea", key)
        if isinstance(cached, dict) and cached.get("period"):
            planet = dict(cached)
            planet["from_cache"] = lookup_cache.fetched_text(when)
            planet["cache_note"] = lookup_cache.from_cache_note("nea", when, _no_answer_reason(no_answer))
            return planet
    raise ValueError("\n".join(errors))


def _lookup_stamp() -> str:
    """2.4.0 (#93): when a live lookup answered: '17 Nov 2026 14:03 UTC'."""
    import datetime as _dt
    import lookup_cache
    now = _dt.datetime.now(_dt.timezone.utc)
    return f"{lookup_cache.fetched_text(now)} {now:%H:%M} UTC"


def _json_ready(planet: dict) -> dict:
    """2.2.21 (#21): a planet dict with only JSON values (NaN/inf as None; sets as lists), for the lookup cache."""
    out = {}
    for k, v in planet.items():
        if isinstance(v, float) and not math.isfinite(v):
            v = None
        elif isinstance(v, (set, tuple)):
            v = list(v)
        elif hasattr(v, "item") and not isinstance(v, (list, dict, str)):
            try:
                v = v.item()
            except Exception:
                continue
        out[str(k)] = v
    return out


def _no_answer_reason(exc) -> str:
    """2.2.21 (#21): '503', 'timed out' or 'no connection' for the cache note."""
    try:
        import photometry_core
        return photometry_core.service_error_text(exc)
    except Exception:
        return ""


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


# ---- Approximate times (low-precision astronomy, good to a few seconds) -------------------------------
# 2.3.0 (#52): the transit planner and Tonight's transits are gone; these remain for the alt-az zenith-keyhole
# note (clearv_app) and the approximate BJD of a report that has no site.

# 2.2.18 (item 8): an alt-az mount cannot follow a star through the zenith smoothly: near it the azimuth speed (and
# the field rotation) grow without bound, so a star passing above this altitude crosses the "zenith keyhole".
ZENITH_KEYHOLE_ALT = 85.0


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


def bjd_minus_jd_days(jd, ra_deg, dec_deg) -> np.ndarray:
    """Approximate BJD_TDB − JD_UTC (days): Roemer delay from the Earth's position plus TDB − UTC.
    Good to a few seconds; used only where no site is known (a report's times; fits use Astropy's exact
    conversion). 2.3.0 (#52): the planner that also used it is gone."""
    sra, sdec, dist = sun_radec(jd)
    # Earth's heliocentric vector is minus the Sun's geocentric vector.
    ex = -dist * np.cos(np.radians(sdec)) * np.cos(np.radians(sra))
    ey = -dist * np.cos(np.radians(sdec)) * np.sin(np.radians(sra))
    ez = -dist * np.sin(np.radians(sdec))
    nx = math.cos(math.radians(dec_deg)) * math.cos(math.radians(ra_deg))
    ny = math.cos(math.radians(dec_deg)) * math.sin(math.radians(ra_deg))
    nz = math.sin(math.radians(dec_deg))
    return ((ex * nx + ey * ny + ez * nz) * 499.004784 + 69.184) / 86400.0


def jd_to_local(jd: float) -> datetime:
    """A JD (UTC) as a local, timezone-aware datetime in the site's time zone, with that night's daylight saving.
    2.2.21 (#22): the site's zone (site_time), no longer the computer's."""
    import site_time
    return site_time.jd_to_local(jd)


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
                header[key.strip().upper()] = value.strip()   # 2.2.17 review: "#Date_Type=" from other writers
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
    # 2.4.0 (#110): the airmass is the DETREND_n column at AIRMASS's place in #DETREND_PARAMETERS (one column per
    # name, in that order); DETREND_1 when the list only mentions airmass in other words, as before.
    detrends = header.get("DETREND_PARAMETERS", "").upper()
    names = [p.strip() for p in detrends.split(",")]
    am_col = f"DETREND_{names.index('AIRMASS') + 1}" if "AIRMASS" in names else "DETREND_1"
    if "AIRMASS" in detrends and col(am_col) is not None and np.isfinite(col(am_col)).any():
        out["airmass"] = col(am_col)
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


def detrend_columns(result: dict) -> list:
    """2.4.0 (#110): the AAVSO exoplanet report's detrend columns for a fit, in the order of result["detrend"]:
    [(NAME, values, format)], one per detrend. airmass → each point's airmass; time → each point's BJD_TDB (the
    time slope's own variable, written like DATE). AAVSO wants as many DETREND_n columns on every data line as
    #DETREND_PARAMETERS has names (0 to 4); a fit with no detrend lists none and writes none."""
    t = np.asarray(result.get("t"), dtype=float)
    # 2.4.0 (#103): the time curve's own variable, (BJD_TDB − the night's mean)² in days², named TIME_SQUARED; with
    # TIME and the baseline it spans the same curves as BJD² would, without 13-digit squares.
    values = {"airmass": (result.get("airmass"), "{:.7f}", "AIRMASS"), "time": (result.get("t"), "{:.8f}", "TIME"),
              "time2": ((t - float(np.mean(t))) ** 2 if t.size else None, "{:.9e}", "TIME_SQUARED")}
    out = []
    for name in result.get("detrend") or []:
        if name not in values:
            raise ValueError(f"the report has no data column for the detrend '{name}'")
        col, fmt, label = values[name]
        col = np.full(len(result["t"]), np.nan) if col is None else np.asarray(col, dtype=float)
        out.append((label, col, fmt))
    return out


def aavso_exoplanet_report(result: dict, meta: dict) -> str:
    """An AAVSO exoplanet report in the format EXOTIC writes (#TYPE=EXOPLANET), from a SHOBS-P fit.
    Check AAVSO's current exoplanet-section instructions before uploading.
    2.4.0 (#110): one DETREND_n column per name in #DETREND_PARAMETERS, in the same order (it always wrote one
    column, the airmass, whatever the list said: AAVSO rejected "AIRMASS, TIME" with "not enough fields; need 5",
    and "TIME" alone carried the airmass under the wrong name)."""
    detrend = detrend_columns(result)
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
        # 2.3.0 (#8): a kept flip step is not a data column, so it is said in #NOTES (flip_report_note), not here.
        # 2.4.0 (#110): no detrend = no names and no DETREND column (AAVSO: "0 to 4 names"; a word such as NONE
        # would count as one name and ask for a column).
        "#DETREND_PARAMETERS=" + ", ".join(name for name, _c, _f in detrend),
        "#MEASUREMENT_TYPE=Rnflux", f"#FILTER={meta.get('filter', 'CV')}",
        "#PRIORS=" + ",".join(priors), "#RESULTS=" + ",".join(results),
        _created(),
        "#DATE,DIFF,ERR" + "".join(f",DETREND_{i + 1}" for i in range(len(detrend))),
    ]
    for i, (t, f, e) in enumerate(zip(result["t"], result["flux"], result["err_scaled"])):
        extra = [fmt.format(col[i]) if math.isfinite(col[i]) else "na" for _name, col, fmt in detrend]
        lines.append(",".join([f"{t:.8f}", f"{f:.7f}", f"{e:.7f}"] + extra))
    return "\n".join(lines) + "\n"


def _is_sloan_code(filt) -> bool:
    import photometry_core as core
    return core.is_sloan(filt)


def _mentions_sloan(filt) -> bool:
    """2.2.17 (review S7): a filter string with a Sloan code in it (SU, or "SG/SI" from a mixed series) that the
    Sloan limb-darkening table did not cover."""
    import photometry_core as core
    return any(core.is_sloan(p) or core.sloan_band_code(p) for p in str(filt or "").split("/"))


def core_band_label(band) -> str:
    """2.2.17 (item 1): "i′" for SI (photometry_core.band_label)."""
    import photometry_core as core
    return core.band_label(band)


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
        cam = str(meta.get("camera_binning") or "").split(" ")[0]   # 2.2.19 (#27): MicroObservatory 2x2 too
        if cam and cam != "1x1":
            how.append(f"camera binning {cam}")
        elif not cam and meta.get("camera_bin", 1) and meta.get("camera_bin", 1) > 1:
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
            + (f"Sloan {core_band_label(str(meta.get('filter')).strip().upper())}, "
               if _is_sloan_code(meta.get("filter")) else "")   # 2.2.17 (item 1): "SI (Sloan i′, …)"
            + ("monochrome camera" if mono else "one-shot color camera") + ", untransformed)\n"
            f"  Pixel scale: {scale_text}\n"
            # 2.2.19 (#27): the camera's binning (as #BINNING) and SHOBS-P's processing bin, apart.
            + (f"  Binning: camera {meta['camera_binning']}; {meta.get('processing_bin') or 'SHOBS-P processing bin not recorded'}\n"
               if meta.get("camera_binning") else "")
            + f"  Photometric aperture: {ap_text}\n"
            f"  Coverage: {cov}{span}\n"
            f"  Result: Tc {s['tc'][0]:.5f} ± {s['tc'][1]:.5f}, depth "
            f"{s['depth'][0] * 1e6:.0f} ± {s['depth'][1] * 1e6:.0f} ppm\n"
            # 2.2.16 (item 6): the comp-choice spread, beside the statistical errors above.
            + (f"  Comp-choice check (diagnostic): {comp_systematic_text(result['comp_loo'])}\n"
               if result.get("comp_loo") else ""))


def _full(v, unit: str = "") -> str:
    """2.4.0 (#93): a number at full precision, or 'not known'. Step 3 regression (18 Nov): 15 significant digits,
    not Python's repr, so a catalog value keeps every digit it was published with (a double holds ~15.9) while a
    derived one loses only the binary noise (k^2 = 0.022159299599999998 -> 0.0221592996)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "not known"
    if not math.isfinite(f):
        return "not known"
    return f"{f:.15g}" + unit


def _full_pm(value, err, unit: str = "") -> str:
    text = _full(value)
    if text == "not known":
        return text
    e = _full(err)
    return text + (f" +/- {e}{unit}" if e != "not known" else f"{unit} (no error given)")


def ephemeris_note(n: int, sigma_min: float) -> str:
    """The NASA lookup's '; most precise of N published ephemerides, ±3 min now' ('' for a single ephemeris). Step 3
    regression (18 Nov): '±0 min now' read oddly, so under a minute is said as such."""
    if n <= 1:
        return ""
    return (f"; most precise of {n} published ephemerides, "
            + (f"±{sigma_min:.0f} min now" if sigma_min >= 1 else "under 1 min now"))


def fit_inputs_lines(result: dict) -> list:
    """2.4.0 (#93, John 15 Nov): the exact inputs the fit used, at full precision, so a later rerun can be checked for
    identical inputs (HAT-P-32 b, 19 Oct: the fit moved 0.004 sigma between 2.3.5 and 2.3.6 and the report's 4-digit
    priors could not show which lookup value had changed). One 'Name: value' line each, ASCII. Report lines only:
    nothing here feeds the fit."""
    r = result or {}
    pl = r.get("planet") or {}
    out = [f"Fit input period: {_full_pm(r.get('period', pl.get('period')), pl.get('period_err'), ' d')}",
           f"Fit input t0: {_full_pm(pl.get('t0'), pl.get('t0_err'))} (BJD_TDB)"
           + ("" if pl.get("t0") else " (no ephemeris: no O - C)"),
           f"Fit input a/R*: {_full_pm(pl.get('a_rs'), pl.get('a_rs_err'))}"
           + (" (derived from the duration)" if pl.get("a_rs_derived") else ""),
           f"Fit input b: {_full_pm(pl.get('b'), pl.get('b_err'))}" + (" (assumed)" if pl.get("b_assumed") else "")]
    k = pl.get("k")
    depth = published_depth(pl)[0] if pl else None
    out.append(f"Fit input published Rp/R*: {_full_pm(k, pl.get('k_err'))}; depth {_full(depth)}"
               + (f" (listed {_full(pl.get('depth_listed'))})" if pl.get("depth_listed") else "")
               + (" (typed on the Input page)" if pl.get("depth_typed") else ""))
    t14 = pl.get("t14")
    out.append(f"Fit input T14: {_full(t14, ' d')}" + (f" ({t14 * 24.0:.6f} h)"
                                                      if isinstance(t14, (int, float)) and math.isfinite(t14) else ""))
    teff = pl.get("teff")
    band = r.get("ld_band") or ""
    out.append(f"Fit input Teff for limb darkening: "
               + (_full(teff, " K") if isinstance(teff, (int, float)) and math.isfinite(teff)
                  else "not known (5800 K assumed)")
               + "; band " + (f"Sloan {core_band_label(band)} (Claret 2004)" if band else "V / clear table")
               + (f" (filter {pl.get('ld_band')})" if pl.get("ld_band") else ""))
    ld = r.get("ld_prior")
    if ld:
        out.append(f"Fit input limb darkening prior: u1 {_full(ld[0])}, u2 {_full(ld[1])}, width {_full(ld[2])}")
    pri = r.get("priors") or {}
    if pri:
        out.append("Fit input shape priors used: " + ", ".join(f"{name} {_full(m)} +/- {_full(sd)}"
                                                               for name, (m, sd) in pri.items())
                   + " (published values with the sigma floors)")
    pb = r.get("prayer_bead")   # 2.4.0 (#62)
    if pb:
        mc = pb.get("mcmc") or {}
        out.append(f"Fit errors: prayer bead {pb['n']} refits, Tc +/- {_full(pb['err'].get('tc'))} d (MCMC "
                   f"{_full(mc.get('tc'))}), depth +/- {_full(pb['depth_err'])} (MCMC {_full(mc.get('depth'))}); "
                   + ("the larger reported for " + ", ".join(pb.get("raised") or []) if pb.get("raised")
                      else "MCMC errors reported") + f"; red-noise beta {_full(r.get('beta'))}")
    ch = r.get("detrend_choice")   # 2.4.0 (#103): every choice's BIC, so the pick can be checked
    if curve_depth_note(r):
        out.append("Fit input detrend note: " + curve_depth_note(r, ascii_only=True))
    out.append("Fit input detrend: " + (detrend_choice_text(ch, ascii_only=True) if ch
                                        else detrend_text(r.get("detrend")) + " (chosen by hand)"))
    src = str(pl.get("source") or "").strip() or "not recorded"
    ref = str(pl.get("reference") or "").strip()
    eph = str(r.get("eph_source") or "").strip()
    cat = src + (f" ({ref})" if ref else "")
    # step 3 regression (18 Nov): the ephemeris source is often the same catalog and reference again; say it once
    ref_core = (ref.split("ephemeris:", 1)[-1] if ref.lower().startswith("ephemeris:") else ref).strip().lstrip("; ")
    if eph and eph != cat and not (ref_core and ref_core in eph and src in eph):
        cat += f"; ephemeris from {eph}"
    out.append(f"Fit input catalog: {cat}")
    if pl.get("from_cache"):
        when = f"from the lookup cache (fetched {pl['from_cache']}; the live service did not answer)"
    elif pl.get("looked_up"):
        when = f"{pl['looked_up']} (live)"
    elif "report" in src.lower():
        when = "not looked up (the report's own values)"
    elif "input page" in src.lower():
        when = "typed on the Input page"
    else:
        when = "not recorded"
    if pl.get("typed_over") and (pl.get("from_cache") or pl.get("looked_up")):
        # Fable nit: values typed over on the Input page after the lookup are not the lookup's
        when += "; typed over on the Input page after it: " + ", ".join(pl["typed_over"])
    out.append(f"Fit input lookup: {when}")
    return [_ascii_line(x) for x in out]


def _ascii_line(text: str) -> str:
    import photometry_core as core
    return core.ascii_safe(str(text).replace("\u2212", "-").replace("\u00b1", "+/-"))


def fit_text(result: dict, meta: dict) -> str:
    """Plain-text summary of a transit fit (ExoFOP package, and a copy for the observer's records)."""
    s, pl = result["summary"], result["planet"]
    ch = result["chain"]
    lines = [f"SHOBS-P {meta.get('version', '')} transit fit", f"Created        {_dtg()}", "",
             f"Planet         {pl.get('name', '')}   host {pl.get('host', '')}",
             f"Observer       {meta.get('obscode', '')}   site {meta.get('lat', '')}, {meta.get('lon', '')}",
             f"Night          {meta.get('obsdate', '')}   {result['n_points']} points, cadence "
             f"{result['cadence_days'] * 86400:.0f} s, filter {meta.get('filter', 'CV')}",
             # 2.2.19 (#27 review): the processing bin is in the ExoFOP block's Binning line, not repeated here.
             "Equipment      " + " | ".join(part for part in str(meta.get("notes", "")).split(" | ")
                                            if not part.startswith("SHOBS-P processing bin")),
             f"Photometry     aperture {meta.get('aperture', '')}; comps {meta.get('comps', '')}"
             + (f"; {meta['scint']}" if meta.get("scint") else ""),
             f"Ephemeris      P {result['period']:.8f} d, "
             + (f"from {result['eph_source']}" if result.get("eph_source") else
                "none available (no O − C)" if result.get("tc_pred") is None else
                f"published values from {pl.get('source', '')} {pl.get('reference', '')}".rstrip()),
             f"Shape priors   from {pl.get('source', '')}"]
    # 2.2.21 (#32, item 14): TESS TOI status (with its ExoFOP-TESS page) and ExoClock priority, when looked up.
    if meta.get("toi_status"):
        lines.append(f"TESS TOI       {meta['toi_status'].replace(' · ', ' ')}"   # the file is ASCII: no middle dot
                     + (f"  {meta['toi_url']}" if meta.get("toi_url") else ""))
    if meta.get("exoclock_status"):
        lines.append(f"ExoClock       {meta['exoclock_status'].replace('ExoClock: ', '')}")
    lines += ["", "Fitted values (MCMC median and 68% interval):"]
    for name in ("tc", "k", "depth", "a_rs", "b", "inc", "t14", "u1", "u2", "c_am", "c_t", "c_t2", "step"):
        if name in s:
            label = {"tc": "Tc (BJD_TDB)", "k": "Rp/R*", "depth": "Depth", "a_rs": "a/R*", "b": "b", "inc": "inc (deg)",
                     "t14": "T14 (days)", "u1": "u1", "u2": "u2", "c_am": "airmass slope", "c_t": "time slope",
                     "c_t2": "time curve", "step": "flip step"}[name]
            v, e = s[name]
            fmt = "{:.6f}" if name == "tc" else "{:.5f}"
            lines.append(f"  {label:<16}{fmt.format(v)} ± {fmt.format(e)}")
    o = oc_summary(result)
    if o is not None:
        # 2.2.19 (#29): the predicted Tc with its propagated error, and O − C in minutes and in sigma.
        lines.append(f"  Predicted Tc    {o['pred']:.6f} ± {o['pred_err']:.6f} ({_pm_minutes(o['pred_err_min'])}; epoch "
                     f"{o['epoch']}; ephemeris from {o['source']})")
        lines.append(f"  O − C           {o['oc_min']:+.2f} ± {o['sigma_min']:.2f} min ("
                     + (f"{o['nsigma']:+.2f}σ" if math.isfinite(o["nsigma"]) else "σ n/a")
                     + f"; fit ±{o['fit_err_min']:.2f} and ephemeris ±{o['pred_err_min']:.2f} min in quadrature)")
    lines += ["", "Priors           " + ", ".join(f"{k} {m:.4g} ± {sd:.2g}" for k, (m, sd) in result["priors"].items())
              + f"; limb darkening u1 {result['ld_prior'][0]:.2f}, u2 {result['ld_prior'][1]:.2f} ± {result['ld_prior'][2]:.2f}"
              + (f" (Claret 2004, Sloan {core_band_label(result['ld_band'])})" if result.get("ld_band") else
                 f" (V-band table used: no Sloan table for {meta.get('filter')})" if _mentions_sloan(meta.get("filter"))
                 else ""),
              # 2.4.0 (#93): the exact inputs at full precision follow this block ("Fit inputs (full precision)")
              f"Noise            rms {result['rms'] * 100:.3f}% per point"
              + (f" ({result['rms_bin10'] * 100:.3f}% per 10-min bin)" if result.get("rms_bin10") is not None else "")
              + f", reduced chi-square {result['rchi2']:.2f}, "
              f"red-noise beta {result['beta']:.2f}, error bars x{result['error_scale']:.2f}",
              "Detrending       " + ", ".join(w for w in ([tc_detrend_words(result)] + (["meridian flip step"]
                                                                  if result.get("flip") else [])) if w),   # 2.3.0 (#8)
              f"MCMC             {ch['walkers']} walkers, {ch['burn']} burn-in + {ch['steps']} steps, "
              f"acceptance {ch['acceptance']:.2f}"]
    ff = result.get("free_fit")
    if ff and ff.get("unconstrained"):
        lines.append(f"Free-shape check not constrained ({ff['unconstrained']})")   # 2.2.19 (nit)
    elif ff:
        lines.append(f"Free-shape check Rp/R* {ff['p']['k']:.4f}, a/R* {ff['p']['a_rs']:.2f}, b {ff['p']['b']:.3f}")
    if result.get("comp_loo"):
        # 2.2.16 (item 6)
        lines.append("Comp-choice check (diagnostic) " + comp_systematic_text(result["comp_loo"]))
    if result.get("robust"):
        lines.append("Robustness check " + robustness_text(result["robust"], ascii_only=True)
                     .replace("Robustness check ", "", 1))   # 2.4.0 step 6b (#35)
    lines += [f"Meridian flip    {ln}" if i == 0 else f"                 {ln}" for i, ln in enumerate(flip_lines(result))]
    wf = result.get("with_flagged")
    if wf:
        lines.append(f"Flagged frames   INCLUDED: {wf['n']} frames flagged {wf['label']}, error bars x{wf['factor']:g}")
    v = result.get("verdict") or {}
    if v.get("depth_unreliable"):
        lines.append("Verdict          timing usable, depth unreliable: " + v["depth_unreliable"])
    # 2.4.0 (#93): the fit's exact inputs, so a rerun can be checked for identical inputs.
    lines += ["", "Fit inputs (full precision)"] + ["  " + ln.replace("Fit input ", "", 1)
                                                    for ln in fit_inputs_lines(result)]
    if result["flags"]:
        lines += ["", "Notes"] + [("  ! " if lvl == "warn" else "  - ") + text for lvl, text in result["flags"]]
    return "\n".join(lines) + "\n"


def flip_step_line(result: dict, ascii_only: bool = False) -> str:
    """2.3.0 (#8): one line on the BIC-gated flip step: "Flip step fitted: +16.2 ± 1.1 ppt (ΔBIC 85), kept" or
    "Flip step not needed: +0.4 ± 0.9 ppt (ΔBIC −6; kept only at 10 or more)"; "" on a night without a flip (or a fit
    that was not gated)."""
    ft = (result or {}).get("flip_test")
    if not ft:
        flip = (result or {}).get("flip")
        if not flip:
            return ""
        st, se = flip["step"]
        text = f"Flip step fitted: {st * 1000:+.1f} ± {se * 1000:.1f} ppt (not gated)"
    else:
        st, se = (result.get("flip") or {}).get("step") or ft["step"]
        if ft.get("kept"):
            text = f"Flip step fitted: {st * 1000:+.1f} ± {se * 1000:.1f} ppt (ΔBIC {ft['dbic']:.0f}), kept"
        else:
            text = (f"Flip step not needed: {ft['step'][0] * 1000:+.1f} ± {ft['step'][1] * 1000:.1f} ppt "
                    f"(ΔBIC {ft['dbic']:.0f}; kept only at {FLIP_STEP_DBIC:g} or more)")
    if ascii_only:
        text = text.replace("±", "+/-").replace("Δ", "d").replace("−", "-")
    return text


def flip_lines(result: dict) -> list:
    """2.2.13: the flip step in plain lines (fit text, ExoFOP notes): [] without a flip. 2.3.0 (#8): a step the BIC
    gate left out gives its one line (time, size, ΔBIC)."""
    flip = (result or {}).get("flip")
    ft = (result or {}).get("flip_test")
    if not flip and ft:
        return [f"at BJD_TDB {ft['t_flip']:.5f}; " + flip_step_line(result, ascii_only=True)
                + "; fitted without a step"]
    if not flip:
        return []
    st, se = flip["step"]
    out = [f"at BJD_TDB {flip['t_flip']:.5f} ({flip.get('source') or 'from the data'}); baseline step fitted jointly "
           f"with the transit: {st * 100:+.2f} +/- {se * 100:.2f}%"]
    if ft:
        out.append(flip_step_line(result, ascii_only=True))   # 2.3.0 (#8)
    c = flip.get("corr")
    if c is not None and math.isfinite(c):
        out.append(f"step-depth correlation {c:+.2f} (MCMC)"
                   + ("; the step and the depth trade off; depth not measurable from this night"
                      if abs(c) > FLIP_CORR_WARN else ""))
    ns = flip.get("nostep")
    if ns:
        out.append(f"without the step: depth {ns['depth'] * 100:.2f} +/- {ns['depth_err'] * 100:.2f}% (least squares), "
                   f"dBIC {ns['dbic']:+.0f} " + ("in favor of the step" if ns["dbic"] >= 0 else "against the step"))
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
    ft = (result or {}).get("flip_test")
    if not flip and ft:
        # 2.3.0 (#8): the flip time is written either way, so a later fit (or another program) uses the same time.
        return (f"SHOBS-P: meridian flip at BJD_TDB {ft['t_flip']:.5f}; " + flip_step_line(result, ascii_only=True)
                + ", no step in this fit")
    if not flip:
        return ""
    st, se = flip["step"]
    text = f"SHOBS-P: meridian flip at BJD_TDB {flip['t_flip']:.5f}; baseline step {st * 100:+.2f}+/-{se * 100:.2f}%"
    if ft:
        text += f" (kept, dBIC {ft['dbic']:.0f})"   # 2.3.0 (#8)
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

