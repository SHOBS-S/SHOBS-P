"""2.4.0 step 3 (#78): suggested comps never drift out of the safe edge margin during the night. The drift comes from
the loaded series' frame positions, else from a few lights read cheaply (core.frame_drift); a star that comes nearer
the edge than 3 x the sky-out radius on any frame checked is never suggested (no fallback lets it back in), and a
star the user picks that does so is named plainly in its pick-time note."""
import os
import sys
import tempfile
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photometry_core as core    # noqa: E402

try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False

try:
    from astropy.io import fits
except Exception:   # pragma: no cover
    fits = None

H, W = 300, 420
SKY_OUT = 14.0   # safe margin 3 x 14 = 42 px
# (x, y, amplitude, B-V, name) on the reference frame; star 0 is the target
FIELD = [(210, 150, 5000, 0.60, "T"),
         (80, 100, 5000, 0.60, "DRIFTER"),    # 80 px in on the reference, 80 - 60 = 20 px in by the last frame
         (330, 90, 5000, 0.60, "SAFE A"),
         (300, 220, 4200, 0.58, "SAFE B"),
         (150, 230, 6000, 0.62, "SAFE C"),
         ] + [(int(x), int(y), 900, None, "") for x, y in
              np.random.default_rng(3).uniform((60, 40), (W - 20, H - 40), (30, 2))]   # unlabeled field stars
DRIFT = [(0.0, 0.0), (-12.0, 2.0), (-24.0, 4.0), (-36.0, 6.0), (-48.0, 8.0), (-60.0, 10.0)]   # frame by frame


def star_image(stars, dx=0.0, dy=0.0, shape=(H, W), seed=0, hot=True):
    rng = np.random.default_rng(seed)
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    img = rng.normal(300.0, 5.0, (h, w))
    for x, y, a, *_ in stars:
        img += a * np.exp(-((xx - x - dx) ** 2 + (yy - y - dy) ** 2) / (2 * 1.8 ** 2))
    if hot:   # the same hot pixels on every frame: they must not vote for "no shift"
        hp = np.random.default_rng(99)
        img[hp.integers(5, h - 5, 120), hp.integers(5, w - 5, 120)] = 4000.0
    return img.astype(np.float32)


def write_night(folder, stars, drift, shape=(H, W)):
    paths = []
    for i, (dx, dy) in enumerate(drift, 1):
        p = os.path.join(folder, f"L_{i:04d}.fits")
        hdr = fits.Header()
        hdr["IMAGETYP"] = "Light Frame"
        hdr["EXPTIME"] = 60.0
        hdr["DATE-OBS"] = f"2026-10-24T06:{10 + i:02d}:00"
        fits.PrimaryHDU(star_image(stars, dx, dy, shape, seed=i), header=hdr).writeto(p, overwrite=True)
        paths.append(p)
    return paths


class TestDriftCore(unittest.TestCase):
    def test_sample_paths(self):
        lights = [f"f{i}" for i in range(300)]
        got = core.drift_sample_paths(lights, "f0")
        self.assertLessEqual(len(got), core.DRIFT_SAMPLES)
        self.assertEqual(got[0], "f1")
        self.assertEqual(got[-1], "f299")
        self.assertNotIn("f0", got)
        self.assertEqual(core.drift_sample_paths(["a", "b", "c"], "b"), ["a", "c"])
        # big frames (John's ASI2600MC, 300 lights): 8 read; small ones (MicroObservatory 650 x 500): every one
        self.assertEqual(core.drift_sample_count((4176, 6248), 300), core.DRIFT_SAMPLES)
        self.assertEqual(core.drift_sample_count((500, 650), 77), 77)
        self.assertEqual(core.drift_sample_count((2000, 3000), 300), 34)

    def test_edge_clearance(self):
        reg = {"dx": -60.0, "dy": 10.0, "angle": 0.0, "flipped": False}
        near, at = core.edge_clearance([(80, 100), (330, 90)], (H, W), [None, reg])
        self.assertAlmostEqual(near[0], 20.0)
        self.assertEqual(int(at[0]), 1)
        self.assertAlmostEqual(near[1], 89.0)   # 419 - 330 on the reference, 270 - ... the reference is closest
        self.assertEqual(int(at[1]), -1)
        flip = {"dx": 0.0, "dy": 0.0, "angle": 0.0, "flipped": True}
        near, _at = core.edge_clearance([(10, 150)], (H, W), [flip])
        self.assertAlmostEqual(near[0], 10.0)   # turned 180 deg: 10 px from the other side
        near, at = core.edge_clearance([(80, 100)], (H, W), [])   # no drift known: the reference frame alone
        self.assertAlmostEqual(near[0], 80.0)

    @unittest.skipIf(fits is None, "needs astropy")
    def test_frame_drift_known_shift_small_and_big_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_night(tmp, FIELD, DRIFT)
            res = core.frame_drift(paths[0], paths[1:])
            self.assertEqual(res["raw_shape"], (H, W))
            for (p, reg), (dx, dy) in zip(res["regs"], DRIFT[1:]):
                self.assertIsNotNone(reg, p)
                self.assertAlmostEqual(reg["dx"], dx, delta=1.0)
                self.assertAlmostEqual(reg["dy"], dy, delta=1.0)
        # a frame wider than DRIFT_VIEW_WIDTH is averaged down (k = 2 here) and the shift scaled back
        big = [(x * 3, y * 3, a, bv, n) for x, y, a, bv, n in FIELD]
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_night(tmp, big, [(0.0, 0.0), (-78.0, 21.0)], shape=(H * 3, W * 3))
            self.assertEqual(core.drift_view(np.zeros((H * 3, W * 3), np.float32))[1], 1)   # 1260 / 1200 -> 1
            old = core.DRIFT_VIEW_WIDTH
            core.DRIFT_VIEW_WIDTH = 600
            try:
                res = core.frame_drift(paths[0], paths[1:])
            finally:
                core.DRIFT_VIEW_WIDTH = old
            reg = res["regs"][0][1]
            self.assertAlmostEqual(reg["dx"], -78.0, delta=2.5)
            self.assertAlmostEqual(reg["dy"], 21.0, delta=2.5)
        self.assertEqual(core.scale_registration({"dx": 2.0, "dy": -1.0, "angle": 0.1, "flipped": False}, 0.5),
                         {"dx": 1.0, "dy": -0.5, "angle": 0.1, "flipped": False})


@unittest.skipUnless(HAVE_TK and fits is not None, "needs a display and astropy")
class TestSuggestionsFollowTheDrift(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test240f.log")
        cls.ca = ca
        cls.app = ca.App()
        cls.app.set_mode("variables")
        cls.app.update()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.paths = write_night(cls.tmp.name, FIELD, DRIFT)
        cls.lines = []
        orig = cls.app.log

        def log(text, *a, **k):
            cls.lines.append(text)
            return orig(text, *a, **k)
        cls.app.log = log

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        cls.tmp.cleanup()

    def _setup(self, field=FIELD, observations=None, lights=True):
        app = self.app
        app.light_dir.set(self.tmp.name if lights else "")   # (a new folder clears the marks: set first)
        app.rejected = set()
        app.pattern.set(core.MONO)
        app.preview = star_image(field, seed=1)
        app.preview_is_debayered = True
        app.preview_path = self.paths[0]
        app._preview_foreign = ""
        app.radius.set(5); app.sky_in.set(9); app.sky_out.set(SKY_OUT)
        app.sat_limit.set(60000)
        app.observations = list(observations or [])
        app.chart_placed = []
        for x, y, a, bv, name in field:
            if not name:
                continue
            st = {"label": name, "mag": 12.0, "band": "V", "catalog": "Tycho-2", "ra": 30.0, "dec": 0.0,
                  "mags": {"V": 12.0, "B": 12.0 + bv}}
            app.chart_placed.append((float(x), float(y), st))
        app.target_xy = (float(field[0][0]), float(field[0][1]))
        app._star_snr = lambda *_a, **_k: 500.0
        app._linear_limit = lambda: None
        self.lines.clear()
        return app

    def _wait_suggest(self, app, timeout=30.0):
        app._suggest_comps()
        t0 = time.time()
        while getattr(app, "_suggest_drift_pending", False) and time.time() - t0 < timeout:
            app.update()
            time.sleep(0.02)
        self.assertFalse(getattr(app, "_suggest_drift_pending", False), "the drift frames were never read")
        return [n for n, _dm, _x, _y in app.suggested_comps]

    def test_drifter_never_suggested_and_said_plainly(self):
        app = self._setup()
        app._drift_cache = None
        app._suggest_comps()
        self.assertTrue(app._suggest_drift_pending)   # the first time, the frames are read in the background
        self.assertEqual(app.suggested_comps, [])     # and nothing is suggested before the drift is known
        names = self._wait_suggest(app)
        self.assertNotIn("DRIFTER", names)
        self.assertEqual(set(names), {"SAFE A", "SAFE B", "SAFE C"})
        note = [t for t in self.lines if t.startswith("Only 3 comps suggested")]
        self.assertTrue(note, self.lines)
        self.assertIn("DRIFTER (", note[0])
        self.assertNotIn("⚠", note[0])   # fewer comps is a note, not a warning
        said = [t for t in self.lines if t.startswith("Suggested comps")][0]
        self.assertIn("All stay at least 42 px from the frame edge all night", said)
        self.assertIn("checked on all 6 lights", said)   # small frames: every light is looked at
        # the suggestions' closest approach over the night is kept for the S lines and check-star advice
        for n in names:
            self.assertGreaterEqual(app.suggested_info[n]["edge"], 42.0)
        # a second suggestion uses the cached drift at once (nothing read again)
        app._suggest_comps()
        self.assertFalse(app._suggest_drift_pending)
        self.assertEqual({n for n, *_ in app.suggested_comps}, set(names))

    def test_pick_time_note_names_the_drift(self):
        app = self._setup()
        self._wait_suggest(app)
        note = app._edge_note("comp", (80.0, 100.0))
        self.assertIn("drifts toward the frame edge later in the night", note)
        self.assertIn("80 px in on this frame, but only 20 px on frame", note)
        self.assertNotIn("checked on", note)   # every frame was looked at
        off = app._edge_note("comp", (45.0, 150.0))   # 45 - 60: off the frame by the last frame
        self.assertIn("drifts off the frame later in the night", off)
        self.assertIn("not measured there", off)
        self.assertEqual(app._edge_note("comp", (330.0, 90.0)), "")   # safe all night: no note

    def test_big_frames_are_sampled(self):
        old = (core.DRIFT_SAMPLES, core.DRIFT_PIXEL_BUDGET)
        core.DRIFT_SAMPLES, core.DRIFT_PIXEL_BUDGET = 3, 1   # as for big frames: first, middle and last only
        try:
            app = self._setup()
            app._drift_cache = None
            names = self._wait_suggest(app)
            self.assertNotIn("DRIFTER", names)   # the last frame (most drift) is always among those read
            said = [t for t in self.lines if t.startswith("Suggested comps")][0]
            self.assertIn("checked on 3 of the 6 lights", said)
            self.assertIn("(checked on 3 of the night's 6 lights)", app._edge_note("comp", (80.0, 100.0)))
        finally:
            core.DRIFT_SAMPLES, core.DRIFT_PIXEL_BUDGET = old
            self.app._drift_cache = None

    def test_no_fallback_lets_an_edge_drifter_back_in(self):
        field = [FIELD[0], (80, 100, 5000, 0.60, "DRIFTER"), (90, 200, 5000, 0.62, "DRIFTER 2")] + FIELD[5:]
        app = self._setup(field)
        names = self._wait_suggest(app)
        self.assertEqual(names, [])
        warn = [t for t in self.lines if t.startswith("⚠ No comp can be suggested")]
        self.assertTrue(warn, self.lines)
        self.assertIn("DRIFTER 2", warn[0])

    def test_loaded_series_positions_are_used_without_reading(self):
        obs = []
        for p, (dx, dy) in zip(self.paths, DRIFT):
            o = core.Obs(jd=2461337.5, mag=0.0, merr=0.01, cmag=0.0, kmag=None, airmass=None, path=p, exptime=60.0,
                         flux_var=1.0, flux_comp=1.0)
            o.pos = {"t": [210.0 + dx, 150.0 + dy]}
            o.night = "2026-10-24"
            obs.append(o)
        app = self._setup(observations=obs)
        app.night_bin = {"2026-10-24": {"binning": 1}}   # Fable N4: measured at the page's binning
        app._drift_cache = None
        app._suggest_comps()
        self.assertFalse(app._suggest_drift_pending)   # nothing to read: the series' own frame shifts
        self.assertIsNone(app._drift_cache)
        names = [n for n, *_ in app.suggested_comps]
        self.assertNotIn("DRIFTER", names)
        said = [t for t in self.lines if t.startswith("Suggested comps")][0]
        self.assertIn("from the loaded series' 6 frames", said)
        note = app._edge_note("comp", (80.0, 100.0))
        self.assertIn("only 20 px on frame", note)
        self.assertNotIn("checked on", note)

    def test_single_frame_keeps_the_reference_frame_rule(self):
        app = self._setup(lights=False)
        names = self._wait_suggest(app)
        self.assertIn("DRIFTER", names, self.lines)   # 80 px in on the only frame there is: fine
        said = [t for t in self.lines if t.startswith("Suggested comps")][0]
        self.assertNotIn("all night", said)


if __name__ == "__main__":
    unittest.main()
