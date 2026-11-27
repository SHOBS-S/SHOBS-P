"""2.4.0 fit-in F4-3, part 1: Multi-night.

1. The time curve (quadratic) and the per-night Auto (BIC) detrend choice in the joint fit (#103 for Multi-night).
2. m655 / m660: a night without airmass and nights in more than one filter are handled by the fit itself: plain
   words, one note, no ⚠ and no button; a depth per filter is the default when the filters differ."""
import inspect
import os
import queue
import sys
import threading
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import multinight as mn  # noqa: E402
import transit_core as tc  # noqa: E402

PLANET = dict(name="T b", host="T", period=2.5, t0=2460000.0, t0_err=1e-4, period_err=1e-6, k=0.12, a_rs=8.0,
              b=0.3, teff=5800, t14=0.11)


def night(i, curve=0.0, slope=0.0, am_slope=0.0, with_am=True, filt="CV", n=150, sigma=0.002):
    """One synthetic night: the exact model times a baseline (curve·dt² + slope·dt + am_slope·airmass)."""
    rng = np.random.default_rng(40 + i)
    tcen = PLANET["t0"] + PLANET["period"] * (100 + 3 * i)
    t = np.linspace(tcen - 0.12, tcen + 0.12, n)
    u1, u2 = tc.limb_darkening_guess(PLANET["teff"], filt)
    f = tc.occultation(tc.separation(t, tcen, PLANET["period"], 8.0, 0.3), 0.12, u1, u2)
    # airmass that is not a parabola in time, so the airmass and the time-curve choices can be told apart
    am = 1.05 + 0.9 * np.exp(6.0 * (t - t[0])) / np.exp(6.0 * 0.24)
    dt = t - t.mean()
    f = f * (1.0 + curve * dt ** 2 + slope * dt + am_slope * (am - am.mean())) + rng.normal(0, sigma, n)
    return mn._finish_night({"kind": "csv", "path": "", "source": f"n{i}.csv", "planet": "T b", "host": "",
                             "filter": filt, "obsdate": f"0{i + 1}-OCT-2026", "software": "SHOBS-P", "t": t,
                             "flux": f, "err": np.full(n, sigma), "airmass": am if with_am else None,
                             "t_flip": None, "single_tc": None, "time_note": "", "notes": []})


class JointProblemCurve(unittest.TestCase):
    def _prob(self, night_detrend):
        nights = [night(0), night(1, with_am=False)]
        start = {"a_rs": 8.0, "b": 0.3, "k:all": 0.12, "q1:all": 0.4, "q2:all": 0.3}
        for j, n in enumerate(nights):
            start.update({f"tc:{j}": float(np.mean(n["t"]) - np.floor(n["t"].min())), f"c0:{j}": 1.0})
        return mn.JointProblem(nights, 2.5, {}, {}, ["all"], start, night_detrend=night_detrend)

    def test_each_night_gets_its_own_terms(self):
        prob = self._prob([(True, True, True), (True, False, False)])
        self.assertIn("c_am:0", prob.free)
        self.assertIn("c_t:0", prob.free)
        self.assertIn("c_t2:0", prob.free)
        # the second night has no airmass in its file: no airmass term whatever is asked
        self.assertEqual([nm for nm in prob.free if nm.endswith(":1")], ["tc:1", "c0:1"])
        self.assertEqual(prob.nights[0]["detrend"], (True, True, True))
        self.assertEqual(prob.nights[1]["detrend"], (False, False, False))

    def test_curve_never_without_its_slope(self):
        prob = self._prob([(False, False, True), (False, True, True)])
        self.assertNotIn("c_t2:0", prob.free)
        self.assertIn("c_t2:1", prob.free)

    def test_baseline_is_the_single_night_formula(self):
        """c0 + c_am·(airmass − mean) + c_t·(t − t_mid) + c_t2·(t − t_mid)²: transit_core.poly_baseline."""
        prob = self._prob([(True, True, True), (False, True, True)])
        x = prob.start.copy()
        x[prob.index["c_am:0"]], x[prob.index["c_t:0"]], x[prob.index["c_t2:0"]] = 0.01, -0.02, 1.7
        d = prob.nights[0]
        want = tc.poly_baseline({"c0": x[prob.index["c0:0"]], "c_am": 0.01, "c_t": -0.02, "c_t2": 1.7}, d["am"],
                                d["t"] - d["t_mid"])
        np.testing.assert_allclose(prob.baseline(x, 0), want, rtol=0, atol=1e-15)
        # at other times too (the plots' fine grid)
        tt = np.linspace(d["t"][0], d["t"][-1], 50)
        want = tc.poly_baseline({"c0": x[prob.index["c0:0"]], "c_am": 0.01, "c_t": -0.02, "c_t2": 1.7},
                                np.interp(tt, d["t"], d["am"]), tt - d["t_mid"])
        np.testing.assert_allclose(prob.baseline(x, 0, tt), want, rtol=0, atol=1e-15)

    def test_bounds_and_scales_as_the_single_night_fit(self):
        prob = self._prob([(False, True, True), (False, True, True)])
        lo, hi = prob.bounds()
        i = prob.index["c_t2:0"]
        self.assertEqual((lo[i], hi[i]), (-20.0, 20.0))
        self.assertEqual(prob.scales()[i], 1e-2)
        x = prob.start.copy()
        self.assertTrue(prob.in_bounds(x))
        x[i] = 20.0
        self.assertFalse(prob.in_bounds(x))

    def test_old_call_is_unchanged(self):
        """Without night_detrend the two flags apply to every night, and no night gets a curve (as before F4)."""
        nights = [night(0), night(1)]
        start = {"a_rs": 8.0, "b": 0.3, "k:all": 0.12, "q1:all": 0.4, "q2:all": 0.3}
        prob = mn.JointProblem(nights, 2.5, {}, {}, ["all"], start, True, True)
        self.assertEqual(prob.free[5:], ["tc:0", "c0:0", "c_am:0", "c_t:0", "tc:1", "c0:1", "c_am:1", "c_t:1"])
        self.assertEqual(len(prob._in[0]), 6)
        self.assertIsNone(prob._in[0][5])


class NightDetrendFlags(unittest.TestCase):
    def test_manual_choice(self):
        with_am, without = night(0), night(1, with_am=False)
        self.assertEqual(mn.night_detrend_flags(with_am, "airmass+curve"), (True, True, True))
        self.assertEqual(mn.night_detrend_flags(with_am, "airmass"), (True, False, False))
        # no airmass in the file: a time slope stands in for the airmass term
        self.assertEqual(mn.night_detrend_flags(without, "airmass"), (False, True, False))
        self.assertEqual(mn.night_detrend_flags(without, "airmass+curve"), (False, True, True))
        self.assertEqual(mn.night_detrend_flags(without, "none"), (False, False, False))
        self.assertEqual(mn.night_detrend_flags(without, "curve"), (False, True, True))


class JointFitAuto(unittest.TestCase):
    """Three nights: a strong time curve, an airmass trend, and a flat night without airmass."""

    @classmethod
    def setUpClass(cls):
        cls.nights = [night(0, curve=-2.5), night(1, am_slope=0.02), night(2, with_am=False)]
        mn.check_nights(cls.nights, "T b", None, True)
        cls.logs = []
        cls.fit = mn.joint_fit(cls.nights, dict(PLANET), mcmc_steps=120, detrend="auto", log=cls.logs.append)

    def test_each_night_keeps_its_own_bic_choice(self):
        got = [n["detrend"] for n in self.fit["nights"]]
        self.assertIn("time2", got[0])             # the curved night gets the time curve
        self.assertEqual(got[1], ["airmass"])      # the airmass night gets airmass alone
        self.assertEqual(got[2], [])               # the flat night without airmass: nothing
        self.assertEqual(self.fit["detrend_mode"], "auto")
        # and it is what the night's own single-night Auto picks
        for n, o in zip(self.nights, self.fit["nights"]):
            am = n["airmass"] if n["airmass"] is not None else np.full(len(n["t"]), np.nan)
            s = tc.lsq_fit(n["t"], n["flux"], n["err"], am, dict(PLANET), detrend_auto=True)
            self.assertEqual(s["detrend"], o["detrend"])
            self.assertEqual(o["detrend_choice"]["key"], s["detrend_choice"]["key"])

    def test_free_parameters(self):
        free = self.fit["free"]
        self.assertIn("c_t2:0", free)
        self.assertIn("c_am:1", free)
        self.assertNotIn("c_t2:1", free)
        self.assertEqual([nm for nm in free if nm.endswith(":2")], ["tc:2", "c0:2"])

    def test_the_curve_is_taken_out(self):
        """The joint shape comes back, and the curved night's Tc is on time (a curve left in would bend both)."""
        d = self.fit["shape"]["all"]["depth"]
        self.assertLess(abs(d[0] - 0.12 ** 2), 4 * d[1] + 3e-4)
        for i, o in enumerate(self.fit["nights"]):
            true = PLANET["t0"] + PLANET["period"] * (100 + 3 * i)
            self.assertLess(abs(o["tc"][0] - true), 5 * o["tc"][1] + 2e-4, o["label"])
        self.assertLess(self.fit["rchi2"], 1.5)

    def test_without_the_curve_the_night_fits_badly(self):
        """The same nights with 'airmass' for all: the curved night's raw χ²ν is far worse than with Auto."""
        fit = mn.joint_fit(self.nights, dict(PLANET), mcmc_steps=60, detrend="airmass")
        self.assertGreater(fit["nights"][0]["rchi2_raw"], 2 * self.fit["nights"][0]["rchi2_raw"])
        # a night without airmass gets a time slope for the airmass term
        self.assertEqual(fit["nights"][2]["detrend"], ["time"])
        self.assertEqual(fit["detrend_mode"], "airmass")

    def test_words(self):
        w = mn.detrend_words(self.fit)
        self.assertTrue(w.startswith("Auto (BIC), each night its own (01-OCT-2026 "))
        self.assertIn("02-OCT-2026 airmass", w)
        self.assertIn("03-OCT-2026 none", w)
        a = mn.timing_analysis(self.fit["nights"], {"t0": PLANET["t0"], "period": 2.5, "t0_err": 1e-4,
                                                    "period_err": 1e-6}, None, 2.5)
        lines = mn.summary_lines(self.fit, a)
        text = "\n".join(t for _l, t in lines)
        self.assertNotIn("⚠ No airmass", text)
        notes = [t for lvl, t in lines if t.startswith("No airmass in the file for")]
        self.assertEqual(len(notes), 1)                            # ONE note
        self.assertEqual([lvl for lvl, t in lines if t.startswith("No airmass")], ["info"])   # and plain
        self.assertIn("03-OCT-2026: its baseline is fitted against time instead", notes[0])
        self.assertIn("02-OCT-2026: baseline airmass (Auto: next ", text)
        self.assertTrue(any("each night alone" in m for m in self.logs))
        self.assertTrue(any(m.strip().startswith("01-OCT-2026: baseline ") for m in self.logs))

    def test_timing_csv_names_the_detrend(self):
        import tempfile
        a = mn.timing_analysis(self.fit["nights"], None, None, 2.5)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.csv")
            mn.write_timing_csv(p, self.fit, a, {"version": "2.4.0"})
            with open(p, encoding="utf-8") as fh:
                head = fh.read()
        self.assertIn("detrending: Auto (BIC), each night its own (01-OCT-2026 ", head)

    def test_corner_labels_the_curve(self):
        res = mn.corner_result(self.fit, "T b")
        self.assertIn("c_t2:0", res["labels"])

    def test_unknown_choice(self):
        with self.assertRaises(ValueError):
            mn.joint_fit(self.nights, dict(PLANET), mcmc_steps=10, detrend="cubic")

    def test_quadratic_is_the_most(self):
        self.assertEqual({c[0] for c in tc.DETREND_CHOICES},
                         {"none", "airmass", "slope", "curve", "airmass+slope", "airmass+curve"})
        self.assertNotIn("c_t3", inspect.getsource(mn))


class LegacyFlagsUnchanged(unittest.TestCase):
    def test_flags_call_fits_as_before(self):
        """joint_fit without detrend= (older callers, tests): the two flags for every night, no curve, and the old
        'detrend' list."""
        nights = [night(0, am_slope=0.02), night(1, am_slope=0.02)]
        mn.check_nights(nights, "T b")
        fit = mn.joint_fit(nights, dict(PLANET), True, False, mcmc_steps=40)
        self.assertEqual(fit["detrend"], ["airmass"])
        self.assertIsNone(fit["detrend_mode"])
        self.assertFalse(any(nm.startswith("c_t") for nm in fit["free"]))
        self.assertEqual(mn.detrend_words(fit), "airmass")


class MixedFilters(unittest.TestCase):
    def test_default_follows_the_filters(self):
        same = [night(0), night(1)]
        mixed = [night(0), night(1, filt="SR")]
        for ns in (same, mixed):
            mn.check_nights(ns, "T b")
        self.assertFalse(mn.per_filter_default(same))
        self.assertTrue(mn.per_filter_default(mixed))
        # a night whose filter is not known (an older CSV) is not "another filter"
        old = [night(0), dict(night(1), filter="")]
        mn.check_nights(old, "T b")
        self.assertFalse(mn.per_filter_default(old))

    def test_status_is_plain(self):
        ns = [night(0), night(1), night(2, filt="SR")]
        mn.check_nights(ns, "T b", None, True, per_filter=True)
        self.assertEqual(ns[2]["status"], ("ok", "✔ ready (filter SR: its own depth and limb darkening)"))
        mn.check_nights(ns, "T b", None, True, per_filter=False)
        self.assertEqual(ns[2]["status"][0], "ok")
        self.assertIn("one depth for all, as chosen", ns[2]["status"][1])
        self.assertNotIn("⚠", "".join(n["status"][1] for n in ns))

    def test_both_remarks_share_one_status(self):
        ns = [night(0), night(1), night(2, filt="SR", with_am=False)]
        mn.check_nights(ns, "T b", None, True, per_filter=True)
        self.assertEqual(ns[2]["status"][1], "✔ ready (filter SR: its own depth and limb darkening; "
                                             + mn.NO_AIRMASS_STATUS + ")")

    def test_note(self):
        ns = [night(0), night(1, filt="SR")]
        mn.check_nights(ns, "T b")
        on, off = mn.mixed_filter_note(ns, True), mn.mixed_filter_note(ns, False)
        self.assertIn("each filter gets its own depth and limb darkening", on)
        self.assertIn("one depth and one limb darkening, as chosen", off)
        self.assertNotIn("⚠", on + off)
        same = [night(0), night(1)]
        mn.check_nights(same, "T b")
        self.assertEqual(mn.mixed_filter_note(same, True), "")

    def test_unknown_filter_joins_the_main_group(self):
        ns = [night(0), night(1), night(2, filt="SR"), dict(night(3), filter="")]
        mn.check_nights(ns, "T b")
        fit = mn.joint_fit(ns, dict(PLANET), per_filter=True, mcmc_steps=30, detrend="none")
        self.assertEqual(fit["groups"], ["CV", "SR"])
        self.assertEqual([n["group"] for n in fit["nights"]], ["CV", "CV", "SR", "CV"])


class _Var:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


class _FakeApp:
    def __init__(self, root):
        self.root = root
        self.logs = []
        self._q = queue.Queue()
        self._cancel = threading.Event()
        self.busy = False
        self.status = _Var("")
        self.status.set = lambda v: None

    def __getattr__(self, name):
        return getattr(self.root, name)

    def call_ui(self, func, *args):
        self._q.put((func, args))

    def log(self, m):
        self.logs.append(m)

    def _start_job(self, label, bar=True):
        return True

    def _end_job(self):
        pass


class Window(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"no display: {exc}")
        self.app = _FakeApp(self.root)
        page = type("P", (), {"current_planet": lambda s: dict(PLANET), "result": None,
                              "detrend_airmass": _Var(True), "detrend_time": _Var(False)})()
        self.w = mn.MultiNightWindow(self.app, page)
        self.w._fill_single_tc = lambda nights: None   # no background fits in these tests

    def tearDown(self):
        try:
            self.w._closed = True
            self.root.destroy()
        except Exception:
            pass

    def test_detrend_box_starts_on_auto(self):
        w = self.w
        self.assertEqual(w.detrend.get(), "Auto (BIC)")
        self.assertEqual(w.detrend_key(), "auto")
        self.assertEqual(list(w.detrend_box.cget("values")),
                         ["Auto (BIC)"] + [lab for _k, lab, _f in tc.DETREND_CHOICES])
        self.assertIn("time curve (quadratic)", w.detrend_box.cget("values"))
        w.set_detrend("airmass+curve")
        self.assertEqual(w.detrend_key(), "airmass+curve")
        self.assertTrue(w.detrend_airmass.get() and w.detrend_time.get())
        w.set_detrend("none")
        self.assertFalse(w.detrend_airmass.get() or w.detrend_time.get())

    def test_no_airmass_night_is_plain(self):
        w = self.w
        w.nights = [night(0), night(1), night(2, with_am=False)]
        w.refresh_nights()
        self.assertEqual(w.prog_text.get(), "3 nights ready.")
        self.assertTrue(w.night_note.get().startswith("No airmass in the file for 03-OCT-2026"))
        self.assertNotIn("⚠", w.night_note.get())
        self.assertFalse(hasattr(w, "time_only_button"))
        self.assertFalse(hasattr(w, "fit_time_only"))
        w.set_detrend("airmass")
        self.assertIn("gets a time slope in place of the airmass term", w.night_note.get())
        w.set_detrend("curve")
        self.assertEqual(w.night_note.get(), "")

    def test_per_filter_ticks_itself_until_set_by_hand(self):
        w = self.w
        w.nights = [night(0), night(1)]
        w.refresh_nights()
        self.assertFalse(w.per_filter.get())
        self.assertIn("disabled", w.per_filter_check.state())
        w.nights.append(night(2, filt="SR"))
        w.refresh_nights()
        self.assertTrue(w.per_filter.get())
        self.assertNotIn("disabled", w.per_filter_check.state())
        self.assertIn("each filter gets its own depth", w.night_note.get())
        # the user unticks it: it stays unticked, and the note says so plainly
        w.per_filter.set(False)
        w._per_filter_clicked()
        self.assertFalse(w.per_filter.get())
        self.assertIn("as chosen", w.night_note.get())
        w.refresh_nights()
        self.assertFalse(w.per_filter.get())
        self.assertNotIn("⚠", w.night_note.get())

    def test_saves_are_greyed_until_the_nights_are_fitted(self):
        w = self.w
        for b in (w.save_csv_button, w.save_png_button):
            self.assertIn("disabled", b.state(), b.cget("text"))
        w.save_csv()                       # a backstop press: a line, no box
        self.assertEqual(w.prog_text.get(), mn.FIT_NIGHTS_FIRST)
        w.fit = {"nights": []}
        w._saves_state()
        for b in (w.save_csv_button, w.save_png_button):
            self.assertNotIn("disabled", b.state(), b.cget("text"))
        w.fit = None

    def test_fit_is_started_with_the_choice(self):
        w = self.w
        w.nights = [night(0), night(1, filt="SR")]
        w.refresh_nights()
        seen = {}

        def fake(use, planet, **kw):
            seen.update(kw)
            raise RuntimeError("Canceled")
        orig, mn.joint_fit = mn.joint_fit, fake
        try:
            w.app._discard_job_findings = lambda: None
            w.run_fit()
            for _ in range(200):
                self.root.update()
                try:
                    func, args = self.app._q.get_nowait()
                except queue.Empty:
                    if seen:
                        continue
                    import time
                    time.sleep(0.01)
                    continue
                func(*args)
                if not w._running:
                    break
        finally:
            mn.joint_fit = orig
        self.assertEqual(seen.get("detrend"), "auto")
        self.assertTrue(seen.get("per_filter"))


if __name__ == "__main__":
    unittest.main()
