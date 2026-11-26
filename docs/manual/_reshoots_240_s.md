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

## Package A

Input, Calibrate, app frame, #113.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/05-input-page.png | 5 | The gray plan line under the Frames box (*Plan: lights = …; flats = ….*) once Lights and a calibration row are filled; the old orange *Darks 60 s, lights 120 s …* line is gone (it is the end of the plan line now). After Scan folders, a new **Setup for these frames** box above the Site box (right column). Take it on your own lights after **Save as my setup**, so the box reads *Your own setup: site …; focal length …, pixel …, aperture ….* with no button | RESHOOT |
| NEW img/05-setup-for-these-frames.png | 5 | NEW: the **Setup for these frames** box on frames from another site (Matthew's Moeller 1 m lights, or MicroObservatory frames): *site …; focal length …, pixel …. From the lights' headers (…), N km from your site. In use for these frames only: nothing is saved as yours.* with **Save as my setup** and **Use my own setup** | NEW SHOT |
| NEW img/05-plan-line-warning.png | 5 | NEW (optional): the plan line with a ⚠ line under it (dark flats 2 s against 0.5 s flats) | NEW SHOT |
| img/03-window-parts.png, img/03-busy-bar.png | 3 | Only if the Input page shows in them: the plan line and the **Setup for these frames** box as above; in a shot taken while a job runs, **Scan folders** and **Try it with sample data…** are greyed | RESHOOT if the Input page shows |
| img/07-calibrate-done.png | 7 | **Cancel** is greyed (nothing is running once the calibration is done) | RESHOOT |
| NEW img/07-color-mask-question.png | 7 | NEW: the question *The flat shows a color mask …, but the Bayer pattern is MONO.* with **Color camera (RGGB)**, **It is monochrome**, **Cancel** (color flats, Bayer pattern set to MONO by hand, press Build masters and calibrate) | NEW SHOT |
| Photometry page shots with the progress row (idle) | 8, 9 | **Cancel run** is greyed while nothing runs (Package B lists the page's other changes) | RESHOOT with Package B's |
| img/17-exoclock-buttons.png | 17 | Buttons unchanged; reshoot only if the status line is in the shot: it now reads *✔ Saved ExoClock_…_SHOBS-P: upload the .txt inside (what to pick on the form is in ExoClock_info.txt).* | RESHOOT if the status line shows |
| NEW img/17-exoclock-folder.png | 17, 22 | NEW (optional): the Output folder in File Explorer with the `ExoClock_<planet>_<date>_<obscode>_SHOBS-P` folder beside the ExoFOP one, opened to show the upload `.txt` and `ExoClock_info.txt` | NEW SHOT |

No shot in the manual shows the four questions that went (*Is that your own site?*, *Use the site and optics in these
frames?*, *Use that telescope's setup for these frames?*, *Use the header values?*), so nothing is removed.

## Package O

Fit-in F3 (#117): single-point outliers in the transit fit. After a fit, a single point more than 5σ (robust) from
the fit gets a plain note, a red ring on the plot (its bin drawn without it) and the button **Leave it out and
refit** in the Points row; after pressing it the point is a red × and **Put it back** shows.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/17-point-outlier.png | 17 | NEW (not in the chapter yet: add it under *One point far from the fit* when shot). A night with one frame far from the fit: the red ring (or the red arrow at the panel's edge), the legend entry *single-point outlier: in the fit, not in the bins*, the note in the Notes panel, and **Leave it out and refit** in the Points row | NEW SHOT |
| img/17-point-left-out.png | 17 | NEW (optional, same place). The same night after **Leave it out and refit**: the red × labeled *left out as a single-point outlier (not fitted)*, the note, and **Put it back** | NEW SHOT (optional) |
| img/17-transit-fit-page.png | 17 | no change unless the night shown has a point more than 5σ from the fit (then the ring, the note and the button show) | CHECK |
| img/17-trim.png | 17 | as above | CHECK |
| img/19-exotic-side-by-side.png | 19 | as above (an opened report is checked the same way) | CHECK |
| img/18-inconclusive-verdict.png | 18 | as above | CHECK |

## After the F3 review (fixes M1, M2, S1–S4)

No new shots. Words only, in the **Setup for these frames** box:

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/05-input-page.png, NEW img/05-setup-for-these-frames.png | 5 | The aperture in the box's text may be followed by its source in brackets, for example *aperture 356 mm (typed by hand)*. Take the shots as planned; nothing else differs unless a site was typed over the header's. | CHECK |
