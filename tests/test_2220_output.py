"""2.2.20 (worker C): item 2 field image, #47 Save for ExoClock, #46 New reduction notes."""
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

import clearv_app as ca
import photometry_core as core


def _frame(h=900, w=1400, seed=3):
    rng = np.random.default_rng(seed)
    img = rng.normal(1000.0, 40.0, (h, w)).astype(np.float32)
    for x, y, a in ((700, 450, 40000), (200, 150, 6000), (1200, 800, 2000)):
        ys, xs = np.mgrid[y - 30:y + 30, x - 30:x + 30]
        img[y - 30:y + 30, x - 30:x + 30] += a * np.exp(-((xs - x) ** 2 + (ys - y) ** 2) / (2 * 4.0 ** 2))
    return img


def _vectors(deg):
    a = math.radians(deg)
    return (math.sin(a), -math.cos(a)), (-math.cos(a), -math.sin(a))


class TestFieldImage(unittest.TestCase):
    def _info(self, n, e, sub="x"):
        return {"title": "HD 219134 — field", "subtitle": sub, "radius": 8, "sky_in": 14, "sky_out": 20,
                "north": n, "east": e, "arcsec_px": 0.56,
                "marks": [(700, 450, "T", "#ff7f0e", "HD 219134")], "seq": [(706, 453, "56"), (1200, 800, "145")]}

    def test_frame_as_shot_fills_the_image(self):
        """(a) never turned: the image drawn is the frame's own shape (no rotated canvas, no empty corners)."""
        img = _frame()
        for deg in (0, 37, 90, 200):
            n, e = _vectors(deg)
            fig = ca.draw_field_figure(img, self._info(n, e))
            ims = fig.axes[0].get_images()
            self.assertEqual(ims[0].get_array().shape, img.shape)
            self.assertGreater(float(np.min(ims[0].get_array())), -1e-6)   # no black fill from a turn

    def test_compass_follows_the_solution(self):
        """(a) N and E arrows point as they lie on the frame, and stay inside it."""
        w, h = 1400, 900
        for deg in (0, 37, 135, 200, 300):
            n, e = _vectors(deg)
            arrows = ca.field_compass(n, e, w, h)
            got = {tag: (np.array(tip) - np.array(base)) for tag, base, tip, _lab in arrows}
            for tag, vec in (("N", n), ("E", e)):
                d = got[tag] / np.linalg.norm(got[tag])
                self.assertAlmostEqual(float(d[0]), vec[0], places=6)
                self.assertAlmostEqual(float(d[1]), vec[1], places=6)
            for _tag, base, tip, lab in arrows:
                for x, y in (base, tip, lab):
                    self.assertTrue(0 <= x < w and 0 <= y < h, (deg, x, y))
        self.assertIsNone(ca.field_compass(None, None, w, h))

    def test_mirrored_frame_compass(self):
        """A mirrored frame (E to the right of N up) is shown as shot: E points right."""
        arrows = ca.field_compass((0.0, -1.0), (1.0, 0.0), 1000, 800)
        e = [t for t in arrows if t[0] == "E"][0]
        self.assertGreater(e[2][0], e[1][0])

    def test_subtitle_wraps_inside(self):
        """(b) a long subtitle wraps; every line fits in the figure."""
        sub = " · ".join(["Light_HD219134_0.75s_Bin1_20261026-031200_0042.fit", "6248 × 4176 px",
                          "0.56″/px, field 58.2′ × 38.9′", "rings: aperture r 22 and sky 40–60 px to scale",
                          "AAVSO chart X43358ABDR", "SHOBS-P 2.2.20"])
        n, e = _vectors(30)
        fig = ca.draw_field_figure(_frame(), self._info(n, e, sub))
        fig.canvas.draw()
        r = fig.canvas.get_renderer()
        fw = fig.bbox.width
        subs = [t for t in fig.texts if t.get_fontsize() == 8]
        self.assertGreaterEqual(len(subs), 2)
        self.assertIn("AAVSO", " ".join(t.get_text() for t in subs))
        for t in subs:
            self.assertLessEqual(t.get_window_extent(r).x1, fw + 0.5)

    def test_background_is_smooth(self):
        """(c) the sky of a noisy short frame is a smooth dark gray (much less pixel noise than the old 5–99.5 %
        linear stretch)."""
        img = _frame()
        sky = (slice(600, 880), slice(100, 500))
        new = ca.field_display(img)[sky]
        old = core.stretch_display(img, None)[sky]
        self.assertLess(float(np.std(new)), 0.5 * float(np.std(old)))
        self.assertLess(float(np.median(new)), 0.35)
        self.assertGreater(float(ca.field_display(img)[450, 700]), 0.9)   # the bright star stays bright

    def test_block_mean_large_frame(self):
        img = _frame(4100, 4100)
        small = ca.block_mean(img, 3)
        self.assertEqual(small.shape, (1366, 1366))
        self.assertLess(float(np.std(small[700:1000, 50:300])), 40.0 / 2.5)

    def test_sequence_ring_beside_target_dropped(self):
        """(d) the small cyan ring (and its label) a few px from the target's marker is not drawn."""
        seq = [(706, 453, "56"), (1200, 800, "145")]
        marks = [(700, 450, "T", "#ff7f0e", "HD 219134")]
        kept = ca.field_seq_shown(seq, marks, 8, 20, 0.5)
        self.assertEqual([s[2] for s in kept], ["145"])
        n, e = _vectors(0)
        fig = ca.draw_field_figure(_frame(), self._info(n, e))
        labels = [t.get_text() for t in fig.axes[0].texts]
        self.assertNotIn("56", labels)
        self.assertIn("145", labels)


class TestExoClock(unittest.TestCase):
    def test_names_and_filter(self):
        self.assertEqual(ca.exoclock_planet("TOI-2046 b"), "TOI-2046b")
        self.assertEqual(ca.exoclock_planet(" HD 219134  b"), "HD219134b")
        self.assertTrue(ca.exoclock_filter("TG").startswith("Johnson V"))
        self.assertEqual(ca.exoclock_filter("SR"), "Sloan r'")
        jd0 = 2461340.6
        self.assertEqual(ca.exoclock_name("TOI-2046 b", jd0), "ExoClock_TOI-2046b_20261027.txt")

    def test_jd_utc_round_trip(self):
        """The fit's BJD_TDB back to the frames' geocentric JD_UTC (mid-exposure), to well under a second."""
        jd = np.linspace(2461340.60, 2461340.75, 40)
        ra, dec, lat, lon = 348.32, 57.17, 35.37, -81.96
        bjd = core.convert_times(jd, "BJD_TDB", ra, dec, lat, lon)
        self.assertGreater(float(np.max(np.abs(bjd - jd))) * 86400, 60)   # they really differ
        back = ca.exoclock_jd_utc(bjd, "BJD_TDB", ra, dec, lat, lon)
        self.assertLess(float(np.max(np.abs(back - jd))) * 86400, 0.01)
        np.testing.assert_array_equal(ca.exoclock_jd_utc(jd, "JD_UTC"), jd)

    def test_upload_and_info_text(self):
        jd = np.array([2461340.70, 2461340.60, np.nan, 2461340.65])
        flux = np.array([0.999, 1.001, 1.0, 0.990])
        err = np.array([0.002, 0.002, 0.002, 0.002])
        meta = {"version": "2.2.20", "filter": "TG", "exposure": "60.0", "obscode": "JABA"}
        text = ca.exoclock_upload_text(jd, flux, err, "TOI-2046 b", meta)
        head = [ln for ln in text.splitlines() if ln.startswith("#")]
        rows = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
        self.assertTrue(any("TOI-2046b" in ln for ln in head))
        self.assertTrue(any("JD_UTC" in ln and "Mid-exposure" in ln for ln in head))
        self.assertEqual(len(rows), 3)                         # the NaN frame is left out
        cols = [r.split(" ") for r in rows]
        self.assertTrue(all(len(c) == 3 for c in cols))
        self.assertEqual([float(c[0]) for c in cols], sorted(float(c[0]) for c in cols))   # time order
        self.assertEqual(cols[0], ["2461340.600000", "1.001000", "0.002000"])
        text.encode("ascii")
        info = ca.exoclock_info_text("TOI-2046 b", meta, "ExoClock_TOI-2046b_20261027.txt")
        for want in ("Planet: TOI-2046b", "Time format: JD_UTC", "Time stamp: Mid-exposure", "Exposure start",
                     "Flux format: Flux", "Filter: Johnson V", "Exposure time (seconds): 60.0",
                     "https://www.exoclock.space/upload/"):
            self.assertIn(want, info)
        info.encode("ascii")


class TestNotesCheck(unittest.TestCase):
    def test_foreign_names(self):
        known = ["DY Peg", "000-BCR-111", "TYC 4006-956-1"]
        self.assertEqual(ca.notes_foreign_stars("OSC reduced; not transformed", known), [])
        self.assertEqual(ca.notes_foreign_stars("defocused for HD 219134; OSC reduced", known), ["HD 219134"])
        self.assertEqual(ca.notes_foreign_stars("check TYC 4006-956-1, comp 000-BCR-111", known), [])
        self.assertEqual(ca.notes_foreign_stars("comp 000-BPL-128", known), ["000-BPL-128"])
        self.assertEqual(ca.notes_foreign_stars("tyc4006-956-1 fine", known), [])        # spacing/case
        self.assertEqual(ca.notes_foreign_stars("BL Cam flared", known, previous="BL Cam"), ["BL Cam"])
        self.assertEqual(ca.notes_foreign_stars("DY Peg ok", known, previous="BL Cam"), [])
        # the current target's planet name is not foreign
        self.assertEqual(ca.notes_foreign_stars("HD 219134 b transit night", ["HD 219134"]), [])
        self.assertEqual(ca.notes_foreign_stars("TOI-2046 b and WASP-12 b", ["TOI-2046"]), ["WASP-12"])


try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestNotesInApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test2220.log")
        cls.app = ca.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def test_new_reduction_resets_notes(self):
        app = self.app
        app.star_id.set("HD 219134")
        app.notes.set("defocused +125 steps for HD 219134")
        with mock.patch.object(ca.messagebox, "askyesno", return_value=True):
            self.assertTrue(app.new_reduction(ask=True))
        self.assertEqual(app.notes.get(), app._default_note())
        self.assertIn(app.notes.get(), (ca.OSC_NOTE, ca.MONO_NOTE))
        self.assertEqual(app._previous_target, "HD 219134")
        # the automatic path (another star found from the lights/planet) too
        app.star_id.set("BL Cam")
        app.notes.set("BL Cam notes")
        self.assertTrue(app._switch_star("the lights' FITS OBJECT is DY Peg, not BL Cam"))
        self.assertEqual(app.notes.get(), app._default_note())

    def test_notes_question(self):
        app = self.app
        app._previous_target = "HD 219134"
        app.star_id.set("DY Peg")
        app.notes.set("OSC reduced; not transformed; HD 219134 defocused")
        with mock.patch.object(ca.messagebox, "askyesno", return_value=False) as ask:
            self.assertFalse(app._notes_check())
            msg = ask.call_args[0][1]
        self.assertIn("The notes name HD 219134, which is not in this reduction.", msg)
        self.assertIn("Save anyway?", msg)
        with mock.patch.object(ca.messagebox, "askyesno", return_value=True):
            self.assertTrue(app._notes_check())
        app.notes.set("OSC reduced; DY Peg near maximum")
        with mock.patch.object(ca.messagebox, "askyesno") as ask:
            self.assertTrue(app._notes_check())
            ask.assert_not_called()

    def test_exoclock_save(self):
        app = self.app
        tp = app.transit_page
        folder = tempfile.mkdtemp()
        jd = np.linspace(2461340.60, 2461340.75, 30)
        ra, dec, lat, lon = 348.32, 57.17, 35.37, -81.96
        app.ra_hours.set(f"{ra / 15:.6f}")
        app.dec_deg.set(f"{dec:.5f}")
        app.lat.set(str(lat))
        app.lon.set(str(lon))
        app.output_dir.set(folder)
        flux = 1.0 + 0.001 * np.sin(np.arange(30))
        tp.result = {"t": core.convert_times(jd, "BJD_TDB", ra, dec, lat, lon), "flux": flux,
                     "err": np.full(30, 0.002), "err_scaled": np.full(30, 0.003), "summary": {},
                     "flagged": np.arange(30) >= 28,
                     "planet": {"name": "TOI-2046 b"}}
        tp.meta_for_files = {"filter": "TG", "exposure": "60.0", "obscode": "JABA"}
        with mock.patch.object(tp, "_file_meta", return_value=dict(tp.meta_for_files, version="2.2.20")), \
                mock.patch.object(tp, "_confirm_export", return_value=True):
            path = app.save_exoclock()
        self.assertEqual(os.path.basename(path), "ExoClock_TOI-2046b_20261027.txt")
        self.assertTrue(os.path.exists(os.path.join(folder, "ExoClock_info.txt")))
        with open(path) as fh:
            rows = [ln.split() for ln in fh if ln.strip() and not ln.startswith("#")]
        self.assertEqual(len(rows), 28)   # 2.2.20 review: put-back flagged frames are left out
        self.assertLess(abs(float(rows[0][0]) - jd[0]) * 86400, 0.1)
        self.assertAlmostEqual(float(rows[0][2]), 0.002, places=6)   # measurement error, not the scaled one
        self.assertTrue(app.status.get().startswith("✔ Saved. On the form, pick Mid-exposure"))
        with mock.patch.object(ca.App, "open_link", return_value=True) as ol:
            app.open_exoclock()
            self.assertEqual(ol.call_args[0][0], "https://www.exoclock.space/upload/")
        tp.result = None
        app.output_dir.set("")


if __name__ == "__main__":
    unittest.main()
