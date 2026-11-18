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
        cat = [ln for ln in self.lines if ln.startswith("Fit input catalog:")][0]
        self.assertNotIn("?", cat)


class TestEphemerisWords(unittest.TestCase):
    def test_under_a_minute(self):
        self.assertEqual(tc.ephemeris_note(8, 0.4), "; most precise of 8 published ephemerides, under 1 min now")
        self.assertEqual(tc.ephemeris_note(8, 1.0), "; most precise of 8 published ephemerides, ±1 min now")
        self.assertEqual(tc.ephemeris_note(3, 3.2), "; most precise of 3 published ephemerides, ±3 min now")
        self.assertEqual(tc.ephemeris_note(1, 0.4), "")

    def test_dedupe_without_a_reference_link(self):
        # Fable review: NEA with no reference link but an eph note gives reference "; most precise of ..."
        r = _result()
        r["planet"]["reference"] = "; most precise of 8 published ephemerides, under 1 min now"
        r["eph_source"] = "NASA Exoplanet Archive (most precise of 8 published ephemerides, under 1 min now)"
        cat = [ln for ln in tc.fit_inputs_lines(r) if ln.startswith("Fit input catalog:")][0]
        self.assertNotIn("; ephemeris from", cat)


class TestAscii(unittest.TestCase):
    def test_letters_without_accents(self):
        self.assertEqual(core.ascii_safe("Yalçınkaya"), "Yalcinkaya")
        self.assertEqual(core.ascii_safe("Łódź Ørsted Straße Æ"), "Lodz Orsted Strasse AE")
        self.assertEqual(core.ascii_safe("中"), "?")   # anything else still a '?'


class TestGainCheckLine(unittest.TestCase):
    MEAS = {"k": 3.0, "pairs": 11, "k_lo": 3.0, "k_hi": 3.02}

    def _model(self, **extra):
        """The dict the app hands to sky_check_line: clearv_app's _noise_model, EGAIN's figure under "k"."""
        m = {"source": "EGAIN", "e_per_adu": 0.243, "k": 2.857, "sky_method": "measured", "sky_meas": dict(self.MEAS)}
        m.update(extra)
        return m

    def test_leads_with_e_per_adu(self):
        import transit_ui as tu
        line = tu.sky_check_line(self._model())
        self.assertTrue(line.startswith("Gain check from the night's sky: 0.255 e-/ADU, 1.05 x the header EGAIN "
                                        "(3 e- per image unit; from 11 frame pairs"), line)
        line.encode("ascii")
        # photon_noise_setup's own key works too
        pn = self._model()
        pn["k_median"] = pn.pop("k")
        self.assertTrue(tu.sky_check_line(pn).startswith("Gain check from the night's sky: 0.255 e-/ADU"))
        # without the EGAIN-side figure, the old words stay
        pn.pop("k_median")
        self.assertTrue(tu.sky_check_line(pn).startswith("Gain check from the night's sky: 3 e- per image unit ("))

    def test_real_noise_model_shape(self):
        """The keys clearv_app copies from photon_noise_setup (Fable review: the test must use the app's dict)."""
        src = open(os.path.join(HERE, "clearv_app.py"), encoding="utf-8").read()
        self.assertIn('"k": setup.get("k_median")}', src)

    def test_camera_averaged(self):
        import transit_ui as tu
        line = tu.sky_check_line(self._model(camera_bin=3, camera_bin_mode="averaged", e_per_stored_adu=2.187))
        self.assertTrue(line.startswith("Gain check from the night's sky: 2.3 e- per stored ADU (camera bin 3x3 "
                                        "averaged), 1.05 x what the header EGAIN gives (3 e- per image unit;"), line)
        summed = tu.sky_check_line(self._model(camera_bin=2, camera_bin_mode="summed"))
        self.assertTrue(summed.startswith("Gain check from the night's sky: 0.255 e-/ADU"), summed)


class TestMasterSize(unittest.TestCase):
    def test_plain_message(self):
        with self.assertRaises(ValueError) as cm:
            core.check_master_size(np.zeros((4176, 6248)), np.zeros((500, 650)), "bias", "flats", "Bias")
        msg = str(cm.exception)
        self.assertIn("The master bias is 6248x4176 px but the flats are 650x500 px", msg)
        self.assertIn("Empty the Bias row on the Input page", msg)
        core.check_master_size(None, np.zeros((5, 5)), "bias", "flats", "Bias")
        core.check_master_size(np.zeros((5, 5)), np.zeros((5, 5)), "bias", "flats", "Bias")

    def test_calibrate_frame_says_it(self):
        m = core.Masters(bias=np.zeros((40, 60), np.float32))
        with self.assertRaises(ValueError) as cm:
            core.calibrate_frame(np.ones((10, 20), np.float32), {"EXPTIME": 60.0}, m)
        self.assertIn("The master bias is 60x40 px but this light is 20x10 px: either the master is from another "
                      "camera or binning (empty the Bias row on the Input page", str(cm.exception))
        self.assertIn("or this light is not from the same camera and binning as the others", str(cm.exception))
        ok = core.calibrate_frame(np.ones((10, 20), np.float32), {"EXPTIME": 60.0},
                                  core.Masters(bias=np.zeros((10, 20), np.float32)))
        self.assertEqual(ok.shape, (10, 20))

    def test_dark_flat_as_pedestal(self):
        m = core.Masters(bias=np.zeros((40, 60), np.float32), pedestal="darkflat")
        with self.assertRaises(ValueError) as cm:
            core.calibrate_frame(np.ones((10, 20), np.float32), {"EXPTIME": 60.0}, m)
        self.assertIn("The master dark flat is 60x40 px", str(cm.exception))
        self.assertIn("empty the Dark flats row", str(cm.exception))

    def test_unapplied_dark_not_checked(self):
        m = core.Masters(dark=np.zeros((40, 60), np.float32), dark_exptime=0.0)
        out = core.calibrate_frame(np.ones((10, 20), np.float32), {"EXPTIME": 60.0}, m)
        self.assertEqual(out.shape, (10, 20))


class TestPreviewLine(unittest.TestCase):
    def test_follows_the_image(self):
        src = open(os.path.join(HERE, "clearv_app.py"), encoding="utf-8").read()
        changed = src[src.index("def _folders_changed"):src.index("def _clear_marks")]
        self.assertIn('self.debayer_info.set("No preview yet.")', changed)
        fail = src[src.index("def _calibration_failed"):src.index("def apply_debayer")]
        self.assertIn('self.preview is None:', fail)
        start = src[src.index("def run_calibration"):src.index("def _calibration_done")]
        self.assertNotIn('self.debayer_info.set("No preview yet.")', start)   # a cancel keeps the old image's line

    @unittest.skipUnless(os.environ.get("DISPLAY") or sys.platform != "linux", "needs a display")
    def test_failure_keeps_a_good_preview_line(self):
        import types
        from unittest import mock
        import clearv_app as ca
        fake = types.SimpleNamespace(log=lambda *_: None, preview=np.zeros((3, 3)),
                                     debayer_info=mock.Mock())
        with mock.patch.object(ca.messagebox, "showerror"):
            ca.App._calibration_failed(fake, "x")
            fake.debayer_info.set.assert_not_called()
            fake.preview = None
            ca.App._calibration_failed(fake, "x")
            fake.debayer_info.set.assert_called_once_with("No preview yet.")


if __name__ == "__main__":
    unittest.main()
