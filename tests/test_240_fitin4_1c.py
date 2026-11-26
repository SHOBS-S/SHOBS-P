"""2.4.0 fit-in F4, package F4-1 (c), in the app: a name typed over a marked comp slot takes the marker and the
magnitude of that star (#64 item 3, m336); a loaded series never runs the comp survey or the catalog comp search
(#72, John's rule of 20 Nov)."""
import math
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_240_n import PlaceBase, HAVE_TK   # noqa: E402


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestRetypedCompName(PlaceBase):
    def _two(self):
        app, field = self._setup()
        c1 = self.ca.COMP_ROLES[0]
        a, b = field[3], field[9]
        getattr(app, f"{c1}_name").set("TYC 100-3-1")
        app._slot_lookup(c1)
        return app, c1, a, b

    def test_typing_another_star_moves_the_marker_and_the_magnitude(self):
        app, c1, a, b = self._two()
        got = getattr(app, f"{c1}_xy")
        self.assertLess(math.hypot(got[0] - a[0], got[1] - a[1]), 0.5)
        old_mag = getattr(app, f"{c1}_mag").get()
        self.assertAlmostEqual(float(old_mag), a[2], places=1)
        # type the other star's name over it and press Enter
        getattr(app, f"{c1}_name").set("TYC 100-9-1")
        _n, lines = self._logged(lambda: app._slot_lookup(c1))
        got = getattr(app, f"{c1}_xy")
        self.assertLess(math.hypot(got[0] - b[0], got[1] - b[1]), 0.5)          # the marker is on the new star
        self.assertAlmostEqual(float(getattr(app, f"{c1}_mag").get()), b[2], places=1)   # and so is its V
        said = [ln for ln in lines if "Magnitude was" in ln]
        self.assertEqual(len(said), 1, lines)
        self.assertIn(f"Magnitude was {old_mag}, now {getattr(app, f'{c1}_mag').get()}", said[0])   # m336
        self.assertIn("the marker moved", said[0])
        self.assertFalse(any("the marker stays where it is" in ln for ln in lines))

    def test_same_star_under_another_name_keeps_the_marker(self):
        app, c1, a, b = self._two()
        before = getattr(app, f"{c1}_xy")
        # the series knows this very star under its own name, at the same sky position
        app.comp_coords = {"MY COMP": self._rd(a[0], a[1])}
        getattr(app, f"{c1}_name").set("MY COMP")
        _n, lines = self._logged(lambda: app._slot_lookup(c1))
        got = getattr(app, f"{c1}_xy")
        self.assertLess(math.hypot(got[0] - before[0], got[1] - before[1]), 0.5)
        self.assertTrue(any("the same star, so the marker stays" in ln for ln in lines), lines)

    def test_unknown_name_only_renames_and_says_the_magnitude_is_unchanged(self):
        app, c1, a, b = self._two()
        before, mag = getattr(app, f"{c1}_xy"), getattr(app, f"{c1}_mag").get()
        getattr(app, f"{c1}_name").set("my favourite star")
        _n, lines = self._logged(lambda: app._slot_lookup(c1))
        self.assertEqual(getattr(app, f"{c1}_xy"), before)
        self.assertEqual(getattr(app, f"{c1}_mag").get(), mag)
        self.assertTrue(any("the magnitude is unchanged" in ln for ln in lines), lines)

    def test_a_star_that_cannot_be_placed_puts_the_marker_back(self):
        app, c1, a, b = self._two()
        before, mag = getattr(app, f"{c1}_xy"), getattr(app, f"{c1}_mag").get()
        app.comp_coords = {"FAR AWAY": (app.chart_solution["ra_deg"] + 2.0, app.chart_solution["dec_deg"])}
        getattr(app, f"{c1}_name").set("FAR AWAY")
        _n, lines = self._logged(lambda: app._slot_lookup(c1))
        self.assertEqual(getattr(app, f"{c1}_xy"), before)
        self.assertEqual(getattr(app, f"{c1}_mag").get(), mag)
        self.assertTrue(any("The marker stays on the star it was on" in ln for ln in lines), lines)


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestLoadedSeriesNeverSurveys(PlaceBase):
    def test_rule(self):
        app, field = self._setup()
        c1, c2 = self.ca.COMP_ROLES[:2]
        self.assertFalse(app._series_no_survey())                 # no series
        app.series_path = "/x/star.cvseries"
        app.comps_used = ["TYC 100-3-1", "TYC 100-9-1"]
        app.active_comps = []
        try:
            self.assertTrue(app._series_no_survey())              # a loaded series: never, placed or not
            self.assertFalse(app._series_comps_all_placed())
            # one comp placed, one off this image tonight: still no survey, and a plain note names it
            getattr(app, f"{c1}_name").set("TYC 100-3-1")
            app._slot_lookup(c1)
            getattr(app, f"{c2}_name").set("TYC 100-9-1")
            app._slot_state[c2] = (app._preview_token, "TYC 100-9-1", "off")
            app._survey_skip_said = None
            _n, lines = self._logged(app._say_survey_skipped)
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith("Comp survey and comp suggestions skipped: a series is loaded"))
            self.assertIn("TYC 100-9-1 is not on this image tonight, so it is not measured; the name stays in the "
                          "series.", lines[0])
            self.assertNotIn("⚠", lines[0])
            _n, again = self._logged(app._say_survey_skipped)     # once per image and target
            self.assertEqual(again, [])
            with mock.patch.object(app, "_survey_start", side_effect=AssertionError("no survey for a series")):
                app._survey_cache = None
                app._suggest_comps()
            self.assertEqual(app.suggested_comps, [])             # no catalog comp search either
            app.set_mode("discovery")
            self.assertFalse(app._series_no_survey())
        finally:
            app.set_mode("variables")
            app.series_path = ""
            app.comps_used = []


if __name__ == "__main__":
    unittest.main()
