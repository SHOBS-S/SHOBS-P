"""2.3.6 (#79 messages, part 1): leveled log and job summary (item 1), findings travel with the result (item 2), one
vocabulary (item 7), the check-star linear-limit wording nit, and a UI-step error that releases the run lock."""
import os
import sys
import tempfile
import unittest

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
    ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test236.log")
    app = ca.App()
    return ca, app


class TestLevels(unittest.TestCase):
    def test_level_from_icon(self):
        import clearv_app as ca
        self.assertEqual(ca.App._msg_level("⚠ comp off the edge"), "warn")
        self.assertEqual(ca.App._msg_level("  ✖ Fit failed."), "error")
        self.assertEqual(ca.App._msg_level("Error: boom"), "error")
        self.assertEqual(ca.App._msg_level("Photometry done in 2:08."), "note")


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestJobSummary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ca, cls.app = _app()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def _job(self, lines):
        app = self.app
        self.assertTrue(app._start_job("Test job"))
        for line in lines:
            app.log(line)
        app._end_job()
        app.update()   # the summary closes at the next idle moment
        return app.status.get()

    def test_note_after_warn_keeps_warn_on_status(self):
        app = self.app
        self.assertTrue(app._start_job("Test job"))
        app.log("⚠ C2 is missing in 24 frames (off the frame edge; frames 61–84)")
        app.log("Photometry done in 2:08. 240 points.")
        self.assertTrue(app.status.get().startswith("⚠ C2 is missing"))
        app._end_job()
        app.update()

    def test_one_warn_ends_on_that_line(self):
        st = self._job(["first note", "⚠ only one thing", "last note"])
        self.assertEqual(st, "⚠ only one thing")
        self.assertEqual(self.app.last_job_findings, ["⚠ only one thing"])

    def test_several_warns_counted(self):
        st = self._job(["⚠ one", "note", "⚠ two", "⚠ three"])
        self.assertEqual(st, "⚠ 3 things need a look (see the Log)")
        self.assertEqual(len(self.app.last_job_findings), 3)

    def test_no_warns_status_is_last_line(self):
        st = self._job(["note a", "Photometry done."])
        self.assertEqual(st, "Photometry done.")
        self.assertEqual(self.app.last_job_findings, [])

    def test_log_only_leaves_status(self):
        app = self.app
        app.status.set("keep me")
        app._log_only("detail for the record")
        self.assertEqual(app.status.get(), "keep me")

    def test_lines_logged_just_after_end_job_still_count(self):
        app = self.app
        self.assertTrue(app._start_job("Test job"))
        app._end_job()
        app.log("⚠ end-of-run check")   # as _photometry_done logs right after releasing the lock
        app.update()
        self.assertEqual(app.last_job_findings, ["⚠ end-of-run check"])

    def test_findings_lines_and_note(self):
        app = self.app
        app.run_findings = ["⚠ C2 is missing in 24 frames", "⚠ flats taken at another angle"]
        lines = app.run_findings_lines()
        self.assertTrue(lines[0].startswith("Findings from the photometry run (2"))
        self.assertEqual(lines[1], "  - ⚠ C2 is missing in 24 frames")
        self.assertEqual(app.run_findings_note(), "2 findings from the run, see the settings file")
        # release review S3: only with points of the run's night
        import types
        app.run_findings_night = "JD2461353"
        pts = [types.SimpleNamespace(night="JD2461353")]
        self.assertEqual(app._findings_report_note(points=pts), "; 2 findings from the run, see the settings file")
        self.assertEqual(app._findings_report_note(points=[types.SimpleNamespace(night="JD1")]), "")
        app.run_findings = []
        self.assertEqual(app.run_findings_lines(), [])
        self.assertEqual(app._findings_report_note(), "")

    def test_ui_step_error_keeps_lock_and_says_so(self):
        """Review MF1: a worker may still run, so the lock stays; the error is said."""
        app = self.app
        self.assertTrue(app._start_job("Test job"))

        def boom():
            raise RuntimeError("bad step")
        app.call_ui(boom)
        import time
        for _ in range(40):
            time.sleep(0.03)
            app.update()
            if "bad step" in app.status.get():
                break
        self.assertTrue(app.busy)
        self.assertIn("bad step", app.status.get())
        app._end_job()
        app.update()

    def test_explicit_close_survives_a_nested_event_loop(self):
        """Review MF2: a done-step with a dialog (nested loop) still counts the lines after it."""
        app = self.app
        self.assertTrue(app._start_job("Test job"))
        app._end_job(defer_close=True)
        app.log("⚠ before the dialog")
        app.update()   # what a messagebox's own loop does
        app.log("⚠ after the dialog")
        app._close_job_findings(force=True)
        self.assertEqual(app.last_job_findings, ["⚠ before the dialog", "⚠ after the dialog"])

    def test_next_job_does_not_show_the_last_jobs_warn(self):
        """Review S2."""
        app = self.app
        self.assertTrue(app._start_job("a"))
        app.log("⚠ from job a")
        app._end_job()
        self.assertTrue(app._start_job("b"))   # before the idle close
        app.log("b went fine")
        app._end_job()
        app.update()
        self.assertEqual(app.status.get(), "b went fine")

    def test_end_job_twice_or_alone_is_harmless(self):
        app = self.app
        app._end_job()
        app._end_job()
        app.update()
        self.assertFalse(app.busy)

    def test_error_summary(self):
        st = self._job(["⚠ one", "✖ Fit failed."])
        self.assertTrue(st.startswith("✖ Fit failed."), st)
        self.assertIn("1 more in the Log", st)

    def test_findings_cleared_by_new_reduction(self):
        app = self.app
        app.run_findings = ["⚠ x"]
        app.new_reduction(ask=False)
        self.assertEqual(app.run_findings, [])

    def test_variables_note_lead(self):
        app = self.app
        import types
        app.run_findings, app.run_findings_night = ["⚠ x"], "JD2461353"
        self.assertEqual(app._findings_report_note(lead=False, points=[types.SimpleNamespace(night="JD2461353")]),
                         "1 finding from the run, see the settings file")
        app.run_findings = []


class TestVocabulary(unittest.TestCase):
    def test_no_level_prefixes_in_log_lines(self):
        import re
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("clearv_app.py", "stack_ui.py", "multinight.py"):
            with open(os.path.join(here, name), encoding="utf-8") as fh:
                text = fh.read()
            hits = re.findall(r'self\.(?:log|_log_only)\(f?"(?:Caution|Careful|Note|Warning): ', text)
            self.assertEqual(hits, [], name)

    def test_check_star_over_linear_limit_is_a_plain_note_naming_the_check(self):
        import clearv_app as ca
        src = open(ca.__file__, encoding="utf-8").read()
        i = src.index("def _linearity_note")
        body = src[i:src.index("def _edge_note", i)]
        self.assertIn('if role == "check"', body)
        self.assertIn("check-star scatter", body)


if __name__ == "__main__":
    unittest.main()
