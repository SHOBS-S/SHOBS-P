"""2.4.0 step 4, the independent review's fixes.

M1  Load series never trims the Use comps choice silently: it asks once (Leave it out re-derives every night as
    Use comps… does and the series shows as not saved; Keep it leaves the file's choice). A file written with star
    records is not asked about; Discovery is not touched.
M2  A camera change resets only the aperture / sky ring / raw sat / linear ADU boxes that still hold what the last
    night left: a series loaded since, or a value typed since, stays.
M3  The wider recenter never moves a series comp onto another star: with a stored position, a catalog star that is
    not at that position is another star; the star found must have the slot's brightness; an accepted wide hop is
    always a ⚠ with a finding.
S1-S6 and the nits: see each test."""
import json
import math
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photometry_core as core    # noqa: E402
import star_record as sr          # noqa: E402
import test_240_m as M            # noqa: E402
import test_240_n as N            # noqa: E402
from test_240_m import A, B, X, K, POS, _old_meta, _points, _numbers   # noqa: E402

SPARE = "TYC 1-1-1"


class Asked:
    """Stands in for clearv_app.ask_choice: answers `value`, remembers the questions."""

    def __init__(self, value):
        self.value, self.calls = value, []

    def __call__(self, parent, title, message, choices, safe, cancel=None):
        self.calls.append((message, [v for v, _t in choices], safe))
        return self.value


# ---- M1 ------------------------------------------------------------------------------------------------------------
class TestLoadAsksAboutTheCompsInUse(M.AppCase):
    CAT = {A: 8.728, B: 11.31, X: 11.9}

    def _file(self, slots, name="S033.cvseries", active=(A, B, X)):
        cat = self.CAT
        meta = _old_meta([A, B, X], cat, extra={"active_comps": list(active)})
        meta["comps_used"] = [A, B, X, SPARE]
        meta["comp_catalog_mags"][SPARE] = 10.0
        meta["comp_coords"][X] = list(POS[X])
        meta["comp_coords"][B] = list(POS[B])
        for role in ("comp", "comp2", "comp3"):
            meta[f"{role}_name"] = meta[f"{role}_mag"] = ""
        for role, name_ in zip(("comp", "comp2", "comp3"), slots):
            meta[f"{role}_name"], meta[f"{role}_mag"] = name_, f"{cat[name_]:.3f}"
        return self.write(name, meta, _points([A, B, X], cat))

    def _slots(self):
        app = self.app
        return [app.comp_name.get(), app.comp2_name.get(), app.comp3_name.get()]

    def test_cleared_comp_is_asked_once_and_left_out(self):
        # S033: the slot was saved empty, active_comps still lists the star (a 2.3.x file)
        path = self._file([A, B])
        on_disk = open(path, "rb").read()
        twin = self._file([A, B], name="twin.cvseries")
        app = self.app
        ask = Asked("leave")
        with mock.patch.object(self.ca, "ask_choice", ask):
            self.load(path)
        self.assertEqual(len(ask.calls), 1, "asked once")
        message, values, safe = ask.calls[0]
        self.assertIn(f"The series' comps in use include {X}, but no comp box holds it", message)
        self.assertIn("as Use comps… does", message)
        self.assertNotIn("⚠", message)
        self.assertEqual((values, safe), (["leave", "keep"], "leave"))   # second review: Enter leaves it out
        self.assertEqual(app.active_comps, [A, B])
        self.assertEqual(self._slots(), [A, B, ""])
        self.assertTrue(app.series_unsaved(), "a re-derive at Load is a change not saved yet")
        self.assertEqual(open(path, "rb").read(), on_disk, "Load does not write the file")
        line = self.said("Comps loaded:")
        self.assertEqual(len(line), 1)
        self.assertIn(f"{X} left out of the comps in use, as you chose", line[0])
        left = _numbers(app.observations)
        # the same numbers as Use comps… gives for A and B on the file as saved
        with mock.patch.object(self.ca, "ask_choice", Asked("keep")):
            self.load(twin)
        self.assertEqual(app.active_comps, [A, B, X])
        kept = _numbers(app.observations)
        self.assertNotEqual(kept, left)
        self.assertTrue(app.apply_comp_selection([A, B], confirmed=True))
        self.assertEqual(_numbers(app.observations), left)
        # by hand, from the stored instrumental magnitudes
        o = app.observations[2]
        self.assertAlmostEqual(o.mag, (8.728 + 11.31) / 2.0 + o.target_inst - sum(o.comp_insts[:2]) / 2.0, places=12)

    def test_left_out_then_saved_never_comes_back(self):
        path = self._file([A, B])
        app = self.app
        with mock.patch.object(self.ca, "ask_choice", Asked("leave")):
            self.load(path)
        left = _numbers(app.observations)
        app._write_series(path)
        self.assertEqual(json.load(open(path, encoding="utf-8"))["meta"]["active_comps"], [A, B])
        ask = Asked("keep")
        with mock.patch.object(self.ca, "ask_choice", ask):
            self.load(path)
        self.assertEqual(ask.calls, [], "nothing to ask about any more")
        self.assertEqual(app.active_comps, [A, B])
        self.assertEqual(self._slots(), [A, B, ""])
        self.assertEqual(_numbers(app.observations), left)
        self.assertFalse(app.series_unsaved())

    def test_keep_leaves_the_series_as_saved(self):
        path = self._file([A, B])
        want = _numbers(core.load_series(path)[1])
        app = self.app
        ask = Asked("keep")
        with mock.patch.object(self.ca, "ask_choice", ask):
            self.load(path)
        self.assertEqual(len(ask.calls), 1)
        self.assertEqual(app.active_comps, [A, B, X])
        self.assertEqual(self._slots(), [A, B, X], "the box is filled again, as 2.3.6 did")
        self.assertEqual(_numbers(app.observations), want)
        self.assertFalse(app.series_unsaved())
        self.assertIn(f"{X} is still in the series' comps in use", self.said("Comps loaded:")[0])

    def test_a_drop_the_user_did_not_make_is_kept_without_a_question(self):
        # review v1: the comp moved off the frame (not the user's choice), the series was saved by 2.4.0 and loaded
        path = self._file([A, B, X])
        app = self.app
        self.load(path)                               # all three in their boxes: nothing to ask
        app.clear_mark("comp3", redraw=False)         # not the user's ✕
        app._write_series(path)
        saved = json.load(open(path, encoding="utf-8"))["meta"]
        self.assertEqual(saved["active_comps"], [A, B, X])
        self.assertEqual(saved["comp3_name"], "")
        self.assertIn("stars", saved)
        ask = Asked("leave")
        with mock.patch.object(self.ca, "ask_choice", ask):
            self.load(path)
        self.assertEqual(ask.calls, [], "a file with star records already followed the save rule")
        self.assertEqual(app.active_comps, [A, B, X])
        self.assertFalse(app.series_unsaved())
        # the nights were derived with three comps: +0.3 on one of them moves every point by exactly 0.1
        before = [o.mag for o in app.observations]
        app.series_path = ""                          # this check is about the numbers, not the file
        app.set_catalog_mags({A: 8.728 + 0.3})
        for was, now in zip(before, [o.mag for o in app.observations]):
            self.assertAlmostEqual(now - was, 0.1, places=12)

    def test_old_file_with_a_spare_in_the_choice_is_asked_not_trimmed(self):
        # review v1 (2): a pre-2.2.19 file whose Use comps choice holds a spare that never was in a comp box
        cat = {A: 8.728, B: 11.31, X: 11.9}
        meta = _old_meta([A, B], cat, extra={"active_comps": [A, X]})
        meta["spare_catalog"] = {X: {"mag": 11.9, "ra": POS[X][0], "dec": POS[X][1]}}
        path = self.write("old.cvseries", meta, _points([A, B], cat))
        want = _numbers(core.load_series(path)[1])
        ask = Asked("keep")
        with mock.patch.object(self.ca, "ask_choice", ask):
            self.load(path)
        self.assertEqual(len(ask.calls), 1)
        self.assertEqual(self.app.active_comps, [A, X])
        self.assertEqual(_numbers(self.app.observations), want)

    def test_discovery_is_not_touched(self):
        cat = {A: 8.728, B: 11.31, X: 11.9}
        meta = _old_meta([A, B], cat, extra={"active_comps": [A, X], "mode": "discovery"})
        meta["spare_catalog"] = {X: {"mag": 11.9, "ra": POS[X][0], "dec": POS[X][1]}}
        path = self.write("disc.cvseries", meta, _points([A, B], cat))
        want = _numbers(core.load_series(path)[1])
        app = self.app
        ask = Asked("leave")
        try:
            with mock.patch.object(self.ca, "ask_choice", ask):
                self.load(path)
            self.assertEqual(app.mode_key, "discovery")
            self.assertEqual(ask.calls, [])
            self.assertEqual(app.active_comps, [A, X])
            self.assertEqual(_numbers(app.observations), want)
            self.assertFalse(app.series_unsaved())
        finally:
            app.observations = []
            app._clear_series_state()
            app.set_mode("variables")

    def test_a_series_whose_comps_are_all_in_their_boxes_is_not_asked(self):
        path = self._file([A, B, X])
        want = _numbers(core.load_series(path)[1])
        ask = Asked("leave")
        with mock.patch.object(self.ca, "ask_choice", ask):
            self.load(path)
        self.assertEqual(ask.calls, [])
        self.assertEqual(_numbers(self.app.observations), want)


# ---- S2, S3, S4, S5, nits (series side) ----------------------------------------------------------------------------
class TestSeriesShouldFixes(M.AppCase):
    def _load(self, comps=(A,), name="HD_219134.cvseries"):
        cat = {A: 8.728, B: 11.31}
        self.path = self.write(name, _old_meta(list(comps), cat), _points(list(comps), cat))
        self.load(self.path)
        return cat

    def test_s2_a_second_load_of_an_old_file_makes_no_second_backup(self):
        self._load()
        first = os.path.join(self.dir, "HD_219134_backup.cvseries")
        self.assertTrue(os.path.isfile(first))
        self.load(self.path)
        self.load(self.path)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "HD_219134_backup2.cvseries")))
        said = self.said("new star format")
        self.assertEqual(said, ["Series updated to the new star format; the original is already kept as "
                                "HD_219134_backup.cvseries."])
        # another file under the same name is another original: it gets its own backup
        cat = {A: 8.728}
        core.save_series(self.path, _old_meta([A], cat), _points([A], cat, n=7))
        self.load(self.path)
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "HD_219134_backup2.cvseries")))

    def test_s3_not_while_busy(self):
        self._load()
        app = self.app
        before = _numbers(app.observations)
        app.comp_mag.set("8.704")
        app.update_star_panel()
        said = []
        app.busy = True
        try:
            with mock.patch.object(self.ca.messagebox, "showinfo", lambda _t, text, **k: said.append(text)):
                self.assertFalse(app.use_new_series_v("comp"))
        finally:
            app.busy = False
        self.assertEqual(len(said), 1)
        self.assertIn("Nothing was changed", said[0])
        self.assertEqual(_numbers(app.observations), before)
        self.assertIn("comp", app._v_disagree, "the question is still open")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "HD_219134_backup2.cvseries")))

    def test_s3_shift_is_from_the_series_value_whatever_the_live_table_holds(self):
        self._load()
        app = self.app
        before = _numbers(app.observations)
        app.comp_mag.set("8.704")
        app.update_star_panel()
        app.comp_catalog_mags[A] = 8.704     # something already put the new value in the live table
        self.assertTrue(app.use_new_series_v("comp"))
        for old, new in zip(before, _numbers(app.observations)):
            self.assertAlmostEqual(new[1], old[1] + (8.704 - 8.728), places=12)
        self.assertIn("instead of 8.728", self.said("Series re-calibrated")[0])

    def test_s3_a_slot_showing_another_name_of_the_star_follows(self):
        self._load()
        app = self.app
        rec = app._series_star_for(A)
        sr.add_name(rec, M.A_GAIA)
        app.comp_mag.set("8.704")
        app.update_star_panel()
        self.assertIn("comp", app._v_disagree)
        app._v_reconciling = True            # the slot now shows tonight's name of the same star
        app.comp_name.set(M.A_GAIA)
        app.comp_mag.set("8.728")
        app._v_reconciling = False
        self.assertTrue(app.use_new_series_v("comp"))
        self.assertEqual(app.comp_mag.get(), "8.704")
        self.assertEqual(app.comp_catalog_mags[A], 8.704)

    def test_s3_a_failed_save_is_said_and_the_series_shows_as_not_saved(self):
        self._load()
        app = self.app
        app.comp_mag.set("8.704")
        app.update_star_panel()
        on_disk = open(self.path, "rb").read()
        boxes = []
        with mock.patch.object(app, "_write_series", side_effect=OSError("disk full")), \
                mock.patch.object(self.ca.messagebox, "showwarning", lambda _t, text, **k: boxes.append(text)):
            self.assertTrue(app.use_new_series_v("comp"))
        self.assertEqual(len(boxes), 1)
        self.assertIn("could not be written (disk full)", boxes[0])
        self.assertIn("Save series", boxes[0])
        self.assertEqual(open(self.path, "rb").read(), on_disk)
        self.assertEqual(app.comp_catalog_mags[A], 8.704)
        self.assertTrue(app.series_unsaved(), "memory and file differ: never silently")
        app._write_series(self.path)
        self.assertFalse(app.series_unsaved())

    def test_s3_a_failed_save_of_the_check_too(self):
        self._load()
        app = self.app
        app.check_mag.set("9.830")
        app.update_star_panel()
        self.assertIn("check", app._v_disagree)
        with mock.patch.object(app, "_write_series", side_effect=OSError("disk full")):
            app.use_new_series_v("check")
        self.assertTrue(app.series_unsaved(), "the check's magnitude is not in the points, but it is not saved")

    def test_s4_another_name_measured_as_a_spare_keeps_its_magnitude(self):
        # comp_catalog_mags holds a second name of comp A; no night's comps use it, but the points measured it as a
        # spare: its magnitude is not dropped at Load
        cat = {A: 8.728, M.A_GAIA: 8.704}
        meta = _old_meta([A], cat)
        meta["comp_coords"][M.A_GAIA] = [POS[A][0], POS[A][1] + 0.2 / 3600.0]
        pts = _points([A], {A: 8.728})
        for o in pts:
            o.spare_insts = {M.A_GAIA: -10.0}
        path = self.write("spare.cvseries", meta, pts)
        self.load(path)
        self.assertEqual(self.app.comp_catalog_mags, {A: 8.728, M.A_GAIA: 8.704})
        # without that spare in the points it goes (one star, one magnitude: test_240_m)
        path = self.write("nospare.cvseries", meta, _points([A], {A: 8.728}))
        self.load(path)
        self.assertEqual(self.app.comp_catalog_mags, {A: 8.728})

    def test_s5_tonights_aliases_go_with_the_target(self):
        app = self.app
        app._tonight_aliases = {A: [M.A_GAIA]}
        app.clear_target(quiet=True)
        self.assertEqual(app._tonight_aliases, {})
        app._tonight_aliases = {A: [M.A_GAIA]}
        app.new_reduction(ask=False)
        self.assertEqual(app._tonight_aliases, {})

    def test_nit_an_unknown_source_is_not_called_typed(self):
        self._load()
        app = self.app
        # a comp the series took from another file: a magnitude, no catalog entry, not in any box
        other = "TYC 7-7-7"
        app.comps_used = [A, other]
        app.comp_catalog_mags[other] = 11.31
        app.comp_coords[other] = (POS[B][0] + 0.2, POS[B][1])
        app._sync_star_records()
        rec = app._series_star_for(other)
        self.assertEqual((rec["v_used"], rec["v_source"]), (11.31, ""))
        self.assertEqual(sr.v_words(rec), "V 11.310")
        # a magnitude in a comp box that no catalog gave is typed
        app.comps_used = [A, B, X]
        app.comp3_name.set(X)
        app.comp3_mag.set("11.900")
        app.comp_catalog_mags[X] = 11.9
        app._sync_star_records()
        self.assertEqual(app._series_star_for(X)["v_source"], sr.TYPED)

    def test_nit_dead_code_is_gone(self):
        self.assertFalse(hasattr(self.ca.App, "_snap_to_star"))
        self.assertFalse(hasattr(self.app, "_last_center"))
        src = open(self.ca.__file__, encoding="utf-8").read()
        self.assertEqual(src.count("\nimport star_record"), 1)


# ---- M2 ------------------------------------------------------------------------------------------------------------
CAM_A = {"NAXIS1": 6248, "NAXIS2": 4176, "INSTRUME": "ZWO ASI2600MC Duo", "XBINNING": 1, "BITPIX": 16}
CAM_B = {"NAXIS1": 3000, "NAXIS2": 2000, "INSTRUME": "QHY 268M", "XBINNING": 1, "BITPIX": 16}


class TestCameraChangeKeepsWhatWasSetSince(N.PlaceBase):
    def setUp(self):
        app = self.app
        app._loading_meta = False
        app.busy = False
        app._camera_last = None
        app._camera_folders = None
        app._camera_aperture_pending = None
        app._camera_apertures, app._camera_series_set = {}, set()
        app._own_setup = None
        self._settings = {k: app.settings.get(k) for k in ("camera_last", "camera_limits")}
        app.settings.pop("camera_last", None)
        app.settings.pop("camera_limits", None)
        for name in ("bias_dir", "dark_dir", "darkflat_dir", "flat_dir", "light_dir"):
            getattr(app, name).set("")
        app.radius.set(8.0); app.sky_in.set(14.0); app.sky_out.set(22.0)
        app.sat_limit.set(60000)
        app.linear_limit.set("40000")
        self.lines = []
        p = mock.patch.object(app, "log", side_effect=lambda text, **kw: self.lines.append(text))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._restore)
        s = mock.patch.object(app, "_save_settings", lambda *a, **k: None)
        s.start()
        self.addCleanup(s.stop)

    def _restore(self):
        app = self.app
        for k, v in self._settings.items():
            if v is None:
                app.settings.pop(k, None)
            else:
                app.settings[k] = v
        app._camera_last = app._camera_folders = app._camera_aperture_pending = None
        app._set_camera_note("")

    def _night_on_a(self):
        app = self.app
        self.assertFalse(app._camera_check(CAM_A, ""))
        app._camera_folders_snapshot()          # Calibrate and Run photometry do this
        self.lines.clear()

    def _boxes(self):
        app = self.app
        return (app.radius.get(), app.sky_in.get(), app.sky_out.get(), app.sat_limit.get(), app.linear_limit.get())

    def test_a_series_loaded_since_keeps_its_aperture_and_limits(self):
        # review v2: camera A reduced; a camera-B series is loaded (its aperture and limits fill the boxes); then
        # B's lights are chosen
        app = self.app
        self._night_on_a()
        app.radius.set(4.0); app.sky_in.set(8.0); app.sky_out.set(12.0)
        app.sat_limit.set(50000)
        app.linear_limit.set("42000")
        self.assertTrue(app._camera_check(CAM_B, ""))
        self.assertEqual(self._boxes(), (4.0, 8.0, 12.0, 50000, "42000"))
        self.assertIsNone(app._camera_aperture_pending, "no sizes from the image over the series' own")
        self.assertEqual(len(self.lines), 3, self.lines)
        self.assertIn("Aperture and sky ring left as they are", self.lines[1])
        self.assertEqual(self.lines[2], "Raw sat ADU and linear ADU left as they are (set after the last camera's "
                                        "night).")
        for line in self.lines:
            self.assertNotIn("⚠", line)

    def test_untouched_boxes_are_still_reset(self):
        app = self.app
        app.radius.set(6.0)                     # camera A's own night used r 6
        self._night_on_a()
        self.assertTrue(app._camera_check(CAM_B, ""))
        self.assertEqual(self._boxes(), (8.0, 14.0, 22.0, 60000, ""))
        self.assertEqual(app._camera_aperture_pending, self.ca.APERTURE_DEFAULTS)
        self.assertIn("back to the defaults", self.lines[1])

    def test_only_the_box_changed_since_is_kept(self):
        app = self.app
        self._night_on_a()
        app.linear_limit.set("35000")           # typed after the last night
        self.assertTrue(app._camera_check(CAM_B, ""))
        self.assertEqual(self._boxes(), (8.0, 14.0, 22.0, 60000, "35000"))
        self.assertIn("Linear ADU left as it is", self.lines[2])
        self.assertIn("Raw sat ADU 60,000", self.lines[2])

    def test_a_fresh_session_has_the_same_guard(self):
        # the last camera was in an earlier session; the boxes are as SHOBS-P started
        app = self.app
        app.settings["camera_last"] = self.ca.camera_signature(CAM_A)
        app._camera_folders_snapshot()          # App.__init__ does this
        app.sat_limit.set(50000)                # a series loaded since the start
        app.linear_limit.set("42000")
        app.radius.set(4.0)
        self.assertTrue(app._camera_check(CAM_B, ""))
        self.assertEqual(self._boxes(), (4.0, 14.0, 22.0, 50000, "42000"))
        # and with nothing changed since the start, the limits are reset as before
        app._camera_last = None
        app.settings["camera_last"] = self.ca.camera_signature(CAM_A)
        app.settings.pop("camera_limits", None)     # (else camera B's limits come back "as last used")
        app.radius.set(8.0); app.sat_limit.set(60000); app.linear_limit.set("40000")
        app._camera_folders_snapshot()
        self.assertTrue(app._camera_check(CAM_B, ""))
        self.assertEqual(self._boxes(), (8.0, 14.0, 22.0, 60000, ""))

    def test_the_session_starts_with_a_snapshot(self):
        snap = self.ca.App._camera_values_now(self.app)
        self.assertEqual(set(snap), {"aperture", "sat", "linear"})
        src = open(self.ca.__file__, encoding="utf-8").read()
        init = src[src.index("    def __init__(self):\n        super().__init__()"):src.index("    def call_ui(")]
        self.assertIn("self._camera_folders_snapshot()", init)

    def test_the_same_cameras_lights_scanned_again_do_not_adopt_a_loaded_series(self):
        app = self.app
        self._night_on_a()
        app.radius.set(4.0)                     # the series loaded since
        self.assertFalse(app._camera_check(CAM_A, ""))     # Scan folders on the old lights
        self.assertTrue(app._camera_check(CAM_B, ""))
        self.assertEqual(app.radius.get(), 4.0)

    def test_new_camera_sizes_are_set_before_run_reads_them(self):
        # review S7: the sizes were set from a background measurement; a quick Run could read the defaults
        app, _field = self._setup()
        own = app.radius.get()                  # camera A's night used this
        self._night_on_a()
        self.assertTrue(app._camera_check(CAM_B, ""))
        self.assertEqual(app._camera_aperture_pending, self.ca.APERTURE_DEFAULTS)
        self.assertEqual(app.radius.get(), 8.0)
        self.assertTrue(app._camera_aperture_settle())
        self.assertIsNone(app._camera_aperture_pending)
        want = core.suggest_apertures(app._seeing())
        self.assertEqual((app.radius.get(), app.sky_in.get(), app.sky_out.get()),
                         (want["radius"], want["sky_in"], want["sky_out"]))
        # those sizes belong to camera B now: the next camera does not keep them (second review, item 8: going
        # back to camera A puts A's own back)
        self.assertTrue(app._camera_check(CAM_A, ""))
        self.assertEqual(app.radius.get(), own)
        self.assertIsNone(app._camera_aperture_pending)
        # Run photometry settles them itself before it reads the boxes
        src = open(self.ca.__file__, encoding="utf-8").read()
        run = src[src.index("    def run_photometry(self"):]
        self.assertLess(run.index("self._camera_aperture_settle()"), run.index("self._aperture_sane()"))

    # ---- second review (items 4 and 8) ----
    SERIES = {"radius": 8.0, "sky_in": 14.0, "sky_out": 22.0, "sat_limit": 50000}

    def test_r2_lights_first_then_a_series_at_the_default_aperture(self):
        # new-camera lights reset the boxes to 8/14/22 (to be set from the star sizes); the series loaded next
        # holds 8/14/22 itself: its aperture stays, the star-size suggestion is off
        app, _field = self._setup()
        self._night_on_a()
        self.assertTrue(app._camera_check(CAM_B, ""))
        self.assertEqual(app._camera_aperture_pending, self.ca.APERTURE_DEFAULTS)
        app._apply_meta(dict(self.SERIES))
        self.assertIsNone(app._camera_aperture_pending)
        self.lines.clear()
        self.assertFalse(app._camera_aperture_settle())
        self.assertFalse(app._camera_aperture_apply(None, 3.0))
        self.assertEqual(self._boxes()[:4], (8.0, 14.0, 22.0, 50000))
        self.assertEqual([ln for ln in self.lines if "Aperture set" in ln], [])

    def test_r2_a_series_equal_to_last_nights_sizes_then_other_lights(self):
        # the loaded series' aperture and raw sat ADU equal what the last night left: still the series' own
        app = self.app
        app.radius.set(4.0); app.sky_in.set(8.0); app.sky_out.set(12.0)
        app.sat_limit.set(50000)
        self._night_on_a()
        app._apply_meta({"radius": 4.0, "sky_in": 8.0, "sky_out": 12.0, "sat_limit": 50000})
        self.assertTrue(app._camera_check(CAM_B, ""))
        self.assertEqual(self._boxes()[:4], (4.0, 8.0, 12.0, 50000))
        self.assertIsNone(app._camera_aperture_pending)
        self.assertEqual(len([ln for ln in self.lines if "Aperture and sky ring left as they are" in ln]), 1)
        self.assertEqual(len([ln for ln in self.lines if "Raw sat ADU left as it is" in ln]), 1)
        # once these lights are calibrated the boxes go with them: the next camera resets them as always
        app._camera_folders_snapshot()
        self.assertEqual(app._camera_series_set, set())
        cam_c = dict(CAM_B, INSTRUME="A third camera", NAXIS1=1234, NAXIS2=999)
        self.assertTrue(app._camera_check(cam_c, ""))
        self.assertEqual(self._boxes()[:3], (8.0, 14.0, 22.0))

    def test_r2_back_to_the_camera_before_puts_its_sizes_back(self):
        # a wrong folder picked (camera B), then the right one again (camera A)
        app = self.app
        app.radius.set(6.0); app.sky_in.set(11.0); app.sky_out.set(17.0)
        self._night_on_a()
        self.assertTrue(app._camera_check(CAM_B, ""))
        self.assertEqual(self._boxes(), (8.0, 14.0, 22.0, 60000, ""))
        self.lines.clear()
        self.assertTrue(app._camera_check(CAM_A, ""))
        self.assertEqual(self._boxes(), (6.0, 11.0, 17.0, 60000, "40000"))
        self.assertIsNone(app._camera_aperture_pending, "not set from the star sizes over A's own")
        self.assertEqual(self.lines[1], "Aperture and sky ring put back to r 6, sky 11–17 px, as last used with this "
                                        "camera in this session.")
        self.assertIn("as last used with this camera", self.lines[2])
        # sizes typed for B since are the user's own: they stay
        self.assertTrue(app._camera_check(CAM_B, ""))
        app.radius.set(5.0)
        self.assertTrue(app._camera_check(CAM_A, ""))
        self.assertEqual(app.radius.get(), 5.0)


# ---- M3, S1, S6 ----------------------------------------------------------------------------------------------------
class TestWiderRecenterKeepsToItsStar(N.PlaceBase):
    NAME = "Gaia DR3 555"

    def _series_comp(self, off, mag="12.000", **setup):
        app, field = self._setup(**setup)
        app.series_path = "/tmp/series.csv"
        c1 = self.ca.COMP_ROLES[0]
        app.comp_coords = {self.NAME: self._rd(300.0 + off, 300.0)}
        getattr(app, f"{c1}_name").set(self.NAME)
        getattr(app, f"{c1}_mag").set(mag)
        placed, lines = self._logged(app._auto_place_named_slots)
        return app, c1, placed, [ln for ln in lines if "555" in ln]

    def tearDown(self):
        self.app.series_path = ""
        self.app.findings.clear()

    def test_never_onto_a_brighter_neighbour(self):
        # review v3: the series comp (V 12.0) is not in tonight's catalogs; a V 10.5 catalog star is 12 px away; the
        # solve is 6.3 px out, so the stored position falls nearer the neighbour
        app, c1, placed, lines = self._series_comp(
            6.3, extra=[(300.0, 300.0, 12.0), (312.0, 300.0, 10.5)],
            chart_extra=[(312.0, 300.0, {"auid": "TYC 9-9-9", "label": "10.5", "mag": 10.5, "mags": {"V": 10.5}})])
        xy = getattr(app, f"{c1}_xy")
        if xy is not None:
            self.assertGreater(math.hypot(xy[0] - 312.0, xy[1] - 300.0), 4.0, "not on the neighbour")
            self.assertLess(math.hypot(xy[0] - 306.3, xy[1] - 300.0), 0.01, "left where the position falls")
        self.assertEqual(getattr(app, f"{c1}_name").get(), self.NAME)
        self.assertEqual(getattr(app, f"{c1}_mag").get(), "12.000")
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("⚠ "), lines[0])
        self.assertNotIn("wider search", lines[0])
        self.assertIn("TYC 9-9-9", lines[0])
        self.assertIn("Click the star" if xy is not None else "click the star", lines[0])
        self.assertEqual(len(app.findings), 1)

    def test_the_lone_catalog_star_is_not_excused_when_the_position_is_known(self):
        # the same with the neighbour of the comp's own brightness: position, not brightness, tells them apart
        app, c1, placed, lines = self._series_comp(
            6.3, extra=[(300.0, 300.0, 12.0), (312.0, 300.0, 12.1)],
            chart_extra=[(312.0, 300.0, {"auid": "TYC 9-9-9", "label": "12.1", "mag": 12.1, "mags": {"V": 12.1}})])
        xy = getattr(app, f"{c1}_xy")
        if xy is not None:
            self.assertGreater(math.hypot(xy[0] - 312.0, xy[1] - 300.0), 4.0)
        self.assertTrue(lines[0].startswith("⚠ "), lines[0])
        self.assertEqual(len(app.findings), 1)

    def test_a_star_of_the_wrong_brightness_is_not_taken(self):
        # nothing in tonight's catalogs near; the only star in reach is 3 mag brighter than the series' comp
        app, c1, placed, lines = self._series_comp(6.3, mag="13.600", extra=[(300.0, 300.0, 10.5)])
        c = app._center_placed((306.3, 300.0), rd=self._rd(306.3, 300.0), mag="13.600")
        self.assertFalse(c["centered"])
        self.assertEqual(c["why"], "bright")      # the star found is too bright to be the series' star
        self.assertEqual(c["xy"], (306.3, 300.0))
        self.assertTrue(lines[0].startswith("⚠ "), lines[0])
        self.assertIn("to be this star", lines[0])
        self.assertEqual(len(app.findings), 1)
        # with the slot's own brightness it is taken, and said as a wide hop
        c = app._center_placed((306.3, 300.0), rd=self._rd(306.3, 300.0), mag="10.6")
        self.assertEqual(c["how"], "wide")

    def test_alone_and_of_the_right_brightness_it_still_centers_with_a_finding(self):
        # Qatar-10 b 0704: 6.8 px off near the edge, no other star near (tonight's catalog star is drawn where the
        # solution puts it)
        star = (300.0, 21.8, 11.5)
        name = "APASS J185609.9+692057"
        app, field = self._setup(extra=[star],
                                 chart_extra=[(300.0, 28.6, {"auid": name, "mag": 11.5, "mags": {"V": 11.5}})])
        c4 = self.ca.COMP_ROLES[3]
        app.series_path = "/tmp/series.csv"
        app.comp_coords = {name: self._rd(300.0, 28.6)}
        getattr(app, f"{c4}_name").set(name)
        getattr(app, f"{c4}_mag").set("11.5")
        placed, lines = self._logged(app._auto_place_named_slots)
        self.assertEqual(placed, [c4], lines)
        got = getattr(app, f"{c4}_xy")
        self.assertLess(math.hypot(got[0] - 300.0, got[1] - 21.8), 0.3)
        line = [ln for ln in lines if name in ln]
        self.assertEqual(len(line), 1)
        self.assertTrue(line[0].startswith("⚠ "))
        self.assertIn("more than the normal centering allows", line[0])
        self.assertIn("Check it is the right star", line[0])
        self.assertEqual(len(app.findings), 1)
        self.assertIn("wider search", app._slot_notes[c4][1])
        # clearing the slot takes the finding away
        app._place_finding(c4, None)
        self.assertEqual(len(app.findings), 0)

    # ---- second review (items 5, 6, 7) ----
    def test_r2_a_neighbour_1p2_mag_brighter_does_not_win_the_wide_hop(self):
        # the comp (V 12.0) and an uncatalogued neighbour 1.2 mag brighter are both in reach; the #77 window let it in
        app, c1, placed, lines = self._series_comp(6.3, extra=[(300.0, 300.0, 12.0), (312.0, 300.0, 10.8)])
        xy = getattr(app, f"{c1}_xy")
        self.assertLess(math.hypot(xy[0] - 306.3, xy[1] - 300.0), 0.01, "the marker stays where the position falls")
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("⚠ "))
        self.assertIn("too bright to be this star (V 12.00)", lines[0])
        self.assertIn("Click the star to center it.", lines[0])
        self.assertEqual(len(app.findings), 1)
        self.assertEqual(self.ca.App.WIDE_FIT_MAG, 0.75)
        self.assertEqual((self.ca.App.NAME_FIT_FAINT, self.ca.App.NAME_FIT_BRIGHT), (1.5, 2.5), "#77 as it was")

    def test_r2_within_the_window_the_wide_hop_is_still_made(self):
        app, c1, placed, lines = self._series_comp(6.3, mag="12.600", extra=[(300.0, 300.0, 12.0)])
        xy = getattr(app, f"{c1}_xy")
        self.assertLess(math.hypot(xy[0] - 300.0, xy[1] - 300.0), 0.3)
        self.assertIn("wider search", lines[0])
        app.findings.clear()
        app, c1, placed, lines = self._series_comp(6.3, mag="12.900", extra=[(300.0, 300.0, 12.0)])
        xy = getattr(app, f"{c1}_xy")
        self.assertLess(math.hypot(xy[0] - 306.3, xy[1] - 300.0), 0.01)
        self.assertIn("too bright to be this star (V 12.90)", lines[0])

    def test_r2_without_a_field_zero_point_the_wide_hop_is_refused(self):
        app, field = self._setup(extra=[(300.0, 300.0, 12.0)])
        app.series_path = "/tmp/series.csv"
        c1 = self.ca.COMP_ROLES[0]
        app.comp_coords = {self.NAME: self._rd(306.3, 300.0)}
        getattr(app, f"{c1}_name").set(self.NAME)
        getattr(app, f"{c1}_mag").set("12.000")
        with mock.patch.object(app, "_field_zero_point", lambda *a, **k: None):
            placed, lines = self._logged(app._auto_place_named_slots)
        lines = [ln for ln in lines if "555" in ln]
        xy = getattr(app, f"{c1}_xy")
        self.assertLess(math.hypot(xy[0] - 306.3, xy[1] - 300.0), 0.01, "the marker stays")
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("⚠ "))
        self.assertIn("but not centered on a star: a star lies 6.3 px from it", lines[0])
        self.assertIn("no brightness check was possible on this image (no field zero point)", lines[0])
        self.assertIn("Click the star to center it.", lines[0])
        self.assertEqual(len(app.findings), 1)

    def test_r2_without_a_zero_point_the_normal_centering_is_as_before(self):
        app, field = self._setup(extra=[(300.0, 300.0, 12.0)])
        app.series_path = "/tmp/series.csv"
        c1 = self.ca.COMP_ROLES[0]
        app.comp_coords = {self.NAME: self._rd(300.9, 300.0)}
        getattr(app, f"{c1}_name").set(self.NAME)
        getattr(app, f"{c1}_mag").set("12.000")
        with mock.patch.object(app, "_field_zero_point", lambda *a, **k: None):
            placed, lines = self._logged(app._auto_place_named_slots)
        xy = getattr(app, f"{c1}_xy")
        self.assertLess(math.hypot(xy[0] - 300.0, xy[1] - 300.0), 0.3)
        self.assertEqual([ln for ln in lines if "⚠" in ln], [])

    def test_r2_the_slots_own_name_off_the_stored_position_is_said_as_such(self):
        # nit 6: the same star catalogued more than 1" from the series' stored position
        app, c1, placed, lines = self._series_comp(
            6.3, extra=[(300.0, 300.0, 12.0)],
            chart_extra=[(300.0, 300.0, {"auid": self.NAME, "label": "12.0", "mag": 12.0, "mags": {"V": 12.0}})])
        self.assertEqual(len(lines), 1, lines)
        self.assertRegex(lines[0], r"tonight's catalog position of this star disagrees with the series' stored "
                                   r"position \(by \d+\.\d\"\), so the star to center on could not be told")
        self.assertNotIn("another catalog star", lines[0])
        self.assertEqual(len(app.findings), 1)

    def test_r2_a_normal_centering_onto_a_much_fainter_star_is_said(self):
        # nit 7: the comp is out of reach; an uncatalogued star 2 mag fainter is within the normal centering
        app, c1, placed, lines = self._series_comp(14.0, extra=[(300.0, 300.0, 12.0), (318.0, 300.0, 14.0)])
        xy = getattr(app, f"{c1}_xy")
        self.assertLess(math.hypot(xy[0] - 318.0, xy[1] - 300.0), 0.3, "the marker goes where it always went")
        self.assertEqual(len(lines), 2, lines)
        self.assertNotIn("⚠", lines[0])
        self.assertRegex(lines[1], r"^⚠ Comp 1 Gaia DR3 555: the star centered on looks 2\.\d mag fainter than the "
                                   r"series' V \(V 14\.\d on this image, V 12\.00 in the series\); check it is the "
                                   r"right star\.$")
        self.assertEqual(len(app.findings), 1)
        app.findings.clear()
        # 1.5 mag fainter: nothing said
        app, c1, placed, lines = self._series_comp(14.0, extra=[(300.0, 300.0, 12.0), (318.0, 300.0, 13.5)])
        self.assertEqual(len(lines), 1, lines)
        self.assertEqual(len(app.findings), 0)

    def test_a_normal_placement_is_unchanged_and_says_the_real_distance(self):
        # S1: the catalog star is the start; the Log gives the distance from where the stored position falls
        app, field = self._setup()
        app.series_path = "/tmp/series.csv"
        c1 = self.ca.COMP_ROLES[0]
        x, y, v = field[5]
        name = "TYC 100-5-1"
        app.comp_coords = {name: self._rd(x + 0.7, y)}       # 0.7" from tonight's catalog position: the same star
        getattr(app, f"{c1}_name").set(name)
        getattr(app, f"{c1}_mag").set(f"{v:.3f}")
        placed, lines = self._logged(app._auto_place_named_slots)
        got = getattr(app, f"{c1}_xy")
        self.assertLess(math.hypot(got[0] - x, got[1] - y), 0.3)
        line = [ln for ln in lines if name in ln][0]
        self.assertRegex(line, r"centered \(0\.[5-9] px from where the position falls\); magnitude")
        self.assertNotIn("⚠", line)
        self.assertNotIn("surely the same star", line)
        self.assertEqual(len(app.findings), 0)

    def test_s1_a_start_star_beyond_one_arcsec_is_said(self):
        app, field = self._setup()
        app.series_path = "/tmp/series.csv"
        c1 = self.ca.COMP_ROLES[0]
        x, y, v = field[5]
        name = "Gaia DR3 777"
        app.comp_coords = {name: self._rd(x + 3.0, y)}       # 3" from tonight's catalog star (1 px = 1")
        getattr(app, f"{c1}_name").set(name)
        getattr(app, f"{c1}_mag").set(f"{v:.3f}")
        placed, lines = self._logged(app._auto_place_named_slots)
        got = getattr(app, f"{c1}_xy")
        self.assertLess(math.hypot(got[0] - x, got[1] - y), 0.3, "which star is taken is as in 2.3.6")
        line = [ln for ln in lines if name in ln][0]
        self.assertRegex(line, r"centered \(3\.\d px from where the position falls\)")
        self.assertIn('tonight\'s catalog star there is 3.0" from the stored position', line)
        self.assertIn("check it is the right star", line)

    def test_s6_a_fast_star_is_matched_where_it_is_tonight(self):
        app, field = self._setup()
        x, y, v = field[5]
        name = "TYC 100-5-1"
        now = self._rd(x, y)
        year = core.pm_epoch()
        # stored 10 years ago; the star moves 2"/yr in declination: it was 20" from where it is tonight
        then = (now[0], now[1] - 20.0 / 3600.0)
        rec = sr.new_record(name, then[0], then[1], pm=[0.0, 2000.0], epoch=year - 10.0)
        app.star_records = [rec]
        app.comp_coords = {name: then}
        try:
            self.assertGreater(sr.separation_arcsec(*then, *now), 19.0)
            got = app._saved_radec(name)
            self.assertLess(sr.separation_arcsec(*got, *now), 0.05)
            self.assertTrue(app._stored_is(name, then, now))
            # tonight's catalog star at that place keeps the series' name, and the stored position is not replaced
            star = app.chart_placed[5][2]
            self.assertEqual(app._slot_name_for(dict(star, auid="Gaia DR3 999")), name)
            c1 = self.ca.COMP_ROLES[0]
            setattr(app, f"{c1}_xy", (x, y))
            app._fill_from_chart(c1, (x, y), say=False)
            self.assertEqual(app.comp_coords[name], then)
            # a star without a proper motion is compared as stored, as before
            self.assertEqual(app._radec_tonight("TYC 100-6-1", (1.0, 2.0)), (1.0, 2.0))
            rec["pm"] = None
            self.assertFalse(app._stored_is(name, then, now))
        finally:
            app.star_records = []
            app.comp_coords = {}


class TestTheManualKnowsTheNewMessages(unittest.TestCase):
    def test_findings_have_their_what_to_do(self):
        import findings
        hints = findings.load_hints()
        for text in (
                "⚠ Comp 1 Gaia DR3 555 placed from the series position, centered by a wider search, 6.3 px from where "
                "the position falls — more than the normal centering allows. Check it is the right star; magnitude "
                "12.0 kept as loaded.",
                "⚠ Comp 1: Comp 1 placed on QATAR COMP; type its magnitude.  Marker at (300, 22). Centered on the "
                "star 6.8 px from the catalog position by a wider search — more than the normal centering allows (22 "
                "px from the top edge). Check it is the right star.",
                "⚠ Comp 1 Gaia DR3 555 placed from the series position at (306, 300) but not centered on a star: "
                "another catalog star (TYC 9-9-9) lies within 11 px, so the star to center on could not be told. "
                "Click the star to center it.",
                "⚠ Comp 1 X couldn't be placed where the series position falls, at (3, 4): the brightest star within "
                "11 px reads about V 10.5, too bright to be this star (V 13.60), so the marker was not moved onto it. "
                "Solve again, or click the star.",
                "⚠ The series was re-calibrated but could not be saved (disk full). It is not saved yet: use Save "
                "series (Output page).",
                "⚠ X could not be left out (no catalog magnitude); the series' comps in use stay as saved.",
                "⚠ X could not be left out (no catalog magnitude); the series' comps in use stay as they are.",
                "⚠ Comp 1 Gaia DR3 555 placed from the series position at (306, 300) but not centered on a star: a "
                "star lies 5.7 px from it (farther than the normal centering allows), but no brightness check was "
                "possible on this image (no field zero point), so the marker was not moved onto it. Click the star "
                "to center it.",
                "⚠ Comp 1 Gaia DR3 555 placed from the series position at (306, 300) but not centered on a star: "
                "tonight's catalog position of this star disagrees with the series' stored position (by 6.3\"), so "
                "the star to center on could not be told. Click the star to center it.",
                "⚠ Comp 1 Gaia DR3 555: the star centered on looks 2.0 mag fainter than the series' V (V 14.0 on "
                "this image, V 12.00 in the series); check it is the right star."):
            self.assertTrue(findings.match_hint(text, hints), text)


if __name__ == "__main__":
    unittest.main()
