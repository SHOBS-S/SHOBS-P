"""2.3.5 (safety + bugs, milestone 2.3.5): #88 leave-one-comp-out with a comp missing in some frames, #80 two-star
solve, #81 flip-step hold, #83 self-test exit code, safe defaults on the "anyway?" questions."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402

import photometry_core as core  # noqa: E402
import transit_core as tc  # noqa: E402

DEPTH = 0.018
PLANET = {"name": "Synth b", "host": "Synth", "period": 3.0, "t0": 2461000.0, "t0_err": 0.0002,
          "period_err": 1e-6, "k": DEPTH ** 0.5, "a_rs": 8.0, "b": 0.3, "t14": 0.115, "teff": 5800.0,
          "depth": DEPTH}
TC = 2461000.0 + 1000 * PLANET["period"]


def synth(n=84, missing=None, seed=3, noise=0.003):
    """84 frames, 4 comps at different brightness, a common transparency drift; the target's flux is divided by the
    tied comp ensemble (the photometry rule). missing = (comp index, first frame, last frame) set to NaN."""
    rng = np.random.default_rng(seed)
    t = TC + np.linspace(-0.12, 0.14, n)
    prob = tc.TransitProblem(t - 2461000.0, np.ones(n), np.ones(n), None, PLANET["period"], {}, ("tc",),
                             {"tc": TC - 2461000.0, "k": PLANET["k"], "a_rs": 8.0, "b": 0.3, "q1": 0.4, "q2": 0.3,
                              "c0": 1.0})
    model = prob.model(prob.full([TC - 2461000.0]))
    drift = 0.05 * np.sin(np.linspace(0, 3, n))                         # mag, common to every star
    offsets = np.array([-1.2, -0.4, 0.3, 0.9])
    cm = offsets[None, :] + drift[:, None] + rng.normal(0, noise, (n, 4))
    if missing is not None:
        j, a, b = missing
        cm[a:b + 1, j] = np.nan
    ens = tc.tied_ensemble(cm)
    target = -2.5 * np.log10(model) + drift + rng.normal(0, noise, n)
    flux = 10 ** (-0.4 * (target - ens))
    flux /= np.nanmedian(flux[np.abs(t - TC) > 0.08])
    return t, flux, np.full(n, noise), cm, ["C1", "C2", "C3", "C4"]


def loo(t, flux, err, cm, names):
    main = tc.lsq_fit(t, flux, err, None, PLANET, detrend_airmass=False)
    summ = {"depth": main["depth"], "tc": (main["p"]["tc"], main["err"]["tc"])}
    return tc.leave_one_comp_out(t, flux, err, None, cm, names, PLANET, summ, detrend_airmass=False)


class TestLeaveOneOutMissingComp(unittest.TestCase):
    """#88: a comp missing in frames 61-84 (0-based 60-83) no longer drops those frames from every refit."""

    def test_all_present(self):
        r = loo(*synth())
        self.assertTrue(all(f["n_points"] == 84 for f in r["fits"]))
        self.assertLess(r["depth_spread"], 0.002)
        self.assertFalse([f for f in r["flags"] if f[0] == "warn"])

    def test_comp_missing_late(self):
        r = loo(*synth(missing=(3, 60, 83)))
        n = [f["n_points"] for f in r["fits"]]
        self.assertEqual(n, [84, 84, 84, 84], n)                         # the old np.mean gave 60 each
        for f in r["fits"]:
            self.assertAlmostEqual(f["depth"], DEPTH, delta=0.003)       # old: 2.10-2.67 %
        self.assertLess(r["depth_spread"], 0.003)
        self.assertFalse([f for f in r["flags"] if f[0] == "warn"], r["flags"])
        self.assertEqual(r["ref"]["n_points"], 84)
        self.assertIn("with all comps (84 points", r["note"])
        self.assertIn("full ensemble 1.", tc.comp_systematic_text(r))

    def test_full_frames_give_plain_mean(self):
        cm = np.random.default_rng(1).normal(10, 0.1, (30, 3))
        np.testing.assert_array_equal(tc.tied_ensemble(cm), np.mean(cm, axis=1))

    def test_note_names_a_refit_with_fewer_points(self):
        # C1-C3 missing in frames 0-9: C4 is the only comp there, so the refit without C4 loses those frames.
        t, flux, err, cm, names = synth()
        cm[0:10, 0:3] = np.nan
        r = loo(t, flux, err, cm, names)
        self.assertTrue(r["fewer"] and r["fewer"][0].startswith("C4"), r["fewer"])
        self.assertIn("Fewer points without C4", r["note"])


class TestTiedEnsembleMatchesPhotometry(unittest.TestCase):
    """transit_core.tied_ensemble must stay in step with photometry_core.night_ensemble (one rule, two copies)."""

    def test_same_numbers(self):
        rng = np.random.default_rng(7)
        n = 50
        cm = np.array([-1.0, 0.0, 0.7])[None, :] + rng.normal(0, 0.01, (n, 3))
        cm[30:45, 2] = np.nan
        cm[5:8, 0] = np.nan
        names = ["A", "B", "C"]
        pts = []
        for f in range(n):
            o = core.Obs(jd=2461000.0 + f, mag=10.0, merr=0.01, cmag=0.0, kmag=None, airmass=None, path="",
                         exptime=60.0, flux_var=0.0, flux_comp=0.0)
            o.comp_insts = [float(v) for v in cm[f]]
            pts.append(o)
        ref = core.night_ensemble(pts, names, names)["ens"]
        np.testing.assert_allclose(tc.tied_ensemble(cm), np.array(ref, dtype=float), rtol=0, atol=1e-12)

    def test_short_comp_left_out_like_photometry(self):
        rng = np.random.default_rng(8)
        n = 40
        cm = np.array([-1.0, 0.0, 0.7])[None, :] + rng.normal(0, 0.01, (n, 3))
        cm[10:, 2] = np.nan                                              # measured in 10 of 40: out of the night
        names = ["A", "B", "C"]
        pts = []
        for f in range(n):
            o = core.Obs(jd=2461000.0 + f, mag=10.0, merr=0.01, cmag=0.0, kmag=None, airmass=None, path="",
                         exptime=60.0, flux_var=0.0, flux_comp=0.0)
            o.comp_insts = [float(v) for v in cm[f]]
            pts.append(o)
        ref = core.night_ensemble(pts, names, names)["ens"]
        np.testing.assert_allclose(tc.tied_ensemble(cm), np.array(ref, dtype=float), rtol=0, atol=1e-12)


if __name__ == "__main__":
    unittest.main()


class TestSelftestExitCode(unittest.TestCase):
    """#83: selftest.py ends with an exit code the wrappers read (0 passed, 10 failed, 11 crashed)."""

    def test_exit_line(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(here, "selftest.py"), encoding="utf-8").read()
        self.assertIn('sys.exit(0 if not FAILS else (11 if "crash" in FAILS else 10))', src)
        for name in ("Run self-test.command", "run_linux.sh"):
            text = open(os.path.join(here, name), encoding="utf-8").read()
            self.assertIn("✔ Self-test passed", text)
            self.assertIn("could not run (setup failed", text)
            self.assertNotIn("Done. The log is", text)
        ps1 = open(os.path.join(here, "setup_python.ps1"), "rb").read()
        self.assertTrue(all(b < 128 for b in ps1), "setup_python.ps1 must stay ASCII (Windows PowerShell 5.1)")
        self.assertIn(b"--only-binary=:all:", ps1)
        self.assertIn(b"$OK Self-test passed", ps1)
        bat = open(os.path.join(here, "Run self-test.bat"), "rb").read()
        self.assertNotIn(b"Done. The log is", bat)


class TestPartTimeCompHold(unittest.TestCase):
    """m791: a chosen comp missing in part of the night is named before the fit and in its notes."""

    def _page(self, drops, n=84, comps=("C1", "C2", "C3", "C4"), out=()):
        import types
        import transit_ui as tu
        obs = [types.SimpleNamespace(night="JD2461000") for _ in range(n)]
        info = {"comp_drops": drops, "night_out": list(out)}
        app = types.SimpleNamespace(observations=obs, active_comps=[], comps_used=list(comps),
                                    _night_info=lambda night: info, _frame_ranges=lambda f: f"{f[0]}–{f[-1]}")
        page = types.SimpleNamespace(report=None, app=app, night=types.SimpleNamespace(get=lambda: "JD2461000"))
        return tu.TransitPage._part_time_comp_text(page)

    def test_joanna_case(self):
        text = self._page({"C4": {"frames": list(range(61, 85)), "reasons": {"off the frame edge": 24}}})
        self.assertTrue(text.startswith("C4 is missing in 24 frames"), text)
        self.assertIn("Use comps…", text)

    def test_one_frame_is_quiet(self):
        self.assertEqual(self._page({"C4": {"frames": [12], "reasons": {}}}), "")

    def test_check_star_and_whole_night_out_are_not_named(self):
        self.assertEqual(self._page({"check": {"frames": list(range(30))}}), "")
        self.assertEqual(self._page({"C4": {"frames": list(range(60))}}, out=("C4",)), "")


class TestTwoStarSolveSource(unittest.TestCase):
    """#80: the two-star question is asked on automatic solves too, with Discard as the safe button."""

    def test_source(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(here, "clearv_app.py"), encoding="utf-8").read()
        self.assertNotIn("if n <= 2 and not auto:", src)
        self.assertIn('[("keep", "On real stars: keep the solve"), ("discard", "Not sure: discard it")],\n'
                      '                safe="discard") != "keep"', src)


class TestFlipStepHoldWrites(unittest.TestCase):
    """#81: with a night held (flip step ⚠, answered No), automatic saves still write every other change."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        import clearv_app as ca
        cls.ca = ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test_235.log")
        cls.app = ca.App()
        cls.app.update()
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def _obs(self, night, jd, mag=10.0):
        return core.Obs(jd=jd, mag=mag, merr=0.01, cmag=0.0, kmag=None, airmass=1.2, path=f"{night}_{jd}.fits",
                        exptime=60.0, flux_var=0.0, flux_comp=0.0, night=night)

    def _setup(self, name):
        app = self.app
        path = os.path.join(self.tmp, name)
        a = [self._obs("JD2461000", 2461000.6 + k * 0.01) for k in range(3)]
        b_old = [self._obs("JD2461001", 2461001.6, mag=9.0)]
        b_new = [self._obs("JD2461001", 2461001.6 + k * 0.01, mag=11.0) for k in range(4)]
        app.observations = a + b_new
        app.series_path = path
        app._holds = {"JD2461001": {"night": "JD2461001", "points": b_old, "bin": None, "legacy": False,
                                    "stamp": app._hold_stamp(), "logged": False}}
        app._hold_stale_logged = False
        app.night_notes = {"JD2461000": "a note"}
        return app, path

    def _nights(self, path):
        _meta, pts = core.load_series(path)
        by = {}
        for o in pts:
            by.setdefault(o.night, []).append(o.mag)
        return _meta, by

    def test_hold(self):
        app, path = self._setup("held.cvseries")
        words = app._save_series_words()
        self.assertIn("held", words)
        meta, by = self._nights(path)
        self.assertEqual(by["JD2461000"], [10.0, 10.0, 10.0])
        self.assertEqual(by["JD2461001"], [9.0], "the held night keeps the file's earlier version")
        self.assertEqual((meta.get("night_notes") or {}).get("JD2461000"), "a note")
        self.assertIn("is held", app.series_status_text())
        self.assertTrue(app.series_unsaved(), "held: the file lacks the new night, so unsaved stays on")
        # A new night (none before): left out of the file entirely.
        app._holds["JD2461001"]["points"] = []
        app._save_series_words()
        _meta, by = self._nights(path)
        self.assertEqual(set(by), {"JD2461000"})
        # Save series writes it and ends the hold.
        app._explicit_save = True
        try:
            app._write_series(path)
        finally:
            app._explicit_save = False
        _meta, by = self._nights(path)
        self.assertEqual(by["JD2461001"], [11.0] * 4)

    def test_two_nights_held(self):
        app, path = self._setup("two.cvseries")
        c_new = [self._obs("JD2461002", 2461002.6, mag=12.0)]
        app.observations += c_new
        app._holds["JD2461002"] = {"night": "JD2461002", "points": [], "stamp": app._hold_stamp()}
        app._save_series_words()
        _meta, by = self._nights(path)
        self.assertEqual(by.get("JD2461001"), [9.0])
        self.assertNotIn("JD2461002", by)
        self.assertIn("are held", app.series_status_text())

    def test_comps_changed_since_hold_writes_nothing(self):
        app, path = self._setup("stale.cvseries")
        app._save_series_words()
        before = open(path, "rb").read()
        app.comp_catalog_mags = dict(app.comp_catalog_mags or {}, NEWCOMP=11.111)
        words = app._save_series_words()
        self.assertIn("Not saved", words)
        self.assertEqual(open(path, "rb").read(), before)
