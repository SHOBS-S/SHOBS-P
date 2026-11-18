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
