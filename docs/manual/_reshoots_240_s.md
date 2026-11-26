# 2.4.0 fit-in F3 screenshots

No screenshots are taken until 2.4.0 is complete. Fit-in F3 is built by three packages; each lists its own shots
under its own heading.

## Package A

Input, Calibrate, app frame, #113.

| Shot | Chapter | What changed in it | Action |
|---|---|---|---|
| img/05-input-page.png | 5 | The gray plan line under the Frames box (*Plan: lights = …; flats = ….*) once Lights and a calibration row are filled; the old orange *Darks 60 s, lights 120 s …* line is gone (it is the end of the plan line now). After Scan folders, a new **Setup for these frames** box above the Site box (right column). Take it on your own lights after **Save as my setup**, so the box reads *Your own setup: site …; focal length …, pixel …, aperture ….* with no button | RESHOOT |
| NEW img/05-setup-for-these-frames.png | 5 | NEW: the **Setup for these frames** box on frames from another site (Matthew's Moeller 1 m lights, or MicroObservatory frames): *site …; focal length …, pixel …. From the lights' headers (…), N km from your site. In use for these frames only: nothing is saved as yours.* with **Save as my setup** and **Use my own setup** | NEW SHOT |
| NEW img/05-plan-line-warning.png | 5 | NEW (optional): the plan line with a ⚠ line under it (dark flats 2 s against 0.5 s flats) | NEW SHOT |
| img/03-window-parts.png, img/03-busy-bar.png | 3 | Only if the Input page shows in them: the plan line and the **Setup for these frames** box as above; in a shot taken while a job runs, **Scan folders** and **Try it with sample data…** are greyed | RESHOOT if the Input page shows |
| img/07-calibrate-done.png | 7 | **Cancel** is greyed (nothing is running once the calibration is done) | RESHOOT |
| NEW img/07-color-mask-question.png | 7 | NEW: the question *The flat shows a color mask …, but the Bayer pattern is MONO.* with **Color camera (RGGB)**, **It is monochrome**, **Cancel** (color flats, Bayer pattern set to MONO by hand, press Build masters and calibrate) | NEW SHOT |
| Photometry page shots with the progress row (idle) | 8, 9 | **Cancel run** is greyed while nothing runs (Package B lists the page's other changes) | RESHOOT with Package B's |
| img/17-exoclock-buttons.png | 17 | Buttons unchanged; reshoot only if the status line is in the shot: it now reads *✔ Saved ExoClock_…_SHOBS-P: upload the .txt inside (what to pick on the form is in ExoClock_info.txt).* | RESHOOT if the status line shows |
| NEW img/17-exoclock-folder.png | 17, 22 | NEW (optional): the Output folder in File Explorer with the `ExoClock_<planet>_<date>_<obscode>_SHOBS-P` folder beside the ExoFOP one, opened to show the upload `.txt` and `ExoClock_info.txt` | NEW SHOT |

No shot in the manual shows the four questions that went (*Is that your own site?*, *Use the site and optics in these
frames?*, *Use that telescope's setup for these frames?*, *Use the header values?*), so nothing is removed.
