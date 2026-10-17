# Survey: PHOTOMETRY page (all modes) + pop-out image window

SHOBS-P 2.2.17 — code survey for the user's manual. Source: clearv_app.py (page/UI), photometry_core.py, transit_ui.py.
Line refs are approximate (clearv_app.py unless noted). Sections are appended as surveyed.

## 1. Page skeleton and layout (`_page_photo` ~3787–3971; `_layout_photo_for_mode` ~3990–4050)

The page is one Card with, top to bottom:

1. **Title** (`photo_title`): "Photometry" (Variables / Transits modes) or "Photometry: field scan" (Discovery).
2. **Hint paragraph** (`photo_hint`, wraps to card width). Non-Discovery text: "Click the image to mark the target, comps (C1–C10), and check star; Watch adds a star to follow. The image is solved on its own; clicking any star then fills in its ID and magnitude from every catalog (AAVSO sequence first), shown in the panel at right with its color and a variable-star check. Cyan circles are AAVSO sequence stars. Right-click a marker to clear it." Discovery text: "The field is solved as soon as the frame is calibrated (from the plate solution in the FITS header when there is one): catalog stars are circled and known variables marked. Scan field then measures every star on every frame, puts them on the catalog's magnitude scale, and flags the ones that scatter more than stars of their brightness. Click a star to watch it or to leave it out."
3. **Tools row** (`tools`):
   - LEFT, Variables/Transits only (`pick_tools`): four Radiobuttons bound to `pick_mode`: "●  Target" (value target), "●  Comps (C1–C10)" (comp), "●  Check" (check), "●  Watch" (watch), each coloured by its Pick<value>.TRadiobutton style; then Button "Clear selected" (`clear_selected_mark`, ~5759) — clears the marker of the current pick mode (Target/Check: that one marker; Comps: the last comp placed). PITFALL: in Watch mode it does nothing useful — `clear_mark("watch")` touches no watch star (status just says "Watch cleared (and its fields)."); use "Remove selected" in the Watch stars group or right-click the W diamond.
   - LEFT, Discovery only (`disc_tools`): Label "Click a star to", Radiobuttons "●  Watch it" (watch) and "✕  Exclude it" (exclude), hint "(right-click a marker to undo)".
   - RIGHT (packed right-to-left): Accent Button "Run photometry" (`run_photometry`, Variables/Transits) or "Scan field" (`scan_field`, Discovery); Button "Solve again" (`label_aavso`); Combobox (readonly, width 15, `self.catalog`, values `core.CATALOGS`) with Label "Circles" to its left. Since 2.2.13 the Circles box only chooses which catalog's circles/labels are drawn — every catalog is fetched and merged on a solve. Changing it redraws immediately if the chart is placed. In Discovery, if it reads "AAVSO sequence" it is switched to "APASS DR9" automatically (an AAVSO sequence only covers charted variables), and restored when leaving Discovery.
4. **Options row** (`options`): Label "Aperture r" + Entry (`radius`, width 6); "sky in" + Entry (`sky_in`); "sky out" + Entry (`sky_out`); Button "Suggest…" (`suggest_apertures`); Label "raw sat ADU" + Entry (`sat_limit`, width 8); Checkbutton "Also measure R and B (color index; slower)" (`measure_color`). Typing in any of the three aperture boxes redraws the rings shortly after (`_aperture_sizes_changed`, debounced ~5651).
   - RIGHT of the options row: **LabelFrame "View"** (`view_box`) containing three stacked lines:
     - Line 1 Checkbuttons: "Show variables (red ○ ×)" (`show_variables`), "Show apertures" (`show_apertures`), "Show catalog stars" (`show_catalog`), "Show labels" (`show_labels`), and — Discovery only — "Candidates only" (`candidates_only`). Each calls `refresh_preview`.
     - Line 2: the display-stretch controls (`_stretch_controls(view_box, "photo")`, ~3292; see §2).
     - Line 3 (zoom line): Label "Zoom", Button "−" (zoom out ×1.5), Button "+" (zoom in ×1.5), Button "Fit" (`reset_photo_view`), Button "Pop out" (`photo_pop_toggle`). While the image is popped out, Zoom/−/+/Fit are hidden from this line (they move into the pop-out window's bar) and the last button reads "Bring back".
5. **Pick label** (`pick_label`, Hint style; Variables/Transits only): starts "No stars marked."; summarises what is marked (`_update_pick_label` ~6634).
6. **Step strip** (`step_row`, see §3): step labels + "Hide tips" on one line; "what to do now" text under it; a permanent zoom tip line "Wheel to zoom, drag to pan. On a trackpad: the − + Fit buttons in the View box, or Pop out for a bigger window." When tips are hidden only a "Show tips" button remains. The choice is saved in settings (`photo_tips`).
7. **Body**: LEFT the matplotlib image canvas (`photo_canvas`, Figure 8×4.2 in) with a **symbol legend row** under it (`legend_row`, only the marker kinds currently drawn, `_update_legend` ~5225). If matplotlib is missing: Label "matplotlib is not installed." RIGHT a scrolling panel (`ScrollBody`, ~520 px, auto-sized to content, max 40% of screen) holding either the **Stars panel** (`_build_star_panel`, Variables/Transits) or the **Discovery panel** (`_build_discovery_panel`). Mouse wheel over the panel scrolls it; over the image it zooms.
   - When the image is popped out, the canvas area shows `photo_placeholder`: "Image is in its own window (Bring back in the View box)."
8. **Progress row** (`photo_prog_row`, above nav): Progressbar (length 320, determinate), progress text (starts "Idle."), Button "Cancel run" (`cancel_job`), Button "Hide log"/"Show log" (`toggle_photo_log`, ~4939).
9. **Live log** (`photo_log`, dark Text, 7 lines, same messages as the Calibrate page log). Hidden/shown by the toggle.
10. **Nav row**: Button "Back" (to Calibrate page) left; "Next: Output" right.

Mode switching (`_layout_photo_for_mode`): swaps pick_tools/disc_tools, star_panel/disc_panel, Run/Scan button, "Candidates only" box, pick label; resets panel scroll to top; forces pick_mode to "watch" entering Discovery (if not watch/exclude) and to "target" leaving it (if it was exclude); updates title/hint and step hint. If the image is popped out when the mode changes, the pop-out bar is rebuilt (`set_mode` normally brings the image back first).

Pitfalls:
- The "Circles" dropdown does NOT choose which catalog is used for magnitudes — all catalogs are merged; it only chooses whose circles are drawn.
- The Stars panel scrolls: with C1–C10 filled, boxes may be below the fold; wheel over the panel (not the image) to scroll.
- "Run photometry" sits far right on the tools row; "Solve again" is next to it.

## 2. View box: stretch, zoom, binning (`_stretch_controls` ~3292–3387; constants 185–187; core 602–603)

**Stretch row** (inside the View box, line 2). Display only — measurements never see it.
- Label "Stretch"; Combobox (readonly, width 7) with values "linear", "log", "asinh" (`core.STRETCH_MODES`).
- Label "black" + horizontal Scale (12 steps: 0, 0.5, 1, 2, 3, 5, 10, 15, 20, 30, 40, 50 %) + live percent read-out (e.g. "5%").
- Label "white" + Scale (12 steps: 90, 95, 97, 98, 99, 99.5, 99.7, 99.8, 99.9, 99.95, 99.99, 100 %) + read-out.
- Button "Reset" → defaults linear / black 5% / white 99.5%.
- Behaviour: the percent labels update immediately; the image redraws 250 ms after you stop moving (debounce). Black ≥ white is silently ignored (no redraw). Saved to settings as `stretch_photo` (Blink page has its own `stretch_blink`).
- Black/white are percentiles of the image's pixel values, not ADU.

**Zoom line** (line 3): "Zoom" label, "−", "+", "Fit", "Pop out"/"Bring back" (see §1 and §4). `photo_zoom_step(1.5)` zooms out, `(1/1.5)` zooms in, about the current view centre (`_photo_zoom_about` ~5303). "Fit" = `reset_photo_view` (~5280): forgets the stored view and redraws the whole frame.

**Binning** lives on the Input page (Optics box: Label "Binning", readonly Combobox values 1, 2, 4, hint "same-color pixels only; colors stay separate", ~1761). Relevance to Photometry: `_binning_changed` (~4388) — if you change Binning after Calibrate, the status bar and log say: "Binning changed from {old} to {new}: run Calibrate again. The image on the Photometry page, its solution and the star markers are still at Binning {old}." (warned once per new value). `_image_binning` (~4379) reports the binning the shown image was actually made at (`masters_binning`). Pitfall: changing Binning does not re-bin the Photometry image; you must Calibrate again or the solve later fails with "no lock".

## 3. Step strip, auto-solve, auto target (`photo_steps` ~4068; `_update_step_hint` ~4139; `_auto_solve` ~4407; `_auto_target_marker` ~4193; `show_step` ~1092)

**Step strip** (`step_line`): labels joined by "→" arrows.
- Variables/Transits: "1 Solve" → "2 Target" → "3 Comps" → "4 Check" → "Run".
- Discovery: "1 Solve" → "2 Scan field".
- Styles: StepOn (current), StepDone ("✓ " prefix, e.g. "✓ 2 Target"), StepTodo, StepWarn. Step 1 has special texts: "✓ 1 Solved", "1 Solving…", "1 Not solved" (warn style when the solve failed or is impossible).
- Button "Hide tips" (right of the labels) hides the whole strip and leaves a "Show tips" button; remembered in settings `photo_tips`.
- Under the labels, the **what-to-do-now text** (`step_text`), chosen by `photo_steps()`:
  - Not calibrated: "Calibrate first (Calibrate page); the image is solved straight away." (Discovery: "...the field is solved straight away.")
  - Solving: "Solving the image… You can click the target meanwhile (Target)." / Discovery "Solving the field…"
  - Solve impossible (lights give no pointing and no RA/Dec typed): "Not solved: these lights do not say where they point and no RA/Dec is given: click the target on the image (Target), then the comps; or enter the target on the Input page and press Solve again." Discovery: "Not solved: these lights do not say where they point. Type the field centre on the Input page, then press Solve again (top right)."
  - Not tried yet: "The image is solved on its own when this page opens (or press Solve again); you can click the target meanwhile (Target)."
  - Failed: "Not solved: press Solve again (top right) to retry, or click the target on the image (Target), then the comps." Discovery: "Not solved: check the field centre on the Input page and the Optics box, then press Solve again (top right)."
  - Solved, no target: "Solved. Pick Target and click the target star."
  - No comps: "Pick Comps and click comparison stars (green rings: suggested, near the target's brightness)." (the parenthesis only when suggestions exist) + " (Not solved: type their magnitudes.)" when unsolved.
  - No check: "Pick Check and click a check star: a steady star that tests the comps." (+ same unsolved suffix).
  - All set: "Ready: Run photometry."
  - Discovery solved: "Solved. Watch or exclude stars if you like, then Scan field."
- Below that, permanent zoom tip (quoted in §1).

**Auto-solve.** When the Photometry page is shown (`show_step`) with a calibrated, debayered preview and no chart placed yet, `_auto_solve` runs 150 ms later and calls `label_aavso(auto=True)` — once per image (`_preview_token`); a new calibration, binning or channel makes a new image and solves again. Nothing to press. If the app is busy it is skipped and retried on the next visit to the page. "Solve again" (Button, tools row) = `label_aavso()` by hand (see §8).

**Auto target marker** (`_auto_target_marker`). Not in Discovery. Once solved, if the RA/Dec boxes on the Input page hold the *target's* coordinates (not a field centre a solve filled in — `_radec_is_fill`), and no Target marker exists: the nearest catalog star within 5″ (else the plate-solution pixel) is recentred on the real star like a click (`core.recenter_info`) and becomes the Target. Log messages (no dialogs):
- "Target: marker placed on {name} at (x, y) from its RA/Dec and the plate solution[ (after the lookup)]. Click with Target to move it if it is not your star."
- "Target: {name} (RA Dec) is not on this frame by its plate solution; click the target by hand."
- "Target: ... is too close to the edge of this frame to measure (at (x, y)); no marker placed." (edge = sky out + 2 px)
- "Target: no star where the plate solution puts {name}, at (x, y); click the target."
After placing it, suggested comps are computed (`_suggest_comps`) and the panel redrawn (`_try_auto_target` ~4185 also fires after a star-name lookup finishes).

Pitfall: the auto target is placed by the Input-page RA/Dec. If those boxes still hold the field centre from a solve, no auto marker appears and you must click the target.

## 3a. Discovery panel (right side, Discovery mode; `_build_discovery_panel` ~4258; `_field_summary` ~4325)

Group **"Field"**: one wrapped label (`disc_field_text`). Before a solve: "Not solved yet. Calibrate, and the field is solved straight away." After: lines such as "Solved from the plate solution in the FITS header | from star patterns: 1.23″/px, centre HH MM SS ±DD MM SS.", "{N} catalog stars placed (all catalogs merged), V 9.1 to 15.8.", "{N} stars detected at 5σ (the 800-star limit; raise "Most stars to measure" for more).", "{N} saturated on this frame (measured, never candidates).", "{N} known or suspected variables in the frame (VSX + SIMBAD)." Or "Not solved yet (it solves after calibrating; Solve again retries)."

Group **"Scan settings"** (`_field` rows: label, Entry, hint):
- "Faintest magnitude" Entry (`scan_mag_limit`, default blank) hint "blank = all".
- "Detection threshold (σ)" Entry (`scan_sigma`, default 5.0) hint "lower = fainter".
- "Candidate at (× normal)" Entry (`scan_excess`, default 3.0) hint "scatter".
- "Most stars to measure" Entry (`scan_max_stars`, default 800) hint "brightest first".
- Checkbutton "Leave out crowded stars (neighbor in the sky ring)" (`scan_skip_crowded`, default on).
- Hint: "Saturated and edge stars are measured but never listed as candidates. "× normal" compares a star's scatter with stars as bright."

Group **"Watched and excluded stars"**: Listbox (`disc_box`, 7 rows) with lines "●  watch   {name}" and "✕  exclude   {AUID|label|(x, y)}"; Button "Remove selected" (`_remove_disc_selected`: removes a watch star or un-excludes); Button "Known variables in the field…" (`_show_known_in_field`): info dialog "{N} known or suspected variable(s) in the frame:" + up to 40 lines "name/type ... at (x, y)", or if none: "No known variables marked yet: the field check runs when the field is solved (VSX and SIMBAD must be reachable)."

## 4. The image view: drawing, markers, legend, compass, hover, zoom/pan (`refresh_preview` ~4980; `_mark` ~5809; `_update_legend` ~5225; `_draw_compass` ~5161; hover ~5597/11473; zoom ~5289–5322)

**Image**: grayscale, stretched for display only (`core.stretch_display`), cached per image+stretch so placing a marker does not re-stretch. Axis title: "Debayered ({mode}) — click to mark stars, right-click a marker to clear it" (Discovery: "...click to watch or exclude stars...") or, if not debayered, "Calibrated mosaic — choose the channel on the Calibrate page" (clicks are refused then; see §6).

**Markers drawn** (all colours are constants ~62–80, 243–246):
- Target: orange (#ff7f0e) "+" with tag "T". Comps: green (#2ca02c) "+" with tags "C1"…"C10". Check: blue (#1f77b4) "+" with tag "K".
- "Show apertures" on: around each of those a solid circle at Aperture r, dashed circle at sky in, dotted circle at sky out (only if 0 < sky in < sky out). Tag sits just outside the outer ring.
- AAVSO sequence stars (Variables/Transits only, when "Show catalog stars" on): cyan (#38d9e8) circle ms 11 with the chart label (e.g. "91" = V 9.1) when "Show labels" is on; a sequence star that is itself a known variable is drawn red.
- Other catalog stars of the Circles catalog: yellow (#ffd24a) circles, brightest 3000 only (`CIRCLES_MAX`); labels (magnitude to 0.1 or chart label) for the brightest 400 only (`CIRCLE_LABELS_MAX`). Hover shows any star.
- Known variables (VSX/SIMBAD): red (#ff5252) circle when they coincide with a catalog star, red "×" otherwise ("Show variables (red ○ ×)").
- Watch stars: cyan (#17becf) open diamond ms 13 with letter "W".
- Suggested comps (Variables/Transits): larger green ring (ms 16) around stars not yet taken.
- Spare comps: light-blue (#7fd6ff) small diamond.
- Discovery scan candidates: open squares with a number label — magenta bold "Possible new variable", muted purple "Known variable (VSX/SIMBAD)", tan "Candidate to check first", light grey "Unchecked (no sky positions)".
- Excluded stars (Discovery): thick orange (#ff9800) "×".
- "Candidates only" (Discovery) hides everything except scan candidates, watch stars and excluded stars without touching the other View switches (`_overlay` ~5671).
- Circles catalog quirk (`_circles_catalog` ~5098): "AAVSO sequence" chosen on a field with no sequence (most exoplanet hosts) shows APASS DR9, else Gaia DR3, else Tycho-2 instead, so a solved image never looks unsolved; the legend then reads "APASS DR9 star (no AAVSO sequence here)".

**Compass** (`_draw_compass`): small yellow N and E arrows with halo letters, in the least crowded corner, tied to "Show labels". Direction comes from this image's solution (follows rotator/flip). Unsolved: grey text "N/E: orientation unknown\nuntil the image is solved".

**Legend row** under the image (`_update_legend`): one bold coloured entry per marker kind present now: "⊕ Target", "⊕ Comp C1"/"⊕ Comps C1–C3"/"⊕ Comps C1, C3", "⊕ Check", "◎ Aperture and sky ring", "◯ Suggested comp", "◇ Spare comp", "◇ W Watch", "○ AAVSO sequence (label = V, e.g. 91 = 9.1)", "○ {catalog} star[ (no AAVSO sequence here)][ (labels hidden)]", "○ Known variable", "× Variable, not labeled", "□ Possible new variable" etc., "× Excluded from the scan". At the right always: "wheel: zoom · drag: pan · right-click a marker: clear". When popped out, the legend moves to the bottom of the pop-out window.

**Hover tooltip** (`_hover_common`): pointer within 8 screen px of a star shows a cream box: marked stars win ties ("Target: {name}  mag 11.2\npixel (x, y)\nright-click to clear"), then "Watch: {name}\n{B V R mags (B-V, source)}\npixel...\nright-click to stop watching", variables ("VSX: name\ndesc"), spares ("Spare comp: name ..."), catalog stars ("{AUID|label}\n{catalog}: B 12.1  V 11.5 ... (B-V 0.6, APASS DR9)\npixel (x, y)[\n⚠ variable text]"), scan candidates ("Scan candidate #3 — possible new variable\n{note}\npixel...").

**Mouse / keyboard** (`_photo_press_event`/`_photo_motion`/`_photo_release_event` ~5558–5595; `_photo_scroll`):
- Mouse wheel over the image: zoom ×1.3 about the pointer (up = in). Zooming out past the full frame snaps to the whole frame; cannot zoom in past 8 px wide.
- Left (or middle) press + drag ≥ 6 px: pan. A left press released without moving: a click (marks a star per pick mode).
- Right-click: `on_image_click` with button 3 — clears the marker under the pointer (or stops watching / un-excludes).
- "−"/"+" buttons: ×1.5 about the view centre. "Fit": whole frame.
- In the pop-out window only: keyboard "+", "=", keypad "+" zoom in; "−", keypad "−" zoom out.
- Zoom and pan survive redraws (marker placed, labels toggled, a run); only a *new image* (new calibration, binning, channel) resets to the whole frame.
- Mouse wheel over the right-hand panel scrolls the panel, not the image.

## 5. Pop out / Bring back / Go back (`photo_pop_out` ~5404; `photo_pop_toggle` ~5465; `_build_popout_bar` ~5487; `photo_bring_back` ~5523)

- Button "Pop out" (View box zoom line) opens a Toplevel titled "SHOBS-P — Photometry image" (`APP_TITLE` + " — Photometry image"), resizable, min 320×240 (scaled). First time ~60% × 75% of the main screen, centred; afterwards the size/place it had last time (settings `photo_popout`, saved 700 ms after each move/resize; a window kept on a second monitor stays there unless its title bar would be unreachable).
- Same figure, so nothing to sync: clicks, drag, wheel, hover work identically; markers appear on the page too when brought back.
- Pop-out **bar** (top row): LEFT the pick Radiobuttons mirroring the page ("●  Target", "●  Comps (C1–C10)", "●  Check", "●  Watch" + Button "Clear selected"; Discovery: Label "Click a star to", "●  Watch it", "✕  Exclude it") on the *same* `pick_mode` variable, so setting it in either place sets both. RIGHT: Label "Zoom", "−", "+", "Fit", and Button "Go back".
- Bottom of the window: the symbol legend row.
- On the page meanwhile: the canvas is replaced by "Image is in its own window (Bring back in the View box).", the Zoom/−/+/Fit widgets are hidden and the Pop out button reads "Bring back". Status bar: "The image is in its own window: resize it as you like. Close it (or Bring back) to put it back on the page."
- The image comes back on: "Go back", "Bring back", closing the window (WM_DELETE), starting a run (`run_photometry`/`scan_field`), a mode switch (`set_mode`), or leaving the Photometry step (`show_step` ~1093). The page button toggles (reads "Bring back" while out); `photo_pop_out` itself, if called while the window exists, only raises it.
- Pitfall: the pop-out has no Suggest…/aperture boxes/Run — those stay on the main page.

## 6. Clicking stars: pick modes, recentring, fill-in, warnings (`on_image_click` ~5857–5966; `_fill_from_chart` ~6643; `_vet_choice` ~6140; `_no_catalog_v_note` ~5968; `_comp_brightness_note` ~7601)

**Prerequisite**: the image must be calibrated and debayered. A left-click on a mosaic gives the dialog: "Calibrate first (Calibrate page). Star positions are measured on the calibrated, debayered image." (OK).

**Recentring (snap)**: every left click is recentred on the star *nearest* the click (not the brightest nearby), with a search size set by the measured seeing (`core.recenter_info`). Status notes: " Centred on the star (2.3 px from the click)." or " No star found right at the click; the marker is where you clicked."

**Pick modes** (`pick_mode` radio, page or pop-out bar):
- **Target**: sets `target_xy`; if solved, suggested comps are computed (green rings). The Target name box ("Star ID") is *not* filled from the catalog by a click (`_fill_from_chart` returns "" for target) — it is the Input page's Star ID.
- **Comps (C1–C10)**: each click adds the next comp. Clicking a star already a comp: status "That star is already C3." (nothing else). Slot choice (`_next_comp_role` ~5777): a star whose catalog name matches a slot left over from a loaded series (name but no marker) goes back into *that* slot; otherwise the first slot with neither marker nor name; otherwise the first slot without a marker. Before a solve: simply the first slot without a marker (C1 first). With all ten marked: dialog "All 10 comparison stars are marked. Right-click one to remove it first." (OK).
- **Check**: sets the single check star (replaces the old one).
- **Watch**: `add_watch_at` — adds a watch star named by its catalog AUID/label, else "watch N (x,y)"; status "Watching {name}." plus, when it is not a catalog star and the image is unsolved, " It is not a labeled catalog star, so click it again on later nights." Clicking an already-watched star updates its position: "{name} is already a watch star; its position on this frame was updated." (row highlighted in the Watch list).
- **Exclude it** (Discovery): adds to `scan_excluded`; status "{name} is left out of field scans (right-click its orange × to bring it back)."
- In Discovery, Target/Comps/Check clicks do nothing.

**Fill-in after a comp/check click** (`_fill_from_chart`, reach 8 px): if a catalog star is under the marker, its name (AUID or label) goes into the slot's name box and its magnitude (3 decimals) into the magnitude box; bands, colours, RA/Dec and AAVSO sequence AUID are remembered (`star_bands`, `comp_coords`, `seq_auids`). Status: "C1: 000-BBC-123  V = 11.234 (AAVSO sequence, ...)" or with another catalog "(APASS DR9)". If the star has no magnitude in its catalog: "{name} has no V magnitude in {catalog}. Pick another star." Clicking a **spare comp's blue diamond** adopts that spare's own record instead (`_spare_at`/`_fill_from_spare`): status "... (APASS DR9, spare comp)".
- The report's CHART field (Input page) is set automatically to the AAVSO chart ID when any marked comp/check is a sequence star, else "na" (`_update_chart_field` ~6770); a hand-typed chart ID is left alone.

**No catalog magnitude** (`_no_catalog_v_note`): on a solved image a comp with an empty magnitude box gets a status/log/panel note "C2: {name} has no V in {catalog}; type its V or pick another star" or "not in the downloaded catalogs; type its V or pick another star". (Band label follows the filter: B, R, Sloan.)

**Variable-star vet** (`_vet_choice`, comps and check only): if the star is a known/suspected variable (VSX/SIMBAD, by catalog flag or by position within ~6″), Yes/No dialog: "{label} is a known or suspected variable:\n  {variable text}\n\nA variable comparison star (C2) [check star] shifts every measurement it touches. Use it anyway?" — **No** clears the marker and fields; **Yes** keeps it (panel line shows "⚠ ..." in red).

**Comp brightness/colour notes** in the panel (`_comp_brightness_note`): "✓ +0.3 mag from the target"; "⚠ 2.1 mag brighter than the target: a much brighter comp reacts differently to airmass, focus, and calibration errors, and can leave steps or trends. Prefer one within about 1 mag — green rings."; "⚠ 1.8 mag fainter than the target: its noise will dominate. Prefer one within about 1 mag."; "closest available: 1.7 mag fainter (no field star is near this target's brightness)"; "⚠ B−V 1.20 against the target's 0.55: a comp of another color dims differently with airmass, which leaves a slow trend. Prefer one within about 0.5 in B−V."

**Sequence note** (log only, once per comp set, `_sequence_note` ~6749): "Note: AAVSO sequence stars are on this frame (suggested: ...), but C2 is not in the sequence. That is fine; AAVSO reports are more comparable with sequence comps."

**Right-click** (any mode): clears the nearest marker within max(2×aperture r, 12 px): a T/C/K marker (`clear_mark` → status "C2 cleared (and its fields)." / "Target cleared."), a watch star ("Stopped watching {name}. Its measurements stay in the series."), or an exclusion ("Star back in the scan."). Nothing near: status "Right-click on a marked star (T, C1…C10, K, W) to clear it."

**Pick label** (`_update_pick_label`): "Target (x, y)    Comps C1, C2    Check (x, y)    Watch 2 of 3 on this frame" ("—" for unset).

Pitfalls:
- Clicking in Target mode never fills the Star ID; type it on the Input page (the auto marker uses that RA/Dec).
- Comps accumulate: to replace a comp, right-click it (or "✕" in its row) first.
- A click on blank sky still places a marker ("No star found right at the click") — watch the status bar.

## 7. Stars panel: comp slots C1–C10, typed names/coordinates, check star, Watch stars (`_build_star_panel` ~6156; `update_star_panel` ~6245; `_slot_lookup` ~5984; watch ~6366–6632)

**Group "Stars"** (grid). Header labels: "Star ID" over column 1, and the magnitude header (`mag_header`, "V magnitude" / "B magnitude" / "r′ magnitude"… following the filter, set ~907) over column 2. Rows: "● Target", "● C1" … "● C10", "● Check" — each row: coloured role Label; Entry name (width 30; Target's is the Input page's `star_id`); Entry magnitude (width 8; not for Target); Button "✕" (width 3, `clear_mark(role)`: marker + name + magnitude cleared); and under it a wrapped status line (Hint / Good green / Warn / Bad red styles).
- C1 always shows; C2…C10 appear as they are used, plus **one empty slot after the last used one** to type into. Its status line reads "type a name or RA/Dec and press Enter, or click the star".
- **Typing a name/coordinates** into an empty comp or check slot then **Enter / keypad Enter** (`_slot_lookup`): search order = this image's catalog stars by any catalog name (tolerant of spacing and catalog prefix, e.g. "TYC4006-1310-1", "J231144.7+570632"), then parsed coordinates ("23 13 48.1 +57 07 34" or degrees "348.4504 57.1261"), then a J-designation's own coordinates, then SIMBAD/VSX in a background thread ("Looking up {text} in SIMBAD and VSX…"). Outcomes (status bar + log + slot line): "✓ C2 placed on {text}; type its magnitude.  Marker at (x, y)." or "✓ C2: {name}  V = 11.234 (APASS DR9)  Marker at (x, y)."; "⚠ {text} is not on this frame (RA Dec)."; "⚠ {text} is already marked as C1."; "⚠ {text} not found (not in this image's catalogs, and SIMBAD/VSX said: …). Check the name, type RA Dec, or click the star on the image."; "⚠ Calibrate first, so {text} can be found on the image."; "⚠ The image is not solved yet, so {text} cannot be placed from its name or position. Click the star on the image (Solve again if the solve failed)."
- Enter on a slot that already has a marker only renames: "✓ C2 renamed to {text}; the marker stays where it is (✕ clears it, then Enter places the name)."
- While typing (before Enter) the line says "press Enter to find and mark this star". A name left from an earlier run/series with no marker on this image: "⚠ not marked on this image: click the star, or ✕ to clear the name" (Warn style). The Target row has no Enter lookup.
- Status line contents for a marked comp/check: "V from APASS DR9 · AAVSO sequence 112", "V from Gaia DR3 (from G and BP-RP, ±0.03)", "✓ B−V 0.62 (APASS DR9)" (green) or "⚠ very red, B−V 1.62: red giants often vary, and the color is far from most targets" (B−V > 1.5), "no catalog B−V for this star", "spare comp (widened window)", the brightness/colour notes (§6), then "not a known or suspected variable (VSX + SIMBAD[, checked by position])" or "⚠ {variable text}" (Bad red) or "variable check: Solve first" / "variable check: mark it on the image to check it by position". Target row: "Listed: {variable text}" when the target itself is in VSX/SIMBAD.
- Below the rows: `field_check_text`, initially "Variable-star check: Solve to check the field against VSX and SIMBAD." (updated after a solve by `_apply_field_check` ~7633).

**Group "Watch stars"**: hint "Pick Watch and click a star, or Add a star… by name or RA/Dec. Watch stars are measured every run, found again after Solve on later nights, and have their own light curves (Output page)." Listbox (`watch_box`, 6 rows) with lines "{name}  V 11.23  —  on this frame" / "—  not found on this frame". Buttons: "Add a star…" (`add_watch_dialog`), "Remove selected" (`_remove_selected_watch`), "Watch light curves…" (`show_watch`, Output-side window; out of this slice).
- "Add a star…": if not calibrated → info "Calibrate first (Calibrate page), so the star can be found on the image." Else a text prompt: "Star to watch: a name (V1441 Cas, NSV 14464, TYC 4006-946-1, a Gaia ID on the labeled chart),\nor RA Dec (23 13 48.1 +57 07 34, or degrees: 348.4504 57.1261)." Lookup order (`add_watch_lookup`): existing watch names (→ info "{name} is already a watch star.\n\nIts row is highlighted in the Watch stars box."), the image's catalog stars, the field's known variables, parsed coordinates, then SIMBAD/VSX (failure → error dialog "Could not find {text}: {exc}"). A star within 4″ of an existing watch star is the same star ("That star is already watched as {name}."). Statuses: "Watching {name} at pixel (x, y).", "Watching {name}. Solve to find it on the image.", "Watching {name}, but it is not on this frame."
- After every solve, watch stars are re-found by name then sky position (`_locate_watch_stars`): log "Watch stars: 2 of 3 found on this frame (click the missing ones with Watch)."
- Right-clicking a W diamond or "Remove selected" → "Stopped watching {name}. Its measurements stay in the series."

Pitfall: a watch star added by click on an *unsolved* image has no sky position and must be clicked again on later nights.

## 8. Solve / "Solve again" (`label_aavso` ~6826–7134; `_label_done` ~7239–7434; `_suggest_comps` ~7507; `_apply_field_check` ~7633; `_fill_marked_from_chart` ~7445)

What "Solve" does: fetches **all** catalogs at once (AAVSO sequence/VSP chart, Gaia DR3, APASS DR9, Tycho-2 — `core.fetch_field_catalogs`, merged by `core.merge_catalogs`), detects stars on the preview, matches, places circles, then runs one VSX + SIMBAD field query to flag variables. Runs as a background job; status bar reads "Solving: fetching AAVSO, Gaia, APASS and Tycho stars (NN′ field, V band)…" then "Catalogs: AAVSO ✓ APASS ✓ Tycho ✓ Gaia… 18 s". Cancel via "Cancel run".

Where it points the catalog query, in order: the FITS header's plate solution (WCS) → (Discovery) header RA/DEC pointing → Input-page RA/Dec → header pointing. Solving strategy: header WCS first (`core.wcs_solve`); else, without a target marker, a blind pattern solve (`core.solve_field`); with a marker, chart matching anchored on the Target marker (`core.match_chart`), then neighbours of the marker, then a pattern solve as last resort. Very few stars (<20) without a WCS → stacks the first 30 lights first ("Only N stars on this frame: stacking 30 frames to find more…").

Side effects on the Input page: empty RA/Dec boxes get filled with the field centre (or the marked target's own position via the WCS); `_radec_filled` remembers this so the auto target does not mistake a field centre for the target. An automatic solve in Variables/Transits with no target yet leaves RA/Dec alone.

**Dialogs/messages** (manual "Solve again" shows dialogs; the automatic solve logs instead):
- Not calibrated: "Calibrate first (Calibrate page)." (manual only).
- No pointing at all: "Solve needs to know roughly where the frame is: the target's RA/Dec on the Input page, or the field center. These frames' FITS headers have no RA/DEC. Type the field center on the Input page (from your planetarium program or the mount; a few arcminutes off is fine) and try again." Auto: status "Field not solved: these lights do not say where they point. Type the field centre on the Input page, then Solve again." The Input page's field-centre box is then shown (`field_centre_needed`).
- Bad optics numbers: error "Check RA, Dec, focal length, and pixel size: {exc}" / status "Field not solved: check RA, Dec, focal length, and pixel size (...)".
- Catalog failure: error "Solve: {error}" / log "Solve: the catalog request failed (...). Click Solve again to retry."; status "Not solved: the catalog request failed."
- No lock (manual): info "The catalog stars did not lock onto this frame ({hits} of {n} matched, {needed} needed).\n\nTarget marker: (x, y)   stars found in frame: N\nPlate scale used: 1.234"/px (camera bin 1, app bin 1, superpixel x2)\n\n[Only N stars were found on this frame...: too few to match a catalog. Very short exposures or a small telescope do this; mark the stars by hand and type their names and magnitudes.]\n\nThe plate scale used is ..."/px, from the focal length and pixel size in the Optics box: for another telescope's frames, enter its values.\n\nCheck that the Target marker is on your star, then RA/Dec, focal length, pixel size, and binning.\nYou can always type the comp and check magnitudes in by hand." Auto: the same in the log + "Check binning too, then Solve again. You can mark stars meanwhile; their IDs and magnitudes fill in once the image is solved." Status: "Not solved: no lock (h of n catalog stars matched). Clicks still work; IDs fill in once the image is solved."
- Header solution disagrees with the Target marker by > max(3×r, 8) px (manual): info "The plate solution in the FITS header puts {target} NN px from the Target marker, at (x, y). Check the marker is on your star (the chart labels follow the header)."
- Marker moved: info "The Target marker was on a neighboring star. The chart locked with the target NN px away, and the marker has been moved there. Check it is your star." (the marker IS moved).
- Only 2 catalog stars (manual): Yes/No "The catalogs have only 2 stars here, and both lined up. Two stars can line up by chance.\n\nLook at the circles. Are they on real stars, in the pattern the catalogs show?" — **No** discards the solve (and any auto-placed Target marker: log "Target: the marker placed from that solve was removed with it; click the target.").

**On success**: axis title "Solved · AAVSO chart X12345ABC  43/60 matched  rotation 12.3°[, mirrored][, scale x1.012]  2345 stars labeled"; status "Solved · AAVSO chart ...: 2345 stars labeled (2100 with V magnitudes). Pick Comparison or Check, then click any star to fill its ID and magnitude (cyan circles: AAVSO sequence stars). Green rings: suggested comps near the target's brightness." (Discovery: "Field solved: N catalog stars labeled (M with V magnitudes). Watch or exclude stars if you like, then Scan field."). Log lines: per-catalog counts and timings, "AAVSO chart X: N sequence stars." or "No AAVSO sequence stars here.", Gaia depth ("Gaia DR3: 4000 stars to G 18.2 (...)"), half-answered Gaia notes ("...Solve again to retry."), catalog failures, colour-calibration availability ("No catalog here gives B magnitudes, so color (B-V) cannot be calibrated on this field." / "B and V present: B-V can be calibrated. No R, so V-R cannot." / Sloan note).
- Then: auto target marker (§3), variable field check, watch stars re-located, **comps/check marked before the solve get their IDs and magnitudes filled in** ("Solve filled in C1: ...", or a stale name corrected: "C2 name updated from {old} to {new}: the marked star is {new}."), suggested comps, panel and image redrawn.

**Field check** (`_apply_field_check`): panel text "Variable-star check (VSX + SIMBAD): N known or suspected variables in the frame; M of them are labeled stars (red circle), the rest are red × (Show variables). Comps and the check star are checked by position, whatever catalog you labeled with. Variables are never used as spares or in the color fit. Hover to see what they are." If unreachable: "Variable-star check: VSX and SIMBAD could not be reached, so the comps were not checked. Solve again later."

**Suggested comps** (`_suggest_comps`): up to 4 labeled stars within ~1 mag of the target (instrumental), not variable, not within 85% of "raw sat ADU", not at the edge, colour-matched when B−V is known; AAVSO sequence stars first, other catalogs fill in. A faint target (SNR < 30) gets comps 0.5–2 mag brighter. Drawn as green rings; log "Suggested comps (near the target in brightness and near its color, target B−V 0.65; green rings): 000-BBC-123 (+0.3 mag, AAVSO sequence), ... Comps like the target cancel airmass, focus, and calibration errors best." Or: "No labeled star within 1 mag of the target here (closest: 1.7 mag away); pick the closest in brightness and color you can." / "Labeled stars within 1 mag of the target here all differ from it in color (B−V more than 0.6 apart); ...". Recomputed when the Target is clicked/moved.

Pitfalls:
- "Solve again" after marking comps keeps your markers and fills their names; a solve that moves the Target marker tells you — check it.
- Another telescope's frames: set focal length / pixel size in the Optics box, else "no lock".
- The automatic solve is silent on failure (log + step strip "1 Not solved"); press "Solve again" to see the dialog with the diagnosis.

## 9. Apertures: boxes, "Suggest…" window, aperture test, sanity check (`suggest_apertures` ~4758; `_neighbor_notes` ~4791; `test_apertures` ~4817; `SuggestWindow` ~13135–13307; `_aperture_sane` ~4693; core.suggest_apertures 5168)

**Boxes** (options row): "Aperture r", "sky in", "sky out" (pixels, on the binned/debayered image), "raw sat ADU" (saturation level of the *raw* frame; used for the saturation checks, 85% of it marks "near saturation" in comp suggestion and the Discovery count). Rings redraw ~400 ms after typing when values are valid (0 < r, sky in < sky out); half-typed numbers are ignored.

**"Suggest…" button** → `suggest_apertures`:
- Not calibrated: info "Calibrate first (Calibrate page); star sizes are measured on the calibrated, debayered image."
- Measures the field FWHM from bright, unsaturated, isolated stars (`core.field_fwhm`); fallback to the marked stars' Gaussian fits; if neither: info "Could not measure star sizes on this frame. Mark the target and comps first."
- Formula (`core.suggest_apertures`): r = max(3, 1.8×FWHM); sky in = ceil(max(r+3, 3.2×FWHM, r+1.5×FWHM)); sky out = ceil(sqrt(sky_in² + 4r²)) (ring area = 4× aperture area).
- Opens the **"SHOBS-P — suggest apertures"** window (Toplevel, scrolling body, resizable):
  - Heading "Suggested apertures"; "Star size (FWHM) on this frame: 3.21 px   (median of 12 bright, unsaturated, isolated stars)" or "(from the marked stars (no bright isolated star to measure))".
  - If marked stars were measured: "Marked stars, for comparison: Target 3.4, C1 3.1 px. Faint or crowded stars measure noisier; the aperture follows the field's seeing."
  - "This window sets the aperture and sky ring, not which stars are used."
  - Bold headline: "Aperture r = 5.8 px (1.8 × FWHM)    sky in = 11    sky out = 17 px (sky ring with 4× the aperture's area)" — always what the Use button will set.
  - Hint: "Bright stars do best with 1.5–2 × FWHM; faint stars with 1–1.5 ×. The seeing changes during the night, so the test below measures real frames and lets the check star decide."
  - Notes (Warn style when "⚠"): faint target note "The target is faint (peak about 20 × the noise). Faint stars usually do best with 1.2–1.5 × FWHM (r = 3.9–4.8 px); the test below will tell."; neighbour notes per marked star: "⚠ C1: 1 other star(s) inside the aperture; its light will be added to c1's. Choose another star, or a smaller aperture.", "⚠ Check: a star 9 px away, just outside the aperture; in poor seeing its light spills in. Prefer another star if you have a choice.", "Target: 2 faint star(s) in the sky ring; the sigma-clipped sky usually rejects them."
  - Results box (filled by the test).
  - Buttons (bottom): Accent **"Use these aperture sizes"** (writes the three boxes, redraws, status "Aperture r 5.8, sky 11–17 px. Use the same sizes for every night of a series.", closes); **"Test 4 apertures on 30 frames"**; **"60 frames (slower, surer)"**; **"Close"**.

**Aperture test** (`test_apertures`): requirements — calibrated ("Calibrate first; the test measures real frames.") and at least one comp ("Mark at least one comparison star; the test compares stars against it."). Judged on the check star if marked, else comp 2, else the target ("target (it may truly vary)"). Radii 1.2, 1.5, 1.8, 2.2 × FWHM (min 2 px) with one shared sky ring; 2.6 and 3.0 × FWHM also measured with a wider ring and only join the comparison when the largest of the four wins. Frames spread evenly through the night; each is calibrated, debayered, registered to the preview. Progress bar label "Aperture test: frame k/n   elapsed m:ss   about m:ss left"; log "Aperture test: r = 3.9, 4.8, 5.8, 7.1 px, sky 11–17 px, 30 frames; judged on the check star against the comps. Also r = 8.3, 9.6 px (...)". Cancel via "Cancel run" ("Cancelled."). Skipped frames logged "  aperture test skipped {file}: {reason}".
- Result text in the window: "Measured on 30 frames; sky ring 11–17 px. ± is one standard error; 'lowest' is how often that aperture stays lowest when the frames are resampled (N times).", "Seeing (FWHM) in these frames: 2.9 to 3.6 px, median 3.2.", a table "r (px)  check star scatter  lowest  target scatter  lowest", then one verdict: "Clear winner: r = 4.8 has the lowest check star scatter in 87% of resamples." / "The larger apertures were no better than r = 7.1 ...; r = 7.1 stays, the clear winner of the first four (...)." / "No clear winner: the differences are within the noise (no aperture is lowest in 80% of resamples). Keep the suggested r = 5.8 (1.8 × FWHM)."; optional "The target alone prefers r = 3.9 (85%). If the target is much fainter than the check star, that can be the better choice; if it truly varies, its scatter includes the variation."; edge warnings "Best at the edge of the tried range (r = ..., the largest tried); a larger aperture may be better. A close neighbour, trailing or defocus can do this; check the image." / "...the smallest tried); a smaller aperture may be better (faint stars often prefer 1–1.2 × FWHM)."; finally "Use these aperture sizes now sets r = 4.8, sky 11–17." The bold headline updates to the tested sizes and a grey line "Before the test: r = 5.8 px (1.8 × FWHM), sky 11–17 px (from the seeing alone)." appears.

**Sanity check at Run** (`_aperture_sane`, called from `run_photometry`): if r > 3×FWHM or r < 0.9×FWHM, Yes/No dialog: "The aperture (r = 12 px) is much larger [smaller] than the stars on this frame (FWHM 3.2 px).\n\nA large aperture collects sky noise and neighboring stars; scatter can be several times worse. [A small aperture loses starlight as the seeing changes. ]Suggest… would use r = 5.8, sky 11–17 px.\n\nRun with the current aperture anyway?" — No aborts the run. Typical after switching telescopes (e.g. MicroObservatory frames).

Pitfalls: keep the same aperture sizes for every night of a series (status reminder); the test needs a comp and ideally a check star; "raw sat ADU" is in raw-frame units, not the calibrated image's.

## 10. Progress row, log (`_progress_start/_progress/_progress_end` ~4957–4978; `toggle_photo_log` ~4939)

- Progressbar + text: "{label}: starting, N frames" → "{label}: frame k/N   elapsed m:ss   about m:ss left" → "{label} done in m:ss. [Cancelled.|Failed.]". Labels: "Photometry", "Aperture test", "Field scan" (see §11/§12). Idle text "Idle.".
- "Cancel run" → `cancel_job` (~9513): status "Nothing is running." when idle; else sets the cancel flag and logs "Cancel requested; stopping after the current frame…".
- "Hide log"/"Show log" toggles the 7-line dark log under the progress row (same messages as the Calibrate page and the status bar's longer explanations).

## 11. "Run photometry": pre-run checks, questions, progress, completion (`run_photometry` ~8220–8740; `_markers_in_order` ~8175; `_mode_guard` ~4625; `_transit_in_lights` ~4542; `_photometry_done` ~8775–8939)

Order of checks when "Run photometry" is pressed (each can stop the run):
1. No lights left: error "No light frames left after the blink reject."
2. No masters: error "Build calibration masters first."
3. Aperture sanity (§9) — Yes/No.
4. `_markers_in_order`: a marker off the image → error "The C2 marker is off the image (x, y; the image is W×H). Clear it and click the star again."; no star under a marker → Yes/No "There is no star under the Target marker at (x, y). Measure blank sky there anyway?" (No stops); names without markers → Yes/No/Cancel "These have a name in the Stars panel but no marker on this image, so they would not be measured:\n  • C2: {name}\n\nYes: clear those names and continue.\nNo: continue and leave them as they are.\nCancel: stop, so you can mark them."
5. `_mode_guard` (wrong-mode catch):
   - Transits mode, target looks like a variable (VSX type/period other than EP, variable at the marker, or an open Variables series) and no planet / no transit in the lights: Yes/No/Cancel "No planet is entered on the Input page[, / No transit of {planet} is predicted during these lights (the nearest is 3.2 h after them)], and {reasons}.\n\nRun in Variables mode instead?\n\nYes: switch to Variables mode and run there.\nNo: run in Transits mode anyway[ (the night starts a new series; the Variables series is not changed)].\nCancel: don't run." Yes switches mode and re-presses Run there (log "Switched to Variables mode for this run.").
   - Transits mode otherwise, planet set but no transit predicted in the lights' span (±15 min): Yes/No "No transit of {planet} is predicted during these lights: the nearest is 3.2 h after them.\n\nIf this is a variability run, switch to Variables mode first (the series and reports are kept per mode).\n\nRun photometry in Transits mode anyway?"
   - Variables mode, no Variables series open, target hosts the planet in the Planet box and a transit is predicted: Yes/No/Cancel "{target} hosts {planet}, and a transit of {planet} is predicted during these lights (mid-transit about HH:MM local).\n\nRun in Transits mode instead?\n\nYes: switch to Transits mode and run there.\nNo: run in Variables mode anyway.\nCancel: don't run."
6. Validation errors (error dialog with the text): "Binning is 2 but the masters were built at 1. Rebuild the masters or set it back."; "Target is not marked"; "Mark at least one comparison star (Comps, then click the star)"; "C1, Check: the labeled catalog is in another band than the comps' V. Solve again so the comps get V magnitudes"; "C2 has no catalog V: that star is not in the downloaded catalogs (or the catalog has no V for it). Type its V in the box next to the name, or pick another star" (solved) / "C2 needs a catalog magnitude (click it once the image is solved, or type it)" (unsolved); "Two comps have the same name (…). Give each its own name."; "Need aperture r < sky in < sky out"; "{name} is marked on top of the target. Select it and click a different star."; "C1 and Check are marked on the same star. Each one must be a different star; the check star in particular must not be one of the comparison stars."; bad numbers in lat/lon/RA/Dec/sat boxes → their float error.
   - Log note (not a dialog) when site lat/lon are empty: "Note: no site latitude/longitude on the Input page, so no airmass: AAVSO reports show AMASS na and the error bars leave out scintillation. Type your site once (it is remembered)."
7. Ensemble without check (Variables mode only, ≥2 comps, no check): Yes/No "With 3 comps the AAVSO report is an ensemble, and AAVSO requires a check star for ensemble reports. Continue without one?" (Transits: log "No check star marked: fine for a transit fit (a check star only shows whether trends are shared).")
8. Colour: "Also measure R and B" ticked but B−V cannot be calibrated: Yes/No "'Also measure R and B' is ticked, but B-V cannot be calibrated on this run:\n  • only N labeled stars have catalog B and V (5 are needed for a field fit)\n  • no color term has been saved from an earlier night\n  • the comps' catalog colors are unknown (…)\n\nSolve the image (Solve again) and click the comps again, or choose comps with catalog B and V.\n\nRun anyway with uncalibrated color?" On a Sloan run colour is silently turned off (log).
9. Spare comps are chosen automatically from catalog stars near the comps' brightness (up to 8; thin field widens the window — log "Spare comps: thin field, so the brightness window was widened to …"); watch stars are recentred.
10. Open series in another mode: Yes/No/Cancel "{series file} is a Variables series and this is Transits mode.\n\nYes: switch to Variables mode and run there (the night can then be added to the series).\nNo: run in Transits mode; the night starts a new series.\nCancel: don't run."
11. Job starts: status "Photometry on N lights…"; **the pop-out image is brought back** so progress shows on the page; log lines for watch stars, spares, colour sources, "Photometry on N lights, M rejected, aperture r px, green channel", rejected frame list, then one line per frame: "  12/300  JD 2460965.12345  mag 11.234 ± 0.004  FWHM 3.2  raw peak 23456  shift (+1, −2) rot +0.01°  [flags]" or "  skipped {file}: could not align this frame (N stars found; clouds? trailing?)". Progress: "Photometry: frame k/N   elapsed …   about … left". Each frame is calibrated, debayered, aligned to the preview frame (`core.register_frame`, chained to the last good frame when needed), measured (`core.reduce_frame`), saturation judged on the raw frame ("sat"/"compsat" flags), spares/watch/colour measured.
12. "Cancel run": "Photometry done in m:ss. Cancelled; nothing was added." + log "Photometry cancelled. Nothing was added to the series." A worker crash: "Stopped by an error; nothing was added."

**Completion** (`_photometry_done`): progress "Photometry done in m:ss. N points."; none → error "No frames produced a magnitude. The Calibrate page log says why each frame was skipped." If a series is open in the same mode, the **append question** (Yes/No): "Add these N points as the night of {date} ({JDxxxxxxx}) to the M points already in the series?\n\nYes keeps every earlier night. No starts a new series with this night only.[ No also leaves {file} on disk untouched; use Save series to keep the new one under a new name.]" plus any of: "Careful: the series is V and this night is B."; "Careful: this night used different stars than the series.\n  Series: comps …; check …\n  This night: comps …; check …\nDifferent comps put each night on its own zero point."; "One star, one name: … Yes uses the series' names and catalog magnitudes for this night. The series' catalog magnitude wins, so every night stays on one zero point; Use comps… (Output page) can re-derive the whole series with other comps afterwards."; "Careful: this night comes from other equipment or another observer: … Untransformed nights from different instruments sit on their own zero points (tenths of a magnitude). A separate series is usually better: answer No."; "Careful: this night was reduced differently from the series: … Nights reduced differently can sit on different zero points (a step between nights that is not the star)."; "The night of {date} is already in the series. Yes replaces that night's points with this run (the other nights stay)." Then log "Photometry done: N points, F flagged (c cloud, e at a cloud edge, h hazy).", zero-point and noise notes, the app jumps to the **Output page**, status "N points across K night(s). Save the series to add more later.[  Auto-saved to {file}.]" (auto-save failure → warning "Could not auto-save the series: …"). A loaded EXOTIC report on the Transit page is closed ("Transit fit: the EXOTIC report that was loaded was closed; the page now uses this photometry.").

Pitfalls:
- A run in the wrong mode is caught, but only if the Input page knows the target/planet; answer the mode dialogs rather than Cancel-and-wonder.
- The pre-run questions are many on a first night; the ensemble-without-check and colour questions are asked on every run; the Transits-vs-Variables mode question is skipped once a Variables series is open (a campaign on a planet host is not asked every night).
- Nothing is written to disk until a series is saved — except an auto-save when a series file is already open.

## 12. Discovery: "Scan field" (`scan_field` ~7665–8062; `_scan_done` ~8064; `mark_scan_candidates` ~8161)

Button "Scan field" (Accent, tools row, Discovery mode; also reachable from other modes as a "scan" of the field — then results open in a `ScanWindow` instead of the Output page).

Pre-checks (dialogs): "Calibrate first (Calibrate page). The scan uses the same frames, calibration, alignment, and aperture as photometry."; "Need aperture r < sky in < sky out" / bad period boxes (Input page min/max period) → error with text; "Binning changed since the masters were built. Rebuild the masters first."; "Check the scan settings (threshold, candidate level, faintest magnitude)."; "Fewer than 8 stars found in the frame; a field scan needs more."

What it does: detects up to "Most stars to measure" stars (brightest first) at "Detection threshold (σ)" on the preview, drops stars within sky out + 3 px of the edge, drops crowded stars when "Leave out crowded stars" is ticked (another detected star inside the sky ring), drops stars you excluded (orange ×), always includes watch stars (labelled "W name"); other stars are "S001", "S002"… Stars with a peak ≥ 85% of "raw sat ADU" are measured but never candidates. Log: "Field scan: N stars, M frames, aperture r px[, with R and B][; k crowded star(s) left out][; j excluded by you]"; "Field scan: the 800-star limit was reached, so only the brightest 800 stars are measured (raise "Most stars to measure" to go fainter)." Status/progress: "Scanning N stars in M frames…", "Field scan: i/M frames…", progress bar "Field scan". Every frame is calibrated, debayered, aligned (chained registration) and all stars measured; a frame that fails: log "  scan skipped {file}: {reason}"; fewer than 10 measurable frames → "Field scan failed: only N frames could be measured; a scan needs at least 10".

Analysis: light curves lined up across meridian flips / rotator moves (log "  2 meridian flip(s) or rotator move(s): each star's light curve lined up across them before measuring its scatter."); magnitude scale from the catalog zero point of solved field stars ("  Magnitude scale: catalog V (best of AAVSO, APASS, Gaia, Tycho per star) zero point from N field stars, ± 0.012 (star-to-star scatter 0.034; untransformed).") or from C1's catalog magnitude (non-Discovery) or instrumental if unsolved; automatic faintest magnitude where typical scatter reaches 0.25 mag ("  Faintest magnitude 15.3: the field's typical scatter reaches 0.25 mag there. Type a faintest magnitude to choose another." or, unsolved, "(instrumental) … (A typed faintest magnitude needs a catalog zero point: run Solve first.)"); candidates = usable stars with scatter ≥ "Candidate at (× normal)" times the expected scatter for their brightness and ≥ 0.005 mag, not saturated, not too faint; cautions ("  Cautions on candidates: n near saturation, m with a change or a jump in noise starting at a flip, rotator move or gap."); Lomb–Scargle best period for each candidate within the Input page's period range; optional colour (not on a Sloan run: "Field scan: color not measured on a Sloan … run."); VSX/SIMBAD cross-match when solved ("  Variable-star check skipped: {reason}" if offline).

Finish (`_scan_done`): log "Field scan done: N stars, M frames, k candidate(s), j already known (VSX / SIMBAD)" or ", no sky positions (run Solve for the VSX/SIMBAD check)"; status "Field scan: k candidate variable(s)."; the app moves to the **Output page** ("Output – Field scan", `ScanView`: scatter-vs-magnitude plot, selected star light curve, periodogram, table with columns "#", "Star / position", "Mag", "Scatter", "× normal", "Best period (d)", "B−V", "Check first", "VSX"; buttons "Save plot PNG…", "Save CSV…", "Export candidates CSV…", "Export all light curves CSV…", "Back"). Before any scan the Output page shows "No field scan yet: Scan field on the Photometry page. (Calibrate first; the field is solved straight away, so every star gets a position, a catalog magnitude and a VSX/SIMBAD check.)" and its buttons answer "Scan the field first (Scan field on the Photometry page)." Cancel: "Field scan cancelled." Failure: error "Field scan failed: {reason}" (traceback in the log).

Back on the Photometry image: `mark_scan_candidates` (from the Output page) draws numbered squares for the candidates (kinds in §4) and switches to the Photometry step; "Candidates only" in the View box hides everything else; `clear_scan_marks` → status "Candidate marks cleared."

Pitfalls: scan without a solve gives instrumental magnitudes and no VSX check; the default 800-star cap may hide faint stars (raise "Most stars to measure"); a typed "Faintest magnitude" is ignored when unsolved.

## 13. Quick reference: shortcuts and gestures (Photometry page)

| Action | How |
|---|---|
| Zoom in/out | Mouse wheel over the image (about the pointer); "+"/"−" buttons (about the view centre); in the pop-out window also keyboard +, =, − (and keypad) |
| Whole frame | "Fit" |
| Pan | Left- or middle-drag ≥ 6 px |
| Mark a star | Left click (no drag) with the pick mode chosen (Target / Comps / Check / Watch; Discovery: Watch it / Exclude it) |
| Clear a marker | Right-click on it (T, C1…C10, K, W, orange ×); or "✕" in its Stars-panel row; or "Clear selected" (current pick mode; Comps = last comp added; not for Watch) |
| Place a star by name/RA-Dec | Type in an empty C-slot or Check box, press Enter / keypad Enter |
| Add a watch star by name | "Add a star…" in the Watch stars group |
| Scroll the right panel | Mouse wheel over the panel |
| Bigger image | "Pop out"; close window / "Go back" / "Bring back" returns it |
| Hover info | Hold the pointer within ~8 px of any star/marker |

Persistent settings touched from this page: `photo_tips` (Hide/Show tips), `stretch_photo`, `photo_popout` (pop-out window geometry), aperture boxes and view switches are app settings saved elsewhere (not surveyed here).

