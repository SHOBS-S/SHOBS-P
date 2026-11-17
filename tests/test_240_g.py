"""2.4.0 step 3 (#104): the gain measured from the night's sky (sky_gain.py; background model + source mask idea by
Mike Fitzgerald, EXOTIC): synthetic nights with a known gain, read noise, pixel-to-pixel sensitivity (no flat), a
gradient and stars; the fallbacks; photon_noise_setup's words and EGAIN cross-check; the settings file / NOTES words;
the SHOBS-FLUX camera profile check; the window loader against the full calibration; speed on 6248x4176 frames; and
the WASP-50 MicroObservatory night as an integration check."""
import glob
import json
import math
import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photometry_core as core    # noqa: E402
import sky_gain as sg             # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WASP50 = "/home/claude/data/wasp50"


def _src(name="clearv_app.py"):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def synth_night(n=14, g=2.0, rn_e=10.0, dark_e=0.0, shape=(360, 420), sky=(600.0, 700.0), prnu=0.02,
                bias=300.0, n_stars=40, seed=7, stars_out=None):
    """n light frames in ADU (bias in), a dark pair and a bias pair, of a camera with gain g e-/ADU and read noise
    rn_e e-. The sky (e- per pixel) runs from sky[0] to sky[1] through the night, with a 30 % gradient across the
    frame; each pixel's sensitivity is off by prnu (fixed: no flat); stars drift 0.3 px a frame."""
    rng = np.random.default_rng(seed)
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    gain_map = 1.0 + prnu * rng.standard_normal(shape)
    grad = 1.0 + 0.3 * xx / w
    sx, sy = rng.uniform(10, w - 10, n_stars), rng.uniform(10, h - 10, n_stars)
    flux = 10 ** rng.uniform(3.5, 5.5, n_stars)
    if stars_out is not None:
        stars_out.extend(zip(sx, sy, flux))

    def stars(dx):
        img = np.zeros(shape)
        for x, y, f in zip(sx + dx, sy + 0.5 * dx, flux):
            x0, x1, y0, y1 = int(max(x - 8, 0)), int(min(x + 9, w)), int(max(y - 8, 0)), int(min(y + 9, h))
            px, py = np.meshgrid(np.arange(x0, x1), np.arange(y0, y1))
            img[y0:y1, x0:x1] += f / (2 * np.pi * 1.6 ** 2) * np.exp(-((px - x) ** 2 + (py - y) ** 2) / (2 * 1.6 ** 2))
        return img

    def read(e_signal):
        e = rng.poisson(np.clip(e_signal, 0, None)).astype(float)
        return (bias + e / g + rng.normal(0.0, rn_e / g, shape)).astype(np.float32)

    lights = []
    for i, s in enumerate(np.linspace(sky[0], sky[1], n)):
        lights.append(read((s * grad + stars(0.3 * i)) * gain_map + dark_e))
    darks = [read(np.full(shape, dark_e)) for _ in range(2)]
    biases = [read(np.zeros(shape)) for _ in range(2)]
    return lights, darks, biases


def _ident_night(lights, darks):
    """Frames calibrated like SHOBS-P with a master dark (the dark pair's mean)."""
    master = 0.5 * (darks[0] + darks[1])
    return {f"f{i:03d}": (img - master).astype(np.float32) for i, img in enumerate(lights)}


class TestEstimatorSynthetic(unittest.TestCase):
    def test_recovers_gain_with_darks_where_the_old_one_is_low(self):
        lights, darks, _b = synth_night(g=2.0, rn_e=10.0, dark_e=40.0, prnu=0.02)
        frames = _ident_night(lights, darks)
        floor = sg.floor_from_pair(darks[0], darks[1])
        self.assertAlmostEqual(floor, ((10.0 / 2) ** 2 + 40.0 / 4), delta=0.05 * (25 + 10))
        m = sg.measure_night(list(frames), frames.__getitem__, sat=60000, floor_var=floor, floor_source="darks")
        self.assertTrue(m["ok"], m.get("why"))
        self.assertEqual(m["floor_source"], "darks")
        self.assertLess(abs(m["k"] / 2.0 - 1), 0.04, m["k"])
        # the old single-frame lower limit on the same night is far low (read noise, dark noise, fixed pattern)
        ref = frames["f000"]
        src = core.detect_sources(ref, max_sources=20)
        st = [core.Star(name=str(i), x=float(x), y=float(y)) for i, (x, y, *_r) in enumerate(src)]
        old = core.sky_electrons_estimate(ref, st, 4.0, 10.0, 16.0)
        self.assertLess(old, 0.85 * 2.0)
        self.assertGreater(m["k"], old)
        # read + dark noise in e- per camera pixel (mono, Bin 1): sqrt(10^2 + 40) = 11.8
        rn = sg.read_noise_e(m["floor_var"], m["k"], 1, core.MONO, "luminance")
        self.assertAlmostEqual(rn, math.sqrt(100 + 40), delta=0.6)

    def test_bias_floor_is_read_noise_only(self):
        lights, darks, biases = synth_night(g=1.5, rn_e=6.0, dark_e=0.0, seed=3)
        frames = _ident_night(lights, darks)
        floor = sg.floor_from_pair(biases[0], biases[1])
        m = sg.measure_night(list(frames), frames.__getitem__, floor_var=floor, floor_source="bias")
        self.assertTrue(m["ok"])
        self.assertLess(abs(m["k"] / 1.5 - 1), 0.04, m["k"])
        self.assertIn("bias", sg.summary(m))

    def test_photon_transfer_floor_without_darks(self):
        # twilight to dark: the sky changes 4x through the night, so the photon-transfer line gives the floor
        lights, darks, _b = synth_night(n=24, g=2.0, rn_e=12.0, sky=(300.0, 1500.0), seed=5)
        frames = _ident_night(lights, darks)
        m = sg.measure_night(list(frames), frames.__getitem__)
        self.assertTrue(m["ok"], m.get("why"))
        self.assertEqual(m["floor_source"], "ptc")
        self.assertTrue(m["ptc"]["ok"])
        self.assertLess(abs(m["k"] / 2.0 - 1), 0.08, m["k"])
        self.assertLess(abs(m["ptc"]["intercept"] / 36.0 - 1), 0.4)
        self.assertIn("Photon-transfer check", sg.ptc_words(m))

    def test_no_floor_and_steady_sky_falls_back(self):
        lights, darks, _b = synth_night(n=10, sky=(600.0, 605.0), seed=9)
        frames = _ident_night(lights, darks)
        m = sg.measure_night(list(frames), frames.__getitem__)
        self.assertFalse(m["ok"])
        self.assertIn("too small for a photon-transfer line", m["why"])
        s = core.photon_noise_setup({}, 1, core.MONO, "luminance", None, True, False, 1.4, 300.0, sky_meas=m)
        self.assertEqual(s["source"], "sky")
        self.assertEqual(s["sky_method"], "lower limit")
        self.assertEqual(s["electrons"], 1.4)
        self.assertIn("a lower limit", s["note"])
        self.assertIn("could not be measured from the night's sky", s["note"])

    def test_too_few_frames(self):
        lights, darks, _b = synth_night(n=3, seed=11)
        frames = _ident_night(lights, darks)
        m = sg.measure_night(list(frames), frames.__getitem__, floor_var=25.0, floor_source="darks")
        self.assertFalse(m["ok"])
        self.assertIn("only 2 usable frame pairs", m["why"])

    def test_bad_frames_and_level_jumps_are_skipped(self):
        lights, darks, _b = synth_night(n=8, seed=13)
        frames = _ident_night(lights, darks)
        a, b = sg.prep_frame(frames["f000"]), sg.prep_frame(frames["f001"] * 1.4)
        self.assertTrue(math.isnan(sg.pair_stats(a, b)[0]))   # 40 % brighter: a cloud or twilight jump
        m = sg.measure_night(list(frames), frames.__getitem__, floor_var=25.0, floor_source="darks",
                             skip={"f003"})
        self.assertEqual(m["pairs_tried"], 6)   # 7 kept frames, 6 neighbouring pairs

    def test_pick_pairs_spread(self):
        self.assertEqual(sg.pick_pairs(1), [])
        self.assertEqual(sg.pick_pairs(4), [(0, 1), (1, 2), (2, 3)])
        p = sg.pick_pairs(100)
        self.assertEqual(len(p), sg.MAX_PAIRS)
        self.assertEqual((p[0], p[-1]), ((0, 1), (98, 99)))
        self.assertEqual(sg.max_pairs_for(650 * 500), 24)
        self.assertEqual(sg.max_pairs_for(6248 * 4176), 12)

    def test_clipping_correction(self):
        self.assertTrue(0.99 < sg.CLIP_VAR < 1.0)
        x = np.random.default_rng(1).normal(0, 3.0, 400_000)
        self.assertAlmostEqual(sg.clipped_var(x) / 9.0, 1.0, delta=0.01)


class TestWindowLoader(unittest.TestCase):
    def test_window_is_the_full_calibration_cut(self):
        from astropy.io import fits
        rng = np.random.default_rng(2)
        h, w, b = 96, 128, 2
        raw = rng.integers(200, 3000, (h, w)).astype(np.uint16)
        bs = (h // (2 * b) * 2 * b // b, w // (2 * b) * 2 * b // b)
        m = core.Masters(bias=rng.uniform(90, 110, bs).astype(np.float32),
                         dark=rng.uniform(0, 20, bs).astype(np.float32),
                         flat=rng.uniform(0.8, 1.2, bs).astype(np.float32), dark_exptime=30.0)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "light.fits")
            hdr = fits.Header()
            hdr["EXPTIME"] = 60.0
            fits.writeto(p, raw, hdr)   # uint16: stored with BZERO 32768
            full = core.debayer(core.calibrate_frame(core.bin_image(core.read_fits(p)[0], b), hdr, m), "RGGB",
                                "luminance")
            load = sg.window_loader(b, m, "RGGB", "luminance", max_px=200)
            win = load(p)
            y0, y1, x0, x1 = sg.window((h, w), 200 * b * b * 4, 2 * b)
            ref = full[y0 // (2 * b):y1 // (2 * b), x0 // (2 * b):x1 // (2 * b)]
            self.assertLess(win.size, full.size)
            np.testing.assert_allclose(win, ref, rtol=1e-5, atol=1e-3)
            # a file it cannot cut a window of goes through the full load
            called = []
            load2 = sg.window_loader(b, m, "RGGB", "luminance", max_px=200,
                                     full_load=lambda q: called.append(q) or full)
            out = load2(os.path.join(d, "stack.xisf"))
            self.assertEqual(called, [os.path.join(d, "stack.xisf")])
            self.assertLessEqual(out.size, 200)

    def test_big_frames_stay_fast(self):
        # John's ASI2600MC: 6248 x 4176 16-bit OSC frames; 12 pairs are read (24 frames)
        from astropy.io import fits
        H, W = 4176, 6248
        rng = np.random.default_rng(4)
        m = core.Masters(dark=np.full((H, W), 500, np.float32), flat=np.ones((H, W), np.float32), dark_exptime=60.0)
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i in range(2):
                p = os.path.join(d, f"big{i}.fits")
                hdr = fits.Header()
                hdr["EXPTIME"] = 60.0
                fits.writeto(p, (rng.poisson(900, (H, W)) + 500).astype(np.uint16), hdr)
                paths.append(p)
            load = sg.window_loader(1, m, "RGGB", "luminance")
            sg.prep_frame(load(paths[0]))   # imports
            t = time.perf_counter()
            for p in paths:
                sg.prep_frame(load(p), 60000)
            per = (time.perf_counter() - t) / 2
        self.assertLess(per * 2 * sg.max_pairs_for(H * W), 8.0, f"{per:.3f} s a frame")


class TestNoiseSetupWords(unittest.TestCase):
    MEAS = {"ok": True, "k": 39.3, "k_lo": 38.9, "k_hi": 39.4, "n_pairs": 24, "floor_var": 1.337,
            "floor_source": "darks", "rn_e": 45.4,
            "ptc": {"ok": True, "g": 37.1, "g_err": 2.7, "range": 1.14, "intercept": 0.94}}

    def test_measured_without_egain(self):
        s = core.photon_noise_setup({}, 1, core.MONO, "luminance", None, True, False, 28.1, 280.0, sky_meas=self.MEAS)
        self.assertEqual((s["source"], s["sky_method"], s["electrons"]), ("sky", "measured", 39.3))
        self.assertEqual(s["sky_k_lower_limit"], 28.1)
        for words in ("gain is measured from the night's sky: 39.3 electrons per image unit (from 24 frame pairs",
                      "read + dark noise 1.16 units from the darks", "idea by Mike Fitzgerald",
                      "Photon-transfer check across the night: 37.1 +/- 2.7"):
            self.assertIn(words, s["note"])
        self.assertEqual(s["sky_meas"]["pairs"], 24)
        json.dumps(s["sky_meas"])

    def test_bias_not_removed_never_uses_the_measurement(self):
        s = core.photon_noise_setup({}, 1, core.MONO, "luminance", None, False, False, 28.1, 280.0, sky_meas=self.MEAS)
        self.assertEqual(s["source"], "assumed")

    def test_egain_cross_check_is_tighter_with_a_measurement(self):
        def run(k, lo=None, hi=None, level=500.0):
            meas = dict(self.MEAS, k=k, k_lo=lo or 0.98 * k, k_hi=hi or 1.02 * k)
            return core.photon_noise_setup({"EGAIN": 1.0}, 1, core.MONO, "luminance", None, True, False, 0.5 * k,
                                           level, sky_meas=meas)
        self.assertEqual(run(1.2)["warning"], "")
        self.assertIn("1.20 x that", run(1.2)["note"])
        self.assertIn("too small", run(1.7)["warning"])
        self.assertEqual(run(1.7, lo=1.4)["warning"], "")       # its own spread reaches 1.5x: no caution
        self.assertIn("12/14-bit", run(0.6)["warning"])
        self.assertEqual(run(0.6, level=50.0)["warning"], "")   # a faint sky: as before, no caution
        # without a measurement the old lower-limit check is unchanged (2x)
        old = core.photon_noise_setup({"EGAIN": 1.0}, 1, core.MONO, "luminance", None, True, False, 1.7, 500.0)
        self.assertEqual(old["warning"], "")
        self.assertIn("(a lower limit)", old["note"])
        self.assertEqual(old["sky_method"], "lower limit")

    def test_camera_binning_uses_the_measurement(self):
        hdr = {"EGAIN": 0.768, "XBINNING": 3}
        # old lower limit 2.0 says summed (under 3 x 0.768); the measurement 6.9 says averaged
        s_old = core.photon_noise_setup(hdr, 1, core.MONO, "luminance", None, True, False, 2.0, 500.0)
        s_new = core.photon_noise_setup(hdr, 1, core.MONO, "luminance", None, True, False, 2.0, 500.0,
                                        sky_meas=dict(self.MEAS, k=6.9, k_lo=6.8, k_hi=7.0))
        self.assertEqual((s_old["camera_bin_mode"], s_new["camera_bin_mode"]), ("summed", "averaged"))


class TestReportWords(unittest.TestCase):
    def test_gain_note_and_notes(self):
        import transit_ui as tu
        pn = {"source": "sky", "k": 39.27, "sky_method": "measured",
              "sky_meas": core._sky_meas_brief(TestNoiseSetupWords.MEAS)}
        note = tu.gain_note(pn)
        self.assertTrue(note.startswith("Gain measured from the night's sky, 39.3 e- per image unit (from 24 frame "
                                        "pairs, 38.9-39.4; read + dark noise 1.16 units from the darks"), note)
        self.assertIn("idea by Mike Fitzgerald", note)
        self.assertEqual(note.split(" (")[0], "Gain measured from the night's sky, 39.3 e- per image unit")
        note.encode("ascii")
        ll = tu.gain_note({"source": "sky", "k": 28.1})
        self.assertEqual(ll.split(" (")[0], "Gain from the sky's own noise, a lower limit of 28.1 e- per image unit")
        self.assertEqual(tu.sky_check_line(pn), "")
        eg = {"source": "EGAIN", "e_per_adu": 0.2554, "sky_meas": pn["sky_meas"]}
        self.assertTrue(tu.sky_check_line(eg).startswith("Gain check from the night's sky: 39.3 e- per image unit"))
        self.assertEqual(tu.gain_note(eg), "Gain 0.255 e-/ADU (header EGAIN)")   # NOTES unchanged on EGAIN nights

    def test_onsky_note(self):
        import onsky_log
        out = onsky_log.electron_scale({"source": "sky", "electrons": 39.3, "k_median": 39.3,
                                        "sky_method": "measured"})
        self.assertIn("measured from the night's sky", out["note"])
        out = onsky_log.electron_scale({"source": "sky", "electrons": 28.1, "k_median": 28.1,
                                        "sky_method": "lower limit"})
        self.assertIn("lower limit", out["note"])


class TestFluxProfileCheck(unittest.TestCase):
    def test_profile_found_and_compared(self):
        import shobs_profile as sp
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"SHOBS_DATA_HOME": d}):
            hdr = {"INSTRUME": "ZWO ASI2600MC Air", "GAIN": 100, "OFFSET": 50, "XBINNING": 1}
            cam = sp.setup_key(hdr)["camera_id"]
            doc = {"format": sp.CAMERA_FORMAT, "format_version": 1, "profile_id": cam, "revision": 1,
                   "created": "2026-11-01", "updated": "2026-11-01", "camera": {"instrume": hdr["INSTRUME"]},
                   "mode": {}, "values": {"gain_e_per_adu": sp.record(0.25, "e-/ADU", "measured"),
                                          "read_noise_e": sp.record(1.4, "e-", "measured")}}
            sp.write_json(sp.profile_path("cameras", cam), doc)
            prof = sp.find_camera_profile(cam)
            self.assertIsNotNone(prof)
            g, rec = sp.camera_value(prof, "gain_e_per_adu")
            rn, rrec = sp.camera_value(prof, "read_noise_e")
            epu1 = core.electrons_per_unit(1.0, 1, "RGGB", "luminance")
            meas = {"ok": True, "k": 0.26 * epu1, "rn_e": 1.5, "floor_source": "darks"}
            text = sg.flux_words(cam, meas, {}, g, rec["source"], rn, rrec["source"], epu1)
            self.assertIn("zwo-asi2600mc-air__g100__o50__bin1: gain 0.25 e-/ADU (measured)", text)
            self.assertIn("the night's sky gives 0.26 e-/ADU, they agree (within 4 %)", text)
            self.assertIn("read + dark noise is 1.5 e-, about the same", text)
            far = sg.flux_words(cam, dict(meas, k=0.4 * epu1), {}, g, "measured", rn, "measured", epu1)
            self.assertIn("60 % higher: check that the profile is for this camera's gain and offset setting", far)
            self.assertNotIn("⚠", far)
            eg = sg.flux_words(cam, None, {"e_per_adu": 0.25}, g, "measured", rn, "measured", epu1)
            self.assertIn("the headers' EGAIN is 0.25 e-/ADU (the same)", eg)
            self.assertEqual(sg.flux_words(cam, meas, {}, None, "", None, "", epu1), "")

    def test_read_noise_units(self):
        # mono Bin 1: floor x k; OSC TG at Bin 2: q = 0.5 / 4
        self.assertAlmostEqual(sg.read_noise_e(4.0, 3.0, 1, core.MONO, "green"), 6.0)
        self.assertAlmostEqual(sg.read_noise_e(4.0, 3.0, 2, "RGGB", "green"), 2 * 3 * math.sqrt(0.125))
        self.assertAlmostEqual(sg.variance_factor(2, "RGGB", "green") * core.electrons_per_unit(1.0, 2, "RGGB",
                                                                                               "green"), 1.0)


class _Box:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


class TestAppGlue(unittest.TestCase):
    def test_run_measures_in_the_worker_before_the_frames(self):
        src = _src()
        i = src.index("        def work_inner():\n            nonlocal target, comps, check")
        body = src[i:i + 1500]
        self.assertIn("noise_setup = self._photon_noise_setup(", body)
        self.assertIn("all_lights=night_lights", body)
        self.assertLess(body.index("noise_setup"), body.index("night_points = []"))

    def test_sky_gain_measure_darks_cache(self):
        import clearv_app
        from astropy.io import fits
        lights, darks, _b = synth_night(n=8, g=2.0, rn_e=10.0, dark_e=20.0, seed=21)
        with tempfile.TemporaryDirectory() as d:
            ld, dd = os.path.join(d, "Lights"), os.path.join(d, "Darks")
            os.makedirs(ld)
            os.makedirs(dd)
            paths = []
            for i, img in enumerate(lights):
                p = os.path.join(ld, f"L{i:03d}.fits")
                fits.writeto(p, img, fits.Header({"EXPTIME": 60.0}))
                paths.append(p)
            for i, img in enumerate(darks):
                fits.writeto(os.path.join(dd, f"D{i}.fits"), img, fits.Header({"EXPTIME": 60.0}))
            fits.writeto(os.path.join(dd, "D_other.fits"), darks[0], fits.Header({"EXPTIME": 10.0}))
            master = core.Masters(dark=(0.5 * (darks[0] + darks[1])).astype(np.float32), dark_exptime=60.0)
            fake = types.SimpleNamespace(dark_dir=_Box(dd), bias_dir=_Box(""), sat_limit=_Box(60000.0),
                                         _linear_limit=lambda: None)
            fake._sky_gain_inputs = lambda: clearv_app.App._sky_gain_inputs(fake)
            hdr = core.read_header(paths[0])
            m = clearv_app.App._sky_gain_measure(fake, paths, 1, core.MONO, "luminance", master, False, False, hdr)
            self.assertTrue(m["ok"], m.get("why"))
            self.assertEqual(m["floor_source"], "darks")
            self.assertLess(abs(m["k"] / 2.0 - 1), 0.05)
            self.assertAlmostEqual(m["rn_e"], math.sqrt(100 + 20), delta=0.8)
            again = clearv_app.App._sky_gain_measure(fake, paths, 1, core.MONO, "luminance", master, False, False,
                                                     hdr)
            self.assertIs(again, m)   # the same frames and calibration: kept (Measure skipped frames agrees)
            # stacks never take the darks' floor
            st = clearv_app.App._sky_gain_measure(fake, paths, 1, core.MONO, "luminance", master, False, True, hdr)
            self.assertNotEqual(st.get("floor_source"), "darks")


def _have_tk():
    try:
        import tkinter as tk
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


@unittest.skipUnless(_have_tk(), "needs a display")
class TestRunInApp(unittest.TestCase):
    """A no-EGAIN night with darks through the real photometry run: the log, the night's record and the gain."""

    def test_run_measures_the_gain(self):
        import clearv_app as ca
        from astropy.io import fits
        from astropy.time import Time
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test240g.log")
        old_onsky, ca.onsky_log = ca.onsky_log, None
        stars = []
        lights, darks, _b = synth_night(n=16, g=2.0, rn_e=10.0, dark_e=20.0, seed=31, stars_out=stars)
        tmp = tempfile.mkdtemp()
        app = ca.App()
        app.withdraw()
        try:
            ld, dd = os.path.join(tmp, "Lights"), os.path.join(tmp, "Darks")
            os.makedirs(ld)
            os.makedirs(dd)
            t0 = 2460950.6
            for i, img in enumerate(lights):
                hdr = fits.Header()
                hdr["EXPTIME"] = 60.0
                hdr["DATE-OBS"] = Time(t0 + i * 70 / 86400, format="jd", scale="utc").isot
                fits.writeto(os.path.join(ld, f"light_{i:04d}.fits"), img, hdr)
            for i, img in enumerate(darks):
                fits.writeto(os.path.join(dd, f"dark_{i}.fits"), img, fits.Header({"EXPTIME": 60.0}))
            # the three brightest stars well inside the frame: target and two comps
            inside = [s for s in stars if 30 < s[0] < 390 and 30 < s[1] < 330]
            best = sorted(inside, key=lambda s: -s[2])
            picked = []
            for s in best:
                if all(math.hypot(s[0] - p[0], s[1] - p[1]) > 30 for p in picked):
                    picked.append(s)
                if len(picked) == 3:
                    break
            (tx, ty, _f), (c1x, c1y, _f1), (c2x, c2y, _f2) = picked
            with mock.patch.object(ca.messagebox, "askyesno", return_value=True), \
                    mock.patch.object(ca.messagebox, "askyesnocancel", return_value=False), \
                    mock.patch.object(ca.messagebox, "showinfo"), mock.patch.object(ca.messagebox, "showwarning"), \
                    mock.patch.object(ca, "ask_choice", side_effect=lambda *a, **k: a[3][0][0]), \
                    mock.patch.object(ca.messagebox, "showerror") as err:
                app.set_mode("variables")
                app.light_dir.set(ld)
                app.dark_dir.set(dd)
                app.pattern.set(core.MONO)
                app.binning.set(1)
                app.star_id.set("")
                app.masters = core.Masters(dark=(0.5 * (darks[0] + darks[1])).astype(np.float32), dark_exptime=60.0)
                app.masters_binning = 1
                first = os.path.join(ld, "light_0000.fits")
                raw, head = core.read_fits(first)
                app.preview = core.calibrate_frame(raw, head, app.masters)
                app.preview_header = head
                app.preview_path = first
                app.preview_is_debayered = True
                app.calibrated = app.preview
                app.radius.set(4.0)
                app.sky_in.set(8.0)
                app.sky_out.set(13.0)
                app.target_xy = (tx, ty)
                app.comp_xy = (c1x, c1y)
                app.comp_name.set("C1")
                app.comp_mag.set("11.0")
                app.comp2_xy = (c2x, c2y)
                app.comp2_name.set("C2")
                app.comp2_mag.set("11.5")
                app.check_xy = None
                app.check_name.set("")
                app.run_photometry()
                end = time.monotonic() + 120
                while time.monotonic() < end and (app.busy or not app.observations):
                    app.update()
                    time.sleep(0.02)
                self.assertFalse(err.called, err.call_args)
            self.assertTrue(app.observations)
            text = app.log_box.get("1.0", "end")
            self.assertIn("so the gain is measured from the night's sky:", text)
            self.assertIn("read + dark noise", text)
            self.assertIn("idea by Mike Fitzgerald", text)
            nm = app._noise_model
            self.assertEqual((nm["source"], nm["sky_method"]), ("sky", "measured"))
            self.assertLess(abs(nm["k"] / 2.0 - 1), 0.05, nm["k"])
            self.assertEqual(nm["sky_meas"]["floor_source"], "darks")
            setup = app._night_setup()
            self.assertEqual(setup["photon_noise"]["sky_method"], "measured")
        finally:
            ca.onsky_log = old_onsky
            app.destroy()
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(os.path.isdir(os.path.join(WASP50, "Lights")), "WASP-50 test night not on this machine")
class TestWasp50Night(unittest.TestCase):
    """Joanna Puszkarska's MicroObservatory WASP-50 b night: 78 frames 650 x 500, 12-bit, no EGAIN, 2 darks."""

    def test_measured_gain(self):
        import clearv_app
        lights = sorted(glob.glob(os.path.join(WASP50, "Lights", "*")))
        darks = sorted(glob.glob(os.path.join(WASP50, "Darks", "*.FITS")))
        master = core.Masters(dark=np.median([core.read_fits(p)[0] for p in darks], axis=0).astype(np.float32),
                              dark_exptime=60.0)
        hdr = core.read_header(lights[0])
        fake = types.SimpleNamespace(dark_dir=_Box(os.path.join(WASP50, "Darks")), bias_dir=_Box(""),
                                     sat_limit=_Box(4095.0), _linear_limit=lambda: None)
        fake._sky_gain_inputs = lambda: clearv_app.App._sky_gain_inputs(fake)
        m = clearv_app.App._sky_gain_measure(fake, lights, 1, core.MONO, "luminance", master, False, False, hdr)
        self.assertTrue(m["ok"], m.get("why"))
        self.assertEqual((m["floor_source"], m["n_pairs"]), ("darks", 24))
        self.assertTrue(36.0 < m["k"] < 46.0, m["k"])
        ref = core.calibrate_frame(core.read_fits(lights[0])[0], hdr, master)
        src = core.detect_sources(ref, max_sources=30)
        st = [core.Star(name=str(i), x=float(x), y=float(y)) for i, (x, y, *_r) in enumerate(src)]
        old = core.sky_electrons_estimate(ref, st, 5.0, 10.0, 15.0)
        self.assertGreater(m["k"], 1.2 * old)   # the old one was a lower limit (28.7 in 2.3.0)
        s = core.photon_noise_setup(hdr, 1, core.MONO, "luminance", None, True, False, old, 280.0, sky_meas=m)
        self.assertEqual(s["sky_method"], "measured")


if __name__ == "__main__":
    unittest.main()
