"""2.4.0 step 7b (#92): the promotion gates, Watch -> Candidate -> Campaign, headless and testable.

Design: Claude Doc "SHOBS-P: Suspect -> Watch -> Candidate -> Campaign" (20 Oct 2026, updated 15 Nov 2026 with
John's decisions 8-16). A star moves up only when its signal survives every test that could make the setup, not the
star, look variable.

The gate runner works on ONE star's light curve from a series (the target, or a Watch star) plus the other stars of
the same frames (check, comps, spare comps, other Watch stars) as null stars. Every periodogram runs after each
night's median is taken out, so a claim stays below about one night; longer periods need a separate trend track.

Two gate sets:
- Candidate (automatic, after each night merges): nights and span (>= 3 nights, >= 10 cycles, span >= 3 d or 3P,
  one consecutive-night pair; John, 15 Nov), cycles and sampling, single-star FAP < 1e-3, look-elsewhere FAP_eff
  < 1e-2, amplitude S/N >= 4 (red-noise beta applied; John, 20 Oct), not within 5% of 1, 1/2, 1/3 d.
- Campaign (the switch lights when these all pass on our data): the Candidate set plus data volume, single-night
  detections, +-1 c/d aliases, leave-one-night-out, phase coherence, flip-side offsets, FAP_eff < 1e-4, null tests,
  correlations (John's 15 Nov wording), and the systematics guards (linear limit, frame edges, check-star nights).

Outside survey data (ZTF, TESS, ATLAS) may clear the +-1 c/d alias and the time-span gates, never a null test or a
systematics guard (decision 10); clear_with_outside() records that.

Every gate is a dict {"key", "label", "group", "set", "value", "need", "pass", "cleared_by", "why"}; pass is True,
False or None (not measurable: too few nights, no data). report["candidate"] / report["campaign"] are True only when
every gate of that set passes (None counts as not passed).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

try:
    from astropy.timeseries import LombScargle
except Exception:   # pragma: no cover - astropy is a requirement
    LombScargle = None

GATES_VERSION = 1
SIDEREAL_DAY = 0.99726958

# ---- thresholds (the design doc's numbers; John's decisions 12 and 13) -------------------------------------------
CAND_MIN_NIGHTS = 3
CAND_MIN_CYCLES = 10.0          # cycles observed (time on the star / P), decision 12
CAND_MIN_SPAN_DAYS = 3.0        # span >= 3 d or 3P, whichever is longer
CAND_SPAN_PERIODS = 3.0
CAND_FAP = 1e-3                 # single-star Baluev FAP
CAND_FAP_EFF = 1e-2             # Bonferroni
CAND_AMP_SNR = 4.0              # Breger et al. 1993 (after the red-noise beta)
SAMPLING_FRAC = 0.25            # median frame spacing < P/4
ALIAS_1D_TOL = 0.05             # not within 5% of 1, 1/2, 1/3 d

CAMP_MIN_NIGHTS = 5
CAMP_MIN_SPAN_DAYS = 14.0
CAMP_MIN_CYCLES = 20.0          # for P < 0.5 d
CAMP_LONG_P = 0.5
CAMP_LONG_SPAN_PERIODS = 5.0    # P >= 0.5 d: span >= 5P and >= 5 cycles
CAMP_LONG_MIN_CYCLES = 5.0
CAMP_FAP_EFF = 1e-4
SINGLE_NIGHTS = 2               # nights that find the period alone
SINGLE_TOL = 0.10
SINGLE_SNR = 2.5
ALIAS_RATIO = 1.5               # best peak / strongest +-1, +-2 c/d alias
PHASE_TOL = 0.15                # cycles
PHASE_FRAC = 0.70
PHASE_SNR = 2.0
OFFSET_TOL = 0.10               # P and A agree within 10%, per night vs per flip side
LUNAR_PERIODS = (29.530589, 14.765294)
NULL_POWER_FRAC = 0.5
NULL_AMP_SIGMA = 2.0
NULL_AMP_FRAC = 1.0 / 3.0
CORR_R = 0.3                    # decision 13
CORR_POWER_FRAC = 0.25
CORR_FAP = 0.05
FLIP_GUARD_MIN = 5.0            # points within this of a pier/rotator change are left out of the flip test
EDGE_PAD_PX = 10.0
EDGE_FRAC = 0.95
CHECK_NIGHT_FACTOR = 1.5        # nights with the check's rms above this x its typical nightly rms are left out
RED_BIN_MINUTES = 30.0

QUIET_LEVEL = 2.0               # quiet-nights strip: chi2nu <~ 2 quiet, >~ 3 active (evidence only, decision 14)
ACTIVE_LEVEL = 3.0

MIN_NIGHT_POINTS = 8


@dataclass
class Curve:
    """One star's light curve (one row per frame, all arrays the same length). err: per-point errors (mag) when known
    (the target's merr), else empty. x, y: the star's pixel position per frame (NaN when not recorded). peak: its
    brightest raw pixel per frame (ADU; NaN when not recorded)."""
    name: str
    jd: np.ndarray
    mag: np.ndarray
    night: list
    segment: list
    err: np.ndarray = field(default_factory=lambda: np.array([]))
    airmass: np.ndarray = field(default_factory=lambda: np.array([]))
    fwhm: np.ndarray = field(default_factory=lambda: np.array([]))
    sky: np.ndarray = field(default_factory=lambda: np.array([]))
    x: np.ndarray = field(default_factory=lambda: np.array([]))
    y: np.ndarray = field(default_factory=lambda: np.array([]))
    peak: np.ndarray = field(default_factory=lambda: np.array([]))
    role: str = ""

    def take(self, keep: np.ndarray) -> "Curve":
        def cut(a):
            a = np.asarray(a)
            return a[keep] if a.size == keep.size else a
        return Curve(self.name, self.jd[keep], self.mag[keep], [n for n, k in zip(self.night, keep) if k],
                     [s for s, k in zip(self.segment, keep) if k], cut(self.err), cut(self.airmass), cut(self.fwhm),
                     cut(self.sky), cut(self.x), cut(self.y), cut(self.peak), self.role)


def make_curve(name, jd, mag, night, segment, **extra) -> Curve:
    jd = np.asarray(jd, float)
    mag = np.asarray(mag, float)
    out = {}
    for k, v in extra.items():
        if k == "role":
            out[k] = str(v or "")
        else:
            arr = np.asarray(v if v is not None else [], float)
            out[k] = arr if arr.size == jd.size else np.array([])
    c = Curve(str(name), jd, mag, [str(n) for n in night], [str(s or "") for s in segment], **out)
    keep = np.isfinite(c.jd) & np.isfinite(c.mag)
    return c.take(keep) if not keep.all() else c


# ---- small pieces ------------------------------------------------------------------------------------------------
def remove_medians(values, groups) -> np.ndarray:
    """Each group's (night's, or night and flip side's) median taken out."""
    v = np.asarray(values, float).copy()
    g = np.asarray([str(x) for x in groups])
    for key in set(g.tolist()):
        sel = g == key
        if sel.any():
            v[sel] -= np.nanmedian(v[sel])
    return v


def night_number(label: str):
    """The integer JD of a night label 'JD2461353' (None when it is not one)."""
    s = str(label or "")
    if s.upper().startswith("JD"):
        s = s[2:]
    try:
        return int(float(s))
    except ValueError:
        return None


def night_groups(c: Curve) -> dict:
    out = {}
    for i, n in enumerate(c.night):
        out.setdefault(n, []).append(i)
    return {n: np.asarray(ix) for n, ix in out.items()}


def observed_days(c: Curve) -> float:
    """Time on the star: the sum of each night's first-to-last span."""
    tot = 0.0
    for ix in night_groups(c).values():
        if ix.size > 1:
            tot += float(c.jd[ix].max() - c.jd[ix].min())
    return tot


def consecutive_pairs(nights) -> int:
    nums = sorted({n for n in (night_number(x) for x in nights) if n is not None})
    return sum(1 for a, b in zip(nums, nums[1:]) if b - a == 1)


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


def red_beta(t, resid, bin_minutes: float = RED_BIN_MINUTES) -> float:
    """Time-averaging beta (Pont et al. 2006; Winn et al. 2008) on each night's residuals: bins of 2 points up to
    bin_minutes, at least 6 bins in a night, the largest ratio of the binned scatter to its white-noise expectation
    (never below 1), pooled over nights by the median of the per-night betas weighted by their points."""
    t = np.asarray(t, float)
    r = np.asarray(resid, float)
    if t.size < 20:
        return 1.0
    order = np.argsort(t)
    t, r = t[order], r[order]
    cad = float(np.median(np.diff(t))) if t.size > 1 else 0.0
    if not (cad > 0):
        return 1.0
    max_bin = max(2, int(round(bin_minutes / 1440.0 / cad)))
    s1 = float(np.std(r))
    if s1 <= 0:
        return 1.0
    best = 1.0
    for nb in range(2, max_bin + 1):
        m = r.size // nb
        if m < 6:
            break
        means = r[: m * nb].reshape(m, nb).mean(axis=1)
        best = max(best, float(np.std(means)) / (s1 / math.sqrt(nb) * math.sqrt(m / (m - 1.0))))
    return float(best)


def night_beta(c: Curve, resid) -> float:
    """red_beta per night (gaps between nights are not white noise), combined as the points-weighted mean."""
    vals, wts = [], []
    for ix in night_groups(c).values():
        if ix.size >= 20:
            vals.append(red_beta(c.jd[ix], np.asarray(resid)[ix]))
            wts.append(ix.size)
    if not vals:
        return 1.0
    return float(max(1.0, np.average(vals, weights=wts)))


def fap_baluev(power: float, n_points: float, t, min_period: float, max_period: float) -> float:
    """Baluev (2008) FAP of a standard-normalized Lomb-Scargle peak, as photometry_core.fap_baluev."""
    try:
        z = float(power)
        n = float(n_points)
        t = np.asarray(t, float)
        if not (0.0 < z < 1.0) or n <= 4 or t.size < 2 or not (0 < min_period < max_period):
            return float("nan")
        nh, nk = n - 1.0, n - 3.0
        fmax = 1.0 / min_period
        w = fmax * math.sqrt(4.0 * math.pi * float(np.var(t)))
        gamma = math.sqrt(2.0 / nh) * math.exp(math.lgamma(nh / 2.0) - math.lgamma((nh - 1.0) / 2.0))
        log_single = 0.5 * nk * math.log1p(-z)
        log_tau = (math.log(gamma * w) + 0.5 * (nk - 1.0) * math.log1p(-z) + 0.5 * math.log(0.5 * nh * z))
        # FAP = 1 - (1 - single) exp(-tau) ~ single + tau for small values; kept in logs so 1e-83 survives
        if log_single < -30 and log_tau < -30:
            return float(math.exp(log_single) + math.exp(log_tau))
        single = math.exp(log_single)
        tau = math.exp(min(log_tau, 700.0))
        return float(min(max(1.0 - (1.0 - single) * math.exp(-tau), 0.0), 1.0))
    except (ValueError, OverflowError, ZeroDivisionError):
        return float("nan")


def bonferroni_fap(fap: float, m_trials: int) -> float:
    """Look-elsewhere: FAP_eff = min(1, M x FAP)."""
    if not (isinstance(fap, (int, float)) and math.isfinite(fap)):
        return float("nan")
    return float(min(1.0, max(1, int(m_trials or 1)) * fap))


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

    def at(self, f: float) -> float:
        return float(self.ls.power(np.atleast_1d(float(f)))[0]) if f > 0 else float("nan")

    def peak_near(self, f: float, half_width: float) -> float:
        """The highest power within +-half_width (c/d) of f (an alias peak sits a little off the exact f +- 1)."""
        if f - half_width <= 0:
            return float("nan")
        grid = np.linspace(f - half_width, f + half_width, 41)
        return float(np.max(self.ls.power(grid)))


def lookup_power(t, y, f: float) -> float:
    try:
        return float(LombScargle(np.asarray(t, float), np.asarray(y, float), normalization="standard").power(
            np.atleast_1d(float(f)))[0])
    except Exception:
        return float("nan")


def corr(a, b) -> float:
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 8 or np.std(a[ok]) == 0 or np.std(b[ok]) == 0:
        return float("nan")
    return float(np.corrcoef(a[ok], b[ok])[0, 1])


def alias_1d_text(period: float, tol: float = ALIAS_1D_TOL) -> str:
    for k, name in ((1, "1 d"), (2, "1/2 d"), (3, "1/3 d")):
        for base, kind in ((1.0, ""), (SIDEREAL_DAY, " sidereal")):
            ref = base / k
            if abs(period - ref) / ref < tol:
                return f"within {abs(period - ref) / ref * 100:.1f}% of {name}{kind}"
    return ""


def _gate(key, label, group, gset, value, need, ok, why="") -> dict:
    return {"key": key, "label": label, "group": group, "set": gset, "value": value, "need": need,
            "pass": (None if ok is None else bool(ok)), "cleared_by": "ours" if ok else None, "why": why}


def _fmt_p(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(x):
        return "—"
    if x == 0:
        return "0"
    return f"{x:.0e}" if x < 1e-3 else f"{x:.3f}"


# ---- the quiet-nights strip (evidence only) ------------------------------------------------------------------
def quiet_nights(c: Curve, bad_nights=()) -> list[dict]:
    """One chip per night: chi2nu of the night's points about the night's median, against the star's own errors when
    known (the target's), else against its point-to-point scatter (std of successive differences / sqrt 2, which a
    variation slower than the cadence does not raise). 'quiet' (<= 2), 'active' (>= 3), 'between', or 'skipped' on a
    night whose check star was bad. Evidence only (decision 14)."""
    out = []
    bad = set(bad_nights or ())
    err = np.asarray(c.err, float)
    for n, ix in sorted(night_groups(c).items(), key=lambda kv: float(np.min(c.jd[kv[1]]))):
        chip = {"night": n, "n": int(ix.size), "chi2nu": None, "basis": "", "state": "skipped" if n in bad else ""}
        if n in bad or ix.size < MIN_NIGHT_POINTS:
            chip["state"] = "skipped"
            out.append(chip)
            continue
        order = ix[np.argsort(c.jd[ix])]
        y = c.mag[order]
        d = y - np.median(y)
        if err.size == c.jd.size and np.all(np.isfinite(err[order])) and np.all(err[order] > 0):
            chi = float(np.sum((d / err[order]) ** 2) / max(1, d.size - 1))
            chip["basis"] = "own errors"
        else:
            p2p = float(np.std(np.diff(y)) / math.sqrt(2.0))
            chi = float(np.var(d, ddof=1) / p2p ** 2) if p2p > 0 else float("nan")
            chip["basis"] = "point-to-point scatter"
        chip["chi2nu"] = round(chi, 2) if math.isfinite(chi) else None
        if chip["chi2nu"] is None:
            chip["state"] = "skipped"
        elif chi <= QUIET_LEVEL:
            chip["state"] = "quiet"
        elif chi >= ACTIVE_LEVEL:
            chip["state"] = "active"
        else:
            chip["state"] = "between"
        out.append(chip)
    return out


def check_bad_nights(check: Curve | None, factor: float = CHECK_NIGHT_FACTOR) -> tuple[list, dict]:
    """Nights whose check-star rms (about the night's median) exceeds factor x the check's typical (median) nightly
    rms. Returns (bad nights, {night: rms})."""
    if check is None or check.jd.size == 0:
        return [], {}
    rms = {}
    for n, ix in night_groups(check).items():
        if ix.size >= MIN_NIGHT_POINTS:
            d = check.mag[ix] - np.median(check.mag[ix])
            rms[n] = float(np.std(d, ddof=1))
    if len(rms) < 2:
        return [], rms
    typical = float(np.median(list(rms.values())))
    bad = [n for n, r in rms.items() if typical > 0 and r > factor * typical]
    return sorted(bad), rms


# ---- the gate runner ------------------------------------------------------------------------------------------
def gate_report(target: Curve, nulls: list[Curve] | None = None, check: Curve | None = None, *,
                min_period: float = 0.02, max_period: float = 20.0, m_trials: int = 1,
                linear_limit: float | None = None, frame_shape=None, edge_margin_px: float | None = None,
                outside: dict | None = None) -> dict:
    """Run every promotion gate on one star. nulls: the other stars of the same frames (check, comps, spares, other
    Watch stars) as Curves; check: the check star (also used to drop bad nights). m_trials: watch stars in the series
    or stars searched in the field scan (look-elsewhere). linear_limit: raw ADU. frame_shape: (height, width) for the
    edge guard; edge_margin_px: aperture + sky ring outer radius (EDGE_PAD_PX is added). outside: {survey: {"period":
    d}} from the outside-data check; it may clear the +-1 c/d alias and the span gates (decision 10).

    Returns {"version", "star", "period_days", "power", "fap", "fap_eff", "amp", "sigma_amp", "beta", "amp_snr",
    "n_points", "nights", "dropped_nights", "gates": [...], "candidate": bool, "campaign": bool, "failed": [...],
    "quiet_nights": [...], "nulls": [...], "summary": str}."""
    nulls = list(nulls or [])
    rep = {"version": GATES_VERSION, "star": target.name, "gates": [], "candidate": False, "campaign": False,
           "failed": [], "dropped_nights": [], "nulls": [], "quiet_nights": []}
    bad, check_rms = check_bad_nights(check)
    rep["dropped_nights"] = bad
    rep["check_rms"] = {k: round(v, 5) for k, v in check_rms.items()}
    c = target.take(np.array([n not in bad for n in target.night], bool)) if bad else target
    rep["quiet_nights"] = quiet_nights(target, bad)
    gates = rep["gates"]
    nights = sorted(set(c.night), key=lambda n: float(np.min(c.jd[np.asarray(c.night) == n])))
    rep["nights"] = nights
    rep["n_points"] = int(c.jd.size)
    if c.jd.size < 3 * MIN_NIGHT_POINTS or len(nights) < 1 or LombScargle is None:
        rep["summary"] = "Too few points to judge a period yet."
        gates.append(_gate("nights", "Nights and span", "data", "candidate", f"{len(nights)} nights",
                           "≥ 3 nights", False, "too few points"))
        rep["failed"] = ["nights"]
        return rep
    y = remove_medians(c.mag, c.night)
    pmax = max_search_period(c, max_period)
    pmin = float(min(min_period, pmax / 2.0))
    pg = Periodogram(c.jd, y, pmin, pmax)
    P = pg.period
    f = pg.best_f
    rep.update({"period_days": P, "power": pg.best_power, "search": [pmin, pmax]})
    n_eff = c.jd.size - len(nights) + 1
    fap = fap_baluev(pg.best_power, n_eff, c.jd, pmin, pmax)
    fap_eff = bonferroni_fap(fap, m_trials)
    rep.update({"fap": fap, "fap_eff": fap_eff, "m_trials": int(max(1, m_trials or 1))})
    amp, phase0, resid, _ = sine_fit(c.jd, y, P)
    sig_pt = float(np.std(resid, ddof=3)) if resid.size > 3 else float("nan")
    sig_a = sig_pt * math.sqrt(2.0 / c.jd.size)
    beta = night_beta(c, resid)
    snr = amp / (sig_a * beta) if sig_a > 0 else float("nan")
    rep.update({"amp": amp, "sigma_point": sig_pt, "sigma_amp": sig_a, "beta": beta, "amp_snr": snr,
                "amp_snr_white": amp / sig_a if sig_a > 0 else float("nan")})
    span = float(c.jd.max() - c.jd.min())
    obs_days = observed_days(c)
    cycles = obs_days / P
    pairs = consecutive_pairs(nights)
    rep.update({"span_days": span, "cycles": cycles, "consecutive_pairs": pairs})

    # -- Watch -> Candidate --
    need_span = max(CAND_MIN_SPAN_DAYS, CAND_SPAN_PERIODS * P)
    ok = len(nights) >= CAND_MIN_NIGHTS and cycles >= CAND_MIN_CYCLES and span >= need_span and pairs >= 1
    why = []
    if len(nights) < CAND_MIN_NIGHTS:
        why.append(f"{len(nights)} nights")
    if cycles < CAND_MIN_CYCLES:
        why.append(f"{cycles:.1f} cycles")
    if span < need_span:
        why.append(f"span {span:.1f} d")
    if pairs < 1:
        why.append("no consecutive-night pair")
    gates.append(_gate("span", "Nights and span", "data", "candidate",
                       f"{len(nights)} nights, {cycles:.0f} cycles, {span:.1f} d, {pairs} consecutive pair(s)",
                       f"≥ 3 nights, ≥ 10 cycles, ≥ {need_span:.1f} d, a consecutive pair", ok, "; ".join(why)))
    diffs = [np.diff(np.sort(c.jd[ix])) for ix in night_groups(c).values() if ix.size > 1]
    spacing = float(np.median(np.concatenate(diffs))) if diffs else float("nan")
    ok = cycles >= 5 and math.isfinite(spacing) and spacing < SAMPLING_FRAC * P
    gates.append(_gate("sampling", "Cycles and sampling", "data", "candidate",
                       f"{cycles:.0f} cycles; spacing {spacing * 1440:.1f} min vs P/4 {P / 4 * 1440:.0f} min",
                       "≥ 5 cycles; spacing < P/4", ok))
    gates.append(_gate("fap", "Single-star FAP", "period", "candidate", _fmt_p(fap), "< 1e-3",
                       math.isfinite(fap) and fap < CAND_FAP))
    gates.append(_gate("fap_eff", "Look-elsewhere (FAP × M)", "period", "candidate",
                       f"{_fmt_p(fap_eff)} (M {rep['m_trials']})", "< 1e-2",
                       math.isfinite(fap_eff) and fap_eff < CAND_FAP_EFF))
    gates.append(_gate("amp_snr", "Amplitude S/N", "period", "candidate",
                       f"A {amp * 1000:.1f} mmag, A/σA {snr:.1f} (β {beta:.1f}; {rep['amp_snr_white']:.1f} white)",
                       "≥ 4 after β", math.isfinite(snr) and snr >= CAND_AMP_SNR))
    a1 = alias_1d_text(P)
    # Fable M1: the exemption needs a night that FINDS the period by itself over 1.25 cycles or more (set below,
    # once the single-night searches have run); a night merely as long as P is always there (the search is capped
    # at the longest night)
    gates.append(_gate("alias_1d", "Daily aliases (1, 1/2, 1/3 d)", "period", "candidate",
                       a1 or f"{P:.5f} d is clear", "not within 5%, unless one night finds a whole cycle itself",
                       not a1))

    # -- Candidate -> Campaign: data volume --
    if P < CAMP_LONG_P:
        need_span_c, need_cyc = CAMP_MIN_SPAN_DAYS, CAMP_MIN_CYCLES
    else:
        need_span_c, need_cyc = max(CAMP_MIN_SPAN_DAYS, CAMP_LONG_SPAN_PERIODS * P), CAMP_LONG_MIN_CYCLES
    ok = len(nights) >= CAMP_MIN_NIGHTS and span >= need_span_c and cycles >= need_cyc
    gates.append(_gate("volume", "Campaign data volume", "data", "campaign",
                       f"{len(nights)} nights, {span:.1f} d, {cycles:.0f} cycles",
                       f"≥ 5 nights, ≥ {need_span_c:.0f} d, ≥ {need_cyc:.0f} cycles", ok))
    gates.append(_gate("fap_eff_c", "Look-elsewhere for Campaign", "period", "campaign", _fmt_p(fap_eff), "< 1e-4",
                       math.isfinite(fap_eff) and fap_eff < CAMP_FAP_EFF))
    # single-night detections (P shorter than a night), else phase coherence stands in
    singles, single_rows = 0, []
    for n, ix in night_groups(c).items():
        if ix.size < MIN_NIGHT_POINTS * 2:
            continue
        t_n, y_n = c.jd[ix], y[ix]
        span_n = float(t_n.max() - t_n.min())
        if span_n < P:
            single_rows.append({"night": n, "found": None})
            continue
        try:
            pg_n = Periodogram(t_n, y_n, pmin, min(pmax, span_n))
            a_n, _, r_n, _ = sine_fit(t_n, y_n, P)
            s_n = float(np.std(r_n, ddof=3)) * math.sqrt(2.0 / t_n.size)
            near = abs(pg_n.period - P) / P <= SINGLE_TOL
            snr_n = a_n / s_n if s_n > 0 else 0.0
            hit = bool(near and snr_n >= SINGLE_SNR)
        except Exception:
            hit, snr_n, pg_n = False, 0.0, None
        singles += int(hit)
        single_rows.append({"night": n, "found": hit, "period": pg_n.period if pg_n else None,
                            "snr": round(snr_n, 1), "span": span_n})
    if a1:
        exempt = any(r.get("found") is True and r.get("span", 0.0) >= 1.25 * P for r in single_rows)
        g1 = next(g for g in gates if g["key"] == "alias_1d")
        g1["pass"], g1["cleared_by"] = bool(exempt), ("ours" if exempt else None)
        if exempt:
            g1["value"] = a1 + "; one night finds it alone"
    long_p = all(r["found"] is None for r in single_rows) if single_rows else True
    if long_p:
        gates.append(_gate("single", "Single-night detections", "period", "campaign", "P longer than a night",
                           "phase coherence stands in", True))
    else:
        gates.append(_gate("single", "Single-night detections", "period", "campaign",
                           f"{singles} of {sum(1 for r in single_rows if r['found'] is not None)} nights",
                           "≥ 2 nights find P alone (±10%, A/σA ≥ 2.5)", singles >= SINGLE_NIGHTS))
    rep["single_nights"] = single_rows
    # +-1 c/d aliases
    hw = max(0.02, 0.5 / max(span, 1e-3))
    aliases = {}
    for k in (-2, -1, 1, 2):
        fa = f + k
        if fa > 1.0 / pmax * 0.5 and fa < 1.0 / pmin * 1.5:
            aliases[k] = pg.peak_near(fa, hw)
    strongest = max([v for v in aliases.values() if math.isfinite(v)], default=float("nan"))
    ratio = pg.best_power / strongest if strongest and math.isfinite(strongest) and strongest > 0 else float("inf")
    k_best = max(aliases, key=lambda k: aliases[k] if math.isfinite(aliases[k]) else -1) if aliases else None
    alias_p = 1.0 / (f + k_best) if k_best is not None else float("nan")
    rep["alias"] = {"ratio": ratio, "strongest_power": strongest, "strongest_period": alias_p}
    gates.append(_gate("alias_pm1", "±1 c/d aliases", "period", "campaign",
                       (f"{pg.best_power:.3f} vs {strongest:.3f} at {alias_p:.5f} d = {ratio:.2f}×"
                        if math.isfinite(ratio) else "no alias peak in range"),
                       "best ≥ 1.5× strongest alias", ratio >= ALIAS_RATIO))
    if P > 5.0:
        lunar = any(abs(P - lp) / lp < 0.05 for lp in LUNAR_PERIODS)
        gates.append(_gate("lunar", "Lunar periods", "period", "campaign", f"{P:.2f} d", "not within 5% of 29.5 or 14.8 d",
                           not lunar))
    # leave-one-night-out
    carried = []
    for n in nights:
        keep = np.asarray(c.night) != n
        if keep.sum() < 3 * MIN_NIGHT_POINTS:
            continue
        try:
            pg_l = Periodogram(c.jd[keep], y[keep], pmin, pmax)
        except Exception:
            continue
        moved = abs(pg_l.period - P) / P > 0.05
        if moved and pg_l.at(f) < 0.5 * pg.best_power:
            carried.append(n)
    gates.append(_gate("loo", "Leave-one-night-out", "period", "campaign",
                       "no night carries it" if not carried else "carried by " + ", ".join(carried),
                       "no night carries the period", (None if len(nights) < 3 else not carried)))
    rep["loo_carried_by"] = carried
    # phase coherence
    phases = []
    for n, ix in night_groups(c).items():
        if ix.size < MIN_NIGHT_POINTS:
            continue
        a_n, ph_n, r_n, _ = sine_fit(c.jd[ix], y[ix], P)
        s_n = float(np.std(r_n, ddof=3)) * math.sqrt(2.0 / ix.size) if ix.size > 3 else float("nan")
        if s_n > 0 and a_n / s_n >= PHASE_SNR:
            d = (ph_n - phase0 + 0.5) % 1.0 - 0.5
            phases.append({"night": n, "dphase": round(float(d), 3)})
    agree = sum(1 for p in phases if abs(p["dphase"]) <= PHASE_TOL)
    frac = agree / len(phases) if phases else float("nan")
    gates.append(_gate("phase", "Phase coherence", "period", "campaign",
                       f"{agree} of {len(phases)} nights within ±{PHASE_TOL} cycle" if phases else "no night measurable",
                       "≥ 70% of nights with A/σA ≥ 2", (frac >= PHASE_FRAC) if phases else None))
    rep["phases"] = phases
    # flip sides: per night vs per (night, side), with points near a side change left out
    guard = np.ones(c.jd.size, bool)
    for n, ix in night_groups(c).items():
        order = ix[np.argsort(c.jd[ix])]
        seg = [c.segment[i] for i in order]
        for j in range(1, len(order)):
            if seg[j] != seg[j - 1]:
                tc = 0.5 * (c.jd[order[j]] + c.jd[order[j - 1]])
                guard &= np.abs(c.jd - tc) * 1440.0 > FLIP_GUARD_MIN
    has_flip = any(s == "flip" for s in c.segment)
    if has_flip and guard.sum() >= 3 * MIN_NIGHT_POINTS:
        cs = c.take(guard)
        ys = remove_medians(cs.mag, [f"{n}|{s}" for n, s in zip(cs.night, cs.segment)])
        try:
            pg_s = Periodogram(cs.jd, ys, pmin, pmax)
            a_s, *_ = sine_fit(cs.jd, ys, pg_s.period)
            dp = (pg_s.period - P) / P
            da = (a_s - amp) / amp if amp > 0 else float("nan")
            ok = abs(dp) <= OFFSET_TOL and abs(da) <= OFFSET_TOL
            gates.append(_gate("flip", "Flip sides (offsets per side)", "systematics", "campaign",
                               f"P {dp * 100:+.1f}%, A {da * 100:+.0f}%", "both within 10%", ok))
            rep["flip"] = {"period": pg_s.period, "amp": a_s}
        except Exception:
            gates.append(_gate("flip", "Flip sides (offsets per side)", "systematics", "campaign", "—", "both within 10%",
                               None))
    else:
        gates.append(_gate("flip", "Flip sides (offsets per side)", "systematics", "campaign", "no meridian flip",
                           "both within 10%", True))
    # null tests at P
    n_quiet, worst = 0, None
    for nc in nulls:
        if nc.jd.size < 3 * MIN_NIGHT_POINTS:
            continue
        keep = np.array([n not in bad for n in nc.night], bool)
        nc2 = nc.take(keep) if bad else nc
        if nc2.jd.size < 3 * MIN_NIGHT_POINTS:
            continue
        yn = remove_medians(nc2.mag, nc2.night)
        pw = lookup_power(nc2.jd, yn, f)
        an, _, rn, _ = sine_fit(nc2.jd, yn, P)
        san = float(np.std(rn, ddof=3)) * math.sqrt(2.0 / nc2.jd.size)
        share = pw / pg.best_power if pg.best_power > 0 else float("nan")
        lim_a = max(NULL_AMP_SIGMA * san, amp * NULL_AMP_FRAC)
        quiet = bool(share < NULL_POWER_FRAC and an < lim_a)
        n_quiet += int(quiet)
        row = {"name": nc.name, "role": nc.role, "power_share": round(share, 3), "amp": round(an, 5),
               "amp_limit": round(lim_a, 5), "quiet": quiet}
        rep["nulls"].append(row)
        if worst is None or share > worst["power_share"]:
            worst = row
    n_null = len(rep["nulls"])
    gates.append(_gate("nulls", "Null tests (other stars at P)", "systematics", "campaign",
                       (f"{n_quiet} of {n_null} quiet; largest power {worst['power_share'] * pg.best_power:.3f} vs "
                        f"{pg.best_power:.3f}" if n_null else "no other star measured"),
                       "power < ½ target's, amplitude small", (n_quiet == n_null) if n_null else None,
                       "" if n_quiet == n_null else "comp_signal"))
    # correlations (decision 13)
    corr_rows, corr_fail = [], []
    for key, arr in (("airmass", c.airmass), ("fwhm", c.fwhm), ("sky", c.sky), ("x", c.x), ("y", c.y)):
        arr = np.asarray(arr, float)
        if arr.size != c.jd.size or np.isfinite(arr).sum() < 3 * MIN_NIGHT_POINTS:
            continue
        ok_ = np.isfinite(arr)
        q = arr.copy()
        q[ok_] = remove_medians(arr[ok_], [n for n, k in zip(c.night, ok_) if k])
        r = corr(resid, q)
        pw = lookup_power(c.jd[ok_], q[ok_], f)
        fq = fap_baluev(pw, ok_.sum() - len(nights) + 1, c.jd[ok_], pmin, pmax) if math.isfinite(pw) else float("nan")
        share = pw / pg.best_power if pg.best_power > 0 else float("nan")
        fail = (math.isfinite(r) and abs(r) >= CORR_R) or (math.isfinite(share) and share > CORR_POWER_FRAC
                                                           and math.isfinite(fq) and fq < CORR_FAP)
        corr_rows.append({"quantity": key, "r": round(r, 3) if math.isfinite(r) else None,
                          "power_share": round(share, 3) if math.isfinite(share) else None, "fail": bool(fail)})
        if fail:
            corr_fail.append(key)
    rep["correlations"] = corr_rows
    if corr_rows:
        top = max(abs(r["r"] or 0) for r in corr_rows)
        gates.append(_gate("corr", "Airmass, FWHM, sky, x/y", "systematics", "campaign",
                           "fails: " + ", ".join(corr_fail) if corr_fail else f"all |r| < 0.3 (largest {top:.2f})",
                           "|r| < 0.3; no strong, significant power at P", not corr_fail,
                           "airmass_corr" if "airmass" in corr_fail else ("cloud_corr" if corr_fail else "")))
    else:
        gates.append(_gate("corr", "Airmass, FWHM, sky, x/y", "systematics", "campaign", "not recorded",
                           "|r| < 0.3", None))
    # systematics guards
    pk = np.asarray(c.peak, float)
    if linear_limit and pk.size == c.jd.size and np.isfinite(pk).any():
        mx = float(np.nanmax(pk))
        gates.append(_gate("linear", "Linear limit", "systematics", "campaign",
                           f"peak {mx:,.0f} ADU = {mx / linear_limit * 100:.0f}% of {linear_limit:,.0f}",
                           "every frame under the linear limit", mx < linear_limit, "" if mx < linear_limit else "saturation"))
        rep["peak_max"] = mx
    else:
        gates.append(_gate("linear", "Linear limit", "systematics", "campaign", "not recorded",
                           "every frame under the linear limit", None))
    xs, ys_ = np.asarray(c.x, float), np.asarray(c.y, float)
    if frame_shape and xs.size == c.jd.size and np.isfinite(xs).any():
        h, w = float(frame_shape[0]), float(frame_shape[1])
        mg = float(edge_margin_px or 0.0) + EDGE_PAD_PX
        okp = np.isfinite(xs) & np.isfinite(ys_)
        clear = okp & (xs >= mg) & (xs <= w - 1 - mg) & (ys_ >= mg) & (ys_ <= h - 1 - mg)
        share = clear.sum() / max(1, okp.sum())
        gates.append(_gate("edge", "Clear of frame edges", "systematics", "campaign", f"{share * 100:.0f}% of frames",
                           f"≥ 95% beyond {mg:.0f} px", share >= EDGE_FRAC, "" if share >= EDGE_FRAC else "edge"))
    else:
        gates.append(_gate("edge", "Clear of frame edges", "systematics", "campaign", "not recorded",
                           "≥ 95% of frames", None))
    if check is not None and check_rms:
        vals = list(check_rms.values())
        gates.append(_gate("check_nights", "Check-star nights", "systematics", "campaign",
                           (f"rms {min(vals) * 1000:.1f}–{max(vals) * 1000:.1f} mmag; "
                            + (f"left out {', '.join(bad)}" if bad else "all nights kept")),
                           f"rms ≤ {CHECK_NIGHT_FACTOR}× typical (worse nights are left out)", True))
    if outside:
        clear_with_outside(rep, outside)
    finish(rep)
    return rep


def clear_with_outside(rep: dict, outside: dict) -> None:
    """Decision 10: survey data that finds the same period may clear the +-1 c/d alias gate and the span/volume gates
    ("cleared by <survey>"); never a null test or a systematics guard. outside = {survey: {"period": days}}."""
    P = rep.get("period_days")
    if not P:
        return
    agree = [s for s, v in (outside or {}).items() if isinstance(v, dict) and v.get("period")
             and abs(float(v["period"]) - P) / P <= 0.01]
    if not agree:
        return
    for g in rep["gates"]:
        if g["key"] in ("alias_pm1", "span", "volume") and not g["pass"]:
            g["pass"] = True
            g["cleared_by"] = " and ".join(agree)
    finish(rep)


def finish(rep: dict) -> None:
    gates = rep["gates"]
    cand = [g for g in gates if g["set"] == "candidate"]
    rep["candidate"] = bool(cand) and all(g["pass"] is True for g in cand)
    rep["campaign"] = rep["candidate"] and all(g["pass"] is True for g in gates)
    rep["failed"] = [g["key"] for g in gates if g["pass"] is not True]
    rep["summary"] = summary_text(rep)


def summary_text(rep: dict) -> str:
    P = rep.get("period_days")
    if not P:
        return rep.get("summary") or "Too few points to judge a period yet."
    fails = [g["label"] for g in rep["gates"] if g["pass"] is False]
    unknown = [g["label"] for g in rep["gates"] if g["pass"] is None]
    head = f"P {P:.5f} d, A {rep['amp'] * 1000:.1f} mmag, A/σA {rep['amp_snr']:.1f}."
    if rep["campaign"]:
        return head + " Every gate passes: ready for a campaign."
    if rep["candidate"]:
        tail = " Candidate; short of a campaign on: " + ", ".join(fails + unknown) + "."
        return head + tail
    cfail = [g["label"] for g in rep["gates"] if g["set"] == "candidate" and g["pass"] is not True]
    return head + " Not a Candidate yet: " + ", ".join(cfail) + "."


def rejection_code(rep: dict) -> str:
    """A named reason for a hard failure on our own data ('' when none): comp_signal, flip, saturation, edge,
    airmass_corr, cloud_corr, alias_1d, single_night."""
    for key, code in (("nulls", "comp_signal"), ("flip", "flip"), ("linear", "saturation"), ("edge", "edge"),
                      ("corr", ""), ("alias_1d", "alias_1d"), ("loo", "single_night")):
        g = next((x for x in rep.get("gates", []) if x["key"] == key), None)
        if g and g["pass"] is False:
            return g.get("why") or code or "cloud_corr"
    return ""


# ---- curves from a SHOBS-P series ------------------------------------------------------------------------------
SKIP_FLAGS = {"cloud", "cloudedge", "haze", "drift", "noflux", "nocomp"}   # as App.watch_series


def _usable(o, need_comp_cat: bool = True) -> bool:
    import photometry_core as core
    if set(str(o.flag or "").split()) & (SKIP_FLAGS | {core.HAZE_FLAG}):
        return False
    vals = (o.jd, o.cmag, o.comp_cat) if need_comp_cat else (o.jd,)
    try:
        return all(math.isfinite(float(v)) for v in vals)
    except (TypeError, ValueError):
        return False


def _pos(o, name: str, kind: str):
    p = (o.pos or {}).get(kind)
    if kind in ("w", "s"):
        p = (p or {}).get(name) if isinstance(p, dict) else None
    try:
        return float(p[0]), float(p[1])
    except (TypeError, ValueError, IndexError):
        return float("nan"), float("nan")


def star_curve(observations, name: str, is_target: bool = False, target_name: str = "") -> Curve:
    """A Watch star's (or spare's) light curve as App.watch_series makes it (the comp ensemble's catalog mean plus
    the star minus the ensemble), or the target's own magnitudes, with the frame data the gates need."""
    rows = []
    for o in observations:
        if not _usable(o, need_comp_cat=not is_target):
            continue
        if is_target:
            m = float(o.mag)
            if not math.isfinite(m):
                continue
            xy = _pos(o, "", "t")
            pk = (o.raw_peaks or {}).get(target_name or name, float("nan"))
            err = float(o.merr) if math.isfinite(float(o.merr)) else float("nan")
        else:
            inst = (o.watch_insts or {}).get(name)
            kind = "w"
            if inst is None:
                inst = (o.spare_insts or {}).get(name)
                kind = "s"
            if inst is None or not math.isfinite(float(inst)):
                continue
            m = float(o.comp_cat) + float(inst) - float(o.cmag)
            xy = _pos(o, name, kind)
            pk = (o.raw_peaks or {}).get(name, float("nan"))
            err = float("nan")
        rows.append((o.jd, m, o.night, o.segment, err, o.airmass if o.airmass is not None else float("nan"),
                     o.fwhm, o.sky, xy[0], xy[1], pk))
    if not rows:
        return make_curve(name, [], [], [], [])
    cols = list(zip(*rows))
    return make_curve(name, cols[0], cols[1], cols[2], cols[3], err=cols[4], airmass=cols[5], fwhm=cols[6],
                      sky=cols[7], x=cols[8], y=cols[9], peak=cols[10], role="target" if is_target else "watch")


def null_curves(observations, comp_names, check_name: str = "", others=(), aliases=None) -> tuple[list, Curve | None]:
    """The null stars of a series: each comp in the series' layout (its instrumental magnitude minus the frame's
    ensemble), each spare comp and other Watch star, and the check. Returns (null curves, check curve or None)."""
    import photometry_core as core
    obs = [o for o in observations if _usable(o, need_comp_cat=False)]
    out = []
    for n in comp_names or []:
        v = [core.star_inst(o, n, list(comp_names), aliases) - o.cmag for o in obs]
        out.append(make_curve(n, [o.jd for o in obs], v, [o.night for o in obs], [o.segment for o in obs],
                              role="comp"))
    for n in others or []:
        v = [float((o.watch_insts or {}).get(n, (o.spare_insts or {}).get(n, float("nan")))) - o.cmag for o in obs]
        out.append(make_curve(n, [o.jd for o in obs], v, [o.night for o in obs], [o.segment for o in obs],
                              role="spare"))
    chk = None
    if check_name and any(o.kmag is not None and math.isfinite(float(o.kmag)) for o in obs):
        v = [(float(o.kmag) if o.kmag is not None else float("nan")) - o.cmag for o in obs]
        chk = make_curve(check_name, [o.jd for o in obs], v, [o.night for o in obs], [o.segment for o in obs],
                         role="check")
        out.append(chk)
    return [c for c in out if c.jd.size], chk


def series_gate_report(observations, star: str, *, is_target: bool, target_name: str, comp_names, check_name: str,
                       watch_names=(), spare_names=(), aliases=None, **kw) -> dict:
    """gate_report for one star of a series, with every other star of the series as its null stars."""
    tc = star_curve(observations, star, is_target=is_target, target_name=target_name)
    others = [n for n in list(watch_names) + list(spare_names) if n != star and n not in (comp_names or [])]
    nulls, chk = null_curves(observations, comp_names, check_name, others, aliases)
    if not is_target:
        # a Watch star's null stars include the series' target (a field star like any other, at the Watch star's P)
        tgt = star_curve(observations, target_name or "target", is_target=True, target_name=target_name)
        if tgt.jd.size:
            tgt.role = "target"
            nulls.append(tgt)
    return gate_report(tc, nulls, chk, **kw)


def frame_shape_from_header(header: dict, binning: int = 1):
    """(height, width) of the measured image: NAXIS2 x NAXIS1, over the app's software binning and the superpixel
    debayer of a color (Bayer) frame. None when the header has no size."""
    try:
        w, h = int(header.get("NAXIS1")), int(header.get("NAXIS2"))
    except (TypeError, ValueError):
        return None
    div = max(1, int(binning or 1))
    if str(header.get("BAYERPAT") or header.get("COLORTYP") or "").strip():
        div *= 2
    return (h // div, w // div)


def compact_report(rep: dict) -> dict:
    """The part of a gate report a series keeps (meta["candidates"]): numbers, gates, quiet nights, summary."""
    keep = ("version", "star", "period_days", "power", "fap", "fap_eff", "m_trials", "amp", "sigma_point",
            "sigma_amp", "beta", "amp_snr", "amp_snr_white", "n_points", "nights", "dropped_nights", "span_days",
            "cycles", "consecutive_pairs", "candidate", "campaign", "failed", "summary", "quiet_nights",
            "is_target", "registry", "registry_key")
    out = {}
    for k in keep:
        v = rep.get(k)
        if isinstance(v, float) and not math.isfinite(v):
            v = None
        if isinstance(v, (np.floating,)):
            v = float(v)
        out[k] = v
    out["gates"] = [{k: g.get(k) for k in ("key", "label", "group", "set", "value", "need", "pass", "cleared_by",
                                            "why")} for g in rep.get("gates") or []]
    return out
