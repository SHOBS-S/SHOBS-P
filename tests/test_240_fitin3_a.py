"""2.4.0 fit-in F3, package A (Input, Calibrate, app frame):
A1 the "Setup for these frames" strip (no questions, nothing saved silently; RESULTS-CHANGING for far-site frames),
A2 a color mask under MONO stops Calibrate, B the calibration-plan line, #113 Save for ExoClock… writes its own
folder, the merges m036 m075 m094 m146 m150, and the F2 leftovers F2-a (no such folder), F2-b (UI watch and modal
boxes), F2-f (tooltip follows the widget), F2-g (Cancel greyed), F2-h (Scan folders under the busy lock)."""
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import photometry_core as core  # noqa: E402
import ui_watch  # noqa: E402

try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


def _src(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


def _fits(folder, name, exptime, imagetyp, value=100):
    from astropy.io import fits
    os.makedirs(folder, exist_ok=True)
    h = fits.Header()
    h["EXPTIME"] = exptime
    h["IMAGETYP"] = imagetyp
    path = os.path.join(folder, name)
    fits.PrimaryHDU(np.full((8, 8), value, dtype=np.uint16), header=h).writeto(path)
    return path


# ---------------------------------------------------------------------------------------------------------------------
class TestUIWatchModal(unittest.TestCase):
    """F2-b: a native message box or file dialog stops the beat (its own modal loop); that is not a stall."""

    @staticmethod
    def _frame(filename, func, back=None):
        return types.SimpleNamespace(f_code=types.SimpleNamespace(co_filename=filename, co_name=func), f_back=back)

    def test_in_modal(self):
        tkdir = os.path.join("C:", "Python312", "Lib", "tkinter")
        app = self._frame(os.path.join(ROOT, "clearv_app.py"), "save_aavso")
        box = self._frame(os.path.join(tkdir, "messagebox.py"), "askyesno", app)
        inner = self._frame(os.path.join(tkdir, "commondialog.py"), "show", box)
        self.assertTrue(ui_watch._in_modal(inner))
        self.assertTrue(ui_watch._in_modal(self._frame(os.path.join(tkdir, "filedialog.py"), "askdirectory", app)))
        self.assertTrue(ui_watch._in_modal(self._frame(os.path.join(tkdir, "__init__.py"), "wait_window", app)))
        # a file of the same name outside tkinter, or SHOBS-P's own code: not a question
        self.assertFalse(ui_watch._in_modal(self._frame(os.path.join(ROOT, "messagebox.py"), "x", app)))
        self.assertFalse(ui_watch._in_modal(self._frame(os.path.join(tkdir, "__init__.py"), "mainloop", app)))
        self.assertFalse(ui_watch._in_modal(app))
        self.assertFalse(ui_watch._in_modal(None))

    def test_check_reports_nothing_while_a_box_is_up(self):
        lines, now = [], [0.0]
        w = ui_watch.UIWatch(lines.append, main_thread_id=threading.get_ident(), clock=lambda: now[0])
        w.beat()
        with mock.patch.object(ui_watch, "_in_modal", return_value=True):
            for t in (5.0, 50.0, 98.7):
                now[0] = t
                w.check()
        self.assertEqual(lines, [])
        self.assertEqual(w.last, 98.7)        # counted from the box, so a real stall after it is timed from there
        with mock.patch.object(ui_watch, "_in_modal", return_value=False):
            now[0] = 100.0
            w.check()
            self.assertEqual(lines, [])       # 1.3 s after the box: no stall yet
            now[0] = 103.0
            w.check()
        self.assertEqual(len(lines), 1)
        self.assertIn("UI thread busy for 4.3 s", lines[0])


class TestCore(unittest.TestCase):
    def test_plan_short_warnings(self):
        plan = core.calibration_plan(False, True, False, False, dark_exp=110, light_exp=55)
        self.assertEqual(plan["short"], ["Darks can't be scaled correctly without bias or dark flats: add bias "
                                         "frames or dark flats, or use 55 s darks."])
        plan = core.calibration_plan(False, True, True, True, dark_exp=55, light_exp=55, flat_exp=0.5, darkflat_exp=2.0)
        self.assertEqual(plan["short"], ["Dark flats 2 s, flats 0.5 s: dark flats should match the flats' exposure. "
                                         "Take dark flats at 0.5 s."])
        plan = core.calibration_plan(False, True, True, False, dark_exp=55, light_exp=55, flat_exp=0.5)
        self.assertEqual(len(plan["short"]), 1)
        self.assertIn("Put dark flats in the Dark flats box", plan["short"][0])
        plan = core.calibration_plan(True, True, True, True, dark_exp=55, light_exp=55, flat_exp=0.5, darkflat_exp=0.5)
        self.assertEqual(plan["short"], [])
        self.assertEqual(len(plan["short"]), len(plan["warnings"]))

    def test_exoclock_folder_name(self):
        import clearv_app as ca
        jd0 = 2461340.60
        self.assertEqual(ca.exoclock_folder_name("TOI-2046 b", jd0, "JABA"),
                         "ExoClock_TOI-2046b_20261027_JABA_SHOBS-P")
        self.assertEqual(ca.exoclock_folder_name("TOI-2046 b", jd0, ""), "ExoClock_TOI-2046b_20261027_obs_SHOBS-P")
        self.assertEqual(ca.exoclock_folder_name("WASP-49 b", jd0, "J/A B"),
                         "ExoClock_WASP-49b_20261027_J_A_B_SHOBS-P")
        self.assertEqual(ca.EXOCLOCK_INFO_NAME, "ExoClock_info.txt")

    def test_source_merges(self):
        """m036, m075, m146: the status copy of a Log line is gone (one message)."""
        src = _src("clearv_app.py")
        self.assertEqual(src.count("already exists: saved as {os.path.basename(free)} instead."), 1)
        self.assertNotIn("Wait for the current step to finish (or cancel it), then choose the new star again.", src)
        self.assertNotIn("Type this telescope's aperture in the Optics box (Input page).\")", src)
        self.assertNotIn("def _darkflat_notes", src)
        # A1: the questions and the silent-save lines are gone
        for gone in ("Is that your own site?", "Use that telescope's setup for these frames?",
                     "Use the site and optics in these frames for them?", "will not ask again for these values",
                     "now saved as your own optics", "saved as yours (change"):
            self.assertNotIn(gone, src, gone)

    def test_manual(self):
        ch23 = _src(os.path.join("docs", "manual", "23-messages-explained.md"))
        for words in ("Setup for these frames", "Save as my setup", "Use my own setup", "Plan: lights =",
                      "shows a color mask", "upload the .txt inside"):
            self.assertTrue(words in ch23, words)
        for gone in ("Is that your own site?", "Use that telescope's setup for these frames?"):
            self.assertFalse(gone in ch23, gone)
        self.assertIn("ExoClock_info.txt", _src(os.path.join("docs", "manual", "22-files-shobs-p-writes.md")))
        self.assertIn("Setup for these frames", _src(os.path.join("docs", "manual", "05-input.md")))
        self.assertIn("## Package A", _src(os.path.join("docs", "manual", "_reshoots_240_s.md")))


# ---------------------------------------------------------------------------------------------------------------------
@unittest.skipUnless(HAVE_TK, "no display")
class TestInApp(unittest.TestCase):
    HOME = {"site_lat": "35.3030", "site_lon": "-81.9400", "site_elevation": "280", "site_tz": "America/New_York"}

    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test_240_fitin3_a.log")
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

    # ---- A1: the strip ---------------------------------------------------------------------------------------------
    def test_a1_far_site_used_at_once_nothing_saved(self):
        app, ca = self.app, self.ca
        self._lights_box()
        with mock.patch.object(ca, "ask_choice") as ask:
            app._header_setup(dict(self.FAR))
            ask.assert_not_called()
        self.boxes["askyesno"].assert_not_called()
        self.assertEqual((app.lat.get(), app.lon.get()), ("31.6800", "-110.8800"))   # used for these frames at once
        self.assertEqual(app.elevation.get(), "")                                    # never yours with their site
        self.assertAlmostEqual(float(app.focal_mm.get()), 6800.0)
        self.assertEqual(app.telescope_name.get(), "")
        self.assertIsNotNone(app._own_setup)
        self.pump(900)                                                               # past every debounced save
        self.assertEqual(self.saves, [])
        self.assertEqual(app.settings["site_lat"], self.HOME["site_lat"])
        self.assertEqual(app.settings["focal_mm"], 2900.0)
        text, buttons = app._setup_strip_parts()
        self.assertEqual(buttons, ["save", "own"])
        self.assertIn("site 31.6800, -110.8800", text)
        self.assertIn("focal length 6800 mm", text)
        self.assertIn("From the lights' headers (Moeller 1 m / QHY600)", text)
        self.assertIn("km from your site", text)
        self.assertIn("In use for these frames only: nothing is saved as yours.", text)
        self.assertIn("Elevation is not in the header: type it in the Elevation box.", text)
        self.assertIn("Elevation not in the header", app.status.get())
        app._update_setup_strip()
        self.assertEqual(app.setup_group.winfo_manager(), "pack")
        self.assertEqual(app.setup_strip_text.get(), text)
        self.assertEqual(app.setup_buttons["save"].winfo_manager(), "pack")
        self.assertEqual(app.setup_buttons["frames"].winfo_manager(), "")
        # the elevation typed: the strip follows, and it is still not saved as yours
        app.elevation.set("1268")
        self.pump(900)
        self.assertNotIn("Elevation is not in the header", app.setup_strip_text.get())
        self.assertIn("1268 m", app.setup_strip_text.get())
        self.assertEqual(app.settings["site_elevation"], "280")
        # a second scan of the same lights changes nothing
        app._header_setup(dict(self.FAR))
        self.assertEqual(app.elevation.get(), "1268")
        self.assertEqual(app._frames_setup["kind"], "frames")

    def test_a1_use_my_own_setup_is_remembered(self):
        app, ca = self.app, self.ca
        folder = self._lights_box()
        app._header_setup(dict(self.FAR))
        app._setup_use_own()
        self.assertIsNone(app._own_setup)
        self.assertEqual((app.lat.get(), app.elevation.get()), (self.HOME["site_lat"], "280"))
        self.assertAlmostEqual(float(app.focal_mm.get()), 2900.0)
        self.assertEqual(app.telescope_name.get(), "iOptron 14 RC")
        text, buttons = app._setup_strip_parts()
        self.assertEqual(buttons, ["frames"])
        self.assertIn("These lights were taken at latitude 31.680, longitude -110.880", text)
        self.assertIn("Your own setup is in use for them, as you chose", text)
        self.assertEqual(app.settings["frames_setups"][-1]["use"], "own")
        # scanned again (Calibrate scans again): still yours, said in the Log, no question
        self.logged.clear()
        app._header_setup(dict(self.FAR))
        self.assertEqual(app.lat.get(), self.HOME["site_lat"])
        self.assertEqual(app._frames_setup["kind"], "own_chosen")
        self.assertTrue(any("your own site and optics are in use for them, as you chose" in t for t in self.logged))
        # remembered for the telescope at that site: another folder of its lights, a new session
        app._frames_choice_s.clear()
        self._lights_box("far2")
        app._header_setup(dict(self.FAR))
        self.assertEqual(app._frames_setup["kind"], "own_chosen")
        # Use the frames' setup goes back
        with mock.patch.object(app, "_lights", return_value=[os.path.join(folder, "a.fits")]), \
                mock.patch.object(core, "read_header", return_value=dict(self.FAR)):
            app._setup_use_frames()
        self.assertEqual(app.lat.get(), "31.6800")
        self.assertEqual(app._frames_setup["kind"], "frames")
        self.assertEqual(app.settings["frames_setups"], [])
        self.boxes["askyesno"].assert_not_called()

    def test_a1_save_as_my_setup_from_the_frames(self):
        app = self.app
        folder = self._lights_box()
        app._header_setup(dict(self.FAR, SITEELEV=1268))
        app._frames_mount = {"mount": core.MOUNT_ALTAZ, "why": "test", "folder": folder}
        self.assertIn(f"{core.mount_label(core.MOUNT_ALTAZ)} mount", app._setup_strip_parts()[0])
        self.assertEqual(app._setup_save_why(), "")
        app._setup_save_as_mine()
        self.assertIsNone(app._own_setup)
        self.assertEqual(app.settings["site_lat"], "31.6800")
        self.assertEqual(app.settings["site_lon"], "-110.8800")
        self.assertEqual(app.settings["site_elevation"], "1268")
        self.assertEqual(app.settings["focal_mm"], 6800.0)
        self.assertEqual(app.settings["mount"], core.MOUNT_ALTAZ)
        self.assertEqual(app._saved_site()[:2], (31.68, -110.88))
        self.assertTrue(any(t.startswith("Saved as your setup: site 31.6800, -110.8800, 1268 m") for t in self.logged))
        text, buttons = app._setup_strip_parts()
        self.assertTrue(text.startswith("Your own setup: site 31.6800"), text)
        self.assertEqual(buttons, [])
        app.mount_choice.set(core.mount_label(core.MOUNT_GEM))

    def test_a1_own_frames_filled_values_are_held_until_saved(self):
        app = self.app
        self._lights_box("own")
        app.settings.pop("focal_mm", None)
        app._loading_meta = True
        app.focal_mm.set("")
        app._loading_meta = False
        self.saves.clear()
        app._header_setup({"EXPTIME": 60, "FOCALLEN": 2098, "XPIXSZ": 3.76})
        self.assertAlmostEqual(float(app.focal_mm.get()), 2098.0)
        self.pump(900)
        self.assertNotIn("focal_mm", app.settings)          # was saved as yours without asking (m137)
        self.assertEqual(self.saves, [])
        text, buttons = app._setup_strip_parts()
        self.assertEqual(buttons, ["save"])
        self.assertIn("From the lights' headers, not saved as yours: focal length 2098", text)
        self.assertTrue(any("Not saved as yours: Save as my setup" in t for t in self.logged))
        app._setup_save_as_mine()
        self.assertEqual(app.settings["focal_mm"], 2098.0)
        self.assertEqual(app._setup_strip_parts()[1], [])
        # a value typed over a header's is yours: saved by the box as always
        app.settings.pop("focal_mm", None)
        app._loading_meta = True
        app.focal_mm.set("")
        app._loading_meta = False
        app._header_setup({"EXPTIME": 60, "FOCALLEN": 2098})
        app.focal_mm.set(2100.0)
        self.pump(900)
        self.assertEqual(app.settings.get("focal_mm"), 2100.0)

    def test_a1_own_frames_header_differs(self):
        app = self.app
        self._lights_box("own")
        self.boxes["askyesno"].return_value = False
        app._header_setup({"EXPTIME": 60, "SITELAT": 35.34, "SITELONG": -81.94, "FOCALLEN": 2098})
        self.boxes["askyesno"].assert_not_called()          # m138's question is gone
        self.assertEqual(app.lat.get(), self.HOME["site_lat"])   # yours stay
        text, buttons = app._setup_strip_parts()
        self.assertEqual(buttons, ["header"])
        self.assertIn("The lights' headers differ: Latitude yours 35.303, header 35.34; "
                      "Focal length (mm) yours 2900, header 2098.", text)
        self.assertNotIn("header_declined", app.settings)
        app._setup_use_header()
        self.assertEqual(app.lat.get(), "35.3400")
        self.assertAlmostEqual(float(app.focal_mm.get()), 2098.0)
        self.pump(900)
        self.assertEqual(app.settings["site_lat"], self.HOME["site_lat"])   # not saved by pressing it
        self.assertEqual(app.settings["focal_mm"], 2900.0)
        self.assertEqual(app._setup_strip_parts()[1], ["save"])
        app._setup_save_as_mine()
        self.assertEqual(app.settings["site_lat"], "35.3400")
        self.assertEqual(app.settings["focal_mm"], 2098.0)

    def test_a1_mount_is_noted_not_saved(self):
        app = self.app
        folder = self._lights_box("own")
        info = {"mount": core.MOUNT_ALTAZ, "note": "", "detected": {"mount": core.MOUNT_ALTAZ, "sure": True,
                                                                     "why": "OBJCTALT changes"}}
        with mock.patch.object(app, "_mount_info", return_value=info):
            self.assertEqual(app._save_header_mount([]), core.MOUNT_ALTAZ)
        self.assertNotIn("mount", app.settings)
        self.assertEqual(self.saves, [])
        self.assertFalse(any("saved as yours" in t for t in self.logged))
        app._frames_setup = {"kind": "own", "folder": folder, "differ": []}
        text, buttons = app._setup_strip_parts()
        self.assertIn(f"not saved as yours: {core.mount_label(core.MOUNT_ALTAZ)} mount", text)
        self.assertEqual(buttons, ["save"])
        app._setup_save_as_mine()
        self.assertEqual(app.settings["mount"], core.MOUNT_ALTAZ)
        app.mount_choice.set(core.mount_label(core.MOUNT_GEM))

    def test_a1_microobservatory_without_a_question(self):
        app, ca = self.app, self.ca
        folder = self._lights_box("mobs")
        app.pattern.set("RGGB")
        head = {"TELESCOP": "MicroObservatory", "XPIXSZ": 13.6, "FOCALLEN": 560, "NAXIS1": 650, "NAXIS2": 500}
        app._setup_microobservatory(head, 78)
        self.boxes["askyesno"].assert_not_called()
        self.assertEqual(app.pattern.get(), core.MONO)
        self.assertEqual(app.lat.get(), "31.6800")
        self.assertEqual(app.aperture_mm.get(), "152")
        self.assertIsNotNone(app._own_setup)
        text, buttons = app._setup_strip_parts()
        self.assertTrue(text.startswith("MicroObservatory's telescope: site 31.6800, -110.8800, 2340 m"), text)
        self.assertIn("monochrome, clear filter (CV), saturation 4000 ADU", text)
        self.assertEqual(buttons, ["save", "own"])
        self.assertIn("MicroObservatory's telescope, not yours", app._setup_save_why())
        app._setup_save_as_mine()                      # greyed: does nothing
        self.assertIsNotNone(app._own_setup)
        self.assertEqual(app.settings["site_lat"], self.HOME["site_lat"])
        self.assertFalse(any("answered No" in t for t in self.logged))
        # Use my own setup: yours back, the frames stay monochrome, and a second scan does not undo it
        app._setup_use_own()
        self.assertIsNone(app._own_setup)
        self.assertEqual(app.lat.get(), self.HOME["site_lat"])
        self.assertEqual(app.pattern.get(), core.MONO)
        app._setup_microobservatory(head, 78)
        self.assertEqual(app.lat.get(), self.HOME["site_lat"])
        self.assertEqual(app._setup_strip_parts()[1], ["frames"])
        with mock.patch.object(app, "_lights", return_value=[os.path.join(folder, "MOBS_a.fits")]), \
                mock.patch.object(core, "read_header", return_value=head):
            app._setup_use_frames()
        self.assertEqual(app.lat.get(), "31.6800")
        self.assertEqual(app._frames_setup["kind"], "mobs")
        app._restore_own_setup()
        app.pattern.set("RGGB")

    def test_a1_typed_for_them_before_comes_back(self):
        """Rule 6: the elevation typed for a far telescope's frames (its header has none) comes back with them."""
        app = self.app
        self._lights_box()
        app._header_setup(dict(self.FAR))
        app.elevation.set("1268")
        app._remember_frames_setup()                   # as at a photometry run
        rec = app.settings["frames_setups"][-1]
        self.assertEqual((rec["use"], rec["values"]["elevation"]), ("frames", "1268"))
        app._restore_own_setup()
        self.assertEqual(app.elevation.get(), "280")
        self._lights_box("far3")
        self.logged.clear()
        app._header_setup(dict(self.FAR))
        self.assertEqual(app.elevation.get(), "1268")
        self.assertTrue(any("As typed for this telescope's frames before: elevation 1268 m" in t for t in self.logged))
        self.assertNotIn("Elevation not in the header", app.status.get())
        # another telescope at another site gets nothing of it
        app._restore_own_setup()
        app._header_setup({"EXPTIME": 60, "SITELAT": 28.3, "SITELONG": -16.5, "TELESCOP": "Other"})
        self.assertEqual(app.elevation.get(), "")

    def test_a1_typed_site_for_these_frames_shows_in_the_strip(self):
        app = self.app
        self._lights_box("typed")
        app._typed_setup_for_these_frames()
        text, buttons = app._setup_strip_parts()
        self.assertTrue(text.startswith("The site and optics you typed"), text)
        self.assertEqual(buttons, ["save", "own"])
        app._setup_use_own()
        self.assertIsNone(app._own_setup)
        self.assertEqual(app._frames_setup["kind"], "own")

    def test_a1_strip_hidden_for_another_folder_and_after_new_setup(self):
        app = self.app
        self._lights_box()
        app._header_setup(dict(self.FAR))
        app._update_setup_strip()
        self.assertEqual(app.setup_group.winfo_manager(), "pack")
        self._lights_box("elsewhere")
        app._update_setup_strip()
        self.assertEqual(app.setup_group.winfo_manager(), "")
        app._restore_own_setup()
        self.assertIsNone(app._frames_setup)

    # ---- A2: a color mask under MONO stops Calibrate ------------------------------------------------------------
    def _conflict(self, folder):
        app = self.app
        app.pattern.set(core.MONO)
        app._pattern_user_set = False
        with mock.patch.object(core, "calibration_source", return_value=(["flat_1.fits"], None)), \
                mock.patch.object(core, "detect_sensor_from_flat", return_value=("cfa", 0.38)), \
                mock.patch.object(core, "read_header", return_value={"XBINNING": 2}):
            app._detect_sensor()
        self.assertEqual(app._sensor_conflict["folder"], folder)

    def test_a2_color_mask_under_mono(self):
        app, ca = self.app, self.ca
        folder = self._lights_box("cfa")
        self._conflict(folder)
        self.assertTrue(any(t.startswith("⚠ the flat shows a color mask (its 2x2 pixel planes differ by 38%)")
                            and "Calibrate asks before it starts" in t for t in self.logged), self.logged)
        asked = []

        def answer(value):
            def ask(parent, title, message, choices, safe, cancel=None):
                asked.append((message, choices, safe))
                return value
            return ask
        # Cancel (the safe answer): Calibrate does not start
        with mock.patch.object(ca, "ask_choice", answer("cancel")):
            self.assertFalse(app._sensor_conflict_check())
        message, choices, safe = asked[-1]
        self.assertIn("The flat shows a color mask (its 2x2 pixel planes differ by 38%), but the Bayer pattern is MONO",
                      message)
        self.assertIn("a color sensor binned 2x2 also looks like this", message)
        self.assertIn("Calibrating a color camera as monochrome gives wrong photometry", message)
        self.assertEqual([c[1] for c in choices], ["Color camera (RGGB)", "It is monochrome", "Cancel"])
        self.assertEqual(safe, "cancel")
        self.assertEqual(app.pattern.get(), core.MONO)
        self.assertTrue(self.logged[-1].startswith("Calibration not started: pick the camera's Bayer pattern"))
        # run_calibration stops on it before anything is built
        with mock.patch.object(ca, "ask_choice", answer("cancel")), \
                mock.patch.object(app, "_typed_site_check"), mock.patch.object(app, "scan_folders"), \
                mock.patch.object(app, "_prune_rejected") as later:
            app._scanned_lights = folder
            app.run_calibration()
            later.assert_not_called()
        # It is monochrome: counts as picked by hand, not asked again for this folder, no ⚠ at the next scan
        with mock.patch.object(ca, "ask_choice", answer("mono")):
            self.assertTrue(app._sensor_conflict_check())
        self.assertTrue(app._pattern_user_set)
        n = len(asked)
        self.logged.clear()
        with mock.patch.object(core, "calibration_source", return_value=(["flat_1.fits"], None)), \
                mock.patch.object(core, "detect_sensor_from_flat", return_value=("cfa", 0.38)), \
                mock.patch.object(core, "read_header", return_value={}):
            app._detect_sensor()
        self.assertIsNone(app._sensor_conflict)
        self.assertFalse(any(t.startswith("⚠") for t in self.logged), self.logged)
        with mock.patch.object(ca, "ask_choice", answer("cancel")):
            self.assertTrue(app._sensor_conflict_check())
        self.assertEqual(len(asked), n)
        # Color camera: RGGB
        app._sensor_conflict_answered = None
        self._conflict(folder)
        with mock.patch.object(ca, "ask_choice", answer("color")):
            self.assertTrue(app._sensor_conflict_check())
        self.assertEqual(app.pattern.get(), "RGGB")
        self.assertTrue(app._pattern_user_set)
        self.assertIsNone(app._sensor_conflict)
        # a pattern picked on the Calibrate page meanwhile, or another Lights folder: nothing to ask
        app._sensor_conflict = {"folder": folder, "what": "the flat", "more": "", "binned": ""}
        with mock.patch.object(ca, "ask_choice", answer("cancel")):
            self.assertTrue(app._sensor_conflict_check())          # the pattern is RGGB now
            app.pattern.set(core.MONO)
            self._lights_box("other")
            self.assertTrue(app._sensor_conflict_check())
        app.pattern.set("RGGB")
        app._pattern_user_set = False

    # ---- B: the calibration-plan line ----------------------------------------------------------------------------
    def _boxes(self, **exp):
        """Folders with one frame each at the given exposures; returns {name: folder}."""
        app = self.app
        out = {}
        typ = {"light": "LIGHT", "bias": "BIAS", "dark": "DARK", "darkflat": "DARKFLAT", "flat": "FLAT"}
        var = {"light": app.light_dir, "bias": app.bias_dir, "dark": app.dark_dir, "darkflat": app.darkflat_dir,
               "flat": app.flat_dir}
        app._loading_meta = True
        for name, seconds in exp.items():
            out[name] = os.path.join(self.dir, name)
            _fits(out[name], f"{name}_1.fits", seconds, typ[name])
            var[name].set(out[name])
        app._loading_meta = False
        return out

    def test_b_plan_line_plain_and_always_shown(self):
        app = self.app
        self.assertEqual(app._update_dark_exposure_note(), "")
        self.assertEqual(app.plan_label.winfo_manager(), "")
        self._boxes(light=60, bias=0.001, dark=60, flat=2, darkflat=2)
        text = app._update_dark_exposure_note()
        self.assertEqual(text, "Plan: lights = light − bias − (dark − bias); flats = flat − dark flat.")
        self.assertEqual(app.plan_note.get(), text)
        self.assertEqual(app.dark_exposure_note.get(), "")           # nothing wrong: no second line
        self.assertEqual(app.plan_label.winfo_manager(), "pack")
        self.assertEqual(str(app.plan_label.cget("style")), "Hint.TLabel")
        self.assertEqual(app.dark_exposure_label.winfo_manager(), "")
        self.assertNotIn("⚠", text)
        # the Log's plan is the same sentence
        lines = app._calibration_plan_lines(["b"], ["d"], None, ["f"], ["df"], None, [])
        self.assertEqual(lines[0], "Calibration: " + text[len("Plan: "):])

    def test_b_warning_line_only_when_something_is_wrong(self):
        app = self.app
        self._boxes(light=60, dark=30, flat=2, darkflat=5)
        text = app._update_dark_exposure_note()
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("Plan: lights = light − dark flat − (dark − dark flat) × t_light / t_dark"))
        self.assertIn("Darks 30 s, lights 60 s: the master dark is scaled ×2; 60 s darks are better.", lines[0])
        self.assertEqual(lines[1:], ["⚠ Dark flats 5 s, flats 2 s: dark flats should match the flats' exposure. "
                                     "Take dark flats at 2 s."])
        self.assertEqual(app.dark_exposure_note.get(), lines[1])
        self.assertEqual(app.dark_exposure_label.winfo_manager(), "pack")
        self.assertEqual(str(app.dark_exposure_label.cget("style")), "Warn.TLabel")

    def test_b_over_3x_dark_scale_is_the_bad_line(self):
        app = self.app
        self._boxes(light=180, dark=30, bias=0.001)
        text = app._update_dark_exposure_note()
        self.assertIn("\n✖ Darks 30 s, lights 180 s: the master dark would be scaled ×6", text)
        self.assertEqual(str(app.dark_exposure_label.cget("style")), "Bad.TLabel")
        app._dark_scale_chosen = (app.dark_dir.get(), app.light_dir.get())
        text = app._update_dark_exposure_note()
        self.assertNotIn("✖", text)
        self.assertIn("✔ Darks 30 s, lights 180 s: the master dark is scaled ×6, as you chose at Calibrate.", text)
        self.assertEqual(app.dark_exposure_note.get(), "")
        app._dark_scale_chosen = None

    def test_b_headers_read_once_per_folder(self):
        app = self.app
        boxes = self._boxes(light=60, dark=60, flat=2, darkflat=2)
        app.__dict__.pop("_plan_cache", None)
        real, calls = core.read_header, []
        with mock.patch.object(core, "read_header", side_effect=lambda p, *a, **k: calls.append(p) or real(p, *a, **k)):
            app._calibration_plan()
            n = len(calls)
            self.assertGreater(n, 0)
            app._calibration_plan()
            app._update_dark_exposure_note()
            self.assertEqual(len(calls), n)
            _fits(boxes["flat"], "flat_2.fits", 2, "FLAT")
            stamp = time.time_ns() + 10 ** 9
            os.utime(boxes["flat"], ns=(stamp, stamp))
            app._calibration_plan()
            self.assertGreater(len(calls), n)

    def test_m150_blink_says_only_the_count(self):
        app = self.app
        boxes = self._boxes(light=60, flat=2, darkflat=5)
        _fits(boxes["flat"], "darkflat_9.fits", 5, "DARKFLAT")
        app.blink_source.set("flats")
        app.status.set("")
        with mock.patch.object(app, "_blink_folder", return_value=boxes["flat"]), \
                mock.patch.object(app, "show_blink_frame", create=True), \
                mock.patch.object(core, "split_dark_flats",
                                  side_effect=lambda paths: ([p for p in paths if "darkflat" not in p],
                                                             [p for p in paths if "darkflat" in p])):
            try:
                app.load_blink_list()
            except Exception:
                pass   # the frame display is not what is tested here
        self.assertEqual(app.status.get(), "1 dark flat in the Flat folder is not shown (they are not flats).")
        app.blink_source.set("lights")

    # ---- merges -----------------------------------------------------------------------------------------------------
    def test_m075_star_change_while_a_job_runs(self):
        app = self.app
        app.busy = True
        before = app.status.get()
        self.assertEqual(app._switch_star("another planet chosen"), "busy")
        self.assertEqual(self.logged[-1], "Star not changed (another planet chosen): a step is still running. Choose "
                                          "the star again when it finishes.")
        self.assertEqual(app.status.get(), before)      # no second wording set beside the Log line

    def test_m094_object_question_is_the_one_message(self):
        app = self.app
        app.star_id.set("TrES-3")
        calls = []
        self._log.stop()
        with mock.patch.object(app, "log", side_effect=lambda t, *a, **k: calls.append((str(t), k))):
            self.boxes["askyesno"].return_value = False
            app._object_verdict("WASP-10", "TrES-3", None, None, None)
        self._log.start()
        self.assertEqual(self.boxes["askyesno"].call_count, 1)
        self.assertTrue(calls[0][0].startswith("Lights: The lights' FITS OBJECT says 'WASP-10', not the Input target"))
        self.assertTrue(calls[0][1].get("keep_status"))                 # the finding: Log only
        self.assertEqual(calls[1][0], "Kept TrES-3 (the lights' FITS OBJECT says 'WASP-10').")   # the answer
        self.assertFalse(calls[1][1].get("keep_status"))
        app.star_id.set("")

    def test_m036_name_taken_one_line(self):
        app, ca = self.app, self.ca
        taken = os.path.join(self.dir, "report.txt")
        open(taken, "w").close()
        app.status.set("before")
        with mock.patch.object(ca.filedialog, "asksaveasfilename", return_value=os.path.join(self.dir, "report .txt")), \
                mock.patch.object(core, "tidy_save_name", return_value=taken):
            path = app.output_path("report.txt", title="Save")
        self.assertEqual(os.path.basename(path), "report_2.txt")
        self.assertEqual([t for t in self.logged if "already exists" in t],
                         ["report.txt already exists: saved as report_2.txt instead."])
        self.assertEqual(app.status.get(), "before")    # set by the Log line alone (log is mocked here)

    # ---- F2 leftovers -----------------------------------------------------------------------------------------------
    def test_f2a_no_such_folder_then_the_same_path_again(self):
        app = self.app
        bias = os.path.join(self.dir, "bias")
        for i in range(3):
            _fits(bias, f"bias_{i}.fits", 0.001, "BIAS")
        with mock.patch.object(app, "_frames_folder_picked"), mock.patch.object(app, "_update_dark_exposure_note"):
            app.bias_dir.set(bias)
            app._folder_typed(app.bias_dir, "Bias", now=True)
            self.assertEqual(app._counts["Bias"].get(), "3 files")
            app.bias_dir.set(bias[:-2])
            app._folder_typed(app.bias_dir, "Bias", now=True)
            self.assertEqual(app._counts["Bias"].get(), "no such folder")
            app.bias_dir.set(bias)                      # typed back to the folder counted last
            app._folder_typed(app.bias_dir, "Bias", now=True)
            self.assertEqual(app._counts["Bias"].get(), "3 files")
            self.pump(500)

    def test_f2f_tooltip_follows_the_widget(self):
        app, ca = self.app, self.ca
        from tkinter import ttk
        words = {"now": "Scan the field first."}
        button = ttk.Button(app, text="x")
        old = ca.App.TOOLTIP_REFRESH_MS
        ca.App.TOOLTIP_REFRESH_MS = 30
        try:
            app._add_tooltip(button, lambda: words["now"])
            button.event_generate("<Enter>")
            self.pump(700)
            tips = [w for w in button.winfo_children() if w.winfo_class() == "Toplevel"]
            self.assertEqual(len(tips), 1)
            label = tips[0].winfo_children()[0]
            self.assertEqual(str(label.cget("text")), "Scan the field first.")
            words["now"] = "Another step is running. This is available again when it finishes."
            self.pump(200)
            self.assertEqual(str(label.cget("text")), words["now"])     # same tip, new words
            words["now"] = ""                                              # the button went live: no reason left
            self.pump(200)
            self.assertEqual([w for w in button.winfo_children() if w.winfo_class() == "Toplevel"], [])
            # and it opens again on the next hover; leaving closes it and stops the refresh
            words["now"] = "Again."
            button.event_generate("<Leave>")
            button.event_generate("<Enter>")
            self.pump(700)
            self.assertEqual(len([w for w in button.winfo_children() if w.winfo_class() == "Toplevel"]), 1)
            button.event_generate("<Leave>")
            self.pump(100)
            self.assertEqual([w for w in button.winfo_children() if w.winfo_class() == "Toplevel"], [])
        finally:
            ca.App.TOOLTIP_REFRESH_MS = old
            button.destroy()

    def test_f2g_cancel_buttons_greyed_with_nothing_running(self):
        app = self.app
        self.assertEqual(app._cancel_why(), "Nothing is running.")
        for button in (app.cal_cancel_button, app.photo_cancel_button):
            self.assertTrue(app.gates.is_gated(button))
            app.gates.refresh()
            self.assertTrue(button.instate(["disabled"]))
            self.assertEqual(app.gates.reason(button), "Nothing is running.")
        app.busy = True
        app.gates.refresh()
        for button in (app.cal_cancel_button, app.photo_cancel_button):
            self.assertFalse(button.instate(["disabled"]))          # live exactly while a job runs
        app.busy = False
        app._run_after_survey = True                                 # a run waiting for the comp survey
        self.assertEqual(app._cancel_why(), "")
        app._run_after_survey = False
        tp = app.transit_page
        old = getattr(tp, "_fit_queued", None)
        tp._fit_queued = ("fit",)                                    # a Fit transit waiting for the ephemeris
        self.assertEqual(app._cancel_why(), "")
        tp._fit_queued = old
        app.gates.refresh()

    def test_f2h_scan_folders_and_sample_data_under_the_busy_lock(self):
        import gate
        app = self.app
        for button in (app.scan_folders_button, app.demo_button):
            self.assertTrue(app.gates.is_gated(button))
            app.gates.refresh()
            self.assertFalse(button.instate(["disabled"]))
        app.busy = True
        app.gates.refresh()
        for button in (app.scan_folders_button, app.demo_button):
            self.assertTrue(button.instate(["disabled"]))
            self.assertEqual(app.gates.reason(button), gate.BUSY_REASON)
        app.busy = False
        app.gates.refresh()

    # ---- #113 -------------------------------------------------------------------------------------------------------
    def test_113_exoclock_files_in_their_own_folder(self):
        app, ca = self.app, self.ca
        tp = app.transit_page
        out = os.path.join(self.dir, "out")
        os.makedirs(out)
        jd = np.linspace(2461340.60, 2461340.75, 30)
        ra, dec, lat, lon = 348.32, 57.17, 35.37, -81.96
        app._loading_meta = True
        app.ra_hours.set(f"{ra / 15:.6f}"); app.dec_deg.set(f"{dec:.5f}")
        app.lat.set(str(lat)); app.lon.set(str(lon))
        app.output_dir.set(out)
        app._loading_meta = False
        tp.result = {"t": core.convert_times(jd, "BJD_TDB", ra, dec, lat, lon), "flux": np.ones(30),
                     "err": np.full(30, 0.002), "summary": {}, "planet": {"name": "WASP-49 b"}}
        meta = {"filter": "TG", "exposure": "60.0", "obscode": "JABA", "version": "2.4.0"}
        self._log.stop()
        logs = []
        try:
            with mock.patch.object(tp, "_file_meta", return_value=meta), \
                    mock.patch.object(tp, "_confirm_export", return_value=True), \
                    mock.patch.object(tp, "_still_checking", return_value=False), \
                    mock.patch.object(ca.filedialog, "asksaveasfilename") as dialog, \
                    mock.patch.object(app, "_remember_report") as remembered, \
                    mock.patch.object(app, "log", side_effect=lambda t, *a, **k: logs.append(str(t))):
                first = app.save_exoclock()
                # Go to ExoClock… and the sent marks still know the upload file (not the folder)
                self.assertEqual(remembered.call_args[0][0], "exoclock")
                self.assertEqual(remembered.call_args[0][2], first)
                status = app.status.get()
                second = app.save_exoclock()
                dialog.assert_not_called()                 # a folder, as the ExoFOP package: no file dialog
        finally:
            self._log.start()
            tp.result = None
        folder = os.path.join(out, "ExoClock_WASP-49b_20261027_JABA_SHOBS-P")
        self.assertEqual(first, os.path.join(folder, "ExoClock_WASP-49b_20261027.txt"))
        self.assertEqual(sorted(os.listdir(folder)), ["ExoClock_WASP-49b_20261027.txt", "ExoClock_info.txt"])
        self.assertEqual(second, os.path.join(folder + "_2", "ExoClock_WASP-49b_20261027.txt"))   # a second save
        self.assertEqual(sorted(os.listdir(folder + "_2")), ["ExoClock_WASP-49b_20261027.txt", "ExoClock_info.txt"])
        self.assertEqual(sorted(os.listdir(out)), [os.path.basename(folder), os.path.basename(folder) + "_2"])
        with open(os.path.join(folder, "ExoClock_info.txt")) as fh:
            self.assertIn("for ExoClock_WASP-49b_20261027.txt", fh.readline())
        with open(first) as fh:
            rows = [ln.split() for ln in fh if ln.strip() and not ln.startswith("#")]
        self.assertEqual(len(rows), 30)                     # the upload file itself is unchanged
        self.assertLess(abs(float(rows[0][0]) - jd[0]) * 86400, 0.1)
        self.assertTrue(status.startswith("✔ Saved ExoClock_WASP-49b_20261027_JABA_SHOBS-P: upload the .txt inside "
                                          "(what to pick on the form is in ExoClock_info.txt)."), status)
        self.assertTrue(logs[0].startswith(f"ExoClock folder: {folder} (30 frames, JD_UTC mid-exposure"), logs)


if __name__ == "__main__":
    unittest.main()
