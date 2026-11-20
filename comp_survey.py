"""2.4.0 step 5 (#76, #61): the pre-run comp survey. Pure functions (no Tk, no file reading) so the tests can drive
them with made-up numbers; clearv_app reads the frames and calls these.

The survey measures every comp candidate (catalog stars of the right brightness, not known variables, clear of the
frame edge all night) on a SAMPLE of the night's own frames, calibrated and lined up like the run itself:
about every 10th light, the first and last, the frames next to the sharpest ones read, and enough on each side of a
meridian flip. From those frames each candidate gets:
  - its scatter against the other candidates (an iterative ensemble, like Comp health),
  - on a Transits night with a meridian flip, the TARGET's step there against it (and, for the set, against the set's
    mean), with the predicted transit taken out (step 5 fix), measured like the transit fit measures it: every frame
    read on each side of the flip, weighted by the span of the night each stands for (step 6b, John 19 Nov),
  - its highest raw peak (the sharpest frames read), for the linear-limit gate,
  - whether it was measured on every sampled frame.
#61: the same frames are measured at a few apertures, and the one with the lowest scatter for stars like the target
is offered (the FWHM rule stays the starting point)."""
from __future__ import annotations

import itertools
import math
import warnings

import numpy as np

SURVEY_EVERY = 10          # about every 10th light
SURVEY_MIN = 8             # but at least this many (every light when there are fewer)
SURVEY_MAX = 24            # at most this many frames in the first pass (26 MP frames read at ~0.7 s each)
SURVEY_SHARP_NEIGHBORS = 2  # frames read either side of the sharpest two of the first pass
SURVEY_SIDE_MIN = 3        # frames wanted on each side of a meridian flip
LINEAR_MARGIN = 0.9        # a comp's highest raw peak must stay under 90 % of the linear limit
VARIABLE_FACTOR = 3.0      # a candidate scattering 3x the stars of its brightness tonight is left out
VARIABLE_MIN_PEERS = 5     # ... judged only when at least this many stars are of about its brightness
VARIABLE_FLOOR = 0.005     # ... and only above 5 mmag (below that it is ranked lower, not left out)
APERTURE_FACTORS = (1.0, 1.2, 1.5, 1.8, 2.2, 2.5)   # x FWHM, the #61 trial
APERTURE_GAIN = 0.10       # a trial aperture is offered only when its scatter is at least 10 % lower
SET_SIZE_COST = 0.0005     # mag: what one comp fewer than asked costs a set (a small nudge to more comps)
SET_TOP = 8                # the set is chosen from the 8 best-ranked survivors
FLIP_NEAR = 6              # the flip step from the 6 frames read nearest the flip on each side
FLIP_GAP_MAX = 12          # at most this many frames read from the gap where the flip is (both ends of it)
FLIP_NEAR_TARGET = 15     # the TARGET's step from up to 15 frames read on each side right at the flip (WASP-49 b
                           # 19 Nov, all 232 frames measured: 6 a side measured +-4 mmag and chose sets stepping
                           # +5 to +7 ppt over the night; 15 a side chose sets within 1 ppt of zero)
FLIP_REACH = FLIP_NEAR_TARGET + 4   # ... and only frames within this many frames of the flip count
FLIP_MIN_SIDE = 3          # ... and at least 3 of them on each side (Fable nit 5: 2 a side is too loose)
FLIP_PASS_MAX = 40         # at most this many extra reads to bracket the flip and read its neighbors
EPHEM_ERR_MISSING = 30.0 / 1440.0   # days: extra margin when the ephemeris gives no Tc uncertainty (typed values)
TARGET_STEP_MARGIN = 10.0 / 1440.0   # days: a near-flip frame this near (plus 3 sigma of Tc) to ingress or egress
                                     # makes the target's measured step depend on the ephemeris: not judged then
OWN_STEP_MAX = 0.010         # step 6c-2 (John 20 Nov): a comp whose own flip step for the target (target minus that
                             # comp alone) is beyond +-1 % (10 ppt) of flux is left out, so "steady on both pier
                             # sides" holds per comp, not only for the set. WASP-49 b 19 Nov, all 232 frames, 16
                             # candidates: own steps ran smoothly across the frame from -59 ppt (left edge) to +63
                             # (right edge); the 6c set took one from each end and they cancelled, so leaving either
                             # out moved the depth 1.3 % -> 3.2-3.4 % and Tc by ~55 min. Stars near the target's
                             # column step under 10 ppt.
OWN_STEP_SIGMA = 3.0         # ... and only when the step is also beyond 3 sigma of its own error (noise is not judged)
APERTURE_MIN_STARS = 3     # #61: judged on at least 3 stars like the target ...
APERTURE_MIN_FRAMES = 12   # ... on at least 12 frames, else a note only


def sample_indices(n: int, every: int = SURVEY_EVERY, cap: int = SURVEY_MAX) -> list[int]:
    """The first pass: about every `every`-th of n lights (at least SURVEY_MIN), always the first and the last, at most
    `cap` frames."""
    if n <= 0:
        return []
    # at least SURVEY_MIN frames (a scatter needs a few), every light when there are fewer
    k = min(n, cap, max(SURVEY_MIN, int(math.ceil(n / float(every))) + 1))
    return sorted({int(round(i)) for i in np.linspace(0, n - 1, k)})


def second_pass(n: int, read: list[int], fwhm: dict, flipped: dict) -> list[int]:
    """Frames to read next: the neighbors (+-1, +-2) of the two sharpest frames read (lowest FWHM), so the brightest
    peaks of the night are seen; and, on a night with a flip, frames from whichever side has fewer than
    SURVEY_SIDE_MIN (taken from between the last frame read before the flip and the first after it), and up to
    FLIP_GAP_MAX frames of the gap where the flip is, from both of its ends (flip_pass then brackets it and reads its
    neighbors). Never a frame already read."""
    have = set(read)
    out = []
    sharp = sorted((f, i) for i, f in fwhm.items() if f is not None and math.isfinite(f) and f > 0)[:2]
    for _f, i in sharp:
        for d in range(-SURVEY_SHARP_NEIGHBORS, SURVEY_SHARP_NEIGHBORS + 1):
            j = i + d
            if 0 <= j < n and j not in have:
                have.add(j)
                out.append(j)
    sides = {i: bool(v) for i, v in flipped.items() if v is not None}
    if sides and len(set(sides.values())) == 2:
        before = sorted(i for i, v in sides.items() if not v)
        after = sorted(i for i, v in sides.items() if v)
        # the flip lies between the last unflipped and the first flipped frame read (frames are in time order)
        lo, hi = max(before), min(after)
        for side, have_n, rng in ((False, len(before), range(lo - 1, -1, -1)), (True, len(after), range(hi + 1, n))):
            need = SURVEY_SIDE_MIN - have_n
            for j in rng:
                if need <= 0:
                    break
                if j not in have:
                    have.add(j)
                    out.append(j)
                    need -= 1
        # and the frames right at the flip, which say where it is: from both ends of the gap, alternately, with a cap
        # of their own (step 5 regression: from the low end only, sharing a cap of 12 with the sharp frames' neighbors,
        # WASP-49 b read frames 102-105 and missed 106-110 just before the flip at 111)
        gap = [j for j in range(lo + 1, hi) if j not in have]
        order = [gap[i] for pair in zip(range(len(gap)), range(len(gap) - 1, -1, -1)) for i in pair]
        took = 0
        for j in order:
            if took >= FLIP_GAP_MAX:
                break
            if j not in have:
                have.add(j)
                out.append(j)
                took += 1
    return sorted(out)


def flip_pass(n: int, tried, flipped: dict, near: int = FLIP_NEAR_TARGET) -> list[int]:
    """Fable re-review M1: the next frames to read so the flip step is measured on frames right at the flip, not on
    the sparse every-10th frames of one side. tried: every frame already read or tried (a frame that could not be
    lined up is not asked for again); flipped: {frame: orientation} for those read. While the frames read do not
    bracket the flip to adjacent frames, the middle frame of the bracket (bisection); then the `near` frames on each
    side of it that are not yet tried. [] when there is no flip or nothing is left to read. Called until []."""
    sides = {i: bool(v) for i, v in flipped.items() if v is not None}
    if not sides or len(set(sides.values())) < 2:
        return []
    first = sides[min(sides)]
    before = [i for i, v in sides.items() if v == first]
    after = [i for i, v in sides.items() if v != first]
    lo = max(i for i in before if i < min(after)) if any(i < min(after) for i in before) else None
    if lo is None:
        return []
    hi = min(after)
    have = set(tried)
    gap = [j for j in range(lo + 1, hi) if j not in have]
    if gap:
        return [gap[len(gap) // 2]]
    want = [j for j in range(max(0, hi - near), min(n, hi + near)) if j not in have]
    return want


def _robust_sigma(a) -> float:
    """The scatter from the 10th to the 90th percentile (a Gaussian's sigma), so one cosmic ray or satellite in a
    frame does not count, but a star that changes on a third of the frames does (a median absolute deviation
    reads a star that jumps on 40 % of the frames as steady). Below 12 frames the percentiles sit on the extreme
    points, so there the standard deviation without the one point farthest from the median is used (Fable nit 17;
    still no MAD, for the reason above)."""
    a = np.asarray(a, dtype=float)
    a = a[np.isfinite(a)]
    if a.size < 3:
        return float("nan")
    if a.size < 12:
        keep = np.delete(a, int(np.argmax(np.abs(a - np.median(a)))))
        return float(np.std(keep, ddof=1)) if keep.size >= 2 else float("nan")
    q10, q90 = np.percentile(a, [10, 90])
    return float((q90 - q10) / 2.5631)


def _sigma_cols(d) -> np.ndarray:
    return np.array([_robust_sigma(d[:, j]) for j in range(d.shape[1])])


def _others_mean(z, use):
    """Per frame and star: the mean of the OTHER stars in `use` (NaN-aware), vectorised: (sum - own) / (n - own)."""
    zu = np.where(use[None, :] & np.isfinite(z), z, 0.0)
    nu = (use[None, :] & np.isfinite(z)).astype(float)
    tot, cnt = zu.sum(axis=1, keepdims=True), nu.sum(axis=1, keepdims=True)
    with np.errstate(all="ignore"):
        return (tot - zu) / (cnt - nu)


def _others_median(z, use):
    """Per frame and star: the median of the other stars in `use`. Exact for a few stars; with many, the median of
    all of them (leaving one star out moves a median of 15 or more very little)."""
    f, s = z.shape
    cols = np.where(use)[0]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        if cols.size >= 15:
            med = np.nanmedian(z[:, cols], axis=1)
            return np.repeat(med[:, None], s, axis=1)
        out = np.full((f, s), np.nan)
        for j in range(s):
            o = cols[cols != j]
            if o.size:
                out[:, j] = np.nanmedian(z[:, o], axis=1)
        return out


def flip_sides(flipped):
    """The frames (in time order) after the flip as a bool array: a frame is 'after' when its orientation differs
    from the FIRST frame's (the flags are relative to the reference frame, which may itself be after the flip;
    Fable 12). None when every frame has the same orientation."""
    if flipped is None:
        return None
    fl = np.asarray([bool(v) for v in flipped])
    if fl.size == 0 or fl.all() or (~fl).all():
        return None
    return fl != fl[0]


def near_flip_rows(after, near_flip: int = FLIP_NEAR, idx=None, reach: int = FLIP_REACH):
    """The rows (frames in time order) nearest the flip: the last `near_flip` of the first side and the first
    `near_flip` of the second. With idx (each row's frame number), only rows within `reach` frames of the flip
    (Fable re-review M1: on the survey's every-10th frames the 'nearest' could be an hour away). (empty, empty)
    without a flip."""
    none = np.zeros(0, int), np.zeros(0, int)
    if after is None:
        return none
    after = np.asarray(after, dtype=bool)
    before_idx, after_idx = np.where(~after)[0], np.where(after)[0]
    if not before_idx.size or not after_idx.size:
        return none
    first_after = after_idx.min()
    b_rows = before_idx[before_idx < first_after][-near_flip:]
    a_rows = after_idx[after_idx > b_rows.max()][:near_flip] if b_rows.size else after_idx[:near_flip]
    if idx is not None and b_rows.size and a_rows.size:
        fr = np.asarray(idx)
        edge = fr[first_after]
        b_rows = b_rows[edge - fr[b_rows] <= reach]
        a_rows = a_rows[fr[a_rows] - edge < reach]
    return b_rows, a_rows


def whole_night_rows(after, idx, n_frames: int, usable=None):
    """2.4.0 step 6b (John 19 Nov, "match the fit"): the rows for the target's flip step measured the way the transit
    fit measures it: EVERY frame read on each side of the flip (the whole night's baseline), not only the frames
    nearest it. after: per row (frames in time order), True after the flip (flip_sides); idx: each row's frame
    number in the night (0 .. n_frames - 1); usable: rows that may be used (finite times, not near ingress or
    egress), all when None. The survey reads ~30 frames right at the flip and only every ~10th elsewhere, so each
    row is weighted by the span of the night's frames it stands for (half-way to its neighbors on its own side,
    the side's first and last rows out to the side's ends), as the fit weights every frame alike.
    Returns (b_rows, a_rows, weights: an array over ALL rows, 0 where not used); empty rows without a flip."""
    none = np.zeros(0, int), np.zeros(0, int), np.zeros(0)
    if after is None:
        return none
    after = np.asarray(after, dtype=bool)
    fr = np.asarray(idx, dtype=float) if idx is not None else np.arange(after.size, dtype=float)
    if fr.size != after.size or not after.any() or after.all():
        return none
    ok = np.ones(after.size, bool) if usable is None else np.asarray(usable, dtype=bool)
    first_after = int(np.where(after)[0].min())
    edge = 0.5 * (fr[first_after - 1] + fr[first_after]) if first_after > 0 else fr[first_after]
    w = np.zeros(after.size)
    out = []
    for side, lo, hi in ((False, -0.5, edge), (True, edge, float(n_frames) - 0.5)):
        # spans from EVERY row read on this side, so a row next to the frames left out (in transit) does not take
        # their span over; then only the usable rows keep theirs
        rows_all = np.where(after == side)[0]
        if rows_all.size:
            f = fr[rows_all]
            mids = 0.5 * (f[1:] + f[:-1])
            w[rows_all] = np.maximum(np.r_[mids, hi] - np.r_[lo, mids], 0.5)
        rows = rows_all[ok[rows_all]]
        out.append(rows)
    w[~ok] = 0.0
    return out[0], out[1], w


BASELINE_SIDE = 15        # step 6b: frames wanted outside the predicted transit on each side of a flip (Transits)
BASELINE_PASS_MAX = 30    # ... at most this many extra reads for them


def baseline_pass(n: int, tried, read_jd: dict, flipped: dict, is_baseline, want: int = BASELINE_SIDE) -> list[int]:
    """2.4.0 step 6b: on a Transits flip night, the next frames to read so the target's step is measured on enough
    of the night's BASELINE (outside the predicted transit) on each side of the flip: the every-10th frames give
    only ~5 there when the flip falls near mid-transit (WASP-49 b 19 Nov), and the frames read at the flip are all
    in transit. tried: frames read or tried; read_jd: {frame: JD} of the frames read (times of the others are
    interpolated, the lights being in time order); flipped: {frame: orientation}; is_baseline(jd array) -> bool
    array. Frames spread evenly over each side's baseline, up to `want` baseline frames a side in all. Step 6c: the
    app passes contact_free as is_baseline (every frame the free-depth step fit can use, in transit too, counts),
    so "baseline" here means "usable for the step"."""
    sides = {i: bool(v) for i, v in flipped.items() if v is not None}
    have_jd = sorted((i, float(j)) for i, j in read_jd.items() if j is not None and math.isfinite(float(j)))
    if len(set(sides.values())) < 2 or len(have_jd) < 2:
        return []
    first = sides[min(sides)]
    after_read = [i for i, v in sides.items() if v != first]
    before_read = [i for i, v in sides.items() if v == first and i < min(after_read)]
    if not before_read:
        return []
    cut = 0.5 * (max(before_read) + min(after_read))   # between the last frame read before and the first after
    xi, xj = np.array([p[0] for p in have_jd], float), np.array([p[1] for p in have_jd], float)
    all_i = np.arange(n)
    jd_all = np.interp(all_i, xi, xj)
    base = np.asarray(is_baseline(jd_all), dtype=bool)
    out = []
    for lo, hi in ((0, cut), (cut, n)):
        side_i = all_i[(all_i >= lo) & (all_i < hi) & base]
        if not side_i.size:
            continue
        have = sum(1 for i in read_jd if lo <= i < hi and base[i])
        need = want - have
        if need <= 0:
            continue
        free = [int(i) for i in side_i if int(i) not in tried]
        if not free:
            continue
        pick = np.unique(np.linspace(0, len(free) - 1, min(need, len(free))).round().astype(int))
        out += [free[k] for k in pick]
    return sorted(out)


def _wmedian(x, w) -> float:
    o = np.argsort(x)
    x, w = x[o], w[o]
    c = np.cumsum(w)
    return float(x[np.searchsorted(c, 0.5 * c[-1])])


def ensemble_stats(mags, flipped=None, iterations: int = 3, near_flip: int = FLIP_NEAR) -> dict:
    """mags: instrumental magnitudes, frames in TIME ORDER in rows, candidates in columns (NaN = not measured).
    flipped: per frame, its orientation against the reference frame (None or all one value: no flip). Each
    candidate against the mean of the others (each zero-pointed to its own median, so brightness does not matter),
    the ensemble rebuilt without stars that scatter more than 3x the median, `iterations` times. The flip step is
    taken from the `near_flip` frames on each side nearest the flip only, so a slow trend over the night does not
    read as a step (Fable 11), against the median of the others (one big-stepping star must not shift the rest).
    Returns {"scatter", "step" (after minus before, mag; NaN without a flip or with fewer than 2 frames a side),
    "n", "frames", "flip", "n_before", "n_after", "in_ensemble"}."""
    m = np.asarray(mags, dtype=float)
    if m.ndim != 2 or m.shape[1] == 0:
        return {"scatter": np.zeros(0), "step": np.zeros(0), "n": np.zeros(0, int), "frames": 0, "flip": False,
                "n_before": 0, "n_after": 0, "in_ensemble": np.zeros(0, bool)}
    f, s = m.shape
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        z = m - np.nanmedian(m, axis=0)
        after = flip_sides(flipped) if flipped is not None and len(flipped) == f else None
        z_sc = z.copy()
        if after is not None:
            # each side zero-pointed on its own for the scatter, so a flip step is not counted as scatter
            for side in (False, True):
                rows = after == side
                z_sc[rows] -= np.nanmedian(z[rows], axis=0)
        use = np.ones(s, dtype=bool)
        scatter = np.full(s, np.nan)
        for _ in range(max(1, iterations)):
            scatter = _sigma_cols(z_sc - _others_mean(z_sc, use))
            good = scatter[use & np.isfinite(scatter)]
            if good.size < 3:
                break
            new = use & ~(np.isfinite(scatter) & (scatter > 3.0 * float(np.median(good))))
            if new.sum() < 2 or np.array_equal(new, use):
                break
            use = new
        step = np.full(s, np.nan)
        n_b = n_a = 0
        if after is not None:
            before_idx = np.where(~after)[0]
            after_idx = np.where(after)[0]
            n_b, n_a = int(before_idx.size), int(after_idx.size)
            b_rows, a_rows = near_flip_rows(after, near_flip)
            if b_rows.size >= 2 and a_rows.size >= 2:
                d = z - _others_median(z, use)
                for j in range(s):
                    a, b = d[a_rows, j], d[b_rows, j]
                    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
                    if a.size >= 2 and b.size >= 2:
                        step[j] = float(np.median(a) - np.median(b))
    return {"scatter": scatter, "step": step, "n": np.sum(np.isfinite(m), axis=0).astype(int), "frames": f,
            "flip": after is not None and bool(np.isfinite(step).any()), "n_before": n_b, "n_after": n_a,
            "in_ensemble": use}


def transit_mags(t, planet: dict | None, occultation=None, ld=(0.4, 0.25)):
    """step 5 fix (#76): the predicted transit at times t (BJD_TDB), in magnitudes (0 out of transit, positive in it),
    and whether it can be trusted near those times: {"mag", "z", "k"}; None without a usable ephemeris and shape
    (period, t0, Rp/R*, a/R*). `occultation(z, k, u1, u2)` is transit_core's model (passed in: no import here)."""
    p = planet or {}
    try:
        period, t0, k, a_rs = (float(p[key]) for key in ("period", "t0", "k", "a_rs"))
    except (KeyError, TypeError, ValueError):
        return None
    b = float(p["b"]) if p.get("b") is not None else 0.3   # Fable nit 4: complete_planet's assumption
    if not all(math.isfinite(v) and v > 0 for v in (period, k, a_rs)) or not math.isfinite(t0) or occultation is None:
        return None
    t = np.asarray(t, dtype=float)
    n = np.round((t - t0) / period)
    tc = t0 + n * period
    cos_i = min(max(b / a_rs, 0.0), 1.0)
    phase = 2.0 * math.pi * (t - tc) / period
    z = a_rs * np.sqrt(np.sin(phase) ** 2 + (cos_i * np.cos(phase)) ** 2)
    z = np.where(np.cos(phase) > 0, z, 1e3)
    flux = np.asarray(occultation(z, k, float(ld[0]), float(ld[1])), dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        mag = np.where(flux > 0, -2.5 * np.log10(flux), np.nan)
    return {"mag": mag, "z": z, "k": k, "tc": tc, "period": period, "t0": t0, "a_rs": a_rs, "b": b,
            "t0_err": _err(p.get("t0_err")), "period_err": _err(p.get("period_err")),
            "err_known": _err(p.get("t0_err")) is not None}


def _err(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v >= 0 else None


def near_contact(t, model: dict, margin: float = TARGET_STEP_MARGIN) -> bool:
    """True when any time in t lies within `margin` + 3 sigma of Tc of a partial phase (ingress or egress: from first
    to second contact and from third to fourth), where an ephemeris error changes the predicted dip fast. The
    contacts are worked out (Fable re-review S6: sampled points could step over a short ingress); a grazing transit
    (no second contact) is partial throughout. Without a known Tc uncertainty the margin grows by EPHEM_ERR_MISSING
    (Fable S5: a typed T0 has none). Times that are not finite count as near."""
    if model is None:
        return True
    t = np.asarray(t, dtype=float)
    if not np.all(np.isfinite(t)):
        return True
    P, a_rs, b, k = model["period"], model["a_rs"], model["b"], model["k"]
    inc = math.acos(min(max(b / a_rs, 0.0), 0.9999))
    s_i = a_rs * math.sin(inc)

    def half(rr):
        arg = (rr * rr - b * b)
        return P / (2 * math.pi) * math.asin(min(math.sqrt(arg) / s_i, 1.0)) if arg > 0 else 0.0
    h14, h23 = half(1.0 + k), half(1.0 - k)
    if h14 <= 0:
        return False   # no transit at all
    for ti in t:
        n = round((ti - model["t0"]) / P)
        tcn = model["t0"] + n * P
        p_err = model.get("period_err")
        if model.get("err_known", True) and model.get("t0_err") is not None and (p_err is not None or n == 0):
            m = margin + 3.0 * math.hypot(model["t0_err"], abs(n) * (p_err or 0.0))
        else:
            m = margin + EPHEM_ERR_MISSING   # a typed T0: 30 min more on each side
        lo, hi = ti - m, ti + m
        for c0, c1 in ((tcn - h14, tcn - h23), (tcn + h23, tcn + h14)):
            if h23 <= 0:
                c0, c1 = tcn - h14, tcn + h14
            if hi >= c0 and lo <= c1:
                return True
    return False


def contact_free(t, model: dict, margin: float = TARGET_STEP_MARGIN) -> np.ndarray:
    """2.4.0 step 6c: per time, True when it lies away from ingress and egress (near_contact for that one time is
    False): the frames on which the target's flip step is fitted with a free depth (free_depth_step). Frames in the
    flat bottom of the transit count (their depth is fitted, as the transit fit fits it); times that are not
    finite never do. All True without a transit."""
    t = np.asarray(t, dtype=float)
    return np.array([bool(math.isfinite(ti)) and not near_contact(np.array([ti]), model, margin) for ti in t],
                    dtype=bool)


def step_plan(after, shape) -> dict:
    """2.4.0 step 6c: what free_depth_step needs, passed where curve_step takes its weights: per row, True after
    the flip, and the predicted transit's shape (mag, positive in transit; NaN or 0 outside)."""
    return {"after": np.asarray(after, dtype=bool), "shape": np.asarray(shape, dtype=float)}


STEP_VIF_MAX = 10.0   # step 6c (Fable M2): above this the step and the depth stand in for each other


def step_depth_vif(after, shape) -> float:
    """2.4.0 step 6c (Fable M2): how much the depth term inflates the step's error (variance inflation factor,
    1/(1 - R^2) of `after` regressed on [1, shape]). 1-3 on a night with frames in transit on both sides of the flip
    or baseline on both sides; hundreds when the flip falls at ingress or egress with no baseline beyond it (a night
    that ends in transit): a step after the flip then looks just like a deeper transit, and the fitted step is noise.
    inf with nothing to fit."""
    a = np.asarray(after, dtype=float)
    sh = np.asarray(shape, dtype=float)
    sh = np.where(np.isfinite(sh), sh, 0.0)
    if a.size < 4 or np.ptp(a) == 0:
        return float("inf")
    X = np.column_stack([np.ones(a.size), sh])
    beta, *_ = np.linalg.lstsq(X, a, rcond=None)
    ss_res = float(np.sum((a - X @ beta) ** 2))
    ss_tot = float(np.sum((a - a.mean()) ** 2))
    if ss_tot <= 0:
        return float("inf")
    r2 = 1.0 - ss_res / ss_tot
    return float("inf") if r2 >= 1.0 - 1e-12 else 1.0 / (1.0 - r2)


def free_depth_step(lc, b_rows, a_rows, plan: dict, min_side: int = FLIP_MIN_SIDE) -> tuple[float, float]:
    """2.4.0 step 6c (John 20 Nov, item 3 "match the fit"): the target's flip step fitted the way the transit fit
    finds it: a least-squares line through every row given (both sides of the flip, in transit too, away from
    ingress and egress) of level + step x (after the flip) + depth scale x (the predicted transit's shape), so a
    published depth that is a little off is fitted, not read as a step. Points beyond 4 sigma (MAD) of the fit are
    left out, up to three times. lc: per-row curve (the target minus a set, mag, with the predicted transit already
    taken out by target_diffs: the depth term then fits what is left of it). Without frames in transit the depth
    term is dropped (a baseline-only step, as step 6b). WASP-49 b 19 Nov: on survey-like samples of ~80 of the real
    frames (stars centred as the run centres them) this gave about +6.2 +/- 1.4 ppt against the fit's +6.4 +/- 0.9;
    step 6b's baseline-only mean on uncentred stars gave 4.6 +/- 1.4 on such samples (+1.1 in the app). Returns (step after minus before in mag, 1-sigma error), NaNs
    with fewer than min_side usable rows a side or when the step cannot be told from the depth."""
    nan = float("nan")
    lc = np.asarray(lc, dtype=float)
    rows = np.r_[np.asarray(b_rows, dtype=int), np.asarray(a_rows, dtype=int)]
    if not rows.size:
        return nan, nan
    after = np.asarray(plan["after"], dtype=bool)[rows]
    shape = np.asarray(plan["shape"], dtype=float)[rows]
    shape = np.where(np.isfinite(shape), shape, 0.0)
    y = lc[rows]
    keep = np.isfinite(y)
    # the depth term only with a few frames really in transit (a grazing or baseline-only night has none)
    use_depth = int((keep & (shape > 1e-4)).sum()) >= 3 and float(np.ptp(shape[keep])) > 1e-4 if keep.any() else False
    if not use_depth:
        keep &= ~(shape > 1e-4)   # Fable S1: one or two frames in transit would carry the depth into the step
    if use_depth and step_depth_vif(after[keep], shape[keep]) > STEP_VIF_MAX:
        return nan, nan           # Fable M2: the step cannot be told from the depth
    X_all = np.column_stack([np.ones(y.size), after.astype(float)] + ([shape] if use_depth else []))

    def enough(k):
        return (int((k & after).sum()) >= max(2, min_side) and int((k & ~after).sum()) >= max(2, min_side)
                and int(k.sum()) > X_all.shape[1] + 1)
    for _ in range(3):
        if not enough(keep):
            return nan, nan
        beta, *_ = np.linalg.lstsq(X_all[keep], y[keep], rcond=None)
        res = y - X_all @ beta
        r_k = res[keep]
        sig = 1.4826 * float(np.median(np.abs(r_k - np.median(r_k))))
        if not sig > 0:
            break
        new = np.isfinite(y) & (np.abs(res) <= 4.0 * sig)
        if np.array_equal(new, keep):
            break
        keep = new
    if not enough(keep):
        return nan, nan
    X, yk = X_all[keep], y[keep]
    beta, *_ = np.linalg.lstsq(X, yk, rcond=None)
    s2 = float(np.sum((yk - X @ beta) ** 2)) / (X.shape[0] - X.shape[1])
    try:
        cov = s2 * np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        return nan, nan
    err = math.sqrt(cov[1, 1]) if cov[1, 1] > 0 else nan
    if not math.isfinite(err):
        return nan, nan
    if X.shape[1] == 3 and step_depth_vif(after[keep], shape[keep]) > STEP_VIF_MAX:
        return nan, nan   # (the clipping can leave a side without baseline)
    return float(beta[1]), err


def out_of_transit(t, model: dict, margin: float = TARGET_STEP_MARGIN) -> np.ndarray:
    """2.4.0 step 6b: per time, True when it lies outside the predicted transit (first to fourth contact) by more
    than `margin` + 3 sigma of the predicted Tc (EPHEM_ERR_MISSING more without a known Tc error, as near_contact):
    the baseline frames, on which the target's flip step is measured as the fit measures it (the fit's free depth
    comes from the frames in transit, the step from the baseline on both sides of the flip). Times that are not
    finite are never baseline. All True without a transit (no first contact)."""
    t = np.asarray(t, dtype=float)
    ok = np.isfinite(t)
    if model is None:
        return np.zeros(t.size, bool)
    P, a_rs, b, k = model["period"], model["a_rs"], model["b"], model["k"]
    inc = math.acos(min(max(b / a_rs, 0.0), 0.9999))
    s_i = a_rs * math.sin(inc)
    arg = (1.0 + k) ** 2 - b * b
    h14 = P / (2 * math.pi) * math.asin(min(math.sqrt(arg) / s_i, 1.0)) if arg > 0 else 0.0
    if h14 <= 0:
        return ok
    out = ok.copy()
    p_err = model.get("period_err")
    for i in np.where(ok)[0]:
        n = round((t[i] - model["t0"]) / P)
        tcn = model["t0"] + n * P
        if model.get("err_known", True) and model.get("t0_err") is not None and (p_err is not None or n == 0):
            m = margin + 3.0 * math.hypot(model["t0_err"], abs(n) * (p_err or 0.0))
        else:
            m = margin + EPHEM_ERR_MISSING
        if abs(t[i] - tcn) <= h14 + m:
            out[i] = False
    return out


def target_diffs(t_mag, comp_mags, t_model=None):
    """step 5 fix (#76): per frame (rows) and candidate (columns): the target's magnitude minus the candidate's, each
    zero-pointed to its own median, with the predicted transit taken out of the target (t_model, mag, positive in
    transit). A set's light curve is the target minus the mean of its comps, so its step is from the mean of these
    columns."""
    tm = np.asarray(t_mag, dtype=float)
    cm = np.asarray(comp_mags, dtype=float)
    if t_model is not None:
        tm = tm - np.asarray(t_model, dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        tz = tm - np.nanmedian(tm)
        cz = cm - np.nanmedian(cm, axis=0)
    return tz[:, None] - cz


def set_curve(diffs, cols) -> np.ndarray:
    """The target minus the set's mean, per frame (NaN where a comp of the set was not measured)."""
    d = np.asarray(diffs, dtype=float)[:, list(cols)]
    ok = np.all(np.isfinite(d), axis=1)
    return np.where(ok, d.mean(axis=1), np.nan)


def curve_step(lc, b_rows, a_rows, min_side: int = FLIP_MIN_SIDE, weights=None) -> tuple[float, float]:
    """(median after - median before, its 1-sigma error) of a per-frame curve on the given rows; NaNs with fewer
    than min_side finite frames a side. weights (step 6b, whole_night_rows): over all rows, a weighted median per
    side, its error from the weighted scatter and the effective number of frames. Step 6c (Fable N5): `weights` may
    instead be a step_plan (a dict): the step is then fitted with a free depth (free_depth_step), which is what the
    app passes; the weighted and plain branches stay for the tests and older callers."""
    if isinstance(weights, dict):   # step 6c: a step_plan: the free-depth fit (free_depth_step)
        return free_depth_step(lc, b_rows, a_rows, weights, min_side)
    lc = np.asarray(lc, dtype=float)
    a_rows, b_rows = np.asarray(a_rows, dtype=int), np.asarray(b_rows, dtype=int)
    a, b = lc[a_rows], lc[b_rows]
    if weights is None:
        a, b = a[np.isfinite(a)], b[np.isfinite(b)]
        if a.size < max(2, min_side) or b.size < max(2, min_side):
            return float("nan"), float("nan")
        err = 1.2533 * math.sqrt(float(np.var(a, ddof=1)) / a.size + float(np.var(b, ddof=1)) / b.size)
        return float(np.median(a) - np.median(b)), err
    w = np.asarray(weights, dtype=float)
    wa, wb = w[a_rows], w[b_rows]
    ka, kb = np.isfinite(a) & (wa > 0), np.isfinite(b) & (wb > 0)
    a, b, wa, wb = a[ka], b[kb], wa[ka], wb[kb]
    if a.size < max(2, min_side) or b.size < max(2, min_side):
        return float("nan"), float("nan")
    # the fit is least squares, so a weighted MEAN (WASP-49 b 19 Nov, every frame: mean +5.7 ppt against the fit's
    # +6.4, median +3.7), with points beyond 4 sigma of the median left out so one bad frame does not set it
    def side(x, ww):
        keep = np.ones(x.size, bool)
        for _ in range(3):
            med = _wmedian(x[keep], ww[keep])
            sig = 1.4826 * _wmedian(np.abs(x[keep] - med), ww[keep])
            new = np.abs(x - med) <= 4.0 * sig if sig > 0 else keep
            if new.sum() < 2 or np.array_equal(new, keep):
                break
            keep = new
        x, ww = x[keep], ww[keep]
        mean = float(np.sum(ww * x) / ww.sum())
        neff = float(ww.sum() ** 2 / np.sum(ww * ww))
        v = float(np.sum(ww * (x - mean) ** 2) / ww.sum()) * neff / max(neff - 1.0, 1.0)
        return mean, v / max(neff, 1.0)
    # Fable N16: a side whose weight sits on one or two rows (weights 40/1/1) has too few effective frames for an error
    for ww in (wa, wb):
        if float(ww.sum() ** 2 / np.sum(ww * ww)) < min_side:
            return float("nan"), float("nan")
    ma, va = side(a, wa)
    mb, vb = side(b, wb)
    return ma - mb, math.sqrt(va + vb)


def set_target_step(diffs, cols, b_rows, a_rows, weights=None) -> tuple[float, float]:
    """The target's flip step (after minus before, mag) with the comps in columns `cols`, and its 1-sigma error: the
    median over the frames after minus the median before (step 6b: every frame read on each side, weighted as the
    fit weights the night, with weights from whole_night_rows), of the target minus the set's mean. (NaN, NaN)
    with fewer than FLIP_MIN_SIDE frames a side where every comp of the set was measured."""
    if not len(cols):
        return float("nan"), float("nan")
    return curve_step(set_curve(diffs, cols), b_rows, a_rows, weights=weights)


def variable_flags(scatter, inst, factor: float = VARIABLE_FACTOR, window: float = 0.5,
                   min_peers: int = VARIABLE_MIN_PEERS) -> list:
    """Per candidate: (scatter / median scatter of the candidates within `window` mag of it) when that is over
    `factor` (it looks variable tonight), else None. With fewer than min_peers such stars the window widens to twice
    `window`, then to every candidate (at least 3); with fewer than 3 others it is not judged."""
    sc = np.asarray(scatter, dtype=float)
    m = np.asarray(inst, dtype=float)
    out = []
    for j in range(sc.size):
        if not math.isfinite(sc[j]) or not math.isfinite(m[j]):
            out.append(None)
            continue
        peers = []
        # a sparse field: the window widens (1 mag, then every candidate with at least 3 others)
        for win, need in ((window, min_peers), (2 * window, min_peers), (float("inf"), 3)):
            peers = [sc[k] for k in range(sc.size) if k != j and math.isfinite(sc[k]) and math.isfinite(m[k])
                     and abs(m[k] - m[j]) <= win]
            if len(peers) >= need:
                break
        else:
            out.append(None)
            continue
        ref = float(np.median(peers))
        out.append(float(sc[j] / ref) if ref > 0 and sc[j] > max(factor * ref, VARIABLE_FLOOR) else None)
    return out


def rank_key(base: float, scatter: float, ref_scatter: float, step: float = float("nan"), dist_frac: float = 0.0,
             flip: bool = False, use_step: bool = False) -> float:
    """Lower is better. The night's own scatter first (relative to the survivors' median), then the catalog score
    (brightness and color, as before, at half weight); on a flip night also the distance from the target (as a
    fraction of the frame's half-diagonal), since pier-side flat errors grow away from the center. The flip step
    itself is not ranked star by star when the set is chosen on the target's measured step (choose_set; one comp's
    step for the target is noisy on a few frames). When the target's step is not measured (use_step, Fable
    re-review S2), a star's step against the others' median counts per 0.01 mag, as before, so a star that jumps at
    the flip ranks lower."""
    sc = scatter / ref_scatter if (math.isfinite(scatter) and ref_scatter and ref_scatter > 0) else 2.0
    key = sc + 0.5 * (base if math.isfinite(base) else 1.0)
    if flip:
        key += 0.5 * dist_frac
        if use_step:
            key += abs(step) / 0.01 if math.isfinite(step) else 1.0
    return float(key)


def choose_set(names: list, keys: list, curve, b_rows, a_rows, count: int = 4,
               top: int = SET_TOP, scatter=None, weights=None) -> tuple[list, float, float]:
    """On a flip night: from the `top` best-ranked (names in rank order, keys their rank keys), the set (2 up to
    `count` stars) whose MEASURED flip step for the target is nearest zero. curve(names) -> per-frame target minus
    the set's mean (set_curve); the step is from the near-flip rows (curve_step). Step 5 fix (#76): on WASP-49 b a
    forecast from the stars nearest the target chose the set that gave the target the largest step.
    Cost = |step| + the set's own noise (sqrt(sum of its members' scatter squared) / n, from `scatter` in name
    order; Fable re-review S-A: on a night with only small real steps the step alone picks on noise and drops the
    steadiest comps) + SET_SIZE_COST per comp fewer than `count`; the lowest cost wins, then the best-ranked. On
    WASP-49 b the noise term kept r 8's set (-0.5 ppt over the night) and moved r 10.3's to one at +2.2 ppt. No tie
    slack (Fable re-review S3/S4: a slack picked sets at its edge, and favored pairs):
    on WASP-49 b, with every frame measured, the measured step tracked the night's fitted step to 2 mmag
    (correlation 0.95-0.99 over all 154 sets), and the lowest measured step chose sets within 1 ppt of zero at both
    apertures, where a 1 mmag tie slack let in sets stepping up to 5 ppt. Returns (names in rank order, step,
    error); ([], NaN, NaN) when no set of 2 or more has a measured step."""
    sc_of = {}
    if scatter is not None:
        sc_of = {n: float(v) for n, v in zip(names, scatter) if v is not None and math.isfinite(float(v))}
    pool = [(n, k) for n, k in zip(names, keys)][:top]
    k = min(count, len(pool))
    nan = float("nan")
    if k < 2:
        return [], nan, nan
    best = None
    for size in range(2, k + 1):
        for c in itertools.combinations(pool, size):
            st, er = curve_step(curve([p[0] for p in c]), b_rows, a_rows, weights=weights)
            if not math.isfinite(st):
                continue
            noise = (math.sqrt(sum(sc_of[p[0]] ** 2 for p in c)) / size) if all(p[0] in sc_of for p in c) else 0.0
            score = (abs(st) + noise + SET_SIZE_COST * (k - size), sum(p[1] for p in c) / size)
            if best is None or score < best[0]:
                best = (score, c, st, er)
    if best is None:
        return [], nan, nan
    order = {p[0]: i for i, p in enumerate(pool)}
    chosen = sorted(best[1], key=lambda p: order[p[0]])
    return [p[0] for p in chosen], best[2], best[3]


def trial_radii(fwhm: float, current: float | None = None) -> list[float]:
    """#61: the apertures tried (x FWHM, at least 2 px), plus the current one."""
    out = set()
    if fwhm and math.isfinite(fwhm) and fwhm > 0:
        out.update(max(2.0, round(f * fwhm, 1)) for f in APERTURE_FACTORS)
    if current and math.isfinite(current) and current > 0:
        out.add(round(float(current), 1))
    return sorted(out)


def aperture_choice(scatter_by_r: dict, current: float) -> dict | None:
    """#61: scatter_by_r {r: median scatter (mag) of the stars like the target against the ensemble at that
    aperture}. Returns {"r", "scatter", "current_scatter", "gain"} for the lowest-scatter aperture when it beats the
    current one by at least APERTURE_GAIN, else None (keep the current aperture)."""
    rows = {float(r): float(v) for r, v in scatter_by_r.items() if v is not None and math.isfinite(v) and v > 0}
    if not rows:
        return None
    cur = min(rows, key=lambda r: abs(r - float(current)))
    if abs(cur - float(current)) > 0.06:
        return None
    best = min(rows, key=lambda r: rows[r])
    gain = 1.0 - rows[best] / rows[cur]
    if best == cur or gain < APERTURE_GAIN:
        return None
    return {"r": best, "scatter": rows[best], "current_scatter": rows[cur], "gain": gain}


def mmag(v: float) -> str:
    return f"{1000.0 * v:.1f} mmag" if v is not None and math.isfinite(v) else "—"


def own_step_ppt(step_mag: float) -> float:
    """A step in mag (+ = fainter after the flip) as ppt of flux (+ = brighter after), the fit's units and sign."""
    return (10 ** (-0.4 * step_mag) - 1.0) * 1000.0 if math.isfinite(step_mag) else float("nan")


def own_step_out(steps, errs, judged, limit: float = OWN_STEP_MAX, sigma: float = OWN_STEP_SIGMA,
                 keep_min: int = 2) -> tuple[dict, list]:
    """step 6c-2 (John 20 Nov): which candidates jump too much at the meridian flip on their own.
    steps, errs: per candidate, the target's flip step against that comp alone and its error (mag; NaN = not
    measured); judged: per candidate, True when it is one the suggestions could use (in the brightness window and not
    already left out). A judged candidate is out when |step| is beyond `limit` (flux fraction) and beyond `sigma` x its
    error. When that would leave fewer than `keep_min` judged candidates with a measured step, the steadiest
    (smallest |step|) are kept instead, so the night still gets comps (the app passes keep_min=0 and lets stars back
    itself, after its other gates). Returns ({index: step in ppt} for those left
    out, [indices kept although beyond the limit])."""
    st = np.asarray(steps, dtype=float)
    er = np.asarray(errs, dtype=float)
    ok = np.asarray(judged, dtype=bool) & np.isfinite(st)
    # Fable N1: judged in ppt of flux, the units the message gives (+-10 ppt on both sides)
    with np.errstate(invalid="ignore", over="ignore"):
        ppt = np.where(np.isfinite(st), (10 ** (-0.4 * np.where(np.isfinite(st), st, 0.0)) - 1.0) * 1000.0, np.nan)
    big = ok & (np.abs(ppt) > limit * 1000.0) & ~(np.isfinite(er) & (np.abs(st) <= sigma * er))
    out = {int(j) for j in np.where(big)[0]}
    kept_anyway = []
    passing = int((ok & ~big).sum())
    if passing < keep_min:
        need = keep_min - passing
        for j in sorted(out, key=lambda j_: abs(st[j_]))[:need]:
            out.discard(j)
            kept_anyway.append(j)
    return {j: own_step_ppt(float(st[j])) for j in sorted(out)}, sorted(kept_anyway)
