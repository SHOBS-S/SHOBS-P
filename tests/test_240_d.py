"""2.4.0 step 2 (#102): the Findings panel. Warnings and cautions reach the user without the Log; the Log stays a
full troubleshooting record (every line, findings tagged [WARN] / [ERROR] in the log file)."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import findings  # noqa: E402

try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


HINTS = [
    {"section": "Anywhere", "patterns": ["… not saved (…). Save to another folder, or close the program that has the "
                                         "file open."], "todo": "Do what it says, then save again."},
    {"section": "Photometry", "patterns": ["Not on this frame, so taken out of tonight's comps (not measured): C2 …"],
     "todo": "Nothing, if the other comps are enough."},
]


class TestModel(unittest.TestCase):
    def test_notes_never_come_here(self):
        f = findings.FindingsList(hints=[])
        self.assertIsNone(f.add("note", "Photometry done."))
        self.assertEqual(len(f), 0)
        self.assertEqual(f.link_text(), "")

    def test_add_count_and_link(self):
        f = findings.FindingsList(hints=[])
        f.add("warn", "⚠ one", step="Photometry on # lights")
        f.add("warn", "⚠ two\nmore detail for the Log", step="Photometry on # lights")
        self.assertEqual(len(f), 2)
        self.assertEqual(f.items[1].text, "⚠ two")   # first line only, word for word
        self.assertEqual(f.link_text(), "⚠ 2 to look at")
        f.add("error", "✖ Fit failed.", step="Fitting the transit")
        self.assertEqual(f.link_text(), "✖ 3 to look at")   # any ✖ leads with ✖

    def test_same_line_same_step_is_counted_not_repeated(self):
        f = findings.FindingsList(hints=[])
        f.add("warn", "⚠ same", step="a")
        f.add("warn", "⚠ same", step="a")
        f.add("warn", "⚠ same", step="b")
        self.assertEqual(len(f), 2)
        self.assertEqual(f.items[0].times, 2)

    def test_rerun_replaces_only_that_step(self):
        f = findings.FindingsList(hints=[])
        f.add("warn", "⚠ from photometry", step="Photometry on # lights")
        f.add("warn", "⚠ from the solve", step="Solving")
        f.add("warn", "⚠ outside a job")
        f.start_step("Photometry on # lights")
        self.assertEqual([x.text for x in f.items], ["⚠ from the solve", "⚠ outside a job"])
        f.start_step("")   # a job with no key never wipes the outside-a-job findings
        self.assertEqual(len(f), 2)

    def test_drop_step_by_level_keeps_errors(self):
        """(review S1) a stopped job's ⚠ go; its ✖ (why it stopped) stays."""
        f = findings.FindingsList(hints=[])
        f.add("warn", "⚠ w", step="Photometry on # lights")
        f.add("error", "✖ Photometry stopped by an error (boom).", step="Photometry on # lights")
        f.drop_step("Photometry on # lights", level="warn")
        self.assertEqual([x.text for x in f.items], ["✖ Photometry stopped by an error (boom)."])

    def test_dismiss_and_clear(self):
        f = findings.FindingsList(hints=[])
        a = f.add("warn", "⚠ a")
        f.add("warn", "⚠ b")
        calls = []
        f.listeners.append(lambda: calls.append(1))
        f.dismiss(a)
        self.assertEqual([x.text for x in f.items], ["⚠ b"])
        f.clear()
        self.assertEqual(len(f), 0)
        self.assertEqual(len(calls), 2)

    def test_step_key_ignores_counts(self):
        self.assertEqual(findings.step_key("Photometry on 165 lights…"), findings.step_key("Photometry on 160 lights…"))
        self.assertNotEqual(findings.step_key("Photometry on 165 lights…"),
                            findings.step_key("Photometry on 12 stacks…"))


class TestHints(unittest.TestCase):
    def test_match(self):
        self.assertEqual(findings.match_hint(
            "✖ AAVSO_x.txt not saved (Permission denied). Save to another folder, or close the program that has the "
            "file open.", HINTS), "Do what it says, then save again.")
        self.assertEqual(findings.match_hint(
            "⚠ Not on this frame, so taken out of tonight's comps (not measured): C2 TYC 4006-1008-1", HINTS),
            "Nothing, if the other comps are enough.")
        self.assertEqual(findings.match_hint("⚠ something the manual does not list", HINTS), "")

    def test_example_values_match_any_value(self):
        """(review S3) chapter 23 writes example values (C2, 60 s, N frames, (x, y)); any value matches them."""
        rows = [{"patterns": ["Not on this frame, so taken out of tonight's comps (not measured): C2 …"], "todo": "A"},
                {"patterns": ["Darks 60 s, lights 120 s: the master dark is scaled ×2 …"], "todo": "B"},
                {"patterns": ["Check marker at (x, y) is N px from the star …"], "todo": "C"}]
        self.assertEqual(findings.match_hint(
            "⚠ Not on this frame, so taken out of tonight's comps (not measured): C3 TYC 1-2-3", rows), "A")
        self.assertEqual(findings.match_hint(
            "Darks 30 s, lights 180 s: the master dark is scaled ×6 for the lights", rows), "B")
        self.assertEqual(findings.match_hint("⚠ Check marker at (512.3, 300.1) is 14 px from the star there", rows), "C")
        self.assertEqual(findings.match_hint("⚠ Darks are fine", rows), "")

    def test_label_before_the_icon(self):
        """(review S4) "Transit fit: ⚠ …" matches its row and is shown with one icon, the label kept."""
        rows = [{"patterns": ["The report's times are JD_UTC, not BJD_TDB …"], "todo": "T"}]
        line = "Transit fit: ⚠ The report's times are JD_UTC, not BJD_TDB, and the star's position is not known"
        self.assertEqual(findings.match_hint(line, rows), "T")
        self.assertEqual(findings.shown_text(line),
                         "Transit fit: The report's times are JD_UTC, not BJD_TDB, and the star's position is not known")
        self.assertEqual(findings.shown_text("⚠ plain"), "plain")

    def test_long_line_is_quick(self):
        """(review N3) a pathological long line cannot stall the UI: only its first characters are matched."""
        import time as _t
        rows = [{"patterns": ["a b c d e f g h i j k l m … n o p q r s"], "todo": "x"}]
        t0 = _t.monotonic()
        findings.match_hint("⚠ " + "a b c d e f g h i j k l m " * 2000, rows)
        self.assertLess(_t.monotonic() - t0, 1.0)

    def test_short_patterns_never_match(self):
        self.assertEqual(findings.match_hint("⚠ outlier at frame 12", [{"patterns": ["outlier"], "todo": "x"}]), "")

    def test_plain_md(self):
        self.assertEqual(findings._plain_md("Press **Solve again**; see [24. Troubleshooting](24-troubleshooting.md) "
                                            "and `pip_log.txt`, *not* that."),
                         "Press Solve again; see 24. Troubleshooting and pip_log.txt, not that.")

    def test_message_help_json_matches_chapter_23(self):
        """Edit chapter 23, then run tools/build_message_help.py: the panel and the manual must say the same thing."""
        import build_message_help
        with open(findings.HELP_FILE, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk, build_message_help.build(),
                         "message_help.json is out of date: run python tools/build_message_help.py")
        self.assertGreater(len(on_disk["rows"]), 100)

    def test_missing_help_file_is_harmless(self):
        self.assertEqual(findings.load_hints(os.path.join(tempfile.gettempdir(), "no_such_help.json")), [])


def _app():
    import clearv_app as ca
    ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test240d.log")
    try:
        os.remove(ca.LOG_FILE)
    except OSError:
        pass
    app = ca.App()
    return ca, app


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ca, cls.app = _app()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.app.findings.clear()
        self.app.update()

    def _job(self, label, lines):
        app = self.app
        self.assertTrue(app._start_job(label))
        for line in lines:
            app.log(line)
        app._end_job()
        app.update()

    def test_job_findings_reach_the_panel_and_link(self):
        app = self.app
        self._job("Photometry on 165 lights…", ["⚠ one", "a note", "⚠ two"])
        self.assertEqual([f.text for f in app.findings.items], ["⚠ one", "⚠ two"])
        self.assertEqual(app.findings_var.get(), "⚠ 2 to look at")
        self.assertTrue(app.findings_link.winfo_manager())
        # the summary line is not a finding of its own
        self.assertEqual(app.status.get(), "⚠ 2 things need a look (click to see them)")
        self.assertEqual(len(app.findings), 2)
        # a later note replaces the status line, but the link stays
        app.log("Saved the series.")
        self.assertEqual(app.status.get(), "Saved the series.")
        self.assertEqual(app.findings_var.get(), "⚠ 2 to look at")

    def test_rerun_replaces_and_clean_rerun_clears(self):
        app = self.app
        self._job("Photometry on 165 lights…", ["⚠ old"])
        self._job("Solving: fetching stars (42′ field)…", ["⚠ solve"])
        self._job("Photometry on 160 lights…", ["⚠ new"])
        self.assertEqual(sorted(f.text for f in app.findings.items), ["⚠ new", "⚠ solve"])
        self._job("Photometry on 160 lights…", ["Photometry done."])
        self.assertEqual([f.text for f in app.findings.items], ["⚠ solve"])
        app.findings.clear()
        app.update()
        self.assertFalse(app.findings_link.winfo_manager())
        self.assertEqual(app.findings_var.get(), "")

    def test_outside_a_job_findings_stay(self):
        app = self.app
        app.log("⚠ outside a job")
        self._job("Testing apertures…", ["fine"])
        self.assertEqual([f.text for f in app.findings.items], ["⚠ outside a job"])

    def test_discarded_job_leaves_the_panel(self):
        app = self.app
        self.assertTrue(app._start_job("Building calibration masters…"))
        app.log("⚠ raised before the cancel")
        app._end_job()
        app._discard_job_findings()
        app.log("Calibration canceled.")
        app.update()
        self.assertEqual(len(app.findings), 0)

    def test_stopped_by_error_keeps_its_cross(self):
        """(review S1) photometry stopped by an error logs its ✖ inside the job, then discards the job's findings."""
        app = self.app
        self.assertTrue(app._start_job("Photometry on 12 lights…"))
        app.log("⚠ a caution before the error")
        app._end_job(defer_close=True)
        app.log("✖ Photometry stopped by an error (boom). Nothing was added to the series.")
        app._discard_job_findings()
        app._close_job_findings(force=True)
        app.update()
        self.assertEqual([f.text for f in app.findings.items],
                         ["✖ Photometry stopped by an error (boom). Nothing was added to the series."])
        self.assertEqual(app.findings_var.get(), "✖ 1 to look at")

    def test_same_line_twice_counts_once(self):
        """(review S6) the status line's count agrees with the panel."""
        self._job("Testing apertures…", ["⚠ same", "⚠ same", "⚠ other"])
        self.assertEqual(self.app.status.get(), "⚠ 2 things need a look (click to see them)")
        self.assertEqual(self.app.findings_var.get(), "⚠ 2 to look at")
        self._job("Testing apertures…", ["⚠ same", "⚠ same"])
        self.assertEqual(self.app.status.get(), "⚠ same")

    def test_status_line_is_a_link_only_for_a_finding(self):
        """(review N5)"""
        app = self.app
        app.log("⚠ needs a look")
        self.assertTrue(app._status_opens_findings())
        app.status.set("Saved the series.")
        self.assertFalse(app._status_opens_findings())
        self.assertEqual(str(app.status_label.cget("cursor")), "")

    def test_summary_line_not_tagged_in_log_file(self):
        """(review N1) the count line is a note in the log file; only real findings are [WARN] / [ERROR]."""
        self._job("Testing apertures…", ["⚠ first x", "⚠ second x"])
        with open(self.ca.LOG_FILE, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("[WARN] ⚠ first x", text)
        self.assertNotIn("[WARN] ⚠ 2 things need a look", text)
        self.assertIn("⚠ 2 things need a look", text)

    def test_log_file_tags(self):
        app = self.app
        app.log("plain note line")
        app.log("⚠ a caution line")
        app.log("✖ an error line")
        with open(self.ca.LOG_FILE, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("  plain note line", text)
        self.assertNotIn("[NOTE]", text)
        self.assertIn("[WARN] ⚠ a caution line", text)
        self.assertIn("[ERROR] ✖ an error line", text)
        # the Log box still shows the line as it was
        self.assertIn("⚠ a caution line\n", app.log_box.get("1.0", "end"))

    def test_window_lists_dismisses_and_updates(self):
        app = self.app
        app.log("✖ AAVSO_x.txt not saved (Permission denied). Save to another folder, or close the program that has "
                "the file open.")
        win = app.open_findings()
        app.update()
        self.assertTrue(win.alive())
        body = win.text.get("1.0", "end")
        self.assertIn("AAVSO_x.txt not saved", body)
        self.assertIn("What to do: Do what it says, then save again.", body)
        self.assertEqual(win.head.get(), "1 thing needs a look")
        self.assertIs(app.open_findings(), win)   # one window: a second click brings it forward
        app.log("⚠ a second one")
        app.update()
        self.assertEqual(win.head.get(), "2 things need a look")
        app.findings.dismiss(app.findings.items[0])
        app.update()
        self.assertNotIn("AAVSO_x.txt", win.text.get("1.0", "end"))
        app.findings.clear()
        app.update()
        self.assertEqual(win.head.get(), "Nothing needs a look.")
        win.close()
        app.update()
        self.assertFalse(win.alive())
        self.assertNotIn(win.refresh_soon, app.findings.listeners)


if __name__ == "__main__":
    unittest.main()
