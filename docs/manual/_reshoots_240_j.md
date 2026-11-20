# 2.4.0 step 6c-2 screenshots

No screenshots are taken until 2.4.0 is complete. Step 6c-2: robustness and comp-check refits decide their own flip
step; the comp survey leaves out a comp that jumps more than ±10 ppt at the flip on its own; a report fit's ExoClock
file takes the report star's own RA/Dec; the frames' legend says *each frame* when nothing was detrended.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| any img/08-*.png or Log shot with *Left out by the comp survey* on a flip night | 08 | new reason *jumps … ppt at the meridian flip on its own …*; on WASP-49 b 19 Nov the suggested set changes (BQG-573 and a star near the target's column) | RESHOOT (replaces the 6c shot) |
| img/17-*.png transit plots with the legend | 17 | *each frame* / *each frame, flip step taken out* where nothing was detrended (was *each frame, detrended*) | RESHOOT every fit shot whose fit had no detrend term |
| any img/17-*.png with the Robustness note on a flip night | 17 | the note's *Flip step: … of … refits …* sentence | RESHOOT if shot |
| any img/17-*.png of an opened report's ExoClock save | 17 | the Log line *ExoClock file: …'s RA/Dec from SIMBAD/VSX …* | CHECK |
