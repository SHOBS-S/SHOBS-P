# 2.4.0 step 1 (Output pages) screenshots

2.4.0 step 1 regroups the controls on both Output pages (#98): seven labelled groups in six rows above the plot (Comps and Analysis share a row, John 16 Nov) (Data, Points,
Corrections, Fit, Comps, Analysis, View) and the save area under it. Every shot that shows either Output page's
controls has changed and must be retaken (house rule: never reuse an image of a page that changed). Windows, dialogs
and saved files that did not change are reused. Listed from the code at the s1-output branch; nothing is reshot yet.

Step 1 review (16 Nov): both Output pages lost a few pixels of vertical padding (the hairlines between the group
rows, around the plot, above the Results row), so the plot is a little taller; the Period box's title reads
**Period: updated** while it is highlighted. Every shot of these pages is on the RESHOOT list below already; take
them from the build with the review fixes in.

John, 16 Nov: the two lines under the night table (Variables) and the two under the Results row (Transit fit) take
room only while they have text. Shots of either page with those lines empty change by a line or two of height; they
are on the RESHOOT list already.

| Shot | What changed in it | Action |
|---|---|---|
| img/09-output-page.png | Variables Output page: six group rows above the plots (Comps + Analysis share one); one Results row under them (Save series, Save AAVSO report, Notes, …); Refit period green in the Fit row | RESHOOT |
| img/10-series-output.png | same page with a series | RESHOOT |
| img/10-night-zoom.png | same page, one night clicked (check whether the crop shows the control rows; reshoot if it does) | RESHOOT if the rows are in the crop |
| img/12-flagged-points.png | Output page light curve (reshoot if the control rows are in the crop; the "Leave flagged points out (sat, drift, cloud)" label is new) | RESHOOT if the rows are in the crop |
| img/13-period.png | Period box, phase plot, periodogram (reshoot if the Period search row is in the crop: it is now the Fit row) | RESHOOT if the rows are in the crop |
| img/15-no-auid.png | the No AUID line under the Results row: the row now starts with Save series and has the Notes box | RESHOOT |
| img/15-webobs-offer.png | Output page after a report save: Results row changed; Save field image now reads "Field image saved ✓" (grayed) | RESHOOT |
| img/17-transit-fit-page.png | Transit fit page: six group rows (Comps + Analysis share one); Fit the series' nights… in the Analysis row; Refit with flagged frames in the Points row | RESHOOT |
| img/17-flip-step.png | Transit fit page on a flip night (same new rows) | RESHOOT |
| img/17-trim.png | the Trim controls are now the Points row | RESHOOT |
| img/17-exoclock-buttons.png | Results row: Fit the series' nights… is gone from it | RESHOOT |
| img/18-inconclusive-verdict.png | Transit fit page (reshoot if the control rows are in the crop) | RESHOOT if the rows are in the crop |
| img/19-exotic-side-by-side.png | Transit fit page with a report: Close report and "meridian flip at the largest gap" in their new rows | RESHOOT |
| img/03-window-parts.png | reuse if it shows the Input page (it does in 2.3.6); reshoot only for the version text at the 2.4.0 release | reused (check at release) |
| img/11-comp-health.png, img/11-use-comps.png, img/14-extremum.png, img/21-watch-stars-window.png, img/15-notes-question.png, img/12-transmission-keep.png, img/17-multinight-window.png, img/17-multinight-corner.png, img/19-field-image.png | separate windows / saved files, unchanged | reused |
| all others | pages not touched by step 1 | reused (unless another 2.4.0 step changes them) |

New shots worth adding (not in the manual yet):

| Shot | Shows |
|---|---|
| img/17-refit-needed.png | Transit fit page with "Refit (settings changed)" on the Fit button and the fit's numbers gray (chapter 17) |
| img/17-no-airmass.png | Corrections row with airmass grayed out and "no airmass: site not set" (chapter 17) |
| img/09-field-image-saved.png | Results row after Save AAVSO report: "Field image saved ✓" grayed out (chapters 9 / 15) |

## Done 17 Nov 2026 (build-240 9d8e660, example site 40/−100, Claude via computer use, 2404×1775)

RESHOT: 09-output-page (SZ Lyn 13–14 Oct, Bin 2, per frame: the old shot was stacked 4, nothing in ch 9 depends on it),
10-series-output (scrubbed HD 219134 example series, Bin Night), 10-night-zoom (6–7 Oct clicked), 12-flagged-points
(crop of the SZ Lyn page: rows are in it), 13-period (crop of the HD 219134 page: rows are in it), 17-transit-fit-page
(TOI-2046 b 20–21 Oct, same numbers as 2.3.6), 17-exoclock-buttons, 17-trim (19:16–19:37), 17-flip-step (HD 189733 b
0723 reduced fresh at 203 mm: depth 2.65 %, flip step −20.0 ± 1.4 ppt kept, ΔBIC 373; validation 2.64 / −20.2),
18-inconclusive-verdict (K2-113 b report, same numbers), 19-exotic-side-by-side (TrES-3 b EXOTIC 04-SEP, Tc .64756 as
before; see nit below). NEW: 17-refit-needed.

LEFT: 15-webobs-offer and NEW 09-field-image-saved: apps.aavso.org answered 500 for the AAVSO chart all session, so the
Results row shows "Fetch AAVSO chart" instead of "Field image saved ✓"; retake once the chart comes back.
15-no-auid: ASASSN-V J185009.93+281035.4 now has an AUID, so the No AUID line no longer appears; needs another star
without an AUID (or keep the 2.3.6 shot with a note).
SKIPPED (optional): 17-no-airmass: with the TOI-2046 series loaded, airmass stayed available after blanking the site
(the series carries each night's site), so the grayed state did not show.

NITS FOUND: (1) after closing an EXOTIC/SHOBS-P report and reopening another, the TOI-2046 b TOI and ExoClock lines stay
under the Results row (they belong to the photometry night, not the report); visible in 19-exotic-side-by-side, so
retake that shot after the fix. (2) With the K2-113 b report still open on the Transit page, New reduction + HD 189733 b
on the Input page, Run photometry warned "No transit of K2-113 b is predicted during these lights": the run used the
open report's planet, not the Input page's. The report page also still said "Your TOI-2046 b photometry is kept".
