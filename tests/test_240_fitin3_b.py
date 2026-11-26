"""2.4.0 fit-in F3, builder B (Photometry page, the run, Discovery scan, core backstops, stacking).

Merges (#79 "one fact, one message"): m220 x148 m477 m202 m311 m314 m379 x079 m275 m558 m230 m285 m286 m471 x144
x149 x203 x063 m740 x237 x140 x169 x189 m353. Areas: E (one verdict per comp slot, John's call 8), F (one end-of-run
summary, call 2: no box, the Findings panel opens by itself), K (scan limits in the scan summary). F2 leftovers:
F2-d (the step strip never says Ready while Run is greyed), F2-e (one Back on the Field scan page)."""
import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ["SHOBS_ONSKY_OFF"] = "1"

import numpy as np  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")

import photometry_core as core  # noqa: E402
import stack_core  # noqa: E402
import clearv_app as ca  # noqa: E402
import test_230_d as D  # noqa: E402


def _src(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


def _body(src, name):
    """The source of one method (from its def line to the next def at the same depth)."""
    return src.split(name, 1)[1].split("\n    def ", 1)[0]


# ---- the core backstops (K05, K20, K22) ---------------------------------------------------------------------------
class TestCoreBackstops(unittest.TestCase):
    def test_astropy_missing_is_one_message_with_the_repair(self):
        text = str(core.need_astropy("reading frames"))
        self.assertEqual(text, "A package SHOBS-P needs (astropy) is missing, so reading frames cannot be done. Run "
                               "Create shortcuts.bat (Mac/Linux: the start file) again; it repairs the packages.")
        self.assertIsInstance(core.need_astropy("x"), RuntimeError)
        with mock.patch.object(core, "fits", None):
            for call, what in ((lambda: core.read_fits("a.fits"), "reading frames"),
                               (lambda: core.read_header("a.fits"), "reading frames"),
                               (lambda: core.write_master("a.fits", np.zeros((2, 2)), {}), "saving masters")):
                with self.assertRaises(RuntimeError) as err:
                    call()
                self.assertIn(f"so {what} cannot be done", str(err.exception))
        with mock.patch.object(stack_core, "fits", None):
            with self.assertRaises(RuntimeError) as err:
                stack_core.write_stack("a.fits", np.zeros((2, 2)), {})
            self.assertIn("so writing stacks cannot be done", str(err.exception))
        for name in ("photometry_core.py", "stack_core.py"):
            self.assertNotIn("astropy is required", _src(name), name)
            self.assertNotIn("pip install astropy", _src(name), name)

    def test_bad_bayer_pattern_one_text_with_what_to_do(self):
        said = []
        for call in (lambda: core.debayer(np.zeros((4, 4)), "XXXX"), lambda: core.debayer_weights("XXXX", "luminance")):
            with self.assertRaises(ValueError) as err:
                call()
            said.append(str(err.exception))
        self.assertEqual(said[0], said[1])
        self.assertEqual(said[0], "Bayer pattern 'XXXX' is not one SHOBS-P knows. Pick the camera's pattern in the "
                                  "Bayer pattern box (Calibrate page).")

    def test_backstops_say_what_the_page_says(self):
        with self.assertRaises(ValueError) as err:
            core.measure_aperture(np.zeros((40, 40)), 20, 20, 8, 6, 12)
        self.assertEqual(str(err.exception), "The aperture sizes must be in order: Aperture r < sky in < sky out.")
        img = np.zeros((60, 60))
        head = {"EXPTIME": 1.0, "DATE-OBS": "2026-10-02T03:00:00"}
        with self.assertRaises(ValueError) as err:
            core.reduce_frame(img, head, core.Star("T", 30, 30), [], None, 4, 7, 10)
        self.assertEqual(str(err.exception), "Mark at least one comparison star (Comps, then click the star).")
        with self.assertRaises(ValueError) as err:
            core.reduce_frame(img, head, core.Star("T", 30, 30), [core.Star("HD 1", 10, 10, None)], None,
                              4, 7, 10)
        self.assertEqual(str(err.exception), "HD 1 has no catalog magnitude in this band: type it in the box next to "
                                             "the name, or pick another star.")
        # the page's own check uses the same sentence (clearv_app._aperture_problems), and both app backstops reuse it
        src = _src("clearv_app.py")
        self.assertEqual(src.count("raise ValueError(self._aperture_problems()[0] or core.APERTURE_ORDER_TEXT)"), 2)
        self.assertNotIn("Need aperture r < sky in < sky out", src)
        self.assertIn(core.APERTURE_ORDER_TEXT[:-1], src)

    def test_edge_records(self):
        NS = types.SimpleNamespace
        obs = [NS(pos={"c": [(5.0, 50.0), (100.0, 50.0)], "k": (1.0, 50.0), "s": {"SP": (3.0, 3.0)}}),
               NS(pos={"c": [(5.0, 50.0), (100.0, 50.0)], "k": (-0.4, 52.0), "s": {"SP": (3.0, 3.0)}})]
        recs = core.edge_records(obs, (100, 200), ["A", "B"], "K", 30.0)
        by = {r["label"]: r for r in recs}
        self.assertEqual(set(by), {"comp A", "check K", "spare SP"})
        self.assertEqual((by["comp A"]["kind"], by["comp A"]["name"]), ("comp", "A"))
        self.assertTrue(by["comp A"]["near"])
        self.assertAlmostEqual(by["comp A"]["closest"], 5.0)
        self.assertIsNone(by["comp A"]["flat_pct"])
        self.assertEqual(by["comp A"]["words"], "comes within 5 px of the frame edge (stars at least 30 px in are safe)")
        self.assertTrue(by["check K"]["words"].startswith("reaches the frame edge"))
        # the sentence form is built from the records, and no longer says "Consider leaving it out" with no how
        text = " ".join(core.edge_problems(obs, (100, 200), ["A", "B"], "K", 30.0))
        self.assertIn("comp A comes within 5 px of the frame edge", text)
        self.assertNotIn("Consider", text)
        # a weak part of the flat is a record of its own kind
        flat = np.ones((100, 200))
        flat[40:60, 90:110] = 0.5
        weak = core.edge_records(obs, (100, 200), ["A", "B"], "", 2.0, flat)
        self.assertEqual([r["label"] for r in weak if r["flat_pct"] is not None], ["comp B"])
        self.assertIn("sits where the flat is only 50% of the center", weak[-1]["words"])


# ---- the app's sentence builders (no window needed) -----------------------------------------------------------
class TestWords(unittest.TestCase):
    def test_slot_verdict_worst_first_one_mark(self):
        R = ca.SLOT_RANK
        reasons = [(R["color"], "warn", "⚠ B−V 1.21 against the target's 0.62: a comp of another color dims …"),
                   (R["edge"], "warn", "⚠ only 12 px from the frame edge: its sky ring (out to 22 px) needs …"),
                   (R["linear"], "warn", "⚠ raw peak 61,000 ADU is over the linear limit (60,000): the camera's "
                                         "response bends there, so this star reads too faint — pick a fainter comp.")]
        text, style = ca.slot_verdict(reasons, ["B−V 1.21 (APASS DR9)"], source=["V from APASS DR9"])
        self.assertEqual(text.count("⚠"), 1)
        self.assertTrue(text.startswith("⚠ raw peak 61,000 ADU is over the linear limit (60,000): the camera's"), text)
        self.assertIn("   also: only 12 px from the frame edge; B−V 1.21 against the target's 0.62   ", text)
        self.assertTrue(text.endswith("B−V 1.21 (APASS DR9)   V from APASS DR9"), text)
        self.assertEqual(style, "Warn.TLabel")
        # a known variable outranks everything and is the only red one
        text, style = ca.slot_verdict(reasons + [(R["variable"], "bad", "V1 Cas (EW, VSX)")], [])
        self.assertTrue(text.startswith("⚠ V1 Cas (EW, VSX)   also: raw peak 61,000 ADU is over the linear limit"), text)
        self.assertEqual(style, "Bad.TLabel")
        self.assertEqual(text.count("⚠"), 1)
        # one reason: no "also"
        text, style = ca.slot_verdict(reasons[:1], ["x"])
        self.assertNotIn("also:", text)

    def test_slot_verdict_good_and_plain(self):
        text, style = ca.slot_verdict([], ["B−V 0.71 (APASS DR9)", "+0.3 mag from the target",
                                           "not a known or suspected variable (VSX + SIMBAD)"],
                                      source=["V from APASS DR9"], good="comp")
        self.assertEqual(text, "✓ good comp: B−V 0.71 (APASS DR9), +0.3 mag from the target, not a known or suspected "
                               "variable (VSX + SIMBAD)   V from APASS DR9")
        self.assertEqual(style, "Good.TLabel")
        text, style = ca.slot_verdict([], ["B−V 0.71", "variable check: Solve first"])
        self.assertEqual((text, style), ("B−V 0.71   variable check: Solve first", "Hint.TLabel"))
        text, _style = ca.slot_verdict([(3, "warn", "⚠ x: y")], ["n"], first=["✓ Comp 1 renamed to A; …"])
        self.assertTrue(text.startswith("✓ Comp 1 renamed to A; …   ⚠ x: y   n"), text)

    def test_reason_short(self):
        self.assertEqual(ca.reason_short("⚠ only 12 px from the frame edge: its sky ring …"),
                         "only 12 px from the frame edge")
        self.assertEqual(ca.reason_short("HD 1 has no V in APASS DR9; type its V or pick another star"),
                         "HD 1 has no V in APASS DR9")
        self.assertEqual(ca.reason_short("⚠ drifts off the frame later in the night — pick a star further in"),
                         "drifts off the frame later in the night")

    def test_recover_words_one_builder(self):
        self.assertEqual(ca.recover_words(2, "C3", True),
                         "2 frames were skipped because of C3, which is no longer a comp: Measure skipped frames "
                         "measures them with the comps in use.")
        self.assertEqual(ca.recover_words(1, "C3", False),
                         "⚠ 1 frame was skipped because of C3, which is no longer a comp — Run photometry again to "
                         "measure it.")
        self.assertEqual(ca.recover_words(3, "C3", False, "the night of 2 Oct"),
                         "⚠ 3 frames of the night of 2 Oct were skipped because of C3, which is no longer a comp — "
                         "Run photometry on that night's frames again to measure them.")
        applied = _body(_src("clearv_app.py"), "    def _comp_selection_applied(self")
        self.assertEqual(applied.count("recover_words("), 2)
        self.assertNotIn("which is no longer a comp", applied)   # no wording of its own left there
        self.assertNotIn("which is no longer a comp", _body(_src("clearv_app.py"), "    def comp_skip_text(self"))

    def test_scan_limits_text(self):
        self.assertEqual(ca.scan_limits_text({}), "")
        self.assertEqual(ca.scan_limits_text(None), "")
        self.assertEqual(ca.scan_limits_text({"limits": ["a", "", "b"]}), "a; b")

    def test_removed_copies(self):
        src = _src("clearv_app.py")
        self.assertNotIn("cannot see changes faster than one stack", _src("stack_ui.py"))             # m740
        self.assertNotIn("self.status.set", _body(src, "    def _warn_marker_offsets(self"))           # m379
        self.assertNotIn("Try Solve again later", src)                                                 # x079
        self.assertNotIn("click the target by hand \"\n                     \"on a frame that shows it", src)   # m202
        self.assertNotIn("is not in these \"", src)                                                    # m296
        self.assertNotIn("def _warn_run_skips", src)                                                   # area F
        self.assertNotIn("def _warn_edges", src)
        self.assertNotIn("The Photometry page shows this beside the View box", src)
        self.assertNotIn("question ({where}) answered", src)                                           # m477
        self.assertNotIn("not marked on this image", src)                                              # m275
        self.assertNotIn("Could not find {text}", src)                                                 # m285 / m286
        # m311: no Log twin before the header-solution question; m314: one sentence for the Log and the box
        done = src.split("if diag.get(\"from_header\"):", 1)[1].split("h, w = self.preview.shape", 1)[0]
        self.assertNotIn("Check the marker is on your star.", done)
        self.assertIn("Target marker kept at (", done)
        self.assertIn("self.log(moved)\n            messagebox.showwarning(APP_TITLE, moved)", done)
        self.assertNotIn("showinfo", done)
        # x079: the solve worker's outage uses the shared sentence
        self.assertIn("catalog_outage_text(failed, self._chart_diag[\"catalogs\"])", src)
        # F2-e: the Field scan page builds no Back of its own (the Results row has the one)
        self.assertNotIn("ttk.Button", _body(src, "    def _page_discovery_output(self"))

    def test_outage_words_reused(self):
        text = ca.catalog_outage_text({"Gaia DR3": "HTTP 503", "APASS DR9": ""}, {})
        self.assertIn("Gaia DR3 (HTTP 503) and APASS DR9 (no answer) did not answer", text)
        self.assertTrue(text.endswith("Solve again in a few minutes."))

    def test_scan_header_starts_with_the_limits(self):
        said = []
        st = types.SimpleNamespace(
            r={"labels": ["a"] * 8, "frames": 12, "candidates": [], "near": [],
               "limits": ["brightest 800 measured (limit reached; raise Most stars to measure to go fainter)"]},
            _scan_suspects=lambda: [], _cal_text="instrumental", _header_tail="tail",
            header_text=types.SimpleNamespace(set=said.append))
        ca.ScanView._update_header(st)
        self.assertTrue(said[0].startswith("Limits of this scan: brightest 800 measured (limit reached; raise Most "
                                           "stars to measure to go fainter). 8 stars, 12 frames."), said[0])
        st.r["limits"] = []
        ca.ScanView._update_header(st)
        self.assertTrue(said[1].startswith("8 stars, 12 frames."), said[1])

    def test_run_summary_no_box_and_opens_findings(self):
        """Area F with stand-ins: a comp left out of frames is a ⚠ line (logged once) and opens the Findings panel;
        a few skipped frames alone are a plain note and open nothing. Never a message box."""
        App = ca.App
        for text, n_warn, opened in (
                ("1 frame skipped: could not align (frames 11).", 0, 0),
                ("⚠ 40 frames skipped: could not align (frames 1-40).", 1, 1),
                ("⚠ C3 left out of 19 frames (no flux above sky; frames 5-23); those frames use the other comps. "
                 "Leave it out with Use comps… (Output page).", 1, 1),
                ("1 frame skipped: could not align (frames 11).\n⚠ check star left out of 40 frames (off the frame "
                 "edge; frames 1-40); no check value there.", 1, 1)):
            logged, opens = [], []
            st = types.SimpleNamespace(
                _night_info=lambda n: {}, _msg_level=lambda ln: App._msg_level(ln),
                _run_edge_notes=lambda n, p, d: ({}, []), comp_skip_log_text=lambda n, edges=None, t=text: t,
                log=lambda t, **k: logged.append((t, k)), _job_findings=[],
                _open_findings_for_run=lambda: opens.append(1))
            with mock.patch.object(ca.messagebox, "showwarning") as warn:
                said = App._run_summary(st, "JD1", [])
            warn.assert_not_called()
            self.assertEqual(len(said), n_warn, text)
            self.assertEqual(len(opens), opened, text)
            self.assertEqual([t for t, _k in logged], [f"JD1: {ln}" for ln in text.splitlines()])
            self.assertEqual([k["level"] for _t, k in logged],
                             ["note" if not ln.startswith("⚠") else "warn" for ln in text.splitlines()])
        # a ~180° turn or an ensemble zero-point ⚠ of this run opens it too, with nothing left out
        for info, job in (({"flip_like": [{"turn": 180}]}, []), ({}, [("warn", "⚠ Ensemble zero point, JD1: …")])):
            opens = []
            st = types.SimpleNamespace(
                _night_info=lambda n, i=info: i, _msg_level=App._msg_level, _run_edge_notes=lambda n, p, d: ({}, []),
                comp_skip_log_text=lambda n, edges=None: "", log=lambda t, **k: None, _job_findings=job,
                _open_findings_for_run=lambda: opens.append(1))
            App._run_summary(st, "JD1", [])
            self.assertEqual(opens, [1])


# ---- with the window ---------------------------------------------------------------------------------------------
@unittest.skipUnless(D.HAVE_TK, "needs a display")
class TestInApp(unittest.TestCase):
    _setup = D.TestRunInApp._setup
    _run = D.TestRunInApp._run

    @classmethod
    def setUpClass(cls):
        cls.ca = ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test240f3b.log")
        cls.app = ca.App()
        cls.app.withdraw()
        cls.patches = [mock.patch.object(ca.messagebox, n, return_value=True)
                       for n in ("showwarning", "showinfo", "showerror", "askyesno")]
        cls.boxes = {n: p.start() for n, p in zip(("showwarning", "showinfo", "showerror", "askyesno"), cls.patches)}
        cls.app._save_settings = lambda *a, **k: None

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.app.destroy()

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for box in self.boxes.values():
            box.reset_mock()
            box.return_value = True
        self.app.findings.clear()
        self.logged = []
        real = self.app.log

        def log(text, *a, **k):
            self.logged.append(str(text))
            return real(text, *a, **k)
        self._log = mock.patch.object(self.app, "log", side_effect=log)
        self._log.start()

    def tearDown(self):
        self._log.stop()
        win = getattr(self.app, "_findings_win", None)
        try:
            if win is not None and win.alive():
                win.close()
        except Exception:
            pass
        self.app._findings_win = None
        D.TestRunInApp.tearDown(self)
        self.app.mode_key = "variables"
        self.app.busy = False

    def _said(self, part):
        return [t for t in self.logged if part in t]

    # ---- F2-d ----
    def test_f2d_step_strip_follows_the_run_gate(self):
        app = self.app
        app.mode_key = "variables"
        app.preview, app.preview_is_debayered = np.zeros((40, 40)), True
        app.chart_placed = [(1, 1, {})]
        app.target_xy, app.comp_xy, app.check_xy = (10, 10), (20, 20), (30, 30)
        app.masters, app.masters_binning = core.Masters(), 1
        app.binning.set(1)
        app.radius.set(4.0)
        app.sky_in.set(7.0)
        app.sky_out.set(10.0)
        try:
            self.assertEqual(app._run_why(), "")
            self.assertTrue(app.photo_steps()[3].startswith("Ready: Run photometry."))
            app.binning.set(2)
            why = app._run_why()
            self.assertIn("The masters were built at bin 1", why)
            names, done, current, text = app.photo_steps()
            self.assertEqual((current, text), (4, "Not ready: " + why))
            app.binning.set(1)
            app.sky_in.set(3.0)   # out of order: the red box's reason
            self.assertTrue(app.photo_steps()[3].startswith("Not ready: The aperture sizes must be in order"))
            app.sky_in.set(7.0)
            self.assertTrue(app.photo_steps()[3].startswith("Ready: Run photometry."))
            # the strip is redrawn by the two things that change the gate
            src = _src("clearv_app.py")
            self.assertIn("self._update_step_hint()", _body(src, "    def _mark_aperture_boxes(self"))
            self.assertEqual(_body(src, "    def _binning_changed(self").count("self._update_step_hint()"), 2)
            # Discovery: the Scan field gate
            app.mode_key = "discovery"
            app.binning.set(2)
            self.assertTrue(app.photo_steps()[3].startswith("Not ready: The masters were built at bin 1"))
            app.binning.set(1)
            self.assertTrue(app.photo_steps()[3].startswith("Solved."))
        finally:
            app.mode_key = "variables"
            app.binning.set(1)
            app.preview, app.preview_is_debayered, app.chart_placed = None, False, []
            app.target_xy = app.comp_xy = app.check_xy = None
            app.masters, app.masters_binning = None, None

    # ---- area E and K14 (slot half) ----
    def test_slot_one_verdict_and_plain_ghost_hint(self):
        app = self.app
        app.mode_key = "variables"
        app.preview, app.preview_is_debayered = np.zeros((200, 200)), True
        try:
            app.comp_name.set("HD 1")
            app.comp_xy = None
            app.update_star_panel()
            self.assertEqual(app.star_status["comp"].get(),
                             "no marker on this image: click the star, or ✕ to clear the name")
            self.assertEqual(str(app.star_status_labels["comp"].cget("style")), "Hint.TLabel")
            # three ⚠ notes from the helpers become one ⚠, worst first
            app.comp_xy = (100.0, 100.0)
            lin = ("⚠ raw peak 61,000 ADU is over the linear limit (60,000): the camera's response bends there, so "
                   "this star reads too faint — pick a fainter comp.")
            edge = "⚠ only 12 px from the frame edge: its sky ring (out to 22 px) needs the star at least 27 px in"
            with mock.patch.object(app, "_linearity_note", return_value=lin), \
                    mock.patch.object(app, "_edge_note", return_value=edge), \
                    mock.patch.object(app, "_no_catalog_v_note", return_value="not in the downloaded catalogs; type "
                                                                              "its V or pick another star"), \
                    mock.patch.object(app, "_comp_brightness_note", return_value="✓ +0.2 mag from the target"):
                app.update_star_panel()
            text = app.star_status["comp"].get()
            self.assertEqual(text.count("⚠"), 1, text)
            self.assertEqual(text.count("✓"), 0, text)
            self.assertTrue(text.startswith(lin + "   also: only 12 px from the frame edge; not in the downloaded "
                                                  "catalogs"), text)
            self.assertIn("+0.2 mag from the target", text)
            self.assertEqual(str(app.star_status_labels["comp"].cget("style")), "Warn.TLabel")
            # a known variable leads, in red, whatever else is wrong
            with mock.patch.object(app, "_linearity_note", return_value=lin), \
                    mock.patch.object(app, "_variables_at", return_value=[{"name": "V1 Cas", "type": "EW",
                                                                           "source": "VSX", "desc": "eclipsing"}]):
                app.update_star_panel()
            text = app.star_status["comp"].get()
            self.assertEqual(text.count("⚠"), 1, text)
            self.assertIn("also: raw peak 61,000 ADU is over the linear limit (60,000)", text)
            self.assertLess(text.index("V1 Cas"), text.index("also:"))
            self.assertEqual(str(app.star_status_labels["comp"].cget("style")), "Bad.TLabel")
            # every test passed: one green line
            star = {"mags": {"V": 10.0, "B": 10.7}, "mag": 10.0, "band": "V", "catalog": "APASS DR9",
                    "mag_source": {"V": "APASS DR9"}, "sources": {}}
            with mock.patch.object(app, "_chart_star_named", return_value=star), \
                    mock.patch.object(app, "_variables_at", return_value=[]), \
                    mock.patch.object(app, "field_check_sources", ["VSX", "SIMBAD"]), \
                    mock.patch.object(app, "_linearity_note", return_value=""), \
                    mock.patch.object(app, "_edge_note", return_value=""), \
                    mock.patch.object(app, "_no_catalog_v_note", return_value=""), \
                    mock.patch.object(ca.core, "catalog_color", return_value=(0.70, "APASS DR9")), \
                    mock.patch.object(app, "_comp_brightness_note", return_value="✓ +0.2 mag from the target"):
                app.update_star_panel()
            text = app.star_status["comp"].get()
            self.assertTrue(text.startswith("✓ good comp: B−V 0.70 (APASS DR9), +0.2 mag from the target, not a known "
                                            "or suspected variable ("), text)
            self.assertTrue(text.endswith("V from APASS DR9"), text)
            self.assertEqual(text.count("✓"), 1)
            self.assertEqual(str(app.star_status_labels["comp"].cget("style")), "Good.TLabel")
        finally:
            app.comp_name.set("")
            app.comp_xy = None
            app.preview, app.preview_is_debayered = None, False
            app.update_star_panel()

    # ---- K14 (Run half) ----
    def test_m275_one_question_two_answers(self):
        app = self.app
        app.mode_key = "variables"
        app.preview, app.preview_is_debayered = np.zeros((60, 60)), True
        asked = []

        def ask(answer):
            def fn(parent, title, message, choices, safe, cancel=None):
                asked.append((message, [t for _v, t in choices], safe))
                return answer
            return fn
        try:
            for role in ("target", "check") + tuple(ca.COMP_ROLES):   # markers left by an earlier test's run
                setattr(app, f"{role}_xy", None)
            for role in ca.COMP_ROLES:
                getattr(app, f"{role}_name").set("")
            app.check_name.set("")
            app.comp_name.set("OLD 1")
            with mock.patch.object(ca, "ask_choice", ask("stop")), \
                    mock.patch.object(ca.messagebox, "askyesnocancel", side_effect=AssertionError("old question")):
                self.assertFalse(app._markers_in_order())
            self.assertEqual(app.comp_name.get(), "OLD 1")
            self.assertTrue(asked[0][0].startswith("These have a name but no marker on this image, so they would not "
                                                   "be measured:"), asked[0][0])
            self.assertIn("OLD 1", asked[0][0])
            self.assertEqual(asked[0][1:], (["Run without them (names cleared)", "Stop to mark them"], "stop"))
            with mock.patch.object(ca, "ask_choice", ask("run")):
                self.assertTrue(app._markers_in_order())
            self.assertEqual(app.comp_name.get(), "")
            self.assertTrue(self._said("Run without (names cleared, no marker on this image): Comp 1 OLD 1."))
        finally:
            app.comp_name.set("")
            app.preview, app.preview_is_debayered = None, False

    # ---- K04 ----
    def test_m220_no_transit_is_one_question(self):
        app = self.app
        planet = {"name": "HD 189733 b"}
        page = types.SimpleNamespace(input_planet=lambda: planet)
        asked = []

        def ask(answer):
            def fn(parent, title, message, choices, safe, cancel=None):
                asked.append((message, [t for _v, t in choices], safe))
                return answer
            return fn
        common = [mock.patch.object(app, "transit_page", page, create=True),
                  mock.patch.object(ca.core, "read_header", return_value={}),
                  mock.patch.object(ca.core, "mid_jd", return_value=2460880.7),
                  mock.patch.object(ca.tcore, "transit_window_check", return_value={"overlap": False, "hours": -3.25}),
                  mock.patch.object(ca.messagebox, "askyesnocancel", side_effect=AssertionError("old question"))]
        for p in common:
            p.start()
        try:
            app.mode_key = "transits"
            with mock.patch.object(ca, "ask_choice", ask("cancel")):
                self.assertFalse(app._transit_in_lights(["a.fits", "b.fits"]))
            self.boxes["askyesno"].assert_not_called()
            message, buttons, safe = asked[0]
            self.assertTrue(message.startswith("No transit of HD 189733 b is predicted during these lights (the "
                                               "nearest is 3.2 h before them). Check the planet and the frames' "
                                               "date."), message)
            self.assertEqual((buttons, safe), (["Switch to Variables mode", "Run in Transits mode anyway", "Cancel"],
                                               "cancel"))
            with mock.patch.object(ca, "ask_choice", ask("run")):
                self.assertTrue(app._transit_in_lights(["a.fits", "b.fits"]))
            self.assertTrue(self._said("Run in Transits mode as chosen (no transit of HD 189733 b"))
            with mock.patch.object(ca, "ask_choice", ask("variables")), \
                    mock.patch.object(app, "_rerun_in_mode", return_value=False) as rerun:
                self.assertFalse(app._transit_in_lights(["a.fits", "b.fits"]))
            rerun.assert_called_once_with("variables")
            # the guard for a known variable asks the same question, with its reason added
            asked.clear()
            with mock.patch.object(ca, "ask_choice", ask("cancel")), \
                    mock.patch.object(app, "_target_variable_reasons", return_value=["the target is a known variable"]), \
                    mock.patch.object(app, "_lights_span", return_value=(2460880.6, 2460880.8)):
                self.assertFalse(app._mode_guard(["a.fits", "b.fits"]))
            self.assertEqual(len(asked), 1)
            self.assertTrue(asked[0][0].startswith("No transit of HD 189733 b is predicted during these lights (the "
                                                   "nearest is 3.2 h before them), and the target is a known "
                                                   "variable. Check the planet"), asked[0][0])
            self.assertEqual(asked[0][1], buttons)
        finally:
            for p in common:
                p.stop()
            app.mode_key = "variables"

    # ---- K08 ----
    def test_m202_target_off_frames_one_sentence(self):
        app = self.app
        app.star_id.set("SZ Lyn")
        try:
            app._target_off_words = "the target is 4′ beyond the north edge"
            self.assertEqual(app._target_off_frames_text("SZ Lyn"),
                             "⚠ The Input page target (SZ Lyn) is not on these frames (the target is 4′ beyond the "
                             "north edge): check the Star ID or RA/Dec on the Input page.")
            app._target_off_words = ""
            self.assertEqual(app._target_off_frames_text(extra="the chart is placed from the plate solution alone"),
                             "⚠ The Input page target (SZ Lyn) is not on these frames; the chart is placed from the "
                             "plate solution alone: check the Star ID or RA/Dec on the Input page.")
            src = _src("clearv_app.py")
            self.assertEqual(src.count("self._target_off_frames_text("), 2)
        finally:
            app.star_id.set("")

    # ---- K16 ----
    def test_m230_aperture_note_is_plain(self):
        app = self.app
        img = np.zeros((40, 40))
        app.preview, app.preview_is_debayered = img, True
        app._preview_token = getattr(app, "_preview_token", 0) + 1
        app._seeing_cache = ((id(img), img.shape), 2.3)
        app._aperture_caution_said = None
        app.radius.set(8.0)
        before = len(app.findings.items)
        try:
            with mock.patch.object(app, "_donut_apertures", return_value=None):
                app._aperture_caution(app._preview_token, measured=True)
            said = self._said("× the stars' FWHM")
            self.assertEqual(len(said), 1)
            self.assertTrue(said[0].startswith("Aperture r = 8 px (kept from before) is 3.5 × the stars' FWHM"), said)
            self.assertEqual(len(app.findings.items), before, "a plain note is not a finding")
            app._aperture_caution_said = None
            with mock.patch.object(app, "_donut_apertures", return_value={"clip_below": 12.0, "radius": 14.0,
                                                                          "sky_in": 20.0, "sky_out": 28.0}):
                app._aperture_caution(app._preview_token, measured=True)
            said = self._said("clips the ring")
            self.assertEqual(said, ["Defocused stars: r = 8 px (kept from before) clips the ring; Suggest… gives r = "
                                    "14, sky 20–28 px."])
            self.assertEqual(len(app.findings.items), before)
        finally:
            app.preview, app.preview_is_debayered = None, False
            app._aperture_caution_said = None

    # ---- K17 ----
    def test_m285_watch_not_found_one_plain_line_no_box(self):
        app = self.app
        with mock.patch.object(ca.core, "lookup_target", side_effect=ValueError("no object of that name.")), \
                mock.patch.object(ca.core, "parse_coordinates", return_value=None):
            app.add_watch_lookup("Zzyzx 9")
            end = time.time() + 10
            while time.time() < end and not self._said("not found at SIMBAD or VSX"):
                app.update()
                time.sleep(0.02)
        self.assertEqual(self._said("not found at SIMBAD or VSX"),
                         ["Zzyzx 9 not found at SIMBAD or VSX (no object of that name): check the spelling, or click "
                          "the star with Watch."])
        self.boxes["showerror"].assert_not_called()
        self.assertEqual(app.status.get(), self._said("not found at SIMBAD or VSX")[0])
        self.assertNotIn("✖", app.status.get())

    # ---- K07 ----
    def test_m477_answer_and_outcome_are_one_log_line(self):
        app = self.app
        night = "JD2460950"
        with mock.patch.object(ca, "ask_choice", return_value=False):
            self.assertFalse(app._ask_keep(night, "question", "after photometry"))
        self.assertEqual(self._said("answered"), [])
        self.assertEqual(self.logged, [])
        app._set_keep_transmission(night, False, "after photometry")
        app._set_keep_transmission(night, True, "Transmission loss…")
        try:
            self.assertEqual(len(self.logged), 2)
            self.assertTrue(self.logged[0].endswith("points left out, flagged (asked after photometry)."), self.logged)
            self.assertTrue(self.logged[1].endswith("points kept (asked at Transmission loss…): errors follow the "
                                                    "reduced light plus the star-to-star scatter there."), self.logged)
            self.assertTrue(app._night_info(night).get("keep_transmission"))
        finally:
            app.night_bin.pop(night, None)

    # ---- K13 and the series-not-saved line in the same hunk ----
    def test_m558_and_series_not_saved_in_use_comps(self):
        app = self.app
        result = {"done": 10, "missing": 0}
        common = [mock.patch.object(app, n) for n in ("_comps_follow_in_use", "_refresh_period", "draw_output",
                                                      "_update_skip_line", "_log_zero_point_check")]
        common += [mock.patch.object(app, "_save_series_words", side_effect=OSError("disk full"))]
        for p in common:
            p.start()
        app.series_path = os.path.join(self.dir, "s.cvseries")
        try:
            # until package C's helper is merged: the old box (this branch runs on its own)
            if not hasattr(ca.App, "series_not_saved"):
                app._comp_selection_applied(["C1"], dict(result))
                self.boxes["showwarning"].assert_called_once()
                self.assertIn("disk full", self.boxes["showwarning"].call_args[0][1])
            # with the helper: it is the one message
            self.boxes["showwarning"].reset_mock()
            helper = mock.Mock(return_value="  Series not saved.")
            with mock.patch.object(ca.App, "series_not_saved", helper, create=True):
                app._comp_selection_applied(["C1"], dict(result))
            helper.assert_called_once()
            self.assertIsInstance(helper.call_args[0][-1], OSError)
            self.boxes["showwarning"].assert_not_called()
            self.assertTrue(app.status.get().startswith("Comps in use: C1.  Series not saved."), app.status.get())
        finally:
            for p in common:
                p.stop()
            app.series_path = ""
            app.active_comps = []

    # ---- area F ----
    def test_comp_skip_text_one_line_per_star(self):
        app = self.app
        info = {"comp_drops": {"C3": {"frames": ["27", "28", "29"], "reasons": {"off the frame edge": 3}},
                               "C4": {"frames": ["5"], "reasons": {"no flux above sky": 1}},
                               "check": {"frames": ["30", "31"], "reasons": {"off the frame edge": 2}}},
                "night_out": []}
        edges = {"C3": {"words": "reaches the frame edge (stars at least 39 px in are safe)", "flat_pct": None},
                 "C4": {"words": "sits where the flat is only 78% of the center (heavy vignetting)", "flat_pct": 78.0},
                 "check": {"words": "", "flat_pct": None, "how": "Pick a check star at least 39 px in next time."}}
        with mock.patch.object(app, "_night_info", return_value=info), \
                mock.patch.object(app, "recoverable_skips", return_value={}):
            page = app.comp_skip_text("N1").splitlines()
            summary = app.comp_skip_text("N1", edges=edges).splitlines()
        self.assertEqual(page[0], "⚠ C3 left out of 3 frames (off the frame edge; frames 27–29); those frames use the "
                                  "other comps. Leave it out with Use comps… (Output page).")
        self.assertEqual(page[2], "⚠ check star left out of 2 frames (off the frame edge; frames 30–31); no check "
                                  "value there.")
        self.assertEqual(summary[0], page[0])   # "off the frame edge" says it already
        self.assertEqual(summary[1], "⚠ C4 left out of 1 frame (no flux above sky; frames 5; it sits where the flat "
                                     "is only 78% of the center (heavy vignetting)); those frames use the other "
                                     "comps. Leave it out with Use comps… (Output page).")
        self.assertEqual(summary[2], page[2] + " Pick a check star at least 39 px in next time.")
        self.assertEqual(len(summary), 3)
        # a comp out of the whole night needs no "leave it out": it is out
        info["night_out"] = ["C3"]
        with mock.patch.object(app, "_night_info", return_value=info), \
                mock.patch.object(app, "recoverable_skips", return_value={}):
            line = app.comp_skip_text("N1").splitlines()[0]
        self.assertTrue(line.endswith("; out of the whole night (missing in most frames)."), line)

    def test_ten_percent_rule_for_a_star_left_out(self):
        """John, 26 Nov: a comp in use or the check left out of frames is a ⚠ finding only past 10 % of the night's
        frames (the skipped-frames rule, 2.3.6); at 10 % or less the same line is a plain note."""
        app = self.app

        def lines(n_c3, n_k, measured=100, skipped=0):
            info = {"comp_drops": {"C3": {"frames": [str(k) for k in range(1, n_c3 + 1)],
                                          "reasons": {"off the frame edge": n_c3}},
                                   "check": {"frames": [str(k) for k in range(1, n_k + 1)],
                                             "reasons": {"off the frame edge": n_k}}},
                    "skipped": [{"frame": f"s{k}", "path": f"/s{k}.fits", "why": "could not align"}
                                for k in range(skipped)]}
            edges = {"check": {"words": "", "flat_pct": None, "how": "Pick a check star at least 39 px in next time."}}
            with mock.patch.object(app, "_night_info", return_value=info), \
                    mock.patch.object(app, "_night_frame_count", return_value=measured), \
                    mock.patch.object(app, "recoverable_skips", return_value={}):
                got = app.comp_skip_text("N1", edges=edges).splitlines()
            return [ln for ln in got if "left out of" in ln]
        # under the threshold: plain notes, nothing to do
        c3, k = lines(9, 3)
        self.assertEqual(c3, "C3 left out of 9 frames (off the frame edge; frames 1–9); those frames use the other "
                             "comps.")
        self.assertEqual(k, "check star left out of 3 frames (off the frame edge; frames 1–3); no check value there.")
        # exactly 10 % is still a plain note (the rule is "more than 10 %")
        c3, k = lines(10, 10)
        self.assertFalse(c3.startswith("⚠"), c3)
        self.assertFalse(k.startswith("⚠"), k)
        self.assertNotIn("Use comps", c3)
        self.assertNotIn("Pick a check star", k)
        # past it: ⚠, with what to do
        c3, k = lines(11, 11)
        self.assertEqual(c3, "⚠ C3 left out of 11 frames (off the frame edge; frames 1–11); those frames use the "
                             "other comps. Leave it out with Use comps… (Output page).")
        self.assertEqual(k, "⚠ check star left out of 11 frames (off the frame edge; frames 1–11); no check value "
                            "there. Pick a check star at least 39 px in next time.")
        # the night's frames = measured + skipped, as for the skipped-frames line: 10 of (90 + 10) is exactly 10 %
        self.assertFalse(lines(10, 1, measured=90, skipped=10)[0].startswith("⚠"))
        self.assertTrue(lines(11, 1, measured=90, skipped=10)[0].startswith("⚠"))
        # a comp out of the whole night is always a ⚠
        info = {"comp_drops": {"C3": {"frames": ["1"], "reasons": {"off the frame edge": 1}}}, "night_out": ["C3"]}
        with mock.patch.object(app, "_night_info", return_value=info), \
                mock.patch.object(app, "_night_frame_count", return_value=100), \
                mock.patch.object(app, "recoverable_skips", return_value={}):
            self.assertTrue(app.comp_skip_text("N1").startswith("⚠ C3 left out of 1 frame"))
        # the end-of-run summary: a plain line is logged as a note, is no finding and opens nothing
        info = {"comp_drops": {"C3": {"frames": ["4", "5"], "reasons": {"off the frame edge": 2}}}}
        app.findings.clear()
        opens = []
        with mock.patch.object(app, "_night_info", return_value=info), \
                mock.patch.object(app, "_night_frame_count", return_value=100), \
                mock.patch.object(app, "recoverable_skips", return_value={}), \
                mock.patch.object(app, "_run_edge_notes", return_value=({}, [])), \
                mock.patch.object(app, "_open_findings_for_run", side_effect=lambda: opens.append(1)):
            self.assertEqual(app._run_summary("N1", []), [])
            self.assertEqual(opens, [])
            self.assertEqual(len(app.findings), 0)
            self.assertEqual(self._said("C3 left out of 2 frames"),
                             ["N1: C3 left out of 2 frames (off the frame edge; frames 4–5); those frames use the "
                              "other comps."])
            self.assertEqual(app.status.get(), self._said("C3 left out of 2 frames")[0])
            info["comp_drops"]["C3"] = {"frames": [str(k) for k in range(1, 31)], "reasons": {"off the frame edge": 30}}
            said = app._run_summary("N1", [])
            self.assertEqual(len(said), 1)
            self.assertEqual(opens, [1])
            self.assertEqual(len(app.findings), 1)

    def test_run_with_a_star_off_the_edge_no_box_findings_opens(self):
        # C3, the check K and a spare start near the top edge and drift off it (1 px/frame)
        stars = [("T", 100, 110, 50000), ("C1", 50, 100, 60000), ("C2", 150, 130, 40000), ("C3", 75, 30, 90000),
                 ("K", 140, 26, 45000), ("FAR", 100, 80, 45000)]
        paths = D.write_night(os.path.join(self.dir, "lights"), stars)
        self._setup(paths, ("T", 100, 110), [("C1", 50, 100, 10.0), ("C2", 150, 130, 10.4), ("C3", 75, 30, 9.6)],
                    check=("K", 140, 26, 10.2), spares=[("FAR", 100, 80, 10.2)])
        app = self.app
        self._run()
        self.assertTrue(app.observations)
        night = app.observations[0].night
        # no box after the run
        after_run = [c[0][1] for c in self.boxes["showwarning"].call_args_list]
        self.assertEqual([t for t in after_run if "left out" in t or "frame edge" in t], [], after_run)
        # one ⚠ line per problem star, with what to do; none says "Consider"
        c3 = self._said("C3 left out of")
        self.assertEqual(len(c3), 1, c3)
        # 2.4.0 fit-in F4 (#79 item 4): a comp off the frame in any frame is out for the whole night: a plain note
        self.assertTrue(c3[0].startswith(f"{night}: C3 left out of the whole night: missing from"), c3)
        k = self._said("check star left out of")
        self.assertEqual(len(k), 1, k)
        self.assertIn("no check value there. A better check star stays farther in: FAR", k[0])
        self.assertTrue(k[0].endswith("Pick it as the check next time."), k)
        self.assertEqual(self._said("Consider leaving"), [])
        self.assertEqual([t for t in self.logged if "comes within" in t and "⚠" in t], [])
        # they are findings (the job summary, the settings file and the report NOTES follow, 2.3.6)
        texts = [f.text for f in app.findings.items]
        self.assertFalse(any("C3 left out of" in t for t in texts), texts)   # F4: a plain note is no finding
        self.assertTrue(any("check star left out of" in t for t in texts), texts)
        self.assertTrue(any("check star left out of" in t for t in app.run_findings), app.run_findings)
        self.assertIn("finding", app.run_findings_note())
        # and the Findings panel opened by itself
        win = getattr(app, "_findings_win", None)
        self.assertTrue(win is not None and win.alive())
        # the Photometry page's line is the same sentence
        self.assertIn(c3[0].split(": ", 1)[1], app.comp_skip_text(night))

    def test_clean_run_opens_nothing(self):
        stars = [("T", 100, 110, 50000), ("C1", 50, 100, 60000), ("C2", 150, 130, 40000), ("K", 140, 90, 45000)]
        paths = D.write_night(os.path.join(self.dir, "lights"), stars, drift=(0.0, 0.0))
        self._setup(paths, ("T", 100, 110), [("C1", 50, 100, 10.0), ("C2", 150, 130, 10.4)], check=("K", 140, 90, 10.2))
        app = self.app
        self._run()
        self.assertTrue(app.observations)
        self.assertEqual(self._said("left out of"), [])
        win = getattr(app, "_findings_win", None)
        self.assertFalse(win is not None and win.alive())

    # ---- area K ----
    def test_scan_limits_collected_once_and_said_with_the_result(self):
        app = self.app
        limits = []
        app._scan_no_crowding(limits, ValueError("boom"))
        app._scan_no_crowding(limits, ValueError("boom again"))   # the second step failing adds nothing
        self.assertEqual(limits, ["crowding not tested: check suspects on the image"])
        self.assertEqual(len(self._said("crowding test skipped")), 1)
        src = _src("clearv_app.py")
        scan = _body(src, "    def scan_field(self")
        self.assertIn('"limits": list(limits)', scan)
        self.assertIn("brightest {cap} measured (limit reached", scan)
        self.assertEqual(scan.count("self._scan_no_crowding(limits, exc)"), 2)
        self.assertIn("keep_status=True", scan.split("-star limit was reached", 1)[1].split("\n", 2)[1])
        # _scan_done: the Log line and the status line carry them
        result = {"candidates": [], "labels": ["a"] * 9, "frames": 12, "vsx": None,
                  "limits": ["brightest 800 measured (limit reached; raise Most stars to measure to go fainter)",
                             "crowding not tested: check suspects on the image"]}
        app.mode_key = "variables"
        with mock.patch.object(app, "_end_job"), mock.patch.object(app, "_progress_end"), \
                mock.patch.object(app, "_stack_scan_done"), mock.patch.object(ca, "ScanWindow"):
            app._scan_done(result, None)
        done = self._said("Field scan done:")
        self.assertEqual(len(done), 1)
        self.assertTrue(done[0].endswith(". Limits: brightest 800 measured (limit reached; raise Most stars to "
                                         "measure to go fainter); crowding not tested: check suspects on the "
                                         "image."), done)
        self.assertIn("Limits: brightest 800 measured", app.status.get())
        result["limits"] = []
        self.logged.clear()
        with mock.patch.object(app, "_end_job"), mock.patch.object(app, "_progress_end"), \
                mock.patch.object(app, "_stack_scan_done"), mock.patch.object(ca, "ScanWindow"):
            app._scan_done(result, None)
        self.assertNotIn("Limits", self._said("Field scan done:")[0])
        self.assertEqual(app.status.get(), "Field scan: 0 suspect(s).")

    # ---- F2-e ----
    def test_f2e_one_back_on_the_field_scan_page(self):
        app = self.app

        def backs(widget):
            n = 0
            for child in widget.winfo_children():
                try:
                    if child.winfo_class() == "TButton" and str(child.cget("text")) == "Back":
                        n += 1
                except Exception:
                    pass
                n += backs(child)
            return n
        app._discovery_placeholder()
        app.update()
        card = app.discovery_host.master
        self.assertEqual(backs(card), 1)


class TestManual(unittest.TestCase):
    def test_chapters_say_the_new_words(self):
        def read(name):
            return _src(os.path.join("docs", "manual", name))
        ch23 = read("23-messages-explained.md")
        for words in ("Leave it out with Use comps… (Output page).", "Stop to mark them",
                      "Switch to Variables mode", "Not ready: The masters were built at bin 1",
                      "is not on these frames", "Limits: brightest 800 measured", "crowding not tested",
                      "✓ good comp:", "also:", "not found at SIMBAD or VSX",
                      "The Target marker was on a neighboring star", "is not one SHOBS-P knows"):
            self.assertIn(words, ch23, words)
        for gone in ("in a box after the run", "in the same box after the run", "**Yes** clears the names",
                     "![A warning box after photometry]"):
            self.assertNotIn(gone, ch23, gone)
        ch08 = read("08-photometry-basics.md")
        self.assertIn("No box comes up after the run", ch08)
        self.assertNotIn("A warning box after the run lists the same lines", ch08)
        self.assertIn("Not ready:", ch08)
        self.assertIn("✓ good comp:", ch08)
        self.assertIn("(astropy) is missing", read("24-troubleshooting.md"))
        self.assertIn("## Package B", read("_reshoots_240_s.md"))
        self.assertIn("Limits: brightest 800 measured", read("20-scan-a-field.md"))


if __name__ == "__main__":
    unittest.main()
