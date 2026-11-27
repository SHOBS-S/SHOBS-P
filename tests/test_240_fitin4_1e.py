"""2.4.0 fit-in F4, package F4-1 (e): the step 8 nits N1 (1-4 frames beside a bad registration take the new
pointing's number) and N6 (Show log never leaves the page), the ✕ buttons inside the Stars panel, and the sheet sweep
of the Input / Calibrate and Solve / Photometry pages (m004 … x062, m509)."""
import os
import sys
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photometry_core as core    # noqa: E402
import clearv_app                 # noqa: E402

App = clearv_app.App
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(name="clearv_app.py"):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def _reg(dx):
    return {"dx": float(dx), "dy": 0.0, "flipped": False}


class TestSegmentBesideABadRegistration(unittest.TestCase):
    """step 8 nit N1."""

    def test_frames_cut_off_by_one_bad_registration_take_the_new_pointing(self):
        regs = [_reg(0)] * 8 + [_reg(200), _reg(200), _reg(900)] + [_reg(200)] * 6
        out = core.recenter_segments(regs, 1.0, 1.0)
        self.assertEqual(out["labels"][:8], [0] * 8)
        self.assertEqual(out["labels"][8:10], [1, 1])       # were 0, 0: the old pointing's number
        self.assertEqual(out["labels"][11:], [1] * 6)
        self.assertEqual(len(out["recenters"]), 1)
        self.assertEqual((out["recenters"][0]["from"], out["recenters"][0]["index"]), (7, 8))

    def test_an_out_and_back_excursion_is_still_no_recenter(self):
        regs = [_reg(0)] * 8 + [_reg(200), _reg(200)] + [_reg(0)] * 6
        out = core.recenter_segments(regs, 1.0, 1.0)
        self.assertEqual(set(out["labels"]), {0})
        self.assertEqual(out["recenters"], [])

    def test_a_short_run_somewhere_else_keeps_the_segment_it_left(self):
        regs = [_reg(0)] * 8 + [_reg(500), _reg(500)] + [_reg(200)] * 6
        out = core.recenter_segments(regs, 1.0, 1.0)
        self.assertEqual(out["labels"][8:10], [0, 0])
        self.assertEqual(out["labels"][10:], [1] * 6)


class TestWithAWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = App()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.destroy()
        except Exception:
            pass

    def test_show_log_opens_a_window_and_stays_on_the_page(self):
        app = self.app
        app.show_step(clearv_app.STEP_INPUT)
        app.update()
        app.log("a line said before the window opened", keep_status=True)
        app._findings_show_log()
        app.update()
        self.assertEqual(app.current_step, clearv_app.STEP_INPUT)
        box = app._log_window_box()   # merge: F4-2's Log window (the same window F4-1 built)
        self.assertIn("a line said before the window opened", box.get("1.0", "end"))
        app.log("a line said while it is open", keep_status=True)
        self.assertIn("a line said while it is open", box.get("1.0", "end"))
        win = box.winfo_toplevel()
        app._findings_show_log()            # a second press: the same window
        self.assertIs(app._log_window_box().winfo_toplevel(), win)
        win.destroy()
        app.update()
        app.log("after the window closed", keep_status=True)   # no error, and the box is forgotten
        self.assertIsNone(app._log_window_box())

    def test_show_log_on_the_photometry_page_shows_that_pages_log(self):
        app = self.app
        app.show_step(clearv_app.STEP_PHOTO)
        app.update()
        if app.photo_logf.winfo_manager():
            app.toggle_photo_log()
        app._findings_show_log()
        self.assertTrue(app.photo_logf.winfo_manager())
        self.assertEqual(app.current_step, clearv_app.STEP_PHOTO)

    def test_clear_buttons_are_inside_the_stars_panel(self):
        app = self.app
        for geo in ("900x600", "1100x700"):
            app.geometry(geo)
            app.show_step(clearv_app.STEP_PHOTO)
            for _ in range(5):
                app.update()
            canvas = app.panel_scroll.canvas
            for role in ("target", "comp", "check"):
                b = [w for w in app.star_row_widgets[role] if w.winfo_class() == "TButton"][0]
                if not b.winfo_ismapped():
                    continue
                right = b.winfo_rootx() + b.winfo_width() - canvas.winfo_rootx()
                self.assertLessEqual(right, canvas.winfo_width(), (geo, role))
                self.assertGreaterEqual(b.winfo_width(), b.winfo_reqwidth() - 1, (geo, role))

    def test_site_hint_shows_and_goes(self):
        app = self.app
        app._site_hint("site", "The Site boxes are empty or not a number: fill them in.")
        self.assertIn("Site boxes", app.site_hint_text.get())
        self.assertTrue(app.site_hint_label.winfo_manager())
        app._site_hint("tz", "This computer's time zone (X) is not in the list")
        self.assertIn("time zone", app.site_hint_text.get())
        app._site_hint("site")
        self.assertNotIn("Site boxes", app.site_hint_text.get())
        app._site_hint("tz")
        self.assertEqual(app.site_hint_text.get(), "")
        self.assertFalse(app.site_hint_label.winfo_manager())

    def test_solve_names_the_one_box_that_is_wrong(self):
        app = self.app
        with mock.patch.object(App, "_setup_value", lambda self, k: "" if k == "focal_mm" else "3.76"):
            app.ra_hours.set("12.5")
            app.dec_deg.set("30")
            text = app._solve_input_problem(ValueError("x"))
            self.assertTrue(text.startswith("Focal length is empty"), text)
            self.assertNotIn("Pixel size", text)
            app.dec_deg.set("thirty")
            self.assertTrue(app._solve_input_problem(ValueError("x")).startswith("Dec 'thirty' is not a number"))
        app.dec_deg.set("")
        app.ra_hours.set("")
        with mock.patch.object(App, "_setup_value", lambda self, k: "1000"):
            self.assertIn("boom", app._solve_input_problem(ValueError("boom")))

    def _aperture_case(self, typed):
        app = self.app
        app.__dict__.pop("_aperture_asked", None)
        app.aperture_mm.set("152")
        app.settings["aperture_scopes"] = []
        app._lights_scope = {"telescope": "", "camera": "MicroObservatory", "focal_mm": 650.0}
        app._remember_aperture_scope()
        said = []
        with mock.patch.object(app, "_ask_aperture_mm", return_value=typed) as ask, \
                mock.patch.object(app, "after_idle"), mock.patch.object(app, "log", lambda t, **k: said.append(t)):
            app._check_aperture_scope({"INSTRUME": "ASI2600MM", "FOCALLEN": 2098})
        return ask, said

    def test_aperture_typed_in_the_question(self):
        """m143."""
        ask, said = self._aperture_case(203.0)
        self.assertIn("Type this telescope's aperture in mm", ask.call_args[0][0])
        self.assertEqual(float(self.app.aperture_mm.get()), 203.0)
        self.assertTrue(any("Telescope aperture set to 203 mm" in t for t in said), said)

    def test_aperture_ok_on_the_value_shown_confirms_it(self):
        _ask, said = self._aperture_case(152.0)
        self.assertEqual(float(self.app.aperture_mm.get()), 152.0)
        self.assertTrue(any("confirmed" in t for t in said), said)

    def test_aperture_cancel_changes_nothing(self):
        _ask, said = self._aperture_case(None)
        self.assertEqual(float(self.app.aperture_mm.get()), 152.0)
        self.assertTrue(any("type its aperture in the Optics box" in t for t in said), said)

    def test_planet_not_found_is_a_line_not_a_box(self):
        body = _src()
        i = body.index("m064")
        part = body[i:i + 500]
        self.assertIn("check the spelling", part)
        self.assertNotIn("showerror", part)
        self.assertFalse("Planet lookup failed" in body)


class TestCompAtTheEdgeIsRefused(unittest.TestCase):
    def _app(self, sky_out="20"):
        return types.SimpleNamespace(preview=np.zeros((200, 300)), sky_out=types.SimpleNamespace(get=lambda: sky_out))

    def test_ring_off_the_frame(self):
        text = App._comp_ring_off_frame(self._app(), (10.0, 100.0))
        self.assertTrue(text.startswith("Not taken as a comp"), text)
        self.assertIn("10 px from the frame edge", text)
        self.assertIn("at least 25 px in", text)

    def test_a_star_far_enough_in_is_taken(self):
        self.assertEqual(App._comp_ring_off_frame(self._app(), (150.0, 100.0)), "")
        self.assertEqual(App._comp_ring_off_frame(self._app(), (25.0, 100.0)), "")

    def test_only_comps_are_refused_in_the_click(self):
        src = _src()
        i = src.index("off = self._comp_ring_off_frame(xy)")
        self.assertTrue("elif comp_number(mode):" in src[i - 120:i])


class TestNeighborAtPick(unittest.TestCase):
    def _app(self, sources):
        said = []
        app = types.SimpleNamespace(
            target_xy=None, comp_xy=(50.0, 50.0), preview=np.zeros((100, 100)), preview_is_debayered=True,
            radius=types.SimpleNamespace(get=lambda: "8"), _preview_token=3, _pick_sources=(3, sources),
            comp_name=types.SimpleNamespace(get=lambda: "TYC 1-2-3"), _slot_notes={},
            log=lambda t, **k: said.append(t), status=types.SimpleNamespace(set=lambda t: said.append("S:" + t)),
            update_star_panel=lambda: None)
        return app, said

    def test_a_star_inside_the_aperture_is_said_on_the_slot(self):
        app, said = self._app([(50.0, 50.0), (55.0, 50.0), (90.0, 90.0)])
        App._neighbor_at_pick(app, "comp")
        self.assertTrue(said and said[0].startswith("⚠ Comp 1: 1 other star inside the aperture (r = 8 px)"), said)
        self.assertEqual(app._slot_notes["comp"][0], "TYC 1-2-3")
        self.assertTrue(app._slot_notes["comp"][2])

    def test_nothing_is_said_without_a_neighbor(self):
        app, said = self._app([(50.0, 50.0), (90.0, 90.0)])
        App._neighbor_at_pick(app, "comp")
        self.assertEqual(said, [])
        self.assertEqual(app._slot_notes, {})


class TestSameStarByLookup(unittest.TestCase):
    """m509."""

    def _app(self, ra="", dec=""):
        return types.SimpleNamespace(ra_hours=types.SimpleNamespace(get=lambda: ra),
                                     dec_deg=types.SimpleNamespace(get=lambda: dec))

    def test_two_names_of_one_star(self):
        with mock.patch.object(core, "lookup_target", return_value={"ra_hours": 23.22, "dec_deg": 57.17}) as look:
            self.assertIs(App._same_star_by_lookup(self._app(), "HIP 114622", {"star_id": "HD 219134"}), True)
            self.assertEqual(look.call_count, 2)

    def test_different_stars(self):
        answers = {"A": {"ra_hours": 1.0, "dec_deg": 10.0}, "B": {"ra_hours": 5.0, "dec_deg": -20.0}}
        with mock.patch.object(core, "lookup_target", side_effect=lambda n: answers[n]):
            self.assertIs(App._same_star_by_lookup(self._app(), "A", {"star_id": "B"}), False)

    def test_known_positions_are_not_looked_up_again(self):
        with mock.patch.object(core, "lookup_target", return_value={"ra_hours": 1.0, "dec_deg": 10.0}) as look:
            got = App._same_star_by_lookup(self._app("1.0", "10.0"), "A", {"star_id": "B"})
            self.assertIs(got, True)
            look.assert_called_once_with("B")

    def test_a_failed_lookup_leaves_it_to_the_question(self):
        with mock.patch.object(core, "lookup_target", side_effect=ValueError("no answer")):
            self.assertIsNone(App._same_star_by_lookup(self._app(), "A", {"star_id": "B"}))
        src = _src()
        i = src.index("same = self._same_star_by_lookup(")
        self.assertLess(i, src.index("Without positions to compare", i))


class TestClickAgainWords(unittest.TestCase):
    def _app(self, ra="", comp2=""):
        ns = types.SimpleNamespace(ra_hours=types.SimpleNamespace(get=lambda: ra),
                                   dec_deg=types.SimpleNamespace(get=lambda: "10" if ra else ""))
        for r in ["check"] + list(clearv_app.COMP_ROLES):
            setattr(ns, f"{r}_name", types.SimpleNamespace(get=lambda r=r: comp2 if r == "comp2" else "TYC 1-2-3"))
        return ns

    def test_target_without_radec_and_an_unnamed_watch_star(self):
        text = App._click_again_words(self._app(), ["Target", "Comp 1", "W2", "V0345 Cas"])
        self.assertEqual(text, " Click again after the solve: Target, W2.")

    def test_nothing_to_click(self):
        text = App._click_again_words(self._app(ra="1.5"), ["Target", "Comp 1", "Check"])
        self.assertEqual(text, " Nothing to click again: wait for the solve.")

    def test_a_comp_without_a_name(self):
        self.assertIn("Comp 2", App._click_again_words(self._app(ra="1.5"), ["Comp 2"]))


class TestSweepWording(unittest.TestCase):
    """The words the sheet's verdicts ask for are in the code, and the retired ones are gone."""

    def test_new_words(self):
        src = _src()
        for words in (
                "pick it in the Filter box (Calibrate page)",                       # m004
                "Calibrate asks ",                                                  # m120
                "No light frames found: the Lights folder is empty",                # m177
                "is reported as \"",                                                # m113 (see below)
                "The scan of the stacks was skipped",                               # m371
                "Check that this file can be written",                              # m519
                "Cloud limit on the Output page changes",                           # m476
                "The step may not have finished: run it ",                          # m014
                "Choose another folder",                                            # m617
                "Check RA/Dec on the Input",                                        # x074
                "Click it by hand to measure it anyway",                            # m203
                "mag column, or untick ",                                          # m549 (merge: F4-2 words)
                "Aperture test not done",                                           # m241
                "COLOR_FIT_FAILED",                                                 # m422, m428
                "type it in the Optics ",                                           # x042
                "set them on the Input page",                                       # x041
                "pick comps this night has",                                        # m435
        ):
            if words == "is reported as \"":
                self.assertTrue("TG/TR/TB" in src)
                continue
            self.assertTrue(words in src, words)

    def test_retired_words_and_boxes(self):
        src = _src()
        for words in ("Check RA, Dec, focal length, and pixel size: {exc}",
                      "{type(exc).__name__}: {exc} — continuing without the color term",
                      "Pick another star.\")",
                      "needs a catalog {core.band_label",
                      "(stacking step skipped: {type(exc).__name__}"):
            self.assertFalse(words in src, words)
        for box in ('showinfo(APP_TITLE, "Calibrate first (Calibrate page). Star positions',
                    'showinfo(APP_TITLE, f"All {MAX_COMPS} comparison stars',
                    'showinfo(APP_TITLE, "No night has frames in a predicted'):
            self.assertFalse(box in src, box)

    def test_color_fit_line_is_one_wording(self):
        text = App.COLOR_FIT_FAILED.format(why="singular matrix")
        self.assertTrue(text.startswith("⚠ The color fit could not be made (singular matrix)"))
        self.assertIn("untick Also measure R and B", text)

    def test_scint_note_says_the_fix(self):
        app = types.SimpleNamespace(_scint_setup=lambda: None, observations=[],
                                    use_scint=types.SimpleNamespace(get=lambda: True))
        self.assertIn("type it in the Optics box, Input page", App._scint_note(app))
        app.use_scint = types.SimpleNamespace(get=lambda: False)
        self.assertEqual(App._scint_note(app), "scintillation not included (switched off)")


class TestCameraQuestionAskedAgain(unittest.TestCase):
    """m120."""

    def _app(self, answer_second):
        said = []
        app = types.SimpleNamespace(light_dir=types.SimpleNamespace(get=lambda: "/n/lights"),
                                    _sensor_unanswered="/n/lights", _pattern_user_set=False, _sensor_asked_for="/n/lights",
                                    log=lambda t, **k: said.append(t))

        def ask(contrast=None):
            app.asked = app._sensor_asked_for is None
            if answer_second:
                app._sensor_unanswered = None
        app._ask_sensor = ask
        return app, said

    def test_calibrate_asks_again_and_goes_on_when_answered(self):
        app, said = self._app(True)
        self.assertTrue(App._sensor_question_again(app))
        self.assertTrue(app.asked)
        self.assertEqual(said, [])

    def test_calibrate_does_not_start_on_a_guess(self):
        app, said = self._app(False)
        self.assertFalse(App._sensor_question_again(app))
        self.assertTrue(said[0].startswith("Calibration not started: SHOBS-P needs to know the camera type"))

    def test_a_pattern_picked_by_hand_answers_it(self):
        app, _ = self._app(False)
        app._pattern_user_set = True
        self.assertTrue(App._sensor_question_again(app))
        self.assertIsNone(app._sensor_unanswered)

    def test_other_lights_are_not_asked(self):
        app, _ = self._app(False)
        app._sensor_unanswered = "/other"
        self.assertTrue(App._sensor_question_again(app))


if __name__ == "__main__":
    unittest.main()
