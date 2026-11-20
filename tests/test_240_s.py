"""2.4.0 step 5: #76 the pre-run comp survey (sampled frames, steadiness, the sharpest frames' peaks, the flip step and
a balanced set), #61 the aperture by lowest scatter on the survey's frames, #58 the linear limit from the SHOBS-FLUX
camera profile every time, and the step 4 regression nits (doubled Log label, the Filter box before the scan, a site
typed "for these frames only" carried into New reduction)."""
import json
import math
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import itertools

import numpy as np

_KEYS = itertools.count(1)   # each made-up survey is another night

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import comp_survey as cs            # noqa: E402
import photometry_core as core      # noqa: E402

try:
    from astropy.io import fits
except Exception:   # pragma: no cover
    fits = None

try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


def _src(name="clearv_app.py"):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


class TestSampling(unittest.TestCase):
    def test_first_pass(self):
        got = cs.sample_indices(227)
        self.assertEqual(got[0], 0)
        self.assertEqual(got[-1], 226)
        self.assertLessEqual(len(got), cs.SURVEY_MAX)
        self.assertGreaterEqual(len(got), 20)          # about every 10th
        self.assertEqual(cs.sample_indices(5), [0, 1, 2, 3, 4])   # few lights: every one
        self.assertEqual(cs.sample_indices(0), [])
        self.assertLessEqual(len(cs.sample_indices(5000)), cs.SURVEY_MAX)

    def test_second_pass_sharpest_and_flip_sides(self):
        read = [0, 10, 20, 30, 40, 50]
        fwhm = {0: 3.0, 10: 2.1, 20: 2.9, 30: 3.3, 40: 2.4, 50: 3.1}
        flipped = {0: False, 10: False, 20: False, 30: False, 40: False, 50: True}
        more = cs.second_pass(60, read, fwhm, flipped)
        for j in (8, 9, 11, 12, 38, 39, 41, 42):     # around the two sharpest (10 and 40)
            self.assertIn(j, more)
        after = [j for j in more if j > 50]
        self.assertGreaterEqual(len(after), cs.SURVEY_SIDE_MIN - 1)   # the flipped side gets its frames
        self.assertTrue(any(40 < j < 50 for j in more))               # and the frames where the flip is
        self.assertFalse(set(more) & set(read))
        # no flip, no FWHM: nothing more to read
        self.assertEqual(cs.second_pass(60, read, {}, {i: False for i in read}), [])

    def test_second_pass_reads_both_ends_of_the_flip_gap(self):
        # step 5 regression (WASP-49 b): the frames just before the flip were never read
        read = list(range(0, 232, 10)) + [231]
        flipped = {i: i >= 115 for i in read}   # the flip somewhere in 111-119 (110 before, 120 after)
        fwhm = {i: 3.0 for i in read}
        fwhm[60], fwhm[200] = 2.0, 2.1
        more = cs.second_pass(232, read, fwhm, flipped)
        gap = [j for j in more if 110 < j < 120]
        self.assertEqual(gap, list(range(111, 120)))              # the whole gap (9 frames), both ends
        big = cs.second_pass(400, [0, 100, 300, 399], {}, {0: False, 100: False, 300: True, 399: True})
        g = [j for j in big if 100 < j < 300]
        self.assertEqual(len(g), cs.FLIP_GAP_MAX)
        self.assertIn(101, g)
        self.assertIn(299, g)


def _night(frames=40, stars=8, noise=0.003, seed=1):
    rng = np.random.default_rng(seed)
    zp = rng.normal(0, 0.05, frames)[:, None]       # clouds / airmass: common to every star
    base = np.linspace(11.0, 11.5, stars)[None, :]
    return base + zp + rng.normal(0, noise, (frames, stars))


class TestEnsemble(unittest.TestCase):
    def test_scatter_and_variable(self):
        m = _night()
        m[:, 3] += np.random.default_rng(7).normal(0, 0.03, m.shape[0])   # a variable among the candidates
        st = cs.ensemble_stats(m)
        self.assertEqual(int(np.nanargmax(st["scatter"])), 3)
        others = np.delete(st["scatter"], 3)
        self.assertTrue(np.all(others < 0.006), others)   # the common zero point cancels
        flags = cs.variable_flags(st["scatter"], np.full(8, 11.2))
        self.assertIsNotNone(flags[3])
        self.assertTrue(all(f is None for k, f in enumerate(flags) if k != 3))
        self.assertFalse(st["flip"])
        self.assertTrue(np.all(np.isnan(st["step"])))

    def test_few_peers_not_judged(self):
        st = cs.ensemble_stats(_night(stars=3))
        self.assertTrue(all(f is None for f in cs.variable_flags(st["scatter"], np.full(3, 11.0))))
        # a sparse field widens the window: a variable 1.5 mag from its 4 peers is still caught
        m = _night(stars=5)
        m[:, 0] += np.random.default_rng(3).normal(0, 0.05, m.shape[0])
        st = cs.ensemble_stats(m)
        flags = cs.variable_flags(st["scatter"], np.array([9.5, 11.0, 11.1, 11.2, 11.3]))
        self.assertIsNotNone(flags[0])
        self.assertTrue(all(f is None for f in flags[1:]))

    def test_flip_steps_and_balanced_set(self):
        m = _night(stars=6)
        flipped = [i >= 20 for i in range(m.shape[0])]
        steps_in = [0.06, -0.06, 0.02, 0.0, 0.0, 0.03]
        for j, s in enumerate(steps_in):
            m[20:, j] += s
        st = cs.ensemble_stats(m, flipped)
        self.assertTrue(st["flip"])
        # each star's step against the others' median (one big-stepping star does not shift the rest)
        for j, s in enumerate(steps_in):
            self.assertAlmostEqual(st["step"][j], s - np.median([v for k, v in enumerate(steps_in) if k != j]),
                                   delta=0.006)
        # the scatter does not count the step (each side is zero-pointed on its own)
        self.assertTrue(np.all(st["scatter"] < 0.006), st["scatter"])

    def test_flip_sides_follow_time_order_and_trend_is_no_step(self):
        # Fable 12: the reference frame may be after the flip; "after" is decided by time order
        self.assertEqual(list(cs.flip_sides([True, True, False, False])), [False, False, True, True])
        self.assertIsNone(cs.flip_sides([True, True]))
        # Fable 11: a slow trend (20 mmag over the night) with no step reads as no step near the flip
        m = _night(frames=40, stars=6, noise=0.0005)
        m[:, 0] += np.linspace(0, 0.02, 40)
        st = cs.ensemble_stats(m, [i >= 20 for i in range(40)])
        self.assertLess(abs(st["step"][0]), 0.004, st["step"][0])
        self.assertEqual((st["n_before"], st["n_after"]), (20, 20))

    def test_rank_key(self):
        steady = cs.rank_key(0.5, 0.002, 0.004)
        noisy = cs.rank_key(0.0, 0.008, 0.004)
        self.assertLess(steady, noisy)                      # steadiness first
        # step 5 fix: a single comp's flip step is not ranked (the set's measured step decides)
        self.assertEqual(cs.rank_key(0.2, 0.004, 0.004, 0.001, 0.1, True),
                         cs.rank_key(0.2, 0.004, 0.004, 0.03, 0.1, True))
        self.assertLess(cs.rank_key(0.2, 0.004, 0.004, 0.0, 0.1, True),
                        cs.rank_key(0.2, 0.004, 0.004, 0.0, 0.9, True))    # nearer the target still wins


def _flip_night(frames=60, flip_at=30, comp_steps=(0.0, 0.004, 0.030, 0.026, -0.002), target_step=0.002,
                noise=0.002, seed=11, transit=None):
    """Target (column 0) and comps with their own pier-side steps (mag, after minus before) and a common zero point."""
    rng = np.random.default_rng(seed)
    n = len(comp_steps) + 1
    zp = rng.normal(0, 0.03, frames)[:, None]
    m = np.linspace(11.0, 11.6, n)[None, :] + zp + rng.normal(0, noise, (frames, n))
    after = np.arange(frames) >= flip_at
    m[after, 0] += target_step
    for j, st in enumerate(comp_steps):
        m[after, j + 1] += st
    if transit is not None:
        m[:, 0] += transit
    return m, after


class TestTargetStep(unittest.TestCase):
    """step 5 fix (#76, WASP-49 b 19 Nov): the target's own step at the flip against a set decides the set."""

    def test_set_step_is_the_target_against_the_mean(self):
        m, after = _flip_night(noise=0.0005)
        b, a = cs.near_flip_rows(after)
        self.assertEqual((list(b), list(a)), (list(range(24, 30)), list(range(30, 36))))
        d = cs.target_diffs(m[:, 0], m[:, 1:])
        # target +2 against comps 0 (0) and 1 (+4): 2 - 2 = 0; against 2 and 3 (+30, +26): 2 - 28 = -26 mmag
        st, err = cs.set_target_step(d, [0, 1], b, a)
        self.assertAlmostEqual(st, 0.0, delta=0.002)
        self.assertLess(err, 0.002)
        self.assertAlmostEqual(cs.set_target_step(d, [2, 3], b, a)[0], -0.026, delta=0.002)
        self.assertTrue(math.isnan(cs.set_target_step(d, [], b, a)[0]))
        names = ["A", "B", "C", "D", "E"]
        chosen, step, _e = cs.choose_set(names, [0.0, 0.1, 0.2, 0.3, 0.4],
                                         lambda nm: cs.set_curve(d, [names.index(x) for x in nm]), b, a)
        self.assertNotIn("C", chosen)
        self.assertNotIn("D", chosen)
        self.assertLess(abs(step), 0.003)

    def test_steady_looking_pair_with_a_big_target_step_is_not_chosen(self):
        # the regression's case: C and D agree with each other (and with the best-ranked) but give the target a
        # large step; ranked first, they still lose to the set that leaves the target's step near zero
        m, after = _flip_night(comp_steps=(0.030, 0.028, 0.0, 0.003), target_step=0.001, noise=0.0008)
        b, a = cs.near_flip_rows(after)
        d = cs.target_diffs(m[:, 0], m[:, 1:])
        names = ["C", "D", "A", "B"]
        chosen, step, _e = cs.choose_set(names, [0.0, 0.1, 0.5, 0.6],
                                         lambda nm: cs.set_curve(d, [names.index(x) for x in nm]), b, a)
        self.assertEqual(chosen, ["A", "B"])

    def test_no_real_steps_keeps_the_steadiest(self):
        # all sets equal within their error: the best-ranked set wins
        m, after = _flip_night(comp_steps=(0.0, 0.0, 0.0, 0.0), target_step=0.0, noise=0.004)
        b, a = cs.near_flip_rows(after)
        d = cs.target_diffs(m[:, 0], m[:, 1:])
        names = ["A", "B", "C", "D"]
        chosen, _s, err = cs.choose_set(names, [0.0, 0.1, 0.2, 0.3],
                                        lambda nm: cs.set_curve(d, [names.index(x) for x in nm]), b, a)
        self.assertTrue(math.isfinite(err))
        self.assertEqual(len(chosen), 4)   # Fable S4: equal sets -> not a pair (the size nudge)
        # Fable re-review S-A: with no real steps and comps getting noisier by rank, the set's noise keeps the
        # steadiest; a pool of 4 is not cut to a pair on noise
        sc = [0.002, 0.003, 0.005, 0.007, 0.009, 0.011]
        picks = []
        for seed in range(20):
            m2, af2 = _flip_night(comp_steps=(0.0,) * 6, target_step=0.0, noise=0.0, seed=seed)
            rng = np.random.default_rng(seed)
            m2[:, 1:] += rng.normal(0, 1, m2[:, 1:].shape) * np.array(sc)
            b2, a2 = cs.near_flip_rows(af2)
            d2 = cs.target_diffs(m2[:, 0], m2[:, 1:])
            nm6 = list("ABCDEF")
            ch, _s2, _e2 = cs.choose_set(nm6, [0.1 * k for k in range(6)],
                                         lambda nm: cs.set_curve(d2, [nm6.index(x) for x in nm]), b2, a2, scatter=sc)
            picks.append(ch)
        self.assertGreaterEqual(sum(1 for ch in picks if set(ch) <= set("ABCD")), 15)
        # nothing measured: no set
        self.assertEqual(cs.choose_set(names, [0, 1, 2, 3], lambda nm: np.full(len(d), np.nan), b, a)[0], [])

    def test_lowest_step_set_wins_over_the_best_ranked(self):
        # Fable S3: four best-ranked comps step +10 mmag, four step 0, target 0: the zero-step set wins
        m, after = _flip_night(frames=60, comp_steps=(0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0), target_step=0.0,
                               noise=0.001, seed=5)
        b, a = cs.near_flip_rows(after)
        d = cs.target_diffs(m[:, 0], m[:, 1:])
        names = list("ABCDEFGH")
        chosen, st, _e = cs.choose_set(names, [0.1 * k for k in range(8)],
                                       lambda nm: cs.set_curve(d, [names.index(x) for x in nm]), b, a)
        self.assertEqual(chosen, ["E", "F", "G", "H"])
        self.assertLess(abs(st), 0.002)

    def test_flip_pass_brackets_then_reads_the_neighbors(self):
        # WASP-49 b: 232 frames, flip before frame 111 (0-based 110); first pass every ~10th
        n, flip = 232, 110
        tried = set(cs.sample_indices(n))
        flags = lambda: {i: i >= flip for i in tried}
        tried |= set(cs.second_pass(n, sorted(tried), {}, flags()))
        rounds = 0
        while True:
            nxt = cs.flip_pass(n, tried, flags())
            if not nxt:
                break
            tried |= set(nxt)
            rounds += 1
            self.assertLess(rounds, 20)
        N = cs.FLIP_NEAR_TARGET
        for j in range(flip - N, flip + N):
            self.assertIn(j, tried)
        idx = sorted(tried)
        b, a = cs.near_flip_rows(cs.flip_sides([i >= flip for i in idx]), N, idx=idx)
        self.assertEqual([idx[r] for r in b], list(range(flip - N, flip)))
        self.assertEqual([idx[r] for r in a], list(range(flip, flip + N)))
        # sparse rows only (no flip pass): the far ones are dropped
        idx = list(range(0, 232, 10))
        b, a = cs.near_flip_rows(cs.flip_sides([i >= 111 for i in idx]), cs.FLIP_NEAR_TARGET, idx=idx)
        self.assertEqual([idx[r] for r in b], [110])               # within 19 frames of the first after (120) only
        self.assertEqual([idx[r] for r in a], [120, 130])
        self.assertEqual(cs.flip_pass(n, {0, 10}, {0: False, 10: False}), [])

    def test_transit_taken_out(self):
        import transit_core as tcore
        planet = {"period": 2.78, "t0": 2461363.8260, "k": 0.12, "a_rs": 7.0, "b": 0.3, "t0_err": 0.0003}
        t = 2461363.74 + np.arange(120) * 0.0015
        model = cs.transit_mags(t, planet, tcore.occultation, (0.45, 0.2))
        self.assertGreater(np.nanmax(model["mag"]), 0.012)
        self.assertEqual(model["mag"][0], 0.0)
        flip = int(np.argmin(np.abs(t - 2461363.826)))   # at mid-transit
        m, after = _flip_night(frames=120, flip_at=flip, comp_steps=(0.0, 0.0, 0.0), target_step=0.0,
                               noise=0.0005, transit=model["mag"])
        b, a = cs.near_flip_rows(after)
        self.assertFalse(cs.near_contact(t[np.r_[b, a]], model))
        d = cs.target_diffs(m[:, 0], m[:, 1:], model["mag"])
        self.assertAlmostEqual(cs.set_target_step(d, [0, 1, 2], b, a)[0], 0.0, delta=0.0015)
        # a flip in ingress: not judged
        t1 = planet["t0"] - tcore.total_duration(planet["period"], planet["a_rs"], planet["b"], planet["k"]) / 2
        near = np.array([t1 - 0.003, t1, t1 + 0.003])
        self.assertTrue(cs.near_contact(near, model))
        self.assertIsNone(cs.transit_mags(t, {"period": 2.78}, tcore.occultation))
        self.assertTrue(cs.near_contact(t[:3], None))
        self.assertTrue(cs.near_contact(np.array([np.nan]), model))        # Fable nit 2
        # Fable S6: a huge uncertainty cannot step over ingress
        wide = dict(model, t0_err=0.2)
        self.assertTrue(cs.near_contact(np.array([planet["t0"]]), wide))
        # Fable S5: no uncertainty given: 30 min more margin, so a frame 45 min from mid-transit (about 35 min
        # inside second contact here) is fine with the error and "near" without it
        self.assertFalse(cs.near_contact(np.array([planet["t0"]]), model))
        t_in = np.array([planet["t0"] - 45 / 1440])
        self.assertFalse(cs.near_contact(t_in, model))
        self.assertTrue(cs.near_contact(t_in, dict(model, err_known=False)))


class TestApertureChoice(unittest.TestCase):
    def test_radii(self):
        self.assertEqual(cs.trial_radii(2.6, 4.6), [2.6, 3.1, 3.9, 4.6, 4.7, 5.7, 6.5])
        self.assertEqual(cs.trial_radii(float("nan"), 5.0), [5.0])
        self.assertEqual(cs.trial_radii(1.0), [2.0, 2.2, 2.5])

    def test_choice(self):
        # Joanna's WASP-43 b MicroObservatory night (#61): r 3 halves the scatter of r 4.6
        got = cs.aperture_choice({3.0: 0.0125, 4.6: 0.0172, 6.0: 0.022}, 4.6)
        self.assertEqual(got["r"], 3.0)
        self.assertAlmostEqual(got["gain"], 1 - 0.0125 / 0.0172, places=6)
        self.assertIsNone(cs.aperture_choice({3.0: 0.0165, 4.6: 0.0172}, 4.6))   # under 10 % better: keep
        self.assertIsNone(cs.aperture_choice({3.0: 0.010, 4.0: 0.012}, 4.6))    # the current one was not measured
        self.assertIsNone(cs.aperture_choice({4.6: 0.01}, 4.6))
        self.assertEqual(cs.mmag(0.0125), "12.5 mmag")


# ---- the survey in the app, on a made-up night ------------------------------------------------------------------

H, W = 260, 360
SKY_OUT = 12.0
# (x, y, amplitude, name); star 0 is the target
FIELD = [(180, 130, 5000, "T"),
         (90, 80, 5200, "STEADY A"), (270, 80, 4800, "STEADY B"), (90, 190, 5100, "STEADY C"),
         (270, 190, 4900, "STEADY D"), (180, 60, 5000, "WOBBLY"), (180, 205, 9400, "PEAKY")]
# PEAKY: 0.7 mag brighter; its peak goes over 90 % of the 20,000 linear limit only on the sharp frames
N_FRAMES = 14


def _frame(i, sharp=False):
    rng = np.random.default_rng(100 + i)
    yy, xx = np.mgrid[0:H, 0:W]
    img = rng.normal(300.0, 4.0, (H, W))
    sigma = 1.2 if sharp else 1.9
    cloud = 1.0 + 0.05 * math.sin(i)                     # a common change every star sees
    for x, y, a, name in FIELD:
        amp = a * cloud
        if name == "WOBBLY":
            amp *= 1.0 + 0.08 * ((-1) ** i)              # varies frame to frame
        img += amp * (1.9 / sigma) ** 2 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma ** 2))
    for x, y in np.random.default_rng(5).uniform((30, 30), (W - 30, H - 30), (20, 2)):
        img += 700 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.9 ** 2))
    return img.astype(np.float32)


def write_survey_night(folder, sharp=(9, 10, 11)):   # a spell of good seeing
    paths = []
    for i in range(N_FRAMES):
        p = os.path.join(folder, f"L_{i + 1:04d}.fits")
        hdr = fits.Header()
        hdr["IMAGETYP"] = "Light Frame"
        hdr["EXPTIME"] = 60.0
        hdr["DATE-OBS"] = f"2026-11-19T06:{10 + i:02d}:00"
        fits.PrimaryHDU(_frame(i, sharp=i in sharp), header=hdr).writeto(p, overwrite=True)
        paths.append(p)
    return paths


@unittest.skipUnless(HAVE_TK and fits is not None, "needs a display and astropy")
class TestSurveyInTheApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test240s.log")
        cls.ca = ca
        cls.app = ca.App()
        cls.app.set_mode("variables")
        cls.app.update()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.paths = write_survey_night(cls.tmp.name)
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

    def _setup(self, linear=20000.0, observations=None):
        app = self.app
        app.light_dir.set(self.tmp.name)
        app.rejected = set()
        app.pattern.set(core.MONO)
        app.binning.set(1)
        app.masters = core.Masters()
        app.preview = _frame(0)
        app.preview_is_debayered = True
        app.preview_path = self.paths[0]
        app._preview_foreign = ""
        app.radius.set(5); app.sky_in.set(8); app.sky_out.set(SKY_OUT)
        app.sat_limit.set(60000)
        app.observations = list(observations or [])
        app.chart_placed = []
        for x, y, _a, name in FIELD:
            st = {"label": name, "mag": 12.0, "band": "V", "catalog": "Tycho-2", "ra": 30.0, "dec": 0.0,
                  "mags": {"V": 12.0, "B": 12.6}}
            app.chart_placed.append((float(x), float(y), st))
        app.target_xy = (float(FIELD[0][0]), float(FIELD[0][1]))
        app._star_snr = lambda *_a, **_k: 500.0
        app._linear_limit = lambda: linear
        app._raw_peak_at = lambda *_a, **_k: 1000.0       # the page's frame alone looks fine for every star
        app._seeing = lambda *_a, **_k: 4.5               # FWHM of the made-up stars (sigma 1.9)
        app._survey_cache = None
        app._drift_cache = None
        app._survey_said = None
        self.lines.clear()
        return app

    def _wait(self, app, timeout=60.0):
        app._suggest_comps()
        t0 = time.time()
        while time.time() - t0 < timeout:
            app.update()
            c = getattr(app, "_survey_cache", None)
            if (c is not None and c.get("state") == "done" and not getattr(app, "_suggest_drift_pending", False)):
                break
            time.sleep(0.02)
        app.update()
        c = app._survey_cache
        self.assertIsNotNone(c)
        self.assertEqual(c["state"], "done", "the survey never finished")
        return [n for n, _dm, _x, _y in app.suggested_comps]

    def test_survey_leaves_out_the_variable_and_the_peaky_star(self):
        app = self._setup()
        names = self._wait(app)
        self.assertNotIn("WOBBLY", names)
        self.assertNotIn("PEAKY", names)
        self.assertEqual(set(names), {"STEADY A", "STEADY B", "STEADY C", "STEADY D"})
        left = [t for t in self.lines if t.startswith("Left out by the comp survey")]
        self.assertTrue(left, self.lines)
        self.assertIn("WOBBLY (scatters", left[-1])
        self.assertIn("PEAKY (raw peak up to", left[-1])
        said = [t for t in self.lines if t.startswith("Suggested comps")][-1]
        self.assertIn(f"of the night's {N_FRAMES} frames (comp survey", said)
        # the panel's S lines say what the night's frames showed
        lines = app._suggested_lines()
        self.assertTrue(any("steady" in ln and "mmag on" in ln for ln in lines), lines)
        res = app._survey_cache["result"]
        self.assertTrue({9, 10, 11} & set(res["frames"]))   # the good-seeing spell was read
        self.assertIn(0, res["frames"])
        self.assertIn(N_FRAMES - 1, res["frames"])

    def test_own_flip_step_lets_the_steadiest_back(self):
        """step 6c-2 (Fable M1/S1): stars beyond the own flip-step limit are left out; when that leaves fewer than
        3 comps after every gate (step 6d), the steadiest of them come back, and the S line says how many passed."""
        app = self._setup()
        real = app._survey_view
        jumps = {"STEADY B": 12.0, "STEADY C": -15.0, "STEADY D": 30.0}

        seen = []

        def view(*a, **k):
            v = real(*a, **k)
            v = dict(v, out=dict(v["out"]), own_out=dict(jumps))
            seen.append(v)
            for n, ppt in jumps.items():
                v["out"][n] = f"jumps {ppt:+.1f} ppt at the meridian flip on its own (test)"
            return v
        with mock.patch.object(app, "_survey_view", side_effect=view):
            names = self._wait(app)
        # step 6d (John 20 Nov): let back until there are 3, steadiest first (B +12, then C -15; D +30 stays out)
        self.assertEqual(set(names), {"STEADY A", "STEADY B", "STEADY C"})
        # (said on the S line of a flip night; this made-up night has no flip, so read it from the survey)
        note = seen[-1]["own_note"]
        self.assertTrue(note.startswith("only 1 comp candidate passes every test (frame edge, linear limit, colour, "
                                        "own flip step within ±10 ppt), so the steadiest of the others are used "
                                        "too: "), note)
        self.assertIn("STEADY B (own step +12.0 ppt)", note)
        self.assertIn("STEADY C (own step -15.0 ppt)", note)
        self.assertNotIn("STEADY D", note)
        left = [t for t in self.lines if t.startswith("Left out by the comp survey")][-1]
        self.assertIn("STEADY D (jumps", left)
        self.assertNotIn("STEADY B (jumps", left)
        self.assertNotIn("STEADY C (jumps", left)

    def test_two_pass_one_let_back(self):
        """step 6d (John 20 Nov): the WASP-49 b case, 2 comps pass, the steadiest reject comes back for a third."""
        app = self._setup()
        real = app._survey_view
        jumps = {"STEADY C": -14.0, "STEADY D": 22.0}
        seen = []

        def view(*a, **k):
            v = real(*a, **k)
            v = dict(v, out=dict(v["out"]), own_out=dict(jumps))
            seen.append(v)
            for n, ppt in jumps.items():
                v["out"][n] = f"jumps {ppt:+.1f} ppt at the meridian flip on its own (test)"
            return v
        with mock.patch.object(app, "_survey_view", side_effect=view):
            names = self._wait(app)
        self.assertEqual(set(names), {"STEADY A", "STEADY B", "STEADY C"})
        self.assertEqual(seen[-1]["own_note"],
                         "only 2 comp candidates pass every test (frame edge, linear limit, colour, own flip step "
                         "within ±10 ppt), so the steadiest of the others is used too: STEADY C (own step -14.0 ppt)")
        left = [t for t in self.lines if t.startswith("Left out by the comp survey")][-1]
        self.assertIn("STEADY D (jumps", left)
        self.assertNotIn("STEADY C (jumps", left)

    def test_flip_set_choice_gets_the_minimum(self):
        """step 6d: on a flip night with let-backs, the set choice is asked for at least 3 comps."""
        app = self._setup()
        names = ["A", "B", "C", "D"]
        survey = {"index": {n: i for i, n in enumerate(names)}, "scatter": [0.002] * 4, "ref_scatter": 0.002,
                  "step": [0.0] * 4, "dist": [10.0] * 4, "flip": True, "tdiffs": None, "b_rows": [], "a_rows": [],
                  "weights": None}
        picks = [(n, 0.0) for n in names[:3]]

        def choose(pool, _gate):
            return [(c[0], 0.0) for c in pool]
        cands = [(n, 0.0, 0.0, 0.0, 0.5) for n in names]
        got = {}

        def fake_set(nm, *_a, **k):
            got.update(k)
            return list(nm[:3]), 0.0, 0.001
        with mock.patch.object(cs, "choose_set", side_effect=fake_set):
            out = app._survey_final_picks(picks, survey, choose, cands, set(names), set(), 0.5, min_size=3)
            self.assertEqual(got.get("min_size"), 3)
            self.assertEqual(len(out[0]), 3)
            app._survey_final_picks(picks, survey, choose, cands, set(names), set(), 0.5)
            self.assertEqual(got.get("min_size"), 2)

    def test_without_the_linear_limit_the_peaky_star_may_stay(self):
        app = self._setup(linear=None)
        names = self._wait(app)
        self.assertNotIn("WOBBLY", names)
        self.assertEqual(len(names), 4)

    def test_run_waits_for_the_survey(self):
        app = self._setup()
        app._suggest_comps()
        t0 = time.time()
        while getattr(app, "_survey_cache", None) is None and time.time() - t0 < 30:
            app.update()
            time.sleep(0.02)
            app._suggest_comps() if not getattr(app, "_suggest_drift_pending", False) else None
        self.assertEqual(app._survey_cache["state"], "loading")
        with mock.patch.object(app, "_lights", side_effect=AssertionError("the run started")):
            app.run_photometry()
        self.assertTrue(app._run_after_survey)
        self.assertTrue(any("waits for the comp survey" in t for t in self.lines))
        app._survey_cache = None   # the read ends itself
        app._run_after_survey = False

    def test_no_suggestions_while_the_survey_reads(self):
        """2.4.0 step 6 (nit a): while the survey reads the night, no S rings and no "Suggested comps" line; the
        survey's own set when it is done."""
        app = self._setup()
        app._suggest_comps()
        t0 = time.time()
        while getattr(app, "_survey_cache", None) is None and time.time() - t0 < 30:
            app.update()
            time.sleep(0.02)
            app._suggest_comps() if not getattr(app, "_suggest_drift_pending", False) else None
        if app._survey_cache["state"] == "loading":
            app._suggest_comps()   # a redraw while it reads
            self.assertEqual(app.suggested_comps, [])
            self.assertTrue(app._suggest_waiting_survey)
            self.assertFalse(any(t.startswith("Suggested comps") for t in self.lines))
            self.assertFalse(app._suggested_lines() and any("S1" in ln for ln in app._suggested_lines()))
        names = self._wait(app)
        self.assertEqual(len(names), 4)
        self.assertFalse(app._suggest_waiting_survey)
        said = [t for t in self.lines if t.startswith("Suggested comps")]
        self.assertEqual(len(said), 1, said)                 # one line, the survey's set
        self.assertIn("comp survey", said[0])

    def test_series_comp_that_fails_is_said(self):
        app = self._setup()
        obs = [core.Obs(jd=2461000.5, mag=12.0, merr=0.01, cmag=12.0, kmag=None, airmass=None, path="x",
                        exptime=60.0, flux_var=1.0, flux_comp=1.0, night="JD2461000")]
        app.observations = obs
        app.comp_name.set("WOBBLY")
        try:
            self._wait(app)
            self.assertTrue(any(t.startswith("Series comp WOBBLY fails tonight") for t in self.lines), self.lines)
        finally:
            app.comp_name.set("")
            app.observations = []

    def test_no_false_no_good_comp_warning(self):
        # Fable 4: nothing within the window failed the survey -> no "⚠ No good comp" (the catalog lines speak)
        app = self._setup()
        view = {"out": {}, "window": set(), "index": {}, "peak_max": np.zeros(0), "peaks": np.zeros((0, 0)),
                "frames": 10}
        app._survey_rejected = set()
        app._survey_said = None
        self.assertFalse(app._survey_report(view, None, []))
        self.assertFalse(any("No good comp" in t for t in self.lines))
        app._survey_rejected = {"X"}
        app._survey_said = None
        self.assertTrue(app._survey_report(view, None, []))
        self.assertTrue(any(t.startswith("⚠ No good comp here tonight") for t in self.lines))

    def test_replaced_survey_keeps_the_waiting_run(self):
        # re-review 2: survey A ends after B replaced it: the run waits for B
        app = self._setup()
        a, b = {"key": ("a",), "state": "loading"}, {"key": ("b",), "state": "loading"}
        app._survey_cache = b
        app._run_after_survey = True
        app._survey_done(a, {"error": ""})
        self.assertTrue(app._run_after_survey)
        # re-review 5: the page changed: nothing cached as an error, the waiting run is said to be dropped
        b["key"] = ("another frame.fits",)
        app._survey_done(b, {"error": ""})
        self.assertIsNone(app._survey_cache)
        self.assertFalse(app._run_after_survey)
        self.assertTrue(any(t.startswith("Run photometry did not start") for t in self.lines))

    def test_no_masters_no_survey(self):
        app = self._setup()
        app.masters = None
        app.precalibrated = False
        self.assertIsNone(app._comp_survey([("A", 1.0, 10.0, 10.0, None)], 1.0))
        self.assertIsNone(app._survey_cache)


# ---- #58 the linear limit from SHOBS-FLUX ------------------------------------------------------------------------

def _profile(folder, lin=60931.0, per_channel=True):
    import shobs_profile as sp
    cam_id = sp.camera_id("ZWO ASI2600MC Air", 100, 50, 1)
    rec = sp.record(lin, "ADU", "measured", date="2026-11-09")
    doc = {"format": sp.CAMERA_FORMAT, "format_version": 1, "profile_id": cam_id, "revision": 1,
           "values": {"linear_limit_adu": rec, "saturation_adu": sp.record(65535.0, "ADU", "measured")},
           "per_channel": {"G": {"linear_limit_adu": sp.record(lin - 1000, "ADU", "measured")}} if per_channel else {}}
    os.makedirs(os.path.join(folder, "cameras"), exist_ok=True)
    path = os.path.join(folder, "cameras", cam_id + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return path


HEADER = {"INSTRUME": "ZWO ASI2600MC Air", "GAIN": 100, "OFFSET": 50, "XBINNING": 1}


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestLinearFromFlux(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test240s_b.log")
        cls.ca = ca
        cls.app = ca.App()
        cls.app.update()
        cls.lines = []
        orig = cls.app.log

        def log(text, *a, **k):
            cls.lines.append(text)
            return orig(text, *a, **k)
        cls.app.log = log
        cls.app._save_settings = lambda *a, **k: None

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"SHOBS_DATA_HOME": self.tmp.name})
        self.env.start()
        self.lines.clear()
        app = self.app
        app.light_dir.set(self.tmp.name)
        app.pattern.set(core.MONO)
        app._set_linear_box("40000", "user")
        app._linear_typed_for = None

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_profile_value_replaces_the_old_box(self):
        _profile(self.tmp.name)
        line = self.app._flux_linear_sync(HEADER)
        self.assertEqual(self.app.linear_limit.get(), "60931")
        self.assertEqual(self.app.linear_source.get(), "from SHOBS-FLUX")
        self.assertIn("Linear ADU 60,931 from the SHOBS-FLUX profile for this camera (measured 2026-11-09)", line)
        self.assertIn("it was 40,000", line)
        self.assertEqual(self.app._flux_linear_sync(HEADER), "")   # the same again: nothing to say

    def test_color_camera_uses_its_channel(self):
        _profile(self.tmp.name)
        self.app.pattern.set("RGGB")
        self.app.debayer_mode.set("green")
        try:
            self.app._flux_linear_sync(HEADER)
            self.assertEqual(self.app.linear_limit.get(), "59931")
        finally:
            self.app.pattern.set(core.MONO)

    def test_typed_value_wins_for_these_lights(self):
        _profile(self.tmp.name)
        self.app._flux_linear_sync(HEADER)
        self.app.linear_limit.set("45000")   # typed
        self.assertEqual(self.app.linear_source.get(), "set here")
        self.assertEqual(self.app._flux_linear_sync(HEADER), "")
        self.assertEqual(self.app.linear_limit.get(), "45000")
        self.app.light_dir.set(os.path.join(self.tmp.name, "other night"))   # other lights: the profile again
        self.app._flux_linear_sync(HEADER)
        self.assertEqual(self.app.linear_limit.get(), "60931")

    def test_profile_gone_keeps_the_box_with_a_note(self):
        path = _profile(self.tmp.name)
        self.app._flux_linear_sync(HEADER)
        os.remove(path)
        line = self.app._flux_linear_sync(HEADER)
        self.assertIn("not found for these lights' camera now; the box keeps 60,931", line)
        self.assertEqual(self.app.linear_limit.get(), "60931")
        self.assertEqual(self.app.linear_source.get(), "set here")
        self.assertNotIn("⚠", line)

    def test_no_profile_leaves_the_box(self):
        self.assertEqual(self.app._flux_linear_sync(HEADER), "")
        self.assertEqual(self.app.linear_limit.get(), "40000")


# ---- step 4 regression nits ---------------------------------------------------------------------------------------

@unittest.skipUnless(HAVE_TK, "needs a display")
class TestNits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test240s_c.log")
        cls.ca = ca
        cls.app = ca.App()
        cls.app.update()
        cls.lines = []
        orig = cls.app.log

        def log(text, *a, **k):
            cls.lines.append(text)
            return orig(text, *a, **k)
        cls.app.log = log

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def test_label_not_doubled(self):
        self.lines.clear()
        self.app._slot_say("comp4", "✓ Comp 4: 000-BNX-001  V = 11.20  Marker at (1913, 22).")
        self.assertEqual(self.lines[-1], "Comp 4: ✓ 000-BNX-001  V = 11.20  Marker at (1913, 22).")
        self.app._slot_say("comp", "Comp 1: TYC 1-2-3 placed.")
        self.assertEqual(self.lines[-1], "Comp 1: TYC 1-2-3 placed.")
        self.app._slot_say("check", "Calibrate first.")
        self.assertEqual(self.lines[-1], "Check: Calibrate first.")

    def test_filter_box_before_the_scan(self):
        app = self.app
        app.pattern.set(core.MONO)
        app.mono_filter = ("V", "V")
        app._mono_filter_source = None
        app._update_filter_hint()
        self.assertIn("as last used (the lights' headers are read at the scan)", app.filter_hint.get())
        self.assertNotIn("FITS FILTER", app.filter_hint.get())
        app._mono_filter_source = "header"
        app._update_filter_hint()
        self.assertIn("filter wheel (FITS FILTER)", app.filter_hint.get())

    def test_new_reduction_brings_your_own_setup_back(self):
        app = self.app
        app._own_setup = {"aperture_mm": app.aperture_mm.get()} if hasattr(app, "aperture_mm") else {}
        app._scanned_lights = "C:/borrowed lights"
        with mock.patch.object(app, "_restore_own_setup", wraps=app._restore_own_setup) as back:
            app.new_reduction()
        back.assert_called()
        # Fable 1: the borrowed lights are scanned again at Calibrate (their setup question comes back)
        self.assertIsNone(app._scanned_lights)

    def test_cancel_calls_off_a_waiting_run(self):
        app = self.app
        app._run_after_survey = True
        app.cancel_job()
        self.assertFalse(app._run_after_survey)
        self.assertIn("called off", app.status.get())


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestSurveyAperture(unittest.TestCase):
    """#61: the survey's trial apertures, judged on the stars like the target."""

    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test240s_d.log")
        cls.app = ca.App()
        cls.app.set_mode("transits")
        cls.app.update()
        cls.lines = []
        orig = cls.app.log

        def log(text, *a, **k):
            cls.lines.append(text)
            return orig(text, *a, **k)
        cls.app.log = log

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def _cache(self, noise_small=0.004, noise_big=0.009, frames=14, like=6):
        # what the survey's worker leaves: the trial's scatter per aperture for the stars like the target
        res = {"radii": [3.0, 4.6], "rings": {3.0: (6.9, 9.0), 4.6: (9.0, 13.0)}, "idx": list(range(frames)),
               "by_r": {3.0: noise_small, 4.6: noise_big}, "n_like": like, "of": 60, "failed": 0}
        n = next(_KEYS)
        return {"key": (f"ref{n}.fits", 0.0, (f"L{n}",)), "state": "done", "result": res,
                "aperture_at_start": (4.6, 9.0, 13.0)}

    def _setup(self, observations=()):
        app = self.app
        app.radius.set(4.6); app.sky_in.set(9.0); app.sky_out.set(13.0)
        app.target_xy = (100.0, 100.0)
        app._inst_at = lambda *_a, **_k: -11.0
        app._survey_cand_inst = [-11.0, -11.1, -10.9, -11.2, -10.8, -12.5]
        app.observations = list(observations)
        self.lines.clear()
        return app

    def test_lower_scatter_aperture_is_set_and_the_trial_said(self):
        app = self._setup()
        cache = self._cache()
        app._survey_aperture_apply(cache)
        self.assertEqual(float(app.radius.get()), 3.0)
        self.assertEqual((float(app.sky_in.get()), float(app.sky_out.get())), (6.9, 9.0))
        line = [t for t in self.lines if t.startswith("Aperture trial on 14 of the night's frames")][-1]
        self.assertIn("r 3: ", line)
        self.assertIn("so it is set", line)

    def test_not_set_on_a_series_or_when_the_boxes_changed(self):
        app = self._setup(observations=[object()])
        app._survey_aperture_apply(self._cache())
        self.assertEqual(float(app.radius.get()), 4.6)
        self.assertTrue(any("not set because a series is open" in t for t in self.lines), self.lines)
        app = self._setup()
        app.radius.set(5.0)
        app._survey_aperture_apply(self._cache())
        self.assertEqual(float(app.radius.get()), 5.0)
        self.assertTrue(any("the boxes were changed while the survey ran" in t for t in self.lines))

    def test_no_clear_winner_keeps_the_aperture_quietly(self):
        app = self._setup()
        app._survey_aperture_apply(self._cache(noise_small=0.009, noise_big=0.009))
        self.assertEqual(float(app.radius.get()), 4.6)
        self.assertFalse(any(t.startswith("Aperture trial") for t in self.lines))
        # Fable 13: too few frames or stars like the target: not judged
        app._survey_aperture_apply(self._cache(frames=8))
        app._survey_aperture_apply(self._cache(like=2))
        self.assertEqual(float(app.radius.get()), 4.6)

    def test_set_once_per_night_and_target(self):
        app = self._setup()
        cache = self._cache()
        app._survey_aperture_apply(cache)
        self.assertEqual(float(app.radius.get()), 3.0)
        app.radius.set(5.0)                       # yours afterwards
        cache["aperture_at_start"] = (5.0, 9.0, 13.0)
        cache["result"]["by_r"] = {3.0: 0.004, 5.0: 0.009}
        cache["result"]["radii"] = [3.0, 5.0]
        app._survey_aperture_apply(cache)         # a second read of the same night and target never sets it again
        self.assertEqual(float(app.radius.get()), 5.0)


@unittest.skipUnless(HAVE_TK and fits is not None, "needs a display and astropy")
class TestTargetStepInTheApp(unittest.TestCase):
    """step 5 fix (#76): the app measures the target's own flip step (Transits, ephemeris, away from contact) and
    otherwise leaves the step to the transit fit, and says which."""

    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test240s_t.log")
        cls.app = ca.App()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def _result(self, t, flip_at, comp_steps, target_step, transit):
        m, after = _flip_night(frames=len(t), flip_at=flip_at, comp_steps=comp_steps, target_step=target_step,
                               noise=0.0005, transit=transit)
        return {"idx": list(range(len(t))), "jd": np.asarray(t), "flipped": list(after),
                "mags": {8.0: m}}

    def _planet(self, app):
        app.set_mode("transits")
        app.pl_period.set("2.78")
        app.pl_t0.set("2461363.8260")
        app.pl_k.set("0.12")
        app.pl_a_rs.set("7.0")
        app.pl_b.set("0.3")
        app.ra_hours.set("6.07")
        app.dec_deg.set("-17.1")
        return app.planet_from_fields()

    def test_measured_in_transits_and_fallback_words(self):
        import transit_core as tcore
        app = self.app
        planet = self._planet(app)
        # frame times are JD UTC; the app turns them into BJD before the ephemeris
        t = 2461363.66 + np.arange(240) * 0.0015   # step 6b: baseline on both sides of the transit
        bjd = t + np.asarray(tcore.bjd_minus_jd_days(t, 6.07 * 15, -17.1))
        model = cs.transit_mags(bjd, planet, tcore.occultation, tcore.limb_darkening_guess(planet.get("teff")))
        flip = int(np.argmin(np.abs(bjd - 2461363.826)))
        res = self._result(t, flip, (0.0, 0.004, 0.030, 0.026), 0.002, model["mag"])
        why, d, b, a, w = app._survey_target_steps(res, 8.0, [0, 1, 2, 3, None], planet)
        self.assertEqual(why, "")
        self.assertEqual(d.shape, (240, 5))
        self.assertTrue(np.all(np.isnan(d[:, 4])))
        # step 6c: every frame away from ingress and egress (in transit too), fitted with a free depth
        self.assertIsInstance(w, dict)
        self.assertTrue(np.any(model["mag"][np.r_[b, a]] > 0.005))   # flat-bottom frames are used
        self.assertGreater(len(b) + len(a), 120)
        self.assertAlmostEqual(cs.set_target_step(d, [0, 1], b, a, w)[0], 0.0, delta=0.003)
        self.assertAlmostEqual(cs.set_target_step(d, [2, 3], b, a, w)[0], -0.026, delta=0.003)
        # Variables: not judged, and the words say the fit decides
        app.set_mode("variables")
        why, d2, _b, _a, _w = app._survey_target_steps(res, 8.0, [0, 1, 2, 3, None], planet)
        self.assertIsNone(d2)
        self.assertIn("outside Transits", why)
        survey_v = {"index": {"A": 0}, "frames": 30, "of": 120, "flip": False, "flip_seen": True, "flip_why": why,
                    "n_before": 15, "n_after": 15, "radius": 8.0, "radius_asked": 8.0}
        self.assertIn("the target's flip step is not measured", app._survey_words(survey_v, float("nan"),
                                                                                 picks=[("A", 0.1)]))
        survey = {"index": {"A": 0}, "frames": 30, "of": 120, "flip": False, "flip_seen": True,
                  "flip_why": "no ephemeris and transit shape on the Input page",
                  "n_before": 15, "n_after": 15, "radius": 8.0, "radius_asked": 8.0}
        words = app._survey_words(survey, float("nan"), picks=[("A", 0.1)])
        self.assertIn("the flip step is left to the transit fit", words)
        survey.update(flip=True, flip_why="")
        words = app._survey_words(survey, 0.0012, picks=[("A", 0.1)], err=0.0009)
        # step 6b: in the fit's units and sign (ppt of flux, + = brighter after): +1.2 mmag is -1.1 ppt
        self.assertIn("-1.1 ± 0.8 ppt", words)
        self.assertIn("as the transit fit finds it", words)
        self.assertNotIn("nearest the target", words)
        # step 6b: a flip at ingress is judged too (on the baseline frames, as the fit judges it)
        app.set_mode("transits")
        planet = self._planet(app)
        t1 = 2461363.826 - tcore.total_duration(2.78, 7.0, 0.3, 0.12) / 2
        flip = int(np.argmin(np.abs(bjd - t1)))
        res = self._result(t, flip, (0.0, 0.0), 0.0, model["mag"])
        why, d3, b3, a3, w3 = app._survey_target_steps(res, 8.0, [0, 1], planet)
        self.assertEqual(why, "")
        self.assertAlmostEqual(cs.set_target_step(d3, [0, 1], b3, a3, w3)[0], 0.0, delta=0.003)
        # too few frames before the flip at all: not judged, and it says why
        flip = 2
        res = self._result(t, flip, (0.0, 0.0), 0.0, model["mag"])
        why, d4, _b, _a, _w = app._survey_target_steps(res, 8.0, [0, 1], planet)
        self.assertIsNone(d4)
        self.assertIn("too few frames read away from ingress and egress on one side of the flip", why)


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestRoleTagByPosition(unittest.TestCase):
    def test_slot_with_another_name_still_tagged(self):
        import clearv_app as ca
        app = ca.App()
        try:
            app.set_mode("variables")
            role = ca.COMP_ROLES[0]
            app.suggested_comps = [("TYC 5936-2073-1", 0.1, 100.0, 120.0)]
            app.suggested_info = {"TYC 5936-2073-1": {"mag": 11.2}}
            getattr(app, f"{role}_name").set("000-BQG-573")   # the slot shows the AUID
            setattr(app, f"{role}_xy", (101.0, 121.0))
            lines = app._suggested_lines()
            self.assertTrue(any("— now" in ln for ln in lines), lines)
            setattr(app, f"{role}_xy", (300.0, 300.0))          # another star: no tag
            self.assertFalse(any("— now" in ln for ln in app._suggested_lines()))
        finally:
            app.destroy()
