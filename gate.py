"""2.4.0 fit-in F2 (#79 item 6, "grey out instead of scold"): buttons that cannot work yet are greyed, and hovering
over a greyed one says why. Replaces the old pattern of a live button that pops a "do X first" box when pressed.

A gated widget has a `why` function: it returns "" when the widget can work, else a short reason in plain words
("Scan the field first (Scan field on the Photometry page)."). A widget gated with busy=True is also greyed while a
job holds the run lock, with BUSY_REASON. Gates are re-checked every INTERVAL_MS by one timer on the Tk thread and
at once when a job starts or ends (refresh()), so no event wiring is needed at each call site.

John 24 Nov 2026: grey-out everywhere #79 lists; every greyed button carries a hover tooltip saying why.
"""
from __future__ import annotations

INTERVAL_MS = 250
BUSY_REASON = "Another step is running. This is available again when it finishes."


class Gates:
    """Keeps the gated widgets of one Tk app. `busy` is a function that says whether a job holds the run lock."""

    def __init__(self, busy=lambda: False, log=None):
        self._busy = busy
        self._log = log
        self._items: dict = {}        # widget -> (why, busy_gated)
        self._reasons: dict = {}      # widget -> current reason ("" when enabled)
        self._failed: set = set()     # widgets whose why() raised once (logged once, then treated as enabled)
        self._timer = None

    def add(self, widget, why=None, busy: bool = True):
        """Gate `widget`. why() -> "" (can work) or a reason. busy=True also greys it while a job runs."""
        self._items[widget] = (why or (lambda: ""), busy)
        self._apply(widget)
        return widget

    def remove(self, widget):
        self._items.pop(widget, None)
        self._reasons.pop(widget, None)

    def reason(self, widget) -> str:
        """The reason the widget is greyed now ("" when it is live)."""
        return self._reasons.get(widget, "")

    def is_gated(self, widget) -> bool:
        return widget in self._items

    def compute(self, widget) -> str:
        why, busy_gated = self._items[widget]
        try:
            if busy_gated and self._busy():
                return BUSY_REASON
        except Exception:
            pass
        try:
            return (why() or "").strip()
        except Exception as exc:   # a broken check must never lock the user out: enabled, logged once
            if widget not in self._failed:
                self._failed.add(widget)
                if self._log is not None:
                    try:
                        self._log(f"(internal) button check failed, left enabled: {exc}")
                    except Exception:
                        pass
            return ""

    def _apply(self, widget) -> bool:
        """Set the widget's state from its reason. False when the widget is gone (then it is dropped)."""
        try:
            if not widget.winfo_exists():
                self.remove(widget)
                return False
        except Exception:
            self.remove(widget)
            return False
        why = self.compute(widget)
        self._reasons[widget] = why
        try:
            if hasattr(widget, "state") and callable(getattr(widget, "state")) and hasattr(widget, "instate"):
                want_disabled = bool(why)
                if widget.instate(["disabled"]) != want_disabled:
                    widget.state(["disabled"] if want_disabled else ["!disabled"])
            else:
                want = "disabled" if why else "normal"
                if str(widget.cget("state")) != want:
                    widget.configure(state=want)
        except Exception:
            self.remove(widget)
            return False
        return True

    def refresh(self):
        for widget in list(self._items):
            self._apply(widget)

    def start(self, root, interval_ms: int = INTERVAL_MS):
        """Re-check every gate on a timer on root's Tk thread."""
        def tick():
            self._timer = None
            try:
                self.refresh()
            finally:
                try:
                    self._timer = root.after(interval_ms, tick)
                except Exception:
                    self._timer = None
        self._timer = root.after(interval_ms, tick)
