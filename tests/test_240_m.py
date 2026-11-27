"""2.4.0 step 4 (#72 star record, #56 the series' V wins, #67 Add-night by position, S033 comps from the slots).

A series' comp and check stars are records: the sky position is the identity (same star within 1 arcsec), every name
is an alias, every V is kept with its source. An older series file converts on Load (backup first, one Log line) and
gives the same numbers. active_comps follows the comp slots. A star is a comp or a Watch star, never both. The series'
magnitude wins over a catalog's; Use new value re-calibrates every night exactly, with a backup. Add-night matches
position-named comps to the series' stars."""
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photometry_core as core    # noqa: E402
import star_record as sr          # noqa: E402

A = "TYC 4006-1008-1"
A_GAIA = "Gaia DR3 2007395519524000128"
B = "Gaia DR3 439074923286442112"
X = "APASS J024814.5+505307"
K = "TYC 4006-832-1"
POS = {A: (348.3001, 57.1702), B: (41.76437, 50.72533), X: (42.0604, 50.8853), K: (348.21, 57.05)}


def _have_tk():
    try:
        import tkinter
        root = tkinter.Tk()
        root.destroy()
        return True
    except Exception:
        return False


def _points(comps, cat, nights=("JD2460950", "JD2460951"), n=6):
    """Synthetic points: mag = mean(catalog of comps) + target_inst - mean(comp_insts)."""
    out = []
    mean_cat = sum(cat[c] for c in comps) / len(comps)
    for ni, night in enumerate(nights):
        for i in range(n):
            jd = float(night[2:]) + 0.6 + 0.01 * i
            t_inst = -9.0 + 0.013 * i + 0.002 * ni
            insts = [-10.0 - 0.3 * k + 0.001 * i for k in range(len(comps))]
            cmag = sum(insts) / len(insts)
            k_inst = -8.5 + 0.0007 * i
            o = core.Obs(jd=jd, mag=mean_cat + t_inst - cmag, merr=0.004 + 0.0001 * i, cmag=cmag, kmag=k_inst,
                         airmass=1.2, path=f"L_{ni}_{i}.fit", exptime=30.0, flux_var=1000.0, flux_comp=2000.0,
                         night=night, filt="TG")
            o.comp_insts = list(insts)
            o.target_inst = t_inst
            o.comp_cat = mean_cat
            o.check_std = mean_cat + k_inst - cmag
            o.n_comps = len(comps)
            o.flag = "sat" if i == 3 else ""
            out.append(o)
    return out


def _old_meta(comps, cat, check=K, check_mag="9.786", extra=None):
    meta = {"star_id": "HD 219134", "mode": "variables", "comps_used": list(comps), "check_used": check,
            "check_name": check, "check_mag": check_mag, "comp_catalog_mags": dict(cat), "active_comps": [],
            "comp_coords": {n: list(POS[n]) for n in list(comps) + [check] if n in POS},
            "site_tz": "America/New_York", "app_version": "2.3.6",
            "star_bands": {"comp": {"name": comps[0], "source": "APASS DR9", "mags": {"V": cat[comps[0]]}}}}
    roles = ["comp", "comp2"] + [f"comp{i}" for i in range(3, 11)]
    for role, name in zip(roles, comps):
        meta[f"{role}_name"] = name
        meta[f"{role}_mag"] = f"{cat[name]:.3f}"
    meta.update(extra or {})
    return meta


def _numbers(observations):
    return [(o.jd, o.mag, o.merr, o.cmag, o.kmag, o.comp_cat, o.check_std, o.flag, tuple(o.comp_insts), o.target_inst)
            for o in observations]


# ---- the record rules (no window) -------------------------------------------------------------------------------
class TestRecordRules(unittest.TestCase):
    def test_step4_names_kept(self):
        self.assertEqual(sr.SAME_STAR_ARCSEC, 1.0)
        self.assertEqual(sr.display_name(["APASS J025525.9-105500", "Gaia DR3 5160920530660588672"]),
                         "Gaia DR3 5160920530660588672")
        self.assertEqual(sr.display_name(["Gaia DR3 1", "TYC 1-2-3", "000-BBB-123"]), "000-BBB-123")

    def test_old_meta_converts_to_one_record_per_star(self):
        cat = {A: 8.728, A_GAIA: 8.704}
        meta = _old_meta([A], cat)
        meta["comp_coords"][A_GAIA] = [POS[A][0], POS[A][1] + 0.1 / 3600.0]   # the same star, 0.1" away
        before = json.dumps(meta, sort_keys=True)
        records, converted = sr.from_meta(meta)
        self.assertTrue(converted)
        self.assertEqual(json.dumps(meta, sort_keys=True), before, "the file's meta is not changed")
        comp = sr.find(records, name=A_GAIA)
        self.assertIs(comp, sr.find(records, name=A))
        self.assertEqual(comp["key"], A, "the name the series' data uses stays the key")
        self.assertEqual(comp["role"], "comp")
        self.assertEqual(comp["v_used"], 8.728)
        self.assertEqual(comp["v_source"], "APASS DR9")
        self.assertEqual(comp["v"], {"APASS DR9": 8.728, "Gaia DR3": 8.704})
        self.assertIsNone(comp["constant"])           # reserved for Gaia DR4
        check = sr.find(records, name=K)
        self.assertEqual((check["role"], check["v_used"]), ("check", 9.786))
        self.assertEqual(sr.mags_view(records), {A: 8.728})
        self.assertEqual(sr.coords_view(records)[A_GAIA], POS[A])

    def test_saved_records_read_back(self):
        records, _ = sr.from_meta(_old_meta([A, B], {A: 8.728, B: 11.3}))
        records[0]["pm"], records[0]["epoch"], records[0]["xy"] = [2075.0, 295.0], 2026.8, [512.5, 300.25]
        meta = {sr.META_KEY: json.loads(json.dumps(sr.to_meta(records)))}
        self.assertEqual(meta[sr.META_KEY]["format"], sr.FORMAT)
        again, converted = sr.from_meta(meta)
        self.assertFalse(converted)
        self.assertEqual(again, records)

    def test_position_is_the_identity(self):
        rec = sr.new_record(B, *POS[B])
        self.assertIs(sr.find([rec], pos=(POS[B][0], POS[B][1] + 0.9 / 3600.0)), rec)
        self.assertIsNone(sr.find([rec], pos=(POS[B][0], POS[B][1] + 1.2 / 3600.0)))
        self.assertIs(sr.find([rec], name="gaia dr3 439074923286442112"), rec)
        # GSC 03308-00462, 10-11 Nov 2026: the comp typed as a position is the series' Gaia star to 0.1"
        typed = sr.position_from_name("02:47:03.45 +50:43:31.2")
        self.assertIs(sr.find([rec], pos=typed), rec)
        self.assertIsNone(sr.position_from_name("TYC 4006-1008-1"))

    def test_fast_star_is_still_matched(self):
        # HD 219134 moves about 2.1"/yr: 26 years after its stored epoch it is 55" away
        rec = sr.new_record("HD 219134", 348.32, 57.168, pm=[2075.0, 295.0], epoch=2000.0)
        now = sr.position_at(rec, 2026.9)
        self.assertGreater(sr.separation_arcsec(*sr.position(rec), *now), 50.0)
        self.assertIsNone(sr.find([rec], pos=now))
        self.assertIs(sr.find([rec], pos=now, year=2026.9), rec)
        self.assertIs(sr.find([rec], pos=sr.position(rec), year=2026.9), rec)

    def test_a_magnitude_never_replaces_the_series_value(self):
        rec = sr.new_record(A, *POS[A], v_used=8.728, v_source="APASS DR9")
        sr.add_v(rec, "Gaia DR3", 8.704)
        self.assertEqual(rec["v_used"], 8.728)
        self.assertEqual(sr.v_words(rec), "V 8.728 (APASS DR9)")
        self.assertEqual(sr.disagreement_text(8.728, "APASS DR9", 8.704, "Gaia DR3"),
                         "Series uses 8.728 (APASS DR9); Gaia DR3 says 8.704")
        self.assertIn("Gaia DR3 8.704", sr.hover_text(rec))
        self.assertFalse(sr.add_name(rec, "tyc 4006-1008-1"))
        self.assertTrue(sr.add_name(rec, A_GAIA))
        self.assertIn(A_GAIA, sr.hover_text(rec))

    def test_backup_never_overwrites(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "HD_219134.cvseries")
            with open(path, "w") as fh:
                fh.write("one")
            first = sr.write_backup(path)
            self.assertEqual(os.path.basename(first), "HD_219134_backup.cvseries")
            with open(path, "w") as fh:
                fh.write("two")
            second = sr.write_backup(path)
            self.assertEqual(os.path.basename(second), "HD_219134_backup2.cvseries")
            self.assertEqual(open(first).read(), "one")
            self.assertEqual(open(second).read(), "two")
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---- in the app ----------------------------------------------------------------------------------------------------
@unittest.skipUnless(_have_tk(), "needs a display")
class AppCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import clearv_app as ca
        cls.ca = ca
        cls.tmp = tempfile.mkdtemp()
        cls.patches = [mock.patch.object(ca, "SETTINGS_FILE", os.path.join(cls.tmp, "s.json")),
                       mock.patch.object(ca.messagebox, "showinfo", lambda *a, **k: None),
                       mock.patch.object(ca.messagebox, "showwarning", lambda *a, **k: None),
                       mock.patch.object(ca.messagebox, "showerror", lambda *a, **k: None),
                       mock.patch.object(ca.messagebox, "askyesno", lambda *a, **k: True)]
        for p in cls.patches:
            p.start()
        ca.LOG_FILE = os.path.join(cls.tmp, "test_240_m.log")
        cls.app = ca.App()
        cls.app.withdraw()
        cls.app._check_planet_transits = lambda *a, **k: None
        cls.real_log = cls.app.log

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        app = self.app
        self.dir = tempfile.mkdtemp(dir=self.tmp)
        self.lines = []
        app.log = lambda text, *a, **k: self.lines.append(str(text))
        app.observations = []
        app._clear_series_state()
        app.clear_target(quiet=True)
        app.watch_list, app.watch_xy = [], {}
        app.chart_solution, app.chart_placed = None, []
        app.settings["series_dir"] = self.dir

    def tearDown(self):
        self.app.log = self.real_log

    def write(self, name, meta, observations):
        path = os.path.join(self.dir, name)
        core.save_series(path, meta, observations)
        return path

    def load(self, path):
        self.app.observations = []
        self.app._clear_series_state()
        self.app.clear_target(quiet=True)
        self.lines.clear()
        self.app._load_series_file(path, lambda words: None)
        self.app.update()

    def said(self, words):
        return [ln for ln in self.lines if words in ln]


class TestOldSeriesConverts(AppCase):
    def test_backup_one_line_same_numbers(self):
        cat = {A: 8.728}
        pts = _points([A], cat)
        path = self.write("HD_219134.cvseries", _old_meta([A], cat), pts)
        original = open(path, "rb").read()
        want = _numbers(core.load_series(path)[1])
        self.load(path)
        app = self.app
        backup = os.path.join(self.dir, "HD_219134_backup.cvseries")
        self.assertEqual(open(backup, "rb").read(), original, "the backup is the file as it was")
        self.assertEqual(open(path, "rb").read(), original, "Load does not write the file")
        said = self.said("older star format")
        self.assertEqual(len(said), 1, self.lines)
        self.assertEqual(said[0], "HD_219134.cvseries is in the older star format: it is converted to the new one "
                                  "when the series is next saved (the original is kept as "
                                  "HD_219134_backup.cvseries).")   # 2.4.0 cleanup (J)
        self.assertEqual(_numbers(app.observations), want)
        self.assertEqual(app.comp_name.get(), A)
        self.assertEqual(app.comp_mag.get(), "8.728")
        self.assertEqual(app.comp_catalog_mags, {A: 8.728})
        self.assertEqual(app.comp_coords[A], POS[A])
        rec = app._series_star_for(A)
        self.assertEqual((rec["key"], rec["v_used"], rec["v_source"]), (A, 8.728, "APASS DR9"))
        self.assertEqual(len(self.said("Comps loaded: C1 TYC 4006-1008-1 V 8.728 (APASS DR9); check")), 1)

        # the next save writes the records AND the keys older code reads; the points are the same numbers
        app._write_series(path)
        saved = json.load(open(path, encoding="utf-8"))["meta"]
        self.assertEqual(saved["stars"]["format"], 1)
        self.assertEqual([r["key"] for r in saved["stars"]["records"]], [A, K])
        self.assertEqual(saved["comp_catalog_mags"], {A: 8.728})
        self.assertEqual(saved["comp_coords"][A], list(POS[A]))
        self.assertEqual(saved["comps_used"], [A])
        self.assertEqual(saved["comp_name"], A)
        self.assertEqual(_numbers(core.load_series(path)[1]), want)
        self.assertEqual(open(backup, "rb").read(), original)

        # a file in the new format is not converted or backed up again
        self.load(path)
        self.assertEqual(self.said("older star format"), [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "HD_219134_backup2.cvseries")))
        self.assertEqual(_numbers(app.observations), want)
        self.assertEqual(app.comp_mag.get(), "8.728")

    def test_two_names_of_one_star_merge(self):
        # #56: comp_catalog_mags held the TYC name and the Gaia DR3 id of one star
        cat = {A: 8.728, A_GAIA: 8.704}
        meta = _old_meta([A], cat)
        meta["comp_coords"][A_GAIA] = [POS[A][0], POS[A][1] + 0.2 / 3600.0]
        path = self.write("two.cvseries", meta, _points([A], cat))
        want = _numbers(core.load_series(path)[1])
        self.load(path)
        self.assertEqual(self.app.comp_catalog_mags, {A: 8.728}, "one star, one magnitude: the series' name's")
        rec = self.app._series_star_for(A_GAIA)
        self.assertEqual(rec["key"], A)
        self.assertEqual(rec["v"], {"APASS DR9": 8.728, "Gaia DR3": 8.704})
        self.assertEqual(_numbers(self.app.observations), want)
        self.assertIn("Gaia DR3 8.704", self.app._slot_hover("comp"))


class TestCompsFromSlots(AppCase):
    def _file(self, slots):
        cat = {A: 8.728, B: 11.31, X: 11.9}
        meta = _old_meta([A, B, X], cat, extra={"active_comps": [A, B, X]})
        meta["comps_used"] = [A, B, X, "TYC 1-1-1"]
        meta["comp_catalog_mags"]["TYC 1-1-1"] = 10.0
        for role in ("comp", "comp2", "comp3"):
            meta[f"{role}_name"] = meta[f"{role}_mag"] = ""
        for role, name in zip(("comp", "comp2", "comp3"), slots):
            meta[f"{role}_name"], meta[f"{role}_mag"] = name, f"{cat[name]:.3f}"
        return self.write("S033.cvseries", meta, _points([A, B, X], cat))

    def test_stale_active_comp_is_asked_about(self):
        # S033, 15 Nov 2026: the comp slot was saved empty but active_comps still listed the star. Step 4 review
        # (M1): Load never trims the choice silently; it asks (tests/test_240_p.py has both answers). The answer
        # here is Keep it (what Escape gives; Enter is Leave it out since the second review): the series stays as
        # saved and the Log says so.
        with mock.patch.object(self.ca, "ask_choice", lambda *a, **k: "keep"):
            self.load(self._file([A, B]))
        app = self.app
        self.assertEqual(app.active_comps, [A, B, X])
        line = self.said("Comps loaded:")
        self.assertEqual(len(line), 1)
        self.assertIn(f"C1 {A}", line[0])
        self.assertIn(f"C2 {B}", line[0])
        self.assertIn(f"{X} is still in the series' comps in use", line[0])

    def test_cleared_comp_stays_cleared(self):
        path = self._file([A, B, X])
        self.load(path)
        app = self.app
        self.assertEqual(app.comp3_name.get(), X)
        app._user_clear_mark("comp3")
        app._write_series(path)
        saved = json.load(open(path, encoding="utf-8"))["meta"]
        self.assertEqual(saved["active_comps"], [A, B])
        self.assertEqual(saved["comp3_name"], "")
        # second review: Save asks (tests/test_240_q.py); the answer here is the default, Leave it out
        self.assertEqual(len(self.said(f"{X} left out of the series' comps in use")), 1)
        self.assertEqual(len(self.said("Comps in use saved from the comp slots")), 0)
        self.load(path)
        self.assertEqual(app.active_comps, [A, B])
        self.assertEqual([app.comp_name.get(), app.comp2_name.get(), app.comp3_name.get()], [A, B, ""])

    def test_a_drop_the_user_did_not_make_keeps_the_choice(self):
        path = self._file([A, B, X])
        self.load(path)
        self.app.clear_mark("comp3", redraw=False)      # e.g. not on tonight's frame: not the user's choice
        self.app._write_series(path)
        self.assertEqual(json.load(open(path, encoding="utf-8"))["meta"]["active_comps"], [A, B, X])


class TestCompOrWatch(AppCase):
    def test_a_comp_is_not_added_to_watch(self):
        app = self.app
        app.comp_name.set(B)
        app.comp_xy = (300.0, 200.0)
        app.comp_coords[B] = POS[B]
        app.add_watch_at((300.5, 200.2))                                  # the same spot
        app._add_watch_sky("02:47:03.45 +50:43:31.2", *sr.position_from_name("02:47:03.45 +50:43:31.2"), "")
        app._add_watch_sky(B, 10.0, 10.0, "")                             # the same name
        self.assertEqual(app.watch_list, [])
        self.assertEqual(len(self.said("can't be both a comp and a Watch star")), 3)
        app.add_watch_at((600.0, 600.0))                                  # another star is fine
        self.assertEqual(len(app.watch_list), 1)

    def test_a_watch_star_is_not_made_a_comp(self):
        app = self.app
        app.watch_list = [{"name": X, "ra": POS[X][0], "dec": POS[X][1]}]
        app.watch_xy = {X: (410.0, 120.0)}
        app.comp2_name.set(X)
        app.comp2_xy = (410.4, 120.3)
        self.assertTrue(app._say_watch_clash("comp2"))
        self.assertEqual((app.comp2_name.get(), app.comp2_xy), ("", None))
        self.assertEqual(len(self.said("was not added as Comp 2")), 1)
        app.comp2_name.set(B)
        app.comp2_xy = (300.0, 200.0)
        self.assertFalse(app._say_watch_clash("comp2"))
        app.check_name.set(X)                                             # the rule is for comps
        app.check_xy = (410.0, 120.0)
        self.assertFalse(app._say_watch_clash("check"))


class TestSeriesVWins(AppCase):
    def _load(self, comps=(A,), cat=None):
        cat = cat or {A: 8.728, B: 11.31}
        self.path = self.write("HD_219134.cvseries", _old_meta(list(comps), cat), _points(list(comps), cat))
        self.load(self.path)
        return cat

    def test_a_catalog_value_does_not_replace_the_series_value(self):
        self._load()
        app = self.app
        app.star_bands["comp"] = {"name": A, "source": "Gaia DR3", "mags": {"V": 8.704}}
        app.comp_mag.set("8.704")          # what a star click or a catalog lookup does
        app.update_star_panel()
        self.assertEqual(app.comp_mag.get(), "8.728")
        self.assertEqual(app._series_v_note("comp"),
                         "Series uses 8.728 (APASS DR9); Gaia DR3 says 8.704 (Series magnitudes… below)")
        self.assertIn("Series uses 8.728 (APASS DR9); Gaia DR3 says 8.704", app.star_status["comp"].get() + " "
                      + " ".join(self.lines))
        self.assertEqual(len(self.said("Series uses 8.728 (APASS DR9); Gaia DR3 says 8.704")), 1)
        app.update_star_panel()
        self.assertEqual(len(self.said("Series uses 8.728")), 1, "said once")
        self.assertTrue(app.keep_series_v("comp"))
        self.assertEqual(app._series_v_note("comp"), "")
        self.assertEqual(app.comp_mag.get(), "8.728")
        self.assertEqual(app.comp_catalog_mags[A], 8.728)

    def test_the_check_too_and_an_impossible_value_is_not_offered(self):
        self._load()
        app = self.app
        app.star_bands["check"] = {"name": K, "source": "APASS DR9", "mags": {"V": 0.622}}
        app.check_mag.set("0.622")         # #64: the damaged APASS copy
        app.update_star_panel()
        self.assertEqual(app.check_mag.get(), "9.786")
        self.assertEqual(app._series_v_note("check"), "")
        app.check_mag.set("9.830")
        app.update_star_panel()
        self.assertEqual(app.check_mag.get(), "9.786")
        self.assertIn("Series uses 9.786", app._series_v_note("check"))

    def test_a_star_the_series_does_not_use_is_left_alone(self):
        self._load()
        app = self.app
        app.comp2_name.set("TYC 9-9-9")
        app.comp2_mag.set("10.111")
        app.update_star_panel()
        self.assertEqual(app.comp2_mag.get(), "10.111")
        self.assertEqual(app._v_disagree, {})

    def test_use_new_value_recalibrates_every_night_exactly(self):
        self._load()
        app = self.app
        before = _numbers(app.observations)
        original = open(self.path, "rb").read()
        app.star_bands["comp"] = {"name": A, "source": "Gaia DR3", "mags": {"V": 8.704}}
        app.comp_mag.set("8.704")
        app.update_star_panel()
        self.assertTrue(app.use_new_series_v("comp"))
        d = 8.704 - 8.728
        after = _numbers(app.observations)
        self.assertEqual(len(after), len(before))
        for old, new in zip(before, after):
            self.assertAlmostEqual(new[1], old[1] + d, places=12)      # mag = comp_cat - 2.5 log(flux ratio)
            self.assertAlmostEqual(new[5], old[5] + d, places=12)      # comp_cat
            self.assertAlmostEqual(new[6], old[6] + d, places=12)      # check_std = comp_cat + kmag - cmag
            self.assertEqual((new[0], new[2], new[3], new[4], new[7], new[8], new[9]),
                             (old[0], old[2], old[3], old[4], old[7], old[8], old[9]))
        # by hand, from the stored instrumental magnitudes
        o = app.observations[4]
        self.assertAlmostEqual(o.mag, 8.704 + o.target_inst - o.cmag, places=12)
        self.assertAlmostEqual(o.check_std, 8.704 + o.kmag - o.cmag, places=12)
        self.assertEqual(app.comp_mag.get(), "8.704")
        self.assertEqual(app.comp_catalog_mags[A], 8.704)
        rec = app._series_star_for(A)
        self.assertEqual((rec["v_used"], rec["v_source"]), (8.704, "Gaia DR3"))
        self.assertEqual(rec["v"]["APASS DR9"], 8.728, "the old value is kept with its source")
        # the backup of the series as it was (the conversion backup already took the first name)
        backup = os.path.join(self.dir, "HD_219134_backup2.cvseries")
        self.assertEqual(open(backup, "rb").read(), original)
        said = self.said("Series re-calibrated")
        self.assertEqual(len(said), 1)
        self.assertIn("8.704 (Gaia DR3) instead of 8.728 (APASS DR9) on every night", said[0])
        self.assertIn("HD_219134_backup2.cvseries", said[0])
        saved_meta, saved_obs = core.load_series(self.path)
        self.assertEqual(_numbers(saved_obs), after)
        self.assertEqual(saved_meta["comp_catalog_mags"][A], 8.704)
        self.assertEqual(saved_meta["stars"]["records"][0]["v_source"], "Gaia DR3")
        app.update_star_panel()
        self.assertEqual(app._v_disagree, {})

    def test_use_new_value_in_an_ensemble(self):
        cat = self._load(comps=(A, B))
        app = self.app
        before = _numbers(app.observations)
        app.comp2_mag.set("11.350")
        app.update_star_panel()
        self.assertEqual(app.comp2_mag.get(), "11.310")
        self.assertTrue(app.use_new_series_v("comp2"))
        d = (11.350 - cat[B]) / 2.0          # the ensemble is the mean of the two comps' catalog magnitudes
        for old, new in zip(before, _numbers(app.observations)):
            self.assertAlmostEqual(new[1], old[1] + d, places=12)
            self.assertAlmostEqual(new[6], old[6] + d, places=12)
            self.assertEqual(new[2], old[2])
        o = app.observations[0]
        self.assertAlmostEqual(o.mag, (8.728 + 11.350) / 2.0 + o.target_inst - o.cmag, places=12)

    def test_no_backup_no_change(self):
        self._load()
        app = self.app
        before = _numbers(app.observations)
        app.comp_mag.set("8.704")
        app.update_star_panel()
        with mock.patch.object(sr, "write_backup", side_effect=OSError("disk full")):
            self.assertFalse(app.use_new_series_v("comp"))
        self.assertEqual(_numbers(app.observations), before)
        self.assertEqual(app.comp_catalog_mags[A], 8.728)

    def test_output_and_settings_say_value_and_source(self):
        self._load()
        app = self.app
        self.assertEqual(app.series_v_words(), [f"{A} V 8.728 (APASS DR9)", f"check {K} V 9.786 (from the series file)"])
        self.assertEqual(app._series_v_lines(), ["Mags    V 8.728 (APASS DR9),", "        check V 9.786 (from the series file)"])
        text = "\n".join(app.variables_settings_lines(app.observations))
        self.assertIn(f"Series magnitudes (value and source): {A} V 8.728 (APASS DR9); check {K} V 9.786", text)


class TestAddNightByPosition(AppCase):
    def test_position_named_comps_are_the_series_stars(self):
        # #67: GSC 03308-00462, 10-11 Nov 2026. Gaia was down; comp 2 and the check were typed as positions.
        check = "Gaia DR3 439068910332260352"
        check_pos = sr.position_from_name("02:46:51.61 +50:41:14.8")
        cat = {A: 10.2, B: 11.31}
        meta = _old_meta([A, B], cat, check=check, check_mag="11.159")
        meta["comp_coords"][B] = [POS[B][0] + 0.05 / 3600.0, POS[B][1] + 0.08 / 3600.0]
        meta["comp_coords"][check] = [check_pos[0], check_pos[1] - 0.1 / 3600.0]
        self.load(self.write("GSC.cvseries", meta, _points([A, B], cat)))
        app = self.app
        typed_b, typed_k = "02:47:03.45 +50:43:31.2", "02:46:51.61 +50:41:14.8"
        prev = (list(app.comps_used), app.check_used)
        app.comps_used, app.check_used = [A, typed_b], typed_k           # tonight's run
        app.comp_catalog_mags[typed_b] = 11.31
        app.check_mag.set("11.159")
        plan = app._same_star_plan(*prev)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["renamed"], {typed_b: B, typed_k: check})
        self.assertEqual(plan["comps"], [A, B])                           # so no "different stars" warning
        self.assertEqual(plan["check"], check)
        self.assertEqual(plan["remag"], {})

    def test_a_star_two_arcsec_away_is_another_star(self):
        cat = {A: 10.2, B: 11.31}
        self.load(self.write("GSC2.cvseries", _old_meta([A, B], cat), _points([A, B], cat)))
        app = self.app
        self.assertIsNone(app._series_star_at(POS[B][0], POS[B][1] + 2.0 / 3600.0))
        self.assertEqual(app._series_star_at(POS[B][0], POS[B][1] + 0.5 / 3600.0)["key"], B)
        self.assertEqual(app._series_key("gaia dr3 439074923286442112"), B)


class TestStableName(AppCase):
    APASS = "APASS J025525.9-105500"
    GAIA = "Gaia DR3 5160920530660588672"

    def _star(self, first, others, xy=(462.5, 204.3)):
        star = {"auid": first, "aka": list(others), "ra": 43.858, "dec": -10.9167, "mag": 12.345, "band": "V",
                "mags": {"V": 12.345}, "catalog": "APASS DR9", "seq": None, "label": "12.3",
                "cat_mags": {"APASS DR9": {"V": 12.345}, "Gaia DR3": {"V": 12.301}}}
        self.app.chart_placed = [(xy[0], xy[1], star)]
        return star

    def _click(self, name, xy=(462.5, 204.3)):
        app = self.app
        app.comp_name.set(name)
        app.comp_xy = xy
        app.comp_coords[name] = (43.858, -10.9167)
        app.star_bands["comp"] = {"name": name, "source": "APASS DR9", "mags": {"V": 12.345}}
        return app._settle_slot_name("comp")

    def test_new_star_same_name_whichever_catalog_answered_first(self):
        # WASP-50 b, 18 Nov 2026: comps typed by their Gaia names were saved under their APASS names
        self._star(self.APASS, [self.GAIA])
        self.assertEqual(self._click(self.APASS), self.GAIA)
        self.assertEqual(self.app.comp_name.get(), self.GAIA)
        self.assertEqual(self.app.comp_coords[self.GAIA], (43.858, -10.9167))
        self.assertEqual(self.app.star_bands["comp"]["name"], self.GAIA)
        self.app.clear_mark("comp", redraw=False)
        self._star(self.GAIA, [self.APASS])
        self.assertEqual(self._click(self.GAIA), self.GAIA)
        self.app.clear_mark("comp", redraw=False)
        self._star(self.APASS, [self.GAIA, "TYC 5290-12-1"])
        self.assertEqual(self._click(self.APASS), "TYC 5290-12-1")
        self.app.clear_mark("comp", redraw=False)
        self._star(self.APASS, [])                      # Gaia did not answer tonight, the star is new
        self.assertEqual(self._click(self.APASS), self.APASS)

    def test_a_series_star_keeps_the_series_name(self):
        cat = {self.APASS: 12.345}
        meta = _old_meta([self.APASS], cat)
        meta["comp_coords"] = {self.APASS: [43.858, -10.9167]}
        path = self.write("WASP-50.cvseries", meta, _points([self.APASS], cat))
        self.load(path)
        app = self.app
        self._star(self.GAIA, [self.APASS])
        app.clear_mark("comp", redraw=False)
        self.assertEqual(self._click(self.GAIA), self.APASS, "names inside the saved points are never changed")
        # Gaia answering later adds its name and V to the record; the key and the series' V stay
        app._write_series(path)
        rec = json.load(open(path, encoding="utf-8"))["meta"]["stars"]["records"][0]
        self.assertEqual(rec["key"], self.APASS)
        self.assertIn(self.GAIA, rec["names"])
        self.assertEqual(rec["v"]["Gaia DR3"], 12.301)
        self.assertEqual(rec["v_used"], 12.345)
        self.assertEqual(sr.shown_name(rec), self.GAIA)   # the name a list may show; the key is unchanged


if __name__ == "__main__":
    unittest.main()
