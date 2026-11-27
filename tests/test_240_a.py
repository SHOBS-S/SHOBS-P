"""2.4.0 step 1 (Output pages): #98 the controls in function groups (same groups and order on the Transit fit and
Variables Output pages, save area under the plot, one green button up top, the "refit needed" cue, the period
flash), #96 Detrend airmass greyed out when the data have no airmass, #101 Save field image after a saved report
makes no duplicate."""
import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import group_rows  # noqa: E402
import photometry_core as core  # noqa: E402
import transit_core as tc  # noqa: E402

try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False

PLANET = {"name": "Synth b", "host": "Synth", "period": 2.21857, "t0": 2461000.0, "t0_err": 0.0002,
          "period_err": 1e-6, "k": 0.155, "a_rs": 8.8, "b": 0.66, "t14": 0.0760, "teff": 5050.0, "depth": 0.024}
TC = 2461000.0 + 1000 * PLANET["period"]


def synth(n=160, noise=0.004, seed=3):
    rng = np.random.default_rng(seed)
    t = TC + np.linspace(-0.08, 0.08, n)
    prob = tc.TransitProblem(t - 2461000.0, np.ones(n), np.ones(n), None, PLANET["period"], {}, ("tc",),
                             {"tc": TC - 2461000.0, "k": 0.155, "a_rs": 8.8, "b": 0.66, "q1": 0.5, "q2": 0.3,
                              "c0": 1.0})
    flux = prob.model(prob.full([TC - 2461000.0])) + rng.normal(0, noise, n)
    am = 1.2 + 0.3 * ((t - t.mean()) / 0.1) ** 2
    return t, flux, np.full(n, noise), am


class TestWrapRows(unittest.TestCase):
    def test_same_rule_as_flow_rows(self):
        import clearv_app as ca
        for widths, avail in (([100, 100, 100], 250), ([300], 100), ([50, 60, 70, 80], None), ([], 100),
                              ([120, 90, 200, 40, 40], 260)):
            self.assertEqual(group_rows.wrap_rows(widths, avail), ca.flow_rows(widths, avail))

    def test_groups_and_order(self):
        self.assertEqual(group_rows.GROUP_KEYS, ("data", "points", "corrections", "fit", "comps", "analysis", "view"))
        self.assertEqual([group_rows.GROUP_LABELS[k] for k in group_rows.GROUP_KEYS],
                         ["Data", "Points", "Corrections", "Fit", "Comps", "Analysis", "View"])
        self.assertGreaterEqual(group_rows.LABEL_WIDTH, max(len(v) for v in group_rows.GROUP_LABELS.values()))


class TestNoAirmassFitUnchanged(unittest.TestCase):
    """#96: unticking the box when there is no airmass is what the fit already did (numbers freeze)."""

    def test_no_airmass_ticked_equals_unticked(self):
        t, f, e, _am = synth()
        nan = np.full(len(t), np.nan)
        a = tc.lsq_fit(t, f, e, nan, PLANET, detrend_airmass=True)
        b = tc.lsq_fit(t, f, e, nan, PLANET, detrend_airmass=False)
        for name in ("tc", "k"):
            self.assertEqual(a["p"][name], b["p"][name])
        self.assertEqual(a["p"], b["p"])


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestOutputPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        import transit_ui
        cls.ca, cls.tu = ca, transit_ui
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test_240_a.log")
        cls.app = ca.App()
        cls.app.update()
        cls.dir = tempfile.mkdtemp()
        t, f, e, am = synth()
        cls.fit = tc.fit_transit(t, f, e, am, PLANET, mcmc_steps=200)

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def pump(self, ms=200, until=None):
        end = time.monotonic() + ms / 1000.0
        while time.monotonic() < end:
            self.app.update()
            if until is not None and until():
                return True
            time.sleep(0.01)
        return until is None

    @staticmethod
    def _walk(w):
        out = [w]
        for c in w.winfo_children():
            out += TestOutputPages._walk(c)
        return out

    # ---- #98: groups ---------------------------------------------------------------------------------------------
    def test_same_groups_same_order_on_both_pages(self):
        app, tp = self.app, self.app.transit_page
        for groups in (tp.groups, app.out_groups):
            self.assertEqual(tuple(groups.boxes), group_rows.GROUP_KEYS)
            self.assertEqual([str(groups.labels[k].cget("text")) for k in groups.boxes],
                             ["Data", "Points", "Corrections", "Fit", "Comps", "Analysis", "View"])
            for key, lab in groups.labels.items():
                self.assertEqual(str(lab.cget("style")), "Hint.TLabel")
                if key not in group_rows.SHARED_ROW:      # the left column; a sharing group's label sits in the row
                    self.assertEqual(int(lab.cget("width")), group_rows.LABEL_WIDTH)
            # John 16 Nov: Comps and Analysis share a row, so six rows and five hairlines, none above or below
            self.assertIs(groups.rows["analysis"], groups.rows["comps"])
            self.assertEqual(len(groups.separators), 5)
            for key in groups.boxes:
                self.assertTrue(groups.tips[key])
            for key in ("points", "corrections"):
                self.assertIn("feed the fit", groups.tips[key])
            # no boxed frames
            self.assertFalse([w for w in self._walk(groups.frame) if w.winfo_class() == "TLabelframe"])

    def test_transit_groups_hold_the_agreed_controls(self):
        tp = self.app.transit_page
        g = tp.groups
        self.assertIn("Night", g.texts("data"))
        self.assertIn("Open AAVSO exoplanet report (EXOTIC)…", g.texts("data"))
        self.assertEqual(g.group_of(tp.close_report_button), "data")
        for word in ("Trim", "Trim and refit", "Clear trim"):
            self.assertIn(word, g.texts("points"))
        self.assertEqual(g.group_of(tp.refit_flagged_button), "points")     # moved from the Fit row
        self.assertIn("Detrend", g.texts("corrections"))
        self.assertEqual(g.group_of(tp.detrend_box.master), "corrections")   # 2.4.0 (#103): one drop-down
        for word in ("airmass", "time slope", "time curve", "Auto (BIC)"):
            self.assertIn(word, " ".join(tp.detrend_box.cget("values")))
        self.assertEqual(g.group_of(tp.flip_at_gap_check), "corrections")
        self.assertEqual([t for t in g.texts("fit") if not t.startswith("PY_VAR")],
                         ["MCMC", "Fit transit", "Cancel", "Fit with saved priors…"])   # 2.4.0 fit-in F4 (#93)
        self.assertEqual(g.texts("comps")[:2], ["Comp health…", "Use comps…"])
        self.assertEqual(g.texts("analysis"), ["Nearby-binary (NEB) check…", "Multi-night…",
                                               "Fit the series' nights…",    # moved from the Results row
                                               "Robustness check"])          # 2.4.0 step 6b (#35)
        self.assertEqual(g.group_of(tp.multinight_series_button), "analysis")
        self.assertEqual([t for t in g.texts("view") if not t.startswith("PY_VAR")], ["credible bands", "Plot bins"])
        saves = [str(b.cget("text")) for b in tp._results_box.winfo_children()]
        self.assertNotIn("Fit the series' nights…", saves)
        for text in saves:      # the area under the plot is the save area only
            self.assertTrue(text.startswith(("Save", "Open", "Go to", "Field image", "Fetch")), text)

    def test_variables_groups_hold_the_agreed_controls(self):
        app = self.app
        g = app.out_groups
        self.assertEqual(g.texts("data"), ["Load series", "Close series", "Add series file…", "Remove night…",
                                           "Night notes…"])
        self.assertNotIn("Save series", g.texts("data"))
        pts = g.texts("points")
        self.assertTrue(pts[0].startswith("Leave flagged points out"))
        for word in ("Apply", "Transmission loss…", "Planet transits…"):
            self.assertIn(word, pts)
        self.assertEqual(g.texts("corrections"), ["Line up segments (nights, flips, re-centers)",
                                                  "Line up flip/re-center only (keep nights)",   # step 8 (#66)
                                                  "Pier side as segments",
                                                  "Check-star correction (fold and period)"])
        fit = [t for t in g.texts("fit") if not t.startswith("PY_VAR")]
        self.assertEqual(fit, ["Period search (days)", "to", "Period from", "Refit period"])
        self.assertEqual(g.texts("comps"), ["Comp health…", "Use comps…"])
        self.assertEqual(g.texts("analysis"), ["Watch stars…", "Time of max/min…"])   # 7d: no Candidates…
        view = [t for t in g.texts("view") if not t.startswith("PY_VAR")]
        self.assertEqual(view, ["Time axis", "Plot bins", "Mark periods (days)", "Show", "Airmass-corrected night means"])
        # the save area: Save series stays under the plot; the report's Notes sit beside Save AAVSO report
        saves = app.out_saves.shown_widgets()
        words = [str(w.cget("text")) if w.winfo_class() == "TButton" else "/".join(
            str(c.cget("text")) for c in w.winfo_children() if c.winfo_class() == "TLabel") for w in saves]
        # 2.4.0 fit-in F3 (area L): the row starts with the Saving box (what the report and the CSV hold), and the
        # "WebObs needs …" line sits between Save AAVSO report and its Notes
        self.assertEqual(saves[0].winfo_class(), "TCombobox")
        self.assertIs(saves[0], app.save_scope_box)
        self.assertEqual([w for w in words[1:5] if w], ["Save series", "Save AAVSO report", "Notes"])
        self.assertNotIn("Refit period", words)
        for gone in ("Load series", "Comp health…", "Use comps…", "Night notes…"):
            self.assertNotIn(gone, words)
        self.assertEqual(str(app.close_series_button_out.master), str(g.parent("data")))

    def test_one_green_button_above_each_plot(self):
        app, tp = self.app, self.app.transit_page
        for groups, text in ((tp.groups, "Fit transit"), (app.out_groups, "Refit period")):
            green = [w for w in self._walk(groups.frame)
                     if w.winfo_class() == "TButton" and str(w.cget("style")) == "Accent.TButton"]
            self.assertEqual([str(w.cget("text")) for w in green], [text])
            self.assertEqual(groups.group_of(green[0]), "fit")

    def test_rows_wrap_when_narrow(self):
        """Each group row wraps like text (125 % on a 1366-px screen), and hidden controls keep out of the row."""
        box = self.app.out_groups.boxes["points"]
        box._key = None
        box.flow(4000)
        self.assertEqual(box.rows(), 1)
        box.flow(500)
        self.assertGreater(box.rows(), 1)
        tp = self.app.transit_page
        tp._flow_row4(300)      # the old name still lays the Transit rows out
        self.assertGreater(tp.groups.boxes["analysis"].rows(), 1)
        tp._flow_row4(4000)
        self.assertEqual(tp.groups.boxes["analysis"].rows(), 1)
        b = tp.groups.boxes["data"]
        self.assertNotIn(tp.close_report_button, b.shown_widgets())
        tp.groups.show("data", tp.close_report_button, True)
        self.assertIn(tp.close_report_button, b.shown_widgets())
        self.assertEqual(b.shown_widgets().index(tp.close_report_button), 1)    # after the Night box, as before
        tp.groups.show("data", tp.close_report_button, False)
        self.assertNotIn(tp.close_report_button, b.shown_widgets())

    # ---- #98: the "refit needed" cue -----------------------------------------------------------------------------
    def _show_fit(self):
        tp = self.app.transit_page
        tp.report = None
        tp.result = dict(self.fit)
        tp.meta_for_files = {}
        tp.set_detrend("airmass")
        tp._fit_settings = tp._settings_key()
        tp._fill_table()
        return tp

    def test_refit_cue_on_a_changed_setting(self):
        tp = self._show_fit()
        try:
            self.assertEqual(str(tp.fit_button.cget("text")), "Fit transit")
            self.assertFalse(tp.fit_is_stale())
            before = [tp.table.item(i)["values"] for i in tp.table.get_children()]
            with mock.patch.object(tp, "fit") as fit:
                tp.set_detrend("airmass+slope")     # a Corrections setting changes while the fit is shown
                self.app.update()
                fit.assert_not_called()            # the fit always waits for the button
            self.assertTrue(tp.fit_is_stale())
            self.assertEqual(str(tp.fit_button.cget("text")), "Refit (settings changed)")
            self.assertEqual(str(tp.fit_button.cget("style")), "Accent.TButton")
            tags = {tuple(tp.table.item(i)["tags"]) for i in tp.table.get_children()}
            self.assertEqual(tags, {("stale",)})
            self.assertEqual(str(tp.table.tag_configure("stale", "foreground")), self.tu.STALE_GRAY)
            self.assertEqual(str(tp.quality_label.cget("foreground")), self.tu.STALE_GRAY)
            self.assertEqual(str(tp.verdict_label.cget("foreground")), self.tu.STALE_GRAY)
            # the numbers themselves are the shown fit's, untouched
            self.assertEqual([tp.table.item(i)["values"] for i in tp.table.get_children()], before)
            self.assertIs(tp.result["summary"], self.fit["summary"])
            tp.set_detrend("airmass")              # set back: the fit matches its settings again
            self.assertFalse(tp.fit_is_stale())
            self.assertEqual(str(tp.fit_button.cget("text")), "Fit transit")
            self.assertNotIn(("stale",), {tuple(tp.table.item(i)["tags"]) for i in tp.table.get_children()})
            self.assertNotEqual(str(tp.verdict_label.cget("foreground")), self.tu.STALE_GRAY)
            tp.set_detrend("auto")                 # Auto is another setting too (#103)
            self.assertEqual(str(tp.fit_button.cget("text")), "Refit (settings changed)")
            tp.mcmc.set(list(self.tu.MCMC_CHOICES)[-1])     # a Fit-group setting is not a cue
            tp.set_detrend("airmass")
            self.assertEqual(str(tp.fit_button.cget("text")), "Fit transit")
        finally:
            tp.result = None
            tp._fit_settings = None
            tp.set_detrend("auto")
            tp._fill_table()

    def test_no_cue_without_a_fit(self):
        tp = self.app.transit_page
        tp.result = None
        tp._fill_table()
        tp.set_detrend("slope")
        self.assertEqual(str(tp.fit_button.cget("text")), "Fit transit")
        tp.set_detrend("auto")

    def test_new_fit_takes_its_settings_and_clears_the_cue(self):
        tp = self._show_fit()
        try:
            tp.set_detrend("curve")
            self.assertTrue(tp.fit_is_stale())
            # what fit() records when it starts and _finish_fit takes over when the fit is shown
            tp._pending_settings = tp._settings_key()
            tp._fit_settings = tp._pending_settings
            tp._fill_table()
            self.assertFalse(tp.fit_is_stale())
            self.assertEqual(str(tp.fit_button.cget("text")), "Fit transit")
        finally:
            tp.result = None
            tp._fit_settings = None
            tp.set_detrend("auto")
            tp._fill_table()

    def test_period_line_flashes_when_it_updates(self):
        app = self.app
        app._fill_summary(["s"], ["Period 0.1100 d"], ["c"], [], [])   # step 1 review: the first fill of an empty
        self.pump(app.PERIOD_FLASH_MS + 1500, lambda: not getattr(app, "_period_flashing", False))   # box: no flash
        n0 = getattr(app, "period_flash_count", 0)
        snap = (["s"], ["Period 0.1205 d"], ["c"], [], [])
        app._fill_summary(*snap)
        self.assertEqual(app.period_flash_count, n0 + 1)
        self.assertEqual(str(app.period_label.cget("background")), app.PERIOD_FLASH_BG)
        app._fill_summary(*snap)                      # the same line again: no flash
        self.assertEqual(app.period_flash_count, n0 + 1)
        self.assertTrue(self.pump(app.PERIOD_FLASH_MS + 1500, lambda: not app._period_flashing))
        self.assertNotEqual(str(app.period_label.cget("background")), app.PERIOD_FLASH_BG)
        app._fill_summary(["s"], ["Period 0.1379 d"], ["c"], [], [])
        self.assertEqual(app.period_flash_count, n0 + 2)
        self.pump(app.PERIOD_FLASH_MS + 300, lambda: not app._period_flashing)
        app.draw_output()

    # ---- #96: Detrend airmass without airmass --------------------------------------------------------------------
    def _obs(self, airmass):
        return [types.SimpleNamespace(night="JD2461234", airmass=a, jd=2461234.5 + i * 0.001, mag=10.0, flag="")
                for i, a in enumerate(airmass)]

    def test_airmass_greyed_out_without_airmass(self):
        """#96 with #103's drop-down: no airmass = the airmass choices leave the list, a chosen one becomes the same
        without airmass, and the user's own choice comes back with airmass."""
        app, tp = self.app, self.app.transit_page
        old = (app.observations, tp.night.get(), tp.report, app.lat.get(), app.lon.get())
        try:
            tp.report = None
            tp._airmass_off_why = ""
            tp.set_detrend("airmass+curve")
            app.observations = self._obs([None] * 30)
            tp.night.set("JD2461234")
            app.lat.set("")
            app.lon.set("")
            tp._update_airmass_box()
            self.assertEqual(tp.detrend.get(), "time curve (quadratic)")                 # what the fit will do
            self.assertFalse(tp.detrend_airmass.get())
            self.assertNotIn("airmass", " ".join(tp.detrend_box.cget("values")))   # no airmass choice offered
            self.assertIn("Auto (BIC)", tp.detrend_box.cget("values"))
            self.assertEqual(tp.airmass_hint.get(), "no airmass: site not set")
            self.assertIn(tp.airmass_hint_label, tp.groups.boxes["corrections"].shown_widgets())
            self.assertEqual(tp._airmass_off_why, "no airmass: site not set")     # the tooltip's words
            self.assertEqual(tp._settings_key()[0], "curve")
            # a site is set but the frames still carry no airmass: another reason, same list
            app.lat.set("35.4")
            app.lon.set("-81.9")
            tp._update_airmass_box()
            self.assertEqual(tp.airmass_hint.get(),   # step 1 review: says what to do
                             "no airmass in these data (run photometry again with the site set)")
            self.assertEqual(tp.detrend.get(), "time curve (quadratic)")
            # airmass again: full list, and the user's earlier choice is back
            app.observations = self._obs([1.1 + 0.01 * i for i in range(30)])
            tp._update_airmass_box()
            self.assertEqual(tp.detrend.get(), "airmass + time curve (quadratic)")
            self.assertTrue(tp.detrend_airmass.get())
            self.assertIn("airmass", " ".join(tp.detrend_box.cget("values")))
            self.assertEqual(tp.airmass_hint.get(), "")
            self.assertNotIn(tp.airmass_hint_label, tp.groups.boxes["corrections"].shown_widgets())
            # Auto stays Auto without airmass (it leaves the airmass choices out itself)
            tp.set_detrend("auto")
            app.observations = self._obs([None] * 30)
            tp._update_airmass_box()
            self.assertEqual(tp.detrend.get(), "Auto (BIC)")
        finally:
            app.observations = old[0]
            tp.night.set(old[1])
            tp.report = old[2]
            app.lat.set(old[3])
            app.lon.set(old[4])
            tp._airmass_off_why = ""
            tp._update_airmass_box()
            tp.set_detrend("auto")

    def test_users_choice_survives_a_night_without_airmass(self):
        app, tp = self.app, self.app.transit_page
        old = (app.observations, tp.night.get(), tp.report)
        try:
            tp.report = None
            app.observations = self._obs([1.2 + 0.01 * i for i in range(30)])
            tp.night.set("JD2461234")
            tp._update_airmass_box()
            tp.set_detrend("slope")                       # the user picked time slope
            app.observations = self._obs([None] * 30)
            tp._update_airmass_box()
            self.assertEqual(tp.detrend.get(), "time slope")
            app.observations = self._obs([1.2 + 0.01 * i for i in range(30)])
            tp._update_airmass_box()
            self.assertEqual(tp.detrend.get(), "time slope")   # still the user's choice
            self.assertFalse(tp._airmass_wanted)               # Multi-night's starting tick follows it
            tp.set_detrend("airmass")
            tp._update_airmass_box()
            self.assertEqual(tp.detrend.get(), "airmass")
            self.assertTrue(tp._airmass_wanted)
        finally:
            app.observations = old[0]
            tp.night.set(old[1])
            tp.report = old[2]
            tp._airmass_off_why = ""
            tp._update_airmass_box()
            tp.set_detrend("auto")

    def test_report_without_airmass_column(self):
        tp = self.app.transit_page
        old = tp.report
        try:
            tp._airmass_off_why = ""
            tp.set_detrend("airmass")
            tp.report = {"t": np.arange(30.0), "airmass": None}
            tp._update_airmass_box()
            self.assertFalse(tp.detrend_airmass.get())
            self.assertEqual(tp.detrend.get(), "none")
            self.assertEqual(tp.airmass_hint.get(), "no airmass in these data")
            tp.report = {"t": np.arange(30.0), "airmass": np.linspace(1.1, 1.5, 30)}
            tp._update_airmass_box()
            self.assertTrue(tp.detrend_airmass.get())
            self.assertEqual(tp.detrend.get(), "airmass")
        finally:
            tp.report = old
            tp._airmass_off_why = ""
            tp._update_airmass_box()
            tp.set_detrend("auto")

    # ---- #101: Save field image after a saved report -------------------------------------------------------------
    INFO = {"title": "SZ Lyn — field", "subtitle": "x", "radius": 8, "sky_in": 14, "sky_out": 20, "north": None,
            "east": None, "arcsec_px": 1.0, "marks": [(10.0, 12.0, "T", "#fff", "SZ Lyn")], "seq": [],
            "center": (122.0, 44.0), "fov_arcmin": 30.0}

    def _fig(self):
        fig = mock.MagicMock()

        def savefig(path, **_k):
            with open(path, "wb") as fh:
                fh.write(b"PNG")
        fig.savefig.side_effect = savefig
        return fig

    def _texts(self):
        app = self.app
        return (str(app.field_button.cget("text")), str(app.transit_page.field_button.cget("text")))

    def _pngs(self, folder):
        return sorted(n for n in os.listdir(folder) if n.endswith("_field.png"))

    def test_field_button_after_a_saved_report(self):
        app, ca = self.app, self.ca
        folder = tempfile.mkdtemp()
        report = os.path.join(folder, "AAVSO_SZ_Lyn_TG_SHOBS-P.txt")
        app._field_saved = None
        info = dict(self.INFO)
        with mock.patch.object(app, "field_image_info", side_effect=lambda: dict(info)), \
                mock.patch.object(app, "_field_frame_problem", return_value=""), \
                mock.patch.object(ca, "draw_field_figure", side_effect=lambda *a, **k: self._fig()), \
                mock.patch.object(core, "vsp_chart_image", return_value=(b"\x89PNGdata", {"chartid": "X1"})) as vsp:
            app.update_field_buttons()
            self.assertEqual(self._texts(), ("Save field image", "Save field image…"))    # before any report
            self.assertFalse(app.field_button.instate(["disabled"]))
            path = app._field_image_with_report(report)                 # what Save AAVSO report does
            self.assertTrue(os.path.exists(path))
            self.assertTrue(self.pump(5000, lambda: not app._field_saved["fetching"]))
            self.pump(100)
            self.assertEqual(self._texts(), ("Field image saved ✓",) * 2)
            for b in (app.field_button, app.transit_page.field_button):
                self.assertTrue(b.instate(["disabled"]))
            state, why = app.field_image_state()
            self.assertEqual(state, "saved")
            self.assertIn(path, why)                                    # the path is the tooltip
            self.assertEqual(app.field_image_action(), path)            # pressing does nothing
            self.assertEqual(self._pngs(folder), ["SZ_Lyn_TG_SHOBS-P_field.png"])       # no _2
            self.assertEqual(vsp.call_count, 1)
            # other marks (Use comps, a moved marker): the saved image no longer shows this; the button is live
            info["marks"] = [(10.0, 12.0, "T", "#fff", "SZ Lyn"), (40.0, 41.0, "C1", "#fff", "comp")]
            app.update_field_buttons()
            self.assertEqual(self._texts(), ("Save field image", "Save field image…"))
            self.assertFalse(app.field_button.instate(["disabled"]))

    def test_fetch_chart_only_when_the_chart_is_missing(self):
        app, ca = self.app, self.ca
        folder = tempfile.mkdtemp()
        report = os.path.join(folder, "AAVSO_SZ_Lyn_TG_SHOBS-P.txt")
        app._field_saved = None
        calls = {"n": 0}

        def vsp(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("timed out")
            return b"\x89PNGdata", {"chartid": "X2"}

        with mock.patch.object(app, "field_image_info", side_effect=lambda: dict(self.INFO)), \
                mock.patch.object(app, "_field_frame_problem", return_value=""), \
                mock.patch.object(ca, "draw_field_figure", side_effect=lambda *a, **k: self._fig()), \
                mock.patch.object(core, "vsp_chart_image", side_effect=vsp):
            path = app._field_image_with_report(report)
            self.assertTrue(self.pump(5000, lambda: not app._field_saved["fetching"]))
            self.pump(100)
            self.assertEqual(self._texts(), ("Fetch AAVSO chart",) * 2)
            self.assertFalse(app.field_button.instate(["disabled"]))
            self.assertEqual(app.field_image_state()[0], "chart")
            self.assertIn("Fetch AAVSO chart again later", app.log_box.get("1.0", "end"))
            app.field_button.invoke()                                   # fetches just the chart
            self.assertTrue(self.pump(5000, lambda: calls["n"] == 2 and not app._field_saved["fetching"]))
            self.pump(100)
            self.assertEqual(self._pngs(folder), ["SZ_Lyn_TG_SHOBS-P_field.png"])       # no second image
            self.assertTrue(os.path.exists(os.path.join(folder, "SZ_Lyn_TG_SHOBS-P_aavso_chart.png")))
            self.assertEqual(sorted(os.listdir(folder)), ["SZ_Lyn_TG_SHOBS-P_aavso_chart.png",
                                                          "SZ_Lyn_TG_SHOBS-P_field.png"])
            self.assertEqual(self._texts(), ("Field image saved ✓",) * 2)
            self.assertTrue(app.field_button.instate(["disabled"]))
            self.assertEqual(path, app._field_saved["path"])

    def test_field_button_resets(self):
        """A new frame (new night, new reduction), another series, or a deleted file: Save field image is live."""
        app, ca = self.app, self.ca

        def saved():
            folder = tempfile.mkdtemp()
            app._field_saved = None
            p = app._field_image_with_report(os.path.join(folder, "AAVSO_SZ_Lyn_TG_SHOBS-P.txt"))
            self.assertTrue(self.pump(5000, lambda: not app._field_saved["fetching"]))
            self.pump(100)
            self.assertEqual(app.field_image_state()[0], "saved")
            return p

        with mock.patch.object(app, "field_image_info", side_effect=lambda: dict(self.INFO)), \
                mock.patch.object(app, "_field_frame_problem", return_value=""), \
                mock.patch.object(ca, "draw_field_figure", side_effect=lambda *a, **k: self._fig()), \
                mock.patch.object(core, "vsp_chart_image", return_value=(b"\x89PNGdata", {})):
            tok, gen = getattr(app, "_preview_token", 0), getattr(app, "_series_gen", 0)
            try:
                saved()
                app._preview_token = tok + 1            # a new image on the Photometry page
                app.update_field_buttons()
                self.assertEqual(self._texts(), ("Save field image", "Save field image…"))
                self.assertIsNone(app._field_saved)
                saved()
                app._series_gen = gen + 1               # a series loaded, closed or started
                app.draw_output()                       # every series change ends in a redraw
                self.assertEqual(self._texts(), ("Save field image", "Save field image…"))
                p = saved()
                os.remove(p)                            # the file is gone
                app.transit_page.refresh()
                self.assertEqual(self._texts(), ("Save field image", "Save field image…"))
                self.assertFalse(app.transit_page.field_button.instate(["disabled"]))
            finally:
                app._preview_token, app._series_gen = tok, gen
                app._field_saved = None
                app.update_field_buttons()

    def test_manual_save_before_any_report_works_as_before(self):
        app, ca = self.app, self.ca
        app._field_saved = None
        with mock.patch.object(app, "save_field_image", return_value="x.png") as save:
            app.update_field_buttons()
            app.field_button.invoke()
            app.transit_page.field_button.invoke()
            self.assertEqual(save.call_count, 2)
            save.assert_called_with()
        self.assertIsNone(app._field_saved)        # a hand-saved image never greys the button out


if __name__ == "__main__":
    unittest.main()
