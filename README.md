# SHOBS-P — Shiloh Hill Observatory Photometry

A Windows desktop app for differential aperture photometry with one-shot-color (OSC) and monochrome
astronomical cameras. It takes you from raw FITS frames to a calibrated light curve and an AAVSO-ready report in
one window.

## Three modes

| Mode | What it does |
|---|---|
| **Variables** | Light curves, Lomb-Scargle period search with day-alias and leave-one-night-out checks, color terms, comparison-star health, multi-night series, AAVSO Extended reports |
| **Transits** | Exoplanet lookup (NASA Exoplanet Archive), MCMC transit fit with a detected / inconclusive / none verdict, transit planner, AAVSO exoplanet reports |
| **Discovery** | Scans the whole field for stars that vary |

All three share the same steps: **Input → Blink → Calibrate → Photometry → Output**.

Highlights: bias/dark/flat calibration with superpixel debayering, frame alignment through meridian flips,
comparison-star ensembles, catalog comps from the AAVSO sequence, Gaia DR3, APASS and Tycho-2, and error bars that
include scintillation (Osborn et al. 2015).

## Requirements

- Windows 10 or 11
- Python 3.11 or newer from [python.org](https://www.python.org/downloads/) (check "Add python.exe to PATH")
- Packages (installed automatically): numpy, scipy, matplotlib, astropy, photutils, ccdproc

<!-- Screenshots: add docs/transit_fit.png, docs/light_curve.png, docs/field_scan.png and uncomment.
![Transit fit](docs/transit_fit.png)
-->

## Install

1. Install **Python 3.11 or newer** from [python.org](https://www.python.org/downloads/). On the first installer
   screen, tick **"Add python.exe to PATH"**.
2. Download **`SHOBS-P_v<version>.zip`** from the latest [release](../../releases/latest) and unzip it somewhere
   permanent (for example `C:\Astro`). It makes a `clearv_photometry` folder.
3. In that folder, double-click **`Create shortcuts.bat`**. It installs the Python packages (a few minutes the first
   time) and puts a SHOBS-P icon on the Desktop and in the Start menu.
4. Double-click **`Run self-test.bat`**. It changes nothing, takes a minute, and should end with
   `RESULT: ALL PASSED` in `selftest_log.txt`.

## Quick start

**First, try it on sample data.** Start SHOBS-P, and on the Input page press **Try it with sample data…** and pick an
empty folder. It writes a small synthetic night (a 0.12-day variable star), fills in every box and marks the stars.
Then go through the steps on the left: **Blink → Calibrate → Photometry → Output**.

**Then your own frames:**

1. **Input:** choose the folders of bias, dark, flat and light frames (FITS or PixInsight XISF), and press **Scan
   folders**. Type your site (latitude, longitude, elevation) and your optics once; SHOBS-P remembers them, and fills
   them from your FITS headers when the camera software writes them. Type the target's name (or the planet's, in
   Transits mode) and press Enter: RA and Dec are looked up for you.
2. **Blink:** step through the lights and reject anything with trails, clouds or bad guiding.
3. **Calibrate:** check the camera (Bayer pattern for a colour camera, MONO for a monochrome one; SHOBS-P reads it
   from the headers or the flats when it can) and press **Build masters and calibrate**.
4. **Photometry:** press **Label chart** to circle catalog stars with their magnitudes, click the target, pick one
   or more comparison stars (green rings are good choices) and a check star, then **Run photometry**.
5. **Output:** the light curve, period search and AAVSO report (Variables); the MCMC transit fit and AAVSO
   exoplanet report (Transits); or the list of stars that vary (Discovery).

Every page and button is described in **[README.txt](README.txt)** (also in the zip).

## Known limitations

- **Windows only** (10 or 11). It is plain Python and may run elsewhere, but only Windows is tested.
- **Colour (OSC) cameras are the most tested.** Monochrome cameras are supported (V, B, R, clear and LRGB imaging
  filters); that path is newer, so reports from mono users are especially welcome.
- **I and Sloan filters are not supported yet**: the comparison-star catalogs used carry B, V and R only.
- **An internet connection is needed** for target, planet and catalog lookups (SIMBAD, VSX, NASA Exoplanet
  Archive, AAVSO VSP, Gaia, APASS, Tycho-2). Calibration and photometry themselves run offline.
- Magnitudes are **untransformed** (no transformation coefficients yet).

## Reporting a problem or an idea

Please open an [Issue](../../issues/new/choose). The forms ask for what helps most: the SHOBS-P version (bottom of
the sidebar), your camera, the mode and step, what you expected and what happened, a screenshot, and the log file.
Two files help with almost any problem: `selftest_log.txt` from **Run self-test.bat**, and `.shobs_p.log` in your
user folder (`C:\Users\<you>`).

## Documentation

**[README.txt](README.txt)** is the full reference: every page and button, what is new in each version, the version
history, and the credits for every catalog, service and published method the app uses. A task-based operator's
manual is planned.

## Status

SHOBS-P is under active development and in regular use for variable-star and exoplanet-transit observing.
Bug reports and suggestions are welcome in [Issues](../../issues).

## How it was made

SHOBS-P was designed, tested and validated against real observations at Shiloh Hill Observatory. Most of the code
was written with the help of an AI assistant (Anthropic's Claude), working to the observatory's specifications and
checked against its data.

## License

Copyright © 2026 Shiloh Hill Observatory.

SHOBS-P is free software: you can redistribute it and/or modify it under the terms of the GNU General Public
License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any
later version. It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY. See the
[LICENSE](LICENSE) file for details.
