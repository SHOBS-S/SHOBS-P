"""2.4.0 step 8 (#89): Trim on the Variables Output page, one night at a time (trim_ui.TrimMixin).

core   the flag word 'trim' follows each night's spans and nothing else; other flag words are untouched.
state  no night chosen (a series of several nights): every Trim control is disabled and the drag does nothing; a
       night chosen in the night table wakes them; Close series greys them again.
trim   From / To (clock time or JD) or a drag trims only the chosen night's points, even when the span runs past the
       night; the points stay in the series with the flag word; they are out of _usable with 'Leave flagged points
       out' ticked or not; the cloud pass does not wipe the word; the night table counts them.
rules  a trim must leave 20 usable points in the night; From after To, no points, nothing typed are refused.
saved  the spans are saved with the night and come back on Load; an old series (no trims) loads as before.
clear  Clear trim puts back the chosen night's points only.
notes  the AAVSO report leaves the trimmed points out and its NOTES name the span on that night's rows only; the
       light-curve CSV keeps every point with its flag word."""
import numpy as np
import math
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photometry_core as core    # noqa: E402
import test_240_m as M            # noqa: E402
import trim_ui                    # noqa: E402
from test_240_m import A, B, _old_meta, _points   # noqa: E402

CAT = {A: 8.728, B: 11.31}
N1, N2 = "JD2460950", "JD2460951"


class TestCore(unittest.TestCase):
    def test_flags_follow_the_spans_of_their_own_night(self):
        pts = _points([A, B], CAT, n=10)
        first = [o for o in pts if o.night == N1]
        lo, hi = first[2].jd - 1e-6, first[5].jd + 1e-6
        # the span reaches far past the night: the other night is not touched
        n = core.apply_trim_flags(pts, {N1: [[lo, hi + 5.0]]})
        self.assertEqual(n, len(first) - 2)
        self.assertTrue(all(core.is_trimmed(o) for o in first[2:]))
        self.assertFalse(any(core.is_trimmed(o) for o in pts if o.night == N2))
        self.assertEqual(first[3].flag, "sat trim", "other words stay")
        n = core.apply_trim_flags(pts, {N1: [[lo, hi]]})
        self.assertEqual(n, 4)
        self.assertEqual(core.apply_trim_flags(pts, {}), 0)
        self.assertEqual(first[3].flag, "sat")
        self.assertFalse(any(core.is_trimmed(o) for o in pts))

    def test_bad_spans_are_ignored(self):
        self.assertEqual(core.clean_trim_spans([[1, 2], "x", [3], [5, 4], [float("nan"), 1], None]), [(1.0, 2.0)])
        self.assertEqual(core.clean_trim_spans(None), [])
        self.assertEqual(trim_ui.merge_spans([(3, 4), (1, 2), (1.5, 2.5)]), [[1.0, 2.5], [3.0, 4.0]])

    def test_cloud_pass_keeps_the_word(self):
        pts = _points([A, B], CAT, n=30)
        first = [o for o in pts if o.night == N1]
        core.apply_trim_flags(pts, {N1: [[first[4].jd, first[8].jd]]})
        core.apply_cloud_flags(pts, 0.20)
        core.apply_cloud_edge_flags(pts)
        self.assertEqual(sum(core.is_trimmed(o) for o in pts), 5)


class TrimCase(M.AppCase):
    def open(self, n=40, nights=(N1, N2)):
        path = self.write("t.cvseries", _old_meta([A, B], CAT), _points([A, B], CAT, nights=nights, n=n))
        self.load(path)
        self.app._write_series(path)
        self.load(path)
        self.app.skip_flagged.set(True)
        self.app.draw_output()
        return path

    def pick(self, night):
        self.app.highlight_night = night
        self.app.draw_output()

    def night(self, night):
        return sorted((o for o in self.app.observations if o.night == night), key=lambda o: o.jd)

    def disabled(self, w):
        return "disabled" in w.state()

    def boxes(self):
        messages = {"info": [], "error": []}
        return messages, [
            mock.patch.object(self.ca.messagebox, "showinfo", lambda _t, m, **k: messages["info"].append(m)),
            mock.patch.object(self.ca.messagebox, "showerror", lambda _t, m, **k: messages["error"].append(m))]

    def trim(self, a, b):
        app = self.app
        app.trim_from.set(a)
        app.trim_to.set(b)
        messages, patches = self.boxes()
        for p in patches:
            p.start()
        try:
            app.trim_points()
        finally:
            for p in patches:
                p.stop()
        return messages


class TestState(TrimCase):
    def test_greyed_until_one_night_is_chosen(self):
        self.open()
        app = self.app
        controls = app._trim_entries + [app.trim_button, app.clear_trim_button]
        self.assertIsNone(app._trim_night())
        self.assertTrue(all(self.disabled(w) for w in controls))
        self.assertEqual(app.trim_text.get(), trim_ui.HINT_NO_NIGHT)
        if app._trim_span is not None:
            self.assertFalse(app._trim_span.get_active())
        # the drag and the button do nothing without a night
        app._trim_span_dragged(0.0, 1.0)
        self.assertEqual(app.trim_from.get(), "")
        app.trim_from.set(f"{self.night(N1)[3].jd:.6f}")
        app.trim_points()
        self.assertFalse(any(core.is_trimmed(o) for o in app.observations))
        self.assertIn("Choose a night", app.status.get())
        app.clear_trim()
        # a night chosen: live (Clear trim only once there is a trim); the other night's typed time is gone
        self.pick(N1)
        self.assertEqual(app._trim_night(), N1)
        self.assertEqual(app.trim_from.get(), "")
        self.assertFalse(any(self.disabled(w) for w in app._trim_entries + [app.trim_button]))
        self.assertTrue(self.disabled(app.clear_trim_button))
        if app._trim_span is not None:
            self.assertTrue(app._trim_span.get_active())
        # unchosen again: greyed
        self.pick(None)
        self.assertTrue(all(self.disabled(w) for w in controls))
        # closed: greyed
        self.pick(N1)
        app.observations = []
        app._clear_series_state()
        app.draw_output()
        self.assertTrue(all(self.disabled(w) for w in controls))

    def test_one_night_series_still_needs_its_night_chosen(self):
        # John, 23 Nov: Trim is not available until a night is selected, even in a one-night series.
        self.open(nights=(N1,))
        self.assertIsNone(self.app._trim_night())
        self.assertTrue(self.disabled(self.app.trim_button))
        self.pick(N1)
        self.assertEqual(self.app._trim_night(), N1)
        self.assertFalse(self.disabled(self.app.trim_button))

    def test_controls_are_in_the_points_group(self):
        app = self.app
        self.assertEqual(app.out_groups.group_of(app.trim_frame), "points")
        self.assertEqual(app.out_groups.group_of(app.trim_hint_label), "points")
        for w in (app.trim_button, app.clear_trim_button):
            self.assertEqual(str(w.winfo_parent()), str(app.trim_frame))
        words = app.out_groups.texts("points")
        self.assertIn("Trim", words)
        self.assertIn("Clear trim", words)


class TestTrim(TrimCase):
    def test_trim_one_night_only_and_everything_follows(self):
        path = self.open()
        app = self.app
        self.pick(N1)
        first = self.night(N1)
        used0 = len(app._usable())
        lo, hi = first[25].jd - 1e-6, first[29].jd + 1e-6
        m = self.trim(f"{lo:.6f}", f"{hi + 3.0:.6f}")     # To runs past the second night
        self.assertEqual(m, {"info": [], "error": []})
        trimmed = [o for o in app.observations if core.is_trimmed(o)]
        self.assertEqual(len(trimmed), len(first) - 25)
        self.assertTrue(all(o.night == N1 for o in trimmed), "never another night's points")
        self.assertEqual(len(app.observations), 80, "nothing is deleted")
        self.assertEqual(len(app._usable()), used0 - 15)
        # unticked: flagged points come back, trimmed ones do not
        app.skip_flagged.set(False)
        self.assertFalse(any(core.is_trimmed(o) for o in app._usable()))
        self.assertEqual(len(app._usable()), 80 - 15)
        app.refit_period()
        self.assertEqual(sum(core.is_trimmed(o) for o in app.observations), 15, "a refit keeps the word")
        app.skip_flagged.set(True)
        app.draw_output()
        # the night table's count, the hint, Clear trim live
        row = next(r for r in app._summary_snapshot["rows"] if r["night"] == N1)
        self.assertIn("15 trimmed", row["flags"])
        self.assertEqual(row["pts"], 40)
        self.assertIn("Trimmed:", app.trim_text.get())
        self.assertFalse(self.disabled(app.clear_trim_button))
        self.assertEqual(app.trim_from.get(), "")
        # saved with the night, and back on Load
        self.assertIn("Saved to t.cvseries", app.status.get())
        meta, pts = core.load_series(path)
        self.assertEqual(len(meta["night_binning"][N1]["trims"]), 1)
        self.assertNotIn("trims", meta["night_binning"].get(N2) or {})
        self.assertEqual(sum(core.is_trimmed(o) for o in pts), 15)
        self.assertEqual(len(pts), 80)
        self.load(path)
        self.assertEqual(sum(core.is_trimmed(o) for o in app.observations), 15)
        self.assertFalse(app.series_unsaved())
        # the flag follows the spans even if a point lost its word
        for o in app.observations:
            o.flag = " ".join(w for w in o.flag.split() if w != "trim")
        app._refresh_period()
        self.assertEqual(sum(core.is_trimmed(o) for o in app.observations), 15)

    def test_second_trim_adds_a_span_and_clear_is_per_night(self):
        self.open()
        app = self.app
        self.pick(N1)
        first = self.night(N1)
        self.trim(f"{first[4].jd:.6f}", f"{first[6].jd:.6f}")
        self.trim(f"{first[30].jd:.6f}", "")              # an empty To: to the night's end
        self.assertEqual(sum(core.is_trimmed(o) for o in first), 3 + 10)
        self.assertEqual(len(app._night_trims(N1)), 2)
        self.assertIn("2 spans", app.trim_text.get())
        self.pick(N2)
        second = self.night(N2)
        self.trim("", f"{second[2].jd:.6f}")              # an empty From: from the night's start
        self.assertEqual(sum(core.is_trimmed(o) for o in second), 3)
        self.assertEqual(sum(core.is_trimmed(o) for o in first), 13, "the other night keeps its trims")
        app.clear_trim()
        self.assertEqual(sum(core.is_trimmed(o) for o in second), 0)
        self.assertEqual(sum(core.is_trimmed(o) for o in first), 13)
        self.assertNotIn("trims", app._night_info(N2))
        self.assertIn("3 points put back", app.status.get())
        self.assertTrue(self.disabled(app.clear_trim_button))
        app.clear_trim()
        self.assertIn("Nothing is trimmed", app.status.get())

    def test_refused(self):
        self.open()
        app = self.app
        self.pick(N1)
        first = self.night(N1)
        m = self.trim("", "")
        self.assertIn("Type a From and/or To time", m["info"][0])
        m = self.trim(f"{first[9].jd:.6f}", f"{first[3].jd:.6f}")
        self.assertIn("is after To", m["error"][0])
        m = self.trim("25:99", "")
        self.assertIn("is not clear", m["error"][0])
        m = self.trim(f"{first[0].jd - 0.5:.6f}", f"{first[0].jd - 0.4:.6f}")
        self.assertIn("nothing to trim", m["info"][0])
        # 40 points, 1 flagged sat (index 3): trimming from index 20 would leave 19 usable, from 21 leaves 20
        m = self.trim(f"{first[20].jd - 1e-6:.6f}", f"{first[39].jd + 1e-6:.6f}")
        self.assertIn("would leave 19 usable points", m["error"][0])
        self.assertIn("at least 20", m["error"][0])
        self.assertFalse(any(core.is_trimmed(o) for o in app.observations))
        m = self.trim(f"{first[21].jd - 1e-6:.6f}", f"{first[39].jd + 1e-6:.6f}")
        self.assertEqual(m["error"], [])
        self.assertEqual(sum(core.is_trimmed(o) for o in first), 19)
        m = self.trim(f"{first[25].jd:.6f}", f"{first[30].jd:.6f}")
        self.assertIn("trimmed already", m["info"][0])

    def test_clock_times_and_the_drag(self):
        import site_time
        self.open()
        app = self.app
        self.pick(N2)
        second = self.night(N2)
        # a clock time is read on the night's own date, in the site's zone
        a = site_time.jd_to_local(second[5].jd).strftime("%H:%M")
        jd = app._trim_parse(a, [o.jd for o in second])
        self.assertLess(abs(jd - second[5].jd), 1.0 / 1440.0 + 1e-9)
        self.assertEqual(app._trim_parse("", [1.0]), None)
        self.assertAlmostEqual(app._trim_parse("60951.61", [1.0]), 2460951.61)
        # a drag: the plot is zoomed to the chosen night (hours from its first point)
        self.assertEqual(app._trim_axis["night"], N2)
        x0 = (second[10].jd - second[0].jd) * 24.0 - 0.01
        x1 = (second[14].jd - second[0].jd) * 24.0 + 0.01
        app._trim_span_dragged(x1, x0)
        self.assertTrue(app.trim_from.get() and app.trim_to.get())
        self.assertIn("(5 points) marked", app.status.get())
        messages, patches = self.boxes()
        for p in patches:
            p.start()
        try:
            app.trim_points()
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(messages, {"info": [], "error": []})
        self.assertEqual([i for i, o in enumerate(second) if core.is_trimmed(o)], [10, 11, 12, 13, 14])
        # a one-night series must have its night chosen too (John, 23 Nov); on any time axis the chosen night's
        # plot is zoomed to it, and the drag still lands on the same frames
        for axis in ("JD", "UTC", "Local time"):
            self.open(nights=(N1,))
            app.time_axis.set(axis)
            self.pick(N1)
            app.draw_output()
            first = self.night(N1)
            amap = app._trim_axis
            self.assertEqual(amap["night"], N1, axis)
            xs = [float(amap["x"][int(np.argmin(np.abs(amap["jd"] - first[k].jd)))]) for k in (20, 22)]
            gap = (first[1].jd - first[0].jd) / amap["scale"] / 4.0
            app._trim_span_dragged(xs[0] - gap, xs[1] + gap)
            app.trim_points()
            self.assertEqual([i for i, o in enumerate(first) if core.is_trimmed(o)], [20, 21, 22], axis)
        app.time_axis.set("JD")

    def test_plot_marks_the_trimmed_points(self):
        self.open()
        app = self.app
        self.pick(N1)
        first = self.night(N1)
        self.trim(f"{first[10].jd:.6f}", f"{first[14].jd:.6f}")
        texts = [t.get_text() for t in app.out_ax1.texts]
        self.assertIn("trimmed", texts)
        self.assertIn("× trimmed", app.out_ax1.get_title())
        # the whole series shown: the band without its label
        self.pick(None)
        self.assertNotIn("trimmed", [t.get_text() for t in app.out_ax1.texts])
        self.assertIn("× trimmed", app.out_ax1.get_title())


class TestFiles(TrimCase):
    def test_report_notes_and_csv(self):
        self.open()
        app = self.app
        self.pick(N1)
        first = self.night(N1)
        self.trim(f"{first[10].jd - 1e-6:.6f}", f"{first[14].jd + 1e-6:.6f}")
        self.pick(None)
        text = app._report_text()
        rows = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
        self.assertEqual(len(rows), 80 - 2 - 5, "flagged and trimmed points are not reported")
        with_note = [ln for ln in rows if "SHOBS-P: trimmed (left out) JD " in ln]
        self.assertEqual(len(with_note), 40 - 1 - 5, "on the trimmed night's rows only")
        self.assertTrue(all(f"night {N1}" in ln for ln in with_note))
        self.assertIn("5 points)", with_note[0])
        self.assertIn(f"JD {first[10].jd:.5f}-{first[14].jd:.5f}", with_note[0])
        with_note[0].encode("ascii")
        self.assertFalse(any(f"{o.jd:.5f}" in ln.split(",")[1] for o in first[10:15] for ln in rows))
        # unticked: the flagged point is reported, the trimmed ones still are not
        app.skip_flagged.set(False)
        rows = [ln for ln in app._report_text().splitlines() if ln and not ln.startswith("#")]
        self.assertEqual(len(rows), 80 - 5)
        app.skip_flagged.set(True)
        lines = app.variables_settings_lines(app._usable())
        self.assertEqual(len([ln for ln in lines if ln.startswith("Trimmed on the Output page")]), 1)
        self.assertEqual(sum(1 for o in app.observations if "trim" in core.observation_to_dict(o)["flag"].split())
                         if hasattr(core, "observation_to_dict") else 5, 5)

    def test_old_series_loads_without_trims(self):
        self.open()
        app = self.app
        self.assertFalse(any(core.is_trimmed(o) for o in app.observations))
        self.assertEqual(app._trims_by_night(), {})

    def test_a_night_measured_again_keeps_its_trims(self):
        self.open()
        app = self.app
        first = self.night(N1)
        spans = [[first[10].jd - 1e-6, first[14].jd + 1e-6], [first[0].jd - 9.0, first[0].jd - 8.0]]
        app.night_bin[N1] = {}
        app._carry_trims(N1, spans, first)
        self.assertEqual(len(app._night_info(N1)["trims"]), 1, "a span without a point is not carried")
        app.night_bin[N1] = {}
        app._carry_trims(N1, [[first[0].jd - 1.0, first[25].jd]], first)
        self.assertNotIn("trims", app._night_info(N1), "dropped when too few points would be left")
        self.assertTrue(self.said("trims were dropped"))


if __name__ == "__main__":
    unittest.main()
