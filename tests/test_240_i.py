"""2.4.0 step 3, Fable review fixes: N2 (the drift read shows the busy bar and keeps the previous suggestions), N3 (a
drift check that fails is said), N4 (a partial or differently binned series is not taken for the night's drift), N7
(the same name on two stars clearly apart), and the nits (fit inputs never cost the settings file, typed-over values
named on the lookup line, the NEB check of a same-star report without RA/Dec, the series drift labels cached), and
#106 (the boxes' rounding no longer drops a looked-up error from the fit's priors), #107 (MicroObservatory pixel
from a binned header scale)."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import photometry_core as core    # noqa: E402
import test_240_f as F            # noqa: E402  (the drifting test night)

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


class TestDriftProgressCore(unittest.TestCase):
    @unittest.skipIf(F.fits is None, "needs astropy")
    def test_progress_and_few_stars(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = F.write_night(tmp, F.FIELD, F.DRIFT)
            calls = []
            res = core.frame_drift(paths[0], paths[1:], progress=lambda i, n: calls.append((i, n)))
            self.assertEqual(calls, [(i, 5) for i in range(5)])
            self.assertNotIn("why", res)
            # a reference frame with no stars: nothing can be lined up, and the result says why
            blank = os.path.join(tmp, "blank.fits")
            F.fits.PrimaryHDU(F.np.full((F.H, F.W), 300.0, F.np.float32)).writeto(blank)
            res = core.frame_drift(blank, paths[1:])
            self.assertEqual(res["why"], "few stars")
            self.assertTrue(all(reg is None for _p, reg in res["regs"]))


@unittest.skipUnless(HAVE_TK and F.fits is not None, "needs a display and astropy")
class TestDriftReadFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test240i.log")
        cls.ca = ca
        cls.app = ca.App()
        cls.app.set_mode("variables")
        cls.app.update()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.paths = F.write_night(cls.tmp.name, F.FIELD, F.DRIFT)
        cls.lines = []
        orig = cls.app.log

        def log(text, *a, **k):
            cls.lines.append(text)
            return orig(text, *a, **k)
        cls.app.log = log
        cls.helper = F.TestSuggestionsFollowTheDrift("test_drifter_never_suggested_and_said_plainly")
        cls.helper.app, cls.helper.paths, cls.helper.tmp, cls.helper.lines = cls.app, cls.paths, cls.tmp, cls.lines

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        cls.tmp.cleanup()

    def _setup(self, **kw):
        app = self.helper._setup(**kw)
        app._drift_cache = None
        app._series_drift_memo = None
        app.night_bin = {}
        app.masters_binning = None   # step 6c: a sibling test's masters (binning 2) are not this test's
        return app

    def _wait(self, app, timeout=30.0):
        t0 = time.time()
        while getattr(app, "_drift_cache", None) and app._drift_cache.get("state") == "loading" \
                and time.time() - t0 < timeout:
            app.update()
            time.sleep(0.02)
        app.update()

    def _series(self, frames, night="2026-10-24"):
        obs = []
        for p, (dx, dy) in zip(self.paths[:frames], F.DRIFT[:frames]):
            o = core.Obs(jd=2461337.5, mag=0.0, merr=0.01, cmag=0.0, kmag=None, airmass=None, path=p, exptime=60.0,
                         flux_var=1.0, flux_comp=1.0)
            o.pos = {"t": [210.0 + dx, 150.0 + dy]}
            o.night = night
            obs.append(o)
        return obs

    # ---- N2 ----
    def test_previous_suggestions_stay_and_the_busy_bar_shows(self):
        app = self._setup()
        old = [("OLD 1", 0.1, 330.0, 90.0), ("OLD 2", -0.2, 300.0, 220.0)]
        app.suggested_comps = list(old)
        app._suggest_for = (app.target_xy, app.preview_path)   # step 6c (Fable): made for this target and frame
        app._suggest_comps()
        self.assertTrue(app._suggest_drift_pending)
        self.assertEqual(app.suggested_comps, old)   # kept while the drift frames are read
        cache = app._drift_cache
        tok = cache.get("tok")
        self.assertIsNotNone(tok)
        self.assertTrue(app.busy_bar.jobs[tok].startswith("Checking the night's drift"))
        app._drift_progress(cache, 2, 6)
        self.assertEqual(app.busy_bar.jobs[tok], "Checking the night's drift… frame 3 of 6")
        self._wait(app)
        self.assertNotIn(tok, app.busy_bar.jobs)   # the bar's job ends with the read
        self.assertFalse(app._suggest_drift_pending)
        self.assertEqual({n for n, *_ in app.suggested_comps}, {"SAFE A", "SAFE B", "SAFE C"})

    def test_replaced_read_ends_its_busy_job(self):
        app = self._setup()
        app._suggest_comps()
        first = app._drift_cache
        tok = first["tok"]
        app._drift_cache = None   # a newer frame replaces the read: its worker stops and its job must still end
        app._drift_done(first, {"raw_shape": (F.H, F.W), "regs": []})
        self.assertNotIn(tok, app.busy_bar.jobs)
        self._wait(app)

    def test_manual_timing_is_measured(self):
        text = _src(os.path.join("docs", "manual", "08-photometry-basics.md"))
        self.assertNotIn("takes about a second", text)
        self.assertIn("about 7 seconds", text)
        body = _src().split("    def _suggest_comps(self", 1)[1].split("\n    def ", 1)[0]
        self.assertNotIn("(a second or two)", body)

    # ---- N3 ----
    def test_a_failed_check_is_said(self):
        app = self._setup()
        cases = [({"raw_shape": (F.H, F.W), "regs": [(p, None) for p in self.paths[1:]], "why": "few stars"},
                  "too few stars were found on this frame"),
                 ({"raw_shape": (F.H, F.W), "regs": [(p, None) for p in self.paths[1:]]},
                  "none of the other lights could be lined up with this frame"),
                 ({"error": "OSError: gone"}, "the lights could not be read"),
                 ({"raw_shape": (F.H * 2, F.W), "regs": []}, "not the same size as the lights")]
        for res, words in cases:
            app._drift_cache = None
            self.lines.clear()
            with mock.patch.object(self.ca.core, "frame_drift", return_value=res):
                app._suggest_comps()
                self._wait(app)
            said = [t for t in self.lines if t.startswith("The night's drift could not be checked")]
            self.assertEqual(len(said), 1, (words, self.lines))
            self.assertIn(words, said[0])
            self.assertTrue(said[0].endswith("only this frame's edge was checked."), said[0])
            self.assertNotIn("⚠", said[0])   # a plain note
        # a check that worked says nothing of the kind
        app._drift_cache = None
        self.lines.clear()
        app._suggest_comps()
        self._wait(app)
        self.assertFalse([t for t in self.lines if t.startswith("The night's drift could not")], self.lines)

    # ---- N4 ----
    def test_partial_series_reads_the_lights(self):
        app = self._setup(observations=self._series(3))   # a run canceled after 3 of 6 frames
        app.night_bin = {"2026-10-24": {"binning": 1}}
        app._suggest_comps()
        self.assertTrue(app._suggest_drift_pending)   # not taken for the night: the lights are read
        self._wait(app)
        said = [t for t in self.lines if t.startswith("Suggested comps")][-1]
        self.assertIn("checked on all 6 lights", said)
        self.assertNotIn("loaded series", said)

    def test_other_binning_or_unknown_reads_the_lights(self):
        for night_bin in ({"2026-10-24": {"binning": 2}}, {}):
            app = self._setup(observations=self._series(6))
            app.night_bin = night_bin
            self.assertIsNone(app._series_drift(start=False), night_bin)   # the frames would be read
        app = self._setup(observations=self._series(6))
        app.night_bin = {"2026-10-24": {"binning": 1, "pattern": app.pattern.get()}}   # re-review nit: grid recorded
        got = app._series_drift(start=False)
        self.assertEqual(got["how"], "series")
        self.assertIs(app._series_drift(start=False), got)   # nit: cached, labels not looked up again
        app.observations[-1].pos = {"t": [100.0, 100.0]}
        self.assertIsNot(app._series_drift(start=False), got)   # a new series position: built again


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestSameNameApart(unittest.TestCase):
    """N7: the same name on two markers clearly apart is two stars sharing a name, not the same star."""

    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test_240_i.log")
        cls.app = ca.App()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        for r in list(self.ca.COMP_ROLES) + ["check"]:
            self.app.clear_mark(r, redraw=False)
        self.app.chart_solution, self.app.chart_placed = None, []

    def _mark(self, role, name, xy):
        setattr(self.app, f"{role}_xy", xy)
        getattr(self.app, f"{role}_name").set(name)

    def test_far_apart_without_a_solve(self):
        self._mark("comp2", "000-BBC-123", (300.0, 300.0))
        self._mark("check", "000-BBC-123", (500.0, 300.0))
        self.assertEqual([r for r, _ in self.app.check_comp_clash()], ["comp2"])   # still refused
        text = self.app.check_clash_text()
        self.assertIn("Comp 2 and the check star have the same name", text)
        self.assertIn("200 px apart on the image", text)
        self.assertIn("Type another name", text)
        self.assertNotIn("are the same star", text)
        self.assertTrue(text.endswith("Run photometry waits until then."))

    def test_on_the_sky_and_mixed(self):
        app = self.app
        app.chart_solution, app.chart_placed = {"auto": True}, [(0, 0, {})]
        scale = 1.0 / 3600.0
        with mock.patch.object(self.ca.core, "pixel_to_sky", side_effect=lambda x, y, sol: (x * scale, y * scale)):
            self._mark("check", "000-BBC-123", (100.0, 100.0))
            self._mark("comp", "000-BBC-123", (130.0, 100.0))    # 30 arcsec: two stars with one name
            self._mark("comp3", "000-BBC-123", (104.0, 100.0))   # 4 arcsec, same name: the same star
            text = app.check_clash_text()
        self.assertIn("Comp 3 and the check star are the same star (Comp 3: the same name, 000-BBC-123)", text)
        self.assertIn("Comp 1 and the check star have the same name (Comp 1: 000-BBC-123, but the markers are "
                      "30″ apart)", text)
        self.assertEqual(text.count("Run photometry waits"), 1)


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestNits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test_240_i_nits.log")
        cls.app = ca.App()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        app = self.app
        app.transit_page.report = None
        for v in app._PLANET_VARS:
            getattr(app, v).set("")
        app.ra_hours.set("")
        app.dec_deg.set("")
        app.planet_info = {}

    def _looked_up(self):
        info = {"name": "HD 189733 b", "host": "HD 189733", "period": 2.218575671, "t0": 2458334.9908991,
                "k": 0.15041, "a_rs": 8.7312, "b": 0.6634, "teff": 5050.0, "t14": 0.07527,
                "source": "NASA Exoplanet Archive", "looked_up": "15 Nov 2026 14:03 UTC"}
        self.app.planet_info = dict(info)
        self.app._planet_to_fields(info)
        return info

    def test_typed_over_values_are_named(self):
        import transit_core as tc
        self._looked_up()
        p = self.app.planet_from_fields()
        self.assertNotIn("typed_over", p)   # the boxes' rounding is not typing
        line = [x for x in tc.fit_inputs_lines({"planet": p}) if x.startswith("Fit input lookup")][0]
        self.assertEqual(line, "Fit input lookup: 15 Nov 2026 14:03 UTC (live)")
        self.app.pl_b.set("0.5")
        self.app.pl_period.set("2.2186")
        p = self.app.planet_from_fields()
        self.assertEqual(p["typed_over"], ["period", "b"])
        line = [x for x in tc.fit_inputs_lines({"planet": p}) if x.startswith("Fit input lookup")][0]
        self.assertEqual(line, "Fit input lookup: 15 Nov 2026 14:03 UTC (live); typed over on the Input page "
                               "after it: period, b")

    def test_box_rounding_keeps_the_lookup_errors(self):
        """#106: a/R* and b are shown to 3 decimals, Rp/R* to 5: that rounding is not typing, so the looked-up errors
        reach the fit's priors; a typed value still drops its error."""
        import transit_core as tc
        info = {"name": "HD 189733 b", "host": "HD 189733", "period": 2.218575671, "period_err": 2e-8,
                "t0": 2458334.9908991, "t0_err": 0.00007, "a_rs": 8.7312, "a_rs_err": 0.25, "b": 0.0835,
                "b_err": 0.07, "k": 0.149183, "k_err": 0.0012, "depth": 0.149183 ** 2, "teff": 5050.0,
                "t14": 0.07527, "source": "NASA Exoplanet Archive", "looked_up": "17 Nov 2026 20:00 UTC"}
        self.app.planet_info = dict(info)
        self.app._planet_to_fields(info)
        self.assertEqual(self.app.pl_a_rs.get(), "8.731")   # shown rounded
        p = self.app.planet_from_fields()
        for key in ("period_err", "t0_err", "a_rs_err", "b_err", "k_err"):
            self.assertEqual(p.get(key), info[key], key)
        self.assertNotIn("depth_typed", p)
        self.assertNotIn("typed_over", p)
        pri = tc.prepare_priors(p)
        self.assertAlmostEqual(pri["b"][1], 0.07)                     # the catalog's, not the 0.04 floor
        self.assertAlmostEqual(pri["a_rs"][1], max(0.25, 0.03 * 8.731))   # 0.25 is under the 3 % floor here
        self.app.planet_info = dict(info, a_rs_err=0.4)
        pri = tc.prepare_priors(self.app.planet_from_fields())
        self.assertAlmostEqual(pri["a_rs"][1], 0.4)                   # a wider catalog error is used
        # a typed value still drops its error (and the prior falls back to the floor)
        self.app.planet_info = dict(info)
        self.app.pl_b.set("0.120")
        p = self.app.planet_from_fields()
        self.assertIsNone(p.get("b_err"))
        self.assertAlmostEqual(tc.prepare_priors(p)["b"][1], 0.04)
        self.assertEqual(p.get("a_rs_err"), 0.25)
        self.assertEqual(p["typed_over"], ["b"])

    def test_small_depth_is_not_called_typed(self):
        """#106: a depth under 0.5 % lost the 2.2.13 'not typed' test to the box's 4 decimals (in %)."""
        info = {"name": "X b", "host": "X", "period": 3.1, "depth": 0.001234567, "source": "NASA Exoplanet Archive",
                "looked_up": "17 Nov 2026 20:00 UTC"}
        self.app.planet_info = dict(info)
        self.app._planet_to_fields(info)
        self.assertEqual(self.app.pl_depth.get(), "0.1235")
        self.assertNotIn("depth_typed", self.app.planet_from_fields())
        self.app.pl_depth.set("0.13")
        self.assertTrue(self.app.planet_from_fields().get("depth_typed"))

    def test_fit_inputs_never_cost_the_settings_file(self):
        import transit_ui as tu
        tp = self.app.transit_page
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(tu.tc, "fit_inputs_lines", side_effect=KeyError("period")), \
                mock.patch.object(tp, "settings_lines", return_value=["Aperture radius: 5 px"]), \
                mock.patch.object(tp, "_findings_apply", return_value=False):
            old = tp.result
            tp.result = {"planet": {}}
            try:
                path = tp._write_settings(os.path.join(d, "report.txt"), {})
            finally:
                tp.result = old
            self.assertTrue(path and os.path.exists(path))
            with open(path, encoding="utf-8") as fh:
                self.assertIn("Aperture radius: 5 px", fh.read())

    def test_neb_same_star_report_without_radec(self):
        import transit_ui as tu
        app, tp = self.app, self.app.transit_page
        app.star_id.set("HD 189733")
        app.planet_name.set("HD 189733 b")
        app.planet_info = {"name": "HD 189733 b", "host": "HD 189733"}   # no RA/Dec anywhere yet
        tp.report = {"header": {}, "t": [], "path": "x.txt", "results": {},
                     "planet": {"name": "HD 189733 b", "host": "HD 189733", "period": 2.2186, "k": 0.15}}
        try:
            self.assertFalse(tu.NebWindow.report_of_another_star(tp))
            # 2.4.0 fit-in F4 (m856): an error box now, leading with what to do
            with mock.patch.object(tu.messagebox, "showerror") as box, mock.patch.object(tu, "Toplevel") as top:
                tu.NebWindow(app, tp)
            top.assert_not_called()
            words = box.call_args[0][1]
            self.assertIn("needs the target's RA and Dec. On the Input page press Look up", words)
            self.assertNotIn("The report shown is of", words)
        finally:
            tp.report = None

    def test_saved_times_button_comment(self):
        src = _src()
        self.assertNotIn("not filled by themselves: under the numbers", src)



@unittest.skipUnless(HAVE_TK, "needs a display")
class TestMicroObservatoryPixel(unittest.TestCase):
    """#107: the header's scale is per binned pixel; the Pixel size box gets the camera's pixel, so the solve's
    x XBINNING gives 5.0"/px (it gave 10"/px and no solve on current headers, XBINNING = 2)."""

    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test_240_i_mobs.log")
        cls.app = ca.App()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def _setup(self, header):
        app = self.app
        app._own_setup = None
        # 2.4.0 fit-in F3 (A1): no question any more; the Log line carries the optics words
        asked = []
        with mock.patch.object(self.ca.messagebox, "askyesno") as box, \
                mock.patch.object(app, "log", side_effect=lambda t, *a, **k: asked.append(str(t))):
            app._setup_microobservatory(header, 78)
            box.assert_not_called()
        asked = ["\n".join(t for t in asked if t.startswith("MicroObservatory setup:"))]
        app.preview_header = header
        solve_scale = float(app.pixel_um.get()) / float(app.focal_mm.get()) * 206.265 \
            * self.ca.solve_camera_bin(header)
        return [ln for ln in asked[0].splitlines() if "optics" in ln][0], float(app.pixel_um.get()), solve_scale

    def test_current_headers_xbinning_2(self):
        head = {"TELESCOP": "Cecilia", "OBSERVAT": "Whipple Observatory", "LATITUDE": 31.68, "LONGITUD": -110.88,
                "IM_SCALE": 5.0, "XBINNING": 2, "YBINNING": 2, "XPIXSZ": 13.6, "FOCALLEN": 560,
                "NAXIS1": 650, "NAXIS2": 500}
        line, pixel, scale = self._setup(head)
        self.assertAlmostEqual(pixel, 6.8, places=1)
        self.assertAlmostEqual(scale, 5.0, delta=0.05)
        self.assertIn("6.80 µm (from the header scale; camera pixel, binned 2x2 by the camera, 5.01\"/px)", line)
        self.assertAlmostEqual(self.app._optics_scale_arcsec(), scale, places=6)   # the field image's scale too

    def test_older_headers_without_binning(self):
        head = {"TELESCOP": "MicroObservatory", "XPIXSZ": 13.6, "FOCALLEN": 560, "NAXIS1": 650, "NAXIS2": 500}
        line, pixel, scale = self._setup(head)
        self.assertAlmostEqual(pixel, 13.6, places=1)
        self.assertAlmostEqual(scale, 5.0, delta=0.05)
        self.assertIn("13.60 µm (from the header scale, 5.01\"/px)", line)
        head = dict(head, XBINNING=1)
        self.assertAlmostEqual(self._setup(head)[2], 5.0, delta=0.05)

    def test_no_scale_in_the_header(self):
        for head, want_pixel in (({"TELESCOP": "MicroObservatory", "XBINNING": 2}, 6.8),
                                 ({"TELESCOP": "MicroObservatory"}, 13.6)):
            line, pixel, scale = self._setup(head)
            self.assertAlmostEqual(pixel, want_pixel, places=1)
            self.assertAlmostEqual(scale, 13.6 / 560 * 206.265, places=3)   # about 5.0"/px either way
            self.assertIn("(approximate", line)


if __name__ == "__main__":
    unittest.main()
