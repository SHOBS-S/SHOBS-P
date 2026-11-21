"""2.4.0 step 7a: #91 the excess chi2nu Suspect test (a star's chi2nu against its own errors, judged against stars as
bright that night; Suspect at 3x scatter OR excess chi2nu 2.5, John 21 Nov) and #92's Suspect stage (the word
Suspect, the night's suspects file beside its results, "Suspect again (N nights)", Add as suspect by hand, the
Gaia DR3 ID)."""
import json
import math
import os
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import photometry_core as core  # noqa: E402

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


def make_field(n_stars=300, n_frames=90, seed=7, var=None, honest=1.0):
    """frames x stars magnitudes with per-star Gaussian noise sigma (fainter = noisier), a common zero point per
    frame, and the errors table the model would predict (sigma x honest). var: {star: extra light-curve (n_frames)}."""
    rng = np.random.default_rng(seed)
    mag = np.linspace(-9.0, -3.0, n_stars)
    sigma = 0.002 * 10 ** (0.2 * (mag + 9.0))     # 2 mmag at the bright end, 32 mmag at the faint end
    zp = rng.normal(0.0, 0.02, n_frames)          # clouds / airmass, common to every star
    inst = mag[None, :] + zp[:, None] + rng.normal(0.0, 1.0, (n_frames, n_stars)) * sigma[None, :]
    for k, curve in (var or {}).items():
        inst[:, k] += curve
    merr = np.tile(sigma * honest, (n_frames, 1))
    return inst, merr, sigma


class TestErrors(unittest.TestCase):
    def test_photon_and_sky(self):
        flux = np.array([10000.0, 400.0, -5.0, np.nan])
        noise = np.array([30.0, 30.0, 30.0, 30.0])
        err = core.scan_mag_errors(flux, noise, 2.0)
        want = 1.0857362 * math.sqrt(30.0 ** 2 + 10000.0 / 2.0) / 10000.0
        self.assertAlmostEqual(err[0], want, places=9)
        self.assertGreater(err[1], err[0])            # fainter: bigger error
        self.assertTrue(np.isnan(err[2]) and np.isnan(err[3]))   # no positive flux: no error
        # no gain known: 1 e-/unit, never a crash
        self.assertTrue(np.isfinite(core.scan_mag_errors(flux, noise, None)[0]))
        self.assertTrue(np.isfinite(core.scan_mag_errors(flux, None, 0)[0]))

    def test_zero_point_error(self):
        inst, merr, sigma = make_field()
        med = np.nanmedian(inst, axis=0)
        zp = np.nanmedian(inst - med[None, :], axis=1)
        ref = np.arange(0, 150)
        e = core.frame_zero_point_errors(inst, ref, med, zp)
        self.assertEqual(e.shape, (inst.shape[0],))
        self.assertTrue(np.all(e > 0))
        self.assertLess(np.median(e), 0.002)          # 150 stars: well under one star's error
        few = core.frame_zero_point_errors(inst, np.array([0, 1]), med, zp)
        self.assertTrue(np.all(few == 0.0))           # fewer than 3 reference stars: no zero-point error

    def test_chi2_cap(self):
        corrected = np.zeros((20, 2))
        corrected[5, 1] = 1.0                          # one wild point (cosmic ray), 100 sigma
        sigma = np.full((20, 2), 0.01)
        chi2 = core.chi2nu_against_errors(corrected, np.zeros(2), sigma)
        self.assertAlmostEqual(chi2[0], 0.0)
        self.assertAlmostEqual(chi2[1], core.CHI2_TERM_CAP / 19)   # capped: 25, not 10 000
        few = core.chi2nu_against_errors(np.zeros((2, 1)), np.zeros(1), np.full((2, 1), 0.01))
        self.assertTrue(np.isnan(few[0]))


class TestExcessChi2(unittest.TestCase):
    def test_quiet_field_reads_one(self):
        inst, merr, _ = make_field()
        st = core.field_variability(inst, merr=merr)
        self.assertIn("excess_chi2", st)
        good = st["usable"]
        self.assertAlmostEqual(float(np.nanmedian(st["chi2nu"][good])), 1.0, delta=0.15)
        self.assertAlmostEqual(float(np.nanmedian(st["excess_chi2"][good])), 1.0, delta=0.1)
        self.assertLess(int(np.sum(st["excess_chi2"][good] >= core.SUSPECT_EXCESS_CHI2)), 3)

    def test_error_model_reading_low_cancels(self):
        """A night whose errors read 30 % low lifts every star alike: the excess chi2nu does not change much."""
        inst, merr, _ = make_field()
        honest = core.field_variability(inst, merr=merr)["excess_chi2"]
        low = core.field_variability(inst, merr=merr * 0.7)
        self.assertAlmostEqual(float(np.nanmedian(low["chi2nu"])), 1.0 / 0.49, delta=0.3)
        np.testing.assert_allclose(low["excess_chi2"], honest, rtol=0.1)   # the cap and the zero-point error do not scale

    def test_low_amplitude_pulsator_is_caught_by_its_own_errors(self):
        """An S033-like star: a sine of about 2x its own noise is under the 3x scatter level but stands well over
        its own errors."""
        n_frames = 90
        t = np.linspace(0, 0.3, n_frames)
        k = 150
        inst0, _, sigma = make_field()
        amp = 2.4 * sigma[k]
        inst, merr, _ = make_field(var={k: amp * np.sin(2 * np.pi * t / 0.103)})
        st = core.field_variability(inst, merr=merr)
        self.assertLess(st["excess"][k], core.SUSPECT_FACTOR)
        self.assertGreaterEqual(st["excess_chi2"][k], core.SUSPECT_EXCESS_CHI2)
        self.assertEqual(core.suspect_reason(st["excess"][k], st["excess_chi2"][k]), "own errors")

    def test_shape_mismatch_and_no_errors(self):
        inst, merr, _ = make_field()
        with self.assertRaises(ValueError):
            core.field_variability(inst, merr=merr[:-1])
        st = core.field_variability(inst)            # without errors: as before, no chi2 keys
        self.assertNotIn("excess_chi2", st)

    def test_reason_and_score(self):
        self.assertEqual(core.suspect_reason(3.2, 1.0), "scatter")
        self.assertEqual(core.suspect_reason(2.1, 2.8), "own errors")
        self.assertEqual(core.suspect_reason(3.0, 2.5), "both")    # the levels themselves count
        self.assertEqual(core.suspect_reason(2.9, 2.49), "")
        self.assertEqual(core.suspect_reason(2.0, float("nan")), "")
        self.assertEqual(core.suspect_reason(2.0, 9.0, chi2_limit=0), "")   # 0 switches the second test off
        self.assertAlmostEqual(core.suspect_score(1.5, 5.0), 2.0)
        self.assertAlmostEqual(core.suspect_score(6.0, float("nan")), 2.0)
        self.assertEqual(core.suspect_score(float("nan"), float("nan")), float("-inf"))
        self.assertEqual((core.SUSPECT_FACTOR, core.SUSPECT_EXCESS_CHI2), (3.0, 2.5))   # John, 21 Nov


def scan_result(n=5):
    labels = [f"S{k + 1:03d}" for k in range(n)]
    return {
        "labels": labels, "xy": [(10.0 * k, 20.0 * k) for k in range(n)],
        "radec": [(42.0 + 0.01 * k, 50.0 + 0.01 * k) for k in range(n)],
        "mag": np.linspace(11, 13, n), "scatter": np.full(n, 0.01), "expected": np.full(n, 0.004),
        "excess": np.array([3.5, 2.1, 1.0, 1.1, 0.9][:n]), "excess_chi2": np.array([6.0, 2.8, 1.0, 1.0, 1.0][:n]),
        "usable": np.ones(n, bool), "n_good": np.full(n, 90), "candidates": [0, 1],
        "suspect_by": {0: "both", 1: "own errors"}, "gaia": ["Gaia DR3 1", "", "", "", ""],
        "periods": {1: {"period": 0.103, "power": 0.6, "ls": {}}}, "jd": np.array([2461358.5, 2461358.7]),
        "frames": 90, "excess_limit": 3.0, "chi2_limit": 2.5, "vsx": None, "bv": None, "vr": None,
    }


class TestSuspectsFile(unittest.TestCase):
    def test_entries_and_document(self):
        r = scan_result()
        doc = core.suspects_document(r, "GSC 03308-00462", "night of 13-14 Nov 2026", "TG", "2.4.0")
        self.assertEqual(doc["kind"], "SHOBS-P Discovery suspects")
        self.assertEqual(doc["levels"], {"factor": 3.0, "excess_chi2": 2.5})
        s = doc["suspects"]
        self.assertEqual([e["label"] for e in s], ["S001", "S002"])
        self.assertEqual(s[0]["gaia_dr3"], "Gaia DR3 1")
        self.assertEqual(s[1]["suspect_by"], "own errors")
        self.assertAlmostEqual(s[1]["period_days"], 0.103)
        self.assertEqual(s[1]["excess_chi2"], 2.8)
        json.dumps(doc)                                 # plain JSON

    def test_same_star(self):
        a = {"gaia_dr3": "Gaia DR3 7", "ra_deg": 42.0, "dec_deg": 50.0}
        self.assertTrue(core.same_suspect(a, {"gaia_dr3": "Gaia DR3 7", "ra_deg": 10.0, "dec_deg": 10.0}))
        self.assertFalse(core.same_suspect(a, {"gaia_dr3": "Gaia DR3 8", "ra_deg": 42.0, "dec_deg": 50.0}))
        b = {"gaia_dr3": "", "ra_deg": 42.0 + 1.0 / 3600 / math.cos(math.radians(50)), "dec_deg": 50.0}   # 1"
        self.assertTrue(core.same_suspect(a, b))       # one has no Gaia ID: by position
        c = dict(b, ra_deg=42.0 + 5.0 / 3600 / math.cos(math.radians(50)))   # 5"
        self.assertFalse(core.same_suspect(a, c))
        self.assertFalse(core.same_suspect({"gaia_dr3": ""}, {"gaia_dr3": ""}))   # nothing to go on

    def test_repeats_and_files_of_the_same_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            field = os.path.join(tmp, "GSC 03308-00462")
            for night, out in (("8Nov26", "Output"), ("12Nov26", "SHOBS-P Output"), ("13Nov26", "Output")):
                os.makedirs(os.path.join(field, night, out))
            here = os.path.join(field, "13Nov26", "Output")
            r = scan_result()
            old = core.suspects_document(r, "f", "night of 8-9 Nov 2026", "TG", "2.4.0")
            old["jd_first"] = 2461353.6
            with open(os.path.join(field, "8Nov26", "Output", "f_TG_09-NOV-2026" + core.SUSPECTS_SUFFIX), "w") as fh:
                json.dump(old, fh)
            other = core.suspects_document(dict(r, candidates=[0]), "f", "night of 12-13 Nov 2026", "TG", "2.4.0")
            other["jd_first"] = 2461357.6
            with open(os.path.join(field, "12Nov26", "SHOBS-P Output", "f_TG_13-NOV-2026" + core.SUSPECTS_SUFFIX),
                      "w") as fh:
                json.dump(other, fh)
            with open(os.path.join(here, "f_TG_14-NOV-2026" + core.SUSPECTS_SUFFIX), "w") as fh:
                json.dump(old, fh)                      # tonight's own file: never counted as another night
            with open(os.path.join(field, "8Nov26", "Output", "x" + core.SUSPECTS_SUFFIX), "w") as fh:
                fh.write("not json")
            files = core.other_suspect_files(here)
            self.assertEqual(len(files), 3)
            docs = [core.read_suspects_file(p) for p in files]
            self.assertEqual(sum(d is None for d in docs), 1)   # the damaged one is skipped
            reps = core.suspect_repeats(core.suspect_entries(r), docs)
            self.assertEqual(reps[0], ["night of 8-9 Nov 2026", "night of 12-13 Nov 2026"])
            self.assertEqual(reps[1], ["night of 8-9 Nov 2026"])
            self.assertEqual(core.suspect_again_text(len(reps[0])), "Suspect again (3 nights)")
            self.assertEqual(core.suspect_again_text(0), "")
            self.assertEqual(core.other_suspect_files(""), [])

    def test_csv_columns(self):
        r = scan_result()
        r.update({"suspect_nights": {0: ["night of 8-9 Nov 2026"]}, "saturated": [], "near_sat": []})
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "s.csv")
            core.write_scan_candidates(p, r)
            with open(p) as fh:
                lines = fh.read().splitlines()
            head = lines[1].split(",")
            for col in ("excess_chi2", "suspect_by", "gaia_dr3", "suspect_nights"):
                self.assertIn(col, head)
            row = dict(zip(head, lines[2].split(",")))
            self.assertEqual((row["suspect_by"], row["gaia_dr3"], row["suspect_nights"]), ("both", "Gaia DR3 1", "2"))
            row2 = dict(zip(head, lines[3].split(",")))
            self.assertEqual((row2["excess_chi2"], row2["suspect_nights"]), ("2.80", "1"))
            p2 = os.path.join(tmp, "all.csv")
            r["light_curves"] = np.zeros((2, 5))
            core.write_scan_stars(p2, r)
            with open(p2) as fh:
                head2 = fh.read().splitlines()[1].split(",")
            self.assertIn("suspect_rank", head2)
            self.assertIn("excess_chi2", head2)
            self.assertNotIn("candidate_rank", head2)


class TestReviewFixes(unittest.TestCase):
    """Fable review of step 7a."""

    def test_short_runs_raise_the_level(self):   # M1
        self.assertEqual(core.chi2_level(2.5, 97), 2.5)
        self.assertAlmostEqual(core.chi2_level(2.5, 10), 1 + 5 * math.sqrt(2 / 9))
        self.assertEqual(core.chi2_level(0, 10), 0.0)          # the test switched off stays off
        self.assertEqual(core.chi2_level(2.5, 2), float("inf"))

    def test_pure_noise_short_run_lists_no_suspects(self):   # M1
        for n_frames in (10, 12, 15):
            hits = 0
            for seed in range(5):
                inst, merr, _ = make_field(n_stars=700, n_frames=n_frames, seed=100 + seed)
                st = core.field_variability(inst, merr=merr)
                for k in np.where(st["usable"])[0]:
                    lv = core.chi2_level(core.SUSPECT_EXCESS_CHI2, st["n_good"][k])
                    if core.suspect_reason(np.nan, st["excess_chi2"][k], chi2_limit=lv):
                        hits += 1
            self.assertLessEqual(hits / 5, 1.0, n_frames)       # was 7.8 per scan at 10 frames (now 0.8 of 700)

    def test_tonight_is_never_another_night(self):   # S2
        r = scan_result()
        e = core.suspect_entries(r)
        same = core.suspects_document(r, "f", "night of 13-14 Nov 2026", "R", "2.4.0")
        overlap = core.suspects_document(r, "f", "13-14 Nov (other label)", "V", "2.4.0")
        overlap.update({"jd_first": 2461358.6, "jd_last": 2461358.9})
        other = core.suspects_document(r, "f", "night of 8-9 Nov 2026", "V", "2.4.0")
        other.update({"jd_first": 2461353.6, "jd_last": 2461353.9})
        reps = core.suspect_repeats(e, [same, overlap, other], night="night of 13-14 Nov 2026",
                                    jd_range=(2461358.5, 2461358.7))
        self.assertEqual(reps[0], ["night of 8-9 Nov 2026"])

    def test_brackets_and_drive_root(self):   # S3, S6
        with tempfile.TemporaryDirectory() as tmp:
            field = os.path.join(tmp, "Field [A]")
            for night in ("n1", "n2"):
                os.makedirs(os.path.join(field, night, "Output"))
            with open(os.path.join(field, "n1", "Output", "x" + core.SUSPECTS_SUFFIX), "w") as fh:
                fh.write("{}")
            self.assertEqual(len(core.other_suspect_files(os.path.join(field, "n2", "Output"))), 1)
        root = os.path.abspath(os.sep)
        self.assertEqual(core.other_suspect_files(os.path.join(root, "X", "Output")), [])

    def test_odd_files(self):   # S4
        with tempfile.TemporaryDirectory() as tmp:
            def put(doc):
                p = os.path.join(tmp, "a.json")
                with open(p, "w") as fh:
                    json.dump(doc, fh)
                return core.read_suspects_file(p)
            base = {"kind": "SHOBS-P Discovery suspects", "version": 1, "suspects": ["junk", {"label": "S1"}],
                    "jd_first": "x", "night": None}
            d = put(base)
            self.assertEqual(d["suspects"], [{"label": "S1"}])
            self.assertIsNone(d["jd_first"])
            self.assertEqual(d["night"], "")
            self.assertIsNone(put(dict(base, version=2)))       # a later version's file
            e = core.suspect_entries(scan_result())
            core.suspect_repeats(e, [d, put(dict(base, jd_first=2461353.5))])   # mixed jd_first: no TypeError

    def test_empty_gaia_name(self):   # N8
        a = {"gaia_dr3": "Gaia DR3 ", "ra_deg": 1.0, "dec_deg": 1.0}
        self.assertFalse(core.same_suspect(a, {"gaia_dr3": "Gaia DR3", "ra_deg": 9.0, "dec_deg": 9.0}))
        self.assertTrue(core.same_suspect(a, {"gaia_dr3": "Gaia DR3 5", "ra_deg": 1.0, "dec_deg": 1.0}))

    def test_nan_sky_noise(self):   # N6
        self.assertTrue(np.isnan(core.scan_mag_errors(np.array([1000.0]), np.array([np.nan]), 1.0)[0]))


class TestWording(unittest.TestCase):
    def test_discovery_says_suspect(self):
        src = _src()
        for old in ('"Candidate at (× normal)"', '"Mark candidates on image"', '"Export candidates CSV…"',
                    '"Clear candidate marks"', 'text="Candidates only"', '"Candidate to check first"'):
            self.assertNotIn(old, src)
        for new in ('"Suspect at (× normal)"', '"or at (χ²ν × normal)"', '"Add as suspect"', '"Mark suspects on image"'):
            self.assertIn(new, src)


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestInTheApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test240_7a.log")
        cls.ca = ca
        cls.app = ca.App()
        cls.app.set_mode("discovery")
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

    def test_setting_defaults(self):
        self.assertEqual(self.app.scan_excess.get(), 3.0)
        self.assertEqual(self.app.scan_excess_chi2.get(), 2.5)

    def test_save_suspects_and_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            field = os.path.join(tmp, "Field X")
            old_out = os.path.join(field, "8Nov26", "Output")
            new_out = os.path.join(field, "13Nov26", "Output")
            os.makedirs(old_out)
            r = scan_result()
            old = core.suspects_document(r, "Field X", "night of 8-9 Nov 2026", "TG", "2.4.0")
            old.update({"jd_first": 2461353.6, "jd_last": 2461353.9})   # another night (Fable S2)
            with open(os.path.join(old_out, "Field_X_TG_09-NOV-2026" + core.SUSPECTS_SUFFIX), "w") as fh:
                json.dump(old, fh)
            self.app.star_id.set("Field X")
            self.app.output_dir.set(new_out)
            try:
                path = self.app._save_suspects(r)
            finally:
                self.app.output_dir.set("")
            self.assertTrue(path.endswith(core.SUSPECTS_SUFFIX))
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(os.path.dirname(path), new_out)
            doc = core.read_suspects_file(path)
            self.assertEqual(len(doc["suspects"]), 2)
            self.assertEqual(doc["suspects"][0]["also_on"], ["night of 8-9 Nov 2026"])
            self.assertEqual(sorted(r["suspect_nights"]), [0, 1])
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_no_output_folder(self):
        r = scan_result()
        self.app.output_dir.set("")
        self.assertEqual(self.app._save_suspects(r), "")
        self.assertEqual(r["suspect_nights"], {})
        self.assertTrue(any("no Output folder" in t for t in self.lines))

    def test_near_misses_stay_while_all_suspects_are_by_hand(self):   # Fable S5
        import tkinter as tk
        r = scan_result()
        r.update({"candidates": [], "suspect_by": {}, "light_curves": np.zeros((40, 5)),
                  "jd": np.linspace(2461358.5, 2461358.8, 40), "period_range": (0.02, 2.0), "saturated": [],
                  "near": [2, 3, 4], "watched": [], "near_sat": [], "names": [""] * 5, "calibrated": False})
        top = tk.Toplevel(self.app)
        try:
            view = self.ca.ScanView(top, self.app, r, on_close=top.destroy)
            self.assertIn("0 suspect(s)", view.header_text.get())
            view.selected, view.picked = 3, []
            view.add_suspect()
            listed = sorted(view.rows.values())
            self.assertEqual(listed, [2, 3, 4])                # 3 as a suspect, 2 and 4 still near misses
            self.assertIn("1 suspect(s) (1 added by hand)", view.header_text.get())
            self.assertIn("nearest misses", view.header_text.get())
        finally:
            top.destroy()

    def test_electrons(self):
        k = self.app._scan_electrons([], 1, core.MONO, "green", None, False)
        self.assertEqual(k, 1.0)                        # no header: 1 e-/unit, never a crash

    def test_blank_level_switches_off(self):   # Fable N2
        self.app.scan_excess_chi2.set(2.5)
        self.assertEqual(self.app._scan_chi2_limit(), 2.5)
        self.app.tk.globalsetvar(self.app.scan_excess_chi2._name, "")
        try:
            self.assertEqual(self.app._scan_chi2_limit(), 0.0)
            self.app.tk.globalsetvar(self.app.scan_excess_chi2._name, "abc")
            with self.assertRaises(ValueError):
                self.app._scan_chi2_limit()
        finally:
            self.app.scan_excess_chi2.set(2.5)

    def test_where_is_fixed_at_scan_start_and_hand_suspects_kept(self):   # Fable S6, S7
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "F", "n1", "Output")
            r = scan_result()
            r["suspects_where"] = {"folder": out, "field": "Field Y", "filter": "TG", "points": "stacks"}
            r["suspect_by"][3] = "by hand"
            r["candidates"].append(3)
            self.app.output_dir.set(os.path.join(tmp, "elsewhere"))
            self.app.star_id.set("Something else")
            try:
                path = self.app._save_suspects(r)
            finally:
                self.app.output_dir.set("")
            self.assertEqual(os.path.dirname(path), out)
            self.assertTrue(os.path.basename(path).startswith("Field_Y_TG_"))
            self.assertEqual(core.read_suspects_file(path)["points"], "stacks")
            # a new scan of the night: the star added by hand comes back
            r2 = scan_result()
            r2["suspects_where"] = dict(r["suspects_where"])
            r2.update({"light_curves": np.random.default_rng(2).normal(12, 0.01, (40, 5)),
                       "jd": np.linspace(2461358.5, 2461358.8, 40), "period_range": (0.02, 2.0)})
            self.app._save_suspects(r2, carry_hand=True)
            self.assertIn(3, r2["candidates"])
            self.assertEqual(r2["suspect_by"][3], "by hand")
            self.assertIn(3, r2["periods"])             # Fable re-review: with its periodogram

    def test_add_as_suspect(self):
        import tkinter as tk
        r = scan_result()
        r.update({"light_curves": np.random.default_rng(1).normal(12, 0.01, (40, 5)),
                  "jd": np.linspace(2461358.5, 2461358.8, 40), "period_range": (0.02, 2.0), "saturated": [],
                  "near": [2, 3], "watched": [], "near_sat": [], "names": [""] * 5, "calibrated": False})
        top = tk.Toplevel(self.app)
        try:
            view = self.ca.ScanView(top, self.app, r, on_close=top.destroy)
            view.selected = 2
            view.picked = []
            self.app.output_dir.set("")
            view.add_suspect()
            self.assertIn(2, r["candidates"])
            self.assertEqual(r["suspect_by"][2], "by hand")
            self.assertIn(2, r["periods"])              # its periodogram, as the scan's own suspects have
            self.assertIn(2, view.rows.values())        # listed in the table
            n = len(r["candidates"])
            view.add_suspect()                          # again: nothing changes
            self.assertEqual(len(r["candidates"]), n)
            self.assertIn("Already a suspect", self.app.status.get())
            self.assertIn("1 added by hand", view.header_text.get())
            view.tree.selection_set([iid for iid, i in view.rows.items() if i == 1])
            view.remove_added_suspect()                 # a scan suspect: stays
            self.assertIn(1, r["candidates"])
            self.assertIn("Only a suspect you added by hand", self.app.status.get())
            view.tree.selection_set([iid for iid, i in view.rows.items() if i == 2])
            view.remove_added_suspect()
            self.assertNotIn(2, r["candidates"])
            self.assertNotIn(2, r["suspect_by"])
            view.selected, view.picked = 2, []
            view.add_suspect()                          # put it back for the checks below
            vals = [view.tree.item(iid, "values") for iid, i in view.rows.items() if i == 1][0]
            cols = list(view.tree["columns"])
            self.assertEqual(vals[cols.index("why")], "own errors")
            self.assertEqual(vals[cols.index("excess_chi2")], "2.8")
        finally:
            top.destroy()


if __name__ == "__main__":
    unittest.main()
