"""2.4.0 cleanup regression fixes (28 Nov): card 1, the fit helper never started on Windows (its reader thread's
pending read on the stdin pipe deadlocked numpy's DLL load), and John's nits N1-N5."""
import os
import struct
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fit_process                # noqa: E402
import clearv_app                 # noqa: E402

App = clearv_app.App
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(name="clearv_app.py"):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


class _Var:
    def __init__(self, v=""):
        self.v = v

    def get(self):
        return self.v

    def set(self, v):
        self.v = v


# ---- card 1: the helper starts, and nothing but our own copies touches the job pipes --------------------------

class TestHelperStarts(unittest.TestCase):
    def test_hello_comes_and_stdin_is_off_the_pipe(self):
        h = fit_process._Helper()          # raises if no hello within START_TIMEOUT_S
        try:
            self.assertTrue(h.alive())
            if sys.platform.startswith("linux"):
                h.send(("job", "os", "readlink", ("/proc/self/fd/0",), {}, [], False))
                kind, result = h.inbox.get(timeout=30)[:2]
                self.assertEqual(kind, "done")
                self.assertEqual(result, os.devnull)     # fd 0 is NUL, not the job pipe
        finally:
            fit_process._end(h)

    def test_imports_come_before_the_reader_thread(self):
        src = _src("fit_process.py")
        body = src[src.index("def _child_main"):]
        self.assertLess(body.index("import transit_core"), body.index("threading.Thread(target=read"))
        self.assertLess(body.index("_windows_std_handles_off_the_pipes()"), body.index("import numpy"))

    def test_the_windows_standard_handles_move(self):
        src = _src("fit_process.py")
        fn = src[src.index("def _windows_std_handles_off_the_pipes"):src.index("def _child_main")]
        self.assertIn("SetStdHandle(0xFFFFFFF6", fn)     # STD_INPUT_HANDLE -> NUL
        self.assertIn("SetStdHandle(0xFFFFFFF5", fn)     # STD_OUTPUT_HANDLE -> the stderr pipe
        self.assertIn("os.dup2(nul, 0)", fn)


class TestInWindowNote(unittest.TestCase):
    def setUp(self):
        self.was = (fit_process.ENABLED, fit_process._broken_why, fit_process._closed)

    def tearDown(self):
        fit_process.ENABLED, fit_process._broken_why, fit_process._closed = self.was

    def test_said_only_when_the_helper_is_not_in_use(self):
        fit_process.ENABLED, fit_process._closed = True, False
        fit_process._broken_why = ""
        self.assertEqual(fit_process.in_window_note(), "")
        fit_process._broken_why = "RuntimeError: x"
        self.assertIn("window's own process", fit_process.in_window_note())
        fit_process.ENABLED = False
        self.assertEqual(fit_process.in_window_note(), "")

    def test_the_transit_page_shows_it_while_fitting(self):
        ui = _src("transit_ui.py")
        i = ui.index("_slow = fit_process.in_window_note()")
        self.assertIn('[("info", FIT_RUNNING)] + ([("info", _slow)] if _slow else [])', ui[i:i + 300])


# ---- N1: a comp the edge rule leaves out is not logged with a ✓ -----------------------------------------------

class TestN1LeftOutComp(unittest.TestCase):
    def _app(self, note):
        return types.SimpleNamespace(_edge_note=lambda role, xy: note)

    def test_a_left_out_comp_gets_words(self):
        note = ("⚠ drifts to the frame edge later in the night: its sky ring (out to 43 px) runs past the edge on "
                "frames 2, 38, so it would be left out of the whole night — pick a star further in")
        out = App._comp_edge_unused_words(self._app(note), "comp4", (42, 696))
        self.assertTrue(out.startswith("drifts to the frame edge"))
        self.assertNotIn("⚠", out)

    def test_a_plain_edge_note_or_the_check_says_nothing(self):
        self.assertEqual(App._comp_edge_unused_words(self._app("120 px from the frame edge on this frame"),
                                                  "comp2", (120, 500)), "")
        self.assertEqual(App._comp_edge_unused_words(self._app("⚠ … left out of the whole night"), "check",
                                                  (42, 696)), "")

    def test_both_paths_use_it(self):
        src = _src()
        self.assertIn('It will not be used: {out}.", bad=True)', src)
        self.assertIn('It will not be used: {self._comp_edge_unused_words(role, xy)}.", bad=True)', src)


# ---- N2: the short-move line once per run ---------------------------------------------------------------------

class TestN2ShortMoveOnce(unittest.TestCase):
    def test_logged_by_run_note_only(self):
        src = _src()
        i = src.index("line = core.short_move_text(short, frame_no=lambda k: labels.get(k, k + 1))")
        part = src[i:i + 300]
        self.assertIn("self._run_note(line)", part)
        self.assertNotIn("self.log(line)", part)


# ---- N3: a Watch star refused before any ✓ --------------------------------------------------------------------

class TestN3WatchBeforeTick(unittest.TestCase):
    def test_click_path(self):
        src = _src()
        i = src.index("# 2.4.0 cleanup regression (N3): a Watch star is refused before anything says ✓ about it")
        j = src.index("# 2.4.0 cleanup (F4 card 5, D): a comp or check clicked is logged too")
        self.assertLess(i, j)

    def test_typed_path(self):
        src = _src()
        i = src.index("elif self._say_watch_clash(role):   # 2.4.0 step 4 (S033)")
        j = src.index('self._slot_say(role, ("" if bad else "✓ ") + msg)   # 2.2.14 (item 14)')
        self.assertLess(i, j)


# ---- N4: Close series takes the series' names out of unmarked slots -------------------------------------------

class TestN4CloseSeriesSlots(unittest.TestCase):
    def _app(self):
        cleared, said = [], []
        a = types.SimpleNamespace(
            _series_slot_names={"comp": clearv_app.name_key("TYC 4006-1008-1"),
                                "check": clearv_app.name_key("TYC 4006-832-1"),
                                "comp2": clearv_app.name_key("TYC 4006-1-1")},
            comp_xy=None, comp_name=_Var("TYC 4006-1008-1"),
            check_xy=None, check_name=_Var("TYC 4006-832-1"),
            comp2_xy=(500.0, 400.0), comp2_name=_Var("TYC 4006-1-1"),       # marked on this image: stays
            _slot_notes={"comp": ("x", "y", False)},
            clear_mark=lambda role, redraw=True: cleared.append(role),
            _update_pick_label=lambda: None, refresh_preview=lambda: None, update_star_panel=lambda: None,
            log=said.append)
        return a, cleared, said

    def test_unmarked_series_names_go(self):
        a, cleared, said = self._app()
        App._clear_series_slots(a)
        self.assertEqual(sorted(cleared), ["check", "comp"])
        self.assertEqual(a._series_slot_names, {})
        self.assertIn("Comp 1", said[0])

    def test_a_name_typed_since_stays(self):
        a, cleared, _ = self._app()
        a.check_name.set("TYC 1-2-3")
        App._clear_series_slots(a)
        self.assertEqual(cleared, ["comp"])

    def test_close_series_calls_it(self):
        src = _src()
        i = src.index("    def close_series(self, asked: bool = False):")
        self.assertIn("self._clear_series_slots()", src[i:i + 1500])
        self.assertIn("self._series_slot_names = {role: name_key(", src)


# ---- N5: no aperture verdict from another field's image -------------------------------------------------------

class TestN5ForeignImageAperture(unittest.TestCase):
    def test_no_aperture_line_on_another_fields_image(self):
        said = []
        a = types.SimpleNamespace(preview=object(), preview_is_debayered=True, _preview_foreign="HD 219134",
                                  _preview_token=0, log=said.append,
                                  radius=types.SimpleNamespace(get=lambda: (_ for _ in ()).throw(AssertionError)))
        App._aperture_caution(a)
        self.assertEqual(said, [])


if __name__ == "__main__":
    unittest.main()
