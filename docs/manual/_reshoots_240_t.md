# 2.4.0 fit-in F4 screenshots

No screenshots are taken until 2.4.0 is complete.

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
