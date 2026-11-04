"""2.3.0 review fixes (stacking #54 and friends): regression tests for the reviewer's findings.

Synthetic monochrome frames (no plate solution, no RA/Dec in the headers) unless a test says color (OSC mosaic)."""
import math
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")

import photometry_core as core  # noqa: E402
import stack_core as sc  # noqa: E402
import test_230_a as T  # noqa: E402

try:
    from astropy.io import fits
    from astropy.time import Time
except Exception:   # pragma: no cover
    fits = None


class TestMastersKey(unittest.TestCase):
    def test_rebuilt_masters_change_the_key(self):
        """Fix 2: masters rebuilt from other frames (same frame counts, same 'used' text) give another cache key."""
        rng = np.random.default_rng(1)
        a = core.Masters()
        a.dark = rng.normal(100, 2, (40, 50)).astype(np.float32)
        a.used = {"dark": "dark built from 10 frames"}
        b = core.Masters()
        b.dark = rng.normal(100, 2, (40, 50)).astype(np.float32)
        b.used = dict(a.used)
        c = core.Masters()
        c.dark = a.dark.copy()
        c.used = dict(a.used)
        self.assertNotEqual(sc.masters_fingerprint(a), sc.masters_fingerprint(b))
        self.assertEqual(sc.masters_fingerprint(a), sc.masters_fingerprint(c))
        a.headers = {"dark": {"FPRINT": "x1"}}
        c.headers = {"dark": {"FPRINT": "x2"}}
        self.assertNotEqual(sc.masters_fingerprint(a), sc.masters_fingerprint(c))
        self.assertEqual(sc.masters_fingerprint(core.Masters()), sc.masters_fingerprint(core.Masters()))


class TestOscClip(unittest.TestCase):
    def _mosaic(self, n=12):
        H, W = 120, 120
        lvl = np.zeros((H, W))
        lvl[0::2, 0::2], lvl[0::2, 1::2], lvl[1::2, 0::2], lvl[1::2, 1::2] = 600.0, 1200.0, 1200.0, 500.0
        frames = {}
        for k in range(n):
            r = np.random.default_rng(k)
            frames[f"f{k}"] = (r.poisson(lvl) + r.normal(0, 3, lvl.shape)).astype(np.float32)
        return frames

    def test_cosmic_rays_clipped_on_every_bayer_position(self):
        """Fix 3: on an OSC mosaic (sky R 600, G 1200, B 500) a 300 ADU hit on an R, a G and a B pixel is clipped:
        the stack there is the mean of the other frames x N."""
        n = 12
        frames = self._mosaic(n)
        hits = [(50, 50), (50, 51), (51, 51)]   # R, G, B in RGGB
        clean = {yx: np.mean([frames[f"f{k}"][yx] for k in range(n) if k != 4]) * n for yx in hits}
        for (y, x) in hits:
            frames["f4"][y, x] += 300.0
        img, info = sc.combine(lambda p: frames[p], [{"path": f"f{k}"} for k in range(n)], [(0, 0)] * n, bayer=True)
        for yx in hits:
            self.assertAlmostEqual(float(img[yx]), clean[yx], delta=0.05, msg=str(yx))
        # the old whole-mosaic level let the R and B hits through
        old, _ = sc.combine(lambda p: frames[p], [{"path": f"f{k}"} for k in range(n)], [(0, 0)] * n, bayer=False)
        self.assertGreater(float(old[51, 51]) - clean[(51, 51)], 200)

    def test_osc_clean_mosaic_barely_clipped_and_starlight_kept(self):
        """Fix 3: real noise is not clipped (the floor per Bayer position) and a star that changes from frame to frame
        on a mosaic keeps all of its light."""
        n = 10
        frames = self._mosaic(n)
        yy, xx = np.mgrid[0:120, 0:120]
        for k in range(n):
            amp = 4000.0 * (0.8 + 0.4 * k / (n - 1))
            sig = (2.0 + 1.5 * k / (n - 1)) / 2.355 * 2   # seeing changes by 1.75x (in mosaic pixels)
            frames[f"f{k}"] += (amp * np.exp(-((xx - 60.3) ** 2 + (yy - 60.6) ** 2) / (2 * sig * sig))).astype(np.float32)
        img, info = sc.combine(lambda p: frames[p], [{"path": f"f{k}"} for k in range(n)], [(0, 0)] * n, bayer=True)
        self.assertLess(info["clipped"], 2e-4)
        total = sum(frames.values())
        m = (xx - 60.3) ** 2 + (yy - 60.6) ** 2 <= 12 ** 2
        self.assertAlmostEqual(float(img[m].sum()) / float(total[m].sum()), 1.0, delta=2e-4)

    def test_mono_unchanged(self):
        """Fix 3: a mono frame is combined exactly as before (bayer=False is the default)."""
        rng = np.random.default_rng(3)
        frames = {f"f{k}": rng.normal(400, 20, (60, 80)).astype(np.float32) for k in range(8)}
        frames["f2"][10, 10] += 2000
        a, ia = sc.combine(lambda p: frames[p], [{"path": k} for k in frames], [(0, 0)] * 8)
        b, ib = sc.combine(lambda p: frames[p], [{"path": k} for k in frames], [(0, 0)] * 8, bayer=False)
        self.assertTrue(np.array_equal(a, b))
        self.assertLess(abs(float(a[10, 10]) - 8 * 400), 150)


class TestEnsembleSoleComp(unittest.TestCase):
    def _pts(self):
        nan = float("nan")
        pts = []
        for k in range(20):
            pts.append(core.Obs(jd=2460000.0 + k / 1440, mag=12.0, merr=0.01, flag="", cmag=-8.0, kmag=None,
                                airmass=1.2, flux_var=1e4, flux_comp=2e4, path=f"f{k}", exptime=30.0,
                                night="JD2460000", target_inst=-7.0, target_var=1e-4,
                                comp_vars=[4e-4, 4e-4 if k < 8 else nan], comp_insts=[-8.0, -8.2 if k < 8 else nan]))
        return pts

    def test_sole_comp_in_few_frames_is_kept(self):
        """Fix 5: B alone, measured in 8 of 20 frames: those 8 frames are derived (as in 2.2.21), the rest nocomp."""
        pts = self._pts()
        res = core.recompute_with_comps(pts, ["B"], ["A", "B"], {"A": 11.0, "B": 11.2})
        self.assertEqual(res["done"], 8)
        self.assertEqual(res["missing"], 12)
        self.assertEqual(res["night_out"], {})
        self.assertEqual(res["night_few"], {"JD2460000": ["B"]})
        good = [o for o in pts if math.isfinite(o.mag)]
        self.assertEqual(len(good), 8)
        self.assertAlmostEqual(good[0].mag, 11.2 + (-7.0) - (-8.2), places=9)
        self.assertTrue(all("nocomp" in o.flag for o in pts[8:]))

    def test_rule_unchanged_when_others_remain(self):
        """Fix 5: with A in every frame, B (40 %) is still left out of the night and A carries it."""
        pts = self._pts()
        res = core.recompute_with_comps(pts, ["A", "B"], ["A", "B"], {"A": 11.0, "B": 11.2})
        self.assertEqual(res["done"], 20)
        self.assertEqual(res["night_out"], {"JD2460000": ["B"]})
        self.assertEqual(res["night_few"], {})
        self.assertEqual({o.n_comps for o in pts}, {1})


class TestNoiseCheckNits(unittest.TestCase):
    def test_comp_snr_per_comp_with_a_dropped_comp(self):
        """Fix 8: a frame missing comp 1 does not put comp 2's values under comp 1."""
        pts = []
        for k in range(10):
            cv = [1e-4, 4e-4] if k % 2 else [float("nan"), 4e-4]
            pts.append(T.FakeObs(flag="", mag=12.0, target_var=1e-4, comp_vars=cv))
        res = sc.noise_check(pts)
        self.assertAlmostEqual(res["comp_snr"][0], 1.0857362 / 0.01, places=6)
        self.assertAlmostEqual(res["comp_snr"][1], 1.0857362 / 0.02, places=6)

    def test_short_night_message(self):
        """Fix 8: weak comps on a night too short to stack say so (not 'time resolution')."""
        noise = {"sig_t": 0.001, "sig_c": 0.003, "ratio": 3.0, "snr_t": 1085.7, "snr_c": 361.9, "comp_snr": [300],
                 "sig_point": 0.0032, "n": 15}
        cap = sc.time_cap("discovery")
        cap = dict(cap, seconds=3600.0, known=True, text="1 h")
        sug = sc.suggest_n(noise, 10.0, cap, 15)
        self.assertEqual(sug["n"], 1)
        text, warn = sc.suggest_text("variables", noise, sug, cap, 15, None)
        self.assertTrue(warn)
        self.assertIn("too short to stack", text)
        self.assertNotIn("time resolution", text)
        sug = sc.suggest_n(noise, 10.0, dict(cap, seconds=5.0), 200)
        text, _ = sc.suggest_text("variables", noise, sug, dict(cap, seconds=5.0), 200, None)
        self.assertIn("time resolution allows no stacking", text)


class TestCombineMemory(unittest.TestCase):
    def test_uncovered_and_inf_pixels_and_seeing_widths(self):
        """Fix 9: the in-place combine still treats NaN/inf as uncovered and keeps the plain mean where no value is
        kept; the worker-side seeing widths read a stack file."""
        rng = np.random.default_rng(9)
        frames = {f"f{k}": rng.normal(300, 10, (40, 50)).astype(np.float32) for k in range(6)}
        frames["f0"][3, 3] = np.inf
        frames["f1"][3, 3] = np.nan
        img, info = sc.combine(lambda p: frames[p], [{"path": k} for k in frames], [(0, 0)] * 6)
        vals = [frames[k][3, 3] for k in frames if np.isfinite(frames[k][3, 3])]
        self.assertTrue(np.isfinite(img[3, 3]))
        self.assertAlmostEqual(float(img[3, 3]), float(np.mean(vals)) * 6, delta=1e-2)
        plain, _ = sc.combine(lambda p: frames[p], [{"path": k} for k in list(frames)[:3]], [(0, 0)] * 3)
        self.assertTrue(np.isfinite(plain).all())
        import stack_ui
        tmp = tempfile.mkdtemp()
        try:
            rng = np.random.default_rng(2)
            frame = T.render(rng)
            path = os.path.join(tmp, "stack_0001.fits")
            sc.write_stack(path, frame * 5, {"SHOBSSTK": 1})
            fs, fr = stack_ui._seeing_widths([path], core.MONO, "luminance", frame)
            self.assertTrue(math.isfinite(fs) and math.isfinite(fr))
            self.assertAlmostEqual(fs, fr, delta=0.3)
            self.assertTrue(all(math.isnan(v) for v in stack_ui._seeing_widths([os.path.join(tmp, "gone.fits")],
                                                                                  core.MONO, "luminance", frame)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestFlipGateFailSoft(unittest.TestCase):
    def test_lsq_errors_failure_falls_back_to_no_step(self):
        """Fix 11b: the flip-step test raising (lsq_errors on a singular Jacobian) fits without a step, with a note."""
        import transit_core as tc
        import test_230_b as B
        t, f, e, am, af = B.synth_night(16.0, seed=1)
        real = tc.lsq_errors
        calls = {"n": 0}

        def boom(prob):
            # only inside the flip-step test (its first lsq_errors call); the fit's own errors work
            calls["n"] += 1
            if calls["n"] == 1:
                raise np.linalg.LinAlgError("Singular matrix")
            return real(prob)
        with mock.patch.object(tc, "lsq_errors", side_effect=boom):
            r = tc.lsq_fit(t, f, e, am, B.PLANET, detrend_airmass=False, after_flip=af)
        self.assertIsNone(r["flip_test"])
        self.assertIsNone(r["t_flip"])
        self.assertNotIn("step", r["free"])
        self.assertIn("Singular", r["flip_gate_error"])
        lines = []
        S = {"t_flip": 0.1, "flip_gated": True, "starts": [{"step": 0.0}, {"tc": 0.0}]}
        with mock.patch.object(tc, "flip_step_test", side_effect=np.linalg.LinAlgError("Singular matrix")):
            self.assertIsNone(tc._apply_flip_gate(S, False, False, lines.append))
        self.assertIsNone(S["t_flip"])
        self.assertEqual(S["starts"], [{"tc": 0.0}])
        self.assertTrue(lines and "could not be done" in lines[0])


def _have_tk():
    try:
        import tkinter as tk
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


HAVE_TK = _have_tk()


@unittest.skipUnless(HAVE_TK and fits is not None, "needs a display and astropy")
class TestStackFixesInApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test230fix.log")
        cls.cache = tempfile.mkdtemp()
        cls._old_cache = sc.STACK_CACHE_DIR
        sc.STACK_CACHE_DIR = os.path.join(cls.cache, "stacks")
        cls._onsky = ca.onsky_log
        ca.onsky_log = None
        cls.app = ca.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        cls.ca.onsky_log = cls._onsky
        sc.STACK_CACHE_DIR = cls._old_cache
        shutil.rmtree(cls.cache, ignore_errors=True)

    def pump_until(self, cond, timeout=180.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.update()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def _setup_run(self, lights):
        app = self.app
        app.set_mode("variables")
        app.light_dir.set(lights)
        app.pattern.set(core.MONO)
        app.binning.set(1)
        app.star_id.set("")
        app.masters = core.Masters()
        app.masters_binning = 1
        first = os.path.join(lights, "light_0000.fits")
        raw, head = core.read_fits(first)
        app.preview = raw.astype(np.float32)
        app.preview_header = head
        app.preview_path = first
        app.preview_is_debayered = True
        app.calibrated = app.preview
        app.radius.set(4.0)
        app.sky_in.set(8.0)
        app.sky_out.set(13.0)
        app.target_xy = (T.TARGET[0], T.TARGET[1])
        app.comp_xy = T.COMPS[0][:2]
        app.comp_name.set("C1")
        app.comp_mag.set("11.0")
        app.comp2_xy = T.COMPS[1][:2]
        app.comp2_name.set("C2")
        app.comp2_mag.set("11.0")
        app.check_xy = None
        app.check_name.set("")

    def _write_lights(self, lights, n_frames=60, exp=2.0, cad=3.0, t0=2460950.6):
        rng = np.random.default_rng(21)
        for i in range(n_frames):
            img = T.render(rng, dx=0.03 * i, dy=0.02 * i, flipped=False)
            hdr = fits.Header()
            hdr["EXPTIME"] = exp
            hdr["DATE-OBS"] = Time(t0 + i * cad / 86400 - exp / 2 / 86400, format="jd", scale="utc").isot
            hdr["EGAIN"] = 1.0
            fits.PrimaryHDU(np.clip(img + 500, 0, 65535).astype(np.uint16), header=hdr).writeto(
                os.path.join(lights, f"light_{i:04d}.fits"))

    def test_discovery_stacks_flag_saturation_from_members(self):
        """Fix 11c: a star whose raw core clips in the second half of the night (not on the reference) is saturated
        in the scan on the stacks too (from the member frames' raw cores), and the stacks' summed peaks do not make
        every bright star 'near saturation'."""
        ca, app = self.ca, self.app
        tmp = tempfile.mkdtemp()
        lights = os.path.join(tmp, "lights")
        os.makedirs(lights)
        rng = np.random.default_rng(8)
        exp, cad, t0, limit = 2.0, 3.0, 2460960.6, 6000
        for i in range(60):
            img = T.render(rng, dx=0.02 * i, dy=0.01 * i, field=T.RICH, target_scale=1.0 if i < 30 else 3.0)
            hdr = fits.Header()
            hdr["EXPTIME"] = exp
            hdr["DATE-OBS"] = Time(t0 + i * cad / 86400 - exp / 2 / 86400, format="jd", scale="utc").isot
            fits.PrimaryHDU(np.clip(img + 500, 0, limit).astype(np.uint16), header=hdr).writeto(
                os.path.join(lights, f"light_{i:04d}.fits"))
        old_mode, old_sat = app.mode_key, app.sat_limit.get()
        try:
            with mock.patch.object(ca.messagebox, "askyesno", return_value=True), \
                    mock.patch.object(ca.messagebox, "askyesnocancel", return_value=False), \
                    mock.patch.object(ca.messagebox, "showinfo"), mock.patch.object(ca.messagebox, "showwarning"), \
                    mock.patch.object(ca.messagebox, "showerror") as err, \
                    mock.patch.object(app, "label_aavso"):
                app.set_mode("discovery")
                app.light_dir.set(lights)
                app.pattern.set(core.MONO)
                app.binning.set(1)
                app.sat_limit.set(str(limit))
                app.masters = core.Masters()
                app.masters_binning = 1
                first = os.path.join(lights, "light_0000.fits")
                raw, head = core.read_fits(first)
                app.preview = raw.astype(np.float32)
                app.preview_header, app.preview_path, app.preview_is_debayered = head, first, True
                app.calibrated = app.preview
                app.radius.set(4.0)
                app.sky_in.set(8.0)
                app.sky_out.set(13.0)
                app.last_scan = None
                app.scan_field()
                self.assertTrue(self.pump_until(lambda: not app.busy and getattr(app, "last_scan", None) is not None
                                                and app._stack_shown), "per-frame scan")
                self.assertFalse(err.called, err.call_args)
                frame_scan = app.last_scan
                xy = np.asarray(frame_scan["xy"], dtype=float)
                k = int(np.argmin(np.hypot(xy[:, 0] - T.TARGET[0], xy[:, 1] - T.TARGET[1])))
                self.assertIn(k, frame_scan["saturated"])
                app.stack_n.set("6")
                app._stack_n_typed = True
                app.start_stack()
                self.assertTrue(self.pump_until(lambda: not app.busy and app.points_choice.get() == "stack"
                                                and app.last_scan is not frame_scan, 180), "stacked scan")
                self.assertFalse(err.called, err.call_args)
                st = app.last_scan
                self.assertIn(k, st["saturated"])
                self.assertEqual(set(st["saturated"]), set(frame_scan["saturated"]))
                self.assertLessEqual(len(st["near_sat"]), len(frame_scan["near_sat"]) + 1)
        finally:
            app.sat_limit.set(old_sat)
            with mock.patch.object(ca.messagebox, "askyesno", return_value=True):
                app.set_mode(old_mode)
            app.update()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_wrap_floors_scale_with_display(self):
        """Fix 10: the Stack line's and the skip line's wrap floors follow the display scaling (200 % here)."""
        app = self.app
        old = float(app.tk.call("tk", "scaling"))
        try:
            app.tk.call("tk", "scaling", 1.3333 * 2)
            app._stack_row.event_generate("<Configure>", width=40, height=10)
            app.skip_line.event_generate("<Configure>", width=40, height=10)
            app.update_idletasks()
            self.assertAlmostEqual(int(str(app.stack_line_label.cget("wraplength"))), 600, delta=3)
            self.assertAlmostEqual(int(str(app.skip_label.cget("wraplength"))), 600, delta=3)
        finally:
            app.tk.call("tk", "scaling", old)

    def test_darkflat_notes_cached(self):
        """Fix 9: the dark-flat note reads the folders' headers once, then again only when a folder changes."""
        ca, app = self.ca, self.app
        tmp = tempfile.mkdtemp()
        try:
            df, fl = os.path.join(tmp, "df"), os.path.join(tmp, "flat")
            os.makedirs(df)
            os.makedirs(fl)
            for folder, exp, typ in ((df, 1.0, "DARKFLAT"), (fl, 2.0, "FLAT")):
                for i in range(3):
                    h = fits.Header()
                    h["EXPTIME"] = exp
                    h["IMAGETYP"] = typ
                    fits.PrimaryHDU(np.zeros((8, 8), dtype=np.uint16), header=h).writeto(
                        os.path.join(folder, f"{typ.lower()}_{i}.fits"))
            app.bias_dir.set("")
            app.darkflat_dir.set(df)
            app.flat_dir.set(fl)
            real = core.read_header
            calls = []
            with mock.patch.object(core, "read_header", side_effect=lambda p, *a, **k: calls.append(p) or real(p, *a, **k)):
                a = app._darkflat_notes(None, None)
                n1 = len(calls)
                self.assertGreater(n1, 0)
                b =app._darkflat_notes(None, None)
                self.assertEqual(len(calls), n1)
                self.assertEqual(a, b)
                self.assertTrue(any("Dark flats 1 s, flats 2 s" in t for t in a), a)
                h = fits.Header()
                h["EXPTIME"] = 1.0
                fits.PrimaryHDU(np.zeros((8, 8), dtype=np.uint16), header=h).writeto(os.path.join(fl, "zz.fits"))
                os.utime(fl, ns=(time.time_ns() + 10 ** 9, time.time_ns() + 10 ** 9))
                app._darkflat_notes(None, None)
                self.assertGreater(len(calls), n1)
        finally:
            app.darkflat_dir.set("")
            app.flat_dir.set("")
            shutil.rmtree(tmp, ignore_errors=True)

    def test_restack_keeps_per_frame_points(self):
        """Fix 1: Stack pressed again while Points: Stacked is shown keeps the per-frame points as 'Per frame'."""
        ca, app = self.ca, self.app
        tmp = tempfile.mkdtemp()
        lights = os.path.join(tmp, "lights")
        os.makedirs(lights)
        self._write_lights(lights)
        try:
            with mock.patch.object(ca.messagebox, "askyesno", return_value=True), \
                    mock.patch.object(ca.messagebox, "askyesnocancel", return_value=False), \
                    mock.patch.object(ca.messagebox, "showinfo"), mock.patch.object(ca.messagebox, "showwarning"), \
                    mock.patch.object(ca.messagebox, "showerror") as err:
                self._setup_run(lights)
                app.run_photometry()
                self.assertTrue(self.pump_until(lambda: not app.busy and app.observations), "per-frame run")
                night = app.observations[0].night
                frames = [o for o in app.observations if o.night == night]
                n_pts = len(frames)
                app.stack_n.set("5")
                app._stack_n_typed = True
                app.start_stack()
                self.assertTrue(self.pump_until(lambda: not app.busy and app._stack_set_live(night)
                                                and app.points_choice.get() == "stack"), "stack 1")
                first_stacks = [o for o in app.observations if o.night == night]
                app.radius.set(5.0)
                app.start_stack()
                self.assertTrue(self.pump_until(lambda: not app.busy and app.points_choice.get() == "stack"
                                                and app._stack_set_live(night)
                                                and {id(o) for o in app.observations if o.night == night}
                                                != {id(o) for o in first_stacks}), "stack 2")
                self.assertFalse(err.called, err.call_args)
                app.points_choice.set("frame")
                app._points_switched()
                pts = [o for o in app.observations if o.night == night]
                self.assertEqual(len(pts), n_pts)
                self.assertFalse(any(os.path.basename(o.path).startswith("stack_") for o in pts))
                self.assertEqual({id(o) for o in pts}, {id(o) for o in frames})
        finally:
            with mock.patch.object(app, "_offer_series_save", return_value=True):
                if app.observations:
                    app.close_series(asked=True)
            app.update()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_switch_carries_flags_and_catalog_and_measure_skipped(self):
        """Fix 6: transit flags and catalog magnitudes changed while the stacks are shown follow to the per-frame
        points on a switch. Fix 4: Measure skipped frames on a stacked night switches to Per frame first and drops
        the stacks once the frames are in; a stacked night without per-frame points is refused."""
        import copy
        ca, app = self.ca, self.app
        tmp = tempfile.mkdtemp()
        lights = os.path.join(tmp, "lights")
        os.makedirs(lights)
        self._write_lights(lights)
        try:
            with mock.patch.object(ca.messagebox, "askyesno", return_value=True), \
                    mock.patch.object(ca.messagebox, "askyesnocancel", return_value=False), \
                    mock.patch.object(ca.messagebox, "showinfo") as info_box, \
                    mock.patch.object(ca.messagebox, "showwarning"), \
                    mock.patch.object(ca.messagebox, "showerror") as err:
                self._setup_run(lights)
                app.run_photometry()
                self.assertTrue(self.pump_until(lambda: not app.busy and app.observations), "per-frame run")
                night = app.observations[0].night
                frames = sorted([o for o in app.observations if o.night == night], key=lambda o: o.jd)
                mag0 = {id(o): o.mag for o in frames}
                app.stack_n.set("5")
                app._stack_n_typed = True
                app.start_stack()
                self.assertTrue(self.pump_until(lambda: not app.busy and app._stack_set_live(night)
                                                and app.points_choice.get() == "stack"), "stack")
                self.assertFalse(err.called, err.call_args)
                # --- fix 6: flags and catalog changes made on the stacks
                lo, hi = frames[10].jd - 1e-6, frames[19].jd + 1e-6
                info = app._night_info(night, create=True)
                info["planet_transits"] = [{"planet": "b", "windows": [[lo, hi]]}]
                app._set_transit_flags(night, True, quiet=True)
                self.assertTrue(any("transit" in o.flag.split() for o in app.observations if o.night == night))
                self.assertTrue(app.set_catalog_mags({"C1": 11.1}))
                app.points_choice.set("frame")
                app._points_switched()
                pts = sorted([o for o in app.observations if o.night == night], key=lambda o: o.jd)
                self.assertEqual({id(o) for o in pts}, {id(o) for o in frames})
                flagged = [o for o in pts if "transit" in o.flag.split()]
                self.assertEqual(len(flagged), 10)
                self.assertTrue(all(lo <= o.jd <= hi for o in flagged))
                shifts = [o.mag - mag0[id(o)] for o in pts if math.isfinite(o.mag) and math.isfinite(mag0[id(o)])]
                self.assertTrue(shifts)
                self.assertTrue(all(abs(s - 0.05) < 1e-6 for s in shifts), shifts[:5])
                # back to the stacks: they were changed already, nothing applied twice
                st_before = {id(o): o.mag for o in app._stack_sets[night]["stack"]}
                app.points_choice.set("stack")
                app._points_switched()
                self.assertTrue(all(o.mag == st_before[id(o)] or not math.isfinite(o.mag)
                                    for o in app.observations if o.night == night))
                info["transit_flagged"] = False
                app._set_transit_flags(night, False, quiet=True)
                info.pop("planet_transits", None)
                info.pop("transit_flagged", None)
                # --- fix 4: Measure skipped frames while the stacks are shown
                extra = frames[0].path
                app._prev_stars = (list(app.comps_used), app.check_used)
                with mock.patch.object(app, "recoverable_skips", return_value={"C9": [extra]}), \
                        mock.patch.object(app, "_lights", return_value=[extra]), \
                        mock.patch.object(app, "run_photometry") as run:
                    app.measure_skipped_frames()
                    run.assert_called_once()
                    self.assertEqual(run.call_args.kwargs.get("only"), [extra])
                self.assertEqual(app.points_choice.get(), "frame")
                self.assertEqual({id(o) for o in app.observations if o.night == night}, {id(o) for o in frames})
                new = copy.copy(frames[-1])
                new.jd = frames[-1].jd + 3.0 / 86400
                new.path = os.path.join(lights, "light_extra.fits")
                app._remeasure_done({"night": night}, [new], "variables")
                self.assertNotIn(night, app._stack_sets)
                self.assertIsNone(app.stack_meta(night))
                self.assertEqual(len(app._stack_source["points"]), len(frames) + 1)
                self.assertTrue(app._stack_source_live())   # Stack can be pressed again
                # a stacked night whose per-frame points are not loaded (saved series): refused
                app._night_info(night, create=True)["stack"] = {"n": 5}
                info_box.reset_mock()
                with mock.patch.object(app, "recoverable_skips", return_value={"C9": [extra]}), \
                        mock.patch.object(app, "_lights", return_value=[extra]), \
                        mock.patch.object(app, "run_photometry") as run:
                    app.measure_skipped_frames()
                    run.assert_not_called()
                self.assertTrue(info_box.called)
                self.assertIn("are stacks", info_box.call_args.args[1])
                app._night_info(night).pop("stack", None)
        finally:
            with mock.patch.object(app, "_offer_series_save", return_value=True):
                if app.observations:
                    app.close_series(asked=True)
            app.update()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
