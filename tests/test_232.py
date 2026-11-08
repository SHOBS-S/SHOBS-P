"""2.3.2 UI/files items (numbers frozen at 2.3.0)."""
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ["SHOBS_ONSKY_OFF"] = "1"

import matplotlib  # noqa: E402
matplotlib.use("Agg")

import clearv_app as ca  # noqa: E402

JOHN = {"site_lat": "35.303", "site_lon": "-81.9407", "site_elevation": "280", "site_tz": "America/New_York",
        "focal_mm": 2900.0, "pixel_um": 3.76, "aperture_mm": "356", "telescope_name": "iOptron 14 RC"}


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
    @classmethod
    def setUpClass(cls):
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test232.log")
        with mock.patch.object(ca.App, "_save_settings", lambda *a, **k: None):
            cls.app = ca.App()
        cls.app.withdraw()
        cls.saves = []
        cls.app._save_settings = lambda *a, **k: cls.saves.append(dict(cls.app.settings))
        cls.patches = [mock.patch.object(ca.messagebox, n, return_value=True)
                       for n in ("showwarning", "showinfo", "showerror", "askyesno")]
        cls.boxes = {n: p.start() for n, p in zip(("showwarning", "showinfo", "showerror", "askyesno"), cls.patches)}

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.app.destroy()

    def setUp(self):
        app = self.app
        for box in self.boxes.values():
            box.reset_mock()
            box.return_value = True
        app._own_setup = None
        app._typed_borrow = None
        app._loading_meta = True   # set the boxes to John's setup without saving
        for k, v in JOHN.items():
            app.settings[k] = v
        app.lat.set(JOHN["site_lat"]); app.lon.set(JOHN["site_lon"]); app.elevation.set(JOHN["site_elevation"])
        app.site_tz.set(JOHN["site_tz"]); app.focal_mm.set(2900.0); app.pixel_um.set(3.76)
        app.aperture_mm.set("356"); app.telescope_name.set("iOptron 14 RC")
        app._loading_meta = False
        app._typed_site_pending = False
        app._confirm_setup_now()
        self.saves.clear()
        self.logged = []
        self._log = mock.patch.object(app, "log", side_effect=lambda t, *a, **k: self.logged.append(str(t)))
        self._log.start()

    def tearDown(self):
        self._log.stop()

    def _type(self, var, text):
        """As Tk does when typing over a selection: cleared, then one character at a time."""
        var.set("")
        for i in range(1, len(text) + 1):
            var.set(text[:i])

    def _type_matthew(self):
        app = self.app
        self._type(app.lat, "39.1918")
        self._type(app.lon, "-84.7104")
        self._type(app.elevation, "191")
        self._type(app.aperture_mm, "203")
        self._type(app.telescope_name, "MG Scope")
        app.focal_mm.set(2721.0)
        app.update()   # the debounced optics saves (none should land)

    # ---- item 1 -----------------------------------------------------------------------------------------------
    def test_typed_far_site_never_saved_while_typing(self):
        app = self.app
        self._type_matthew()
        for k, v in JOHN.items():
            self.assertEqual(app.settings.get(k), v, k)
        self.assertTrue(app._typed_site_pending)

    def test_no_keeps_saved_setup_and_borrows(self):
        app = self.app
        self._type_matthew()
        self.boxes["askyesno"].return_value = False
        app._typed_site_check()
        self.boxes["askyesno"].assert_called_once()
        msg = self.boxes["askyesno"].call_args[0][1]
        self.assertIn("km from your saved site", msg)
        self.assertIn("these frames only", msg)
        for k, v in JOHN.items():
            self.assertEqual(app.settings.get(k), v, k)
        self.assertIsNotNone(app._own_setup)
        self.assertEqual(app._own_setup["lat"], "35.303")
        self.assertEqual(app._own_setup["aperture_mm"], "356")
        self.assertEqual(app.lat.get(), "39.1918")   # the typed site stays in use for these frames
        # more typing now (a borrowed setup) is not saved either
        app.aperture_mm.set("200")
        self.assertEqual(app.settings["aperture_mm"], "356")
        # asked once only
        app._typed_site_check()
        self.boxes["askyesno"].assert_called_once()

    def test_no_kept_for_a_header_without_site_then_back(self):
        app = self.app
        self._type_matthew()
        self.boxes["askyesno"].return_value = False
        app._typed_site_check()
        app.light_dir.set("/tmp/matthew")
        app._header_setup({"EXPTIME": 60})   # Matthew's frames name no site
        self.assertEqual(app.lat.get(), "39.1918")
        app._header_setup({"EXPTIME": 60})   # scanned again: still his
        self.assertEqual(app.lat.get(), "39.1918")
        app.light_dir.set("/tmp/john")
        app._header_setup({"EXPTIME": 60, "SITELAT": 35.303, "SITELONG": -81.9407})
        self.assertIsNone(app._own_setup)
        self.assertEqual(app.lat.get(), "35.303")
        self.assertEqual(app.aperture_mm.get(), "356")
        for k, v in JOHN.items():
            self.assertEqual(app.settings.get(k), v, k)

    def test_yes_saves_site_and_optics(self):
        app = self.app
        self._type_matthew()
        app._typed_site_check()
        self.boxes["askyesno"].assert_called_once()
        self.assertEqual(app.settings["site_lat"], "39.1918")
        self.assertEqual(app.settings["site_elevation"], "191")
        self.assertEqual(app.settings["aperture_mm"], "203")
        self.assertEqual(app.settings["telescope_name"], "MG Scope")
        self.assertEqual(app.settings["focal_mm"], 2721.0)
        self.assertIsNone(app._own_setup)

    def test_small_own_edits_save_without_a_question(self):
        app = self.app
        self._type(app.lon, "-81.9400")
        self._type(app.elevation, "285")
        app._typed_site_check()
        self.boxes["askyesno"].assert_not_called()
        self.assertEqual(app.settings["site_lon"], "-81.9400")
        self.assertEqual(app.settings["site_elevation"], "285")

    def test_elevation_far_off_is_asked(self):
        app = self.app
        self._type(app.elevation, "2340")   # passes through 234, near enough to be saved on the way
        self.boxes["askyesno"].return_value = False
        app._typed_site_check()
        self.assertIn("elevation 2340 m", self.boxes["askyesno"].call_args[0][1])
        self.assertEqual(app.settings["site_elevation"], "280")

    def test_cleared_box_not_saved_and_not_asked(self):
        app = self.app
        app.lat.set("")
        self.assertEqual(app.settings["site_lat"], "35.303")
        app._typed_site_check()
        self.boxes["askyesno"].assert_not_called()

    def test_optics_typed_before_the_site_go_back_on_no(self):
        app = self.app
        self._type(app.aperture_mm, "203")   # at John's own site: saved (could be a new scope of his)
        self.assertEqual(app.settings["aperture_mm"], "203")
        self._type(app.lat, "39.1918"); self._type(app.lon, "-84.7104")
        self.boxes["askyesno"].return_value = False
        app._typed_site_check()
        self.assertEqual(app.settings["aperture_mm"], "356")
        self.assertEqual(app._own_setup["aperture_mm"], "356")
        self.assertTrue(any("aperture mm 203" in t for t in self.logged))

    def test_first_site_ever_saves(self):
        app = self.app
        for k in ("site_lat", "site_lon", "site_elevation"):
            app.settings.pop(k, None)
        app._confirm_setup_now()   # a fresh install: no site of yours yet
        self._type(app.lat, "51.5"); self._type(app.lon, "-0.12")
        self.assertEqual(app.settings["site_lat"], "51.5")
        self.boxes["askyesno"].assert_not_called()
        for k in ("site_lat", "site_lon", "site_elevation"):
            app.settings[k] = JOHN[k]

    # ---- item 14 ----------------------------------------------------------------------------------------------
    def test_fresh_install_optics_blank(self):
        with mock.patch.object(ca.App, "_load_settings", lambda self: {}), \
                mock.patch.object(ca.App, "_save_settings", lambda *a, **k: None):
            fresh = ca.App()
        try:
            fresh.withdraw()
            self.assertEqual(fresh._setup_value("focal_mm"), "")
            self.assertEqual(fresh._setup_value("pixel_um"), "")
            self.assertTrue(fresh._optics_unset("focal_mm"))
            # a header fills the empty boxes
            fresh._header_setup({"FOCALLEN": 2098, "XPIXSZ": 3.76})
            self.assertAlmostEqual(float(fresh.focal_mm.get()), 2098.0)
            self.assertAlmostEqual(float(fresh.pixel_um.get()), 3.76)
            # typing 2900 by hand is saved like any other value (no more "starting value" exception)
            saved = []
            fresh._save_settings = lambda *a, **k: saved.append(dict(fresh.settings))
            fresh.settings.pop("focal_mm", None)
            fresh.focal_mm.set(2900.0)
            fresh.update(); fresh.after(800); fresh.update()
            import time; time.sleep(0.8); fresh.update()
            self.assertEqual(fresh.settings.get("focal_mm"), 2900.0)
        finally:
            fresh.destroy()

    # ---- item 3 -----------------------------------------------------------------------------------------------
    def test_exoplanet_offer_has_no_status_button(self):
        app = self.app
        app.offer_upload("/tmp/AAVSO_TrES-3b_x.txt", "Open AAVSO Exoplanet Database…", ca.core.AAVSO_EXOPLANET_URL,
                         "the AAVSO Exoplanet Database (check its current instructions)", page_button=True)
        app.update()
        self.assertFalse(app.status_link.winfo_ismapped())
        self.assertEqual(app._status_offer, ("", ""))
        self.assertIn("with Open AAVSO Exoplanet Database… on the Results row", app.status.get())
        # WebObs keeps its status-bar button (unchanged in 2.3.2)
        app.offer_upload("/tmp/AAVSO_x.txt", "Open AAVSO WebObs…", ca.core.AAVSO_WEBOBS_URL, "AAVSO WebObs")
        self.assertEqual(app._status_offer[1], ca.core.AAVSO_WEBOBS_URL)

    def test_transit_ui_passes_page_button(self):
        import inspect
        import transit_ui
        self.assertIn("page_button=True", inspect.getsource(transit_ui))


if __name__ == "__main__":
    unittest.main()
