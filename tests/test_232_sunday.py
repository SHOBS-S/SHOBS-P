"""2.3.2 Sunday items (UI/files only; numbers frozen at 2.3.0): 4, 6, 7, 8, 10, 13, 15."""
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


def _have_tk():
    try:
        import tkinter as tk
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


class TestPlotBins(unittest.TestCase):
    """item 6: Plot bins drop-down (display only), >= 3 frames a bin, the legend names the real width."""

    def test_widths(self):
        import transit_ui as tu
        self.assertAlmostEqual(tu.plot_bin_width_h("Auto", 33 / 3600), 10 / 60)       # John's 33 s cadence
        self.assertAlmostEqual(tu.plot_bin_width_h("Auto", 4.4 / 60), 13.2 / 60)       # MicroObservatory
        for choice, minutes in (("5 min", 5), ("10 min", 10), ("15 min", 15), ("30 min", 30)):
            self.assertAlmostEqual(tu.plot_bin_width_h(choice, 4.4 / 60), minutes / 60)
        self.assertEqual(tu.plot_bin_label(13.2 / 60), "13-minute bins")
        self.assertEqual(tu.plot_bin_label(10 / 60), "10-minute bins")
        self.assertEqual(tu.PLOT_BIN_CHOICES, ("Auto", "5 min", "10 min", "15 min", "30 min"))

    def test_bin_needs_three_frames(self):
        import numpy as np
        import transit_ui as tu
        x = np.array([0.0, 0.01, 0.02, 0.5, 0.51, 1.0, 1.01, 1.02, 1.03])
        y = np.ones_like(x)
        bins = tu._bin(x, y, 0.2)
        self.assertEqual(len(bins), 2)   # the 2-frame bin at 0.5 h is left out
        self.assertTrue(all(abs(b[0] - 0.5) > 0.1 for b in bins))


class TestFreeShapeRunaway(unittest.TestCase):
    """item 7: a/R*, b and T14 of the free shape say 'not constrained' when they ran away."""

    def test_joanna_wasp35(self):
        import transit_posterior as tp
        ff = {"p": {"k": 0.114, "a_rs": 5.68, "b": 0.3}, "err": {"k": 0.004, "a_rs": 0.6, "b": 0.2},
              "t14": 4.137 / 24}
        ref = {"a_rs": 8.53, "b": 0.3, "t14": 3.143 / 24}
        out = tp.free_shape_runaway(ff, ref)
        self.assertIn("a_rs", out)
        self.assertIn("t14", out)
        self.assertNotIn("b", out)

    def test_good_fit_passes(self):
        import transit_posterior as tp
        ff = {"p": {"k": 0.15, "a_rs": 6.0, "b": 0.12}, "err": {"a_rs": 0.3, "b": 0.1}, "t14": 3.1 / 24}
        self.assertEqual(tp.free_shape_runaway(ff, {"a_rs": 6.05, "b": 0.1, "t14": 3.11 / 24}), {})

    def test_b_and_errors(self):
        import transit_posterior as tp
        out = tp.free_shape_runaway({"p": {"a_rs": 6.0, "b": 0.9}, "err": {"a_rs": 3.5, "b": 0.05}, "t14": None},
                                    {"a_rs": 6.0, "b": 0.2, "t14": None})
        self.assertIn("a_rs", out)   # its own error is 58 % of it
        self.assertIn("b", out)      # 0.7 from the reference
        # TOI-2046 b 20-21 Oct (John, 8 Nov): a wide b error alone is not a runaway
        good = tp.free_shape_runaway({"p": {"a_rs": 5.31, "b": 0.27}, "err": {"a_rs": 1.4, "b": 0.45},
                                      "t14": 2.428 / 24}, {"a_rs": 4.75, "b": 0.51, "t14": 2.41 / 24})
        self.assertEqual(good, {})
        self.assertEqual(tp.free_shape_runaway({}, {}), {})

    def test_b_without_finite_error_and_numpy_scalars(self):
        import numpy as np
        import transit_posterior as tp
        got = tp.free_shape_runaway({"p": {"b": 0.4}, "err": {"b": float("nan")}}, {})
        self.assertEqual(set(got), {"b"})
        got = tp.free_shape_runaway({"p": {"a_rs": np.float32(5.68)}, "err": {"a_rs": np.float32(0.3)},
                                     "t14": np.float32(4.137 / 24)}, {"a_rs": 8.53, "t14": 3.143 / 24})
        self.assertEqual(set(got), {"a_rs", "t14"})

    def test_reference_missing_skips_comparison(self):
        import transit_posterior as tp
        ff = {"p": {"a_rs": 5.0, "b": 0.5}, "err": {}, "t14": 0.2}
        self.assertEqual(tp.free_shape_runaway(ff, {"a_rs": None, "b": float("nan"), "t14": None}), {})


class TestPierSides(unittest.TestCase):
    """item 8: camera angles within 10 degrees are one pier side (HD 219134 6-7 Nov: 37-38 deg vs 30 deg)."""

    def test_37_joins_30(self):
        import photometry_core as core
        self.assertEqual(core.PIER_ANGLE_TOL, 10.0)
        g = core.pier_side_groups([30, 30, 30, 210, 210, 37, 38, 217])
        self.assertEqual({g[a] for a in (30, 37, 38)}, {30})
        self.assertEqual({g[a] for a in (210, 217)}, {210})
        out = core.group_pier_sides({"a": {"source": "ROTATOR", "before": "30°", "after": "210°"},
                                     "b": {"source": "ROTATOR", "before": "30°", "after": "210°"},
                                     "c": {"source": "ROTATOR", "before": "38°", "after": ""}})
        self.assertEqual(out["c"]["before"], "30°")
        self.assertEqual(out["c"]["read"]["before"], "38°")

    def test_far_angles_stay_apart(self):
        import photometry_core as core
        g = core.pier_side_groups([30, 30, 30, 60, 60])
        self.assertNotEqual(g[30], g[60])   # a real camera turn (30 degrees) is another side


class TestWindowFits(unittest.TestCase):
    """item 13: the main window opens inside the usable screen (Mac: menu bar and Dock)."""

    def test_mac_imac(self):
        for sw, sh in ((1920, 1080), (2048, 1152), (1440, 900), (1280, 800)):
            w, h, x, y, mw, mh = ca.main_window_geometry(sw, sh, "darwin")
            self.assertGreaterEqual(y, ca.MAC_MENU_BAR)
            self.assertLessEqual(y + ca.MAC_TITLE_BAR + h, sh - ca.MAC_DOCK_ROOM)
            self.assertGreaterEqual(x, 0)
            self.assertLessEqual(x + w, sw)
            self.assertLessEqual(mw, w)
            self.assertLessEqual(mh, h)

    def test_windows_unchanged_on_a_normal_screen(self):
        self.assertEqual(ca.main_window_geometry(1920, 1080, "win32")[:4], (1536, 864, 192, 88))

    def test_fit_on_screen(self):
        self.assertEqual(ca.fit_on_screen(1700, 1000, -30, 10, 1440, 900, "darwin"),
                         (1440, 900 - 25 - 28 - 80, 0, 25))
        self.assertEqual(ca.fit_on_screen(800, 600, 100, 100, 1920, 1080, "darwin"), (800, 600, 100, 100))


class TestMacApp(unittest.TestCase):
    """item 15: Create shortcut.command builds SHOBS-P.app (run here without sips/iconutil: no icon)."""

    def test_builds_the_app(self):
        import plistlib
        import shutil
        import subprocess
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("needs bash")
        repo = os.path.dirname(HERE)
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HOME=d, SHOBS_APP_DEST=os.path.join(d, "Apps"), SHOBS_DESKTOP_DIR=d)
            r = subprocess.run([bash, os.path.join(repo, "Create shortcut.command")], env=env, capture_output=True,
                               text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            app = os.path.join(d, "Apps", "SHOBS-P.app", "Contents")
            with open(os.path.join(app, "Info.plist"), "rb") as fh:
                info = plistlib.load(fh)
            self.assertEqual(info["CFBundleExecutable"], "SHOBS-P")
            self.assertEqual(info["CFBundleShortVersionString"], ca.APP_VERSION)
            exe = os.path.join(app, "MacOS", "SHOBS-P")
            self.assertTrue(os.access(exe, os.X_OK))
            text = open(exe, encoding="utf-8").read()
            self.assertIn(f"APP_DIR='{os.path.realpath(repo)}'", text)
            self.assertIn("Start SHOBS-P.command", text)
            # run-through (iMac): macOS refused the hidden start in ~/Downloads ("Operation not permitted"), so
            # every start goes through Terminal; the window goes to the Dock (shobs_setup.sh) and the old log is gone
            self.assertNotIn("shobs_setup.sh run", text)
            self.assertNotIn("app_start_log", text)
            self.assertIn("app_minimize", text)
            self.assertEqual(subprocess.run([bash, "-n", exe]).returncode, 0)
            self.assertTrue(os.path.islink(os.path.join(d, "SHOBS-P")))
            # run again (the folder moved, say): written over, still one app
            r = subprocess.run([bash, os.path.join(repo, "Create shortcut.command")], env=env, capture_output=True,
                               text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_setup_docks_the_terminal_only_when_the_app_asked(self):
        repo = os.path.dirname(HERE)
        text = open(os.path.join(repo, "shobs_setup.sh"), encoding="utf-8").read()
        run = text[text.index("    run)\n"):text.index("    selftest)\n")]
        self.assertIn("app_minimize", run)
        self.assertIn("\\033[2t", run)       # to the Dock
        self.assertIn("\\033[1t", run)       # back up on an error
        self.assertIn("-le 120", run)           # a left-over note is not used
        self.assertIn('[ "$MAC" = 1 ]', run)

    def test_in_the_release_zip(self):
        import zipfile
        import subprocess
        repo = os.path.dirname(HERE)
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "z.zip")
            r = subprocess.run([sys.executable, os.path.join(repo, "tools", "make_release_zip.py"), out, "--src", repo],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with zipfile.ZipFile(out) as z:
                info = [i for i in z.infolist() if i.filename.endswith("/Create shortcut.command")]
                self.assertEqual(len(info), 1)
                self.assertEqual((info[0].external_attr >> 16) & 0o777, 0o755)


@unittest.skipUnless(_have_tk(), "needs a display")
class TestInApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test232s.log")
        with mock.patch.object(ca.App, "_save_settings", lambda *a, **k: None):
            cls.app = ca.App()
        cls.app.withdraw()
        cls.app._save_settings = lambda *a, **k: None
        cls.patches = [mock.patch.object(ca.messagebox, n, return_value=True)
                       for n in ("showwarning", "showinfo", "showerror", "askyesno")]
        for p in cls.patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.app.destroy()

    # ---- item 4: Calibrate runs the Scan folders checks when the Lights folder was not scanned ----------------
    def test_calibrate_scans_unscanned_lights_first(self):
        app = self.app
        with tempfile.TemporaryDirectory() as d:
            app.light_dir.set(d)
            app._scanned_lights = None   # a restart: the box is filled in, never scanned
            with mock.patch.object(app, "scan_folders") as scan:
                app.run_calibration()   # no lights: stops at "No light frames found."
                scan.assert_called_once()
            app._scanned_lights = d
            with mock.patch.object(app, "scan_folders") as scan:
                app.run_calibration()
                scan.assert_not_called()

    def test_scan_folders_records_the_lights_folder(self):
        app = self.app
        with tempfile.TemporaryDirectory() as d:
            app.light_dir.set(d)
            app.scan_folders()
            self.assertEqual(app._scanned_lights, d)

    # ---- items 6 and 7 on the Transit fit page --------------------------------------------------------------
    def _tp(self):
        tp = getattr(self.app, "transit_page", None) or getattr(self.app, "transit", None)
        if tp is None:
            self.skipTest("no transit page")
        return tp

    def test_plot_bins_box_redraws_without_refit(self):
        tp = self._tp()
        self.assertEqual(tp.plot_bins.get(), "Auto")
        with mock.patch.object(tp, "draw") as draw, mock.patch.object(tp, "fit") as fit:
            tp.result = {"x": 1}
            tp.plot_bins.set("15 min")
            tp._plot_bins_changed()
            draw.assert_called_once_with()
            fit.assert_not_called()
            self.assertAlmostEqual(tp._plot_bin_width(0.01), 0.25)
            tp.result = None
            tp._last_preview = ("t", "f", "note")
            draw.reset_mock()
            tp._plot_bins_changed()
            draw.assert_called_once_with(preview=("t", "f", "note"))
        tp.plot_bins.set("Auto")
        tp._last_preview = None

    def test_table_says_not_constrained_for_runaway_shape(self):
        tp = self._tp()
        r = {"summary": {"a_rs": [8.4, 0.2], "b": [0.25, 0.1], "t14": [3.14 / 24, 0.01], "k": [0.114, 0.002],
                         "depth": [0.013, 0.0005]},
             "free_fit": {"p": {"k": 0.114, "a_rs": 5.68, "b": 0.3}, "err": {"a_rs": 0.6, "b": 0.2},
                          "t14": 4.137 / 24, "unconstrained": ""}}
        planet = {"name": "WASP-35 b", "a_rs": 8.53, "b": 0.3, "t14": 3.143 / 24}
        got = tp._free_runaway(r, planet, r["summary"])
        self.assertEqual(set(got), {"a_rs", "t14"})

    # ---- item 10: Photometry image room; corner plot named with its fit's set number ----------------------
    def test_zoom_tip_line_gone(self):
        self.assertFalse(hasattr(self.app, "step_zoom_tip"))
        import inspect
        self.assertNotIn("Pop out for a bigger window", inspect.getsource(ca))
        self.assertEqual(str(self.app.step_hide_button.cget("style")), "Compact.TButton")

    def test_photometry_layout_item10(self):
        """2.3.2 item 10 (review S6): legend column left of the image, no Small image row, the Stack /
        Measured-with / skipped lines in the column beside the View box, the column packed after the boxes."""
        import inspect
        app = self.app
        self.assertEqual(app._legend_page_row.pack_info()["side"], "left")
        self.assertFalse(hasattr(app, "photo_small_row"))
        self.assertNotIn("photo_small_hint", inspect.getsource(ca))
        app._stack_shown = True
        app._aperture_state_shown = True
        app._skip_line_shown = True
        try:
            app._layout_meas_col()
            app._stack_place_points_box()
            for w in (app._stack_row, app.aperture_state_label, app.skip_line):
                self.assertIs(w.pack_info()["in"], app._meas_col)
            self.assertEqual(app._meas_col.pack_slaves()[1:], [app._stack_row, app.aperture_state_label,
                                                              app.skip_line])
            self.assertIs(app._view_options.pack_slaves()[-1], app._meas_col)
        finally:
            app._stack_shown = app._aperture_state_shown = app._skip_line_shown = False
            app._layout_meas_col()
        self.assertEqual(app._meas_col.pack_slaves()[1:], [])

    def test_status_lines_follow_view_box_own_line(self):
        """2.3.2 (John, run-through; review M1/S2/S3): when the View box drops to its own line, the Stack /
        Measured-with lines go on that line, left of the Points / View boxes, only when the Stack controls fit there
        whole; otherwise (and when the View box is back beside the settings) they stay in the column."""
        app = self.app
        app._stack_shown = True
        app._aperture_state_shown = True
        opts = app._view_options

        def layout(width, one_line):
            app.update_idletasks()   # real requested sizes (the boxes report 1 px before their first layout)
            with mock.patch.object(opts, "winfo_width", return_value=width), \
                    mock.patch.object(app, "_meas_one_line", return_value=one_line):
                app._layout_view_box(force=True)
                app._layout_meas_col()
                app._stack_place_points_box()

        try:
            # settings too wide for the boxes beside them, a wide line: the lines go beside the boxes
            layout(4000, 5000)
            self.assertTrue(app._view_own_line)
            self.assertTrue(app._meas_side_ok)
            for w in (app._stack_row, app.aperture_state_label):
                self.assertIs(w.pack_info()["in"], app._meas_side)
            self.assertEqual(app._meas_side.pack_slaves(), [app._stack_row, app.aperture_state_label])
            self.assertEqual(app._meas_col.pack_slaves()[1:], [])
            self.assertEqual(app._view_line2.pack_slaves(), [app._view_box, app._meas_side])
            # the Points box rides on the Stack row, right after the Stack button
            self.assertIs(app._points_box.master, app._stack_ctl)
            self.assertEqual(app._stack_ctl.pack_slaves()[-2:], [app.stack_button, app._points_box])
            # the side's share holds the Stack controls whole
            need = app._meas_side_need(ca.ui_scale(app))
            self.assertGreaterEqual(need, app._stack_ctl.winfo_reqwidth())
            # a narrow line: no room for the Stack controls beside the boxes, so the lines stay in the column
            layout(900, 5000)
            self.assertTrue(app._view_own_line)
            self.assertFalse(app._meas_side_ok)
            self.assertEqual(app._meas_col.pack_slaves()[1:], [app._stack_row, app.aperture_state_label])
            self.assertEqual(app._meas_side.pack_slaves(), [])
            # the View box back beside the settings: the column again
            layout(4000, 100)
            self.assertFalse(app._view_own_line)
            self.assertFalse(app._meas_side_ok)
            self.assertEqual(app._meas_col.pack_slaves()[1:], [app._stack_row, app.aperture_state_label])
            # nothing shown: the side needs no room
            app._stack_shown = app._aperture_state_shown = False
            self.assertEqual(app._meas_side_need(1.0), 0)
        finally:
            app._stack_shown = app._aperture_state_shown = False
            app._layout_view_box(force=True)
            app._layout_meas_col()

    def test_view_box_width_ignores_wrapped_column(self):
        """review S1: the View box layout counts the column's one-line width, not its wrapped (self-sized) width."""
        app = self.app
        app.stack_line.set("x " * 200)   # a long suggestion line, wrapped at the column's width
        app._stack_shown = True
        try:
            app._layout_meas_col()
            app.update_idletasks()
            with mock.patch.object(app._meas_col, "winfo_reqwidth", return_value=5000) as req:
                app._layout_view_box(force=True)
                req.assert_not_called()   # the column's wrapped width never feeds the layout
        finally:
            app._stack_shown = False
            app.stack_line.set("")
            app._layout_meas_col()

    def test_corner_name_follows_the_set(self):
        tp = self._tp()
        app = self.app
        with tempfile.TemporaryDirectory() as d:
            app.output_dir.set(d)
            res = {"_serial": 4242, "planet": {"name": "TrES-1 b"}}
            with mock.patch.object(tp, "family_names", return_value=["AAVSO_TrES-1b.txt", "TrES-1b_transit.png",
                                                                     "TrES-1b_transit_fit.csv"]), \
                    mock.patch.object(app, "_results_key", return_value=("k", 1)):
                tp.result = res
                app._save_set = {"key": (("k", 1), os.path.normcase(os.path.abspath(d))), "k": 2, "used": {"x"}}
                path, how = tp.corner_save_target(True)
                self.assertEqual(os.path.basename(path), "TrES-1b_transit_2_corner.png")
                self.assertEqual(how, "new")
                # Save plot PNG wrote that file (all parameters) for this fit: the window's view replaces it
                open(path, "w").close()
                tp._corner_written(path, False)
                self.assertEqual(tp.corner_save_target(True), (path, "replace"))
                self.assertEqual(tp.corner_save_target(False), (path, "same"))
                # an older file of another fit is never written over
                tp.result = {"_serial": 99}
                p2, how2 = tp.corner_save_target(True)
                self.assertEqual(os.path.basename(p2), "TrES-1b_transit_2_corner_2.png")
                self.assertEqual(how2, "new")
            tp.result = None
            app._save_set = None
            app.output_dir.set("")

    def test_corner_window_first_then_plot_png_one_set(self):
        """review M2: corner window Save first, then Save plot PNG: one set, one corner file."""
        import transit_ui as tu
        tp = self._tp()
        app = self.app
        with tempfile.TemporaryDirectory() as d:
            app.output_dir.set(d)
            app._save_set = None
            tp.result = {"_serial": 77, "chain": {"flat": 1}}
            names = ["AAVSO_X.txt", "X_transit.png", "X_transit_fit.csv"]
            touch = lambda p, *a, **k: open(p, "w").close()  # noqa: E731
            with mock.patch.object(tp, "family_names", return_value=names), \
                    mock.patch.object(app, "_family_names", return_value=["X_lightcurve.csv"] + names), \
                    mock.patch.object(app, "_results_key", return_value=("k",)), \
                    mock.patch.object(tu.tpost, "save_corner", side_effect=lambda r, p, **k: touch(p)), \
                    mock.patch.object(tp, "_write_png", side_effect=touch), \
                    mock.patch.object(tp, "_still_checking", return_value=False), \
                    mock.patch.object(app, "saved_notice"):
                w = type("W", (), {})()
                w.page, w.app, w.result, w.win = tp, app, tp.result, app
                w._shape_only = lambda: True
                tu.CornerWindow.save(w)
                tp.save_png()
                self.assertEqual(sorted(os.listdir(d)), ["X_transit.png", "X_transit_corner.png"])
            tp.result = None
            app._save_set = None
            app.output_dir.set("")


if __name__ == "__main__":
    unittest.main()
