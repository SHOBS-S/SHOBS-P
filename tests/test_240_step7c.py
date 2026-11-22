"""2.4.0 step 7c (#92): Watch reach-back, Check neighbors (look-alikes, close companions, the blend test) and the
outside-data check (ZTF, TESS, ATLAS)."""
import io
import json
import math
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import neighbors as nb  # noqa: E402
import outside_data as od  # noqa: E402
import photometry_core as core  # noqa: E402
import promotion as pr  # noqa: E402

S033_SERIES = os.environ.get("SHOBS_S033_SERIES", "")


# ---- a small synthetic field -------------------------------------------------------------------------------------
RA0, DEC0 = 42.05, 50.88
SCALE = 0.535   # arcsec / px


def sky_of(dx_px, dy_px):
    """Sky position of a pixel offset from (RA0, DEC0) on an image with north up, east left."""
    return RA0 - dx_px * SCALE / 3600.0 / math.cos(math.radians(DEC0)), DEC0 + dy_px * SCALE / 3600.0


STARS = {   # name: (x, y, flux) on the reference image
    "T": (200.0, 200.0, 60000.0), "C1": (120.0, 150.0, 90000.0), "C2": (300.0, 260.0, 80000.0),
    "C3": (260.0, 110.0, 70000.0), "K": (140.0, 300.0, 50000.0), "W": (330.0, 150.0, 40000.0),
    "N": (200.0, 214.0, 30000.0),   # a close companion of T (14 px)
}


def star_image(stars, shape=(400, 420), fwhm=4.0, sky=100.0, rng=None, rot180=False, shift=(0.0, 0.0)):
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.full(shape, sky, float)
    s = fwhm / 2.3548
    for name, (x, y, f) in stars.items():
        if rot180:
            x, y = w - 1 - x, h - 1 - y
        x, y = x + shift[0], y + shift[1]
        img += f / (2 * math.pi * s * s) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * s * s))
    if rng is not None:
        img += rng.normal(0, 3.0, shape)
    return img.astype(np.float32)


def frame_obs(i, stars, rot=False, shift=(0.0, 0.0), w_measured=True):
    h, w = 400, 420

    def p(n):
        x, y, _ = stars[n]
        if rot:
            x, y = w - 1 - x, h - 1 - y
        return [x + shift[0], y + shift[1]]
    m = {n: core.inst_mag(stars[n][2], 1.0) for n in stars}   # stored inst (perfect)
    o = core.Obs(jd=2461353.5 + i * 0.002, mag=12.0, merr=0.004, cmag=float(np.mean([m["C1"], m["C2"], m["C3"]])),
                 kmag=m["K"], airmass=1.1, path=f"frame{i}.fit", exptime=1.0, flux_var=1.0, flux_comp=1.0,
                 night="JD2461353", segment="flip" if rot else "")
    o.target_inst = m["T"]
    o.comp_insts = [m["C1"], m["C2"], m["C3"]]
    o.comp_cat = 12.0
    o.pos = {"t": p("T"), "c": [p("C1"), p("C2"), p("C3")], "k": p("K")}
    if w_measured:
        o.watch_insts = {"W": m["W"]}
        o.pos["w"] = {"W": p("W")}
    return o


def sky_table():
    out = {}
    for n, (x, y, _f) in STARS.items():
        out[n] = sky_of(-(x - 200.0), (y - 200.0))   # x grows to the west here: east left
    return out


class TestGeometry(unittest.TestCase):
    def test_tangent_and_affine_map_back_to_pixels(self):
        sky = sky_table()
        o = frame_obs(0, STARS)
        fm = nb.FrameMap(o, sky, ["C1", "C2", "C3"], "T", "K")
        self.assertTrue(fm.ok)
        self.assertLess(fm.rms, 0.05)
        x, y = fm.xy(*sky["N"])
        self.assertAlmostEqual(x, 200.0, delta=0.1)
        self.assertAlmostEqual(y, 214.0, delta=0.1)
        self.assertAlmostEqual(fm.scale_arcsec_per_px(), SCALE, delta=0.01)

    def test_map_follows_a_meridian_flip(self):
        sky = sky_table()
        o = frame_obs(1, STARS, rot=True, shift=(3.0, -2.0))
        fm = nb.FrameMap(o, sky, ["C1", "C2", "C3"], "T", "K")
        x, y = fm.xy(*sky["W"])
        self.assertAlmostEqual(x, 420 - 1 - 330.0 + 3.0, delta=0.1)
        self.assertAlmostEqual(y, 400 - 1 - 150.0 - 2.0, delta=0.1)

    def test_a_bad_anchor_is_left_out(self):
        sky = sky_table()
        o = frame_obs(0, STARS)
        o.pos["k"] = [o.pos["k"][0] + 40, o.pos["k"][1]]   # a check star misplaced by 40 px
        stars = dict(STARS, C4=(80.0, 80.0, 1.0), C5=(350.0, 350.0, 1.0))
        sky.update({n: sky_of(-(stars[n][0] - 200.0), stars[n][1] - 200.0) for n in ("C4", "C5")})
        o.pos["c"] += [[80.0, 80.0], [350.0, 350.0]]
        fm = nb.FrameMap(o, sky, ["C1", "C2", "C3", "C4", "C5"], "T", "K")
        self.assertNotIn("K", fm.used)
        self.assertLess(fm.rms, 0.1)

    def test_too_few_anchors(self):
        self.assertIsNone(nb.fit_affine([[0, 0], [1, 1]], [[0, 0], [1, 1]]))


class TestMeasuring(unittest.TestCase):
    def setUp(self):
        self.sky = sky_table()
        self.rng = np.random.default_rng(1)

    def _load(self, imgs):
        def load(path):
            return None, {"EXPTIME": 1.0}, imgs[path]
        return load

    def test_reach_back_recovers_the_stored_value_tied_to_the_comps(self):
        # every frame scaled by a transparency factor; the stored values are a different calibration (offset)
        obs, imgs = [], {}
        for i in range(6):
            rot = i >= 3
            o = frame_obs(i, STARS, rot=rot, w_measured=False)
            trans = 1.0 - 0.05 * i
            stars = {n: (x, y, f * trans) for n, (x, y, f) in STARS.items()}
            imgs[o.path] = star_image(stars, rng=self.rng, rot180=rot)
            obs.append(o)
        missing = nb.missing_frames(obs, "W")
        self.assertEqual(missing, list(range(6)))
        star = {"key": "W", "name": "W", "ra": self.sky["W"][0], "dec": self.sky["W"][1]}
        res = nb.measure_series(core, obs, [star], sky=self.sky, comp_names=["C1", "C2", "C3"], target_name="T",
                                check_name="K", load=self._load(imgs), aperture_for=lambda o: (6.0, 12.0, 18.0))
        self.assertEqual(res["frames"], 6)
        n = nb.fill_watch(obs, "W", res["values"]["W"])
        self.assertEqual(n, 6)
        want = core.inst_mag(STARS["W"][2], 1.0)
        for o in obs:
            # the stored scale has no transparency loss and a perfect aperture: the tie puts W on it
            self.assertAlmostEqual(o.watch_insts["W"], want, delta=0.01)
            self.assertIn("W", o.pos["w"])
        self.assertEqual(nb.missing_frames(obs, "W"), [])
        chk = nb.consistency(obs, res["values"]["W"], "W")
        self.assertEqual(chk["n"], 6)
        self.assertLess(abs(chk["median"]), 1e-9)

    def test_the_star_is_never_tied_to_its_own_value(self):
        o = frame_obs(0, STARS, w_measured=True)
        o.watch_insts["W"] += 0.5   # a wrong stored value must not pull the tie
        img = star_image(STARS, rng=self.rng)
        star = {"key": "W", "name": "W", "ra": self.sky["W"][0], "dec": self.sky["W"][1]}
        res = nb.measure_series(core, [o], [star], sky=self.sky, comp_names=["C1", "C2", "C3"], target_name="T",
                                check_name="K", load=self._load({o.path: img}), aperture_for=lambda o: (6.0, 12.0, 18.0))
        self.assertAlmostEqual(res["values"]["W"][0]["inst"], core.inst_mag(STARS["W"][2], 1.0), delta=0.01)

    def test_sample_and_missing(self):
        obs = [frame_obs(i, STARS, w_measured=(i % 2 == 0)) for i in range(10)]
        self.assertEqual(nb.missing_frames(obs, "W"), [1, 3, 5, 7, 9])
        self.assertEqual(len(nb.sample_measured(obs, "W", per_night=3)), 3)

    def test_a_frame_that_cannot_be_read_is_listed(self):
        o = frame_obs(0, STARS)

        def load(path):
            raise OSError("gone")
        res = nb.measure_series(core, [o], [{"key": "W", "ra": self.sky["W"][0], "dec": self.sky["W"][1]}],
                                sky=self.sky, comp_names=["C1", "C2", "C3"], target_name="T", check_name="K",
                                load=load, aperture_for=lambda o: (6.0, 12.0, 18.0))
        self.assertEqual(res["frames"], 0)
        self.assertEqual(len(res["failed"]), 1)


class TestPicksAndBlend(unittest.TestCase):
    def test_lookalikes_nearest_first_and_close_companions(self):
        here = (RA0, DEC0)
        cat = [{"auid": "A", "ra": sky_of(-100, 0)[0], "dec": DEC0, "mag": 13.6},
               {"auid": "B", "ra": sky_of(-50, 0)[0], "dec": DEC0, "mag": 13.3},
               {"auid": "Far", "ra": sky_of(-40, 0)[0], "dec": DEC0, "mag": 15.0},
               {"auid": "Close", "ra": DEC0 and sky_of(0, 0)[0], "dec": DEC0 + 10 / 3600.0, "mag": 16.0},
               {"auid": "Known", "ra": sky_of(-30, 0)[0], "dec": DEC0, "mag": 13.5},
               {"auid": "Self", "ra": RA0, "dec": DEC0, "mag": 13.4}]
        known = {"Known": (sky_of(-30, 0)[0], DEC0)}
        picks = nb.pick_neighbors(*here, 13.4, cat, known, window=0.5, close_arcsec=19.0)
        self.assertEqual([p["name"] for p in picks["lookalikes"]], ["B", "A"])
        self.assertEqual([p["name"] for p in picks["close"]], ["Close"])

    def test_blend_verdict(self):
        t = np.concatenate([2461353.6 + np.arange(80) * 0.0021 + d for d in (0, 1, 2)])
        night = [f"JD{2461353 + int(x - 2461353.1)}" for x in t]
        P = 0.1
        rng = np.random.default_rng(2)

        def c(amp):
            return pr.make_curve("x", t, 12 + amp * np.sin(2 * np.pi * t / P) + rng.normal(0, 0.001, t.size), night,
                                 [""] * t.size)
        star = nb.blend_verdict(pr, c(0.010), c(0.008), c(0.011), P)
        self.assertEqual(star["verdict"], "star")
        comp = nb.blend_verdict(pr, c(0.004), c(0.009), c(0.002), P)
        self.assertEqual(comp["verdict"], "companion")
        none = nb.blend_verdict(pr, None, None, None, P)
        self.assertIsNone(none["verdict"])

    def test_sidecar_round_trip(self):
        obs = [frame_obs(i, STARS) for i in range(3)]
        with tempfile.TemporaryDirectory() as d:
            series = os.path.join(d, "S.cvseries")
            doc = nb.sidecar_load(series)
            nb.sidecar_put(doc, obs, {"name": "A", "ra": 1.0, "dec": 2.0, "mag": 13.5},
                           {0: {"inst": -9.1, "x": 1, "y": 2}, 2: {"inst": float("nan")}}, "cal words")
            path = nb.sidecar_save(series, doc)
            self.assertTrue(path.endswith("S_neighbors.json"))
            back = nb.sidecar_load(series)
            vals = nb.sidecar_values(back, obs, "A")
            self.assertEqual(list(vals), [0])
            self.assertAlmostEqual(vals[0]["inst"], -9.1)
            c = nb.curve_from_values(pr, obs, "A", vals)
            self.assertEqual(c.jd.size, 1)


class TestNeighborCheckIsTheGatesNullTest(unittest.TestCase):
    def test_same_numbers_as_the_gate_runner(self):
        t = np.concatenate([2461353.6 + np.arange(90) * 0.0021 + d for d in (0, 1, 2, 4)])
        night = [f"JD{2461353 + int(x - 2461353.1)}" for x in t]
        rng = np.random.default_rng(3)
        P = 0.1027
        tgt = pr.make_curve("T", t, 13 + 0.01 * np.sin(2 * np.pi * t / P) + rng.normal(0, 0.003, t.size), night,
                            [""] * t.size, role="target")
        quiet = pr.make_curve("Q", t, 12 + rng.normal(0, 0.003, t.size), night, [""] * t.size, role="comp")
        loud = pr.make_curve("L", t, 12 + 0.01 * np.sin(2 * np.pi * t / P) + rng.normal(0, 0.003, t.size), night,
                             [""] * t.size, role="comp")
        rep = pr.gate_report(tgt, [quiet, loud])
        nc = pr.neighbor_check(tgt, [quiet, loud], period=rep["period_days"])
        self.assertEqual([r["quiet"] for r in nc["rows"]], [r["quiet"] for r in rep["nulls"]])
        self.assertEqual([r["power_share"] for r in nc["rows"]], [r["power_share"] for r in rep["nulls"]])
        self.assertEqual(nc["n_quiet"], 1)
        self.assertIn("1 of 2", nc["bottom"])

    def test_too_few_points(self):
        c = pr.make_curve("T", [1, 2, 3], [1, 1, 1], ["a"] * 3, [""] * 3)
        self.assertIn("too few", pr.neighbor_check(c, []).get("bottom"))

    def test_watch_star_in_the_comp_layout_is_a_null_star(self):
        obs = []
        for i in range(60):
            o = frame_obs(i, STARS)
            o.night = f"JD{2461353 + i // 20}"
            o.jd = 2461353.6 + (i // 20) + (i % 20) * 0.005
            o.comp_insts = o.comp_insts + [float("nan")]   # W is a 4th comp, never measured as one
            obs.append(o)
        rep = pr.series_gate_report(obs, "T", is_target=True, target_name="T", comp_names=["C1", "C2", "C3", "W"],
                                    check_name="K", watch_names=["W"])
        self.assertIn("W", [r["name"] for r in rep["nulls"]])


# ---- outside data --------------------------------------------------------------------------------------------------
def ztf_csv(P=0.1029705, n=600, seed=4):
    rng = np.random.default_rng(seed)
    t = 2458200 + np.sort(rng.uniform(0, 2500, n))
    m = 13.7 + 0.01 * np.sin(2 * np.pi * t / P) + rng.normal(0, 0.004, n)
    rows = ["oid,expid,hjd,mjd,mag,magerr,catflags,filtercode,ra,dec"]
    for k, (a, b) in enumerate(zip(t, m)):
        rows.append(f"1,{k},{a:.6f},{a - 2400000.5:.6f},{b:.5f},0.004,{0 if k % 10 else 512},"
                    f"{'zg' if k % 2 else 'zr'},42.05,50.88")
    return "\n".join(rows)


def qlp_fits(P=0.1029705, n=3000, seed=5):
    from astropy.io import fits
    rng = np.random.default_rng(seed)
    t = 3260.0 + np.arange(n) * 200 / 86400.0
    f = 1000 * (1 + 0.01 * np.sin(2 * np.pi * (t + 2457000) / P)) + rng.normal(0, 2, n)
    cols = fits.ColDefs([fits.Column(name="TIME", format="D", array=t),
                         fits.Column(name="KSPSAP_FLUX", format="E", array=f),
                         fits.Column(name="QUALITY", format="J", array=np.zeros(n, int))])
    buf = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(), fits.BinTableHDU.from_columns(cols)]).writeto(buf)
    return buf.getvalue()


class TestOutside(unittest.TestCase):
    def fake_get(self, down=()):
        qlp = qlp_fits()

        def get(url, timeout=60, data=None):
            for d in down:
                if d in url:
                    raise od.ServiceDown("HTTP 503")
            if "irsa" in url:
                return ztf_csv().encode()
            if "tesscut" in url:
                return json.dumps({"results": [{"sector": "0058"}, {"sector": "0085"}]}).encode()
            if "invoke" in url:
                return json.dumps({"data": [{"ID": 428049570, "Tmag": 12.9, "ra": 42.05, "dec": 50.88}]}).encode()
            if "hlsp_qlp" in url:
                return qlp if "s0058" in url else b""
            raise AssertionError(url)
        return get

    @staticmethod
    def vizier(*a, **k):
        return [{"ATOID": "J042.0507+50.8818", "fp-LSper": "0.249402", "fp-period": "", "fp-lngfitper": "41.900433",
                 "Class": "dubious"}]

    def test_s033_like_check(self):
        res = od.check(42.05, 50.88, 0.10279, get=self.fake_get(), vizier=self.vizier)
        self.assertEqual(res["agree"], ["ZTF", "TESS"])
        self.assertEqual(res["surveys"]["ATLAS"]["relation"], "other")
        self.assertIn("dubious", res["surveys"]["ATLAS"]["note"])
        self.assertIn("no QLP light curve for 85", res["surveys"]["TESS"]["detail"])
        self.assertEqual(set(od.for_gates(res)), {"ZTF", "TESS"})
        self.assertIn("ZTF and TESS", res["summary"])

    def test_a_service_down_fails_softly(self):
        res = od.check(42.05, 50.88, 0.10279, get=self.fake_get(down=("irsa", "mast.stsci.edu")), vizier=self.vizier)
        self.assertEqual(res["surveys"]["ZTF"]["status"], "down")
        self.assertEqual(res["surveys"]["TESS"]["status"], "down")
        self.assertEqual(res["agree"], [])
        self.assertEqual(od.for_gates(res), {})

    def test_relation(self):
        self.assertEqual(od.relation(0.10297, 0.10279), "same")
        P = 0.10297
        self.assertEqual(od.relation(1 / (1 / P - 1), P), "alias")
        self.assertEqual(od.relation(2 * P, P), "harmonic")
        self.assertEqual(od.relation(0.3, P), "other")
        self.assertIsNone(od.relation(None, P))

    def test_ztf_parse_keeps_clean_points_only(self):
        bands = od.parse_ztf_csv(ztf_csv(n=100))
        self.assertEqual(set(bands), {"zg", "zr"})
        self.assertEqual(sum(v[0].size for v in bands.values()), 90)
        self.assertEqual(od.parse_ztf_csv(""), {})
        with self.assertRaises(od.BadAnswer):
            od.parse_ztf_csv("no data")

    def test_outside_clears_only_alias_and_span_on_a_record(self):
        from neighbors_ui import apply_outside_to_record
        rec = {"evidence": {"period_days": 0.10279, "gates": [
            {"key": "alias_pm1", "pass": False}, {"key": "volume", "pass": False}, {"key": "nulls", "pass": False}]}}
        res = od.check(42.05, 50.88, 0.10279, get=self.fake_get(), vizier=self.vizier)
        apply_outside_to_record(rec, res)
        g = {x["key"]: x for x in rec["evidence"]["gates"]}
        self.assertTrue(g["alias_pm1"]["pass"])
        self.assertEqual(g["alias_pm1"]["cleared_by"], "ZTF and TESS")
        self.assertTrue(g["volume"]["pass"])
        self.assertFalse(g["nulls"]["pass"])
        self.assertEqual(rec["outside"]["agree"], ["ZTF", "TESS"])


class TestReviewFixes(unittest.TestCase):
    """Fable review of e7429f6 and John's calls of 22 Nov 2026."""

    def test_span_is_its_own_gate_and_only_it_is_cleared(self):
        t = np.concatenate([2461353.6 + np.arange(90) * 0.0021 + d for d in (0, 1)])
        night = [f"JD{2461353 + int(x - 2461353.1)}" for x in t]
        c = pr.make_curve("S", t, 13 + 0.01 * np.sin(2 * np.pi * t / 0.1027), night, [""] * t.size)
        rep = pr.gate_report(c, [], None, outside={"ZTF": {"period": 0.1027}})
        g = {x["key"]: x for x in rep["gates"]}
        self.assertFalse(g["span"]["pass"])               # 2 nights: our own data must show 3
        self.assertEqual(g["span"]["label"], "Nights and cycles")
        self.assertTrue(g["span_days"]["pass"])           # the time span alone may be cleared
        self.assertEqual(g["span_days"]["cleared_by"], "ZTF")
        self.assertFalse(rep["candidate"])
        self.assertEqual(pr.GATES_VERSION, 3)

    def test_dubious_atlas_and_weak_peaks_do_not_count(self):
        dub = od.atlas(42.05, 50.88, 0.249402, query=lambda *a: [{"ATOID": "J1", "fp-LSper": "0.249402",
                                                                   "fp-period": "", "fp-lngfitper": "",
                                                                   "Class": "dubious"}])
        self.assertEqual(dub["relation"], "same")
        self.assertFalse(od.counts(dub))
        weak = {"status": "ok", "period": 0.1, "relation": "same", "fap": 0.2}
        self.assertFalse(od.counts(weak))
        self.assertTrue(od.counts(dict(weak, fap=1e-6)))
        self.assertEqual(od.for_gates({"surveys": {"ATLAS": dub, "ZTF": weak}}), {})

    def test_unreadable_irsa_answer_is_an_error_not_none(self):
        r = od.ztf(42.05, 50.88, 0.1, 0.02, 20, get=lambda *a, **k: b"<html>Service unavailable</html>")
        self.assertEqual(r["status"], "error")

    def test_a_down_survey_keeps_the_earlier_good_answer_and_clears_can_be_undone(self):
        from neighbors_ui import merge_outside, unclear_outside
        good = {"utc": "U1", "surveys": {"ZTF": {"status": "ok", "period": 0.1, "relation": "same", "fap": 1e-9}}}
        down = {"utc": "U2", "surveys": {"ZTF": {"status": "down", "detail": "HTTP 503"}}}
        m = merge_outside(good, down)
        self.assertEqual(m["surveys"]["ZTF"]["status"], "ok")
        self.assertEqual(m["agree"], ["ZTF"])
        alias = {"utc": "U3", "surveys": {"ZTF": {"status": "ok", "period": 0.11, "relation": "alias", "fap": 1e-9}}}
        gates = [{"key": "alias_pm1", "pass": True, "cleared_by": "ZTF"}, {"key": "nulls", "pass": True}]
        unclear_outside(gates, merge_outside(good, alias))
        self.assertFalse(gates[0]["pass"])
        self.assertIsNone(gates[0]["cleared_by"])
        self.assertTrue(gates[1]["pass"])

    def test_three_anchors_are_not_enough_to_write(self):
        sky = sky_table()
        o = frame_obs(0, STARS, w_measured=False)
        o.pos = {"t": o.pos["t"], "c": o.pos["c"][:2]}   # target + 2 comps: an exact 3-star fit
        img = star_image(STARS)
        res = nb.measure_series(core, [o], [{"key": "W", "ra": sky["W"][0], "dec": sky["W"][1]}], sky=sky,
                                comp_names=["C1", "C2"], target_name="T", check_name="", aperture_for=lambda o:
                                (6.0, 12.0, 18.0), load=lambda p: (None, {"EXPTIME": 1.0}, img))
        self.assertEqual(res["frames"], 0)
        self.assertIn("only 3 stars", res["failed"][0][1])

    def test_values_follow_the_frames_not_their_positions(self):
        """Fable M1: the job carries the frames; a list rebuilt meanwhile never shifts a value onto another frame."""
        sky = sky_table()
        obs = [frame_obs(i, STARS, w_measured=False) for i in range(3)]
        img = star_image(STARS)
        res = nb.measure_series(core, obs, [{"key": "W", "name": "W", "ra": sky["W"][0], "dec": sky["W"][1]}],
                                sky=sky, comp_names=["C1", "C2", "C3"], target_name="T", check_name="K",
                                aperture_for=lambda o: (6.0, 12.0, 18.0), load=lambda p: (None, {"EXPTIME": 1.0}, img))
        shorter = obs[1:]   # a night removed while it ran
        live = {id(o) for o in shorter}
        good = {i: v for i, v in res["values"]["W"].items() if id(obs[i]) in live}
        nb.fill_watch(obs, "W", good)
        self.assertNotIn("W", obs[0].watch_insts)
        self.assertIn("W", obs[1].watch_insts)

    def test_kept_answer_is_judged_against_todays_period(self):
        from neighbors_ui import merge_outside
        good = {"utc": "U1", "surveys": {"ZTF": {"status": "ok", "period": 0.2, "relation": "same", "fap": 1e-9}}}
        m = merge_outside(good, {"utc": "U2", "period": 0.31, "surveys": {"ZTF": {"status": "down"}}})
        self.assertEqual(m["agree"], [])
        self.assertEqual(m["surveys"]["ZTF"]["relation"], "other")

    def test_close_companion_cut_follows_the_amplitude(self):
        cat = [{"auid": "F", "ra": RA0, "dec": DEC0 + 10 / 3600.0, "mag": 17.4}]   # 4 mag fainter
        self.assertEqual(nb.pick_neighbors(RA0, DEC0, 13.4, cat, {}, close_arcsec=19.0, star_amp=0.05)["close"], [])
        self.assertEqual(nb.pick_neighbors(RA0, DEC0, 13.4, cat, {}, close_arcsec=19.0,
                                           star_amp=0.05)["close_faint"], 1)
        self.assertEqual(len(nb.pick_neighbors(RA0, DEC0, 13.4, cat, {}, close_arcsec=19.0,
                                               star_amp=0.01)["close"]), 1)

    def test_saved_master_nearest_exposure(self):
        from neighbors_ui import NeighborsMixin
        with tempfile.TemporaryDirectory() as d:
            m = os.path.join(d, core.MASTER_DIR)
            os.makedirs(m)
            for e in (20, 120, 180):
                open(os.path.join(m, f"SHOBS-P_master_dark_bin1_{e}s.fits"), "w").close()
            self.assertTrue(NeighborsMixin._saved_master(d, "dark", 1, 170.0).endswith("_180s.fits"))
            self.assertTrue(NeighborsMixin._saved_master(d, "dark", 1, 30.0).endswith("_20s.fits"))
            self.assertIsNone(NeighborsMixin._saved_master(d, "flat", 1))


# ---- the App ------------------------------------------------------------------------------------------------------
class TestApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import tkinter
            tkinter.Tk().destroy()
        except Exception as exc:
            raise unittest.SkipTest(f"no display: {exc}")
        import clearv_app as ca
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["SHOBS_DATA_HOME"] = cls.tmp.name
        ca.LOG_FILE = os.path.join(cls.tmp.name, "test.log")
        cls.ca = ca
        cls.boxes = []
        for nm in ("showinfo", "showwarning", "showerror"):
            setattr(ca.messagebox, nm, lambda *a, _n=nm, **k: cls.boxes.append((_n, a)))
        cls.app = ca.App()
        cls.app.set_mode("variables")
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        os.environ.pop("SHOBS_DATA_HOME", None)
        cls.tmp.cleanup()

    def test_buttons_are_there(self):
        app = self.app
        app.observations = [frame_obs(i, STARS) for i in range(3)]
        app.watch_list = [{"name": "W", "ra": 1.0, "dec": 2.0}]
        win = self.ca.WatchWindow(app)
        texts = []

        def walk(w):
            for c in w.winfo_children():
                try:
                    texts.append(c.cget("text"))
                except Exception:
                    pass
                walk(c)
        walk(win.win)
        win.win.destroy()
        self.assertIn("Reach back…", texts)
        self.assertIn("Check neighbors…", texts)
        app.observations, app.watch_list = [], []

    def test_watch_revision_marks_the_series_unsaved(self):
        app = self.app
        app.observations = [frame_obs(i, STARS) for i in range(3)]
        app.series_path = os.path.join(self.tmp.name, "x.cvseries")
        app._series_saved_fp = app._series_fingerprint()
        self.assertFalse(app.series_unsaved())
        app._bump_watch_revision()
        self.assertTrue(app.series_unsaved())
        app.observations, app.series_path = [], ""

    def test_night_flat_folder(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "8Nov26", "Lights"))
            os.makedirs(os.path.join(d, "8Nov26", "Flats"))
            light = os.path.join(d, "8Nov26", "Lights", "L_0001.fit")
            self.assertEqual(self.app.night_flat_folder(light, "fallback"), os.path.join(d, "8Nov26", "Flats"))
            other = os.path.join(d, "9Nov26", "Lights", "L_0001.fit")
            self.assertEqual(self.app.night_flat_folder(other, "fallback"), "fallback")

    def test_reach_back_refuses_a_star_without_sky_position(self):
        app = self.app
        app.observations = [frame_obs(i, STARS, w_measured=False) for i in range(3)]
        app.watch_list = [{"name": "W"}]
        self.boxes.clear()
        self.assertFalse(app.reach_back("W"))
        self.assertIn("no sky position", self.boxes[-1][1][1])
        app.observations, app.watch_list = [], []

    def test_neighbors_window_on_a_scan(self):
        rng = np.random.default_rng(7)
        n_fr, n_st = 120, 6
        jd = 2461358.6 + np.arange(n_fr) * 0.0021
        lc = 13 + rng.normal(0, 0.003, (n_fr, n_st))
        lc[:, 0] += 0.012 * np.sin(2 * np.pi * jd / 0.1027)   # the suspect
        xy = [(200, 200), (205, 210), (260, 200), (120, 150), (330, 330), (60, 340)]
        result = {"labels": ["S033", "S040", "S051", "Comp 1", "Check", "S077"], "jd": jd, "light_curves": lc,
                  "mag": np.array([13.43, 15.9, 13.6, 12.6, 12.7, 13.2]), "xy": xy,
                  "usable": [True] * n_st, "saturated": [], "candidates": [0]}
        self.app.sky_out.set(20.0)
        win = self.app.check_neighbors_suspect(result, 0)
        self.app.update()
        names = [r["name"] for r in win.result["rows"]]
        self.assertEqual(names[0], "S040")    # close companion first
        self.assertIn("S051", names)          # a look-alike
        self.assertIn("Comp 1", names)        # comps and check
        self.assertNotIn("S077", names[:2])
        self.assertTrue(all(r["quiet"] for r in win.result["rows"]))
        self.assertIn("Neighbors quiet at P", win.bottom_line.get())
        win.save()
        self.assertIn("neighbor_checks", result)
        win.close()


    def test_scan_rows_get_names_dmag_and_arcsec(self):
        """7c regression nit (22 Nov): scan mode shows the catalog name, ΔV and arcsec like series mode."""
        rng = np.random.default_rng(3)
        n_fr, n_st = 100, 3
        jd = 2461358.6 + np.arange(n_fr) * 0.0021
        lc = 13 + rng.normal(0, 0.003, (n_fr, n_st))
        result = {"labels": ["W APASS J1", "S041", "S042"], "jd": jd, "light_curves": lc,
                  "mag": np.array([13.43, 13.60, 13.30]), "xy": [(200, 200), (260, 200), (300, 120)],
                  "names": ["APASS J1", "APASS J024811.4+505612", ""],
                  "radec": [(42.05, 50.88), (42.05, 50.89), (42.06, 50.88)],
                  "usable": [True] * n_st, "saturated": [], "candidates": [0]}
        self.app.sky_out.set(20.0)
        win = self.app.check_neighbors_suspect(result, 0)
        self.app.update()
        rows = {r["name"]: r for r in win.result["rows"]}
        self.assertEqual(rows["S041"]["display"], "APASS J024811.4+505612 (S041)")
        self.assertAlmostEqual(rows["S041"]["dmag"], 0.17, places=6)
        self.assertAlmostEqual(rows["S041"]["sep"], 36.0, delta=0.5)
        self.assertIsNone(rows["S042"]["display"])   # no catalog name: the scan label alone
        win.close()

@unittest.skipUnless(S033_SERIES and os.path.isfile(S033_SERIES), "S033 series not available (SHOBS_S033_SERIES)")
class TestS033Series(unittest.TestCase):
    def test_neighbors_of_s033_are_quiet(self):
        meta, obs = core.load_series(S033_SERIES)
        tname = meta["star_id"]
        comps = [c for c in meta["comps_used"]]
        watch = [w["name"] for w in meta.get("watch_list") or []]
        rep = pr.series_gate_report(obs, tname, is_target=True, target_name=tname, comp_names=comps,
                                    check_name=meta.get("check_used") or "", watch_names=watch)
        self.assertIn("APASS J024814.5+505307", [r["name"] for r in rep["nulls"]])
        self.assertTrue(all(r["quiet"] for r in rep["nulls"]))


if __name__ == "__main__":
    unittest.main()


class TestCertificateRetry(unittest.TestCase):
    """7c regression (22 Nov 2026): IRSA/MAST failed the Windows certificate check on John's PC."""

    def _err(self):
        import ssl, urllib.error
        return urllib.error.URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] x"))

    def test_retries_with_bundled_roots(self):
        import io as _io
        from unittest import mock
        import outside_data as od
        calls = []

        class R(_io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake(req, timeout=None, context=None):
            calls.append(context)
            if context is None:
                raise self._err()
            return R(b"ok")
        with mock.patch("urllib.request.urlopen", fake), mock.patch.object(od, "_bundled_context", lambda: object()):
            self.assertEqual(od.http_get("https://x"), b"ok")
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0])
        self.assertIsNotNone(calls[1])

    def test_plain_words_when_both_fail(self):
        from unittest import mock
        import outside_data as od

        def fake(req, timeout=None, context=None):
            raise self._err()
        with mock.patch("urllib.request.urlopen", fake), mock.patch.object(od, "_bundled_context", lambda: object()):
            with self.assertRaises(od.ServiceDown) as cm:
                od.http_get("https://x")
        self.assertIn("certificate problem", str(cm.exception))
        self.assertNotIn("_ssl.c", str(cm.exception))

    def test_bundled_context_exists(self):
        import outside_data as od
        self.assertIsNotNone(od._bundled_context())
