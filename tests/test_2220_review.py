"""2.2.20 review fixes: no joint flip step while the single-night fit has none; ExoClock leaves out put-back frames."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import multinight
import transit_core as tc


class TestJointFlipStep(unittest.TestCase):
    def _night(self):
        t = 2461334.5 + np.linspace(0, 0.2, 60)
        return {"t": t, "flux": np.ones_like(t), "err": np.full_like(t, 0.002), "airmass": 1 + 0.5 * (t - t[0]),
                "t_flip": float(t[30]), "group": "g"}

    def test_no_step_when_disabled(self):
        self.assertFalse(tc.FLIP_STEP_ENABLED)
        jp = multinight.JointProblem([self._night()], 1.5, {}, {}, ["g"], {})
        self.assertFalse(any(n.startswith("step:") for n in jp.free))

    def test_step_when_enabled(self):
        old = tc.FLIP_STEP_ENABLED
        tc.FLIP_STEP_ENABLED = True
        try:
            jp = multinight.JointProblem([self._night()], 1.5, {}, {}, ["g"], {})
            self.assertIn("step:0", jp.free)
        finally:
            tc.FLIP_STEP_ENABLED = old


if __name__ == "__main__":
    unittest.main()
