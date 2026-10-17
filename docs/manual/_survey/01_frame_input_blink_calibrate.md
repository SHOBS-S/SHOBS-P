# Survey 01 — Main window frame, INPUT, BLINK and CALIBRATE pages

SHOBS-P 2.2.17 (`clearv_app.py`, `photometry_core.py` = `core`). Code read-only; line numbers refer to
`clearv_app.py` unless prefixed `core:`. Written section by section; later sections may be missing if the
session stopped early.

---

## 1. Main window frame

### 1.1 Window
- Title bar: `"Shiloh Hill Observatory – Photometry (SHOBS-P)"` (`APP_TITLE`, l.56); version `"2.2.17"` (`APP_VERSION`, l.57).
- Opens as a normal (not maximized) window at ~80 % of the screen, centred; min size 1200×800 or the screen if smaller (`App.__init__`, l.495-501). Icon `shobs_p.ico` / `shobs_p.png` from the program folder; Windows taskbar AppUserModelID `ShilohHill.SHOBS-P` (`_set_window_icon`, l.1208).
- ttk theme `clam`; background `#e7eef2`; pages are white "cards"; group boxes are `ttk.LabelFrame` with bold title in the mode colour (`_style`, l.909; `_apply_theme`, l.963).
- Unhandled errors inside the window: logged to `~/.shobs_p.log` and shown as a messagebox
  `"Something went wrong:\n<error>\n\nDetails were written to <LOG_FILE>."` (`report_callback_exception`, l.1227).

### 1.2 Layout (`_build`, l.977)
Left **sidebar** (230 px, mode colour), right side = **banner** strip (mode colour) over the **page area**; **status bar** across the very bottom (packed before the main area so a short window squeezes the pages, not the status bar — l.1032-1035).

**Sidebar, top to bottom**
1. Title label `"Shiloh Hill\nObservatory\nPhotometry\n(SHOBS-P)"` (bold 13 pt).
2. Subtitle (changes with mode):
   - Variables: `"Light curves, periods,\nand AAVSO reports"`
   - Transits: `"Exoplanet transits:\nfit, planner, ExoFOP"`
   - Discovery: `"Every star in the field,\nlooking for variables"`
3. Small heading `"MODE"`, then three mode buttons (`ttk.Button`, style `Mode<key>.TButton`, each painted in its own colour): `"Variables"` (navy `#1f4464`), `"Transits"` (green `#2a6340`), `"Discovery"` (plum `#63295f`). The active one is prefixed `"▶  "`; the others are indented with spaces (`set_mode`, l.1178-1179).
4. Small heading `"STEPS"`, then five step buttons labelled `"1   Input"`, `"2   Blink"`, `"3   Calibrate"`, `"4   Photometry"`, `"5   Output"` (`STEPS`, l.177). The current step is prefixed `"●  "` (`show_step`, l.1124-1126). While the Credits page is shown no step carries the dot (`show_credits`, l.1081).
5. Bottom of sidebar: button `"Credits and sources"` and label `"Version 2.2.17"`.

**Banner** (one bold white line, mode-coloured; `MODES[...]["banner"]`, l.164-175):
- Variables: `"VARIABLES  ·  light curves, periods, color, and AAVSO reports"`
- Transits: `"TRANSITS  ·  transit fit (MCMC), planner, nearby-binary check, and ExoFOP package"`
- Discovery: `"DISCOVERY  ·  measure every star in the field and flag the ones that vary"`

**Page area**: the five step pages, the Discovery output page, the Transit page (`transit_ui.TransitPage`) and the Credits page are all stacked with `place()`; `show_step()` lifts the right one. Step 5 "Output" shows the Variables output page in Variables mode, the Transit page in Transits mode and the Discovery output page in Discovery mode (`show_step`, l.1100-1106).

**Status bar** (two lines, dark mode colour; l.1032-1053)
- Line 1: the status text (`self.status`), initially `"Choose the calibration and light folders to begin."`. Every `log()` call also copies its first line here (`log`, l.1241-1250). Beside it an initially hidden button that appears after a report save and offers the matching upload page (e.g. AAVSO WebObs / ExoFOP); it disappears with the next status message (`status_link`, `_status_changed`, l.1043-1045, 1565-1580).
- Line 2 (dim, fixed): `"© Shiloh Hill Observatory    Built on Astropy, photutils, ccdproc, NumPy, SciPy, Matplotlib    Data: Gaia (ESA), APASS, Tycho-2, AAVSO VSX/VSP, SIMBAD, VizieR (CDS)    Full citations and acknowledgements: Credits and sources (sidebar)"`.

### 1.3 Mode selector behaviour (`set_mode`, l.1140)
- Clicking the current mode does nothing. While a job runs: info box `"Wait for the current run to finish (or cancel it) before switching modes."` and no switch.
- Switching between Variables and Transits while a series of the *other* kind is open asks (yes/no):
  `"<series file name | The open series> is a <Variables|Transits> series. Switching to <new mode> closes it here (the file on disk is not changed), so nights reduced next start a new series.\n\nSwitch?"` — **No** cancels; **Yes** then offers to save unsaved points (`_offer_series_save("switch modes")`; declining that cancel also aborts the switch), closes the series and redraws Output.
- A popped-out Photometry image is brought back first (l.1171-1172).
- The whole frame is recoloured (`_apply_theme`), subtitle/banner updated, Input and Photometry pages re-laid out for the mode, and you **stay on the step you were on** (2.2.9). Switching to Discovery marks a field summary as due when Photometry is next shown.
- Mode is remembered in the settings file (`settings["mode"]`). Status: `"<Mode> mode. Calibrated frames and marked stars are kept."`

### 1.4 Step bar behaviour (`show_step`, l.1092)
- Any step can be clicked at any time (buttons are never disabled).
- Going to Blink reloads the frame list if the Blink folder changed since the last load, otherwise refreshes the "set aside" list (l.1107-1110).
- Going to Photometry with a calibrated (debayered) preview auto-runs the plate solve once per frame (`_auto_solve` after 150 ms) so stars can be picked without pressing anything; in Discovery a field summary is shown after a mode switch (l.1115-1123).
- Leaving Photometry brings a popped-out image back (l.1093-1094).
- A job in progress blocks a second one: `"Another step is still running. Wait for it to finish."` (`_start_job`, l.749). The cursor turns into the busy "watch" while a job runs.

### 1.5 Credits page (`_page_credits`, l.1055; `show_credits`, l.1076)
- Reached from the sidebar button `"Credits and sources"`. Heading `"Credits and sources"`; hint `"Every library, catalog, service, and published method this app uses. Quoted sentences are the acknowledgements each source asks for; please include them when you publish results made with it."`
- Read-only scrolling `Text` box filled from `core.credits_text()` (core: l.6923).
- Bottom row: `"Save credits as text…"` (left) — saves `SHOBS-P_credits.txt` via the usual output-path chooser (into the Output folder if set, else a Save dialog), writing `"<APP_TITLE> <APP_VERSION>"` + the credits text; status `"Wrote <path>"`. `"Back"` (right) — returns to the step you came from.

### 1.6 Settings file `~/.shobs_p_settings.json` (`SETTINGS_FILE`, l.160; `_load_settings` / `_save_settings`, l.1193-1206)
JSON, written with indent 2, silently ignored if unreadable. Keys written by the frame / Input / Blink / Calibrate code:
- `mode` — last mode (`set_mode`).
- `site_lat`, `site_lon`, `site_elevation` — typed once on the Input page, remembered (`_remember_site`, l.1311). Start blank for a new user (never shipped in program files; comment l.515).
- `telescope_name` (`_remember_telescope`, l.1320); `aperture_mm` and `use_scint` (scintillation term; `_remember_scint`, l.1346; a 2.2.2 value in cm is converted once — `core.aperture_mm_from_settings`).
- `blink_seconds` — Blink speed (`_remember_blink_speed`, l.2917).
- `stretch_blink`, `stretch_photo` — display stretch per page (`STRETCH_PAGES`, l.184).
- `photo_tips` — Photometry step-hint on/off.
- `mono_filter_choice` — hand-set mono filter (Calibrate page "Filter" box).
- `output_dir` is **no longer** remembered (2.2.2): if present it is deleted at start (l.724-726). Frame folders also start blank every launch.
- Other keys (observer, chart, series dir, pop-out geometry…) belong to other pages' surveys.
- Companion log file: `~/.shobs_p.log` (`LOG_FILE`, l.161) — tracebacks only.

---

## 2. INPUT page (step 1) — `_page_input`, l.1603

Heading `"Input"`; hint: `"Frames, target, site, and where the report goes. Comparison, check, and watch stars are picked on the Photometry page. Settings are saved with the series."`

### 2.1 Controls, top to bottom

**Series line** (row under the hint; l.1615-1629)
- Label (wraps): `series_status`. With nothing loaded: `"No series loaded — this run will be a single night. To add a night to a series, Load series… before running."`; with a series: `"Series: <file or 'not saved yet'> — N nights, last <date>. After the run you'll be asked to add the night."` (`series_status_text`, l.9954).
- Button `"Load series…"` (right) — tooltip: `"Load series… replaces what is open: the open series is set aside (you are offered a save first if it has unsaved changes), and a series of another star clears this target's comps, check and watch stars."` (`load_series`, l.10174 — detail belongs to Output survey).
- Button `"Close series"` (far right; disabled when no series is open) — sets the open series aside, file on disk untouched; if unsaved: yes/no/cancel `"<name> has changes that are not saved | (N night(s)) has not been saved.\n\nYes: save the series first, then close it.\nNo: close it without saving.\nCancel: keep it open."` Status: `"Series closed. The next run is a single night (a new series)."` While a job runs: `"Wait for the current run to finish (or cancel it) before closing the series."` (`close_series`, l.10111).

**Group `"Frames"`** (l.1638-1647) — five `_folder_row`s (l.1252). Each row: label (width 12), `Entry` (path, typeable), `"Browse"` button (folder dialog `"Select <Key> folder"`), for Bias/Dark/Flat also `"Master file…"` (file dialog `"Select a SHOBS-P master bias|dark|flat"`, FITS filter), `"✕"` clear button, and a count label.
- `"Bias"`, `"Dark"`, `"Flat"`: count label starts `"0 files"`; after Browse/Scan: `"N files"`, `"N files + master"` (a saved SHOBS-P master exists in the folder or its `SHOBS-P masters` subfolder) or `"master file"` (the entry is a single master file) (`_count_text`, l.1278). `"✕"` → status `"<Key> folder cleared. Calibration will skip it."`
- `"Master file…"` accepts only a master SHOBS-P itself built; otherwise error box: `"<file> is not a master SHOBS-P built. Masters from PixInsight or other software are not used, because how they were scaled and normalized is not known. Choose the folder of raw <kind> frames instead (SHOBS-P builds its master there and reuses it)."` or `"<file> is a master <x>, not a master <kind>."` or `"<file> cannot be read (...)."` (`core.check_master_file`, core:917). Status on success: `"<Key>: SHOBS-P master <file>"`.
- `"Lights"`: `"N files"`; after Scan may read `"N files (+M cal skipped)"` or `"N files (M set aside: another field)"` or `"… (K unreadable)"`.
- `"Output"`: count label blank, or `"in use"` once a folder is set. Browse → status `"Save buttons now write into <folder> (never over an existing file)."`; clear → `"No output folder: each Save asks where to put the file."` (`_output_changed`, l.1395).
- Hint under the rows: `"Output: where every Save button writes (reports, light curves, plots, packages). Leave it empty to be asked each time. Files already there are never overwritten; a new copy gets _2, _3 …."`
- Changing any of Bias/Dark/Flat/Lights discards masters, calibrated preview, field solution, chart labels, suggested comps (`_folders_changed`, l.763); changing Lights also clears every star marker and sets the pick mode back to Target (`_clear_marks`, l.785).
- Files recognised: `.fits .fit .fts .fits.gz .fit.gz .fits.fz .xisf` (`core.IMAGE_EXTS`, core:86-89). Sorted by name. Files named `SHOBS-P_master_*` are never stacked as raw frames.

**Left column**

Group `"Target"` (Variables and Transits; hidden in Discovery — `_layout_input_for_mode`, l.2011)
- `"Star ID"` — `Entry`. **Enter or leaving the box (FocusOut) looks the star up automatically** if the name changed since the last lookup (`_auto_lookup`, l.2356). Buttons `"Look up again"` (forces the lookup; empty box → info `"Enter a Star ID or designation first."`) and `"Clear target"`.
- Hidden row under Star ID that appears when the typed name is only mis-spaced/mis-cased: amber label `"AAVSO spells this “HD 219134”"` + button `"Use it"` (replaces the text, status `"Star ID set to <name>."`, and looks it up) (`_update_name_hint`, l.2036; `core.suggest_star_name`, core:8477).
- `"RA (hours)"` `Entry` + live sexagesimal label to its right; `"Dec (degrees)"` `Entry` + sexagesimal label; hint `"J2000 frame, proper motion to today"`.
- Lookup (`lookup_star`, l.2363; SIMBAD + VSX in a thread): status `"Looking up <name> in SIMBAD and VSX…"`, then on success fills RA/Dec, remembers VSX period/epoch/type, status `"<name> from <source>: RA h, Dec ° at the current epoch   VSX period x d (TYPE)"`; in Transits mode it also looks for the star's planets (see Planet group). Quiet failure (auto lookup): status `"Could not look up <name> (<error>). Type RA/Dec, or let the plate solution fill them, or press Look up again."`; failure after the button: error box with the message. If the image is already solved, the Target marker is placed automatically (`_try_auto_target`).
- `"Clear target"` (`clear_target`, l.2052) empties Star ID/RA/Dec, **all comps C1–C10 and the check (names, mags, markers)**, suggested comps, spare marks, watch stars added this session, Chart ID; status `"Target, comps, and check cleared. Type the new Star ID and press Enter to look it up."`

Group `"Field centre (only needed when the headers do not say)"` — **Discovery only**, and only when the first light has neither WCS nor pointing keywords (`_check_lights_pointing`, l.2349). Hint: `"These lights have no plate solution or pointing in their FITS headers. Type roughly where the frame points (a few arcminutes off is fine) so the field can be solved."` Same `"RA (hours)"`/`"Dec (degrees)"` entries (shared variables).

Group `"Planet (Transits)"` — **Transits only** (l.1705-1737)
- `"Planet"` `Entry` — auto lookup on Enter / keypad Enter / FocusOut when the name changed (`_auto_planet_lookup`, l.1774). Buttons `"Look up again"` (empty → info `"Type the planet's name first, under any of its names (for example TrES-3 b, KOI-217 b or Kepler-71 b, TOI-2046 b, EPIC 220504338 b)."`) and `"Tonight's transits…"` (opens `transit_ui.FinderWindow` — Transits survey).
- Two-column entries: `"Period (d)"` / `"T0 (BJD_TDB)"`; `"Duration T14 (h)"` / `"Depth (%)"`; `"Rp/R*"` / `"a/R*"`; `"Impact parameter b"` / `"Star Teff (K)"` (hint `"for limb darkening"`).
- Source line (hint, initially `"Type the planet's name and press Enter (NASA Exoplanet Archive; TOIs also from ExoFOP), or type the values."`). After lookup: `"<planet> (you typed X); also known as …, from <source> (<reference>); star V 10.2, TESS 9.8; filled RA/Dec and Star ID. [a/R* derived from the duration.]"`. Quiet failure: `"Could not look up <name> (<error>). Check the spelling and press Look up again, or type the values."`; button failure: error box `"Planet lookup failed:\n<error>"`.
- Amber `report_note` line under it (set elsewhere; Transits survey).
- Multi-planet system: modal window `"<APP_TITLE> — choose a planet"`: `"<system> has N known planets. Which one did you observe?"`, one button per planet (non-transiting ones suffixed `"   (does not transit)"`), `"Cancel"` → source line `"Lookup cancelled."` (`_choose_planet`, l.1916). A Star ID lookup in Transits mode auto-fills the planet when the star has exactly one (transiting) planet, else opens this chooser; no planets → `"No known planets for <star> in the NASA Exoplanet Archive (type the planet's name if it has another)."` (`_planets_for_star_done`, l.1901).
- A planet of a **different** host than the current Star ID (positions > 60″ apart) replaces Star ID and RA/Dec and clears the old field (log `"Cleared the old field (...): the planet X orbits Y, not Z."`) (l.1834-1856).

**Right column**

Group `"AAVSO report"`: `"Observer code"` `Entry` (width 14); `"Chart ID"` `Entry`, hint `"na for non-AAVSO comps"`.

Group `"Site"`: `"Latitude (°, north +)"`; `"Longitude (°, east +)"` hint `"west is negative"`; `"Elevation (m)"` hint `"for exoplanet reports and scintillation"`. All three remembered in the settings file as typed (`_remember_site`, l.1311) — but not while a borrowed setup (another site's frames) is in use.

Group `"Optics"`: `"Telescope"` `Entry` hint `"blank = from the header"` (remembered; used for ExoFOP/night setup since the header TELESCOP is often the mount); `"Telescope aperture (mm)"` hint `"for the scintillation error"` (remembered); `"Focal length (mm)"` (default 2900); `"Pixel size (µm)"` (default 3.76); `"Binning"` read-only `Combobox` 1/2/4 with hint `"same-color pixels only; colors stay separate"`; `Checkbutton` `"Include scintillation (atmospheric twinkling) in the error bars"` (default on, remembered); red hint line `aperture_hint` (e.g. warns when the aperture is blank/odd — `core.aperture_mm_hint`).

**Bottom nav row**: `"Scan folders"` (left), `"Try it with sample data…"`, `"Next: Blink"` (right).

### 2.2 `"Scan folders"` — what happens (`scan_folders`, l.2420)
1. A popped-out Photometry image is brought back.
2. Each of Bias/Dark/Flat/Lights is counted; unreadable files (RAW, TIFF, JPEG…) are counted in the label and, when the folder holds nothing readable, logged: `"The <Key> folder holds N file(s) SHOBS-P cannot read (for example <name>). It reads FITS and XISF: save or export them in one of those."`
3. Lights are split (`core.split_lights`, core:6960): masters and stray bias/dark/flat files mixed into Lights are skipped (`"N files (+M cal skipped)"`); but if **more than half** the folder is labelled calibration, all are kept as lights (log note: capture software probably saved the night with the wrong image type).
4. First light's header is read, then in order:
   - `_check_lights_pointing` — Discovery Field-centre box shown/hidden.
   - `_read_mono_filter` — mono filter from FITS `FILTER` (see Calibrate §4.3).
   - `_check_lights_targets` (l.2198) — **two-field check**: samples ~40 headers (all if a second pointing shows up). If frames point at more than one place, yes/no: `"This Lights folder holds frames pointing at more than one place:\n  • N frames at RA Dec (OBJECT …)\n  • …\n\nUse only the N frames near the Input target <star>? (The others are set aside as rejected frames; Blink can bring any of them back.)"` Yes → others added to `rejected`, Lights count `"N files (M set aside: another field)"`. Asked once per folder/grouping. Then **OBJECT check**: the most common FITS OBJECT that is not the target name (and not generic: fov/light/object/target/unknown/none/test/flat/dark/bias) is looked up in a thread; if it resolves to the same star (within 2′) nothing is said. If the frames point at the target anyway: status/log `"The lights' FITS OBJECT says 'X', but they point at the Input target Y. The OBJECT keyword looks mislabelled (the capture program's plan name); the frames themselves are fine."` Otherwise yes/no (default No): `"The lights' FITS OBJECT says 'X', not the Input target Y ('X' is d° away). Check that these are the right lights, or update the target on the Input page.\n\nClear the old target, comps, and check star, and look up X?"` Yes → clear_target + Star ID set to X + lookup. During a run it is only a warning box.
   - `_check_target_field` (l.2144) — **pointing vs target**: if the lights' centre (WCS or header pointing) is farther than max(1°, 2×half-diagonal) from the Input RA/Dec, yes/no: `"These lights point at <RA Dec> (FITS OBJECT: X), d° from the Input page's target, <star> at <RA Dec>.\n\nClear the old target, comps, and check star, and look up X?"` No → log `"Kept <star> as the target although the lights point d° away."` and not asked again for that pair. Yes → clear + lookup of OBJECT.
   - `_header_setup` (l.2676, non-MicroObservatory) — **Site/Optics from header**: (a) header site > 50 km from yours: yes/no `"These lights were taken at latitude …, longitude … (<TELESCOP / INSTRUME>), N km from your site.\n\nUse the site and optics in these frames for them? Your own site, optics and camera settings are kept and come back when you scan a folder of your own lights."` Yes borrows site, elevation, focal/pixel/aperture, blanks Telescope name (status `"Lights from another site: their site and optics are in use."`); No keeps yours (log advises checking Site/Optics). (b) Your Site boxes empty: `"These lights were taken at latitude …, longitude … (<cam>).\n\nIs that your own site? Yes saves it as yours; No uses it for these frames only."` (c) Own frames: empty boxes filled silently (log `"Filled from the lights' FITS header: …"`); values that differ beyond tolerance → yes/no `"The lights' FITS header differs from the Site and Optics boxes:\n  Latitude: yours …, header … (header site)\n…\n\nUse the header values? (No keeps yours and will not ask again for these values.)"` — a No is remembered in settings `header_declined` (last 20). Binning is never taken from the header.
   - Bayer pattern: from header (`core.header_bayer`) → status `"N lights. Bayer pattern from header: RGGB"`. No keyword → `_detect_sensor` (l.2554) looks at up to five raw **flats**: no colour mask → pattern set to `MONO` (status `"Monochrome camera found (no colour mask in the flat): Bayer pattern MONO."`, log tells you to pick the pattern on Calibrate if it is really a colour camera) unless the pattern was picked by hand; colour mask while MONO → log caution. Needs a Flat folder: with no flats, no detection.
   - MicroObservatory frames (`core.is_microobservatory`): yes/no `"These N lights look like MicroObservatory frames, taken in Arizona with a small telescope and a monochrome camera.\n\nUse that telescope's setup for these frames?\n  Site: … \n  Optics: focal length 560 mm, pixel … µm …\n  Camera: monochrome (no debayer), clear filter (CV), binning 1, saturation 4000 ADU\n\nYour own site, optics, and camera settings are kept and come back when you scan a folder of your own lights."` Yes → MONO, luminance, CV filter, sat 4000, binning 1, site/optics set, aperture 152 mm; status `"N MicroObservatory lights: telescope setup in use."` No → log says to set MONO yourself (l.2600).
5. Failure reading the first light: status `"Could not read first light: <error>"`. Otherwise `"Folders scanned."`
- Your own setup is restored automatically the next time you scan your own lights (`_restore_own_setup`, l.2790; log `"Your own site, optics, and camera settings are back …"`).

### 2.3 `"Try it with sample data…"` (`write_demo`, l.12715)
Folder dialog `"Folder for a small synthetic FITS set"`; writes demo bias/dark/flat/lights (`core.write_demo_set`), fills all four folders, Star ID `DEMO VAR`, comp `DEMO COMP` 11.50, check `DEMO CHECK` 12.20, Chart `DEMO`, Observer `TST`, RA 0.75 h, Dec 40.0, binning 1, RGGB, period range 0.03–1 d, apertures 4/7/11, runs Scan, pre-marks the stars. Info box: `"Demo FITS written and folders filled in. The stars are already marked.\n\nAfter Calibrate (which also debayers), they sit at\ntarget (35, 45), comparison (70, 50), check (55, 75)\nbecause superpixel debayer halves the coordinates.\n\nThe demo variable has a <P> d period."` Failure: error `"Could not write the demo set: …"`.

### 2.4 Output folder behaviour (`output_path`, l.1506; `_unique_path`, `_family_path`)
With an Output folder every Save writes there without a dialog, never overwriting: duplicates get `_2`, `_3`… (`.fits.xxx` handled). Report/CSV/PNG of one result share one number so they sort together. Status after save: `"Saved <file> to <folder>."` (no popup); without an Output folder a Save dialog opens and a `"Saved\n<path>"` info box follows (`saved_notice`, l.1527). Unusable folder → warning `"The output folder could not be used (<err>); choose where to save."`

### 2.5 Pitfalls for new users (Input)
- Folders are **not remembered** between launches (2.2.2); Site/Optics/Observer/telescope are.
- Press **Enter** (or Tab out) after typing the Star ID — nothing is looked up on keystrokes; RA/Dec come from the lookup (or later from the plate solution).
- Run **Scan folders** before moving on: it is what reads the header, sets the Bayer pattern/MONO, checks the field, and fills Site/Optics. (`Next: Blink` does not scan; Blink loads its own list and Calibrate reads headers again, but the two-field / OBJECT checks only run from Scan.)
- Longitude is **east-positive** (west negative); MicroObservatory headers are converted automatically.
- Mono detection needs raw **flats**; without a `BAYERPAT`-type keyword and without flats you must pick `MONO` yourself on Calibrate.
- "Clear target" wipes **all** comps and the check as well.
- A single SHOBS-P master file may stand in for a Bias/Dark/Flat folder; third-party masters are refused.
---

## 3. BLINK page (step 2) — `_page_blink`, l.2810

Heading `"Blink"`; hint: `"Raw lights, before calibration. Reject trails, clouds, and bad guiding. Rejected frames are left on disk and skipped by photometry."`

### 3.1 Controls, top to bottom / left to right
**Tool row** (l.2820-2833)
1. `Radiobutton` `"Lights"` / `Radiobutton` `"Flats"` — which folder Blink shows (`blink_source`; default lights). Switching reloads the list from the Lights or the Flat folder (`_blink_folder`, l.2871). Flats cannot be a single master file here (a master-file entry lists nothing).
2. `"Previous"`, `"Next"` — step one frame back/forward, wrapping round (`step_blink`, l.3025).
3. `"Blink"` — toggles automatic playing (press again to pause); `"Stop"` — stops, status `"Blink stopped."` (`toggle_blink`, `stop_blink`).
4. `"Speed"` read-only `Combobox`: `"0.25 s/frame"`, `"0.5 s/frame"`, `"1 s/frame"` (default), `"2 s/frame"` (`BLINK_SPEEDS`, l.178) — remembered in settings `blink_seconds`.
5. `"Reject"` — marks the frame on screen rejected; status `"Rejected frame 222 (21:53:12): <file>."` A frame already rejected stays rejected: status `"Frame 222 (…) is already rejected (Keep brings it back)."` (`reject_frame`, l.3132). (2.2.17: no longer a toggle.)
6. `"Keep"` — un-rejects the frame on screen; status `"Kept frame 222 (…): <file> is used again."` or `"Frame 222 … is not rejected."` (`keep_frame`, l.3144).

**Stretch row** (`_stretch_controls(card, "blink")`, l.3292; display only — never affects measurements): label `"Stretch"`, read-only `Combobox` of `linear` / `log` / `asinh`; label `"black"` + `Scale` slider stepping through 0, 0.5, 1, 2, 3, 5, 10, 15, 20, 30, 40, 50 % with a `"5%"`-style readout; label `"white"` + slider through 90 … 100 % (`STRETCH_*_STEPS`, l.185-186); `"Reset"` → linear 5 % / 99.5 % (`core.STRETCH_DEFAULT`). Redraw is debounced 250 ms; the setting is saved as `stretch_blink`; cached frames are re-stretched in the background from float16 copies without re-reading files (`_blink_stretch_changed`, l.2963). Black ≥ white is ignored.

**Frame line** (`blink_label`): initially `"No lights loaded."`; with a list: `"Frame 222  21:53:12   keep|REJECTED   (n of N in the list, R rejected)   <file name>  exp 120.0s"` (`show_blink`, l.3245). `"No lights in the lights folder."` when the folder is empty; `"<file>  unreadable: <error>"` if a frame cannot be read; `"Reading frame 222 …"` while the preload thread is still working on the next frame during play.
- Frame number = trailing digits of the file name (up to 5), e.g. `Light_…_0222.fit` → `222`; names without a number get `#k` (list position); duplicates get `12a`, `12b` (`core.frame_labels`, core:125). The clock is the **local** time of exposure start from DATE-OBS/JD (`_header_clock`, l.3104).

**Rejected row** (l.2838-2852): label `"Rejected frames: none"` / `"Rejected frames (3): 63, 64, 65"` (`"Rejected flats …"` when showing flats; long lists end `" … (+n more)"`); on the right: label `"Rejected frame"`, read-only `Combobox` of rejected frame numbers, `"Show"` (jumps Blink to that frame, stops play), `"Keep this one"` (un-rejects the picked frame without showing it) (`show_listed_frame`, `keep_listed_frame`, l.3203-3219). With nothing picked: status `"Pick a rejected frame in the list first."` or `"No rejected frames."`

**Image**: a Matplotlib canvas (grey, nearest-neighbour, about 1000 px wide downsample, binned per the Optics Binning value). Title above the image: `"Frame 222  21:53:12   <file>"`, or in red bold `"REJECTED — Frame 222  21:53:12"`.

**Bottom nav**: `"Back"` (→ Input), `"Next: Calibrate"`.

### 3.2 Automatic behaviour
- Arriving on the page loads the list when the folder differs from the one last loaded (`show_step`, l.1107); frames are read ahead in a background thread starting at the current frame and cached (~1.5 GB cap, `BLINK_CACHE_BYTES`) so blinking is just swapping pictures (`_start_blink_preload`, l.2991).
- The Lights list excludes SHOBS-P masters and stray calibration files (`core.split_lights`); the Flats list excludes masters only.
- Rejections are a set of paths (`self.rejected`): kept across pages, honoured by Calibrate/Photometry (`_lights`, l.2874), **not written to disk**, and lost on exit unless saved with a series. Frames set aside by the two-field check on Input also appear here and can be brought back.
- Changing Binning on Input after frames were cached re-reads them at the new binning on the next tick (l.3062).

### 3.3 Keyboard shortcuts
None on this page: Previous/Next/Blink/Reject/Keep are buttons only (no key bindings found; the only global bindings are the mouse wheel on scroll bodies, l.386-400).

### 3.4 Pitfalls (Blink)
- Blink shows **raw** frames (no calibration); hot pixels and vignetting are normal here.
- "Reject" and "Keep" act on the frame **on screen**; the list on the right is the only way to un-reject a frame without finding it.
- Rejections vanish on restart unless the night is saved into a series.
- Changing the stretch changes the look only; photometry reads the original pixels.
---

## 4. CALIBRATE page (step 3) — `_page_calibrate`, l.3389

Heading `"Calibrate"`; hint: `"Master bias, bias-subtracted master dark scaled by exposure, normalized master flat. Each light is then (light − bias − dark) / flat. The first light is then debayered with the channel below and shown on the Photometry page; every light is calibrated and debayered the same way during the run."`

### 4.1 Controls, top to bottom
**Group `"Camera and channel"`** (one row + hint)
- `"Bayer pattern"` read-only `Combobox`: `RGGB` (default), `BGGR`, `GRBG`, `GBRG`, `MONO`. Picking one by hand sets `_pattern_user_set` so the flat-based mono detection will not override it (l.3410); the flag is cleared when a folder changes.
- **Colour camera (pattern ≠ MONO):** label `"Channel"`, read-only `Combobox` `green` (default) / `red` / `blue` / `luminance`, and button `"Apply channel to the preview"` (`apply_debayer`).
- **MONO:** the same spot shows label `"Filter"` and a read-only `Combobox` of `core.MONO_FILTER_CHOICES` (core:1308): `"V (Johnson V)"`, `"B (Johnson B)"`, `"R (Cousins R)"`, `"Clear / L (CV)"`, `"Green imaging (TG)"`, `"Red imaging (TR)"`, `"Blue imaging (TB)"`, `"Sloan g′ (SG)"`, `"Sloan r′ (SR)"`, `"Sloan i′ (SI)"`, `"Sloan z′ (SZ)"`, `"Sloan u′ (SU)"`. The Channel box and Apply button are hidden (`_show_channel_or_filter`, l.2480). Switching to MONO forces channel `luminance`; switching back restores the channel used before (`_pattern_changed`, l.2466).
- Two-line hint label `filter_hint` to the right, e.g. `"AAVSO filter code TG\nGreen channel, V comps"`; on MONO `"AAVSO filter code V\nfilter wheel (FITS FILTER), V comps"` / `"…filter set here…"` / `"…MicroObservatory, clear…"`; with no usable filter `"AAVSO filter code CV\nno filter in the header: clear, V comps"` or `"…filter in the header not supported: clear, V comps"` (`_update_filter_hint`, l.893). Sloan bands print as `g′ i′` etc.
- Hint paragraph: `"Green is the normal choice (AAVSO TG, V comps). Red (TR, R comps) suits a red dwarf; blue is TB; luminance is clear, reported as CV. MONO is for monochrome cameras (MicroObservatory and others): no debayer, and Channel becomes Filter (from the FITS FILTER keyword, or set it here). The pattern is read from the FITS header when the camera writes it; without one, a raw flat with no colour mask sets MONO."`

**Action row**
- `"Build masters and calibrate"` (orange Accent button) — `run_calibration`.
- `"Cancel"` — sets the cancel flag; log `"Cancel requested; stopping after the current frame…"`; when idle, status `"Nothing is running."` (`cancel_job`, l.9513). Cancelled calibration logs `"Calibration cancelled. The old masters (if any) are unchanged."`
- `Checkbutton` `"Rebuild masters (ignore saved ones)"` (default off) — forces a fresh stack instead of reusing saved masters.
- Hint label `debayer_info`: `"No preview yet."` → after calibration `"Preview: green from RGGB  →  3120×2080 (superpixel)"` or `"Preview: monochrome  →  W×H"`.

**Log box**: dark `Text` widget (`log_box`, 18 rows, monospace) — this is the app's main log; every `log()` call writes here (and to the Photometry page's log), trimmed to ~6000 lines. Read-only in practice (no state=disabled, but nothing reads typed text).

**Bottom nav**: `"Back"` (→ Blink), `"Next: Photometry"`.

### 4.2 `"Build masters and calibrate"` — the sequence (`run_calibration`, l.3556)
1. Each Bias/Dark/Flat entry is resolved to raw frames **or** a chosen master file (`core.calibration_source`); rejected flats (from Blink's Flats view) are left out. A chosen master that fails `check_master_file` → error box (texts in §2.1) and stop.
2. Lights = Lights folder minus rejected frames minus calibration/master files; skips are logged (`"Skipped N calibration file(s) found in the Lights folder: …"`, and the "most of the folder is labelled calibration" note).
3. Unreadable files in a folder that yielded nothing readable: `"These folders hold files SHOBS-P cannot read (it reads FITS and XISF):\n  • Bias: N file(s) such as X\n…"` + either error `"\n\nNo light frames can be read."` (stop) or yes/no `"\n\nThose steps would be skipped. Calibrate anyway?"`. No lights at all → error `"No light frames found."`
4. Flats that look already calibrated (PixInsight `_c` files, float pixels, CALSTAT-type keywords) are used as they are; log `"Flats look already calibrated (…): used as they are, without subtracting bias or dark again."`
5. **Pre-calibrated lights** (`core.precalibration_signs`, core:5684: file name contains `cal`/`calibrated` or PixInsight `_c`, floating-point BITPIX, CALSTAT/BIASSUB/DARKSUB/FLATCOR/CALIBRAT keywords): if any calibration input is given, yes/no `"These lights look already calibrated:\n  • <reasons>\n\nCalibrating them again subtracts the bias and dark twice and divides by the flat twice.\n\nSkip bias, dark, and flat for this run? (Recommended)\nThe raw saturation level no longer applies (these values are not camera counts); saturation is judged from flat-topped star cores instead."` Yes → no masters applied (`precalibrated=True`); No → calibrate anyway.
6. **Pre-checks** (`_calibration_checks`, l.3452), skipped for pre-calibrated lights:
   - Dark exposure more than 3× from the lights' (`core.dark_scale_problem`): yes/no/cancel `"Dark folder '<name>':\n\nThe darks are 30 s and the lights 120 s, so the master dark is scaled x4 to match. Scaling up multiplies the dark's own noise and fixed pattern (and any bias mismatch) by the same amount, and stamps it on every light.\n\nYes: calibrate without the dark this time (bias only). A cooled camera's dark current is usually tiny, and the bias still removes the pedestal.\nNo: use these darks anyway.\nCancel: stop, and pick darks closer to the lights' exposure on the Input page."` Cancel → status `"Calibration not started: choose a dark folder that matches the lights."`
   - Flats at mixed rotator angles (spread > 2°, 180° = same position across a flip): yes/no `"The Flat folder mixes camera angles: <summary>.\n\nOne master flat is built from all of them, so it matches none exactly. (Readings 180° apart are the same camera position across a meridian flip and are not counted as different.)\n\nCalibrate with these flats anyway? (No: stop, so you can keep only the flats taken at the lights' angle.)"`
   - Flats vs lights rotator differ > 2°: yes/no `"The flats were taken at rotator angle X° and the lights at Y°.\n\nVignetting and dust shadows from the optics in front of the rotator turn with the camera, so a flat taken at another angle corrects the wrong pattern. Errors of about 1% are typical, more when stars move on the sensor during the night (enough to fake or hide a shallow transit).\n\nCalibrate with these flats anyway? (No: stop, so you can pick flats taken at the lights' angle, or clear the Flat folder.)"`
7. Job starts (status `"Building calibration masters…"`, busy cursor). Log lines: `"Note: no bias, dark frames. That step is skipped."` when folders are empty; `"Building calibration masters. The window stays live; this can take several minutes for 100 full frames."`
8. Background thread (`core.prepare_masters`, core:1034): per kind, a **saved master is reused** when `<cal folder>/SHOBS-P masters/SHOBS-P_master_<kind>_bin<N>[_<exp>s].fits` exists and its fingerprint (names, sizes, mtimes, count), binning and dependent bias/dark still match (`core.find_saved_master`, core:933; a master saved the pre-2.2.13 way beside the frames is found and moved into the subfolder). Log `"Using saved master flat …: <file>"`; otherwise `"Median-combining N bias frames"`, progress `"  median through i/N <file>"`, then `"Saved master flat in the flat folder's 'SHOBS-P masters' subfolder: <file> (reused next time while these frames are unchanged)"` (or `"Could not save the master … continuing without saving it."` on a read-only folder). A chosen master dark still holding bias has the given bias subtracted (log says so); a bias-subtracted master dark with no bias given logs that the pedestal stays in.
9. Masters are checked against the first light (`core.master_mismatches`, core:977): size or binning mismatch → failure `"Calibration failed:\nThe masters do not fit these lights: master flat is …x… px but the binned lights are …x… px; master flat was built at binning 2, the lights use binning 1. Pick the matching frames, or tick Rebuild masters."`; Bayer pattern, gain, offset, sensor temperature (> 3 °C) differences are logged as `"Caution: …"`.
10. First light is calibrated; rotator readings through the night are surveyed (`_rotator_survey`, l.3531): log `"Meridian flip before frame 222 (…° to …°) (the rotator reading changed by 180°). The camera keeps its place … each star can show a small step at the flip. …"` and/or `"Note: the camera rotator moved during these lights: …. One set of flats can only match one angle; expect steps in the light curve there."`
11. `_calibration_done` (l.3699): log `"Masters ready: bias n=51 saved master, dark n=20 exp=120s, flat n=30"` and `"Calibrated preview: <file>  shape (H, W)  median …  NaN px …"`. Bayer pattern re-read from the header; MicroObservatory offer if applicable; camera-binned frames (XBINNING > 1) → log note that some capture programs mix colours when binning a colour sensor. Then the pointing/filter/field checks run again (`_check_lights_pointing`, `_read_mono_filter`, `_check_target_field` — same dialogs as §2.2). WCS in header → log `"The lights carry a plate solution in their FITS headers: Solve uses it directly."`
12. `apply_debayer(auto=True)` (l.3747): superpixel debayer with the chosen channel (MONO = no debayer) → `debayer_info` updated, log `"Preview debayered: green from RGGB, W×H px. Star positions are on this image."`, markers rescaled if the image shape changed, **and the app jumps to the Photometry page automatically** (`show_step(STEP_PHOTO)`), which starts the plate solve. Status: `"Calibrated and debayered. The image is being solved; click the target and the comparison stars (their IDs and magnitudes fill in from the catalogs)."` (Discovery: `"Calibrated and debayered. Solving the field…"`).
- Failure: error box `"Calibration failed:\n<message>"`, full traceback in the log.

### 4.3 Mono filter logic (`_read_mono_filter`, l.847; `_mono_filter_picked`, l.2500)
- On MONO the report filter code and the comps' catalog band come from the FITS `FILTER` keyword when it is a known value (V, B, R, L/Clear, TG/TR/TB imaging filters, Sloan g′ r′ i′ z′ u′); log `"Filter from the FITS header: <raw> → AAVSO filter V, comps in V."` A header filter always wins over the hand-set one.
- No `FILTER` keyword → the hand-set Filter box choice (remembered in settings `mono_filter_choice`).
- Unknown `FILTER` value (e.g. I, Ha) → treated as clear CV with V comps, the box shows `"Clear / L (CV)"`, log `"Caution: the FITS FILTER is 'Ha', which SHOBS-P cannot calibrate (the comp catalogs carry B, V, R and Sloan g′ r′ i′ z′ only). These frames are reported as clear (CV) with V comps."`
- Picking in the Filter box: log `"Filter set by hand: Sloan i′ (SI) → AAVSO filter SI, comps in i′."` Changing to/from a Sloan band after a solve drops the labelled catalog stars and their filled magnitudes (hand-typed names stay) and, if an image is calibrated and nothing is running, re-solves at once; otherwise log says `"Solve again to fetch i′ comps."` (`_comp_band_changed`, l.2522).
- MicroObservatory frames with no FILTER are always clear (CV), never the hand-set filter.
- The report note defaults follow the camera: `"OSC reduced; not transformed"` vs `"Monochrome camera; not transformed"` unless you typed your own (`_sync_camera_note`, l.2591).

### 4.4 `"Apply channel to the preview"` (`apply_debayer`, manual)
Re-debayers the already calibrated mosaic with the current Channel (safe to repeat), clears chart labels/solution/suggested comps, and jumps to Photometry again. With nothing calibrated: info `"Build masters and calibrate first (Calibrate page)."` Bad pattern/channel → error box with the exception text.

### 4.5 Folder/file expectations summary
- Bias/Dark/Flat: a folder of raw FITS/XISF frames (any mix of names), **or** one `SHOBS-P_master_*.fits` built by SHOBS-P. Masters land in `<folder>/SHOBS-P masters/` so other programs that take "every FITS in the folder" never see them (core:802-806). Names: `SHOBS-P_master_bias_bin1.fits`, `SHOBS-P_master_dark_bin1_120s.fits`, `SHOBS-P_master_flat_bin1.fits`.
- Any of the three may be empty; the step is skipped (logged).
- Lights: FITS/XISF; stray calibration frames and masters are skipped; one night, one field (two fields prompt on Scan).
- Binning (Input → Optics) is SHOBS-P's own software binning of same-colour pixels; masters are keyed by it, so changing binning means new masters (saved masters for the other binning stay on disk).

### 4.6 Pitfalls (Calibrate)
- You normally do **not** need to press "Next: Photometry": a successful calibration jumps there by itself.
- If the camera writes no Bayer keyword and there are no flats, the pattern stays `RGGB` — a monochrome camera must be set to `MONO` here by hand, or every frame is "debayered" wrongly.
- Masters are reused silently from `SHOBS-P masters`; after replacing frames with the same names/sizes/mtimes (rare) or when in doubt, tick "Rebuild masters".
- Darks far from the lights' exposure trigger a three-way question; "Yes" means bias-only for this run.
- Third-party masters (PixInsight etc.) are refused on purpose; point SHOBS-P at the raw frames instead.
- Pre-calibrated lights (file names with `cal`, `_c.xisf`, float data) are detected; answer "Yes" to skip calibration or the data are calibrated twice.
- The log box on this page is the place to look when something looks off; the status bar shows only its last line.

---

## 5. Cross-page quick reference
- Settings persisted (this slice): `mode`, `site_lat/lon/elevation`, `telescope_name`, `aperture_mm`, `use_scint`, `blink_seconds`, `stretch_blink`, `stretch_photo`, `mono_filter_choice`, `header_declined`, `photo_tips`.
- Not persisted: the five folder paths, rejected frames, Bayer pattern, channel, binning, focal length, pixel size (unless loaded from a series file or header).
- Dialog defaults: most yes/no questions default to Yes; the OBJECT-mismatch "clear the old target" question defaults to **No** (l.2343).
- Verification entry points: `_build` l.977, `set_mode` l.1140, `show_step` l.1092, `_page_input` l.1603, `scan_folders` l.2420, `_header_setup` l.2676, `_page_blink` l.2810, `show_blink` l.3221, `_page_calibrate` l.3389, `run_calibration` l.3556, `_calibration_done` l.3699, `apply_debayer` l.3747.
