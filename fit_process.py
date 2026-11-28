"""2.4.0 cleanup (#118): the long fits run in a helper process, so the window never waits for them.

On Windows a fit running on a worker thread starved the window's thread: every small Tk call (the busy bar's words,
a scroll binding) had to win Python's interpreter lock back from the fit, a few milliseconds each, so the busy bar
froze or never showed and the progress words stuck ("UI thread busy 3.1 s" with the window inside trivial calls).
A separate process has its own interpreter lock, so the window runs at full speed whatever the fit is doing.

    result = fit_process.call(tc.fit_transit, t, flux, err, am, planet, progress=progress, log=log, cancel=cancel)

call() is made from the page's existing worker thread and blocks it (waiting on a pipe holds no lock) until the
helper answers. It runs the function by name in the helper and gives back exactly what the function returns (or
raises what it raised). Callables passed as keywords (progress, log, detrend_progress...) are called back here, on
the calling thread, with the arguments the helper gave them (positional and keyword; their return value does not
reach the fit, which gets None), so the pages' call_ui lines work as before. A
threading.Event passed as cancel= is forwarded: setting it cancels the helper's fit.

The helper is a plain `python fit_process.py --child` started with the window's own Python, kept running between
fits (main() turns the helper on and starts it in the background as the window opens) and ended when the window closes, or
by itself when the window's process goes away (its stdin closes). If the helper cannot start, cannot take the job, or
stops during the fit, the fit runs (again) on the calling thread as before (the old way: the window may be slow while
it runs) and the log file says why; after two helpers stopped mid-fit, every later fit runs that way.
Functions that are not a module's own (a test's stand-in) also run on the calling thread.
"""
from __future__ import annotations

import importlib
import os
import pickle
import queue
import struct
import subprocess
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ENABLED = False                            # main() turns it on: tests and the self-test run fits on their thread
START_TIMEOUT_S = 60.0                     # a helper that has not said hello by then is given up on
CANCEL_GRACE_S = 10.0                      # after a cancel, the helper gets this long to stop before it is ended
MAX_IDLE = 1                               # helpers kept waiting for the next fit

_log = None                                # set_logger(log_to_file): the log file's writer
_lock = threading.Lock()
_idle: list = []
_all: list = []
_broken_why = ""                           # set once a helper could not start: later calls run in-thread
_deaths = 0                                # helpers that stopped during a fit (review S3): after 2, stay in-thread
MAX_DEATHS = 2
_closed = False


def in_window_note() -> str:
    """2.4.0 cleanup regression (card 1): a plain line for the page while a fit runs, when the helper is not in use
    (it could not start, or stopped twice), so the slow window has a visible reason, not only a Log line. "" when
    the helper is fine (or not switched on: tests, the self-test)."""
    if not ENABLED or _closed or not _broken_why:
        return ""
    return ("The fit helper is not running, so this fit runs in the window's own process and the window may be slow "
            "until it ends (the log file says why).")


def set_logger(func) -> None:
    global _log
    _log = func


def _say(text: str) -> None:
    try:
        if _log is not None:
            _log(text)
    except Exception:
        pass


# ---- framing: 4-byte length + pickle --------------------------------------------------------------------------

def _send(fh, obj) -> None:
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    fh.write(struct.pack("<I", len(data)) + data)
    fh.flush()


def _read_exact(fh, n: int) -> bytes:
    """n bytes from the pipe, read straight into one buffer (review S4: appending chunk by chunk was quadratic, and a
    20 MB result in 4 KB chunks took seconds of the window process's lock)."""
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        k = fh.readinto(view[got:])
        if not k:
            raise EOFError("the helper process closed its pipe")
        got += k
    return bytes(buf)


def _recv(fh):
    (n,) = struct.unpack("<I", _read_exact(fh, 4))
    return pickle.loads(_read_exact(fh, n))


# ---- the window's side ----------------------------------------------------------------------------------------

class _Helper:
    def __init__(self):
        flags = 0
        if sys.platform.startswith("win"):
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"   # review nit: the helper's messages decode alike on Windows
        self.proc = subprocess.Popen([sys.executable, os.path.join(HERE, "fit_process.py"), "--child"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     cwd=HERE, env=env, bufsize=0, creationflags=flags)
        self.inbox: queue.Queue = queue.Queue()
        self.dead = ""
        threading.Thread(target=self._read, daemon=True, name="fit-helper-read").start()
        threading.Thread(target=self._read_err, daemon=True, name="fit-helper-stderr").start()
        try:
            kind, *rest = self.inbox.get(timeout=START_TIMEOUT_S)
        except queue.Empty:
            self.kill()
            raise RuntimeError(f"the helper process did not start within {START_TIMEOUT_S:.0f} s")
        if kind != "hello":
            self.kill()
            raise RuntimeError(f"the helper process did not start ({rest[0] if rest else kind})")

    def _read(self):
        try:
            while True:
                self.inbox.put(_recv(self.proc.stdout))
        except Exception as exc:
            self.dead = self.dead or f"{type(exc).__name__}: {exc}"
            self.inbox.put(("dead", self.dead))

    def _read_err(self):
        try:
            for raw in iter(self.proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    _say(f"Fit helper: {line}")
        except Exception:
            pass

    def alive(self) -> bool:
        return not self.dead and self.proc.poll() is None

    def send(self, obj) -> None:
        _send(self.proc.stdin, obj)

    def kill(self):
        try:
            self.proc.kill()
        except Exception:
            pass


def _take() -> _Helper:
    with _lock:
        while _idle:
            h = _idle.pop()
            if h.alive():
                return h
    h = _Helper()
    with _lock:
        _all.append(h)
    return h


def _give_back(h: _Helper) -> None:
    with _lock:
        if not _closed and h.alive() and len(_idle) < MAX_IDLE:
            _idle.append(h)
            return
    _end(h)


def _end(h: _Helper) -> None:
    try:
        h.send(("quit",))
    except Exception:
        pass
    try:
        h.proc.wait(timeout=2)
    except Exception:
        h.kill()
    with _lock:
        if h in _all:
            _all.remove(h)


def warm_up() -> None:
    """Start one helper now (in the background), so the first fit does not wait for it to load."""
    def run():
        global _broken_why
        if _closed or not ENABLED:
            return
        try:
            with _lock:
                if _idle:
                    return
            _give_back(_take())
        except Exception as exc:
            _broken_why = f"{type(exc).__name__}: {exc}"
            _say(f"Fit helper process could not start ({_broken_why}); fits run in the window's process "
                 "(the window may be slow while one runs).")
    threading.Thread(target=run, daemon=True, name="fit-helper-warm").start()


def shutdown() -> None:
    """The window is closing: end every helper."""
    global _closed
    with _lock:
        _closed = True
        helpers = list(_all)
        _idle.clear()
    for h in helpers:
        _end(h)


def _own_function(func) -> tuple | None:
    """(module, name) when func is a module's own function as imported (a test's mock is not)."""
    mod, name = getattr(func, "__module__", None), getattr(func, "__name__", None)
    if not mod or not name or mod == "__main__":
        return None
    m = sys.modules.get(mod)
    if m is None or getattr(m, name, None) is not func:
        return None
    if os.path.normcase(os.path.dirname(os.path.abspath(getattr(m, "__file__", "") or ""))) != os.path.normcase(HERE):
        return None
    return mod, name


def call(func, *args, cancel=None, **kwargs):
    """func(*args, **kwargs) in the helper process; see the module notes. Blocks the calling thread."""
    global _broken_why, _deaths
    where = _own_function(func)
    if where is None or not ENABLED or _broken_why or _closed:
        return func(*args, cancel=cancel, **kwargs) if cancel is not None else func(*args, **kwargs)
    callbacks = {k: v for k, v in kwargs.items() if callable(v)}
    plain = {k: v for k, v in kwargs.items() if k not in callbacks}
    job = (where[0], where[1], args, plain, sorted(callbacks), cancel is not None)
    try:
        pickle.dumps(("job",) + job, protocol=pickle.HIGHEST_PROTOCOL)   # review nit: before any helper is taken
    except Exception as exc:
        _say(f"Fit helper process could not take the fit ({type(exc).__name__}: {exc}); it runs in the window's "
             "process instead.")
        return func(*args, cancel=cancel, **kwargs) if cancel is not None else func(*args, **kwargs)
    try:
        h = _take()
    except Exception as exc:
        _broken_why = f"{type(exc).__name__}: {exc}"
        _say(f"Fit helper process could not start ({_broken_why}); this fit and the next run in the window's "
             "process (the window may be slow while one runs).")
        return func(*args, cancel=cancel, **kwargs) if cancel is not None else func(*args, **kwargs)
    try:
        h.send(("job",) + job)
    except Exception as exc:
        h.kill()
        _say(f"Fit helper process could not take the fit ({type(exc).__name__}: {exc}); it runs in the window's "
             "process instead.")
        return func(*args, cancel=cancel, **kwargs) if cancel is not None else func(*args, **kwargs)
    cancel_sent_at = None
    ok = False
    died = False
    try:
        while True:
            # (checked on every message too: a fit that streams progress must still hear Cancel)
            if cancel is not None and cancel.is_set():
                if cancel_sent_at is None:
                    cancel_sent_at = time.monotonic()
                    try:
                        h.send(("cancel",))
                    except Exception:
                        pass
                elif time.monotonic() - cancel_sent_at > CANCEL_GRACE_S:
                    raise RuntimeError("Canceled")      # (the finally ends the helper: it did not stop)
            try:
                msg = h.inbox.get(timeout=0.1)
            except queue.Empty:
                continue
            kind = msg[0]
            if kind == "cb":
                _name, cb_args = msg[1], msg[2]
                cb_kw = msg[3] if len(msg) > 3 else {}
                cb = callbacks.get(_name)
                if cb is not None:
                    cb(*cb_args, **cb_kw)
            elif kind == "done":
                ok = True
                return msg[1]
            elif kind == "error":
                ok = True               # the helper is fine; the function raised
                exc, remote_tb = msg[1], msg[2]
                if not isinstance(exc, BaseException):
                    exc = RuntimeError(str(exc))
                try:
                    exc.add_note("In the fit helper process:\n" + remote_tb.rstrip())
                except Exception:
                    pass
                raise exc
            elif kind == "dead":
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("Canceled")
                # review S3: the helper went away during the fit (an antivirus, say): the fit runs again in the window's
                # process, as the notes promise (its progress starts again); after MAX_DEATHS, every fit does
                _deaths += 1
                if _deaths >= MAX_DEATHS:
                    _broken_why = f"the helper stopped {_deaths} times"
                _say(f"Fit helper process stopped during the fit ({msg[1]}); the fit runs again in the window's "
                     "process (the window may be slow while it runs).")
                died = True
                break
    finally:
        if ok:
            _give_back(h)
        else:
            h.kill()
            with _lock:
                if h in _all:
                    _all.remove(h)
    if died:
        return func(*args, cancel=cancel, **kwargs) if cancel is not None else func(*args, **kwargs)


# ---- the helper's side ----------------------------------------------------------------------------------------

def _windows_std_handles_off_the_pipes() -> None:
    """2.4.0 cleanup regression (card 1): on Windows the helper never said hello. Its reader thread sat in a
    synchronous ReadFile on the stdin pipe while the main thread imported numpy; loading numpy's compiled library
    (its C runtime starting up looks at the process's standard handles) then waited on that same pipe for good, so the
    window gave up after 60 s and every fit ran in the window's process again. Linux has no such lock, which is why
    only Windows showed it. Cure: the process's standard input points at NUL and its standard output at the stderr
    pipe (fds 0/1 and the Win32 standard handles both; a GUI pythonw's dup2 does not move the Win32 ones), so nothing
    but our own private copies ever touches the job pipes, and a library loaded later in a fit (scipy, astropy) cannot
    hang either."""
    import ctypes
    import msvcrt
    nul = os.open(os.devnull, os.O_RDONLY)
    os.dup2(nul, 0)
    os.close(nul)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.SetStdHandle.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    k32.SetStdHandle.restype = ctypes.c_int
    k32.SetStdHandle(0xFFFFFFF6, msvcrt.get_osfhandle(0))   # STD_INPUT_HANDLE  -> NUL
    k32.SetStdHandle(0xFFFFFFF5, msvcrt.get_osfhandle(1))   # STD_OUTPUT_HANDLE -> the stderr pipe (fd 1 = copy of 2)


def _child_main() -> int:
    # The pickles use private copies of the real stdin/stdout (binary); anything a library prints goes to stderr
    # (the window's log file), and the standard handles are moved off the job pipes (card 1, above).
    if sys.platform.startswith("win"):
        import msvcrt
        msvcrt.setmode(0, os.O_BINARY)
        msvcrt.setmode(1, os.O_BINARY)
    out = os.fdopen(os.dup(1), "wb", buffering=0)
    inp = os.fdopen(os.dup(0), "rb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    if sys.platform.startswith("win"):
        _windows_std_handles_off_the_pipes()
    else:
        nul = os.open(os.devnull, os.O_RDONLY)
        os.dup2(nul, 0)
        os.close(nul)
    try:
        sys.stdin = open(os.devnull, "r")
    except Exception:
        sys.stdin = None
    out_lock = threading.Lock()

    def send(obj):
        with out_lock:
            _send(out, obj)

    jobs: queue.Queue = queue.Queue()
    current = {"cancel": None}

    def read():
        try:
            while True:
                msg = _recv(inp)
                if msg[0] == "cancel":
                    ev = current["cancel"]
                    if ev is not None:
                        ev.set()
                else:
                    if msg[0] == "job":
                        # review nit: the job's Event exists as soon as the job is read, so a Cancel sent right
                        # after it (before the fit starts) is not lost
                        current["cancel"] = threading.Event()
                        msg = msg + (current["cancel"],)
                    jobs.put(msg)
        except Exception:
            jobs.put(("quit",))         # the window's process went away

    try:
        # loaded now, before anything waits on the pipe (card 1), so the first fit does not wait for them either
        import numpy  # noqa: F401
        import transit_core  # noqa: F401
    except Exception as exc:
        send(("fail", f"{type(exc).__name__}: {exc}"))
        return 1
    threading.Thread(target=read, daemon=True).start()
    send(("hello", os.getpid()))
    while True:
        msg = jobs.get()
        if msg[0] != "job":
            return 0
        _k, mod, name, args, kwargs, cb_names, has_cancel, ev = msg
        kw = dict(kwargs)
        for cb in cb_names:
            # (review nit: keyword arguments too; a callback's return value is always None here)
            kw[cb] = (lambda n: (lambda *a, **k: send(("cb", n, a, k))))(cb)
        if has_cancel:
            kw["cancel"] = ev
        try:
            result = getattr(importlib.import_module(mod), name)(*args, **kw)
            send(("done", result))
        except BaseException as exc:      # noqa: BLE001 (every failure goes back to the window)
            tb = traceback.format_exc()
            try:
                pickle.loads(pickle.dumps(exc))   # review nit: one that pickles but cannot come back is sent as text
            except Exception:
                exc = RuntimeError(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)
            send(("error", exc, tb))
        finally:
            if current["cancel"] is ev:
                current["cancel"] = None


if __name__ == "__main__" and "--child" in sys.argv:
    sys.path.insert(0, HERE)
    sys.exit(_child_main())
