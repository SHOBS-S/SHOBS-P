# 2.4.0 step 4 screenshots

No screenshots are taken until 2.4.0 is complete. Each builder appends a section for its issues.

## #110, #111 (s4-quick)

#110: the AAVSO exoplanet report writes one DETREND_n column per name in #DETREND_PARAMETERS (a text file; nothing on
screen changes). #111: lights from another camera than the last ones empty the Bias, Flat and Dark flats boxes left
over from that camera (Dark unless it fits), reset the aperture and sky ring (set from the new frames' star sizes at
Calibrate) and set raw sat ADU / linear ADU for the new camera; three plain lines appear under the Frames boxes on the
Input page while those lights are in the Lights box. The aperture caution leaves the Findings list when the boxes are
fixed. The New reduction tooltip has one more sentence.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/05-input-page.png | 05 | nothing when the shot's lights are the session's first or the same camera (no note under the Frames boxes) | CHECK, reuse if no note shows |
| (new) img/05-another-camera.png | 05 | optional new shot for "Lights from another camera": the Frames box after New reduction and a MicroObservatory Lights folder, with the three plain lines under it and Bias / Flat emptied | NEW (optional; ask John) |
| img/07-calibrate*.png | 07 | nothing (text only: the boxes are emptied before Calibrate) | reuse |
| img/08-*.png showing the aperture row | 08 | nothing in the row itself; values differ only after a camera change | reuse |
| any shot of the Findings list with *Aperture r = … (kept from before)* | 08 / 23 | the finding now leaves when the boxes are fixed; its words are unchanged while kept | CHECK, reuse if none shows it |
| img/19-*.png, img/17-*.png | 17 / 19 | nothing (#110 is the report file's text) | reuse |
