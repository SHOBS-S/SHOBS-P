"""2.4.0 step 7d (John, 22 Nov 2026): the Suspect -> Watch -> Candidate -> Campaign promotion system is removed.

What stays, and is tested here: the quiet test behind Check neighbors (neighbors.py), Check neighbors on a field scan,
Outside data as a plain tool (ZTF, TESS, ATLAS; the certificate retry), both on the Field scan page's Checks group.
What is gone, and is checked gone: promotion.py, registry.py, the Candidates window, the gates, Watch reach-back,
Check neighbors on a series; an older series file that carries gate reports still loads and saves without them."""
import glob
import importlib.util
import io
import json
import os
import re
import sys
import time
import unittest
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import neighbors as nb  # noqa: E402
import outside_data as od  # noqa: E402
import photometry_core as core  # noqa: E402

try:
    from tests import test_240_m as m4  # noqa: E402  (the series fixtures and the App test case)
except Exception:   # a direct run from the tests folder
    import test_240_m as m4  # noqa: E402


def _src(name="clearv_app.py"):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


# ---- the quiet test ------------------------------------------------------------------------------------------------
class TestQuietTest(unittest.TestCase):
    def setUp(self):
        t = np.concatenate([2461353.6 + np.arange(90) * 0.0021 + d for d in (0, 1, 2, 4)])
        night = [f"JD{2461353 + int(x - 2461353.1)}" for x in t]
        rng = np.random.default_rng(3)
        self.P = 0.1027
        wave = 0.01 * np.sin(2 * np.pi * t / self.P)
        self.tgt = nb.make_curve("T", t, 13 + wave + rng.normal(0, 0.003, t.size), night, role="target")
        self.quiet = nb.make_curve("Q", t, 12 + rng.normal(0, 0.003, t.size), night, role="comp")
        self.loud = nb.make_curve("L", t, 12 + wave + rng.normal(0, 0.003, t.size), night, role="comp")

    def test_a_quiet_and_a_loud_neighbor(self):
        nc = nb.neighbor_check(self.tgt, [self.quiet, self.loud], period=self.P)
        self.assertEqual([r["quiet"] for r in nc["rows"]], [True, False])
        self.assertEqual((nc["n_quiet"], nc["n_measured"]), (1, 2))
        self.assertEqual(nc["bottom"], "Neighbors quiet at P: 1 of 2 measured")
        self.assertAlmostEqual(nc["amp"], 0.01, delta=0.001)
        q, loud = nc["rows"]
        self.assertLess(q["power_share"], nb.NULL_POWER_FRAC)
        self.assertLess(q["amp"], q["amp_limit"])
        self.assertGreater(loud["power_share"], nb.NULL_POWER_FRAC)

    def test_the_period_is_found_when_none_is_given(self):
        nc = nb.neighbor_check(self.tgt, [self.quiet], min_period=0.05, max_period=0.3)
        self.assertAlmostEqual(nc["period"], self.P, delta=0.002)
        self.assertTrue(nc["rows"][0]["quiet"])

    def test_null_test_row_is_the_same_test(self):
        nc = nb.neighbor_check(self.tgt, [self.loud], period=self.P)
        row = nb.null_test_row(self.loud, self.P, nc["power"], nc["amp"])
        self.assertEqual(row, nc["rows"][0])
        self.assertIsNone(nb.null_test_row(self.loud, 0.0, nc["power"], nc["amp"]))

    def test_too_few_points(self):
        c = nb.make_curve("T", [1, 2, 3], [1, 1, 1], ["a"] * 3)
        self.assertIn("too few", nb.neighbor_check(c, []).get("bottom"))
        few = nb.make_curve("F", [1, 2, 3], [1, 1, 1], ["a"] * 3)
        nc = nb.neighbor_check(self.tgt, [few], period=self.P)
        self.assertIsNone(nc["rows"][0]["quiet"])
        self.assertIn("1 not judged", nc["bottom"])

    def test_make_curve_drops_bad_points(self):
        c = nb.make_curve("T", [1.0, float("nan"), 3.0], [1.0, 2.0, float("nan")], ["a", "a", "b"])
        self.assertEqual((c.jd.size, c.night), (1, ["a"]))

    def test_sep_arcsec(self):
        self.assertAlmostEqual(nb.sep_arcsec(42.05, 50.88, 42.05, 50.89), 36.0, delta=0.01)


# ---- outside data: a plain tool ------------------------------------------------------------------------------------
def ztf_csv(P=0.1029705, n=600, seed=4):
    rng = np.random.default_rng(seed)
    t = 2458200 + np.sort(rng.uniform(0, 2500, n))
    m = 13.7 + 0.01 * np.sin(2 * np.pi * t / P) + rng.normal(0, 0.004, n)
    rows = ["oid,expid,hjd,mjd,mag,magerr,catflags,filtercode,ra,dec"]
    for k, (a, b) in enumerate(zip(t, m)):
        rows.append(f"1,{k},{a:.6f},{a - 2400000.5:.6f},{b:.5f},0.004,{0 if k % 10 else 512},"
                    f"{'zg' if k % 2 else 'zr'},42.05,50.88")
    return "\n".join(rows)


def qlp_fits(P=0.1029705, n=3000, seed=5):
    from astropy.io import fits
    rng = np.random.default_rng(seed)
    t = 3260.0 + np.arange(n) * 200 / 86400.0
    f = 1000 * (1 + 0.01 * np.sin(2 * np.pi * (t + 2457000) / P)) + rng.normal(0, 2, n)
    cols = fits.ColDefs([fits.Column(name="TIME", format="D", array=t),
                         fits.Column(name="KSPSAP_FLUX", format="E", array=f),
                         fits.Column(name="QUALITY", format="J", array=np.zeros(n, int))])
    buf = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(), fits.BinTableHDU.from_columns(cols)]).writeto(buf)
    return buf.getvalue()


def fake_get(down=()):
    qlp = qlp_fits()

    def get(url, timeout=60, data=None):
        for d in down:
            if d in url:
                raise od.ServiceDown("HTTP 503")
        if "irsa" in url:
            return ztf_csv().encode()
        if "tesscut" in url:
            return json.dumps({"results": [{"sector": "0058"}, {"sector": "0085"}]}).encode()
        if "invoke" in url:
            return json.dumps({"data": [{"ID": 428049570, "Tmag": 12.9, "ra": 42.05, "dec": 50.88}]}).encode()
        if "hlsp_qlp" in url:
            return qlp if "s0058" in url else b""
        raise AssertionError(url)
    return get


def fake_vizier(*a, **k):
    return [{"ATOID": "J042.0507+50.8818", "fp-LSper": "0.249402", "fp-period": "", "fp-lngfitper": "41.900433",
             "Class": "dubious"}]


_S033 = {}


def s033_result(period=0.10279):
    """The S033-like check, made once per period (the searches take a while): ZTF and TESS find 0.10297 d."""
    if period not in _S033:
        _S033[period] = od.check(42.05, 50.88, period, min_period=0.05, max_period=1.0, get=fake_get(),
                                 vizier=fake_vizier)
    return json.loads(json.dumps(_S033[period]))


class TestOutside(unittest.TestCase):
    def test_s033_like_check(self):
        res = s033_result()
        self.assertEqual(res["agree"], ["ZTF", "TESS"])
        self.assertEqual(res["surveys"]["ZTF"]["relation"], "same")
        self.assertAlmostEqual(res["surveys"]["ZTF"]["period"], 0.1029705, delta=0.0001)
        self.assertEqual(res["surveys"]["ATLAS"]["relation"], "other")
        self.assertIn("dubious", res["surveys"]["ATLAS"]["note"])
        self.assertIn("no QLP light curve for 85", res["surveys"]["TESS"]["detail"])
        self.assertIn("ZTF and TESS", res["summary"])

    def test_same_period_within_one_percent_only(self):
        res = s033_result(0.1050)   # 2% off the surveys' period
        self.assertEqual(res["agree"], [])
        self.assertEqual(res["surveys"]["ZTF"]["relation"], "other")
        self.assertIn("No outside survey confirms the period", res["summary"])

    def test_a_service_down_fails_softly(self):
        res = od.check(42.05, 50.88, 0.10279, get=fake_get(down=("irsa", "mast.stsci.edu")), vizier=fake_vizier)
        self.assertEqual(res["surveys"]["ZTF"]["status"], "down")
        self.assertEqual(res["surveys"]["TESS"]["status"], "down")
        self.assertEqual(res["agree"], [])
        self.assertIn("ZTF did not answer", res["summary"])

    def test_relation(self):
        self.assertEqual(od.relation(0.10297, 0.10279), "same")
        P = 0.10297
        self.assertEqual(od.relation(1 / (1 / P - 1), P), "alias")
        self.assertEqual(od.relation(2 * P, P), "harmonic")
        self.assertEqual(od.relation(0.3, P), "other")
        self.assertIsNone(od.relation(None, P))

    def test_ztf_parse_keeps_clean_points_only(self):
        bands = od.parse_ztf_csv(ztf_csv(n=100))
        self.assertEqual(set(bands), {"zg", "zr"})
        self.assertEqual(sum(v[0].size for v in bands.values()), 90)
        self.assertEqual(od.parse_ztf_csv(""), {})
        with self.assertRaises(od.BadAnswer):
            od.parse_ztf_csv("no data")

    def test_dubious_atlas_never_agrees_and_weak_peaks_do_not_count(self):
        def query(*a):
            return [{"ATOID": "J1", "fp-LSper": "0.249402", "fp-period": "", "fp-lngfitper": "", "Class": "dubious"}]
        dub = od.atlas(42.05, 50.88, 0.249402, query=query)
        self.assertEqual(dub["relation"], "same")
        self.assertFalse(od.counts(dub))
        res = od.check(42.05, 50.88, 0.249402, surveys=("ATLAS",), vizier=query)
        self.assertEqual(res["agree"], [])
        self.assertIn("not counted (dubious in ATLAS)", res["summary"])
        weak = {"status": "ok", "period": 0.1, "relation": "same", "fap": 0.2}
        self.assertFalse(od.counts(weak))
        self.assertTrue(od.counts(dict(weak, fap=1e-6)))

    def test_unreadable_irsa_answer_is_an_error_not_none(self):
        r = od.ztf(42.05, 50.88, 0.1, 0.02, 20, get=lambda *a, **k: b"<html>Service unavailable</html>")
        self.assertEqual(r["status"], "error")

    def test_nothing_feeds_gates_any_more(self):
        self.assertFalse(hasattr(od, "for_gates"))
        src = _src("outside_data.py") + _src("neighbors_ui.py")
        for word in ("for_gates", "OUTSIDE_CLEARS", "cleared_by", "clear_with_outside", "may clear"):
            self.assertNotIn(word, src)

    def test_the_report_text(self):
        from neighbors_ui import outside_text
        res = s033_result()
        text = outside_text("S033", res)
        self.assertIn("Outside data for S033", text)
        self.assertIn("Our period: 0.102790 d", text)
        self.assertIn("Outside data agrees: ZTF and TESS.", text)
        for s in ("ZTF: ", "TESS: ", "ATLAS: "):
            self.assertIn(s, text)
        self.assertIn("TESS pixels are 21″", text)
        self.assertNotIn("gate", text.lower())


class TestCertificateRetry(unittest.TestCase):
    """22 Nov 2026: IRSA/MAST failed the Windows certificate check on John's PC."""

    def _err(self):
        import ssl
        import urllib.error
        return urllib.error.URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] x"))

    def test_retries_with_bundled_roots(self):
        calls = []

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=None, context=None):
            calls.append(context)
            if context is None:
                raise self._err()
            return R(b"ok")
        with mock.patch("urllib.request.urlopen", fake), mock.patch.object(od, "_bundled_context", lambda: object()):
            self.assertEqual(od.http_get("https://x"), b"ok")
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0])
        self.assertIsNotNone(calls[1])

    def test_plain_words_when_both_fail(self):
        def fake(req, timeout=None, context=None):
            raise self._err()
        with mock.patch("urllib.request.urlopen", fake), mock.patch.object(od, "_bundled_context", lambda: object()):
            with self.assertRaises(od.ServiceDown) as cm:
                od.http_get("https://x")
        self.assertIn("certificate problem", str(cm.exception))
        self.assertNotIn("_ssl.c", str(cm.exception))

    def test_bundled_context_exists(self):
        self.assertIsNotNone(od._bundled_context())

    def test_certifi_is_a_requirement(self):
        self.assertIn("certifi", _src("requirements.txt"))


# ---- what is gone --------------------------------------------------------------------------------------------------
class TestPromotionIsGone(unittest.TestCase):
    def app_files(self):
        return sorted(glob.glob(os.path.join(ROOT, "*.py")) + glob.glob(os.path.join(ROOT, "tools", "*.py")))

    def test_the_modules_are_gone(self):
        for name in ("promotion", "registry"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, name + ".py")), name)
            self.assertIsNone(importlib.util.find_spec(name), name)

    def test_nothing_imports_them(self):
        pat = re.compile(r"^\s*(import|from)\s+(promotion|registry)\b", re.M)
        for path in self.app_files() + sorted(glob.glob(os.path.join(ROOT, "tests", "*.py"))):
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            self.assertIsNone(pat.search(src), path)
            if os.path.dirname(path) != os.path.join(ROOT, "tests"):
                self.assertNotRegex(src, r"\b(promotion|registry)\.[a-zA-Z_]", path)

    def test_no_promotion_code_in_the_app(self):
        src = _src()
        for word in ("CandidatesWindow", "run_gates", "gate_reports", "_gate_nights", "gate_stars", "show_candidates",
                     "_gates_after_save", "_gate_settings", "reach_back", "campaign_registry", "write_sched_export",
                     '"Candidates…"', '"Reach back…"', "_watch_revision", 'meta["candidates"] =',
                     'meta["gate_nights"] ='):
            self.assertNotIn(word, src, word)

    def test_no_series_measuring_left_in_the_neighbors_modules(self):
        src = _src("neighbors.py") + _src("neighbors_ui.py")
        for word in ("sidecar", "_neighbors.json", "measure_series", "FrameMap", "fit_affine", "fill_watch",
                     "blend_verdict", "run_blend", "find_lookalikes", "Measure look-alikes", "gate_report"):
            self.assertNotIn(word, src, word)
        for name in ("Curve", "make_curve", "remove_medians", "sine_fit", "Periodogram", "lookup_power",
                     "null_test_row", "neighbor_check", "sep_arcsec"):
            self.assertTrue(hasattr(nb, name), name)

    def test_the_suspect_test_is_untouched(self):
        self.assertEqual((core.SUSPECT_FACTOR, core.SUSPECT_EXCESS_CHI2), (3.0, 2.5))
        self.assertEqual(core.suspect_reason(3.5, 1.0), "scatter")
        self.assertTrue(core.suspect_reason(1.0, 3.0))
        self.assertEqual(core.suspect_reason(1.0, 1.0), "")


# ---- in the App ----------------------------------------------------------------------------------------------------
def scan_result(n_fr=120, seed=7):
    """A small field scan: S033 varies at 0.1027 d; S040 sits inside its sky ring; S051 is a look-alike."""
    rng = np.random.default_rng(seed)
    labels = ["S033", "S040", "S051", "Comp 1", "Check", "S077"]
    n = len(labels)
    jd = 2461358.6 + np.arange(n_fr) * 0.0021
    lc = 13 + rng.normal(0, 0.003, (n_fr, n))
    lc[:, 0] += 0.012 * np.sin(2 * np.pi * jd / 0.1027)
    ls = core.lomb_scargle(jd, lc[:, 0], 0.02, 2.0)
    return {
        "labels": labels, "jd": jd, "light_curves": lc, "mag": np.array([13.43, 15.9, 13.6, 12.6, 12.7, 13.2]),
        "xy": [(200, 200), (205, 210), (260, 200), (120, 150), (330, 330), (60, 340)],
        "radec": [(42.05 + 0.01 * k, 50.88 + 0.005 * k) for k in range(n)],
        "names": ["", "", "APASS J024811.4+505612", "", "", ""],
        "scatter": np.full(n, 0.01), "expected": np.full(n, 0.004),
        "excess": np.array([3.5, 1.0, 1.0, 1.1, 0.9, 1.0]), "excess_chi2": np.array([6.0, 1.0, 1.0, 1.0, 1.0, 1.0]),
        "usable": np.ones(n, bool), "n_good": np.full(n, n_fr), "saturated": [], "candidates": [0],
        "suspect_by": {0: "both"}, "gaia": [""] * n, "near": [], "watched": [], "near_sat": [],
        "periods": {0: {"period": 0.1027, "power": ls["power"], "ls": ls},
                    5: {"period": 2.0, "power": 0.2, "ls": dict(ls, edge="max")}},
        "period_range": (0.02, 2.0), "frames": n_fr, "excess_limit": 3.0, "chi2_limit": 2.5, "vsx": None, "bv": None,
        "vr": None, "calibrated": False,
    }


def all_texts(widget) -> list:
    out = []

    def walk(w):
        for c in w.winfo_children():
            try:
                t = str(c.cget("text"))
                if t:
                    out.append(t)
            except Exception:
                pass
            walk(c)
    walk(widget)
    return out


class TestFieldScanChecks(m4.AppCase):
    """Check neighbors… and Outside data… sit in the Field scan page's Checks group and act on the selected star."""

    def setUp(self):
        super().setUp()
        import tkinter as tk
        self.boxes = []
        self.box_patches = [mock.patch.object(self.ca.messagebox, nm, (lambda *a, _n=nm, **k: self.boxes.append(
            (_n, a, k)))) for nm in ("showinfo", "showerror")]
        for p in self.box_patches:
            p.start()
        self.r = scan_result()
        self.top = tk.Toplevel(self.app)
        self.view = self.ca.ScanView(self.top, self.app, self.r)   # as on the Discovery Output page
        self.app.update()

    def tearDown(self):
        for p in self.box_patches:
            p.stop()
        self.top.destroy()
        super().tearDown()

    def wait_for_box(self, seconds=10.0):
        end = time.monotonic() + seconds
        while not self.boxes and time.monotonic() < end:
            self.app.update()
            time.sleep(0.01)
        self.app.update()

    def test_the_groups(self):
        v = self.view
        self.assertEqual(list(v.groups), ["marks", "suspects", "checks"])
        self.assertEqual({k: str(lab.cget("text")) for k, lab in v.group_labels.items()},
                         {"marks": "Marks", "suspects": "Suspects", "checks": "Checks"})
        v.clear_selection()
        self.app.update()
        self.assertEqual(v.scan_group_texts("marks"), ["Mark suspects on image", "Mark on image",
                                                       "Hide known variables"])
        self.assertEqual(v.scan_group_texts("suspects"), ["Watch selected star", "Add as suspect",
                                                          "Remove added suspect", "Known variables measured…"])
        self.assertEqual(v.scan_group_texts("checks"), ["Check neighbors…", "Outside data…"])
        texts = all_texts(self.top)
        self.assertIn("Results", texts)
        self.assertEqual(texts.count("Check neighbors…"), 1)
        self.assertEqual(texts.count("Outside data…"), 1)
        for lab in v.group_labels.values():
            self.assertEqual(str(lab.cget("style")), "Hint.TLabel")
        self.assertEqual(int(v.group_labels["marks"].cget("width")), self.ca.GROUP_LABEL_WIDTH)
        # existing ttk styles only: plain buttons (they darken when pressed and grey when disabled)
        for b in (v.neighbors_button, v.outside_button):
            self.assertIn(str(b.cget("style")), ("", "TButton"))
            self.assertFalse(b.instate(["disabled"]))

    def test_the_rows_are_few(self):
        """Marks has a row; Suspects and Checks share one (with Results: three short rows under the plot)."""
        v = self.view
        self.assertIs(v.groups["suspects"].box, v.groups["checks"].box)
        self.assertIsNot(v.groups["marks"], v.groups["suspects"].box)

    def test_check_neighbors_acts_on_the_selected_star(self):
        self.app.sky_out.set(20.0)
        made = []
        real = self.app.check_neighbors_suspect

        def spy(result, i, parent=None):
            win = real(result, i, parent=parent)
            made.append((i, win))
            return win
        with mock.patch.object(self.app, "check_neighbors_suspect", spy):
            self.view.selected = 0
            self.view.neighbors_button.invoke()
            self.app.update()
        self.assertEqual(len(made), 1)
        i, win = made[0]
        try:
            self.assertEqual((i, win.name), (0, "S033"))
            names = [r["name"] for r in win.result["rows"]]
            self.assertEqual(names[0], "S040")    # the close companion first
            self.assertIn("S051", names)          # a look-alike
            self.assertIn("Comp 1", names)        # comps and check
            self.assertAlmostEqual(win.result["period"], 0.1027)   # the scan's own period
            self.assertTrue(all(r["quiet"] for r in win.result["rows"]))
            self.assertIn("Neighbors quiet at P", win.bottom_line.get())
            self.assertTrue(win.sub.get().startswith("Suspect · S033"))
            rows = {r["name"]: r for r in win.result["rows"]}
            self.assertEqual(rows["S051"]["display"], "APASS J024811.4+505612 (S051)")
            self.assertAlmostEqual(rows["S051"]["dmag"], 0.17, places=6)
            self.assertGreater(rows["S051"]["sep"], 30.0)   # arcsec, from the scan's sky positions
            texts = all_texts(win.win)
            for gone in ("Measure look-alikes", "Run blend test", "Show comps, check and other stars"):
                self.assertNotIn(gone, texts)
            self.assertNotIn("gate", win.note.get())
            self.assertNotIn("series", win.note.get())
            with mock.patch.object(self.app, "_save_suspects", lambda r: ""):
                win.save()
            self.assertEqual(self.r["neighbor_checks"][0]["n_quiet"], win.result["n_quiet"])
        finally:
            win.close()

    def test_check_neighbors_needs_a_selected_star(self):
        self.view.selected = None
        with mock.patch.object(self.app, "check_neighbors_suspect") as spy:
            self.view.neighbors_button.invoke()
        spy.assert_not_called()
        self.assertIn("Select a star first", self.app.status.get())

    def test_outside_data_acts_on_the_selected_star(self):
        calls = []
        answer = s033_result()   # the network is mocked: the surveys' answers are made here

        def fake_check(ra, dec, period, **kw):
            calls.append((ra, dec, period, kw))
            return answer
        self.view.selected = 0
        with mock.patch("neighbors_ui.od.check", fake_check):
            self.view.outside_button.invoke()
            self.wait_for_box()
        self.assertEqual(len(calls), 1)
        ra, dec, period, kw = calls[0]
        self.assertEqual((ra, dec, period), (42.05, 50.88, 0.1027))   # the star's scan position and scan period
        self.assertEqual(kw, {"min_period": 0.02, "max_period": 2.0})   # the scan's own period range
        kind, args, _k = self.boxes[-1]
        self.assertEqual(kind, "showinfo")
        self.assertIn("Outside data for S033", args[1])
        self.assertIn("Outside data agrees: ZTF and TESS.", args[1])
        self.assertIn("ATLAS classes it dubious", args[1])
        self.assertTrue(self.said("Outside data for S033: Outside data agrees: ZTF and TESS."))
        self.assertTrue(self.said("  ZTF: ZTF "))
        self.assertFalse(self.said("Registry"))
        self.assertEqual(self.app.watch_list, [])            # nothing is stored anywhere
        self.assertNotIn("outside", self.r)
        self.assertFalse(self.app._outside_running)

    def test_outside_data_failure_is_said(self):
        def boom(*a, **k):
            raise RuntimeError("no network")
        self.view.selected = 0
        with mock.patch("neighbors_ui.od.check", boom):
            self.view.outside_data()
            self.wait_for_box()
        kind, args, _k = self.boxes[-1]
        self.assertEqual(kind, "showerror")
        self.assertIn("no network", args[1])
        self.assertTrue(self.said("✖ Outside data for S033"))

    def test_outside_data_says_when_there_is_no_period(self):
        with mock.patch("neighbors_ui.od.check") as chk:
            for i in (1, 5):   # no period entry; a best peak at the edge of the search (a drift)
                self.boxes.clear()
                self.view.selected = i
                self.view.outside_data()
                self.assertEqual(self.boxes[-1][0], "showinfo")
                self.assertIn("found no period", self.boxes[-1][1][1])
            chk.assert_not_called()

    def test_outside_data_says_when_there_is_no_position(self):
        self.r["radec"][0] = (float("nan"), float("nan"))
        self.view.selected = 0
        with mock.patch("neighbors_ui.od.check") as chk:
            self.view.outside_data()
            chk.assert_not_called()
        self.assertIn("no sky position", self.boxes[-1][1][1])

    def test_an_unsolved_scan_greys_outside_data(self):
        import tkinter as tk
        r = scan_result()
        r["radec"] = None
        top = tk.Toplevel(self.app)
        try:
            view = self.ca.ScanView(top, self.app, r, on_close=top.destroy)
            self.assertTrue(view.outside_button.instate(["disabled"]))
            self.assertFalse(view.neighbors_button.instate(["disabled"]))
            self.assertIn("Close", all_texts(top))   # the pop-up's Close sits on the Results row
        finally:
            top.destroy()


class TestWindowsWithoutPromotion(m4.AppCase):
    def test_watch_stars_window(self):
        app = self.app
        app.watch_list = [{"name": "W", "ra": 1.0, "dec": 2.0}]
        win = self.ca.WatchWindow(app)
        try:
            texts = all_texts(win.win)
        finally:
            win.win.destroy()
            app.watch_list = []
        self.assertIn("Export watch light curves CSV…", texts)
        self.assertIn("Stop watching selected", texts)
        for gone in ("Reach back…", "Check neighbors…", "Outside data…"):
            self.assertNotIn(gone, texts)
        for name in ("reach_back", "check_neighbors"):
            self.assertFalse(hasattr(self.ca.WatchWindow, name), name)

    def test_variables_output_page_has_no_candidates_button(self):
        app = self.app
        app.set_mode("variables")
        app.update()
        texts = all_texts(app)
        self.assertIn("Watch stars…", texts)
        self.assertNotIn("Candidates…", texts)
        self.assertNotIn("Check neighbors…", texts)
        self.assertNotIn("Outside data…", texts)
        for name in ("show_candidates", "run_gates", "reach_back", "check_neighbors", "gate_stars"):
            self.assertFalse(hasattr(app, name), name)
        self.assertFalse(hasattr(self.ca, "CandidatesWindow"))


class TestOldSeriesWithGateReports(m4.AppCase):
    """A series saved by step 7b/7c carries the last gate reports; they are ignored on Load and gone after Save."""

    def test_loads_and_saves_without_them(self):
        A, cat = m4.A, {m4.A: 8.728}
        gate = {"key": "span", "label": "Nights and cycles", "set": "candidate", "pass": True, "cleared_by": "ours"}
        extra = {"app_version": "2.4.0", "gate_nights": ["JD2460950", "JD2460951"],
                 "candidates": {"W1": {"star": "W1", "version": 3, "candidate": True, "campaign": False,
                                       "period_days": 0.10297, "gates": [gate], "registry": "Candidate",
                                       "registry_key": "gaia:1", "outside": {"agree": ["ZTF"]}},
                                "broken": "not a report"},
                 "watch_list": [{"name": "W1", "ra": 348.31, "dec": 57.18, "mag": 11.2,
                                 "outside": {"agree": ["ZTF"]}, "reach_back": [{"frames": 3}]}]}
        pts = m4._points([A], cat)
        path = self.write("HD_219134.cvseries", m4._old_meta([A], cat, extra=extra), pts)
        want = m4._numbers(core.load_series(path)[1])
        self.load(path)
        app = self.app
        self.assertEqual(m4._numbers(app.observations), want)
        self.assertEqual([w["name"] for w in app.watch_list], ["W1"])
        self.assertFalse(hasattr(app, "gate_reports"))
        self.assertFalse([ln for ln in self.lines if "gate" in ln.lower() or "Candidate" in ln or "Registry" in ln],
                         self.lines)
        self.assertFalse(app.series_unsaved())
        app._write_series(path)
        app.update()
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)["meta"]
        self.assertNotIn("candidates", saved)
        self.assertNotIn("gate_nights", saved)
        self.assertEqual([w["name"] for w in saved["watch_list"]], ["W1"])
        self.assertEqual(m4._numbers(core.load_series(path)[1]), want)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "campaign_registry.json")))


if __name__ == "__main__":
    unittest.main()
