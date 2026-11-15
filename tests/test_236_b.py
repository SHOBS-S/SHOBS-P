"""2.3.6 (#79 smaller bugs, part 2): open_link keeps its friendly status line (m041-m044), an empty Lights folder is
not blamed on Blink (m383), the dark question words the pedestal by whether there is a bias (m161), the missing
color band is named (m331), a series comp not on this frame gets a ⚠ line (m088), and the Discovery /
Variables exports say a disk error in words (unguarded saves)."""
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


def _app():
    import clearv_app as ca
    ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test236b.log")
    app = ca.App()
    return ca, app


def _src():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "clearv_app.py"), encoding="utf-8") as fh:
        return fh.read()


class TestSourceWording(unittest.TestCase):
    def test_band_named(self):
        s = _src()
        self.assertNotIn('"  No catalog here gives B magnitudes, so color', s)
        self.assertIn('No catalog here gives {missing} magnitudes', s)

    def test_dark_question_only_claims_bias_with_bias(self):
        s = _src()
        i = s.index("2.3.6 (m161)")
        block = s[i:i + 1500]
        self.assertIn("if bias:", block)
        self.assertIn("no bias frames", block)


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ca, cls.app = _app()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def test_open_link_status_stays(self):
        app = self.app
        with mock.patch("webbrowser.open", return_value=True):
            self.assertTrue(app.open_link("https://example.org/x", "the test page"))
        self.assertEqual(app.status.get(), "Opened the test page in your browser.")
        with mock.patch("webbrowser.open", return_value=False):
            self.assertFalse(app.open_link("https://example.org/y"))
        self.assertEqual(app.status.get(), "Could not open a browser: go to https://example.org/y")

    def test_empty_lights_not_blamed_on_blink(self):
        app = self.app
        shown = []
        with mock.patch.object(app, "_lights", return_value=[]), \
                mock.patch.object(app, "_split_lights_cached", return_value=([], [], "")), \
                mock.patch.object(app, "_typed_site_check", return_value=None), \
                mock.patch.object(self.ca.messagebox, "showerror", side_effect=lambda t, m, **k: shown.append(m)):
            app.run_photometry()
        self.assertTrue(shown and "No light frames in the Lights folder" in shown[-1], shown)
        shown.clear()
        with mock.patch.object(app, "_lights", return_value=[]), \
                mock.patch.object(app, "_split_lights_cached", return_value=(["a.fits"], [], "")), \
                mock.patch.object(app, "_typed_site_check", return_value=None), \
                mock.patch.object(self.ca.messagebox, "showerror", side_effect=lambda t, m, **k: shown.append(m)):
            app.run_photometry()
        self.assertTrue(shown and "rejected in Blink" in shown[-1], shown)

    def _slots_off_frame(self, series_path):
        app = self.app
        old_preview, old_path = app.preview, app.series_path
        self.addCleanup(lambda: (setattr(app, "preview", old_preview), setattr(app, "series_path", old_path),
                                 app.clear_mark("comp", redraw=False)))
        app.preview = np.zeros((100, 120), dtype=float)
        app.series_path = series_path
        app.comp_name.set("APASS J000000.0+000000")
        app.comp_mag.set("11.20")
        app.comp_xy = (500.0, 40.0)   # off the 120 x 100 frame
        lines = []
        with mock.patch.object(app, "log", side_effect=lambda t, **k: lines.append(t)):
            dropped = app._drop_offframe_slots()
        return dropped, lines

    def test_series_comp_off_frame_is_a_warning(self):
        # (review S1) the slot is still cleared, as in 2.3.5 (numbers freeze); only the words change
        app = self.app
        dropped, lines = self._slots_off_frame("/tmp/some_series.csv")
        self.assertEqual(len(dropped), 1)
        self.assertIsNone(app.comp_xy)
        self.assertEqual(app.comp_name.get(), "")
        self.assertTrue(lines and lines[0].startswith("⚠ Not on this frame, so taken out of tonight's comps"), lines)

    def test_leftover_without_series_is_cleared(self):
        app = self.app
        dropped, lines = self._slots_off_frame("")
        self.assertEqual(len(dropped), 1)
        self.assertEqual(app.comp_name.get(), "")
        self.assertTrue(lines and lines[0].startswith("Cleared, left from an earlier run"), lines)

    def test_stack_run_with_no_frames(self):
        app = self.app
        shown = []
        with mock.patch.object(app, "_typed_site_check", return_value=None), \
                mock.patch.object(self.ca.messagebox, "showerror", side_effect=lambda t, m, **k: shown.append(m)):
            app.run_photometry(stack_run={"paths": []})
        self.assertEqual(shown[-1:], ["No frames to measure."])

    def test_guarded_write_says_disk_error(self):
        app = self.app
        shown = []

        def boom():
            raise PermissionError(13, "Permission denied")
        with mock.patch.object(self.ca.messagebox, "showerror", side_effect=lambda t, m, **k: shown.append(m)):
            self.assertFalse(app.guarded_write("/x/field_scan_candidates.csv", boom))
            self.assertTrue(app.guarded_write("/x/ok.csv", lambda: None))
        self.assertEqual(len(shown), 1)
        self.assertIn("field_scan_candidates.csv not saved (Permission denied)", shown[0])
        self.assertIn("close the program", shown[0])
        shown.clear()

        def bad_type():
            raise ValueError("Format 'dat' is not supported")
        with mock.patch.object(self.ca.messagebox, "showerror", side_effect=lambda t, m, **k: shown.append(m)):
            self.assertFalse(app.guarded_write("/x/scan.dat", bad_type))
        self.assertIn("scan.dat not saved (Format 'dat' is not supported)", shown[0])


class TestAuditFixes236(unittest.TestCase):
    """2.3.6 audit fixes: a label before the icon keeps the line's level; stacking notes are plain."""

    def test_label_before_icon(self):
        import clearv_app
        lv = clearv_app.App._msg_level
        self.assertEqual(lv("JD2461353: ⚠ 3 frames skipped (no star)"), "warn")
        self.assertEqual(lv("22–23 Oct: ✖ stopped"), "error")
        self.assertEqual(lv("Solve: 12 stars matched: ⚠ x"), "note")
        self.assertEqual(lv("Target: fine"), "note")
        self.assertEqual(lv("⚠ plain warn"), "warn")

    def test_stack_notes_plain(self):
        import stack_core as sc
        src = open(sc.__file__, encoding="utf-8").read()
        self.assertNotIn('{cap_note}", True)', src)
        self.assertNotIn('would help.", True)', src)


    def test_skip_share_constant(self):
        import clearv_app
        self.assertAlmostEqual(clearv_app.SKIP_FINDING_SHARE, 0.10)


    def test_few_skips_plain_many_warn(self):
        import types
        import clearv_app
        def stub(n_skip, n_pts):
            st = types.SimpleNamespace()
            st.observations = [types.SimpleNamespace(night="N1", jd=2460000.0 + i) for i in range(n_pts)]
            info = {"skipped": [{"frame": str(i), "why": "could not align"} for i in range(n_skip)]}
            st._night_info = lambda night, create=False: info
            st.recoverable_skips = lambda night: {}
            st._frame_ranges = lambda labs: ",".join(labs)
            return clearv_app.App.comp_skip_text(st, "N1")
        self.assertTrue(stub(2, 98).startswith("2 frames skipped"))     # 2 % of the night: a plain note
        self.assertTrue(stub(20, 80).startswith("⚠ 20 frames skipped"))  # 20 %: a finding


if __name__ == "__main__":
    unittest.main()
