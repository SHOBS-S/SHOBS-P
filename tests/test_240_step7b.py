"""2.4.0 step 7b (#115): the suspects file name from the field folder, and the normalise-then-cap excess chi2nu.
(The promotion gates, Registry and Campaign switch this file also tested were removed in step 7d.)"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photometry_core as core  # noqa: E402


class TestSuspectsName(unittest.TestCase):
    def test_field_folder(self):
        self.assertEqual(core.suspects_field_name("W:/Variables/GSC 03308-00462/13Nov26/Output", "x"),
                         "GSC 03308-00462")
        self.assertEqual(core.suspects_field_name("", "HD 1"), "HD 1")
        self.assertEqual(core.suspects_field_name("", ""), "field")


class TestNormaliseThenCap(unittest.TestCase):
    def test_bright_variable_no_longer_compressed(self):
        """#115: errors that read far low on the bright stars (no floor) no longer compress a bright variable."""
        rng = np.random.default_rng(3)
        n_f, n_s = 90, 300
        mags = np.linspace(8, 16, n_s)
        err = 0.002 * 10 ** (0.2 * (mags - 8))
        floor = np.where(mags < 10.5, 0.008, 0.0)          # unmodelled floor on the bright stars
        true = np.sqrt(err ** 2 + floor ** 2)
        inst = mags[None, :] + rng.normal(0, 1, (n_f, n_s)) * true[None, :]
        j = 10                                              # a bright star with a small sine
        inst[:, j] += 2.0 * true[j] * np.sin(np.arange(n_f) / 6.0)
        merr = np.repeat(err[None, :], n_f, axis=0)
        st = core.field_variability(inst, merr=merr)
        self.assertGreaterEqual(st["excess_chi2"][j], core.SUSPECT_EXCESS_CHI2)
        quiet = np.delete(np.arange(n_s), j)
        self.assertLess(np.nanmedian(st["excess_chi2"][quiet]), 1.3)

    def test_widening_never_narrows(self):
        corr = np.zeros((10, 6))
        w = core.chi2_widening(corr, np.zeros(6), np.ones((10, 6)), np.ones(6, bool))
        self.assertTrue(np.all(w >= 1.0))


if __name__ == "__main__":
    unittest.main()
