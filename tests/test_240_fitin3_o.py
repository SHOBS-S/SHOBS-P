"""2.4.0 fit-in F3 (#117, builder O): single-point outliers in the transit fit (John's decision 9: flag + offer).

core   transit_core.find_point_outliers: robust (MAD x 1.4826) scatter, beyond 5 sigma, single points only, strictly isolated (a run of
       three or more is real structure), put-back flagged frames never named.
night  Joanna's WASP-39 b case on a synthetic night: one +8.9 % point at mid-transit among 51. It is named; the
       reduced chi-square falls without it.
page   the Transit fit page: nothing changes before the button (51 points fitted, files as before); a plain note, the
       ring, the bins without the point, "Leave it out and refit"; after it 50 points, the note, NOTES, the settings
       file, the fit CSV ("trimmed outlier": Multi-night skips it), the AAVSO report on the fit's own points; "Put it
       back"; the left-out point survives a trim reset and goes back with another source; greyed during a job."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import multinight  # noqa: E402
import transit_core as tc  # noqa: E402
import transit_outlier_ui as tou  # noqa: E402
import transit_ui as tui  # noqa: E402

try:
    import tkinter  # noqa: F401
    tkinter.Tk().destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False

PLANET = {"name": "WASP-39 b", "host": "WASP-39", "period": 4.0552941, "t0": 2459669.9460, "t0_err": 0.0003,
          "period_err": 1e-6, "k": 0.1457, "a_rs": 11.4, "b": 0.45, "t14": 0.1168, "teff": 5400.0, "depth": 0.0212}


def joanna_night(seed=4, n=51, noise=0.0088, bump=0.089):
    """51 points across a WASP-39 b transit, 0.88 % scatter, and one frame +8.9 % high 6 minutes after mid-transit."""
    rng = np.random.default_rng(seed)
    t = PLANET["t0"] + np.linspace(-0.105, 0.105, n)
    model = tc.occultation(tc.separation(t, PLANET["t0"], PLANET["period"], PLANET["a_rs"], PLANET["b"]),
                           PLANET["k"], 0.45, 0.25)
    flux = model + rng.normal(0, noise, n)
    k = int(np.argmin(np.abs(t - (PLANET["t0"] + 0.1 / 24))))
    flux[k] = model[k] + bump
    am = 1.2 + 0.3 * ((t - t.mean()) / 0.1) ** 2
    return t, flux, np.full(n, noise * 0.75), am, k


class TestFindPointOutliers(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(11)
        self.t = 2461000.5 + np.arange(60) * 0.003
        self.r = rng.normal(0, 0.005, 60)

    def test_limits(self):
        self.assertEqual(tc.OUTLIER_NSIGMA, 5.0)
        self.assertEqual(tc.OUTLIER_MAX_RUN, 1)   # John 26 Nov: strictly isolated
        self.assertAlmostEqual(tc.robust_sigma(np.random.default_rng(1).normal(0, 2.0, 20000)), 2.0, delta=0.06)

    def test_one_point(self):
        r = self.r.copy()
        r[30] = 0.06
        got = tc.find_point_outliers(self.t, r)
        self.assertEqual(got["index"], [30])
        self.assertGreater(got["nsigma"][0], 5)
        self.assertAlmostEqual(got["sigma"], tc.robust_sigma(r))
        self.assertLess(got["sigma"], 0.007, "the bad point does not widen its own yardstick")
        r[30] = -0.06
        self.assertLess(tc.find_point_outliers(self.t, r)["nsigma"][0], -5)

    def test_nothing_under_the_limit_and_nothing_changed(self):
        r = self.r.copy()
        r[30] = 4.0 * tc.robust_sigma(r)
        keep = r.copy()
        self.assertEqual(tc.find_point_outliers(self.t, r)["index"], [])
        np.testing.assert_array_equal(r, keep)

    def test_runs_are_real_structure(self):
        r = self.r.copy()
        r[20] = 0.06                         # one alone: named
        self.assertEqual(tc.find_point_outliers(self.t, r)["index"], [20])
        r[20:22] = 0.06                      # two in a row (John 26 Nov): not single, none named
        self.assertEqual(tc.find_point_outliers(self.t, r)["index"], [])
        r[20:23] = 0.06                      # three in a row: a real feature, none named
        got = tc.find_point_outliers(self.t, r)
        self.assertEqual(got["index"], [])
        self.assertEqual(got["runs_ignored"], 1)
        r[40] = -0.07                        # a lone point elsewhere is still named
        self.assertEqual(tc.find_point_outliers(self.t, r)["index"], [40])

    def test_time_order_not_array_order(self):
        r = self.r.copy()
        r[[5, 6, 7]] = 0.06
        order = np.random.default_rng(3).permutation(60)
        self.assertEqual(tc.find_point_outliers(self.t[order], r[order])["index"], [])
        r2 = self.r.copy()
        r2[30] = 0.06
        got = tc.find_point_outliers(self.t[order], r2[order])
        self.assertEqual([int(order[k]) for k in got["index"]], [30])

    def test_skip_and_few_points(self):
        r = self.r.copy()
        r[30] = 0.06
        skip = np.zeros(60, dtype=bool)
        skip[30] = True
        self.assertEqual(tc.find_point_outliers(self.t, r, skip=skip)["index"], [])
        self.assertEqual(tc.find_point_outliers(self.t[25:40], r[25:40])["index"], [])
        self.assertEqual(tc.find_point_outliers(self.t, np.zeros(60))["index"], [], "no scatter: nothing to say")
        r[10] = np.nan
        self.assertEqual(tc.find_point_outliers(self.t, r)["index"], [30])


class TestJoannaNight(unittest.TestCase):
    def test_named_and_chi2_drops_without_it(self):
        t, f, e, am, k = joanna_night()
        r = tc.fit_transit(t, f, e, am, PLANET, detrend_airmass=True, mcmc_steps=300)
        self.assertEqual(r["n_points"], 51, "the fit itself drops nothing")
        got = tc.find_point_outliers(r["t"], r["residuals"] / r["baseline"])
        self.assertEqual(got["index"], [k])
        self.assertGreater(got["nsigma"][0], 5)
        keep = np.arange(51) != k
        r2 = tc.fit_transit(t[keep], f[keep], e[keep], am[keep], PLANET, detrend_airmass=True, mcmc_steps=300)
        self.assertLess(r2["rchi2"], 0.6 * r["rchi2"])
        self.assertLess(r2["summary"]["tc"][1], r["summary"]["tc"][1])
        self.assertEqual(tc.find_point_outliers(r2["t"], r2["residuals"] / r2["baseline"])["index"], [])


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestTransitPageOutliers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        cls.tmp = tempfile.mkdtemp()
        cls.patches = [mock.patch.object(ca, "SETTINGS_FILE", os.path.join(cls.tmp, "s.json")),
                       mock.patch.object(ca.messagebox, "showinfo", lambda *a, **k: None),
                       mock.patch.object(ca.messagebox, "showwarning", lambda *a, **k: None),
                       mock.patch.object(ca.messagebox, "showerror", lambda *a, **k: None),
                       mock.patch.object(ca.messagebox, "askyesno", lambda *a, **k: True),
                       mock.patch.dict(tui.MCMC_CHOICES, {k: 300 for k in tui.MCMC_CHOICES}),
                       mock.patch.object(tc, "published_rp_cached", lambda *a, **k: ((), (), False))]
        for p in cls.patches:
            p.start()
        ca.LOG_FILE = os.path.join(cls.tmp, "test_240_fitin3_o.log")
        cls.app = ca.App()
        cls.app.withdraw()
        cls.lines = []
        cls.app.log = lambda text, *a, **k: cls.lines.append(str(text))
        app, tp = cls.app, cls.app.transit_page
        cls.t, cls.f, cls.e, cls.am, cls.k = joanna_night()
        tp.report = {"t": cls.t, "flux": cls.f, "err": cls.e, "airmass": cls.am, "results": {},
                     "header": {"OBSCODE": "TST", "FILTER": "CV", "SOFTWARE": "EXOTIC 4.3"},
                     "planet": dict(PLANET), "path": os.path.join(cls.tmp, "wasp39.txt")}
        tp._trims, tp._left_out = [], []
        tp.set_detrend("airmass")
        tp.fit()
        assert cls.pump(lambda: not app.busy and tp.result is not None and not tp._fitting), "first fit"
        cls.r1 = tp.result
        cls.before = cls.snapshot(tp)
        tp.leave_out_and_refit()
        assert cls.pump(lambda: not app.busy and tp.result is not None and tp.result is not cls.r1
                        and not tp._fitting), "refit"
        cls.r2 = tp.result
        cls.after = cls.snapshot(tp)

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        for p in cls.patches:
            p.stop()

    @classmethod
    def pump(cls, cond, seconds=600):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            cls.app.update()
            if cond():
                return True
            time.sleep(0.02)
        return False

    @classmethod
    def snapshot(cls, tp):
        """What the page shows and would save for the fit on it."""
        r = tp.result
        meta = tp._file_meta()
        path = os.path.join(cls.tmp, f"fit_{r['n_points']}.csv")
        tui.write_fit_csv(path, r, meta=meta)
        with open(path, encoding="utf-8") as fh:
            csv_text = fh.read()
        box = tp.groups.boxes["points"]
        return {"notes": meta.get("notes", ""), "csv": csv_text, "csv_path": path,
                "aavso": tc.aavso_exoplanet_report(r, meta),
                "buttons": [str(w.cget("text")) for w in box.shown_widgets() if w.winfo_class() == "TButton"],
                "legend": list(tp._legend_items[1]) if tp._legend_items else [],
                "source": tp.source_text.get(), "settings": tp.left_out_settings_line(meta, r),
                "robust_n": len(tp._robust_src[1]["t"]) if getattr(tp, "_robust_src", None) else None}

    def test_nothing_changes_before_the_button(self):
        r, b = self.r1, self.before
        self.assertEqual(r["n_points"], 51)
        np.testing.assert_array_equal(r["t"], self.t)
        self.assertIsNone(r.get("left_out"))
        self.assertNotIn("left out", b["notes"])
        self.assertNotIn("outlier", b["csv"])
        self.assertEqual(sum(1 for ln in b["csv"].splitlines() if ln[:1].isdigit()), 51)
        self.assertTrue(b["settings"].endswith(": none"))
        self.assertIn(f"{self.t[self.k]:.5f}"[:-1], b["aavso"], "the reports still carry the point")
        self.assertEqual(b["robust_n"], 51)

    def test_flagged_as_a_plain_note_naming_the_frame(self):
        r = self.r1
        found = r["outliers"]
        self.assertEqual(list(found["index"]), [self.k])
        self.assertAlmostEqual(float(found["t"][0]), self.t[self.k], places=7)
        self.assertGreater(abs(found["nsigma"][0]), 5)
        notes = [(lvl, x) for lvl, x in r["flags"] if x.startswith("One point stands far from the fit")]
        self.assertEqual(len(notes), 1)
        lvl, text = notes[0]
        self.assertEqual(lvl, "info", "a plain note, never a caution")
        self.assertNotIn("⚠", text)
        self.assertIn(f"point {self.k + 1} of 51", text)
        self.assertIn(f"BJD_TDB {self.t[self.k]:.5f}", text)
        self.assertIn("local time", text)
        self.assertIn("above the fit", text)
        self.assertIn("Leave it out and refit takes it out; nothing is left out unless you press it.", text)
        self.assertGreater(found["rms_pct"], found["rms_without_pct"])
        self.assertIn(tou.LEAVE_ONE, self.before["buttons"])
        self.assertNotIn(tou.PUT_ONE, self.before["buttons"])
        self.assertTrue(any(x.startswith("Transit fit: One point stands far") for x in self.lines))

    def test_plot_marks_it_and_bins_leave_it_out(self):
        tp = self.app.transit_page
        y = np.asarray(self.r1["detrended"], dtype=float)
        yb = tp._outlier_bin_values(self.r1, y)
        self.assertTrue(np.isnan(yb[self.k]))
        self.assertEqual(int(np.isnan(yb).sum()), 1)
        self.assertFalse(np.isnan(y).any(), "the fit's own values are not touched")
        self.assertTrue(any("single-point outlier: in the fit, not in the bins" in x for x in self.before["legend"]))
        x = (self.r1["t"] - self.r1["best"]["tc"]) * 24
        width = 1.0
        with_it = {round(b[0], 6): b[1] for b in tui._bin(x, y, width)}
        without = {round(b[0], 6): b[1] for b in tui._bin(x, yb, width)}
        self.assertEqual(len(with_it), len(without), "the same bins, one of them without the bad frame")
        self.assertEqual(sum(1 for (_a, va), (_b, vb) in zip(sorted(with_it.items()), sorted(without.items()))
                             if abs(va - vb) > 1e-9), 1)
        np.testing.assert_array_equal(tp._outlier_bin_values(self.r2, y[:50]), y[:50])

    def test_leave_it_out_refits_without_it(self):
        r1, r2, a = self.r1, self.r2, self.after
        self.assertEqual(r2["n_points"], 50)
        self.assertFalse(np.any(np.abs(r2["t"] - self.t[self.k]) < 1e-7))
        self.assertLess(r2["rchi2"], 0.6 * r1["rchi2"], "the reduced chi-square drops")
        self.assertLess(r2["summary"]["tc"][1], r1["summary"]["tc"][1])
        self.assertIsNone(r2["outliers"])
        left = r2["left_out"]
        self.assertEqual(left["n"], 1)
        self.assertAlmostEqual(float(left["t"][0]), self.t[self.k], places=7)
        notes = [(lvl, x) for lvl, x in r2["flags"] if x.startswith("Left out of the fit as a single-point outlier")]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0][0], "info")
        self.assertIn(f"point {self.k + 1} of 51", notes[0][1])
        self.assertIn("Put it back refits with it.", notes[0][1])
        self.assertIn(tou.PUT_ONE, a["buttons"])
        self.assertNotIn(tou.LEAVE_ONE, a["buttons"])
        self.assertTrue(any("left out as a single-point outlier (not fitted)" in x for x in a["legend"]))
        self.assertEqual(a["robust_n"], 50, "the Robustness check refits the fit's own points")

    def test_recorded_in_notes_settings_csv_and_reports(self):
        a = self.after
        self.assertIn("SHOBS-P: left out of the fit as single-point outlier (the user's choice): "
                      f"point {self.k + 1} of 51 BJD_TDB {self.t[self.k]:.5f}", a["notes"])
        self.assertTrue(a["notes"].isascii())
        self.assertIn(f"point {self.k + 1} of 51 BJD_TDB {self.t[self.k]:.5f}", a["settings"])
        rows = [ln for ln in a["csv"].splitlines() if "trimmed outlier" in ln]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].startswith(f"{self.t[self.k]:.7f},"))
        self.assertEqual(rows[0].split(",")[2:5], ["", "", ""], "no error, model or residual: it was not fitted")
        night = multinight.load_fit_csv(a["csv_path"])
        self.assertEqual(len(night["t"]), 50, "Multi-night reads the same points as the fit")
        # the AAVSO report (and everything else written from the result) carries the fit's 50 points
        self.assertNotIn(f"{self.t[self.k]:.5f}"[:-1], a["aavso"].split("#NOTES")[-1].split("\n", 1)[-1])
        self.assertIn("left out of the fit as single-point outlier", a["aavso"])
        tp = self.app.transit_page
        path = tp._write_settings(os.path.join(self.tmp, "rep.txt"), tp._file_meta())
        with open(path, encoding="utf-8") as fh:
            self.assertIn("Single points left out of the fit as outliers (the user's choice): point", fh.read())

    def test_put_it_back_and_the_one_rule(self):
        tp, app = self.app.transit_page, self.app
        keep = list(tp._left_out)
        try:
            self.assertEqual(len(keep), 1)
            tp._reset_trim()                                   # Clear trim's reset: the point stays out
            self.assertEqual(len(tp._left_out), 1)
            app.busy = True                                    # busy: greyed, and a press does nothing
            app.gates.refresh()
            self.assertIn("disabled", tp.put_back_button.state())
            self.assertIn("disabled", tp.leave_out_button.state())
            with mock.patch.object(tp, "fit") as fit:
                tp.put_back_and_refit()
                fit.assert_not_called()
            self.assertEqual(len(tp._left_out), 1)
            app.busy = False
            app.gates.refresh()
            self.assertNotIn("disabled", tp.put_back_button.state())
            with mock.patch.object(tp, "fit") as fit:
                tp.put_back_and_refit()
                fit.assert_called_once()
            self.assertEqual(tp._left_out, [])
            self.assertNotIn(tp.put_back_button, tp.groups.boxes["points"].shown_widgets())
            tp._left_out = list(keep)                          # another source puts it back, with a log line
            n = len(self.lines)
            tp._reset_left_out("another night was chosen")
            self.assertEqual(tp._left_out, [])
            self.assertTrue(any("another night was chosen" in x and "back in" in x for x in self.lines[n:]))
            # a fit of all 51 points again: the same arrays as given
            t, f, e, am = tp._apply_left_out(self.t, self.f, self.e, self.am, {})
            self.assertEqual(len(t), 51)
        finally:
            app.busy = False
            tp._left_out = keep
            tp._show_outlier_buttons()

    def test_john_calls_26_nov(self):
        """John 26 Nov (13:03): (2) left-out points live in the report's _settings.txt only, never the series file;
        (3) no cap on repeated leave-outs; (4) re-running photometry keeps a left-out point (matched by its time)
        unless the series counter changes."""
        tp, app = self.app.transit_page, self.app
        keep, rep, gen0 = list(tp._left_out), tp.report, getattr(app, "_series_gen", 0)
        try:
            src = open(os.path.join(os.path.dirname(tc.__file__), "clearv_app.py"), encoding="utf-8").read()
            self.assertNotIn("_left_out", src, "(2) the series file never records a left-out point")
            tp._left_out = [{"t": float(self.t[k]), "label": f"point {k + 1}", "pct": 1.0, "nsigma": 6.0}
                            for k in range(12)]
            self.assertEqual(int(tp._left_out_mask(self.t).sum()), 12, "(3) no cap")
            tp.report = None                                   # a night's own photometry, not a report
            tp._left_out_gen = gen0
            tp._left_out_source_check()                        # (4) photometry run again: same series counter
            self.assertEqual(len(tp._left_out), 12)
            app._series_gen = gen0 + 1                         # a loaded / cleared series: put back
            tp._left_out_source_check()
            self.assertEqual(tp._left_out, [])
        finally:
            app._series_gen = gen0
            tp.report, tp._left_out = rep, keep
            tp._show_outlier_buttons()

    def test_too_few_points_left(self):
        tp = self.app.transit_page
        keep = list(tp._left_out)
        try:
            tp._left_out = [{"t": float(tv)} for tv in self.t[:35]]
            with self.assertRaises(ValueError) as ctx:
                tp._apply_left_out(self.t, self.f, self.e, self.am, {})
            self.assertIn("a transit fit needs at least 20", str(ctx.exception))
        finally:
            tp._left_out = keep


if __name__ == "__main__":
    unittest.main()
