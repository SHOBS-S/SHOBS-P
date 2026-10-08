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

## Install

1. Click **Code → Download ZIP** on this page and unzip it anywhere.
2. Double-click **`Create shortcuts.bat`**. It installs the packages and puts a SHOBS-P icon on the Desktop and in
   the Start menu.
3. Double-click **`Run self-test.bat`** to check your installation. It changes nothing and writes
   `selftest_log.txt`.
4. Start SHOBS-P and type your site's latitude, longitude and elevation once on the Input page (Site and optics).
   SHOBS-P remembers them in your user folder.

## Documentation

**[README.txt](README.txt)** is the full user guide: every page and button, what is new in each version, the
version history, and the credits for every catalog, service and published method the app uses.

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
