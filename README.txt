Shiloh Hill Observatory – Photometry (SHOBS-P)  2.2.18
======================================================

Windows desktop app for photometry with color (OSC) and monochrome cameras: variable-star light curves and AAVSO
reports, exoplanet transit fits and AAVSO exoplanet reports, and a field scan that finds stars that vary.

Quick start
1. Install Python 3.11 or newer from python.org ("Add python.exe to PATH" ticked). Double-click "Create shortcuts.bat"
   in this folder, then "Run self-test.bat" (it should end with RESULT: ALL PASSED).
2. Try it first: on the Input page press "Try it with sample data…" and pick an empty folder; every box is filled and
   the stars are marked. Then Blink, Calibrate, Photometry, Output.
3. Your own frames: Input (folders, Scan folders, site and optics once, the target's name then Enter), Blink (reject bad
   frames), Calibrate (camera pattern or MONO; Build masters and calibrate), Photometry (the image solves itself;
   click the target, comps and check star, Run photometry), Output (light curve and AAVSO report; transit fit; or field scan).
4. Problems and ideas: GitHub Issues (github.com/SHOBS-S/SHOBS-P/issues). Attach selftest_log.txt and .shobs_p.log
   (in your user folder) and a screenshot.

Process flow
1. Input: folders of bias, dark, flat, and light FITS, and an optional Output folder; the target (type the Star ID
   and press Enter: SIMBAD, then VSX, fill RA/Dec); AAVSO observer code and chart; site and optics. (Comparison and check stars are chosen on the
   Photometry page.)
2. Blink: step through raw lights (or flats) and reject trails, clouds, and bad guiding. Rejected frames stay on disk.
3. Calibrate: master bias, exposure-scaled master dark, normalized master flat. Each light becomes
   (light - bias - dark) / flat. The first light is then debayered (superpixel; the channel is chosen on this page:
   green is the default, AAVSO TG with V comps; red is TR, blue is TB, luminance is CV; MONO for monochrome cameras)
   and the app goes straight to Photometry.
4. Photometry: mark the target, one to ten comparison stars (C1-C10), and the check star. The image is solved on
   its own when the page opens (from the plate solution in the FITS header, or against the catalogs), and every
   catalog is asked at once: the AAVSO sequence, Gaia DR3, APASS DR9 and Tycho-2, merged into one list (one entry
   per star, high proper-motion stars moved to the night's date). With Comps or Check selected, clicking any star
   fills in its ID and magnitude, whichever circles are shown. Names: Tycho, then APASS, then Gaia (the other names
   are kept, and typing any of them finds the star). Magnitudes: the AAVSO sequence first, then APASS, Gaia, Tycho;
   the panel says where each came from. AAVSO sequence stars are always circled in cyan with their chart label
   (91 = V 9.1) and are suggested first; a sequence comp puts the AAVSO chart ID in the Chart box (otherwise na).
   The Circles box only chooses which catalog's circles are drawn. Type a name or RA/Dec in an empty comp or check
   slot and press Enter to put the marker on that star. Solve again retries (for example after a catalog did not
   answer). Every frame is aligned to the marked frame (shift, rotation, meridian flip).
5. Output: light curve, folded curve, check-star plot, Lomb-Scargle periodogram; AAVSO Extended report, CSV, PNG.

Run on Windows 11
Python 3.11 or newer from python.org, with "Add python.exe to PATH" checked.
Double-click "Create shortcuts.bat" once: it installs the Python packages and puts a SHOBS-P icon on the Desktop and
in the Start menu. Pin it by right-clicking the running app's taskbar button (Pin to taskbar); the shortcuts carry the
app's ID, so the pin keeps the SHOBS-P icon and name. The shortcut starts the app without a console
window; any error message is shown in the app and written to .shobs_p.log in your user folder. Run it again if you
move the folder. run_windows.bat still works and shows the console, which is handy when something goes wrong.

Modes (2.0)
The three buttons at the top of the sidebar switch modes; the whole frame takes the mode's color and the banner
names it. Input, Blink, and Calibrate are shared, and calibrated frames and marked stars carry over.
- Variables (navy): light curves, periods, color, comp health, and AAVSO reports, as before.
- Transits (green): the planet on the Input page, then photometry as in Variables, then the Transit fit page
  (see New in 2.1).
- Discovery (plum): Scan field is the main button on the Photometry page, and the scan results fill the Output page.
A series file records its mode. Loading one switches to its mode; series from different modes are never merged.

New in 2.2.18 (S = shared by every mode, M = one mode only, T = Transits, D = Discovery, V = Variables)
- S  Alt-az mounts and derotators, not only German equatorial mounts: a Mount box (German equatorial / Alt-az) in
     the Optics box, remembered; the headers decide only when they are clear (a flip or both pier sides = German;
     a mount keyword, or a derotator named in the headers with an alt-az telescope = alt-az). On alt-az nights
     there are no flip segments, the pier side shows "-", alignment follows the field's rotation, and the
     planners warn about the zenith keyhole (above 85 degrees) instead of a meridian flip. Flats: a German mount
     compares rotator angles modulo 180 degrees as before; an alt-az camera turned between flats and lights is
     asked about; on a derotator one caution line explains what flats can and cannot correct.
- S  Markers a few px off a star: comp and check clicks out on a star's wing now center on the star, and before
     every run the Log warns when a marker would be measured off its star (the cause of the faint check and comp
     on two HD 219134 nights).
- S  Color images that were already debayered (3-channel RGB FITS/XISF): SHOBS-P asks which channel to measure
     (green TG, red TR, blue TB or luminance CV), skips the debayer and calibration for them, and records the
     channel per night (a series flags nights in different channels).
- S  Monochrome cameras without flats: the lights' sky is checked for a color mask; when it cannot tell, SHOBS-P
     asks "Color or monochrome camera?" instead of keeping the last Bayer pattern.
- S  Focal length, pixel size and observer code are remembered between sessions (like the site); your FITS header
     still fills empty boxes, and a series' or another telescope's values are never saved as yours.
- S  The Catalog mag column in Use comps is editable (comps, spares and check): every point that used that comp
     shifts by the change, without reducing again.
- S  Keep questions after transmission loss have clear buttons, "Use these points" / "Leave them out"; each answer
     goes to the Log with the night and time.
- S  Every Log line is also written to .shobs_p.log with the date and time (rotated past 5 MB); tracebacks kept.
- S  Closing the window with unsaved series work asks to save it first.
- S  Credits page: "How to cite SHOBS-P" with a Copy button.
- S  Clear selected in Watch mode removes the selected watch star.
- S  The Input boxes and the Photometry View box wrap instead of hiding their boxes in a narrow window.
- S  A bad Master file choice says "Calibration failed:" and is logged. Self-test: no drive paths (sample transit
     reports go in a selftest_reports folder next to selftest.py). US spelling throughout; messages no longer
     name old version numbers.
- V  The duplicate-report warning moved from saving to the upload buttons (Open AAVSO WebObs..., Open AAVSO
     Exoplanet Database..., Open ExoFOP...): opening the upload page marks the report's nights "sent" (a Sent column
     in the night table; double-click a night to unmark it) and warns before a night goes twice. Transits too.
- V  Period search boxes refit on Enter or when a changed box is left. "Run in Transits mode instead?" now asks
     Variables-only users too (the target's planets are looked up). AAVSO's spelling of the Star ID is applied only
     after the report is saved.
- V  After the automatic target marker the pick moves to Comps (Variables and Transits), unless you picked a step.
- T  The Transit page also saves a fit CSV (time, flux, error, model, residual, flag; trimmed points flagged) with the
     same number as the other files of that save. The planner and Tonight's transits share their defaults
     (altitude 30 degrees, Sun -18 degrees).
- D  Discovery has its own period range in Scan settings (remembered), and the watch list survives a restart.

New in 2.2.17 (S = shared by every mode, M = one mode only, T = Transits, D = Discovery, V = Variables)
- S  Sloan g', r', i' filters (GitHub #6), for monochrome cameras: FILTER spellings like g', gp, sdss g, Sloan g,
     "Astrodon Sloan g'" are read as SG / SR / SI (z' and u' too); the Filter box offers them. Comp and check
     magnitudes come in that band: the AAVSO sequence when it has it, then APASS DR9 g'r'i', then Gaia DR3
     (synthetic SDSS photometry, else the Gaia DR3 relations from G and BP-RP, put on the primed system), each with
     its source. Reports say FILT SI (exoplanet reports #FILTER=SI, as EXOTIC does); transit fits use Sloan limb
     darkening (Claret 2004). B-V / V-R color calibration is skipped (one band). Changing the filter by hand after a
     solve fetches comps in the new band; a night in another band added to a series keeps its own magnitudes.
     Field solves on narrow fields retry with stars inside the frame (stricter acceptance on the retry).
- S  Error bars in electrons: each point's photon and sky noise now uses the camera gain (EGAIN in the header),
     binning, the superpixel debayer and the flat (the CCD equation). Before 2.2.17 one image unit was treated as
     one electron and 0.005 mag was added to every point, so long exposures had error bars about 1.9x too big
     (transit fits with chi-squared 0.27-0.44; now about 1). Without EGAIN the gain is estimated from the sky; the
     log shows both and warns when they disagree. Nights reduced before 2.2.17 keep their old error bars (a caution
     says which; run photometry on them again for consistent errors).
- S  Pop-out image window: Target / Comps / Check / Watch buttons in the pop-out too (kept in step with the page),
     the zoom buttons move into it, and "Go back"; the page button reads "Bring back" while the image is out.
- S  Compass: both N and E stay inside the image for every rotation and flip.
- S  Blink shows each frame's own number from its file name (Frame 222) and its time; Reject and Keep are separate
     buttons (Reject on a rejected frame no longer restores it); a "Rejected frames" list with Show and Keep this
     one. The same frame numbers are used in the log and in a new last "frame" column of the light-curve CSV.
- V  Periods and airmass: before a period is called significant, the nightly means are checked against each night's
     airmass, and a second false-alarm probability counts nights (for periods longer than about two nights it
     tests the nightly means alone). A peak that follows the airmass, or that a few nights carry, is shown as
     "not significant". A long period now needs about 8-10 nights to be called significant. "Airmass-corrected
     night means" shows the trend on the plot (plot only, never in reports).
- T  Comp health... and Use comps... on the Transit fit page; after Use comps the transit is refitted by itself.
     The comp cautions in the notes link to Comp health.
- T  Trim and refit: From/To times (or drag across the plot) leave out a stretch without reducing again; several
     spans; Clear trim brings the points back. Trimmed spans are grayed on the plot and named in the notes, the
     AAVSO exoplanet report NOTES, the ExoFOP fit file and the PNG.
- T  An opened report names the program that wrote it ("Fitting a SHOBS-P 2.2.16 report", "an EXOTIC 4.3.2
     report"); JD_UTC and HJD reports are converted to BJD_TDB (or warned when they cannot be).
- T  ExoFOP package folder named ExoFOP_<planet>_<tag>, with only the light-curve CSV, fit file and plot inside (no
     AAVSO report copy). The transit report, PNG and CSV of one save share one number.
- T  The chi-squared warning now says what it means: much below 1 = error bars too big (gain), much above 1 = noise
     the model leaves out.

New in 2.2.16 (S = shared by every mode, M = one mode only, T = Transits, D = Discovery, V = Variables)
- S  Bigger Photometry image (GitHub #2): zoom buttons (- + Fit) in the View box, and "Pop out" moves the image into
     its own window you can make as large as you like (a second monitor works; the window remembers its place).
     Closing it puts the image back on the page.
- S  Faster solve: each field's catalog answers are kept on disk for 30 days (proper motions reapplied), so solving
     the same field again is quick. The status line shows each catalog as it arrives (AAVSO, APASS, Gaia ...) and
     stars can be marked once the sequence and APASS are in; Gaia's deep half finishes in the background. Answers a
     server cut short are used but not kept.
- S  Photometry step strip: "1 Solving..." while it works, "1 Solved" when done, "1 Not solved" (amber, pointing to
     Solve again) when it failed.
- S  Typed comp/check names: while a typed name waits for Enter the slot says "press Enter to find and mark this
     star"; Enter always gives a check-mark or warning line.
- S  A new target or field clears the old one: loading a series of another star (or new lights of another field, or
     a mode switch that changes the target) clears the old target, comps C1-C10, check, magnitudes, suggestions,
     spares, session watch stars and slot notes. When only names can be compared you are asked (No keeps what is
     marked). Comp names without a marker are never saved into a series.
- S  Suggest apertures: the bold headline shows exactly what "Use these aperture sizes" will set; when the best size
     is at the edge of the tried range, 2.6 and 3.0 x FWHM are tried too; the sky ring starts at least 1.5 x FWHM
     outside the aperture.
- S  Telescope name: a mount name in the TELESCOP header ('EQMod Mount' from the ASIAIR) is ignored; a new Telescope
     box in Optics names your telescope (else "0.36 m, f 2900 mm" from the Optics box).
- S  Color-fit failures no longer stop the photometry run (the night is kept; the log says the color fit failed).
- S  Series hints on the Input page; "Close series" also on the Output page.
- V  Light-curve y-axis from the used points: flagged points far off scale show as small amber x at the edge ("N
     flagged off scale" in the title) instead of flattening the curve. Same for the check panel and Discovery curves.
- V  The check-star panel follows the night zoom (same hours axis and night, its own sigma).
- V  The planet-transit question names the series and night it is about.
- T  Leave-one-comp-out check after each transit fit: each comp is left out in turn and the fit redone. The spread is
     reported as a systematic (comp choice) in the notes and the ExoFOP fit file. A comp is named in a caution only
     when leaving it out moves the depth or Tc beyond the statistical error and beyond its own noise, and it is the
     odd one out; with 2 comps no comp is ever named.
- T  ExoFOP package: a folder only (no zip); a second save with nothing changed is skipped.

New in 2.2.15 (S = shared by every mode, M = one mode only, T = Transits, D = Discovery, V = Variables)
- S  Photometry page tips (GitHub #3): a step strip "1 Solve -> 2 Target -> 3 Comps -> 4 Check -> Run" shows where you
     are; Hide tips / Show tips. Once the image is solved and the target's RA/Dec are known, the Target marker is put
     on the target by itself (centerd like a click; never moves a marker you placed; not placed when no star is there
     or the RA/Dec are only the field center). Without a solve the tip says "click the target".
- S  Display stretch on Blink and Photometry (GitHub #1): linear, log or asinh with black and white points (Stretch
     controls; Reset). Display only: the measured data never change. Remembered per page.
- S  Series line at the top of the Input page: "Series: X.cvseries - 8 nights, last 13-14 Oct" or "No series loaded -
     this run will be a single night", with Load series... and Close series. Closing, loading another series or
     switching modes over unsaved points offers a save first; nothing of a closed series is carried into the next.
- S  Upload links: "Open AAVSO WebObs..." (Variables) and "Open AAVSO Exoplanet Database..." (Transits) on the Results
     rows; after a report save the status line names the file and offers the upload page.
- S  Night table flag "dew/haze?" (the log keeps "transmission loss (dew/haze?)"); the Output cautions include the
     ensemble zero-point warning; Use comps says how many magnitudes fainter than the target a widened-window spare is.
- V  Planet transits in a campaign: when the Star ID has transiting planets (NASA Exoplanet Archive, most precise
     ephemeris), every frame is checked against each planet's predicted transit (T14 plus the ephemeris uncertainty,
     in BJD_TDB when the RA/Dec are known). The log and night table say "N frames fall in planet b's transit" and
     you are asked once per night whether to flag them ("transit", left out like cloud; Planet transits... changes
     the answer). Nights of a loaded series are checked too. Planet-transit frames never go into an AAVSO report.
- V  Clicking a night in the night table zooms the light curve to that night (hours from its first point, y fitted to
     its points; click again for the whole series); the other nights' means fade with their points; the PNG saved
     while zoomed is that night's plot.
- V  Pier sides grouped: camera angles within 5 degrees are one side and their 180-degree partners the other (29 and
     30 degrees are one side), in the Side column, the pier-side caution, Pier side as segments and Comp health.
- T  "Open ExoFOP..." beside the ExoFOP package opens the target's ExoFOP-TESS page (TOI or TIC), with a one-time note
     on how uploads work. The package's fit file gives the telescope/camera from the headers and Optics box, the
     pixel scale in "/px, the aperture radius in arcsec, and the coverage (Full / Ingress / Egress / Partial / None).

New in 2.2.14 (S = shared by every mode, M = one mode only, T = Transits, D = Discovery, V = Variables)
- S  One star, one name per series: a comp, check, watch star or spare that is a series star (by any catalog name,
     a variable's name, within 4", or the same pixel) takes the series' name AND catalog magnitude when a night is
     added, whatever tonight's catalogs call it; the add-night question and the log list each change. Loading a series
     drops a session watch star that is the same star under another name. Also after a canceled run.
- S  Colors from one catalog per star: B-V from the AAVSO sequence or APASS when it has both bands (Gaia for stars
     brighter than APASS's V 10), else Gaia's synthetic photometry; V-R from Gaia. The panel and the color log name
     the catalog. The magnitude itself still comes from the AAVSO sequence first.
- S  Deeper Gaia: the frame's own box down to G 19 (VizieR), with Gaia's synthetic B/V/R for the brighter stars;
     every clickable star gets a magnitude in Milky Way fields. Circles and labels are capped so the image stays fast.
- S  Spares: a click on a blue diamond adopts that spare (name, V, colors); spares far from their catalog position
     are dropped; spares from a widened window say how many magnitudes fainter than the target they are.
- S  A comp with no catalog magnitude is flagged when it is marked; an AAVSO report with a check star named only
     'check' asks for its name; typed names in comp/check slots say what happened in the slot's hint and the log
     (keypad Enter works, catalog prefixes and spacing do not matter, APASS/2MASS J-names are found on the frame).
- S  Watch stars box fills after Load series; 'already watching' highlights the row.
- S  Save series keeps one version back: the file as it was before this session's first save becomes X_prev.cvseries.
- S  Wrong-mode guard: Transits mode on a known variable with no transit in the lights (or the reverse) asks whether to
     run in the other mode; planet hosts (VSX type EP) do not count as variables.
- S  Transmission loss vs cloud: when every measured star fades together, smoothly, with a flat sky (dew, haze), the
     frames are labeled 'transmission loss (dew/haze?)' and can be kept (default: left out) with their error bars
     raised by the star-to-star scatter measured in that stretch. Patchy or fast cloud stays 'cloud'. Points that sit
     far off right at the edge of a cloud run are flagged 'cloud edge'.
- S  Pop-up windows (Suggest apertures, Use comps, Export) open wide enough for their contents at any display scaling.
- V  Time of maximum / minimum with O-C ('Time of max/min...' on the Output page): local polynomial with a red-noise
     aware bootstrap error, Kwee-van Woerden and a Fourier fit as cross-checks, HJD and BJD, O-C against VSX or typed
     elements, saved to a times CSV next to the series that grows into an O-C diagram.
- V  Pier side for campaigns: each night's side is recorded (ROTATOR, PIERSIDE or the flip), a Side column in the night
     table, 'Pier side as segments' lines the two sides up, and Comp health shows each star's step between sides.
- V  Output: no legend on the light curve (a symbol key in the title instead); the clicked night stands out on the
     phase plot; a check star without a catalog magnitude is plotted instrumentally; 'Bin N' and wrapped comps in the
     Series box; the JD axis says 'JD - 2461326' instead of an offset; the light-curve CSV has positions for every comp;
     a warning when the comp ensemble moves the night's level against the comps nearest the target's brightness.
- T  The fit's rms per point and per 10-minute bin under the verdict and in the PNG (GitHub #5); the usable-point count
     follows the data and shows the points in the fit (#7); no check-star question in Transits mode (#4); the Planet
     box looks the planet up by itself.

New in 2.2.13 (S = shared by every mode, M = one mode only, T = Transits, D = Discovery, V = Variables)
- S  One star list from every catalog. The image solves itself when the Photometry page opens (header plate solution
     or a catalog solve), and the AAVSO sequence, Gaia DR3, APASS DR9 and Tycho-2 are fetched together and merged:
     click any star and its ID and magnitude fill in, whichever catalog's circles are shown. The Catalog box is now
     'Circles' (display only); 'Label chart' / 'Solve field' is 'Solve again'. Proper motions are applied to the
     night's date, so high proper-motion stars (HD 219134, about 55" from its J2000 place) match. A catalog that does
     not answer within 45 s costs only its own stars, and the log says so. Fields with no AAVSO sequence show APASS
     (or Gaia) circles when the Circles box says AAVSO sequence.
- S  AAVSO sequence stars are always circled (cyan, with the chart label), suggested first as comps, and set the
     Chart box to the chart ID when one is used as a comp or check (a chart ID typed by hand is kept). In AAVSO
     reports a sequence comp or check is named by its AUID (the series and the panel keep the catalog name). A note
     in the log, once, when other comps are used while sequence stars are on the frame.
- S  Type a name (any catalog's, a variable's, or a SIMBAD name) or RA/Dec in an empty comp or check slot and press
     Enter: the marker goes on that star, or the status bar says it is off the frame. One empty comp slot is always shown
     after the last one. Typing in a slot that already has a marker only changes its name.
- S  Watch stars: a star typed by RA/Dec takes its catalog name, and a star already watched (by position or by any of
     its catalog names) is not added twice.
- S  Binning changed after Calibrate: said at once ('run Calibrate again'); the solve keeps using the image's own
     binning. Loading a series reduced at another binning asks before switching.
- S  Lights scanned: a folder pointing at two places (e.g. two targets saved under one plan name) is described, and
     SHOBS-P offers to use only the frames near the target (the others are set aside as rejected; Blink brings them
     back). A FITS OBJECT naming another object than the target gives a warning, unless it is another spelling or
     (looked up) another name of the same star; when the frames do point at the target it is only a log note.
- S  Masters are saved in a 'SHOBS-P masters' subfolder of the calibration folder, so other programs that take every
     FITS file in the folder never pick them up. Masters saved beside the frames by 2.2.8-2.2.12 are still found and
     moved into the subfolder the first time they are reused.
- S  Photometry image: a new image (calibration, binning, channel) always opens showing the whole frame; star labels
     stay inside the image; small N and E arrows in the least crowded corner (from the solution; 'orientation unknown
     until the image is solved' before that), shown with Show labels.
- V  Comp health window: click a table row to highlight that star's curve (thicker, others faded, bold in the legend);
     axis labels and dates are no longer cut off at any window size; the legend sits in a column right of the plot;
     table columns fit their contents, with a horizontal scroll bar only when they do not fit.
- T  The published depth the fit is compared with is (Rp/R*)^2 from the same Archive entry; when the Archive's listed
     depth disagrees with it a note says so (HAT-P-23 b: listed 1.05%, (Rp/R*)^2 1.37%). The table, plot and verdict
     use the same value.
- T  When the cloud flag removes all the baseline before ingress or after egress, the caution says so (how many frames,
     when), and 'Refit with flagged frames (errors x2)' fits again with them, clearly labeled as including them.
- T  Meridian flip in the transit: a fit with a step at the flip, fitted together with the transit, is built and
     validated but switched off in this version (more test nights first). When it is switched on, a flip with no
     out-of-transit baseline on one side gives 'Inconclusive' (the timing is not trusted either).

New in 2.2.12 (S = shared by every mode, M = one mode only, T = Transits, D = Discovery, V = Variables)
- S  Files carry the time they were made as a military Date Time Group in Zulu (e.g. 121153Z OCT 26): on plot
     PNGs, as a '# Created' first line in CSV files, in the transit fit text of the ExoFOP package, and as a '#'
     comment line in AAVSO Extended and exoplanet reports (AAVSO's format allows comment lines; they are kept in
     the archive and ignored on upload). Tools reading the CSVs should skip lines that start with '#'.
- S  Mac: the side panels scroll with a trackpad (small wheel steps were rounded to nothing), and the fonts fall
     back to Helvetica Neue and Menlo, so the Series and Period boxes still line up. Linux wheels scroll too.
- V D Periodograms gray out periods longer than about half the data span, labeled 'longer than the data can
     test'. The search still runs there (a series of several nights can test them).
- D  Typical-scatter curve: a running median over stars within half a magnitude, leaving out stars that already
     stand well above it, so the bright end follows the bright stars (they no longer sit under the curve, which
     hid bright variables). No line is drawn across magnitude ranges with no stars.
- D  Faint end: frames where a star is not detected (net flux under 3x the sky noise) are left out of its light
     curve and scatter, so non-detections no longer read as drops to 18-20 mag. With Faintest magnitude blank, the
     scan stops where the field's typical scatter reaches 0.25 mag (shown as a dashed line and in the log).
- D  Field scan table: columns sized to what they hold (Star / position no longer cuts Gaia IDs; VSX no longer
     takes half the width).
- T  The verdict shows once, above the plot; its copy is gone from the notes on the right and from the notes in
     the saved PNG.
- T  MCMC progress names the phase: 'MCMC burn-in 600 of 1,000', then 'MCMC step 1,200 of 3,000' (the second count
     now matches the MCMC menu).
- T  Saved transit PNG: long verdicts wrap to the page (measured, not estimated), and notes are printed in full.
- T  An EXOTIC report without an ephemeris: SHOBS-P looks the ephemeris up (NASA Exoplanet Archive, most precise
     one), or reads EXOTIC's inits file beside the report, before fitting (the fit waits for it). The
     lowest point is only the fit's starting guess and is never shown as a published time. The O − C line says
     where the ephemeris came from, is shown even when the ephemeris has no stated uncertainty (with a caution),
     and flags an ephemeris whose uncertainty swamps the fit's.
- T  Variable View with only an EXOTIC report loaded: 'Variable View not available from an EXOTIC report.' (it shows
     SHOBS-P's own photometry; the report has only its final light curve).

New in 2.2.11 (S = shared by every mode, M = one mode only, T = Transits, D = Discovery)
- S  Monochrome cameras: when the lights carry no Bayer keyword, SHOBS-P looks at a raw flat. With no color mask
     (its 2x2 pixel planes agree to within 1%) it sets the Bayer pattern to MONO and says so in the log; a pattern
     you picked by hand is kept. A color mask while MONO is set gives a caution.
- S  On MONO the Calibrate page's Channel box becomes a Filter box: V, B, R, Clear (CV) and the imaging filters
     (TG, TR, TB). It fills from the FITS FILTER keyword, or set it by hand when the header has none; the AAVSO
     filter code and the comps' band follow it. (I and Sloan filters are not offered: the comp catalogs carry only
     B, V and R.) 'Apply channel to the preview' is hidden on MONO.
- S  Blink: 0.25 s/frame added.
- S  Input page: 'Write demo FITS set' is now 'Try it with sample data…'.
- T  Transit fit page: the parameter table fills the strip under the plot; notes and cautions, when there are
     any, take a third on the right. The old hint sentence is gone (the plot says what to do). The empty space
     above the plot is gone: the banner and verdict take a line only when they say something, and the plot keeps
     room for a title only when it has one.
- T  No transit predicted during the data: the page shows the night's light curve (relative flux against hours
     from the first point) with an arrow note to the nearest predicted transit, instead of an empty frame.
- T  Tonight's transits: After and End by take 2300 or 23:00 (also 23.00, 23, 130 for 01:30).

New in 2.2.10 (S = shared by every mode, M = one mode only, T = Transits, D = Discovery)
- T  Transit fit page: the plot is the page. It fills the width and height, with margins just big enough for its
     labels. The verdict sits above the plot; the parameter table and the notes run in a strip under it (as on
     the Field scan page). The table's columns are as wide as their contents, with no sideways scroll bar.
- T  The progress bar shows only while a fit runs; its message stays until you pick another night or report.
- T  Saved plot PNGs and the ExoFOP plot keep their usual 8 x 5.4 in shape, whatever the plot's size on screen.
- S  The status bar keeps its room when the window is short (its second line was cut off at the bottom edge).

New in 2.2.9 (S = shared by every mode, M = one mode only, T = Transits, D = Discovery)
- S  Input page: "Site and optics" is split in two. Site holds latitude, longitude and elevation; Optics holds
     aperture, focal length, pixel size and binning, with the scintillation checkbox and the aperture hint.
- S  Scan files fills Site and Optics from the FITS header (FOCALLEN, XPIXSZ, APTDIA or FOCRATIO, and the site
     keywords including OBSGEO-B/L/H). Empty boxes are filled quietly; when the header disagrees with what you typed
     (more than 0.01 deg, 30 m or 2%) it asks once, and a declined value is not asked again. Binning is never taken
     from XBINNING; a binned XPIXSZ is divided by XBINNING for the Pixel size box. With your Site boxes still empty
     it asks whether the header's site is yours before saving it.
- S  Frames from another observer (site more than 50 km from yours): SHOBS-P asks, then borrows their site and
     optics for the run and says so in the log. Your own values are kept and come back with your next folder of
     your own frames. A borrowed aperture is never saved as yours.
- S  Unistellar eVscope frames are recognized: 450 mm focal length, 2.9 um pixels and 114 mm aperture fill what
     their headers lack. When a frame shows too few stars to label (very short exposures), Label chart stacks up to
     30 frames first; when it still fails it says why (too few stars; the scale comes from the Optics box).
- S  Noise note after photometry: when scintillation is more than twice the photon noise, the log says so, gives
     both numbers and how much longer the exposures could be before the target reaches 75% of saturation.
- S  Adding a night warns separately when it comes from other equipment (telescope / camera), another observer
     (the header's OBSERVER), or used another telescope's site and optics.
     Such nights are best kept as their own series.
- S  Photometry page: a "View" box on the right, under the Catalog row, holds Show variables, Show apertures,
     Show catalog stars, Show labels, Candidates only (Discovery only) and Fit view.
- S  Switching mode keeps you on the page you were on.
- S  Output page titles: "Output – Variability", "Output – Transit fit", "Output – Field scan".
- T  When no transit is predicted during the data, Run photometry warns before starting, and the Transit fit page
     says how far away the nearest predicted transit is instead of fitting. A manual fit asks "Fit anyway?". The
     plot axis never stretches beyond the data and the transit window.
- T  The save buttons moved into a "Results" row below the plot.
- D  The Output page shows an empty Field scan frame before a scan (its Results row already there, as on the
     other modes' Output pages), and a "Results" row with Save plot PNG, Save
     CSV (every star: position, magnitude, scatter, candidate flags), Export candidates CSV and Export all light
     curves CSV.

New in 2.2.8 (S = shared by every mode, M = one mode only)
- S  Calibration masters are saved and reused. Each master SHOBS-P builds is written into the folder its raw frames
     came from (SHOBS-P_master_flat_bin2.fits, SHOBS-P_master_dark_bin2_0.75s.fits, SHOBS-P_master_bias_bin2.fits)
     and stays there. The next run on the same folder uses it instead of stacking again, as long as the raw frames
     (names, sizes, times, count), the binning and the bias/dark it was built with are unchanged; otherwise it is
     rebuilt and replaced. "Rebuild masters" on the Calibrate page forces a fresh stack. Masters are never stacked
     as frames, and a read-only folder only means the master is not saved. Bias/Dark/Flat also take one master file
     ("Master file…"), but only masters SHOBS-P built (their scaling and normalization are known); PixInsight and
     other masters are refused with a plain message. Before use a master is checked against the lights (size and
     binning must match; Bayer pattern, gain, offset, temperature, flat rotator angle and flat age are cautions),
     the log says which masters were used ("using saved master flat from 1–2 Oct (51 frames)"), and the night's
     setup record in the series keeps it.
- S  Star size (FWHM) is measured on bright, unsaturated, isolated stars with a Gaussian fit, not on the marked
     stars in a fixed 12 px circle. Faint or crowded stars had measured 3x too big (MicroObservatory CoRoT-1:
     10.5 px for 2-3 px stars), so Suggest... proposed apertures that swallowed neighbors. The aperture check before
     photometry and the aperture test's seeing column use the same measurement; a faint target gets a note
     suggesting 1.2-1.5 x FWHM.
- S  Clicking a star centers the marker on the star nearest the click, not on whatever is brightest nearby: a
     brighter neighbor a few pixels away no longer pulls the marker (and the aperture) onto itself. With no star at
     the click the marker stays where you clicked and the status line says so. Every frame of the photometry run
     centers the same way.
- S  The Suggest apertures window grows to show the test results when they arrive (it scrolls if the screen is
     too small).
- S  The Photometry page's right-hand panel (Stars and Watch stars) scrolls when it does not fit, for example with
     ten comps; the mouse wheel scrolls it while the pointer is over it, and the image keeps its wheel zoom. The
     comps' status lines are kept in full.
- S  A comp clicked before Label chart now fills C1 (the first slot without a marker), even when C1 still holds a
     name from another night; Label chart then corrects a leftover name that belongs to a different star
     ("Comp 1 name updated from ... to ..."). A single comp other than C1 is labeled "Comp C2" in the legend.
- S  Adding a night: a comp or check labeled from another catalog (Gaia DR3 tonight, TYC in the series) is
     recognized as the same star by sky position (3"). It takes the series' name and catalog magnitude, the night
     is re-derived with that magnitude (Gaia's V and Tycho's V differ), and the log says so. Only stars that really
     differ are warned about. Comp health counts a star under both of its names as one star, every night.
- S  Period result: when nothing is significant (false-alarm probability above 1%, or one night carries it) the
     Output page, the saved PNG and the summary say so first: "No significant period: the star looks constant at
     this precision (scatter ... per point, ... night to night)", and the fold is titled "Best peak (not
     significant)".
- S  AAVSO reports: the default note follows the camera ("Monochrome camera; not transformed" for MONO, "OSC
     reduced; not transformed" for color); a note you typed is kept. MicroObservatory frames give latitude and
     longitude but no elevation: the telescope's (Mt. Hopkins, about 2340 m) is used, never your own.
- S  Suggested comps for a faint target are about 1 mag brighter (0.3 fainter to 2 brighter), not equally faint:
     a comp as faint as the target adds as much noise as the target.
- M  Transits: the fit notes say when the comparison stars, not the target, set the noise (the target is much
     steadier against the check star than against the comps), and suggest brighter comps.

New in 2.2.7 (S = shared by every mode, M = one mode only)
- S  Nights are named the way observers say them: "K2-113 b · night of 5–6 Oct 2026" in the Transit fit night box,
     the Output per-night table (the JD moves to its own column), the night-by-night plots, Comp health and the
     series messages. The date is your local evening (from the site longitude), so a night past midnight stays one
     night. Series files still use the JD underneath, so older series open unchanged.
- S  Flips the alignment cannot see: when the camera rotator leaves its angle during a flip and comes back to it
     (ROTATOR 360 → 180 → 360), the images look unflipped, but the light path has changed. The frames after the
     rotator's excursion now start the "after flip" segment (rejected frames' headers count too).
- M  Transits: Save plot PNG on the Transit fit page: the plot as shown, with the planet, the data source, the
     verdict, the results table and the notes above it. Works for fits of EXOTIC reports too.
- M  Transits: opening an EXOTIC/AAVSO report clears the previous plot at once and fits the report by itself. A
     banner says which report is being fitted, and that your own photometry is kept: choose its night, or Close
     report, to go back. The Input page says so too, and is not changed by the report. A new photometry run or a
     loaded series closes a report left open, so the page never fits the wrong data.
- M  Transits: a report with no OBSDATE (EXOTIC 4.3.1) takes the date of its first point, in the header and the
     file name.
- M  Transits: looking up a planet whose star differs from the Star ID (a target left from another planet) replaces
     the Star ID and RA/Dec and says so. Looking up a Star ID also finds its planets: one transiting planet fills
     the Planet box; several open the chooser, transiting planets first and the others marked "does not transit".
- M  Transits: when the depth is far from the published one and the night gives a reason (a jump at an in-transit
     gap that fits nearly as well, or a one-sided baseline), the verdict reads "Timing usable; depth unreliable"
     in amber, and saved reports carry the note. The depth note no longer blames a neighbor for a deeper dip.
- M  Transits: ephemeris references with accented names read properly (Kabáth, not Kab&aacute;th).
- M  Discovery: select rows in the field-scan table (several with Ctrl or Shift) and the button marks only those
     on the image ("Mark 3 selected on image"); with none selected it marks all candidates.
- M  Discovery: a new caution, "noise begins at flip (or after a gap): check", for a star that is quiet on one side
     of a flip or gap and scattered on the other (it moves the candidate to the dim "check first" style).

New in 2.2.6 (S = shared by every mode, M = one mode only)
- M  Transits: the planet lookup finds a planet under any of its names. It first asks the NASA Exoplanet Archive's
     own alias service which name the Archive uses now, so KOI-217 b, Kepler-71 b, KOI-217.01 and TOI-4426 b all
     find the same planet, as do TrES-3b, "wasp 12 b", EPIC and TIC numbers. (2.2.5 turned KOI-217 b into
     Kepler-71 b, a name the Archive's planet table no longer uses, and found nothing.) The Input page says what the
     name resolved to and the planet's other names. A star with several planets (HD 219134) asks which one.
- S  Star ID lookup: a planet name typed as the Star ID (TrES-3 b) now finds its star: SHOBS-P tries SIMBAD's
     spelling (TrES-3b) and then the host star.

New in 2.2.5 (S = shared by every mode, M = one mode only)
- S  Up to 10 comparison stars. Pick "Comps (C1–C10)" on the Photometry page and click stars one after another:
     each click adds the next comp (C1, C2 … C10), numbered on the image. Right-click a comp to remove it; the next
     click fills the free number. Clear selected removes the last comp. The Stars panel lists every comp in use
     with its catalog magnitude, color and variable check. The comps are averaged as an error-weighted ensemble
     (as Use comps does); spares, Use comps and Comp health work as before. With two or more comps the AAVSO report
     is an ensemble (CNAME ENSEMBLE, CMAG na), and AAVSO requires a check star then: SHOBS-P warns if there is none.
     Series saved by 2.2.4 and earlier open with their comps as C1 and C2. The image legend shows one entry for
     the comps (for example "Comps C1–C4").
- S  One naming pattern for result files, modeled on the Transits report:
       AAVSO_<star>_<filter>_<date>_SHOBS-P.txt
       <star>_<filter>_<date>_SHOBS-P_lightcurve.csv   and   ..._lightcurve.png
     for example AAVSO_HD_219134_TG_06-OCT-2026_SHOBS-P.txt. The date is the UTC date of the first point saved; a
     save spanning several nights shows the first and last (16-SEP-2026_to_06-OCT-2026). The three files of one save
     share one number when a second set is saved (_2). The ExoFOP package keeps its own name.
- S  The target is looked up by itself: type the Star ID and press Enter (or click elsewhere), and RA, Dec and the
     VSX period fill in. A failed lookup is a note in the status line, not a pop-up. Look up again forces a refresh.
- S  The aperture and sky ring are drawn around the target, comps and check at the sizes in the boxes, and redraw
     as you type new sizes. "Show apertures" turns them off. In the Suggest… window the button now reads "Use these
     aperture sizes", and the window says the stars it lists were only measured for their size.
- S  Overlay switches on the Photometry image: "Show catalog stars" hides the catalog circles and their labels
     (the labeling stays, so magnitudes, comps and the variable check still work); "Show variables" now hides the
     red circles of labeled variables as well as the red ×.
- S  Blink: the "REJECTED" title above the image is no longer cut off (and is bold red).
- M  Discovery: candidate marks on the image say which candidates matter. Bold magenta: possible new variable (not
     in VSX/SIMBAD, nothing to check first). Dim: known variable, or a candidate with a "Check first" note. Light
     gray: unchecked (no sky positions, so no catalog check). Hover over a mark for the reason.
- M  Discovery: "Candidates only" next to Watch/Exclude hides everything but the scan candidates, watch stars and
     excluded stars; turning it off brings back exactly what was shown before.
- M  Discovery: "Hide known variables" on the field-scan results takes the candidates already in VSX/SIMBAD off the
     plot and the table. The others keep their numbers, so #7 in the table is still mark 7 on the image.

New in 2.2.4 (S = shared by every mode, M = one mode only)
- S  Site latitude, longitude and elevation start blank for a new user (Input page, Site and optics) and are
     remembered once typed, in the settings file on this PC (.shobs_p_settings.json in your user folder). They are no
     longer written into the program, so a shared copy of SHOBS-P carries nobody's location. After updating, type
     your site once. The transit fit and planner ask for the site if it is blank (it is needed for BJD times).

New in 2.2.3 (S = shared by every mode, M = one mode only)
- S  Telescope aperture is typed in millimetres (356 for a 14-inch). An aperture saved by 2.2.2 in centimetres is
     converted once on start (a value under 100 was centimetres; a larger one was already millimetres). A red note
     under the box warns when the number looks like centimetres, inches or metres by mistake.
- S  Save plot PNG puts the summary above the plots, laid out as on the Output page: the Series, Period and Color
     boxes side by side, the per-night table under them, the spread line and any cautions, then the plots.
- S  Blink is fast: frames are read once in the background and kept as small pictures, so blinking only swaps
     pictures. A Speed box on the Blink page offers 0.5, 1 or 2 s per frame (1 s to start; it is remembered).
- S  Series checks: adding a night (or a saved series) warns when it was reduced differently from the nights already
     there: binning, aperture or sky radii, comparison stars, check star. Each night's settings are now stored in
     the series (series saved before 2.2.3 hold only the binning, so only that is compared for their nights).
- S  Markers stay honest: when the binning changes, every marker (target, comps, check, watch stars, variable-star
     ×) moves to the same star on the new image instead of keeping the old pixel positions. A name left in the
     Stars panel with no marker on the image says so ("not marked on this image"). Before photometry the app
     stops if a marker is off the image, asks if there is no star under one, and offers to clear names that have
     no marker.
- S  The Photometry image fills its area as soon as the page opens (it could start small in the top-left corner
     until the mouse wheel redrew it); the Blink and Output plots get the same fix.
- S  Zoom and pan stay put when a marker is placed or cleared, labels are drawn, or the panel updates. Only a new
     image (new calibration or binning) starts again from the whole frame.
- S  Save series always asks for the folder and name. It starts where the open series lives (or where a series was
     last saved or loaded), with its name filled in, so saving back to the same file is one click. The AAVSO
     report, CSV and PNG still go straight to the Input page's output folder.
- S  Scintillation error with spare comps: the error follows the comps actually averaged for each point. Changing
     comps with Use comps also re-derives the photon-noise part of the error for the new ensemble (stored per star
     from 2.2.3 on; a spare's is scaled from the nearest-in-brightness comp). Nights saved before 2.2.3 keep their
     old photon error, with the scintillation part using the new comp count.
- S  Flat camera angle: every flat is checked, not only the first. A Flat folder holding flats from two camera
     angles is caught (readings 180° apart are the same camera position across a meridian flip and are not counted
     as different), as before when the flats and lights differ.
- S  Saturation on pre-calibrated frames (already dark-subtracted and flat-divided by other software): their values
     are not camera counts, so the raw limit cannot be used. A star whose core is flat-topped is flagged sat (target)
     or compsat (comps) instead: the pixels within 2% of the peak must make up a fifth of the star's half-maximum
     area (an unclipped star has about 3%), tested color by color on a color camera. Clearly clipped cores are
     caught at any star width; a core clipped only slightly below its true peak can pass.
- S  One save, one number: the AAVSO report, CSV and PNG saved for the same results share the same _2, _3 … number,
     so the three files of one save always match.
- M  Variables: period sanity checks. A best period within 5% of 1, 1/2 or 1/3 day (solar or sidereal) is marked
     "near a day alias" with a caution, and the search is repeated with each night left out: when one night alone
     carries the period ("depends on JD… alone"), the Period box and cautions say so. Both checks show what an offset
     between nights can fake.
- S  Edge warning: after photometry, a comp, spare or check star that came within 3× the outer sky radius of the
     frame edge, or sat where the master flat is under 85% of the center, is named in a warning and the log.
- S  One star under two catalog names (Tycho-2 and APASS, say) is recognized by position (within 3″) when both
     names carry coordinates. Use comps shows it as one row ("TYC … = APASS …") counted on the nights of both, and
     the ensemble uses whichever name was measured on each night. Comps picked from a labeled chart carry
     coordinates from 2.2.3 on.
- S  Highlighted night: with a night highlighted in a series, Save AAVSO report and Save light-curve CSV ask whether
     to save just that night (file names end in _JD…) or the whole series; Save plot PNG saves the view on screen.
     Each AAVSO report is noted in the series, and a report that repeats nights already saved in a report of the
     other kind (the night's own, or the whole series) warns that uploading both would put them in AAVSO twice.
- Self-test: double-click "Run self-test.bat" in the program folder. It checks this copy with your own Python
  (Astropy, photutils, Tkinter): the 2.2.3 helpers, a period search, the transit regression on your AAVSO exoplanet
  reports when it finds them on W:, and that the window opens. It changes no data; the log goes to
  selftest_log.txt (and Downloads\SHOBS-P_selftest_log.txt).
- Compatibility: everything above works with a monochrome camera, with frames that have no plate solution, and with
  frames that have no RA/Dec in the header; the flat-angle, saturation and coordinate checks simply skip what the
  headers do not carry.

New in 2.2.2 (S = shared by every mode, M = one mode only)
- S  The Output folder starts empty each time the app opens, like the frame folders. Once chosen it holds for the
     whole session.
- S  Pop-up windows (Export, Comp health, Use comps, Watch stars, field scan, transit planner …) open at the size
     their contents need, up to 90% of the screen. Their buttons stay in view; if the screen is short, the body
     scrolls.
- S  CSV files and reports are written in plain ASCII, so Excel shows the header lines correctly (no "â€“").
- S  The segment column says "before flip", "after flip" or "no flip".
- S  Scintillation in the error bars: type the telescope aperture once (Input page, Site and optics; it is
     remembered; in mm from 2.2.3) and each point's error gains the atmospheric-twinkling term for its airmass and exposure time,
     using the Elevation box. It applies to the Output plots, AAVSO reports, CSVs, exports and the transit fit, and
     the export header and fit text say whether it was used. Untick "Include scintillation" to leave it out.
     Points with no airmass (no RA/Dec and no site) keep their plain error. Young (1967); Osborn et al. (2015).
- S  Spare comps in a thin field: if fewer than 3 stars pass the checks, the brightness window widens (up to 4 mag
     fainter than the comps) until 3 do, and the log lists why the others were turned away (crowded, near the
     edge, known variable, very red, near saturation …). Reduce a night again to get the extra spares.
- M  Variables/Transits: Comp health on a single night shows each star through the night (binned, with the meridian
     flip marked) and judges it there: steady, noisier than the others, or a jump at the flip. A Flip step column
     is added, the table fits (with a sideways scrollbar if needed), and Use comps shows each star's scatter.
- M  Discovery: two cautions in a "Check first" column, on the plot title and in the candidates CSV: "near
     saturation" (the star's peak reached 80% of the saturation level in some frame) and "change begins at flip"
     (or after a gap). The light curve marks flips and gaps with a dashed line.

New in 2.2.1 (S = shared by every mode, M = one mode only)
Labeling and comps
- S  Label chart always aims the catalog where the header's plate solution says the frame points, so a target left
     over from another star cannot send it to the wrong sky. If the target is not in the frame at all, the chart
     is placed from the solution alone and the log says so.
- S  Wrong field: when new lights point far from the Input page's target, SHOBS-P asks to clear the old target,
     comps, and check star, and looks up the header's OBJECT name instead ("HD 219134 b" → HD 219134). The same
     star on a new night asks nothing. Input page: a Clear target button beside Lookup RA/Dec.
- S  A comp, comp 2, or check star marked before Label chart gets its ID and magnitude from the labeled star
     under it (typed names are never replaced).
- S  Suggested comps (green rings) are chosen by brightness and color (B−V near the target's), are always
     measured as spares, and so can be picked later in Use comps. A comp whose B−V differs from the target's by
     more than 0.5 gets a warning. When no field star is near the target's brightness (a V 5.6 star), the
     brightness note says "closest available" instead of warning.
- S  A legend under the Photometry image lists the markers on it now; Show labels hides the magnitude text on a
     crowded field.
Calibration and files
- S  Rotator angles are compared ignoring a meridian flip (30° and 210° are the same camera position on a German
     mount); flips are logged as flips, real rotator moves as moves.
- S  PixInsight XISF files are read (uncompressed, zlib, zlib with byte shuffle). Frames PixInsight already
     calibrated (_c) are recognized: calibrated flats are used as they are, without subtracting bias again. A folder
     with no readable images (raw camera files, TIFF …) is reported instead of skipped quietly.
- S  With an output folder, saves no longer open a dialog; the status bar says what was saved where.
- S  Monochrome cameras with filter wheels: the AAVSO filter (V, B, R, I, Sloan, clear) and the comps' band come
     from the FITS FILTER keyword.
Transits
- M  Three verdicts: ✓ Transit detected, ⚠ Inconclusive (the dip beats a flat line but a plain jump at a gap
     explains it as well; Tc may be usable, the depth is not), ✗ No transit measured. An inconclusive report says
     so in its notes.
- M  "Variable View" (bottom right of the Transit fit page) and "Transit View" (bottom right of the Variables page).
Discovery
- M  The Target/Comp/Check line is hidden; the scan settings fit their panel; a "Most stars to measure" setting
     (800 by default) replaces the old 400-star limit, and the log says when the limit is reached.
- M  With no candidates, the ten nearest misses are listed (~1, ~2 …). Saturated stars are gray × in the scatter
     plot, marked in their light-curve title, and never the star shown first.
- M  "Known variables measured…" lists every VSX/SIMBAD variable in the frame with the scatter measured for it.
- M  (also the Variables field scan) Each star's light curve is lined up across a meridian flip or rotator move
     before its scatter is measured, which lowers the noise floor on flip nights.

New in 2.2 (all modes; S = shared by every mode, M = one mode only)
All modes
- S  Input page: an Output folder. Every Save button (reports, light curves, plots, CSVs, the ExoFOP package,
     credits) writes there without asking; a file already there is never overwritten (a new copy gets _2, _3 …).
     Leave it empty to be asked each time. A series saves back to its own file once it has one. (2.2.2: the folder
     is no longer remembered between sessions.)
- S  Input page: a ✕ (clear) button beside each folder's Browse button.
- S  Input page: the "Comparison and check stars" note is gone (they are chosen on the Photometry page).
- S  No Debayer step. Steps are Input, Blink, Calibrate, Photometry, Output. The Bayer pattern and channel are on the
     Calibrate page; when calibration finishes, the first light is debayered and the Photometry page opens.
     "Apply channel to the preview" re-debayers it if you change the channel.
- S  Photometry image: the mouse wheel zooms around the cursor, dragging pans, and Fit view shows the whole frame
     again. A click without dragging still marks a star; the view is kept while you mark stars.
- S  Plate solutions in the FITS header (TAN, with SIP distortion terms; ASIAIR, NINA, SharpCap and others write
     them) are used directly by Label chart / Solve field: no target and no star-pattern search needed. The triangle
     search is still there for frames without one.
- S  Calibrate warns before building masters when the darks are far from the lights' exposure (more than 3x either
     way: 0.75 s darks for 120 s lights are scaled x160, which multiplies the dark's noise and pattern into every
     light), and offers bias only for that run. It also warns when the flats were taken at another rotator angle
     than the lights (ROTATOR in the header), and logs any rotator change during the lights.
- S  Comp guidance: after Label chart, labeled stars within about 1 mag of the target (not variable, not
     saturated) get green rings and are listed in the log. A comp more than 1.5 mag brighter or fainter than the
     target gets a warning beside it.
Transits
- M  The fit runs by itself when the Transit fit page opens on fresh photometry (Cancel stops it).
- M  A fit is cleared as soon as the photometry, the comps, or the planet change, so an old fit can no longer be
     saved by mistake.
- M  "Light-curve details, comps, check star (Variables view)" sits at the left of the button rows, and that page has
     "◀ Back to transit fit". "Save light-curve CSV…" is on the Transit fit page too. The page no longer runs past
     the edge of a scaled (125–150%) screen.
- M  The verdict also fails (No transit measured) when the depth is more than 3x the published depth (or under a
     third of it) and more than 3σ away, or when a plain jump at the night's largest gap (20 min or more) fits as
     well as the transit (a mount problem or rotator move can fake a dip). Checked on K2-113 b, 6 Oct 2026: the
     bright-comp reductions fail, as they should.
- M  Gap note: a real gap between frames inside the transit is reported separately from time not covered at the
     start or end of the data ("the data end 40 min before the fitted egress").
- M  Planet lookup uses the published ephemeris with the smallest predicted uncertainty tonight (all of the NASA
     Exoplanet Archive's entries for the planet), not only the composite table's. The Input page says which.
- M  A warning when the per-point error bars are far larger than the real scatter (reduced χ² under 0.3), which
     usually points at calibration.
Discovery
- M  No Target block on the Input page; a Field center box appears only when the lights' headers neither carry a
     plate solution nor say where the telescope pointed.
- M  Photometry page: no Target / Comp / Check buttons. The field is solved as soon as calibration finishes (from the
     header's plate solution when there is one); catalog stars are circled and known variables marked. The panel
     shows the field (scale, center, stars, magnitude range, saturated stars, known variables) and the scan settings
     (faintest magnitude, detection threshold, candidate level, leave out crowded stars). The catalog defaults to
     APASS DR9 (an AAVSO sequence only covers charted variables).
- M  Scan magnitudes are put on the catalog's scale automatically: an ensemble zero point from every labeled,
     non-variable, unsaturated star (sigma-clipped; untransformed).
- M  Click a star to Watch it (always measured and listed, with its light curve) or Exclude it (left out of the
     scan); right-click the marker to undo. Both are listed in the panel.
Variables
- M  RA and Dec are also shown in hours-minutes-seconds and degrees-arcminutes-arcseconds on the Input page.
- M  Star ID spelling: "hd219134" suggests "HD 219134" (also TYC, HIP, BD, GCVS names like "rr lyr"), on the Input
     page and again before an AAVSO report is saved.
- M  On a single night shorter than two cycles of the best period, the fold and the periodogram are grayed out with
     a note, and the period is marked "not reliable".

New in 2.1 (Transits)
- Input page, Planet group (Transits mode): type the planet (TrES-3 b, KOI-217 b, TOI-2046 b) and press Lookup.
  It asks the NASA Exoplanet Archive (TOIs not yet there: ExoFOP) for period, T0, duration, depth, Rp/R*, a/R*,
  impact parameter, and the star's temperature, and fills RA/Dec and Star ID if they are empty. Every value can be
  typed or changed. Site and optics gains Elevation (m) for exoplanet reports.
- Output page in Transits mode is the Transit fit page. Pick a night, press Fit transit:
  1. Least-squares fit with the published a/R* and impact parameter as Gaussian priors (a/R* at least ±3%, b at
     least ±0.04), broad limb-darkening priors from the star's temperature, and airmass detrending (on by default;
     a time slope is optional).
  2. Error bars scaled so the reduced chi-square is 1, then by the red-noise beta (correlated noise).
  3. MCMC every time (Quick 1,500, Standard 3,000, or Thorough 8,000 steps; 20 walkers), for the values and
     uncertainties. The Standard run takes well under a minute.
  4. A free-shape fit (no priors on a/R* and b) as a check.
  The table lists SHOBS-P, the free shape, the published values, and EXOTIC's results when a report is loaded.
  Orange notes flag a fit that pulls more than 3σ from the published shape, a grazing geometry, a one-sided
  baseline, a gap inside the transit, a missed transit, O − C against the ephemeris, red noise, and a depth that
  differs from the published one.
- Open AAVSO exoplanet report (EXOTIC)…: refits the light curve in an EXOTIC report and shows EXOTIC's numbers
  beside SHOBS-P's, with both models on the plot. On the TrES-3 b (4 Sep 2026) and KOI-217 b (9 Sep 2026) reports,
  SHOBS-P's Tc agrees with EXOTIC's within the errors, and its Rp/R* stays near the published value where EXOTIC's
  ran large (0.19 vs 0.357 for TrES-3 b; 0.132 vs 0.174 for KOI-217 b).
- Tonight's transits… (Transit fit page, and next to Lookup on the Input page): every known transiting planet
  whose whole transit plus the baseline is in the dark (Sun below −18° by default) with the star above the
  altitude limit (30°), V 9–13.5, depth at least 0.5%. After / End by (24-hour local time, e.g. 23:00) limit the
  window. Double-click a row to make it the planet (switches to Transits, fills the Input page, runs Lookup).
  The planet list comes from the NASA Exoplanet Archive and is saved and refreshed weekly. "Same search on
  Swarthmore…" opens the Swarthmore Transit Finder with the same site and limits, to compare.
- Transit planner…: transits in the next N days from the site, with local ingress/mid/egress times, the target's
  altitude, the Sun, the Moon, the ephemeris uncertainty at that date, and whether the baseline fits ("full",
  "transit only", "partial"). Export CSV.
- Nearby-binary (NEB) check…: every labeled neighbor within 2.5′, the eclipse depth it would need to make the
  signal, and a verdict. "Watch the neighbors that need checking" adds them as watch stars; after a transit night is
  measured and fitted, each is cleared (or not) from its measured drop at the transit time.
- Save ExoFOP package…: a folder and a .zip with the light curve (CSV), the fit and ExoFOP notes (text), the plot,
  and an AAVSO exoplanet report. Save AAVSO exoplanet report… writes the report alone (EXOTIC's #TYPE=EXOPLANET
  format; check AAVSO's current instructions before uploading).
- Light-curve details (Variables view) opens the usual Output page for the same series.

New in 2.0.1
- Each variable is marked once: a labeled star that is a known variable gets a red circle only; variables that are not
  labeled stars get a red ×. "Show variables" (next to the color checkbox) hides or shows the red ×s.
- Comps, comp 2, and the check star are checked against VSX and SIMBAD by where they are marked, so the check works
  whatever catalog the chart was labeled with tonight (for example comps picked from Tycho-2, chart labeled with Gaia).
- Wider Star ID boxes in the star panel.
- Watch stars: "Add a star…" takes a name (V1441 Cas, NSV 14464, TYC 4006-946-1, a labeled Gaia ID) or RA Dec
  (23 13 48.1 +57 07 34, or degrees). It looks on the labeled chart, then in the field's known variables, then asks
  SIMBAD and VSX. A star clicked with Watch that is not a catalog star now keeps its sky position, so it is found
  again on later nights after the image is solved.
- Aperture test: each scatter has a ± (bootstrap), and a "lowest" column says how often that aperture stays lowest
  when the frames are resampled. Only 80% or more counts as a clear winner; otherwise it says "no clear winner" and
  keeps the suggested 1.8 × FWHM. The target's scatter has its own ± and lowest column. "60 frames (slower, surer)"
  runs the test on twice as many frames.
- Tables (Output summary, watch stars, Comp health, field scan) size their columns to fit the headings and the
  Windows display scaling.
- Period at the edge: when the best period sits at the end of the search range (within 10% of the max, or 5% of the
  min) the Output page cautions that the true period may lie outside it; the field scan marks it "(edge)".
- Switching modes lands on the Photometry page: always for Discovery, and for Variables and Transits when a frame
  is already loaded.
- Field scan: "Known variables in the field…" lists every VSX and SIMBAD variable in the frame and what the scan saw
  for each (a candidate, more scatter than normal, steady tonight, or not measured). Click one to see its light curve;
  Watch selected follows it from then on.
- Input page subtitle updated: comparison, check, and watch stars are picked on the Photometry page.

New in 2.0
- The three modes above, with mode-colored frames and a banner on every page.
- Comparison and check stars moved to the Photometry page, in a panel beside the image: ID, magnitude, catalog
  B-V (✓), a warning for very red stars (B-V > 1.5), and a variable-star check. Clear a star with its ✕.
- Variable-star check: the solve also asks AAVSO VSX and SIMBAD (one query per field) for known and suspected
  variables. They are marked with a red × on the image, labeled stars on them get red circles, and hovering says
  what they are (for example "SIMBAD LP? long-period variable candidate"). Clicking one as a comp or check asks
  first. Flagged stars are never picked as spare comps or used in the color fit.
- Watch stars: pick Watch and click a star to follow it. It is measured every frame, found again on later nights after
  the solve, and has its own light curve (Watch light curves…, also on the Photometry page). Spares in Comp health
  and stars in a field scan can be watched with one button; a watched spare's earlier measurements count at once.
- Suggest… (next to the aperture sizes): measures the star size (FWHM) and proposes r = 1.8 × FWHM, a sky ring from
  the larger of r + 3 and 3.2 × FWHM, with four times the aperture's area. It warns about neighbors in or near the
  aperture. "Test 4 apertures on 30 frames" measures real frames and picks the size with the lowest check-star scatter.
- Output summary as a table: Series, Period, and Color boxes, one row per night (date, points, mean ± error, target
  and check scatter, flip step, B-V, flags, note), the spread of nightly means, and cautions in orange. Click a night
  to highlight it on the plots; click it again to show all.
- SHOBS-P icon in the title bar and taskbar, and Create shortcuts.bat.

New in 1.8.1
- Clear a marked star: right-click its marker on the Photometry image, or pick its role and press "Clear selected".
  Clearing a comp, comp 2, or check also empties its Star ID and magnitude on the Input page and forgets its stored
  catalog colors.
- Hover on the Photometry image: point at any marker or labeled star to see its name, catalog magnitudes (and B-V),
  and pixel position. Spare comps from the last run are drawn as small blue diamonds and named on hover.
- Comp health takes SUSPECT stars out of the reference and measures again, so one drifting star no longer makes the
  steady ones look as if they drift the opposite way. The window lists the stars it left out.
- Comp health shows each star's pixel position and its distance from the target (the distance does not change with
  framing or the meridian flip). Spare positions are recorded from 1.8.1 on.

New in 1.8
- Name: Shiloh Hill Observatory – Photometry (SHOBS-P).
- Spare comps and "Use comps…" (Output page). Each run also measures up to 8 labeled stars near the comps'
  brightness ("spares", listed in the log). Comp health shows them with the comps, and Use comps… lets you choose
  any set of comps and spares as the comparison; every point is re-derived from the stored measurements, with no
  re-reduction, and "Back to the original comps" undoes it. Nights reduced before 1.8 have no spares, but their
  comps can still be dropped.
- Line up flip only (Output page): moves each night's after-flip points onto that night's before-flip level and
  leaves the nights alone, so slow night-to-night changes survive. The summary always lists each night's flip step.
- Period from: the period search can use 5, 10 (default), or 30-minute bins instead of every frame, so each night
  counts by the hours observed rather than the number of frames, and the false-alarm probability is no longer
  inflated by thousands of correlated frames.
- Hover over any point on the Output plots (raw points, nightly bins, check star, fold, periodogram) to see its value,
  time, night, flag, and file.
- Color calibration: the fit weights each field star by its measured noise, leaves out stars noisier than 0.08 mag,
  uses only frames that pass QC (sat, drift, cloud), and is marked NOT USED when the weighted scatter exceeds 0.15 mag.
  The field scan uses the same fit and leaves clouded frames out. The summary lists B-V per night.
- The solve logs how many stars each catalog gave (and any catalog that did not answer) and how many
  labeled stars have B, V, and R. If "Also measure R and B" is ticked but B-V cannot be calibrated, the app says so
  before the run starts.
- Cancel buttons for calibration, photometry, and the field scan. A canceled run adds nothing.
- Series safety: answering No when adding a night starts a new series and no longer overwrites the loaded file.
  Reducing a night that is already in the series offers to replace it.
- The light-curve CSV has each star's pixel position per frame (target, comps, check), to see where a star sat on
  the sensor each night.
- Pick buttons on the Photometry page match their marker colors and reset to Target for a new night. Comp health's
  table no longer clips its rows.

New in 1.7
- Binning fix. Binning (Input page) now bins only same-color pixels, so the frame stays a color (Bayer) mosaic and
  the red, green, and blue channels stay separate. Before 1.7, binning 2 or 4 averaged a red, two green, and a blue
  pixel together before the debayer: every channel was really a clear-like blend, the filter code (TG, TR, TB)
  did not match the light measured, and B-V came out equal to the comps' catalog color. Re-reduce any night that
  was binned in an earlier version. Loading such a series names those nights, and the Output summary repeats it.
  Binning 1 was never affected.
- Live log and progress bar on the Photometry page: the last few log lines, frame count, elapsed time, and time
  left, for photometry runs and field scans. "Hide log" folds the log away.
- Scatter per night: the Output summary lists each night's robust scatter (1.4826 x MAD) for the target and the
  check star.
- Color calibration. With "Also measure R and B" ticked and the chart labeled from Gaia DR3 (B, V, R) or APASS DR9
  (B, V), the labeled field stars are measured in each channel on about 25 frames. Their catalog colors against
  their instrumental colors give a straight line, B-V = a + k x (instrumental color); outliers (variables,
  blends, bad catalog colors) are rejected. The fit, its scatter, and the number of stars go in the log, and the
  target's B-V and V-R are put on the catalog scale. A fit is not used when the stars span less than 0.3 mag in
  color or the slope is far from 1. Field stars near saturation or with a close neighbor are left out.
  The color term k belongs to the camera and optics, so a good fit is saved and reused on nights without enough
  labeled stars (then anchored on the comps' catalog colors). The summary says which method each color used.
  Field scans use the same fit for every star's B-V. Magnitudes are still untransformed (TRANS=NO).
- If the camera itself binned the frames (XBINNING > 1), the log notes that some capture programs mix the colors
  when binning a color sensor.

New in 1.6
- Binning (Output page, "Bin"): one averaged point per night, or per 60/30/15/10 minutes, drawn over the raw
  points with honest error bars (the larger of the scatter-based error and the typical point error / sqrt(n)).
- Mark periods: type any periods in days, comma separated (catalog values, planet candidates). They are drawn on the
  periodogram and the power at each one is reported. Period search limits can go out as far as the data support.
- Comp health (Output page): each comparison star and the check star are measured against the others, frame by
  frame, so clouds and airmass cancel. A star whose nightly level wanders is marked SUSPECT. Needs three stars
  (two comps and a check) to say which one moved, and per-comp data saved from 1.6 on (re-reduce older nights).
- Export... : time as JD (UTC), HJD (UTC), or BJD_TDB; magnitude or relative flux; every point or binned; extra
  columns of your choice. Setups can be saved by name. The file header records the target, comps, processing
  choices, and night notes.
- Add series file... merges a saved series into the open one (it warns if the comps differ).
- Night notes... attaches a note to a night ("pre-calibrated by other software", "thin cirrus after 02:00").
  Notes are saved with the series, marked * in the legend, and written to the CSV and exports.
- Bias, dark, flat, and master files found in the Lights folder are skipped (by name or IMAGETYP) and listed in
  the log. Master files are always skipped. If most of the folder is labeled bias/dark/flat, the capture software
  saved the night's lights with the wrong image type, so they are all kept as lights and the log says so (1.6.1).
- The check-star zero point weights every night equally, so one long night no longer sets the level for the
  rest. A checkbox turns the check-star correction off to compare.
- The Credits page Back button returns to the page you came from. The sidebar filter-code text is gone.
  Pre-calibrated frames show "peak (pre-calibrated file; not camera counts)" in the log.

New in 1.5
- Pre-calibrated frames. If the lights already look calibrated (a _CAL style file name, floating-point pixels, or
  calibration keywords in the header), Calibrate says why and offers to skip bias, dark, and flat. The raw
  saturation check is turned off for those frames, because their values are no longer camera counts.
- Cloud flag. A frame is flagged "cloud" when the comparison stars' light falls more than the set percentage
  (default 20) below that night's median: dew, cloud, haze, twilight. Set the percentage on the Output page and
  press Apply; every night in the series is re-flagged at once. Flagged points show as gray x marks and are left
  out of the period search and the AAVSO report while "Leave flagged points out" is ticked.
- Time axis. The light-curve and check-star plots can show JD, UTC, or local time (Output page). Data, CSV, and
  reports stay in JD.
- Color (opt-in). Tick "Also measure R and B" on the Photometry page. Each frame is also measured in the green,
  red, and blue channels, and the Output page reports B-V and V-R with a rough temperature and spectral class.
  Real B-V needs comps picked from a catalog with B, V, and R (Gaia DR3 has all three; APASS and Tycho-2 have
  B and V). Without them, only the color difference from the comps is shown. The channels are untransformed, so
  treat the numbers as approximate.
- Field scan (opt-in, "Scan field for variables" on the Photometry page). Measures every star in the frame on every
  frame, with the same calibration, alignment, and aperture, and plots scatter against brightness. Constant stars
  form a band; a star that scatters at least 3 times more than stars of its brightness is listed as a candidate,
  with its light curve and periodogram. Once the image is solved, each star gets RA/Dec and is checked against VSX:
  "KNOWN" or "not in VSX - possible new variable". Export the candidate list (ready to start a VSX submission) or
  every star's light curve. A candidate is a lead, not a discovery: confirm it on another night, and rule out a
  blend with a nearby star or a bad pixel. Stars closer together than about two aperture radii are measured as one.
- Credits and sources page (sidebar), with each source's own acknowledgement wording. "Save credits as text" writes
  the list for a paper or report.

AAVSO report
Extended format (TYPE=Extended, DATE=JD, OBSTYPE=CCD, DELIM=comma). Filter follows the channel: TG, TR, TB, or CV.
TRANS is NO, MTYPE is STD. CMAG and KMAG are instrumental. Two comps are reported as CNAME=ENSEMBLE with CMAG=na.
GROUP is na; the night and any QC flag go in NOTES. The app warns if the observer code, chart ID, or star ID is
blank. Confirm the observer code, chart ID, and comparison magnitude before uploading with WebObs. Not transformed.

Multi-night series
Save series writes a .cvseries file with every point and the settings; it auto-saves after each later run.
Load series, reduce the new night with the same comp, comp 2, and check stars, and choose Yes to add it.
Remove night... (Output page) drops one night so you can redo it. Line up segments removes night-to-night and
meridian-flip offsets for transit-style work.

Demo
"Try it with sample data…" (Input page) builds a small synthetic night with a 0.12 d variable and fills in every field.
Calibrate, apply the debayer, then Run photometry.

Notes
Binning 2 keeps ASI2600-size frames quick (1.7 and later bin same-color pixels only). Coordinates are on the
binned, debayered image. If you change binning, rebuild the masters. "raw sat ADU" is checked on the raw frame. Chart labeling needs the right focal length and pixel size.
A single night cannot pin down a long period; the Output page warns under 1.5 cycles.

History
2.2.18 Alt-az mounts and derotators; markers centered from a star's wing and a pre-run marker check; RGB
       pre-calibrated frames; mono detection from the lights; remembered optics and observer code; editable
       comp catalog magnitudes; Keep-question buttons; timestamped log file; save prompt on close; duplicate
       warning at the upload buttons; transit fit CSV; Discovery period range and watch list kept; How to cite;
       US spelling.
2.2.17 Sloan g' r' i' filters (#6); error bars in electrons (camera gain, binning, debayer, flat; no 0.005 floor);
       airmass check and night-counting FAP before a period is called significant; pop-out image controls; compass
       labels inside the image; Blink frame numbers, Reject/Keep and rejected list; Comp health and Use comps on the
       Transit fit page; trim and refit; report program named; ExoFOP_<planet> package; chi-squared wording.
2.2.16 Pop-out and zoom for the Photometry image (#2); catalog cache and per-catalog progress; three-state
       solve step; a new target clears the old field; Suggest apertures headline; telescope name; color-fit guard;
       leave-one-comp-out check on transit fits; ExoFOP package as a folder; light-curve y from used points; check
       panel follows the night zoom; series hints and Close series on Output.
2.2.15 Photometry tips and automatic target marker (#3); display stretch (#1); Input-page series line with Close
       series; AAVSO and ExoFOP upload links; ExoFOP fit-file equipment, scale and coverage; planet transits flagged
       in Variables campaigns; night click zooms the light curve; pier sides grouped within 5 degrees; dew/haze? flag;
       zero-point warning in the cautions; fainter-than-target in Use comps.
2.2.14 One star one name (and magnitude) per series; one-catalog colors; deeper Gaia; spares adopted on click;
       transmission loss vs cloud; time of maximum with O-C; pier side for campaigns; Output title key, instrumental
       check, Bin N, zero-point warning; Save series keeps _prev; wrong-mode guard; transit rms line and point count.
2.2.13 One star list from every catalog, auto-solve, typed stars, AAVSO sequence first; target check on
       scanned lights; masters subfolder; compass and fit view; Comp health window; transit depth reference and
       cloud-flagged baseline.
2.2.12 DTG stamp on files; Mac trackpad and fonts; periodogram shading; Discovery scatter curve, faint end and
       table columns; verdict once; MCMC phases; PNG wrap; ephemeris for EXOTIC reports; report-only Variable View.
2.2.11 Mono cameras found from the flat, Filter box on MONO; 0.25 s blink; sample-data button; Transit fit
       table fills the strip, no empty band, light curve when no transit is predicted; HHMM times.
2.2.10 Transit fit page: plot first, table and notes in a strip under it; progress bar only while
       fitting; status bar no longer cut off.
2.2.9  Site and Optics boxes; site and optics from the FITS header (and borrowed for another observer's
       frames, then restored); Unistellar headers and stacked solve; scintillation note; equipment/observer
       warning; View box; mode switch keeps the page; no-predicted-transit check; Results rows; Field scan saves.
2.2.8  Calibration masters saved beside their frames and reused; star size from bright isolated stars;
       nearest-star centring; same star under two catalog names when adding a night and in comp health; plain
       'No significant period'; report note follows the camera; MicroObservatory elevation; scrolling star panel;
       comp-noise warning (Transits).
2.2.7  Night names; hidden-flip detection; Transit PNG; report handling (auto fit, banner, closes on new data);
       Star ID finds planets; depth-unreliable verdict; Discovery mark selected and noise-at-flip caution.
2.2.6  Planet lookup through the Exoplanet Archive's alias service (any name; KOI-217 b fixed); planet names as
       Star ID.
2.2.5  Up to 10 comps (ensemble reports); one file-name pattern with dates; automatic target lookup; aperture
       rings with a switch; Show catalog stars; Discovery candidate styles, Candidates only, Hide known; blink title.
2.2.4  Site starts blank and is remembered in the settings file, not the program (ready to share publicly).
2.2.3  Aperture in mm; PNG with the summary header; fast blink with a speed control; series setting checks; markers
       rescale with binning and stay in sync with the Stars panel; plots fill on first show; zoom kept; Save series
       asks; scintillation follows the comps in use; every flat's angle checked; saturation on pre-calibrated
       frames; one number per save; period alias and leave-one-night-out checks; edge warning; one star under two
       catalog names; save a highlighted night, with duplicate-report warnings.
2.2.2  Output folder starts empty; pop-ups fit their contents; ASCII CSVs and reports; segment wording;
       scintillation in the error bars; more spares in thin fields; one-night comp health with the flip marked;
       Discovery cautions for near-saturated stars and changes that start at a flip.
2.2.1  Labeling follows the header plate solution; wrong-field check and Clear target; comps filled after Label
       chart; color-aware comp suggestions kept as spares; image legend and Show labels; meridian-flip-aware rotator
       check; XISF and PixInsight-calibrated frames; quiet saves to the output folder; filter-wheel filters for
       monochrome cameras; inconclusive transit verdict; Variable View / Transit View; Discovery near-misses,
       saturated-star marking, star limit setting, and flip alignment in the scan.
2.2.0  Output folder, clear buttons, no Debayer step, wheel zoom, header plate solutions, dark-scaling and rotator
       warnings, comp guidance; Transits: automatic fit, stale-fit clearing, depth and step-at-gap checks in the
       verdict, gap wording, most precise ephemeris, error-bar check, page fits the screen; Discovery: target-free
       Photometry page with auto-solve, catalog zero point, watch and exclude; Variables: sexagesimal display,
       star-name spelling, grayed period panels on short single nights.
2.1.8  Tonight's transits: every known transiting planet observable whole on a night (dark, altitude,
       baseline, V range, depth, Moon, meridian, and an After / End by window); double-click to use it. Same search
       on the Swarthmore Transit Finder with one button.
2.1.7  Field solving: Label chart works without a target, from the FITS header pointing (or a typed field
       center), by matching star triangles; it also rescues a failed target-anchored match. Saturated stars are kept
       out of field-scan candidates. Candidate marks can be cleared; stale variable-check text clears with new folders.
2.1.6  Transit verdict (no transit measured: flat line about as good, ΔBIC < 10, or Tc unpinned), grayed
       numbers and an export guard; resizable results panel, EXOTIC column only with a report; aperture check before
       a photometry run.
2.1.5  Label chart: match tolerance about 3" of sky at any plate scale, and when the target marker is on a
       neighbor, the stars near the click are tried as the target (the marker moves to the one that locks).
2.1.4  MicroObservatory frames: one question sets the telescope's site, optics (560 mm), and camera on the
       Input page; your own setup comes back when you scan your own lights.
2.1.3  MicroObservatory: Label chart uses the telescope's own plate scale (no superpixel for monochrome
       frames), and the header's west-positive longitude is read correctly.
2.1.2  Monochrome cameras (Bayer pattern MONO) and MicroObservatory frames: local-time DATE-OBS with a UTC
       offset, no debayer, 12-bit saturation, the telescope's site offered for airmass and BJD.
2.1.1  Planet lookup fixed (the Archive rejected two columns), and survey names in any spelling (Wasp-12 b).
2.1    Transits: planet lookup, transit fit with MCMC and red noise, EXOTIC report comparison, planner, NEB
       check, ExoFOP package, AAVSO exoplanet report.
2.0.1  Single variable marks and Show variables, comp vetting by position, Add a star, aperture test with
       uncertainties, fitted table columns, period-edge caution, mode landing page, known variables in a scan.
2.0    Three modes (Variables, Transits preview, Discovery), comp panel with VSX/SIMBAD vetting, watch stars,
       aperture suggestion, tabular Output summary, icon and shortcuts.
1.8.1  Clear a marked star, hover on the Photometry image, Comp health second pass and star positions.
1.8    Spare comps and Use comps, flip-only line-up, binned period search, hover values, weighted color fit,
       catalog band report, cancel, series safety, star positions in the CSV, renamed SHOBS-P.
1.7    Bayer-preserving binning (fixes mixed colors), live log and progress bar, scatter per night, color
       calibration from field stars.
1.6    Binning by night, mark periods, comp health, export with HJD/BJD, add series file, night notes, master and
       mislabeled-frame handling (1.6.1).
1.5    Pre-calibrated frame warning, cloud flag, time axis, color from channels, field scan, credits page.
1.4.x  Remove night; same-star guard; different-stars warning; grouped Input page; button rows always visible;
       alignment that handles rotation, diffraction spikes, and meridian flips; raw-frame saturation check;
       meridian-flip segments; Gaia fallback through VizieR; catalog choice for chart labeling.
1.1-1.3 First working release: AAVSO report columns fixed, NaN flat fixed, VSX lookup fixed, check-star zero point
       fixed, chart labeling, frame alignment, normal-size window.

CREDITS AND SOURCES
-------------------
Quoted sentences are the acknowledgements each source asks for. Please include them when you publish results.

SOFTWARE

Astropy
  Used for: FITS input, time and Julian Dates, coordinates and airmass, units, sigma clipping, Lomb-Scargle.
  "This work made use of Astropy: a community-developed core Python package and an ecosystem of tools and resources for astronomy." Astropy Collaboration 2013, A&A 558, A33; 2018, AJ 156, 123; 2022, ApJ 935, 167.
  https://www.astropy.org

photutils
  Used for: Aperture photometry, sky annuli, centroids, FWHM and elongation.
  Bradley, L. et al., photutils: an Astropy package for detection and photometry of astronomical sources. Zenodo, doi:10.5281/zenodo.596036.
  https://photutils.readthedocs.io

ccdproc
  Used for: Bias, dark, and flat calibration.
  Craig, M. et al., ccdproc: CCD data reduction software. Zenodo, doi:10.5281/zenodo.1069648.
  https://ccdproc.readthedocs.io

NumPy
  Used for: Array math throughout.
  Harris, C. R. et al. 2020, Nature 585, 357.
  https://numpy.org

SciPy
  Used for: Star detection filters.
  Virtanen, P. et al. 2020, Nature Methods 17, 261.
  https://scipy.org

Matplotlib
  Used for: All plots and image displays.
  Hunter, J. D. 2007, Computing in Science & Engineering 9, 90.
  https://matplotlib.org

CATALOGS AND SERVICES

Gaia DR3 (ESA)
  Used for: Comparison stars, positions, proper motions, Johnson V/B/R synthetic photometry, field stars for color calibration.
  "This work has made use of data from the European Space Agency (ESA) mission Gaia, processed by the Gaia Data Processing and Analysis Consortium (DPAC). Funding for the DPAC has been provided by national institutions, in particular the institutions participating in the Gaia Multilateral Agreement." Gaia Collaboration, Prusti et al. 2016, A&A 595, A1; Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1; Gaia Collaboration, Montegriffo et al. 2023, A&A 674, A33 (synthetic photometry).
  https://www.cosmos.esa.int/gaia

APASS (AAVSO)
  Used for: Comparison stars, Johnson B and V and Sloan g', r', i' (2.2.17), field stars for B-V calibration.
  "This research was made possible through the use of the AAVSO Photometric All-Sky Survey (APASS), funded by the Robert Martin Ayers Sciences Fund and NSF AST-1412587." Henden, A. A. et al. 2016, VizieR II/336.
  https://www.aavso.org/apass

Tycho-2
  Used for: Bright comparison stars.
  Høg, E. et al. 2000, A&A 355, L27.
  https://cdsarc.cds.unistra.fr/viz-bin/cat/I/259

AAVSO VSX
  Used for: Target lookup, catalog periods, and the known-variable check of comparison stars, spares, watch stars, and field scans.
  Watson, C. L., Henden, A. A. & Price, A. 2006, Society for Astronomical Sciences Annual Symposium 25, 47.
  https://vsx.aavso.org

AAVSO Variable Star Plotter
  Used for: AAVSO comparison-star sequences and chart IDs.
  American Association of Variable Star Observers.
  https://app.aavso.org/vsp/

SIMBAD (CDS)
  Used for: Target coordinates and proper motion; known and suspected variables (object types) used to vet comparison stars, spares, and field scans (TAP service); name lookup for Add a star (watch stars).
  "This research has made use of the SIMBAD database, CDS, Strasbourg Astronomical Observatory, France." Wenger, M. et al. 2000, A&AS 143, 9.
  https://simbad.cds.unistra.fr

NASA Exoplanet Archive
  Used for: Planet lookup in Transits mode: period, T0, duration, depth, Rp/R*, a/R*, impact parameter, host star (Planetary Systems Composite table, TAP service).
  "This research has made use of the NASA Exoplanet Archive, which is operated by the California Institute of Technology, under contract with the National Aeronautics and Space Administration under the Exoplanet Exploration Program." Akeson, R. L. et al. 2013, PASP 125, 989.
  https://exoplanetarchive.ipac.caltech.edu

ExoFOP (TESS)
  Used for: Lookup of TESS Objects of Interest not yet in the Exoplanet Archive; the ExoFOP package is laid out for its time-series uploads.
  "This research has made use of the Exoplanet Follow-up Observation Program (ExoFOP; DOI: 10.26134/ExoFOP5) website, which is operated by the California Institute of Technology, under contract with the National Aeronautics and Space Administration under the Exoplanet Exploration Program."
  https://exofop.ipac.caltech.edu/tess/

VizieR (CDS)
  Used for: Access to APASS, Tycho-2, Gaia DR3, and VSX.
  "This research has made use of the VizieR catalogue access tool, CDS, Strasbourg Astronomical Observatory, France (DOI : 10.26093/cds/vizier)." Ochsenbein, F. et al. 2000, A&AS 143, 23.
  https://vizier.cds.unistra.fr

METHODS

Lomb-Scargle periodogram
  Used for: Period search.
  Lomb, N. R. 1976, Ap&SS 39, 447; Scargle, J. D. 1982, ApJ 263, 835; VanderPlas, J. T. 2018, ApJS 236, 16.

Gaia G, BP-RP to Johnson V
  Used for: V for Gaia stars when the ESA archive is unavailable.
  Riello, M. et al. 2021, A&A 649, A3.

Gaia G, BP-RP to SDSS g, r, i, z
  Used for: Sloan magnitudes for Gaia stars without synthetic SDSS photometry (no value outside each relation's color range).
  Gaia DR3 documentation, section 5.5.1 (Carrasco, J. M. & Bellazzini, M.), Tables 5.8 and 5.10; Riello, M. et al. 2021, A&A 649, A3.
  https://gea.esac.esa.int/archive/documentation/GDR3/Data_processing/chap_cu5pho/cu5pho_sec_photSystem/cu5pho_ssec_photRelations.html

Gaia DR3 synthetic SDSS photometry
  Used for: Sloan magnitudes of Gaia stars (Gaia Synthetic Photometry Catalogue).
  Gaia Collaboration, Montegriffo, P. et al. 2023, A&A 674, A33.

SDSS to primed Sloan system
  Used for: Puts SDSS-system magnitudes on the primed g', r', i', z' system of APASS and AAVSO sequences.
  Tucker, D. L. et al. 2006, Astron. Nachr. 327, 821.

Sloan limb darkening
  Used for: Quadratic limb-darkening coefficients for transit fits in Sloan filters.
  Claret, A. 2004, A&A 428, 1001 (VizieR J/A+A/428/1001, table 4).

Tycho BT, VT to Johnson B, V
  Used for: Johnson magnitudes for Tycho-2 stars.
  ESA 1997, The Hipparcos and Tycho Catalogues, ESA SP-1200.

HJD and BJD_TDB
  Used for: Heliocentric and barycentric times in exports (computed with Astropy).
  Eastman, J., Siverd, R. & Gaudi, B. S. 2010, PASP 122, 935.

B-V to temperature
  Used for: Approximate temperature from color.
  Ballesteros, F. J. 2012, EPL 97, 34008.

Color transformation
  Used for: Color terms that put the OSC channel colors on the Johnson B-V and V-R scales, fitted to field stars with catalog colors.
  Henden, A. A. & Kaitchuck, R. H. 1982, Astronomical Photometry (Van Nostrand Reinhold).

Luminance weights
  Used for: Clear (CV) channel from an OSC frame.
  ITU-R Recommendation BT.601.

Transit light curve
  Used for: Transit model: exact planet-star overlap areas summed over limb-darkened annuli (any grazing geometry), quadratic limb darkening.
  Mandel, K. & Agol, E. 2002, ApJ 580, L171; Seager, S. & Mallen-Ornelas, G. 2003, ApJ 585, 1038 (durations).

Limb-darkening parametrization
  Used for: q1, q2 sampling that keeps the stellar profile physical; broad priors near V-band values for the star's temperature.
  Kipping, D. M. 2013, MNRAS 435, 2152; Claret, A. 2000, A&A 363, 1081.

MCMC sampler
  Used for: Affine-invariant ensemble sampler (stretch move), written for SHOBS-P, for every transit fit.
  Goodman, J. & Weare, J. 2010, Comm. App. Math. Comp. Sci. 5, 65; Foreman-Mackey, D. et al. 2013, PASP 125, 306.

Red noise (time-averaging beta)
  Used for: Transit error bars scaled for correlated noise.
  Pont, F., Zucker, S. & Queloz, D. 2006, MNRAS 373, 231; Winn, J. N. et al. 2008, ApJ 683, 1076.

Sun and Moon positions
  Used for: Transit planner: darkness, Moon phase and distance (low-precision formulae).
  U.S. Naval Observatory & HM Nautical Almanac Office, The Astronomical Almanac (low-precision formulae).

Swarthmore Transit Finder (Tapir)
  Used for: A button opens it with your site and search limits, to compare with SHOBS-P's own Tonight's transits list.
  Jensen, E. 2013, Tapir: A web interface for transit/eclipse observability, Astrophysics Source Code Library ascl:1306.007.
  https://astro.swarthmore.edu/transits/

EXOTIC and Exoplanet Watch
  Used for: SHOBS-P reads EXOTIC's AAVSO exoplanet reports to compare fits, and writes the same report format (and uses its filter names, e.g. SI for Sloan i'). No EXOTIC code is used.
  Zellem, R. T. et al. 2020, PASP 132, 054401. EXOTIC is developed by Exoplanet Watch, a citizen science project managed by NASA's Jet Propulsion Laboratory.
  https://exoplanets.nasa.gov/exoplanet-watch/

FITS World Coordinate System
  Used for: Plate solutions written in the FITS header (TAN projection), used to place catalog stars without a target.
  Greisen, E. W. & Calabretta, M. R. 2002, A&A 395, 1061; Calabretta, M. R. & Greisen, E. W. 2002, A&A 395, 1077.
  https://fits.gsfc.nasa.gov/fits_wcs.html

SIP distortion convention
  Used for: Optical distortion terms in header plate solutions.
  Shupe, D. L. et al. 2005, ASP Conf. Ser. 347, 491.

Scintillation noise
  Used for: The atmospheric twinkling term added to each point's error bar (2.2.2), from the telescope aperture, airmass, exposure time and site elevation.
  Young, A. T. 1967, AJ 72, 747; Osborn, J. et al. 2015, MNRAS 452, 1707 (modified Young approximation).

CCD equation
  Used for: Each point's photon and sky noise in electrons (camera gain EGAIN, binning, debayer and flat accounted for), including the sky-level error term.
  Merline, W. J. & Howell, S. B. 1995, Experimental Astronomy 6, 163.

False-alarm probability
  Used for: Periodogram FAP; for long periods a night-counting check (permutation test on the nightly means) so a few nights cannot look significant.
  Baluev, R. V. 2008, MNRAS 385, 1279.

Bootstrap resampling
  Used for: Uncertainties and the 'clear winner' test in the aperture test (frames resampled with replacement, the same frames for every aperture).
  Efron, B. 1979, Annals of Statistics 7, 1.

Kwee-van Woerden method
  Used for: Times of maximum and minimum (Variables Output, Time of max/min…): the branches folded about trial times, with its error estimate; checked against a local polynomial with bootstrap errors and a Fourier series at the catalog period, and O−C against VSX elements.
  Kwee, K. K. & van Woerden, H. 1956, Bull. Astron. Inst. Netherlands 12, 327.

Asinh stretch
  Used for: Display stretch on the Blink and Photometry images (asinh option; display only).
  Lupton, R. H., Gunn, J. E. & Szalay, A. S. 1999, AJ 118, 1406.

Median absolute deviation
  Used for: Robust scatter of light curves in the aperture test, comp health, and field scans.
  Hampel, F. R. 1974, JASA 69, 383; Rousseeuw, P. J. & Croux, C. 1993, JASA 88, 1273.

AAVSO Extended File Format
  Used for: Variable-star report files.
  American Association of Variable Star Observers.
  https://www.aavso.org/aavso-extended-file-format
