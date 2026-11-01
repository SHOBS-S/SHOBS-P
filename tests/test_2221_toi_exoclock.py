"""2.2.21 (#32, item 14): TESS TOI status and ExoClock priority at planet lookup (target_status.py). No network."""
import datetime as dt
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lookup_cache
import target_status as ts
import transit_core as tc


class CacheDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("SHOBS_P_LOOKUP_CACHE_DIR", "SHOBS_P_NO_LOOKUP_CACHE")}
        os.environ["SHOBS_P_LOOKUP_CACHE_DIR"] = self._tmp
        os.environ.pop("SHOBS_P_NO_LOOKUP_CACHE", None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmp, ignore_errors=True)


def _row(toi, disp, tid="468574941", ra="16.184942", dec="74.331306", per=""):
    return {"toi": toi, "tid": tid, "tfopwg_disp": disp, "toipfx": toi.split(".")[0], "ra": ra, "dec": dec,
            "pl_orbper": per}


class TestDispositionText(CacheDir):
    def test_candidates_point_to_exofop(self):
        self.assertEqual(ts.disposition_text("1234.01", "PC"), "TOI-1234.01 · PC (planet candidate) — consider "
                         "uploading to ExoFOP-TESS (coordinate with TFOP SG1)")
        self.assertIn("APC (ambiguous planet candidate) — consider uploading to ExoFOP-TESS",
                      ts.disposition_text("121.01", "apc"))

    def test_confirmed_and_known_point_to_aavso(self):
        self.assertEqual(ts.disposition_text("2046.01", "CP"),
                         "TOI-2046.01 · CP (confirmed) — AAVSO / Exoplanet Watch is the right home")
        self.assertIn("KP (known planet) — AAVSO / Exoplanet Watch", ts.disposition_text("1725.01", "KP"))

    def test_false_positives_and_none_show_nothing(self):
        for disp in ("FP", "FA", "", None, "XX"):
            self.assertEqual(ts.disposition_text("1.01", disp), "")
        self.assertEqual(ts.status_from_row(None)["text"], "")

    def test_link_to_exofop_page(self):
        st = ts.status_from_row(_row("1234.01", "PC"))
        self.assertEqual(st["url"], "https://exofop.ipac.caltech.edu/tess/target.php?toi=1234")
        self.assertEqual(ts.status_from_row(_row("1.01", "FP"))["url"], "")

    def test_failure_is_unknown_never_not_a_toi(self):
        def down(_q):
            raise TimeoutError("timed out")
        st = ts.toi_status({"name": "TOI-9999 b", "tic": "TIC 1"}, query=down)
        self.assertEqual(st["state"], "unknown")
        self.assertEqual(st["text"], "TOI status unknown (Archive did not answer)")
        self.assertNotIn("not a TOI", st["text"])


class TestTOIMatch(CacheDir):
    def test_by_tic(self):
        asked = []

        def q(adql):
            asked.append(adql)
            return [_row("2046.01", "CP")]
        st = ts.toi_status({"name": "TOI-2046 b", "tic": "TIC 468574941"}, query=q)
        self.assertIn("where tid = 468574941", asked[0])
        self.assertTrue(st["text"].startswith("TOI-2046.01 · CP"))

    def test_by_toi_number_in_aliases(self):
        asked = []

        def q(adql):
            asked.append(adql)
            return [_row("1725.01", "KP", tid="86396382")]
        st = ts.toi_status({"name": "WASP-12 b", "aliases": ["TOI-1725 b", "WASP-12 A b"]}, query=q)
        self.assertIn("toipfx = '1725'", asked[0])
        self.assertTrue(st["text"].startswith("TOI-1725.01 · KP"))

    def test_by_coordinates_within_10_arcsec(self):
        asked = []

        def q(adql):
            asked.append(adql)
            return [_row("1725.01", "KP", tid="86396382", ra="97.636645", dec="29.672266"),
                    _row("9.01", "PC", tid="5", ra="97.6466", dec="29.672266")]   # ~31" away: not this star
        st = ts.toi_status({"name": "WASP-12 b", "ra": 97.6366, "dec": 29.6723}, query=q)
        self.assertIn("ra between", asked[0])
        self.assertNotIn("contains(", asked[0])   # the Archive's firewall refuses contains()
        self.assertEqual(st["toi"], "1725.01")
        far = ts.toi_status({"name": "Elsewhere b", "ra": 97.66, "dec": 29.70},
                            query=lambda _q: [_row("1725.01", "KP", ra="97.636645", dec="29.672266")])
        self.assertEqual(far["state"], "none")
        self.assertEqual(far["text"], "")

    def test_not_a_toi_shows_nothing(self):
        st = ts.toi_status({"name": "HAT-P-1 b", "ra": 344.4, "dec": 38.67}, query=lambda _q: [])
        self.assertEqual((st["state"], st["text"]), ("none", ""))

    def test_nothing_to_ask(self):
        st = ts.toi_status({"name": "Foo b"}, query=lambda _q: self.fail("no query without TIC, TOI or position"))
        self.assertEqual(st["text"], "")

    def test_several_tois_of_one_star(self):
        rows = [_row("175.01", "CP", per="3.6906759"), _row("175.02", "CP", per="7.4507305"),
                _row("175.03", "PC", per="2.2531062")]
        self.assertEqual(ts.pick_toi_row(rows, {"name": "L 98-59 d", "period": 7.45})["toi"], "175.02")
        self.assertEqual(ts.pick_toi_row(rows, {"name": "TOI-175 c"})["toi"], "175.01")   # 2.2.21 review: no letter rule
        self.assertEqual(ts.pick_toi_row(rows, {"name": "x", "typed": "TOI-175.03"})["toi"], "175.03")

    def test_cache_fallback_with_date(self):
        ts.toi_status({"name": "TOI-1234 b", "tic": "TIC 42"}, query=lambda _q: [_row("1234.01", "PC", tid="42")])

        def down(_q):
            raise OSError("no connection")
        st = ts.toi_status({"name": "TOI-1234 b", "tic": "TIC 42"}, query=down)
        today = lookup_cache.fetched_text(dt.datetime.now(dt.timezone.utc))
        self.assertTrue(st["text"].startswith("TOI-1234.01 · PC"))
        self.assertTrue(st["text"].endswith(f"(from cache, fetched {today})"))

    def test_html_error_page_counts_as_failure(self):
        st = ts.toi_status({"name": "TOI-77 b", "tic": "TIC 77"}, query=lambda _q: [{"<!DOCTYPE html>": "x"}])
        self.assertEqual(st["state"], "unknown")


FEED = {
    "WASP-12b": {"name": "WASP-12b", "priority": "low", "total_observations": 539, "total_observations_recent": 31,
                 "min_telescope_inches": 5.0},
    "TOI-1259Ab": {"name": "TOI-1259Ab", "priority": "high", "total_observations_recent": 2,
                   "min_telescope_inches": 5.43},
    "HATS-50b": {"name": "HATS-50b", "priority": "alert TTVs", "total_observations_recent": 1,
                 "min_telescope_inches": 12.0},
}


class TestExoClock(CacheDir):
    def test_name_matching(self):
        for name in ("WASP-12 b", "WASP-12b", "wasp 12 b"):
            self.assertEqual(ts.exoclock_entry(FEED, name)["name"], "WASP-12b")
        self.assertEqual(ts.exoclock_entry(FEED, "TOI-1259 A b")["name"], "TOI-1259Ab")
        self.assertIsNone(ts.exoclock_entry(FEED, "WASP-121 b"))
        # an alias matches too
        st = ts.exoclock_status({"name": "Foo b", "aliases": ["WASP-12 b"]}, download=lambda: FEED)
        self.assertTrue(st["on_list"])
        self.assertEqual(st["url"], "https://www.exoclock.space/database/planets/WASP-12b/")

    def test_texts(self):
        self.assertEqual(ts.exoclock_text(FEED["TOI-1259Ab"], True), "ExoClock: HIGH priority (2 recent observations)")
        self.assertEqual(ts.exoclock_text(FEED["HATS-50b"], True), "ExoClock: ALERT priority (TTVs) (1 recent observation)")
        self.assertEqual(ts.exoclock_text(None, True), "Not on ExoClock's list")
        self.assertEqual(ts.exoclock_text(None, False), "")   # never "not on the list" when the feed was not read
        # aperture below the smallest telescope ExoClock asks for (356 mm = 14"; 100 mm = 3.9")
        self.assertEqual(ts.exoclock_text(FEED["WASP-12b"], True, aperture_mm=100),
                         "ExoClock: LOW priority (31 recent observations) (needs ≥ 5″ telescope)")
        self.assertNotIn("needs", ts.exoclock_text(FEED["WASP-12b"], True, aperture_mm=356))

    def test_feed_downloaded_once_a_day(self):
        calls = []

        def dl():
            calls.append(1)
            return FEED
        ts.exoclock_feed(dl)
        feed, note = ts.exoclock_feed(dl)
        self.assertEqual(len(calls), 1)
        self.assertEqual((feed["WASP-12b"]["name"], note), ("WASP-12b", ""))
        tomorrow = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
        ts.exoclock_feed(dl, now=tomorrow)
        self.assertEqual(len(calls), 2)

    def test_feed_cache_fallback(self):
        lookup_cache.put("exoclock", "planets_json", FEED)

        def down():
            raise OSError("no connection")
        tomorrow = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
        feed, note = ts.exoclock_feed(down, now=tomorrow)
        today = lookup_cache.fetched_text(dt.datetime.now(dt.timezone.utc))
        self.assertIn("WASP-12b", feed)
        self.assertEqual(note, f"(from cache, fetched {today})")
        lookup_cache.clear()
        self.assertEqual(ts.exoclock_feed(down), (None, ""))
        st = ts.exoclock_status({"name": "WASP-12 b"}, download=down)
        self.assertEqual(st["text"], "")


class TestFitText(unittest.TestCase):
    def test_summary_lines(self):
        """The fit summary (ExoFOP package's _fit.txt) carries the TOI status with its ExoFOP link, and ExoClock."""
        import numpy as np
        planet = dict(name="TOI-2046 b", host="TOI-2046", period=1.4971842, t0=2460000.5, t0_err=0.0005,
                      period_err=1e-6, k=0.12, a_rs=5.0, b=0.3, teff=6000, t14=0.08)
        rng = np.random.default_rng(4)
        t = np.linspace(planet["t0"] - 0.12, planet["t0"] + 0.12, 150)
        f = tc.occultation(tc.separation(t, planet["t0"], planet["period"], 5.0, 0.3), 0.12, 0.4, 0.25)
        f = f + rng.normal(0, 0.002, len(t))
        r = tc.fit_transit(t, f, np.full(len(t), 0.002), np.ones(len(t)), planet, mcmc_steps=150)
        meta = {"toi_status": ts.disposition_text("2046.01", "CP"),
                "toi_url": "https://exofop.ipac.caltech.edu/tess/target.php?toi=2046",
                "exoclock_status": "ExoClock: LOW priority (24 recent observations)"}
        text = tc.fit_text(r, meta)
        self.assertIn("TESS TOI       TOI-2046.01 CP (confirmed) — AAVSO / Exoplanet Watch is the right home  "
                      "https://exofop.ipac.caltech.edu/tess/target.php?toi=2046", text)
        self.assertIn("ExoClock       LOW priority (24 recent observations)", text)
        self.assertNotIn("TESS TOI", tc.fit_text(r, {}))


if __name__ == "__main__":
    unittest.main()
