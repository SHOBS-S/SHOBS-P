"""2.4.0 fit-in F4-3, part 4: the sheet rows read from the sheet itself (the first sweep matched some ids by place):
m812 a report in a time system SHOBS-P does not convert keeps its own label and gets no O - C; m856 the NEB check's
error box; m842 the short ExoFOP hint; m767 the one-line scatter note; m673 the alternative ephemeris' expected form;
x256 the catalog entry with no period."""
import inspect
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

import multinight as mn  # noqa: E402
import transit_core as tc  # noqa: E402
import transit_ui as tui  # noqa: E402
import test_240_fitin4_3c as C  # noqa: E402


class OwnTimeSystem(unittest.TestCase):
    def rep(self, dt):
        t = 2460000.3 + np.arange(30) / 500.0
        return {"header": {"DATE_TYPE": dt}, "t": t.copy(), "flux": np.ones(30), "err": np.full(30, 0.002)}

    def test_unknown_system_keeps_its_name_and_says_what_is_converted(self):
        rep = self.rep("MJD UTC")
        t0 = rep["t"].copy()
        tui.TransitPage._report_time_system(None, rep)
        self.assertEqual(rep["date_type"], "MJD_UTC")
        self.assertTrue(np.array_equal(rep["t"], t0))                       # never shifted
        self.assertTrue(rep["time_note"].startswith("⚠ The report's times are MJD_UTC, which SHOBS-P does not convert"))
        self.assertIn("it converts HJD_UTC and JD_UTC", rep["time_note"])
        self.assertIn("Tc is given in MJD_UTC", rep["time_note"])
        self.assertIn("no O − C is stated", rep["time_note"])
        self.assertNotIn("fits them as BJD_TDB", rep["time_note"])
        self.assertIn("Save the report again with BJD_TDB times", rep["time_note"])

    def test_bjd_and_hjd_are_still_bjd_tdb(self):
        for dt in ("BJD_TDB", "", "HJD_UTC"):
            rep = self.rep(dt)
            rep["planet"] = {}
            tui.TransitPage._report_time_system(None, rep)
            self.assertEqual(rep["date_type"], "BJD_TDB", dt)

    def test_page_helper(self):
        f = tui.TransitPage._own_time_system
        self.assertEqual(f(types.SimpleNamespace(report=None)), "")
        self.assertEqual(f(types.SimpleNamespace(report={"date_type": "BJD_TDB"})), "")
        self.assertEqual(f(types.SimpleNamespace(report={"date_type": "JD_UTC"})), "JD_UTC")
        self.assertEqual(f(types.SimpleNamespace(report={"date_type": "MJD_UTC"})), "MJD_UTC")

    def test_time_label(self):
        self.assertEqual(tui._time_label(False), "BJD_TDB")
        self.assertEqual(tui._time_label(""), "BJD_TDB")
        self.assertEqual(tui._time_label(True), "JD_UTC")
        self.assertEqual(tui._time_label("JD_UTC"), "JD_UTC")
        self.assertEqual(tui._time_label("MJD_UTC"), "MJD_UTC")

    def test_fit_csv_header_carries_the_label_and_no_o_minus_c(self):
        r = C.result()
        r.update(error_scale=1.2, rchi2=1.3, beta=1.05)
        own = tui.fit_csv_comments(r, {"filter": "CV"}, "MJD_UTC")
        self.assertIn(" MJD_UTC; verdict", own[0])
        self.assertNotIn("BJD_TDB;", own[0])
        bjd = tui.fit_csv_comments(r, {"filter": "CV"})
        self.assertIn(" BJD_TDB; verdict", bjd[0])
        # the O - C line is the only one a non-BJD file drops
        self.assertTrue(set(own[1:]) <= set(bjd[1:]))

    def test_files_notes_and_other_windows(self):
        src = inspect.getsource(tui.TransitPage)
        self.assertIn('if o is not None and state != "none" and not own:', src)
        self.assertIn("write_fit_csv(fit_path, self.result, self._own_time_system(), meta=self.meta_for_files)", src)
        self.assertIn("the page's report has {own} times, which SHOBS-P does not convert", inspect.getsource(mn))
        import clearv_app
        ex = inspect.getsource(clearv_app.App.save_exoclock)
        self.assertIn("ExoClock file is written (it would mislabel the times)", ex)


class Wording(unittest.TestCase):
    def test_m856_neb_box_is_an_error_that_leads_with_the_action(self):
        src = inspect.getsource(tui.NebWindow.__init__)
        self.assertIn('messagebox.showerror("SHOBS-P", "The NEB check needs the target\'s RA and Dec. On the Input '
                      'page press "', src)
        self.assertNotIn("needs the target's RA/Dec: {exc}", src)

    def test_m842_exofop_hint_is_two_lines(self):
        src = inspect.getsource(tui.TransitPage.open_exofop)
        self.assertIn("chapter 19 (Reports)", src)
        self.assertNotIn("Data tag: YYYYMMDD", src)
        with open(os.path.join(ROOT, "docs", "manual", "19-transit-reports.md"), encoding="utf-8") as fh:
            ch19 = fh.read()
        self.assertIn("To upload it:", ch19)
        self.assertIn("YYYYMMDD_username_description_n", ch19)

    def test_m767_scatter_note_is_one_line(self):
        src = inspect.getsource(tc.transit_flags)
        self.assertIn("the fit widened its error bars to match.", src)
        self.assertNotIn("noise the error model leaves out, such as thin cloud", src)
        with open(os.path.join(ROOT, "docs", "manual", "18-inconclusive-or-none.md"), encoding="utf-8") as fh:
            self.assertIn("12/14-bit data scaled to 16 bits", fh.read())          # the causes live in the manual

    def test_m673_alternative_ephemeris_form(self):
        self.assertIn("T0 as a BJD_TDB", mn.ALT_EPH_FORM)
        self.assertIn("period in days", mn.ALT_EPH_FORM)
        self.assertIn("{ALT_EPH_FORM}", inspect.getsource(mn))
        with self.assertRaises(ValueError):
            mn.parse_ephemeris("", "", "2.5", "")
        e = mn.parse_ephemeris("2460123.45678", "", "2.5", "")                    # a ± may be left empty
        self.assertIsNone(e["t0_err"])

    def test_x256_no_period_says_what_to_do(self):
        with self.assertRaises(ValueError) as ctx:
            tc.complete_planet({"name": "X b"})
        self.assertEqual(str(ctx.exception),
                         "the catalog entry has no period: type the period in the Planet boxes on the Input page")


if __name__ == "__main__":
    unittest.main()
