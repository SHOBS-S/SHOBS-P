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


if __name__ == "__main__":
    unittest.main()
