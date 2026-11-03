# 2.3.0 manual notes, builder C (items 11, #53, #55)

All three items are S (shared by Variables, Transits and Discovery).

## Item 11: Dark flats box (Input page, Frames group)

**What the user sees**
- A new row **Dark flats** sits between **Dark** and **Flat**. It works like the other boxes: **Browse**, **Master file…**, **✕**, and a file count on the right ("4 files", "4 files + master", "master file"). A typed or pasted path is counted the same way as one picked with Browse (about 400 ms after the last key, or at once on Enter or leaving the box). ✕ clears it and the status line says "Dark flats folder cleared. Calibration will skip it."
- A new hint line under the Output hint: "Dark flats (optional, like Bias): darks taken at the flats' exposure. They are taken off the flats, and with no bias they also stand in for it, so Bias can stay empty. Leave it empty if you have none."
- The box is optional. Leave it empty and nothing changes for users who have bias, darks, flats and lights.
- Like the other frame folders, it starts empty every launch. It is not saved in the series; it is cleared the same way the Bias box is. "Settings/series persistence like the Bias box" therefore means it is not remembered.

**What SHOBS-P does with it**
- Dark flats given: each flat becomes flat − dark flat (exact).
- No bias: the dark flats also stand in for the bias (the zero-exposure pedestal). With darks that match the lights' exposure the lights become light − dark exactly, with no warning. With darks that need scaling, the lights become light − dark flat − (dark − dark flat) × t_light/t_dark, and the Log says so.
- Bias and dark flats both given: the dark flats are used for the flats, and the bias for the lights and darks.
- The old trick (dark flats in the **Bias** box) still works exactly as before. When the Bias box's frames say they are dark flats (IMAGETYP), the Log adds a note that the Dark flats box is now the place for them.
- IMAGETYP accepted: 'Dark Flat', 'DARKFLAT', 'Flat Dark', 'FLATDARK', 'Dark-Flat', 'dark_flat' and the like, in any case. A file name with "darkflat" or "flatdark" also counts when there is no IMAGETYP.
- Dark flats saved inside the **Flat** folder are never stacked into the master flat. With the Dark flats box empty, they are used as the dark flats ("N dark flats found in the Flat folder (IMAGETYP or name): used as the dark flats, and left out of the master flat."). Blink's **Flats** view leaves them out and says so on the status line.
- Saved master: `SHOBS-P masters/SHOBS-P_master_darkflat_bin<b>_<exp>s.fits`, saved in the dark-flat folder and reused like the other masters. **Master file…** on the row takes this file. A master flat built with dark flats is reused only with the same dark flats.
- If the flats are already calibrated (PixInsight _c and similar), the dark flats are not used, and the Log says so.

**Messages**
- Input page, under the frame rows (the same line as the 2.2.19 dark-exposure note):
  - "⚠ No bias and no dark flats: darks can't be scaled correctly without bias or dark flats (110 s darks to 55 s lights scales the camera's offset too). Add bias frames or dark flats, or use darks at the lights' exposure."
  - "⚠ Dark flats 2 s, flats 0.5 s: dark flats should match the flats' exposure. Take dark flats at 0.5 s."
- Calibrate Log, before the masters are built: one summary line, for example
  "Calibration: lights = light − dark (the darks match the lights' exposure, so no bias is needed); flats = flat − dark flat."
  It can be followed by indented notes and ⚠ warnings:
  - the dark-flats-as-pedestal note when the darks are scaled;
  - "Bias and dark flats are both given: …";
  - "Darks can't be scaled correctly without bias or dark flats. …" (no bias and no dark flats, with darks scaled to the lights);
  - "The flats (0.5 s) get the master dark (55 s) scaled ×0.00909, and with no bias that scales the camera's offset too … Put dark flats … in the Dark flats box, or add bias frames." This is Matthew's case with Bias left empty, which is wrong before 2.3.0 and is now warned;
  - dark flats vs flats exposure mismatch.
- "Note: no bias frames. That step is skipped." no longer appears when dark flats are given.
- Masters lines: "Median-combining N dark flat frames", "Saved master dark flat in the dark flat folder's 'SHOBS-P masters' subfolder: …", "Using saved master dark flat …".
- The Transit fit settings file's "Calibration:" line lists the dark flat master and, with no bias, "dark flat used as the bias (no bias frames)".

**Screenshots to retake**
- Input page (the Frames group has a new row and a new hint line): every Input page shot that shows the Frames group.
- Calibrate page Log, if a shot shows the start of a calibration (there is a new "Calibration: lights = …" line).
- The Input page note showing the new ⚠ lines would be a good new shot for the calibration chapter (optional).

## #53: error bars on camera-binned frames

**Root cause.** EGAIN is the camera's gain for its native pixel. NINA writes the driver's value, and it is the same at every binning. ZWO cameras (RAW16, Matthew's ASI2600MM at bin 3) **average** the binned pixels. A stored ADU then holds camera_bin² × EGAIN electrons: 9 × 0.768 = 6.9 at bin 3. SHOBS-P used EGAIN as it was, so it counted 9× too few electrons. The photon error bars came out √9 = 3× too big where photon noise dominates, and about 2× once the sky and read noise are mixed in. That matches Matthew's 11.7 vs 6.4 ppt and reduced χ² 0.41–0.45. The sky cross-check already saw it: "The sky's noise says at least 23 electrons per image unit, over twice what EGAIN 0.768 e-/ADU gives (3.07)". 23/3.07 ≈ 7.5, which is near 9 because the sky estimate is a lower limit. The 2.2.17 docstring had assumed NINA writes the binned pixel's gain; that assumption was wrong.

**Fix.** With XBINNING > 1, the sky's own noise (always measured on the reference frame) decides how the camera binned:
- above cam_bin × the summed value (the geometric middle of the two candidates), the camera averaged, and EGAIN × cam_bin² is used;
- otherwise it summed (CCD charge binning), and EGAIN is used as it is;
- with no sky estimate, EGAIN is used as it is and the Log says the binning mode is not known.

Software "processing bin" was already right (b² in electrons_per_unit), as were read noise (inside the measured sky scatter), the sky variance (counted once) and scintillation (Osborn formula; it is small for 0.2 m at 55 s and is not the cause). Unbinned cameras, John's OSC included, are unchanged to the last digit.

**What the user sees**
- Calibrate/Photometry Log, the "Photon noise:" line gains a sentence. For example: "The camera binned 3x3 and averaged the binned pixels (the sky's noise says so), so each stored ADU holds 9 x EGAIN electrons: about 6.91 electrons per image unit." Or "… summed the binned pixels …: EGAIN is used as it is." Or "… cannot tell whether it summed … or averaged …. If the Transit fit's reduced chi-square is well under 1, the camera averaged."
- On binned ZWO frames the "⚠ The sky's noise says at least … over twice what EGAIN … gives" warning no longer appears.
- The Transit fit settings file / Log "Gain:" line reads "Gain 0.768 e-/ADU (header EGAIN); camera bin 3x3 averaged, so 6.91 e- per stored ADU used (from the sky's noise)".
- Error bars on Matthew-type nights roughly halve (reduced χ² near 1). Nights reduced before 2.3.0 keep their old error bars until they are reduced again.

**Screenshots:** none required, unless the manual shows a "Photon noise:" Log line or a settings file from binned frames.

## #55: on-sky log gets electrons per unit

Nothing changes on screen. The on-sky log file for SHOBS-FLUX (`~/.shobs/onsky/*.json`) now has format_version **2**, with these additions:
- `frames[].stars[name].e_per_unit`: electrons per SHOBS-P image unit at that star. This is the median of SHOBS-P's electrons_per_unit map over the aperture, so it includes EGAIN (×cam_bin² when the camera averaged, from #53), the software binning, the superpixel (8 × EGAIN at TG Bin 2) and the master flat's value at the star.
- `measurement.e_per_unit_median`, `e_per_unit_source` ("EGAIN" / "sky" / "assumed" / null), `e_per_unit_note`, `flat_applied`, `camera_bin_mode`.
- With no gain known (1 e-/ADU assumed for the error bars only) or with pre-calibrated frames, e_per_unit is null and the note says why.

The manual only needs this if a chapter documents the on-sky log fields (ch. on SHOBS-FLUX hand-off): add the new fields and "format version 2". No screenshots.
