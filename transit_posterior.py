"""2.2.19 (#31): what the transit fit's MCMC posterior says beyond median ± error: a corner plot, a check for parameters
pinned against their allowed range, and credible bands for the transit plot. Also two fit checks on the Transit fit
page: the free-shape fit running away (nit), and "Both" detrending absorbing the transit (nit).

NumPy (and matplotlib for the corner plot only); no new dependency. Kept apart from transit_core so the posterior
tools stay in one place; transit_core.fit_transit calls into it.
"""
from __future__ import annotations

import math

import numpy as np

import transit_core as tc

# Credible bands: posterior draws (capped) and the time grid they are drawn on.
BAND_DRAWS = 300
BAND_GRID = 500

# Prior-bound check: a parameter is "pinned" when at least BOUND_FRAC of its samples lie within BOUND_EDGE of its
# range (as a fraction of the range) from an edge (the samples reach the edge), AND the histogram bin at that edge
# (30 bins over the samples' span) holds at least BOUND_PEAK of the tallest bin: the histogram does not fall away
# towards the edge (a pile-up or a flat top there, not the thin tail of a hump). WASP-12 b's no-detection night
# (Rp/R* down to 0.005, Tc humps at both ends of its window) is the test case; well-measured nights never reach an edge.
BOUND_EDGE = 0.01
BOUND_FRAC = 0.005
BOUND_PEAK = 0.35

# Free-shape fit "not constrained": depth more than FREE_DEPTH_RATIO x (or less than 1/that) the reference depth,
# a shape parameter at its least-squares bound, or Rp/R* with an error larger than itself.
FREE_DEPTH_RATIO = 3.0

# Labels for the fitted parameters (the order the corner plot uses).
LABELS = {"tc": "Tc", "k": "Rp/R*", "a_rs": "a/R*", "b": "b", "q1": "q1 (LD)", "q2": "q2 (LD)",
          "c0": "Baseline", "c_am": "Airmass slope", "c_t": "Time slope (/day)", "step": "Flip step (%)"}
ORDER = ("tc", "k", "a_rs", "b", "step", "c0", "c_am", "c_t", "q1", "q2")
# Parameters whose pile-up at an edge is reported as a caution (limb darkening is prior-led by design; the baseline
# terms have no hard bound in the MCMC).
FLAG_PARAMS = ("tc", "k", "a_rs", "b", "step")


def _base(name: str) -> str:
    """2.2.20 (item 5): the kind of a parameter: "tc" for the multi-night "tc:2", "k" for "k:all" (single-night names
    have no ":")."""
    return str(name).partition(":")[0]


def param_bounds(prob, k_med: float | None = None) -> dict:
    """The hard limits the MCMC uses (TransitProblem.in_bounds), per free parameter, in the units the chain holds:
    Tc within the data span ± 0.2 d; 0.005 < Rp/R* < 0.6; 1.2 < a/R* < 200; 0 ≤ b < 1 + Rp/R*; |step| < 0.5;
    0 < q1, q2 < 1. The baseline terms (c0, airmass and time slopes) have no hard limit in the MCMC. SHOBS-P puts no
    window prior on Tc (unlike a prior box around the prediction) and only Gaussian priors on a/R* and b."""
    k = 0.1 if k_med is None else k_med
    out = {"k": (0.005, 0.6), "a_rs": (1.2, 200.0), "b": (0.0, 1.0 + k), "step": (-0.5, 0.5), "q1": (0.0, 1.0),
           "q2": (0.0, 1.0)}
    nights = getattr(prob, "nights", None)
    res = {}
    for name in prob.free:
        base, _sep, j = name.partition(":")
        if base == "tc":
            if nights is not None:
                # 2.2.20 (item 5): multi-night (multinight.JointProblem): each night's own window, as its in_bounds
                d = nights[int(j)]
                res[name] = (float(d["t"][0]) - 0.2, float(d["t"][-1]) + 0.2)
            else:
                res[name] = (float(prob.t.min()) - 0.2, float(prob.t.max()) + 0.2)
        elif base in out:
            res[name] = out[base]
    return res


def _edge_text(name: str, edge: float, side: str) -> str:
    name = _base(name)
    if name == "tc":
        return ("the start of the allowed window (data start − 0.2 d)" if side == "lo"
                else "the end of the allowed window (data end + 0.2 d)")
    if name == "b" and side == "hi":
        return "1 + Rp/R* (a barely grazing planet)"
    if name == "step":
        return f"{edge * 100:+.0f} %"
    return f"{edge:g}"


def prior_bound_flags(prob, flat, labels: dict | None = None) -> list:
    """2.2.19 (#31): parameters whose MCMC samples pile up against an edge of their allowed range (see param_bounds).
    [{"name", "label", "sides": ["lo"/"hi"], "edges", "frac", "text"}], one per parameter. Such an error bar describes
    the range, not the data. 2.2.20 (item 5): also the multi-night problem ("tc:0", "k:all", …); labels names them
    ("Tc 15-OCT-2026")."""
    flat = np.asarray(flat, dtype=float)
    if flat.ndim != 2 or flat.shape[0] < 50:
        return []
    free = list(prob.free)
    ks = [i for i, nm in enumerate(free) if _base(nm) == "k"]
    # b < 1 + Rp/R* for every shape set: the smallest Rp/R* sets b's upper edge
    k_med = min(float(np.median(flat[:, i])) for i in ks) if ks else None
    bounds = param_bounds(prob, k_med)
    out = []
    names = [nm for base in FLAG_PARAMS for nm in free if _base(nm) == base]
    for name in names:
        if name not in bounds:
            continue
        x = flat[:, free.index(name)]
        x = x[np.isfinite(x)]
        if x.size < 50 or np.ptp(x) <= 0:
            continue
        lo, hi = bounds[name]
        tol = BOUND_EDGE * (hi - lo)
        counts, _edges = np.histogram(x, bins=30)
        peak = max(int(counts.max()), 1)
        sides = []
        for side, edge in (("lo", lo), ("hi", hi)):
            frac = float(np.mean(np.abs(x - edge) < tol))
            edge_bin = counts[0] if side == "lo" else counts[-1]
            if frac >= BOUND_FRAC and edge_bin >= BOUND_PEAK * peak:
                sides.append((side, edge, frac))
        if not sides:
            continue
        label = (labels or {}).get(name) or LABELS.get(_base(name), name)
        level = "warn"
        if name == "b" and len(sides) == 1 and sides[0][0] == "lo":
            # 2.2.19 review: a central transit (published b ≈ 0) is a measurement with a one-sided error, not a
            # prior problem: a note, not a caution, and kept out of the report notes.
            q95 = float(np.percentile(x, 95))
            level = "info"
            text = (f"b sits against 0, the lower end of its range (a central transit): read b as an upper limit "
                    f"(b < {q95:.2f}, 95%), not value ± error.")
        elif len(sides) == 2:
            where = ("its whole allowed window (data span ± 0.2 d)" if _base(name) == "tc" else
                     f"its whole allowed range ({_edge_text(name, lo, 'lo')} to {_edge_text(name, hi, 'hi')})")
            text = (f"{label} is limited by its prior range: its MCMC samples spread over {where}, so the error bar "
                    "is not a measurement.")
        else:
            text = (f"{label} is limited by its prior range: its MCMC samples pile up against "
                    f"{_edge_text(name, sides[0][1], sides[0][0])}, so the error bar is not a measurement.")
        out.append({"name": name, "label": label, "sides": [sd[0] for sd in sides], "edges": [sd[1] for sd in sides],
                    "frac": max(sd[2] for sd in sides), "text": text, "level": level,
                    "upper": float(np.percentile(x, 95)) if level == "info" else None})
    return out


def credible_bands(prob, flat, best: dict, n_draws: int = BAND_DRAWS, n_grid: int = BAND_GRID, seed: int = 11) -> dict:
    """2.2.19 (#31): 68 % and 95 % envelopes of the model over random posterior draws (capped at n_draws), on a time
    grid across the data. As the plot shows it (flux divided by the best-fit baseline):
      model: transit × baseline of each draw / best baseline (out of transit this is the baseline's own spread);
      base:  baseline of each draw / best baseline − 1 (the baseline/detrend line's spread, around 0).
    Times are in the problem's (relative) days. Percentiles: 2.275 / 15.865 / 84.135 / 97.725."""
    flat = np.asarray(flat, dtype=float)
    rng = np.random.default_rng(seed)
    n = int(min(max(n_draws, 1), flat.shape[0]))
    pick = flat[rng.choice(flat.shape[0], n, replace=False)]
    tt = np.linspace(float(prob.t.min()), float(prob.t.max()), int(n_grid))
    am = np.interp(tt, prob.t, prob.am)
    base_best = prob.baseline(best, tt, am)
    models = np.empty((n, tt.size))
    bases = np.empty((n, tt.size))
    for j, x in enumerate(pick):
        p = prob.full(x)
        u1, u2 = tc.kipping_to_u(p["q1"], p["q2"])
        base = prob.baseline(p, tt, am) / base_best
        models[j] = tc.occultation(tc.separation(tt, p["tc"], prob.period, p["a_rs"], p["b"]), p["k"], u1, u2) * base
        bases[j] = base - 1.0
    q = [2.275, 15.865, 84.135, 97.725]
    m = np.percentile(models, q, axis=0)
    b = np.percentile(bases, q, axis=0)
    return {"t": tt, "m95": (m[0], m[3]), "m68": (m[1], m[2]), "b95": (b[0], b[3]), "b68": (b[1], b[2]), "draws": n}


# ---- Free-shape fit and "Both" detrending (Transit fit page nits) -------------------------------------------------

def free_shape_unconstrained(free_fit: dict, errors: dict, ref_depth: float | None, ref_label: str) -> str:
    """2.2.19 (nit): '' when the free-shape fit (no a/R*, b priors) is usable, else why its numbers are not shown:
    (1) its depth is more than 3× (or under 1/3 of) the reference depth (the published (Rp/R*)², else the MCMC
    depth); (2) Rp/R*, a/R* or b ends at its least-squares bound; (3) Rp/R*'s error is larger than Rp/R* itself."""
    p = free_fit.get("p") or {}
    k = p.get("k")
    if k is None or not math.isfinite(k):
        return "the fit did not converge"
    depth = k * k
    if ref_depth and ref_depth > 0 and depth > 0:
        ratio = depth / ref_depth
        if ratio > FREE_DEPTH_RATIO or ratio < 1.0 / FREE_DEPTH_RATIO:
            return (f"depth {depth * 100:.2f}% is {ratio:.1f}x the {ref_label} {ref_depth * 100:.2f}%"
                    if ratio > 1 else f"depth {depth * 100:.3f}% is under a third of the {ref_label} "
                                      f"{ref_depth * 100:.2f}%")
    for name, lo, hi in (("k", 0.006, 0.59), ("a_rs", 1.3, 199.0), ("b", 0.0, 1.5)):
        v = p.get(name)
        if v is None:
            continue
        span = hi - lo
        if (name != "b" and v - lo < 0.005 * span) or hi - v < 0.005 * span:
            return f"{LABELS[name]} ran to the end of its range ({v:.3g})"
    ek = errors.get("k")
    if ek is not None and (not math.isfinite(ek) or ek > k):
        return f"Rp/R* {k:.3f} has an error larger than itself (± {ek:.3f})" if math.isfinite(ek) else \
            "Rp/R* has no finite error"
    return ""


# 2.3.2 (item 7): a free shape can run away in a/R*, b or T14 with a sensible depth (Joanna's WASP-35 b,
# MicroObservatory: a/R* 5.68 and T14 4.137 h against the published 8.53 and 3.143 h, shown as normal values).
# Display only: the table says "not constrained" for that parameter; the fit and the saved numbers are unchanged.
FREE_SHAPE_OFF = 0.25       # a/R* or T14 more than 25 % above or below the reference
FREE_B_OFF = 0.3            # b more than 0.3 from the reference (or no finite error)
FREE_REL_ERR = 0.5          # a/R*'s own error above 50 % of its value
# John 8 Nov: b's own error bar is no test: b and a/R* trade off on any single ground-based light curve, so a wide
# b error is normal (TOI-2046 b 20-21 Oct, a good night, was flagged); b says "not constrained" only when it lands
# far from the published value. a/R*'s error test was eased from 25 % to 50 % for the same reason.


def free_shape_runaway(free_fit: dict, ref: dict) -> dict:
    """{"a_rs" | "b" | "t14": why} for the free-shape parameters that are not constrained. ref holds the published
    values ("a_rs", "b", "t14"; t14 in days like the fit), else the MCMC's; a missing or non-finite reference skips
    the comparison (a/R*'s error test still runs; for b the only error test left is "has no finite error", since b and
    a/R* trade off in a free fit and a wide b error alone is common)."""
    out = {}
    if not free_fit:
        return out
    p = free_fit.get("p") or {}
    err = free_fit.get("err") or {}

    def finite(v):
        # 2.3.2 review: numpy scalars too (np.float32 is not a float subclass); bool is not a number here
        return isinstance(v, (int, float, np.floating, np.integer)) and not isinstance(v, bool) and math.isfinite(v)

    def ratio_off(v, r):
        return finite(v) and finite(r) and r > 0 and abs(v / r - 1.0) > FREE_SHAPE_OFF

    a, ea = p.get("a_rs"), err.get("a_rs")
    if finite(a):
        if ea is not None and (not finite(ea) or ea > FREE_REL_ERR * a):
            out["a_rs"] = f"a/R* {a:.2f} ± {ea:.2f}" if finite(ea) else f"a/R* {a:.2f} has no finite error"
        elif ratio_off(a, ref.get("a_rs")):
            out["a_rs"] = f"a/R* {a:.2f} against {ref['a_rs']:.2f}"
    b, eb = p.get("b"), err.get("b")
    if finite(b):
        rb = ref.get("b")
        if eb is not None and not finite(eb):
            out["b"] = f"b {b:.2f} has no finite error"
        elif finite(rb) and abs(b - rb) > FREE_B_OFF:
            out["b"] = f"b {b:.2f} against {rb:.2f}"
    t14 = free_fit.get("t14")
    if ratio_off(t14, ref.get("t14")):
        out["t14"] = f"T14 {t14 * 24:.2f} h against {ref['t14'] * 24:.2f} h"
    return out


def single_detrend_dbic(prob, best: dict, starts) -> dict:
    """2.2.19 (nit): with "Both" detrending (airmass and time slope), refit by least squares with each one alone and
    return {"airmass": ΔBIC, "time": ΔBIC, "corr": correlation of airmass with time}. ΔBIC is the flat line (same
    baseline) minus the transit model, the transit paying for Tc and Rp/R* as in transit_verdict. Least squares only,
    run only when Both found no transit."""
    from scipy.optimize import least_squares

    t, flux, err, am = prob.t, prob.flux, prob.err, prob.am
    n = len(t)
    out = {"corr": float(np.corrcoef(am, t)[0, 1]) if np.std(am) > 0 and np.std(t) > 0 else float("nan")}
    for which, use_am, use_t in (("airmass", True, False), ("time", False, True)):
        try:
            p1 = tc.TransitProblem(t, flux, err, am, prob.period, prob.priors, tuple(prob.free),
                                   dict(best), use_am, use_t, prob.ld_prior,
                                   prob.t_flip)
            # The start: the Both fit's shape, with this baseline only.
            for name in ("c_am", "c_t"):
                if name not in p1.free:
                    p1.fixed[name] = 0.0
            x, chi2_tr = tc.best_fit(p1, starts)
            names = [p for p in ("c0", "c_am", "c_t", "step") if p in p1.free]
            after = p1.after if p1.after is not None else 0.0

            def flat(xx):
                q = dict(zip(names, xx))
                base = q.get("c0", 1.0) + q.get("c_am", 0.0) * am + q.get("c_t", 0.0) * (t - p1.t_mid)
                if "step" in q:
                    base = base * (1.0 + q["step"] * after)
                return (flux - base) / err

            sol = least_squares(flat, [1.0 if nm == "c0" else 0.0 for nm in names])
            chi2_flat = float(np.dot(sol.fun, sol.fun))
            out[which] = (chi2_flat + len(names) * math.log(n)) - (chi2_tr + (len(names) + 2) * math.log(n))
        except Exception:
            out[which] = None
    return out


def both_detrend_text(check: dict, threshold: float = 10.0) -> str:
    """'' unless one of the single detrends detects the transit while Both does not."""
    hits = [(w, d) for w in ("airmass", "time") if isinstance(d := check.get(w), (int, float)) and d >= threshold]
    if not hits:
        return ""
    corr = check.get("corr")
    same = isinstance(corr, float) and math.isfinite(corr) and abs(corr) > 0.9
    why = ("airmass and time trends are nearly the same over this night (correlation "
           f"{corr:+.2f}) and may absorb the transit together" if same else
           "the airmass and time slopes together may absorb the transit")
    alone = "; ".join(f"{'Airmass' if w == 'airmass' else 'time slope'} alone detects it (ΔBIC {d:.0f})"
                      for w, d in hits)
    alone = alone[:1].upper() + alone[1:]
    other = ("one of the two (airmass or time slope)" if len(hits) == 2 else
             "time slope" if hits[0][0] == "airmass" else "airmass")
    fix = (f"Untick {other} and fit again only if that trend is not real (a slope artifact can also read as a "
           "detection)")
    return f"No detection with Both: {why}. {alone}. {fix}."


# ---- Corner plot ---------------------------------------------------------------------------------------------------

def _transform(name: str, x, tc_med: float):
    name = _base(name)
    if name == "tc":
        return (x - tc_med) * 1440.0
    if name == "step":
        return x * 100.0
    return x


SHAPE_PARAMS = ("tc", "k", "a_rs", "b", "step")


# 2.2.20 (item 5): the multi-night corner plot's default: the shared shape, then each night's Tc.
MULTI_ORDER = ("k", "a_rs", "b", "tc", "step", "c0", "c_am", "c_t", "q1", "q2")
MULTI_SHAPE = ("k", "a_rs", "b", "tc")


def corner_params(result: dict, shape_only: bool = False) -> list:
    """The fitted (free) parameters in corner-plot order; parameters held fixed are left out. shape_only: Tc, Rp/R*,
    a/R*, b and the flip step (the Corner plot window's default; review: ten parameters on a laptop were unreadable).
    2.2.20 (item 5): multi-night names ("k:all", "tc:0", …): Rp/R*, a/R*, b, then each night's Tc by default."""
    free = list(result.get("free_params") or [])
    if any(":" in p for p in free):
        order = MULTI_ORDER
        out = [p for base in order for p in free if _base(p) == base]
        out += [p for p in free if p not in out]
        return [p for p in out if _base(p) in MULTI_SHAPE] if shape_only else out
    out = [p for p in ORDER if p in free] + [p for p in free if p not in ORDER]
    return [p for p in out if p in SHAPE_PARAMS] if shape_only else out


def param_label(result: dict, name: str, short: bool = False) -> str:
    """2.2.20 (item 5): a parameter's name for the plot ("Tc 15-OCT-2026" in a multi-night plot; short, for the axes:
    "Tc 15-OCT")."""
    if short and (result.get("short_labels") or {}).get(name):
        return result["short_labels"][name]
    return (result.get("labels") or {}).get(name) or LABELS.get(_base(name), name)


def _fmt(v: float, e: float) -> str:
    if not (math.isfinite(e) and e > 0):
        return f"{v:.4g}"
    digits = max(0, min(7, 1 - int(math.floor(math.log10(e)))))
    return f"{v:.{digits}f}"


def _contour_levels(h, fracs=(0.393, 0.865)):
    """Density levels enclosing the given fractions of the samples (2-D 1σ and 2σ)."""
    flat = np.sort(h.ravel())[::-1]
    cum = np.cumsum(flat)
    if cum[-1] <= 0:
        return []
    cum /= cum[-1]
    levels = sorted({float(flat[min(np.searchsorted(cum, f), flat.size - 1)]) for f in fracs})
    return [lv for lv in levels if lv > 0]


def corner_figure(result: dict, fig=None, max_samples: int = 20000, font: float = 8.0, shape_only: bool = False,
                  keep_size: bool = False):
    """2.2.19 (#31): the corner plot of the MCMC samples on a matplotlib Figure (made here when fig is None, Agg).
    Diagonal: each fitted parameter's histogram with the median and 16/84 % lines, value +err/−err in the title
    (⚠ and an orange title when the parameter piles up against its allowed range). Below the diagonal: 2-D density
    with the 1σ and 2σ contours. Tc is shown in minutes from its median. keep_size: lay out at the figure's present
    size (a window's canvas resized it) instead of setting it from the number of parameters."""
    if fig is None:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        fig = Figure(dpi=100, facecolor="#ffffff")
        FigureCanvasAgg(fig)
    chain = result["chain"]["flat"]
    free = list(result["free_params"])
    names = corner_params(result, shape_only)
    n = len(names)
    side = min(max(1.25 * n + 0.9, 5.0), 12.5)
    if not keep_size:
        fig.set_size_inches(side, side, forward=False)
    # 2.2.21 (nit 2): everything below is laid out from the figure's actual size (a canvas may have resized it, or
    # set_size_inches may round), not from the size asked for, so the titles and tick labels never clip.
    fig_w, fig_h = (float(v) for v in fig.get_size_inches())
    fig_w, fig_h = max(fig_w, 1.0), max(fig_h, 1.0)
    fig.clear()
    rng = np.random.default_rng(5)
    flat = np.asarray(chain, dtype=float)
    if flat.shape[0] > max_samples:
        flat = flat[rng.choice(flat.shape[0], max_samples, replace=False)]
    t_ref = float(result.get("t_ref", 0.0))
    t_refs = result.get("t_refs") or {}      # 2.2.20 (item 5): multi-night: each Tc's own reference day
    tc_meds = {p: float(np.median(flat[:, free.index(p)])) for p in free if _base(p) == "tc"}
    data = {p: _transform(p, flat[:, free.index(p)], tc_meds.get(p, 0.0)) for p in names}
    flagged = {f["name"] for f in (result.get("bound_flags") or []) if f.get("level", "warn") == "warn"}
    upper = {f["name"]: f.get("upper") for f in (result.get("bound_flags") or []) if f.get("level") == "info"}
    ranges = {}
    for p in names:
        lo, hi = np.percentile(data[p], [0.2, 99.8])
        pad = 0.08 * (hi - lo) if hi > lo else (abs(lo) * 0.01 or 1e-6)
        ranges[p] = (lo - pad, hi + pad)
    # 2.2.20 (item 1b): headroom in inches (whatever the figure's size) for the main title (wrapped, up to two
    # lines), the ⚠ line under it, and the top panel's own two-line title (K2-29 b: the Tc title ran into the main
    # title).
    main_title = _corner_title(result, fig_w, font)
    n_title = main_title.count("\n") + 1
    warn_line = wrap_to(WARN_LINE, fig_w * 72.0 * 0.96, font) if flagged else ""
    n_warn = warn_line.count("\n") + 1 if warn_line else 0
    head_in = 0.12 + n_title * (font + 2) * 1.3 / 72.0 + n_warn * font * 1.3 / 72.0 + 2 * font * 1.3 / 72.0 + 0.22
    top = max(1.0 - head_in / fig_h, 0.55)
    # 2.2.21 (nit 2): room for the rotated tick labels and the axis names in inches, whatever the figure's size
    left = min(max(0.09, (0.45 + 2.6 * font / 72.0) / fig_w), 0.3)
    bottom = min(max(0.08, (0.4 + 3.2 * font / 72.0) / fig_h), 0.3)
    gs = fig.add_gridspec(n, n, hspace=0.08, wspace=0.08, left=left, right=1.0 - 0.15 / fig_w, bottom=bottom,
                          top=top)
    green, orange = "#0b6e4f", "#b35c00"
    for i, pi in enumerate(names):
        for j, pj in enumerate(names[: i + 1]):
            ax = fig.add_subplot(gs[i, j])
            if i == j:
                x = data[pi]
                ax.hist(x, bins=40, range=ranges[pi], color="#9aa7b0", histtype="stepfilled", alpha=0.6)
                ax.hist(x, bins=40, range=ranges[pi], color="#33424d", histtype="step", lw=0.8)
                q16, q50, q84 = np.percentile(x, [15.865, 50, 84.135])
                for q, ls in ((q16, "--"), (q50, "-"), (q84, "--")):
                    ax.axvline(q, color=green, ls=ls, lw=1.0)
                warn = pi in flagged
                label = param_label(result, pi)
                if _base(pi) == "tc":
                    ref = float(t_refs.get(pi, t_ref))
                    # 2.2.20 (item 5): a night's Tc ("Tc 15-OCT-2026") on a line of its own, so it fits its panel
                    sep = "\n" if pi != "tc" else " = "
                    title = f"{label}{sep}{tc_meds[pi] + ref:.5f}\n+{q84 - q50:.1f} / −{q50 - q16:.1f} min"
                elif upper.get(pi) is not None:
                    title = f"{label} < {upper[pi]:.2f} (95 %)\nagainst 0: upper limit"
                else:
                    e = 0.5 * (q84 - q16)
                    title = f"{label} = {_fmt(q50, e)}\n+{_fmt(q84 - q50, e)} / −{_fmt(q50 - q16, e)}"
                ax.set_title(("⚠ " if warn else "") + title, fontsize=font, color=orange if warn else "#1a1a1a",
                             pad=3)
                ax.set_yticks([])
                ax.set_xlim(ranges[pi])
            else:
                x, y = data[pj], data[pi]
                h, xe, ye = np.histogram2d(x, y, bins=30, range=[ranges[pj], ranges[pi]])
                h = _smooth(h)
                ax.pcolormesh(xe, ye, h.T, cmap="Greys", shading="flat", rasterized=True)
                levels = _contour_levels(h)
                if levels:
                    xc, yc = 0.5 * (xe[1:] + xe[:-1]), 0.5 * (ye[1:] + ye[:-1])
                    try:
                        ax.contour(xc, yc, h.T, levels=levels, colors=green, linewidths=0.9)
                    except Exception:
                        pass
                ax.set_xlim(ranges[pj])
                ax.set_ylim(ranges[pi])
            ax.tick_params(labelsize=font - 1, length=2, pad=1)
            for lab in ax.get_xticklabels():
                lab.set_rotation(45)
                lab.set_ha("right")
            if i < n - 1:
                ax.set_xticklabels([])
            else:
                ax.set_xlabel(param_label(result, pj, short=True) + (" (min)" if _base(pj) == "tc" else ""),
                              fontsize=font)
            if j > 0 or i == 0:
                if i != j:
                    ax.set_yticklabels([])
            else:
                ax.set_ylabel(param_label(result, pi, short=True) + (" (min)" if _base(pi) == "tc" else ""),
                              fontsize=font)
            ax.xaxis.set_major_locator(_locator())
            if i != j:
                ax.yaxis.set_major_locator(_locator())
    # 2.2.20 (item 1b): the title wrapped to the figure's width (it was clipped left and right), centered at the top;
    # the ⚠ line under it; both above the top panel's own title.
    y_top = 1.0 - 0.12 / fig_h
    fig.suptitle(main_title, fontsize=font + 2, y=y_top, va="top")
    if warn_line:
        y = y_top - n_title * (font + 2) * 1.3 / 72.0 / fig_h
        fig.text(0.5, y, warn_line, ha="center", va="top", fontsize=font, color=orange)
    return fig


WARN_LINE = "⚠ set by its allowed range, not by the data: that error bar is not a measurement"


def _corner_title(result: dict, side: float, font: float) -> str:
    name = (result.get("planet") or {}).get("name") or "Transit"
    ch = result["chain"]
    what = result.get("corner_title") or "MCMC posterior"     # 2.2.20 (item 5): "joint MCMC posterior, 4 nights"
    return wrap_to(f"{name}: {what} ({ch['walkers']} walkers × {ch['steps']} steps; median and 16/84 % lines)",
                   side * 72.0 * 0.96, font + 2)


def wrap_to(text: str, width_pt: float, font_pt: float) -> str:
    """2.2.20 (item 1b): text wrapped so each line fits width_pt (an average character is ~0.55 of the font size)."""
    import textwrap
    chars = max(int(width_pt / (0.55 * font_pt)), 20)
    return "\n".join(textwrap.wrap(str(text), chars)) or str(text)


def _locator():
    from matplotlib.ticker import MaxNLocator
    return MaxNLocator(3, prune="both")


def _smooth(h):
    """A light 3×3 smoothing of a 2-D histogram (NumPy only)."""
    k = np.array([0.25, 0.5, 0.25])
    p = np.pad(h, 1, mode="edge")
    p = k[0] * p[:-2, :] + k[1] * p[1:-1, :] + k[2] * p[2:, :]
    return k[0] * p[:, :-2] + k[1] * p[:, 1:-1] + k[2] * p[:, 2:]


def save_corner(result: dict, path: str, dpi: int = 110, shape_only: bool = False) -> str:
    """Write the corner plot to path (PNG) and return it (all fitted parameters unless shape_only)."""
    fig = corner_figure(result, shape_only=shape_only)
    fig.savefig(path, dpi=dpi, facecolor="#ffffff")
    return path
