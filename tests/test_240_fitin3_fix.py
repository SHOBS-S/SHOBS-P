"""2.4.0 fit-in F3: the fixes after the independent review (review_f3.md). Each test first walks the reviewer's own
scenario.
M1 a placeholder / impossible header site is not used; M2 a site typed over the header's survives Scan folders;
S1 another telescope at the same far site gets its own optics; S2 Load series over a frames / MicroObservatory strip;
S3 Bayer pattern back after Use my own setup on MicroObservatory frames; S4 header-held values do not carry to the
next telescope; and the nits (rule 6 files only what was typed, the aperture's source in the strip, a left-out point
that is now trimmed, the ExoFOP package date)."""
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import photometry_core as core  # noqa: E402


def _src(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


class TestCore(unittest.TestCase):
    def test_m1_header_site_must_be_a_real_site(self):
        self.assertIsNone(core.header_site({"SITELAT": 0.0, "SITELONG": 0.0}))
        self.assertEqual(core.header_site_raw({"SITELAT": 0.0, "SITELONG": 0.0})[:2], (0.0, 0.0))
        self.assertIsNone(core.header_site({"SITELAT": 9999, "SITELONG": -81.9}))
        self.assertIsNone(core.header_site({"SITELAT": 35.3, "SITELONG": 400.0}))
        self.assertIsNone(core.header_site({"SITELAT": float("nan"), "SITELONG": -81.9}))
        self.assertIsNone(core.header_site({"SITELAT": 35.3, "SITELONG": float("inf")}))
        self.assertIsNone(core.header_site({"SITELAT": "0 0 0", "SITELONG": "0:00:00"}))
        # real sites still read, the equator and the Greenwich meridian among them
        self.assertEqual(core.header_site({"SITELAT": 0.0, "SITELONG": 36.8})[:2], (0.0, 36.8))
        self.assertEqual(core.header_site({"SITELAT": 51.48, "SITELONG": 0.0})[:2], (51.48, 0.0))
        self.assertEqual(core.header_site({"SITELAT": 31.68, "SITELONG": 249.12})[:2], (31.68, 249.12))
        self.assertEqual(core.header_site({"SITELAT": -90, "SITELONG": 0})[:2], (-90.0, 0.0))
        self.assertEqual(core.header_site({"SITELAT": 31.68, "SITELONG": -110.88, "SITEELEV": float("nan")}),
                         (31.68, -110.88, None))
        self.assertIsNone(core.header_site({"SITELAT": 31.68}))

    def test_nit_sources(self):
        tui = _src("transit_ui.py")
        self.assertIn('t_first = min(t_first, float(np.min(lo["t"])))', tui)      # the package date: left-out too
        self.assertIn("A NaN k also lands here", _src("transit_core.py"))

    def test_nit_left_out_point_now_trimmed_is_dropped(self):
        import transit_outlier_ui as tou
        lines, shown = [], []

        class Page(tou.OutlierMixin):
            report = None
            _data_extra = None
            _time_map = None

            def __init__(self):
                self.app = types.SimpleNamespace(log=lambda text, *a, **k: lines.append(str(text)), _series_gen=0)
                self._left_out_gen = 0
                self._left_out = []

            def _show_outlier_buttons(self):
                shown.append(len(self._left_out))

            def _point_words(self, label, bjd):
                return label

        page = Page()
        t = 2460000.0 + np.arange(40) * 0.002
        f, e, am = np.ones(40), np.full(40, 0.002), np.ones(40)
        page._left_out = [{"t": float(t[5]), "label": "frame 6", "pct": 3.0, "nsigma": 7.0}]
        # the point is among the fit's points: left out as before
        t2, _f, _e, _a = page._apply_left_out(t, f, e, am, {})
        self.assertEqual(len(t2), 39)
        self.assertEqual(len(page._left_out), 1)
        # the reviewer's case: a trim now covers it (the fit gets t[10:]): no record, so no Put it back
        meta = {}
        t3, _f, _e, _a = page._apply_left_out(t[10:], f[10:], e[10:], am[10:], meta)
        self.assertEqual(len(t3), 30)
        self.assertEqual(page._left_out, [])
        self.assertNotIn("left_out", meta)
        self.assertEqual(shown[-1], 0)
        self.assertTrue(any("frame 6 is no longer among the points being fit" in x for x in lines), lines)


try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestFix(unittest.TestCase):
    HOME = {"site_lat": "35.3030", "site_lon": "-81.9400", "site_elevation": "280", "site_tz": "America/New_York"}

    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test_240_fitin3_fix.log")
        with mock.patch.object(ca.App, "_save_settings", lambda *a, **k: None):
            cls.app = ca.App()
        cls.app.withdraw()
        cls.saves = []
        cls.app._save_settings = lambda *a, **k: cls.saves.append(dict(cls.app.settings))
        names = ("showwarning", "showinfo", "showerror", "askyesno")
        cls.patches = [mock.patch.object(ca.messagebox, n, return_value=True) for n in names]
        cls.boxes = {n: p.start() for n, p in zip(names, cls.patches)}
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.app.destroy()

    def setUp(self):
        app = self.app
        self.dir = tempfile.mkdtemp()
        for box in self.boxes.values():
            box.reset_mock()
            box.return_value = True
        app._own_setup = None
        app._typed_borrow = None
        app._frames_setup = None
        app._header_held = {}
        app._frames_choice_s = {}
        app._frames_mount = None
        app._header_held_from = None
        app._pattern_before_mobs = None
        app._aperture_source = ""
        app._aperture_hand = None
        app.settings.pop("aperture_scopes", None)
        app.settings.pop("aperture_scope_last", None)
        app._sensor_conflict = None
        app._sensor_conflict_answered = None
        app.settings.pop("frames_setups", None)
        app.settings.pop("mount", None)
        app._loading_meta = True   # the boxes set to the home setup without saving or asking
        for k, v in self.HOME.items():
            app.settings[k] = v
        app.settings["focal_mm"], app.settings["pixel_um"] = 2900.0, 3.76
        app.lat.set(self.HOME["site_lat"]); app.lon.set(self.HOME["site_lon"])
        app.elevation.set(self.HOME["site_elevation"]); app.site_tz.set(self.HOME["site_tz"])
        app.focal_mm.set(2900.0); app.pixel_um.set(3.76); app.aperture_mm.set("356")
        app.telescope_name.set("iOptron 14 RC")
        for var in (app.bias_dir, app.dark_dir, app.darkflat_dir, app.flat_dir, app.light_dir, app.output_dir):
            var.set("")
        app._loading_meta = False
        app._typed_site_pending = False
        app._confirm_setup_now()
        app.busy = False
        self.pump(50)
        self.saves.clear()
        self.logged = []
        self._log = mock.patch.object(app, "log", side_effect=lambda t, *a, **k: self.logged.append(str(t)))
        self._log.start()

    def tearDown(self):
        self._log.stop()
        app = self.app
        app._loading_meta = True
        for var in (app.bias_dir, app.dark_dir, app.darkflat_dir, app.flat_dir, app.light_dir, app.output_dir):
            var.set("")
        app._loading_meta = False
        app.busy = False
        shutil.rmtree(self.dir, ignore_errors=True)

    def pump(self, ms=0):
        end = time.time() + ms / 1000.0
        self.app.update()
        while time.time() < end:
            self.app.update()
            time.sleep(0.01)

    def _lights_box(self, name="far"):
        """The Lights box set to a folder (no folder-picked checks)."""
        folder = os.path.join(self.dir, name)
        os.makedirs(folder, exist_ok=True)
        self.app._loading_meta = True
        self.app.light_dir.set(folder)
        self.app._loading_meta = False
        return folder

    FAR = {"EXPTIME": 60, "SITELAT": 31.6800, "SITELONG": -110.8800, "TELESCOP": "Moeller 1 m",
           "INSTRUME": "QHY600", "FOCALLEN": 6800, "XPIXSZ": 3.76}

    FAR = {"EXPTIME": 60, "SITELAT": 31.6800, "SITELONG": -110.8800, "TELESCOP": "Moeller 1 m",
           "INSTRUME": "QHY600", "FOCALLEN": 6800, "XPIXSZ": 3.76}

    # ---- M1 ---------------------------------------------------------------------------------------------------------
    def test_m1_placeholder_site_is_not_used(self):
        app = self.app
        self._lights_box("zero")
        for head, said in (({"SITELAT": 0.0, "SITELONG": 0.0, "TELESCOP": "My RC"}, "latitude 0, longitude 0"),
                           ({"SITELAT": 9999, "SITELONG": -81.94, "TELESCOP": "My RC"}, "latitude 9999"),
                           ({"SITELAT": float("nan"), "SITELONG": -81.94, "TELESCOP": "My RC"}, "latitude nan")):
            self.logged.clear()
            app._header_setup(dict(head))
            self.assertEqual((app.lat.get(), app.lon.get(), app.elevation.get()),
                             (self.HOME["site_lat"], self.HOME["site_lon"], "280"), head)
            self.assertIsNone(app._own_setup)
            self.assertEqual(app._frames_setup["kind"], "own")           # the own-frames path
            self.assertTrue(any(said in t and "not a real site, so it is not used" in t for t in self.logged),
                            self.logged)
            self.assertFalse(any(t.lstrip().startswith(("⚠", "✖")) for t in self.logged))
            text, buttons = app._setup_strip_parts()
            self.assertTrue(text.startswith("Your own setup: site 35.3030, -81.9400, 280 m"), text)
            self.assertNotIn("own", buttons)
        self.boxes["askyesno"].assert_not_called()
        # on a friend's frames too: 0 / 0 after a real far site brings your own setup back, not 0 / 0
        self._lights_box("far")
        app._header_setup(dict(self.FAR))
        self.assertEqual(app.lat.get(), "31.6800")
        self._lights_box("zero")
        app._header_setup({"SITELAT": 0.0, "SITELONG": 0.0, "TELESCOP": "Friend"})
        self.assertEqual((app.lat.get(), app.lon.get()), (self.HOME["site_lat"], self.HOME["site_lon"]))

    def test_m1_no_site_saved_yet(self):
        app = self.app
        for k in self.HOME:
            app.settings.pop(k, None)
        app._loading_meta = True
        app.lat.set(""); app.lon.set(""); app.elevation.set("")
        app._loading_meta = False
        self._lights_box("zero")
        app._header_setup({"SITELAT": 0.0, "SITELONG": 0.0})
        self.assertEqual((app.lat.get(), app.lon.get()), ("", ""))
        self.assertIsNone(app._own_setup)

    # ---- M2 ---------------------------------------------------------------------------------------------------------
    def test_m2_site_typed_over_the_headers_survives_scan_folders(self):
        app = self.app
        self._lights_box()
        west = dict(self.FAR, SITELONG=110.88)                 # the longitude west-positive in the header
        app._header_setup(dict(west))
        self.assertIn("site 31.6800, 110.8800", app._setup_strip_parts()[0])
        app.lon.set("-110.8800")                               # typed over
        app.elevation.set("1268")
        self.pump(900)
        self.logged.clear()
        app._header_setup(dict(west))                          # Scan folders again (a Flats folder was added)
        self.assertEqual((app.lat.get(), app.lon.get(), app.elevation.get()), ("31.6800", "-110.8800", "1268"))
        self.assertEqual(self.logged, [])                      # nothing redone, nothing said twice
        self.assertIsNotNone(app._own_setup)
        text, buttons = app._setup_strip_parts()
        self.assertIn("site 31.6800, -110.8800, 1268 m", text)
        self.assertIn("The site was typed over the lights' header's (the header says 31.6800, 110.8800; "
                      "Moeller 1 m / QHY600)", text)
        self.assertNotIn("From the lights' headers (", text)
        self.assertEqual(buttons, ["save", "own"])
        self.assertIn("typed over", app.setup_strip_text.get())   # the strip followed the box
        # nothing saved as yours (the typed site is filed for this telescope only when another folder is scanned
        # or at a run: round 2, below)
        self.assertEqual(self.saves, [])
        self.assertEqual(app.settings["site_lon"], self.HOME["site_lon"])
        # Save as my setup saves what the strip shows
        app._setup_save_as_mine()
        self.assertEqual(app.settings["site_lon"], "-110.8800")

    def test_m2_untouched_boxes_read_as_before(self):
        app = self.app
        self._lights_box()
        app._header_setup(dict(self.FAR))
        app._header_setup(dict(self.FAR))
        text, _b = app._setup_strip_parts()
        self.assertIn("From the lights' headers (Moeller 1 m / QHY600)", text)
        self.assertNotIn("typed over", text)
        app.lon.set("")                                        # a box being typed is not "typed over"
        self.assertNotIn("typed over", app._setup_strip_parts()[0])

    # ---- round 2 ----------------------------------------------------------------------------------------------------
    def test_r2_typed_over_site_follows_the_telescope(self):
        """The reviewer's run: type -110.88 over a west-positive header, scan the next night's folder, go back."""
        app = self.app
        west = dict(self.FAR, SITELONG=110.88)
        first = self._lights_box("night1")
        app._header_setup(dict(west))
        app.lon.set("-110.8800")
        app.elevation.set("1268")
        self._lights_box("night2")
        self.logged.clear()
        app._header_setup(dict(west))                          # the same telescope's next night
        self.assertEqual((app.lat.get(), app.lon.get(), app.elevation.get()), ("31.6800", "-110.8800", "1268"))
        line = " ".join(self.logged)
        self.assertIn("Using the site you typed over this telescope's header before, 31.6800, -110.8800 "
                      "(the header says 31.6800, 110.8800)", line)
        self.assertIn("nothing is saved as yours", line)
        self.assertFalse(any(t.lstrip().startswith(("⚠", "✖")) for t in self.logged))
        self.assertIn("The site was typed over the lights' header's", app._setup_strip_parts()[0])
        rec = app.settings["frames_setups"][-1]
        self.assertEqual((rec["use"], rec["lon"], rec["values"]["lon"]), ("frames", 110.88, "-110.8800"))
        self.assertEqual(app.settings["site_lon"], self.HOME["site_lon"])     # never your saved site
        self.assertEqual(app._own_setup["lon"], self.HOME["site_lon"])
        app.light_dir.set(first)                               # back to the first folder
        app._header_setup(dict(west))
        self.assertEqual((app.lon.get(), app.elevation.get()), ("-110.8800", "1268"))
        # your own lights in between, then a new session's scan: still the typed site
        app._restore_own_setup()
        self.assertEqual(app.lon.get(), self.HOME["site_lon"])
        self._lights_box("night3")
        app._header_setup(dict(west))
        self.assertEqual(app.lon.get(), "-110.8800")
        # another telescope at that site gets its own header's site
        self._lights_box("other")
        app._header_setup(dict(west, TELESCOP="Other 0.4 m", FOCALLEN=2000))
        self.assertEqual(app.lon.get(), "110.8800")
        # the header's numbers typed back end it
        self._lights_box("night3")
        app._header_setup(dict(west))
        self.assertEqual(app.lon.get(), "-110.8800")
        app.lon.set("110.8800")
        self._lights_box("night4")
        app._header_setup(dict(west))
        self.assertEqual(app.lon.get(), "110.8800")
        self.assertNotIn("lon", app.settings["frames_setups"][-1]["values"])

    def test_r2_typed_over_site_filed_at_a_run(self):
        app = self.app
        self._lights_box("night1")
        app._header_setup(dict(self.FAR, SITELONG=110.88))
        app.lon.set("-110.8800")
        app._remember_frames_setup()                           # as at a photometry run
        self.assertEqual(app.settings["frames_setups"][-1]["values"]["lon"], "-110.8800")
        # Save as my setup: it is yours now, nothing left filed for "their" frames
        app._setup_save_as_mine()
        self.assertEqual(app.settings["frames_setups"], [])

    def test_r2_emptied_site_box_is_said(self):
        app = self.app
        self._lights_box()
        app._header_setup(dict(self.FAR))
        app.lat.set("")
        self.pump(200)
        text, buttons = app._setup_strip_parts()
        self.assertIn("The Latitude or Longitude box is empty: type the frames' site, or press Scan folders to put "
                      "the header's back.", text)
        self.assertNotIn("typed over", text)
        self.assertEqual(app.setup_strip_text.get(), text)
        self.assertIn("Fill in the Latitude and Longitude boxes first", app._setup_save_why())
        app._header_setup(dict(self.FAR))                      # Scan folders: the header's back
        self.assertEqual(app.lat.get(), "31.6800")
        self.assertNotIn("box is empty", app._setup_strip_parts()[0])
        self.assertNotIn("frames_setups", app.settings)

    def test_r2_manual_says_what_a_trim_does_to_a_left_out_point(self):
        text = " ".join(_src(os.path.join("docs", "manual", "17-fit-a-transit-and-submit-it.md")).split())
        self.assertIn("One case ends the choice", text)
        self.assertIn("When you then clear that trim, the point is fitted again", text)

    # ---- S1 ---------------------------------------------------------------------------------------------------------
    def test_s1_another_telescope_at_the_same_site_gets_its_own_optics(self):
        app = self.app
        self._lights_box("a")
        app._header_setup(dict(self.FAR))
        self.assertAlmostEqual(float(app.focal_mm.get()), 6800.0)
        self._lights_box("b")
        app._header_setup(dict(self.FAR, TELESCOP="Other 0.4 m", FOCALLEN=2000, XPIXSZ=9.0))
        self.assertAlmostEqual(float(app.focal_mm.get()), 2000.0)
        self.assertAlmostEqual(float(app.pixel_um.get()), 9.0)
        text, _b = app._setup_strip_parts()
        self.assertIn("focal length 2000 mm, pixel 9 µm", text)
        self.assertIn("From the lights' headers (Other 0.4 m / QHY600)", text)
        self.assertEqual(app._frames_setup["scope"]["telescope"], "Other 0.4 m")
        # the same telescope in the SAME folder with no optics in the header: your own, never the first telescope's
        self._lights_box("c")
        app._header_setup({"SITELAT": 31.68, "SITELONG": -110.88, "TELESCOP": "Third"})
        self.assertAlmostEqual(float(app.focal_mm.get()), 2900.0)
        # your setup aside is still yours
        self.assertEqual(app._own_setup["lat"], self.HOME["site_lat"])
        self.assertAlmostEqual(float(app._own_setup["focal_mm"]), 2900.0)

    def test_s1_same_telescope_other_folder_keeps_the_typed_elevation(self):
        app = self.app
        self._lights_box("night1")
        app._header_setup(dict(self.FAR))
        app.elevation.set("1268")
        self._lights_box("night2")
        app._header_setup(dict(self.FAR))
        self.assertEqual(app.elevation.get(), "1268")
        self.assertEqual(app._frames_setup["folder"], app.light_dir.get())
        self.assertNotIn("Elevation is not in the header", app._setup_strip_parts()[0])
        # another telescope there: not carried
        self._lights_box("night3")
        app._header_setup(dict(self.FAR, TELESCOP="Other 0.4 m", FOCALLEN=2000))
        self.assertEqual(app.elevation.get(), "")

    # ---- S2 ---------------------------------------------------------------------------------------------------------
    def test_s2_load_series_over_a_frames_strip(self):
        app = self.app
        self._lights_box()
        app._header_setup(dict(self.FAR))
        self.assertEqual(app._setup_strip_parts()[1], ["save", "own"])
        with mock.patch.object(app, "_apply_meta_values",
                               side_effect=lambda meta: (app.lat.set("48.0000"), app.lon.set("11.0000"),
                                                         app.elevation.set("500"))), \
                mock.patch.object(app, "_take_series_snapshot"):
            app._apply_meta({"lat": "48.0000", "lon": "11.0000", "elevation": "500"})
        text, buttons = app._setup_strip_parts()
        self.assertEqual(buttons, [])                           # no Save as my setup for a series' site
        self.assertTrue(text.startswith("The Site and Optics boxes hold the loaded series' values: site 48.0000, "
                                        "11.0000, 500 m"), text)
        self.assertNotIn("From the lights' headers", text)
        self.assertEqual(app.setup_strip_text.get(), text)
        app._setup_save_as_mine()                               # a stray press: nothing
        self.assertEqual(app.settings["site_lat"], self.HOME["site_lat"])
        app._remember_frames_setup()                            # nothing filed under the header's telescope
        self.assertNotIn("frames_setups", app.settings)
        self.assertEqual(self.saves, [])
        # Scan folders brings the lights' setup back
        app._header_setup(dict(self.FAR))
        self.assertEqual((app.lat.get(), app._frames_setup["kind"]), ("31.6800", "frames"))
        self.assertEqual(app._own_setup["lat"], self.HOME["site_lat"])

    def test_s2_series_with_the_same_site_leaves_the_strip(self):
        app = self.app
        self._lights_box()
        app._header_setup(dict(self.FAR))
        with mock.patch.object(app, "_apply_meta_values"), mock.patch.object(app, "_take_series_snapshot"):
            app._apply_meta({})
        self.assertEqual(app._frames_setup["kind"], "frames")

    def test_s2_load_series_over_a_microobservatory_strip(self):
        app = self.app
        self._lights_box("mobs")
        head = {"TELESCOP": "MicroObservatory", "XPIXSZ": 13.6, "FOCALLEN": 560, "NAXIS1": 650, "NAXIS2": 500}
        app._setup_microobservatory(head, 10)
        self.assertEqual(app._frames_setup["kind"], "mobs")
        with mock.patch.object(app, "_apply_meta_values", side_effect=lambda meta: app.lat.set("48.0000")), \
                mock.patch.object(app, "_take_series_snapshot"):
            app._apply_meta({})
        text, buttons = app._setup_strip_parts()
        self.assertEqual((app._frames_setup["kind"], buttons), ("series", []))
        self.assertNotIn("MicroObservatory's telescope", text)
        app._restore_own_setup()

    # ---- S3 ---------------------------------------------------------------------------------------------------------
    MOBS = {"TELESCOP": "MicroObservatory", "XPIXSZ": 13.6, "FOCALLEN": 560, "NAXIS1": 650, "NAXIS2": 500}

    def test_s3_pattern_back_on_your_own_frames(self):
        app = self.app
        app.pattern.set("RGGB")
        try:
            self._lights_box("mobs")
            app._setup_microobservatory(dict(self.MOBS), 78)
            app._setup_use_own()
            self.assertEqual(app.pattern.get(), core.MONO)          # their frames are monochrome
            app._setup_microobservatory(dict(self.MOBS), 78)        # scanned again: still MONO, still yours
            self.assertEqual((app.pattern.get(), app.lat.get()), (core.MONO, self.HOME["site_lat"]))
            self._lights_box("home")
            self.logged.clear()
            app._header_setup({"EXPTIME": 60})                      # a home folder; header names no Bayer pattern
            self.assertEqual(app.pattern.get(), "RGGB")
            self.assertTrue(any("Bayer pattern back to RGGB" in t for t in self.logged))
            # back on the MicroObservatory folder (your setup, as chosen): MONO again, and RGGB again after it
            self._lights_box("mobs")
            app._setup_microobservatory(dict(self.MOBS), 78)
            self.assertEqual((app.pattern.get(), app._frames_setup["kind"]), (core.MONO, "own_chosen"))
            self._lights_box("home")
            app._header_setup({"EXPTIME": 60})
            self.assertEqual(app.pattern.get(), "RGGB")
        finally:
            app._restore_own_setup()
            app.pattern.set("RGGB")

    def test_s3_use_the_frames_setup_sets_your_own_pattern_aside(self):
        app = self.app
        app.pattern.set("RGGB")
        try:
            folder = self._lights_box("mobs")
            app._setup_microobservatory(dict(self.MOBS), 78)
            app._setup_use_own()
            with mock.patch.object(app, "_lights", return_value=[os.path.join(folder, "MOBS_a.fits")]), \
                    mock.patch.object(core, "read_header", return_value=dict(self.MOBS)):
                app._setup_use_frames()
            self.assertEqual(app._frames_setup["kind"], "mobs")
            self.assertEqual(app._own_setup["pattern"], "RGGB")      # not MONO set aside as "yours"
            self.assertEqual(app.pattern.get(), core.MONO)
            app._restore_own_setup()
            self.assertEqual(app.pattern.get(), "RGGB")
        finally:
            app._restore_own_setup()
            app.pattern.set("RGGB")

    def test_s3_a_pattern_picked_by_hand_is_kept(self):
        app = self.app
        app.pattern.set("RGGB")
        try:
            self._lights_box("mobs")
            app._setup_microobservatory(dict(self.MOBS), 78)
            app._setup_use_own()
            app.pattern.set("GRBG")                                 # picked by hand since
            self._lights_box("home")
            app._header_setup({"EXPTIME": 60})
            self.assertEqual(app.pattern.get(), "GRBG")
        finally:
            app._restore_own_setup()
            app.pattern.set("RGGB")

    # ---- S4 ---------------------------------------------------------------------------------------------------------
    def _no_optics_saved(self):
        app = self.app
        app.settings.pop("focal_mm", None); app.settings.pop("pixel_um", None)
        app._loading_meta = True
        app.focal_mm.set(""); app.pixel_um.set("")
        app._loading_meta = False

    def test_s4_held_values_do_not_carry_to_the_next_telescope(self):
        app = self.app
        self._no_optics_saved()
        self._lights_box("a")
        app._header_setup({"EXPTIME": 60, "TELESCOP": "A", "FOCALLEN": 500, "XPIXSZ": 2.4})
        self.assertAlmostEqual(float(app.focal_mm.get()), 500.0)
        self.assertIn("not saved as yours: focal length 500", app._setup_strip_parts()[0])
        self._lights_box("b")
        self.logged.clear()
        app._header_setup({"EXPTIME": 60, "TELESCOP": "B"})         # no optics in this header
        self.assertEqual((str(app._setup_value("focal_mm")), str(app._setup_value("pixel_um"))), ("", ""))
        text, buttons = app._setup_strip_parts()
        self.assertNotIn("From the lights' headers", text)
        self.assertNotIn("focal length", text)
        self.assertEqual(buttons, [])
        self.assertTrue(any("are taken out" in t for t in self.logged))
        self._lights_box("c")
        self.logged.clear()
        app._header_setup({"EXPTIME": 60, "TELESCOP": "C", "FOCALLEN": 1000})
        self.assertAlmostEqual(float(app.focal_mm.get()), 1000.0)   # filled, not "yours 500, header 1000"
        text, buttons = app._setup_strip_parts()
        self.assertNotIn("differ", text)
        self.assertNotIn("yours 500", " ".join(self.logged))
        self.assertIn("not saved as yours: focal length 1000", text)
        self.pump(900)
        self.assertNotIn("focal_mm", app.settings)
        self.assertEqual(self.saves, [])

    def test_s4_straight_from_a_to_c(self):
        app = self.app
        self._no_optics_saved()
        self._lights_box("a")
        app._header_setup({"EXPTIME": 60, "TELESCOP": "A", "FOCALLEN": 500, "XPIXSZ": 2.4})
        self._lights_box("c")
        app._header_setup({"EXPTIME": 60, "TELESCOP": "C", "FOCALLEN": 1000})
        self.assertAlmostEqual(float(app.focal_mm.get()), 1000.0)
        self.assertEqual(str(app._setup_value("pixel_um")), "")
        self.assertNotIn("differ", app._setup_strip_parts()[0])

    def test_s4_same_lights_scanned_again_keep_the_held_values(self):
        app = self.app
        self._no_optics_saved()
        self._lights_box("a")
        head = {"EXPTIME": 60, "TELESCOP": "A", "FOCALLEN": 500, "XPIXSZ": 2.4}
        app._header_setup(dict(head))
        app._header_setup(dict(head))
        self.assertAlmostEqual(float(app.focal_mm.get()), 500.0)
        self._lights_box("a2")                                      # the same telescope's other night
        app._header_setup(dict(head))
        self.assertAlmostEqual(float(app.focal_mm.get()), 500.0)
        self.assertIn("not saved as yours: focal length 500", app._setup_strip_parts()[0])
        # a value typed over the header's is yours: it stays for the next telescope
        app.focal_mm.set(510.0)
        self.pump(900)
        self._lights_box("b")
        app._header_setup({"EXPTIME": 60, "TELESCOP": "B"})
        self.assertAlmostEqual(float(app.focal_mm.get()), 510.0)
        app.settings["focal_mm"] = 2900.0

    # ---- nits -------------------------------------------------------------------------------------------------------
    def test_nit_rule_6_files_only_what_was_typed_for_them(self):
        app = self.app
        self._lights_box()
        app._header_setup({"EXPTIME": 60, "SITELAT": 31.68, "SITELONG": -110.88, "TELESCOP": "Bare"})
        self.assertAlmostEqual(float(app.focal_mm.get()), 2900.0)   # your own: the header gave none
        app.elevation.set("1268")
        app._remember_frames_setup()
        values = app.settings["frames_setups"][-1]["values"]
        self.assertEqual(values.get("elevation"), "1268")
        self.assertNotIn("focal_mm", values)
        self.assertNotIn("pixel_um", values)
        app.focal_mm.set(6800.0)                                    # typed for this telescope: filed
        app._remember_frames_setup()
        self.assertAlmostEqual(float(app.settings["frames_setups"][-1]["values"]["focal_mm"]), 6800.0)

    def test_nit_aperture_source_in_the_strip_and_the_question_stays(self):
        app = self.app
        app.aperture_mm.set("152")
        app.settings["aperture_scope_last"] = {"telescope": "", "camera": "MOBS cam", "focal_mm": 560.0,
                                               "aperture_mm": 152.0}
        self.boxes["askyesno"].return_value = False
        with mock.patch.object(app, "after_idle"):
            app._check_aperture_scope({"INSTRUME": "ASI2600MM", "FOCALLEN": 2098})
        self.assertEqual(self.boxes["askyesno"].call_count, 1)      # m145 stays (triage K15 keeps its after-No line)
        self.assertIn("aperture 152 mm (still from the last frames,", app._setup_values_words())
        self.assertIn(": check it)", app._setup_values_words())
        app._aperture_focus_in()
        app.aperture_mm.set("203")
        app._aperture_typed()
        self.assertIn("aperture 203 mm (typed by hand)", app._setup_values_words())
        # Yes: confirmed
        app.aperture_mm.set("152")
        app._aperture_before = "152"
        app._aperture_hand = None
        app.__dict__.pop("_aperture_asked", None)
        self.boxes["askyesno"].return_value = True
        app._check_aperture_scope({"INSTRUME": "ASI2600MM", "FOCALLEN": 2098})
        self.assertIn("aperture 152 mm (confirmed for this telescope)", app._setup_values_words())


if __name__ == "__main__":
    unittest.main()
