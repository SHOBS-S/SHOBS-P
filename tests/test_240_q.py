"""2.4.0 step 4, the second independent review's fixes (series side; the camera and placement cases are in
tests/test_240_p.py beside the first review's).

1  Measure skipped frames, Add series… and Remove night… save the open series again (a NameError, `save`, stopped
   each of them after the series had changed in memory).
2  Save never drops a comp the user cleared from the "comps in use" without re-deriving the nights: it asks once,
   with Load's words and buttons. Leave it out (the default) re-derives every night as Use comps… does, then saves;
   Keep it leaves the series as it is.
3  Load's question: Leave it out is the default (Enter); Escape and closing the window keep."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photometry_core as core    # noqa: E402
import test_240_m as M            # noqa: E402
from test_240_m import A, B, X, _old_meta, _points, _numbers   # noqa: E402

CAT = {A: 8.728, B: 11.31, X: 11.9}


class Asked:
    """Stands in for clearv_app.ask_choice: answers `value`, remembers the questions."""

    def __init__(self, value):
        self.value, self.calls = value, []

    def __call__(self, parent, title, message, choices, safe, cancel=None):
        self.calls.append({"message": message, "values": [v for v, _t in choices],
                           "texts": [t for _v, t in choices], "safe": safe, "cancel": cancel})
        return self.value


# ---- 1 -------------------------------------------------------------------------------------------------------------
class TestSeriesActionsSaveTheOpenSeries(M.AppCase):
    def _open(self):
        path = self.write("f.cvseries", _old_meta([A, B], CAT), _points([A, B], CAT))
        self.load(path)
        self.app._write_series(path)
        self.load(path)
        self.assertEqual(self.app.series_path, path)
        return path

    def _points_on_disk(self, path):
        return len(core.load_series(path)[1])

    def test_remove_night_saves(self):
        path = self._open()
        app = self.app
        n = len(app.observations)
        nights = sorted({o.night for o in app.observations})
        self.assertEqual(len(nights), 2)
        with mock.patch.object(self.ca.simpledialog, "askstring", lambda *a, **k: "1"), \
                mock.patch.object(self.ca, "ask_no", lambda *a, **k: True):
            app.remove_night()
        self.assertEqual(len(app.observations), n // 2)
        self.assertEqual(self._points_on_disk(path), n // 2, "the file is written")
        self.assertIn("Saved to f.cvseries", app.status.get())
        self.assertFalse(app.series_unsaved())

    def test_add_series_file_saves(self):
        path = self._open()
        app = self.app
        n = len(app.observations)
        other = self.write("g.cvseries", _old_meta([A, B], CAT), _points([A, B], CAT, nights=("JD2460960",)))
        extra = len(core.load_series(other)[1])
        with mock.patch.object(self.ca.filedialog, "askopenfilename", lambda *a, **k: other), \
                mock.patch.object(self.ca, "ask_no", lambda *a, **k: True):
            app.add_series_file()
        self.assertEqual(len(app.observations), n + extra)
        self.assertEqual(self._points_on_disk(path), n + extra, "the file is written")
        self.assertFalse(app.series_unsaved())

    def test_measure_skipped_frames_saves(self):
        path = self._open()
        app = self.app
        n = len(app.observations)
        night = sorted({o.night for o in app.observations})[-1]
        new = _points([A, B], CAT, nights=(night,))[:2]
        for i, o in enumerate(new):
            o.jd += 0.2 + 0.001 * i               # two frames of that night the run had skipped
            o.path = f"skipped_{i}.fit"
        app._prev_stars = (list(app.comps_used), app.check_used)
        app._run_skipped = []
        app._remeasure_done({"night": night}, new, app.mode_key)
        self.assertEqual(len(app.observations), n + 2)
        self.assertEqual(self._points_on_disk(path), n + 2, "the file is written")
        self.assertFalse(app.series_unsaved())

    def test_no_name_is_used_before_it_is_set_in_these_actions(self):
        # the three functions have no `save` argument: the pasted `and save` must not come back
        import ast
        src = open(self.ca.__file__, encoding="utf-8").read()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef):
                args = {a.arg for a in node.args.args + node.args.kwonlyargs}
                uses = {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and n.id == "save"
                        and isinstance(n.ctx, ast.Load)}
                stores = {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and n.id == "save"
                          and isinstance(n.ctx, ast.Store)}
                if uses and "save" not in args | stores:
                    self.fail(f"{node.name} (line {node.lineno}) reads `save` but has no such argument")


# ---- 2 -------------------------------------------------------------------------------------------------------------
class TestSaveAsksAboutAClearedComp(M.AppCase):
    def _open(self, name="f.cvseries"):
        path = self.write(name, _old_meta([A, B, X], CAT, extra={"active_comps": [A, B, X]}),
                          _points([A, B, X], CAT))
        self.load(path)
        self.app._write_series(path)          # the 2.4.0 format (star records)
        self.load(path)
        self.assertEqual(self.app.comp3_name.get(), X)
        return path

    def _shift_for(self, dv=0.3):
        app = self.app
        keep, app.series_path = app.series_path, ""      # about the numbers, not the file
        try:
            before = [o.mag for o in app.observations]
            app.set_catalog_mags({A: CAT[A] + dv})
            shifts = [now - was for was, now in zip(before, [o.mag for o in app.observations])]
            app.set_catalog_mags({A: CAT[A]})
        finally:
            app.series_path = keep
        return shifts

    def test_leave_it_out_rederives_every_night_then_saves(self):
        path = self._open()
        app = self.app
        before = _numbers(app.observations)
        app._user_clear_mark("comp3")
        ask = Asked("leave")
        self.lines.clear()
        with mock.patch.object(self.ca, "ask_choice", ask):
            app._write_series(path)
        self.assertEqual(len(ask.calls), 1, "asked once")
        q = ask.calls[0]
        self.assertIn(f"{X} is in the series' comps in use, but you cleared its comp box.", q["message"])
        self.assertIn("Leave it out: every night is re-derived with the other comps", q["message"])
        self.assertIn("as Use comps… does", q["message"])
        self.assertNotIn("⚠", q["message"])
        self.assertEqual((q["values"], q["texts"]), (["leave", "keep"], ["Leave it out", "Keep it"]))
        self.assertEqual((q["safe"], q["cancel"]), ("leave", "keep"), "Enter leaves it out; Escape keeps")
        self.assertEqual(len(self.said(f"{X} left out of the series' comps in use")), 1)
        self.assertEqual(app.active_comps, [A, B])
        left = _numbers(app.observations)
        self.assertNotEqual(left, before, "the nights were re-derived")
        self.assertFalse(app.series_unsaved())
        saved = json.load(open(path, encoding="utf-8"))["meta"]
        self.assertEqual(saved["active_comps"], [A, B])
        self.assertEqual(saved["comp3_name"], "")
        # the points are exactly what Use comps… gives for A and B
        twin = self._open("twin.cvseries")
        app.series_path = ""
        self.assertTrue(app.apply_comp_selection([A, B], confirmed=True))
        self.assertEqual(_numbers(app.observations), left)
        # after a reload: not asked, the same numbers, and the nights really are two-comp nights
        ask2 = Asked("keep")
        with mock.patch.object(self.ca, "ask_choice", ask2):
            self.load(path)
        self.assertEqual(ask2.calls, [])
        self.assertEqual(app.active_comps, [A, B])
        self.assertEqual([app.comp_name.get(), app.comp2_name.get(), app.comp3_name.get()], [A, B, ""])
        self.assertEqual(_numbers(app.observations), left)
        for shift in self._shift_for(0.3):
            self.assertAlmostEqual(shift, 0.15, places=12)    # +0.3 on one of the two comps in use
        self.assertTrue(os.path.isfile(twin))

    def test_keep_it_leaves_the_nights_and_the_choice(self):
        path = self._open()
        app = self.app
        before = _numbers(app.observations)
        app._user_clear_mark("comp3")
        ask = Asked("keep")
        self.lines.clear()
        with mock.patch.object(self.ca, "ask_choice", ask):
            app._write_series(path)
            app._write_series(path)          # the next save does not ask again
        self.assertEqual(len(ask.calls), 1)
        self.assertEqual(len(self.said(f"{X} kept in the series' comps in use")), 1)
        self.assertEqual(app.active_comps, [A, B, X])
        self.assertEqual(app.comp3_name.get(), "", "the box stays empty")
        self.assertEqual(_numbers(app.observations), before)
        saved = json.load(open(path, encoding="utf-8"))["meta"]
        self.assertEqual(saved["active_comps"], [A, B, X])
        self.assertEqual(saved["comp3_name"], "")
        ask2 = Asked("leave")
        with mock.patch.object(self.ca, "ask_choice", ask2):
            self.load(path)
        self.assertEqual(ask2.calls, [], "the 2.4.0 file's choice is kept without a question")
        self.assertEqual(app.active_comps, [A, B, X])
        self.assertEqual(_numbers(app.observations), before)
        for shift in self._shift_for(0.3):
            self.assertAlmostEqual(shift, 0.1, places=12)     # three-comp nights, three comps in use

    def test_put_back_before_the_save_is_not_asked(self):
        path = self._open()
        app = self.app
        app._user_clear_mark("comp3")
        app.comp3_name.set(X)
        ask = Asked("leave")
        with mock.patch.object(self.ca, "ask_choice", ask):
            app._write_series(path)
        self.assertEqual(ask.calls, [])
        self.assertEqual(json.load(open(path, encoding="utf-8"))["meta"]["active_comps"], [A, B, X])

    def test_a_refused_rederive_keeps(self):
        # the other comps cannot carry the nights (no catalog magnitude): nothing is trimmed
        path = self._open()
        app = self.app
        before = _numbers(app.observations)
        app._user_clear_mark("comp3")
        with mock.patch.object(self.ca, "ask_choice", Asked("leave")), \
                mock.patch.object(app, "_comp_selection_ok", lambda *a, **k: False):
            app._write_series(path)
        self.assertEqual(app.active_comps, [A, B, X])
        self.assertEqual(_numbers(app.observations), before)
        self.assertEqual(json.load(open(path, encoding="utf-8"))["meta"]["active_comps"], [A, B, X])

    def test_a_fingerprint_or_meta_read_never_asks_or_trims(self):
        path = self._open()
        app = self.app
        app._user_clear_mark("comp3")
        ask = Asked("leave")
        with mock.patch.object(self.ca, "ask_choice", ask):
            app._meta()
            app.series_unsaved()
        self.assertEqual(ask.calls, [])
        self.assertEqual(app.active_comps, [A, B, X])
        self.assertTrue(os.path.isfile(path))


# ---- 3 -------------------------------------------------------------------------------------------------------------
class TestLoadQuestionDefault(M.AppCase):
    def _file(self):
        meta = _old_meta([A, B, X], CAT, extra={"active_comps": [A, B, X]})
        meta["comp3_name"] = meta["comp3_mag"] = ""       # a 2.3.x file: the box saved empty, the choice not
        return self.write("S033.cvseries", meta, _points([A, B, X], CAT))

    def test_enter_leaves_it_out_and_escape_keeps(self):
        path = self._file()
        want = _numbers(core.load_series(path)[1])
        app = self.app
        ask = Asked("keep")                               # what Escape and closing the window answer
        with mock.patch.object(self.ca, "ask_choice", ask):
            self.load(path)
        q = ask.calls[0]
        self.assertEqual((q["safe"], q["cancel"]), ("leave", "keep"))
        self.assertEqual(app.active_comps, [A, B, X])
        self.assertEqual(_numbers(app.observations), want, "Escape re-derives nothing")
        self.assertFalse(app.series_unsaved())

    def test_escape_and_closing_answer_cancel_enter_answers_safe(self):
        # ask_choice itself: Escape / the window's close box give `cancel`, Enter the safe button
        src = open(self.ca.__file__, encoding="utf-8").read()
        body = src[src.index("def ask_choice("):src.index("def ", src.index("def ask_choice(") + 10 + 2000)]
        self.assertIn('away = safe if cancel is None else cancel', body)
        self.assertIn('win.protocol("WM_DELETE_WINDOW", lambda: pick(away))', body)
        self.assertIn('win.bind("<Escape>", lambda _e: pick(away))', body)


class TestBookkeepingWritesDoNotAsk(M.AppCase):
    """Re-review 3 (M1): a report note, a sent mark, a night note or planet-transit flags write the series without
    the cleared-comp question (the report on disk was made from the numbers as they are); the next real save asks."""

    def test_report_note_does_not_ask(self):
        app = self.app
        calls = []
        with mock.patch.object(app, "_save_comps_question", side_effect=lambda: calls.append(1) or []):
            path = self.write("r.cvseries", _old_meta([A, B], CAT), _points([A, B], CAT))
            self.load(path)
            calls.clear()
            app._write_series(path, ask=False)
            self.assertEqual(calls, [])
            app._write_series(path)
            self.assertEqual(calls, [1])

    def test_callers_pass_ask_false(self):
        import inspect
        src = inspect.getsource(self.ca.App)
        self.assertIn("self._report_series_override(), ask=False)", src)
        self.assertIn("self._write_series(self.series_path, ask=False)", src)
        self.assertIn("self._save_series_words(ask=False)", src)
        import multinight
        self.assertIn("override, ask=False)", inspect.getsource(multinight))


if __name__ == "__main__":
    unittest.main()
