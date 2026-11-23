"""2.4.0 step 8 (#66): a re-center (the pointing jumps between two consecutive frames with no meridian flip, HD 219134
10-11 Nov) starts a new segment like a flip: found from the frames' registrations (at least 20 arcsec AND 10 px), saved
with the series (Obs.jump), reported per night (Flip/re-center step), lined up by 'Line up segments' and by 'Line up
flip/re-center only (keep nights)', and seen by the Discovery scan and the Stack step."""
import os
import sys
import types
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import photometry_core as core      # noqa: E402
import stack_core                   # noqa: E402


def _src(name="clearv_app.py"):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


def _obs(k, mag=10.0, night="JD2461000", segment="", jump=0):
    return core.Obs(jd=2461000.6 + k * 0.002, mag=mag, merr=0.01, cmag=9.0, kmag=10.5, airmass=1.1,
                    path=f"Light_{k:04d}.fit", exptime=60.0, flux_var=1.0, flux_comp=1.0, night=night,
                    segment=segment, jump=jump)


def _reg(dx, dy, flipped=False):
    return {"dx": float(dx), "dy": float(dy), "angle": 0.0, "flipped": flipped}


class TestPointingJumps(unittest.TestCase):
    def test_the_hd219134_night(self):
        # 0.27"/px at Bin 1, color camera (superpixel): the image pixel is 0.54", 2 stored pixels
        regs = [_reg(0.1 * k, 0.0) for k in range(20)] + [_reg(85.0 + 0.1 * k, 83.0) for k in range(20)]
        got = core.pointing_jumps(regs, scale_arcsec=0.54, px_factor=2)
        self.assertEqual([j["index"] for j in got], [20])
        self.assertAlmostEqual(got[0]["arcsec"], 0.54 * np.hypot(83.1, 83.0), delta=0.1)   # about 64"
        self.assertAlmostEqual(got[0]["px"], 2 * np.hypot(83.1, 83.0), delta=0.2)          # about 235 px
        self.assertIn("″", core.recenter_text(got[0]))
        self.assertIn("px)", core.recenter_text(got[0]))

    def test_both_limits_must_be_passed(self):
        # 30 px but only 8" (fine scale): no jump
        self.assertEqual(core.pointing_jumps([_reg(0, 0), _reg(30, 0)], scale_arcsec=0.27, px_factor=1), [])
        # 25" but only 5 px (coarse scale): no jump
        self.assertEqual(core.pointing_jumps([_reg(0, 0), _reg(5, 0)], scale_arcsec=5.0, px_factor=1), [])
        # exactly at both limits: a jump (>=)
        self.assertEqual(len(core.pointing_jumps([_reg(0, 0), _reg(10, 0)], scale_arcsec=2.0, px_factor=1)), 1)
        # the pixel limit is in stored pixels: 6 image px of a 2x2 superpixel image are 12
        self.assertEqual(len(core.pointing_jumps([_reg(0, 0), _reg(6, 0)], scale_arcsec=4.0, px_factor=2)), 1)

    def test_small_dithers_and_drift_split_nothing(self):
        rng = np.random.default_rng(1)
        regs = [_reg(0.4 * k + rng.normal(0, 3), rng.normal(0, 3)) for k in range(200)]   # drift 80 px in all
        self.assertEqual(core.pointing_jumps(regs, scale_arcsec=0.54, px_factor=2), [])

    def test_no_time_gap_needed_and_unaligned_frames_are_skipped(self):
        regs = [_reg(0, 0), None, _reg(100, 0), _reg(100, 1)]
        got = core.pointing_jumps(regs, scale_arcsec=1.0)
        self.assertEqual([j["index"] for j in got], [2])

    def test_a_flip_is_not_a_jump(self):
        regs = [_reg(0, 0)] * 5 + [_reg(150, -90, flipped=True)] * 5
        self.assertEqual(core.pointing_jumps(regs, scale_arcsec=1.0), [])
        # but a re-center after the flip is one
        regs += [_reg(250, -90, flipped=True)] * 5
        self.assertEqual([j["index"] for j in core.pointing_jumps(regs, scale_arcsec=1.0)], [10])

    def test_no_plate_scale_finds_nothing(self):
        # review M1 (c): pixels alone called every dither a re-center, so without a scale nothing is a jump
        self.assertEqual(core.pointing_jumps([_reg(0, 0), _reg(12, 0)], scale_arcsec=None, px_factor=1), [])
        self.assertEqual(core.pointing_jumps([_reg(0, 0), _reg(900, 0)], scale_arcsec=float("nan")), [])


class TestMarkRecenters(unittest.TestCase):
    def test_counts_per_frame(self):
        pts = [_obs(k) for k in range(30)]
        regs = [_reg(0, 0)] * 10 + [_reg(100, 0)] * 10 + [_reg(100, 120)] * 10
        found = core.mark_recenters(pts, regs, 1.0, 1)
        self.assertEqual(len(found), 2)
        self.assertEqual([o.jump for o in pts], [0] * 10 + [1] * 10 + [2] * 10)
        self.assertEqual((found[0]["before"].path, found[0]["after"].path), ("Light_0009.fit", "Light_0010.fit"))

    def test_the_flip_itself_is_not_also_a_recenter(self):
        # a flip the alignment cannot see (rotator excursion): the points' segment changes where the pointing jumps
        pts = [_obs(k) for k in range(10)] + [_obs(k, segment="flip") for k in range(10, 20)]
        regs = [_reg(0, 0)] * 10 + [_reg(90, 40)] * 10
        self.assertEqual(core.mark_recenters(pts, regs, 1.0, 1), [])
        self.assertEqual({o.jump for o in pts}, {0})

    def test_points_out_of_time_order(self):
        pts = [_obs(k) for k in range(12)]
        regs = [_reg(0, 0)] * 6 + [_reg(100, 0)] * 6
        order = list(reversed(range(12)))
        core.mark_recenters([pts[k] for k in order], [regs[k] for k in order], 1.0, 1)
        self.assertEqual([o.jump for o in pts], [0] * 6 + [1] * 6)


class TestSteps(unittest.TestCase):
    def _night(self, night="JD2461000", step=0.05, flip=None, n=40, at=20, k0=0):
        pts = []
        for k in range(n):
            seg = "flip" if flip is not None and k >= flip else ""
            pts.append(_obs(k0 + k, mag=10.0 + (step if k >= at else 0.0) + (0.03 if seg else 0.0), night=night,
                            segment=seg, jump=1 if k >= at else 0))
        return pts

    def test_recenter_step_reported_and_lined_up(self):
        pts = self._night()
        vals = [o.mag for o in pts]
        steps = core.night_steps(pts, vals)
        self.assertEqual(len(steps["JD2461000"]), 1)
        st = steps["JD2461000"][0]
        self.assertEqual(st["kind"], "re-center")
        self.assertAlmostEqual(st["offset"], 0.05)
        self.assertEqual((st["n_before"], st["n_after"]), (20, 20))
        self.assertEqual(core.flip_offsets(pts, vals), {})   # no flip on this night
        np.testing.assert_allclose(core.align_steps(pts, vals, steps), 10.0)
        np.testing.assert_allclose(core.normalize_segments(pts), 10.025)   # each segment to the median of the two
        self.assertEqual(core.night_steps_text(steps["JD2461000"]), "re-center +0.050 ⚠")
        self.assertEqual(core.night_steps_text(None), "—")

    def test_flip_alone_reads_and_measures_as_before(self):
        pts = [_obs(k, mag=10.0 + (0.012 if k >= 15 else 0), segment="flip" if k >= 15 else "") for k in range(30)]
        vals = [o.mag for o in pts]
        old = core.flip_offsets(pts, vals)
        self.assertAlmostEqual(old["JD2461000"]["offset"], 0.012)
        self.assertEqual((old["JD2461000"]["n_before"], old["JD2461000"]["n_after"]), (15, 15))
        steps = core.night_steps(pts, vals)
        self.assertEqual(core.night_steps_text(steps["JD2461000"]), "+0.012")
        self.assertEqual(core.align_steps(pts, vals, steps), core.align_flips(pts, vals, old))
        self.assertEqual(core.segment_words(pts), ["before flip"] * 15 + ["after flip"] * 15)

    def test_flip_and_recenter_on_one_night(self):
        pts = self._night(step=0.05, flip=30, at=15)   # re-center at 15 (+0.05), flip at 30 (+0.03)
        vals = [o.mag for o in pts]
        steps = core.night_steps(pts, vals)["JD2461000"]
        self.assertEqual([s["kind"] for s in steps], ["re-center", "flip"])
        self.assertAlmostEqual(steps[0]["offset"], 0.05)
        self.assertAlmostEqual(steps[1]["offset"], 0.03)   # right at the flip, not against the whole first side
        self.assertAlmostEqual(steps[1]["shift"], 0.08)
        self.assertAlmostEqual(core.flip_offsets(pts, vals)["JD2461000"]["offset"], 0.03)
        np.testing.assert_allclose(core.align_steps(pts, vals, {"JD2461000": steps}), 10.0)
        self.assertEqual(core.night_steps_text(steps), "re-center +0.050 ⚠, flip +0.030 ⚠")
        words = core.segment_words(pts)
        self.assertEqual(words[0], "before flip / before re-center")
        self.assertEqual(words[20], "before flip / after re-center")
        self.assertEqual(words[-1], "after flip / after re-center")
        self.assertFalse(any("," in w for w in words))   # a CSV column

    def test_keep_nights_keeps_nights(self):
        a = self._night("JD2461000", step=0.05)
        b = self._night("JD2461001", step=0.0, k0=500)
        for o in b:
            o.mag += 0.3
            o.jump = 0
        pts = a + b
        vals = [o.mag for o in pts]
        out = core.align_steps(pts, vals, core.night_steps(pts, vals))
        np.testing.assert_allclose(out[:40], 10.0)
        np.testing.assert_allclose(out[40:], 10.3)   # the night-to-night change survives
        self.assertEqual(core.segment_words(pts)[40:], ["no flip"] * 40)   # a night without one reads as before
        self.assertEqual(core.segment_words(pts)[0], "no flip / before re-center")

    def test_too_few_points_on_a_side_gives_no_step(self):
        pts = self._night(at=37)   # 3 points after the re-center
        vals = [o.mag for o in pts]
        self.assertEqual(core.night_steps(pts, vals), {})
        self.assertEqual(core.align_steps(pts, vals, {}), vals)
        use = [k < 22 for k in range(40)]   # flagged points do not count
        self.assertEqual(core.night_steps(self._night(), vals, use=use), {})

    def test_check_star_zero_point_sees_the_segment(self):
        pts = self._night(step=0.0)
        for o in pts:
            o.check_std = 11.0 + (0.04 if o.jump else 0.0)
            o.mag = 10.0 + (0.04 if o.jump else 0.0)
        np.testing.assert_allclose(core.apply_night_zero_point(pts), 10.02)


class TestSeriesFile(unittest.TestCase):
    def test_jump_saved_and_old_files_read_zero(self):
        o = _obs(3, jump=2)
        raw = core.obs_to_dict(o)
        self.assertEqual(raw["jump"], 2)
        self.assertEqual(core.obs_from_dict(raw).jump, 2)
        raw.pop("jump")
        self.assertEqual(core.obs_from_dict(raw).jump, 0)   # saved before 2.4.0
        for bad in (None, "", "x", -1):
            self.assertEqual(core.obs_from_dict(dict(raw, jump=bad)).jump, 0)
        self.assertEqual(core.Obs.__dataclass_fields__["jump"].default, 0)


class TestScanAndStack(unittest.TestCase):
    def test_scan_boundary_kind(self):
        seg = np.array([0] * 10 + [1] * 10 + [2] * 10)
        jd = 2461000.5 + np.arange(30) * 0.001
        self.assertEqual(core.scan_boundaries(seg, jd), [(10, "flip"), (20, "flip")])   # as before
        self.assertEqual(core.scan_boundaries(seg, jd, [20]), [(10, "flip"), (20, "recenter")])
        res = {"at_boundary": {0: "recenter"}, "noise_at_boundary": {1: "recenter"}}
        self.assertIn("change begins at re-center: check", core.scan_cautions(res, 0))
        self.assertIn("noise begins at re-center: check", core.scan_cautions(res, 1))

    def test_no_stack_spans_a_recenter(self):
        pts = [_obs(k, jump=1 if k >= 6 else 0) for k in range(12)]
        recs = [stack_core.frame_record(o, _reg(0, 0)) for o in pts]
        self.assertEqual([r["jump"] for r in recs], [0] * 6 + [1] * 6)
        groups = stack_core.plan_groups(recs, 4) if hasattr(stack_core, "plan_groups") else None
        if groups is not None:
            for g in groups:
                self.assertEqual(len({m["jump"] for m in g["members"]}), 1)
            self.assertEqual([len(g["members"]) for g in groups], [4, 2, 4, 2])
        stack = _obs(100)
        stack_core.apply_member_peaks(stack, recs[6:10])
        self.assertEqual(stack.jump, 1)


class TestApp(unittest.TestCase):
    def test_done_step_marks_and_logs(self):
        import clearv_app
        lines = []
        notes = []
        fake = types.SimpleNamespace(log=lambda t, *a, **k: lines.append(t), _run_jump_scale=(0.54, 2),
                                     _run_note=notes.append, _log_only=lambda t: None,
                                     NO_SCALE_NOTE=clearv_app.App.NO_SCALE_NOTE)
        pts = [_obs(k) for k in range(40)]
        for k, o in enumerate(pts):
            o._stack_reg = _reg(0.1 * k, 0) if k < 20 else _reg(85 + 0.1 * k, 83)
        found = clearv_app.App._mark_recenters(fake, pts)
        self.assertEqual(len(found), 1)
        self.assertEqual([o.jump for o in pts], [0] * 20 + [1] * 20)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("Re-center: the pointing moved by 64″ ("), lines[0])
        self.assertNotIn("⚠", lines[0])   # a plain note
        self.assertIn("Flip/re-center step", lines[0])
        # no plate scale (review M1 c): nothing is looked for, and the run says so (not as a ⚠)
        lines.clear()
        fake._run_jump_scale = (None, 2)
        self.assertEqual(clearv_app.App._mark_recenters(fake, pts), [])
        self.assertEqual({o.jump for o in pts}, {0})
        self.assertEqual(lines, [])
        self.assertEqual(notes, [clearv_app.App.NO_SCALE_NOTE])
        self.assertIn("Re-centers were not looked for", notes[0])
        self.assertNotIn("⚠", notes[0])
        # no registrations (frames not aligned): nothing found, nothing said
        lines.clear()
        plain = [_obs(k) for k in range(5)]
        self.assertEqual(clearv_app.App._mark_recenters(fake, plain), [])
        self.assertEqual(lines, [])

    def test_wording_on_the_output_page(self):
        src = _src()
        self.assertIn('text="Line up flip/re-center only (keep nights)"', src)
        self.assertNotIn('"Line up flip only (keep nights)"', src)
        self.assertIn('("flip", "Flip/re-center step", 130)', src)
        self.assertIn("core.align_steps(obs_list, list(mag), flip_steps)", src)
        self.assertIn("self._mark_recenters(night_points)", src)
        self.assertLess(src.index("self._mark_rotator_excursion(night_points)\n"),
                        src.index("self._mark_recenters(night_points)"))   # the flip is known first


try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestOutputPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from unittest import mock
        import clearv_app as ca
        cls.ca, cls.mock = ca, mock
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test240_step8_recenter.log")
        cls.app = ca.App()
        cls.app.withdraw()
        cls.patches = [mock.patch.object(ca.messagebox, n, return_value=True)
                       for n in ("showwarning", "showinfo", "showerror", "askyesno")]
        for p in cls.patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.app.destroy()

    def _draw(self):
        got = {}

        def grab(series_lines, period_lines, color_lines, rows, cautions):
            got["rows"], got["cautions"] = [dict(r) for r in rows], list(cautions)
        with self.mock.patch.object(self.app, "_fill_summary", side_effect=grab):
            self.app.draw_output()
        return got

    def test_night_table_plot_and_line_up(self):
        app = self.app
        app.mode_key = "variables"
        rng = np.random.default_rng(5)
        pts = []
        for night, base in (("JD2461000", 0.0), ("JD2461001", 0.2)):
            for k in range(40):
                jump = 1 if (night == "JD2461000" and k >= 20) else 0
                o = _obs(k + (0 if night == "JD2461000" else 500), mag=10.0 + base + 0.05 * jump + rng.normal(0, 0.002),
                         night=night, jump=jump)
                o.filt = "CV"
                pts.append(o)
        app.observations = pts
        for var in (app.norm_segments, app.pier_segments, app.align_flip):
            var.set(False)
        got = self._draw()
        cells = {r["night"]: r["flip"] for r in got["rows"]}
        self.assertRegex(cells["JD2461000"], r"^re-center \+0\.05\d ⚠$")
        self.assertEqual(cells["JD2461001"], "—")
        self.assertFalse(any("re-center" in c.lower() for c in got["cautions"]))   # a plain note, no ⚠ caution
        dashed = [ln for ln in app.out_ax1.lines if ln.get_linestyle() == "--"]
        self.assertEqual(len(dashed), 1)   # the boundary shows on the light curve
        self.assertIn("re-center", app.out_ax1.get_title() + " ".join(t.get_text() for t in app.out_ax1.texts)
                      + " ".join(t.get_text() for t in app.out_fig.texts) + str(getattr(app, "_lc_title_key", "")))
        # keep nights: the step goes, the 0.2 mag between the nights stays
        app.align_flip.set(True)
        vals = np.array(app._analysis_values(pts))
        self.assertLess(abs(np.median(vals[20:40]) - np.median(vals[:20])), 0.003)
        self.assertAlmostEqual(float(np.median(vals[40:]) - np.median(vals[:20])), 0.2, delta=0.004)
        self.assertIn("flip and re-center lined up", (self._draw(), app.out_ax1.get_ylabel())[1])
        # line up segments: everything on one level
        app.align_flip.set(False)
        app.norm_segments.set(True)
        vals = np.array(app._analysis_values(pts))
        self.assertLess(float(np.ptp([np.median(vals[:20]), np.median(vals[20:40]), np.median(vals[40:])])), 0.003)
        app.norm_segments.set(False)
        heads = dict((c[0], c[1]) for c in app.SUMMARY_COLUMNS)
        self.assertEqual(heads["flip"], "Flip/re-center step")


if __name__ == "__main__":
    unittest.main()
