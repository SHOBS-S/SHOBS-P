# 2.3.0 manual notes, builder B (Transits mode: #8, #51, #52, Save corner plot)

For the manual writer. What the user now sees and does, and which pages and screenshots change. House rule: reshoot
every screenshot whose page changed; reuse only those that did not.

## #8: meridian-flip step in the transit fit (fitted, kept only when the BIC says so)

**What changed.** On a night with a meridian flip (German equatorial mount; SHOBS-P already splits the night at the
flip from the frames' ROTATOR / PIERSIDE headers or the image), the transit fit now tests a flux step at the flip:
after the flip the baseline is multiplied by (1 + step). It fits the night twice by least squares, with and without
the step, and keeps the step only when it lowers the BIC by **10 or more** (ΔBIC ≥ 10, the same bar as a detection).
When the step is kept, it is fitted together with the transit in the MCMC, so its uncertainty is part of the depth's.
When it is not needed, the fit is made with no step term at all, as before 2.3.0. An alt-az night (no flip) never
gets the term. The fit never changes the flip time: it is halfway between the last frame before the flip and the
first after it.

Why it matters (for the text): on HD 189733 b (23 Jul 2025, Matthew's 1 m data) the target was 16 mmag fainter after
the flip, and without the term the post-flip baseline pulled the depth down to 14 ppt against the published 24 ppt.

**Where the user sees it.**
- **Result line** (above the Results row), after the main numbers: *"; Flip step fitted: −14.4 ± 0.5 ppt (ΔBIC 629),
  kept"* or *"; Flip step not needed: +0.2 ± 0.6 ppt (ΔBIC −6; kept only at 10 or more)"*. The same line goes to the
  log as *"Transit fit: Flip step …"*. (The step is relative flux in ppt; a target that gets fainter after the flip
  has a negative step.)
- **Log while fitting**: *"meridian flip: step −14.5 ± 0.6 ppt, ΔBIC 628.8 (kept at 10 or more): kept, fitted with
  the transit"* or *"… not needed, fitted without a step"*.
- **Parameter table**:
  - step kept: row **Flip step (ppt)**, e.g. *"−14.4 ± 0.5 (ΔBIC 629, kept)"*, then **Depth without step (%)** and
    **Step–depth correlation** as in 2.2.13 (the row was **Flip step (%)** before; it is now in ppt);
  - step not needed: one row **Flip step**, *"not needed (ΔBIC −6; test step +0.2 ± 0.6 ppt)"*.
- **Plot**: step kept: purple dash-dot line at the flip, label *"flip: step −14.4 ± 0.5 ppt"*. Step not needed: a
  dotted purple line at the flip, label *"flip: no step needed"*.
- **Notes** (page, PNG, fit text): kept: *"Meridian flip at BJD_TDB … (+2.57 h from mid-transit; the frames' flip
  segment): a baseline step there was fitted together with the transit: step −14.4 ± 0.5 ppt (kept: ΔBIC 629, at
  least 10 needed)."*, then the 2.2.13 lines (depth without the step, step–depth correlation, baseline on each side).
  Not needed: *"Meridian flip at BJD_TDB … (+2.57 h from mid-transit): Flip step not needed: … The step is kept only
  at ΔBIC 10 or more, so this fit has no step term."*
- **Fit text file** (ExoFOP package / records): the *Meridian flip* lines, the *Flip step …* line, and **Detrending**
  says *"airmass, meridian flip step"* when the step is kept.
- **AAVSO exoplanet report #NOTES**: *"SHOBS-P: meridian flip at BJD_TDB 2463218.67686; baseline step −1.45+/−0.05%
  (kept, dBIC 629) …"* or *"SHOBS-P: meridian flip at BJD_TDB …; Flip step not needed: … , no step in this fit"*. The
  flip time is now written in both cases, so a later refit of the report (or another program) uses the same time.
  **#DETREND_PARAMETERS** is unchanged (it lists data columns; the step is in NOTES).
- **ExoClock file** and the **fit CSV**: unchanged (raw relative flux, not detrended; the fit CSV already has the
  segment column and the *"# Meridian flip at …"* line, now written whether or not the step was kept).
- **The "meridian flip at the largest gap" box** (shown only for a loaded report) is visible again, since the flip
  step is on. It tells the fit that the report's largest gap is the flip; the same BIC gate then decides.
- **Multi-night fit**: each night gets its own flip step only when that night's own test kept it (same ΔBIC ≥ 10
  rule). The **Nights** part of the summary says per night *"flip step fitted: … ppt (ΔBIC …), kept"* or *"flip
  step not needed (ΔBIC …; kept only at 10 or more)"*. The corner plot shows *step:N* for a kept step.
- The leave-one-comp-out check uses the main fit's decision (step or no step) for every refit.

**Chapters to update**
- **17 Fit a transit** (the parameter table, the notes, reading the plot; a short section "A meridian flip in the
  night": what the step is, the ΔBIC ≥ 10 rule, how to read *kept* / *not needed*, what *Depth without step* and the
  correlation mean). Mention the result-line addition.
- **16 Plan a transit night**, section *Meridian flips*: the bullet "The transit fit tests whether a plain jump at
  the largest gap …" should say that with the flip known (German equatorial mount set in the Optics box), the fit
  tests a step at the flip itself and keeps it when ΔBIC ≥ 10. The planning advice (flip in the baseline, 30 min+ on
  each side) still holds and matters more now: with too little baseline on one side the result is "inconclusive".
- **18 Inconclusive or none**: the gap/jump section: add that on a flip night the flat line in the verdict test also
  carries the step when it is kept ("ΔBIC … against a flat line with the flip step").
- **19 Transit reports**: the NOTES line for the flip (always written now).
- **23 Messages explained**: the two log lines above and the two table/notes texts.
- **25 Glossary**: *Flip step* (transit fit): the multiplicative step at the meridian flip, in ppt, kept only at
  ΔBIC ≥ 10.

**Screenshots**: `img/17-transit-fit-page.png` only if it is retaken on a flip night (the table rows changed for
those); a new shot of a flip night with the step kept (table + plot) would serve the new section. Matthew's HD 189733
b 23 Jul 2025 night (Mount = German equatorial) is the real case.

## #51: busy bar during the star-name planet lookup; no "looking up…" in the ephemeris boxes

**What the user sees now.** In Transits mode, typing the host star in **Star ID** (e.g. *TrES-3*) and pressing Enter
runs the busy bar (green on the dark status strip) the whole time, in three steps with no gap:
*"Looking up TrES-3 at SIMBAD and VSX…"* → *"Looking up TrES-3's planets at the NASA Exoplanet Archive…"* →
*"Looking up planet TrES-3 b at the NASA Exoplanet Archive…"*. The bar goes when the planet boxes are filled.

- The ephemeris boxes (**Period (d)**, **T0 (BJD_TDB)**, **Duration T14 (h)**, **Depth (%)**, **Rp/R\***, **a/R\***,
  **Impact parameter b**, **Star Teff (K)**) no longer show the gray **looking up…**: an empty box stays blank until
  the values come (a value already in a box stays until the answer replaces it).
- When the star has no planet in the Archive (or the Archive did not answer), the line under the Planet box says, in
  red: *"✖ No planets found for <star> at the NASA Exoplanet Archive (none listed under that name, or the Archive did
  not answer). Type the planet's name, or press Look up again on the Star ID."* (before: *"No known planets for … (type
  the planet's name if it has another)."*).
- If the planet-list step gets no answer within 60 s: *"✖ TrES-3: the NASA Exoplanet Archive did not answer within
  60 s — type the planet's name, or Look up again on the Star ID."*
- A star with several planets still opens the chooser (the bar stops when it opens).

**Chapters to update**
- **05 Input**, *Transits mode: the planet*: delete *"and the empty boxes say 'looking up…' in gray"*; add the star-name
  path and its bar words. Delete *"and Tonight's transits…"* there too (#52).
- **16 Plan a transit night**, *Enter a planet* step 2: delete *"and the empty boxes show a gray looking up…"*; add a
  line for typing the host star in Star ID (the three bar steps above). Add the new ✖ line.
- **03 Tour of the window**, list of busy-bar examples: may add *"Looking up TrES-3's planets at the NASA Exoplanet
  Archive…"*.
- **23 Messages explained**: the two ✖ lines above.

**Screenshots to reshoot**: `img/16-planet-box.png` (it may show the Planet box mid-lookup or the Tonight's
transits… button: reshoot after a TrES-3 lookup; the button is gone either way, see #52), `img/05-input-page.png` only
if it shows the Planet box (it is the Variables-mode Input page; check, reuse if unchanged). Optionally a new shot of
the status strip during *"Looking up TrES-3's planets…"*.

## #52: Tonight's transits and the Transit planner are removed

**What changed.** Both are gone, with everything that only served them:
- Output page (Transit fit), tools row: **Tonight's transits…** and **Transit planner…** removed. The row now starts
  with **Nearby-binary (NEB) check…**, then **Multi-night…**, **Comp health…**, **Use comps…**, the **credible bands**
  box (see the corner-plot item below).
- Input page, Planet (Transits) box: the **Tonight's transits…** button beside **Look up again** is removed.
- Their windows (*SHOBS-P — tonight's transits*, *SHOBS-P — transit planner: …*), their CSV exports
  (`transits_<date>.csv`, `<planet>_transits.csv`), the *Same search on Swarthmore…* button, the downloaded planet
  list `.shobs_p_transit_catalog.csv`, and the remembered search limits are gone. (An old
  `~/.shobs_p_transit_catalog.csv` on disk is no longer read or written; it can be deleted.)
- Sidebar subtitle in Transits: *"Exoplanet transits: fit, NEB check, ExoFOP"*; banner: *"TRANSITS · transit fit
  (MCMC), nearby-binary check, and ExoFOP package"* (the word "planner" is gone).
- Mount hint (Input page, Optics box): *"… Set Mount to Alt-az if this is your own telescope."* (was "… so the
  transit planners use it."). Same in the log note.
- Credits: *Swarthmore Transit Finder (Tapir)* removed; *Sun and Moon positions* is now *Sun position*, used for
  "Approximate BJD_TDB of a report's times when the report has no site". (README.txt's credits block updated to
  match credits_text(); the README "New in" is the lead's.)
- Still there (not planner-only): the NASA Exoplanet Archive lookup, the planet-transit check before a run, the
  zenith-keyhole log note for alt-az nights, the "another star" new-reduction path (planet lookup of another host).
- Leave the space clean: the manual should not point to any other program instead.

**Chapters to update (rewrite)**
- **16 Plan a transit night**: the intro ("SHOBS-P has two tools for this …"), the "This chapter covers" list
  (*finding tonight's transits*, *planning the coming transits of one planet*), *Before you start* step 2 ("Both
  planning tools refuse to run without …") and step 3 (times in the planning windows; **After**, **End by**), the
  whole sections **Find tonight's transits** and **Plan the coming transits of one planet** (incl. the tip on shared
  limits), *Meridian flips* first bullet (*Tonight's transits marks such nights …*), *Alt-az mounts: the zenith
  keyhole* (the sentences on the Zenith column, the Transit planner's Zenith keyhole column and the CSV columns), and
  the *Checklist* line *Start time = Start (with baseline) …*. The chapter keeps: entering a planet, the TOI/ExoClock
  lines, how much baseline to take, meridian flips (with the #8 change), the zenith keyhole (as advice), the
  checklist. Consider renaming it ("Prepare for a transit night"), and README.md's chapter list line (*"… Tonight's
  transits, baseline …"*).
- **17 Fit a transit**: the tools-row bullet (*Tonight's transits…, Transit planner… (see chapter 16), …*).
- **05 Input**: *"Chapter 16 covers this box and Tonight's transits… in detail."*
- **03 Tour of the window**: the Transits banner text in the modes table.
- **01 Welcome**: the Transits row (*"planet lookup, transit planner, transit fit …"*).
- **22 Files SHOBS-P writes**: the two Export CSV rows and `.shobs_p_transit_catalog.csv` row.
- **25 Glossary**: *Zenith keyhole* ("Tonight's transits and the Transit planner mark transits …"); keep the term,
  drop that sentence.
- **26 Credits and citing**: the Swarthmore and Sun/Moon entries, if listed there.
- **README.md (manual index)**: chapter 16's one-line description.

**Screenshots**: delete `img/16-tonights-transits.png` and `img/16-transit-planner.png` (and their references).
Reshoot `img/17-transit-fit-page.png` (tools row and Results row both changed), `img/16-planet-box.png` (button
gone), `img/03-window-parts.png` (shows the Transits banner/subtitle if it was taken in Transits mode: check; reshoot
if so), `img/17-exoclock-buttons.png` (the Results row now has **Save corner plot…**, see below).

## John's note: Save corner plot… moved to the Results row

**What changed.** The tools row's **Corner plot…** button is now **Save corner plot…**, in the bottom **Results**
row just before **Save field image…** (order: … **Save light-curve CSV…**, **Save plot PNG…**, **Save corner
plot…**, **Save field image…**, **Fit the series' nights…**). It opens the same corner-plot window as before (with
its own **Save PNG…**, **Show all parameters …** and **Close**); without a fit it says *"Fit a transit first: the
corner plot shows that fit's MCMC samples."* The **credible bands** box stays in the tools row. **Save plot PNG…**
still saves the corner plot beside the plot as before. The Multi-night window's own **Corner plot…** button is
unchanged.

**Chapters to update**: **17** (tools-row bullet *"Corner plot… and the credible bands box"* → *credible bands*; the
Results-row list; section *The corner plot*: "**Save corner plot…** (Results row) opens a window …"), **19**
(*"The Corner plot… window's Save PNG… saves it on its own"* → **Save corner plot…**), **22** (row *Corner plot… →
Save PNG…* → *Save corner plot… → Save PNG…*), **23** (*"Look at Corner plot…"* → *Save corner plot…*), **25**
(Glossary *Corner plot*: *"Corner plot… on the Transit fit page"* → *"Save corner plot… in the Transit fit page's
Results row"*).

**Screenshots**: `img/17-transit-fit-page.png` and `img/17-exoclock-buttons.png` (Results row), reshoot.

## Screenshot summary

| Shot | Action | Why |
|---|---|---|
| `img/16-tonights-transits.png` | delete | window removed (#52) |
| `img/16-transit-planner.png` | delete | window removed (#52) |
| `img/16-planet-box.png` | reshoot | Tonight's transits… button gone (#52); no "looking up…" (#51) |
| `img/17-transit-fit-page.png` | reshoot | tools row (#52, corner button) and Results row (Save corner plot…); flip rows on a flip night (#8) |
| `img/17-exoclock-buttons.png` | reshoot | Results row has Save corner plot… |
| `img/03-window-parts.png` | check, reshoot if taken in Transits mode | Transits banner and subtitle (#52) |
| `img/05-input-page.png` | check, reuse if Variables mode with no Planet box | Planet box changed (#51, #52) |
| new (optional) | add | a flip night with the step kept (#8); status strip during the star's planet lookup (#51) |
