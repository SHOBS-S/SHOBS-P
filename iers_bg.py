"""2.4.0 fit-in F1 (#116, found by the UI watch in John's F1 regression, 23 Nov 2026): the post-solve freeze.

astropy keeps an Earth-orientation table (IERS-A, finals2000A.all: UT1-UTC and polar motion). The first coordinate
transform that needs it (an airmass, a BJD light time) refreshes it over the network when the cached copy is older
than astropy's auto_max_age (30 days), inside whatever made the call. On the Tk thread that froze the window for
21.7 s right after the solve on HD 219134 10-11 Nov.

SHOBS-P now:
  1. configure(): astropy never downloads inside a transform (iers.conf.auto_download off). Until a fresh table lands
     it uses the IERS-A copy bundled with the astropy-iers-data package (as new as the installed package), however old
     (auto_max_age off: Fable F1 M1, else an old bundle raises on a recent night); times past a table's end warn and
     fall back to the predictions / 50-year mean instead of raising.
  2. start(log): one daemon thread fetches the table (the cached copy when it is under REFRESH_DAYS old, else a new
     download from the IERS, then the USNO mirror, checked by reading it before it replaces the cached copy), and
     hands it to astropy (earth_orientation_table). A failed download keeps the older cached copy if there is one,
     else the bundled one. One log-file line says which.

Accuracy: UT1-UTC (under 1 s) and polar motion (under 1") enter SHOBS-P's numbers at the microsecond level in BJD and
far below 1e-4 in airmass, so which of the tables is in use does not change any result that SHOBS-P shows.
"""
import os
import threading
import time

REFRESH_DAYS = 7.0
TIMEOUT_S = 20.0


def configure():
    """Main thread, before the first transform: no downloads inside astropy transforms, and no error for an old table."""
    try:
        from astropy.utils import iers
        iers.conf.auto_download = False
        iers.conf.auto_max_age = None
        iers.conf.iers_degraded_accuracy = "warn"
    except Exception:
        pass


def _cached_path(url):
    from astropy.utils import data
    try:
        try:
            there = data.is_url_in_cache(url, on_missing="ignore")   # astropy 8: no warning on a first run
        except TypeError:
            there = data.is_url_in_cache(url)
        if there:
            return data.download_file(url, cache=True)   # in the cache: no network
    except Exception:
        pass
    return None


def _fetch_checked(url, download):
    """Download to a temporary file, read it as IERS-A, and only then put it in astropy's cache (Fable F1 S1: an
    error page from a captive portal must not replace a good cached copy). Returns (cached path, table)."""
    from astropy.utils import data, iers
    tmp = download(url)
    table = iers.IERS_A.read(tmp)
    try:
        path = data.import_file_to_cache(url, tmp, remove_original=True, replace=True)
    except Exception:
        path = tmp
    return path, table


def refresh(log=lambda s: None, download=None, now=time.time):
    """Worker thread (and tests). Returns the path of the table handed to astropy, or None."""
    try:
        from astropy.utils import data, iers
    except Exception:
        return None
    try:
        iers.IERS_Auto.open()   # Fable F1 N3: read the bundled table here, not on the Tk thread's first transform
    except Exception:
        pass
    download = download or (lambda url: data.download_file(url, cache=False, timeout=TIMEOUT_S, show_progress=False))
    urls = [u for u in (getattr(iers.conf, "iers_auto_url", None), getattr(iers.conf, "iers_auto_url_mirror", None))
            if u]
    cached = max((p for p in (_cached_path(u) for u in urls) if p), key=os.path.getmtime, default=None)   # S2: newest
    path = table = None
    how = ""
    if cached and 0 <= (now() - os.path.getmtime(cached)) < REFRESH_DAYS * 86400:   # N6: a future date is not fresh
        path, how = cached, "cached copy"
    else:
        for url in urls:
            try:
                path, table = _fetch_checked(url, download)
                how = "downloaded"
                break
            except Exception:
                path = table = None
                continue
        if path is None and cached:
            path, how = cached, "older cached copy (download failed)"
    if path is None:
        log("Earth orientation table (IERS-A): no download and no cached copy; using the one bundled with astropy.")
        return None
    try:
        if table is None:
            table = iers.IERS_A.read(path)
        iers.earth_orientation_table.set(table)
    except Exception as exc:
        log(f"Earth orientation table (IERS-A) not usable ({exc}); using the one bundled with astropy.")
        return None
    when = "today" if how == "downloaded" else "from " + time.strftime("%d %b %Y", time.localtime(os.path.getmtime(path)))
    log(f"Earth orientation table (IERS-A): {how} {when}, loaded in the background.")
    return path


def start(log=lambda s: None):
    configure()
    threading.Thread(target=lambda: refresh(log), daemon=True, name="iers-refresh").start()
