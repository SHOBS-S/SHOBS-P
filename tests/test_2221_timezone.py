"""2.2.21 (#22): the observing site's time zone for every local time (not the computer's)."""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

import site_time
import transit_core as tc


def jd_of(y, mo, d, h, mi=0, s=0.0):
    return dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp() / 86400.0 + 2440587.5 + s / 86400.0


class _Zone(unittest.TestCase):
    """Each test starts from 'no site zone' and a Warsaw computer, and leaves the module as it found it."""
    computer = "Europe/Warsaw"

    def setUp(self):
        self._old = site_time._NAME
        p = mock.patch.object(site_time, "computer_zone_name", return_value=self.computer)
        p.start()
        self.addCleanup(p.stop)
        site_time.set_zone("")

    def tearDown(self):
        site_time.set_zone(self._old)


class TestLabels(_Zone):
    def test_dst_change_new_york_1_nov_2026(self):
        site_time.set_zone("America/New_York")
        before = jd_of(2026, 10, 31, 23)   # 19:00 EDT
        after = jd_of(2026, 11, 2, 0)      # 19:00 EST
        self.assertEqual(site_time.label(before), "Local time (UTC−4)")
        self.assertEqual(site_time.label(after), "Local time (UTC−5)")
        self.assertEqual(site_time.jd_to_local(before).hour, 19)
        self.assertEqual(site_time.jd_to_local(after).hour, 19)
        self.assertEqual(site_time.label_span([before, after]), "Local time (UTC−4 / UTC−5)")
        self.assertEqual(site_time.offset_at(after, ascii_only=True), "UTC-5")
        self.assertEqual(site_time.tag(after), "local time (UTC−5)")

    def test_half_hour_and_positive_offsets(self):
        site_time.set_zone("Asia/Kolkata")
        self.assertEqual(site_time.label(jd_of(2026, 6, 1, 0)), "Local time (UTC+5:30)")
        site_time.set_zone("Etc/UTC")
        self.assertEqual(site_time.label(jd_of(2026, 6, 1, 0)), "Local time (UTC+0)")

    def test_phoenix_site_seen_from_warsaw(self):
        """The beta tester's case: Arizona frames on a Polish computer show Arizona time (UTC−7), not 08:34."""
        jd = jd_of(2026, 10, 2, 6, 34, 1.0)   # 23:34 MST on 1 Oct (a second in: a JD float is not exact)
        site_time.set_zone("")
        self.assertEqual(tc.jd_to_local(jd).strftime("%H:%M"), "08:34")   # the computer's (Warsaw, CEST)
        site_time.set_zone("America/Phoenix")
        self.assertEqual(tc.jd_to_local(jd).strftime("%H:%M"), "23:34")
        self.assertEqual(site_time.label(jd), "Local time (UTC−7)")

    def test_bad_zone_falls_back_once(self):
        msg = site_time.set_zone("Mars/Olympus_Mons")
        self.assertIn("could not be loaded", msg)
        self.assertIsNone(site_time.set_zone("Mars/Olympus_Mons"))   # said once
        self.assertEqual(site_time.zone_name(), "Europe/Warsaw")
        self.assertEqual(site_time.jd_to_local(jd_of(2026, 7, 1, 12)).hour, 14)   # the computer's (CEST)

    def test_zone_list(self):
        zones = site_time.available_zones()
        self.assertIn("America/Phoenix", zones)
        self.assertEqual(zones, sorted(zones))


class TestTypedTimes(_Zone):
    def test_local_to_jd_reads_the_site_clock(self):
        site_time.set_zone("America/Phoenix")
        self.assertAlmostEqual(site_time.local_to_jd(dt.date(2026, 10, 1), 23, 34), jd_of(2026, 10, 2, 6, 34),
                               places=7)
        site_time.set_zone("America/New_York")   # after the change: 23:00 EST = 04:00 UTC
        self.assertAlmostEqual(site_time.local_to_jd(dt.date(2026, 11, 1), 23, 0), jd_of(2026, 11, 2, 4), places=7)

    def test_trim_box_and_back(self):
        import transit_ui
        site_time.set_zone("America/Phoenix")
        p = transit_ui.TransitPage.__new__(transit_ui.TransitPage)
        jd = jd_of(2026, 10, 2, 5) + np.linspace(0, 0.15, 50)
        off = np.full(jd.shape, 0.003)
        tmap = (jd + off, off, True)
        p._time_map = tmap
        bjd = p._clock_to_bjd("23:34", tmap)
        self.assertAlmostEqual(bjd - 0.003, jd_of(2026, 10, 2, 6, 34), places=6)
        self.assertEqual(p._bjd_to_clock(bjd), "23:34")
        self.assertEqual(p._bjd_offset(bjd), "UTC−7")


class TestSettingsAndSeries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tkinter
        try:
            tkinter.Tk().destroy()
        except Exception as exc:
            raise unittest.SkipTest(f"no display: {exc}")
        import clearv_app as ca
        cls.ca = ca
        cls.tmp = tempfile.mkdtemp()
        cls.settings_file = os.path.join(cls.tmp, "settings.json")
        with open(cls.settings_file, "w", encoding="utf-8") as fh:
            json.dump({"site_lat": "35.4", "site_lon": "-81.9"}, fh)   # a 2.2.20 settings file: no time zone
        cls.patches = [mock.patch.object(ca, "SETTINGS_FILE", cls.settings_file),
                       mock.patch.object(site_time, "computer_zone_name", return_value="America/New_York")]
        for p in cls.patches:
            p.start()
        cls.app = ca.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.destroy()
        except Exception:
            pass
        for p in cls.patches:
            p.stop()
        site_time.set_zone("")

    def _saved(self):
        with open(self.settings_file, encoding="utf-8") as fh:
            return json.load(fh)

    def test_old_settings_start_with_the_computer_zone(self):
        self.app._own_setup = None
        self.app.site_tz.set("")
        self.app.site_tz.set("America/New_York")
        self.assertEqual(site_time.zone_name(), "America/New_York")

    def test_zone_round_trip_in_settings_and_series(self):
        app = self.app
        app._own_setup = None
        app.site_tz.set("America/Phoenix")
        self.assertEqual(self._saved().get("site_tz"), "America/Phoenix")
        self.assertEqual(site_time.zone_name(), "America/Phoenix")
        meta = app._meta()
        self.assertEqual(meta["site_tz"], "America/Phoenix")
        # a series saved before 2.2.21 (no site_tz): the box keeps its zone (2.2.21 review), the settings keep yours
        old = {k: v for k, v in meta.items() if k != "site_tz"}
        app._apply_meta(old)
        self.assertEqual(app.site_tz.get(), "America/Phoenix")
        self.assertEqual(self._saved().get("site_tz"), "America/Phoenix")
        meta["site_tz"] = "Europe/Madrid"
        app._apply_meta(meta)
        self.assertEqual(app.site_tz.get(), "Europe/Madrid")
        self.assertEqual(site_time.zone_name(), "Europe/Madrid")
        self.assertEqual(self._saved().get("site_tz"), "America/Phoenix")   # a series' zone is never saved as yours
        app.site_tz.set("America/New_York")

    def test_half_typed_name_is_not_used(self):
        app = self.app
        app._own_setup = None
        app.site_tz.set("America/New_York")
        app.site_tz.set("Amer")
        self.assertEqual(site_time.zone_name(), "America/New_York")
        self.assertEqual(self._saved().get("site_tz"), "America/New_York")
        app.site_tz_values()
        self.assertTrue(all("amer" in z.lower() for z in app.site_tz_box.cget("values")))
        app.site_tz.set("America/New_York")

    def test_header_clock_and_axis_label(self):
        app = self.app
        app.site_tz.set("America/New_York")
        header = {"DATE-OBS": "2026-11-02T00:30:00", "EXPTIME": 0}
        text = self.ca.App._header_clock(header)
        self.assertTrue(text.startswith("19:30:00"), text)
        self.assertTrue(text.endswith("UTC−5"), text)
        self.assertEqual(app._time_text(jd_of(2026, 11, 2, 0, 30)), "JD 2461346.52083 (Nov 01 19:30 local time (UTC−5))")


if __name__ == "__main__":
    unittest.main()
