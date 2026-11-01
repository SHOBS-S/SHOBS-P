"""2.2.21 review fixes (Fable): slim ExoClock cache, no letter rule for TOI rows, IDIA asked for VSX."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lookup_cache  # noqa: E402
import photometry_core as core  # noqa: E402
import target_status as ts  # noqa: E402


class Review2221(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["SHOBS_P_LOOKUP_CACHE_DIR"] = self.tmp
        os.environ.pop("SHOBS_P_NO_LOOKUP_CACHE", None)

    def tearDown(self):
        os.environ.pop("SHOBS_P_LOOKUP_CACHE_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_exoclock_feed_cached_slim(self):
        feed = {"WASP-12b": {"name": "WASP-12b", "priority": "low", "total_observations_recent": 31,
                             "min_telescope_inches": 4.0, "t0_bjd_tdb": 2457010.5, "big": "x" * 5000}}
        got, note = ts.exoclock_feed(download=lambda: feed)
        cached, _ = lookup_cache.get("exoclock", "planets_json")
        self.assertNotIn("big", cached["WASP-12b"])
        self.assertNotIn("t0_bjd_tdb", cached["WASP-12b"])
        self.assertEqual(ts.exoclock_entry(got, "WASP-12 b")["priority"], "low")

    def test_toi_rows_no_letter_rule(self):
        rows = [{"toi": "175.01", "pl_orbper": 3.69}, {"toi": "175.02", "pl_orbper": 7.45},
                {"toi": "175.03", "pl_orbper": 2.25}]
        # no period: first row, not ".01 = b"
        self.assertEqual(ts.pick_toi_row(rows, {"name": "L 98-59 d"})["toi"], "175.01")
        self.assertEqual(ts.pick_toi_row(rows, {"name": "L 98-59 b", "period": 2.2531})["toi"], "175.03")

    def test_idia_carries_vsx(self):
        self.assertIn("https://vizier.idia.ac.za", core._vizier_hosts("B/vsx/vsx"))

    def test_clear_takes_the_lock(self):
        lookup_cache.put("simbad", "szlyn", {"ra": 1})
        self.assertEqual(lookup_cache.clear(), 1)
        self.assertEqual(lookup_cache.get("simbad", "szlyn"), (None, None))


if __name__ == "__main__":
    unittest.main()
