"""2.4.0 step 6c-2: robustness refits decide their own flip step (#35); the comp survey judges each comp's own
flip step; ExoClock on a report fit uses the report planet's RA/Dec; the legend says "each frame" when nothing was
detrended."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

import transit_core as tc  # noqa: E402
from test_240_u import PLANET, night  # noqa: E402


def cancelling_comps(t, after, step_mag=0.03):
    """Three comps: A and B step by +/- step_mag at the flip and cancel in the set (WASP-49 b 19 Nov)."""
    a = 10.0 - step_mag * after
    b = 10.0 + step_mag * after
    c = np.full(t.size, 10.0)
    return np.column_stack([a, b, c])


class TestCompCheckOwnStep(unittest.TestCase):
    def test_leave_one_out_refits_gate_their_own_step(self):
        t, f, e, am, after = night(noise=0.0015)
        cm = cancelling_comps(t, after)
        summary = {"depth": (0.01, 0.0005), "tc": (2461000.70, 0.0010)}
        kw = dict(after_flip=after, flip_step=False)
        new = tc.leave_one_comp_out(t, f, e, am, cm, ["A", "B", "C"], PLANET, summary, flip_gated=True, **kw)
        old = tc.leave_one_comp_out(t, f, e, am, cm, ["A", "B", "C"], PLANET, summary, flip_gated=False, **kw)
        by_new = {x["name"]: x for x in new["fits"]}
        by_old = {x["name"]: x for x in old["fits"]}
        self.assertTrue(by_new["A"]["step"] and by_new["B"]["step"])
        self.assertFalse(by_new["C"]["step"])
        self.assertFalse(by_old["A"]["step"])
        self.assertLess(new["depth_spread"], old["depth_spread"])
        self.assertLess(new["depth_spread"], 0.003)
        # Fable S3: said in the note even when no comp is flagged
        self.assertEqual(sorted(new["step_other"]), ["A", "B"])
        self.assertIn("Without A, B the night needs a flip step of its own: those comps jump", new["note"])
        self.assertEqual(old["step_other"], [])

    def test_forced_step_kept_in_every_refit(self):
        t, f, e, am, after = night(noise=0.0015)
        cm = np.full((t.size, 3), 10.0)
        summary = {"depth": (0.01, 0.0005), "tc": (2461000.70, 0.0010)}
        rob = tc.robustness_check(t, f, e, am, PLANET, summary, (True, False, False), after_flip=after,
                                  flip_step=True, comp_mags=cm, comp_names=["A", "B", "C"], flip_gated=False)
        self.assertTrue(rob["ref"]["step"])
        self.assertTrue(all(r["step"] for r in rob["rows"]))


class TestRobustnessOwnStep(unittest.TestCase):
    def setUp(self):
        self.t, self.f, self.e, self.am, self.after = night(noise=0.0015)
        self.cm = cancelling_comps(self.t, self.after)
        self.summary = {"depth": (0.01, 0.0005), "tc": (2461000.70, 0.0010)}

    def run_check(self, gated):
        return tc.robustness_check(self.t, self.f, self.e, self.am, PLANET, self.summary, (True, False, False),
                                   after_flip=self.after, flip_step=False, comp_mags=self.cm,
                                   comp_names=["A", "B", "C"], flip_gated=gated)

    def test_each_refit_gates_its_own_step(self):
        rob = self.run_check(True)
        self.assertFalse(rob["ref"]["step"])          # the night as fitted: no step (the comps cancel)
        rows = {r["label"]: r for r in rob["rows"]}
        self.assertTrue(rows["A left out"]["step"])
        self.assertTrue(rows["B left out"]["step"])
        self.assertFalse(rows["C left out"]["step"])
        # with their own step the refits keep the depth: no false alarm from the inherited model
        for k in ("A left out", "B left out"):
            self.assertAlmostEqual(rows[k]["depth"], rob["ref"]["depth"], delta=0.0015)
        self.assertIn("Flip step: 2 of", rob["note"])
        self.assertIn("refits need a flip step of their own, though the night as fitted has none (A left out; B left out",
                      rob["note"])
        self.assertTrue(all(ord(c) < 128 for c in tc.robustness_text(rob, ascii_only=True).replace("±", "")))

    def test_inherited_step_is_the_old_behaviour(self):
        rob = self.run_check(False)
        rows = {r["label"]: r for r in rob["rows"]}
        self.assertFalse(rows["A left out"]["step"])
        self.assertNotIn("Flip step:", rob["note"])
        new = {r["label"]: r for r in self.run_check(True)["rows"]}
        ref = rob["ref"]["depth"]
        self.assertGreater(abs(rows["A left out"]["depth"] - ref), abs(new["A left out"]["depth"] - ref))

    def test_no_flip_no_step_line(self):
        rob = tc.robustness_check(self.t, self.f, self.e, self.am, PLANET, self.summary, (True, False, False),
                                  comp_mags=np.full((self.t.size, 3), 10.0), comp_names=["A", "B", "C"])
        self.assertTrue(all(not r["step"] for r in rob["rows"]))
        self.assertNotIn("Flip step:", rob["note"])


import comp_survey as cs  # noqa: E402


def mag_of_ppt(ppt):
    return -2.5 * np.log10(1 + ppt / 1000.0)


class TestOwnStepGate(unittest.TestCase):
    def test_wasp49_like_gradient(self):
        # WASP-49 b 19 Nov, all 232 frames: own steps (ppt) of the candidates in the brightness window
        ppt = np.array([0.5, 47.6, 30.2, -2.2, 23.2, 11.0, -19.9, -42.8, 25.2, 62.9, -58.7])
        st = mag_of_ppt(ppt)
        err = np.full(st.size, 0.0015)
        gone, kept = cs.own_step_out(st, err, np.ones(st.size, bool), keep_min=2)
        self.assertEqual(sorted(gone), [1, 2, 4, 5, 6, 7, 8, 9, 10])
        self.assertEqual(kept, [])
        # step 6d default (3): only 2 pass, so the steadiest reject (+11.0 ppt) is kept
        gone3, kept3 = cs.own_step_out(st, err, np.ones(st.size, bool))
        self.assertEqual(kept3, [5])
        self.assertNotIn(5, gone3)
        self.assertAlmostEqual(gone[10], -58.7, places=1)

    def test_noise_is_not_judged(self):
        st = mag_of_ppt(np.array([15.0, 1.0, -2.0]))
        err = np.array([0.008, 0.001, 0.001])      # 15 ppt but under 3 sigma of its error
        gone, _k = cs.own_step_out(st, err, np.ones(3, bool))
        self.assertEqual(gone, {})

    def test_steadiest_kept_when_all_jump(self):
        st = mag_of_ppt(np.array([30.0, -14.0, 22.0, -60.0]))
        gone, kept = cs.own_step_out(st, np.full(4, 0.001), np.ones(4, bool))
        # step 6d: the steadiest are kept until there are 3 (OWN_STEP_MIN_COMPS)
        self.assertEqual(kept, [0, 1, 2])
        self.assertEqual(sorted(gone), [3])
        gone, kept = cs.own_step_out(st, np.full(4, 0.001), np.ones(4, bool), keep_min=2)
        self.assertEqual(kept, [1, 2])
        self.assertEqual(sorted(gone), [0, 3])

    def test_ties_go_to_the_steadiest(self):
        # step 6d: same |own step|, the lower scatter tonight is let back first
        st = mag_of_ppt(np.array([20.0, -20.0, 40.0]))
        _g, kept = cs.own_step_out(st, np.full(3, 0.001), np.ones(3, bool), keep_min=1,
                                   scatter=np.array([0.004, 0.002, 0.001]))
        self.assertEqual(kept, [1])

    def test_limit_in_ppt_both_sides(self):
        # Fable N1: +10.03 ppt is beyond +-10 ppt (the mag limit used to allow +10.1 on the bright side)
        st = mag_of_ppt(np.array([10.03, -10.03, 9.9, -9.9]))
        gone, _k = cs.own_step_out(st, np.full(4, 1e-4), np.ones(4, bool), keep_min=0)
        self.assertEqual(sorted(gone), [0, 1])

    def test_only_judged_candidates(self):
        st = mag_of_ppt(np.array([50.0, 1.0, -1.0, np.nan]))
        judged = np.array([False, True, True, True])
        gone, kept = cs.own_step_out(st, np.full(4, 0.001), judged)
        self.assertEqual(gone, {})
        self.assertEqual(kept, [])


class TestFrameLegend(unittest.TestCase):
    def test_labels(self):
        try:
            import transit_ui as tu
        except Exception as exc:   # pragma: no cover (no Tk)
            self.skipTest(str(exc))
        self.assertEqual(tu.frame_points_label({"detrend_flags": (False, False, False)}), "each frame")
        self.assertEqual(tu.frame_points_label({"detrend_flags": (True, False, False)}), "each frame, detrended")
        self.assertEqual(tu.frame_points_label({"detrend_flags": (False, False, False), "flip": {"step": 1}}),
                         "each frame, flip step taken out")
        self.assertEqual(tu.frame_points_label({"detrend_flags": (False, True, False), "flip": {"step": 1}}),
                         "each frame, detrended, flip step taken out")


class TestChooseSetMinSize(unittest.TestCase):
    def test_min_size_keeps_three(self):
        # step 6d: a pair that cancels best is not chosen when the night was let back to 3 comps
        names = ["A", "B", "C", "D"]
        keys = [0.0, 0.1, 0.2, 0.3]
        steps = {"A": 0.004, "B": -0.004, "C": 0.003, "D": 0.006}
        b_rows, a_rows = np.arange(5), np.arange(5, 10)

        def curve(sel):
            s_ = np.mean([steps[n] for n in sel])
            return np.r_[np.zeros(5), np.full(5, s_)]
        two, _st, _er = cs.choose_set(names, keys, curve, b_rows, a_rows, count=3)
        self.assertEqual(len(two), 2)
        three, _st, _er = cs.choose_set(names, keys, curve, b_rows, a_rows, count=3, min_size=3)
        self.assertEqual(len(three), 3)
        # never asks for more than the pool holds
        small, _st, _er = cs.choose_set(names[:2], keys[:2], curve, b_rows, a_rows, count=3, min_size=3)
        self.assertEqual(len(small), 2)


if __name__ == "__main__":
    unittest.main()
