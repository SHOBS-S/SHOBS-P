"""2.3.0 builder D: #50 (a comp drifting off the frame edge no longer throws away frames; skipped frames listed; Use
comps offers to measure frames skipped because of a comp no longer in use) and item 10 (a ~180° field turn in one
frame gap on an alt-az night is called a meridian flip; the settings file's 'measured:' comp list is accurate).

Synthetic MONO frames with no plate solution and no RA/Dec in the headers (only EXPTIME and DATE-OBS)."""
import math
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["SHOBS_ONSKY_OFF"] = "1"

import numpy as np  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")

import photometry_core as core  # noqa: E402

H, W = 160, 200
RADIUS, SKY_IN, SKY_OUT = 5.0, 8.0, 13.0
_rng = np.random.default_rng(5)
# background stars, kept clear of the named stars' places and of the top 45 px (where the drifting stars go)
FIELD = [(float(x), float(y), float(f)) for x, y, f in zip(_rng.uniform(15, W - 15, 60), _rng.uniform(50, H - 12, 60),
                                                          _rng.uniform(15000, 60000, 60))]
# a sparser field over a larger frame (no drift), as a real field is, for the rotation tests
ROT_SHAPE = (450, 600)
FULL_FIELD = [(float(x), float(y), float(f)) for x, y, f in zip(_rng.uniform(15, ROT_SHAPE[1] - 15, 110),
                                                               _rng.uniform(15, ROT_SHAPE[0] - 15, 110),
                                                               _rng.uniform(15000, 60000, 110))]


def _obs(f, insts, target, night="N", kmag=None):
    return core.Obs(jd=2460950.6 + f * 0.002, mag=0.0, merr=0.01, cmag=0.0, kmag=kmag, airmass=None, path=f"f{f:03d}",
                    exptime=10.0, flux_var=1.0, flux_comp=1.0, comp_insts=list(insts), target_inst=target,
                    comp_vars=[1e-6] * len(insts), target_var=1e-6, night=night)


def write_night(folder, stars, n=40, drift=(0.0, -1.0), rotate_from=None, names=None, background=None,
                shape=(H, W), turn_per_frame=0.0):
    """Mono FITS frames: stars [(name, x, y, flux)] plus a field of background stars, the whole field moving by `drift`
    px per frame (up the frame by default). rotate_from: frames from that index on are turned by 180° (a meridian
    flip), named as a restarted sequence. Returns the paths."""
    from astropy.io import fits
    from astropy.time import Time
    rng = np.random.default_rng(11)
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    os.makedirs(folder, exist_ok=True)
    paths = []
    t0 = Time("2026-10-02T02:00:00", scale="utc").jd
    for f in range(n):
        dx, dy = drift[0] * f, drift[1] * f
        img = np.full((h, w), 300.0)
        ext = 10 ** (-0.4 * 0.004 * f)   # a little extinction: every star fades together
        field = [s for s in (FIELD if background is None else background) if all(math.hypot(s[0] - x, s[1] - y) > 3 * SKY_OUT for _n, x, y, _f in stars)]
        ang = math.radians(turn_per_frame * f)   # field rotation about the frame's center (alt-az, no derotator)
        ca_, sa_ = math.cos(ang), math.sin(ang)
        for _name, x, y, flux in list(stars) + [(None, *s) for s in field]:
            bx, by = x - (w - 1) / 2.0, y - (h - 1) / 2.0
            sx = ca_ * bx - sa_ * by + (w - 1) / 2.0 + dx
            sy = sa_ * bx + ca_ * by + (h - 1) / 2.0 + dy
            img += ext * flux / (2 * math.pi * 1.8 ** 2) * np.exp(-((xx - sx) ** 2 + (yy - sy) ** 2) / (2 * 1.8 ** 2))
        img = img + rng.normal(0, np.sqrt(img))
        if rotate_from is not None and f >= rotate_from:
            img = img[::-1, ::-1]
        hdr = fits.Header()
        hdr["EXPTIME"] = 10.0
        hdr["DATE-OBS"] = Time(t0 + f * 0.002 - 5.0 / 86400.0, format="jd", scale="utc").isot
        if names is not None:
            name = names(f)
        else:
            name = f"Light_{f + 1:04d}.fits"
        path = os.path.join(folder, name)
        fits.PrimaryHDU(np.clip(img, 0, 65535).astype(np.uint16), header=hdr).writeto(path, overwrite=True)
        paths.append(path)
    return paths


class TestEnsembleNoStep(unittest.TestCase):
    """night_ensemble / recompute_with_comps: a comp that leaves part way through the night leaves no step."""

    def test_comp_leaves_mid_night_no_step(self):
        rng = np.random.default_rng(3)
        pts = []
        for f in range(200):
            ext = 0.3 * f / 200
            c = [-9.0 + ext + rng.normal(0, 0.002), -8.5 + ext + rng.normal(0, 0.002), -7.6 + ext + rng.normal(0, 0.002)]
            if f >= 120:
                c[2] = float("nan")   # off the edge from frame 120 on
            pts.append(_obs(f, c, -9.2 + ext + rng.normal(0, 0.002), kmag=-8.0 + ext))
        cat = {"A": 10.0, "B": 10.5, "C": 11.4}
        r = core.recompute_with_comps(pts, ["A", "B", "C"], ["A", "B", "C"], cat)
        self.assertEqual(r["missing"], 0)
        self.assertEqual(r["partial"], 80)
        m = np.array([o.mag for o in pts])
        self.assertLess(abs(np.median(m[120:]) - np.median(m[:120])), 0.002)
        # the ensemble (cmag) follows the extinction smoothly across frame 120: no jump beyond noise
        cm = np.array([o.cmag for o in pts])
        self.assertLess(abs((cm[120] - cm[119]) - 0.3 / 200), 0.01)
        self.assertEqual(pts[150].n_comps, 2)
        self.assertEqual(pts[50].n_comps, 3)
        # the check star's reduced value has no step either
        k = np.array([o.check_std for o in pts])
        self.assertLess(abs(np.median(k[120:]) - np.median(k[:120])), 0.002)

    def test_full_frames_unchanged(self):
        pts = [_obs(f, [-9.0, -8.5], -9.2) for f in range(20)]
        core.recompute_with_comps(pts, ["A", "B"], ["A", "B"], {"A": 10.0, "B": 10.5})
        self.assertAlmostEqual(pts[0].mag, 10.25 + (-9.2) - (-8.75), places=9)

    def test_comp_missing_most_of_night_out_of_night(self):
        pts = [_obs(f, [-9.0, -8.5 if f < 6 else float("nan")], -9.2) for f in range(40)]
        r = core.recompute_with_comps(pts, ["A", "B"], ["A", "B"], {"A": 10.0, "B": 10.5})
        self.assertEqual(r["night_out"], {"N": ["B"]})
        self.assertAlmostEqual(pts[0].mag, 10.0 + (-9.2) - (-9.0), places=9)   # A alone, all night
        self.assertAlmostEqual(pts[30].mag, pts[0].mag, places=9)

    def test_star_never_measured_still_nocomp(self):
        pts = [_obs(f, [-9.0], -9.2) for f in range(10)]
        r = core.recompute_with_comps(pts, ["A", "Z"], ["A"], {"A": 10.0, "Z": 11.0})
        self.assertEqual(r["missing"], 10)   # another field's comp: as before #50

    def test_clip_reason(self):
        self.assertEqual(core.aperture_clip_reason((100, 100), 50, 50, 5, 8, 13), "")
        self.assertEqual(core.aperture_clip_reason((100, 100), 50, 4, 5, 8, 13), "off the frame edge")
        self.assertEqual(core.aperture_clip_reason((100, 100), 50, -20, 5, 8, 13), "off the frame edge")
        self.assertEqual(core.aperture_clip_reason((100, 100), 6.5, 6.5, 5, 8, 13), "sky ring mostly off the frame edge")
        self.assertEqual(core.aperture_clip_reason((100, 100), 50, 7, 5, 8, 13), "")   # most of the ring on

    def test_obs_round_trip_keeps_drops(self):
        o = _obs(0, [-9.0, float("nan")], -9.2)
        o.comp_drop, o.check_lost = {"B": "off the frame edge"}, "off the frame edge"
        back = core.obs_from_dict(core.obs_to_dict(o))
        self.assertEqual(back.comp_drop, {"B": "off the frame edge"})
        self.assertEqual(back.check_lost, "off the frame edge")


class TestFlipLikeTurns(unittest.TestCase):
    def test_half_turn_in_one_gap(self):
        angles = [0.2 * k for k in range(30)] + [180.0 + 0.2 * k for k in range(30, 60)]
        labs = [f"{k + 1}" for k in range(30)] + [f"{k - 29}b" for k in range(30, 60)]
        jd = [2460000.5 + k * 0.001 for k in range(60)]
        got = core.flip_like_turns(angles, labs, jd)
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["before"], got[0]["after"]), ("30", "1b"))
        self.assertAlmostEqual(got[0]["turn"], 179.8, places=1)

    def test_smooth_rotation_no_warning(self):
        # 2°/frame near the zenith, wrapping through ±180°, and one long gap: all explained by the night's own rate
        angles = [((-170.0 + 2.0 * k + 180) % 360) - 180 for k in range(120)]
        jd = [2460000.5 + k * 0.001 for k in range(120)]
        self.assertEqual(core.flip_like_turns(angles, [str(k) for k in range(120)], jd), [])
        angles2 = [0.5 * k for k in range(40)] + [0.5 * 40 + 160.0 + 0.5 * k for k in range(40)]
        jd2 = [2460000.5 + k * 0.001 for k in range(40)] + [2460000.5 + 0.04 + 0.32 + k * 0.001 for k in range(40)]
        self.assertEqual(core.flip_like_turns(angles2, [str(k) for k in range(80)], jd2), [])   # 8 h gap


def _have_tk():
    try:
        import tkinter as tk
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


HAVE_TK = _have_tk()


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestRunInApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        ca.LOG_FILE = os.path.join(tempfile.gettempdir(), "shobs_p_test230d.log")
        cls.app = ca.App()
        cls.app.withdraw()
        cls.patches = [mock.patch.object(ca.messagebox, n, return_value=True)
                       for n in ("showwarning", "showinfo", "showerror", "askyesno")]
        cls.boxes = {n: p.start() for n, p in zip(("showwarning", "showinfo", "showerror", "askyesno"), cls.patches)}

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.app.destroy()

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for box in self.boxes.values():
            box.reset_mock()

    def tearDown(self):
        app = self.app
        app.observations = []
        app.active_comps = []
        app.comps_used = []
        app.night_bin = {}
        app._comps_under = {}
        app.spare_catalog = {}
        app.spare_marks = []
        app.series_path = ""
        app.series_mode = ""
        app.mount_choice.set(core.mount_label(core.MOUNT_GEM))
        shutil.rmtree(self.dir, ignore_errors=True)

    def _setup(self, paths, target, comps, check=None, spares=()):
        app = self.app
        app.mode_key = "variables"
        app.light_dir.set(os.path.dirname(paths[0]))
        app._split_cache = None
        app.rejected.clear()
        app.masters, app.masters_binning, app.precalibrated = core.Masters(), None, False
        app.pattern.set(core.MONO)
        app.binning.set(1)
        app.radius.set(RADIUS)
        app.sky_in.set(SKY_IN)
        app.sky_out.set(SKY_OUT)
        app.lat.set("")
        app.lon.set("")
        app.ra_hours.set("")
        app.dec_deg.set("")
        app.measure_color.set(False)
        raw, head = core.read_fits(paths[0])
        app.preview = core.calibrate_frame(raw, head, app.masters)
        app.preview_path, app.preview_is_debayered, app.chart_placed = paths[0], True, []
        app.star_id.set(target[0])
        app.target_xy = (target[1], target[2])
        for r in self.ca.COMP_ROLES:
            getattr(app, f"{r}_name").set("")
            getattr(app, f"{r}_mag").set("")
            setattr(app, f"{r}_xy", None)
        for r, (name, x, y, mag) in zip(self.ca.COMP_ROLES, comps):
            getattr(app, f"{r}_name").set(name)
            getattr(app, f"{r}_mag").set(f"{mag:.3f}")
            setattr(app, f"{r}_xy", (x, y))
        if check is not None:
            app.check_name.set(check[0])
            app.check_mag.set(f"{check[3]:.3f}")
            app.check_xy = (check[1], check[2])
        else:
            app.check_name.set("")
            app.check_mag.set("")
            app.check_xy = None
        app._comps_under = {name: {"xy": (x, y), "mag": f"{mag:.3f}", "mag_value": mag, "bands": None}
                            for name, x, y, mag in spares}
        app.spare_catalog = {name: {"mag": mag} for name, _x, _y, mag in spares}

    def _run(self, **kw):
        app = self.app
        n0 = len(app.observations)
        app.run_photometry(**kw)
        end = time.time() + 120
        while time.time() < end:
            app.update()
            if not app.busy:
                break
            time.sleep(0.02)
        for _ in range(5):
            app.update()
        self.assertFalse(app.busy, "run did not finish")
        return len(app.observations) - n0

    def test_comp_off_top_edge_frames_kept(self):
        # C3 starts 30 px under the top edge and drifts off (1 px/frame); so do the check star and a spare (#34)
        stars = [("T", 100, 110, 50000), ("C1", 50, 100, 60000), ("C2", 150, 130, 40000), ("C3", 75, 30, 90000),
                 ("K", 140, 26, 45000), ("SP", 110, 22, 50000)]
        paths = write_night(os.path.join(self.dir, "lights"), stars)
        self._setup(paths, ("T", 100, 110), [("C1", 50, 100, 10.0), ("C2", 150, 130, 10.4), ("C3", 75, 30, 9.5)],
                    check=("K", 140, 26, 10.2), spares=[("SP", 110, 22, 10.1)])
        added = self._run()
        app = self.app
        self.assertEqual(added, len(paths))   # every frame kept (2.2.21 skipped every frame without C3)
        night = app.observations[0].night
        info = app._night_info(night)
        self.assertNotIn("skipped", info)
        drops = info["comp_drops"]
        self.assertIn("C3", drops)
        self.assertIn("check", drops)
        self.assertNotIn("SP", drops)   # a spare off the edge is simply not measured there
        n_c3 = len(drops["C3"]["frames"])
        self.assertGreater(n_c3, 10)
        self.assertLess(n_c3, len(paths) // 2 + 1)
        obs = sorted(app.observations, key=lambda o: o.jd)
        self.assertTrue(all(math.isfinite(o.mag) and "nocomp" not in o.flag for o in obs))
        has = [not o.comp_drop for o in obs]
        k = has.index(False)
        self.assertTrue(all(has[:k]) and not any(has[k:]))   # once off, it stays off
        mags = np.array([o.mag for o in obs])
        step = float(np.median(mags[k:k + 8]) - np.median(mags[k - 8:k]))
        noise = float(np.std(np.diff(mags))) / math.sqrt(2)
        self.assertLess(abs(step), max(4 * noise, 0.003), (step, noise))
        flux = [o.flux_comp for o in obs]
        self.assertLess(abs(flux[k] / flux[k - 1] - 1), 0.05)   # comp light (cloud check) does not drop at k
        lost_k = [o for o in obs if o.check_lost]
        self.assertTrue(lost_k and all(o.kmag is None and o.check_std is None for o in lost_k))
        self.assertTrue(all(o.check_std is not None for o in obs if not o.check_lost))
        self.assertTrue(all("SP" not in o.spare_insts for o in obs[k + 10:]))
        # on screen: the Photometry page's line, and the settings file's comp list
        text = app.comp_skip_text(night)
        self.assertIn("C3 left out of", text)
        self.assertIn("those frames use the other comps", text)
        self.assertIn("check star left out of", text)
        app._update_skip_line()
        app.update()
        self.assertIn("C3 left out of", app.skip_text.get())
        self.assertTrue(app.skip_line.winfo_ismapped() or not app.winfo_ismapped())
        measured = app.measured_comps_text(night)
        self.assertTrue(measured.startswith("C1, C2, C3 (missing in"), measured)
        self.assertIn("SP (as spare", measured)
        self.assertNotIn("check", measured)
        self.boxes["showwarning"].assert_called()   # said on screen after the run
        comps_row = next(x for x in self._settings_lines(night) if x.startswith("Comps: "))
        self.assertIn("measured: C1, C2, C3 (missing in", comps_row)
        self.assertIn("SP (as spare", comps_row)
        # Use comps without C3: 'measured:' still names what was measured, not the series' list
        self.assertTrue(app.apply_comp_selection(["C1", "C2"], confirmed=True))
        comps_row = next(x for x in self._settings_lines(night) if x.startswith("Comps: "))
        self.assertTrue(comps_row.startswith("Comps: C1, C2 (Use comps choice; measured: C1, C2, C3 (missing in"),
                        comps_row)
        self.assertNotIn("no longer a comp", app.comp_skip_text(night))   # nothing was skipped because of C3

    def _settings_lines(self, night):
        tp = self.app.transit_page
        obs = sorted([o for o in self.app.observations if o.night == night], key=lambda o: o.jd)
        meta = {"binning": "1x1"}
        tp._repro_meta(meta, obs, night, [o.jd for o in obs])
        return tp.settings_lines(meta, {})

    def test_only_comp_off_edge_use_comps_then_measure_skipped(self):
        # one comp, which drifts off the top: those frames are skipped (no comp left); a spare stays on
        stars = [("T", 100, 110, 50000), ("C1", 60, 30, 60000), ("SP", 150, 120, 55000)]
        paths = write_night(os.path.join(self.dir, "lights"), stars)
        self._setup(paths, ("T", 100, 110), [("C1", 60, 30, 10.0)], spares=[("SP", 150, 120, 10.1)])
        added = self._run()
        app = self.app
        night = app.observations[0].night
        skipped = app._night_info(night)["skipped"]
        self.assertEqual(added + len(skipped), len(paths))
        self.assertGreater(len(skipped), 5)
        self.assertTrue(all(s["comps"] == ["C1"] and not s["target"] for s in skipped))
        self.assertTrue(all("every comp was lost" in s["why"] for s in skipped))
        self.assertIn(f"⚠ {len(skipped)} frames skipped", app.comp_skip_text(night))
        self.assertNotIn("no longer a comp", app.comp_skip_text(night))
        # the settings file lists the skipped frames and why (2.2.21: "Frames left out: none" and only the log knew)
        row = next(x for x in self._settings_lines(night) if x.startswith("Frames skipped at photometry"))
        self.assertIn("every comp lost (off the frame edge", row)
        self.assertIn(str(skipped[0]["frame"]), row)
        # Use comps takes C1 out: the line says so, with the button
        self.assertTrue(app.apply_comp_selection(["SP"], confirmed=True))
        app.update()
        text = app.comp_skip_text(night)
        self.assertIn(f"{len(skipped)} frames were skipped because of C1, which is no longer a comp — Run photometry "
                      "again to measure them", text)
        self.assertIn("no longer a comp", app.skip_text.get())
        self.assertTrue(app.skip_button.winfo_manager())
        # Measure skipped frames: just those frames, into the night
        app.measure_skipped_frames()
        end = time.time() + 120
        while app.busy and time.time() < end:
            app.update()
            time.sleep(0.02)
        for _ in range(5):
            app.update()
        self.assertEqual(len(app.observations), len(paths))
        self.assertNotIn("skipped", app._night_info(night))
        self.assertTrue(all(math.isfinite(o.mag) and "nocomp" not in o.flag for o in app.observations))
        mags = np.array([o.mag for o in sorted(app.observations, key=lambda o: o.jd)])
        self.assertLess(float(np.std(mags)), 0.02)   # one level, measured against SP throughout
        self.assertFalse(app.skip_button.winfo_manager())
        self.assertEqual(app.active_comps, ["SP"])
        self.assertEqual(app.comps_used[:1], ["C1"])

    def test_altaz_half_turn_warns(self):
        stars = [("T", 100, 90, 50000), ("C1", 50, 80, 60000), ("C2", 150, 100, 40000)]
        # ASIAIR-style restart: frames after the flip are numbered from 1 again ("…_b_0001")
        paths = write_night(os.path.join(self.dir, "lights"), stars, n=24, drift=(0.0, 0.0), rotate_from=12,
                            background=FULL_FIELD, shape=ROT_SHAPE,
                            names=lambda f: f"Light_a_{f + 1:04d}.fits" if f < 12 else f"Light_b_{f - 11:04d}.fits")
        self._setup(paths, ("T", 100, 90), [("C1", 50, 80, 10.0), ("C2", 150, 100, 10.4)])
        self.app.mount_choice.set(core.mount_label(core.MOUNT_ALTAZ))
        self._run()
        app = self.app
        night = app.observations[0].night
        flips = app._night_info(night).get("flip_like") or []
        self.assertEqual(len(flips), 1, flips)
        self.assertGreater(flips[0]["turn"], 170)
        app._update_skip_line()
        text = app.skip_text.get()
        self.assertIn("that looks like a meridian flip. Is the Mount set right (German equatorial)?", text)
        self.assertIn(f"between frames {flips[0]['before']} and {flips[0]['after']}", text)
        warned = " ".join(str(c) for c in self.boxes["showwarning"].call_args_list)
        self.assertIn("looks like a meridian flip", warned)

    def test_altaz_smooth_rotation_no_warning(self):
        stars = [("T", 300, 225, 50000), ("C1", 260, 210, 60000), ("C2", 345, 240, 40000)]
        paths = write_night(os.path.join(self.dir, "lights"), stars, n=16, drift=(0.0, 0.0), background=FULL_FIELD,
                            shape=ROT_SHAPE, turn_per_frame=0.8)   # 12° through the night, smoothly
        self._setup(paths, ("T", 300, 225), [("C1", 260, 210, 10.0), ("C2", 345, 240, 10.4)])
        self.app.mount_choice.set(core.mount_label(core.MOUNT_ALTAZ))
        self._run()
        night = self.app.observations[0].night
        self.assertEqual(len(self.app.observations), len(paths))
        self.assertNotIn("flip_like", self.app._night_info(night))
        self.assertNotIn("meridian flip", self.app.skip_text.get())


if __name__ == "__main__":
    unittest.main()
