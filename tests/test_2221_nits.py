"""2.2.21 nits found while retaking the manual's screenshots: (j) a report save keeps the series' own site, (f) the
field image belongs to the series target, (a) typed folder paths are counted, (b) the AAVSO report's saved line
survives a chart time-out, (h) New reduction clears the last run's line, GCVS names in the notes check, the corner plot
laid out at the figure's own size."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")

import clearv_app as ca  # noqa: E402
import photometry_core as core  # noqa: E402
import transit_posterior as tpost  # noqa: E402


class TestGcvsNames(unittest.TestCase):
    def test_gcvs_names_flagged(self):
        known = ["DY Peg", "000-BCR-111"]
        self.assertEqual(ca.notes_foreign_stars("SZ Lyn reached maximum", known), ["SZ Lyn"])
        self.assertEqual(ca.notes_foreign_stars("comp near V1441 Cas", known), ["V1441 Cas"])
        self.assertEqual(ca.notes_foreign_stars("XX Cyg, then R Leo and RR Lyr", known), ["XX Cyg", "R Leo", "RR Lyr"])
        self.assertEqual(ca.notes_foreign_stars("HD 219134 and SZ Lyn", known), ["HD 219134", "SZ Lyn"])  # in order

    def test_current_target_and_plain_words_not_flagged(self):
        self.assertEqual(ca.notes_foreign_stars("DY Peg near maximum", ["DY Peg"]), [])
        self.assertEqual(ca.notes_foreign_stars("dy peg near maximum", ["DY Peg"]), [])   # case of the known name
        # ordinary words that look like a name but are not (lower case, no constellation, "J" letters)
        for text in ("OSC reduced; not transformed", "a Leo b", "at Cas", "JJ Cyg", "SZ Lynx", "OK Per-haps",
                     "AB Xyz"):
            self.assertEqual(ca.notes_foreign_stars(text, ["DY Peg"]), [], text)
        self.assertEqual(len(ca.IAU_CONSTELLATIONS), 88)


def _have_tk():
    try:
        import tkinter as tk
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


HAVE_TK = _have_tk()


class TestCornerSize(unittest.TestCase):
    def test_laid_out_from_the_figure_size(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from test_2220_multinight import single_fit
        r = single_fit(100, 1)
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        for size in ((5.0, 5.0), (9.0, 6.0), (6.0, 9.0)):
            fig = Figure(dpi=100)
            FigureCanvasAgg(fig)
            fig.set_size_inches(*size)
            tpost.corner_figure(r, fig, shape_only=True, keep_size=True)
            self.assertEqual(tuple(fig.get_size_inches()), size)   # kept, not reset
            fig.canvas.draw()
            rend = fig.canvas.get_renderer()
            W, H = fig.bbox.width, fig.bbox.height
            for ax in fig.get_axes():
                for t in [ax.title, ax.xaxis.label, ax.yaxis.label] + ax.get_xticklabels() + ax.get_yticklabels():
                    if not t.get_text() or not t.get_visible():
                        continue
                    bb = t.get_window_extent(rend)
                    self.assertGreaterEqual(bb.x0, -1, (size, t.get_text()))
                    self.assertGreaterEqual(bb.y0, -1, (size, t.get_text()))
                    self.assertLessEqual(bb.x1, W + 1, (size, t.get_text()))
                    self.assertLessEqual(bb.y1, H + 1, (size, t.get_text()))


def _obs(night, jd, mag=10.0):
    return core.Obs(jd=jd, mag=mag, merr=0.01, cmag=9.0, kmag=10.5, airmass=1.1, path=f"{jd}.fit", exptime=60.0,
                    flux_var=1000.0, flux_comp=2000.0, night=night, filt="TG")


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestNitsInApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test2221.log")
        cls.app = ca.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        app = self.app
        self._site = (app.lat.get(), app.lon.get(), app.elevation.get(), app.observer.get())

    def tearDown(self):
        app = self.app
        app._loading_meta = True
        try:
            for var, v in zip((app.lat, app.lon, app.elevation, app.observer), self._site):
                var.set(v)
        finally:
            app._loading_meta = False
        if app.observations:
            with mock.patch.object(app, "_offer_series_save", return_value=True):
                app.close_series(asked=True)
        app.output_dir.set("")
        shutil.rmtree(self.dir, ignore_errors=True)

    def pump(self, ms=500):
        end = time.monotonic() + ms / 1000.0
        while time.monotonic() < end:
            self.app.update()
            time.sleep(0.01)

    def _load_series(self, star="SZ Lyn", ra="8.1500", dec="44.4700"):
        path = os.path.join(self.dir, "SZ_Lyn.cvseries")
        meta = {"star_id": star, "ra_hours": ra, "dec_deg": dec, "lat": "35.37", "lon": "-81.96",
                "elevation": "300", "observer": "JABA", "mode": "variables"}
        obs = [_obs("2026-10-20", 2461334.6 + k * 0.01) for k in range(5)]
        obs += [_obs("2026-10-21", 2461335.6 + k * 0.01) for k in range(5)]
        core.save_series(path, meta, obs)
        app = self.app
        with mock.patch.object(ca.filedialog, "askopenfilename", return_value=path), \
                mock.patch.object(ca.messagebox, "showinfo"), \
                mock.patch.object(ca.messagebox, "askyesno", return_value=True), \
                mock.patch.object(app, "_check_planet_transits"):
            app.load_series()
        self.assertEqual(app.series_path, path)
        return path

    def _set_page_site(self, lat, lon, elev, observer):
        app = self.app
        app._loading_meta = True   # not saved as the tester's own settings
        try:
            app.lat.set(lat)
            app.lon.set(lon)
            app.elevation.set(elev)
            app.observer.set(observer)
        finally:
            app._loading_meta = False

    # ---- (j) -----------------------------------------------------------------------------------------------------
    def test_report_save_keeps_series_site_and_asks(self):
        app = self.app
        app.star_id.set("")
        path = self._load_series()
        self.assertEqual(app.lat.get(), "35.37")
        self._set_page_site("40", "-100", "500", "")   # the example profile's site, no observer code
        report = os.path.join(self.dir, "AAVSO_SZ_Lyn.txt")
        with mock.patch.object(ca.messagebox, "askyesno", return_value=True) as ask:
            app._remember_report("exoclock", ["2026-10-20"], report)
            self.assertEqual(ask.call_count, 1)
            msg = ask.call_args[0][1]
        self.assertIn("This series was observed from 35.37, -81.96, 300 m, observer JABA; the Input page has 40, "
                      "-100, 500 m, no observer code.", msg)
        self.assertIn("Keep the series' site?", msg)
        with open(path, encoding="utf-8") as fh:
            meta = json.load(fh)["meta"]
        self.assertEqual((meta["lat"], meta["lon"], meta["elevation"], meta["observer"]),
                         ("35.37", "-81.96", "300", "JABA"))
        self.assertEqual(len(json.load(open(path, encoding="utf-8"))["observations"]), 10)   # no night replaced
        self.assertTrue(any(e.get("to") == "exoclock" for e in meta["reports_saved"]["2026-10-20"]))
        # never silent: the save's status line names the series file
        app.offer_upload(report, "Open AAVSO WebObs…", core.AAVSO_WEBOBS_URL, "AAVSO WebObs")
        self.assertIn("Series file updated: SZ_Lyn.cvseries", app.status.get())
        self.assertTrue(app.status.get().startswith("Saved AAVSO_SZ_Lyn.txt — upload to AAVSO WebObs:"))
        # asked once for this series and site; the same answer is used for the next save
        with mock.patch.object(ca.messagebox, "askyesno", return_value=True) as ask:
            app._remember_report("webobs", ["2026-10-21"], report + "2")
            ask.assert_not_called()
        # the Input page's site is not changed by the save
        self.assertEqual((app.lat.get(), app.observer.get()), ("40", ""))

    def test_report_save_no_puts_page_site_and_same_site_no_question(self):
        app = self.app
        app.star_id.set("")
        path = self._load_series()
        with mock.patch.object(ca.messagebox, "askyesno") as ask:   # same site: no question
            app._remember_report("webobs", ["2026-10-20"], os.path.join(self.dir, "a.txt"))
            ask.assert_not_called()
        self._set_page_site("35.4", "-81.96", "300", "JABA")
        with mock.patch.object(ca.messagebox, "askyesno", return_value=False):
            app._remember_report("webobs", ["2026-10-21"], os.path.join(self.dir, "b.txt"))
        with open(path, encoding="utf-8") as fh:
            meta = json.load(fh)["meta"]
        self.assertEqual(meta["lat"], "35.4")

    # ---- (f) -----------------------------------------------------------------------------------------------------
    def test_field_image_refused_for_another_stars_frame(self):
        app = self.app
        app.star_id.set("HD 219134")
        app.ra_hours.set("23.2267")
        app.dec_deg.set("57.1700")
        app.preview = np.zeros((200, 300), np.float32)
        app.preview_is_debayered = True
        app.preview_header = {"RA": 348.4, "DEC": 57.17}
        app._preview_foreign = ""
        self.assertEqual(app._field_frame_problem(), "")
        self._load_series()   # SZ Lyn, far away: the frame is HD 219134's
        self.assertEqual(app.star_id.get(), "SZ Lyn")
        with mock.patch.object(ca.messagebox, "showinfo") as info, \
                mock.patch.object(ca, "draw_field_figure") as draw:
            self.assertEqual(app.save_field_image(), "")
            draw.assert_not_called()
            self.assertIn("Reduce a night of SZ Lyn first; the field image comes from a reduction", info.call_args[0][1])
        # with a report: logged, never drawn
        with mock.patch.object(ca, "draw_field_figure") as draw:
            self.assertEqual(app.save_field_image(report_path=os.path.join(self.dir, "r.txt")), "")
            draw.assert_not_called()
        # a frame whose header points elsewhere than the target (no Load series flag) is refused too
        app._preview_foreign = ""
        self.assertIn("Reduce a night of SZ Lyn", app._field_frame_problem())
        # a frame of the series' own target is used
        app.preview_header = {"RA": 8.15 * 15, "DEC": 44.47}
        self.assertEqual(app._field_frame_problem(), "")
        # series open, no frame at all
        app.preview = None
        self.assertIn("Reduce a night of SZ Lyn", app._field_frame_problem())
        app.preview_is_debayered = False
        app.preview_header = None

    # ---- (a) -----------------------------------------------------------------------------------------------------
    def test_typed_folder_is_counted(self):
        app = self.app
        lights = os.path.join(self.dir, "lights")
        bias = os.path.join(self.dir, "bias")
        os.makedirs(lights)
        os.makedirs(bias)
        from astropy.io import fits
        for k in range(3):
            hdr = fits.Header()
            hdr["IMAGETYP"] = "Bias Frame"
            fits.PrimaryHDU(np.zeros((20, 20), np.uint16), header=hdr).writeto(os.path.join(bias, f"bias_{k}.fits"))
        old_l, old_b = app.light_dir.get(), app.bias_dir.get()
        try:
            with mock.patch.object(app, "_frames_folder_picked"):
                app.bias_dir.set(bias[:-2])        # half typed: no count yet
                self.pump(600)
                self.assertEqual(app._counts["Bias"].get(), "no such folder")
                app.bias_dir.set(bias)             # finished typing
                self.assertNotEqual(app._counts["Bias"].get(), "3 files")   # debounced
                self.pump(700)
                self.assertEqual(app._counts["Bias"].get(), "3 files")
                # Enter / leaving the box counts at once (same path as Browse)
                app.light_dir.set(lights)
                app._folder_typed(app.light_dir, "Lights", now=True)
                self.assertEqual(app._counts["Lights"].get(), "0 files")
                self.assertTrue(app.status.get().startswith("Lights: 0 files"))
        finally:
            app.light_dir.set(old_l)
            app.bias_dir.set(old_b)
            self.pump(600)

    def test_browse_same_path(self):
        app = self.app
        folder = os.path.join(self.dir, "flat")
        os.makedirs(folder)
        old = app.flat_dir.get()
        try:
            with mock.patch.object(ca.filedialog, "askdirectory", return_value=folder):
                app._browse(app.flat_dir, "Flat")
            self.assertEqual(app._counts["Flat"].get(), "0 files")
            self.assertNotIn("Flat", app._folder_jobs)   # no second count from the typed-path timer
        finally:
            app.flat_dir.set(old)
            self.pump(600)

    # ---- (b) -----------------------------------------------------------------------------------------------------
    def test_saved_line_survives_chart_timeout(self):
        app = self.app
        app.output_dir.set(self.dir)
        report = os.path.join(self.dir, "AAVSO_SZ_Lyn_TG_SHOBS-P.txt")
        info = {"title": "SZ Lyn — field", "subtitle": "x", "radius": 8, "sky_in": 14, "sky_out": 20, "north": None,
                "east": None, "arcsec_px": 1.0, "marks": [], "seq": [], "center": (122.0, 44.0), "fov_arcmin": 30.0}

        def timeout(*_a, **_k):
            raise TimeoutError("timed out")

        fig = mock.MagicMock()
        with mock.patch.object(app, "field_image_info", return_value=info), \
                mock.patch.object(app, "_field_frame_problem", return_value=""), \
                mock.patch.object(ca, "draw_field_figure", return_value=fig), \
                mock.patch.object(core, "vsp_chart_image", side_effect=timeout):
            app.save_field_image(report_path=report)
            app.saved_notice(report, "Upload with the AAVSO WebObs file tool.")
            app.offer_upload(report, "Open AAVSO WebObs…", core.AAVSO_WEBOBS_URL, "AAVSO WebObs")
            line = app.status.get()
            self.pump(800)
        self.assertEqual(app.status.get(), line)
        self.assertTrue(line.startswith("Saved AAVSO_SZ_Lyn_TG_SHOBS-P.txt — upload to AAVSO WebObs:"))
        self.assertEqual(app._status_offer[1], core.AAVSO_WEBOBS_URL)   # the Open AAVSO WebObs… button stays
        self.assertIn("AAVSO chart not saved (timed out)", app.log_box.get("1.0", "end"))

    # ---- (h) -----------------------------------------------------------------------------------------------------
    def test_new_reduction_clears_done_line(self):
        app = self.app
        app.progress_text.set("Photometry done in 2:08. 240 points.")
        with mock.patch.object(ca.messagebox, "askyesno", return_value=True):
            self.assertTrue(app.new_reduction(ask=True))
        self.assertEqual(app.progress_text.get(), "")

    # ---- leftover 1: the target's own catalog name -------------------------------------------------------------
    def test_notes_check_target_alias(self):
        app = self.app
        app.star_id.set("HD 219134")
        app._target_lookup_names = {"HD 219134", "V0646 Cas"}
        app._previous_target = ""
        app.notes.set("V0646 Cas defocused")
        with mock.patch.object(ca.messagebox, "askyesno") as ask:
            self.assertTrue(app._notes_check())
            ask.assert_not_called()
        app.notes.set("defocused; SZ Lyn")
        with mock.patch.object(ca.messagebox, "askyesno", return_value=False) as ask:
            self.assertFalse(app._notes_check())
            self.assertIn("SZ Lyn", ask.call_args[0][1])
        app._target_lookup_names = set()
        app.notes.set(app._default_note())


if __name__ == "__main__":
    unittest.main()
