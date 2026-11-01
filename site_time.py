"""2.2.21 (#22): the observing site's time zone, for every local time SHOBS-P shows or reads.

Local times used to follow the computer's own clock (datetime.astimezone() with no zone), so a tester in Poland
reducing Arizona frames saw Polish times. Every local time now goes through this module: the site's IANA zone
(America/New_York, America/Phoenix, ...) set on the Input page (Site box, "Time zone"), with daylight saving taken
for that very night. UTC, JD and BJD values are not affected.

The zone in use is module state (set_zone), so transit_core and transit_ui need no app object. An empty name means
"this computer's zone". A zone zoneinfo cannot load (no tzdata package on Windows, a typing slip) falls back to the
computer's zone; set_zone says so once per name (the app puts it in the Log).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, time as dtime, timedelta, timezone

try:
    import zoneinfo as _zi
except ImportError:   # pragma: no cover (Python 3.9+ always has it; SHOBS-P needs 3.11)
    _zi = None

_NAME = ""          # the zone asked for ("" = the computer's)
_TZ = None          # its ZoneInfo, or None (the computer's zone is used)
_WARNED: set = set()

# Windows names (registry TimeZoneKeyName) of the common zones -> IANA (from the Unicode CLDR windowsZones table).
_WINDOWS_ZONES = {
    "Eastern Standard Time": "America/New_York", "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver", "US Mountain Standard Time": "America/Phoenix",
    "Pacific Standard Time": "America/Los_Angeles", "Alaskan Standard Time": "America/Anchorage",
    "Hawaiian Standard Time": "Pacific/Honolulu", "Atlantic Standard Time": "America/Halifax",
    "Newfoundland Standard Time": "America/St_Johns", "Canada Central Standard Time": "America/Regina",
    "US Eastern Standard Time": "America/Indiana/Indianapolis", "Mexico Standard Time": "America/Mexico_City",
    "Central Standard Time (Mexico)": "America/Mexico_City", "Pacific SA Standard Time": "America/Santiago",
    "SA Pacific Standard Time": "America/Bogota", "Argentina Standard Time": "America/Buenos_Aires",
    "E. South America Standard Time": "America/Sao_Paulo", "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik", "UTC": "Etc/UTC", "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest", "Central European Standard Time": "Europe/Warsaw",
    "Romance Standard Time": "Europe/Paris", "E. Europe Standard Time": "Europe/Chisinau",
    "FLE Standard Time": "Europe/Kiev", "GTB Standard Time": "Europe/Bucharest", "Russian Standard Time":
    "Europe/Moscow", "Turkey Standard Time": "Europe/Istanbul", "Israel Standard Time": "Asia/Jerusalem",
    "South Africa Standard Time": "Africa/Johannesburg", "Arabian Standard Time": "Asia/Dubai",
    "India Standard Time": "Asia/Calcutta", "China Standard Time": "Asia/Shanghai",
    "Tokyo Standard Time": "Asia/Tokyo", "Korea Standard Time": "Asia/Seoul", "Singapore Standard Time":
    "Asia/Singapore", "Taipei Standard Time": "Asia/Taipei", "AUS Eastern Standard Time": "Australia/Sydney",
    "E. Australia Standard Time": "Australia/Brisbane", "Cen. Australia Standard Time": "Australia/Adelaide",
    "AUS Central Standard Time": "Australia/Darwin", "W. Australia Standard Time": "Australia/Perth",
    "Tasmania Standard Time": "Australia/Hobart", "New Zealand Standard Time": "Pacific/Auckland",
    "Canary Standard Time": "Atlantic/Canary", "Azores Standard Time": "Atlantic/Azores",
}


UNKNOWN_WINDOWS_ZONE = ""   # a Windows zone name not in _WINDOWS_ZONES (the Log says to pick the site's zone)


def computer_zone_name() -> str:
    """This computer's own IANA zone name, or "" when it cannot be told (then the computer's clock is used as is)."""
    tz = os.environ.get("TZ", "").lstrip(":")
    if "/" in tz and _loads(tz):
        return tz
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation") as key:
                name = str(winreg.QueryValueEx(key, "TimeZoneKeyName")[0]).strip("\x00 ")
            iana = _WINDOWS_ZONES.get(name, "")
            if not iana:   # 2.2.21 review: say why the Time zone box is empty
                global UNKNOWN_WINDOWS_ZONE
                UNKNOWN_WINDOWS_ZONE = name
            return iana
        except Exception:
            return ""
    try:   # Linux and Mac: /etc/localtime links to .../zoneinfo/Area/City
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            name = target.split("zoneinfo/", 1)[1]
            if name.startswith(("posix/", "right/")):
                name = name.split("/", 1)[1]
            if _loads(name):
                return name
    except OSError:
        pass
    try:
        with open("/etc/timezone", encoding="utf-8") as fh:
            name = fh.read().strip()
        if _loads(name):
            return name
    except OSError:
        pass
    return ""


def _loads(name: str):
    if not name or _zi is None:
        return None
    try:
        return _zi.ZoneInfo(name)
    except Exception:
        return None


def computer_zone():
    """This computer's zone as a tzinfo (None: use the clock's own, via astimezone())."""
    return _loads(computer_zone_name())


def available_zones() -> list[str]:
    """Every IANA zone name zoneinfo knows, sorted (empty without a zone database)."""
    if _zi is None:
        return []
    try:
        return sorted(z for z in _zi.available_timezones() if "/" in z and not z.startswith(("posix/", "right/")))
    except Exception:
        return []


def is_valid(name: str) -> bool:
    return _loads((name or "").strip()) is not None


def set_zone(name: str) -> str | None:
    """Use this site zone from now on. Returns a message (once per name) when it cannot be loaded and the
    computer's zone is used instead; None otherwise."""
    global _NAME, _TZ
    name = (name or "").strip()
    _NAME = name
    _TZ = _loads(name) if name else None
    if name and _TZ is None and name not in _WARNED:
        _WARNED.add(name)
        return (f"Time zone '{name}' could not be loaded (unknown name, or the tzdata package is missing); local "
                "times use this computer's time zone instead.")
    return None


def zone_name() -> str:
    """The zone in use: the site's name, else the computer's (may be "" when the computer's cannot be named)."""
    return _NAME if _TZ is not None else computer_zone_name()


def _tz():
    return _TZ if _TZ is not None else computer_zone()


def to_local(dt: datetime) -> datetime:
    """An aware datetime (or a naive one in UTC) in the site's zone, with that date's daylight saving."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tz = _tz()
    return dt.astimezone(tz) if tz is not None else dt.astimezone()


def jd_to_local(jd: float) -> datetime:
    """A JD (UTC) as an aware datetime in the site's zone."""
    return to_local(datetime.fromtimestamp((float(jd) - 2440587.5) * 86400.0, tz=timezone.utc))


def local_to_jd(day, h: int = 0, m: int = 0) -> float:
    """A site clock time on a calendar date -> JD (UTC). A time skipped or repeated by a daylight-saving change is
    taken as zoneinfo does (the earlier reading)."""
    naive = datetime.combine(day, dtime(int(h) % 24, int(m)))
    tz = _tz()
    aware = naive.replace(tzinfo=tz) if tz is not None else naive.astimezone()
    return aware.timestamp() / 86400.0 + 2440587.5


def offset_text(dt: datetime, ascii_only: bool = False) -> str:
    """'UTC−5', 'UTC+1', 'UTC+5:30', 'UTC+0' (Unicode minus, as SHOBS-SCHED writes it; ascii_only for CSV files)."""
    off = dt.utcoffset() or timedelta(0)
    mins = int(round(off.total_seconds() / 60.0))
    sign = "+" if mins >= 0 else ("-" if ascii_only else "−")
    h, mm = divmod(abs(mins), 60)
    return f"UTC{sign}{h}" + (f":{mm:02d}" if mm else "")


def offset_at(jd: float, ascii_only: bool = False) -> str:
    return offset_text(jd_to_local(jd), ascii_only)


def label(jd: float | None = None) -> str:
    """'Local time (UTC−5)': the site's offset on that night (now when jd is None)."""
    dt = jd_to_local(jd) if jd is not None else to_local(datetime.now(timezone.utc))
    return f"Local time ({offset_text(dt)})"


def label_span(jds) -> str:
    """'Local time (UTC−4)', or 'Local time (UTC−4 / UTC−5)' when the nights span a daylight-saving change."""
    seen = []
    for jd in jds:
        try:
            o = offset_at(jd)
        except (OverflowError, ValueError, OSError, TypeError):
            continue
        if o not in seen:
            seen.append(o)
    return f"Local time ({' / '.join(seen)})" if seen else "Local time"


def tag(jd: float) -> str:
    """'local time (UTC−5)', for running text such as '05:02–05:25 local time (UTC−5)'."""
    try:
        return "local time (" + offset_at(jd) + ")"
    except (OverflowError, ValueError, OSError, TypeError):
        return "local time"
