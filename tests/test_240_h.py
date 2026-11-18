"""2.4.0 step 3 (#104), Fable review fixes for the gain from the night's sky (sky_gain.py and _sky_gain_measure):
S1 stacks are read as they are (Bin 2 stacks recover the truth), N1 a pair counts only when its sky variance is at
least twice the floor, and the floor's own error widens the range, N5 the photon-transfer floor's gain error is the
range and the log says +/-, N6 frames read whole get at most 12 pairs and the log says the measurement started; the
nits: the cache keeps the masters object, prepared frames are not left behind, a sky near saturation is masked, and a
measured gain under the one-frame lower limit gets a plain note."""
import math
import os
import sys
import tempfile
import types
import unittest
import weakref
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photometry_core as core    # noqa: E402
import sky_gain as sg             # noqa: E402
from tests.test_240_g import _Box, synth_night   # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fake_app(dark_dir="", bias_dir="", sat=60000.0):
    import clearv_app
    fake = types.SimpleNamespace(dark_dir=_Box(dark_dir), bias_dir=_Box(bias_dir), sat_limit=_Box(sat),
                                 _linear_limit=lambda: None)
    fake._sky_gain_inputs = lambda: clearv_app.App._sky_gain_inputs(fake)
    return fake


def _write_stacks(folder, lights, master, n=4, b=2):
    """Stack files like the Stack step's: n consecutive frames, calibrated (master dark) and binned b x b (mono:
    block mean), summed (the stack's sum scale), with the stack header."""
    import stack_core
    paths = []
    groups = [lights[i:i + n] for i in range(0, len(lights) - n + 1, n)]
    for k, grp in enumerate(groups):
        img = np.sum([core.bin_plain(f - master, b) for f in grp], axis=0).astype(np.float32)
        members = [{"exp": 60.0} for _ in grp]
        hdr = stack_core.stack_header({"EXPTIME": 60.0}, members, {"plain": True}, [2460950.6 + k * 0.003] * n,
                                      k + 1, len(groups), False)
        p = os.path.join(folder, f"stack_{k + 1:04d}.fits")
        stack_core.write_stack(p, img, hdr)
        paths.append(p)
    return paths


class TestStacksS1(unittest.TestCase):
    def test_bin2_stacks_known_floor_recover_truth(self):
        # mono camera g = 2 e-/ADU at Bin 2 (block mean): 8 electrons per image unit, on frames and on sum-scale stacks
        import stack_core
        lights, darks, _b = synth_night(n=40, g=2.0, rn_e=10.0, dark_e=20.0, shape=(360, 420), sky=(800.0, 900.0),
                                        seed=41)
        master = (0.5 * (darks[0] + darks[1])).astype(np.float32)
        b, n = 2, 4
        fl1 = sg.floor_from_pair(core.bin_plain(darks[0], b), core.bin_plain(darks[1], b))
        with tempfile.TemporaryDirectory() as d:
            paths = _write_stacks(d, lights, master, n, b)

            def full(p):
                return stack_core.read_stack(p)[0]
            load = sg.window_loader(b, core.Masters(), core.MONO, "luminance", full_load=full, whole=lambda p: True)
            m = sg.measure_night(paths, load, floor_var=n * fl1, floor_source="darks")
            self.assertTrue(m["ok"], m.get("why"))
            self.assertLess(abs(m["k"] / 8.0 - 1), 0.06, m["k"])
            # before the fix the stack went through the window path: binned again (4x less variance at the same
            # level: about 4x too many electrons)
            old = sg.measure_night(paths, sg.window_loader(b, core.Masters(), core.MONO, "luminance"),
                                   floor_var=n * fl1 / 4, floor_source="darks")
            self.assertTrue(old["ok"])
            self.assertGreater(old["k"], 2.5 * 8.0)

    def test_app_stack_run_bin2(self):
        import clearv_app
        import stack_core
        # twilight: the sky changes 4x, so the photon-transfer line gives the floor (stacks never take the darks')
        lights, darks, _b = synth_night(n=48, g=2.0, rn_e=10.0, dark_e=0.0, shape=(360, 420), sky=(300.0, 1500.0),
                                        seed=43)
        master = (0.5 * (darks[0] + darks[1])).astype(np.float32)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(stack_core, "STACK_CACHE_DIR", d):
            sub = os.path.join(d, "night1")
            os.makedirs(sub)
            paths = _write_stacks(sub, lights, master, 4, 2)
            import stack_ui
            self.assertTrue(all(stack_ui.is_stack_path(p) for p in paths))
            hdr = core.read_header(paths[0])
            logs = []
            masters = core.Masters(dark=core.bin_plain(master, 2), dark_exptime=60.0)
            with mock.patch.object(sg, "measure_night", wraps=sg.measure_night) as mn:
                m = clearv_app.App._sky_gain_measure(_fake_app(), paths, 2, core.MONO, "luminance", masters, False,
                                                     True, hdr, log=logs.append)
            self.assertIsNone(mn.call_args.kwargs["sat"])            # a stack is a sum: no saturation cut
            self.assertLessEqual(mn.call_args.kwargs["max_pairs"], 12)   # read whole
            self.assertTrue(m["ok"], m.get("why"))
            self.assertEqual(m["floor_source"], "ptc")
            self.assertLess(abs(m["k"] / 8.0 - 1), 0.05, (m["k"], m["k_lo"], m["k_hi"]))   # 31.4 before
            self.assertEqual(logs, ["Measuring the gain from the night's sky (11 frame pairs)…"])


class TestFloorN1(unittest.TestCase):
    def test_sky_variance_under_twice_the_floor_falls_back(self):
        # uncooled camera, faint sky, darks a little warmer than the lights: true floor 100, the darks say 130
        S = np.linspace(55.0, 65.0, 12)
        v = S / 1.0 + 100.0
        m = sg.night_gain(S, v, 130.0, "darks")
        self.assertFalse(m["ok"])   # before: k about 2 (truth 1.0), passed the old 1.05 x floor guard
        self.assertIn("too faint against the read + dark noise", m["why"])
        self.assertIn("in only 0 of 12 frame pairs", m["why"])
        s = core.photon_noise_setup({}, 1, core.MONO, "luminance", None, True, False, 0.4, 60.0, sky_meas=m)
        self.assertEqual(s["sky_method"], "lower limit")
        self.assertIn("It could not be measured from the night's sky: the sky is too faint", s["note"])

    def test_twice_the_floor_still_measured(self):
        S = np.linspace(200.0, 220.0, 10)
        m = sg.night_gain(S, S / 2.0 + 90.0, 90.0, "darks")   # v >= 2 x floor in every pair
        self.assertTrue(m["ok"], m["why"])
        self.assertAlmostEqual(m["k"], 2.0, places=6)

    def test_floor_error_widens_the_range(self):
        rng = np.random.default_rng(3)
        S = np.linspace(400.0, 420.0, 16)
        v = (S / 2.0 + 30.0) * (1 + 0.004 * rng.standard_normal(S.size))
        a = sg.night_gain(S, v, 30.0, "darks")
        b = sg.night_gain(S, v, 30.0, "darks", floor_err=3.0)
        self.assertEqual(a["k"], b["k"])
        self.assertLess(b["k_lo"], a["k_lo"])
        self.assertGreater(b["k_hi"], a["k_hi"])
        self.assertAlmostEqual(b["floor_err"], 3.0)
        # 10 % on a floor 30 against a sky variance ~200: the gain moves by about 1.5 %
        self.assertAlmostEqual(b["k_hi"] / b["k"] - 1, 3.0 / (v.mean() - 30.0), delta=0.008)

    def test_floor_from_darks_carries_its_error(self):
        rng = np.random.default_rng(5)
        d1, d2 = (rng.normal(500, 5.0, (300, 300)) for _ in range(2))
        fl, err = sg.floor_from_pair(d1, d2, with_err=True)
        self.assertAlmostEqual(fl, 25.0, delta=1.0)
        self.assertAlmostEqual(err / fl, math.sqrt(2.0 / (292 * 292)), delta=1e-4)
        f, e = sg.floor_combine([25.0, 27.0, 26.0], [0.1, 0.1, 0.1])
        self.assertEqual(f, 26.0)
        self.assertAlmostEqual(e, 1.0 / math.sqrt(3))   # their scatter, larger than the pixels' own error
        self.assertEqual(sg.floor_combine([float("nan")]), (None, 0.0))


class TestPtcN5(unittest.TestCase):
    def test_photon_transfer_floor_uses_the_line_error(self):
        lights, darks, _b = synth_night(n=24, g=2.0, rn_e=12.0, sky=(300.0, 1500.0), seed=5)
        master = 0.5 * (darks[0] + darks[1])
        frames = {f"f{i:03d}": (img - master).astype(np.float32) for i, img in enumerate(lights)}
        m = sg.measure_night(list(frames), frames.__getitem__)
        self.assertTrue(m["ok"], m.get("why"))
        self.assertEqual(m["floor_source"], "ptc")
        self.assertAlmostEqual(m["k_err"], m["ptc"]["g_err"])
        self.assertLessEqual(m["k_lo"], m["k"] - m["k_err"] + 1e-9)
        self.assertGreaterEqual(m["k_hi"], m["k"] + m["k_err"] - 1e-9)
        text = sg.summary(m)
        self.assertIn(f"{m['k']:.3g} +/- {m['k_err']:.2g} electrons per image unit (from", text)
        self.assertIn("from the photon-transfer line", text)
        import transit_ui as tu
        brief = core._sky_meas_brief(m)
        self.assertIn("+/-", tu.sky_meas_words({"sky_meas": brief}))
        # darks / bias floors keep the range words
        d = sg.night_gain(np.linspace(400, 420, 8), np.linspace(400, 420, 8) / 2 + 30, 30.0, "darks")
        self.assertNotIn("+/-", sg.summary(d))

    def test_egain_check_uses_the_wider_range(self):
        meas = {"ok": True, "k": 1.7, "k_lo": 1.3, "k_hi": 2.1, "k_err": 0.4, "n_pairs": 20, "floor_var": 4.0,
                "floor_source": "ptc", "ptc": {"ok": True, "g": 1.7, "g_err": 0.4, "range": 4.0}}
        s = core.photon_noise_setup({"EGAIN": 1.0}, 1, core.MONO, "luminance", None, True, False, 0.9, 500.0,
                                    sky_meas=meas)
        self.assertEqual(s["warning"], "")   # 1.7 x EGAIN, but the line's error reaches under 1.5 x


class TestWholeFramesN6(unittest.TestCase):
    def test_plain_fits_2d(self):
        from astropy.io import fits
        with tempfile.TemporaryDirectory() as d:
            plain, comp, ext = (os.path.join(d, n) for n in ("a.fits", "b.fits", "c.fits"))
            img = np.zeros((40, 50), np.float32)
            fits.writeto(plain, img)
            fits.HDUList([fits.PrimaryHDU(), fits.CompImageHDU(img)]).writeto(comp)
            fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(img)]).writeto(ext)
            self.assertTrue(sg.plain_fits_2d(plain))
            self.assertFalse(sg.plain_fits_2d(comp))
            self.assertFalse(sg.plain_fits_2d(ext))
            self.assertFalse(sg.plain_fits_2d(os.path.join(d, "x.xisf")))

    def test_compressed_frames_get_at_most_12_pairs_and_a_log_line(self):
        import clearv_app
        from astropy.io import fits
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i in range(30):
                p = os.path.join(d, f"L{i:03d}.fits")
                fits.HDUList([fits.PrimaryHDU(), fits.CompImageHDU(np.zeros((40, 50), np.float32))]).writeto(p)
                paths.append(p)
            logs = []
            with mock.patch.object(sg, "measure_night", return_value={"ok": False, "why": "x"}) as mn:
                clearv_app.App._sky_gain_measure(_fake_app(), paths, 1, core.MONO, "luminance", core.Masters(),
                                                 False, False, {"EXPTIME": 60.0}, log=logs.append)
            self.assertEqual(mn.call_args.kwargs["max_pairs"], 12)   # 24 before: the header had no NAXIS1/2
            self.assertEqual(logs, ["Measuring the gain from the night's sky (12 frame pairs)…"])


class TestNits(unittest.TestCase):
    def test_cache_keeps_the_masters_object(self):
        import clearv_app
        fake = _fake_app()
        paths = [f"/nowhere/L{i}.fits" for i in range(6)]
        m1 = core.Masters(dark=np.zeros((4, 4), np.float32))
        with mock.patch.object(sg, "measure_night", side_effect=lambda *a, **k: {"ok": False}) as mn:
            first = clearv_app.App._sky_gain_measure(fake, paths, 1, core.MONO, "luminance", m1, False, False, {})
            again = clearv_app.App._sky_gain_measure(fake, paths, 1, core.MONO, "luminance", m1, False, False, {})
            self.assertIs(again, first)
            m2 = core.Masters(dark=np.zeros((4, 4), np.float32))   # equal, but rebuilt masters
            clearv_app.App._sky_gain_measure(fake, paths, 1, core.MONO, "luminance", m2, False, False, {})
            self.assertEqual(mn.call_count, 2)
        self.assertIs(fake._sky_gain_cache[1], m2)

    def test_prepared_frames_are_not_left_behind(self):
        class Prep(dict):
            pass
        alive = []
        peak = [0]
        rng = np.random.default_rng(1)

        def fake_prep(img, sat=None):
            p = Prep(resid=rng.normal(0, 10.0, (40, 40)).astype(np.float32), level=100.0, mask=None, masked=0.0)
            alive.append(weakref.ref(p))
            return p

        def load(path):
            peak[0] = max(peak[0], sum(1 for r in alive if r() is not None))
            return None
        with mock.patch.object(sg, "prep_frame", side_effect=fake_prep):
            m = sg.measure_night([f"f{i}" for i in range(100)], load, floor_var=0.0, floor_source="darks")
        self.assertEqual(m["pairs_tried"], 24)
        self.assertLessEqual(peak[0], 3)   # 24 before the fix (every pair's second frame was kept)

    def test_sky_near_saturation_is_masked(self):
        # a gradient sky (g = 1: variance = level) running up to the saturation level on the right; the camera clips
        rng = np.random.default_rng(8)
        h, w, sat = 300, 400, 1100.0
        level = np.tile(np.linspace(500.0, 1100.0, w), (h, 1))
        fr = [np.minimum(rng.poisson(level).astype(np.float32), sat) for _ in range(2)]
        a, b = sg.prep_frame(fr[0], sat), sg.prep_frame(fr[1], sat)
        S, v = sg.pair_stats(a, b)
        right = a["mask"][:, -40:].mean()
        self.assertGreater(right, 0.95)                 # the sky within 5 sigma of saturation is masked
        self.assertLess(a["mask"][:, 10:200].mean(), 0.1)
        good = ~(a["mask"] | b["mask"])
        truth = float(np.median(level[good]))
        self.assertLess(abs(S / v / (truth / float(level[good].mean())) - 1), 0.03, (S, v))
        # all of the sky near saturation: no usable pair (not a gain biased high)
        sat2 = 1050.0 + 4.5 * math.sqrt(1050.0)   # the sky 4.5 sigma under saturation
        flat = [np.minimum(rng.poisson(np.full((200, 200), 1050.0)).astype(np.float32), sat2) for _ in range(2)]
        S2, v2 = sg.pair_stats(sg.prep_frame(flat[0], sat2), sg.prep_frame(flat[1], sat2))
        self.assertFalse(math.isfinite(S2) and math.isfinite(v2))

    def test_measured_under_the_lower_limit_gets_a_note(self):
        meas = {"ok": True, "k": 30.0, "k_lo": 29.0, "k_hi": 31.0, "n_pairs": 20, "floor_var": 1.0,
                "floor_source": "darks", "ptc": {}}
        s = core.photon_noise_setup({}, 1, core.MONO, "luminance", None, True, False, 40.0, 300.0, sky_meas=meas)
        self.assertIn("Note: this is under the one-frame lower limit (40), which should not happen", s["note"])
        self.assertIn("on the safe (large) side", s["note"])
        self.assertEqual(s["electrons"], 30.0)   # still used: the error bars are on the safe side
        eg = core.photon_noise_setup({"EGAIN": 1.0}, 1, core.MONO, "luminance", None, True, False, 40.0, 300.0,
                                     sky_meas=meas)
        self.assertIn("This check is then less certain", eg["note"])
        ok = core.photon_noise_setup({}, 1, core.MONO, "luminance", None, True, False, 30.5, 300.0, sky_meas=meas)
        self.assertNotIn("lower limit (", ok["note"])


def _cloudy_night(n=16, cloudy=range(4, 11), amp=0.06, seed=51):
    """A synthetic night (g = 2) with a passing cloud on the frames in `cloudy`: 80 smooth blobs of 35 px FWHM, amp x
    the sky (about 6 %: under the source detection, so not masked), drifting 30 px a frame. Returns (frames dict,
    floor from the darks)."""
    lights, darks, _b = synth_night(n=n, g=2.0, rn_e=10.0, dark_e=20.0, seed=seed)
    master = 0.5 * (darks[0] + darks[1])
    h, w = lights[0].shape
    yy, xx = np.mgrid[0:h, 0:w]
    rng = np.random.default_rng(2)
    cx, cy = rng.uniform(-200, w, 80), rng.uniform(0, h, 80)
    s2 = 2 * (35 / 2.3548) ** 2

    def cloud(i):
        c = np.zeros((h, w))
        for x, y in zip(cx + 30 * i, cy + 9 * i):
            c += np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / s2)
        return amp * 325.0 * c   # the sky is about 325 ADU
    frames = {f"f{i:02d}": (lights[i] - master + (cloud(i) if i in cloudy else 0.0)).astype(np.float32)
              for i in range(n)}
    return frames, sg.floor_from_pair(darks[0], darks[1])


class TestWhitenessGuard(unittest.TestCase):
    """Mike Fitzgerald's review of the method: structure the background model misses (a passing cloud, moonlight)
    adds variance and biases the gain low; a pair whose whiteness ratio is over 1.05 is dropped (John's choice)."""

    def test_white_noise_ratio_is_one(self):
        rng = np.random.default_rng(4)
        a, b = (sg.prep_frame(rng.normal(300.0, 12.0, (300, 300)).astype(np.float32)) for _ in range(2))
        self.assertAlmostEqual(sg.whiteness(a, b), 1.0, delta=0.02)

    def test_passing_cloud_pairs_are_dropped(self):
        frames, fl = _cloudy_night()
        m = sg.measure_night(list(frames), frames.__getitem__, sat=60000, floor_var=fl, floor_source="darks")
        self.assertTrue(m["ok"], m.get("why"))
        self.assertGreaterEqual(m["dropped_structure"], 4)
        self.assertLess(abs(m["k"] / 2.0 - 1), 0.02, m["k"])
        self.assertIn(f"{m['dropped_structure']} pairs dropped: the sky had structure the background model missed "
                      "(cloud, moonlight, or faint stars that moved between the frames)", sg.summary(m))
        import transit_ui as tu
        self.assertIn("pairs dropped: the sky had structure", tu.sky_meas_words({"sky_meas": core._sky_meas_brief(m)}))
        with mock.patch.object(sg, "WHITE_MAX", float("inf")):   # without the guard: low
            raw = sg.measure_night(list(frames), frames.__getitem__, sat=60000, floor_var=fl, floor_source="darks")
        self.assertEqual(raw["dropped_structure"], 0)
        self.assertLess(raw["k"], m["k"] - 0.04)

    def test_cloudy_all_night_falls_back_with_the_reason(self):
        frames, fl = _cloudy_night(n=6, cloudy=range(6), amp=0.08)
        m = sg.measure_night(list(frames), frames.__getitem__, sat=60000, floor_var=fl, floor_source="darks")
        self.assertFalse(m["ok"])
        self.assertIn("pairs dropped: the sky had structure the background model missed", m["why"])


class TestStackedRunUsesTheFramesGain(unittest.TestCase):
    """John's choice: a stacked run takes the gain measured on the single frames that went into the stacks; else on
    the stacks with the darks' floor x STACKN; else the lower limit. A stack is a sum (stack_core.write_stack: STACKSCL
    SUM), so the electrons per image unit are the frames' own: Bin 2 mono, g = 2 e-/ADU -> 8."""

    def _night(self, d):
        from astropy.io import fits
        lights, darks, _b = synth_night(n=32, g=2.0, rn_e=10.0, dark_e=20.0, shape=(360, 420), sky=(800.0, 900.0),
                                        seed=61)
        ld, dd, sd = (os.path.join(d, x) for x in ("Lights", "Darks", "stacks"))
        for f in (ld, dd, sd):
            os.makedirs(f)
        frames = []
        for i, img in enumerate(lights):
            p = os.path.join(ld, f"L{i:03d}.fits")
            fits.writeto(p, img, fits.Header({"EXPTIME": 60.0}))
            frames.append(p)
        for i, img in enumerate(darks):
            fits.writeto(os.path.join(dd, f"D{i}.fits"), img, fits.Header({"EXPTIME": 60.0}))
        master = (0.5 * (darks[0] + darks[1])).astype(np.float32)
        stacks = _write_stacks(sd, lights, master, 4, 2)
        masters = core.Masters(dark=core.bin_plain(master, 2), dark_exptime=60.0)
        return frames, stacks, masters, dd

    def _setup(self, fake, stacks, masters, frames, logs):
        import clearv_app
        return clearv_app.App._photon_noise_setup(fake, stacks, 2, core.MONO, "luminance", masters, False, [], 4.0,
                                                  8.0, 13.0, log=logs.append, stacks=True, stack_frames=frames)

    def _fake(self, dd):
        import clearv_app
        fake = _fake_app(dark_dir=dd)
        fake.preview = None
        fake.log = lambda t: None
        for name in ("_sky_gain_measure", "_flux_gain_check"):
            setattr(fake, name, (lambda n: lambda *a, **k: getattr(clearv_app.App, n)(fake, *a, **k))(name))
        return fake

    def setUp(self):
        self._mono = core._SENSOR["mono"]
        core.set_mono(True)

    def tearDown(self):
        core.set_mono(self._mono)

    def test_bin2_stacked_run_takes_the_frames_gain(self):
        import stack_core
        with tempfile.TemporaryDirectory() as d, mock.patch.object(stack_core, "STACK_CACHE_DIR", os.path.join(d, "stacks")):
            frames, stacks, masters, dd = self._night(d)
            logs = []
            s = self._setup(self._fake(dd), stacks, masters, frames, logs)
            self.assertEqual(s["sky_method"], "measured")
            self.assertLess(abs(s["electrons"] / 8.0 - 1), 0.04, s["electrons"])
            self.assertEqual(s["sky_meas"]["floor_source"], "darks")
            self.assertEqual(s["sky_meas"]["basis"], "frames")
            self.assertIn("measured on the single frames that went into the stacks", s["note"])
            self.assertIn("Measuring the gain from the night's sky (24 frame pairs)…", logs)
            # read noise per camera pixel is the frames' own: sqrt(10^2 + 20) e-
            self.assertAlmostEqual(s["sky_meas"]["rn_e"], math.sqrt(120), delta=1.0)

    def test_frames_unreadable_uses_the_stacks_with_the_darks_floor_x_n(self):
        import stack_core
        with tempfile.TemporaryDirectory() as d, mock.patch.object(stack_core, "STACK_CACHE_DIR", os.path.join(d, "stacks")):
            _frames, stacks, masters, dd = self._night(d)
            gone = [os.path.join(d, "gone", f"L{i}.fits") for i in range(32)]
            s = self._setup(self._fake(dd), stacks, masters, gone, [])
            self.assertEqual(s["sky_method"], "measured")
            self.assertLess(abs(s["electrons"] / 8.0 - 1), 0.06, s["electrons"])
            self.assertEqual(s["sky_meas"]["basis"], "stacks")
            self.assertEqual(s["sky_meas"]["stack_n"], 4)
            self.assertIn("units per stack of 4 frames (from the darks, added up over the 4 frames)", s["note"])
            self.assertIn("e- per camera pixel in a single frame", s["note"])
            self.assertIn("measured on the stacks themselves", s["note"])
            self.assertAlmostEqual(s["sky_meas"]["rn_e"], math.sqrt(120), delta=1.5)

    def test_neither_falls_back_and_says_both(self):
        import stack_core
        with tempfile.TemporaryDirectory() as d, mock.patch.object(stack_core, "STACK_CACHE_DIR", os.path.join(d, "stacks")):
            _frames, stacks, masters, _dd = self._night(d)
            gone = [os.path.join(d, "gone", f"L{i}.fits") for i in range(32)]
            fake = self._fake("")
            with mock.patch("photometry_core.sky_electrons_estimate", return_value=(5.0, 400.0)):
                s = self._setup(fake, stacks[:3], masters, gone, [])
            self.assertEqual(s["sky_method"], "lower limit")
            self.assertIn("It could not be measured from the night's sky: not on the single frames (", s["note"])
            self.assertIn("nor on the stacks (", s["note"])

    def test_run_passes_the_stacks_frames(self):
        with open(os.path.join(HERE, "clearv_app.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("gain_inputs=gain_inputs, stack_frames=stack_frames)", src)
        self.assertIn('for m in (stack_run.get("meta") or {}).get(sp, {}).get("members", []):', src)
        import stack_core
        self.assertIn("(sum scale)", stack_core.write_stack.__code__.co_consts.__repr__() + open(
            os.path.join(HERE, "stack_core.py"), encoding="utf-8").read())


class TestManual(unittest.TestCase):
    def test_chapter_23_and_8(self):
        with open(os.path.join(HERE, "docs", "manual", "23-messages-explained.md"), encoding="utf-8") as fh:
            ch23 = " ".join(fh.read().split())
        self.assertIn("Measuring the gain from the night's sky (… frame pairs)…", ch23)
        self.assertIn("under the one-frame lower limit", ch23)
        self.assertIn("the sky had structure the background model missed", ch23)
        with open(os.path.join(HERE, "docs", "manual", "08-photometry-basics.md"), encoding="utf-8") as fh:
            ch8 = " ".join(fh.read().split())
        self.assertIn("at least twice the read noise", ch8)
        self.assertIn("neighbouring pixels", ch8)
        self.assertIn("more than 5 % larger", ch8)
        self.assertIn("single frames that went into the stacks", ch8)
        self.assertIn("stacks", ch8.split("### The gain behind the error bars")[1].split("### Comps near")[0])


if __name__ == "__main__":
    unittest.main()
