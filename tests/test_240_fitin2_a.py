"""2.4.0 fit-in F2 builder A (#79 item 6 "grey out instead of scold" and tweaks): the busy lock on the main window's
job buttons (m003 and its twins), the Input page (m062 m096 m107 m110 m109 m034 m133 m140 m198 m636 m637), the
Calibrate/Photometry page (m292 m348 m284 x070 m350 x064 m349 x066 m555 m724 m745 m725 m728 m727) and Discovery
(m374 m277)."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import gate  # noqa: E402
import site_time  # noqa: E402

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


class TestZoneLookup(unittest.TestCase):
    """m133: the site's zone from latitude/longitude, and the fallback without the package."""

    def test_zone_at(self):
        if not site_time.zone_lookup_available():
            self.skipTest("tzfpy not installed")
        self.assertEqual(site_time.zone_at(31.68, -110.88), "America/Phoenix")
        self.assertEqual(site_time.zone_at(52.2, 21.0), "Europe/Warsaw")
        self.assertEqual(site_time.zone_at(35.37, 360.0 - 81.96), "America/New_York")   # 0-360 longitude
        self.assertEqual(site_time.zone_at("x", 3), "")
        self.assertEqual(site_time.zone_at(95, 0), "")

    def test_missing_package_is_silent(self):
        with mock.patch.object(site_time, "_FINDER", None), mock.patch.object(site_time, "_FINDER_MISSING", False), \
                mock.patch.dict(sys.modules, {"tzfpy": None}):
            self.assertEqual(site_time.zone_at(31.68, -110.88), "")
            self.assertFalse(site_time.zone_lookup_available())

    def test_requirements_and_setup(self):
        req = _src("requirements.txt")
        self.assertIn("tzfpy", [l.strip() for l in req.splitlines()])   # every platform (ready-made copies)
        self.assertNotIn("tzfpy", _src("shobs_check_python.py").split("PACKAGES = ")[1].split(")")[0])
        self.assertIn("tzfpy", _src("photometry_core.py"))   # credited
        self.assertIn("ODbL", _src("photometry_core.py"))


@unittest.skipUnless(HAVE_TK, "no display")
class TestApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        cls.tmp = tempfile.TemporaryDirectory()
        sf = os.path.join(cls.tmp.name, "s.json")
        with open(sf, "w") as fh:
            json.dump({}, fh)
        cls.p = mock.patch.object(ca, "SETTINGS_FILE", sf)
        cls.p.start()
        cls.app = ca.App()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        cls.p.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.app.busy = False
        self.app.gates.refresh()

    def greyed(self, w):
        return w.instate(["disabled"])

    # ---- m003: the busy lock -------------------------------------------------------------------------------------
    def test_busy_lock_greys_and_releases(self):
        app = self.app
        app.masters = object()   # Run photometry's own condition met
        app.radius.set(6.0)
        app.sky_in.set(10.0)
        app.sky_out.set(15.0)
        app.gates.refresh()
        self.assertFalse(self.greyed(app.run_button), app.gates.reason(app.run_button))
        self.assertTrue(app._start_job("test job"))
        try:
            for w in list(app.mode_buttons.values()) + [app.run_button, app.calibrate_button, app.new_reduction_button,
                                                        app.filter_box, app.star_entry, app.stack_button]:
                self.assertTrue(self.greyed(w), w)
            self.assertEqual(app.gates.reason(app.run_button), gate.BUSY_REASON)
            # a second job is refused without a pop-up (status-line backstop)
            with mock.patch.object(self.ca.messagebox, "showinfo") as box:
                self.assertFalse(app._start_job("second"))
                box.assert_not_called()
            self.assertIn("Another step is still running", app.status.get())
            with mock.patch.object(self.ca.messagebox, "showinfo") as box:
                app.set_mode("transits" if app.mode_key != "transits" else "variables")
                box.assert_not_called()
        finally:
            app._end_job()
        self.assertFalse(self.greyed(app.run_button))
        self.assertFalse(any(self.greyed(b) for b in app.mode_buttons.values()))
        app.masters = None
        app.gates.refresh()

    def test_readonly_combobox_stays_readonly(self):
        app = self.app
        box = app.filter_box
        self.assertTrue(box.instate(["readonly"]))
        app.busy = True
        app.gates.refresh()
        self.assertTrue(box.instate(["disabled"]))
        app.busy = False
        app.gates.refresh()
        self.assertFalse(box.instate(["disabled"]))
        self.assertTrue(box.instate(["readonly"]))

    def test_close_series_gate_and_tooltip_reason(self):
        app = self.app
        app.observations = []
        app.update_series_status()
        for b in (app.close_series_button, app.close_series_button_out):
            self.assertTrue(self.greyed(b))
            self.assertEqual(app.gates.reason(b), "No series is open.")

    # ---- Input page ----------------------------------------------------------------------------------------------
    def test_lookup_buttons_grey_without_a_name(self):
        app = self.app
        app.star_id.set("")
        app.planet_name.set("")
        app.gates.refresh()
        self.assertTrue(self.greyed(app.star_lookup_button))
        self.assertIn("Star ID", app.gates.reason(app.star_lookup_button))
        self.assertIn("TrES-3 b", app.gates.reason(app.planet_lookup_button))   # m062: the examples are kept
        with mock.patch.object(self.ca.messagebox, "showinfo") as box:
            app.lookup_star()
            app.lookup_planet()
            box.assert_not_called()

    def test_color_images_grey_the_pattern_and_list_four_channels(self):
        app = self.app
        app._rgb_channel = "green"
        app._sync_filter_choices()
        app.gates.refresh()
        try:
            self.assertTrue(self.greyed(app.pattern_box))
            self.assertIn("Filter box", app.gates.reason(app.pattern_box))
            values = list(app.filter_box.cget("values"))
            self.assertEqual(len(values), 4)
            self.assertIn("Clear / L (CV)", values)
            self.assertNotIn("V (Johnson V)", values)
        finally:
            app._leave_rgb_lights()
            app.gates.refresh()
        self.assertFalse(self.greyed(app.pattern_box))
        self.assertIn("V (Johnson V)", list(app.filter_box.cget("values")))

    def test_apply_channel_greyed_until_calibrated(self):
        app = self.app
        app.calibrated = None
        app.gates.refresh()
        self.assertTrue(self.greyed(app.apply_button))
        with mock.patch.object(self.ca.messagebox, "showinfo") as box:
            app.apply_debayer()
            box.assert_not_called()

    def test_output_folder_checked_without_creating(self):
        app = self.app
        with tempfile.TemporaryDirectory() as d:
            new = os.path.join(d, "a", "b")
            self.assertTrue(app.output_folder_usable(new))
            self.assertFalse(os.path.exists(new))   # nothing created
            f = os.path.join(d, "file.txt")
            open(f, "w").close()
            self.assertFalse(app.output_folder_usable(f))
            self.assertFalse(app.output_folder_usable(os.path.join(f, "under")))
            app.output_dir.set(f)
            with mock.patch.object(self.ca.messagebox, "showwarning") as box:
                self.assertEqual(app._out_folder(), "")
                box.assert_not_called()
            self.assertEqual(app._counts["Output"].get(), "cannot be used")
            self.assertIn("cannot be used", app.status.get())
            app.output_dir.set("")

    def test_m133_zone_from_the_frames_site(self):
        app = self.app
        app.site_tz.set("Europe/Warsaw")
        if site_time.zone_lookup_available():
            self.assertTrue(app._zone_from_site(31.68, -110.88, "the frames' site"))
            self.assertEqual(app.site_tz.get(), "America/Phoenix")
        with mock.patch.object(site_time, "zone_at", return_value=""), \
                mock.patch.object(site_time, "zone_lookup_available", return_value=False):
            app.site_tz.set("Europe/Warsaw")
            self.assertFalse(app._zone_from_site(31.68, -110.88, "the frames' site"))
            self.assertEqual(app.site_tz.get(), "Europe/Warsaw")   # fallback: unchanged, as before
        app.site_tz.set("")

    def test_m140_known_telescope_set_without_a_question(self):
        app = self.app
        app.aperture_mm.set("152")
        app.settings["aperture_scopes"] = [{"telescope": "NexStar 8SE", "camera": "ASI2600MM", "focal_mm": 2098.0,
                                            "aperture_mm": 203.0}]
        try:
            with mock.patch.object(self.ca.messagebox, "askyesno") as ask:
                app._check_aperture_scope({"TELESCOP": "NexStar 8SE", "INSTRUME": "ASI2600MM", "FOCALLEN": 2100})
                ask.assert_not_called()
            self.assertEqual(app.aperture_mm.get(), "203")
        finally:
            app.settings.pop("aperture_scopes", None)

    def test_export_setup_buttons(self):
        app = self.app
        with mock.patch.object(self.ca, "load_export_setups", return_value={"Mine": {"time": "BJD_TDB"}}):
            w = self.ca.ExportWindow(app)
        try:
            w.setup_name.set("")
            app.gates.refresh()
            self.assertTrue(self.greyed(w.load_setup_button))
            self.assertTrue(self.greyed(w.save_setup_button))
            self.assertTrue(self.greyed(w.delete_setup_button))
            w.setup_name.set("Mine")
            app.gates.refresh()
            self.assertFalse(self.greyed(w.load_setup_button))
            self.assertFalse(self.greyed(w.save_setup_button))
            w.setup_name.set("New one")
            app.gates.refresh()
            self.assertTrue(self.greyed(w.load_setup_button))
            self.assertFalse(self.greyed(w.save_setup_button))
        finally:
            w.win.destroy()

    # ---- Calibrate / Photometry ------------------------------------------------------------------------------------
    def test_solve_scan_gated_until_calibrated(self):
        app = self.app
        app.preview, app.preview_is_debayered, app.masters = None, False, None
        app.gates.refresh()
        for b in (app.label_button, app.scan_button, app.run_button):
            self.assertTrue(self.greyed(b))
            self.assertIn("alibrate", app.gates.reason(b))
        with mock.patch.object(self.ca.messagebox, "showinfo") as box:
            app.label_aavso()
            app.scan_field()
            box.assert_not_called()

    def test_x070_aperture_boxes(self):
        app = self.app
        app.masters = object()
        try:
            app.radius.set(9.0)
            app.sky_in.set(8.0)
            app.sky_out.set(15.0)
            app.gates.refresh()
            self.assertTrue(self.greyed(app.run_button))
            self.assertIn("in order", app.gates.reason(app.run_button))
            self.assertEqual(str(app._aperture_entries["sky in"].cget("style")), "Invalid.TEntry")
            app.sky_in.set(12.0)
            app.gates.refresh()
            self.assertFalse(self.greyed(app.run_button))
            self.assertEqual(str(app._aperture_entries["sky in"].cget("style")), "TEntry")
            app.sky_out._tk.globalsetvar(app.sky_out._name, "abc")   # not a number (set on the variable's own interpreter)
            reason, bad = app._aperture_problems()
            self.assertEqual(bad, {"sky out"})
            self.assertIn("sky out is not a number", reason)
            app.sky_out.set(15.0)
        finally:
            app.masters = None
            app.gates.refresh()

    def test_x064_binning_change_greys_until_set_back(self):
        # review S5: the masters are kept, marked with their bin; Run / Scan grey while Binning differs, live again
        # when it is set back
        app = self.app
        app.binning.set(1)
        masters = object()
        app.masters, app.masters_binning, app.calibrated = masters, 1, np.zeros((4, 4))
        why = "The masters were built at bin 1: Calibrate again (or set Binning back to 1)."
        try:
            app.binning.set(2)
            self.assertIs(app.masters, masters)
            self.assertEqual(app.masters_binning, 1)   # the image on the page is still at bin 1
            self.assertIn("Calibrate again to rebuild them at bin 2, or set Binning back to 1", app.status.get())
            self.assertTrue(self.greyed(app.run_button))
            self.assertEqual(app.gates.reason(app.run_button), why)
            self.assertEqual(app._bin_mismatch_why(), why)
            with mock.patch.object(app, "_stack_source_live", return_value={"night": "JD1"}):
                self.assertEqual(app._stack_why(), why)   # Stack too
            app.binning.set(1)
            self.assertIs(app.masters, masters)
            self.assertEqual(app._bin_mismatch_why(), "")
            self.assertIn("Binning is back at 1", app.status.get())
            self.assertNotEqual(app.gates.reason(app.run_button), why)
            # the Binning box is on the busy lock (readonly kept)
            app.busy = True
            app.gates.refresh()
            self.assertTrue(app._binning_box.instate(["disabled"]))
            app.busy = False
            app.gates.refresh()
            self.assertFalse(app._binning_box.instate(["disabled"]))
            self.assertTrue(app._binning_box.instate(["readonly"]))
        finally:
            app.busy = False
            app.masters, app.masters_binning, app.calibrated = None, None, None
            app.binning.set(1)

    def test_m555_one_button_and_nothing_until_pressed(self):
        app = self.app
        with mock.patch.object(app, "measure_skipped_frames") as measure:
            app.status.set("Comps in use: C1, C2.  4 skipped frames can be measured now:")
            app._offer_measure_skipped("2460000", 4)
            app.update()
            self.assertTrue(app.status_link.winfo_ismapped() or app.status_link.winfo_manager())
            self.assertEqual(str(app.status_link.cget("text")), "Measure skipped frames")
            measure.assert_not_called()            # nothing changes until it is pressed
            app.status.set("Something else")        # an action offer stays with a new status line
            self.assertTrue(app.status_link.winfo_manager())
            app.busy = True
            app.gates.refresh()
            self.assertTrue(self.greyed(app.status_link))
            app.busy = False
            app.gates.refresh()
            app.status_link.invoke()
            measure.assert_called_once()
            self.assertFalse(app.status_link.winfo_manager())

    def test_m555_lines_plain_when_measurable(self):
        app = self.app
        with mock.patch.object(app, "_latest_night", return_value="N1"), \
                mock.patch.object(app, "recoverable_skips", return_value={"C3": ["/a.fits", "/b.fits"]}), \
                mock.patch.object(app, "_night_info", return_value={}), \
                mock.patch.object(app, "_skips_measurable", return_value=(["/a.fits", "/b.fits"], [])):
            text = app.comp_skip_text("N1")
        self.assertIn("2 frames were skipped because of C3, which is no longer a comp: Measure skipped frames", text)
        self.assertNotIn("⚠", text)
        with mock.patch.object(app, "_latest_night", return_value="N1"), \
                mock.patch.object(app, "recoverable_skips", return_value={"C3": ["/a.fits"]}), \
                mock.patch.object(app, "_night_info", return_value={}), \
                mock.patch.object(app, "_skips_measurable", return_value=([], ["/a.fits"])):
            text = app.comp_skip_text("N1")
        self.assertTrue(text.startswith("⚠ 1 frame was skipped"), text)

    def test_m727_spinbox_and_stack_gate(self):
        app = self.app
        self.assertEqual(app.stack_n_box.winfo_class(), "TSpinbox")
        self.assertEqual(float(app.stack_n_box.cget("from")), 2.0)
        app.gates.refresh()
        self.assertTrue(self.greyed(app.stack_button))   # no run yet (m725)
        self.assertIn("Run photometry first", app.gates.reason(app.stack_button))
        self.assertTrue(self.greyed(app.points_stack_button))
        with mock.patch.object(self.ca.messagebox, "showinfo") as box, \
                mock.patch.object(self.ca.messagebox, "showerror") as err:
            app.start_stack()
            box.assert_not_called()
            err.assert_not_called()

    def test_m284_add_star_greyed(self):
        app = self.app
        app.preview, app.preview_is_debayered = None, False
        with mock.patch.object(self.ca.messagebox, "showinfo") as box:
            app.add_watch_dialog()
            box.assert_not_called()

    # ---- Discovery ----------------------------------------------------------------------------------------------
    def test_m374_placeholder_buttons_greyed(self):
        app = self.app
        app._discovery_placeholder()
        app.update()
        found = []

        def walk(w):
            for k in w.winfo_children():
                if k.winfo_class() == "TButton" and str(k.cget("text")) in (
                        "Save plot PNG…", "Save CSV…", "Export suspects CSV…", "Export all light curves CSV…"):
                    found.append(k)
                walk(k)
        walk(app.discovery_host)
        self.assertEqual(len(found), 4)
        for b in found:
            self.assertTrue(self.greyed(b))
            self.assertIn("Scan the field first", app.gates.reason(b))

    def test_m277_position_stored_after_the_solve(self):
        app = self.app
        app.watch_list.append({"name": "watch 9 (10,20)"})
        app.watch_xy["watch 9 (10,20)"] = (10.0, 20.0)
        app._watch_click_token = {"watch 9 (10,20)": getattr(app, "_preview_token", 0)}
        app.chart_solution = {"target_xy": (0.0, 0.0), "scale": 1.0, "angle": 0.0, "ra_deg": 10.0, "dec_deg": 20.0}
        try:
            with mock.patch.object(self.ca.core, "pixel_to_sky", return_value=(10.5, 20.25)):
                app._store_watch_positions()
            entry = app.watch_list[-1]
            self.assertEqual((entry["ra"], entry["dec"]), (10.5, 20.25))
        finally:
            app.watch_list.pop()
            app.watch_xy.pop("watch 9 (10,20)", None)
            app.chart_solution = None


if __name__ == "__main__":
    unittest.main()
