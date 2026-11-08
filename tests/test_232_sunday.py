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
        out = tp.free_shape_runaway({"p": {"a_rs": 6.0, "b": 0.9}, "err": {"a_rs": 2.5, "b": 0.05}, "t14": None},
                                    {"a_rs": 6.0, "b": 0.2, "t14": None})
        self.assertIn("a_rs", out)   # its own error is 40 % of it
        self.assertIn("b", out)      # 0.7 from the reference
        self.assertEqual(tp.free_shape_runaway({}, {}), {})

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


if __name__ == "__main__":
    unittest.main()
