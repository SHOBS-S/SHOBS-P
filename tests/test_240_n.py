"""2.4.0 step 4 (s4-place): the Photometry page after a plate solve.

#72: a series' comps and check are placed from their STORED sky positions (comp_coords) and centered like a click;
a catalog name of the same star is an alias; "not on this frame" only when the position really is off it; one Log
line per star. #71: a wider recenter search, and a marker that cannot be centered is said (Log, status, Findings)
while Run photometry only warns. #77: the name of a marked star is picked by brightness (field zero point), not by
the nearest catalog position. An already-centered setup gives the same marker positions as before."""
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ["SHOBS_ONSKY_OFF"] = "1"

import numpy as np  # noqa: E402
import star_record  # noqa: E402
import matplotlib  # noqa: E402
matplotlib.use("Agg")

try:
    import tkinter
    _r = tkinter.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False

SIGMA = 1.6          # star width (px); FWHM about 3.8 px
SHAPE = (400, 500)
SOLUTION = {"target_xy": (250.0, 200.0), "scale": 1.0, "angle": 0.0, "ra_deg": 30.0, "dec_deg": 0.0, "auto": True}


def _amp(v):
    return 30000.0 * 10 ** (-0.4 * (v - 10.0))


def _image(stars, seed=5):
    """Gaussian stars [(x, y, V)] on a flat sky with noise."""
    rng = np.random.default_rng(seed)
    h, w = SHAPE
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.full(SHAPE, 300.0)
    for x, y, v in stars:
        img += _amp(v) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * SIGMA ** 2))
    return img + rng.normal(0, 5, SHAPE)


def _field_stars(n=24, seed=11):
    """Isolated field stars on a jittered grid, V 10 to 13, clear of the edges and of the test spots."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        gx, gy = i % 6, i // 6
        out.append((70.0 + gx * 72.0 + rng.uniform(-6, 6), 90.0 + gy * 72.0 + rng.uniform(-6, 6),
                    float(rng.uniform(10.0, 13.0))))
    return out


def _app():
    import clearv_app as ca
    ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test240n.log")
    return ca, ca.App()


@unittest.skipUnless(HAVE_TK, "needs a display")
class PlaceBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ca, cls.app = _app()
        cls.app.set_mode("variables")
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def _setup(self, extra=(), chart_extra=(), chart_skip=(), field=None):
        """A solved image: the field stars (all in the chart with their true V, so there is a zero point), extra
        stars on the image only [(x, y, V)], chart_extra entries [(x, y, star dict)] with no light of their own
        unless also in extra, chart_skip: indexes of field stars left out of the chart."""
        app, ca = self.app, self.ca
        field = _field_stars() if field is None else field
        app.preview = _image(list(field) + list(extra) + [(250.0, 200.0, 11.0)])   # the target's own star
        app.preview_is_debayered = True
        app._preview_token = getattr(app, "_preview_token", 0) + 1
        app._preview_foreign = ""
        app.series_path = ""
        app.observations = []
        app.radius.set(5); app.sky_in.set(9); app.sky_out.set(14)
        app.chart_solution = dict(SOLUTION)
        app.ra_hours.set("2.0"); app.dec_deg.set("0.0")
        app.chart_placed = []
        for i, (x, y, v) in enumerate(field):
            if i in chart_skip:
                continue
            ra, dec = ca.core.pixel_to_sky(x, y, app.chart_solution)
            app.chart_placed.append((float(x), float(y), {"auid": f"TYC 100-{i}-1", "aka": [], "label": f"{v:.1f}",
                                                          "ra": ra, "dec": dec, "mag": v, "mags": {"V": v},
                                                          "band": "V", "catalog": "Tycho-2"}))
        for x, y, st in chart_extra:
            ra, dec = ca.core.pixel_to_sky(x, y, app.chart_solution)
            app.chart_placed.append((float(x), float(y), dict({"ra": ra, "dec": dec, "band": "V", "aka": [],
                                                               "catalog": "Gaia DR3"}, **st)))
        app.target_xy = (250.0, 200.0)
        app.comp_coords = {}
        app.comp_catalog_mags = {}
        app._slot_typed = {}
        app._slot_notes = {}
        app._slot_state = {}
        app._tonight_aliases = {}
        app.findings.clear()
        for r in list(ca.COMP_ROLES) + ["check"]:
            setattr(app, f"{r}_xy", None)
            getattr(app, f"{r}_name").set("")
            getattr(app, f"{r}_mag").set("")
        app.star_bands = {}
        return app, field

    def _rd(self, x, y):
        return self.ca.core.pixel_to_sky(float(x), float(y), self.app.chart_solution)

    def _logged(self, fn):
        lines = []
        real = self.app.log

        def grab(text, *a, **k):
            lines.append(text)
            return real(text, *a, **k)
        with mock.patch.object(self.app, "log", side_effect=grab):
            out = fn()
        return out, lines


class TestSeriesPlacement(PlaceBase):
    """#72: stored RA/Dec first, centered like a click; alias, not a rename; one line per star."""

    def test_placed_from_stored_position_when_catalogs_lack_the_name(self):
        app, field = self._setup(chart_skip=(3,))          # tonight's catalogs do not have this star at all
        app.series_path = "/tmp/series.csv"
        c1 = self.ca.COMP_ROLES[0]
        x, y, _v = field[3]
        app.comp_coords = {"Gaia DR3 4006100810081": self._rd(x + 0.6, y - 0.5)}   # the solve is 0.8 px out
        getattr(app, f"{c1}_name").set("Gaia DR3 4006100810081")
        getattr(app, f"{c1}_mag").set(f"{_v:.3f}")          # the series' magnitude of this star
        self.assertIsNone(app._chart_star_named("Gaia DR3 4006100810081"))
        placed, lines = self._logged(app._auto_place_named_slots)
        self.assertEqual(placed, [c1])
        got = getattr(app, f"{c1}_xy")
        self.assertLess(math.hypot(got[0] - x, got[1] - y), 0.3)              # on the star, not on the projection
        self.assertEqual(getattr(app, f"{c1}_name").get(), "Gaia DR3 4006100810081")
        self.assertEqual(getattr(app, f"{c1}_mag").get(), f"{_v:.3f}")
        mine = [ln for ln in lines if "Gaia DR3 4006100810081" in ln]
        self.assertEqual(len(mine), 1, lines)                                   # ONE line per star
        self.assertRegex(mine[0], r"^Comp 1 Gaia DR3 4006100810081 placed from the series position, centered "
                                  r"\(\d\.\d px from where the position falls\); magnitude \d+\.\d{3} kept as loaded\.$")
        self.assertNotIn("⚠", mine[0])
        self.assertEqual(len(app.findings), 0)

    def test_catalog_name_of_the_same_star_is_an_alias(self):
        app, field = self._setup()
        c2 = self.ca.COMP_ROLES[1]
        x, y, v = field[7]                                   # tonight this star answers to TYC 100-7-1
        app.comp_coords = {"Gaia DR3 77": self._rd(x + 0.3, y)}
        getattr(app, f"{c2}_name").set("Gaia DR3 77")
        getattr(app, f"{c2}_mag").set("11.111")
        placed, lines = self._logged(app._auto_place_named_slots)
        self.assertEqual(placed, [c2])
        self.assertEqual(getattr(app, f"{c2}_name").get(), "Gaia DR3 77")       # never renamed
        self.assertEqual(getattr(app, f"{c2}_mag").get(), "11.111")             # never re-magnituded
        self.assertEqual(app._tonight_aliases.get("Gaia DR3 77"), ["TYC 100-7-1"])
        line = [ln for ln in lines if "Gaia DR3 77" in ln]
        self.assertEqual(len(line), 1, lines)
        self.assertIn("= TYC 100-7-1 in tonight's catalogs (the same star; the series' name is kept)", line[0])
        # the stored position is not replaced by tonight's catalog position
        self.assertEqual(app.comp_coords["Gaia DR3 77"], self._rd(x + 0.3, y))
        self.assertNotIn("TYC 100-7-1", app.comp_coords)

    def test_name_match_only_without_a_stored_position(self):
        app, field = self._setup()
        x, y, v = field[9]
        app.check_name.set("TYC 100-9-1")
        app.check_mag.set("9.786")
        placed, lines = self._logged(app._auto_place_named_slots)
        self.assertEqual(placed, ["check"])
        self.assertLess(math.hypot(app.check_xy[0] - x, app.check_xy[1] - y), 0.3)
        self.assertIn("placed from its name in tonight's catalogs (no saved position)", "\n".join(lines))
        # no stored position and no such name: said, with a finding; the name stays
        app, field = self._setup()
        app.check_name.set("NOBODY 1")
        app.check_mag.set("9.9")
        placed, lines = self._logged(app._auto_place_named_slots)
        self.assertEqual(placed, [])
        self.assertEqual(app.check_name.get(), "NOBODY 1")
        self.assertTrue(any(ln.startswith("⚠ Check NOBODY 1 couldn't be placed") for ln in lines), lines)
        self.assertEqual(len(app.findings), 1)

    def test_one_line_per_series_star(self):
        app, field = self._setup()
        roles = list(self.ca.COMP_ROLES[:3]) + ["check"]
        picks = [2, 5, 8, 13]
        for role, k in zip(roles, picks):
            x, y, _v = field[k]
            app.comp_coords[f"SER {k}"] = self._rd(x, y)
            getattr(app, f"{role}_name").set(f"SER {k}")
            getattr(app, f"{role}_mag").set("10.5")
        far = self._rd(250.0, -60.0)
        c4 = self.ca.COMP_ROLES[3]
        app.comp_coords["SER FAR"] = far
        getattr(app, f"{c4}_name").set("SER FAR")
        getattr(app, f"{c4}_mag").set("10.9")
        placed, lines = self._logged(app._auto_place_named_slots)
        self.assertEqual(placed, roles)
        for name in [f"SER {k}" for k in picks] + ["SER FAR"]:
            self.assertEqual(sum(1 for ln in lines if name + " " in ln), 1, (name, lines))
        # a second solve: the placed ones are reported as already marked, still one line each
        placed, lines = self._logged(app._auto_place_named_slots)
        self.assertEqual(placed, [])
        for name in [f"SER {k}" for k in picks]:
            mine = [ln for ln in lines if name + ":" in ln]
            self.assertEqual(len(mine), 1, lines)
            self.assertIn("already marked", mine[0])

    def test_enter_uses_the_stored_position(self):
        """_slot_lookup: the series' own position before tonight's names and before SIMBAD."""
        app, field = self._setup(chart_skip=(4,))
        c1 = self.ca.COMP_ROLES[0]
        x, y, _v = field[4]
        app.comp_coords = {"Gaia DR3 44": self._rd(x, y)}
        getattr(app, f"{c1}_name").set("Gaia DR3 44")
        with mock.patch.object(self.ca.core, "lookup_target", side_effect=AssertionError("no lookup needed")):
            app._slot_lookup(c1)
        got = getattr(app, f"{c1}_xy")
        self.assertIsNotNone(got)
        self.assertLess(math.hypot(got[0] - x, got[1] - y), 0.3)
        self.assertEqual(getattr(app, f"{c1}_name").get(), "Gaia DR3 44")

    def test_load_on_a_solved_image_without_catalog_stars_places_right_away(self):
        app, field = self._setup()
        c1 = self.ca.COMP_ROLES[0]
        x, y, _v = field[6]
        app.chart_placed = []                                 # solved (header WCS), but the catalogs did not answer
        app.comp_coords = {"SER 6": self._rd(x, y)}
        getattr(app, f"{c1}_name").set("SER 6")
        getattr(app, f"{c1}_mag").set("10.2")
        with mock.patch.object(app, "_auto_solve", side_effect=AssertionError("no second solve needed")):
            app._resolve_for_loaded_series()
        got = getattr(app, f"{c1}_xy")
        self.assertIsNotNone(got)
        self.assertLess(math.hypot(got[0] - x, got[1] - y), 0.3)


class TestOffFrame(PlaceBase):
    """#72: off the frame only when the projected position really is; a series star is never silently dropped."""

    def test_near_the_edge_is_on_the_frame(self):
        near = (300.0, 10.0, 11.0)                            # 10 px from the top: was "not on this frame" before
        app, field = self._setup(extra=[near])
        c1 = self.ca.COMP_ROLES[0]
        app.comp_coords = {"EDGE 1": self._rd(300.5, 10.4)}
        getattr(app, f"{c1}_name").set("EDGE 1")
        getattr(app, f"{c1}_mag").set("11.0")
        placed, lines = self._logged(app._auto_place_named_slots)
        self.assertEqual(placed, [c1], lines)
        got = getattr(app, f"{c1}_xy")
        self.assertLess(math.hypot(got[0] - 300.0, got[1] - 10.0), 0.4)

    def test_really_off_keeps_the_name_and_says_where(self):
        app, field = self._setup()
        c1 = self.ca.COMP_ROLES[0]
        app.series_path = "/tmp/series.csv"
        app.comp_coords = {"SER OFF": self._rd(250.0, -40.0)}
        getattr(app, f"{c1}_name").set("SER OFF")
        getattr(app, f"{c1}_mag").set("10.75")
        app._drop_offframe_slots()                            # the solve's order: drop, then place
        placed, lines = self._logged(app._auto_place_named_slots)
        self.assertEqual(placed, [])
        self.assertIsNone(getattr(app, f"{c1}_xy"))
        self.assertEqual(getattr(app, f"{c1}_name").get(), "SER OFF")           # never silently dropped
        self.assertEqual(getattr(app, f"{c1}_mag").get(), "10.75")
        line = [ln for ln in lines if "SER OFF" in ln]
        self.assertEqual(len(line), 1, lines)
        self.assertIn("is not on this frame tonight", line[0])
        self.assertRegex(line[0], r"falls at \(250, -40\), 40 px beyond the top edge")
        self.assertIn("its position (02h 00m 00.0s +00° 04′ 00″)", line[0])      # said with the position
        self.assertNotIn("⚠", line[0])
        self.assertEqual(app._slot_state[c1][2], "off")
        self.assertIn("not on this frame", app._slot_notes[c1][1])
        # a second solve does not bring it back (the 2.3.6 m088 lesson), and Run does not ask about it or clear it
        self.assertEqual(app._auto_place_named_slots(), [])
        self.assertIsNone(getattr(app, f"{c1}_xy"))
        with mock.patch.object(self.ca, "ask_choice", side_effect=AssertionError("not asked")), \
                mock.patch.object(self.ca.messagebox, "askyesnocancel", side_effect=AssertionError("not asked")):
            self.assertTrue(app._markers_in_order())
        self.assertEqual(getattr(app, f"{c1}_name").get(), "SER OFF")

    def test_at_the_edge_without_a_star_is_not_placed_but_kept(self):
        app, field = self._setup()
        c1 = self.ca.COMP_ROLES[0]
        app.series_path = "/tmp/series.csv"
        app.comp_coords = {"SER EDGE": self._rd(250.0, -3.0)}  # 3 px beyond the edge: less than the aperture
        getattr(app, f"{c1}_name").set("SER EDGE")
        getattr(app, f"{c1}_mag").set("10.75")
        placed, lines = self._logged(app._auto_place_named_slots)
        self.assertEqual(placed, [])
        self.assertEqual(getattr(app, f"{c1}_name").get(), "SER EDGE")
        self.assertTrue(any(ln.startswith("⚠ Comp 1 SER EDGE couldn't be placed") and "Solve again, or click the star"
                            in ln for ln in lines), lines)
        self.assertEqual(app._slot_state[c1][2], "unplaced")
        # before Run: asked, and the default keeps the name
        asked = []

        def answer(parent, title, message, choices, safe, cancel=None):
            asked.append((message, safe))
            return safe
        with mock.patch.object(self.ca, "ask_choice", answer):
            self.assertTrue(app._markers_in_order())
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0][1], "keep")
        self.assertEqual(getattr(app, f"{c1}_name").get(), "SER EDGE")

    def test_weak_solve_no_star_where_the_position_falls(self):
        app, field = self._setup()
        c1 = self.ca.COMP_ROLES[0]
        app.comp_coords = {"SER LOST": self._rd(36.0, 200.0)}   # blank sky, well inside the frame
        getattr(app, f"{c1}_name").set("SER LOST")
        getattr(app, f"{c1}_mag").set("10.75")
        placed, lines = self._logged(app._auto_place_named_slots)
        self.assertEqual(placed, [])
        self.assertIsNone(getattr(app, f"{c1}_xy"))             # no marker on blank sky
        self.assertEqual(getattr(app, f"{c1}_name").get(), "SER LOST")
        self.assertTrue(any("couldn't be placed: no star was found within" in ln for ln in lines), lines)
        self.assertEqual(len(app.findings), 1)

    def test_series_marker_outside_the_image_keeps_its_name(self):
        app, field = self._setup()
        c1, c2 = self.ca.COMP_ROLES[:2]
        x, y, _v = field[10]
        app.series_path = "/tmp/series.csv"
        app.comp_coords = {"SER 10": self._rd(x, y)}
        getattr(app, f"{c1}_name").set("SER 10")
        getattr(app, f"{c1}_mag").set("10.75")
        setattr(app, f"{c1}_xy", (900.0, 40.0))               # carried from another reference frame: off this image
        getattr(app, f"{c2}_name").set("STRANGER")             # not a star of the series
        getattr(app, f"{c2}_mag").set("11.0")
        setattr(app, f"{c2}_xy", (900.0, 80.0))
        dropped = app._drop_offframe_slots()
        self.assertEqual(dropped, ["Comp 2 STRANGER"])
        self.assertEqual(getattr(app, f"{c2}_name").get(), "")
        self.assertEqual(getattr(app, f"{c1}_name").get(), "SER 10")
        self.assertIsNone(getattr(app, f"{c1}_xy"))
        # then placed from its sky position, never from the old pixels
        self.assertEqual(app._auto_place_named_slots(), [c1])
        got = getattr(app, f"{c1}_xy")
        self.assertLess(math.hypot(got[0] - x, got[1] - y), 0.3)


class TestWiderRecenter(PlaceBase):
    """#71: the Qatar-10 b 0704 case and the marker that cannot be centered."""

    def test_seven_px_off_near_the_edge_is_centered(self):
        star = (300.0, 21.8, 11.5)                            # 22 px from the top edge
        # step 4 review (M3): tonight's catalog star is drawn where the solution puts it (6.8 px out, like the
        # stored position: the same RA/Dec), so it is the star meant and nothing else is near
        app, field = self._setup(extra=[star], chart_extra=[(300.0, 28.6, {"auid": "APASS J185609.9+692057",
                                                                         "mag": 11.5, "mags": {"V": 11.5}})])
        c4 = self.ca.COMP_ROLES[3]
        app.series_path = "/tmp/series.csv"
        app.comp_coords = {"APASS J185609.9+692057": self._rd(300.0, 28.6)}    # the solution is 6.8 px out here
        getattr(app, f"{c4}_name").set("APASS J185609.9+692057")
        getattr(app, f"{c4}_mag").set("11.5")
        # the old rule alone would have kept the catalog position
        fwhm = app._seeing()
        self.assertTrue(math.isfinite(fwhm))
        self.assertGreater(6.8, max(2.0, 1.5 * fwhm))
        placed, lines = self._logged(app._auto_place_named_slots)
        self.assertEqual(placed, [c4], lines)
        got = getattr(app, f"{c4}_xy")
        self.assertLess(math.hypot(got[0] - 300.0, got[1] - 21.8), 0.3)
        line = [ln for ln in lines if "APASS J185609.9+692057" in ln]
        self.assertEqual(len(line), 1, lines)
        self.assertRegex(line[0], r"^⚠ .*centered by a wider search, 6\.\d px from where the position falls — more "
                                  r"than the normal centering allows \(22 px from the top edge\)\. Check it is the "
                                  r"right star")
        self.assertEqual(len(app.findings), 1)                # review M3: a wide hop is always a finding
        # the run's own check has nothing to say about it
        self.assertEqual(app._warn_marker_offsets(5.0), [])

    def test_typed_name_placed_by_enter_gets_the_wider_search(self):
        star = (300.0, 21.8, 11.5)
        app, field = self._setup(extra=[star])
        c1 = self.ca.COMP_ROLES[0]
        getattr(app, f"{c1}_name").set("QATAR COMP")
        app._set_slot(c1, (300.0, 28.6), "QATAR COMP")
        got = getattr(app, f"{c1}_xy")
        self.assertLess(math.hypot(got[0] - 300.0, got[1] - 21.8), 0.3)
        self.assertIn("wider search", app._slot_notes[c1][1])
        self.assertIn("⚠", app._slot_notes[c1][1])
        self.assertEqual(len(app.findings), 1)                # review M3: a wide hop is always a finding

    def test_cannot_center_is_said_and_run_only_warns(self):
        a, b = (300.0, 40.0, 11.0), (314.0, 40.0, 11.2)       # two catalog stars; the position falls between them
        app, field = self._setup(extra=[a, b], chart_extra=[
            (300.0, 40.0, {"auid": "Gaia DR3 1", "mag": 11.0, "mags": {"V": 11.0}}),
            (314.0, 40.0, {"auid": "Gaia DR3 2", "mag": 11.2, "mags": {"V": 11.2}})])
        c1 = self.ca.COMP_ROLES[0]
        getattr(app, f"{c1}_name").set("BETWEEN")
        _none, lines = self._logged(lambda: app._set_slot(c1, (307.0, 40.0), "BETWEEN"))
        got = getattr(app, f"{c1}_xy")
        self.assertEqual(got, (307.0, 40.0))                  # kept at the catalog position...
        warn = [ln for ln in lines if ln.startswith("⚠ Comp 1")]
        self.assertEqual(len(warn), 1, lines)                 # ...and said: Log, status line, Findings
        self.assertIn("not centered on a star", warn[0])
        self.assertIn("more than one catalog star", warn[0])
        self.assertIn("Click the star to center it", warn[0])
        self.assertIn("not centered on a star", app.status.get())
        self.assertEqual([f.text for f in app.findings.items], [warn[0]])
        self.assertIn("Click the star to center the marker", app.findings.items[0].hint)   # chapter 23's words
        self.assertTrue(app._slot_notes[c1][2])
        # Run photometry: a finding with the offset in px and the edge distance; it never refuses
        with mock.patch.object(self.ca, "ask_no", side_effect=AssertionError("Run must not ask")), \
                mock.patch.object(self.ca.messagebox, "showerror", side_effect=AssertionError("Run must not refuse")):
            ok, lines = self._logged(app._markers_in_order)
        self.assertTrue(ok)
        run = [f for f in app.findings.items if f.step == app.MARKER_STEP]
        self.assertEqual(len(run), 1, [f.text for f in app.findings.items])
        self.assertRegex(run[0].text, r"^⚠ Comp 1 marker at \(307\.0, 40\.0\) is 7\.\d px from the center of the star")
        self.assertIn("the marker is 40 px from the top edge", run[0].text)
        self.assertIn("click the star again", run[0].hint)
        # a second Run replaces the finding instead of adding a twin; a click on the star takes both away
        self.assertTrue(app._markers_in_order())
        self.assertEqual(len([f for f in app.findings.items if f.step == app.MARKER_STEP]), 1)
        app.clear_mark(c1, redraw=False)
        self.assertEqual([f for f in app.findings.items if f.step != app.MARKER_STEP], [])


class TestNameByBrightness(PlaceBase):
    """#77: GSC 03308-00462, 9 Nov 2026, bin 2."""

    BRIGHT = {"auid": "APASS J024802.6+505201", "aka": [], "mag": 12.651, "mags": {"V": 12.651},
              "catalog": "APASS DR9", "seq": {"label": "127", "auid": "000-BBB-127", "chartid": "X1"}}
    FAINT = {"auid": "Gaia DR3 439030152547276544", "mag": 17.977, "mags": {"V": 17.977}}

    def _click(self, app, x, y):
        import types
        app.pick_mode.set("comp")
        app.on_image_click(types.SimpleNamespace(xdata=x, ydata=y, button=1))
        role = self.ca.COMP_ROLES[0]
        return getattr(app, f"{role}_name").get(), getattr(app, f"{role}_mag").get(), getattr(app, f"{role}_xy")

    def test_zero_point_from_the_chart_stars(self):
        app, field = self._setup()
        zp = app._field_zero_point()
        self.assertIsNotNone(zp)
        self.assertLess(zp["scatter"], 0.1)
        x, y, v = field[5]
        self.assertAlmostEqual(app._seen_mag((x, y)), v, delta=0.15)

    def test_invisible_faint_neighbor_cannot_take_the_name(self):
        # the bright star's own chart position is drawn 9 px left of it (a predicted position); the invisible
        # V 18 Gaia star's is 7.5 px to its right, so it is the nearest within 8 px
        star = (420.0, 60.0, 12.651)
        app, field = self._setup(extra=[star], chart_extra=[(411.0, 60.0, self.BRIGHT), (427.5, 60.0, self.FAINT)])
        self.assertEqual(self.ca.App._chart_star_label(app._chart_star_at((420.0, 60.0))),
                         "Gaia DR3 439030152547276544")       # what 2.3.x named it
        (name, mag, xy), lines = self._logged(lambda: self._click(app, 420.6, 60.5))
        self.assertLess(math.hypot(xy[0] - 420.0, xy[1] - 60.0), 0.3)
        self.assertEqual(mag, "12.651")
        self.assertEqual(name, "000-BBB-127")                 # star_record.display_name: the AUID first
        self.assertTrue(any("named by brightness" in ln and "too faint to be this star" in ln for ln in lines), lines)

    def test_bright_star_missing_from_the_chart_takes_no_neighbors_name(self):
        star = (420.0, 60.0, 12.651)
        app, field = self._setup(extra=[star], chart_extra=[(427.5, 60.0, self.FAINT)])
        (name, mag, xy), lines = self._logged(lambda: self._click(app, 420.6, 60.5))
        self.assertLess(math.hypot(xy[0] - 420.0, xy[1] - 60.0), 0.3)
        self.assertEqual((name, mag), ("", ""))
        self.assertTrue(any("No name or magnitude was taken from it" in ln for ln in lines), lines)
        self.assertIn("too faint to be this star", app.status.get())

    def test_star_right_under_the_marker_keeps_the_position_rule(self):
        # numbers-safe: the nearest catalog star within the seeing wins whatever its catalog magnitude says
        star = (420.0, 60.0, 12.651)
        app, field = self._setup(extra=[star], chart_extra=[(420.8, 60.3, dict(self.FAINT, auid="Gaia DR3 9"))])
        name, mag, _xy = self._click(app, 420.2, 60.1)
        self.assertEqual((name, mag), ("Gaia DR3 9", "17.977"))

    def test_without_a_zero_point_the_nearest_wins_as_before(self):
        star = (420.0, 60.0, 12.651)
        app, field = self._setup(extra=[star], chart_extra=[(411.0, 60.0, self.BRIGHT), (427.5, 60.0, self.FAINT)])
        with mock.patch.object(app, "_field_zero_point", return_value=None):
            name, mag, _xy = self._click(app, 420.6, 60.5)
        self.assertEqual((name, mag), ("Gaia DR3 439030152547276544", "17.977"))

    def test_merged_names_show_the_display_name_and_a_series_name_stays(self):
        x, y = 420.0, 60.0
        merged = {"auid": "APASS J025525.9-105500", "aka": ["Gaia DR3 5160920530660588672"], "mag": 12.0,
                  "mags": {"V": 12.0}}
        app, field = self._setup(extra=[(x, y, 12.0)], chart_extra=[(x, y, merged)])
        name, _mag, _xy = self._click(app, x, y)
        self.assertEqual(name, "Gaia DR3 5160920530660588672")   # Gaia before APASS (WASP-50 b, 18 Nov)
        # a series that already knows this star as the APASS name keeps that name
        app, field = self._setup(extra=[(x, y, 12.0)], chart_extra=[(x, y, merged)])
        app.comp_coords = {"APASS J025525.9-105500": self._rd(x, y)}
        # merge (step 4): a loaded series always carries star records (s4-record); the record is what the series knows
        rd = self._rd(x, y)
        self.addCleanup(setattr, app, "star_records", [])   # the app is shared by the class's tests
        app.star_records = [star_record.new_record("APASS J025525.9-105500", ra=rd[0], dec=rd[1], role="comp",
                                                   names=["APASS J025525.9-105500"])]
        name, _mag, _xy = self._click(app, x, y)
        self.assertEqual(name, "APASS J025525.9-105500")


class TestNumbersSafe(PlaceBase):
    """A setup whose markers landed centered before lands on exactly the same pixels now."""

    def _old_snap(self, xy):
        """2.3.6's _snap_to_star, word for word in effect."""
        app, core = self.app, self.ca.core
        radius = float(app.radius.get())
        seeing = app._seeing()
        fwhm = seeing if math.isfinite(seeing) else None
        info = core.recenter_info(app.preview, float(xy[0]), float(xy[1]), radius, fwhm, wing="dip")
        reach = max(2.0, 1.5 * fwhm) if fwhm else max(2.0, 0.5 * radius)
        if info.get("found") and not info.get("blended") and info["shift"] <= reach:
            return (float(info["x"]), float(info["y"]))
        return (float(xy[0]), float(xy[1]))

    def test_identical_positions(self):
        app, field = self._setup(chart_skip=(15,))
        roles = list(self.ca.COMP_ROLES[:3]) + ["check"]
        picks = [1, 15, 11, 20]                               # 15 is not in tonight's chart: projected, then centered
        expect = {}
        for role, k in zip(roles, picks):
            x, y, _v = field[k]
            rd = self._rd(x + 0.4, y - 0.3)
            app.comp_coords[f"SER {k}"] = rd
            getattr(app, f"{role}_name").set(f"SER {k}")
            getattr(app, f"{role}_mag").set("10.5")
            # 2.3.6: the labeled star within 5 arcsec (its chart position), else the projected position centered once
            start = next(((cx, cy) for cx, cy, st in app.chart_placed
                          if self.ca.core.sky_separation_arcsec(rd[0], rd[1], st["ra"], st["dec"]) < 5.0), None)
            start = start or app._project_to_frame(rd[0], rd[1])
            expect[role] = self._old_snap(start)
        self.assertEqual(app._auto_place_named_slots(), roles)
        for role in roles:
            self.assertEqual(getattr(app, f"{role}_xy"), expect[role], role)   # bit for bit
            self.assertEqual(getattr(app, f"{role}_mag").get(), "10.5")
        self.assertEqual(len(app.findings), 0)

    def test_click_on_a_chart_star_names_it_as_before(self):
        import types
        app, field = self._setup()
        x, y, v = field[12]
        app.pick_mode.set("comp")
        app.on_image_click(types.SimpleNamespace(xdata=x + 0.7, ydata=y - 0.4, button=1))
        c1 = self.ca.COMP_ROLES[0]
        self.assertEqual(getattr(app, f"{c1}_name").get(), "TYC 100-12-1")
        self.assertEqual(getattr(app, f"{c1}_mag").get(), f"{v:.3f}")


if __name__ == "__main__":
    unittest.main()
