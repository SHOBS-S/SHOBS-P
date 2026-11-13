"""2.3.4 items (numbers frozen at 2.3.0): #73 fit-CSV error columns, #74 markers after a Blink reject, #75 Comp health
zero points, auto-placed series comps, the Stars panel and the suggested-comp rules."""
import csv
import math
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ["SHOBS_ONSKY_OFF"] = "1"

import numpy as np  # noqa: E402
import matplotlib  # noqa: E402
matplotlib.use("Agg")

import transit_ui as tui  # noqa: E402
import multinight as mn  # noqa: E402
from test_2220_multinight import single_fit  # noqa: E402


class TestFitCsvErrors(unittest.TestCase):
    """#73: the fit CSV says rel_flux_err is the scaled error and carries the raw one."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self):
        r = single_fit(100, 1)
        p = os.path.join(self.dir, "a_transit_fit.csv")
        tui.write_fit_csv(p, r, False, {"filter": "CV"})
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        return r, p, text

    def test_header_line_and_raw_column(self):
        r, p, text = self._write()
        line = [ln for ln in text.splitlines() if ln.startswith("# Errors:")]
        self.assertEqual(len(line), 1)
        self.assertIn(f"{r['error_scale']:.3f}", line[0])
        self.assertIn(f"raw reduced chi-square {r['rchi2']:.2f}", line[0])
        self.assertIn(f"beta {r['beta']:.2f}", line[0])
        rows = [row for row in csv.reader(text.splitlines()) if row and not row[0].startswith("#")][1:]
        head = [row for row in csv.reader(text.splitlines()) if row and row[0] == "BJD_TDB"][0]
        # the old columns keep their places; the raw error is the new last one
        self.assertEqual(head[:8], ["BJD_TDB", "rel_flux", "rel_flux_err", "model", "residual", "flag", "airmass",
                                    "segment"])
        self.assertEqual(head[-1], "rel_flux_err_raw")
        i, j = head.index("rel_flux_err"), head.index("rel_flux_err_raw")
        for row in rows:
            if row[i] and row[j]:
                self.assertAlmostEqual(float(row[i]) / float(row[j]), r["error_scale"], delta=2e-3 * r["error_scale"])

    def test_raw_chi2_rebuilt_from_file(self):
        r, p, text = self._write()
        rows = [row for row in csv.reader(text.splitlines()) if row and not row[0].startswith("#")][1:]
        res = np.array([float(row[4]) for row in rows if row[4]])
        raw = np.array([float(row[-1]) for row in rows if row[4]])
        chi = float(np.sum((res / raw) ** 2)) / max(len(res) - len(r["free_params"]), 1)
        self.assertAlmostEqual(chi, r["rchi2"], delta=0.02 * r["rchi2"])

    def test_multinight_still_reads_it(self):
        r, p, text = self._write()
        n = mn.load_fit_csv(p)
        self.assertGreater(len(n["t"]) if isinstance(n, dict) else len(n.t), 10)

    def test_no_line_without_numbers(self):
        self.assertEqual(tui.fit_csv_error_line({}), "")
        self.assertEqual(tui.fit_csv_error_line({"error_scale": float("nan"), "rchi2": 1, "beta": 1}), "")


try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


def _app():
    import clearv_app as ca
    ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test234.log")
    app = ca.App()
    return ca, app


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestStarsPanelWidth(unittest.TestCase):
    """2.3.4: the Stars panel's ✕ buttons and "V magnitude" header stay inside the panel when its text grows."""

    def _check(self, screen=None):
        ca, app = _app()
        try:
            if screen:
                app.winfo_screenwidth = lambda: screen
            app.set_mode("variables")
            app.update()
            app.suggest_text.set("S1 TYC 3308-50-1  V 11.26  B-V 0.83  AAVSO sequence star, steady over three nights " * 3)
            for role in (ca.COMP_ROLES[0], "check"):
                app.star_status[role].set("Peak 56,000 ADU on the reference frame; near the linear limit " * 3)
                # a bigger font: the status line asks for more width than the panel was first given
                app.star_status_labels[role].configure(wraplength=680)
            app.update()
            canvas_w = app.panel_scroll.canvas.winfo_width()
            for role in (ca.COMP_ROLES[0], "check"):
                x_btn = [w for w in app.star_row_widgets[role] if w.winfo_class() == "TButton"][0]
                self.assertLessEqual(x_btn.winfo_x() + x_btn.winfo_width(), canvas_w)
        finally:
            app.destroy()

    def test_panel_refits_when_text_grows(self):
        self._check()

    def test_capped_panel_keeps_the_clear_buttons(self):
        self._check(screen=1200)


class TestCompHealthZeroPoint(unittest.TestCase):
    """2.3.4 (#75): a star missing from part of a night no longer moves the other stars' night level."""

    def _obs(self, night, jd, insts, spare=None):
        import photometry_core as core
        return core.Obs(jd=jd, mag=10.0, merr=0.01, cmag=0.0, kmag=None, airmass=1.2, path="f.fits", exptime=30,
                        flux_var=1.0, flux_comp=1.0, night=night, comp_insts=list(insts),
                        spare_insts=dict(spare or {}))

    def test_partly_missing_star_does_not_fake_a_night_offset(self):
        import numpy as np
        import photometry_core as core
        rng = np.random.default_rng(1)
        obs = []
        for k, night in enumerate(("2026-11-08", "2026-11-09", "2026-11-10")):
            for i in range(60):
                cloud = 0.3 * rng.random()   # common to every star in the frame
                insts = [-9.0 + cloud + rng.normal(0, 0.003), -8.2 + cloud + rng.normal(0, 0.003),
                         -8.6 + cloud + rng.normal(0, 0.003)]
                spare = {}
                # a star 1.5 mag fainter than the rest, on every frame of the first two nights, half of the third
                if night != "2026-11-10" or i % 2 == 0:
                    spare["APASS J1"] = -7.1 + cloud + rng.normal(0, 0.003)
                obs.append(self._obs(night, 2460990.5 + k + i / 1000.0, insts, spare))
        res = core.comp_health(obs, ["A", "B", "C"], spare_names=["APASS J1"])
        for row in res["summary"]:
            if row["label"] in ("A", "B", "C"):
                self.assertLess(row["night_spread"], 0.005, row)
                self.assertEqual(row["verdict"], "steady", row)

    def test_drifting_star_still_flagged(self):
        import numpy as np
        import photometry_core as core
        rng = np.random.default_rng(2)
        obs = []
        for k, night in enumerate(("2026-11-08", "2026-11-09", "2026-11-10")):
            for i in range(40):
                cloud = 0.2 * rng.random()
                insts = [-9.0 + cloud + rng.normal(0, 0.003), -8.2 + cloud + 0.04 * k + rng.normal(0, 0.003),
                         -8.6 + cloud + rng.normal(0, 0.003), -8.8 + cloud + rng.normal(0, 0.003)]
                obs.append(self._obs(night, 2460990.5 + k + i / 1000.0, insts))
        res = core.comp_health(obs, ["A", "B", "C", "D"])
        verdict = {r["label"]: r["verdict"] for r in res["summary"]}
        self.assertTrue(verdict["B"].startswith("SUSPECT"), verdict)
        for n in ("A", "C", "D"):
            self.assertEqual(verdict[n], "steady", verdict)


def _field(shift=(0.0, 0.0), flipped=False, n=40, shape=(400, 500), seed=3):
    """A synthetic star field (Gaussian stars on a flat sky) and the star positions on it."""
    import numpy as np
    rng = np.random.default_rng(seed)
    h, w = shape
    pts = np.column_stack((rng.uniform(30, w - 30, n), rng.uniform(30, h - 30, n)))
    amp = rng.uniform(800, 20000, n)
    if flipped:
        pts = np.column_stack((w - 1 - pts[:, 0], h - 1 - pts[:, 1]))
    pts = pts + np.asarray(shift)
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.full(shape, 300.0)
    for (x, y), a in zip(pts, amp):
        img += a * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.6 ** 2))
    img += rng.normal(0, 5, shape)
    return img, pts


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestMarkersAfterSolve(unittest.TestCase):
    """2.3.4: series comps/check placed after the solve (names and magnitudes kept); #74 markers follow a new
    reference frame."""

    @classmethod
    def setUpClass(cls):
        cls.ca, cls.app = _app()
        cls.app.set_mode("variables")
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def _solved(self, img, pts):
        app, ca = self.app, self.ca
        app.preview = img
        app.preview_is_debayered = True
        app.radius.set(5); app.sky_in.set(9); app.sky_out.set(14)
        # a fake plate solution: 1 px = 1 arcsec, RA grows with x (enough for sky_to_pixel and the matching)
        app.chart_solution = {"target_xy": (250.0, 200.0), "scale": 1.0, "angle": 0.0, "ra_deg": 30.0,
                              "dec_deg": 0.0, "auto": True}
        app.chart_placed = []
        for i, (x, y) in enumerate(pts[:12]):
            ra, dec = ca.core.pixel_to_sky(float(x), float(y), app.chart_solution)
            app.chart_placed.append((float(x), float(y), {"label": f"TYC 1-{i}-1", "ra": ra, "dec": dec,
                                                          "mag": 10.0 + i / 10, "band": "V", "catalog": "Tycho-2"}))
        return app

    def test_series_slots_placed_and_keep_their_numbers(self):
        img, pts = _field()
        app = self._solved(img, pts)
        for r in list(self.ca.COMP_ROLES) + ["check"]:
            setattr(app, f"{r}_xy", None)
            getattr(app, f"{r}_name").set("")
            getattr(app, f"{r}_mag").set("")
        c1, chk = self.ca.COMP_ROLES[0], "check"
        app.comp_coords = {"TYC 1-3-1": app.chart_placed[3][2]["ra"] and
                           (app.chart_placed[3][2]["ra"], app.chart_placed[3][2]["dec"])}
        getattr(app, f"{c1}_name").set("TYC 1-3-1")
        getattr(app, f"{c1}_mag").set("8.728")      # the series' V, not the catalog's 10.300
        app.check_name.set("TYC 1-5-1")              # no saved position: found by name on the chart
        app.check_mag.set("9.786")
        placed = app._auto_place_named_slots()
        self.assertEqual(set(placed), {c1, chk})
        self.assertLess(math.hypot(app.__dict__[f"{c1}_xy"][0] - pts[3][0], app.__dict__[f"{c1}_xy"][1] - pts[3][1]), 1.0)
        self.assertLess(math.hypot(app.check_xy[0] - pts[5][0], app.check_xy[1] - pts[5][1]), 1.0)
        self.assertEqual(getattr(app, f"{c1}_mag").get(), "8.728")
        self.assertEqual(app.check_mag.get(), "9.786")
        self.assertEqual(getattr(app, f"{c1}_name").get(), "TYC 1-3-1")
        # a second call does nothing (markers are there)
        self.assertEqual(app._auto_place_named_slots(), [])

    def test_off_frame_series_comp_says_so(self):
        img, pts = _field()
        app = self._solved(img, pts)
        c2 = self.ca.COMP_ROLES[1]
        for r in list(self.ca.COMP_ROLES) + ["check"]:
            setattr(app, f"{r}_xy", None)
            getattr(app, f"{r}_name").set("")
        ra, dec = self.ca.core.pixel_to_sky(2000.0, 2000.0, app.chart_solution)
        app.comp_coords = {"FAR AWAY": (ra, dec)}
        getattr(app, f"{c2}_name").set("FAR AWAY")
        self.assertEqual(app._auto_place_named_slots(), [])
        self.assertIsNone(getattr(app, f"{c2}_xy"))
        self.assertIn("not on this frame", app._slot_notes[c2][1])

    def test_markers_follow_new_reference_frame(self):
        app, ca = self.app, self.ca
        img1, pts1 = _field()
        img2, pts2 = _field(shift=(6.0, -4.0))
        app.pattern.set(ca.core.MONO)
        app.preview, app.preview_is_debayered, app.preview_path = img1, True, "/data/lights/L_0001.fits"
        c1 = ca.COMP_ROLES[0]
        app.target_xy = tuple(pts1[0])
        setattr(app, f"{c1}_xy", tuple(pts1[1]))
        app.check_xy = tuple(pts1[2])
        app.watch_xy = {"V1441 Cas": tuple(pts1[4])}
        self.assertEqual(app._carry_marks_to_new_reference(img2, "/data/lights/L_0016.fits"), "moved")
        for xy, want in ((app.target_xy, pts2[0]), (getattr(app, f"{c1}_xy"), pts2[1]), (app.check_xy, pts2[2]),
                         (app.watch_xy["V1441 Cas"], pts2[4])):
            self.assertLess(math.hypot(xy[0] - want[0], xy[1] - want[1]), 1.0)
        # same frame: nothing moves
        before = app.check_xy
        app.preview_path = "/data/lights/L_0016.fits"
        self.assertEqual(app._carry_marks_to_new_reference(img2, "/data/lights/L_0016.fits"), "")
        self.assertEqual(app.check_xy, before)

    def test_markers_follow_across_a_meridian_flip(self):
        app, ca = self.app, self.ca
        img1, pts1 = _field()
        img2, pts2 = _field(flipped=True, shift=(3.0, 2.0))
        app.pattern.set(ca.core.MONO)
        app.preview, app.preview_is_debayered, app.preview_path = img1, True, "/data/lights/L_0001.fits"
        c1 = ca.COMP_ROLES[0]
        setattr(app, f"{c1}_xy", tuple(pts1[1]))
        app.check_xy = tuple(pts1[2])
        app.target_xy = None
        app.watch_xy = {}
        self.assertEqual(app._carry_marks_to_new_reference(img2, "/data/lights/L_0030.fits"), "moved")
        self.assertLess(math.hypot(app.check_xy[0] - pts2[2][0], app.check_xy[1] - pts2[2][1]), 1.0)

    def test_unalignable_frames_take_markers_off_but_keep_names(self):
        app, ca = self.app, self.ca
        img1, pts1 = _field()
        import numpy as np
        blank = np.full(img1.shape, 300.0)
        app.pattern.set(ca.core.MONO)
        app.preview, app.preview_is_debayered, app.preview_path = img1, True, "/data/lights/L_0001.fits"
        c1 = ca.COMP_ROLES[0]
        setattr(app, f"{c1}_xy", tuple(pts1[1]))
        getattr(app, f"{c1}_name").set("TYC 4006-1008-1")
        getattr(app, f"{c1}_mag").set("8.728")
        app.watch_xy = {}
        self.assertEqual(app._carry_marks_to_new_reference(blank, "/data/lights/L_0030.fits"), "cleared")
        self.assertIsNone(getattr(app, f"{c1}_xy"))
        self.assertEqual(getattr(app, f"{c1}_name").get(), "TYC 4006-1008-1")
        self.assertEqual(getattr(app, f"{c1}_mag").get(), "8.728")


class TestSuggestCore(unittest.TestCase):
    def test_color_weight(self):
        import photometry_core as core
        cands = [("bright-match", -9.3, 0, 0, 0.62), ("same-mag-off-color", -9.0, 0, 0, 0.85)]
        # weight 1: 0.3 + 0.02 = 0.32 vs 0 + 0.25 = 0.25 -> the off-color star first; weight 2: 0.34 vs 0.50
        self.assertEqual(core.suggest_comps_near(-9.0, cands, target_bv=0.60)[0][0], "same-mag-off-color")
        self.assertEqual(core.suggest_comps_near(-9.0, cands, target_bv=0.60, color_weight=2.0)[0][0], "bright-match")
        self.assertEqual(core.suggest_comps_near(-9.0, cands, target_bv=0.60, max_dbv=0.2), [("bright-match",
                                                                                              core.suggest_comps_near(
            -9.0, cands, target_bv=0.60, max_dbv=0.2)[0][1])])


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestSuggestedCompRules(unittest.TestCase):
    """2.3.4 (medium feature): the colour gate 0.3 (colour weighted above brightness), the linear-limit gate, the
    bigger edge margin, the measured target B-V, and S-lines that say why."""

    @classmethod
    def setUpClass(cls):
        cls.ca, cls.app = _app()
        cls.app.set_mode("variables")
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def _setup(self, specs, target_bv=0.60):
        """specs: list of (x, y, amplitude, B-V or None, name). Star 0 is the target."""
        import numpy as np
        app, ca = self.app, self.ca
        h, w = 400, 600
        yy, xx = np.mgrid[0:h, 0:w]
        img = np.full((h, w), 300.0)
        for x, y, a, _bv, _n in specs:
            img += a * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.6 ** 2))
        app.preview, app.preview_is_debayered = img, True
        app.preview_path = ""
        app.radius.set(5); app.sky_in.set(9); app.sky_out.set(14)
        app.sat_limit.set(60000)
        app.observations = []
        app.chart_placed = []
        for x, y, a, bv, name in specs:
            st = {"label": name, "mag": 12.0, "band": "V", "catalog": "Tycho-2", "ra": 30.0, "dec": 0.0}
            if bv is not None:
                st["mags"] = {"V": 12.0, "B": 12.0 + bv}
            app.chart_placed.append((float(x), float(y), st))
        tx, ty = specs[0][0], specs[0][1]
        app.target_xy = (float(tx), float(ty))
        app._star_snr = lambda *_a, **_k: 500.0
        return app

    def _names(self, app):
        app._suggest_comps()
        return [n for n, _dm, _x, _y in app.suggested_comps]

    def test_color_gate_and_weight(self):
        app = self._setup([(300, 200, 5000, 0.60, "T"),
                           (150, 150, 5000, 0.95, "OFF COLOR"),    # same brightness, 0.35 redder
                           (420, 260, 7000, 0.65, "MATCH A"),     # 0.4 mag brighter, colour matched
                           (200, 280, 3500, 0.55, "MATCH B")])
        self.assertTrue(self.ca.core.catalog_bv(app.chart_placed[1][2]) is not None)
        names = self._names(app)
        self.assertEqual(set(names[:2]), {"MATCH A", "MATCH B"})
        self.assertIn("OFF COLOR", names)   # filled in at the 0.6 gate (only three candidates)
        line = [l for l in app._suggested_lines() if "OFF COLOR" in l][0]
        self.assertIn("closest color here", line)

    def test_edge_margin_preferred(self):
        app = self._setup([(300, 200, 5000, 0.60, "T"),
                           (25, 200, 5000, 0.60, "EDGE"),         # 25 px in: < 3 x 14 = 42
                           (420, 260, 5000, 0.60, "INSIDE")])
        names = self._names(app)
        self.assertEqual(names[0], "INSIDE")
        self.assertIn("EDGE", names)
        line = [l for l in app._suggested_lines() if "EDGE" in l][0]
        self.assertIn("px from the edge", line)

    def test_linear_gate(self):
        app = self._setup([(300, 200, 5000, 0.60, "T"),
                           (150, 150, 5000, 0.60, "HOT"),
                           (420, 260, 5000, 0.60, "COOL")])
        app.preview_path = "/data/L_0001.fits"
        old = (app._linear_limit, app._raw_peak_at)
        try:
            app._linear_limit = lambda: 32000.0
            app._raw_peak_at = lambda xy: 26000.0 if abs(xy[0] - 150) < 2 else 15000.0
            names = self._names(app)
            self.assertEqual(names, ["COOL"])
            self.assertIn("47% of the linear limit", app._suggested_lines()[1])
        finally:
            app._linear_limit, app._raw_peak_at = old
            app.preview_path = ""

    def test_measured_target_bv_wins(self):
        import photometry_core as core
        app = self._setup([(300, 200, 5000, 1.20, "T"),           # catalog says red
                           (150, 150, 5000, 1.15, "RED"),
                           (420, 260, 5000, 0.60, "BLUE")])
        self.assertEqual(self._names(app)[0], "RED")
        app.observations = [core.Obs(jd=1.0 + k, mag=10.0, merr=0.01, cmag=0.0, kmag=None, airmass=1.1, path="",
                                     exptime=30, flux_var=1.0, flux_comp=1.0, bv=0.58 + 0.01 * k) for k in range(5)]
        self.assertEqual(self._names(app)[0], "BLUE")
        self.assertIn("measured", app._suggested_lines()[1])
        app.observations = []


if __name__ == "__main__":
    unittest.main()
