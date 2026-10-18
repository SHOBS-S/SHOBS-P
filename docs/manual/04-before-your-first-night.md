# 4. Before your first night

A few minutes of setup makes every later night quicker: tell SHOBS-P where you observe and with what, and take
calibration frames it can use. This chapter covers what to set once, which camera settings matter, and how to
organize your folders.

## Enter your site

Your site is needed for airmass, for accurate (BJD) times, and for the scintillation part of the error bars. Without
it, AAVSO reports show the airmass as `na`.

1. Go to the **Input** page (**1 Input** in the sidebar).
2. In the **Site** box, type:
   - **Latitude (°, north +)**: decimal degrees, north positive (for example `40.0`).
   - **Longitude (°, east +)**: decimal degrees, **east positive, west negative**. Observers in the Americas type a
     negative number (for example `-100.0`).
   - **Elevation (m)**: meters above sea level. It is used in exoplanet reports and for scintillation.
3. That's all. SHOBS-P remembers these three values on this computer from now on.

> **Tip:** If your capture program writes the site into the FITS headers (keywords such as `SITELAT` and `SITELONG`),
> SHOBS-P offers to fill the Site box from them when you press **Scan folders**. It always asks before saving a
> header's site as yours, because the frames may come from someone else's telescope.

## Enter your optics

In the **Optics** box on the Input page:

| Box | What to type | Why it matters |
|---|---|---|
| **Telescope** | Your telescope's name, for example "0.25 m Newtonian". | Goes into reports. Leave it blank to use the FITS header's name (often the mount's name, so typing your own is better). Remembered. |
| **Telescope aperture (mm)** | The clear aperture in millimeters. | Used for the scintillation error. Remembered. |
| **Focal length (mm)** | The effective focal length, with any reducer. | With the pixel size, gives the plate scale the solve needs. |
| **Pixel size (µm)** | Your camera's pixel size. | As above. |
| **Binning** | 1, 2 or 4. | SHOBS-P's own binning (see below). Usually 1. |

Leave **Include scintillation (atmospheric twinkling) in the error bars** ticked. It makes the error bars match the
real scatter, especially for bright stars and short exposures.

> **Caution:** **Focal length (mm)** and **Pixel size (µm)** are not remembered between sessions. They start at
> 2900 mm and 3.76 µm each time. If your capture program writes `FOCALLEN` and `XPIXSZ` into the headers,
> **Scan folders** fills them in (or asks if they differ); otherwise, check both boxes before every session. Wrong
> values are the most common reason a solve fails.

**About Binning.** Leave your camera unbinned (bin 1) when you capture, and use this box if you want bigger pixels.
SHOBS-P bins only pixels of the same color, so the colors of an OSC camera stay separate. Some capture programs mix
red, green and blue when they bin a color sensor; SHOBS-P notes this in the log when it sees camera-binned frames.

## Your observer code

If you report to the AAVSO, type your observer code in **Observer code** (the **AAVSO report** box on the Input
page). It is not remembered between sessions, but it is saved in every series file, so you normally type it once per
target.

## Color (OSC) or monochrome camera

### Color (one-shot-color) cameras

SHOBS-P needs to know the color pattern of your sensor (the "Bayer pattern": RGGB, BGGR, GRBG or GBRG).

- Most capture programs write it into the FITS header (`BAYERPAT`). SHOBS-P reads it when you press **Scan folders**
  and the status bar says, for example, **Bayer pattern from header: RGGB**.
- If your headers don't say, choose it on the **Calibrate** page under **Bayer pattern**. Your camera's manual or your
  capture program's settings give it.

You then choose which color to measure with **Channel** on the Calibrate page:

| Channel | AAVSO filter code | Comparison-star magnitudes |
|---|---|---|
| **green** (the normal choice) | TG | V |
| **red** (suits red stars, such as red dwarfs) | TR | R |
| **blue** | TB | B |
| **luminance** (all colors together) | CV | V |

### Monochrome cameras

Set **Bayer pattern** to **MONO** on the Calibrate page. SHOBS-P does this by itself when it finds no color mask in
your raw flats; without flats or a header keyword it cannot tell, and you must choose **MONO** yourself.

With **MONO**, the **Channel** box becomes **Filter**. SHOBS-P reads the filter from the FITS `FILTER` keyword, so
with a filter wheel each night is labeled correctly by itself. If your headers have no filter, choose it in the
**Filter** box (SHOBS-P remembers that choice). The choices are:

- **V (Johnson V)**, **B (Johnson B)**, **R (Cousins R)**
- **Clear / L (CV)**
- **Green imaging (TG)**, **Red imaging (TR)**, **Blue imaging (TB)**
- **Sloan g′ (SG)**, **Sloan r′ (SR)**, **Sloan i′ (SI)**, **Sloan z′ (SZ)**, **Sloan u′ (SU)**

**Sloan filters.** SHOBS-P understands the usual ways capture programs spell Sloan filters in the header, such as
`g'`, `gp`, `sdss g`, `Sloan g` or `Astrodon Sloan g'`. The comparison stars then get magnitudes in the same band:
from the AAVSO sequence when it has that band, otherwise APASS or Gaia. Sloan u′ has few catalog stars, so expect fewer
comparison stars to choose from.

**Filters SHOBS-P cannot use.** Johnson/Cousins I, H-alpha and other narrowband filters have no catalog magnitudes
here. Frames taken through them are reported as clear (CV) with V comparison magnitudes, and the log says so.

## Calibration frames

SHOBS-P uses bias, darks and flats. Each is optional (a missing one is skipped, and the log says so), but a good flat
matters most for precise photometry.

**Bias frames**
- The shortest exposure your camera allows, with the same gain, offset and temperature as your lights.
- 20 to 50 frames is plenty.

**Dark frames**
- The **same exposure** as your lights, at the same gain, offset and sensor temperature.
- SHOBS-P scales a dark to the lights' exposure if it must, but if the darks are more than 3 times shorter or longer
  than the lights, it asks whether to leave the dark out. Scaling a dark up also scales up its noise.
- 10 to 30 frames.

**Flat frames**
- Taken through the same optics, the same filter, the same focus, and **with the camera in the same rotation on the
  telescope** as the lights.
- Exposed to roughly a third to half of full scale, not saturated.
- 20 to 50 frames. You do not need separate darks for the flats: SHOBS-P takes the bias (and the dark, scaled to
  the flats' exposure) out of each flat before combining them.
- Fresh is best. Dust moves. SHOBS-P adds a caution to the log when a saved master flat is more than 30 days from the
  lights.

### Flats and the camera's rotation

Dust shadows and vignetting from the optics in front of the camera turn with the camera. A flat taken with the camera
turned to another angle corrects the wrong pattern, often by about 1%: enough to fake or hide a shallow transit.

So **take flats with the camera in the same rotation as the lights**. If your capture program writes the rotator angle
into the header (`ROTATOR` or a similar keyword), SHOBS-P checks this for you:

- If the flats and lights were taken at angles more than 2° apart, it asks before calibrating.
- If the Flat folder mixes flats from different angles, it asks too, because one master flat built from all of them
  matches none of them exactly.

**A meridian flip does not need new flats.** On a German equatorial mount, the rotator reading changes by 180° at the
flip, but the camera has not turned on the telescope. SHOBS-P compares rotator angles modulo 180°, so readings of 30°
and 210° are the same camera position, and the same flats fit both sides of the flip. You only need new flats when the
camera or rotator is actually turned to a new angle on the telescope.

> **Caution:** If your rotator really turns the camera 180° during the night (rather than the mount flipping), the
> flats fit only one side. SHOBS-P tells a flip from a turn using the `PIERSIDE` keyword when your headers have it.

### Saved masters

The first time you calibrate, SHOBS-P builds a master bias, dark and flat and saves each in a subfolder called
`SHOBS-P masters` inside the bias, dark or flat folder. The next time you use the same folder, it reuses the master
instead of stacking the frames again, as long as the frames, the binning and the bias and dark they were built with
have not changed. This saves a lot of time when you use one set of darks or flats for many nights.

- To force a fresh stack, tick **Rebuild masters (ignore saved ones)** on the Calibrate page.
- You can also point a calibration box at one saved master file with **Master file…** on the Input page.
- Masters made by other programs (PixInsight and others) are not accepted, because how they were scaled and
  normalized is not known. Give SHOBS-P the raw frames instead.

## A folder layout that works well

SHOBS-P reads one folder per kind of frame. Any layout works, but this one keeps things simple:

```
C:\Astro\
  Calibration\
    Bias_gain100\
    Darks_gain100_-10C_120s\
    Darks_gain100_-10C_60s\
  SZ Lyn\
    2026-10-14\
      Lights\
      Flats\
    2026-10-21\
      Lights\
      Flats\
    Reports\
  TrES-3 b\
    2026-09-04\
      Lights\
      Flats\
    Reports\
```

Why this works:

- **Bias and darks in a shared Calibration folder**, named by what makes them match (gain, temperature, exposure).
  They don't change from night to night, so one set serves many nights and its saved master is reused.
- **Flats go with the night they were taken.** Flats record that night's focus, dust and camera angle, so each
  night's flats sit in a **Flats** folder next to its **Lights**. The master flat SHOBS-P builds is saved in that
  folder's `SHOBS-P masters` subfolder, so it stays with the night too. If a night has no usable flats of its own,
  point the Flats box at another night's Flats folder taken at the same camera angle (SHOBS-P checks the angle and
  the flats' age and tells you if they don't match).
- **One night, one field, one Lights folder.** Keep each night's lights of one target in their own folder. SHOBS-P can
  sort out a folder that holds two fields (it asks), but it is quicker not to need that.
- **Calibration frames that end up in a Lights folder are skipped** automatically, so a capture program that saves
  everything together still works.
- **A Reports folder per target** to use as the **Output** folder: every Save button then writes there without asking,
  and never overwrites a file.

---

Previous: [3. A tour of the window](03-tour-of-the-window.md) · Next: [5. Input](05-input.md)
