"""2.4.0 step 4 (#112): leaving the Blink page stops playback and the frame reader behind it."""
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import numpy as np

from tests.test_240_m import _have_tk


@unittest.skipUnless(_have_tk(), "needs a display")
class LeaveBlink(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        cls.tmp = tempfile.mkdtemp()
        cls.patches = [mock.patch.object(ca, "SETTINGS_FILE", os.path.join(cls.tmp, "s.json")),
                       mock.patch.object(ca.messagebox, "showinfo", lambda *a, **k: None),
                       mock.patch.object(ca.messagebox, "showwarning", lambda *a, **k: None),
                       mock.patch.object(ca.messagebox, "showerror", lambda *a, **k: None)]
        for p in cls.patches:
            p.start()
        ca.LOG_FILE = os.path.join(cls.tmp, "test_240_r.log")
        cls.app = ca.App()
        cls.app.withdraw()
        cls.real_log = cls.app.log

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        app = self.app
        self.lines = []
        real = self.real_log

        def spy(text, *a, **k):   # the real log (status line rules included), recorded
            self.lines.append(str(text))
            return real(text, *a, **k)
        app.log = spy
        app.show_step(self.ca.STEP_BLINK)
        app.stop_blink()
        self.reads = []
        # three small frames, read by the real reader from a stand-in picture function
        self.paths = [os.path.join(self.tmp, f"f{i}.fit") for i in range(3)]
        app.blink_paths = list(self.paths)
        app._blink_index_of = {p: i for i, p in enumerate(self.paths)}
        app._blink_labels = {p: str(i + 1) for i, p in enumerate(self.paths)}
        app.blink_index = 0
        with app._blink_lock:
            app._blink_cache.clear()
        app._blink_failed.clear()

    def tearDown(self):
        self.app.stop_blink()
        self.app._blink_gen += 1
        self.app.log = self.real_log

    def _slow_picture(self, path, binning):
        self.reads.append(path)
        time.sleep(0.05)
        return self.app_picture

    @property
    def app_picture(self):
        img = np.zeros((4, 4), dtype=np.uint8)
        return (img, 60.0, np.zeros((4, 4), dtype=np.float16), dict(self.app.stretch["blink"]))

    def _pump(self, sec, until=None):
        t = time.time()
        while time.time() - t < sec:
            self.app.update()
            time.sleep(0.02)
            if until and until():
                return True
        return False

    def _playing(self):
        app = self.app
        app.blink_playing = True
        app._blink_job = app.after(60000, lambda: None)
        app._blink_show_job = app.after(60000, lambda: None)

    def test_next_step_stops_playback(self):
        self._playing()
        self.app.show_step(self.ca.STEP_CAL)
        self.assertFalse(self.app.blink_playing)
        self.assertIsNone(self.app._blink_job)
        self.assertIsNone(self.app._blink_show_job)
        self.assertIsNone(getattr(self.app, "_blink_busy_tok", None))
        self.assertTrue(any("Blink stopped (left the Blink page)" in ln for ln in self.lines))
        self.assertIn("left the Blink page", self.app.status.get())

    def test_credits_stops_playback(self):
        self._playing()
        self.app.show_credits()
        self.assertFalse(self.app.blink_playing)
        self.assertIsNone(self.app._blink_job)

    def test_mode_change_on_blink_is_not_a_leave(self):
        self._playing()
        with mock.patch.object(self.ca.messagebox, "askyesno", lambda *a, **k: True):
            self.app.set_mode("variables" if self.app.mode_key != "variables" else "transits")
        self.assertEqual(self.app.current_step, self.ca.STEP_BLINK)
        self.assertTrue(self.app.blink_playing)

    def test_only_an_awaited_frame_is_dropped_quietly(self):
        self.app._blink_show_job = self.app.after(60000, lambda: None)
        self.app.show_step(self.ca.STEP_INPUT)
        self.assertIsNone(self.app._blink_show_job)
        self.assertFalse(any("Blink stopped" in ln for ln in self.lines))

    def test_real_reader_stops_while_away_and_restarts_on_return(self):
        app = self.app
        with mock.patch.object(app, "_blink_picture", self._slow_picture), \
                mock.patch.object(app, "_blink_next_to_read",
                                  lambda b, paths=None: next((p for p in (paths or app.blink_paths)
                                                              if (p, b) not in app._blink_cache), None)):
            app.blink_index = 2
            app.show_blink()   # frame 3 awaited: the reader starts
            self.assertTrue(self._pump(2, lambda: len(self.reads) >= 1))
            app.show_step(self.ca.STEP_CAL)   # leave while frames are still being read
            reader = app._blink_reader
            self.assertTrue(self._pump(2, lambda: not reader.is_alive()))
            n_away = len(self.reads)
            self._pump(0.4)
            self.assertEqual(len(self.reads), n_away)   # nothing read while away
            app.show_step(self.ca.STEP_BLINK)   # back: the awaited frame is asked for again and drawn
            self.assertTrue(self._pump(3, lambda: app._blink_key(self.paths[2]) in app._blink_cache))
            self._pump(0.3)
            self.assertIn("Frame 3", app.blink_label.get())
            self.assertIsNone(getattr(app, "_blink_busy_tok", None))

    def test_running_jobs_warning_stays_on_the_status_line(self):
        app = self.app
        app._job_findings = []
        try:
            app.log("⚠ 3 frames skipped")
            shown = app.status.get()
            self.assertIn("⚠", shown)
            self._playing()
            app.show_step(self.ca.STEP_CAL)
            self.assertEqual(app.status.get(), shown)
            self.assertTrue(any("Blink stopped (left the Blink page)" in ln for ln in self.lines))
        finally:
            app._job_findings = None

    def test_stretch_change_off_the_page_reads_nothing_and_shows_on_return(self):
        app = self.app
        with mock.patch.object(app, "_blink_picture", self._slow_picture), \
                mock.patch.object(app, "_blink_next_to_read",
                                  lambda b, paths=None: next((p for p in (paths or app.blink_paths)
                                                              if (p, b) not in app._blink_cache), None)):
            app.show_blink()
            self.assertTrue(self._pump(3, lambda: all(app._blink_key(p) in app._blink_cache for p in self.paths)))
            app.show_step(self.ca.STEP_CAL)
            reader = app._blink_reader
            self.assertTrue(self._pump(2, lambda: reader is None or not reader.is_alive()))
            new = dict(app.stretch["blink"])
            new["white"] = 98.0 if new.get("white") != 98.0 else 97.0
            app.stretch["blink"] = new
            n = len(self.reads)
            app._blink_stretch_changed()
            self.assertIsNone(app._blink_show_job)
            r = app._blink_reader
            self.assertTrue(r is None or not r.is_alive() or r is reader)
            self.assertEqual(len(self.reads), n)
            app.show_step(self.ca.STEP_BLINK)
            self._pump(0.3)
            key = app._blink_key(self.paths[app.blink_index])
            self.assertEqual(app._blink_cache[key][3], app.stretch["blink"])

    def test_not_playing_says_nothing(self):
        self.app.show_step(self.ca.STEP_INPUT)
        self.assertFalse(any("Blink stopped" in ln for ln in self.lines))


if __name__ == "__main__":
    unittest.main()
