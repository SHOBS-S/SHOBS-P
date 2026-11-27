# 2.4.0 cleanup package screenshots (after the F4 regression, 27 Nov 2026)

No screenshots are taken until 2.4.0 is complete. This list adds to `_reshoots_240_a.md` … `_reshoots_240_t.md`.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| any Stars panel shot with an edge line on a comp | 8 | the one edge rule (#119): *⚠ only … px from the frame edge: its sky ring (out to … px) runs past the edge, so it would be left out of the whole night …*; a comp near (not past) the edge is a plain line *… px from the frame edge on this frame (its sky ring needs … px) …*; a drifting comp reads *… runs past the edge on frames …, so it would be left out of the whole night* | RESHOOT if the line shows |
| img/08-photometry-page.png (and any shot with the line under "Measured with") | 8 | a comp out by the edge rule reads *C3 left out of the whole night: its sky ring runs past the frame edge in 14 of 40 frames; frames …*; on an edge night more frames go out than before | RESHOOT if the line shows |
| img/17-transit-page.png (or any Transit page shot taken during a fit) | 17 | while a fit runs the plot area and notes say *Fitting the transit… the busy bar at the lower left names each step.* | RESHOOT only if the shot is taken during a fit |
| any Transit Log shot with *Detrend Auto (BIC): …* on a flip night | 17 | the line ends *flip step kept (it lowers the BIC by …)* / *flip step not needed (…)* (was *flip step kept with …*) | RESHOOT if the line shows |
| img/20-known-variables.png (Known variables measured… window), if the manual has one | 20 | one row per star (*GSC 03308-00462 / ATO J041.8228+50.7518*, Source *VSX + SIMBAD*); *not measured: …* gives the reason | RESHOOT |
| img/20-field-scan-output.png | 20 | the text above the table: *Each star's light curve was lined up across 1 re-center first.* on a re-center night (was the raw list *[(63, 'recenter')]*) | RESHOOT if the shot's scan has a flip or re-center |
| (no shot) | 9, 12 | a re-center step question *This night's re-center step is … mag ⚠ …* (a box) | none (or NEW SHOT, optional) |
| (no shot) | 12, 20, 23 | Log / status line *The pointing moved at frames 95–97 (64″) but held for fewer than 5 frames …* | none |
| (no shot) | 10, 23 | Log *… is in the older star format: it is converted to the new one when the series is next saved (…)* | none |
| (no shot) | 21 | Log *Watch stars: placed after Calibrate on this series' lights …* | none |
| all others | | nothing of the cleanup package in view | reuse |
