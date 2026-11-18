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
