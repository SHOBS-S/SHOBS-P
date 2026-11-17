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

    # ---- #105: the NEB check never mixes two stars ----
    def test_neb_one_star(self):
        import transit_ui as tu
        app, tp = self.app, self.app.transit_page
        self._hd189733()
        tp.report = _report()                     # K2-113 b: another star than the Input page's
        self.assertTrue(tu.NebWindow.report_of_another_star(tp))
        with mock.patch.object(tu.messagebox, "showinfo") as box, mock.patch.object(tu, "Toplevel") as top:
            tu.NebWindow(app, tp)
        top.assert_not_called()
        self.assertIn("The report shown is of K2-113 b", box.call_args[0][1])
        self.assertIn("HD 189733 b", box.call_args[0][1])
        # a report of the Input page's own planet: its depth with the Input page's position
        rep = _report()
        rep["planet"] = dict(rep["planet"], name="HD 189733 b", host="HD 189733")
        tp.report = rep
        self.assertFalse(tu.NebWindow.report_of_another_star(tp))
        self.assertEqual(tp._report_radec(), app.target_radec_deg())
        tp.report = None
        self.assertFalse(tu.NebWindow.report_of_another_star(tp))

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


class TestFitInputs(unittest.TestCase):
    """#93: the exact inputs at full precision in the fit text, the fit CSV header and the settings file."""

    @classmethod
    def setUpClass(cls):
        import numpy as np
        import transit_core as tc
        sys.path.insert(0, os.path.join(ROOT, "tests"))
        from test_230_b import PLANET, synth_night
        cls.tc, cls.np = tc, np
        t, f, e, am, _af = synth_night(0.0, seed=3)
        cls.data = (t, f, e, am)
        cls.planet = dict(PLANET, a_rs=5.344123456789, a_rs_err=0.16, b=0.0831234567, b_err=0.07,
                          source="NASA Exoplanet Archive", reference="Wang et al. 2019",
                          looked_up="15 Nov 2026 14:03 UTC")
        cls.r = tc.fit_transit(t, f, e, am, cls.planet, mcmc_steps=200)

    def test_lines_full_precision(self):
        lines = self.tc.fit_inputs_lines(self.r)
        text = "\n".join(lines)
        self.assertTrue(text.isascii(), text)
        self.assertIn("Fit input period: 2.21857 +/- 1e-06 d", text)
        self.assertIn("Fit input t0: 2461000.0 +/- 0.0002 (BJD_TDB)", text)
        self.assertIn("Fit input a/R*: 5.344123456789 +/- 0.16", text)
        self.assertIn("Fit input b: 0.0831234567 +/- 0.07", text)
        self.assertIn("Fit input published Rp/R*: 0.155", text)
        self.assertIn("Fit input T14: 0.076 d", text)
        self.assertIn("Fit input Teff for limb darkening: 5050.0 K; band V / clear table", text)
        u1, u2, w = self.r["ld_prior"]
        self.assertIn(f"Fit input limb darkening prior: u1 {u1!r}, u2 {u2!r}, width {w!r}", text)
        self.assertIn("Fit input shape priors used: a_rs 5.344123456789 +/- 0.16", text)
        self.assertIn("Fit input catalog: NASA Exoplanet Archive (Wang et al. 2019)", text)
        self.assertIn("Fit input lookup: 15 Nov 2026 14:03 UTC (live)", text)

    def test_cache_and_report_sources(self):
        r = dict(self.r, planet=dict(self.r["planet"], from_cache="12 Oct 2026", looked_up=None))
        self.assertIn("from the lookup cache (fetched 12 Oct 2026", "\n".join(self.tc.fit_inputs_lines(r)))
        r = dict(self.r, planet=dict(self.r["planet"], source="the report's priors", looked_up=None))
        self.assertIn("not looked up (the report's own values)", "\n".join(self.tc.fit_inputs_lines(r)))
        r = dict(self.r, planet=dict(self.r["planet"], teff=None, looked_up=None, source="Input page"))
        text = "\n".join(self.tc.fit_inputs_lines(r))
        self.assertIn("not known (5800 K assumed)", text)
        self.assertIn("Fit input lookup: typed on the Input page", text)

    def test_fit_text_and_csv(self):
        import transit_ui
        text = self.tc.fit_text(self.r, {"version": "2.4.0", "filter": "V"})
        self.assertIn("\nFit inputs (full precision)\n  period: 2.21857 +/- 1e-06 d\n", text)
        self.assertIn("  lookup: 15 Nov 2026 14:03 UTC (live)", text)
        com = transit_ui.fit_csv_comments(self.r, {"filter": "V"})
        self.assertIn("# Fit input a/R*: 5.344123456789 +/- 0.16", com)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "Synthb_V_01-JAN-2026_SHOBS-P_transit_fit.csv")
            transit_ui.write_fit_csv(path, self.r, meta={"filter": "V"})
            import multinight
            night = multinight.load_fit_csv(path)   # the extra comment lines never upset the reader
            self.assertEqual(len(night["t"]), len(self.r["t"]))
            self.assertEqual(night["planet"], "Synth b")

    def test_no_fit_number_changes(self):
        t, f, e, am = self.data
        plain = {k: v for k, v in self.planet.items() if k not in ("looked_up", "source", "reference")}
        r2 = self.tc.fit_transit(t, f, e, am, plain, mcmc_steps=200)
        for name in ("tc", "k", "depth", "a_rs", "b"):
            self.assertEqual(self.r["summary"][name], r2["summary"][name])

    def test_settings_file_gets_the_inputs(self):
        body = _src("transit_ui.py").split("    def _write_settings(self", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("tc.fit_inputs_lines(self.result)", body)
        # the lookup stamp reaches the fit through the Input page
        self.assertIn('"looked_up": info.get("looked_up")', _src())


class TestSavedTimesLines(unittest.TestCase):
    """#65: the saved-times list's O−C against the current elements."""
    ROWS = [
        {"star": "GSC 03308-00462", "type": "max", "hjd": 2461353.81255, "error_min": 20.8, "o_c_min": 9.4,
         "period_d": 0.149082, "elements": "VSX HJD", "night": "JD2461353"},
        {"star": "GSC 03308-00462", "type": "max", "hjd": 2461354.85761, "error_min": 24.4, "o_c_min": -6.8,
         "period_d": 0.11664, "elements": "saved HJD", "night": "JD2461354"},
    ]

    def test_against_the_boxes(self):
        import photometry_core as core
        lines = core.extremum_times_lines(self.ROWS, elements=(0.11664, 2461353.81255, "HJD"))
        self.assertIn("O\u2212C +0.0 min (saved +9.4 against VSX HJD)", lines[0])   # the epoch itself: 0
        oc = core.extremum_row_oc(self.ROWS[1], 0.11664, 2461353.81255, "HJD")
        self.assertIn(f"O\u2212C {oc:+.1f} min", lines[1])
        # the diagram's own sum gives the same
        want = core.o_minus_c(2461354.85761, 0.11664, 2461353.81255)["oc_min"]
        self.assertAlmostEqual(oc, want)

    def test_without_elements_as_saved(self):
        import photometry_core as core
        lines = core.extremum_times_lines(self.ROWS)
        self.assertIn("O\u2212C +9.4 min", lines[0])
        self.assertNotIn("saved", lines[0])

    def test_minimum_half_cycles_and_bjd(self):
        import photometry_core as core
        row = {"type": "min", "hjd": 2461000.55, "bjd_tdb": 2461000.551, "elements": "saved BJD"}
        self.assertAlmostEqual(core.extremum_row_oc(row, 1.0, 2461000.0, "BJD"), 0.051 * 1440, places=4)   # half cycle 0.5
        self.assertIsNone(core.extremum_row_oc({"type": "max"}, 1.0, 2461000.0))


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestExtremumWindowOpens(unittest.TestCase):
    """#65: the window opens on the saved times' elements; Type refreshes the epoch; VSX stays the fallback."""

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

    def _window(self, rows, d):
        import photometry_core as core
        app = self.app
        path = os.path.join(d, core.extremum_csv_name("GSC 03308-00462"))
        for r in rows:
            core.save_extremum_row(path, dict(r))
        app.star_id.set("GSC 03308-00462")
        app.catalog_period, app.catalog_epoch = 0.149082, 2452000.5
        app.catalog_var_type = "DSCT"
        app.extremum_csv, app.extremum_csv_star = path, "GSC 03308-00462"
        return self.ca.ExtremumWindow(app)

    def test_open_type_switch_and_list(self):
        rows = TestSavedTimesLines.ROWS + [
            {"star": "GSC 03308-00462", "type": "min", "hjd": 2461353.87, "error_min": 15.0, "o_c_min": 1.0,
             "period_d": 0.11664, "elements": "saved HJD", "night": "JD2461353"}]
        with tempfile.TemporaryDirectory() as d:
            w = self._window(rows, d)
            try:
                self.assertEqual(w.kind.get(), "maximum")
                self.assertEqual(w.epoch.get(), "2461353.81255")   # the first saved maximum
                self.assertEqual(w.period.get(), "0.11664000")      # the period last used
                self.assertEqual(w._elements_source(*w._elements()), "saved")
                self.assertIn("first saved maximum", w.source.get())
                text = w.csv_text.get()
                self.assertIn("O\u2212C against P 0.11664000, E 2461353.81255 (HJD), as in the diagram:", text)
                self.assertIn("(saved +9.4 against VSX HJD)", text)
                w.kind.set("minimum")
                self.assertEqual(w.epoch.get(), "2461353.87000")    # that type's first saved time
                self.assertIn("first saved minimum", w.source.get())
                # typed elements are the user's own: a Type switch leaves them
                w.epoch.set("2461353.90000")
                w.kind.set("maximum")
                self.assertEqual(w.epoch.get(), "2461353.90000")
            finally:
                w.win.destroy()
                self.app.star_id.set("")

    def test_vsx_is_the_fallback(self):
        rows = [{"star": "GSC 03308-00462", "type": "min", "hjd": 2461353.87, "period_d": 0.11664,
                 "elements": "saved HJD"}]
        with tempfile.TemporaryDirectory() as d:
            w = self._window(rows, d)
            try:
                self.assertEqual(w.kind.get(), "maximum")
                self.assertEqual(w.epoch.get(), "2452000.50000")   # no saved maximum: VSX's
                self.assertEqual(w.period.get(), "0.14908200")
                self.assertIn("no saved maxima yet; VSX elements", w.source.get())
            finally:
                w.win.destroy()
                self.app.star_id.set("")

    def test_vsx_lookup_type_does_not_refill(self):
        with tempfile.TemporaryDirectory() as d:
            w = self._window(TestSavedTimesLines.ROWS, d)
            try:
                w._lookup_done({"period_days": 0.149082, "epoch_jd": 2452000.5, "var_type": "EA"}, None)
                self.assertEqual(w.kind.get(), "minimum")
                self.assertEqual(w.epoch.get(), "2452000.50000")   # VSX's, as asked for
            finally:
                w.win.destroy()
                self.app.star_id.set("")


if __name__ == "__main__":
    unittest.main()
