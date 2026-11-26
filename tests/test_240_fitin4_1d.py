"""2.4.0 fit-in F4, package F4-1 (d): Suggest's aperture test judged on the steadiest spare (m627), the zenith keyhole
said only for frames that really are smeared (m031), OBJECT = Star ID but the RA/Dec is off: looked up again
(m089), camera-binned colour frames: the channels compared (m191), and the nits (reference set-up busy words,
variable_flags, Measure skipped frames after a reference change, the scroll leftovers)."""
import math
import os
import sys
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import comp_survey as cs          # noqa: E402
import page_scroll                # noqa: E402
import photometry_core as core    # noqa: E402
import clearv_app                 # noqa: E402

App = clearv_app.App
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(name="clearv_app.py"):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def _body(src: str, start: str, end: str = "\n    def ") -> str:
    i = src.index(start)
    return src[i:src.index(end, i + len(start))]


def _obs(f, night="N1", **kw):
    o = core.Obs(jd=2461000.0 + f * 0.001, mag=10.0, merr=0.01, cmag=-9.0, kmag=None, airmass=1.2,
                 path=f"/n/Light_{f + 1:04d}.fits", exptime=60.0, flux_var=0.0, flux_comp=0.0, night=night)
    for k, v in kw.items():
        setattr(o, k, v)
    return o


class TestSteadiestSpare(unittest.TestCase):
    def _app(self, n=30):
        rng = np.random.default_rng(4)
        pts = []
        for f in range(n):
            pts.append(_obs(f, spare_insts={"QUIET": -8.0 + rng.normal(0, 0.003), "NOISY": -7.5 + rng.normal(0, 0.03),
                                            "RARE": -7.0} if f < 3 else
                            {"QUIET": -8.0 + rng.normal(0, 0.003), "NOISY": -7.5 + rng.normal(0, 0.03)}))
        app = types.SimpleNamespace(observations=pts, _latest_night=lambda: "N1",
                                    spare_marks=[(10.0, 20.0, "NOISY"), (30.0, 40.0, "QUIET"), (50.0, 60.0, "RARE")])
        return app

    def test_picks_the_spare_with_the_least_scatter(self):
        self.assertEqual(App._steadiest_spare(self._app()), ("QUIET", (30.0, 40.0)))

    def test_none_without_a_run_or_spares(self):
        app = self._app()
        app.spare_marks = []
        self.assertIsNone(App._steadiest_spare(app))
        app = self._app()
        app.observations = []
        self.assertIsNone(App._steadiest_spare(app))

    def test_the_test_judges_on_it_in_the_check_stars_place(self):
        rng = np.random.default_rng(1)
        m = np.column_stack([-9 + rng.normal(0, 0.05, 40), -8 + rng.normal(0, 0.002, 40),
                             -8.2 + rng.normal(0, 0.002, 40), -7.9 + rng.normal(0, 0.002, 40)])
        a = core.aperture_test_stats({4.0: m, 6.0: m + rng.normal(0, 0.001, m.shape)}, "steadiest spare (QUIET)",
                                     has_comp2=True)
        b = core.aperture_test_stats({4.0: m, 6.0: m + rng.normal(0, 0.001, m.shape)}, "check star", has_comp2=True)
        self.assertAlmostEqual(a["rows"][0]["ref_sigma"], b["rows"][0]["ref_sigma"], places=6)
        self.assertLess(a["rows"][0]["ref_sigma"], 0.01)        # the quiet column 3, not the target's 0.05
        body = _body(_src(), "    def test_apertures(self")
        self.assertIn('ref_label = f"steadiest spare ({spare[0]})"', body)
        self.assertIn("No check star is marked, so the test judges on the", _src())


class TestKeyholeNote(unittest.TestCase):
    def _night(self, smeared=()):
        pts = []
        for f in range(40):
            high = 15 <= f < 25
            pts.append(_obs(f, airmass=1.002 if high else 1.05, elong=1.6 if f in smeared else 1.08))
        return pts

    def test_silent_when_the_frames_up_there_are_fine(self):
        self.assertEqual(App.keyhole_note(self._night()), "")

    def test_names_the_smeared_frames(self):
        text = App.keyhole_note(self._night(smeared=(18, 19, 20)))
        self.assertIn("3 of the 10 frames taken above 85° altitude are smeared", text)
        self.assertIn("frames 19–21", text)
        self.assertIn("Blink page", text)

    def test_a_smeared_frame_low_down_is_not_the_keyhole(self):
        self.assertEqual(App.keyhole_note(self._night(smeared=(2, 3))), "")

    def test_not_said_from_the_altitude_alone_any_more(self):
        self.assertNotIn("frames there can", _body(_src(), "    def _update_mount_hint(self"))
        self.assertIn("key_line = self.keyhole_note(night_points)", _src())


class TestObjectMatchesButPointingOff(unittest.TestCase):
    def _app(self):
        logged, looked = [], []
        hdr = {"OBJECT": "SZ Lyn", "RA": 200.0, "DEC": 10.0, "NAXIS1": 1000, "NAXIS2": 800}
        app = types.SimpleNamespace(
            mode_key="variables", ra_hours=types.SimpleNamespace(get=lambda: "8.16"),
            dec_deg=types.SimpleNamespace(get=lambda: "44.47"), star_id=types.SimpleNamespace(get=lambda: "SZ Lyn"),
            pixel_um=types.SimpleNamespace(get=lambda: 3.76), focal_mm=types.SimpleNamespace(get=lambda: 2800.0),
            log=lambda t, **k: logged.append(t), lookup_star=lambda quiet=False: looked.append(quiet),
            busy=False, observations=[])
        return app, hdr, logged, looked

    def test_looked_up_again_first_then_a_warning_with_what_to_do(self):
        app, hdr, logged, looked = self._app()
        with mock.patch.object(clearv_app, "ask_no", side_effect=AssertionError("no question")):
            App._check_target_field(app, hdr)
            self.assertEqual(looked, [True])
            self.assertIn("is looked up again", logged[0])
            self.assertFalse(logged[0].startswith("⚠"))
            # still that far off after the lookup: said once more, as a ⚠ with what to do; no second lookup
            App._check_target_field(app, hdr)
        self.assertEqual(looked, [True])
        self.assertTrue(logged[1].startswith("⚠ Lights: OBJECT SZ Lyn is the Star ID"))
        self.assertIn("check the Star ID, or type the field's RA/Dec", logged[1])


class TestCameraBinnedChannels(unittest.TestCase):
    def _mosaic(self, mixed: bool, n=40, seed=3):
        rng = np.random.default_rng(seed)
        h, w = 400, 400
        yy, xx = np.mgrid[0:h, 0:w]
        img = np.zeros((h, w))
        red = np.zeros((h, w))
        for _ in range(n):
            x, y = rng.uniform(20, w - 20), rng.uniform(20, h - 20)
            f = rng.uniform(4000, 20000)
            g = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 3.0 ** 2))
            img += f * g
            red += f * g * 10 ** (-0.4 * rng.uniform(-0.5, 0.5))     # each star's own colour
        out = np.full((h, w), 500.0) + img
        if not mixed:
            out[0::2, 0::2] = 700.0 + red[0::2, 0::2]                 # the red pixels: another sky, other colours
        return (out + rng.normal(0, 5.0, (h, w))).astype(np.float32)

    def test_mixed_channels_are_found(self):
        r = core.cfa_channels_identical(self._mosaic(True))
        self.assertTrue(r["identical"], r)
        self.assertLess(r["spread"], 0.03)

    def test_real_colour_data_say_nothing(self):
        r = core.cfa_channels_identical(self._mosaic(False))
        self.assertIs(r["identical"], False, r)

    def test_too_few_stars_cannot_tell(self):
        self.assertIsNone(core.cfa_channels_identical(self._mosaic(True, n=3))["identical"])
        src = _src()
        self.assertNotIn("If the channels look identical or B-V", src)
        self.assertIn("mix = core.cfa_channels_identical(self.calibrated)", src)


class TestNits(unittest.TestCase):
    def test_reference_set_up_busy_words(self):
        self.assertIn('self.call_ui(self._job_words, f"Setting up the reference (frame '
                      '{core.frame_label(lights[0])})…")', _src())

    def test_variable_flags_matches_the_plain_form(self):
        rng = np.random.default_rng(1)
        for n in (2, 4, 9, 60, 300):
            for _ in range(4):
                m = np.round(rng.uniform(-12, -6, n), int(rng.integers(1, 4)))
                sc = np.abs(rng.normal(0.01, 0.01, n))
                sc[int(rng.integers(0, n))] = np.nan
                if n > 5:
                    sc[int(rng.integers(0, n))] = 0.3
                self.assertEqual(cs.variable_flags(sc, m), cs.variable_flags_slow(sc, m))

    def test_measure_skipped_frames_after_a_reference_change(self):
        body = _body(_src(), "    def _remeasure_done(self")
        self.assertIn('was_ref != getattr(self, "_run_ref_frame", was_ref)', body)
        self.assertIn("known = []", body)
        self.assertIn('setdefault("_night_ref_frame", {})[night]', _src())

    def test_touchpad_scroll(self):
        def pack(dx, dy):
            return ((dx & 0xFFFF) << 16) | (dy & 0xFFFF)
        self.assertEqual(page_scroll.touchpad_dy(pack(0, 12)), 12)
        self.assertEqual(page_scroll.touchpad_dy(pack(3, -7)), -7)
        self.assertEqual(page_scroll.touchpad_dy("x"), 0)
        carry = [0]
        steps = [page_scroll.touchpad_units(pack(0, -8), carry) for _ in range(5)]   # fingers up: down the page
        self.assertEqual(sum(steps), 2)
        self.assertEqual(carry[0], 0)
        self.assertEqual(page_scroll.touchpad_units(pack(0, 45), [0]), -2)

    def test_scroll_bodies_guard_value_boxes_and_dead_spots(self):
        src = _src()
        self.assertIn("def _guard_value_boxes(self):", src)
        self.assertIn("if cls in page_scroll.WHEEL_NO_VALUE and not force:", src)
        ps = _src("page_scroll.py")
        self.assertIn("for w in (self, self.canvas):", ps)
        self.assertIn('"<TouchpadScroll>"', ps)


if __name__ == "__main__":
    unittest.main()
