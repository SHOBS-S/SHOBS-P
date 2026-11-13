"""2.3.4 (John's run-through, GSC 03308-00462 9 Nov, bin 2): a comp click a few px off a V 12.65 star took the name and
V of an invisible V 18 Gaia star 8 px away (the 2.2.18 faint-comp-beside-a-bright-star rule). Such a catalog star is
not what the user sees: the click centers on the bright star and takes its name."""
import os
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["SHOBS_ONSKY_OFF"] = "1"

import numpy as np  # noqa: E402
import matplotlib  # noqa: E402
matplotlib.use("Agg")

try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False

BRIGHT = {"label": "APASS J024802.6+504838", "mag": 12.651, "catalog": "APASS", "ra": 42.0106, "dec": 50.8106}
FAINT = {"label": "Gaia DR3 439030152547276544", "mag": 17.977, "catalog": "Gaia DR3", "ra": 42.0075, "dec": 50.8093}


class TestRule(unittest.TestCase):
    def test_dmag_like_with_like(self):
        import clearv_app as ca
        dm = ca.App._chart_dmag
        self.assertAlmostEqual(dm({"bright": 18.0, "mag": 19.0}, {"bright": 12.0, "mag": 12.5}), 6.0)   # broad band first
        self.assertAlmostEqual(dm({"mags": {"V": 17.0}, "mag": 18.0}, {"mags": {"V": 12.0}, "bright": 11.0}), 5.0)
        self.assertAlmostEqual(dm({"mag": 15.0}, {"mag": 12.0}), 3.0)
        self.assertIsNone(dm({"bright": 15.0}, {"mag": 12.0}))   # never one band against another
        self.assertIsNone(dm({"label": "x"}, {"mag": 12.0}))


def _star(img, x0, y0, peak, alpha=1.6, beta=1.8, elong=0.0):
    # Moffat: a real star's wings are wider than a Gaussian's (what fooled the 2.2.8 blend test on the night);
    # elong stretches it along x (tracking, wind), the axis the faint catalog star sits on
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    ax = alpha * (1.0 + elong)
    img += peak * (1.0 + ((xx - x0) / ax) ** 2 + ((yy - y0) / alpha) ** 2) ** (-beta)


FIELD = [(15.0, 100.0), (100.0, 15.0), (105.0, 105.0), (20.0, 30.0), (95.0, 70.0), (60.0, 105.0)]


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestClick(unittest.TestCase):
    def _click(self, faint_mag, at, faint_light=None, sep=8.0, bright_in_chart=True, elong=0.0):
        """A V 12.65 star at (50, 60) and a catalog star sep px to its right. faint_light: give the faint star its own
        light on the image (default: only when brighter than V 15, as a V 18 star is not seen at bin 2)."""
        import clearv_app as ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test234fp.log")
        app = ca.App()
        try:
            app.set_mode("variables")
            rng = np.random.default_rng(3)
            img = 1000.0 + rng.normal(0, 10, (120, 120))
            _star(img, 50.0, 60.0, 30000.0, elong=elong)
            for fx, fy in FIELD:   # isolated field stars, so the seeing is measured (re-review S-3)
                _star(img, fx, fy, 8000.0, elong=elong)
            if faint_light if faint_light is not None else faint_mag < 15:
                _star(img, 50.0 + sep, 60.0, 30000.0 * 10 ** (-0.4 * (faint_mag - 12.651)))
            app.preview = img
            app.preview_is_debayered = True
            app.chart_placed = ([(50.0, 60.0, dict(BRIGHT))] if bright_in_chart else []) + \
                [(50.0 + sep, 60.0, dict(FAINT, mag=faint_mag, bright=faint_mag))]
            if bright_in_chart:
                app.chart_placed[0][2]["bright"] = 12.651
            app.target_xy = (20.0, 20.0)
            app.pick_mode.set("comp")
            self.seeing = app._seeing()
            app.on_image_click(types.SimpleNamespace(xdata=at[0], ydata=at[1], button=1))
            role = ca.COMP_ROLES[0]
            return getattr(app, f"{role}_name").get(), getattr(app, f"{role}_xy")
        finally:
            app.destroy()

    def test_invisible_v18_neighbor_loses(self):
        # John's 9 Nov bin-2 case: clicks a few px right of the comp, up to the faint star's catalog spot
        for x in (55.0, 56.5, 57.5, 58.0):
            name, xy = self._click(17.977, (x, 60.0))
            self.assertEqual(name, BRIGHT["label"], f"click at x={x}")
            self.assertLess(abs(xy[0] - 50.0), 1.0, f"click at x={x}")

    def test_centre_click_unchanged(self):
        name, _xy = self._click(17.977, (50.4, 60.2))
        self.assertEqual(name, BRIGHT["label"])

    def test_seen_faint_comp_kept_however_faint(self):
        # Review M1: a star that is there on the image, 3-4 mag fainter and 5-8 px from a bright one, clicked dead on,
        # is still that star (the 2.2.18 rule)
        for dmag, sep in ((3.0, 6.0), (3.5, 6.0), (4.0, 8.0)):
            name, xy = self._click(12.651 + dmag, (50.0 + sep, 60.0), faint_light=True, sep=sep)
            self.assertEqual(name, FAINT["label"], f"dmag {dmag} sep {sep}")
            self.assertLess(abs(xy[0] - (50.0 + sep)), 2.5, f"dmag {dmag} sep {sep}")

    def test_seeing_measured(self):
        import math
        self._click(17.977, (50.4, 60.2))
        self.assertTrue(math.isfinite(self.seeing), "the test field must give a measured seeing")

    def test_elongated_star_wing_is_not_light(self):
        # Re-review M-A: a star stretched 10-20 % along the line to the invisible V 18 catalog star still wins
        for elong in (0.10, 0.20):
            for x in (55.0, 56.5, 57.5, 58.0):
                name, _xy = self._click(17.977, (x, 60.0), elong=elong)
                self.assertEqual(name, BRIGHT["label"], f"elong {elong} click at x={x}")

    def test_typed_name_with_catalog_a_few_px_off_still_fills(self):
        # Re-review M-B regression guard: a comp placed by name whose catalog position is 3 px off its star keeps
        # the catalog V (the 2.3.4 light test is only for clicks)
        import clearv_app as ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test234fp.log")
        app = ca.App()
        try:
            app.set_mode("variables")
            rng = np.random.default_rng(5)
            img = 1000.0 + rng.normal(0, 10, (120, 120))
            _star(img, 50.0, 60.0, 30000.0)
            for fx, fy in FIELD:
                _star(img, fx, fy, 8000.0)
            app.preview = img
            app.preview_is_debayered = True
            app.chart_placed = [(53.0, 60.0, dict(BRIGHT, bright=12.651))]
            app.target_xy = (20.0, 20.0)
            role = ca.COMP_ROLES[0]
            app._set_slot(role, (53.0, 60.0), BRIGHT["label"], vet=False)
            self.assertEqual(getattr(app, f"{role}_name").get(), BRIGHT["label"])
            self.assertEqual(getattr(app, f"{role}_mag").get(), "12.651")
        finally:
            app.destroy()

if __name__ == "__main__":
    unittest.main()
