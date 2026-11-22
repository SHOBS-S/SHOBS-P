# 21. Follow a suspect

A field scan gives you leads. One night is never enough to call a star variable: you want to see it change again, on
another night, with the same behavior. This chapter covers:

- deciding which suspects are worth following,
- putting them on the watch list,
- measuring them on more nights,
- exporting their light curves, for your own analysis or a VSX submission.

## Decide which suspects are worth following

Work down the scan table (see [20. Scan a field](20-scan-a-field.md)) and, for each suspect:

1. Read **Check first**. A suspect whose change begins at a flip or a gap, that is near saturation or clipped, or
   that has a blended neighbor, is most likely instrumental. Follow it only if its light curve also changes well away from the flip or gap.
2. Read **VSX**. **KNOWN (VSX): …** is already cataloged. It may still be worth following if the catalog lacks a
   period or type, but it is not a new variable. Tick **Hide known variables** to take them out of the table, the
   plot and the image while you work through the new ones.
3. Look at its light curve (click the row). A real variable usually changes smoothly, or repeats; a single-frame
   spike is usually a cosmic ray, a satellite or a hot pixel. A **Best period** of **no period (drift)** means the
   periodogram's best peak sat at the long end of the search: the star drifts slowly, which may be the star or may be
   the instrument. Click the row again (or press **Esc**) to clear the selection.
4. Click **Mark suspects on image** and look at the star in the image. A bright neighbor, a diffraction spike,
   a column defect or the frame edge can each make a star "vary".
5. Look at **× normal** together with **Scatter**. A faint star at 3× normal may scatter 0.1 mag from noise alone; a
   bright star at 3× normal with 0.02 mag scatter is a cleaner lead.
6. Look at **Suspect by** and **χ²ν × normal**. A star that is a suspect by its **own errors** only scatters
   modestly but far more than its error bars allow: a low-amplitude variable (S033 was one), or something its error
   bars do not cover, such as a neighbor in the sky ring. **Nights** says **Suspect again (N nights)** when it was a
   suspect on other nights of this field too: a much stronger lead.

### Pick stars on the scatter plot

Not every interesting star reaches a suspect level. On the **Scatter vs brightness** plot you can pick any
measured star:

- **Click** a point to pick that star (and draw its light curve); click it again to clear.
- **Ctrl+click** adds a star to the picked set, or takes it out.
- **Drag** a box to pick every star inside it.

Picked stars are ringed on the plot. Press **Mark on image** to mark them on the Photometry image: suspects keep
their rank number, other stars are labeled with SHOBS-P's label (for example `S066`) and drawn as **Picked on the scan
plot**. From there you can look at each one, and watch it (below). Press **Add as suspect** to make the picked stars
suspects by hand (chapter 20): they are kept in the night's suspects file with the others.

## Put a suspect on the watch list

Watch stars are measured on every later run, with their own light curves. There are three ways to add one:

- On the **Output – Field scan** page, click the suspect's row (or its point on the scatter plot) and press **Watch
  selected star**. The status bar says *"Watching …"*.
- In **Known variables measured…**, select a known variable and press **Watch selected**. This works even for one the
  scan could not measure (it is watched by its sky position).
- On the Photometry page, with **● Watch it** chosen, click the star in the image.

A watch star is remembered by its catalog name and sky position when the field is solved. A star that is not a
labeled catalog star on an unsolved image is remembered only by its pixel position; the status bar warns *"It is
not a labeled catalog star, so click it again on later nights."*


## Measure another night

### Night after night in Discovery mode

1. On the Input page, change the **Lights** folder (and the calibration folders, if they differ) to the next night's
   frames and press **Scan folders**.
2. Blink and calibrate as before. The field is solved again, and the watch stars are found by name and sky position.
   The log says how many were found, for example *"Watch stars: 2 of 3 found on this frame"*.
3. Press **Scan field**. Watch stars appear in the table as **W** rows (with their periodogram), whether or not they
   reach a suspect level that night.

Compare the two nights. A star that is a suspect again, with similar behavior, is a much stronger lead: the
**Nights** column says **Suspect again (N nights)** when the field's other nights have a suspects file (chapter 20).

Discovery watch stars are remembered between sessions, with their sky positions. After the next solve, the ones
that fall on the new frame are watched again, and the log says, for example, *"Watch stars remembered for this field
(2): …"*. A star clicked on an unsolved frame has no sky position, so it cannot be found again and is not remembered.
Right-click a watch star (or **Remove selected**) to stop watching it.

> **Tip:** Discovery keeps the watch list, but not each night's measurements. To keep a suspect's history across
> nights, follow it in Variables mode with a series, as below.

### Over many nights (Variables mode)

A series in Variables mode keeps the watch list and every night's measurements in one file. There are two ways to
use it.

**The suspect as the target** (best when one suspect matters most):

1. Switch to **Variables** mode. Marked stars and calibrated frames are kept.
2. On the Input page, type the suspect's catalog name in **Star ID** (the name in brackets in the scan table), or
   type its **RA (hours)** and **Dec (degrees)**.
3. On the Photometry page mark it as the **Target**, choose comparison stars and a check star, and press **Run
   photometry**.
4. **Save series** on the Output page. On each new night, **Load series…** first, then reduce the night and answer
   **Yes** to add it.

You get the full Variables-mode tools: the period search with its significance tests, the comparison-star health
check, Time of max/min and an AAVSO report. See [9. Reduce a variable-star night](09-reduce-a-variable-night.md),
[10. Build a multi-night series](10-build-a-multi-night-series.md) and [13. Look for a period](13-look-for-a-period.md).

**The suspect as a watch star** (best for several suspects in one field):

1. In Variables mode, pick any good star in the field as the target, with comps and a check star.
2. Make sure the suspects are on the watch list (they stay on it when you switch modes in the same session).
3. Run photometry and save the series. The watch list is saved with it.
4. On later nights, **Load series…** before reducing; the watch stars are found again after the solve and measured
   on every run.
5. On the Output page, click **Watch stars…** to see each watch star's light curve across all nights, measured
   against the comparison stars in use.

![The Watch stars window](img/21-watch-stars-window.png)

### Reach back over frames already on disk

A star you start watching tonight has no values on the series' earlier nights, although its light is on every one
of those frames. **Reach back…** in the Watch stars window (from 2.4.0) measures the selected watch star on the
series' frames that do not have it yet:

1. Select the star and press **Reach back…**. SHOBS-P says how many frames on how many nights it will measure, and
   about how long it takes (a little over a second a frame for full-size frames).
2. Each frame is calibrated with **that night's own flat** (the *Flats* folder beside the night's *Lights* folder)
   and the **bias and dark on the Input page**, as saved SHOBS-P masters are reused. A night with no flat folder is
   measured without a flat, and the log says so with a ⚠: those values carry the flat-field pattern (a few percent,
   different on each side of a flip).
3. The star is placed on each frame from the frame's own recorded star positions (target, comps, check, spares and
   other watch stars, mapped from their sky positions), so flips and drift are followed without solving each frame.
4. Each value is **tied to the frame's stored comp values**: the comps are measured the same way on the same image,
   and the star takes the frame's own instrumental scale. A different calibration or centering cancels out.
5. A few frames per night that already have the star are measured again as a check. The log line gives the
   difference, for example *Check on 24 frame(s) that had it: +0.0 mmag (rms 0.4)*; more than 10 mmag gets a ⚠.
6. **Save series** keeps the new values (the series line shows unsaved changes until you do).

The star needs a sky position (watched on a solved image); a star watched by its pixel position cannot be placed on
other nights. Frames no longer on disk are skipped and counted.

## From Watch to Candidate to Campaign

A watch star moves up one gate at a time. **Suspect** (one night, automatic or by hand) → **Watch** (your click) →
**Candidate** (automatic, once its light curve passes the Candidate gates) → an outcome: **Confirmed new**, **Known**
or **Rejected**. **Campaign** is not a stage but a switch: on, the star gets its own nights in SHOBS-SCHED; off, it
rides along on the field's frames again. A star's outcome and its Campaign switch are independent.

On the Variables Output page, click **Candidates…**. After each night you add with **Save series**, SHOBS-P runs the
promotion gates on every watch star of the series (and on the target when you have asked for it with **Check the
target too**). The window lists each star with its **Registry** status, best period, amplitude S/N, how many
Candidate gates and how many of all the gates pass, and its quiet nights. Select a star to see each gate, what was
measured and what it needs.

![The Candidates and campaigns window](img/21-candidates-window.png)

Every period test runs after each night's median is taken out, so a period up to about one night can be judged;
slower changes need a longer track (Time of max/min, the nightly means). The gates:

| Gate | Candidate needs | Why |
|---|---|---|
| Nights and span | 3 nights or more, 10 cycles observed, a span of 3 days or 3 periods (whichever is longer), and two nights in a row | cycles and sampling prove a period, not the calendar; two nights in a row break the one-day aliases |
| Cycles and sampling | 5 cycles; frames closer than a quarter period | |
| Single-star FAP | under 1e-3 | |
| Look-elsewhere | the FAP times the number of watch stars, under 1e-2 | searching many stars is many tries |
| Amplitude S/N | 4 or more, after the red-noise factor β | real nights have correlated noise |
| Daily aliases | not within 5 % of 1, 1/2 or 1/3 day, unless one night (1.25 cycles long or more) finds the period by itself | |

The **Campaign** switch lights when every gate passes, these and the rest: 5 nights over 14 days and 20 cycles
(longer periods: 5 periods); at least two nights find the period alone; the best peak at least 1.5 times its
±1 cycle/day alias; no single night carries the period (leave-one-night-out); the nights agree in phase (±0.15
cycle on 70 % of them); the period and amplitude stay within 10 % with the two flip sides fitted separately; the
look-elsewhere FAP under 1e-4; every other star of the frames (comps, check, spares, other watch stars) quiet at the
period (**null tests**); no correlation of |r| 0.3 or more with airmass, FWHM, sky or the star's x/y position, and
none of those with strong, significant power at the period; every frame under the linear limit (the **linear ADU**
on the Photometry page, SHOBS-FLUX's number when it came from there; the raw saturation level only when no linear
ADU is set), and never at or above **raw sat ADU** (a star that saturates on any frame fails even when the linear
ADU is higher, and so does a frame SHOBS-P itself flagged saturated); the star clear of
the frame edges on 95 % of frames. Nights whose check star scatters more than 1.5 times its typical night are left out
before any gate runs (the quiet-nights strip shows them as *skipped*).

The Linear limit gate reads *not recorded* when the series holds no raw peaks for the star: pre-calibrated lights,
nights run before 2.2.19, and Watch stars on nights run before 2.4.0 (run those nights again to record them). When
a new SHOBS-P changes how a gate judges, the stored results count as out of date: opening **Candidates…** or
saving the series runs the gates again, and the Registry treats the new verdict like new data.

**Quiet nights** (the chips above the gate list) give each night's χ²ν: against the star's own errors for a target,
against its point-to-point scatter for a watch star. *quiet* (2 or less), *active* (3 or more). A quiet night shows the
star's noise is understood, so its active nights are real; one active night among quiet ones can be an eclipse a
period search misses. The strip is evidence only, not a gate.

**The Registry** (`campaign_registry.json` in the SHOBS data folder, one per observatory) holds every Candidate and
every star with an outcome, keyed on its Gaia DR3 source ID (or its position until it has one). Suspects and plain
watch stars are never in it. A watch star the catalogs already list as variable enters it as **Known**.

- **Promote to campaign** (lit when every gate passes, or any time once the star is Confirmed new or Known) switches
  the campaign on. **End campaign** switches it off; the record and outcome stay.
- **Outcome…** sets Confirmed new, Known, Rejected (with a reason: `alias_1d`, `single_night`, `comp_signal`, `flip`,
  `saturation`, `edge`, `cloud_corr`, `airmass_corr`, `duplicate_of`, or a note), or back to Candidate.
- A Campaign star that fails a hard gate (null tests, flip sides, linear limit, edges, correlations, daily aliases,
  leave-one-night-out) on new data (other nights or points than the last gate run) has its campaign switched off and stays a Candidate; a second failure in a row
  makes it Rejected. SHOBS-P tells you in a message box.
- **Export for SHOBS-SCHED…** writes the Campaign stars (campaign on, not Rejected) as SHOBS-SCHED target rows
  (kind campaign, mode V, with the period) to a file you choose. SHOBS-P never writes SHOBS-SCHED's own files.

## Check the neighbors

A star that varies may be borrowing its signal: from a neighbor whose light falls in its aperture, from a variable
comparison star, or from something every star of that brightness shows (the sky, the detector). **Check neighbors…**
(from 2.4.0) puts the star's light curve beside its neighbors' on one shared scale. Open it from:

- the **Watch stars** window or the **Candidates** window (a watch star, or the target): every night of the series;
- the **Field scan** page (a suspect): that one night, with every star the scan measured.

![The Check neighbors window](img/21-check-neighbors.png)

The rows, in groups:

- **Target**: the star itself, with its amplitude at its period P.
- **Close companions**: any star inside the aperture plus the sky ring. Its light is in the star's measurement.
- **Look-alikes (±0.5 mag)**: stars of about the same brightness, nearest first: the series' own stars (comps,
  spares, other watch stars) and up to six from the catalogs (Gaia DR3, APASS) that sit on the frames. A star that
  shares the star's brightness shares its noise and its systematics.
- **Comps and check** (untick **Show comps and check** to hide them).

Each row ends with **Power at P** (the star's periodogram power at P as a share of the target's own) and **Amp at
P**, then **✓ Quiet** or **⚠ Signal at P**. A row is quiet when its power is under half the target's and its
amplitude under a third of the target's (or 2σ). These are the gate runner's own null tests, the same code, so the
window and the **Null tests** gate always agree. The bottom line sums it up: *Neighbors quiet at P: 7 of 7
measured*. There is no correlation column: every star of the frames correlates a little with the target (r about
0.15–0.3) through the shared comps and airmass, which would mislead.

**View**: **Folded at P** (all nights, 40 phase bins) or **Time (last night)** (6-minute bins). Type another
look-alike range in **Look-alikes ±** and press Enter to search again.

**Measure look-alikes**: catalog look-alikes are not in the series, so they show *not measured yet*. The button
measures them on every frame of the series still on disk, the same way as reach-back (each night's flat, tied to
the comps). They are kept in a file beside the series, `<series>_neighbors.json`, so the series itself and **Use
comps…** are untouched, and the next time the window opens they are there.

**Run blend test** (when a star sits inside the aperture plus sky ring): the star is measured three times on every
frame, with the aperture centered, moved toward the companion and moved away from it (by half the aperture radius,
or half the separation). The true variable's amplitude grows as the aperture moves onto it: *the amplitude does not
grow toward the companion … the signal sits on the star*, or *… grows toward the companion: the signal may belong to
the companion*. With no star that close, the window says no blend test is needed.

**Save to star record** keeps the result with the watch star (in the series), in the neighbors file, and in the
star's Registry record when it has one; for a suspect, in the night's suspects file.

## Outside data (ZTF, TESS, ATLAS)

From one site a short-period star's best peak and its ±1 cycle/day alias can be nearly equal, and no number of your
own nights settles that quickly. When a star becomes a **Candidate**, SHOBS-P asks three surveys about its position,
once, in the background (from 2.4.0):

- **ZTF** (IRSA light curves, within 2″): each filter searched over the same period range as the gates;
- **TESS** (the TIC star within 5″, the sectors that covered it, the Quick-Look Pipeline light curves): one search
  over all sectors. TESS pixels are 21″, so TESS confirms the period, not which star has it (Check neighbors does);
- **ATLAS** (the ATLAS variable-star catalog): its periods and class.

A survey that finds the star's period within 1 % **clears the ±1 c/d alias gate and the time-span gates**; the
gate list shows *✓ ZTF and TESS* instead of *✓ pass*. Outside data never clears a null test or a systematics guard:
those must pass on your own data. A survey that finds the ±1 c/d alias instead is named in the summary: that is a
reason to doubt your period. **Outside data…** in the Candidates window runs the check again (or for a star that is
not a Candidate yet) and shows each survey's answer. A survey that does not answer is skipped for now (*did not
answer*); the others still count.

## Worked example: S033

S033 (APASS J024812.2+505255, Gaia DR3 440531844915790208) went the whole way in November 2026.

1. **Suspect** (13–14 Nov, GSC 03308-00462 field scan): 2.2× normal scatter, under the 3× flag, but χ²ν 2.7 times
   that of stars of its brightness: a suspect by its own errors.
2. **Watch**: watched from the scan page; the series reached back to 8 Nov over the field's frames on disk
   (6 nights, 583 usable points of 624).
3. **Candidate**: **Candidates…** → **Check the target too**: P 0.10279 d, A 10.4 mmag, A/σA 23.3 (10.7 after β
   2.2), 13 cycles over 6.2 days with two runs of nights in a row, 4 of 6 single-night detections, phase coherence 5
   of 5, all its null stars quiet, peak 18 % of the 60,931 ADU linear limit. Short of a campaign on the data volume
   (6.2 days, 13 cycles) and the ±1 c/d alias (1.15 times, needs 1.5).
4. **Check neighbors**: the nearest neighbor (APASS J024814.5+505307, 26″ away, ΔV +0.30, also a watch star)
   0.3 % of the power at P and 0.4 mmag; the two look-alike comps, the other comps and the check all quiet: *Neighbors
   quiet at P: 7 of 7 measured*. No star inside the aperture plus sky ring (19″), so no blend test.
5. **Outside data**: ZTF finds 0.102970 d (g and r) and TESS 0.102970 d (sectors 18, 58, 85): the ±1 c/d alias gate
   and the data volume read *✓ ZTF and TESS*. ATLAS lists the star as *dubious* at 0.249 d (not counted).
6. **Outcome**: Confirmed new (submitted to VSX on 15 Nov 2026 as a δ Scuti star, P 0.1029705 d). **Promote to
   campaign** is then lit any time, to build data under its new name.

## Export the light curves

| What you want | Where | File |
|---|---|---|
| One row per measured star, with scatter, excess and period | **Save CSV…** on the Field scan page | `<field>_<filter>_<date>_SHOBS-P_scan.csv` |
| One row per suspect, ready to start a VSX new-variable submission | **Export suspects CSV…** | `field_scan_suspects.csv` |
| The night's suspects, kept for **Suspect again** on later nights | written by the scan (and by **Add as suspect**) | `<field>_<filter>_<date>_SHOBS-P_suspects.json` |
| Every star's light curve from one scan (label, JD, magnitude) | **Export all light curves CSV…** | `field_scan_lightcurves.csv` |
| Watch stars' light curves across a series | **Export watch light curves CSV…** in the Watch stars window | `watch_stars.csv` |
| The target's full light curve | **Save light-curve CSV** or **Export…** on the Variables Output page | see chapter 22 |
| The Campaign stars, for SHOBS-SCHED to import | **Export for SHOBS-SCHED…** in the Candidates window | `SHOBS-P_campaigns_for_SHOBS-SCHED.json` |
| Look-alikes measured by Check neighbors, its saved results and blend tests | **Measure look-alikes**, **Run blend test**, **Save to star record** | `<series>_neighbors.json` beside the series |

Scan magnitudes are on the catalog's scale (untransformed) when the field was solved, and instrumental otherwise.
Every CSV starts with a `# Created …` line.

## Before you report a new variable

- See it vary on at least two nights, ideally with the same period or the same kind of change.
- Rule out a neighbor: **Check neighbors…** (and its blend test when a star is close), and look at the image at
  full zoom.
- Check the comparison stars: a variable comp makes every other star look variable in the opposite sense.
- Check that the star is not already cataloged under another name (SIMBAD, VSX search by position).
- Follow the VSX submission guidelines. The suspects CSV gives position, magnitude, scatter, period and color as a
  starting point.

---

Previous: [20. Scan a field](20-scan-a-field.md) · Next: [22. Files SHOBS-P writes](22-files-shobs-p-writes.md)
