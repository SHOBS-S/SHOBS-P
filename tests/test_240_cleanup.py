"""2.4.0 cleanup package (after the F4 regression, 27 Nov): #118 fits in a helper process and the watch's short
pauses, #119 the one edge rule for comps, the re-center save-hold (step 8 N6), short pointing moves said (E), the
field scan header (F), Known variables one row per star (G), the flip-step words (H), Watch stars after Load series
(I), the series-format line (J), Star ID lookups only on a real leave (K), the old target's findings (K+), a clicked
comp in the Log (D), the Fit transit note while it runs (B) and the m509 lookup off the window thread."""
import inspect
import os
import sys
import threading
import time
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photometry_core as core    # noqa: E402
import transit_core as tc         # noqa: E402
import fit_process                # noqa: E402
import ui_watch                   # noqa: E402
import clearv_app                 # noqa: E402
import findings                   # noqa: E402

App = clearv_app.App
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(name="clearv_app.py"):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def _night(n=200, seed=1):
    rng = np.random.default_rng(seed)
    P = 2.15
    t = np.linspace(-0.12, 0.12, n)
    f = tc.occultation(tc.separation(t, 0.0, P, 6.0, 0.1), 0.15, 0.4, 0.25) * (1 + rng.normal(0, 2e-3, n))
    planet = {"name": "Test b", "period": P, "t0": 2460000.0, "a_rs": 6.0, "b": 0.1, "k": 0.15}
    return t + 2460000.0, f, np.full(n, 2e-3), 1 + 0.3 * (t + 0.12), planet


# ---- #118: the fit helper process -----------------------------------------------------------------------------

class TestFitProcess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.was = fit_process.ENABLED
        fit_process.ENABLED = True
        fit_process._broken_why = ""
        fit_process._closed = False

    @classmethod
    def tearDownClass(cls):
        fit_process.shutdown()
        fit_process.ENABLED = cls.was
        fit_process._closed = False

    def test_same_numbers_as_in_the_window_process(self):
        t, f, e, am, planet = _night()
        kw = dict(mcmc_steps=200, after_flip=(t - 2460000.0) > 0.03, detrend_auto=True)
        here = tc.fit_transit(t, f, e, am, planet, **kw)
        seen, said = [], []
        there = fit_process.call(tc.fit_transit, t, f, e, am, planet, progress=lambda *a: seen.append(a),
                                 log=said.append, **kw)
        self.assertEqual(here["summary"], there["summary"])
        self.assertTrue(np.array_equal(here["model"], there["model"]))
        self.assertEqual(repr(here["flags"]), repr(there["flags"]))
        self.assertTrue(seen)                                  # the progress came back to this thread
        self.assertTrue(any("detrend" in str(a[0]) for a in seen))
        self.assertTrue(fit_process._all)                      # it ran in a helper, kept for the next fit

    def test_cancel_stops_the_helper_fit(self):
        t, f, e, am, planet = _night()
        ev = threading.Event()
        threading.Timer(0.8, ev.set).start()
        t0 = time.monotonic()
        with self.assertRaises(RuntimeError) as cm:
            fit_process.call(tc.fit_transit, t, f, e, am, planet, cancel=ev, mcmc_steps=50000)
        self.assertEqual(str(cm.exception), "Canceled")
        self.assertLess(time.monotonic() - t0, 15.0)

    def test_an_error_comes_back_as_itself(self):
        t, f, e, am, planet = _night()
        with self.assertRaises(ValueError) as cm:
            fit_process.call(tc.fit_transit, t[:2], f[:2], e[:2], None, planet)
        self.assertIn("a transit fit needs at least", str(cm.exception))
        self.assertTrue(any("helper process" in n for n in getattr(cm.exception, "__notes__", [])))

    def test_a_stand_in_runs_on_the_calling_thread(self):
        stand_in = mock.Mock(return_value={"ok": 1})
        stand_in.__module__, stand_in.__name__ = "transit_core", "fit_transit"
        self.assertEqual(fit_process.call(stand_in, 1, 2, progress=None), {"ok": 1})
        stand_in.assert_called_once()

    def test_off_means_the_calling_thread(self):
        fit_process.ENABLED = False
        try:
            with mock.patch.object(fit_process, "_take", side_effect=AssertionError("no helper")):
                out = fit_process.call(tc.total_duration, 2.15, 6.0, 0.1, 0.15)
            self.assertAlmostEqual(out, tc.total_duration(2.15, 6.0, 0.1, 0.15))
        finally:
            fit_process.ENABLED = True

    def test_a_helper_that_cannot_start_falls_back(self):
        said = []
        fit_process.set_logger(said.append)
        try:
            with mock.patch.object(fit_process, "_take", side_effect=OSError("no python")):
                out = fit_process.call(tc.total_duration, 2.15, 6.0, 0.1, 0.15)
            self.assertAlmostEqual(out, tc.total_duration(2.15, 6.0, 0.1, 0.15))
            self.assertTrue(any("could not start" in s for s in said), said)
        finally:
            fit_process._broken_why = ""
            fit_process.set_logger(None)

    def test_every_long_fit_goes_through_it(self):
        ui = _src("transit_ui.py")
        for name in ("tc.fit_transit", "tc.leave_one_comp_out", "tc.robustness_check"):
            self.assertIn(f"fit_process.call({name}" if name != "tc.robustness_check" else
                          "tc.robustness_check,", ui)
        mn = _src("multinight.py")
        self.assertIn("fit_process.call(joint_fit", mn)
        self.assertIn("fit_process.call(quick_single_tc", mn)
        app = _src()
        self.assertIn("fit_process.ENABLED = True", app)    # main() only: tests and the self-test stay in-thread
        self.assertIn("fit_process.shutdown()", app)


class TestWatchSeesShortPauses(unittest.TestCase):
    def test_pauses_of_one_to_three_seconds_are_summed_up(self):
        now = [100.0]
        said = []
        w = ui_watch.UIWatch(said.append, main_thread_id=-1, clock=lambda: now[0])
        for gap in (1.6, 0.05, 2.4, 0.05):
            now[0] += gap
            w.beat()
        self.assertEqual(len(w._slow), 2)
        now[0] += ui_watch.SLOW_EVERY_S
        w.last = now[0]
        w.check()
        line = [s for s in said if s.startswith("UI thread slow")]
        self.assertTrue(line, said)
        self.assertIn("2 pauses of 1-3 s", line[0])
        self.assertIn("longest 2.4 s", line[0])
        self.assertEqual(w._slow, [])

    def test_a_reported_stall_is_not_counted_twice(self):
        now = [0.0]
        w = ui_watch.UIWatch(lambda s: None, main_thread_id=-1, clock=lambda: now[0])
        now[0] += 4.0
        w.beat()            # longer than the stall line's 3 s: not a "slow" pause
        self.assertEqual(w._slow, [])


# ---- #119: one edge rule for comps ----------------------------------------------------------------------------

class TestOneEdgeRule(unittest.TestCase):
    def test_the_one_definition(self):
        shape = (200, 300)
        self.assertTrue(core.sky_ring_past_edge(shape, 42.0, 100.0, 43.0))    # card 1: comp 4 at x = 42, ring 43
        self.assertFalse(core.sky_ring_past_edge(shape, 43.0, 100.0, 43.0))
        self.assertTrue(core.sky_ring_past_edge(shape, float("nan"), 100.0, 10.0))
        pos = np.array([[42.0, 100.0], [43.0, 100.0], [150.0, 190.0]])
        self.assertEqual(core.scan_near_edge(pos, shape, 43.0).tolist(),
                         [core.sky_ring_past_edge(shape, x, y, 43.0) for x, y in pos])

    def _frame(self, comp_x):
        img = np.full((200, 300), 100.0)
        yy, xx = np.mgrid[0:200, 0:300]
        for (x, y, a) in ((150.0, 100.0, 5000.0), (comp_x, 100.0, 5000.0), (220.0, 60.0, 5000.0)):
            img += a * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.5 ** 2))
        return img

    def test_a_comp_with_its_ring_past_the_edge_is_not_measured_in_that_frame(self):
        img = self._frame(16.0)
        hdr = {"EXPTIME": 60.0, "DATE-OBS": "2026-11-13T23:42:41"}
        target = core.Star("T", 150.0, 100.0)
        comps = [core.Star("EDGE", 16.0, 100.0, 11.0), core.Star("IN", 220.0, 60.0, 11.0)]
        o = core.reduce_frame(img, hdr, target, comps, None, 4.0, 8.0, 18.0)
        self.assertEqual(o.comp_drop.get("EDGE"), core.RING_PAST_EDGE)   # aperture on, ring partly off
        self.assertNotIn("IN", o.comp_drop)

    def test_out_for_the_whole_night_in_plain_words(self):
        pts = []
        for k in range(6):
            o = _mk("n1", 2460000.0 + k / 100.0, 12.0)
            o.target_inst = -8.0
            o._ci = {"EDGE": -7.0 if k < 4 else float("nan"), "IN": -7.5}
            o.comp_drop = {"EDGE": core.RING_PAST_EDGE} if k >= 4 else {}
            pts.append(o)
        with mock.patch.object(core, "star_inst", lambda o, name, names, aliases=None: o._ci.get(name, float("nan"))):
            ne = core.night_ensemble(pts, ["EDGE", "IN"], ["EDGE", "IN"])
        self.assertEqual(ne["out"], ["EDGE"])
        self.assertEqual(ne["why"]["EDGE"], "its sky ring runs past the frame edge in 2 of 6 frames")
        self.assertEqual(ne["use"], ["IN"])

    def test_spares_follow_it_but_not_the_target(self):
        img = self._frame(16.0)
        stars = [core.Star("T", 16.0, 100.0), core.Star("S", 16.0, 100.0)]
        m = core.channel_inst_mags(img, stars, 4.0, 8.0, 18.0, 60.0, ring_rule_from=1)
        self.assertTrue(np.isfinite(m[0]))
        self.assertTrue(np.isnan(m[1]))

    def test_a_click_is_refused_by_the_same_rule(self):
        app = types.SimpleNamespace(preview=np.zeros((200, 300)), sky_out=types.SimpleNamespace(get=lambda: "20"))
        text = App._comp_ring_off_frame(app, (19.0, 100.0))
        self.assertIn("runs past the edge", text)
        self.assertIn("left out of the whole night", text)
        self.assertEqual(App._comp_ring_off_frame(app, (20.0, 100.0)), "")   # the ring just fits: taken

    def test_the_star_panel_says_out_for_the_night(self):
        src = _src()
        i = src.index("    def _edge_note(self, role: str, xy) -> str:")
        body = src[i:src.index("    def _check_alternative_ring", i)]
        self.assertIn("need = sky_out   # 2.4.0 cleanup (#119)", body)
        self.assertIn("so it would be left out of the whole", body)
        self.assertNotIn("sky_out + 5.0", body)
        self.assertNotIn("sky_out + 5.0", src[src.index("    def _comp_ring_off_frame"):][:2000])


# ---- step 8 N6: the re-center save-hold -----------------------------------------------------------------------

def _mk(night, jd, mag):
    return core.Obs(jd=jd, mag=mag, merr=0.003, cmag=11.0, kmag=None, airmass=None, path=f"f{jd:.3f}.fit",
                    exptime=60.0, flux_var=1.0, flux_comp=1.0, night=night)


def _obs(night, jd, mag, seg="", jump=0, check=None):
    o = _mk(night, jd, mag)
    o.segment, o.jump, o.check_std, o.flag = seg, jump, check, ""
    return o


class TestRecenterHoldsTheSave(unittest.TestCase):
    def _app(self):
        return types.SimpleNamespace(_last_flip_signature=False)

    def test_a_big_recenter_step_with_the_check_stepping_too(self):
        pts = ([_obs("n", 1.0 + k / 100, 12.0, check=10.0) for k in range(10)]
               + [_obs("n", 1.2 + k / 100, 12.06, jump=1, check=10.05) for k in range(10)])
        why = App._flip_step_suspect(self._app(), pts, "n")
        self.assertEqual(why["kind"], core.STEP_RECENTER)
        self.assertAlmostEqual(why["step"], 0.06, places=3)
        self.assertIn("the check star steps too", why["text"])

    def test_the_flip_still_comes_first(self):
        pts = ([_obs("n", 1.0 + k / 100, 12.0, check=10.0) for k in range(10)]
               + [_obs("n", 1.2 + k / 100, 12.05, seg="flip", check=10.04) for k in range(10)]
               + [_obs("n", 1.4 + k / 100, 12.12, seg="flip", jump=1, check=10.10) for k in range(10)])
        why = App._flip_step_suspect(self._app(), pts, "n")
        self.assertEqual(why["kind"], core.STEP_FLIP)

    def test_the_question_names_the_recenter(self):
        src = _src()
        i = src.index("rc = why.get(\"kind\") == core.STEP_RECENTER")
        part = src[i:i + 1600]
        self.assertIn('("re-center step", "re-center")', part)
        self.assertIn("Not auto-saved: the night's {'re-center' if rc else 'flip'} step", src)


# ---- E: a pointing move that held under 5 frames is said ------------------------------------------------------

class TestShortMoves(unittest.TestCase):
    def test_a_move_at_the_end_of_the_night_is_said(self):
        # GSC 03308-00462 13 Nov: frames 95-97 re-centered 117 px (64") and the night ended
        regs = [{"dx": 92.0 + 0.5 * k, "dy": -5.0, "flipped": False} for k in range(10)] + \
               [{"dx": -10.0 + 0.6 * k, "dy": -3.7, "flipped": False} for k in range(3)]
        out = core.recenter_segments(regs, 0.55, 2.0)
        self.assertEqual(out["recenters"], [])            # not a segment (it held 3 frames)
        self.assertEqual(len(out["short"]), 1)
        self.assertEqual(out["short"][0]["frames"], [10, 11, 12])
        text = core.short_move_text(out["short"], frame_no=lambda k: k + 85)
        self.assertTrue(text.startswith("The pointing moved at frames 95–97 ("), text)
        self.assertIn("held for fewer than 5 frames", text)
        self.assertNotIn("⚠", text)

    def test_a_real_recenter_is_not_a_short_move(self):
        regs = [{"dx": 0.0, "dy": 0.0, "flipped": False}] * 8 + [{"dx": 200.0, "dy": 0.0, "flipped": False}] * 8
        out = core.recenter_segments(regs, 1.0, 1.0)
        self.assertEqual(len(out["recenters"]), 1)
        self.assertEqual(out["short"], [])

    def test_the_run_and_the_scan_say_it(self):
        src = _src()
        self.assertIn("out_short=short", src)
        self.assertIn("core.short_move_text(\n                            found[\"short\"]", src)


# ---- F: the field scan header --------------------------------------------------------------------------------

class TestScanHeaderWords(unittest.TestCase):
    def test_a_recenter_night(self):
        self.assertEqual(clearv_app.scan_moves_words({"recenters": 1, "n_flips": 0, "segments": 2}), "1 re-center")
        self.assertEqual(clearv_app.scan_moves_words({"recenters": 1, "n_flips": 1, "segments": 3}),
                         "1 meridian flip or rotator move and 1 re-center")
        self.assertEqual(clearv_app.scan_moves_words({"recenters": 0, "segments": 3}),
                         "2 meridian flips or rotator moves")

    def test_the_header_no_longer_prints_the_boundary_list(self):
        src = _src()
        self.assertNotIn("result.get('boundaries', result['segments'] - 1)", src)
        self.assertNotIn('result["boundaries"] = int(n_fl) + len(recenters)', src)

    def test_no_all_nan_warning_from_align_segments(self):
        inst = np.array([[1.0, np.nan], [1.0, np.nan], [2.0, np.nan]] * 4)
        seg = np.array([0, 0, 1] * 4)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            core.align_segments(inst, seg, min_frames=2)


# ---- G: Known variables, one row per star ---------------------------------------------------------------------

class TestKnownVariablesOnce(unittest.TestCase):
    def test_vsx_and_simbad_for_one_star_merge(self):
        known = [{"name": "GSC 03308-00462", "source": "VSX", "desc": "VSX EW", "ra": 41.8228, "dec": 50.7518,
                  "type": "EW"},
                 {"name": "ATO J041.8228+50.7518", "source": "SIMBAD", "type": "EB*", "desc": "eclipsing",
                  "ra": 41.82285, "dec": 50.75182},
                 {"name": "Other", "source": "VSX", "desc": "VSX RR", "ra": 41.9, "dec": 50.8}]
        out = core.merge_known_variables(known, 8.0)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["name"], "GSC 03308-00462 / ATO J041.8228+50.7518")
        self.assertEqual(out[0]["source"], "VSX + SIMBAD")
        self.assertEqual(len(out[0]["members"]), 2)
        self.assertEqual(core.merge_known_variables(out, 8.0), out)   # idempotent

    def test_not_measured_gives_the_reason(self):
        why = core.known_not_measured_why
        left = [(100.0, 100.0, "crowded"), (5.0, 50.0, "edge"), (300.0, 300.0, "excluded")]
        self.assertIn("crowded", why((101.0, 100.0), left, (1000, 1000), 23, 4.0, False, 800))
        self.assertIn("frame edge", why((5.0, 51.0), left, (1000, 1000), 23, 4.0, False, 800))
        self.assertIn("by hand", why((300.0, 301.0), left, (1000, 1000), 23, 4.0, False, 800))
        self.assertIn("brightest 800", why((500.0, 500.0), left, (1000, 1000), 23, 4.0, True, 800))
        self.assertIn("not detected", why((500.0, 500.0), left, (1000, 1000), 23, 4.0, False, 800))
        self.assertIn("frame edge", why((10.0, 500.0), [], (1000, 1000), 23, 4.0, False, 800))


# ---- H: the flip-step words ------------------------------------------------------------------------------------

class TestFlipStepWords(unittest.TestCase):
    def _choice(self, kept_mine, dbs, other_kept=False):
        rows = [{"key": "none", "label": "none", "dbic": 0.0, "kept": kept_mine, "dbic_step": dbs},
                {"key": "airmass", "label": "airmass", "dbic": 5.6, "kept": other_kept, "dbic_step": 12.0}]
        return {"key": "none", "label": "none", "rows": rows}

    def test_not_needed(self):
        text = tc.detrend_choice_text(self._choice(False, 0.5))
        self.assertIn("flip step not needed (it lowers the BIC by only 0.5; 10 is needed)", text)
        self.assertNotIn("kept with none", text)

    def test_kept(self):
        self.assertIn("flip step kept (it lowers the BIC by 25.0)", tc.detrend_choice_text(self._choice(True, 25.0)))

    def test_other_choices_that_keep_it_are_named(self):
        text = tc.detrend_choice_text(self._choice(False, -1.0, other_kept=True))
        self.assertIn("flip step not needed (it does not lower the BIC) (it would be kept with airmass)", text)


# ---- B, D, I, J, K, K+ and m509 --------------------------------------------------------------------------------

class TestSmallOnes(unittest.TestCase):
    def test_b_the_page_says_the_fit_runs(self):
        import transit_ui
        src = inspect.getsource(transit_ui.TransitPage.fit)
        i = src.index('self.app._job_words("Fitting the transit: least-squares fit…")')
        self.assertIn("self._notes_text([(\"info\", FIT_RUNNING)]", src[i:i + 600])   # (+ the helper note, card 1)
        self.assertNotIn("press Fit transit", transit_ui.FIT_RUNNING)

    def test_d_a_clicked_comp_is_logged(self):
        said = []
        app = types.SimpleNamespace(status=types.SimpleNamespace(set=lambda t: None), log=said.append,
                                    comp1_name=types.SimpleNamespace(get=lambda: "TYC 1"), _slot_notes={},
                                    update_star_panel=lambda: None)
        App._slot_say(app, "comp1", "✓ Comp 1: TYC 1  V = 11.234 (APASS)")
        self.assertEqual(said, ["Comp 1: ✓ TYC 1  V = 11.234 (APASS)"])
        src = _src()
        self.assertIn('self._slot_say(mode, ("✓ " if "=" in filled else "") + filled + snap_note', src)

    def test_i_watch_stars_wait_for_the_series_lights(self):
        said = []
        app = types.SimpleNamespace(watch_list=[{"name": "W1"}, {"name": "W2"}], _preview_foreign="GSC",
                                    log=said.append, _refresh_watch_box=lambda: None, series_path="x",
                                    _series_off_this_image=lambda: True)
        App._locate_watch_stars(app)
        self.assertEqual(len(said), 1)
        self.assertIn("placed after Calibrate on this series' lights", said[0])
        self.assertNotIn("click the missing", said[0])

    def test_j_the_format_line_says_when(self):
        src = _src()
        i = src.index("    def _convert_series_backup")
        body = src[i:i + 2500]
        self.assertIn("is in the older star format: it is converted to the new one when the \"", body)
        self.assertEqual(body.count("series is next saved"), 3)
        self.assertNotIn("Series updated to the new star format", body)

    def test_k_focus_that_went_elsewhere_looks_nothing_up(self):
        ran = []
        entry = types.SimpleNamespace(instate=lambda s: False)
        queue_ = []

        def app_with(focus_seq):
            seq = list(focus_seq)
            a = types.SimpleNamespace(after=lambda ms, fn: queue_.append(fn))
            a.focus_get = lambda: seq.pop(0) if len(seq) > 1 else seq[0]
            a.__dict__["_pending_lookups"] = {}
            return a

        def drain():
            while queue_:
                queue_.pop(0)()
        for seq, want in (([entry], 0), (["other"], 1), ([None, None, entry], 0), ([None, "other"], 1)):
            ran.clear()
            app = app_with(seq)
            App._focus_left(app, entry, lambda: ran.append(1))
            drain()
            self.assertEqual(len(ran), want, seq)

    def test_k_a_quick_click_on_a_job_button_looks_up_first(self):
        # review M1: the button's command (and _start_job) runs before the deferred check
        ran, later = [], []
        app = types.SimpleNamespace(after=lambda ms, fn: later.append(fn), focus_get=lambda: "button")
        app.__dict__["_pending_lookups"] = {}
        entry = types.SimpleNamespace(instate=lambda s: True)
        App._focus_left(app, entry, lambda: ran.append("lookup"))
        App._flush_pending_lookup(app)          # what _start_job does first
        ran.append("job")
        for fn in later:
            fn()
        self.assertEqual(ran, ["lookup", "job"])
        self.assertIn("self._flush_pending_lookup()   # 2.4.0 cleanup (review M1)", _src())

    def test_k_plus_a_new_target_takes_the_old_findings(self):
        store = findings.FindingsList(hints=[])
        store.add("error", "✖ The frames are centered at 02:47:17 +50:45:20, 12° from HIP 115622 …",
                  step=App.TARGET_CHECK)
        store.add("warn", "⚠ something from a run", step="Photometry")
        app = types.SimpleNamespace(findings=store, TARGET_CHECK=App.TARGET_CHECK)
        App._drop_target_findings(app)
        self.assertEqual([f.step for f in store.items], ["Photometry"])
        src = _src()
        self.assertIn('if why == "lookup":', src)
        self.assertIn("self._drop_target_findings()   # 2.4.0 cleanup (K+): the series' target", src)

    def test_m509_never_on_the_window_thread_in_the_threaded_load(self):
        src = _src()
        i = src.index("            elif not sync:\n                # 2.4.0 cleanup: never a network call")
        self.assertIn("same = None", src[i:i + 400])
        stale = App._series_read_stale
        app = types.SimpleNamespace(star_id=types.SimpleNamespace(get=lambda: "HIP 114622"))
        meta = {"star_id": "HD 219134"}
        self.assertTrue(stale(app, (([meta], []) and (meta, []), None, {"cur": "HIP 114", "same": None})))
        self.assertFalse(stale(app, ((meta, []), None, {"cur": "HIP 114622", "same": True})))
        app2 = types.SimpleNamespace(star_id=types.SimpleNamespace(get=lambda: "HD 219134"))
        self.assertFalse(stale(app2, ((meta, []), None, {"cur": "", "none": True})))   # same name: no lookup


if __name__ == "__main__":
    unittest.main()


# ---- the independent review's findings (27 Nov) ------------------------------------------------------------------

class TestReviewFixes(unittest.TestCase):
    def test_m2_a_scan_star_matches_any_catalog_position_of_a_merged_star(self):
        src = _src()
        self.assertIn('for pm in (k.get("members") or [k]):', src)

    def _app(self):
        return types.SimpleNamespace(_last_flip_signature=False)

    def test_s1_a_benign_flip_does_not_hide_a_suspicious_recenter(self):
        pts = ([_obs("n", 1.0 + k / 100, 12.0, check=10.0) for k in range(10)]
               + [_obs("n", 1.2 + k / 100, 12.03, seg="flip", check=10.0) for k in range(10)]
               + [_obs("n", 1.4 + k / 100, 12.13, seg="flip", jump=1, check=10.10) for k in range(10)])
        why = App._flip_step_suspect(self._app(), pts, "n")
        self.assertEqual(why["kind"], core.STEP_RECENTER)
        self.assertIn("the check star steps too", why["text"])

    def test_s1_the_suspicious_one_of_two_recenters(self):
        pts = ([_obs("n", 1.0 + k / 100, 12.0, check=10.0) for k in range(10)]
               + [_obs("n", 1.2 + k / 100, 12.05, jump=1, check=10.05) for k in range(10)]
               + [_obs("n", 1.4 + k / 100, 12.17, jump=2, check=10.05) for k in range(10)])
        why = App._flip_step_suspect(self._app(), pts, "n")
        self.assertAlmostEqual(why["step"], 0.05, places=3)
        self.assertIn("the check star steps too", why["text"])

    def test_s1_nothing_suspicious_saves(self):
        pts = ([_obs("n", 1.0 + k / 100, 12.0, check=10.0) for k in range(10)]
               + [_obs("n", 1.2 + k / 100, 12.05, jump=1, check=10.0) for k in range(10)])
        self.assertEqual(App._flip_step_suspect(self._app(), pts, "n")["text"], "")

    def test_s2_the_notes_come_back_after_cancel(self):
        import transit_ui
        said = []
        page = types.SimpleNamespace(result={"flags": [("info", "Detected."), ("warn", "a caution")], "verdict": None},
                                     _notes_text=said.append)
        transit_ui.TransitPage._notes_after_no_fit(page, "Canceled: not fitted yet. Press Fit transit.")
        self.assertEqual(said[0][0], ("info", "Canceled: not fitted yet. Press Fit transit."))
        self.assertTrue(any("caution" in t for _l, t in said[0][1:]))
        page.result = None
        transit_ui.TransitPage._notes_after_no_fit(page, "The fit failed: x", warn=True)
        self.assertEqual(said[1], [("warn", "The fit failed: x")])

    def test_s5_an_answered_question_is_not_a_slow_pause(self):
        now = [0.0]
        said = []
        w = ui_watch.UIWatch(said.append, main_thread_id=-1, clock=lambda: now[0])
        with mock.patch.object(w, "_in_question", return_value=True):
            now[0] += 2.0
            w.check()               # the watch sees the box up during the pause
            w.beat()                # answered: the beat does not count it
        self.assertEqual(w._slow, [])

    def test_s6_and_s7_in_the_code(self):
        src = _src()
        i = src.index("# 2.4.0 cleanup (review S6): a new solve checks the target")
        self.assertIn("self._drop_target_findings()", src[i:i + 500])
        self.assertIn("self._drop_target_findings()   # 2.4.0 cleanup (review S6): a new image", src)
        self.assertIn("core.sky_ring_past_edge(img.shape, xy[j, 0], xy[j, 1], so)", _src("transit_ui.py"))

    def test_nit_nan_counts_as_past_the_edge_either_way(self):
        self.assertTrue(core.sky_ring_past_edge((200, 300), 50.0, float("nan"), 10.0))
        self.assertTrue(core.sky_ring_past_edge((200, 300), float("nan"), 50.0, 10.0))

    def test_nit_held_why(self):
        self.assertEqual(App._held_why({"a": {"kind": "re-center"}}), "re-center step ⚠")
        self.assertEqual(App._held_why({"a": {}}), "flip step ⚠")
        self.assertEqual(App._held_why({"a": {"kind": "flip"}, "b": {"kind": "re-center"}}),
                         "flip or re-center step ⚠")


def _kw_callback(progress=None):
    progress(1, 2, label="x")
    return "ok"


class TestHelperReviewFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.was = fit_process.ENABLED
        fit_process.ENABLED = True
        fit_process._broken_why, fit_process._deaths, fit_process._closed = "", 0, False

    @classmethod
    def tearDownClass(cls):
        fit_process.shutdown()
        fit_process.ENABLED = cls.was
        fit_process._closed = False
        fit_process._broken_why, fit_process._deaths = "", 0

    def test_s3_a_helper_that_dies_mid_fit_falls_back(self):
        import signal
        t, f, e, am, planet = _night()
        said = []
        fit_process.set_logger(said.append)

        def kill():
            time.sleep(1.2)
            for h in list(fit_process._all):
                try:
                    os.kill(h.proc.pid, signal.SIGKILL)
                except Exception:
                    pass
        threading.Thread(target=kill, daemon=True).start()
        try:
            r = fit_process.call(tc.fit_transit, t, f, e, None, planet, mcmc_steps=3000, progress=lambda *a: None)
        finally:
            fit_process.set_logger(None)
        self.assertIn("summary", r)
        self.assertTrue(any("stopped during the fit" in s for s in said), said)
        fit_process._deaths, fit_process._broken_why = 0, ""

    def test_s4_a_big_frame_reads_whole(self):
        import io
        data = os.urandom(3_000_000)
        buf = io.BytesIO()
        fit_process._send(buf, data)
        buf.seek(0)

        class Slow:   # a pipe that gives 4 KB at a time
            def readinto(self, view):
                chunk = buf.read(min(4096, len(view)))
                view[:len(chunk)] = chunk
                return len(chunk)
        t0 = time.monotonic()
        self.assertEqual(fit_process._recv(Slow()), data)
        self.assertLess(time.monotonic() - t0, 2.0)

    def test_nit_a_keyword_callback_works(self):
        got = []
        # (a test module is not in the app's folder, so call() would run it in-thread: send it to the helper anyway)
        with mock.patch.object(fit_process, "_own_function", return_value=(_kw_callback.__module__, "_kw_callback")):
            out = fit_process.call(_kw_callback, progress=lambda *a, **k: got.append((a, k)))
        self.assertEqual(out, "ok")
        self.assertEqual(got, [((1, 2), {"label": "x"})])

    def test_nit_a_cancel_set_before_the_call_stops_at_once(self):
        t, f, e, am, planet = _night()
        ev = threading.Event()
        ev.set()
        t0 = time.monotonic()
        with self.assertRaises(RuntimeError):
            fit_process.call(tc.fit_transit, t, f, e, None, planet, mcmc_steps=50000, cancel=ev)
        self.assertLess(time.monotonic() - t0, 5.0)

    def test_nit_a_job_that_does_not_pickle_runs_here_without_a_helper(self):
        t, f, e, am, planet = _night()
        before = len(fit_process._all)
        planet = dict(planet, odd=threading.Lock())   # cannot be pickled
        with self.assertRaises(Exception):
            fit_process.call(tc.fit_transit, t[:2], f[:2], e[:2], None, planet)   # in-thread: the fit's own error
        self.assertLessEqual(len(fit_process._all), max(before, 1))
