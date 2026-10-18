# 20. Scan a field

Discovery mode measures every star in the field on every frame, and points out the ones that vary more than stars
of the same brightness. You need no target: any series of frames of one patch of sky, with calibration frames, will do.

This chapter covers:

- setting up a field scan,
- the scan settings (faintest magnitude, detection threshold, candidate level),
- reading the scan table and plots,
- the kinds of candidate and the "check first" warnings.

[21. Follow a candidate](21-follow-a-candidate.md) covers what to do with a candidate next.

> **Tip:** A candidate is a lead, not a discovery. Most are explained by something ordinary: a bright neighbor, a
> bad pixel, saturation, a meridian flip. The scan tells you which to check first.

## Set up the scan

1. Click **Discovery** in the sidebar. The window turns plum.
2. On the Input page, choose the Bias, Dark, Flat and Lights folders and press **Scan folders**, as in any mode (see
   [5. Input](05-input.md)). There is no Target box in Discovery mode.
3. If the lights carry neither a plate solution nor the telescope's pointing in their headers, a box **Field centre
   (only needed when the headers do not say)** appears. Type roughly where the frames point, in **RA (hours)** and
   **Dec (degrees)**. A few arcminutes off is fine. Your planetarium program or mount can tell you.
4. Make sure the **Optics** box (focal length, pixel size, binning) is right: the solve depends on the plate scale.
5. **Blink** the lights and reject the bad ones (see [6. Blink](06-blink.md)).
6. On the **Calibrate** page, press **Build masters and calibrate** (see [7. Calibrate](07-calibrate.md)).

When calibration ends, SHOBS-P opens the Photometry page and solves the field straight away.

## The Photometry page in Discovery mode

The page title is **Photometry: field scan**. The step strip shows **1 Solve → 2 Scan field**.

Once solved, catalog stars are circled and known variables (from VSX and SIMBAD) are marked with red circles or ×.
The **Circles** box defaults to **APASS DR9** in this mode, because an AAVSO sequence covers only charted
variables.

The **Field** box on the right sums up the solve: the plate scale and center, how many catalog stars were placed,
how many stars were detected, how many are saturated, and how many known or suspected variables lie in the frame.

![The Photometry page in Discovery mode after the solve](img/20-discovery-photometry.png)
<!-- SCREENSHOT 20-discovery-photometry: Discovery mode, Photometry page after a successful solve, before scanning; image with catalog circles and red variable marks, the "Click a star to ● Watch it / ✕ Exclude it" tools, the Field and Scan settings boxes on the right visible; whole window. -->

If the solve fails, the step strip says **1 Not solved**. Check the field center on the Input page and the Optics
box, then press **Solve again** (top right). You can scan an unsolved field, but then:

- magnitudes are instrumental, not on a catalog scale,
- there is no check against VSX and SIMBAD, so every candidate is "unchecked",
- a typed **Faintest magnitude** is ignored.

### Apertures

The scan uses the **Aperture r**, **sky in** and **sky out** boxes, like photometry. Press **Suggest…** and then
**Use these aperture sizes** to set them from the field's seeing (see [8. Photometry page basics](08-photometry-basics.md)).
Also check **raw sat ADU**: stars near that level are measured but never listed as candidates.

### Watch or leave out stars

Above the image, **Click a star to** offers two choices:

- **● Watch it**: the star is always measured and listed in the scan (as a "W" row), with its own light curve and
  period search.
- **✕ Exclude it**: the star is left out of the scan, for example a star you know is a ghost or sits on a bad column.

Right-click a marker to undo. Both lists appear in the **Watched and excluded stars** box, where **Remove selected**
also undoes one. **Known variables in the field…** lists the known variables SHOBS-P found in the frame.

## The scan settings

The **Scan settings** box on the right holds:

| Setting | Default | What it does |
|---|---|---|
| **Faintest magnitude** | blank | The faintest catalog magnitude that can be a candidate. Blank: SHOBS-P stops where the field's typical scatter reaches 0.25 mag, and says where in the log. |
| **Detection threshold (σ)** | 5.0 | How far above the sky noise a star must be to be measured. Lower finds fainter stars, and more noise. |
| **Candidate at (× normal)** | 3.0 | How many times more than normal a star must scatter to be a candidate. |
| **Most stars to measure** | 800 | The brightest stars first, up to this number. The log says when the limit is reached; raise it to go fainter. |
| **Leave out crowded stars (neighbor in the sky ring)** | on | Skip stars with another detected star inside their sky ring. |

"Normal" means the typical scatter of stars of the same brightness in this field. A star at **× normal** 3 scatters
three times as much as its neighbors in magnitude. Saturated stars and stars at the edge of the frame are measured
but never listed as candidates.

## Run the scan

1. Press **Scan field** (top right).
2. Wait. The progress line counts the frames. Each frame is calibrated, aligned and every star measured, so a scan
   takes about as long as a photometry run with many stars. **Cancel run** stops it.
3. When the scan ends, SHOBS-P opens the Output step, now the **Output – Field scan** page, and the status bar says
   *"Field scan: N candidate variable(s)."*

Before measuring each star's scatter, SHOBS-P lines its light curve up across any meridian flip or rotator move,
so a flip cannot pass for a variable star.

## Read the Field scan page

The hint under the title sums up the scan: the number of stars and frames, how the magnitudes were calibrated (for
example *"on the catalog V … scale (zero point from n field stars, ± 0.012; untransformed)"*), and how many stars
reached the candidate level. If none did, the ten nearest misses are listed as **~1**, **~2** and so on.

![The Field scan page after a scan](img/20-field-scan-output.png)
<!-- SCREENSHOT 20-field-scan-output: Discovery mode, Output step, Field scan page after a scan with 3–5 candidates (at least one possible new variable and one known); scatter-vs-brightness plot, selected candidate's light curve and periodogram, and the table visible; whole window. -->

### The plots

- **Scatter vs brightness** (left): every star, scatter against magnitude, on a log scale. The blue line is the
  typical scatter. Candidates are red circles with their rank number; known variables are orange squares; saturated
  stars are grey ×. Click a point to see that star.
- **The selected star's light curve** (top right): red for a candidate, blue otherwise. Dashed purple lines mark a
  flip, rotator move or gap.
- **Lomb-Scargle periodogram** (bottom right): the red line is the best period. Periods longer than about half the
  data span are greyed (*"longer than the data can test"*).

### The table

| Column | Meaning |
|---|---|
| **#** | Rank by how much the star exceeds normal. **~1, ~2** are near misses; **W** are watch stars. |
| **Star / position** | SHOBS-P's label (`S012`), the catalog name if any, and RA/Dec when solved. |
| **Mag** | Magnitude on the catalog scale (or instrumental if unsolved). |
| **Scatter** | The star's scatter in magnitudes, robust against outliers. |
| **× normal** | Scatter divided by the typical scatter at that magnitude. |
| **Best period (d)** | The best Lomb-Scargle period. **(edge)** means it sits at the end of the search range. |
| **B−V** | Color, when measured. |
| **Check first** | Reasons to doubt the candidate (below). |
| **VSX** | **KNOWN (VSX): name type**, or **not in VSX or SIMBAD — possible new variable**. |

Click a row to draw that star. You can select several rows.

> **Tip:** The period search range comes from the **Period search (days)** boxes on the Variables Output page
> (0.05 to 20 days by default). To change it for a scan, switch to Variables mode, change the range on the Output
> page, and switch back.

### Check first

The **Check first** column lists warnings that often explain a candidate away:

- **near saturation: check linearity**: the star's peak came close to saturation in some frames.
- **change begins at flip: check** / **change begins after a gap: check**: the light curve changes level at a flip,
  rotator move or gap, which points at the instrument rather than the star.
- **noise begins at flip: check** / **noise begins after a gap: check**: the same for a jump in noise.

### The candidate kinds

**Mark candidates on image** switches to the Photometry page and draws numbered squares on the candidates. With rows
selected in the table, it marks only those (**Mark N selected on image**). The style of each square says its kind;
hover over it for the reason:

- **Possible new variable** (bright magenta): not in VSX or SIMBAD, and nothing to check first.
- **Known variable (VSX/SIMBAD)** (pale purple).
- **Candidate to check first** (tan): it has a **Check first** warning.
- **Unchecked (no sky positions)** (grey): the field was not solved.

Tick **Candidates only** in the **View** box to hide everything else on the image. Press the button again (now
**Clear candidate marks**) to remove the marks.

![Candidate marks on the image](img/20-candidate-marks.png)
<!-- SCREENSHOT 20-candidate-marks: Discovery mode, Photometry page after "Mark candidates on image" with "Candidates only" ticked; numbered squares of at least two kinds, the legend row under the image, and a hover tooltip on one candidate; crop to the image and legend. -->

### Known variables

- **Known variables measured…** lists every VSX and SIMBAD variable in the frame and what the scan saw for each, in
  the column **In this scan**: **VARIED: a scan candidate**, **steady tonight, within the noise**, **somewhat more
  scatter than normal**, **measured on too few frames**, **saturated: clipped, so not usable tonight**, or **not
  measured (faint, saturated, blended, or not detected)**. It is a quick way to see whether your setup catches the
  variables you know are there.
- **Hide known variables** removes known variables from the table and plot, so the new ones stand out. Their rank
  numbers are kept, so the table may jump from #1 to #3, matching the marks on the image.

## Save the results

The **Results** row holds:

- **Save plot PNG…**: the three plots with a one-line summary, `<field>_<filter>_<date>_SHOBS-P_scan.png`
  (`<field>` is the Input page's Star ID if one is left from another mode, otherwise `field`).
- **Save CSV…**: one row per measured star (position, magnitude, scatter, expected scatter, excess, candidate rank
  and kind, period, VSX name).
- **Export candidates CSV…**: one row per candidate, with color and VSX period, `field_scan_candidates.csv`.
- **Export all light curves CSV…**: every star's light curve, one row per star per frame,
  `field_scan_lightcurves.csv`.
- **Back** returns to the Photometry page.

See [21. Follow a candidate](21-follow-a-candidate.md) for what to do with them.

---

Previous: [19. Reports](19-transit-reports.md) · Next: [21. Follow a candidate](21-follow-a-candidate.md)
