"""2.4.0 fit-in F4, package F4-1 (a): one comp set per night (#79 item 4, m218), Comp health names an over-linear
star, the sky gain's saturation cut on the raw pixels, the mixed-angle Flat folder (m167)."""
import math
import os
import sys
import tempfile
import types
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photometry_core as core    # noqa: E402
import sky_gain as sg             # noqa: E402
import transit_core as tc         # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(name="clearv_app.py"):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def _obs(f, comps, target=-9.2, night="N", peaks=None, kmag=None):
    o = core.Obs(jd=2461000.0 + f * 0.001, mag=10.0, merr=0.01, cmag=0.0, kmag=kmag, airmass=None, path=f"f{f}",
                 exptime=60.0, flux_var=0.0, flux_comp=0.0, night=night)
    o.comp_insts = [float(v) for v in comps]
    o.target_inst = float(target)
    o.target_var = 1e-6
    o.comp_vars = [1e-6] * len(comps)
    if peaks is not None:
        o.raw_peaks = dict(peaks)
    return o


NAMES = ["A", "B", "C"]
CAT = {"A": 10.0, "B": 10.5, "C": 11.4}


class TestOneCompSetPerNight(unittest.TestCase):
    def test_comp_off_the_frame_in_one_frame_is_out_all_night(self):
        pts = [_obs(f, [-9.0, -8.5, -7.6 if f != 17 else float("nan")]) for f in range(40)]
        r = core.recompute_with_comps(pts, NAMES, NAMES, CAT)
        self.assertEqual(r["night_out"], {"N": ["C"]})
        self.assertEqual(r["night_use"], {"N": ["A", "B"]})
        self.assertEqual(r["partial"], 0)
        self.assertIn("missing from 1 of 40 frames", r["night_why"]["N"]["C"])
        # every frame is the plain mean of A and B: the same set from first to last
        want = (10.0 + 10.5) / 2 + (-9.2) - (-9.0 - 8.5) / 2
        for o in pts:
            self.assertAlmostEqual(o.mag, want, places=12)
            self.assertEqual(o.n_comps, 2)
            self.assertNotIn("nocomp", o.flag)

    def test_comp_over_the_linear_limit_in_one_frame_is_out_all_night(self):
        pts = [_obs(f, [-9.0, -8.5, -7.6], peaks={"A": 30000, "B": 20000, "C": 61000 if f == 3 else 50000})
               for f in range(30)]
        r = core.recompute_with_comps(pts, NAMES, NAMES, CAT, linear_limit=60000.0)
        self.assertEqual(r["night_out"], {"N": ["C"]})
        self.assertEqual(r["night_why"]["N"]["C"], "over the linear limit in 1 frame")
        self.assertAlmostEqual(pts[0].mag, 10.25 + (-9.2) - (-8.75), places=12)
        # no limit given: nothing is left out (the saturation flag is the only limit, as before)
        r2 = core.recompute_with_comps(pts, NAMES, NAMES, CAT)
        self.assertEqual(r2["night_out"], {})

    def test_each_night_has_its_own_set(self):
        pts = [_obs(f, [-9.0, -8.5, float("nan") if f == 2 else -7.6], night="N1") for f in range(10)]
        pts += [_obs(f, [-9.0, -8.5, -7.6], night="N2") for f in range(10)]
        r = core.recompute_with_comps(pts, NAMES, NAMES, CAT)
        self.assertEqual(r["night_out"], {"N1": ["C"]})
        self.assertEqual(r["night_use"]["N2"], NAMES)
        self.assertEqual({o.n_comps for o in pts if o.night == "N2"}, {3})

    def test_lines_plain_until_too_few_comps_are_left(self):
        pts = [_obs(f, [-9.0, -8.5, -7.6 if f else float("nan")]) for f in range(12)]
        r = core.recompute_with_comps(pts, NAMES, NAMES, CAT)
        lines = core.night_out_lines(r)
        self.assertEqual(len(lines), 1)
        self.assertFalse(lines[0].lstrip().startswith("⚠"), lines)
        self.assertIn("C: missing from 1 of 12 frames", lines[0])
        self.assertIn("The night uses A, B in every frame.", lines[0])
        # two comps chosen, one out: one comp left = fewer than the minimum: ⚠ with what to do
        pts = [_obs(f, [-9.0, -8.5 if f else float("nan")]) for f in range(12)]
        r = core.recompute_with_comps(pts, ["A", "B"], ["A", "B"], CAT)
        line = core.night_out_lines(r)[0]
        self.assertTrue(line.startswith("⚠"), line)
        self.assertIn("That leaves only A", line)
        self.assertIn("Use comps…", line)
        self.assertEqual(core.NIGHT_SET_MIN, 2)

    def test_no_comp_in_every_frame_stands_on_the_best_and_warns(self):
        pts = [_obs(f, [-9.0 if f > 1 else float("nan"), -8.5 if f < 3 else float("nan")]) for f in range(20)]
        r = core.recompute_with_comps(pts, ["A", "B"], ["A", "B"], CAT)
        self.assertEqual(r["night_few"], {"N": ["A"]})
        self.assertEqual(r["missing"], 2)
        self.assertTrue(all("nocomp" in o.flag for o in pts[:2]))
        self.assertTrue(core.night_out_lines(r)[0].startswith("⚠"))

    def test_a_frame_without_the_target_is_no_evidence(self):
        # a frame where nothing was measured does not put every comp out
        pts = [_obs(f, [-9.0, -8.5, -7.6]) for f in range(10)]
        pts[4].comp_insts = [float("nan")] * 3
        pts[4].target_inst = float("nan")
        r = core.recompute_with_comps(pts, NAMES, NAMES, CAT)
        self.assertEqual(r["night_out"], {})
        self.assertEqual(r["missing"], 1)

    def test_compsat_flag_comes_off_when_the_comp_is_out(self):
        pts = [_obs(f, [-9.0, -8.5, -7.6], peaks={"A": 30000, "B": 20000, "C": 65535 if f == 3 else 40000})
               for f in range(8)]
        pts[3].flag, pts[3].saturated = "compsat", True
        n = core.clear_out_comp_flags(pts, ["A", "B"], NAMES, 65000.0)
        self.assertEqual(n, 1)
        self.assertEqual(pts[3].flag, "")
        self.assertFalse(pts[3].saturated)
        pts[3].flag = "compsat"
        self.assertEqual(core.clear_out_comp_flags(pts, NAMES, NAMES, 65000.0), 0)   # C in use: the flag stays

    def test_transit_set_is_the_comps_in_every_frame(self):
        rng = np.random.default_rng(5)
        cm = np.array([-1.0, 0.0, 0.7])[None, :] + rng.normal(0, 0.01, (50, 3))
        cm[30:45, 2] = np.nan
        ens = tc.set_ensemble(cm)
        np.testing.assert_allclose(ens, cm[:, :2].mean(axis=1), atol=1e-12)
        # in step with the photometry's rule
        pts = [_obs(f, cm[f]) for f in range(50)]
        ref = core.night_ensemble(pts, NAMES, NAMES)["ens"]
        np.testing.assert_allclose(ens, np.array(ref), atol=1e-12)
        self.assertIs(tc.tied_ensemble, tc.set_ensemble)

    def test_the_tie_in_is_gone_from_the_code(self):
        core_src = _src("photometry_core.py")
        body = core_src[core_src.index("def night_ensemble("):core_src.index("def recompute_with_comps(")]
        self.assertNotIn("np.median(mk[", body)   # no per-comp offset any more
        self.assertIn("night_set(", body)
        app = _src()
        self.assertIn("RESULTS-CHANGING", app[app.index("if any(o.comp_drop for o in night_points) or over_lin:"):][:400])
        # the page, the settings file and the Transit fit read one record
        self.assertIn('"night_out", "night_why", "night_use", "night_few", "flip_like"', app)


class TestCompHealthOverLinear(unittest.TestCase):
    def test_over_linear_star_is_not_steady(self):
        rng = np.random.default_rng(2)
        pts = []
        for night in ("N1", "N2", "N3"):
            for f in range(30):
                c = [-9.0 + rng.normal(0, 0.002), -8.5 + rng.normal(0, 0.002), -7.6 + rng.normal(0, 0.002)]
                o = _obs(f, c, night=night, peaks={"A": 61000 if f < 4 else 40000, "B": 20000, "C": 30000})
                o.jd += {"N1": 0, "N2": 1, "N3": 2}[night]
                pts.append(o)
        res = core.comp_health(pts, NAMES, linear_limit=60000.0)
        rows = {r["label"]: r for r in res["summary"]}
        self.assertEqual(rows["A"]["over_linear"], (12, 90))
        self.assertTrue(rows["A"]["verdict"].startswith("over the linear limit in 12 of 90 frames"), rows["A"])
        self.assertNotIn("steady", rows["A"]["verdict"])
        self.assertEqual(rows["A"]["verdict_before_linear"], "steady")
        self.assertEqual(rows["B"]["verdict"], "steady")
        res = core.comp_health(pts, NAMES, linear_limit=60000.0, limit_word="the saturation level")
        self.assertTrue(res["summary"][0]["verdict"].startswith("over the saturation level in 12"))


class TestSkyGainRawSaturationCut(unittest.TestCase):
    def test_raw_over_maps_raw_pixels_to_image_pixels(self):
        raw = np.zeros((8, 12), dtype=np.float32)
        raw[3, 5] = 60000
        over = sg.raw_over(raw, 60000, (4, 6))
        self.assertEqual(over.shape, (4, 6))
        self.assertTrue(over[1, 2])
        self.assertEqual(int(over.sum()), 1)
        self.assertIs(sg.raw_over(raw, 60000, (3, 5)), None)
        self.assertTrue(sg.raw_over(raw, 60000, (8, 12))[3, 5])

    def test_cut_is_made_on_raw_values_not_dark_subtracted_ones(self):
        from astropy.io import fits
        rng = np.random.default_rng(9)
        h, w = 120, 160
        raw = rng.normal(3000.0, 30.0, (h, w))
        raw[40:44, 60:64] = 60500.0          # at the raw limit 60,000 ...
        dark = np.full((h, w), 1500.0, dtype=np.float32)   # ... but 59,000 once the dark is taken off
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "light.fits")
            hdr = fits.Header()
            hdr["EXPTIME"] = 60.0
            fits.writeto(p, np.clip(raw, 0, 65535).astype(np.uint16), hdr)
            m = core.Masters(dark=dark)
            old = sg.window_loader(1, m, core.MONO, "luminance")(p)
            self.assertFalse(sg.window_loader(1, m, core.MONO, "luminance").raw_sat)
            patch_old = old[40:44, 60:64]
            self.assertTrue(np.isfinite(patch_old).all() and (patch_old < 60000).all())   # slipped through before
            load = sg.window_loader(1, m, core.MONO, "luminance", sat=60000.0)
            self.assertTrue(load.raw_sat)
            new = load(p)
            self.assertTrue(np.isnan(new[40:44, 60:64]).all())
            self.assertEqual(int(np.isnan(new).sum()), 16)
            prep = sg.prep_frame(new, 60000.0, cut=False)
            self.assertTrue(prep["mask"][40:44, 60:64].all())

    def test_a_file_with_no_raw_pixels_keeps_the_old_cut(self):
        img = np.full((50, 50), 100.0, dtype=np.float32)
        img[10, 10] = 61000.0
        load = sg.window_loader(1, core.Masters(), core.MONO, "luminance", full_load=lambda p: img,
                                whole=lambda p: True, sat=60000.0)
        out = load("stack.fits")
        self.assertTrue(np.isnan(out[10, 10]))

    def test_app_hands_the_limit_to_the_loader(self):
        self.assertIn("whole=whole, sat=sat)", _src())
        self.assertIn('prep_frame(img, sat, cut=False) if getattr(load, "raw_sat", False)', _src("sky_gain.py"))


if __name__ == "__main__":
    unittest.main()
