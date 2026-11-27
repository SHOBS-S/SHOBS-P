"""2.4.0 fit-in F4, the merge session (26 Nov 2026): the #115 edge rule for field-scan stars, m028 (a drift toward the
edge that keeps the sky ring on the frame is a plain note: test_240_f), and m509's same-star lookup on Load series'
read worker."""
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

    def test_scan_worker_passes_the_edge_table(self):
        import inspect
        src = inspect.getsource(ca.App) if HAVE_TK else ""
        if not src:
            self.skipTest("needs the app module")
        self.assertIn("core.scan_near_edge(pos, img.shape, sky_out)", src)
        self.assertIn("near_edge=np.vstack(edge_rows)", src)
        self.assertIn("core.chi2_level(chi2_limit, n_judged[i])", src)


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
