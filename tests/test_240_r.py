"""2.4.0 step 4 (#112): leaving the Blink page stops playback and the frame reader behind it."""
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

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

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.lines = []
        self.app.log = lambda text, *a, **k: self.lines.append(str(text))
        self.app.show_step(self.ca.STEP_BLINK)
        self.app.blink_paths = ["a.fit", "b.fit", "c.fit"]

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
        self.assertTrue(any("Blink stopped (left the Blink page)" in ln for ln in self.lines))
        self.assertIn("left the Blink page", self.app.status.get())

    def test_credits_stops_playback(self):
        self._playing()
        self.app.show_credits()
        self.assertFalse(self.app.blink_playing)
        self.assertIsNone(self.app._blink_job)
        # a mode change keeps the step on screen: on the Blink page, playback is on the page shown (not hidden)
        self.app.show_step(self.ca.STEP_BLINK)
        self._playing()
        with mock.patch.object(self.ca.messagebox, "askyesno", lambda *a, **k: True):
            self.app.set_mode("variables" if self.app.mode_key != "variables" else "transits")
        self.assertEqual(self.app.current_step, self.ca.STEP_BLINK)
        self.app.show_step(self.ca.STEP_OUTPUT)
        self.assertFalse(self.app.blink_playing)

    def test_reader_is_told_to_end(self):
        stop = threading.Event()
        reader = threading.Thread(target=stop.wait, daemon=True)
        reader.start()
        self.app._blink_reader = reader
        gen = self.app._blink_gen
        try:
            self.app.show_step(self.ca.STEP_PHOTO)
            self.assertEqual(self.app._blink_gen, gen + 1)
        finally:
            stop.set()
            reader.join(1)
            self.app._blink_reader = None

    def test_staying_on_blink_or_not_playing_says_nothing(self):
        self._playing()
        self.app.show_step(self.ca.STEP_BLINK)
        self.assertTrue(self.app.blink_playing)
        self.app.stop_blink()
        self.lines.clear()
        self.app.show_step(self.ca.STEP_INPUT)
        self.assertFalse(any("Blink stopped" in ln for ln in self.lines))


if __name__ == "__main__":
    unittest.main()
