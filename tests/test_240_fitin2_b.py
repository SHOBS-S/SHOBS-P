"""2.4.0 fit-in F2 (#79 item 6, builder B): grey out instead of scold on the Transit fit page, the Variables Output
page, Trim, the Multi-night window and the open windows, plus the Auto (BIC) detrend busy-bar words (banked nit 9).

transit    the saves are greyed before a fit and while the comp check runs (m827 m838 m845 m045 m844); Fit transit
           pressed during the ephemeris lookup waits for it (m787); Trim and refit greys with the reason (fewer than
           20 left) and takes To before From as the same span (m819); job buttons grey during a job.
variables  every Output button that needs a light curve is greyed without one and live with one; Night notes… and
           Remove night… use the night picked in the night table (m535), greyed when none is picked.
trim       Trim greys live with the reason as From / To are typed (fewer than 20 left; a box not understood).
multinight Fit nights pressed during the lookup waits for it (m681); a lookup that found no T0 is a plain note (m698).
windows    Time of max/min's Elements from VSX needs Star ID (m628).
detrend    the busy-bar words name the choice and the flip step's second fit; the fit's numbers do not change."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import gate  # noqa: E402
import photometry_core as core  # noqa: E402
import transit_core as tc  # noqa: E402
import transit_ui as tui  # noqa: E402
import trim_ui  # noqa: E402

try:
    import tkinter  # noqa: F401
    tkinter.Tk().destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False

if HAVE_TK:
    import test_240_m as M  # noqa: E402
    from test_240_m import A, B, _old_meta, _points  # noqa: E402
    Base = M.AppCase
else:
    Base = unittest.TestCase

CAT = {"TYC 4006-1008-1": 8.728, "Gaia DR3 439074923286442112": 11.31}
N1, N2 = "JD2460950", "JD2460951"


def disabled(w):
    return "disabled" in w.state()


def find_button(root, text):
    stack = [root]
    while stack:
        w = stack.pop()
        try:
            if w.winfo_class() == "TButton" and w.cget("text") == text:
                return w
        except Exception:
            pass
        stack.extend(w.winfo_children())
    return None


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestTransitPage(Base):
    def setUp(self):
        super().setUp()
        tp = self.app.transit_page
        tp.result, tp.report, tp._checking, tp._fitting = None, None, False, False
        self.app.busy = False

    def tearDown(self):
        tp = self.app.transit_page
        tp.result, tp.report, tp._checking, tp._fitting = None, None, False, False
        self.app.busy = False
        self.app.gates.refresh()
        super().tearDown()

    def saves(self):
        tp = self.app.transit_page
        return [tp.save_aavso_button, tp.save_package_button, tp.save_exoclock_button, tp.save_png_button]

    def test_saves_greyed_before_a_fit_and_during_the_comp_check(self):
        app, tp = self.app, self.app.transit_page
        app.gates.refresh()
        for b in self.saves() + [tp.corner_button, tp.save_csv_button]:
            self.assertTrue(disabled(b), b.cget("text"))
        self.assertEqual(app.gates.reason(tp.save_aavso_button), tui.FIT_FIRST)
        tp.result = {"summary": {}, "chain": {"flat": np.zeros((4, 2))}}
        app.gates.refresh()
        for b in self.saves() + [tp.corner_button, tp.save_csv_button]:
            self.assertFalse(disabled(b), b.cget("text"))
        # m844: greyed while the comp check runs, with its own words; not the CSV or the corner plot
        tp._checking = True
        app.busy = True
        app.gates.refresh()
        for b in self.saves():
            self.assertTrue(disabled(b))
            self.assertEqual(app.gates.reason(b), tui.COMP_CHECK_RUNNING)
        self.assertFalse(disabled(tp.save_csv_button))
        # the Fit button says the comp check too, not only "another step"
        self.assertEqual(app.gates.reason(tp.fit_button), tui.COMP_CHECK_RUNNING)
        # a backstop press: a status line, no box
        with mock.patch.object(tui.messagebox, "showinfo") as box:
            tp.save_aavso()
            app.save_exoclock()
        box.assert_not_called()
        self.assertIn(tui.COMP_CHECK_RUNNING, app.status.get())

    def test_job_buttons_greyed_during_a_job(self):
        app, tp = self.app, self.app.transit_page
        app.gates.refresh()
        self.assertFalse(disabled(tp.fit_button))
        app.busy = True
        app.gates.refresh()
        for b in (tp.fit_button, tp.multinight_button, tp.comp_health_button, tp.use_comps_button,
                  tp.robust_button, tp.clear_trim_button, tp.open_report_button):
            self.assertTrue(disabled(b), b.cget("text"))
        self.assertEqual(app.gates.reason(tp.fit_button), gate.BUSY_REASON)
        app.busy = False
        app.gates.refresh()
        self.assertFalse(disabled(tp.fit_button))
        self.assertTrue(disabled(tp.robust_button), "no fit yet")

    def test_fit_waits_for_the_ephemeris(self):
        # m787: a press during the lookup is kept, and the fit starts when the lookup lands
        app, tp = self.app, self.app.transit_page
        rep = {"planet": {"name": "TOI-2046 b", "period": 1.4971842}, "t": np.linspace(0, 0.1, 30),
               "flux": np.ones(30), "err": np.ones(30) * 1e-3, "airmass": None, "header": {}, "path": ""}
        tp.report = rep
        tp._eph_pending = rep
        with mock.patch.object(tui.messagebox, "showinfo") as box:
            tp.fit()
        box.assert_not_called()
        self.assertEqual(app.status.get(), tui.EPH_WAIT)
        self.assertIs(tp._fit_queued["report"], rep)
        self.assertFalse(disabled(tp.fit_button), "not greyed: the press is kept")
        with mock.patch.object(tp, "fit") as fit, mock.patch.object(tp, "_fill_table"), \
                mock.patch.object(tp, "draw"):
            tp._ephemeris_found(rep, {"t0": 2460000.5, "period": 1.4971842, "eph_source": "test"}, "")
        fit.assert_called_once_with(with_flagged=False)
        self.assertIsNone(tp._eph_pending)
        self.assertEqual(rep["planet"]["t0"], 2460000.5)
        # with no press, the lookup only says so (the fit still waits for the button)
        tp._eph_pending = rep
        with mock.patch.object(tp, "fit") as fit, mock.patch.object(tp, "_fill_table"), \
                mock.patch.object(tp, "draw"), mock.patch.object(tp, "_notes_text"):
            tp._ephemeris_found(rep, None, "offline")
        fit.assert_not_called()

    def test_trim_and_refit_grey_and_swap(self):
        app, tp = self.app, self.app.transit_page
        t = 2460000.5 + np.arange(60) / 600.0
        tmap = (t, np.zeros(60), True)
        tp._trims = []
        tp._drag = None
        with mock.patch.object(tp, "_trim_times", return_value=(t, tmap)):
            tp.trim_from.set("")
            tp.trim_to.set("")
            app.gates.refresh()
            self.assertEqual(app.gates.reason(tp.trim_refit_button), tui.TRIM_TYPE_FIRST)
            # the under-20 refusal is a live grey, with the count
            tp.trim_from.set(f"{t[5] - 1e-6:.6f}")
            tp.trim_to.set(f"{t[50] + 1e-6:.6f}")
            app.gates.refresh()
            self.assertTrue(disabled(tp.trim_refit_button))
            self.assertIn("would leave 14 points; a transit fit needs at least 20", app.gates.reason(tp.trim_refit_button))
            self.assertIn("would leave 14 points", tp.trim_text.get())
            # m819: To before From is the same span
            tp.trim_from.set(f"{t[30] + 1e-6:.6f}")
            tp.trim_to.set(f"{t[20] - 1e-6:.6f}")
            app.gates.refresh()
            self.assertFalse(disabled(tp.trim_refit_button))
            why, plan = tp._trim_plan(t, tmap)
            self.assertEqual(why, "")
            self.assertTrue(plan["swapped"])
            self.assertLess(plan["lo"], plan["hi"])
            self.assertEqual(int(plan["inside"].sum()), 11)
        with mock.patch.object(tp, "_data", return_value=(t, None, None, None, {})), \
                mock.patch.object(tp, "_time_map_for", return_value=tmap), \
                mock.patch.object(tp, "_trim_source_check"), mock.patch.object(tp, "fit") as fit, \
                mock.patch.object(tui.messagebox, "showerror") as err:
            tp.trim_and_refit()
        err.assert_not_called()
        fit.assert_called_once()
        self.assertEqual(len(tp._trims), 1)
        lo, hi = tp._trims[0]
        self.assertLess(lo, hi)
        self.assertEqual(tp.trim_from.get(), f"{t[20] - 1e-6:.6f}", "the boxes show the span used")
        tp._trims = []
        tp.trim_from.set("")
        tp.trim_to.set("")


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestVariablesOutput(Base):
    NEED = ("Save series", "Save AAVSO report", "Save light-curve CSV", "Export…", "Save plot PNG", "Comp health…",
            "Use comps…", "Time of max/min…", "Remove night…", "Night notes…")

    def open(self, n=40, nights=(N1, N2)):
        path = self.write("t.cvseries", _old_meta([A, B], CAT), _points([A, B], CAT, nights=nights, n=n))
        self.load(path)
        self.app.skip_flagged.set(True)
        self.app.draw_output()
        return path

    def buttons(self):
        page = self.app.page_frames[-1]
        out = {}
        for text in self.NEED:
            b = find_button(page, text)
            self.assertIsNotNone(b, text)
            out[text] = b
        return out

    def test_greyed_with_no_light_curve_and_live_after(self):
        app = self.app
        app.highlight_night = None
        app.gates.refresh()
        for text, b in self.buttons().items():
            self.assertTrue(disabled(b), text)
            self.assertTrue(app.gates.reason(b), text)
        self.assertEqual(app.gates.reason(self.buttons()["Save series"]), app.NO_SERIES)
        self.open()
        app.gates.refresh()
        for text, b in self.buttons().items():
            if text in ("Remove night…", "Night notes…"):
                self.assertTrue(disabled(b), text)
                self.assertEqual(app.gates.reason(b), app.PICK_NIGHT)
            else:
                self.assertFalse(disabled(b), text)
        # the run lock greys the job ones; the saves stay live
        app.busy = True
        try:
            app.gates.refresh()
            self.assertTrue(disabled(self.buttons()["Save series"]))
            self.assertTrue(disabled(self.buttons()["Use comps…"]))
            self.assertFalse(disabled(self.buttons()["Save AAVSO report"]))
        finally:
            app.busy = False
            app.gates.refresh()

    def test_night_notes_and_remove_night_use_the_picked_night(self):
        # m535: no number prompt; the night chosen in the night table
        app = self.app
        self.open()
        b = self.buttons()
        app.highlight_night = N2
        app.draw_output()
        app.gates.refresh()
        self.assertFalse(disabled(b["Night notes…"]))
        self.assertFalse(disabled(b["Remove night…"]))
        asked = []
        with mock.patch.object(self.ca.simpledialog, "askstring",
                               lambda *a, **k: asked.append(a) or "clouds after midnight"):
            app.edit_night_note()
        self.assertEqual(len(asked), 1, "only the note's own question")
        self.assertEqual(app.night_notes.get(N2), "clouds after midnight")
        self.assertNotIn(N1, app.night_notes)
        n1 = sum(1 for o in app.observations if o.night == N1)
        with mock.patch.object(self.ca, "ask_no", lambda *a, **k: True), \
                mock.patch.object(self.ca.simpledialog, "askstring") as ask:
            app.remove_night()
        ask.assert_not_called()
        self.assertEqual({o.night for o in app.observations}, {N1})
        self.assertEqual(len(app.observations), n1)
        app.gates.refresh()
        self.assertTrue(disabled(b["Remove night…"]), "the picked night is gone")
        # unpicked: a backstop press does nothing
        app.highlight_night = None
        with mock.patch.object(self.ca, "ask_no") as ask:
            app.remove_night()
        ask.assert_not_called()
        self.assertEqual(app.status.get(), app.PICK_NIGHT)

    def test_trim_greys_live_with_the_reason(self):
        app = self.app
        self.open(n=22)
        app.highlight_night = N1
        app.draw_output()
        pts = sorted((o for o in app.observations if o.night == N1), key=lambda o: o.jd)
        self.assertTrue(disabled(app.trim_button))
        self.assertEqual(app.gates.reason(app.trim_button), trim_ui.TYPE_FIRST)
        app.trim_from.set(f"{pts[10].jd - 1e-6:.6f}")
        app.trim_to.set(f"{pts[14].jd + 1e-6:.6f}")   # 5 out, 1 flagged: 16 usable left
        self.assertTrue(disabled(app.trim_button))
        why = app.gates.reason(app.trim_button)
        self.assertIn("would leave 16 usable points", why)
        self.assertIn("a night needs at least 20", why)
        self.assertEqual(app.trim_text.get(), why, "the hint says it as it is typed")
        app.trim_to.set(f"{pts[10].jd + 1e-6:.6f}")    # one point out: 20 left
        self.assertFalse(disabled(app.trim_button))
        self.assertNotIn("would leave", app.trim_text.get())
        app.trim_from.set("")
        app.trim_to.set("")


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestMultiNight(Base):
    def test_fit_waits_for_the_lookup_and_no_t0_is_plain(self):
        import multinight as mn
        planet = {"name": "TOI-2046 b", "host": "TOI-2046"}
        page = type("P", (), {"current_planet": lambda s: dict(planet), "result": None,
                              "detrend_airmass": type("V", (), {"get": lambda s: True})(),
                              "detrend_time": type("V", (), {"get": lambda s: False})()})()
        with mock.patch.object(mn.MultiNightWindow, "lookup"):
            w = mn.MultiNightWindow(self.app, page)
        try:
            w._lookup_running = 1
            w._all_nights = lambda: [{"use": True, "status": ("ok", "")}, {"use": True, "status": ("ok", "")}]
            with mock.patch.object(mn, "check_nights"), mock.patch.object(mn.messagebox, "showinfo") as box, \
                    mock.patch.object(w, "_start_fit_thread") as start:
                w.run_fit()
                box.assert_not_called()
                start.assert_not_called()
                self.assertTrue(w._fit_after_lookup)
                self.assertIn("waiting for the ephemeris", w.prog_text.get())
                with mock.patch.object(w.busy, "end"):
                    w._looked_up("TOI-2046 b", dict(planet, period=1.4971842), "")
                start.assert_called_once()
                self.assertEqual(w._lookup_outcome, "no_t0")
            self.app._end_job()
            # after a failed lookup the message stays (the one case it is still needed)
            w.planet = dict(planet)
            with mock.patch.object(mn, "check_nights"), mock.patch.object(mn.messagebox, "showinfo") as box:
                w.run_fit()
            box.assert_called_once()
        finally:
            w._running = False
            w.close()
        # m698
        fit = {"shape": {"all": {"k": (0.1, 0.01), "depth": (0.01, 0.001), "t14": (0.1, 0.01)}, "a_rs": (10.0, 1.0),
                         "b": (0.3, 0.1), "inc": (88.0, 1.0)}, "groups": ["all"], "rchi2": 1.0, "n_data": 100,
               "detrend": [], "nights": []}
        plain = mn.summary_lines(fit, {}, looked_up="no_t0")
        self.assertIn(("info", "No published ephemeris: epochs counted from the first night."), plain)
        self.assertFalse(any(lvl == "warn" for lvl, _t in plain))
        for outcome in (None, "failed"):
            self.assertTrue(any(t.startswith("⚠ No input ephemeris") and t.endswith("Look up.")
                                for _l, t in mn.summary_lines(fit, {}, looked_up=outcome)))


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestWindows(Base):
    def test_elements_from_vsx_needs_the_star_name(self):
        app = self.app
        path = self.write("t.cvseries", _old_meta([A, B], CAT), _points([A, B], CAT, n=30))
        self.load(path)
        app.star_id.set("")
        w = self.ca.ExtremumWindow(app)
        try:
            app.gates.refresh()
            self.assertTrue(disabled(w.vsx_button))
            self.assertIn("Star ID", app.gates.reason(w.vsx_button))
            app.star_id.set("V1441 Cas")
            app.gates.refresh()
            self.assertFalse(disabled(w.vsx_button))
            self.assertFalse(disabled(w.measure_button))
            app.busy = True
            app.gates.refresh()
            self.assertTrue(disabled(w.measure_button), "the busy lock covers open windows")
        finally:
            app.busy = False
            w.win.destroy()


class TestDetrendWords(unittest.TestCase):
    def test_words(self):
        self.assertEqual(tui.fit_progress_words("detrend", 2, 6), "Fitting the transit: choosing the detrend… 2 of 6")
        self.assertEqual(tui.fit_progress_words("detrend", 0, 6, "reference fit"),
                         "Fitting the transit: choosing the detrend… reference fit (before 1 of 6)")
        self.assertEqual(tui.fit_progress_words("detrend", 5, 6, "airmass + time slope"),
                         "Fitting the transit: choosing the detrend… trying airmass + time slope (5 of 6)")
        self.assertEqual(tui.fit_progress_words("detrend", 2, 6, "airmass", "with the flip step"),
                         "Fitting the transit: choosing the detrend… trying airmass (2 of 6, with the flip step)")
        self.assertIn("trying no detrend (1 of 6)", tui.fit_progress_words("detrend", 1, 6, "none"))

    def test_words_name_each_choice_and_the_numbers_do_not_change(self):
        from tests.test_230_b import PLANET, synth_night
        t, f, e, am, after = synth_night(0.004, seed=3)
        plain = tc.lsq_fit(t, f, e, am, PLANET, after_flip=after, detrend_auto=True)
        steps = []
        worded = tc.lsq_fit(t, f, e, am, PLANET, after_flip=after, detrend_auto=True,
                            progress=lambda *a: steps.append(a))
        self.assertEqual(plain["p"], worded["p"])
        self.assertEqual(plain["err"], worded["err"])
        self.assertEqual(plain["chi2"], worded["chi2"])
        self.assertEqual(plain["detrend_choice"]["key"], worded["detrend_choice"]["key"])
        self.assertEqual(steps[0], (0, 6, "reference fit", None))
        labels = [lab for _k, lab, _f in tc.DETREND_CHOICES]
        self.assertEqual([s[2] for s in steps if s[3] is None][1:], labels)
        if worded.get("flip_test") is not None or after.any():
            self.assertIn((2, 6, "airmass", "with the flip step"), steps)


if __name__ == "__main__":
    unittest.main()
