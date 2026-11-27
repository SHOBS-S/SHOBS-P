"""2.4.0 fit-in F4-3, part 2: #115 finish. The field scan's per-point errors get a floor (scintillation and a
flat-field term) before the excess chi2nu Suspect test. The Suspect level stays 2.5 and the flat-field floor 0.003:
the retune on the two real GSC 03308-00462 scan nights (26 Nov, numbers beside SCAN_FLAT_FLOOR_MAG) found nothing to
gain from moving either. These tests fix the mechanics and that the floor does no harm."""
import inspect
import math
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import photometry_core as core  # noqa: E402


def field(seed=1, n_frames=97, n_stars=600, floor_true=0.004, amp=0.012, var_at=5, var_mag=9.0):
    """A scan night: photon + sky noise, a real floor of floor_true mag on every star, cloud on every frame, and one
    bright star with a sine of semi-amplitude amp."""
    rng = np.random.default_rng(seed)
    mag = np.sort(rng.uniform(8.5, 15.5, n_stars))
    mag[var_at] = var_mag
    flux = 10 ** (-0.4 * (mag - 20)) * 50
    phot = 1.0857 * np.sqrt(flux + 30.0 ** 2) / flux
    true_sig = np.hypot(phot, floor_true)
    inst = (-2.5 * np.log10(flux)[None, :] + rng.normal(0, 1, (n_frames, n_stars)) * true_sig[None, :]
            + rng.normal(0, 0.02, n_frames)[:, None])
    inst[:, var_at] += amp * np.sin(2 * np.pi * np.arange(n_frames) / 23.0)
    return inst, np.tile(phot, (n_frames, 1))


class Floor(unittest.TestCase):
    def test_constant_and_level(self):
        self.assertEqual(core.SCAN_FLAT_FLOOR_MAG, 0.003)
        self.assertEqual(core.SUSPECT_EXCESS_CHI2, 2.5)        # retuned on the two real nights: 2.5 stands

    def test_flat_floor_alone_when_scintillation_is_unknown(self):
        self.assertAlmostEqual(core.scan_error_floor(), 0.003)
        self.assertAlmostEqual(core.scan_error_floor(None, 60.0, 0.356), 0.003)       # no airmass
        self.assertAlmostEqual(core.scan_error_floor(1.3, 60.0, None), 0.003)         # no aperture
        self.assertEqual(core.scan_error_floor(flat_floor=0.0), 0.0)
        self.assertEqual(core.scan_error_floor(flat_floor=float("nan")), 0.0)

    def test_scintillation_in_quadrature(self):
        sc = 1.0857362 * core.scintillation_sigma(0.356, 1.3, 60.0, 300.0)
        self.assertAlmostEqual(core.scan_error_floor(1.3, 60.0, 0.356, 300.0), math.hypot(0.003, sc))
        self.assertAlmostEqual(core.scan_error_floor(1.3, 60.0, 0.356, 300.0, flat_floor=0.0), sc)
        # a stack passes its total exposure: 16 x 60 s twinkles a quarter as much as one frame
        s16 = core.scan_error_floor(1.3, 960.0, 0.356, 300.0, flat_floor=0.0)
        self.assertAlmostEqual(s16, sc / 4.0)
        # more airmass, more scintillation
        self.assertGreater(core.scan_error_floor(2.0, 60.0, 0.356, 300.0), core.scan_error_floor(1.1, 60.0, 0.356, 300.0))

    def test_frame_floor_from_the_header_or_the_field(self):
        setup = {"aperture_m": 0.356, "altitude_m": 300.0, "lat": 35.4, "lon": -81.9, "ra_hours": 2.8, "dec_deg": 50.9}
        jd = 2461358.60
        am = core.airmass(2.8, 50.9, 35.4, -81.9, jd)
        want = core.scan_error_floor(am, 60.0, 0.356, 300.0)
        self.assertAlmostEqual(core.scan_frame_floor({}, jd, 60.0, setup), want)                 # the field's position
        self.assertAlmostEqual(core.scan_frame_floor({"RA_HRS": 2.8, "DEC": 50.9}, jd, 60.0,
                                                     dict(setup, ra_hours=None, dec_deg=None)), want)   # the header's
        # nothing known: the flat floor only, never an error and never NaN
        self.assertAlmostEqual(core.scan_frame_floor({}, jd, 60.0, None), 0.003)
        self.assertAlmostEqual(core.scan_frame_floor({}, float("nan"), 60.0, setup), 0.003)
        self.assertAlmostEqual(core.scan_frame_floor({"RA_HRS": "x"}, jd, 60.0, dict(setup, lat=None)), 0.003)

    def test_log_line(self):
        fl = [core.scan_error_floor(1.3, 60.0, 0.356, 300.0)] * 97
        line = core.scan_floor_text(fl)
        self.assertTrue(line.strip().startswith("Scan errors: flat-field floor 0.003 mag and scintillation 0.00"))
        self.assertIn("(median of 97 frames) added to every point's photon and sky noise.", line)
        self.assertNotIn("⚠", line)
        bare = core.scan_floor_text([0.003] * 5)
        self.assertIn("Scintillation not added: it needs the telescope aperture", bare)
        self.assertEqual(core.scan_floor_text([0.0, 0.0], flat_floor=0.0), "")


class Variability(unittest.TestCase):
    def test_floor_goes_into_every_points_error(self):
        inst, merr = field()
        no = core.field_variability(inst, merr=merr)
        self.assertTrue(np.all(no["error_floor"] == 0))
        fl = np.full(inst.shape[0], 0.004)
        fl[3] = float("nan")                                   # a frame whose floor is unknown counts 0
        yes = core.field_variability(inst, merr=merr, floor=fl)
        self.assertEqual(yes["error_floor"][3], 0.0)
        self.assertEqual(yes["error_floor"][0], 0.004)
        one = core.field_variability(inst, merr=merr, floor=0.004)   # one number for all frames
        self.assertTrue(np.all(one["error_floor"] == 0.004))
        # scatter test untouched (it needs no error model)
        np.testing.assert_array_equal(no["excess"], yes["excess"])

    def test_bright_stars_errors_no_longer_read_low(self):
        """With no floor in the errors the brightest stars' chi2nu needs the 7b widening; with the true floor in,
        their raw chi2nu is 1 by itself."""
        inst, merr = field(floor_true=0.004, amp=0.0)
        fv = core.field_variability(inst, merr=merr)
        zp = fv["zp_err"]

        def raw_chi2(floor):
            sig = np.sqrt(merr ** 2 + zp[:, None] ** 2 + floor ** 2)
            return core.chi2nu_against_errors(fv["corrected"], fv["median"], sig)
        bright = np.argsort(fv["median"])[:40]
        self.assertGreater(np.nanmedian(raw_chi2(0.0)[bright]), 3.0)
        self.assertLess(abs(np.nanmedian(raw_chi2(0.004)[bright]) - 1.0), 0.25)

    def test_low_amplitude_bright_variable_stays_a_suspect(self):
        """The #115 case: a bright star varying by a few floors is over the 2.5 level with the floor in (a floor a
        little under and a little over the true one included), and under the scatter test's 3x: only the own-errors
        test finds it."""
        inst, merr = field(amp=0.008)
        for fl in (0.003, 0.004, 0.006):
            fv = core.field_variability(inst, merr=merr, floor=fl)
            self.assertGreaterEqual(fv["excess_chi2"][5], core.SUSPECT_EXCESS_CHI2, fl)
            self.assertLess(fv["excess"][5], core.SUSPECT_FACTOR)

    def test_floor_makes_no_false_suspects(self):
        """A quiet field (several seeds): the floor adds no Suspects by the own-errors test."""
        for seed in (2, 3, 4):
            inst, merr = field(seed=seed, amp=0.0)
            level = core.chi2_level(core.SUSPECT_EXCESS_CHI2, inst.shape[0])
            n0 = int(np.nansum(core.field_variability(inst, merr=merr)["excess_chi2"] >= level))
            n1 = int(np.nansum(core.field_variability(inst, merr=merr, floor=0.003)["excess_chi2"] >= level))
            self.assertLessEqual(n1, n0 + 1, seed)
            self.assertLessEqual(n1, 2, seed)

    def test_floor_moves_the_excess_only_a_little(self):
        """The excess is a ratio to stars as bright and 7b already normalises, so the floor shifts a star's value by
        a few percent: no ground for moving the 2.5 level without real nights."""
        inst, merr = field(amp=0.012)
        a = core.field_variability(inst, merr=merr)["excess_chi2"][5]
        b = core.field_variability(inst, merr=merr, floor=0.003)["excess_chi2"][5]
        self.assertLess(abs(b - a) / a, 0.10)


class Hook(unittest.TestCase):
    def test_the_scan_passes_the_floor(self):
        import clearv_app
        src = inspect.getsource(clearv_app)
        self.assertIn("core.scan_frame_floor(header, frame_jd, exp, scan_floor_setup)", src)
        self.assertIn("floor=scan_floors if len(scan_floors) == len(merr_all) else None", src)
        self.assertIn("core.scan_floor_text(scan_floors)", src)

    def test_setup_reads_the_boxes(self):
        import clearv_app

        class V:
            def __init__(self, v):
                self.v = v

            def get(self):
                return self.v
        app = type("A", (), {})()
        app.aperture_mm, app.elevation = V("356"), V("300")
        app.lat, app.lon, app.ra_hours, app.dec_deg = V("35.4"), V("-81.9"), V(""), V("50.9")
        s = clearv_app.App._scan_floor_setup(app)
        self.assertAlmostEqual(s["aperture_m"], 0.356)
        self.assertEqual((s["altitude_m"], s["lat"], s["lon"], s["ra_hours"], s["dec_deg"]),
                         (300.0, 35.4, -81.9, None, 50.9))
        app.aperture_mm = V("")
        self.assertIsNone(clearv_app.App._scan_floor_setup(app)["aperture_m"])


if __name__ == "__main__":
    unittest.main()


class SheetSweep(unittest.TestCase):
    """The sheet's wording rows of this package (house rule: an error says what to do; a button that cannot work
    yet is greyed with the reason on hover, not answered with a box)."""

    def test_fit_errors_say_what_to_do(self):
        import transit_core as tc
        self.assertIn("Refit with flagged frames", tc.fit_failed_help("only 14 usable points; a transit fit needs at "
                                                                      "least 20"))
        self.assertIn("simpler Detrend", tc.fit_failed_help("the least-squares fit did not converge with any detrend "
                                                            "choice"))
        self.assertIn("Input page", tc.fit_failed_help("the starting fit is outside the priors or bounds"))
        self.assertIn("Leave that night out", tc.fit_failed_help("n2.csv: the night alone could not be fitted (x)"))
        self.assertIn("Look up", tc.fit_failed_help("the planet's period is not known: look the planet up first"))
        self.assertEqual(tc.fit_failed_help("[Errno 13] Permission denied"), "")
        self.assertEqual(tc.fit_failed_help(None), "")

    def test_wording_in_the_sources(self):
        import multinight as mn
        import transit_ui as tui
        src_t, src_m = inspect.getsource(tui), inspect.getsource(mn)
        self.assertIn("tc.fit_failed_help(error[0])", src_t)
        self.assertIn("tc.fit_failed_help(error[0])", src_m)
        self.assertEqual(tui.SAVE_ELSEWHERE, "Save to another folder, or close the program that has the file open.")
        self.assertGreaterEqual(src_t.count("{SAVE_ELSEWHERE}"), 4)
        self.assertEqual(src_m.count("{tui.SAVE_ELSEWHERE}"), 3)
        self.assertIn("Save the report again with BJD_TDB times in the program that wrote it", tui.TIMES_NOT_CONVERTED)   # m812
        self.assertNotIn('showinfo("SHOBS-P", "Fit the nights first."', src_m)
        self.assertEqual(mn.FIT_NIGHTS_FIRST, "Fit the nights first (Fit nights).")

    def test_multinight_night_errors_say_what_to_do(self):
        import multinight as mn
        with self.assertRaises(mn.NightError) as ctx:
            mn.to_bjd_tdb([2460000.5], "MJD")
        self.assertIn("it reads BJD_TDB, HJD_UTC and JD_UTC", str(ctx.exception))
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "X_CV_01-OCT-2026_SHOBS-P_transit_fit.csv")
            with open(p, "w") as fh:
                fh.write("BJD_TDB,rel_flux,rel_flux_err,model,residual,flag\n2460000.5,1.0,,,,trimmed\n")
            with self.assertRaises(mn.NightError) as ctx:
                mn.load_fit_csv(p)
        self.assertIn("save the fit CSV again from the Transit fit page", str(ctx.exception))
