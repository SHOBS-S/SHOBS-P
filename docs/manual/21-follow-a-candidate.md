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
ADU is set); the star clear of
the frame edges on 95 % of frames. Nights whose check star scatters more than 1.5 times its typical night are left out
before any gate runs (the quiet-nights strip shows them as *skipped*).

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

Worked example: S033 (APASS J024812.2+505255), measured from its saved series of 8–14 Nov 2026 (583 points, 6
nights): P 0.10279 d, A 10.4 mmag, A/σA 23.3 (10.7 after β 2.2), 13 cycles over 6.2 days with two runs of nights in a
row, 4 of 6 single-night detections, phase coherence 5 of 5, all 14 null stars quiet, peak 27 % of the 40,000 ADU
linear limit. It is a **Candidate**, short of a campaign on the data volume (6.2 days, 13 cycles) and the ±1 cycle/day
alias (1.15 times, needs 1.5). ZTF and TESS data settled the alias by hand on 15 Nov; SHOBS-P's own outside-data
check (ZTF, TESS, ATLAS), which may clear that gate and the data volume, comes in a later 2.4.0 step.

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

Scan magnitudes are on the catalog's scale (untransformed) when the field was solved, and instrumental otherwise.
Every CSV starts with a `# Created …` line.

## Before you report a new variable

- See it vary on at least two nights, ideally with the same period or the same kind of change.
- Rule out a neighbor: look at the image at full zoom, and try a smaller aperture.
- Check the comparison stars: a variable comp makes every other star look variable in the opposite sense.
- Check that the star is not already cataloged under another name (SIMBAD, VSX search by position).
- Follow the VSX submission guidelines. The suspects CSV gives position, magnitude, scatter, period and color as a
  starting point.

---

Previous: [20. Scan a field](20-scan-a-field.md) · Next: [22. Files SHOBS-P writes](22-files-shobs-p-writes.md)
