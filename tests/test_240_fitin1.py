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
        flat = "".join(src.split())   # the words whatever the line breaks and indentation
        self.assertIn("aneighbor'slightintheaperture\"\"cannotmakeatransitdeeper", flat)
        self.assertIn("canmaketheskyestimatetoobright", flat.replace('\"\"', ""))


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

    def test_sleep_is_not_a_stall(self):
        """Fable S4: the computer slept an hour; the watch thread wakes before the Tk thread beats."""
        now = [100.0]
        lines = []
        w = ui_watch.UIWatch(lines.append, main_thread_id=threading.get_ident(), clock=lambda: now[0])
        w.check()
        now[0] += 3600.0
        w.check()
        self.assertEqual(len(lines), 1)
        self.assertIn("UI watch was held 3600 s itself", lines[0])   # one plain line, not a stall
        now[0] += 0.5
        w.check()
        self.assertEqual(len(lines), 1)
        now[0] += 3.5
        w.check()                    # a real stall after the wake is still seen
        self.assertIn("UI thread busy", lines[1])

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
            app.text_size_var.set("100 %")   # Fable N2: back to the size on screen: no restart offered
            app._text_size_chosen()
            restart.assert_called_once()
            self.assertIn("the size SHOBS-P is showing now", app.status.get())
        app.settings["text_size"] = 100

    def test_restart_saves_first_then_replaces_the_process(self):
        """Fable M2: the close button's series offer and the pending saves come before the new SHOBS-P."""
        app = self.app
        order = []
        with mock.patch.object(app, "_offer_series_save", lambda *_a: order.append("offer") or True), \
                mock.patch.object(app, "_finish_saves_on_close", lambda: order.append("saves")), \
                mock.patch.object(app, "destroy", lambda: order.append("destroy")), \
                mock.patch.object(self.ca.os, "execv", lambda *a: order.append("execv")), \
                mock.patch.object(self.ca.os, "chdir", lambda *_a: None), \
                mock.patch.object(app, "series_path", "x.cvseries"):
            app._restart_app()
        self.assertEqual(order, ["offer", "saves", "execv"])   # POSIX: the process image is replaced, no teardown
        app._closing = False
        order.clear()

        def fail(*_a):
            raise OSError("no exec")
        shown = []
        with mock.patch.object(app, "_offer_series_save", lambda *_a: True), \
                mock.patch.object(app, "_finish_saves_on_close", lambda: None), \
                mock.patch.object(app, "destroy", lambda: order.append("destroy")), \
                mock.patch.object(self.ca.os, "execv", fail), \
                mock.patch.object(self.ca.os, "chdir", lambda *_a: None), \
                mock.patch.object(self.ca.messagebox, "showinfo", lambda _t, m, **k: shown.append(m)), \
                mock.patch.object(app, "series_path", ""):
            app._restart_app()   # re-review S1: a failed restart says so and SHOBS-P stays open
        self.assertEqual(order, [])
        self.assertIn("could not restart itself (no exec)", shown[0])
        self.assertFalse(app._closing)
        app._closing = False
        order.clear()
        with mock.patch.object(app, "_offer_series_save", lambda *_a: order.append("offer") or False), \
                mock.patch.object(app, "_finish_saves_on_close", lambda: order.append("saves")), \
                mock.patch.object(app, "destroy", lambda: order.append("destroy")), \
                mock.patch.object(app, "series_path", "x.cvseries"):
            app._restart_app()   # Cancel on the save offer: SHOBS-P stays
        self.assertEqual(order, ["offer"])


class TestTextScaleFonts(unittest.TestCase):
    def test_scaled_font(self):
        import text_scale
        f = text_scale.scaled_font
        self.assertEqual(f(("Segoe UI", 9, "bold"), 1.5), ("Segoe UI", 14, "bold"))
        self.assertEqual(f(["Menlo", -12], 1.5), ["Menlo", -18])
        self.assertEqual(f("TkDefaultFont", 1.5), "TkDefaultFont")
        self.assertEqual(f(("Segoe UI",), 1.5), ("Segoe UI",))
        self.assertEqual(f(("Segoe UI", 9), 1.0), ("Segoe UI", 9))

    @unittest.skipUnless(HAVE_TK, "needs a display")
    def test_mac_path_scales_fonts_where_given(self):
        """Fable M1: on Aqua tk scaling does not move point fonts; forced here (fresh interpreter, so the patches stay
        out of the other tests): widget fonts, ttk styles, Font objects, named fonts and Figure dpi all follow."""
        import subprocess
        code = (
            "import sys, tkinter as tk, tkinter.ttk as ttk, tkinter.font as tkf\n"
            f"sys.path.insert(0, {ROOT!r})\n"
            "import text_scale\n"
            "text_scale._linespace = lambda w, size=12: 15\n"   # fonts 'did not grow': the Mac path
            "r = tk.Tk(); r.withdraw()\n"
            "base = int(tkf.nametofont('TkDefaultFont').cget('size'))\n"
            "how = text_scale.apply(r, 150)\n"
            "assert how == 'fonts', how\n"
            "assert abs(int(tkf.nametofont('TkDefaultFont').cget('size'))) == round(abs(base) * 1.5), base\n"
            "l = tk.Label(r, font=('Helvetica', 10, 'bold'))\n"
            "assert tkf.Font(font=l.cget('font')).actual('size') == 15, l.cget('font')\n"
            "assert tkf.Font(root=r, family='Helvetica', size=10).cget('size') == 15\n"
            "s = ttk.Style(r); s.configure('X.TLabel', font=('Helvetica', 8))\n"
            "assert '12' in str(s.lookup('X.TLabel', 'font')), s.lookup('X.TLabel', 'font')\n"
            "from matplotlib.figure import Figure\n"
            "assert abs(Figure(dpi=100).dpi - 150) < 1e-6\n"
            "print('OK')\n")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertIn("OK", out.stdout, out.stderr)


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
    def window(self):
        ca, app = self.ca, self.app
        w = ca.ExtremumWindow.__new__(ca.ExtremumWindow)
        w.app = app
        w.win = ca.Toplevel(app)
        w.win.withdraw()
        w.SYNC = False
        w.kind = ca.StringVar(value="maximum")
        w.window_min = ca.StringVar(value="")
        w.system = ca.StringVar(value="HJD")
        w.result_text = ca.StringVar(value="")
        w.measure_button = ca.ttk.Button(w.win)
        w.save_button = ca.ttk.Button(w.win)
        t = np.linspace(2461358.6, 2461358.9, 40)
        w._night_points = lambda: ("JD2461358", [SimpleNamespace(jd=x) for x in t], list(np.sin(t * 40)))
        w._elements = lambda: (None, None)
        w._noise_period = lambda: (None, "none", "")
        w._coords = lambda: (None, None, None, None)
        w.draw = lambda: None
        return w

    def wait(self, w):
        end = time.time() + 5
        while getattr(w, "_measuring", False) and time.time() < end:
            self.app.update()
            time.sleep(0.02)

    def test_measure_runs_off_the_tk_thread(self):
        """#116: Measure greys itself, the work runs on another thread, the result arrives through the UI queue."""
        ca = self.ca
        seen = {"calls": 0}

        def fake_work(job):
            seen["calls"] += 1
            seen["thread"] = threading.current_thread() is not threading.main_thread()
            time.sleep(0.3)
            return [], ["no extremum inside the window"]

        w = self.window()
        with mock.patch.object(ca.ExtremumWindow, "_measure_work", staticmethod(fake_work)), \
                mock.patch.object(ca, "grow_to_content", lambda *_a: None):
            w.measure()
            self.assertIn("disabled", w.measure_button.state())
            self.assertIn("Measuring the time of maximum", w.result_text.get())
            w.measure()   # a second press while measuring does nothing
            self.wait(w)
        self.assertEqual(seen["calls"], 1)
        self.assertTrue(seen.get("thread"))
        self.assertFalse(w._measuring)
        self.assertNotIn("disabled", w.measure_button.state())
        self.assertIn("no extremum inside the window", w.result_text.get())
        w.win.destroy()

    def test_worker_error_is_shown_and_frees_measure(self):
        ca = self.ca

        def boom(job):
            raise RuntimeError("bad fit")

        w = self.window()
        with mock.patch.object(ca.ExtremumWindow, "_measure_work", staticmethod(boom)), \
                mock.patch.object(ca, "grow_to_content", lambda *_a: None), \
                mock.patch.object(ca.traceback, "print_exc", lambda: None):
            w.measure()
            self.wait(w)
        self.assertIn("the measurement stopped: RuntimeError: bad fit", w.result_text.get())
        self.assertNotIn("disabled", w.measure_button.state())
        w.win.destroy()

    def test_window_closed_while_measuring(self):
        ca = self.ca

        def slow(job):
            time.sleep(0.3)
            return [], ["none"]

        w = self.window()
        with mock.patch.object(ca.ExtremumWindow, "_measure_work", staticmethod(slow)), \
                mock.patch.object(ca, "grow_to_content", lambda *_a: None):
            w.measure()
            w.win.destroy()
            self.wait(w)
        self.assertFalse(w._measuring)   # no error, nothing shown on a closed window




class TestIersOffTkThread(unittest.TestCase):
    """F1 regression (23 Nov): the 21.7 s post-solve freeze was astropy downloading IERS-A inside a transform."""

    def setUp(self):
        from astropy.utils import iers
        self.iers = iers
        self._saved = (iers.conf.auto_download, iers.conf.iers_degraded_accuracy, iers.conf.auto_max_age)

    def tearDown(self):
        (self.iers.conf.auto_download, self.iers.conf.iers_degraded_accuracy,
         self.iers.conf.auto_max_age) = self._saved

    def test_configure_turns_off_downloads(self):
        import iers_bg
        iers_bg.configure()
        self.assertFalse(self.iers.conf.auto_download)
        self.assertEqual(self.iers.conf.iers_degraded_accuracy, "warn")
        self.assertIsNone(self.iers.conf.auto_max_age)

    def test_stale_bundle_does_not_raise(self):
        """Fable F1 M1: a bundle whose predictions are over 30 days old must not raise on a recent night."""
        import iers_bg
        from astropy.time import Time
        iers_bg.configure()
        tab = self.iers.IERS_Auto.open()
        saved = tab.meta.get("predictive_mjd")
        try:
            tab.meta["predictive_mjd"] = Time.now().mjd - 60
            a = core.airmass(23.25, 57.17, 35.36, -81.96, Time.now().jd - 1)
            b = core.convert_times(np.array([Time.now().jd - 1]), "BJD_TDB", 348.3, 57.17, 35.36, -81.96)
        finally:
            tab.meta["predictive_mjd"] = saved
        self.assertTrue(math.isfinite(a))
        self.assertTrue(np.isfinite(b).all())

    def test_airmass_never_downloads_after_configure(self):
        import iers_bg
        from astropy.utils import data
        iers_bg.configure()
        with mock.patch.object(data, "download_file", side_effect=AssertionError("downloaded")), \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            a = core.airmass(23.25, 57.17, 35.36, -81.96, 2461355.6)
            b = core.convert_times(np.array([2461355.6]), "BJD_TDB", 348.3, 57.17, 35.36, -81.96)
        self.assertTrue(math.isfinite(a) and 1.0 <= a < 8.0)
        self.assertTrue(np.isfinite(b).all())

    def test_refresh_download_then_failure_paths(self):
        import iers_bg
        from astropy.utils.iers import IERS_A_FILE
        logs = []
        with mock.patch.object(iers_bg, "_cached_path", return_value=None), \
                mock.patch.object(self.iers.earth_orientation_table, "set") as put:
            with mock.patch("astropy.utils.data.import_file_to_cache", side_effect=lambda u, f, **k: f) as imp:
                p = iers_bg.refresh(logs.append, download=lambda url: IERS_A_FILE)
            self.assertEqual(p, IERS_A_FILE)
            self.assertTrue(imp.called)
            self.assertTrue(put.called)
            self.assertIn("downloaded", logs[-1])

            def boom(url):
                raise OSError("offline")
            self.assertIsNone(iers_bg.refresh(logs.append, download=boom))
            self.assertIn("bundled", logs[-1])

    def test_refresh_uses_fresh_cache_without_network(self):
        import iers_bg
        from astropy.utils.iers import IERS_A_FILE
        logs = []
        with mock.patch.object(iers_bg, "_cached_path", return_value=IERS_A_FILE), \
                mock.patch.object(self.iers.earth_orientation_table, "set"):
            p = iers_bg.refresh(logs.append, download=lambda url: self.fail("downloaded"),
                                now=lambda: os.path.getmtime(IERS_A_FILE) + 3600)
        self.assertEqual(p, IERS_A_FILE)
        self.assertIn("cached copy", logs[-1])

    def test_main_module_alias_and_start(self):
        src = open(os.path.join(ROOT, "clearv_app.py"), encoding="utf-8").read()
        self.assertIn('sys.modules.setdefault("clearv_app", sys.modules[__name__])', src)
        self.assertIn("iers_bg.start(log_to_file)", src)

    def test_bad_download_never_reaches_the_cache(self):
        """Fable F1 S1: an error page is read (and fails) before it could replace the cached copy."""
        import iers_bg
        bad = os.path.join(tempfile.mkdtemp(), "portal.html")
        with open(bad, "w") as fh:
            fh.write("<html>log in to the hotel wifi</html>")
        logs = []
        with mock.patch.object(iers_bg, "_cached_path", return_value=None), \
                mock.patch("astropy.utils.data.import_file_to_cache") as imp, \
                mock.patch.object(self.iers.earth_orientation_table, "set") as put:
            self.assertIsNone(iers_bg.refresh(logs.append, download=lambda url: bad))
        self.assertFalse(imp.called)
        self.assertFalse(put.called)
        self.assertIn("bundled", logs[-1])


class TestWatchNamesOwnCaller(unittest.TestCase):
    def test_deep_library_stall_names_shobs_frame(self):
        import importlib
        import types
        # a "library" module outside the SHOBS-P folder, called from this (SHOBS-P-named) test through many frames
        lib = types.ModuleType("fakelib")
        code = "def deep(n, ev, go):\n    if n:\n        return deep(n - 1, ev, go)\n    go.set(); ev.wait(5)\n"
        exec(compile(code, os.path.join(tempfile.gettempdir(), "fakelib.py"), "exec"), lib.__dict__)
        ev, go = threading.Event(), threading.Event()
        holder = {}

        def worker_in_own_file():
            holder["id"] = threading.get_ident()
            lib.deep(30, ev, go)
        t = threading.Thread(target=worker_in_own_file)
        t.start()
        go.wait(5)
        w = ui_watch.UIWatch(lambda s: None, main_thread_id=holder["id"])
        with mock.patch.object(ui_watch, "_is_own", side_effect=lambda fn: os.path.basename(fn) == "test_240_fitin1.py"):
            text = w.stack_text()
        ev.set()
        t.join()
        self.assertIn("(called from SHOBS-P:)", text)
        self.assertIn("worker_in_own_file", text)


if __name__ == "__main__":
    unittest.main()
