# 22. Files SHOBS-P writes

SHOBS-P never changes your frames. It writes only the files listed here: saved masters next to your calibration
frames, the results you save, series files, and a few small files in your user folder.

## Where results go

The Input page's **Output** folder decides where results go:

- **With an Output folder**, every Save button writes there without asking. The status bar says what was saved
  where, for example *"Saved AAVSO_HD_219134_TG_05-OCT-2026_SHOBS-P.txt to Results."*
- **Without one**, each Save opens a dialog with a suggested name, and a message confirms where the file went. The
  dialog opens in the folder you last saved a result to in this session, with the next free name (and number) filled
  in. A name typed with a number after its extension (`…_SHOBS-P.txt.2`) is tidied to `…_SHOBS-P_2.txt`; if that name
  is taken, the next free one is used and the status bar says so.
- **Nothing is overwritten.** If a file of that name exists, the new one gets `_2`, `_3` and so on. One exception
  (from 2.3.2): a transit fit's corner plot saved again for the same fit, this session, replaces that fit's own
  corner plot instead of adding a copy.
- **Matching numbers.** The report, light-curve CSV and plot PNG of one set of results share one number, so they
  sort together (for example `…_SHOBS-P_2.txt`, `…_SHOBS-P_lightcurve_2.csv`, `…_SHOBS-P_lightcurve_2.png`). The files
  saved beside them (corner plot, settings file, field image, AAVSO chart) keep that number too, and a number counts
  as taken when any of them exists. A new result starts a new number.
- **The Output folder is not remembered** between sessions. Set it again each time you start SHOBS-P. Series files
  are the exception: they always ask where to go, and save back to their own file after that.

### Names

Most result files follow one pattern:

`<star>_<filter>_<date>_SHOBS-P…`

- `<star>`: the Star ID with spaces turned into `_` (the planet's name, without spaces, for transit reports).
- `<filter>`: the AAVSO filter code, such as `TG`, `V`, `CV` or `SI`.
- `<date>`: the UTC date of the first point, as `DD-MON-YYYY` (for example `05-OCT-2026`). A save covering several
  UTC dates gives the first and last: `16-SEP-2026_to_06-OCT-2026`.

## The "Created" line

Every text file SHOBS-P writes (reports, CSVs, fit notes) carries a line such as:

`# Created 121153Z OCT 26 by SHOBS-P`

The time is a date-time group in UTC: day 12, 11:53 Z, October 2026. Plot PNGs carry *Created … · SHOBS-P 2.3.0*
in the picture. The line tells you, and anyone you send the file to, when it was made and with which program.

## Result files by mode

### Variables mode (Output – Variability page)

| Button | File |
|---|---|
| **Save AAVSO report** | `AAVSO_<star>_<filter>_<date>_SHOBS-P.txt`: AAVSO Extended File Format, ready for WebObs. Beside it, `AAVSO_<star>_<filter>_<date>_SHOBS-P_settings.txt`: what the reported points were measured with, night by night (aperture and sky radii, binning, comps and check measured, frames skipped and left out, calibration, gain (from 2.4.0 with how it was measured from the night's sky, or the night's sky check of EGAIN, and the SHOBS-FLUX camera profile check when there is a profile), telescope aperture, stacking, the SHOBS-P version), and the report's comps, check, scintillation and line-up choices. |
| **Save light-curve CSV** | `<star>_<filter>_<date>_SHOBS-P_lightcurve.csv`: every point, flagged ones included with their flag. The `segment` column says `no flip`, `before flip` or `after flip`; on a night with a re-center (from 2.4.0, chapter 12) it goes on with ` / before re-center`, ` / after re-center` or ` / after re-center 2`. The words name the segment: frames that came back to the first position read ` / before re-center` again. |
| **Save plot PNG** | `<star>_<filter>_<date>_SHOBS-P_lightcurve.png`: the four plots with the summary above them. |
| **Save field image** (and with every **Save AAVSO report**) | `<star>_<filter>_<date>_SHOBS-P_field.png`: the reference frame as shot, with N and E arrows showing where north and east lie, and the target, comps and check star marked; and, when the AAVSO's chart server answers, `…_aavso_chart.png` (sometimes `.jpg` or `.svg`, as VSP sends it) of the same field. The field image comes with the report: after the report is saved the button reads **Field image saved ✓** (no second `…_2_field.png`), or **Fetch AAVSO chart** when only the chart is missing; that saves just the chart beside the image (from 2.4.0). |
| **Export…** | `<star>_<time>_<mag or flux>.csv`: the columns you choose; its header records every choice. |
| **Time of max/min…** → **Save to times CSV** | `<star>_times_of_extremum.csv`, next to the series file. Each new time is added; a re-measured one replaces the old row. Columns: below. |
| **Watch stars…** → **Export watch light curves CSV…** | `watch_stars.csv` |

When a night is highlighted in the night table, **Save AAVSO report** and **Save light-curve CSV** first ask whether
to save that night only or the whole series. See [15. Submit to AAVSO](15-submit-to-aavso.md).

**The times CSV's columns** (`<star>_times_of_extremum.csv`, one row per time of maximum or minimum):

| Columns | What they hold |
|---|---|
| `star`, `type`, `night` | The star, `max` or `min`, and the night. |
| `hjd`, `bjd_tdb`, `jd_utc` | The adopted time in HJD (UTC), BJD_TDB and JD (UTC). `hjd` and `bjd_tdb` are blank without RA/Dec. |
| `error_d`, `error_min`, `method` | The adopted error in days and minutes, and the method (polynomial and bootstrap). |
| `cycle`, `o_c_d`, `o_c_min`, `period_d`, `epoch`, `elements` | The O − C and the elements it was worked out against, with their source (`VSX HJD`, `saved HJD`, `typed HJD`). Blank when no elements were given. |
| `n_points`, `window_min` | Points in the fit window, and its half-width in minutes. |
| `kvw_jd`, `kvw_err`, `poly_jd`, `poly_err`, `fourier_jd`, `fourier_err` | Each method's own time (JD) and error (days). |
| `time_note`, `note` | Notes on the time conversion, and the result's ⚠ warnings. |
| `noise_period_d`, `noise_period_source` | **New in 2.4.0.** The period the error's noise model used, and where it came from: `series` (the Output page's period), `times CSV`, `VSX`, `Period box`, or `none` when the time was measured without a period (its error may be 2–3 times too small). |
| `noise_period_used_d` | **New in 2.4.0.** The period the noise model was really fitted at: `noise_period_d` after SHOBS-P fitted it to the night (a night of two cycles or more, chapter 14), or the same number when it was not fitted. Blank when there was no period. |

The three new columns are added at the end, so the older columns keep their places. A CSV from 2.3 still reads; the
first time 2.4.0 saves into it, the header gains the new columns and the older rows have them blank. A blank is not
`none`: for a row from before 2.4.0 the error used the elements' period, so it was measured without a period exactly
when its `period_d` is blank too. Columns you added yourself are kept, and so are notes you edited by hand: a note in quotes with commas in it stays whole, any `#` line you added stays at the top, and the rows already in the file keep their full text (only a new row's note is shortened, to 160 characters). SHOBS-P writes commas inside text as `;`, so the file stays readable by older versions and by a plain comma split. See [14. Time a maximum or minimum](14-time-a-maximum-or-minimum.md).

### Transits mode (Transit fit page)

| Button | File |
|---|---|
| **Save AAVSO exoplanet report…** | `AAVSO_<planet>_<date>_SHOBS-P.txt` (for example `AAVSO_KELT-16b_05-OCT-2026_SHOBS-P.txt`), and beside it `AAVSO_<planet>_<date>_SHOBS-P_settings.txt` (the reduction settings and, from 2.4.0, the fit's exact inputs at full precision: period, t0, a/R\*, b, Rp/R\*, T14, Teff and band, limb-darkening prior, catalog and lookup date; a fit of an opened report gets one too) and the field image `<planet>_<date>_SHOBS-P_field.png` (with `…_aavso_chart.png`). |
| **Save plot PNG…** | `<planet>_<filter>_<date>_SHOBS-P_transit.png`, and the corner plot `…_transit_corner.png` beside it (`…_transit_2.png` and `…_transit_2_corner.png` in set 2) |
| **Save corner plot…** → **Save PNG…** | `<planet>_<filter>_<date>_SHOBS-P_transit_corner.png`, with the fit's set number like the plot PNG (`…_transit_2_corner.png`); for the same fit it replaces the one **Save plot PNG…** wrote, in the view shown |
| **Save field image…** | `<star>_<filter>_<date>_SHOBS-P_field.png`, named after the Star ID (the host star), with `…_aavso_chart.png` |
| **Save light-curve CSV…** | The Variables-style light-curve CSV of the same photometry, and with a fit shown also `<planet>_<filter>_<date>_SHOBS-P_transit_fit.csv`: columns `BJD_TDB`, `rel_flux`, `rel_flux_err`, `model`, `residual`, `flag`, `airmass`, `segment`, `rel_flux_err_raw` (from 2.3.4; the error before the fit's scale S, given on the `# Errors:` line) (trimmed points flagged `trimmed`, and from 2.4.0 a point left out as a single-point outlier flagged `trimmed outlier`; `segment` is `before flip` / `after flip` on a night with a meridian flip). `rel_flux` is the raw relative flux, not detrended. Comment lines starting with `#` under the Created line give the planet, filter, Tc, verdict and O − C, a `# Columns:` line saying what the columns hold, `# Meridian flip at BJD_TDB …` when there was a flip, and (from 2.4.0) `# Fit input …` lines with the fit's exact inputs at full precision (chapter 19). It gets the same number as the report and plot. |
| **Save ExoFOP package…** | A folder `ExoFOP_<planet>_<YYYYMMDD>_<observer code>_SHOBS-P` holding `…_lightcurve.csv`, `…_fit.txt` (ending, from 2.4.0, with the fit's exact inputs at full precision) and `…_plot.png`. |
| **Save for ExoClock…** | `ExoClock_<planet>_<YYYYMMDD>.txt` (for example `ExoClock_TOI-2046b_20261027.txt`; the planet without spaces, the UTC date of the first frame): `#` header lines, then JD_UTC at mid-exposure, relative flux (not detrended) and error, one line per frame the fit used. Beside it, `ExoClock_<planet>_<YYYYMMDD>_info.txt` (named after its upload file; `ExoClock_info.txt` before 2.4.0): what to pick on ExoClock's upload form. Both get `_2`, `_3` … when the name is taken. |
| **Multi-night…** → **Save timing CSV…** | `<planet>_<first night>_to_<last night>_SHOBS-P_multinight_timings.csv`: one row per epoch (mid-time in BJD_TDB with its error, O − C against each ephemeris), with the joint shape and the ephemerides in `#` header lines |
| **Multi-night…** → **Save plot PNG…** | `<planet>_<first night>_to_<last night>_SHOBS-P_multinight.png` |
| **Multi-night…** → **Corner plot…** → **Save PNG…** | `<planet>_<first night>_to_<last night>_SHOBS-P_multinight_corner.png` |

See [19. Reports](19-transit-reports.md).

### Discovery mode (Field scan page)

| Button | File |
|---|---|
| **Save plot PNG…** | `<field>_<filter>_<date>_SHOBS-P_scan.png` (`<field>` is the Star ID, or `field`) |
| **Save CSV…** | `<field>_<filter>_<date>_SHOBS-P_scan.csv`: one row per measured star. |
| **Export suspects CSV…** | `field_scan_suspects.csv` |
| **Export all light curves CSV…** | `field_scan_lightcurves.csv` |
| (written by the scan itself, and again by **Add as suspect**) | `<field>_<filter>_<date>_SHOBS-P_suspects.json` in the Output folder: the night's suspects, read by later nights of the field for **Suspect again**. A new scan of the night replaces it. Not written without an Output folder. |

The two fixed names get `_2`, `_3` … like any other file, so give them a folder of their own per field if you scan
many fields.

### Any mode

| Button | File |
|---|---|
| **Credits and sources** → **Save credits as text…** | `SHOBS-P_credits.txt` |

## Series files

A series (`.cvseries`) holds every night of one star in one mode: all points, flags, the comparison and check stars,
night notes, the watch list and the settings of each night. It is a text file (JSON).

From 2.4.0 each point also carries `jump`: the number of its re-center segment in its night (chapter 12). 0 is
the first position of the field, 1 the segment after the first re-center, and so on; frames that came back to an
earlier position carry that segment's number again. Series saved by earlier versions have no such field and load
as before (every point reads 0, no re-center).

- **Save series** always asks where to save it, suggesting `<star>.cvseries`.
- After that, many actions save it again by themselves: adding or removing a night, night notes, **Use comps…**,
  **Trim** and **Clear trim**, planet-transit flags, catalog magnitudes typed in **Use comps…**, and report bookkeeping (saved reports and the
  nights marked sent). The status bar says so (*"Auto-saved to …"*).
- **The backup copy.** The first time SHOBS-P writes to a series file in a session, it keeps the previous version as
  `<name>_prev.cvseries`. Later writes in that session leave the `_prev` file alone, so it always holds the series
  as it was before this session. If something goes wrong, close the series and load the `_prev` file.
- **`<name>_backup.cvseries`.** Written beside the series, and never overwritten (the next ones are `_backup2`,
  `_backup3`…): the original of a series made before 2.4.0, copied when Load series updates it to the star
  records; and the series as it was each time you press **Use new value** under **Series magnitudes…**.
- **Trims.** From 2.4.0 a night's trimmed spans (**Trim** on the Variables Output page, chapter 12) are kept in
  that night's settings (`"night_binning"` → the night → `"trims"`: a list of `[JD from, JD to]`), and each trimmed
  point carries the word `trim` in its `flag`. The points themselves stay in the file. A series without the field
  loads as before; an older SHOBS-P ignores the field but treats the points as flagged.
- **Star records.** From 2.4.0 the file holds one record per comp and check star (`"stars"`): its sky position,
  every name it goes by, every catalog magnitude with its source and the one the series uses. The older entries
  (comp names, catalog magnitudes, positions) are still written, so an older SHOBS-P can read the file.
- Star positions on the image are **not** stored. You mark the target and comparison stars again on each new night.
- **Stacked nights.** A night whose points are stacks (the Photometry page's **Points: Stacked**, chapter 8) stores
  only the stacked points, and its settings carry a `"stack"` entry: the stack size N, the number of stacks and
  frames, how many were partial, the effective exposure and the frames' own exposure, and how far the field turned,
  how much was clipped and the largest shift. A per-frame night has none.
- A series records its mode. Loading it switches SHOBS-P to that mode.

See [10. Build a multi-night series](10-build-a-multi-night-series.md).

## Saved calibration masters

When SHOBS-P builds a master bias, dark, dark flat or flat, it saves it in a subfolder **SHOBS-P masters** inside the
folder of the raw frames:

- `SHOBS-P_master_bias_bin1.fits`
- `SHOBS-P_master_dark_bin1_120s.fits` (the dark's exposure is in the name)
- `SHOBS-P_master_darkflat_bin1_0.5s.fits` (the dark flats' exposure is in the name)
- `SHOBS-P_master_flat_bin1.fits`

The next time you calibrate with the same frames and binning, the master is reused, which saves minutes. It is
rebuilt automatically when the frames change, or when you tick **Rebuild masters (ignore saved ones)**. Keeping the
masters in a subfolder means other programs that take "every FITS file in the folder" never pick them up. You can
delete the subfolder at any time; it is rebuilt when needed. See [7. Calibrate](07-calibrate.md).

## Files in your user folder

These live in your user folder (on Windows `C:\Users\<you>`, on Linux and the Mac your home folder `~`). The names
start with a dot, so Windows Explorer and the Mac's Finder may hide them; type the name into the address bar, or turn
on hidden items (on a Mac, Cmd+Shift+period in Finder).

| File | What it holds |
|---|---|
| `.shobs_p_settings.json` | Your remembered settings: mode, site, telescope, aperture, focal length, pixel size, mount, observer code, scintillation choice, linear limit, stretch, Blink speed, window places, Discovery's period range and watch stars, and similar. Not the folders. |
| `.shobs_p.log` | Every line of the Log window, each with the date and time in front, and the full details of any error. From 2.4.0 a ⚠ line has `[WARN]` and a ✖ line `[ERROR]` after the time, so they are easy to search for. Past 5 MB it is renamed `.shobs_p.log.1` (one old file is kept) and a new log starts. |
| `.shobs_p_cache\catalogs\` | Star catalogs fetched for your fields, kept for 30 days so solving the same field again is faster. |
| `.shobs_p_cache\stacks\` | The stacks of the last **Stack** run (chapter 8): one 32-bit FITS file per stack, `stack_0001.fits` and on. Only the newest set is kept, because each stack is a full frame. Your Lights folder is never written to. |
| `.shobs_p_cache\lookups\` | The last good answer from each online lookup: `simbad.json` and `vsx.json` (Star ID lookups), `vsx_cone.json` (the variable-star check), `vsp.json` (AAVSO charts and sequences), `nea.json` (planet lookups), and more from other services. Used only when the service does not answer; a live answer always replaces it. Only public catalog values are kept, never your site, observer code or data. **Clear cached catalogs** (Credits page) empties this folder and `catalogs`. |
| `.shobs\onsky\` | One small JSON file per finished reduction, `<night>__<target>__<run id>.json`, for the SHOBS-FLUX app: the setup, the aperture and each frame's star fluxes, sky, seeing and airmass. Your site is never written to it. Since 2.3.0 it is format version 2: each star in each frame also has `e_per_unit` (electrons per SHOBS-P image unit, with the gain, binning, superpixel and flat included), and the measurement part has `e_per_unit_median`, `e_per_unit_source`, `e_per_unit_note`, `flat_applied` and `camera_bin_mode`. With no gain known, or on pre-calibrated frames, `e_per_unit` is empty (null) and the note says why. From 2.4.0, when `e_per_unit_source` is `sky`, `e_per_unit_note` says whether the gain was measured from the night's sky or is the one-frame lower limit. Nothing on screen changes. |
| `.shiloh_photometry_exports.json` | Named setups from the **Export…** window. |
| `.shiloh_photometry_colorcal.json` | The color term saved from a field color calibration, reused on later nights. |

Deleting any of these is safe: SHOBS-P starts again with defaults (and re-downloads what it needs). You will have to
type your site again if you delete the settings file. A `.shobs_p_transit_catalog.csv` left from SHOBS-P 2.2.21 or
earlier is no longer read or written (it served Tonight's transits, which is gone); you can delete it.

Your site is stored only here, and only after you type it (or say **Yes** when SHOBS-P offers the one in your FITS
headers). It is never shipped with the program. A series file carries the site of its own nights, but loading it
never replaces your own site.

## The self-test log

**Run self-test.bat** (Windows), **Run self-test.command** (Mac) and `./run_selftest.sh` (Linux) write
`selftest_log.txt` in the program folder and a copy, `SHOBS-P_selftest_log.txt`, in your Downloads folder (when you
have one).

SHOBS-P's own Python environment is not in the program folder either: it is in `%LOCALAPPDATA%\SHOBS-P` on Windows,
`~/.local/share/SHOBS-P` on Linux and `~/Library/Application Support/SHOBS-P` on a Mac (chapter 2). See [24. Troubleshooting](24-troubleshooting.md).

## The setup log (Mac and Linux)

On a Mac or Linux, the first start sets up SHOBS-P's own Python environment and downloads the packages it needs.
What the installer (pip) said is kept in `pip_log.txt` in that same SHOBS-P environment folder:
`~/Library/Application Support/SHOBS-P` on a Mac, `~/.local/share/SHOBS-P` on Linux. If setup fails, attach this
file to your problem report. To start the setup afresh, delete that SHOBS-P folder; it is set up again on the next
start.

---

Previous: [21. Check a suspect](21-follow-a-candidate.md) · Next: [23. Messages explained](23-messages-explained.md)
