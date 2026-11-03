"""2.3.0 builder A, GitHub #54: stacking frames when the comps are weak (all modes).

Synthetic monochrome frames (no plate solution, no RA/Dec in the headers): the weak-comp noise check and the
suggested N, the time caps, groups that never cross a flip or a gap, the sigma-clipped mean (a cosmic ray goes, the
mean is a mean and not a median, starlight that changes with seeing is not clipped), flux conserved under the
whole-pixel shifts, the error model of a stack (errors fall as 1/sqrt(N) and match the scatter), and in the real
window: the Stack row, the Points switch and its "Results use" line, end to end on files."""
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

import photometry_core as core  # noqa: E402
import stack_core as sc  # noqa: E402

try:
    from astropy.io import fits
    from astropy.time import Time
except Exception:   # pragma: no cover
    fits = None

H, W = 120, 160
FIELD = [(20, 20, 40000), (140, 25, 30000), (30, 100, 35000), (130, 95, 45000), (80, 15, 25000),
         (15, 60, 20000), (145, 60, 22000), (60, 105, 28000)]
TARGET = (80.0, 60.0, 60000.0)
COMPS = [(50.0, 45.0, 6000.0), (110.0, 75.0, 6000.0)]
_r = np.random.default_rng(230)
RICH = FIELD + [(float(x), float(y), float(f)) for x, y, f in zip(_r.uniform(22, 138, 30), _r.uniform(22, 98, 30),
                                                                   _r.uniform(15000, 60000, 30))]   # a scan needs 8+


def render(rng, dx=0.0, dy=0.0, flipped=False, seeing=1.8, sky=200.0, rn=10.0, gain=1.0, target_scale=1.0,
           noise=True, field=None):
    """One calibrated frame in ADU (gain e-/ADU): Gaussian stars, Poisson + read noise."""
    yy, xx = np.mgrid[0:H, 0:W]
    img = np.full((H, W), sky * gain, dtype=np.float64)
    stars = (field or FIELD) + [(TARGET[0], TARGET[1], TARGET[2] * target_scale)] + COMPS
    for x, y, f in stars:
        if flipped:
            x, y = W - 1 - x, H - 1 - y
        x, y = x + dx, y + dy
        img += f * gain / (2 * math.pi * seeing ** 2) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * seeing ** 2))
    if noise:
        img = rng.poisson(img).astype(np.float64) + rng.normal(0, rn * gain, img.shape)
    img = img / gain - sky   # ADU, sky level taken out as a bias would leave it (sky stays in: add back)
    return (img + sky).astype(np.float32)


def star_at(xy, reg, shape, name, mag):
    st = core.Star(name=name, x=xy[0], y=xy[1], catalog_mag=mag)
    return core.move_star(st, reg, shape)


def header_for(jd_mid, exp, gain=1.0):
    return {"EXPTIME": exp, "JD-MID": jd_mid, "EGAIN": gain}


class FakeObs:
    def __init__(self, **kw):
        self.__dict__.update(dict(mag=12.0, flag="", segment="", exptime=2.0, raw_peak=1000.0, raw_peaks={}), **kw)


class TestRules(unittest.TestCase):
    def test_time_caps(self):
        planet = {"period": 3.0, "a_rs": 9.0, "k": 0.1, "b": 0.3}
        tau = sc.ingress_days(planet)
        # (T14 - T23) / 2 against the textbook approximation T14 * k / ((1 + k)(1 - b^2)) within 15 %
        t14 = planet["period"] / math.pi * math.asin(math.sqrt(1.1 ** 2 - 0.09) / (9.0 * math.sin(math.acos(0.3 / 9))))
        self.assertAlmostEqual(tau, t14 * 0.1 / (1.1 * (1 - 0.09)), delta=0.15 * tau)
        cap = sc.time_cap("transits", planet)
        self.assertTrue(cap["known"])
        self.assertAlmostEqual(cap["seconds"], tau * 86400 / 5, places=6)
        self.assertIn("ingress", cap["text"])
        only_t14 = sc.time_cap("transits", {"period": 3.0, "t14": 0.1, "k": 0.1})
        self.assertAlmostEqual(only_t14["seconds"], 0.1 * 0.1 / 1.1 * 86400 / 5, places=6)
        v = sc.time_cap("variables", None, 0.5)
        self.assertAlmostEqual(v["seconds"], 0.5 * 86400 / 50)
        self.assertEqual(sc.time_cap("discovery")["seconds"], 300.0)
        self.assertIn("hides events", sc.time_cap("discovery")["text"])
        for mode in ("transits", "variables"):
            d = sc.time_cap(mode, None, None)
            self.assertFalse(d["known"])
            self.assertIn("conservative default", d["text"])

    def test_weak_comps_suggest_n(self):
        # target noise 2 mmag, comp ensemble 10 mmag per frame: comps dominate, N = (10 / (0.5 * 2))^2 = 100
        pts = [FakeObs(jd=2460000.0 + i * 3 / 86400, target_var=0.002 ** 2, comp_vars=[0.01414 ** 2, 0.01414 ** 2])
               for i in range(2000)]
        noise = sc.noise_check(pts)
        self.assertAlmostEqual(noise["sig_c"], 0.01, delta=2e-4)
        self.assertAlmostEqual(noise["ratio"], 5.0, delta=0.1)
        recs = [sc.frame_record(o, {"dx": 0, "dy": 0, "angle": 0, "flipped": False}) for o in pts]
        cad = sc.cadence_seconds(recs)
        self.assertAlmostEqual(cad, 3.0, places=3)
        cap = sc.time_cap("variables", None, 1.0)   # 1728 s -> 576 frames
        sug = sc.suggest_n(noise, cad, cap, len(pts))
        self.assertTrue(sug["dominate"])
        self.assertEqual(sug["n_rule"], 100)
        self.assertEqual(sug["n"], 100)
        self.assertFalse(sug["capped"])
        self.assertEqual(sug["n_stacks"], 20)
        self.assertAlmostEqual(sug["snr_c"], noise["snr_c"] * 10, places=6)
        text, warn = sc.suggest_text("variables", noise, sug, cap, len(pts), None)
        self.assertTrue(warn)
        self.assertTrue(text.startswith("⚠ Comps are weak"))
        self.assertIn("Stack 100 frames", text)
        self.assertIn("2000 frames → 20 points", text)
        # never fewer than 10 points from a night: 400 frames -> at most 40 per stack, and the line says why
        s40 = sc.suggest_n(noise, cad, cap, 400)
        self.assertEqual(s40["n"], 40)
        self.assertTrue(s40["few_points"])
        self.assertIn("at least 10 points", sc.suggest_text("variables", noise, s40, cap, 400, None)[0])
        # the time cap stops it: 1/50 of a 0.1-d period = 172.8 s = 57 frames, and the line says so
        cap2 = sc.time_cap("variables", None, 0.1)
        sug2 = sc.suggest_n(noise, cad, cap2, len(pts))
        self.assertTrue(sug2["capped"])
        self.assertEqual(sug2["n"], 57)
        text2, _ = sc.suggest_text("variables", noise, sug2, cap2, len(pts), None)
        self.assertIn("Capped at 57 frames", text2)
        # a precision goal makes N grow (comps fine here: target-dominated per frame)
        fine = [FakeObs(jd=2460000.0 + i * 3 / 86400, target_var=0.004 ** 2, comp_vars=[0.002 ** 2]) for i in range(400)]
        nf = sc.noise_check(fine)
        s0 = sc.suggest_n(nf, cad, cap, 400)
        self.assertFalse(s0["dominate"])
        self.assertEqual(s0["n"], 1)
        t0, w0 = sc.suggest_text("variables", nf, s0, cap, 400, None)
        self.assertFalse(w0)
        self.assertIn("Comps are fine per frame", t0)
        sg = sc.suggest_n(nf, cad, cap, 400, goal_ppt=1.0)
        self.assertEqual(sg["n_goal"], math.ceil((nf["sig_point"] / 1.0857362e-3) ** 2 - 1e-9))
        self.assertTrue(sg["goal_met"])
        tg, wg = sc.suggest_text("variables", nf, sg, cap, 400, 1.0)
        self.assertTrue(wg)
        self.assertIn("1 ppt goal", tg)

    def test_groups_never_cross_flip_or_gap(self):
        recs = []
        for i in range(50):
            jd = 2460000.0 + i * 10 / 86400 + (0.01 if i >= 30 else 0.0)   # a 14-min gap before frame 30
            flipped = i >= 20
            recs.append({"path": f"f{i}", "jd": jd, "exp": 8.0, "segment": "flip" if flipped else "", "keep": i != 5,
                         "why": "" if i != 5 else "sat", "dx": 0.0, "dy": 0.0, "angle": 0.0, "flipped": flipped,
                         "raw_peak": 1.0, "raw_peaks": {}})
        groups = sc.plan_groups(recs, 8)
        for g in groups:
            sides = {(m["segment"], m["flipped"]) for m in g["members"]}
            self.assertEqual(len(sides), 1, "a stack crosses the flip")
            jds = [m["jd"] for m in g["members"]]
            self.assertLess((max(jds) - min(jds)) * 86400, 8 * 10 + 1, "a stack crosses the gap")
            self.assertNotIn("f5", [m["path"] for m in g["members"]])   # the saturated frame is left out
        sizes = [len(g["members"]) for g in groups]
        self.assertEqual(sum(sizes), 49)
        # 0-19 minus f5 = 19 frames -> 8, 8, 3 (partial before the flip); 20-29 -> 8, 2 (partial before the gap); 30-49 -> 8, 8, 4
        self.assertEqual(sizes, [8, 8, 3, 8, 2, 8, 8, 4])
        self.assertEqual([g["partial"] for g in groups], [False, False, True, False, True, False, False, True])

    def test_report_words(self):
        meta = {"n": 40, "exp": 30.0, "partial": 1, "smallest": 12}
        self.assertEqual(sc.report_note(meta), "points are stacks of 40 frames (effective exposure 30 s)")
        self.assertIn("1 partial (smallest 12 frames)", sc.stack_words(meta))
        self.assertEqual(sc.report_note({"n": 1}), "")
        self.assertTrue(all(ord(c) < 128 for c in sc.report_note(meta)) and "," not in sc.report_note(meta))


class TestCombine(unittest.TestCase):
    def test_cosmic_ray_clipped_and_mean_not_median(self):
        rng = np.random.default_rng(3)
        n = 9
        frames = {f"f{i}": render(rng, seeing=1.8) for i in range(n)}
        frames["f4"][10, 70] += 8000.0          # a cosmic ray on the sky
        frames["f6"][60, 82] += 50000.0         # a cosmic ray on the target's wing, brighter than the star there
        mem = [{"path": k} for k in frames]
        out, info = sc.combine(lambda p: frames[p], mem, [(0, 0)] * n)
        plain = sum(frames.values())
        clean_sky = (plain[10, 70] - 8000.0) / n
        self.assertLess(abs(out[10, 70] / n - clean_sky), 15.0)        # the hit is gone (read noise ~10)
        self.assertLess(abs(out[60, 82] / n - (plain[60, 82] - 50000.0) / n), 0.1 * (plain[60, 82] - 50000.0) / n)
        self.assertFalse(info["plain"])
        self.assertLess(info["clipped"], 0.002)
        # a pixel with no outlier is the MEAN of its values, not their median
        vals = np.array([frames[k][30, 30] for k in frames], dtype=np.float64)
        self.assertAlmostEqual(out[30, 30] / n, vals.mean(), delta=1e-3 * abs(vals.mean()))
        skew = {f"s{i}": np.full((8, 8), v, dtype=np.float32) for i, v in enumerate((100, 101, 102, 104, 106, 113))}
        skew["s0"][:4] += np.arange(32, dtype=np.float32).reshape(4, 8) * 0.01   # a field, not one flat value
        o2, _ = sc.combine(lambda p: skew[p], [{"path": k} for k in skew], [(0, 0)] * 6)
        vals2 = np.array([skew[k][6, 6] for k in skew], dtype=np.float64)
        self.assertAlmostEqual(o2[6, 6] / 6, vals2.mean(), places=3)
        self.assertNotAlmostEqual(o2[6, 6] / 6, float(np.median(vals2)), places=1)
        # fewer than 5 frames: a plain mean, even with a hit
        small = {k: frames[k] for k in list(frames)[:4]}
        o3, i3 = sc.combine(lambda p: small[p], [{"path": k} for k in small], [(0, 0)] * 4)
        self.assertTrue(i3["plain"])
        self.assertTrue(np.allclose(o3, sum(small.values()), rtol=1e-5, atol=1e-2))

    def test_seeing_changes_not_clipped(self):
        rng = np.random.default_rng(5)
        yy, xx = np.mgrid[0:H, 0:W]
        r = (xx - TARGET[0]) ** 2 + (yy - TARGET[1]) ** 2 <= 36
        diffs = []
        for trial in range(10):
            n = 6
            frames = {f"f{i}": render(rng, seeing=rng.uniform(1.5, 2.5)) for i in range(n)}   # 1.7x within a stack
            out, _ = sc.combine(lambda p: frames[p], [{"path": k} for k in frames], [(0, 0)] * n)
            plain = sum(frames.values())
            diffs.append(out[r].sum() / plain[r].sum() - 1)
        # far below the target's photon noise in the stack (~1.7 ppt)
        self.assertLess(float(np.max(np.abs(diffs))), 2e-4)

    def test_flux_conserved_under_shift(self):
        rng = np.random.default_rng(1)
        drifts = [(0.0, 0.0), (1.0, -2.0), (3.0, 1.0), (-2.0, 2.0), (4.0, -1.0), (2.0, 3.0)]
        frames, recs = {}, []
        for i, (dx, dy) in enumerate(drifts):
            frames[f"f{i}"] = render(rng, dx=dx, dy=dy, noise=False)
            recs.append({"path": f"f{i}", "dx": dx, "dy": dy, "angle": 0.0})
        shifts, anchor, turn = sc.group_shifts(recs, 1)
        self.assertEqual(anchor, 3)
        self.assertEqual(shifts[anchor], (0, 0))
        out, _ = sc.combine(lambda p: frames[p], recs, shifts)
        ax, ay = drifts[anchor]
        # the target's aperture flux in the stack = the sum of the frames' own aperture fluxes
        stack_flux = core.measure_aperture(out, TARGET[0] + ax, TARGET[1] + ay, 6.0, 10.0, 15.0)["flux"]
        frame_sum = sum(core.measure_aperture(frames[f"f{i}"], TARGET[0] + dx, TARGET[1] + dy, 6.0, 10.0, 15.0)["flux"]
                        for i, (dx, dy) in enumerate(drifts))
        self.assertAlmostEqual(stack_flux / frame_sum, 1.0, delta=1e-5)
        # and the stars stay sharp: the stack's star is where the anchor frame's is
        x, y, moved = core.recenter(out, TARGET[0] + ax, TARGET[1] + ay, 6.0)
        self.assertLess(moved, 0.1)
        # color camera: whole 2x2 cells (the Bayer pattern is kept)
        sh2, _, _ = sc.group_shifts(recs, 2)
        self.assertTrue(all(a % 2 == 0 and b % 2 == 0 for a, b in sh2))


@unittest.skipIf(fits is None, "needs astropy")
class TestStackPhotometry(unittest.TestCase):
    """Frames -> registration -> stacks (files) -> reduce_frame: errors fall as 1/sqrt(N) and match the scatter."""

    def test_snr_improves_sqrt_n_and_errors_fit(self):
        rng = np.random.default_rng(11)
        tmp = tempfile.mkdtemp()
        try:
            n_frames, n, exp, cad = 240, 10, 2.0, 3.0
            jd0 = 2460900.5
            ref = render(rng)
            ref_src = core.detect_sources(ref, max_sources=80)
            frames, obs_list = {}, []
            target = core.Star("T", TARGET[0], TARGET[1])
            comps = [core.Star(f"C{k + 1}", x, y, catalog_mag=11.0) for k, (x, y, _f) in enumerate(COMPS)]
            for i in range(n_frames):
                dx, dy = 0.02 * i, -0.015 * i    # slow drift: up to ~5 px
                img = render(rng, dx=dx, dy=dy, seeing=rng.uniform(1.6, 2.2))
                path = os.path.join(tmp, f"f{i:04d}")
                frames[path] = img
                reg = core.register_frame(ref_src, core.detect_sources(img, max_sources=80), img.shape)
                self.assertIsNotNone(reg)
                h = header_for(jd0 + i * cad / 86400, exp)
                o = core.reduce_frame(img, h, core.move_star(target, reg, img.shape),
                                      [core.move_star(c, reg, img.shape) for c in comps], None, 4.0, 8.0, 13.0,
                                      sat_limit=float("inf"), electrons=1.0)
                o.path = path
                o._stack_reg = reg
                obs_list.append(o)
            recs = [sc.frame_record(o) for o in obs_list]
            self.assertTrue(all(r["keep"] for r in recs))
            # the weak-comp check on these frames
            noise = sc.noise_check(obs_list)
            self.assertGreater(noise["ratio"], 1.0)   # comps dominate
            sug = sc.suggest_n(noise, sc.cadence_seconds(recs), sc.time_cap("variables", None, None), n_frames)
            self.assertEqual(sug["n_rule"], math.ceil((noise["sig_c"] / (0.5 * noise["sig_t"])) ** 2 - 1e-9))
            groups = sc.plan_groups(recs, n)
            self.assertEqual(len(groups), n_frames // n)
            stack_obs = []
            for gi, g in enumerate(groups, 1):
                shifts, a, _ = sc.group_shifts(g["members"], 1)
                img, info = sc.combine(lambda p: frames[p], g["members"], shifts)
                head = sc.stack_header(header_for(0, exp), g["members"], info, [m["jd"] for m in g["members"]], gi,
                                       len(groups), g["partial"])
                path = os.path.join(tmp, "stacks", f"stack_{gi:04d}.fits")
                sc.write_stack(path, img, head)
                simg, shead = sc.read_stack(path)
                self.assertEqual(int(shead["STACKN"]), n)
                self.assertAlmostEqual(float(shead["EXPTIME"]), n * exp)
                self.assertAlmostEqual(core.mid_jd(shead), float(np.mean([m["jd"] for m in g["members"]])), places=7)
                reg = core.register_frame(ref_src, core.detect_sources(simg, max_sources=80), simg.shape)
                so = core.reduce_frame(simg, shead, core.move_star(target, reg, simg.shape),
                                       [core.move_star(c, reg, simg.shape) for c in comps], None, 4.0, 8.0, 13.0,
                                       sat_limit=float("inf"), electrons=1.0)
                sc.apply_member_peaks(so, g["members"])
                stack_obs.append(so)
            f_err = float(np.median([o.merr for o in obs_list]))
            s_err = float(np.median([o.merr for o in stack_obs]))
            # predicted errors of a mean of N frames: 1/sqrt(N) (photon, sky and read noise all counted per frame)
            self.assertAlmostEqual(s_err / f_err, 1 / math.sqrt(n), delta=0.12 / math.sqrt(n))
            # the real scatter falls the same way, and the stacks' error bars describe it
            f_sd = float(np.std([o.mag for o in obs_list], ddof=1))
            s_sd = float(np.std([o.mag for o in stack_obs], ddof=1))
            self.assertAlmostEqual(s_sd / f_sd, 1 / math.sqrt(n), delta=0.35 / math.sqrt(n))
            self.assertAlmostEqual(s_sd / s_err, 1.0, delta=0.35)
            self.assertAlmostEqual(f_sd / f_err, 1.0, delta=0.15)
            # SNR of the comps in the stack ~ sqrt(N) better
            c_f = 1.0857 / math.sqrt(float(np.median([v[0] for v in (o.comp_vars for o in obs_list)])))
            c_s = 1.0857 / math.sqrt(float(np.median([v[0] for v in (o.comp_vars for o in stack_obs)])))
            self.assertAlmostEqual(c_s / c_f, math.sqrt(n), delta=0.12 * math.sqrt(n))
            # the instrumental magnitudes stay on the frames' scale (sum scale / exposure sum)
            self.assertAlmostEqual(float(np.median([o.target_inst for o in stack_obs])),
                                   float(np.median([o.target_inst for o in obs_list])), delta=0.01)
            self.assertEqual(stack_obs[0].exptime, n * exp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


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
class TestStackInApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test230a.log")
        cls.cache = tempfile.mkdtemp()
        cls._old_cache = sc.STACK_CACHE_DIR
        sc.STACK_CACHE_DIR = os.path.join(cls.cache, "stacks")
        cls._onsky = ca.onsky_log
        ca.onsky_log = None   # no on-sky log files from the test runs
        cls.app = ca.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        cls.ca.onsky_log = cls._onsky
        sc.STACK_CACHE_DIR = cls._old_cache
        shutil.rmtree(cls.cache, ignore_errors=True)

    def pump_until(self, cond, timeout=120.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.update()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def test_widgets_exist(self):
        app = self.app
        app.show_step(self.ca.STEP_PHOTO)
        app.update()
        for name in ("points_frame_button", "points_stack_button", "stack_button", "stack_line_label"):
            self.assertTrue(hasattr(app, name), name)
        self.assertEqual(str(app.stack_button.cget("text")), "Stack")
        app._stack_ui_update()
        self.assertEqual(app.results_use.get(), "Results use: Per frame")
        self.assertFalse(app._stack_shown)   # no run, no Stack row
        self.assertEqual(str(app.points_frame_button.cget("text")), "Per frame")
        self.assertEqual(str(app.points_stack_button.cget("text")), "Stacked")

    def test_end_to_end_on_files(self):
        """Mono frames on disk, no plate solution and no RA/Dec: per-frame run -> suggest line -> Stack -> the
        Points switch changes the series' points and the Results use line, both ways; the report note follows."""
        ca, app = self.ca, self.app
        tmp = tempfile.mkdtemp()
        lights = os.path.join(tmp, "lights")
        os.makedirs(lights)
        rng = np.random.default_rng(21)
        exp, cad, n_frames = 2.0, 3.0, 60
        t0 = 2460950.6
        for i in range(n_frames):
            flipped = i >= 40
            img = render(rng, dx=0.03 * i, dy=0.02 * i, flipped=flipped)
            hdr = fits.Header()
            hdr["EXPTIME"] = exp
            hdr["DATE-OBS"] = Time(t0 + i * cad / 86400 - exp / 2 / 86400, format="jd", scale="utc").isot
            hdr["EGAIN"] = 1.0
            hdr["PIERSIDE"] = "West" if flipped else "East"
            fits.PrimaryHDU(np.clip(img + 500, 0, 65535).astype(np.uint16), header=hdr).writeto(
                os.path.join(lights, f"light_{i:04d}.fits"))
        try:
            with mock.patch.object(ca.messagebox, "askyesno", return_value=True), \
                    mock.patch.object(ca.messagebox, "askyesnocancel", return_value=False), \
                    mock.patch.object(ca.messagebox, "showinfo"), mock.patch.object(ca.messagebox, "showwarning"), \
                    mock.patch.object(ca.messagebox, "showerror") as err:
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
                app.target_xy = (TARGET[0], TARGET[1])
                app.comp_xy = COMPS[0][:2]
                app.comp_name.set("C1")
                app.comp_mag.set("11.0")
                app.comp2_xy = COMPS[1][:2]
                app.comp2_name.set("C2")
                app.comp2_mag.set("11.0")
                app.check_xy = None
                app.check_name.set("")
                app.run_photometry()
                self.assertTrue(self.pump_until(lambda: not app.busy and app.observations), "per-frame run")
                self.assertFalse(err.called, err.call_args)
                night = app.observations[0].night
                n_pts = len([o for o in app.observations if o.night == night])
                self.assertGreaterEqual(n_pts, 55)
                self.assertIn("Comps are weak", app.stack_line.get())
                self.assertEqual(app.results_use.get(), "Results use: Per frame")
                self.assertEqual(str(app.points_stack_button.cget("state")), "disabled")
                app.stack_n.set("5")
                app._stack_n_typed = True
                app.start_stack()
                self.assertTrue(self.pump_until(lambda: not app.busy and app._stack_set_live(night)
                                                and app.points_choice.get() == "stack", 180), "stack run")
                self.assertFalse(err.called, err.call_args)
                pts = [o for o in app.observations if o.night == night]
                # 40 frames before the flip -> 8 stacks; 20 after -> 4: never across it
                self.assertEqual(len(pts), 12)
                self.assertEqual(sum(1 for o in pts if o.segment == "flip"), 4)
                self.assertTrue(all(os.path.basename(o.path).startswith("stack_") for o in pts))
                self.assertTrue(all(abs(o.exptime - 5 * exp) < 1e-6 for o in pts))
                self.assertEqual(app.results_use.get(), "Results use: Stacked (5 frames)")
                self.assertEqual(app.stack_meta(night)["n"], 5)
                self.assertIn("points are stacks of 5 frames (effective exposure 10 s)",
                              app.stack_report_note(pts, lead=False))
                self.assertIn("stacks of 5", app._report_text(pts))
                self.assertEqual(app.out_points_label.cget("text"), "Results use: Stacked (5 frames)")
                # a transit fit made on the stacks (a real fit result stands in for one)
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                from test_2220_multinight import single_fit
                tp = app.transit_page
                tp.refresh()
                tp.night.set(night)
                fit = single_fit(100, 1)
                tp.result, tp._fit_signature, tp.meta_for_files = fit, tp._signature(), {}
                # switch back: per-frame points, line follows; the stacks' fit is put aside (not shown for frames)
                app.points_choice.set("frame")
                app._points_switched()
                self.assertEqual(app.results_use.get(), "Results use: Per frame")
                self.assertEqual(len([o for o in app.observations if o.night == night]), n_pts)
                self.assertIsNone(app.stack_meta(night))
                self.assertEqual(app.stack_report_note([o for o in app.observations], lead=False), "")
                self.assertIsNot(tp.result, fit)
                app.points_choice.set("stack")
                app._points_switched()
                self.assertEqual(app.results_use.get(), "Results use: Stacked (5 frames)")
                self.assertEqual(len([o for o in app.observations if o.night == night]), 12)
                self.assertIs(tp.result, fit)   # the cached fit is back at once, no refit
        finally:
            with mock.patch.object(app, "_offer_series_save", return_value=True):
                if app.observations:
                    app.close_series(asked=True)
            app.update()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_discovery_scan_on_stacks(self):
        """Discovery: Scan field, then Stack: the scan runs on the stacks; Points switches the scan shown."""
        ca, app = self.ca, self.app
        tmp = tempfile.mkdtemp()
        lights = os.path.join(tmp, "lights")
        os.makedirs(lights)
        rng = np.random.default_rng(8)
        exp, cad = 2.0, 3.0
        t0 = 2460960.6
        for i in range(60):
            img = render(rng, dx=0.02 * i, dy=0.01 * i, field=RICH)
            hdr = fits.Header()
            hdr["EXPTIME"] = exp
            hdr["DATE-OBS"] = Time(t0 + i * cad / 86400 - exp / 2 / 86400, format="jd", scale="utc").isot
            fits.PrimaryHDU(np.clip(img + 500, 0, 65535).astype(np.uint16), header=hdr).writeto(
                os.path.join(lights, f"light_{i:04d}.fits"))
        old_mode = app.mode_key
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
                app.scan_field()
                self.assertTrue(self.pump_until(lambda: not app.busy and getattr(app, "last_scan", None) is not None
                                                and app._stack_shown), "per-frame scan")
                self.assertFalse(err.called, err.call_args)
                frame_scan = app.last_scan
                self.assertEqual(frame_scan["frames"], 60)
                self.assertIn("a stack hides events faster than itself", app.stack_line.get())
                self.assertEqual(app.stack_n.get(), "6")   # up to the 5-min cap, but at least 10 stacks
                app.stack_n.set("6")
                app._stack_n_typed = True
                app.start_stack()
                self.assertTrue(self.pump_until(lambda: not app.busy and app.points_choice.get() == "stack"
                                                and app.last_scan is not frame_scan, 180), "stacked scan")
                self.assertFalse(err.called, err.call_args)
                self.assertEqual(app.last_scan["frames"], 10)
                self.assertEqual(app.last_scan["stack"]["n"], 6)
                self.assertEqual(app.results_use.get(), "Results use: Stacked (6 frames)")
                app.points_choice.set("frame")
                app._points_switched()
                self.assertIs(app.last_scan, frame_scan)
                self.assertEqual(app.results_use.get(), "Results use: Per frame")
        finally:
            with mock.patch.object(ca.messagebox, "askyesno", return_value=True):
                app.set_mode(old_mode)
            app.update()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
