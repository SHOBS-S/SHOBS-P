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


def param_bounds(prob, k_med: float | None = None) -> dict:
    """The hard limits the MCMC uses (TransitProblem.in_bounds), per free parameter, in the units the chain holds:
    Tc within the data span ± 0.2 d; 0.005 < Rp/R* < 0.6; 1.2 < a/R* < 200; 0 ≤ b < 1 + Rp/R*; |step| < 0.5;
    0 < q1, q2 < 1. The baseline terms (c0, airmass and time slopes) have no hard limit in the MCMC. SHOBS-P puts no
    window prior on Tc (unlike a prior box around the prediction) and only Gaussian priors on a/R* and b."""
    k = 0.1 if k_med is None else k_med
    out = {"tc": (float(prob.t.min()) - 0.2, float(prob.t.max()) + 0.2), "k": (0.005, 0.6), "a_rs": (1.2, 200.0),
           "b": (0.0, 1.0 + k), "step": (-0.5, 0.5), "q1": (0.0, 1.0), "q2": (0.0, 1.0)}
    return {name: out[name] for name in prob.free if name in out}


def _edge_text(name: str, edge: float, side: str) -> str:
    if name == "tc":
        return ("the start of the allowed window (data start − 0.2 d)" if side == "lo"
                else "the end of the allowed window (data end + 0.2 d)")
    if name == "b" and side == "hi":
        return "1 + Rp/R* (a barely grazing planet)"
    if name == "step":
        return f"{edge * 100:+.0f} %"
    return f"{edge:g}"


def prior_bound_flags(prob, flat) -> list:
    """2.2.19 (#31): parameters whose MCMC samples pile up against an edge of their allowed range (see param_bounds).
    [{"name", "label", "sides": ["lo"/"hi"], "edges", "frac", "text"}], one per parameter. Such an error bar describes
    the range, not the data."""
    flat = np.asarray(flat, dtype=float)
    if flat.ndim != 2 or flat.shape[0] < 50:
        return []
    k_med = float(np.median(flat[:, prob.free.index("k")])) if "k" in prob.free else None
    bounds = param_bounds(prob, k_med)
    out = []
    for name in FLAG_PARAMS:
        if name not in bounds:
            continue
        x = flat[:, prob.free.index(name)]
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
        label = LABELS.get(name, name)
        if name == "b" and len(sides) == 1 and sides[0][0] == "lo":
            q95 = float(np.percentile(x, 95))
            text = (f"b piles up against 0, the lower end of its range: the ± is not a two-sided measurement; read b "
                    f"as an upper limit (b < {q95:.2f}, 95%).")
        else:
            where = (("both ends of its allowed window (data span ± 0.2 d)" if name == "tc" else
                      f"both ends of its allowed range ({_edge_text(name, lo, 'lo')} to {_edge_text(name, hi, 'hi')})")
                     if len(sides) == 2 else _edge_text(name, sides[0][1], sides[0][0]))
            text = (f"{label} is limited by its prior range: its MCMC samples pile up against {where}, so the error "
                    "bar is not a measurement.")
        out.append({"name": name, "label": label, "sides": [sd[0] for sd in sides], "edges": [sd[1] for sd in sides],
                    "frac": max(sd[2] for sd in sides), "text": text})
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
            p1 = tc.TransitProblem(t, flux, err, am, prob.period, prob.priors, tuple(prob.free) + ("c_am", "c_t"),
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
           f"{corr:+.2f}) and absorb the transit together" if same else
           "the airmass and time slopes together absorb the transit")
    alone = "; ".join(f"{'Airmass' if w == 'airmass' else 'time slope'} alone detects it (ΔBIC {d:.0f})"
                      for w, d in hits)
    alone = alone[:1].upper() + alone[1:]
    if len(hits) == 2:
        fix = "Untick one of the two (airmass or time slope) and fit again"
    else:
        fix = "Untick time slope and fit again" if hits[0][0] == "airmass" else "Untick airmass and fit again"
    return f"No detection with Both: {why}. {alone}. {fix}."


# ---- Corner plot ---------------------------------------------------------------------------------------------------

def _transform(name: str, x, tc_med: float):
    if name == "tc":
        return (x - tc_med) * 1440.0
    if name == "step":
        return x * 100.0
    return x


def corner_params(result: dict) -> list:
    """The fitted (free) parameters in corner-plot order; parameters held fixed are left out."""
    free = list(result.get("free_params") or [])
    return [p for p in ORDER if p in free] + [p for p in free if p not in ORDER]


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


def corner_figure(result: dict, fig=None, max_samples: int = 20000, font: float = 8.0):
    """2.2.19 (#31): the corner plot of the MCMC samples on a matplotlib Figure (made here when fig is None, Agg).
    Diagonal: each fitted parameter's histogram with the median and 16/84 % lines, value +err/−err in the title
    (⚠ and an orange title when the parameter piles up against its allowed range). Below the diagonal: 2-D density
    with the 1σ and 2σ contours. Tc is shown in minutes from its median."""
    if fig is None:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        fig = Figure(dpi=100, facecolor="#ffffff")
        FigureCanvasAgg(fig)
    chain = result["chain"]["flat"]
    free = list(result["free_params"])
    names = corner_params(result)
    n = len(names)
    side = min(max(1.25 * n + 0.9, 5.0), 12.5)
    fig.set_size_inches(side, side, forward=False)
    fig.clear()
    rng = np.random.default_rng(5)
    flat = np.asarray(chain, dtype=float)
    if flat.shape[0] > max_samples:
        flat = flat[rng.choice(flat.shape[0], max_samples, replace=False)]
    t_ref = float(result.get("t_ref", 0.0))
    tc_med = float(np.median(flat[:, free.index("tc")])) if "tc" in free else 0.0
    data = {p: _transform(p, flat[:, free.index(p)], tc_med) for p in names}
    flagged = {f["name"] for f in (result.get("bound_flags") or [])}
    ranges = {}
    for p in names:
        lo, hi = np.percentile(data[p], [0.2, 99.8])
        pad = 0.08 * (hi - lo) if hi > lo else (abs(lo) * 0.01 or 1e-6)
        ranges[p] = (lo - pad, hi + pad)
    gs = fig.add_gridspec(n, n, hspace=0.08, wspace=0.08, left=0.09, right=0.98, bottom=0.08, top=0.93)
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
                label = LABELS.get(pi, pi)
                if pi == "tc":
                    title = f"Tc = {tc_med + t_ref:.5f}\n+{q84 - q50:.1f} / −{q50 - q16:.1f} min"
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
                ax.set_xlabel(LABELS.get(pj, pj) + (" (min)" if pj == "tc" else ""), fontsize=font)
            if j > 0 or i == 0:
                if i != j:
                    ax.set_yticklabels([])
            else:
                ax.set_ylabel(LABELS.get(pi, pi) + (" (min)" if pi == "tc" else ""), fontsize=font)
            ax.xaxis.set_major_locator(_locator())
            if i != j:
                ax.yaxis.set_major_locator(_locator())
    name = (result.get("planet") or {}).get("name") or "Transit"
    ch = result["chain"]
    fig.suptitle(f"{name}: MCMC posterior ({ch['walkers']} walkers × {ch['steps']} steps; median and 16/84 % lines)",
                 fontsize=font + 2, y=0.985)
    if flagged:
        fig.text(0.98, 0.955, "⚠ piles up against its allowed range: that error bar is not a measurement",
                 ha="right", va="top", fontsize=font, color=orange)
    return fig


def _locator():
    from matplotlib.ticker import MaxNLocator
    return MaxNLocator(3, prune="both")


def _smooth(h):
    """A light 3×3 smoothing of a 2-D histogram (NumPy only)."""
    k = np.array([0.25, 0.5, 0.25])
    p = np.pad(h, 1, mode="edge")
    p = k[0] * p[:-2, :] + k[1] * p[1:-1, :] + k[2] * p[2:, :]
    return k[0] * p[:, :-2] + k[1] * p[:, 1:-1] + k[2] * p[:, 2:]


def save_corner(result: dict, path: str, dpi: int = 110) -> str:
    """Write the corner plot to path (PNG) and return it."""
    fig = corner_figure(result)
    fig.savefig(path, dpi=dpi, facecolor="#ffffff")
    return path
