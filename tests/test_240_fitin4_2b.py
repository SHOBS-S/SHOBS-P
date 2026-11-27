"""2.4.0 fit-in F4, package 2, second part: Save light-curve CSV's busy bar, and the third pass of the sheet sweep.

csv        Save light-curve CSV writes on a worker thread (2.2.19) and, on a long series, has the busy bar up
           before the snapshot of the points is taken (#100 item 4, #116).
sweep      m487 the Period box says why no search was made; m490 'Airmass not checked' row; m605 and m282 no box;
           m540 one line for series names; m520 the early-version box once per series file, and no report of those
           nights; x025 a short Keep question; m729 m732 m741 m742 stacking boxes; m217 m367 m662 m028 wording."""
import inspect
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import photometry_core as core  # noqa: E402

try:
    import tkinter  # noqa: F401
    tkinter.Tk().destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False

if HAVE_TK:
    import clearv_app as ca  # noqa: E402
    import multinight  # noqa: E402
    import stack_core  # noqa: E402
    import stack_ui  # noqa: E402
    import test_240_fitin4_2 as T  # noqa: E402
    import test_240_m as M  # noqa: E402
    Base = M.AppCase
else:
    Base = unittest.TestCase


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestSaveCsvBar(Base):
    def _save(self, n_bar):
        app = self.app
        app.mode_key = "variables"
        app.observations = T._series(nights=2, per_night=20)
        app.highlight_night = None
        path = os.path.join(self.dir, "curve.csv")
        order, real_begin = [], app.busy_begin

        def begin(words):
            order.append(("bar", words, threading_name()))
            return real_begin(words)

        def write(p, obs, notes, scint):
            order.append(("write", len(obs), threading_name()))
            with open(p, "w") as fh:
                fh.write("x\n")

        def threading_name():
            import threading
            return "window" if threading.current_thread() is threading.main_thread() else "worker"
        import copy as copy_mod
        real_deep = copy_mod.deepcopy

        def deep(x, *a, **k):
            if isinstance(x, list) and x and isinstance(x[0], core.Obs):
                order.append(("snapshot", len(x), threading_name()))
            return real_deep(x, *a, **k)
        with mock.patch.object(ca.App, "CSV_BAR_POINTS", n_bar), \
                mock.patch.object(app, "_save_scope", return_value="series"), \
                mock.patch.object(app, "output_path", return_value=path), \
                mock.patch.object(app, "busy_begin", side_effect=begin), \
                mock.patch.object(copy_mod, "deepcopy", side_effect=deep), \
                mock.patch.object(core, "write_curve_csv", side_effect=write):
            got = app.save_csv()
            app.wait_for_saves()
            app.update()
        self.assertEqual(got, path)
        return order

    def test_long_series_has_the_bar_up_before_the_snapshot(self):
        order = self._save(10)
        kinds = [k for k, *_ in order]
        self.assertLess(kinds.index("bar"), kinds.index("snapshot"))
        self.assertIn("Saving the light-curve CSV (40 points", order[kinds.index("bar")][1])
        self.assertEqual(order[kinds.index("snapshot")][2], "window")   # the snapshot is the window thread's
        self.assertEqual(order[kinds.index("write")][2], "worker")      # the file is the worker's
        self.app.update()
        self.assertFalse(self.app.busy_bar.jobs)                         # and the bar ends with the save

    def test_short_series_shows_no_bar_for_a_quick_save(self):
        order = self._save(2000)
        self.assertEqual([k for k, *_ in order], ["snapshot", "write"])
        self.assertFalse(self.app.busy_bar.jobs)


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestPeriodBoxRows(Base):
    def _draw(self):
        app, got = self.app, {}

        def grab(series_lines, period_lines, color_lines, rows, cautions):
            got["period"], got["cautions"] = list(period_lines), list(cautions)
        with mock.patch.object(app, "_fill_summary", side_effect=grab):
            app.draw_output()
        return got

    def test_no_search_is_said_in_the_period_box(self):
        app = self.app
        app.mode_key = "variables"
        app.observations = T._series()
        app.catalog_period = None
        app.mark_periods.set("")
        app.min_period.set(5.0)
        app.max_period.set(1.0)
        app._refresh_period()
        self.assertIsNone(app.period)
        self.assertIn("correct the two Period search boxes", app._period_error)
        row = self._draw()["period"][0]
        self.assertTrue(row.startswith("Period  None: no search: "), row)
        self.assertIn("Check the Period search range.", row)
        self.assertNotIn("8 usable points", row)
        # a good range clears it
        app.min_period.set(0.02)
        app.max_period.set(20.0)
        app._refresh_period()
        self.assertEqual(app._period_error, "")
        self.assertNotIn("no search", self._draw()["period"][0])

    def test_airmass_not_checked_row(self):
        app = self.app
        app.mode_key = "variables"
        app.observations = T._series()
        app.catalog_period = None
        app.mark_periods.set("")
        app.min_period.set(0.02)
        app.max_period.set(20.0)
        app._refresh_period()
        self.assertTrue(app.period)
        app.period["airmass_trend"] = {"skip": "no airmass"}
        self.assertIn("Airmass not checked (no site or RA/Dec)", self._draw()["period"])
        app.period["airmass_trend"] = {}
        self.assertFalse([r for r in self._draw()["period"] if r.startswith("Airmass not checked")])


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestNoBoxes(Base):
    def test_cancelled_check_name_is_a_status_line(self):
        app = self.app
        app.observations = T._series(nights=1, per_night=10)
        app.check_used = "check"
        app.check_name.set("check")
        app.report_names = {}
        app.seq_auids = {}
        app.check_xy = None
        with mock.patch.object(ca.simpledialog, "askstring", return_value=None), \
                mock.patch.object(ca.messagebox, "showinfo") as box:
            self.assertFalse(app._name_report_check())
        box.assert_not_called()
        self.assertTrue([t for t in self.lines if t.startswith("Report not saved: it needs the check star's name")])

    def test_repeated_watch_star_is_a_status_line(self):
        app = self.app
        with mock.patch.object(ca.messagebox, "showinfo") as box, \
                mock.patch.object(app, "_refresh_watch_box"):
            app._already_watching("V1", "V1 is already a watch star.", True)
        box.assert_not_called()
        self.assertEqual(app.status.get(), "V1 is already a watch star. Its row is highlighted in the Watch stars box.")
        self.assertIn("Watch stars: V1 is already a watch star.", self.lines)

    def test_series_names_one_line(self):
        src = inspect.getsource(ca.App._apply_same_star_plan)
        self.assertIn('f"Series names and magnitudes used for {n_ren} star', src)
        k = src.index("(same star): series name")
        self.assertIn("keep_status=True", src[k:k + 260])   # each star's line is for the Log only


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestEarlyVersionNights(Base):
    def test_box_once_per_series_file(self):
        app = self.app
        app.settings.pop("legacy_warned", None)
        path = os.path.join(self.dir, "old.cvseries")
        with mock.patch.object(ca.messagebox, "showwarning") as box, mock.patch.object(app, "_save_settings"):
            app._warn_legacy({"JD2460001"}, "old.cvseries", path)
            self.assertEqual(box.call_count, 1)
            app._warn_legacy({"JD2460001"}, "old.cvseries", path)
            self.assertEqual(box.call_count, 1)               # the second load: one plain Log line
            self.assertTrue([t for t in self.lines if "binned before the debayer" in t and "⚠" not in t])
            app._warn_legacy({"JD2460001"}, "other.cvseries", os.path.join(self.dir, "other.cvseries"))
            self.assertEqual(box.call_count, 2)               # another file is told once too
            app._warn_legacy(set(), "old.cvseries", path)
            self.assertEqual(box.call_count, 2)
        self.assertEqual(len(app.settings["legacy_warned"]), 2)

    def test_report_with_such_a_night_is_not_saved(self):
        app = self.app
        pts = T._series(nights=2, per_night=5)
        app.observations = pts
        app.legacy_mixed = {pts[0].night}
        with mock.patch.object(ca.messagebox, "showerror") as box:
            self.assertTrue(app._legacy_report_stop(pts))
            self.assertIn("binned before the debayer", box.call_args[0][1])
            self.assertIn("choose another night in the Saving box", box.call_args[0][1])
            good = [o for o in pts if o.night != pts[0].night]
            self.assertFalse(app._legacy_report_stop(good))
            self.assertEqual(box.call_count, 1)
        app.legacy_mixed = set()
        self.assertIn("self._legacy_report_stop(points)", inspect.getsource(ca.App._save_aavso_report))


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestWording(Base):
    def test_keep_question_is_short(self):
        app = self.app
        with mock.patch.object(app, "_transmission_excess", return_value=0.0):
            text = app._keep_question("JD2460950")
        self.assertLess(len(text), 330)
        self.assertNotIn("photon", text)
        self.assertNotIn("scintillation", text)
        self.assertIn(app.KEEP_USE, text)
        self.assertIn(app.KEEP_LEAVE, text)
        self.assertIn("the default", text)
        with mock.patch.object(app, "_transmission_excess", return_value=0.012):
            self.assertIn("0.012 mag added", app._keep_question("JD2460950"))

    def test_edge_note_silent_when_the_drift_was_checked(self):
        app = self.app
        app.preview = np.zeros((600, 600))
        app.sky_out.set("20")
        xy = (40.0, 300.0)   # 40 px in: the ring (20 px) needs 25, the fixed margin wants 60
        with mock.patch.object(app, "_series_drift", return_value=None):
            self.assertIn("40 px from the frame edge", app._edge_note("comp", xy))   # not checked: said as before
        drift = {"regs": [("f2", {"dx": 0.0, "dy": 0.0})], "how": "series"}
        with mock.patch.object(app, "_series_drift", return_value=drift), \
                mock.patch.object(app, "_drift_clearance", return_value=(40.0, "", [("f2", 40.0)])):
            self.assertEqual(app._edge_note("comp", xy), "")                           # stays in all night
        with mock.patch.object(app, "_series_drift", return_value=drift), \
                mock.patch.object(app, "_drift_clearance", return_value=(12.0, "", [("f2", 12.0)])):
            self.assertIn("12 px from the frame edge", app._edge_note("comp", (12.0, 300.0)))   # too near: still said
        app.preview = None

    def test_reworded_lines(self):
        src = inspect.getsource(ca)
        self.assertNotIn("comp1_x/y and check_x/y in the light-curve CSV", src)
        self.assertIn("on the Blink page and run each side on its", src)
        self.assertIn("only SIMBAD was checked. Scan again later before reporting a", src)
        mn = inspect.getsource(multinight)
        self.assertIn("ephemeris, not the data.\"", mn)
        self.assertNotIn("exposure start instead of mid, or JD_UTC labeled BJD_TDB). \"\n"
                         "                                \"A current ephemeris", mn)


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestPlainNoteAndStatus(Base):
    def test_old_error_bars_is_a_plain_line(self):
        app = self.app
        app.mode_key = "variables"
        pts = T._series(nights=2, per_night=12)
        for o in pts:
            o.e_model = 1 if o.night == pts[0].night else 0   # one night from before 2.2.17
        app.observations = pts
        app.highlight_night = None
        app._refresh_period()
        app.draw_output()
        self.assertIn("keep the old, larger error bars", app.note_text.get())
        self.assertNotIn("⚠", app.note_text.get())
        self.assertNotIn("keep the old, larger error bars", app.caution_text.get())
        self.assertEqual(app._summary_snapshot["notes"], [app.note_text.get()])   # the saved plot keeps it
        for o in pts:
            o.e_model = 1
        app.draw_output()
        self.assertEqual(app.note_text.get(), "")
        self.assertEqual(app._summary_snapshot["notes"], [])

    def test_added_file_on_its_own_comps_stays_on_the_status_line(self):
        src = inspect.getsource(ca.App.add_series_file)
        self.assertIn("Its nights keep their own comps: expect an offset.", src)
        self.assertIn("+ own_comps)", src)

    def test_zero_point_log_line_stays_a_finding(self):
        # sheet row m621 (plain Log copy) is not done: the ⚠ line is what lists it in the Findings panel
        self.assertIn('self.log("⚠ Ensemble zero point, "', inspect.getsource(ca.App._log_zero_point_check))


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestStackingBoxes(Base):
    def test_skip_reason_names_the_common_one(self):
        app = self.app
        app._run_skipped = [{"why": "could not align (too few stars)"}, {"why": "could not align (too few stars)"},
                            {"why": "every comp was lost: A: off the frame edge"}]
        self.assertEqual(app._stack_skip_reason(),
                         "the most common reason was could not align (2 of 3 stacks skipped)")
        app._run_skipped = [{"why": "could not align (too few stars)"}] * 2
        self.assertEqual(app._stack_skip_reason(), "every stack was skipped for the same reason, could not align")
        app._run_skipped = []
        self.assertEqual(app._stack_skip_reason(), "")

    def test_boxes_in_the_source(self):
        st = inspect.getsource(stack_ui)
        self.assertIn("is the largest that gives", st)                      # m732
        self.assertIn('(n_al, "could not be aligned")', st)                 # m729
        self.assertIn("Photometry on the stacks stopped by an error: {error}", st)   # m741
        self.assertIn("self._stack_skip_reason()", inspect.getsource(stack_ui.StackMixin._stack_done))   # m742
        self.assertTrue(hasattr(stack_core, "MIN_POINTS"))


if __name__ == "__main__":
    unittest.main()
