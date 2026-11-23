"""2.4.0 step 8 (#89): Trim on the Variables Output page (the App's TrimMixin).

A bad stretch that trips no flag (trailed frames, a short burst of cirrus) is left out by dragging across the light
curve, or typing From / To, and pressing Trim, as on the Transit fit page. What is different here:

- Trim works on ONE night at a time (John, 23 Nov 2026). A night must be chosen in the night table, even in a
  one-night series; with no night chosen every Trim control is greyed and the drag does nothing. A span trims only that
  night's points, even when it runs past the night. There is no whole-series trim.
- A trim belongs to its night inside the series and is saved with it: the night's setup holds
  "trims": [[JD from, JD to], ...] and each trimmed point carries the flag word "trim" (core.TRIM_FLAG). The flag is
  re-derived from the spans every time the flags are (App._apply_cloud), so nothing can lose it. Nothing is deleted.
- Trimmed points stay out of the period search, the fold, Time of max/min, the AAVSO report and the comp check even
  with "Leave flagged points out" unticked: a trim is the user's own choice.
"""
from __future__ import annotations

import math
import re
from tkinter import LEFT, StringVar, messagebox, ttk

import numpy as np

import photometry_core as core
import site_time

TITLE = "SHOBS-P"

NEED_NIGHT_TIP = "Choose a night in the night table first: Trim works on one night at a time."
TRIM_CAUTION = "Trim bad data (trailed frames, a burst of cloud), not data that is only inconvenient."
BOX_TIP = ("The site's local clock time (HHMM or HH:MM) or a full JD. Leave From empty to trim from the night's start, To "
           "empty to trim to its end. Dragging across the light curve fills both.")
TRIM_TIP = ("Leaves the points between From and To out of this night: the period, the fold, Time of max/min, the "
            "AAVSO report and the comp check. They stay in the series, marked trimmed. " + TRIM_CAUTION)
CLEAR_TIP = "Puts this night's trimmed points back. Other nights keep their trims."
NOTHING_TIP = "Nothing is trimmed on this night."
HINT_NO_NIGHT = "choose a night first"
HINT_READY = "local time or JD, or drag across the plot"
GREY = "#9aa7b0"


def merge_spans(spans) -> list:
    """Spans [(lo, hi), ...] with the overlapping ones joined, in time order, as [[lo, hi], ...]."""
    out: list = []
    for lo, hi in sorted(core.clean_trim_spans(spans)):
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


class TrimMixin:
    """The App's Trim methods for the Variables Output page (see the module text)."""

    # ---- the controls (the Points group of the Output page) ----------------------------------------------------
    def _trim_build_ui(self, groups, g):
        self.trim_from = StringVar(value="")
        self.trim_to = StringVar(value="")
        self.trim_text = StringVar(value=HINT_NO_NIGHT)
        self._trim_drag = None        # the span last dragged: exact JDs behind the boxes' clock text
        self._trim_span = None        # the matplotlib SpanSelector on the light curve
        self._trim_axis = None        # how the light curve's x maps to JD for the night that can be trimmed
        self._trim_boxes_night = None
        # One compound control (the boxes and both buttons), so the group's wrapping keeps them together; the hint
        # is its own item and wraps on by itself.
        box = ttk.Frame(g, style="Card.TFrame")
        labels = [ttk.Label(box, text="Trim", style="Card.TLabel")]
        labels[0].pack(side=LEFT)
        labels.append(ttk.Label(box, text="from", style="Card.TLabel"))
        labels[-1].pack(side=LEFT, padx=(6, 2))
        from_box = ttk.Entry(box, textvariable=self.trim_from, width=9)
        from_box.pack(side=LEFT)
        labels.append(ttk.Label(box, text="to", style="Card.TLabel"))
        labels[-1].pack(side=LEFT, padx=(6, 2))
        to_box = ttk.Entry(box, textvariable=self.trim_to, width=9)
        to_box.pack(side=LEFT)
        for entry in (from_box, to_box):
            for key in ("<Return>", "<KP_Enter>"):
                try:
                    entry.bind(key, lambda _e: self.trim_points())
                except Exception:
                    pass
        self.trim_button = ttk.Button(box, text="Trim", command=self.trim_points, width=6)
        self.trim_button.pack(side=LEFT, padx=(8, 0))
        self.clear_trim_button = ttk.Button(box, text="Clear trim", command=self.clear_trim, width=10)
        self.clear_trim_button.pack(side=LEFT, padx=(6, 0))
        self.trim_frame = groups.add("points", box, pad=8)
        self.trim_hint_label = groups.add("points", ttk.Label(g, textvariable=self.trim_text, style="Hint.TLabel",
                                                              justify=LEFT))
        self._trim_labels = labels
        self._trim_entries = [from_box, to_box]

        def tip(words):
            return lambda: words if self._trim_night() is not None else NEED_NIGHT_TIP

        def clear_tip():
            night = self._trim_night()
            if night is None:
                return NEED_NIGHT_TIP
            return CLEAR_TIP if self._night_trims(night) else NOTHING_TIP
        for w in labels + [box, from_box, to_box]:
            self._add_tooltip(w, tip(BOX_TIP))
        self._add_tooltip(self.trim_button, tip(TRIM_TIP))
        self._add_tooltip(self.clear_trim_button, clear_tip)
        self._add_tooltip(self.trim_hint_label, tip(BOX_TIP))
        self.update_trim_state()

    # ---- which night, and its spans ---------------------------------------------------------------------------
    def _trim_night(self):
        """The one night Trim works on: the night chosen in the night table; None otherwise (Trim is then not
        available). John, 23 Nov: a night must be selected, even in a one-night series."""
        nights = []
        for o in getattr(self, "observations", None) or []:
            if o.night not in nights:
                nights.append(o.night)
        chosen = getattr(self, "highlight_night", None)
        if chosen in nights:
            return chosen
        return None

    def _night_trims(self, night) -> list:
        return core.clean_trim_spans(self._night_info(night).get("trims"))

    def _trims_by_night(self) -> dict:
        return {night: info.get("trims") for night, info in (self.night_bin or {}).items()
                if isinstance(info, dict) and info.get("trims")}

    def _apply_trims(self) -> int:
        """The 'trim' flag word on exactly the points inside their night's saved spans (every refresh calls this)."""
        return core.apply_trim_flags(self.observations, self._trims_by_night())

    def _trim_spans_info(self, night, points=None) -> list:
        """A night's trimmed stretches as they are in the data: [{"first", "last" (JD of the first and last trimmed
        point), "n", "clock" ('22:14–22:31'), "utc" ('UTC−5'), "utc_ascii"}], in time order; spans without a point
        are left out."""
        pts = [o for o in (self.observations if points is None else points)
               if o.night == night and core.is_trimmed(o) and o.jd is not None and math.isfinite(o.jd)]
        out = []
        for lo, hi in merge_spans(self._night_trims(night)):
            jds = [o.jd for o in pts if lo <= o.jd <= hi]
            if not jds:
                continue
            first, last = min(jds), max(jds)
            try:
                utc, utc_ascii = site_time.offset_at(first), site_time.offset_at(first, True)
            except (OverflowError, ValueError, OSError, TypeError):
                utc = utc_ascii = ""
            out.append({"first": first, "last": last, "n": len(jds),
                        "clock": f"{self._trim_clock(first)}–{self._trim_clock(last)}",
                        "utc": utc, "utc_ascii": utc_ascii})
        return out

    def _trim_night_notes(self, points) -> dict:
        """{night: note} for the AAVSO report's NOTES (ASCII): the stretches trimmed on each reported night."""
        out = {}
        for night in dict.fromkeys(o.night for o in points or []):
            spans = self._trim_spans_info(night)
            if spans:
                out[night] = "SHOBS-P: trimmed (left out) " + "; ".join(
                    f"JD {s['first']:.5f}-{s['last']:.5f} ({s['clock'].replace('–', '-')} local time"
                    f"{' (' + s['utc_ascii'] + ')' if s['utc_ascii'] else ''}; {s['n']} point"
                    f"{'s' if s['n'] != 1 else ''})" for s in spans)
        return out

    def _trim_settings_line(self, night) -> str:
        """The settings file's line for a night's trims ('' when the night has none)."""
        spans = self._trim_spans_info(night)
        if not spans:
            return ""
        return "Trimmed on the Output page (JD, local time: points): " + "; ".join(
            f"{s['first']:.5f}-{s['last']:.5f}, {s['clock'].replace('–', '-')}"
            f"{' (' + s['utc_ascii'] + ')' if s['utc_ascii'] else ''}: {s['n']}" for s in spans)

    def _carry_trims(self, night, old_trims, night_points) -> None:
        """A night measured again keeps its trims (they belong to the night): the spans that still hold points stay,
        unless they would leave the new points under the minimum; then the trims are dropped and a ⚠ finding says
        so (review S3: the user's own trims going is never told in the Log alone)."""
        spans = merge_spans(old_trims)
        spans = [sp for sp in spans if any(o.jd is not None and sp[0] <= o.jd <= sp[1] for o in night_points)]
        if not spans:
            return
        left = sum(1 for o in night_points if math.isfinite(o.mag) and o.jd is not None
                   and not [w for w in (o.flag or "").split() if w != core.TRIM_FLAG]
                   and not any(lo <= o.jd <= hi for lo, hi in spans))
        if left < core.TRIM_MIN_POINTS:
            self.log(f"⚠ {self.label_nights(night)}: the night's trims were dropped: they would leave {left} usable "
                     f"points of the new measurement, and a night needs {core.TRIM_MIN_POINTS}. Every point of the "
                     "night is back in; trim it again if it still needs it.")
            return
        self._night_info(night, create=True)["trims"] = spans
        n = sum(1 for o in night_points if o.jd is not None and any(lo <= o.jd <= hi for lo, hi in spans))
        self.log(f"{self.label_nights(night)}: the night's trims are kept ({n} point{'s' if n != 1 else ''} "
                 "trimmed); Clear trim puts them back.")

    # ---- clock <-> JD ------------------------------------------------------------------------------------------
    @staticmethod
    def _trim_clock(jd: float) -> str:
        try:
            return site_time.jd_to_local(jd + 30.0 / 86400.0).strftime("%H:%M")   # nearest minute
        except (OverflowError, ValueError, OSError):
            return f"{jd:.5f}"

    @staticmethod
    def _trim_parse(text: str, night_jds) -> float | None:
        """A Trim box: the site's clock time or a JD -> JD; None when empty. A clock time is taken on the date that
        puts it nearest the middle of the night's points, so 00:42 on a night that started at 20:41 is the next
        morning's 00:42.
        Review N2: only HHMM (2300, 045) and HH:MM (23:00) are clock times here, and only a full JD (2461329.699) or
        a reduced one (61329.699) is a JD. Anything else is refused, never guessed: with the JD time axis '0.45'
        read off the plot is a fraction of a day, and it was taken as 00:45 on the clock."""
        import datetime as _dt
        raw = (text or "").strip()
        if not raw:
            return None
        if re.fullmatch(r"24\d{5}(\.\d*)?", raw):
            return float(raw)
        if re.fullmatch(r"[4-9]\d{4}\.\d*", raw):
            return float(raw) + 2400000.0     # a reduced JD (61329.699)
        got = re.fullmatch(r"(\d{1,2}):(\d{2})", raw) or re.fullmatch(r"(\d{1,2})(\d{2})", raw)
        h, m = (int(got.group(1)), int(got.group(2))) if got else (99, 99)
        if h == 24 and m == 0:
            h = 0
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"'{raw}' is not clear. Type a clock time as HHMM or HH:MM (for example 2300 or 23:00), "
                             "or a full JD (for example 2461329.699).")
        jds = [j for j in night_jds if j is not None and math.isfinite(j)]
        if not jds:
            raise ValueError("the night has no points to trim")
        jd_mid = (min(jds) + max(jds)) / 2.0
        mid = site_time.jd_to_local(jd_mid)
        best = None
        for d in (-1, 0, 1):
            jd = site_time.local_to_jd(mid.date() + _dt.timedelta(days=d), h, m)
            if best is None or abs(jd - jd_mid) < abs(best - jd_mid):
                best = jd
        return best

    # ---- state: live only with one night chosen -----------------------------------------------------------------
    def update_trim_state(self):
        """Grey or wake every Trim control (called on every redraw of the Output page, so it follows the night
        table's choice, Load, Close and Remove night)."""
        if getattr(self, "trim_button", None) is None:
            return
        night = self._trim_night()
        ok = night is not None
        if night != self._trim_boxes_night:
            # typed or dragged times belong to the night they were made on
            self._trim_boxes_night = night
            self._trim_drag = None
            self.trim_from.set("")
            self.trim_to.set("")
        try:
            for w in self._trim_entries + [self.trim_button]:
                w.state(["!disabled"] if ok else ["disabled"])
            has = bool(ok and self._night_trims(night))
            self.clear_trim_button.state(["!disabled"] if has else ["disabled"])
            for lab in self._trim_labels + [self.trim_hint_label]:
                lab.configure(foreground="" if ok else GREY)
        except Exception:
            pass
        span = getattr(self, "_trim_span", None)
        if span is not None:
            try:
                span.set_active(ok)
            except Exception:
                pass
        if not ok:
            self.trim_text.set(HINT_NO_NIGHT)
            return
        spans = self._trim_spans_info(night)
        if spans:
            n = sum(s["n"] for s in spans)
            self.trim_text.set(f"Trimmed: {spans[0]['clock']} ({n} pts)" if len(spans) == 1
                               else f"Trimmed: {len(spans)} spans, {n} pts")
        else:
            self.trim_text.set(HINT_READY)

    # ---- Trim and Clear trim ------------------------------------------------------------------------------------
    def _trim_save_words(self) -> str:
        if not getattr(self, "series_path", ""):
            return ""
        try:
            return self._save_series_words()
        except OSError as exc:
            messagebox.showwarning(TITLE, f"Could not save the series: {exc}")
            return ""

    def _trim_other_set(self, night):
        """Review N7: ("Stacked" or "Per frame", its points) for the night's point set that is NOT on screen now, when
        the night has both (the Stack step's Points switch); None otherwise."""
        try:
            sets = getattr(self, "_stack_sets", {}).get(night)
            if not sets or sets.get("scan") or not self._stack_set_live(night):
                return None
            now = {id(o) for o in self.observations if o.night == night}
            for key, words in (("stack", "Stacked"), ("frame", "Per frame")):
                pts = list(sets.get(key) or [])
                if pts and {id(o) for o in pts} != now:
                    return words, pts
        except Exception:
            pass
        return None

    def trim_points(self):
        """Add the From–To span (an empty From = from the night's start, an empty To = to its end) to the chosen
        night's trims, refit the period and save the series."""
        night = self._trim_night()
        if night is None:
            self.status.set(NEED_NIGHT_TIP)
            return
        if self.busy:
            self.status.set("Wait for the current job to end, then Trim.")
            return
        a, b = self.trim_from.get().strip(), self.trim_to.get().strip()
        if not a and not b:
            messagebox.showinfo(TITLE, "Type a From and/or To time (the site's local time, HHMM or HH:MM, or a full "
                                       "JD), or drag across the light curve, then Trim.")
            return
        pts = [o for o in self.observations if o.night == night and o.jd is not None and math.isfinite(o.jd)]
        jds = [o.jd for o in pts]
        try:
            drag = self._trim_drag or {}
            lo = drag["lo"] if drag and a and a == drag.get("from") else self._trim_parse(a, jds)
            hi = drag["hi"] if drag and b and b == drag.get("to") else self._trim_parse(b, jds)
        except ValueError as exc:
            messagebox.showerror(TITLE, f"Trim: {exc}")
            return
        lo = -math.inf if lo is None else lo
        hi = math.inf if hi is None else hi
        if lo > hi:
            messagebox.showerror(TITLE, f"Trim: From ({a}) is after To ({b}).")
            return
        label = self.night_label(night, short=True)
        inside = [o for o in pts if lo <= o.jd <= hi]
        new = [o for o in inside if not core.is_trimmed(o)]
        if not new:
            messagebox.showinfo(TITLE, (f"The points of {label} between From and To are trimmed already."
                                        if inside else
                                        f"No points of {label} fall between From and To: nothing to trim."))
            return
        left = sum(1 for o in pts if not o.flag and math.isfinite(o.mag) and not (lo <= o.jd <= hi))
        if left < core.TRIM_MIN_POINTS:
            messagebox.showerror(TITLE, f"That trim would leave {left} usable points on {label}; a night needs at "
                                        f"least {core.TRIM_MIN_POINTS}. To drop the whole night, use Remove night….")
            return
        # Review N7: the minimum holds for the night's other point set too (Points: Per frame / Stacked), since the
        # trim goes with the night whichever set the results use: 20 frames left can be 4 stacks.
        other = self._trim_other_set(night)
        if other is not None:
            spans = self._night_trims(night) + [(lo, hi)]
            left = sum(1 for o in other[1] if o.jd is not None and math.isfinite(o.jd) and math.isfinite(o.mag)
                       and not [w for w in (o.flag or "").split() if w != core.TRIM_FLAG]
                       and not any(a_ <= o.jd <= b_ for a_, b_ in spans))
            if left < core.TRIM_MIN_POINTS:
                messagebox.showerror(TITLE, f"That trim would leave {left} usable points on {label} with Points: "
                                            f"{other[0]}; a night needs at least {core.TRIM_MIN_POINTS} either way. "
                                            "To drop the whole night, use Remove night….")
                return
        # A span is kept inside its own night (1 s beyond the first and last point), so it never needs an open end.
        pad = 1.0 / 86400.0
        span = [max(lo, min(jds) - pad), min(hi, max(jds) + pad)]
        info = self._night_info(night, create=True)
        info["trims"] = merge_spans(self._night_trims(night) + [tuple(span)])
        self._trim_drag = None
        self.trim_from.set("")
        self.trim_to.set("")
        self._apply_trims()
        words = (f"Trimmed {len(new)} point{'s' if len(new) != 1 else ''} on {label} "
                 f"({self._trim_clock(min(o.jd for o in new))}–{self._trim_clock(max(o.jd for o in new))} "
                 f"{site_time.tag(new[0].jd)}).")
        self.log(f"Trim: {words} They stay in the series, flagged trim; Clear trim puts them back.")
        self.refit_period()
        self.status.set(words + self._trim_save_words())

    def clear_trim(self):
        """Put the chosen night's trimmed points back (other nights keep theirs)."""
        night = self._trim_night()
        if night is None:
            self.status.set(NEED_NIGHT_TIP)
            return
        label = self.night_label(night, short=True)
        if not self._night_trims(night):
            self.trim_from.set("")
            self.trim_to.set("")
            self._trim_drag = None
            self.status.set(f"Nothing is trimmed on {label}.")
            return
        if self.busy:
            self.status.set("Wait for the current job to end, then Clear trim.")
            return
        n = sum(1 for o in self.observations if o.night == night and core.is_trimmed(o))
        self._night_info(night, create=True).pop("trims", None)
        self._apply_trims()
        words = f"Trim cleared on {label}: {n} point{'s' if n != 1 else ''} put back."
        self.log(words)
        self.refit_period()
        self.status.set(words + self._trim_save_words())

    # ---- the drag across the light curve ----------------------------------------------------------------------------
    def _trim_x_to_jd(self, x: float):
        """A light-curve x (hours from the night's first point, JD minus a day number, or a matplotlib date) -> JD,
        from the chosen night's own points; None when the plot holds no such night."""
        ax_map = self._trim_axis
        if not ax_map or ax_map.get("night") != self._trim_night() or not len(ax_map["x"]):
            return None
        xs, jds = ax_map["x"], ax_map["jd"]
        k = int(np.argmin(np.abs(xs - x)))
        return float(jds[k] + (x - xs[k]) * ax_map["scale"])

    def _trim_span_dragged(self, x0: float, x1: float):
        """A drag across the light curve: its ends into the From and To boxes as local clock times; the exact times
        are used unless a box is edited. Nothing happens without a chosen night."""
        night = self._trim_night()
        if night is None:
            return
        lo, hi = self._trim_x_to_jd(min(x0, x1)), self._trim_x_to_jd(max(x0, x1))
        if lo is None or hi is None:
            return
        a, b = self._trim_clock(lo), self._trim_clock(hi)
        self._trim_drag = {"from": a, "to": b, "lo": lo, "hi": hi}
        self._trim_boxes_night = night
        self.trim_from.set(a)
        self.trim_to.set(b)
        n = sum(1 for o in self.observations if o.night == night and o.jd is not None and lo <= o.jd <= hi
                and not core.is_trimmed(o))
        self.status.set(f"Trim span {a}–{b} on {self.night_label(night, short=True)} ({n} point"
                        f"{'s' if n != 1 else ''}) marked: Trim leaves it out.")

    def _drop_trim_selector(self):
        old, self._trim_span = getattr(self, "_trim_span", None), None
        if old is not None:
            try:
                old.disconnect_events()
            except Exception:
                pass

    def _make_trim_selector(self):
        """A matplotlib SpanSelector on the light curve (made again after every redraw; the axes were cleared). It is
        active only with one night chosen."""
        self._drop_trim_selector()
        if getattr(self, "out_canvas", None) is None or not getattr(self, "observations", None):
            return
        try:
            from matplotlib.widgets import SpanSelector
        except Exception:
            return

        def onselect(x0, x1):
            if not (math.isfinite(x0) and math.isfinite(x1)) or x0 == x1:
                return
            self._trim_span_dragged(x0, x1)
        style = dict(facecolor="#9aa7b0", alpha=0.3)
        scale = (self._trim_axis or {}).get("scale") or 1.0
        minspan = (0.5 / 1440.0) / scale     # half a minute, in the axis' own units
        try:
            self._trim_span = SpanSelector(self.out_ax1, onselect, "horizontal", useblit=False, props=style,
                                           minspan=minspan)
        except TypeError:
            try:
                self._trim_span = SpanSelector(self.out_ax1, onselect, "horizontal", useblit=False, rectprops=style,
                                               minspan=minspan)
            except Exception:
                self._trim_span = None
        except Exception:
            self._trim_span = None
        if self._trim_span is not None:
            try:
                self._trim_span.set_active(self._trim_night() is not None)
            except Exception:
                pass

    # ---- on the plot ------------------------------------------------------------------------------------------------
    def _draw_trim(self, ax, obs_list, lc_x, jd, mag, trimmed, in_zoom, nights, zoom, alpha_for) -> list:
        """The trimmed points as faint gray × in a gray band labelled 'trimmed' (the label only where one night
        fills the plot), and the x -> JD map for the drag. Returns the title key's words for them."""
        night_now = self._trim_night()
        self._trim_axis = None
        if night_now is not None:
            sel = np.array([o.night == night_now for o in obs_list], dtype=bool) & np.isfinite(lc_x) & np.isfinite(jd)
            if sel.any():
                order = np.argsort(lc_x[sel])
                self._trim_axis = {"night": night_now, "x": np.asarray(lc_x[sel], dtype=float)[order],
                                   "jd": np.asarray(jd[sel], dtype=float)[order],
                                   "scale": (1.0 / 24.0) if zoom is not None else 1.0}
        if not trimmed.any():
            return []
        drawn = False
        for night in nights:
            mask = np.array([o.night == night for o in obs_list], dtype=bool)
            tm = mask & trimmed & np.isfinite(lc_x)
            if not tm.any():
                continue
            a = alpha_for(night, 1.0)
            xs_night = np.sort(lc_x[mask & np.isfinite(lc_x)])
            steps = np.diff(xs_night)
            pad = float(np.median(steps)) / 2.0 if steps.size else 0.0
            wide = zoom == night or len(nights) == 1
            for lo, hi in merge_spans(self._night_trims(night)):
                inside = tm & (jd >= lo) & (jd <= hi)
                if not inside.any():
                    continue
                x0, x1 = float(lc_x[inside].min()) - pad, float(lc_x[inside].max()) + pad
                ax.axvspan(x0, x1, color="#c8d0d6", alpha=0.45 * a, lw=0, zorder=0)
                if wide:
                    ax.text((x0 + x1) / 2.0, 0.99, "trimmed", transform=ax.get_xaxis_transform(), ha="center",
                            va="top", fontsize=7, color="#5d6d76", clip_on=True)
            show = tm & np.isfinite(mag)
            if show.any():
                ax.plot(lc_x[show], mag[show], "x", color="#b0bac1", ms=4, mew=0.7, alpha=a, zorder=1)
                drawn = drawn or bool((show & in_zoom).any())
        return ["× trimmed"] if drawn else []
