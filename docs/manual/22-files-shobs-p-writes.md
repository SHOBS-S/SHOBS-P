# 22. Files SHOBS-P writes

SHOBS-P never changes your frames. It writes only the files listed here: saved masters next to your calibration
frames, the results you save, series files, and a few small files in your user folder.

## Where results go

The Input page's **Output** folder decides where results go:

- **With an Output folder**, every Save button writes there without asking. The status bar says what was saved
  where, for example *"Saved AAVSO_HD_219134_TG_05-OCT-2026_SHOBS-P.txt to Results."*
- **Without one**, each Save opens a dialog with a suggested name, and a message confirms where the file went.
- **Nothing is overwritten.** If a file of that name exists, the new one gets `_2`, `_3` and so on.
- **Matching numbers.** The report, light-curve CSV and plot PNG of one set of results share one number, so they
  sort together (for example `…_SHOBS-P_2.txt`, `…_SHOBS-P_lightcurve_2.csv`, `…_SHOBS-P_lightcurve_2.png`). A new
  result starts a new number.
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

The time is a date-time group in UTC: day 12, 11:53 Z, October 2026. Plot PNGs carry *Created … · SHOBS-P 2.2.17*
in the picture. The line tells you, and anyone you send the file to, when it was made and with which program.

## Result files by mode

### Variables mode (Output – Variability page)

| Button | File |
|---|---|
| **Save AAVSO report** | `AAVSO_<star>_<filter>_<date>_SHOBS-P.txt`: AAVSO Extended File Format, ready for WebObs. |
| **Save light-curve CSV** | `<star>_<filter>_<date>_SHOBS-P_lightcurve.csv`: every point, flagged ones included with their flag. |
| **Save plot PNG** | `<star>_<filter>_<date>_SHOBS-P_lightcurve.png`: the four plots with the summary above them. |
| **Export…** | `<star>_<time>_<mag or flux>.csv`: the columns you choose; its header records every choice. |
| **Time of max/min…** → **Save to times CSV** | `<star>_times_of_extremum.csv`, next to the series file. Each new time is added; a re-measured one replaces the old row. |
| **Watch stars…** → **Export watch light curves CSV…** | `watch_stars.csv` |

When a night is highlighted in the night table, **Save AAVSO report** and **Save light-curve CSV** first ask whether
to save that night only or the whole series. See [15. Submit to AAVSO](15-submit-to-aavso.md).

### Transits mode (Transit fit page)

| Button | File |
|---|---|
| **Save AAVSO exoplanet report…** | `AAVSO_<planet>_<date>_SHOBS-P.txt` (for example `AAVSO_KELT-16b_05-OCT-2026_SHOBS-P.txt`) |
| **Save plot PNG…** | `<planet>_<filter>_<date>_SHOBS-P_transit.png` |
| **Save light-curve CSV…** | The Variables-style light-curve CSV of the same photometry. |
| **Save ExoFOP package…** | A folder `ExoFOP_<planet>_<YYYYMMDD>_<observer code>_SHOBS-P` holding `…_lightcurve.csv`, `…_fit.txt` and `…_plot.png`. |
| **Tonight's transits…** → **Export CSV…** | `transits_<date>.csv` |
| **Transit planner…** → **Export CSV…** | `<planet>_transits.csv` |

See [19. Reports](19-transit-reports.md).

### Discovery mode (Field scan page)

| Button | File |
|---|---|
| **Save plot PNG…** | `<field>_<filter>_<date>_SHOBS-P_scan.png` (`<field>` is the Star ID, or `field`) |
| **Save CSV…** | `<field>_<filter>_<date>_SHOBS-P_scan.csv`: one row per measured star. |
| **Export candidates CSV…** | `field_scan_candidates.csv` |
| **Export all light curves CSV…** | `field_scan_lightcurves.csv` |

The two fixed names get `_2`, `_3` … like any other file, so give them a folder of their own per field if you scan
many fields.

### Any mode

| Button | File |
|---|---|
| **Credits and sources** → **Save credits as text…** | `SHOBS-P_credits.txt` |

## Series files

A series (`.cvseries`) holds every night of one star in one mode: all points, flags, the comparison and check stars,
night notes, the watch list and the settings of each night. It is a text file (JSON).

- **Save series** always asks where to save it, suggesting `<star>.cvseries`.
- After that, many actions save it again by themselves: adding or removing a night, night notes, **Use comps…**,
  planet-transit flags, and AAVSO report bookkeeping. The status bar says so (*"Auto-saved to …"*).
- **The backup copy.** The first time SHOBS-P writes to a series file in a session, it keeps the previous version as
  `<name>_prev.cvseries`. Later writes in that session leave the `_prev` file alone, so it always holds the series
  as it was before this session. If something goes wrong, close the series and load the `_prev` file.
- Star positions on the image are **not** stored. You mark the target and comparison stars again on each new night.
- A series records its mode. Loading it switches SHOBS-P to that mode.

See [10. Build a multi-night series](10-build-a-multi-night-series.md).

## Saved calibration masters

When SHOBS-P builds a master bias, dark or flat, it saves it in a subfolder **SHOBS-P masters** inside the folder of
the raw frames:

- `SHOBS-P_master_bias_bin1.fits`
- `SHOBS-P_master_dark_bin1_120s.fits` (the dark's exposure is in the name)
- `SHOBS-P_master_flat_bin1.fits`

The next time you calibrate with the same frames and binning, the master is reused, which saves minutes. It is
rebuilt automatically when the frames change, or when you tick **Rebuild masters (ignore saved ones)**. Keeping the
masters in a subfolder means other programs that take "every FITS file in the folder" never pick them up. You can
delete the subfolder at any time; it is rebuilt when needed. See [7. Calibrate](07-calibrate.md).

## Files in your user folder

These live in your user folder (on Windows `C:\Users\<you>`). The names start with a dot, so Windows Explorer may
hide them; type the name into the address bar, or turn on hidden items.

| File | What it holds |
|---|---|
| `.shobs_p_settings.json` | Your remembered settings: mode, site, telescope, aperture, scintillation choice, stretch, Blink speed, window places, Tonight's transits limits, and similar. Not the folders. |
| `.shobs_p.log` | Error details. When something goes wrong in the window, the full error is appended here. |
| `.shobs_p_transit_catalog.csv` | The list of transiting planets for Tonight's transits, refreshed weekly. |
| `.shobs_p_cache\catalogs\` | Star catalogs fetched for your fields, kept for 30 days so solving the same field again is faster. |
| `.shiloh_photometry_exports.json` | Named setups from the **Export…** window. |
| `.shiloh_photometry_colorcal.json` | The color term saved from a field color calibration, reused on later nights. |

Deleting any of these is safe: SHOBS-P starts again with defaults (and re-downloads what it needs). You will have to
type your site again if you delete the settings file.

Your site is stored only here, and only after you type it (or say **Yes** when SHOBS-P offers the one in your FITS
headers). It is never shipped with the program. A series file carries the site of its own nights, but loading it
never replaces your own site.

## The self-test log

**Run self-test.bat** writes `selftest_log.txt` in the program folder and a copy, `SHOBS-P_selftest_log.txt`, in
your Downloads folder. See [24. Troubleshooting](24-troubleshooting.md).

---

Previous: [21. Follow a candidate](21-follow-a-candidate.md) · Next: [23. Messages explained](23-messages-explained.md)
