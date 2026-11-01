"""2.2.21 (#21, #48): cached answers when a service is down; VSX through the AAVSO API; "VSX not reached"."""
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from unittest import mock

# 2.2.21 (#21): no test (in any module: pytest imports this one while collecting) writes the user's real lookup cache.
os.environ.setdefault("SHOBS_P_LOOKUP_CACHE_DIR", tempfile.mkdtemp(prefix="shobs_p_lookups_"))

import lookup_cache
import photometry_core as core
import transit_core as tc


class CacheDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"SHOBS_P_LOOKUP_CACHE_DIR": self.tmp, "SHOBS_P_NO_LOOKUP_CACHE": ""})
        self.env.start()
        core._SIMBAD_DOWN.clear()
        core._VIZIER_DOWN.clear()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        core._SIMBAD_DOWN.clear()
        core._VIZIER_DOWN.clear()


def http_503(url, *a, **k):
    raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)


SIMBAD_TEXT = "Coordinates(ICRS,ep=J2000,eq=2000): 17 52 07.0184 +37 32 46.237 (Opt ) A\n"


class TestLookupCache(CacheDir):
    def test_put_get_and_note(self):
        lookup_cache.put("vsp", "q1", {"chartid": "X123"})
        value, when = lookup_cache.get("vsp", "q1")
        self.assertEqual(value, {"chartid": "X123"})
        note = lookup_cache.from_cache_note("vsp", when, "503")
        self.assertTrue(note.startswith("AAVSO sequence from cache (fetched "))
        self.assertTrue(note.endswith("the live service did not answer (503)."))
        self.assertEqual(lookup_cache.get("vsp", "nope"), (None, None))

    def test_error_text(self):
        self.assertEqual(core.service_error_text(urllib.error.HTTPError("u", 503, "x", {}, None)), "503")
        self.assertEqual(core.service_error_text(TimeoutError("timed out")), "timed out")
        self.assertEqual(core.service_error_text(urllib.error.URLError("refused")), "no connection")


class TestLookupTargetFallback(CacheDir):
    def test_simbad_and_vsx_from_cache(self):
        vsx_payload = {"VSXObject": {"Name": "TrES-3", "RA2000": "268.02924", "Declination2000": "37.54618",
                                     "Period": "1.30618", "VariabilityType": "EP", "AUID": "000-BJP-123"}}

        def live(url, timeout=45):
            if "simbad" in url:
                return SIMBAD_TEXT.encode()
            return json.dumps(vsx_payload).encode()
        with mock.patch.object(core, "_http_get", live):
            first = core.lookup_target("TrES-3")
        self.assertNotIn("cache_notes", first)
        with mock.patch.object(core, "_http_get", http_503):
            again = core.lookup_target("tres 3")   # another spelling: the same key
        self.assertAlmostEqual(again["ra_hours"], first["ra_hours"], places=6)
        self.assertEqual(again["auid"], "000-BJP-123")
        self.assertTrue(again["in_vsx"])
        notes = again["cache_notes"]
        self.assertTrue(any(n.startswith("SIMBAD from cache (fetched ") and "(503)" in n for n in notes), notes)
        self.assertTrue(any(n.startswith("VSX from cache") for n in notes), notes)

    def test_no_cache_still_fails(self):
        with mock.patch.object(core, "_http_get", http_503):
            with self.assertRaises(ValueError):
                core.lookup_target("Nothing-Cached-1")

    def test_live_wins(self):
        lookup_cache.put("simbad", lookup_cache.norm_key("TrES-3"),
                         "Coordinates(ICRS,ep=J2000,eq=2000): 01 00 00.0 +10 00 00.0 (Opt ) A\n")
        with mock.patch.object(core, "_http_get", lambda url, timeout=45: SIMBAD_TEXT.encode()):
            got = core._lookup_simbad("TrES-3")
        self.assertAlmostEqual(got["ra_hours"], 17.8686, places=3)
        self.assertNotIn("cache_note", got)


class TestVsxCone(CacheDir):
    API = {"VSXObjects": {"VSXObject": [
        {"Name": "V0492 Peg", "AUID": "000-BJQ-063", "RA2000": "330.65708", "Declination2000": "18.90072",
         "VariabilityType": "DSCT+GDOR", "Period": "0.56088", "MaxMag": "10.76 V", "MinMag": "10.80 V"},
        {"Name": "Gaia DR3 1779542634500476672", "RA2000": "330.59436", "Declination2000": "18.82990",
         "VariabilityType": "ROT", "MaxMag": "13.27 G", "MinMag": "13.29 G"},
        {"Name": "broken", "RA2000": "", "Declination2000": ""}]}}

    def test_parse_api(self):
        rows = core.parse_vsx_api_list(self.API)
        self.assertEqual([r["name"] for r in rows], ["V0492 Peg", "Gaia DR3 1779542634500476672"])
        self.assertAlmostEqual(rows[0]["period"], 0.56088)
        self.assertIsNone(rows[1]["period"])
        self.assertAlmostEqual(rows[0]["ra"], 330.65708)
        self.assertEqual(core.parse_vsx_api_list({"VSXObjects": []}), [])
        one = core.parse_vsx_api_list({"VSXObjects": {"VSXObject": self.API["VSXObjects"]["VSXObject"][0]}})
        self.assertEqual(len(one), 1)
        with self.assertRaises(ValueError):
            core.parse_vsx_api_list("<html>Just a moment...</html>")

    def test_idia_skipped_for_vsx(self):
        hosts = core._vizier_hosts("B/vsx/vsx")
        self.assertNotIn("https://vizier.idia.ac.za", hosts)
        self.assertIn("https://vizier.idia.ac.za", core._vizier_hosts("I/259/tyc2"))

    def test_vizier_failed_host_asked_last(self):
        asked = []

        def fake(url, timeout=45):
            host = url.split("/viz-bin")[0]
            asked.append(host)
            if "cds.unistra" in host:
                raise TimeoutError("timed out")
            return b"#\nName\tRAJ2000\n----\t----\n"
        with mock.patch.object(core, "_http_get", fake):
            core._vizier_query("I/259/tyc2", 10.0, 20.0, 0.1, ["Name"], "Name", 10)
            self.assertEqual(asked[:2], ["https://vizier.cds.unistra.fr", "https://vizier.cfa.harvard.edu"])
            asked.clear()
            core._vizier_query("I/259/tyc2", 10.0, 20.0, 0.1, ["Name"], "Name", 10)
            self.assertEqual(asked[0], "https://vizier.cfa.harvard.edu")

    def test_api_fallback_then_cache(self):
        def vizier_down(url, timeout=45):
            if "viz-bin" in url:
                raise TimeoutError("timed out")
            if "vsx.aavso.org" in url:
                self.assertIn("view=api.list", url)
                return json.dumps(self.API).encode()
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        with mock.patch.object(core, "_http_get", vizier_down):
            rows = core.fetch_vsx_cone(330.79, 18.88, 0.3)
            self.assertEqual(getattr(rows, "via", ""), "VSX API")
            self.assertEqual(len(rows), 2)
            found, sources = core.field_variables(330.79, 18.88, 0.3)
            self.assertIn("VSX", sources)
            self.assertTrue(any("VSX API" in n for n in sources.notes))
        # now everything is down: the cached list
        with mock.patch.object(core, "_http_get", http_503):
            rows = core.fetch_vsx_cone(330.79, 18.88, 0.3)
            self.assertEqual(len(rows), 2)
            self.assertTrue(rows.cache_note.startswith("VSX from cache (fetched "))

    def test_vsx_not_reached(self):
        def only_simbad(url, timeout=45):
            if "sim-tap" in url:
                return b"main_id,ra,dec,otype\nV* XX Peg,330.6,18.9,RR*\n"
            raise TimeoutError("timed out")
        with mock.patch.object(core, "_http_get", only_simbad):
            with self.assertRaises(ConnectionError) as ctx:
                core.fetch_vsx_cone(1.0, 2.0, 0.3)
            self.assertIn("VSX not reached", str(ctx.exception))
            found, sources = core.field_variables(1.0, 2.0, 0.3)
        self.assertEqual(list(sources), ["SIMBAD"])
        self.assertIn("VSX not reached", sources.vsx_error)
        self.assertEqual(core.check_sources_text(sources), "SIMBAD; VSX not reached")
        self.assertEqual(core.check_sources_text(["VSX", "SIMBAD"]), "VSX + SIMBAD")
        self.assertEqual(core.not_in_sources_text(["SIMBAD"]), "VSX not reached")
        self.assertEqual(core.not_in_sources_text(["VSX", "SIMBAD"]), "not in VSX or SIMBAD")
        res = {"vsx": [None], "vsx_sources": ["SIMBAD"]}
        self.assertEqual(core.scan_candidate_kind(res, 0), ("new", "VSX not reached"))


class TestVspCache(CacheDir):
    def test_sequence_from_cache_keeps_chart_id(self):
        payload = {"chartid": "X28123AB", "photometry": [
            {"auid": "000-AAA-001", "label": "110", "ra": "10:00:00", "dec": "+20:00:00",
             "bands": [{"band": "V", "mag": 11.0, "error": 0.02}]}]}
        with mock.patch.object(core, "_http_get", lambda url, timeout=45: json.dumps(payload).encode()):
            stars, chart = core.fetch_comparison_stars("AAVSO sequence", 10.0, 20.0, 30.0, "V")
        self.assertEqual(chart, "X28123AB")
        self.assertEqual(getattr(stars, "cache_note", ""), "")
        with mock.patch.object(core, "_http_get", http_503):
            stars2, chart2 = core.fetch_comparison_stars("AAVSO sequence", 10.0, 20.0, 30.0, "V")
            self.assertEqual(chart2, "X28123AB")
            self.assertEqual(len(stars2), len(stars))
            self.assertTrue(stars2.cache_note.startswith("AAVSO sequence from cache (fetched "))
            self.assertIn("(503)", stars2.cache_note)
            # background retries never take the cached answer
            with self.assertRaises(core.VSPError):
                core.fetch_aavso_chart(10.0, 20.0, 30.0, 16, use_cache=False)


class TestPlanetCache(CacheDir):
    PLANET = {"name": "TrES-3 b", "host": "TrES-3", "period": 1.30618608, "t0": 2454185.9104, "period_err": 1e-7,
              "t0_err": 2e-4, "source": "NASA Exoplanet Archive", "reference": "ephemeris: X 2020", "typed": "TrES-3 b"}

    def test_falls_back_and_says_so(self):
        with mock.patch.object(tc, "lookup_nea", lambda n: dict(self.PLANET)):
            tc.lookup_planet("TrES-3 b")

        def down(n):
            raise ConnectionError("timed out")
        with mock.patch.object(tc, "lookup_nea", down):
            got = tc.lookup_planet("tres3b")
        self.assertAlmostEqual(got["period"], 1.30618608)
        self.assertTrue(got["cache_note"].startswith("NASA Exoplanet Archive from cache (fetched "))
        self.assertIn("from cache (fetched ", tc.ephemeris_source(got))

    def test_unknown_planet_not_from_cache(self):
        lookup_cache.put("nea", lookup_cache.norm_key("TrES-3 b"), dict(self.PLANET))

        def unknown(n):
            raise ValueError("the NASA Exoplanet Archive has no planet called TrES-3 b")
        with mock.patch.object(tc, "lookup_nea", unknown):
            with self.assertRaises(ValueError):
                tc.lookup_planet("TrES-3 b")

    def test_planet_choice_still_raised(self):
        lookup_cache.put("nea", lookup_cache.norm_key("HD 219134"), dict(self.PLANET))

        def choice(n):
            raise tc.PlanetChoice("HD 219134", ["HD 219134 b", "HD 219134 c"])
        with mock.patch.object(tc, "lookup_nea", choice):
            with self.assertRaises(tc.PlanetChoice):
                tc.lookup_planet("HD 219134")


class TestClearCachedCatalogs(CacheDir):
    def test_clear_both(self):
        import clearv_app
        cat_dir = os.path.join(self.tmp, "catalogs")
        os.makedirs(cat_dir)
        for name in ("Gaia_a.json", "Tycho_b.json"):
            open(os.path.join(cat_dir, name), "w").close()
        lookup_cache.put("simbad", "x", "y")
        lookup_cache.put("vsp", "q", {"chartid": "1"})
        fake = mock.Mock()
        fake.status = mock.Mock()
        with mock.patch.object(core, "CATALOG_CACHE_DIR", cat_dir):
            n = clearv_app.App.clear_cached_catalogs(fake, ask=False)
        self.assertEqual(n, 4)
        self.assertEqual(os.listdir(cat_dir), [])
        self.assertEqual(lookup_cache.get("simbad", "x"), (None, None))
        self.assertIn("4 files removed", fake.log.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
