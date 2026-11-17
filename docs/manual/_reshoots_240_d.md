# 2.4.0 step 3 screenshots

No screenshots are taken until 2.4.0 is complete. Each builder appends a section for its issues.

## #105 + #82 (the wrong-planet family)

The pre-run transit check and everything after a run use the Input page's planet; New reduction closes a report open
on the Transit fit page; the TOI / ExoClock lines under the Results row hide for a report of another planet; after
No to a planet of another star the previous planet comes back (or the value boxes stay empty).

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/19-exotic-side-by-side.png | 19 | a report of another planet open: the *TOI-… / ExoClock: …* lines under the Results row are gone (they showed the photometry night's TOI-2046 b beside a TrES-3 b report) | RESHOOT |
| img/16-planet-box.png | 16 | only if the shot shows the line under the Planet box after **No** to a planet of another star (new words) | CHECK, reshoot if so |
| img/05-input-page.png | 05 | nothing of this in view (the Planet box line is the lookup's) | reuse |

## #68 (the check star is never a comp)

A new box and Stars-panel line when a comp and the check star are the same star (same name, or within about 2″), and
Run photometry refuses with the same words. No existing shot shows the case (it needs a deliberate mistake).

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/08-stars-panel.png | 08 | nothing (no clash in the shot) | reuse |
| all others | | nothing of #68 in view | reuse |

## #93 (the fit's exact inputs in the result files)

Text files only: a *Fit inputs (full precision)* block at the end of the ExoFOP `_fit.txt`, *Fit input …* rows in the
transit report's `_settings.txt` (a fit of an opened report now gets one too) and `# Fit input …` lines in the fit
CSV header. Nothing on screen changes; no fit number changes.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| all | | nothing of #93 in view (no shot shows these files) | reuse |

## #65 (Time of max/min starts from the saved times)

The window opens with Period and Epoch from the star's saved times (label *from the times CSV: epoch = first saved
maximum, period last used*), and the saved-times list under the result now has a heading line (*O−C against P …,
E … (HJD), as in the diagram:*) and gives each time's O − C against the boxes' elements, with *(saved … against …)*
where the stored value differs.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/14-extremum.png | 14 | the label beside the elements, the list's new heading line and its O − C values (GSC 03308-00462's 9 Nov maximum now reads O−C +0.0 with *(saved +9.4 against …)*, matching the diagram); the caption was rewritten. Take it on opening the window, without pressing **Elements from saved times** | RESHOOT |

## #105 (the NEB check never mixes two stars)

A new message box when the NEB check is opened with a report of another star shown. No shot shows the NEB window or
this box.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| all | | nothing of this in view | reuse |

None of these shots has been taken (no screenshots until 2.4.0 is complete). Each builder appends its own section.

## #78 Suggested comps follow the night's drift

Changes that show in pictures: the green rings **S1–S4** never sit on a star that the night's drift takes nearer the
edge than 3 × the sky-out radius (on a field with such a star there are fewer rings, or another star); the Log line
*Suggested comps (…)* ends with *All stay at least … px from the frame edge all night (the field drifts up to … px;
checked on … of the … lights).*; a new plain Log note *Only 3 comps suggested: …*; the S lines under the Stars panel
no longer say *only … px from the edge*; a comp or check you click that drifts toward or off the edge later in the
night gets a new Stars panel line *⚠ drifts toward the frame edge later in the night: …* / *⚠ drifts off the frame
later in the night: …*.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/08-photometry-solved.jpg | 08 | green rings S1–S4 (an edge-drifting suggestion is gone); the Log line with the suggestions, if in view, ends with the drift sentence | RESHOOT (same night; check the rings and the Log line) |
| img/08-stars-panel.png | 08 | the S lines: no *only … px from the edge* words; a loaded series gives the drift from its own frames | CHECK, reshoot if an S line or ring changed |
| img/09-marked-stars.jpg | 09 | green rings S1, S2 … around the marked stars | CHECK, reshoot if a ring moved or went |
| NEW (optional) img/08-drift-edge-note.png | 08, 23 | Stars panel line of a comp clicked near the edge that drifts off it later: *⚠ drifts off the frame later in the night: it is off the frame edge on frames …* (WASP-50 MOBS night of 24 Oct 2026: click a star about 45 px from the right-hand edge) | NEW SHOT if John wants one; the text is in ch 8 without it |
| all others | | nothing of #78 in view | reuse |
