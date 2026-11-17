"""2.4.0 step 3 bug fixes: #105 + #82 (the wrong-planet family), #68 (check star = comp), #93 (fit inputs in the
transit reports), #65 (Time of max/min elements from the saved times)."""
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


def _src(name="clearv_app.py"):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


REPORT_PLANET = {"name": "K2-113 b", "host": "K2-113", "period": 5.81766, "t0": 2457824.0, "k": 0.1, "a_rs": 12.0,
                 "b": 0.3}


def _report():
    return {"header": {"SOFTWARE": "SHOBS-P 2.1.8", "OBSDATE": "06-OCT-2026"}, "t": [2461320.5 + i * 0.002
                                                                                   for i in range(30)],
            "planet": dict(REPORT_PLANET), "path": "AAVSO_K2-113b_06-OCT-2026_SHOBS-P.txt", "results": {}}


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestWrongPlanet(unittest.TestCase):
    """#105 + #82."""

    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test_240_e.log")
        cls.app = ca.App()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        app = self.app
        app.transit_page.report = None
        app.transit_page._show_source()
        app.observations = []
        for v in app._PLANET_VARS:
            getattr(app, v).set("")
        app.planet_info = {}

    def _hd189733(self):
        app = self.app
        app.star_id.set("HD 189733")
        app.ra_hours.set("20.011")
        app.dec_deg.set("22.71")
        app.planet_name.set("HD 189733 b")
        app.pl_period.set("2.21857567")
        app.pl_t0.set("2458334.990899")
        app.pl_k.set("0.1504")
        app.pl_a_rs.set("8.7")
        app.pl_b.set("0.66")
        app.planet_info = {"name": "HD 189733 b", "host": "HD 189733"}

    # ---- #105 ----
    def test_input_planet_never_the_reports(self):
        self._hd189733()
        tp = self.app.transit_page
        tp.report = _report()
        self.assertEqual(tp.current_planet()["name"], "K2-113 b")   # the page's own fit: the report's
        self.assertEqual(tp.input_planet()["name"], "HD 189733 b")  # a run and its checks: the Input page's

    def test_pre_run_checks_use_the_input_planet(self):
        app, tp = self.app, self.app.transit_page
        self._hd189733()
        tp.report = _report()
        seen = []

        def chk(j0, j1, planet):
            seen.append(planet.get("name"))
            return {"overlap": True, "hours": 0.0}
        old_mode = app.mode_key
        try:
            app.mode_key = "transits"
            with mock.patch.object(self.ca.tcore, "transit_window_check", side_effect=chk), \
                    mock.patch.object(self.ca.core, "read_header", return_value={}), \
                    mock.patch.object(self.ca.core, "mid_jd", return_value=2460880.7), \
                    mock.patch.object(app, "_target_variable_reasons", return_value=[]):
                self.assertTrue(app._mode_guard(["a.fits", "b.fits"]))
        finally:
            app.mode_key = old_mode
        self.assertEqual(seen, ["HD 189733 b"])
        for name in ("    def _transit_in_lights(self", "    def _mode_guard(self"):
            body = _src().split(name, 1)[1].split("\n    def ", 1)[0]
            self.assertNotIn("current_planet()", body)

    def test_new_reduction_closes_the_report(self):
        app, tp = self.app, self.app.transit_page
        self._hd189733()
        tp.report = _report()
        tp._show_source()
        self.assertTrue(tp.banner.get())
        self.assertTrue(app.new_reduction())
        self.assertIsNone(tp.report)
        self.assertEqual(tp.banner.get(), "")
        self.assertIsNone(tp.input_planet())

    def test_kept_line_only_while_photometry_is_open(self):
        tp = self.app.transit_page
        self._hd189733()
        tp.report = _report()
        tp.refresh()
        self.assertNotIn("photometry is kept", tp.banner.get())

    def test_toi_lines_follow_the_report(self):
        app, tp = self.app, self.app.transit_page
        self._hd189733()
        app.planet_info = {"name": "HD 189733 b"}
        app._show_target_status({"planet": "HD 189733 b", "toi": {"text": "TOI-4138.01: KP"},
                                 "exoclock": {"text": "ExoClock: low priority"}})
        self.assertEqual(tp.toi_line.get(), "TOI-4138.01: KP")
        tp.report = _report()
        tp._show_source()
        self.assertEqual(tp.toi_line.get(), "")       # a report of another planet: hidden
        self.assertEqual(tp.exoclock_line.get(), "")
        tp.close_report(quiet=True)
        self.assertEqual(tp.toi_line.get(), "TOI-4138.01: KP")   # back with the photometry's planet
        app._show_target_status({})

    def test_exofop_page_of_a_report_of_another_planet(self):
        app, tp = self.app, self.app.transit_page
        self._hd189733()
        app.planet_info = {"name": "HD 189733 b", "toi": 4138}
        tp.report = _report()
        with mock.patch.object(self.ca.tcore, "exofop_ids", return_value=(None, None)) as ids:
            tp.exofop_page()
        args = ids.call_args[0]
        self.assertEqual(args[0], {})
        self.assertNotIn("HD 189733 b", args[1:])

    # ---- #82 ----
    def _other_star_lookup(self, switched):
        app = self.app
        p = {"name": "WASP-10 b", "host": "WASP-10", "ra": 348.99, "dec": 31.46, "period": 3.0927616,
             "t0": 2457000.1, "k": 0.16, "a_rs": 11.6, "b": 0.3, "source": "NASA Exoplanet Archive"}
        app.planet_name.set("WASP-10 b")
        with mock.patch.object(app, "_switch_star", return_value=switched), \
                mock.patch.object(app, "_start_target_status"):
            app._planet_lookup_done(dict(p), None, "WASP-10 b", quiet=True)

    def test_no_puts_the_previous_planet_back(self):
        app = self.app
        self._hd189733()
        app._status_before_lookup = {}
        before = app._planet_snapshot()
        self._other_star_lookup(None)
        self.assertEqual(app.planet_name.get(), "HD 189733 b")
        self.assertEqual(app.pl_period.get(), before["vars"]["pl_period"])
        self.assertEqual(app.transit_page.input_planet()["name"], "HD 189733 b")
        line = app.planet_source.get()
        self.assertNotIn("answer Yes", line)
        self.assertIn("type it in the Planet box again and press Enter", line)
        self.assertEqual(app.star_id.get(), "HD 189733")

    def test_no_with_no_planet_before_empties_the_values(self):
        app = self.app
        self._hd189733()
        for v in app._PLANET_VARS:
            getattr(app, v).set("")
        self._other_star_lookup(None)
        self.assertEqual(app.planet_name.get(), "WASP-10 b")   # the typed name stays
        self.assertEqual(app.pl_period.get(), "")
        self.assertEqual(app.pl_t0.get(), "")
        self.assertIsNone(app.planet_from_fields())
        self.assertEqual(app.planet_info, {})
        line = app.planet_source.get()
        self.assertNotIn("answer Yes", line)
        self.assertIn("Look up again", line)
        self.assertEqual(app._last_planet_lookup, "WASP-10 b")   # a click elsewhere does not ask straight away

    def test_busy_restores_too(self):
        app = self.app
        self._hd189733()
        self._other_star_lookup("busy")
        self.assertEqual(app.planet_name.get(), "HD 189733 b")
        self.assertIsNone(app._last_planet_lookup)
        self.assertIn("wait for the current step", app.planet_source.get())


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestCheckIsNotAComp(unittest.TestCase):
    """#68."""

    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test_240_e.log")
        cls.app = ca.App()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        app = self.app
        for r in list(self.ca.COMP_ROLES) + ["check"]:
            app.clear_mark(r, redraw=False)
        app.chart_solution, app.chart_placed = None, []

    def _mark(self, role, name, xy):
        setattr(self.app, f"{role}_xy", xy)
        getattr(self.app, f"{role}_name").set(name)

    def test_same_name(self):
        self._mark("comp", "TYC 3308-462-1", (100.0, 100.0))
        self._mark("comp2", "Gaia DR3 439068910332260352", (300.0, 300.0))
        self._mark("check", "Gaia DR3 439068910332260352", (500.0, 500.0))
        clash = self.app.check_comp_clash()
        self.assertEqual([r for r, _ in clash], ["comp2"])
        text = self.app.check_clash_text()
        self.assertIn("Comp 2 and the check star are the same star", text)
        self.assertIn("Run photometry waits", text)

    def test_placeholder_names_are_not_a_match(self):
        self._mark("comp", "comp", (100.0, 100.0))
        self._mark("check", "check", (500.0, 500.0))
        self.assertEqual(self.app.check_comp_clash(), [])

    def test_same_spot_without_a_solve(self):
        self._mark("comp", "A", (100.0, 100.0))
        self._mark("check", "B", (101.0, 100.5))
        self.assertEqual([r for r, _ in self.app.check_comp_clash()], ["comp"])
        self._mark("check", "B", (110.0, 100.0))
        self.assertEqual(self.app.check_comp_clash(), [])

    def test_within_2_arcsec_on_the_sky(self):
        app = self.app
        app.chart_solution, app.chart_placed = {"auto": True}, [(0, 0, {})]
        scale = 1.0 / 3600.0   # 1 arcsec per pixel (in degrees)
        with mock.patch.object(self.ca.core, "pixel_to_sky", side_effect=lambda x, y, sol: (x * scale, y * scale)):
            self._mark("comp", "A", (100.0, 100.0))
            self._mark("check", "B", (101.5, 100.0))    # 1.5 arcsec: the same star
            self.assertEqual([r for r, _ in app.check_comp_clash()], ["comp"])
            self.assertIn("1.5\u2033 apart", app.check_clash_text())
            self._mark("check", "B", (103.0, 100.0))    # 3 arcsec: another star
            self.assertEqual(app.check_comp_clash(), [])

    def test_said_at_pick_time(self):
        app = self.app
        self._mark("check", "Gaia DR3 1", (500.0, 500.0))
        self._mark("comp2", "Gaia DR3 1", (500.0, 500.0))
        with mock.patch.object(self.ca.messagebox, "showwarning") as box:
            self.assertTrue(app._say_check_clash("comp2"))
        box.assert_called_once()
        self.assertTrue(app._slot_notes["comp2"][1].startswith("\u26a0 Comp 2 and the check star"))
        self.assertTrue(app.status.get().startswith("\u26a0 Comp 2 and the check star"))
        with mock.patch.object(self.ca.messagebox, "showwarning") as box:
            self.assertFalse(app._say_check_clash("target"))
        box.assert_not_called()

    def test_run_refuses(self):
        body = _src().split("    def run_photometry(self", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("clash = self.check_clash_text() if check is not None else \"\"", body)
        self.assertIn("raise ValueError(\"\\u26a0 \" + clash)", body)
        # and at pick time: a click and a typed name or position both say it
        src = _src()
        self.assertIn("if not self._say_check_clash(mode):", src)
        set_slot = src.split("    def _set_slot(self", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("self._say_check_clash(role)", set_slot)


if __name__ == "__main__":
    unittest.main()
