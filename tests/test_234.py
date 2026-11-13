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


if __name__ == "__main__":
    unittest.main()
