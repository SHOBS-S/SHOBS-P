"""2.4.0 step 4 (#72): the per-star record for a series' comp and check stars.

A star's identity is its sky position, not its name. Every catalog name is an alias, and every V is kept with the
catalog that gave it. This module holds the small, UI-free rules every part of SHOBS-P shares:

- SAME_STAR_ARCSEC: two positions this close are one star (John, 18 Nov 2026: "about 1 arcsec").
- name_kind(name): which kind of name it is (AUID, AAVSO label, TYC, Gaia DR3, APASS, position, other).
- display_name(names): the name shown for a star (John, 16 Nov 2026): AUID / AAVSO label first, then TYC, then Gaia;
  then APASS, then any other name, then a typed position. The other names go on hover.
- same_star(a, b): True when two (ra, dec) positions are one star.

The record itself (its fields, how a series stores it, how an old series converts on Load) is built on top of
this in step 4; see the module's later functions.
"""
from __future__ import annotations

import math
import re

SAME_STAR_ARCSEC = 1.0

# Order of preference for the name shown (lower = preferred).
DISPLAY_ORDER = ("auid", "aavso_label", "tyc", "gaia", "apass", "other", "position")

_AUID = re.compile(r"^\d{3}-[A-Z]{3}-\d{3}$")
_TYC = re.compile(r"^TYC\s*\d+-\d+-\d+$", re.I)
_GAIA = re.compile(r"^Gaia\s+DR\d\s+\d+$", re.I)
_APASS = re.compile(r"^APASS\s+J\d", re.I)
_POSITION = re.compile(r"^\d{1,2}[:h ]\d{1,2}[:m ]\d{1,2}(\.\d*)?s?\s+[+-]?\d{1,2}[:d° ]\d{1,2}[:' ]\d{1,2}(\.\d*)?\"?$")
_LABEL = re.compile(r"^(AAVSO\s+)?\d{2,3}$")       # an AAVSO comparison-star label, e.g. "127" or "AAVSO 127"


def name_kind(name: str) -> str:
    """The kind of a star name: auid, aavso_label, tyc, gaia, apass, position or other."""
    n = str(name or "").strip()
    if _AUID.match(n):
        return "auid"
    if _LABEL.match(n):
        return "aavso_label"
    if _TYC.match(n):
        return "tyc"
    if _GAIA.match(n):
        return "gaia"
    if _APASS.match(n):
        return "apass"
    if _POSITION.match(n):
        return "position"
    return "other"


def display_name(names) -> str:
    """The name shown for one star out of all its names (first of the most preferred kind, in the given order)."""
    cleaned = [str(n).strip() for n in (names or []) if str(n or "").strip()]
    if not cleaned:
        return ""
    rank = {k: i for i, k in enumerate(DISPLAY_ORDER)}
    return min(enumerate(cleaned), key=lambda t: (rank[name_kind(t[1])], t[0]))[1]


def separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Angular separation in arcsec (haversine; fine at any separation)."""
    r1, d1, r2, d2 = (math.radians(float(v)) for v in (ra1, dec1, ra2, dec2))
    h = math.sin((d2 - d1) / 2.0) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2.0) ** 2
    return math.degrees(2.0 * math.asin(min(1.0, math.sqrt(h)))) * 3600.0


def same_star(a, b, tol_arcsec: float = SAME_STAR_ARCSEC) -> bool:
    """True when positions a and b ((ra, dec) in degrees) are one star. False when either is missing."""
    try:
        if a is None or b is None:
            return False
        vals = [float(a[0]), float(a[1]), float(b[0]), float(b[1])]
    except (TypeError, ValueError, IndexError):
        return False
    if not all(math.isfinite(v) for v in vals):
        return False
    return separation_arcsec(*vals) <= tol_arcsec


# ---- the record (2.4.0 step 4) --------------------------------------------------------------------------------------
# One dict per comp or check star of a series, saved in the series file under meta["stars"]:
#
#   key       the name the series' data uses for this star (inside every saved point). Never changed for an existing
#             series; a brand-new star's key is its display name.
#   role      "comp", "check" or "" (a star the series knows but does not use now)
#   ra, dec   sky position in degrees: the star's identity. epoch: the year the position is for (None: not known).
#   pm        [pm_ra*cos(dec), pm_dec] in mas/yr when a catalog gave one, else None
#   names     every name the star answers to (the key first)
#   v         every catalog magnitude with its source, {"APASS DR9": 8.728, "Gaia DR3": 8.704}
#   v_used    the magnitude the series uses; v_source: where it came from ("" when not known)
#   band      the band of those magnitudes ("V" unless the series is in another band)
#   constant  reserved for Gaia DR4: None, or {"value": True/False, "source": "..."}
#   xy        [x, y] on the series' reference frame when known, else None
FORMAT = 1
META_KEY = "stars"
TYPED = "typed"          # the source of a magnitude the user typed
SERIES = "series"        # the source of a magnitude an older series file held without saying where it came from


def new_record(key: str, ra=None, dec=None, role: str = "", names=(), v=None, v_used=None, v_source: str = "",
               pm=None, epoch=None, xy=None, band: str = "V") -> dict:
    rec = {"key": str(key), "role": role, "ra": None, "dec": None, "epoch": None, "pm": None,
           "names": [str(key)] if str(key) else [], "v": {}, "v_used": None, "v_source": "", "band": band,
           "constant": None, "xy": None}
    set_position(rec, ra, dec, epoch, pm)
    for n in names or ():
        add_name(rec, n)
    for src, mag in (v or {}).items():
        add_v(rec, src, mag)
    if v_used is not None:
        set_used(rec, v_used, v_source)
    if xy is not None:
        try:
            rec["xy"] = [float(xy[0]), float(xy[1])]
        except (TypeError, ValueError, IndexError):
            pass
    return rec


def _num(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def set_position(rec: dict, ra, dec, epoch=None, pm=None) -> bool:
    """Give the record a position (only when both numbers are real). pm: (pm_ra*cos(dec), pm_dec) in mas/yr."""
    ra, dec = _num(ra), _num(dec)
    if ra is None or dec is None:
        return False
    rec["ra"], rec["dec"] = ra % 360.0, dec
    rec["epoch"] = _num(epoch)
    rec["pm"] = None
    try:
        if pm is not None and _num(pm[0]) is not None and _num(pm[1]) is not None:
            rec["pm"] = [float(pm[0]), float(pm[1])]
    except (TypeError, IndexError):
        pass
    return True


def position(rec: dict):
    """The record's stored (ra, dec), or None."""
    if not rec or rec.get("ra") is None or rec.get("dec") is None:
        return None
    return float(rec["ra"]), float(rec["dec"])


def position_at(rec: dict, year=None):
    """The record's (ra, dec) moved to `year` with its proper motion; the stored position when there is no proper
    motion, no epoch or no year."""
    pos = position(rec)
    year = _num(year)
    if pos is None or year is None or not rec.get("pm") or rec.get("epoch") is None:
        return pos
    years = year - float(rec["epoch"])
    dec = pos[1] + float(rec["pm"][1]) * years / 3_600_000.0
    ra = pos[0] + float(rec["pm"][0]) * years / (3_600_000.0 * max(math.cos(math.radians(pos[1])), 0.01))
    return ra % 360.0, dec


def _norm(name) -> str:
    return "".join(str(name or "").lower().replace("_", " ").split())


def record_names(rec: dict) -> list:
    return [n for n in (rec or {}).get("names") or [] if n]


def has_name(rec: dict, name) -> bool:
    k = _norm(name)
    return bool(k) and any(_norm(n) == k for n in record_names(rec))


def add_name(rec: dict, name) -> bool:
    """Add an alias (never replaces the key). True when it was new."""
    name = str(name or "").strip()
    if not name or has_name(rec, name):
        return False
    rec.setdefault("names", []).append(name)
    return True


def add_v(rec: dict, source, mag) -> bool:
    """Keep a magnitude with its source. Never changes the magnitude the series uses."""
    mag, source = _num(mag), str(source or "").strip()
    if mag is None or not source:
        return False
    rec.setdefault("v", {})[source] = mag
    return True


def set_used(rec: dict, mag, source: str = "") -> None:
    """The magnitude the series uses for this star, and where it came from."""
    mag = _num(mag)
    rec["v_used"] = mag
    rec["v_source"] = str(source or "").strip() if mag is not None else ""
    if mag is not None and rec["v_source"]:
        rec.setdefault("v", {})[rec["v_source"]] = mag


def source_of(rec: dict, mag, tol: float = 0.0005) -> str:
    """The catalog whose stored magnitude is `mag` ("" when none is)."""
    mag = _num(mag)
    if mag is None:
        return ""
    for src, value in (rec.get("v") or {}).items():
        if src not in (TYPED, SERIES) and abs(float(value) - mag) <= tol:
            return src
    return ""


def shown_name(rec: dict) -> str:
    """The name shown for a record (display_name of all its names)."""
    return display_name(record_names(rec)) or str((rec or {}).get("key") or "")


def other_names(rec: dict, shown: str = "") -> list:
    """The record's names apart from the one shown (for a hover note)."""
    shown = shown or shown_name(rec)
    return [n for n in record_names(rec) if _norm(n) != _norm(shown)]


def source_words(source: str) -> str:
    return {"": "", TYPED: "typed", SERIES: "from the series file"}.get(source, source)


def v_words(rec: dict, band: str = "") -> str:
    """"V 8.728 (APASS DR9)" for a record, "" without a magnitude."""
    if not rec or rec.get("v_used") is None:
        return ""
    src = source_words(rec.get("v_source") or "")
    return f"{band or rec.get('band') or 'V'} {float(rec['v_used']):.3f}" + (f" ({src})" if src else "")


def hover_text(rec: dict, shown: str = "") -> str:
    """The hover note for a star's name: its other names and every catalog magnitude with its source."""
    if not rec:
        return ""
    lines = []
    others = other_names(rec, shown or shown_name(rec))
    if others:
        lines.append("Also known as: " + ", ".join(others))
    mags = [f"{source_words(s)} {float(m):.3f}" for s, m in (rec.get("v") or {}).items()]
    if mags:
        lines.append(f"{rec.get('band') or 'V'}: " + "; ".join(mags))
    if rec.get("v_used") is not None:
        lines.append("The series uses " + v_words(rec) + ".")
    return "\n".join(lines)


def disagreement_text(series_v, series_source: str, new_v, new_source: str) -> str:
    """"Series uses 8.728 (APASS DR9); Gaia DR3 says 8.704"."""
    s = source_words(series_source)
    n = source_words(new_source)
    first = f"Series uses {float(series_v):.3f}" + (f" ({s})" if s else "")
    if n == "typed":
        return f"{first}; you typed {float(new_v):.3f}"
    return f"{first}; {n or 'the catalog'} says {float(new_v):.3f}"


def find(records, name=None, pos=None, tol_arcsec: float = SAME_STAR_ARCSEC, year=None):
    """The record that `name` (any of its names) or `pos` ((ra, dec), within tol_arcsec) is, or None. A name is tried
    first. With `year`, a record with a proper motion is compared where it is in that year as well as where it was
    stored, so a fast star is still matched."""
    if name:
        for rec in records or []:
            if has_name(rec, name):
                return rec
    if pos is None:
        return None
    best = None
    for rec in records or []:
        for p in {position(rec), position_at(rec, year)}:
            if p is None:
                continue
            try:
                d = separation_arcsec(float(pos[0]), float(pos[1]), p[0], p[1])
            except (TypeError, ValueError, IndexError):
                return None
            if d <= tol_arcsec and (best is None or d < best[0]):
                best = (d, rec)
    return best[1] if best else None


_SEX = re.compile(r"^(\d{1,2})[:h ](\d{1,2})[:m ](\d{1,2}(?:\.\d*)?)s?\s+([+-]?)(\d{1,2})[:d° ](\d{1,2})[:' ]"
                  r"(\d{1,2}(?:\.\d*)?)\"?$")
_JNAME = re.compile(r"J(\d{2})(\d{2})(\d{2}(?:\.\d+)?)([+-])(\d{2})(\d{2})(\d{2}(?:\.\d+)?)$")
_DEG = re.compile(r"^(?:watch\s+)?(\d{1,3}\.\d+)\s+([+-]?\d{1,2}\.\d+)$", re.I)


def position_from_name(name: str):
    """(ra, dec) in degrees read from a name that is a position ("02:47:03.45 +50:43:31.2", "41.7644 +50.7253"), or
    None. A J-name ("APASS J024703.4+504331") is only good to about an arcsecond, so it is read with
    approx=True in position_from_name_approx, not here."""
    n = str(name or "").strip()
    m = _SEX.match(n)
    if m:
        ra = (int(m.group(1)) + int(m.group(2)) / 60.0 + float(m.group(3)) / 3600.0) * 15.0
        dec = int(m.group(5)) + int(m.group(6)) / 60.0 + float(m.group(7)) / 3600.0
        dec = -dec if m.group(4) == "-" else dec
        return (ra, dec) if ra < 360.0 and dec <= 90.0 else None
    m = _DEG.match(n)
    if m:
        ra, dec = float(m.group(1)), float(m.group(2))
        return (ra, dec) if ra < 360.0 and abs(dec) <= 90.0 else None
    return None


def backup_path(path: str, exists=None) -> str:
    """Where the original of a converted (or re-calibrated) series file is kept: "<name>_backup<ext>" beside it, or
    "<name>_backup2<ext>", "_backup3"… when that name is taken (an existing backup is never overwritten)."""
    import os
    exists = exists or os.path.exists
    stem, ext = os.path.splitext(path)
    n = 1
    while True:
        cand = f"{stem}_backup{'' if n == 1 else n}{ext}"
        if not exists(cand):
            return cand
        n += 1


def write_backup(path: str) -> str:
    """Copy the file to its backup name (see backup_path) and return that name. Raises OSError."""
    import shutil
    dest = backup_path(path)
    shutil.copy2(path, dest)
    return dest


def is_current(meta: dict) -> bool:
    """True when a series file's meta already holds star records this version reads."""
    block = (meta or {}).get(META_KEY)
    return isinstance(block, dict) and isinstance(block.get("records"), list) and _num(block.get("format")) is not None


def to_meta(records) -> dict:
    """The block saved under meta["stars"]."""
    out = []
    for rec in records or []:
        r = dict(rec)
        r["names"] = list(record_names(rec))
        r["v"] = dict(rec.get("v") or {})
        out.append(r)
    return {"format": FORMAT, "records": out}


def from_meta(meta: dict, used_names=()) -> tuple:
    """(records, converted) for a series file's meta.

    A file with meta["stars"] gives its records as saved (converted False). An older file is converted (True): one
    record per comp and check star, from the comp/check slots, comps_used, check_used, comp_catalog_mags, comp_coords,
    star_bands (the catalog a slot's magnitude came from) and seq_auids. Two names at one position (within
    SAME_STAR_ARCSEC) become one record; its key is the name the series' data uses (a name in `used_names`, comps_used
    or check_used), so nothing inside the saved points is renamed. Nothing in `meta` is changed."""
    meta = meta or {}
    if is_current(meta):
        records = []
        for raw in meta[META_KEY]["records"]:
            if not isinstance(raw, dict) or not str(raw.get("key") or "").strip():
                continue
            rec = new_record(str(raw["key"]).strip(), raw.get("ra"), raw.get("dec"), str(raw.get("role") or ""),
                             raw.get("names") or (), raw.get("v") if isinstance(raw.get("v"), dict) else None,
                             pm=raw.get("pm"), epoch=raw.get("epoch"), xy=raw.get("xy"),
                             band=str(raw.get("band") or "V"))
            rec["v_used"] = _num(raw.get("v_used"))
            rec["v_source"] = str(raw.get("v_source") or "") if rec["v_used"] is not None else ""
            rec["constant"] = raw.get("constant") if isinstance(raw.get("constant"), dict) else None
            records.append(rec)
        return records, False

    roles = ["comp", "comp2"] + [f"comp{i}" for i in range(3, 11)]
    slots = []                                         # (role, name, magnitude text) as the file's slots hold them
    for role in roles + ["check"]:
        name = str(meta.get(f"{role}_name") or "").strip()
        if name:
            slots.append(("check" if role == "check" else "comp", role, name, meta.get(f"{role}_mag")))
    comps_used = [str(n).strip() for n in meta.get("comps_used") or [] if str(n or "").strip()]
    check_used = str(meta.get("check_used") or "").strip()
    cat = {str(k): _num(v) for k, v in (meta.get("comp_catalog_mags") or {}).items()} \
        if isinstance(meta.get("comp_catalog_mags"), dict) else {}
    coords = {}
    if isinstance(meta.get("comp_coords"), dict):
        for k, v in meta["comp_coords"].items():
            if isinstance(v, (list, tuple)) and len(v) == 2 and _num(v[0]) is not None and _num(v[1]) is not None:
                coords[str(k)] = (float(v[0]), float(v[1]))
    bands = meta.get("star_bands") if isinstance(meta.get("star_bands"), dict) else {}
    auids = meta.get("seq_auids") if isinstance(meta.get("seq_auids"), dict) else {}
    strong = {_norm(n) for n in list(used_names or ()) + comps_used + [check_used]} - {""}

    order = []                                         # every name, the series' own first
    for n in comps_used + [check_used] + [s[2] for s in slots] + list(cat) + list(coords):
        if n and n not in order:
            order.append(n)
    records = []
    for name in order:
        pos = coords.get(name)
        if pos is None:
            pos = position_from_name(name)
        rec = find(records, name=name) or (find(records, pos=pos) if pos is not None else None)
        if rec is None:
            rec = new_record(name, *(pos or (None, None)))
            records.append(rec)
        else:
            if _norm(name) in strong and _norm(rec["key"]) not in strong:
                rec["names"] = [name] + [n for n in record_names(rec) if _norm(n) != _norm(name)]
                rec["key"] = name                      # the series' data uses this name: it is the key
            add_name(rec, name)
            if position(rec) is None and pos is not None:
                set_position(rec, *pos)
    for rec in records:
        names = record_names(rec)
        if any(n == check_used for n in names) or any(s[0] == "check" and s[2] in names for s in slots):
            rec["role"] = "check"
        if any(n in comps_used for n in names) or any(s[0] == "comp" and s[2] in names for s in slots):
            rec["role"] = "comp" if rec["role"] != "check" or any(n in comps_used for n in names) else rec["role"]
        for n in names:
            if auids.get(n):
                add_name(rec, auids[n])
        # every magnitude with its source: the slot's catalog (star_bands), then the series' own value
        slot = next((s for s in slots if s[2] in names), None)
        entry = bands.get(slot[1]) if slot else None
        slot_source = ""
        if isinstance(entry, dict):
            slot_source = str(entry.get("source") or "").strip()
            v_cat = _num((entry.get("mags") or {}).get("V")) if isinstance(entry.get("mags"), dict) else None
            if slot_source and v_cat is not None:
                add_v(rec, slot_source, v_cat)
            seq = entry.get("seq") if isinstance(entry.get("seq"), dict) else {}
            for extra in (seq.get("auid"), ):
                add_name(rec, extra)
        # the series' value: comp_catalog_mags under the key (what every night was reduced with), else the slot's
        used = cat.get(rec["key"])
        if used is None:
            used = next((cat[n] for n in names if cat.get(n) is not None), None)
        if rec["role"] == "check" or used is None:
            slot_mag = _num(slot[3]) if slot else None
            if slot_mag is not None and (rec["role"] == "check" or used is None):
                used = slot_mag if used is None or rec["role"] == "check" else used
        if used is not None:
            src = source_of(rec, used) or SERIES
            rec["v_used"], rec["v_source"] = used, src
            rec.setdefault("v", {}).setdefault(src, used)
        for n in names:                                # another name's different value is kept, with its name
            other = cat.get(n)
            if other is not None and used is not None and abs(other - used) > 0.0005 and not source_of(rec, other):
                kind = {"gaia": "Gaia DR3", "tyc": "Tycho-2", "apass": "APASS DR9"}.get(name_kind(n))
                add_v(rec, kind or f"as {n}", other)
    return records, True


def coords_view(records) -> dict:
    """{name: (ra, dec)} for every name of every record with a position: the app's comp_coords."""
    out = {}
    for rec in records or []:
        pos = position(rec)
        if pos is not None:
            for n in record_names(rec):
                out.setdefault(n, pos)
    return out


def mags_view(records) -> dict:
    """{key: the magnitude the series uses} for the comps: the app's comp_catalog_mags."""
    return {rec["key"]: float(rec["v_used"]) for rec in records or []
            if rec.get("role") != "check" and rec.get("v_used") is not None}


def new_key(names) -> str:
    """The key of a brand-new star: its display name."""
    return display_name(names)
