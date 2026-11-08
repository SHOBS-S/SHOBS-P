# 2.3.2 manual: screenshots to reshoot

House rule: reshoot every shot whose page or window changed; reuse shots where nothing on them changed. Never show
the exact observing site. The sidebar's version line alone does not trigger a reshoot (as for 2.3.1).

| # | File | Chapter | What changed on it | State to set up |
|---|---|---|---|---|
| 1 | `img/08-photometry-solved.jpg` | 08 | The "Wheel to zoom, drag to pan … Pop out" line is gone; the status rows above the image are tighter, so the image is taller. | Variables, SZ Lyn (as before), solved, target marked, Comps step. |
| 2 | `img/20-discovery-photometry.jpg` | 20 | Same Photometry page layout change, in Discovery. | Discovery, after the solve (as before). |
| 3 | `img/17-transit-fit-page.png` | 17 | Tools row has the **Plot bins** box; the page waits for **Fit transit**. | TOI-2046 b 20–21 Oct (as before), press Fit transit. |
| 4 | `img/17-flip-step.png` | 17 | Tools row (**Plot bins**). | HD 189733 b 23 Jul 2025, flip step kept (as before). |
| 5 | `img/17-trim.png` | 17 | Tools row (**Plot bins**). | Transits, drag a trim span (as before). |
| 6 | `img/19-exotic-side-by-side.png` | 19 | Tools row (**Plot bins**); the fit waits for **Fit transit** after the report opens. | Open the EXOTIC report (as before), press Fit transit. |
| 7 | `img/15-webobs-offer.png` | 15 | No WebObs button at the right end of the status bar any more. | Variables, Save AAVSO report (as before). |

## Second round (after 2be501a, the item-10 layout: Aperture line under the Target line, Stack / Measured-with lines
beside the View box, legend column left of the image)

| # | File | Chapter | What changed on it | State to set up |
|---|---|---|---|---|
| 8 | `img/08-photometry-solved.jpg` | 08 | Retake again: legend column, Aperture line position. | As #1. |
| 9 | `img/20-discovery-photometry.jpg` | 20 | Retake again: same layout change. | As #2. |
| 10 | `img/17-transit-fit-page.png` | 17 | Retake again (John's list). | As #3. |
| 11 | `img/17-trim.png` | 17 | Retake again (John's list). | As #5. |
| 12 | `img/08-stack-row.png` | 08 | The Stack row is now in the column beside the Points / View boxes, not a full-width row under them. | Variables or Transits, a per-frame run with weak comps (as in 2.3.0), do not stack. Crop: Aperture line to the bottom of the View box. |
| 13 | `img/20-discovery-stack-row.png` | 20 | Same move, Discovery. | Discovery, Scan field done (as in 2.3.0). Same crop. |

Reused after the second round: `15-webobs-offer.png`, `17-flip-step.png`, `19-exotic-side-by-side.png` (pages
unchanged since the first round); `08-points-stacked.png` (the boxes only, unchanged); `08-popout-window.jpg` (the
pop-out window keeps its legend row and hint).

Checked and reusable (first round): `08-points-stacked.png`, `08-stars-panel.png`, `08-suggest-apertures.png`,
`08-popout-window.jpg`, `17-exoclock-buttons.png` (Results row unchanged),
`03-window-parts.png`, `02-first-start.png` (its Optics values are the example's typed values, not the install
defaults), `05-input-page.png`.

## Third round (8 Nov, after John's look at the Stack row)

The Stack, Measured-with and skipped-frames lines follow the View box onto its own line (left of it, when the Stack
controls fit there whole), and the Points box moved onto the Stack row, after the Stack button. Retaken on the
example site (40 / −100 / 500 m), window 2409 × 1775:

- `08-photometry-solved.jpg` (SZ Lyn, Bin 2, solved, target marked, Comps step), `20-discovery-photometry.jpg`
  (NGC 7789 21–22 Oct, solved), `17-transit-fit-page.png` and `17-trim.png` (TOI-2046 b series, night 20–21 Oct;
  trim 19:16–19:37) — the second-round list.
- `08-stack-row.png` (HD 219134 2 Nov, r 13 / 20–30, scintillation off: "Comps are weak 4.8×", Stack 9 → 26 points,
  as in 2.3.0; nothing saved to the series) and `20-discovery-stack-row.png` (NGC 7789, Scan field, stacks of 4 →
  67 points, as in 2.3.0). Crop: Aperture line to the bottom of the View box.
- `08-points-stacked.png`: no longer reusable (the Points box moved). HD 219134 2 Nov after Stack (9 frames): the
  Stack controls and the Points box with Stacked chosen.

OCR: no user name, observer code or real site in any of them.
