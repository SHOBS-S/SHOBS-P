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
import shutil
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

    section("2c 2.2.8: masters saved and reused (real Astropy FITS), star size, centring")
    import tempfile
    tmp = tempfile.mkdtemp(prefix="shobsp_selftest_")
    try:
        from astropy.io import fits as _fits
        folders = {}
        for kind, level, exp in (("bias", 300.0, 0.0), ("dark", 330.0, 30.0), ("flat", 20000.0, 2.0)):
            d = os.path.join(tmp, kind)
            os.makedirs(d)
            folders[kind] = d
            for i in range(4):
                h = _fits.Header()
                h["EXPTIME"] = exp
                h["GAIN"] = 100
                h["DATE-OBS"] = "2026-10-0%dT03:00:00" % (1 + i // 2)
                h["ROTATOR"] = 30.0
                _fits.PrimaryHDU((level + rng.normal(0, 3, (32, 48))).astype(np.uint16), header=h).writeto(
                    os.path.join(d, f"{kind}_{i}.fits"))
        frames = {k: core.calibration_source(v)[0] for k, v in folders.items()}
        logs = []
        m1 = core.prepare_masters(frames["bias"], frames["dark"], frames["flat"], 1, logs.append, version=ca.APP_VERSION)
        sub = [os.path.join(v, core.MASTER_DIR) for v in folders.values()]
        saved = sorted(f for d in sub if os.path.isdir(d) for f in os.listdir(d) if f.startswith(core.MASTER_PREFIX))
        check("three masters written in the 'SHOBS-P masters' subfolders (2.2.13)", len(saved) == 3, ", ".join(saved))
        frames = {k: core.calibration_source(v)[0] for k, v in folders.items()}
        check("saved masters are not counted as frames", all(len(v) == 4 for v in frames.values()))
        logs.clear()
        m2 = core.prepare_masters(frames["bias"], frames["dark"], frames["flat"], 1, logs.append, version=ca.APP_VERSION)
        check("second run reuses all three", sum("Using saved master" in t for t in logs) == 3, " | ".join(logs[:3]))
        check("reused flat equals the built one", bool(np.allclose(m1.flat, m2.flat, equal_nan=True)))
        mf = os.path.join(folders["flat"], core.MASTER_DIR, core.master_file_name("flat", 1))
        h = core.read_header(mf)
        check("master stamp readable", core.master_stamp(h) == "FLAT" and int(h.get("NFRAMES")) == 4, str(h.get("SHOBSP")))
        check("a master picked directly is accepted", core.check_master_file(mf, "flat") == "")
        alien = os.path.join(tmp, "other_master_flat.fits")
        _fits.PrimaryHDU(np.ones((32, 48), np.float32)).writeto(alien)
        check("another program's master is refused", core.check_master_file(alien, "flat") != "")
    except Exception as exc:
        check("masters saved and reused", False, str(exc).splitlines()[0] if str(exc) else repr(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    yy2, xx2 = np.mgrid[0:200, 0:200]
    field = rng.normal(100, 5, (200, 200))
    for (sx, sy, a) in ((40, 40, 5000), (150, 60, 3000), (60, 150, 4000), (140, 150, 6000), (100, 100, 2500)):
        field += a * np.exp(-((xx2 - sx) ** 2 + (yy2 - sy) ** 2) / (2 * (3.0 / 2.3548) ** 2))
    fw = core.field_fwhm(field)["fwhm"]
    check("seeing measured on bright isolated stars (3.0 px)", abs(fw - 3.0) < 0.3, f"{fw:.2f}")
    pair = rng.normal(100, 5, (100, 100))
    yy3, xx3 = np.mgrid[0:100, 0:100]
    pair += 300 * np.exp(-((xx3 - 50.3) ** 2 + (yy3 - 50.2) ** 2) / (2 * 0.95 ** 2))
    pair += 900 * np.exp(-((xx3 - 46.0) ** 2 + (yy3 - 54.5) ** 2) / (2 * 0.95 ** 2))
    info = core.recenter_info(pair, 50.0, 49.0, 8)
    check("a click stays on the clicked star beside a brighter one", abs(info["x"] - 50.3) < 0.6 and abs(info["y"] - 50.2) < 0.6,
          f"({info['x']:.1f}, {info['y']:.1f})")

    section("2d 2.2.9: header optics, short-exposure stack, transit window")
    try:
        from astropy.io import fits as _fits
        hu = _fits.Header()
        hu["TELESCOP"] = "eVscope v2.0"
        hu["ORIGIN"] = "Unistellar"
        hu["OBSGEO-B"] = 33.38
        hu["OBSGEO-L"] = -111.96
        hu["OBSGEO-H"] = 339.0
        opt = core.header_optics(hu)
        check("Unistellar header recognized", core.is_unistellar(hu))
        check("Unistellar optics 450 mm / 2.9 um / 114 mm",
              opt.get("focal_mm") == 450 and abs(float(opt.get("pixel_um", 0)) - 2.9) < 1e-6
              and float(opt.get("aperture_mm", 0)) == 114, str({k: v for k, v in opt.items() if k != "source"}))
        site = core.header_site(hu)
        check("site from OBSGEO keywords", site is not None and abs(site[0] - 33.38) < 1e-6, str(site))
        hz = _fits.Header()
        hz["FOCALLEN"] = 2563.0
        hz["XPIXSZ"] = 3.76
        hz["FOCRATIO"] = 7.2
        oz = core.header_optics(hz)
        check("focal, pixel and aperture from FOCALLEN/XPIXSZ/FOCRATIO",
              oz.get("focal_mm") == 2563 and abs(float(oz.get("aperture_mm", 0)) - 356) < 1, str(oz.get("aperture_mm")))
    except Exception as exc:
        check("header optics", False, str(exc).splitlines()[0] if str(exc) else repr(exc))
    yy4, xx4 = np.mgrid[0:150, 0:200]
    pts4 = [(rng.uniform(10, 190), rng.uniform(10, 140), 10 ** rng.uniform(1.3, 2.6)) for _ in range(40)]
    shots = []
    for k in range(20):
        img = rng.normal(0, 40, (150, 200))
        for (sx, sy, a) in pts4 + [(100, 75, 3000)]:
            img += a * np.exp(-((xx4 - sx - k % 4) ** 2 + (yy4 - sy) ** 2) / (2 * 1.2 ** 2))
        shots.append(img.astype(np.float32))
    n1 = len(core.detect_sources(shots[0]))
    n20 = len(core.detect_sources(core.quick_stack(shots)))
    check("a stack of short frames shows more stars than one", n20 > n1, f"{n1} -> {n20}")
    pl = {"period": 3.092926, "t0": 2461323.81372, "t14": 0.95 / 24}
    w1 = tc.transit_window_check(2461322.6925, 2461322.6994, pl)
    check("no transit predicted during a 10-minute run (HD 219134 b, 9 Oct)", w1 is not None and not w1["overlap"],
          f"{w1['hours']:.1f} h away" if w1 else "None")
    check("no ephemeris gives no window check", tc.transit_window_check(1, 2, {"period": 3}) is None)

    section("2e 2.2.12: detection test (real photutils), DTG, scatter curve, Mac helpers")
    try:
        rng5 = np.random.default_rng(12)
        img5 = rng5.normal(1000, 20, (120, 160))
        yy5, xx5 = np.mgrid[0:120, 0:160]
        img5 += 20000 * np.exp(-((xx5 - 40) ** 2 + (yy5 - 60) ** 2) / (2 * 2.0 ** 2))
        flux5, sky5, noise5 = core.measure_many(img5, np.array([[40.0, 60.0], [120.0, 60.0]]), 6, 10, 16, noise=True)
        mags5 = core.detected_mags(flux5, 10.0, noise5)
        check("sky noise measured by photutils", bool(np.all(np.isfinite(noise5)) and np.all(noise5 > 0)), str(noise5))
        check("star detected, empty sky left out", bool(np.isfinite(mags5[0]) and np.isnan(mags5[1])), str(mags5))
    except Exception as exc:
        check("detection test", False, str(exc).splitlines()[0] if str(exc) else repr(exc))
    import re as _re
    check("DTG stamp like 121153Z OCT 26", _re.fullmatch(r"\d{6}Z [A-Z]{3} \d{2}", core.dtg()) is not None, core.dtg())
    m5 = np.concatenate([np.linspace(8.7, 10.5, 12), np.linspace(11, 16, 150)])
    s5 = 0.004 + 0.002 * 10 ** (0.4 * (m5 - 13)).clip(0)
    e5 = core.typical_scatter(m5, s5, np.ones(m5.size, bool))
    check("bright stars on the typical-scatter curve", bool(np.all(np.abs(s5[:12] / e5[:12] - 1) < 0.35)))
    check("font names set for this computer", bool(core.UI_FONT and core.MONO_FONT), f"{core.UI_FONT} / {core.MONO_FONT}")
    check("wheel: any scroll moves at least one step", core.wheel_units(3) != 0 and core.wheel_units(-120) == 1)

    section("2f 2.2.13: catalog merge, proper-motion epoch (real Astropy Time), constrained plots")
    try:
        ep = core.header_epoch_year({"DATE-OBS": "2026-10-13T03:00:00"})
        check("epoch from DATE-OBS", ep is not None and abs(ep - 2026.78) < 0.01, str(ep))
        core.PM_EPOCH_YEAR = 2026.8
        pm6 = (2074.5, 294.8)
        now6 = (348.34919, 57.17055)
        yrs = 26.8
        j2000 = (now6[0] - pm6[0] * yrs / 3.6e6 / np.cos(np.radians(now6[1])), now6[1] - pm6[1] * yrs / 3.6e6)
        merged6 = core.merge_catalogs({
            "Gaia DR3": [{"auid": "Gaia DR3 1", "ra": now6[0], "dec": now6[1], "mags": {"V": 5.57}, "bright": 5.2,
                          "pm": pm6}],
            "AAVSO sequence": [{"auid": "000-BBC-222", "label": "56", "ra": j2000[0], "dec": j2000[1],
                                "mags": {"V": 5.57}, "chartid": "X1"}]}, "V")
        check("high proper-motion star: J2000 sequence position matched to Gaia's star now",
              len(merged6) == 1 and merged6[0]["seq"] is not None, str(len(merged6)))
    except Exception as exc:
        check("catalog merge", False, str(exc).splitlines()[0] if str(exc) else repr(exc))
    finally:
        core.PM_EPOCH_YEAR = None
    try:
        from matplotlib.figure import Figure as _Fig
        try:
            fig6 = _Fig(figsize=(6, 3), layout="constrained")
        except TypeError:
            fig6 = _Fig(figsize=(6, 3), constrained_layout=True)
        check("constrained-layout figures available (Comp health margins)",
              fig6.get_layout_engine() is not None if hasattr(fig6, "get_layout_engine") else True)
    except Exception as exc:
        check("constrained-layout figure", False, str(exc).splitlines()[0] if str(exc) else repr(exc))

    section("2g 2.2.14: time of maximum and O-C (real Astropy HJD), binned rms")
    try:
        # SZ Lyn, 14 Oct 2026: RA 08h09m35.8s, Dec +44 28' 17"; HJD - JD about -0.53 min that morning.
        jd_max = 2461327.90043 + 0.53 / 1440.0
        tt = core.extremum_times(jd_max, 122.39917, 44.47139, 35.3, -81.9)
        check("SZ Lyn HJD - JD about -0.53 min", abs((tt["hjd"] - jd_max) * 1440 + 0.53) < 0.03,
              f"{(tt['hjd'] - jd_max) * 1440:+.3f} min")
        oc = core.o_minus_c(tt["hjd"], 0.12053525, 2453766.839)
        check("SZ Lyn cycle 62729, O-C about +8.3 min", oc["cycle"] == 62729 and abs(oc["oc_min"] - 8.3) < 0.2,
              f"E {oc['cycle']}, O-C {oc['oc_min']:+.2f} min")
    except Exception as exc:
        check("time of maximum", False, str(exc).splitlines()[0] if str(exc) else repr(exc))
    try:
        import transit_core as _tc
        tb = 2461300.6 + np.arange(240) / 1440.0
        rb = np.random.default_rng(7).normal(0, 0.003, tb.size)
        b10 = _tc.binned_rms(tb, rb, 10.0)
        check("10-min bin rms about rms / sqrt(10)", b10 is not None and 0.6 < b10 / (0.003 / np.sqrt(10)) < 1.5,
              f"{b10}")
    except Exception as exc:
        check("binned rms", False, str(exc).splitlines()[0] if str(exc) else repr(exc))

    section("2h 2.2.15: planet windows in BJD (real Astropy), pier sides, display stretch")
    try:
        import transit_core as _tc
        # HD 219134, 12-13 Oct 2026: BJD_TDB - JD about +5.9 min; planet b's predicted ingress 05:17 EDT (09:17 UT).
        jd0 = np.array([2461326.88])
        bjd = core.convert_times(jd0, "BJD_TDB", 348.34919, 57.17055, 35.0, -80.0)
        check("HD 219134 BJD - JD about +5.9 min", abs((bjd[0] - jd0[0]) * 1440 - 5.94) < 0.1,
              f"{(bjd[0] - jd0[0]) * 1440:+.2f} min")
        pb = {"name": "HD 219134 b", "period": 3.0929386, "t14": 0.945 / 24, "t0": None}
        win = _tc.transit_windows(2461326.5, 2461327.0, {**pb, "t0": 2461326.8854 + 3.0929386 * 5})
        check("transit window found from an ephemeris five orbits away", len(win) == 1, f"{len(win)} window(s)")
    except Exception as exc:
        check("planet windows", False, str(exc).splitlines()[0] if str(exc) else repr(exc))
    try:
        g = core.pier_side_groups([29, 30, 30, 26, 34, 210, 209, 210])
        check("29/30/26/34 are one side, 209/210 the other", len({g[a] for a in (26, 29, 30, 34)}) == 1
              and g[209] == g[210] and g[30] != g[210], str(g))
        img = np.random.default_rng(1).normal(100, 5, (64, 64)).astype(np.float32)
        before = img.copy()
        for mode in ("linear", "log", "asinh"):
            d = core.stretch_display(img, {"mode": mode, "black": 5.0, "white": 99.5})
        check("display stretch 0..1 and the data untouched", float(d.min()) >= 0 and float(d.max()) <= 1
              and np.array_equal(img, before))
    except Exception as exc:
        check("pier sides / stretch", False, str(exc).splitlines()[0] if str(exc) else repr(exc))

    section("2i 2.2.16: VizieR markers, mount names, comp odd-one-out")
    try:
        import transit_core as _tc
        col = "#Column\te_Vmag\t(F6.3)\t[0/7]? Vmag uncertainty\t[ucd=stat.error;phot.mag]\n"
        check("APASS column description is not an error marker", not core.vizier_markers(col))
        check("a VizieR error line is", bool(core.vizier_markers("#++++ Error: query aborted\n")))
        check("'EQMod Mount' is a mount, not a telescope", core.is_mount_name("EQMod Mount")
              and not core.is_mount_name("Celestron RC 14"))
        check("one bad comp of three is the odd one out", _tc._odd_one_out([0.30, -0.15, -0.15]) == 0
              and _tc._odd_one_out([0.3, -0.3]) is None)
    except Exception as exc:
        check("2.2.16 checks", False, str(exc).splitlines()[0] if str(exc) else repr(exc))

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
