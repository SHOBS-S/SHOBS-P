"""2.3.0 builder C: item 11 (Dark flats box), #53 (error bars on camera-binned frames), #55 (electrons per unit in
the on-sky log). Synthetic data only."""
import json
import math
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")

from astropy.io import fits  # noqa: E402

import photometry_core as core  # noqa: E402
import onsky_log  # noqa: E402
import transit_ui  # noqa: E402


# ---- synthetic frames -------------------------------------------------------------------------------------------

STAR_POS = [(45, 45), (130, 60), (60, 130), (125, 128)]
STAR_E = (4e5, 2e5, 3e5, 1.5e5)


def sim_frames(n, cam_bin, osc, rng, egain=0.768, rn=3.5, sky_e=400.0, shape=(180, 180), sigma=2.2, summed=False,
               flat_fn=None):
    """Frames of a fixed field in ADU as the camera stores them: native pixels with Poisson + read noise, binned
    cam_bin x cam_bin by the camera (averaged like ZWO RAW16, or summed). OSC: RGGB with color responses."""
    H, W = shape[0] * cam_bin, shape[1] * cam_bin
    yy, xx = np.mgrid[0:H, 0:W]
    model = np.zeros((H, W))
    for (x, y), e in zip(STAR_POS, STAR_E):
        xs, ys, s = (x + 0.5) * cam_bin - 0.5, (y + 0.5) * cam_bin - 0.5, sigma * cam_bin
        model += e / (2 * math.pi * s * s) * np.exp(-((xx - xs) ** 2 + (yy - ys) ** 2) / (2 * s * s))
    resp = np.ones((H, W))
    if osc:
        resp[0::2, 0::2], resp[1::2, 1::2] = 0.6, 0.5
    if flat_fn is not None:
        resp = resp * flat_fn(H, W)
    out = []
    for i in range(n):
        adu = (rng.poisson((model + sky_e) * resp).astype(float) + rng.normal(0, rn, (H, W))) / egain
        if cam_bin > 1:
            if osc:
                adu = core.bin_image(adu.astype(np.float32), cam_bin)
                adu = adu * cam_bin * cam_bin if summed else adu
            else:
                adu = adu.reshape(H // cam_bin, cam_bin, W // cam_bin, cam_bin)
                adu = adu.sum(axis=(1, 3)) if summed else adu.mean(axis=(1, 3))
        hdr = {"EGAIN": egain, "XBINNING": cam_bin, "YBINNING": cam_bin, "EXPTIME": 55.0,
               "DATE-OBS": "2025-07-05T05:%02d:%02d" % (i // 60, i % 60)}
        out.append((adu.astype(np.float32), hdr))
    return out


def error_ratio(cam_bin, osc, proc_bin, rng, n=120, summed=False, flat=False):
    """median predicted error / measured scatter of the differential magnitude, and the photon-noise setup."""
    flat_fn = (lambda H, W: 0.9 + 0.2 * np.mgrid[0:H, 0:W][1] / W) if flat else None  # noqa: E731
    frames = sim_frames(n, cam_bin, osc, rng, summed=summed, flat_fn=flat_fn)
    pattern, mode = ("RGGB", "green") if osc else (core.MONO, "green")
    core.set_mono(not osc)
    try:
        master = None
        if flat:
            f0 = sim_frames(1, cam_bin, osc, rng, sky_e=30000.0, summed=summed, flat_fn=flat_fn)[0][0]
            f0 = f0 - 0.0
            master = core.normalize_flat(core.bin_image(f0, proc_bin))
        imgs = []
        for adu, _h in frames:
            a = core.bin_image(adu, proc_bin)
            imgs.append(core.debayer(a / master if master is not None else a, pattern, mode))
    finally:
        core.set_mono(False)
    scale = (0.5 if osc else 1.0) / proc_bin
    stars = [core.Star(f"S{k}", x * scale, y * scale, 10.0 + 0.3 * k) for k, (x, y) in enumerate(STAR_POS)]
    r = max(3.0 * 2.2 * scale * (2 if osc else 1), 3.0)
    s_in, s_out = 2.0 * r, 3.2 * r
    sky_k, sky_lvl = core.sky_electrons_estimate(imgs[0], stars, r, s_in, s_out, with_level=True)
    setup = core.photon_noise_setup(frames[0][1], proc_bin, pattern, mode, master, True, False, sky_k, sky_lvl)
    mags, errs = [], []
    for img, (_a, hdr) in zip(imgs, frames):
        o = core.reduce_frame(img, hdr, stars[0], stars[1:], None, r, s_in, s_out, electrons=setup["electrons"])
        mags.append(o.mag)
        errs.append(o.merr)
    return float(np.median(errs)) / float(np.std(mags, ddof=1)), setup


class TestErrorBarsBinned(unittest.TestCase):
    """#53: predicted per-point errors match the real scatter (0.9-1.1) on camera-binned frames, mono and OSC."""

    def check(self, cam_bin, osc, proc_bin, **kw):
        rng = np.random.default_rng(11 + cam_bin * 7 + proc_bin + (3 if osc else 0))
        ratios, setup = [], None
        for _ in range(3):
            ratio, setup = error_ratio(cam_bin, osc, proc_bin, rng, **kw)
            ratios.append(ratio)
        mean = float(np.mean(ratios))
        self.assertTrue(0.9 <= mean <= 1.1, f"bin {cam_bin} {'OSC' if osc else 'mono'} proc {proc_bin}: "
                                            f"err/sd {mean:.3f} {ratios}")
        self.assertFalse(setup.get("warning"), setup.get("warning"))
        return setup

    def test_mono_unbinned(self):
        s = self.check(1, False, 1)
        self.assertEqual(s["camera_bin"], 1)
        self.assertAlmostEqual(s["k_median"], 0.768, places=6)   # EGAIN as it is

    def test_mono_camera_bin3_averaged(self):   # Matthew's ASI2600MM at bin 3
        s = self.check(3, False, 1)
        self.assertEqual(s["camera_bin_mode"], "averaged")
        self.assertAlmostEqual(s["e_per_stored_adu"], 0.768 * 9, places=6)
        self.assertIn("averaged", s["note"])

    def test_mono_camera_bin3_proc_bin2(self):   # ... and SHOBS-P processing bin 2
        s = self.check(3, False, 2)
        self.assertAlmostEqual(s["k_median"], 0.768 * 9 * 4, places=4)

    def test_mono_camera_bin3_summed(self):   # a camera that sums (CCD charge binning): EGAIN as it is
        s = self.check(3, False, 1, summed=True)
        self.assertEqual(s["camera_bin_mode"], "summed")
        self.assertAlmostEqual(s["k_median"], 0.768, places=6)

    def test_osc_unbinned(self):   # John's case: unchanged (k = 2 x EGAIN for TG at Bin 1, no flat)
        s = self.check(1, True, 1)
        self.assertAlmostEqual(s["k_median"], 2 * 0.768, places=6)
        self.assertNotIn("camera_bin_mode", {k for k, v in s.items() if v})

    def test_osc_unbinned_flat_bin2(self):   # John's usual: TG at Bin 2 with a flat
        self.check(1, True, 2, flat=True)

    def test_osc_camera_bin3(self):
        s = self.check(3, True, 1)
        self.assertEqual(s["camera_bin_mode"], "averaged")

    def test_unbinned_k_unchanged_by_the_fix(self):
        """John's OSC unbinned path gives exactly the pre-2.3.0 numbers (EGAIN, superpixel, flat; no camera_bin)."""
        flat = np.ones((40, 40), np.float32)
        flat[0::2, 1::2] = 1.3
        flat[1::2, 0::2] = 1.3
        hdr = {"EGAIN": 0.2554, "XBINNING": 1}
        s = core.photon_noise_setup(hdr, 2, "RGGB", "green", flat, True, False, 9.0, 100.0)
        want = core.electrons_per_unit(0.2554, 2, "RGGB", "green", flat)
        self.assertTrue(np.allclose(s["electrons"], want))
        self.assertAlmostEqual(s["k_median"], 8 * 0.2554 * 1.3, places=4)

    def test_no_sky_estimate_keeps_egain_and_says_so(self):
        s = core.photon_noise_setup({"EGAIN": 0.768, "XBINNING": 3}, 1, core.MONO, "green", None, True, False)
        self.assertEqual(s["camera_bin_mode"], "unknown")
        self.assertAlmostEqual(s["k_median"], 0.768)
        self.assertIn("cannot tell", s["note"])

    def test_gain_note(self):
        pn = {"source": "EGAIN", "e_per_adu": 0.768, "camera_bin": 3, "camera_bin_mode": "averaged",
              "e_per_stored_adu": 6.912}
        self.assertIn("averaged, so 6.91 e- per stored ADU", transit_ui.gain_note(pn))
        self.assertEqual(transit_ui.gain_note({"source": "EGAIN", "e_per_adu": 0.243}),
                         "Gain 0.243 e-/ADU (header EGAIN)")


# ---- item 11: dark flats ----------------------------------------------------------------------------------------

def write_set(folder, kind, n, level, exp, shape=(24, 32), rng=None, imagetyp=None, extra=None, noise=0.3):
    os.makedirs(folder, exist_ok=True)
    rng = rng or np.random.default_rng(1)
    paths = []
    for i in range(n):
        h = fits.Header()
        h["EXPTIME"] = exp
        h["IMAGETYP"] = imagetyp or kind
        h["DATE-OBS"] = "2025-07-05T03:00:%02d" % i
        for k, v in (extra or {}).items():
            h[k] = v
        data = np.broadcast_to(np.asarray(level, dtype=float), shape) + rng.normal(0, noise, shape)
        p = os.path.join(folder, f"{kind.replace(' ', '')}_{i:03d}.fits")
        fits.PrimaryHDU(data.astype(np.float32), header=h).writeto(p)
        paths.append(p)
    return paths


class TestDarkFlatsCore(unittest.TestCase):
    """Matthew's case (2 Nov): no bias, darks = lights' exposure (55 s), flats + dark flats 0.5 s."""

    PED = 500.0            # camera offset (ADU)
    DC = 0.4               # dark current ADU/s

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        rng = np.random.default_rng(3)
        yy, xx = np.mgrid[0:24, 0:32]
        self.vign = 0.8 + 0.4 * xx / 31.0                     # the true flat pattern
        self.dark = write_set(os.path.join(self.dir, "dark"), "Dark", 5, self.PED + self.DC * 55, 55.0, rng=rng)
        self.dflat = write_set(os.path.join(self.dir, "dflat"), "Dark Flat", 5, self.PED + self.DC * 0.5, 0.5,
                               rng=rng, imagetyp="Dark Flat")
        self.flat = write_set(os.path.join(self.dir, "flat"), "Flat", 5, self.PED + self.DC * 0.5 + 5000 * self.vign,
                              0.5, rng=rng, noise=0.3)
        self.light_exp = 55.0
        self.light = (self.PED + self.DC * 55 + 3000.0 * self.vign).astype(np.float32)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_imagetyp_words(self):
        for t in ("Dark Flat", "DARKFLAT", "Flat Dark", "FLATDARK", "dark_flat", "Dark-Flat", "flat-dark"):
            self.assertTrue(core.is_dark_flat_type(t), t)
        for t in ("Dark", "Flat", "Bias", "Light", "Master Flat"):
            self.assertFalse(core.is_dark_flat_type(t), t)
        self.assertTrue(core.frame_is_dark_flat(self.dflat[0]))
        self.assertFalse(core.frame_is_dark_flat(self.flat[0]))

    def test_split_dark_flats(self):
        flats, dfl = core.split_dark_flats(self.flat + self.dflat)
        self.assertEqual((len(flats), len(dfl)), (5, 5))

    def _sky_flatness(self, masters):
        cal = core.calibrate_frame(self.light.copy(), {"EXPTIME": self.light_exp}, masters)
        return float(np.std(cal) / np.mean(cal)), float(np.mean(cal))

    def test_box_empty_bias_wrong_flat(self):
        """Bias EMPTY and no dark flats: the 55 s dark scaled to 0.5 s leaves ~99% of the offset in the flats."""
        m = core.prepare_masters([], self.dark, self.flat, 1, save=False)
        spread, _ = self._sky_flatness(m)
        self.assertGreater(spread, 0.008)                    # the flat no longer flattens the sky
        plan = core.calibration_plan(False, True, True, False, dark_exp=55, light_exp=55, flat_exp=0.5)
        self.assertTrue(any("scales the camera's offset" in w for w in plan["warnings"]), plan)

    def test_dark_flats_box_no_bias(self):
        """Dark flats given, Bias empty: flats F - DF exactly, lights L - D exactly (no warning)."""
        logs = []
        m = core.prepare_masters([], self.dark, self.flat, 1, logs.append, save=True, darkflat_paths=self.dflat,
                                 version="test")
        self.assertEqual(m.pedestal, "darkflat")
        spread, mean = self._sky_flatness(m)
        self.assertLess(spread, 0.001)                       # sky flat to 0.1%
        self.assertAlmostEqual(mean, 3000.0 * float(np.median(self.vign)), delta=3.0)
        plan = core.calibration_plan(False, True, True, True, dark_exp=55, light_exp=55, flat_exp=0.5, darkflat_exp=0.5)
        self.assertEqual(plan["warnings"], [])
        self.assertIn("no bias is needed", plan["lights"])
        self.assertEqual(plan["flats"], "flat − dark flat")
        # the master dark flat is saved and reused (with the flat built on it)
        saved = os.listdir(os.path.join(self.dir, "dflat", core.MASTER_DIR))
        self.assertEqual(saved, [core.master_file_name("darkflat", 1, 0.5)])
        logs.clear()
        m2 = core.prepare_masters([], self.dark, self.flat, 1, logs.append, save=True, darkflat_paths=self.dflat,
                                  version="test")
        self.assertEqual(sum("Using saved master" in t for t in logs), 3, logs)
        self.assertTrue(np.allclose(m.flat, m2.flat, equal_nan=True))
        # the same flat folder without dark flats: the saved flat is not reused
        logs.clear()
        core.prepare_masters([], self.dark, self.flat, 1, logs.append, save=False)
        self.assertTrue(any("Median-combining 5 flat" in t for t in logs), logs)

    def test_scaled_darks_with_dark_flats_as_pedestal(self):
        """Darks 110 s for 55 s lights, no bias: L - DF - (D - DF) * t_L / t_D removes the offset and dark current."""
        dark110 = write_set(os.path.join(self.dir, "dark110"), "Dark", 5, self.PED + self.DC * 110, 110.0,
                            rng=np.random.default_rng(9))
        m = core.prepare_masters([], dark110, self.flat, 1, save=False, darkflat_paths=self.dflat)
        cal = core.calibrate_frame(self.light.copy(), {"EXPTIME": 55.0}, m)
        want = 3000.0 * self.vign / float(np.median(self.vign))
        # residual offset well under 1 ADU (the dark flats' 0.5 s of dark current, ~0.1 ADU, counts as pedestal)
        self.assertLess(abs(float(np.mean(cal - want * float(np.median(self.vign))))), 1.0)
        plan = core.calibration_plan(False, True, True, True, dark_exp=110, light_exp=55, flat_exp=0.5, darkflat_exp=0.5)
        self.assertTrue(any("zero-exposure pedestal" in n for n in plan["notes"]), plan)
        self.assertEqual(plan["warnings"], [])

    def test_scaled_darks_no_bias_no_darkflats_warns(self):
        plan = core.calibration_plan(False, True, False, False, dark_exp=110, light_exp=55)
        self.assertTrue(any(w.startswith("Darks can't be scaled correctly without bias or dark flats")
                            for w in plan["warnings"]), plan)

    def test_both_given_dark_flats_win_for_flats(self):
        bias = write_set(os.path.join(self.dir, "bias"), "Bias", 5, self.PED, 0.0, rng=np.random.default_rng(4))
        m = core.prepare_masters(bias, self.dark, self.flat, 1, save=False, darkflat_paths=self.dflat)
        self.assertEqual(m.pedestal, "bias")
        spread, _ = self._sky_flatness(m)
        self.assertLess(spread, 0.001)
        plan = core.calibration_plan(True, True, True, True, dark_exp=55, light_exp=55, flat_exp=0.5, darkflat_exp=0.5)
        self.assertEqual(plan["flats"], "flat − dark flat")
        self.assertTrue(any("Bias and dark flats are both given" in n for n in plan["notes"]))

    def test_old_trick_dark_flats_in_bias_box_still_works(self):
        m = core.prepare_masters(self.dflat, self.dark, self.flat, 1, save=False)
        spread, _ = self._sky_flatness(m)
        self.assertLess(spread, 0.001)
        plan = core.calibration_plan(True, True, True, False, dark_exp=55, light_exp=55, flat_exp=0.5,
                                     darkflats_in_bias=True)
        self.assertTrue(any("Dark flats box is now the place" in n for n in plan["notes"]))

    def test_dark_flat_exposure_mismatch_warns(self):
        plan = core.calibration_plan(False, True, True, True, dark_exp=55, light_exp=55, flat_exp=0.5, darkflat_exp=2.0)
        self.assertTrue(any("Dark flats should match the flats' exposure" in w for w in plan["warnings"]), plan)

    def test_chosen_master_dark_flat(self):
        core.prepare_masters([], [], [], 1, save=True, darkflat_paths=self.dflat, version="test")
        mf = os.path.join(self.dir, "dflat", core.MASTER_DIR, core.master_file_name("darkflat", 1, 0.5))
        self.assertEqual(core.check_master_file(mf, "darkflat"), "")
        self.assertIn("master dark flat, not a master bias", core.check_master_file(mf, "bias"))
        m = core.prepare_masters([], self.dark, self.flat, 1, files={"darkflat": mf}, save=False)
        self.assertEqual(m.pedestal, "darkflat")
        self.assertLess(self._sky_flatness(m)[0], 0.001)


# ---- #55: electrons per unit in the on-sky log ------------------------------------------------------------------

class TestOnskyElectrons(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = os.environ.get("SHOBS_DATA_HOME")
        os.environ["SHOBS_DATA_HOME"] = self.tmp

    def tearDown(self):
        if self.old is None:
            os.environ.pop("SHOBS_DATA_HOME", None)
        else:
            os.environ["SHOBS_DATA_HOME"] = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ctx(self, noise, pattern="RGGB", binning=2, flat=True):
        t = core.Star("HD 219134", 30.0, 20.0, None)
        c1 = core.Star("C1", 10.0, 10.0, 9.85)
        return {"run_id": "r1", "target": t, "comps": [c1], "check": None, "spares": [], "radius": 4.0,
                "sky_in": 6.0, "sky_out": 9.0, "binning": binning, "pattern": pattern, "debayer": "green",
                "precalibrated": False, "mode_key": "transits", "filt": "TG", "band": "V",
                "header": {"INSTRUME": "ZWO ASI2600MC", "EGAIN": 0.2554, "XBINNING": 1},
                "noise": noise, "flat_applied": flat}

    def _obs(self):
        o = core.Obs(jd=2461347.6, mag=5.5, merr=0.01, cmag=9.0, kmag=None, airmass=1.2, path="", exptime=1.0,
                     flux_var=1.0, flux_comp=1.0, fwhm=3.0, sky=12.0)
        o.target_inst, o.comp_insts = -11.2, [-6.9]
        o.pos = {"t": [30.0, 20.0], "c": [[10.0, 10.0]], "k": None}
        return o

    def test_osc_flat_map_per_star(self):
        """OSC TG Bin 2 with a flat: 8 x EGAIN x the green flat at each star (1.3 left, 1.38 right)."""
        flat = np.ones((80, 100), np.float32)
        flat[0::2, 1::2] = 1.30
        flat[1::2, 0::2] = 1.30
        flat[:, 50:][0::2, 1::2] = 1.38
        flat[:, 50:][1::2, 0::2] = 1.38
        noise = core.photon_noise_setup({"EGAIN": 0.2554}, 2, "RGGB", "green", flat, True, False)
        doc = onsky_log.build(self._ctx(noise), [self._obs()], "test", header_reads=False)
        self.assertEqual(doc["format_version"], 2)
        st = doc["frames"][0]["stars"]
        self.assertAlmostEqual(st["C1"]["e_per_unit"], 8 * 0.2554 * 1.30, places=3)
        self.assertAlmostEqual(st["HD 219134"]["e_per_unit"], 8 * 0.2554 * 1.38, places=3)
        m = doc["measurement"]
        self.assertTrue(m["flat_applied"])
        self.assertEqual(m["e_per_unit_source"], "EGAIN")
        self.assertAlmostEqual(m["e_per_unit_median"], float(np.nanmedian(noise["electrons"])), places=3)
        json.dumps(doc)   # plain JSON

    def test_mono_no_flat_number(self):
        noise = core.photon_noise_setup({"EGAIN": 0.768}, 2, core.MONO, "green", None, True, False)
        doc = onsky_log.build(self._ctx(noise, pattern=core.MONO, flat=False), [self._obs()], "test",
                              header_reads=False)
        self.assertAlmostEqual(doc["frames"][0]["stars"]["C1"]["e_per_unit"], 4 * 0.768, places=4)
        self.assertFalse(doc["measurement"]["flat_applied"])

    def test_mono_camera_binned_uses_the_53_value(self):
        noise = core.photon_noise_setup({"EGAIN": 0.768, "XBINNING": 3}, 2, core.MONO, "green", None, True, False,
                                        sky_k=27.0, sky_level=500.0)
        self.assertEqual(noise["camera_bin_mode"], "averaged")
        doc = onsky_log.build(self._ctx(noise, pattern=core.MONO, flat=False), [self._obs()], "test",
                              header_reads=False)
        self.assertAlmostEqual(doc["frames"][0]["stars"]["C1"]["e_per_unit"], 0.768 * 9 * 4, places=3)
        self.assertEqual(doc["measurement"]["camera_bin_mode"], "averaged")

    def test_no_gain_known_writes_null_and_why(self):
        noise = core.photon_noise_setup({}, 2, "RGGB", "green", None, True, False)
        self.assertEqual(noise["source"], "assumed")
        doc = onsky_log.build(self._ctx(noise), [self._obs()], "test", header_reads=False)
        self.assertIsNone(doc["frames"][0]["stars"]["C1"]["e_per_unit"])
        self.assertIsNone(doc["measurement"]["e_per_unit_median"])
        self.assertIn("no camera gain known", doc["measurement"]["e_per_unit_note"])
        doc = onsky_log.build(self._ctx(None), [self._obs()], "test", header_reads=False)
        self.assertIsNone(doc["measurement"]["e_per_unit_median"])
        self.assertIn("not known", doc["measurement"]["e_per_unit_note"])

    def test_electrons_from_log_match_true_electrons(self):
        """End to end: a synthetic OSC frame with a green flat; flux x e_per_unit = the star's true electrons."""
        rng = np.random.default_rng(2)
        H, W = 120, 160
        yy, xx = np.mgrid[0:H, 0:W]
        resp = np.ones((H, W))
        resp[0::2, 0::2], resp[1::2, 1::2] = 0.6, 0.5
        vign = 1.0 + 0.3 * xx / W
        star_e = 2.0e5                                    # electrons in the GREEN pixels of the star
        x0, y0 = 100.5, 60.5
        psf = np.exp(-((xx - x0) ** 2 + (yy - y0) ** 2) / (2 * 3.0 ** 2))
        green = np.zeros((H, W), bool)
        green[0::2, 1::2] = green[1::2, 0::2] = True
        psf_g = psf * green
        signal = star_e * psf_g / psf_g.sum()
        egain = 0.25
        adu = (signal * vign + 0.0 * rng.normal(size=(H, W))) / egain
        flat_raw = 20000.0 * resp * vign
        master = core.normalize_flat(core.bin_image(flat_raw.astype(np.float32), 1))
        img = core.debayer(adu / master * 1.0, "RGGB", "green")
        noise = core.photon_noise_setup({"EGAIN": egain}, 1, "RGGB", "green", master, True, False)
        m = core.measure_aperture(img, x0 / 2, y0 / 2, 6.0, 9.0, 12.0)
        k = onsky_log.e_per_unit_at(noise["electrons"], x0 / 2, y0 / 2, 6.0)
        detected = float(np.sum(signal * vign))           # what the sensor recorded (raw sums: vignetting in)
        self.assertAlmostEqual(m["flux"] * k / detected, 1.0, delta=0.01)
        # FLUX's version-1 guess (gain x n_color x bin^2, no flat) is off by the green flat at the star
        guess = egain * 2 * 1
        self.assertGreater(detected / (m["flux"] * guess), 1.1)


# ---- item 11 in the app (real Tk) -------------------------------------------------------------------------------

def _have_tk():
    try:
        import tkinter as tk
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


@unittest.skipUnless(_have_tk(), "needs a display")
class TestDarkFlatsBox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        cls.tmp_settings = tempfile.mkdtemp()
        cls.patch = mock.patch.object(ca, "SETTINGS_FILE", os.path.join(cls.tmp_settings, "s.json"))
        cls.patch.start()
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test230c.log")
        cls.app = ca.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        cls.patch.stop()
        shutil.rmtree(cls.tmp_settings, ignore_errors=True)

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        for var in (self.app.bias_dir, self.app.dark_dir, self.app.darkflat_dir, self.app.flat_dir, self.app.light_dir):
            var.set("")
        self.pump(500)
        shutil.rmtree(self.dir, ignore_errors=True)

    def pump(self, ms=500):
        end = time.monotonic() + ms / 1000.0
        while time.monotonic() < end:
            self.app.update()
            time.sleep(0.01)

    def test_box_counts_typed_path(self):
        app = self.app
        folder = os.path.join(self.dir, "df")
        write_set(folder, "Dark Flat", 4, 500.0, 0.5, imagetyp="DARKFLAT")
        self.assertIn("Dark flats", app._counts)
        with mock.patch.object(app, "_frames_folder_picked"):
            app.darkflat_dir.set(folder)
            self.pump(700)
        self.assertEqual(app._counts["Dark flats"].get(), "4 files")
        app._clear_folder(app.darkflat_dir, "Dark flats")
        self.assertEqual(app._counts["Dark flats"].get(), "0 files")
        self.assertIn("Calibration will skip it", app.status.get())

    def test_input_notes(self):
        app = self.app
        d = os.path.join(self.dir, "dark")
        lights = os.path.join(self.dir, "lights")
        flats = os.path.join(self.dir, "flat")
        dfl = os.path.join(self.dir, "df")
        write_set(d, "Dark", 2, 520.0, 110.0)
        write_set(lights, "Light", 2, 900.0, 55.0, imagetyp="Light")
        write_set(flats, "Flat", 2, 20000.0, 0.5)
        write_set(dfl, "Dark Flat", 2, 500.0, 2.0, imagetyp="Dark Flat")
        with mock.patch.object(app, "_frames_folder_picked"), mock.patch.object(app, "_check_target_field"):
            app.dark_dir.set(d)
            app.light_dir.set(lights)
            self.pump(600)
            text = app._update_dark_exposure_note()
            self.assertIn("darks can't be scaled correctly without bias or dark flats", text)
            app.flat_dir.set(flats)
            app.darkflat_dir.set(dfl)
            self.pump(600)
            text = app._update_dark_exposure_note()
            self.assertNotIn("without bias or dark flats", text)
            self.assertIn("Dark flats 2 s, flats 0.5 s", text)

    def test_plan_lines_and_bias_box_holding_dark_flats(self):
        app = self.app
        dfl = os.path.join(self.dir, "df")
        paths = write_set(dfl, "Dark Flat", 2, 500.0, 0.5, imagetyp="Flat Dark")
        app.bias_dir.set(dfl)
        self.assertTrue(app._bias_holds_dark_flats())
        lights = write_set(os.path.join(self.dir, "l"), "Light", 1, 900.0, 55.0, imagetyp="Light")
        darks = write_set(os.path.join(self.dir, "d"), "Dark", 1, 520.0, 55.0)
        flats = write_set(os.path.join(self.dir, "f"), "Flat", 1, 20000.0, 0.5)
        lines = app._calibration_plan_lines(paths, darks, None, flats, [], None, lights)
        self.assertTrue(lines[0].startswith("Calibration: lights = light − bias − (dark − bias)"), lines)
        self.assertTrue(any("Dark flats box is now the place" in t for t in lines), lines)
        app.bias_dir.set("")
        lines = app._calibration_plan_lines([], darks, None, flats, paths, None, lights)
        self.assertIn("no bias is needed", lines[0])
        self.assertIn("flats = flat − dark flat", lines[0])
        self.assertFalse(any(t.startswith("⚠") for t in lines), lines)


if __name__ == "__main__":
    unittest.main()
