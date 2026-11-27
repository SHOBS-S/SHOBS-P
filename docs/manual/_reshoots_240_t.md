# 2.4.0 fit-in F4 screenshots

No screenshots are taken until 2.4.0 is complete. One heading per builder package.

## F4-1 (solve, stars and the run)

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/08-photometry-page.png (and any shot with the line under "Measured with") | 8 | A comp off the frame is now a plain line *C3 left out of the whole night: missing from … frames …* followed by *The night uses … in every frame.* (no orange ⚠ unless fewer than two comps are left) | RESHOOT if the shot shows that line |
| img/11-comp-health.png | 11 | Verdict column can read *over the linear limit in N of M frames (left out of those nights)* | RESHOOT only if the shot's night has an over-limit star |
| img/08-suggest-window.png | 8 | Aperture test with no check star: the table's first scatter column is headed *steadiest spare scatter*, with the line *No check star is marked, so the test judges on the steadiest spare (…)* | RESHOOT if the shot shows a test without a check star |
| img/08-stars-panel.png | 8 | No new look. Slot note after typing a name over a marked comp now reads *✓ … renamed to …; the marker stays where it is and the magnitude is unchanged …* or the slot takes the new star | no reshoot unless the note is in the shot |
| Log shots (ch 7, 8) | 7, 8 | New plain lines: *Flats: N of M used for the master flat …*, *Aperture re-fitted to this field …*, *Binning changed: … re-centred.*, *… marker re-centred on its star before the run …*, catalog lines ending *It is asked again by itself in about 45 s …* | RESHOOT only where a shot shows the old wording (*Solve again to use them*, *Check them before running*, *that looks like a meridian flip*) |

New question box (no shot planned): *The … marker at (x, y) is N px from the nearest star … Move the marker onto that star?* (Move it onto that star / Leave it).

F4-1, second part (sheet sweep and nits):

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/05-input-page.png (and any shot of the Site box) | 5 | No new look in normal use. A gray line appears inside the Site box only when the boxes are half filled or the computer's time zone is unknown | no reshoot |
| any shot of the Stars panel with a comp placed by the solve | 8, 9 | The slot line is now the bare *✓ placed after the solve* (the coordinates and "magnitude kept as loaded" are in the Log only) | RESHOOT if the shot shows the long line |
| any shot of the planet boxes after a failed lookup | 16 | *✖ … not found (…): check the spelling and press Look up again, or type the values.* under the boxes, no error box | RESHOOT only if a shot shows *Lookup failed.* |
| NEW (optional) 03-log-window | 3 | The **Log** window that **Show log** in the Findings window opens from a page with no log (Output, Transit fit …) | NEW shot if wanted |

Boxes changed (no shot planned): the telescope aperture question is a box with a number field (OK / Cancel); *Not solved: …* boxes are error boxes with the cause first; *No light frames found: …*, *Something went wrong: …*, *Could not write the demo set: …*, *No catalog magnitude for …* say what to do. Gone as boxes (status line now): *Calibrate first …*, *All 10 comparison stars are marked …*, *No night has frames in a predicted planet transit …*, *Planet lookup failed*.

## Package F4-2

Variables Output, series, Time of max, Discovery table (fit-in F4, builder 2). Changes that show in pictures: the
Output page's View row says **Plot bins** where it said **Bin**; the Period box can show a plain **Range   searched
…: widened from … to cover …** row, a **Saved** row and an **Edge** row, never a **Range ⚠** row or a **Search … d
instead** button; the Time of max/min window has a new button under the elements, **Store O−C of all saved times
with these elements**; the Field scan page's line under the table is plain grey text with no button; the scan's
periodogram title is black with no ⚠. None of these shots has been taken (the builder cannot take them). Do not
reuse an old image for a page that changed: reshoot it.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/09-output-page.png | 09 | View row: **Plot bins** (was **Bin**); the Period box may hold the new **Saved** row | RESHOOT |
| img/10-series-output.png | 10 | same page: **Plot bins**; Period box rows as above | RESHOOT |
| img/10-night-zoom.png | 10 | same page: **Plot bins** if the View row is in the shot | RESHOOT if the View row shows |
| img/12-flagged-points.png | 12 | same page: **Plot bins** if the View row is in the shot | RESHOOT if the View row shows |
| img/13-period.png | 13 | the Period box: no **Range ⚠** row and no **Search … d instead** button; a FAP reads **< 1e-99**, never **0.0e+00**; an at-the-edge peak has a plain **Edge** row | RESHOOT |
| img/14-extremum.png | 14 | new button **Store O−C of all saved times with these elements** under the Period / Epoch row | RESHOOT |
| img/20-field-scan-output.png | 20 | the line under the table (when a known variable's VSX period is outside the range) is plain grey, no orange ⚠ and no button; the periodogram title of an at-the-edge star is black: *… (the peak is at the edge of the search)* | RESHOOT |
| img/03-findings.png | 03 | the window itself is unchanged (**Show log** now opens a Log window from pages without a Log) | reuse |
| img/06-blink-page.jpg, img/06-rejected-list.jpg | 06 | unchanged (the first line before any frame is loaded reads **Rejected frames: none**, as it always did once frames were loaded) | reuse |
| img/12-transmission-keep.png | 12 | the Keep question is two short lines now (*“Use these points”: they go into the light curve with larger error bars …* / *“Leave them out” (the default …): they stay flagged, like cloud.*) | RESHOOT |
| img/13-period.png (again) | 13 | only if the shot is of a night with no site: the Period box has the plain row **Airmass not checked (no site or RA/Dec)** | RESHOOT (same shot as above; nothing more if the site is typed) |
| img/10-series-output.png (again) | 10 | only if the series in the shot has nights from before 2.2.17: the *… keep the old, larger error bars …* line is plain grey text under the night table, not an orange ⚠ line | RESHOOT (same shot as above) |
| OPTIONAL NEW img/13-no-search.png | 13 | the Period box with *Period  None: no search: the min period must be above 0 and below the max period; … Check the Period search range.* and the plot title **No period search** | NEW SHOT (optional; no image link yet) |
| OPTIONAL NEW img/13-range-widened.png | 13 | the Period box with *Range   searched 0.02–28 d: widened from 0.02–20 d to cover VSX 22.1 d* | NEW SHOT (optional; no image link in the chapter yet) |
| OPTIONAL NEW img/12-choose-night.png | 12 | the small window *N nights have frames in a predicted planet transit. Which night do you want to look at?* with the **Night** box, **Choose this night** and **Cancel** | NEW SHOT (optional; no image link yet) |
| OPTIONAL NEW img/03-log-window.png | 03 | the Log window opened by **Show log** from the Output page | NEW SHOT (optional; no image link yet) |
| all others | | nothing of F4-2 in view | reuse |

## Package F4-3

Transit fit page, Multi-night window, Use comps window, Discovery scan errors (fit-in F4, builder 3). Changes that
show in pictures: the Transit fit page's Fit row has a new button, **Fit with saved priors…**; after the comp check the
result line and the **Depth (%)** row give two error terms (*1.84 ± 0.05 (stat) ± 0.04 (comps)*); a button **Open Use
comps, untick …** can appear in the Comps row; the Multi-night window has one **Detrend** drop-down (Auto (BIC) first)
where it had two boxes, no orange lines and no **Fit every night against time only** button, and its two saves are
greyed before a fit; the Use comps window can have a **Flip step** column. None of these shots has been taken (the
builder cannot take them). Do not reuse an old image for a page that changed: reshoot it.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/17-transit-fit-page.png | 17 | Fit row: **Fit with saved priors…** after **Cancel**. With two or more comps, once the comp check is done: result line *Comps checked: depth … ± … (stat) ± … (comps) %; Tc ± … (stat) ± … (comps) min (all comps … %; without each …–… %)*, and the **Depth (%)** row *… ± … (stat) ± … (comps)* | RESHOOT |
| img/17-multinight-window.png | 17 | **Detrend** is one drop-down showing *Auto (BIC)* (the *airmass* and *time slope* boxes are gone); Status column plain (*✔ ready (no airmass in the file: baseline fitted against time)*, *✔ ready (filter SR: its own depth and limb darkening)*); one plain grey line under the table instead of an orange ⚠ and the **Fit every night against time only** button; **Depth and limb darkening per filter** ticked by itself with nights in two filters; the Summary tab's *Nights* lines start *baseline …*; **Save timing CSV…** / **Save plot PNG…** greyed before a fit | RESHOOT |
| img/17-multinight-corner.png | 17 | a night with a time curve adds a *c_t2* panel when **Show all parameters** is on; the default view (shape and Tc) is unchanged | reuse, unless the shot shows all parameters |
| img/17-trim.png | 17 | Fit row in view: **Fit with saved priors…** | RESHOOT if the Fit row is in the shot |
| img/17-flip-step.png | 17 | Fit row in view: **Fit with saved priors…**; Depth row with *(stat) ± (comps)* after the comp check | RESHOOT if the Fit row or the table is in the shot |
| img/17-exoclock-buttons.png | 17 | Results row only: unchanged | reuse |
| img/18-inconclusive-verdict.png | 18 | Fit row in view: **Fit with saved priors…** | RESHOOT if the Fit row is in the shot |
| img/19-exotic-side-by-side.png | 19 | Fit row in view: **Fit with saved priors…** (a report fit has no comp check: the Depth row is unchanged) | RESHOOT if the Fit row is in the shot |
| img/11-use-comps.png | 11 | a **Flip step** column (*+65 mmag* / *—*) and its hint line, only when a night of the series has a meridian flip; the ten-night Variables series of the shot has it if any of its nights flipped | RESHOOT if the series has a flip night, else reuse |
| OPTIONAL NEW img/17-fit-with-saved-priors.png | 17 | the Fit row reading **Fit with the current priors** and the note *This fit used the priors saved in …_settings.txt (period, T0, a/R\*, b, limb darkening), not today's looked-up values.* | NEW SHOT (optional; no image link in the chapter yet) |
| OPTIONAL NEW img/17-untick-comp.png | 17 | the Comps row with **Open Use comps, untick C3** and the Use comps window opened from it (C3 unticked, the line *C3 is unticked here because the Transit fit moves most when it is left out. …*) | NEW SHOT (optional; no image link yet) |
| img/20-field-scan-output.png | 20 | nothing in view (the error floor is a Log line; a star's χ²ν × normal can move by a few percent) | reuse |
| img/17-multinight-window.png (Summary tab, if shown) | 17 | only when the shot shows a timing ⚠: the two warnings read differently (*⚠ Check the time system of every night first …*; *… Compare with one that gives its ±: …*) | RESHOOT only if a timing ⚠ is in the shot |
| img/20-field-scan-output.png | 20 | #115 retune: the level stays 2.5 and the floor 0.003, so nothing in view changes | reuse |
| (no shot) | 17 | status bar after a save: *The AAVSO exoplanet report saved earlier (…) is of the fit before this one: …* | none |
| all others | | nothing of builder F4-3 in view | reuse |

## Merge session (m028, scan-star edge rule)

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| any Stars panel shot with a *drifts toward the frame edge later in the night* line | 8 | the line is plain now (no ⚠, not orange) | RESHOOT if the line shows |
| img/20-field-scan-output.png | 20 | a star at the frame edge on some frames may show *at the frame edge on N frames (not judged by its own errors there): check its light curve* in Check first; the suspect list can be shorter | RESHOOT |
