"""2.4.0 step 6b: the robustness check (#35), the comp survey's flip step measured as the fit measures it, its busy
words, one display name per star (#72), a loaded series whose comps are all placed skips the survey, and the
message nits (detrend labels, Rp/R* in the Log, the stacking line, a spare near the edge only in the log file)."""
import math
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import matplotlib  # noqa: E402

matplotlib.use("Agg")

import comp_survey as cs  # noqa: E402
import stack_core  # noqa: E402
import transit_core as tc  # noqa: E402

try:
    import tkinter
    tkinter.Tk().destroy()
    HAVE_TK = True
except Exception:   # pragma: no cover
    HAVE_TK = False
try:
    from astropy.io import fits
    from astropy.time import Time
except Exception:   # pragma: no cover
    fits = None

PLANET = tc.complete_planet({"period": 2.0, "t0": 2461000.70, "k": 0.1, "a_rs": 8.0, "b": 0.3, "teff": 5600})


def night(n=200, depth_step=0.0, seed=4, noise=0.002):
    rng = np.random.default_rng(seed)
    t = 2461000.62 + np.arange(n) * 0.0008
    z = tc.separation(t, 2461000.70, 2.0, 8.0, 0.3)
    f = tc.occultation(z, 0.1, 0.45, 0.25)
    after = t > 2461000.71
    f = f * (1 + depth_step * after) + rng.normal(0, noise, n)
    am = 1.2 + 40 * (t - t.mean()) ** 2
    return t, f, np.full(n, noise), am, after


class TestRobustnessCore(unittest.TestCase):
    def test_refits_and_plain_note_when_steady(self):
        t, f, e, am, after = night()
        rng = np.random.default_rng(1)
        cm = 10 + rng.normal(0, 0.0005, (t.size, 3))
        summary = {"depth": (0.01, 0.0008), "tc": (2461000.70, 0.0012)}
        ap = [("aperture", "aperture r 3.2 px", 1 + rng.normal(0, 3e-4, t.size)),
              ("sky", "sky ring 9.5–14.5 px", 1 + rng.normal(0, 3e-4, t.size))]
        rob = tc.robustness_check(t, f, e, am, PLANET, summary, (True, False, False), after_flip=after,
                                  flip_step=False, comp_mags=cm, comp_names=["A", "B", "C"], measured=ap)
        groups = {r["group"] for r in rob["rows"]}
        self.assertEqual(groups, {"detrend", "trim", "comp", "aperture", "sky"})
        self.assertEqual(sum(r["group"] == "comp" for r in rob["rows"]), 3)
        # every other choice that Auto's own BIC table (made here: no table given) calls reasonable
        n_det = sum(r["group"] == "detrend" for r in rob["rows"])
        self.assertEqual(n_det + sum(x.startswith("detrend ") for x in rob["left_out"]), 5)
        self.assertGreaterEqual(n_det, 3)
        self.assertFalse([f_ for f_ in rob["flags"] if f_[0] == "warn"])
        self.assertIn("across choices (formal ± 0.080 %)", rob["note"])
        self.assertIn("aperture ±20 %", rob["note"])
        trims = [r for r in rob["rows"] if r["group"] == "trim"]
        self.assertTrue(all(r["n"] == t.size - 20 for r in trims))
        self.assertTrue(math.isfinite(rob["spread"]["rms_lo"]))
        self.assertIn("+/-", tc.robustness_text(rob, ascii_only=True))
        self.assertTrue(all(ord(c) < 128 for c in tc.robustness_text(rob, ascii_only=True)))

    def test_warn_names_the_choice_that_moves_it(self):
        t, f, e, am, after = night()
        # a comp that brightens across the night: leaving it out moves the depth a lot
        cm = np.column_stack([np.full(t.size, 10.0), np.full(t.size, 10.0),
                              10.0 - 0.02 * np.clip((t - 2461000.68) / 0.03, 0, 1)])
        summary = {"depth": (0.01, 0.0002), "tc": (2461000.70, 0.0012)}
        rob = tc.robustness_check(t, f, e, am, PLANET, summary, (True, False, False), comp_mags=cm,
                                  comp_names=["A", "B", "C"])
        warns = [x for lv, x in rob["flags"] if lv == "warn"]
        self.assertTrue(any("depth moves more across reasonable reduction choices" in w for w in warns), warns)
        self.assertEqual(rob["most"]["depth"]["label"], "C left out")
        self.assertTrue(rob["spread"]["depth"] > rob["formal"]["depth"])

    def test_unreasonable_detrend_choices_left_out(self):
        t, f, e, am, _a = night()
        rows = [{"key": "airmass", "dbic": 0.0}, {"key": "none", "dbic": 25.0}, {"key": "slope", "dbic": 3.0},
                {"key": "curve", "dbic": 4.0}, {"key": "airmass+slope", "dbic": 1.5},
                {"key": "airmass+curve", "dbic": 12.0}]
        rob = tc.robustness_check(t, f, e, am, PLANET, {"depth": (0.01, 0.001), "tc": (2461000.7, 0.001)},
                                  (True, False, False), detrend_rows=rows)
        labels = [r["label"] for r in rob["rows"] if r["group"] == "detrend"]
        self.assertEqual(labels, ["detrend time slope", "detrend time curve (quadratic)",
                                  "detrend airmass + time slope"])
        self.assertTrue(any("detrend none (ΔBIC +25)" in x for x in rob["left_out"]))
        self.assertIn("Not tried (not reasonable choices here)", rob["note"])

    def test_flagged_frames_both_ways(self):
        t, f, e, am, after = night()
        summary = {"depth": (0.01, 0.001), "tc": (2461000.7, 0.001)}
        mask = np.zeros(t.size, bool)
        mask[50:60] = True
        rob = tc.robustness_check(t, f, e, am, PLANET, summary, (True, False, False), flagged_mask=mask)
        row = [r for r in rob["rows"] if r["group"] == "flagged"][0]
        self.assertEqual(row["label"], "the 10 flagged frames left out")
        self.assertEqual(row["n"], t.size - 10)
        fl = {"t": t[:8] + 0.0001, "flux": f[:8], "err": e[:8], "airmass": am[:8], "after_flip": np.zeros(8, bool)}
        rob = tc.robustness_check(t, f, e, am, PLANET, summary, (True, False, False), flagged=fl)
        row = [r for r in rob["rows"] if r["group"] == "flagged"][0]
        self.assertEqual(row["label"], "the 8 flagged frames put back")
        self.assertEqual(row["n"], t.size + 8)

    def test_aperture_variants(self):
        v = tc.aperture_variants(8.0, 14.0, 22.0)
        self.assertEqual([x[0] for x in v], ["aperture", "aperture", "sky"])
        self.assertAlmostEqual(v[0][2], 6.4)
        self.assertAlmostEqual(v[1][2], 9.6)
        self.assertEqual(v[1][3:], (15.6, 23.6))      # the ring moved out with the bigger aperture, same gap
        self.assertEqual(v[2][2:], (8.0, 18.0, 26.0))  # the ring moved out by half its width
        self.assertEqual(tc.aperture_variants(None, 14, 22), [])
        self.assertEqual(tc.aperture_variants(8, 6, 22), [])

    def test_lsq_fit_rms(self):
        t, f, e, am, _a = night(noise=0.003)
        r = tc.lsq_fit(t, f, e, am, PLANET)
        self.assertAlmostEqual(r["rms"], 0.003, delta=0.0006)


class TestSurveyStepLikeTheFit(unittest.TestCase):
    def test_whole_night_rows_and_weights(self):
        idx = np.array([0, 10, 20, 30, 40, 50, 51, 52, 53, 60, 70, 80, 90])
        after = idx >= 52
        usable = np.ones(idx.size, bool)
        usable[[4, 5]] = False                         # in transit
        b, a, w = cs.whole_night_rows(after, idx, 100, usable)
        self.assertEqual(list(b), [0, 1, 2, 3, 6])
        self.assertEqual(list(a), [7, 8, 9, 10, 11, 12])
        self.assertEqual(w[4], 0.0)
        self.assertAlmostEqual(w[0], 5.5)              # frames -0.5 .. 5
        self.assertAlmostEqual(w[3], 10.0)
        self.assertAlmostEqual(w[6], 1.0)              # a frame at the flip stands for itself
        self.assertAlmostEqual(w[12], 14.5)            # frame 90: from 85 out to the end of the night
        b0, a0, w0 = cs.whole_night_rows(np.zeros(5, bool), np.arange(5), 5)   # no flip: nothing
        self.assertEqual((b0.size, a0.size, w0.size), (0, 0, 0))

    def test_weighted_step_matches_the_whole_night(self):
        rng = np.random.default_rng(5)
        n = 232
        fr = np.arange(n)
        after = fr >= 110
        true = 0.006
        lc = rng.normal(0, 0.004, n) + true * after + 0.004 * ((fr > 20) & (fr < 60))   # a bump the sparse frames hit
        every = sorted(set(np.linspace(0, n - 1, 24).round().astype(int)) | set(range(95, 125)))
        idx = np.array(every)
        b, a, w = cs.whole_night_rows(after[idx], idx, n)
        st_w, err = cs.curve_step(lc[idx], b, a, weights=w)
        st_all, _e = cs.curve_step(lc, *cs.whole_night_rows(after, fr, n)[:2], weights=np.ones(n))
        self.assertAlmostEqual(st_w, st_all, delta=2.5 * err)
        self.assertTrue(math.isfinite(err) and err > 0)

    def test_out_of_transit_and_baseline_pass(self):
        model = cs.transit_mags(np.zeros(1), PLANET, tc.occultation)
        t = 2461000.55 + np.arange(300) * 0.001
        base = cs.out_of_transit(t, model, cs.TARGET_STEP_MARGIN)
        t14 = tc.total_duration(2.0, 8.0, 0.3, 0.1)
        inside = np.abs(t - 2461000.70) < t14 / 2
        self.assertFalse(np.any(base & inside))
        self.assertTrue(base[0] and base[-1])
        # the frames read: every 10th, and 30 at a flip right at mid-transit (all in transit)
        read = sorted(set(range(0, 300, 12)) | set(range(135, 165)))
        jd = {i: float(t[i]) for i in read}
        flipped = {i: i >= 150 for i in read}
        more = cs.baseline_pass(300, set(read), jd, flipped,
                                lambda x: cs.out_of_transit(x, model, cs.TARGET_STEP_MARGIN))
        self.assertTrue(more)
        self.assertFalse(set(more) & set(read))
        for lo, hi in ((0, 150), (150, 300)):
            have = [i for i in read + more if lo <= i < hi and base[i]]
            self.assertGreaterEqual(len(have), min(cs.BASELINE_SIDE, int(base[lo:hi].sum())))
        self.assertTrue(all(base[i] for i in more))
        self.assertEqual(cs.baseline_pass(300, set(read), jd, {i: False for i in read}, lambda x: np.ones(len(x),
                                                                                                        bool)), [])


class TestMessageNits(unittest.TestCase):
    def test_detrend_labels(self):
        self.assertEqual(tc.detrend_label("curve"), "time curve (quadratic)")
        self.assertEqual(tc.detrend_label("airmass+curve"), "airmass + time curve (quadratic)")
        self.assertEqual(tc.DETREND_OLD_LABELS["time curve"], "curve")

    def test_stacking_line_names_the_box_and_the_scintillation(self):
        noise = {"sig_t": 0.001, "sig_c": 0.005, "ratio": 5.0, "sig_point": 0.02, "snr_t": 1000, "snr_c": 200,
                 "comp_snr": [200], "n": 50}
        sug = stack_core.suggest_n(noise, 30.0, {"seconds": 600, "known": True, "text": "x"}, 200)
        text, _ = stack_core.suggest_text("variables", noise, sug, {"seconds": 600, "known": True, "text": "x"},
                                          200, None)
        self.assertIn("small beside the scintillation (about 25% of each point's error)", text)
        self.assertIn("type a number of frames in the Stack N box", text)
        self.assertNotIn("Type N", text)
        self.assertNotIn("× the target's", text)
        # Fable S6: comps 1.5x the target's and no scintillation: never "beside the scintillation"
        noise2 = dict(noise, sig_c=0.0015, ratio=1.5, sig_point=math.hypot(0.001, 0.0015))
        sug2 = stack_core.suggest_n(noise2, 30.0, {"seconds": 600, "known": True, "text": "x"}, 200)
        text2, _ = stack_core.suggest_text("variables", noise2, sug2, {"seconds": 600, "known": True, "text": "x"},
                                           200, None)
        self.assertNotIn("scintillation", text2)
        self.assertIn("1.5× the target's, under the 2× at which stacking starts to help", text2)

    def test_param_words(self):
        self.assertEqual(tc.PARAM_WORDS["k"], "Rp/R*")


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestAppPieces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test_240_u.log")
        cls.ca = ca
        cls.app = ca.App()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def test_one_name_for_a_catalog_star(self):
        app = self.app
        app.comp_coords = {}
        st = {"auid": "TYC 5936-2073-1", "label": "TYC 5936-2073-1", "seq": {"auid": "000-BQG-573", "label": "107"},
              "aka": ["Gaia DR3 2991287972537580288"], "ra": 90.9, "dec": -17.05}
        self.assertEqual(app._cat_name(st), "000-BQG-573")
        st2 = {"auid": "APASS J060338.6-170306", "label": "11.5", "aka": ["Gaia DR3 2991287972537580288"],
               "ra": 90.91, "dec": -17.05}
        self.assertEqual(app._cat_name(st2), "Gaia DR3 2991287972537580288")
        self.assertEqual(app._cat_name(None), "")

    def test_series_comps_all_placed(self):
        app = self.app
        app.set_mode("transits")
        roles = self.ca.COMP_ROLES
        old_path = app.series_path
        try:
            app.series_path = ""
            self.assertFalse(app._series_comps_all_placed())
            app.series_path = "x.json"
            app.comp_coords = {"A": (1.0, 2.0), "B": (1.0, 2.1)}
            old_used, app.comps_used = app.comps_used, ["A", "B"]
            old_active, app.active_comps = getattr(app, "active_comps", None), None
            for r in roles + ("check",):
                getattr(app, f"{r}_name").set("")
                setattr(app, f"{r}_xy", None)
            getattr(app, f"{roles[0]}_name").set("A")
            getattr(app, f"{roles[1]}_name").set("B")
            setattr(app, f"{roles[0]}_xy", (10.0, 10.0))
            self.assertFalse(app._series_comps_all_placed())     # B has no marker yet
            setattr(app, f"{roles[1]}_xy", (20.0, 20.0))
            self.assertTrue(app._series_comps_all_placed())
            app.comps_used = ["A", "B", "C"]                      # Fable S4: a series comp in no box: survey runs
            self.assertFalse(app._series_comps_all_placed())
            app.active_comps = ["A", "B"]                         # ... unless Use comps left it out
            self.assertTrue(app._series_comps_all_placed())
            app.comps_used, app.active_comps = ["A", "B"], None
            getattr(app, f"{roles[2]}_name").set("Z")            # a new comp typed in tonight
            setattr(app, f"{roles[2]}_xy", (30.0, 30.0))
            self.assertFalse(app._series_comps_all_placed())
            getattr(app, f"{roles[2]}_name").set("")
            setattr(app, f"{roles[2]}_xy", None)
            app.set_mode("discovery")
            self.assertFalse(app._series_comps_all_placed())
            app.set_mode("transits")
            # a survey reading the frames is dropped when Run is pressed, and said once
            app._survey_cache = {"state": "loading", "tok": None}
            app._survey_skip_said = None
            with mock.patch.object(app, "log") as log:
                self.assertTrue(app._drop_survey_for_series())
                self.assertIsNone(app._survey_cache)
                self.assertTrue(any("Comp survey skipped" in c.args[0] for c in log.call_args_list))
        finally:
            for r in roles + ("check",):
                getattr(app, f"{r}_name").set("")
                setattr(app, f"{r}_xy", None)
            app.comp_coords = {}
            app.series_path = old_path
            app.comps_used, app.active_comps = old_used, old_active

    def test_remeasure_reads_comps_in_the_series_layout(self):        # Fable M1
        import photometry_core as core
        import stack_ui
        app = self.app
        tp = app.transit_page
        yy, xx = np.mgrid[0:100, 0:120]

        def star(x, y, f):
            return f / (2 * math.pi * 2.0 ** 2) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / 8.0)
        img = (100.0 + star(60, 50, 50000) + star(20, 20, 30000) + star(95, 75, 20000)
               + star(30, 80, 10000)).astype(np.float32)

        class O:
            def __init__(self, i):
                self.path = f"f{i}.fits"
                # the series layout A, B, C; A was not measured tonight (None); D is a spare
                self.pos = {"t": [60.0, 50.0], "c": [None, [20.0, 20.0], [95.0, 75.0]], "s": {"D": [30.0, 80.0]}}
        old_used, old_info = app.comps_used, dict(app.night_bin)
        try:
            app.comps_used = ["A", "B", "C"]
            app.night_bin["N1"] = {"radius": 4.0, "sky_in": 8.0, "sky_out": 13.0, "binning": 1,
                                   "comps": ["B", "C"], "pattern": core.MONO, "debayer": "superpixel"}
            app.masters = core.Masters()
            app.binning.set(1)
            obs = [O(i) for i in range(12)]
            plan = tp._robust_measure_plan({"obs": obs, "night": "N1", "names": ["B", "D"]})
            self.assertEqual(plan["why"], "")
            self.assertEqual(plan["cols"], [(1, "B"), (None, "D")])
            with mock.patch.object(stack_ui, "load_frame", return_value=(None, {}, img)):
                out, why = tp._robust_remeasure(plan, object(), mock.Mock(is_set=lambda: False))
            self.assertEqual(why, "")
            self.assertEqual([x[0] for x in out], ["aperture", "aperture", "sky"])
            for _g, _l, fac in out:   # every frame measured (B and D found), and a smooth star: ratios near 1
                self.assertTrue(np.all(np.isfinite(fac)))
                self.assertTrue(np.all(np.abs(fac - 1) < 0.05), fac)
        finally:
            app.comps_used = old_used
            app.night_bin.clear()
            app.night_bin.update(old_info)
            app.masters = None

    def test_survey_words_in_ppt_like_the_fit(self):
        survey = {"index": {"A": 0}, "frames": 59, "of": 232, "flip": True, "flip_seen": True, "flip_why": "",
                  "n_before": 25, "n_after": 34, "radius": 8.5, "radius_asked": 8.5}
        words = self.app._survey_words(survey, -0.0065, picks=[("A", 0.1)], err=0.0013)
        self.assertIn("+6.0 ± 1.2 ppt", words)   # -6.5 mmag = brighter after = +6.0 ppt of flux
        self.assertIn("as the transit fit measures it", words)
        survey["step_how"] = "at_flip"   # Fable S5: no baseline on one side: measured at the flip, and it says so
        self.assertIn("measured at the flip: too little baseline on one side",
                      self.app._survey_words(survey, -0.0065, picks=[("A", 0.1)], err=0.0013))

    def test_robust_button_follows_the_fit(self):
        tp = self.app.transit_page
        tp._robust_src = None
        tp._update_robust_button()
        self.assertIn("disabled", tp.robust_button.state())
        r = {"summary": {}}
        tp.result = r
        tp._robust_src = (r, {"t": []})
        tp._update_robust_button()
        self.assertNotIn("disabled", tp.robust_button.state())
        tp.result = None
        tp._update_robust_button()
        self.assertIn("disabled", tp.robust_button.state())
        tp._robust_src = None


@unittest.skipUnless(HAVE_TK and fits is not None, "needs a display and astropy")
class TestRobustnessOnFiles(unittest.TestCase):
    """End to end: Transits photometry on synthetic frames with a transit and a flip, a Quick fit on the page, then
    Robustness check: every group, the target and comps re-measured at the aperture and sky variations."""

    def test_end_to_end(self):
        import clearv_app as ca
        import photometry_core as core
        from test_230_a import render, TARGET, COMPS, FIELD
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test_240_u_e2e.log")
        app = ca.App()
        app.update()

        def pump(cond, s=600):
            end = time.monotonic() + s
            while time.monotonic() < end:
                app.update()
                if cond():
                    return True
                time.sleep(0.02)
            return False
        tmp = tempfile.mkdtemp()
        lights = os.path.join(tmp, "lights")
        os.makedirs(lights)
        rng = np.random.default_rng(7)
        n, exp, cad = 120, 30.0, 120.0
        third = [(40.0, 80.0, 7000.0)]
        for i in range(n):
            jd = 2461000.62 + i * cad / 86400
            sc_ = float(tc.occultation(tc.separation(np.array([jd]), 2461000.70, 2.0, 8.0, 0.3), 0.1, 0.45, 0.25)[0])
            flipped = i >= 70
            img = render(rng, dx=0.02 * i, dy=0.01 * i, flipped=flipped, target_scale=sc_, field=FIELD + third)
            hdr = fits.Header()
            hdr["EXPTIME"] = exp
            hdr["DATE-OBS"] = Time(jd - exp / 2 / 86400, format="jd", scale="utc").isot
            hdr["EGAIN"] = 1.0
            hdr["PIERSIDE"] = "West" if flipped else "East"
            fits.PrimaryHDU(np.clip(img + 500, 0, 65535).astype(np.uint16), header=hdr).writeto(
                os.path.join(lights, f"l_{i:04d}.fits"))
        try:
            with mock.patch.object(ca.messagebox, "askyesno", return_value=True), \
                    mock.patch.object(ca.messagebox, "askyesnocancel", return_value=False), \
                    mock.patch.object(ca.messagebox, "showinfo"), mock.patch.object(ca.messagebox, "showwarning"), \
                    mock.patch.object(ca.messagebox, "showerror") as err:
                app.set_mode("transits")
                app.light_dir.set(lights)
                app.pattern.set(core.MONO)
                app.binning.set(1)
                app.star_id.set("")
                app.lat.set("35.3")
                app.lon.set("-81.9")
                app.ra_hours.set("6.07")
                app.dec_deg.set("-17.1")
                for var, v in ((app.pl_period, "2.0"), (app.pl_t0, "2461000.70"), (app.pl_k, "0.1"),
                               (app.pl_a_rs, "8.0"), (app.pl_b, "0.3")):
                    var.set(v)
                app.masters = core.Masters()
                app.masters_binning = 1
                first = os.path.join(lights, "l_0000.fits")
                raw, head = core.read_fits(first)
                app.preview, app.preview_header, app.preview_path = raw.astype(np.float32), head, first
                app.preview_is_debayered = True
                app.calibrated = app.preview
                app.radius.set(4.0)
                app.sky_in.set(8.0)
                app.sky_out.set(13.0)
                app.target_xy = (TARGET[0], TARGET[1])
                for role, xy, name in (("comp", COMPS[0][:2], "C1"), ("comp2", COMPS[1][:2], "C2"),
                                       ("comp3", third[0][:2], "C3")):
                    setattr(app, f"{role}_xy", xy)
                    getattr(app, f"{role}_name").set(name)
                    getattr(app, f"{role}_mag").set("11.0")
                app.check_xy = None
                app.check_name.set("")
                app.run_photometry()
                self.assertTrue(pump(lambda: not app.busy and app.observations), "run")
                tp = app.transit_page
                tp.refresh()
                tp.night.set(app.observations[0].night)
                tp.mcmc.set("Quick (1,500 steps)")
                self.assertIn("disabled", tp.robust_button.state())
                tp.fit()
                self.assertTrue(pump(lambda: not app.busy and tp.result is not None
                                     and not getattr(tp, "_checking", False), 900), "fit")
                self.assertNotIn("disabled", tp.robust_button.state())
                tp.robustness_check()
                self.assertTrue(pump(lambda: not app.busy and tp.result.get("robust"), 900), "robustness")
                self.assertFalse(err.called, err.call_args)
                rob = tp.result["robust"]
                groups = {r["group"] for r in rob["rows"]}
                self.assertTrue({"detrend", "trim", "comp", "aperture", "sky"} <= groups, groups)
                self.assertEqual(sum(r["group"] == "comp" for r in rob["rows"]), 3)
                # the re-measured curves really differ from the fitted one (they were measured, not copied)
                ap = [r for r in rob["rows"] if r["group"] in ("aperture", "sky")]
                self.assertEqual(len(ap), 3)
                self.assertTrue(all(abs(r["depth"] - rob["ref"]["depth"]) > 1e-6 for r in ap))
                self.assertIn("Robustness:", tp.prog_text.get())
                self.assertTrue(any(ln.startswith("Robustness check: (") for ln in
                                    tp.settings_lines(tp.meta_for_files, tp.result)))
                self.assertTrue(any(x == rob["note"] for _lv, x in tp.result["flags"]))
                # a second press replaces the first one's lines
                tp.robustness_check()
                self.assertTrue(pump(lambda: not app.busy, 900))
                self.assertEqual(sum(1 for _lv, x in tp.result["flags"] if x.startswith("Robustness check (")), 1)
        finally:
            app.destroy()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
