"""2.4.0 fit-in F1: #116 (UI-thread stall watch; Time of max/min Measure off the Tk thread), #109 (Text size),
#60 (no-flats sky-dominance caution, sky-ring report text), and the label nit."""
import math
import os
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
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


def obs(flux, sky, flag=""):
    return SimpleNamespace(flux_var=flux, sky=sky, flag=flag)


class TestSkyDominance(unittest.TestCase):
    def test_fraction(self):
        r = 4.0
        area = math.pi * r * r
        pts = [obs(1000.0, 100.0 / area), obs(1000.0, 100.0 / area), obs(5.0, 9e9, flag="sat")]
        self.assertAlmostEqual(core.target_light_fraction(pts, r), 1000.0 / 1100.0)
        self.assertTrue(math.isnan(core.target_light_fraction([], r)))
        self.assertTrue(math.isnan(core.target_light_fraction(pts, 0.0)))
        self.assertTrue(math.isnan(core.target_light_fraction([obs(-3.0, 1.0)], r)))

    def test_wasp43_like_night(self):
        """Joanna's WASP-43 b MOBs: the target ~6 % of its aperture's light, no flats, darks only."""
        r = 4.6
        sky_total = 1000.0 * 0.94 / 0.06
        pts = [obs(1000.0, sky_total / (math.pi * r * r)) for _ in range(30)]
        frac = core.target_light_fraction(pts, r)
        self.assertAlmostEqual(frac, 0.06, places=3)
        note = core.sky_dominated_note(frac, have_flat=False, sky_calibrated=True)
        self.assertTrue(note.startswith("⚠ No flats, and the sky dominates the target's aperture"), note)
        self.assertIn("only 6 % of the light", note)
        self.assertIn("take flats", note)

    def test_quiet_cases(self):
        f = core.sky_dominated_note
        self.assertEqual(f(0.06, have_flat=True, sky_calibrated=True), "")      # flats: no caution
        self.assertEqual(f(0.06, have_flat=False, sky_calibrated=False), "")    # sky still holds the pedestal
        self.assertEqual(f(0.25, have_flat=False, sky_calibrated=True), "")     # star bright enough
        self.assertEqual(f(float("nan"), have_flat=False, sky_calibrated=True), "")


class TestReportText(unittest.TestCase):
    def test_sky_ring_words(self):
        import inspect
        import transit_core
        src = inspect.getsource(transit_core)
        self.assertNotIn("a neighbor's light cannot make a transit deeper", src)
        self.assertIn("a neighbor's light in the sky ring", src)
        self.assertIn("a neighbor's light in the aperture \"\n                   \"cannot make a transit deeper", src)


class TestUIWatch(unittest.TestCase):
    def test_stall_logged_with_stack_then_recovery(self):
        now = [100.0]
        lines = []
        w = ui_watch.UIWatch(lines.append, main_thread_id=threading.get_ident(), clock=lambda: now[0])
        w.check()
        self.assertEqual(lines, [])
        now[0] += 3.5
        w.check()
        self.assertEqual(len(lines), 1)
        self.assertIn("UI thread busy for 3.5 s", lines[0])
        self.assertIn("test_stall_logged_with_stack_then_recovery", lines[0])   # the stack of the stuck thread
        w.check()
        self.assertEqual(len(lines), 1)                                         # said once
        now[0] += 7.0
        w.check()
        self.assertIn("still busy after 10.5 s", lines[1])
        now[0] += 1.0
        w.beat()
        self.assertIn("responding again after 11.5 s", lines[2])
        w.check()
        self.assertEqual(len(lines), 3)

    def test_thread_runs(self):
        lines = []
        w = ui_watch.UIWatch(lines.append, main_thread_id=threading.get_ident(), stall_s=0.2, long_s=99)
        old = ui_watch.CHECK_S
        ui_watch.CHECK_S = 0.05
        try:
            w.start()
            time.sleep(0.5)   # no beats: the "main" thread (this one) is stuck in sleep
        finally:
            w.stop()
            ui_watch.CHECK_S = old
        self.assertTrue(lines and "UI thread busy" in lines[0], lines)
        self.assertIn("time.sleep", lines[0])


@unittest.skipUnless(HAVE_TK, "needs a display")
class AppCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        cls.tmp = tempfile.TemporaryDirectory()
        cls.p = mock.patch.object(ca, "SETTINGS_FILE", os.path.join(cls.tmp.name, "s.json"))
        cls.p.start()
        cls.app = ca.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        cls.p.stop()
        cls.tmp.cleanup()


class TestTextSize(AppCase):
    def test_setting_values(self):
        f = self.ca.text_size_setting
        self.assertEqual(f({}), 100)
        self.assertEqual(f({"text_size": 130}), 130)
        self.assertEqual(f({"text_size": "115"}), 115)
        self.assertEqual(f({"text_size": 123}), 100)
        self.assertEqual(f({"text_size": "big"}), 100)
        self.assertEqual(f(None), 100)

    def test_apply_scales_tk(self):
        import tkinter as tk
        root = tk.Tk()
        try:
            root.withdraw()
            before = float(root.tk.call("tk", "scaling"))
            self.ca.apply_text_size(root, 100)
            self.assertAlmostEqual(float(root.tk.call("tk", "scaling")), before)
            self.ca.apply_text_size(root, 150)
            self.assertAlmostEqual(float(root.tk.call("tk", "scaling")), before * 1.5, delta=0.01 * before)   # Tk keeps whole dpi
        finally:
            root.destroy()

    def test_side_bar_choice_is_saved_and_offers_restart(self):
        app = self.app
        self.assertEqual(app.text_size_var.get(), "100 %")
        asked = []
        with mock.patch.object(self.ca.messagebox, "askyesno",
                               lambda *a, **k: asked.append(k.get("default")) or False), \
                mock.patch.object(app, "_restart_app") as restart:
            app.text_size_var.set("130 %")
            app._text_size_chosen()
            self.assertEqual(app.settings["text_size"], 130)
            self.assertEqual(asked, ["no"])            # Later is the default
            restart.assert_not_called()
            self.assertIn("applies the next time SHOBS-P starts", app.status.get())
            with open(self.ca.SETTINGS_FILE, encoding="utf-8") as fh:
                self.assertIn('"text_size": 130', fh.read())
            app._text_size_chosen()                    # same value again: nothing asked
            self.assertEqual(len(asked), 1)
        with mock.patch.object(self.ca.messagebox, "askyesno", lambda *a, **k: True), \
                mock.patch.object(app, "_restart_app") as restart:
            app.text_size_var.set("90 %")
            app._text_size_chosen()
            restart.assert_called_once()
        app.settings["text_size"] = 100


class TestLabel(AppCase):
    def test_line_up_label_names_recenters(self):
        texts = []

        def walk(widget):
            for c in widget.winfo_children():
                try:
                    texts.append(str(c.cget("text")))
                except Exception:
                    pass
                walk(c)
        walk(self.app)
        self.assertIn("Line up segments (nights, flips, re-centers)", texts)
        self.assertNotIn("Line up segments (nights, meridian flip)", texts)


class TestSkyDominatedHook(AppCase):
    def test_logs_once_without_flats(self):
        app = self.app
        r = 4.0
        pts = [SimpleNamespace(flux_var=100.0, sky=1000.0 / (math.pi * r * r), flag="") for _ in range(5)]
        app.radius.set(str(r))
        app.precalibrated = False
        lines = []
        with mock.patch.object(app, "log", lambda t, *a, **k: lines.append(t)):
            app.masters = core.Masters(dark=np.zeros((2, 2)))
            app._say_sky_dominated(pts)
            self.assertEqual(len(lines), 1)
            self.assertIn("only 9 % of the light", lines[0])
            app.masters = core.Masters(dark=np.zeros((2, 2)), flat=np.ones((2, 2)))
            app._say_sky_dominated(pts)
            app.masters = core.Masters()   # no bias, no dark: the sky holds the pedestal
            app._say_sky_dominated(pts)
            app.masters = None
            app._say_sky_dominated(pts)
        self.assertEqual(len(lines), 1)


class TestMeasureThreaded(AppCase):
    def test_measure_runs_off_the_tk_thread(self):
        """#116: Measure greys itself, the work runs on another thread, the result arrives through the UI queue."""
        ca = self.ca
        app = self.app
        seen = {}

        def fake_work(job):
            seen["thread"] = threading.current_thread() is not threading.main_thread()
            time.sleep(0.3)
            return [], ["no extremum inside the window"]

        win = SimpleNamespace()
        w = ca.ExtremumWindow.__new__(ca.ExtremumWindow)
        w.app = app
        w.win = app
        w.SYNC = False
        w.kind = ca.StringVar(value="maximum")
        w.window_min = ca.StringVar(value="")
        w.system = ca.StringVar(value="HJD")
        w.result_text = ca.StringVar(value="")
        w.measure_button = ca.ttk.Button(app)
        w.save_button = ca.ttk.Button(app)
        t = np.linspace(2461358.6, 2461358.9, 40)
        w._night_points = lambda: ("JD2461358", [SimpleNamespace(jd=x) for x in t], list(np.sin(t * 40)))
        w._elements = lambda: (None, None)
        w._noise_period = lambda: (None, "none", "")
        w._coords = lambda: (None, None, None, None)
        w.draw = lambda: None
        with mock.patch.object(ca.ExtremumWindow, "_measure_work", staticmethod(fake_work)), \
                mock.patch.object(ca, "grow_to_content", lambda *_a: None):
            w.measure()
            self.assertIn("disabled", w.measure_button.state())
            self.assertIn("Measuring the time of maximum", w.result_text.get())
            w.measure()   # a second press while measuring does nothing
            end = time.time() + 5
            while getattr(w, "_measuring", False) and time.time() < end:
                app.update()
                time.sleep(0.02)
        self.assertTrue(seen.get("thread"))
        self.assertFalse(w._measuring)
        self.assertNotIn("disabled", w.measure_button.state())
        self.assertIn("no extremum inside the window", w.result_text.get())
        del win


if __name__ == "__main__":
    unittest.main()
