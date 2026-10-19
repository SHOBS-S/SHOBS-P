# 24. Troubleshooting

This chapter covers:

- the self-test, which checks that SHOBS-P and its Python packages work on your computer,
- the log file, where error details go,
- typical problems and their fixes,
- how to report a problem.

## The self-test

The self-test runs SHOBS-P's own checks with your Python and your copies of Astropy, photutils and Tkinter. It
changes nothing: no settings, no data.

To run it:

1. Open the SHOBS-P program folder.
2. Double-click **Run self-test.bat**. A console window opens.
3. Wait a minute or two. The transit fits are the slow part.
4. Read the last line. It should be `RESULT: ALL PASSED`.


The full log is written to `selftest_log.txt` in the program folder, with a copy, `SHOBS-P_selftest_log.txt`, in
your Downloads folder.

What the self-test does, in sections:

1. imports the program files and prints the versions of SHOBS-P, NumPy, Astropy and photutils;
2. checks many helper calculations (masters, star sizes, times, names, flags);
3. runs a real period search;
4. runs a transit regression on sample reports, when they are present (in a `selftest_reports` folder next to
   `selftest.py`; see chapter 2);
5. opens the main window and closes it again, with nothing clicked.

Lines marked `ok` passed; `FAIL` did not. Some lines say a step was *skipped*: for example section 4 when there are no
sample reports (`(no sample transit reports to run on: skipped)`), or the planet-name checks when the NASA Exoplanet Archive cannot be reached. A
skipped step is not a failure.

If the result is `N FAILED`, send `selftest_log.txt` with your report (see below).

## The log file

SHOBS-P started from its desktop shortcut has no console window. When something goes wrong inside the program,
it shows *"Something went wrong: … Details were written to ….shobs_p.log."* and appends the full error to
`.shobs_p.log` in your user folder (`C:\Users\<you>`). Messages that would have gone to a console go there too.

The file also holds every line of the program's own log (below), each with the date and time in front, so you can
look back at an earlier session. When it grows past 5 MB, it is renamed `.shobs_p.log.1` and a new one starts; only
that one older file is kept.

The file name starts with a dot, so Windows Explorer may hide it. Type `%USERPROFILE%\.shobs_p.log` into the
Explorer address bar to open it.

The program's **own log** (the dark box on the Calibrate page, and under the image on the Photometry page) is
different: it records everything SHOBS-P did in this session, step by step. Read it whenever a result looks odd. Its
lines are also written to `.shobs_p.log`, as above.

## Starting with the console

**run_windows.bat** in the program folder starts SHOBS-P with a console window. It checks the Python packages first,
and if SHOBS-P cannot start, the console shows why and stays open (*"The app did not start. Leave this window open
and copy the message above."*). Use it when the desktop shortcut does nothing.

## Typical problems

### Starting

| Problem | Fix |
|---|---|
| Double-clicking the shortcut does nothing. | Start **run_windows.bat** and read the console. If the Python SHOBS-P used was removed or upgraded, run **Create shortcuts.bat** again: it finds a working Python and rebuilds SHOBS-P's own environment. |
| *"SHOBS-P could not find a Python it can use."* | Each Python it tried is listed above that line with the reason. Install Python 3.11 or newer from python.org (tick **Add python.exe to PATH**), or with Anaconda run **Create shortcuts.bat** from the Anaconda Prompt, then try again. |
| The shortcut stopped working after you moved the folder. | Run **Create shortcuts.bat** again from the new place. |
| *matplotlib is not installed.* where the image should be. | The packages did not install. Run **run_windows.bat**, which installs them, and read any error it shows. |
| The window runs off the screen, or text is cut. | Maximize the window. On a scaled display (125–150%) the lower rows of the Output pages need the full height. In a narrow window (for example half the screen), the Input page's boxes and the Photometry page's **View** box wrap onto more rows instead of hiding. |

### Input and calibration

| Problem | Fix |
|---|---|
| The folders are empty every time you start. | That is by design: folders are not remembered. Your site, optics and observer code are. |
| A folder shows *0 files*. | SHOBS-P reads FITS (`.fits`, `.fit`, `.fts`, also compressed) and XISF. Save camera RAW, TIFF or JPEG frames as FITS. |
| A monochrome camera's frames look like a checkerboard. | The Bayer pattern is wrong. On the Calibrate page choose **MONO** (SHOBS-P sets it by itself from raw flats, or from the lights' sky background when there are bias or dark frames or the sky is bright; otherwise it asks **Color or monochrome camera?** at **Scan folders**). |
| Calibration takes a long time every night. | It should reuse saved masters. If your flats or darks change every night, that is expected. |
| Results look worse after replacing calibration frames. | Tick **Rebuild masters (ignore saved ones)** once. |

### Solving and stars

| Problem | Fix |
|---|---|
| **1 Not solved**. | Press **Solve again** to see the diagnosis. Check **Focal length (mm)**, **Pixel size (µm)** and **Binning** on the Input page; a wrong plate scale is the usual cause. Check that RA/Dec are near the field. |
| Frames from another telescope do not solve. | Set that telescope's focal length and pixel size in the Optics box. |
| The check star (or a comp) reads much fainter than its catalog magnitude, or scatters a lot. | The marker is probably a few pixels off the star's center, so every frame is measured beside the star. Before each run SHOBS-P warns in the log (*"⚠ Check marker at … is … px from the center of the star there …"*). Clear the marker, zoom in, and click the star again. |
| A star's name and magnitude do not fill in when clicked. | The image is not solved yet, or the star is not in the catalogs. Type the magnitude yourself. |
| The image is too small to click stars. | Wheel to zoom, drag to pan, or **Pop out** the image into its own window. |
| The solve or lookups fail at night. | The catalogs and lookups need the internet. Calibration and photometry do not. Catalog stars fetched for a field are kept for 30 days, which often lets the same field solve again without the internet. |

### Results

| Problem | Fix |
|---|---|
| Error bars much larger than the scatter (χ²ν far below 1). | Usually the camera gain: the EGAIN keyword is missing or wrong in your headers. The photometry log's *Photon noise* line says which gain was used. |
| Steps between nights in a series. | Different comps, apertures or equipment between nights. Read the **Careful:** lines when adding a night, and use **Use comps…** to put every night on the same comps. |
| A step at the meridian flip. | Look at **Comp health…**: a comp that jumps at the flip is a common cause. Try **Pier side as segments** (Variables mode). |
| Frames on an alt-az mount are lost or misaligned through the night. | Set **Mount** to **Alt-az** in the Optics box (Input page), so the alignment follows the field rotation. |
| The transit fit does not start. | It needs a planet on the Input page and at least 20 usable points. Look at the hint under the page title. |
| The Output pages show an old result. | Run photometry again: an old transit fit is cleared as soon as the data change. |
| You lost a night from a series. | Close the series and load `<name>_prev.cvseries`, the version from before this session. |

## Reporting a problem

Problems and ideas go to the project's GitHub Issues page (github.com/SHOBS-S/SHOBS-P/issues). The forms ask for:

- the SHOBS-P version (bottom of the sidebar, for example **Version 2.2.18**),
- your camera, and whether it is color or monochrome,
- the mode and the step,
- what you expected and what happened,
- a screenshot,
- `selftest_log.txt` from **Run self-test.bat**, and `.shobs_p.log` from your user folder.

A copy of the program's own log (from the Calibrate page) around the time of the problem helps too.

---

Previous: [23. Messages explained](23-messages-explained.md) · Next: [25. Glossary](25-glossary.md)
