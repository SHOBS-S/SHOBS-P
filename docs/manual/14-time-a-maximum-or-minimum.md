# 14. Time a maximum or minimum

For eclipsing binaries and short-period pulsators, the most valuable single number from a night is often the *time* of a minimum or maximum. Compared with the time predicted by the star's elements (period and epoch), it gives **O − C**: observed minus calculated. Collected over months and years, O − C values show whether the period is changing.

SHOBS-P times an extremum from one night of your series and keeps the results in a CSV file per star, which grows night by night into an O − C diagram.

## Before you start

- The night must cover the maximum or minimum, with points well before and after it. SHOBS-P needs at least 12 usable points.
- Only untrimmed points are used (from 2.4.0): a stretch left out with **Trim** on the Output page (chapter 12) is left out of the time of maximum or minimum too, whether **Leave flagged points out** is ticked or not. Never trim around the maximum itself to tidy it.
- For the O − C, you need the star's elements. SHOBS-P can fetch them from VSX.
- For heliocentric or barycentric times (HJD, BJD_TDB), the target's RA/Dec must be on the Input page. They are filled in when the Star ID is looked up. Without them, times stay in JD, and the O−C is taken in JD (up to 8 minutes off).

## Measure a time

1. On the Output page, press **Time of max/min…**. The **Time of maximum / minimum** window opens.
2. In **Night**, choose the night. It starts on the highlighted night, or the last one.
3. In **Type**, choose **maximum** or **minimum**. SHOBS-P sets this from the VSX variable type: minimum for eclipsing binaries, maximum for pulsators and the rest.
4. Leave **Half-window (min)** blank. SHOBS-P then picks how much of the light curve on each side of the extremum to fit. Type a number of minutes only if the automatic window catches something it should not, such as a nearby shoulder of the light curve.
5. Check **Period (d)** and **Epoch**. For a star with times already saved in its times CSV (below), the window
   opens with them filled from those times (from 2.4.0; 2.3.3 had only the button): **Period** is the one used last
   and **Epoch** the first saved maximum (or minimum, by the **Type** box), and the label says **from the times CSV:
   epoch = first saved maximum, period last used**. That is often better than VSX's elements, whose epoch can be
   decades old. Switching **Type** takes the epoch from that type's first saved time, unless you typed the elements
   yourself (then they stay). With no saved time of the chosen type, the boxes hold VSX's elements from the Star ID
   lookup and the label says, for example, **no saved maxima yet; VSX elements**; if they are empty, press **Elements
   from VSX**. **Elements from VSX** and **Elements from saved times** switch between the two at any time. A time
   saved against the saved times' elements is marked **saved** in the CSV's elements column. The label beside it shows **VSX** when the elements are VSX's, or **typed** when you typed your own. VSX epochs are HJD, so leave the system box on **HJD** for VSX elements. Choose **BJD** if your elements are in BJD.
6. Press **Measure**.

With Period and Epoch filled in, SHOBS-P looks for the extremum near each time those elements predict in the night.
Old elements can miss by part of a cycle: VSX epochs are often decades old, and a period error far too small to notice
grows to an hour or more over tens of thousands of cycles. The predicted window then lands on the other branch of the
light curve (for a maximum, on the night's minimum). From 2.3.5, when no predicted window holds the extremum, SHOBS-P
measures the night's own extremum instead and adds a note: *"found away from the time the elements predict …: the
elements may be off by part of a cycle"*. Its O − C is then large, which is the sign to use your own elements: your
own period (Output page, Period box) and one of your own well-measured times as the Epoch. Before 2.3.5 it said only
*"polynomial: no extremum inside the window"*; clearing the **Epoch** box and pressing **Measure** again measures the
night's own extremum.

![The Time of maximum / minimum window for GSC 03308-00462: the elements filled by Elements from saved times, a measured maximum, the two saved times listed under the result, and the O − C diagram](img/14-extremum.png)

*The saved-times list under the result works out each time's O − C against the elements in the boxes, as the O − C
diagram does, so the two always agree (the list's first line names the elements). When a time was saved against other
elements (VSX's, or an earlier period), its line adds the O − C stored then, for example* `O−C +0.0 min (saved +9.4
against VSX HJD)`*. With no elements in the boxes the list shows each row's O − C as stored.*

## Read the result

The result text gives, for each extremum found that night:

- **Tmin** or **Tmax**: the adopted time in HJD with its error in days and minutes, plus the same moment in **BJD_TDB** and **JD**.
- **polynomial deg N**: the time from a local polynomial fit, with how many points and what window it used. This is the adopted method.
- **Kwee-van Woerden**: the classic method for eclipse timings, shown for comparison (it assumes mirror-image branches, so on a lopsided maximum it can read a minute off).
- **Fourier**: the time from a Fourier series fitted at the period, also for comparison.
- **O−C**: in days and minutes, with the cycle number E and the calculated time C it was compared against. For a minimum, E can be a half cycle (a secondary eclipse).
- Any notes and ⚠ warnings.

On the left plot, the red curve is the polynomial fit and the red line the adopted time. The blue dashed line is Kwee-van Woerden, the green dotted line Fourier, and the purple dash-dot line the predicted time C.

Where does the error come from? SHOBS-P refits the polynomial 500 times with stretches of the night's own noise added, and allows for noise that lasts minutes (red noise). The error is never smaller than half the difference from the Fourier time (and, for a minimum, from Kwee-van Woerden). So if the methods disagree, the error says so. Without elements there is no Fourier check and the error can be low by a factor of 2–3; the result says so. (The details are for the methods document, planned.)

If no elements are given, the result says **No elements: type the period and epoch (or Elements from VSX) for the O−C.** You still get the time.

## Save the time

1. Press **Save to times CSV**.
2. The first time, if the series has no file and no Output folder is set, SHOBS-P asks where to save.

The file is called `<Star>_times_of_extremum.csv` (for example `SZ_Lyn_times_of_extremum.csv`). It goes next to the series file, or in the Output folder. The line under the result shows which file is in use and how many times of this star it holds, and, from 2.3.3, lists the saved times themselves (type, HJD and error, O − C, night; the last eight), so you need not open the CSV to find an epoch.

**Older times saved against VSX's elements.** The CSV keeps each row as it was saved: its own `period_d`, `epoch`,
`elements` (`VSX HJD`, `saved HJD`, `typed HJD`) and the O − C and cycle worked out then. SHOBS-P never rewrites them,
so the file stays a record of what was used. So a CSV started in 2.3 with VSX's elements and continued in 2.4.0 with
the saved times' elements holds O − C columns against two sets of elements. The window does not mix them: the list and
the diagram work every row out again against the elements in the boxes. If you analyse the CSV elsewhere, use the time
columns (`hjd`, `bjd_tdb`) with one set of elements, not the stored `o_c_min` column.

Measuring the same extremum again and saving replaces that row (same star, same type, within 0.01 d), so you can refine a timing without making duplicates.

To keep times in another file, for example one shared between several series of the same star, press **Choose times CSV…** and pick or name a file.

## The O − C diagram

The right-hand plot reads the times CSV and works every saved time out against the elements in the boxes (it follows
them as you type). Saved maxima are filled circles, saved minima filled squares. Tonight's result, not yet saved, is an open red marker. The horizontal line at zero is "exactly on time".

- Points scattered around zero: the elements are good.
- Points along a sloping straight line: the period is slightly off.
- Points along a curve: the period is changing.

Until there is something to plot, the panel says **The O−C diagram appears here once times are saved to the CSV**.

---

Previous: [13. Look for a period](13-look-for-a-period.md) · Next: [15. Submit to AAVSO](15-submit-to-aavso.md)
