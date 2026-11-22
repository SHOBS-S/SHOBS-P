"""2.4.0 step 7c (#92): the outside-data check, once a star becomes a Candidate: ZTF, TESS and ATLAS at its position.

Decision 10 (John, 15 Nov 2026): survey data that finds the same period may clear the +-1 c/d alias gate and the
time-span gates, recorded as cleared by that survey; it never clears a null test or a systematics guard, which must
pass on our own data. Why: from one site a short-period star's best peak and its 1 c/d alias can be nearly equal
(S033: 0.497 vs 0.443), and no number of our own nights settles that quickly.

- ZTF: the IRSA light-curve service (DR light curves at the position, 2"), each filter searched with Lomb-Scargle
  over the same period range as our gates; the filter with the strongest peak gives the survey's period.
- TESS: the TIC star at the position (MAST), the sectors that covered it (TESScut), and the MIT Quick-Look Pipeline
  (QLP) light curves of those sectors (MAST HLSP); one search over every sector. TESS pixels are 21", so a
  neighbor's light shares the pixel: TESS can confirm a period, not which star carries it (Check neighbors does).
- ATLAS: the ATLAS variable-star catalog (Heinze et al. 2018, VizieR J/AJ/156/241): its periods and class.

Fail softly only when a service is down or has nothing at the position (status "down" / "none"), never to hide a bug:
a parsing or programming error raises.

result = check(ra, dec, period) -> {"utc", "period": ours, "surveys": {"ZTF": {...}, "TESS": {...}, "ATLAS": {...}},
"agree": [surveys that find our period within 1%], "alias": [surveys that find our +-1 c/d alias instead],
"summary": text}. Each survey: {"status": "ok" | "none" | "down", "period", "power", "fap", "n", "detail", "note",
"relation": "same" | "alias" | "harmonic" | "other" | None}. for_gates(result) -> {survey: {"period": P}} for
promotion.gate_report(outside=...).
"""
from __future__ import annotations

import http.client
import importlib
import io
import math
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import numpy as np

try:
    from astropy.timeseries import LombScargle
except Exception:   # pragma: no cover - astropy is a requirement
    LombScargle = None

AGREE_TOL = 0.01          # promotion.clear_with_outside's rule: the same period within 1%
AGREE_FAP = 1e-3          # a ZTF / TESS peak must be significant to count (step 7c review)
ZTF_RADIUS_ARCSEC = 2.0
TESS_MATCH_ARCSEC = 5.0
ATLAS_RADIUS_ARCSEC = 5.0
MIN_POINTS = 40
TIMEOUT_S = 60
USER_AGENT = "SHOBS-P outside-data check"
ZTF_URL = "https://irsa.ipac.caltech.edu/cgi-bin/ZTF/nph_light_curves"
MAST_API = "https://mast.stsci.edu/api/v0/invoke"
TESSCUT_SECTORS = "https://mast.stsci.edu/tesscut/api/v0.1/sector"
QLP_URL = ("https://archive.stsci.edu/hlsps/qlp/s{s:04d}/{a}/{b}/{c}/{d}/"
           "hlsp_qlp_tess_ffi_s{s:04d}-{tic:016d}_tess_v01_llc.fits")
TESS_NOTE = "TESS pixels are 21″: a neighbor's light shares them, so TESS confirms the period, not which star has it."
SURVEYS = ("ZTF", "TESS", "ATLAS")


class ServiceDown(Exception):
    """The service did not answer (network, timeout, HTTP 5xx, a cut-off reply): a soft failure."""


class BadAnswer(Exception):
    """The service answered with something SHOBS-P cannot read (an HTML error page, changed columns): not a soft
    failure, the survey is reported as an error so a changed service never passes as "nothing there"."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bundled_context():
    """An SSL context on a bundled list of root certificates (certifi, else the copy pip carries), or None.

    7c regression (22 Nov 2026): on John's PC Python's check against the Windows certificate store failed for IRSA
    (ZTF) and MAST (TESS) with CERTIFICATE_VERIFY_FAILED, while VizieR worked. The certificate is still checked; only
    the list of trusted roots changes."""
    for name in ("certifi", "pip._vendor.certifi"):
        try:
            return ssl.create_default_context(cafile=importlib.import_module(name).where())
        except Exception:
            continue
    return None


def _cert_failed(exc) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason)


def http_get(url: str, timeout: float = TIMEOUT_S, data: bytes | None = None) -> bytes:
    """GET (or POST with data); a network failure, timeout or 5xx is ServiceDown; 404 returns b"" (nothing there).
    A certificate the computer's own store cannot check is tried once more against the bundled roots."""
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    context = None
    for attempt in (0, 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return b""
            if exc.code >= 500 or exc.code in (408, 429):
                raise ServiceDown(f"HTTP {exc.code}") from exc
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            if _cert_failed(exc):
                if attempt == 0:
                    context = _bundled_context()
                    if context is not None:
                        continue
                raise ServiceDown("certificate problem: this computer could not check the site's security "
                                  "certificate" + (" (with Windows' list and SHOBS-P's own)" if attempt else "")) from exc
            raise ServiceDown(str(getattr(exc, "reason", exc))) from exc
        except http.client.HTTPException as exc:   # IncompleteRead and the like: the reply was cut off
            raise ServiceDown(f"{type(exc).__name__}") from exc
    raise ServiceDown("no answer")   # pragma: no cover - the loop always returns or raises


# ---- the period search -----------------------------------------------------------------------------------------
def search(t, y, dy=None, min_period: float = 0.02, max_period: float = 20.0) -> dict:
    """Lomb-Scargle over [min_period, max_period] (max capped at half the span). {"period", "power", "fap", "n"}."""
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    ok = np.isfinite(t) & np.isfinite(y)
    if dy is not None:
        dy = np.asarray(dy, float)
        ok &= np.isfinite(dy) & (dy > 0)
    t, y = t[ok], y[ok]
    dy = dy[ok] if dy is not None else None
    if t.size < MIN_POINTS or LombScargle is None:
        return {"period": None, "power": None, "fap": None, "n": int(t.size)}
    span = float(t.max() - t.min())
    pmax = min(float(max_period), max(span / 2.0, 2.0 * float(min_period)))
    ls = LombScargle(t, y, dy)
    freq, power = ls.autopower(minimum_frequency=1.0 / pmax, maximum_frequency=1.0 / float(min_period),
                               samples_per_peak=10)
    k = int(np.argmax(power))
    f = float(freq[k])
    # refine the peak (a long baseline makes the grid step matter)
    df = (freq[1] - freq[0]) if freq.size > 1 else f * 1e-4
    fine = np.linspace(f - df, f + df, 201)
    pf = ls.power(fine)
    j = int(np.argmax(pf))
    f, pk = float(fine[j]), float(pf[j])
    try:
        fap = float(ls.false_alarm_probability(pk, minimum_frequency=1.0 / pmax,
                                               maximum_frequency=1.0 / float(min_period), method="baluev"))
    except Exception:
        fap = None
    return {"period": 1.0 / f, "power": pk, "fap": fap, "n": int(t.size)}


def relation(found: float | None, ours: float | None) -> str | None:
    """How a survey's period stands to ours: "same" (within 1%), "alias" (our +-1 or +-2 c/d alias), "harmonic"
    (2x or 1/2), "other"."""
    if not found or not ours:
        return None
    if abs(found - ours) / ours <= AGREE_TOL:
        return "same"
    f, f0 = 1.0 / found, 1.0 / ours
    for k in (-2, -1, 1, 2):
        fa = f0 + k
        if fa > 0 and abs(f - fa) / fa <= AGREE_TOL:
            return "alias"
    for m in (2.0, 0.5):
        if abs(found - m * ours) / (m * ours) <= AGREE_TOL:
            return "harmonic"
    return "other"


# ---- ZTF -------------------------------------------------------------------------------------------------------
def parse_ztf_csv(text: str) -> dict:
    """{filtercode: (hjd, mag, magerr)} from the IRSA light-curve CSV, catflags == 0 only."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {}
    head = [h.strip() for h in lines[0].split(",")]
    need = ("hjd", "mag", "magerr", "catflags", "filtercode")
    if not all(n in head for n in need):
        raise BadAnswer("IRSA's reply is not a ZTF light-curve table: " + lines[0][:80])
    ix = {n: head.index(n) for n in need}
    out: dict = {}
    for ln in lines[1:]:
        p = ln.split(",")
        if len(p) < len(head):
            continue
        try:
            if int(float(p[ix["catflags"]])) != 0:
                continue
            row = (float(p[ix["hjd"]]), float(p[ix["mag"]]), float(p[ix["magerr"]]))
        except ValueError:
            continue
        out.setdefault(p[ix["filtercode"]].strip(), []).append(row)
    return {k: tuple(np.asarray(c, float) for c in zip(*v)) for k, v in out.items()}


def _clip(t, m, e, k: float = 4.0):
    med = np.median(m)
    mad = 1.4826 * np.median(np.abs(m - med))
    keep = np.abs(m - med) < k * mad if mad > 0 else np.ones(m.size, bool)
    return t[keep], m[keep], e[keep]


def ztf(ra: float, dec: float, period: float | None, min_period: float, max_period: float, get=http_get) -> dict:
    q = urllib.parse.urlencode({"POS": f"CIRCLE {ra:.6f} {dec:.6f} {ZTF_RADIUS_ARCSEC / 3600.0:.6f}",
                                "FORMAT": "csv"})
    try:
        text = get(f"{ZTF_URL}?{q}").decode("utf-8", "replace")
    except ServiceDown as exc:
        return {"status": "down", "detail": f"IRSA did not answer ({exc})"}
    try:
        bands = parse_ztf_csv(text)
    except BadAnswer as exc:
        return {"status": "error", "detail": str(exc)}
    if not bands:
        return {"status": "none", "detail": "no ZTF light curve at the position"}
    best, rows = None, []
    for fc in sorted(bands):
        t, m, e = bands[fc]
        if t.size < MIN_POINTS:
            rows.append(f"{fc} {t.size} points (too few)")
            continue
        t, m, e = _clip(t, m, e)
        r = search(t, m, e, min_period, max_period)
        if r["period"]:
            rows.append(f"{fc} {r['n']} points: P {r['period']:.6f} d (power {r['power']:.2f})")
            r["band"] = fc
            if best is None or r["power"] > best["power"]:
                best = r
    if best is None:
        return {"status": "none", "detail": "ZTF: " + "; ".join(rows) if rows else "no usable ZTF points"}
    return {"status": "ok", "period": best["period"], "power": best["power"], "fap": best["fap"], "n": best["n"],
            "band": best["band"], "detail": "ZTF " + "; ".join(rows), "relation": relation(best["period"], period)}


# ---- TESS (QLP) ------------------------------------------------------------------------------------------------
def tic_at(ra: float, dec: float, get=http_get):
    """(TIC id, Tmag, separation arcsec) of the TIC star nearest the position within TESS_MATCH_ARCSEC, or None."""
    import json
    req = {"service": "Mast.Catalogs.Tic.Cone", "format": "json", "pagesize": 20, "page": 1,
           "params": {"ra": ra, "dec": dec, "radius": TESS_MATCH_ARCSEC / 3600.0}}
    body = urllib.parse.urlencode({"request": json.dumps(req)}).encode()
    raw = get(MAST_API, data=body)
    if not raw:
        return None
    rows = (json.loads(raw.decode("utf-8", "replace")) or {}).get("data") or []
    best = None
    for r in rows:
        try:
            dra = (float(r["ra"]) - ra) * math.cos(math.radians(dec)) * 3600.0
            dde = (float(r["dec"]) - dec) * 3600.0
            sep = math.hypot(dra, dde)
            if best is None or sep < best[2]:
                best = (int(r["ID"]), float(r["Tmag"]) if r.get("Tmag") is not None else None, sep)
        except (KeyError, TypeError, ValueError):
            continue
    return best


def tess_sectors(ra: float, dec: float, get=http_get) -> list[int]:
    import json
    raw = get(f"{TESSCUT_SECTORS}?{urllib.parse.urlencode({'ra': ra, 'dec': dec, 'radius': '0m'})}")
    if not raw:
        return []
    return sorted({int(r["sector"]) for r in (json.loads(raw.decode("utf-8", "replace")) or {}).get("results") or []})


def qlp_url(tic: int, sector: int) -> str:
    s = f"{int(tic):016d}"
    return QLP_URL.format(s=int(sector), a=s[0:4], b=s[4:8], c=s[8:12], d=s[12:16], tic=int(tic))


def read_qlp(data: bytes):
    """(time BJD, relative mag, cadence d) from one QLP light-curve file: the detrended KSPSAP_FLUX when there,
    else SAP_FLUX, QUALITY == 0 only."""
    from astropy.io import fits
    with fits.open(io.BytesIO(data)) as hdul:
        d = hdul[1].data
        cols = d.columns.names
        col = "KSPSAP_FLUX" if "KSPSAP_FLUX" in cols else "SAP_FLUX"
        t = np.asarray(d["TIME"], float) + 2457000.0
        fl = np.asarray(d[col], float)
        q = np.asarray(d["QUALITY"]) if "QUALITY" in cols else np.zeros(t.size)
    ok = (q == 0) & np.isfinite(t) & np.isfinite(fl) & (fl > 0)
    t, fl = t[ok], fl[ok]
    if t.size < MIN_POINTS:
        return t, np.array([]), float("nan")
    m = -2.5 * np.log10(fl / np.nanmedian(fl))
    return t, m, float(np.median(np.diff(t)))


def tess(ra: float, dec: float, period: float | None, min_period: float, max_period: float, get=http_get) -> dict:
    try:
        hit = tic_at(ra, dec, get)
        if hit is None:
            return {"status": "none", "detail": f"no TIC star within {TESS_MATCH_ARCSEC:.0f}″"}
        sectors = tess_sectors(ra, dec, get)
    except ServiceDown as exc:
        return {"status": "down", "detail": f"MAST did not answer ({exc})"}
    tic, tmag, _sep = hit
    if not sectors:
        return {"status": "none", "detail": f"TIC {tic}: no TESS sector covered it"}
    ts, ms, used, cad, missing = [], [], [], [], []
    for s in sectors:
        try:
            data = get(qlp_url(tic, s))
        except ServiceDown as exc:
            return {"status": "down", "detail": f"MAST HLSP did not answer ({exc})"}
        if not data:
            missing.append(s)
            continue
        t, m, c = read_qlp(data)
        if m.size:
            ts.append(t)
            ms.append(m - np.median(m))
            used.append(s)
            cad.append(c)
    tm = f", T {tmag:.1f}" if tmag is not None else ""
    if not used:
        return {"status": "none", "detail": f"TIC {tic}{tm}: sectors {', '.join(map(str, sectors))}, but no QLP "
                                            "light curve (QLP covers stars to about T 13.5)"}
    t = np.concatenate(ts)
    m = np.concatenate(ms)
    nyq_p = 2.0 * max(cad) if cad else min_period
    lo = max(float(min_period), nyq_p * 1.02)
    r = search(t, m, None, lo, max_period)
    detail = (f"TIC {tic}{tm}: QLP sectors {', '.join(map(str, used))}"
              + (f" (no QLP light curve for {', '.join(map(str, missing))})" if missing else ""))
    if not r["period"]:
        return {"status": "none", "detail": detail + ": too few good points"}
    note = TESS_NOTE
    if period and period < lo:
        note += f" Our period is shorter than the longest sector cadence allows ({lo * 1440:.0f} min)."
    return {"status": "ok", "period": r["period"], "power": r["power"], "fap": r["fap"], "n": r["n"],
            "detail": detail, "note": note, "relation": relation(r["period"], period), "tic": tic}


# ---- ATLAS (catalog) --------------------------------------------------------------------------------------------
def atlas(ra: float, dec: float, period: float | None, query=None) -> dict:
    """The ATLAS variable catalog entry at the position (via photometry_core._vizier_query, with its mirrors)."""
    if query is None:
        import photometry_core as core

        def query(src, r, d, rad, cols, sort, n):
            return core._vizier_query(src, r, d, rad, cols, sort, n)
    try:
        rows = query("J/AJ/156/241/table4", ra, dec, ATLAS_RADIUS_ARCSEC / 3600.0,
                     ["ATOID", "RAJ2000", "DEJ2000", "fp-LSper", "fp-period", "fp-lngfitper", "Class"], "_r", 5)
    except (OSError, ConnectionError, TimeoutError, ServiceDown, http.client.HTTPException) as exc:
        return {"status": "down", "detail": f"VizieR did not answer ({exc})"}
    except ValueError as exc:   # _vizier_query: every mirror failed or answered damaged
        return {"status": "down", "detail": f"VizieR did not answer ({exc})"}
    if not rows:
        return {"status": "none", "detail": "not in the ATLAS variable catalog"}
    r = rows[0]

    def num(k):
        try:
            v = float(str(r.get(k, "")).strip())
            return v if math.isfinite(v) and v > 0 else None
        except ValueError:
            return None
    periods = [p for p in (num("fp-period"), num("fp-LSper"), num("fp-lngfitper")) if p]
    cls = str(r.get("Class") or "").strip()
    name = str(r.get("ATOID") or "").strip()
    rel = None
    best = periods[0] if periods else None
    for p in periods:   # the catalog lists up to three periods: one that matches ours counts
        if relation(p, period) == "same":
            best, rel = p, "same"
            break
    if rel is None and best:
        rel = relation(best, period)
    listed = ", ".join(f"{p:.6g} d" for p in periods) or "no period"
    dubious = cls.lower() == "dubious"
    return {"status": "ok" if best else "none", "period": best, "relation": rel, "counts": not dubious,
            "detail": f"ATO {name}: class {cls or '—'}, periods {listed}",
            "note": ("ATLAS classes it dubious, so it does not count (John, 22 Nov 2026)" if dubious else "")}


# ---- the whole check -------------------------------------------------------------------------------------------
def check(ra: float, dec: float, period: float | None, *, min_period: float = 0.02, max_period: float = 20.0,
          surveys=SURVEYS, get=http_get, vizier=None) -> dict:
    out = {"utc": utc_now(), "period": period, "ra": ra, "dec": dec, "surveys": {}, "agree": [], "alias": []}
    for s in surveys:
        try:
            if s == "ZTF":
                r = ztf(ra, dec, period, min_period, max_period, get)
            elif s == "TESS":
                r = tess(ra, dec, period, min_period, max_period, get)
            elif s == "ATLAS":
                r = atlas(ra, dec, period, vizier)
            else:
                continue
        except Exception as exc:   # an answer we cannot read (or a bug): said as ✖, never passed as "none", and
            r = {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}   # the other surveys still run
        out["surveys"][s] = r
        if counts(r) and r.get("relation") == "same":
            out["agree"].append(s)
        elif counts(r) and r.get("relation") == "alias":
            out["alias"].append(s)
    out["summary"] = summary(out)
    return out


def counts(r: dict) -> bool:
    """A survey's answer counts as evidence: it answered, its peak is significant (ZTF, TESS: FAP < AGREE_FAP), and
    ATLAS does not class the star dubious."""
    if not r or r.get("status") != "ok" or not r.get("period"):
        return False
    if r.get("counts") is False:
        return False
    fap = r.get("fap")
    if "fap" in r and (fap is None or not (fap < AGREE_FAP)):
        return False
    return True


def summary(res: dict) -> str:
    parts = []
    for s, r in (res.get("surveys") or {}).items():
        st = r.get("status")
        if st == "down":
            parts.append(f"{s} did not answer")
        elif st == "error":
            parts.append(f"{s}: unreadable answer ({r.get('detail')})")
        elif not counts(r) and r.get("period"):
            why = "dubious in ATLAS" if r.get("counts") is False else "peak not significant"
            parts.append(f"{s} finds {r['period']:.6g} d, not counted ({why})")
        elif st == "none":
            parts.append(f"{s}: nothing usable")
        elif r.get("relation") == "same":
            parts.append(f"{s} finds the same period ({r['period']:.6f} d)")
        elif r.get("relation") == "alias":
            parts.append(f"{s} finds our ±1 c/d alias ({r['period']:.6f} d)")
        elif r.get("period"):
            parts.append(f"{s} finds {r['period']:.6g} d ({r.get('relation') or 'other'})")
    head = ("Outside data agrees: " + " and ".join(res.get("agree") or []) + "." if res.get("agree")
            else "No outside survey confirms the period.")
    return head + (" " + "; ".join(parts) + "." if parts else "")


def for_gates(res: dict | None) -> dict:
    """{survey: {"period": P}} for the surveys that found our period (promotion.gate_report(outside=...))."""
    if not res:
        return {}
    return {s: {"period": r["period"]} for s, r in (res.get("surveys") or {}).items()
            if counts(r) and r.get("relation") == "same"}
