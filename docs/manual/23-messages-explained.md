# 23. Messages explained

SHOBS-P talks to you in four places:

- **The status bar** at the bottom of the window: the latest news, one line. After a report save it also holds a
  button for the upload page.
- **The log**: on the Calibrate page, and under the image on the Photometry page (**Show log** / **Hide log**). Every
  status line goes here too, often with more detail. When something looks wrong, read the log first.
- **Message boxes**: a question (Yes / No, sometimes Cancel) when SHOBS-P needs a decision, a warning when it found a
  problem, an error when it cannot go on.
- **Notes and cautions on the Output pages**: lines starting with **⚠** are cautions about the result.

![A warning box after photometry](img/23-edge-caution.png)

Most questions have a default that is the safe choice; just read the whole message before you answer. The messages
below are the ones new users meet most often, grouped by step. "…" stands for the names and numbers filled in.

## Anywhere

| Message | What it means | What to do |
|---|---|---|
| *Another step is still running. Wait for it to finish.* | Only one job runs at a time. | Wait, or press **Cancel** / **Cancel run**. |
| *Wait for the current run to finish (or cancel it) before switching modes.* | Same, for the mode buttons. | Wait or cancel. |
| *Something went wrong: … Details were written to ….shobs_p.log.* | An unexpected error. | Note what you were doing and see [24. Troubleshooting](24-troubleshooting.md). |
| *Could not open a browser: go to …* | The upload or ExoFOP page could not open. | Copy the address into your browser. |
| *The output folder could not be used (…); choose where to save.* | The Output folder is missing or read-only. | Pick another folder on the Input page. |

## Input

| Message | What it means | What to do |
|---|---|---|
| *Could not look up … (…). Type RA/Dec, or let the plate solution fill them, or press Look up again.* | SIMBAD and VSX did not find the name, or there is no internet. | Check the spelling (the Input page may suggest the AAVSO spelling: **Use it**), or type RA/Dec. |
| *The … folder holds N file(s) SHOBS-P cannot read … It reads FITS and XISF* | Camera RAW, TIFF or JPEG files. | Save the frames as FITS or XISF in your capture program. |
| *This Lights folder holds frames pointing at more than one place: … Use only the N frames near the Input target …?* | Two fields in one folder. | **Yes** sets the other field aside (Blink can bring frames back). |
| *The lights' FITS OBJECT says '…', not the Input target …* | The frames may be of another star, or the OBJECT keyword is wrong. | Check the lights. **Yes** clears the old target and looks up the header's name. |
| *These lights point at …, d° from the Input page's target …* | The frames are of another field than the Input target. | **Yes** to switch to the frames' target; **No** to keep yours. |
| *These lights were taken at latitude …, longitude …, N km from your site.* | The frames come from another observatory (shared or downloaded data). | **Yes** uses their site and optics for these frames only; your own come back with your own frames. |
| *… is not a master SHOBS-P built.* | A master from other software was chosen. | Choose the folder of raw frames instead; SHOBS-P builds and reuses its own master. |

## Calibrate

| Message | What it means | What to do |
|---|---|---|
| *Dark folder '…': The darks are 30 s and the lights 120 s, so the master dark is scaled x4 …* | The darks are far from the lights' exposure. | **Yes** calibrates without the dark (fine for most cooled cameras); **Cancel** lets you pick better darks. |
| *The flats were taken at rotator angle X° and the lights at Y°.* | The camera was turned between flats and lights; the flat corrects the wrong pattern. | Use flats taken at the lights' angle if you have them. A meridian flip (180°) does not cause this. |
| *The Flat folder mixes camera angles: …* | Flats from more than one camera angle. | Keep only the flats taken at the lights' angle. |
| *These lights look already calibrated: … Skip bias, dark, and flat for this run? (Recommended)* | The files show signs of earlier calibration. | **Yes**, or they are calibrated twice. |
| *Calibration failed: The masters do not fit these lights: …* | Size or binning differs between masters and lights. | Pick matching frames, or tick **Rebuild masters (ignore saved ones)**. |
| *No light frames found.* | The Lights folder is empty or every frame is rejected. | Check the folder and the Blink page. |
| Log: *Monochrome camera found (no colour mask in the flat): Bayer pattern MONO.* | Information. | If your camera is in fact a color camera, choose its pattern on the Calibrate page. |
| Log: *Meridian flip before frame … (…° to …°)* | Information: where the flip happened. | Nothing; one set of flats covers both sides. |

## Photometry

| Message | What it means | What to do |
|---|---|---|
| *Calibrate first (Calibrate page). Star positions are measured on the calibrated, debayered image.* | You clicked the image before calibrating. | Calibrate. |
| Step strip **1 Not solved**, or *Not solved: no lock (h of n catalog stars matched).* | The plate solve failed. | Press **Solve again** for the full diagnosis. Check the Optics box (focal length, pixel size, binning) and the RA/Dec. You can still click stars and type magnitudes. |
| *Solve needs to know roughly where the frame is: …* | No pointing in the headers and no RA/Dec typed. | Type the target (or field center) on the Input page. |
| *… is a known or suspected variable: … Use it anyway?* | A comp or check you clicked is in VSX or SIMBAD. | Usually **No**, and pick another star. |
| *The aperture (r = 12 px) is much larger than the stars on this frame (FWHM 3.2 px).* | The aperture does not suit the seeing. | **No**, then **Suggest…** to set sizes from the stars. |
| *There is no star under the Target marker at (x, y).* | The marker is on blank sky. | **No**, and click the star again. |
| *These have a name in the Stars panel but no marker on this image …* | A name left from an earlier night or series. | **Yes** clears the names; **Cancel** lets you mark them. |
| *With 3 comps the AAVSO report is an ensemble, and AAVSO requires a check star …* | No check star in Variables mode. | Mark a check star (or continue if you will not report this run). |
| *No transit of … is predicted during these lights …* | Transits mode, but the frames miss the transit. | Check the planet and date; use Variables mode for a variability run. |
| *… hosts …, and a transit of … is predicted during these lights …* | Variables mode on a planet host during a transit. | **Yes** switches to Transits mode. |
| *Binning is 2 but the masters were built at 1.* | Binning changed after calibration. | Calibrate again, or set binning back. |
| Log: *skipped …: could not align this frame (N stars found; clouds? trailing?)* | One frame was unusable. | Nothing, unless many are skipped; then check those frames in Blink. |
| *No frames produced a magnitude. The Calibrate page log says why each frame was skipped.* | Every frame failed. | Read the log. |

## Output, Variables mode

| Message | What it means | What to do |
|---|---|---|
| *Add these N points as the night of … to the M points already in the series?* | A series is open. | **Yes** adds the night. Read any **Careful:** lines first (other comps, filter or equipment). |
| *… frames of … lost light …, but every measured star … faded by the same amount …: transmission loss (dew/haze?), not cloud. Keep these points …?* | Dew or haze, not cloud. | **No** (the default) leaves them out; see [12. Flags and bad data](12-flags-and-bad-data.md). |
| *…: N frames fall in planet b's transit … Leave those frames out …?* | A known planet transits the variable during your frames. | **Yes** (the default) follows the campaign rule. |
| *Before you upload: • AAVSO observer code is empty …* | The report is missing something AAVSO needs. | Fix the listed items and save again. |
| *These nights are already in another AAVSO report: …* | You might upload the same data twice. | Upload only one report per night. |
| ⚠ *No reliable period …* / *No significant period …* | The best peak does not pass the significance tests. | See [13. Look for a period](13-look-for-a-period.md). |

## Output, Transits mode

| Message | What it means | What to do |
|---|---|---|
| *Enter your site's latitude and longitude on the Input page first …* | Needed for BJD times and planning. | Type your site once. |
| *…: N usable points. Run photometry in Transits mode first (or open an exoplanet report).* | Fewer than 20 points. | Run photometry, or check the flags. |
| *The photometry, the comps, or the planet changed since the last fit, so that fit was cleared.* | The fit no longer matches the data. | Press **Fit transit**. |
| *The comps are still being checked (a few seconds). Save the … when that is done …* | The leave-one-comp-out check is running. | Wait a moment and save again. |
| **⚠ Inconclusive …**, **✗ No transit measured …** | See [18. When the fit says "inconclusive" or "none"](18-inconclusive-or-none.md). | |
| *The per-point error bars are about …x larger than the real scatter …* | Usually the camera gain (EGAIN) in the headers. | Check the photometry log's *Photon noise* line. |
| *This fit did not measure a transit: … Save it anyway …?* | Saving a "none" result. | Save for your records only. |

## Discovery mode

| Message | What it means | What to do |
|---|---|---|
| *Field not solved: these lights do not say where they point.* | No pointing in the headers. | Type the field center on the Input page and **Solve again**. |
| *Fewer than 8 stars found in the frame; a field scan needs more.* | Too few stars detected. | Check focus and exposure, or lower **Detection threshold (σ)**. |
| *Field scan failed: only N frames could be measured; a scan needs at least 10* | Too many frames could not be aligned. | Blink out bad frames; check that the frames are of one field. |
| Log: *Field scan: the 800-star limit was reached …* | Only the brightest stars were measured. | Raise **Most stars to measure**. |
| *VSX and SIMBAD could not be reached during the scan. Scan again when online.* | No check against known variables. | Scan again when online. |

---

Previous: [22. Files SHOBS-P writes](22-files-shobs-p-writes.md) · Next: [24. Troubleshooting](24-troubleshooting.md)
