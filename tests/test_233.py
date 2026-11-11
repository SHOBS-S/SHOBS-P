"""2.3.3 items (numbers frozen at 2.3.0): #57 new-version check, #64 item 1 damaged APASS copies, #63 rejected-frame
labels, and the Export / Time of max/min / Elevation nits."""
import io
import json
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ["SHOBS_ONSKY_OFF"] = "1"

import matplotlib  # noqa: E402
matplotlib.use("Agg")

import photometry_core as core  # noqa: E402
import shobs_update  # noqa: E402


def _tsv(rows, cols=("RAJ2000", "DEJ2000", "Vmag", "e_Vmag", "Bmag")):
    """A minimal VizieR asu-tsv answer."""
    lines = ["#RESOURCE=yCat_2336", "\t".join(cols), "\t".join("-" * 5 for _ in cols)]
    for r in rows:
        lines.append("\t".join(str(r.get(c, "")) for c in cols))
    return ("\n".join(lines) + "\n").encode()


def _good_rows(n=40):
    return [{"RAJ2000": f"{41.80 + i * 0.001:.6f}", "DEJ2000": f"{50.75 + i * 0.001:+.6f}", "Vmag": f"{12.0 + i * 0.1:.3f}",
             "e_Vmag": "0.05", "Bmag": f"{12.6 + i * 0.1:.3f}"} for i in range(n)]


def _cfa_rows(n=40):
    # 10 Nov 2026: the CfA copy, V 0.1-2.1 and B 999999
    return [{"RAJ2000": f"{41.80 + i * 0.001:.6f}", "DEJ2000": f"{50.75 + i * 0.001:+.6f}", "Vmag": f"{0.2 + i * 0.04:.3f}",
             "e_Vmag": "0.05", "Bmag": "999999.0"} for i in range(n)]


class DamageRule(unittest.TestCase):
    def test_good_field_passes(self):
        self.assertEqual(core.apass_damage([12.0 + 0.1 * i for i in range(50)], [12.6] * 50), "")

    def test_one_or_two_odd_rows_pass(self):
        vs = [12.0 + 0.1 * i for i in range(50)] + [5.8]
        self.assertEqual(core.apass_damage(vs), "")
        self.assertEqual(core.apass_damage([13.0] * 50 + [99.999], [13.6] * 50 + [99.999]), "")

    def test_shifted_columns_damaged(self):
        self.assertIn("brighter than V 4", core.apass_damage([0.2 + 0.04 * i for i in range(40)]))

    def test_two_below_floor_damaged_even_in_big_field(self):
        self.assertTrue(core.apass_damage([12.0] * 3000 + [1.0, 2.0]))

    def test_more_than_ten_below_six(self):
        self.assertTrue(core.apass_damage([12.0] * 3000 + [5.0 + 0.05 * i for i in range(11)]))
        self.assertEqual(core.apass_damage([12.0] * 3000 + [5.0 + 0.05 * i for i in range(10)]), "")

    def test_one_or_two_row_damaged_answer(self):
        # Fable S3: a 1-row answer at V 0.3 (CfA, small cone) is damaged; one bright row in a big field is not judged
        self.assertTrue(core.apass_damage([0.3]))
        self.assertTrue(core.apass_damage([0.3, 12.0]))
        self.assertEqual(core.apass_damage([12.0] * 40 + [3.0]), "")
        self.assertEqual(core.apass_damage([7.1]), "")

    def test_majority_bright(self):
        self.assertTrue(core.apass_damage([5.0, 5.2, 5.5, 12.0]))
        self.assertEqual(core.apass_damage([5.0, 5.2, 12.0, 12.5, 13.0]), "")

    def test_sentinel_majority(self):
        self.assertIn("placeholder", core.apass_damage([12.0] * 10, [999999.0] * 10))
        self.assertIn("placeholder", core.apass_damage([99.999] * 6 + [12.0] * 4))

    def test_sentinel_values(self):
        for v in (99.999, 999999.0, -99.999):
            self.assertTrue(core.is_sentinel_mag(v))
        for v in (0.6, 17.2, 30.0, float("nan")):
            self.assertFalse(core.is_sentinel_mag(v))


class FetchApass(unittest.TestCase):
    def setUp(self):
        core.CATALOG_NOTES.clear()
        core._VIZIER_DOWN.clear()
        core._VIZIER_DAMAGED.clear()

    def tearDown(self):
        core._VIZIER_DAMAGED.clear()
        core._VIZIER_DOWN.clear()

    def _run(self, answers):
        """answers: {host substring: bytes or Exception}; returns (stars, hosts asked)."""
        asked = []

        def fake(url, timeout=60, **_k):
            for key, ans in answers.items():
                if key in url:
                    asked.append(key)
                    if isinstance(ans, Exception):
                        raise ans
                    return ans
            raise AssertionError(url)
        with mock.patch.object(core, "_http_get", side_effect=fake):
            return core.fetch_apass9(41.80, 50.75, 0.3), asked

    def test_good_cds_answer_identical(self):
        stars, asked = self._run({"cds": _tsv(_good_rows())})
        self.assertEqual(asked, ["cds"])
        self.assertEqual(len(stars), 40)
        self.assertEqual(stars[0]["mags"], {"V": 12.0, "B": 12.6})
        self.assertEqual(getattr(stars, "incomplete", ""), "")
        self.assertEqual(core.CATALOG_NOTES, [])

    def test_damaged_cfa_skipped_for_idia(self):
        stars, asked = self._run({"cds": ConnectionError("down"), "cfa": _tsv(_cfa_rows()), "idia": _tsv(_good_rows())})
        self.assertEqual(asked, ["cds", "cfa", "idia"])
        self.assertEqual(len(stars), 40)
        self.assertAlmostEqual(stars[0]["mags"]["V"], 12.0)
        self.assertEqual(getattr(stars, "incomplete", ""), "")   # IDIA's good stars are complete: kept
        self.assertEqual(len(core.CATALOG_NOTES), 1)
        self.assertIn("vizier.cfa.harvard.edu looked wrong", core.CATALOG_NOTES[0])
        # Fable S4: the damaged copy is asked last for APASS for a while; its other catalogs keep their place
        self.assertEqual(core._vizier_hosts("II/336/apass9")[-1], "https://vizier.cfa.harvard.edu")
        self.assertEqual(core._vizier_hosts("I/259/tyc2")[0], "https://vizier.cfa.harvard.edu")   # CDS down: CfA first

    def test_only_damaged_copies_fail(self):
        with self.assertRaises(ConnectionError) as cm:
            self._run({"cds": ConnectionError("down"), "cfa": _tsv(_cfa_rows()), "idia": ConnectionError("down")})
        self.assertIn("VizieR", str(cm.exception))
        with self.assertRaises(ConnectionError) as cm:
            self._run({"cds": _tsv(_cfa_rows()), "cfa": _tsv(_cfa_rows()), "idia": _tsv(_cfa_rows())})
        self.assertIn("looked wrong", str(cm.exception))

    def test_empty_after_damaged_not_kept(self):
        stars, _ = self._run({"cds": _tsv(_cfa_rows()), "cfa": _tsv([]), "idia": _tsv(_good_rows())})
        self.assertEqual(list(stars), [])
        self.assertFalse(core._cacheable("APASS DR9", stars, None))   # an empty answer is never kept

    def test_sentinel_b_dropped_per_row(self):
        rows = _good_rows()
        rows[3]["Bmag"] = "99.999"
        rows[4]["Vmag"] = "99.999"
        stars, _ = self._run({"cds": _tsv(rows)})
        self.assertEqual(len(stars), 39)
        self.assertNotIn("B", stars[3]["mags"])

    def test_tycho_untouched(self):
        cols = ("TYC1", "TYC2", "TYC3", "RAmdeg", "DEmdeg", "pmRA", "pmDE", "BTmag", "VTmag")
        rows = [{"TYC1": 1, "TYC2": i, "TYC3": 1, "RAmdeg": 41.8, "DEmdeg": 50.75, "pmRA": 0, "pmDE": 0, "BTmag": 3.0,
                 "VTmag": 2.5} for i in range(5)]
        with mock.patch.object(core, "_http_get", return_value=_tsv(rows, cols)):
            self.assertEqual(len(core.fetch_tycho2(41.8, 50.75, 0.3)), 5)   # bright Tycho stars are real


class SavedCopies(unittest.TestCase):
    def setUp(self):
        core.CATALOG_NOTES.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(core, "CATALOG_CACHE_DIR", self.tmp.name)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def _stars(self, v, b):
        return [{"auid": f"APASS J{i}", "ra": 41.8, "dec": 50.75, "mag": v + 0.01 * i, "mags": {"V": v + 0.01 * i,
                 "B": b}, "band": "V", "catalog": "APASS DR9", "bright": v} for i in range(30)]

    def test_damaged_saved_copy_ignored(self):
        key = core.catalog_cache_key("APASS DR9", 2.787, 50.75, 40.0, "V", None)
        self.assertTrue(core.catalog_cache_save(key, self._stars(0.6, 999999.0)))
        self.assertIsNone(core.catalog_cache_load(key))
        self.assertIn("saved copy", core.CATALOG_NOTES[0])

    def test_good_saved_copy_used(self):
        key = core.catalog_cache_key("APASS DR9", 2.787, 50.75, 40.0, "V", None)
        core.catalog_cache_save(key, self._stars(12.0, 12.6))
        self.assertEqual(len(core.catalog_cache_load(key)), 30)
        self.assertEqual(core.CATALOG_NOTES, [])

    def test_corrupt_saved_copy_unreadable(self):
        key = core.catalog_cache_key("APASS DR9", 2.787, 50.75, 40.0, "V", None)
        stars = self._stars(12.0, 12.6)
        stars[0]["mags"] = ["not", "a", "dict"]
        core.catalog_cache_save(key, stars)
        self.assertIsNone(core.catalog_cache_load(key))

    def test_other_catalogs_not_judged(self):
        key = core.catalog_cache_key("Tycho-2", 2.787, 50.75, 40.0, "V", None)
        core.catalog_cache_save(key, self._stars(2.0, 2.5))
        self.assertEqual(len(core.catalog_cache_load(key)), 30)


class FrameLabels(unittest.TestCase):
    def test_distinct_labels(self):
        with tempfile.TemporaryDirectory() as d:
            paths = [os.path.join(d, f"MOBS_x_WASP-43210112{t}.FITS") for t in ("082446", "082712", "083012")]
            for p in paths:
                open(p, "wb").close()
            with mock.patch.object(core, "frame_labels_for", return_value=["#1", "#1", "#2"]):
                labs = core.distinct_frame_labels(paths)
            self.assertEqual(labs[2], "#2")
            self.assertEqual(labs[0], f"#1 ({os.path.basename(paths[0])})")
            self.assertEqual(labs[1], f"#1 ({os.path.basename(paths[1])})")
            self.assertEqual(len(set(labs)), 3)

    def test_same_file_twice_once(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Light_0007.fit")
            open(p, "wb").close()
            alt = os.path.join(d, ".", "Light_0007.fit")
            self.assertEqual(core.distinct_frame_labels([p, alt]), ["7"])

    def test_plain_numbers_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            paths = [os.path.join(d, f"Light_{i:04d}.fit") for i in (3, 9, 12)]
            for p in paths:
                open(p, "wb").close()
            self.assertEqual(core.distinct_frame_labels(paths), core.frame_labels_for(paths))


class ExtremumCsv(unittest.TestCase):
    ROWS = [
        {"star": "GSC 03308-00462", "type": "max", "hjd": 2461354.85761, "error_min": 24.4, "o_c_min": -6.8,
         "period_d": 0.11664, "elements": "typed HJD", "night": "JD2461354"},
        {"star": "GSC 03308-00462", "type": "max", "hjd": 2461353.81255, "error_min": 20.8, "o_c_min": float("nan"),
         "period_d": 0.11660, "elements": "typed HJD", "night": "JD2461353"},
        {"star": "GSC 03308-00462", "type": "min", "hjd": 2461353.87, "period_d": float("nan"), "elements": ""},
    ]

    def test_elements(self):
        got = core.extremum_csv_elements(self.ROWS, "max")
        self.assertAlmostEqual(got["epoch"], 2461353.81255)   # the first saved maximum
        self.assertAlmostEqual(got["period"], 0.11664)          # the period last used (latest time)
        self.assertEqual(got["system"], "HJD")
        self.assertEqual(got["n"], 2)

    def test_min_and_none(self):
        got = core.extremum_csv_elements(self.ROWS, "min")
        self.assertAlmostEqual(got["epoch"], 2461353.87)
        self.assertAlmostEqual(got["period"], 0.11664)
        self.assertIsNone(core.extremum_csv_elements([], "max"))
        self.assertIsNone(core.extremum_csv_elements(self.ROWS[2:], "max"))

    def test_bjd_elements(self):
        rows = [{"type": "max", "hjd": 2461353.81255, "bjd_tdb": 2461353.81333, "period_d": 0.1,
                 "elements": "VSX BJD"}]
        got = core.extremum_csv_elements(rows, "max")
        self.assertEqual(got["system"], "BJD")
        self.assertAlmostEqual(got["epoch"], 2461353.81333)

    def test_lines(self):
        lines = core.extremum_times_lines(sorted(self.ROWS[:2], key=lambda r: r["hjd"]))
        self.assertEqual(len(lines), 2)
        self.assertIn("HJD 2461353.81255 ± 20.8 min", lines[0])
        self.assertIn("O−C −6.8 min", lines[1].replace("-", "−"))

    def test_round_trip_csv(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, core.extremum_csv_name("GSC 03308-00462"))
            for r in self.ROWS[:2]:
                core.save_extremum_row(path, dict(r))
            rows = core.read_extremum_csv(path)
            got = core.extremum_csv_elements(rows, "max")
            self.assertAlmostEqual(got["epoch"], 2461353.81255)
            self.assertAlmostEqual(got["period"], 0.11664)


class UpdateModule(unittest.TestCase):
    def test_same_as_sched_copy(self):
        sched = os.path.join(os.path.dirname(os.path.dirname(HERE)), "shobs-sched", "shobs_update.py")
        if not os.path.exists(sched):
            self.skipTest("SHOBS-SCHED checkout not beside this one")
        with open(sched, encoding="utf-8") as a, open(os.path.join(os.path.dirname(HERE), "shobs_update.py"),
                                                       encoding="utf-8") as b:
            self.assertEqual(a.read(), b.read())

    def test_note_for_newer(self):
        st = {}
        got = {"tag": "v2.3.4", "url": "https://github.com/SHOBS-S/SHOBS-P/releases/tag/v2.3.4", "name": "v2.3.4",
               "highlights": ["Damaged catalog copies are turned away"], "published": "2026-11-20"}
        note = shobs_update.check("SHOBS-P", "SHOBS-S/SHOBS-P", "2.3.3", st, now=1e9, fetch=lambda: got)
        self.assertEqual(note["version"], "2.3.4")
        self.assertTrue(note["text"].startswith("SHOBS-P 2.3.4 is available: Damaged"))
        self.assertEqual(st["update_tag"], "v2.3.4")
        self.assertIsNone(shobs_update.check("SHOBS-P", "x", "2.3.4", dict(st), now=1e9 + 10, fetch=lambda: 1 / 0))

    def test_off(self):
        self.assertIsNone(shobs_update.check("SHOBS-P", "x", "2.3.3", {"check_updates": False}, fetch=lambda: 1 / 0))


def _have_tk():
    try:
        import tkinter as tk
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


@unittest.skipUnless(_have_tk(), "needs a display")
class AppPieces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        cls.tmp = tempfile.TemporaryDirectory()
        cls.p = mock.patch.object(ca, "SETTINGS_FILE", os.path.join(cls.tmp.name, "s.json"))
        cls.p.start()
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

    def test_version_and_no_check_in_tests(self):
        self.assertEqual(self.ca.APP_VERSION, "2.3.3")
        self.assertEqual(self.ca.REPO, "SHOBS-S/SHOBS-P")
        self.assertFalse(self.ca.update_check_wanted())   # a bare App() (tests, self-test) never asks GitHub
        # Fable M1: the real program does (main() sets the flag); the env var still turns it off
        with mock.patch.object(self.ca, "UPDATE_CHECK", True):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SHOBS_P_NO_UPDATE_CHECK", None)
                self.assertTrue(self.ca.update_check_wanted())
            with mock.patch.dict(os.environ, {"SHOBS_P_NO_UPDATE_CHECK": "1"}):
                self.assertFalse(self.ca.update_check_wanted())

    def test_main_turns_the_check_on(self):
        with mock.patch.object(self.ca, "App") as fake, mock.patch.object(self.ca, "enable_windows_dpi"), \
                mock.patch.object(self.ca, "log_to_file"), mock.patch.object(self.ca, "UPDATE_CHECK", False):
            self.ca.main()
            self.assertTrue(self.ca.UPDATE_CHECK)
            fake.return_value.mainloop.assert_called_once()

    def test_extremum_saved_elements_button(self):
        app = self.app
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, core.extremum_csv_name("GSC 03308-00462"))
            for r in ExtremumCsv.ROWS[:2]:
                core.save_extremum_row(path, dict(r))
            app.star_id.set("GSC 03308-00462")
            app.catalog_period, app.catalog_epoch = 0.1166, 2452000.5
            app.extremum_csv, app.extremum_csv_star = path, "GSC 03308-00462"
            w = self.ca.ExtremumWindow(app)
            try:
                # opens on VSX's elements, as 2.3.0 (numbers freeze): the saved times are listed, not used by themselves
                self.assertEqual(w.epoch.get(), "2452000.50000")
                self.assertIn("2461353.81255", w.csv_text.get())
                w._prefill_from_csv()
                self.assertEqual(w.epoch.get(), "2461353.81255")
                self.assertEqual(w.period.get(), "0.11664000")
                self.assertEqual(w._elements_source(*w._elements()), "saved")
            finally:
                w.win.destroy()
                app.star_id.set("")

    def test_show_update_and_window(self):
        app = self.app
        note = {"text": "SHOBS-P 2.3.4 is available: New thing — click for details", "version": "2.3.4",
                "tag": "v2.3.4", "url": "https://github.com/SHOBS-S/SHOBS-P/releases/tag/v2.3.4",
                "highlights": ["New thing"], "published": "2026-11-20", "app": "SHOBS-P"}
        app.settings["check_updates"] = True
        app.show_update(note)
        self.assertIn("2.3.4 is available", app.update_var.get())
        app.status.set("something else")
        self.assertIn("2.3.4 is available", app.update_var.get())   # other messages never wipe it
        app.open_update()
        w = app._new_version_win
        self.assertEqual(w.lines, ["New thing"])
        with mock.patch.object(app, "open_link") as ol:
            w.open_page()
            ol.assert_called_once()
        app.check_updates_var.set(False)
        app._set_check_updates()
        self.assertEqual(app.update_var.get(), "")
        self.assertFalse(app.settings["check_updates"])
        app.show_update(note)
        self.assertEqual(app.update_var.get(), "")   # unticked: nothing shown
        app.check_updates_var.set(True)
        app._set_check_updates()

    def test_plain_note_without_highlights(self):
        app = self.app
        app.settings["check_updates"] = True
        note = {"text": "SHOBS-P 2.3.4 is available — see the release notes", "version": "2.3.4", "tag": "v2.3.4",
                "url": "https://github.com/SHOBS-S/SHOBS-P/releases/tag/v2.3.4", "highlights": [], "app": "SHOBS-P"}
        app.show_update(note)
        with mock.patch.object(app, "open_link") as ol:
            app.open_update()
            ol.assert_called_once_with(note["url"], "the release page")
        app.update_var.set("")
        app.update_note = {}

    def test_catalog_notes_in_log_text(self):
        text = self.app._catalog_times_text({"catalog_notes": ["II/336/apass9 from vizier.cfa.harvard.edu looked "
                                                               "wrong (40 of 40 stars brighter than V 4); not used"]},
                                            {}, {})
        self.assertIn("looked wrong", text)

    def test_export_window_says_result(self):
        app = self.app
        win = self.ca.ExportWindow(app)
        try:
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "x.csv")
                with mock.patch.object(app, "_usable", return_value=[]), \
                        mock.patch.object(app, "_analysis_values", return_value=[]), \
                        mock.patch.object(self.ca.core, "convert_times", return_value=[]), \
                        mock.patch.object(self.ca.core, "write_export", return_value=12):
                    self.assertEqual(win.export(path), path)
                self.assertEqual(win.result.get(), f"Exported 12 rows to {path}")
                with mock.patch.object(app, "_usable", return_value=[]), \
                        mock.patch.object(app, "_analysis_values", return_value=[]), \
                        mock.patch.object(self.ca.core, "convert_times", return_value=[]), \
                        mock.patch.object(self.ca.core, "write_export", side_effect=PermissionError("in use")):
                    self.assertIsNone(win.export(path))
                self.assertIn("Could not write", win.result.get())
        finally:
            win.win.destroy()


if __name__ == "__main__":
    unittest.main()
