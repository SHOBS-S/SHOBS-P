# 24. Troubleshooting

This chapter covers:

- the self-test, which checks that SHOBS-P and its Python packages work on your computer,
- the log file, where error details go (and `pip_log.txt`, for setup problems on Linux and the Mac),
- typical problems and their fixes,
- how to report a problem.

## The self-test

The self-test runs SHOBS-P's own checks with your Python and your copies of Astropy, photutils and Tkinter. It
changes nothing: no settings, no data.

To run it:

1. Open the SHOBS-P program folder.
2. Double-click **Run self-test.bat** (Windows) or **Run self-test.command** (Mac), or type `./run_selftest.sh` in a
   terminal in that folder (Linux). A console window opens.
3. Wait a minute or two. The transit fits are the slow part.
4. Read the last line. It should be `RESULT: ALL PASSED`.


The full log is written to `selftest_log.txt` in the program folder, with a copy, `SHOBS-P_selftest_log.txt`, in
your Downloads folder (on Linux and the Mac, when you have one). On Linux without a screen, the window test runs on a
hidden screen when `xvfb-run` is installed.

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
`.shobs_p.log` in your user folder (`C:\Users\<you>`; your home folder `~` on Linux and the Mac). Messages that
would have gone to a console go there too.

The file also holds every line of the program's own log (below), each with the date and time in front, so you can
look back at an earlier session. When it grows past 5 MB, it is renamed `.shobs_p.log.1` and a new one starts; only
that one older file is kept.

The file name starts with a dot, so Windows Explorer may hide it. Type `%USERPROFILE%\.shobs_p.log` into the
Explorer address bar to open it.

The program's **own log** (the dark box on the Calibrate page, and under the image on the Photometry page) is
different: it records everything SHOBS-P did in this session, step by step. Read it whenever a result looks odd. Its
lines are also written to `.shobs_p.log`, as above.

## pip_log.txt (Linux and Mac setup)

On Linux and the Mac, the first start downloads SHOBS-P's Python packages with pip. The Terminal shows only a short
line per package; pip's own detailed messages go to **pip_log.txt**, next to SHOBS-P's environment:

- Mac: `~/Library/Application Support/SHOBS-P/pip_log.txt`
- Linux: `~/.local/share/SHOBS-P/pip_log.txt`

Each setup attempt starts with a line `=== <date> <the Python used>`. When the setup fails, the Terminal names this
file. Attach it to your problem report. (On a Mac, the `Library` folder is hidden in Finder: in Finder choose **Go →
Go to Folder…** and paste `~/Library/Application Support/SHOBS-P`.)

## Starting with the console

**run_windows.bat** in the program folder starts SHOBS-P with a console window. It checks the Python packages first,
and if SHOBS-P cannot start, the console shows why and stays open (*"The app did not start. Leave this window open
and copy the message above."*). Use it when the desktop shortcut does nothing.

On Linux and the Mac, SHOBS-P always starts from a terminal (`./run_linux.sh`, or the Terminal window that **Start
SHOBS-P.command** opens), so any error is shown there: *"SHOBS-P did not start, or stopped with an error. Copy the
message above."*

## Typical problems

### Starting

| Problem | Fix |
|---|---|
| Double-clicking the shortcut does nothing. | Start **run_windows.bat** and read the console. If the Python SHOBS-P used was removed or upgraded, run **Create shortcuts.bat** again: it finds a working Python and rebuilds SHOBS-P's own environment. |
| *"SHOBS-P could not find a Python it can use."* | Each Python it tried is listed above that line with the reason. Install Python 3.11 or newer from python.org (tick **Add python.exe to PATH**), then try again. |
| The shortcut stopped working after you moved the folder. | Run **Create shortcuts.bat** again from the new place. |
| *matplotlib is not installed.* where the image should be. | The packages did not install. Run **run_windows.bat**, which installs them, and read any error it shows. |
| Linux: *"These Pythons are missing Tkinter, the window toolkit"* | Install the package it names (Debian, Ubuntu, Mint: `sudo apt install python3-tk`; Fedora: `sudo dnf install python3-tkinter`; Arch: `sudo pacman -S tk`), then run `./run_linux.sh` again. |
| Linux: *"SHOBS-P could not make its own environment …"* and a line about the venv package | Debian, Ubuntu, Mint: `sudo apt install python3-venv`, then try again. |
| Linux: *"This is a Wayland session without XWayland (DISPLAY is not set) …"* or *"No screen found (DISPLAY is not set) …"* | Tk needs X to draw its windows. Install XWayland (package `xwayland`) or log in with an "on Xorg" session; or start SHOBS-P from your desktop session, not over a plain SSH login. |
| Mac: macOS refuses to open **Start SHOBS-P.command** (a file from an unidentified developer, or one Apple could not check). | Right-click (or Control-click) it, choose **Open**, then **Open** again. Once is enough. On macOS 15 and newer, if there is no **Open**, press **Open Anyway** in System Settings → Privacy & Security. Or type `xattr -d com.apple.quarantine *.command *.sh` in Terminal in the SHOBS-P folder; that clears every start file at once (also **Run self-test.command**). |
| Mac: *"✖ SHOBS-P could not find a Python it can use …"* | Install Python 3.13 from python.org/downloads/macos (it includes Tk) and double-click again. A Homebrew Python needs its Tk package; the message names it (for example `brew install python-tk@3.13`). |
| Linux or Mac: *"⚠ Some packages SHOBS-P needs have no ready-made copy for Python 3.15.x yet …"* | Not an error: that Python is too new for some packages. SHOBS-P tries the next Python by itself. If it was the only one, install Python 3.13 (Mac: from python.org; Linux: 3.13, 3.12 or 3.11 with Tkinter) and start again. You do not need to remove the newer Python. |
| Linux or Mac: *"✖ SHOBS-P could not set up its packages with any Python on this computer …"* | Read the lines above it: each Python tried is listed with the reason. Usually installing Python 3.13 fixes it. If not, report the problem with `pip_log.txt` attached (see above). |
| Linux or Mac: *"✖ The packages could not be downloaded. Check the internet connection and try again."* | Check the internet connection (and any proxy or firewall), then start again. |
| Mac: a window asks to install Apple's "command line developer tools". | SHOBS-P 2.2.20 and newer never compile anything, so this should not happen. If you see it while SHOBS-P sets up, choose **Cancel**, install Python 3.13 from python.org, delete the `SHOBS-P` folder in `~/Library/Application Support` and start again. Report it with `pip_log.txt`. |
| Mac: SHOBS-P closed when you closed the Terminal window. | Keep the Terminal window open while SHOBS-P runs. |
| *"SHOBS-P is being set up in another window …"* (Linux, Mac) | A second start while the first setup was still running. Wait for it to finish, then start again. |
| The install seems broken after an update or a Python upgrade. | Start afresh: delete SHOBS-P's own environment folder and start again (chapter 2, "Starting afresh"). Your settings are kept. |
| The window runs off the screen, or text is cut. | Maximize the window. On a scaled display (125–150%) the lower rows of the Output pages need the full height. In a narrow window (for example half the screen), the Input page's boxes and the Photometry page's **View** box wrap onto more rows instead of hiding. |

### Input and calibration

| Problem | Fix |
|---|---|
| The folders are empty every time you start. | That is by design: folders are not remembered. Your site, optics and observer code are. |
| A folder shows *0 files*. | SHOBS-P reads FITS (`.fits`, `.fit`, `.fts`, also compressed) and XISF. Save camera RAW, TIFF or JPEG frames as FITS. |
| A monochrome camera's frames look like a checkerboard. | The Bayer pattern is wrong. On the Calibrate page choose **MONO** (SHOBS-P sets it by itself from raw flats, or from the lights' sky background when there are bias or dark frames or the sky is bright; otherwise it asks **Color or monochrome camera?** at **Scan folders**). |
| Calibration takes a long time every night. | It should reuse saved masters. If your flats or darks change every night, that is expected. |
| Results look worse after replacing calibration frames. | Tick **Rebuild masters (ignore saved ones)** once. |
| You have no bias frames, only dark flats. | Put the dark flats in the **Dark flats** row and leave **Bias** empty: they stand in for the bias (chapter 7). If they sit in the Flat folder, SHOBS-P finds them there by their IMAGETYP or file name. |
| The Input page says *"⚠ No bias and no dark flats: darks can't be scaled correctly …"*. | Add bias frames or dark flats, or use darks at the lights' exposure. Without either, a scaled dark takes off the wrong amount of the camera's offset. |

### Solving and stars

| Problem | Fix |
|---|---|
| **1 Not solved**. | Press **Solve again** to see the diagnosis. Check **Focal length (mm)**, **Pixel size (µm)** and **Binning** on the Input page; a wrong plate scale is the usual cause. Check that RA/Dec are near the field. |
| Frames from another telescope do not solve. | Set that telescope's focal length and pixel size in the Optics box. |
| The check star (or a comp) reads much fainter than its catalog magnitude, or scatters a lot. | The marker is probably a few pixels off the star's center, so every frame is measured beside the star. Before each run SHOBS-P warns in the log (*"⚠ Check marker at … is … px from the center of the star there …"*). Clear the marker, zoom in, and click the star again. |
| A star's name and magnitude do not fill in when clicked. | The image is not solved yet, or the star is not in the catalogs. Type the magnitude yourself. |
| The image is too small to click stars. | Wheel to zoom, drag to pan, or **Pop out** the image into its own window. At 125–150 % display scaling a small frame (MicroObservatory) can stay small even after the log shrinks; the line **⚠ Small image? …** under the image has a **Pop out** button, and **Hide log** gives more room. |
| The AAVSO sequence is missing (*"AAVSO sequence did not answer (…)"* in the log). | The AAVSO chart server (VSP, apps.aavso.org) was slow or down. SHOBS-P asks again in the background; if the log later says it answered, press **Solve again**. Otherwise **Solve again** later. |
| The target marker is not placed, and the log says the target is not on this frame. | The first frames may miss the target; SHOBS-P looks at the next frames for it (chapter 8). If it cannot find it, reject the frames without the target in Blink and calibrate again. |
| A lookup ends with *"✖ …: SIMBAD and VSX did not answer within 60 s …"*. | The service is slow or down, or the internet is. Type RA/Dec yourself, or press **Look up again** later; a late answer is still used. |
| The solve or lookups fail at night. | The catalogs and lookups need the internet. Calibration and photometry do not. Catalog stars fetched for a field are kept for 30 days, which often lets the same field solve again without the internet. |
| The log or status line says *"… from cache (fetched 12 Oct 2026); the live service did not answer (503)."* | A service was down, so SHOBS-P used its last good answer (see "When a service is down" below). Nothing to do; check the values look right, or try again later for a live answer. |
| The variable-star check says *"VSX not reached"*. | VizieR (CDS, then CfA) and AAVSO's own VSX could not be reached, and no earlier answer for this field was saved. Only SIMBAD was checked, so a variable that is only in VSX is not marked. **Solve again** later. |

### When a service is down

SHOBS-P asks several online services: SIMBAD and VSX (Star ID lookup and the variable-star check), VizieR (catalogs),
AAVSO's chart server (the sequence and chart ID), and the NASA Exoplanet Archive (planets). Every time one answers,
SHOBS-P keeps that answer in your user folder (`.shobs_p_cache\lookups`, chapter 22). When a service does not answer
later, SHOBS-P uses the kept answer for the same star, field or planet and says so in the log and on the page, for
example *"AAVSO sequence from cache (fetched 12 Oct 2026); the live service did not answer (503)."* A live answer
always wins and replaces the kept one.

- **Star ID lookup**: RA/Dec, VSX period and AUID come from the kept answer; the status line ends with the note.
- **AAVSO sequence**: the kept sequence and its chart ID are used, and the chart ID still goes in the AAVSO report.
  The note is in the log and the status line only; the report itself is not changed (WebObs format).
- **Variable-star check**: VizieR is asked first (CDS, then CfA; the Cape Town mirror does not carry VSX), then
  AAVSO's own VSX, then the kept list. A server that just failed is asked last for the next 10 minutes, so a dead
  server costs one wait, not one on every solve.
- **Planet lookup**: the kept planet values are used; the line under the planet boxes ends with the note, and the
  transit report's ephemeris line says *"from cache (fetched …)"*. A star with several planets still needs the live
  Archive to list them.

To start fresh, press **Clear cached catalogs** on the Credits page (chapter 3).

### Results

| Problem | Fix |
|---|---|
| Error bars much larger than the scatter (χ²ν far below 1). | Usually the camera gain: the EGAIN keyword is missing or wrong in your headers. The photometry log's *Photon noise* line says which gain was used. On camera-binned frames, check that line's second sentence: SHOBS-P 2.3.0 tells from the sky's noise whether the camera summed or averaged the binned pixels (chapter 7). Nights reduced with 2.2.21 or earlier keep their old error bars until you run photometry again. |
| The comps are much fainter than the target, and the points are noisy. | Read the Stack row's line on the Photometry page. If it says *"⚠ Comps are weak …"*, press **Stack** (chapter 8, "Stacking when comps are weak"), or choose brighter comps. |
| Frames are missing from the light curve after **Use comps…** dropped a comp. | Those frames were skipped at photometry because of that comp. Press **Measure skipped frames** on the Photometry page's orange line (chapter 8). |
| A comp near the edge cost many frames in an older version. | Run photometry again in 2.3.0: a comp off the frame edge is now left out of those frames only (chapter 8, "Comps near the frame edge"). |
| Steps between nights in a series. | Different comps, apertures or equipment between nights. Read the **Careful:** lines when adding a night, and use **Use comps…** to put every night on the same comps. |
| A step at the meridian flip. | Look at **Comp health…**: a comp that jumps at the flip is a common cause. Read the log after the run: a line *"⚠ After the meridian flip the … apertures were off their stars …"* means an aperture problem, not the stars (chapter 12). Try **Pier side as segments** (Variables mode). |
| One star's comps turned up on another star's night (points flagged `nocomp`). | Press **New reduction** on the Input page before you start on another star (chapter 5). |
| Frames on an alt-az mount are lost or misaligned through the night. | Set **Mount** to **Alt-az** in the Optics box (Input page), so the alignment follows the field rotation. |
| The run warns *"⚠ The field turned by 180° between frames … — that looks like a meridian flip."* | **Mount** is set to **Alt-az**, but the frames come from a German equatorial mount (often a setting left from another telescope's data). Set **Mount** to **German equatorial** and run photometry again. |
| A transit's depth is far off on a night with a meridian flip. | Check that **Mount** is **German equatorial**, so the fit can test a flip step, and read the **Flip step** row (chapter 17). Keep 30 minutes or more of baseline on each side of the flip. |
| The transit fit does not start. | It needs a planet on the Input page and at least 20 usable points. Look at the hint under the page title. |
| The Output pages show an old result. | Run photometry again: an old transit fit is cleared as soon as the data change. |
| You lost a night from a series. | Close the series and load `<name>_prev.cvseries`, the version from before this session. |

## Reporting a problem

Problems and ideas go to the project's GitHub Issues page (github.com/SHOBS-S/SHOBS-P/issues). The forms ask for:

- the SHOBS-P version (bottom of the sidebar, for example **Version 2.2.21**),
- your computer's system (Windows, macOS or Linux, and which version),
- your camera, and whether it is color or monochrome,
- the mode and the step,
- what you expected and what happened,
- a screenshot,
- `selftest_log.txt` from the self-test, and `.shobs_p.log` from your user folder,
- for a setup problem on Linux or a Mac, `pip_log.txt` (see above).

A copy of the program's own log (from the Calibrate page) around the time of the problem helps too.

---

Previous: [23. Messages explained](23-messages-explained.md) · Next: [25. Glossary](25-glossary.md)
