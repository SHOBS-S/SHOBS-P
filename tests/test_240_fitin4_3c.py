"""2.4.0 fit-in F4-3, part 3: the Transit fit page.

#35    the comp choice as a second error term (m755), the reference beside it (m793), in the fit file, the settings
       file, the fit CSV and the report NOTES; "Open Use comps, untick X" when one comp drives the spread (m754).
8g     the sky-measured gain is remembered per camera and used as the fallback (x283, x295); the sky's gain is used
       when EGAIN is over 2x off a measurement (m473).
m811   a JD_UTC report: the host's position is looked up from the planet and the times go to BJD_TDB.
#93    Fit with saved priors: the fit inputs read back from a settings file and used exactly.
nits   ERR is scaled (said in the report and the package CSV); the corner plot names a raised prayer-bead error;
       the Trim line under a report; the Night box follows a new night; the Use comps Flip step column."""
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import photometry_core as core  # noqa: E402
import sky_gain as sg  # noqa: E402
import transit_core as tc  # noqa: E402
import transit_posterior as tpost  # noqa: E402
import transit_ui as tui  # noqa: E402

try:
    import tkinter  # noqa: F401
    tkinter.Tk().destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False

if HAVE_TK:
    import test_240_m as M  # noqa: E402
    Base = M.AppCase
else:
    Base = unittest.TestCase

PLANET = dict(name="T b", host="T", period=2.5, t0=2460000.0, t0_err=1e-4, period_err=1e-6, k=0.12, a_rs=8.0,
              a_rs_err=0.2, b=0.3, b_err=0.05, teff=5800, t14=0.11)


def loo(depths=(0.0180, 0.0188, 0.0183), ref=0.0184, flagged=None, single=False):
    fits = [{"name": f"C{i + 1}", "depth": d, "tc": 2460250.0 + 1e-4 * (i - 1), "flagged": (i == flagged)}
            for i, d in enumerate(depths)]
    return {"fits": fits, "ref": {"depth": ref, "tc": 2460250.0, "n_points": 150}, "n_comps": len(depths),
            "single": single, "depth_min": min(depths), "depth_max": max(depths),
            "depth_spread": max(depths) - min(depths), "tc_spread_s": 17.3}


def result(**kw):
    r = {"summary": {"depth": (0.0184, 0.0005), "tc": (2460250.0, 0.00036)}, "comp_loo": loo(**kw)}
    r["comp_err"] = tc.comp_error(r["comp_loo"])
    return r


class TwoErrorTerms(unittest.TestCase):
    def test_comp_error_is_half_the_range_over_all_and_each(self):
        ce = tc.comp_error(loo())
        self.assertAlmostEqual(ce["depth"], (0.0188 - 0.0180) / 2)
        self.assertAlmostEqual(ce["tc"], 1e-4)
        self.assertEqual((ce["depth_all"], ce["depth_min"], ce["depth_max"]), (0.0184, 0.0180, 0.0188))
        self.assertEqual(ce["driver"], "")
        # the full ensemble counts in the range: every refit on one side of it
        ce = tc.comp_error(loo(depths=(0.0186, 0.0188, 0.0190)))
        self.assertAlmostEqual(ce["depth"], (0.0190 - 0.0184) / 2)
        self.assertIsNone(tc.comp_error(None))
        self.assertIsNone(tc.comp_error({"fits": []}))

    def test_driver_is_the_one_named_comp(self):
        self.assertEqual(tc.comp_error(loo(flagged=1))["driver"], "C2")
        two = loo()
        two["fits"][0]["flagged"] = two["fits"][2]["flagged"] = True
        self.assertEqual(tc.comp_error(two)["driver"], "")       # two named: no single comp drives it

    def test_m755_wording(self):
        r = result()
        self.assertEqual(tc.two_term_text(r, with_tc=False), "depth 1.84 ± 0.05 (stat) ± 0.04 (comps) %")
        self.assertEqual(tc.two_term_text(r), "depth 1.84 ± 0.05 (stat) ± 0.04 (comps) %; Tc ± 0.52 (stat) ± 0.14 "
                                              "(comps) min")
        a = tc.two_term_text(r, ascii_only=True)
        self.assertNotIn("±", a)
        self.assertIn("+/- 0.05 (stat) +/- 0.04 (comps)", a)
        self.assertEqual(tc.two_term_text({"summary": r["summary"]}), "")     # no comp check: nothing

    def test_m793_reference(self):
        self.assertEqual(tc.comp_reference_text(loo()), "all comps 1.84 %; without each 1.80–1.88 %")
        self.assertEqual(tc.comp_reference_text(loo(), ascii_only=True), "all comps 1.84 %; without each 1.80-1.88 %")
        self.assertEqual(tc.comp_reference_text(None), "")

    def test_line_says_it_is_not_added(self):
        line = tc.comp_error_line(result())
        self.assertTrue(line.startswith("depth 1.84 ± 0.05 (stat) ± 0.04 (comps) %; Tc "))
        self.assertIn("(all comps 1.84 %; without each 1.80–1.88 %)", line)
        self.assertIn("each of the 3 comps left out in turn", line)
        self.assertIn("it is not added to the statistical error", line)
        self.assertIn("single comp", tc.comp_error_line(result(depths=(0.018, 0.019), single=True)))

    def test_the_statistical_error_is_untouched(self):
        r = result()
        self.assertEqual(r["summary"]["depth"], (0.0184, 0.0005))

    def test_leave_one_comp_out_carries_it(self):
        import test_235 as T
        out = T.loo(*T.synth())
        self.assertIn("comp_err", out)
        self.assertEqual(out["comp_err"], tc.comp_error(out))
        self.assertEqual(out["comp_err"]["n_comps"], 4)
        self.assertGreater(out["comp_err"]["depth"], 0.0)

    def test_csv_comment_and_page_cell(self):
        r = result()
        r.update(error_scale=1.2, rchi2=1.3, beta=1.05)
        lines = tui.fit_csv_comments(r, {"filter": "CV"})
        got = [ln for ln in lines if ln.startswith("# Result with both error terms: ")]
        self.assertEqual(len(got), 1)
        self.assertIn("depth 1.84 +/- 0.05 (stat) +/- 0.04 (comps) %", got[0])
        self.assertTrue(got[0].isascii())
        self.assertEqual(tui.TransitPage._depth_cell(r, "1.84 ± 0.05"), "1.84 ± 0.05 (stat) ± 0.04 (comps)")
        self.assertEqual(tui.TransitPage._depth_cell({}, "1.84 ± 0.05"), "1.84 ± 0.05")
        self.assertEqual(tui.UNTICK_BUTTON.format(name="C2"), "Open Use comps, untick C2")


class ErrScaled(unittest.TestCase):
    def test_note(self):
        n = tc.err_scaled_note({"error_scale": 1.234, "rchi2": 1.31, "beta": 1.08})
        self.assertEqual(n, "ERR is each point's measured error x 1.234, scaled to the scatter and the red noise "
                            "(raw reduced chi-square 1.31, red-noise beta 1.08)")
        n = tc.err_scaled_note({"error_scale": 1.0, "rchi2": 0.62, "beta": 1.0})
        self.assertIn("not scaled (raw reduced chi-square 0.62", n)
        self.assertIn("never scaled down", n)
        self.assertEqual(tc.err_scaled_note({}), "")
        self.assertTrue(n.isascii())

    def test_fit_csv_header_already_states_it(self):
        """The 11 Nov nit: the fit CSV header gives the raw reduced chi-square and the error scale (2.3.4, #73)."""
        line = tui.fit_csv_error_line({"error_scale": 1.234, "rchi2": 1.31, "beta": 1.08, "n_points": 150,
                                       "free_params": ["tc", "k"]})
        self.assertIn("S = sqrt(max(chi2_nu, 1)) x beta = 1.234", line)
        self.assertIn("raw reduced chi-square 1.31 (N 150, 2 free)", line)

    def test_m766_scaling_stays_up_only(self):
        """John, 26 Nov: error bars are never scaled down (m766 Won't do)."""
        import inspect
        src = inspect.getsource(tc)
        self.assertGreaterEqual(src.count("math.sqrt(max(rchi, 1.0))"), 4)


class CornerNote(unittest.TestCase):
    def test_raised_errors_are_named(self):
        r = {"prayer_bead": {"raised": ["tc", "depth"], "err": {"tc": 0.00043}, "depth_err": 0.0005}}
        w = tpost.prayer_bead_words(r)
        self.assertEqual(w, "The table gives the larger prayer-bead error for Tc (± 0.62 min) and depth (± 0.050 %); "
                            "these panels show the MCMC's spread")
        self.assertEqual(tpost.prayer_bead_words({"prayer_bead": {"raised": []}}), "")
        self.assertEqual(tpost.prayer_bead_words({}), "")

    def test_title_holds_it(self):
        r = {"planet": {"name": "T b"}, "chain": {"walkers": 20, "steps": 100},
             "prayer_bead": {"raised": ["tc"], "err": {"tc": 0.00043}, "depth_err": 0.0}}
        self.assertIn("larger prayer-bead error for Tc", tpost._corner_title(r, 9.0, 8.0).replace("\n", " "))
        r["prayer_bead"]["raised"] = []
        self.assertNotIn("prayer-bead", tpost._corner_title(r, 9.0, 8.0))


class RememberedGain(unittest.TestCase):
    HEAD = {"INSTRUME": "ZWO ASI2600MM Pro", "GAIN": 100, "OFFSET": 50, "XBINNING": 1}
    MEAS = {"ok": True, "k": 0.26, "k_lo": 0.25, "k_hi": 0.27, "n_pairs": 20, "floor_var": 1.3,
            "floor_source": "darks"}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"SHOBS_DATA_HOME": self.tmp})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_remember_and_recall_per_camera(self):
        self.assertIsNone(sg.recall(self.HEAD))
        self.assertTrue(sg.remember(self.HEAD, 0.26, "2026-11-13", 20))
        self.assertTrue(sg.remember(self.HEAD, 0.24, "2026-11-14", 18))
        got = sg.recall(self.HEAD)
        self.assertAlmostEqual(got["e_per_adu"], 0.25)            # the median of the nights kept
        self.assertEqual((got["nights"], got["last"]), (2, "2026-11-14"))
        self.assertEqual(got["camera"], "zwo-asi2600mm-pro__g100__o50__bin1")
        # a rerun of a night replaces its entry
        self.assertTrue(sg.remember(self.HEAD, 0.30, "2026-11-14", 18))
        self.assertAlmostEqual(sg.recall(self.HEAD)["e_per_adu"], 0.28)
        # another gain setting, or another binning, is another camera
        self.assertIsNone(sg.recall(dict(self.HEAD, GAIN=0)))
        self.assertIsNone(sg.recall(dict(self.HEAD, XBINNING=2)))
        # its own file in the SHOBS data folder; no camera profile of SHOBS-FLUX is made
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, sg.REMEMBER_FILE)))
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, "cameras")))

    def test_only_the_newest_nights_are_kept(self):
        for i in range(sg.REMEMBER_KEEP + 5):
            sg.remember(self.HEAD, 0.2 + 0.001 * i, f"2026-10-{i + 1:02d}", 10)
        self.assertEqual(sg.recall(self.HEAD)["nights"], sg.REMEMBER_KEEP)

    def test_nothing_without_a_camera_name_or_a_gain(self):
        self.assertFalse(sg.remember({"GAIN": 100}, 0.26, "2026-11-13"))
        self.assertFalse(sg.remember(self.HEAD, float("nan"), "2026-11-13"))
        self.assertFalse(sg.remember(self.HEAD, 0.0, "2026-11-13"))
        self.assertIsNone(sg.recall({}))
        with open(os.path.join(self.tmp, sg.REMEMBER_FILE), "w") as fh:
            fh.write("not json")
        self.assertIsNone(sg.recall(self.HEAD))                   # a damaged file is no error
        self.assertTrue(sg.remember(self.HEAD, 0.26, "2026-11-13"))

    def test_stored_gain_from_the_measured_units(self):
        unit = core.electrons_per_unit(1.0, 2, "RGGB", "green", None)     # TG at Bin 2: 8 per e-/ADU
        self.assertAlmostEqual(sg.stored_e_per_adu(0.26 * unit, unit), 0.26)
        self.assertTrue(math.isnan(sg.stored_e_per_adu("x", unit)))

    def test_fallback_uses_the_remembered_gain(self):
        """x283 / x295: no EGAIN and no measurement tonight: the remembered gain, not the one-frame lower limit and
        not the assumed 1 e-/ADU."""
        sg.remember(self.HEAD, 0.26, "2026-11-14", 20)
        bad = {"ok": False, "why": "only 2 usable frame pairs"}
        for sky_k in (0.2, float("nan")):      # with and without a one-frame estimate
            s = core.photon_noise_setup(self.HEAD, 1, "MONO", "mono", None, True, False, sky_k, 500.0, sky_meas=bad,
                                        remembered=sg.recall(self.HEAD))
            self.assertEqual((s["source"], s["sky_method"]), ("sky", "remembered"))
            self.assertAlmostEqual(s["k_median"], 0.26)
            self.assertIn("the gain the sky measured with this camera before is used: 0.26 e-/ADU", s["note"])
            self.assertIn("only 2 usable frame pairs", s["note"])
            self.assertEqual(s["warning"], "")
        note = tui.gain_note({"source": "sky", "k": s["k_median"], "sky_method": "remembered",
                              "remembered": s["remembered"]})
        self.assertTrue(note.startswith("Gain as the sky measured it with this camera before, 0.26 e-/ADU"))
        self.assertIn("measured on 2026-11-14", note)

    def test_order_of_the_gains(self):
        rem = {"e_per_adu": 0.5, "nights": 3, "last": "2026-11-14"}
        # tonight's own measurement wins over the remembered one
        s = core.photon_noise_setup(self.HEAD, 1, "MONO", "mono", None, True, False, 0.2, 500.0, sky_meas=self.MEAS,
                                    remembered=rem)
        self.assertEqual(s["sky_method"], "measured")
        self.assertAlmostEqual(s["k_median"], 0.26)
        # EGAIN in the header wins over the remembered one
        s = core.photon_noise_setup(dict(self.HEAD, EGAIN=0.25), 1, "MONO", "mono", None, True, False, 0.2, 500.0,
                                    remembered=rem)
        self.assertEqual(s["source"], "EGAIN")
        # pre-calibrated frames never use it (their values may be rescaled)
        s = core.photon_noise_setup(self.HEAD, 1, "MONO", "mono", None, True, True, 0.2, 500.0, remembered=rem)
        self.assertNotEqual(s.get("sky_method"), "remembered")
        # nothing remembered: the old fallbacks, word for word
        s = core.photon_noise_setup(self.HEAD, 1, "MONO", "mono", None, True, False, 0.2, 500.0)
        self.assertEqual(s["sky_method"], "lower limit")
        s = core.photon_noise_setup(self.HEAD, 1, "MONO", "mono", None, True, False)
        self.assertEqual(s["source"], "assumed")

    def test_with_a_flat_the_remembered_gain_is_a_map(self):
        flat = np.ones((8, 8))
        flat[:, 4:] = 1.2
        s = core.photon_noise_setup(self.HEAD, 1, "MONO", "mono", flat, True, False,
                                    remembered={"e_per_adu": 0.26, "nights": 1, "last": "2026-11-14"})
        self.assertEqual(np.shape(s["electrons"]), (8, 8))

    def test_onsky_log_says_remembered(self):
        import onsky_log
        out = onsky_log.electron_scale({"source": "sky", "electrons": 0.26, "k_median": 0.26,
                                        "sky_method": "remembered"})
        self.assertEqual(out["source"], "sky")
        self.assertIn("on earlier nights", out["note"])

    def test_app_hook(self):
        import clearv_app as ca
        setup = {"sky_method": "measured"}
        self.assertTrue(ca._sky_gain_remember(self.HEAD, setup, self.MEAS, 1, "MONO", "mono", None, []))
        self.assertAlmostEqual(ca._sky_gain_recall(self.HEAD)["e_per_adu"], 0.26)
        # not after a night that could not measure it, and never an error
        self.assertFalse(ca._sky_gain_remember(self.HEAD, {"sky_method": "lower limit"}, {"ok": False}, 1, "MONO",
                                               "mono", None, []))
        self.assertFalse(ca._sky_gain_remember(None, None, None, 1, "MONO", "mono", None, None))


class EgainOverTwoTimesOff(unittest.TestCase):
    """m473. #104 (confirmed first): EGAIN was always used and a measured sky gain more than 1.5x off only gave a
    caution. Now over 2x off, with the whole spread beyond 2x, the sky's gain is used."""
    HEAD = {"INSTRUME": "Cam", "GAIN": 100, "OFFSET": 50, "XBINNING": 1, "EGAIN": 1.0}

    def meas(self, k, half=0.02):
        return {"ok": True, "k": k, "k_lo": k - half, "k_hi": k + half, "n_pairs": 20, "floor_var": 1.3,
                "floor_source": "darks"}

    def setup(self, k, level=5000.0, **kw):
        return core.photon_noise_setup(self.HEAD, 1, "MONO", "mono", None, True, False, 0.2, level,
                                       sky_meas=self.meas(k, **kw))

    def test_constants(self):
        self.assertEqual((core.MEAS_CHECK, core.MEAS_OVERRIDE), (1.5, 2.0))

    def test_sky_far_below_egain_is_used(self):
        s = self.setup(0.26)
        self.assertEqual((s["source"], s["sky_method"]), ("sky", "measured"))
        self.assertAlmostEqual(s["k_median"], 0.26)
        self.assertEqual(s["egain_ignored"], 1.0)
        self.assertEqual(s["warning"], "")              # the action is taken: a plain note, no ⚠
        self.assertIn("over 2x off, so the sky's gain is used for the error bars and EGAIN is not", s["note"])
        note = tui.gain_note({"source": "sky", "k": 0.26, "sky_method": "measured", "egain_ignored": 1.0})
        self.assertIn("the header EGAIN 1 e-/ADU is over 2x off it and was not used", note)

    def test_sky_far_above_egain_is_used(self):
        s = self.setup(3.0)
        self.assertEqual(s["source"], "sky")
        self.assertIn("EGAIN may be too small", s["note"])

    def test_between_1p5_and_2_keeps_egain_with_the_caution(self):
        for k in (0.6, 1.7):
            s = self.setup(k)
            self.assertEqual(s["source"], "EGAIN", k)
            self.assertTrue(s["warning"], k)
            self.assertNotIn("egain_ignored", s)

    def test_agreeing_gain_is_untouched(self):
        s = self.setup(1.05)
        self.assertEqual((s["source"], s["warning"]), ("EGAIN", ""))

    def test_spread_reaching_inside_2x_keeps_egain(self):
        self.assertEqual(self.setup(0.45, half=0.1)["source"], "EGAIN")      # k_hi 0.55 > 0.5
        self.assertEqual(self.setup(2.1, half=0.2)["source"], "EGAIN")       # k_lo 1.9 < 2

    def test_faint_sky_is_not_trusted_downwards(self):
        self.assertEqual(self.setup(0.26, level=20.0)["source"], "EGAIN")    # under SKY_CHECK_E electrons

    def test_one_frame_lower_limit_never_overrides(self):
        s = core.photon_noise_setup(self.HEAD, 1, "MONO", "mono", None, True, False, 0.2, 5000.0)
        self.assertEqual(s["source"], "EGAIN")


class SavedPriors(unittest.TestCase):
    def fit(self, planet, n=150, seed=5):
        rng = np.random.default_rng(seed)
        tcen = PLANET["t0"] + 2.5 * 100
        t = np.linspace(tcen - 0.12, tcen + 0.12, n)
        u1, u2 = tc.limb_darkening_guess(5800, "CV")
        f = tc.occultation(tc.separation(t, tcen, 2.5, 8.0, 0.3), 0.12, u1, u2) + rng.normal(0, 0.002, n)
        return tc.fit_transit(t, f, np.full(n, 0.002), None, planet, detrend_airmass=False, mcmc_steps=150)

    def test_round_trip_through_the_settings_lines(self):
        r = self.fit(dict(PLANET, ld_band="CV"))
        lines = tc.fit_inputs_lines(r)
        saved = tc.parse_fit_inputs("Source: x\nAperture: r 8\n" + "\n".join(lines) + "\n")
        self.assertIsNotNone(saved)
        p = saved["planet"]
        for key in ("period", "period_err", "t0", "t0_err", "a_rs", "a_rs_err", "b", "b_err", "k", "t14", "teff"):
            self.assertAlmostEqual(p[key], PLANET[key], places=12, msg=key)
        self.assertEqual(saved["priors"], {k: tuple(v) for k, v in r["priors"].items()})
        self.assertEqual(tuple(saved["ld"]), tuple(r["ld_prior"]))
        # the fit CSV's comments and the fit file's block read the same
        self.assertEqual(tc.parse_fit_inputs("\n".join("# " + ln for ln in lines))["priors"], saved["priors"])
        block = "Fit inputs (full precision)\n" + "\n".join("  " + ln.replace("Fit input ", "", 1) for ln in lines)
        self.assertEqual(tc.parse_fit_inputs("x\n\n" + block + "\n\nNotes\n  - y\n")["priors"], saved["priors"])

    def test_no_inputs(self):
        self.assertIsNone(tc.parse_fit_inputs("Source: x\nGain: 1\n"))
        self.assertIsNone(tc.parse_fit_inputs(""))

    def test_flags_are_read(self):
        saved = tc.parse_fit_inputs("Fit input period: 2.5 +/- 1e-06 d\nFit input a/R*: 8 (no error given) (derived "
                                    "from the duration)\nFit input b: 0.3 (no error given) (assumed)\n"
                                    "Fit input t0: not known (BJD_TDB) (no ephemeris: no O - C)\n")
        p = saved["planet"]
        self.assertTrue(p["a_rs_derived"] and p["b_assumed"])
        self.assertIsNone(p["t0"])
        self.assertIsNone(saved["priors"])

    def test_refit_uses_exactly_the_saved_priors(self):
        """Today's catalog values have moved; the refit with the saved inputs has the old fit's priors to the bit,
        and the same result (same data, same seed)."""
        old = self.fit(dict(PLANET, ld_band="CV"))
        saved = tc.parse_fit_inputs("\n".join(tc.fit_inputs_lines(old)))
        today = dict(PLANET, a_rs=8.6, a_rs_err=0.05, b=0.15, b_err=0.01, teff=6400, t0=2460000.004, ld_band="CV")
        moved = self.fit(today)
        self.assertNotEqual(moved["priors"], old["priors"])
        planet = tc.apply_saved_inputs(today, saved, "X_settings.txt")
        self.assertEqual(planet["name"], "T b")
        again = self.fit(planet)
        self.assertEqual(again["priors"], old["priors"])
        self.assertEqual(tuple(again["ld_prior"]), tuple(old["ld_prior"]))
        self.assertEqual(again["summary"]["tc"], old["summary"]["tc"])
        self.assertEqual(again["summary"]["depth"], old["summary"]["depth"])
        self.assertTrue(any("saved priors: the values above were read from X_settings.txt" in ln
                            for ln in tc.fit_inputs_lines(again)))
        self.assertFalse(any("saved priors" in ln for ln in tc.fit_inputs_lines(old)))

    def test_old_file_without_the_priors_line(self):
        saved = tc.parse_fit_inputs("Fit input period: 2.5 +/- 1e-06 d\nFit input a/R*: 8 +/- 0.2\n"
                                    "Fit input b: 0.3 +/- 0.05\n")
        planet = tc.apply_saved_inputs(dict(PLANET, a_rs=9.0), saved, "old.txt")
        self.assertNotIn("priors", planet["saved_priors"])
        self.assertEqual(tc.prepare_priors(planet)["a_rs"], (8.0, 0.24))       # the usual rule, from the saved value


@unittest.skipUnless(HAVE_TK, "needs a display")
class Page(Base):
    def setUp(self):
        super().setUp()
        tp = self.app.transit_page
        tp.result, tp.report, tp._checking, tp._fitting = None, None, False, False
        tp._drop_saved_priors()
        self.app.busy = False

    def tearDown(self):
        tp = self.app.transit_page
        tp.result, tp.report, tp._checking, tp._fitting = None, None, False, False
        tp._drop_saved_priors()
        tp._comp_driver = ""
        self.app.busy = False
        self.app.gates.refresh()
        super().tearDown()

    # ---- #35 / m754 ----
    def test_untick_button_follows_the_comp_check(self):
        app, tp = self.app, self.app.transit_page
        self.assertFalse(tp.groups.boxes["comps"].is_shown(tp.untick_button))
        tp.result = result(flagged=1)
        tp._show_comp_driver()
        self.assertTrue(tp.groups.boxes["comps"].is_shown(tp.untick_button))
        self.assertEqual(tp.untick_button.cget("text"), "Open Use comps, untick C2")
        with mock.patch.object(app, "choose_comps") as cc, \
                mock.patch.object(tp, "_comp_windows_ok", return_value=""):
            tp.open_use_comps_untick()
        cc.assert_called_once_with(untick=["C2"])
        self.assertTrue(any("Use comps opened with C2 unticked" in ln for ln in self.lines))
        # no single comp named: no button
        tp.result = result()
        tp._show_comp_driver()
        self.assertFalse(tp.groups.boxes["comps"].is_shown(tp.untick_button))
        # nor under a report
        tp.result = result(flagged=0)
        tp.report = {"planet": {}}
        tp._show_comp_driver()
        self.assertFalse(tp.groups.boxes["comps"].is_shown(tp.untick_button))

    def test_use_comps_window_opens_unticked_with_the_flip_step(self):
        app, ca = self.app, self.ca
        comps = [M.A, M.B, M.X]
        cat = {M.A: 8.728, M.B: 11.31, M.X: 10.2}
        app.observations = M._points(comps, cat)
        app.comps_used, app.active_comps, app.check_used = list(comps), [], ""
        app.comp_catalog_mags = dict(cat)
        rows = [{"label": M.A, "flip_step": 0.065, "verdict": "steady"},
                {"label": M.B, "flip_step": float("nan"), "verdict": "steady"},
                {"label": M.X, "flip_step": -0.064, "verdict": "steady"}]
        with mock.patch.object(ca.core, "comp_health", return_value={"summary": rows}), \
                mock.patch.object(app, "_comp_catalog", return_value=dict(cat)):
            win = ca.UseCompsWindow(app, untick=[M.X])
        try:
            self.assertTrue(win.vars[M.A].get() and win.vars[M.B].get())
            self.assertFalse(win.vars[M.X].get())
            texts = []
            stack = [win.win]
            while stack:
                w = stack.pop()
                try:
                    texts.append(str(w.cget("text")))
                except Exception:
                    pass
                stack.extend(w.winfo_children())
            self.assertIn("Flip step", texts)
            self.assertIn("+65 mmag", texts)
            self.assertIn("-64 mmag", texts)
            self.assertIn(ca.USE_COMPS_FLIP_HINT, texts)
            self.assertIn(ca.use_comps_untick_text([M.X]), texts)
        finally:
            win.win.destroy()
        self.assertEqual(ca.use_comps_flip_text(None), "—")
        self.assertEqual(ca.use_comps_flip_text(float("nan")), "—")

    # ---- m811 ----
    def _jd_report(self):
        t = 2460250.0 + np.linspace(-0.1, 0.1, 40)
        return {"header": {"DATE_TYPE": "JD_UTC", "OBSLAT": "35.4", "OBSLON": "-81.9"}, "t": t.copy(),
                "flux": np.ones(40), "err": np.full(40, 0.002), "airmass": None, "results": {},
                "planet": {"name": "WASP-999 b", "host": "WASP-999", "period": 2.5, "t0": 2460000.0}, "path": ""}

    def test_jd_utc_report_is_converted_when_the_position_lands(self):
        tp = self.app.transit_page
        rep = self._jd_report()
        t_old = rep["t"].copy()
        tp._report_time_system(rep)
        self.assertTrue(rep["radec_pending"])
        self.assertEqual(rep["date_type"], "JD_UTC")
        tp.report = rep
        tp._eph_pending = rep
        with mock.patch.object(tp, "draw"), mock.patch.object(tp, "_fill_table"):
            tp._ephemeris_found(rep, None, "", (41.5, 50.9, "NASA Exoplanet Archive"), False)
        self.assertEqual(rep["date_type"], "BJD_TDB")
        self.assertFalse(rep["radec_pending"])
        shift = (rep["t"] - t_old) * 86400.0
        self.assertTrue(np.all(np.abs(shift) < 600.0) and np.all(np.abs(shift) > 20.0))    # light time + TDB − UTC
        self.assertIn("looked up from the planet (NASA Exoplanet Archive)", rep["time_note"])
        self.assertNotIn("⚠", rep["time_note"])
        self.assertAlmostEqual(rep["planet"]["t0"], 2460000.0 + float(np.median(rep["t"] - t_old)), delta=1e-5)
        self.assertIsNone(tp._eph_pending)
        self.assertTrue(any("converted them to BJD_TDB" in ln for ln in self.lines))

    def test_jd_utc_report_with_no_position_found_keeps_the_caution(self):
        tp = self.app.transit_page
        rep = self._jd_report()
        tp._report_time_system(rep)
        tp.report = rep
        with mock.patch.object(tp, "draw"), mock.patch.object(tp, "_fill_table"):
            tp._ephemeris_found(rep, None, "offline", None, False)
        self.assertEqual(rep["date_type"], "JD_UTC")
        self.assertEqual(rep["time_note"], tui.JD_UTC_NOT_CONVERTED)
        self.assertTrue(any("could not be looked up (offline)" in ln for ln in self.lines))

    def test_lookup_asks_for_the_position(self):
        tp = self.app.transit_page
        rep = self._jd_report()
        tp._report_time_system(rep)
        tp.report = rep
        got = {}

        class T:
            def __init__(self, target=None, daemon=None):
                self.target = target

            def start(self):
                self.target()
        with mock.patch.object(tui.tc, "lookup_planet", return_value={"ra": 41.5, "dec": 50.9, "t0": 1.0,
                                                                        "period": 2.5, "source": "NEA"}), \
                mock.patch.object(tui.threading, "Thread", T), \
                mock.patch.object(self.app, "call_ui", lambda f, *a: got.setdefault(getattr(f, "__name__", "?"), a)):
            tp._find_ephemeris(rep, want_eph=False, want_pos=True)
        args = got["_ephemeris_found"]
        self.assertIsNone(args[1])                           # the report keeps its own ephemeris
        self.assertEqual(args[3], (41.5, 50.9, "NEA"))
        self.assertFalse(args[4])

    def test_known_position_converts_at_once(self):
        tp = self.app.transit_page
        rep = self._jd_report()
        with mock.patch.object(tp, "_report_radec", return_value=(41.5, 50.9)):
            tp._report_time_system(rep)
        self.assertEqual(rep["date_type"], "BJD_TDB")
        self.assertEqual(rep["time_note"], "The report's times are JD_UTC; SHOBS-P converted them to BJD_TDB.")

    # ---- #93 ----
    def test_fit_with_saved_priors_button(self):
        app, tp = self.app, self.app.transit_page
        self.assertEqual(tp.saved_priors_button.cget("text"), "Fit with saved priors…")
        app.gates.refresh()
        self.assertIn("disabled", tp.saved_priors_button.state())            # no night, no report
        path = os.path.join(self.dir, "X_settings.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("Source: x\nFit input period: 2.5 +/- 1e-06 d\nFit input t0: 2460000 +/- 0.0001 (BJD_TDB)\n"
                     "Fit input a/R*: 8 +/- 0.2\nFit input b: 0.3 +/- 0.05\n"
                     "Fit input limb darkening prior: u1 0.4, u2 0.25, width 0.1\n"
                     "Fit input shape priors used: a_rs 8 +/- 0.24, b 0.3 +/- 0.05 (published values with the sigma "
                     "floors)\n")
        planet = dict(PLANET, a_rs=9.0)
        seen = {}
        with mock.patch.object(tp, "_saved_priors_why", return_value=""), \
                mock.patch.object(tp, "current_planet", return_value=planet), \
                mock.patch.object(tp, "fit", side_effect=lambda *a, **k: seen.update(
                    planet=tp._with_saved_priors(tp.current_planet()))):
            tp.fit_with_saved_priors(path)
            self.assertEqual(tp.saved_priors_button.cget("text"), "Fit with the current priors")
            self.assertEqual(seen["planet"]["a_rs"], 8.0)
            self.assertEqual(seen["planet"]["saved_priors"]["priors"], {"a_rs": (8.0, 0.24), "b": (0.3, 0.05)})
            self.assertEqual(seen["planet"]["saved_priors"]["ld"], (0.4, 0.25, 0.1))
            self.assertEqual(seen["planet"]["saved_priors"]["file"], "X_settings.txt")
            self.assertTrue(any("fitting with the priors saved in X_settings.txt" in ln for ln in self.lines))
            # pressed again: back to the current priors, and a fit
            seen.clear()
            tp.fit_with_saved_priors()
            self.assertEqual(tp.saved_priors_button.cget("text"), "Fit with saved priors…")
            self.assertNotIn("saved_priors", seen["planet"])

    def test_saved_priors_of_another_source_are_dropped(self):
        tp = self.app.transit_page
        tp._saved_priors = {"saved": {"planet": {"period": 2.5}, "priors": {}, "ld": None, "n": 1}, "file": "f.txt",
                            "key": ("night", "JD1", "tb")}
        with mock.patch.object(tp, "_saved_key", return_value=("night", "JD2", "tb")):
            p = tp._with_saved_priors(dict(PLANET))
        self.assertNotIn("saved_priors", p)
        self.assertIsNone(tp._saved_priors)
        self.assertTrue(any("no longer in use" in ln for ln in self.lines))

    def test_file_without_inputs_and_another_planets_file(self):
        tp = self.app.transit_page
        bad = os.path.join(self.dir, "plain.txt")
        with open(bad, "w") as fh:
            fh.write("Source: x\n")
        other = os.path.join(self.dir, "other_settings.txt")
        with open(other, "w") as fh:
            fh.write("Fit input period: 3.9 +/- 1e-06 d\n")
        with mock.patch.object(tp, "_saved_priors_why", return_value=""), \
                mock.patch.object(tp, "current_planet", return_value=dict(PLANET)), \
                mock.patch.object(tp, "fit") as fit:
            with mock.patch.object(tui.messagebox, "showinfo") as box:
                tp.fit_with_saved_priors(bad)
            self.assertIn("plain.txt holds no fit inputs", box.call_args[0][1])
            fit.assert_not_called()
            with mock.patch.object(tui, "core_ask_no", return_value=False) as ask:
                tp.fit_with_saved_priors(other)
            self.assertIn("period 3.900000 d; the planet here has 2.500000 d", ask.call_args[0][1])
            fit.assert_not_called()
            self.assertIsNone(tp._saved_priors)
            with mock.patch.object(tui, "core_ask_no", return_value=True):
                tp.fit_with_saved_priors(other)
            fit.assert_called_once()

    # ---- nits ----
    def test_trim_line_does_not_keep_the_last_fits_words(self):
        tp = self.app.transit_page
        tp._trims = []
        tp.result = {"trim": {"spans": [{"clock": "2130–2200", "utc": "", "n": 12}], "n": 12}}
        tp._update_trim_text()
        self.assertTrue(tp.trim_text.get().startswith("Trimmed: 2130–2200"))
        import inspect
        src = inspect.getsource(tui.TransitPage.open_report)
        self.assertLess(src.index("self.result = None"), src.index("self._update_trim_text()"))
        src = inspect.getsource(tui.TransitPage._use_night)
        self.assertLess(src.index("self.result = None"), src.index("self._update_trim_text()"))
        tp.result = None
        tp._update_trim_text()
        self.assertNotIn("Trimmed", tp.trim_text.get())

    def test_night_box_follows_a_new_night(self):
        app, tp = self.app, self.app.transit_page
        cat = {M.A: 8.728, M.B: 11.31}
        app.observations = M._points([M.A, M.B], cat, nights=("JD2460950", "JD2460951"))
        tp._nights_seen = None
        with mock.patch.object(tp, "_use_night") as use:
            tp.refresh()
            self.assertEqual(tp.night.get(), "JD2460951")          # the newest, as before
            tp.night.set("JD2460950")                              # the user goes back to the first night
            tp.refresh()
            self.assertEqual(tp.night.get(), "JD2460950")          # nothing new: it stays
            use.reset_mock()
            app.observations = app.observations + M._points([M.A, M.B], cat, nights=("JD2460949",))
            tp.refresh()
            self.assertEqual(tp.night.get(), "JD2460949")          # the night just added, though not the newest
            use.assert_called_once_with(auto=False)
            self.assertTrue(any("the night just added" in ln for ln in self.lines))
            # not while a fit runs, and not under a report
            app.observations = app.observations + M._points([M.A, M.B], cat, nights=("JD2460955",))
            tp._fitting = True
            tp.refresh()
            self.assertEqual(tp.night.get(), "JD2460949")
        tp._fitting = False


if __name__ == "__main__":
    unittest.main()
