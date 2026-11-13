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
    def test_helper(self):
        import clearv_app as ca
        ok = lambda h, t: ca.App._faint_catalog_pick_ok(ca.App, h, t)   # noqa: E731
        self.assertFalse(ok(FAINT, BRIGHT))                       # 5.3 mag fainter: not the star you see
        self.assertTrue(ok(dict(FAINT, mag=14.0), BRIGHT))        # 1.3 mag: a faint comp beside a bright star
        self.assertTrue(ok(FAINT, None))                          # neighbor not in the chart: rule as before
        self.assertTrue(ok({"label": "x"}, BRIGHT))               # magnitude unknown: rule as before
        self.assertFalse(ok({"label": "x", "bright": 18.0}, {"label": "y", "bright": 12.0}))


def _star(img, x0, y0, peak, alpha=1.6, beta=1.8):
    # Moffat: a real star's wings are wider than a Gaussian's (what fooled the 2.2.8 blend test on the night)
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    img += peak * (1.0 + ((xx - x0) ** 2 + (yy - y0) ** 2) / (alpha * alpha)) ** (-beta)


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestClick(unittest.TestCase):
    def _click(self, faint_mag, at):
        import clearv_app as ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test234fp.log")
        app = ca.App()
        try:
            app.set_mode("variables")
            rng = np.random.default_rng(3)
            img = 1000.0 + rng.normal(0, 10, (120, 120))
            _star(img, 50.0, 60.0, 30000.0)
            # the faint star's own light: none for V 18, a visible shoulder for the 'real faint comp' case
            if faint_mag < 15:
                _star(img, 58.0, 60.0, 30000.0 * 10 ** (-0.4 * (faint_mag - 12.651)))
            app.preview = img
            app.preview_is_debayered = True
            app.chart_placed = [(50.0, 60.0, dict(BRIGHT)), (58.0, 60.0, dict(FAINT, mag=faint_mag))]
            app.target_xy = (20.0, 20.0)
            app.pick_mode.set("comp")
            app.on_image_click(types.SimpleNamespace(xdata=at[0], ydata=at[1], button=1))
            role = ca.COMP_ROLES[0]
            return app, getattr(app, f"{role}_name").get(), getattr(app, f"{role}_xy")
        finally:
            app.destroy()

    def test_invisible_v18_neighbor_loses(self):
        for x in (55.0, 56.5, 57.5, 58.0):
            _app, name, xy = self._click(17.977, (x, 60.0))
            self.assertEqual(name, BRIGHT["label"], f"click at x={x}")
            self.assertLess(abs(xy[0] - 50.0), 1.0, f"click at x={x}")

    def test_centre_click_unchanged(self):
        _app, name, _xy = self._click(17.977, (50.4, 60.2))
        self.assertEqual(name, BRIGHT["label"])

    def test_visible_faint_comp_still_kept(self):
        # 2.2.18 kept: a real comp 1.3 mag fainter right beside a bright star, clicked on its own spot, stays that star
        _app, name, _xy = self._click(13.95, (58.0, 60.0))
        self.assertEqual(name, FAINT["label"])


if __name__ == "__main__":
    unittest.main()
