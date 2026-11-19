"""2.4.0 step 5 (#76, #61): the pre-run comp survey. Pure functions (no Tk, no file reading) so the tests can drive
them with made-up numbers; clearv_app reads the frames and calls these.

The survey measures every comp candidate (catalog stars of the right brightness, not known variables, clear of the
frame edge all night) on a SAMPLE of the night's own frames, calibrated and lined up like the run itself:
about every 10th light, the first and last, the frames next to the sharpest ones read, and enough on each side of a
meridian flip. From those frames each candidate gets:
  - its scatter against the other candidates (an iterative ensemble, like Comp health),
  - its step at the meridian flip (after minus before, against the others), when the night has one,
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
SET_STEP_SLACK = 0.003     # mag: sets whose cost is this close to the best count as equal (then the best-ranked)
SET_SIZE_COST = 0.003      # mag: what one comp fewer than asked costs a set (fewer comps, less S/N)
SET_TOP = 8                # the set is chosen from the 8 best-ranked survivors
FLIP_NEAR = 6              # the flip step from the 6 frames read nearest the flip on each side
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
    SURVEY_SIDE_MIN (taken from between the last frame read before the flip and the first after it). Never a frame
    already read."""
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
        # and the frames right at the flip, which say where it is
        for j in range(lo + 1, hi):
            if j not in have and len(out) < 12:
                have.add(j)
                out.append(j)
    return sorted(out)


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
            # the frames nearest the flip: the last of the first side and the first of the second (time order)
            first_after = after_idx.min()
            b_rows = before_idx[before_idx < first_after][-near_flip:]
            a_rows = after_idx[after_idx > b_rows.max()][:near_flip] if b_rows.size else after_idx[:near_flip]
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


def steps_near_target(step, dist, eligible=None, scatter=None, k: int = 3):
    """Each candidate's flip step against a reference: the median step of the k candidates nearest the target among
    the ELIGIBLE ones (those that could be comps: through the gates, in the comp brightness window; Fable 2: the faint
    stars nearest the target are noise, not a reference). The pier-side error depends on where a star sits on the
    sensor (the flat and the optics are point-symmetric about the axis, and the flip turns the field 180 degrees), so
    the stars nearest the target step most like it; a set whose steps against them average to zero leaves the
    target's step out of the result. Against the other candidates instead, a set can agree with itself and still give
    the target a large step (WASP-49 b 19 Nov, r 8: TYC 5936-2079-1 + 2074-1 + 2164-1 agree to -2.8 mmag among
    themselves but give the target -26.5 mmag; 2073-1 + 2164-1, the two nearest it, give -1.6). A median of a few,
    not a wider or weighted mean: the reference must stay local (a mean over 5 of WASP-49's comps chose a set that
    left the target -20.8 mmag). `scatter` is accepted for the callers' convenience and not used. All NaN with fewer
    than 2 eligible stars with a measured step."""
    st = np.asarray(step, dtype=float)
    d = np.asarray(dist, dtype=float)
    ok = np.isfinite(st) & np.isfinite(d)
    if eligible is not None:
        ok &= np.asarray(eligible, dtype=bool)
    if ok.sum() < 2:
        return np.full(st.shape, np.nan)
    near = np.argsort(np.where(ok, d, np.inf))[:max(2, min(k, int(ok.sum())))]
    return st - float(np.median(st[near]))


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
             flip: bool = False) -> float:
    """Lower is better. The night's own scatter first (relative to the survivors' median), then the catalog score
    (brightness and color, as before, at half weight); on a flip night also the measured flip step (per 0.01 mag)
    and the distance from the target (as a fraction of the frame's half-diagonal), since pier-side flat errors grow
    away from the center."""
    sc = scatter / ref_scatter if (math.isfinite(scatter) and ref_scatter and ref_scatter > 0) else 2.0
    key = sc + 0.5 * (base if math.isfinite(base) else 1.0)
    if flip:
        key += (abs(step) / 0.01 if math.isfinite(step) else 1.0) + 0.5 * dist_frac
    return float(key)


def choose_set(names: list, keys: list, steps: list, count: int = 4, top: int = SET_TOP) -> tuple[list, float]:
    """On a flip night: from the `top` best-ranked (names in rank order, keys their rank keys), the set (2 up to
    `count` stars) with the lowest cost = |expected flip step| (the mean of its members' steps) + half their mean
    |step| (steady stars first: a set that only cancels big steps is fragile) + SET_SIZE_COST per comp fewer than
    `count`; among sets within SET_STEP_SLACK of the lowest, the best-ranked (lowest total key). Stars without a
    measured step only join when there are not enough with one. Returns (names in rank order, expected step)."""
    pool = [(n, k, s) for n, k, s in zip(names, keys, steps)][:top]
    stepped = [p for p in pool if math.isfinite(p[2])]
    k = min(count, len(pool))
    if k == 0:
        return [], float("nan")
    if len(stepped) < k:
        chosen = pool[:k]
        vals = [p[2] for p in chosen if math.isfinite(p[2])]
        return [p[0] for p in chosen], (float(np.mean(vals)) if vals else float("nan"))
    combos = []
    for size in range(min(2, k), k + 1):
        for c in itertools.combinations(stepped, size):
            st = [p[2] for p in c]
            cost = abs(float(np.mean(st))) + 0.5 * float(np.mean(np.abs(st))) + SET_SIZE_COST * (k - size)
            combos.append((cost, sum(p[1] for p in c) / size, c))
    best = min(c[0] for c in combos)
    ok = [c for c in combos if c[0] <= best + SET_STEP_SLACK]
    pick = min(ok, key=lambda c: c[1])[2]
    order = {p[0]: i for i, p in enumerate(pool)}
    chosen = sorted(pick, key=lambda p: order[p[0]])
    return [p[0] for p in chosen], float(np.mean([p[2] for p in chosen]))


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
