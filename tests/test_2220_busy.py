"""2.2.20 (items 3 and 4): the main window's one busy bar, and the Output page's "No AUID" warning."""
import os
import sys
import tempfile
import threading
import time
import unittest
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import clearv_app as ca  # noqa: E402
import transit_ui  # noqa: E402


class TestWords(unittest.TestCase):
    def test_counted_words(self):
        self.assertEqual(ca.App._progress_words("Photometry", 0, 38, 0.0), "Photometry… 0 of 38")
        text = ca.App._progress_words("Photometry", 12, 38, 60.0)
        self.assertTrue(text.startswith("Photometry… 12 of 38, about "), text)
        self.assertTrue(text.endswith(" left"), text)
        self.assertEqual(ca.App._progress_words("Field scan", 38, 38, 60.0), "Field scan… 38 of 38")

    def test_fit_words(self):
        self.assertEqual(transit_ui.fit_progress_words("burn-in", 600, 1000),
                         "Fitting the transit: MCMC burn-in… 600 of 1,000")
        self.assertEqual(transit_ui.fit_progress_words("steps", 1200, 3000),
                         "Fitting the transit: MCMC step… 1,200 of 3,000")

    def test_mix(self):
        self.assertEqual(ca.mix_color("#000000", "#ffffff", 0.0), "#000000")
        self.assertEqual(ca.mix_color("#000000", "#ffffff", 1.0), "#ffffff")
        self.assertEqual(ca.mix_color("#000000", "#ffffff", 0.5), "#808080")
        self.assertEqual(ca.mix_color("#10283b", "#ffffff", 0.18), "#3b4f5e")


class TestNoAuidDecision(unittest.TestCase):
    def test_cases(self):
        f = ca.no_auid_warning_needed
        # in VSX, no AUID, same star, Variables: warn
        self.assertTrue(f("variables", True, "", "V1234 Cyg", "V1234 Cyg"))
        # has an AUID (from VSX or from a series' meta): no warning
        self.assertFalse(f("variables", True, "000-BCD-123", "V1234 Cyg", "V1234 Cyg"))
        # not in VSX at all: a different case, no warning
        self.assertFalse(f("variables", False, "", "HD 219134", "HD 219134"))
        # looked up for another star
        self.assertFalse(f("variables", True, "", "V1234 Cyg", "RR Lyr"))
        # other modes
        self.assertFalse(f("transits", True, "", "V1234 Cyg", "V1234 Cyg"))
        self.assertFalse(f("discovery", True, "", "V1234 Cyg", "V1234 Cyg"))
        # no Star ID
        self.assertFalse(f("variables", True, "", "", ""))

    def test_lookup_target_marks_vsx(self):
        import photometry_core as core
        old_s, old_v = core._lookup_simbad, core._lookup_vsx
        try:
            core._lookup_simbad = lambda n: {"ra_hours": 1.0, "dec_deg": 2.0, "name": n, "source": "SIMBAD"}
            core._lookup_vsx = lambda n: {"ra_hours": 1.0, "dec_deg": 2.0, "name": n, "source": "VSX", "auid": ""}
            self.assertTrue(core.lookup_target("X")["in_vsx"])

            def no_vsx(n):
                raise ValueError("VSX has no object called X")
            core._lookup_vsx = no_vsx
            self.assertFalse(core.lookup_target("X")["in_vsx"])
        finally:
            core._lookup_simbad, core._lookup_vsx = old_s, old_v


class TestMainWindowBar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test_2220.log")
        cls.app = ca.App()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def pump(self, ms=350):
        end = time.monotonic() + ms / 1000.0
        while time.monotonic() < end:
            self.app.update()
            time.sleep(0.01)

    def test_one_bar_at_left_of_status_strip(self):
        app = self.app
        bar = app.busy_bar
        row = app.status_label.master
        self.assertIs(bar.master, row)
        self.assertEqual(row.pack_slaves()[0], bar)   # the left end
        for gone in ("_star_busy_bar", "_planet_busy_bar", "progress_bar"):
            self.assertFalse(hasattr(app, gone), gone)
        self.assertFalse(hasattr(app, "SPINNER"))
        self.assertFalse(hasattr(app.transit_page, "bar"))

        # no other Progressbar anywhere in the main window (the busy bar's own is the only one)
        def bars(w):
            out = [w] if isinstance(w, ttk.Progressbar) else []
            for c in w.winfo_children():
                out += bars(c)
            return out
        self.assertEqual(bars(app), [bar.bar])

    def test_style_follows_mode(self):
        app = self.app
        st = ttk.Style(app)
        app._apply_theme("transits")
        t1 = st.lookup("Status.Horizontal.TProgressbar", "troughcolor")
        app._apply_theme("variables")
        t2 = st.lookup("Status.Horizontal.TProgressbar", "troughcolor")
        self.assertNotEqual(t1, t2)
        self.assertEqual(t2, ca.mix_color(ca.MODES["variables"]["status"], "#ffffff", 0.18))
        self.assertEqual(st.lookup("Status.Horizontal.TProgressbar", "background"), ca.BUSY_GREEN)

    def test_job_words_and_count(self):
        app = self.app
        self.assertTrue(app._start_job("Photometry on 38 lights…"))
        try:
            self.assertEqual(app.busy_bar.text, "Photometry on 38 lights…")
            app._progress_start(38, "Photometry")
            self.assertEqual(app.busy_bar.text, "Photometry… 0 of 38")
            app._progress(12)
            self.assertTrue(app.busy_bar.text.startswith("Photometry… 12 of 38"))
            self.pump()
            self.assertTrue(app.busy_bar.shown)
            self.assertEqual(str(app.busy_bar.bar.cget("mode")), "indeterminate")
        finally:
            app._end_job()
            app._progress_end("38 points.")
        self.assertFalse(app.busy_bar.busy)
        self.assertFalse(app.busy)
        self.pump(50)
        self.assertFalse(app.busy_bar.shown)

    def test_star_lookup_steps(self):
        app = self.app
        app._lookup_busy_start("star", "TOI-2046", "SIMBAD and VSX")
        self.assertEqual(app.busy_bar.text, "Looking up TOI-2046 at SIMBAD and VSX…")
        self.assertEqual(app.star_busy.get(), "")
        app._lookup_busy_stop("star", name="TOI-2046", soon=True)
        tok = app.busy_begin("Looking up TOI-2046's planets at the NASA Exoplanet Archive…")
        app.update_idletasks()
        app.update()
        self.assertEqual(app.busy_bar.text, "Looking up TOI-2046's planets at the NASA Exoplanet Archive…")
        app.busy_end(tok, soon=True)
        app._lookup_busy_start("planet", "TOI-2046 b", "NASA Exoplanet Archive")
        app.update()
        self.assertEqual(app.busy_bar.text, "Looking up planet TOI-2046 b at the NASA Exoplanet Archive…")
        app._lookup_busy_stop("planet", name="TOI-2046 b")
        app.update()
        self.assertFalse(app.busy_bar.busy)

    def test_lookup_gives_up_with_cross_line(self):
        app = self.app
        old = app.LOOKUP_GIVE_UP_S
        app.LOOKUP_GIVE_UP_S = 0.05
        try:
            app._lookup_busy_start("star", "Nowhere", "SIMBAD and VSX")
            self.pump(300)
        finally:
            app.LOOKUP_GIVE_UP_S = old
        self.assertFalse(app.busy_bar.busy)
        self.assertTrue(app.star_busy.get().startswith("✖ Nowhere: SIMBAD and VSX did not answer"))
        self.assertTrue(app.status.get().startswith("✖"))
        app._lookup_busy_show("star", "")

    def test_slow_save_on_bar(self):
        app = self.app
        path = os.path.join(tempfile.mkdtemp(), "x.csv")
        gate = threading.Event()

        def write():
            gate.wait(5)
            with open(path, "w") as fh:
                fh.write("x\n")
        app._save_in_background(path, write, "light-curve CSV (3 points)")
        self.assertEqual(app.busy_bar.text, "Saving the light-curve CSV (3 points)…")
        self.assertNotIn("◐", app.status.get())
        gate.set()
        app.wait_for_saves(5)
        self.pump(150)
        self.assertFalse(app.busy_bar.busy)
        self.assertTrue(app.status.get().startswith("✔ Saved x.csv"))

    def test_no_auid_line(self):
        app = self.app
        app.set_mode("variables")
        app.star_id.set("V1234 Cyg")
        app.target_auid, app.target_auid_name, app.target_in_vsx = "", "V1234 Cyg", True
        app.show_step(ca.STEP_OUTPUT)
        app._update_no_auid()
        app.update()
        self.assertTrue(app.no_auid_label.winfo_ismapped())
        self.assertEqual(app.no_auid_label.cget("text"), ca.NO_AUID_WARNING)
        app.target_auid = "000-BCD-123"   # a series supplies it
        app._update_no_auid()
        app.update()
        self.assertFalse(app.no_auid_label.winfo_ismapped())
        app.target_auid, app.target_in_vsx = "", False   # not in VSX at all
        app._update_no_auid()
        app.update()
        self.assertFalse(app.no_auid_label.winfo_ismapped())
        # the status line says it once with the report's save
        app.target_in_vsx = True
        app.offer_upload("/tmp/AAVSO_x.txt", "Open AAVSO WebObs…", ca.core.AAVSO_WEBOBS_URL, "AAVSO WebObs",
                         note=ca.NO_AUID_WARNING if app._no_auid_needed() else "")
        self.assertTrue(app.status.get().startswith("⚠ No AUID: WebObs will reject this report."))
        app.star_id.set("")
        app._update_no_auid()
        app.show_step(0)


if __name__ == "__main__":
    unittest.main()
