# 2.3.0 manual: screenshots to reshoot or add

One combined list from `_notes_230_a.md` (#54 stacking), `_notes_230_b.md` (#8, #51, #52, Save corner plot),
`_notes_230_c.md` (Dark flats, #53, #55), `_notes_230_d.md` (#50, item 10) and the 2.3.0 chapter text.
House rule: reshoot every shot whose page or window changed; reuse only shots where nothing changed.
Never show the exact observing site in any shot (round or blank the Site box, crop the status line if it shows it).

**NEW** = the file does not exist yet; the chapter already references it under this name.

Total: 22 shots to take (5 new, 11 reshoots or checks for changed content, 6 for the version line only), plus 3 optional
extras not referenced in the text, 2 deleted, and 2 checked and reusable (`11-use-comps.png`, `23-edge-caution.png`).

## A. New shots (referenced in the chapters, files still to make)

| # | File | Chapter | What must be visible | Mode / state to set up |
|---|---|---|---|---|
| 1 | `img/08-stack-row.png` **NEW** | 08 Stacking when comps are weak | Photometry page after a per-frame run: the **Points** box just left of the View box (**Per frame** selected, **Stacked** grayed, "Results use: Per frame"), and the **Stack row** under the settings row with the orange ⚠ *"Comps are weak (comp noise …× the target's per frame). Stack N frames …"* line and **Stack N** / **Precision goal** / **Stack** controls. | Variables or Transits; a night with comps much fainter than the target (Matthew's binned ASI2600MM nights are a good case); Run photometry; do not stack yet. Wide window. |
| 2 | `img/08-points-stacked.png` **NEW** | 08 The Points box | Points box with **Stacked** selected and "Results use: Stacked (N frames)" wrapped under the buttons, in the **narrow-window** layout where the View box (and the Points box with it) moves to its own line. | Same night as #1 after **Stack**; return to Photometry, narrow the window until the View box wraps. |
| 3 | ~~`img/08-skipped-frames.png`~~ DROPPED (John 4 Nov: in 2.3.0 an edge comp no longer skips frames; text reworded, no shot) | 08 Measure skipped frames (also 11 Use comps) | Orange line under "Measured with r …": *"⚠ 64 frames were skipped because of APASS J175248.7+373202, which is no longer a comp — Run photometry again to measure them."* with the **Measure skipped frames** button at its right. | TrES-3 b 5 Jul 2025 night: run with the edge comp included (it causes skips), then **Use comps…** without that comp, back to the Photometry page. A "C3 left out of … frames" line in the same shot is welcome. |
| 4 | `img/17-flip-step.png` **NEW** | 17 A meridian flip in the night | Transit fit page on a flip night with the step **kept**: result line *"…; Flip step fitted: … ppt (ΔBIC …), kept"*, table rows **Flip step (ppt)**, **Depth without step (%)**, **Step–depth correlation**, and the purple dash-dot *"flip: step … ppt"* line on the plot. | Transits, Matthew's HD 189733 b 23 Jul 2025 night, **Mount = German equatorial** in the Optics box; Run photometry, let the fit finish. |
| 5 | `img/20-discovery-stack-row.png` **NEW** | 20 Scan stacks of frames | Discovery Photometry page after **Scan field**: the gray *"Stacking (optional): stacks of N frames (… each); … → … points. Time cap 5.0 min: …"* line, the Stack controls and the Points box. | Discovery, any field with 60+ frames; Scan field; back to Photometry. |

## B. Reshoots (the page or window changed in 2.3.0)

| # | File | Chapter | What changed / must be visible | Mode / state to set up |
|---|---|---|---|---|
| 6 | `img/05-input-page.png` | 05 Input | Frames group now has six rows (**Dark flats** between Dark and Flat) and the new Dark flats hint line under the Output hint. | Variables, folders chosen, target looked up, as the 2.2.21 shot. Dark flats row may be empty or filled. |
| 7 | `img/16-planet-box.png` | 16 Prepare for a transit night | Planet (Transits) box after a lookup: **Tonight's transits…** button gone (#52); no gray "looking up…" (#51); TOI / ExoClock lines. | Transits; type `TrES-3` in Star ID and Enter (the star-name path), wait until the boxes fill. |
| 8 | `img/17-transit-fit-page.png` | 17 Fit a transit | Tools row starts with **Nearby-binary (NEB) check…** (planner buttons and Corner plot… gone), credible bands box; Results row has **Save corner plot…** before **Save field image…**. If shot on a flip night: the flip rows (but #4 covers that; a no-flip night keeps this shot simple). | Transits, a detected transit night (e.g. TrES-3 b or KELT-16 b), fit done. |
| 9 | `img/17-exoclock-buttons.png` | 17 Send it to ExoClock | Results row with **Save corner plot…** added. | Same as #8, crop to the Results row. |
| 10 | `img/26-credits-page.png` | 26 Credits | Citation reads version 2.3.0; credits list without Swarthmore, with "Sun position"; Version 2.3.0 in the sidebar. | Any mode, Credits and sources page. |
| 11 | `img/07-calibrate-done.png` | 07 Calibrate | Log now starts with the *"Calibration: lights = …; flats = …."* summary line (and no "Note: no dark frames…" line when dark flats are given); Version 2.3.0. Ideally a night with dark flats, so "Median-combining N dark flat frames" / "Saved master dark flat …" show. | Variables; bias or dark flats, darks, flats; Build masters and calibrate. |
| 12 | `img/03-window-parts.png` | 03 Tour | Shot is in Variables mode (banner unchanged), but it shows the Calibrate page log (new "Calibration:" line) and **Version 2.2.21**. Reshoot the same layout with the four callouts. | Variables, Calibrate page after calibration, callouts 1–4 added as before. |
| 13 | `img/03-busy-bar.png` | 03 The busy bar | Check only: unchanged unless it shows the version or a planner. Reuse if the strip is as before; optionally retake during *"Looking up TrES-3's planets at the NASA Exoplanet Archive…"* (#51) or *"Stacking… 3 of 10"*. | Transits, Star ID `TrES-3` + Enter, catch the bar. |
| 14 | `img/09-output-page.png` | 09 Read the Output page | Output – Variability page; 2.3.0 adds **Results use: …** at the right end of the Airmass-corrected night means row (shown once stacked) and Version 2.3.0. Shoot with a stacked night so "Results use: Stacked (N frames)" shows. | Variables, one night, after **Stack** (as #1/#2), Output page. |
| 15 | `img/17-trim.png` | 17 Trim | Plot only? If the shot includes the tools or Results row, reshoot with #8's state; if it is the plot alone, reuse. | Transits, drag a trim span. |
| 16 | `img/18-inconclusive-verdict.png` | 18 | Verdict text unchanged for a no-flip night; reshoot only if it was a flip night (the flat line now carries the step: "against a flat line with the flip step") or if the tools row is in view. | Check the shot. |

## Checked, reuse

- `img/11-use-comps.png` (ch 11): the window's layout is unchanged; the nocomp question box is not in the shot.
- `img/23-edge-caution.png` (ch 3, 23): the "near the frame edge or a weak part of the flat" box is unchanged in the
  code. (The new #50 after-run box listing skipped frames is different; see the optional extras if wanted.)

## C. Version line only (window shows "Version 2.2.21"; nothing else changed)

Past releases retook every window that shows the version. These need it only for the sidebar's version text:

| # | File | Chapter | Mode / state |
|---|---|---|---|
| 17 | `img/02-first-start.png` | 02 | First start, as before. |
| 18 | `img/08-photometry-solved.jpg` | 08 | Variables, solved, target/comp/check marked, before a run. |
| 19 | `img/09-marked-stars.jpg` | 09 | As before. |
| 20 | `img/20-discovery-photometry.jpg` | 20 | Discovery, after the solve. |
| 21 | `img/10-series-output.png` | 10 | As before (an eight-night series). |
| 22 | `img/06-blink-page.jpg` | 06 | As before (Lights view). |

(Other full-window shots — 12-*, 13-period, 14-extremum, 15-*, 19-*, 20-field-scan-output, 21-watch-stars-window —
also show the sidebar version if they are full-window; check each and retake with the version line if so. Pop-up
windows without the sidebar can be reused.)

## D. Optional extras (not referenced in the text; add a reference if taken)

- Input page with the new ⚠ lines under the Frames box (*"⚠ No bias and no dark flats: …"* or *"⚠ Dark flats 2 s,
  flats 0.5 s: …"*), for chapter 7's Dark flats section (suggested name `img/07-dark-flats-note.png`).
- The #50 after-run warning box listing skipped frames and comps left out (TrES-3 b 5 Jul 2025).
- The alt-az flip warning box (*"⚠ The field turned by 180° between frames 314a and 1b — that looks like a meridian
  flip …"*), for chapter 8 / 16 (HD 189733 b 23 Jul 2025 with **Mount = Alt-az**; suggested `img/08-altaz-flip-warning.png`).

## E. Deleted

- `img/16-tonights-transits.png` — window removed (#52); file and reference deleted.
- `img/16-transit-planner.png` — window removed (#52); file and reference deleted.

## Log text in the manual

The manual quotes Log text (stacking lines in ch 8, "Calibration:" in ch 7, "Photon noise:" in ch 7) as text, not
screenshots, so no Log shot is needed for those.
