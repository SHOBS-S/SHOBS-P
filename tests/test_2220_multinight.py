"""2.2.20 (items 1, 1b, 5, #45): Multi-night window layout and plots, corner plot from the joint chain, airmass in
the transit-fit CSV and the ⚠ when nights are detrended differently."""
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")

import transit_core as tc  # noqa: E402
import transit_posterior as tpost  # noqa: E402
import transit_ui as tui  # noqa: E402
import multinight as mn  # noqa: E402

PLANET = dict(name="TOI-2046 b", host="TOI-2046", period=1.4971842, t0=2460000.5, t0_err=0.0005, period_err=1e-6,
              k=0.12, a_rs=5.0, b=0.3, teff=6000, t14=0.08)


def synth_night(n, seed, am_slope=0.01, npts=200):
    rng = np.random.default_rng(seed)
    tcn = PLANET["t0"] + n * PLANET["period"]
    t = np.linspace(tcn - 0.12, tcn + 0.12, npts)
    am = 1.05 + 0.8 * ((t - t[0]) / (t[-1] - t[0]) - 0.3) ** 2
    z = tc.separation(t, tcn, PLANET["period"], 5.0, 0.3)
    f = tc.occultation(z, 0.12, 0.4, 0.25) * (1 + am_slope * (am - am.mean()))
    f = f + rng.normal(0, 0.002, len(t))
    return t, f, np.full(len(t), 0.002), am


_FITS = {}


def single_fit(n, seed):
    """One synthetic night through the real single-night fit (cached: a few seconds each)."""
    if (n, seed) not in _FITS:
        t, f, e, am = synth_night(n, seed)
        _FITS[(n, seed)] = tc.fit_transit(t, f, e, am, PLANET, mcmc_steps=200)
    return _FITS[(n, seed)]


def write_old_csv(path, r):
    """A fit CSV as 2.2.19 wrote it: six columns, no airmass, no header comments beyond Created and Tc."""
    import csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write("# Created 011200Z NOV 26 by SHOBS-P\r\n")
        w = csv.writer(fh)
        w.writerow(["BJD_TDB", "rel_flux", "rel_flux_err", "model", "residual", "flag"])
        for k in range(len(r["t"])):
            w.writerow([f"{r['t'][k]:.7f}", f"{r['flux'][k]:.6f}", f"{r['err_scaled'][k]:.6f}",
                        f"{r['model'][k]:.6f}", f"{r['residuals'][k]:.6f}", ""])


class TestFitCsv(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_new_csv_round_trip_with_airmass(self):
        r = single_fit(100, 1)
        p = os.path.join(self.dir, "a_transit_fit.csv")
        tui.write_fit_csv(p, r, False, {"filter": "CV"})
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        head = [ln for ln in text.splitlines() if ln.startswith("BJD_TDB")][0]
        # the old six columns first, so older readers still work
        self.assertTrue(head.startswith("BJD_TDB,rel_flux,rel_flux_err,model,residual,flag"))
        self.assertIn("airmass", head)
        self.assertIn("NOT detrended", text)
        n = mn.load_fit_csv(p)
        self.assertIsNotNone(n["airmass"])
        self.assertEqual(len(n["airmass"]), len(n["t"]))
        self.assertIsNotNone(n["single_tc"])
        self.assertAlmostEqual(n["single_tc"][0], r["summary"]["tc"][0], places=5)

    def test_flip_segment_round_trip(self):
        r = dict(single_fit(100, 1))
        t_flip = float(np.median(r["t"]))
        p = os.path.join(self.dir, "f_transit_fit.csv")
        tui.write_fit_csv(p, r, False, {"filter": "CV", "t_flip": t_flip})
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Meridian flip at BJD_TDB", text)
        self.assertIn("after flip", text)
        n = mn.load_fit_csv(p)
        self.assertAlmostEqual(n["t_flip"], t_flip, places=5)

    def test_no_airmass_data_writes_blank(self):
        r = dict(single_fit(100, 1))
        r["airmass"] = np.full(len(r["t"]), np.nan)     # mono / no header pointing: no airmass at all
        p = os.path.join(self.dir, "n_transit_fit.csv")
        tui.write_fit_csv(p, r, False, {"filter": "CV"})
        with open(p, encoding="utf-8") as fh:
            self.assertIn("none in these data", fh.read())
        n = mn.load_fit_csv(p)
        self.assertIsNone(n["airmass"])

    def test_old_csv_still_loads_and_warns(self):
        p_new = os.path.join(self.dir, "TOI-2046b_CV_01-OCT-2026_SHOBS-P_transit_fit.csv")
        p_old = os.path.join(self.dir, "TOI-2046b_CV_04-OCT-2026_SHOBS-P_transit_fit.csv")
        tui.write_fit_csv(p_new, single_fit(100, 1), False, {"filter": "CV"})
        write_old_csv(p_old, single_fit(102, 2))
        nights = [mn.load_fit_csv(p_new), mn.load_fit_csv(p_old)]
        self.assertIsNone(nights[1]["airmass"])
        self.assertIsNone(nights[1]["single_tc"])     # no Tc line in the old file
        mn.check_nights(nights, "TOI-2046 b", None, True)
        self.assertEqual(nights[0]["status"][0], "ok")
        self.assertEqual(nights[1]["status"][0], "warn")
        self.assertTrue(nights[1]["status"][1].startswith("⚠ No airmass in this file"))
        self.assertTrue(nights[1]["use"])
        # time-only detrending: every night matches, no caution
        mn.check_nights(nights, "TOI-2046 b", None, False)
        self.assertEqual(nights[1]["status"][0], "ok")
        # quick single-night Tc for the column
        tcv, sig = mn.quick_single_tc(nights[1], PLANET, False, True)
        self.assertLess(abs(tcv - (PLANET["t0"] + 102 * PLANET["period"])) * 1440, 5.0)
        self.assertGreater(sig, 0)

    def test_all_without_airmass_no_warning(self):
        nights = []
        for i, nn in enumerate((100, 102)):
            p = os.path.join(self.dir, f"TOI-2046b_CV_0{i + 1}-OCT-2026_SHOBS-P_transit_fit.csv")
            write_old_csv(p, single_fit(nn, i + 1))
            nights.append(mn.load_fit_csv(p))
        mn.check_nights(nights, "TOI-2046 b", None, True)
        self.assertEqual([n["status"][0] for n in nights], ["ok", "ok"])


class TestJointPlotsAndCorner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        nights = []
        for i, nn in enumerate((100, 102, 105, 109)):
            t, f, e, am = synth_night(nn, 10 + i, npts=160)
            nights.append(mn._finish_night({"kind": "csv", "source": f"n{i}", "planet": "TOI-2046 b", "filter": "CV",
                                            "obsdate": f"0{i + 1}-OCT-2026", "t": t, "flux": f, "err": e,
                                            "airmass": am, "t_flip": None, "single_tc": None, "notes": []}))
        mn.check_nights(nights, "TOI-2046 b")
        cls.fit = mn.joint_fit(nights, dict(PLANET, ld_band="CV"), mcmc_steps=300)
        eph = {"t0": PLANET["t0"], "t0_err": PLANET["t0_err"], "period": PLANET["period"],
               "period_err": PLANET["period_err"], "source": "a test ephemeris with a long name " * 3}
        cls.a = mn.timing_analysis(cls.fit["nights"], eph, None, PLANET["period"])

    def test_draw_layout(self):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        eph_alt = {"t0": 2460000.501, "t0_err": 0.0003, "period": 1.4971842, "period_err": 2e-6,
                   "source": "Exoplanet Watch, a long source name for the test " * 2}
        a = mn.timing_analysis(self.fit["nights"], self.a["input"]["eph"], eph_alt, 1.4971842)
        for size in ((11, 5.4), (8, 4.2), (14, 8)):
            fig = Figure(figsize=size, dpi=100, layout="constrained")
            FigureCanvasAgg(fig)
            mn.draw(fig, self.fit, a, "TOI-2046 b")
            fig.canvas.draw()
            axes = fig.get_axes()
            data_axes = [ax for ax in axes if ax.axison]
            lc = data_axes[-1]
            # no legend inside any data axes; one shared legend outside them all
            self.assertTrue(all(ax.get_legend() is None for ax in data_axes))
            legends = [ax.get_legend() for ax in axes if ax.get_legend() is not None]
            self.assertEqual(len(legends), 1)
            r = fig.canvas.get_renderer()
            lb = legends[0].get_window_extent(r)
            for ax in data_axes:
                self.assertFalse(lb.overlaps(ax.get_window_extent(r)), f"legend over a panel at {size}")
            # O - C panels: few y ticks, and their labels never overlap; titles stay in their column
            for ax in data_axes[:-1]:
                boxes = [lab.get_window_extent(r) for lab in ax.get_yticklabels()
                         if lab.get_text() and lab.get_visible()]
                self.assertLessEqual(len(boxes), 6)
                for b1, b2 in zip(boxes, boxes[1:]):
                    self.assertFalse(b1.overlaps(b2), "y tick labels overlap")
                tb = ax.title.get_window_extent(r)
                self.assertGreaterEqual(tb.x0, -1)
                self.assertLess(tb.x1, lc.get_window_extent(r).x0, f"O - C title runs into the next column at {size}")
            tb = lc.title.get_window_extent(r)
            self.assertLessEqual(tb.x1, fig.bbox.width + 1, f"light-curve title clipped at {size}")
            # night labels in the right margin, outside the data
            for t in lc.texts:
                self.assertGreaterEqual(t.get_window_extent(r).x0, lc.get_window_extent(r).x1)
                self.assertLessEqual(t.get_window_extent(r).x1, fig.bbox.width + 1, f"night label clipped at {size}")

    def test_nights_do_not_overlap(self):
        nights = [self.fit["nights"][i] for i in self.a["order"]]
        step = mn.lc_offset(self.fit, nights)
        depth = self.fit["shape"]["all"]["depth"][0]
        for n in nights:
            lo, hi = np.percentile(n["detrended"], [0.2, 99.8])
            self.assertGreater(step, hi - lo)   # a night's spread fits in one step: no overlap with the next
        self.assertGreater(step, depth)

    def test_corner_from_joint_chain(self):
        res = mn.corner_result(self.fit, "TOI-2046 b")
        names = tpost.corner_params(res, shape_only=True)
        self.assertEqual(names[:3], ["k:all", "a_rs", "b"])
        self.assertEqual(sum(1 for p in names if p.startswith("tc:")), 4)
        self.assertTrue(all(res["labels"][p].startswith("Tc ") for p in names if p.startswith("tc:")))
        fig = tpost.corner_figure(res, shape_only=True)
        titles = [ax.get_title() for ax in fig.get_axes()]
        self.assertTrue(any(t.startswith("Tc 01-OCT-2026\n246") for t in titles))
        self.assertIsInstance(res["bound_flags"], list)
        allp = tpost.corner_params(res, shape_only=False)
        self.assertGreater(len(allp), len(names))

    def test_progress_words(self):
        self.assertEqual(mn.progress_words(4, "step", 1200, 5000), "Fitting 4 nights… step 1,200 of 5,000")
        self.assertIn("each night alone (2 of 4)", mn.progress_words(4, "prep", 1, 4))


class TestCornerTitle(unittest.TestCase):
    def test_title_wrapped_and_clear_of_top_panel(self):
        r = single_fit(100, 1)
        flag = {"name": "tc", "label": "Tc", "level": "warn", "text": "x"}
        for res in (dict(r, planet=dict(r["planet"], name="K2-29 b")),
                    dict(r, planet=dict(r["planet"], name="K2-29 b"), bound_flags=[flag])):
            for shape_only in (True, False):
                fig = tpost.corner_figure(res, shape_only=shape_only)
                fig.canvas.draw()
                rend = fig.canvas.get_renderer()
                self.assertTrue(fig._suptitle.get_text().startswith("K2-29 b: MCMC posterior"))
                texts = [fig._suptitle] + [t for t in fig.texts if t.get_text()]
                lowest = min(t.get_window_extent(rend).y0 for t in texts)
                for t in texts:
                    bb = t.get_window_extent(rend)
                    self.assertGreaterEqual(bb.x0, 0, "title clipped on the left")
                    self.assertLessEqual(bb.x1, fig.bbox.width, "title clipped on the right")
                    self.assertLessEqual(bb.y1, fig.bbox.height)
                tb = fig.get_axes()[0].title.get_window_extent(rend)
                self.assertLess(tb.y1, lowest, "top panel title runs into the main title")


class _FakeApp:
    """Just enough of the app for the Multi-night window (Tk root, job flags, log, call_ui queue)."""

    def __init__(self, root):
        self.root = root
        self.busy = False
        self._cancel = threading.Event()
        self._q = queue.Queue()
        self.logs = []
        import tkinter as tk
        self.status = tk.StringVar(master=root)

    def __getattr__(self, name):
        return getattr(self.root, name)

    def call_ui(self, func, *args):
        self._q.put((func, args))

    def drain(self):
        while True:
            try:
                func, args = self._q.get_nowait()
            except queue.Empty:
                return
            func(*args)

    def log(self, m):
        self.logs.append(m)

    def _start_job(self, label, bar=True):
        self.busy = True
        return True

    def _end_job(self):
        self.busy = False

    def cancel_job(self):
        self._cancel.set()


class TestWindow(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.root = tk.Tk()
        self.app = _FakeApp(self.root)
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        shutil.rmtree(self.dir, ignore_errors=True)

    def pump(self, until, timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            self.app.drain()
            self.root.update()
            if until():
                return True
            time.sleep(0.02)
        return False

    def test_window_mixed_nights_fit_and_corner(self):
        paths = []
        for i, nn in enumerate((100, 102, 105)):
            p = os.path.join(self.dir, f"TOI-2046b_CV_0{i + 1}-OCT-2026_SHOBS-P_transit_fit.csv")
            if i < 2:
                tui.write_fit_csv(p, single_fit(nn, i + 1), False, {"filter": "CV"})
            else:
                write_old_csv(p, single_fit(nn, i + 1))
            paths.append(p)
        page = type("P", (), {"current_planet": lambda s: dict(PLANET), "result": None,
                              "detrend_airmass": type("V", (), {"get": lambda s: True})(),
                              "detrend_time": type("V", (), {"get": lambda s: False})()})()
        w = mn.MultiNightWindow(self.app, page)
        w.add_files(paths)
        self.root.update()
        statuses = [self.tree_value(w, iid, "status") for iid in w.tree.get_children()]
        self.assertTrue(statuses[2].startswith("⚠ No airmass in this file"))
        self.assertIn("No airmass in this file", w.night_note.get())
        self.assertTrue(w.time_only_button.winfo_manager())
        self.assertTrue(w.prog_text.get().startswith("⚠"))
        # the old CSV's Single-night Tc is filled by a quick fit
        self.assertTrue(self.pump(lambda: bool(self.tree_value(w, w.tree.get_children()[2], "single").strip()
                                               and "fitting" not in self.tree_value(w, w.tree.get_children()[2],
                                                                                    "single"))))
        self.assertIn("±", self.tree_value(w, w.tree.get_children()[2], "single"))
        # "Fit every night against time only" clears the caution
        w.fit_time_only()
        self.root.update()
        self.assertFalse(w.detrend_airmass.get())
        self.assertTrue(w.detrend_time.get())
        self.assertEqual(w.night_note.get(), "")
        self.assertFalse(w.time_only_button.winfo_manager())
        # fit: busy bar words, no filling bar, corner button enabled after
        self.assertFalse(hasattr(w, "bar"))
        self.assertIn("disabled", w.corner_button.state())
        w.mcmc.set("Quick (1,500 steps)")
        w.run_fit()
        self.assertTrue(w.busy.busy)
        self.assertTrue(w.busy.text.startswith("Fitting 3 nights…"))
        self.assertTrue(self.pump(lambda: not w._running, timeout=300))
        self.assertIsNotNone(w.fit, w.prog_text.get())
        self.assertFalse(w.busy.busy)
        self.assertTrue(w.prog_text.get().startswith("✔"))
        self.assertNotIn("disabled", w.corner_button.state())
        # pop out follows the fit; canvas fills and follows resizes
        w.pop_out()
        self.root.update()
        pop = w._popouts[0]
        pop.win.geometry("900x600")
        self.root.update()
        self.root.update()
        cw = pop.canvas.get_tk_widget()
        self.assertGreater(cw.winfo_width(), 700)
        pop.win.geometry("1200x800")
        self.pump(lambda: cw.winfo_width() > 1000, timeout=5)
        self.assertGreater(cw.winfo_width(), 1000)
        # corner plot window
        cwin = mn.MultiCornerWindow(self.app, mn.corner_result(w.fit, "TOI-2046 b"), "x", w.win)
        self.root.update()
        self.assertIsNotNone(cwin.fig)
        w.close()

    @staticmethod
    def tree_value(w, iid, col):
        return str(w.tree.set(iid, col))


if __name__ == "__main__":
    unittest.main()
