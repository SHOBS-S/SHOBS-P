"""SHOBS instrument profiles, format v1: read, check, name and write them (shared by SHOBS-FLUX, SHOBS-P, SHOBS-SCHED).

ONE FILE, COPIED BYTE FOR BYTE. The master copy lives in the SHOBS-FLUX repository; SHOBS-P and SHOBS-SCHED carry
an identical copy. Change it only in SHOBS-FLUX, raise SHOBS_PROFILE_VERSION, and copy it into the other apps with
SHOBS-FLUX's tools/sync_shared.py. Each app's tests check the version it was built against, so a stale copy is
caught instead of drifting. Standard library only (each app ships as a plain zip).

The data folder (~/.shobs, or $SHOBS_DATA_HOME for tests) holds:
  cameras/<camera id>.json       camera profile: bench values for one camera at one gain, offset and binning
  systems/<system id>.json       system profile: on-sky values for one telescope + camera, per band
  specs/<model>.json             camera spec: the manufacturer's curves against the gain setting
  history/                       every earlier revision of a profile, kept when a newer one replaces it
  onsky/                         SHOBS-P's on-sky log: one JSON file per reduction
  flux_runs.sqlite               SHOBS-FLUX's run history

Every measured or assumed number is a VALUE RECORD:
  {"value", "unit", "sigma", "source", "n", "date", "conditions", "run"} (+ an optional "note")
where source is one of SOURCES. A reader always knows where a number came from, and "none" means not known yet.
The site is never stored in a profile (each app keeps its own).

Profiles are written as JSON with a two-space indent, keys in the order they were made, non-ASCII kept as is, and
no newline at the end, so a profile read and written back is byte-identical."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import time
from datetime import datetime

SHOBS_PROFILE_VERSION = "1.0.0"          # of this file; raise on every change (the apps' tests check it)

FORMAT_VERSION = 1
CAMERA_FORMAT = "shobs-camera-profile"
SYSTEM_FORMAT = "shobs-system-profile"
SPEC_FORMAT = "shobs-camera-spec"
ONSKY_FORMAT = "shobs-onsky-log"
FORMATS = {CAMERA_FORMAT: "cameras", SYSTEM_FORMAT: "systems", SPEC_FORMAT: "specs"}

SOURCES = ("measured", "header", "spec", "estimate", "user", "derived", "none")
SOURCE_WORDS = {"measured": "measured", "header": "from the FITS header", "spec": "manufacturer's spec",
                "estimate": "estimate", "user": "typed by you", "derived": "worked out", "none": "not known yet"}
# how much to trust a source when two give the same value (higher wins)
SOURCE_RANK = {"measured": 6, "user": 5, "header": 4, "derived": 3, "spec": 2, "estimate": 1, "none": 0}
RECORD_KEYS = ("value", "unit", "sigma", "source", "n", "date", "conditions", "run")
NONE_WORDS = frozenset({"", "NONE", "N/A", "NA", "-", "UNKNOWN"})


class ProfileError(Exception):
    """A profile that cannot be read or is not format v1 (the message is a plain sentence)."""


# ---- where things live ---------------------------------------------------------------------------------------------

def data_dir(make: bool = True) -> str:
    """The SHOBS data folder: $SHOBS_DATA_HOME when set (tests), else ~/.shobs."""
    d = os.environ.get("SHOBS_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".shobs")
    if make:
        os.makedirs(d, exist_ok=True)
    return d


def sub_dir(name: str, make: bool = True) -> str:
    """cameras, systems, specs, history or onsky inside the data folder."""
    d = os.path.join(data_dir(make), name)
    if make:
        os.makedirs(d, exist_ok=True)
    return d


def onsky_dir(make: bool = True) -> str:
    return sub_dir("onsky", make)


def runs_db_path() -> str:
    return os.path.join(data_dir(), "flux_runs.sqlite")


# ---- names ---------------------------------------------------------------------------------------------------------

def slug(text) -> str:
    """'ZWO ASI2600MC Air' → 'zwo-asi2600mc-air'; '14in RC' → '14in-rc'. Empty → 'unknown'."""
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    return s or "unknown"


def _num_part(v) -> str:
    """A gain or offset for a file name: 100 → '100', 2.5 → '2p5', unknown → 'na'."""
    f = _finite(v)
    if f is None:
        return "na"
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return f"{f:g}".replace(".", "p").replace("-", "m")


def camera_id(instrume, gain_setting, offset, binning) -> str:
    """The camera profile's id and file stem: <camera>__g<gain>__o<offset>__bin<n>."""
    b = _finite(binning)
    return (f"{slug(instrume)}__g{_num_part(gain_setting)}__o{_num_part(offset)}"
            f"__bin{int(round(b)) if b is not None and b >= 1 else 1}")


def system_id(telescope, focal_mm, cam_id: str) -> str:
    """The system profile's id and file stem: <telescope>-<focal>mm__<camera id>."""
    f = _finite(focal_mm)
    return f"{slug(telescope)}-{int(round(f)) if f is not None and f > 0 else 0}mm__{cam_id}"


def profile_path(kind: str, pid: str) -> str:
    """kind = 'cameras' | 'systems' | 'specs'."""
    return os.path.join(sub_dir(kind), f"{pid}.json")


# ---- value records -------------------------------------------------------------------------------------------------

def record(value, unit: str, source: str, sigma=None, n=None, date=None, conditions=None, run=None,
           note: str | None = None) -> dict:
    """A value record in the format-v1 key order. A value that is not a finite number (None, NaN) is stored as None
    with source 'none'."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    if isinstance(value, float) and not math.isfinite(value):
        value = None
    if value is None:
        source = "none"
    out = {"value": value, "unit": unit, "sigma": sigma, "source": source, "n": n, "date": date,
           "conditions": conditions, "run": run}
    if note:
        out["note"] = note
    return out


def is_record(obj) -> bool:
    return isinstance(obj, dict) and "value" in obj and "source" in obj and "unit" in obj


def value_of(rec, default=None):
    """The number in a value record (None-safe; a record with source 'none' gives default)."""
    if not is_record(rec) or rec.get("source") == "none":
        return default
    v = rec.get("value")
    return default if v is None else v


def iter_records(doc, path: tuple = ()):
    """Every value record in a profile, as (path tuple, record)."""
    if is_record(doc):
        yield path, doc
        return
    if isinstance(doc, dict):
        for k, v in doc.items():
            yield from iter_records(v, path + (k,))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            yield from iter_records(v, path + (i,))


# ---- reading, checking, writing ------------------------------------------------------------------------------------

def dumps(doc: dict) -> str:
    """The one way profiles are written (see the module text: byte-identical round trips)."""
    return json.dumps(doc, indent=2, ensure_ascii=False)


def loads(text: str) -> dict:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProfileError(f"it is not valid JSON (line {exc.lineno}: {exc.msg})") from None
    if not isinstance(doc, dict):
        raise ProfileError("it is not a JSON object")
    return doc


def load(path: str) -> dict:
    """Read a profile, spec or on-sky log. ProfileError with a plain reason when it cannot be used."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise ProfileError(f"it could not be opened ({exc.strerror or exc})") from None
    except UnicodeDecodeError:
        raise ProfileError("it is not a UTF-8 text file") from None
    doc = loads(text)
    fmt = doc.get("format")
    if fmt not in (CAMERA_FORMAT, SYSTEM_FORMAT, SPEC_FORMAT, ONSKY_FORMAT):
        raise ProfileError(f"it is not a SHOBS profile (format {fmt!r})")
    ver = doc.get("format_version")
    if not isinstance(ver, int) or ver > FORMAT_VERSION:
        raise ProfileError(f"it is format version {ver!r}; this program reads version {FORMAT_VERSION} "
                           "(a newer SHOBS app wrote it: update this one)")
    return doc


def validate(doc: dict) -> list[str]:
    """Problems with a profile as plain sentences ([] = fine). Unknown extra keys are allowed (later versions may
    add some); a value record must have every key and a known source."""
    out: list[str] = []
    fmt = doc.get("format")
    if fmt not in (CAMERA_FORMAT, SYSTEM_FORMAT, SPEC_FORMAT, ONSKY_FORMAT):
        return [f"format is {fmt!r}, not a SHOBS profile"]
    if doc.get("format_version") != FORMAT_VERSION:
        out.append(f"format_version is {doc.get('format_version')!r}, expected {FORMAT_VERSION}")
    if fmt in (CAMERA_FORMAT, SYSTEM_FORMAT):
        for key in ("profile_id", "revision", "created", "updated"):
            if key not in doc:
                out.append(f"{key} is missing")
        if not isinstance(doc.get("revision"), int) or doc.get("revision", 0) < 1:
            out.append("revision must be a whole number from 1")
        if fmt == CAMERA_FORMAT:
            for key in ("camera", "mode", "values"):
                if not isinstance(doc.get(key), dict):
                    out.append(f"{key} is missing")
        else:
            for key in ("camera_profile", "telescope", "bands"):
                if key not in doc:
                    out.append(f"{key} is missing")
            if "site" in doc and not (isinstance(doc["site"], str) and "never stored" in doc["site"]):
                out.append("a system profile never stores the site")
    if fmt == SPEC_FORMAT:
        if not isinstance(doc.get("curves"), dict):
            out.append("curves is missing")
        for name, pts in (doc.get("curves") or {}).items():
            if name == "x":
                continue
            if not isinstance(pts, list) or not all(isinstance(p, list) and len(p) == 2 for p in pts):
                out.append(f"curve {name} must be a list of [x, y] points")
    for path, rec in iter_records(doc):
        where = ".".join(str(p) for p in path)
        missing = [k for k in RECORD_KEYS if k not in rec]
        if missing:
            out.append(f"{where}: missing {', '.join(missing)}")
        if rec.get("source") not in SOURCES:
            out.append(f"{where}: unknown source {rec.get('source')!r}")
        v = rec.get("value")
        if v is not None and not isinstance(v, (int, float, list, str)):
            out.append(f"{where}: value is not a number")
    return out


def now_iso() -> str:
    """Local time with its UTC offset, to the second (as the approved examples)."""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _atomic_write(path: str, text: str) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_json(path: str, doc: dict) -> None:
    """Write any SHOBS JSON document (atomic: a crash never leaves half a file)."""
    _atomic_write(path, dumps(doc))


def save_profile(doc: dict, written_by: str = "") -> str:
    """Save a camera or system profile (or a spec) in its folder. When a different version is already there, the old
    one goes to history/ first and the revision goes up by one; saving identical content changes nothing. Returns the
    path. ProfileError when the profile does not validate."""
    problems = validate(doc)
    if problems:
        raise ProfileError("the profile is not valid: " + "; ".join(problems[:5]))
    fmt = doc["format"]
    kind = FORMATS.get(fmt)
    if kind is None:
        raise ProfileError("only camera and system profiles and specs are saved here")
    pid = doc.get("profile_id") if fmt != SPEC_FORMAT else (doc.get("spec_id") or slug(doc.get("model")))
    path = profile_path(kind, pid)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                old_text = fh.read()
        except (OSError, UnicodeDecodeError):
            old_text = None
        if old_text == dumps(doc):
            return path
        if old_text is not None:
            try:
                old = loads(old_text)
            except ProfileError:
                old = {}
            hist = sub_dir("history")
            rev = old.get("revision", 0) if isinstance(old.get("revision"), int) else 0
            stamp = time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(path, os.path.join(hist, f"{kind}__{pid}__rev{rev}__{stamp}.json"))
            if fmt != SPEC_FORMAT:
                doc = dict(doc)
                doc["revision"] = max(int(doc.get("revision", 1)), rev + 1)
                doc["updated"] = now_iso()
                if written_by:
                    doc["written_by"] = written_by
    write_json(path, doc)
    return path


def history_of(kind: str, pid: str) -> list[str]:
    """Earlier revisions of one profile in history/, oldest first."""
    hist = sub_dir("history")
    pre = f"{kind}__{pid}__rev"
    return sorted((os.path.join(hist, n) for n in os.listdir(hist) if n.startswith(pre) and n.endswith(".json")),
                  key=lambda p: os.path.getmtime(p))


def list_profiles(kind: str) -> list[str]:
    """Profile files of one kind ('cameras', 'systems', 'specs'), by name."""
    d = sub_dir(kind)
    return sorted(os.path.join(d, n) for n in os.listdir(d) if n.endswith(".json") and not n.startswith("."))


# ---- headers → setup key -------------------------------------------------------------------------------------------

def _finite(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _text(v):
    if isinstance(v, str) and v.strip() and v.strip().upper() not in NONE_WORDS:
        return v.strip()
    return None


def setup_key(header: dict) -> dict:
    """What a frame's header says about the setup (None where it says nothing):
    instrume, gain_setting (GAIN: the camera's gain SETTING, not e⁻/ADU), offset (OFFSET / BLKLEVEL), binning
    (XBINNING), focal_mm (FOCALLEN), pixel_um (XPIXSZ ÷ binning), bayer_pattern (BAYERPAT; 'MONO' when it says none),
    e_per_adu (EGAIN), set_temp_c (SET-TEMP), camera_id. TELESCOP is kept but never used as the telescope name:
    ASIAIR writes the mount driver there."""
    h = {str(k).upper(): v for k, v in (header or {}).items()}
    inst = _text(h.get("INSTRUME"))
    gain = _finite(h.get("GAIN"))
    off = _finite(h.get("OFFSET"))
    if off is None:
        off = _finite(h.get("BLKLEVEL"))
    xb = _finite(h.get("XBINNING"))
    binning = int(round(xb)) if xb is not None and 1 <= xb <= 16 else None
    xp = _finite(h.get("XPIXSZ"))
    bayer = h.get("BAYERPAT")
    if isinstance(bayer, str) and bayer.strip():
        bp = bayer.strip().upper()
        bayer = bp if bp in ("RGGB", "BGGR", "GRBG", "GBRG") else ("MONO" if bp in NONE_WORDS or bp == "MONO" else bp)
    else:
        bayer = None
    eg = _finite(h.get("EGAIN"))
    out = {
        "instrume": inst,
        "gain_setting": gain,
        "offset": off,
        "binning": binning,
        "focal_mm": _finite(h.get("FOCALLEN")),
        "pixel_um": round(xp / (binning or 1), 4) if xp and xp > 0 else None,
        "bayer_pattern": bayer,
        "e_per_adu": eg if eg is not None and 0.01 <= eg <= 100 else None,
        "set_temp_c": _finite(h.get("SET-TEMP")),
        "telescop_header": _text(h.get("TELESCOP")),
    }
    out["camera_id"] = camera_id(inst or "unknown camera", gain, off, binning or 1)
    return out


def setup_missing(key: dict) -> list[str]:
    """The header keywords a setup key lacks (for "asked once and remembered")."""
    names = {"instrume": "INSTRUME", "gain_setting": "GAIN", "offset": "OFFSET", "binning": "XBINNING",
             "focal_mm": "FOCALLEN", "pixel_um": "XPIXSZ"}
    return [kw for k, kw in names.items() if key.get(k) is None]


# ---- finding profiles ----------------------------------------------------------------------------------------------

def find_camera_profile(cam_id: str) -> dict | None:
    """The saved camera profile with this id, or None."""
    path = profile_path("cameras", cam_id)
    if not os.path.exists(path):
        return None
    try:
        return load(path)
    except ProfileError:
        return None


def find_system_profile(cam_id: str, focal_mm=None, tolerance: float = 0.03) -> dict | None:
    """The system profile for this camera id whose focal length is within tolerance (3 %) of focal_mm (ASIAIR's
    FOCALLEN moves a few mm with each plate solve), or the only one for the camera when focal_mm is None."""
    found = []
    for path in list_profiles("systems"):
        try:
            doc = load(path)
        except ProfileError:
            continue
        if doc.get("camera_profile") != cam_id:
            continue
        tel = doc.get("telescope") or {}
        f = _finite(tel.get("focal_mm"))
        if focal_mm is None or f is None or abs(f - float(focal_mm)) <= tolerance * max(f, 1.0):
            found.append(doc)
    return found[0] if len(found) == 1 or (found and focal_mm is not None) else None


def find_spec(instrume, extra_dirs: tuple = ()) -> dict | None:
    """The camera spec whose matches_instrume list holds this INSTRUME (case and spacing ignored). The user's specs
    folder first, then any extra folders (an app's built-in specs)."""
    want = slug(instrume)
    if want == "unknown":
        return None
    for d in (sub_dir("specs"),) + tuple(extra_dirs):
        if not d or not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".json"):
                continue
            try:
                doc = load(os.path.join(d, name))
            except ProfileError:
                continue
            if doc.get("format") != SPEC_FORMAT:
                continue
            if any(slug(m) == want for m in doc.get("matches_instrume") or []):
                return doc
    return None


def spec_at(spec: dict | None, curve: str, gain_setting):
    """A spec curve read at a gain setting: straight-line between the two nearest points, flat beyond the ends,
    None when the spec has no such curve. A curve with a jump (an HCG switch) has two points at the same x; the one
    listed last is used from that x up."""
    if not spec or gain_setting is None:
        return None
    pts = (spec.get("curves") or {}).get(curve)
    g = _finite(gain_setting)
    if not pts or g is None:
        return None
    pts = [(float(x), float(y)) for x, y in pts if _finite(x) is not None and _finite(y) is not None]
    if not pts:
        return None
    pts.sort(key=lambda p: p[0])
    if g <= pts[0][0]:
        return pts[0][1] if g < pts[0][0] or len(pts) == 1 or pts[1][0] != pts[0][0] else pts[1][1]
    if g >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= g < x1:
            return y0 + (y1 - y0) * (g - x0) / (x1 - x0)
        if g == x1:
            later = [y for x, y in pts if x == x1]
            return later[-1]
    return pts[-1][1]


def camera_value(profile: dict | None, key: str, channel: str | None = None):
    """A camera profile's value for key (e.g. 'gain_e_per_adu'), the channel's own when the bench run measured one,
    as (value, record) or (None, None)."""
    if not profile:
        return None, None
    if channel:
        ch = ((profile.get("per_channel") or {}).get(channel) or {}).get(key)
        if is_record(ch) and value_of(ch) is not None:
            return value_of(ch), ch
    rec = (profile.get("values") or {}).get(key)
    return value_of(rec), (rec if is_record(rec) else None)


def self_check() -> list[str]:
    """Quick check of this file's own pieces (each app's self-test calls it). [] = fine."""
    out = []
    if camera_id("ZWO ASI2600MC Air", 100, 50, 1) != "zwo-asi2600mc-air__g100__o50__bin1":
        out.append("camera_id")
    if system_id("14in RC", 2900, "x") != "14in-rc-2900mm__x":
        out.append("system_id")
    r = record(1.5, "e-", "spec")
    if list(r) != list(RECORD_KEYS) or validate({"format": SPEC_FORMAT, "format_version": 1, "curves": {}}):
        out.append("record")
    if spec_at({"curves": {"g": [[0, 1.0], [100, 3.0]]}}, "g", 50) != 2.0:
        out.append("spec_at")
    return out
