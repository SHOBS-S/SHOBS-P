# 1. Welcome

SHOBS-P (Shiloh Hill Observatory Photometry) is a Windows program for differential aperture photometry. It takes a
night of raw FITS frames from a color (OSC) or monochrome camera and turns them into a calibrated light curve and a
report you can send to the AAVSO or ExoFOP, all in one window.

You do not need to be a programmer to use it. If you can take a night of frames with bias, darks and flats, you can
reduce them with SHOBS-P.

## Who it is for

SHOBS-P is written for amateur observers who send data to the AAVSO, to Exoplanet Watch, or who just want to know
whether a star varies. It works with:

- **Color (OSC) cameras.** The color filter mosaic is split into its colors without mixing them (a "superpixel"
  debayer). The green channel is the usual choice and is reported to the AAVSO as TG.
- **Monochrome cameras** with Johnson V, B, Cousins R, clear or LRGB imaging filters, and Sloan g′ r′ i′ z′ u′.
- **Frames with or without a plate solution.** If your capture program writes the field's position in the FITS
  header, SHOBS-P uses it. If not, it finds the field from the target's name, or from a rough field center you type.

## The three modes

SHOBS-P has three modes. You choose one with the buttons at the top left of the window.

| Mode | Use it to… |
|---|---|
| **Variables** | Follow a known variable star over one night or many: light curve, period search, times of maximum and minimum, AAVSO Extended reports. |
| **Transits** | Catch an exoplanet transit: planet lookup, transit planner, transit fit with a plain verdict (detected, inconclusive or none), AAVSO exoplanet report and ExoFOP package. |
| **Discovery** | Measure every star in a field and flag the ones that vary more than stars of the same brightness. No target star needed. |

All three modes share the same five steps: **Input → Blink → Calibrate → Photometry → Output**. Part 2 of this
manual covers the first four steps, which work the same way in every mode. Parts 3, 4 and 5 cover each mode's own
work.

## What SHOBS-P does for you

- **Calibrates** your lights with master bias, dark and flat frames, and saves the masters so the next night is
  quicker.
- **Finds the field** by itself (a plate solve) and asks the AAVSO comparison-star sequence, Gaia DR3, APASS and
  Tycho-2 for the stars in it. Click a star and its name and magnitude fill in.
- **Suggests comparison stars** close to your target in brightness and color, with AAVSO sequence stars first.
- **Checks your choices**: it warns you when a comparison star is a known variable, much brighter or fainter than the
  target, or very red.
- **Lines frames up** through meridian flips and flags saturated frames, cloud and drift.
- **Gives honest error bars**: photon and sky noise in electrons, plus atmospheric twinkling (scintillation), so the
  error bars match the real scatter.

## What SHOBS-P does not do

Knowing the limits saves time:

- **It does not run your camera or mount.** Take your frames with your usual capture program, then open them here.
- **It reads FITS and PixInsight XISF files only.** Camera RAW, TIFF and JPEG files are not read.
- **It does not transform magnitudes.** Results are untransformed (no transformation coefficients yet), and the
  report says so.
- **It does not support Johnson/Cousins I yet.** The comparison-star catalogs it uses carry B, V, R and Sloan bands
  only.
- **It does not use other programs' master frames.** Point it at your raw bias, dark and flat frames instead. It
  builds its own masters and reuses them.
- **It needs the internet for lookups**: star and planet names, catalogs and the variable-star check. Calibration and
  photometry themselves run offline.
- **It is made for Windows 10 and 11.** It also runs on a Mac, but the Mac is only lightly tested.

## How to use this manual

- **Part 1** (chapters 1–4) gets you installed and ready for your first night.
- **Part 2** (chapters 5–8) walks through the steps every mode shares.
- **Parts 3–5** are task-based: "reduce a variable-star night", "fit a transit", "scan a field".
- **Part 6** is reference: files, messages, troubleshooting and a glossary.

Button names and other words you see on the screen are printed in **bold**, exactly as the program shows them.

> **Tip:** The quickest way to learn SHOBS-P is to press **Try it with sample data…** on the Input page. It writes a
> small practice night and fills in every box. Chapter 2 shows how.

---

Previous: [Contents](README.md) · Next: [2. Installing and starting](02-installing-and-starting.md)
