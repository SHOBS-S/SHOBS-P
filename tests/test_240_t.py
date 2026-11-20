"""2.4.0 step 6a: #103 (detrend drop-down, Auto by BIC, time curve, flip step inside each choice), #70 (depth
against the published (Rp/R*)² range), #62 (prayer bead, beta bins, prior-held inflation flag), #63 (high-airmass
trim offer) and step 6 nit a (no suggestions while the comp survey reads the night)."""
import math
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402

import transit_core as tc  # noqa: E402
from tests.test_230_b import PLANET, TC, synth_night  # noqa: E402


def night(quad=0.0, slope=0.0, step=0, seed=3, red=0.0):
    t, f, e, am, after = synth_night(step, seed=seed)
    x = t - t.mean()
    f = f * (1 + slope * x + quad * x ** 2)
    if red:
        rng = np.random.default_rng(seed + 10)
        f = f * (1 + red * np.convolve(rng.normal(0, 1, len(t) + 40), np.ones(41) / np.sqrt(41), "valid"))
    return t, f, e, am, after


class TestDetrendChoices(unittest.TestCase):
    def test_choice_table(self):
        keys = [k for k, _l, _f in tc.DETREND_CHOICES]
        self.assertEqual(keys, ["none", "airmass", "slope", "curve", "airmass+slope", "airmass+curve"])
        for k, _l, f in tc.DETREND_CHOICES:
            self.assertEqual(tc.detrend_key(f), k)
            self.assertFalse(f[2] and not f[1])          # a curve always comes with its slope
        self.assertEqual(tc.detrend_label("auto"), "Auto (BIC)")
        self.assertEqual(tc.detrend_names((True, True, True)), ["airmass", "time", "time2"])
        self.assertEqual(tc.detrend_names((True, False, False), has_airmass=False), [])
        self.assertEqual(tc.detrend_text(["airmass", "time", "time2"]), "airmass, time curve")
        self.assertEqual(tc.detrend_text([]), "none")

    def test_curve_term_in_the_model(self):
        t = np.linspace(0, 0.2, 50)
        prob = tc.TransitProblem(t, np.ones(50), np.ones(50), None, 2.0, {}, tc.PARAMS, {"tc": 5.0, "k": 0.1},
                                 False, True, None, None, detrend_curve=True)
        self.assertIn("c_t2", prob.free)
        self.assertIn("c_t", prob.free)
        base = prob.baseline(dict(prob.fixed, c0=1.0, c_t=0.0, c_t2=2.0))
        self.assertAlmostEqual(float(base[0]), 1.0 + 2.0 * (t[0] - t.mean()) ** 2, places=12)
        no = tc.TransitProblem(t, np.ones(50), np.ones(50), None, 2.0, {}, tc.PARAMS, {}, False, True)
        self.assertNotIn("c_t2", no.free)
        none = tc.TransitProblem(t, np.ones(50), np.ones(50), None, 2.0, {}, tc.PARAMS, {}, False, False,
                                 detrend_curve=True)
        self.assertNotIn("c_t2", none.free)              # the curve needs its slope

    def test_auto_finds_a_curve_and_keeps_it_simple(self):
        t, f, e, _am, _after = night(quad=0.8, slope=-0.02)
        flat_am = np.full(len(t), np.nan)                 # no airmass: the curve is the only way to follow it
        r = tc.lsq_fit(t, f, e, flat_am, PLANET, detrend_auto=True)
        ch = r["detrend_choice"]
        self.assertEqual(ch["key"], "curve")
        self.assertFalse(ch["airmass_tried"])
        self.assertTrue(all(not row["flags"][0] for row in ch["rows"]))
        self.assertEqual(r["detrend"], ["time", "time2"])
        self.assertAlmostEqual(r["depth"][0], PLANET["k"] ** 2, delta=0.004)
        # a flat night: the extra terms are not worth it (ties go to fewer terms)
        t, f, e, _am, _after = night()
        r = tc.lsq_fit(t, f, e, flat_am, PLANET, detrend_auto=True)
        self.assertEqual(r["detrend_choice"]["key"], "none")

    def test_tie_goes_to_fewer_terms_then_lower_bic(self):
        rows = [{"key": "none", "bic": 10.0, "k": 7}, {"key": "slope", "bic": 9.0, "k": 8},
                {"key": "curve", "bic": 7.5, "k": 9}, {"key": "airmass+slope", "bic": 7.0, "k": 9}]
        bmin = min(r["bic"] for r in rows)
        near = [r for r in rows if r["bic"] <= bmin + tc.DETREND_TIE]
        chosen = min(near, key=lambda r: (r["k"], r["bic"], rows.index(r)))
        self.assertEqual(chosen["key"], "slope")          # the same rule choose_detrend uses

    def test_flip_step_tested_inside_each_choice(self):
        t, f, e, am, after = night(step=14)
        r = tc.lsq_fit(t, f, e, am, PLANET, after_flip=after, detrend_auto=True)
        ch = r["detrend_choice"]
        self.assertTrue(all("kept" in row and "dbic_step" in row for row in ch["rows"]))
        self.assertTrue(r["flip_test"]["kept"])
        self.assertEqual(r["flip_test"]["detrend"], ch["label"])
        self.assertIsNotNone(r["t_flip"])
        self.assertIn("flip step kept with", tc.detrend_choice_text(ch))
        # forced step (flip_step=True): every choice carries it
        r2 = tc.lsq_fit(t, f, e, am, PLANET, after_flip=after, detrend_auto=True, flip_step=True)
        self.assertTrue(all(row["kept"] for row in r2["detrend_choice"]["rows"]))
        self.assertIsNone(r2["flip_test"])

    def test_manual_choice_unchanged(self):
        t, f, e, am, _after = night()
        r = tc.lsq_fit(t, f, e, am, PLANET, detrend_airmass=True)
        self.assertIsNone(r["detrend_choice"])
        self.assertEqual(r["detrend"], ["airmass"])
        self.assertEqual(r["detrend_flags"], (True, False, False))

    def test_report_columns_and_lines(self):
        n = 30
        result = {"t": 2461000.5 + np.arange(n) * 0.002, "airmass": np.linspace(1.2, 1.5, n),
                  "detrend": ["airmass", "time", "time2"]}
        cols = tc.detrend_columns(result)
        self.assertEqual([c[0] for c in cols], ["AIRMASS", "TIME", "TIME_SQUARED"])
        sq = cols[2][1]
        self.assertAlmostEqual(float(sq[0]), (result["t"][0] - result["t"].mean()) ** 2)
        ch = {"key": "curve", "label": "time curve", "rows": [
            {"key": "none", "label": "none", "dbic": 12.0}, {"key": "curve", "label": "time curve", "dbic": 0.0},
            {"key": "slope", "label": "time slope", "dbic": 3.5}], "airmass_tried": False}
        note = tc.detrend_report_note({"detrend_choice": ch})
        self.assertEqual(note, "SHOBS-P: detrend chosen by BIC: time curve (next time slope, dBIC +3.5)")
        self.assertTrue(note.isascii())
        text = tc.detrend_choice_text(ch, ascii_only=True)
        self.assertIn("airmass not tried", text)
        self.assertTrue(text.isascii())
        self.assertEqual(tc.tc_detrend_words({"detrend": ["time", "time2"], "detrend_choice": ch}),
                         "time curve (Auto, the lowest BIC)")


class TestPublishedRange(unittest.TestCase):
    PL = {"name": "X b", "k": 0.1213, "k_err": 0.002,
          "k_published": [[0.1213, 0.0021, "Kabath et al. 2022"]], "depths_listed": [[0.0163, "ExoFOP"]]}

    def result(self, depth, err, planet=None):
        return {"planet": planet or self.PL, "summary": {"depth": (depth, err)}}

    def test_range_from_rp_only(self):
        rng = tc.published_range({"k_published": [[0.13, 0.001, "a"], [0.12, None, "b"], [0.14, 0.002, "c"]],
                                  "depths_listed": [[0.05, "ExoFOP"]]})
        self.assertAlmostEqual(rng["lo"], 0.0144)
        self.assertAlmostEqual(rng["hi"], 0.0196)           # listed depths never widen it
        self.assertTrue(rng["has_errors"])
        self.assertEqual(tc.range_text(rng), "1.44–1.96 % (3 values)")
        self.assertEqual(tc.range_text(tc.published_range({"k": 0.1, "k_err": 0.01})), "1.00 %")

    def test_toi_2046_like_caution_and_detected_stays(self):
        sc = tc.size_check(self.result(0.0222, 0.0019))
        self.assertEqual(sc["state"], "outside")
        self.assertGreater(sc["sigma"], 3)
        self.assertIn("deeper than the published (Rp/R*)² 1.47 %", sc["text"])
        self.assertIn("Kabath", sc["text"])
        self.assertEqual(tc.size_check(self.result(0.0211, 0.0017, {"k": 0.131, "k_err": 0.0008,
                                                                  "k_published": [[0.131, 0.0008, "Kokori"]]}))["state"],
                         "near")                            # WASP-11 b, 2.3 sigma: plain
        self.assertEqual(tc.size_check(self.result(0.0200, 0.0010, {
            "k_published": [[0.13, 0.001, "a"], [0.15, 0.001, "b"]]}))["state"], "inside")
        self.assertIsNone(tc.size_check(self.result(0.03, 0.001, {"k": 0.1})))   # no published error: no check

    def test_factor_three_only_without_errors(self):
        r = {"planet": {"k": 0.1, "k_err": 0.001}, "summary": {"depth": (0.05, 0.002)}}
        self.assertEqual(tc.depth_plausibility(r), "")         # with an error: the size caution decides
        r = {"planet": {"k": 0.1}, "summary": {"depth": (0.05, 0.002)}}
        self.assertIn("5.0x the published", tc.depth_plausibility(r))

    def test_size_verdict(self):
        r = dict(self.result(0.0222, 0.0019), verdict={"state": "detected", "depth_unreliable": ""})
        tc._size_verdict(r)
        self.assertTrue(r["verdict"]["size_caution"].startswith("depth 2.22"))
        r = dict(self.result(0.0222, 0.0019), verdict={"state": "inconclusive"})
        tc._size_verdict(r)
        self.assertEqual(r["verdict"]["size_caution"], "")
        r = dict(self.result(0.02, 0.001, {"k_published": [[0.13, 0.001, "a"], [0.15, 0.001, "b"]]}),
                 verdict={"state": "detected"})
        tc._size_verdict(r)
        self.assertEqual(r["verdict"]["size_note"], "Depth within the published range 1.69–2.25 % (2 values).")

    def test_cached_lookup(self):
        import lookup_cache
        with mock.patch.dict(os.environ, {"SHOBS_P_NO_LOOKUP_CACHE": ""}):
            import tempfile
            with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"SHOBS_P_LOOKUP_CACHE_DIR": d}):
                with mock.patch.object(tc, "_nea_query", return_value=[
                        {"pl_ratror": "0.12", "pl_ratrorerr1": "0.002", "pl_ratrorerr2": "-0.003",
                         "pl_refname": "<a href=x>Kab&aacute;th et al. 2022</a>"},
                        {"pl_trandep": "1.6", "pl_refname": "ExoFOP"}]):
                    ks, ds = tc.published_rp_cached("TOI-2046 b")
                self.assertEqual(ks, [[0.12, 0.003, "Kabáth et al. 2022"]])
                self.assertEqual(ds, [[0.016, "ExoFOP"]])
                with mock.patch.object(tc, "_nea_query", side_effect=OSError("down")):
                    self.assertEqual(tc.published_rp_cached("TOI-2046 b")[0], ks)   # the last good answer
                self.assertTrue(lookup_cache.get("nea_rp", "toi2046b")[0])


class TestRedNoise(unittest.TestCase):
    def test_prayer_bead_sees_red_noise(self):
        t, f, e, am, _a = night(red=0.003, seed=5)
        r = tc.fit_transit(t, f, e, am, PLANET, detrend_airmass=True, mcmc_steps=400)
        pb = r["prayer_bead"]
        self.assertIsNotNone(pb)
        self.assertGreaterEqual(pb["n"], 8)
        self.assertLessEqual(pb["n"], tc.PB_MAX_SHIFTS)
        for name in pb["raised"]:
            self.assertAlmostEqual(r["summary"][name][1], pb["depth_err"] if name == "depth" else pb["err"][name])
        self.assertTrue(any("fit errors: prayer bead" in ln.lower() for ln in tc.fit_inputs_lines(r)))

    def test_apply_takes_the_larger(self):
        s = {"tc": (1.0, 0.002), "depth": (0.02, 0.001), "k": (0.14, 0.003)}
        raised = tc.apply_prayer_bead(s, {"err": {"tc": 0.001, "k": 0.004}, "depth_err": 0.002})
        self.assertEqual(sorted(raised), ["depth", "k"])
        self.assertEqual(s["tc"], (1.0, 0.002))
        self.assertEqual(s["k"], (0.14, 0.004))
        self.assertEqual(tc.apply_prayer_bead(s, None), [])

    def test_beta_skips_bins_under_ten_minutes(self):
        rng = np.random.default_rng(1)
        n = 49
        t = np.linspace(0, 3.6 / 24, n)
        cad = float(np.median(np.diff(t)))
        resid = np.repeat(rng.normal(0, 0.015, 10), 5)[:n] + rng.normal(0, 0.004, n)   # groups of 5 frames
        new = tc.red_noise_beta(t, resid, cad)
        old = tc.red_noise_beta(t, resid, cad, min_minutes=0.0)
        self.assertGreater(new, 1.0)
        self.assertGreaterEqual(new, old)

    def test_prior_held_inflation_flag(self):
        r = {"summary": {"k": (0.268, 0.01), "t14": (1.38 / 24, 0.001), "a_rs": (4.0, 0.1), "b": (0.6, 0.05)},
             "best": {"k": 0.268, "b": 0.3, "tc": 0.15, "a_rs": 4.0}, "period": 0.81, "priors": {},
             "t": np.linspace(0.0, 0.3, 49), "flux": np.ones(49), "residuals": np.zeros(49), "beta": 1.0,
             "rchi2": 1.0, "error_scale": 1.0, "cadence_days": 0.006, "verdict": {"state": "detected"},
             "planet": {"k": 0.159, "t14": 1.16 / 24}, "free_fit": {"unconstrained": "depth 34.8 %"}}
        flags = tc.transit_flags(r)
        text = " ".join(t_ for _l, t_ in flags)
        self.assertIn("The free shape ran away AND", text)
        self.assertIn("1.7× the published 0.159", text)


class TestHighAirmass(unittest.TestCase):
    def test_spans_and_text(self):
        t = np.arange(40) * 0.003 + 2461000.0
        am = np.linspace(1.4, 4.0, 40)
        h = tc.high_airmass_spans(t, am, 3.0, tc=t[15], t14=0.03)
        self.assertEqual(h["where"], "at the end")
        self.assertEqual(h["n"], int((am > 3).sum()))
        lo, hi = h["spans"][0]
        self.assertTrue(math.isinf(hi))
        self.assertTrue(t[am <= 3].max() < lo < t[am > 3].min())
        self.assertIn("past airmass 3 (up to 4.00, at the end)", tc.high_airmass_text(h))
        self.assertIn("of out-of-transit baseline before the transit", tc.high_airmass_text(h))
        self.assertIsNone(tc.high_airmass_spans(t, np.full(40, 1.2), 3.0))
        self.assertIsNone(tc.high_airmass_spans(t, None, 3.0))
        both = tc.high_airmass_spans(t, np.r_[np.full(5, 3.5), np.full(30, 1.5), np.full(5, 3.2)], 3.0)
        self.assertEqual(both["where"], "at both ends")
        self.assertEqual(len(both["spans"]), 2)


if __name__ == "__main__":
    unittest.main()


class TestTransitPageStep6(unittest.TestCase):
    """The page: the Detrend drop-down feeds the fit (Auto by default), the table shows the time terms and the
    listed depths, and the high-airmass offer comes and goes with the fit."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        import clearv_app as ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test_240_t.log")
        cls.app = ca.App()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def test_default_is_auto_and_the_fit_gets_it(self):
        tp = self.app.transit_page
        self.assertEqual(tp.detrend.get(), "Auto (BIC)")
        self.assertEqual(tp.detrend_key(), "auto")
        tp.set_detrend("airmass+curve")
        self.assertEqual(tp._settings_key()[0], "airmass+curve")
        self.assertTrue(tp.detrend_airmass.get())
        self.assertTrue(tp.detrend_time.get())
        tp.set_detrend("auto")

    def test_airmass_offer_shows_and_trims(self):
        tp = self.app.transit_page
        t = 2461000.5 + np.arange(60) * 0.004
        am = np.linspace(1.3, 3.8, 60)
        result = {"t": t, "airmass": am, "summary": {"tc": (t[20], 0.001), "t14": (0.05, 0.001)}, "flags": [],
                  "verdict": {"state": "detected", "reason": "Transit detected."}}
        old = list(tp._trims)
        try:
            tp._offer_airmass_trim(result)
            self.assertIsNotNone(tp._airmass_offer)
            self.assertIn(tp.trim_airmass_button, tp.groups.boxes["points"].shown_widgets())
            self.assertEqual(str(tp.trim_airmass_button.cget("text")), "Trim airmass > 3")
            self.assertTrue(any("past airmass 3" in t_ for _l, t_ in result["flags"]))
            with mock.patch.object(tp, "fit") as fit:
                tp.trim_high_airmass()
                fit.assert_called_once()
            self.assertEqual(len(tp._trims), len(old) + 1)
            self.assertTrue(math.isinf(tp._trims[-1][1]))
            self.assertNotIn(tp.trim_airmass_button, tp.groups.boxes["points"].shown_widgets())
            self.app.settings["transit_trim_airmass"] = 5.0          # a higher limit: nothing to offer
            tp._offer_airmass_trim(result)
            self.assertIsNone(tp._airmass_offer)
        finally:
            self.app.settings.pop("transit_trim_airmass", None)
            tp._reset_trim()
            tp._trims = old

    def test_progress_words(self):
        import transit_ui as tu
        self.assertEqual(tu.fit_progress_words("detrend", 2, 6), "Fitting the transit: choosing the detrend… 2 of 6")
        self.assertIn("prayer-bead", tu.fit_progress_words("prayer", 0, 1))


class TestStep6aReviewFixes(unittest.TestCase):
    """Fable review of step 6a: S1-S4, S6, S7, N1-N4, N6, N8."""

    def test_partial_airmass_auto_drops_those_frames(self):            # S2
        t, f, e, am, _a = night(quad=0.0)
        am = am.copy()
        am[:3] = np.nan
        said = []
        r = tc.lsq_fit(t, f, e, am, PLANET, detrend_auto=True)
        ch = r["detrend_choice"]
        self.assertTrue(ch["airmass_tried"])
        self.assertEqual(ch["dropped_no_airmass"], 3)
        self.assertEqual(len(r["t"]), len(t) - 3)
        S, _fl, _ft, _ch = tc._setup_and_detrend(t, f, e, am, PLANET, True, False, False, True, None, None, None,
                                                say=said.append)
        self.assertTrue(any("3 frames without airmass left out" in m for m in said))
        half = am.copy()
        half[: len(t) // 2 + 5] = np.nan                            # most missing: airmass left out instead
        r = tc.lsq_fit(t, f, e, half, PLANET, detrend_auto=True)
        self.assertFalse(r["detrend_choice"]["airmass_tried"])
        self.assertEqual(len(r["t"]), len(t))

    def test_a_choice_that_does_not_fit_is_skipped(self):              # S3
        t, f, e, am, _a = night()
        real = tc.best_fit
        calls = {"n": 0}

        def flaky(problem, starts=None):
            calls["n"] += 1
            if getattr(problem, "free", None) and "c_t2" in problem.free and "c_am" not in problem.free:
                raise ValueError("the least-squares fit did not converge")
            return real(problem, starts)
        said = []
        with mock.patch.object(tc, "best_fit", side_effect=flaky):
            S, _fl, _ft, ch = tc._setup_and_detrend(t, f, e, am, PLANET, True, False, False, True, None, None, None,
                                                   say=said.append)
        self.assertEqual(ch["failed"], ["time curve"])
        self.assertNotIn("curve", [r_["key"] for r_ in ch["rows"]])
        self.assertTrue(any("detrend time curve: no fit" in m for m in said))

    def test_curve_depth_note(self):                                     # S1 (John: note with / without)
        ch = {"key": "airmass+curve", "label": "airmass + time curve", "flags": (True, True, True), "rows": [
            {"key": "airmass", "label": "airmass", "flags": (True, False, False), "bic": 113.5, "dbic": 13.5,
             "depth": 0.0356},
            {"key": "airmass+curve", "label": "airmass + time curve", "flags": (True, True, True), "bic": 100.0,
             "dbic": 0.0, "depth": 0.0484},
            {"key": "none", "label": "none", "flags": (False, False, False), "bic": 130.0, "dbic": 30.0,
             "depth": 0.03}]}
        r = {"detrend_choice": ch, "summary": {"depth": (0.0484, 0.0087)}}
        note = tc.curve_depth_note(r)
        self.assertIn("depth 4.84 % with the time curve", note)
        self.assertIn("without it, airmass (ΔBIC +13.5), gives 3.56 %", note)
        self.assertTrue(tc.curve_depth_note(r, ascii_only=True).isascii())
        ch2 = dict(ch, key="airmass", flags=(True, False, False))
        self.assertEqual(tc.curve_depth_note({"detrend_choice": ch2, "summary": r["summary"]}), "")
        self.assertEqual(tc.curve_depth_note({"summary": r["summary"]}), "")

    def test_rows_carry_their_depth(self):
        t, f, e, am, after = night(step=14)
        r = tc.lsq_fit(t, f, e, am, PLANET, after_flip=after, detrend_auto=True)
        for row in r["detrend_choice"]["rows"]:
            self.assertAlmostEqual(row["depth"], PLANET["k"] ** 2, delta=0.006)

    def test_lookup_status_and_names(self):                              # S4, N8
        import tempfile
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"SHOBS_P_LOOKUP_CACHE_DIR": d,
                                                                              "SHOBS_P_NO_LOOKUP_CACHE": ""}):
            with mock.patch.object(tc, "_nea_query", side_effect=OSError("down")):
                self.assertEqual(tc.published_rp_cached("Nowhere b", with_status=True), ([], [], False))
            seen = {}

            def q(adql, timeout=45):
                seen["adql"], seen["timeout"] = adql, timeout
                return []
            with mock.patch.object(tc, "_nea_query", side_effect=q):
                self.assertEqual(tc.published_rp_cached("wasp-12b", timeout=10, with_status=True), ([], [], True))
        self.assertIn("lower(pl_name) = 'wasp-12 b'", seen["adql"])
        self.assertEqual(seen["timeout"], 10)
        with mock.patch.object(tc, "_nea_query", side_effect=q):
            tc.published_rp_values("O'Brien-1 b")
        self.assertNotIn("O'B", seen["adql"])

    def test_old_single_value_note_defers_to_the_range(self):            # S7
        t, f, e, am, _a = night()
        r = tc.fit_transit(t, f, e, am, dict(PLANET, k=0.13, k_err=0.001,
                                             k_published=[[0.13, 0.001, "a"], [0.17, 0.001, "b"]]),
                           detrend_airmass=True, mcmc_steps=300)
        self.assertEqual((r["verdict"].get("size_check") or {}).get("state"), "inside")
        self.assertFalse(any("vs published" in t_ and "a large difference" in t_ for _l, t_ in r["flags"]))

    def test_beta_short_run_falls_back(self):                            # N4
        rng = np.random.default_rng(2)
        t = np.arange(100) * 20 / 86400.0                                   # 33 min at 20 s
        walk = np.cumsum(rng.normal(0, 0.001, 100))
        self.assertGreater(tc.red_noise_beta(t, walk - walk.mean(), 20 / 86400.0), 1.0)

    def test_prayer_bead_error_does_not_stop_the_fit(self):              # N6
        t, f, e, am, _a = night()
        with mock.patch.object(tc, "prayer_bead", side_effect=RuntimeError("odd")):
            r = tc.fit_transit(t, f, e, am, PLANET, mcmc_steps=200)
        self.assertIsNone(r["prayer_bead"])
        with mock.patch.object(tc, "prayer_bead", side_effect=RuntimeError("Canceled")):
            with self.assertRaises(RuntimeError):
                tc.fit_transit(t, f, e, am, PLANET, mcmc_steps=200)

    def test_both_check_words(self):                                      # S5
        import transit_posterior as tpost
        text = tpost.both_detrend_text({"airmass": 30.0, "time": 2.0, "corr": 0.97})
        self.assertIn("Choose airmass in Detrend", text)
        self.assertNotIn("Untick", text)
        self.assertNotIn("Both", text)


class TestStep6aReviewFixesPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        import clearv_app as ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test_240_t2.log")
        cls.app = ca.App()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def test_choice_made_while_airmass_is_missing_is_kept(self):          # S6
        tp = self.app.transit_page
        old = tp.report
        try:
            tp._airmass_off_why = ""
            tp.set_detrend("airmass")
            tp.report = {"t": np.arange(30.0), "airmass": None}
            tp._update_airmass_box()
            self.assertEqual(tp.detrend.get(), "none")
            tp.set_detrend("curve")                                    # picked while airmass is missing
            tp.report = {"t": np.arange(30.0), "airmass": np.linspace(1.1, 1.5, 30)}
            tp._update_airmass_box()
            self.assertEqual(tp.detrend.get(), "airmass + time curve")  # airmass added back, the curve kept
        finally:
            tp.report = old
            tp._airmass_off_why = ""
            tp._update_airmass_box()
            tp.set_detrend("auto")

    def test_offer_goes_with_the_fit(self):                               # N2, N3
        tp = self.app.transit_page
        t = 2461000.5 + np.arange(60) * 0.004
        result = {"t": t, "airmass": np.linspace(1.3, 3.8, 60), "flags": [],
                  "summary": {"tc": (t[20], 0.001), "t14": (0.05, 0.001)}}
        tp._offer_airmass_trim(result)
        self.assertIn(tp.trim_airmass_button, tp.groups.boxes["points"].shown_widgets())
        self.assertFalse(any("takes them out" in t_ for _l, t_ in result["flags"]))   # no button words in files
        tp.result = None
        tp._fill_table()
        self.assertIsNone(tp._airmass_offer)
        self.assertNotIn(tp.trim_airmass_button, tp.groups.boxes["points"].shown_widgets())


class TestStep6aReReview(unittest.TestCase):
    def test_only_a_few_frames_are_left_out_for_airmass(self):          # R1
        t, f, e, am, _a = night()
        third = am.copy()
        third[::3] = np.nan                                              # a third missing: keep every frame
        r = tc.lsq_fit(t, f, e, third, PLANET, detrend_auto=True)
        self.assertFalse(r["detrend_choice"]["airmass_tried"])
        self.assertEqual(len(r["t"]), len(t))
        few = am.copy()
        few[:4] = np.nan
        r = tc.lsq_fit(t, f, e, few, PLANET, detrend_auto=True)
        ch = r["detrend_choice"]
        self.assertEqual(ch["dropped_no_airmass"], 4)
        self.assertIn("4 frames without airmass left out of every choice", tc.detrend_choice_text(ch))
