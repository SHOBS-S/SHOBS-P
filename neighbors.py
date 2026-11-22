"""2.4.0 step 7c (#92): measure a star on frames already on disk (Watch reach-back, look-alikes and close companions
for the Check neighbors window, and the blend test), headless and testable.

Design: Claude Doc "SHOBS-P: Suspect -> Watch -> Candidate -> Campaign" (15 Nov 2026) and the Check neighbors window
mockup. John's choices for 7c (22 Nov 2026): reach-back finds each night's own saved masters (the night's Flats
folder, the Input page's bias and dark) and ties the star to that frame's stored comp values; look-alikes measured
on demand live in a sidecar file beside the series (<series>_neighbors.json), so the series and Use comps... are
not touched.

Where a star sits on a frame. Every frame of a series keeps the pixel positions of its target, comps, check, spares
and Watch stars (Obs.pos); the series knows their sky positions. A frame's sky -> pixel map is an affine fit of
those stars' tangent-plane positions to their pixels (6 parameters, one 3-sigma pass), so any star with a sky
position can be placed on any frame of the series, flips and drift included, with no plate solve per frame.

Tied to the comps. The star and the frame's anchor stars (comps, spares, check, target) are measured the same way
on the same calibrated image; the median of (stored instrumental magnitude - this measurement) over the anchors is
added to the star. The star then sits on the frame's own instrumental scale, as if the original run had measured
it, and a different calibration or centring cancels to the anchors' spread (reported per frame).
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone

import numpy as np

SIDECAR_KIND = "SHOBS-P neighbors"
SIDECAR_VERSION = 1
SIDECAR_SUFFIX = "_neighbors.json"
MIN_ANCHORS = 3          # stars with a sky and a pixel position needed for a frame's sky -> pixel map
MIN_TIE = 2              # anchors measured to tie a star to the frame's instrumental scale
MIN_MAP_STARS = 4        # a value is written only when the frame's map rests on this many stars (3 fit exactly)
MAP_RMS_MAX = 2.0        # ... and fits them within this (px)
TIE_SPREAD_MAX = 0.05    # ... and its anchors agree on the tie within this (mag, robust spread)
CLOSE_DMAG_MAX = 3.0     # a close companion fainter than the star by more than this (6 % of its light) is no blend
FIT_CLIP_PX = 2.0        # an anchor further than this (and 3 sigma) from the fit is left out once
LOOKALIKE_MAG = 0.5      # John, 15 Nov: look-alikes within +-0.5 mag until real use says otherwise
LOOKALIKE_MAX = 6
SAME_STAR_ARCSEC = 3.0
BLEND_SHIFT_FRAC = 0.5   # the blend test moves the aperture by half the radius (or half the separation)
BLEND_GROW = 1.15        # amplitude toward the companion this much above the centred one: the signal may be its
BLEND_SIGMA = 2.0        # ... and by more than 2 sigma_A


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- sky <-> pixel on one frame ---------------------------------------------------------------------------------
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


def fit_affine(src, dst):
    """Least-squares affine map src (N, 2) -> dst (N, 2). The worst point is left out, one at a time (at most 3,
    keeping MIN_ANCHORS + 1), while it lies further than max(FIT_CLIP_PX, 4 x the robust spread of the others) from
    the fit: one misplaced star never bends the map. Returns (M (2, 3), rms px, kept mask) or None with fewer than
    MIN_ANCHORS points."""
    src = np.asarray(src, float).reshape(-1, 2)
    dst = np.asarray(dst, float).reshape(-1, 2)
    ok = np.all(np.isfinite(src), axis=1) & np.all(np.isfinite(dst), axis=1)
    if ok.sum() < MIN_ANCHORS:
        return None
    keep = ok.copy()

    def solve(mask):
        A = np.column_stack([src[mask], np.ones(int(mask.sum()))])
        sol, *_ = np.linalg.lstsq(A, dst[mask], rcond=None)
        M = sol.T
        pred = np.column_stack([src, np.ones(len(src))]) @ M.T
        return M, np.hypot(*(pred - dst).T)
    M, res = solve(keep)
    for _ in range(3):
        if keep.sum() <= MIN_ANCHORS + 1:
            break
        idx = np.flatnonzero(keep)
        worst = idx[int(np.argmax(res[idx]))]
        trial = keep.copy()
        trial[worst] = False
        M2, res2 = solve(trial)
        others = res2[trial]
        spread = 1.4826 * float(np.median(others)) if others.size else 0.0
        if res2[worst] > max(FIT_CLIP_PX, 4.0 * spread):
            keep, M, res = trial, M2, res2
        else:
            break
    rms = float(np.sqrt(np.mean(res[keep] ** 2)))
    return M, rms, keep


def apply_affine(M, pts):
    pts = np.atleast_2d(np.asarray(pts, float))
    return np.column_stack([pts, np.ones(len(pts))]) @ np.asarray(M).T


def frame_points(o, comp_names, target_name: str, check_name: str) -> dict:
    """{name: (x, y)} of every star this frame recorded: target ("t"), comps by the series' layout ("c"), check
    ("k"), spares ("s") and Watch stars ("w")."""
    pos = getattr(o, "pos", None) or {}
    out = {}

    def put(name, p):
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            return
        if name and math.isfinite(x) and math.isfinite(y):
            out[name] = (x, y)
    put(target_name, pos.get("t"))
    for k, p in enumerate(pos.get("c") or []):
        if k < len(comp_names or []):
            put(comp_names[k], p)
    if check_name:
        put(check_name, pos.get("k"))
    for key in ("s", "w"):
        for n, p in (pos.get(key) or {}).items() if isinstance(pos.get(key), dict) else ():
            put(n, p)
    return out


def stored_inst(o, name: str, comp_names, target_name: str, check_name: str) -> float:
    """The frame's stored instrumental magnitude of one of its own stars (NaN when it has none)."""
    try:
        if name == target_name:
            return float(o.target_inst)
        if name in (comp_names or []):
            k = list(comp_names).index(name)
            return float(o.comp_insts[k]) if k < len(o.comp_insts) and o.comp_insts[k] is not None else float("nan")
        if check_name and name == check_name:
            return float(o.kmag) if o.kmag is not None else float("nan")
        for store in (getattr(o, "spare_insts", None) or {}, getattr(o, "watch_insts", None) or {}):
            if name in store:
                return float(store[name])
    except (TypeError, ValueError, IndexError):
        pass
    return float("nan")


class FrameMap:
    """One frame's sky -> pixel map, built from the stars it recorded that have sky positions."""

    def __init__(self, o, sky: dict, comp_names, target_name: str, check_name: str, center=None, exclude=()):
        self.points = {n: p for n, p in frame_points(o, comp_names, target_name, check_name).items()
                       if n not in set(exclude or ())}
        names = [n for n in self.points if n in sky and sky[n] is not None]
        self.names = names
        self.center = center or (tuple(sky[names[0]]) if names else (0.0, 0.0))
        self.M, self.rms, self.used = None, float("nan"), []
        if len(names) >= MIN_ANCHORS:
            xi, eta = tangent([sky[n][0] for n in names], [sky[n][1] for n in names], *self.center)
            fit = fit_affine(np.column_stack([xi, eta]), [self.points[n] for n in names])
            if fit is not None:
                self.M, self.rms, keep = fit
                self.used = [n for n, k in zip(names, keep) if k]

    @property
    def ok(self) -> bool:
        return self.M is not None

    def xy(self, ra: float, dec: float):
        if self.M is None:
            return float("nan"), float("nan")
        xi, eta = tangent(ra, dec, *self.center)
        p = apply_affine(self.M, [[float(xi), float(eta)]])[0]
        return float(p[0]), float(p[1])

    def scale_arcsec_per_px(self) -> float:
        if self.M is None:
            return float("nan")
        det = abs(float(np.linalg.det(np.asarray(self.M)[:, :2])))
        return 1.0 / math.sqrt(det) if det > 0 else float("nan")


# ---- measuring --------------------------------------------------------------------------------------------------
def _measure(core, img, x, y, radius, sky_in, sky_out, exptime, recentre=True):
    """(inst mag, x, y, why-not) for one aperture; recentred within reduce_frame's jump limit when asked."""
    if recentre:
        try:
            x2, y2, moved = core.recenter(img, x, y, radius)
            if moved <= core.recenter_jump_limit(radius):
                x, y = x2, y2
        except Exception:
            pass
    why = core.aperture_clip_reason(img.shape, x, y, radius, sky_in, sky_out)
    if why:
        return float("nan"), x, y, why
    try:
        m = core.measure_aperture(img, x, y, radius, sky_in, sky_out)
    except Exception as exc:
        return float("nan"), x, y, str(exc)
    return core.inst_mag(m["flux"], exptime), x, y, ""


def measure_frame(core, img, raw, o, fmap: FrameMap, stars: list[dict], *, comp_names, target_name: str,
                  check_name: str, radius: float, sky_in: float, sky_out: float, exptime: float,
                  total_bin: int = 1) -> dict:
    """Measure `stars` ({"key", "ra", "dec", optional "toward": (ra, dec), "sign": +1/-1}) on one calibrated,
    debayered frame, tied to the frame's own stored values. Returns {"offset", "spread", "n_tie", "values": {key:
    {"inst", "x", "y", "peak", "why"}}}."""
    diffs = []
    for name in fmap.points:
        st = stored_inst(o, name, comp_names, target_name, check_name)
        if not math.isfinite(st):
            continue
        x, y = fmap.points[name]
        m, *_ = _measure(core, img, x, y, radius, sky_in, sky_out, exptime)
        if math.isfinite(m):
            diffs.append(st - m)
    out = {"offset": float("nan"), "spread": float("nan"), "n_tie": len(diffs), "values": {}}
    if len(diffs) >= MIN_TIE:
        d = np.asarray(diffs)
        out["offset"] = float(np.median(d))
        out["spread"] = float(1.4826 * np.median(np.abs(d - np.median(d)))) if d.size > 2 else float(np.std(d))
    poor = ""
    if not fmap.ok:
        poor = "frame could not be mapped"
    elif len(fmap.used) < MIN_MAP_STARS:
        poor = f"only {len(fmap.used)} stars to map the frame"
    elif fmap.rms > MAP_RMS_MAX:
        poor = f"the frame's stars do not fit one map ({fmap.rms:.1f} px)"
    elif math.isfinite(out["spread"]) and out["spread"] > TIE_SPREAD_MAX:
        poor = f"the comps disagree on the tie ({out['spread']:.3f} mag)"
    out["poor"] = poor
    for st in stars:
        key = st["key"]
        if poor:
            out["values"][key] = {"inst": float("nan"), "why": poor}
            continue
        x, y = fmap.xy(st["ra"], st["dec"])
        m, x, y, why = _measure(core, img, x, y, radius, sky_in, sky_out, exptime)
        if st.get("toward") is not None and math.isfinite(m):
            cx, cy = fmap.xy(*st["toward"])
            dx, dy = cx - x, cy - y
            dist = math.hypot(dx, dy)
            if dist > 0:
                step = min(BLEND_SHIFT_FRAC * radius, 0.5 * dist) * float(st.get("sign", 1))
                x, y = x + dx / dist * step, y + dy / dist * step
                m, x, y, why = _measure(core, img, x, y, radius, sky_in, sky_out, exptime, recentre=False)
        val = {"inst": m + out["offset"] if math.isfinite(m) and math.isfinite(out["offset"]) else float("nan"),
               "x": round(float(x), 1), "y": round(float(y), 1), "why": why or ("" if math.isfinite(out["offset"])
                                                                                    else "too few comps to tie to")}
        if raw is not None and getattr(raw, "size", 0):
            try:
                val["peak"] = core.raw_peak_near(raw, core.Star(name=key, x=x, y=y), radius, total_bin)
            except Exception:
                val["peak"] = float("nan")
        out["values"][key] = val
    return out


def series_sky(meta_sky: dict, target_name: str, target_radec) -> dict:
    """{name: (ra, dec)} for the stars a series knows the sky position of, plus the target."""
    out = {k: (float(v[0]), float(v[1])) for k, v in (meta_sky or {}).items()
           if v is not None and len(v) >= 2 and v[0] is not None and v[1] is not None}
    if target_name and target_radec and target_radec[0] is not None and target_radec[1] is not None:
        out[target_name] = (float(target_radec[0]), float(target_radec[1]))
    return out


def measure_series(core, observations, stars: list[dict], *, sky: dict, comp_names, target_name: str,
                   check_name: str, load, aperture_for, total_bin: int = 1, indices=None, progress=None,
                   cancel=None, exclude=()) -> dict:
    """Measure stars on the series' frames. load(path) -> (raw, header, calibrated debayered image) or raises;
    aperture_for(obs) -> (radius, sky_in, sky_out). exclude: names never used as anchors (the stars being
    measured, so a star is never tied to its own stored value). indices: the observation indices to visit (default all), or
    {star key: set of indices} to visit only those per star. Returns {"values": {key: {index: {...}}}, "frames": n
    measured, "failed": [(path, why)], "ties": {index: (offset, spread, n_tie)}, "map_rms": [...]}."""
    want = {}
    if isinstance(indices, dict):
        for st in stars:
            for i in indices.get(st["key"], ()):
                want.setdefault(int(i), []).append(st)
    else:
        idx = range(len(observations)) if indices is None else indices
        for i in idx:
            want[int(i)] = list(stars)
    res = {"values": {st["key"]: {} for st in stars}, "frames": 0, "failed": [], "ties": {}, "map_rms": []}
    center = None
    if sky:
        center = tuple(next(iter(sky.values())))   # any star of the field (a mean RA breaks at 0/360 deg)
    order = sorted(want)
    for n_done, i in enumerate(order, 1):
        if cancel is not None and cancel.is_set():
            res["cancelled"] = True
            break
        o = observations[i]
        if progress is not None:
            try:
                progress(n_done, len(order), o)
            except Exception:
                pass
        fmap = FrameMap(o, sky, comp_names, target_name, check_name, center,
                        exclude=set(exclude or ()) | {st.get("name") or st["key"] for st in stars})
        if not fmap.ok:
            res["failed"].append((o.path, "too few stars with sky positions on this frame"))
            continue
        try:
            raw, header, img = load(o.path)
        except Exception as exc:
            res["failed"].append((o.path, f"{type(exc).__name__}: {exc}"))
            continue
        try:
            r, si, so = aperture_for(o)
            exptime = float(core.header_exptime(header)) if header is not None else float(o.exptime)
            fr = measure_frame(core, img, raw, o, fmap, want[i], comp_names=comp_names, target_name=target_name,
                               check_name=check_name, radius=r, sky_in=si, sky_out=so, exptime=exptime,
                               total_bin=total_bin(o) if callable(total_bin) else total_bin)
        except Exception as exc:
            res["failed"].append((o.path, f"{type(exc).__name__}: {exc}"))
            continue
        if fr.get("poor"):
            res["failed"].append((o.path, fr["poor"]))
            continue
        res["frames"] += 1
        res["ties"][i] = (fr["offset"], fr["spread"], fr["n_tie"])
        res["map_rms"].append(fmap.rms)
        for key, v in fr["values"].items():
            res["values"][key][i] = v
    return res


def consistency(observations, values: dict, name: str) -> dict:
    """Reach-back's own check: on frames that already had the Watch star, this measurement minus the stored one.
    {"n", "median", "rms"} in mag (n 0 when nothing to compare)."""
    d = []
    for i, v in values.items():
        st = (getattr(observations[i], "watch_insts", None) or {}).get(name)
        if st is not None and math.isfinite(float(st)) and math.isfinite(v.get("inst", float("nan"))):
            d.append(v["inst"] - float(st))
    if not d:
        return {"n": 0, "median": None, "rms": None}
    a = np.asarray(d)
    return {"n": int(a.size), "median": float(np.median(a)), "rms": float(np.std(a, ddof=1)) if a.size > 1 else 0.0}


def missing_frames(observations, name: str) -> list[int]:
    """The series' frames without a value for this Watch star (what reach-back fills)."""
    return [i for i, o in enumerate(observations)
            if not math.isfinite(float((getattr(o, "watch_insts", None) or {}).get(name, float("nan"))))]


def sample_measured(observations, name: str, per_night: int = 4) -> list[int]:
    """A few frames per night that already have the star, for the consistency check."""
    by = {}
    for i, o in enumerate(observations):
        v = (getattr(o, "watch_insts", None) or {}).get(name)
        if v is not None and math.isfinite(float(v)):
            by.setdefault(o.night, []).append(i)
    out = []
    for _n, ix in by.items():
        if len(ix) <= per_night:
            out += ix
        else:
            step = (len(ix) - 1) / (per_night - 1)
            out += [ix[int(round(k * step))] for k in range(per_night)]
    return sorted(set(out))


def fill_watch(observations, name: str, values: dict, overwrite: bool = False) -> int:
    """Write reach-back values into the frames: watch_insts, pos["w"], raw_peaks. Returns frames filled."""
    n = 0
    for i, v in values.items():
        inst = v.get("inst", float("nan"))
        if not math.isfinite(inst):
            continue
        o = observations[i]
        if not overwrite and math.isfinite(float((o.watch_insts or {}).get(name, float("nan")))):
            continue
        o.watch_insts[name] = float(inst)
        pos = o.pos if isinstance(o.pos, dict) else {}
        w = pos.get("w") if isinstance(pos.get("w"), dict) else {}
        w[name] = [v.get("x"), v.get("y")]
        pos["w"] = w
        o.pos = pos
        pk = v.get("peak")
        if pk is not None and math.isfinite(float(pk)) and getattr(o, "raw_peaks", None) is not None:
            if overwrite:
                o.raw_peaks[name] = float(pk)
            else:
                o.raw_peaks.setdefault(name, float(pk))
        n += 1
    return n


# ---- light curves from measured values --------------------------------------------------------------------------
def curve_from_values(promotion, observations, name: str, values: dict, role: str = "neighbor"):
    """A promotion.Curve from per-frame instrumental values, put on the catalog scale like a Watch star (comp
    ensemble's catalog mean + star - ensemble), cloud / drift frames left out as for Watch stars."""
    rows = []
    for i, v in sorted(values.items()):
        o = observations[i]
        inst = v.get("inst") if isinstance(v, dict) else v
        try:
            inst = float(inst)
            if not (math.isfinite(inst) and math.isfinite(float(o.cmag)) and math.isfinite(float(o.comp_cat))):
                continue
        except (TypeError, ValueError):
            continue
        if not promotion._usable(o, need_comp_cat=True):
            continue
        rows.append((o.jd, float(o.comp_cat) + inst - float(o.cmag), o.night, o.segment,
                     o.airmass if o.airmass is not None else float("nan")))
    if not rows:
        return promotion.make_curve(name, [], [], [], [], role=role)
    c = list(zip(*rows))
    return promotion.make_curve(name, c[0], c[1], c[2], c[3], airmass=c[4], role=role)


# ---- picking the neighbors ----------------------------------------------------------------------------------------
def pick_neighbors(star_ra: float, star_dec: float, star_mag, catalog: list[dict], known: dict, *,
                   window: float = LOOKALIKE_MAG, n_max: int = LOOKALIKE_MAX, close_arcsec: float = 0.0,
                   field_arcsec: float | None = None, keep=None, star_amp: float | None = None) -> dict:
    """Look-alikes (catalog stars within +-window mag of the star, nearest first, at most n_max) and close
    companions (any catalog star within close_arcsec: the aperture plus sky ring), leaving out the star itself and
    the stars the series already measures (known = {name: (ra, dec)}). A close companion is kept when it is bright
    enough to carry the star's amplitude (star_amp, mag): no more than max(CLOSE_DMAG_MAX, -2.5 log10(star_amp))
    fainter (Fable N3: a 4 mag fainter star with deep eclipses can put 10 mmag on the star); the ones left out are
    counted in "close_faint"; keep(pick) -> False leaves a star out (before the n_max cut). Each pick: {"name", "ra", "dec", "mag",
    "sep", "dmag"}."""
    looks, close = [], []
    dmax = CLOSE_DMAG_MAX
    if star_amp and star_amp > 0:
        dmax = max(CLOSE_DMAG_MAX, -2.5 * math.log10(float(star_amp)))
    faint = 0
    for st in catalog or []:
        ra, dec = st.get("ra"), st.get("dec")
        if ra is None or dec is None:
            continue
        sep = sep_arcsec(star_ra, star_dec, ra, dec)
        if sep < SAME_STAR_ARCSEC:
            continue
        if any(v and sep_arcsec(v[0], v[1], ra, dec) < SAME_STAR_ARCSEC for v in known.values()):
            continue
        if field_arcsec is not None and sep > field_arcsec:
            continue
        mag = st.get("mag")
        aka = st.get("aka") or []
        name = str(st.get("auid") or st.get("name") or (aka[0] if aka else "") or f"{float(ra):.5f} {float(dec):+.5f}")
        pick = {"name": name, "ra": float(ra), "dec": float(dec), "mag": mag, "sep": sep,
                "dmag": (float(mag) - float(star_mag)) if mag is not None and star_mag is not None else None}
        if keep is not None and not keep(pick):   # e.g. not on the series' frames
            continue
        if close_arcsec and sep <= close_arcsec:
            if pick["dmag"] is None or pick["dmag"] <= dmax:
                close.append(pick)
            else:
                faint += 1
        elif pick["dmag"] is not None and abs(pick["dmag"]) <= window:
            looks.append(pick)
    looks.sort(key=lambda p: p["sep"])
    close.sort(key=lambda p: p["sep"])
    return {"lookalikes": looks[:n_max], "close": close, "close_faint": faint, "close_dmag_max": dmax}


# ---- the blend test -----------------------------------------------------------------------------------------------
def blend_verdict(promotion, centred, toward, away, period: float) -> dict:
    """The three apertures' amplitudes at P (centred, moved toward the companion, moved away). The true variable's
    amplitude grows as the aperture moves onto it: toward > centred (by BLEND_GROW and BLEND_SIGMA) says the signal
    may be the companion's; toward <= centred and away >= toward says it is the star's."""
    amps = {}
    for key, c in (("centred", centred), ("toward", toward), ("away", away)):
        if c is None or c.jd.size < 3 * promotion.MIN_NIGHT_POINTS:
            amps[key] = (float("nan"), float("nan"))
            continue
        y = promotion.remove_medians(c.mag, c.night)
        a, _ph, r, _ = promotion.sine_fit(c.jd, y, period)
        s = float(np.std(r, ddof=3)) * math.sqrt(2.0 / c.jd.size)
        amps[key] = (float(a), s)
    a0, s0 = amps["centred"]
    at, st = amps["toward"]
    aw, _sw = amps["away"]
    out = {"amps": {k: (round(v[0], 5) if math.isfinite(v[0]) else None,
                        round(v[1], 6) if math.isfinite(v[1]) else None) for k, v in amps.items()}}
    if not (math.isfinite(a0) and math.isfinite(at)):
        out.update({"verdict": None, "text": "Blend test: too few points measured to judge."})
        return out
    s = math.hypot(s0, st) if math.isfinite(st) else s0
    grows = at > BLEND_GROW * a0 and (at - a0) > BLEND_SIGMA * s
    if grows:
        out.update({"verdict": "companion",
                    "text": (f"Blend test: the amplitude grows toward the companion ({a0 * 1000:.1f} → {at * 1000:.1f} "
                             "mmag): the signal may belong to the companion.")})
    else:
        out.update({"verdict": "star",
                    "text": (f"Blend test: the amplitude does not grow toward the companion ({a0 * 1000:.1f} → "
                             f"{at * 1000:.1f} mmag; away {aw * 1000:.1f}): the signal sits on the star."
                             if math.isfinite(aw) else
                             f"Blend test: the amplitude does not grow toward the companion ({a0 * 1000:.1f} → "
                             f"{at * 1000:.1f} mmag): the signal sits on the star.")})
    return out


# ---- the sidecar -------------------------------------------------------------------------------------------------
def sidecar_path(series_path: str) -> str:
    stem = os.path.splitext(series_path or "")[0]
    return stem + SIDECAR_SUFFIX if stem else ""


def sidecar_load(series_path: str) -> dict:
    path = sidecar_path(series_path)
    empty = {"kind": SIDECAR_KIND, "version": SIDECAR_VERSION, "stars": {}, "checks": {}}
    if not path or not os.path.isfile(path):
        return empty
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return empty
    if not isinstance(doc, dict) or doc.get("kind") != SIDECAR_KIND:
        return empty
    doc.setdefault("stars", {})
    doc.setdefault("checks", {})
    return doc


def _clean(v):
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, (np.floating,)):
        return _clean(float(v))
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


def sidecar_save(series_path: str, doc: dict) -> str:
    path = sidecar_path(series_path)
    if not path:
        raise ValueError("the series has not been saved to a file yet")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(_clean(doc), fh, indent=1)
    os.replace(tmp, path)
    return path


def sidecar_put(doc: dict, observations, pick: dict, values: dict, calibration: str = "") -> None:
    """Keep one measured neighbor: its per-frame values keyed by the frame's JD (6 decimals) and file name."""
    pts = []
    for i, v in sorted(values.items()):
        o = observations[i]
        inst = v.get("inst")
        if inst is None or not math.isfinite(float(inst)):
            continue
        pts.append([round(float(o.jd), 6), os.path.basename(str(o.path)), round(float(inst), 5), v.get("x"),
                    v.get("y"), v.get("peak")])
    doc["stars"][pick["name"]] = {"ra": pick["ra"], "dec": pick["dec"], "mag": pick.get("mag"),
                                  "measured_utc": utc_now(), "calibration": calibration, "points": pts}


def sidecar_values(doc: dict, observations, name: str) -> dict:
    """{observation index: {"inst"}} for a kept neighbor, matched to the series' frames by JD and file name."""
    rec = (doc.get("stars") or {}).get(name)
    if not rec:
        return {}
    by = {(round(float(o.jd), 6), os.path.basename(str(o.path))): i for i, o in enumerate(observations)}
    out = {}
    for p in rec.get("points") or []:
        try:
            i = by.get((round(float(p[0]), 6), str(p[1])))
        except (TypeError, ValueError, IndexError):
            continue
        if i is not None and p[2] is not None:
            out[i] = {"inst": float(p[2]), "x": p[3], "y": p[4], "peak": p[5]}
    return out
