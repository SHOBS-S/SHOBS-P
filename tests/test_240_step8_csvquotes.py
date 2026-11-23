"""2.4.0 step 8 regression (23 Nov 2026): the times-of-extremum CSV reader must honour quoted fields, and a save must
keep hand-added '#' lines and the full text of rows already in the file (GSC 03308-00462: three campaign notes with
commas inside quotes were cut at the first comma, and the '# Corrected …' line was dropped)."""
import os
import tempfile
import unittest

import photometry_core as core

HEAD = ("star,type,hjd,bjd_tdb,jd_utc,error_d,error_min,method,cycle,o_c_d,o_c_min,period_d,epoch,elements,night,"
        "n_points,window_min,kvw_jd,kvw_err,poly_jd,poly_err,fourier_jd,fourier_err,time_note,note")
LONG = ("remeasured 14 Nov 2026 on series _16.3 (r 16.3 px, colour-matched comps); this time and error are as sent "
        "to Campaign #938 (Saragrace confirmed 16 Nov); fit details not recorded")


class QuotedNotes(unittest.TestCase):
    def _file(self, d):
        path = os.path.join(d, "X_times_of_extremum.csv")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("# Created 091439Z NOV 26 by SHOBS-P\r\n# Updated 151341Z NOV 26 by SHOBS-P\r\n"
                     "# Corrected 171430Z NOV 26 by hand: three maxima as the campaign holds them\r\n")
            fh.write(HEAD + "\r\n")
            fh.write("X,max,2461353.81047,2461353.81125,2461353.80573,0.01632,23.50,,0,-0.00208,-3.00,0.14908200,"
                     f"2461353.81255,typed HJD,JD2461353,,,,,,,,,,\"{LONG}\"\r\n")
            fh.write("X,max,2461358.75799,2461358.75876,2461358.75320,0.00100,1.44,polynomial deg 4 + bootstrap,33,"
                     "0.02573,37.06,0.14908200,2461353.81255,typed HJD,JD2461358,18,27.47,2461358.75239,0.00060,"
                     "2461358.75320,0.00100,,,,\r\n")
        return path

    def test_read_keeps_quoted_note_whole(self):
        with tempfile.TemporaryDirectory() as d:
            rows = core.read_extremum_csv(self._file(d))
            self.assertEqual(rows[0]["note"], LONG)
            self.assertEqual(rows[0]["night"], "JD2461353")
            self.assertAlmostEqual(rows[0]["period_d"], 0.149082)

    def test_save_keeps_notes_and_comment_lines(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._file(d)
            status = core.save_extremum_row(path, {"star": "X", "type": "max", "hjd": 2461358.75799,
                                                   "error_min": 4.14, "night": "JD2461358",
                                                   "noise_period_d": 0.14904504, "noise_period_source": "series",
                                                   "note": "n" * 300})
            self.assertEqual(status, "replaced")
            text = open(path, encoding="ascii").read()
            self.assertIn("# Corrected 171430Z NOV 26 by hand", text)
            self.assertEqual(text.count("# Updated"), 1)
            self.assertTrue(text.startswith("# Created 091439Z NOV 26"))
            self.assertNotIn('"', text)   # still written without quoting, so 2.3.x reads it
            rows = core.read_extremum_csv(path)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["note"], LONG.replace(",", ";"))   # full length, commas as ';'
            self.assertEqual(len(rows[1]["note"]), 160)                  # only the new row's text is capped
            self.assertEqual(rows[1]["noise_period_source"], "series")
            # a plain comma split (2.3.x's reader) sees the same number of fields on every line
            body = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
            self.assertEqual(len({len(ln.split(",")) for ln in body}), 1)

    def test_second_save_changes_nothing_else(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._file(d)
            core.save_extremum_row(path, {"star": "X", "type": "max", "hjd": 2461359.77959, "error_min": 5.75})
            first = core.read_extremum_csv(path)
            core.save_extremum_row(path, {"star": "X", "type": "max", "hjd": 2461359.77959, "error_min": 5.75})
            second = core.read_extremum_csv(path)
            self.assertEqual([r["note"] for r in first], [r["note"] for r in second])
            self.assertEqual(open(path, encoding="ascii").read().count("# Corrected"), 1)


if __name__ == "__main__":
    unittest.main()
