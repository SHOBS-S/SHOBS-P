# 2.4.0 step 3 screenshots

No screenshots are taken until 2.4.0 is complete. Each builder appends a section for its issues.

## #105 + #82 (the wrong-planet family)

The pre-run transit check and everything after a run use the Input page's planet; New reduction closes a report open
on the Transit fit page; the TOI / ExoClock lines under the Results row hide for a report of another planet; after
No to a planet of another star the previous planet comes back (or the value boxes stay empty).

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/19-exotic-side-by-side.png | 19 | a report of another planet open: the *TOI-… / ExoClock: …* lines under the Results row are gone (they showed the photometry night's TOI-2046 b beside a TrES-3 b report) | RESHOOT |
| img/16-planet-box.png | 16 | only if the shot shows the line under the Planet box after **No** to a planet of another star (new words) | CHECK, reshoot if so |
| img/05-input-page.png | 05 | nothing of this in view (the Planet box line is the lookup's) | reuse |

## #68 (the check star is never a comp)

A new box and Stars-panel line when a comp and the check star are the same star (same name, or within about 2″), and
Run photometry refuses with the same words. No existing shot shows the case (it needs a deliberate mistake).

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/08-stars-panel.png | 08 | nothing (no clash in the shot) | reuse |
| all others | | nothing of #68 in view | reuse |

## #93 (the fit's exact inputs in the result files)

Text files only: a *Fit inputs (full precision)* block at the end of the ExoFOP `_fit.txt`, *Fit input …* rows in the
transit report's `_settings.txt` (a fit of an opened report now gets one too) and `# Fit input …` lines in the fit
CSV header. Nothing on screen changes; no fit number changes.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| all | | nothing of #93 in view (no shot shows these files) | reuse |

## #65 (Time of max/min starts from the saved times)

The window opens with Period and Epoch from the star's saved times (label *from the times CSV: epoch = first saved
maximum, period last used*), and the saved-times list under the result now has a heading line (*O−C against P …,
E … (HJD), as in the diagram:*) and gives each time's O − C against the boxes' elements, with *(saved … against …)*
where the stored value differs.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/14-extremum.png | 14 | the label beside the elements, the list's new heading line and its O − C values (GSC 03308-00462's 9 Nov maximum now reads O−C +0.0 with *(saved +9.4 against …)*, matching the diagram); the caption was rewritten. Take it on opening the window, without pressing **Elements from saved times** | RESHOOT |
