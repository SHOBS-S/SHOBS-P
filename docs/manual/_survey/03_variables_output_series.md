# Survey 03 — VARIABLES mode: Output – Variability page, popups, flags, series, AAVSO report

Source: clearv_app.py (2.2.17), photometry_core.py. Line refs are to clearv_app.py unless noted.
Status: written incrementally; sections appended as surveyed.

## 1. Output – Variability page: layout (clearv_app.py `_page_output` 10258–10375)

Page title label "Output – Variability" (bold 16pt). Top-down order inside the card:

1. **Summary panel** (`_build_summary` 11109) — read-only text + night table (see §2).
2. **Period-search row**
   - "Period search (days)" — two `Entry`s (`min_period`, `max_period`), min "to" max. Range scanned by the periodogram.
   - "Line up segments (nights, meridian flip)" — `Checkbutton` (`norm_segments`), triggers `refit_period`. Normalises each night/flip segment to a common level before period search.
   - "Time axis" — read-only `Combobox` (`time_axis`): "JD" | "UTC" | "Local time"; redraws plots.
   - "Notes" — `Entry` (`notes`), free text; goes into the AAVSO report NOTES field (see §AAVSO).
3. **QC row**
   - "Leave flagged points (sat, drift, cloud) out of the period and report (planet-transit frames never go in a report)" — `Checkbutton` (`skip_flagged`), triggers `refit_period`.
   - "Cloud flag: comp light more than" [`Entry` `cloud_limit`, width 5] "% below the night's median" + "Apply" `Button` (→ `refit_period`).
   - "Transmission loss…" `Button` → `edit_transmission` (9488) — keep or drop a night's dew/haze points.
   - "Planet transits…" `Button` → `edit_planet_transits` (9196) — keep or drop frames inside a known planet's predicted transit.
4. **Tools row**
   - "Check-star correction (fold and period)" `Checkbutton` (`use_check_zp`) → refit.
   - "Line up flip only (keep nights)" `Checkbutton` (`align_flip`) → refit.
   - "Pier side as segments" `Checkbutton` (`pier_segments`) → refit (2.2.14: each pier side normalised on its own).
   - "Bin" read-only `Combobox` (`bin_choice`, values `BIN_CHOICES`) → redraw.
   - "Mark periods (days, comma separated)" `Entry` (`mark_periods`) + "Show" `Button` → redraw (vertical marks on periodogram).
   - "Period from" read-only `Combobox` (`period_bins`, `PERIOD_BIN_CHOICES`) → refit.
5. **View row** (2.2.17 item 10, own line for high-DPI): "Airmass-corrected night means (plot only, never in reports)" `Checkbutton` (`airmass_view`) → redraw.
6. **Figure** — 2×2 matplotlib grid: ax1 light curve (top-left), ax2 (top-right), ax3 (bottom-left), ax4 (bottom-right) — see §2 for content. Hover → `_on_hover`; resize refits the light-curve title (2.2.14 item 16).
7. **Series row** (label "Series"): "Save series", "Load series" (tooltip `LOAD_SERIES_TIP`), "Close series" (enabled only with a series open; mirrors Input page), "Add series file…", "Remove night…", "Night notes…", "Comp health…", "Use comps…", "Watch stars…", "Time of max/min…".
8. **Results row** (label "Results"): "Save AAVSO report" (Accent), "Open AAVSO WebObs…", "Save light-curve CSV", "Export…", "Save plot PNG", "Refit period"; right side: "Back" (→ Photometry step) and, in Transits mode only, "◀  Transit View" (Accent) which returns to the transit fit (`show_variables_view` 10382 packs it; status line: "Variable View: light-curve details, comps, and check star. ◀ Transit View (bottom right) returns to the fit.").

Note: In Transits mode this same page is reached as "Variable View" from the transit fit page; everything below applies there too.

## 2. Summary panel, plots, night table, cautions (`draw_output` 10691–11102, `_build_summary` 11109, `_fill_summary` 11347)

### 2.1 Three summary boxes (top, left to right): "Series", "Period", "Color"
- Empty state text: "No series yet: run photometry" / "or Load series." (10718). In Transits mode with only an EXOTIC report loaded: "Variable View not available from an EXOTIC report." plus plot text "It shows SHOBS-P's own photometry; the report has only its final light curve." (10705–10715).
- **Series box** lines (10987): `Points N (M used)`, `Nights N   span X d`, `Comps …` (wrapped at ~50 chars, 2.2.14), `Check <name>   σ 0.xxx [(instrumental)]`, `Filter <f>   Bin 2 [/1]   [plot bins <choice>]`.
- **Period box** lines (10918–10968): `Best`/`Peak <P> d (<h> h) [(not significant)] [(one short night: not reliable)] [⚠ near a day alias] [⚠ at the edge]`, `Power x.xx   FAP 1.0e-03 (points), 2.3e-02 counting nights (from the nightly means | night offsets taken out)`, `Basis every point (N)` or `N 10-min bins`, `Alias … d (one-day)`, `Epoch JD …`, `Fold check-star zero point` | `Fold check-star correction off`, optional `VSX <P> d` (catalog period), `Check ⚠ depends on <night> alone`, `Airmass ⚠ nightly means follow airmass (+0.0xx mag per airmass)[; period demoted]`, `Marked <m> d → power x.xx` or `Marked <m> d (outside the search)`.
  - Leading "No … period" line when not significant (one of): "No reliable period yet: one short night covers under two cycles of the best peak. Add nights." / "No reliable period: the peak depends on <nights> alone (often an offset between nights, not the star)." / "No reliable period: the peak follows the nights' airmass; it is not shown to be the star." / "No significant period: counting nights, the peak's false-alarm probability is X (a night's points share offsets)." / "No significant period: the star looks constant at this precision (scatter 0.00x mag per point, 0.00x mag night to night)."
  - Fewer than 8 usable points: box reads "Needs at least 8 usable points." and ax2 title "Not enough points for a period"; ax4 title "No periodogram".
  - Significance rule (10890): FAP ≤ 1%, not carried by one night, not too_short (one night < 2 cycles), night-FAP ≤ 1%, not airmass-demoted.
- **Color box** (`_color_lines` 11373): per colour (B−V, V−R) "<label> x.xxx ± y.yyy" or "+0.0xx relative to the comps" / "not measured"; "Temp  ≈ 5,800 K, G-type"; "Cal   field stars, k = …" | "Cal   N of M points" | "Cal   untransformed". Cautions: "<label> is calibrated on only part of the series." / "<label> is untransformed (approximate); calibrate it with 'Also measure R and B' on a solved image."

### 2.2 Night table (`Treeview`, `SUMMARY_COLUMNS` 11105; rows from `_night_rows` 11274)
Columns: "Night" (short label e.g. date/target), "JD" (night key), "Pts", "used", "Mean mag ± err" (night mean from `bin_points`), "Target σ" (robust), "Check σ" (reduced, else instrumental), "Side" (pier side(s), e.g. "30°/210° flipped"), "Flip step" (+0.0xx, "⚠" if > 0.02 mag `FLIP_STEP_MARK`), "B−V" (median), "Flags" (counts, most common first, e.g. "12 cloud, 3 sat"), "Note" ("* " + night note).
- Flag words shown in the table: `sat`, `drift`, `cloud`, "cloud edge" (from `cloudedge`), "dew/haze?" (from `haze`, `HAZE_SHORT`), "kept, dew/haze?" (kept transmission-loss points, count), "in planet b transit" (from `transit` flag) or "kept, in planet b/c transit" (user chose to keep), fallback "in a planet transit". "—" when no flags.
- Table height grows to 8 rows then scrolls. Clicking a row (`_on_night_click` 11139) toggles `highlight_night`: that night stays bright, others fade (alpha ×0.12); with >1 night the light curve and check plot ZOOM to that night (x = hours from the night's first point; y fitted to that night's points — `_zoom_axes` 10645); click the same row again to show all. Hint line under table: "Spread of nightly means 0.0xxx mag  (typical error 0.0xxx)     Click a night to zoom the light curve to it; click it again to show all." (shown only with >1 night means).
- Pitfall: a night with no finite JD cannot be zoomed (silently ignored, 10764).

### 2.3 Cautions line (`Warn.TLabel`, each prefixed "⚠ ", joined by spaces; wraplength 1500)
Sources appended in `draw_output`: `core.error_model_text` (old error model, 2.2.17 item 8); period alias text; "Leave <night> out and this period disappears: one night carries it (often an offset between nights, not the star). Check that night before trusting it."; airmass trend text (+ why, or " The period survives with the trend taken out and without the high-airmass nights."); `period_edge_text`; "The data span only X d, under 1.5 cycles of the best period; it is not pinned down yet."; "Re-reduce <nights>: binned before 1.7, so the channels were mixed (the filter and colors are not real)."; "One night: the night-to-night spread needs more nights."; pier cautions (`_pier_cautions` 11203: "Nights on both pier sides (…): … Tick 'Pier side as segments' to normalise each side on its own." / "...the star itself changes within a night (…), so a side offset read from its light curve means little. Comp health shows each comp's step at the flip." / "Pier side unknown on N night(s) (no ROTATOR, no PIERSIDE, no flip): left as they are." / "Pier side as segments on a star that changes within a night (…): the side offsets may come from the star, not the pier side." / "Pier side as segments has nothing to do: every night is on one side." or "...no night has ROTATOR or PIERSIDE in its headers or a meridian flip, so the sides are unknown."); zero-point cautions (`_zero_point_cautions` 12155); colour cautions.

### 2.4 The four plots
- **ax1 Light curve** (top-left): one colour per night (7-colour cycle, no legend), error bars grey; flagged/excluded points as grey "×"; points after a meridian flip ringed "○"; black "◆" = night mean or chosen bin mean (Bin box ≠ None; raw points fade to 0.25 alpha when binned). Y inverted, fitted to used points; flagged points beyond the range are pinned at the edge as small × and counted. Title = star id (+ " · <night>" when zoomed) with a symbol key: "× flagged, left out", "○ after flip", "◆ night mean"/"◆ 30 min mean", "N flagged off scale". Y-label "<Filter> magnitude" (+ " (segments lined up)" / " (pier sides lined up)" / " (flip lined up)"); Sloan filters shown as "Sloan i′ magnitude". X axis per "Time axis" box (labels "JD − 2460000"/"Julian Date", "UTC", "Local time (<zone>)") or, when zoomed, "Hours from the night's first point (HH:MM local, Mon D)".
  - "Airmass-corrected night means" ticked (`_draw_airmass_corrected` 9605): small filled orange diamonds = nightly mean, large hollow orange diamonds = mean with the fitted airmass slope removed, footer "◇ airmass-corrected night means"; hover shows "<night> night mean 12.3456 → 12.3400 airmass-corrected (airmass 1.23, +0.012 mag per airmass; display only)". Not drawn when zoomed to one night or no period. If no trend could be fitted: footer "Airmass-corrected night means: not available (no airmass | no airmass trend fitted)". Airmass comes from the frames' headers or, lacking pointing, from the target RA/Dec + site; otherwise the log says "Airmass check of the period skipped: no airmass (type the site on the Input page and the target's RA/Dec, or use frames with pointing in the headers)." (9596). NEVER in reports/CSV/period.
- **ax2 Phase plot** (top-right): used points folded on best period, plotted 0–2 phase (second cycle fainter), zero-pointed magnitudes (check-star corrected if "Check-star correction" on). Title "Folded  P = x.xxxxx d" or "Best peak (not significant)  P = x.xxxxx d". One short night: points grey + boxed note "One night of X h covers under two cycles of the best period (Y h): the fold is not meaningful yet. Add nights." Hover: night, phase, value, time.
- **ax3 Check star** (bottom-left): check values per night, same colours; flagged as ×; title "Check star  σ = 0.0xx  catalog [i′ ]11.230 (N flagged off scale)" or "Check star, instrumental …" (no catalog mag; y-label "Check − comps (instr.)"); "No check star" if none. Follows the night zoom (same hours axis); "No check points this night" when the check was measured on other nights only.
- **ax4 Lomb-Scargle periodogram** (bottom-right): power vs period (log x), red line = best period, green dashed "VSX" = catalog period if in range, purple dotted "marked" lines for "Mark periods" entries (labelled with value). Periods longer than half the usable span are shaded grey and labelled "longer than the data can test" (`shade_long_periods` 456). One short night: grey curve + note "Periods longer than half the night cannot be told apart yet". Hover gives P (d, h) and power.

### 2.5 Period search mechanics (`_refresh_period` 9523)
Runs `_apply_cloud` first (cloud flags from `cloud_limit`), needs ≥ 8 usable points; "Period from" = "Every point" | "5 min bins" | "10 min bins" | "30 min bins" (binned only if ≥ 8 bins result; otherwise falls back to every point). Lomb-Scargle between min/max period; then alias check, leave-one-night-out (`carried_by`), night-counting FAP, airmass trend & demotion (2.2.17 item 10; failure logs "Airmass check of the period not done (…)."). Error in the search → status "Period search: <exc>".
Combo/cheque interactions: "Line up segments" overrides "Pier side as segments" overrides "Line up flip only" (10736–10741).

## 3. Flags (what each word means, where it is set) — photometry_core.py + clearv_app.py

Flags are space-separated words in `Obs.flag`. With "Leave flagged points … out" ticked (default), any flagged point is excluded from the period search, the plots' fitted axes (drawn as grey ×) and the AAVSO report (`_usable` 9392). "transit" frames are never reported regardless (see §AAVSO).

| word | shown in table as | set by | meaning |
|---|---|---|---|
| `sat` | sat | photometry (core 2307) | target peak ≥ saturation limit |
| `compsat` | compsat | core 2309 | a comp peak ≥ saturation limit |
| `drift` | drift | core 2311 | star drifted off its expected position |
| `noflux` | noflux | core 2313 | target flux ≤ 0 |
| `cloud` | cloud | `core.apply_cloud_flags` 5913 via `_apply_cloud` 9404 | comp light more than `cloud_limit`% (default 20; clamped 0–95) below the night's median, patchy/fast/with brighter sky. Re-applied on every Apply/refit (replaces old cloud/haze flags). |
| `haze` (`HAZE_FLAG`) | "dew/haze?" (`HAZE_SHORT`); log uses `HAZE_LABEL` = "transmission loss (dew/haze?)" | same routine, 2.2.14 item 18 | a smooth stretch where every measured star fades together under a flat sky. Flagged like cloud unless the night is marked keep_transmission; kept points get an extra `haze_err` (star-to-star excess scatter) in quadrature. |
| `cloudedge` | "cloud edge" | `core.apply_cloud_edge_flags` 5986 | an outlier within a few frames of a cloud run (edge crossing target and comps at different times). Only points without another flag. |
| `transit` (`TRANSIT_FLAG`) | "in planet b transit" / "kept, in planet b transit" | `core.set_transit_flags` 5963 via `_set_transit_flags` 9172 | frame inside a known planet's predicted transit window (campaign rule) |

### 3.1 Transmission-loss (dew/haze) Keep question
- **After photometry** (`_offer_keep_transmission` 9454), only when a night has transmission episodes and more than one star was measured. `askyesno` (default **No**): "N frames of <night> lost light (00:15–02:25, down to 0.94 mag), but every measured star (comps, check, spares) faded by the same amount, smoothly, with a flat sky: transmission loss (dew/haze?), not cloud.\n\nKeep these points (errors scaled for the lost light)?\n\nYes keeps them: each frame's photon error already comes from its own (reduced) fluxes, and scintillation is a fraction of the light that arrives, so the error bars grow where the light is lost. [On top of that, every kept point gets the 0.0xx mag star-to-star scatter measured in that stretch (in quadrature), so the error bars stay honest even if this was a smooth cloud bank. ]No (the default) leaves them flagged, like cloud. Transmission loss… on the Output page changes this later."
  - Yes → night info `keep_transmission=True`, points unflagged with inflated errors; log "…points kept (errors follow the reduced light, plus the star-to-star scatter…)". No → stay flagged "haze"; log "…left out (flagged)."
  - With only one measured star no question is asked; log line says cloud cannot be told from dew/haze, left out, and that "Transmission loss…" can still keep them.
- **"Transmission loss…" button** (`edit_transmission` 9488): re-applies cloud flags; if no night has haze → info "No night has transmission loss (dew/haze?) at the current cloud limit. (Lost light that is patchy, fast, or comes with a brighter sky stays flagged cloud.)"; if several nights have it and none is highlighted → info "Nights with transmission loss (dew/haze?): <list>.\n\nClick one in the night table first, then Transmission loss… again."; otherwise askyesno "<night>: N frames with transmission loss (dew/haze?) (<when, depth>); now kept|left out.\n\n<Keep question>" (default No). A changed answer refits the period.
- Pitfall: the saved series stores `keep_transmission` per night (night_bin info), so the choice survives reload; the cloud limit itself is saved in meta.

### 3.2 Planet-transit frames (campaign rule, 2.2.15 item 1; `_check_planet_transits` 8944 ff.)
- Variables mode only, needs a Star ID. Runs automatically after photometry of a night ("run"), after Load series ("loaded"), and from the button for the open series ("series"). Looks up transiting planets of the Star ID at the NASA Exoplanet Archive in a background thread (cached per star/session; retry after `PLANET_RETRY_S` if it failed). Status while running: "Checking <star>'s planets for tonight's run…" / "…for the [loaded ]series…". Frames put on BJD_TDB when RA/Dec known; otherwise windows padded 15 min ("plus 15 min for JD vs BJD").
- Nothing is said when offline/no planet/no hits (unless from the button: status "Planet transits: no transiting planet with an ephemeris was found for the Star ID (or the NASA Exoplanet Archive could not be reached)." or "Planet transits: no frame of the series falls in a predicted transit.").
- If the series/mode/Star ID changed during the lookup the question is dropped (log "Planet-transit check for … dropped: the series was closed or replaced | the mode was switched | the Star ID changed …"; status "Planet transits: check dropped (…). Press Planet transits… again to check the open series.").
- **Question** (`askyesno`, default **Yes**), one for all unanswered nights: "<Tonight's run (Star, folder) | Series X.cvseries [(just loaded)] | The open series (not saved yet)>[ — not the lights now picked (<folder>), which have not been run yet]:\n<night>: 9 frames fall in planet b's transit (frames 05:02–05:25 local; transit 05:17–06:14 ±1 min[, plus 15 min for JD vs BJD][; T14 not published; 2.5 h assumed][; ephemeris uncertainty not published]).\n\nLeave those frames out (flagged \"transit\", like cloud)?\n\nYes (the default) follows the campaign rule: no data while a planet transits[ (every night listed)]. The frames stay in the series and Planet transits… on the Output page puts them back; they never go in a report.\nNo keeps them." Multi-night form lists "N nights have frames in a predicted planet transit:" with bullets.
  - Either answer is remembered per night (`transit_flagged`), period refit, redraw, and an open series file is re-written immediately: status "Planet transit flags saved to <file>." (log on failure "Could not save the series with the planet transit flags: …").
- **"Planet transits…" button** (`edit_planet_transits` 9196): with no night checked yet → runs the check on every night (status "Planet transits: looking up the Star ID's planets and checking every night…"); if not applicable → info "No night has frames in a predicted planet transit. (The check runs in Variables mode when the Star ID has transiting planets in the NASA Exoplanet Archive.)"; several nights, none highlighted → info "Nights with frames in a planet transit: <list>.\n\nClick one in the night table first, then Planet transits… again."; else askyesno "<night>: <hits>; now left out|kept.\n\nLeave them out (campaign rule: no data while a planet transits)?" (default = current state).
- Log lines: "<night>: 9 frames fall in planet b's transit (…) (campaign rule: no data while a planet transits)."; "<night>: frames in a planet transit left out (flagged transit, N)." / "kept (not flagged)."

### 3.3 Frame-edge warning (`_warn_edges` 9226, after photometry)
`showwarning` "<night>: near the frame edge or a weak part of the flat:\n\n  • <problem>…" when a comp/spare/check sits within 3×sky_out of the edge or in a weak flat region. Also logged with "⚠".

## 4. Series handling (.cvseries files)

### 4.1 What a series is
A `.cvseries` file is JSON (`core.save_series` photometry_core.py 5572): `{"version":1, "meta":{…}, "observations":[…]}`. `meta` (`_meta` 9694) holds every Input/Photometry setting (observer, star_id, chart, comp/check names & mags for comps 1–10, RA/Dec, notes, site lat/lon/elevation, pattern, debayer, catalog, time axis, bin choice, mark periods, min/max period, aperture radius/sky annulus, focal/pixel, sat & cloud limits, binning), plus `rejected` frames, `comps_used`, `active_comps`, `check_used`, `use_check_zp`, `align_flip`, `pier_segments`, `period_bins`, `night_notes`, `night_binning` (per-night setup incl. pier side, keep_transmission, planet_transits/transit_flagged, band_sources), `legacy_mixed_nights`, `color_cal`, `watch_list`, `spare_catalog`, `comp_catalog_mags`, `comp_coords`, `seq_auids`, `report_names`, `reports_saved`, `star_bands`, `mode`, `app_version`, and planet fields (Transits mode). Comp/check names are saved only from slots with a marker or names the series already knows (2.2.16 item 11) so another field's names never leak in.
- Pitfall: star pixel positions are NOT stored; the user must mark target/comps again on each new night's frame (the Load series info box says so).
- A series file's site is shown for that series but never saved as the user's own site (`_apply_meta` 9752).

### 4.2 Input-page series line (1615–1629)
Row at the top of the Input card: `Label` (`series_status`, wraps) + right-side `Button`s "Load series…" (tooltip: "Load series… replaces what is open: the open series is set aside (you are offered a save first if it has unsaved changes), and a series of another star clears this target's comps, check and watch stars.") and "Close series" (disabled when nothing is open; the Output page's "Close series" is enabled/disabled in step, `update_series_status` 10031).
Text (`series_status_text` 9954):
- no series: "No series loaded — this run will be a single night. To add a night to a series, Load series… before running."
- open: "Series: HD_219134.cvseries — 8 nights, last 13–14 Oct. After the run you'll be asked to add the night." ("not saved yet" in place of the file name for an unsaved series).

### 4.3 Load series (`load_series` 10174) — Input page "Load series…", Output page "Load series"
1. If a series with unsaved points is open → `_offer_series_save("load another")` (askyesnocancel: "<file|The series> has changes that are not saved | (N night(s)) has not been saved.\n\nYes: save the series first, then load another.\nNo: load another without saving.\nCancel: keep it open."). A cancelled/failed save dialog keeps it open.
2. Open-file dialog "Load observation series" (types: SHOBS-P series *.cvseries, JSON, All), starting in the last series folder (`settings["series_dir"]`).
3. Read failure → showerror "Could not read <file>:\n<exc>".
4. If the file's star differs from the Input target (`_other_target`): when positions decide, the target's comps/check/watch are cleared (`clear_target`); when only names differ → askyesno (default No) "<file> is a series of <star>; the Input page's target is <cur>. Without positions to compare, SHOBS-P cannot tell whether they are the same star (another catalog's name?).\n\nYes: they are different stars: clear <cur>'s comps, check star and watch stars.\nNo: the same star: keep what is marked." (No keeps marked slots' names and mags; log "Kept the marked stars: … taken as another name of …").
5. Meta applied; mode switched to the file's if different (log "Switched to <Mode> mode to match <file>."); a loaded EXOTIC report on the Transit page is closed; period refit; drawn unzoomed.
6. Legacy warning (`_warn_legacy` 10524, showwarning) for nights reduced with app binning before 1.7: "<file> has night(s) reduced with app binning before version 1.7: … Those versions binned the raw color frame before the debayer … re-reduce those nights in 1.7 (and use Remove night...) before reporting them."
7. Status "Loaded N points from M night(s). Reduce the new night, then choose Add." and showinfo "Loaded N points, M night(s).\n\nPoint Input at the new night's bias, dark, flat, and lights, run the flow, and choose Yes when asked to add the night.\n\nThe star positions are not stored, so mark the target and comps again on the new night's frame."
8. Background planet-transit check of nights not yet checked (source "loaded"; see §3.2).
9. The next save of this file keeps the as-loaded version as `_prev` (the `_prev_kept` marker is cleared, 10239).

### 4.4 Save series (`save_series` 10146, `_write_series` 9910)
- No points → showinfo "Run photometry first."
- Always asks where: save dialog "Save observation series", default name `<Star_ID>.cvseries` (spaces → underscores; "variable.cvseries" if no star), default ext .cvseries, types SHOBS-P series / JSON; starts in the open series' folder, else last series folder.
- Writes `X.cvseries.part` (core writes a `.tmp` then renames) and then renames over the target; **first write to a given file in a session** first moves the existing file to `X_prev.cvseries` (`series_prev_path` 102; replacing any older _prev; a file whose stem already ends in `_prev` gets no copy). Later writes in the same session leave `_prev` alone, so it is always "the version from before this session". Log "Series: the previous version was kept as X_prev.cvseries." (or "…could not keep the previous version as … (<exc>); the file is overwritten.").
- Status "Series saved (the version before is kept as X_prev.cvseries). Next night, load this file before reducing: <path>". Error → showerror "Could not save: <exc>".
- Automatic re-writes of an open series file (no dialog) happen after: a night is added/replaced by a run (status "… Auto-saved to <file>."; warning "Could not auto-save the series: <exc>"), "Add series file…", "Remove night…", "Night notes…", "Use comps…" (comp change), planet-transit flag answers, and (see §5) AAVSO report bookkeeping. Pitfall: "No" to the add-night question never touches the old file; a new unsaved series starts.
- Unsaved-change tracking (`_series_fingerprint` 9939): points, flags, night setups, active comps. Plot-only settings do not count.

### 4.5 Close series (`close_series` 10111) — both pages
- Nothing open → status "No series is open."; a run in progress → showinfo "Wait for the current run to finish (or cancel it) before closing the series."
- Unsaved → `_offer_series_save("close it")` (Yes/No/Cancel as above).
- Clears points and all series state (`_clear_series_state` 10064: night info, active comps, notes, report records, watch stars and AUIDs that came from the file; watch stars added this session stay). File on disk untouched. Log "Series closed: <file> (the file on disk is unchanged)."; status "Series closed. The next run is a single night (a new series)."

### 4.6 Adding a night after photometry (`_photometry_done` 8775)
Night key = `JD<floor(first jd)>`. With a series open in the same mode, askyesno (default Yes button):
"Add these N points as the night of <date> (JD2460…) to the M points already in the series?\n\nYes keeps every earlier night. No starts a new series with this night only.[\n\nNo also leaves <file> on disk untouched; use Save series to keep the new one under a new name.]" followed by any of these warnings:
- "Careful: the series is V and this night is TG." (filter mismatch)
- "Careful: this night used different stars than the series.\n  Series: comps …; check …\n  This night: comps …; check …\nDifferent comps put each night on its own zero point."
- "One star, one name: <X is the series' Y; …>. Yes uses the series' names [and catalog magnitudes] for this night.[ The series' catalog magnitude wins, so every night stays on one zero point; Use comps… (Output page) can re-derive the whole series with other comps afterwards.]" (same star under another catalog name, `_same_star_plan` 11755)
- "Careful: this night comes from other equipment or another observer:\n  • …\nUntransformed nights from different instruments sit on their own zero points (tenths of a magnitude). A separate series is usually better: answer No."
- "Careful: this night was reduced differently from the series:\n  • <binning/aperture/… differences>\nNights reduced differently can sit on different zero points (a step between nights that is not the star)."
- Duplicate night: "The night of <date> (JDxxx) is already in the series. Yes replaces that night's points with this run (the other nights stay)." (log "Replaced the earlier points of JDxxx with this run.")
Yes → merged (`core.merge_nights` drops points at an identical JD), the night's setup recorded, period refit, Output page shown, status "N points across M night(s). Save the series to add more later.[  Auto-saved to <file>.]" No → the old series is set aside unsaved-untouched (log "New series started; <file> was left unchanged on disk."), tonight becomes a new, unsaved series.
If the open series is of another mode, no question: a new series starts.
Post-run log: "Photometry done: N points, F flagged (C cloud[, E at a cloud edge][, H transmission loss (dew/haze?)])." plus the Keep question (§3.1), zero-point check log, noise note, edge warning (§3.3), then the planet-transit check (§3.2).

### 4.7 "Add series file…" (`add_series_file` 11548) — merge another saved series into the open one
Open dialog "Add a saved series to the one that is open". Errors: "Could not read <file>:\n<exc>"; mode clash → showerror "<file> is a Transits series; the open series is Variables. Series from different modes cannot be merged." Confirmation askyesno "Add N points (JD…, JD…) from <file>?" with optional warnings: "Careful: that file used comps … and check …; this series uses comps … and check …", "Reduced differently from the open series:\n  • <night>: <difference>", "These nights are already in the series and will be merged point by point: …". Takes over the file's spares/catalog mags/coords/notes/colour cal/night setups where the open series has none; auto-saves an open file (" Saved to <file>."); legacy warning for the added file; status "Added <file>: N points, M night(s)."

### 4.8 "Remove night…" (`remove_night` 12280)
askstring listing "  1)  JD2460xxx   (N points)" … "Type its number:"; bad input → showerror "'x' is not one of the numbers listed."; confirm askyesno "Remove the night of <date> (JDxxx, N points) from the series? This cannot be undone." Removes points, colour cal and night setup; refit; auto-save; status "Removed the night of <date>. N points left.[ Saved to <file>.]". (Undo path: the `_prev` file from the first save this session, or Add series file… from a backup.)

### 4.9 "Night notes…" (`edit_night_note` 11641)
No series → showinfo "There is no series loaded." askstring "Which night is the note for?\n\n  1)  <date> (JDxxx)   note: <first 60 chars>…\n\nType its number:", then askstring "Note for the night of <date> (stays with the series and goes into the CSV and exports; leave empty to remove):" prefilled. Saved in meta `night_notes`, shown in the table's "Note" column as "* text", in the light-curve CSV and exports; auto-saves an open file; status "Note for JDxxx saved." / "removed."

## 5. Results row: AAVSO report, WebObs, CSV, PNG, Refit

### 5.1 File naming (shared by the three saves; `_family_names` 1425, `output_path` 1506)
- Pattern: `AAVSO_<Star>_<FILT>_<DATE>_SHOBS-P.txt`, `<Star>_<FILT>_<DATE>_SHOBS-P_lightcurve.csv`, `<Star>_<FILT>_<DATE>_SHOBS-P_lightcurve.png`. Star spaces → `_`; illegal filename chars → `_`. DATE = UTC date of the first saved point as `DD-MON-YYYY` (upper case), or `16-SEP-2026_to_06-OCT-2026` when the points span UTC dates; for a one-night save the highlighted night's date.
- With an **output folder** set on the Input page: no dialog; the three files of one set of results share one counter (`_2`, `_3`… `_family_path` 1468) so report/CSV/PNG match; saving the same kind again starts a new number. Without an output folder: a Save dialog with the name prefilled. Saved files are never overwritten (`_unique_path`).
- Every text output starts with a stamp line `# Created 121153Z OCT 26 by SHOBS-P` (`core.created_line` 27; DTG = DDHHMMZ MON YY, UTC) — in the AAVSO report it sits among the `#` header lines; the PNG carries "Created <DTG> · SHOBS-P 2.2.17" in its title/footer.
- `saved_notice` 1527: with an output folder → status "Saved <file> to <folder>. <advice>" + log; without → showinfo "Saved\n<path>\n\n<advice>".

### 5.2 Highlighted-night scope (`_save_scope` 12505)
With a night highlighted in a series of ≥ 2 nights, Save AAVSO report and Save light-curve CSV ask (askyesnocancel): "<Star> · night of 5–6 Oct 2026 is highlighted.\n\nYes: save the AAVSO report|light-curve CSV for just that night.\nNo: save the … for the whole series (N nights).\nCancel: don't save." Save plot PNG never asks: it saves what is on screen (the zoomed night) and joins that night's file set.

### 5.3 "Save AAVSO report" (Accent button; `save_aavso` 12409, `_report_text` 12381, `core.aavso_report` 5297)
Checks in order:
1. No points → showinfo "Run photometry first."
2. Star-name spelling (`core.suggest_star_name`): askyesno "The Star ID is “hd219134”. AAVSO spells it “HD 219134”, and WebObs may not match the other spelling to the star.\n\nUse “HD 219134” in the report?" (Yes rewrites the Star ID field.)
3. Problem list → askyesno "Before you upload:\n  • AAVSO observer code is empty\n  • Chart ID is empty\n  • Star ID is empty\n  • the series mixes filters\n  • ensemble report (more than one comp) without a check star; AAVSO requires one\n  • a comp has no star name (the report would call it “comp1”)\n\nSave the report anyway?"
4. Check-star name (`_name_report_check` 12472): if the check is only a placeholder ("check", "comp2"…) and has no AUID, askstring "The check star has no name in this series (the report would say KNAME = check), and AAVSO needs to know which star it is.\n\nType its AUID or catalog name (for example 000-BJR-417 or TYC 4006-956-1):" (prefilled from the chart star under the check marker if any). Empty/placeholder → showinfo "Report not saved: it needs the check star's name (its AUID or catalog name). Save it again and type the name." and no report. The typed name is stored in `report_names` (saved with the series).
5. Scope question (§5.2). Points = usable points (unflagged unless "Leave flagged…" is off) of the scope, **minus any `transit`-flagged frame, always** (log "AAVSO report: N frames in a planet transit left out of the report."). None left → showinfo "No usable points to report for that choice."
6. Duplicate-upload warning (`_already_reported` 12573): "These nights are already in another AAVSO report:\n  • <night>: already saved in its own report | a whole-series report, <file> (<date>)\n\nIf that report was uploaded, uploading this one too puts the same observations into the AAVSO database twice. Upload only one of them for each night.\n\nSave this report anyway?" (records live in meta `reports_saved`, written back to the series file after each save).
7. Save ("Save AAVSO extended report", .txt) → ASCII file; status/notice advice "Upload with the AAVSO WebObs file tool after you check the observer code, chart, and comparison magnitude."; then `offer_upload`: status line "Saved <file> — upload to AAVSO WebObs:" with a button "Open AAVSO WebObs…" appearing at the right end of the status bar (disappears when the status text changes).

**Report contents** (AAVSO Extended File Format, comma delimited):
```
#TYPE=Extended
#OBSCODE=<observer>            (na if blank)
#SOFTWARE=SHOBS-P 2.2.17
#DELIM=,
#DATE=JD
#OBSTYPE=CCD
# Created 121153Z OCT 26 by SHOBS-P
#NAME,DATE,MAG,MERR,FILT,TRANS,MTYPE,CNAME,CMAG,KNAME,KMAG,AMASS,GROUP,CHART,NOTES
<star>,<JD .5f>,<mag .3f>,<merr .3f>,<FILT>,NO,STD,<CNAME>,<CMAG>,<KNAME>,<KMAG>,<airmass|na>,na,<CHART>,<NOTES>
```
- NAME = Star ID (fields truncated/cleaned by `_field`). FILT = each point's filter (TG/V/SI…). MERR = `point_err` (photon + scintillation + haze excess).
- CNAME = the single comp's name, or **ENSEMBLE** with ≥ 2 comps (then CMAG = na, and NOTES gets "ens <comp1> <comp2> …"). Comps/check are named by the stars the reduction actually used (`active_comps` from Use comps… else `comps_used`), replaced by their **AUID** when the star came from the AAVSO sequence (`seq_auids`, 2.2.13) or by a typed `report_names` entry. CMAG/KMAG are instrumental magnitudes.
- KNAME/KMAG = check star (AUID/name) and its instrumental mag; "na" when no check.
- AMASS = frame airmass (needs site + pointing) else na. GROUP = na. CHART = Chart ID (≤ 20 chars).
- NOTES = Output-page "Notes" entry (commas and | → ;) [+ "; comp/check mags SI from APASS DR9 2; Gaia DR3 G/BP-RP (+/-0.04) 1" for a Sloan run, 2.2.17] [+ "; ens …"] + "; night JD2460xxx" + "; <flags>" if the point carries flags (only possible with "Leave flagged points…" unticked).
- Pitfalls: a Sloan run's comp-source note is dropped if the comp set changed via Use comps… or the series predates 2.2.17; flagged points enter the report only when the QC checkbox is off; AAVSO requires a check star for ENSEMBLE (warned, not blocked).

### 5.4 "Open AAVSO WebObs…" (`open_webobs` 1579)
Opens `https://apps.aavso.org/v2/data/submit/photometry/` in the default browser; status "Opened AAVSO WebObs (upload the report with its file tool) in your browser." or "Could not open a browser: go to <url>". Same page as the status-bar button after a report save.

### 5.5 "Save light-curve CSV" (`save_csv` 12601, `core.write_curve_csv` 5406)
Scope question (§5.2), then file `<stem>_lightcurve.csv`. ALL points of the scope are written (flagged ones too, with their flag word) — not just usable. Header line `# Created … by SHOBS-P`, then columns: jd, mag, merr, filter, zero_pointed_mag, comp_inst_mag, check_inst_mag, check_reduced_mag, airmass, exptime, fwhm_px, peak, raw_peak, sky, flag, night, segment ("before flip"/"after flip"/"no flip"), segment_normalized_mag, b_minus_v, v_minus_r, b_minus_v_diff, v_minus_r_diff, color_calibration, target_x/y, comp1_x/y, comp2_x/y, check_x/y, night_note, file, scint_mag, err_total, comp3_x/y… (appended), frame. Status "Wrote <path>". Airmass-corrected means are never in it.

### 5.6 "Save plot PNG" (`save_png` 12619, `_save_png_with_header` 12636)
Saves the 2×2 figure as shown (zoomed night if highlighted) at 140 dpi, with a header laid out like the page: title "<Star>   SHOBS-P 2.2.17   Created <DTG>", the Series/Period/Color boxes side by side (monospace), the night table (Note column only if any note), the spread line, and up to 14 wrapped caution lines in red prefixed "! ". Status "Wrote <path>".

### 5.7 "Refit period" (`refit_period` 12315)
Re-applies cloud flags and the period search, redraws. Fewer than 8 usable points → showinfo "Need at least 8 usable points." (then still redraws).

### 5.8 "Export…" → ExportWindow (see §6.3). "Back" → Photometry step. "◀  Transit View" (Transits mode only) → transit fit page.

## 6. Popups from the Series row

### 6.1 "Comp health…" → CompHealthWindow (`show_comp_health` 12202; class 12768)
- Guards: no series → "There is no series loaded."; no per-comp data → "This series has no per-comp measurements yet. They are saved from version 1.6 on, so reduce the nights again to use the comp health check."; too few → "The health check needs at least two stars (two comps, or a comp and a check star)."
- Window title "SHOBS-P — comparison star health". Heading "Comparison star health" + explanatory hint ("Each star is measured against the average of the others in the same frame, so clouds and airmass cancel. A steady star stays flat from night to night. A star that drifts or jumps is quietly variable or affected by something local … and should be replaced." + one-night note "Only one night is loaded, so this shows each star through the night instead; the night-to-night check needs two or more nights.").
- Plot: multi-night → each star's night median minus its own median vs night ("Night-to-night behaviour of each star (should be flat)"), spares dashed/thin; one night → "Within-night behaviour of each star (one night: should be flat and not jump at the flip)", stars offset vertically, binned medians as lines, purple dashed "meridian flip" line. Legend in a column at right; selecting a table row highlights that star's curve (others fade).
- Table columns: "Star", "Role" (comp/spare/check), "Night offset", "Scatter in night", "Flip step", "Both pier sides", "Nights", "Pixel (x, y)", "From target", "Verdict". Verdict words (core 7700–7728): "steady", "steady tonight (one night)", "SUSPECT: drifts night to night", "SUSPECT: jumps +0.0xx at the flip", "SUSPECT: noisier than the others tonight", "too few points", "pair drifts; add a 3rd star to tell which", "one night; steady pair", "one night; pair jumps at the flip, add a 3rd star to tell which".
- Hint lines (as applicable): "Left out of the reference so their drift does not echo in the steady stars: …"; pixel/From-target explanation; "Spares are extra stars measured every frame. Use comps… (Output page) can swap a steady spare in for a SUSPECT comp without reducing again."; pier text "Both pier sides (flip step after minus before, median over the nights with both sides): steady on both sides, good comps for the campaign: …. steps at the flip, better not as comps (or use 'Pier side as segments'): …. A target that steps while steady comps do not points at the field position (flat, a ghost or the star's halo), not the comps."; red "⚠ Ensemble zero point: …" when the ensemble shifts a night by > `ZP_WARN_MAG` against the comps nearest the target's brightness.
- Buttons: "Watch selected spare" (adds the selected spare row to the watch list; "Select a spare in the table first." / "Only spare comps can be added here; comps and the check star are already measured every run."), "Close".
- Frames flagged by a planet transit still count for the comps (only the target dims).

### 6.2 "Use comps…" → UseCompsWindow (`choose_comps` 12084; class 13023; `apply_comp_selection` 12093)
- Guards: "There is no series loaded."; "This series does not record which comps it used; reduce it again in 1.8."
- Title "SHOBS-P — comparison stars in use". Intro: "Tick the stars to average as the comparison. Every point is re-derived from the measurements already stored, so nothing is reduced again, and you can switch back at any time. Spares were measured on the nights reduced in 1.8 or later. The check star cannot be a comp. Colors (B-V) stay tied to the comps the nights were reduced with."
- Grid columns: "Use" (Checkbutton), "Star" (aliases shown "TYC … = APASS …"), "Role" (comp / spare[, "(widened window): N mag fainter than the target"]), "Catalog mag", "Nights measured" ("3 of 8"), "Night offset", "Scatter in night", "Comp health" (verdict; SUSPECT in red).
- Hints: "No spare comps in this series yet. Nights reduced in 1.8 or later after Solve carry spares." / "Only two stars to choose from. With three or more, …" / "Spares from a widened window (a thin field) are much fainter than the target: their noise can dominate the ensemble and they can shift its zero point. Prefer comps within about a magnitude of the target."
- Buttons: "Apply" (Accent) — none ticked → "Tick at least one comparison star."; "Back to the original comps" (reselects `comps_used`); "Close".
- On Apply: showerror "No catalog magnitude for <names>." if a chosen star has none; askyesno "N point(s) lack the stored values needed to change comps (their comp magnitudes were not saved) and would be left out. Continue?"; askyesno "N point(s) on JD… lack one of the chosen stars (it was not measured that night, or not in those frames). They will be flagged 'nocomp' and left out. Continue?". Then every magnitude is re-derived (`core.recompute_with_comps`), `active_comps` set (empty when identical to the original set), period refit, log "Comps in use: …. N points re-derived[, M flagged nocomp].", zero-point check logged, open series auto-saved; status "Comps in use: ….[ Saved to <file>.]" In Transits mode the transit fit refits. Nights added later keep the chosen comps (8893).
- Pitfall: the AAVSO report names the active comps; the Sloan comp-source note is dropped after a comp change.

### 6.3 "Export…" → ExportWindow (`open_export` 12274; class 13857)
Guard: "Run photometry or load a series first." (details below in §6.6).

### 6.4 "Watch stars…" → WatchWindow (`show_watch` 12077; class 13722)
Guard: "No watch stars yet. On the Photometry page pick Watch and click a star, or use Watch in Comp health or the field scan." (details §6.7).

### 6.5 "Time of max/min…" → ExtremumWindow (`show_extremum` 12195; class 13314)
Guard: "There is no light curve yet: run photometry or load a series." (details §6.8).

### 6.6 ExportWindow (class 13857; `core.write_export` 7757) — "Export…" button
- Title "SHOBS-P — export". Intro: "Values follow the Output page: flagged points left out if that box is ticked, check-star correction and segment line-up as set there. The file header records every choice."
- "Saved setup" row: editable `Combobox` of named setups + "Load" ("Pick a saved setup first." if none), "Save as this name" ("Type a name for this setup in the box first."; status "Saved export setup '<name>'."), "Delete". Setups are stored in `~/.shiloh_photometry_exports.json` (`EXPORT_SETUPS_FILE`); scintillation settings are not part of a setup.
- Group "Time" (`Radiobutton`s): "JD (UTC, geocentric) — AAVSO" (default), "HJD (UTC, heliocentric)", "BJD_TDB (barycentric) — exoplanet and timing work" (HJD/BJD need RA/Dec; failure → showerror "Time conversion: <exc>").
- Group "Values": "Magnitude" (default) | "Relative flux (median = 1)".
- Group "Points": "Every point" (default) | "Binned:" + `Combobox` of Night / 60 min / 30 min / 15 min / 10 min.
- Group "Extra columns (every-point export)" `Checkbutton`s: "Error", "Night", "Segment (flip)", "QC flag", "Airmass", "Check star", "Comp instrumental", "FWHM", "B-V", "Night note" (default on: Error, Night, QC flag, Airmass).
- Buttons: "Export…" (Accent) → file `<Star>_<TIME>_<mag|flux>.csv` via `output_path` (output folder or dialog "Save CSV"); "Close".
- File: header lines "# SHOBS-P 2.2.17", "# Created <DTG> by SHOBS-P", "# target: …   filter: …   observer: …", "# comparison stars: …   check: …", "# time: JD_UTC (mid-exposure)   values: magnitude|relative flux, median = 1", "# processing: aperture r=… sky …-… px; check-star correction on|off; segments lined up yes|no; flip lined up yes|no; flagged points left out|included; cloud flag at N%; <scint note>", "# note JDxxx: <night note>", "# credits: see the app's Credits and sources page". Every-point columns: time, mag|flux, then chosen extras (err, night, segment, flag, airmass, check_mag, comp_inst_mag, fwhm_px, b_minus_v, night_note). Binned columns: time, mag|flux, err, n, night. Uses only usable points (`_usable`) and the analysis values (check-star correction / line-ups as on the page). Status "Exported N rows to <path>".

### 6.7 WatchWindow (class 13722) — "Watch stars…"
- Title "SHOBS-P — watch stars". Hint: "Each watch star is measured against the comparison stars in use, frame by frame. Cloud, drift, and frames without the star are left out. Magnitudes are untransformed, like the target's."
- Table columns: "Star", "Catalog mag", "Points", "Nights", "Night-to-night spread", "First → last night", "Known as" (VSX/SIMBAD note). Selecting a row draws its curve: every frame (small, faint) + black "night mean" diamonds; title "<name>   (N points, M nights)" or "<name>: no measurements yet (it is measured from the next run on)". Time axis follows the Output page setting.
- Buttons: "Export watch light curves CSV…" → `watch_stars.csv` (output folder or "Save CSV" dialog), header "# SHOBS-P 2.2.17: watch stars, measured against the comps in use; untransformed", "# Created … by SHOBS-P", columns star,jd,mag,night,file; status "Wrote N rows to <path>". "Stop watching selected" (removes from the watch list and reopens the window if any remain). "Close".
- Watch values (`watch_series` 12061) = comps' catalog mean + (star − ensemble) per frame, skipping frames flagged cloud/cloudedge/haze/drift/noflux/nocomp; they follow a Use comps… choice.

### 6.8 ExtremumWindow (class 13314) — "Time of max/min…" (2.2.14 item 13)
- Title "SHOBS-P — time of maximum / minimum". Hint explains: local polynomial timing; error from 500 bootstrap refits with red-noise factor β, never less than half the difference from the Fourier series (and for a minimum from Kwee & van Woerden); O−C against the elements (VSX epochs are HJD); saved times go into one CSV per star next to the series file, growing into an O−C diagram.
- Form row 1: "Night" read-only `Combobox` (night labels; default = highlighted night else last), "Type" `Combobox` "maximum" | "minimum" (default from VSX var type), "Half-window (min)" `Entry` ("blank = automatic").
- Form row 2: "Period (d)" `Entry`, "Epoch" `Entry`, system `Combobox` "HJD" | "BJD", "Elements from VSX" `Button` (needs a Star ID: "Type the star's name in Star ID on the Input page first."; while running the source label says "looking up <star> in VSX…", then "VSX <type>[ (no period/epoch there; type them)]" or "VSX: <error>"), source label "VSX" / "typed".
- Result text (monospace) starts "Choose the night and press Measure."; after Measure: "Tmax  HJD … ± …  (x.x min)   BJD_TDB …   JD …", "polynomial deg N: JD … ± … min (N points, ±M min)", "Kwee-van Woerden: …", "Fourier (N harmonics, rms …): …", "O−C +0.0xxxx d = +x.x ± y.y min   cycle E   (C = HJD …; P …, E …)", notes, "⚠ warnings", "No elements: type the period and epoch (or Elements from VSX) for the O−C." or "No extremum found."
- CSV line: "Times CSV: <path>  (N times of <star>)" or "Times CSV: not chosen yet (no series file or output folder); Save asks where."
- Buttons: "Measure" (Accent; < 12 usable points → "That night has too few usable points (12 or more needed)."; bad half-window → showerror "Half-window: minutes, or blank for automatic."), "Save to times CSV" (enabled after a result), "Choose times CSV…" (save dialog "Times of maximum / minimum CSV (new or existing)", no overwrite confirm), "Close".
- Times CSV: `<Star>_times_of_extremum.csv` (`core.extremum_csv_name` 7482) beside the series file (else output folder, else asks). `save_extremum_row` 7536: appends a row, or REPLACES a row of the same star/type within 0.01 d (a re-measurement), keeping the "# Created" line and adding "# Updated". Columns include star, type, hjd, bjd_tdb, jd_utc, error_d/min, method, cycle, o_c_d/min, period_d, epoch, elements ("VSX HJD"), night, n_points, window_min, kvw/poly/fourier times & errors, time_note, note. Log "Time of maximum saved to <file> (added|replaced)."; status "Saved to <path>". Error → showerror "Could not write <path>: <exc>".
- Plots: left = the night's points with red polynomial fit and lines (red solid = adopted, blue dashed KvW, green dotted Fourier, purple dash-dot predicted C); right = O−C diagram from the CSV ("● saved max · ■ saved min · open red = this night, not saved yet") or placeholder "The O−C diagram appears here once times are saved to the CSV (and elements are given)".
- Pitfall: the CSV chosen earlier is reused only for the same star; loading another series resets it.

### 6.9 KnownVariablesWindow (class 14023) — Discovery / field scan, not the Variables Output page
Opened from the scan view's "Known variables measured…" button (14198); title "SHOBS-P — known variables in the field". Lists every VSX + SIMBAD variable in the scanned frame: columns "Name", "Source", "Type", "Scan star", "Mag", "Scatter", "× normal", "In this scan" ("VARIED: a scan candidate" / "steady tonight, within the noise" / "somewhat more scatter than normal" / "measured on too few frames" / "saturated: clipped, so not usable tonight" / "not measured (faint, saturated, blended, or not detected)"). Selecting a measured row shows its curve in the scan window; "Watch selected" adds it to the watch list (by sky position for unmeasured ones). If the scan had no sky positions / no network: "This scan has no sky positions, so it could not be checked against VSX and SIMBAD. Solve before scanning." / "VSX and SIMBAD could not be reached during the scan. Scan again when online."
Related: the Photometry page's Discovery panel button "Known variables in the field…" (`_show_known_in_field` 4316) shows a plain info list "N known or suspected variable(s) in the frame: …" (or "No known variables marked yet: the field check runs when the field is solved (VSX and SIMBAD must be reachable).").

## 7. New-user pitfalls (summary)
1. Load the series BEFORE reducing the new night; otherwise the run is a single night and the add-night question never appears (Input line says so).
2. Star positions are not saved in the series: re-mark target/comps each night; the add-night question warns if different stars were used.
3. "No" to the add-night question silently starts a new unsaved series — the old file is untouched, but you must Save series under a new name or the night is lost on close (Close/Load offer a save).
4. Auto-saves re-write the open file after many actions (add/remove night, notes, Use comps, planet flags, AAVSO report bookkeeping). The only undo is `X_prev.cvseries`, which holds the state from before the FIRST write of this session.
5. Period "significant" needs FAP ≤ 1% on both points and nights, not carried by one night, not airmass-demoted; a "Best peak (not significant)" is not the star's period.
6. Click a night row to zoom; forgetting to click it again leaves the plot zoomed, and Save AAVSO/CSV then ask about one-night scope and Save PNG saves that night only.
7. Transit-flagged frames never enter an AAVSO report even when "Leave flagged points…" is unticked; the CSV, however, contains ALL points with their flags.
8. ENSEMBLE reports need a check star; a check named only "check" blocks the report until an AUID/catalog name is typed.
9. Airmass-corrected night means are display only; the hover and footer say so.
10. Export setups live in the user's home folder (`~/.shiloh_photometry_exports.json`), not with the series.
