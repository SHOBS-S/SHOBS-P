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

## #104 Gain from the night's sky

No shot in the manual shows a no-EGAIN night's *Photon noise* line or gain note: the transit shots are John's
ASI2600MC nights (EGAIN), whose fit-notes gain line is unchanged (*"Gain 0.243 e-/ADU (header EGAIN)."*). The sky check
of EGAIN goes only into the settings file and the Log (a ⚠ only when it differs by more than 1.5x). The Credits page
gets a new METHODS entry, below the part in the shot.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/17-transit-fit-page.png | 17 | nothing (EGAIN night: gain note unchanged) | CHECK when retaken for other reasons: no new ⚠ from the gain check on that night |
| img/17-flip-step.png, img/17-trim.png, img/17-refit-needed.png | 17 | as above | CHECK as above |
| img/26-credits-page.png | 26 | new METHODS entry "Gain from the night's sky" (below the visible part: SOFTWARE and the first catalogs show) | reuse |
| any shot of the Photometry log with a *Photon noise:* line from a no-EGAIN (MicroObservatory) night | 08 | the line now reads *…so the gain is measured from the night's sky: …* | RESHOOT if one exists (none found) |

## #104 Fable review fixes (S1, N1, N5, N6, nits) and the whiteness guard

Log and settings-file words only: a new *Measuring the gain from the night's sky (… frame pairs)…* line before the
*Photon noise* line (every run that measures the gain, EGAIN nights included), *… pairs dropped: the sky had structure
…* when the whiteness guard drops pairs, *+/- …* for a photon-transfer floor, a plain note when the measurement is
under the one-frame lower limit. No dialog, panel or button changes.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| any shot of the Photometry log just after a run starts | 08 | a *Measuring the gain from the night's sky (… frame pairs)…* line now comes before the *Photon noise* line | RESHOOT if one shows the log at that point (none found) |
| img/17-transit-fit-page.png and the other ch 17 transit shots | 17 | nothing (EGAIN night: the gain note is unchanged) | reuse |
| all others | | nothing of these fixes in view | reuse |

## #104 follow-up (cloud guard at 1.05; stacks use the frames' gain)

Log and settings-file words only (a stacked run's *Photon noise* line ends *…, measured on the single frames that went
into the stacks …*). Nothing on screen besides the Log changes.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| any shot of the Photometry log after a run on stacks | 08 | the *Photon noise* line's ending | RESHOOT if one exists (none found) |
| all others | | nothing of this in view | reuse |

## Fable review fixes (#78 N2–N4, #68 N7, #93, #65, #105 nits)

While the night's drift is read after Calibrate, the busy bar in the lower left says *Checking the night's drift…
frame 3 of 8* and green rings already shown stay; a drift check that finds nothing gets a plain Log note; the loaded
series' positions are used only when they cover nearly all lights at the page's binning. A comp and the check star
with the same name but markers clearly apart get *… have the same name …* words. The fit inputs' lookup row names
values typed over after the lookup. The NEB check of a same-star report with no RA/Dec says it needs the RA/Dec.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/08-photometry-solved.jpg | 08 | only if shot within the few seconds after Calibrate: the status strip's busy bar would show *Checking the night's drift…* | CHECK when retaken for #78: shoot after the bar has gone |
| any shot of the status strip right after Calibrate | 07, 08 | the busy bar may show the drift words | CHECK, reshoot if the words are in view |
| all others | | nothing of these fixes in view (text-file rows, boxes that need a deliberate mistake) | reuse |

## #106 (the boxes' rounding no longer drops a looked-up error)

The a/R* and b priors now use the lookup's published uncertainty when the catalog value has more decimals than the
Input page box shows. Only fits of such planets can move (priors only); the boxes and pages look the same.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/17-transit-fit-page.png, img/17-flip-step.png, img/17-trim.png, img/17-refit-needed.png | 17 | only if that planet's catalog a/R\*, b or Rp/R\* has more decimals than the box: the fit's numbers and the notes' *Priors* line may move | CHECK when retaken: compare the Priors line with step 2's; RESHOOT if any number changed |
| all others | | nothing of #106 in view | reuse |

## #107 (MicroObservatory pixel from the binned header scale)

The MicroObservatory setup question now says *pixel 6.8 µm (the camera's pixel; the camera bins 2x2, so about
5.0"/px)* on current headers, and the Pixel size box gets 6.8 µm (was 13.6 µm, which made the solve use 10"/px).

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| any shot of the MicroObservatory setup question | 05 | new Optics line | RESHOOT if one exists |
| any shot of the Input page Optics box with MicroObservatory frames scanned | 05 | Pixel size 6.8 (was 13.6) | RESHOOT if one exists |
| img/08-photometry-solved.jpg (if a MOBS night) | 08 | nothing (it was solved with 6.8 µm typed by hand) | reuse |

## MicroObservatory elevation from the header (HEIGHT, #108)

The MicroObservatory setup question now reads the header's elevation (`HEIGHT`, 1268 m) instead of the approximate
2340 m, so its site line says *elevation 1268 m (from the FITS header)*.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| any shot of the MicroObservatory setup question | 05 | elevation 1268 m from the header, no "elevation approximate" | RESHOOT if one exists |
| all others | | nothing of this in view | reuse |

## Fable re-review fixes (#104, #107, #108, #78, #68: N1-N4 and nits)

Text only: the whiteness guard's words (faint stars that moved, resampled frames), the stacks' read-noise wording
(*… units per stack of 4 frames (from the darks, added up over the 4 frames) …*), *… or more (no upper bound)*
instead of *inf*, a Log line when a pre-2.4.0 series' Pixel size 13.6 µm is changed to 6.8 µm over current
MicroObservatory frames, and new or reworded chapter 23 rows (same-name variants, the lower-limit note). No number
changes; the gain of the WASP-50 night is the same (39.27 e- per image unit, 24 pairs, none dropped).

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| any shot of the Log or the Photon noise line of a stacked run on darks | 08 | the read-noise wording | RESHOOT if the line is in view |
| any shot of the Input page Optics box after opening a pre-2.4.0 MicroObservatory series | 05 | Pixel size 6.8 (was 13.6) and the new Log line | RESHOOT if one exists |
| any shot of the Findings panel with a same-name or same-star check message | 23 | the "what to do" line now matches the before-solve and combined variants | CHECK when retaken |
| all others | | nothing of these fixes in view | reuse |

## Step 3 regression text fixes (18 Nov)

Text files and messages only: the *Fit input …* rows (15 significant figures; the catalog row says its ephemeris
source once; "under 1 min now"), the settings file's *Gain check from the night's sky* row (e-/ADU first, then
"x the header EGAIN"), author names in ASCII files (Yalcinkaya), a plain *Calibration failed: The master bias is … px
but the flats are … px …* message, and the Calibrate page's preview line reading **No preview yet.** while a
calibration runs or after one fails.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| (none) | 7, 16, 19, 23 | Text only; no screenshot shows these lines | No reshoot |
