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
    reads a star that jumps on 40 % of the frames as steady)."""
    a = np.asarray(a, dtype=float)
    a = a[np.isfinite(a)]
    if a.size < 3:
        return float("nan")
    q10, q90 = np.percentile(a, [10, 90])
    return float((q90 - q10) / 2.5631)


def ensemble_stats(mags, flipped=None, iterations: int = 3) -> dict:
    """mags: instrumental magnitudes, frames in rows, candidates in columns (NaN = not measured). flipped: per frame,
    True after the meridian flip (None or all one value: no flip). Each candidate against the mean of the others
    (each zero-pointed to its own median, so brightness does not matter), the ensemble rebuilt without stars that
    scatter more than 3x the median, `iterations` times. Returns {"scatter": per star (mag, robust),
    "step": per star (after minus before, mag; NaN without a flip or with fewer than 2 frames a side),
    "n": frames measured per star, "frames": frames, "flip": bool}."""
    m = np.asarray(mags, dtype=float)
    if m.ndim != 2 or m.shape[1] == 0:
        return {"scatter": np.zeros(0), "step": np.zeros(0), "n": np.zeros(0, int), "frames": 0, "flip": False}
    f, s = m.shape
    with np.errstate(all="ignore"):
        z = m - np.nanmedian(m, axis=0)
    sides = None
    if flipped is not None:
        fl = np.asarray([bool(v) for v in flipped])
        if fl.size == f and fl.any() and (~fl).any():
            sides = fl
    if sides is not None:
        # each side zero-pointed on its own for the scatter, so a flip step is not counted as scatter
        z_sc = z.copy()
        with np.errstate(all="ignore"):
            for side in (False, True):
                rows = sides == side
                z_sc[rows] -= np.nanmedian(z[rows], axis=0)
    else:
        z_sc = z
    use = np.ones(s, dtype=bool)
    scatter = np.full(s, np.nan)
    for _ in range(max(1, iterations)):
        scatter = np.full(s, np.nan)
        for j in range(s):
            others = use.copy()
            others[j] = False
            if not others.any():
                continue
            with np.errstate(all="ignore"):
                ens = np.nanmean(z_sc[:, others], axis=1)
            scatter[j] = _robust_sigma(z_sc[:, j] - ens)
        good = scatter[use & np.isfinite(scatter)]
        if good.size < 3:
            break
        new = use & ~(np.isfinite(scatter) & (scatter > 3.0 * float(np.median(good))))
        if new.sum() < 2 or np.array_equal(new, use):
            break
        use = new
    step = np.full(s, np.nan)
    if sides is not None:
        for j in range(s):
            others = use.copy()
            others[j] = False
            if not others.any():
                continue
            with np.errstate(all="ignore"):
                # the MEDIAN of the others: one star with a big pier-side step (an edge star) must not shift every
                # other star's step (WASP-49 b 19 Nov: APASS J060338.6 +63 mmag made the central comps read -35)
                d = z[:, j] - np.nanmedian(z[:, others], axis=1)
            a, b = d[sides], d[~sides]
            a, b = a[np.isfinite(a)], b[np.isfinite(b)]
            if a.size >= 2 and b.size >= 2:
                step[j] = float(np.median(a) - np.median(b))
    return {"scatter": scatter, "step": step, "n": np.sum(np.isfinite(m), axis=0).astype(int), "frames": f,
            "flip": sides is not None, "in_ensemble": use}


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
