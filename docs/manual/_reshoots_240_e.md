# 2.4.0 step 4 screenshots

No screenshots are taken until 2.4.0 is complete. Each builder appends a section for its issues.

## #110, #111 (s4-quick)

#110: the AAVSO exoplanet report writes one DETREND_n column per name in #DETREND_PARAMETERS (a text file; nothing on
screen changes). #111: lights from another camera than the last ones empty the Bias, Flat and Dark flats boxes left
over from that camera (Dark unless it fits), reset the aperture and sky ring (set from the new frames' star sizes at
Calibrate) and set raw sat ADU / linear ADU for the new camera; three plain lines appear under the Frames boxes on the
Input page while those lights are in the Lights box. The aperture caution leaves the Findings list when the boxes are
fixed. The New reduction tooltip has one more sentence.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/05-input-page.png | 05 | nothing when the shot's lights are the session's first or the same camera (no note under the Frames boxes) | CHECK, reuse if no note shows |
| (new) img/05-another-camera.png | 05 | optional new shot for "Lights from another camera": the Frames box after New reduction and a MicroObservatory Lights folder, with the three plain lines under it and Bias / Flat emptied | NEW (optional; ask John) |
| img/07-calibrate*.png | 07 | nothing (text only: the boxes are emptied before Calibrate) | reuse |
| img/08-*.png showing the aperture row | 08 | nothing in the row itself; values differ only after a camera change | reuse |
| any shot of the Findings list with *Aperture r = … (kept from before)* | 08 / 23 | the finding now leaves when the boxes are fixed; its words are unchanged while kept | CHECK, reuse if none shows it |
| img/19-*.png, img/17-*.png | 17 / 19 | nothing (#110 is the report file's text) | reuse |
## Placement after the solve, #71, #77 (s4-place)

After a solve the series' comps and check are placed from their saved sky positions and centered like a click, with
one Log line per star; a star off the frame keeps its name (row: *not on this frame tonight*); a marker that cannot be
centered says so (row, status line, Log, Findings); a new star's name follows the fixed order (AUID / AAVSO label,
TYC, Gaia, APASS) and is picked by brightness; a new question before Run for series stars without a marker.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/08-stars-panel.png | 08 | rows of series comps now read *✓ placed after the solve at (x, y), centered (0.8 px); magnitude … kept as loaded* (the words after "at (x, y)" are new) | RESHOOT |
| img/08-stars-panel.png (same shot) | 10 | chapter 10 points at the same rows; a new comp clicked on an AAVSO sequence star now shows its AUID as the name | covered by the reshoot above |
| any shot of the Photometry Log after a solve on a loaded series (08, 10) | 08, 10 | the single *Comps/check placed after the solve: …* line is gone; one line per star (*Comp 1 … placed from the series position, centered (… px); magnitude … kept as loaded.*) | CHECK, reshoot if the Log is readable in the shot |
| any shot whose comp rows show a comp picked by a click on a star with several catalog names (08, 09, 11, 15) | 08, 09, 11, 15 | the name shown can differ (Gaia before APASS; AUID for a sequence star) when the shot is taken again with a fresh click; a loaded series keeps its names | CHECK, reuse when the series was loaded |
| new: a comp row and status line with *⚠ Comp 4 … is at the catalog position (x, y), not centered on a star: …* and its Findings entry | 08 (Place a star by name) | new message; no existing shot (needs a star the wider search cannot center, e.g. two catalog stars close together) | optional new shot |
| new: a comp row reading *not on this frame tonight (N px beyond the top edge); not measured, the name stays* | 10 | new row text; no existing shot | optional new shot |
| new: the question *These stars of the series have no marker on this image …* (Go on without them / Clear those names and go on / Stop, so I can mark them) | 08, 09 | new question box | optional new shot |
| img/08-photometry-solved.jpg and other whole-page shots | 08 | nothing of this in view unless the Log or comp rows are legible | reuse |
## #72 star record, #56, #67 (s4-record)

The series keeps a record per comp and check star; an older series converts on Load (a Log line and a `_backup`
file). The Output page's Series box has a new **Mags** line (each comp's and the check's magnitude and source). On
the Photometry page a **Series magnitudes…** button appears under the Stars panel only while a catalog (or a typed
value) disagrees with the series; a comp's name box has a hover note (other names, each catalog's magnitude). A new
star's name follows a fixed order (AUID, TYC, Gaia, APASS), so a slot may show a Gaia or TYC name where it showed an
APASS name.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/09-output-page.png | 09 | the Series box has one more line, **Mags** (value and source per comp, then the check) | RESHOOT |
| img/10-series-output.png | 10 | the same Series box: the new **Mags** line | RESHOOT |
| img/10-night-zoom.png | 10 | the Series box, if it is in view: the new **Mags** line | CHECK, reshoot if the box shows |
| img/08-stars-panel.png | 08 | comp/check names of stars new to a series may be the AUID, TYC or Gaia name instead of the APASS name; no Series magnitudes… button unless a value disagrees | CHECK, reshoot if the names differ |
| img/09-marked-stars.jpg, img/08-photometry-solved.jpg | 08, 09 | marker labels follow the slot names (same note as the Stars panel) | CHECK, reshoot if the names differ |
| img/11-use-comps.png, img/11-comp-health.png | 11 | nothing (Use comps… and Comp health are unchanged) | reuse |
| img/10-colour-box.png | 10 | nothing | reuse |
| NEW: the Stars panel with a disagreement and the Series magnitudes… window | 10 | new in 2.4.0 ("The series' magnitudes win") | NEW SHOT when 2.4.0 is complete |
| all others | | nothing of #72 / #56 / #67 in view | reuse |

## Step 4 review fixes (M1-M3, S1-S6)

No screenshots were taken. Words changed in view of existing shots, and new windows:

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/08-stars-panel.png | 08 | rows of series comps now read *✓ placed after the solve at (x, y), centered (0.8 px from where the position falls); magnitude … kept as loaded* ("from where the position falls" is new) | RESHOOT (already on the list above; shoot once, with these words) |
| any shot of the Photometry Log after a solve on a loaded series (08, 10) | 08, 10 | the per-star lines say *centered (N px from where the position falls)*; a wider-search placement is now a ⚠ line and a Findings entry | RESHOOT (already on the list above) |
| img/05-another-camera.png (the optional new shot above) | 05 | the aperture / limits lines may read *… left as they are …* when a series was loaded or a value typed since the last night; for the shot, use untouched boxes so the three reset lines show | note for the new shot |
| NEW: the Load series question *The series' comps in use include …, but no comp box holds it …* with **Leave it out** / **Keep it** | 10 | new in 2.4.0 ("Load never changes the comps in use without asking"); needs a pre-2.4.0 series saved with a cleared comp still in its Use comps choice | NEW SHOT (optional) when 2.4.0 is complete |
| NEW: a comp row with *⚠ … centered by a wider search … Check it is the right star* and its Findings entry | 08 | the wider-search placement is a ⚠ with a finding now | NEW SHOT (optional) |
| all others | | nothing of the review fixes in view | reuse |

## Step 4 second review fixes

No screenshots were taken. No existing shot changes; new or changed question windows:

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| the optional new shot of the Load series question (row above) | 10 | the accented (default) button is now **Leave it out**, not **Keep it** | note for the new shot |
| NEW: the Save question *… is in the series' comps in use, but you cleared its comp box. …* with **Leave it out** / **Keep it** | 10 ("Comps you clear stay cleared") | new question at a save after a Use comps star was cleared with ✕ | NEW SHOT (optional) when 2.4.0 is complete |
| img/05-another-camera.png (the optional new shot above) | 05 | going back to the first camera in one session reads *Aperture and sky ring put back to r …, as last used with this camera in this session.*; not in view when the shot goes A to B only | note for the new shot |
| all others | | nothing of the second review's fixes in view | reuse |
