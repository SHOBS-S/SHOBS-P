"""2.4.0 fit-in F1 (#116): a watch on the Tk (main) thread, for "the window stops responding" reports.

The main window's UI-queue drain beats every 50 ms (UIWatch.beat). A daemon thread checks the beat; when the Tk
thread has not beaten for STALL_S seconds, the log file gets one line saying so, with the Python stack of the Tk
thread at that moment (where it is stuck); a stall still going at LONG_S gets the stack again (has it moved?), and
the end of every reported stall gets its length. Log file only: nothing on screen, nothing in the Findings.
"""
import sys
import threading
import time
import traceback

STALL_S = 3.0
LONG_S = 10.0
CHECK_S = 0.5
STACK_FRAMES = 14


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

    def beat(self):
        """Tk thread: called every 50 ms by the UI-queue drain."""
        now = self.clock()
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
        lines = traceback.format_stack(frame)[-STACK_FRAMES:]
        return "".join(lines).rstrip()

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
        """Watch thread (and tests): one look at the beat."""
        gap = self.clock() - self.last
        if gap >= self.stall_s and self._said == 0:
            self._stall_start = self.last
            self._said = 1
            others = self.others_text()
            self.log(f"UI thread busy for {gap:.1f} s (window not responding); it is in:\n{self.stack_text()}"
                     + (f"\nOther threads:\n{others}" if others else ""))
        elif gap >= self.long_s and self._said == 1:
            self._said = 2
            self.log(f"UI thread still busy after {gap:.1f} s; it is now in:\n{self.stack_text()}")

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
