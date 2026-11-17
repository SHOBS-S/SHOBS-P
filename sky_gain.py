"""2.4.0 (#104): the camera gain measured from the night's own sky. Idea (background model + source mask, from the
darks' read noise first): Mike Fitzgerald (EXOTIC), 16 Nov 2026. Pure functions, no Tk.

Before 2.4.0 SHOBS-P took g = sky level / sky variance in the stars' sky rings on one frame
(photometry_core.sky_electrons_estimate). Read noise, dark noise, faint stars, gradients and the pixels' own fixed
pattern (no flat) all add variance without adding level, so that was only a LOWER limit (28.7 e- per unit on the
WASP-50 MicroObservatory night). This module measures it instead:

1. Each sampled frame (calibrated the way the run calibrates it, then cropped to a central window for speed) gets a
   smooth 2-D background model (photutils Background2D, a sigma-clipped median mesh about 1/12 of the window's short
   side, at least 16 px) taken off; the stars are found on the smoothed residual (photutils segmentation at 2 sigma)
   and masked with a few pixels to spare, with saturated / non-finite pixels and the window's edge.
2. Two NEIGHBOURING frames are subtracted (each minus its own background, masks OR'd). The sky's photon noise is
   independent in the two, so var(difference) / 2 = sky / g + floor; everything fixed on the sensor (hot pixels,
   pixel-to-pixel sensitivity, the master dark and flat themselves) is the same in both and cancels. That is why no
   extra term is added for the masters' own noise. A pair whose sky levels differ by more than 20 %, or whose frames
   have another size, is skipped.
3. The floor (read noise + dark-current noise at the lights' exposure, in image units) comes from two raw darks of
   the lights' exposure, std(d1 - d2) / sqrt 2, which is exactly what the lights carry; else from two bias frames
   (read noise only: the dark current's noise is left in the gain, so it comes out a little low, the safe side);
   else from the photon-transfer intercept (step 4); else there is none.
4. Per pair g = S / (v - floor); the night's value is the median, with the 16-84 % spread, widened by the floor's own
   uncertainty (Fable review N1; with the photon-transfer floor, by that line's gain error, N5). A pair counts only
   when its sky variance is at least twice the floor (the sky's noise at least the floor's): below that a small error
   in the floor (darks a little warmer than the lights) moves the gain a lot (review N1: 2.3x on an uncooled camera
   with a faint sky). A photon-transfer line v = S / g + c over the pairs (weighted, Janesick 2007) is the
   cross-check; it is reported only when the night's sky range constrains its slope to better than 15 %.
5. With no floor and no usable photon-transfer line, or fewer than 3 usable pairs, there is no measurement: the
   caller falls back to the old single-frame lower limit and says so.

Whiteness guard (Mike Fitzgerald's review of the method): the estimator assumes the masked, background-subtracted
difference is white noise. A passing cloud, reflected moonlight or other structure the background mesh misses adds
variance and makes the gain LOW. Each pair's var(difference) is compared with the variance from neighbouring-pixel
differences (var(d[:, 1:] - d[:, :-1]) / 2, and the same along y; both pixels unmasked): white noise gives 1, smooth
structure more. The smaller of the x and y ratios is used (a camera's readout pattern can push one direction alone:
MicroObservatory darks give x 0.88, y 0.98); a pair over WHITE_MAX is dropped and the count is said.

Stacks (the Stack step's files) are read as they are, never binned or calibrated again (review S1), exactly as the run
reads them; they are on the scale of a sum, so the electrons per image unit are the frames' own.

Units: everything is in SHOBS-P image units (binned, calibrated, debayered), the same units the photometry measures,
so the result plugs into photometry_core.photon_noise_setup as electrons per image unit like the old estimate did.

Variances are sigma-clipped (4 sigma, iterated); a clipped Gaussian's variance is a little low, and CLIP_VAR is that
factor, divided out of both the pair variance and the floor so the gain is not biased high by it."""
from __future__ import annotations

import math

import numpy as np

CLIP_SIGMA = 4.0
MAX_PAIRS = 24              # frame pairs sampled through the night
MIN_PAIRS = 3               # fewer usable pairs: no measurement (the old lower limit is used)
MAX_LEVEL_STEP = 0.20       # a pair whose sky levels differ by more than this is skipped (twilight, clouds)
PTC_MAX_REL_ERR = 0.15      # the photon-transfer slope is reported when its error is under this
PTC_MIN_PAIRS = 5
WINDOW_PX = 360_000         # analysis window per frame (image pixels), central; a MOBS frame is 325 000
EDGE_PX = 4                 # the window's edge is masked
DETECT_SIGMA = 2.0
DILATE_PX = 4
MIN_SKY_OVER_FLOOR = 2.0    # review N1: a pair counts only when its variance is at least this x the floor
WHITE_MAX = 1.10            # Mike Fitzgerald's whiteness guard: a pair whose ratio is over this is dropped
NEAR_SAT_SIGMA = 5.0        # review nit: the sky within this many sigma of the saturation level is masked
METHOD_WORDS = "background model and source mask, idea by Mike Fitzgerald"


def _clip_var_factor(k: float = CLIP_SIGMA) -> float:
    """The variance of a unit Gaussian after iterated k-sigma clipping (the fixed point s^2 = Var(X | |X| < k s))."""
    s2 = 1.0
    for _ in range(200):
        a = k * math.sqrt(s2)
        phi = math.exp(-0.5 * a * a) / math.sqrt(2.0 * math.pi)
        inside = math.erf(a / math.sqrt(2.0))
        new = 1.0 - 2.0 * a * phi / inside
        if abs(new - s2) < 1e-12:
            break
        s2 = new
    return s2


CLIP_VAR = _clip_var_factor()


def clipped_var(values: np.ndarray) -> float:
    """Sigma-clipped variance of the finite values, corrected for the clipping (NaN when too few)."""
    from astropy.stats import sigma_clipped_stats
    v = np.asarray(values, dtype=np.float64).ravel()
    v = v[np.isfinite(v)]
    if v.size < 100:
        return float("nan")
    _m, _med, sd = sigma_clipped_stats(v, sigma=CLIP_SIGMA, maxiters=10)
    return float(sd) ** 2 / CLIP_VAR


def window(shape, max_px: int = WINDOW_PX, align: int = 2) -> tuple:
    """(y0, y1, x0, x1) of a central window of at most max_px pixels (the whole frame when it is smaller); corners
    and sizes are multiples of align, so a Bayer mosaic keeps its pattern and binning blocks stay whole."""
    a = max(int(align or 1), 1)
    h, w = int(shape[0]) // a * a, int(shape[1]) // a * a
    if h * w <= max_px:
        return 0, h, 0, w
    f = math.sqrt(max_px / float(h * w))
    hh, ww = max(int(h * f) // a * a, a), max(int(w * f) // a * a, a)
    y0, x0 = (h - hh) // 2 // a * a, (w - ww) // 2 // a * a
    return y0, y0 + hh, x0, x0 + ww


def max_pairs_for(raw_px: int) -> int:
    """Pairs sampled: MAX_PAIRS on small frames, half that on big ones (over 8 Mpx: an ASI2600MC frame is 26 Mpx),
    so the measurement stays a few seconds. Enough either way: each pair's variance is known to ~0.5 %."""
    return MAX_PAIRS if raw_px <= 8_000_000 else MAX_PAIRS // 2


def _read_rows(path: str, y0: int, y1: int, x0: int, x1: int):
    """A window of a plain 2-D FITS image, read without loading the whole file: (window as float32, header), scaled
    like photometry_core.read_fits (BSCALE / BZERO). None when the file is not a plain 2-D FITS image."""
    from astropy.io import fits
    if not str(path).lower().endswith((".fit", ".fits", ".fts")):
        return None
    with fits.open(path, memmap=True, do_not_scale_image_data=True) as hdul:
        hdu = hdul[0]
        if hdu.data is None or hdu.header.get("NAXIS") != 2 or isinstance(hdu, fits.CompImageHDU):
            return None
        bscale, bzero = float(hdu.header.get("BSCALE", 1.0)), float(hdu.header.get("BZERO", 0.0))
        win = np.array(hdu.data[y0:y1, x0:x1], dtype=np.float32)
        header = dict(hdu.header)
    if bscale != 1.0:
        win *= np.float32(bscale)
    if bzero != 0.0:
        win += np.float32(bzero)
    return win, header


def plain_fits_2d(path: str) -> bool:
    """True when a window of the file can be read without loading it whole (a plain 2-D FITS image, not
    compressed, image in the primary HDU)."""
    from astropy.io import fits
    if not str(path).lower().endswith((".fit", ".fits", ".fts")):
        return False
    try:
        with fits.open(path, memmap=True, do_not_scale_image_data=True) as hdul:
            hdu = hdul[0]
            return bool(hdu.header.get("NAXIS") == 2 and not isinstance(hdu, fits.CompImageHDU)
                        and int(hdu.header.get("NAXIS1") or 0) > 0 and int(hdu.header.get("NAXIS2") or 0) > 0)
    except Exception:
        return False


def window_loader(binning: int, masters, pattern: str, mode: str, full_load=None, max_px: int = WINDOW_PX,
                  whole=None):
    """load(path) for measure_night: the frame calibrated exactly as the run calibrates it (photometry_core.bin_image,
    calibrate_frame with the run's masters, debayer), but only a central window of it (at most max_px image pixels),
    with the masters cut to the same window, so a 26 Mpx frame costs a fraction of a full calibration.
    full_load(path) -> calibrated, debayered image is used for anything else (XISF, compressed or 3-plane files)
    and for every path whole(path) is true for (review S1: the Stack step's stacks, already calibrated and binned);
    its central window is taken."""
    import dataclasses
    import photometry_core as core
    b = max(int(binning or 1), 1)
    mono = (pattern or "").upper() == core.MONO
    cell = b if mono else 2 * b
    per = b * b * (1 if mono else 4)          # raw pixels per image pixel

    def load(path):
        got = None
        if whole is not None and whole(path):
            if full_load is None:
                raise ValueError("no loader for this file")
            img = np.asarray(full_load(path), dtype=np.float32)
            y0, y1, x0, x1 = window(img.shape, max_px, 1)
            return img[y0:y1, x0:x1]
        try:
            from astropy.io import fits
            h = fits.getheader(path) if str(path).lower().endswith((".fit", ".fits", ".fts")) else None
            if h is not None and h.get("NAXIS") == 2:
                y0, y1, x0, x1 = window((int(h["NAXIS2"]), int(h["NAXIS1"])), max_px * per, cell)
                got = _read_rows(path, y0, y1, x0, x1)
        except Exception:
            got = None
        if got is None:
            if full_load is None:
                raise ValueError("cannot read a window of this file")
            img = np.asarray(full_load(path), dtype=np.float32)
            y0, y1, x0, x1 = window(img.shape, max_px, 1)
            return img[y0:y1, x0:x1]
        raw, header = got
        my0, my1, mx0, mx1 = y0 // b, y1 // b, x0 // b, x1 // b

        def cut(a):
            return None if a is None else np.asarray(a)[my0:my1, mx0:mx1]
        m = dataclasses.replace(masters, bias=cut(masters.bias), dark=cut(masters.dark), flat=cut(masters.flat)) \
            if masters is not None else core.Masters()
        for a in (m.bias, m.dark, m.flat):
            if a is not None and a.shape != (raw.shape[0] // b, raw.shape[1] // b):
                raise ValueError("the masters do not fit this frame")
        cal = core.calibrate_frame(core.bin_image(raw, b), header, m)
        return core.debayer(cal, pattern, mode)
    return load


def mesh_size(shape) -> int:
    """Background mesh: about 1/12 of the short side, at least 16 px."""
    return max(16, int(round(min(shape[0], shape[1]) / 12.0)))


def prep_frame(img: np.ndarray, sat: float | None = None) -> dict:
    """A calibrated frame (or window of one) -> {"resid": frame minus its background model (NaN where masked),
    "mask": stars, saturated / bad pixels and the edge, "level": the background's median, "masked": fraction}.
    Review nit: where the sky itself comes within NEAR_SAT_SIGMA of sat, the pixels at sat are the sky's own upper
    noise tail, not stars; cutting them would make the variance low and the gain high, so that sky is masked too."""
    from astropy.stats import SigmaClip
    from photutils.background import Background2D, MedianBackground
    from photutils.segmentation import detect_sources
    from scipy.ndimage import binary_dilation, gaussian_filter

    x = np.asarray(img, dtype=np.float32)
    bad = ~np.isfinite(x)
    if sat is not None and math.isfinite(float(sat)):
        bad |= x >= float(sat)
    if bad.any():
        bad = binary_dilation(bad, iterations=2)
    mesh = mesh_size(x.shape)
    bkg = Background2D(np.where(bad, 0.0, x), (mesh, mesh), filter_size=(3, 3), mask=bad,
                       sigma_clip=SigmaClip(sigma=3.0), bkg_estimator=MedianBackground())
    back = np.asarray(bkg.background, dtype=np.float32)
    r = x - back
    r0 = np.where(bad, 0.0, r)
    smooth = gaussian_filter(r0, 2.0 / 2.3548, truncate=2.5)   # FWHM 2 px, as make_2dgaussian_kernel(2.0, 5)
    rms = float(bkg.background_rms_median)
    mask = bad.copy()
    if sat is not None and math.isfinite(float(sat)):
        near = back + NEAR_SAT_SIGMA * np.asarray(bkg.background_rms, dtype=np.float32) >= float(sat)
        if near.any():
            mask |= near
    if rms > 0 and math.isfinite(rms):
        seg = detect_sources(smooth, DETECT_SIGMA * rms, 5, mask=bad)   # n_pixels (npixels before photutils 3)
        if seg is not None:
            mask |= binary_dilation(seg.data > 0, iterations=DILATE_PX)
    e = EDGE_PX
    if e > 0 and min(mask.shape) > 4 * e:
        mask[:e, :] = mask[-e:, :] = True
        mask[:, :e] = mask[:, -e:] = True
    good = ~mask
    level = float(np.median(back[good])) if good.any() else float("nan")
    resid = np.where(mask, np.nan, r).astype(np.float32)
    return {"resid": resid, "mask": mask, "level": level, "masked": float(mask.mean()), "rms": rms}


def pair_stats(a: dict, b: dict) -> tuple:
    """Two prepared neighbouring frames -> (sky level S, var(difference) / 2) in image units; NaN when unusable."""
    nan = float("nan")
    if a["resid"].shape != b["resid"].shape:
        return nan, nan
    sa, sb = a["level"], b["level"]
    if not (math.isfinite(sa) and math.isfinite(sb)) or sa <= 0 or sb <= 0:
        return nan, nan
    if abs(sa - sb) / max(sa, sb) > MAX_LEVEL_STEP:
        return nan, nan
    d = a["resid"] - b["resid"]   # NaN where either is masked
    v = clipped_var(d)
    return 0.5 * (sa + sb), 0.5 * v


def whiteness(a: dict, b: dict) -> float:
    """Mike Fitzgerald's whiteness ratio of a prepared pair: var(difference) / (var(neighbouring-pixel differences) / 2),
    the smaller of x and y (masks OR'd for both pixels; sigma-clipped). About 1 for white noise; NaN when unusable."""
    if a["resid"].shape != b["resid"].shape:
        return float("nan")
    d = a["resid"] - b["resid"]   # NaN where either is masked, so a neighbour difference is NaN when either is
    v = clipped_var(d)
    vx, vy = clipped_var(d[:, 1:] - d[:, :-1]), clipped_var(d[1:, :] - d[:-1, :])
    ratios = [v / (n / 2.0) for n in (vx, vy) if math.isfinite(n) and n > 0]
    if not ratios or not math.isfinite(v):
        return float("nan")
    return float(min(ratios))


def structure_words(n: int) -> str:
    return (f"{n} pair{'s' if n != 1 else ''} dropped: the sky had structure the background model missed, e.g. cloud "
            "or moonlight")


def floor_from_pair(f1: np.ndarray, f2: np.ndarray, with_err: bool = False):
    """Noise floor (variance per image pixel) from two frames without light taken the same way as the lights (two
    darks of the lights' exposure: read + dark-current noise; two bias frames: read noise): clipped var(f1-f2)/2.
    with_err: (floor, its statistical error floor * sqrt(2 / pixels used))."""
    nan = float("nan")
    a, b = np.asarray(f1, dtype=np.float64), np.asarray(f2, dtype=np.float64)
    if a.shape != b.shape:
        return (nan, nan) if with_err else nan
    e = EDGE_PX
    d = a - b
    if min(d.shape) > 4 * e:
        d = d[e:-e, e:-e]
    fl = 0.5 * clipped_var(d)
    if not with_err:
        return fl
    n = int(np.isfinite(d).sum())
    return fl, (fl * math.sqrt(2.0 / n) if n > 0 and math.isfinite(fl) else nan)


def floor_combine(values, errors=()) -> tuple:
    """(floor, error) from up to a few frame pairs' floors: the median, with the larger of the pairs' own error (over
    sqrt n) and their scatter (over sqrt n). (None, 0) when none is usable."""
    v = np.asarray([x for x in values if x is not None and math.isfinite(x) and x > 0], dtype=float)
    if not v.size:
        return None, 0.0
    e = np.asarray([x for x in errors if x is not None and math.isfinite(x) and x >= 0], dtype=float)
    stat = float(np.median(e)) / math.sqrt(v.size) if e.size else 0.0
    scat = float(np.std(v, ddof=1)) / math.sqrt(v.size) if v.size > 1 else 0.0
    return float(np.median(v)), max(stat, scat)


def pick_pairs(n: int, max_pairs: int = MAX_PAIRS) -> list:
    """Neighbouring frame pairs (i, i+1) spread through a night of n frames, at most max_pairs."""
    if n < 2:
        return []
    if n - 1 <= max_pairs:
        return [(i, i + 1) for i in range(n - 1)]
    starts = np.unique(np.round(np.linspace(0, n - 2, max_pairs)).astype(int))
    return [(int(i), int(i) + 1) for i in starts]


def ptc_fit(levels, variances) -> dict:
    """Photon-transfer line v = S / g + c (weights 1/v: each pair's variance is known to a similar fraction).
    {"ok", "g", "g_err", "intercept", "intercept_err", "slope_rel_err", "n", "range"}; ok only when the slope is
    positive and known to better than PTC_MAX_REL_ERR from at least PTC_MIN_PAIRS pairs."""
    S, v = np.asarray(levels, dtype=float), np.asarray(variances, dtype=float)
    keep = np.isfinite(S) & np.isfinite(v) & (v > 0) & (S > 0)
    S, v = S[keep], v[keep]
    out = {"ok": False, "g": float("nan"), "g_err": float("nan"), "intercept": float("nan"),
           "intercept_err": float("nan"), "slope_rel_err": float("nan"), "n": int(S.size),
           "range": float(S.max() / S.min()) if S.size else float("nan")}
    if S.size < PTC_MIN_PAIRS or np.ptp(S) <= 0:
        return out
    try:
        p, cov = np.polyfit(S, v, 1, w=1.0 / v, cov=True)
    except Exception:
        return out
    slope, icpt = float(p[0]), float(p[1])
    se, ie = float(np.sqrt(max(cov[0, 0], 0.0))), float(np.sqrt(max(cov[1, 1], 0.0)))
    rel = se / slope if slope > 0 else float("inf")
    out.update({"intercept": icpt, "intercept_err": ie, "slope_rel_err": rel})
    if slope > 0:
        out["g"] = 1.0 / slope
        out["g_err"] = se / slope ** 2
    out["ok"] = bool(slope > 0 and rel < PTC_MAX_REL_ERR)
    return out


def night_gain(levels, variances, floor_var: float | None = None, floor_source: str = "",
               floor_err: float | None = None, dropped_structure: int = 0) -> dict:
    """The night's gain from the pairs' (S, v). floor_source: "darks", "bias" or "" (none known); floor_err: the
    floor's own error (variance units), which widens k_lo / k_hi.
    {"ok", "k", "k_lo", "k_hi", "k_err" (the photon-transfer gain error when the floor is that line's, else NaN),
    "n_pairs", "floor_var", "floor_err", "floor_source" ("darks" / "bias" / "ptc" / ""), "ptc", "why"}. k in electrons
    per image unit; not ok -> the caller uses the old lower limit. dropped_structure: pairs the whiteness guard dropped
    (said in "why" when too few are left; kept as "dropped_structure")."""
    S, v = np.asarray(levels, dtype=float), np.asarray(variances, dtype=float)
    keep = np.isfinite(S) & np.isfinite(v) & (S > 0) & (v > 0)
    S, v = S[keep], v[keep]
    nan = float("nan")
    ptc = ptc_fit(S, v)
    out = {"ok": False, "k": nan, "k_lo": nan, "k_hi": nan, "k_err": nan, "n_pairs": int(S.size), "floor_var": nan,
           "floor_err": nan, "floor_source": "", "ptc": ptc, "why": "", "levels": S.tolist(), "variances": v.tolist(),
           "dropped_structure": int(dropped_structure or 0)}
    if S.size < MIN_PAIRS:
        out["why"] = f"only {S.size} usable frame pair{'s' if S.size != 1 else ''}"
        if dropped_structure:
            out["why"] += f" ({structure_words(int(dropped_structure))})"
        return out
    fl = float(floor_var) if floor_var is not None and math.isfinite(float(floor_var)) and floor_var >= 0 else None
    src = floor_source if fl is not None else ""
    fe = float(floor_err) if (fl is not None and floor_err is not None and math.isfinite(float(floor_err))
                              and floor_err > 0) else 0.0
    if fl is None and ptc["ok"] and ptc["intercept"] > 0:
        fl, src = ptc["intercept"], "ptc"
        fe = 0.0   # the line's own gain error is used for the range instead (review N5)
    if fl is None:
        out["why"] = ("no darks or bias frames for the read noise, and the night's sky range is too small for a "
                      "photon-transfer line")
        return out
    # review N1: only pairs whose sky noise is at least the floor's (variance >= 2 x floor)
    use = np.isfinite(S) & (v >= MIN_SKY_OVER_FLOOR * fl) & (v > fl)
    with np.errstate(divide="ignore", invalid="ignore"):
        g_all = S / (v - fl)
    use &= np.isfinite(g_all) & (g_all > 0)
    if int(use.sum()) < MIN_PAIRS:
        out["why"] = (f"the sky is too faint against the {floor_words(src)} (the sky's own noise has to be at least "
                      f"as large as the {floor_words(src)}, and it was in only {int(use.sum())} of {S.size} frame "
                      "pairs), so a small error in the read noise would change the gain a lot")
        out["floor_var"], out["floor_source"] = fl, src
        return out
    Su, vu = S[use], v[use]
    g = g_all[use]
    k = float(np.median(g))
    with np.errstate(divide="ignore", invalid="ignore"):
        g_lo = Su / (vu - fl + fe)                 # a lower floor: a lower gain
        d_hi = vu - fl - fe
        g_hi = np.where(d_hi > 0, Su / np.where(d_hi > 0, d_hi, 1.0), np.inf)
    lo, hi = float(np.percentile(g_lo, 16)), float(np.percentile(g_hi, 84))
    k_err = nan
    if src == "ptc" and math.isfinite(ptc.get("g_err", nan)):
        k_err = float(ptc["g_err"])                 # review N5: the photon-transfer line's own error
        lo, hi = min(lo, k - k_err), max(hi, k + k_err)
    out.update({"ok": True, "k": k, "k_lo": lo, "k_hi": hi, "k_err": k_err, "n_pairs": int(g.size), "floor_var": fl,
                "floor_err": fe if src != "ptc" else float(ptc.get("intercept_err", nan)), "floor_source": src})
    return out


def measure_night(paths, load, sat: float | None = None, floor_var: float | None = None, floor_source: str = "",
                  max_pairs: int = MAX_PAIRS, skip=None, floor_err: float | None = None) -> dict:
    """The night's gain from up to max_pairs neighbouring pairs of `paths` (in time order). load(path) -> the
    calibrated image window (raises on a frame it cannot read). skip: paths never used (bad frames). Each frame is
    read and prepared once. Returns night_gain's dict plus "pairs_tried"."""
    skip = set(skip or ())
    use = [p for p in paths if p not in skip]
    pairs = pick_pairs(len(use), max_pairs)
    cache: dict = {}

    def prepared(i):
        if i not in cache:
            try:
                cache[i] = prep_frame(load(use[i]), sat)
            except Exception:
                cache[i] = None
        return cache[i]

    S, V = [], []
    dropped = 0
    for n, (i, j) in enumerate(pairs):
        a, b = prepared(i), prepared(j)
        cache.pop(i, None)   # pairs go forward in time: frame i is not needed again
        if n + 1 >= len(pairs) or pairs[n + 1][0] != j:
            cache.pop(j, None)   # review nit: nor is j when the next pair does not start at it
        if a is None or b is None:
            continue
        s, v = pair_stats(a, b)
        if math.isfinite(s) and math.isfinite(v):
            r = whiteness(a, b)
            if math.isfinite(r) and r > WHITE_MAX:   # Mike Fitzgerald's whiteness guard
                dropped += 1
                continue
            S.append(s)
            V.append(v)
    out = night_gain(S, V, floor_var, floor_source, floor_err, dropped)
    out["pairs_tried"] = len(pairs)
    return out


def variance_factor(binning: int, pattern: str, mode: str) -> float:
    """q = sum of the debayer weights squared / b^2: the variance in image units of a white noise of 1 ADU^2 per
    camera pixel (no flat). Camera e-/ADU = k * q; read noise per camera pixel (e-) = sqrt(floor) * k * sqrt(q)."""
    import photometry_core as core
    b = max(int(binning or 1), 1)
    return sum(w * w for w in core.debayer_weights(pattern, mode).values()) / (b * b)


def read_noise_e(floor_var: float, k: float, binning: int, pattern: str, mode: str) -> float:
    """The floor as electrons per camera pixel (NaN when not known)."""
    if not (floor_var is not None and math.isfinite(floor_var) and floor_var >= 0 and math.isfinite(k) and k > 0):
        return float("nan")
    return math.sqrt(floor_var) * k * math.sqrt(variance_factor(binning, pattern, mode))


def floor_words(src: str) -> str:
    return {"darks": "read + dark noise", "bias": "read noise", "ptc": "read + dark noise"}.get(src, "read noise")


def floor_from_words(src: str) -> str:
    return {"darks": "from the darks", "bias": "from the bias frames (dark noise left in, so the gain is a little low)",
            "ptc": "from the photon-transfer line"}.get(src, "")


def summary(meas: dict) -> str:
    """The measurement in a sentence fragment for the log (after 'gain measured from the night's sky: ')."""
    if not meas or not meas.get("ok"):
        return ""
    k = meas["k"]
    n = meas["n_pairs"]
    rn = math.sqrt(meas["floor_var"]) if math.isfinite(meas.get("floor_var", float("nan"))) else float("nan")
    k_err = meas.get("k_err")
    if meas.get("floor_source") == "ptc" and k_err is not None and math.isfinite(k_err):
        # review N5: the photon-transfer floor's gain error is the honest spread
        text = (f"{k:.3g} +/- {k_err:.2g} electrons per image unit (from {n} frame pairs; "
                f"{floor_words(meas['floor_source'])} {rn:.3g} units {floor_from_words(meas['floor_source'])}")
    else:
        text = (f"{k:.3g} electrons per image unit (from {n} frame pairs, {meas['k_lo']:.3g}-{meas['k_hi']:.3g}; "
                f"{floor_words(meas['floor_source'])} {rn:.3g} units {floor_from_words(meas['floor_source'])}")
    rn_e = meas.get("rn_e")
    if rn_e is not None and math.isfinite(rn_e):
        text += f", about {rn_e:.3g} e- per camera pixel"
    if meas.get("dropped_structure"):
        text += f"; {structure_words(int(meas['dropped_structure']))}"
    text += ")"
    return text


def ptc_words(meas: dict) -> str:
    """The photon-transfer cross-check in a sentence (or why there is none)."""
    p = (meas or {}).get("ptc") or {}
    if p.get("ok"):
        return (f"Photon-transfer check across the night: {p['g']:.3g} +/- {p['g_err']:.2g} electrons per image unit "
                f"(the sky level ranged {p['range']:.2g}x over the pairs).")
    if (meas or {}).get("n_pairs", 0) >= PTC_MIN_PAIRS:
        return "No photon-transfer check: the night's sky level changed too little to give a slope."
    return ""


FLUX_GAIN_TOL = 0.15        # gain within 15 % of the bench value: agrees
FLUX_RN_TOL = 0.25          # read noise within 25 % (the darks' floor also holds the dark current's noise)


def _pct(a: float, b: float) -> str:
    d = (a / b - 1.0) * 100.0
    return f"{abs(d):.0f} % {'higher' if d > 0 else 'lower'}"


def flux_words(cam_id: str, meas: dict | None, setup: dict | None, g_flux, g_src: str, rn_flux, rn_src: str,
               epu1) -> str:
    """2.4.0 (#104): a plain note comparing the run's gain (and read noise) with a SHOBS-FLUX camera profile's.
    epu1 = photometry_core.electrons_per_unit(1, ...) (image units per camera e-/ADU, a number or map). "" when the
    profile has no gain."""
    import shobs_profile as sp
    try:
        g_flux = float(g_flux)
    except (TypeError, ValueError):
        return ""
    if not (math.isfinite(g_flux) and g_flux > 0):
        return ""
    q1 = float(np.nanmedian(epu1)) if np.ndim(epu1) else float(epu1)
    head = f"SHOBS-FLUX camera profile {cam_id}: gain {g_flux:.3g} e-/ADU ({sp.SOURCE_WORDS.get(g_src, g_src or 'source not given')})"
    if meas and meas.get("ok") and q1 > 0:
        g_sky = float(meas["k"]) / q1
        agree = (f"they agree (within {abs(g_sky / g_flux - 1) * 100:.0f} %)" if abs(g_sky / g_flux - 1) <= FLUX_GAIN_TOL
                 else f"{_pct(g_sky, g_flux)}: check that the profile is for this camera's gain and offset setting")
        text = f"{head}; the night's sky gives {g_sky:.3g} e-/ADU, {agree}."
        rn_e = meas.get("rn_e")
        try:
            rn_flux = float(rn_flux)
        except (TypeError, ValueError):
            rn_flux = float("nan")
        if rn_e is not None and math.isfinite(rn_e) and math.isfinite(rn_flux) and rn_flux > 0:
            what = floor_words(meas.get("floor_source", ""))
            ok = abs(rn_e / rn_flux - 1) <= FLUX_RN_TOL
            text += (f" Read noise {rn_flux:.3g} e- ({sp.SOURCE_WORDS.get(rn_src, rn_src or 'source not given')}); "
                     f"the night's {what} is {rn_e:.3g} e-, " + ("about the same." if ok else _pct(rn_e, rn_flux) + "."))
        return text
    used = (setup or {}).get("e_per_adu")
    if used:
        return (f"{head}; the headers' EGAIN is {float(used):.3g} e-/ADU"
                + (" (the same)." if abs(float(used) / g_flux - 1) <= 0.02 else f" ({_pct(float(used), g_flux)}).")
                + " The night's sky could not be measured for a second check.")
    return f"{head}; the night's sky could not be measured, so there is nothing to compare it with."
