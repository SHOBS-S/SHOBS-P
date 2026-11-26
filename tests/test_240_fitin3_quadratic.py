"""2.4.0 fit-in F3: the transit model is the exact closed form for quadratic limb darkening (Mandel & Agol 2002).

transit_core.occultation is checked against the model it replaces (occultation_annulus_reference, the star cut into
annuli, kept for these tests only), against the exact geometry of a uniform disk, against a direct numerical integral,
at every contact point, for the array shapes callers pass, and for speed."""
import math
import os
import sys
import time
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import transit_core as tc  # noqa: E402

PPM = 1e-6
# limb darkening: none, typical, the corners of the physical triangle, a Sloan-table value with negative u2
LD = [(0.0, 0.0), (0.4, 0.25), (0.3, 0.3), (0.5, 0.0), (0.0, 0.5), (1.0, 0.0), (0.0, 1.0), (0.9288, -0.0827),
      (0.2, 0.44)]
K_TRANSIT = [0.003, 0.01, 0.02, 0.05, 0.08, 0.1, 0.12, 0.15]
K_LARGE = [0.2, 0.3, 0.49, 0.5, 0.51, 0.8, 1.0, 1.2, 2.0, 5.0]


def z_grid(k, n=401):
    """A dense grid over the whole transit plus every special separation and points a hair to each side of it."""
    edge = abs(1.0 - k)
    special = [0.0, 1e-13, 1e-9, k, edge, 1.0 + k, 0.5, 1.0, max(k - 1.0, 0.0)]
    near = [s + d for s in (k, edge, 1.0 + k, max(k - 1.0, 0.0)) for d in (-1e-6, -1e-10, -1e-13, 1e-13, 1e-10, 1e-6)]
    z = np.concatenate([np.linspace(0.0, 1.0 + k + 0.1, n), special, near])
    return np.abs(z)


def uniform_exact(z, k):
    """Flux of a uniform disk: 1 - (area of overlap of the two circles) / pi, from plain geometry with math only."""
    out = []
    for zi in np.atleast_1d(z):
        if zi >= 1.0 + k:
            area = 0.0
        elif zi <= abs(1.0 - k):
            area = math.pi * min(1.0, k) ** 2
        else:
            c1 = (zi * zi + 1.0 - k * k) / (2.0 * zi)
            c2 = (zi * zi + k * k - 1.0) / (2.0 * zi * k)
            sq = max((-zi + 1.0 + k) * (zi + 1.0 - k) * (zi - 1.0 + k) * (zi + 1.0 + k), 0.0)
            area = math.acos(max(-1.0, min(1.0, c1))) + k * k * math.acos(max(-1.0, min(1.0, c2))) - 0.5 * math.sqrt(sq)
        out.append(1.0 - area / math.pi)
    return np.array(out)


def integral_exact(z, k, u1, u2):
    """Flux by direct integration over the star's radius (SciPy quad): the planet covers the arc 2*phi(r) of the
    circle of radius r. Independent of both models."""
    from scipy.integrate import quad

    def phi(r):
        if r <= k - z:
            return math.pi
        if r <= z - k or r >= z + k:
            return 0.0
        return math.acos(max(-1.0, min(1.0, (r * r + z * z - k * k) / (2.0 * r * z))))

    def f(theta):                                    # r = sin(theta): smooth at the limb
        r, mu = math.sin(theta), math.cos(theta)
        return (1.0 - u1 * (1.0 - mu) - u2 * (1.0 - mu) ** 2) * 2.0 * r * phi(r) * mu

    lo = 0.0 if k > z else math.asin(min(1.0, max(0.0, z - k)))
    hi = math.asin(min(1.0, z + k))
    pts = [math.asin(x) for x in (abs(z - k),) if 0.0 < x < 1.0]
    blocked = quad(f, lo, hi, points=pts or None, epsabs=1e-13, epsrel=1e-13, limit=400)[0]
    return 1.0 - blocked / (math.pi * (1.0 - u1 / 3.0 - u2 / 6.0))


class AgainstReference(unittest.TestCase):
    def test_transiting_planets_within_2_ppm_of_the_old_model(self):
        """The requirement: max |new - reference| <= 2 ppm over (k, z, u1, u2), every special point included."""
        worst = 0.0
        for k in K_TRANSIT:
            z = z_grid(k)
            for u1, u2 in LD:
                d = np.abs(tc.occultation(z, k, u1, u2) - tc.occultation_annulus_reference(z, k, u1, u2))
                self.assertTrue(np.isfinite(d).all(), (k, u1, u2))
                worst = max(worst, float(d.max()))
                self.assertLessEqual(d.max(), 2 * PPM, (k, u1, u2, z[d.argmax()]))
        self.assertLess(worst, 2 * PPM)

    def test_large_planets_within_the_old_models_own_error(self):
        """For a planet from 0.2 of the star up to larger than the star the 200-annulus model is itself off by up to
        ~12 ppm (its error falls as more annuli are used); the new model stays within that, and within 0.1 ppm of
        the reference run with 4000 annuli."""
        for k in K_LARGE:
            z = z_grid(k, 201)
            for u1, u2 in LD:
                new = tc.occultation(z, k, u1, u2)
                self.assertTrue(np.isfinite(new).all(), (k, u1, u2))
                d200 = np.abs(new - tc.occultation_annulus_reference(z, k, u1, u2))
                self.assertLessEqual(d200.max(), 15 * PPM, (k, u1, u2, z[d200.argmax()]))
                d4000 = np.abs(new - tc.occultation_annulus_reference(z, k, u1, u2, n_annuli=4000))
                self.assertLessEqual(d4000.max(), 0.1 * PPM, (k, u1, u2, z[d4000.argmax()]))

    def test_finer_reference_converges_to_the_new_model(self):
        """More annuli bring the old model closer to the new one: the difference was the old model's."""
        z = z_grid(0.12)
        new = tc.occultation(z, 0.12, 0.4, 0.25)
        d = [np.abs(new - tc.occultation_annulus_reference(z, 0.12, 0.4, 0.25, n_annuli=n)).max()
             for n in (200, 800, 3200)]
        self.assertLess(d[1], d[0] / 4)
        self.assertLess(d[2], d[1] / 4)
        self.assertLess(d[2], 0.01 * PPM)

    def test_reference_default_is_the_old_model(self):
        """The reference with its default is the 200-annulus sum, unchanged (pinned values from before the change)."""
        z = np.array([0.0, 0.3, 0.95, 1.05, 1.2])
        got = tc.occultation_annulus_reference(z, 0.1, 0.4, 0.25)
        np.testing.assert_array_equal(got, tc.occultation_annulus_reference(z, 0.1, 0.4, 0.25, n_annuli=200))
        self.assertEqual(got[-1], 1.0)
        self.assertAlmostEqual(got[0], 0.987891, places=6)


class ExactValues(unittest.TestCase):
    def test_uniform_disk_is_the_circle_overlap_area(self):
        for k in K_TRANSIT + K_LARGE:
            z = z_grid(k)
            d = np.abs(tc.occultation(z, k, 0.0, 0.0) - uniform_exact(z, k))
            # 5e-9: the textbook arccos form used by uniform_exact loses digits within ~1e-12 of a contact point
            self.assertLessEqual(d.max(), 5e-9, (k, z[d.argmax()]))
            away = np.minimum(np.abs(z - abs(1 - k)), np.abs(z - (1 + k))) > 1e-5
            self.assertLessEqual(d[away].max(), 1e-12, (k, z[away][d[away].argmax()]))

    def test_center_of_the_disk_closed_form(self):
        """z = 0, planet smaller than the star: every term has an elementary value."""
        for k in (0.01, 0.1, 0.3, 0.7, 0.999):
            for u1, u2 in LD:
                c1, c0 = u1 + 2 * u2, 1 - u1 - 2 * u2
                blocked = c0 * k * k + c1 * (2.0 / 3.0) * (1 - (1 - k * k) ** 1.5) + u2 * 0.5 * k ** 4
                want = 1 - blocked / (c0 + 2 * c1 / 3 + u2 / 2)
                self.assertAlmostEqual(float(tc.occultation(np.array([0.0]), k, u1, u2)[0]), want, delta=1e-10)

    def test_against_direct_integration(self):
        worst = 0.0
        for k in (0.01, 0.1, 0.3, 0.5, 0.8, 1.0, 1.5, 3.0):
            edge = abs(1 - k)
            for z in (0.0, 1e-5, k / 2, k, k - 1e-10, k + 1e-10, edge, edge + 1e-4, edge / 2, 0.5, 0.9, 1.0,
                      1 + k - 1e-3, 1 + k / 2, max(k - 1, 0) + 1e-3):
                if not 0 <= z < 1 + k:
                    continue
                for u1, u2 in ((0.4, 0.25), (1.0, 0.0), (0.0, 1.0)):
                    got = float(tc.occultation(np.array([z]), k, u1, u2)[0])
                    worst = max(worst, abs(got - integral_exact(z, k, u1, u2)))
        self.assertLess(worst, 1e-9)

    def test_no_planet_no_overlap_and_full_cover(self):
        z = np.array([0.0, 0.5, 1.0, 1.1, 1.1000001, 3.0, 1e3])
        np.testing.assert_array_equal(tc.occultation(z, 0.0, 0.4, 0.25), np.ones(7))
        np.testing.assert_array_equal(tc.occultation(z, -0.1, 0.4, 0.25), np.ones(7))
        np.testing.assert_array_equal(tc.occultation(z, 1e-12, 0.4, 0.25), np.ones(7))
        f = tc.occultation(z, 0.1, 0.4, 0.25)
        np.testing.assert_array_equal(f[3:], np.ones(4))            # z >= 1 + k (1.1 exactly: fourth contact)
        self.assertTrue((f[:3] < 1.0).all())
        big = tc.occultation(np.array([0.0, 0.2, 0.5, 0.5 + 1e-9, 1.0, 2.5, 2.6]), 1.5, 0.4, 0.25)
        np.testing.assert_array_equal(big[:3], np.zeros(3))         # the planet covers the whole star
        self.assertTrue(0.0 <= big[3] < 1e-6)
        self.assertTrue(0.0 < big[4] < 1.0)
        self.assertEqual(big[5], 1.0)
        self.assertEqual(big[6], 1.0)
        self.assertEqual(float(tc.occultation(np.array([0.0]), 1.0, 0.4, 0.25)[0]), 0.0)   # k = 1, centered

    def test_tiny_planet_is_the_local_surface_brightness(self):
        """k -> 0: the dip is k^2 times the intensity under the planet over the disk's mean intensity."""
        k, u1, u2 = 1e-3, 0.4, 0.25
        for z in (0.0, 0.3, 0.6, 0.9):
            mu = math.sqrt(1 - z * z)
            want = k * k * (1 - u1 * (1 - mu) - u2 * (1 - mu) ** 2) / (1 - u1 / 3 - u2 / 6)
            got = 1 - float(tc.occultation(np.array([z]), k, u1, u2)[0])
            self.assertAlmostEqual(got / want, 1.0, places=5)


class ContactPoints(unittest.TestCase):
    def test_continuous_across_every_special_separation(self):
        for k in (0.01, 0.1, 0.3, 0.5, 0.9, 1.0, 1.3, 2.5):
            for u1, u2 in LD:
                for s in (0.0, k, abs(1 - k), 1 + k, max(k - 1, 0.0), 0.5, 1.0):
                    for step in (1e-7, 1e-10, 1e-13):
                        z = np.abs(np.array([s - step, s, s + step]))
                        f = tc.occultation(z, k, u1, u2)
                        # the flux changes by at most ~sqrt(step) across a contact (the limb is steep), far less inside
                        self.assertLess(np.ptp(f), 30 * math.sqrt(step), (k, u1, u2, s, step, f))

    def test_exactly_one_at_and_after_fourth_contact(self):
        for k in (0.01, 0.1, 0.5, 1.0, 2.0):
            z = np.array([1 + k, 1 + k + 1e-12, 2 + k])
            np.testing.assert_array_equal(tc.occultation(z, k, 0.4, 0.25), np.ones(3))
            just_in = float(tc.occultation(np.array([1 + k - 1e-6]), k, 0.4, 0.25)[0])
            self.assertTrue(1 - 1e-8 < just_in <= 1.0, just_in)

    def test_monotonic_from_center_to_fourth_contact(self):
        """With limb darkening that dims outward (and u2 >= 0) the flux never drops as the planet moves outward."""
        for k in K_TRANSIT + [0.3, 0.5, 0.9, 1.0, 1.5]:
            z = np.sort(z_grid(k, 2001))
            for u1, u2 in ((0.0, 0.0), (0.4, 0.25), (0.3, 0.3), (1.0, 0.0), (0.0, 1.0), (0.0, 0.5)):
                f = tc.occultation(z, k, u1, u2)
                self.assertGreaterEqual(np.diff(f).min(), -1e-11, (k, u1, u2))
                self.assertLessEqual(f.max(), 1.0)
                self.assertGreaterEqual(f.min(), 0.0)

    def test_flat_bottom_for_a_uniform_disk(self):
        z = np.linspace(0.0, 0.9, 50)                                 # wholly on the disk: k = 0.1
        np.testing.assert_allclose(tc.occultation(z, 0.1, 0.0, 0.0), 1 - 0.01, rtol=0, atol=1e-12)


class ShapesAndCallers(unittest.TestCase):
    def test_any_shape_and_input_untouched(self):
        z1 = np.abs(np.linspace(-1.3, 1.3, 60))
        want = tc.occultation(z1, 0.1, 0.4, 0.25)
        self.assertEqual(want.shape, (60,))
        self.assertEqual(want.dtype, np.float64)
        np.testing.assert_array_equal(tc.occultation(z1.reshape(6, 10), 0.1, 0.4, 0.25), want.reshape(6, 10))
        np.testing.assert_array_equal(tc.occultation(list(z1), 0.1, 0.4, 0.25), want)
        np.testing.assert_array_equal(tc.occultation(z1.astype(np.float32).astype(float), 0.1, 0.4, 0.25),
                                      tc.occultation(z1.astype(np.float32), 0.1, 0.4, 0.25))
        self.assertEqual(tc.occultation(np.array([]), 0.1, 0.4, 0.25).shape, (0,))
        scalar = tc.occultation(np.float64(0.3), 0.1, 0.4, 0.25)
        self.assertEqual(np.shape(scalar), ())
        self.assertAlmostEqual(float(scalar), float(tc.occultation(np.array([0.3]), 0.1, 0.4, 0.25)[0]), places=15)
        one = tc.occultation(np.array([0.3]), 0.1, 0.4, 0.25)        # the single-point callers index [0]
        self.assertEqual(one.shape, (1,))
        keep = z1.copy()
        tc.occultation(z1, 0.1, 0.4, 0.25)
        np.testing.assert_array_equal(z1, keep)

    def test_each_point_alone_equals_the_array(self):
        """A point's value does not depend on what else is in the array (the number of passes is set by the array)."""
        z = z_grid(0.12, 101)
        whole = tc.occultation(z, 0.12, 0.4, 0.25)
        alone = np.array([tc.occultation(np.array([zi]), 0.12, 0.4, 0.25)[0] for zi in z])
        self.assertLess(np.abs(whole - alone).max(), 1e-11)

    def test_numpy_scalars_and_ints_for_k_and_limb_darkening(self):
        z = np.linspace(0, 1.2, 30)
        want = tc.occultation(z, 0.1, 1.0, 0.0)
        np.testing.assert_array_equal(tc.occultation(z, np.float64(0.1), 1, 0), want)

    def test_behind_the_star_and_bad_values(self):
        z = tc.separation(np.array([0.0, 1.5]), 0.0, 3.0, 8.0, 0.3)   # second point: the planet behind the star
        f = tc.occultation(z, 0.1, 0.4, 0.25)
        self.assertLess(f[0], 1.0)
        self.assertEqual(f[1], 1.0)
        f = tc.occultation(np.array([np.nan, np.inf, 0.2]), 0.1, 0.4, 0.25)
        self.assertEqual(f[0], 1.0)                                   # as the old model: not a hit
        self.assertEqual(f[1], 1.0)
        self.assertLess(f[2], 1.0)
        np.testing.assert_array_equal(tc.occultation(np.array([0.2, 2.0]), float("nan"), 0.4, 0.25), np.ones(2))

    def test_no_numpy_warnings_anywhere(self):
        """No divide-by-zero, overflow or invalid value at any geometry (they would show up as RuntimeWarnings)."""
        with np.errstate(all="raise"):
            for k in K_TRANSIT + K_LARGE + [1e-6, 1e-4, 0.999999, 1.000001, 50.0]:
                for u1, u2 in LD:
                    self.assertTrue(np.isfinite(tc.occultation(z_grid(k, 101), k, u1, u2)).all(), (k, u1, u2))

    def test_negative_separation_counts_as_its_size(self):
        z = np.linspace(-1.2, 1.2, 41)
        np.testing.assert_array_equal(tc.occultation(z, 0.1, 0.4, 0.25), tc.occultation(np.abs(z), 0.1, 0.4, 0.25))

    def test_callers_use_the_new_model_unchanged(self):
        """The model method, the multi-night grid, the comp-survey forecast and the mid-transit dip all go through
        transit_core.occultation; none calls the reference."""
        for name in ("transit_core.py", "transit_ui.py", "transit_posterior.py", "multinight.py", "comp_survey.py",
                     "clearv_app.py"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                src = fh.read()
            uses = src.count("occultation_annulus_reference")
            self.assertEqual(uses, 2 if name == "transit_core.py" else 0, name)   # its definition and one comment
        import comp_survey as cs
        import multinight as mn
        t = np.linspace(2461000.6, 2461000.8, 50)
        planet = {"period": 3.0, "t0": 2461000.7, "k": 0.1, "a_rs": 8.0}
        mags = cs.transit_mags(t, planet, tc.occultation, (0.4, 0.25))["mag"]
        self.assertEqual(np.shape(mags), (50,))
        self.assertTrue(np.isfinite(mags).all())
        self.assertTrue(0.01 < mags.max() < 0.02)
        self.assertFalse(hasattr(mn, "fast_occultation"))   # Multi-night's interpolation grid is gone
        dip = tc.ld_center_depth({"k": 0.1, "b": 0.2, "teff": 5800})
        self.assertTrue(0.01 < dip < 0.014)

    def test_light_curve_of_a_fit_problem_matches_the_reference(self):
        """A whole 300-point model light curve, as the fit evaluates it, old against new: under 1 ppm."""
        t = np.linspace(-0.13, 0.13, 300)
        for k, a_rs, b in ((0.11, 8.5, 0.35), (0.05, 12.0, 0.0), (0.15, 5.0, 0.85), (0.1, 6.0, 1.02)):
            z = tc.separation(t, 0.0012, 3.0, a_rs, b)
            d = np.abs(tc.occultation(z, k, 0.42, 0.26) - tc.occultation_annulus_reference(z, k, 0.42, 0.26))
            self.assertLess(d.max(), 1 * PPM, (k, a_rs, b))


class MultiNightExact(unittest.TestCase):
    """2.4.0 fit-in F3: the joint fit evaluates the exact model (no 126-point interpolation grid)."""
    PLANET = dict(name="TOI-2046 b", host="TOI-2046", period=1.4971842, t0=2460000.5, t0_err=0.0005,
                  period_err=1e-6, k=0.12, a_rs=5.0, b=0.3, teff=6000, t14=0.08)

    @classmethod
    def setUpClass(cls):
        import multinight as mn
        cls.mn = mn
        nights = []
        for i, (nn, flt, npts) in enumerate(((100, "CV", 90), (102, "SR", 70), (105, "CV", 110))):
            rng = np.random.default_rng(300 + i)
            tcn = cls.PLANET["t0"] + nn * cls.PLANET["period"]
            t = np.linspace(tcn - 0.11, tcn + 0.12, npts)
            am = 1.05 + 0.8 * ((t - t[0]) / (t[-1] - t[0]) - 0.3) ** 2
            f = tc.occultation(tc.separation(t, tcn, cls.PLANET["period"], 5.0, 0.3), 0.12, 0.4, 0.25)
            f = f * (1 + 0.01 * (am - am.mean())) + rng.normal(0, 0.002, npts)
            nights.append(mn._finish_night({"kind": "csv", "source": f"n{i}", "planet": "TOI-2046 b", "filter": flt,
                                            "obsdate": f"0{i + 1}-OCT-2026", "t": t, "flux": f,
                                            "err": np.full(npts, 0.002), "airmass": am, "t_flip": None,
                                            "single_tc": None, "notes": []}))
        mn.check_nights(nights, "TOI-2046 b")
        cls.fits = {pf: mn.joint_fit(nights, dict(cls.PLANET, ld_band="CV"), per_filter=pf, mcmc_steps=60)
                    for pf in (False, True)}

    def _direct(self, prob, x, j, t=None):
        d = prob.nights[j]
        ik, i1, i2 = prob._ig[d["group"]]
        root_q1 = math.sqrt(x[i1])                       # the fit's q1, q2 -> u1, u2
        u1, u2 = 2.0 * root_q1 * x[i2], root_q1 * (1.0 - 2.0 * x[i2])
        tt = d["t"] if t is None else t
        return tc.occultation(tc.separation(tt, x[prob._in[j][0]], prob.period, x[0], x[1]), x[ik], u1, u2)

    def test_every_night_is_the_exact_model(self):
        """One shape for all nights, and one per filter (two groups, nights of unequal length): each night's model
        in the likelihood is transit_core.occultation at that night's own times, to the last bit."""
        for pf, fit in self.fits.items():
            prob = fit["problem"]
            self.assertEqual(len({d["group"] for d in prob.nights}), 2 if pf else 1)
            rng = np.random.default_rng(5)
            flat = fit["chain"]["flat"]
            for x in [np.asarray(flat[i], dtype=float) for i in rng.integers(0, len(flat), 6)]:
                shape = prob._shape(x)
                for j, d in enumerate(prob.nights):
                    want = self._direct(prob, x, j)
                    got = prob.night_model(x, j, shape=shape, transit_only=True)
                    self.assertEqual(got.shape, d["t"].shape)
                    np.testing.assert_array_equal(got, want)
                    np.testing.assert_array_equal(prob.night_model(x, j, transit_only=True, exact=True), want)
                    np.testing.assert_array_equal(prob.night_model(x, j), want * prob.baseline(x, j))
                    other = d["t"][::3] + 1e-4
                    np.testing.assert_array_equal(prob.night_model(x, j, t=other, transit_only=True),
                                                  self._direct(prob, x, j, other))

    def test_likelihood_is_built_on_the_exact_model(self):
        for fit in self.fits.values():
            prob = fit["problem"]
            x = np.asarray(fit["chain"]["flat"][0], dtype=float)
            chi2 = 0.0
            for j, d in enumerate(prob.nights):
                r = (d["flux"] - self._direct(prob, x, j) * prob.baseline(x, j)) / d["err"]
                chi2 += float(np.dot(r, r))
                self.assertAlmostEqual(prob.night_chi2(x)[j], float(np.dot(r, r)), places=9)
            pr = prob._prior_resid(x)
            self.assertAlmostEqual(prob.log_prob(x), -0.5 * (chi2 + float(np.dot(pr, pr))), places=9)
            self.assertEqual(len(prob.residual_vector(x)), sum(len(d["t"]) for d in prob.nights) + len(pr))

    def test_saved_night_models_are_exact_and_the_grid_is_gone(self):
        fit = self.fits[False]
        self.assertEqual(len(fit["nights"]), 3)
        self.assertTrue(abs(fit["summary"]["k:all"][0] - 0.12) < 0.01)
        with open(os.path.join(ROOT, "multinight.py"), encoding="utf-8") as fh:
            src = fh.read()
        for gone in ("occ_curve", "fast_occultation", "_S_IN", "_S_EDGE", "np.interp(z, grid"):
            self.assertNotIn(gone, src)


class Speed(unittest.TestCase):
    @staticmethod
    def _best(fn, z, loops=40, repeats=9):
        best = float("inf")
        for _ in range(repeats):
            t0 = time.perf_counter()
            for _ in range(loops):
                fn(z, 0.1, 0.4, 0.25)
            best = min(best, (time.perf_counter() - t0) / loops)
        return best

    def test_much_faster_than_the_annulus_sum_on_a_300_point_curve(self):
        """300 points, all on the star (the old model's ~0.6 ms case). Typical: 5-9x; asserted loosely (>= 3x) so a
        busy machine or parallel test workers do not fail it. Best of several repeats of both, interleaved."""
        z = np.abs(np.linspace(-1.09, 1.09, 300))
        ratios = []
        for _ in range(3):
            old = self._best(tc.occultation_annulus_reference, z)
            new = self._best(tc.occultation, z)
            ratios.append(old / new)
        self.assertGreaterEqual(max(ratios), 3.0, ratios)

    def test_faster_on_a_real_night_with_baseline_frames(self):
        """300 frames of which about half are in transit: still clearly faster (typical 3-4.5x; asserted >= 1.8x)."""
        z = tc.separation(np.linspace(-0.12, 0.12, 300), 0.0, 3.0, 8.0, 0.3)
        ratios = []
        for _ in range(3):
            old = self._best(tc.occultation_annulus_reference, z)
            new = self._best(tc.occultation, z)
            ratios.append(old / new)
        self.assertGreaterEqual(max(ratios), 1.8, ratios)


if __name__ == "__main__":
    unittest.main()
