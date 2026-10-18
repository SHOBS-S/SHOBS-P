# SHOBS-P User's Manual (version 2.2.18)

> Draft, October 2026. Written for SHOBS-P 2.2.18 and checked against the program.
> Each chapter is its own file in this folder; this page is the table of contents.

## Part 1 — Getting to know SHOBS-P

1. **[Welcome](01-welcome.md)** — what SHOBS-P is, who it is for, what it does not do.
2. **[Installing and starting](02-installing-and-starting.md)** — Python, the release zip, shortcuts, first launch, `Run self-test.bat`.
3. **[A tour of the window](03-tour-of-the-window.md)**
   - The three modes and their colors: Variables (navy), Transits (green), Discovery (plum).
   - The five steps: Input → Blink → Calibrate → Photometry → Output.
   - Page titles, the status bar, warnings and cautions, the Credits page.
   - Where settings are kept, and what is never saved (for example your site, unless you enter it).
4. **[Before your first night](04-before-your-first-night.md)**
   - Your site and optics (Input page Site and Optics boxes), including the mount (German equatorial or alt-az).
   - Color (OSC) or monochrome camera, and color images that are already debayered; filters, including Sloan g′ r′ i′.
   - Calibration frames: bias, darks, flats; matching flats to the rotator angle (also on alt-az mounts and derotators); saved masters.
   - Folder layout that works well.

## Part 2 — Common steps (all modes)

5. **[Input](05-input.md)** — choosing folders, target lookup, the series line (Load / Close series), what the program checks in your headers (pointing, mislabeled frames, mixed binning).
6. **[Blink](06-blink.md)** — playing the frames, stretch, rejecting and keeping frames, the rejected list, frame numbers.
7. **[Calibrate](07-calibrate.md)** — what happens, masters reuse, binning, common warnings.
8. **[Photometry page basics](08-photometry-basics.md)** — auto-solve, the image view (zoom, pop-out, compass, View box, stretch), the step strip, clicking stars and how names and magnitudes fill in, apertures and Suggest apertures, running photometry and the questions it may ask.

## Part 3 — Variables mode

9. **[Reduce a variable-star night](09-reduce-a-variable-night.md)** — target, comparison stars (AAVSO sequence first), check star, run, read the light curve.
10. **[Build a multi-night series](10-build-a-multi-night-series.md)** — add a night, the "same star" and equipment questions, night notes, saving, the backup copy.
11. **[Check your comparison stars](11-check-your-comparison-stars.md)** — Comp health, Use comps, correcting a catalog magnitude, spares, zero-point warnings.
12. **[Flags and bad data](12-flags-and-bad-data.md)** — saturation, cloud, dew/haze (the Keep question), planet transits in a variable's frames, pier side.
13. **[Look for a period](13-look-for-a-period.md)** — the periodogram, what "significant" means here, aliases, the airmass check.
14. **[Time a maximum or minimum](14-time-a-maximum-or-minimum.md)** — Time of max/min, O − C.
15. **[Submit to AAVSO](15-submit-to-aavso.md)** — the Extended report, AUIDs, single-night vs series reports, uploading with WebObs, the duplicate warning and the Sent column.

## Part 4 — Transits mode

16. **[Plan a transit night](16-plan-a-transit-night.md)** — planet lookup by any name, Tonight's transits, baseline, meridian flips and the zenith keyhole.
17. **[Fit a transit and submit it](17-fit-a-transit-and-submit-it.md)** — the Transit fit page, the verdict, O − C, rms lines, leave-one-comp-out, Trim, Comp health and Use comps on this page.
18. **[When the fit says "inconclusive" or "none"](18-inconclusive-or-none.md)** — gaps, one-sided baselines, cloud-flagged frames, the depth-unreliable warning.
19. **[Reports](19-transit-reports.md)** — AAVSO exoplanet report, ExoFOP package, the fit CSV, opening an EXOTIC report for a side-by-side fit.

## Part 5 — Discovery mode

20. **[Scan a field](20-scan-a-field.md)** — solving, known variables, Faintest magnitude, the threshold, the scan table and candidate kinds.
21. **[Follow a candidate](21-follow-a-candidate.md)** — the watch list (remembered between sessions), adding nights, exporting light curves.

## Part 6 — Reference

22. **[Files SHOBS-P writes](22-files-shobs-p-writes.md)** — names, folders, the "Created" line, series files.
23. **[Messages explained](23-messages-explained.md)** — common warnings and cautions, what they mean, what to do.
24. **[Troubleshooting](24-troubleshooting.md)** — the self-test, the log file, typical problems.
25. **[Glossary](25-glossary.md)** — comp, check, ensemble, AUID, BJD_TDB, O − C, scintillation, superpixel, and more.
26. **[Credits and how to cite SHOBS-P](26-credits-and-citing.md)** — the Copy button, DOI, CITATION.cff.

---
*Screenshots are from SHOBS-P on Windows; some are still placeholders.*
