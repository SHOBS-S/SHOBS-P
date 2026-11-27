"""2.4.0 fit-in F3, package C: Output pages, Transit fit, Multi-night, saving.

period     area J: one verdict row (Reliable / ⚠ Doubtful / None) from period_verdict; the rows below are plain;
           one plain Bins row (m486/m488); Check row plain (m524).
saving     m040 no "Saved" box without an Output folder; m536/m574 one "Series not saved" message; m609/m610 one
           sentence for a failed background save; m512 Load series: the status line only.
area L     the save scope is on the Results row (the highlighted night by default) and no save asks; the save gate
           asks only about what WebObs rejects (default No), advice is a Log note; an empty Observer code / Star ID
           is marked as soon as there is a light curve; AAVSO's spelling by itself at Look up, with one plain line.
F2-c       Apply, Transmission loss…, Planet transits… and Refit period are gated; no "Need at least 8" pop-up.
transit    transit_flags: one Shape note, one Coverage note, no "not needed" flip note, the two flip ⚠ only where the
           verdict does not carry them, flagged-frames note plain with "Without them: …"; one comps-noise wording;
           parse_clock's one message.
multinight outlier nights in the scatter line (m668), one no-airmass note (m701), plain progress line (m677), one
           wording for "not a fit CSV" (x095) and for JD_UTC without RA/Dec (x111).
m543       the series' catalog magnitude: the why is said once."""
import inspect
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import multinight as mn  # noqa: E402
import photometry_core as core  # noqa: E402
import transit_core as tc  # noqa: E402
import transit_ui as tui  # noqa: E402

try:
    import tkinter  # noqa: F401
    tkinter.Tk().destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False

if HAVE_TK:
    import clearv_app as ca  # noqa: E402
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


# ---- area J: the period verdict (pure) ---------------------------------------------------------------------------
@unittest.skipUnless(HAVE_TK, "clearv_app needs tkinter")
class TestPeriodVerdict(unittest.TestCase):
    def test_reliable(self):
        state, reasons = ca.period_verdict(0.03912, {"fap": 0.001})
        self.assertEqual((state, reasons), ("reliable", []))
        line = ca.period_verdict_line(0.03912, {"fap": 0.001})
        self.assertEqual(line, "Period  Reliable: 0.03912 d (FAP 0.1 %)")
        self.assertNotIn("⚠", line)
        self.assertIn("under 0.01 %", ca.period_verdict_line(0.5, {"fap": 1e-12}))

    def test_doubtful_worst_reason_first(self):
        checks = {"fap": 1e-6, "alias": True, "carried_by": ["22–23 Oct"], "edge": True}
        state, reasons = ca.period_verdict(0.9973, checks)
        self.assertEqual(state, "doubtful")
        self.assertTrue(reasons[0].startswith("depends on 22–23 Oct alone"))
        self.assertEqual(reasons[1:], ["near a day alias", "at the edge of the search"])
        line = ca.period_verdict_line(0.9973, checks)
        self.assertTrue(line.startswith("Period  ⚠ Doubtful: 0.99730 d — depends on 22–23 Oct alone"))
        self.assertEqual(line.count("⚠"), 1)
        # an airmass-demoted period, a period outside the search and a short span are doubts too
        self.assertEqual(ca.period_verdict(1.0, {"fap": 1e-6, "airmass_demoted": True})[0], "doubtful")
        self.assertIn("VSX 0.5 d outside the search",
                      ca.period_verdict(1.0, {"fap": 1e-6, "outside": "VSX 0.5 d"})[1])
        self.assertEqual(ca.period_verdict(1.0, {"fap": 1e-6, "short_span": True})[0], "doubtful")

    def test_none(self):
        for checks, words in (
                ({"fap": 1e-9, "too_short": True}, "one short night covers under two cycles"),
                ({"fap": 0.3}, "the star looks constant at this precision"),
                ({"fap": float("nan")}, "no significant period"),
                ({"fap": 1e-6, "night_fap": 0.2}, "counting nights"),
                ({"no_search": "needs at least 8 usable points"}, "needs at least 8 usable points")):
            state, reasons = ca.period_verdict(0.5, checks)
            self.assertEqual(state, "none", checks)
            self.assertIn(words, reasons[0])
            line = ca.period_verdict_line(0.5, checks)
            self.assertTrue(line.startswith("Period  None: "), line)
            self.assertNotIn("⚠", line)
            self.assertTrue(line.endswith("."))
        # the star's scatter is carried in the reason
        line = ca.period_verdict_line(0.5, {"fap": 0.4, "constant": "the star looks constant at this precision "
                                                                    "(scatter 0.004 mag per point)"})
        self.assertIn("(scatter 0.004 mag per point)", line)
        # counting nights says no, but a night carries it: Doubtful, with both reasons
        state, reasons = ca.period_verdict(0.5, {"fap": 1e-6, "night_fap": 0.2, "carried_by": ["N1"]})
        self.assertEqual(state, "doubtful")
        self.assertEqual(len(reasons), 2)

    def test_bins_row_is_one_plain_line(self):
        row = ca.App._bins_too_coarse(10.0, 0.039)
        self.assertEqual(row, "Bins    every point used (10-min bins too coarse for 0.039 d)")
        # 2.4.0 fit-in F4-2: the search is split in two for Load series' worker thread (prepare + job)
        src = inspect.getsource(ca.App._period_prepare) + inspect.getsource(ca.App._period_job)
        self.assertEqual(src.count("_bins_too_coarse("), 2)
        self.assertNotIn("⚠", src)


# ---- transit_flags (pure) ----------------------------------------------------------------------------------------
def _result(**over):
    """A small fit result for transit_flags: a full transit, 60 min of baseline on each side, nothing to flag."""
    t14 = 2.0 / 24
    r = {"summary": {"k": (0.12, 0.003), "t14": (t14, 0.001), "a_rs": (8.0, 0.2), "b": (0.3, 0.05),
                     "depth": (0.0144, 0.0005), "tc": (0.5, 0.0005)},
         "best": {"k": 0.12, "b": 0.3, "tc": 0.5, "a_rs": 8.0}, "period": 2.0, "priors": {},
         "t": np.linspace(0.5 - t14 / 2 - 1.0 / 24, 0.5 + t14 / 2 + 1.0 / 24, 121), "beta": 1.0, "rchi2": 1.0,
         "verdict": {"state": "detected"}, "planet": {"k": 0.12}}
    r.update(over)
    return r


def _texts(r):
    return [text for _lvl, text in tc.transit_flags(r)]


class TestTransitFlags(unittest.TestCase):
    def test_clean_night_has_no_shape_or_coverage_note(self):
        texts = _texts(_result())
        self.assertFalse([x for x in texts if x.startswith(("Shape:", "Coverage:")) or "Coverage:" in x], texts)

    def test_one_shape_note(self):
        # m756 + m759: the fit pulls from the published a/R*, and the free shape goes elsewhere: one ⚠ note
        ff = {"p": {"k": 0.15, "b": 0.6, "a_rs": 6.5}}
        flags = tc.transit_flags(_result(priors={"a_rs": (10.0, 0.3)}, free_fit=ff))
        shape = [(lvl, x) for lvl, x in flags if x.startswith("Shape:")]
        self.assertEqual(len(shape), 1)
        lvl, text = shape[0]
        self.assertEqual(lvl, "warn")
        self.assertIn("a/R* = 8 is 6.7σ from the published 10 ± 0.3", text)
        self.assertIn("left free, Rp/R* goes to 0.150 (b 0.60, a/R* 6.5)", text)
        self.assertIn("First look at Comp health, then trim a cloudy end.", text)
        # only the free-shape test: a plain note
        flags = tc.transit_flags(_result(free_fit=ff))
        shape = [(lvl, x) for lvl, x in flags if x.startswith("Shape:")]
        self.assertEqual(len(shape), 1)
        self.assertEqual(shape[0][0], "info")
        self.assertIn("the reported values lean on the published a/R* and b", shape[0][1])
        self.assertNotIn("pulls hard", " ".join(x for _l, x in flags))

    def test_one_coverage_note(self):
        # a night that ends 14 min before egress, with a gap inside the transit: one ⚠ line, not three
        t14 = 2.0 / 24
        t1, t4 = 0.5 - t14 / 2, 0.5 + t14 / 2
        t = np.concatenate([np.linspace(t1 - 62.0 / 1440, t1 + 20.0 / 1440, 60),
                            np.linspace(t1 + 50.0 / 1440, t4 - 14.0 / 1440, 40)])
        flags = tc.transit_flags(_result(t=t))
        cover = [(lvl, x) for lvl, x in flags if "Coverage:" in x]
        self.assertEqual(len(cover), 1)
        lvl, text = cover[0]
        self.assertEqual(lvl, "warn")
        self.assertIn("little or no baseline after egress (62 min before, 0 min after)", text)
        self.assertIn("egress not observed (data end 14 min before it)", text)
        self.assertIn("a 30-min gap inside the transit", text)
        self.assertIn("A one-sided baseline lets a trend imitate depth or shift Tc.", text)
        joined = " ".join(x for _l, x in flags)
        for old in ("was not observed", "-minute gap in the data", "Little or no baseline"):
            self.assertNotIn(old, joined)

    def test_no_points_inside_follows_the_verdict(self):
        t = np.linspace(0.7, 0.8, 40)   # all after the window
        with_verdict = _texts(_result(t=t, verdict={"state": "inconclusive"}))
        self.assertTrue(any("almost no points inside the predicted transit window" in x for x in with_verdict))
        none = _texts(_result(t=t, verdict={"state": "none"}))   # (m764) the verdict says it
        self.assertFalse(any("almost no points inside" in x for x in none))

    def _flip(self, **over):
        flip = {"t_flip": 0.52, "step": (0.002, 0.0005), "corr": 0.95, "baseline_ok": False, "depth_ok": False,
                "sides": {"before": (40.0, 30), "after": (5.0, 4)}, "source": "the frames' flip segment"}
        flip.update(over)
        return flip

    def test_flip_notes(self):
        # m775: a step that was tested and not needed is no longer among the cautions
        ft = {"kept": False, "t_flip": 0.52, "step": (0.0002, 0.0006), "dbic": -6.0}
        texts = _texts(_result(flip_test=ft))
        self.assertFalse(any("no step term" in x or "Flip step not needed" in x for x in texts), texts)
        self.assertIn("Flip step not needed", tc.flip_step_line(_result(flip_test=ft)))   # the table / result line
        # m776 / m778: on a detected transit the verdict carries both reasons: no note
        flags = tc.transit_flags(_result(flip=self._flip()))
        joined = " ".join(x for _l, x in flags)
        self.assertNotIn("trade off", joined)
        self.assertNotIn("Out-of-transit baseline on each side", joined)
        # with another verdict they stay, plain
        flags = tc.transit_flags(_result(flip=self._flip(), verdict={"state": "inconclusive"}))
        kept = [(lvl, x) for lvl, x in flags if "trade off" in x or "Out-of-transit baseline on each side" in x]
        self.assertEqual(len(kept), 2)
        self.assertEqual({lvl for lvl, _x in kept}, {"info"})
        # the low-correlation note is unchanged
        texts = _texts(_result(flip=self._flip(corr=0.2, baseline_ok=True, depth_ok=True)))
        self.assertTrue(any("Step–depth correlation +0.20 (MCMC); its uncertainty is part of the depth's." in x
                            for x in texts))

    def test_flagged_frames_note_is_plain(self):
        flags = tc.transit_flags(_result(with_flagged={"n": 12, "label": "cloud", "factor": 2.0}))
        mine = [(lvl, x) for lvl, x in flags if x.startswith("This fit includes")]
        self.assertEqual(mine, [("info", "This fit includes 12 frames flagged cloud, error bars ×2.")])

    def test_without_flagged_words(self):
        prev = _result(t=np.arange(100.0))
        new = _result(t=np.arange(112.0), with_flagged={"n": 12, "label": "cloud", "factor": 2.0})
        self.assertEqual(tui.without_flagged_words(prev, new), "Without them: depth 1.44 %, Tc 0.50000.")
        self.assertEqual(tui.without_flagged_words(None, new), "")
        self.assertEqual(tui.without_flagged_words(_result(t=np.arange(90.0)), new), "")   # another night's fit
        self.assertEqual(tui.without_flagged_words(new, new), "")                          # itself with flagged
        self.assertEqual(tui.without_flagged_words(prev, _result()), "")                   # not a with-flagged fit
        other = _result(t=np.arange(100.0), planet={"k": 0.12, "name": "X b"})
        self.assertEqual(tui.without_flagged_words(other, new), "")                        # another planet

    def test_comps_noise_one_wording(self):
        self.assertEqual(tui.COMP_CAUTION_PREFIXES[0], "Comps limit the noise")
        src = inspect.getsource(core.comp_noise_warning)
        self.assertIn('"Comps limit the noise (the target scatters', src)
        self.assertNotIn("The comparison stars set most of the noise", src)
        fin = inspect.getsource(tui)
        self.assertNotIn('"Comps: " + meta["comp_noise"]', fin)
        self.assertIn('("info" if detected else "warn", meta["comp_noise"])', fin)

    def test_parse_clock_one_message(self):
        for bad in ("2x:00", "abc", "2500", "12:7"):
            with self.assertRaises(ValueError) as cm:
                tui.parse_clock(bad)
            self.assertEqual(str(cm.exception),
                             f"'{bad}' is not a time; use HHMM or HH:MM (for example 2300 or 23:00)")
        self.assertEqual(inspect.getsource(tui.parse_clock).count("is not a time"), 1)
        self.assertEqual(tui.parse_clock("2300"), (23, 0))
        self.assertEqual(tui.parse_clock(""), (None, None))


# ---- multi-night (pure) ------------------------------------------------------------------------------------------
class TestMultiNightWords(unittest.TestCase):
    def _a(self, chi2, oc, sig=1.0):
        rows = [{"oc_min": v, "sigma_min": sig} for v in oc]
        return {"tc": [0.0] * len(oc), "labels": [f"N{i + 1}" for i in range(len(oc))],
                "own": {"eph": {"dof": len(oc) - 2, "chi2_red": chi2}, "rows": rows}}

    def test_scatter_line_names_the_outliers(self):
        out = mn.timing_statements(self._a(4.2, [0.1, 5.0, -0.2, 4.1]))
        warn = [x for lvl, x in out if lvl == "warn"]
        self.assertEqual(len(warn), 1)
        self.assertIn("(χ²ν 4.2); outlier nights: N2, N4. Its errors are scaled by √χ²ν = 2.05.", warn[0])
        self.assertFalse(any(x.startswith("⚠ Outlier night") for _l, x in out))
        # the scatter test failed but no night stands out
        out = mn.timing_statements(self._a(4.2, [1.0, -1.0, 1.0, -1.0]))
        warn = [x for lvl, x in out if lvl == "warn"]
        self.assertEqual(len(warn), 1)
        self.assertIn("no single night stands out", warn[0])

    def test_outlier_with_a_passing_scatter_test_is_plain(self):
        out = mn.timing_statements(self._a(1.1, [0.1, 3.5, -0.2, 0.3]))
        self.assertFalse([x for lvl, x in out if lvl == "warn"])
        self.assertIn(("info", "Night N2 is more than 3σ from the nights' own ephemeris."), out)

    def test_one_no_airmass_note(self):
        # 2.4.0 fit-in F4 (m655): plain now (no ⚠, no button): the fit handles such a night by itself
        note = mn.no_airmass_note(["5–6 Oct", "9–10 Oct"])
        self.assertTrue(note.startswith("No airmass in the file for 5–6 Oct, 9–10 Oct: their baseline is fitted "
                                        "against time instead"))
        self.assertFalse(hasattr(mn, "TIME_ONLY_BUTTON"))
        self.assertIn(": its baseline", mn.no_airmass_note(["5–6 Oct"]))
        src = inspect.getsource(mn.summary_lines)
        self.assertEqual(src.count("no_airmass_note("), 1)
        self.assertLess(src.index("no_airmass_note("), src.index('for n in fit["nights"]:'))   # once, not per night
        self.assertNotIn("NO_AIRMASS_WARN", src)

    def test_progress_line_is_plain(self):
        src = inspect.getsource(mn)
        # 2.4.0 fit-in F4 (m677): the line is always "N nights ready." (a night without airmass needs no action)
        self.assertNotIn("fitted against time only: see the note", src)
        self.assertNotIn("with no airmass (see above)", src)

    def test_not_a_fit_csv_one_wording(self):
        with tempfile.TemporaryDirectory() as d:
            for name, body in (("a.csv", "x,y\n1,2\n"), ("b.csv", "time,flux,err\n1,2,3\n")):
                path = os.path.join(d, name)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(body)
                with self.assertRaises(mn.NightError) as cm:
                    mn.load_fit_csv(path)
                self.assertEqual(str(cm.exception), mn.NOT_FIT_CSV)
        self.assertEqual(mn.NOT_FIT_CSV,
                         "not a SHOBS-P transit-fit CSV (it needs time, rel_flux and rel_flux_err columns)")

    def test_jd_utc_one_wording(self):
        src = inspect.getsource(mn)
        self.assertNotIn("star position unknown", src)
        self.assertEqual(src.count("so they cannot be put on BJD_TDB"), 1)
        self.assertIn('raise NightError("the page\'s " + JD_UTC_NO_RADEC)', src)


# ---- the app -----------------------------------------------------------------------------------------------------
@unittest.skipUnless(HAVE_TK, "needs a display")
class TestSaving(Base):
    def test_saved_notice_never_a_box(self):
        app = self.app
        app.output_dir.set("")
        path = os.path.join(self.dir, "curve.csv")
        with mock.patch.object(ca.messagebox, "showinfo") as box:
            app.saved_notice(path, "Advice here.")
        box.assert_not_called()
        self.assertEqual(app.status.get(), f"Saved curve.csv to {os.path.basename(self.dir)}. Advice here.")
        self.assertIn(f"Saved {path}", self.lines)

    def test_series_not_saved_is_one_message(self):
        app = self.app
        exc = PermissionError(13, "Permission denied")
        with mock.patch.object(ca.messagebox, "showerror") as box:
            tail = app.series_not_saved(exc)
        text = "✖ Series not saved (Permission denied). Save series… to another folder before closing."
        self.assertEqual(self.lines[-1], text)
        box.assert_called_once()
        self.assertEqual(box.call_args[0][1], text.lstrip("✖ "))
        self.assertEqual(tail, " ✖ Series not saved: see the Log.")
        with mock.patch.object(ca.messagebox, "showerror") as box:
            app.series_not_saved(exc, box=False)   # an automatic save: the Log only
        box.assert_not_called()
        # every site uses it
        import trim_ui
        old = 'f"Could not save the series: {exc}"'
        self.assertEqual(inspect.getsource(trim_ui).count(old), 0)
        # one site is left to package B (inside its m558 hunk, _comp_selection_applied); none after the merge
        self.assertLessEqual(inspect.getsource(ca).count(old), 1)
        self.assertGreaterEqual(inspect.getsource(ca).count("self.series_not_saved(exc"), 6)

    def test_remove_night_save_failure(self):
        app = self.app
        path = self.write("t.cvseries", _old_meta([A, B], CAT), _points([A, B], CAT, n=12))
        self.load(path)
        app.highlight_night = N2
        with mock.patch.object(app, "_save_series_words", side_effect=OSError(28, "No space left on device")), \
                mock.patch.object(ca.messagebox, "showerror") as box, \
                mock.patch.object(ca.messagebox, "showwarning") as warn, \
                mock.patch.object(ca, "ask_no", return_value=True), \
                mock.patch.object(ca.messagebox, "askyesno", return_value=True):
            app.remove_night()
        if {o.night for o in app.observations} == {N1}:   # the night was removed: the failure is said once
            warn.assert_not_called()
            box.assert_called_once()
            self.assertIn("Series not saved (No space left on device)", box.call_args[0][1])
            self.assertTrue(app.status.get().endswith("✖ Series not saved: see the Log."))

    def test_background_save_failure_one_sentence(self):
        app = self.app
        path = os.path.join(self.dir, "lc.csv")

        def write():
            raise PermissionError(13, "Permission denied")

        with mock.patch.object(ca.messagebox, "showerror") as box, mock.patch.object(ca.traceback, "print_exc"):
            app._save_in_background(path, write, "light-curve CSV (3 points)")
            for _ in range(50):
                app.update()
                if box.called:
                    break
        text = ("✖ lc.csv not saved (Permission denied). Save it to another folder, or close the program that has "
                "the file open.")
        self.assertEqual([ln for ln in self.lines if "not saved" in ln], [text])
        box.assert_called_once()
        self.assertEqual(box.call_args[0][1], text.lstrip("✖ "))
        self.assertFalse(os.path.exists(path))
        # an automatic save: no box
        self.lines.clear()
        with mock.patch.object(ca.messagebox, "showerror") as box, mock.patch.object(ca.traceback, "print_exc"):
            app._save_in_background(path, write, "light-curve CSV", asked=False)
            for _ in range(20):
                app.update()
        box.assert_not_called()
        self.assertEqual(len(self.lines), 1)

    def test_load_series_status_line_only(self):
        app = self.app
        path = self.write("t.cvseries", _old_meta([A, B], CAT), _points([A, B], CAT, n=12))
        with mock.patch.object(ca.messagebox, "showinfo") as box:
            self.load(path)
        box.assert_not_called()
        self.assertEqual(app.status.get(), "Loaded 24 points from 2 night(s). Reduce the new night, then choose "
                                           "Add; the series' comps and check are placed after the solve.")

    def test_series_catalog_magnitude_said_once(self):
        src = inspect.getsource(ca)
        self.assertFalse("(The series' catalog magnitude wins, so every night stays on one zero point" in src)
        plan = inspect.getsource(ca.App._apply_same_star_plan)
        self.assertEqual(plan.count("so every night stays on one zero point"), 1)
        self.assertIn("keep_status=True", plan)


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestVariablesOutput(Base):
    def open(self, n=40, nights=(N1, N2)):
        path = self.write("t.cvseries", _old_meta([A, B], CAT), _points([A, B], CAT, nights=nights, n=n))
        self.load(path)
        self.app.skip_flagged.set(True)
        self.app.draw_output()
        return path

    def tearDown(self):
        self.app.highlight_night = None
        self.app.observer.set("")
        super().tearDown()

    # ---- F2-c ----
    def test_points_and_fit_buttons_are_gated(self):
        app = self.app
        page = app.page_frames[-1]
        app.gates.refresh()
        names = ("Apply", "Transmission loss…", "Planet transits…", "Refit period")
        for text in names:
            b = find_button(page, text)
            self.assertIsNotNone(b, text)
            self.assertTrue(disabled(b), text)
            self.assertEqual(app.gates.reason(b), app.NO_SERIES, text)
        self.open()
        app.gates.refresh()
        for text in names:
            self.assertFalse(disabled(find_button(page, text)), text)
        # under 8 usable points: Refit period greyed with the reason, Apply still live, and no pop-up
        self.open(n=3)
        with mock.patch.object(ca.messagebox, "showinfo") as box:
            app.refit_period()
        box.assert_not_called()
        app.gates.refresh()
        refit = find_button(page, "Refit period")
        self.assertTrue(disabled(refit))
        self.assertEqual(app.gates.reason(refit), "Need at least 8 usable points.")
        self.assertFalse(disabled(find_button(page, "Apply")))
        self.assertTrue(app._summary_snapshot["period"][0].startswith("Period  None: needs at least 8 usable points"))
        self.assertNotIn('messagebox.showinfo(APP_TITLE, "Need at least 8 usable points.")', inspect.getsource(ca))

    # ---- area J in the page ----
    def test_period_box_leads_with_the_verdict(self):
        app = self.app
        self.open()
        lines = app._summary_snapshot["period"]
        self.assertTrue(lines[0].startswith("Period  "), lines)
        self.assertRegex(lines[0], r"^Period  (Reliable: |⚠ Doubtful: |None: )")
        self.assertRegex(lines[1], r"^(Best|Peak)    \d")
        self.assertNotIn("(not significant)", lines[1])
        for row in lines[1:]:
            self.assertNotIn("⚠", row)
        self.assertEqual(app._period_verdict[0] == "doubtful", "⚠" in lines[0])
        # the same verdict goes into the report's settings file, in ASCII
        row = [x for x in app.variables_settings_lines(app._usable()) if x.startswith("Period verdict: ")]
        self.assertEqual(len(row), 1)
        self.assertRegex(row[0], r"^Period verdict: (Reliable|Doubtful|None): ")
        self.assertNotIn("⚠", row[0])

    # ---- area L: scope ----
    def test_scope_box_follows_the_highlight_and_no_save_asks(self):
        app = self.app
        self.open()
        self.assertEqual(app.save_scope_text.get(), "Saving: whole series (2 nights)")
        self.assertEqual(list(app.save_scope_box.cget("values")), ["Saving: whole series (2 nights)"])
        with mock.patch.object(ca.messagebox, "askyesnocancel") as ask:
            self.assertEqual(app._save_scope("AAVSO report"), "series")
            app.highlight_night = N2
            app.draw_output()
            self.assertEqual(app._save_scope("AAVSO report"), "night")   # John's call 3: the highlighted night
            ask.assert_not_called()
        night_words = "Saving: night " + app.night_label(N2, short=True, with_target=False)
        self.assertEqual(app.save_scope_text.get(), night_words)
        self.assertEqual(list(app.save_scope_box.cget("values")), ["Saving: whole series (2 nights)", night_words])
        # the user picks the whole series: it holds until the highlight changes
        app.save_scope_box.current(0)
        app._save_scope_picked()
        self.assertEqual(app._save_scope("light-curve CSV"), "series")
        app.draw_output()
        self.assertEqual(app._save_scope("light-curve CSV"), "series")
        self.assertIn("whole series (2 nights)", app.status.get())
        app.highlight_night = N1
        app.draw_output()
        self.assertEqual(app._save_scope("light-curve CSV"), "night")
        app.highlight_night = None
        app.draw_output()
        self.assertEqual(app._save_scope("light-curve CSV"), "series")
        # one night: nothing to choose
        self.open(nights=(N1,))
        app.highlight_night = N1
        app.draw_output()
        self.assertEqual(app._save_scope("AAVSO report"), "series")
        self.assertEqual(app.save_scope_text.get(), "Saving: whole series (1 night)")

    def test_csv_save_names_the_scope(self):
        app = self.app
        self.open()
        app.highlight_night = N2
        app.draw_output()
        out = os.path.join(self.dir, "lc.csv")
        with mock.patch.object(app, "output_path", return_value=out), \
                mock.patch.object(app, "_save_in_background") as bg, \
                mock.patch.object(ca.messagebox, "askyesnocancel") as ask:
            app.save_csv()
        ask.assert_not_called()
        what = bg.call_args[0][2]
        self.assertIn("40 points", what)
        self.assertIn("night " + app.night_label(N2, short=True, with_target=False), what)

    # ---- area L: the save gate ----
    def test_webobs_rejects_are_marked_at_once(self):
        app = self.app
        app.observer.set("")
        app.star_id.set("")
        app._mark_webobs()
        self.assertEqual(app.webobs_needs.get(), "")          # nothing to report yet: nothing is marked
        self.assertEqual(str(app.observer_entry.cget("style")), "TEntry")
        self.open()
        app.observer.set("")
        self.assertEqual([k for k, _w in app.webobs_rejects()], ["observer"])
        self.assertEqual(app.webobs_needs.get(), "WebObs needs an observer code (Input page)")
        self.assertEqual(str(app.observer_entry.cget("style")), "Invalid.TEntry")
        self.assertEqual(str(app.star_entry.cget("style")), "TEntry")
        app.observer.set("JABA")                              # typed: the mark goes by itself
        self.assertEqual(app.webobs_needs.get(), "")
        self.assertEqual(str(app.observer_entry.cget("style")), "TEntry")
        app.star_id.set("")
        self.assertEqual(app.webobs_needs.get(), "WebObs needs a Star ID (Input page)")
        self.assertEqual(str(app.star_entry.cget("style")), "Invalid.TEntry")
        app.star_id.set("HD 219134")
        # more than one comp and no check star
        for o in app.observations:
            o.kmag = None
        app._mark_webobs()
        self.assertEqual(app.webobs_needs.get(), "WebObs needs a check star (more than one comp)")

    def test_save_asks_once_about_rejects_only(self):
        app = self.app
        self.open()
        app.observer.set("")
        for o in app.observations[:5]:
            o.filt = "TB"                                      # mixed filters: advice, never a question
        with mock.patch.object(ca, "ask_no", return_value=False) as ask, \
                mock.patch.object(ca.messagebox, "askyesno") as yn, \
                mock.patch.object(app, "_name_report_check") as later:
            app.save_aavso()
        ask.assert_called_once()
        message = ask.call_args[0][1]
        self.assertTrue(message.startswith("WebObs will reject this file:\n  • the AAVSO observer code is empty"))
        self.assertTrue(message.endswith("Save the report anyway?"))
        self.assertNotIn("filters", message)
        yn.assert_not_called()
        later.assert_not_called()                              # No (the default) stops the save
        # nothing WebObs rejects: no question at all; the advice is a Log line
        app.observer.set("JABA")
        self.lines.clear()
        with mock.patch.object(ca, "ask_no") as ask, \
                mock.patch.object(app, "_name_report_check", return_value=False):
            app.save_aavso()
        ask.assert_not_called()
        self.assertIn("AAVSO report: the series mixes filters; each row carries its own.", self.lines)
        self.assertIs(ca.ask_no.__defaults__, None)            # ask_no's default button is No (2.3.5)
        self.assertIn("default=messagebox.NO", inspect.getsource(ca.ask_no))

    # ---- area L: AAVSO's spelling ----
    def test_aavso_spelling_by_itself(self):
        app = self.app
        app.star_id.set("rr lyr")
        self.assertIn("AAVSO spells this “RR Lyr”: Look up switches to it.", app.name_hint.get())
        started = []
        with mock.patch.object(ca.threading, "Thread") as thread, \
                mock.patch.object(app, "_lookup_busy_start", lambda *a, **k: started.append(a)), \
                mock.patch.object(ca.messagebox, "askyesno") as ask:
            app.lookup_star(quiet=True)
        ask.assert_not_called()
        thread.assert_called_once()
        self.assertEqual(app.star_id.get(), "RR Lyr")
        self.assertEqual(started[0][1], "RR Lyr")              # the lookup is made with AAVSO's spelling
        self.assertIn("Star ID written the way AAVSO spells it: RR Lyr (was rr lyr).", self.lines)
        self.assertEqual(app.name_hint.get(), "")
        # a name that is fine: nothing said
        self.lines.clear()
        self.assertEqual(app._aavso_spelling(), "RR Lyr")
        self.assertEqual(self.lines, [])
        # the save-time question is gone; a name no lookup has seen is switched at the save, with the same line
        src = inspect.getsource(ca.App.save_aavso)
        self.assertNotIn("askyesno", src)
        self.assertIn("self._aavso_spelling()", src)
        self.assertFalse(hasattr(app, "_use_suggested_name"))


if __name__ == "__main__":
    unittest.main()
