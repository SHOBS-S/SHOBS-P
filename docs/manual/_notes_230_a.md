# 2.3.0 builder A: notes for the manual writer

Item 1 = GitHub #54 "Stacking when comps are weak", all modes (S). This page says what the user now sees and does. The manual itself was not edited.

## Where it is

The **Photometry** page has two new parts. Neither shows until the first run (Run photometry, or Scan field in Discovery) has made points:

1. **Points box**: a small box titled **Points**, just left of the **View** box. When the window is narrow and the View box moves to its own line, the Points box moves with it.
   - Two buttons: **Per frame** and **Stacked**. **Stacked** is greyed out until stacks exist for the night.
   - Under them, a plain line: **Results use: Per frame** or **Results use: Stacked (N frames)**. It wraps onto two lines when it is wider than the two buttons.
2. **Stack row**: under the settings row (Aperture, sky, raw sat ADU and so on) and above the "Measured with r …" line.
   - First, the **suggestion line**. In orange with ⚠ when stacking is advised, grey otherwise.
   - Then the controls: **Stack N** [box] **frames   Precision goal** [box] **ppt per point (optional)** and the **Stack** button.

The **Output** page (Variables, and Transits' Variable View) shows the same **Results use: …** text at the right end of the row with "Airmass-corrected night means". It is empty until the night has been stacked.

## What the suggestion line says

After a per-frame run, SHOBS-P checks the noise of every good frame. It compares the target's photon noise with the photon noise of the comp ensemble. Both come from the run's own error model.

The Log gets one line, for example:
`Noise check (per frame): target 5.4 mmag (SNR 202), comp ensemble 21.5 mmag (SNR 50; each comp 36, 35) over 60 frames; comp noise 4.01× the target's.`
The suggestion line is logged too.

Possible lines (N and the numbers are examples):

- When the comps dominate, that is, the comp noise is larger than the target's:
  `⚠ Comps are weak (comp noise 4.7× the target's per frame). Stack 20 frames (60 s each) → comps SNR ~193; 200 frames → 10 points?`
  One or more of these notes may follow:
  - `Capped at 57 frames by the time resolution (1/50 of the 2.40-h period = 2.9 min); the comp rule wanted 100.`
  - `Time cap: 5.0 min, a conservative default: no period known (VSX gave none).` (Transits: `2 min, a conservative default: no ingress/egress known (enter the planet on the Input page and Lookup)`.)
  - `Kept to 20 frames so the night still has at least 10 points.`
  - `The 2 ppt goal is not met (about 2.6 ppt per point).`
  - `(3 frames left out of stacks: 2 sat, 1 cloud.)`
- When the comps dominate but the time cap allows no stacking:
  `⚠ Comps are weak (…), but the time resolution allows no stacking (…). Brighter comps, or longer exposures, would help.`
- When the comps are fine but a precision goal is typed and not reached per frame:
  `⚠ Per-frame points are about 3.1 ppt; your 2 ppt goal needs stacks of 3 frames (9 s each); …`
- When the comps are fine:
  `Comps are fine per frame (comp noise 35% of the target's): stacking is not needed. Type N to stack anyway.`
- Discovery has no comps, so the line is never orange:
  `Stacking (optional): stacks of 6 frames (18 s each); 60 frames → 10 points. Time cap 5.0 min: a stack hides events faster than itself.`

### How N is chosen (for the manual's explanation)

- **Default rule:** the smallest N at which a stack's comp-ensemble noise is no more than **half the target's noise in one frame**.
- **Precision goal** (optional, blank by default, in ppt per point): N grows until a stacked point's whole error meets the goal. The whole error includes scintillation.
- **Time cap**, so the stack does not blur the signal:
  - **Transits:** 1/5 of the ingress/egress duration, from the Input page planet (period, a/R*, Rp/R*, b, or T14).
  - **Variables:** 1/50 of the VSX period.
  - **Discovery:** 5 minutes, with the note that a stack hides events faster than itself.
  - **Without a planet or period:** 2 min (Transits) or 5 min (Variables), and the line says this is a conservative default.
- **Never fewer than 10 points** from the night.
- The **N box** fills in with the suggestion. Typing your own N keeps it. Typing a precision goal fills in the new suggestion.

## Stack

**Stack** first asks only when needed:
- Stacks longer than the time cap: "Stacks of N frames last about … longer than the time resolution allows (…): they may blur the signal. Stack anyway?"
- Transits with fewer than 20 stacks: "That makes only K stacked points; the transit fit needs 20 or more. Stack anyway?"
- Discovery with fewer than 10 stacks gives an error: "a field scan needs at least 10. Use a smaller N."

Then two things happen on the busy bar:
1. **Stacking… k of K.** Each stack is a sigma-clipped mean of N calibrated frames.
2. **Photometry on K stacks…** The same photometry runs on the stacks. In Discovery, the field scan runs on the stacks instead.

Log lines (examples):
- `Stacking 200 frames into 10 stacks of up to 20 (sigma-clipped mean; whole-pixel shifts from the measured drift; never across a meridian flip or a gap).`
- `Stacks made: 200 frames → 10 stacks of 20 frames (effective exposure 40 s); 0.012% of the pixel values clipped (cosmic rays, satellite trails, hot pixels); shifts up to 2 px.` When some stacks are short, this is added: `The last stack of a run (before a flip, a gap, or the end) can be partial.`
- When the field turns: `Note: the field turned by up to 0.30° inside a stack (about 1.4 px at the frame's corners) …`
- `Photometry on the stacks done: 10 points (stacks of 20 frames (effective exposure 40 s)), 0 flagged. Points: Stacked is selected; Per frame (beside the View box) switches back.`
- **Aperture check on the stacks:** the stacks' star FWHM against one frame's, with the suggested r for each. When the stacks' stars are over 15 % wider, the line starts with ⚠ and says to run **Suggest…** with Points: Stacked. That test then measures the stacks. If you choose a new aperture, press **Stack** again. The stacks are reused, so only the photometry is redone.

After a stack run the app goes to Output, with **Points: Stacked** selected.

### Rules the manual should state
- Rejected frames are not in the run.
- Frames flagged by the per-frame run are left out before stacking: sat, compsat, drift, cloud, and the other flags.
- Saturation is per exposure, so a sum of unsaturated frames stays linear.
- A stack never spans a meridian flip, a rotator move, or a gap of more than 3 frame spacings. Each pier side is stacked on its own, so the last stack before a flip or gap can be partial.
- One combine method only: a sigma-clipped **mean**, with a plain mean below 5 frames. There is no median, no weighting or normalizing, and no drizzle.
- Shifts are whole pixels from the drift the run already measured. On a color camera they are whole 2×2 Bayer cells.
- Stacks are 32-bit floating point, with no 16-bit ceiling.
- Each stack records:
  - its mid-time: the mean of its frames' mid-times
  - its frame count N
  - its exposure sum, which is the effective exposure
- Stacks are kept in the SHOBS-P cache folder (`~/.shobs_p_cache/stacks`), never in the Lights folder. Only the newest set is kept, because each stack is one full frame.

## The Points switch

- **Per frame / Stacked** swaps which points are the night's points in the series, instantly. Everything downstream uses the chosen set:
  - Output page
  - period search and Time of max/min
  - Comp health
  - AAVSO report and light-curve CSV
  - Save series
  - Transit fit, and the ExoClock/EXOTIC/ExoFOP files
- The **Results use** line on the Photometry page and on the Output page always says which set that is.
- The transit fit of each set is kept. Switching back shows the earlier fit at once ("✔ Fit of the stacked points (kept from before)."). A set never fitted is fitted when the Transit fit page is on screen.
- A Use comps… choice is applied to whichever set is switched in.
- With a series file open, a switch auto-saves the series with the chosen set, as a run does.
- If a series is loaded or closed, or the night is removed, the switch goes back to Per frame and Stacked greys out. Only the chosen set is stored in the series file.

## Reports, settings, series

- **AAVSO report NOTES:** `points are stacks of 40 frames (effective exposure 30 s)`. It is added after any typed notes, separated by "; ". The CSV's exptime column holds the effective exposure. Its file and frame columns name the stack file (stack_0003 → frame 3).
- **Transit settings record** (`<report>_settings.txt` and the Log): a line `Stacking: stacks of 40 frames (effective exposure 30 s) (sigma-clipped mean, whole-pixel shifts, never across a flip)`. The report's `SHOBS-P settings:` NOTES part gets `points are stacks of 40 frames (effective exposure 30 s)`. The fit's exposure is the effective exposure.
- **Series file:** a stacked night's setup carries `"stack": {"n", "stacks", "frames", "partial", "smallest", "exp", "frame_exp", "turn", "clipped", "shift"}`. That is the stack size and effective exposure. A per-frame night has none.

## Manual screenshots to retake (house rule)

- **Photometry page** after a run in each mode. The new Points box sits left of the View box, and the Stack row sits under the settings row. Shoot one with the ⚠ weak-comp line and one with "Comps are fine".
- **Photometry page** with Points: Stacked and the Results use line, to show the narrow-window layout where the Points box shares the View box's line.
- **Output page** (Variables): the "Results use: Stacked (N frames)" text at the right of the Airmass row.
- **Discovery** Photometry page with the optional Stacking line.
- The **Log** pane after Stack (the Stacks made / Aperture check lines) if the manual shows Log text.
- Any manual page that shows the whole Photometry settings row. It now has the Points box, so the row's layout changed.

A new manual section is needed (the issue asks for one): "Stacking when comps are weak". It should explain why, the suggestion line, N, the cap, the Points switch, what reports say, and that stacking hides changes faster than one stack.
