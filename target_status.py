"""2.2.21 (#32, item 14): what the planet's follow-up community needs, looked up beside the planet (Transits mode).

- TESS TOI status (#32) from the NASA Exoplanet Archive's TOI table (TAP): a candidate (PC/APC) is follow-up science
  for ExoFOP-TESS (TFOP SG1); a confirmed or known planet (CP/KP) belongs with AAVSO / Exoplanet Watch; a false
  positive (FP/FA) or a planet that is not a TOI shows nothing.
- ExoClock priority (item 14) from ExoClock's planet feed (https://www.exoclock.space/database/planets_json), read
  once a day.

Both answers are cached in lookup_cache (services "toi" and "exoclock"); when the live service does not answer, the
cached answer is used and says when it was fetched. A failed TOI lookup with nothing cached says "TOI status unknown",
never "not a TOI". No Tk here: the App runs these in a worker thread and shows the texts.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import re

import lookup_cache

EXOFOP_TESS_URL = "https://exofop.ipac.caltech.edu/tess/"
EXOCLOCK_FEED_URL = "https://www.exoclock.space/database/planets_json"
EXOCLOCK_PLANET_URL = "https://www.exoclock.space/database/planets/"
TOI_MATCH_ARCSEC = 10.0

# 2.2.21 (#32): TFOPWG dispositions in plain words; "follow" = worth an ExoFOP-TESS upload.
DISPOSITIONS = {
    "PC": ("planet candidate", "exofop"),
    "APC": ("ambiguous planet candidate", "exofop"),
    "CP": ("confirmed", "aavso"),
    "KP": ("known planet", "aavso"),
    "FP": ("false positive", None),
    "FA": ("false alarm", None),
}
EXOFOP_ADVICE = "consider uploading to ExoFOP-TESS (coordinate with TFOP SG1)"
AAVSO_ADVICE = "AAVSO / Exoplanet Watch is the right home"
TOI_UNKNOWN = "TOI status unknown (Archive did not answer)"


def exofop_url(toi: str = "", tic: str = "") -> str:
    """The target's ExoFOP-TESS page (photometry_core.exofop_target_url, without importing the big module here)."""
    try:
        import photometry_core as core
        return core.exofop_target_url(toi=toi, tic=tic) or ""
    except Exception:
        m = re.search(r"(\d+)", str(toi or ""))
        if m and int(m.group(1)) > 0:
            return f"{EXOFOP_TESS_URL}target.php?toi={int(m.group(1))}"
        m = re.search(r"(\d+)", str(tic or ""))
        return f"{EXOFOP_TESS_URL}target.php?id={int(m.group(1))}" if m and int(m.group(1)) > 0 else ""


def disposition_text(toi: str, disp: str) -> str:
    """'TOI-1234.01 · PC (planet candidate) — consider uploading to ExoFOP-TESS (coordinate with TFOP SG1)';
    '' for a false positive / false alarm or an empty or unknown disposition."""
    disp = str(disp or "").strip().upper()
    words, where = DISPOSITIONS.get(disp, (None, None))
    if not toi or where is None:
        return ""
    advice = EXOFOP_ADVICE if where == "exofop" else AAVSO_ADVICE
    return f"TOI-{toi} · {disp} ({words}) — {advice}"


def status_from_row(row: dict | None, cache_note: str = "") -> dict:
    """The status shown for one TOI row (or None: not a TOI). text '' means nothing is shown."""
    if not row:
        return {"state": "none", "text": "", "url": "", "toi": "", "disp": ""}
    toi = str(row.get("toi") or "").strip().strip('"')
    disp = str(row.get("tfopwg_disp") or "").strip().upper()
    tic = str(row.get("tid") or "").strip()
    text = disposition_text(toi, disp)
    if text and cache_note:
        text += f" {cache_note}"
    return {"state": "toi", "text": text, "toi": toi, "disp": disp, "tic": tic,
            "url": exofop_url(toi=toi.split(".")[0], tic=tic) if text else ""}


def unknown_status() -> dict:
    return {"state": "unknown", "text": TOI_UNKNOWN, "url": "", "toi": "", "disp": ""}


def _f(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _sep_arcsec(ra1, dec1, ra2, dec2) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c)))) * 3600.0


def _letter_index(name: str):
    """'TOI-175 c' -> 2 (b=1, c=2, ...); None without a planet letter."""
    m = re.search(r"\d\s*([b-z])\s*$", str(name or ""))
    return ord(m.group(1).lower()) - ord("a") if m else None


def pick_toi_row(rows: list, planet: dict | None = None, ra=None, dec=None) -> dict | None:
    """The TOI row that is this planet, from rows of one star (or one cone): within TOI_MATCH_ARCSEC when a position
    is known; then a typed TOI-NNNN.NN, the period (within 1 %), or the planet letter (b -> .01); else the first."""
    p = planet or {}
    rows = [r for r in rows or [] if str(r.get("toi") or "").strip()]
    if ra is not None and dec is not None:
        near = []
        for r in rows:
            rra, rdec = _f(r.get("ra")), _f(r.get("dec"))
            if rra is None or rdec is None or _sep_arcsec(ra, dec, rra, rdec) <= TOI_MATCH_ARCSEC:
                near.append(r)
        rows = near
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    names = [str(n or "") for n in (p.get("name"), p.get("typed"), *(p.get("aliases") or []))]
    for n in names:
        m = re.search(r"(?i)\bTOI[-\s]?(\d+\.\d+)", n)
        if m:
            hit = next((r for r in rows if str(r.get("toi")).strip() == m.group(1)), None)
            if hit:
                return hit
    per = _f(p.get("period"))
    if per:
        best = min(rows, key=lambda r: abs((_f(r.get("pl_orbper")) or 1e9) - per))
        bp = _f(best.get("pl_orbper"))
        if bp and abs(bp - per) <= 0.01 * per:
            return best
    for n in names:
        k = _letter_index(n)
        if k:
            hit = next((r for r in rows if str(r.get("toi")).strip().endswith(f".{k:02d}")), None)
            if hit:
                return hit
    return rows[0]


TOI_COLUMNS = "toi,tid,tfopwg_disp,toipfx,ra,dec,pl_orbper"


def toi_query(planet: dict | None, *names: str):
    """(ADQL, how) for the planet's TOI rows: by TIC, else by TOI number, else by a small box around the host's
    position (ADQL contains() is refused by the Archive's web firewall; a between-box is not). None: nothing to ask."""
    import transit_core as tc

    p = planet or {}
    toi, tic = tc.exofop_ids(p, *names)
    if not toi and not tic:
        # a TOI among the planet's other names (exofop_ids only looks there when no TIC is known either)
        for a in p.get("aliases") or []:
            m = re.search(r"(?i)\bTOI[-\s]?(\d+)", str(a))
            if m:
                toi = m.group(1)
                break
    if tic:
        return f"select {TOI_COLUMNS} from toi where tid = {int(tic)}", "tic"
    if toi:
        return f"select {TOI_COLUMNS} from toi where toipfx = '{int(toi)}'", "toi"
    ra, dec = _f(p.get("ra")), _f(p.get("dec"))
    if ra is None or dec is None:
        return None
    d = TOI_MATCH_ARCSEC / 3600.0 * 1.5
    dra = min(d / max(math.cos(math.radians(dec)), 1e-3), 180.0)
    return (f"select {TOI_COLUMNS} from toi where ra between {ra - dra:.6f} and {ra + dra:.6f} "
            f"and dec between {dec - d:.6f} and {dec + d:.6f}", "coords")


def toi_cache_key(planet: dict | None, *names: str) -> str:
    p = planet or {}
    return lookup_cache.norm_key(p.get("name") or (names[0] if names else "") or p.get("tic") or "")


def toi_status(planet: dict | None, *names: str, query=None) -> dict:
    """2.2.21 (#32): the planet's TOI status. query(adql) -> rows (default: the Archive's TAP, transit_core._nea_query;
    tests pass a fake). Never raises."""
    p = planet or {}
    try:
        q = toi_query(p, *names)
    except Exception:
        q = None
    if q is None:
        return status_from_row(None)
    adql, how = q
    key = toi_cache_key(p, *names)
    if query is None:
        import transit_core as tc
        query = tc._nea_query
    try:
        rows = query(adql)
        if rows and not any("toi" in r for r in rows):
            raise ValueError("unexpected answer")   # an HTML error page read as CSV
        row = pick_toi_row(rows, p, *((_f(p.get("ra")), _f(p.get("dec"))) if how == "coords" else (None, None)))
    except Exception:
        cached, when = lookup_cache.get("toi", key)
        if isinstance(cached, dict) and "row" in cached:
            return status_from_row(cached["row"], f"(from cache, fetched {lookup_cache.fetched_text(when)})")
        return unknown_status()
    keep = {k: row.get(k) for k in ("toi", "tid", "tfopwg_disp", "toipfx", "ra", "dec", "pl_orbper")} if row else None
    lookup_cache.put("toi", key, {"row": keep})
    return status_from_row(keep)


# ---- ExoClock (item 14) ----------------------------------------------------------------------------------------------

def _download_feed(timeout: float = 45) -> dict:
    import transit_core as tc

    data = json.loads(tc._http_get(EXOCLOCK_FEED_URL, timeout=timeout).decode("utf-8", "replace"))
    if not isinstance(data, dict) or not data:
        raise ValueError("ExoClock's planet list was empty")
    return data


def exoclock_feed(download=None, now=None):
    """(feed dict or None, cache note). The whole feed is downloaded at most once a (UTC) day; when ExoClock does not
    answer, yesterday's (or older) copy is used with "(from cache, fetched 12 Oct 2026)"."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    cached, when = lookup_cache.get("exoclock", "planets_json")
    if isinstance(cached, dict) and cached and when is not None and when.date() == now.date():
        return cached, ""
    try:
        feed = (download or _download_feed)()
        lookup_cache.put("exoclock", "planets_json", feed)
        return feed, ""
    except Exception:
        if isinstance(cached, dict) and cached:
            return cached, f"(from cache, fetched {lookup_cache.fetched_text(when)})"
        return None, ""


def exoclock_entry(feed: dict | None, *names: str):
    """The feed's entry for the planet, matched on lookup_cache.norm_key ('WASP-12 b' = 'WASP-12b'), or None."""
    if not feed:
        return None
    index = {}
    for key, entry in feed.items():
        index.setdefault(lookup_cache.norm_key(key), entry)
        if isinstance(entry, dict) and entry.get("name"):
            index.setdefault(lookup_cache.norm_key(entry["name"]), entry)
    for n in names:
        k = lookup_cache.norm_key(n)
        if k and k in index:
            return index[k]
    return None


def _inches(v) -> str:
    return f"{v:g}"


def exoclock_text(entry: dict | None, feed_read: bool, aperture_mm=None, cache_note: str = "") -> str:
    """'ExoClock: HIGH priority (2 recent observations)', 'Not on ExoClock's list' (only when the feed was read), or
    '' (the feed could not be read and nothing is cached). A tag ExoClock adds to the priority is kept."""
    if not feed_read:
        return ""
    note = f" {cache_note}" if cache_note else ""
    if not entry:
        return "Not on ExoClock's list" + note
    pr = str(entry.get("priority") or "").strip()
    head, _, tag = pr.partition(" ")
    for sep in ("_", "-", "/", ","):
        if not tag and sep in head:
            head, _, tag = head.partition(sep)
    words = f"{head.upper()} priority" + (f" ({tag.strip(' ()')})" if tag.strip(" ()") else "") if head else "listed"
    n = entry.get("total_observations_recent")
    try:
        n = int(n)
        words += f" ({n} recent observation{'s' if n != 1 else ''})"
    except (TypeError, ValueError):
        pass
    need = _f(entry.get("min_telescope_inches"))
    ap = _f(aperture_mm)
    if need and ap and ap / 25.4 < need - 1e-6:
        words += f" (needs ≥ {_inches(need)}″ telescope)"
    return "ExoClock: " + words + note


def exoclock_status(planet: dict | None, *names: str, aperture_mm=None, download=None) -> dict:
    """2.2.21 (item 14): {"text", "url", "on_list"} for the planet. Never raises."""
    p = planet or {}
    try:
        feed, note = exoclock_feed(download)
        all_names = [p.get("name"), p.get("typed"), *names, *(p.get("aliases") or [])]
        entry = exoclock_entry(feed, *[n for n in all_names if n])
        text = exoclock_text(entry, feed is not None, aperture_mm, note)
        url = (EXOCLOCK_PLANET_URL + str(entry.get("name") or "") + "/") if entry and entry.get("name") else ""
        return {"text": text, "url": url, "on_list": bool(entry)}
    except Exception:
        return {"text": "", "url": "", "on_list": False}


def target_status(planet: dict | None, *names: str, aperture_mm=None, query=None, download=None) -> dict:
    """Both answers at once (the App's worker thread): {"planet": name, "toi": {...}, "exoclock": {...}}."""
    p = planet or {}
    return {"planet": p.get("name") or (names[0] if names else ""),
            "toi": toi_status(p, *names, query=query),
            "exoclock": exoclock_status(p, *names, aperture_mm=aperture_mm, download=download)}
