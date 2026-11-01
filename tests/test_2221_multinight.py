"""2.2.21 (#44, nit g): Multi-night from the open Transits series (Output/Transit fit page's Multi-night fit…), the
ephemeris used shown and kept in the saved files, the alternative ephemeris kept with the series, and the window's
divider opening below the nights table (not at the very top)."""
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
import transit_core as tc  # noqa: E402
import multinight as mn  # noqa: E402

try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False

PLANET = dict(name="TOI-2046 b", host="TOI-2046", period=1.4971842, t0=2460000.5, t0_err=0.0005, period_err=1e-6,
              k=0.12, a_rs=5.0, b=0.3, teff=6000, t14=0.08)
RA, DEC, LAT, LON = 15.06, 74.06, 35.37, -81.96


def night_points(n, seed, night_key, npts=120, flip=False):
    """One synthetic Transits night as series points (Obs): magnitudes with a transit at epoch n."""
    rng = np.random.default_rng(seed)
    tcn = PLANET["t0"] + n * PLANET["period"]
    jd = np.linspace(tcn - 0.12, tcn + 0.12, npts)
    z = tc.separation(jd, tcn, PLANET["period"], 5.0, 0.3)
    f = tc.occultation(z, 0.12, 0.4, 0.25) + rng.normal(0, 0.002, npts)
    mag = 10.0 - 2.5 * np.log10(f)
    am = 1.05 + 0.8 * ((jd - jd[0]) / (jd[-1] - jd[0]) - 0.3) ** 2
    out = []
    for i in range(npts):
        o = core.Obs(jd=float(jd[i]), mag=float(mag[i]), merr=0.002, cmag=0.0, kmag=None, airmass=float(am[i]),
                     path=f"f{i}.fit", exptime=60.0, flux_var=1.0, flux_comp=1.0, night=night_key, filt="CV")
        if flip and i >= npts // 2:
            o.segment = "flip"
        out.append(o)
    return out


class TestSeriesHelpers(unittest.TestCase):
    def test_night_from_points_flip_and_airmass(self):
        pts = night_points(100, 1, "JD2460150", flip=True)
        n = mn.night_from_points(pts, (RA, DEC), LAT, LON, None, "TOI-2046 b", "series: x")
        self.assertEqual(len(n["t"]), len(pts))
        self.assertIsNotNone(n["airmass"])
        self.assertIsNotNone(n["t_flip"])           # the flip segment comes across (used where FLIP_STEP_ENABLED)
        self.assertFalse(mn.prescaled(n))            # measured errors: the single-night rule scales them
        # BJD_TDB, not JD
        self.assertGreater(abs(float(n["t"][0]) - pts[0].jd) * 86400, 1.0)
        with self.assertRaises(mn.NightError):
            mn.night_from_points(pts[:5], (RA, DEC), LAT, LON)
        with self.assertRaises(mn.NightError):
            mn.night_from_points(pts, None, LAT, LON)

    def test_ephemeris_used_line(self):
        a = {"input": {"eph": {"t0": 2460000.5, "t0_err": 0.0005, "period": 1.4971842, "period_err": 1e-6,
                               "source": "NASA Exoplanet Archive (Ivshina & Winn 2022)"}}}
        line = mn.ephemeris_used_line(a)
        self.assertIn("Ivshina & Winn 2022", line)
        self.assertIn("T0 2460000.5000000 ± 0.0005000", line)
        self.assertIn("P 1.497184200 ± 0.000001000", line)
        self.assertTrue(mn.ephemeris_used_line({}).startswith("Ephemeris used: none"))


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestSeriesInApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test2221.log")
        cls.app = ca.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        app = self.app
        app.mode_key = "transits"
        app.ra_hours.set(f"{RA / 15:.6f}")
        app.dec_deg.set(f"{DEC:.5f}")
        app.lat.set(str(LAT))
        app.lon.set(str(LON))
        app.planet_name.set("TOI-2046 b")
        app.planet_info = {"name": "TOI-2046 b", "t0": PLANET["t0"], "period": PLANET["period"], "t0_err": PLANET["t0_err"], "period_err": PLANET["period_err"],
                           "source": "NASA Exoplanet Archive", "reference": "Ivshina & Winn 2022",
                           "ra": RA, "dec": DEC}
        app._planet_to_fields(PLANET)

    def tearDown(self):
        app = self.app
        tp = app.transit_page
        if getattr(tp, "_multinight", None) is not None:
            tp._multinight.close()
        app.observations = []
        app.reports_saved = {}
        app.series_mode = ""
        app.series_path = ""
        app.multinight_alt_eph = None
        app.update_series_status()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _series(self, epochs):
        app = self.app
        obs = []
        keys = []
        for i, nn in enumerate(epochs):
            key = f"JD24600{50 + i * 3:02d}"
            keys.append(key)
            obs += night_points(nn, 10 + i, key)
        app.observations = obs
        app.series_mode = "transits"
        app.update_series_status()
        self.app.update()
        return keys

    def _pump(self, until, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            self.app.update()
            if until():
                return True
            time.sleep(0.02)
        return False

    def test_one_night_disabled(self):
        tp = self.app.transit_page
        self._series([100])
        self.assertIn("disabled", tp.multinight_series_button.state())
        with mock.patch.object(self.ca.messagebox, "showinfo") as info:
            self.assertIsNone(tp.open_multinight_series())
            self.assertIn("at least two nights", info.call_args[0][1])

    def test_variables_series_disabled(self):
        tp = self.app.transit_page
        self._series([100, 102])
        self.app.series_mode = "variables"
        self.app.mode_key = "variables"
        self.app.update_series_status()
        self.assertIn("disabled", tp.multinight_series_button.state())
        self.assertEqual(mn.series_transit_nights(self.app), [])

    def test_two_nights_prefilled_with_missing_csv(self):
        app, tp = self.app, self.app.transit_page
        keys = self._series([100, 102, 105])
        self.assertNotIn("disabled", tp.multinight_series_button.state())
        # night 1: a fit CSV recorded and present (its Tc goes to the Single-night Tc column); night 2: recorded
        # but deleted since
        good = os.path.join(self.dir, "TOI-2046b_CV_01-OCT-2026_SHOBS-P_transit_fit.csv")
        with open(good, "w", encoding="utf-8") as fh:
            fh.write("# Created 011200Z NOV 26 by SHOBS-P\n# Transit fit: planet TOI-2046 b; filter CV; "
                     f"Tc {PLANET['t0'] + 100 * PLANET['period']:.6f} +/- 0.000700; verdict detected\n"
                     "BJD_TDB,rel_flux,rel_flux_err,model,residual,flag\n")
            for k in range(30):
                fh.write(f"{PLANET['t0'] + 100 * PLANET['period'] - 0.1 + k * 0.007:.6f},1.0,0.002,1.0,0.0,\n")
        gone = os.path.join(self.dir, "TOI-2046b_CV_04-OCT-2026_SHOBS-P_transit_fit.csv")
        app._remember_report(mn.FIT_CSV_KEY, [keys[0]], good)
        app._remember_report(mn.FIT_CSV_KEY, [keys[1]], gone)
        win = tp.open_multinight_series()
        self.assertIsNotNone(win)
        app.update()
        rows = [win.tree.set(i) for i in win.tree.get_children()]
        self.assertEqual(len(rows), 3)          # every night loaded, the one with the missing file too
        self.assertTrue(all(r["use"] == "✔" for r in rows), rows)
        self.assertTrue(all(r["source"].startswith("series: night of") for r in rows))
        self.assertFalse(win.use_current.get())
        self.assertIn(os.path.basename(gone), win.series_note.get())
        self.assertIn("not found", win.series_note.get())
        self.assertTrue(win.series_note_label.winfo_manager())
        self.assertIn(f"{PLANET['t0'] + 100 * PLANET['period']:.5f}", rows[0]["single"])
        # the input ephemeris shown with its source and ±
        self.assertIn("Ivshina & Winn 2022", win.eph_text.get())
        self.assertIn("± 0.72 min", win.eph_text.get())
        self.assertIn("kept with the series", win.eph_text.get())
        self.assertNotIn("disabled", win.series_button.state())
        # the alternative ephemeris is kept with the series and comes back next time
        win.alt_vars["t0"].set(f"{PLANET['t0'] + 0.0008:.6f}")
        win.alt_vars["period"].set(f"{PLANET['period']:.7f}")
        win.alt_vars["source"].set("Exoplanet Watch")
        win.apply_alt()
        self.assertEqual(app.multinight_alt_eph["source"], "Exoplanet Watch")
        self.assertEqual(app._meta()["multinight_alt_eph"]["source"], "Exoplanet Watch")
        win.close()
        win = tp.open_multinight_series()
        app.update()
        self.assertEqual(win.alt_vars["source"].get(), "Exoplanet Watch")
        self.assertIsNotNone(win.eph_alt)

    def test_missing_only_file_and_no_site(self):
        """A night that cannot be made from the points (no site) and whose file is gone: said, others loaded."""
        app, tp = self.app, self.app.transit_page
        keys = self._series([100, 102])
        gone = os.path.join(self.dir, "gone_transit_fit.csv")
        app._remember_report(mn.FIT_CSV_KEY, [keys[1]], gone)
        app.lat.set("")
        nights, msgs = mn.nights_from_series(app)
        self.assertEqual(len(nights), 2)
        self.assertTrue(all(n.get("error") for n in nights))
        self.assertTrue(any("gone_transit_fit.csv" in m and "not found" in m for m in msgs), msgs)

    def test_hint_after_save(self):
        app, tp = self.app, self.app.transit_page
        self._series([100, 102])
        tp.report = None
        tp._series_hint_after_save()
        self.assertIn("2 transit nights", tp.series_hint.get())
        self.assertIn("Multi-night fit…", tp.series_hint.get())

    def test_saved_outputs_keep_ephemeris(self):
        """A joint fit of the series' nights: the timing CSV and the summary name the ephemeris used."""
        app, tp = self.app, self.app.transit_page
        self._series([100, 102])
        nights, msgs = mn.nights_from_series(app)
        self.assertEqual(msgs, [])
        mn.check_nights(nights, "TOI-2046 b")
        self.assertEqual([n["use"] for n in nights], [True, True])
        fit = mn.joint_fit(nights, dict(PLANET, ld_band="CV"), mcmc_steps=200)
        eph = {"t0": PLANET["t0"], "t0_err": PLANET["t0_err"], "period": PLANET["period"],
               "period_err": PLANET["period_err"], "source": "NASA Exoplanet Archive (Ivshina & Winn 2022)"}
        a = mn.timing_analysis(fit["nights"], eph, None, fit["period"])
        path = os.path.join(self.dir, "t.csv")
        mn.write_timing_csv(path, fit, a, {"version": "2.2.21", "series": "TOI-2046b.cvseries"})
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Ivshina & Winn 2022", text)
        self.assertIn("# Nights from the series TOI-2046b.cvseries", text)
        self.assertTrue(any("Ephemeris used:" in t and "Ivshina" in t for _l, t in mn.summary_lines(fit, a)))
        for n in fit["nights"]:
            self.assertLess(abs(n["tc"][0] - (PLANET["t0"] + round((n["tc"][0] - PLANET["t0"]) / PLANET["period"])
                                              * PLANET["period"])) * 1440, 5.0)
        from matplotlib.figure import Figure
        png = os.path.join(self.dir, "t.png")
        mn.save_figure(Figure(), png, fit, a, "TOI-2046 b", "2.2.21")
        self.assertTrue(os.path.getsize(png) > 1000)

    def test_sash_not_at_top(self):
        """nit g: the divider opens below the nights table and Fit nights, not at the very top."""
        app, tp = self.app, self.app.transit_page
        win = tp.open_multinight()
        self._pump(lambda: getattr(win, "_sash_placed", False), timeout=10)
        app.update()
        pos = int(win.panes.sashpos(0))
        self.assertGreater(pos, 100)
        self.assertGreaterEqual(pos, min(win.upper.winfo_reqheight(), int(0.55 * win.panes.winfo_height())) - 2)
        self.assertLess(pos, win.panes.winfo_height())
        self.assertGreater(win.tree.winfo_height(), 20)


if __name__ == "__main__":
    unittest.main()
