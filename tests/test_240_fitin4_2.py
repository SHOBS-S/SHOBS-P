"""2.4.0 fit-in F4, package 2: Variables Output, series, Time of max.

8c         the period search follows the known period: core.period_range_follow, the Period box's plain Range row,
           no "cannot find" caution, a plain Edge row; a field scan's known variable is searched over its own range.
threads    Load series reads the file and searches the period on a worker thread (#100 item 4, #116); the period
           search is split into prepare / job / apply.
nits       FAP underflow; Epoch with a JD label; each problem once; "looks constant" while a night changes; times
           CSV n_points; Store O−C with a backup; AAVSO NOTES comp names; Close series puts the own site back; Show
           log opens a Log window; one count of picked stars; m456 / m482 night chooser; m070 / m448; a stacked
           night's frame count; Blink wording; scan file names in Discovery; Plot bins; a mode-switch rerun keeps
           the answers; the Planet box survives a mode switch."""
import csv
import glob
import inspect
import math
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import photometry_core as core  # noqa: E402
import transit_core as tc  # noqa: E402

try:
    import tkinter  # noqa: F401
    tkinter.Tk().destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False

if HAVE_TK:
    import clearv_app as ca  # noqa: E402
    import test_240_m as M  # noqa: E402
    Base = M.AppCase
else:
    Base = unittest.TestCase


CAT = {"TYC 4006-1008-1": 8.728, "Gaia DR3 439074923286442112": 11.31}
COMPS = list(CAT)


def _series(period=0.25, nights=3, per_night=60, amp=0.10, noise=0.004, seed=1, span_h=6.0):
    """A sinusoidal variable: `nights` nights in a row, per_night points over span_h hours each."""
    rng = np.random.default_rng(seed)
    out = []
    for ni in range(nights):
        night = f"JD{2460950 + ni}"
        for i in range(per_night):
            jd = 2460950.55 + ni + i * span_h / 24.0 / per_night
            mag = 10.0 + amp * math.sin(2 * math.pi * jd / period) + rng.normal(0, noise)
            out.append(core.Obs(jd=jd, mag=mag, merr=noise, cmag=9.0, kmag=10.5, airmass=1.2,
                                path=f"L_{ni}_{i:03d}.fit", exptime=30.0, flux_var=1.0, flux_comp=1.0, night=night,
                                filt="CV"))
    return out


# ---- 8c: the range follows the known period (pure) -----------------------------------------------------------------
class TestRangeFollow(unittest.TestCase):
    def test_outside_long_end_is_covered(self):
        got = core.period_range_follow(0.02, 20.0, [("VSX", 22.1)], 100.0)
        self.assertEqual(got["widened"], [("VSX", 22.1)])
        self.assertEqual(got["lo"], 0.02)
        self.assertGreaterEqual(got["hi"], 1.25 * 22.1 * 0.99)
        self.assertLess(22.1, 0.9 * got["hi"])            # no longer "at the edge" for lomb_scargle

    def test_at_the_edge_counts(self):
        got = core.period_range_follow(0.02, 20.0, [("VSX", 19.0)], 100.0)   # inside, but >= 0.9 x max
        self.assertTrue(got["widened"])
        self.assertLess(19.0, 0.9 * got["hi"])
        got = core.period_range_follow(0.05, 20.0, [("VSX", 0.039)], 3.0)   # below the minimum
        self.assertLess(got["lo"], 0.039 / 1.05)
        self.assertEqual(got["hi"], 20.0)

    def test_comfortably_inside_changes_nothing(self):
        got = core.period_range_follow(0.02, 20.0, [("VSX", 0.31), ("Marked", 2.0)], 30.0)
        self.assertEqual((got["lo"], got["hi"], got["widened"], got["too_long"]), (0.02, 20.0, [], []))

    def test_longer_than_half_the_span_is_left_alone(self):
        got = core.period_range_follow(0.02, 20.0, [("VSX", 332.0)], 3.0)
        self.assertEqual((got["lo"], got["hi"], got["widened"]), (0.02, 20.0, []))
        self.assertEqual(got["too_long"], [("VSX", 332.0)])
        # inside the range but untestable: nothing to say at all
        got = core.period_range_follow(0.02, 20.0, [("VSX", 15.0)], 3.0)
        self.assertEqual((got["widened"], got["too_long"]), ([], []))

    def test_several_known_periods_and_bad_values(self):
        got = core.period_range_follow(0.05, 1.0, [("VSX", 0.03), ("the saved times'", 1.2), ("Marked", "x"),
                                                    ("Marked", -1), ("Marked", None)], 10.0)
        self.assertLess(got["lo"], 0.03)
        self.assertGreater(got["hi"], 1.2)
        self.assertEqual(len(got["widened"]), 2)

    def test_note_is_plain(self):
        note = core.period_range_note((0.02, 20.0), (0.02, 28.0), [("VSX", 22.1)])
        self.assertEqual(note, "searched 0.02–28 d: widened from 0.02–20 d to cover VSX 22.1 d")
        self.assertNotIn("⚠", note)

    def test_edge_text_is_a_plain_note_that_says_what_to_do(self):
        for edge, word in (("max", "raise the max period"), ("min", "lower the min period")):
            text = core.period_edge_text({"edge": edge, "period_days": 19.5, "min_period": 0.02, "max_period": 20.0})
            self.assertNotIn("⚠", text)
            self.assertIn(word, text.lower())
            self.assertIn("Refit period", text)
            self.assertIn("no known period", text)
        self.assertEqual(core.period_edge_text({"edge": None}), "")


# ---- the small pure nits -------------------------------------------------------------------------------------------
class TestPureNits(unittest.TestCase):
    def test_fap_text_never_zero(self):
        self.assertEqual(core.fap_text(0.0), "< 1e-99")
        self.assertEqual(core.fap_text(1e-150), "< 1e-99")
        self.assertEqual(core.fap_text(3.2e-5), "3.2e-05")
        self.assertEqual(core.fap_text(float("nan")), "")
        self.assertEqual(core.fap_text(None), "")
        self.assertNotIn("0.0e+00", core.fap_text(4e-320))

    def test_fap_text_names_the_permutation_floor(self):
        floor = 1.0 / math.factorial(6)
        self.assertEqual(core.fap_text(floor, floor, 6), f"{floor:.1e} (the lowest 6 nights can show)")
        self.assertEqual(core.fap_text(0.2, floor, 6), "2.0e-01")

    def test_night_fap_reports_its_floor(self):
        pts = _series(period=3.3, nights=6, per_night=12, span_h=2.0)
        jd = np.array([o.jd for o in pts])
        y = np.array([o.mag for o in pts])
        got = core.period_night_fap(jd, y, [o.night for o in pts], {"period_days": 3.3}, 0.02, 20.0)
        self.assertEqual(got["method"], "nightly means")
        self.assertAlmostEqual(got["floor"], 1.0 / 720.0)
        self.assertGreaterEqual(got["fap"], got["floor"] - 1e-12)

    def test_parse_epoch_takes_a_label(self):
        for text, want in (("2461353.81255", 2461353.81255), ("JD 2461353.81255", 2461353.81255),
                           ("HJD=2461353.8", 2461353.8), ("BJD_TDB: 2461353.8", 2461353.8),
                           ("jd2461353.8", 2461353.8), ("2461353,5", 2461353.5), (" 2 461 353.5 ", 2461353.5)):
            self.assertAlmostEqual(core.parse_epoch(text), want, places=6, msg=text)
        for text in ("", "JD", "abc", "2461353.8 x", None, "JD 24x"):
            self.assertIsNone(core.parse_epoch(text), text)

    def test_in_night_changes(self):
        rng = np.random.default_rng(5)
        t = 2460950.6 + np.arange(120) * 0.002
        flat = 10.0 + rng.normal(0, 0.03, t.size)
        self.assertEqual(core.in_night_changes(t, flat, ["N1"] * t.size), [])
        moving = flat + 0.04 * np.sin(2 * math.pi * (t - t[0]) / 0.35)      # 0.08 mag over the night
        got = core.in_night_changes(t, moving, ["N1"] * t.size)
        self.assertEqual([g["night"] for g in got], ["N1"])
        self.assertGreater(got[0]["range"], 0.04)
        self.assertLess(got[0]["noise"], 0.05)
        # too few points: not judged
        self.assertEqual(core.in_night_changes(t[:10], moving[:10], ["N1"] * 10), [])

    def test_aavso_notes_keep_each_comp_name(self):
        pts = _series(nights=1, per_night=3)
        long_name = "APASS J024816.7+504250"
        comps = [core.Star("TYC 3308-208-1", 1, 1, 10.1), core.Star("000-BBC-123", 2, 2, 10.5),
                 core.Star(long_name, 3, 3, 11.0)]
        text = core.aavso_report(pts, "JABA", "GSC 03308-00462", comps, None, "X1234")
        self.assertIn(long_name, text)
        self.assertIn(f"ens TYC 3308-208-1 000-BBC-123 {long_name}", text)


class TestTimesCsv(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "X_times_of_extremum.csv")

    def _row(self, t, star="AA LMi", kind="max", **over):
        row = {"star": star, "type": kind, "hjd": t, "bjd_tdb": t + 0.0008, "jd_utc": t - 0.002, "error_d": 0.0005,
               "error_min": 0.72, "method": "polynomial", "cycle": 0.0, "o_c_d": 0.001, "o_c_min": 1.44,
               "period_d": 0.0543, "epoch": 2461300.5, "elements": "VSX HJD", "night": "JD2461353", "n_points": 16,
               "window_min": 20.0, "note": ""}
        row.update(over)
        return row

    def _cells(self, name):
        with open(self.path, encoding="utf-8") as fh:
            rows = list(csv.reader(ln for ln in fh if not ln.startswith("#")))
        k = rows[0].index(name)
        return [r[k] for r in rows[1:]]

    def test_n_points_stays_a_count(self):
        core.save_extremum_row(self.path, self._row(2461353.8))
        core.save_extremum_row(self.path, self._row(2461354.8))    # the first row is read back and rewritten
        core.save_extremum_row(self.path, self._row(2461355.8))
        self.assertEqual(self._cells("n_points"), ["16", "16", "16"])

    def test_store_oc_rewrites_the_rows_with_a_backup(self):
        core.save_extremum_row(self.path, self._row(2461353.8))
        core.save_extremum_row(self.path, self._row(2461354.9, note="a note; kept"))
        core.save_extremum_row(self.path, self._row(2461355.0, star="Other Star"))
        before = open(self.path, encoding="utf-8").read()
        got = core.rewrite_extremum_oc(self.path, "AA LMi", 0.11, 2461353.8, "HJD", "typed")
        self.assertEqual(got["rows"], 2)
        self.assertTrue(os.path.exists(got["backup"]))
        self.assertIn("_before_oc_", os.path.basename(got["backup"]))
        self.assertEqual(open(got["backup"], encoding="utf-8").read(), before)
        rows = core.read_extremum_csv(self.path)
        mine = [r for r in rows if r["star"] == "AA LMi"]
        other = [r for r in rows if r["star"] == "Other Star"][0]
        self.assertEqual([r["cycle"] for r in mine], [0.0, 10.0])
        self.assertAlmostEqual(mine[0]["o_c_min"], 0.0, places=2)
        self.assertAlmostEqual(mine[1]["o_c_d"], 2461354.9 - (2461353.8 + 10 * 0.11), places=5)
        self.assertEqual({r["elements"] for r in mine}, {"typed HJD"})
        self.assertEqual({r["period_d"] for r in mine}, {0.11})
        self.assertEqual(mine[1]["note"], "a note; kept")
        self.assertEqual(other["elements"], "VSX HJD")           # another star's rows are not touched
        self.assertAlmostEqual(other["period_d"], 0.0543)
        self.assertAlmostEqual(mine[0]["hjd"], 2461353.8)          # the times themselves never move

    def test_store_oc_refuses_without_rows_or_elements(self):
        core.save_extremum_row(self.path, self._row(2461353.8))
        with self.assertRaises(ValueError):
            core.rewrite_extremum_oc(self.path, "Nobody", 0.11, 2461353.8)
        with self.assertRaises(ValueError):
            core.rewrite_extremum_oc(self.path, "AA LMi", 0.0, 2461353.8)
        self.assertEqual(glob.glob(os.path.join(self.dir, "*_before_oc_*")), [])   # nothing copied for nothing


class TestArchiveStatus(unittest.TestCase):
    def test_no_answer_and_unknown_are_told_apart(self):
        with mock.patch.object(tc, "_http_get", side_effect=OSError("timed out")):
            self.assertIsNone(tc.nea_resolve("Nowhere 7"))
        self.assertEqual(tc.nea_last_status("nowhere  7"), "no answer")
        with mock.patch.object(tc, "_http_get", return_value=b'{"manifest": {"lookup_status": "NOT FOUND"}}'):
            self.assertIsNone(tc.nea_resolve("Nowhere 8"))
        self.assertEqual(tc.nea_last_status("Nowhere 8"), "unknown")
        self.assertEqual(tc.nea_last_status("never asked"), "")


# ---- in the app ----------------------------------------------------------------------------------------------------
@unittest.skipUnless(HAVE_TK, "needs a display")
class TestPeriodFollowsInApp(Base):
    def _open(self, pts, lo=0.02, hi=20.0, vsx=None, marks=""):
        app = self.app
        app.mode_key = "variables"
        app.observations = pts
        app.min_period.set(lo)
        app.max_period.set(hi)
        app.catalog_period = vsx
        app.mark_periods.set(marks)
        app._period_range_widened = None
        app._refresh_period()
        got = {}

        def grab(series_lines, period_lines, color_lines, rows, cautions):
            got["period"], got["cautions"] = list(period_lines), list(cautions)
        with mock.patch.object(app, "_fill_summary", side_effect=grab):
            app.draw_output()
        return got

    def test_vsx_period_outside_the_boxes_is_searched(self):
        app = self.app
        got = self._open(_series(period=0.25), lo=0.02, hi=0.1, vsx=0.25)
        self.assertGreater(float(app.max_period.get()), 0.25 / 0.9)          # the boxes show what was searched
        self.assertAlmostEqual(app.period["period_days"], 0.25, delta=0.01)   # RESULTS-CHANGING: it is found now
        rows = [ln for ln in got["period"] if ln.startswith("Range   ")]
        self.assertEqual(len(rows), 1, got["period"])
        self.assertIn("widened from 0.02–0.1 d to cover VSX 0.25 d", rows[0])
        self.assertFalse(any("cannot find" in c or "outside the search" in c for c in got["cautions"]))
        self.assertFalse(any("⚠" in ln for ln in got["period"] if ln.startswith(("Range", "Edge"))))
        self.assertIsNone(app._period_range_fix)                              # no "Search … instead" button
        self.assertTrue(any(ln.startswith("Period search ") and "widened from 0.02–0.1 d to cover VSX 0.25 d" in ln
                            for ln in self.lines), self.lines)
        self.assertEqual(app._period_range_used, (float(app.min_period.get()), float(app.max_period.get())))

    def test_the_row_stays_while_the_boxes_are_the_widened_ones_and_logs_once(self):
        app = self.app
        self._open(_series(period=0.25), lo=0.02, hi=0.1, vsx=0.25)
        n = sum("widened from" in ln for ln in self.lines)
        app._refresh_period()                                                 # a refit: nothing left to widen
        self.assertEqual(sum("widened from" in ln for ln in self.lines), n)
        self.assertIn("widened from", app._period_range_note)
        app.max_period.set(5.0)                                               # the user's own, still covering it
        app._refresh_period()
        self.assertEqual(app._period_range_note, "")

    def test_range_inside_is_left_alone(self):
        app = self.app
        got = self._open(_series(period=0.25), lo=0.02, hi=20.0, vsx=0.25)
        self.assertEqual((float(app.min_period.get()), float(app.max_period.get())), (0.02, 20.0))
        self.assertFalse([ln for ln in got["period"] if ln.startswith("Range   ")])

    def test_marked_period_widens_too(self):
        app = self.app
        self._open(_series(period=0.25), lo=0.02, hi=0.1, marks="0.25")
        self.assertGreater(float(app.max_period.get()), 0.25 / 0.9)
        self.assertIn("Marked 0.25 d", app._period_range_note)

    def test_saved_times_period_widens_too(self):
        app = self.app
        app.star_id.set("AA LMi")
        folder = tempfile.mkdtemp(dir=self.tmp)
        path = os.path.join(folder, core.extremum_csv_name("AA LMi"))
        core.save_extremum_row(path, {"star": "AA LMi", "type": "max", "hjd": 2460950.7, "jd_utc": 2460950.7,
                                      "period_d": 0.25, "epoch": 2460950.7, "n_points": 20})
        app.extremum_csv, app.extremum_csv_star = path, "AA LMi"
        try:
            self.assertAlmostEqual(app._times_csv_period(), 0.25)
            got = self._open(_series(period=0.25), lo=0.02, hi=0.1)
            self.assertIn("the saved times' 0.25 d", app._period_range_note)
            self.assertTrue(any(ln.startswith("Saved   0.25000 d") for ln in got["period"]), got["period"])
        finally:
            app.extremum_csv = app.extremum_csv_star = ""

    def test_known_period_too_long_for_the_nights_is_a_plain_row(self):
        app = self.app
        got = self._open(_series(period=0.25), lo=0.02, hi=20.0, vsx=332.0)
        self.assertEqual((float(app.min_period.get()), float(app.max_period.get())), (0.02, 20.0))
        rows = [ln for ln in got["period"] if ln.startswith("Range   ")]
        self.assertEqual(len(rows), 1, got["period"])
        self.assertIn("VSX 332 d is outside the search", rows[0])
        self.assertIn("Add nights", rows[0])
        self.assertNotIn("⚠", rows[0])
        self.assertFalse(any("outside the search" in c for c in got["cautions"]))
        self.assertFalse(any("outside the search" in ln for ln in got["period"][:1]))   # not a verdict reason

    def test_edge_with_no_known_period_is_a_plain_row_not_a_caution(self):
        # a slow drift over three nights: the best "period" sits at the long end of the search
        app = self.app
        self._open(_series(period=0.25), lo=0.02, hi=1.0)
        app.period = dict(app.period, edge="max", period_days=0.95, max_period=1.0)
        got = {}

        def grab(series_lines, period_lines, color_lines, rows, cautions):
            got["period"], got["cautions"] = list(period_lines), list(cautions)
        with mock.patch.object(app, "_fill_summary", side_effect=grab):
            app.draw_output()
        rows = [ln for ln in got["period"] if ln.startswith("Edge    ")]
        self.assertEqual(len(rows), 1, got["period"])
        self.assertNotIn("⚠", rows[0])
        self.assertFalse(any("long end of the search" in c for c in got["cautions"]))

    def test_vsx_elements_travel_with_the_series_file(self):
        app = self.app
        app.catalog_period, app.catalog_epoch, app.catalog_var_type = 0.25, 2460950.7, "DSCT"
        meta = app._meta()
        self.assertEqual(meta["vsx_elements"], {"period_d": 0.25, "epoch": 2460950.7, "var_type": "DSCT"})
        app.catalog_period, app.catalog_epoch, app.catalog_var_type = None, None, ""
        app._apply_meta_values({"vsx_elements": meta["vsx_elements"]})
        self.assertEqual((app.catalog_period, app.catalog_epoch, app.catalog_var_type), (0.25, 2460950.7, "DSCT"))
        app.catalog_period = 0.3                                   # a lookup's own value wins over the file's
        app._apply_meta_values({"vsx_elements": meta["vsx_elements"]})
        self.assertEqual(app.catalog_period, 0.3)

    def test_constant_is_not_said_while_a_night_changes(self):
        # two short nights of a star that moves 0.08 mag in each: no period is significant, but it is not constant
        rng = np.random.default_rng(2)
        pts = []
        for ni in range(2):
            for i in range(80):
                jd = 2460950.60 + 3 * ni + i * 0.0006
                mag = 10.0 + 0.04 * math.sin(2 * math.pi * i / 110.0 + ni) + rng.normal(0, 0.006)
                pts.append(core.Obs(jd=jd, mag=mag, merr=0.006, cmag=9.0, kmag=10.5, airmass=1.2,
                                    path=f"M_{ni}_{i:03d}.fit", exptime=30.0, flux_var=1.0, flux_comp=1.0,
                                    night=f"JD{2460950 + 3 * ni}", filt="CV"))
        app = self.app
        self._open(pts)
        fake = dict(app.period, fap=0.4)
        app.period = fake                                          # whatever the search said: not significant
        got = {}

        def grab(series_lines, period_lines, color_lines, rows, cautions):
            got["period"] = list(period_lines)
        with mock.patch.object(app, "_fill_summary", side_effect=grab):
            app.draw_output()
        first = got["period"][0]
        self.assertTrue(first.startswith("Period  None: no significant period: the star does change ("), first)
        self.assertNotIn("looks constant", first)
        self.assertIn("Add nights", first)

    def test_fap_row_never_prints_zero(self):
        app = self.app
        self._open(_series(period=0.25))
        app.period = dict(app.period, fap=0.0, night_fap={"fap": 0.0, "method": "within nights"})
        got = {}

        def grab(series_lines, period_lines, color_lines, rows, cautions):
            got["period"] = list(period_lines)
        with mock.patch.object(app, "_fill_summary", side_effect=grab):
            app.draw_output()
        power = [ln for ln in got["period"] if ln.startswith("Power   ")][0]
        self.assertNotIn("0.0e+00", power)
        self.assertIn("FAP < 1e-99 (points), < 1e-99 counting nights", power)


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestPeriodJobSplit(Base):
    def test_job_needs_no_tk_and_matches_the_in_line_search(self):
        app = self.app
        app.mode_key = "variables"
        app.observations = _series(period=0.25)
        app.min_period.set(0.02)
        app.max_period.set(20.0)
        app.catalog_period = None
        app.mark_periods.set("")
        app._refresh_period()
        want = app.period["period_days"]
        job = app._period_prepare()
        self.assertIsNone(app.period)
        box = {}

        def work():
            box["tid"] = threading.get_ident()
            box["out"] = ca.App._period_job(job)       # a static method: no self, so no Tk
        t = threading.Thread(target=work)
        t.start()
        t.join(120)
        self.assertNotEqual(box["tid"], threading.get_ident())
        app._period_apply(job, box["out"])
        self.assertAlmostEqual(app.period["period_days"], want, places=9)
        for key in ("alias_text", "carried_by", "night_fap", "airmass_trend", "night_means"):
            self.assertIn(key, app.period)
        src = inspect.getsource(ca.App._period_job)
        for word in ("self.", ".get()", "messagebox", "StringVar"):
            self.assertNotIn(word, src, word)

    def test_bad_range_is_said_not_raised(self):
        app = self.app
        app.observations = _series(period=0.25)
        app.catalog_period = None
        app.mark_periods.set("")
        app.min_period.set(5.0)
        app.max_period.set(1.0)
        app._refresh_period()
        self.assertIsNone(app.period)
        self.assertTrue(app.status.get().startswith("Period search:"), app.status.get())
        app.min_period.set(0.02)
        app.max_period.set(20.0)


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestLoadSeriesThread(Base):
    def _file(self):
        pts = M._points(COMPS, CAT, n=12)
        meta = M._old_meta(COMPS, CAT)
        path = os.path.join(self.dir, "s.cvseries")
        core.save_series(path, meta, pts)
        return path, len(pts)

    def pump(self, cond, seconds=120):
        end = time.time() + seconds
        while time.time() < end and not cond():
            self.app.update()
            time.sleep(0.01)
        return cond()

    def test_slow_parts_run_off_the_window_thread(self):
        app = self.app
        path, n = self._file()
        main = threading.get_ident()
        seen = {"read": None, "search": None, "busy_in_read": None, "words": []}
        real_read, real_job = core.load_series, ca.App._period_job

        def read(p):
            seen["read"] = threading.get_ident()
            seen["busy_in_read"] = app.busy
            time.sleep(0.4)                      # long enough for the bar to show and the window to keep answering
            return real_read(p)

        def job(j):
            seen["search"] = threading.get_ident()
            return real_job(j)
        real_begin, real_words = app.busy_begin, app.busy_words

        def begin(words):
            seen["words"].append(words)
            return real_begin(words)

        def words(tok, text):
            seen["words"].append(text)
            return real_words(tok, text)
        beats = {"n": 0}

        def beat():
            beats["n"] += 1
            if app.busy:
                app.after(20, beat)
        with mock.patch.object(ca.App, "SERIES_SYNC", False), \
                mock.patch.object(ca.filedialog, "askopenfilename", return_value=path), \
                mock.patch.object(core, "load_series", read), \
                mock.patch.object(ca.App, "_period_job", staticmethod(job)), \
                mock.patch.object(app, "busy_begin", begin), mock.patch.object(app, "busy_words", words):
            app.status.set("")
            app.load_series()
            self.assertTrue(app.busy)                               # the run lock while the worker reads
            self.assertEqual(app.observations, [])                  # nothing loaded on the window thread yet
            app.after(20, beat)
            self.assertTrue(self.pump(lambda: app.status.get().startswith("Loaded ")), app.status.get())
        self.assertIsNotNone(seen["read"])
        self.assertNotEqual(seen["read"], main)
        self.assertIsNotNone(seen["search"])
        self.assertNotEqual(seen["search"], main)
        self.assertTrue(seen["busy_in_read"])
        self.assertGreaterEqual(beats["n"], 3)                      # the window's own thread kept running
        self.assertFalse(app.busy)
        self.assertEqual(len(app.observations), n)
        self.assertEqual(app.series_path, path)
        self.assertFalse(app.busy_bar.jobs)                         # the bar ended
        text = " | ".join(seen["words"])
        for step in ("reading the file", "the stars and settings", "the period search", "drawing the light curve"):
            self.assertIn(step, text)

    def test_unreadable_file_ends_the_bar_and_the_lock(self):
        app = self.app
        bad = os.path.join(self.dir, "bad.cvseries")
        with open(bad, "w") as fh:
            fh.write("not a series")
        shown = []
        with mock.patch.object(ca.App, "SERIES_SYNC", False), \
                mock.patch.object(ca.filedialog, "askopenfilename", return_value=bad), \
                mock.patch.object(ca.messagebox, "showerror", lambda *a, **k: shown.append(a)):
            app.load_series()
            self.assertTrue(self.pump(lambda: bool(shown)))
            self.pump(lambda: not app.busy, 5)
        self.assertFalse(app.busy)
        self.assertFalse(app.busy_bar.jobs)
        self.assertIn("Could not read bad.cvseries", shown[0][1])

    def test_in_line_path_still_works(self):
        app = self.app
        path, n = self._file()
        self.assertTrue(ca.App.SERIES_SYNC)                         # conftest: tests load in line
        with mock.patch.object(ca.filedialog, "askopenfilename", return_value=path):
            app.load_series()
        self.assertEqual(len(app.observations), n)
        self.assertFalse(app.busy)
        self.assertFalse(app.busy_bar.jobs)

    def test_save_csv_writes_on_a_worker(self):
        src = inspect.getsource(ca.App.save_csv)
        self.assertIn("_save_in_background", src)
        bg = inspect.getsource(ca.App._save_in_background)
        self.assertIn("threading.Thread", bg)
        self.assertIn("busy_begin", bg)


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestSeriesSite(Base):
    def test_close_series_puts_the_own_site_back(self):
        app = self.app
        pts = M._points(COMPS, CAT)
        meta = M._old_meta(COMPS, CAT, extra={"lat": "48.1000", "lon": "11.6000", "observer": "XYZ"})
        path = os.path.join(self.dir, "site.cvseries")
        core.save_series(path, meta, pts)
        app.lat.set("35.3700")
        app.lon.set("-81.9600")
        app.observer.set("JABA")
        app._site_before_series = None
        app._load_series_file(path, lambda words: None)
        self.assertEqual((app.lat.get(), app.observer.get()), ("48.1000", "XYZ"))       # the series' own, shown
        with mock.patch.object(app, "_offer_series_save", return_value=True):
            app.close_series()
        self.assertEqual((app.lat.get(), app.lon.get(), app.observer.get()), ("35.3700", "-81.9600", "JABA"))
        self.assertTrue(any(ln.startswith("Input page: your own latitude, longitude, observer code are back")
                            for ln in self.lines), self.lines)

    def test_a_box_typed_since_the_load_is_kept(self):
        app = self.app
        pts = M._points(COMPS, CAT)
        meta = M._old_meta(COMPS, CAT, extra={"lat": "48.1000", "lon": "11.6000", "observer": "XYZ"})
        path = os.path.join(self.dir, "site2.cvseries")
        core.save_series(path, meta, pts)
        app.lat.set("35.3700")
        app.lon.set("-81.9600")
        app.observer.set("JABA")
        app._site_before_series = None
        app._load_series_file(path, lambda words: None)
        app.observer.set("NEW")                                     # typed by the user after the load
        with mock.patch.object(app, "_offer_series_save", return_value=True):
            app.close_series()
        self.assertEqual(app.observer.get(), "NEW")
        self.assertEqual(app.lat.get(), "35.3700")

    def test_same_site_says_nothing(self):
        app = self.app
        pts = M._points(COMPS, CAT)
        path = os.path.join(self.dir, "site3.cvseries")
        core.save_series(path, M._old_meta(COMPS, CAT), pts)
        app._site_before_series = None
        app._load_series_file(path, lambda words: None)
        with mock.patch.object(app, "_offer_series_save", return_value=True):
            app.close_series()
        self.assertFalse(any(ln.startswith("Input page: your own") for ln in self.lines))


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestSmallNitsInApp(Base):
    def test_show_log_opens_a_window_and_keeps_the_page(self):
        app = self.app
        step = ca.STEPS.index("Input")
        app.show_step(step)
        app._findings_show_log()
        box = app._log_window_box()
        self.assertIsNotNone(box)
        self.assertEqual(app.current_step, step)                    # no jump to the Calibrate page
        app.log = self.real_log
        app.log("a line for the Log window test", keep_status=True)
        self.assertIn("a line for the Log window test", box.get("1.0", "end"))
        win = box.winfo_toplevel()
        self.assertIs(app.open_log_window(), win)                   # a second press shows the same window
        win.destroy()
        app.update()
        self.assertIsNone(app._log_window_box())
        app.log("after the window closed", keep_status=True)        # and logging goes on without it

    def test_show_log_on_the_calibrate_page_opens_nothing(self):
        app = self.app
        app.show_step(ca.STEP_CAL)
        app._findings_show_log()
        self.assertIsNone(app._log_window_box())
        self.assertEqual(app.current_step, ca.STEP_CAL)

    def test_night_is_chosen_in_the_dialog(self):
        app = self.app
        nights = ["JD2460950", "JD2460951", "JD2460953"]
        asked = {}

        def pick(parent, title, message, labels, start=0):
            asked.update(title=title, message=message, labels=list(labels), start=start)
            return 1
        with mock.patch.object(ca, "ask_night", pick):
            got = app._pick_night(nights, "Transmission loss…", "Which night?")
        self.assertEqual(got, "JD2460951")
        self.assertEqual(len(asked["labels"]), 3)
        self.assertEqual(asked["start"], 2)                         # starts at the latest night
        self.assertIn("Transmission loss…", asked["title"])
        with mock.patch.object(ca, "ask_night", lambda *a, **k: None):
            self.assertIsNone(app._pick_night(nights, "Planet transits…", "Which night?"))
        for fn in (ca.App.edit_transmission, ca.App.edit_planet_transits):
            src = inspect.getsource(fn)
            self.assertIn("_pick_night(", src)
            self.assertNotIn("Click one in the night table first", src)

    def test_planet_transits_asks_for_the_picked_night(self):
        app = self.app
        app.mode_key = "variables"
        app.observations = _series(nights=3, per_night=10)
        app.highlight_night = None
        nights = sorted({o.night for o in app.observations})
        for n in nights[:2]:
            app._night_info(n, create=True)["planet_transits"] = [{"letter": "b", "frames": 3, "name": "X b"}]
        asked = []
        with mock.patch.object(ca, "ask_night", lambda *a, **k: 1), \
                mock.patch.object(app, "_planet_hits_text", lambda h: "3 frames fall in planet b's transit"), \
                mock.patch.object(ca.messagebox, "askyesno", lambda t, m, **k: asked.append(m) or False), \
                mock.patch.object(app, "_set_transit_flags") as setf, \
                mock.patch.object(app, "_after_transit_flags"):
            app.edit_planet_transits()
        self.assertEqual(len(asked), 1)
        self.assertTrue(asked[0].startswith(app.night_label(nights[1], short=True)), asked[0])
        setf.assert_called_once_with(nights[1], False)

    def test_no_planets_text_tells_the_two_cases_apart(self):
        with mock.patch.dict(tc._NEA_LAST, {"nowhere-1": "no answer", "hd 1": "unknown", "hd 2": "ok"}, clear=True):
            text = ca.App._no_planets_text("Nowhere-1")
            self.assertTrue(text.startswith("✖ Nowhere-1: the NASA Exoplanet Archive did not answer"), text)
            self.assertIn("Look up again", text)
            for star in ("HD 1", "HD 2"):
                text = ca.App._no_planets_text(star)
                self.assertTrue(text.startswith(f"The NASA Exoplanet Archive lists no planet for {star}."), text)
                self.assertNotIn("✖", text)
                self.assertNotIn("did not answer", text)
            self.assertIn("or the Archive did not answer", ca.App._no_planets_text("never asked"))

    def test_planet_transits_status_tells_the_two_cases_apart(self):
        app = self.app
        app.series_mode = "variables"
        ctx = {"star_text": "HD 219134", "status": "x", "status_before": ""}
        with mock.patch.object(app, "_planet_check_stale", return_value=""):
            with mock.patch.dict(tc._NEA_LAST, {"hd 219134": "no answer"}, clear=True):
                app._planet_transits_found({}, None, user=True, ctx=ctx)
                self.assertTrue(app.status.get().startswith("✖ Planet transits: the NASA Exoplanet Archive did not "
                                                            "answer"), app.status.get())
            with mock.patch.dict(tc._NEA_LAST, {"hd 219134": "ok"}, clear=True):
                app._planet_transits_found({}, [], user=True, ctx=ctx)
                self.assertTrue(app.status.get().startswith("Planet transits: the NASA Exoplanet Archive lists no "
                                                            "transiting planet"), app.status.get())
                self.assertNotIn("could not be reached", app.status.get())

    def test_stacked_night_counts_its_per_frame_points(self):
        app = self.app
        app.observations = _series(nights=1, per_night=10)
        night = app.observations[0].night
        app._night_info(night, create=True)["stack"] = {"n": 4, "stacks": 10, "frames": 40, "frame_points": 38}
        self.assertEqual(app._night_frame_count(night), 38)
        app._night_info(night)["stack"].pop("frame_points")
        self.assertEqual(app._night_frame_count(night), 40)          # an older file: as before
        self.assertIn('info["stack"]["frame_points"] = len(sets["frame"])',
                      inspect.getsource(sys.modules["stack_ui"]))

    def test_blink_wording(self):
        app = self.app
        self.assertEqual(ca.App.__init__ is not None, True)
        src = inspect.getsource(ca.App)
        self.assertNotIn('"Rejected: none"', src)
        app.blink_paths = []
        old = app.blink_source.get()
        try:
            app.blink_source.set("flats")
            app.show_blink()
            self.assertEqual(app.blink_label.get(), "No flats in the Flat folder.")
            app.blink_source.set("lights")
            app.show_blink()
            self.assertEqual(app.blink_label.get(), "No lights in the lights folder.")
        finally:
            app.blink_source.set(old)

    def test_planet_box_survives_a_mode_switch(self):
        app = self.app
        start = app.mode_key
        try:
            app.set_mode("transits")
            app.planet_name.set("TOI-2046 b")
            app.set_mode("variables")
            app.set_mode("transits")
            self.assertEqual(app.planet_name.get(), "TOI-2046 b")
        finally:
            app.planet_name.set("")
            app.set_mode(start or "variables")

    def test_rerun_after_a_mode_switch_does_not_ask_again(self):
        app = self.app
        calls = []
        with mock.patch.object(app, "_typed_site_check", lambda **k: calls.append("site")), \
                mock.patch.object(app, "_aperture_sane", lambda: calls.append("aperture") or False), \
                mock.patch.object(app, "_markers_in_order", lambda: calls.append("markers") or False), \
                mock.patch.object(app, "_mode_guard", lambda lights: calls.append("mode") or False), \
                mock.patch.object(app, "_lights", lambda **k: ["a.fit"]), \
                mock.patch.object(app, "_prune_rejected", lambda: 0), \
                mock.patch.object(app, "_camera_aperture_settle", lambda: None), \
                mock.patch.object(app, "_camera_folders_snapshot", lambda **k: None), \
                mock.patch.object(app, "_drop_survey_for_series", lambda: False):
            app._survey_cache = None
            app.masters = object()
            try:
                app._rerun_answered = True
                app.run_photometry()
                self.assertEqual(calls, ["mode"])                   # the mode checks still run; nothing else asks
                self.assertFalse(app._rerun_answered)               # used once
                calls.clear()
                app.run_photometry()
                self.assertEqual(calls, ["site", "aperture"])       # an ordinary run asks as before
            finally:
                app.masters = None
        self.assertIn("self._rerun_answered = True", inspect.getsource(ca.App._rerun_in_mode))

    def test_sweep_messages_say_what_to_do(self):
        src = inspect.getsource(ca)
        import stack_ui
        st = inspect.getsource(stack_ui)
        for gone in ('f"Could not save: {exc}"', '"⚠ Could not save the color term', 'f"Time conversion: {exc}"',
                     '"No usable points to report for that choice."', "⚠ peak at the search edge"):
            self.assertNotIn(gone, src, gone)
        for here in ("then press Save series again", "press Apply to put every night", "or untick Leave flagged points out",
                     "then press Save again", "HJD and \"\n", "(the peak is at the edge of the search)"):
            if here.startswith("HJD"):
                continue
            self.assertIn(here, src, here)
        for here in ("Press Stack \"", "press Apply.", "Save series (under the plot on the Output page)"):
            if here.startswith("Press Stack"):
                self.assertIn("to try again, or carry on with the per-frame points", st)
                continue
            self.assertIn(here, st, here)
        with self.assertRaises(ValueError) as err:
            core.lomb_scargle(np.arange(20.0), np.arange(20.0), 5.0, 1.0)
        self.assertIn("correct the two Period search boxes", str(err.exception))

    def test_plot_bins_label(self):
        src = inspect.getsource(ca.App._page_output)
        self.assertIn('text="Plot bins"', src)
        self.assertNotIn('text="Bin"', src)


class _Var:
    def __init__(self, v=""):
        self.v = v

    def get(self):
        return self.v

    def set(self, v):
        self.v = v


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestWindowsWithoutAWindow(unittest.TestCase):
    """Methods of the Time of max/min window and the scan view, run on stand-ins (no window needed)."""

    def test_epoch_with_a_label_is_read(self):
        w = mock.Mock()
        w.period, w.epoch = _Var("0.15151"), _Var("JD 2461353.81255")
        self.assertEqual(ca.ExtremumWindow._elements(w), (0.15151, 2461353.81255))
        w._elements = lambda: ca.ExtremumWindow._elements(w)
        self.assertEqual(ca.ExtremumWindow._elements_problem(w), "")
        w.epoch = _Var("HJD=2461353.8")
        self.assertEqual(ca.ExtremumWindow._elements(w), (0.15151, 2461353.8))

    def test_unreadable_epoch_is_said(self):
        w = mock.Mock()
        w.period, w.epoch = _Var("0.15151"), _Var("last Tuesday")
        w._elements = lambda: ca.ExtremumWindow._elements(w)
        self.assertEqual(w._elements(), (None, None))
        text = ca.ExtremumWindow._elements_problem(w)
        self.assertIn("Epoch 'last Tuesday' is not a Julian Date", text)
        self.assertIn("No O−C", text)
        w.epoch = _Var("53.8")                                      # a number, but not a full JD
        self.assertIn("not a full Julian Date", ca.ExtremumWindow._elements_problem(w))
        w.period, w.epoch = _Var(""), _Var("")
        self.assertEqual(ca.ExtremumWindow._elements_problem(w), "")
        w.period, w.epoch = _Var("0.15"), _Var("")                 # a period alone serves the error: fine
        self.assertEqual(ca.ExtremumWindow._elements_problem(w), "")

    def test_each_problem_once(self):
        t = 2461353.6 + np.arange(40) * 0.002
        vals = 10.0 + 0.5 * (t - t[0])                              # a ramp: no maximum covered on both sides
        job = dict(kind="max", period=0.05, epoch=2461353.0, hw=None, t=t, vals=vals,
                   guesses=[2461353.61, 2461353.66], noise=(None, "none", "", None), source_words="",
                   coords=(None, None, None, None), use_bjd=False)
        results, problems = ca.ExtremumWindow._measure_work(job)
        self.assertEqual(results, [])
        self.assertEqual(len(problems), len(set(problems)), problems)

    def test_store_oc_button_gate_and_action(self):
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, core.extremum_csv_name("AA LMi"))
        core.save_extremum_row(path, {"star": "AA LMi", "type": "max", "hjd": 2461353.8, "jd_utc": 2461353.8,
                                      "period_d": 0.0543, "epoch": 2461300.5, "elements": "VSX HJD",
                                      "n_points": 20})
        w = mock.Mock()
        w.period, w.epoch, w.system, w.source = _Var("0.11"), _Var("JD 2461353.8"), _Var("HJD"), _Var("")
        w._elements = lambda: ca.ExtremumWindow._elements(w)
        w._star = lambda: "AA LMi"
        w._default_csv = lambda: path
        w._csv_rows = lambda: core.read_extremum_csv(path)
        w._elements_source = lambda p, e: "typed"
        self.assertEqual(ca.ExtremumWindow._store_oc_why(w), "")
        ca.ExtremumWindow.store_oc(w)
        row = core.read_extremum_csv(path)[0]
        self.assertEqual((row["period_d"], row["elements"], row["cycle"]), (0.11, "typed HJD", 0.0))
        self.assertEqual(len(glob.glob(os.path.join(folder, "*_before_oc_*.csv"))), 1)
        logged = w.app.log.call_args[0][0]
        self.assertIn("O−C of 1 saved time of AA LMi stored", logged)
        self.assertIn("is kept as", logged)
        w.period = _Var("")
        self.assertIn("Type a Period and an Epoch first", ca.ExtremumWindow._store_oc_why(w))
        w.period = _Var("0.11")
        w._csv_rows = lambda: []
        self.assertIn("No saved times of AA LMi yet", ca.ExtremumWindow._store_oc_why(w))

    def test_picked_stars_one_count(self):
        v = mock.Mock()
        v._plot_points = lambda: np.array([0, 1, 2, 5])
        for picked, want in (([], "No star picked on the scatter plot."),
                             ([1], "1 star picked on the scatter plot: Mark on image shows it."),
                             ([1, 2, 5], "3 stars picked on the scatter plot: Mark on image shows them."),
                             ([1, 9], "1 star picked on the scatter plot: Mark on image shows it.")):   # 9 not drawn
            v.picked = picked
            ca.ScanView._say_picked(v)
            self.assertEqual(v.app.status.set.call_args[0][0], want)
        src = inspect.getsource(ca.ScanView._on_release)
        self.assertNotIn("star(s) picked", src)
        self.assertGreaterEqual(src.count("_say_picked()"), 3)       # a drag, a click, a second click that clears

    def test_scan_files_named_after_the_field_in_discovery(self):
        v = mock.Mock()
        v.app.star_id = _Var("BL Cam")                              # left there by Variables mode
        v.app._filter_code = lambda: "TG"
        v._scan_jds = lambda: []
        out = os.path.join(tempfile.mkdtemp(), "NGC 7790", "13Nov26", "Output")
        os.makedirs(out)
        v.app.output_dir = _Var(out)
        v.r = {}
        v.app.mode_key = "discovery"
        self.assertEqual(ca.ScanView._scan_stem(v), "NGC_7790_TG_SHOBS-P_scan")
        v.r = {"suspects_where": {"folder": out}}
        self.assertEqual(ca.ScanView._scan_stem(v), "NGC_7790_TG_SHOBS-P_scan")
        v.app.mode_key = "variables"                                # the Star ID is this mode's own: as before
        self.assertEqual(ca.ScanView._scan_stem(v), "BL_Cam_TG_SHOBS-P_scan")

    def test_scan_known_variable_gets_its_own_range(self):
        rng = np.random.default_rng(4)
        jd = 2460950.5 + np.sort(rng.uniform(0, 6.0, 400))
        lc = np.column_stack([10 + rng.normal(0, 0.01, jd.size),
                              11 + 0.1 * np.sin(2 * math.pi * jd / 0.9) + rng.normal(0, 0.01, jd.size),
                              12 + rng.normal(0, 0.01, jd.size)])
        ls1 = core.lomb_scargle(jd, lc[:, 1], 0.02, 0.5)
        r = {"period_range": (0.02, 0.5), "jd": jd, "light_curves": lc,
             "vsx": [None, {"name": "V1", "period": 0.9}, {"name": "V2", "period": 40.0}],
             "periods": {1: {"period": ls1["period_days"], "power": ls1["power"], "ls": ls1}}}
        v = mock.Mock()
        v.r = r
        v.rows = {"i1": 1, "i2": 2, "i0": 0}
        v.range_text = _Var("")
        v._range_packed = False
        ca.ScanView._update_range_note(v)
        per = r["periods"][1]
        self.assertAlmostEqual(per["period"], 0.9, delta=0.03)       # RESULTS-CHANGING: found over its own range
        self.assertGreater(per["range"][1], 0.9 / 0.9)
        self.assertNotIn(2, r["periods"])                            # 40 d on 6 d: no range can test it
        text = v.range_text.get()
        self.assertNotIn("⚠", text)
        self.assertIn("Searched over a wider range to cover the VSX period: V1 0.9 d", text)
        self.assertIn("VSX period too long for this scan to test (under two cycles): V2 40 d", text)
        v.tree.set.assert_called()                                   # the row filled a moment ago shows it
        self.assertEqual(r["period_range"], (0.02, 0.5))             # the scan's own range is untouched
        calls = v.tree.set.call_count
        ca.ScanView._update_range_note(v)                            # a second fill: not searched again
        self.assertEqual(v.tree.set.call_count, calls)
        self.assertNotIn("range_button", inspect.getsource(ca.ScanView.__init__))

    def test_x037_period_cell_untouched(self):
        self.assertEqual(ca.period_cell({"ls": {"edge": "max"}, "period": 20.0}), "no period (drift)")
        self.assertEqual(ca.period_cell({"ls": {"edge": "min"}, "period": 0.02}), "at the short end: search shorter")


if __name__ == "__main__":
    unittest.main()
