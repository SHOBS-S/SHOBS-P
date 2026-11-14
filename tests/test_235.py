"""2.3.5 (safety + bugs, milestone 2.3.5): #88 leave-one-comp-out with a comp missing in some frames, #80 two-star
solve, #81 flip-step hold, #83 self-test exit code, safe defaults on the "anyway?" questions."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402

import photometry_core as core  # noqa: E402
import transit_core as tc  # noqa: E402

DEPTH = 0.018
PLANET = {"name": "Synth b", "host": "Synth", "period": 3.0, "t0": 2461000.0, "t0_err": 0.0002,
          "period_err": 1e-6, "k": DEPTH ** 0.5, "a_rs": 8.0, "b": 0.3, "t14": 0.115, "teff": 5800.0,
          "depth": DEPTH}
TC = 2461000.0 + 1000 * PLANET["period"]


def synth(n=84, missing=None, seed=3, noise=0.003):
    """84 frames, 4 comps at different brightness, a common transparency drift; the target's flux is divided by the
    tied comp ensemble (the photometry rule). missing = (comp index, first frame, last frame) set to NaN."""
    rng = np.random.default_rng(seed)
    t = TC + np.linspace(-0.12, 0.14, n)
    prob = tc.TransitProblem(t - 2461000.0, np.ones(n), np.ones(n), None, PLANET["period"], {}, ("tc",),
                             {"tc": TC - 2461000.0, "k": PLANET["k"], "a_rs": 8.0, "b": 0.3, "q1": 0.4, "q2": 0.3,
                              "c0": 1.0})
    model = prob.model(prob.full([TC - 2461000.0]))
    drift = 0.05 * np.sin(np.linspace(0, 3, n))                         # mag, common to every star
    offsets = np.array([-1.2, -0.4, 0.3, 0.9])
    cm = offsets[None, :] + drift[:, None] + rng.normal(0, noise, (n, 4))
    if missing is not None:
        j, a, b = missing
        cm[a:b + 1, j] = np.nan
    ens = tc.tied_ensemble(cm)
    target = -2.5 * np.log10(model) + drift + rng.normal(0, noise, n)
    flux = 10 ** (-0.4 * (target - ens))
    flux /= np.nanmedian(flux[np.abs(t - TC) > 0.08])
    return t, flux, np.full(n, noise), cm, ["C1", "C2", "C3", "C4"]


def loo(t, flux, err, cm, names):
    main = tc.lsq_fit(t, flux, err, None, PLANET, detrend_airmass=False)
    summ = {"depth": main["depth"], "tc": (main["p"]["tc"], main["err"]["tc"])}
    return tc.leave_one_comp_out(t, flux, err, None, cm, names, PLANET, summ, detrend_airmass=False)


class TestLeaveOneOutMissingComp(unittest.TestCase):
    """#88: a comp missing in frames 61-84 (0-based 60-83) no longer drops those frames from every refit."""

    def test_all_present(self):
        r = loo(*synth())
        self.assertTrue(all(f["n_points"] == 84 for f in r["fits"]))
        self.assertLess(r["depth_spread"], 0.002)
        self.assertFalse([f for f in r["flags"] if f[0] == "warn"])

    def test_comp_missing_late(self):
        r = loo(*synth(missing=(3, 60, 83)))
        n = [f["n_points"] for f in r["fits"]]
        self.assertEqual(n, [84, 84, 84, 84], n)                         # the old np.mean gave 60 each
        for f in r["fits"]:
            self.assertAlmostEqual(f["depth"], DEPTH, delta=0.003)       # old: 2.10-2.67 %
        self.assertLess(r["depth_spread"], 0.003)
        self.assertFalse([f for f in r["flags"] if f[0] == "warn"], r["flags"])
        self.assertEqual(r["ref"]["n_points"], 84)
        self.assertIn("with all comps (84 points", r["note"])
        self.assertIn("full ensemble 1.", tc.comp_systematic_text(r))

    def test_full_frames_give_plain_mean(self):
        cm = np.random.default_rng(1).normal(10, 0.1, (30, 3))
        np.testing.assert_array_equal(tc.tied_ensemble(cm), np.mean(cm, axis=1))

    def test_note_names_a_refit_with_fewer_points(self):
        # C1-C3 missing in frames 0-9: C4 is the only comp there, so the refit without C4 loses those frames.
        t, flux, err, cm, names = synth()
        cm[0:10, 0:3] = np.nan
        r = loo(t, flux, err, cm, names)
        self.assertTrue(r["fewer"] and r["fewer"][0].startswith("C4"), r["fewer"])
        self.assertIn("Fewer points without C4", r["note"])


class TestTiedEnsembleMatchesPhotometry(unittest.TestCase):
    """transit_core.tied_ensemble must stay in step with photometry_core.night_ensemble (one rule, two copies)."""

    def test_same_numbers(self):
        rng = np.random.default_rng(7)
        n = 50
        cm = np.array([-1.0, 0.0, 0.7])[None, :] + rng.normal(0, 0.01, (n, 3))
        cm[30:45, 2] = np.nan
        cm[5:8, 0] = np.nan
        names = ["A", "B", "C"]
        pts = []
        for f in range(n):
            o = core.Obs(jd=2461000.0 + f, mag=10.0, merr=0.01, cmag=0.0, kmag=None, airmass=None, path="",
                         exptime=60.0, flux_var=0.0, flux_comp=0.0)
            o.comp_insts = [float(v) for v in cm[f]]
            pts.append(o)
        ref = core.night_ensemble(pts, names, names)["ens"]
        np.testing.assert_allclose(tc.tied_ensemble(cm), np.array(ref, dtype=float), rtol=0, atol=1e-12)

    def test_short_comp_left_out_like_photometry(self):
        rng = np.random.default_rng(8)
        n = 40
        cm = np.array([-1.0, 0.0, 0.7])[None, :] + rng.normal(0, 0.01, (n, 3))
        cm[10:, 2] = np.nan                                              # measured in 10 of 40: out of the night
        names = ["A", "B", "C"]
        pts = []
        for f in range(n):
            o = core.Obs(jd=2461000.0 + f, mag=10.0, merr=0.01, cmag=0.0, kmag=None, airmass=None, path="",
                         exptime=60.0, flux_var=0.0, flux_comp=0.0)
            o.comp_insts = [float(v) for v in cm[f]]
            pts.append(o)
        ref = core.night_ensemble(pts, names, names)["ens"]
        np.testing.assert_allclose(tc.tied_ensemble(cm), np.array(ref, dtype=float), rtol=0, atol=1e-12)


if __name__ == "__main__":
    unittest.main()
