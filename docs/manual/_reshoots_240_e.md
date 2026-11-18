# 2.4.0 step 4 screenshots

No screenshots are taken until 2.4.0 is complete. Each builder appends a section for its issues.

## #72 star record, #56, #67 (s4-record)

The series keeps a record per comp and check star; an older series converts on Load (a Log line and a `_backup`
file). The Output page's Series box has a new **Mags** line (each comp's and the check's magnitude and source). On
the Photometry page a **Series magnitudes…** button appears under the Stars panel only while a catalog (or a typed
value) disagrees with the series; a comp's name box has a hover note (other names, each catalog's magnitude). A new
star's name follows a fixed order (AUID, TYC, Gaia, APASS), so a slot may show a Gaia or TYC name where it showed an
APASS name.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/09-output-page.png | 09 | the Series box has one more line, **Mags** (value and source per comp, then the check) | RESHOOT |
| img/10-series-output.png | 10 | the same Series box: the new **Mags** line | RESHOOT |
| img/10-night-zoom.png | 10 | the Series box, if it is in view: the new **Mags** line | CHECK, reshoot if the box shows |
| img/08-stars-panel.png | 08 | comp/check names of stars new to a series may be the AUID, TYC or Gaia name instead of the APASS name; no Series magnitudes… button unless a value disagrees | CHECK, reshoot if the names differ |
| img/09-marked-stars.jpg, img/08-photometry-solved.jpg | 08, 09 | marker labels follow the slot names (same note as the Stars panel) | CHECK, reshoot if the names differ |
| img/11-use-comps.png, img/11-comp-health.png | 11 | nothing (Use comps… and Comp health are unchanged) | reuse |
| img/10-colour-box.png | 10 | nothing | reuse |
| NEW: the Stars panel with a disagreement and the Series magnitudes… window | 10 | new in 2.4.0 ("The series' magnitudes win") | NEW SHOT when 2.4.0 is complete |
| all others | | nothing of #72 / #56 / #67 in view | reuse |
