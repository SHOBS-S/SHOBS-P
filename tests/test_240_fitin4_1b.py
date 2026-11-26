"""2.4.0 fit-in F4, package F4-1 (b): late catalog answers applied in the background (#79 item 8a), a 180° turn
between frames = a meridian flip (8d), markers re-centred after a binning change (8b, m249 m248) and at Run (m378)."""
import math
import os
import sys
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photometry_core as core    # noqa: E402
import clearv_app                 # noqa: E402

App = clearv_app.App
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(name="clearv_app.py"):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def _body(src: str, start: str, end: str = "\n    def ") -> str:
    i = src.index(start)
    return src[i:src.index(end, i + len(start))]


def _field(shape=(120, 160), stars=((50.0, 50.0, 40000.0), (110.0, 70.0, 30000.0)), sigma=1.8, seed=1):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    img = np.full(shape, 300.0)
    for x, y, f in stars:
        img += f / (2 * math.pi * sigma ** 2) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma ** 2))
    return (img + rng.normal(0, 4.0, shape)).astype(np.float32)


class _Var:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v

    def set(self, v):
        self.v = v


def _fake(**kw):
    logged = []
    ns = types.SimpleNamespace(
        logged=logged, log=lambda t, **k: logged.append(t), status=_Var(""), busy=False, _solving=False,
        preview=None, preview_is_debayered=True, preview_path="/x/Light_0001.fits", _preview_token=3,
        refresh_preview=lambda: None, update_star_panel=lambda: None, findings=None,
        _seeing=lambda: 4.2, _edge_text=lambda xy: "", radius=_Var(6.0),
        watch_xy={}, scan_excluded=[], variable_marks=[], spare_marks=[], _comps_under={},
        target_xy=None, check_xy=None)
    for r in clearv_app.COMP_ROLES:
        setattr(ns, f"{r}_xy", None)
    for name in ("_late_key", "_catalog_retry_later", "_late_apply", "_late_resolve", "_catalog_late",
                 "_warn_marker_offsets", "_rescale_marks"):
        setattr(ns, name, types.MethodType(getattr(App, name), ns))
    ns._cat_retry_words = App._cat_retry_words
    for k in ("CATALOG_RETRY_S", "LATE_POLL_MS", "LATE_POLL_MAX", "MARKER_SNAP_FAR", "MARKER_STEP"):
        setattr(ns, k, getattr(App, k))
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class TestLateCatalogAnswers(unittest.TestCase):
    """8a: no 'Solve again to use them / to retry': late answers are used, missing ones asked again, by themselves."""

    def _app(self):
        timers = []
        solved = []
        app = _fake(preview=np.zeros((4, 4)))
        app.after = lambda ms, fn: timers.append((ms, fn)) or len(timers)
        app.label_aavso = lambda auto=False: solved.append(auto)
        return app, timers, solved

    def test_no_message_asks_to_solve_again_for_a_late_or_missing_catalog(self):
        src = _src()
        for name in ("    def _catalog_late(self", "    def _apply_field_check(self"):
            self.assertNotIn("Solve again", _body(src, name), name)
        self.assertNotIn("Solve again to retry", src)
        self.assertNotIn("Solve again to use", src)
        self.assertNotIn("Solve again later", src)

    def test_late_aavso_answer_is_applied_by_itself(self):
        app, timers, solved = self._app()
        app._catalog_late("AAVSO sequence", {"arrived": True, "n": 12, "seconds": 31, "retry": 2, "chartid": "X1"},
                          True)
        self.assertIn("12 sequence stars (chart X1); they are put to use now, by themselves.", app.logged[0])
        self.assertEqual(len(timers), 1)
        timers[0][1]()
        self.assertEqual(solved, [True])            # the quiet solve (no boxes), with nothing pressed
        self.assertEqual(app._late_solve, "apply")

    def test_late_answer_for_other_lights_is_only_logged(self):
        app, timers, solved = self._app()
        app._catalog_late("AAVSO sequence", {"arrived": True, "n": 12, "seconds": 31}, False)
        self.assertIn("(that solve was for other lights)", app.logged[0])
        self.assertEqual(timers, [])

    def test_late_gaia_part_applied_when_kept(self):
        app, timers, solved = self._app()
        app._catalog_late("Gaia DR3", {"arrived": True, "n": 5000, "seconds": 70, "cached": True}, True)
        self.assertIn("Kept for this field; they are put to use now, by themselves.", app.logged[0])
        timers[0][1]()
        self.assertEqual(solved, [True])

    def test_it_waits_while_a_job_runs_and_drops_for_another_image(self):
        app, timers, solved = self._app()
        app.busy = True
        app._late_apply()
        timers.pop()[1]()
        self.assertEqual(solved, [])
        self.assertEqual(timers[-1][0], App.LATE_POLL_MS)      # looks again later
        app.busy = False
        timers.pop()[1]()
        self.assertEqual(solved, [True])
        # another image on the page: the late answer is not applied to it
        app._late_apply()
        app._preview_token = 4
        timers.pop()[1]()
        self.assertEqual(solved, [True])

    def test_a_catalog_that_did_not_answer_is_asked_again_three_times(self):
        app, timers, solved = self._app()
        words = app._catalog_retry_later()
        self.assertIn("asked again by itself in about 45 s", words)
        self.assertEqual(app._catalog_retry_later(), words)     # several catalogs in one solve: one retry
        self.assertEqual(len(timers), 1)
        self.assertEqual(timers[0][0], 45000)
        timers.pop()[1]()
        self.assertEqual(solved, [True])
        self.assertEqual(app._late_solve, "retry")
        self.assertIn("about 2 min", app._catalog_retry_later())
        timers.pop()[1]()
        self.assertIn("about 5 min", app._catalog_retry_later())
        timers.pop()[1]()
        last = app._catalog_retry_later()
        self.assertIn("did not answer after 3 more tries by itself; it is asked again at the next solve", last)
        self.assertEqual(timers, [])

    def test_label_done_error_retries_and_a_solve_by_hand_resets(self):
        body = _body(_src(), "    def _label_done(self")
        self.assertIn("self._cat_retry = None   # a solve by hand starts the background retries afresh", body)
        self.assertIn('late, self._late_solve = getattr(self, "_late_solve", None), None', body)
        self.assertIn('("✖ " if gave_up else "")', body)


class TestHalfTurnIsAFlip(unittest.TestCase):
    """8d: frames turned by more than 90° against the reference are the flip segment."""

    def _pts(self, turns):
        pts = []
        for f, a in enumerate(turns):
            o = core.Obs(jd=2461000.0 + f * 0.001, mag=10.0, merr=0.01, cmag=0.0, kmag=None, airmass=None, path="",
                         exptime=60.0, flux_var=0.0, flux_comp=0.0)
            o._turn = a
            pts.append(o)
        return pts

    def test_frames_on_the_other_side_are_marked(self):
        pts = self._pts([0.1, -0.2, 0.0, 179.6, -179.9, 180.2, None])
        self.assertEqual(core.mark_flip_by_turn(pts), 3)
        self.assertEqual([o.segment for o in pts], ["", "", "", "flip", "flip", "flip", ""])

    def test_reference_after_the_flip_marks_the_frames_before_it(self):
        pts = self._pts([180.3, 179.9, 0.2, 0.0])
        core.mark_flip_by_turn(pts)
        self.assertEqual([o.segment for o in pts], ["flip", "flip", "", ""])   # as reg["flipped"] on a German mount

    def test_words_are_a_plain_note(self):
        text = App.flip_like_text({"turn": 180.2, "before": "314a", "after": "1b"})
        self.assertIn("between frames 314a and 1b: a meridian flip.", text)
        self.assertNotIn("run photometry again", text)
        self.assertNotIn("Is the Mount set right", text)
        src = _src()
        self.assertIn("core.mark_flip_by_turn(night_points)", src)
        self.assertNotIn('"⚠ " + self.flip_like_text(fl)', src)


class TestMarkersRecentred(unittest.TestCase):
    def test_run_snaps_a_marker_that_is_a_little_off(self):
        app = _fake(preview=_field())
        app.target_xy = (54.2, 50.0)          # 4 px off: beyond the centring's jump limit, inside the aperture
        with mock.patch.object(clearv_app, "ask_choice") as ask:
            left = app._warn_marker_offsets(6.0)
        ask.assert_not_called()
        self.assertEqual(left, [])
        self.assertLess(math.hypot(app.target_xy[0] - 50.0, app.target_xy[1] - 50.0), 0.6)
        line = [t for t in app.logged if "re-centred on its star before the run" in t]
        self.assertEqual(len(line), 1, app.logged)
        self.assertFalse(line[0].startswith("⚠"))

    def test_a_far_snap_is_asked_first_and_leave_keeps_the_warning(self):
        app = _fake(preview=_field())
        app.check_xy = (57.8, 50.0)           # 7.8 px: further than the 6 px aperture radius
        with mock.patch.object(clearv_app, "ask_choice", return_value="leave") as ask:
            left = app._warn_marker_offsets(6.0)
        ask.assert_called_once()
        self.assertIn("further than the aperture radius (6 px)", ask.call_args[0][2])
        self.assertEqual(ask.call_args[1].get("safe"), "leave")
        self.assertEqual([lab for lab, _o in left], ["Check"])
        self.assertEqual(app.check_xy, (57.8, 50.0))
        self.assertTrue(any(t.startswith("⚠ Check marker") and "Left where it is, as chosen" in t for t in app.logged))
        with mock.patch.object(clearv_app, "ask_choice", return_value="move"):
            left = app._warn_marker_offsets(6.0)
        self.assertEqual(left, [])
        self.assertLess(math.hypot(app.check_xy[0] - 50.0, app.check_xy[1] - 50.0), 0.6)

    def test_a_centred_marker_is_left_alone(self):
        app = _fake(preview=_field())
        app.target_xy = (50.3, 49.8)
        self.assertEqual(app._warn_marker_offsets(6.0), [])
        self.assertEqual(app.target_xy, (50.3, 49.8))
        self.assertEqual(app.logged, [])

    def test_binning_change_moves_recentres_and_keeps_the_spares(self):
        # marks made on a 60 x 80 image (bin 2); the new image is 120 x 160 (bin 1)
        new = _field()
        app = _fake(preview=new)
        app.target_xy = (24.0, 25.2)          # (50, 50) on the new image is (24.75, 24.75) on the old: a little off
        c1, c2 = clearv_app.COMP_ROLES[:2]
        setattr(app, f"{c1}_xy", (54.75, 34.75))   # (110, 70)
        setattr(app, f"{c2}_xy", (10.0, 10.0))     # blank sky
        app.spare_marks = [(54.75, 34.75, "SP1")]
        app._rescale_marks((60, 80), (120, 160))
        self.assertLess(math.hypot(app.target_xy[0] - 50.0, app.target_xy[1] - 50.0), 0.6)
        got = getattr(app, f"{c1}_xy")
        self.assertLess(math.hypot(got[0] - 110.0, got[1] - 70.0), 0.6)
        self.assertEqual(len(app.spare_marks), 1)                       # m248: kept and moved, not cleared
        self.assertAlmostEqual(app.spare_marks[0][0], 110.0, delta=0.01)
        text = " ".join(app.logged)
        self.assertIn("3 markers moved to the same stars on the new image (x2)", text)
        self.assertNotIn("Check them before running", text)
        self.assertNotIn("Solve again", text)
        self.assertIn("No star was found under Comp 2", text)

    def test_binning_change_all_found_says_recentred(self):
        app = _fake(preview=_field())
        app.target_xy = (24.75, 24.75)
        app._rescale_marks((60, 80), (120, 160))
        self.assertEqual(app.logged, ["Binning changed: 1 marker moved to the same star on the new image (x2) and "
                                      "re-centred."])


class TestApertureRefit(unittest.TestCase):
    """8e: an aperture kept from the last field that does not fit this field's stars is re-fitted, plain note."""

    def _app(self, r=8.0, fwhm=2.3, observations=(), set_for=None, folder="/n2/lights"):
        app = _fake(observations=list(observations), light_dir=_Var(folder), sky_in=_Var(12.0), sky_out=_Var(18.0),
                    _aperture_set_for=set_for, _camera_folders=None, _CAMERA_VALUES="values")
        app.radius = _Var(r)
        app._seeing = lambda: fwhm
        app._box_number = lambda var: float(var.get())
        app._aperture_refit = types.MethodType(App._aperture_refit, app)
        return app

    def test_too_large_aperture_is_refitted_once(self):
        app = self._app()
        self.assertTrue(app._aperture_refit(None))
        prop = core.suggest_apertures(2.3)
        self.assertEqual((app.radius.get(), app.sky_in.get(), app.sky_out.get()),
                         (prop["radius"], prop["sky_in"], prop["sky_out"]))
        self.assertTrue(app.logged[0].startswith("Aperture re-fitted to this field: r = 8 px was kept from the last "
                                                 "field, and it is 3.5 × the stars' FWHM here (2.3 px)."))
        self.assertNotIn("⚠", app.logged[0])
        self.assertEqual(app._aperture_set_for, "/n2/lights")
        self.assertFalse(app._aperture_refit(None))        # these lights have their sizes now

    def test_too_small_and_donut(self):
        app = self._app(r=3.0, fwhm=6.0)
        self.assertTrue(app._aperture_refit(None))
        app = self._app(r=8.0)
        self.assertTrue(app._aperture_refit({"clip_below": 12.0, "radius": 14.0, "sky_in": 20.0, "sky_out": 28.0}))
        self.assertEqual(app.radius.get(), 14.0)
        self.assertIn("clips the ring", app.logged[0])

    def test_left_alone_when_it_fits_or_is_yours_or_a_series_is_open(self):
        self.assertFalse(self._app(r=5.0)._aperture_refit(None))                       # fits
        self.assertFalse(self._app(set_for="/n2/lights")._aperture_refit(None))        # set for these lights
        self.assertFalse(self._app(observations=[1])._aperture_refit(None))            # a series keeps one aperture
        src = _src()
        self.assertIn("if not typed and self._aperture_refit(donut):", src)
        self.assertIn("self._aperture_set_for = self.light_dir.get().strip()",
                      _body(src, "    def _aperture_caution_recheck(self"))


if __name__ == "__main__":
    unittest.main()
