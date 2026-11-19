"""2.4.0 step 4 (s4-quick): #110 the AAVSO exoplanet report writes one DETREND_n column per name in
#DETREND_PARAMETERS (it always wrote one, the airmass); #111 lights from another camera never keep the last camera's
Bias / Flat / Dark flats, aperture and ADU limits, and a fix to the aperture boxes clears the aperture caution.
Neither changes a result number on a correctly set-up night."""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import transit_core as tc         # noqa: E402

try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


# ---- #110 -----------------------------------------------------------------------------------------------------------
def _result(detrend, n=6, airmass=True):
    t = 2461363.5 + 0.002 * np.arange(n)
    am = 1.1 + 0.01 * np.arange(n) if airmass else np.full(n, np.nan)
    return {"summary": {"tc": (2461363.506, 0.0004), "k": (0.11, 0.004), "a_rs": (8.1, 0.2), "b": (0.3, 0.05),
                        "inc": (87.9, 0.4), "depth": (0.0121, 0.0009), "t14": (0.11, 0.002)},
            "planet": {"name": "TOI-2570 b", "host": "TOI-2570", "period_err": 1e-6, "k": 0.11},
            "period": 2.98876, "priors": {"a_rs": (8.1, 0.24), "b": (0.3, 0.04)}, "ld_prior": (0.5, 0.2, 0.1),
            "detrend": list(detrend), "t": t, "flux": 1.0 - 0.001 * np.arange(n),
            "err_scaled": np.full(n, 0.002), "airmass": am}


META = {"obscode": "JABA", "version": "2.4.0", "obsdate": "2026-11-18", "exposure": 60, "filter": "CV"}


def _split(text):
    head = [ln for ln in text.splitlines() if ln.startswith("#")]
    data = [ln.split(",") for ln in text.splitlines() if ln and not ln.startswith("#")]
    params = next(ln for ln in head if ln.startswith("#DETREND_PARAMETERS=")).split("=", 1)[1]
    cols = next(ln for ln in head if ln.startswith("#DATE,"))[1:].split(",")
    return params, cols, data


class TestAavsoDetrendColumns(unittest.TestCase):
    def test_airmass(self):
        r = _result(["airmass"])
        params, cols, data = _split(tc.aavso_exoplanet_report(r, META))
        self.assertEqual(params, "AIRMASS")
        self.assertEqual(cols, ["DATE", "DIFF", "ERR", "DETREND_1"])
        self.assertTrue(all(len(row) == 4 for row in data))
        np.testing.assert_allclose([float(row[3]) for row in data], r["airmass"], atol=1e-7)
        # the airmass night's file is what it has always been, line for line
        self.assertEqual(",".join(data[0]), f"{r['t'][0]:.8f},{r['flux'][0]:.7f},0.0020000,1.1000000")

    def test_time_only_holds_the_time_not_the_airmass(self):
        r = _result(["time"])
        params, cols, data = _split(tc.aavso_exoplanet_report(r, META))
        self.assertEqual(params, "TIME")
        self.assertEqual(cols, ["DATE", "DIFF", "ERR", "DETREND_1"])
        self.assertTrue(all(len(row) == 4 for row in data))
        for row, t in zip(data, r["t"]):
            self.assertEqual(row[3], row[0])                 # the point's BJD_TDB, written as DATE
            self.assertAlmostEqual(float(row[3]), t, places=7)

    def test_both_gives_five_fields(self):
        r = _result(["airmass", "time"])
        text = tc.aavso_exoplanet_report(r, META)
        params, cols, data = _split(text)
        self.assertEqual(params, "AIRMASS, TIME")
        self.assertEqual(cols, ["DATE", "DIFF", "ERR", "DETREND_1", "DETREND_2"])
        self.assertEqual(len(data), len(r["t"]))
        self.assertTrue(all(len(row) == 5 for row in data), "AAVSO: not enough fields; need 5")
        np.testing.assert_allclose([float(row[3]) for row in data], r["airmass"], atol=1e-7)
        np.testing.assert_allclose([float(row[4]) for row in data], r["t"], atol=1e-7)

    def test_none_has_no_name_and_no_column(self):
        r = _result([])
        text = tc.aavso_exoplanet_report(r, META)
        params, cols, data = _split(text)
        self.assertEqual(params, "")                      # AAVSO: 0 to 4 names; NONE would count as one
        self.assertEqual(cols, ["DATE", "DIFF", "ERR"])
        self.assertTrue(all(len(row) == 3 for row in data))
        self.assertNotIn("NONE", text)

    def test_fields_always_three_plus_the_names(self):
        for det in ([], ["airmass"], ["time"], ["airmass", "time"], ["time", "airmass"]):
            params, cols, data = _split(tc.aavso_exoplanet_report(_result(det), META))
            n = len([p for p in params.split(",") if p.strip()])
            self.assertEqual(n, len(det))
            self.assertEqual(len(cols), 3 + n)
            self.assertEqual({len(row) for row in data}, {3 + n}, det)
            self.assertEqual([p.strip() for p in params.split(",") if p.strip()], [d.upper() for d in det])

    def test_missing_airmass_point_is_na(self):
        r = _result(["airmass", "time"])
        r["airmass"][2] = np.nan
        _p, _c, data = _split(tc.aavso_exoplanet_report(r, META))
        self.assertEqual(data[2][3], "na")
        self.assertEqual(len(data[2]), 5)

    def test_unknown_detrend_is_refused_not_guessed(self):
        with self.assertRaises(ValueError):
            tc.aavso_exoplanet_report(_result(["humidity"]), META)

    def test_reads_back(self):
        for det, has_am in ((["airmass"], True), (["airmass", "time"], True), (["time", "airmass"], True),
                            (["time"], False), ([], False)):
            r = _result(det)
            rep = tc.parse_exoplanet_report(tc.aavso_exoplanet_report(r, META))
            np.testing.assert_allclose(rep["t"], r["t"], atol=1e-7)
            np.testing.assert_allclose(rep["flux"], r["flux"], atol=1e-7)
            np.testing.assert_allclose(rep["err"], r["err_scaled"], atol=1e-7)
            if has_am:
                np.testing.assert_allclose(rep["airmass"], r["airmass"], atol=1e-7, err_msg=str(det))
            else:
                self.assertIsNone(rep["airmass"], det)   # the time column is never taken for the airmass

    def test_reads_older_files(self):
        # 2.3.6 and earlier: "AIRMASS, TIME" over one column (the airmass); EXOTIC: two columns, airmass first
        old = ("#TYPE=EXOPLANET\n#DETREND_PARAMETERS=AIRMASS, TIME\n#DATE,DIFF,ERR,DETREND_1\n"
               "2461363.5,1.0,0.002,1.21\n2461363.6,0.99,0.002,1.25\n")
        np.testing.assert_allclose(tc.parse_exoplanet_report(old)["airmass"], [1.21, 1.25])
        exotic = ("#TYPE=EXOPLANET\n#DETREND_PARAMETERS=AIRMASS, AIRMASS CORRECTION FUNCTION\n"
                  "#DATE,DIFF,ERR,DETREND_1,DETREND_2\n2461363.5,1.0,0.002,1.21,0.998\n")
        np.testing.assert_allclose(tc.parse_exoplanet_report(exotic)["airmass"], [1.21])

    def test_manual_says_so(self):
        ch19 = _read("docs", "manual", "19-transit-reports.md")
        self.assertIn("DETREND_2", ch19)
        self.assertIn("not enough fields", _read("docs", "manual", "23-messages-explained.md"))


# ---- #111 -----------------------------------------------------------------------------------------------------------
ASI = {"INSTRUME": "ZWO ASI2600MC Duo", "NAXIS1": 6248, "NAXIS2": 4176, "XBINNING": 1, "BITPIX": 16,
       "GAIN": 100, "OFFSET": 50, "EXPTIME": 120.0}
MOBS = {"TELESCOP": "MicroObservatory", "NAXIS1": 650, "NAXIS2": 500, "XBINNING": 2, "BITPIX": 16, "EXPTIME": 60.0}


class TestCameraSignature(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca

    def test_signature_and_words(self):
        ca = self.ca
        a, m = ca.camera_signature(ASI), ca.camera_signature(MOBS)
        self.assertEqual((a["nx"], a["ny"], a["xbin"], a["bits"]), (6248, 4176, 1, 16))
        self.assertEqual((m["nx"], m["ny"], m["xbin"], m["bits"]), (650, 500, 2, 12))
        self.assertEqual(ca.camera_words(a), "ZWO ASI2600MC Duo, 6248×4176")
        self.assertEqual(ca.camera_words(m), "650×500, binned 2x2, 12-bit")
        self.assertIsNone(ca.camera_signature({"INSTRUME": "x"}))
        self.assertEqual(ca.camera_bits({"BITPIX": 16, "DATAMAX": 4095}), 12)
        self.assertEqual(ca.camera_bits({"BITPIX": 16, "DATAMAX": 65535}), 16)
        self.assertIsNone(ca.camera_bits({"BITPIX": -32}))

    def test_same_camera(self):
        ca = self.ca
        a = ca.camera_signature(ASI)
        self.assertTrue(ca.same_camera(a, ca.camera_signature(dict(ASI, INSTRUME="zwo  asi2600mc duo", EXPTIME=30))))
        self.assertTrue(ca.same_camera(a, ca.camera_signature({k: v for k, v in ASI.items() if k != "INSTRUME"})))
        self.assertFalse(ca.same_camera(a, ca.camera_signature(MOBS)))
        self.assertFalse(ca.same_camera(a, ca.camera_signature(dict(ASI, INSTRUME="ZWO ASI2600MM Pro"))))
        self.assertFalse(ca.same_camera(a, ca.camera_signature(dict(ASI, XBINNING=2, NAXIS1=3124, NAXIS2=2088))))
        self.assertFalse(ca.same_camera(a, ca.camera_signature(dict(ASI, DATAMAX=4095))))
        self.assertFalse(ca.same_camera(a, None))


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestCameraChange(unittest.TestCase):
    """The issue's case: HAT-P-32 b on the ASI2600MC Duo (masters, r 8 / 14-22, linear 40000), New reduction, then a
    MicroObservatory night (650 x 500, 12-bit, darks only)."""

    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        cls.tmp = tempfile.mkdtemp(prefix="shobs_p_240_o_")
        cls._patches = [mock.patch.object(ca, "SETTINGS_FILE", os.path.join(cls.tmp, "settings.json")),
                        mock.patch.object(ca, "LOG_FILE", os.path.join(cls.tmp, "log.txt")),
                        mock.patch.dict(os.environ, {"SHOBS_DATA_HOME": os.path.join(cls.tmp, "shobs")})]
        for p in cls._patches:
            p.start()
        cls.app = ca.App()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        for p in reversed(cls._patches):
            p.stop()

    def setUp(self):
        app = self.app
        app._loading_meta = False
        app.busy = False
        app._camera_last = None
        app._camera_folders = None
        app._camera_aperture_pending = None
        app._own_setup = None
        app.settings.pop("camera_last", None)
        app.settings.pop("camera_limits", None)
        for name in ("bias_dir", "dark_dir", "darkflat_dir", "flat_dir", "light_dir"):
            getattr(app, name).set("")
        app.radius.set(8.0)
        app.sky_in.set(14.0)
        app.sky_out.set(22.0)
        app.sat_limit.set(60000)
        app.linear_limit.set("40000")
        app._set_camera_note("")
        app.findings.clear()
        self.lines = []
        self._log = mock.patch.object(app, "log", side_effect=lambda text, **kw: self.lines.append(text))
        self._log.start()
        self.addCleanup(self._log.stop)

    def _old_night(self, dark_header=None):
        """The ASI night: its boxes filled and its lights checked."""
        app = self.app
        app.bias_dir.set("/data/asi/master_bias.fits")
        app.flat_dir.set("/data/asi/flats")
        app.darkflat_dir.set("/data/asi/darkflats")
        app.dark_dir.set("/data/asi/darks")
        app.light_dir.set("/data/asi/hat-p-32")
        self.assertFalse(app._camera_check(ASI, "/data/asi/hat-p-32/l1.fits"))
        app._camera_folders_snapshot()      # Calibrate does this
        self.lines.clear()

    def test_first_lights_change_nothing(self):
        app = self.app
        app.bias_dir.set("/data/asi/bias")
        app.light_dir.set("/data/asi/hat-p-32")
        self.assertFalse(app._camera_check(ASI))
        self.assertEqual(self.lines, [])
        self.assertEqual(app.bias_dir.get(), "/data/asi/bias")
        self.assertEqual(app.linear_limit.get(), "40000")
        self.assertEqual(app.camera_note.get(), "")

    def test_same_camera_keeps_everything(self):
        app = self.app
        self._old_night()
        app.radius.set(6.5)
        app.light_dir.set("/data/asi/next-night")
        self.assertFalse(app._camera_check(dict(ASI, EXPTIME=90.0), "/data/asi/next-night/l1.fits"))
        self.assertEqual(self.lines, [])
        self.assertEqual(app.bias_dir.get(), "/data/asi/master_bias.fits")
        self.assertEqual(app.flat_dir.get(), "/data/asi/flats")
        self.assertEqual(app.darkflat_dir.get(), "/data/asi/darkflats")
        self.assertEqual(app.dark_dir.get(), "/data/asi/darks")
        self.assertEqual((app.radius.get(), app.sky_in.get(), app.sky_out.get()), (6.5, 14.0, 22.0))
        self.assertEqual(app.sat_limit.get(), 60000)
        self.assertEqual(app.linear_limit.get(), "40000")
        self.assertIsNone(app._camera_aperture_pending)
        self.assertEqual(app.camera_note.get(), "")

    def test_changed_camera_clears_and_resets_one_line_each(self):
        app = self.app
        app.radius.set(9.0)                 # the ASI night's own size (step 4 review M2: set before its snapshot)
        self._old_night()
        app.light_dir.set("/data/mobs/wasp-50")
        with mock.patch.object(app, "_first_header", return_value=dict(ASI)):      # the carried-over darks
            self.assertTrue(app._camera_check(MOBS, "/data/mobs/wasp-50/WASP-50b1.FITS"))
        for name in ("bias_dir", "flat_dir", "darkflat_dir", "dark_dir"):
            self.assertEqual(getattr(app, name).get(), "", name)
        self.assertEqual(app._counts["Bias"].get(), "0 files")
        self.assertEqual(len(self.lines), 3, self.lines)                           # one plain line each
        folders, aperture, limits = self.lines
        self.assertIn("another camera than the last ones (ZWO ASI2600MC Duo, 6248×4176 before, 650×500, binned 2x2, "
                      "12-bit now): Bias, Dark flats and Flat emptied, because they belong to the last camera.",
                      folders)
        self.assertIn("Dark emptied too: its frames (6248×4176, 120 s) are not these lights' (650×500, 60 s).",
                      folders)
        self.assertEqual((app.radius.get(), app.sky_in.get(), app.sky_out.get()), (8.0, 14.0, 22.0))
        self.assertIn("Aperture and sky ring back to the defaults (r 8, sky 14–22 px)", aperture)
        self.assertEqual(app._camera_aperture_pending, self.ca.APERTURE_DEFAULTS)
        self.assertEqual(app.linear_limit.get(), "")                               # 40000 means nothing at 4095
        self.assertIn("Linear ADU emptied", limits)
        self.assertIn("MicroObservatory setup", limits)
        self.assertEqual(app.sat_limit.get(), 60000)        # left to the MicroObservatory setup (it sets 4000)
        for line in self.lines:
            self.assertNotIn("⚠", line)                     # a reset for a camera change is a plain note
            self.assertNotIn("\n", line)
        self.assertEqual(app.camera_note.get(), "\n".join(self.lines))
        self.assertEqual(len(app.findings), 0)
        # the same lights again (Scan folders after Browse): nothing more is said or done
        self.lines.clear()
        self.assertFalse(app._camera_check(MOBS, "/data/mobs/wasp-50/WASP-50b1.FITS"))
        self.assertEqual(self.lines, [])
        self.assertNotEqual(app.camera_note.get(), "")

    def test_boxes_chosen_for_the_new_night_are_left_alone(self):
        """New reduction, then the new night's Dark is chosen before its Lights (the issue: darks only)."""
        app = self.app
        self._old_night()
        with mock.patch.object(self.ca.messagebox, "askyesno", return_value=True):
            self._log.stop()
            try:
                self.assertTrue(app.new_reduction())
            finally:
                self._log.start()
        app.dark_dir.set("/data/mobs/darks")
        app.light_dir.set("/data/mobs/wasp-50")
        self.lines.clear()
        self.assertTrue(app._camera_check(MOBS, "/data/mobs/wasp-50/WASP-50b1.FITS"))
        self.assertEqual(app.dark_dir.get(), "/data/mobs/darks")
        self.assertEqual(app.bias_dir.get(), "")
        self.assertEqual(app.flat_dir.get(), "")
        self.assertNotIn("Dark kept", self.lines[0])
        self.assertNotIn("Dark emptied", self.lines[0])

    def test_matching_darks_are_kept(self):
        app = self.app
        self._old_night()
        other = dict(ASI, INSTRUME="ZWO ASI2600MM Pro")       # another camera, same chip size and exposure
        app.light_dir.set("/data/mm/night")
        with mock.patch.object(app, "_first_header", return_value=dict(ASI)):
            self.assertTrue(app._camera_check(other, "/data/mm/night/l1.fits"))
        self.assertEqual(app.dark_dir.get(), "/data/asi/darks")
        self.assertEqual(app.bias_dir.get(), "")
        self.assertIn("Dark kept: its frames have these lights' size and exposure.", self.lines[0])
        # no profile, 16-bit file: SHOBS-P's own value, linear emptied, and it says so
        self.assertEqual(app.sat_limit.get(), 60000)
        self.assertEqual(app.linear_limit.get(), "")
        self.assertIn("Raw sat ADU 60,000 and linear ADU emptied", self.lines[2])
        self.assertIn("no SHOBS-FLUX profile for this camera", self.lines[2])

    def test_bit_depth_maximum_without_a_profile(self):
        app = self.app
        self._old_night()
        twelve = {"INSTRUME": "QHY5III462", "NAXIS1": 1920, "NAXIS2": 1080, "BITPIX": 16, "DATAMAX": 4095}
        app.light_dir.set("/data/qhy")
        self.assertTrue(app._camera_check(twelve, "/data/qhy/l1.fits"))
        self.assertEqual(app.sat_limit.get(), 4095)
        self.assertEqual(app.linear_limit.get(), "")
        self.assertIn("Raw sat ADU 4,095 and linear ADU emptied", self.lines[2])
        self.assertIn("the camera's 12-bit maximum", self.lines[2])

    def test_limits_from_the_flux_profile(self):
        import shobs_profile as sp
        app = self.app
        self._old_night()
        hdr = {"INSTRUME": "ZWO ASI2600MM Pro", "NAXIS1": 6248, "NAXIS2": 4176, "XBINNING": 1, "BITPIX": 16,
               "GAIN": 100, "OFFSET": 50}
        cam = sp.setup_key(hdr)["camera_id"]
        doc = {"format": sp.CAMERA_FORMAT, "format_version": 1, "profile_id": cam, "revision": 1,
               "created": "2026-11-01", "updated": "2026-11-01", "camera": {"instrume": hdr["INSTRUME"]},
               "mode": {}, "values": {"saturation_adu": sp.record(65532, "ADU", "measured"),
                                      "linear_limit_adu": sp.record(52000, "ADU", "measured")}}
        sp.write_json(sp.profile_path("cameras", cam), doc)
        self.addCleanup(os.remove, sp.profile_path("cameras", cam))
        app.light_dir.set("/data/mm/night")
        self.assertTrue(app._camera_check(hdr, "/data/mm/night/l1.fits"))
        self.assertEqual(app.sat_limit.get(), 65532)
        self.assertEqual(app.linear_limit.get(), "52000")
        self.assertIn("Raw sat ADU 65,532 and linear ADU 52,000 (from the SHOBS-FLUX profile for this camera).",
                      self.lines[2])

    def test_back_to_the_first_camera_brings_its_limits_back(self):
        app = self.app
        self._old_night()
        app.light_dir.set("/data/mobs/wasp-50")
        self.assertTrue(app._camera_check(MOBS, "/data/mobs/wasp-50/WASP-50b1.FITS"))
        self.assertEqual(app.linear_limit.get(), "")
        app.light_dir.set("/data/asi/next-night")
        self.lines.clear()
        self.assertTrue(app._camera_check(ASI, "/data/asi/next-night/l1.fits"))
        self.assertEqual(app.linear_limit.get(), "40000")
        self.assertEqual(app.sat_limit.get(), 60000)
        self.assertIn("as last used with this camera", self.lines[2])

    def test_loading_a_series_or_a_running_job_is_left_alone(self):
        app = self.app
        self._old_night()
        for flag in ("_loading_meta", "busy"):
            setattr(app, flag, True)
            try:
                self.assertFalse(app._camera_check(MOBS))
            finally:
                setattr(app, flag, False)
        self.assertEqual(app.bias_dir.get(), "/data/asi/master_bias.fits")
        self.assertEqual(self.lines, [])

    def test_aperture_set_from_the_new_frames(self):
        app, core = self.app, self.ca.core
        self._old_night()
        app.light_dir.set("/data/mobs/wasp-50")
        app._camera_check(MOBS, "/data/mobs/wasp-50/WASP-50b1.FITS")
        self.lines.clear()
        self.assertTrue(app._camera_aperture_apply(None, 2.3))
        want = core.suggest_apertures(2.3)
        self.assertEqual((app.radius.get(), app.sky_in.get(), app.sky_out.get()),
                         (want["radius"], want["sky_in"], want["sky_out"]))
        self.assertEqual(len(self.lines), 1)
        self.assertIn(f"Aperture set for the new camera's frames: r {want['radius']:g}", self.lines[0])
        self.assertNotIn("⚠", self.lines[0])
        self.assertIsNone(app._camera_aperture_pending)
        self.assertFalse(app._camera_aperture_apply(None, 2.3))       # once

    def test_aperture_typed_since_the_reset_is_the_users(self):
        app = self.app
        self._old_night()
        app.light_dir.set("/data/mobs/wasp-50")
        app._camera_check(MOBS, "/data/mobs/wasp-50/WASP-50b1.FITS")
        app.radius.set(5.0)
        self.assertFalse(app._camera_aperture_apply(None, 2.3))
        self.assertEqual(app.radius.get(), 5.0)

    def test_aperture_not_measurable_says_so(self):
        app = self.app
        self._old_night()
        app.light_dir.set("/data/mobs/wasp-50")
        app._camera_check(MOBS, "/data/mobs/wasp-50/WASP-50b1.FITS")
        self.lines.clear()
        self.assertFalse(app._camera_aperture_apply(None, float("nan")))
        self.assertEqual(app.radius.get(), 8.0)
        self.assertIn("Star sizes could not be measured on this image", self.lines[0])

    def test_camera_from_the_last_session(self):
        """After a restart the linear ADU box still holds the last camera's remembered value."""
        app = self.app
        app.settings["camera_last"] = self.ca.camera_signature(ASI)
        app.radius.set(5.0)                                   # typed since the start: the user's own
        app.bias_dir.set("/data/mobs/bias")                   # chosen this session: never emptied
        app.light_dir.set("/data/mobs/wasp-50")
        self.assertTrue(app._camera_check(MOBS, "/data/mobs/wasp-50/WASP-50b1.FITS"))
        self.assertEqual(app.bias_dir.get(), "/data/mobs/bias")
        self.assertEqual(app.radius.get(), 5.0)
        self.assertIn("Aperture and sky ring left as they are", self.lines[1])
        self.assertEqual(app.linear_limit.get(), "")

    def test_settings_remember_the_camera(self):
        app = self.app
        self._old_night()
        with open(self.ca.SETTINGS_FILE, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["camera_last"]["nx"], 6248)
        self.assertEqual(list(saved["camera_limits"].values())[-1], {"sat": 60000.0, "linear": 40000.0})

    # ---- the aperture caution in the Findings panel ----
    def _image(self, fwhm):
        app = self.app
        img = np.zeros((40, 40))
        app.preview = img
        app.preview_is_debayered = True
        app._preview_token = getattr(app, "_preview_token", 0) + 1
        app._seeing_cache = ((id(img), img.shape), fwhm)
        app._aperture_caution_said = None
        self.addCleanup(lambda: (setattr(app, "preview", None), setattr(app, "preview_is_debayered", False)))

    def _pump(self, seconds=1.2):
        end = time.time() + seconds
        while time.time() < end:
            self.app.update()
            time.sleep(0.02)

    def test_fixing_the_boxes_clears_the_kept_from_before_finding(self):
        app = self.app
        self._log.stop()
        self.addCleanup(self._log.start)
        self._image(2.3)
        with mock.patch.object(app, "_donut_apertures", return_value=None):
            app._aperture_caution(app._preview_token, measured=True)
            texts = [f.text for f in app.findings.items]
            self.assertEqual(len(texts), 1, texts)
            self.assertIn("⚠ Aperture r = 8 px (kept from before) is 3.5 × the stars' FWHM (2.3 px)", texts[0])
            self.assertTrue(app.findings.items[0].hint, "chapter 23 has the What to do")
            # still too large after a change: the finding is replaced, in the new numbers, no longer "kept from before"
            app.radius.set(7.0)
            self._pump()
            texts = [f.text for f in app.findings.items]
            self.assertEqual(len(texts), 1, texts)
            self.assertIn("⚠ Aperture r = 7 px is 3.0 × the stars' FWHM", texts[0])
            self.assertNotIn("kept from before", texts[0])
            # fixed: gone, with no new solve
            app.radius.set(4.1)
            self._pump()
            self.assertEqual([f.text for f in app.findings.items], [])

    def test_new_camera_image_sets_the_boxes_instead_of_warning(self):
        app = self.app
        self._log.stop()
        self.addCleanup(self._log.start)
        self._image(2.3)
        app._camera_aperture_pending = self.ca.APERTURE_DEFAULTS
        with mock.patch.object(app, "_donut_apertures", return_value=None):
            app._aperture_caution(app._preview_token, measured=True)
            self._pump(0.9)
        want = self.ca.core.suggest_apertures(2.3)
        self.assertEqual(app.radius.get(), want["radius"])
        self.assertEqual([f.text for f in app.findings.items], [])

    # ---- where it is hooked in, and the words ----
    def test_hooks_and_words(self):
        src = _read("clearv_app.py")
        scan = src.split("    def scan_folders(self):", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("self._camera_check(header, lights[0])", scan)
        picked = src.split("    def _frames_folder_picked(self, key: str):", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("self._camera_check(header", picked)
        self.assertLess(picked.index("self._camera_check(header"), picked.index("self._update_dark_exposure_note()"))
        for name in ("    def new_reduction(self", "    def run_calibration(self"):
            self.assertIn("self._camera_folders_snapshot()", src.split(name, 1)[1].split("\n    def ", 1)[0], name)
        self.assertIn("another camera", self.ca.App.NEW_REDUCTION_TIP)
        for ch, words in (("05-input.md", "another camera"), ("07-calibrate.md", "another camera"),
                          ("23-messages-explained.md", "These lights come from another camera than the last ones"),
                          ("23-messages-explained.md", "(kept from before)")):
            self.assertIn(words, _read("docs", "manual", ch), ch)
        self.assertIn("## #110, #111 (s4-quick)", _read("docs", "manual", "_reshoots_240_e.md"))


if __name__ == "__main__":
    unittest.main()
