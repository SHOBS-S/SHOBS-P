"""2.3.1 UI nits (numbers frozen at 2.3.0): the Telescope aperture follows the telescope, the Photometry title on mono
frames, a settings file for Variables reports, the short-night period search, the catalog-outage pop-up, and the
check star near the edge (with spares near the edge only logged).

Synthetic MONO frames with no plate solution and no RA/Dec in the headers (only EXPTIME and DATE-OBS)."""
import math
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ["SHOBS_ONSKY_OFF"] = "1"

import numpy as np  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")

import photometry_core as core  # noqa: E402
import clearv_app as ca  # noqa: E402
import test_230_d as D  # noqa: E402


class TestScopeHelpers(unittest.TestCase):
    def test_lights_scope(self):
        self.assertIsNone(ca.lights_scope({}))
        sc = ca.lights_scope({"TELESCOP": "NexStar 8SE", "INSTRUME": "ZWO ASI2600MM Pro", "FOCALLEN": 2098})
        self.assertEqual(sc, {"telescope": "NexStar 8SE", "camera": "ZWO ASI2600MM Pro", "focal_mm": 2098.0})
        self.assertEqual(ca.lights_scope({"INSTRUME": "cam"}), {"telescope": "", "camera": "cam", "focal_mm": None})

    def test_same_scope_tolerates_plate_solved_focal(self):
        a = {"telescope": "EQ8-R", "camera": "ZWO ASI2600MC Duo", "focal_mm": 2898.0}
        b = {"telescope": "eq8-r ", "camera": "ZWO  ASI2600MC Duo", "focal_mm": 2906.0}
        self.assertTrue(ca.same_scope(a, b))
        self.assertFalse(ca.same_scope(a, dict(b, focal_mm=2098.0)))
        self.assertFalse(ca.same_scope(a, dict(b, camera="ZWO ASI2600MM Pro")))
        self.assertFalse(ca.same_scope(a, dict(b, focal_mm=None)))
        self.assertTrue(ca.same_scope(dict(a, focal_mm=None), dict(b, focal_mm=None)))
        self.assertFalse(ca.same_scope(None, b))
        self.assertIn("focal 2898 mm", ca.scope_words(a))


class TestCatalogOutage(unittest.TestCase):
    def test_failed_catalogs_named(self):
        text = ca.catalog_outage_text({"Gaia DR3": "HTTP 503"}, {"APASS DR9": 0, "Tycho-2": 3})
        self.assertIn("Gaia DR3 (HTTP 503)", text)
        self.assertIn("APASS DR9 (no stars came back)", text)
        self.assertIn("not your RA/Dec or Optics values", text)
        self.assertNotIn("Tycho-2", text)

    def test_empty_gaia_alone_counts(self):
        self.assertIn("Gaia DR3 (no stars came back)", ca.catalog_outage_text({}, {"Gaia DR3": 0, "APASS DR9": 40}))

    def test_enough_stars_is_not_an_outage(self):
        """review S5: Gaia down but 60 APASS/Tycho stars to match: the lock failure is RA/Dec or optics."""
        err, cnt = {"Gaia DR3": "HTTP 503"}, {"APASS DR9": 60, "Tycho-2": 9}
        self.assertEqual(ca.catalog_outage_text(err, cnt, 60), "")
        self.assertIn("Gaia DR3", ca.catalog_outage_text(err, cnt, 8))
        self.assertIn("did not answer", ca.catalog_outage_text({c: "x" for c in ca.CATALOG_OUTAGE_MAIN}, {}, 60))

    def test_no_outage(self):
        self.assertEqual(ca.catalog_outage_text({}, {"Gaia DR3": 900, "APASS DR9": 40, "Tycho-2": 0}), "")
        self.assertEqual(ca.catalog_outage_text({"AAVSO sequence": "HTTP 503"}, {"Gaia DR3": 900}), "")
        self.assertEqual(ca.catalog_outage_text(None, None), "")


class TestPeriodHelpers(unittest.TestCase):
    def test_floor_from_grid_then_box(self):
        self.assertAlmostEqual(ca.period_search_floor({"freqs": np.array([0.05, 1.0, 50.0])}), 0.02)
        self.assertAlmostEqual(ca.period_search_floor({}, 0.05), 0.05)
        self.assertIsNone(ca.period_search_floor(None, ""))

    def test_text(self):
        t = ca.too_short_period_text(0.8 / 24, 0.02)
        self.assertTrue(t.startswith("Too short for a period search: one night of 0.8 h"), t)
        self.assertIn("0.02 d = 0.5 h", t)
        self.assertNotIn("⚠", t)


def _o(k, kmag=10.0, lost="", spares=None, s_pos=None, k_pos=(100.0, 100.0)):
    o = core.Obs(jd=2460950.6 + k * 0.002, mag=10.0, merr=0.01, cmag=9.0, kmag=kmag, airmass=1.1, path=f"f{k:03d}.fit",
                 exptime=10.0, flux_var=1.0, flux_comp=1.0, night="N")
    o.check_lost = lost
    o.spare_insts = dict(spares or {})
    o.pos = {"c": [], "k": list(k_pos) if k_pos else None, "s": dict(s_pos or {})}
    return o


class TestCheckEdgeCore(unittest.TestCase):
    def test_missing_frames_edge_only(self):
        obs = [_o(0), _o(1, None, "aperture or sky ring falls outside the frame"), _o(2, None, "no flux above sky in "
                                                                                            "this frame"), _o(3)]
        self.assertEqual(core.check_missing_frames(obs), [1])
        self.assertEqual(core.check_missing_frames([]), [])

    def test_alternative_spare(self):
        obs = []
        for k in range(20):
            obs.append(_o(k, kmag=-8.0, spares={"FAR": -7.6, "EDGE": -8.0, "BRIGHT": -10.5},
                          s_pos={"FAR": [100.0, 80.0], "EDGE": [5.0, 80.0], "BRIGHT": [100.0, 100.0]}))
        alt = core.check_edge_alternative(obs, (160, 200), 39.0, "K")
        self.assertEqual(alt["name"], "FAR")
        self.assertAlmostEqual(alt["dmag"], 0.4, places=6)
        self.assertGreaterEqual(alt["closest_px"], 39.0)
        # a spare missing from more than 5 % of the frames is not offered
        for o in obs[:3]:
            o.spare_insts.pop("FAR")
        self.assertIsNone(core.check_edge_alternative(obs, (160, 200), 39.0, "K"))
        self.assertIsNone(core.check_edge_alternative(obs, (160, 200), 39.0, ""))


def _have_tk():
    try:
        import tkinter as tk
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


@unittest.skipUnless(_have_tk(), "needs a display")
class TestInApp(unittest.TestCase):
    _setup = D.TestRunInApp._setup
    _run = D.TestRunInApp._run

    @classmethod
    def setUpClass(cls):
        cls.ca = ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test231.log")
        cls.app = ca.App()
        cls.app.withdraw()
        cls.patches = [mock.patch.object(ca.messagebox, n, return_value=True)
                       for n in ("showwarning", "showinfo", "showerror", "askyesno")]
        cls.boxes = {n: p.start() for n, p in zip(("showwarning", "showinfo", "showerror", "askyesno"), cls.patches)}
        cls._settings = dict(cls.app.settings)
        cls.app._save_settings = lambda *a, **k: None   # never write the tester's real settings file

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.app.destroy()

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for box in self.boxes.values():
            box.reset_mock()
            box.return_value = True
        self.logged = []
        self._log = mock.patch.object(self.app, "log", side_effect=lambda t, *a, **k: self.logged.append(str(t)))
        self._log.start()

    def tearDown(self):
        self._log.stop()
        D.TestRunInApp.tearDown(self)
        self.app.settings.pop("aperture_scopes", None)
        self.app.settings.pop("aperture_scope_last", None)

    # ---- item 1: the Telescope aperture follows the telescope ---------------------------------------------------
    def test_aperture_offered_for_a_known_telescope(self):
        app = self.app
        app._own_setup = None
        app.aperture_mm.set("152")
        app.settings["aperture_scopes"] = [{"telescope": "NexStar 8SE", "camera": "ASI2600MM", "focal_mm": 2098.0,
                                            "aperture_mm": 203.0}]
        app._check_aperture_scope({"TELESCOP": "NexStar 8SE", "INSTRUME": "ASI2600MM", "FOCALLEN": 2100})
        self.boxes["askyesno"].assert_called_once()
        self.assertIn("203 mm", self.boxes["askyesno"].call_args[0][1])
        self.assertEqual(app.aperture_mm.get(), "203")
        # same value already in the box: no question
        self.boxes["askyesno"].reset_mock()
        app._check_aperture_scope({"TELESCOP": "NexStar 8SE", "INSTRUME": "ASI2600MM", "FOCALLEN": 2100})
        self.boxes["askyesno"].assert_not_called()
        # the header's own aperture wins: no question either
        app.aperture_mm.set("152")
        app._check_aperture_scope({"TELESCOP": "NexStar 8SE", "INSTRUME": "ASI2600MM", "FOCALLEN": 2100,
                                   "APTDIA": 203})
        self.boxes["askyesno"].assert_not_called()

    def test_aperture_question_for_another_telescope(self):
        app = self.app
        app.aperture_mm.set("152")
        app._lights_scope = {"telescope": "", "camera": "MicroObservatory", "focal_mm": 650.0}
        app._remember_aperture_scope()
        self.assertEqual(app.settings["aperture_scope_last"]["aperture_mm"], 152.0)
        self.boxes["askyesno"].return_value = False
        with mock.patch.object(app, "after_idle"):
            app._check_aperture_scope({"INSTRUME": "ASI2600MM", "FOCALLEN": 2098})
        msg = self.boxes["askyesno"].call_args[0][1]
        self.assertIn("another telescope", msg)
        self.assertIn("152 mm", msg)
        self.assertEqual(app.aperture_mm.get(), "152")   # asks only; never changed behind the user's back
        self.assertIn("Optics box", app.status.get())
        # a value typed since that run (the box no longer holds the old one): no question
        self.boxes["askyesno"].reset_mock()
        app.aperture_mm.set("203")
        app._check_aperture_scope({"INSTRUME": "ASI2600MM", "FOCALLEN": 2098})
        self.boxes["askyesno"].assert_not_called()

    def test_answers_not_asked_twice(self):
        """review S2: Yes is remembered at once; No is not asked again this session for the same value."""
        app = self.app
        app.__dict__.pop("_aperture_asked", None)
        app.aperture_mm.set("152")
        app.settings["aperture_scope_last"] = {"telescope": "", "camera": "MOBS", "focal_mm": 650.0, "aperture_mm": 152.0}
        head = {"INSTRUME": "ASI2600MM", "FOCALLEN": 2098}
        app._check_aperture_scope(head)   # Yes: 152 is right for it
        app._check_aperture_scope(head)
        self.assertEqual(self.boxes["askyesno"].call_count, 1)
        self.assertTrue(any(ca.same_scope(r, ca.lights_scope(head)) for r in app.settings["aperture_scopes"]))
        app.settings["aperture_scopes"] = [{"telescope": "", "camera": "X", "focal_mm": 1000.0, "aperture_mm": 254.0}]
        self.boxes["askyesno"].reset_mock()
        self.boxes["askyesno"].return_value = False
        for _ in range(2):
            app._check_aperture_scope({"INSTRUME": "X", "FOCALLEN": 1000})
        self.assertEqual(self.boxes["askyesno"].call_count, 1)

    def test_no_to_one_question_does_not_silence_the_other(self):
        """review r2: No to 'is 203 right for X?' must not hide the later offer 'use 114 mm, as last used with X?'."""
        app = self.app
        app.__dict__.pop("_aperture_asked", None)
        app.aperture_mm.set("203")
        app.settings["aperture_scopes"] = []
        app.settings["aperture_scope_last"] = {"telescope": "", "camera": "Mine", "focal_mm": 2900.0, "aperture_mm": 203.0}
        head = {"INSTRUME": "eVscope", "FOCALLEN": 450}
        self.boxes["askyesno"].return_value = False
        with mock.patch.object(app, "after_idle"):
            app._check_aperture_scope(head)   # "another telescope … is 203 right?" → No
        app.settings["aperture_scopes"] = [dict(ca.lights_scope(head), aperture_mm=114.0)]
        app._check_aperture_scope(head)   # now known with 114: offered
        self.assertEqual(self.boxes["askyesno"].call_count, 2)
        self.assertIn("114 mm", self.boxes["askyesno"].call_args[0][1])

    def test_remember_uses_the_measured_frames(self):
        """review S4: the telescope recorded is the one of the Lights folder's frames, not the last Scan folders."""
        app = self.app
        paths = D.write_night(os.path.join(self.dir, "lights"), [("T", 100, 110, 50000)], n=2, drift=(0.0, 0.0))
        from astropy.io import fits
        for pth in paths:
            with fits.open(pth, mode="update") as hd:
                hd[0].header["INSTRUME"] = "CamB"
                hd[0].header["FOCALLEN"] = 800.0
        app.light_dir.set(os.path.dirname(paths[0]))
        app._split_cache = None
        app._lights_scope = {"telescope": "", "camera": "CamA", "focal_mm": 2000.0}
        app.aperture_mm.set("100")
        app._remember_aperture_scope()
        self.assertEqual(app.settings["aperture_scope_last"]["camera"], "CamB")

    def test_remember_keeps_one_record_per_telescope(self):
        app = self.app
        app.settings["aperture_scopes"] = []
        for focal, ap in ((2898.0, "356"), (2906.0, "356"), (2098.0, "203")):
            app._lights_scope = {"telescope": "", "camera": "cam", "focal_mm": focal}
            app.aperture_mm.set(ap)
            app._remember_aperture_scope()
        recs = app.settings["aperture_scopes"]
        self.assertEqual(len(recs), 2)
        self.assertEqual(sorted(r["aperture_mm"] for r in recs), [203.0, 356.0])

    # ---- item 2: the Photometry image's title on a mono camera ----------------------------------------------------
    def test_mono_title(self):
        stars = [("T", 100, 110, 50000), ("C1", 50, 100, 60000)]
        paths = D.write_night(os.path.join(self.dir, "lights"), stars, n=3, drift=(0.0, 0.0))
        app = self.app
        app.light_dir.set(os.path.dirname(paths[0]))
        app._split_cache = None
        app.pattern.set(core.MONO)
        app.masters, app.masters_binning, app.precalibrated = core.Masters(), None, False
        app.calibrated = core.calibrate_frame(*core.read_fits(paths[0]), app.masters)
        app.preview, app.preview_path = app.calibrated, paths[0]
        app._rgb_channel = None
        app.apply_debayer()
        for _ in range(5):
            app.update()
        app.refresh_preview()
        self.assertTrue(app.photo_ax.get_title().startswith("Monochrome — click to"), app.photo_ax.get_title())
        self.assertTrue(any(t.startswith("Preview: monochrome") for t in self.logged), self.logged[-5:])

    # ---- items 3 and 6: a night with the check drifting off the top edge ----------------------------------------
    def test_check_off_edge_named_spare_quiet_and_settings_file(self):
        # K starts 26 px under the top edge and drifts off (1 px/frame); EDGE (a spare) does too; FAR (a spare as
        # bright as K) stays well inside all night
        stars = [("T", 100, 110, 50000), ("C1", 50, 100, 60000), ("C2", 150, 130, 40000), ("K", 140, 26, 45000),
                 ("EDGE", 110, 22, 50000), ("FAR", 100, 80, 45000)]
        paths = D.write_night(os.path.join(self.dir, "lights"), stars)
        self._setup(paths, ("T", 100, 110), [("C1", 50, 100, 10.0), ("C2", 150, 130, 10.4)],
                    check=("K", 140, 26, 10.2), spares=[("EDGE", 110, 22, 10.1), ("FAR", 100, 80, 10.2)])
        app = self.app
        # margin 3 × sky-out = 39 px: FAR (from 80 up to 41 px under the top edge) stays in, K and EDGE do not
        app.aperture_mm.set("203")
        app.use_scint.set(True)
        self._run()
        night = app.observations[0].night
        warns = [c[0][1] for c in self.boxes["showwarning"].call_args_list if "frame edge" in c[0][1]]
        self.assertEqual(len(warns), 1, [c[0][1] for c in self.boxes["showwarning"].call_args_list])
        text = warns[0]
        self.assertIn("check K", text)
        self.assertIn("check K comes within", text)   # review S1: the edge distance is kept
        self.assertIn("The check star was off the frame in 20 frames", text)
        self.assertNotIn(" .", text)
        self.assertIn("A better check star stays farther in: FAR", text)
        self.assertNotIn("spare EDGE", text)   # spares near the edge: log only
        self.assertTrue(any("spare EDGE" in t and "nothing to do" in t for t in self.logged), self.logged)
        # the night's setup records the Telescope aperture; the Variables settings file says it
        self.assertEqual(app._night_info(night).get("aperture_mm"), 203.0)
        rpt = os.path.join(self.dir, "AAVSO_T_CV_02-OCT-2026_SHOBS-P.txt")
        open(rpt, "w").close()
        path = app._write_variables_settings(rpt, app._usable())
        self.assertTrue(path.endswith("_settings.txt"))
        with open(path, encoding="ascii") as fh:
            body = fh.read()
        self.assertTrue(body.startswith(f"SHOBS-P {ca.APP_VERSION} settings for "), body[:80])
        for want in ("Star: T", "Mode: variables", f"Aperture: r {D.RADIUS:g} px, sky {D.SKY_IN:g}-",
                     "Comps measured: C1, C2", "Check measured: K", "Telescope aperture box when the night was measured: 203 mm",
                     "Frames skipped at photometry (reason: frame numbers): none", "Scintillation (this report's error bars): ",
                     "Frames left out (flag: frame numbers):", f"Reduced with: SHOBS-P {ca.APP_VERSION}"):
            self.assertIn(want, body)
        # a second save never overwrites the first
        self.assertNotEqual(app._write_variables_settings(rpt, app._usable()), path)

    # ---- item 5: the no-lock pop-up names a catalog outage ------------------------------------------------------
    def test_no_lock_popup_names_outage(self):
        app = self.app
        app.preview = np.zeros((160, 200), dtype=np.float32)
        app.preview_is_debayered = True
        for errors, counts, want, not_want in (
                ({"Gaia DR3": "HTTP 503"}, {"APASS DR9": 0, "Tycho-2": 2}, "Gaia DR3 (HTTP 503)", "Check that the Target"),
                ({}, {"Gaia DR3": 800, "APASS DR9": 60, "Tycho-2": 9}, "Check that the Target", "outage")):
            self.boxes["showinfo"].reset_mock()
            app._chart_diag = {"auto": False, "catalog_errors": errors, "catalogs": counts, "sources": 80,
                               "scale": 1.25, "camera_bin": 1, "target": (100.0, 110.0)}
            stars = [{"bright": 10.0 + k} for k in range(3)]
            app._label_done("Gaia DR3", "", stars, stars, {"hits": 0}, [], "V", None)
            msg = self.boxes["showinfo"].call_args[0][1]
            self.assertIn(want, msg)
            self.assertNotIn(not_want, msg)

    # ---- item 4: one night too short for any searched period ---------------------------------------------------
    def test_short_night_period_box(self):
        app = self.app
        app.mode_key = "variables"
        rng = np.random.default_rng(3)
        app.observations = [core.Obs(jd=2460950.6 + k * 0.8 / 24 / 60, mag=10.0 + rng.normal(0, 0.01), merr=0.01,
                                     cmag=9.0, kmag=10.5, airmass=1.1, path=f"f{k:03d}.fit", exptime=30.0,
                                     flux_var=1.0, flux_comp=1.0, night="N1", filt="CV") for k in range(60)]
        app.min_period.set(0.02)
        app.max_period.set(20.0)
        app._refresh_period()
        self.assertIsNotNone(app.period)
        got = {}

        def grab(series_lines, period_lines, color_lines, rows, cautions):
            got["period"], got["cautions"] = list(period_lines), list(cautions)
        with mock.patch.object(app, "_fill_summary", side_effect=grab):
            app.draw_output()
        self.assertEqual(len(got["period"]), 1, got["period"])
        self.assertTrue(got["period"][0].startswith("Too short for a period search"), got["period"])
        self.assertFalse(any("⚠" in c or "day alias" in c for c in got["cautions"]), got["cautions"])
        self.assertEqual(app.out_ax2.get_title(), "Too short for a period search")
        # a night long enough for the shortest period, but not for its best peak: still the old wording, no ⚠
        app.observations = [core.Obs(jd=2460950.6 + k * 0.004, mag=10.0 + 0.1 * math.sin(k * 0.3), merr=0.01,
                                     cmag=9.0, kmag=10.5, airmass=1.1, path=f"f{k:03d}.fit", exptime=30.0,
                                     flux_var=1.0, flux_comp=1.0, night="N1", filt="CV") for k in range(60)]
        app._refresh_period()
        with mock.patch.object(app, "_fill_summary", side_effect=grab):
            app.draw_output()
        self.assertFalse(got["period"][0].startswith("Too short for a period search"), got["period"])


if __name__ == "__main__":
    unittest.main()
