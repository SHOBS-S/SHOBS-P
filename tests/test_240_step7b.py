"""2.4.0 step 7b (#92, #115): the promotion gates, the Registry file, the Campaign switch, the SHOBS-SCHED export,
the suspects file name from the field folder, and the normalise-then-cap excess chi2nu."""
import json
import math
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photometry_core as core  # noqa: E402
import promotion as pr  # noqa: E402
import registry as reg  # noqa: E402

S033_SERIES = os.environ.get("SHOBS_S033_SERIES", "")


def night_times(start_jds, hours=5.0, cadence_min=3.0):
    t, nights, seg = [], [], []
    for jd0 in start_jds:
        n = int(hours * 60 / cadence_min)
        tt = jd0 + np.arange(n) * cadence_min / 1440.0
        t.extend(tt)
        nights.extend([f"JD{int(jd0)}"] * n)
        seg.extend(["" if k < n // 2 else "flip" for k in range(n)])
    return np.array(t), nights, seg


def curve(name, t, nights, seg, amp=0.0, period=0.1027, noise=0.004, seed=0, step=0.0, role=""):
    rng = np.random.default_rng(seed)
    y = 12.0 + amp * np.sin(2 * np.pi * t / period) + rng.normal(0, noise, t.size)
    y = y + np.array([step if s == "flip" else 0.0 for s in seg])
    return pr.make_curve(name, t, y, nights, seg, role=role, airmass=1.0 + 0.2 * np.cos(2 * np.pi * (t % 1.0)),
                         x=np.full(t.size, 500.0), y=np.full(t.size, 400.0), peak=np.full(t.size, 9000.0))


SIX = [2461353.6, 2461354.6, 2461355.6, 2461357.6, 2461358.6, 2461359.6]


class TestGates(unittest.TestCase):
    def setUp(self):
        self.t, self.n, self.s = night_times(SIX)
        self.nulls = [curve(f"C{k}", self.t, self.n, self.s, seed=10 + k, role="comp") for k in range(4)]
        self.check = curve("K", self.t, self.n, self.s, seed=99, role="check")

    def report(self, star, **kw):
        kw.setdefault("linear_limit", 40000)
        kw.setdefault("frame_shape", (2088, 3124))
        kw.setdefault("edge_margin_px", 43)
        return pr.gate_report(star, self.nulls + [self.check], self.check, min_period=0.02, max_period=20, **kw)

    def gate(self, rep, key):
        return next(g for g in rep["gates"] if g["key"] == key)

    def test_s033_like_star_is_a_candidate_short_of_campaign(self):
        rep = self.report(curve("S033", self.t, self.n, self.s, amp=0.0104, noise=0.0076, seed=1))
        self.assertAlmostEqual(rep["period_days"], 0.1027, delta=0.001)
        self.assertTrue(rep["candidate"], rep["summary"])
        self.assertFalse(rep["campaign"])
        self.assertFalse(self.gate(rep, "volume")["pass"])   # 6 nights, 6 d < 14 d
        self.assertTrue(self.gate(rep, "nulls")["pass"])
        self.assertTrue(self.gate(rep, "phase")["pass"])
        self.assertTrue(self.gate(rep, "linear")["pass"])
        self.assertGreater(rep["amp_snr_white"], 15)

    def test_constant_star_is_not_a_candidate(self):
        rep = self.report(curve("flat", self.t, self.n, self.s, amp=0.0, seed=2))
        self.assertFalse(rep["candidate"])
        self.assertIn("fap", rep["failed"])

    def test_signal_in_a_comp_fails_the_null_test(self):
        star = curve("S", self.t, self.n, self.s, amp=0.01, seed=3)
        self.nulls[0] = curve("C0", self.t, self.n, self.s, amp=0.01, seed=13, role="comp")
        rep = self.report(star)
        g = self.gate(rep, "nulls")
        self.assertFalse(g["pass"])
        self.assertEqual(g["why"], "comp_signal")
        self.assertEqual(pr.rejection_code(rep), "comp_signal")

    def test_span_gate_needs_a_consecutive_pair(self):
        t, n, s = night_times([2461353.6, 2461356.6, 2461359.6, 2461362.6])
        star = curve("S", t, n, s, amp=0.01, seed=4)
        rep = pr.gate_report(star, [], None, min_period=0.02, max_period=20)
        g = self.gate(rep, "span")
        self.assertFalse(g["pass"])
        self.assertIn("consecutive", g["why"])

    def test_span_gate_needs_three_nights(self):
        t, n, s = night_times([2461353.6, 2461354.6])
        rep = pr.gate_report(curve("S", t, n, s, amp=0.01, seed=5), [], None, min_period=0.02, max_period=20)
        self.assertFalse(self.gate(rep, "span")["pass"])
        self.assertFalse(rep["candidate"])

    def test_flip_step_that_moves_the_period_fails_the_flip_test(self):
        """A 20 mmag step at each night's flip: per-night offsets find another period than per-side offsets, so the
        result rests on the flip (rejection code flip); per side the true period comes back."""
        rep = self.report(curve("S", self.t, self.n, self.s, amp=0.01, seed=6, step=0.02))
        self.assertFalse(self.gate(rep, "flip")["pass"])
        self.assertAlmostEqual(rep["flip"]["period"], 0.1027, delta=0.001)

    def test_small_flip_step_passes(self):
        rep = self.report(curve("S", self.t, self.n, self.s, amp=0.01, seed=6, step=0.002))
        self.assertTrue(self.gate(rep, "flip")["pass"], self.gate(rep, "flip")["value"])

    def test_saturated_star_fails_the_linear_limit(self):
        star = curve("S", self.t, self.n, self.s, amp=0.01, seed=7)
        star.peak[:] = 45000.0
        rep = self.report(star)
        self.assertFalse(self.gate(rep, "linear")["pass"])

    def test_edge_guard(self):
        star = curve("S", self.t, self.n, self.s, amp=0.01, seed=8)
        star.x[:] = 20.0
        self.assertFalse(self.gate(self.report(star), "edge")["pass"])

    def test_bad_check_night_is_left_out(self):
        k = self.check
        sel = np.asarray(k.night) == "JD2461355"
        k.mag[sel] += np.random.default_rng(5).normal(0, 0.03, sel.sum())
        rep = self.report(curve("S", self.t, self.n, self.s, amp=0.01, seed=9))
        self.assertEqual(rep["dropped_nights"], ["JD2461355"])
        self.assertNotIn("JD2461355", rep["nights"])
        chip = next(c for c in rep["quiet_nights"] if c["night"] == "JD2461355")
        self.assertEqual(chip["state"], "skipped")

    def test_quiet_nights_strip(self):
        star = curve("S", self.t, self.n, self.s, amp=0.0, seed=11)
        sel = np.asarray(star.night) == "JD2461357"
        star.mag[sel] += 0.02 * np.sin(2 * np.pi * star.jd[sel] / 0.1)
        chips = {c["night"]: c for c in pr.quiet_nights(star)}
        self.assertEqual(chips["JD2461357"]["state"], "active")
        self.assertEqual(chips["JD2461353"]["state"], "quiet")
        self.assertEqual(chips["JD2461353"]["basis"], "point-to-point scatter")

    def test_outside_data_clears_alias_and_span_but_not_nulls(self):
        star = curve("S", self.t, self.n, self.s, amp=0.0104, noise=0.0076, seed=1)
        self.nulls[0] = curve("C0", self.t, self.n, self.s, amp=0.01, seed=13, role="comp")
        rep = self.report(star, outside={"ZTF": {"period": 0.1027}})
        self.assertTrue(self.gate(rep, "volume")["pass"])
        self.assertEqual(self.gate(rep, "volume")["cleared_by"], "ZTF")
        self.assertFalse(self.gate(rep, "nulls")["pass"])
        self.assertFalse(rep["campaign"])

    def test_bonferroni(self):
        self.assertEqual(pr.bonferroni_fap(1e-4, 20), 2e-3)
        self.assertEqual(pr.bonferroni_fap(0.2, 10), 1.0)

    def test_baluev_keeps_tiny_values(self):
        t = np.linspace(0, 6, 600)
        f = pr.fap_baluev(0.5, 594, t, 0.02, 0.3)
        self.assertGreater(f, 0.0)
        self.assertLess(f, 1e-50)

    def test_compact_report_is_json(self):
        rep = self.report(curve("S", self.t, self.n, self.s, amp=0.01, seed=12))
        json.dumps(pr.compact_report(rep))

    def test_frame_shape_from_header(self):
        self.assertEqual(pr.frame_shape_from_header({"NAXIS1": 6248, "NAXIS2": 4176, "BAYERPAT": "RGGB"}),
                         (2088, 3124))
        self.assertEqual(pr.frame_shape_from_header({"NAXIS1": 2000, "NAXIS2": 1000}, 2), (500, 1000))
        self.assertIsNone(pr.frame_shape_from_header({}))


@unittest.skipUnless(S033_SERIES and os.path.isfile(S033_SERIES), "S033 series not available")
class TestS033Series(unittest.TestCase):
    def test_walk_through(self):
        meta, obs = core.load_series(S033_SERIES)
        spares = sorted({n for o in obs for n in o.spare_insts})
        rep = pr.series_gate_report(obs, meta["star_id"], is_target=True, target_name=meta["star_id"],
                                    comp_names=meta["comps_used"], check_name=meta["check_name"],
                                    watch_names=[w["name"] for w in meta["watch_list"]], spare_names=spares,
                                    min_period=meta["min_period"], max_period=meta["max_period"],
                                    linear_limit=40000, frame_shape=(2088, 3124), edge_margin_px=46)
        self.assertAlmostEqual(rep["period_days"], 0.10271, delta=0.0002)
        self.assertTrue(rep["candidate"])
        self.assertFalse(rep["campaign"])
        failed = {g["key"] for g in rep["gates"] if g["pass"] is not True}
        self.assertEqual(failed, {"volume", "alias_pm1"})


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, reg.REGISTRY_NAME)
        t, n, s = night_times(SIX)
        self.rep = pr.gate_report(curve("S", t, n, s, amp=0.0104, noise=0.0076, seed=1),
                                  [curve("C", t, n, s, seed=2)], None, min_period=0.02, max_period=20)

    def tearDown(self):
        self.tmp.cleanup()

    def test_gaia_id(self):
        self.assertEqual(reg.gaia_source_id("Gaia DR3 440531844915790208"), "440531844915790208")
        self.assertEqual(reg.gaia_source_id("Gaia DR2 440531844915790208"), "")
        self.assertEqual(reg.star_key("", 42.05, 50.88), "pos:42.0500,+50.8800")

    def test_candidate_enters_and_round_trips(self):
        doc = reg.load(self.path)
        rec, ev = reg.upsert_from_report(doc, self.rep, "S033", "Gaia DR3 440531844915790208", 42.05, 50.88,
                                         series_file="x/S.cvseries", parent_target="GSC 03308-00462")
        self.assertEqual(ev, "new")
        self.assertEqual(rec["status"], "candidate")
        self.assertFalse(rec["campaign"])
        self.assertEqual(rec["key"], "440531844915790208")
        reg.save(doc, self.path)
        again = reg.load(self.path)
        self.assertIn("440531844915790208", again["stars"])
        self.assertIsNotNone(reg.find(again, "", 42.05001, 50.88001))

    def test_not_candidate_is_not_added(self):
        doc = reg.load(self.path)
        self.rep["candidate"] = False
        rec, ev = reg.upsert_from_report(doc, self.rep, "X", "", 10.0, 10.0)
        self.assertIsNone(rec)
        self.assertEqual(ev, "")
        self.assertEqual(doc["stars"], {})

    def test_campaign_switch_and_demotion(self):
        doc = reg.empty()
        rec, _ = reg.upsert_from_report(doc, self.rep, "S", "", 1.0, 2.0)
        self.assertFalse(reg.campaign_ready(rec))     # data volume and alias not yet
        reg.set_status(rec, "confirmed_new", note="VSX")
        self.assertTrue(reg.campaign_ready(rec))      # decision 11: any time once Confirmed
        reg.set_campaign(rec, True)
        self.assertEqual(len(reg.sched_targets(doc)), 1)
        bad = json.loads(json.dumps(pr.compact_report(self.rep)))
        for g in bad["gates"]:
            if g["key"] == "nulls":
                g["pass"] = False
        reg.set_status(rec, "candidate")
        rec2, ev = reg.upsert_from_report(doc, bad, "S", "", 1.0, 2.0)
        self.assertEqual(ev, "campaign_fail")
        self.assertFalse(rec2["campaign"])
        self.assertEqual(rec2["status"], "candidate")
        rec3, ev = reg.upsert_from_report(doc, bad, "S", "", 1.0, 2.0)
        self.assertEqual(ev, "rejected")
        self.assertEqual(rec3["status"], "rejected")
        self.assertEqual(rec3["rejection"]["code"], "comp_signal")
        self.assertEqual(reg.sched_targets(doc), [])

    def test_outside_clearance_is_kept(self):
        doc = reg.empty()
        rec, _ = reg.upsert_from_report(doc, self.rep, "S", "", 1.0, 2.0)
        for g in rec["evidence"]["gates"]:
            if g["key"] == "alias_pm1":
                g["pass"], g["cleared_by"] = True, "ZTF and TESS"
        rec, _ = reg.upsert_from_report(doc, self.rep, "S", "", 1.0, 2.0)
        g = next(g for g in rec["evidence"]["gates"] if g["key"] == "alias_pm1")
        self.assertTrue(g["pass"])
        self.assertEqual(g["cleared_by"], "ZTF and TESS")

    def test_unreadable_registry_is_never_replaced(self):
        with open(self.path, "w") as fh:
            fh.write("{broken")
        with self.assertRaises(ValueError):
            reg.load(self.path)

    def test_sched_export(self):
        doc = reg.empty()
        rec, _ = reg.upsert_from_report(doc, self.rep, "S033", "", 42.05, 50.88)
        reg.set_campaign(rec, True)
        out = os.path.join(self.tmp.name, "x.json")
        self.assertEqual(reg.write_sched_export(doc, out, "2.4.0"), 1)
        data = json.load(open(out))
        self.assertEqual(data["kind"], reg.SCHED_EXPORT_KIND)
        row = data["targets"][0]
        self.assertEqual((row["kind"], row["mode"]), ("campaign", "V"))
        self.assertAlmostEqual(row["elements"]["period"], self.rep["period_days"], places=5)

    def test_save_keeps_previous(self):
        doc = reg.empty()
        reg.save(doc, self.path)
        reg.save(doc, self.path)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "campaign_registry_prev.json")))


class TestSuspectsName(unittest.TestCase):
    def test_field_folder(self):
        self.assertEqual(core.suspects_field_name("W:/Variables/GSC 03308-00462/13Nov26/Output", "x"),
                         "GSC 03308-00462")
        self.assertEqual(core.suspects_field_name("", "HD 1"), "HD 1")
        self.assertEqual(core.suspects_field_name("", ""), "field")


class TestNormaliseThenCap(unittest.TestCase):
    def test_bright_variable_no_longer_compressed(self):
        """#115: errors that read far low on the bright stars (no floor) no longer compress a bright variable."""
        rng = np.random.default_rng(3)
        n_f, n_s = 90, 300
        mags = np.linspace(8, 16, n_s)
        err = 0.002 * 10 ** (0.2 * (mags - 8))
        floor = np.where(mags < 10.5, 0.008, 0.0)          # unmodelled floor on the bright stars
        true = np.sqrt(err ** 2 + floor ** 2)
        inst = mags[None, :] + rng.normal(0, 1, (n_f, n_s)) * true[None, :]
        j = 10                                              # a bright star with a small sine
        inst[:, j] += 2.0 * true[j] * np.sin(np.arange(n_f) / 6.0)
        merr = np.repeat(err[None, :], n_f, axis=0)
        st = core.field_variability(inst, merr=merr)
        self.assertGreaterEqual(st["excess_chi2"][j], core.SUSPECT_EXCESS_CHI2)
        quiet = np.delete(np.arange(n_s), j)
        self.assertLess(np.nanmedian(st["excess_chi2"][quiet]), 1.3)

    def test_widening_never_narrows(self):
        corr = np.zeros((10, 6))
        w = core.chi2_widening(corr, np.zeros(6), np.ones((10, 6)), np.ones(6, bool))
        self.assertTrue(np.all(w >= 1.0))


if __name__ == "__main__":
    unittest.main()


try:
    import tkinter
    _root = tkinter.Tk()
    _root.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK and S033_SERIES and os.path.isfile(S033_SERIES), "needs a display and the S033 series")
class TestCandidatesWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import shutil
        import clearv_app as ca
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["SHOBS_DATA_HOME"] = cls.tmp.name
        ca.LOG_FILE = os.path.join(cls.tmp.name, "test.log")
        cls.ca = ca
        cls.boxes = []
        for nm in ("showinfo", "showwarning", "showerror"):
            setattr(ca.messagebox, nm, lambda *a, _n=nm, **k: cls.boxes.append((_n, a)))
        for nm in ("askyesno", "askokcancel"):
            setattr(ca.messagebox, nm, lambda *a, **k: False)
        ca.messagebox.askyesnocancel = lambda *a, **k: False
        cls.app = ca.App()
        cls.app.set_mode("variables")
        cls.app.update()
        cls.series = os.path.join(cls.tmp.name, "S033.cvseries")
        shutil.copy(S033_SERIES, cls.series)
        cls.app._load_series_file(cls.series, lambda words: None)
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        os.environ.pop("SHOBS_DATA_HOME", None)
        cls.tmp.cleanup()

    def wait_gates(self):
        import time
        t0 = time.time()
        while time.time() - t0 < 120:
            self.app.update()
            if not getattr(self.app, "_gates_running", False):
                return
            time.sleep(0.05)
        self.fail("gates did not finish")

    def test_window_gates_registry_campaign(self):
        app = self.app
        app.show_candidates()
        self.wait_gates()
        win = app._candidates_win
        self.assertIsNotNone(win)
        if "APASS J024812.2+505255" not in app.gate_reports:
            win.run(with_target=True)
            self.wait_gates()
        rep = app.gate_reports["APASS J024812.2+505255"]
        self.assertTrue(rep["candidate"], rep.get("summary"))
        doc = reg.load()
        rec = reg.find(doc, "", 42.0509, 50.8819, tol_arcsec=30)
        self.assertIsNotNone(rec, list(doc["stars"]))
        self.assertEqual(rec["status"], "candidate")
        # select the target row; Promote is off until every gate passes or it is Confirmed
        for item, name in win.rows.items():
            if name == "APASS J024812.2+505255":
                win.tree.selection_set(item)
        app.update()
        win.show_star()
        self.assertIn("disabled", win.promote_btn.state())
        self.assertGreater(len(win.gates.get_children()), 10)
        reg.set_status(rec, "confirmed_new", note="VSX 15 Nov")
        reg.save(doc)
        win.refresh()
        self.assertNotIn("disabled", win.promote_btn.state())
        win.promote()
        rec = reg.find(reg.load(), "", 42.0509, 50.8819, tol_arcsec=30)
        self.assertTrue(rec["campaign"])
        out = os.path.join(self.tmp.name, "for_sched.json")
        app.output_path = lambda *a, **k: out
        win.export_sched()
        self.assertEqual(len(json.load(open(out))["targets"]), 1)
        win.end_campaign()
        self.assertFalse(reg.find(reg.load(), "", 42.0509, 50.8819, tol_arcsec=30)["campaign"])
        # the series keeps the last reports in its meta
        meta = app._meta()
        self.assertIn("APASS J024812.2+505255", meta["candidates"])
        json.dumps(meta["candidates"])
        win.close()
