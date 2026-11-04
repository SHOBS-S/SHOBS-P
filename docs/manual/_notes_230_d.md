# 2.3.0 manual notes, builder D (#50, item 10)

Both items are S (shared: Variables, Transits, Discovery all use the same photometry run).

## #50: a comp drifting off the frame edge no longer throws away frames

**What changed in a run (Run photometry, Photometry page)**

- A comp whose aperture is partly off the frame, whose sky ring is mostly (over half) off the frame, or that has no
  flux above sky is **left out of that frame only**. The frame is still measured with the other comps. Before, the
  whole frame was skipped. (TrES-3 b, 5 Jul 2025: 64 of 206 frames were lost, and frames with a half-clipped comp
  went into the transit fit.)
- A clipped comp is never measured. Its value is not kept, so it cannot leak into the fit.
- **No step in the light curve:** in a frame without some comps, the comparison is rebuilt from the comps that are
  there. Each of those comps is first put on the night's scale using its own median offset from the others. The
  comparison level therefore stays the same when a comp leaves or comes back; the frame is only a little noisier.
  Frames that have every comp get exactly the plain mean, as before.
- **Rule for the whole night:** a comp measured in fewer than half of a night's frames (or in fewer than 5) is left
  out of that **whole night**. The night uses the other comps from start to end. The log says so.
- **Check star off the edge:** that frame has no check value (check columns empty), and the frame is kept.
- **Spares and watch stars off the edge** (the 'comps under' spares from Use comps, #34): they are simply not
  measured in that frame. They never cost a frame.
- **Target off the edge:** the frame is still skipped, and the skip is now listed (see below).
- **Every comp lost in a frame:** the frame is skipped with the reason "every comp lost (off the frame edge)".

**What the user now sees**

- **Photometry page:** a new orange line under the "Measured with r … " line. It appears only when there is
  something to report, for example:
  - `⚠ 16 frames skipped: every comp lost (off the frame edge) (frames 25–40).`
  - `C3 left out of 14 frames (off the frame edge; frames 27–40); those frames use the other comps.`
  - `check star left out of 18 frames (off the frame edge; frames 23–40); no check value there.`
  - `… out of the whole night (missing in most frames)` when the night rule applied.
- **After the run:** a warning box lists the same lines. The log has them too, prefixed with the night (JD…).
- **Use comps…:** when the new choice drops a comp that caused skipped frames, the Photometry page line says:
  `⚠ 64 frames were skipped because of APASS J175248.7+373202, which is no longer a comp — Run photometry again to measure them.`
  A **Measure skipped frames** button appears at the right of that line. The log and the status line say it too.
- **Measure skipped frames:** measures only those frames, with the comps now in use, and adds them to the night
  (the series is auto-saved if it has a file; in Transits mode the fit is refreshed). It needs that night's Lights
  folder on the Input page and its image loaded. Otherwise a box explains this and says to use Run photometry.
  Once the frames are back, the line and the button go away.
- **Settings file** (`<report stem>_settings.txt`, Transit fit page): a new row,
  `Frames skipped at photometry (reason: frame numbers): every comp lost (off the frame edge): 158-213`
  (or `none`). The old row "Frames left out (flag: frame numbers)" is unchanged; it lists frames the fit left out
  because of their flags.
- **Use comps… question:** frames missing only some of the chosen comps are no longer counted as "will be flagged
  nocomp". Only frames with none of them are (or nights that never measured a chosen star). The log line "Comps in
  use: … N points re-derived" adds "(M of them from the comps present in that frame)".

## Item 10: a ~180° field turn on an alt-az night; the settings file's 'measured:' list

**Warning.** When the Mount is Alt-az (Optics box, or the headers say so) and the field turns by about 180° between
two frames, the run says:

`⚠ The field turned by 180° between frames 314a and 1b — that looks like a meridian flip. Is the Mount set right (German equatorial)? An alt-az field turns smoothly, not by half a turn between two frames. If it was a flip, set Mount to German equatorial in the Optics box (Input page) and run photometry again, so the frames after the flip are treated as after it.`

It shows on the Photometry page's orange line, in a warning box after the run, and in the log. The settings file
gets a `Warning: field turned 180 deg between frames … on an alt-az night (a meridian flip? check Mount)` row. On such
a night, the old "Field rotation: the field turned by 180.7° … (alt-az mount)" line is no longer written.

**The rule:** frames are taken in time order (ASIAIR restarts its numbering after a flip). A gap is called a flip when
the field turns by at least 150° in that one gap **and** by at least 90° more than the night's own rotation rate
explains for that gap's length. A real alt-az field turns smoothly (well under a degree per frame), so ordinary field
rotation never triggers it, even across a long gap or near the zenith.

**'measured:' comp list** (settings file, row `Comps:`). What 2.2.21 did wrong: it printed the night's stored comp
list, or else the series' current comp list. That list could name comps from other nights. It left out the series
comps that this night measured as spares (the "comps under" from Use comps), and it said nothing about comps missing
from some frames. Now it lists only what this night really measured:
`Comps: C1, C2 (Use comps choice; measured: C1, C2, C3 (missing in 14 frames: off the frame edge), SP (as spare))`.
A comp that no frame of the night measured is not listed. Without a Use comps choice, the row gets the
`(measured: …)` part only when it adds something.

## Manual screenshots to retake

- **Photometry page** after a run that left a comp out of some frames: the new orange line under "Measured with …".
  The best example is the TrES-3 b night (Use comps without the edge comp), which shows the "no longer a comp" line
  and the **Measure skipped frames** button.
- **Use comps…** chapter: the Photometry page after Use comps drops a comp that caused skips (same shot as above).
- **Transit fit → settings file** example text: the new "Frames skipped at photometry" row and the `measured:` list.
- **Mount / alt-az section:** the meridian-flip warning box (HD 189733 b 23 Jul with Mount = Alt-az is the real case).
- Any shot of the "Use comps… nocomp" question box: its count is now smaller (frames missing one comp are kept).

## Review fixes

- **Use comps… with one comp that is in fewer than half of a night's frames:** when that comp is the night's only
  comp in use, it is no longer left out of the whole night (which left the night with no magnitudes). It is kept and
  used in the frames where it was measured; the other frames are flagged nocomp, as in 2.2.21. The Log says:
  `<night>: <comp> measured in fewer than half the night's frames but kept (no other comp in use there): only the
  frames where it was measured get a magnitude; the others are flagged nocomp.` With other comps in use the rule is
  unchanged (the comp is left out of that night and the others carry it).
- **Measure skipped frames on a night that shows stacked points:** see _notes_230_a.md, Review fixes (the night
  switches to Points: Per frame first; a stacked night from a saved series gets a message).
- High-DPI: the orange skipped-frames line and the "Measured with …" line wrap with the window at any display
  scaling; the Input page's "Output: …" and "Dark flats (optional …)" hints wrap at the page's width.
