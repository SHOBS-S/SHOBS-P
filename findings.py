"""2.4.0 step 2 (#102): the Findings panel.

John, 16 Nov 2026: the Log is not the place to tell the user about a warning, caution or note (it scrolls, its lines
are small and all look alike). The rule: anything can go in the Log; a Log line must never be the WAY the user is told
about an issue. Every ⚠ / ✖ that needs the user reaches them through the status line, a message box, or this list.

    store = FindingsList(hints=load_hints())
    store.start_step("Running photometry…")      # a new run of a step replaces that step's old findings
    store.add("warn", "⚠ C2 is missing in 24 frames …", step="Running photometry…")
    store.summary()                               # "⚠ 1 thing needs a look"
    FindingsWindow(app, store, on_show_log=…)     # the list: icon, words, what to do; ✓ Done per entry

Notes (no ⚠ / ✖) never come here (house rule: ⚠ only when serious and needing action). The Log keeps every line,
these included, so a run can still be traced afterwards (the Log stays a full troubleshooting record).

The "what to do" words come from the user's manual, chapter 23 (Messages explained), through message_help.json
(tools/build_message_help.py writes it; tests/test_240_d.py checks it matches chapter 23), so the panel and the manual
always say the same thing. The model (FindingsList, match_hint) needs no display; FindingsWindow is Tk.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
HELP_FILE = os.path.join(HERE, "message_help.json")

ICONS = ("⚠", "✖")
_LEAD = re.compile(r"^\s*(?:[⚠✖✔]\s*)+")
MIN_LITERAL = 12            # a manual pattern needs this many fixed characters to match anything


def bare(text: str) -> str:
    """The message without its leading icon(s) and surrounding blanks."""
    return _LEAD.sub("", str(text or "")).strip()


# ---- the manual's "What to do" words ----------------------------------------------------------------------------
def _plain_md(text: str) -> str:
    """Chapter 23 cell text → plain words: links keep their words, bold/italic/code marks go."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*]+?)\*(?![\w*])", r"\1", text)
    text = text.replace("….", "…")   # an italic message ending in … before the cell's own full stop
    return re.sub(r"\s+", " ", text).strip()


def _split_row(line: str) -> list[str]:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


def parse_chapter23(md_text: str) -> list[dict]:
    """Every table row of chapter 23 that quotes a message: [{"section", "patterns": [...], "todo"}].
    A pattern is one *italic* message in the first cell, "…" standing for names and numbers."""
    rows, section = [], ""
    for line in md_text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if not line.startswith("| ") or line.startswith("|---") or line.startswith("| Message"):
            continue
        cells = _split_row(line)
        if len(cells) < 3:
            continue
        pats = []
        for seg in re.findall(r"\*([^*]+)\*", cells[0]):
            seg = bare(seg)
            if len(re.sub(r"…", "", seg).strip()) >= MIN_LITERAL:
                pats.append(seg)
        todo = _plain_md(cells[2])
        if pats and todo:
            rows.append({"section": section, "patterns": pats, "todo": todo})
    return rows


def load_hints(path: str = HELP_FILE) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return [r for r in data.get("rows", []) if r.get("patterns") and r.get("todo")]
    except Exception:
        return []   # no file (a developer checkout without it): the panel simply has no "what to do" lines


def _pattern_regex(pat: str):
    pieces = [re.escape(p.strip()) for p in pat.split("…")]
    body = r".*?".join(p for p in pieces if p)
    if not body:
        return None, 0
    lead = "" if pat.lstrip().startswith("…") else r"^"
    literal = sum(len(p.strip()) for p in pat.split("…"))
    try:
        return re.compile(lead + body, re.S), literal
    except re.error:
        return None, 0


def match_hint(text: str, hints: list[dict]) -> str:
    """The manual's "what to do" for this message, or "" (the best = the longest fixed text that matches)."""
    msg = bare(text)
    best, best_len = "", 0
    for row in hints or []:
        for pat in row["patterns"]:
            rx, literal = _pattern_regex(pat)
            if rx is None or literal < MIN_LITERAL or literal <= best_len:
                continue
            if rx.search(msg):
                best, best_len = row["todo"], literal
    return best


def step_key(label: str) -> str:
    """A job's busy-bar words → the step it is, so a rerun replaces the last run's findings even when its counts
    differ ("Photometry on 165 lights…" and "Photometry on 160 lights…" are one step)."""
    key = re.sub(r"\d[\d,.]*", "#", str(label or ""))
    return re.sub(r"\s+", " ", key).strip(" …")


# ---- the list -----------------------------------------------------------------------------------------------------
@dataclass
class Finding:
    level: str          # "warn" | "error"
    text: str           # the Log line's first line, word for word (a screenshot of one matches the file)
    step: str = ""      # the job that raised it ("" = outside a job)
    when: str = field(default_factory=lambda: time.strftime("%H:%M"))
    hint: str = ""
    where: str = ""     # the step's words as the busy bar showed them (for the list); step is the key
    times: int = 1      # the same line again in the same step bumps this instead of adding a twin

    @property
    def icon(self) -> str:
        return "✖" if self.level == "error" else "⚠"


class FindingsList:
    """What still needs a look. A new run of a step replaces that step's old findings; ✓ Done (dismiss) or Clear
    all removes them; a later plain note never does."""

    def __init__(self, hints: list[dict] | None = None):
        self.hints = hints if hints is not None else load_hints()
        self.items: list[Finding] = []
        self.listeners: list = []       # called with no arguments after every change

    def _changed(self):
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:
                pass

    def add(self, level: str, text: str, step: str = "", where: str = "") -> Finding | None:
        if level not in ("warn", "error"):
            return None   # notes never come here
        first = (str(text or "").strip().splitlines() or [""])[0].strip()
        if not first:
            return None
        for f in self.items:
            if f.step == step and f.text == first:
                f.times += 1
                f.when = time.strftime("%H:%M")
                f.where = where or f.where
                self._changed()
                return f
        f = Finding(level=level, text=first, step=step, where=where or step, hint=match_hint(first, self.hints))
        self.items.append(f)
        self._changed()
        return f

    def start_step(self, step: str):
        """A step starts (again): its findings from the last time go; other steps' stay."""
        self.drop_step(step)

    def drop_step(self, step: str):
        if not step:
            return
        before = len(self.items)
        self.items = [f for f in self.items if f.step != step]
        if len(self.items) != before:
            self._changed()

    def dismiss(self, finding: Finding):
        if finding in self.items:
            self.items.remove(finding)
            self._changed()

    def clear(self):
        if self.items:
            self.items = []
            self._changed()

    def __len__(self):
        return len(self.items)

    def counts(self) -> tuple[int, int]:
        errors = sum(1 for f in self.items if f.level == "error")
        return errors, len(self.items) - errors

    def link_text(self) -> str:
        """The status strip's lasting link: "" when nothing needs a look."""
        n = len(self.items)
        if not n:
            return ""
        errors, _w = self.counts()
        icon = "✖" if errors else "⚠"
        return f"{icon} {n} to look at"


# ---- the window (Tk) ----------------------------------------------------------------------------------------------
WARN_FG = "#b35c00"     # the app's amber (StepWarn)
ERROR_FG = "#b00020"


class FindingsWindow:
    """One per app. Lists the findings: icon + words (never colour alone), when and which step, what to do. Each entry
    has ✓ Done; the bottom row has Clear all, Show log, Close. Updates itself while open."""

    def __init__(self, app, store: FindingsList, on_show_log=None, title: str = "Findings", font: str = "Segoe UI"):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.app, self.store, self.on_show_log = app, store, on_show_log
        F = font
        win = self.win = tk.Toplevel(app)
        win.title(f"{title} — what needs a look")
        win.transient(app)
        try:
            win.geometry(f"{_px(app, 760)}x{_px(app, 480)}")
        except Exception:
            pass
        outer = ttk.Frame(win, padding=10)
        outer.pack(fill="both", expand=True)
        self.head = tk.StringVar(master=win)
        ttk.Label(outer, textvariable=self.head, font=(F, 11, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Only things that need a look come here; routine notes stay in the Log. "
                              "A step's findings are replaced the next time that step runs.",
                  foreground="#555555", wraplength=_px(app, 680), justify="left").pack(anchor="w", pady=(2, 6))
        # the button row first, at the bottom, so a short window squeezes the list, never the buttons
        row = ttk.Frame(outer)
        row.pack(fill="x", side="bottom", pady=(8, 0))
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", relief="flat", borderwidth=0, background="#ffffff",
                            padx=8, pady=6, cursor="arrow", font=(F, 10))
        sb = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("warn", foreground=WARN_FG, font=(F, 11, "bold"))
        self.text.tag_configure("error", foreground=ERROR_FG, font=(F, 11, "bold"))
        self.text.tag_configure("msg", font=(F, 10))
        self.text.tag_configure("meta", foreground="#666666", font=(F, 9))
        self.text.tag_configure("todo", lmargin1=_px(app, 22), lmargin2=_px(app, 22), font=(F, 10))
        self.text.tag_configure("todo_head", font=(F, 10, "bold"))
        self.text.tag_configure("gap", font=(F, 4))
        ttk.Button(row, text="Close", command=self.close).pack(side="right")
        if on_show_log is not None:
            ttk.Button(row, text="Show log", command=self._show_log).pack(side="right", padx=(0, 6))
        self.clear_btn = ttk.Button(row, text="Clear all", command=self.store.clear)
        self.clear_btn.pack(side="left")
        win.bind("<Escape>", lambda _e: self.close())
        win.protocol("WM_DELETE_WINDOW", self.close)
        self.store.listeners.append(self.refresh)
        self.refresh()

    def _show_log(self):
        try:
            self.on_show_log()
        except Exception:
            pass

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

    def lift(self):
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_set()
        except Exception:
            pass

    def close(self):
        try:
            self.store.listeners.remove(self.refresh)
        except ValueError:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass

    def refresh(self):
        if not self.alive():
            return
        t, ttk = self.text, self.ttk
        n = len(self.store)
        self.head.set("Nothing needs a look." if not n else
                      f"{n} thing{'s' if n != 1 else ''} need{'s' if n == 1 else ''} a look")
        try:
            self.clear_btn.state(["disabled"] if not n else ["!disabled"])
        except Exception:
            pass
        t.configure(state="normal")
        for child in t.winfo_children():
            child.destroy()
        t.delete("1.0", "end")
        if not n:
            t.insert("end", "When a step finds something that needs your attention, it is listed here with what to do.",
                     "meta")
        for f in list(self.store.items):
            t.insert("end", f.icon + " ", f.level)
            t.insert("end", bare(f.text) + ("" if f.times == 1 else f"  (×{f.times})") + "\n", "msg")
            meta = f.when + (f" · {f.where.rstrip('…. ')}" if f.where else "") + "    "
            t.insert("end", meta, "meta")
            btn = ttk.Button(t, text="✓ Done", style="Toolbutton", command=lambda f=f: self.store.dismiss(f))
            t.window_create("end", window=btn, align="center")
            t.insert("end", "\n", "meta")
            if f.hint:
                t.insert("end", "What to do: ", ("todo", "todo_head"))
                t.insert("end", f.hint + "\n", "todo")
            t.insert("end", "\n", "gap")
        t.configure(state="disabled")


def _px(widget, n: float) -> int:
    try:
        scale = max(float(widget.tk.call("tk", "scaling")) / 1.3333, 1.0)
    except Exception:
        scale = 1.0
    return int(round(n * scale))
