# 2.4.0 step 4 screenshots

No screenshots are taken until 2.4.0 is complete. Each builder appends a section for its issues.

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
