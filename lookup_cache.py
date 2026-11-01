"""2.2.21 (#21): the last good answer from each online service, per target, so a lookup still works while the
service is down.

Services cache only PUBLIC catalog/ephemeris answers (never the site, observer or the user's data). Keys are
(service, target key); one small JSON file per service in ~/.shobs_p_cache/lookups/. A live answer always wins and
refreshes the entry; when the live request fails, the caller asks `get()` and says in plain words that the answer came
from the cache, with the date it was fetched (`from_cache_note`).

Service names in use (keep them stable, they are file names):
  simbad      lookup of a star name            key: the typed name, normalised (see norm_key)
  vsx         VSX lookup of a star name        key: the typed name
  vsx_cone    VSX variables near a position    key: cone_key(ra, dec, radius)
  vsp         AAVSO VSP chart + sequence       key: the chart request (star/field + fov + maglimit)
  nea         NASA Exoplanet Archive planet    key: the typed planet name
  toi         TESS TOI status (#32)            key: the planet name (or TIC)
  exoclock    ExoClock priority (#14)          key: the planet name

SHOBS_P_NO_LOOKUP_CACHE=1 turns the cache off (tests set it, or point SHOBS_P_LOOKUP_CACHE_DIR at a temp folder).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading

_LOCK = threading.Lock()
VERSION = 1


def cache_dir() -> str:
    return os.environ.get("SHOBS_P_LOOKUP_CACHE_DIR") or os.path.join(
        os.path.expanduser("~"), ".shobs_p_cache", "lookups")


def enabled() -> bool:
    return os.environ.get("SHOBS_P_NO_LOOKUP_CACHE", "") not in ("1", "true", "yes")


def norm_key(text) -> str:
    """Case, spaces and dashes don't matter: 'TrES-3 b', 'tres 3b' and 'TRES-3B' are one key."""
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def cone_key(ra_deg: float, dec_deg: float, radius_deg: float) -> str:
    """A position key on a ~18" grid with the radius rounded up to 0.01 deg."""
    import math
    return f"{round(ra_deg * 200) / 200:.3f}_{round(dec_deg * 200) / 200:+.3f}_{math.ceil(radius_deg * 100) / 100:.2f}"


def _path(service: str) -> str:
    return os.path.join(cache_dir(), f"{service}.json")


def _load(service: str) -> dict:
    try:
        with open(_path(service), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("version") == VERSION and isinstance(data.get("entries"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": VERSION, "entries": {}}


def put(service: str, key: str, value) -> None:
    """Store a live answer (JSON-able). Never raises: a cache that can't be written just isn't used."""
    if not enabled() or not key:
        return
    try:
        with _LOCK:
            os.makedirs(cache_dir(), exist_ok=True)
            data = _load(service)
            data["entries"][key] = {"fetched": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                                    "value": value}
            tmp = _path(service) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, _path(service))
    except Exception:
        pass


def get(service: str, key: str):
    """(value, fetched datetime UTC) of the last good answer, or (None, None)."""
    if not enabled() or not key:
        return None, None
    with _LOCK:
        entry = _load(service)["entries"].get(key)
    if not entry:
        return None, None
    try:
        when = _dt.datetime.fromisoformat(entry["fetched"])
    except (KeyError, ValueError):
        when = None
    return entry.get("value"), when


def fetched_text(when) -> str:
    """'12 Oct 2026' (English month always, like the rest of SHOBS-P)."""
    if when is None:
        return "an earlier day"
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{when.day:02d} {months[when.month - 1]} {when.year}"


SERVICE_LABEL = {"simbad": "SIMBAD", "vsx": "VSX", "vsx_cone": "VSX", "vsp": "AAVSO sequence",
                 "nea": "NASA Exoplanet Archive", "toi": "TESS TOI status", "exoclock": "ExoClock"}


def from_cache_note(service: str, when, reason: str = "") -> str:
    """The plain sentence for the log/page/report, e.g.
    'AAVSO sequence from cache (fetched 12 Oct 2026); the live service did not answer (503).'"""
    why = f" ({reason})" if reason else ""
    return (f"{SERVICE_LABEL.get(service, service)} from cache (fetched {fetched_text(when)}); "
            f"the live service did not answer{why}.")


def clear() -> int:
    """Remove every cached lookup (Credits page → Clear cached catalogs). Returns files removed."""
    with _LOCK:   # 2.2.21 review: not mid-put
        return _clear_locked()


def _clear_locked() -> int:
    n = 0
    try:
        for name in os.listdir(cache_dir()):
            if name.endswith(".json"):
                try:
                    os.remove(os.path.join(cache_dir(), name))
                    n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return n
