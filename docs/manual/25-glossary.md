# 25. Glossary

Terms used in SHOBS-P and in this manual, in alphabetical order. Words in **bold** inside an entry have their own
entry.

**a/R\*** — The planet's orbital distance divided by the star's radius. With the **impact parameter**, it sets the
transit's shape and duration. The fit holds it near the published value.

**AAVSO sequence** — The comparison stars the AAVSO publishes for a variable star's chart, with checked magnitudes.
SHOBS-P circles them in cyan and suggests them first. Their labels are magnitudes without the decimal point: 91 is
V 9.1.

**Airmass** — How much atmosphere the light passes through: 1 at the zenith, about 2 at 30° altitude. Stars dim and
redden with airmass. SHOBS-P works it out from the frame time, the target's position and your site.

**Alt-az mount** — A mount that moves in altitude and azimuth, unlike a German equatorial mount. It never makes a
**meridian flip**, but the field slowly turns on the sensor through the night (field rotation), unless a
**derotator** turns the camera to follow it. Set **Mount** to **Alt-az** in the Optics box (Input page). See also
**zenith keyhole**.

**Alias (one-day alias)** — A false period in a period search, caused by observing only at night, once a day. A
period near 1 day, or one whose frequency differs from the true one by 1 per day, is suspect. SHOBS-P warns about it.

**Aperture, sky ring** — The circle of radius **Aperture r** in which a star's light is summed, and the ring between
**sky in** and **sky out** where the sky background is measured and subtracted.

**AUID** — AAVSO Unique Identifier, such as `000-BBC-123`. Every star in the AAVSO's charts and VSX has one.
SHOBS-P puts it in AAVSO reports when a comp or check star comes from the AAVSO sequence.

**Baseline** — In transit work, the out-of-transit light curve before ingress and after egress. The fit learns the
star's level and trends from it. See [16. Plan a transit night](16-plan-a-transit-night.md).

**Bayer pattern, OSC, MONO** — A one-shot-color (OSC) camera has a mosaic of red, green and blue filters over its
pixels in a pattern such as RGGB. A monochrome camera has none (SHOBS-P calls it **MONO**) and uses a filter wheel.

**ΔBIC** — A score that compares two models (here: a transit against a flat line), with a penalty for the extra
parameters. SHOBS-P needs a ΔBIC of 10 or more to call a transit detected.

**Binning** — Adding neighboring pixels together. SHOBS-P's **Binning** (Input page) adds same-color pixels only, so
colors stay separate. It is separate from any binning done in the camera.

**BJD_TDB** — Barycentric Julian Date in Barycentric Dynamical Time: the time the light would have reached the center
of mass of the solar system, on a uniform time scale. The standard for exoplanet timing. It differs from JD by up to
about ±8 minutes, depending on the star's position and the time of year.

**Check star** — A steady star measured like the target, to show whether the comparison stars are behaving. The
AAVSO requires one for **ensemble** reports.

**Chart ID** — The ID of the AAVSO chart (from the Variable Star Plotter) whose comparison stars you used. `na` when
the comps are not from an AAVSO chart.

**χ²ν (reduced chi-square)** — How the scatter of the points around the model compares with their error bars. About
1 is right. Well below 1: the error bars are too big. Well above 1: there is noise the error bars leave out.

**Cloud flag, cloud edge** — Flags on frames where the comparison stars lost more light than the **Cloud flag** limit
(Output page), or that sit at the edge of a cloudy stretch. Flagged frames are left out of the period search,
reports and the transit fit.

**Comp (comparison star)** — A steady star of known magnitude in the same frame. The target's magnitude is measured
against it, so changes in the sky affect both and cancel. SHOBS-P takes up to ten, **C1** to **C10**.

**Derotator** — A rotator on an **alt-az mount** that turns the camera through the night to follow the field
rotation, so the stars stay put on the sensor. SHOBS-P recognizes one from a rotator reading that drifts smoothly in
the headers.

**Detrending** — Removing a slow trend from a light curve, for example with **airmass**, while fitting. In the
transit fit the trend is fitted together with the transit.

**DTG** — Date-time group, the stamp on every file SHOBS-P writes: `121153Z OCT 26` means the 12th, 11:53 UTC,
October 2026.

**EGAIN** — The FITS header keyword with the camera gain in electrons per count (ADU). SHOBS-P uses it to work out
the photon noise in each point. Without it, the gain is estimated from the sky.

**Ensemble** — Using the average of several comparison stars. In an AAVSO report the comparison name is then
`ENSEMBLE`.

**Ephemeris, T0, epoch** — A planet's (or variable's) timetable: a reference time T0 and the period. Epoch is the
number of periods since T0. The predicted mid-transit is T0 + epoch × period.

**EXOTIC** — The transit-fitting program of NASA's Exoplanet Watch project. SHOBS-P reads and writes the same
AAVSO exoplanet report format.

**ExoFOP (ExoFOP-TESS)** — The website where follow-up observations of TESS targets (TOIs) are collected.

**FAP (false-alarm probability)** — The chance that noise alone would produce a peak as high as the one found in a
period search. SHOBS-P calls a period significant at 1% or less, counted both by points and by nights.

**Flat, bias, dark** — Calibration frames. A bias is a zero-length exposure (the camera's electronic offset), a dark
is an exposure with the shutter closed (heat signal), and a flat is an evenly lit exposure that maps vignetting and
dust shadows.

**FWHM** — Full width at half maximum: the size of a star's image, a measure of seeing and focus, in pixels.

**Impact parameter (b)** — How far from the star's center the planet crosses, in units of the star's radius. 0 is
straight across the middle; near 1 is a grazing transit.

**Ingress, egress, T14, Tc** — Ingress is when the planet starts to cross the star, egress when it has left. T14 is
the total duration from first to last contact. Tc is the time of mid-transit.

**JD, HJD** — Julian Date, a running day count used in astronomy (JD 2461000 is in 2025). HJD is the time the light
would have reached the Sun's center. AAVSO variable-star reports use JD; timing work uses HJD or **BJD_TDB**.

**Limb darkening** — A star looks darker near its edge than at its center, which rounds the bottom of a transit.
SHOBS-P sets it from the star's temperature and the filter.

**Lomb-Scargle periodogram** — A period search for unevenly spaced data. Its plot shows how well each trial period
fits; the highest peak is the best period.

**MCMC** — Markov-chain Monte Carlo: a way to find each fitted value's uncertainty by exploring many parameter
combinations near the best fit. **Quick**, **Standard** and **Thorough** set how long it runs.

**Meridian flip** — On a German equatorial mount, the telescope swings to the other side of the pier when the
target crosses the meridian. The field ends up rotated 180° on the sensor. One set of flats still covers both sides.

**NEB (nearby eclipsing binary)** — A faint eclipsing binary star close to the target that can fake a transit in a
low-resolution survey such as TESS. The NEB check measures the neighbors to rule them out.

**O − C** — Observed minus calculated: the measured time of a transit (or a variable's maximum or minimum) minus the
time the ephemeris predicts. A changing O − C shows a period change or a wrong ephemeris.

**Plate solution (WCS)** — The mapping between pixels in the image and positions on the sky. Many capture programs
write one into the FITS header; otherwise SHOBS-P finds it by matching star patterns to the catalogs.

**Red noise, β** — Noise that is correlated in time (seeing, tracking, thin cloud) rather than random from frame to
frame. β above 1 means the binned scatter falls more slowly than random noise would; the fit widens its
uncertainties by β.

**Rp/R\*, depth** — The planet's radius divided by the star's. The depth SHOBS-P reports is (Rp/R\*)²; the actual dip
at mid-transit is somewhat deeper because of **limb darkening**.

**Scintillation** — Twinkling: fast brightness changes caused by the atmosphere. It adds noise that grows with
airmass and is larger for small telescopes. SHOBS-P includes it in the error bars when **Include scintillation
(atmospheric twinkling) in the error bars** is ticked and the telescope aperture is entered.

**Series** — A file (`.cvseries`) holding several nights of one star in one mode, with every setting needed to add
more nights. See [10. Build a multi-night series](10-build-a-multi-night-series.md).

**Spare comp** — A catalog star near the comps' brightness that SHOBS-P measures on every frame, so you can swap it in
later with **Use comps…** without reducing again.

**Superpixel** — A way of turning a color camera's mosaic into an image: each 2×2 group of pixels becomes one pixel of
the chosen color channel. The image has half the width and height of the sensor, and no colors are mixed.

**Transmission loss (dew/haze?)** — A smooth fading of every star together under a steady sky, typical of dew on the
optics or high haze. SHOBS-P tells it apart from patchy cloud and asks whether to keep those points.

**Untransformed** — Magnitudes measured through your own filter and camera without converting them to the standard
system. SHOBS-P's magnitudes are untransformed; reports say so.

**VSX, SIMBAD** — The AAVSO's Variable Star Index and the CDS astronomical database. SHOBS-P uses them to look up
targets and to warn when a comp, check or candidate star is a known variable.

**Watch star** — A star you ask SHOBS-P to measure on every run, with its own light curve, without it being the
target. See [21. Follow a candidate](21-follow-a-candidate.md).

**× normal** — In a field scan: a star's scatter divided by the typical scatter of stars of the same brightness. 3 or
more makes a candidate by default.

**Zenith keyhole** — The small patch of sky around the zenith (above 85° in SHOBS-P) where an **alt-az mount** must
turn very fast in azimuth and the field rotation gets extreme, so frames there can trail or smear. Tonight's transits
and the Transit planner mark transits that pass through it when **Mount** is **Alt-az**.

**Zero point** — The offset that turns instrumental magnitudes into catalog magnitudes, measured from comparison
stars or, in a field scan, from all the catalog stars in the field.

---

Previous: [24. Troubleshooting](24-troubleshooting.md) · Next: [26. Credits and how to cite SHOBS-P](26-credits-and-citing.md)
