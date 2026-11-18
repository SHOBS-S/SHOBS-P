"""2.4.0 step 3, John's regression (18 Nov): text-only fixes found in the step 3 regression runs. Full-precision fit
inputs without binary noise; the catalog line says its ephemeris source once; "under 1 min now" for "±0 min now"; the
EGAIN gain check leads with e-/ADU; letters with no accent to drop keep a letter in ASCII files (Yalçınkaya); a master
that does not fit the frames gets a plain message; the Calibrate preview line is cleared when a calibration starts or
fails. None of these changes a number."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photometry_core as core    # noqa: E402
import transit_core as tc         # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _result():
    ref = "ephemeris: Yalçınkaya et al. 2024; most precise of 8 published ephemerides, under 1 min now"
    return {"period": 1.95509245,
            "eph_source": "NASA Exoplanet Archive (Yalçınkaya et al. 2024; most precise of 8 published ephemerides, "
                          "under 1 min now)",
            "planet": {"period_err": 4.8e-08, "t0": 2458411.09342, "t0_err": 5.5e-05, "a_rs": 7.51, "a_rs_err": 0.1,
                       "b": 0.669, "b_err": 0.018, "k": 0.139, "k_err": 0.0006, "t14": 0.0764, "teff": 5400.0,
                       "source": "NASA Exoplanet Archive", "reference": ref, "looked_up": "18 Nov 2026 19:59 UTC"},
            "ld_prior": (0.52, 0.20600000000000002, 0.1),
            "priors": {"a_rs": (7.51, 0.22529999999999997), "b": (0.669, 0.04)}}


class TestFitInputs(unittest.TestCase):
    def setUp(self):
        self.lines = tc.fit_inputs_lines(_result())
        self.text = "\n".join(self.lines)

    def test_no_binary_noise(self):
        self.assertIn("; depth 0.019321", self.text)
        self.assertNotIn("0.019321000000000005", self.text)
        self.assertIn("u2 0.206,", self.text)
        self.assertIn("a_rs 7.51 +/- 0.2253", self.text)
        self.assertNotIn("0000000", self.text)
        self.assertNotIn("9999999", self.text)

    def test_catalog_digits_kept(self):
        self.assertIn("Fit input t0: 2458411.09342 +/- 5.5e-05 (BJD_TDB)", self.lines)
        self.assertIn("Fit input period: 1.95509245 +/- 4.8e-08 d", self.lines)
        self.assertEqual(tc._full(2456265.154123), "2456265.154123")
        self.assertEqual(tc._full(0.13002083333333334, " d"), "0.130020833333333 d")
        self.assertEqual(tc._full(float("nan")), "not known")

    def test_catalog_line_once(self):
        cat = [ln for ln in self.lines if ln.startswith("Fit input catalog:")][0]
        self.assertEqual(cat.count("Yalcinkaya"), 1, cat)
        self.assertNotIn("; ephemeris from", cat)
        # a different ephemeris source is still said
        r = _result()
        r["eph_source"] = "EXOTIC's inits file x.json"
        cat2 = [ln for ln in tc.fit_inputs_lines(r) if ln.startswith("Fit input catalog:")][0]
        self.assertIn("; ephemeris from EXOTIC's inits file x.json", cat2)

    def test_ascii(self):
        for ln in self.lines:
            ln.encode("ascii")
            self.assertNotIn("?", ln.replace("? ", ""), ln)


class TestEphemerisWords(unittest.TestCase):
    def test_under_a_minute(self):
        src = open(os.path.join(HERE, "transit_core.py"), encoding="utf-8").read()
        self.assertIn('"under 1 min now"', src)
        self.assertIn('if best["sigma_min"] >= 1', src)


class TestAscii(unittest.TestCase):
    def test_letters_without_accents(self):
        self.assertEqual(core.ascii_safe("Yalçınkaya"), "Yalcinkaya")
        self.assertEqual(core.ascii_safe("Łódź Ørsted Straße Æ"), "Lodz Orsted Strasse AE")
        self.assertEqual(core.ascii_safe("中"), "?")   # anything else still a '?'


class TestGainCheckLine(unittest.TestCase):
    def test_leads_with_e_per_adu(self):
        import transit_ui as tu
        meas = {"k": 3.0, "pairs": 11, "k_lo": 3.0, "k_hi": 3.02}
        pn = {"source": "EGAIN", "e_per_adu": 0.243, "k_median": 2.857, "sky_meas": meas}
        line = tu.sky_check_line(pn)
        self.assertTrue(line.startswith("Gain check from the night's sky: 0.255 e-/ADU, 1.05 x the header EGAIN "
                                        "(3 e- per image unit; from 11 frame pairs"), line)
        line.encode("ascii")
        # without the EGAIN-side figure, the old words stay
        pn.pop("k_median")
        self.assertTrue(tu.sky_check_line(pn).startswith("Gain check from the night's sky: 3 e- per image unit ("))


class TestMasterSize(unittest.TestCase):
    def test_plain_message(self):
        with self.assertRaises(ValueError) as cm:
            core.check_master_size(np.zeros((4176, 6248)), np.zeros((500, 650)), "bias", "flats", "Bias")
        msg = str(cm.exception)
        self.assertIn("The master bias is 6248x4176 px but the flats are 650x500 px", msg)
        self.assertIn("Empty the Bias box on the Input page", msg)
        core.check_master_size(None, np.zeros((5, 5)), "bias", "flats", "Bias")
        core.check_master_size(np.zeros((5, 5)), np.zeros((5, 5)), "bias", "flats", "Bias")

    def test_calibrate_frame_says_it(self):
        m = core.Masters(bias=np.zeros((40, 60), np.float32))
        with self.assertRaises(ValueError) as cm:
            core.calibrate_frame(np.ones((10, 20), np.float32), {"EXPTIME": 60.0}, m)
        self.assertIn("The master bias is 60x40 px but the lights are 20x10 px", str(cm.exception))
        ok = core.calibrate_frame(np.ones((10, 20), np.float32), {"EXPTIME": 60.0},
                                  core.Masters(bias=np.zeros((10, 20), np.float32)))
        self.assertEqual(ok.shape, (10, 20))


class TestPreviewLine(unittest.TestCase):
    def test_cleared_on_start_and_failure(self):
        src = open(os.path.join(HERE, "clearv_app.py"), encoding="utf-8").read()
        fail = src[src.index("def _calibration_failed"):src.index("def apply_debayer")]
        self.assertIn('self.debayer_info.set("No preview yet.")', fail)
        start = src[src.index("def run_calibration"):src.index("def _calibration_done")]
        self.assertIn('self.debayer_info.set("No preview yet.")', start)


if __name__ == "__main__":
    unittest.main()
