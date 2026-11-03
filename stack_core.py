"""2.3.0 (#54): stacking calibrated frames when the comparison stars are weak (all modes).

Everything the Stack step needs that does not touch the window: which frames may be stacked, the suggested stack
size N (the comps-should-not-dominate rule, the optional precision goal and the time-resolution cap), the groups of
consecutive frames, the combine (a per-pixel sigma-clipped MEAN, plain mean below 5 frames), and the stack files.

Choices (agreed with John, 3 Nov 2026, GitHub #54):
- ONE combine method: sigma-clipped mean. No median (about 25 % more noise, and not linear when seeing or
  transparency change), no weighting or normalizing of frames, no drizzle, no local normalization.
- Shifts are WHOLE PIXELS (whole 2x2 Bayer cells on a color camera), taken from the drift the run already measured
  (the registration of each frame). A whole-pixel shift moves every pixel's value unchanged, so flux is conserved
  exactly and the noise of neighboring pixels stays independent: the photometry's sky-noise and photon-noise error
  model stays right for the stack. A sub-pixel (bilinear) shift would smooth the noise, and the measured sky scatter
  would then promise error bars that are too small. The price is at most half a pixel of extra blur per frame,
  small against the aperture (the same choice as quick_stack's for the field solve).
- Stacks are stored on the scale of a SUM of the N frames (clipped mean x N) with EXPTIME = the exposure sum, as
  32-bit floats (no 16-bit ceiling). Then every image unit is still the same number of electrons as in one frame,
  so the run's photon-noise setup (EGAIN, binning, superpixel, flat) applies unchanged, the measured sky scatter
  includes the read noise of all N frames, and the scintillation term uses the effective exposure N x t.
- Frames are stacked on the calibrated, binned mosaic before the debayer (the debayer is a weighted sum of the
  four Bayer cells, so it commutes with the mean): the run then measures the stack exactly as it measures a frame,
  in any channel, color included.
- Never across a meridian flip (or a rotator move) and never across a gap: each pier side is stacked on its own.
- Rejected frames are not in the run; saturated, drifted, cloud-flagged or otherwise flagged frames are left out
  before stacking (saturation is per exposure, so a sum of unsaturated frames stays linear).
Nothing here needs a plate solution, header pointing (RA/Dec) or a color camera.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil

import numpy as np

try:
    from astropy.io import fits
except Exception:   # pragma: no cover - astropy is a requirement of the app
    fits = None

STACK_VERSION = 1
CLIP_SIGMA = 3.0          # per-pixel clip, in units of the (trimmed, bias-corrected) scatter of that pixel
MIN_CLIP_FRAMES = 5       # below this, a plain mean (too few values to tell an outlier)
COMP_SHARE = 0.5          # the default rule: a stack's comp noise at most 1/2 of the target's noise in one frame
TRANSIT_CAP_FRACTION = 1.0 / 5.0     # of the ingress / egress duration
VARIABLE_CAP_FRACTION = 1.0 / 50.0   # of the period
DISCOVERY_CAP_S = 300.0              # 5 min
DEFAULT_TRANSIT_CAP_S = 120.0        # no ingress/egress known: conservative
DEFAULT_VARIABLE_CAP_S = 300.0       # no period known: conservative
GAP_CADENCES = 3.0        # a gap longer than this many frame spacings ends a stack
# Flags that keep a frame out of a stack (every flag the run sets that means "not a good measurement").
LEAVE_OUT = ("sat", "compsat", "drift", "noflux", "nocomp", "cloud", "cloudedge", "haze", "edge", "offedge")
STACK_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".shobs_p_cache", "stacks")


# ---- which frames, and how far apart -------------------------------------------------------------------------
def frame_record(obs, reg=None) -> dict:
    """One measured frame for the planner: its file, time, exposure, pier side, drift and why it may be left out."""
    reg = reg if reg is not None else getattr(obs, "_stack_reg", None)
    flags = set(str(getattr(obs, "flag", "") or "").split())
    why = ""
    if not math.isfinite(float(getattr(obs, "mag", float("nan")))):
        why = "no magnitude"
    elif flags:
        hit = [f for f in flags if f in LEAVE_OUT] or sorted(flags)
        why = hit[0]
    elif reg is None:
        why = "not aligned"
    rec = {"path": str(getattr(obs, "path", "")), "jd": float(obs.jd), "exp": float(getattr(obs, "exptime", 0) or 0),
           "segment": str(getattr(obs, "segment", "") or ""), "keep": not why, "why": why,
           "dx": float(reg["dx"]) if reg else 0.0, "dy": float(reg["dy"]) if reg else 0.0,
           "angle": float(reg.get("angle", 0.0)) if reg else 0.0, "flipped": bool(reg.get("flipped")) if reg else False,
           "raw_peak": float(getattr(obs, "raw_peak", float("nan"))),
           "raw_peaks": dict(getattr(obs, "raw_peaks", {}) or {})}
    return rec


def scan_record(path: str, jd: float, exp: float, reg: dict | None, rotator=None) -> dict:
    """The planner's record for a Discovery scan frame (no per-frame magnitude: every scanned frame is kept)."""
    return {"path": str(path), "jd": float(jd), "exp": float(exp or 0), "segment": "" if rotator is None else
            f"rot{round(float(rotator) / 5.0) * 5:g}", "keep": reg is not None, "why": "" if reg else "not aligned",
            "dx": float(reg["dx"]) if reg else 0.0, "dy": float(reg["dy"]) if reg else 0.0,
            "angle": float(reg.get("angle", 0.0)) if reg else 0.0, "flipped": bool(reg.get("flipped")) if reg else False,
            "raw_peak": float("nan"), "raw_peaks": {}}


def left_out_counts(records) -> dict:
    out = {}
    for r in records:
        if not r["keep"]:
            out[r["why"]] = out.get(r["why"], 0) + 1
    return out


def cadence_seconds(records) -> float:
    """The usual spacing of the kept frames (median of the time between neighbors, in seconds); at least the
    exposure. NaN with fewer than two frames."""
    kept = sorted((r for r in records if r["keep"]), key=lambda r: r["jd"])
    if len(kept) < 2:
        return float("nan")
    d = np.diff([r["jd"] for r in kept]) * 86400.0
    d = d[d > 0]
    if not d.size:
        return float("nan")
    exps = [r["exp"] for r in kept if r["exp"] > 0]
    return float(max(np.median(d), np.median(exps) if exps else 0.0))


# ---- the noise check (first per-frame pass) ------------------------------------------------------------------
def noise_check(points, scint=None, point_err=None) -> dict | None:
    """Per-frame photon noise of the target and of the comp ensemble, from the run's own error model (target_var,
    comp_vars; mag^2), median over the frames that would be stacked. Returns {"sig_t", "sig_c", "ratio",
    "snr_t", "snr_c", "comp_snr" (each comp), "sig_point" (a point's whole error incl. scintillation), "n"} or None."""
    t, c, each, tot = [], [], [], []
    for o in points:
        if getattr(o, "flag", "") or not math.isfinite(float(getattr(o, "mag", float("nan")))):
            continue
        tv = float(getattr(o, "target_var", float("nan")))
        cv = [float(v) for v in (getattr(o, "comp_vars", None) or []) if math.isfinite(float(v))]
        if not (math.isfinite(tv) and tv > 0 and cv):
            continue
        t.append(tv)
        c.append(sum(cv) / len(cv) ** 2)   # the error of a mean of n comp magnitudes
        each.append(cv)
        if point_err is not None:
            try:
                e = float(point_err(o, scint))
                if math.isfinite(e):
                    tot.append(e)
            except Exception:
                pass
    if len(t) < 3:
        return None
    sig_t, sig_c = math.sqrt(float(np.median(t))), math.sqrt(float(np.median(c)))
    n_comp = max(len(v) for v in each)
    comp_snr = []
    for k in range(n_comp):
        vals = [v[k] for v in each if len(v) > k]
        comp_snr.append(1.0857362 / math.sqrt(float(np.median(vals))) if vals else float("nan"))
    sig_point = float(np.median(tot)) if tot else math.hypot(sig_t, sig_c)
    return {"sig_t": sig_t, "sig_c": sig_c, "ratio": sig_c / sig_t if sig_t > 0 else float("inf"),
            "snr_t": 1.0857362 / sig_t, "snr_c": 1.0857362 / sig_c, "comp_snr": comp_snr,
            "sig_point": sig_point, "n": len(t)}


# ---- the time-resolution cap ---------------------------------------------------------------------------------
def ingress_days(planet: dict | None) -> float | None:
    """Ingress (= egress) duration in days, first to second contact, from the Input page's planet (circular orbit,
    Seager & Mallen-Ornelas 2003: (T14 - T23) / 2). With only T14 and Rp/R*: T14 k / (1 + k) (b = 0). None if unknown."""
    p = planet or {}
    period, a_rs, k, b, t14 = (p.get(key) for key in ("period", "a_rs", "k", "b", "t14"))
    try:
        if period and a_rs and k and a_rs > 1:
            b = float(b or 0.0)
            inc = math.acos(min(max(b / a_rs, 0.0), 0.9999))
            def dur(kk):
                arg = (1.0 + kk) ** 2 - b * b
                if arg <= 0:
                    return 0.0
                return period / math.pi * math.asin(min(math.sqrt(arg) / (a_rs * math.sin(inc)), 1.0))
            t14_, t23 = dur(k), dur(-k)
            tau = (t14_ - t23) / 2.0 if t23 > 0 else t14_ / 2.0   # grazing: no flat bottom
            if tau > 0 and math.isfinite(tau):
                return float(tau)
        if t14 and k:
            return float(t14) * float(k) / (1.0 + float(k))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return None


def time_cap(mode: str, planet: dict | None = None, period_days: float | None = None) -> dict:
    """How long one stack may last before it blurs the signal: {"seconds", "text", "known"}.
    Transits: 1/5 of the ingress/egress; Variables: 1/50 of the period; Discovery: 5 minutes (events faster than a
    stack are hidden). Without the planet or period, a conservative default, and the text says so."""
    if mode == "transits":
        tau = ingress_days(planet)
        if tau:
            s = tau * 86400.0 * TRANSIT_CAP_FRACTION
            return {"seconds": s, "known": True,
                    "text": f"1/5 of the {tau * 1440:.1f}-min ingress/egress (Input page planet) = {_dur(s)}"}
        return {"seconds": DEFAULT_TRANSIT_CAP_S, "known": False,
                "text": f"{_dur(DEFAULT_TRANSIT_CAP_S)}, a conservative default: no ingress/egress known (enter the "
                        "planet on the Input page and Lookup)"}
    if mode == "variables":
        if period_days and period_days > 0:
            s = period_days * 86400.0 * VARIABLE_CAP_FRACTION
            return {"seconds": s, "known": True, "text": f"1/50 of the {_period_words(period_days)} period = {_dur(s)}"}
        return {"seconds": DEFAULT_VARIABLE_CAP_S, "known": False,
                "text": f"{_dur(DEFAULT_VARIABLE_CAP_S)}, a conservative default: no period known (VSX gave none)"}
    return {"seconds": DISCOVERY_CAP_S, "known": True,
            "text": f"{_dur(DISCOVERY_CAP_S)} for a field scan; stacking hides events faster than one stack"}


def _dur(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} h"


def _period_words(days: float) -> str:
    return f"{days * 24:.2f}-h" if days < 1 else f"{days:.3f}-d"


# ---- the suggested N -----------------------------------------------------------------------------------------
def suggest_n(noise: dict | None, cadence_s: float, cap: dict, n_frames: int, goal_ppt: float | None = None,
              cap_frames_override: int | None = None) -> dict:
    """The stack size N for the Suggest line.

    Default rule (#54): the smallest N at which a stack's comp-ensemble noise is no more than 1/2 of the target's own
    noise in one frame, sig_c / sqrt(N) <= sig_t / 2: then a stacked point's comps add no more than a perfect-comp
    single frame would carry, and the comps stop setting the noise floor of the light curve. (Within a stack the
    comps and the target both average down as 1/sqrt(N), so their RATIO cannot change: the rule compares with the
    single frame.) Optional precision goal (ppt per point): N grows until a point's whole error (photon +
    scintillation, sig_point / sqrt(N)) meets it. Cap: the stack may not last longer than cap["seconds"].
    Returns {"n", "n_rule", "n_goal", "n_cap", "capped", "goal_met", "dominate", "n_stacks", "span_s", "snr_c"}."""
    out = {"n": 1, "n_rule": 1, "n_goal": None, "n_cap": None, "capped": False, "goal_met": None, "dominate": False,
           "n_stacks": n_frames, "span_s": cadence_s, "snr_c": float("nan"), "point_ppt": float("nan")}
    if noise:
        out["dominate"] = noise["ratio"] > 1.0
        need = (noise["sig_c"] / (COMP_SHARE * noise["sig_t"])) ** 2 if noise["sig_t"] > 0 else float("inf")
        out["n_rule"] = max(1, int(math.ceil(need - 1e-9))) if math.isfinite(need) else 10 ** 6
    if goal_ppt and goal_ppt > 0 and noise:
        goal_mag = 1.0857362e-3 * goal_ppt
        out["n_goal"] = max(1, int(math.ceil((noise["sig_point"] / goal_mag) ** 2 - 1e-9)))
    n = max(out["n_rule"], out["n_goal"] or 1)
    if cap_frames_override:
        n_cap = int(cap_frames_override)
    elif math.isfinite(cadence_s) and cadence_s > 0:
        n_cap = max(1, int(math.floor(cap["seconds"] / cadence_s + 1e-9)))
    else:
        n_cap = 1
    out["n_cap"] = n_cap
    if n > n_cap:
        n, out["capped"] = n_cap, True
    n = max(1, min(n, max(n_frames, 1)))
    out["n"] = n
    if out["n_goal"] is not None:
        out["goal_met"] = n >= out["n_goal"]
    out["n_stacks"] = int(math.ceil(n_frames / n)) if n_frames else 0
    out["span_s"] = n * cadence_s if math.isfinite(cadence_s) else float("nan")
    if noise:
        out["snr_c"] = noise["snr_c"] * math.sqrt(n)
        out["point_ppt"] = noise["sig_point"] / math.sqrt(n) / 1.0857362e-3
    return out


def suggest_text(mode: str, noise: dict | None, sug: dict, cap: dict, n_kept: int, goal_ppt: float | None) -> tuple:
    """(the line's text, warn?) for the Photometry page's Stack row."""
    n = sug["n"]
    span = _dur(sug["span_s"]) if math.isfinite(sug.get("span_s", float("nan"))) else "?"
    tail = f"; {n_kept} frames → {sug['n_stacks']} points"
    cap_note = ""
    if sug["capped"]:
        want = max(sug["n_rule"], sug["n_goal"] or 1)
        cap_note = (f" Capped at {sug['n_cap']} frames by the time resolution ({cap['text']}); the "
                    + ("precision goal" if (sug["n_goal"] or 0) > sug["n_rule"] else "comp rule")
                    + f" wanted {want}.")
    elif not cap.get("known"):
        cap_note = f" Time cap: {cap['text']}."
    if sug.get("goal_met") is False:
        cap_note += f" The {goal_ppt:g} ppt goal is not met (about {sug['point_ppt']:.1f} ppt per point)."
    if mode == "discovery" or noise is None:
        if mode == "discovery":
            return (f"Stacking (optional): stacks of {n} frames ({span} each){tail}. A stack hides events faster "
                    f"than itself (cap {cap['text']}).", False)
        return (f"Stacking: the noise could not be judged from this run (too few good frames). Stacks of {n} "
                f"frames ({span} each){tail}.{cap_note}", False)
    goal_needs = goal_ppt and (sug["n_goal"] or 1) > 1
    if noise["ratio"] > 1.0 and n > 1:
        return (f"⚠ Comps are weak (comp noise {noise['ratio']:.1f}× the target's per frame). Stack {n} frames "
                f"({span} each) → comps SNR ~{sug['snr_c']:.0f}{tail}?{cap_note}", True)
    if noise["ratio"] > 1.0:
        return (f"⚠ Comps are weak (comp noise {noise['ratio']:.1f}× the target's per frame), but the time "
                f"resolution allows no stacking ({cap['text']}). Brighter comps, or longer exposures, would help.",
                True)
    if goal_needs and n > 1:
        return (f"⚠ Per-frame points are about {noise['sig_point'] / 1.0857362e-3:.1f} ppt; your {goal_ppt:g} ppt goal "
                f"needs stacks of {n} frames ({span} each){tail}.{cap_note}", True)
    return (f"Comps are fine per frame (comp noise {noise['ratio']:.0%} of the target's): stacking is not needed. "
            f"Type N to stack anyway.", False)


# ---- groups --------------------------------------------------------------------------------------------------
def plan_groups(records, n: int, cadence_s: float | None = None) -> list[dict]:
    """Consecutive kept frames in groups of n: a new group at every pier side change (flip or rotator move), every
    gap longer than GAP_CADENCES frame spacings, and every n frames. The last group of each run may be partial.
    Returns [{"members": [records], "partial": bool}]."""
    n = max(1, int(n))
    kept = sorted((r for r in records if r["keep"]), key=lambda r: r["jd"])
    if cadence_s is None:
        cadence_s = cadence_seconds(records)
    max_gap = GAP_CADENCES * cadence_s if cadence_s and math.isfinite(cadence_s) else float("inf")
    groups, cur = [], []

    def close():
        if cur:
            groups.append({"members": list(cur), "partial": len(cur) < n})
            cur.clear()

    for r in kept:
        if cur:
            prev = cur[-1]
            side_change = (r["segment"], r["flipped"]) != (prev["segment"], prev["flipped"])
            gap = (r["jd"] - prev["jd"]) * 86400.0 > max_gap
            if side_change or gap:
                close()
        cur.append(r)
        if len(cur) >= n:
            close()
    close()
    return groups


def group_shifts(members, scale: int = 1) -> tuple[list, int, float]:
    """Whole-pixel shifts (dy, dx) in the STORED image's pixels that put each member on the group's middle frame
    (the anchor). The drift is the registration's (dx, dy) in measured-image pixels; scale = 2 on a color camera
    (one measured pixel is a 2x2 Bayer cell, so the Bayer pattern is kept). Returns (shifts, anchor index, the
    largest field turn inside the group in degrees)."""
    a = len(members) // 2
    ax, ay = members[a]["dx"], members[a]["dy"]
    out = []
    for m in members:
        sx = int(round(ax - m["dx"])) * scale
        sy = int(round(ay - m["dy"])) * scale
        out.append((sy, sx))
    angles = [m["angle"] for m in members]
    turn = float(max(angles) - min(angles)) if angles else 0.0
    return out, a, turn


def _shifted(img: np.ndarray, sy: int, sx: int) -> np.ndarray:
    """img moved by (sy, sx) whole pixels; uncovered pixels NaN. Values are moved, never resampled."""
    h, w = img.shape[-2:]
    out = np.full(img.shape, np.nan, dtype=np.float32)
    if abs(sy) >= h or abs(sx) >= w:
        return out
    ys, yd = (slice(0, h - sy), slice(sy, h)) if sy >= 0 else (slice(-sy, h), slice(0, h + sy))
    xs, xd = (slice(0, w - sx), slice(sx, w)) if sx >= 0 else (slice(-sx, w), slice(0, w + sx))
    out[..., yd, xd] = img[..., ys, xs]
    return out


_TRIM_CORR: dict = {}


def trimmed_var_factor(n: int) -> float:
    """E[variance of the n - 2 values left after dropping the lowest and highest] / sigma^2 for normal noise (the
    variance taken about their own mean with divisor n - 2). Monte Carlo, fixed seed, cached."""
    n = int(n)
    if n < 3:
        return 1.0
    if n in _TRIM_CORR:
        return _TRIM_CORR[n]
    rng = np.random.default_rng(54)
    reps = max(400, min(20000, 2_000_000 // n))
    x = np.sort(rng.standard_normal((reps, n)), axis=1)[:, 1:-1]
    v = float(np.mean(np.var(x, axis=1)))
    _TRIM_CORR[n] = v
    return v


def combine(loader, members, shifts, clip_sigma: float = CLIP_SIGMA, min_clip: int = MIN_CLIP_FRAMES,
            cancel=None, progress=None) -> tuple[np.ndarray, dict]:
    """The stack of `members` on the scale of their SUM (clipped mean x n), float32.

    loader(path) -> the calibrated, binned (not debayered) frame. Frames are read one at a time (twice when
    clipping), never all held: memory is a few full-size accumulators.
    n >= min_clip: per pixel, the lowest and highest values are left out for the reference level and scatter
    (so one cosmic ray, satellite trail or hot pixel cannot hide itself), the scatter is corrected for that trim,
    then every value farther than clip_sigma x scatter from the reference is thrown out and the rest are averaged.
    The clip is symmetric about an unbiased level, so real noise is not biased. Below min_clip: a plain mean.
    Pixels a shift left uncovered in some frames are the mean of the frames that cover them. Never median, never
    weighted or normalized.
    Returns (image, {"n", "clipped" (fraction of values thrown out), "plain" (bool)})."""
    n = len(members)
    if n == 0:
        raise ValueError("no frames to stack")
    S = Q = mx = mn = cnt = None
    for k, (m, (sy, sx)) in enumerate(zip(members, shifts)):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Canceled")
        img = np.asarray(loader(m["path"]), dtype=np.float32)
        x = _shifted(img, sy, sx) if (sy or sx) else img.astype(np.float32, copy=True)
        del img
        if S is None:
            S = np.zeros(x.shape, dtype=np.float64)
            Q = np.zeros(x.shape, dtype=np.float64) if n >= min_clip else None
            mx = np.full(x.shape, -np.inf, dtype=np.float32) if n >= min_clip else None
            mn = np.full(x.shape, np.inf, dtype=np.float32) if n >= min_clip else None
            cnt = np.zeros(x.shape, dtype=np.int32)
        elif x.shape != S.shape:
            raise ValueError(f"{os.path.basename(m['path'])} has another size than the other frames")
        ok = np.isfinite(x)
        x0 = np.where(ok, x, 0.0)
        S += x0
        cnt += ok
        if Q is not None:
            Q += x0.astype(np.float64) ** 2
            np.fmax(mx, np.where(ok, x, -np.inf), out=mx)
            np.fmin(mn, np.where(ok, x, np.inf), out=mn)
        del x, x0, ok
        if progress is not None:
            progress(k + 1, 2 * n if n >= min_clip else n)
    with np.errstate(invalid="ignore", divide="ignore"):
        plain = np.where(cnt > 0, S / np.maximum(cnt, 1), np.nan)
    if n < min_clip:
        return (plain * n).astype(np.float32), {"n": n, "clipped": 0.0, "plain": True}
    # Reference level and scatter with each pixel's lowest and highest value left out.
    m2 = np.maximum(cnt - 2, 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        ref = (S - mx - mn) / m2
        var = (Q - mx.astype(np.float64) ** 2 - mn.astype(np.float64) ** 2) / m2 - ref ** 2
    del Q, S
    corr = np.ones(cnt.shape, dtype=np.float64)
    for c in np.unique(cnt):
        if c >= min_clip:
            corr[cnt == c] = trimmed_var_factor(int(c))
    sig = np.sqrt(np.maximum(var, 0.0) / corr).astype(np.float32)
    del var, corr, mx, mn
    ref = ref.astype(np.float32)
    clippable = cnt >= min_clip
    # A scatter taken from a handful of values is itself uncertain (from 3 values, often half the truth), and a clip on
    # an underestimate throws out good values, adding noise. So: (1) no pixel's scatter is taken below the frame's
    # typical one (the median over all pixels: the sky's noise), and (2) the clip widens for few frames, by the
    # relative uncertainty of a scatter from n - 2 values (k = clip_sigma (1 + 1.5 / sqrt(n - 2))): 3 sigma at
    # n -> large, ~5.6 at n = 5, ~4.6 at n = 10. Cosmic rays, trails and hot pixels sit tens to thousands of sigma
    # off, so they are still thrown out.
    floor = float(np.nanmedian(sig[clippable])) if np.any(clippable) else 0.0
    if math.isfinite(floor) and floor > 0:
        np.maximum(sig, np.float32(floor), out=sig)
    kfac = np.ones(cnt.shape, dtype=np.float32) * np.float32(clip_sigma)
    for c in np.unique(cnt):
        if c >= min_clip:
            kfac[cnt == c] = clip_sigma * (1.0 + 1.5 / math.sqrt(c - 2))
    # (3) Starlight is never clipped for changing with the seeing or the sky's transparency: a value may differ from
    # the pixel's reference by as much as the pixel's own signal above the frame's sky level (median over the frame),
    # i.e. between no signal and twice the signal, before the sigma clip applies. On the sky (no signal) that adds
    # nothing, so cosmic rays, trails and hot pixels there go; on a star the clip catches only hits brighter than the
    # star itself. (Measured on synthetic stars whose seeing changes by 2x within a stack: without this the clip added
    # noise comparable to the photon noise; with it, under 1/10 of it.)
    sky_level = float(np.nanmedian(ref[clippable])) if np.any(clippable) else 0.0
    signal = np.abs(ref - np.float32(sky_level)) if math.isfinite(sky_level) else np.zeros_like(ref)
    # A pixel whose values are all equal has no scatter: only exactly-equal values are kept there.
    tol = (kfac * sig + signal + 1e-6 * np.abs(ref)).astype(np.float32)
    del kfac, signal
    K = np.zeros(cnt.shape, dtype=np.float64)
    KC = np.zeros(cnt.shape, dtype=np.int32)
    thrown = 0
    for k, (m, (sy, sx)) in enumerate(zip(members, shifts)):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Canceled")
        img = np.asarray(loader(m["path"]), dtype=np.float32)
        x = _shifted(img, sy, sx) if (sy or sx) else img
        del img
        ok = np.isfinite(x)
        with np.errstate(invalid="ignore"):
            keep = ok & (~clippable | (np.abs(x - ref) <= tol))
        thrown += int(np.count_nonzero(ok & ~keep))
        K += np.where(keep, x, 0.0)
        KC += keep
        del x, ok, keep
        if progress is not None:
            progress(n + k + 1, 2 * n)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(KC > 0, K / np.maximum(KC, 1), plain)
    total = int(cnt.sum())
    return (out * n).astype(np.float32), {"n": n, "clipped": thrown / total if total else 0.0, "plain": False}


# ---- files ---------------------------------------------------------------------------------------------------
def stack_folder(key_parts) -> str:
    """A folder in the user's SHOBS-P cache for one set of stacks (never in the Lights folder, which is listed as
    lights). Older stack sets are removed when a new one is made (prune_folders)."""
    key = hashlib.sha1(json.dumps(key_parts, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return os.path.join(STACK_CACHE_DIR, f"set_{key}")


def prune_folders(keep: str) -> int:
    """Remove every stack set but `keep` (stacks are large: one full frame each)."""
    n = 0
    try:
        names = os.listdir(STACK_CACHE_DIR)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(STACK_CACHE_DIR, name)
        if os.path.abspath(path) == os.path.abspath(keep) or not name.startswith("set_"):
            continue
        try:
            shutil.rmtree(path)
            n += 1
        except OSError:
            pass
    return n


_SKIP_CARDS = {"SIMPLE", "BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "NAXIS3", "EXTEND", "BZERO", "BSCALE", "COMMENT",
               "HISTORY", "", "CHECKSUM", "DATASUM", "END"}


def stack_header(anchor_header: dict, members, info: dict, mid_jds, number: int, of: int, partial: bool,
                 frame_labels=None) -> dict:
    """The stack's header: the middle frame's own cards (camera, gain, filter, pier side, ...) with the exposure,
    mid-time and the stack's record replaced."""
    h = {k: v for k, v in dict(anchor_header or {}).items()
         if str(k).upper() not in _SKIP_CARDS and isinstance(v, (str, int, float, bool))}
    exps = [float(m["exp"]) for m in members]
    h["EXPTIME"] = float(sum(exps))
    h["EXPOSURE"] = float(sum(exps))
    h["JD-MID"] = float(np.mean(mid_jds))
    for key in ("MIDJD", "JD", "MJD-OBS", "MJD-AVG", "JD-OBS", "JD_UTC", "DATE-END", "DATE-AVG"):
        h.pop(key, None)
    h["STACKN"] = int(len(members))
    h["STACKEXP"] = float(np.median(exps))
    h["STACKNUM"] = int(number)
    h["STACKOF"] = int(of)
    h["STACKPRT"] = bool(partial)
    h["STACKCLP"] = 0.0 if info.get("plain") else float(CLIP_SIGMA)
    h["STACKREJ"] = float(round(info.get("clipped", 0.0), 6))
    h["STACKSCL"] = "SUM"
    h["SHOBSSTK"] = int(STACK_VERSION)
    if frame_labels:
        h["STACKF1"] = str(frame_labels[0])[:60]
        h["STACKF2"] = str(frame_labels[-1])[:60]
    return h


def write_stack(path: str, img: np.ndarray, header: dict) -> None:
    if fits is None:
        raise RuntimeError("astropy is required to write stacks")
    hdr = fits.Header()
    for k, v in header.items():
        key = str(k).upper()
        if len(key) > 8 or not all(ch.isalnum() or ch in "-_" for ch in key):
            continue
        if isinstance(v, str):
            v = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in v)[:68]
        try:
            hdr[key] = v
        except Exception:
            continue
    hdr["COMMENT"] = "SHOBS-P stack: sigma-clipped mean x N of calibrated frames (sum scale), whole-pixel shifts"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    fits.PrimaryHDU(data=np.asarray(img, dtype=np.float32), header=hdr).writeto(tmp, overwrite=True)
    os.replace(tmp, path)


def read_stack(path: str) -> tuple[np.ndarray, dict]:
    """(the stack, as calibrated and binned like a frame before its debayer; header dict)."""
    with fits.open(path, memmap=False) as hdul:
        return np.asarray(hdul[0].data, dtype=np.float32), dict(hdul[0].header)


def is_stack_header(header: dict | None) -> bool:
    return bool((header or {}).get("SHOBSSTK"))


# ---- after photometry on the stacks --------------------------------------------------------------------------
def apply_member_peaks(obs, members) -> None:
    """The stack's raw peaks are its frames' (the stack is no raw frame): the highest of each star's."""
    peaks = [m["raw_peak"] for m in members if math.isfinite(m["raw_peak"])]
    obs.raw_peak = float(max(peaks)) if peaks else float("nan")
    merged = {}
    for m in members:
        for name, v in (m.get("raw_peaks") or {}).items():
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(v):
                merged[name] = max(merged.get(name, -math.inf), v)
    obs.raw_peaks = merged
    segs = {m["segment"] for m in members}
    if len(segs) == 1:
        obs.segment = next(iter(segs))


def summary(groups, n: int) -> dict:
    sizes = [len(g["members"]) for g in groups]
    exps = [sum(m["exp"] for m in g["members"]) for g in groups if len(g["members"]) == n] or \
           [sum(m["exp"] for m in g["members"]) for g in groups]
    one = [m["exp"] for g in groups for m in g["members"] if m["exp"] > 0]
    return {"n": int(n), "stacks": len(groups), "frames": int(sum(sizes)), "partial": sum(1 for s in sizes if s < n),
            "smallest": int(min(sizes)) if sizes else 0,
            "exp": float(np.median(exps)) if exps else float("nan"),
            "frame_exp": float(np.median(one)) if one else float("nan")}


def stack_words(meta: dict | None) -> str:
    """'stacks of 40 frames (effective exposure 30 s)' for the Results line, settings and reports."""
    if not meta or int(meta.get("n") or 1) <= 1:
        return ""
    exp = meta.get("exp")
    text = f"stacks of {int(meta['n'])} frames"
    if exp and math.isfinite(float(exp)):
        text += f" (effective exposure {float(exp):g} s)"
    if meta.get("partial"):
        text += f"; {int(meta['partial'])} partial (smallest {int(meta.get('smallest') or 0)} frames)"
    return text


def report_note(meta: dict | None) -> str:
    """The AAVSO report's NOTES part (ASCII, no commas or bars): 'points are stacks of N frames (effective exposure
    X s)'."""
    if not meta or int(meta.get("n") or 1) <= 1:
        return ""
    exp = meta.get("exp")
    text = f"points are stacks of {int(meta['n'])} frames"
    if exp and math.isfinite(float(exp)):
        text += f" (effective exposure {float(exp):g} s)"
    return text
