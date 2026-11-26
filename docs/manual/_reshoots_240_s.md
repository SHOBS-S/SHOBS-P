# 2.4.0 fit-in F3 screenshots

No screenshots are taken until 2.4.0 is complete. One heading per builder package.

## Package B

Photometry page, the run, Discovery scan (fit-in F3, builder B). Changes that show in pictures: each comp / check row
of the Stars panel shows ONE verdict (one ⚠ and the worst reason, then *also: …*, or a green *✓ good comp: …*); a
name without a marker shows a plain grey hint, not an orange ⚠; no warning box comes up after photometry (the
Findings window opens by itself instead, and each "left out of N frames" line is a ⚠ with what to do); the step strip
says *Not ready: …* while **Run photometry** is greyed; the Field scan page has one **Back** (on the Results row) and
its header can start with *Limits of this scan: …*; two questions have new buttons. None of these shots has been
taken (the builder cannot take them). Do not reuse an old image for a page that changed: reshoot it.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/08-stars-panel.png | 08 | every comp / check row: one verdict line (*✓ good comp: B−V …, +0.3 mag from the target, not a known or suspected variable (…)   V from …* in green; a problem star: one ⚠, then *also: …*) | RESHOOT |
| img/08-photometry-solved.jpg | 08 | Stars panel rows in view read as above; the step strip is unchanged when Run is live | RESHOOT if the Stars panel text is readable in the shot |
| img/09-marked-stars.jpg | 09 | Stars panel rows in view read as above | RESHOOT if the Stars panel text is readable in the shot |
| img/23-edge-caution.png | 23 (intro) | was the warning box after photometry, which no longer exists. New shot: the **Findings** window as it opens by itself after a run in which a comp or the check left the frame (one line per star, e.g. *⚠ C3 left out of 14 frames (off the frame edge; frames 27–40); those frames use the other comps. Leave it out with Use comps… (Output page).*). The caption in chapter 23 already reads "The Findings list after a photometry run" | RESHOOT (new content, same file name) |
| img/03-findings.png | 03 | the per-star lines now read *⚠ … left out of N frames (…) … Leave it out with Use comps… (Output page).*; a "comes within … px of the frame edge … Consider leaving it out" entry no longer exists (the SZ Lyn shot shows one) | RESHOOT |
| img/09-output-page.png | 09 | only if the shot shows the warning box over the Output page after a run: that box is gone | CHECK |
| img/20-field-scan-output.png | 20 | one **Back** only (at the right of the Results row; the second one under it is gone); when the 800-star limit was reached the header starts *Limits of this scan: brightest 800 measured (…)* | RESHOOT |
| img/20-discovery-photometry.jpg | 20 | step strip: unchanged when Scan field is live (*Not ready: …* only while it is greyed) | reuse |
| img/12-transmission-keep.png | 12 | the Keep question itself is unchanged (only its Log line changed) | reuse |
| OPTIONAL NEW img/08-step-not-ready.png | 08 | the step strip saying *Not ready: The masters were built at bin 1: Calibrate again (or set Binning back to 1).* with **Run photometry** greyed | NEW SHOT (optional; no image link in the chapter yet) |
| OPTIONAL NEW img/08-no-transit-question.png | 08 | the question *No transit of … is predicted during these lights (…). Check the planet and the frames' date.* with **Switch to Variables mode** / **Run in Transits mode anyway** / **Cancel** | NEW SHOT (optional; no image link yet) |
| OPTIONAL NEW img/08-names-without-markers.png | 08 | the question *These have a name but no marker on this image …* with **Run without them (names cleared)** / **Stop to mark them** | NEW SHOT (optional; no image link yet) |
| all others | | nothing of builder B's F3 in view | reuse |

## Package C

Output pages, Transit fit, Multi-night, saving (areas J and L, F2-c, the package's merges).

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/13-period.png | 13 | Period box: a first row **Period  Reliable: … / ⚠ Doubtful: … / None: …**; the **Best/Peak** row has no "(not significant)" tail; **Range**, **Check** and **Airmass** rows have no ⚠; the **Bins** row reads "every point used (…-min bins too coarse for … d)" | RESHOOT |
| img/15-results-row-scope.png | 15 | NEW: the Results row with the **Saving:** box first (a night highlighted in a series of two or more nights, so the box reads **Saving: night …**) | NEW SHOT |
| img/09-output-page.png | 9 | Results row: the **Saving: whole series (1 night)** box before **Save series**; with the observer code empty, "WebObs needs an observer code (Input page)" beside **Save AAVSO report** (take it with the code filled) | RESHOOT |
| img/10-night-zoom.png | 9, 10 | a night chosen: the box reads **Saving: night …** | RESHOOT |
| img/10-series-output.png | 10 | Results row: **Saving: whole series (N nights)** | RESHOOT |
| img/15-no-auid.png | 15 | Results row has the Saving box | RESHOOT |
| img/15-webobs-offer.png | 15 | Results row has the Saving box; the status line after a save says "It holds the …" | RESHOOT |
| img/12-flagged-points.png | 12 | if the Results row or the Period box is in view: as above | CHECK, reshoot if they show |
| img/05-input-page.png (the Input page's Target and AAVSO report groups) | 5 | the line under Star ID is grey and has no **Use it** button ("AAVSO spells this “…”: Look up switches to it."); with a light curve and an empty **Observer code** or **Star ID**, that box is red (take the usual shot with both filled: no change then) | CHECK, reshoot if the spelling line shows |
| img/17-transit-fit-page.png | 17 | fit notes: one **Shape** and one **Coverage** note where there were several; no "Flip step not needed … no step term" note; "Comps limit the noise …" is a plain note on a detected transit | CHECK, reshoot if the notes changed for the shot's night |
| img/17-flip-step.png | 17 | notes on a flip night: no ⚠ "Step–depth correlation … trade off" or "Out-of-transit baseline on each side" note when the transit is detected (the verdict says it) | CHECK |
| img/18-inconclusive-verdict.png | 18 | notes: **Coverage** / **Shape** wording | RESHOOT |
| img/17-multinight-window.png | 17 | with a no-airmass night: the line under the fit options is plain ("N nights in use (M fitted against time only: see the note under the table)."); Summary: one ⚠ note for all such nights; the scatter ⚠ names its outlier nights | CHECK, reshoot if such a night is in the shot |

Not changed: img/15-notes-question.png, img/11-comp-health.png, img/11-use-comps.png, img/14-extremum.png,
img/17-multinight-corner.png. Gone: the "Loaded N points" pop-up, the "… is highlighted. Yes / No / Cancel" save
question, the "Use AAVSO's spelling in the report?" question and the "Saved <path>" box without an Output folder (none
of them had a screenshot).
