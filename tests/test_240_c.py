"""2.4.0 step 1 review fixes: the Multi-night window's airmass default after a no-airmass source (numbers freeze);
Blink's busy bar when the list empties, and a failed frame read again; Escape at the Save AAVSO chart-ID question;
the header-vs-target check (superpixel, 1 degree floor, equinox of the date); the far-off frame with no pointing in
the other headers still searches; Save field image live again after a run; the period flash's word; the airmass
hint; plain_read_error's loose words; Load series' busy bar behind a question; the no-site question after the
other run checks; the Output pages' vertical padding."""
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import group_rows                 # noqa: E402
import multinight                 # noqa: E402
import photometry_core as core    # noqa: E402

try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(name="clearv_app.py"):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def _body(src: str, start: str, end: str = "\n    def ") -> str:
    i = src.index(start)
    j = src.index(end, i + len(start))
    return src[i:j]


class _Box:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


# ---- 1: the Multi-night window's airmass default (numbers freeze) ----------------------------------------------
class TestMultiNightAirmassDefault(unittest.TestCase):
    def _page(self, box, wanted=True, why=""):
        return types.SimpleNamespace(detrend_airmass=_Box(box), _airmass_wanted=wanted, _airmass_off_why=why)

    def test_forced_untick_is_not_the_users_choice(self):
        # the page showed a night with no airmass: its box is forced off, the user's tick is kept aside
        page = self._page(False, wanted=True, why="no airmass: site not set")
        self.assertTrue(multinight.page_airmass_default(page))      # as 2.3.6 started: ticked

    def test_users_own_untick_is_kept(self):
        self.assertFalse(multinight.page_airmass_default(self._page(False, wanted=False, why="")))
        self.assertFalse(multinight.page_airmass_default(self._page(False, wanted=False, why="no airmass in these data")))
        self.assertTrue(multinight.page_airmass_default(self._page(True)))

    def test_no_page_and_old_pages(self):
        self.assertTrue(multinight.page_airmass_default(None))
        self.assertFalse(multinight.page_airmass_default(types.SimpleNamespace(detrend_airmass=_Box(False))))

    def test_window_uses_it(self):
        s = _src("multinight.py")
        self.assertIn("self.detrend_airmass = BooleanVar(value=page_airmass_default(page))", s)
        self.assertNotIn("BooleanVar(value=bool(page.detrend_airmass.get())", s)


# ---- 5: the header's pointing, brought to J2000 ---------------------------------------------------------------
class TestHeaderPointingJ2000(unittest.TestCase):
    def test_equinox_of_the_date_is_precessed(self):
        j2000 = (103.0204, -8.2002)
        from astropy.coordinates import FK5, SkyCoord
        from astropy.time import Time
        import astropy.units as u
        now = SkyCoord(j2000[0] * u.deg, j2000[1] * u.deg, frame=FK5(equinox=Time(2000.0, format="jyear"))) \
            .transform_to(FK5(equinox=Time(2026.9, format="jyear")))
        head = {"RA": float(now.ra.deg), "DEC": float(now.dec.deg), "EQUINOX": 2026.9}
        raw = core.header_pointing(head)
        self.assertGreater(core.great_circle_deg(raw[0], raw[1], *j2000), 0.3)     # about 0.37 deg in 2026
        back = core.header_pointing_j2000(head)
        self.assertLess(core.great_circle_deg(back[0], back[1], *j2000), 1e-4)
        head_old = {"RA": float(now.ra.deg), "DEC": float(now.dec.deg), "EPOCH": "J2026.9"}
        back = core.header_pointing_j2000(head_old)
        self.assertLess(core.great_circle_deg(back[0], back[1], *j2000), 1e-4)

    def test_j2000_or_unnamed_is_left_alone(self):
        for extra in ({}, {"EQUINOX": 2000.0}, {"EQUINOX": "J2000"}, {"EQUINOX": "junk"}, {"EQUINOX": True}):
            head = dict({"RA": 103.0204, "DEC": -8.2002}, **extra)
            self.assertEqual(core.header_pointing_j2000(head), core.header_pointing(head), extra)
        self.assertIsNone(core.header_pointing_j2000({"OBJECT": "x"}))

    def test_equinox_year(self):
        self.assertIsNone(core.header_equinox_year({}))
        self.assertIsNone(core.header_equinox_year({"EQUINOX": 2000}))
        self.assertAlmostEqual(core.header_equinox_year({"EQUINOX": 2026.87}), 2026.87)
        self.assertAlmostEqual(core.header_equinox_year({"EPOCH": "B1950"}), 1950.0)


# ---- 11: plain_read_error's loose words -----------------------------------------------------------------------
class TestLooseReadWords(unittest.TestCase):
    def _from_reader(self, exc):
        """The exception as read_fits raises it (the reader is in its traceback)."""
        d = tempfile.mkdtemp()
        path = os.path.join(d, "x.fits")
        with open(path, "wb") as fh:
            fh.write(b"x")
        with mock.patch.object(core.fits, "open", side_effect=exc):
            try:   # not assertRaises: it takes the traceback off the exception it hands back
                core.read_fits(path)
            except type(exc) as got:
                return got
        self.fail("read_fits did not raise")

    def _from_processing(self, exc):
        try:
            raise exc
        except type(exc) as got:
            return got

    def test_reshape_from_the_reader_is_a_damaged_file(self):
        exc = self._from_reader(ValueError("cannot reshape array of size 5 into shape (2,3)"))
        self.assertEqual(core.plain_read_error(exc), core.READ_ERR_DAMAGED)
        exc = self._from_reader(TypeError("buffer is too small for requested array"))
        self.assertEqual(core.plain_read_error(exc), core.READ_ERR_DAMAGED)
        exc = self._from_reader(OSError("[Errno 22] Invalid argument"))
        self.assertEqual(core.plain_read_error(exc), core.READ_ERR_DISK)

    def test_same_words_from_processing_claim_no_cause(self):
        for text in ("cannot reshape array of size 5 into shape (2,3)", "buffer is too small for requested array",
                     "[Errno 22] Invalid argument"):
            exc = self._from_processing(ValueError(text))
            self.assertEqual(core.plain_read_error(exc), core.READ_ERR_UNSURE, text)
            self.assertEqual(core.plain_read_error(text), core.READ_ERR_UNSURE, text)     # a saved text: unknown
        self.assertEqual(core.READ_ERR_UNSURE, "could not be read (see .shobs_p.log)")
        self.assertIn(core.READ_ERR_UNSURE, core.READ_ERR_WORDS)
        self.assertEqual(core.plain_read_error(core.READ_ERR_UNSURE), core.READ_ERR_UNSURE)

    def test_clear_words_do_not_need_the_reader(self):
        self.assertEqual(core.plain_read_error(OSError("Header missing END card.")), core.READ_ERR_DAMAGED)
        self.assertEqual(core.plain_read_error(OSError("[Errno 5] Input/output error")), core.READ_ERR_DISK)
        self.assertEqual(core.plain_read_error(ValueError("every comp was lost (C1: off the frame edge)")), "")


# ---- source-level checks (no window needed) -------------------------------------------------------------------
class TestSource(unittest.TestCase):
    def test_chart_id_question_cancel_is_dont_save(self):
        body = _body(_src(), "    def _chart_id_at_save(self)")
        self.assertIn('safe="save"', body)
        self.assertIn('cancel="stop"', body)

    def test_no_site_question_comes_after_the_other_checks(self):
        body = _body(_src(), "    def run_photometry(self")
        guard = body.index("self._transit_site_guard()")
        self.assertEqual(body.count("self._transit_site_guard()"), 1)
        for earlier in ("self._aperture_sane()", "self._markers_in_order()", "self._mode_guard(lights)",
                        'sky_out = self._num_box(self.sky_out, "Sky out")', "Mark at least one comparison star",
                        "messagebox.showerror(APP_TITLE, str(exc))"):
            self.assertLess(body.index(earlier), guard, earlier)
        self.assertLess(guard, body.index("mode = self.debayer_mode.get()"))      # still before the run starts

    def test_load_series_hides_the_bar_behind_its_question(self):
        body = _body(_src(), "    def _load_series_file(self")
        ask = body.index("sure = messagebox.askyesno(")
        self.assertIn("step(None)", body[ask - 200:ask])
        after = body.index('step("the stars and settings")', ask)
        self.assertLess(after, body.index("kept_marked = {}"))

    def test_far_frame_stops_only_when_the_other_headers_say_so(self):
        body = _body(_src(), "    def _auto_target_marker(self")
        self.assertIn("if later is False:", body)
        self.assertNotIn("if not later:", body)

    def test_separators_have_no_padding(self):
        s = _src("group_rows.py")
        self.assertIn("sep.pack(fill=X)", s)
        self.assertEqual(group_rows.SHARED_ROW, {"analysis": "comps"})      # the agreed layout is not touched


# ---- with the window ------------------------------------------------------------------------------------------
@unittest.skipUnless(HAVE_TK, "needs a display")
class TestWithApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        import transit_ui
        cls.ca, cls.tu = ca, transit_ui
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test_240_c.log")
        cls.app = ca.App()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    # ---- 1 ----
    def test_multinight_default_from_the_real_page(self):
        app, tp = self.app, self.app.transit_page
        old = (app.observations, tp.night.get(), tp.report, app.lat.get(), app.lon.get())
        try:
            tp.report = None
            tp.detrend_airmass.set(True)
            tp._airmass_wanted, tp._airmass_off_why = True, ""
            app.observations = [types.SimpleNamespace(night="JD2461234", airmass=None, jd=2461234.5 + i * 0.001,
                                                      mag=10.0, flag="") for i in range(30)]
            tp.night.set("JD2461234")
            app.lat.set("")
            app.lon.set("")
            tp._update_airmass_box()
            self.assertFalse(tp.detrend_airmass.get())                  # the page's box: forced off
            self.assertTrue(multinight.page_airmass_default(tp))        # the Multi-night window: still ticked
            tp._airmass_wanted = False                                  # the user had unticked it himself
            self.assertFalse(multinight.page_airmass_default(tp))
        finally:
            app.observations = old[0]
            tp.night.set(old[1])
            tp.report = old[2]
            app.lat.set(old[3])
            app.lon.set(old[4])
            tp._airmass_wanted = True
            tp._update_airmass_box()
            tp._airmass_off_why = ""
            tp.detrend_airmass_check.state(["!disabled"])
            tp.detrend_airmass.set(True)

    # ---- 10 ----
    def test_airmass_hint_says_what_to_do(self):
        app, tp = self.app, self.app.transit_page
        old = (app.observations, tp.night.get(), tp.report, app.lat.get(), app.lon.get())
        try:
            tp.report = None
            app.observations = [types.SimpleNamespace(night="JD2461234", airmass=None, jd=2461234.5, mag=10.0,
                                                      flag="")]
            tp.night.set("JD2461234")
            app.lat.set("")
            app.lon.set("")
            self.assertEqual(tp._airmass_missing(), "no airmass: site not set")
            app.lat.set("35.4")
            app.lon.set("-81.9")
            self.assertEqual(tp._airmass_missing(),
                             "no airmass in these data (run photometry again with the site set)")
            tp.report = {"t": np.arange(5.0), "airmass": None}          # a report: nothing to run again
            self.assertEqual(tp._airmass_missing(), "no airmass in these data")
        finally:
            app.observations = old[0]
            tp.night.set(old[1])
            tp.report = old[2]
            app.lat.set(old[3])
            app.lon.set(old[4])

    # ---- 2 ----
    def test_blink_bar_ends_when_the_list_empties(self):
        app = self.app
        old = (app.blink_paths, app.blink_index)
        try:
            app.blink_paths = []
            app._blink_busy("Reading frame 37… (12 of 210 read)")
            self.assertIsNotNone(app._blink_busy_tok)
            app.show_blink()
            self.assertIsNone(app._blink_busy_tok)
            self.assertFalse(app.busy_bar.busy)
            self.assertEqual(app.blink_label.get(), "No lights in the lights folder.")
        finally:
            app._blink_busy("")
            app.blink_paths, app.blink_index = old

    def test_load_blink_list_ends_the_bar(self):
        body = _body(_src(), "    def load_blink_list(self)")
        self.assertIn('self._blink_busy("")', body)
        self.assertLess(body.index('self._blink_busy("")'), body.index("self._start_blink_preload()"))

    # ---- 3 ----
    def test_failed_blink_frame_is_read_again(self):
        app = self.app
        d = tempfile.mkdtemp()
        path = os.path.join(d, "Light_0037.fit")
        with open(path, "wb") as fh:
            fh.write(b"half a file")
        key = app._blink_key(path)
        old = (dict(app._blink_failed), dict(app._blink_failed_stamp))
        try:
            def fail():
                app._blink_failed[key] = core.READ_ERR_DAMAGED
                app._blink_failed_stamp[key] = app._blink_file_stamp(path)
            fail()
            self.assertFalse(app._blink_retry(path))                 # playback passing it: not read again
            self.assertIn(key, app._blink_failed)
            self.assertTrue(app._blink_retry(path, by_hand=True))    # asked for by hand: read again
            self.assertNotIn(key, app._blink_failed)
            self.assertFalse(app._blink_retry(path, by_hand=True))   # nothing failed: nothing to do
            fail()
            with open(path, "ab") as fh:                             # the copy finished: the file changed
                fh.write(b" and the rest of it")
            self.assertTrue(app._blink_retry(path))
            self.assertNotIn(key, app._blink_failed)
            self.assertNotIn(key, app._blink_failed_stamp)
        finally:
            app._blink_failed, app._blink_failed_stamp = old

    def test_step_and_listed_frame_retry_by_hand(self):
        s = _src()
        for name in ("    def step_blink(self", "    def show_listed_frame(self"):
            body = _body(s, name)
            self.assertIn("by_hand=True", body)
            self.assertLess(body.index("by_hand=True"), body.rindex("self.show_blink()"))
        self.assertIn("self._blink_retry(path)", _body(s, "    def show_blink(self)"))

    def test_reader_records_the_files_stamp(self):
        app = self.app
        d = tempfile.mkdtemp()
        path = os.path.join(d, "Light_0001.fit")
        with open(path, "wb") as fh:
            fh.write(b"not a fits file at all " * 50)
        old = (app.blink_paths, app.blink_index, dict(app._blink_failed), dict(app._blink_failed_stamp))
        try:
            app.blink_paths, app.blink_index = [path], 0
            app._blink_index_of = {path: 0}
            app._blink_failed, app._blink_failed_stamp = {}, {}
            app._start_blink_preload()
            app._blink_reader.join(10)
            key = app._blink_key(path)
            self.assertEqual(app._blink_failed.get(key), core.READ_ERR_DAMAGED)
            self.assertEqual(app._blink_failed_stamp.get(key), app._blink_file_stamp(path))
        finally:
            app._blink_gen += 1
            app.blink_paths, app.blink_index, app._blink_failed, app._blink_failed_stamp = old
            app._blink_index_of = {p: i for i, p in enumerate(app.blink_paths)}
            app._blink_busy("")

    # ---- 4 ----
    def test_escape_at_the_chart_id_question_does_not_save(self):
        app, ca = self.app, self.ca
        seen = {}

        def escape(parent, title, message, choices, safe, cancel=None):
            seen.update(safe=safe, cancel=cancel, values=[v for v, _t in choices])
            return safe if cancel is None else cancel       # what Escape and the window's X answer

        old = (app.chart.get(), getattr(app, "_chart_auto", None))
        try:
            app.chart.set("")
            app._chart_auto = None
            with mock.patch.object(ca, "ask_choice", escape):
                self.assertFalse(app._chart_id_at_save())
            self.assertEqual(seen["cancel"], "stop")
            self.assertEqual(seen["safe"], "save")           # Enter still saves
            self.assertIn("stop", seen["values"])
        finally:
            app.chart.set(old[0])
            app._chart_auto = old[1]

    # ---- 5 ----
    def _pointing_setup(self, header, ra_deg, dec_deg, pattern=None):
        app = self.app
        old = {k: getattr(app, k, None) for k in ("preview", "preview_header", "preview_path", "mode_key",
                                                  "_header_pointing_said", "_radec_filled", "calibrated")}
        old["boxes"] = (app.ra_hours.get(), app.dec_deg.get(), app.pattern.get(), app._setup_value("focal_mm"),
                        app._setup_value("pixel_um"), app.binning.get())
        app.preview = np.zeros((2000, 3000), dtype=np.float32)
        app.preview_header, app.preview_path = header, "ref.fit"
        app._header_pointing_said, app._radec_filled, app.calibrated = None, None, None
        if app.mode_key == "discovery":
            app.mode_key = "variables"
        app.ra_hours.set(f"{ra_deg / 15.0:.6f}")
        app.dec_deg.set(f"{dec_deg:.5f}")
        if pattern is not None:
            app.pattern.set(pattern)
        return old

    def _pointing_restore(self, old):
        app = self.app
        ra, dec, pattern, focal, pixel, binning = old.pop("boxes")
        for k, v in old.items():
            setattr(app, k, v)
        app.ra_hours.set(ra)
        app.dec_deg.set(dec)
        app.pattern.set(pattern)
        for var, v in ((app.focal_mm, focal), (app.pixel_um, pixel), (app.binning, binning)):
            try:
                var.set(v)
            except Exception:
                pass

    def _note(self):
        said = []
        with mock.patch.object(self.app, "log", lambda text, *a, **k: said.append(text)):
            self.app._header_pointing_note()
        return said

    def test_optics_scale_matches_the_field_images(self):
        app = self.app
        old = self._pointing_setup({}, 100.0, 10.0)
        sol = app.chart_solution
        try:
            app.chart_solution = None
            app.focal_mm.set(2000.0)
            app.pixel_um.set(3.76)
            app.binning.set(1)
            color = next(p for p in (app.pattern.get(), "RGGB") if p != core.MONO)
            app.pattern.set(color)
            base = 3.76 / 2000.0 * 206.265
            self.assertAlmostEqual(app._optics_scale_arcsec(), base * 2, places=6)      # the 2x2 superpixel
            self.assertEqual(app._field_scale_arcsec(), (app._optics_scale_arcsec(), False))
            app.pattern.set(core.MONO)
            self.assertAlmostEqual(app._optics_scale_arcsec(), base, places=6)
            self.assertEqual(app._field_scale_arcsec(), (app._optics_scale_arcsec(), False))
        finally:
            app.chart_solution = sol
            self._pointing_restore(old)

    def test_header_note_has_a_floor_of_one_degree(self):
        app = self.app
        self.assertEqual(app.POINTING_FLOOR_DEG, 1.0)
        # a small field (about 0.2 deg): a header 0.8 deg off is no finding, 3 deg off is
        old = self._pointing_setup({"RA": 100.0, "DEC": 10.8}, 100.0, 10.0, pattern=core.MONO)
        try:
            app.focal_mm.set(4000.0)
            app.pixel_um.set(5.0)
            app.binning.set(1)
            self.assertEqual(self._note(), [])
            app.preview_header = {"RA": 100.0, "DEC": 13.0, "OBJECT": "elsewhere"}
            said = self._note()
            self.assertEqual(len(said), 1)
            self.assertTrue(said[0].startswith("⚠ The frame's header says the telescope pointed at"), said)
        finally:
            self._pointing_restore(old)

    def test_header_note_counts_the_color_cameras_superpixel(self):
        # 3000 px at 1.2"/px is a 1 deg field on a mono camera, 2 deg with the color camera's superpixel: a header
        # 1.5 deg off is outside the first and inside the second
        app = self.app
        old = self._pointing_setup({"RA": 100.0, "DEC": 11.5}, 100.0, 10.0, pattern=core.MONO)
        try:
            app.focal_mm.set(1000.0)
            app.pixel_um.set(1.2 / 206.265 * 1000.0)
            app.binning.set(1)
            self.assertEqual(len(self._note()), 1)
            app._header_pointing_said = None
            app.pattern.set("RGGB")
            self.assertEqual(self._note(), [])
        finally:
            self._pointing_restore(old)

    def test_header_note_precesses_a_header_of_the_date(self):
        # the telescope pointed 0.75 deg from the target; its header gives that in the equinox of 2026.9, which
        # reads as about 1.1 deg from the J2000 target until it is brought to J2000
        from astropy.coordinates import FK5, SkyCoord
        from astropy.time import Time
        import astropy.units as u
        app = self.app
        target = (100.0, 10.0)
        point = None
        for d_ra, d_dec in ((0.75, 0.0), (-0.75, 0.0), (0.0, 0.75), (0.0, -0.75), (0.53, 0.53), (-0.53, -0.53),
                            (0.53, -0.53), (-0.53, 0.53)):
            p2000 = (target[0] + d_ra / np.cos(np.radians(target[1])), target[1] + d_dec)
            now = SkyCoord(p2000[0] * u.deg, p2000[1] * u.deg, frame=FK5(equinox=Time(2000.0, format="jyear"))) \
                .transform_to(FK5(equinox=Time(2026.9, format="jyear")))
            raw = (float(now.ra.deg), float(now.dec.deg))
            if core.great_circle_deg(raw[0], raw[1], *target) > 1.02:
                point = raw
                break
        self.assertIsNotNone(point)
        old = self._pointing_setup({"RA": point[0], "DEC": point[1], "EQUINOX": 2026.9}, *target, pattern=core.MONO)
        try:
            app.focal_mm.set(4000.0)
            app.pixel_um.set(5.0)
            app.binning.set(1)
            self.assertEqual(self._note(), [])                         # within the 1 deg floor once in J2000
            app.preview_header = {"RA": point[0], "DEC": point[1]}     # no equinox named: taken as it stands
            self.assertEqual(len(self._note()), 1)
        finally:
            self._pointing_restore(old)

    def test_later_frames_use_the_floor_and_the_equinox(self):
        app = self.app
        heads = {"a.fit": {"RA": 100.0, "DEC": 10.9}, "b.fit": {"OBJECT": "x"}}
        with mock.patch.object(app, "_lights", lambda *a, **k: list(heads)), \
                mock.patch.object(core, "read_header", lambda p: heads[p]):
            # field 0.3 deg: 1.5 fields is 0.45 deg, but a header 0.9 deg off is still "near" (the floor)
            self.assertIs(app._later_frames_point_near(100.0, 10.0, 0.3), True)
            heads["a.fit"] = {"RA": 100.0, "DEC": 13.0}
            self.assertIs(app._later_frames_point_near(100.0, 10.0, 0.3), False)
            heads["a.fit"] = {"OBJECT": "y"}
            self.assertIsNone(app._later_frames_point_near(100.0, 10.0, 0.3))
        self.assertIn("core.header_pointing_j2000(core.read_header(path))",
                      _body(_src(), "    def _later_frames_point_near(self"))

    # ---- 6 ----
    def _far_run(self, later):
        app = self.app
        keep = {k: getattr(app, k, None) for k in ("preview", "preview_is_debayered", "chart_solution", "target_xy",
                                                   "chart_placed", "_radec_filled", "mode_key", "_target_off_words")}
        boxes = (app.ra_hours.get(), app.dec_deg.get())
        said, searched = [], []
        off = {"case": "far", "center": (100.0, 12.5), "sep_deg": 2.5, "field_deg": 0.5, "beyond_deg": 2.2,
               "way": "south"}
        try:
            app.preview = np.zeros((200, 300), dtype=np.float32)
            app.preview_is_debayered, app.chart_solution = True, {"scale": 1.0}
            app.target_xy, app.chart_placed, app._radec_filled = None, [], None
            if app.mode_key == "discovery":
                app.mode_key = "variables"
            app.ra_hours.set("6.666667")
            app.dec_deg.set("10.0")
            with mock.patch.object(core, "sky_to_pixel", lambda *a, **k: (-9000.0, -9000.0)), \
                    mock.patch.object(core, "target_vs_frame", lambda *a, **k: dict(off)), \
                    mock.patch.object(app, "_later_frames_point_near", lambda *a, **k: later), \
                    mock.patch.object(app, "_start_target_search", lambda *a, **k: searched.append(a) or True), \
                    mock.patch.object(app, "log", lambda text, *a, **k: said.append(text)):
                placed = app._auto_target_marker()
            return placed, said, searched, app._target_off_words
        finally:
            for k, v in keep.items():
                setattr(app, k, v)
            app.ra_hours.set(boxes[0])
            app.dec_deg.set(boxes[1])

    def test_far_frame_other_headers_point_away_stops(self):
        placed, said, searched, _words = self._far_run(False)
        self.assertFalse(placed)
        self.assertEqual(searched, [])
        self.assertEqual(len(said), 1)
        self.assertTrue(said[0].startswith("✖ The frames are centered at"), said)
        self.assertIn("The target isn't in this night's frames.", said[0])

    def test_far_frame_no_pointing_in_the_other_headers_searches(self):
        for later in (None, True):
            placed, said, searched, words = self._far_run(later)
            self.assertFalse(placed)
            self.assertEqual(len(searched), 1, later)                     # as 2.3.6: the next frames are looked at
            self.assertFalse([t for t in said if t.startswith("✖")], said)
            self.assertEqual(words, "this frame is centered 2.5° from the target, which lies to the south")

    # ---- 7 ----
    def test_field_image_button_is_live_again_after_a_run(self):
        app = self.app
        old = getattr(app, "_field_saved", None)
        try:
            for cancelled, want_cleared in ((False, True), (True, False)):
                app._field_saved = {"path": "x_field.png", "chart": "x_chart.png", "key": ("k",)}
                with mock.patch.object(app, "_end_job", lambda *a, **k: None), \
                        mock.patch.object(app, "_close_job_findings", lambda *a, **k: None), \
                        mock.patch.object(app, "_photometry_done_body", lambda *a, **k: False), \
                        mock.patch.object(app, "update_field_buttons", lambda *a, **k: None):
                    app._photometry_done([], "green", cancelled=cancelled)
                self.assertEqual(app._field_saved is None, want_cleared, cancelled)
        finally:
            app._field_saved = old

    def test_field_state_key_follows_the_radii(self):
        app = self.app
        info = {"title": "t", "marks": [(10.0, 20.0, "T", "#fff", "star")], "seq": []}
        old = app._setup_value("radius")
        try:
            k1 = app._field_state_key(info)
            app.radius.set(float(old or 6) + 2.0)
            k2 = app._field_state_key(info)
            self.assertNotEqual(k1, k2)
            app.radius.set(old)
            self.assertEqual(app._field_state_key(info), k1)
        finally:
            app.radius.set(old)

    # ---- 9 ----
    def pump(self, ms, until):
        import time
        end = time.monotonic() + ms / 1000.0
        while time.monotonic() < end:
            self.app.update()
            if until():
                return True
            time.sleep(0.01)
        return False

    def test_period_flash_has_a_word_and_skips_the_first_fill(self):
        app = self.app
        self.pump(app.PERIOD_FLASH_MS + 1500, lambda: not getattr(app, "_period_flashing", False))
        before = app.summary_vars["period"].get()
        try:
            app.summary_vars["period"].set("—")
            n0 = getattr(app, "period_flash_count", 0)
            app._fill_summary(["s"], ["Period 0.1205 d"], ["c"], [], [])      # a series just loaded: no flash
            self.assertEqual(getattr(app, "period_flash_count", 0), n0)
            self.assertEqual(str(app.period_box.cget("text")), "Period")
            app._fill_summary(["s"], ["Period 0.1379 d"], ["c"], [], [])      # a refit changed it: flash + word
            self.assertEqual(app.period_flash_count, n0 + 1)
            self.assertEqual(str(app.period_box.cget("text")), "Period: updated")
            self.assertEqual(str(app.period_label.cget("background")), app.PERIOD_FLASH_BG)
            self.assertTrue(self.pump(app.PERIOD_FLASH_MS + 1500, lambda: not app._period_flashing))
            self.assertEqual(str(app.period_box.cget("text")), "Period")
        finally:
            app.summary_vars["period"].set(before)

    # ---- 12 ----
    def test_load_series_bar_pauses_and_resumes(self):
        app = self.app
        seen = []

        def fake_file(path, step):
            seen.append(app.busy_bar.text)
            step(None)                              # a question waits
            seen.append(app.busy_bar.busy)
            step("the stars and settings")          # answered: the bar is back
            seen.append(app.busy_bar.text)
            step(None)

        with mock.patch.object(self.ca.filedialog, "askopenfilename", lambda **k: "/tmp/x.cvseries"), \
                mock.patch.object(app, "_load_series_file", fake_file), \
                mock.patch.object(app, "_offer_series_save", lambda *a, **k: True):
            app.load_series()
        self.assertEqual(seen[0], "Loading x.cvseries… reading the file")
        self.assertIs(seen[1], False)
        self.assertEqual(seen[2], "Loading x.cvseries… the stars and settings")
        self.assertFalse(app.busy_bar.busy)

    # ---- 8 ----
    def test_group_rows_are_still_six(self):
        app, tp = self.app, self.app.transit_page
        for groups in (tp.groups, app.out_groups):
            self.assertEqual(tuple(groups.boxes), group_rows.GROUP_KEYS)
            self.assertIs(groups.rows["analysis"], groups.rows["comps"])
            self.assertEqual(len(groups.separators), 5)
            for sep in groups.separators:
                self.assertEqual(str(sep.pack_info().get("pady")), "0")


if __name__ == "__main__":
    unittest.main()
