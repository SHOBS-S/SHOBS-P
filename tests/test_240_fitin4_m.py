"""2.4.0 fit-in F4, the merge session (26 Nov 2026): the #115 edge rule for field-scan stars, m028 (a drift toward the
edge that keeps the sky ring on the frame is a plain note: test_240_f), and m509's same-star lookup on Load series'
read worker."""
import math
import os
import sys
import threading
import time
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
    import test_240_m as M  # noqa: E402
    Base = M.AppCase
else:
    Base = unittest.TestCase


def _field(n_frames=80, n_stars=60, seed=3):
    rng = np.random.default_rng(seed)
    mags = np.linspace(9.0, 13.0, n_stars)
    err = 0.002 + 0.003 * 10 ** (0.4 * (mags - 9.0)) / 10
    inst = mags[None, :] + rng.normal(0, 1, (n_frames, n_stars)) * err[None, :]
    merr = np.broadcast_to(err, inst.shape).copy()
    return inst, merr


class TestNearEdge(unittest.TestCase):
    def test_sky_ring_past_the_edge(self):
        pos = [(5.0, 50.0), (30.0, 30.0), (99.0 - 10.0, 50.0), (50.0, 20.0), (np.nan, 10.0)]
        near = core.scan_near_edge(pos, (100, 100), 20.0)
        self.assertEqual(near.tolist(), [True, False, True, False, True])

    def test_edge_points_are_not_judged_by_own_errors(self):
        inst, merr = _field()
        k = 30
        inst[60:, k] += 0.08                 # an edge star: its last 20 frames jump (it ran into the edge)
        near = np.zeros(inst.shape, dtype=bool)
        near[60:, k] = True
        without = core.field_variability(inst, merr=merr)
        level = core.chi2_level(core.SUSPECT_EXCESS_CHI2, inst.shape[0])
        self.assertGreater(without["excess_chi2"][k], level)    # listed without the rule
        with_rule = core.field_variability(inst, merr=merr, near_edge=near)
        self.assertLess(with_rule["excess_chi2"][k], level)     # not listed with it
        self.assertEqual(int(with_rule["edge_points"][k]), 20)
        self.assertEqual(int(with_rule["n_chi2"][k]), inst.shape[0] - 20)
        self.assertEqual(int(with_rule["n_chi2"][0]), inst.shape[0])
        # the scatter test and the light curve keep every point
        np.testing.assert_allclose(with_rule["scatter"], without["scatter"])
        np.testing.assert_allclose(with_rule["corrected"], without["corrected"])

    def test_a_real_variable_away_from_the_edge_stays(self):
        inst, merr = _field()
        k = 20
        inst[:, k] += 0.03 * np.sin(np.linspace(0, 6 * np.pi, inst.shape[0]))
        near = np.zeros(inst.shape, dtype=bool)
        near[:, 45] = True                    # another star at the edge all night
        st = core.field_variability(inst, merr=merr, near_edge=near)
        self.assertGreater(st["excess_chi2"][k], core.chi2_level(core.SUSPECT_EXCESS_CHI2, inst.shape[0]))
        self.assertTrue(np.isnan(st["excess_chi2"][45]))    # no point left to judge: not a Suspect by own errors

    def test_a_star_at_the_edge_most_of_the_night_is_not_made_a_suspect(self):
        """Review M1: the judged points are taken about their own median."""
        for share in (0.45, 0.6, 0.9):
            inst, merr = _field(n_frames=97, n_stars=80, seed=7)
            level = core.chi2_level(core.SUSPECT_EXCESS_CHI2, inst.shape[0])
            near = np.zeros(inst.shape, dtype=bool)
            n_edge = int(round(share * inst.shape[0]))
            for k in range(10, 70, 6):
                near[-n_edge:, k] = True
                inst[-n_edge:, k] += 4.0 * merr[0, k]       # the edge artifact the rule is for
            st = core.field_variability(inst, merr=merr, near_edge=near)
            for k in range(10, 70, 6):
                n_j = int(st["n_chi2"][k])
                ex = st["excess_chi2"][k]
                if n_j:
                    self.assertLess(ex, core.chi2_level(core.SUSPECT_EXCESS_CHI2, n_j), (share, k, ex))
                else:
                    self.assertTrue(np.isnan(ex))
                self.assertLess(ex if np.isfinite(ex) else 0.0, level, (share, k))

    def test_a_handful_of_points_is_not_judged(self):
        inst, merr = _field(n_frames=60)
        near = np.zeros(inst.shape, dtype=bool)
        near[8:, 5] = True                                   # 8 clear points of 60: under 10
        near[:35, 6] = True                                  # 25 clear of 60: over a third, judged
        st = core.field_variability(inst, merr=merr, near_edge=near)
        self.assertEqual(int(st["n_chi2"][5]), 0)
        self.assertTrue(np.isnan(st["excess_chi2"][5]))
        self.assertEqual(int(st["n_chi2"][6]), 25)
        self.assertTrue(np.isfinite(st["excess_chi2"][6]))

    def test_shape_must_match(self):
        inst, merr = _field()
        with self.assertRaises(ValueError):
            core.field_variability(inst, merr=merr, near_edge=np.zeros((3, 3), dtype=bool))

    def test_caution_on_the_star(self):
        res = {"edge_points": {4: 12, 5: 1}}
        self.assertIn("at the frame edge on 12 frames (not judged by its own errors there): check its light curve",
                      core.scan_cautions(res, 4))
        self.assertIn("at the frame edge on 1 frame (not judged", core.scan_cautions(res, 5)[0])
        self.assertEqual(core.scan_cautions(res, 6), [])
        res["suspects_where"] = {"points": "stacks"}
        self.assertIn("at the frame edge on 12 stacks", core.scan_cautions(res, 4)[0])

    def test_scan_worker_passes_the_edge_table(self):
        import inspect
        src = inspect.getsource(ca.App) if HAVE_TK else ""
        if not src:
            self.skipTest("needs the app module")
        self.assertIn("core.scan_near_edge(pos, img.shape, sky_out)", src)
        self.assertIn("near_edge=np.vstack(edge_rows)", src)
        self.assertIn("core.chi2_level(chi2_limit, n_judged[i])", src)


def _obs(f, comps, drop=None):
    o = core.Obs(jd=2461000.0 + f * 0.001, mag=10.0, merr=0.01, cmag=0.0, kmag=None, airmass=None, path=f"f{f}",
                 exptime=60.0, flux_var=0.0, flux_comp=0.0, night="N")
    o.comp_insts = [float(v) for v in comps]
    o.target_inst = -9.2
    o.target_var = 1e-6
    o.comp_vars = [1e-6] * len(comps)
    o.comp_drop = dict(drop or {})
    return o


NAN = float("nan")


class TestNoFluxFrameKeepsTheComp(unittest.TestCase):
    """F4 review S1: one comp set per night counts only frames where a comp left the frame."""

    def test_one_cloud_frame(self):
        pts = [_obs(f, [-9.0, -8.5, NAN if f == 31 else -7.6],
                    {"C": "no flux above sky in this frame"} if f == 31 else None) for f in range(60)]
        e = core.night_ensemble(pts, ["A", "B", "C"], ["A", "B", "C"])
        self.assertEqual(e["use"], ["A", "B", "C"])
        self.assertEqual(e["out"], [])
        self.assertTrue(math.isnan(e["ens"][31]))           # that frame alone gets no comparison
        self.assertTrue(all(math.isfinite(v) for k, v in enumerate(e["ens"]) if k != 31))

    def test_two_comps_each_without_one_frame(self):
        pts = [_obs(f, [NAN if f == 5 else -9.0, NAN if f == 9 else -8.5],
                    {"A": "no flux above sky in this frame"} if f == 5 else
                    {"B": "no flux above sky in this frame"} if f == 9 else None) for f in range(40)]
        e = core.night_ensemble(pts, ["A", "B"], ["A", "B"])
        self.assertEqual(e["use"], ["A", "B"])
        self.assertEqual(e["few"], [])                     # no "no comp was measured in every frame"

    def test_off_the_edge_still_puts_it_out(self):
        pts = [_obs(f, [-9.0, -8.5, NAN if f >= 50 else -7.6],
                    {"C": "off the frame edge"} if f >= 50 else None) for f in range(60)]
        e = core.night_ensemble(pts, ["A", "B", "C"], ["A", "B", "C"])
        self.assertEqual(e["out"], ["C"])
        self.assertIn("off the frame or its edge", e["why"]["C"])

    def test_no_reason_on_file_says_not_measured(self):
        pts = [_obs(f, [-9.0, -8.5, NAN if f == 3 else -7.6]) for f in range(30)]
        for o in pts:
            o.comp_drop = {}
        e = core.night_ensemble(pts, ["A", "B", "C"], ["A", "B", "C"])
        self.assertEqual(e["out"], ["C"])                   # an older night: as before
        self.assertEqual(e["why"]["C"], "not measured in 1 of 30 frames")


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestOwnRangeStands(Base):
    """F4 review S2: after the boxes were widened for a known period, a range the user then types stands."""

    def test_narrowed_by_hand_is_searched_as_typed(self):
        import test_240_fitin4_2 as F2
        app = self.app
        app.mode_key = "variables"
        app.observations = F2._series(period=0.25)
        app.min_period.set(0.02)
        app.max_period.set(0.1)
        app.catalog_period = 0.25
        app.mark_periods.set("")
        app._period_range_widened = app._period_range_own = None
        app._refresh_period()
        self.assertGreater(float(app.max_period.get()), 0.25 / 0.9)        # widened for VSX 0.25 d
        app.max_period.set(0.1)                                             # the user looks for a short period
        app._refresh_period()
        self.assertEqual(float(app.max_period.get()), 0.1)                  # his range stands
        self.assertEqual(app._period_range_used, (0.02, 0.1))
        self.assertIn("as you set it: VSX 0.25 d", app._period_range_note)
        self.assertNotIn("⚠", app._period_range_note)
        app._refresh_period()                                               # and again on a refit
        self.assertEqual(float(app.max_period.get()), 0.1)
        app._clear_series_state()                                           # another series follows again
        self.assertIsNone(app._period_range_own)


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestSameStarOnTheWorker(Base):
    def pump(self, cond, seconds=120):
        end = time.time() + seconds
        while time.time() < end and not cond():
            self.app.update()
            time.sleep(0.01)
        return cond()

    def test_lookup_runs_off_the_window_thread(self):
        app = self.app
        cat = {"TYC 4006-1008-1": 8.728, "Gaia DR3 439074923286442112": 11.31}
        pts = M._points(list(cat), cat, n=12)
        meta = M._old_meta(list(cat), cat)           # a series of HD 219134, no RA/Dec in the file
        meta.pop("ra_hours", None)
        meta.pop("dec_deg", None)
        path = os.path.join(self.dir, "m509.cvseries")
        core.save_series(path, meta, pts)
        app.star_id.set("HIP 114622")
        app.ra_hours.set("")
        app.dec_deg.set("")
        main = threading.get_ident()
        seen = {}

        def same(self_, cur, meta_, cur_pos=None):
            seen["thread"] = threading.get_ident()
            seen["cur_pos"] = cur_pos
            return True
        asked = []
        lines = []
        real_log = app.log
        with mock.patch.object(ca.App, "SERIES_SYNC", False), \
                mock.patch.object(ca.filedialog, "askopenfilename", return_value=path), \
                mock.patch.object(ca.App, "_same_star_by_lookup", same), \
                mock.patch.object(ca.messagebox, "askyesno", lambda *a, **k: asked.append(a) or False), \
                mock.patch.object(app, "log", lambda t, *a, **k: (lines.append(t), real_log(t, *a, **k))):
            app.load_series()
            self.assertTrue(self.pump(lambda: app.status.get().startswith("Loaded ") or not app.busy))
            self.pump(lambda: not app.busy, 30)
        self.assertIn("thread", seen)
        self.assertNotEqual(seen["thread"], main)            # the network lookup was not on the window thread
        self.assertEqual(seen["cur_pos"], ("", ""))         # the Input page's RA/Dec, read on the window thread
        self.assertEqual(asked, [])                         # same star: no question
        self.assertTrue(any("are the same star" in t for t in lines), lines)


if __name__ == "__main__":
    unittest.main()
