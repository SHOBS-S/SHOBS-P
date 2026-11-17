"""2.4.0 step 3: Fable's re-review fixes. N2 the whiteness guard's words (faint stars that moved, resampled frames);
N3 a series saved with MicroObservatory's binned pixel (13.6 um) re-opened over current-header frames (XBINNING 2);
N4 no stray "Photon noise" pattern in message_help.json; the gain nits (stack wording, no "inf", the most common
STACKN, STACKSCL, no fall-through to the stacks on a physical failure, the difference worked out once); the drift
nits (suggestions tied to their target and frame, the pixel grid); the same-name rows of chapter 23; the report's
camera binning by the solve's rule."""
import glob
import json
import math
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import findings                   # noqa: E402
import photometry_core as core    # noqa: E402
import sky_gain as sg             # noqa: E402
from tests import test_240_h as H  # noqa: E402
from tests.test_240_g import WASP50, _Box  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    import tkinter
    tkinter.Tk().destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


def _read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as fh:
        return fh.read()


# ---- N2 -------------------------------------------------------------------------------------------------------------
class TestWhitenessWords(unittest.TestCase):
    def test_words_name_faint_stars_and_resampling(self):
        w = sg.structure_words(3)
        self.assertIn("faint stars that moved between the frames", w)
        self.assertNotIn("resampled", w)
        self.assertIn("resampled (aligned or registered by other software", sg.structure_words(9, most=True))

    def test_resampled_frames_are_all_dropped_and_said(self):
        # frames another program registered: each shifted by a fraction of a pixel with interpolation, so
        # neighbouring pixels are alike and the whiteness ratio is well over 1.05 on a clear night
        from scipy.ndimage import shift
        rng = np.random.default_rng(3)
        frames = {}
        for i in range(8):
            img = rng.normal(900.0, 15.0, (320, 320))
            frames[f"f{i}"] = shift(img, (0.37 * (i % 3), 0.41), order=3, mode="nearest").astype(np.float32)
        m = sg.measure_night(list(frames), frames.__getitem__, sat=60000, floor_var=4.0, floor_source="darks")
        self.assertFalse(m["ok"])
        self.assertGreater(m["dropped_structure"], m["n_pairs"])
        self.assertIn("With most pairs dropped, the frames may have been resampled", m["why"])
        self.assertEqual(m["pairs_read"], m["pairs_tried"])

    def test_summary_says_most_only_when_most(self):
        meas = {"ok": True, "k": 2.0, "k_lo": 1.9, "k_hi": 2.1, "n_pairs": 5, "floor_var": 4.0,
                "floor_source": "darks", "dropped_structure": 2}
        self.assertNotIn("resampled", sg.summary(meas))
        self.assertIn("resampled", sg.summary(dict(meas, dropped_structure=9)))


# ---- gain nits ------------------------------------------------------------------------------------------------------
class TestGainNits(unittest.TestCase):
    def test_pair_measure_is_pair_stats_and_whiteness(self):
        rng = np.random.default_rng(5)
        a, b = (sg.prep_frame(rng.normal(400.0, 10.0, (240, 260)).astype(np.float32)) for _ in range(2))
        s, v, r = sg.pair_measure(a, b)
        self.assertEqual((s, v), sg.pair_stats(a, b))
        self.assertEqual(r, sg.whiteness(a, b))
        b2 = dict(b, level=b["level"] * 1.5)   # a jump in the sky level: unusable, no ratio worked out
        self.assertTrue(all(math.isnan(x) for x in sg.pair_measure(a, b2)[1:]))

    def test_no_upper_bound_is_said_not_inf(self):
        S = np.full(10, 1000.0)
        v = np.full(10, 30.0)
        m = sg.night_gain(S, v, floor_var=10.0, floor_source="darks", floor_err=25.0)
        self.assertTrue(m["ok"])
        self.assertTrue(math.isinf(m["k_hi"]))
        text = sg.summary(m)
        self.assertNotIn("inf", text)
        self.assertIn(f"{m['k_lo']:.3g} or more (no upper bound)", text)
        import transit_ui as tu
        words = tu.sky_meas_words({"sky_meas": core._sky_meas_brief(m)})
        self.assertIn("or more (no upper bound)", words)
        self.assertNotIn("inf", words)
        self.assertEqual(sg.range_words(1.0, 2.0), "1-2")

    def test_stack_floor_is_said_once_as_the_stacks(self):
        meas = {"ok": True, "k": 8.0, "k_lo": 7.8, "k_hi": 8.2, "n_pairs": 6, "floor_var": 480.0,
                "floor_source": "darks", "rn_e": 11.0, "stack_n": 4, "basis": "stacks"}
        text = sg.summary(meas)
        self.assertIn("read + dark noise 21.9 units per stack of 4 frames (from the darks, added up over the 4 "
                      "frames), about 11 e- per camera pixel in a single frame", text)
        self.assertNotIn("x 4 for the stacks", text)
        import transit_ui as tu
        self.assertIn("21.9 units per stack of 4 frames", tu.sky_meas_words({"sky_meas": core._sky_meas_brief(meas)}))
        plain = sg.summary(dict(meas, stack_n=0, basis=""))
        self.assertIn("read + dark noise 21.9 units from the darks, about 11 e- per camera pixel)", plain)


class TestStacksRouting(unittest.TestCase):
    """Re-review nits: the frames fail for a physical reason -> no second try on the stacks; the frames could not be
    read -> the stacks are tried."""

    def _run(self, frames_meas):
        import clearv_app
        calls = []

        def measure(lights, *a, **k):
            calls.append(lights)
            return dict(frames_meas) if len(calls) == 1 else {"ok": False, "why": "stacks reason", "pairs_read": 6}
        fake = H._fake_app()
        fake.preview = None
        fake._sky_gain_measure = measure
        fake._flux_gain_check = lambda *a, **k: ""
        with mock.patch("photometry_core.sky_electrons_estimate", return_value=(5.0, 400.0)), \
                mock.patch("photometry_core.read_header", return_value={}):
            s = clearv_app.App._photon_noise_setup(fake, ["S1", "S2"], 1, core.MONO, "luminance", core.Masters(dark=1),
                                                   False, [], 4.0, 8.0, 13.0, log=lambda t: None, stacks=True,
                                                   stack_frames=["F1", "F2", "F3"])
        return s, calls

    def test_physical_failure_is_not_retried_on_the_stacks(self):
        s, calls = self._run({"ok": False, "why": "the sky is too faint against the read + dark noise",
                              "pairs_read": 12})
        self.assertEqual(len(calls), 1)
        self.assertEqual(s["sky_method"], "lower limit")
        self.assertIn("It could not be measured from the night's sky: on the single frames that went into the "
                      "stacks, the sky is too faint", s["note"])

    def test_unreadable_frames_fall_through(self):
        s, calls = self._run({"ok": False, "why": "only 0 usable frame pairs", "pairs_read": 0})
        self.assertEqual(len(calls), 2)
        self.assertIn("not on the single frames (only 0 usable frame pairs), nor on the stacks (stacks reason)",
                      s["note"])


class TestStackN(unittest.TestCase):
    """Re-review nits: N is the most common STACKN (the first stack may be partial); the floor x N only for stacks
    whose STACKSCL says SUM."""
    _night = H.TestStackedRunUsesTheFramesGain._night

    def setUp(self):
        self._mono = core._SENSOR["mono"]
        core.set_mono(True)

    def tearDown(self):
        core.set_mono(self._mono)

    def _measure(self, dd, stacks, masters):
        import clearv_app
        fake = H._fake_app(dark_dir=dd)
        return clearv_app.App._sky_gain_measure(fake, stacks, 2, core.MONO, "luminance", masters, False, True,
                                                core.read_header(stacks[0]))

    def test_partial_first_stack_and_stackscl(self):
        import stack_core
        from astropy.io import fits
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(stack_core, "STACK_CACHE_DIR", os.path.join(d, "stacks")):
            _frames, stacks, masters, dd = self._night(d)
            fits.setval(stacks[0], "STACKN", value=3)   # a partial stack first
            m = self._measure(dd, stacks, masters)
            self.assertEqual(m.get("stack_n"), 4)
            self.assertEqual(m["floor_source"], "darks")
            self.assertTrue(m["ok"], m.get("why"))
            for p in stacks:
                fits.setval(p, "STACKSCL", value="MEAN")
            m2 = self._measure(dd, stacks, masters)
            self.assertNotIn("stack_n", m2)
            self.assertNotEqual(m2["floor_source"], "darks")
            if not m2["ok"]:
                self.assertIn("STACKSCL MEAN", m2["why"])


# ---- N4 and the chapter 23 rows ------------------------------------------------------------------------------------
class TestMessageHelp(unittest.TestCase):
    def setUp(self):
        with open(findings.HELP_FILE, encoding="utf-8") as fh:
            self.rows = json.load(fh)["rows"]
        self.pats = [p for r in self.rows for p in r["patterns"]]

    def test_no_stray_photon_noise_pattern(self):
        self.assertNotIn("Photon noise", self.pats)
        sys.path.insert(0, os.path.join(HERE, "tools"))
        import build_message_help
        self.assertEqual(build_message_help.build()["rows"], self.rows)   # rebuilt from chapter 23

    def _hint(self, text):
        return findings.match_hint(text, self.rows)

    def test_same_name_variants_have_help(self):
        named = ("Comp 3 and the check star have the same name (Comp 3: X1, but the markers are 25 px apart on the "
                 "image). They look like two different stars, but the results would not tell them apart. Type "
                 "another name in one of the boxes. Run photometry waits until then.")
        same = ("Comp 2 and the check star are the same star (Comp 2: 0.3″ apart). The check star tests the "
                "comps, so it can't also be one of them. Change one of them: clear the marker and pick another star.")
        self.assertIn("Type another name", self._hint("⚠ " + named))
        self.assertIn("Clear one of the two", self._hint("⚠ " + same + " Run photometry waits until then."))
        both = self._hint("⚠ " + same + " " + named)
        self.assertIn("same star", both)
        self.assertIn("only shares the name", both)

    def test_manual_words(self):
        ch23 = " ".join(_read("docs", "manual", "23-messages-explained.md").split())
        ch8 = " ".join(_read("docs", "manual", "08-photometry-basics.md").split())
        ch5 = " ".join(_read("docs", "manual", "05-input.md").split())
        self.assertIn("This check is then less certain.", ch23)
        self.assertIn("Pixel size set to 6.8 µm, the MicroObservatory camera's own pixel.", ch23)
        self.assertIn("units per stack of 4 frames (from the darks, added up over the 4 frames)", ch8)
        self.assertNotIn("a second or two on small frames", ch8)
        self.assertIn("GitHub #108", ch5)
        self.assertIn("about 14 % larger", ch5)
        readme = " ".join(_read("README.txt").split())
        self.assertIn("MicroObservatory elevation from the header [108]", readme)
        self.assertIn("HEIGHT, #108)", _read("docs", "manual", "_reshoots_240_d.md"))


# ---- WASP-50 unchanged -------------------------------------------------------------------------------------------
@unittest.skipUnless(os.path.isdir(os.path.join(WASP50, "Lights")), "WASP-50 test night not on this machine")
class TestWasp50Unchanged(unittest.TestCase):
    def test_gain_as_before(self):
        import clearv_app
        lights = sorted(glob.glob(os.path.join(WASP50, "Lights", "*")))
        darks = sorted(glob.glob(os.path.join(WASP50, "Darks", "*.FITS")))
        master = core.Masters(dark=np.median([core.read_fits(p)[0] for p in darks], axis=0).astype(np.float32),
                              dark_exptime=60.0)
        fake = types.SimpleNamespace(dark_dir=_Box(os.path.join(WASP50, "Darks")), bias_dir=_Box(""),
                                     sat_limit=_Box(4095.0), _linear_limit=lambda: None)
        fake._sky_gain_inputs = lambda: clearv_app.App._sky_gain_inputs(fake)
        m = clearv_app.App._sky_gain_measure(fake, lights, 1, core.MONO, "luminance", master, False, False,
                                             core.read_header(lights[0]))
        self.assertAlmostEqual(m["k"], 39.2667385, places=5)   # a8a5e6a: 39.266738513512664
        self.assertEqual((m["n_pairs"], m["dropped_structure"], m["pairs_read"]), (24, 0, 24))


# ---- N3, the report's camera binning, the drift nits ---------------------------------------------------------------
@unittest.skipUnless(HAVE_TK, "needs a display")
class TestApp(unittest.TestCase):
    MOBS = {"TELESCOP": "Cecilia", "OBSERVAT": "Whipple Observatory", "LATITUDE": 31.68, "LONGITUD": 110.88,
            "XBINNING": 2, "YBINNING": 2, "XPIXSZ": 13.6, "FOCALLEN": 560, "EXPTIME": 60.0,
            "IMAGETYP": "Light Frame"}

    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        cls.tmp = tempfile.mkdtemp(prefix="shobs_240_k_")
        ca.LOG_FILE = os.path.join(cls.tmp, "log.txt")
        cls.app = ca.App()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.lines = []
        self._log = mock.patch.object(self.app, "log", side_effect=lambda t, *a, **k: self.lines.append(t))
        self._log.start()

    def tearDown(self):
        self._log.stop()

    def _lights(self, header, name):
        from astropy.io import fits
        folder = os.path.join(self.tmp, name)
        os.makedirs(folder, exist_ok=True)
        for i in range(3):
            h = fits.Header()
            for k, v in dict(header, **{"DATE-OBS": f"2024-01-01T03:0{i}:00"}).items():
                h[k] = v
            fits.writeto(os.path.join(folder, f"MOBS_{i}.fits"), np.full((50, 65), 500, np.float32), h,
                         overwrite=True)
        self.app.light_dir.set(folder)
        self.app.preview_header = None
        self.app.focal_mm.set(560.0)
        self.app.pixel_um.set(6.8)
        self.app.binning.set(1)
        self.app.pattern.set(core.MONO)

    def _load(self, pixel):
        with mock.patch.object(self.ca.messagebox, "askyesno", return_value=True):
            self.app._apply_meta_values({"pixel_um": pixel, "focal_mm": 560.0})
        return float(self.app.pixel_um.get())

    def test_n3_old_series_over_current_mobs_frames(self):
        self._lights(self.MOBS, "mobs_now")
        self.assertAlmostEqual(self._load(13.6), 6.8, places=2)
        said = [t for t in self.lines if t.startswith("Pixel size set to 6.8 µm")]
        self.assertEqual(len(said), 1, self.lines)
        self.assertIn("plate scale stays about 5.0\"/px", said[0])
        head = core.read_header(self.app._lights()[0])
        scale = 6.8 / 560 * 206.265 * self.ca.solve_camera_bin(head)
        self.assertAlmostEqual(scale, 5.0, delta=0.05)
        self.assertAlmostEqual(self.app.exofop_equipment()["scale"], scale, places=6)   # the report's scale too
        # the reference frame's header counts too (no lights folder needed)
        self.app.light_dir.set(os.path.join(self.tmp, "none"))
        self.app.preview_header = head
        self.assertAlmostEqual(self._load(13.6), 6.8, places=2)

    def test_n3_left_alone_otherwise(self):
        old = {k: v for k, v in self.MOBS.items() if k not in ("XBINNING", "YBINNING")}
        self._lights(old, "mobs_old")   # older headers name no binning: 13.6 is right for them
        self.assertAlmostEqual(self._load(13.6), 13.6, places=2)
        self._lights(self.MOBS, "mobs_typed")
        self.assertAlmostEqual(self._load(9.0), 9.0, places=2)   # not the binned pixel: the series' own value
        own = {"TELESCOP": "RC14", "XBINNING": 2, "XPIXSZ": 13.6, "FOCALLEN": 2000, "EXPTIME": 60.0,
               "IMAGETYP": "Light Frame"}
        self._lights(own, "own")   # not MicroObservatory frames
        self.assertAlmostEqual(self._load(13.6), 13.6, places=2)
        self.assertFalse([t for t in self.lines if t.startswith("Pixel size set to")])

    def test_report_camera_bin_is_the_solves(self):
        own = {"TELESCOP": "RC14", "BINNING": 2, "XPIXSZ": 3.76, "FOCALLEN": 2000, "EXPTIME": 60.0,
               "IMAGETYP": "Light Frame"}
        self._lights(own, "binning_key")   # no XBINNING: the solve counts 1, so does the report now
        self.assertEqual(self.app.exofop_equipment()["camera_bin"], 1)
        self.assertEqual(self.ca.solve_camera_bin(own), 1)

    def test_drift_usable_needs_the_same_grid(self):
        app = self.app
        lights = [f"/x/L{i}.fits" for i in range(4)]
        obs = []
        for p in lights:
            o = types.SimpleNamespace(path=p, night="2026-10-24")
            obs.append(o)
        app.binning.set(1)
        app.masters_binning = 1
        app.pattern.set(core.MONO)
        app.night_bin = {"2026-10-24": {"binning": 1, "pattern": core.MONO, "debayer": "luminance"}}
        self.assertTrue(app._series_drift_usable(obs, lights))
        app.night_bin = {"2026-10-24": {"binning": 1, "pattern": "RGGB", "debayer": "luminance"}}
        self.assertFalse(app._series_drift_usable(obs, lights))   # superpixel grid: half the mono grid
        app.night_bin = {"2026-10-24": {"binning": 1}}
        self.assertFalse(app._series_drift_usable(obs, lights))   # not recorded: the lights are read
        app.pattern.set("RGGB")
        app.night_bin = {"2026-10-24": {"binning": 1, "pattern": "GRBG", "debayer": "green"}}
        self.assertTrue(app._series_drift_usable(obs, lights))    # another color pattern: the same grid
        setup = app._night_setup()
        self.assertEqual((setup["pattern"], setup["debayer"]), ("RGGB", app.debayer_mode.get()))
        app.pattern.set(core.MONO)

    def test_kept_suggestions_belong_to_this_target_and_frame(self):
        app = self.app
        keep = (app.target_xy, app.chart_placed, app.preview_path, app.suggested_comps)
        try:
            if app.mode_key == "discovery":
                self.skipTest("discovery mode")
            app.target_xy = (100.0, 100.0)
            app.chart_placed = [(150.0, 150.0, {"mag": 10.0})]
            app.preview_path = "/x/ref.fits"
            with mock.patch.object(app, "_inst_at", return_value=10.0), \
                    mock.patch.object(app, "_series_drift", return_value=None):
                app.suggested_comps = [("S1", 0.1, 1.0, 2.0)]
                app._suggest_for = ((100.0, 100.0), "/x/ref.fits")
                app._suggest_comps()
                self.assertEqual(app.suggested_comps, [("S1", 0.1, 1.0, 2.0)])   # the same target and frame: kept
                app.preview_path = "/x/other.fits"
                app._suggest_comps()
                self.assertEqual(app.suggested_comps, [])   # another frame: not this frame's suggestions
        finally:
            app.target_xy, app.chart_placed, app.preview_path, app.suggested_comps = keep


if __name__ == "__main__":
    unittest.main()
