"""2.4.0 step 1 (builder B): #94 a skipped frame's reason in plain words, once; #95 Transits mode with no site is
asked about before the photometry; #97 the Chart ID is off the Input page (beside the comps and at Save AAVSO, never
empty); #99 frames that point far from the target say how far; #100 Blink never reads a frame on the window's own
thread, and Load series shows the busy bar; nit: "1 frame left out of stacks"."""
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photometry_core as core   # noqa: E402

try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = ("No SIMPLE card found, this file does not appear to be a valid FITS file. If this is really a FITS file, "
       "try with ignore_missing_simple=True")


def _src(name="clearv_app.py"):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


# ---- #94 ---------------------------------------------------------------------------------------------------------
class TestPlainReadError(unittest.TestCase):
    def test_astropy_text_in_plain_words(self):
        self.assertEqual(core.plain_read_error(OSError(RAW)), core.READ_ERR_DAMAGED)
        self.assertEqual(core.plain_read_error(RAW), "not a readable FITS file (damaged or cut short)")
        self.assertEqual(core.plain_read_error(OSError("Header missing END card.")), core.READ_ERR_DAMAGED)
        # step 1 review: these words also come from processing; without the reader in the traceback no cause is
        # claimed (tests/test_240_c.py has the reader's side)
        self.assertEqual(core.plain_read_error(TypeError("buffer is too small for requested array")),
                         core.READ_ERR_UNSURE)

    def test_other_common_errors(self):
        self.assertEqual(core.plain_read_error(FileNotFoundError(2, "No such file or directory")), core.READ_ERR_GONE)
        self.assertEqual(core.plain_read_error(PermissionError(13, "Permission denied")), core.READ_ERR_LOCKED)
        self.assertEqual(core.plain_read_error(MemoryError()), core.READ_ERR_MEMORY)
        self.assertEqual(core.plain_read_error("Unable to allocate 104. MiB for an array"), core.READ_ERR_MEMORY)
        self.assertEqual(core.plain_read_error(OSError("[Errno 5] Input/output error")), core.READ_ERR_DISK)

    def test_not_a_read_error_is_left_alone(self):
        self.assertEqual(core.plain_read_error(ValueError("every comp was lost (C1: off the frame edge)")), "")
        self.assertEqual(core.plain_read_error("could not align with the reference frame"), "")
        self.assertEqual(core.plain_read_error(""), "")

    def test_real_files(self):
        d = tempfile.mkdtemp()
        junk, empty = os.path.join(d, "junk.fits"), os.path.join(d, "empty.fits")
        with open(junk, "wb") as fh:
            fh.write(b"not a fits file at all " * 200)
        open(empty, "wb").close()
        for path, want in ((junk, core.READ_ERR_DAMAGED), (empty, core.READ_ERR_EMPTY),
                           (os.path.join(d, "gone.fits"), core.READ_ERR_GONE)):
            with self.assertRaises(Exception) as ctx:
                core.read_fits(path)
            self.assertEqual(core.plain_read_error(ctx.exception, path), want, path)

    def test_cut_short_file(self):
        from astropy.io import fits
        d = tempfile.mkdtemp()
        whole, cut = os.path.join(d, "whole.fits"), os.path.join(d, "cut.fits")
        fits.PrimaryHDU(np.arange(400 * 300, dtype=np.uint16).reshape(300, 400)).writeto(whole)
        with open(whole, "rb") as fh:
            data = fh.read()
        with open(cut, "wb") as fh:
            fh.write(data[:len(data) // 3])
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # step 1 review: caught as the app catches it, traceback and all (assertRaises takes it off, and the
            # reader in the traceback is what tells a cut-short file from trouble in the processing)
            try:
                core.read_fits(cut)
            except Exception as exc:
                got = exc
            else:
                self.fail("a cut-short file was read")
        self.assertEqual(core.plain_read_error(got, cut), core.READ_ERR_DAMAGED, repr(got))

    def test_skip_reason_word_maps_old_series(self):
        # a series saved by 2.3.6 holds the library's text: shown in plain words from now on
        self.assertEqual(core.skip_reason_word(RAW), core.READ_ERR_DAMAGED)
        self.assertEqual(core.skip_reason_word(core.READ_ERR_DAMAGED), core.READ_ERR_DAMAGED)
        self.assertEqual(core.skip_reason_word("TOI-1 is not on this frame"), "target not on the frame")
        self.assertEqual(core.skip_reason_word("could not align (3 stars)"), "could not align")


class TestSayFrameSkip(unittest.TestCase):
    def _stub(self):
        import clearv_app as ca
        logged = []
        st = types.SimpleNamespace(call_ui=lambda f, *a: f(*a), log=lambda t, **k: logged.append(t),
                                   _log_only=lambda t: logged.append(t))
        return ca, st, logged

    def test_plain_once_and_raw_only_in_the_log_file(self):
        ca, st, logged = self._stub()
        filed = []
        with mock.patch.object(ca, "log_to_file", lambda t, **k: filed.append(t)):
            why = ca.App._say_frame_skip(st, "  aperture test skipped", "/n/L_0037.fits", OSError(RAW))
            why2 = ca.App._say_frame_skip(st, "  skipped", "/n/L_0037.fits", OSError(RAW))
        self.assertEqual(why, core.READ_ERR_DAMAGED)
        self.assertEqual(why2, core.READ_ERR_DAMAGED)   # the reason kept with the night is the plain one
        self.assertEqual(len(logged), 1, logged)        # once in the Log, however many steps met the file
        self.assertIn("L_0037.fits: not a readable FITS file (damaged or cut short)", logged[0])
        self.assertNotIn("SIMPLE", " ".join(logged))
        self.assertEqual(len(filed), 2)
        self.assertTrue(all("ignore_missing_simple" in t for t in filed))

    def test_other_reasons_logged_as_before(self):
        ca, st, logged = self._stub()
        why = ca.App._say_frame_skip(st, "  skipped", "/n/L_0001.fits", ValueError("could not align (2 stars)"))
        self.assertEqual(why, "could not align (2 stars)")
        self.assertEqual(logged, ["  skipped L_0001.fits: could not align (2 stars)"])

    def _text(self, skips, said=None, n_frames=200):
        import clearv_app as ca
        st = types.SimpleNamespace(
            _night_info=lambda n: {"skipped": skips}, _night_frame_count=lambda n: n_frames,
            _frame_ranges=lambda labs: ", ".join(str(x) for x in labs), recoverable_skips=lambda n: {},
            observations=[])
        return ca.App.comp_skip_text(st, "N1", said=said)

    def test_log_summary_does_not_repeat_the_unreadable_frames(self):
        skips = [{"frame": "37", "path": "/n/a.fits", "why": core.READ_ERR_DAMAGED},
                 {"frame": "90", "path": "/n/b.fits", "why": "could not align (2 stars)"}]
        page = self._text(skips)
        self.assertIn("2 frames skipped", page)
        self.assertIn("not a readable FITS file (damaged or cut short) (frame 37)", page)
        log = self._text(skips, said={"/n/a.fits"})
        self.assertNotIn("not a readable", log)
        self.assertIn("1 frame skipped: could not align (frame 90).", log)
        self.assertEqual(self._text(skips[:1], said={"/n/a.fits"}), "")   # nothing left to say a second time
        # most of the night unreadable is still a finding in the Log
        self.assertTrue(self._text(skips[:1], said={"/n/a.fits"}, n_frames=2).startswith("⚠ 1 frame skipped"))

    def test_settings_file_wording(self):
        s = _src()
        self.assertIn("why = self._say_frame_skip(\"  skipped\", path, exc)", s)
        self.assertIn('skipped.append({"path": path, "why": why,', s)


# ---- #99 ---------------------------------------------------------------------------------------------------------
class TestTargetVsFrame(unittest.TestCase):
    SOL = {"target_xy": (499.5, 399.5), "scale": 1.0, "angle": 0.0, "ra_deg": 103.0338, "dec_deg": 18.2142}
    SHAPE = (800, 1000)

    def test_far(self):
        off = core.target_vs_frame(103.0204, -8.2002, self.SHAPE, self.SOL)
        self.assertEqual(off["case"], "far")
        self.assertAlmostEqual(off["sep_deg"], 26.414, places=2)
        self.assertEqual(off["way"], "south")
        self.assertAlmostEqual(off["center"][1], 18.2142, places=3)

    def test_just_off_the_north_edge(self):
        off = core.target_vs_frame(103.0338, 18.2142 + (399.5 + 240.0) / 3600.0, self.SHAPE, self.SOL)
        self.assertEqual(off["case"], "near")
        self.assertEqual(off["way"], "north")
        self.assertAlmostEqual(off["beyond_deg"] * 60.0, 4.0, places=1)
        self.assertEqual(core.angle_words(off["beyond_deg"]), "4′")

    def test_on_the_frame(self):
        off = core.target_vs_frame(103.0338, 18.2142 + 0.01, self.SHAPE, self.SOL)
        self.assertEqual((off["case"], off["beyond_deg"]), ("on", 0.0))

    def test_more_than_a_field_width_is_far(self):
        # field = 1000 px = 0.278°: 0.5° beyond the east edge
        ra = 103.0338 + (500.0 + 1800.0) / 3600.0 / np.cos(np.radians(18.2142))
        off = core.target_vs_frame(ra, 18.2142, self.SHAPE, self.SOL)
        self.assertEqual(off["case"], "far")
        self.assertEqual(off["way"], "east")

    def test_words(self):
        self.assertEqual(core.radec_colons(103.0338, 18.2142), "06:52:08 +18:12:51")
        self.assertEqual(core.radec_colons(103.0204, -8.2002), "06:52:05 −08:12:01")
        self.assertEqual(core.angle_words(26.414), "26.4°")
        self.assertEqual(core.angle_words(2.4 / 60.0), "2.4′")
        self.assertEqual(core.angle_words(25.0 / 3600.0), "25″")
        self.assertAlmostEqual(core.great_circle_deg(0, 0, 0, 90), 90.0, places=6)
        self.assertAlmostEqual(core.great_circle_deg(359.5, 10, 0.5, 10), np.cos(np.radians(10)), places=3)

    def test_bad_solution(self):
        self.assertIsNone(core.target_vs_frame(10.0, 10.0, self.SHAPE, {"scale": 1.0}))


class TestSourceWording(unittest.TestCase):
    def test_far_message_has_no_blink_advice(self):
        s = _src()
        i = s.index("def _far_from_target_text")
        block = s[i:i + 1100]
        self.assertIn("The frames are centered at", block)
        self.assertIn("The target isn't in this night's frames.", block)
        self.assertIn("Check the telescope's target coordinates", block)
        self.assertNotIn("Blink", block)

    def test_chart_id_empty_message_retired(self):
        s = _src()
        self.assertNotIn("Chart ID is empty", s)
        self.assertNotIn('"Chart ID", self.chart', s)             # no box on the Input page
        self.assertIn('self._field(report, 0, "Observer code", self.observer', s)   # the observer code stays
        self.assertIn('"observer", "star_id", "chart",', s)       # the series' key is the same

    def test_stack_line_singular(self):
        s = _src("stack_ui.py")
        self.assertNotIn("frames left out of stacks", s)
        self.assertIn("frame{'s' if n_out != 1 else ''} left out of stacks", s)

    def test_blink_never_reads_in_show_or_tick(self):
        s = _src()
        for name in ("def show_blink(self):", "def _blink_tick(self):"):
            i = s.index(name)
            j = s.index("\n    def ", i + 10)
            self.assertNotIn("_blink_picture(", s[i:j], name)

    def test_load_series_uses_the_busy_bar(self):
        s = _src()
        i = s.index("    def load_series(self):")
        j = s.index("    def _load_series_file", i)
        self.assertIn("self.busy_begin(", s[i:j])
        self.assertIn('self.busy_end(bar["tok"])', s[i:j])   # step 1 review: the bar can pause for a question
        k = s.index("    def _page_output", j)
        self.assertIn('step("the period search")', s[j:k])


# ---- with the window ---------------------------------------------------------------------------------------------
@unittest.skipUnless(HAVE_TK, "needs a display")
class TestApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test240b.log")
        with mock.patch.object(ca.App, "_save_settings", lambda *a, **k: None):
            cls.app = ca.App()
        cls.app.withdraw()
        cls.app._save_settings = lambda *a, **k: None
        cls.patches = [mock.patch.object(ca.messagebox, n, return_value=True)
                       for n in ("showwarning", "showinfo", "showerror")]
        for p in cls.patches:
            p.start()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.app.destroy()

    def _logged(self):
        lines = []
        return lines, mock.patch.object(self.app, "log", lambda t, **k: lines.append(t))

    # ---- #95 ----
    def test_site_guard(self):
        ca, app = self.ca, self.app
        old = (app.mode_key, app.lat.get(), app.lon.get())
        asked = []

        def answer(value):
            def ask(parent, title, message, choices, safe, cancel=None):
                asked.append((message, [t for _v, t in choices], safe))
                return value
            return ask
        try:
            app.lat.set("")
            app.lon.set("")
            app.mode_key = "variables"
            with mock.patch.object(ca, "ask_choice", answer("site")):
                self.assertTrue(app._transit_site_guard())       # Variables mode: never asked
            self.assertEqual(asked, [])
            app.mode_key = "transits"
            lines, patch = self._logged()
            with mock.patch.object(ca, "ask_choice", answer("run")), patch:
                self.assertTrue(app._transit_site_guard())       # photometry only
            self.assertTrue(asked[0][0].startswith("No site is set, so the transit fit can't run."))
            self.assertEqual(asked[0][1], ["Set the site now", "Run photometry only"])
            self.assertEqual(asked[0][2], "site")
            self.assertTrue(any("Photometry only" in t for t in lines))
            shown = []
            with mock.patch.object(ca, "ask_choice", answer("site")), \
                    mock.patch.object(app, "show_step", lambda i: shown.append(i)):
                self.assertFalse(app._transit_site_guard())      # Set the site now: no run, the Input page
            self.assertEqual(shown, [ca.STEPS.index("Input")])
            self.assertIn("Site box", app.status.get())
            app.lat.set("35.37")
            app.lon.set("-81.96")
            n = len(asked)
            with mock.patch.object(ca, "ask_choice", answer("site")):
                self.assertTrue(app._transit_site_guard())       # a site is set: not asked
            self.assertEqual(len(asked), n)
        finally:
            app.mode_key = old[0]
            app.lat.set(old[1])
            app.lon.set(old[2])

    def test_site_guard_runs_before_the_photometry(self):
        s = _src()
        i = s.index("    def run_photometry(self")
        self.assertLess(s.index("self._transit_site_guard()", i), s.index("self._start_job(f\"Photometry on", i))

    # ---- #97 ----
    def test_chart_id_never_empty(self):
        app = self.app
        old = (app.chart.get(), getattr(app, "_chart_auto", None), getattr(app, "_chart_loaded", None))
        try:
            app._chart_loaded = None
            app.chart.set("")
            self.assertEqual(app.chart_id(), "na")              # an old settings / series value of ""
            app._chart_auto = None
            app._update_chart_field()
            self.assertEqual(app.chart.get(), "na")
            self.assertIn("AAVSO chart ID: na", app.chart_line.get())
            with mock.patch.object(self.ca.simpledialog, "askstring", return_value=" x36281 abc "):
                self.assertTrue(app.edit_chart_id())
            self.assertEqual(app.chart.get(), "x36281abc")
            self.assertIn("x36281abc (typed)", app.chart_line.get())
            app._update_chart_field()                           # a typed ID is kept
            self.assertEqual(app.chart.get(), "x36281abc")
            with mock.patch.object(self.ca.simpledialog, "askstring", return_value=""):
                self.assertTrue(app.edit_chart_id())            # empty: back to SHOBS-P's own
            self.assertEqual(app.chart.get(), "na")
            with mock.patch.object(self.ca.simpledialog, "askstring", return_value=None):
                self.assertFalse(app.edit_chart_id())           # Cancel changes nothing
            app._chart_auto = "X12345AB"
            app.chart.set("X12345AB")
            self.assertIn("X12345AB (from the AAVSO sequence", app.chart_line.get())
        finally:
            app._chart_auto, app._chart_loaded = old[1], old[2]
            app.chart.set(old[0])

    def test_chart_id_shown_with_edit(self):
        app = self.app
        self.assertEqual(str(app.chart_edit_button.cget("text")), "Edit…")
        old = app.chart.get()
        try:
            app.chart.set("na")
            asked = []

            def ask(parent, title, message, choices, safe, cancel=None):
                asked.append((message, choices, safe))
                return "edit" if len(asked) == 1 else "save"
            with mock.patch.object(self.ca, "ask_choice", ask), \
                    mock.patch.object(self.ca.simpledialog, "askstring", return_value="X777"):
                self.assertTrue(app._chart_id_at_save())
            self.assertIn("Chart ID in this report: na", asked[0][0])
            self.assertIn("Chart ID in this report: X777 (typed)", asked[1][0])
            self.assertEqual(asked[0][2], "save")
            with mock.patch.object(self.ca, "ask_choice", lambda *a, **k: "stop"):
                self.assertFalse(app._chart_id_at_save())
            # John 16 Nov: a chart ID filled in from the AAVSO sequence saves without asking
            old_auto = getattr(app, "_chart_auto", None)
            try:
                app._chart_auto = "X12345AB"
                app.chart.set("X12345AB")
                never = mock.Mock(side_effect=AssertionError("asked"))
                with mock.patch.object(self.ca, "ask_choice", never):
                    self.assertTrue(app._chart_id_at_save())
                app.chart.set("X999TYPED")           # typed by hand: asks again
                with mock.patch.object(self.ca, "ask_choice", lambda *a, **k: "save") as _m:
                    self.assertTrue(app._chart_id_at_save())
            finally:
                app._chart_auto = old_auto
        finally:
            app.chart.set(old)
        src = _src()
        i = src.index("    def _save_aavso_report")
        self.assertIn("self._chart_id_at_save()", src[i:i + 2500])
        i = src.index("    def _report_text")
        self.assertIn("self.chart_id(),", src[i:i + 2500])

    # ---- #99 ----
    def test_far_text(self):
        off = core.target_vs_frame(103.0204, -8.2002, (800, 1000), TestTargetVsFrame.SOL)
        text = self.app._far_from_target_text(off, "ATO J103.0204-08.2002", 103.0204, -8.2002)
        self.assertTrue(text.startswith(
            "✖ The frames are centered at 06:52:08 +18:12:51, 26.4° from ATO J103.0204-08.2002 "
            "(06:52:05 −08:12:01). The target isn't in this night's frames. Check the telescope's target "
            "coordinates"), text)
        one = self.app._far_from_target_text(off, "X", 103.0204, -8.2002, all_frames=False)
        self.assertIn("The reference frame is centered at", one)

    def test_header_pointing_note(self):
        app = self.app
        old = (app.preview, app.preview_header, app.preview_path, app.ra_hours.get(), app.dec_deg.get(),
               app.star_id.get(), app.mode_key)
        lines, patch = self._logged()
        try:
            app.mode_key = "variables"
            app.preview = np.zeros((800, 1000), dtype=np.float32)
            app.preview_header = {"RA": 103.0338, "DEC": 18.2142, "OBJECT": "M 35 plan"}
            app.preview_path = "/n/L_0001.fits"
            app.star_id.set("ATO J103.0204-08.2002")
            app.ra_hours.set(str(103.0204 / 15.0))
            app.dec_deg.set("-8.2002")
            app._header_pointing_said = None
            with patch, mock.patch.object(app, "_radec_is_fill", lambda: False):
                app._header_pointing_note()
                app._header_pointing_note()   # once for a frame and target
                self.assertEqual(len(lines), 1, lines)
                self.assertIn("06:52:08 +18:12:51 (OBJECT M 35 plan), 26.4° from ATO J103.0204-08.2002", lines[0])
                self.assertTrue(lines[0].startswith("⚠ The frame's header says the telescope pointed at"))
                app.preview_header = {"RA": 103.03, "DEC": -8.21}
                app._header_pointing_said = None
                app._header_pointing_note()   # the header agrees with the target: nothing
                self.assertEqual(len(lines), 1)
        finally:
            app.preview, app.preview_header, app.preview_path = old[0], old[1], old[2]
            app.ra_hours.set(old[3])
            app.dec_deg.set(old[4])
            app.star_id.set(old[5])
            app.mode_key = old[6]

    def test_later_frames_point_near(self):
        app = self.app
        heads = {"/n/a.fits": {"RA": 10.0, "DEC": 20.0}, "/n/b.fits": {"RA": 10.0, "DEC": 20.0}}
        with mock.patch.object(app, "_lights", lambda **k: list(heads)), \
                mock.patch.object(core, "read_header", lambda p: heads[p]):
            self.assertIs(app._later_frames_point_near(10.0, -6.0, 0.3), False)
            self.assertIs(app._later_frames_point_near(10.0, 20.1, 0.3), True)
        with mock.patch.object(app, "_lights", lambda **k: list(heads)), \
                mock.patch.object(core, "read_header", lambda p: {}):
            self.assertIsNone(app._later_frames_point_near(10.0, -6.0, 0.3))

    # ---- #100 ----
    def _blink_night(self, n=24):
        from astropy.io import fits
        d = tempfile.mkdtemp()
        rng = np.random.default_rng(3)
        for i in range(n):
            fits.PrimaryHDU(rng.integers(0, 1000, (40, 60)).astype(np.uint16)).writeto(
                os.path.join(d, f"L_{i + 1:04d}.fits"))
        return d

    def test_blink_playback_never_reads_on_the_window_thread(self):
        ca, app = self.ca, self.app
        folder = self._blink_night(24)
        with open(os.path.join(folder, "L_0025.fits"), "wb") as fh:
            fh.write(b"junk" * 500)                      # an unreadable frame: said in plain words, playback goes on
        real, main, ui_reads = app._blink_picture, threading.main_thread(), []

        def slow(path, binning, stretch=None):
            if threading.current_thread() is main:
                ui_reads.append(path)
            time.sleep(0.12)                             # a reader slower than the 0.05 s the playback asks for
            return real(path, binning, stretch)
        old_dir, old_speed = app.light_dir.get(), app.blink_speed.get()
        try:
            with mock.patch.object(app, "_blink_picture", slow), \
                    mock.patch.object(app, "_blink_seconds", lambda: 0.05):
                app.light_dir.set(folder)
                app.blink_source.set("lights")
                app.load_blink_list()
                app.toggle_blink()
                seen, bars, labels, longest, last, t0 = set(), set(), set(), 0.0, time.time(), time.time()
                while time.time() - t0 < 6.0 and len(seen) < 25:
                    app.update()
                    now = time.time()
                    longest, last = max(longest, now - last), now
                    seen.add(app.blink_index)
                    bars.add(app.busy_bar.text)
                    labels.add(app.blink_label.get())
                    time.sleep(0.004)
                app.stop_blink()
            self.assertEqual(ui_reads, [])               # never on the window's own thread
            self.assertGreaterEqual(len(seen), 20, seen)  # playback went on, at the reader's pace
            self.assertTrue(any(b.startswith("Reading frame ") for b in bars), bars)
            self.assertTrue(any("playing at about" in t for t in labels), list(labels)[:3])
            self.assertLess(longest, 1.0)                # the window kept answering
            self.assertEqual(app.busy_bar.text, "")      # Stop ends the wait on the bar
            bad = [v for k, v in app._blink_failed.items() if k[0].endswith("L_0025.fits")]
            if 24 in seen:
                self.assertEqual(bad, [core.READ_ERR_DAMAGED])
        finally:
            app.blink_playing = False
            app._cancel_blink_timer()
            app.light_dir.set(old_dir)
            app._blink_loaded_from = None

    def test_blink_memory_follows_the_play_position(self):
        ca, app = self.ca, self.app
        paths = [f"/n/L_{i:04d}.fits" for i in range(200)]
        old = (app.blink_paths, app._blink_index_of, dict(app._blink_cache), list(app._blink_cache_order),
               app._blink_pos, dict(app._blink_failed))
        pic = (np.zeros((4, 4), np.uint8), 1.0, np.zeros((4, 4), np.float16), {})
        try:
            app._blink_gen += 1                          # any reader of an earlier test ends
            app.blink_paths = paths
            app._blink_index_of = {p: i for i, p in enumerate(paths)}
            app._blink_failed = {}
            with app._blink_lock:
                app._blink_cache.clear()
                app._blink_cache_order[:] = []
            with mock.patch.object(app, "_blink_limit", lambda value=None: 50):
                # play through the night: every frame is read when it is the next one wanted
                for pos in range(150):
                    app._blink_pos = pos
                    nxt = app._blink_next_to_read(1)
                    while nxt is not None:
                        app._blink_store((nxt, 1), pic)
                        nxt = app._blink_next_to_read(1)
                    have = {app._blink_index_of[k[0]] for k in app._blink_cache}
                    self.assertLessEqual(len(have), 50)
                    self.assertIn(pos, have)             # the frame on screen
                    self.assertTrue(set(range(pos, pos + 39)) <= have, pos)   # and the frames ahead of it
                    if pos >= 10:
                        self.assertTrue(set(range(pos - 10, pos)) <= have, pos)   # a few just shown, for a step back
                self.assertNotIn(100, {app._blink_index_of[k[0]] for k in app._blink_cache})   # shown long ago: dropped
                # a jump back to a frame no longer in memory: it is the next one read
                app._blink_pos = 3
                self.assertEqual(app._blink_next_to_read(1), paths[3])
                app._blink_failed[(paths[3], 1)] = core.READ_ERR_DAMAGED
                self.assertEqual(app._blink_next_to_read(1), paths[4])   # an unreadable frame is not tried again
        finally:
            app.blink_paths, app._blink_index_of = old[0], old[1]
            with app._blink_lock:
                app._blink_cache.clear()
                app._blink_cache.update(old[2])
                app._blink_cache_order[:] = old[3]
            app._blink_pos, app._blink_failed = old[4], old[5]

    def test_blink_step_shows_reading_then_the_frame(self):
        app = self.app
        folder = self._blink_night(3)
        old_dir = app.light_dir.get()
        gate = threading.Event()
        real = app._blink_picture

        def held(path, binning, stretch=None):
            gate.wait(5.0)
            return real(path, binning, stretch)
        try:
            with mock.patch.object(app, "_blink_picture", held):
                app.light_dir.set(folder)
                app.blink_source.set("lights")
                app.load_blink_list()                    # returns at once: the frame is still being read
                self.assertIn("Reading frame 1…", app.blink_label.get())
                self.assertTrue(app.busy_bar.text.startswith("Reading frame 1…"))
                gate.set()
                t0 = time.time()
                while time.time() - t0 < 5.0 and "Reading" in app.blink_label.get():
                    app.update()
                    time.sleep(0.01)
            self.assertIn("Frame 1", app.blink_label.get())
            self.assertIn("(1 of 3 in the list", app.blink_label.get())
            self.assertEqual(app.busy_bar.text, "")
        finally:
            gate.set()
            app._cancel_blink_timer()
            app.light_dir.set(old_dir)
            app._blink_loaded_from = None

    def test_busy_bar_show_now(self):
        bar = self.app.busy_bar
        tok = bar.begin("Loading big.cvseries… reading the file")
        try:
            self.assertFalse(bar.shown)                  # it would wait a quarter second
            bar.show_now()
            self.assertTrue(bar.shown)
            self.assertIsNone(bar._show_job)
        finally:
            bar.end(tok)
        self.assertFalse(bar.shown)


if __name__ == "__main__":
    unittest.main()
