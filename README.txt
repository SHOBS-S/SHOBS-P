Shiloh Hill Observatory – Photometry (SHOBS-P)  2.2.7
=====================================================

Windows desktop app for color-camera (OSC) photometry: variable-star light curves, AAVSO reports, and a field scan
that finds stars that vary.

Process flow
1. Input: folders of bias, dark, flat, and light FITS, and an optional Output folder; the target (type the Star ID
   and press Enter: SIMBAD, then VSX, fill RA/Dec); AAVSO observer code and chart; site and optics. (Comparison and check stars are chosen on the
   Photometry page.)
2. Blink: step through raw lights (or flats) and reject trails, clouds, and bad guiding. Rejected frames stay on disk.
3. Calibrate: master bias, exposure-scaled master dark, normalized master flat. Each light becomes
   (light - bias - dark) / flat. The first light is then debayered (superpixel; the channel is chosen on this page:
   green is the default, AAVSO TG with V comps; red is TR, blue is TB, luminance is CV; MONO for monochrome cameras)
   and the app goes straight to Photometry.
4. Photometry: mark the target, one to ten comparison stars (C1-C10), and the check star. "Label chart" circles
   catalog stars with their magnitudes; with Comps or Check selected, clicking a circled star fills in its ID and
   magnitude.
   Catalogs: AAVSO sequence (preferred for AAVSO reports; sets the chart ID), Gaia DR3 (almost every star; Johnson V,
   B, R), APASS DR9 (B and V, about V 10 to 17), Tycho-2 (bright stars). Comps from Gaia, APASS, or Tycho-2 set
   Chart ID to na. Every frame is aligned to the marked frame (shift, rotation, meridian flip).
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
     grey: unchecked (no sky positions, so no catalog check). Hover over a mark for the reason.
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
     calibrated (_c) are recognised: calibrated flats are used as they are, without subtracting bias again. A folder
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
- M  With no candidates, the ten nearest misses are listed (~1, ~2 …). Saturated stars are grey × in the scatter
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
- M  No Target block on the Input page; a Field centre box appears only when the lights' headers neither carry a
     plate solution nor say where the telescope pointed.
- M  Photometry page: no Target / Comp / Check buttons. The field is solved as soon as calibration finishes (from the
     header's plate solution when there is one); catalog stars are circled and known variables marked. The panel
     shows the field (scale, centre, stars, magnitude range, saturated stars, known variables) and the scan settings
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
- M  On a single night shorter than two cycles of the best period, the fold and the periodogram are greyed out with
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
  again on later nights after Label chart.
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
- Variable-star check: Label chart also asks AAVSO VSX and SIMBAD (one query per field) for known and suspected
  variables. They are marked with a red × on the image, labeled stars on them get red circles, and hovering says
  what they are (for example "SIMBAD LP? long-period variable candidate"). Clicking one as a comp or check asks
  first. Flagged stars are never picked as spare comps or used in the color fit.
- Watch stars: pick Watch and click a star to follow it. It is measured every frame, found again on later nights after
  Label chart, and has its own light curve (Watch light curves…, also on the Photometry page). Spares in Comp health
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
- Label chart logs which service answered (e.g. "VizieR I/355 (ESA archive unavailable; V from G only)") and how many
  labeled stars have B, V, and R. If "Also measure R and B" is ticked but B-V cannot be calibrated, the app says so
  before the run starts.
- Cancel buttons for calibration, photometry, and the field scan. A cancelled run adds nothing.
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
  the log. Master files are always skipped. If most of the folder is labelled bias/dark/flat, the capture software
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
  press Apply; every night in the series is re-flagged at once. Flagged points show as grey x marks and are left
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
  with its light curve and periodogram. After Label chart, each star gets RA/Dec and is checked against VSX:
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
"Write demo FITS set" (Input page) builds a small synthetic night with a 0.12 d variable and fills in every field.
Calibrate, apply the debayer, then Run photometry.

Notes
Binning 2 keeps ASI2600-size frames quick (1.7 and later bin same-color pixels only). Coordinates are on the
binned, debayered image. If you change binning, rebuild the masters. "raw sat ADU" is checked on the raw frame. Chart labeling needs the right focal length and pixel size.
A single night cannot pin down a long period; the Output page warns under 1.5 cycles.

History
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
       star-name spelling, greyed period panels on short single nights.
2.1.8  Tonight's transits: every known transiting planet observable whole on a night (dark, altitude,
       baseline, V range, depth, Moon, meridian, and an After / End by window); double-click to use it. Same search
       on the Swarthmore Transit Finder with one button.
2.1.7  Field solving: Label chart works without a target, from the FITS header pointing (or a typed field
       center), by matching star triangles; it also rescues a failed target-anchored match. Saturated stars are kept
       out of field-scan candidates. Candidate marks can be cleared; stale variable-check text clears with new folders.
2.1.6  Transit verdict (no transit measured: flat line about as good, ΔBIC < 10, or Tc unpinned), greyed
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
  Used for: Comparison stars, Johnson B and V, field stars for B-V calibration.
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
  Used for: SHOBS-P reads EXOTIC's AAVSO exoplanet reports to compare fits, and writes the same report format. No EXOTIC code is used.
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

Bootstrap resampling
  Used for: Uncertainties and the 'clear winner' test in the aperture test (frames resampled with replacement, the same frames for every aperture).
  Efron, B. 1979, Annals of Statistics 7, 1.

Median absolute deviation
  Used for: Robust scatter of light curves in the aperture test, comp health, and field scans.
  Hampel, F. R. 1974, JASA 69, 383; Rousseeuw, P. J. & Croux, C. 1993, JASA 88, 1273.

AAVSO Extended File Format
  Used for: Variable-star report files.
  American Association of Variable Star Observers.
  https://www.aavso.org/aavso-extended-file-format
