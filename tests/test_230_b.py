"""2.3.0 builder B: #8 (meridian-flip step as a BIC-gated free parameter), #51 (busy bar for the star-name planet
lookup, no "looking up…" in the ephemeris boxes), #52 (Tonight's transits and the Transit planner removed), and the
Output page's "Save corner plot…" button (John's note)."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402

import transit_core as tc  # noqa: E402

# A synthetic hot Jupiter (HD 189733 b-like shape, 24 ppt deep), cadence ~50 s, 397 points as on Matthew's night.
PLANET = {"name": "Synth b", "host": "Synth", "period": 2.21857, "t0": 2461000.0, "t0_err": 0.0002,
          "period_err": 1e-6, "k": 0.155, "a_rs": 8.8, "b": 0.66, "t14": 0.0760, "teff": 5050.0, "depth": 0.024}
TC = 2461000.0 + 1000 * PLANET["period"]


def synth_night(step_mmag, seed=1, flip_frac=0.77, n=397, noise=0.004):
    """The whole transit before the flip (as on HD 189733 b, 23 Jul 2025); after it, baseline only, dimmed by
    step_mmag (a +16 mmag step = the target fainter by 16 mmag after the flip)."""
    rng = np.random.default_rng(seed)
    t = TC + np.linspace(-0.07, 0.16, n)
    prob = tc.TransitProblem(t - 2461000.0, np.ones(n), np.ones(n), None, PLANET["period"], {}, ("tc",),
                             {"tc": TC - 2461000.0, "k": 0.155, "a_rs": 8.8, "b": 0.66, "q1": 0.5, "q2": 0.3,
                              "c0": 1.0})
    model = prob.model(prob.full([TC - 2461000.0]))
    after = np.arange(n) >= int(flip_frac * n)
    flux = model * np.where(after, 10 ** (-0.4 * step_mmag / 1000.0), 1.0) + rng.normal(0, noise, n)
    am = 1.2 + 0.3 * ((t - t.mean()) / 0.1) ** 2
    return t, flux, np.full(n, noise), am, after


class TestFlipStepGate(unittest.TestCase):
    """#8: the step is fitted only on a flip night, and kept only when ΔBIC >= FLIP_STEP_DBIC."""

    def test_enabled_with_threshold(self):
        self.assertTrue(tc.FLIP_STEP_ENABLED)
        self.assertEqual(tc.FLIP_STEP_DBIC, 10.0)

    def test_step_recovers_depth_without_it_biased(self):
        for seed in (1, 2):
            t, f, e, am, af = synth_night(16.0, seed=seed)
            gated = tc.lsq_fit(t, f, e, am, PLANET, detrend_airmass=False, after_flip=af)
            plain = tc.lsq_fit(t, f, e, am, PLANET, detrend_airmass=False, after_flip=af, flip_step=False)
            ft = gated["flip_test"]
            self.assertTrue(ft["kept"], ft)
            self.assertGreater(ft["dbic"], tc.FLIP_STEP_DBIC)
            self.assertIn("step", gated["free"])
            # +16 mmag fainter = relative flux x 0.9854: a step of about -14.6 ppt
            self.assertAlmostEqual(gated["p"]["step"], 10 ** (-0.4 * 0.016) - 1, delta=3 * gated["err"]["step"])
            d, de = gated["depth"]
            self.assertLess(abs(d - PLANET["depth"]), 2 * de, (d, de))           # recovered within errors
            self.assertLess(plain["depth"][0], PLANET["depth"] - 3 * de)          # biased shallow without the term
            self.assertNotIn("step", plain["free"])

    def test_no_flip_night_never_adds_the_term(self):
        t, f, e, am, _af = synth_night(0.0)
        r = tc.lsq_fit(t, f, e, am, PLANET, detrend_airmass=False, after_flip=None)
        self.assertIsNone(r["flip_test"])
        self.assertNotIn("step", r["free"])
        self.assertIsNone(r["t_flip"])
        r = tc.lsq_fit(t, f, e, am, PLANET, detrend_airmass=False, after_flip=np.zeros(len(t), bool))
        self.assertIsNone(r["flip_test"])
        self.assertNotIn("step", r["free"])

    def test_flip_night_without_a_real_step_rejected(self):
        for seed in (1, 2, 3):
            t, f, e, am, af = synth_night(0.0, seed=seed)
            r = tc.lsq_fit(t, f, e, am, PLANET, detrend_airmass=False, after_flip=af)
            ft = r["flip_test"]
            self.assertIsNotNone(ft)
            self.assertFalse(ft["kept"], ft)
            self.assertLess(ft["dbic"], tc.FLIP_STEP_DBIC)
            self.assertNotIn("step", r["free"])                    # fitted without the step
            self.assertAlmostEqual(r["depth"][0], PLANET["depth"], delta=2 * r["depth"][1])

    def test_forced_on_and_off(self):
        t, f, e, am, af = synth_night(0.0)
        on = tc.lsq_fit(t, f, e, am, PLANET, detrend_airmass=False, after_flip=af, flip_step=True)
        self.assertIn("step", on["free"])
        self.assertIsNone(on["flip_test"])     # not gated
        off = tc.lsq_fit(t, f, e, am, PLANET, detrend_airmass=False, after_flip=af, flip_step=False)
        self.assertNotIn("step", off["free"])


class TestFlipStepReporting(unittest.TestCase):
    """#8: the full fit (MCMC) on a stepped night and on a clean flip night, and every place that reports it."""

    @classmethod
    def setUpClass(cls):
        t, f, e, am, af = synth_night(16.0)
        cls.kept = tc.fit_transit(t, f, e, am, PLANET, mcmc_steps=300, after_flip=af,
                                  flip_source="the frames' flip segment")
        t, f, e, am, af = synth_night(0.0)
        cls.dropped = tc.fit_transit(t, f, e, am, PLANET, mcmc_steps=300, after_flip=af,
                                     flip_source="the frames' flip segment")

    def test_mcmc_kept(self):
        r = self.kept
        self.assertTrue(r["flip_test"]["kept"])
        self.assertIsNotNone(r["flip"])
        self.assertIn("step", r["summary"])
        d, de = r["summary"]["depth"]
        self.assertLess(abs(d - PLANET["depth"]), 2.5 * de, (d, de))
        line = tc.flip_step_line(r)
        self.assertTrue(line.startswith("Flip step fitted: -"), line)
        self.assertIn("ppt (ΔBIC", line)
        self.assertTrue(line.endswith(", kept"), line)

    def test_mcmc_dropped(self):
        r = self.dropped
        self.assertFalse(r["flip_test"]["kept"])
        self.assertIsNone(r["flip"])
        self.assertNotIn("step", r["summary"])
        self.assertTrue(tc.flip_step_line(r).startswith("Flip step not needed: "), tc.flip_step_line(r))
        self.assertIn("kept only at 10 or more", tc.flip_step_line(r))
        # the flags (page notes, PNG, fit text) say it was tested and left out
        self.assertTrue(any("this fit has no step term" in text for _l, text in r["flags"]))

    def test_texts(self):
        meta = {"version": "2.3.0", "obscode": "XYZ", "filter": "V"}
        for r, word in ((self.kept, "kept"), (self.dropped, "not needed")):
            text = tc.fit_text(r, meta)
            self.assertIn("Meridian flip", text)
            self.assertIn(word, text)
            note = tc.flip_report_note(r)
            self.assertTrue(note.isascii(), note)
            # the flip time is always written in the form a later fit reads back
            self.assertAlmostEqual(tc.report_flip_time({"NOTES": note}), r["flip_test"]["t_flip"], places=4)
            rep = tc.aavso_exoplanet_report(r, meta)
            self.assertIn("#DETREND_PARAMETERS=AIRMASS\n", rep)   # a column list: the step is in NOTES instead
        self.assertIn("meridian flip step", tc.fit_text(self.kept, meta))
        self.assertIn("(kept, dBIC", tc.flip_report_note(self.kept))
        self.assertIn("no step in this fit", tc.flip_report_note(self.dropped))


class TestMultiNightFlip(unittest.TestCase):
    """#8: the joint fit keeps a night's flip step only when that night's own BIC test kept it."""

    def test_joint_problem_respects_the_night(self):
        import multinight
        t = 2461334.5 + np.linspace(0, 0.2, 60)
        night = {"t": t, "flux": np.ones_like(t), "err": np.full_like(t, 0.002), "airmass": 1 + 0.5 * (t - t[0]),
                 "t_flip": float(t[30]), "group": "g"}
        jp = multinight.JointProblem([night], 1.5, {}, {}, ["g"], {})
        self.assertIn("step:0", jp.free)
        jp = multinight.JointProblem([dict(night, flip_step=False)], 1.5, {}, {}, ["g"], {})
        self.assertNotIn("step:0", jp.free)

    def test_joint_fit_per_night_steps(self):
        import multinight
        nights = []
        for k, step in ((0, 16.0), (1, 0.0)):
            t, f, e, am, af = synth_night(step, seed=5 + k)
            shift = k * 3 * PLANET["period"]
            tt = t + shift
            nights.append({"t": tt, "flux": f / np.median(f), "err": e, "airmass": None,
                           "t_flip": 0.5 * (tt[~af].max() + tt[af].min()), "use": True, "source": f"n{k}",
                           "filter": "V", "kind": "csv", "group": "all"})
        fit = multinight.joint_fit(nights, PLANET, detrend_airmass=False, mcmc_steps=200)
        a, b = fit["nights"]
        self.assertTrue(a["flip"])
        self.assertIsNotNone(a["flip_step"])
        self.assertFalse(b["flip"])
        self.assertIn("step:0", fit["free"])
        self.assertNotIn("step:1", fit["free"])
        self.assertNotIn("flip_step", nights[0])   # the nights handed in are not changed


class TestPlannerGone(unittest.TestCase):
    """#52: Tonight's transits and the Transit planner are gone, with the code that only served them."""

    def test_code_gone(self):
        import photometry_core as core
        import transit_ui
        for name in ("FinderWindow", "PlannerWindow", "_plan_limits", "_altaz"):
            self.assertFalse(hasattr(transit_ui, name), name)
        for name in ("plan_transits", "find_transits", "night_window", "load_transit_catalog", "swarthmore_url",
                     "moon_radec", "altitude", "peak_altitude", "PLAN_MIN_ALT", "CATALOG_FILE"):
            self.assertFalse(hasattr(tc, name), name)
        # still used elsewhere
        for name in ("bjd_minus_jd_days", "sun_radec", "jd_to_local", "now_jd", "ZENITH_KEYHOLE_ALT"):
            self.assertTrue(hasattr(tc, name), name)
        text = core.credits_text()
        self.assertNotIn("Swarthmore", text)
        self.assertNotIn("planner", text.lower())
        self.assertNotIn("Tonight's transits", text)

    def test_no_mention_in_the_code(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for fn in ("clearv_app.py", "transit_ui.py", "photometry_core.py", "multinight.py", "transit_core.py"):
            with open(os.path.join(here, fn), encoding="utf-8") as fh:
                src = fh.read()
            for word in ("Tonight's transits…", "Transit planner…", "_open_finder", "FinderWindow(",
                         "PlannerWindow("):
                self.assertFalse(word in src, (fn, word))


class TestUi(unittest.TestCase):
    """#51, #52 and the corner-plot button on the real window."""

    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test_230_b.log")
        cls.app = ca.App()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def _pump(self, until, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.update()
            if until():
                return True
            time.sleep(0.02)
        return False

    @staticmethod
    def _buttons(w):
        from tkinter import ttk
        out = [w] if isinstance(w, ttk.Button) else []
        for c in w.winfo_children():
            out += TestUi._buttons(c)
        return out

    def test_no_planner_buttons(self):
        texts = [str(b.cget("text")) for b in self._buttons(self.app)]
        for gone in ("Tonight's transits…", "Transit planner…", "Corner plot…"):
            self.assertNotIn(gone, texts)
        self.assertNotIn("planner", self.ca.MODES["transits"]["banner"].lower())
        self.assertNotIn("planner", self.ca.MODES["transits"]["subtitle"].lower())

    def test_corner_button_in_results_row(self):
        tp = self.app.transit_page
        names = [str(b.cget("text")) for b in tp._results_box.winfo_children()]
        self.assertIn("Save corner plot…", names)
        i = names.index("Save corner plot…")
        self.assertEqual(names[i + 1], "Save field image…")
        self.assertEqual(names[i - 1], "Save plot PNG…")
        import transit_ui
        with mock.patch.object(transit_ui, "CornerWindow") as cw, \
                mock.patch.object(transit_ui.messagebox, "showinfo") as info:
            tp.result = None
            tp._results_box.winfo_children()[i].invoke()
            info.assert_called_once()   # no fit: "Fit a transit first…"
            cw.assert_not_called()

    def test_star_name_lookup_keeps_the_bar_until_the_boxes_fill(self):
        app = self.app
        app.set_mode("transits")
        app.planet_name.set("")
        app.planet_info = None
        for var in (app.pl_period, app.pl_t0, app.pl_t14, app.pl_depth):
            var.set("")
        seen = []

        def slow_star(_n):
            time.sleep(0.6)
            return {"system": "TrES-3", "planets": ["TrES-3 b"], "transiting": {"TrES-3 b"}}

        def slow_planet(_n):
            time.sleep(0.6)
            return {"name": "TrES-3 b", "host": "TrES-3", "period": 1.30618627, "t0": 2454185.9104, "ra": 268.0292,
                    "dec": 37.5463, "source": "NASA Exoplanet Archive"}

        with mock.patch.object(self.ca.core, "lookup_target",
                               lambda n: {"ra_hours": 17.8686, "dec_deg": 37.5463, "name": n, "source": "SIMBAD"}), \
                mock.patch.object(self.ca.tcore, "star_planets", slow_star), \
                mock.patch.object(self.ca.tcore, "lookup_planet", slow_planet), \
                mock.patch.object(app, "_start_target_status", lambda *a, **k: None):
            app.star_id.set("TrES-3")
            app._last_lookup_name = None
            app.lookup_star(quiet=True)

            def done():
                seen.append((app.busy_bar.busy, app.busy_bar.text, app.pl_period.get()))
                return bool(app.pl_period.get())
            self.assertTrue(self._pump(done, 15))
        texts = [s[1] for s in seen]
        self.assertTrue(any("TrES-3's planets" in x for x in texts), texts)
        self.assertTrue(any(x.startswith("Looking up planet TrES-3 b") for x in texts), texts)
        # busy from the start until the period appears, never idle in between
        before = [s for s in seen if not s[2]]
        self.assertTrue(all(s[0] for s in before), before)
        # no placeholder text in any ephemeris box, ever
        self.assertTrue(all("looking" not in s[2].lower() for s in seen))
        self.assertEqual(app.pl_period.get(), "1.30618627")
        self._pump(lambda: not app.busy_bar.busy, 2)
        self.assertFalse(app.busy_bar.busy)

    def test_star_with_no_planets_says_so(self):
        app = self.app
        app.set_mode("transits")
        with mock.patch.object(self.ca.tcore, "star_planets", lambda _n: None):
            app.star_id.set("Nowhere-1")
            app._planets_for_star("Nowhere-1", {})
            self.assertTrue(app.busy_bar.busy)
            self.assertTrue(self._pump(lambda: not app.busy_bar.busy, 5))
        self.assertTrue(app.planet_source.get().startswith("✖ No planets found for Nowhere-1"))
        app.star_id.set("")

    def test_flip_fit_on_the_page_and_png(self):
        """#8: both flip nights through the page (table row, result line, notes) and its PNG (looked at by eye)."""
        app, tp = self.app, self.app.transit_page
        out = os.environ.get("SHOBS_TEST_PNG_DIR") or tempfile.mkdtemp()
        rep = TestFlipStepReporting
        if not hasattr(rep, "kept"):
            rep.setUpClass()
        app.set_mode("transits")
        app.show_step(self.ca.STEP_OUTPUT)
        for r, name, row in ((rep.kept, "flip_kept.png", "Flip step (ppt)"),
                             (rep.dropped, "flip_dropped.png", "Flip step")):
            tp.report = None
            tp.result = dict(r)
            tp.meta_for_files = {}
            tp._fill_table()
            tp.draw()
            app.update()
            rows = {str(tp.table.item(i)["values"][0]): tp.table.item(i)["values"] for i in tp.table.get_children()}
            self.assertIn(row, rows)
            cell = str(rows[row][1])
            self.assertIn("kept" if r is rep.kept else "not needed", cell)
            path = os.path.join(out, name)
            tp._write_png(path)
            self.assertGreater(os.path.getsize(path), 10000)
        tp.result = None
        app.show_step(0)

    def test_planet_boxes_get_no_placeholder(self):
        app = self.app
        self.assertFalse(hasattr(app, "PLANET_PLACEHOLDER"))
        app.pl_period.set("")
        app._lookup_busy_start("planet", "X b", "NASA Exoplanet Archive")
        self.assertEqual(app.pl_period.get(), "")
        app._lookup_busy_stop("planet", name="X b")
        self.assertEqual(app.pl_period.get(), "")


if __name__ == "__main__":
    unittest.main()
