"""2.4.0 step 8 (#90): Time of max/min. The error always uses the best period SHOBS-P has (series, times CSV, VSX,
Period box), apart from the Period/Epoch boxes, which only predict the time and give the O−C; the "no period given"
note is gone; saved rows record the period the error used (noise_period_d, noise_period_source)."""
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import photometry_core as core  # noqa: E402

try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False

P_TRUE = 0.149
T_REF = 2461358.70


def night(seed=1, hours=6.0, period=P_TRUE, cadence_s=60.0, red=0.008, white=0.006, start=2461358.60):
    """A HADS-like night (fast rise, slow fall) with white noise and red noise lasting ~8 minutes."""
    rng = np.random.default_rng(seed)
    t = start + np.arange(0, hours / 24.0, cadence_s / 86400.0)
    ph = 2 * np.pi * (t - T_REF) / period
    m = 12 - 0.20 * np.cos(ph) - 0.06 * np.cos(2 * ph + 0.6) - 0.02 * np.cos(3 * ph + 1.0)
    r = np.zeros(t.size)
    a = math.exp(-1 / 8.0)
    for i in range(1, t.size):
        r[i] = a * r[i - 1] + rng.normal(0, red * math.sqrt(1 - a * a))
    return t, m + r + rng.normal(0, white, t.size)


class NoisePeriodChoice(unittest.TestCase):
    ROWS = [
        {"star": "X", "type": "max", "hjd": 2461353.8, "period_d": 0.1166},
        {"star": "X", "type": "min", "hjd": 2461354.9, "period_d": 0.11664},
        {"star": "X", "type": "max", "hjd": 2461355.9, "period_d": float("nan")},
    ]

    def test_order(self):
        f = core.extremum_noise_period
        self.assertEqual(f(series=0.14906, csv_rows=self.ROWS, vsx=0.15151, typed=0.2), (0.14906, "series"))
        self.assertEqual(f(series=None, csv_rows=self.ROWS, vsx=0.15151, typed=0.2), (0.11664, "times CSV"))
        self.assertEqual(f(series=None, csv_rows=[], vsx=0.15151, typed=0.2), (0.15151, "VSX"))
        self.assertEqual(f(series=None, csv_rows=None, vsx=None, typed=0.2), (0.2, "Period box"))
        self.assertEqual(f(), (None, "none"))

    def test_bad_values_are_skipped(self):
        f = core.extremum_noise_period
        self.assertEqual(f(series=float("nan"), vsx=0.0, typed=-1.0), (None, "none"))
        self.assertEqual(f(series="x", csv_rows=[{"hjd": 1.0, "period_d": ""}], vsx=0.3), (0.3, "VSX"))

    def test_csv_period_is_the_latest_of_either_type(self):
        # the latest saved time WITH a period, whatever its type (the elements' epoch is per type; the period is not)
        self.assertEqual(core.extremum_noise_period(csv_rows=list(reversed(self.ROWS))), (0.11664, "times CSV"))


class MeasureWithNoisePeriod(unittest.TestCase):
    def test_no_period_note_is_gone_and_result_says_none(self):
        t, m = night()
        r = core.measure_extremum(t, m, "max", n_boot=100)
        self.assertFalse(any("no period given" in n for n in r["notes"]), r["notes"])
        self.assertIsNone(r["noise_period"])
        self.assertIsNone(r["fourier"])

    def test_noise_period_does_not_move_the_time(self):
        """The issue: 'The time doesn't move; the error bar is what was wrong.'"""
        t, m = night()
        none = core.measure_extremum(t, m, "max", n_boot=100)
        with_p = core.measure_extremum(t, m, "max", period=P_TRUE, window_period=None, n_boot=100)
        self.assertAlmostEqual(none["t"], with_p["t"], places=9)
        self.assertEqual(with_p["noise_period"], P_TRUE)
        self.assertGreater(with_p["err"], 1.5 * none["err"])     # the red noise is now counted
        self.assertIsNotNone(with_p["fourier"])

    def test_default_window_period_is_the_period_as_before(self):
        t, m = night()
        a = core.measure_extremum(t, m, "max", period=P_TRUE, n_boot=60)
        b = core.measure_extremum(t, m, "max", period=P_TRUE, window_period=P_TRUE, n_boot=60)
        self.assertEqual(a["t"], b["t"])
        self.assertEqual(a["err"], b["err"])

    def test_approximate_period_is_enough_when_tuned(self):
        """A period 5% off made the error several times too large (the Fourier series cannot follow the star);
        tuned to the night it gives about the error of the true period."""
        ratios_raw, ratios_tuned = [], []
        for seed in (1, 2, 3):
            t, m = night(seed, hours=8.0)   # review S1: tuned only on a night of at least two cycles
            true = core.measure_extremum(t, m, "max", period=P_TRUE, window_period=None, n_boot=120)
            off = core.measure_extremum(t, m, "max", period=0.95 * P_TRUE, window_period=None, n_boot=120)
            tuned = core.measure_extremum(t, m, "max", period=0.95 * P_TRUE, window_period=None, n_boot=120,
                                          tune_period=True)
            self.assertEqual(tuned["noise_period"], 0.95 * P_TRUE)             # the period asked for
            self.assertLess(abs(tuned["noise_period_tuned"] - P_TRUE), 0.015 * P_TRUE)
            self.assertAlmostEqual(tuned["t"], true["t"], places=9)
            ratios_raw.append(off["err"] / true["err"])
            ratios_tuned.append(tuned["err"] / true["err"])
        self.assertGreater(np.median(ratios_raw), 2.0)
        self.assertLess(np.median(ratios_tuned), 1.4)
        self.assertGreater(np.median(ratios_tuned), 0.7)

    def test_tune_leaves_a_short_night_alone(self):
        t, m = night(hours=3.0)     # under one cycle: the night cannot tell periods apart
        self.assertEqual(core.tune_noise_period(t, m, 0.16), 0.16)
        self.assertEqual(core.tune_noise_period(t[:8], m[:8], 0.01), 0.01)
        t, m = night(hours=7.0)     # review S1: 1.96 cycles is still too short to tune on
        self.assertEqual(core.tune_noise_period(t, m, 1.02 * P_TRUE), 1.02 * P_TRUE)
        t, m = night(hours=8.0)
        self.assertLess(abs(core.tune_noise_period(t, m, 1.02 * P_TRUE) - P_TRUE), 0.01 * P_TRUE)
        # one-day alias of the true period: still found (within the 20% reach)
        self.assertLess(abs(core.tune_noise_period(t, m, 1 / (1 / P_TRUE + 1)) - P_TRUE), 0.015 * P_TRUE)


class CsvColumns(unittest.TestCase):
    OLD_HEAD = ("star,type,hjd,bjd_tdb,jd_utc,error_d,error_min,method,cycle,o_c_d,o_c_min,period_d,epoch,elements,"
                "night,n_points,window_min,kvw_jd,kvw_err,poly_jd,poly_err,fourier_jd,fourier_err,time_note,note")

    def test_new_columns_are_at_the_end(self):
        self.assertEqual(core.EXTREMUM_COLUMNS[-3:], ("noise_period_d", "noise_period_source", "noise_period_used_d"))
        self.assertEqual(",".join(core.EXTREMUM_COLUMNS[:-3]), self.OLD_HEAD)

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.csv")
            core.save_extremum_row(path, {"star": "X", "type": "max", "hjd": 2461358.75799, "error_min": 3.8,
                                          "noise_period_d": 0.14906, "noise_period_source": "series"})
            row = core.read_extremum_csv(path)[0]
            self.assertAlmostEqual(row["noise_period_d"], 0.14906)
            self.assertEqual(row["noise_period_source"], "series")
            self.assertFalse(core.extremum_row_no_period(row))

    def test_old_csv_reads_and_appends(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.csv")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# Created 2026-11-14 by SHOBS-P 2.3.5\n" + self.OLD_HEAD + ",my_column\n"
                         "X,max,2461353.81255,2461353.81300,2461353.81000,0.00264,3.80,polynomial deg 4 + bootstrap,"
                         "0,0.00000,0.00,0.14906000,2461353.81255,saved HJD,JD2461353,80,35.00,,,2461353.81000,0.00264,"
                         ",,,,mine\n"
                         "X,max,2461358.75799,,2461358.75500,0.00097,1.40,polynomial deg 4 + bootstrap,,,,,,,"
                         "JD2461358,80,35.00,,,2461358.75500,0.00097,,,,,\n")
            old = core.read_extremum_csv(path)
            self.assertEqual(len(old), 2)
            self.assertNotIn("noise_period_d", old[0])
            # an old row with elements was timed with their period; one without was timed with none
            self.assertFalse(core.extremum_row_no_period(old[0]))
            self.assertTrue(core.extremum_row_no_period(old[1]))
            status = core.save_extremum_row(path, {"star": "X", "type": "max", "hjd": 2461359.9, "error_min": 3.0,
                                                   "noise_period_d": float("nan"), "noise_period_source": "none"})
            self.assertEqual(status, "added")
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertTrue(text.startswith("# Created 2026-11-14 by SHOBS-P 2.3.5\n"))
            head = [ln for ln in text.splitlines() if not ln.startswith("#")][0]
            self.assertEqual(head, ",".join(core.EXTREMUM_COLUMNS) + ",my_column")   # the hand-added column is kept
            rows = core.read_extremum_csv(path)
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["my_column"], "mine")
            self.assertAlmostEqual(rows[0]["period_d"], 0.14906)
            self.assertTrue(math.isnan(rows[0]["noise_period_d"]))      # old rows: blank, not invented
            self.assertEqual(rows[0]["noise_period_source"], "")
            self.assertEqual(rows[2]["noise_period_source"], "none")
            self.assertTrue(core.extremum_row_no_period(rows[2]))
            # the elements the window starts from still come out of it
            got = core.extremum_csv_elements(rows, "max")
            self.assertAlmostEqual(got["period"], 0.14906)

    def test_list_marks_a_time_measured_without_a_period(self):
        rows = [{"star": "X", "type": "max", "hjd": 2461353.81255, "error_min": 3.8, "period_d": 0.14906},
                {"star": "X", "type": "max", "hjd": 2461358.75799, "error_min": 1.4, "period_d": float("nan")},
                {"star": "X", "type": "max", "hjd": 2461359.9, "error_min": 3.0, "period_d": float("nan"),
                 "noise_period_d": 0.14906, "noise_period_source": "series"}]
        lines = core.extremum_times_lines(rows)
        self.assertNotIn("timed without a period", lines[0])
        self.assertIn("(timed without a period: ± may be 2-3× too small; measure again)", lines[1])
        self.assertNotIn("timed without a period", lines[2])   # no elements, but the error had the series period


@unittest.skipUnless(HAVE_TK, "needs a display")
class WindowUsesTheBestPeriod(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        ca.ExtremumWindow.SYNC = True   # fit-in F1 (#116): Measure in line for these tests
        cls.ca = ca
        cls.tmp = tempfile.TemporaryDirectory()
        cls.p = mock.patch.object(ca, "SETTINGS_FILE", os.path.join(cls.tmp.name, "s.json"))
        cls.p.start()
        ca.LOG_FILE = os.path.join(cls.tmp.name, "test_240_step8_tmax.log")
        cls.app = ca.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.destroy()
        except Exception:
            pass
        cls.p.stop()
        cls.tmp.cleanup()

    def _open(self, d, star="S8 Test", series=None, vsx=None, vsx_epoch=None, rows=(), hours=6.0, nights=1):
        app = self.app
        obs = []
        for n in range(nights):
            t, m = night(seed=5 + n, hours=hours, start=2461358.60 + n)
            obs += [core.Obs(jd=float(a), mag=float(b), merr=0.01, cmag=9.0, kmag=10.5, airmass=1.1,
                             path=f"n{n}f{k:04d}.fit", exptime=30.0, flux_var=1.0, flux_comp=1.0, night=f"N{n + 1}",
                             filt="CV") for k, (a, b) in enumerate(zip(t, m))]
        app.observations = obs
        app.mode_key = "variables"
        app.star_id.set(star)
        app.series_path = ""
        app.catalog_period, app.catalog_epoch, app.catalog_var_type = vsx, vsx_epoch, "DSCT"
        app.period = series
        path = os.path.join(d, core.extremum_csv_name(star))
        for r in rows:
            core.save_extremum_row(path, dict(r))
        app.extremum_csv, app.extremum_csv_star = path, star
        return self.ca.ExtremumWindow(app), path

    def _close(self, w):
        w.win.destroy()
        self.app.star_id.set("")
        self.app.observations = []
        self.app.period = None
        self.app.catalog_period = self.app.catalog_epoch = None
        self.app.extremum_csv = ""

    GOOD = {"period_days": 0.14906, "fap": 1e-30, "carried_by": [], "night_fap": {"fap": 1e-6},
            "airmass_check": {"demoted": False}}

    def test_label_and_hint(self):
        with tempfile.TemporaryDirectory() as d:
            w, _ = self._open(d)
            try:
                texts = []

                def walk(widget):
                    for c in widget.winfo_children():
                        try:
                            texts.append(str(c.cget("text")))
                        except Exception:
                            pass
                        walk(c)
                walk(w.win)
                self.assertIn("Elements for the predicted time and O−C (optional)", texts)
                hint = w.hint.cget("text")
                self.assertIn("The error finds its period itself", hint)
                self.assertIn("The elements below are optional", hint)
            finally:
                self._close(w)

    def test_series_period_with_empty_boxes(self):
        """John's 14 Nov case: Period and Epoch cleared; the error still has the series period."""
        with tempfile.TemporaryDirectory() as d:
            w, path = self._open(d, series=dict(self.GOOD), vsx=0.15151, vsx_epoch=2452000.5, nights=2)
            try:
                w.period.set("")
                w.epoch.set("")
                with mock.patch.object(self.ca.messagebox, "showinfo"), \
                        mock.patch.object(self.ca.messagebox, "showerror"):
                    w.measure()
                self.assertEqual(len(w.results), 1)
                r = w.results[0]
                self.assertEqual((r["noise_period"], r["noise_source"]), (0.14906, "series"))
                text = w.result_text.get()
                self.assertIn("error: noise model at P 0.14906 d (series period)", text)
                self.assertNotIn("no period", text)
                self.assertNotIn("may be 2-3 times too small", text)
                self.assertIn("Fourier (", text)                      # the check runs without elements too
                self.assertNotIn("O−C ", text.split("No elements")[0])   # no elements: no O−C
                self.assertIn("No elements: type the period and epoch (or Elements from VSX) for the O−C (optional); "
                              "the error does not need them.", text)
                w.save()
                row = core.read_extremum_csv(path)[0]
                self.assertAlmostEqual(row["noise_period_d"], 0.14906)
                self.assertEqual(row["noise_period_source"], "series")
                self.assertTrue(math.isnan(row["period_d"]))          # the elements columns stay the elements
                self.assertEqual(row["elements"], "")
                self.assertNotIn("timed without a period", w.csv_text.get())
            finally:
                self._close(w)

    def test_boxes_do_not_change_the_error(self):
        """'If it doesn't matter whether something is filled in or not, why have it?': the elements now change the
        O−C only, never the error."""
        with tempfile.TemporaryDirectory() as d:
            w, _ = self._open(d, series=dict(self.GOOD), nights=2)
            try:
                w.night.set(next(k for k, v in w.night_names.items() if v == "N1"))
                w.period.set("")
                w.epoch.set("")
                w.measure()
                blank = w.results[0]
                # elements that put the predicted time on this maximum
                w.period.set("0.15151")
                w.epoch.set(f"{blank['t']:.5f}")
                w.measure()
                # (the elements may predict a second maximum in the night; take the same one)
                filled = min(w.results, key=lambda r: abs(r["t"] - blank["t"]))
                self.assertEqual(filled["noise_period"], 0.14906)
                self.assertAlmostEqual(filled["t"], blank["t"], delta=0.5 / 1440.0)
                self.assertAlmostEqual(filled["err"], blank["err"], delta=0.35 * blank["err"])
                self.assertIsNotNone(filled["oc"])
                self.assertIn("error: noise model at P 0.14906 d (series period)", w.result_text.get())
            finally:
                self._close(w)

    def test_unreliable_series_period_falls_to_csv_then_vsx(self):
        bad = dict(self.GOOD, period_days=1.0027, fap=0.3)          # a best peak the Output page calls not significant
        rows = [{"star": "S8 Test", "type": "min", "hjd": 2461350.5, "period_d": 0.1489, "elements": "typed HJD"}]
        with tempfile.TemporaryDirectory() as d:
            w, _ = self._open(d, series=bad, vsx=0.15151, rows=rows)
            try:
                self.assertEqual(w._series_period()[0], None)
                self.assertEqual(w._noise_period()[:2], (0.1489, "times CSV"))
                w.measure()
                self.assertIn("error: noise model at P 0.14890 d (times CSV)", w.result_text.get())
            finally:
                self._close(w)
        with tempfile.TemporaryDirectory() as d:
            w, _ = self._open(d, series=dict(self.GOOD, carried_by=["N1"]), vsx=0.15151)
            try:
                self.assertEqual(w._noise_period()[:2], (0.15151, "VSX"))
            finally:
                self._close(w)
        with tempfile.TemporaryDirectory() as d:     # one night under two cycles of the peak: "one short night"
            w, _ = self._open(d, series=dict(self.GOOD, period_days=0.2), hours=6.0)
            try:
                p, why = w._series_period()
                self.assertIsNone(p)
                self.assertIn("is not a reliable period: one short night", why)
            finally:
                self._close(w)

    def test_vsx_period_without_epoch(self):
        """AA LMi, 16 Nov: VSX gives a period but no epoch; the period alone drives the error."""
        with tempfile.TemporaryDirectory() as d:
            w, path = self._open(d, vsx=0.15, vsx_epoch=None)
            try:
                self.assertEqual(w.period.get(), "0.15000000")
                self.assertEqual(w.epoch.get(), "")
                self.assertEqual(w.source.get(), "VSX gives a period but no epoch")
                w.measure()
                text = w.result_text.get()
                self.assertIn("error: noise model at P 0.15000 d (VSX)", text)
                self.assertIn("Period without an epoch: no predicted time or O−C.", text)
                self.assertNotIn("may be 2-3 times too small", text)
                w.save()
                row = core.read_extremum_csv(path)[0]
                self.assertEqual(row["noise_period_source"], "VSX")
                # the lookup's own wording, each case
                w._lookup_done({"period_days": 0.054306, "epoch_jd": None, "var_type": "DSCT"}, None)
                self.assertEqual(w.source.get(), "VSX DSCT: VSX gives a period but no epoch (type an epoch for the "
                                                 "O−C; the error uses the period)")
                self.assertNotIn("no period/epoch there", w.source.get())
                w._lookup_done({"period_days": None, "epoch_jd": None, "var_type": "DSCT"}, None)
                self.assertIn("VSX gives no period or epoch", w.source.get())
                w._lookup_done({"period_days": 0.054306, "epoch_jd": 2455000.5, "var_type": "DSCT"}, None)
                self.assertEqual(w.source.get(), "VSX DSCT")
            finally:
                self._close(w)

    def test_period_box_is_the_last_resort(self):
        with tempfile.TemporaryDirectory() as d:
            w, _ = self._open(d)
            try:
                w.period.set("0.149")      # typed, no epoch, nothing else anywhere
                self.assertEqual(w._noise_period()[:2], (0.149, "Period box"))
                w.measure()
                self.assertIn("error: noise model at P 0.14900 d (Period box)", w.result_text.get())
                # with VSX's period known, a typed one does not outrank it
                self.app.catalog_period = 0.15151
                self.assertEqual(w._noise_period()[:2], (0.15151, "VSX"))
            finally:
                self._close(w)

    def test_no_period_anywhere_is_said_plainly(self):
        with tempfile.TemporaryDirectory() as d:
            w, path = self._open(d, series=dict(self.GOOD, period_days=0.2))    # one short night: not reliable
            try:
                w.period.set("")
                w.epoch.set("")
                logged = []
                with mock.patch.object(self.app, "log", side_effect=logged.append):
                    w.measure()
                text = w.result_text.get()
                self.assertIn("⚠ error: SHOBS-P has no period for this star", text)
                self.assertIn("may be 2-3 times too small (the Output page's best peak, 0.2 d, is not a reliable "
                              "period: one short night).", text)
                self.assertIn("Run the period search on the Output page first", text)
                self.assertNotIn("no period given", text)
                self.assertFalse(any("period" in str(x) for x in logged))     # told on screen, not in the Log
                self.assertIsNone(w.results[0]["noise_period"])
                w.save()
                row = core.read_extremum_csv(path)[0]
                self.assertEqual(row["noise_period_source"], "none")
                self.assertTrue(math.isnan(row["noise_period_d"]))
                self.assertIn("timed without a period", w.csv_text.get())
            finally:
                self._close(w)


if __name__ == "__main__":
    unittest.main()
