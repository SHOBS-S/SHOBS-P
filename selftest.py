"""SHOBS-P self-test (2.2.3; 2.2.5 and 2.2.6 checks added). Double-click "Run self-test.bat" in this folder.

Runs with your own Python (the one SHOBS-P uses), so it checks the real Astropy, photutils and Tkinter:
  1. the program files import, and the version;
  2. the 2.2.3 helpers (aperture in mm, flat angles, flat-topped cores, comp errors, same-star names, period
     checks, series setting checks, edge warnings);
  3. a real Lomb-Scargle period search with the leave-one-night-out check;
  4. the transit regression on your AAVSO exoplanet reports, if it can find them
     (W:\\Exoplanet\\TrES-3 b, KOI-217 b, Wasp-12 b), expecting detected / detected / none;
  5. the window opens and closes (Tkinter), with nothing clicked.
It changes nothing: no settings, no data. The log is written next to this file (selftest_log.txt) and to your
Downloads folder (SHOBS-P_selftest_log.txt).
"""
from __future__ import annotations

import glob
import io
import math
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LOG = io.StringIO()
FAILS: list[str] = []


def out(text: str = "") -> None:
    print(text)
    LOG.write(text + "\n")


def check(name: str, cond: bool, info: str = "") -> None:
    out(("  ok    " if cond else "  FAIL  ") + name + (f"   ({info})" if info else ""))
    if not cond:
        FAILS.append(name)


def section(title: str) -> None:
    out("")
    out("== " + title)


def main() -> None:
    out(f"SHOBS-P self-test   {time.strftime('%Y-%m-%d %H:%M')}   Python {sys.version.split()[0]}   {HERE}")
    section("1  imports and version")
    import numpy as np
    import photometry_core as core
    import transit_core as tc
    import clearv_app as ca
    out(f"  SHOBS-P {ca.APP_VERSION}   numpy {np.__version__}")
    try:
        import astropy
        import photutils
        out(f"  astropy {astropy.__version__}   photutils {photutils.__version__}")
    except Exception as exc:
        check("astropy / photutils import", False, str(exc))
    check("version is 2.2.3 or later", tuple(int(x) for x in ca.APP_VERSION.split(".")) >= (2, 2, 3), ca.APP_VERSION)

    section("2  2.2.3 helpers")
    check("aperture 35.6 cm -> 356 mm", core.aperture_mm_from_settings({"aperture_cm": "35.6"}) == "356")
    check("aperture 356 (typed in mm by mistake) kept", core.aperture_mm_from_settings({"aperture_cm": "356"}) == "356")
    check("aperture hint for 35.6 mm", "tiny" in core.aperture_mm_hint("35.6"))
    check("flats 30 and 210 deg are one camera angle", core.rotator_spread([30, 210, 30.5]) <= 1)
    check("flats 30 and 2 deg are two angles", core.rotator_spread([30, 30, 2]) > 20)
    rng = np.random.default_rng(7)
    yy, xx = np.mgrid[0:120, 0:120]
    star = rng.poisson(400 + 20000 * np.exp(-((xx - 60.3) ** 2 + (yy - 59.8) ** 2) / (2 * 3.0 ** 2))).astype(float)
    clipped = np.minimum(rng.poisson(400 + 40000 * np.exp(-((xx - 60.3) ** 2 + (yy - 59.8) ** 2) / (2 * 3.0 ** 2))), 20000.0)
    s = core.Star("t", 59.8, 59.3)
    check("unsaturated star not flat-topped", not core.flat_topped_core(star, s, 8, 1, cfa=False))
    check("clipped star flat-topped", core.flat_topped_core(clipped.astype(float), s, 8, 1, cfa=False))
    o = core.Obs(jd=1, mag=5.5, merr=0.011, cmag=-10.6, kmag=None, airmass=1.2, path="", exptime=0.75, flux_var=1,
                 flux_comp=1, comp_insts=[-10.6], spare_insts={"SP": -10.3}, target_inst=-13.7, comp_cat=8.728,
                 target_var=1e-6, comp_vars=[0.0105 ** 2], n_comps=1)
    e1 = core.ensemble_merr(o, ["C"], ["C"])
    core.recompute_with_comps([o], ["C", "SP"], ["C"], {"C": 8.728, "SP": 9.057})
    check("two comps: smaller photon error, n_comps 2", o.merr < e1 and o.n_comps == 2, f"{e1:.4f} -> {o.merr:.4f}")
    setup = {"aperture_m": 0.356, "altitude_m": 280, "n_comps": 1}
    sc2 = core.scint_mag(o, setup)
    o.n_comps = 1
    check("scintillation follows the comps in use", sc2 < core.scint_mag(o, setup))
    al = core.same_star_aliases({"TYC A": (347.936431, 57.108842), "APASS B": (347.936440, 57.108850)})
    check("same star under two names", al.get("TYC A") == ["APASS B"])
    check("0.967 d flagged as a day alias", bool(core.period_alias_text(0.96726)))
    check("0.695 d not flagged", core.period_alias_text(0.6947) == "")
    diffs = core.night_setup_differences({"binning": 1, "radius": 8}, {"JD1": {"binning": 2, "radius": 8}})
    check("series setting check finds Bin 1 vs Bin 2", len(diffs) == 1 and "Bin 1" in diffs[0])

    class P:
        pass
    pts = []
    for k in range(4):
        p = P()
        p.pos = {"c": [[900, 500], [1164, 62]], "k": [650, 400]}
        pts.append(p)
    edges = core.edge_problems(pts, (1044, 1562), ["C1", "C2"], "K", 66)
    check("edge warning names the comp at the edge only", len(edges) == 1 and "C2" in edges[0])

    section("2b 2.2.5 helpers")
    check("ten comparison-star slots", len(ca.COMP_ROLES) == 10 and ca.role_label("comp10") == "Comp 10")
    res = {"candidates": [0, 1, 2], "vsx": [{"name": "V1 Cas", "type": "EW", "source": "VSX"}, None, None],
           "vsx_sources": ["VSX", "SIMBAD"], "near_sat": [2], "at_boundary": {}}
    kinds = [core.scan_candidate_kind(res, i)[0] for i in range(3)]
    check("candidate kinds: known / new / check first", kinds == ["known", "new", "caution"], str(kinds))
    res["vsx"] = None
    check("candidate with no sky positions is unchecked", core.scan_candidate_kind(res, 1)[0] == "unchecked")

    section("3  period search (real Astropy Lomb-Scargle)")
    t, y, nights = [], [], []
    for n in (2461300, 2461309, 2461311, 2461313, 2461315, 2461320):
        tt = n + 0.52 + np.arange(0, 0.15, 0.007)
        t += list(tt)
        y += list(5.54 + (0.025 if n == 2461300 else 0.0) + rng.normal(0, 0.003, tt.size))
        nights += [f"JD{n}"] * tt.size
    t, y = np.array(t), np.array(y)
    best = core.lomb_scargle(t, y, 0.05, 20.0)
    loo = core.period_leave_one_out(t, y, nights, best, 0.05, 20.0)
    out(f"  best {best['period_days']:.4f} d, FAP {best['fap']:.1e}, carried by {loo['carried_by']}")
    check("one offset night is caught (leave-one-night-out)", loo["carried_by"] == ["JD2461300"])

    section("4  transit regression")
    found = []
    for pattern in (r"W:\Exoplanet\TrES-3 b\**\AAVSO_TrES-3b_04-SEP-2026.txt",
                    r"W:\Exoplanet\KOI-217 b\**\AAVSO_KOI-217b_09-SEP-2026.txt",
                    r"W:\Exoplanet\Wasp-12 b\**\AAVSO_WASP-12b_25-JAN-2015.txt"):
        hits = [h for h in glob.glob(pattern, recursive=True) if "working" not in h.lower()]
        if hits:
            found.append(hits[0])
    if not found:
        out("  (reports not found on W:, skipped)")
    expect = {"TrES-3": "detected", "KOI-217": "detected", "WASP-12": "none"}
    for path in found:
        rep = tc.parse_exoplanet_report(open(path, encoding="utf-8", errors="replace").read())
        pl = tc.planet_from_report(rep)
        res = tc.fit_transit(np.asarray(rep["t"]), np.asarray(rep["flux"]), np.asarray(rep["err"]),
                             rep["airmass"], pl, mcmc_steps=800)
        state = res["verdict"]["state"]
        key = next((k for k in expect if k in os.path.basename(path)), None)
        check(f"{os.path.basename(path)}: {state}", key is None or state == expect[key], f"expected {expect.get(key)}")

    section("4b planet names (2.2.6; needs the internet)")
    names = ("KOI-217 b", "Kepler-71 b", "KOI-217.01", "TrES-3b", "wasp 12 b")
    got = {n: tc.nea_resolve(n) for n in names}
    if all(v is None for v in got.values()):
        out("  (NASA Exoplanet Archive not reachable: skipped)")
    else:
        same = {(got[n] or {}).get("resolved") for n in names[:3]}
        check("KOI-217 b, Kepler-71 b and KOI-217.01 are one planet", len(same) == 1 and None not in same, str(same))
        check("TrES-3b resolves", bool((got["TrES-3b"] or {}).get("resolved")), str((got["TrES-3b"] or {}).get("resolved")))
        check("wasp 12 b resolves", bool((got["wasp 12 b"] or {}).get("resolved")), str((got["wasp 12 b"] or {}).get("resolved")))
        try:
            p = tc.lookup_planet("Kepler-71 b")
            check("Kepler-71 b looks up (period 3.905 d)", abs(p["period"] - 3.905) < 0.01, f"{p['name']} P {p['period']:.5f}")
        except Exception as exc:
            check("Kepler-71 b looks up", False, str(exc).splitlines()[0])
        try:
            t = core.lookup_target("TrES-3 b")
            check("Star ID 'TrES-3 b' finds the star", 17.8 < t["ra_hours"] < 17.9, t.get("note", t.get("source", "")))
        except Exception as exc:
            check("Star ID 'TrES-3 b' finds the star", False, str(exc).splitlines()[0])

    section("5  the window opens and closes")
    try:
        app = ca.App()
        app.update()
        for i in range(5):
            app.show_step(i)
            app.update()
        app._fit_visible_canvases()
        check("main window built, every step shown", True)
        app.show_step(1)
        app._blink_layout()
        check("blink keeps room for the title", app.blink_fig.subplotpars.top < 0.99)
        app.star_id.set("HD 219134")
        check("report name pattern", app._family_names()[0].startswith("AAVSO_HD_219134_")
              and app._family_names()[0].endswith("_SHOBS-P.txt"), app._family_names()[0])
        app.destroy()
    except Exception:
        check("main window", False, traceback.format_exc(limit=3).strip().splitlines()[-1])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        out("")
        out("CRASHED:")
        out(traceback.format_exc())
        FAILS.append("crash")
    out("")
    out("RESULT: " + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: " + "; ".join(FAILS)))
    text = LOG.getvalue()
    for path in (os.path.join(HERE, "selftest_log.txt"),
                 os.path.join(os.path.expanduser("~"), "Downloads", "SHOBS-P_selftest_log.txt")):
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            pass
