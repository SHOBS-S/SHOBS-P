"""2.4.0 step 6c: the comp survey's flip-step forecast fitted with a free depth on stars centred as the run centres
them (item a), order-free comp sums so the seeded MCMC repeats (item b), the robustness warning's words (c), a
fit of an opened report saved without the Output page's light curve and ExoClock's info file named after its
upload (d), "reaches the frame edge" (e), and Plot bins that work (f)."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import comp_survey as cs  # noqa: E402
import photometry_core as core  # noqa: E402
import transit_core as tc  # noqa: E402

PLANET = {"period": 2.78, "t0": 2461363.826, "k": 0.116, "a_rs": 8.4, "b": 0.77, "t0_err": 0.0002,
          "period_err": 1e-6}


def flip_night(step_mag=-0.0069, depth_scale=1.05, n=232, seed=3, noise=0.006, flip_frac=0.48):
    """A WASP-49 b-like night: transit mid-night, the flip near mid-transit, a target-minus-set curve with a step
    and a published depth that is 5 % off (as target_diffs leaves it: the predicted transit taken out)."""
    rng = np.random.default_rng(seed)
    t = 2461363.742 + np.arange(n) * 0.00073
    model = cs.transit_mags(t, PLANET, tc.occultation, (0.49, 0.23))
    after = np.arange(n) >= int(flip_frac * n)
    lc = step_mag * after + (depth_scale - 1.0) * model["mag"] + rng.normal(0, noise, n)
    return t, model, after, lc


class TestFreeDepthStep(unittest.TestCase):
    def test_recovers_the_step_with_a_depth_that_is_off(self):
        t, model, after, lc = flip_night()
        usable = cs.contact_free(t, model)
        self.assertTrue(np.any(model["mag"][usable] > 0.005))   # flat-bottom frames are used
        self.assertFalse(np.all(usable))                          # ingress and egress are not
        b, a = np.where(usable & ~after)[0], np.where(usable & after)[0]
        st, er = cs.curve_step(lc, b, a, weights=cs.step_plan(after, model["mag"]))
        self.assertLess(abs(st - (-0.0069)) / er, 3.0)
        self.assertLess(er, 0.002)

    def test_a_survey_sample_matches_every_frame(self):
        t, model, after, lc = flip_night(seed=11)
        usable = cs.contact_free(t, model)
        full = cs.free_depth_step(lc, np.where(usable & ~after)[0], np.where(usable & after)[0],
                                  cs.step_plan(after, model["mag"]))
        fi = int(np.where(after)[0].min())
        rows = np.array(sorted(set(cs.sample_indices(len(t))) | set(range(fi - 15, fi + 15))
                               | set(range(0, 60, 4)) | set(range(len(t) - 60, len(t), 4))))
        sub = cs.free_depth_step(lc[rows], np.where(usable[rows] & ~after[rows])[0],
                                 np.where(usable[rows] & after[rows])[0], cs.step_plan(after[rows], model["mag"][rows]))
        self.assertLess(abs(sub[0] - full[0]), 2.5 * sub[1])

    def test_one_bad_frame_does_not_set_it(self):
        t, model, after, lc = flip_night(seed=5)
        lc2 = lc.copy()
        lc2[10] += 0.2
        usable = cs.contact_free(t, model)
        b, a = np.where(usable & ~after)[0], np.where(usable & after)[0]
        plan = cs.step_plan(after, model["mag"])
        self.assertAlmostEqual(cs.free_depth_step(lc2, b, a, plan)[0], cs.free_depth_step(lc, b, a, plan)[0],
                               delta=0.0006)

    def test_too_few_a_side_is_not_judged(self):
        t, model, after, lc = flip_night()
        plan = cs.step_plan(after, model["mag"])
        st, er = cs.free_depth_step(lc, np.array([0, 1]), np.where(after)[0], plan)
        self.assertTrue(math.isnan(st) and math.isnan(er))

    def test_no_transit_frames_drops_the_depth_term(self):
        n = 120
        rng = np.random.default_rng(2)
        after = np.arange(n) >= 60
        lc = 0.004 * after + rng.normal(0, 0.003, n)
        st, er = cs.free_depth_step(lc, np.arange(60), np.arange(60, n), cs.step_plan(after, np.zeros(n)))
        self.assertAlmostEqual(st, 0.004, delta=3 * er)


class TestRecenterPoints(unittest.TestCase):
    def test_centres_like_the_run_and_refuses_a_jump(self):
        img = np.full((200, 200), 100.0, dtype=np.float32)
        yy, xx = np.mgrid[0:200, 0:200]
        for (x0, y0) in ((60.3, 70.6), (140.0, 120.0)):
            img += 5000 * np.exp(-((xx - x0) ** 2 + (yy - y0) ** 2) / (2 * 2.0 ** 2))
        out = core.recenter_points(img, np.array([[61.0, 70.0], [100.0, 100.0], [np.nan, 5.0]]), 6.0)
        self.assertLess(math.hypot(out[0, 0] - 60.3, out[0, 1] - 70.6), 0.2)
        self.assertEqual(tuple(out[1]), (100.0, 100.0))   # no star near: kept where aligned
        self.assertTrue(np.isnan(out[2, 0]))


class TestOrderFree(unittest.TestCase):
    def test_omean_does_not_depend_on_order(self):
        rng = np.random.default_rng(1)
        for _ in range(200):
            v = list(rng.normal(-9.0, 1.3, 4))
            self.assertEqual(core.omean(v), core.omean(v[::-1]))
            self.assertEqual(core.omean([v[0], v[3], v[2], v[1]]), core.omean(v))
        self.assertTrue(math.isnan(core.omean([])))

    def test_tied_ensemble_same_bits_in_any_comp_order(self):
        rng = np.random.default_rng(7)
        cm = rng.normal(-9.0, 0.8, (300, 4)) + rng.normal(0, 0.003, (300, 4))
        cm[5, 2] = np.nan
        a = tc.tied_ensemble(cm)
        b = tc.tied_ensemble(cm[:, [0, 3, 2, 1]])
        self.assertTrue(np.array_equal(a, b))


class TestWords(unittest.TestCase):
    def test_edge_reach(self):
        from types import SimpleNamespace as NS
        obs = [NS(pos={"c": [(5.0, 50.0)], "k": (1.0, 50.0)}), NS(pos={"c": [(5.0, 50.0)], "k": (-0.4, 52.0)})]
        text = " ".join(core.edge_problems(obs, (100, 200), ["A"], "K", 30.0))
        self.assertIn("check K reaches the frame edge (stars at least 30 px in are safe)", text)
        self.assertIn("comp A comes within 5 px of the frame edge", text)
        self.assertNotIn("within 0 px", text)


class TestPlotBins(unittest.TestCase):
    def test_too_narrow_bins_give_way(self):
        import transit_ui as tu
        x = np.arange(0, 3.5, 3 / 60)   # MOBS: a frame every 3 min
        w, note = tu.plot_bins_that_work(x, 5 / 60, "5 min", 3 / 60)
        self.assertAlmostEqual(w, 10 / 60)
        self.assertIn("5-min bins hold under 3 frames on this night (one every 3 min): showing 10-min bins", note)
        self.assertEqual(tu.plot_bins_that_work(x, 10 / 60, "10 min", 3 / 60), (10 / 60, ""))
        self.assertEqual(tu.plot_bins_that_work(x, 13 / 60, "Auto", 3 / 60), (13 / 60, ""))
        x1 = np.arange(0, 3.5, 1 / 60)
        self.assertEqual(tu.plot_bins_that_work(x1, 5 / 60, "5 min", 1 / 60), (5 / 60, ""))


class TestRobustWords(unittest.TestCase):
    def test_says_how_far_the_choice_moves_it(self):
        import inspect
        src = inspect.getsource(tc.robustness_check)
        self.assertIn("which moves it by", src)


class TestStepDepthTogether(unittest.TestCase):
    def test_flip_at_ingress_on_a_night_that_ends_in_transit_is_not_judged(self):
        rng = np.random.default_rng(9)
        t = 2461363.70 + np.arange(150) * 0.00073
        model = cs.transit_mags(t, PLANET, tc.occultation, (0.49, 0.23))
        ing = int(np.where(model["mag"] > 1e-4)[0][0])
        after = np.arange(150) >= ing
        lc = -0.0064 * after + rng.normal(0, 0.003, 150)
        usable = cs.contact_free(t, model)
        self.assertGreater(cs.step_depth_vif(after[usable], model["mag"][usable]), cs.STEP_VIF_MAX)
        st, er = cs.free_depth_step(lc, np.where(usable & ~after)[0], np.where(usable & after)[0],
                                    cs.step_plan(after, model["mag"]))
        self.assertTrue(math.isnan(st) and math.isnan(er))

    def test_mid_transit_flip_is_well_conditioned(self):
        t, model, after, _lc = flip_night()
        usable = cs.contact_free(t, model)
        self.assertLess(cs.step_depth_vif(after[usable], model["mag"][usable]), 3.0)

    def test_one_or_two_transit_frames_without_the_depth_term_are_left_out(self):
        n = 100
        after = np.arange(n) >= 50
        shape = np.zeros(n)
        shape[60:62] = 0.012            # two frames in transit after the flip
        lc = 0.0 * after + 0.012 * (shape > 0)   # the predicted transit not taken out of them
        st, _er = cs.free_depth_step(lc + np.random.default_rng(1).normal(0, 1e-4, n), np.arange(50),
                                     np.arange(50, n), cs.step_plan(after, shape))
        self.assertAlmostEqual(st, 0.0, delta=0.0002)


class TestPlotBinsFallback(unittest.TestCase):
    def test_no_wider_width_keeps_the_choice_without_a_note(self):
        import transit_ui as tu
        x = np.array([0.0, 1.0, 2.0, 3.0, 4.0])   # a few frames an hour apart; nothing wider than 30 min in the box
        self.assertEqual(tu.plot_bins_that_work(x, 0.5, "30 min", 1 / 60), (0.5, ""))


try:
    import tkinter
    tkinter.Tk().destroy()
    HAVE_TK = True
except Exception:   # pragma: no cover
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestReportFitSaveSet(unittest.TestCase):
    def test_report_fit_and_output_run_are_numbered_apart(self):
        import tempfile
        from unittest import mock
        import clearv_app as ca
        app = ca.App()
        try:
            folder = tempfile.mkdtemp()
            app.set_mode("transits")
            tp = app.transit_page
            fit = ["AAVSO_TOI-2046b_03-SEP-2026_SHOBS-P.txt", "TOI-2046b_V_03-SEP-2026_SHOBS-P_transit.png",
                   "TOI-2046b_V_03-SEP-2026_SHOBS-P_transit_fit.csv"]
            with mock.patch.object(tp, "family_names", return_value=fit), \
                    mock.patch.object(app, "_family_names",
                                      return_value=["AAVSO_wasp-49_TG_SHOBS-P.txt", "wasp-49_TG_SHOBS-P_lightcurve.csv",
                                                    "wasp-49_TG_SHOBS-P_lightcurve.png"] + fit):
                open(os.path.join(folder, "wasp-49_TG_SHOBS-P_lightcurve.csv"), "w").close()
                tp.result, tp.report = {"summary": {}}, {"t": [0.0]}
                app._save_set = None
                # the report fit's own files: no _2 from the other run's light curve
                self.assertEqual(os.path.basename(app._family_path(folder, fit[0])), fit[0])
                app._save_set = None
                self.assertEqual(os.path.basename(app._family_path(folder, fit[2])), fit[2])
                # the Output page's own save still numbers against its own names
                app._save_set = None
                self.assertEqual(os.path.basename(app._family_path(folder, "wasp-49_TG_SHOBS-P_lightcurve.csv")),
                                 "wasp-49_TG_SHOBS-P_lightcurve_2.csv")
                # re-review S1: files of both sets saved in turn keep each set's own number
                app._save_set, app._save_sets = None, {}
                got = [os.path.basename(app._family_path(folder, n)) for n in
                       (fit[0], "wasp-49_TG_SHOBS-P_lightcurve.csv", fit[1], fit[2],
                        "TOI-2046b_V_03-SEP-2026_SHOBS-P_transit_corner.png")]
                self.assertEqual(got, [fit[0], "wasp-49_TG_SHOBS-P_lightcurve_2.csv", fit[1], fit[2],
                                       "TOI-2046b_V_03-SEP-2026_SHOBS-P_transit_corner.png"])
                app._save_set, app._save_sets = None, {}
                got = [os.path.basename(app._family_path(folder, n)) for n in
                       ("wasp-49_TG_SHOBS-P_lightcurve.csv", fit[0])]
                self.assertEqual(got, ["wasp-49_TG_SHOBS-P_lightcurve_2.csv", fit[0]])
                tp.report = None   # a fit of the run itself: one set again
                app._save_set = None
                self.assertEqual(os.path.basename(app._family_path(folder, fit[0])),
                                 "AAVSO_TOI-2046b_03-SEP-2026_SHOBS-P_2.txt")
        finally:
            app.transit_page.result = None
            app.transit_page.report = None
            app.destroy()


if __name__ == "__main__":
    unittest.main()
