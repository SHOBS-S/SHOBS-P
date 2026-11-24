"""2.4.0 fit-in F2, Fable review 1 fixes.

S1  the gates are cheap: a refresh stays in milliseconds on a 42,000-point, 120-night series (the series' answers
    are kept until the series changes, and recomputed when it does).
S2  a late ephemeris lookup for another report neither clears nor fires the Fit transit queued for this one.
S3  Cancel, Close report, another report and a planet change call off a queued Fit transit (m787); Cancel and a
    planet change call off a queued Fit nights (m681); the status line says so.
S4  m140 never overwrites an aperture typed by hand for these lights.
S6  Add series file… and Clear target are on the busy lock.
S9  the Output box is checked after typing stops, off the Tk thread; a network folder is checked when you save.
N1  the gate timer is cancelled.
N4  the saves name the missing plot library, not a missing fit."""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import gate  # noqa: E402
import photometry_core as core  # noqa: E402
import transit_ui as tui  # noqa: E402

try:
    import tkinter  # noqa: F401
    tkinter.Tk().destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False

if HAVE_TK:
    import test_240_m as M  # noqa: E402
    Base = M.AppCase
else:
    Base = unittest.TestCase


def disabled(w):
    return "disabled" in w.state()


def find_button(root, text):
    stack = [root]
    while stack:
        w = stack.pop()
        try:
            if w.winfo_class() == "TButton" and w.cget("text") == text:
                return w
        except Exception:
            pass
        stack.extend(w.winfo_children())
    return None


def _big_series(nights=120, per=350):
    out = []
    for ni in range(nights):
        night = f"JD{2460950 + ni}"
        for i in range(per):
            out.append(core.Obs(jd=2460950.6 + ni + 0.001 * i, mag=10.0 + 0.001 * (i % 7), merr=0.004, cmag=-10.0,
                                kmag=-8.5, airmass=1.2, path=f"L_{ni}_{i}.fit", exptime=30.0, flux_var=1000.0,
                                flux_comp=2000.0, night=night, filt="TG"))
    return out


def _report(name):
    return {"planet": {"name": name, "period": 1.5}, "t": np.linspace(0, 0.1, 30), "flux": np.ones(30),
            "err": np.ones(30) * 1e-3, "airmass": None, "header": {}, "path": ""}


class TestSeriesMemo(unittest.TestCase):
    def test_kept_until_the_series_changes(self):
        owner = type("O", (), {})()
        owner.observations = [1, 2, 3]
        calls = []

        def work():
            calls.append(1)
            return len(calls)
        self.assertEqual(gate.series_memo(owner, "k", work), 1)
        self.assertEqual(gate.series_memo(owner, "k", work), 1)          # kept
        owner.observations = owner.observations + [4]                     # a new list
        self.assertEqual(gate.series_memo(owner, "k", work), 2)
        gate.bump_series(owner)                                           # flags changed in place
        self.assertEqual(gate.series_memo(owner, "k", work), 3)
        self.assertEqual(gate.series_memo(owner, "k", work, vals=("a",)), 4)
        self.assertEqual(gate.series_memo(owner, "k", work, vals=("a",)), 4)
        ref = []
        self.assertEqual(gate.series_memo(owner, "k", work, refs=(ref,)), 5)
        self.assertEqual(gate.series_memo(owner, "k", work, refs=([],)), 6)   # another object, same value

    def test_stop_cancels_the_timer(self):
        if not HAVE_TK:
            self.skipTest("no display")
        root = tkinter.Tk()
        root.withdraw()
        try:
            g = gate.Gates()
            g.start(root, interval_ms=10000)
            self.assertIsNotNone(g._timer)
            g.stop()
            self.assertIsNone(g._timer)
            self.assertFalse(any("tick" in str(j) for j in root.tk.call("after", "info")))
        finally:
            root.destroy()


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestGateCost(Base):
    def test_refresh_is_cheap_on_a_long_series(self):
        app = self.app
        obs = _big_series()
        try:
            app.observations = obs
            app.update_series_status()
            last = obs[-1].night
            for label, setup in (("no night", lambda: setattr(app, "highlight_night", None)),
                                 ("night picked", lambda: setattr(app, "highlight_night", last)),
                                 ("trim text", lambda: (app.trim_from.set("2100"), app.trim_to.set("2200")))):
                setup()
                app.gates.refresh()   # the first one after a change may work it out
                t = time.perf_counter()
                for _ in range(10):
                    app.gates.refresh()
                ms = (time.perf_counter() - t) / 10 * 1000.0
                # target ~2 ms; a generous bound for a busy test machine (it was 200-260 ms before)
                self.assertLess(ms, 25.0, f"{label}: a gate refresh took {ms:.1f} ms")
            self.assertEqual(app._trim_night(), last)
            # the kept answers follow the series: a night removed is gone at once
            app.observations = [o for o in obs if o.night != last]
            self.assertIsNone(app._trim_night())
            self.assertIsNone(app._picked_night())
        finally:
            app.trim_from.set("")
            app.trim_to.set("")
            app.highlight_night = None
            app.observations = []
            app.update_series_status()


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestQueuedFit(Base):
    def setUp(self):
        super().setUp()
        tp = self.app.transit_page
        tp._fit_queued = None
        tp._eph_pending = None

    def tearDown(self):
        tp = self.app.transit_page
        tp._fit_queued = None
        tp._eph_pending = None
        tp.report = None
        super().tearDown()

    def _queue(self, rep):
        tp = self.app.transit_page
        tp.report = rep
        tp._eph_pending = rep
        tp.fit()
        self.assertIs(tp._fit_queued["report"], rep)

    def _land(self, rep, found=None):
        tp = self.app.transit_page
        with mock.patch.object(tp, "fit") as fit, mock.patch.object(tp, "_fill_table"), \
                mock.patch.object(tp, "draw"), mock.patch.object(tp, "_notes_text"):
            tp._ephemeris_found(rep, found, "")
        return fit

    def test_s2_a_late_lookup_for_another_report(self):
        tp = self.app.transit_page
        a, b = _report("A b"), _report("B b")
        self._queue(b)
        fit = self._land(a)              # A's lookup lands while B is shown
        fit.assert_not_called()
        self.assertIs(tp._fit_queued["report"], b, "B's queued fit is kept")
        fit = self._land(b, {"t0": 1.0, "period": 1.5})
        fit.assert_called_once_with(with_flagged=False)
        self.assertIsNone(tp._fit_queued)

    def test_s3_cancel_calls_it_off(self):
        app, tp = self.app, self.app.transit_page
        rep = _report("A b")
        self._queue(rep)
        app.busy = False
        app.cancel_job()
        self.assertIsNone(tp._fit_queued)
        self.assertIn("Fit transit called off", app.status.get())
        self._land(rep).assert_not_called()

    def test_s3_close_report_and_another_report(self):
        app, tp = self.app, self.app.transit_page
        rep = _report("A b")
        self._queue(rep)
        with mock.patch.object(tp, "refresh"), mock.patch.object(tp, "draw"), mock.patch.object(tp, "_fill_table"):
            tp.close_report()
        self.assertIsNone(tp._fit_queued)
        self.assertIn("called off: the report was closed", app.status.get())
        self._land(rep).assert_not_called()
        # another report opened: the queue for the one before goes
        self._queue(rep)

        class Stop(Exception):
            pass
        with mock.patch.object(tui.filedialog, "askopenfilename", return_value=__file__), \
                mock.patch.object(tui.tc, "parse_exoplanet_report", return_value=_report("B b")), \
                mock.patch.object(tui.tc, "planet_from_report", return_value={"name": "B b"}), \
                mock.patch.object(tp, "_report_time_system"), \
                mock.patch.object(tp, "_clear_progress", side_effect=Stop):
            with self.assertRaises(Stop):
                tp.open_report()
        self.assertIsNone(tp._fit_queued)
        self.assertIn("another report was opened", app.status.get())

    def test_s3_planet_changed_while_it_waited(self):
        app, tp = self.app, self.app.transit_page
        rep = _report("A b")
        self._queue(rep)
        with mock.patch.object(tp, "current_planet", return_value={"name": "C c", "period": 2.0}):
            fit = self._land(rep)
        fit.assert_not_called()
        self.assertIn("the planet changed", app.status.get())
        self.assertIsNone(tp._fit_queued)

    def test_n4_no_plot_library(self):
        tp = self.app.transit_page
        keep_r, keep_f = tp.result, tp.fig
        try:
            tp.result, tp.fig = {"x": 1}, None
            self.assertIn("matplotlib", tp._save_why())
            tp.result = None
            self.assertEqual(tp._save_why(), tui.FIT_FIRST)
        finally:
            tp.result, tp.fig = keep_r, keep_f


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestQueuedMultiNight(Base):
    def _window(self):
        import multinight as mn
        planet = {"name": "TOI-2046 b", "host": "TOI-2046"}
        page = type("P", (), {"current_planet": lambda s: dict(planet), "result": None,
                              "detrend_airmass": type("V", (), {"get": lambda s: True})(),
                              "detrend_time": type("V", (), {"get": lambda s: False})()})()
        with mock.patch.object(mn.MultiNightWindow, "lookup"):
            w = mn.MultiNightWindow(self.app, page)
        w._lookup_running = 1
        w._all_nights = lambda: [{"use": True, "status": ("ok", ""), "planet": "TOI-2046 b"},
                                 {"use": True, "status": ("ok", ""), "planet": "TOI-2046 b"}]
        return mn, w

    def test_cancel_and_planet_change(self):
        mn, w = self._window()
        try:
            with mock.patch.object(mn, "check_nights"), mock.patch.object(w, "_start_fit_thread") as start:
                w.run_fit()
                self.assertTrue(w._fit_after_lookup)
                w.cancel()                                   # Cancel calls it off
                self.assertFalse(w._fit_after_lookup)
                self.assertIn("Fit nights called off", w.prog_text.get())
                with mock.patch.object(w.busy, "end"):
                    w._looked_up("TOI-2046 b", {"name": "TOI-2046 b", "period": 1.497}, "")
                start.assert_not_called()
                # a planet change while it waits
                w.planet = {"name": "TOI-2046 b"}             # no period yet: the fit waits
                w._lookup_running = 1
                w.run_fit()
                self.assertEqual(w._fit_after_lookup, "TOI-2046 b")
                w.planet = {"name": "WASP-12 b"}
                with mock.patch.object(w.busy, "end"):
                    w._looked_up("TOI-2046 b", None, "offline")
                start.assert_not_called()
                self.assertIn("the planet changed", w.prog_text.get())
        finally:
            w._running = False
            w.close()


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestInputPage(Base):
    SCOPE = {"TELESCOP": "NexStar 8SE", "INSTRUME": "ASI2600MM", "FOCALLEN": 2100}

    def test_s4_hand_typed_aperture_is_kept(self):
        app = self.app
        app.settings["aperture_scopes"] = [{"telescope": "NexStar 8SE", "camera": "ASI2600MM", "focal_mm": 2098.0,
                                            "aperture_mm": 152.0}]
        try:
            app._aperture_hand = None
            app.aperture_mm.set("100")
            app._check_aperture_scope(self.SCOPE)            # not typed: the remembered value is set
            self.assertEqual(app.aperture_mm.get(), "152")
            app.aperture_mm.set("203")
            app._aperture_typed()                            # typed by hand for these lights
            self.lines.clear()
            app._check_aperture_scope(self.SCOPE)            # Scan folders again
            self.assertEqual(app.aperture_mm.get(), "203")
            self.assertTrue(self.said("kept at 203 mm (typed by hand)"))
            # typed for other lights (another telescope): these lights get their remembered value
            app._aperture_hand = ("203", {"telescope": "Other", "camera": "X", "focal_mm": 500.0})
            app._check_aperture_scope(self.SCOPE)
            self.assertEqual(app.aperture_mm.get(), "152")
        finally:
            app.settings.pop("aperture_scopes", None)
            app._aperture_hand = None

    def test_s6_add_series_file_and_clear_target_on_the_busy_lock(self):
        app = self.app
        buttons = [find_button(app, "Add series file…"), find_button(app, "Clear target")]
        self.assertNotIn(None, buttons)
        try:
            app.busy = True
            app.gates.refresh()
            for b in buttons:
                self.assertTrue(disabled(b))
                self.assertEqual(app.gates.reason(b), gate.BUSY_REASON)
            with mock.patch.object(self.ca.filedialog, "askopenfilename") as ask:
                app.add_series_file()
                ask.assert_not_called()
        finally:
            app.busy = False
            app.gates.refresh()
        for b in buttons:
            self.assertFalse(disabled(b))

    def test_s9_output_box_not_checked_per_keystroke(self):
        import tempfile
        app = self.app
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(type(app), "output_folder_usable", side_effect=lambda f: True) as usable:
            for part in (d[:3], d[:6], d):
                app.output_dir.set(part)
            usable.assert_not_called()                       # nothing touched the disk while typing
            end = time.time() + 5.0
            while app._counts["Output"].get() != "in use" and time.time() < end:
                app.update()
                time.sleep(0.02)
            self.assertEqual(app._counts["Output"].get(), "in use")
            self.assertEqual(usable.call_count, 1)           # once, after typing stopped
            usable.reset_mock()
            app.output_dir.set("\\\\server\\share\\shobs")
            end = time.time() + 2.0
            while app._counts["Output"].get() != app.OUTPUT_NETWORK_WORDS and time.time() < end:
                app.update()
                time.sleep(0.02)
            self.assertEqual(app._counts["Output"].get(), "checked when you save")
            usable.assert_not_called()
            app.output_dir.set("")
            self.assertEqual(app._counts["Output"].get(), "")
        self.assertTrue(app.output_folder_on_network("//server/share"))
        self.assertFalse(app.output_folder_on_network(os.path.expanduser("~")))


if __name__ == "__main__":
    unittest.main()
