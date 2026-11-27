"""2.4.0 fit-in F4-3, part 5: the #115 retune on the two real scan nights (the level and the floor stand, the numbers
are written down), m663 / m664 (Multi-night timing warnings say what to do), and the Transit fit page's note when a
new fit's files are saved while the AAVSO exoplanet report on disk is the earlier fit's (nit, 6 Nov)."""
import inspect
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import multinight as mn  # noqa: E402
import photometry_core as core  # noqa: E402
import transit_ui as tui  # noqa: E402


class Retune(unittest.TestCase):
    def test_level_and_floor_stand(self):
        self.assertEqual(core.SUSPECT_EXCESS_CHI2, 2.5)
        self.assertEqual(core.SCAN_FLAT_FLOOR_MAG, 0.003)

    def test_the_numbers_are_written_down(self):
        with open(os.path.join(ROOT, "photometry_core.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("RETUNE on real nights (fit-in F4, 26 Nov 2026)", src)
        self.assertNotIn("It has NOT been tuned on scan nights yet", src)
        with open(os.path.join(ROOT, "docs", "manual", "20-scan-a-field.md"), encoding="utf-8") as fh:
            ch20 = fh.read()
        self.assertIn("**Why 2.5.**", ch20)
        self.assertIn("S033 read 4.3 and 5.9", ch20)

    def test_s033_like_values_are_suspects_and_quiet_ones_are_not(self):
        level = core.chi2_level(core.SUSPECT_EXCESS_CHI2, 97)
        for s033 in (2.66, 2.78, 3.00, 4.27, 5.85):          # every value S033 has shown on its nights
            self.assertEqual(core.suspect_reason(2.05, s033, chi2_limit=level), "own errors")
        for quiet in (1.00, 1.45, 2.11, 2.33):               # the ordinary stars' median, p95, p99, p99.5
            self.assertEqual(core.suspect_reason(1.2, quiet, chi2_limit=level), "")


def timing(oc, t0_err, key="input"):
    rows = [{"oc_min": v, "fit_err_min": 0.5, "pred_err_min": 1.0} for v in oc]
    return {"tc": list(range(len(oc))), "labels": [f"N{i + 1}" for i in range(len(oc))],
            key: {"eph": {"source": "NASA Exoplanet Archive", "t0_err": t0_err}, "rows": rows}}


class TimingWarnings(unittest.TestCase):
    def test_m664_leads_with_the_action(self):
        out = mn.timing_statements(timing([6.0, 6.3, 5.8], 1e-4))
        self.assertEqual(len(out), 1)
        level, text = out[0]
        self.assertEqual(level, "warn")
        self.assertTrue(text.startswith("⚠ Check the time system of every night first (BJD_TDB, and the ephemeris "
                                        "in BJD_TDB too): against the input ephemeris (NASA Exoplanet Archive) the "
                                        "nights are late by +6.0 ± 0.3 min"), text)
        self.assertIn("more than that ephemeris's own uncertainty (±1.0 min at these epochs).", text)
        self.assertTrue(text.endswith("A real period change needs many more epochs to show."))

    def test_m663_names_the_comparison(self):
        out = mn.timing_statements(timing([-6.0, -6.3, -5.8], None, key="alt"))
        level, text = out[0]
        self.assertEqual(level, "warn")
        self.assertIn("the nights are early by -6.0 ± 0.3 min", text)
        self.assertIn("cannot tell an out-of-date ephemeris from a timing problem. " + mn.TIMING_COMPARE, text)
        self.assertIn("type Exoplanet Watch's or ExoClock's (with its ±) as the alternative", mn.TIMING_COMPARE)
        self.assertNotIn("or the joint fit's own", text)

    def test_on_time_and_within_the_ephemeris_are_untouched(self):
        self.assertEqual(mn.timing_statements(timing([0.2, -0.1, 0.3], 1e-4))[0][0], "ok")
        self.assertEqual(mn.timing_statements(timing([1.8, 1.9, 1.7], 1e-4))[0][0], "info")

    def test_chapter_23_has_both(self):
        with open(os.path.join(ROOT, "docs", "manual", "23-messages-explained.md"), encoding="utf-8") as fh:
            ch23 = fh.read()
        self.assertIn("⚠ Check the time system of every night first (BJD_TDB, and the ephemeris in BJD_TDB too)", ch23)
        self.assertIn(mn.TIMING_COMPARE, ch23)


class AavsoOlderFit(unittest.TestCase):
    def page(self, saved, serial=7, nights=("JD2461000",), result=True):
        logged = []
        app = types.SimpleNamespace(log=lambda text, keep_status=False: logged.append((text, keep_status)))
        p = types.SimpleNamespace(app=app, result={"_serial": serial} if result else None,
                                  _fit_nights=lambda: list(nights))
        if saved is not None:
            p._aavso_saved = saved
        return p, logged

    def note(self, p):
        return tui.TransitPage._aavso_older_fit_note(p)

    def test_no_report_saved_or_no_fit(self):
        p, logged = self.page(None)
        self.assertEqual(self.note(p), "")
        p, logged = self.page({"serial": 3, "path": "/o/AAVSO_X.txt", "nights": ("JD2461000",)}, result=False)
        self.assertEqual(self.note(p), "")
        self.assertEqual(logged, [])

    def test_report_of_this_fit(self):
        p, logged = self.page({"serial": 7, "path": "/o/AAVSO_X.txt", "nights": ("JD2461000",)})
        self.assertEqual(self.note(p), "")

    def test_report_of_the_fit_before(self):
        p, logged = self.page({"serial": 3, "path": os.path.join("o", "AAVSO_X_SHOBS-P.txt"),
                               "nights": ("JD2461000",)})
        note = self.note(p)
        self.assertEqual(note, " The AAVSO exoplanet report saved earlier (AAVSO_X_SHOBS-P.txt) is of the fit before "
                               "this one: press Save AAVSO exoplanet report… to save this fit's.")
        self.assertEqual(logged, [(note.strip(), True)])       # in the Log too, the save's status line kept

    def test_another_nights_report_is_not_named(self):
        p, logged = self.page({"serial": 3, "path": "/o/AAVSO_X.txt", "nights": ("JD2460990",)})
        self.assertEqual(self.note(p), "")

    def test_the_saves_use_it(self):
        self.assertIn("self._aavso_saved = {\"serial\": fit_serial(self.result), \"path\": path,",
                      inspect.getsource(tui.TransitPage.save_aavso))
        self.assertIn("self._aavso_older_fit_note()", inspect.getsource(tui.TransitPage.save_png))
        self.assertIn("self._aavso_older_fit_note()", inspect.getsource(tui.TransitPage.save_csv))


if __name__ == "__main__":
    unittest.main()
