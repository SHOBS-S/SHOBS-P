"""2.4.0 fit-in F1 (#116): a watch on the Tk (main) thread, for "the window stops responding" reports.

The main window's UI-queue drain beats every 50 ms (UIWatch.beat). A daemon thread checks the beat; when the Tk
thread has not beaten for STALL_S seconds, the log file gets one line saying so, with the Python stack of the Tk
thread at that moment (where it is stuck); a stall still going at LONG_S gets the stack again (has it moved?), and
the end of every reported stall gets its length. 2.4.0 cleanup (#118): pauses of SLOW_S to STALL_S (too short for
the stall line, but the window still froze) are counted and summed up in one line once a minute. Log file only: nothing on screen, nothing in the Findings.
"""
import os
import sys
import threading
import time
import traceback

STALL_S = 3.0
SLOW_S = 1.0     # 2.4.0 cleanup (#118): shorter pauses are counted and summed up once a minute
SLOW_EVERY_S = 60.0
LONG_S = 10.0
CHECK_S = 0.5
SLEEP_S = 60.0   # the watch thread itself held up this long: the computer slept (a lock held that long is not seen)
STACK_FRAMES = 14
HERE = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))


def _is_own(filename: str) -> bool:
    """A frame in SHOBS-P's own folder (Fable F1 N2: by folder, not by file name)."""
    try:
        return os.path.normcase(os.path.dirname(os.path.abspath(filename))) == HERE
    except Exception:
        return False


# 2.4.0 fit-in F3 (F2-b): on Windows a native message box or file dialog runs its own modal loop inside one Tk call,
# so Tk timers do not fire and the beat stops while the question is up ("UI thread busy for 98.7 s" for a box the
# user was reading). These are tkinter's own modules for such boxes; wait_window is a Toplevel question's wait.
MODAL_FILES = ("commondialog.py", "messagebox.py", "filedialog.py", "simpledialog.py", "colorchooser.py")
MODAL_FUNCTIONS = ("wait_window",)


def _in_modal(frame) -> bool:
    """True when the Tk thread's stack (its innermost frame, walked outwards) is inside a tkinter question or file
    dialog: the user is answering, the window is not stuck."""
    seen = 0
    while frame is not None and seen < 200:
        try:
            code = frame.f_code
            folder, name = os.path.split(code.co_filename)
            if code.co_name in MODAL_FUNCTIONS and name == "__init__.py" and os.path.basename(folder) == "tkinter":
                return True
            if name in MODAL_FILES and os.path.basename(folder) == "tkinter":
                return True
        except Exception:
            return False
        frame = frame.f_back
        seen += 1
    return False


class UIWatch:
    def __init__(self, log, main_thread_id: int | None = None, stall_s: float = STALL_S, long_s: float = LONG_S,
                 clock=time.monotonic):
        self.log = log
        self.main_id = main_thread_id if main_thread_id is not None else threading.main_thread().ident
        self.stall_s, self.long_s, self.clock = stall_s, long_s, clock
        self.last = clock()
        self._said = 0          # 0 = no stall reported; 1 = reported at stall_s; 2 = reported at long_s
        self._stall_start = None
        self._stop = threading.Event()
        self._last_check = None
        self._slow: list = []            # (gap) of each 1-3 s pause since the last summary
        self._slow_who = ""              # the other threads' innermost frames, seen during one of them
        self._slow_since = clock()
        self._slow_seen = None            # the beat time of the pause already looked at

    def beat(self):
        """Tk thread: called every 50 ms by the UI-queue drain."""
        now = self.clock()
        if not self._said and SLOW_S <= now - self.last < self.stall_s:
            self._slow.append(now - self.last)   # #118: a pause too short for the stall line
        if self._said:
            took = now - (self._stall_start or now)
            self.log(f"UI thread responding again after {took:.1f} s.")
            self._said = 0
            self._stall_start = None
        self.last = now

    def stack_text(self) -> str:
        frame = sys._current_frames().get(self.main_id)
        if frame is None:
            return "  (no stack)"
        full = traceback.extract_stack(frame)
        text = "".join(traceback.format_list(full[-STACK_FRAMES:])).rstrip()
        # F1 regression (23 Nov): a stall deep in a library (astropy's IERS download) pushed every SHOBS-P frame past
        # the cut; name the innermost SHOBS-P frames above it so the log says which of our code made the call.
        tail_own = any(_is_own(f.filename) for f in full[-STACK_FRAMES:][-4:])
        above = [f for f in full[:-STACK_FRAMES] if _is_own(f.filename)]
        if above and not tail_own:
            text = ("  (called from SHOBS-P:)\n" + "".join(traceback.format_list(above[-3:])).rstrip()
                    + "\n  ...\n" + text)
        return text

    def others_text(self) -> str:
        """The other threads' innermost frames: when the Tk thread sits idle in mainloop but did not beat, another
        thread held the interpreter lock (a C library reading a big catalog answer, say); this names it."""
        names = {t.ident: t.name for t in threading.enumerate()}
        out = []
        for ident, frame in sys._current_frames().items():
            if ident in (self.main_id, threading.get_ident()):
                continue
            tail = traceback.format_stack(frame)[-3:]
            out.append(f"  thread {names.get(ident, ident)}:\n" + "".join(tail).rstrip())
        return "\n".join(out)

    def check(self):
        """Watch thread (and tests): one look at the beat. Fable S4: when this thread itself was held up far longer
        than any stall it reports (SLEEP_S: the computer slept), the gap is the sleep, not a stall: start counting again."""
        now = self.clock()
        prev, self._last_check = self._last_check, now
        if prev is not None and now - prev > SLEEP_S:
            self.last = now
            self.log(f"UI watch was held {now - prev:.0f} s itself (the computer slept, or the interpreter was held); "
                     "counting again.")
            return
        gap = now - self.last
        if SLOW_S <= gap < self.stall_s and self._slow_seen != self.last and not self._in_question():
            self._slow_seen = self.last          # #118: who held the window up, once per pause
            self._slow_who = self.others_text()
        if self._slow and now - self._slow_since >= SLOW_EVERY_S:
            self.log(self.slow_summary())
        if gap >= self.stall_s and self._in_question():
            # F2-b: a question or file dialog is up: no stall. Count from now, so a real stall after the answer is
            # timed from the answer. A stall reported before the box opened still gets its "responding again" line.
            self.last = now
            return
        if gap >= self.stall_s and self._said == 0:
            self._stall_start = self.last
            self._said = 1
            others = self.others_text()
            self.log(f"UI thread busy for {gap:.1f} s (window not responding); it is in:\n{self.stack_text()}"
                     + (f"\nOther threads:\n{others}" if others else ""))
        elif gap >= self.long_s and self._said == 1:
            self._said = 2
            self.log(f"UI thread still busy after {gap:.1f} s; it is now in:\n{self.stack_text()}")

    def slow_summary(self) -> str:
        """#118: one line for the 1-3 s pauses since the last one (then the count starts again)."""
        n, longest, total = len(self._slow), max(self._slow), sum(self._slow)
        who = self._slow_who
        self._slow, self._slow_who, self._slow_since = [], "", self.clock()
        return (f"UI thread slow: {n} pause{'s' if n != 1 else ''} of {SLOW_S:.0f}-{self.stall_s:.0f} s in the "
                f"last minute or so (longest {longest:.1f} s, {total:.1f} s in all)"
                + (f"; other threads during one:\n{who}" if who else "."))

    def _in_question(self) -> bool:
        try:
            return _in_modal(sys._current_frames().get(self.main_id))
        except Exception:
            return False

    def start(self):
        def run():
            while not self._stop.wait(CHECK_S):
                try:
                    self.check()
                except Exception:
                    pass
        threading.Thread(target=run, daemon=True, name="ui-watch").start()
        return self

    def stop(self):
        self._stop.set()
