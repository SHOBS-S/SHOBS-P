# SHOBS-P User's Manual (version 2.2.17) — outline

> Draft outline, 17 Oct 2026. Chapters are written one at a time and checked against the program.
> Each chapter is its own file in this folder; this page is the table of contents.

## Part 1 — Getting to know SHOBS-P

1. **Welcome** — what SHOBS-P is, who it is for, what it does not do.
2. **Installing and starting** — Python, the release zip, shortcuts, first launch, `Run self-test.bat`.
3. **A tour of the window**
   - The three modes and their colours: Variables (navy), Transits (green), Discovery (plum).
   - The five steps: Input → Blink → Calibrate → Photometry → Output.
   - Page titles, the status bar, warnings and cautions, the Credits page.
   - Where settings are kept, and what is never saved (for example your site, unless you enter it).
4. **Before your first night**
   - Your site and optics (Input page Site and Optics boxes).
   - Colour (OSC) or monochrome camera; filters, including Sloan g′ r′ i′.
   - Calibration frames: bias, darks, flats; matching flats to the rotator angle; saved masters.
   - Folder layout that works well.

## Part 2 — Common steps (all modes)

5. **Input** — choosing folders, target lookup, the series line (Load / Close series), what the program checks in your headers (pointing, mislabelled frames, mixed binning).
6. **Blink** — playing the frames, stretch, rejecting and keeping frames, the rejected list, frame numbers.
7. **Calibrate** — what happens, masters reuse, binning, common warnings.
8. **Photometry page basics** — auto-solve, the image view (zoom, pop-out, compass, View box, stretch), the step strip, clicking stars and how names and magnitudes fill in, apertures and Suggest apertures, running photometry and the questions it may ask.

## Part 3 — Variables mode

9. **Reduce a variable-star night** — target, comparison stars (AAVSO sequence first), check star, run, read the light curve.
10. **Build a multi-night series** — add a night, the "same star" and equipment questions, night notes, saving, the backup copy.
11. **Check your comparison stars** — Comp health, Use comps, spares, zero-point warnings.
12. **Flags and bad data** — saturation, cloud, dew/haze (the Keep question), planet transits in a variable's frames, pier side.
13. **Look for a period** — the periodogram, what "significant" means here, aliases, the airmass check.
14. **Time a maximum or minimum** — Time of max/min, O − C.
15. **Submit to AAVSO** — the Extended report, AUIDs, single-night vs series reports, uploading with WebObs.

## Part 4 — Transits mode

16. **Plan a transit night** — planet lookup by any name, Tonight's transits, baseline, meridian flips.
17. **Fit a transit and submit it** — the Transit fit page, the verdict, O − C, rms lines, leave-one-comp-out, Trim, Comp health and Use comps on this page.
18. **When the fit says "inconclusive" or "none"** — gaps, one-sided baselines, cloud-flagged frames, the depth-unreliable warning.
19. **Reports** — AAVSO exoplanet report, ExoFOP package, opening an EXOTIC report for a side-by-side fit.

## Part 5 — Discovery mode

20. **Scan a field** — solving, known variables, Faintest magnitude, the threshold, the scan table and candidate kinds.
21. **Follow a candidate** — the watch list, adding nights, exporting light curves.

## Part 6 — Reference

22. **Files SHOBS-P writes** — names, folders, the "Created" line, series files.
23. **Messages explained** — common warnings and cautions, what they mean, what to do.
24. **Troubleshooting** — the self-test, the log file, typical problems.
25. **Glossary** — comp, check, ensemble, AUID, BJD_TDB, O − C, scintillation, superpixel, and more.
26. **Credits and how to cite SHOBS-P** — DOI, CITATION.cff.

---
*Screenshots: to be decided (see open questions in the handoff).*
