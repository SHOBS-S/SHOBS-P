"""2.4.0 step 8: fixes from the independent review of step 8 (#89 Trim, #90 Time of max error period, #66 re-center).

M1  a re-center is a POSITION that holds: a new segment needs 5 measured frames at the new pointing; a shorter
    excursion is no re-center; frames back at an earlier pointing rejoin its segment; no plate scale = no detection
    (said to the user); the field scan uses the same core function.
S1  the noise period is tuned only on a night of at least two cycles.
S2  a times-CSV / VSX / typed period twice the series period wins, and the result says so.
S3  dropped trims are a ⚠ finding; a re-center check that was not made is said on screen.
S4  a frame measured later takes the segment its own registration puts it in.
S5  a re-center segment of under 5 usable points is never lined up by itself.
N1  trimmed points are not counted as 'flagged off scale'.   N2  the Trim boxes refuse ambiguous numbers.
N3  the times CSV has noise_period_used_d.   N5  the Fourier warning names the period and its source.
N7  the 20-point Trim minimum holds for the night's other point set (Per frame / Stacked) too."""
import copy
import math
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import photometry_core as core                  # noqa: E402
import trim_ui                                  # noqa: E402
import test_240_step8_tmax as TM                # noqa: E402
import test_240_step8_trim as TT                # noqa: E402
from test_240_step8_recenter import _obs, _reg, _src   # noqa: E402

N1 = TT.N1


def _labels(regs, scale=0.27, factor=1, sides=None):
    return core.recenter_segments(regs, scale, factor, sides=sides)


class TestRecenterIsAPosition(unittest.TestCase):
    """M1, the reviewer's cases."""

    def test_one_frame_excursion_is_no_recenter(self):
        regs = [_reg(0.05 * k, 0) for k in range(40)]
        regs[17] = _reg(90, 0)   # one bad registration, 90 px away and back
        got = _labels(regs)
        self.assertTrue(got["checked"])
        self.assertEqual(got["recenters"], [])
        self.assertEqual(got["labels"], [0] * 40)
        # the numbers are those of no detection at all
        pts = [_obs(k, mag=10.0 + 0.01 * (k % 7)) for k in range(40)]
        plain = core.normalize_segments(pts)
        self.assertEqual(core.mark_recenters(pts, regs, 0.27, 1), [])
        self.assertEqual({o.jump for o in pts}, {0})
        self.assertEqual(core.normalize_segments(pts), plain)
        self.assertEqual(core.night_steps(pts, [o.mag for o in pts]), {})

    def test_excursion_of_up_to_four_frames_stays_with_its_segment(self):
        for n in (1, 2, 3, 4):
            regs = [_reg(0, 0)] * 12 + [_reg(300, 0)] * n + [_reg(0, 0)] * 12
            got = _labels(regs)
            self.assertEqual(got["recenters"], [], n)
            self.assertEqual(set(got["labels"]), {0}, n)
        # five frames at the new pointing is a re-center, and the way back another (to the first segment)
        got = _labels([_reg(0, 0)] * 12 + [_reg(300, 0)] * 5 + [_reg(0, 0)] * 12)
        self.assertEqual(got["labels"], [0] * 12 + [1] * 5 + [0] * 12)
        self.assertEqual([j["returned"] for j in got["recenters"]], [False, True])

    def test_excursion_right_before_a_real_recenter(self):
        # 2 stray frames, then the new pointing holds: one re-center; the strays stay with the segment they left
        regs = [_reg(0, 0)] * 10 + [_reg(600, 0)] * 2 + [_reg(240, 0)] * 10
        got = _labels(regs)
        self.assertEqual(got["labels"], [0] * 12 + [1] * 10)
        self.assertEqual(len(got["recenters"]), 1)
        self.assertEqual((got["recenters"][0]["index"], got["recenters"][0]["from"]), (12, 9))
        self.assertAlmostEqual(got["recenters"][0]["px"], 240.0)
        # no segment has fewer than 5 frames, whatever happens
        for labels in (got["labels"], _labels([_reg(0, 0)] * 3 + [_reg(500, 0)] * 20)["labels"]):
            for lab in set(labels):
                self.assertGreaterEqual(labels.count(lab), core.RECENTER_HOLD)

    def test_return_to_an_earlier_pointing_rejoins_its_segment(self):
        regs = [_reg(0, 0)] * 10 + [_reg(240, 0)] * 10 + [_reg(1.5, -1)] * 10 + [_reg(240, 300)] * 10
        got = _labels(regs)
        self.assertEqual(got["labels"], [0] * 10 + [1] * 10 + [0] * 10 + [2] * 10)
        self.assertEqual([j["returned"] for j in got["recenters"]], [False, True, False])
        pts = [_obs(k, mag=10.0 + (0.05 if 10 <= k < 20 else 0.0)) for k in range(40)]
        found = core.mark_recenters(pts, regs, 0.27, 1)
        self.assertEqual([o.jump for o in pts], got["labels"])
        self.assertEqual((found[1]["before"].path, found[1]["after"].path), ("Light_0019.fit", "Light_0020.fit"))
        steps = core.night_steps(pts, [o.mag for o in pts])["JD2461000"]
        self.assertAlmostEqual(steps[0]["offset"], 0.05)
        self.assertEqual((steps[0]["n_before"], steps[0]["n_after"]), (20, 10))   # both stretches of segment 0
        self.assertAlmostEqual(steps[1]["shift"], 0.0)
        words = core.segment_words(pts)
        self.assertEqual((words[0], words[15], words[25], words[35]),
                         ("no flip / before re-center", "no flip / after re-center", "no flip / before re-center",
                          "no flip / after re-center 2"))

    def test_return_is_only_looked_for_on_the_same_side_of_the_flip(self):
        regs = [_reg(0, 0)] * 10 + [_reg(240, 0)] * 10 + [_reg(0, 0, flipped=True)] * 10 + [_reg(240, 0, flipped=True)] * 10
        got = _labels(regs)
        self.assertEqual(got["labels"], [0] * 10 + [1] * 10 + [1] * 10 + [2] * 10)
        self.assertEqual([j["index"] for j in got["recenters"]], [10, 30])
        # a flip alone is never a re-center, also one the alignment cannot see (the points' segment says it)
        self.assertEqual(_labels([_reg(0, 0)] * 10 + [_reg(90, 40)] * 10, sides=[""] * 10 + ["flip"] * 10)["labels"],
                         [0] * 20)

    def test_dither_and_drift(self):
        dither = [_reg(12.0 * ((k // 5) % 2), 0) for k in range(60)]   # 12 px every 5 frames
        got = _labels(dither, scale=None)
        self.assertFalse(got["checked"])                                # no plate scale: nothing is looked for
        self.assertEqual((got["labels"], got["recenters"]), ([0] * 60, []))
        got = _labels(dither, scale=0.27)                               # 12 px = 3.2 arcsec: under the sky limit
        self.assertTrue(got["checked"])
        self.assertEqual((got["labels"], got["recenters"]), ([0] * 60, []))
        drift = [_reg(1.5 * k, 0) for k in range(200)]                  # 300 px over the night, never a jump
        self.assertEqual(_labels(drift)["recenters"], [])
        # and after hours of drift, a return is judged against where the segment was last
        regs = drift[:100] + [_reg(600, 0)] * 10 + [_reg(150, 0)] * 10
        self.assertEqual(_labels(regs)["labels"], [0] * 100 + [1] * 10 + [0] * 10)

    def test_the_real_64_arcsec_recenter(self):
        # HD 219134, 10-11 Nov: 0.54 arcsec per image pixel, 2 stored pixels per image pixel, 64 arcsec at frame 20
        regs = [_reg(0.1 * k, 0) if k < 20 else _reg(85 + 0.1 * k, 83) for k in range(40)]
        got = _labels(regs, scale=0.54, factor=2)
        self.assertEqual(got["labels"], [0] * 20 + [1] * 20)
        self.assertEqual(len(got["recenters"]), 1)
        self.assertEqual(core.recenter_text(got["recenters"][0])[:3], "64″")
        self.assertEqual(_labels(regs, scale=None, factor=2)["recenters"], [])

    def test_frames_without_a_registration(self):
        regs = [_reg(0, 0)] * 8 + [None] + [_reg(200, 0)] * 8 + [None]
        self.assertEqual(_labels(regs)["labels"], [0] * 9 + [1] * 9)
        self.assertEqual(_labels([None] * 4)["labels"], [0] * 4)
        self.assertEqual(_labels([{"dx": "x"}, _reg(0, 0), {"dx": float("nan"), "dy": 0}])["labels"], [0, 0, 0])

    def test_field_scan_uses_the_same_function(self):
        src = _src()
        scan = src[src.index("recenters, recenter_at, recenter_note = [], [], \"\""):]
        scan = scan[:scan.index("result[\"boundaries\"] = ")]
        self.assertIn("core.recenter_segments(regs, jump_scale[0], jump_scale[1], sides=", scan)
        self.assertNotIn("pointing_jumps", src)
        self.assertIn("recenter_note = self.NO_SCALE_NOTE", scan)
        self.assertIn('result["recenter_note"] = recenter_note', scan)
        self.assertIn('result["recenter_note"] + " " if result.get("recenter_note")', src)   # on the scan's page
        # what the scan does with the labels: ids in order of first appearance, boundaries where the id changes
        seg = np.array([0] * 12 + [1] * 12)
        regs = [_reg(0, 0)] * 6 + [_reg(240, 0)] * 6 + [_reg(0, 0, True)] * 12
        found = core.recenter_segments(regs, 0.27, 1, sides=[int(v) for v in seg])
        ids, both = {}, []
        for key in zip(seg.tolist(), found["labels"]):
            both.append(ids.setdefault(key, len(ids)))
        self.assertEqual(both, [0] * 6 + [1] * 6 + [2] * 12)
        at = [k for k in range(1, 24) if seg[k] == seg[k - 1] and both[k] != both[k - 1]]
        jd = 2461000.5 + np.arange(24) * 0.001
        self.assertEqual(core.scan_boundaries(np.array(both), jd, at), [(6, "recenter"), (12, "flip")])


class TestSmallSegments(unittest.TestCase):
    """S5: too few points give no level of their own."""

    def _night(self, n_after, usable_after=None):
        pts = [_obs(k, mag=10.0) for k in range(30)] + [_obs(30 + k, mag=10.3, jump=1) for k in range(n_after)]
        for o in pts[30 + (usable_after if usable_after is not None else n_after):]:
            o.mag = float("nan")
        return pts

    def test_normalize_leaves_a_small_recenter_segment_with_its_neighbor(self):
        for n in (1, 2, 3, 4):
            pts = self._night(n)
            np.testing.assert_allclose(core.normalize_segments(pts), [10.0] * 30 + [10.3] * n)   # nothing is moved
            self.assertEqual(core.night_steps(pts, [o.mag for o in pts]), {})
        pts = self._night(8, usable_after=4)   # 8 frames but only 4 with a magnitude
        out = core.normalize_segments(pts)
        np.testing.assert_allclose(out[30:34], 10.3)
        pts = self._night(5)                   # 5 usable points: its own segment, lined up
        np.testing.assert_allclose(core.normalize_segments(pts), 10.15)

    def test_small_first_part_goes_with_the_part_after_it(self):
        pts = [_obs(k, mag=10.3) for k in range(3)] + [_obs(3 + k, mag=10.0, jump=1) for k in range(30)]
        np.testing.assert_allclose(core.normalize_segments(pts), [10.3] * 3 + [10.0] * 30)

    def test_check_star_zero_point_too(self):
        pts = self._night(3)
        for k, o in enumerate(pts):
            o.check_std = 11.0 + (0.2 if k >= 30 else 0.0)
        np.testing.assert_allclose(core.apply_night_zero_point(pts), [o.mag for o in pts])
        pts = self._night(6)
        for k, o in enumerate(pts):
            o.check_std = 11.0 + (0.2 if k >= 30 else 0.0)
        out = core.apply_night_zero_point(pts)
        self.assertAlmostEqual(out[0] - out[-1], (10.0 + 0.1) - (10.3 - 0.1))

    def test_nights_and_flips_without_a_recenter_are_as_before(self):
        # a 2-point night and a 3-point flip side keep their own level, as before 2.4.0
        pts = ([_obs(k, mag=10.0) for k in range(20)] + [_obs(20 + k, mag=10.2, segment="flip") for k in range(3)]
               + [_obs(100 + k, mag=10.4, night="JD2461001") for k in range(2)])
        np.testing.assert_allclose(core.normalize_segments(pts), 10.2)


class TestFrameAddedLater(unittest.TestCase):
    """S4."""

    def test_own_registration_picks_the_segment(self):
        known = ([(_reg(0.1 * k, 0), "", 0) for k in range(10)] + [(_reg(240, 0), "", 1) for _ in range(10)]
                 + [(_reg(0, 0, True), "flip", 1) for _ in range(10)])
        f = core.frame_jump
        self.assertEqual(f(_reg(239, 1), "", known, 0.27, 1), 1)
        self.assertEqual(f(_reg(2, 0), "", known, 0.27, 1), 0)
        self.assertEqual(f(_reg(3, 0, True), "flip", known, 0.27, 1), 1)
        self.assertIsNone(f(_reg(700, 0), "", known, 0.27, 1))          # at none of the night's pointings
        self.assertIsNone(f(_reg(2, 0), "", known, None, 1))            # no plate scale
        self.assertIsNone(f(None, "", known, 0.27, 1))
        self.assertIsNone(f(_reg(2, 0), "", [], 0.27, 1))

    def test_measure_skipped_frames_uses_it(self):
        src = _src()
        body = src[src.index("    def _remeasure_done("):]
        body = body[:body.index("self.observations = core.merge_nights(self.observations, new_points)")]
        self.assertIn('core.frame_jump(getattr(o, "_stack_reg", None), o.segment, known, scale, factor)', body)
        self.assertIn("if jump is None:", body)   # the old rule (the point before it) only as the fallback


class TestRunNotes(unittest.TestCase):
    """M1 (c) and S3 (b): a check that was not made is on the status line at the end of the run."""

    def test_notes_reach_the_status_line(self):
        import clearv_app
        status = types.SimpleNamespace(v="40 points across 1 night(s).")
        status.get, status.set = (lambda: status.v), (lambda t: setattr(status, "v", t))
        logged = []
        fake = types.SimpleNamespace(status=status, _log_only=logged.append, _run_notes=None)
        clearv_app.App._run_note(fake, clearv_app.App.NO_SCALE_NOTE)
        clearv_app.App._run_note(fake, clearv_app.App.NO_SCALE_NOTE)   # said once
        self.assertEqual(logged, [clearv_app.App.NO_SCALE_NOTE] * 2)
        clearv_app.App._show_run_notes(fake)
        self.assertEqual(status.v, "40 points across 1 night(s).  " + clearv_app.App.NO_SCALE_NOTE)
        self.assertEqual(fake._run_notes, [])
        clearv_app.App._show_run_notes(fake)
        self.assertEqual(status.v.count("Re-centers were not looked for"), 1)

    def test_a_failed_check_is_said(self):
        import clearv_app
        notes, lines = [], []
        fake = types.SimpleNamespace(log=lines.append, _log_only=lines.append, _run_note=notes.append,
                                     _run_jump_scale=(0.5, 1), NO_SCALE_NOTE=clearv_app.App.NO_SCALE_NOTE)
        pts = [_obs(k, jump=3) for k in range(10)]
        for o in pts:
            o._stack_reg = _reg(0, 0)
        with mock.patch.object(core, "mark_recenters", side_effect=RuntimeError("boom")):
            self.assertEqual(clearv_app.App._mark_recenters(fake, pts), [])
        self.assertEqual({o.jump for o in pts}, {0})
        self.assertEqual(len(notes), 1)
        self.assertIn("The re-center check could not be made on this run (RuntimeError)", notes[0])
        self.assertTrue(any("re-center check skipped: RuntimeError: boom" in ln for ln in lines))
        src = _src()
        done = src[src.index("    def _photometry_done(self"):src.index("    def _photometry_done_body(")]
        self.assertLess(done.index("self._close_job_findings(force=True)"), done.index("self._show_run_notes()"))

    def test_return_is_logged_as_a_return(self):
        import clearv_app
        lines = []
        fake = types.SimpleNamespace(log=lambda t, *a, **k: lines.append(t), _run_jump_scale=(0.27, 1),
                                     _run_note=lines.append, _log_only=lines.append,
                                     NO_SCALE_NOTE=clearv_app.App.NO_SCALE_NOTE)
        pts = [_obs(k) for k in range(30)]
        for k, o in enumerate(pts):
            o._stack_reg = _reg(240 if 10 <= k < 20 else 0, 0)
        found = clearv_app.App._mark_recenters(fake, pts)
        self.assertEqual(len(found), 2)
        self.assertEqual([o.jump for o in pts], [0] * 10 + [1] * 10 + [0] * 10)
        self.assertIn("The frames after it are a new segment", lines[0])
        self.assertIn("back to where it was earlier in the night", lines[1])
        self.assertIn("rejoin that earlier segment", lines[1])


class TestNoisePeriod(unittest.TestCase):
    def test_tuned_only_from_two_cycles(self):
        """S1."""
        self.assertEqual(core.TUNE_MIN_CYCLES, 2.0)
        p = 1.03 * TM.P_TRUE
        for hours, tuned in ((4.0, False), (6.0, False), (7.0, False), (7.6, True), (9.0, True)):
            t, m = TM.night(hours=hours)
            got = core.tune_noise_period(t, m, p)
            self.assertEqual(got != p, tuned, hours)
            r = core.measure_extremum(t, m, "max", period=p, window_period=None, n_boot=40, tune_period=True)
            self.assertEqual(r["noise_period"], p)
            self.assertEqual(r["noise_period_tuned"] != p, tuned, hours)

    def test_twice_the_series_period_wins(self):
        """S2."""
        f = core.extremum_noise_choice
        rows = [{"star": "X", "type": "min", "hjd": 2461353.8, "period_d": 0.3671}]
        self.assertEqual(f(series=0.1835, vsx=0.3670), {"period": 0.3670, "source": "VSX", "half": 0.1835})
        self.assertEqual(f(series=0.1835, csv_rows=rows, vsx=0.3670),
                         {"period": 0.3671, "source": "times CSV", "half": 0.1835})
        self.assertEqual(f(series=0.1835, typed=0.3760), {"period": 0.3760, "source": "Period box", "half": 0.1835})
        # not twice (over 3% off, the same period, three times): the series period stays
        for other in (0.3790, 0.1835, 0.5505, 0.0918):
            self.assertEqual(f(series=0.1835, vsx=other), {"period": 0.1835, "source": "series", "half": None})
        # without a series period the usual order
        self.assertEqual(f(vsx=0.3670), {"period": 0.3670, "source": "VSX", "half": None})
        self.assertEqual(core.extremum_noise_period(series=0.1835, vsx=0.3670), (0.3670, "VSX"))
        self.assertEqual(f(), {"period": None, "source": "none", "half": None})

    def test_fourier_warning_names_the_period(self):
        """N5."""
        src = _src("photometry_core.py")
        self.assertIn('f"the Fourier series at P {period:.5f} d{where} reads', src)
        self.assertNotIn("the Fourier series at the period reads", src)
        self.assertIn('where = f" ({period_source})" if period_source else ""', src)
        # the source words reach it from the window (what the result line says, S2's "half of it" included)
        app = _src()
        self.assertEqual(app.count("period_source=source_words)"), 2)
        self.assertIn('source_words = self._noise_source_words({"noise_source": noise_source, "noise_half": '
                      'noise_half})', app)

    def test_csv_column_for_the_period_used(self):
        """N3."""
        self.assertEqual(core.EXTREMUM_COLUMNS[-1], "noise_period_used_d")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.csv")
            core.save_extremum_row(path, {"star": "X", "type": "max", "hjd": 2461358.75799, "error_min": 3.8,
                                          "noise_period_d": 0.1416, "noise_period_source": "VSX",
                                          "noise_period_used_d": 0.14897})
            with open(path, encoding="utf-8") as fh:
                head = [ln for ln in fh if not ln.startswith("#")][0].strip().split(",")
            self.assertEqual(head[-3:], ["noise_period_d", "noise_period_source", "noise_period_used_d"])
            row = core.read_extremum_csv(path)[0]
            self.assertAlmostEqual(row["noise_period_used_d"], 0.14897)
            # a file saved before the column: it is added on the next save, blank in the old rows
            old = os.path.join(d, "old.csv")
            with open(old, "w", encoding="utf-8") as fh:
                fh.write(",".join(core.EXTREMUM_COLUMNS[:-1]) + "\n")
                fh.write("X,max,2461350.5" + "," * (len(core.EXTREMUM_COLUMNS) - 4) + "\n")
            core.save_extremum_row(old, {"star": "X", "type": "max", "hjd": 2461358.75799, "error_min": 3.8,
                                         "noise_period_d": 0.1416, "noise_period_used_d": 0.1416})
            rows = core.read_extremum_csv(old)
            self.assertEqual(len(rows), 2)
            first = min(rows, key=lambda r: r["hjd"])
            v = first.get("noise_period_used_d")
            self.assertTrue(v is None or v == "" or (isinstance(v, float) and math.isnan(v)))


@unittest.skipUnless(TM.HAVE_TK, "needs a display")
class TestWindow(TM.WindowUsesTheBestPeriod):
    """S2, N3 in the Time of max/min window. (The parent's own tests are not run again.)"""
    test_label_and_hint = test_series_period_with_empty_boxes = test_boxes_do_not_change_the_error = None

    def _measure(self, w):
        with mock.patch.object(self.ca.messagebox, "showinfo"), mock.patch.object(self.ca.messagebox, "showerror"):
            w.measure()

    def test_vsx_twice_the_series_period(self):
        with tempfile.TemporaryDirectory() as d:
            series = dict(self.GOOD, period_days=TM.P_TRUE / 2.0)
            w, path = self._open(d, star="S8 Half", series=series, vsx=TM.P_TRUE, nights=2)
            try:
                w.period.set("")
                w.epoch.set("")
                self._measure(w)
                r = w.results[0]
                self.assertEqual((r["noise_period"], r["noise_source"]), (TM.P_TRUE, "VSX"))
                self.assertAlmostEqual(r["noise_half"], TM.P_TRUE / 2.0)
                self.assertIn(f"error: noise model at P {TM.P_TRUE:.5f} d (VSX; the series period "
                              f"{TM.P_TRUE / 2.0:.5f} d is half of it", w.result_text.get())
                w.save()
                row = core.read_extremum_csv(path)[0]
                self.assertEqual(row["noise_period_source"], "VSX")
                self.assertAlmostEqual(row["noise_period_d"], TM.P_TRUE)
                self.assertAlmostEqual(row["noise_period_used_d"], TM.P_TRUE)   # a 6 h night: not tuned
            finally:
                self._close(w)

    def test_period_used_is_saved_when_tuned(self):
        with tempfile.TemporaryDirectory() as d:
            w, path = self._open(d, star="S8 Tuned", vsx=0.95 * TM.P_TRUE, hours=8.5)
            try:
                w.period.set("")
                w.epoch.set("")
                self._measure(w)
                r = w.results[0]
                self.assertAlmostEqual(r["noise_period"], 0.95 * TM.P_TRUE)
                self.assertLess(abs(r["noise_period_tuned"] - TM.P_TRUE), 0.015 * TM.P_TRUE)
                self.assertIn("; fitted to this night at ", w.result_text.get())
                w.save()
                row = core.read_extremum_csv(path)[0]
                self.assertAlmostEqual(row["noise_period_d"], 0.95 * TM.P_TRUE, places=7)
                self.assertAlmostEqual(row["noise_period_used_d"], r["noise_period_tuned"], places=7)
            finally:
                self._close(w)

    def test_no_period_leaves_the_column_blank(self):
        with tempfile.TemporaryDirectory() as d:
            w, path = self._open(d, star="S8 None")
            try:
                w.period.set("")
                w.epoch.set("")
                self._measure(w)
                w.save()
                row = core.read_extremum_csv(path)[0]
                self.assertEqual(row["noise_period_source"], "none")
                self.assertTrue(math.isnan(row["noise_period_used_d"]))
            finally:
                self._close(w)


class TestTrimParse(unittest.TestCase):
    """N2."""

    def test_only_clear_forms(self):
        import site_time
        f = trim_ui.TrimMixin._trim_parse
        jds = [2461329.60 + 0.001 * k for k in range(300)]
        self.assertAlmostEqual(f("2461329.699", jds), 2461329.699)
        self.assertAlmostEqual(f("61329.699", jds), 2461329.699)
        self.assertIsNone(f("  ", jds))
        mid = site_time.jd_to_local(jds[150])
        for text in ("2300", "23:00", "045", "0:45", "00:45", "2400"):
            jd = f(text, jds)
            got = site_time.jd_to_local(jd + 1e-6)
            want = (0, 0) if text == "2400" else (int(text.replace(":", "")[:-2] or 0), int(text[-2:]))
            self.assertEqual((got.hour, got.minute), want, text)
            self.assertLess(abs(jd - jds[150]), 0.51, text)
            self.assertIsNotNone(mid)
        for bad in ("0.45", "23.00", ".45", "23", "5", "23h00", "0,45", "1e3", "329.699", "2461329", "abc",
                    "25:00", "2360", "12:5", "1:2:3", "-0.2", "12345"):
            if bad == "2461329":
                self.assertAlmostEqual(f(bad, jds), 2461329.0)   # a full JD without decimals is still a JD
                continue
            with self.assertRaises(ValueError, msg=bad) as ctx:
                f(bad, jds)
            self.assertIn("is not clear. Type a clock time as HHMM or HH:MM", str(ctx.exception))
            self.assertIn("or a full JD", str(ctx.exception))


class TestTrimFixes(TT.TrimCase):
    def test_bare_decimal_is_refused(self):
        """N2 in the app: nothing is trimmed, and the message says what to type."""
        self.open()
        self.pick(N1)
        m = self.trim("0.45", "")
        self.assertEqual(m["error"], [])   # 2.4.0 fit-in F2: Trim's greyed reason and the status line, no box
        self.assertIn("Trim: '0.45' is not clear.", self.app.status.get())
        self.assertIn("Trim: '0.45' is not clear.", self.app._trim_why())
        self.assertFalse(any(core.is_trimmed(o) for o in self.app.observations))

    def test_dropped_trims_are_a_finding(self):
        """S3 (a)."""
        self.open()
        app = self.app
        pts = self.night(N1)
        seen = []
        real = self.real_log   # the app's own log (the test case swaps app.log for a list)
        with mock.patch.object(app, "log", lambda t, *a, **k: (seen.append(t), real(t, *a, **k))[1]):
            n0 = len(app.findings)
            app._carry_trims(N1, [[pts[0].jd - 1.0, pts[-10].jd]], pts)   # would leave 9 points
        self.assertNotIn("trims", app._night_info(N1))
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].startswith("⚠ "), seen[0])
        self.assertIn("the night's trims were dropped: they would leave 9 usable points of the new measurement, "
                      "and a night needs 20.", seen[0])
        self.assertEqual(app._msg_level(seen[0]), "warn")
        self.assertEqual(len(app.findings), n0 + 1)
        # trims that still fit are kept, with a plain note
        seen.clear()
        with mock.patch.object(app, "log", lambda t, *a, **k: seen.append(t)):
            app._carry_trims(N1, [[pts[3].jd, pts[6].jd]], pts)
        self.assertEqual(len(app._night_info(N1)["trims"]), 1)
        self.assertNotIn("⚠", seen[0])

    def test_minimum_holds_for_the_stacked_set_too(self):
        """N7: 40 frames, 8 stacks of 5... the night's stacked set has 24 points here; a trim that leaves 30 frames
        but only 18 stacks is refused whichever set is on screen."""
        self.open(n=40)
        app = self.app
        self.pick(N1)
        frames = self.night(N1)
        stacks = []
        for k in range(24):
            o = copy.copy(frames[0])
            o.jd = frames[0].jd + (frames[-1].jd - frames[0].jd) * k / 23.0
            o.flag = ""
            stacks.append(o)
        app._stack_sets[N1] = {"frame": list(frames), "stack": stacks, "meta": {}, "paths": []}
        try:
            self.assertTrue(app._stack_set_live(N1))
            self.assertEqual(app._trim_other_set(N1)[0], "Stacked")
            m = self.trim("", f"{frames[9].jd + 1e-6:.6f}")   # 10 of 40 frames, 6 of 24 stacks: 18 stacks left
            # fit-in F2: no box at all; the words are Trim's greyed reason and the status line
            self.assertEqual((len(m["error"]), len(m["info"])), (0, 0), m)
            said = app.status.get()
            self.assertIn("would leave 18 usable points", said)
            self.assertTrue(said.endswith("use Remove night."), said)
            self.assertIn("with Points: Stacked; a night needs at least 20 either way", said)
            self.assertEqual(said, app._trim_why())
            self.assertFalse(any(core.is_trimmed(o) for o in app.observations))
            m = self.trim("", f"{frames[4].jd + 1e-6:.6f}")   # 5 frames, 3 stacks: 21 left
            self.assertEqual(m["error"], [])
            self.assertEqual(sum(core.is_trimmed(o) for o in app.observations), 5)
            # and the other way round: the stacks on screen, the frames counted too
            other = [o for o in app.observations if o.night != N1]
            app.observations = other + stacks
            app._apply_trims()
            self.assertEqual(app._trim_other_set(N1)[0], "Per frame")
        finally:
            app._stack_sets.pop(N1, None)
        # a night with one point set: nothing else to count
        self.assertIsNone(app._trim_other_set(N1))

    def test_trimmed_points_are_not_flagged_off_scale(self):
        """N1."""
        self.open()
        app = self.app
        self.pick(N1)
        pts = self.night(N1)
        m = self.trim(f"{pts[3].jd - 1e-6:.6f}", f"{pts[6].jd + 1e-6:.6f}")
        self.assertEqual(m["error"], [])
        calls = []
        real = app._pin_off_scale

        def pin(ax, x, y, mask, alpha=1.0):
            if ax is app.out_ax1:   # the light curve (the check-star panel has its own count)
                calls.append(np.asarray(mask, dtype=bool).copy())
            return real(ax, x, y, mask, alpha)
        for o in pts[3:7]:
            o.mag += 3.0   # far off the y-scale of the used points
        with mock.patch.object(app, "_pin_off_scale", pin):
            app.draw_output()
        trimmed = np.array([core.is_trimmed(o) for o in app.observations], dtype=bool)
        self.assertEqual(int(trimmed.sum()), 4)
        self.assertTrue(calls)
        for mask in calls:
            self.assertFalse((mask & trimmed).any())
        self.assertNotIn("flagged off scale", app.out_ax1.get_title())
        self.assertIn("excluded & ~trimmed & nm & in_zoom", _src())


if __name__ == "__main__":
    unittest.main()


class TestOutAndBackThenFlip(unittest.TestCase):
    """Step 8 re-review S1: A -> B -> back to A -> flip: the flip is measured against A (the part just before it),
    not against B, and nothing false is marked."""

    def test_flip_after_a_return_uses_the_part_before_it(self):
        def ob(k, mag, segment="", jump=0):
            return core.Obs(jd=2461000.6 + k * 0.002, mag=mag, merr=0.01, cmag=9.0, kmag=10.5, airmass=1.1,
                            path=f"Light_{k:04d}.fit", exptime=60.0, flux_var=1.0, flux_comp=1.0,
                            night="JD2461000", segment=segment, jump=jump)
        pts = ([ob(k, 10.00) for k in range(10)] + [ob(10 + k, 10.05, jump=1) for k in range(10)]
               + [ob(20 + k, 10.00) for k in range(10)] + [ob(30 + k, 10.01, segment="flip") for k in range(10)])
        vals = [o.mag for o in pts]
        steps = core.night_steps(pts, vals)["JD2461000"]
        self.assertEqual([st["kind"] for st in steps], ["re-center", "flip"])
        self.assertAlmostEqual(steps[0]["offset"], 0.05, places=6)
        self.assertAlmostEqual(steps[1]["offset"], 0.01, places=6)
        self.assertAlmostEqual(core.flip_offsets(pts, vals)["JD2461000"]["offset"], 0.01, places=6)
        self.assertEqual(core.night_steps_text(steps), "re-center +0.050 ⚠, flip +0.010")
        lined = core.align_steps(pts, vals, core.night_steps(pts, vals))
        self.assertLess(max(abs(v - 10.0) for v in lined), 1e-9)
