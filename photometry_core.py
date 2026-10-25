"""Calibration, debayer, aperture photometry, period search, AAVSO export."""

from __future__ import annotations

import json
import re as _re
import math
import os
import sys

# 2.2.12: UI fonts per platform. Segoe UI and Consolas exist only on Windows; elsewhere Tk falls back to its
# default (proportional) font, which breaks the lined-up Series and Period boxes. Menlo ships with every Mac.
if sys.platform == "win32":
    UI_FONT, MONO_FONT = "Segoe UI", "Consolas"
elif sys.platform == "darwin":
    UI_FONT, MONO_FONT = "Helvetica Neue", "Menlo"
else:
    UI_FONT, MONO_FONT = "DejaVu Sans", "DejaVu Sans Mono"


def dtg(when=None) -> str:
    """2.2.12 (John): a military Date Time Group, Zulu, for when a file was made: "121153Z OCT 26"."""
    from datetime import datetime, timezone
    now = when or datetime.now(timezone.utc)
    return now.strftime("%d%H%MZ ") + now.strftime("%b %y").upper()


def created_line() -> str:
    """The comment line that stamps a written file (CSV, AAVSO reports)."""
    return f"# Created {dtg()} by SHOBS-P"


def wheel_units(delta) -> int:
    """2.2.12: scroll units for a <MouseWheel> delta. Windows sends multiples of 120; a Mac trackpad sends small
    deltas (1-10), which int(-delta / 120) turned into 0, so nothing scrolled. Any non-zero delta moves at least
    one unit; on macOS only its sign counts."""
    try:
        delta = float(delta)
    except (TypeError, ValueError):
        return 0
    if not delta:
        return 0
    if sys.platform == "darwin":
        return -1 if delta > 0 else 1
    n = int(-delta / 120)
    return n if n else (-1 if delta > 0 else 1)
from dataclasses import dataclass, field
from datetime import datetime, timezone
from glob import glob

import numpy as np
from astropy.time import Time
import astropy.units as u
from astropy.coordinates import AltAz, EarthLocation, SkyCoord
from astropy.nddata import CCDData
from astropy.stats import SigmaClip
from astropy.timeseries import LombScargle
from ccdproc import flat_correct, subtract_bias, subtract_dark
from photutils.aperture import ApertureStats, CircularAnnulus, CircularAperture, aperture_photometry
from photutils.centroids import centroid_com
from photutils.morphology import data_properties

try:
    from astropy.io import fits
except ImportError:  # pragma: no cover
    fits = None


# 2.2.15 (items 8, 9): upload pages, all in one place. AAVSO's WebObs 2.0 photometry submit page and the AAVSO
# Exoplanet Database submit page (both checked 15 Oct 2026), and ExoFOP-TESS.
AAVSO_WEBOBS_URL = "https://apps.aavso.org/v2/data/submit/photometry/"
AAVSO_EXOPLANET_URL = "https://apps.aavso.org/exosite/submit"
EXOFOP_TESS_URL = "https://exofop.ipac.caltech.edu/tess/"


def exofop_target_url(toi=None, tic=None) -> str:
    """2.2.15 (item 9): the ExoFOP-TESS page of a TOI (target.php?toi=NNNN) or a TIC (target.php?id=NNN); ''
    when neither is known. TOI-2046 b or "2046.01" -> toi=2046; "TIC 468574941" -> id=468574941."""
    m = _re.search(r"(\d+)", str(toi or ""))
    if m and int(m.group(1)) > 0:
        return f"{EXOFOP_TESS_URL}target.php?toi={int(m.group(1))}"
    m = _re.search(r"(\d+)", str(tic or ""))
    if m and int(m.group(1)) > 0:
        return f"{EXOFOP_TESS_URL}target.php?id={int(m.group(1))}"
    return ""


FITS_EXTS =(".fits", ".fit", ".fts", ".fits.gz", ".fit.gz")
# 2.2.1: PixInsight's XISF is read too (uncompressed, zlib, or zlib with byte shuffling).
IMAGE_EXTS = FITS_EXTS + (".fits.fz", ".xisf")


def list_fits(folder: str) -> list[str]:
    """Image files SHOBS-P can read in a folder (FITS and XISF)."""
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(os.listdir(folder)):
        low = name.lower()
        if low.endswith(IMAGE_EXTS):
            out.append(os.path.join(folder, name))
    return out


# ---- 2.2.17 (item 6): each frame's own number, from its file name ------------------------------------------------
def frame_number(path: str):
    """The frame's own number from its file name: the trailing digits before the extension (Light_…_0222.fit -> 222;
    NINA's 2026-09-01_23-17-28_i'_-50.04_120.00s_0000.fits -> 0). Up to 5 digits, so a trailing time such as
    _205345 is not taken for a counter. None when the name ends in no number."""
    import re
    name = os.path.basename(str(path))
    low = name.lower()
    for ext in sorted(IMAGE_EXTS, key=len, reverse=True):
        if low.endswith(ext):
            name = name[: -len(ext)]
            break
    else:
        name = os.path.splitext(name)[0]
    if re.search(r"(?<!\d)\d{4}-\d{2}-\d{2}$", name):
        return None   # ends in a date (…_2026-10-02), not a frame counter
    m = re.search(r"(?<!\d)(\d{1,5})$", name)
    return int(m.group(1)) if m else None


def frame_labels(paths: list[str]) -> dict:
    """{path: label} for a list of frames: the file's own number ("222"); "#k" (position in the list, from 1) when the
    name has no number; two files with the same number (two sequences in one folder, or two folders) get a, b, … in
    list order ("12a", "12b"), so every frame has its own label."""
    nums = [frame_number(p) for p in paths]
    count = {}
    for n in nums:
        if n is not None:
            count[n] = count.get(n, 0) + 1
    seen, out = {}, {}
    for k, (p, n) in enumerate(zip(paths, nums)):
        if n is None:
            out[p] = f"#{k + 1}"
        elif count[n] > 1:
            i = seen.get(n, 0)
            seen[n] = i + 1
            suffix = ""
            while True:   # a, b, … z, aa, ab …
                suffix = chr(ord("a") + i % 26) + suffix
                i = i // 26 - 1
                if i < 0:
                    break
            out[p] = f"{n}{suffix}"
        else:
            out[p] = str(n)
    return out


_FRAME_LABEL_CACHE = {}
_CAL_VERDICTS = {}   # 2.2.17 (round 2): {(path, size): is_calibration_frame reason}; a file's header is read once


def _folder_frame_labels(folder: str):
    """({lights: label}, {every image but SHOBS-P masters: label}) for a folder. The lights set is the list Blink shows
    for lights (masters out, then split_lights), so numbers, letters and #k positions match the page; the second set is
    the Flats list (masters out).

    2.2.17 (round 2): cached on the folder's file list (names and sizes, from one directory scan), not its modification
    time, so a report or CSV written into the folder costs nothing; each file's calibration verdict (a header read) is
    kept per (path, size), so a frame NINA has just saved costs one header and unchanged files none."""
    try:
        with os.scandir(folder) as it:
            entries = sorted((e.name, e.stat().st_size) for e in it
                             if e.name.lower().endswith(IMAGE_EXTS) and not e.name.startswith("."))
    except OSError:
        return None
    images_named = [(n, size) for n, size in entries if not is_shobsp_master_name(n)]
    key = (folder, tuple(images_named))
    hit = _FRAME_LABEL_CACHE.get(key)
    if hit is None:
        images = [os.path.join(folder, n) for n, _size in images_named]
        sizes = dict(zip(images, (size for _n, size in images_named)))

        def verdict(path):
            vkey = (path, sizes.get(path))
            if vkey not in _CAL_VERDICTS:
                if len(_CAL_VERDICTS) > 50000:
                    _CAL_VERDICTS.clear()
                _CAL_VERDICTS[vkey] = is_calibration_frame(path)
            return _CAL_VERDICTS[vkey]

        try:
            lights = split_lights(images, verdict)[0]
        except Exception:
            lights = images
        hit = (frame_labels(lights), frame_labels(images))
        if len(_FRAME_LABEL_CACHE) > 20:
            _FRAME_LABEL_CACHE.clear()
        _FRAME_LABEL_CACHE[key] = hit
    return hit


def frame_label(path: str, fallback: dict | None = None) -> str:
    """One frame's label (see frame_labels), worked out from its folder's frame list (lights as Blink lists them, else
    every image but masters, e.g. flats) so it is the same on the Blink page, in the log and in the CSV whatever is
    rejected. When the folder is gone (a series made on another PC): `fallback` ({path: label} from frame_labels of
    that night's files, computed once by the caller), else the file's own number."""
    sets = _folder_frame_labels(os.path.dirname(str(path)))
    for labels in (sets or ()):
        if path in labels:
            return labels[path]
    if fallback and path in fallback:
        return fallback[path]
    return frame_labels([path]).get(path, "")


def frame_labels_for(paths: list[str]) -> list[str]:
    """frame_label for many frames, one folder look-up per folder (the Blink rejected list, the run log)."""
    sets, out = {}, []
    for p in paths:
        folder = os.path.dirname(str(p))
        if folder not in sets:
            sets[folder] = _folder_frame_labels(folder) or ()
        out.append(next((labels[p] for labels in sets[folder] if p in labels), None) or frame_label(p))
    return out


def frame_list_text(labels: list[str], limit: int = 40) -> str:
    """'63, 64, 65' (sorted by number; long lists end with ' … (+n more)')."""
    def key(t):
        import re
        m = re.match(r"#?(\d+)(.*)$", t)
        return (t.startswith("#"), int(m.group(1)) if m else 10 ** 9, m.group(2) if m else t)
    labels = sorted(labels, key=key)
    text = ", ".join(labels[:limit])
    return text + (f" … (+{len(labels) - limit} more)" if len(labels) > limit else "")


def unreadable_files(folder: str) -> list[str]:
    """Files in a folder that are not images SHOBS-P reads (raw camera files, TIFF, JPEG …), ignoring
    hidden files and the usual side files."""
    if not folder or not os.path.isdir(folder):
        return []
    skip = (".txt", ".log", ".json", ".ini", ".db", ".csv", ".xml", ".md")
    out = []
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        low = name.lower()
        if name.startswith(".") or os.path.isdir(full) or low.endswith(IMAGE_EXTS) or low.endswith(skip):
            continue
        out.append(name)
    return out


_XISF_TYPES = {"UInt8": np.uint8, "UInt16": np.uint16, "UInt32": np.uint32, "Int8": np.int8, "Int16": np.int16,
               "Int32": np.int32, "Float32": np.float32, "Float64": np.float64}


def _xisf_header(raw_head: bytes):
    """Parse an XISF header: (Image element, FITS-style header dict)."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(raw_head.decode("utf-8", "replace").strip("\x00").strip())
    image = None
    for el in root.iter():
        if el.tag.split("}")[-1] == "Image":
            image = el
            break
    if image is None:
        raise ValueError("no image in this XISF file")
    header = {}
    for el in image.iter():
        tag = el.tag.split("}")[-1]
        if tag == "FITSKeyword":
            key = (el.get("name") or "").strip().upper()
            value = (el.get("value") or "").strip()
            if not key:
                continue
            if value.startswith("'") and value.endswith("'") and len(value) >= 2:
                value = value[1:-1].strip()
            else:
                try:
                    value = int(value) if _re.fullmatch(r"[+-]?\d+", value) else float(value)
                except ValueError:
                    if value in ("T", "F"):
                        value = value == "T"
            if key in ("HISTORY", "COMMENT") and key in header:
                header[key] = f"{header[key]} {value}"
            else:
                header[key] = value
        elif tag == "Property" and (el.get("id") or "").startswith("PixInsight:ProcessingHistory"):
            header["HISTORY"] = f"{header.get('HISTORY', '')} {el.text or ''}".strip()
    return image, header


def read_xisf(path: str, header_only: bool = False):
    """A PixInsight XISF monolithic file: (2-D float32 image, header dict). A color image gives the channel set with
    set_rgb_channel (2.2.18; the first channel when none is set); Bayer (CFA) frames come back as the mosaic, like a
    FITS from the camera."""
    import zlib

    with open(path, "rb") as fh:
        sig = fh.read(16)
        if sig[:8] != b"XISF0100":
            raise ValueError(f"{os.path.basename(path)} is not an XISF 1.0 file")
        length = int.from_bytes(sig[8:12], "little")
        image, header = _xisf_header(fh.read(length))
        geometry = [int(v) for v in (image.get("geometry") or "0:0:1").split(":")]
        width, height = geometry[0], geometry[1]
        channels = geometry[2] if len(geometry) > 2 else 1
        header.setdefault("NAXIS1", width)
        header.setdefault("NAXIS2", height)
        if channels > 1:
            header.setdefault("NAXIS3", channels)   # 2.2.11: a color image (2.2.18: see set_rgb_channel)
        fmt0 = image.get("sampleFormat") or "UInt16"
        header.setdefault("BITPIX", -32 if fmt0.startswith("Float") else 8 * np.dtype(_XISF_TYPES.get(fmt0, np.uint16)).itemsize)
        if header_only:
            return None, header
        fmt = image.get("sampleFormat") or "UInt16"
        if fmt not in _XISF_TYPES:
            raise ValueError(f"{os.path.basename(path)}: XISF sample format {fmt} is not supported")
        dtype = np.dtype(_XISF_TYPES[fmt]).newbyteorder(">" if image.get("byteOrder") == "big" else "<")
        location = (image.get("location") or "").split(":")
        if location[0] != "attachment" or len(location) < 3:
            raise ValueError(f"{os.path.basename(path)}: only XISF images stored as attachments are supported")
        fh.seek(int(location[1]))
        blob = fh.read(int(location[2]))
    comp = (image.get("compression") or "").split(":")
    if comp[0]:
        codec = comp[0].lower()
        if codec not in ("zlib", "zlib+sh"):
            raise ValueError(f"{os.path.basename(path)}: XISF compression {comp[0]} is not supported (save it "
                             "uncompressed or with zlib in PixInsight, or as FITS)")
        blob = zlib.decompress(blob)
        if codec.endswith("+sh"):
            item = int(comp[2]) if len(comp) > 2 else dtype.itemsize
            n = len(blob) // item
            blob = np.frombuffer(blob[:n * item], dtype=np.uint8).reshape(item, n).T.tobytes()
    data = np.frombuffer(blob, dtype=dtype, count=width * height * channels)
    if (image.get("pixelStorage") or "Planar").lower() == "normal" and channels > 1:
        data = np.moveaxis(data.reshape(height, width, channels), 2, 0)
    else:
        data = data.reshape(channels, height, width)
    data = data.astype(np.float32)
    # 2.2.18 (item 1): a color image gives the chosen channel (or luminance); before, always the first (red).
    data = rgb_plane(data) if channels == 3 else data[0]
    if fmt.startswith("Float"):
        # PixInsight keeps floating-point pixels in a 0..1 range (the bounds attribute): put them back on the
        # 16-bit ADU scale the rest of the app (saturation, noise, masters in ADU) works in.
        try:
            lo, hi = (float(v) for v in (image.get("bounds") or "0:1").split(":")[:2])
        except ValueError:
            lo, hi = 0.0, 1.0
        if hi > lo:
            data = (data - lo) / (hi - lo) * 65535.0
    return data, header


def read_fits(path: str) -> tuple[np.ndarray, dict]:
    if path.lower().endswith(".xisf"):
        return read_xisf(path)
    if fits is None:
        raise RuntimeError("astropy is required. Install with: pip install astropy")
    with fits.open(path, memmap=False) as hdul:
        hdu = hdul[0]
        if hdu.data is None:
            for extra in hdul[1:]:
                if extra.data is not None:
                    hdu = extra
                    break
        data = np.asarray(hdu.data, dtype=np.float32)
        header = dict(hdu.header)
    if data.ndim == 3 and data.shape[0] == 3:
        data = rgb_plane(data)   # 2.2.18 (item 1): the chosen channel of a color image, not always red
    elif data.ndim == 3 and data.shape[0] in (1, 4):
        data = data[0]
    if data.ndim != 2:
        raise ValueError(f"{os.path.basename(path)} is not a 2-D image (shape {data.shape})")
    return data, header


def header_exptime(header: dict) -> float:
    for key in ("EXPTIME", "EXPOSURE", "EXP_TIME"):
        val = header.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return 1.0


def header_bayer(header: dict) -> str | None:
    for key in ("BAYERPAT", "COLORTYP"):
        val = header.get(key)
        if isinstance(val, str) and val.strip():
            pat = val.strip().upper()
            if pat in ("RGGB", "BGGR", "GRBG", "GBRG"):
                return pat
    return None


def _parse_iso(text: str) -> datetime:
    s = text.strip().replace("Z", "").replace("z", "")
    if "T" not in s and " " in s:
        s = s.replace(" ", "T", 1)
    if "." in s:
        whole, frac = s.split(".", 1)
        frac = "".join(ch for ch in frac if ch.isdigit())[:6]
        s = whole + "." + frac
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def datetime_to_jd(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    y = dt.year
    m = dt.month
    day = dt.day + (
        dt.hour + (dt.minute + (dt.second + dt.microsecond / 1e6) / 60.0) / 60.0
    ) / 24.0
    if m <= 2:
        y -= 1
        m += 12
    a = math.floor(y / 100)
    b = 2 - a + math.floor(a / 4)
    jd = math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + day + b - 1524.5
    return jd


def mid_jd(header: dict) -> float:
    """Geocentric UTC Julian Date at mid-exposure. BJD is deliberately not used: AAVSO DATE=JD is geocentric."""
    exp = header_exptime(header)
    half = exp / 2.0 / 86400.0
    for key in ("JD-MID", "MIDJD"):
        val = header.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    if header.get("JD") is not None:
        try:
            return float(header["JD"]) + half
        except (TypeError, ValueError):
            pass
    for key in ("DATE-OBS", "DATE_OBS", "DATEOBS"):
        val = header.get(key)
        if isinstance(val, str) and val.strip():
            text = val.strip().replace("Z", "").replace(" ", "T", 1)
            offset_h = 0.0
            m = _re.search(r"T[0-9:.]+([+-])(\d{2}):?(\d{2})$", text)
            if m:
                # Local time with a UTC offset (MicroObservatory writes "2015-01-25T19:39:40.386-0700").
                offset_h = (1 if m.group(1) == "+" else -1) * (int(m.group(2)) + int(m.group(3)) / 60.0)
                text = text[: m.start(1)]
            if "T" not in text:
                # Old-style header: date in DATE-OBS, time in TIME-OBS or UT.
                clock = header.get("TIME-OBS") or header.get("UT") or header.get("UTSTART")
                if not isinstance(clock, str) or not clock.strip():
                    raise ValueError("DATE-OBS has a date but no time, and there is no TIME-OBS or UT")
                text = f"{text}T{clock.strip()}"
            return Time(text, format="isot", scale="utc").jd - offset_h / 24.0 + half
    raise ValueError("No DATE-OBS or JD in FITS header")


# Monochrome sensors (MicroObservatory and other mono cameras): no Bayer mosaic, so binning is plain and the
# "debayer" passes the frame through. Set by the app when the Bayer pattern is MONO.
MONO = "MONO"
_SENSOR = {"mono": False, "rgb_channel": None}

# 2.2.18 (item 1): a color image that other software already debayered (three channels, NAXIS3 = 3): the channel
# measured, with its AAVSO filter code and the comps' band (as an OSC camera's channel), set by the app for a run.
RGB_CHANNELS = {"green": ("TG", "V"), "red": ("TR", "R"), "blue": ("TB", "B"), "luminance": ("CV", "V")}
RGB_CHANNEL_WORDS = {"green": "green (TG)", "red": "red (TR)", "blue": "blue (TB)", "luminance": "luminance (CV)"}


def set_mono(flag: bool) -> None:
    _SENSOR["mono"] = bool(flag)


def set_rgb_channel(channel: str | None) -> None:
    """2.2.18 (item 1): the channel read from a three-channel (already debayered) image; None = the first channel,
    as before 2.2.18."""
    _SENSOR["rgb_channel"] = channel if channel in RGB_CHANNELS else None


def rgb_channel() -> str | None:
    return _SENSOR.get("rgb_channel")


def is_rgb_header(header: dict | None) -> bool:
    """2.2.18 (item 1): a color image already debayered by other software: three channels (NAXIS3 = 3)."""
    header = header or {}
    try:
        if int(header.get("NAXIS", 3) or 3) < 3:
            return False
        return int(header.get("NAXIS3", 1) or 1) == 3
    except (TypeError, ValueError):
        return False


def rgb_plane(planes: np.ndarray, channel: str | None = None) -> np.ndarray:
    """2.2.18 (item 1): one 2-D image from an (3, h, w) red/green/blue stack: the chosen channel, or luminance (the
    same Rec. 601 weights as debayer's luminance). channel None: the set one (set_rgb_channel), else the first."""
    channel = channel or rgb_channel()
    if channel == "luminance":
        return (0.299 * planes[0] + 0.587 * planes[1] + 0.114 * planes[2]).astype(np.float32)
    return np.asarray(planes[{"red": 0, "green": 1, "blue": 2}.get(channel, 0)], dtype=np.float32)


def is_microobservatory(path: str, header: dict) -> bool:
    text = " ".join(str(header.get(k, "")) for k in ("TELESCOP", "INSTRUME", "OBSERVAT", "ORIGIN", "SITENAME"))
    return os.path.basename(path).upper().startswith("MOBS_") or "MICROOBS" in text.upper().replace(" ", "")


def header_site(header: dict):
    """(lat, lon east-positive, elevation m) from common FITS site keywords, or None."""
    def num(*keys):
        for key in keys:
            v = header.get(key)
            if v is None:
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                parts = str(v).replace(":", " ").split()
                try:
                    d = [float(x) for x in parts]
                    sign = -1.0 if str(v).strip().startswith("-") else 1.0
                    return sign * (abs(d[0]) + (d[1] if len(d) > 1 else 0) / 60 + (d[2] if len(d) > 2 else 0) / 3600)
                except (ValueError, IndexError):
                    continue
        return None
    lat = num("SITELAT", "LAT-OBS", "OBSLAT", "LATITUDE", "OBSGEO-B")
    lon = num("SITELONG", "LONG-OBS", "OBSLON", "LONGITUD", "LONGITUDE", "OBSGEO-L")
    elev = num("SITEELEV", "ALT-OBS", "OBSELEV", "ELEVATIO", "OBSGEO-H")
    if elev is None and is_unistellar(header):
        elev = num("ALTITUDE")   # Unistellar: "altitude in meters of observing site" (elsewhere often pointing altitude)
    if lat is None or lon is None:
        return None
    return lat, lon, elev


def is_unistellar(header: dict) -> bool:
    text = " ".join(str(header.get(k, "")) for k in ("ORIGIN", "TELESCOP"))
    return "UNISTELLAR" in text.upper() or "EVSCOPE" in text.upper().replace(" ", "")


def header_optics(header: dict) -> dict:
    """2.2.9: optics the FITS header gives: {"focal_mm", "pixel_um", "aperture_mm"} (only the ones found) and
    {"source": {name: keyword}}. Unistellar eVscopes (114 mm, 450 mm, 2.9 µm IMX347) fill what their headers lack."""
    out: dict = {"source": {}}

    def num(*keys):
        for key in keys:
            try:
                v = float(header.get(key))
            except (TypeError, ValueError):
                continue
            if math.isfinite(v) and v > 0:
                return v, key
        return None, None

    focal, k = num("FOCALLEN", "FOCAL", "TELFOCAL")
    if focal:
        out["focal_mm"], out["source"]["focal_mm"] = focal, k
    pix, k = num("XPIXSZ", "PIXSIZE1", "PIXSIZE")
    if pix:
        # XPIXSZ is written after camera binning (MaxIm, N.I.N.A., ASIAIR); the Pixel size box is the unbinned pixel.
        try:
            cam_bin = int(float(header.get("XBINNING") or 1))
        except (TypeError, ValueError):
            cam_bin = 1
        if cam_bin > 1:
            pix /= cam_bin
            k = f"{k} / XBINNING {cam_bin}"
        out["pixel_um"], out["source"]["pixel_um"] = pix, k
    ap, k = num("APTDIA", "APERTURE", "TELAPER")
    if ap:
        if ap < 3:      # some programs write the aperture in metres
            ap *= 1000.0
        out["aperture_mm"], out["source"]["aperture_mm"] = ap, k
    else:
        fr, k2 = num("FOCRATIO", "FRATIO")
        if focal and fr:
            out["aperture_mm"], out["source"]["aperture_mm"] = focal / fr, "FOCALLEN/FOCRATIO"
    if is_unistellar(header):
        for key, value in (("focal_mm", 450.0), ("pixel_um", 2.9), ("aperture_mm", 114.0)):
            if key not in out:
                out[key], out["source"][key] = value, "Unistellar eVscope"
    return out


def site_distance_km(a: tuple, b: tuple) -> float:
    """Great-circle distance between two (lat, lon) in km."""
    la1, lo1, la2, lo2 = (math.radians(float(v)) for v in (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def header_plate_scale(header: dict):
    """Arcseconds per pixel of the stored frame, from plate-scale or WCS keywords, or from focal length and pixel
    size in the header; None when the header has none of them."""
    for key in ("SECPIX", "SECPIX1", "PIXSCALE", "PIXSCAL1", "SCALE", "PLTSCALE"):
        try:
            v = abs(float(header.get(key)))
            if 0.05 < v < 60:
                return v
        except (TypeError, ValueError):
            continue
    try:
        v = abs(float(header.get("CDELT1"))) * 3600.0
        if 0.05 < v < 60:
            return v
    except (TypeError, ValueError):
        pass
    try:
        cd = math.hypot(float(header.get("CD1_1")), float(header.get("CD2_1"))) * 3600.0
        if 0.05 < cd < 60:
            return cd
    except (TypeError, ValueError):
        pass
    try:
        focal = float(header.get("FOCALLEN"))
        pix = float(header.get("XPIXSZ") or header.get("PIXSIZE1"))
        if focal > 0 and pix > 0:
            return pix / focal * 206.265
    except (TypeError, ValueError):
        pass
    return None


def bin_plain(img: np.ndarray, factor: int) -> np.ndarray:
    """Average factor x factor blocks. Only for single-color images: on a Bayer mosaic this
    mixes red, green, and blue pixels into one value."""
    if factor <= 1:
        return img
    h = img.shape[0] // factor * factor
    w = img.shape[1] // factor * factor
    cropped = img[:h, :w]
    return cropped.reshape(h // factor, factor, w // factor, factor).mean(axis=(1, 3)).astype(np.float32)


# ---- 2.2.15 (GitHub #1): display stretch for the Blink and Photometry images (display only, never the data) ----
STRETCH_MODES = ("linear", "log", "asinh")
STRETCH_DEFAULT = {"mode": "linear", "black": 5.0, "white": 99.5}   # the look before 2.2.15
STRETCH_LOG_A = 1000.0      # log: log10(1 + a x) / log10(1 + a), as in DS9
STRETCH_ASINH_B = 0.1       # asinh: asinh(x / b) / asinh(1 / b); smaller b = stronger
STRETCH_SAMPLE_PIXELS = 4_000_000   # percentiles from a regular sample above this many pixels


def stretch_settings(value=None) -> dict:
    """A display stretch as {"mode", "black", "white"} (black and white points as percentiles of the image), with
    anything missing or out of range replaced by the default."""
    out = dict(STRETCH_DEFAULT)
    if isinstance(value, dict):
        if value.get("mode") in STRETCH_MODES:
            out["mode"] = value["mode"]
        for key in ("black", "white"):
            try:
                v = float(value.get(key))
                if math.isfinite(v):
                    out[key] = min(max(v, 0.0), 100.0)
            except (TypeError, ValueError):
                pass
    if out["black"] >= out["white"]:
        out["black"], out["white"] = STRETCH_DEFAULT["black"], STRETCH_DEFAULT["white"]
    return out


def stretch_limits(data: np.ndarray, black: float = 5.0, white: float = 99.5, step: int | None = None):
    """Data values at the black and white percentiles (from every `step`-th pixel; by default a regular sample of
    about STRETCH_SAMPLE_PIXELS). Always lo < hi."""
    data = np.asarray(data)
    if step is None:
        step = max(1, int(math.ceil(math.sqrt(data.size / STRETCH_SAMPLE_PIXELS)))) if data.size else 1
    sample = data[::step, ::step] if data.ndim == 2 and step > 1 else data
    finite = sample[np.isfinite(sample)]
    if finite.size:
        lo, hi = (float(v) for v in np.percentile(finite, [black, white]))
    else:
        lo, hi = 0.0, 1.0
    if not hi > lo:
        hi = lo + 1.0
    return lo, hi


def stretch_display(data: np.ndarray, stretch=None, limits=None, step: int | None = None) -> np.ndarray:
    """Map image data to display values 0..1 (float32): linear, log or asinh between the black and white points
    (percentiles, see stretch_limits). NaN shows black. The data are not changed."""
    s = stretch_settings(stretch)
    lo, hi = limits if limits is not None else stretch_limits(data, s["black"], s["white"], step)
    x = np.array(data, dtype=np.float32)          # one working copy; everything below is done in place
    x -= np.float32(lo)
    x *= np.float32(1.0 / (hi - lo))
    np.nan_to_num(x, copy=False, nan=0.0, posinf=1.0, neginf=0.0)
    np.clip(x, 0.0, 1.0, out=x)
    if s["mode"] == "log":
        x *= np.float32(STRETCH_LOG_A)
        np.log1p(x, out=x)
        x *= np.float32(1.0 / math.log(1.0 + STRETCH_LOG_A))
    elif s["mode"] == "asinh":
        x *= np.float32(1.0 / STRETCH_ASINH_B)
        np.arcsinh(x, out=x)
        x *= np.float32(1.0 / math.asinh(1.0 / STRETCH_ASINH_B))
    return x.astype(np.float32, copy=False)


def stretch_to_uint8(data: np.ndarray, stretch=None, limits=None, step: int | None = None) -> np.ndarray:
    """stretch_display as an 8-bit picture (0..255), for the Blink cache."""
    return (stretch_display(data, stretch, limits, step) * np.float32(255.0)).astype(np.uint8)


def bin_image(img: np.ndarray, factor: int) -> np.ndarray:
    """Bin a raw color (Bayer) frame and keep it a Bayer mosaic.

    Each of the four Bayer positions (for RGGB: red, green, green, blue) is binned only with
    pixels of its own color, then the four are interleaved again in the same pattern. The
    result is a smaller mosaic with the same pattern, so calibration and the debayer work on
    it exactly as on an unbinned frame, and the colors stay separate. (Before 1.7 plain 2x2
    averaging mixed one red, two green, and one blue pixel into every binned pixel.)
    """
    if factor <= 1:
        return img
    if _SENSOR["mono"]:
        return bin_plain(img, factor)
    cell = 2 * factor
    h = img.shape[0] // cell * cell
    w = img.shape[1] // cell * cell
    if h == 0 or w == 0:
        raise ValueError(f"Frame {img.shape} is too small to bin by {factor}")
    img = img[:h, :w]
    out = np.empty((h // factor, w // factor), dtype=np.float32)
    for iy in (0, 1):
        for ix in (0, 1):
            out[iy::2, ix::2] = bin_plain(img[iy::2, ix::2], factor)
    return out


def _ccd(data: np.ndarray, exptime: float | None = None) -> CCDData:
    ccd = CCDData(np.asarray(data, dtype=np.float32), unit=u.adu)
    if exptime is not None:
        ccd.meta["EXPTIME"] = float(exptime)
    return ccd


def median_stack(paths: list[str], binning: int = 1, log=None, preprocess=None) -> np.ndarray | None:
    """Batched median. Eight frames at a time, so a 100-frame ASI2600 stack does not freeze the window."""
    if not paths:
        return None
    batch = []
    partials = []
    for i, path in enumerate(paths, 1):
        data, header = read_fits(path)
        data = bin_image(data, binning)
        if preprocess is not None:
            data = preprocess(data, header)
        batch.append(data)
        if len(batch) == 8 or i == len(paths):
            partials.append(np.median(np.stack(batch, axis=0), axis=0).astype(np.float32))
            batch = []
            if log:
                log(f"  median through {i}/{len(paths)} {os.path.basename(path)}")
    if len(partials) == 1:
        return partials[0]
    return np.median(np.stack(partials, axis=0), axis=0).astype(np.float32)


def normalize_flat(flat: np.ndarray) -> np.ndarray:
    finite = np.isfinite(flat) & (flat > 0)
    if not np.any(finite):
        raise ValueError("Flat has no positive pixels")
    med = float(np.median(flat[finite]))
    out = flat / med
    out[~finite] = 1.0
    out[out < 0.2] = np.nan
    return out.astype(np.float32)


@dataclass
class Masters:
    bias: np.ndarray | None = None
    dark: np.ndarray | None = None
    flat: np.ndarray | None = None
    dark_exptime: float = 1.0
    notes: list[str] = field(default_factory=list)
    used: dict = field(default_factory=dict)      # 2.2.8: where each master came from (for the log and the night)
    headers: dict = field(default_factory=dict)   # 2.2.8: each master's header, for the checks against the lights


def build_masters(
    bias_paths: list[str],
    dark_paths: list[str],
    flat_paths: list[str],
    binning: int = 1,
    log=None,
    flats_calibrated: bool = False,
) -> Masters:
    masters = Masters()
    log = log or (lambda *_: None)
    if bias_paths:
        log(f"Median-combining {len(bias_paths)} bias frames")
        masters.bias = median_stack(bias_paths, binning, log)
        masters.notes.append(f"bias n={len(bias_paths)} batched median")
    if dark_paths:
        log(f"Median-combining {len(dark_paths)} dark frames")
        exps = [header_exptime(read_fits(path)[1]) for path in dark_paths]

        def prep_dark(data, header):
            ccd = _ccd(data, header_exptime(header))
            if masters.bias is not None:
                ccd = subtract_bias(ccd, _ccd(masters.bias))
            return np.asarray(ccd.data, dtype=np.float32)

        masters.dark = median_stack(dark_paths, binning, log, prep_dark)
        masters.dark_exptime = float(np.median(exps)) if exps else 1.0
        masters.notes.append(f"dark n={len(dark_paths)} exp={masters.dark_exptime:.3g}s")
    if flat_paths:
        log(f"Median-combining {len(flat_paths)} flat frames")

        def prep_flat(data, header):
            if flats_calibrated:
                # Already calibrated by other software (PixInsight _c): bias and dark are out already.
                return np.asarray(data, dtype=np.float32)
            ccd = _ccd(data, header_exptime(header))
            if masters.bias is not None:
                ccd = subtract_bias(ccd, _ccd(masters.bias))
            if masters.dark is not None and masters.dark_exptime > 0:
                ccd = subtract_dark(
                    ccd,
                    _ccd(masters.dark, masters.dark_exptime),
                    data_exposure=header_exptime(header) * u.s,
                    dark_exposure=masters.dark_exptime * u.s,
                    scale=True,
                )
            return np.asarray(ccd.data, dtype=np.float32)

        masters.flat = normalize_flat(median_stack(flat_paths, binning, log, prep_flat))
        masters.notes.append(f"flat n={len(flat_paths)}" + (" (already calibrated)" if flats_calibrated else ""))
    return masters


# ---- 2.2.8: SHOBS-P's own calibration masters, saved beside their raw frames and reused ---------------------

MASTER_PREFIX = "SHOBS-P_master_"
# 2.2.13 (John): masters live in a subfolder of the calibration folder, not beside the raw frames. Other programs
# that take "every FITS in the folder" (TransitLab debayered SHOBS-P_master_bias_bin2.fits with the raw bias frames
# on 13 Oct, and EXOTIC crashed on the half-size master) then never see them.
MASTER_DIR = "SHOBS-P masters"
MASTER_KINDS = ("bias", "dark", "flat")


def master_file_name(kind: str, binning: int, exptime: float | None = None) -> str:
    """SHOBS-P_master_flat_bin2.fits, SHOBS-P_master_dark_bin2_0.75s.fits, SHOBS-P_master_bias_bin2.fits."""
    if kind == "dark":
        return f"{MASTER_PREFIX}dark_bin{int(binning)}_{float(exptime or 0):g}s.fits"
    return f"{MASTER_PREFIX}{kind}_bin{int(binning)}.fits"


def master_path(paths: list[str], kind: str, binning: int, exptime: float | None = None) -> str:
    """Where the master for these raw frames is saved: <their folder>/SHOBS-P masters/<master_file_name>."""
    return os.path.join(os.path.dirname(paths[0]), MASTER_DIR, master_file_name(kind, binning, exptime))


def is_shobsp_master_name(path: str) -> bool:
    return os.path.basename(path).lower().startswith(MASTER_PREFIX.lower())


def calibration_source(path: str) -> tuple[list[str], str | None]:
    """A Bias/Dark/Flat entry is a folder of raw frames (SHOBS-P masters in it are never stacked as frames) or one
    master file picked directly. Returns (raw frames, master file or None)."""
    p = (path or "").strip()
    if not p:
        return [], None
    if os.path.isfile(p):
        return [], p
    return [f for f in list_fits(p) if not is_shobsp_master_name(f)], None


def frames_fingerprint(paths: list[str]) -> str:
    """Names, sizes, modification times and count of the raw frames: a saved master is reused only while this is
    unchanged."""
    import hashlib

    h = hashlib.sha1()
    rows = []
    for path in paths:
        try:
            st = os.stat(path)
            rows.append(f"{os.path.basename(path)}|{st.st_size}|{int(st.st_mtime)}")
        except OSError:
            rows.append(f"{os.path.basename(path)}|?|?")
    rows.sort()
    h.update(f"{len(rows)}\n".encode())
    for row in rows:
        h.update(row.encode("utf-8", "replace") + b"\n")
    return h.hexdigest()[:20]


_COPY_KEYS = ("GAIN", "OFFSET", "EGAIN", "CCD-TEMP", "SET-TEMP", "BAYERPAT", "INSTRUME", "XPIXSZ", "YPIXSZ")


def _master_cards(kind: str, binning: int, paths: list[str], headers: list[dict], version: str, extra: dict) -> dict:
    first = headers[0] if headers else {}
    cards = {"SHOBSP": (str(version), "built by SHOBS-P (Shiloh Hill Observatory - Photometry)"),
             "MASTTYPE": (kind.upper(), "SHOBS-P master type"),
             "IMAGETYP": (f"Master {kind.title()}", ""),
             "SHBIN": (int(binning), "binning applied when stacking"),
             "NFRAMES": (len(paths), "raw frames median-combined"),
             "FPRINT": (frames_fingerprint(paths), "fingerprint of the raw frames (names, sizes, times)"),
             "SRCDIR": (os.path.basename(os.path.dirname(paths[0]))[:60] if paths else "", "folder of the raw frames")}
    dates = sorted(str(h.get("DATE-OBS")) for h in headers if h.get("DATE-OBS"))
    if dates:
        cards["DATE-BEG"] = (dates[0][:30], "first raw frame")
        cards["DATE-END"] = (dates[-1][:30], "last raw frame")
    for key in _COPY_KEYS:
        if first.get(key) not in (None, ""):
            cards[key] = (first[key], "")
    rot = header_rotator(first) if first else None
    if kind == "flat" and rot is not None:
        cards["ROTATOR"] = (float(rot), "camera angle of the flats")
    for key, value in extra.items():
        cards[key] = value
    return cards


def write_master(path: str, data: np.ndarray, cards: dict) -> None:
    """Write a master as 32-bit float FITS with its SHOBS-P stamp."""
    if fits is None:
        raise RuntimeError("astropy is required to save masters")
    hdr = fits.Header()
    for key, value in cards.items():
        val, comment = value if isinstance(value, tuple) else (value, "")
        if isinstance(val, str):
            val = ascii_safe(val)[:68]
        try:
            hdr[key] = (val, ascii_safe(str(comment))[:46])
        except Exception:
            continue
    tmp = path + ".part"
    try:
        fits.PrimaryHDU(np.asarray(data, dtype=np.float32), header=hdr).writeto(tmp, overwrite=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def master_stamp(header: dict) -> str | None:
    """The master type (BIAS, DARK, FLAT) when the file carries SHOBS-P's stamp, else None."""
    if not header or not str(header.get("SHOBSP") or "").strip():
        return None
    kind = str(header.get("MASTTYPE") or "").strip().upper()
    return kind if kind in ("BIAS", "DARK", "FLAT") else None


def check_master_file(path: str, kind: str) -> str:
    """'' when path is a SHOBS-P master of this kind; otherwise a plain reason it cannot be used."""
    try:
        header = read_header(path)
    except Exception as exc:
        return f"{os.path.basename(path)} cannot be read ({exc})."
    stamp = master_stamp(header)
    if stamp is None:
        return (f"{os.path.basename(path)} is not a master SHOBS-P built. Masters from PixInsight or other software "
                "are not used, because how they were scaled and normalized is not known. Choose the folder of raw "
                f"{kind} frames instead (SHOBS-P builds its master there and reuses it).")
    if stamp != kind.upper():
        return f"{os.path.basename(path)} is a master {stamp.lower()}, not a master {kind}."
    return ""


def find_saved_master(paths: list[str], kind: str, binning: int, depends: dict, exptime: float | None = None):
    """The saved SHOBS-P master for these raw frames, when it exists and still matches: same frames (fingerprint
    and count), same binning, and built from the same bias/dark it depends on. Returns (path, header) or
    (None, reason)."""
    if not paths:
        return None, "no frames"
    path = master_path(paths, kind, binning, exptime)
    if not os.path.isfile(path):
        # 2.2.13: a master saved the old way (beside the frames, 2.2.8-2.2.12) is still found, and moved into the
        # subfolder on this first reuse; when the folder is read-only it is used where it is.
        old = os.path.join(os.path.dirname(paths[0]), master_file_name(kind, binning, exptime))
        if not os.path.isfile(old):
            return None, "none saved yet"
        path = old
        try:
            header = read_header(old)
            if master_stamp(header) == kind.upper():
                new = master_path(paths, kind, binning, exptime)
                os.makedirs(os.path.dirname(new), exist_ok=True)
                os.replace(old, new)
                path = new
        except Exception:
            path = old
    try:
        header = read_header(path)
    except Exception as exc:
        return None, f"saved master unreadable ({exc})"
    if master_stamp(header) != kind.upper():
        return None, "saved file is not a SHOBS-P master"
    try:
        if int(header.get("SHBIN", -1)) != int(binning):
            return None, "built with another binning"
        if int(header.get("NFRAMES", -1)) != len(paths):
            return None, "the number of raw frames changed"
    except (TypeError, ValueError):
        return None, "stamp incomplete"
    if str(header.get("FPRINT", "")) != frames_fingerprint(paths):
        return None, "the raw frames changed"
    for key, value in depends.items():
        if str(header.get(key, "")) != str(value):
            return None, f"built with a different {key[:-2].lower() or key} set"
    return path, header


def master_mismatches(master_header: dict, kind: str, light_header: dict, light_shape: tuple, master_shape: tuple,
                      binning: int, mount: str | None = None,
                      derotator: bool = False) -> tuple[list[str], list[str]]:
    """(errors that make the master unusable, warnings) comparing a master with the lights."""
    errors, warns = [], []
    if tuple(master_shape) != tuple(light_shape):
        errors.append(f"master {kind} is {master_shape[1]}x{master_shape[0]} px but the binned lights are "
                      f"{light_shape[1]}x{light_shape[0]} px")
    try:
        mb = int(master_header.get("SHBIN", binning))
        if mb != int(binning):
            errors.append(f"master {kind} was built at binning {mb}, the lights use binning {binning}")
    except (TypeError, ValueError):
        pass
    mp, lp = header_bayer(master_header), header_bayer(light_header)
    if mp and lp and mp != lp:
        warns.append(f"master {kind} Bayer pattern {mp}, lights {lp}")
    for key, label, tol in (("GAIN", "gain", 0.5), ("OFFSET", "offset", 0.5), ("CCD-TEMP", "sensor temperature", 3.0)):
        try:
            a, b = float(master_header.get(key)), float(light_header.get(key))
        except (TypeError, ValueError):
            continue
        if abs(a - b) > tol:
            warns.append(f"master {kind} {label} {a:g}, lights {b:g}")
    if kind == "flat":
        fr, lr = master_header.get("ROTATOR"), header_rotator(light_header)
        try:
            # 2.2.18 (item 8): alt-az: the derotator's position angle, compared without the flip (flat_angle_difference)
            if (fr is not None and lr is not None and not derotator   # derotator: see flat_angle_difference
                    and flat_angle_difference(float(fr), float(lr), mount) > flat_angle_tolerance(mount)):
                warns.append(f"master flat taken at rotator {float(fr):g}°, lights at {float(lr):g}°")
        except (TypeError, ValueError):
            pass
        d0 = str(master_header.get("DATE-END") or master_header.get("DATE-BEG") or "")[:10]
        dl = str(light_header.get("DATE-OBS") or "")[:10]
        try:
            gap = abs((datetime.fromisoformat(dl) - datetime.fromisoformat(d0)).days)
            if gap > 30:
                warns.append(f"master flat is from {d0}, {gap} days before or after the lights (dust moves)")
        except ValueError:
            pass
    return errors, warns


def master_summary(header: dict) -> str:
    """'from 1–2 Oct (51 frames)' for the log."""
    d0, d1 = str(header.get("DATE-BEG") or "")[:10], str(header.get("DATE-END") or "")[:10]
    n = header.get("NFRAMES")
    when = ""
    try:
        a, b = datetime.fromisoformat(d0), datetime.fromisoformat(d1)
        when = (f"{a.day}–{b.day} {b.strftime('%b')}" if (a.year, a.month) == (b.year, b.month) and a.day != b.day
                else a.strftime("%d %b").lstrip("0") if a.date() == b.date()
                else f"{a.strftime('%d %b').lstrip('0')}–{b.strftime('%d %b').lstrip('0')}")
    except ValueError:
        pass
    parts = [f"from {when}" if when else "", f"({n} frames)" if n else ""]
    return " ".join(p for p in parts if p)


def prepare_masters(bias_paths: list[str], dark_paths: list[str], flat_paths: list[str], binning: int = 1,
                    log=None, flats_calibrated: bool = False, files: dict | None = None, save: bool = True,
                    rebuild: bool = False, version: str = "") -> Masters:
    """2.2.8: build_masters, but each master is saved into the folder its raw frames came from and reused on later
    runs while the raw frames, binning, and the bias/dark it was built with are unchanged. files: {"bias": path,
    "dark": path, "flat": path} for SHOBS-P masters picked directly. Masters.used describes each master's source;
    Masters.headers keeps each master's header for the checks against the lights."""
    log = log or (lambda *_: None)
    files = files or {}
    masters = Masters()
    masters.used = {}
    masters.headers = {}

    def load(kind, path, header, how):
        data, _h = read_fits(path)
        masters.headers[kind] = header
        summary = master_summary(header)
        masters.used[kind] = f"{how} SHOBS-P master {kind}" + (f" {summary}" if summary else "")
        log(f"Using {how} master {kind} {summary}".rstrip() + f": {os.path.basename(path)}")
        return np.asarray(data, dtype=np.float32)

    def save_it(kind, data, paths, headers, extra, exptime=None):
        if not save or not paths:
            return
        path = master_path(paths, kind, binning, exptime)
        folder = os.path.dirname(path)
        try:
            os.makedirs(folder, exist_ok=True)
            write_master(path, data, _master_cards(kind, binning, paths, headers, version, extra))
            masters.headers[kind] = read_header(path)
            log(f"Saved master {kind} in the {kind} folder's '{MASTER_DIR}' subfolder: {os.path.basename(path)} "
                "(reused next time while these frames are unchanged)")
        except Exception as exc:
            log(f"Could not save the master {kind} in {folder} ({exc}); continuing without saving it.")

    def stack(paths, prep=None):
        headers = []

        def wrapped(data, header):
            headers.append(header)
            return prep(data, header) if prep is not None else data
        return median_stack(paths, binning, log, wrapped), headers

    # Bias
    bias_fp = "none"
    if files.get("bias"):
        h = read_header(files["bias"])
        masters.bias = load("bias", files["bias"], h, "chosen")
        bias_fp = str(h.get("FPRINT", "file"))
        masters.notes.append("bias: chosen master")
    elif bias_paths:
        bias_fp = frames_fingerprint(bias_paths)
        found, hdr = (None, "rebuild asked") if rebuild else find_saved_master(bias_paths, "bias", binning, {})
        if found:
            masters.bias = load("bias", found, hdr, "saved")
        else:
            log(f"Median-combining {len(bias_paths)} bias frames")
            masters.bias, hs = stack(bias_paths)
            masters.used["bias"] = f"bias built from {len(bias_paths)} frames"
            save_it("bias", masters.bias, bias_paths, hs, {})
        masters.notes.append(f"bias n={len(bias_paths)} " + ("saved master" if found else "batched median"))

    # Dark
    dark_fp = "none"

    def prep_dark(data, header):
        ccd = _ccd(data, header_exptime(header))
        if masters.bias is not None:
            ccd = subtract_bias(ccd, _ccd(masters.bias))
        return np.asarray(ccd.data, dtype=np.float32)

    if files.get("dark"):
        h = read_header(files["dark"])
        masters.dark = load("dark", files["dark"], h, "chosen")
        masters.dark_exptime = header_exptime(h)
        dark_fp = str(h.get("FPRINT", "file"))
        bias_out = str(h.get("BIASSUB", "")).upper() in ("T", "TRUE", "1")
        if masters.bias is None and bias_out:
            log("  Note: this master dark had the bias taken out, and no bias is given now: the pedestal stays in the "
                "lights (the sky ring still removes it in photometry).")
        elif masters.bias is not None and not bias_out:
            # The lights and flats lose the bias first; a dark that still holds it would take the pedestal off twice.
            if masters.bias.shape == masters.dark.shape:
                masters.dark = (masters.dark - masters.bias).astype(np.float32)
                log("  This master dark still held the bias; the bias given now was taken out of it, so the pedestal "
                    "is removed once.")
            else:
                raise ValueError("The chosen master dark still holds the bias, and the bias given is another size; "
                                 "clear one of them.")
        masters.notes.append(f"dark: chosen master exp={masters.dark_exptime:.3g}s")
    elif dark_paths:
        exps = [header_exptime(read_header(path)) for path in dark_paths]
        exptime = float(np.median(exps)) if exps else 1.0
        dark_fp = frames_fingerprint(dark_paths)
        deps = {"BIASFP": bias_fp}
        found, hdr = (None, "rebuild asked") if rebuild else find_saved_master(dark_paths, "dark", binning, deps, exptime)
        if found:
            masters.dark = load("dark", found, hdr, "saved")
        else:
            log(f"Median-combining {len(dark_paths)} dark frames")
            masters.dark, hs = stack(dark_paths, prep_dark)
            masters.used["dark"] = f"dark built from {len(dark_paths)} frames"
            save_it("dark", masters.dark, dark_paths, hs,
                    {"EXPTIME": (exptime, "dark exposure, s"), "BIASSUB": (masters.bias is not None, "bias removed"),
                     "BIASFP": (bias_fp, "bias set used")}, exptime)
        masters.dark_exptime = exptime
        masters.notes.append(f"dark n={len(dark_paths)} exp={masters.dark_exptime:.3g}s" + (" saved master" if found else ""))

    # Flat
    def prep_flat(data, header):
        if flats_calibrated:
            return np.asarray(data, dtype=np.float32)
        ccd = _ccd(data, header_exptime(header))
        if masters.bias is not None:
            ccd = subtract_bias(ccd, _ccd(masters.bias))
        if masters.dark is not None and masters.dark_exptime > 0:
            ccd = subtract_dark(ccd, _ccd(masters.dark, masters.dark_exptime),
                                data_exposure=header_exptime(header) * u.s,
                                dark_exposure=masters.dark_exptime * u.s, scale=True)
        return np.asarray(ccd.data, dtype=np.float32)

    if files.get("flat"):
        h = read_header(files["flat"])
        masters.flat = load("flat", files["flat"], h, "chosen")
        masters.notes.append("flat: chosen master")
    elif flat_paths:
        deps = {"BIASFP": bias_fp, "DARKFP": dark_fp, "FLATCAL": bool(flats_calibrated)}
        found, hdr = (None, "rebuild asked") if rebuild else find_saved_master(flat_paths, "flat", binning, deps)
        if found:
            masters.flat = load("flat", found, hdr, "saved")
        else:
            log(f"Median-combining {len(flat_paths)} flat frames")
            raw, hs = stack(flat_paths, prep_flat)
            masters.flat = normalize_flat(raw)
            masters.used["flat"] = f"flat built from {len(flat_paths)} frames"
            save_it("flat", masters.flat, flat_paths, hs,
                    {"NORMFLAT": (True, "normalized to 1 at the median"), "BIASFP": (bias_fp, "bias set used"),
                     "DARKFP": (dark_fp, "dark set used"), "FLATCAL": (bool(flats_calibrated), "flats were pre-calibrated")})
        masters.notes.append(f"flat n={len(flat_paths)}" + (" (already calibrated)" if flats_calibrated else "")
                             + (" saved master" if found else ""))
    return masters


def calibrate_frame(data: np.ndarray, header: dict, masters: Masters) -> np.ndarray:
    ccd = _ccd(data, header_exptime(header))
    if masters.bias is not None:
        ccd = subtract_bias(ccd, _ccd(masters.bias))
    if masters.dark is not None and masters.dark_exptime > 0:
        ccd = subtract_dark(
            ccd,
            _ccd(masters.dark, masters.dark_exptime),
            data_exposure=header_exptime(header) * u.s,
            dark_exposure=masters.dark_exptime * u.s,
            scale=True,
        )
    if masters.flat is not None:
        # The master flat is already normalized to 1 and carries NaN on dead or deeply
        # vignetted pixels. norm_value=1 stops ccdproc taking the mean of the flat, which
        # would be NaN and blank the whole frame. photutils masks the NaN pixels.
        ccd = flat_correct(ccd, _ccd(masters.flat), norm_value=1.0)
    return np.asarray(ccd.data, dtype=np.float32)


def debayer(img: np.ndarray, pattern: str, mode: str = "luminance") -> np.ndarray:
    """Superpixel debayer. mode is 'luminance' (clear / CV) or 'green'."""
    pattern = (pattern or "RGGB").upper()
    if pattern == MONO:
        return np.asarray(img, dtype=np.float32)
    positions = {
        "RGGB": {(0, 0): "R", (0, 1): "G", (1, 0): "G", (1, 1): "B"},
        "BGGR": {(0, 0): "B", (0, 1): "G", (1, 0): "G", (1, 1): "R"},
        "GRBG": {(0, 0): "G", (0, 1): "R", (1, 0): "B", (1, 1): "G"},
        "GBRG": {(0, 0): "G", (0, 1): "B", (1, 0): "R", (1, 1): "G"},
    }
    if pattern not in positions:
        raise ValueError(f"Unsupported Bayer pattern {pattern}")
    h = img.shape[0] // 2 * 2
    w = img.shape[1] // 2 * 2
    img = img[:h, :w]
    samples: dict[str, list[np.ndarray]] = {"R": [], "G": [], "B": []}
    for (iy, ix), color in positions[pattern].items():
        samples[color].append(img[iy::2, ix::2])
    red = samples["R"][0]
    blue = samples["B"][0]
    green = samples["G"][0] if len(samples["G"]) == 1 else (samples["G"][0] + samples["G"][1]) * 0.5
    if mode == "green":
        return green.astype(np.float32)
    if mode == "red":
        return red.astype(np.float32)
    if mode == "blue":
        return blue.astype(np.float32)
    # Rec. 601 luminance, used as the clear measurement on an OSC frame.
    return (0.299 * red + 0.587 * green + 0.114 * blue).astype(np.float32)


def filter_for_mode(mode: str) -> str:
    return {"luminance": "CV", "green": "TG", "red": "TR", "blue": "TB"}.get(mode, "CV")


# A monochrome camera's filter wheel, from the FITS FILTER keyword: (AAVSO filter code, catalog band for comps).
_FILTER_NAMES = {
    # Photometric filters, named unambiguously.
    "v": ("V", "V"), "johnsonv": ("V", "V"), "besselv": ("V", "V"), "vbessel": ("V", "V"), "jv": ("V", "V"),
    "vjohnson": ("V", "V"), "visual": ("V", "V"),
    "johnsonb": ("B", "B"), "besselb": ("B", "B"), "bbessel": ("B", "B"), "jb": ("B", "B"), "bjohnson": ("B", "B"),
    "rc": ("R", "R"), "cousinsr": ("R", "R"), "besselr": ("R", "R"), "rbessel": ("R", "R"), "rcousins": ("R", "R"),
    # Imaging (LRGB) filters: a bare R, G, or B on a filter wheel is almost always an imaging filter, reported like
    # a color camera's channels (TR, TG, TB).
    "r": ("TR", "R"), "red": ("TR", "R"), "g": ("TG", "V"), "green": ("TG", "V"), "b": ("TB", "B"), "blue": ("TB", "B"),
    # Clear / luminance.
    "clear": ("CV", "V"), "cv": ("CV", "V"), "l": ("CV", "V"), "lum": ("CV", "V"), "luminance": ("CV", "V"),
    "none": ("CV", "V"), "c": ("CV", "V"), "open": ("CV", "V"), "empty": ("CV", "V"),
}
# I and U are not mapped: the comp catalogs here carry no I or U magnitudes for most stars.
# 2.2.17 (item 1): Sloan g' r' i' (z', u') are recognized by sloan_band_code below (GitHub #6).

# 2.2.17 (item 1): Sloan bands. The AAVSO filter code (SG, SR, SI, SZ, SU) is also the catalog band key in a star's
# "mags", so a Sloan run asks the catalogs for that band and zero-points on it. Labels for the screen and the log.
SLOAN_BANDS = ("SG", "SR", "SI", "SZ", "SU")
BAND_LABELS = {"SG": "g\u2032", "SR": "r\u2032", "SI": "i\u2032", "SZ": "z\u2032", "SU": "u\u2032"}
_PRIME_CHARS = "\u2032\u2019\u00b4`\u02b9\""


def is_sloan(band) -> bool:
    """2.2.17 (item 1): True for a Sloan band key / AAVSO code (SG, SR, SI, SZ, SU)."""
    return str(band or "").strip().upper() in SLOAN_BANDS


def filter_band(code) -> str:
    """2.2.17 (item 1, review): the catalog band an AAVSO filter code is reduced in: SG/SR/SI/SZ/SU themselves, B for
    B/TB, R for R/TR, V for everything else (V, CV, TG…), as filter_for_mode / _FILTER_NAMES pair them."""
    c = str(code or "").strip().upper()
    if c in SLOAN_BANDS:
        return c
    return {"B": "B", "TB": "B", "R": "R", "TR": "R"}.get(c, "V")


def band_label(band) -> str:
    """2.2.17 (item 1): a band as people write it: "i\u2032" for SI, "g\u2032" for SG; V, B, R unchanged."""
    text = str(band or "")
    return BAND_LABELS.get(text.strip().upper(), text)


def sloan_band_code(value) -> str | None:
    """2.2.17 (item 1): the AAVSO Sloan code (SG, SR, SI, SZ, SU) for a FITS FILTER value, or None.
    Accepted, case-, space-, underscore- and hyphen-insensitive: g' (any prime or apostrophe), gp, g_prime,
    sdss g, Sloan g, sloan_g', g-sdss, SG; the same for r, i, z, u. Review S5: brand words and brackets are allowed
    around ONE band letter when the value says Sloan/SDSS anywhere or primes the letter ("g' (Sloan)", "r (sdss)",
    "Baader Sloan r'", "Astrodon Sloan g'"). A Sloan filter must SAY it is Sloan (a prime, "p"/"prime",
    "sdss"/"sloan", or the AAVSO code SG/SR/SI/SZ/SU): a bare G or R (either case) stays the imaging filter it
    always was (TG / TR, see _FILTER_NAMES), and a bare I, Z or U is not recognized (Johnson-Cousins I is not
    calibrated here), so Johnson/Cousins R and I are never taken for Sloan r and i."""
    text = str(value or "").strip().lower()
    for ch in _PRIME_CHARS:
        text = text.replace(ch, "'")
    compact = _re.sub(r"[\s_\-.()\[\]{},/]+", "", text).replace("filter", "")
    m = _re.fullmatch(r"(sloan|sdss)?([ugriz])('|p|prime)?(sloan|sdss)?", compact)
    if m and (m.group(1) or m.group(3) or m.group(4)):
        return "S" + m.group(2).upper()
    m = _re.fullmatch(r"s([ugriz])", compact)
    if m:
        return "S" + m.group(1).upper()
    # Words: "sloan"/"sdss" split off wherever they stand, brackets and separators are spaces.
    spaced = _re.sub(r"(sloan|sdss)", r" \1 ", text)
    words = [w for w in _re.split(r"[\s_\-.()\[\]{},/]+", spaced) if w and w != "filter"]
    says_sloan = any(w in ("sloan", "sdss") for w in words)
    letters = [_re.fullmatch(r"([ugriz])('|p|prime)?", w) for w in words]
    letters = [lm for lm in letters if lm]
    if len(letters) == 1 and (says_sloan or letters[0].group(2)):
        return "S" + letters[0].group(1).upper()
    return None

# 2.2.11: the filters a monochrome camera can be set to by hand (Calibrate page) when the header does not say:
# label -> (AAVSO filter code, comp band). Only bands the comp catalogs carry (B, V, R; 2.2.17: Sloan g' r' i' z' u').
MONO_FILTER_CHOICES = {
    "V (Johnson V)": ("V", "V"),
    "B (Johnson B)": ("B", "B"),
    "R (Cousins R)": ("R", "R"),
    "Clear / L (CV)": ("CV", "V"),
    "Green imaging (TG)": ("TG", "V"),
    "Red imaging (TR)": ("TR", "R"),
    "Blue imaging (TB)": ("TB", "B"),
    # 2.2.17 (item 1): Sloan filters; comps in the same Sloan band (AAVSO sequence, APASS DR9, Gaia DR3).
    "Sloan g\u2032 (SG)": ("SG", "SG"),
    "Sloan r\u2032 (SR)": ("SR", "SR"),
    "Sloan i\u2032 (SI)": ("SI", "SI"),
    "Sloan z\u2032 (SZ)": ("SZ", "SZ"),
    "Sloan u\u2032 (SU)": ("SU", "SU"),   # comps only from Gaia's synthetic SDSS u (GSPC): few stars
}


def header_filter_value(header: dict) -> str:
    """The raw FILTER / FILTNAME / FILTER1 / FILT value of a frame, or '' when it has none."""
    for key in ("FILTER", "FILTNAME", "FILTER1", "FILT"):
        value = (header or {}).get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def mono_filter_label(found) -> str:
    """The Filter-box label for a (code, band) pair, or ''."""
    for label, pair in MONO_FILTER_CHOICES.items():
        if found and tuple(found) == pair:
            return label
    return ""


def cfa_contrast(img: np.ndarray):
    """2.2.11: how different the four 2x2 sub-planes of a raw frame are, from the central half of the frame:
    (max/min - 1 of the four plane medians, mean level). A color (Bayer) sensor's flat has red, green and blue
    pixels at clearly different levels (often 20-60%); a monochrome sensor's planes agree to well under 1%."""
    a = np.asarray(img)
    if a.ndim != 2 or min(a.shape) < 16:
        return None, None
    h, w = a.shape
    y0, x0 = (h // 4) // 2 * 2, (w // 4) // 2 * 2
    c = a[y0:y0 + (h // 2) // 2 * 2, x0:x0 + (w // 2) // 2 * 2].astype(np.float64)   # crop first: frames are big
    meds = [float(np.nanmedian(c[i::2, j::2])) for i in (0, 1) for j in (0, 1)]
    lo, hi = min(meds), max(meds)
    if not np.isfinite(lo) or lo <= 0:
        return None, None
    return hi / lo - 1.0, float(np.mean(meds))


def detect_sensor_from_flat(path: str):
    """'mono', 'cfa', or None (cannot tell) from one raw flat frame, with the contrast found. A flat bright enough
    (mean > 1000 ADU) with planes within 1% is monochrome; planes 4% or more apart are a color mask. A color image
    (three channels, already debayered) or a calibrated/normalized frame cannot be judged and gives None."""
    try:
        data, header = read_fits(path)
    except Exception:
        return None, None
    header = header or {}
    if header_bayer(header):
        return "cfa", None
    try:
        if int(header.get("NAXIS", 2)) >= 3 and int(header.get("NAXIS3", 1)) > 1:
            return None, None
    except (TypeError, ValueError):
        pass
    if data is None or np.asarray(data).ndim != 2:
        return None, None
    contrast, level = cfa_contrast(data)
    if contrast is None or level is None or level < 1000:
        return None, contrast
    if contrast < 0.01:
        return "mono", contrast
    if contrast >= 0.04:
        return "cfa", contrast
    return None, contrast


SENSOR_SKY_FLOOR_RAW = 1000.0   # 2.2.18 (review M1): a raw light with no known pedestal: as for the flat test
SENSOR_SKY_FLOOR = 20.0         # 2.2.18 (review M1): the sky above a known pedestal (or zero, pre-calibrated)


def detect_sensor_from_light(path: str, pedestal: float | None = None):
    """2.2.18 (item 2): 'mono', 'cfa', or None (cannot tell) from one light's sky background, with the contrast
    found, for runs without raw flats (pre-calibrated lights, empty calibration folders). The four 2x2 pixel planes
    of the central half are compared on the sky only: stars (over 3 sigma of their plane, and their neighbors) are
    masked. A color mask leaves the planes apart by the sky's color (planes 3% or more apart: 'cfa'); a monochrome
    sensor's planes agree (under 0.5%, measured to better than 0.1%: 'mono').
    Review M1: a bias pedestal dilutes the contrast (a faint sky over 500 ADU of bias looked monochrome), so the
    planes are compared above `pedestal` (the bias level the app measured in the Bias or Dark folder), else above the
    header's PEDESTAL or zero for a pre-calibrated light. A raw light with no known pedestal is called 'mono' only
    when its sky is at least SENSOR_SKY_FLOOR_RAW (1000 ADU, the flat test's rule); otherwise None (the app asks).
    A color image (three channels) gives None."""
    try:
        data, header = read_fits(path)
    except Exception:
        return None, None
    header = header or {}
    if is_rgb_header(header):   # review S7: before the Bayer keyword (a debayered image can keep BAYERPAT)
        return None, None
    if header_bayer(header):
        return "cfa", None
    a = np.asarray(data)
    if a.ndim != 2 or min(a.shape) < 64:
        return None, None
    floor = SENSOR_SKY_FLOOR
    if pedestal is None or not math.isfinite(float(pedestal)):
        hp = _header_float(header, "PEDESTAL")
        if hp is not None:
            pedestal = hp
        elif precalibration_signs(path, header):
            pedestal = 0.0
        else:
            pedestal, floor = 0.0, SENSOR_SKY_FLOOR_RAW
    pedestal = float(pedestal)
    h, w = a.shape
    y0, x0 = (h // 4) // 2 * 2, (w // 4) // 2 * 2
    c = a[y0:y0 + (h // 2) // 2 * 2, x0:x0 + (w // 2) // 2 * 2].astype(np.float64)
    planes = [c[i::2, j::2] for i in (0, 1) for j in (0, 1)]
    stats = []
    for p in planes:
        med = float(np.nanmedian(p))
        sig = 1.4826 * float(np.nanmedian(np.abs(p - med)))
        if not (math.isfinite(med) and math.isfinite(sig) and sig > 0):
            return None, None
        stats.append((med, sig))
    stars = np.zeros(planes[0].shape, dtype=bool)
    for p, (med, sig) in zip(planes, stats):
        stars |= ~np.isfinite(p) | (p > med + 3.0 * sig)
    from scipy import ndimage
    stars = ndimage.binary_dilation(stars, iterations=2)
    means, sigs, counts = [], [], []
    for p, (med, sig) in zip(planes, stats):
        v = p[~stars & (np.abs(p - med) < 3.0 * sig)]
        if v.size < 1000:
            return None, None
        means.append(float(np.mean(v)) - pedestal)
        sigs.append(float(np.std(v)))
        counts.append(v.size)
    lo, hi = min(means), max(means)
    if not (math.isfinite(lo) and lo > 0):
        return None, None
    contrast = hi / lo - 1.0
    precision = max(s / math.sqrt(n) for s, n in zip(sigs, counts)) / lo
    if contrast >= 0.03:
        return "cfa", contrast
    if contrast < 0.005 and precision < 0.001 and lo >= floor:
        return "mono", contrast
    return None, contrast


def filter_from_header(header: dict):
    """(AAVSO filter code, comp band) from FITS FILTER / FILTNAME for a monochrome camera, or None if unknown."""
    for key in ("FILTER", "FILTNAME", "FILTER1", "FILT"):
        value = header.get(key)
        if value is None or str(value).strip() == "":
            continue
        sloan = sloan_band_code(value)   # 2.2.17 (item 1): before the table, so "g'" is Sloan, not imaging G
        if sloan:
            return (sloan, sloan)
        text = str(value).strip().lower()
        compact = text.replace(" ", "").replace("-", "").replace("_", "")
        for cand in (text, compact, compact.replace("filter", "")):
            if cand in _FILTER_NAMES:
                return _FILTER_NAMES[cand]
        return None
    return None


def _crop(img: np.ndarray, x: float, y: float, radius: int) -> tuple[np.ndarray, float, float]:
    x0 = int(round(x))
    y0 = int(round(y))
    r = int(radius)
    y1, y2 = max(0, y0 - r), min(img.shape[0], y0 + r + 1)
    x1, x2 = max(0, x0 - r), min(img.shape[1], x0 + r + 1)
    return img[y1:y2, x1:x2], x0 - x1, y0 - y1


def measure_aperture(
    img: np.ndarray,
    x: float,
    y: float,
    radius: float,
    sky_inner: float,
    sky_outer: float,
) -> dict:
    """photutils circular aperture with a 3-sigma-clipped median sky annulus."""
    if not (0 < radius < sky_inner < sky_outer):
        raise ValueError("Need 0 < aperture radius < sky inner < sky outer")
    aperture = CircularAperture((x, y), r=radius)
    annulus = CircularAnnulus((x, y), r_in=sky_inner, r_out=sky_outer)
    bad = ~np.isfinite(img)
    mask = bad if bad.any() else None
    sky_stats = ApertureStats(img, annulus, mask=mask, sigma_clip=SigmaClip(sigma=3.0, maxiters=10))
    if not np.isfinite(sky_stats.median):
        raise ValueError("Aperture or sky annulus falls outside the frame")
    phot = aperture_photometry(img, aperture, mask=mask)
    area = float(aperture.area)
    sky_med = float(sky_stats.median)
    flux = float(phot["aperture_sum"][0]) - sky_med * area
    sky_std = float(sky_stats.std) if np.isfinite(sky_stats.std) else 0.0
    variance = max(flux, 0.0) + area * sky_std ** 2
    return {
        "flux": flux,
        "sky": sky_med,
        "sky_std": sky_std,
        "n": int(round(area)),
        "variance": variance,
        "x": float(x),
        "y": float(y),
    }


def inst_mag(flux: float, exptime: float) -> float:
    if flux <= 0 or exptime <= 0:
        return float("nan")
    return -2.5 * math.log10(flux / exptime)


def airmass(ra_hours: float, dec_deg: float, lat_deg: float, lon_deg: float, jd: float) -> float:
    """Astropy AltAz secant z. East longitude positive. Site height is 300 m."""
    loc = EarthLocation(lat=lat_deg * u.deg, lon=lon_deg * u.deg, height=300 * u.m)
    target = SkyCoord(ra=ra_hours * u.hourangle, dec=dec_deg * u.deg)
    frame = AltAz(obstime=Time(jd, format="jd", scale="utc"), location=loc)
    secz = target.transform_to(frame).secz
    value = float(secz)
    if not np.isfinite(value) or value <= 0 or value > 8:
        return float("nan")
    return value


@dataclass
class Star:
    name: str
    x: float
    y: float
    catalog_mag: float | None = None


@dataclass
class Obs:
    jd: float
    mag: float
    merr: float
    cmag: float
    kmag: float | None
    airmass: float | None
    path: str
    exptime: float
    flux_var: float
    flux_comp: float
    night: str = ""
    filt: str = "CV"
    fwhm: float = float("nan")
    elong: float = float("nan")
    peak: float = float("nan")
    sky: float = float("nan")
    shift: float = 0.0
    saturated: bool = False
    drifted: bool = False
    check_std: float | None = None
    flag: str = ""
    segment: str = ""   # "flip" for frames after a meridian flip
    raw_peak: float = float("nan")
    # Each comparison star's instrumental magnitude, in the order the comps were given.
    comp_insts: list = field(default_factory=list)
    # Optional color (Photometry page "Also measure R and B"). *_diff are differential
    # instrumental colors (target minus comp ensemble); bv / vr add the comps' catalog colors.
    bv: float = float("nan")
    vr: float = float("nan")
    bv_diff: float = float("nan")
    vr_diff: float = float("nan")
    # How bv / vr were put on the catalog scale: "field" (fit to labeled field stars that
    # night), "saved" (slope from an earlier field fit), or "" (comp colors only, slope 1).
    color_cal: str = ""
    # Base values for re-computing the magnitude with a different set of comps (1.8+):
    # the target's instrumental magnitude and the catalog mean of the comps in use.
    target_inst: float = float("nan")
    comp_cat: float = float("nan")
    # Spare comparison stars measured every frame: {name: instrumental magnitude}.
    spare_insts: dict = field(default_factory=dict)
    # 2.2.3: photon-noise pieces of merr, so the error can be re-derived when the comps change (Use comps):
    # the target's magnitude variance and each original comp's. NaN / empty in series saved before 2.2.3.
    target_var: float = float("nan")
    comp_vars: list = field(default_factory=list)
    # 2.2.3: how many comparison stars were averaged for this point (0 = not recorded; use the series count).
    n_comps: int = 0
    # Watch stars (suspected variables and the like) measured every frame: {name: instrumental magnitude}.
    watch_insts: dict = field(default_factory=dict)
    # Pixel positions used in this frame: {"t": [x, y], "c": [[x, y], ...], "k": [x, y]}.
    pos: dict = field(default_factory=dict)
    # 2.2.14 (item 18, review): extra error (mag) for a kept transmission-loss point: the star-to-star scatter measured
    # in its stretch beyond photon noise. Set by apply_cloud_flags each time; not saved with the series.
    haze_err: float = 0.0
    # 2.2.17 (item 8, review): which photon-noise model made merr / target_var / comp_vars. 0 = before 2.2.17 (image
    # units taken as electrons, plus a fixed 0.005 mag); 1 = 2.2.17 (electrons via EGAIN / binning / superpixel /
    # flat, no fixed term). Series saved before 2.2.17 read as 0, so a comps change keeps their model.
    e_model: int = 0


def recenter_info(img: np.ndarray, x: float, y: float, radius: float, fwhm: float | None = None,
                  blend_check: bool = True, search: float | None = None, wing: str = "gauss",
                  min_sigma: float | None = None) -> dict:
    """2.2.8: center on the star nearest (x, y), not on whatever is brightest nearby.

    1. Look for significant peaks (3x3-smoothed, above sky + 4 sigma of the smoothed noise) within a search radius
       (max(3, 1.2 x aperture radius), or 2.5 x FWHM when the FWHM is known).
    2. Take the peak NEAREST the start point, so a brighter neighbor a few pixels away cannot capture it
       (CoRoT-1, 10 Oct 2026: a star 2.5x brighter 5.8 px away pulled the old center-of-mass 3.5 px off the target).
    3. Center of mass of the sky-subtracted light inside about one FWHM of that peak, iterated three times.
    With no significant peak (a faint star lost in the noise), the start point is kept and found = False.
    2.2.18 (item 5): blend_check=False never keeps the start point for a blended neighbor, and search widens the
    search radius (both for the pre-run marker check, marker_offset). wing="dip" (comp and check clicks, review): a
    start point on a bright star's own wing is centered on that star unless the light between peak and start point dips
    and rises again. The default "gauss" keeps the 2.2.8 test (target clicks and every frame's centring), which keeps a
    faint target in a brighter neighbor's wing even when it shows only as a shoulder (CoRoT-1 b, 2.2.7).
    Returns {"x", "y", "shift", "found"}."""
    from scipy import ndimage

    wide = search
    if fwhm is not None and math.isfinite(fwhm) and fwhm > 0:
        search = max(3.0, 2.5 * float(fwhm))
        window = max(1.5, float(fwhm))
    else:
        search = max(3.0, 1.2 * float(radius))
        window = min(max(1.5, 0.45 * float(radius)), 5.0)
    if wide is not None and math.isfinite(wide):
        search = max(search, float(wide))
    half = int(math.ceil(search + window)) + 3
    crop, cx, cy = _crop(img, x, y, half)
    keep = {"x": float(x), "y": float(y), "shift": 0.0, "found": False}
    if crop.size < 9:
        return keep
    data = np.array(crop, dtype=float, copy=True)
    finite = np.isfinite(data)
    if finite.sum() < 9:
        return keep
    sky = float(np.median(data[finite]))
    noise = 1.4826 * float(np.median(np.abs(data[finite] - sky)))
    if not math.isfinite(noise) or noise <= 0:
        noise = float(np.std(data[finite])) or 1.0
    data[~finite] = sky
    x_off = x - round(x)
    y_off = y - round(y)
    sx, sy = cx + x_off, cy + y_off  # start point in crop coordinates
    smooth = ndimage.uniform_filter(data, size=3)
    peaks = (smooth == ndimage.maximum_filter(smooth, size=3)) & (smooth > sky + 4.0 * noise / 3.0)
    if min_sigma is not None:
        # 2.2.18 (item 5, review): only peaks this far above the sky (raw-pixel noise) can be the star looked for, so a
        # noise bump on a bright star's wing nearer the start point does not win.
        cand = peaks & (smooth > sky + float(min_sigma) * noise)
    else:
        cand = peaks
    yy, xx = np.mgrid[0 : data.shape[0], 0 : data.shape[1]]
    all_py, all_px = np.nonzero(peaks)
    cand = cand & (np.hypot(xx - sx, yy - sy) <= search)
    py, px = np.nonzero(cand)
    if px.size == 0:
        return keep
    k = int(np.argmin(np.hypot(px - sx, py - sy)))
    ux, uy = float(px[k]), float(py[k])
    work = np.clip(data - sky, 0, None)
    peak_x, peak_y = ux, uy
    # Size the window to the star itself: its half-maximum region (wide for a bright or saturated, flat-topped star),
    # but keep it well short of the next star.
    top = None
    area = 1
    lab, own = None, 0
    try:
        py0, px0 = int(peak_y), int(peak_x)
        level = 0.5 * float(smooth[py0, px0] - sky)
        lab, _n = ndimage.label(smooth - sky > level)
        own = int(lab[py0, px0])
        area = int(np.count_nonzero(lab == own)) if own else 0
        window = max(window, 1.5 * math.sqrt(area / math.pi))
        # A flat (saturated) top is one star with many equal "peaks": move to the middle of that top. Only the
        # top's own connected patch counts, so a brighter neighbor whose half-maximum region touches this star's
        # is never taken for part of it (review, 2.2.8).
        raw_peak = float(data[py0, px0])
        tol = 1e-6 * max(1.0, abs(raw_peak))
        # Pixels EQUAL to the peak (a clipped, saturated top), not merely as bright: a brighter neighbor's core is
        # brighter than this star's peak and must not join it.
        tl, _m = ndimage.label(np.abs(data - raw_peak) <= tol)
        if tl[py0, px0]:
            top = tl == tl[py0, px0]
            if np.count_nonzero(top) > 1:
                peak_x, peak_y = float(xx[top].mean()), float(yy[top].mean())
                ux, uy = peak_x, peak_y
    except Exception:
        top = None
    # A star blended into a brighter neighbor's wing (closer than about 1.5 FWHM) has no peak of its own, so the
    # nearest peak can be the neighbor. When the click holds clearly more light than that peak's own profile
    # would put there, the click is on another star: keep it rather than jump to the neighbor.
    try:
        d_click = math.hypot(peak_x - sx, peak_y - sy)
        if d_click > 1.5:
            p_val = float(smooth[int(round(peak_y)), int(round(peak_x))] - sky)
            r_half = math.sqrt(max(area, 1) / math.pi)
            sig = math.sqrt((r_half / 1.1774) ** 2 + 0.67)
            expect = p_val * math.exp(-d_click ** 2 / (2.0 * sig * sig))
            here = float(smooth[int(round(sy)), int(round(sx))] - sky)
            # 2.2.18 (item 5, review): a real star's wings are wider than a Gaussian (Moffat-like, plus seeing and
            # tracking asymmetry), so a click 1-3 FWHM out on a bright star's own wing passes the test above and the
            # marker stays off the star (HD 219134 6-7 Oct check 9 px off, 15-16 Oct comp 6 px off). For comp and
            # check clicks (wing="dip"): along the line from the peak to the click a wing only falls; a second star
            # makes the light dip and rise again, so the click must hold clearly more light than the lowest point of
            # that profile between peak and click.
            lopsided = True
            if wing == "dip":
                from scipy.ndimage import map_coordinates
                n_pts = max(4, int(round(2 * d_click)))
                ts = np.linspace(0.25, 1.0, n_pts)
                prof = map_coordinates(smooth, [peak_y + ts * (sy - peak_y), peak_x + ts * (sx - peak_x)], order=1,
                                       mode="nearest") - sky
                inner = prof[:-1]
                lopsided = bool(inner.size) and here > float(inner.min()) + 8.0 * noise / 3.0
            if here > 3.0 * expect + 8.0 * noise / 3.0 and here < 0.5 * p_val and lopsided and blend_check:
                return {"x": float(x), "y": float(y), "shift": 0.0, "found": True, "blended": True}
    except Exception:
        pass
    # Other stars: peaks outside this star's half-maximum region, and much brighter peaks inside it (a neighbor
    # whose light reaches this star). Peaks of about the same height inside the region belong to this star (the
    # ring of a defocused "donut", noise on a broad top).
    p_here = float(smooth[int(round(peak_y)), int(round(peak_x))])
    other_pts = []
    for ox, oy in zip(all_px, all_py):
        if (top is not None and top[oy, ox]) or math.hypot(ox - peak_x, oy - peak_y) <= 1.5:
            continue
        same_region = lab is not None and own and lab[oy, ox] == own
        if same_region and float(smooth[oy, ox]) - sky <= 1.5 * (p_here - sky):
            # Same star only when the light stays high all the way between the two peaks (a donut's ring does);
            # two stars have a dip between them (review, 2.2.8).
            lower = min(p_here, float(smooth[oy, ox])) - sky
            try:
                ridge, _r = ndimage.label(smooth - sky >= 0.8 * lower)
                if ridge[int(round(peak_y)), int(round(peak_x))] and \
                        ridge[oy, ox] == ridge[int(round(peak_y)), int(round(peak_x))]:
                    continue
            except Exception:
                continue
        other_pts.append((float(ox), float(oy)))
    others = [math.hypot(ox - peak_x, oy - peak_y) for ox, oy in other_pts]
    # Pixels nearer another star's peak than this one's never count for this star's center.
    mine = np.ones(data.shape, dtype=bool)
    for ox, oy in other_pts:
        mine &= np.hypot(xx - peak_x, yy - peak_y) <= np.hypot(xx - ox, yy - oy)
    others = [d for d in others if d > 1.5]
    if others:
        window = max(1.5, min(window, 0.75 * min(others)))
    for _ in range(6):
        m = (np.hypot(xx - ux, yy - uy) <= window) & mine
        wsum = float(work[m].sum())
        if wsum <= 0:
            break
        nx = float((work[m] * xx[m]).sum() / wsum)
        ny = float((work[m] * yy[m]).sum() / wsum)
        if not (math.isfinite(nx) and math.isfinite(ny)):
            break
        done = math.hypot(nx - ux, ny - uy) < 0.01
        ux, uy = nx, ny
        if done:
            break
    # A center that wandered far from its own peak was pulled by a neighbor's wings: use the peak's 3x3 centroid.
    if others and min(others) < 2.0 * window and math.hypot(ux - peak_x, uy - peak_y) > max(1.0, 0.5 * window):
        m = (np.abs(xx - peak_x) <= 1) & (np.abs(yy - peak_y) <= 1)
        wsum = float(work[m].sum())
        if wsum > 0:
            ux = float((work[m] * xx[m]).sum() / wsum)
            uy = float((work[m] * yy[m]).sum() / wsum)
        else:
            ux, uy = peak_x, peak_y
    x_new = float(x) + (ux - sx)
    y_new = float(y) + (uy - sy)
    # 2.2.18 (item 5, review): how significant the star found is (raw-pixel sigma) and the light at the start point,
    # so a caller can refuse a noise bump on a bright star's wing (the comp/check retry, marker_offset).
    try:
        peak_lvl = float(smooth[int(round(peak_y)), int(round(peak_x))] - sky)
        click_lvl = float(smooth[int(round(min(max(sy, 0), smooth.shape[0] - 1))),
                                 int(round(min(max(sx, 0), smooth.shape[1] - 1)))] - sky)
    except Exception:
        peak_lvl, click_lvl = float("nan"), float("nan")
    return {"x": x_new, "y": y_new, "shift": float(math.hypot(x_new - x, y_new - y)), "found": True,
            "peak_sigma": peak_lvl / noise if noise > 0 else float("nan"), "peak_level": peak_lvl,
            "click_level": click_lvl}


def real_star(info: dict, min_sigma: float = 10.0) -> bool:
    """2.2.18 (item 5, review): a recenter_info result is a real star, not a noise bump on a bright star's wing: its
    smoothed peak at least min_sigma (raw-pixel noise) above the sky, and brighter than the light at the start point."""
    if not info.get("found") or info.get("blended"):
        return False
    sig, pk, at = info.get("peak_sigma"), info.get("peak_level"), info.get("click_level")
    try:
        return float(sig) >= min_sigma and float(pk) >= float(at)
    except (TypeError, ValueError):
        return False


def recenter(img: np.ndarray, x: float, y: float, radius: float, fwhm: float | None = None) -> tuple[float, float, float]:
    """Center on the nearest star (see recenter_info); returns (x, y, shift). Keeps (x, y) when no star is found."""
    r = recenter_info(img, x, y, radius, fwhm)
    return r["x"], r["y"], r["shift"]


def recenter_jump_limit(radius: float) -> float:
    """2.2.18 (item 5): how far reduce_frame lets a frame's centring move a star from its aligned position (further
    and the centring is taken to have found another star, so the star is measured where it was marked)."""
    return max(3.0, 0.5 * float(radius))


def marker_offset(img: np.ndarray, x: float, y: float, radius: float, fwhm: float | None = None,
                  wing: str = "gauss") -> dict:
    """2.2.18 (item 5, review): the check before a run, from what reduce_frame will actually do with this marker.
    True center: the nearest real star (real_star: >= 10 sigma, brighter than the marker spot) within
    max(2.5 FWHM, 1.5 x aperture radius), centered without the "blended neighbor" exception. Measured: reduce_frame's
    own recenter (no seeing given), refused beyond recenter_jump_limit (then the marker position is used). off is True
    when the measured position is more than about 1.5 px from the true center. blended: the click test says the marker
    sits on a faint star in a brighter one's wing (then the nearest star is only reported, not called a mistake);
    wing as in recenter_info ("dip" for comps and checks, as their clicks are tested).
    Returns {"found", "x", "y", "offset", "measured", "error", "limit", "off", "blended"}."""
    out = {"found": False, "x": float(x), "y": float(y), "offset": 0.0, "measured": (float(x), float(y)),
           "error": 0.0, "limit": 1.5, "off": False, "blended": False}
    try:
        true = recenter_info(img, x, y, radius, fwhm, blend_check=False, search=1.5 * float(radius), min_sigma=10.0)
    except Exception:
        return out
    if not real_star(true):
        return out
    fx, fy, sh = recenter(img, x, y, radius)
    if sh > recenter_jump_limit(radius):
        fx, fy = float(x), float(y)
    err = math.hypot(fx - true["x"], fy - true["y"])
    try:
        blended = bool(recenter_info(img, x, y, radius, fwhm, wing=wing).get("blended"))
    except Exception:
        blended = False
    out.update({"found": True, "x": float(true["x"]), "y": float(true["y"]), "offset": float(true["shift"]),
                "measured": (float(fx), float(fy)), "error": float(err), "off": err > 1.5, "blended": blended})
    return out


def star_fwhm(img: np.ndarray, x: float, y: float) -> dict:
    """2.2.8: FWHM of one star from a circular 2-D Gaussian fit (amplitude, center, sigma, sky) to a box that grows
    with the star. Replaces moments in a fixed 12 px radius, which on a faint or crowded star measured the noise and
    the neighbors (CoRoT-1 MicroObservatory frames: real stars 2.7-3.3 px, old method 10.5 px).
    Returns {"fwhm", "peak", "snr", "ok"}."""
    from scipy.optimize import least_squares

    out = {"fwhm": float("nan"), "peak": float("nan"), "snr": float("nan"), "ok": False}
    half = 6
    guess = None
    for _attempt in range(3):
        crop, cx, cy = _crop(img, x, y, half)
        if crop.shape[0] < 5 or crop.shape[1] < 5:
            return out
        data = np.array(crop, dtype=float)
        finite = np.isfinite(data)
        if finite.sum() < 20:
            return out
        yy, xx = np.mgrid[0 : data.shape[0], 0 : data.shape[1]]
        edge = np.hypot(xx - cx, yy - cy) >= half - 1.5
        sky0 = float(np.median(data[finite & edge])) if np.any(finite & edge) else float(np.median(data[finite]))
        amp0 = float(np.nanmax(data)) - sky0
        if not math.isfinite(amp0) or amp0 <= 0:
            return out
        s0 = guess if guess else 1.5
        p0 = [amp0, float(cx), float(cy), s0, sky0]

        def resid(p):
            a, x0, y0, sg, b = p
            model = a * np.exp(-((xx - x0) ** 2 + (yy - y0) ** 2) / (2.0 * sg * sg)) + b
            return (model - data)[finite]

        try:
            fit = least_squares(resid, p0, bounds=([0, cx - 3, cy - 3, 0.3, -np.inf], [np.inf, cx + 3, cy + 3, half, np.inf]))
        except Exception:
            return out
        a, _x0, _y0, sg, b = fit.x
        fwhm = 2.3548 * float(sg)
        res = resid(fit.x)
        noise = 1.4826 * float(np.median(np.abs(res - np.median(res)))) or 1.0
        out = {"fwhm": fwhm, "peak": float(a + b), "snr": float(a / noise), "ok": bool(fit.success and a > 0)}
        if fwhm * 1.5 <= half or half >= 20:
            break
        guess = float(sg)
        half = min(20, int(math.ceil(fwhm * 1.6)) + 2)
    return out


def field_fwhm(img: np.ndarray, n: int = 12, sat_level: float | None = None) -> dict:
    """2.2.8: the frame's seeing, measured on bright, unsaturated, isolated stars (median of up to n Gaussian fits).
    Returns {"fwhm", "n", "values"}; fwhm is NaN when no suitable star is found."""
    data = np.asarray(img, dtype=float)
    stars = detect_sources(data, max_sources=80, nsigma=8.0, min_sep=4.0)
    if not stars:
        return {"fwhm": float("nan"), "n": 0, "values": []}
    finite = data[np.isfinite(data)]
    top = float(np.max(finite)) if finite.size else float("nan")
    limit = sat_level if (sat_level is not None and math.isfinite(sat_level) and sat_level > 0) else 0.9 * top
    pts = np.array(stars, dtype=float)
    values = []
    for i, (sx, sy) in enumerate(stars):
        if len(values) >= n:
            break
        d = np.hypot(pts[:, 0] - sx, pts[:, 1] - sy)
        d[i] = np.inf
        if d.size and float(np.min(d)) < 10.0:
            continue  # a neighbor close enough to disturb the fit
        h, w = data.shape
        if not (12 <= sx < w - 12 and 12 <= sy < h - 12):
            continue
        r = star_fwhm(data, sx, sy)
        if not r["ok"] or not math.isfinite(r["fwhm"]) or not (0.8 < r["fwhm"] < 30):
            continue
        if math.isfinite(limit) and r["peak"] >= 0.85 * limit:
            continue  # saturated or nearly: a flat top fits too wide
        if r["snr"] < 15:
            continue
        values.append(r["fwhm"])
    if not values:
        return {"fwhm": float("nan"), "n": 0, "values": []}
    return {"fwhm": float(np.median(values)), "n": len(values), "values": values}


def shape_metrics(img: np.ndarray, x: float, y: float, radius: float, sky: float) -> tuple[float, float, float]:
    """photutils data_properties FWHM and elongation, plus peak counts."""
    crop, cx, cy = _crop(img, x, y, int(math.ceil(radius)) + 2)
    yy, xx = np.mgrid[0 : crop.shape[0], 0 : crop.shape[1]]
    rr = np.hypot(xx - cx, yy - cy)
    work = np.array(crop, dtype=float, copy=True)
    work[rr > radius] = sky
    peak = float(np.nanmax(crop[rr <= radius])) if np.any(rr <= radius) else float("nan")
    try:
        props = data_properties(work - sky)
        fwhm = float(props.fwhm.value) if props.fwhm is not None else float("nan")
        elong = float(props.elongation.value) if props.elongation is not None else float("nan")
    except Exception:
        return float("nan"), float("nan"), peak
    return fwhm, elong, peak


def register_frame(
    ref_sources: list[tuple[float, float]],
    sources: list[tuple[float, float]],
    shape: tuple[int, int],
    bin_px: float = 8.0,
    angles=None,
    allow_flip: bool = True,
    max_angle: float = 10.0,
) -> dict | None:
    """Find how the star field moved between the reference frame and this one.

    Step 1: every (reference star, frame star) pair votes for an offset; coarse 8 px bins
    tolerate a small rotation. Also tried with a 180-degree turn (a German-mount meridian flip).
    Step 2: take the stars that agree and fit shift plus rotation by least squares,
    tightening the match radius each pass. Slow field rotation from polar misalignment
    is absorbed here.
    Returns {"dx", "dy", "angle", "flipped", "votes", "of"} or None.

    2.2.18 (item 8): the defaults are the German-mount registration, unchanged. For an alt-az mount (field rotation,
    see register_rotating): angles = rotation guesses in degrees (the votes are cast with the reference turned by each
    guess first, so a field turned by many degrees still lines up), allow_flip=False (a field turned by 180° is a
    rotation, not a meridian flip) and max_angle=180.
    """
    if len(ref_sources) < 3 or len(sources) < 3:
        return None
    ref = np.asarray(ref_sources, dtype=float)
    cur = np.asarray(sources, dtype=float)
    h, w = shape
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    best = None
    # 2.2.18 (item 8): rotation guesses (alt-az). None = the German-mount registration as before (no guess).
    guesses = [0.0] if angles is None else [float(a) for a in angles]
    fewest_n = int(min(len(ref), len(cur)))

    def turned(pts, deg):
        if deg == 0.0:
            return pts
        t = math.radians(deg)
        c_, s_ = math.cos(t), math.sin(t)
        bx_, by_ = pts[:, 0] - cx, pts[:, 1] - cy
        return np.column_stack((c_ * bx_ - s_ * by_ + cx, s_ * bx_ + c_ * by_ + cy))

    if len(guesses) > 8:
        # A sweep (frame lost its track): rank the guesses by their best vote, refine only the 3 best.
        scores = []
        for g in guesses:
            k_ = np.round((cur[None, :, :] - turned(ref, g)[:, None, :]).reshape(-1, 2) / bin_px).astype(np.int64)
            k_ -= k_.min(axis=0)
            scores.append(int(np.bincount(k_[:, 0] * (int(k_[:, 1].max()) + 1) + k_[:, 1]).max()) if k_.size else 0)
        guesses = [guesses[k] for k in np.argsort(scores)[::-1][:3]]
    for flipped, hint in [(f, g) for f in ((False, True) if allow_flip else (False,)) for g in guesses]:
        if (angles is not None and best is not None and len(guesses) > 1
                and best["votes"] >= max(3, int(0.7 * fewest_n))):
            break   # 2.2.18 (item 8): a guess already matched most stars
        base = np.column_stack((w - 1 - ref[:, 0], h - 1 - ref[:, 1])) if flipped else ref
        vote_base = turned(base, hint)
        diff = (cur[None, :, :] - vote_base[:, None, :]).reshape(-1, 2)
        keys = np.round(diff / bin_px).astype(np.int64)
        uniq, counts = np.unique(keys, axis=0, return_counts=True)
        for key in uniq[np.argsort(counts)[::-1][:6]]:
            guess = key.astype(float) * bin_px
            near = diff[np.hypot(diff[:, 0] - guess[0], diff[:, 1] - guess[1]) < bin_px * 1.5]
            if near.size:
                guess = np.median(near, axis=0)
            # Model: cur = R(angle) (base - center) + center + shift
            angle, shift = math.radians(hint), guess
            matched = 0
            for tol in (bin_px * 1.5, 6.0, 4.0, 3.0):
                ca, sa = math.cos(angle), math.sin(angle)
                bx, by = base[:, 0] - cx, base[:, 1] - cy
                px = ca * bx - sa * by + cx + shift[0]
                py = sa * bx + ca * by + cy + shift[1]
                dist = np.hypot(px[:, None] - cur[None, :, 0], py[:, None] - cur[None, :, 1])
                j = dist.argmin(axis=1)
                ok = dist[np.arange(len(base)), j] < tol
                matched = int(ok.sum())
                if matched < 3:
                    break
                # Least-squares rotation + shift (2-D Procrustes) on the matched pairs.
                a = np.column_stack((bx[ok], by[ok]))
                b = cur[j[ok]] - np.array([cx, cy])
                am, bm = a.mean(axis=0), b.mean(axis=0)
                a0, b0 = a - am, b - bm
                num = np.sum(a0[:, 0] * b0[:, 1] - a0[:, 1] * b0[:, 0])
                den = np.sum(a0[:, 0] * b0[:, 0] + a0[:, 1] * b0[:, 1])
                angle = math.atan2(num, den)
                ca, sa = math.cos(angle), math.sin(angle)
                shift = bm - np.array([ca * am[0] - sa * am[1], sa * am[0] + ca * am[1]])
            if best is None or matched > best["votes"]:
                best = {"dx": float(shift[0]), "dy": float(shift[1]), "angle": math.degrees(angle),
                        "flipped": flipped, "votes": matched, "of": int(min(len(ref), len(cur)))}
    fewest = min(len(ref_sources), len(sources))
    # Sparse fields (a handful of stars) need every star; richer fields allow misses.
    needed = 3 if fewest <= 5 else max(4, fewest // 6)
    if best is None or best["votes"] < needed or abs(best["angle"]) > max_angle:
        return None
    return best


def move_star(star: "Star", reg: dict | None, shape: tuple[int, int]) -> "Star":
    """Where a star marked on the reference frame sits in a frame with registration reg."""
    if reg is None:
        return star
    h, w = shape
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    x, y = (w - 1 - star.x, h - 1 - star.y) if reg["flipped"] else (star.x, star.y)
    a = math.radians(reg.get("angle", 0.0))
    ca, sa = math.cos(a), math.sin(a)
    bx, by = x - cx, y - cy
    return Star(
        name=star.name,
        x=ca * bx - sa * by + cx + reg["dx"],
        y=sa * bx + ca * by + cy + reg["dy"],
        catalog_mag=star.catalog_mag,
    )


def compose_registration(first: dict, second: dict, shape: tuple[int, int]) -> dict:
    """Reference -> A (first), then A -> B (second), as one reference -> B registration."""
    h, w = shape
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    # Push three reference points through both, then refit.
    pts = [(cx - 300, cy - 200), (cx + 300, cy - 150), (cx + 50, cy + 250)]
    out = []
    for x, y in pts:
        s = move_star(move_star(Star("p", x, y), first, shape), second, shape)
        out.append((s.x, s.y))
    flipped = bool(first["flipped"]) != bool(second["flipped"])
    base = np.array([(w - 1 - x, h - 1 - y) if flipped else (x, y) for x, y in pts]) - np.array([cx, cy])
    b = np.array(out) - np.array([cx, cy])
    am, bm = base.mean(axis=0), b.mean(axis=0)
    a0, b0 = base - am, b - bm
    angle = math.atan2(np.sum(a0[:, 0] * b0[:, 1] - a0[:, 1] * b0[:, 0]), np.sum(a0[:, 0] * b0[:, 0] + a0[:, 1] * b0[:, 1]))
    ca, sa = math.cos(angle), math.sin(angle)
    shift = bm - np.array([ca * am[0] - sa * am[1], sa * am[0] + ca * am[1]])
    return {"dx": float(shift[0]), "dy": float(shift[1]), "angle": math.degrees(angle), "flipped": flipped,
            "votes": min(first["votes"], second["votes"]), "of": second.get("of", 0), "chained": True}


# 2.2.18 (item 8; review: finer, at most 8 so register_frame refines every guess): degrees around the predicted angle
ROTATION_NEAR_GUESSES = (0.0, -0.5, 0.5, -1.0, 1.0, -2.0, 2.0, -4.0)
ROTATION_SWEEP_STEP = 1.0                             # 2.2.18 (item 8): a frame that lost its track: every 1°


def register_rotating(ref_sources, sources, shape: tuple[int, int], last_sources=None, last_reg: dict | None = None):
    """2.2.18 (item 8): registration for an alt-az mount. Without a derotator the field turns about the optical
    axis through the night (by tens of degrees, fastest near the zenith); the German-mount registration votes for a
    shift only and refuses turns over 10°. Here: (1) against the reference, guessing angles near the last frame's
    (the field turns smoothly), (2) else the step from the last aligned frame, chained onto its registration,
    (3) else a sweep of the whole circle. Never 'flipped' (no meridian flip on an alt-az mount). With a derotator
    the angle stays near 0 and step (1) finds it at once. Returns a register_frame result or None; it carries "rate"
    (degrees turned since the last frame), so the next frame's guess is extrapolated (review: sparse fields at
    0.5°/frame lost a third of their frames without it)."""
    hint = float(last_reg.get("angle", 0.0)) if last_reg else 0.0
    rate = float(last_reg.get("rate", 0.0) or 0.0) if last_reg else 0.0
    hint += rate
    kw = {"allow_flip": False, "max_angle": 180.0}
    reg = register_frame(ref_sources, sources, shape, angles=[hint + d for d in ROTATION_NEAR_GUESSES], **kw)
    if reg is None and last_sources is not None and last_reg is not None:
        step = register_frame(last_sources, sources, shape, angles=ROTATION_NEAR_GUESSES, **kw)
        if step is not None:
            reg = compose_registration(last_reg, step, shape)
    if reg is None:
        reg = register_frame(ref_sources, sources, shape,
                             angles=list(np.arange(-180.0, 180.0, ROTATION_SWEEP_STEP)), **kw)
    if reg is not None:
        reg = dict(reg)
        reg["rate"] = (((reg["angle"] - float(last_reg.get("angle", 0.0)) + 180.0) % 360.0) - 180.0) if last_reg else 0.0
    return reg


def raw_peak_near(raw: np.ndarray, star: "Star", radius: float, total_bin: int) -> float:
    """Brightest raw (unbinned, uncalibrated) pixel around a star given in final-image coordinates.

    total_bin is app binning x 2 (the superpixel debayer). Binning and debayer average pixels,
    which can hide a clipped core; the raw frame cannot.
    """
    rx = (star.x + 0.5) * total_bin - 0.5
    ry = (star.y + 0.5) * total_bin - 0.5
    r = max(radius * total_bin, total_bin * 2)
    x0, x1 = max(0, int(rx - r)), min(raw.shape[1], int(rx + r) + 1)
    y0, y1 = max(0, int(ry - r)), min(raw.shape[0], int(ry + r) + 1)
    if x1 <= x0 or y1 <= y0:
        return float("nan")
    return float(np.nanmax(raw[y0:y1, x0:x1]))


def star_under(img: np.ndarray, x: float, y: float, radius: float, sigma: float = 5.0) -> bool:
    """2.2.3: is there a star under (x, y)? The brightest pixel within the aperture must stand `sigma` noise units
    above the median of the surrounding box."""
    h, w = img.shape[:2]
    r = max(int(round(radius * 2)), 4)
    x0, x1 = max(0, int(x) - r), min(w, int(x) + r + 1)
    y0, y1 = max(0, int(y) - r), min(h, int(y) + r + 1)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return False
    box = np.asarray(img[y0:y1, x0:x1], dtype=float)
    finite = box[np.isfinite(box)]
    if finite.size < 9:
        return False
    background = float(np.median(finite))
    noise = 1.4826 * float(np.median(np.abs(finite - background))) or 1e-9
    yy, xx = np.mgrid[y0:y1, x0:x1]
    inner = box[(np.hypot(xx - x, yy - y) <= max(radius, 2.0)) & np.isfinite(box)]
    return bool(inner.size and float(inner.max()) - background > sigma * noise)


def flat_topped_core(raw: np.ndarray, star: "Star", radius: float, total_bin: int,
                     min_pixels: int = 4, tolerance: float = 0.02, cfa: bool | None = None) -> bool:
    """2.2.3: does the star's core look clipped (saturated) in a frame whose values are no longer camera counts?

    Pre-calibrated frames (dark-subtracted and flat-divided elsewhere) have no fixed saturation level, so the raw
    limit cannot be used. A clipped core shows as a plateau. The test compares the plateau (pixels within
    `tolerance` of the peak) with the star's half-maximum area, which makes it independent of the star's width:
    an unclipped Gaussian has a plateau of about 3% of its half-max area (2*-ln(1-tol) / (2 ln 2)), a core
    clipped well below its true peak (about 25% or more) has over 20%. On a color (CFA) mosaic each color's pixels are tested
    on their own, since the colors sit at different levels after flat division, and two of them must agree. The
    peak must also stand well
    above the sky noise, so a faint star in noise is never called saturated."""
    rx = (star.x + 0.5) * total_bin - 0.5
    ry = (star.y + 0.5) * total_bin - 0.5
    r = max(radius * total_bin, total_bin * 2)
    x0, x1 = max(0, int(rx - r)), min(raw.shape[1], int(rx + r) + 1)
    y0, y1 = max(0, int(ry - r)), min(raw.shape[0], int(ry + r) + 1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return False
    box = np.asarray(raw[y0:y1, x0:x1], dtype=float)
    if cfa is None:
        cfa = total_bin >= 2 and total_bin % 2 == 0
    planes = [box[i::2, j::2] for i in (0, 1) for j in (0, 1)] if cfa else [box]
    floor_pixels = min_pixels
    flagged = 0
    for plane in planes:
        finite = plane[np.isfinite(plane)]
        if finite.size < 9:
            continue
        peak = float(finite.max())
        # Sky from the box's outer ring, so a star that fills much of the box does not raise it.
        ring = np.concatenate([plane[0, :], plane[-1, :], plane[1:-1, 0], plane[1:-1, -1]])
        ring = ring[np.isfinite(ring)]
        if ring.size < 6:
            ring = finite
        background = float(np.median(ring))
        noise = 1.4826 * float(np.median(np.abs(ring - background))) or 1.0
        height = peak - background
        if height < 50 * noise:
            continue
        plateau = int(np.count_nonzero(finite >= background + height * (1.0 - tolerance)))
        half = int(np.count_nonzero(finite >= background + 0.5 * height))
        if plateau >= floor_pixels and half > 0 and plateau >= 0.20 * half:
            flagged += 1
    # A clipped core clips every color that reaches the limit (at least the two green planes); one plane alone can
    # be a star sitting exactly between its pixels.
    return flagged >= (2 if cfa else 1)


def raw_peaks_many(raw: np.ndarray, pos, radius: float, total_bin: int) -> np.ndarray:
    """2.2.19 (#25): raw_peak_near for many stars (final-image positions, one row per star); NaN off the frame."""
    pos = np.asarray(pos, dtype=float).reshape(-1, 2)
    out = np.full(len(pos), np.nan)
    if raw is None or np.ndim(raw) < 2:
        return out
    h, w = raw.shape[0], raw.shape[1]
    r = max(radius * total_bin, total_bin * 2)
    for k, (x, y) in enumerate(pos):
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        rx = (x + 0.5) * total_bin - 0.5
        ry = (y + 0.5) * total_bin - 0.5
        x0, x1 = max(0, int(rx - r)), min(w, int(rx + r) + 1)
        y0, y1 = max(0, int(ry - r)), min(h, int(ry + r) + 1)
        if x1 > x0 and y1 > y0:
            with np.errstate(all="ignore"):
                out[k] = float(np.nanmax(raw[y0:y1, x0:x1]))
    return out


def scan_saturated(hits, measured, min_fraction: float = 0.05) -> tuple[set, set]:
    """2.2.19 (#25): (saturated, clipped now and then) star indices from per-star counts of frames whose raw core
    reached the saturation level (hits) out of the frames measured. Saturated: clipped on at least `min_fraction` of
    the frames (and on at least one); a star clipped on fewer frames is only a caution."""
    hits = np.asarray(hits, dtype=float)
    measured = np.asarray(measured, dtype=float)
    sat, some = set(), set()
    for i, (n_hit, n) in enumerate(zip(hits, measured)):
        if n_hit <= 0 or n <= 0:
            continue
        if n_hit >= max(1.0, min_fraction * n):
            sat.add(i)
        else:
            some.add(i)
    return sat, some


def scan_neighbor_flags(points, point_mags, others, other_mags, reach: float, dmag: float = 3.0,
                        same: float = 1.0) -> np.ndarray:
    """2.2.19 (#26): crowded[i] is True when one of `others` (an un-thinned list: every detected peak, or the catalog
    stars placed on the frame) lies within `reach` px of points[i] (the aperture, the gap and the sky ring) and is not
    at least `dmag` fainter (unknown magnitude: counts). Others closer than `same` px are the star itself."""
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    oth = np.asarray(others, dtype=float).reshape(-1, 2)
    out = np.zeros(len(pts), dtype=bool)
    if not len(pts) or not len(oth) or not (reach > 0):
        return out
    pm = np.asarray(point_mags, dtype=float)
    om = np.asarray(other_mags, dtype=float)
    from scipy.spatial import cKDTree
    near = cKDTree(oth).query_ball_point(pts, r=float(reach))
    for i, idx in enumerate(near):
        for j in idx:
            if math.hypot(oth[j, 0] - pts[i, 0], oth[j, 1] - pts[i, 1]) < same:
                continue
            if not math.isfinite(om[j]) or not math.isfinite(pm[i]) or om[j] < pm[i] + dmag:
                out[i] = True
                break
    return out


def _header_float(header: dict, *keys: str) -> float | None:
    for key in keys:
        raw = header.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _mag_variance(flux: float, variance: float) -> float:
    if flux <= 0:
        return 1.0
    return (1.085736 / flux) ** 2 * variance


# ---- 2.2.17 (item 8): photon noise in electrons ------------------------------------------------------------------
# Before 2.2.17 a star's photon variance was its net flux in image units, as if one image unit were one electron.
# SHOBS-P's image unit is not: the software binning and the superpixel debayer AVERAGE camera pixels (TG: 8 green
# pixels per final pixel at Bin 2), the flat divides by its value (green ~1.5 on an OSC flat normalized at the
# mosaic median), and the camera gives EGAIN e-/ADU (ASI2600 at gain 100: 0.243). The photon variance is now
# flux / k, with k = electrons per image unit = EGAIN / sum(w_j^2 / (b^2 f_j)) over the camera pixels j averaged
# into a final pixel (weights w_j of the debayer, b = software binning, f_j = master flat). The sky term stays the
# measured sky scatter, plus the error of the sky level itself (the CCD equation of Merline & Howell 1995).
# The old fixed 0.005 mag added to every point is gone: the real 60 s nights scatter LESS than 0.005 per point.
E_PER_ADU_RANGE = (0.01, 100.0)   # plausible camera gains in e-/ADU; anything else is not a gain
SKY_CHECK_E = 100.0               # sky electrons per image pixel above which read noise cannot hide a gain mismatch
OLD_ERR_FLOOR = 0.005             # the fixed term of the pre-2.2.17 model (kept only for points measured with it)


def error_model_text(observations, name=None) -> str:
    """2.2.17 (item 8, review): a caution when a series mixes nights reduced before 2.2.17 (image units as electrons
    plus 0.005 mag: larger error bars on long exposures) with nights reduced after; '' when all nights share one."""
    by_night = {}
    for o in observations:
        by_night.setdefault(o.night, set()).add(int(getattr(o, "e_model", 0) or 0))
    old = [n for n, m in by_night.items() if m == {0}]
    if not old or len(old) == len(by_night):
        return ""
    label = name or (lambda n: str(n))
    shown = ", ".join(label(n) for n in old[:4]) + (" …" if len(old) > 4 else "")
    return (f"{len(old)} night{'s' if len(old) > 1 else ''} ({shown}) reduced by an older version of SHOBS-P keep the old, larger "
            "error bars (image units taken as electrons, plus 0.005 mag); run photometry on them again for error bars "
            "that match the other nights.")


def header_e_per_adu(header: dict | None) -> float | None:
    """2.2.17 (item 8): the camera gain in electrons per ADU from EGAIN (NINA, ASIAIR, SharpCap, MaxIm, ...), or None.
    GAIN is not read as e-/ADU: ZWO, QHY, NINA and ASIAIR write the gain SETTING there (100), Andor an index (1)."""
    v = _header_float(header or {}, "EGAIN")
    if v is None or not (E_PER_ADU_RANGE[0] <= v <= E_PER_ADU_RANGE[1]):
        return None
    return float(v)


def debayer_weights(pattern: str, mode: str) -> dict:
    """2.2.17 (item 8): the weight of each 2x2 Bayer position (iy, ix) in one superpixel of debayer(pattern, mode);
    {(0, 0): 1.0} for a monochrome camera."""
    pattern = (pattern or "RGGB").upper()
    if pattern == MONO:
        return {(0, 0): 1.0}
    positions = {
        "RGGB": {(0, 0): "R", (0, 1): "G", (1, 0): "G", (1, 1): "B"},
        "BGGR": {(0, 0): "B", (0, 1): "G", (1, 0): "G", (1, 1): "R"},
        "GRBG": {(0, 0): "G", (0, 1): "R", (1, 0): "B", (1, 1): "G"},
        "GBRG": {(0, 0): "G", (0, 1): "B", (1, 0): "R", (1, 1): "G"},
    }.get(pattern)
    if positions is None:
        raise ValueError(f"Unsupported Bayer pattern {pattern}")
    per_color = {"luminance": {"R": 0.299, "G": 0.587 / 2, "B": 0.114}, "green": {"R": 0.0, "G": 0.5, "B": 0.0},
                 "red": {"R": 1.0, "G": 0.0, "B": 0.0}, "blue": {"R": 0.0, "G": 0.0, "B": 1.0}}
    w = per_color.get(mode, per_color["luminance"])
    return {pos: w[color] for pos, color in positions.items() if w[color] > 0}


def electrons_per_unit(e_per_adu: float, binning: int, pattern: str, mode: str, flat: np.ndarray | None = None):
    """2.2.17 (item 8): electrons per unit of the measured (binned, calibrated, debayered) image: a number, or with a
    master flat a map the shape of the debayered image (the flat changes it across the field and per color).

    A final pixel is sum_j w_j * raw_j / f_j over camera pixels j (b x b binning averages, then the superpixel
    weights w_j), so its photon variance in image units is value * sum_j (w_j^2 / (b^2 f_j)) / EGAIN when the colors
    carry similar signal. k = EGAIN / that sum. Mono, Bin 1, no flat: k = EGAIN. TG at Bin 2: 8 * EGAIN * f_green.
    EGAIN is taken as the gain of the pixels in the file: with camera binning (XBINNING > 1) NINA and ASIAIR write the
    camera's value for its binned pixels, which is what is used; the sky cross-check (photon_noise_setup) catches a
    gain that does not fit the stored values (12/14-bit data scaled to 16 bits, or a binned frame summed in software)."""
    b = max(int(binning or 1), 1)
    weights = debayer_weights(pattern, mode)
    if flat is None:
        q = sum(w * w for w in weights.values()) / (b * b)
        return float(e_per_adu) / q
    flat = np.asarray(flat, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = np.where(np.isfinite(flat) & (flat > 0), 1.0 / flat, np.nan)
    if (pattern or "").upper() == MONO:
        q = inv / (b * b)
    else:
        h, w = inv.shape[0] // 2 * 2, inv.shape[1] // 2 * 2
        q = np.zeros((h // 2, w // 2))
        for (iy, ix), wt in weights.items():
            q = q + (wt * wt) * inv[iy:h:2, ix:w:2]
        q = q / (b * b)
    fill = np.nanmedian(q) if np.isfinite(q).any() else sum(x * x for x in weights.values()) / (b * b)
    q = np.where(np.isfinite(q) & (q > 0), q, fill)
    return (float(e_per_adu) / q).astype(np.float32)


def electrons_at(electrons, x: float, y: float, radius: float) -> float:
    """2.2.17 (item 8): electrons per image unit at a star (the mean of the map over the aperture's box)."""
    if electrons is None:
        return float("nan")
    if np.ndim(electrons) == 0:
        return float(electrons)
    h, w = electrons.shape
    r = max(int(math.ceil(radius)), 1)
    x0, x1 = max(0, int(round(x)) - r), min(w, int(round(x)) + r + 1)
    y0, y1 = max(0, int(round(y)) - r), min(h, int(round(y)) + r + 1)
    if x1 <= x0 or y1 <= y0:
        return float(np.nanmedian(electrons))
    return float(np.nanmean(electrons[y0:y1, x0:x1]))


def star_variance(measured: dict, electrons, radius: float, sky_inner: float, sky_outer: float) -> float:
    """2.2.17 (item 8): a star's flux variance in image units squared: photon noise flux / k (k electrons per unit
    at the star) plus the sky, n_pix * sky_sd^2 * (1 + n_pix / n_sky) (Merline & Howell 1995: the second part is the
    error of the sky level taken from n_sky annulus pixels). The sky level is a median, whose variance is pi/2 times
    a mean's, so n_sky counts 2/pi of the annulus pixels. electrons None: the pre-2.2.17 variance as measured."""
    if electrons is None:
        return float(measured["variance"])
    k = electrons_at(electrons, measured.get("x", 0.0), measured.get("y", 0.0), radius)
    if not (math.isfinite(k) and k > 0):
        return float(measured["variance"])
    n_pix = float(measured.get("n") or math.pi * radius ** 2)
    n_sky = max(2.0 * (sky_outer ** 2 - sky_inner ** 2), 1.0)   # pi (r_out^2 - r_in^2) x 2/pi (median)
    sd = float(measured.get("sky_std") or 0.0)
    return max(float(measured["flux"]), 0.0) / k + n_pix * sd * sd * (1.0 + n_pix / n_sky)


def sky_electrons_estimate(img: np.ndarray, stars, radius: float, sky_inner: float, sky_outer: float,
                           with_level: bool = False):
    """2.2.17 (item 8): electrons per image unit from the sky itself, sky level / sky variance (a Poisson sky has
    variance = level in electrons), the median over the given stars' annuli. Read noise, dark noise and faint stars
    only add variance, so this is a LOWER limit on k (photon error bars from it are on the large side). Only valid
    on a frame with the bias taken out (sky level = sky signal). NaN when it cannot be measured."""
    vals, levels = [], []
    for s in stars:
        try:
            m = measure_aperture(img, s.x, s.y, radius, sky_inner, sky_outer)
        except Exception:
            continue
        sd, sky = float(m.get("sky_std") or 0.0), float(m.get("sky") or 0.0)
        if sd > 0 and sky > 0 and math.isfinite(sd * sky):
            vals.append(sky / (sd * sd))
            levels.append(sky)
    k = float(np.median(vals)) if vals else float("nan")
    if with_level:   # 2.2.17 (review): also the sky level, for the EGAIN cross-check
        return k, (float(np.median(levels)) if levels else float("nan"))
    return k


def photon_noise_setup(header: dict | None, binning: int, pattern: str, mode: str, flat=None,
                       bias_removed: bool = True, precalibrated: bool = False, sky_k: float = float("nan"),
                       sky_level: float = float("nan")) -> dict:
    """2.2.17 (item 8): how the photon noise is converted to electrons for a run. Returns {"electrons": number or map,
    "e_per_adu": EGAIN or None, "source": "EGAIN" / "sky" / "assumed", "note": the log line(s), "warning": '' or a
    caution, "k_median", "sky_k"}.
    1. EGAIN in the header (not for pre-calibrated frames, whose values may have been rescaled by other software).
    2. Else the sky's own level / variance (sky_k, measured on the run's reference frame), a lower limit on k when
       the bias is out of the frame and no pedestal was added.
    3. Else 1 e-/ADU, with the averaging and flat still counted.
    Review: with EGAIN the sky estimate is a cross-check. sky_k is a lower limit, so sky_k > 2 k says EGAIN is too
    small for the stored values or the frames carry a pedestal; sky_k < k / 2 on a bright sky (over SKY_CHECK_E
    electrons per pixel, where read noise cannot explain it) says EGAIN is too large for the stored values (12/14-bit
    data scaled to 16 bits with EGAIN per native ADU, or a software-summed binned frame)."""
    b = max(int(binning or 1), 1)
    pedestal = _header_float(header or {}, "PEDESTAL")
    ped_note = (f" The header gives a pedestal ({pedestal:g}): if it is still in the values, the sky estimate is too "
                "high and these error bars may be too small." if pedestal else
                (" If the other software added a pedestal, the sky estimate is too high and the error bars may be too "
                 "small." if precalibrated else ""))
    gain = None if precalibrated else header_e_per_adu(header)
    if gain is not None:
        k = electrons_per_unit(gain, b, pattern, mode, flat)
        at = float(np.nanmedian(k)) if np.ndim(k) else float(k)
        note = (f"Photon noise: camera gain {gain:.3g} e-/ADU (EGAIN) = about {at:.3g} electrons per image unit "
                f"after Bin {b}" + ("" if (pattern or "").upper() == MONO else ", the superpixel")
                + (" and the flat" if flat is not None else "") + ".")
        warning = ""
        if bias_removed and math.isfinite(sky_k) and sky_k > 0:
            note += f" The sky's own noise gives {sky_k:.3g} (a lower limit)."
            sky_e = sky_level * at if math.isfinite(sky_level) else float("nan")
            if sky_k > 2.0 * at:
                warning = (f"The sky's noise says at least {sky_k:.3g} electrons per image unit, over twice what "
                           f"EGAIN {gain:.3g} e-/ADU gives ({at:.3g}): EGAIN may be too small for the stored values, "
                           "or the frames carry a pedestal. Error bars may be too large.")
            elif sky_k < 0.5 * at and math.isfinite(sky_e) and sky_e > SKY_CHECK_E:
                warning = (f"The sky's noise says about {sky_k:.3g} electrons per image unit, under half what EGAIN "
                           f"{gain:.3g} e-/ADU gives ({at:.3g}), on a sky too bright for read noise to explain: "
                           "EGAIN may be per native ADU of 12/14-bit data stored as 16-bit, or for unsummed binning. "
                           "Error bars may be too small; check the Transit fit's reduced chi-square.")
        return {"electrons": k, "e_per_adu": gain, "source": "EGAIN", "note": note, "k_median": at,
                "sky_k": sky_k, "warning": warning}
    why = ("the frames are pre-calibrated (their values may not be camera ADU)" if precalibrated
           else "the headers give no EGAIN (camera gain in e-/ADU)")
    if bias_removed and math.isfinite(sky_k) and sky_k > 0:
        safe = ("a lower limit: error bars on the safe side" if not (precalibrated or pedestal)
                else "a lower limit unless a pedestal is in the values")
        return {"electrons": float(sky_k), "e_per_adu": None, "source": "sky", "k_median": float(sky_k),
                "sky_k": sky_k, "warning": "",
                "note": f"Photon noise: {why}, so the gain comes from the sky's own noise ({sky_k:.3g} electrons per "
                        f"image unit, {safe}).{ped_note}"}
    k = electrons_per_unit(1.0, b, pattern, mode, flat)
    at = float(np.nanmedian(k)) if np.ndim(k) else float(k)
    return {"electrons": k, "e_per_adu": None, "source": "assumed", "k_median": at, "sky_k": sky_k, "warning": "",
            "note": f"Photon noise: {why}, so 1 e-/ADU is assumed. Error bars scale as 1/sqrt(true gain); "
                    "the Transit fit's reduced chi-square shows whether they fit the scatter."}


def reduce_frame(
    img: np.ndarray,
    header: dict,
    target: Star,
    comps: list[Star],
    check: Star | None,
    radius: float,
    sky_inner: float,
    sky_outer: float,
    lat: float | None = None,
    lon: float | None = None,
    ra_hours: float | None = None,
    dec_deg: float | None = None,
    sat_limit: float = 60000,
    aligned: bool = True,
    electrons=None,
) -> Obs:
    """One calibrated, debayered frame -> one differential magnitude.

    With more than one comparison star this is a simple ensemble: the mean instrumental
    magnitude of the comps against the mean of their catalog magnitudes.
    2.2.17 (item 8): electrons = electrons per image unit (a number or a map, see photon_noise_setup) for the photon
    noise; None keeps the measured "variance" (flux in image units as electrons, the pre-2.2.17 model).
    """
    exp = header_exptime(header)
    jd = mid_jd(header)
    if not comps:
        raise ValueError("At least one comparison star is required")
    for comp in comps:
        if comp.catalog_mag is None or not math.isfinite(comp.catalog_mag):
            raise ValueError(f"{comp.name} needs a catalog magnitude in the band you are measuring")

    tx, ty, shift = recenter(img, target.x, target.y, radius)
    drifted = shift > radius
    # 2.2.8: frames are aligned to well under a pixel, so a big jump means the centring found another star: measure
    # at the aligned position instead.
    jump_limit = recenter_jump_limit(radius) if aligned else float("inf")   # unaligned frames follow drift by centring
    use_x, use_y = (target.x, target.y) if shift > jump_limit else (tx, ty)
    var = measure_aperture(img, use_x, use_y, radius, sky_inner, sky_outer)
    fwhm, elong, peak = shape_metrics(img, use_x, use_y, radius, var["sky"])

    comp_inst = []
    comp_pos = []
    comp_fluxes = []
    comp_mag_var = []
    comp_peak = float("-inf")
    for comp in comps:
        cx, cy, c_shift = recenter(img, comp.x, comp.y, radius)
        if c_shift > jump_limit:
            cx, cy = comp.x, comp.y
        comp_pos.append([round(float(cx), 2), round(float(cy), 2)])
        measured = measure_aperture(img, cx, cy, radius, sky_inner, sky_outer)
        if measured["flux"] <= 0:
            raise ValueError(f"{comp.name} has no flux above sky in this frame")
        comp_inst.append(inst_mag(measured["flux"], exp))
        comp_fluxes.append(measured["flux"])
        # 2.2.17 (item 8): photon noise in electrons
        comp_mag_var.append(_mag_variance(measured["flux"], star_variance(measured, electrons, radius, sky_inner,
                                                                          sky_outer)))
        _, _, cpeak = shape_metrics(img, cx, cy, radius, measured["sky"])
        if math.isfinite(cpeak):
            comp_peak = max(comp_peak, cpeak)
    c_inst = float(np.mean(comp_inst))
    c_cat = float(np.mean([comp.catalog_mag for comp in comps]))
    # Error of a mean of n independent comp magnitudes.
    c_var = float(np.sum(comp_mag_var)) / len(comps) ** 2

    chk = None
    k_pos = None
    if check is not None:
        kx, ky, k_shift = recenter(img, check.x, check.y, radius)
        if k_shift > jump_limit:
            kx, ky = check.x, check.y
        chk = measure_aperture(img, kx, ky, radius, sky_inner, sky_outer)
        k_pos = [round(float(kx), 2), round(float(ky), 2)]

    v_inst = inst_mag(var["flux"], exp)
    mag = c_cat + (v_inst - c_inst)
    # 2.2.17 (item 8): photon noise in electrons; no fixed 0.005 mag floor (the real scatter is below it on 60 s nights).
    # electrons None (no gain worked out) keeps the whole pre-2.2.17 model, floor included, marked e_model 0.
    t_var = _mag_variance(var["flux"], star_variance(var, electrons, radius, sky_inner, sky_outer))
    merr = math.sqrt(t_var + c_var + (OLD_ERR_FLOOR ** 2 if electrons is None else 0.0))

    kmag = inst_mag(chk["flux"], exp) if chk is not None else None
    if kmag is not None and not math.isfinite(kmag):
        kmag = None
    check_std = None
    if kmag is not None and check is not None and check.catalog_mag is not None and math.isfinite(check.catalog_mag):
        check_std = c_cat + (kmag - c_inst)

    if ra_hours is None:
        ra_hours = _header_float(header, "RA_HRS")
        if ra_hours is None:
            ra_deg = _header_float(header, "OBJCTRA_DEG", "RA")
            ra_hours = ra_deg / 15.0 if ra_deg is not None else None
    if dec_deg is None:
        dec_deg = _header_float(header, "DEC", "OBJDEC")
    am = None
    if lat is not None and lon is not None and ra_hours is not None and dec_deg is not None:
        try:
            am = airmass(float(ra_hours), float(dec_deg), lat, lon, jd)
        except (TypeError, ValueError):
            am = None
        if am is not None and not math.isfinite(am):
            am = None

    saturated = bool(math.isfinite(peak) and peak >= sat_limit) or comp_peak >= sat_limit
    flags = []
    if math.isfinite(peak) and peak >= sat_limit:
        flags.append("sat")
    if comp_peak >= sat_limit:
        flags.append("compsat")
    if drifted:
        flags.append("drift")
    if var["flux"] <= 0:
        flags.append("noflux")
    return Obs(
        jd=jd,
        mag=mag,
        merr=merr,
        cmag=c_inst,
        kmag=kmag,
        airmass=am,
        path=str(header.get("_PATH", "")),
        exptime=exp,
        flux_var=var["flux"],
        flux_comp=float(np.sum(comp_fluxes)),
        fwhm=fwhm,
        elong=elong,
        peak=peak,
        sky=var["sky"],
        shift=shift,
        saturated=saturated,
        drifted=drifted,
        check_std=check_std,
        flag=" ".join(flags),
        comp_insts=[float(v) for v in comp_inst],
        target_var=float(t_var),
        comp_vars=[float(v) for v in comp_mag_var],
        n_comps=len(comps),
        e_model=0 if electrons is None else 1,   # 2.2.17 (item 8)
        target_inst=float(v_inst),
        comp_cat=c_cat,
        pos={"t": [round(float(use_x), 2), round(float(use_y), 2)], "c": comp_pos, "k": k_pos},
    )


def planet_name_fallbacks(name: str) -> list[str]:
    """2.2.6: for a name that ends in a planet letter after a number ("TrES-3 b", "KOI-217b", "HD 219134 b"): the
    no-space planet spelling SIMBAD uses, then the host star. Anything else: []."""
    import re

    clean = " ".join(name.strip().split())
    m = re.match(r"^(.*\d)\s?([b-iB-I])$", clean)
    if not m:
        return []
    host = m.group(1).strip()
    return list(dict.fromkeys([host + m.group(2).lower(), host]))


def lookup_target(name: str) -> dict:
    """SIMBAD first, so any designation works. VSX is the fallback, and also supplies a catalog period when it has one."""
    errors = []
    found = None
    try:
        found = _lookup_simbad(name)
    except Exception as exc:
        errors.append(f"SIMBAD: {exc}")
    try:
        vsx = _lookup_vsx(name)
    except Exception as exc:
        errors.append(f"VSX: {exc}")
        vsx = None
    if found is None and vsx is None:
        # 2.2.6: a planet name in the Star ID box. SIMBAD writes planets without the space ("WASP-12b",
        # "Kepler-71b") and does not know some spellings ("TrES-3 b"); try its spelling, then the host star.
        for alt in planet_name_fallbacks(name):
            try:
                found = _lookup_simbad(alt)
                found["name"] = alt
                found["note"] = f"looked up as {alt}"
                break
            except Exception:
                continue
    if found is None and vsx is None:
        raise ValueError(f"Could not find {name}.\n" + "\n".join(errors))
    if found is None:
        found = vsx
        found["source"] = "VSX"
    elif vsx is not None:
        for key in ("period_days", "var_type", "auid", "epoch_jd"):   # 2.2.14 (item 13): epoch
            if vsx.get(key):
                found[key] = vsx[key]
    return found


def _http_get(url: str, timeout: float = 45) -> bytes:
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "SHOBS-P/2.1.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _lookup_simbad(name: str) -> dict:
    import re
    import urllib.parse

    url = "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=" + urllib.parse.quote(name.strip()) + "&output.format=ASCII"
    text = _http_get(url).decode("utf-8", errors="replace")
    if "identifier not found" in text.lower() or "no known catalog" in text.lower():
        raise ValueError(f"SIMBAD has no object called {name}")
    coord = re.search(r"Coordinates\(ICRS,ep=J2000.*?\):\s+([0-9.\s+-]+)", text)
    if not coord:
        raise ValueError(f"SIMBAD has no coordinates for {name}")
    parts = coord.group(1).split()
    sign_index = next((i for i, part in enumerate(parts) if part[0] in "+-"), None)
    if sign_index is None or sign_index == 0:
        raise ValueError(f"Could not read SIMBAD coordinates for {name}")
    ra_deg = _sexagesimal(" ".join(parts[:sign_index])) * 15.0
    dec_deg = _sexagesimal(" ".join(parts[sign_index:sign_index + 3]))
    pm = re.search(r"Proper motions:\s+([+-]?\d+(?:\.\d+)?)\s+([+-]?\d+(?:\.\d+)?)", text)
    if pm:
        ra_deg, dec_deg = _apply_pm(ra_deg, dec_deg, float(pm.group(1)), float(pm.group(2)))
    return {"ra_hours": ra_deg / 15.0, "dec_deg": dec_deg, "name": name.strip(), "source": "SIMBAD"}


def _lookup_vsx(name: str) -> dict:
    """AAVSO VSX object API. RA2000 and Declination2000 come back as decimal degrees."""
    import urllib.parse

    query = urllib.parse.urlencode({"view": "api.object", "ident": name.strip(), "format": "json"})
    payload = json.loads(_http_get("https://vsx.aavso.org/index.php?" + query).decode("utf-8"))
    obj = payload.get("VSXObject") if isinstance(payload, dict) else None
    if not obj or not obj.get("RA2000") or not obj.get("Declination2000"):
        raise ValueError(f"VSX has no object called {name}")
    ra_deg = _angle(obj["RA2000"], hours=True)
    dec_deg = _angle(obj["Declination2000"], hours=False)
    try:
        ra_deg, dec_deg = _apply_pm(ra_deg, dec_deg, float(obj["ProperMotionRA"]), float(obj["ProperMotionDec"]))
    except (KeyError, TypeError, ValueError):
        pass
    period = None
    try:
        period = float(obj.get("Period")) if obj.get("Period") else None
    except ValueError:
        period = None
    return {
        "ra_hours": ra_deg / 15.0,
        "dec_deg": dec_deg,
        "name": obj.get("Name") or name.strip(),
        "source": "VSX",
        "period_days": period,
        "var_type": obj.get("VariabilityType", ""),
        "auid": obj.get("AUID", ""),
        "epoch_jd": _vsx_epoch(obj.get("Epoch")),   # 2.2.14 (item 13): epoch of max/min for O-C (HJD in VSX)
    }


def _vsx_epoch(text):
    """2.2.14 (item 13): VSX's Epoch field (a full JD/HJD, sometimes with a trailing note) as a float, else None.
    Review: a 5-digit epoch is JD - 2400000 ('53766.839'), a 7-digit one the full JD."""
    try:
        v = float(str(text).split()[0])
    except (TypeError, ValueError, IndexError):
        return None
    if 10000.0 <= v < 100000.0:
        v += 2400000.0
    return v if 2400000.0 < v < 2600000.0 else None


# 2.2.13: the epoch catalog positions are moved to (decimal year). None = now; the app sets it from the lights'
# DATE-OBS before solving, so old data (WASP-12 b 2015) get the stars where they were that night.
PM_EPOCH_YEAR = None


def header_epoch_year(header: dict):
    """Decimal year of a frame's DATE-OBS (None when missing or unreadable)."""
    text = str((header or {}).get("DATE-OBS") or "").strip()
    if not text:
        return None
    try:
        return float(Time(text.replace(" ", "T")[:23], format="isot", scale="utc").decimalyear)
    except Exception:
        return None


def pm_epoch() -> float:
    return float(PM_EPOCH_YEAR) if PM_EPOCH_YEAR else Time.now().decimalyear


def _apply_pm(ra_deg: float, dec_deg: float, pm_ra_cosdec: float, pm_dec: float) -> tuple[float, float]:
    """Move J2000 coordinates to now. Proper motions in mas/yr, RA component already times cos(dec)."""
    years = pm_epoch() - 2000.0
    ra_deg += pm_ra_cosdec * years / (3_600_000.0 * max(math.cos(math.radians(dec_deg)), 0.01))
    dec_deg += pm_dec * years / 3_600_000.0
    return ra_deg % 360.0, dec_deg


def fetch_aavso_chart(ra_hours: float, dec_deg: float, fov_arcmin: float, maglimit: float = 16.0) -> dict:
    """AAVSO Variable Star Plotter photometry table. RA is hours, Dec is degrees."""
    import urllib.parse

    query = urllib.parse.urlencode({
        "format": "json",
        "ra": f"{ra_hours * 15.0:.5f}",
        "dec": f"{dec_deg:.5f}",
        "fov": f"{min(max(fov_arcmin, 5.0), 900.0):.0f}",
        "maglimit": maglimit,
    })
    errors = []
    for base in ("https://app.aavso.org/vsp/api/chart/", "https://www.aavso.org/apps/vsp/api/chart/"):
        try:
            return json.loads(_http_get(base + "?" + query, timeout=60).decode("utf-8"))
        except Exception as exc:
            errors.append(f"{base}: {exc}")
    raise ConnectionError("AAVSO chart request failed.\n" + "\n".join(errors))


def _sexagesimal(text: str) -> float:
    """'12 34 56.7', '-05:06:07', '12 34.5' or '12' -> decimal value in the same unit as the first field."""
    text = str(text).strip()
    parts = [float(part) for part in text.replace(":", " ").split()]
    if not parts:
        raise ValueError("empty coordinate")
    sign = -1 if text.startswith("-") else 1
    value = abs(parts[0])
    if len(parts) > 1:
        value += parts[1] / 60.0
    if len(parts) > 2:
        value += parts[2] / 3600.0
    return sign * value


def _angle(text: str, hours: bool) -> float:
    """Decimal degrees from either a decimal-degree string or sexagesimal (hours for RA)."""
    text = str(text).strip()
    if ":" in text or " " in text:
        value = _sexagesimal(text)
        return value * 15.0 if hours else value
    return float(text)


def _hms_to_deg(text: str) -> float:
    # Kept for older callers: sexagesimal to decimal in the unit of the first field.
    return _sexagesimal(text)


def _vsp_band(name: str) -> str | None:
    raw = str(name or "").strip()
    # 2.2.17 (item 1): Sloan bands in a VSP sequence (SG/SR/SI, "g'", "Sloan g"…). The lowercase single-letter rule
    # is only a harmless fallback: VSP writes Johnson-Cousins bands in capitals (V, B, Rc, Ic, U), so a lowercase
    # u/g/r/i/z can only be Sloan.
    if raw in ("u", "g", "r", "i", "z"):
        return "S" + raw.upper()
    sloan = sloan_band_code(raw)
    if sloan:
        return sloan
    name = raw.upper()
    return {"V": "V", "B": "B", "R": "R", "RC": "R", "I": "I", "IC": "I"}.get(name)


def chart_stars(payload: dict, band: str = "V") -> list[dict]:
    """Comparison stars from an AAVSO VSP payload. 'mag' is in the requested band;
    'mags' keeps every band the chart lists (V, B, R from Rc, I from Ic; 2.2.17: Sloan as SG, SR, SI, SZ, SU)."""
    band = band.upper()
    stars = []
    for item in payload.get("photometry") or []:
        mags: dict[str, float] = {}
        for entry in item.get("bands") or []:
            key = _vsp_band(entry.get("band"))
            if key is None or entry.get("mag") is None:
                continue
            try:
                mags[key] = float(entry.get("mag"))
            except (TypeError, ValueError):
                continue
        if band not in mags and not is_sloan(band):
            continue
        # 2.2.17 (item 1, review S4): on a Sloan run a sequence star without that band is kept, with no magnitude
        # (mag None), so its AUID, label and chart still name it; its Sloan magnitude comes from APASS or Gaia.
        ra = _angle(item["ra"], hours=True)
        dec = _angle(item["dec"], hours=False)
        stars.append({
            "auid": item.get("auid", ""),
            "label": str(item.get("label", "")),
            "ra": ra,
            "dec": dec,
            "mag": mags.get(band),
            "mags": mags,
            "band": band,
        })
    return stars


def detect_sources(img: np.ndarray, max_sources: int = 80, nsigma: float = 5.0, min_sep: float = 8.0) -> list[tuple[float, float]]:
    """Star-like peaks: local maxima of a lightly smoothed image, above nsigma of the background.

    Smoothing first stops noise in a bright star's halo from turning into dozens of fake "stars".
    """
    from scipy import ndimage

    data = np.array(img, dtype=np.float32, copy=True)
    finite = np.isfinite(data)
    if not finite.any():
        return []
    med = float(np.median(data[finite]))
    data[~finite] = med
    sigma = 1.4826 * float(np.median(np.abs(data[finite] - med)))
    if sigma <= 0:
        sigma = float(np.std(data[finite])) or 1.0
    smooth = ndimage.uniform_filter(data, size=3)
    peaks = (smooth == ndimage.maximum_filter(smooth, size=9)) & (smooth > med + nsigma * sigma / 3.0)
    # Ignore a thin border, where partial stars and edge artifacts live.
    peaks[:5, :] = peaks[-5:, :] = False
    peaks[:, :5] = peaks[:, -5:] = False
    ys, xs = np.nonzero(peaks)
    if xs.size == 0:
        return []
    order = np.argsort(smooth[ys, xs])[::-1]
    picked: list[tuple[float, float]] = []
    peaks_kept: list[float] = []
    for index in order:
        x, y = float(xs[index]), float(ys[index])
        value = float(smooth[ys[index], xs[index]]) - med
        ok = True
        for (px, py), pv in zip(picked, peaks_kept):
            d2 = (x - px) ** 2 + (y - py) ** 2
            if d2 <= min_sep ** 2:
                ok = False
                break
            # Points on a bright star's diffraction spikes or halo: much fainter and close by.
            # The exclusion radius grows with how much brighter the neighbor is.
            ratio = pv / max(value, 1e-6)
            if ratio > 10 and d2 < min(80.0, 5.0 * math.sqrt(ratio)) ** 2:
                ok = False
                break
        if ok:
            picked.append((x, y))
            peaks_kept.append(value)
            if len(picked) >= max_sources:
                break
    return picked


def quick_stack(frames: list) -> np.ndarray | None:
    """2.2.9: mean of a few calibrated, debayered frames, each shifted (whole pixels) so its brightest star lands on
    the first frame's. Very short exposures (Unistellar 0.04 s) show ~10 stars per frame, too few to match a catalog;
    30 stacked frames show several times more. Field rotation over a few minutes is ignored."""
    from scipy import ndimage

    ref = None
    acc = None
    n = 0
    for img in frames:
        if img is None:
            continue
        data = np.nan_to_num(np.asarray(img, dtype=np.float32), nan=float(np.nanmedian(img)))
        sm = ndimage.uniform_filter(data, size=3)
        y, x = np.unravel_index(int(np.argmax(sm)), sm.shape)
        if ref is None:
            ref = (x, y)
            acc = data.astype(np.float64)
        else:
            if data.shape != acc.shape:
                continue
            acc += np.roll(np.roll(data, ref[1] - y, axis=0), ref[0] - x, axis=1)
        n += 1
    return (acc / n).astype(np.float32) if n else None


def match_chart(
    sources: list[tuple[float, float]],
    stars: list[dict],
    target_xy: tuple[float, float],
    ra_deg: float,
    dec_deg: float,
    scale_arcsec: float,
    tolerance_px: float = 6.0,
    scale_slop: float = 0.06,
) -> dict | None:
    """Rotate (and, if needed, mirror) the AAVSO sequence about the clicked target until it lands on detected stars.

    Also tries plate scales within +/- scale_slop, because focal length is rarely known to
    better than a few percent and that error grows toward the frame edge. Returns the
    placement with the most matched stars, ties broken by the tighter fit.
    """
    if not sources or not stars or scale_arcsec <= 0:
        return None
    cos_dec = math.cos(math.radians(dec_deg))
    east = np.array([((s["ra"] - ra_deg + 180.0) % 360.0 - 180.0) * 3600.0 * cos_dec for s in stars])
    north = np.array([(s["dec"] - dec_deg) * 3600.0 for s in stars])
    src = np.asarray(sources, dtype=float)
    tx, ty = target_xy

    def search(factors, angles, mirrors, tol):
        best = None
        for factor in factors:
            e0 = east / (scale_arcsec * factor)
            n0 = north / (scale_arcsec * factor)
            for mirrored in mirrors:
                e = -e0 if mirrored else e0
                for angle in angles:
                    theta = math.radians(angle)
                    ct, st = math.cos(theta), math.sin(theta)
                    # Image y runs down. North up, east left at angle 0.
                    x = tx - e * ct - n0 * st
                    y = ty + e * st - n0 * ct
                    dist = np.hypot(x[:, None] - src[None, :, 0], y[:, None] - src[None, :, 1])
                    nearest = dist.min(axis=1)
                    hit = nearest < tol
                    score = (int(hit.sum()), -float(nearest[hit].sum()))
                    if best is None or score > best["score"]:
                        best = {
                            "score": score, "hits": score[0], "angle": float(angle) % 360.0, "mirrored": mirrored,
                            "scale_factor": float(factor), "_xy": (x, y, hit, dist.argmin(axis=1)),
                        }
        return best

    # Coarse pass: 2 degree steps, 1 percent scale steps, generous tolerance because a
    # star 700 px out moves 24 px per 2 degrees. Fine pass: refine around the winner.
    coarse = search(
        np.arange(1.0 - scale_slop, 1.0 + scale_slop + 1e-9, 0.01),
        np.arange(0.0, 360.0, 2.0),
        (False, True),
        max(tolerance_px * 3, 18.0),
    )
    best = search(
        np.arange(coarse["scale_factor"] - 0.012, coarse["scale_factor"] + 0.012 + 1e-9, 0.002),
        np.arange(coarse["angle"] - 2.5, coarse["angle"] + 2.5 + 1e-9, 0.1),
        (coarse["mirrored"],),
        tolerance_px,
    )
    x, y, hit, idx = best.pop("_xy")
    placed = []
    for i, star in enumerate(stars):
        if hit[i]:
            placed.append((float(src[idx[i], 0]), float(src[idx[i], 1]), star))
        else:
            placed.append((float(x[i]), float(y[i]), star))
    best["placed"] = placed
    return best


# ---- Comparison-star catalogs ---------------------------------------------------------

CATALOGS = ("AAVSO sequence", "Gaia DR3", "APASS DR9", "Tycho-2")


def _band_for(band: str) -> str:
    b = str(band or "V").upper()
    if b in SLOAN_BANDS:
        return b   # 2.2.17 (item 1)
    return {"V": "V", "R": "R", "B": "B"}.get(b, "V")


def _pm_to_now(ra_deg: float, dec_deg: float, pm_ra, pm_dec, epoch: float) -> tuple[float, float]:
    try:
        pm_ra = float(pm_ra)
        pm_dec = float(pm_dec)
    except (TypeError, ValueError):
        return ra_deg, dec_deg
    if not (math.isfinite(pm_ra) and math.isfinite(pm_dec)):
        return ra_deg, dec_deg
    years = pm_epoch() - epoch
    ra_deg += pm_ra * years / (3_600_000.0 * max(math.cos(math.radians(dec_deg)), 0.01))
    dec_deg += pm_dec * years / 3_600_000.0
    return ra_deg % 360.0, dec_deg


def _position_name(prefix: str, ra_deg: float, dec_deg: float) -> str:
    """APASS has no star IDs, so name it by position: APASS J231324.1+571024."""
    ra_h = (ra_deg % 360.0) / 15.0
    h = int(ra_h)
    m = int((ra_h - h) * 60)
    s = ((ra_h - h) * 60 - m) * 60
    sign = "+" if dec_deg >= 0 else "-"
    d_abs = abs(dec_deg)
    dd = int(d_abs)
    dm = int((d_abs - dd) * 60)
    ds = int(round(((d_abs - dd) * 60 - dm) * 60))
    if ds == 60:
        ds, dm = 0, dm + 1
    return f"{prefix} J{h:02d}{m:02d}{s:04.1f}{sign}{dd:02d}{dm:02d}{ds:02d}"


def parse_vizier_tsv(text: str) -> list[dict]:
    """VizieR ASU-TSV: '#' comments, a row of column names, a units row, a dashes row, then data."""
    rows: list[dict] = []
    names = None
    in_data = False
    for line in text.splitlines():
        if not line.strip():
            if in_data:
                in_data = False
                names = None
            continue
        if line.startswith("#"):
            continue
        cells = [cell.strip() for cell in line.split("\t")]
        if names is None:
            names = cells
            continue
        if not in_data:
            if all(set(cell) <= {"-", " "} for cell in cells):
                in_data = True
            continue
        rows.append(dict(zip(names, cells)))
    return rows


class CatalogRows(list):
    """2.2.16 (review): a list of rows or stars that remembers why it may be incomplete ('' when complete), from
    VizieR's '#' comment lines (an error, a time limit, a truncated answer). Such an answer is used but not cached."""
    incomplete = ""


# "error" not after a dot or a letter: VizieR's column descriptions carry UCDs such as "stat.error" (APASS e_Vmag).
_VIZIER_ERR = r"(?<![.\w])error|time.?limit|timeout|timed out|aborted"
_VIZIER_BAD = _re.compile(_VIZIER_ERR + r"|truncat|overflow|incomplete", _re.IGNORECASE)
# 2.2.16 (round-2 review): table/column description lines are never status markers.
_VIZIER_DESCRIPTIVE = ("#Column", "#Title", "#Table", "#Name", "#Coosys", "#Explanations", "#RESOURCE")


def vizier_markers(text: str) -> list[str]:
    """2.2.16 (review): VizieR's '#' comment lines that say an answer is not complete (description lines skipped)."""
    out = []
    for line in str(text or "").splitlines():
        if line.startswith("#") and not line.startswith(_VIZIER_DESCRIPTIVE) and _VIZIER_BAD.search(line):
            out.append(line.strip()[:160])
    return out


def rows_incomplete(rows, max_rows: int | None) -> str:
    """2.2.16 (review): why a VizieR answer is not complete: an error marker always; a truncation/overflow marker only
    when fewer rows came back than were asked for (VizieR reports OVERFLOW whenever -out.max is reached)."""
    marks = list(getattr(rows, "markers", []) or [])
    if not marks:
        return ""
    errors = [m for m in marks if _re.search(_VIZIER_ERR, m, _re.IGNORECASE)]
    if errors:
        return errors[0]
    if max_rows is None or len(rows) < int(max_rows):
        return marks[0]
    return ""


def _with_incomplete(stars: list, rows, max_rows: int | None) -> list:
    out = CatalogRows(stars)
    out.incomplete = rows_incomplete(rows, max_rows)
    return out


def vizier_params(source: str, ra_deg: float, dec_deg: float, radius_deg: float, columns: list[str], sort: str,
                  max_rows: int, box_deg: tuple[float, float] | None = None, constraints: dict | None = None) -> dict:
    """2.2.14 (item 5): the VizieR ASU parameters: a circle (-c.rd), or a box (-c.bd, width along RA x height along
    Dec, degrees) when the frame's footprint is known; column constraints such as {"Gmag": "<19"}."""
    params = {
        "-source": source,
        "-c": f"{ra_deg:.6f} {dec_deg:+.6f}",
        "-out": ",".join(columns),
        "-out.max": str(int(max_rows)),
        "-sort": sort,
    }
    if box_deg:
        params["-c.bd"] = f"{box_deg[0]:.4f}x{box_deg[1]:.4f}"
    else:
        params["-c.rd"] = f"{radius_deg:.4f}"
    for column, rule in (constraints or {}).items():
        params[column] = rule
    return params


def _vizier_query(source: str, ra_deg: float, dec_deg: float, radius_deg: float, columns: list[str], sort: str,
                  max_rows: int, box_deg=None, constraints: dict | None = None, timeout: float = 60) -> list[dict]:
    import urllib.parse

    query = urllib.parse.urlencode(vizier_params(source, ra_deg, dec_deg, radius_deg, columns, sort, max_rows,
                                                 box_deg, constraints))
    errors = []
    for host in ("https://vizier.cds.unistra.fr", "https://vizier.cfa.harvard.edu"):
        try:
            text = _http_get(f"{host}/viz-bin/asu-tsv?{query}", timeout=timeout).decode("utf-8", errors="replace")
            rows = CatalogRows(parse_vizier_tsv(text))
            rows.markers = vizier_markers(text)   # 2.2.16 (review): kept so an incomplete answer is never cached
            return rows
        except Exception as exc:
            errors.append(f"{host}: {exc}")
    raise ConnectionError("VizieR request failed.\n" + "\n".join(errors))


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def fetch_tycho2(ra_deg: float, dec_deg: float, radius_deg: float, band: str = "V") -> list[dict]:
    """Tycho-2 (VizieR I/259/tyc2). Good for bright fields, roughly V < 11.
    Johnson from Tycho: V = VT - 0.090 (BT - VT), B - V = 0.850 (BT - VT)."""
    rows = _vizier_query(
        "I/259/tyc2", ra_deg, dec_deg, radius_deg,
        ["TYC1", "TYC2", "TYC3", "RAmdeg", "DEmdeg", "pmRA", "pmDE", "BTmag", "VTmag"], "VTmag", 3000,
    )
    band = _band_for(band)
    stars = []
    for row in rows:
        ra = _num(row.get("RAmdeg"))
        dec = _num(row.get("DEmdeg"))
        vt = _num(row.get("VTmag"))
        bt = _num(row.get("BTmag"))
        if ra is None or dec is None or vt is None:
            continue
        pos0 = (ra, dec, 2000.0)   # 2.2.16 (item 2): the catalog's own position and epoch, for the field cache
        ra, dec = _pm_to_now(ra, dec, row.get("pmRA"), row.get("pmDE"), 2000.0)
        mags = {}
        if bt is not None:
            v = vt - 0.090 * (bt - vt)
            mags = {"V": v, "B": v + 0.850 * (bt - vt)}
        mag = mags.get(band)
        name = f"TYC {row.get('TYC1', '')}-{row.get('TYC2', '')}-{row.get('TYC3', '')}"
        stars.append({"auid": name, "label": f"{mag:.1f}" if mag is not None else "", "ra": ra, "dec": dec,
                      "mag": mag, "mags": mags, "band": band, "catalog": "Tycho-2", "bright": vt,
                      "service": "VizieR I/259", "pm": (_num(row.get("pmRA")), _num(row.get("pmDE"))), "pos0": pos0})
    return _with_incomplete(stars, rows, 3000)   # 2.2.16 (review)


def fetch_apass9(ra_deg: float, dec_deg: float, radius_deg: float, band: str = "V", max_rows: int = 3000,
                 box_deg=None) -> list[dict]:
    """APASS DR9 (VizieR II/336/apass9). Johnson B and V, about V 10 to 17. No R band. 2.2.17: Sloan g' r' i' too
    when the band asked is Sloan.
    2.2.14 (item 5): max_rows and the frame's box (box_deg) follow the field (a Milky Way field passes 3000)."""
    band = _band_for(band)
    # 2.2.17 (item 1): a Sloan run also asks for APASS's Sloan g' r' i' (columns g'mag, r'mag, i'mag; no z' or u').
    sloan_cols = [f"{b}'mag" for b in "gri"] + [f"e_{b}'mag" for b in "gri"] if is_sloan(band) else []
    rows = _vizier_query(
        "II/336/apass9", ra_deg, dec_deg, radius_deg,
        ["RAJ2000", "DEJ2000", "Vmag", "e_Vmag", "Bmag"] + sloan_cols, "Vmag", max_rows, box_deg=box_deg,
    )
    stars = []
    for row in rows:
        try:
            ra = _angle(row.get("RAJ2000", ""), hours=" " in row.get("RAJ2000", "").strip() or ":" in row.get("RAJ2000", ""))
            dec = _angle(row.get("DEJ2000", ""), hours=False)
        except (ValueError, IndexError):
            continue
        v = _num(row.get("Vmag"))
        if v is None:
            continue
        mags = {"V": v}
        if _num(row.get("Bmag")) is not None:
            mags["B"] = _num(row.get("Bmag"))
        if sloan_cols:
            for b in "gri":
                m = _num(row.get(f"{b}'mag"))
                if m is not None:
                    mags["S" + b.upper()] = m   # 2.2.17 (item 1)
        mag = mags.get(band)
        stars.append({"auid": _position_name("APASS", ra, dec), "label": f"{mag:.1f}" if mag is not None else "",
                      "ra": ra, "dec": dec, "mag": mag, "mags": mags, "band": band, "catalog": "APASS DR9", "bright": v,
                      "service": "VizieR II/336"})
    return _with_incomplete(stars, rows, max_rows)   # 2.2.16 (review)


# 2.2.14 (item 5): how deep Gaia is asked. 2.2.13 asked the ESA archive for TOP 3000 G < 17 in a circle, which in a
# Milky Way field (HD 219134, Cas) stopped long before the frame's faint limit: only 512 Gaia stars on the 56' Bin 1
# frame, and most stars one could click had no magnitude. Asking the ESA archive itself for more does not work: its
# synchronous service aborts a deep query of a one-degree Milky Way field after about 12 s (HTTP 408; tried 14 Oct
# 2026, with and without the GSPC join, with POLYGON and CIRCLE, ordered or not). So Gaia comes in two parts, asked
# at the same time:
#   - the ESA archive as in 2.2.13 (the 3000 brightest, G < 17, with the GSPC Johnson B, V, R: the colors and the
#     best V), on the circle through the frame's corners when its plate solution is known;
#   - VizieR's copy of Gaia DR3 (I/355), deep: G < 19 in the frame's RA/Dec box (or the circle), the brightest N
#     stars, N about twice the star-like peaks on the image scaled to the query area (between 3000 and 20000;
#     20000 rows came back in a few seconds); V from G and BP-RP.
# A star in both keeps the archive's GSPC magnitudes. Live test on HD 219134's field (14 Oct 2026, a 66' x 40'
# footprint): 2.2.13's query 3000 stars to G 15.4 in 8 s; this one 11,429 stars to G 19.0 (2,938 with GSPC) in 12.5 s.
GAIA_DEEP_G = 19.0
GAIA_ROWS_MIN = 3000
GAIA_ROWS_MAX = 20000
GAIA_ESA_ROWS = 3000
GAIA_ESA_G = 17.0
GAIA_ESA_TIMEOUT_S = 25.0      # 2.2.13; the VizieR fallback then fits in what is left of CATALOG_TIMEOUT_S
GAIA_VIZIER_TIMEOUT_S = 18.0
GAIA_VIZIER_LATE_TIMEOUT_S = 60.0   # 2.2.16 (item 2): the deep half when it may finish after the solve


def count_star_peaks(img: np.ndarray, nsigma: float = 5.0) -> int:
    """2.2.14 (item 5): how many star-like peaks the image shows (local maxima of the lightly smoothed image above
    nsigma; as detect_sources, without its slow neighbor test). A rough count of the stars one could click."""
    from scipy import ndimage

    data = np.array(img, dtype=np.float32, copy=True)
    finite = np.isfinite(data)
    if not finite.any():
        return 0
    med = float(np.median(data[finite]))
    data[~finite] = med
    sigma = 1.4826 * float(np.median(np.abs(data[finite] - med)))
    if sigma <= 0:
        sigma = float(np.std(data[finite])) or 1.0
    smooth = ndimage.uniform_filter(data, size=3)
    peaks = (smooth == ndimage.maximum_filter(smooth, size=9)) & (smooth > med + nsigma * sigma / 3.0)
    peaks[:5, :] = peaks[-5:, :] = False
    peaks[:, :5] = peaks[:, -5:] = False
    return int(peaks.sum())


def gaia_row_cap(n_peaks: int | None, frame_area_deg2: float | None, query_area_deg2: float | None,
                 margin: float = 2.0) -> int:
    """2.2.14 (item 5): rows to ask Gaia for, brightest first: `margin` x the stars seen on the frame, scaled up by
    how much bigger the query area is than the frame, between GAIA_ROWS_MIN and GAIA_ROWS_MAX. Unknown counts give
    GAIA_ROWS_MAX (deep enough; the G cut still applies)."""
    try:
        n = float(n_peaks)
        ratio = float(query_area_deg2) / float(frame_area_deg2)
    except (TypeError, ValueError, ZeroDivisionError):
        return GAIA_ROWS_MAX
    if not (math.isfinite(n) and math.isfinite(ratio)) or n <= 0 or ratio <= 0:
        return GAIA_ROWS_MAX
    return int(min(GAIA_ROWS_MAX, max(GAIA_ROWS_MIN, math.ceil(margin * n * max(ratio, 1.0)))))


def polygon_area_deg2(corners) -> float:
    """Area of a small sky polygon [(ra, dec), …] in square degrees (flat-sky, around its mean Dec)."""
    pts = [(float(r), float(d)) for r, d in corners]
    if len(pts) < 3:
        return 0.0
    ra0 = pts[0][0]
    cosd = math.cos(math.radians(sum(d for _r, d in pts) / len(pts)))
    xy = [((((r - ra0 + 180.0) % 360.0) - 180.0) * cosd, d) for r, d in pts]
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]))) / 2.0


def frame_footprint(wcs: dict, saved_shape: tuple[int, int], margin: float = 0.04) -> list[tuple[float, float]] | None:
    """2.2.14 (item 5): the frame's four corners on the sky (RA, Dec, degrees) from its plate solution, pushed out
    by `margin` of the frame size (catalog positions, proper motions, a star half off the edge). None without one."""
    if not wcs:
        return None
    try:
        h, w = float(saved_shape[0]), float(saved_shape[1])
        cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
        k = 1.0 + 2.0 * margin
        xs = [cx + (x - cx) * k for x in (-0.5, w - 0.5, w - 0.5, -0.5)]
        ys = [cy + (y - cy) * k for y in (-0.5, -0.5, h - 0.5, h - 0.5)]
        ra, dec = wcs_pixel_to_sky(wcs, xs, ys)
        corners = [(float(r), float(d)) for r, d in zip(ra, dec)]
    except Exception:
        return None
    if not all(math.isfinite(r) and math.isfinite(d) for r, d in corners):
        return None
    return corners


def footprint_box(corners, ra0: float, dec0: float) -> tuple[float, float]:
    """(width along RA, height along Dec) in degrees of the RA/Dec box around (ra0, dec0) that holds the corners."""
    cosd = max(math.cos(math.radians(dec0)), 0.01)
    half_w = max(abs((((float(r) - ra0 + 180.0) % 360.0) - 180.0) * cosd) for r, _d in corners)
    half_h = max(abs(float(d) - dec0) for _r, d in corners)
    return 2.0 * half_w, 2.0 * half_h


def gaia_adql(ra_deg: float, dec_deg: float, radius_deg: float, glimit: float = 17.0, max_rows: int = 3000,
              sloan: bool = False) -> str:
    """The ESA archive query (2.2.13's, built here for testing): a circle, G < glimit, the brightest `max_rows` stars
    with their GSPC Johnson B, V, R; 2.2.14 also asks BP-RP, for V from G where there is no GSPC.
    2.2.17 (item 1): sloan=True also asks GSPC's standardised SDSS u g r i z."""
    return (
        f"SELECT TOP {int(max_rows)} g.source_id, g.ra, g.dec, g.pmra, g.pmdec, g.phot_g_mean_mag, g.bp_rp, "
        "s.v_jkc_mag, s.b_jkc_mag, s.r_jkc_mag "
        + (", s.u_sdss_mag, s.g_sdss_mag, s.r_sdss_mag, s.i_sdss_mag, s.z_sdss_mag " if sloan else "") +
        "FROM gaiadr3.gaia_source AS g "
        "LEFT OUTER JOIN gaiadr3.synthetic_photometry_gspc AS s ON s.source_id = g.source_id "
        f"WHERE 1 = CONTAINS(POINT('ICRS', g.ra, g.dec), CIRCLE('ICRS', {ra_deg:.6f}, {dec_deg:.6f}, {radius_deg:.4f})) "
        f"AND g.phot_g_mean_mag < {glimit:.2f} "
        "ORDER BY g.phot_g_mean_mag"
    )


def v_from_gaia_g(g: float | None, bp_rp: float | None) -> float | None:
    """Johnson V from Gaia G and BP-RP (Riello et al. 2021): G - V = -0.02704 + 0.01424 C - 0.2156 C^2 + 0.01426 C^3,
    good to about 0.03 mag, for -0.5 < BP-RP < 5. None outside."""
    if g is None or bp_rp is None or not (-0.5 < bp_rp < 5.0):
        return None
    c = bp_rp
    return g + 0.02704 - 0.01424 * c + 0.2156 * c ** 2 - 0.01426 * c ** 3


# 2.2.17 (item 1): Sloan magnitudes from Gaia DR3 G and BP-RP, for stars without GSPC (fainter than G 17.65, or no
# XP spectrum). Gaia DR3 documentation, section 5.5.1 (Carrasco & Bellazzini), Table 5.8 "SDSS12 relationships"
# (G - x as a polynomial in C = BP-RP, coefficients c0..c3/c4, and the fit's scatter sigma) and Table 5.10 (range of
# applicability). Above BP-RP 2.0 the G-g and G-r fits follow M giants only (Table 5.10 notes f, g), so for the
# dwarfs that make most comps they stop at 2.0. Outside the range: no magnitude.
#   band: (coefficients c0, c1, ..., sigma, (C min, C max))
GAIA_SDSS_POLY = {
    "SG": ((0.2199, -0.6365, -0.1548, 0.0064), 0.0745, (0.3, 2.0)),
    "SR": ((-0.09837, 0.08592, 0.1907, -0.1701, 0.02263), 0.03776, (0.0, 2.0)),
    "SI": ((-0.293, 0.6404, -0.09609, -0.002104), 0.04092, (0.5, 2.0)),
    "SZ": ((-0.4619, 0.8992, -0.08271, 0.005029), 0.041161, (-0.5, 4.5)),
}
GSPC_SDSS_COLUMNS = {"SU": "u_sdss_mag", "SG": "g_sdss_mag", "SR": "r_sdss_mag", "SI": "i_sdss_mag", "SZ": "z_sdss_mag"}


def sdss_to_prime(mags: dict) -> dict:
    """2.2.17 (item 1): SDSS 2.5 m ugriz (what Gaia's GSPC and the Gaia DR3 relations give) to the u'g'r'i'z' standard
    system (APASS, AAVSO SG/SR/SI), inverting the SDSS transformation (Tucker et al. 2006, AN 327, 821; SDSS DR7
    "jeg_photometric_eq_dr1"): g' = g - 0.060 ((g-r) - 0.53), r' = r - 0.035 ((r-i) - 0.21), i' = i - 0.041 ((r-i) - 0.21),
    z' = z + 0.030 ((i-z) - 0.09), u' = u. A band whose partner color is missing is left as it is (the change is a
    few hundredths, below the G and BP-RP relations' scatter). Keys SU/SG/SR/SI/SZ. The published equations use the
    primed colors; using the SDSS colors instead (the inverse to first order) differs by about 1 mmag."""
    g, r, i, z = (mags.get(k) for k in ("SG", "SR", "SI", "SZ"))
    out = dict(mags)
    if g is not None and r is not None:
        out["SG"] = g - 0.060 * ((g - r) - 0.53)
    if r is not None and i is not None:
        out["SR"] = r - 0.035 * ((r - i) - 0.21)
        out["SI"] = i - 0.041 * ((r - i) - 0.21)
    if i is not None and z is not None:
        out["SZ"] = z + 0.030 * ((i - z) - 0.09)
    return out


def sloan_from_gaia_g(g: float | None, bp_rp: float | None) -> dict:
    """2.2.17 (item 1): {band: (magnitude on the primed system, sigma)} from Gaia G and BP-RP with the Gaia DR3
    relations (GAIA_SDSS_POLY); bands outside their BP-RP range are left out."""
    if g is None or bp_rp is None or not (math.isfinite(g) and math.isfinite(bp_rp)):
        return {}
    raw, sig = {}, {}
    for band, (coef, sigma, (lo, hi)) in GAIA_SDSS_POLY.items():
        if lo < bp_rp < hi:
            raw[band] = g - sum(c * bp_rp ** k for k, c in enumerate(coef))
            sig[band] = sigma
    prime = sdss_to_prime(raw)
    return {b: (prime[b], sig[b]) for b in raw}


def gaia_sloan_note(sigma: float) -> str:
    """2.2.17 (item 1): the note a Sloan magnitude from G and BP-RP carries (screen, log, report notes)."""
    return f"from Gaia G and BP-RP, \u00b1{sigma:.2f}"


def _add_gaia_sloan(mags: dict, notes: dict, row_get, g, bp_rp) -> None:
    """2.2.17 (item 1): a Gaia star's Sloan magnitudes into mags (and their notes): GSPC's standardised SDSS where the
    archive gave it (converted to the primed system), else the G and BP-RP relations."""
    gspc = {b: _num(row_get(c)) for b, c in GSPC_SDSS_COLUMNS.items()} if row_get is not None else {}
    gspc = {b: v for b, v in gspc.items() if v is not None}
    if gspc:
        for b, v in sdss_to_prime(gspc).items():
            mags[b] = v
            notes[b] = "Gaia synthetic SDSS (GSPC)"
    for b, (v, sigma) in sloan_from_gaia_g(g, bp_rp).items():
        if b not in mags:
            mags[b] = v
            notes[b] = gaia_sloan_note(sigma)


def cap_brightest(stars: list[dict], max_rows: int) -> list[dict]:
    """2.2.14 (item 5): the brightest `max_rows` stars (by 'bright', the catalog's own broad-band magnitude)."""
    if len(stars) <= max_rows:
        return stars
    return sorted(stars, key=lambda s: (s.get("bright") is None, s.get("bright") or 0.0))[:int(max_rows)]


def catalog_depth(stars: list[dict], max_rows: int | None = None) -> dict:
    """2.2.14 (item 5): {n, faintest (its 'bright' magnitude), capped (the row cap was reached, so the list stops
    at 'faintest' rather than at the magnitude cut)}."""
    vals = [float(s["bright"]) for s in stars if s.get("bright") is not None]
    return {"n": len(stars), "faintest": max(vals) if vals else None,
            "capped": bool(max_rows) and len(stars) >= int(max_rows)}


def combine_gaia(esa: list[dict], deep: list[dict], max_rows: int) -> list[dict]:
    """2.2.14 (item 5): one Gaia list from the archive's GSPC answer and VizieR's deep one: a star in both keeps the
    archive's entry (GSPC B, V, R); the brightest `max_rows` are kept."""
    have = {s.get("auid") for s in esa}
    out = list(esa) + [s for s in deep if s.get("auid") not in have]
    return cap_brightest(out, max(int(max_rows), len(esa)))


def _gaia_esa(ra_deg: float, dec_deg: float, radius_deg: float, band: str, glimit: float, max_rows: int,
              timeout: float) -> list[dict]:
    import urllib.parse

    adql = gaia_adql(ra_deg, dec_deg, radius_deg, glimit, max_rows, sloan=is_sloan(band))   # 2.2.17 (item 1)
    query = urllib.parse.urlencode({"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": adql})
    text = _http_get("https://gea.esac.esa.int/tap-server/tap/sync?" + query, timeout=timeout).decode("utf-8",
                                                                                                     errors="replace")
    return parse_gaia_csv(text, band)


def fetch_gaia_dr3(ra_deg: float, dec_deg: float, radius_deg: float, band: str = "V", glimit: float = 17.0,
                   max_rows: int = 3000, polygon=None, status: dict | None = None, wait_s: float | None = None,
                   on_late=None, cache_key: dict | None = None) -> list[dict]:
    """Gaia DR3 from the ESA archive, with Johnson-Kron-Cousins synthetic photometry (GSPC) where it exists.
    Positions are epoch 2016.0 and are moved to now with Gaia proper motions.
    2.2.14 (item 5): asked deeper than the archive's 3000 brightest (glimit > 17, more rows, or the frame's
    footprint `polygon`), the archive's GSPC part and VizieR's deep part are fetched at the same time and combined
    (see GAIA_DEEP_G); either one alone still answers. Stars without GSPC get V from G and BP-RP (no B, R: colors
    only from GSPC, item 1).
    2.2.16 (item 2): wait_s caps how long the deep half is waited for (the solve's own deadline); with on_late, a
    deep half still running then is not lost: it gets its own longer timeout, finishes in the background, and
    on_late(combined stars or None, error text or "") is called when it ends. The answer returned meanwhile is the
    archive half alone and status["deep"] says "still running". cache_key (the field cache's key for this request):
    each half is read from / saved to the cache on its own, so a half that answered is kept even when the other
    did not (the ESA archive sometimes refuses a big field), and only the missing half is asked next time."""
    import threading
    import time as _time

    band = _band_for(band)
    deep = glimit > GAIA_ESA_G or max_rows > GAIA_ESA_ROWS or bool(polygon)
    if not deep:
        try:
            return _gaia_esa(ra_deg, dec_deg, radius_deg, band, glimit, max_rows, GAIA_ESA_TIMEOUT_S)
        except Exception as exc:
            # ESA archive slow or down (25 s, 2.2.13: so the fallback still fits in the solve's 45 s): same stars from
            # the VizieR copy of Gaia DR3.
            try:
                return fetch_gaia_dr3_vizier(ra_deg, dec_deg, radius_deg, band, glimit, max_rows)
            except Exception as exc2:
                raise ConnectionError(f"Gaia archive: {exc}\nVizieR Gaia DR3: {exc2}") from exc2
    # The archive part on the circle through the frame's corners (from the frame's center) when they are known.
    esa_radius = (max(sky_separation_arcsec(ra_deg, dec_deg, r, d) for r, d in polygon) / 3600.0
                  if polygon else radius_deg)
    box = footprint_box(polygon, ra_deg, dec_deg) if polygon else None
    results: dict = {}

    def run(key, fn):
        try:
            results[key] = ("ok", fn())
        except Exception as exc:
            results[key] = ("error", exc)

    # 2.2.16 (item 2): with a background finish (on_late) the deep half gets its own, longer timeout; on 15 Oct the
    # 18 s one (per VizieR host) most likely cut it off and only the archive's 3000 stars to G 17.2 came back.
    deep_timeout = GAIA_VIZIER_LATE_TIMEOUT_S if on_late is not None else GAIA_VIZIER_TIMEOUT_S
    times: dict = {}
    t0 = _time.monotonic()

    def run_timed(key, fn):
        run(key, fn)
        times[key] = _time.monotonic() - t0

    # The archive half does not depend on the deep cut or the row cap.
    half_keys = ({"esa": dict({k: v for k, v in cache_key.items() if k not in ("max_rows", "glimit")}, half="esa"),
                  "deep": dict(cache_key, half="deep")} if cache_key else {})
    from_cache = {}
    for h, hk in half_keys.items():
        hit = catalog_cache_load(hk)
        if hit is not None:
            results[h] = ("ok", hit)
            times[h] = 0.0
            from_cache[h] = True
    jobs = {"esa": threading.Thread(target=run_timed, daemon=True, args=("esa", lambda: _gaia_esa(
                ra_deg, dec_deg, esa_radius, band, min(glimit, GAIA_ESA_G), GAIA_ESA_ROWS, GAIA_ESA_TIMEOUT_S))),
            "deep": threading.Thread(target=run_timed, daemon=True, args=("deep", lambda: fetch_gaia_dr3_vizier(
                ra_deg, dec_deg, radius_deg, band, glimit, max_rows, box, deep=True, timeout=deep_timeout)))}
    jobs = {h: t for h, t in jobs.items() if h not in from_cache}
    for t in jobs.values():
        t.start()
    limit = GAIA_ESA_TIMEOUT_S + 2.0 * GAIA_VIZIER_TIMEOUT_S if wait_s is None else max(float(wait_s), 1.0)
    for t in jobs.values():
        t.join(max(0.0, limit - (_time.monotonic() - t0)))
    saved_halves = {}
    for h in jobs:
        got = results.get(h)
        if h in half_keys and got and got[0] == "ok" and got[1] and not getattr(got[1], "incomplete", ""):
            saved_halves[h] = catalog_cache_save(half_keys[h], got[1])   # 2.2.16 (item 2): each half on its own
    esa = results.get("esa", ("error", TimeoutError("no answer")))
    viz_now = results.get("deep")   # 2.2.16 (review): read once, then decide whether it is still running
    pending = viz_now is None and "deep" in jobs and jobs["deep"].is_alive() and on_late is not None
    viz = viz_now or ("error", TimeoutError("no answer"))
    if status is not None:
        # 2.2.14 (review S3): which half answered, so the solve can say what is missing tonight.
        status.update({"esa": "ok" if esa[0] == "ok" else str(esa[1]).splitlines()[0][:120],
                       "deep": "ok" if viz[0] == "ok" else ("still running" if pending
                                                            else str(viz[1]).splitlines()[0][:120]),
                       "esa_faintest": max((s_.get("bright") or 0.0 for s_ in esa[1]), default=None) if esa[0] == "ok"
                       else None,
                       # 2.2.16 (item 2): per-half timings and counts for the log
                       "esa_s": times.get("esa"), "deep_s": times.get("deep"),
                       "esa_n": len(esa[1]) if esa[0] == "ok" else None,
                       "deep_n": len(viz[1]) if viz[0] == "ok" else None,
                       "esa_cached": bool(from_cache.get("esa")), "deep_cached": bool(from_cache.get("deep")),
                       "cache_failed": [h for h, ok in saved_halves.items() if not ok],
                       "incomplete": {h: getattr(results[h][1], "incomplete", "") for h in ("esa", "deep")
                                      if results.get(h, ("",))[0] == "ok" and getattr(results[h][1], "incomplete", "")}})
    if pending:
        esa_now = esa

        def finish():
            # 2.2.16 (item 2): the deep half, past the solve's deadline.
            jobs["deep"].join(deep_timeout * 2.0 + 5.0)
            late = results.get("deep", ("error", TimeoutError(f"no answer within {_time.monotonic() - t0:.0f} s")))
            saved = None   # None: no cache in use; True / False: whether it was written
            incomplete = getattr(late[1], "incomplete", "") if late[0] == "ok" else ""
            if late[0] == "ok" and late[1] and "deep" in half_keys and not incomplete:
                saved = catalog_cache_save(half_keys["deep"], late[1])
            if status is not None:
                status["deep_late"] = "ok" if late[0] == "ok" else str(late[1]).splitlines()[0][:120]
                status["deep_s"] = times.get("deep")
            # 2.2.16 (review): what really happened: cached or not, and the archive half's state, said apart.
            extra = {"cached": saved, "incomplete": incomplete,
                     "archive_missing": "" if esa_now[0] == "ok" else str(esa_now[1]).splitlines()[0][:160]}
            try:
                if late[0] == "ok":
                    on_late(combine_gaia(esa_now[1] if esa_now[0] == "ok" else [], late[1], max_rows), "", extra)
                else:
                    on_late(None, str(late[1]).splitlines()[0][:200], extra)
            except Exception:
                pass

        threading.Thread(target=finish, daemon=True).start()
    if esa[0] != "ok" and viz[0] != "ok":
        raise ConnectionError(f"Gaia archive: {esa[1]}\nVizieR Gaia DR3: {viz[1]}"
                              + (" (still running in the background)" if pending else ""))
    return combine_gaia(esa[1] if esa[0] == "ok" else [], viz[1] if viz[0] == "ok" else [], max_rows)


def parse_gaia_csv(text: str, band: str = "V") -> list[dict]:
    """The ESA archive's CSV answer as SHOBS-P star records (2.2.14: split out of fetch_gaia_dr3 for testing)."""
    import csv
    import io

    band = _band_for(band)
    column = {"V": "v_jkc_mag", "B": "b_jkc_mag", "R": "r_jkc_mag"}.get(band)
    sloan = is_sloan(band)   # 2.2.17 (item 1)
    stars = []
    for row in csv.DictReader(io.StringIO(text)):
        ra = _num(row.get("ra"))
        dec = _num(row.get("dec"))
        g = _num(row.get("phot_g_mean_mag"))
        if ra is None or dec is None or g is None:
            continue
        pos0 = (ra, dec, 2016.0)   # 2.2.16 (item 2)
        ra, dec = _pm_to_now(ra, dec, row.get("pmra"), row.get("pmdec"), 2016.0)
        mags = {k: _num(row.get(c)) for k, c in (("V", "v_jkc_mag"), ("B", "b_jkc_mag"), ("R", "r_jkc_mag"))}
        mags = {k: v for k, v in mags.items() if v is not None}
        v_note = ""
        if "V" not in mags:
            # 2.2.14 (item 5): no synthetic photometry (fainter than G 17.65, or no XP spectrum): V from G and BP-RP.
            v_g = v_from_gaia_g(g, _num(row.get("bp_rp")))
            if v_g is not None:
                mags = {"V": v_g}
                v_note = " (V from G)"
        notes: dict = {}
        if sloan:
            _add_gaia_sloan(mags, notes, row.get, g, _num(row.get("bp_rp")))   # 2.2.17 (item 1)
        mag = mags.get(band) if band == "V" or sloan else _num(row.get(column))
        star = {"auid": f"Gaia DR3 {row.get('source_id', '')}", "label": f"{mag:.1f}" if mag is not None else "",
                "ra": ra, "dec": dec, "mag": mag, "mags": mags, "band": band, "catalog": "Gaia DR3", "bright": g,
                "service": "ESA Gaia archive" + v_note, "pm": (_num(row.get("pmra")), _num(row.get("pmdec"))),
                "pos0": pos0}
        if notes:
            star["mag_notes"] = notes
        stars.append(star)
    if not stars and "error" in text.lower():
        raise ValueError("Gaia archive: " + text.strip()[:300])
    return stars


def fetch_gaia_dr3_vizier(ra_deg: float, dec_deg: float, radius_deg: float, band: str = "V", glimit: float = 17.0,
                          max_rows: int = 3000, box_deg=None, deep: bool = False,
                          timeout: float = GAIA_VIZIER_TIMEOUT_S) -> list[dict]:
    """Gaia DR3 from VizieR (I/355/gaiadr3). V comes from G and BP-RP with the Gaia DR3
    photometric relation (Riello et al. 2021): G - V = -0.02704 + 0.01424 C - 0.2156 C^2 + 0.01426 C^3,
    good to about 0.03 mag. Only V is given this way; B and R are left empty.
    2.2.14 (item 5): the G cut is asked of VizieR (not only applied here), the frame's box when known, and the row
    cap, brightest first. deep=True: the deep half of fetch_gaia_dr3 (not a fallback)."""
    rows = _vizier_query(
        "I/355/gaiadr3", ra_deg, dec_deg, radius_deg,
        ["Source", "RA_ICRS", "DE_ICRS", "pmRA", "pmDE", "Gmag", "BP-RP"], "Gmag", max_rows,
        box_deg=box_deg, constraints={"Gmag": f"<{glimit:.2f}"}, timeout=timeout,
    )
    return _with_incomplete(parse_gaia_vizier_rows(rows, band, glimit, max_rows,
                                                   "VizieR I/355 (V from G and BP-RP)" if deep else
                                                   "VizieR I/355 (ESA archive unavailable; V from G only)"),
                            rows, max_rows)   # 2.2.16 (review)


def parse_gaia_vizier_rows(rows: list[dict], band: str = "V", glimit: float = 17.0, max_rows: int = 3000,
                           service: str = "VizieR I/355 (ESA archive unavailable; V from G only)") -> list[dict]:
    band = _band_for(band)
    stars = []
    for row in rows:
        ra = _num(row.get("RA_ICRS"))
        dec = _num(row.get("DE_ICRS"))
        g = _num(row.get("Gmag"))
        if ra is None or dec is None or g is None or g > glimit:
            continue
        pos0 = (ra, dec, 2016.0)   # 2.2.16 (item 2)
        ra, dec = _pm_to_now(ra, dec, row.get("pmRA"), row.get("pmDE"), 2016.0)
        v_from_g = v_from_gaia_g(g, _num(row.get("BP-RP")))
        mags = {"V": v_from_g} if v_from_g is not None else {}
        notes: dict = {}
        if is_sloan(band):
            _add_gaia_sloan(mags, notes, None, g, _num(row.get("BP-RP")))   # 2.2.17 (item 1)
        mag = v_from_g if band == "V" else mags.get(band) if is_sloan(band) else None
        star = {"auid": f"Gaia DR3 {row.get('Source', '')}", "label": f"{mag:.1f}" if mag is not None else "",
                "ra": ra, "dec": dec, "mag": mag, "mags": mags,
                "band": band, "catalog": "Gaia DR3", "bright": g,
                "service": service,
                "pm": (_num(row.get("pmRA")), _num(row.get("pmDE"))), "pos0": pos0}
        if notes:
            star["mag_notes"] = notes
        stars.append(star)
    return cap_brightest(stars, max_rows)


def fetch_comparison_stars(catalog: str, ra_hours: float, dec_deg: float, fov_arcmin: float, band: str = "V",
                           depth: dict | None = None) -> tuple[list[dict], str]:
    """Stars for chart matching and click-to-fill. Returns (stars, chart_id). chart_id is '' for non-AAVSO catalogs.
    2.2.14 (item 5): depth (from field_depth) sets Gaia's G cut and row cap and the frame's footprint for Gaia and
    APASS; without it the 2.2.13 query is made."""
    ra_deg = ra_hours * 15.0
    radius_deg = fov_arcmin / 60.0 / 2.0 * 1.15
    depth = depth or {}
    polygon = depth.get("polygon")
    box = footprint_box(polygon, ra_deg, dec_deg) if polygon else None
    if catalog == "Gaia DR3":
        return fetch_gaia_dr3(ra_deg, dec_deg, radius_deg, band, float(depth.get("glimit", 17.0)),
                              int(depth.get("max_rows", 3000)), polygon, depth.get("status"),
                              depth.get("wait_s"), depth.get("on_late"), depth.get("cache_key")), ""   # 2.2.16 (item 2)
    if catalog == "APASS DR9":
        return fetch_apass9(ra_deg, dec_deg, radius_deg, band, int(depth.get("max_rows", 3000)), box), ""
    if catalog == "Tycho-2":
        return fetch_tycho2(ra_deg, dec_deg, radius_deg, band), ""
    payload = fetch_aavso_chart(ra_hours, dec_deg, fov_arcmin, 16)
    stars = chart_stars(payload, band)
    for star in stars:
        star["catalog"] = "AAVSO"
        star["bright"] = star["mag"]
        star["service"] = "AAVSO VSP"
    return stars, str(payload.get("chartid") or "")


# ---- 2.2.13: one star list from every catalog ---------------------------------------------------------------
# Which catalog a star's name and magnitudes come from, best first (John, 13 Oct). Names: Tycho, then APASS, then
# Gaia, so the names already in John's series stay the same; an AAVSO sequence star keeps its AUID and chart label
# alongside. Magnitudes: the AAVSO sequence (vetted, what AAVSO reports expect), then APASS (Johnson B, V), then
# Gaia's Johnson-Kron-Cousins synthetic photometry, then Tycho converted to Johnson.
NAME_ORDER = ("Tycho-2", "APASS DR9", "Gaia DR3", "AAVSO sequence")
MAG_ORDER = ("AAVSO sequence", "APASS DR9", "Gaia DR3", "Tycho-2")
MERGE_ARCSEC = 3.0


def _pm_back(star: dict, to_year: float, now_year: float | None = None):
    """A PM-corrected (now-epoch) catalog position moved back to another epoch, or None without a proper motion."""
    pm = star.get("pm") or (None, None)
    try:
        pmra, pmde = float(pm[0]), float(pm[1])
    except (TypeError, ValueError, IndexError):
        return None
    if not (math.isfinite(pmra) and math.isfinite(pmde)):
        return None
    if now_year is None:
        return None
    years = to_year - now_year
    dec = star["dec"] + pmde * years / 3_600_000.0
    ra = star["ra"] + pmra * years / (3_600_000.0 * max(math.cos(math.radians(star["dec"])), 0.01))
    return ra % 360.0, dec


def merge_catalogs(by_catalog: dict, band: str = "V", tol_arcsec: float = MERGE_ARCSEC) -> list[dict]:
    """One entry per star from several catalogs' star lists ({catalog: [star, …]}), matched by sky position.

    Gaia (complete, with proper motions) is the backbone; Tycho is already moved to the epoch; AAVSO and APASS
    positions are J2000-ish without proper motion, so they are matched against each star's position now AND at
    J2000 (HD 219134 has moved ~55" since 2000). A match also needs the V magnitudes to agree within 1 mag when both
    are known, so a faint Gaia neighbor never takes a bright star's name. Each entry keeps the keys the rest of
    SHOBS-P reads (auid, label, ra, dec, mag, mags, band, catalog, bright, service) plus: sources {catalog: that
    catalog's own entry}, aka (every other name), seq (AAVSO sequence label, AUID, chart) and mag_source per band."""
    band = _band_for(band)
    merged: list[dict] = []
    order = [c for c in ("Gaia DR3", "Tycho-2", "APASS DR9", "AAVSO sequence") if c in by_catalog]
    order += [c for c in by_catalog if c not in order]
    cells: dict = {}
    cell = 10.0 / 3600.0
    every = [st for c in order for st in (by_catalog.get(c) or []) if st.get("ra") is not None and st.get("dec") is not None]
    if not every:
        return []
    # The search grid uses ONE reference RA and cos(Dec) for the whole field (review 2.2.13: a per-star cos(Dec)
    # at RA ~350 deg moved a star a cell or two for a few arcsec of Dec, so true pairs were never compared), with RA
    # unwrapped around the field center (fields across 0h). The match itself uses the exact separation.
    ra0 = float(every[0]["ra"])
    dec0 = float(np.median([float(st["dec"]) for st in every]))
    cos0 = max(math.cos(math.radians(dec0)), 0.05)
    try:
        epoch_now = pm_epoch() if any(st.get("pm") for st in every) else None
    except Exception:
        epoch_now = None

    def key(ra, dec):
        dra = ((ra - ra0 + 180.0) % 360.0) - 180.0
        return (int(math.floor(dec / cell)), int(math.floor(dra * cos0 / cell)))

    def add_to_grid(entry, ra, dec):
        cells.setdefault(key(ra, dec), []).append(entry)

    def nearby(ra, dec):
        ky, kx = key(ra, dec)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                yield from cells.get((ky + dy, kx + dx), [])

    for cat in order:
        j2000 = cat in ("AAVSO sequence", "APASS DR9")
        taken = set()
        for st in by_catalog.get(cat) or []:
            if st.get("ra") is None or st.get("dec") is None:
                continue
            st = dict(st)
            st["catalog"] = cat
            v_new = (st.get("mags") or {}).get("V")
            best = None
            for entry in nearby(st["ra"], st["dec"]):
                if cat in entry["sources"] or id(entry) in taken:
                    continue
                d = sky_separation_arcsec(st["ra"], st["dec"], entry["ra"], entry["dec"])
                if j2000 and entry.get("_pos2000"):
                    d = min(d, sky_separation_arcsec(st["ra"], st["dec"], *entry["_pos2000"]))
                if d > tol_arcsec:
                    continue
                v_old = (entry.get("mags") or {}).get("V")
                if v_new is not None and v_old is not None and abs(v_new - v_old) > 1.0:
                    continue
                if best is None or d < best[0]:
                    best = (d, entry)
            if best is None:
                entry = {"ra": st["ra"], "dec": st["dec"], "sources": {}, "bright": st.get("bright"), "mags": {}}
                p2000 = _pm_back(st, 2000.0, epoch_now)
                if p2000:
                    entry["_pos2000"] = p2000
                    entry["pm"] = st.get("pm")
                merged.append(entry)
                add_to_grid(entry, st["ra"], st["dec"])
                if p2000:
                    add_to_grid(entry, *p2000)
            else:
                entry = best[1]
            taken.add(id(entry))
            entry["sources"][cat] = st
            if not entry.get("mags", {}).get("V") and v_new is not None:
                entry.setdefault("mags", {})["V"] = v_new   # for the magnitude test of later catalogs
    out = []
    for entry in merged:
        src = entry["sources"]
        names = [str(src[c].get("auid") or "") for c in NAME_ORDER if c in src and src[c].get("auid")]
        mags, mag_src = {}, {}
        mag_notes = {}
        for b in ("V", "B", "R", "I") + (SLOAN_BANDS if is_sloan(band) else ()):   # 2.2.17 (item 1)
            for c in MAG_ORDER:
                m = (src.get(c, {}).get("mags") or {}).get(b)
                if m is not None and math.isfinite(float(m)):
                    mags[b], mag_src[b] = float(m), c
                    note = (src[c].get("mag_notes") or {}).get(b)
                    if note:
                        mag_notes[b] = note
                    break
        seq = None
        if "AAVSO sequence" in src:
            a = src["AAVSO sequence"]
            seq = {"label": str(a.get("label") or ""), "auid": str(a.get("auid") or ""),
                   "chartid": str(a.get("chartid") or "")}
        bright = next((src[c].get("bright") for c in ("Gaia DR3", "Tycho-2", "APASS DR9", "AAVSO sequence")
                       if c in src and src[c].get("bright") is not None), None)
        mag = mags.get(band)
        catalog = mag_src.get(band) or next(iter(src))
        star = {"auid": names[0] if names else "", "aka": names[1:], "ra": entry["ra"], "dec": entry["dec"],
                "mag": mag, "mags": mags, "mag_source": mag_src, "band": band, "catalog": catalog,
                "bright": bright, "service": str(src[catalog].get("service") or catalog),
                "sources": src, "seq": seq,
                "label": (seq["label"] if seq and seq["label"] else (f"{mag:.1f}" if mag is not None else ""))}
        if entry.get("pm"):
            star["pm"] = entry["pm"]
        if mag_notes:
            star["mag_notes"] = mag_notes   # 2.2.17 (item 1): e.g. a Sloan magnitude from Gaia G and BP-RP
        # 2.2.14 (item 1): every catalog's own magnitudes, and the colors taken from ONE catalog each
        # ({"bv": [B-V, catalog], "vr": [V-R, catalog]}); "mags" stays the best magnitude per band (MAG_ORDER).
        star["cat_mags"] = {c: dict(src[c].get("mags") or {}) for c in src}
        star["colors"] = star_colors(star)
        out.append(star)
    return out


def star_names(star: dict) -> list[str]:
    """Every name a (merged) catalog star answers to: its ID, other catalogs' IDs, the AAVSO AUID and label."""
    names = [str(star.get("auid") or "")] + [str(n) for n in star.get("aka") or []]
    seq = star.get("seq") or {}
    names += [seq.get("auid", ""), seq.get("label", "")]
    if not seq:
        names.append(str(star.get("label") or ""))
    return [n for n in names if n]


CATALOG_TIMEOUT_S = 45.0   # 2.2.13: a catalog slower than this is left out of this solve (Solve again retries)


def field_depth(n_peaks: int | None, frame_shape, scale_arcsec: float | None, fov_arcmin: float,
                polygon=None, glimit: float = GAIA_DEEP_G) -> dict:
    """2.2.14 (item 5): how deep to ask the catalogs for one frame: {glimit, max_rows, polygon}. The query area is
    the RA/Dec box around the frame's footprint (polygon, from a plate solution; what VizieR is asked for) or the
    circle fetch_comparison_stars asks for."""
    if polygon:
        ra_c = float(np.degrees(np.arctan2(np.mean(np.sin(np.radians([r for r, _d in polygon]))),
                                           np.mean(np.cos(np.radians([r for r, _d in polygon])))))) % 360.0
        dec_c = float(np.mean([d for _r, d in polygon]))
        bw, bh = footprint_box(polygon, ra_c, dec_c)
        query_area = bw * bh
    else:
        query_area = math.pi * (fov_arcmin / 60.0 / 2.0 * 1.15) ** 2
    try:
        h, w = frame_shape
        frame_area = float(h) * float(w) * (float(scale_arcsec) / 3600.0) ** 2
    except (TypeError, ValueError):
        frame_area = None
    out = {"glimit": float(glimit), "max_rows": gaia_row_cap(n_peaks, frame_area, query_area)}
    if polygon:
        out["polygon"] = [(float(r), float(d)) for r, d in polygon]
    return out


# ---- 2.2.16 (item 2): per-field catalog cache --------------------------------------------------------------
# A series comes back to the same field night after night; its catalog answers are kept on disk for
# CATALOG_CACHE_DAYS. Each entry is ONE catalog's answer to ONE request, stored with every star's own catalog
# position and epoch ("pos0") and proper motion, so the night's epoch (PM_EPOCH_YEAR) is applied again on reading:
# a hit gives the same star list as asking the server again. To make tonight's request the same as last night's, the
# request is put on a grid while the cache is in use: the center on a CATALOG_CACHE_GRID_DEG grid (the field and box
# widened by the shift, so the frame is still covered), sizes rounded up, Gaia's row cap up to the next CACHE_ROWS
# step. A larger or deeper request is another key, so it is fetched. Only real network fetches are cached (not the
# test fakes); SHOBS_P_NO_CATALOG_CACHE=1 turns it off.
CATALOG_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".shobs_p_cache", "catalogs")
CATALOG_CACHE_DAYS = 30.0
CATALOG_CACHE_GRID_DEG = 0.05
CATALOG_CACHE_VERSION = 1
CACHE_ROWS = (3000, 4000, 5000, 6000, 8000, 10000, 12000, 16000, 20000, 24000, 30000)
CATALOG_SHORT = {"AAVSO sequence": "AAVSO", "APASS DR9": "APASS", "Gaia DR3": "Gaia", "Tycho-2": "Tycho"}
CATALOG_CACHE_FORCE = None   # tests: True / False overrides the "real network fetch only" check


def catalog_cache_active() -> bool:
    """2.2.16 (item 2): cache only real network answers (a test that swaps in fake fetches never reads or writes it)."""
    if os.environ.get("SHOBS_P_NO_CATALOG_CACHE"):
        return False
    if CATALOG_CACHE_FORCE is not None:
        return bool(CATALOG_CACHE_FORCE)
    real = _REAL_FETCHERS
    return bool(real) and all(globals().get(name) is fn for name, fn in real.items())


def _ceil_step(v: float, step: float) -> float:
    return round(math.ceil(float(v) / step - 1e-9) * step, 6)


def canonical_field_request(ra_hours: float, dec_deg: float, fov_arcmin: float, depth: dict | None = None):
    """2.2.16 (item 2): the request on the cache grid: (ra_hours, dec_deg, fov_arcmin, depth). The center moves to
    the nearest grid point; the field (and the footprint box) grows by twice the largest such shift, so it still
    holds the frame; Gaia/APASS row caps go up to the next CACHE_ROWS step. The footprint becomes the RA/Dec box's corners."""
    step = CATALOG_CACHE_GRID_DEG
    dec_c = max(-90.0, min(90.0, round(round(float(dec_deg) / step) * step, 6)))
    ra_step = step / max(math.cos(math.radians(dec_c)), 0.05)
    ra_c = round((round(float(ra_hours) * 15.0 / ra_step) * ra_step) % 360.0, 6)
    # The widening uses the largest shift a grid cell allows (not tonight's), so the same frame gives the same
    # request wherever in the cell it points.
    shift_deg = math.hypot(step / 2.0, step / 2.0)
    fov_c = _ceil_step(float(fov_arcmin) + 2.0 * shift_deg * 60.0, 1.0)
    out_depth = None
    if depth is not None:
        out_depth = {k: v for k, v in depth.items() if k not in ("polygon", "max_rows", "glimit")}
        if "glimit" in depth:
            out_depth["glimit"] = round(float(depth["glimit"]), 2)
        # 2.2.16 (review): the widened area gets proportionally more rows, so the frame itself keeps as many stars
        # as the request before widening would have given it.
        area_ratio = (fov_c / max(float(fov_arcmin), 1e-6)) ** 2
        if depth.get("polygon"):
            # The frame's own box (around tonight's center), widened by the largest shift, rounded up.
            bw0, bh0 = footprint_box(depth["polygon"], float(ra_hours) * 15.0, float(dec_deg))
            bw, bh = _ceil_step(bw0 + 2.0 * shift_deg, step), _ceil_step(bh0 + 2.0 * shift_deg, step)
            area_ratio = (bw * bh) / max(bw0 * bh0, 1e-9)
            cosd = max(math.cos(math.radians(dec_c)), 0.01)
            hw, hh = bw / 2.0 / cosd, bh / 2.0
            out_depth["polygon"] = [((ra_c - hw) % 360.0, dec_c - hh), ((ra_c + hw) % 360.0, dec_c - hh),
                                    ((ra_c + hw) % 360.0, dec_c + hh), ((ra_c - hw) % 360.0, dec_c + hh)]
            out_depth["_box"] = (bw, bh)
        if "max_rows" in depth:
            rows = int(math.ceil(int(depth["max_rows"]) * max(area_ratio, 1.0)))
            out_depth["max_rows"] = next((r for r in CACHE_ROWS if r >= rows), CACHE_ROWS[-1])
    return ra_c / 15.0, dec_c, fov_c, out_depth


def catalog_cache_key(catalog: str, ra_hours: float, dec_deg: float, fov_arcmin: float, band: str,
                      depth: dict | None) -> dict:
    """2.2.16 (item 2): what one catalog's answer depends on (after canonical_field_request)."""
    key = {"v": CATALOG_CACHE_VERSION, "catalog": catalog, "ra": round(float(ra_hours) * 15.0, 5),
           "dec": round(float(dec_deg), 5), "fov": round(float(fov_arcmin), 2), "band": _band_for(band)}
    depth = depth or {}
    if catalog in ("Gaia DR3", "APASS DR9") and depth:
        key["max_rows"] = int(depth.get("max_rows", 3000))
        if depth.get("_box"):
            key["box"] = [round(float(x), 4) for x in depth["_box"]]
    if catalog == "Gaia DR3" and depth:
        key["glimit"] = round(float(depth.get("glimit", 17.0)), 2)
    return key


def _cache_path(key: dict) -> str:
    import hashlib

    text = json.dumps(key, sort_keys=True)
    slug = CATALOG_SHORT.get(key.get("catalog"), "cat")
    return os.path.join(CATALOG_CACHE_DIR, f"{slug}_{hashlib.sha1(text.encode()).hexdigest()[:20]}.json")


CATALOG_CACHE_ERRORS: list = []   # 2.2.16 (review): save failures, logged once by the next fetch_field_catalogs


def _json_safe(v):
    """2.2.16 (review): numpy scalars to Python numbers, NaN / inf to None, tuples to lists (JSON, allow_nan=False)."""
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _replace_retry(src: str, dst: str, tries: int = 6, pause: float = 0.15) -> None:
    """2.2.16 (review): os.replace, retried briefly on PermissionError (Windows: the target or a file in a folder is
    open in another thread, an antivirus scan or the search indexer right after writing)."""
    import time as _time

    for k in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if k == tries - 1:
                raise
            _time.sleep(pause * (k + 1))


def catalog_cache_save(key: dict, stars: list[dict], now: float | None = None) -> bool:
    """2.2.16 (item 2): one catalog's answer, written atomically (.part, then os.replace). False on any failure (the
    reason goes to CATALOG_CACHE_ERRORS for the log)."""
    import time as _time

    path = _cache_path(key)
    tmp = f"{path}.{os.getpid()}.{threading_ident()}.part"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rows = [_json_safe({k: v for k, v in st.items() if k != "status"}) for st in stars]
        payload = {"key": _json_safe(key), "created": float(now if now is not None else _time.time()), "stars": rows}
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, allow_nan=False)
        _replace_retry(tmp, path)
        return True
    except Exception as exc:
        CATALOG_CACHE_ERRORS.append(f"{key.get('catalog', '?')}{' ' + key['half'] if key.get('half') else ''}: "
                                    f"{type(exc).__name__}: {exc}"[:200])
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False


def threading_ident() -> int:
    import threading
    return threading.get_ident()


def catalog_cache_load(key: dict, now: float | None = None) -> list[dict] | None:
    """2.2.16 (item 2): the cached answer for this exact request, younger than CATALOG_CACHE_DAYS, with every
    proper motion applied again for tonight's epoch (pm_epoch()); None when missing, old, or unreadable."""
    import time as _time

    path = _cache_path(key)
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict) or payload.get("key") != json.loads(json.dumps(key)):
            return None
        age = float(now if now is not None else _time.time()) - float(payload["created"])
        if not (0.0 <= age <= CATALOG_CACHE_DAYS * 86400.0):
            return None
        rows = payload["stars"]
        if not isinstance(rows, list) or not rows:
            return None
        stars = []
        for row in rows:
            st = dict(row)
            if st.get("pm") is not None:
                st["pm"] = tuple(st["pm"])
            if st.get("pos0") is not None:
                ra0, dec0, ep0 = (float(x) for x in st["pos0"])
                st["pos0"] = (ra0, dec0, ep0)
                pm = st.get("pm") or (None, None)
                st["ra"], st["dec"] = _pm_to_now(ra0, dec0, pm[0], pm[1], ep0)
            if st.get("ra") is None or st.get("dec") is None:
                return None
            stars.append(st)
        return stars
    except Exception:
        return None


def catalog_cache_prune(now: float | None = None) -> int:
    """2.2.16 (item 2): delete cache files older than CATALOG_CACHE_DAYS (and stale .part files)."""
    import time as _time

    now = float(now if now is not None else _time.time())
    n = 0
    try:
        names = os.listdir(CATALOG_CACHE_DIR)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(CATALOG_CACHE_DIR, name)
        try:
            if now - os.path.getmtime(path) > CATALOG_CACHE_DAYS * 86400.0 or (
                    name.endswith(".part") and now - os.path.getmtime(path) > 3600.0):
                os.remove(path)
                n += 1
        except OSError:
            pass
    return n


def _cacheable(catalog: str, stars: list[dict], status: dict | None) -> bool:
    """2.2.16 (item 2): only a complete answer is kept: never Gaia with a half missing or the VizieR fallback."""
    if not stars or getattr(stars, "incomplete", ""):
        return False   # 2.2.16 (review): VizieR said the answer was cut short
    if catalog == "Gaia DR3":
        st = status or {}
        if st and (st.get("esa") != "ok" or st.get("deep") != "ok"):
            return False
        if any("unavailable" in str(s.get("service") or "") for s in stars):
            return False
    return True


def fetch_field_catalogs(ra_hours: float, dec_deg: float, fov_arcmin: float, band: str = "V",
                         catalogs=None, timeout: float = CATALOG_TIMEOUT_S, cancel=None,
                         depth: dict | None = None, progress=None, info: dict | None = None) -> tuple[dict, dict]:
    """2.2.13: every catalog for one field, asked at the same time. Returns ({catalog: stars}, {catalog: error
    text}); a catalog that fails, or has not answered within `timeout` seconds, costs only its own stars. Daemon
    threads, so a slow server never holds the solve (or closing the program) hostage; `cancel` (an Event) stops
    the wait. 2.2.14 (item 5): depth (field_depth) goes to fetch_comparison_stars.
    2.2.16 (item 2): the per-field cache (catalog_cache_active), progress(catalog, state, detail) from the worker
    threads ("cached", "ok", "error", "tick" with catalog None and the seconds so far, and "late" when Gaia's deep
    half arrives after the deadline: detail {"n", "error", "seconds"}), and info filled with {"times": {catalog:
    seconds}, "cached": {catalog: True}, "gaia": Gaia's half status}."""
    import threading
    import time as _time

    catalogs = list(catalogs or CATALOGS)
    results: dict = {}
    t0 = _time.monotonic()
    use_cache = catalog_cache_active()
    if use_cache:
        ra_hours, dec_deg, fov_arcmin, depth = canonical_field_request(ra_hours, dec_deg, fov_arcmin, depth)
    info = info if info is not None else {}
    info.setdefault("times", {})
    info.setdefault("cached", {})
    info["cache"] = bool(use_cache)
    if depth and depth.get("max_rows"):
        info["max_rows"] = int(depth["max_rows"])   # 2.2.16 (review): the row cap actually asked

    def tell(cat, state, detail=None):
        if progress is not None:
            try:
                progress(cat, state, detail)
            except Exception:
                pass
    try:
        import inspect
        takes_depth = bool(depth) and "depth" in inspect.signature(fetch_comparison_stars).parameters
    except (TypeError, ValueError):
        takes_depth = False

    def one(cat):
        key = catalog_cache_key(cat, ra_hours, dec_deg, fov_arcmin, band, depth) if use_cache else None
        # Gaia asked deep with a depth: its two halves are cached one by one inside fetch_gaia_dr3 (cache_key).
        gaia_halves = cat == "Gaia DR3" and takes_depth and depth is not None
        if key is not None and not gaia_halves:
            hit = catalog_cache_load(key)
            if hit is not None:
                info["times"][cat] = _time.monotonic() - t0
                info["cached"][cat] = True
                results[cat] = ("ok", hit)
                tell(cat, "cached", len(hit))
                return
        cat_depth = depth
        status = None
        if cat == "Gaia DR3" and takes_depth and depth is not None:
            cat_depth = dict(depth)
            status = depth.get("status")
            if status is None:
                status = cat_depth["status"] = {}
            # 2.2.16 (item 2): Gaia answers before this call's deadline (its archive half at least); a deep half still
            # running then finishes in the background and is cached / reported (no silent loss).
            cat_depth["wait_s"] = max(0.5, timeout - (_time.monotonic() - t0) - min(1.5, 0.25 * timeout))

            def late(stars, error, extra=None):
                secs = _time.monotonic() - t0
                extra = extra or {}
                if stars is not None:
                    for st in stars:
                        st["catalog"] = cat
                # 2.2.16 (review): "cached" is the real save result (None when the cache is not in use).
                tell(cat, "late", {"n": len(stars) if stars is not None else 0, "error": error, "seconds": secs,
                                   "arrived": stars is not None, "cached": extra.get("cached"),
                                   "archive_missing": extra.get("archive_missing", ""),
                                   "incomplete": extra.get("incomplete", "")})
            cat_depth["on_late"] = late
            if key is not None:
                cat_depth["cache_key"] = key
        try:
            stars, chart_id = (fetch_comparison_stars(cat, ra_hours, dec_deg, fov_arcmin, band, depth=cat_depth)
                               if takes_depth else fetch_comparison_stars(cat, ra_hours, dec_deg, fov_arcmin, band))
            for st in stars:
                st["catalog"] = cat
                if cat == "AAVSO sequence":
                    st["chartid"] = chart_id
            results[cat] = ("ok", stars)
            info["times"][cat] = _time.monotonic() - t0
            if status is not None:
                info["gaia"] = dict(status)
            if gaia_halves:
                if status and status.get("esa_cached") and status.get("deep_cached"):
                    info["cached"][cat] = True
                    tell(cat, "cached", len(stars))
                    return
            elif key is not None and _cacheable(cat, stars, status):
                catalog_cache_save(key, stars)
            if getattr(stars, "incomplete", ""):
                info.setdefault("incomplete", {})[cat] = stars.incomplete
            tell(cat, "ok", len(stars))
        except Exception as exc:
            results[cat] = ("error", str(exc).splitlines()[0][:200] if str(exc) else exc.__class__.__name__)
            info["times"][cat] = _time.monotonic() - t0
            tell(cat, "error", results[cat][1])

    threads = [threading.Thread(target=one, args=(c,), daemon=True) for c in catalogs]
    for t in threads:
        t.start()
    deadline = t0 + timeout
    last_tick = 0
    for t in threads:
        while t.is_alive() and _time.monotonic() < deadline and not (cancel is not None and cancel.is_set()):
            t.join(0.2)
            secs = int(_time.monotonic() - t0)
            if secs != last_tick:   # 2.2.16 (item 2): the status line's seconds
                last_tick = secs
                tell(None, "tick", secs)
    if use_cache:
        try:
            catalog_cache_prune()
        except Exception:
            pass
    # 2.2.16 (review): cache writes that failed (one log line each in the app)
    while CATALOG_CACHE_ERRORS:
        try:
            info.setdefault("cache_errors", []).append(CATALOG_CACHE_ERRORS.pop(0))
        except IndexError:
            break
    found, errors = {}, {}
    for cat in catalogs:
        state = results.get(cat)
        if state is None:
            errors[cat] = "canceled" if cancel is not None and cancel.is_set() else f"no answer within {timeout:.0f} s"
        elif state[0] == "ok":
            found[cat] = state[1]
        else:
            errors[cat] = state[1]
    return found, errors


def frame_pointing(header: dict):
    """2.2.13: where one frame points: the center of its plate solution, else its RA/Dec keywords, else None."""
    wcs = header_wcs(header or {})
    if wcs is not None:
        try:
            return tuple(float(v) for v in wcs_center(wcs, (int(header.get("NAXIS2")), int(header.get("NAXIS1")))))
        except (TypeError, ValueError):
            pass
    return header_pointing(header or {})


def pointing_groups(pointings: list, gap_deg: float = 1.0) -> list[list[int]]:
    """Frames grouped by where they point: indices whose pointings lie within gap_deg of a group's first frame
    (frames with no pointing are left out). Largest group first."""
    groups: list[tuple[tuple[float, float], list[int]]] = []
    for i, p in enumerate(pointings):
        if p is None:
            continue
        for centre, members in groups:
            if sky_separation_arcsec(p[0], p[1], centre[0], centre[1]) / 3600.0 <= gap_deg:
                members.append(i)
                break
        else:
            groups.append(((float(p[0]), float(p[1])), [i]))
    return [m for _c, m in sorted(groups, key=lambda g: -len(g[1]))]


GENERIC_OBJECTS = ("", "fov", "light", "object", "target", "unknown", "none", "test", "flat", "dark", "bias")


def same_object_name(a: str, b: str) -> bool:
    """2.2.13: two spellings of one object name ('HAT-P-23 b', 'HAT-P-23b', 'hatp23'; 'HD219134' and 'HD 219134');
    a planet's name and its star's count as the same field. Real aliases (HIP vs HD) need a lookup."""
    import re

    def norm(t):
        t = re.sub(r"[\s_\-.]+", "", str(t or "").lower())
        return re.sub(r"(?<=\d)[b-h]$", "", t)
    return bool(norm(a)) and norm(a) == norm(b)


def header_pointing(header: dict):
    """Where the telescope pointed, from the FITS header: (RA deg, Dec deg), or None.
    Reads RA/DEC in degrees (NINA, ASIAIR, SharpCap), OBJCTRA/OBJCTDEC as 'HH MM SS' / '+DD MM SS', or a WCS
    reference point (CRVAL1/CRVAL2)."""
    def sexa(text, hours):
        parts = str(text).replace(":", " ").replace("h", " ").replace("m", " ").replace("s", " ").replace("d", " ").split()
        try:
            vals = [float(p) for p in parts]
        except ValueError:
            return None
        if not vals:
            return None
        sign = -1.0 if str(text).strip().startswith("-") else 1.0
        v = abs(vals[0]) + (vals[1] if len(vals) > 1 else 0) / 60 + (vals[2] if len(vals) > 2 else 0) / 3600
        return sign * v * (15.0 if hours else 1.0)

    ra = dec = None
    for key in ("RA", "OBJRA", "RA_OBJ"):
        v = header.get(key)
        if v is None:
            continue
        try:
            ra = float(v)
        except (TypeError, ValueError):
            ra = sexa(v, hours=True)
        if ra is not None:
            break
    for key in ("DEC", "OBJDEC", "DEC_OBJ"):
        v = header.get(key)
        if v is None:
            continue
        try:
            dec = float(v)
        except (TypeError, ValueError):
            dec = sexa(v, hours=False)
        if dec is not None:
            break
    if ra is None and header.get("OBJCTRA") is not None:
        ra = sexa(header.get("OBJCTRA"), hours=True)
    if dec is None and header.get("OBJCTDEC") is not None:
        dec = sexa(header.get("OBJCTDEC"), hours=False)
    if (ra is None or dec is None) and header.get("CRVAL1") is not None and header.get("CRVAL2") is not None:
        try:
            ra, dec = float(header["CRVAL1"]), float(header["CRVAL2"])
        except (TypeError, ValueError):
            pass
    if ra is None or dec is None or not (-90 <= dec <= 90):
        return None
    return ra % 360.0, dec


def _triangles(pts: np.ndarray, k: int = 6):
    """Triangles from each point and pairs of its k nearest neighbors: vertex indices ordered so that vertex 0 is
    opposite the shortest side and vertex 2 opposite the longest, plus the scale-free shape (L1/L3, L2/L3)
    and the longest side."""
    n = len(pts)
    if n < 3:
        return np.zeros((0, 3), int), np.zeros((0, 2)), np.zeros(0)
    d = np.hypot(pts[:, None, 0] - pts[None, :, 0], pts[:, None, 1] - pts[None, :, 1])
    tris = set()
    for i in range(n):
        near = np.argsort(d[i])[1:k + 1]
        for a in range(len(near)):
            for b in range(a + 1, len(near)):
                tris.add(tuple(sorted((i, int(near[a]), int(near[b])))))
    out_idx, out_shape, out_long = [], [], []
    for tri in tris:
        i, j, m = tri
        sides = {i: d[j, m], j: d[i, m], m: d[i, j]}          # side opposite each vertex
        order = sorted(tri, key=lambda v: sides[v])
        l1, l2, l3 = (sides[v] for v in order)
        if l3 <= 0 or l1 / l3 < 0.05:
            continue
        out_idx.append(order)
        out_shape.append((l1 / l3, l2 / l3))
        out_long.append(l3)
    return np.array(out_idx, int), np.array(out_shape, float), np.array(out_long, float)


def _fit_placement(e: np.ndarray, n: np.ndarray, x: np.ndarray, y: np.ndarray, mirrored: bool):
    """Least-squares (tx, ty, angle, scale factor) in place_stars's convention, e and n already in pixels at the
    nominal scale. Returns (params, rms)."""
    ee = -e if mirrored else e
    # x = tx - a*ee - b*n ;  y = ty + b*ee - a*n   with a = cos/f, b = sin/f
    rows = len(e)
    A = np.zeros((2 * rows, 4))
    rhs = np.concatenate([x, y])
    A[:rows, 0] = 1.0
    A[:rows, 2] = -ee
    A[:rows, 3] = -n
    A[rows:, 1] = 1.0
    A[rows:, 2] = -n
    A[rows:, 3] = ee
    sol, *_ = np.linalg.lstsq(A, rhs, rcond=None)
    tx, ty, a, b = sol
    resid = A @ sol - rhs
    f = 1.0 / max(math.hypot(a, b), 1e-12)
    return {"tx": float(tx), "ty": float(ty), "angle": math.degrees(math.atan2(b, a)) % 360.0, "scale_factor": f,
            "mirrored": mirrored}, float(np.sqrt(np.mean(resid ** 2)))


SOLVE_RETRY_HITS = 8         # 2.2.17 (item 1): the app accepts a field solve from 8 matched stars
SOLVE_POINTING_SLACK_ARCMIN = 3.0
SOLVE_STRONG_HITS = 20      # 2.2.17 (round 2): a retry with this many matches needs rms < tolerance only
SOLVE_RETRY_FRACTION = 0.15  # 2.2.17 (review S3): a retry needs 15% of the catalog stars it tried matched


def solve_field(sources: list, stars: list[dict], ra_deg: float, dec_deg: float, scale_arcsec: float,
                tolerance_px: float = 3.0, scale_range: float = 0.3, n_src: int = 30, n_cat: int = 45,
                frame_shape=None) -> dict | None:
    """Field solve without a target (_solve_field_once). 2.2.17 (item 1, Moeller's 22' frames): the catalog is
    fetched for a circle well beyond the frame, so on a small field many of the brightest catalog stars fall off the
    image and too few triangles agree (7 of the 8 needed on TOI-4028, 3 on the dense TOI-5933 field). When that
    happens and the frame's size is known (frame_shape, h x w px), the solve is tried again on the same footprint on
    both sides: the image stars inside the frame's inscribed circle and the catalog stars inside that circle around
    the pointing, first with the usual numbers of stars, then with twice as many; last, twice as many on the frame's
    whole circle (half-diagonal x 1.1 plus SOLVE_POINTING_SLACK_ARCMIN). A retry counts only with SOLVE_RETRY_FRACTION
    of its catalog stars matched and a tight fit (review S3); the best answer is kept, so a field that
    solved before is unchanged (the retries run only below SOLVE_RETRY_HITS)."""
    best = _solve_field_once(sources, stars, ra_deg, dec_deg, scale_arcsec, tolerance_px, scale_range, n_src, n_cat)
    if frame_shape is None or (best is not None and best.get("hits", 0) >= SOLVE_RETRY_HITS):
        return best
    try:
        h, w = float(frame_shape[0]), float(frame_shape[1])
        r_in = min(h, w) / 2.0
        reach = math.hypot(w, h) / 2.0 * float(scale_arcsec) * 1.1 + SOLVE_POINTING_SLACK_ARCMIN * 60.0
    except (TypeError, ValueError, IndexError):
        return best
    sep = {id(st): sky_separation_arcsec(ra_deg, dec_deg, st["ra"], st["dec"]) for st in stars
           if st.get("ra") is not None and st.get("dec") is not None}
    cat_in = [st for st in stars if sep.get(id(st), 1e9) < r_in * float(scale_arcsec)]
    cat_frame = [st for st in stars if sep.get(id(st), 1e9) < reach]
    cx, cy = (w - 1.0) / 2.0, (h - 1.0) / 2.0
    src_in = [p for p in sources if math.hypot(p[0] - cx, p[1] - cy) < r_in]
    tries = ((src_in, cat_in, n_src, n_cat), (src_in, cat_in, 2 * n_src, 2 * n_cat),
             (sources, cat_frame, 2 * n_src, 2 * n_cat))
    for src_k, cat_k, ns, nc in tries:
        if len(src_k) < 6 or len(cat_k) < 6:
            continue
        again = _solve_field_once(src_k, cat_k, ra_deg, dec_deg, scale_arcsec, tolerance_px, scale_range, ns, nc)
        # 2.2.17 (review S3): more catalog stars give a chance match more room, so a retry is kept only with
        # SOLVE_RETRY_FRACTION of the catalog stars tried matched (at least SOLVE_RETRY_HITS) and a tight fit
        # (rms under half the match tolerance). Wrong fields reached 5-6 of 90 on the dense TOI-5933 field;
        # the right Moeller fields match 35-52.
        if again is None:
            continue
        need = max(SOLVE_RETRY_HITS, math.ceil(SOLVE_RETRY_FRACTION * min(nc, len(cat_k))))
        # Round 2: the right TOI-4028 solution has rms 2.2-2.4 px against tol 4.9, so a strong match (20+ stars, which
        # a chance match never reaches) only needs rms under the tolerance; marginal counts keep the tight limit.
        hits, rms = again.get("hits", 0), again.get("rms", float("inf"))
        if hits < need or not (rms < 0.5 * tolerance_px or (hits >= max(need, SOLVE_STRONG_HITS) and rms < tolerance_px)):
            continue
        if best is None or again.get("hits", 0) > best.get("hits", 0):
            best = again
        break
    return best


def _solve_field_once(sources: list, stars: list[dict], ra_deg: float, dec_deg: float, scale_arcsec: float,
                      tolerance_px: float = 3.0, scale_range: float = 0.3, n_src: int = 30,
                      n_cat: int = 45) -> dict | None:
    """Find the field without a target: match triangles of bright stars between the image and the catalog
    (shape is unchanged by shift, rotation, mirror, and scale), then fit the placement and count all matches.

    (ra_deg, dec_deg) only needs to be near the field (the header pointing); the solution returns where that
    point falls on the image as 'target_xy', in the same convention as match_chart / place_stars."""
    src = np.asarray(sources[:n_src], dtype=float)
    cat = [s for s in stars if s.get("ra") is not None and s.get("dec") is not None]
    cat.sort(key=lambda s: s.get("bright", s.get("mag", 99)) if s.get("bright", s.get("mag")) is not None else 99)
    cat = cat[:n_cat]
    if len(src) < 6 or len(cat) < 6:
        return None
    cos_dec = math.cos(math.radians(dec_deg))
    e = np.array([((s["ra"] - ra_deg + 180.0) % 360.0 - 180.0) * 3600.0 * cos_dec for s in cat]) / scale_arcsec
    n = np.array([(s["dec"] - dec_deg) * 3600.0 for s in cat]) / scale_arcsec
    catxy = np.column_stack([e, n])
    si, sshape, slong = _triangles(src)
    ci, cshape, clong = _triangles(catxy)
    if len(si) == 0 or len(ci) == 0:
        return None
    dshape = np.hypot(sshape[:, None, 0] - cshape[None, :, 0], sshape[:, None, 1] - cshape[None, :, 1])
    ratio = slong[:, None] / clong[None, :]
    ok = (dshape < 0.012) & (ratio > 1.0 - scale_range) & (ratio < 1.0 + scale_range)
    pairs = np.argwhere(ok)
    if len(pairs) == 0:
        return None
    # Most-similar triangles first; stop early on a clear winner.
    pairs = pairs[np.argsort(dshape[ok])][:4000]
    all_src = np.asarray(sources, dtype=float)
    best = None
    for p, q in pairs:
        s_idx, c_idx = si[p], ci[q]
        for mirrored in (False, True):
            par, rms = _fit_placement(e[c_idx], n[c_idx], src[s_idx, 0], src[s_idx, 1], mirrored)
            if rms > 2.0 * tolerance_px or abs(par["scale_factor"] - 1.0) > scale_range:
                continue
            theta = math.radians(par["angle"])
            ct, st = math.cos(theta), math.sin(theta)
            ee = (-e if mirrored else e) / par["scale_factor"]
            nn = n / par["scale_factor"]
            px = par["tx"] - ee * ct - nn * st
            py = par["ty"] + ee * st - nn * ct
            dist = np.hypot(px[:, None] - all_src[None, :, 0], py[:, None] - all_src[None, :, 1])
            hit = dist.min(axis=1) < 2.0 * tolerance_px
            if best is None or hit.sum() > best[0]:
                best = (int(hit.sum()), par, mirrored)
        if best is not None and best[0] >= max(12, 0.5 * len(cat)):
            break
    if best is None or best[0] < 6:
        return None
    # Refine on every matched star, then report in match_chart's form.
    _hits, par, mirrored = best
    for _ in range(2):
        theta = math.radians(par["angle"])
        ct, st = math.cos(theta), math.sin(theta)
        ee = (-e if mirrored else e) / par["scale_factor"]
        nn = n / par["scale_factor"]
        px = par["tx"] - ee * ct - nn * st
        py = par["ty"] + ee * st - nn * ct
        dist = np.hypot(px[:, None] - all_src[None, :, 0], py[:, None] - all_src[None, :, 1])
        j = dist.argmin(axis=1)
        hit = dist.min(axis=1) < tolerance_px
        if hit.sum() < 4:
            break
        par, _rms = _fit_placement(e[hit], n[hit], all_src[j[hit], 0], all_src[j[hit], 1], mirrored)
    theta = math.radians(par["angle"])
    ct, st = math.cos(theta), math.sin(theta)
    ee = (-e if mirrored else e) / par["scale_factor"]
    nn = n / par["scale_factor"]
    px = par["tx"] - ee * ct - nn * st
    py = par["ty"] + ee * st - nn * ct
    dist = np.hypot(px[:, None] - all_src[None, :, 0], py[:, None] - all_src[None, :, 1])
    j = dist.argmin(axis=1)
    hit = dist.min(axis=1) < tolerance_px
    placed = [(float(all_src[j[i], 0]), float(all_src[j[i], 1]), cat[i]) for i in range(len(cat)) if hit[i]]
    d_hit = dist.min(axis=1)[hit]
    return {"hits": int(hit.sum()), "angle": par["angle"], "mirrored": mirrored, "scale_factor": par["scale_factor"],
            "placed": placed, "target_xy": (par["tx"], par["ty"]), "score": (int(hit.sum()), 0.0),
            "match_count": len(cat),
            # 2.2.17 (review S3): rms of the matched stars' offsets, px (the retry's acceptance test)
            "rms": float(np.sqrt(np.mean(d_hit ** 2))) if d_hit.size else float("inf")}


def place_stars(stars: list[dict], solution: dict, target_xy: tuple[float, float], ra_deg: float, dec_deg: float, scale_arcsec: float) -> list[tuple[float, float, dict]]:
    """Put catalog stars on the image using a match_chart solution."""
    cos_dec = math.cos(math.radians(dec_deg))
    factor = solution.get("scale_factor", 1.0)
    theta = math.radians(solution["angle"])
    ct, st = math.cos(theta), math.sin(theta)
    out = []
    for star in stars:
        e = ((star["ra"] - ra_deg + 180.0) % 360.0 - 180.0) * 3600.0 * cos_dec / (scale_arcsec * factor)
        n = (star["dec"] - dec_deg) * 3600.0 / (scale_arcsec * factor)
        if solution.get("mirrored"):
            e = -e
        out.append((target_xy[0] - e * ct - n * st, target_xy[1] + e * st - n * ct, star))
    return out


def _segment_key(obs: Obs) -> tuple[str, str]:
    return (obs.night, obs.segment)


def apply_night_zero_point(observations: list[Obs]) -> list[float]:
    """Remove zero-point jumps between nights, and between the two sides of a meridian flip,
    using the check star. Stored mags are not changed.

    check_std is the check star reduced against the comps. Each segment's median of it,
    minus the median over the whole series, is that segment's offset. Segments without a
    check star are left as they are.
    """
    by_seg: dict[tuple[str, str], list[float]] = {}
    for obs in observations:
        if obs.check_std is None or not math.isfinite(obs.check_std):
            continue
        by_seg.setdefault(_segment_key(obs), []).append(obs.check_std)
    if not by_seg:
        return [obs.mag for obs in observations]
    # Each night / flip segment counts once, however many frames it has, so one long
    # night cannot set the reference for all the others.
    seg_meds = {key: float(np.median(vals)) for key, vals in by_seg.items()}
    series_med = float(np.median(list(seg_meds.values())))
    offsets = {key: med - series_med for key, med in seg_meds.items()}
    return [obs.mag - offsets.get(_segment_key(obs), 0.0) for obs in observations]


def normalize_segments(observations: list[Obs], values: list[float] | None = None) -> list[float]:
    """Shift each night / flip segment so its median matches the median of the whole series.

    For transit-style work, where only relative changes inside a segment matter. This also
    removes the offset a meridian flip leaves in the target that the check star does not see.
    """
    vals = list(values) if values is not None else [obs.mag for obs in observations]
    if not vals:
        return []
    groups: dict[tuple[str, str], list[float]] = {}
    for obs, v in zip(observations, vals):
        if math.isfinite(v):
            groups.setdefault(_segment_key(obs), []).append(v)
    seg_meds = {key: float(np.median(v)) for key, v in groups.items()}
    overall = float(np.median(list(seg_meds.values()))) if seg_meds else 0.0
    shift = {key: med - overall for key, med in seg_meds.items()}
    return [v - shift.get(_segment_key(obs), 0.0) for obs, v in zip(observations, vals)]


SIDEREAL_DAY = 0.99726957


def period_alias_text(period_days: float, tolerance: float = 0.05) -> str:
    """2.2.3: a note when a period sits within `tolerance` (fraction) of 1, 1/2 or 1/3 day (solar or sidereal),
    where observing at about the same time every night makes false peaks. '' otherwise."""
    if not (period_days and math.isfinite(period_days) and period_days > 0):
        return ""
    for k, name in ((1, "1 day"), (2, "1/2 day"), (3, "1/3 day")):
        for base, kind in ((1.0, ""), (SIDEREAL_DAY, " (sidereal)")):
            ref = base / k
            if abs(period_days - ref) / ref < tolerance:
                return (f"{period_days:.4f} d is within {abs(period_days - ref) / ref * 100:.1f}% of {name}{kind}: "
                        "nights observed at the same time of evening make false peaks there. Treat it as a likely "
                        "sampling alias unless a single night shows the variation itself.")
    return ""


def period_leave_one_out(jd: np.ndarray, values: np.ndarray, nights: list[str], best: dict,
                         min_period: float, max_period: float) -> dict:
    """2.2.3: does the best period depend on one night alone? The search is repeated with each night left out.

    A night "carries" the period when, without it, the best peak moves more than 5% away from the period AND the
    power at the period falls to under half. Returns {"carried_by": [nights], "checked": n_nights}."""
    t = np.asarray(jd, dtype=float)
    y = np.asarray(values, dtype=float)
    labels = np.asarray(nights)
    uniq = sorted(set(nights))
    out = {"carried_by": [], "checked": 0}
    if len(uniq) < 3 or not best:
        return out
    p0 = float(best["period_days"])
    f0 = 1.0 / p0
    p_power0 = float(best["power"])
    for night in uniq:
        keep = labels != night
        if keep.sum() < 8:
            continue
        try:
            res = lomb_scargle(t[keep], y[keep], min_period, max_period)
        except Exception:
            res = None
        if not res:
            continue
        out["checked"] += 1
        freqs = np.asarray(res["freqs"])
        power = np.asarray(res["power_spectrum"])
        order = np.argsort(freqs)
        at_p = float(np.interp(f0, freqs[order], power[order]))
        moved = abs(res["period_days"] - p0) / p0 > 0.05
        if moved and at_p < 0.5 * p_power0:
            out["carried_by"].append(night)
    return out


# ---- 2.2.17 (item 10): airmass / color check before a period is called significant -----------------------------
AIRMASS_TREND_MIN_NIGHTS = 5     # nights with a mean and an airmass before the trend is judged
AIRMASS_TREND_P = 0.01           # review: two-sided p of the slope's t statistic (n - 2 dof) below this: follows
AIRMASS_TREND_SIGMA = 3.0        # (the t threshold for many nights; few nights need more, see airmass_t_limit)
AIRMASS_TREND_MIN_SPREAD = 0.05  # nights' median airmasses must spread at least this much
NIGHT_MIN_POINTS = 3


def nightly_means(values, nights, airmass=None) -> list[dict]:
    """2.2.17 (item 10): each night's mean value and median airmass (NaN when the night has none), in night order:
    [{"night", "mean", "airmass", "n"}]. Nights with fewer than NIGHT_MIN_POINTS finite values are left out."""
    v = np.asarray(values, dtype=float)
    labels = list(nights)
    am = np.full(len(v), np.nan) if airmass is None else np.asarray(
        [np.nan if a is None else float(a) for a in airmass], dtype=float)
    out = []
    for night in dict.fromkeys(labels):
        k = np.array([n == night for n in labels], dtype=bool) & np.isfinite(v)
        if k.sum() < NIGHT_MIN_POINTS:
            continue
        a = am[k][np.isfinite(am[k])]
        out.append({"night": night, "mean": float(np.mean(v[k])), "airmass": float(np.median(a)) if a.size else
                    float("nan"), "n": int(k.sum())})
    return out


def airmass_t_limit(dof: int, p: float = AIRMASS_TREND_P) -> float:
    """2.2.17 (review): |t| above which a slope fitted with `dof` degrees of freedom is significant at two-sided p
    (Student's t; 3 dof: 5.84, 8 dof: 3.36). A fixed 3 sigma let 5-6-night series of real 1-3 d variables trip."""
    if dof < 1:
        return float("inf")
    try:
        from scipy.stats import t as student_t
        return float(student_t.ppf(1.0 - p / 2.0, dof))
    except Exception:   # scipy missing: the p = 0.01 table
        table = {1: 63.66, 2: 9.925, 3: 5.841, 4: 4.604, 5: 4.032, 6: 3.707, 7: 3.499, 8: 3.355, 9: 3.250,
                 10: 3.169, 12: 3.055, 15: 2.947, 20: 2.845, 30: 2.750}
        for d in sorted(table, reverse=True):
            if dof >= d:
                return table[d]
        return table[1]


def airmass_trend(means: list[dict]) -> dict:
    """2.2.17 (item 10): a straight-line fit of the nightly means against each night's median airmass (ordinary
    least squares; the slope's error from the scatter about the line, since night-to-night offsets are not photon
    noise). Returns {"n", "slope" (mag per airmass), "slope_err", "sigma" (|slope| / error), "sd_before",
    "sd_after", "intercept", "ref_airmass", "follows" (True when >= AIRMASS_TREND_MIN_NIGHTS nights and sigma above
    the Student-t limit for n - 2 dof at p = AIRMASS_TREND_P, airmass_t_limit), "t_limit", "skip" (why not judged,
    or '')}."""
    rows = [m for m in means if math.isfinite(m.get("airmass", float("nan"))) and math.isfinite(m["mean"])]
    out = {"n": len(rows), "slope": float("nan"), "slope_err": float("nan"), "sigma": 0.0, "follows": False,
           "sd_before": float("nan"), "sd_after": float("nan"), "intercept": float("nan"),
           "ref_airmass": float("nan"), "skip": ""}
    if len(rows) < AIRMASS_TREND_MIN_NIGHTS:
        out["skip"] = (f"{len(rows)} night(s) with an airmass" if any(math.isfinite(m.get("airmass", float("nan")))
                                                                      for m in means) else "no airmass")
        return out
    a = np.array([m["airmass"] for m in rows])
    y = np.array([m["mean"] for m in rows])
    if float(a.max() - a.min()) < AIRMASS_TREND_MIN_SPREAD:
        out["skip"] = f"the nights' airmasses spread only {a.max() - a.min():.2f}"
        return out
    design = np.vstack([np.ones_like(a), a]).T
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = y - design @ coef
    dof = len(y) - 2
    s2 = float(np.sum(resid ** 2) / dof) if dof > 0 else float("nan")
    try:
        cov = s2 * np.linalg.inv(design.T @ design)
        err = math.sqrt(cov[1, 1]) if cov[1, 1] > 0 else float("nan")
    except np.linalg.LinAlgError:
        err = float("nan")
    slope = float(coef[1])
    sigma = abs(slope) / err if (math.isfinite(err) and err > 0) else (float("inf") if abs(slope) > 0 else 0.0)
    out.update(slope=slope, slope_err=err, sigma=float(sigma), intercept=float(coef[0]),
               ref_airmass=float(np.median(a)), sd_before=float(np.std(y)), sd_after=float(np.std(resid)),
               t_limit=airmass_t_limit(dof), follows=bool(sigma > airmass_t_limit(dof)))
    return out


def airmass_trend_text(trend: dict) -> str:
    """2.2.17 (item 10): the warning when the nightly means follow airmass; '' otherwise."""
    if not trend or not trend.get("follows"):
        return ""
    return (f"Nightly means follow airmass ({trend['slope']:+.3f} ± {trend['slope_err']:.3f} mag per airmass, "
            f"{trend['n']} nights): a period may come from which nights ran low, not from the star. Night-to-night "
            f"spread {trend['sd_before']:.3f} mag, {trend['sd_after']:.3f} after taking the trend out.")


def fap_baluev(power: float, n_points: float, t, min_period: float, max_period: float) -> float:
    """2.2.17 (item 10): the false-alarm probability of a Lomb-Scargle peak (standard normalization), Baluev (2008)
    as in astropy (VanderPlas 2018), with the number of points given (period_night_fap counts N - k + 1 after the
    nights' own means are taken out). NaN when it cannot be worked out."""
    try:
        z = float(power)
        n = float(n_points)
        t = np.asarray(t, dtype=float)
        t = t[np.isfinite(t)]
        if not (0.0 < z < 1.0) or n <= 4 or t.size < 2 or not (0 < min_period < max_period):
            return float("nan")
        nh, nk = n - 1.0, n - 3.0
        fmax = 1.0 / min_period
        w = fmax * math.sqrt(4.0 * math.pi * float(np.var(t)))
        gamma = math.sqrt(2.0 / nh) * math.exp(math.lgamma(nh / 2.0) - math.lgamma((nh - 1.0) / 2.0))
        tau = gamma * w * (1.0 - z) ** (0.5 * (nk - 1.0)) * math.sqrt(0.5 * nh * z)
        single = (1.0 - z) ** (0.5 * nk)                           # P(power > z) at one frequency
        fap = 1.0 - (1.0 - single) * math.exp(-tau)
        return float(min(max(fap, 0.0), 1.0))
    except (ValueError, OverflowError, ZeroDivisionError):
        return float("nan")


NIGHT_PERM_EXACT = 5040   # all orderings of the nightly means up to 7 nights; random orderings above
NIGHT_PERM_RANDOM = 3000


NIGHT_PERM_MAX_FREQS = 400   # review: frequency grid cap for the nightly means (k means have about k independent peaks)
NIGHT_PERM_CHUNK = 250       # review: orderings per block, so memory stays small


def _night_perm_fap(t_mean: np.ndarray, y_mean: np.ndarray, f_lo: float, f_hi: float, seed: int = 0) -> float:
    """2.2.17 (review): permutation false-alarm probability of the highest Lomb-Scargle power among the nightly means
    (floating mean, frequencies f_lo..f_hi): the means are shuffled among the nights' times and the search repeated.
    With no period, night offsets are exchangeable, so this is calibrated whatever their size; with k nights the
    smallest possible FAP is 1/k! (5 nights 0.008, 6 nights 0.0014). NaN under 4 nights (not judged).
    The grid is 8x oversampled on the means' span but capped at NIGHT_PERM_MAX_FREQS frequencies: the observed and
    the shuffled means are searched on the same grid, so the calibration holds; orderings run in blocks of
    NIGHT_PERM_CHUNK (60 nights over 700 d: a fraction of a second, a few MB)."""
    import itertools
    k = t_mean.size
    if k < 4:
        return float("nan")
    span = float(t_mean.max() - t_mean.min())
    if not (span > 0 and f_hi > f_lo > 0):
        return float("nan")
    step = max(1.0 / (8.0 * span), (f_hi - f_lo) / NIGHT_PERM_MAX_FREQS)
    freqs = np.arange(f_lo, f_hi, step)
    if freqs.size < 2:
        freqs = np.array([f_lo, f_hi])
    tt = t_mean - t_mean.mean()
    # sine and cosine at every frequency, each made orthogonal to the mean, then to each other (Gram-Schmidt): the
    # power of centered values is their squared projection on the two (the floating-mean Lomb-Scargle power).
    ph = 2 * math.pi * np.outer(tt, freqs)                    # (k, F)
    sn, cs = np.sin(ph), np.cos(ph)
    sn -= sn.mean(axis=0)
    cs -= cs.mean(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        sn /= np.sqrt((sn ** 2).sum(axis=0))
        cs -= sn * (cs * sn).sum(axis=0)
        cs /= np.sqrt((cs ** 2).sum(axis=0))

    def max_power(ys: np.ndarray) -> np.ndarray:              # ys: (m, k)
        out = np.empty(ys.shape[0])
        for a in range(0, ys.shape[0], NIGHT_PERM_CHUNK):
            yc = ys[a:a + NIGHT_PERM_CHUNK]
            yc = yc - yc.mean(axis=1, keepdims=True)
            tot = (yc ** 2).sum(axis=1)
            with np.errstate(invalid="ignore", divide="ignore"):
                out[a:a + NIGHT_PERM_CHUNK] = np.nanmax((yc @ sn) ** 2 + (yc @ cs) ** 2, axis=1) / tot
        return out
    observed = float(max_power(y_mean[None, :])[0])
    if not math.isfinite(observed):
        return float("nan")
    if math.factorial(k) <= NIGHT_PERM_EXACT:
        perms = np.array(list(itertools.permutations(range(k))))
        powers = max_power(y_mean[perms])
        return float(np.mean(powers >= observed - 1e-12))
    rng = np.random.default_rng(seed)
    perms = np.array([rng.permutation(k) for _ in range(NIGHT_PERM_RANDOM)])
    powers = max_power(y_mean[perms])
    return float((np.sum(powers >= observed - 1e-12) + 1) / (NIGHT_PERM_RANDOM + 1))


def period_night_fap(jd, values, nights, best: dict, min_period: float, max_period: float) -> dict:
    """2.2.17 (item 10; review): a false-alarm probability that counts nights, as a conservative heuristic next to
    the point FAP (points of a night share offsets from clouds, extinction and calibration, so ten nights cannot give
    a FAP of 1e-14).
    - Best period longer than twice the median night's span (a night shows only part of a cycle): the evidence is
      the nightly means, so the FAP is a permutation test on them (_night_perm_fap; with 5-7 nights only a strong
      signal can pass, since 1/k! is the smallest FAP possible; under 4 nights NaN: not judged, and the 2.2.3
      leave-one-night-out / one-short-night rules decide as before).
    - Shorter periods (a night shows a cycle or more): each night's own mean is taken out, so night offsets cannot
      make the peak, and the sine's share of the remaining scatter at the best period gives a Baluev FAP with
      N - k + 1 points (k nights).
    An earlier version (Kish N_eff of the points after the fitted sine) let 16-51% of constant stars with night
    offsets through in simulations, and failed real short periods on 3 nights. Returns {"fap", "n_eff" (the number
    of points the FAP counts: nights, or N - k + 1), "n", "method": "nightly means" / "within nights", "nights"}."""
    out = {"fap": float("nan"), "n_eff": float("nan"), "n": 0, "method": "", "nights": 0}
    if not best:
        return out
    t = np.asarray(jd, dtype=float)
    y = np.asarray(values, dtype=float)
    labels = np.asarray(list(nights))
    ok = np.isfinite(t) & np.isfinite(y)
    t, y, labels = t[ok], y[ok], labels[ok]
    if t.size < 8:
        return out
    p0 = float(best["period_days"])
    names = [n for n in dict.fromkeys(labels.tolist()) if int(np.sum(labels == n)) >= NIGHT_MIN_POINTS]
    keep = np.isin(labels, names)
    t, y, labels = t[keep], y[keep], labels[keep]
    k = len(names)
    out.update(nights=k, n=int(t.size))
    if k == 0 or t.size < 8:
        return out
    spans = [float(np.ptp(t[labels == n])) for n in names]
    night_span = float(np.median(spans))
    if p0 > 2.0 * night_span:
        t_mean = np.array([float(np.mean(t[labels == n])) for n in names])
        y_mean = np.array([float(np.mean(y[labels == n])) for n in names])
        f_hi = min(1.0 / min_period, 1.0 / max(2.0 * night_span, 1e-6))
        out.update(method="nightly means", n_eff=float(k), fap=_night_perm_fap(t_mean, y_mean, 1.0 / max_period, f_hi))
        return out
    yc = y.copy()
    for n in names:
        sel = labels == n
        yc[sel] -= np.mean(y[sel])
    w = 2.0 * math.pi / p0
    design = np.column_stack([np.sin(w * (t - t[0])), np.cos(w * (t - t[0]))])
    try:
        coef, *_ = np.linalg.lstsq(design, yc, rcond=None)
        tot = float(np.sum(yc ** 2))
        power = 1.0 - float(np.sum((yc - design @ coef) ** 2)) / tot if tot > 0 else float("nan")
    except np.linalg.LinAlgError:
        power = float("nan")
    n_use = float(t.size - k + 1)
    # Deliberately conservative: the Baluev bandwidth is the whole search range (1 / min_period) over the whole
    # baseline, though the peak was picked from the raw points and only this one frequency is tested here.
    out.update(method="within nights", n_eff=n_use, fap=fap_baluev(power, n_use, t, min_period, max_period))
    return out


def period_airmass_check(jd, values, nights, night_airmass: dict, best: dict, min_period: float, max_period: float,
                         trend: dict, name=None) -> dict:
    """2.2.17 (item 10): does the best period come from the airmass of the nights? Only asked when the nightly means
    follow airmass (trend["follows"]). Two tests, each with the leave-one-night-out rule of 2.2.3 (the best peak
    moves more than 5% AND the power at the period falls under half):
    1. each night's points shifted by the trend (slope x (night airmass - median)), the search run again;
    2. the high-airmass nights (the top third by median airmass, at least one) left out, the search run again
       (only when at least AIRMASS_TREND_MIN_NIGHTS nights remain).
    A search that fails counts as "not gone": an optional check never demotes on its own failure.
    jd/values/nights: the series the period search used (points or bins); night_airmass: {night: median airmass};
    name: night key -> the name shown (optional).
    Returns {"demoted": bool, "why": plain text or '', "detrended_period", "without_high_period", "high_nights"}."""
    out = {"demoted": False, "why": "", "detrended_period": None, "without_high_period": None, "high_nights": []}
    if not best or not trend or not trend.get("follows"):
        return out
    t = np.asarray(jd, dtype=float)
    y = np.asarray(values, dtype=float)
    labels = np.asarray(list(nights))
    night_am = {n: float(a) for n, a in (night_airmass or {}).items() if a is not None and math.isfinite(a)}
    p0 = float(best["period_days"])
    f0 = 1.0 / p0
    power0 = float(best["power"])

    def gone(res) -> bool:
        if not res:
            return False   # review: a failed optional search never changes the verdict
        freqs = np.asarray(res["freqs"])
        power = np.asarray(res["power_spectrum"])
        order = np.argsort(freqs)
        at_p = float(np.interp(f0, freqs[order], power[order]))
        return abs(res["period_days"] - p0) / p0 > 0.05 and at_p < 0.5 * power0

    shift = np.array([trend["slope"] * (night_am[n] - trend["ref_airmass"]) if n in night_am else 0.0
                      for n in labels.tolist()])
    try:
        res1 = lomb_scargle(t, y - shift, min_period, max_period)
    except Exception:
        res1 = None
    out["detrended_period"] = res1["period_days"] if res1 else None
    ranked = sorted(night_am, key=lambda n: night_am[n], reverse=True)
    high = ranked[:max(1, len(ranked) // 3)]
    out["high_nights"] = high
    keep = ~np.isin(labels, high)
    res2 = None
    # review: at least AIRMASS_TREND_MIN_NIGHTS nights must remain, or this test is skipped (never demotes)
    if keep.sum() >= 8 and len(set(labels[keep].tolist())) >= AIRMASS_TREND_MIN_NIGHTS:
        try:
            res2 = lomb_scargle(t[keep], y[keep], min_period, max_period)
        except Exception:
            res2 = None
        out["without_high_period"] = res2["period_days"] if res2 else None
    why = []
    if gone(res1):
        why.append("with the airmass trend taken out of the nightly means")
    if res2 is not None and gone(res2):
        label = name or (lambda n: str(n))
        why.append(f"without the {len(high)} highest-airmass night{'s' if len(high) > 1 else ''} "
                   f"({', '.join(label(n) for n in high)})")
    if why:
        out["demoted"] = True
        out["why"] = ("The period disappears " + " and ".join(why) + ": it is consistent with the airmass, not "
                      "shown to be the star.")
    return out


def lomb_scargle(jd: np.ndarray, mag: np.ndarray, min_period: float, max_period: float, nfreq: int = 2000):
    """Astropy LombScargle. Power is the standard normalization."""
    t = np.asarray(jd, dtype=float)
    y = np.asarray(mag, dtype=float)
    mask = np.isfinite(t) & np.isfinite(y)
    t = t[mask]
    y = y[mask]
    if len(t) < 8:
        return None
    if not (0 < min_period < max_period):
        raise ValueError("Period search needs 0 < min period < max period")
    ls = LombScargle(t, y, normalization="standard")
    frequency, power = ls.autopower(
        minimum_frequency=1.0 / max_period,
        maximum_frequency=1.0 / min_period,
        samples_per_peak=8,
    )
    best = int(np.argmax(power))
    period = 1.0 / float(frequency[best])
    bright = int(np.argmin(y))
    try:
        fap = float(ls.false_alarm_probability(power[best], minimum_frequency=1.0 / max_period, maximum_frequency=1.0 / min_period))
    except Exception:
        fap = float("nan")
    edge = None
    if period >= 0.9 * max_period:
        edge = "max"
    elif period <= 1.05 * min_period:
        edge = "min"
    return {
        "period_days": period,
        "edge": edge,
        "min_period": float(min_period),
        "max_period": float(max_period),
        "power": float(power[best]),
        "fap": fap,
        "epoch_jd": float(t[bright]),
        "baseline_days": float(t.max() - t.min()),
        "freqs": np.asarray(frequency),
        "power_spectrum": np.asarray(power),
    }


def _field(text, limit: int = 30) -> str:
    text = str(text if text is not None else "").replace(",", " ").strip()
    return text[:limit] if text else "na"


def flip_offsets(observations: list[Obs], values: list[float], use: list[bool] | None = None,
                 min_points: int = 5) -> dict[str, dict]:
    """Each night's meridian-flip step: median after the flip minus median before it.

    Only nights with at least min_points usable points on both sides get an offset.
    Returns {night: {"offset": mag, "n_before": n, "n_after": n}}.
    """
    before: dict[str, list[float]] = {}
    after: dict[str, list[float]] = {}
    for i, (obs, v) in enumerate(zip(observations, values)):
        if not math.isfinite(v) or (use is not None and not use[i]):
            continue
        (after if obs.segment == "flip" else before).setdefault(obs.night, []).append(v)
    out = {}
    for night, post in after.items():
        pre = before.get(night, [])
        if len(pre) >= min_points and len(post) >= min_points:
            out[night] = {"offset": float(np.median(post) - np.median(pre)), "n_before": len(pre), "n_after": len(post)}
    return out


def align_flips(observations: list[Obs], values: list[float], offsets: dict[str, dict]) -> list[float]:
    """Move each night's after-flip points onto that night's before-flip level. Nights are not
    moved against each other, so slow changes from night to night survive (unlike
    normalize_segments, which lines up every night as well)."""
    return [v - offsets[obs.night]["offset"] if obs.segment == "flip" and obs.night in offsets else v
            for obs, v in zip(observations, values)]


# ---- Choosing comparison stars after the fact (1.8) -------------------------------------

def fill_base_values(observations: list[Obs], comp_catalog_mean: float | None) -> int:
    """Series reduced before 1.8 lack the target's instrumental magnitude. It follows from the
    stored magnitude, the comps' instrumental mean, and their catalog mean:
    target_inst = mag - comp_cat + cmag. Only call this on points that still carry the
    original comps (they always do until a comp selection is applied). Returns how many were filled."""
    filled = 0
    for obs in observations:
        if not math.isfinite(obs.comp_cat) and comp_catalog_mean is not None and math.isfinite(comp_catalog_mean):
            obs.comp_cat = float(comp_catalog_mean)
        if not math.isfinite(obs.target_inst) and math.isfinite(obs.comp_cat) and math.isfinite(obs.mag) \
                and math.isfinite(obs.cmag):
            obs.target_inst = obs.mag - obs.comp_cat + obs.cmag
            filled += 1
    return filled


def star_inst(obs: Obs, name: str, comp_names: list[str], aliases: dict | None = None) -> float:
    """Instrumental magnitude of a comp (by its position in comp_names) or a spare, NaN if absent.
    aliases (2.2.3): {name: [other catalog names of the same star]}; when the star was not measured under `name`
    in this frame, its measurement under another name is used (one star, labeled from two catalogs)."""
    v = _own_inst(obs, name, comp_names)
    if not math.isfinite(v) and aliases:
        for other in aliases.get(name, ()):
            v = _own_inst(obs, other, comp_names)
            if math.isfinite(v):
                break
    return v


def check_instrumental(obs: Obs) -> float:
    """2.2.14 (item 8): the check star's instrumental differential magnitude (check minus the comp ensemble, both
    instrumental), NaN when the check was not measured. Used when the check has no catalog magnitude, so there is
    no reduced check magnitude (check_std) to plot; it still shows whether the check stayed steady."""
    k, c = obs.kmag, obs.cmag
    if k is None or c is None or not math.isfinite(k) or not math.isfinite(c):
        return float("nan")
    return float(k - c)


ZP_WARN_MAG = 0.025      # 2.2.14 (item 10): "a few hundredths"
ZP_NEAR_MAG = 1.0        # comps within this many magnitudes count as "near the target's brightness"


def ensemble_zero_point_check(observations: list[Obs], selection: list[str], comp_names: list[str],
                              catalog: dict, aliases: dict | None = None, limit: float = ZP_WARN_MAG,
                              near: float = ZP_NEAR_MAG) -> dict | None:
    """2.2.14 (item 10): does the chosen comp ensemble move the night's level compared with the comps nearest the
    target's brightness?

    Faint comps can carry a color term, a non-linearity or a sky-subtraction bias that bright comps do not, and
    in an ensemble that shifts the whole night (13 Oct, HD 219134: 6 comps incl. 11-13 mag stars gave 5.383, the
    8.7 mag comp alone 5.501). The reference set is the chosen comps within `near` mag of the target's median;
    when no comp is that close (a bright target), within `near` mag of the comp nearest the target in brightness.
    Comps of the run that are not in use count only when no chosen comp is at that level. Frame by frame the target is reduced against both sets (only frames without
    flags, with every star measured), and the night's median difference is the shift.

    Returns None when there is nothing to compare (fewer than two chosen comps with catalog magnitudes, or the
    reference set is the chosen set), else {"reference": [names], "selection": [names], "nights": {night:
    {"shift", "level_sel", "level_ref", "sigma_sel", "sigma_ref", "n"}}, "warn": [nights over the limit],
    "text": [one plain line per warned night]}. Positive shift = the chosen ensemble makes the target fainter."""
    def cat(n):
        try:
            v = float(catalog.get(n))
        except (TypeError, ValueError):
            return float("nan")
        return v

    sel = [n for n in selection if math.isfinite(cat(n))]
    if len(sel) < 2:
        return None
    pool = list(dict.fromkeys(sel + [n for n in comp_names if math.isfinite(cat(n))]))
    clean = [o for o in observations if not o.flag and math.isfinite(o.mag)]
    if not clean:
        return None
    target = float(np.median([o.mag for o in clean]))
    # The brightness level to compare at: the target's, or (a bright target) the nearest comp's.
    if any(abs(cat(n) - target) <= near for n in pool):
        anchor = target
    else:
        # 2.2.14 review 2: the chosen comps set the level first, so a comp dropped on purpose does not come back.
        anchor = cat(min(sel, key=lambda n: abs(cat(n) - target)))
    # 2.2.14 review: comps left out on purpose (Use comps…) are not the reference while a chosen comp is at that
    # level; they come back (named "not in use") only when none of the chosen comps is.
    ref = [n for n in sel if abs(cat(n) - anchor) <= near]
    if not ref:
        ref = [n for n in pool if abs(cat(n) - anchor) <= near]
    if not ref or set(ref) == set(sel):
        return None
    sel_cat = float(np.mean([cat(n) for n in sel]))
    ref_cat = float(np.mean([cat(n) for n in ref]))
    by_night: dict = {}
    for o in clean:
        if not math.isfinite(o.target_inst):
            continue
        si = [star_inst(o, n, comp_names, aliases) for n in sel]
        ri = [star_inst(o, n, comp_names, aliases) for n in ref]
        if not all(math.isfinite(v) for v in si + ri):
            continue
        by_night.setdefault(o.night, []).append((sel_cat + o.target_inst - float(np.mean(si)),
                                                 ref_cat + o.target_inst - float(np.mean(ri))))
    out = {"reference": ref, "selection": sel, "nights": {}, "warn": [], "text": []}
    for night, pairs in by_night.items():
        if len(pairs) < 5:
            continue
        a = np.array(pairs)
        shift = float(np.median(a[:, 0] - a[:, 1]))
        out["nights"][night] = {"shift": shift, "level_sel": float(np.median(a[:, 0])),
                                "level_ref": float(np.median(a[:, 1])), "sigma_sel": robust_sigma(a[:, 0]),
                                "sigma_ref": robust_sigma(a[:, 1]), "n": len(pairs)}
        if abs(shift) > limit:
            out["warn"].append(night)
    if not out["nights"]:
        return None
    ref_text = ", ".join(f"{n} (V {cat(n):.2f}{'' if n in sel else ', not in use'})" for n in ref)
    for night in sorted(out["warn"]):
        r = out["nights"][night]
        out["text"].append(
            f"{night}: the {len(sel)} chosen comps put the target at {r['level_sel']:.3f} (σ {r['sigma_sel']:.3f}); "
            f"the comp(s) nearest its brightness, {ref_text}, at {r['level_ref']:.3f} (σ {r['sigma_ref']:.3f}): "
            f"{r['shift']:+.3f} mag apart. Comps much fainter than the target can shift the zero point (color, "
            "linearity, sky); compare both sets in Use comps… before reporting.")
    return out


def _own_inst(obs: Obs, name: str, comp_names: list[str]) -> float:
    if name in comp_names:
        k = comp_names.index(name)
        if k < len(obs.comp_insts) and math.isfinite(obs.comp_insts[k]):
            return float(obs.comp_insts[k])
    v = obs.spare_insts.get(name)
    return float(v) if v is not None else float("nan")


def same_star_aliases(coords: dict, tol_arcsec: float = 3.0) -> dict:
    """2.2.3: names that are one star in two catalogs (e.g. TYC 4006-956-1 and APASS J231144.7+570632).

    coords: {name: (ra_deg, dec_deg)}. Two names within `tol_arcsec` are the same star. Returns
    {name: [the other names of that star]} for names with at least one alias."""
    items = [(n, float(rd[0]), float(rd[1])) for n, rd in coords.items()
             if rd and rd[0] is not None and rd[1] is not None
             and math.isfinite(float(rd[0])) and math.isfinite(float(rd[1]))]
    out: dict[str, list[str]] = {}
    for i, (a, ra1, de1) in enumerate(items):
        for b, ra2, de2 in items[i + 1:]:
            dra = (ra1 - ra2) * math.cos(math.radians((de1 + de2) / 2.0))
            if math.hypot(dra, de1 - de2) * 3600.0 <= tol_arcsec:
                out.setdefault(a, []).append(b)
                out.setdefault(b, []).append(a)
    return out


def ensemble_merr(obs: Obs, selection: list[str], comp_names: list[str], aliases: dict | None = None) -> float:
    """2.2.3: the photon-noise error of a point when the comparison is the mean of `selection`.

    The target's variance and each original comp's were stored at photometry time. A spare's variance was not, so
    it is scaled from the nearest-in-brightness original comp as photon noise scales (variance x 10^(0.4 dm)), which
    is close for stars well above the sky. NaN when the pieces are missing (series saved before 2.2.3), so the caller
    keeps the old error."""
    if not math.isfinite(obs.target_var) or not obs.comp_vars:
        return float("nan")
    refs = [(float(obs.comp_insts[k]), float(obs.comp_vars[k])) for k in range(min(len(obs.comp_insts), len(obs.comp_vars)))
            if math.isfinite(obs.comp_insts[k]) and math.isfinite(obs.comp_vars[k]) and obs.comp_vars[k] > 0]
    if not refs:
        return float("nan")
    total = 0.0
    for name in selection:
        if (name in comp_names and comp_names.index(name) < len(obs.comp_vars)
                and math.isfinite(_own_inst(obs, name, comp_names))):
            v = float(obs.comp_vars[comp_names.index(name)])
        else:
            m = star_inst(obs, name, comp_names, aliases)
            if not math.isfinite(m):
                return float("nan")
            m_ref, v_ref = min(refs, key=lambda r: abs(r[0] - m))
            v = v_ref * 10 ** (0.4 * (m - m_ref))
        if not math.isfinite(v):
            return float("nan")
        total += v
    # 2.2.17 (item 8, review): a point keeps the model it was measured with: the fixed 0.005 mag only for points
    # reduced before 2.2.17 (e_model 0), so a comps change never invents a third error model.
    floor = OLD_ERR_FLOOR ** 2 if not getattr(obs, "e_model", 0) else 0.0
    return math.sqrt(obs.target_var + total / len(selection) ** 2 + floor)


def recompute_with_comps(observations: list[Obs], selection: list[str], comp_names: list[str],
                         catalog: dict[str, float], aliases: dict | None = None) -> dict:
    """Re-derive every magnitude (and the check star) from the chosen comps.

    selection: names of the comps / spares to use as the ensemble.
    catalog: catalog magnitude for each name, in the band measured.
    A frame missing any chosen star gets mag NaN and the flag 'nocomp'.
    Returns {"done": n, "missing": n, "missing_nights": [...]}.
    """
    if not selection:
        raise ValueError("Choose at least one comparison star")
    for name in selection:
        if catalog.get(name) is None or not math.isfinite(catalog[name]):
            raise ValueError(f"{name} has no catalog magnitude")
    ens_cat = float(np.mean([catalog[n] for n in selection]))
    done = missing = 0
    missing_nights = set()
    for obs in observations:
        words = [w for w in obs.flag.split() if w != "nocomp"]
        insts = [star_inst(obs, n, comp_names, aliases) for n in selection]
        if not math.isfinite(obs.target_inst) or not all(math.isfinite(v) for v in insts):
            obs.mag = float("nan")
            words.append("nocomp")
            missing += 1
            missing_nights.add(obs.night)
        else:
            ens = float(np.mean(insts))
            obs.mag = ens_cat + obs.target_inst - ens
            obs.cmag = ens
            obs.comp_cat = ens_cat
            obs.n_comps = len(selection)
            new_err = ensemble_merr(obs, selection, comp_names, aliases)
            if math.isfinite(new_err):
                obs.merr = new_err
            if obs.kmag is not None and math.isfinite(obs.kmag):
                obs.check_std = ens_cat + obs.kmag - ens
            done += 1
        obs.flag = " ".join(words)
    return {"done": done, "missing": missing, "missing_nights": sorted(missing_nights)}


def apply_catalog_mags(observations: list[Obs], changes: dict, comp_sets: dict | None = None,
                       default_set: list[str] | None = None, aliases: dict | None = None) -> dict:
    """2.2.18 (item 3): shift the points for catalog magnitudes typed in Use comps… (whole series).

    changes: {star: (old catalog mag, new catalog mag)}. A point's comparison is the mean of its comp set: comp_sets
    {night: [names]} (the comps that night was reduced with), else default_set. Its mag, comp_cat and check_std move
    by sum over the changed stars in that set of (new - old) / len(set): only the catalog deltas, never a mean
    rebuilt from positional comp_insts (a night reduced with other comps than the series' last run). Errors, flags
    and instrumental values stay as they are; points flagged nocomp or without a finite comp_cat are left alone.
    Returns {"changed": points moved, "shift": {night: median shift (mag)}}."""
    deltas = {}
    for name, pair in (changes or {}).items():
        try:
            d = float(pair[1]) - float(pair[0])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(d) and d != 0.0:
            deltas[name] = d

    def delta_of(name):
        if name in deltas:
            return deltas[name]
        for other in (aliases or {}).get(name, ()):
            if other in deltas:
                return deltas[other]
        return 0.0

    changed = 0
    shifts: dict = {}
    for obs in observations:
        if not math.isfinite(obs.comp_cat) or "nocomp" in obs.flag.split():
            continue
        names = list((comp_sets or {}).get(obs.night) or default_set or [])
        if not names:
            continue
        d = sum(delta_of(n) for n in names) / len(names)
        if abs(d) < 1e-12:
            continue
        obs.comp_cat = float(obs.comp_cat) + d
        if math.isfinite(obs.mag):
            obs.mag = float(obs.mag) + d
        if obs.check_std is not None and math.isfinite(obs.check_std):
            obs.check_std = float(obs.check_std) + d
        changed += 1
        shifts.setdefault(obs.night, []).append(d)
    return {"changed": changed, "shift": {n: float(np.median(v)) for n, v in shifts.items()}}


# ---- Vetting comparison stars against known and suspected variables (2.0) ---------------

RED_LIMIT_BV = 1.5
# SIMBAD object types that mean "varies or probably varies". Condensed labels, as SIMBAD shows them.
VARIABLE_OTYPES = {
    "V*": "variable star", "V*?": "suspected variable", "Ir*": "irregular variable", "Er*": "eruptive variable",
    "Ro*": "rotating variable", "BY*": "BY Dra variable (spotted star)", "RS*": "RS CVn variable",
    "Pu*": "pulsating variable", "RR*": "RR Lyrae variable", "Ce*": "Cepheid", "cC*": "classical Cepheid",
    "WV*": "type II Cepheid", "dS*": "delta Scuti variable", "SX*": "SX Phe variable", "gD*": "gamma Dor variable",
    "bC*": "beta Cep variable", "RV*": "RV Tauri variable", "LP*": "long-period variable", "Mi*": "Mira variable",
    "sr*": "semi-regular variable", "SR*": "semi-regular variable", "EB*": "eclipsing binary",
    "Al*": "Algol-type eclipsing binary", "bL*": "beta Lyr eclipsing binary", "WU*": "W UMa eclipsing binary",
    "El*": "ellipsoidal variable", "Fl*": "flare star", "Or*": "Orion-type variable", "TT*": "T Tauri star",
    "Ae*": "Herbig Ae/Be star", "CV*": "cataclysmic variable", "No*": "nova", "AB*": "AGB red giant (these usually vary)",
    "C*": "carbon star (these usually vary)", "S*": "S-type star (these usually vary)",
    "LP?": "long-period variable candidate", "Mi?": "Mira candidate", "RR?": "RR Lyrae candidate",
    "Ce?": "Cepheid candidate", "EB?": "eclipsing binary candidate", "BY?": "BY Dra candidate",
    "RS?": "RS CVn candidate", "Pu?": "pulsating variable candidate", "AB?": "AGB red giant candidate (these usually vary)",
    "C*?": "carbon star candidate", "S*?": "S-type star candidate", "CV?": "cataclysmic variable candidate",
    "TT?": "T Tauri candidate", "Ae?": "Herbig Ae/Be candidate", "sv?": "suspected variable",
}


# 2.2.14 (item 1): a star's color comes from ONE catalog, never B from one and V from another (2.2.13's merge picks
# each band separately, so B could be Gaia's and V APASS's, and V-R was nearly always APASS V against Gaia R; the
# mixed zero points add scatter and a bias to the color fit and the comps' catalog colors). B-V: the AAVSO sequence
# or APASS when that catalog has both bands, else Gaia's synthetic Johnson B and V (GSPC), else Tycho-2 (B and V both
# from BT, VT). V-R: the AAVSO sequence when it has V and Rc, else Gaia (APASS has no R). The magnitude itself (V)
# keeps MAG_ORDER.
COLOR_BANDS = {"bv": ("B", "V"), "vr": ("V", "R")}
COLOR_ORDER = {"bv": ("AAVSO sequence", "APASS DR9", "Gaia DR3", "Tycho-2"),
               "vr": ("AAVSO sequence", "Gaia DR3")}
APASS_SATURATES_V = 10.0   # review: brighter than this, Gaia GSPC's B-V comes before APASS's


def _pair_from(mags: dict, first: str, second: str):
    try:
        a, b = float(mags[first]), float(mags[second])
    except (KeyError, TypeError, ValueError):
        return None
    return a - b if math.isfinite(a) and math.isfinite(b) else None


def catalog_color(star: dict, key: str = "bv") -> tuple[float | None, str]:
    """2.2.14 (item 1): (color, catalog it came from) for key "bv" or "vr", both bands from one catalog; (None, "")
    when no catalog has both. Reads, in turn: a stored pick ("colors": {key: [value, catalog]}, kept on comps, spares
    and watch stars), a merged star's per-catalog entries ("sources"), or a single catalog's own "mags"."""
    if not star:
        return None, ""
    first, second = COLOR_BANDS[key]
    if isinstance(star.get("colors"), dict):
        # A stored pick is final, also when it is empty (no catalog had both bands): never fall back to the merged
        # "mags", whose bands may come from different catalogs.
        pick = star["colors"].get(key)
        if isinstance(pick, (list, tuple)) and len(pick) == 2 and pick[0] is not None:
            return float(pick[0]), str(pick[1] or "")
        return None, ""
    src = star.get("sources")
    if isinstance(src, dict) and src:
        order = COLOR_ORDER[key]
        apass_v = ((src.get("APASS DR9") or {}).get("mags") or {}).get("V")
        if apass_v is not None and float(apass_v) < APASS_SATURATES_V and "Gaia DR3" in order:
            # review: APASS saturates brighter than about V 10; Gaia's GSPC is the better B-V there.
            order = tuple(c for c in order if c != "APASS DR9")
            order = order[:order.index("Gaia DR3") + 1] + ("APASS DR9",) + order[order.index("Gaia DR3") + 1:]
        for cat in order:
            entry = src.get(cat)
            if not entry:
                continue
            value = _pair_from(entry.get("mags") or {}, first, second)
            if value is not None:
                return value, cat
        return None, ""
    # One catalog's own star (not merged): its own bands are one catalog by construction.
    cat = str(star.get("catalog") or "")
    if cat and cat not in COLOR_ORDER[key] and cat != "AAVSO":
        return None, ""
    value = _pair_from(star.get("mags") or {}, first, second)
    return (value, cat) if value is not None else (None, "")


def star_colors(star: dict) -> dict:
    """2.2.14 (item 1): {"bv": [value, catalog], "vr": [...]} for the keys a star has, ready to store (JSON)."""
    out = {}
    for key in COLOR_BANDS:
        value, cat = catalog_color(star, key)
        if value is not None:
            out[key] = [round(float(value), 4), cat]
    return out


def catalog_bv(star: dict) -> float | None:
    """B-V from one catalog (2.2.14 item 1: see catalog_color)."""
    return catalog_color(star, "bv")[0]


def fetch_simbad_variables(ra_deg: float, dec_deg: float, radius_deg: float) -> list[dict]:
    """Known and suspected variables in a circle, from SIMBAD's TAP service (one query per field)."""
    import csv
    import io
    import urllib.parse

    types = ", ".join("'" + t.replace("'", "''") + "'" for t in VARIABLE_OTYPES)
    adql = (
        "SELECT main_id, ra, dec, otype FROM basic "
        f"WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra_deg:.6f}, {dec_deg:.6f}, {radius_deg:.4f})) = 1 "
        f"AND otype IN ({types})"
    )
    query = urllib.parse.urlencode({"request": "doQuery", "lang": "adql", "format": "csv", "query": adql})
    text = _http_get("https://simbad.cds.unistra.fr/simbad/sim-tap/sync?" + query, timeout=60).decode("utf-8", "replace")
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        ra, dec = _num(row.get("ra")), _num(row.get("dec"))
        otype = (row.get("otype") or "").strip()
        if ra is None or dec is None:
            continue
        out.append({"name": (row.get("main_id") or "").strip(), "type": otype, "source": "SIMBAD",
                    "desc": VARIABLE_OTYPES.get(otype, otype), "ra": ra, "dec": dec})
    return out


def field_variables(ra_deg: float, dec_deg: float, radius_deg: float) -> tuple[list[dict], list[str]]:
    """Known and suspected variables from AAVSO VSX and SIMBAD. Returns (variables, sources that answered)."""
    found, answered = [], []
    try:
        for v in fetch_vsx_cone(ra_deg, dec_deg, radius_deg):
            per = f", P = {v['period']:g} d" if v.get("period") else ""
            found.append({"name": v["name"], "type": v.get("type", ""), "source": "VSX",
                          "desc": f"VSX {v.get('type', '') or 'variable'}{per}", "ra": v["ra"], "dec": v["dec"],
                          "period": v.get("period")})   # 2.2.19 (#19): for the period-range check
        answered.append("VSX")
    except Exception:
        pass
    try:
        found.extend(fetch_simbad_variables(ra_deg, dec_deg, radius_deg))
        answered.append("SIMBAD")
    except Exception:
        pass
    return found, answered


def flag_variables(stars: list[dict], variables: list[dict], tol_arcsec: float = 6.0) -> int:
    """Mark catalog stars that sit on a known or suspected variable: star['variable'] = [matches]."""
    flagged = 0
    # 2.2.14 (item 5): one numpy pass per variable over all stars (a deeper Gaia list made the star x variable
    # loop in Python take seconds in a Milky Way field); the same separation formula as sky_separation_arcsec.
    have = [k for k, st in enumerate(stars) if st.get("ra") is not None and st.get("dec") is not None]
    ra = np.array([float(stars[k]["ra"]) for k in have], dtype=float)
    dec = np.array([float(stars[k]["dec"]) for k in have], dtype=float)
    near: dict[int, list] = {}
    for v in variables:
        try:
            vra, vdec = float(v["ra"]), float(v["dec"])
        except (KeyError, TypeError, ValueError):
            continue
        d_ra = np.radians(((ra - vra + 180.0) % 360.0) - 180.0) * np.cos(np.radians((dec + vdec) / 2.0))
        sep = np.degrees(np.hypot(d_ra, np.radians(dec - vdec))) * 3600.0
        for i in np.nonzero(sep <= tol_arcsec)[0]:
            near.setdefault(int(have[i]), []).append(v)
    for k, star in enumerate(stars):
        if star.get("ra") is None or star.get("dec") is None:
            continue
        hits = near.get(k, [])
        if hits:
            star["variable"] = hits
            flagged += 1
        else:
            star.pop("variable", None)
    return flagged


def variable_text(star: dict) -> str:
    """'SIMBAD LP? (long-period variable candidate); VSX ...' for a flagged star, '' otherwise."""
    parts = []
    for v in star.get("variable") or []:
        label = f"{v['source']}: {v['name']}"
        if v["source"] == "SIMBAD":
            label += f" {v['type']} ({v['desc']})"
        else:
            label += f" ({v['desc']})"
        parts.append(label)
    return "; ".join(parts)


# ---- Aperture suggestion (2.0) ------------------------------------------------------------

def suggest_apertures(fwhm: float) -> dict:
    """Aperture radius 1.8 x FWHM, sky ring starting at the largest of r + 3, 3.2 x FWHM and r + 1.5 x FWHM, and a sky
    ring with four times the aperture's area."""
    r = max(3.0, round(1.8 * fwhm, 1))
    sky_in = sky_inner_radius(r, fwhm)
    sky_out = float(math.ceil(math.sqrt(sky_in ** 2 + 4.0 * r ** 2)))
    return {"radius": r, "sky_in": sky_in, "sky_out": sky_out}


def sky_inner_radius(r: float, fwhm: float) -> float:
    """2.2.16 (item 1): the sky ring starts at least 1.5 x FWHM outside the aperture (and r + 3, 3.2 x FWHM as before),
    so the star's wings stay out of the sky; the outer radius then keeps the ring's area (4 x the aperture's)."""
    return float(math.ceil(max(r + 3.0, 3.2 * fwhm, r + 1.5 * fwhm)))


def robust_sigma(values) -> float:
    v = np.asarray([x for x in values if x is not None and math.isfinite(x)], dtype=float)
    if v.size < 5:
        return float("nan")
    return float(1.4826 * np.median(np.abs(v - np.median(v))))


DEFAULT_MIN_PERIOD = 0.02   # 2.2.19 (#19): days; many HADS / SX Phe stars have periods of 0.03-0.05 d


def period_range_check(expected, lo: float, hi: float):
    """2.2.19 (#19): expected periods ([(label, days)], e.g. the VSX period and the "Mark periods") that lie outside
    the search range lo-hi. Returns (outside [(label, days)], suggested (lo, hi) that covers them, or None). The
    suggested minimum is DEFAULT_MIN_PERIOD (or 0.8 x a still shorter period), the maximum 1.25 x a longer one."""
    out = []
    for label, p in expected or ():
        try:
            p = float(p)
        except (TypeError, ValueError):
            continue
        if math.isfinite(p) and p > 0 and not (lo <= p <= hi):
            out.append((label, p))
    if not out:
        return [], None
    shortest = min(p for _l, p in out)
    longest = max(p for _l, p in out)
    new_lo = lo if shortest >= lo else min(DEFAULT_MIN_PERIOD, float(f"{0.8 * shortest:.2g}"))
    new_hi = hi if longest <= hi else float(f"{1.25 * longest:.2g}")
    return out, (new_lo, new_hi)


def period_edge_text(ls: dict) -> str:
    """A caution when the best period sits at the edge of the search range, or '' when it does not."""
    if not ls or not ls.get("edge"):
        return ""
    if ls["edge"] == "max":
        return (f"The best period ({ls['period_days']:.4g} d) is at the long end of the search (max "
                f"{ls['max_period']:g} d). The true period may be longer, or this is a slow trend rather than a "
                "period. Raise the max period and refit; longer periods need more nights.")
    return (f"The best period ({ls['period_days']:.4g} d) is at the short end of the search (min "
            f"{ls['min_period']:g} d). The true period may be shorter; lower the min period and refit "
            "(the frames must be closer together than half the period).")


def aperture_test_stats(mags_by_r: dict, ref_label: str, has_comp2: bool, boot: int = 400, seed: int = 1) -> dict:
    """Scatter of the reference star (check star, comp 2, or target) and the target against the comps, for each
    aperture, with standard errors and how often each aperture is lowest when the frames are resampled.

    mags_by_r: {radius: array (frames x 4) of instrumental mags for target, comp, comp 2, check}.
    The same frames are resampled for every aperture, so the comparison is paired."""
    radii = sorted(mags_by_r)
    if not radii:
        return {"rows": [], "boot": 0}
    n = min(len(mags_by_r[r]) for r in radii)

    def series(m):
        comps = m[:, 1] if (not has_comp2 or ref_label == "comp 2") else np.nanmean(m[:, 1:3], axis=1)
        other = m[:, 3] if ref_label == "check star" else (m[:, 2] if ref_label == "comp 2" else m[:, 0])
        return other - comps, m[:, 0] - comps

    ref = np.array([series(mags_by_r[r][:n])[0] for r in radii])
    tgt = np.array([series(mags_by_r[r][:n])[1] for r in radii])
    rng = np.random.default_rng(seed)
    ref_boot = np.full((boot, len(radii)), np.nan)
    tgt_boot = np.full((boot, len(radii)), np.nan)
    for b in range(boot):
        idx = rng.integers(0, n, n)
        for k in range(len(radii)):
            ref_boot[b, k] = robust_sigma(ref[k, idx])
            tgt_boot[b, k] = robust_sigma(tgt[k, idx])

    def wins(arr):
        ok = np.all(np.isfinite(arr), axis=1)
        if not ok.any():
            return np.zeros(arr.shape[1])
        lowest = np.argmin(arr[ok], axis=1)
        return np.bincount(lowest, minlength=arr.shape[1]) / ok.sum()

    ref_win, tgt_win = wins(ref_boot), wins(tgt_boot)
    rows = []
    for k, r in enumerate(radii):
        rows.append({"r": r, "n": int(np.isfinite(ref[k]).sum()),
                     "ref_sigma": robust_sigma(ref[k]), "ref_err": float(np.nanstd(ref_boot[:, k])),
                     "ref_win": float(ref_win[k]),
                     "target_sigma": robust_sigma(tgt[k]), "target_err": float(np.nanstd(tgt_boot[:, k])),
                     "target_win": float(tgt_win[k])})
    finite = [row for row in rows if math.isfinite(row["ref_sigma"])]
    tfinite = [row for row in rows if math.isfinite(row["target_sigma"])]
    return {"rows": rows, "boot": boot,
            "ref_best": max(finite, key=lambda row: row["ref_win"]) if finite else None,
            "target_best": max(tfinite, key=lambda row: row["target_win"]) if tfinite else None}


def pick_spare_comps(placed: list, avoid_xy: list, shape: tuple[int, int], radius: float, sky_outer: float,
                     mag_range: tuple[float, float], max_spares: int = 8,
                     reasons: dict | None = None) -> list[tuple[float, float, dict]]:
    """Labeled chart stars to measure as spare comps: a catalog magnitude in mag_range, inside
    the frame, well away from the target, comps, and check star, and with no catalog star
    brighter than 3 mag fainter within the sky annulus. Brightest first.
    reasons (2.2.2), when given, counts why stars in the magnitude range were turned away."""
    h, w = shape
    edge = sky_outer + 6
    lo, hi = mag_range
    out = []

    def turned_away(why):
        if reasons is not None:
            reasons[why] = reasons.get(why, 0) + 1

    # 2.2.14 (items 1, 5): the crowding test for all stars at once (k-d tree); with a deeper Gaia list the per-star
    # loop over every placed star became slow.
    crowded_all = crowded_flags(np.array([(float(x), float(y)) for x, y, _st in placed], dtype=float).reshape(-1, 2),
                                np.array([_float_or_nan(st.get("mag")) for _x, _y, st in placed]),
                                sky_outer + radius, 3.0) if placed else np.zeros(0, dtype=bool)
    for k, (x, y, star) in enumerate(placed):
        mag = star.get("mag")
        if mag is None or not (lo <= mag <= hi):
            continue
        if star.get("variable"):
            turned_away("known variable")
            continue
        if (catalog_bv(star) or 0.0) > RED_LIMIT_BV:
            turned_away("very red")
            continue
        if not (edge <= x < w - edge and edge <= y < h - edge):
            turned_away("near the edge")
            continue
        if any(p is not None and math.hypot(x - p[0], y - p[1]) < 2 * sky_outer for p in avoid_xy):
            turned_away("too close to the target, comps or check")
            continue
        if crowded_all[k]:
            turned_away("crowded (a neighbor in the sky ring)")
            continue
        out.append((x, y, star))
    out.sort(key=lambda item: item[2]["mag"])
    return out[:max_spares]


def aavso_report(
    observations: list[Obs],
    observer: str,
    star_id: str,
    comp,
    check: Star | None,
    chart: str,
    software: str = "Shiloh Hill Observatory - Photometry (SHOBS-P)",
    notes: str = "OSC reduced; not transformed",
    filt: str = "CV",
    scint: dict | None = None,
) -> str:
    """AAVSO Extended File Format, comma delimited, DATE=JD.

    comp is one Star or a list of Stars. Two or more comps are reported as an ensemble
    (CNAME=ENSEMBLE, CMAG=na), which AAVSO requires to carry a check star.
    CMAG and KMAG are instrumental magnitudes, as the format asks.
    """
    comps = list(comp) if isinstance(comp, (list, tuple)) else [comp]
    ensemble = len(comps) > 1
    lines = [
        "#TYPE=Extended",
        f"#OBSCODE={observer.strip() or 'na'}",
        f"#SOFTWARE={software}",
        "#DELIM=,",
        "#DATE=JD",
        "#OBSTYPE=CCD",
        created_line(),
        "#NAME,DATE,MAG,MERR,FILT,TRANS,MTYPE,CNAME,CMAG,KNAME,KMAG,AMASS,GROUP,CHART,NOTES",
    ]
    chart_field = _field(chart, 20)
    cname = "ENSEMBLE" if ensemble else _field(comps[0].name if comps else "")
    kname = _field(check.name) if check is not None else "na"
    star = _field(star_id)
    base_notes = (notes or "").replace(",", ";").replace("|", ";").strip()
    if ensemble:
        base_notes = (base_notes + "; " if base_notes else "") + "ens " + " ".join(_field(c.name, 15) for c in comps)
    for obs in observations:
        if not math.isfinite(obs.mag):
            continue
        merr = point_err(obs, scint)
        merr = merr if math.isfinite(merr) else 0.0
        kmag = obs.kmag if (check is not None and obs.kmag is not None and math.isfinite(obs.kmag)) else None
        am = f"{obs.airmass:.4f}" if obs.airmass is not None and math.isfinite(obs.airmass) else "na"
        cmag = "na" if ensemble or not math.isfinite(obs.cmag) else f"{obs.cmag:.3f}"
        note = base_notes
        if obs.night:
            note = (note + "; " if note else "") + f"night {obs.night}"
        if obs.flag:
            note = (note + "; " if note else "") + obs.flag
        lines.append(
            ",".join(
                [
                    star,
                    f"{obs.jd:.5f}",
                    f"{obs.mag:.3f}",
                    f"{merr:.3f}",
                    obs.filt or filt,
                    "NO",
                    "STD",
                    cname,
                    cmag,
                    kname if kmag is not None else "na",
                    f"{kmag:.3f}" if kmag is not None else "na",
                    am,
                    "na",
                    chart_field,
                    note or "na",
                ]
            )
        )
    return "\n".join(lines) + "\n"


def _pos_fields(obs: Obs) -> list[str]:
    """target, comp 1, comp 2, check pixel positions (binned, debayered frame) for the CSV."""
    def xy(p):
        try:
            return [f"{float(p[0]):.1f}", f"{float(p[1]):.1f}"]
        except (TypeError, ValueError, IndexError):
            return ["", ""]
    pos = obs.pos or {}
    comps = list(pos.get("c") or [])
    return (xy(pos.get("t")) + xy(comps[0] if len(comps) > 0 else None)
            + xy(comps[1] if len(comps) > 1 else None) + xy(pos.get("k")))


def _extra_comp_pos_fields(obs: Obs, n_comps: int) -> list[str]:
    """2.2.14 (item 9): pixel positions of comps 3..n_comps, appended after the older columns so a reader that
    looks columns up by name (or by their old place) still works."""
    out = []
    comps = list((obs.pos or {}).get("c") or [])
    for k in range(2, n_comps):
        p = comps[k] if k < len(comps) else None
        try:
            out += [f"{float(p[0]):.1f}", f"{float(p[1]):.1f}"]
        except (TypeError, ValueError, IndexError):
            out += ["", ""]
    return out


def segment_words(observations: list[Obs]) -> list[str]:
    """2.2.2: the segment column in words: "before flip" / "after flip" on a night with a meridian flip,
    "no flip" on a night without one (the stored value stays "" or "flip")."""
    flipped = {o.night for o in observations if o.segment == "flip"}
    return ["after flip" if o.segment == "flip" else ("before flip" if o.night in flipped else "no flip")
            for o in observations]


def write_curve_csv(path: str, observations: list[Obs], notes: dict | None = None, scint: dict | None = None) -> None:
    corrected = apply_night_zero_point(observations) if observations else []
    n_comps = max([len((o.pos or {}).get("c") or []) for o in observations] + [2])   # 2.2.14 (item 9)
    with AsciiWriter(path) as fh:
        fh.write(created_line() + "\n")
        fh.write(
            "jd,mag,merr,filter,zero_pointed_mag,comp_inst_mag,check_inst_mag,check_reduced_mag,"
            "airmass,exptime,fwhm_px,peak,raw_peak,sky,flag,night,segment,segment_normalized_mag,"
            "b_minus_v,v_minus_r,b_minus_v_diff,v_minus_r_diff,color_calibration,"
            "target_x,target_y,comp1_x,comp1_y,comp2_x,comp2_y,check_x,check_y,night_note,file,scint_mag,err_total"
            # 2.2.14 (item 9): every comp's position, comps 3 and up appended at the end (old columns keep their place).
            + "".join(f",comp{k + 1}_x,comp{k + 1}_y" for k in range(2, n_comps))
            + ",frame"   # 2.2.17 (item 6): the frame's own number from its file name (as on the Blink page), last
            + "\n"
        )
        by_night, night_labels, dir_ok = {}, {}, {}
        for obs in observations:
            by_night.setdefault(obs.night, []).append(obs.path)

        def frame_of(obs):
            # The folder's labels; a night whose folder is gone gets frame_labels of its files once (not per point).
            folder = os.path.dirname(obs.path)
            if folder not in dir_ok:   # one directory scan per folder for the whole CSV
                dir_ok[folder] = _folder_frame_labels(folder) if os.path.isdir(folder) else None
            for labels in (dir_ok[folder] or ()):
                if obs.path in labels:
                    return labels[obs.path]
            if obs.night not in night_labels:
                night_labels[obs.night] = frame_labels(by_night[obs.night])
            return frame_label(obs.path, night_labels[obs.night])
        normalized = normalize_segments(observations) if observations else []
        seg_words = segment_words(observations)
        for k, (obs, zp, nm) in enumerate(zip(observations, corrected, normalized)):
            def num(value, fmt="{:.4f}"):
                return "" if value is None or not math.isfinite(value) else fmt.format(value)

            fh.write(
                ",".join([
                    f"{obs.jd:.6f}",
                    num(obs.mag),
                    num(obs.merr),
                    obs.filt,
                    num(zp),
                    num(obs.cmag),
                    num(obs.kmag),
                    num(obs.check_std),
                    num(obs.airmass),
                    f"{obs.exptime:.3f}",
                    num(obs.fwhm, "{:.2f}"),
                    num(obs.peak, "{:.0f}"),
                    num(obs.raw_peak, "{:.0f}"),
                    num(obs.sky, "{:.1f}"),
                    obs.flag.replace(",", " "),
                    obs.night,
                    seg_words[k],
                    num(nm),
                    num(obs.bv),
                    num(obs.vr),
                    num(obs.bv_diff),
                    num(obs.vr_diff),
                    obs.color_cal,
                    *_pos_fields(obs),
                    ((notes or {}).get(obs.night, "") or "").replace(",", ";"),
                    os.path.basename(obs.path).replace(",", "_"),
                    num(scint_mag(obs, scint)),
                    num(point_err(obs, scint)),
                    *_extra_comp_pos_fields(obs, n_comps),
                    frame_of(obs).replace(",", "_"),
                ])
                + "\n"
            )


def obs_to_dict(obs: Obs) -> dict:
    return {
        "jd": obs.jd,
        "mag": obs.mag,
        "merr": obs.merr,
        "cmag": obs.cmag,
        "kmag": obs.kmag,
        "airmass": obs.airmass,
        "path": obs.path,
        "exptime": obs.exptime,
        "flux_var": obs.flux_var,
        "flux_comp": obs.flux_comp,
        "night": obs.night,
        "filt": obs.filt,
        "fwhm": obs.fwhm,
        "elong": obs.elong,
        "peak": obs.peak,
        "sky": obs.sky,
        "shift": obs.shift,
        "saturated": obs.saturated,
        "drifted": obs.drifted,
        "check_std": obs.check_std,
        "flag": obs.flag,
        "segment": obs.segment,
        "raw_peak": obs.raw_peak,
        "bv": obs.bv,
        "vr": obs.vr,
        "bv_diff": obs.bv_diff,
        "vr_diff": obs.vr_diff,
        "comp_insts": list(obs.comp_insts),
        "color_cal": obs.color_cal,
        "target_inst": obs.target_inst,
        "comp_cat": obs.comp_cat,
        "spare_insts": dict(obs.spare_insts),
        "watch_insts": dict(obs.watch_insts),
        "pos": obs.pos,
        "target_var": obs.target_var,
        "comp_vars": list(obs.comp_vars),
        "n_comps": int(obs.n_comps or 0),
        "e_model": int(obs.e_model or 0),   # 2.2.17 (item 8)
    }


def obs_from_dict(raw: dict) -> Obs:
    return Obs(
        jd=float(raw["jd"]),
        mag=float(raw["mag"]),
        merr=float(raw["merr"]),
        cmag=float(raw["cmag"]),
        kmag=None if raw.get("kmag") is None else float(raw["kmag"]),
        airmass=None if raw.get("airmass") is None else float(raw["airmass"]),
        path=str(raw.get("path", "")),
        exptime=float(raw.get("exptime", 1.0)),
        flux_var=float(raw.get("flux_var", 0.0)),
        flux_comp=float(raw.get("flux_comp", 0.0)),
        night=str(raw.get("night", "")),
        filt=str(raw.get("filt", "CV")),
        fwhm=float(raw.get("fwhm", float("nan"))),
        elong=float(raw.get("elong", float("nan"))),
        peak=float(raw.get("peak", float("nan"))),
        sky=float(raw.get("sky", float("nan"))),
        shift=float(raw.get("shift", 0)),
        saturated=bool(raw.get("saturated", False)),
        drifted=bool(raw.get("drifted", False)),
        check_std=None if raw.get("check_std") is None else float(raw["check_std"]),
        flag=str(raw.get("flag", "")),
        segment=str(raw.get("segment", "")),
        raw_peak=float(raw.get("raw_peak", float("nan")) if raw.get("raw_peak") is not None else float("nan")),
        bv=_float_or_nan(raw.get("bv")),
        vr=_float_or_nan(raw.get("vr")),
        bv_diff=_float_or_nan(raw.get("bv_diff")),
        vr_diff=_float_or_nan(raw.get("vr_diff")),
        comp_insts=[_float_or_nan(v) for v in (raw.get("comp_insts") or [])],
        color_cal=str(raw.get("color_cal") or ""),
        target_inst=_float_or_nan(raw.get("target_inst")),
        comp_cat=_float_or_nan(raw.get("comp_cat")),
        spare_insts={str(k): _float_or_nan(v) for k, v in (raw.get("spare_insts") or {}).items()},
        watch_insts={str(k): _float_or_nan(v) for k, v in (raw.get("watch_insts") or {}).items()},
        pos=dict(raw.get("pos") or {}),
        target_var=_float_or_nan(raw.get("target_var")),
        comp_vars=[_float_or_nan(v) for v in (raw.get("comp_vars") or [])],
        n_comps=int(raw.get("n_comps") or 0),
        e_model=int(raw.get("e_model") or 0),   # 2.2.17 (item 8): missing = before 2.2.17
    )


def _float_or_nan(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def save_series(path: str, meta: dict, observations: list[Obs]) -> None:
    payload = {
        "version": 1,
        "meta": meta,
        "observations": [obs_to_dict(obs) for obs in observations],
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, path)


def load_series(path: str) -> tuple[dict, list[Obs]]:
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    observations = [obs_from_dict(raw) for raw in payload.get("observations", [])]
    observations.sort(key=lambda obs: obs.jd)
    return payload.get("meta", {}), observations


def merge_nights(existing: list[Obs], new_points: list[Obs]) -> list[Obs]:
    """Append a night, dropping a point already stored at the same JD."""
    kept = list(existing)
    seen = {round(obs.jd, 6) for obs in kept}
    for obs in new_points:
        key = round(obs.jd, 6)
        if key in seen:
            continue
        kept.append(obs)
        seen.add(key)
    kept.sort(key=lambda obs: obs.jd)
    return kept


DEMO_PERIOD_DAYS = 0.12


def write_demo_set(folder: str, n_lights: int = 40) -> dict:
    """Tiny synthetic OSC night: bias, dark, flat, and an RGGB light series with one variable.

    Raw star positions are (70, 90) variable, (140, 100) comparison, (110, 150) check.
    After a superpixel debayer they sit near (35, 45), (70, 50), (55, 75).
    """
    if fits is None:
        raise RuntimeError("astropy is required to write demo FITS")
    rng = np.random.default_rng(7)
    h, w = 180, 220
    bias_level = 100.0
    dark_rate = 0.2  # ADU per second
    light_exp = 30.0
    yy, xx = np.mgrid[0:h, 0:w]
    vignette = 1 - 0.25 * (((xx - w / 2) / w) ** 2 + ((yy - h / 2) / h) ** 2) * 4
    response = np.ones((h, w), dtype=np.float32)
    response[0::2, 0::2] = 0.8   # R
    response[1::2, 1::2] = 0.6   # B
    throughput = (vignette * response).astype(np.float32)

    def save(path, data, header=None):
        fits.PrimaryHDU(np.clip(data, 0, 65535).astype(np.uint16), header=header).writeto(path, overwrite=True)

    dirs = {}
    for kind in ("bias", "dark", "flat", "lights"):
        dirs[kind] = os.path.join(folder, kind)
        os.makedirs(dirs[kind], exist_ok=True)
    for i in range(5):
        hdr = fits.Header()
        hdr["EXPTIME"] = 0.0
        save(os.path.join(dirs["bias"], f"bias_{i:02d}.fits"), rng.normal(bias_level, 3, (h, w)), hdr)
    for i in range(5):
        hdr = fits.Header()
        hdr["EXPTIME"] = light_exp
        save(os.path.join(dirs["dark"], f"dark_{i:02d}.fits"), rng.normal(bias_level + dark_rate * light_exp, 3.2, (h, w)), hdr)
    for i in range(5):
        hdr = fits.Header()
        hdr["EXPTIME"] = 2.0
        hdr["BAYERPAT"] = "RGGB"
        signal = 22000 * throughput
        save(os.path.join(dirs["flat"], f"flat_{i:02d}.fits"), bias_level + dark_rate * 2 + rng.normal(signal, np.sqrt(signal)), hdr)

    t0 = datetime_to_jd(datetime(2026, 10, 2, 0, 30, 0, tzinfo=timezone.utc))
    for i in range(n_lights):
        jd_mid = t0 + i * 0.00625
        phase = ((jd_mid - t0) % DEMO_PERIOD_DAYS) / DEMO_PERIOD_DAYS
        delta_mag = 0.35 * math.cos(2 * math.pi * phase)
        sky = np.full((h, w), 300.0)
        stars = np.zeros((h, w))
        for sx, sy, flux in ((70, 90, 60000 * 10 ** (-0.4 * delta_mag)), (140, 100, 90000), (110, 150, 45000)):
            sigma = 1.6
            stars += flux / (2 * math.pi * sigma ** 2) * np.exp(-((xx - sx) ** 2 + (yy - sy) ** 2) / (2 * sigma ** 2))
        electrons = (sky + stars) * throughput
        img = bias_level + dark_rate * light_exp + rng.normal(electrons, np.sqrt(np.maximum(electrons, 1))) + rng.normal(0, 3, (h, w))
        hdr = fits.Header()
        hdr["EXPTIME"] = light_exp
        hdr["BAYERPAT"] = "RGGB"
        hdr["DATE-OBS"] = Time(jd_mid - light_exp / 2 / 86400.0, format="jd", scale="utc").isot
        hdr["RA_HRS"] = 0.75
        hdr["DEC"] = 40.0
        save(os.path.join(dirs["lights"], f"light_{i:03d}.fits"), img, hdr)
    return {
        "bias": dirs["bias"],
        "dark": dirs["dark"],
        "flat": dirs["flat"],
        "lights": dirs["lights"],
        "target_xy": (35.0, 45.0),
        "comp_xy": (70.0, 50.0),
        "check_xy": (55.0, 75.0),
        "period_days": DEMO_PERIOD_DAYS,
    }


# ---- Pre-calibrated frames ------------------------------------------------------------

def precalibration_signs(path: str, header: dict) -> list[str]:
    """Reasons to think a light frame has already been calibrated by other software."""
    import re

    reasons = []
    name = os.path.basename(path)
    if re.search(r"(^|[_\-. ])(cal|calibrated)([_\-. ]|$)", name, flags=re.IGNORECASE):
        reasons.append(f"file name '{name}' marks it as calibrated")
    elif re.search(r"_c(_[a-z]+)?\.xisf$", name):
        reasons.append(f"file name '{name}' ends in _c, PixInsight's mark for a calibrated frame")
    try:
        bitpix = int(header.get("BITPIX", 16))
    except (TypeError, ValueError):
        bitpix = 16
    if bitpix < 0:
        reasons.append("pixel data is floating point (a camera writes whole numbers)")
    for key in ("CALSTAT", "BIASSUB", "DARKSUB", "FLATCOR", "CALIBRAT"):
        if header.get(key) not in (None, "", False):
            reasons.append(f"header has {key} = {header.get(key)}")
    history = " ".join(str(header.get(k, "")) for k in ("HISTORY", "COMMENT")).lower()
    if "imagecalibration" in history or "calibrat" in history:
        reasons.append("header history mentions calibration")
    return reasons


# ---- Transparency ("cloud") flag ------------------------------------------------------

HAZE_FLAG = "haze"   # 2.2.14 (item 18): the flag word for transmission loss (dew/haze), shown in words in the app
HAZE_LABEL = "transmission loss (dew/haze?)"
HAZE_SHORT = "dew/haze?"   # 2.2.15 (item 4): the night table's short form (the log keeps HAZE_LABEL)


def _robust_std(values) -> float:
    v = np.asarray([x for x in values if x is not None and math.isfinite(x)], dtype=float)
    if v.size < 2:
        return float("nan")
    return float(1.4826 * np.median(np.abs(v - np.median(v))))


def _star_matrix(night_obs: list[Obs]) -> np.ndarray:
    """Frames x stars instrumental magnitudes of every measured star but the target: each comp, the check and the
    spares seen in at least half the frames. Old series without per-comp values fall back on the comp mean."""
    cols = []
    n_comp = max((len(o.comp_insts) for o in night_obs), default=0)
    for j in range(n_comp):
        cols.append([o.comp_insts[j] if j < len(o.comp_insts) else float("nan") for o in night_obs])
    if not cols:
        cols.append([o.cmag for o in night_obs])
    if sum(1 for o in night_obs if o.kmag is not None and math.isfinite(o.kmag)) >= 0.5 * len(night_obs):
        cols.append([o.kmag if o.kmag is not None else float("nan") for o in night_obs])
    counts: dict[str, int] = {}
    for o in night_obs:
        for name in o.spare_insts:
            counts[name] = counts.get(name, 0) + 1
    for name, n in sorted(counts.items()):
        if n >= 0.5 * len(night_obs):
            cols.append([o.spare_insts.get(name, float("nan")) for o in night_obs])
    data = np.array(cols, dtype=float).T
    data[~np.isfinite(data)] = np.nan
    return data


def light_loss_episodes(night_obs: list[Obs], limit: float = 0.20, min_frames: int = 6) -> list[dict]:
    """2.2.14 (item 18): the night's stretches of lost light (comp light more than `limit` below the night's median),
    each judged as cloud or as transmission loss (dew on the optics, haze).

    Transmission loss: every measured star (comps, check, spares) fades by the same amount, smoothly, and the sky
    stays flat. The differential magnitudes are then fine; only the noise grows. Cloud: the stars lose different
    amounts (patchy), the loss jumps from frame to frame, or the sky jumps or brightens (lit cloud). A stretch shorter
    than min_frames is called cloud. Each episode: {"kind": "transmission" | "cloud", "frames": [indices into the
    time-sorted night], "jd": (first, last), "depth": median loss in mag, "max_depth", "reasons": [...]}."""
    obs = sorted(night_obs, key=lambda o: o.jd)
    n = len(obs)
    rates = np.array([o.flux_comp / o.exptime if o.flux_comp > 0 and o.exptime > 0 else np.nan for o in obs])
    good = np.isfinite(rates)
    if good.sum() < 5 or len({round(r, 6) for r in rates[good]}) < 2 or not limit > 0:
        return []
    median = float(np.median(rates[good]))
    lossy = good & (rates < (1.0 - limit) * median)
    if not lossy.any():
        return []
    clear = good & ~lossy
    stars = _star_matrix(obs)
    ref = np.nanmedian(stars[clear], axis=0) if clear.sum() >= 3 else np.nanmedian(stars, axis=0)
    with np.errstate(invalid="ignore"):
        delta = stars - ref                                 # positive: fainter than on the clear frames
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        common = np.nanmedian(delta, axis=1)                # the loss all stars share, frame by frame
    rate_loss = -2.5 * np.log10(np.where(good, rates, np.nan) / median)
    common = np.where(np.isfinite(common), common, rate_loss)
    jd = np.array([o.jd for o in obs])
    sky = np.array([o.sky / o.exptime if o.exptime > 0 and math.isfinite(o.sky) else np.nan for o in obs])
    cad = float(np.median(np.diff(jd))) if n > 1 else 0.0

    # Group the lost frames: a stretch ends at 5 clear frames in a row or a gap of 30 minutes.
    idx = np.where(lossy)[0]
    groups, cur = [], [int(idx[0])]
    for a, b in zip(idx[:-1], idx[1:]):
        if (b - a) > 5 or jd[b] - jd[a] > 30.0 / 1440.0:
            groups.append(cur)
            cur = []
        cur.append(int(b))
    groups.append(cur)

    # Clear-sky yardsticks: frame-to-frame noise of the common loss, of each star against it, and of the sky.
    def diff_noise(v, sel):
        v = v[sel]
        v = v[np.isfinite(v)]
        return _robust_std(np.diff(v)) / math.sqrt(2.0) if v.size >= 6 else float("nan")

    noise_common = diff_noise(common, clear)
    resid = delta - common[:, None]
    n_stars = stars.shape[1]
    star_noise_clear = [diff_noise(resid[:, j], clear) for j in range(n_stars)]
    sky_clear = float(np.nanmedian(sky[clear])) if np.isfinite(sky[clear]).any() else float("nan")
    sky_noise_clear = diff_noise(sky, clear)

    episodes = []
    for g in groups:
        span = list(range(g[0], g[-1] + 1))   # clear frames inside the stretch count too: patchy cloud flickers
        sel = np.zeros(n, dtype=bool)
        sel[span] = True
        reasons = []
        depth = float(np.nanmedian(common[g])) if np.isfinite(common[g]).any() else float("nan")
        max_depth = float(np.nanmax(common[g])) if np.isfinite(common[g]).any() else float("nan")
        grow = 10.0 ** (0.4 * max(0.0, depth if math.isfinite(depth) else 0.0))   # noise grows as the light drops
        kind = "transmission"
        if len(g) < min_frames:
            kind = "cloud"
            reasons.append(f"brief ({len(g)} frame{'s' if len(g) != 1 else ''})")
        # 1. Smooth: the shared loss follows a slow curve (running median over 7 frames) without jumps. Three clear
        # frames on each side are included, so a cloud that arrives (or leaves) in one frame counts as a jump even
        # when its depth is steady inside the stretch. Checked on the stars' shared loss and on the comp light rate
        # that set the flag (the lead's merge review: a steady-depth stretch was judged only from its inside).
        ext = np.zeros(n, dtype=bool)
        ext[max(0, g[0] - 3):min(n, g[-1] + 4)] = True
        for series, noise in ((common, noise_common), (rate_loss, diff_noise(rate_loss, clear))):
            c = series[ext]
            c = c[np.isfinite(c)]
            if c.size < 7:
                continue
            pad = np.pad(c, 3, mode="edge")
            run = np.array([np.median(pad[k:k + 7]) for k in range(c.size)])
            rough = _robust_std(c - run)
            jump = float(np.max(np.abs(np.diff(c))))
            base = noise if math.isfinite(noise) else 0.01
            if rough > max(0.02, 3.0 * base * grow) or jump > max(0.15, 8.0 * base * grow):
                kind = "cloud"
                reasons.append(f"the loss changes fast ({rough:.3f} mag frame-to-frame scatter, jumps up to "
                               f"{jump:.2f} mag)")
                break
        # 2. Even: every star loses the same amount (needs two or more stars).
        # 2.2.14 review: photon noise grows only as 10^(0.2 x depth), so a uniform cloud bank with 0.02-0.03 mag of
        # star-to-star patchiness passed the old 4 x 10^(0.4 x depth) flicker test; tightened, and the spread limit
        # no longer opens up with depth.
        excess = 0.0
        if n_stars >= 2:
            offsets = []
            uneven_noise = []
            excess_sq = []
            photon_grow = 10.0 ** (0.2 * max(0.0, depth if math.isfinite(depth) else 0.0))
            for j in range(n_stars):
                r = resid[sel, j]
                r = r[np.isfinite(r)]
                rc = resid[clear, j]
                rc = rc[np.isfinite(rc)]
                if r.size >= 3 and rc.size >= 3:
                    offsets.append(float(np.median(r) - np.median(rc)))
                    ns = star_noise_clear[j] if math.isfinite(star_noise_clear[j]) else 0.01
                    rs = _robust_std(r)
                    uneven_noise.append(rs / max(ns, 0.002))
                    excess_sq.append(rs ** 2 - (ns * photon_grow) ** 2)
            if excess_sq:
                # Each star's scatter about the shared loss beyond its photon noise; resid is measured against the
                # stars' median, which takes away about 1/n of it.
                excess = math.sqrt(max(0.0, float(np.median(excess_sq))) * n_stars / (n_stars - 1.0))
            if offsets:
                spread = max(offsets) - min(offsets)
                if spread > max(0.06, 0.05 * (depth if math.isfinite(depth) else 0.0)):
                    kind = "cloud"
                    reasons.append(f"the stars lose different amounts (up to {spread:.2f} mag apart)")
                elif uneven_noise and float(np.median(uneven_noise)) > 2.5 * photon_grow:
                    kind = "cloud"
                    reasons.append("the stars flicker against each other")
        else:
            reasons.append("one star only, so evenness across the field was not checked")
        # 3. Flat sky: no jumps and no brightening (cloud lit by the Moon or towns).
        s = sky[sel]
        s = s[np.isfinite(s)]
        if s.size >= 4 and math.isfinite(sky_clear) and sky_clear > 0:
            rise = float(np.median(s) / sky_clear - 1.0)
            jitter = _robust_std(np.diff(s)) / math.sqrt(2.0)
            ref_noise = sky_noise_clear if math.isfinite(sky_noise_clear) else 0.0
            # Jumps: frame-to-frame changes far beyond the clear-sky noise in more than one frame in ten (a robust
            # scatter alone would not see jumps in a quarter of the frames).
            jumps = float(np.mean(np.abs(np.diff(s)) > max(5.0 * ref_noise, 0.05 * sky_clear)))
            if rise > 0.25:
                kind = "cloud"
                reasons.append(f"the sky brightens by {rise * 100:.0f}%")
            elif jumps > 0.10 or jitter > 3.0 * ref_noise + 0.02 * sky_clear:
                kind = "cloud"
                reasons.append("the sky jumps")
        if kind == "transmission":
            reasons.insert(0, "all stars fade together, smoothly, with a flat sky")
        episodes.append({"kind": kind, "frames": list(g), "jd": (float(jd[g[0]]), float(jd[g[-1]])),
                         "depth": depth, "max_depth": max_depth, "reasons": reasons, "excess": float(excess),
                         "minutes": float((jd[g[-1]] - jd[g[0]]) * 1440.0 + cad * 1440.0)})
    return episodes


def _plain_cloud_episode(night_obs: list[Obs], limit: float, exc: Exception) -> list[dict]:
    """2.2.14 review: the pre-2.2.14 cut (comp light more than `limit` below the night's median) as one 'cloud'
    episode, used when light_loss_episodes fails."""
    obs = sorted(night_obs, key=lambda o: o.jd)
    rates = [o.flux_comp / o.exptime if o.flux_comp > 0 and o.exptime > 0 else float("nan") for o in obs]
    good = [r for r in rates if math.isfinite(r)]
    if len(good) < 5 or len({round(r, 6) for r in good}) < 2 or not limit > 0:
        return []
    median = float(np.median(good))
    frames = [k for k, r in enumerate(rates) if math.isfinite(r) and r < (1.0 - limit) * median]
    if not frames:
        return []
    return [{"kind": "cloud", "frames": frames, "jd": (obs[frames[0]].jd, obs[frames[-1]].jd),
             "depth": float("nan"), "max_depth": float("nan"), "excess": 0.0,
             "reasons": [f"check failed ({exc}); plain cut"], "minutes": 0.0, "failed": True, "error": str(exc)}]


def apply_cloud_flags(observations: list[Obs], limit: float = 0.20, keep_nights=None,
                      report: dict | None = None) -> int:
    """Flag frames whose comparison-star light is more than `limit` (a fraction) below that
    night's median. Dew, cloud, haze, and twilight dim every star; the bright target and the
    fainter comps do not dim identically, so those frames drift. Re-applying replaces old
    'cloud' flags. Returns how many frames are flagged.

    2.2.14 (item 18): a stretch where all measured stars fade together, smoothly, under a flat sky is transmission
    loss (dew/haze), flagged 'haze' instead of 'cloud' (light_loss_episodes). Nights in keep_nights keep those
    points unflagged: each frame's photon noise already comes from its own (reduced) fluxes, and scintillation is a
    fraction of the light that does arrive; on top of that (review) each kept point gets the star-to-star scatter
    measured in its stretch beyond photon noise (Obs.haze_err, added in quadrature by point_err), so the error bars
    stay honest even if a smooth cloud bank was taken for haze. report, when given, is filled with
    {night: episodes}. When the classification fails, the night falls back on the plain cut (all 'cloud') and the
    episode list says so (reason 'check failed: ...')."""
    flagged = 0
    keep = set(keep_nights or ())
    by_night: dict[str, list[Obs]] = {}
    for obs in observations:
        by_night.setdefault(obs.night, []).append(obs)
    for night, night_obs in by_night.items():
        for o in night_obs:
            o.flag = " ".join(w for w in o.flag.split() if w not in ("cloud", HAZE_FLAG))
            o.haze_err = 0.0
        try:
            episodes = light_loss_episodes(night_obs, limit)
        except Exception as exc:
            # 2.2.14 review: never lose the cloud cut itself; fall back on the plain rate cut, all 'cloud'.
            episodes = _plain_cloud_episode(night_obs, limit, exc)
        if report is not None:
            report[night] = episodes
        if not episodes:
            continue
        ordered = sorted(night_obs, key=lambda o: o.jd)
        for ep in episodes:
            if ep["kind"] == "transmission" and night in keep:
                for k in ep["frames"]:
                    ordered[k].haze_err = float(ep.get("excess") or 0.0) * math.sqrt(1.0 + 1.0 / max(1, ordered[k].n_comps or 1))  # target's share too
                continue
            word = HAZE_FLAG if ep["kind"] == "transmission" else "cloud"
            for k in ep["frames"]:
                o = ordered[k]
                o.flag = " ".join(o.flag.split() + [word])
                flagged += 1
    return flagged


TRANSIT_FLAG = "transit"   # 2.2.15 (item 1): a frame taken while a planet of the target transits (campaign rule)


def set_transit_flags(observations: list[Obs], windows, on: bool) -> int:
    """2.2.15 (item 1): flag (on=True) or unflag the frames whose time falls in any of the windows ([[lo, hi], ...],
    JD). Other flags are left alone; the flag is stored with the series like any other. Returns how many frames
    carry the flag afterwards (of those in a window)."""
    wins = [(float(a), float(b)) for a, b in (windows or [])]
    n = 0
    for o in observations:
        if not any(a <= o.jd <= b for a, b in wins):
            continue
        words = [w for w in o.flag.split() if w != TRANSIT_FLAG]
        if on:
            words.append(TRANSIT_FLAG)
            n += 1
        o.flag = " ".join(words)
    return n


CLOUD_EDGE_LABEL = "cloud edge"   # 2.2.14 (item 10): the 'cloudedge' flag in words (night table)
CLOUD_EDGE_FRAMES = 2       # 2.2.14 (item 10): how far (in frames) from a cloud-flagged frame a point can be flagged
CLOUD_EDGE_SIGMA = 5.0      # ... and how far off the local level it must be (robust sigmas of the night)
CLOUD_EDGE_MIN_MAG = 0.05   # ... but never less than this (a quiet night's 5 sigma can be a few millimag)


def apply_cloud_edge_flags(observations: list[Obs]) -> int:
    """2.2.14 (item 10): flag 'cloudedge' on outlying points at the edge of a cloud or haze run.

    Frames just before or after a cloud run often lose less light than the cloud limit (so they pass the cloud
    flag), but the cloud edge crosses the target and the comps at different moments and the point lands far off
    (13 Oct: two points at 5.75 / 6.0 on a 5.5 star next to a cloud run). The rule is kept narrow so good data stay:
    1. only points within CLOUD_EDGE_FRAMES frames of a 'cloud' (or left-out haze) frame (in time order, and not
       separated from it by a gap of more than three typical frame spacings) can be flagged; nothing else on the
       night is looked at;
    2. only if the point sits more than max(CLOUD_EDGE_SIGMA x the night's robust scatter, CLOUD_EDGE_MIN_MAG,
       3 x its own error) from the median of the 10 nearest clean points (no flags, not next to cloud);
    3. points that already carry another flag keep it and are not double-flagged.
    A real eclipse or flare starting right at a cloud run could be caught; that is why only the frames touching
    the run are looked at. Re-applying replaces old 'cloudedge' flags. Returns how many points are flagged."""
    for o in observations:
        if "cloudedge" in o.flag.split():
            o.flag = " ".join(w for w in o.flag.split() if w != "cloudedge")
    flagged = 0
    by_night: dict[str, list[Obs]] = {}
    for obs in observations:
        by_night.setdefault(obs.night, []).append(obs)
    for night_obs in by_night.values():
        pts = sorted(night_obs, key=lambda o: o.jd)
        # A left-out transmission-loss ("haze") stretch counts like cloud here (merge of items 10 and 18).
        cloud_idx = [k for k, o in enumerate(pts) if {"cloud", HAZE_FLAG} & set(o.flag.split())]
        if not cloud_idx or len(pts) < 10:
            continue
        jd = np.array([o.jd for o in pts])
        step = float(np.median(np.diff(jd)))
        if not step > 0:
            continue
        near = set()
        for c in cloud_idx:
            for k in range(max(0, c - CLOUD_EDGE_FRAMES), min(len(pts), c + CLOUD_EDGE_FRAMES + 1)):
                if k != c and abs(jd[k] - jd[c]) <= 3.0 * step * abs(k - c):
                    near.add(k)
        clean = [k for k, o in enumerate(pts) if not o.flag and k not in near and math.isfinite(o.mag)]
        if len(clean) < 10:
            continue
        sigma = robust_sigma([pts[k].mag for k in clean])
        if not math.isfinite(sigma):
            continue
        clean_jd = jd[clean]
        clean_mag = np.array([pts[k].mag for k in clean])
        for k in sorted(near):
            o = pts[k]
            if o.flag or not math.isfinite(o.mag):
                continue
            local = float(np.median(clean_mag[np.argsort(np.abs(clean_jd - jd[k]))[:10]]))
            err = o.merr if o.merr is not None and math.isfinite(o.merr) else 0.0
            if abs(o.mag - local) > max(CLOUD_EDGE_SIGMA * sigma, CLOUD_EDGE_MIN_MAG, 3.0 * err):
                o.flag = "cloudedge"
                flagged += 1
    return flagged


# ---- Time axis ------------------------------------------------------------------------

def jd_to_datetime_utc(jd: float) -> datetime:
    """UTC datetime for a Julian Date (leap seconds ignored; fine for plotting)."""
    from datetime import timedelta

    return datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(days=float(jd) - 2451545.0)


# ---- Color ----------------------------------------------------------------------------

def bv_temperature(bv: float) -> float:
    """Effective temperature from B-V (Ballesteros 2012, EPL 97, 34008). Approximate."""
    if not math.isfinite(bv) or bv < -0.4 or bv > 2.2:
        return float("nan")
    return 4600.0 * (1.0 / (0.92 * bv + 1.7) + 1.0 / (0.92 * bv + 0.62))


def bv_spectral_class(bv: float) -> str:
    """Rough main-sequence spectral class from B-V."""
    if not math.isfinite(bv):
        return "?"
    for limit, label in ((-0.3, "O"), (0.0, "B"), (0.30, "A"), (0.58, "F"), (0.81, "G"), (1.40, "K")):
        if bv < limit:
            return label
    return "M"


def ensemble_color(comps_cat: list[dict], first: str, second: str) -> float | None:
    """Mean catalog color (first - second) of the comps that have both bands, or None.
    2.2.14 (item 1): an entry may be a star record ("colors", "sources" or "mags"); its color then comes from one
    catalog (catalog_color). A plain {band: mag} dict is read as before."""
    value, _cats = ensemble_catalog_color(comps_cat, first, second)
    return value


def ensemble_catalog_color(comps_cat: list[dict], first: str, second: str) -> tuple[float | None, list[str]]:
    """2.2.14 (item 1): (mean catalog color of the comps that have one, the catalogs it came from, comp by comp)."""
    key = next((k for k, bands in COLOR_BANDS.items() if bands == (first, second)), None)
    vals, cats = [], []
    for c in comps_cat:
        if not c:
            continue
        if key is not None and any(k in c for k in ("colors", "sources", "mags")):
            value, cat = catalog_color(c, key)
        elif first in c and second in c:
            value, cat = float(c[first]) - float(c[second]), ""
        else:
            value, cat = None, ""
        if value is not None and math.isfinite(value):
            vals.append(float(value))
            cats.append(cat)
    return (float(np.mean(vals)) if vals else None), cats


def color_sources_text(cats: list[str]) -> str:
    """2.2.14 (item 1): 'APASS DR9' or 'APASS DR9 2, Gaia DR3 1' for the catalogs a set of colors came from."""
    counts: dict[str, int] = {}
    for c in cats:
        counts[c or "catalog"] = counts.get(c or "catalog", 0) + 1
    if len(counts) == 1:
        return next(iter(counts))
    return ", ".join(f"{c} {n}" for c, n in sorted(counts.items(), key=lambda kv: -kv[1]))


# ---- Field scan (discovery mode) -----------------------------------------------------

def measure_many(img: np.ndarray, xy: np.ndarray, radius: float, sky_inner: float, sky_outer: float,
                 noise: bool = False):
    """Aperture photometry of many stars at once (photutils). Returns (net flux, sky median); with noise=True
    (2.2.12) also the sky noise inside the aperture (sky standard deviation × sqrt(aperture area)), for a
    detection test."""
    xy = np.asarray(xy, dtype=float)
    bad = ~np.isfinite(img)
    mask = bad if bad.any() else None
    apertures = CircularAperture(xy, r=radius)
    annuli = CircularAnnulus(xy, r_in=sky_inner, r_out=sky_outer)
    stats = ApertureStats(img, annuli, mask=mask, sigma_clip=SigmaClip(sigma=3.0, maxiters=10))
    sky = np.atleast_1d(np.asarray(stats.median, dtype=float))
    sums = np.asarray(aperture_photometry(img, apertures, mask=mask)["aperture_sum"], dtype=float)
    net = sums - sky * math.pi * radius ** 2
    if noise:
        sd = np.atleast_1d(np.asarray(stats.std, dtype=float))
        return net, sky, sd * math.sqrt(math.pi * radius ** 2)
    return net, sky


def detected_mags(flux, exptime: float, sky_noise=None, min_snr: float = 3.0) -> np.ndarray:
    """2.2.12: instrumental magnitudes, NaN where the star is not detected in this frame (net flux below min_snr ×
    the sky noise in the aperture). A non-detection measured as a magnitude read as a drop to 18-20 mag (S126)."""
    flux = np.asarray(flux, dtype=float)
    floor = 0.0
    if sky_noise is not None:
        floor = np.where(np.isfinite(sky_noise), min_snr * np.asarray(sky_noise, dtype=float), 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where((flux > 0) & (flux > floor), -2.5 * np.log10(flux / exptime), np.nan)


def move_points(xy: np.ndarray, reg: dict | None, shape: tuple[int, int]) -> np.ndarray:
    """move_star for an array of (x, y) points."""
    xy = np.asarray(xy, dtype=float)
    if reg is None:
        return xy.copy()
    h, w = shape
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    x, y = xy[:, 0], xy[:, 1]
    if reg["flipped"]:
        x, y = w - 1 - x, h - 1 - y
    a = math.radians(reg.get("angle", 0.0))
    ca, sa = math.cos(a), math.sin(a)
    bx, by = x - cx, y - cy
    return np.column_stack((ca * bx - sa * by + cx + reg["dx"], sa * bx + ca * by + cy + reg["dy"]))


def pixel_to_sky(x: float, y: float, solution: dict) -> tuple[float, float]:
    """Inverse of place_stars: image pixel -> (RA deg, Dec deg), using a chart-match solution."""
    tx, ty = solution["target_xy"]
    scale = solution["scale"] * solution.get("scale_factor", 1.0)
    theta = math.radians(solution["angle"])
    ct, st = math.cos(theta), math.sin(theta)
    dx, dy = x - tx, y - ty
    e = -dx * ct + dy * st
    n = -dx * st - dy * ct
    if solution.get("mirrored"):
        e = -e
    dec = solution["dec_deg"] + n * scale / 3600.0
    ra = solution["ra_deg"] + e * scale / 3600.0 / max(math.cos(math.radians(solution["dec_deg"])), 0.01)
    return ra % 360.0, dec


def sky_to_pixel(ra_deg: float, dec_deg: float, solution: dict) -> tuple[float, float]:
    """Sky position -> image pixel, using a chart-match solution (the same mapping as place_stars)."""
    x, y, _ = place_stars([{"ra": ra_deg, "dec": dec_deg}], solution, solution["target_xy"], solution["ra_deg"],
                          solution["dec_deg"], solution["scale"])[0]
    return x, y


def parse_coordinates(text: str):
    """'23 13 48.1 +57 07 34', '23:13:48.1 57:07:34', or '348.2004 57.1261' (degrees) -> (RA deg, Dec deg).
    Returns None when the text is not a coordinate pair."""
    import re

    t = text.strip().replace(",", " ")
    if not t or re.search(r"[A-Za-z]", t.replace("h", "").replace("m", "").replace("s", "").replace("d", "")):
        return None
    t = re.sub(r"[hmsd:°'\"]", " ", t)
    parts = t.split()
    try:
        if len(parts) == 2:
            ra, dec = float(parts[0]), float(parts[1])
        elif len(parts) == 6:
            sign = -1.0 if parts[3].startswith("-") else 1.0
            ra = 15.0 * (float(parts[0]) + float(parts[1]) / 60.0 + float(parts[2]) / 3600.0)
            dec = sign * (abs(float(parts[3])) + float(parts[4]) / 60.0 + float(parts[5]) / 3600.0)
        elif len(parts) == 4:
            sign = -1.0 if parts[2].startswith("-") else 1.0
            ra = 15.0 * (float(parts[0]) + float(parts[1]) / 60.0)
            dec = sign * (abs(float(parts[2])) + float(parts[3]) / 60.0)
        else:
            return None
    except ValueError:
        return None
    if not (0.0 <= ra < 360.0 and -90.0 <= dec <= 90.0):
        return None
    return ra, dec


def field_variability(inst: np.ndarray, min_fraction: float = 0.7, exclude=None) -> dict:
    """Variability statistics for every star in a field.

    inst: frames x stars instrumental magnitudes (NaN where unmeasured).
    Each frame's zero point is the median offset of the well-measured stars from their own
    medians, so clouds and airmass cancel. Scatter is the robust standard deviation
    (1.4826 x MAD). The expected scatter at each brightness is a running median of all stars;
    'excess' is scatter / expected, and real variables stand well above 1.
    """
    inst = np.asarray(inst, dtype=float)
    n_frames, n_stars = inst.shape
    star_med = np.nanmedian(inst, axis=0)
    good_count = np.sum(np.isfinite(inst), axis=0)
    usable = good_count >= max(5, min_fraction * n_frames)
    # Zero point per frame from usable stars, brightest half weighted by just using them.
    order = np.argsort(np.where(usable, star_med, np.inf))
    ref = order[: max(3, int(usable.sum() * 0.6))]
    zp = np.nanmedian(inst[:, ref] - star_med[ref][None, :], axis=1)
    zp = np.where(np.isfinite(zp), zp, 0.0)
    corrected = inst - zp[:, None]
    med = np.nanmedian(corrected, axis=0)
    mad = np.nanmedian(np.abs(corrected - med[None, :]), axis=0)
    scatter = 1.4826 * mad
    expected = typical_scatter(med, scatter, usable, exclude=exclude)
    excess = scatter / expected
    return {
        "corrected": corrected,
        "median": med,
        "scatter": scatter,
        "expected": expected,
        "excess": excess,
        "usable": usable,
        "n_good": good_count,
        "zero_point": zp,
    }


def typical_scatter(mag, scatter, usable, half_width: float = 0.5, n_min: int = 5, clip: float = 2.5,
                    exclude=None):
    """2.2.12: the scatter a star of each brightness normally shows. A running median over the stars within
    ±half_width mag (at least n_min, at most an eighth of the field, nearest in magnitude first), so the bright end
    follows the bright stars instead of borrowing the fainter, noisier ones (on the HD 219134 field every star of
    8.7-10.5 mag sat below the old rank-window curve). Stars more than clip × the first curve (variables,
    artifacts) are left out of the second, final pass. exclude: stars (indices) evaluated but never used to build
    the curve (saturated stars)."""
    mag = np.asarray(mag, dtype=float)
    scatter = np.asarray(scatter, dtype=float)
    out = np.full(mag.size, np.nan)
    idx = np.where(np.asarray(usable, dtype=bool) & np.isfinite(scatter) & (scatter > 0) & np.isfinite(mag))[0]
    if not idx.size:
        return out
    if exclude is not None and len(exclude):
        pool0 = np.setdiff1d(idx, np.asarray(list(exclude), dtype=int))
        if pool0.size < n_min:
            pool0 = idx
    else:
        pool0 = idx
    k_max = max(n_min, pool0.size // 8)

    def curve(pool):
        res = np.full(mag.size, np.nan)
        pm = mag[pool]
        for s in idx:
            d = np.abs(pm - mag[s])
            take = min(max(int((d <= half_width).sum()), n_min), k_max, pool.size)
            nearest = np.argpartition(d, take - 1)[:take] if take < pool.size else np.arange(pool.size)
            res[s] = np.median(scatter[pool[nearest]])
        return res

    first = curve(pool0)
    keep = pool0[scatter[pool0] <= clip * first[pool0]]
    return curve(keep if keep.size >= n_min else pool0)


def scatter_curve_segments(mag, expected, max_gap: float = 0.75):
    """2.2.12: the typical-scatter curve as (mag, expected) segments, broken where no stars are for more than
    max_gap mag, so no line is drawn across an empty magnitude range (HD 219134 at 5.5 mag drew a flat line to
    8.7)."""
    mag = np.asarray(mag, dtype=float)
    expected = np.asarray(expected, dtype=float)
    ok = np.isfinite(mag) & np.isfinite(expected)
    m, e = mag[ok], expected[ok]
    order = np.argsort(m)
    m, e = m[order], e[order]
    if not m.size:
        return []
    cuts = np.where(np.diff(m) > max_gap)[0] + 1
    return [(a, b) for a, b in zip(np.split(m, cuts), np.split(e, cuts))]


def faint_limit(mag, expected, level: float = 0.25):
    """2.2.12: the default faintest magnitude: where the field's typical scatter reaches `level` mag for good
    (stars past it are too noisy to call variable). Found from the faint end, so a noisy group of bright stars can
    never set it. None when the faint end stays under the level, or no part of the field is under it."""
    mag = np.asarray(mag, dtype=float)
    expected = np.asarray(expected, dtype=float)
    ok = np.isfinite(mag) & np.isfinite(expected)
    if ok.sum() < 5:
        return None
    m, e = mag[ok], expected[ok]
    order = np.argsort(m)
    m, e = m[order], e[order]
    below = np.where(e < level)[0]
    if not below.size:
        return None
    k = int(below[-1])                 # the faintest star still under the level
    if k == m.size - 1:
        return None
    # Straight-line crossing between it and the next, fainter star (at or above the level).
    m0, m1, e0, e1 = m[k], m[k + 1], e[k], e[k + 1]
    return float(m0 + (level - e0) * (m1 - m0) / (e1 - e0)) if e1 > e0 else float(m1)


def fetch_vsx_cone(ra_deg: float, dec_deg: float, radius_deg: float) -> list[dict]:
    """Known variables near a position, from VizieR's copy of AAVSO VSX (B/vsx/vsx)."""
    rows = _vizier_query(
        "B/vsx/vsx", ra_deg, dec_deg, radius_deg,
        ["Name", "Type", "Period", "max", "min", "RAJ2000", "DEJ2000"], "Name", 2000,
    )
    out = []
    for row in rows:
        try:
            ra = _angle(row.get("RAJ2000", ""), hours=True)
            dec = _angle(row.get("DEJ2000", ""), hours=False)
        except (ValueError, IndexError):
            continue
        out.append({"name": row.get("Name", ""), "type": row.get("Type", ""), "period": _num(row.get("Period")),
                    "max": row.get("max", ""), "min": row.get("min", ""), "ra": ra, "dec": dec})
    return out


def sky_separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    d_ra = math.radians(((ra1 - ra2 + 180.0) % 360.0) - 180.0) * math.cos(math.radians((dec1 + dec2) / 2.0))
    d_dec = math.radians(dec1 - dec2)
    return math.degrees(math.hypot(d_ra, d_dec)) * 3600.0


def format_radec(ra_deg: float, dec_deg: float) -> str:
    return _position_name("", ra_deg, dec_deg).strip().lstrip("J")


def channel_inst_mags(img: np.ndarray, stars: list["Star"], radius: float, sky_inner: float, sky_outer: float, exptime: float) -> np.ndarray:
    """Instrumental magnitudes of a few stars in one debayered channel (NaN where it fails)."""
    out = []
    for star in stars:
        try:
            x, y, _ = recenter(img, star.x, star.y, radius)
            m = measure_aperture(img, x, y, radius, sky_inner, sky_outer)
            out.append(inst_mag(m["flux"], exptime))
        except Exception:
            out.append(float("nan"))
    return np.array(out, dtype=float)


def differential_color(first: np.ndarray, second: np.ndarray) -> float:
    """(first - second) of the target (index 0) minus the mean of the comps (indices 1..)."""
    if len(first) < 2:
        return float("nan")
    comp = first[1:] - second[1:]
    comp = comp[np.isfinite(comp)]
    t = first[0] - second[0]
    if not comp.size or not math.isfinite(t):
        return float("nan")
    return float(t - comp.mean())


# ---- Color calibration from field stars ------------------------------------------------
#
# An OSC camera's green, red, and blue channels are not Johnson V, R, and B, so the raw
# channel color (blue - green) is not B-V. Field stars with catalog colors fix that: their
# catalog B-V plotted against their instrumental color lies on a straight line,
#     B-V = a + k * (instrumental color)
# and the slope k (the color term) belongs to the camera, filters, and optics, so it carries
# over from night to night. Each frame's instrumental colors are taken relative to the median
# star in that frame, so changing extinction and exposure cancel.

COLOR_CAL_FILE = os.path.join(os.path.expanduser("~"), ".shiloh_photometry_colorcal.json")
# key, catalog bands (first - second), channels (first - second)
COLOR_INDICES = (("bv", "B", "V", "blue", "green"), ("vr", "V", "R", "green", "red"))


def color_calibration_stars(placed: list, target_xy, radius: float, sky_outer: float, shape: tuple[int, int],
                            max_stars: int = 50) -> list:
    """Labeled chart stars that can anchor a color fit.

    Keeps stars with catalog B and V (R is used when present), inside the frame, away from the
    target, and with no catalog star brighter than 3 mag fainter inside 1.5 aperture radii
    (a blend would give a wrong color). Brightest first.
    """
    h, w = shape
    edge = sky_outer + 3
    out = []
    if not placed:
        return out
    # 2.2.14 (item 1): the crowding test for every star at once (a k-d tree), not every candidate against every
    # star in Python: with Gaia's thousands of stars merged in, the old O(n^2) loop stalled the window for seconds
    # when a run started with "Also measure R and B".
    xy = np.array([(float(x), float(y)) for x, y, _st in placed], dtype=float)
    vmag = np.array([_float_or_nan((st.get("mags") or {}).get("V", st.get("mag"))) for _x, _y, st in placed])
    crowded = crowded_flags(xy, vmag, 1.5 * radius, 3.0)
    for k, (x, y, star) in enumerate(placed):
        mags = star.get("mags") or {}
        if "V" not in mags or catalog_color(star, "bv")[0] is None:
            continue   # 2.2.14 (item 1): B and V from one catalog
        if star.get("variable"):
            continue
        if not (edge <= x < w - edge and edge <= y < h - edge):
            continue
        if target_xy is not None and math.hypot(x - target_xy[0], y - target_xy[1]) < 2 * radius:
            continue
        if not crowded[k]:
            out.append((x, y, star))
    out.sort(key=lambda item: item[2]["mags"]["V"])
    return out[:max_stars]


def crowded_flags(xy: np.ndarray, mags: np.ndarray, reach: float, dmag: float = 3.0) -> np.ndarray:
    """2.2.14 (item 1): crowded[i] is True when another star lies closer than `reach` px to star i and is not at least
    `dmag` fainter (or its magnitude is unknown). All pairs from one k-d tree query, so it is fast for thousands of
    stars (the same test the old per-star loops made)."""
    xy = np.asarray(xy, dtype=float).reshape(-1, 2)
    mags = np.asarray(mags, dtype=float)
    out = np.zeros(len(xy), dtype=bool)
    if len(xy) < 2 or not (reach > 0):
        return out
    from scipy.spatial import cKDTree
    pairs = cKDTree(xy).query_pairs(float(np.nextafter(reach, 0.0)), output_type="ndarray")
    if pairs.size == 0:
        return out
    i, j = pairs[:, 0], pairs[:, 1]
    mi, mj = mags[i], mags[j]
    with np.errstate(invalid="ignore"):
        j_crowds_i = np.isnan(mj) | (mj < mi + dmag)
        i_crowds_j = np.isnan(mi) | (mi < mj + dmag)
    out[i[j_crowds_i]] = True
    out[j[i_crowds_j]] = True
    return out


def relative_colors(first: np.ndarray, second: np.ndarray) -> tuple[np.ndarray, float]:
    """Instrumental color (first - second) of every star in one frame, minus the frame's
    median star. Returns (relative colors, the median that was removed)."""
    color = np.asarray(first, dtype=float) - np.asarray(second, dtype=float)
    finite = color[np.isfinite(color)]
    if finite.size < 3:
        return np.full(color.shape, np.nan), float("nan")
    ref = float(np.median(finite))
    return color - ref, ref


COLOR_FIT_MAX_SCATTER = 0.15   # mag; a weighted fit looser than this is not used
COLOR_FIT_MAX_STAR_ERR = 0.08  # mag; stars whose own color is noisier than this are left out
COLOR_FIT_FLOOR = 0.03         # mag; catalog color errors and transformation mismatch


def robust_mean_err(values: np.ndarray, axis: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Median along an axis and its standard error (1.2533 x robust sigma / sqrt(n))."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(values, axis=axis)
        mad = np.nanmedian(np.abs(values - np.expand_dims(med, axis)), axis=axis)
        n = np.sum(np.isfinite(values), axis=axis)
        err = 1.2533 * 1.4826 * mad / np.sqrt(np.maximum(n, 1))
    err = np.where(n >= 3, err, np.nan)
    return med, err


def fit_color_term(inst: np.ndarray, catalog: np.ndarray, min_stars: int = 5, clip: float = 3.0,
                   inst_err: np.ndarray | None = None, floor: float = COLOR_FIT_FLOOR,
                   max_scatter: float = COLOR_FIT_MAX_SCATTER) -> dict | None:
    """Weighted, outlier-resistant straight line catalog = a + k * inst.

    Each star is weighted by 1 / (its color error), where the error combines its own
    measured noise (inst_err, from frame-to-frame scatter) scaled by the slope, and a floor
    for catalog errors. Faint, noisy stars therefore count little, and stars noisier than
    COLOR_FIT_MAX_STAR_ERR are left out. Outliers (variables, blends, bad catalog colors)
    beyond clip x the normalized scatter are rejected. None if too few stars survive.

    Returns k, a, scatter (weighted rms about the line), k_err, n, n_rejected, range, and
    'problem' when the fit should not be trusted (narrow color range, slope far from 1, a
    poorly determined slope, or scatter above max_scatter).
    """
    # 2.2.16 (item 3): never raise. np.polyfit can fail ("SVD did not converge") on bad input; that killed the
    # photometry worker thread and lost the night. A failure comes back as {"failed": reason, "problem": reason}.
    try:
        return _fit_color_term(inst, catalog, min_stars, clip, inst_err, floor, max_scatter)
    except Exception as exc:   # LinAlgError, ValueError, ... : whatever it is, the run goes on
        return color_fit_failure(exc)


def color_fit_usable(fit) -> bool:
    """2.2.16 (item 3 review): a color fit whose k, a, scatter can be read and used (not None, failed or a problem)."""
    return (isinstance(fit, dict) and not fit.get("failed") and not fit.get("problem")
            and all(fit.get(k) is not None for k in ("k", "a", "scatter", "n")))


def color_fit_failure(exc) -> dict:
    """2.2.16 (item 3): a color fit that could not be done; 'problem' keeps every caller from using it."""
    reason = f"color fit failed: {type(exc).__name__}: {exc}" if isinstance(exc, BaseException) else f"color fit failed: {exc}"
    return {"failed": reason, "problem": reason, "n": 0, "n_rejected": 0, "n_noisy": 0}


def _fit_color_term(inst, catalog, min_stars, clip, inst_err, floor, max_scatter) -> dict | None:
    x = np.asarray(inst, dtype=float).ravel()
    y = np.asarray(catalog, dtype=float).ravel()
    if x.shape != y.shape:
        raise ValueError(f"{x.size} instrumental colors for {y.size} catalog colors")
    err = np.zeros_like(x) if inst_err is None else np.asarray(inst_err, dtype=float).ravel()
    if err.shape != x.shape:
        raise ValueError(f"{err.size} color errors for {x.size} stars")
    floor = float(floor) if np.isfinite(floor) and floor > 0 else COLOR_FIT_FLOOR   # 2.2.16 (item 3): weights > 0
    ok = np.isfinite(x) & np.isfinite(y) & np.isfinite(err) & (err >= 0)
    n_noisy = 0
    if inst_err is not None:
        noisy = ok & (err > COLOR_FIT_MAX_STAR_ERR)
        n_noisy = int(noisy.sum())
        ok &= ~noisy
    if ok.sum() < min_stars:
        return None
    use = ok.copy()
    k, a = 1.0, 0.0
    sig = np.sqrt((k * err) ** 2 + floor ** 2)
    for _ in range(8):
        if use.sum() < min_stars:
            return None
        if float(np.ptp(x[use])) <= 0:   # 2.2.16 (item 3): all stars the same instrumental color: no line
            raise ValueError("all stars have the same instrumental color")
        k, a = np.polyfit(x[use], y[use], 1, w=1.0 / sig[use])
        if not (np.isfinite(k) and np.isfinite(a)):
            raise ValueError("the fit gave no finite line")
        sig = np.sqrt((k * err) ** 2 + floor ** 2)
        norm = (y - (a + k * x)) / sig
        spread = 1.4826 * float(np.median(np.abs(norm[use] - np.median(norm[use]))))
        new = ok & (np.abs(norm) < clip * max(spread, 1.0))
        if np.array_equal(new, use):
            break
        use = new
    if use.sum() < min_stars:
        return None
    if float(np.ptp(x[use])) <= 0:   # 2.2.16 (item 3)
        raise ValueError("all stars have the same instrumental color")
    k, a = np.polyfit(x[use], y[use], 1, w=1.0 / sig[use])
    if not (np.isfinite(k) and np.isfinite(a)):
        raise ValueError("the fit gave no finite line")
    res = y[use] - (a + k * x[use])
    wts = 1.0 / sig[use] ** 2
    n = int(use.sum())
    scatter = float(math.sqrt(np.sum(wts * res ** 2) / np.sum(wts) * n / max(n - 2, 1)))
    xm = float(np.sum(wts * x[use]) / np.sum(wts))
    sxx = float(np.sum(wts * (x[use] - xm) ** 2))
    chi2 = float(np.sum(wts * res ** 2)) / max(n - 2, 1)
    k_err = math.sqrt(max(chi2, 1.0) / sxx) if sxx > 0 else float("inf")
    lo, hi = float(np.min(y[use])), float(np.max(y[use]))
    problem = ""
    if hi - lo < 0.3:
        problem = f"the stars span only {hi - lo:.2f} mag in color"
    elif not (0.4 <= k <= 2.5):
        problem = f"the color term {k:.2f} is far from 1"
    elif k_err > 0.15:
        problem = f"the color term is poorly determined (±{k_err:.2f})"
    elif scatter > max_scatter:
        problem = f"the stars scatter {scatter:.2f} mag about the line (limit {max_scatter:.2f})"
    return {"k": float(k), "a": float(a), "scatter": scatter, "k_err": float(k_err), "n": n,
            "n_rejected": int(ok.sum() - n), "n_noisy": n_noisy, "range": [lo, hi], "problem": problem}


def color_fit_text(name: str, fit: dict | None) -> str:
    if not fit:
        return f"{name}: no fit"
    if fit.get("failed"):   # 2.2.16 (item 3)
        return f"{name}: {fit['failed']} — continuing without the color term"
    text = (f"{name} = {fit['a']:+.3f} + {fit['k']:.3f} x instrumental (±{fit['k_err']:.3f}), "
            f"{fit['n']} stars ({fit['n_rejected']} rejected, {fit.get('n_noisy', 0)} too noisy), "
            f"weighted scatter {fit['scatter']:.3f} mag, "
            f"catalog {fit['range'][0]:.2f} to {fit['range'][1]:.2f}")
    if fit.get("problem"):
        text += f"  NOT USED: {fit['problem']}"
    return text


def load_color_calibration(path: str = COLOR_CAL_FILE) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) and (data.get("bv") or data.get("vr")) else None
    except (OSError, ValueError):
        return None


def save_color_calibration(cal: dict, path: str = COLOR_CAL_FILE) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cal, fh, indent=2)
    os.replace(tmp, path)


def apply_color_calibration(observations: list[Obs], fits: dict, comp_offsets: dict | None,
                            saved: dict | None, cat_colors: dict) -> dict:
    """Put each frame's target color on the catalog scale.

    fits: {"bv": fit or None, "vr": ...} from tonight's field stars.
    comp_offsets: {"bv": the comps' relative instrumental color, ...} measured with the fit.
    saved: an earlier calibration {"bv": fit, "vr": fit} (slope only is reused).
    cat_colors: {"bv": comps' catalog B-V or None, "vr": ...}.
    Returns {"bv": "field" | "saved" | "", "vr": ...}: which method each index used.
    """
    used = {}
    for key, *_ in COLOR_INDICES:
        fit = (fits or {}).get(key)
        offset = (comp_offsets or {}).get(key)
        old = (saved or {}).get(key)
        method = ""
        if color_fit_usable(fit) and offset is not None and math.isfinite(offset):
            method = "field"
        elif color_fit_usable(old) and cat_colors.get(key) is not None:
            method = "saved"
        for obs in observations:
            diff = obs.bv_diff if key == "bv" else obs.vr_diff
            if not math.isfinite(diff):
                continue
            if method == "field":
                value = fit["a"] + fit["k"] * (diff + offset)
            elif method == "saved":
                value = cat_colors[key] + old["k"] * diff
            else:
                continue
            setattr(obs, key, float(value))
        used[key] = method
    tag = ";".join(f"{key}={method}" for key, method in used.items() if method)
    for obs in observations:
        if math.isfinite(obs.bv) or math.isfinite(obs.vr):
            obs.color_cal = tag
    return used


def field_star_colors(first: np.ndarray, second: np.ndarray, catalog: np.ndarray,
                      frame_ok: np.ndarray | None = None) -> tuple[np.ndarray, dict | None, np.ndarray | None]:
    """Colors for every star in a field scan.

    first, second: frames x stars instrumental magnitudes in two channels (e.g. blue, green).
    catalog: each star's catalog color, NaN where unknown.
    frame_ok: frames to use (e.g. not clouded); all when None.
    Returns (relative instrumental color per star, fit, calibrated color per star or None).
    """
    first = np.atleast_2d(np.asarray(first, dtype=float))
    second = np.atleast_2d(np.asarray(second, dtype=float))
    if frame_ok is not None and np.sum(frame_ok) >= 5:
        first, second = first[frame_ok], second[frame_ok]
    rel = np.vstack([relative_colors(f, s)[0] for f, s in zip(first, second)])
    inst, inst_err = robust_mean_err(rel, axis=0)
    fit = fit_color_term(inst, catalog, inst_err=inst_err)
    calibrated = fit["a"] + fit["k"] * inst if color_fit_usable(fit) else None
    return inst, fit, calibrated


def color_method(obs: Obs, key: str) -> str:
    """'field', 'saved', or '' for one color index of one point (see apply_color_calibration)."""
    for part in (obs.color_cal or "").split(";"):
        name, _, method = part.partition("=")
        if name == key:
            return method
    return ""


def scan_cautions(result: dict, i: int) -> list[str]:
    """2.2.2: reasons to doubt a field-scan candidate (near saturation; biggest change at a flip or gap)."""
    out = []
    clipped = (result.get("clipped_some") or {}).get(i)   # 2.2.19 (#25): raw core clipped on a few frames
    if clipped:
        out.append(f"clipped on {clipped} frame(s): check linearity")
    elif i in set(result.get("near_sat") or []):
        out.append("near saturation: check linearity")
    if i in set(result.get("blended") or []):   # 2.2.19 (#26)
        out.append("blended neighbor: check")
    vsx = result.get("vsx")
    hit = vsx[i] if vsx is not None and i < len(vsx) else None
    rng = result.get("period_range")
    if hit and rng and hit.get("period"):   # 2.2.19 (#19): a known period the search could not find
        outside, _fix = period_range_check([("VSX", hit.get("period"))], float(rng[0]), float(rng[1]))
        if outside:
            out.append(f"VSX period {outside[0][1]:.4g} d outside the search")
    kind = (result.get("at_boundary") or {}).get(i)
    if kind == "flip":
        out.append("change begins at flip: check")
    elif kind == "gap":
        out.append("change begins after a gap: check")
    noise = (result.get("noise_at_boundary") or {}).get(i)   # 2.2.7
    if noise == "flip" and kind != "flip":
        out.append("noise begins at flip: check")
    elif noise == "gap" and kind != "gap":
        out.append("noise begins after a gap: check")
    return out


def scan_candidate_kind(result: dict, i: int) -> tuple[str, str]:
    """2.2.5: what a field-scan candidate is worth, and why: ("new", ""), ("known", "KNOWN (VSX): name type"),
    ("caution", "near saturation: check linearity"), or ("unchecked", "no sky positions: …"). A known star with a
    caution counts as known. The reason is shown when hovering over the mark."""
    vsx = result.get("vsx")
    hit = vsx[i] if vsx is not None and i < len(vsx) else None
    if hit:
        text = f"KNOWN ({hit.get('source', 'VSX')}): {hit.get('name', '')} {hit.get('type', '')}".strip()
        return "known", text
    cautions = scan_cautions(result, i)
    if cautions:
        return "caution", "Check first: " + "; ".join(cautions)
    if vsx is None:
        return "unchecked", "no sky positions, so not checked against VSX/SIMBAD (solve the field first)"
    sources = " or ".join(result.get("vsx_sources") or ["VSX"])
    return "new", f"not in {sources}"


def scan_period_value(per: dict):
    """2.2.19 (nit): a field-scan star's best period, or None when the best peak sits at the edge of the search (no
    period: a drift reads as the longest period searched, "20 d")."""
    if not per or (per.get("ls") or {}).get("edge"):
        return None
    return per.get("period")


def write_scan_candidates(path: str, result: dict) -> None:
    """One row per star that stands out, ready to start a VSX new-variable submission."""
    with AsciiWriter(path) as fh:
        fh.write(created_line() + "\n")
        fh.write("rank,label,x,y,ra_deg,dec_deg,radec,mag,scatter,expected_scatter,excess,best_period_days,"
                 "period_power,b_minus_v,v_minus_r,color_method,vsx_name,vsx_type,vsx_period,n_points,caution\n")
        for rank, i in enumerate(result["candidates"], 1):
            def num(v, fmt="{:.4f}"):
                return "" if v is None or not (isinstance(v, (int, float)) and math.isfinite(v)) else fmt.format(v)
            ra, dec = result["radec"][i] if result.get("radec") else (float("nan"), float("nan"))
            vsx = result["vsx"][i] if result.get("vsx") else None
            per = result["periods"].get(i, {})
            fh.write(",".join([
                str(rank), result["labels"][i], f"{result['xy'][i][0]:.1f}", f"{result['xy'][i][1]:.1f}",
                num(ra, "{:.6f}"), num(dec, "{:.6f}"),
                format_radec(ra, dec) if math.isfinite(ra) else "",
                num(result["mag"][i], "{:.3f}"), num(result["scatter"][i]), num(result["expected"][i]),
                num(result["excess"][i], "{:.2f}"), num(scan_period_value(per)), num(per.get("power"), "{:.3f}"),
                num(result["bv"][i], "{:.3f}") if result.get("bv") is not None else "",
                num(result["vr"][i], "{:.3f}") if result.get("vr") is not None else "",
                (result.get("color_method") or {}).get("bv", ""),
                (vsx or {}).get("name", "").replace(",", " "), (vsx or {}).get("type", "").replace(",", " "),
                num((vsx or {}).get("period")), str(int(result["n_good"][i])),
                "; ".join(scan_cautions(result, i)),
            ]) + "\n")


def write_scan_stars(path: str, result: dict) -> None:
    """2.2.9: every measured star of a field scan (what the scatter plot shows): position, magnitude, scatter, how
    much more it scatters than stars of its brightness, candidate kind, and any VSX/SIMBAD match."""
    cand_rank = {i: k for k, i in enumerate(result["candidates"], 1)}
    sat = set(result.get("saturated") or [])

    def num(v, fmt="{:.4f}"):
        return "" if v is None or not (isinstance(v, (int, float)) and math.isfinite(v)) else fmt.format(v)

    with AsciiWriter(path) as fh:
        fh.write(created_line() + "\n")
        fh.write("label,x,y,ra_deg,dec_deg,radec,mag,scatter,expected_scatter,excess,candidate_rank,candidate_kind,"
                 "saturated,usable,best_period_days,vsx_name,vsx_type,n_points,caution\n")
        for i, label in enumerate(result["labels"]):
            ra, dec = result["radec"][i] if result.get("radec") else (float("nan"), float("nan"))
            vsx = result["vsx"][i] if result.get("vsx") else None
            per = (result.get("periods") or {}).get(i, {})
            kind = scan_candidate_kind(result, i)[0] if i in cand_rank else ""
            fh.write(",".join([
                str(label).replace(",", " "), f"{result['xy'][i][0]:.1f}", f"{result['xy'][i][1]:.1f}",
                num(ra, "{:.6f}"), num(dec, "{:.6f}"), format_radec(ra, dec) if math.isfinite(ra) else "",
                num(result["mag"][i], "{:.3f}"), num(result["scatter"][i]), num(result["expected"][i]),
                num(result["excess"][i], "{:.2f}"), str(cand_rank.get(i, "")), kind,
                "yes" if i in sat else "", "yes" if result["usable"][i] else "no", num(scan_period_value(per)),
                (vsx or {}).get("name", "").replace(",", " "), (vsx or {}).get("type", "").replace(",", " "),
                str(int(result["n_good"][i])), "; ".join(scan_cautions(result, i)).replace(",", ";"),
            ]) + "\n")


def write_scan_lightcurves(path: str, result: dict) -> None:
    """Every star's zero-pointed light curve, long format (one row per star per frame)."""
    corrected = result["light_curves"]
    with AsciiWriter(path) as fh:
        fh.write(created_line() + "\n")
        fh.write("label,jd,mag\n")
        for i, label in enumerate(result["labels"]):
            for j, jd in enumerate(result["jd"]):
                v = corrected[j, i]
                if math.isfinite(v):
                    fh.write(f"{label},{jd:.6f},{v:.4f}\n")


# ---- Credits --------------------------------------------------------------------------
# Every library, catalog, service, and published method the app relies on. Acknowledgement
# sentences in quotes are the wording each source asks for, copied from its own pages.
# When a feature adds a new component, its credit is added here in the same build.

CREDITS = [
    ("Software", [
        ("Astropy", "FITS input, time and Julian Dates, coordinates and airmass, units, sigma clipping, Lomb-Scargle.",
         "\"This work made use of Astropy: a community-developed core Python package and an ecosystem of tools "
         "and resources for astronomy.\" Astropy Collaboration 2013, A&A 558, A33; 2018, AJ 156, 123; 2022, ApJ 935, 167.",
         "https://www.astropy.org"),
        ("photutils", "Aperture photometry, sky annuli, centroids, FWHM and elongation.",
         "Bradley, L. et al., photutils: an Astropy package for detection and photometry of astronomical sources. "
         "Zenodo, doi:10.5281/zenodo.596036.", "https://photutils.readthedocs.io"),
        ("ccdproc", "Bias, dark, and flat calibration.",
         "Craig, M. et al., ccdproc: CCD data reduction software. Zenodo, doi:10.5281/zenodo.1069648.",
         "https://ccdproc.readthedocs.io"),
        ("NumPy", "Array math throughout.", "Harris, C. R. et al. 2020, Nature 585, 357.", "https://numpy.org"),
        ("SciPy", "Star detection filters.", "Virtanen, P. et al. 2020, Nature Methods 17, 261.", "https://scipy.org"),
        ("Matplotlib", "All plots and image displays.", "Hunter, J. D. 2007, Computing in Science & Engineering 9, 90.",
         "https://matplotlib.org"),
    ]),
    ("Catalogs and services", [
        ("Gaia DR3 (ESA)", "Comparison stars, positions, proper motions, Johnson V/B/R synthetic photometry, field stars for color calibration.",
         "\"This work has made use of data from the European Space Agency (ESA) mission Gaia, processed by the Gaia "
         "Data Processing and Analysis Consortium (DPAC). Funding for the DPAC has been provided by national "
         "institutions, in particular the institutions participating in the Gaia Multilateral Agreement.\" "
         "Gaia Collaboration, Prusti et al. 2016, A&A 595, A1; Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1; "
         "Gaia Collaboration, Montegriffo et al. 2023, A&A 674, A33 (synthetic photometry).",
         "https://www.cosmos.esa.int/gaia"),
        ("APASS (AAVSO)", "Comparison stars, Johnson B and V and Sloan g', r', i' (2.2.17), field stars for B-V calibration.",
         "\"This research was made possible through the use of the AAVSO Photometric All-Sky Survey (APASS), funded "
         "by the Robert Martin Ayers Sciences Fund and NSF AST-1412587.\" Henden, A. A. et al. 2016, VizieR II/336.",
         "https://www.aavso.org/apass"),
        ("Tycho-2", "Bright comparison stars.", "Høg, E. et al. 2000, A&A 355, L27.", "https://cdsarc.cds.unistra.fr/viz-bin/cat/I/259"),
        ("AAVSO VSX", "Target lookup, catalog periods, and the known-variable check of comparison stars, spares, watch "
         "stars, and field scans.",
         "Watson, C. L., Henden, A. A. & Price, A. 2006, Society for Astronomical Sciences Annual Symposium 25, 47.",
         "https://vsx.aavso.org"),
        ("AAVSO Variable Star Plotter", "AAVSO comparison-star sequences and chart IDs.",
         "American Association of Variable Star Observers.", "https://app.aavso.org/vsp/"),
        ("SIMBAD (CDS)", "Target coordinates and proper motion; known and suspected variables (object types) used to vet "
         "comparison stars, spares, and field scans (TAP service); name lookup for Add a star (watch stars).",
         "\"This research has made use of the SIMBAD database, CDS, Strasbourg Astronomical Observatory, France.\" "
         "Wenger, M. et al. 2000, A&AS 143, 9.", "https://simbad.cds.unistra.fr"),
        ("NASA Exoplanet Archive", "Planet lookup in Transits mode: period, T0, duration, depth, Rp/R*, a/R*, impact "
         "parameter, host star (Planetary Systems Composite table, TAP service).",
         "\"This research has made use of the NASA Exoplanet Archive, which is operated by the California Institute of "
         "Technology, under contract with the National Aeronautics and Space Administration under the Exoplanet "
         "Exploration Program.\" Akeson, R. L. et al. 2013, PASP 125, 989.",
         "https://exoplanetarchive.ipac.caltech.edu"),
        ("ExoFOP (TESS)", "Lookup of TESS Objects of Interest not yet in the Exoplanet Archive; the ExoFOP package "
         "is laid out for its time-series uploads.",
         "\"This research has made use of the Exoplanet Follow-up Observation Program (ExoFOP; DOI: 10.26134/ExoFOP5) "
         "website, which is operated by the California Institute of Technology, under contract with the National "
         "Aeronautics and Space Administration under the Exoplanet Exploration Program.\"",
         "https://exofop.ipac.caltech.edu/tess/"),
        ("VizieR (CDS)", "Access to APASS, Tycho-2, Gaia DR3, and VSX.",
         "\"This research has made use of the VizieR catalogue access tool, CDS, Strasbourg Astronomical Observatory, "
         "France (DOI : 10.26093/cds/vizier).\" Ochsenbein, F. et al. 2000, A&AS 143, 23.",
         "https://vizier.cds.unistra.fr"),
    ]),
    ("Methods", [
        ("Lomb-Scargle periodogram", "Period search.",
         "Lomb, N. R. 1976, Ap&SS 39, 447; Scargle, J. D. 1982, ApJ 263, 835; VanderPlas, J. T. 2018, ApJS 236, 16.", ""),
        ("Gaia G, BP-RP to Johnson V", "V for Gaia stars when the ESA archive is unavailable.",
         "Riello, M. et al. 2021, A&A 649, A3.", ""),
        # 2.2.17 (item 1): Sloan filters.
        ("Gaia G, BP-RP to SDSS g, r, i, z", "Sloan magnitudes for Gaia stars without synthetic SDSS photometry "
         "(no value outside each relation's color range).",
         "Gaia DR3 documentation, section 5.5.1 (Carrasco, J. M. & Bellazzini, M.), Tables 5.8 and 5.10; Riello, M. et "
         "al. 2021, A&A 649, A3.",
         "https://gea.esac.esa.int/archive/documentation/GDR3/Data_processing/chap_cu5pho/cu5pho_sec_photSystem/cu5pho_ssec_photRelations.html"),
        ("Gaia DR3 synthetic SDSS photometry", "Sloan magnitudes of Gaia stars (Gaia Synthetic Photometry Catalogue).",
         "Gaia Collaboration, Montegriffo, P. et al. 2023, A&A 674, A33.", ""),
        ("SDSS to primed Sloan system", "Puts SDSS-system magnitudes on the primed g', r', i', z' system of APASS "
         "and AAVSO sequences.", "Tucker, D. L. et al. 2006, Astron. Nachr. 327, 821.", ""),
        ("Sloan limb darkening", "Quadratic limb-darkening coefficients for transit fits in Sloan filters.",
         "Claret, A. 2004, A&A 428, 1001 (VizieR J/A+A/428/1001, table 4).", ""),
        ("Tycho BT, VT to Johnson B, V", "Johnson magnitudes for Tycho-2 stars.",
         "ESA 1997, The Hipparcos and Tycho Catalogues, ESA SP-1200.", ""),
        ("HJD and BJD_TDB", "Heliocentric and barycentric times in exports (computed with Astropy).",
         "Eastman, J., Siverd, R. & Gaudi, B. S. 2010, PASP 122, 935.", ""),
        ("B-V to temperature", "Approximate temperature from color.", "Ballesteros, F. J. 2012, EPL 97, 34008.", ""),
        ("Color transformation", "Color terms that put the OSC channel colors on the Johnson B-V and V-R scales, "
         "fitted to field stars with catalog colors.",
         "Henden, A. A. & Kaitchuck, R. H. 1982, Astronomical Photometry (Van Nostrand Reinhold).", ""),
        ("Luminance weights", "Clear (CV) channel from an OSC frame.", "ITU-R Recommendation BT.601.", ""),
        ("Transit light curve", "Transit model: exact planet-star overlap areas summed over limb-darkened annuli "
         "(any grazing geometry), quadratic limb darkening.",
         "Mandel, K. & Agol, E. 2002, ApJ 580, L171; Seager, S. & Mallen-Ornelas, G. 2003, ApJ 585, 1038 (durations).", ""),
        ("Limb-darkening parametrization", "q1, q2 sampling that keeps the stellar profile physical; broad priors near "
         "V-band values for the star's temperature.",
         "Kipping, D. M. 2013, MNRAS 435, 2152; Claret, A. 2000, A&A 363, 1081.", ""),
        ("MCMC sampler", "Affine-invariant ensemble sampler (stretch move), written for SHOBS-P, for every transit fit.",
         "Goodman, J. & Weare, J. 2010, Comm. App. Math. Comp. Sci. 5, 65; Foreman-Mackey, D. et al. 2013, PASP 125, 306.", ""),
        ("Red noise (time-averaging beta)", "Transit error bars scaled for correlated noise.",
         "Pont, F., Zucker, S. & Queloz, D. 2006, MNRAS 373, 231; Winn, J. N. et al. 2008, ApJ 683, 1076.", ""),
        ("Sun and Moon positions", "Transit planner: darkness, Moon phase and distance (low-precision formulae).",
         "U.S. Naval Observatory & HM Nautical Almanac Office, The Astronomical Almanac (low-precision formulae).", ""),
        ("Swarthmore Transit Finder (Tapir)", "A button opens it with your site and search limits, to compare with "
         "SHOBS-P's own Tonight's transits list.",
         "Jensen, E. 2013, Tapir: A web interface for transit/eclipse observability, Astrophysics Source Code "
         "Library ascl:1306.007.", "https://astro.swarthmore.edu/transits/"),
        ("EXOTIC and Exoplanet Watch", "SHOBS-P reads EXOTIC's AAVSO exoplanet reports to compare fits, and writes "
         "the same report format (and uses its filter names, e.g. SI for Sloan i'). No EXOTIC code is used.",
         "Zellem, R. T. et al. 2020, PASP 132, 054401. EXOTIC is developed by Exoplanet Watch, a citizen science project "
         "managed by NASA's Jet Propulsion Laboratory.", "https://exoplanets.nasa.gov/exoplanet-watch/"),
        ("FITS World Coordinate System", "Plate solutions written in the FITS header (TAN projection), used to "
         "place catalog stars without a target.",
         "Greisen, E. W. & Calabretta, M. R. 2002, A&A 395, 1061; Calabretta, M. R. & Greisen, E. W. 2002, A&A 395, 1077.",
         "https://fits.gsfc.nasa.gov/fits_wcs.html"),
        ("SIP distortion convention", "Optical distortion terms in header plate solutions.",
         "Shupe, D. L. et al. 2005, ASP Conf. Ser. 347, 491.", ""),
        ("Scintillation noise", "The atmospheric twinkling term added to each point's error bar (2.2.2), from "
         "the telescope aperture, airmass, exposure time and site elevation.",
         "Young, A. T. 1967, AJ 72, 747; Osborn, J. et al. 2015, MNRAS 452, 1707 (modified Young approximation).", ""),
        # 2.2.17 (item 8): photon noise in electrons.
        ("CCD equation", "Each point's photon and sky noise in electrons (camera gain EGAIN, binning, debayer and "
         "flat accounted for), including the sky-level error term.",
         "Merline, W. J. & Howell, S. B. 1995, Experimental Astronomy 6, 163.", ""),
        # 2.2.17 (item 10): honest period significance.
        ("False-alarm probability", "Periodogram FAP; for long periods a night-counting check (permutation test on "
         "the nightly means) so a few nights cannot look significant.",
         "Baluev, R. V. 2008, MNRAS 385, 1279.", ""),
        ("Bootstrap resampling", "Uncertainties and the 'clear winner' test in the aperture test (frames resampled "
         "with replacement, the same frames for every aperture).",
         "Efron, B. 1979, Annals of Statistics 7, 1.", ""),
        # 2.2.14 (item 13): times of maximum / minimum.
        ("Kwee-van Woerden method", "Times of maximum and minimum (Variables Output, Time of max/min…): the "
         "branches folded about trial times, with its error estimate; checked against a local polynomial with "
         "bootstrap errors and a Fourier series at the catalog period, and O−C against VSX elements.",
         "Kwee, K. K. & van Woerden, H. 1956, Bull. Astron. Inst. Netherlands 12, 327.", ""),
        # 2.2.15 (item 13): display stretch.
        ("Asinh stretch", "Display stretch on the Blink and Photometry images (asinh option; display only).",
         "Lupton, R. H., Gunn, J. E. & Szalay, A. S. 1999, AJ 118, 1406.", ""),
        ("Median absolute deviation", "Robust scatter of light curves in the aperture test, comp health, and field scans.",
         "Hampel, F. R. 1974, JASA 69, 383; Rousseeuw, P. J. & Croux, C. 1993, JASA 88, 1273.", ""),
        ("AAVSO Extended File Format", "Variable-star report files.", "American Association of Variable Star Observers.",
         "https://www.aavso.org/aavso-extended-file-format"),
    ]),
]


def credits_text() -> str:
    lines = []
    for section, items in CREDITS:
        lines.append(section.upper())
        lines.append("")
        for name, use, cite, url in items:
            lines.append(f"{name}")
            lines.append(f"  Used for: {use}")
            lines.append(f"  {cite}")
            if url:
                lines.append(f"  {url}")
            lines.append("")
    return "\n".join(lines)


# ---- Calibration frames mixed in with the lights --------------------------------------

_CAL_TYPES = ("bias", "zero", "dark", "flat", "master", "offset")


def is_calibration_frame(path: str, header: dict | None = None) -> str:
    """Return a short reason if this file is a bias/dark/flat or a master, else ''."""
    name = os.path.basename(path).lower()
    for word in ("master", "bias", "dark", "flat"):
        if name.startswith(word) or f"_{word}" in name.split("light")[0]:
            return f"file name says {word}"
    if header is None:
        try:
            header = read_header(path)
        except Exception:
            header = {}
    kind = str((header or {}).get("IMAGETYP", "") or (header or {}).get("FRAMETYP", "")).strip().lower()
    if kind and any(word in kind for word in _CAL_TYPES) and "light" not in kind:
        return f"IMAGETYP = {kind}"
    return ""


def split_lights(paths: list[str], verdict=None) -> tuple[list[str], list[tuple[str, str]], str]:
    """(lights to use, [(skipped path, reason)], note for the log).

    Master frames are always skipped. Individual bias/dark/flat files mixed in with lights are
    skipped too. But when most of the folder is labeled bias/dark/flat, the night's lights were
    almost certainly saved with the wrong image type by the capture software, so they are all
    kept and the note says so. 2.2.17 (round 2): `verdict` (path -> reason) lets a caller cache the header reads.
    """
    keep, skipped, labelled = [], [], []
    for path in paths:
        reason = (verdict or is_calibration_frame)(path)
        if not reason:
            keep.append(path)
        elif "master" in reason or os.path.basename(path).lower().startswith("master"):
            skipped.append((path, reason))
        else:
            labelled.append((path, reason))
    note = ""
    candidates = len(keep) + len(labelled)
    if labelled and len(labelled) > 0.5 * candidates:
        keep = sorted(keep + [p for p, _ in labelled])
        note = (f"{len(labelled)} of {candidates} files in the Lights folder are labeled as calibration frames "
                f"({labelled[0][1]}). That is most of the folder, so they are treated as lights; the capture "
                "software probably saved this night with the wrong image type.")
    else:
        skipped.extend(labelled)
    return keep, skipped, note


# ---- Binning --------------------------------------------------------------------------

def bin_points(observations: list[Obs], values: list[float], interval_minutes: float | None,
               scint: dict | None = None) -> list[dict]:
    """Average points by night (interval None) or by time bins of `interval_minutes` within a night.

    Each bin: jd (mean), value (mean), err (standard error from the scatter, never smaller than
    the typical point error over sqrt(n)), n, night. Bins with fewer than 3 points are dropped.
    """
    groups: dict[tuple, list[int]] = {}
    for i, (obs, v) in enumerate(zip(observations, values)):
        if not math.isfinite(v):
            continue
        if interval_minutes:
            key = (obs.night, math.floor(obs.jd * 1440.0 / interval_minutes))
        else:
            key = (obs.night,)
        groups.setdefault(key, []).append(i)
    bins = []
    for key, idx in groups.items():
        if len(idx) < 3:
            continue
        vals = np.array([values[i] for i in idx], dtype=float)
        errs = np.array([point_err(observations[i], scint) for i in idx], dtype=float)
        n = len(idx)
        sem = float(np.std(vals, ddof=1) / math.sqrt(n))
        floor = float(np.nanmedian(errs) / math.sqrt(n)) if np.isfinite(errs).any() else 0.0
        bins.append({
            "jd": float(np.mean([observations[i].jd for i in idx])),
            "value": float(np.mean(vals)),
            "err": max(sem, floor),
            "n": n,
            "night": key[0],
        })
    bins.sort(key=lambda b: b["jd"])
    return bins


# ---- Time systems ---------------------------------------------------------------------

TIME_SYSTEMS = ("JD_UTC", "HJD_UTC", "BJD_TDB")


def convert_times(jd_utc: np.ndarray, system: str, ra_deg: float | None, dec_deg: float | None,
                  lat: float | None, lon: float | None) -> np.ndarray:
    """Geocentric JD (UTC) to HJD (UTC) or BJD (TDB), using Astropy's light-travel-time model.

    BJD_TDB is the standard for exoplanet and timing work (Eastman et al. 2010).
    Needs the target RA/Dec and the site; raises ValueError if they are missing.
    """
    jd_utc = np.asarray(jd_utc, dtype=float)
    if system == "JD_UTC":
        return jd_utc
    if None in (ra_deg, dec_deg, lat, lon):
        raise ValueError("HJD and BJD need the target RA/Dec and the site latitude/longitude on the Input page")
    site = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=300 * u.m)
    times = Time(jd_utc, format="jd", scale="utc", location=site)
    target = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg)
    if system == "HJD_UTC":
        return (times.utc + times.light_travel_time(target, kind="heliocentric")).jd
    if system == "BJD_TDB":
        return (times.tdb + times.light_travel_time(target, kind="barycentric")).jd
    raise ValueError(f"Unknown time system {system}")


# ---- Times of maximum / minimum and O-C (2.2.14 item 13) --------------------------------
# 2.2.14 (item 13): the time of a light-curve extremum from one night, three ways (Kwee & van Woerden 1956; a local
# polynomial with bootstrap errors; a Fourier series at a known period as a cross-check), its O-C against catalog
# (VSX) or typed elements, and a CSV of times that grows night by night into an O-C diagram. Works on the light
# curve alone (any camera, solved or not); HJD/BJD need RA/Dec (typed, looked up, or from the solve).

EXTREMUM_COLUMNS = ("star", "type", "hjd", "bjd_tdb", "jd_utc", "error_d", "error_min", "method", "cycle", "o_c_d",
                    "o_c_min", "period_d", "epoch", "elements", "night", "n_points", "window_min", "kvw_jd",
                    "kvw_err", "poly_jd", "poly_err", "fourier_jd", "fourier_err", "time_note", "note")

ECLIPSING_TYPES = ("EA", "EB", "EW", "E", "ELL")


def extremum_kind_for(var_type: str) -> str:
    """'min' for eclipsing binaries (their elements count minima), 'max' for pulsators and the rest."""
    head = _re.split(r"[^A-Za-z]", str(var_type or "").strip().upper())[0]
    return "min" if head in ECLIPSING_TYPES else "max"


def _running_median(t: np.ndarray, m: np.ndarray, width: float) -> np.ndarray:
    lo = np.searchsorted(t, t - width / 2.0, side="left")
    hi = np.searchsorted(t, t + width / 2.0, side="right")
    return np.array([np.median(m[a:b]) for a, b in zip(lo, hi)])


def _clean_sorted(t, m):
    t = np.asarray(t, dtype=float)
    m = np.asarray(m, dtype=float)
    ok = np.isfinite(t) & np.isfinite(m)
    t, m = t[ok], m[ok]
    order = np.argsort(t)
    return t[order], m[order]


def extremum_window(t, m, kind: str = "max", t_guess: float | None = None, period: float | None = None,
                    depart: float = 0.35) -> dict:
    """Locate one extremum roughly and choose a window around it.

    The curve is smoothed with a running median; the extremum is the brightest (kind 'max', smallest magnitude) or
    faintest point of the smoothed curve (near t_guess when given: within a quarter period, or a quarter of the
    night). The half-width is where the smoothed curve has moved `depart` x the night's amplitude from the extreme,
    taking the nearer side so the window is symmetric (Kwee-van Woerden needs both branches).
    Returns {"t0", "half_width", "amplitude", "short_side": '' | 'before' | 'after', "n_before", "n_after"}."""
    t, m = _clean_sorted(t, m)
    if t.size < 12:
        raise ValueError("too few points to find an extremum (need 12 or more)")
    dt = float(np.median(np.diff(t))) if t.size > 1 else 0.0
    span = float(t[-1] - t[0])
    width = max(7.0 * dt, 0.03 * period if period else 0.02 * span)
    sm = _running_median(t, m, width)
    sign = -1.0 if kind == "max" else 1.0     # brighter = smaller magnitude
    if t_guess is not None and math.isfinite(t_guess):
        reach = 0.25 * period if period else 0.25 * span
        near = np.abs(t - t_guess) <= reach
        if not near.any():
            raise ValueError("no points near the expected time")
        idx = np.where(near)[0]
        i0 = int(idx[np.argmax(sign * sm[idx])])
    else:
        i0 = int(np.argmax(sign * sm))
    amp = float(np.max(sm) - np.min(sm))
    if not amp > 0:
        raise ValueError("the light curve is flat; no extremum to time")
    level = depart * amp

    def reach_side(step):
        k = i0
        while 0 <= k + step < t.size:
            k += step
            if abs(sm[k] - sm[i0]) >= level:
                return abs(t[k] - t[i0]), True
        return abs(t[k] - t[i0]), False

    d_before, ok_before = reach_side(-1)
    d_after, ok_after = reach_side(+1)
    short = "" if ok_before and ok_after else ("before" if not ok_before else "after")
    hw = min(d_before, d_after)
    t0 = float(t[i0])
    n_before = int(np.sum((t >= t0 - hw) & (t < t0)))
    n_after = int(np.sum((t > t0) & (t <= t0 + hw)))
    return {"t0": t0, "half_width": float(hw), "amplitude": amp, "short_side": short,
            "n_before": n_before, "n_after": n_after, "cadence": dt}


def kwee_van_woerden(t, m, t_guess: float, half_width: float, n_trial: int = 201) -> dict:
    """Time of a symmetric extremum by Kwee & van Woerden (1956, BAN 12, 327).

    For trial times T the curve is folded about T and S(T) = mean of (m(T + tau) - m(T - tau))^2 over pairs tau =
    cadence, 2 x cadence ... half_width (linear interpolation between the measured points). S is smallest where the
    two branches match; a parabola S = aT^2 + bT + c through the trials around the smallest S gives
    T0 = -b / 2a and sigma^2 = (4ac - b^2) / (4a^2 (Z - 1)) with Z = N / 4 (N measured points within half_width),
    as in the paper. Returns {"t", "err", "n", "s_min"}; raises ValueError when the window is not covered."""
    t, m = _clean_sorted(t, m)
    hw = float(half_width)
    dt = float(np.median(np.diff(t))) if t.size > 2 else 0.0
    if not (hw > 0 and dt > 0):
        raise ValueError("Kwee-van Woerden: no window")
    shift = 0.5 * hw
    lo_t, hi_t = max(t_guess - shift, t[0] + hw), min(t_guess + shift, t[-1] - hw)
    if hi_t <= lo_t:
        raise ValueError("Kwee-van Woerden: the extremum is not covered on both sides")
    taus = np.arange(1, int(hw / dt) + 1) * dt
    if taus.size < 3:
        raise ValueError("Kwee-van Woerden: too few points in the window")
    trials = np.linspace(lo_t, hi_t, n_trial)
    s = np.array([np.mean((np.interp(T + taus, t, m) - np.interp(T - taus, t, m)) ** 2) for T in trials])
    k = int(np.argmin(s))
    half = max(3, n_trial // 10)
    a_, b_ = max(0, k - half), min(n_trial, k + half + 1)
    if b_ - a_ < 5:
        raise ValueError("Kwee-van Woerden: the minimum of S is at the edge of the search")
    x = trials[a_:b_] - trials[k]
    a, b, c = np.polyfit(x, s[a_:b_], 2)
    if not a > 0:
        raise ValueError("Kwee-van Woerden: S has no minimum here")
    t0 = float(trials[k] - b / (2.0 * a))
    n = int(np.sum(np.abs(t - t0) <= hw))
    z = n / 4.0
    var = (4.0 * a * c - b * b) / (4.0 * a * a * (z - 1.0)) if z > 1 else float("nan")
    return {"t": t0, "err": float(math.sqrt(var)) if var > 0 else float("nan"), "n": n,
            "s_min": float(c - b * b / (4.0 * a))}


def _poly_extreme(x, y, degree: int, kind: str, limit: float = 0.85):
    coef = np.polyfit(x, y, degree)
    roots = np.roots(np.polyder(coef))
    cand = [float(r.real) for r in roots if abs(r.imag) < 1e-9 and abs(r.real) <= limit]
    if not cand:
        return None, coef
    vals = np.polyval(coef, cand)
    pick = int(np.argmin(vals)) if kind == "max" else int(np.argmax(vals))
    # Must be a true extremum of the right kind (second derivative sign).
    second = np.polyval(np.polyder(coef, 2), cand[pick])
    if (kind == "max" and second <= 0) or (kind == "min" and second >= 0):
        return None, coef
    return cand[pick], coef


def poly_extremum(t, m, t_guess: float, half_width: float, kind: str = "max", degree: int | None = None,
                  n_boot: int = 500, seed: int = 1, block_minutes: float = 5.0, noise=None) -> dict:
    """Time of an extremum from a polynomial fitted to the points within half_width of t_guess.

    The degree (2-5) is chosen by the Bayesian information criterion unless given; the error is the scatter of the
    extremum over n_boot bootstrap resamples of the points (Efron 1979), with half the change when the degree goes
    up by one added in quadrature (the model's own uncertainty on an asymmetric peak).
    2.2.14 review: the bootstrap treats the points as independent, so with red noise (seeing, guiding, thin cloud) it
    was ~4x too small; it is multiplied by the residuals' time-averaging beta (Pont et al. 2006; Winn et al. 2008).
    Returns {"t", "err", "boot_err", "model_err", "beta", "degree", "n"}."""
    t, m = _clean_sorted(t, m)
    hw = float(half_width)
    sel = np.abs(t - t_guess) <= hw
    x, y = (t[sel] - t_guess) / hw, m[sel]
    n = int(x.size)
    if n < 8:
        raise ValueError("polynomial: too few points in the window")
    free_degree = degree is None
    if free_degree:
        degree = _bic_degree(x, y)
    x0, _ = _poly_extreme(x, y, degree, kind)
    if x0 is None:
        raise ValueError("polynomial: no extremum inside the window")
    model_err = 0.0
    if n >= degree + 7:
        x1, _ = _poly_extreme(x, y, degree + 1, kind)
        if x1 is not None:
            model_err = 0.5 * abs(x1 - x0) * hw
    rng = np.random.default_rng(seed)
    boots = []
    # 2.2.14 review: a moving-block bootstrap of the residuals (blocks of ~block_minutes added back onto the fitted
    # curve at the same times), so noise correlated over minutes (seeing, guiding, thin cloud) is resampled together
    # and widens the spread as it should; with white noise it is the ordinary residual bootstrap.
    cad = float(np.median(np.diff(t[sel]))) if n > 1 else 0.0
    blk = int(max(1, min(n // 4, round(block_minutes / 1440.0 / cad)))) if cad > 0 else 1
    fit = np.polyval(np.polyfit(x, y, degree), x)
    res = y - fit
    # The window's own residuals hide red noise (the polynomial bends into it); the whole night's residuals about a
    # Fourier series at the period (noise=) show it, so their blocks are used when given.
    pool = np.asarray(noise, dtype=float) if noise is not None else res
    pool = pool[np.isfinite(pool)]
    if pool is not res and pool.size >= 2 * n:
        # Whole-night residuals: each draw is one stretch of real noise as long as the window ("prayer bead" style,
        # cf. Desert et al. 2009 for transit times), so correlations of any length up to the window survive.
        blk = n
    if pool.size < blk + 1:
        pool = res
    for _ in range(int(n_boot)):
        starts = rng.integers(0, pool.size - blk + 1, int(math.ceil(n / blk)))
        idx = (starts[:, None] + np.arange(blk)[None, :]).ravel()[:n]
        yb = fit + pool[idx]
        # Review: the degree is chosen again on every resample, so a degree that flips with the noise (3, 4 or 5 on
        # a HADS maximum) widens the spread as it widens the real scatter.
        xb, _ = _poly_extreme(x, yb, _bic_degree(x, yb) if free_degree else degree, kind)
        if xb is not None:
            boots.append(xb)
    boots = np.asarray(boots)
    if boots.size < max(20, n_boot // 4):
        raise ValueError("polynomial: the bootstrap could not find the extremum often enough")
    # What the residuals still hide (a fit bends into minutes-long noise): their time-averaging beta, taken at the
    # worst bin length up to half the window (whole-night residuals when given, else the window's own).
    beta = _red_beta_max(pool, max(2, n), min_bins=4)
    boot_err = float(np.std(boots, ddof=1)) * hw * beta
    return {"t": float(t_guess + x0 * hw), "err": float(math.hypot(boot_err, model_err)), "boot_err": boot_err,
            "model_err": float(model_err), "beta": float(beta), "degree": int(degree), "n": n}


def _bic_degree(x, y) -> int:
    """Polynomial degree 2-5 with the smallest Bayesian information criterion (2 when too few points)."""
    n = len(x)
    best = None
    for d in range(2, 6):
        if n < d + 6:
            break
        coef = np.polyfit(x, y, d)
        rss = float(np.sum((np.polyval(coef, x) - y) ** 2))
        bic = n * math.log(max(rss, 1e-30) / n) + (d + 1) * math.log(n)
        if best is None or bic < best[0]:
            best = (bic, d)
    return best[1] if best else 2


def _red_beta_max(resid, max_bin: int, min_bins: int = 6) -> float:
    """2.2.14 review: time-averaging beta (Pont et al. 2006; Winn et al. 2008) of evenly spaced residuals, the
    largest over bin lengths 2 .. max_bin points with at least min_bins bins; 1 for white noise (never below 1)."""
    r = np.asarray(resid, dtype=float)
    r = r[np.isfinite(r)]
    s1 = float(np.std(r)) if r.size else 0.0
    best = 1.0
    if r.size < 20 or s1 <= 0:
        return best
    for nb in range(2, int(max_bin) + 1):
        m = r.size // nb
        if m < min_bins:
            break
        means = r[: m * nb].reshape(m, nb).mean(axis=1)
        best = max(best, float(np.std(means)) / (s1 / math.sqrt(nb) * math.sqrt(m / (m - 1.0))))
    return best


def _red_beta(t, resid, span_days: float) -> float:
    """2.2.14 review: transit_core.red_noise_beta on fit residuals, averaging over up to half the window (<= 30 min)."""
    import transit_core as _tc
    t = np.asarray(t, dtype=float)
    if t.size < 20:
        return 1.0
    cad = float(np.median(np.diff(t)))
    try:
        return float(_tc.red_noise_beta(t, resid, cad, max_minutes=min(30.0, max(2.0, span_days * 1440.0))))
    except Exception:
        return 1.0


def _fourier_design(t, period, n_harm, t_ref):
    ph = 2.0 * math.pi * (np.asarray(t) - t_ref) / period
    cols = [np.ones_like(ph)]
    for k in range(1, n_harm + 1):
        cols += [np.cos(k * ph), np.sin(k * ph)]
    return np.vstack(cols).T


def fourier_extremum(t, m, period: float, t_guess: float, kind: str = "max", n_harm: int | None = None,
                     n_boot: int = 200, seed: int = 2) -> dict:
    """Cross-check: a Fourier series at a known period (harmonics chosen by BIC, up to 8) fitted to the whole night,
    and the time of its extremum within a quarter period of t_guess. Error from refits with the residuals
    resampled. Returns {"t", "err", "n_harm", "rms", "n"}."""
    t, m = _clean_sorted(t, m)
    if not (period and period > 0):
        raise ValueError("Fourier: needs a period")
    n = t.size
    t_ref = float(t_guess)
    if n_harm is None:
        best = None
        for h in range(1, 9):
            if n < 2 * h + 8:
                break
            a = _fourier_design(t, period, h, t_ref)
            coef, *_ = np.linalg.lstsq(a, m, rcond=None)
            rss = float(np.sum((a @ coef - m) ** 2))
            bic = n * math.log(max(rss, 1e-30) / n) + (2 * h + 1) * math.log(n)
            if best is None or bic < best[0]:
                best = (bic, h)
        if best is None:
            raise ValueError("Fourier: too few points")
        n_harm = best[1]
    a = _fourier_design(t, period, n_harm, t_ref)
    coef, *_ = np.linalg.lstsq(a, m, rcond=None)
    model = a @ coef
    resid = m - model
    grid = t_ref + np.linspace(-0.25, 0.25, 2001) * period
    g = _fourier_design(grid, period, n_harm, t_ref)

    def extreme(c):
        v = g @ c
        return float(grid[int(np.argmin(v)) if kind == "max" else int(np.argmax(v))])

    t0 = extreme(coef)
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(int(n_boot)):
        cb, *_ = np.linalg.lstsq(a, model + rng.choice(resid, n, replace=True), rcond=None)
        boots.append(extreme(cb))
    beta = _red_beta(t, resid, 0.25 * period)   # 2.2.14 review: red noise, as for the polynomial
    err = float(np.std(boots, ddof=1)) * beta if len(boots) > 1 else float("nan")
    return {"t": t0, "err": err, "beta": float(beta), "n_harm": int(n_harm),
            "resid": resid,
            "rms": float(np.sqrt(np.mean(resid ** 2))), "n": int(n)}


def measure_extremum(t, m, kind: str = "max", period: float | None = None, t_guess: float | None = None,
                     half_width: float | None = None, n_boot: int = 500, seed: int = 1) -> dict:
    """One night's time of maximum or minimum (JD of the input times): the local polynomial with its bootstrap error
    is adopted; Kwee-van Woerden and (with a period) a Fourier series are measured alongside as checks.
    Returns {"t", "err", "method", "kind", "half_width", "window", "kvw", "poly", "fourier", "warnings", "notes"};
    raises ValueError when the extremum cannot be timed."""
    t, m = _clean_sorted(t, m)
    win = extremum_window(t, m, kind, t_guess, period)
    hw = float(half_width) if half_width else win["half_width"]
    warnings = []
    if win["short_side"]:
        warnings.append(f"the night {'starts' if win['short_side'] == 'before' else 'ends'} close to the "
                        f"{'maximum' if kind == 'max' else 'minimum'}: one branch is short")
    if win["short_side"] and win["half_width"] <= 0:
        raise ValueError(f"the {'maximum' if kind == 'max' else 'minimum'} is at the "
                         f"{'start' if win['short_side'] == 'before' else 'end'} of the night: not covered on both sides")
    if win["n_before"] < 5 or win["n_after"] < 5:
        raise ValueError(f"the {'maximum' if kind == 'max' else 'minimum'} is not covered on both sides "
                         f"({win['n_before']} points before, {win['n_after']} after)")
    noise = None
    if period:
        # 2.2.14 review: the whole night's residuals about a Fourier series carry the red noise for the bootstrap.
        # The number of harmonics is chosen on 5-minute means, so the series cannot bend into minutes-long noise
        # (chosen on every frame, BIC took 7-8 harmonics and hid half of it).
        try:
            n_harm = None
            edges = np.arange(t[0], t[-1] + 5.0 / 1440.0, 5.0 / 1440.0)
            which = np.digitize(t, edges)
            tb = np.array([t[which == k].mean() for k in np.unique(which)])
            mb = np.array([m[which == k].mean() for k in np.unique(which)])
            if tb.size >= 12:
                n_harm = fourier_extremum(tb, mb, period, win["t0"], kind, n_boot=0)["n_harm"]
            noise = fourier_extremum(t, m, period, win["t0"], kind, n_harm=n_harm, n_boot=0)["resid"]
        except (ValueError, np.linalg.LinAlgError):
            noise = None
    poly = poly_extremum(t, m, win["t0"], hw, kind, n_boot=max(50, n_boot // 5), seed=seed, noise=noise)
    # Second pass centered on the first answer, so the window is symmetric about the extremum itself.
    poly = poly_extremum(t, m, poly["t"], hw, kind, n_boot=n_boot, seed=seed, noise=noise)
    try:
        kvw = kwee_van_woerden(t, m, poly["t"], hw)
    except ValueError as exc:
        kvw = None
        warnings.append(str(exc))
    fourier = None
    if period:
        try:
            fourier = fourier_extremum(t, m, period, poly["t"], kind, seed=seed + 1)
        except ValueError as exc:
            warnings.append(str(exc))
    notes = []
    # 2.2.14 review: the adopted error is never below half the disagreement with the Fourier series (and, for a
    # minimum, which is usually symmetric, with Kwee-van Woerden; on an asymmetric maximum KvW's bias is known).
    err = poly["err"]
    floor = 0.0
    if fourier:
        floor = 0.5 * abs(fourier["t"] - poly["t"])
    if kind == "min" and kvw:
        floor = max(floor, 0.5 * abs(kvw["t"] - poly["t"]))
    if floor > err:
        notes.append(f"error raised from {err * 1440:.2f} to {floor * 1440:.2f} min: half the difference between "
                     "the methods")
        err = floor
    if poly.get("beta", 1.0) > 1.2:
        notes.append(f"red noise: the bootstrap error was multiplied by beta {poly['beta']:.1f}")
    if kvw and math.isfinite(kvw.get("err", float("nan"))):
        diff = kvw["t"] - poly["t"]
        if abs(diff) > 3.0 * math.hypot(err, kvw["err"]):
            # Synthetic HADS maxima (fast rise, slow fall) put KvW ~1 min late: it assumes mirror-image branches.
            notes.append(f"Kwee-van Woerden reads {abs(diff) * 1440:.1f} min {'later' if diff > 0 else 'earlier'}: "
                         "the extremum is asymmetric (KvW assumes mirror-image branches), so the polynomial is used")
    if fourier and math.isfinite(fourier.get("err", float("nan"))):
        diff = fourier["t"] - poly["t"]
        if abs(diff) > 3.0 * math.hypot(poly["err"], fourier["err"]) and abs(diff) > 2.0 * err:
            warnings.append(f"the Fourier series at the period reads {abs(diff) * 1440:.1f} min "
                            f"{'later' if diff > 0 else 'earlier'} (more than 3 sigma): a second period, a short "
                            "branch, or the wrong period")
    if not period:
        # 2.2.14 review 2: without a period the bootstrap only sees the window's own residuals, which hide noise
        # lasting minutes (tests: error low by about 2-3x on red noise).
        notes.append("no period given: noise lasting minutes cannot be measured, so this error may be low by a factor "
                     "of 2-3; give the elements for an honest error")
    return {"t": poly["t"], "err": err, "method": f"polynomial deg {poly['degree']} + bootstrap",
            "kind": kind, "half_width": hw, "window": win, "kvw": kvw, "poly": poly, "fourier": fourier,
            "warnings": warnings, "notes": notes}


def predicted_extrema(t_start: float, t_end: float, period: float, epoch: float, margin: float = 0.0) -> list:
    """Times epoch + E x period that fall inside [t_start - margin, t_end + margin]."""
    if not (period and period > 0):
        return []
    e0 = math.ceil((t_start - margin - epoch) / period)
    e1 = math.floor((t_end + margin - epoch) / period)
    return [epoch + e * period for e in range(e0, e1 + 1)]


def o_minus_c(t_obs: float, period: float, epoch: float, half_cycles: bool = False) -> dict:
    """Cycle count E (nearest whole cycle, or half cycle for a secondary minimum), calculated time epoch + E x P and
    O-C in days and minutes. t_obs and epoch must be in the same time system (VSX epochs are HJD)."""
    e = (float(t_obs) - float(epoch)) / float(period)
    cycle = round(e * 2.0) / 2.0 if half_cycles else float(round(e))
    calc = float(epoch) + cycle * float(period)
    oc = float(t_obs) - calc
    return {"cycle": cycle, "calc": calc, "oc_d": oc, "oc_min": oc * 1440.0}


def extremum_times(jd_utc: float, ra_deg, dec_deg, lat, lon) -> dict:
    """HJD (UTC) and BJD (TDB) of one geocentric JD (UTC). Without RA/Dec both are NaN; without a site, a point on the
    equator at longitude 0 stands in (the site changes the light time by at most ~0.02 s)."""
    out = {"jd_utc": float(jd_utc), "hjd": float("nan"), "bjd_tdb": float("nan"), "note": ""}
    if ra_deg is None or dec_deg is None:
        out["note"] = "no RA/Dec: HJD/BJD not computed (light-time correction up to 8.3 min missing)"
        return out
    site_note = ""
    if lat is None or lon is None:
        lat, lon, site_note = 0.0, 0.0, "site unknown: computed for 0° N 0° E (at most 0.02 s off)"
    for key, system in (("hjd", "HJD_UTC"), ("bjd_tdb", "BJD_TDB")):
        out[key] = float(convert_times(np.array([jd_utc]), system, ra_deg, dec_deg, lat, lon)[0])
    out["note"] = site_note
    return out


def extremum_csv_name(star: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_+." else "_" for ch in str(star or "target").strip()) or "target"
    return f"{safe}_times_of_extremum.csv"


_EXTREMUM_NUMBERS = ("hjd", "bjd_tdb", "jd_utc", "error_d", "error_min", "cycle", "o_c_d", "o_c_min", "period_d",
                     "epoch", "n_points", "window_min", "kvw_jd", "kvw_err", "poly_jd", "poly_err", "fourier_jd",
                     "fourier_err")


def same_star_text(a, b) -> bool:
    """2.2.14 review: star names in the times CSV match ignoring case and spacing ('SZ Lyn' = 'sz  lyn')."""
    def norm(x):
        return " ".join(str(x or "").split()).lower()
    return norm(a) == norm(b)


def read_extremum_csv(path: str) -> list[dict]:
    """Rows of a times-of-extremum CSV (comment lines skipped), our number columns as floats; text columns and any
    column SHOBS-P does not know (added by hand or by another program) stay text, so a rewrite keeps them."""
    rows = []
    if not path or not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = [ln.rstrip("\r\n") for ln in fh if ln.strip() and not ln.startswith("#")]
    if not lines:
        return rows
    head = [h.strip() for h in lines[0].split(",")]
    for ln in lines[1:]:
        parts = ln.split(",")
        row = {}
        for k, v in zip(head, parts):
            v = v.strip()
            if k in _EXTREMUM_NUMBERS:
                try:
                    row[k] = float(v)
                except ValueError:
                    row[k] = float("nan") if not v else v
            else:
                row[k] = v
        rows.append(row)
    return rows


def _extremum_header(path: str) -> list[str]:
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            if ln.strip() and not ln.startswith("#"):
                return [h.strip() for h in ln.rstrip("\r\n").split(",")]
    return []


def save_extremum_row(path: str, row: dict) -> str:
    """Add one time of extremum to the CSV (created if missing). A row for the same star and type within 0.01 d is
    replaced (a re-measurement). The first '# Created' line is kept and an '# Updated' line follows.
    Returns 'added' or 'replaced'."""
    old = read_extremum_csv(path)
    created = None
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            first = fh.readline().strip()
        if first.startswith("# Created"):
            created = first

    def key_t(r):
        for k in ("hjd", "jd_utc"):
            v = r.get(k)
            if isinstance(v, float) and math.isfinite(v):
                return v
        return float("nan")

    new_t = key_t(row)
    kept = [r for r in old if not (same_star_text(r.get("star"), row.get("star")) and r.get("type") == row.get("type")
                                   and abs(key_t(r) - new_t) < 0.01)]
    extra = [h for h in _extremum_header(path) if h and h not in EXTREMUM_COLUMNS]   # 2.2.14 review: keep them
    columns = list(EXTREMUM_COLUMNS) + extra
    status = "replaced" if len(kept) < len(old) else "added"
    rows = sorted(kept + [row], key=lambda r: (key_t(r) if math.isfinite(key_t(r)) else 0.0))

    def cell(k, v):
        if isinstance(v, float):
            if not math.isfinite(v):
                return ""
            if k in ("hjd", "bjd_tdb", "jd_utc", "kvw_jd", "poly_jd", "fourier_jd", "epoch"):
                return f"{v:.5f}"
            if k in ("error_d", "o_c_d", "kvw_err", "poly_err", "fourier_err"):
                return f"{v:.5f}"
            if k == "period_d":
                return f"{v:.8f}"
            if k == "cycle":
                return f"{v:g}"
            return f"{v:.2f}"
        if isinstance(v, int):
            return str(v)
        return str(v if v is not None else "").replace(",", ";").replace("\n", " ").strip()[:160]

    with AsciiWriter(path) as fh:
        fh.write((created or created_line()) + "\n")
        if created:
            fh.write(f"# Updated {dtg()} by SHOBS-P\n")
        fh.write(",".join(columns) + "\n")
        for r in rows:
            fh.write(",".join(cell(k, r.get(k, "")) for k in columns) + "\n")
    return status


# ---- Comparison ensemble health -------------------------------------------------------

def comp_health(observations: list[Obs], names: list[str], check_name: str = "",
                spare_names: list[str] | None = None, aliases: dict | None = None,
                night_sign: dict | None = None) -> dict | None:
    """Measure each comp, spare comp, and the check star against the others, frame by frame.

    For every star, its instrumental magnitude minus the mean of the other stars in the same
    frame removes clouds and airmass. A steady star gives a flat line; a star that drifts
    from night to night, or scatters more than the rest, is the one to drop. The reference
    for each star uses only the stars present in most frames, so a spare that exists on a
    few nights cannot shift the others.

    2.2.14 (item 20): each star's flip step on every night with both sides ("flip_steps": {night: step}, after minus
    before, times night_sign[night] when given so every night reads the same pair of pier sides the same way), with
    "pier" / "pier_kind" from pier_verdicts: steady on both sides, or a stepper.
    """
    spare_names = list(spare_names or [])
    aliases = aliases or {}
    # 2.2.8: one star labeled from two catalogs (TYC on some nights, APASS or Gaia on others) is one row, measured
    # every night under whichever name it had (Use comps already counted it so; health saw "one night").
    taken: set = set()
    kept_spares = []
    for n in names:
        taken.add(n)
        taken.update(aliases.get(n, ()))
    for n in spare_names:
        if n in taken:
            continue
        kept_spares.append(n)
        taken.add(n)
        taken.update(aliases.get(n, ()))
    spare_names = kept_spares
    labels, kinds = [], []
    for n in names:
        labels.append(n)
        kinds.append("comp")
    for n in spare_names:
        labels.append(n)
        kinds.append("spare")
    has_check = bool(check_name) and any(o.kmag is not None and math.isfinite(o.kmag) for o in observations)
    if has_check:
        labels.append(f"{check_name} (check)")
        kinds.append("check")
    rows, nights, row_jd, row_seg = [], [], [], []
    for o in observations:
        if not o.comp_insts:
            continue
        row = [star_inst(o, n, names, aliases) for n in names] + [star_inst(o, n, names, aliases) for n in spare_names]
        if has_check:
            row.append(float(o.kmag) if o.kmag is not None and math.isfinite(o.kmag) else float("nan"))
        rows.append(row)
        nights.append(o.night)
        row_jd.append(float(o.jd))
        row_seg.append(o.segment or "")
    if not rows:
        return None
    data = np.array(rows, dtype=float)
    present = np.isfinite(data).mean(axis=0)
    width = int((present > 0).sum())
    if width < 2:
        return {"labels": labels, "too_few": True}
    night_list = sorted(set(nights))
    night_arr = np.array(nights)

    def evaluate(core_set):
        resid = np.full_like(data, np.nan)
        for j in range(data.shape[1]):
            ref_cols = [c for c in range(data.shape[1]) if c != j and core_set[c]]
            if not ref_cols:
                ref_cols = [c for c in range(data.shape[1]) if c != j]
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                ref = np.nanmean(data[:, ref_cols], axis=1)
            resid[:, j] = data[:, j] - ref
        nightly = {lab: {} for lab in labels}
        summary = []
        flip_rows = np.array([sg == "flip" for sg in row_seg])
        for j, lab in enumerate(labels):
            within = []
            steps = []
            night_steps = {}   # 2.2.14 (item 20)
            seg_scatter = []
            for night in night_list:
                sel = (night_arr == night) & np.isfinite(resid[:, j])
                if sel.sum() >= 3:
                    v = resid[sel, j]
                    nightly[lab][night] = float(np.median(v))
                    within.append(1.4826 * float(np.median(np.abs(v - np.median(v)))))
                    # 2.2.2: the step at a meridian flip inside this night, when both sides have 3+ points.
                    pre, post = sel & ~flip_rows, sel & flip_rows
                    if pre.sum() >= 3 and post.sum() >= 3:
                        m_pre, m_post = float(np.median(resid[pre, j])), float(np.median(resid[post, j]))
                        steps.append(m_post - m_pre)
                        night_steps[night] = (m_post - m_pre) * float((night_sign or {}).get(night, 1.0))
                        # Scatter with the step taken out, to judge whether the step is real.
                        flat = np.concatenate([resid[pre, j] - m_pre, resid[post, j] - m_post])
                        seg_scatter.append(1.4826 * float(np.median(np.abs(flat))))
            meds = np.array(list(nightly[lab].values()))
            spread = float(np.max(np.abs(meds - np.median(meds)))) if meds.size >= 2 else float("nan")
            summary.append({"label": lab, "kind": kinds[j], "night_spread": spread,
                            "scatter": float(np.median(within)) if within else float("nan"),
                            "flip_step": max(steps, key=abs) if steps else float("nan"),
                            "seg_scatter": float(np.median(seg_scatter)) if seg_scatter else float("nan"),
                            "nights": len(meds), "flip_steps": night_steps})
        spreads = np.array([s["night_spread"] for j, s in enumerate(summary)
                            if math.isfinite(s["night_spread"]) and core_set[j]])
        typical = float(np.median(spreads)) if spreads.size else float("nan")
        for j, s in enumerate(summary):
            sp = s["night_spread"]
            if not math.isfinite(sp):
                # 2.2.2: one night only. Judge the night itself: scatter against the others, and any jump at the flip.
                sc, st = s["scatter"], s["flip_step"]
                sc_seg = s["seg_scatter"] if math.isfinite(s["seg_scatter"]) else sc
                others = [x["scatter"] for k, x in enumerate(summary) if k != j and math.isfinite(x["scatter"])]
                other_steps = [abs(x["flip_step"]) for k, x in enumerate(summary)
                               if k != j and math.isfinite(x["flip_step"])]
                # One star jumping makes the others look as if they jump a little the other way: flag only the
                # star whose step clearly stands out from the rest.
                typical_step = float(np.median(other_steps)) if other_steps else 0.0
                if not math.isfinite(sc):
                    s["verdict"] = "too few points"
                elif width < 3:
                    s["verdict"] = ("one night; pair jumps at the flip, add a 3rd star to tell which"
                                    if math.isfinite(st) and abs(st) > 0.015 else "one night; steady pair")
                elif (math.isfinite(st) and abs(st) > 0.015 and abs(st) > 3.0 * sc_seg
                      and abs(st) > 2.0 * typical_step):
                    s["verdict"] = f"SUSPECT: jumps {st:+.3f} at the flip"
                elif others and sc > 0.01 and sc > 1.5 * float(np.median(others)):
                    s["verdict"] = "SUSPECT: noisier than the others tonight"
                else:
                    s["verdict"] = "steady tonight (one night)"
            elif width < 3:
                s["verdict"] = "pair drifts; add a 3rd star to tell which" if sp > 0.01 else "steady"
            elif sp > 0.01 and sp > 1.5 * typical:
                s["verdict"] = "SUSPECT: drifts night to night"
            else:
                s["verdict"] = "steady"
        return nightly, summary, resid

    core_set = present >= 0.8
    nightly, summary, resid = evaluate(core_set)
    # A drifting star in the reference makes every other star look as if it drifts the opposite way.
    # Take the suspects out of the reference and measure again, so steady stars read as steady.
    excluded = []
    for _ in range(3):
        suspects = [j for j, s in enumerate(summary) if s["verdict"].startswith("SUSPECT") and core_set[j]]
        if not suspects or int(core_set.sum()) - len(suspects) < 2:
            break
        for j in suspects:
            core_set[j] = False
            excluded.append(labels[j])
        nightly, summary, resid = evaluate(core_set)
    pier_verdicts(summary)   # 2.2.14 (item 20)
    return {"labels": labels, "kinds": kinds, "nights": night_list, "nightly": nightly, "summary": summary,
            "excluded": excluded, "too_few": False,
            # 2.2.2: frame-by-frame residuals for the within-night plot.
            "resid": {lab: resid[:, j] for j, lab in enumerate(labels)}, "jd": np.array(row_jd),
            "row_night": list(nights), "row_flip": [sg == "flip" for sg in row_seg]}


# ---- Configurable export ----------------------------------------------------------------

EXPORT_COLUMNS = ("err", "night", "segment", "flag", "airmass", "check", "comp", "fwhm", "color", "note")


def write_export(path: str, observations: list[Obs], values: list[float], setup: dict, meta: dict,
                 times: np.ndarray, notes: dict[str, str]) -> int:
    """Write a CSV in the user's chosen layout. Returns the number of data rows.

    setup: {"time": one of TIME_SYSTEMS, "values": "mag" or "flux", "binning": None | "night" | minutes,
            "columns": subset of EXPORT_COLUMNS}
    values: the magnitudes to export (already zero-pointed / lined up as the user chose).
    times: the converted times, one per observation.
    """
    cols = set(setup.get("columns") or [])
    as_flux = setup.get("values") == "flux"
    finite = np.array([v for v in values if math.isfinite(v)])
    ref = float(np.median(finite)) if finite.size else 0.0
    lines = [
        f"# {meta.get('software', 'Shiloh Hill Observatory - Photometry (SHOBS-P)')}",
        created_line(),
        f"# target: {meta.get('target', '')}   filter: {meta.get('filter', '')}   observer: {meta.get('observer', '')}",
        f"# comparison stars: {meta.get('comps', '')}   check: {meta.get('check', '')}",
        f"# time: {setup.get('time')} (mid-exposure)   values: "
        + ("relative flux, median = 1" if as_flux else "magnitude"),
        f"# processing: {meta.get('processing', '')}",
    ]
    for night in sorted(notes):
        if notes[night]:
            lines.append(f"# note {night}: {notes[night]}")
    lines.append("# credits: see the app's Credits and sources page")
    binning = setup.get("binning")
    rows = []
    if binning:
        interval = None if binning == "night" else float(binning)
        bins = bin_points(observations, values, interval, setup.get("scint"))
        offset = {}
        if setup.get("time") != "JD_UTC" and observations:
            jd = np.array([o.jd for o in observations])
            delta = np.asarray(times) - jd
            offset = float(np.median(delta))
        header = ["time", "flux" if as_flux else "mag", "err", "n", "night"]
        for b in bins:
            v, e = b["value"], b["err"]
            if as_flux:
                f = 10 ** (-0.4 * (v - ref))
                v, e = f, e * f / 1.085736
            t = b["jd"] + (offset if isinstance(offset, float) else 0.0)
            rows.append([f"{t:.6f}", f"{v:.6f}" if as_flux else f"{v:.4f}", f"{e:.6f}" if as_flux else f"{e:.4f}",
                         str(b["n"]), b["night"]])
    else:
        header = ["time", "flux" if as_flux else "mag"]
        order = [c for c in EXPORT_COLUMNS if c in cols]
        names = {"err": "err", "night": "night", "segment": "segment", "flag": "flag", "airmass": "airmass",
                 "check": "check_mag", "comp": "comp_inst_mag", "fwhm": "fwhm_px", "color": "b_minus_v",
                 "note": "night_note"}
        header += [names[c] for c in order]
        seg_words = segment_words(observations)
        for o, v, t, sw in zip(observations, values, times, seg_words):
            if not math.isfinite(v):
                continue
            e = point_err(o, setup.get("scint"))
            if as_flux:
                f = 10 ** (-0.4 * (v - ref))
                v_text, e_text = f"{f:.6f}", f"{e * f / 1.085736:.6f}"
            else:
                v_text, e_text = f"{v:.4f}", f"{e:.4f}"
            row = [f"{t:.6f}", v_text]
            for c in order:
                row.append({
                    "err": e_text, "night": o.night, "segment": sw, "flag": o.flag.replace(",", " "),
                    "airmass": "" if o.airmass is None or not math.isfinite(o.airmass) else f"{o.airmass:.4f}",
                    "check": "" if o.check_std is None or not math.isfinite(o.check_std) else f"{o.check_std:.4f}",
                    "comp": f"{o.cmag:.4f}" if math.isfinite(o.cmag) else "",
                    "fwhm": f"{o.fwhm:.2f}" if math.isfinite(o.fwhm) else "",
                    "color": f"{o.bv:.3f}" if math.isfinite(o.bv) else "",
                    "note": (notes.get(o.night, "") or "").replace(",", ";"),
                }[c])
            rows.append(row)
    with open(path, "w", encoding="ascii", errors="replace") as fh:
        fh.write(ascii_safe("\n".join(lines)) + "\n")
        fh.write(",".join(header) + "\n")
        for row in rows:
            fh.write(ascii_safe(",".join(row)) + "\n")
    return len(rows)


# ---- 2.2: plate solutions in the FITS header, calibration sanity, names, comps ----------------

def read_header(path: str) -> dict:
    """Just the header of a FITS (or XISF) file (fast: the pixels are not read)."""
    if path.lower().endswith(".xisf"):
        return read_xisf(path, header_only=True)[1]
    if fits is None:
        raise RuntimeError("astropy is required. Install with: pip install astropy")
    with fits.open(path, memmap=True) as hdul:
        hdu = hdul[0]
        if hdu.header.get("NAXIS", 0) == 0 and len(hdul) > 1:
            hdu = hdul[1]
        return dict(hdu.header)


def _sip_terms(header: dict, prefix: str) -> list[tuple[int, int, float]]:
    try:
        order = int(header.get(f"{prefix}_ORDER", 0) or 0)
    except (TypeError, ValueError):
        return []
    terms = []
    for p in range(order + 1):
        for q in range(order + 1 - p):
            v = header.get(f"{prefix}_{p}_{q}")
            if v is None:
                continue
            try:
                c = float(v)
            except (TypeError, ValueError):
                continue
            if c != 0.0:
                terms.append((p, q, c))
    return terms


def header_wcs(header: dict) -> dict | None:
    """A celestial plate solution (TAN, with SIP distortion when present) written into the FITS header by the
    capture or plate-solving software, or None. Pixel coordinates are 0-based on the frame as saved."""
    if not header:
        return None
    t1, t2 = str(header.get("CTYPE1", "")).upper(), str(header.get("CTYPE2", "")).upper()
    if not (t1.startswith("RA---TAN") and t2.startswith("DEC--TAN")):
        return None
    try:
        crval = (float(header["CRVAL1"]), float(header["CRVAL2"]))
        crpix = (float(header["CRPIX1"]) - 1.0, float(header["CRPIX2"]) - 1.0)
        if header.get("CD1_1") is not None:
            cd = np.array([[float(header.get("CD1_1", 0.0)), float(header.get("CD1_2", 0.0))],
                           [float(header.get("CD2_1", 0.0)), float(header.get("CD2_2", 0.0))]])
        else:
            cdelt1, cdelt2 = float(header["CDELT1"]), float(header["CDELT2"])
            pc = np.array([[float(header.get("PC1_1", 1.0)), float(header.get("PC1_2", 0.0))],
                           [float(header.get("PC2_1", 0.0)), float(header.get("PC2_2", 1.0))]])
            if header.get("PC1_1") is None and header.get("CROTA2") is not None:
                # FITS convention: CD1_2 = -CDELT2 sin(rho), CD2_1 = CDELT1 sin(rho).
                rot = math.radians(float(header["CROTA2"]))
                pc = np.array([[math.cos(rot), -math.sin(rot) * cdelt2 / cdelt1],
                               [math.sin(rot) * cdelt1 / cdelt2, math.cos(rot)]])
            cd = np.diag([cdelt1, cdelt2]) @ pc
    except (KeyError, TypeError, ValueError):
        return None
    det = float(np.linalg.det(cd))
    if not np.all(np.isfinite(cd)) or abs(det) < 1e-20 or not (-90 <= crval[1] <= 90):
        return None
    sip = "SIP" in t1 and "SIP" in t2
    return {"crval": crval, "crpix": crpix, "cd": cd, "cd_inv": np.linalg.inv(cd),
            "a": _sip_terms(header, "A") if sip else [], "b": _sip_terms(header, "B") if sip else [],
            "ap": _sip_terms(header, "AP") if sip else [], "bp": _sip_terms(header, "BP") if sip else [],
            "scale_arcsec": math.sqrt(abs(det)) * 3600.0}


def _poly(terms, u, v):
    out = np.zeros_like(np.asarray(u, dtype=float))
    for p, q, c in terms:
        out = out + c * np.power(u, p) * np.power(v, q)
    return out


def wcs_pixel_to_sky(wcs: dict, x, y):
    """0-based pixel(s) on the frame as saved -> RA, Dec in degrees."""
    u = np.asarray(x, dtype=float) - wcs["crpix"][0]
    v = np.asarray(y, dtype=float) - wcs["crpix"][1]
    if wcs["a"] or wcs["b"]:
        u, v = u + _poly(wcs["a"], u, v), v + _poly(wcs["b"], u, v)
    cd = wcs["cd"]
    xi = np.radians(cd[0, 0] * u + cd[0, 1] * v)
    eta = np.radians(cd[1, 0] * u + cd[1, 1] * v)
    a0, d0 = math.radians(wcs["crval"][0]), math.radians(wcs["crval"][1])
    rho = np.hypot(xi, eta)
    c = np.arctan(rho)
    safe = np.where(rho > 0, rho, 1.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        ra = a0 + np.arctan2(xi * np.sin(c), rho * math.cos(d0) * np.cos(c) - eta * math.sin(d0) * np.sin(c))
        dec = np.arcsin(np.clip(np.cos(c) * math.sin(d0) + eta * np.sin(c) * math.cos(d0) / safe, -1.0, 1.0))
    dec = np.where(rho > 0, dec, d0)
    return np.degrees(ra) % 360.0, np.degrees(dec)


def wcs_sky_to_pixel(wcs: dict, ra, dec):
    """RA, Dec in degrees -> 0-based pixel(s) on the frame as saved (NaN for points behind the tangent plane)."""
    a = np.radians(np.asarray(ra, dtype=float))
    d = np.radians(np.asarray(dec, dtype=float))
    a0, d0 = math.radians(wcs["crval"][0]), math.radians(wcs["crval"][1])
    cosc = math.sin(d0) * np.sin(d) + math.cos(d0) * np.cos(d) * np.cos(a - a0)
    with np.errstate(invalid="ignore", divide="ignore"):
        xi = np.degrees(np.cos(d) * np.sin(a - a0) / cosc)
        eta = np.degrees((math.cos(d0) * np.sin(d) - math.sin(d0) * np.cos(d) * np.cos(a - a0)) / cosc)
    inv = wcs["cd_inv"]
    U = inv[0, 0] * xi + inv[0, 1] * eta
    V = inv[1, 0] * xi + inv[1, 1] * eta
    if wcs["ap"] or wcs["bp"]:
        u, v = U + _poly(wcs["ap"], U, V), V + _poly(wcs["bp"], U, V)
    elif wcs["a"] or wcs["b"]:
        u, v = U.copy(), V.copy()
        for _ in range(8):           # invert the forward distortion by fixed-point iteration
            u, v = U - _poly(wcs["a"], u, v), V - _poly(wcs["b"], u, v)
    else:
        u, v = U, V
    x = u + wcs["crpix"][0]
    y = v + wcs["crpix"][1]
    bad = ~(cosc > 0)
    return np.where(bad, np.nan, x), np.where(bad, np.nan, y)


def wcs_to_image(x, y, factor: float):
    """Pixels on the frame as saved -> pixels on the binned, debayered image (factor = app binning x 2 for a color
    camera's superpixel debayer, x 1 for a monochrome camera)."""
    return (np.asarray(x, dtype=float) + 0.5) / factor - 0.5, (np.asarray(y, dtype=float) + 0.5) / factor - 0.5


def image_to_wcs(x, y, factor: float):
    return (np.asarray(x, dtype=float) + 0.5) * factor - 0.5, (np.asarray(y, dtype=float) + 0.5) * factor - 0.5


def wcs_center(wcs: dict, shape_saved: tuple[int, int]) -> tuple[float, float]:
    """RA, Dec (degrees) at the center of a frame of shape (rows, cols) as saved."""
    h, w = shape_saved
    ra, dec = wcs_pixel_to_sky(wcs, (w - 1) / 2.0, (h - 1) / 2.0)
    return float(ra), float(dec)


def wcs_solve(wcs: dict, factor: float, sources: list, stars: list[dict], ra_deg: float, dec_deg: float,
              scale_arcsec: float, shape: tuple[int, int], tolerance_px: float = 3.0) -> dict | None:
    """Use the header's plate solution: put the catalog stars on the image through it, check them against the
    stars actually found, and express the result in place_stars's convention (shift, rotation, mirror, scale
    about (ra_deg, dec_deg)) so the rest of the app uses it like a matched chart. None when it does not fit."""
    cat = [s for s in stars if s.get("ra") is not None and s.get("dec") is not None]
    if len(cat) < 4 or len(sources) < 4:
        return None
    ra = np.array([s["ra"] for s in cat], dtype=float)
    dec = np.array([s["dec"] for s in cat], dtype=float)
    sx, sy = wcs_sky_to_pixel(wcs, ra, dec)
    px, py = wcs_to_image(sx, sy, factor)
    h, w = shape
    inside = np.isfinite(px) & np.isfinite(py) & (px > -20) & (px < w + 20) & (py > -20) & (py < h + 20)
    if inside.sum() < 4:
        return None
    cos_dec = math.cos(math.radians(dec_deg))
    e = ((ra - ra_deg + 180.0) % 360.0 - 180.0) * 3600.0 * cos_dec / scale_arcsec
    n = (dec - dec_deg) * 3600.0 / scale_arcsec
    best = None
    for mirrored in (False, True):
        par, rms = _fit_placement(e[inside], n[inside], px[inside], py[inside], mirrored)
        if best is None or rms < best[1]:
            best = (par, rms)
    par = best[0]
    src = np.asarray(sources, dtype=float)
    # How many catalog stars land on a detected star (brightest catalog stars first, as in the other solvers).
    order = np.argsort([s.get("bright", s.get("mag", 99)) if s.get("bright", s.get("mag")) is not None else 99
                        for s in cat])
    use = [i for i in order if inside[i]][:60]
    dist = np.hypot(px[use, None] - src[None, :, 0], py[use, None] - src[None, :, 1])
    j = dist.argmin(axis=1)
    hit = dist.min(axis=1) < tolerance_px
    if hit.sum() < 4:
        return None
    # Refine the linear placement on the matched stars (absorbs a small shift between header and frame).
    hit_idx = np.array(use)[hit]
    mirrored = par["mirrored"]
    par, _rms = _fit_placement(e[hit_idx], n[hit_idx], src[j[hit], 0], src[j[hit], 1], mirrored)
    placed = [(float(src[j[k], 0]), float(src[j[k], 1]), cat[use[k]]) for k in range(len(use)) if hit[k]]
    return {"hits": int(hit.sum()), "angle": par["angle"], "mirrored": mirrored, "scale_factor": par["scale_factor"],
            "placed": placed, "target_xy": (par["tx"], par["ty"]), "score": (int(hit.sum()), 0.0),
            "match_count": len(use), "from_header": True}


def header_rotator(header: dict) -> float | None:
    """Camera rotator angle in degrees (ROTATOR, ROTATANG, ROTANG, ROTPA), or None."""
    for key in ("ROTATOR", "ROTATANG", "ROTANG", "ROTPA", "ROT_ANG"):
        v = header.get(key)
        if v is None:
            continue
        try:
            return float(v) % 360.0
        except (TypeError, ValueError):
            continue
    return None


def rotator_excursion_start(angles: list) -> int | None:
    """2.2.7: index of the first frame after a rotator excursion, else None. angles: each light's rotator reading in
    time order (rejected frames included, None when a header has none). A mount flip done with the camera turned
    back to its first angle leaves the images unflipped (ROTATOR 360 → 180 during the flip → 360/0 again), so the
    alignment sees no flip, but the light path through the telescope has changed: treat the frames after the
    excursion as a new segment, as a flip."""
    ref = next((a for a in angles if a is not None), None)
    if ref is None:
        return None
    seen_excursion = False
    for i, a in enumerate(angles):
        if a is None:
            continue
        far = angle_difference(a, ref) > 90.0
        if far:
            seen_excursion = True
        elif seen_excursion:
            return i
    return None


def angle_difference(a: float, b: float) -> float:
    """Smallest difference between two angles in degrees (360 and 0 are the same)."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def rotator_difference(a: float, b: float) -> float:
    """Difference between two rotator readings that ignores a meridian flip: on a German mount the readings on the
    two sides of the pier differ by 180° for the same camera position on the telescope (30° and 210° are the same
    position), so the angles are compared modulo 180°."""
    d = angle_difference(a, b)
    return min(d, 180.0 - d)


# ---- 2.2.18 (item 8): alt-az mounts and derotators ----------------------------------------
# SHOBS-P grew up on a German equatorial mount (GEM): meridian flips, pier sides, a rotator that turns 180° at the
# flip. An alt-az mount has none of these. With a derotator (a Nasmyth PlaneWave PW1000 run by NINA) ROTATOR is the
# sky position angle the derotator holds (it drifts smoothly by 0.04-0.3° over a night), and NINA writes PIERSIDE
# 'East' all night; without a derotator the field turns on the sensor through the night. The mount comes from the
# headers when they say it unambiguously, else from the Mount choice in the Optics box.

MOUNT_GEM = "gem"
MOUNT_ALTAZ = "altaz"
MOUNT_LABELS = {MOUNT_GEM: "German equatorial", MOUNT_ALTAZ: "Alt-az"}
ROTATOR_DRIFT_MIN = 0.01      # degrees: a derotator's smooth drift over the night is at least this ...
ROTATOR_DRIFT_MAX = 0.5       # ... and at most this (PW1000: 0.04-0.34°); John's ASIAIR solved PA creeps 1-2° on a GEM
_ROTATOR_DEVICE_KEYS = ("ROTNAME", "ROTSTPSZ")   # a rotator device named in the header (NINA)
ROTATOR_STEP_MAX = 2.0        # degrees: larger steps between frames are camera moves or flips, not drift
ALTAZ_FLAT_PA_TOL = 5.0       # degrees: flats and lights at sky position angles further apart = camera turned
_ALTAZ_NAME = _re.compile(
    r"planewave interface|\bpwi ?[34]\b|\bl-?(?:350|500|600)\b|\bpw ?1000\b|\bcdk ?(?:700|1000)\b|nasmyth|derotator"
    r"|\balt[- /]?az|unistellar|evscope|seestar|vespera|stellina|\bdwarf ?(?:ii|2|3|lab)\b", _re.I)
_MOUNT_KEYS = ("MOUNTTYP", "MOUNTTYPE", "MNTTYPE", "MOUNT", "TELMOUNT")


def mount_label(mount: str | None) -> str:
    return MOUNT_LABELS.get(mount or "", MOUNT_LABELS[MOUNT_GEM])


def _header_time_key(header: dict):
    return str(header.get("DATE-OBS") or header.get("DATE-BEG") or "")


def rotator_drift(angles: list) -> dict | None:
    """2.2.18 (item 8): does a night's rotator reading drift smoothly (a derotator holding the sky's position angle)?
    angles in time order (None skipped). Smooth: no step over ROTATOR_STEP_MAX, a total change of at least
    ROTATOR_DRIFT_MIN, the reading changing on most steps and mostly in one direction (a static GEM rotator reads the
    same value all night; jitter goes both ways). Returns {"first", "last", "span"} or None."""
    vals = [float(a) for a in angles if a is not None and math.isfinite(float(a))]
    if len(vals) < 4:
        return None
    steps = [((b - a + 180.0) % 360.0) - 180.0 for a, b in zip(vals, vals[1:])]
    if any(abs(d) > ROTATOR_STEP_MAX for d in steps):
        return None
    total = sum(steps)
    if abs(total) < ROTATOR_DRIFT_MIN:
        return None
    moving = [d for d in steps if abs(d) > 1e-6]
    if len(moving) < 0.6 * len(steps):
        return None
    if sum(1 for d in moving if d * total > 0) < 0.8 * len(moving):
        return None
    return {"first": vals[0], "last": vals[-1], "span": abs(total)}


def detect_mount(headers: list[dict]) -> dict:
    """2.2.18 (item 8): the mount type from a night's light headers (any order; sorted by DATE-OBS when present).
    Returns {"mount": MOUNT_GEM / MOUNT_ALTAZ / None, "sure": bool, "derotator": True / False / None,
    "why": words, "max_alt": highest CENTALT/OBJCTALT or None}. Sure GEM: PIERSIDE takes both sides in the night, or the
    rotator turns by 180° (a meridian flip). Sure alt-az: a mount-type keyword says so, a Unistellar eVscope, or a
    derotator: the rotator drifts smoothly by ROTATOR_DRIFT_MIN to ROTATOR_DRIFT_MAX while PIERSIDE never changes,
    a rotator device is named (ROTNAME / ROTSTPSZ) AND the telescope is a usual alt-az system. Drift alone is never
    enough (review): an ASIAIR on a German mount writes ROTATOR as each frame's solved angle, which creeps 1-2° on a
    night without a flip. Only a hint (sure False): the telescope or rotator name is a usual alt-az system (PlaneWave
    PWI4, PW1000, CDK700, L-series, which can also sit on a wedge). mount None: the headers cannot tell; the Mount
    choice decides."""
    hs = [h for h in (headers or []) if isinstance(h, dict)]
    out = {"mount": None, "sure": False, "derotator": None, "why": "", "max_alt": None}
    if not hs:
        return out
    if all(_header_time_key(h) for h in hs):
        hs = sorted(hs, key=_header_time_key)
    alts = [_header_float(h, "CENTALT", "OBJCTALT") for h in hs]
    alts = [a for a in alts if a is not None and -5.0 <= a <= 90.0]
    out["max_alt"] = max(alts) if alts else None
    sides = {("east" if "east" in str(h.get("PIERSIDE")).lower() else "west")
             for h in hs if any(w in str(h.get("PIERSIDE") or "").lower() for w in ("east", "west"))}
    rots = [header_rotator(h) for h in hs]
    known = [r for r in rots if r is not None]
    flip = any(abs(angle_difference(a, b) - 180.0) <= ROTATOR_STEP_MAX for a, b in zip(known, known[1:]))
    if len(sides) == 2:
        out.update(mount=MOUNT_GEM, sure=True, why="PIERSIDE changes sides during the night (a meridian flip)")
        return out
    if flip:
        out.update(mount=MOUNT_GEM, sure=True, why="the rotator reading turns by 180° (a meridian flip)")
        return out
    for h in hs[:1]:
        for key in _MOUNT_KEYS:
            text = str(h.get(key) or "").lower()
            if not text:
                continue
            if _re.search(r"\balt[- /]?az|azimuth", text):
                out.update(mount=MOUNT_ALTAZ, sure=True, why=f"{key} = '{h.get(key)}'")
                return out
            if _re.search(r"equatorial|\bgem\b|german", text):
                out.update(mount=MOUNT_GEM, sure=True, why=f"{key} = '{h.get(key)}'")
                return out
    names = " / ".join(str(hs[0].get(k) or "") for k in ("TELESCOP", "ROTNAME", "INSTRUME") + _MOUNT_KEYS
                       if hs[0].get(k))
    m = _ALTAZ_NAME.search(names)
    drift = rotator_drift(rots)
    device = any(hs[0].get(k) not in (None, "") for k in _ROTATOR_DEVICE_KEYS)
    if drift is not None and m and device and drift["span"] <= ROTATOR_DRIFT_MAX:
        out.update(mount=MOUNT_ALTAZ, sure=True, derotator=True,
                   why=(f"the rotator drifts smoothly ({drift['first']:.3f}° to {drift['last']:.3f}°, a derotator "
                        "holding the sky's angle)" + (f" and PIERSIDE stays '{hs[0].get('PIERSIDE')}'"
                                                      if sides else "")))
        return out
    if is_unistellar(hs[0]):
        out.update(mount=MOUNT_ALTAZ, sure=True, derotator=False, why="a Unistellar eVscope (alt-az, no derotator)")
        return out
    if m:
        out.update(mount=MOUNT_ALTAZ, sure=False, why=f"the headers name '{m.group(0)}', usually an alt-az system")
    return out


def resolve_mount(detected: dict | None, choice: str | None) -> tuple[str, str]:
    """2.2.18 (item 8): (mount used, note). The headers win only when they are sure; otherwise the Mount choice
    (German equatorial when none was made). The note says what was used and why ('' for a plain GEM default)."""
    choice = choice if choice in MOUNT_LABELS else MOUNT_GEM
    d = detected or {}
    if d.get("sure") and d.get("mount") in MOUNT_LABELS:
        if d["mount"] == choice:
            return choice, ("" if choice == MOUNT_GEM else
                            f"Mount: {mount_label(choice)} (the headers agree: {d.get('why')}).")
        return d["mount"], (f"Mount: {mount_label(d['mount'])} from the headers ({d.get('why')}); the Mount box "
                            f"says {mount_label(choice)} and is kept for other lights. If this is your own "
                            f"telescope, set Mount to {mount_label(d['mount'])} so the transit planners use it.")
    if d.get("mount") and d["mount"] != choice:
        return choice, (f"Mount: {mount_label(choice)} as chosen in the Optics box, but {d.get('why')}. If this "
                        f"is an {mount_label(d['mount']).lower()} mount, set Mount to {mount_label(d['mount'])}.")
    return choice, ("" if choice == MOUNT_GEM else f"Mount: {mount_label(choice)} as chosen in the Optics box.")


def flat_angle_difference(flat_rot: float, light_rot: float, mount: str | None = None) -> float:
    """2.2.18 (item 8): how far the camera turned between the flats and the lights, from their rotator readings.
    German mount (and unknown): flip-aware (modulo 180°, rotator_difference), as before: after a meridian flip the
    reading changes by 180° but the camera keeps its place on the telescope.
    Alt-az: there is no flip, so a reading 180° away is a real turn: plain difference (modulo 360°). That is the
    rule for an alt-az mount WITHOUT a derotator, where the reading is a mechanical camera angle (a fixed rotator):
    the caller asks above ALTAZ_FLAT_PA_TOL.
    With a derotator (review) the reading is the sky angle it holds, and the camera turns against the optics all
    night anyway (it follows the field rotation). Flats then correct dust on the filter, window and sensor fully, and
    dust/vignetting in front of the derotator only in its symmetric part, whatever the flats' angle; so a derotated
    night gets one log caution, never a question (DEROTATOR_FLAT_NOTE)."""
    if mount == MOUNT_ALTAZ:
        return angle_difference(flat_rot, light_rot)
    return rotator_difference(flat_rot, light_rot)


DEROTATOR_FLAT_NOTE = (
    "on a derotator the camera turns against the optics during the night anyway; flats correct dust on the "
    "filter/window/sensor fully, and dust/vignetting in front of the derotator only in its symmetric part, whatever "
    "the flats' angle")


def flat_angle_tolerance(mount: str | None = None) -> float:
    """2.2.18 (item 8): degrees between flats and lights before the flats-angle caution (2° on a German mount)."""
    return ALTAZ_FLAT_PA_TOL if mount == MOUNT_ALTAZ else 2.0


# ---- Pier side across a campaign (2.2.14 item 20) ---------------------------------------
# 2.2.14 (item 20): HD 219134, 25 Sep–14 Oct: the 210° (after-meridian) side read 0.02–0.06 brighter than the 30°
# side on 3 of 5 nights, the check star moving the same way. Each night records which side (camera angle) each of its
# segments was on, so a campaign can be normalized side by side and comps that step at the flip can be found.

FLIP_STEP_MARK = 0.02   # a night's flip step above this is marked in the night table
PIER_ANGLE_TOL = 5.0    # 2.2.15 (item 10): camera angles within this many degrees are one pier side


def side_label(angle) -> str:
    """'30°' for a rotator reading (to the nearest degree, 360 = 0)."""
    return f"{int(round(float(angle))) % 360}°"


def night_pier_info(rot_before=None, rot_after=None, pier_before=None, pier_after=None,
                    has_flip: bool = False) -> dict:
    """A night's pier sides: {"source", "before", "after", "note"}. before/after label the segment before the
    meridian flip and the one after it ('' when the night has no such segment).

    ROTATOR (or ROTATANG ...) readings name the side by camera angle ('30°' / '210°'). Without them, the header's
    PIERSIDE ('East' / 'West'). Without either, a night with a flip seen in the image orientation gets 'before flip' /
    'after flip'; a night without one gets no side at all (it cannot be placed)."""
    def num(v):
        try:
            v = float(v)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    rb, ra = num(rot_before), num(rot_after)
    out = {"source": "", "before": "", "after": "", "note": ""}
    if rb is not None or ra is not None:
        out["source"] = "ROTATOR"
        out["before"] = side_label(rb) if rb is not None else ""
        if has_flip and ra is not None:
            out["after"] = side_label(ra)
            if rb is not None and angle_difference(ra, rb) <= PIER_ANGLE_TOL:
                # The camera was turned back to its first angle after the flip: same angle, other pier side.
                out["after"] = side_label(ra) + " flipped"
        elif has_flip and rb is not None:
            out["after"] = side_label(rb + 180.0)
        return out
    pb = str(pier_before or "").strip().lower()
    pa = str(pier_after or "").strip().lower()
    if pb or pa:
        def word(p):
            return "East" if "east" in p else "West" if "west" in p else (p.title() if p else "")
        out["source"] = "PIERSIDE"
        out["before"] = word(pb)
        out["after"] = word(pa) if has_flip else ""
        return out
    if has_flip:
        out["source"] = "flip"
        out["before"], out["after"] = "before flip", "after flip"
        out["note"] = "no ROTATOR or PIERSIDE in the headers: sides from the image orientation (the flip)"
        return out
    out["note"] = "no ROTATOR or PIERSIDE and no flip: pier side unknown"
    return out


def pier_side_groups(angles, tol: float = PIER_ANGLE_TOL) -> dict:
    """2.2.15 (item 10): camera angles grouped into pier sides. An angle joins a side when it is within ±tol of any
    angle already on it (29° and 30°; 358° and 2° across 360), but never more than 2·tol from the side's most
    frequent angle, so a chain cannot walk toward the other side. The 180° partners of a side are the other side of
    the same pair (210°). Each side is named by its most frequent angle (ties: the smaller). The result does not
    depend on the order of the readings. angles: readings in degrees (one per night and segment, repeats count);
    None / NaN are skipped. Returns {whole-degree angle: representative whole-degree angle}."""
    counts: dict[int, int] = {}
    for a in angles:
        try:
            a = float(a)
        except (TypeError, ValueError):
            continue
        if math.isfinite(a):
            k = int(round(a)) % 360
            counts[k] = counts.get(k, 0) + 1
    out: dict[int, int] = {}
    remaining = set(counts)
    reach = min(2.0 * tol, 45.0)
    while remaining:
        # The most frequent angle left anchors a pair of sides; its family grows by links within tol (mod 180).
        axis = min(remaining, key=lambda x: (-counts[x], x))
        family, frontier = {axis}, [axis]
        while frontier:
            x = frontier.pop()
            for y in sorted(remaining - family):
                if rotator_difference(x, y) <= tol and rotator_difference(y, axis) <= reach:
                    family.add(y)
                    frontier.append(y)
        remaining -= family
        near = [x for x in family if angle_difference(x, axis) <= 90.0]
        far = [x for x in family if angle_difference(x, axis) > 90.0]
        for side in (near, far):
            if side:
                rep = min(side, key=lambda x: (-counts[x], x))
                for x in side:
                    out[x] = rep
    return out


_SIDE_ANGLE = _re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*°(.*)$")


def group_pier_sides(pier_by_night: dict, tol: float = PIER_ANGLE_TOL) -> dict:
    """2.2.15 (item 10): {night: pier info} with every angle side label ('29°', '209° flipped') renamed to its
    group's representative ('30°', '210° flipped'), so nights a degree apart count as one side. Works on the labels
    alone, so series saved by 2.2.14 (per-night angles) group without re-reduction. The infos are copied, never
    changed in place; a renamed one keeps its own readings in "read" ({"before": '29°', "after": '209°'})."""
    def parse(label):
        m = _SIDE_ANGLE.match(str(label or ""))
        return (float(m.group(1)), m.group(2)) if m else None

    readings = []
    for info in pier_by_night.values():
        for key in ("before", "after"):
            got = parse((info or {}).get(key))
            if got is not None:
                readings.append(got[0])
    groups = pier_side_groups(readings, tol)
    out = {}
    for night, info in pier_by_night.items():
        if not isinstance(info, dict):
            out[night] = info
            continue
        new = dict(info)
        for key in ("before", "after"):
            got = parse(info.get(key))
            if got is not None:
                rep = groups.get(int(round(got[0])) % 360)
                if rep is not None:
                    new[key] = f"{rep}°{got[1]}"
        if (new.get("before"), new.get("after")) != (info.get("before"), info.get("after")):
            new["read"] = {"before": info.get("before") or "", "after": info.get("after") or ""}
        out[night] = new
    return out


def side_text(info: dict | None) -> str:
    """Night table text for a night's sides: '30°', '30°/210°', 'before/after flip', or '—'."""
    if not info:
        return "—"
    b, a = info.get("before") or "", info.get("after") or ""
    if info.get("source") == "flip":
        return "before/after flip"
    if b and a:
        return f"{b}/{a}"
    return b or a or "—"


def point_sides(observations: list[Obs], pier_by_night: dict) -> list[str]:
    """Each point's side label from its night's pier info and its segment ('' when unknown)."""
    out = []
    for o in observations:
        info = pier_by_night.get(o.night) or {}
        out.append((info.get("after") if o.segment == "flip" else info.get("before")) or "")
    return out


def side_offsets(observations: list[Obs], values, sides: list[str], use=None, min_points: int = 5) -> dict:
    """2.2.14 (item 20): how much each pier side reads brighter or fainter than the reference side (the side with
    the most points). Nights with both sides give the cleanest answer (the star cannot change much within a night):
    the median over those nights of (side median - reference side median). A side never seen together with the
    reference side on one night falls back on the whole-series medians (less safe for a variable star).
    Returns {"ref", "offsets": {side: mag}, "how": {side: words}, "counts": {side: points}, "nights": {side: n}}."""
    vals = np.asarray(values, dtype=float)
    ok = np.isfinite(vals) & np.array([bool(s) for s in sides], dtype=bool)
    if use is not None:
        ok &= np.asarray(use, dtype=bool)
    counts: dict[str, int] = {}
    nights_of: dict[str, set] = {}
    for o, s, good in zip(observations, sides, ok):
        if good:
            counts[s] = counts.get(s, 0) + 1
            nights_of.setdefault(s, set()).add(o.night)
    out = {"ref": "", "offsets": {}, "how": {}, "counts": counts, "nights": {s: len(n) for s, n in nights_of.items()}}
    if not counts:
        return out
    ref = max(counts, key=lambda s: (counts[s], s))
    out["ref"] = ref
    out["offsets"][ref] = 0.0
    out["how"][ref] = "reference"
    by = {}
    for o, s, v, good in zip(observations, sides, vals, ok):
        if good:
            by.setdefault((o.night, s), []).append(v)
    for side in counts:
        if side == ref:
            continue
        steps = []
        for night in sorted(nights_of[side] & nights_of[ref]):
            a, b = by.get((night, side), []), by.get((night, ref), [])
            if len(a) >= min_points and len(b) >= min_points:
                steps.append(float(np.median(a) - np.median(b)))
        if steps:
            out["offsets"][side] = float(np.median(steps))
            out["how"][side] = f"median step on {len(steps)} night{'s' if len(steps) != 1 else ''} with both sides"
        else:
            sv = vals[ok & np.array([s == side for s in sides])]
            rv = vals[ok & np.array([s == ref for s in sides])]
            out["offsets"][side] = float(np.median(sv) - np.median(rv))
            out["how"][side] = "series medians (no night has both sides)"
    return out


def apply_side_offsets(sides: list[str], values, offsets: dict) -> list[float]:
    """Each point moved onto the reference side's level ('pier side as segments')."""
    return [float(v) - offsets.get(s, 0.0) if s else float(v) for s, v in zip(sides, values)]


def pier_verdicts(summary: list[dict], steady: float = 0.01, stepper: float = 0.02) -> list[dict]:
    """2.2.14 (item 20): from comp_health's per-star flip steps (one per night with both sides), which stars read
    the same on both pier sides and which step. Adds "pier" (words) and "pier_kind" ('steady' / 'steps' / '') to
    each summary row and returns the rows."""
    for row in summary:
        steps = [v for v in (row.get("flip_steps") or {}).values() if math.isfinite(v)]
        if not steps:
            row["pier"], row["pier_kind"] = "—", ""
            continue
        med = float(np.median(steps))
        worst = max(steps, key=abs)
        n = len(steps)
        if abs(med) > stepper or (n >= 2 and sum(1 for s in steps if abs(s) > stepper) > n / 2):
            row["pier"], row["pier_kind"] = f"STEPS {med:+.3f} ({n} night{'s' if n != 1 else ''})", "steps"
        elif abs(med) <= steady and abs(worst) <= stepper:
            row["pier"], row["pier_kind"] = f"steady {med:+.3f} ({n} night{'s' if n != 1 else ''})", "steady"
        else:
            row["pier"], row["pier_kind"] = f"{med:+.3f}, worst {worst:+.3f} ({n})", ""
    return summary


def edge_problems(observations: list, shape: tuple, comp_names: list[str], check_name: str, margin: float,
                  flat: np.ndarray | None = None, flat_scale: float = 1.0, weak_flat: float = 0.85) -> list[str]:
    """2.2.3: comparison, spare and check stars that sit near the frame edge (or where the flat is weak) in a night.

    observations: one night's points; their .pos holds each star's position per frame ("c": comps in order,
    "k": check, "s": {spare name: [x, y]}). shape: (height, width) of the measured image. margin: the closest a
    star may come to an edge, in pixels. flat: optional normalized master flat (pixels of the calibrated mosaic,
    flat_scale times the measured image's). Returns one sentence per star with a problem."""
    h, w = shape[0], shape[1]
    # The flat as the measured image sees it: on a color camera one value per 2x2 superpixel (the mean of its
    # four colors, so a red or blue pixel's lower level is not mistaken for vignetting), relative to the center.
    flat_img = None
    if flat is not None:
        f = np.asarray(flat, dtype=float)
        k = max(1, int(round(flat_scale)))
        if k > 1:
            hh, ww = (f.shape[0] // k) * k, (f.shape[1] // k) * k
            f = f[:hh, :ww].reshape(hh // k, k, ww // k, k)
            with np.errstate(all="ignore"):
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    f = np.nanmean(f, axis=(1, 3))
        ch, cw = f.shape[0] // 2, f.shape[1] // 2
        dh, dw = max(1, f.shape[0] // 10), max(1, f.shape[1] // 10)
        center = float(np.nanmedian(f[ch - dh:ch + dh + 1, cw - dw:cw + dw + 1]))
        if math.isfinite(center) and center > 0:
            flat_img = f / center
    tracks: dict[str, list] = {}
    for o in observations:
        pos = getattr(o, "pos", None) or {}
        for k, xy in enumerate(pos.get("c") or []):
            if xy is not None and k < len(comp_names):
                tracks.setdefault(f"comp {comp_names[k]}", []).append(xy)
        if pos.get("k") is not None and check_name:
            tracks.setdefault(f"check {check_name}", []).append(pos["k"])
        for name, xy in (pos.get("s") or {}).items():
            if xy is not None:
                tracks.setdefault(f"spare {name}", []).append(xy)
    out = []
    for label, pts in tracks.items():
        xy = np.array(pts, dtype=float)
        if xy.size == 0:
            continue
        dist = np.minimum.reduce([xy[:, 0], xy[:, 1], (w - 1) - xy[:, 0], (h - 1) - xy[:, 1]])
        closest = float(np.nanmin(dist))
        problem = []
        if closest < margin:
            problem.append(f"comes within {max(closest, 0):.0f} px of the frame edge")
        if flat_img is not None:
            fx = np.clip(np.round(xy[:, 0]).astype(int), 0, flat_img.shape[1] - 1)
            fy = np.clip(np.round(xy[:, 1]).astype(int), 0, flat_img.shape[0] - 1)
            vals = flat_img[fy, fx]
            vals = vals[np.isfinite(vals)]
            if vals.size and float(np.min(vals)) < weak_flat:
                problem.append(f"sits where the flat is only {float(np.min(vals)) * 100:.0f}% of the center "
                               "(heavy vignetting)")
        if problem:
            out.append(f"{label} {' and '.join(problem)}: the flat corrects edges and corners least well, so it can "
                       "read wrong all night. Consider leaving it out of this night's comps.")
    return out


def night_setup_differences(new: dict, others: dict) -> list[str]:
    """2.2.3: how a night's reduction settings differ from the other nights of a series.

    new: {"binning", "radius", "sky_in", "sky_out", "comps", "check"} for the night being added.
    others: {night: the same kind of dict} (series saved before 2.2.3 hold only "binning"; missing keys are skipped).
    Returns one plain sentence per difference."""
    out = []

    def collect(key):
        vals = {}
        for night, info in sorted(others.items()):
            if isinstance(info, dict) and info.get(key) not in (None, "", []):
                vals.setdefault(_setup_text(key, info[key]), []).append(night)
        return vals

    for key, label in (("binning", "binning"), ("radius", "aperture radius"), ("sky_in", "inner sky radius"),
                       ("sky_out", "outer sky radius"), ("comps", "comparison star(s)"), ("check", "check star"),
                       ("instrument", "telescope / camera"), ("observer", "observer"),
                       ("photon_noise", "photon noise"),   # 2.2.17 (item 8, review)
                       ("rgb_channel", "channel of the color images")):   # 2.2.18 (review S4)
        mine = new.get(key)
        if mine in (None, "", []):
            continue
        theirs = collect(key)
        mine_t = _setup_text(key, mine)
        different = {t: n for t, n in theirs.items() if t != mine_t}
        if different:
            parts = "; ".join(f"{t} on {', '.join(n[:3])}{' …' if len(n) > 3 else ''}" for t, n in different.items())
            out.append(f"{label}: {mine_t} on this night, {parts}")
    # 2.2.17 (item 8, review): nights reduced before 2.2.17 have no photon_noise entry: say so when this one has it.
    if isinstance(new.get("photon_noise"), dict):
        older = sorted(n for n, info in others.items() if isinstance(info, dict) and not info.get("photon_noise"))
        if older:
            out.append(f"photon noise: {_setup_text('photon_noise', new['photon_noise'])} on this night; the "
                       f"pre-2.2.17 model (image units as electrons + 0.005 mag, larger error bars) on "
                       f"{', '.join(older[:3])}{' …' if len(older) > 3 else ''}")
    return out


def _setup_text(key: str, value) -> str:
    if key == "comps":
        return " + ".join(str(v) for v in value) if isinstance(value, (list, tuple)) else str(value)
    if key in ("radius", "sky_in", "sky_out"):
        try:
            return f"{float(value):g} px"
        except (TypeError, ValueError):
            return str(value)
    if key == "binning":
        return f"Bin {value}"
    if key == "rgb_channel":   # 2.2.18 (review S4)
        return RGB_CHANNEL_WORDS.get(value, str(value))
    if key == "photon_noise" and isinstance(value, dict):   # 2.2.17 (item 8)
        if value.get("source") == "EGAIN" and value.get("e_per_adu"):
            return f"electrons via EGAIN {float(value['e_per_adu']):.3g} e-/ADU"
        return {"sky": "electrons from the sky's noise", "assumed": "1 e-/ADU assumed",
                "old": "pre-2.2.17 model"}.get(value.get("source"), str(value.get("source")))
    return str(value)


def typical_rotator(angles: list[float], mount: str | None = None) -> float | None:
    """2.2.3: the most common camera position among rotator readings (180° apart counts as the same position),
    returned as one of the readings. None for an empty list. 2.2.18 (item 8): mount=MOUNT_ALTAZ compares without
    the flip, within ALTAZ_FLAT_PA_TOL (flat_angle_difference)."""
    if not angles:
        return None
    best, best_n = None, -1
    tol = flat_angle_tolerance(mount)
    for a in angles:
        n = sum(1 for b in angles if flat_angle_difference(a, b, mount) <= tol)
        if n > best_n:
            best, best_n = a, n
    return best


def rotator_spread(angles: list[float], mount: str | None = None) -> float | None:
    """2.2.3: the largest camera-position difference among rotator readings (flip-aware), or None.
    2.2.18 (item 8): mount as in typical_rotator."""
    if len(angles) < 2:
        return None if not angles else 0.0
    ref = typical_rotator(angles, mount)
    return max(flat_angle_difference(ref, a, mount) for a in angles)


def rotator_summary(angles: list[float], mount: str | None = None) -> str:
    """2.2.3: e.g. '30° (40 files), 2° (10 files)', grouping flip-equivalent readings. 2.2.18 (item 8): mount as in
    typical_rotator."""
    groups: list[list[float]] = []
    tol = flat_angle_tolerance(mount)
    for a in angles:
        for g in groups:
            if flat_angle_difference(g[0], a, mount) <= tol:
                g.append(a)
                break
        else:
            groups.append([a])
    groups.sort(key=len, reverse=True)
    return ", ".join(f"{g[0]:g}° ({len(g)} file{'s' if len(g) != 1 else ''})" for g in groups)


def dark_scale_problem(dark_exp: float | None, light_exp: float | None, limit: float = 3.0) -> str | None:
    """A warning when the master dark must be stretched (or shrunk) a long way to match the lights."""
    if not dark_exp or not light_exp or dark_exp <= 0 or light_exp <= 0:
        return None
    factor = light_exp / dark_exp
    if 1.0 / limit <= factor <= limit:
        return None
    return (f"The darks are {dark_exp:g} s and the lights {light_exp:g} s, so the master dark is scaled x{factor:.3g} "
            "to match. Scaling up multiplies the dark's own noise and fixed pattern (and any bias mismatch) by the same "
            "amount, and stamps it on every light.")


_NAME_PREFIXES = ("HD", "HIP", "SAO", "TYC", "GSC", "TIC", "NSV", "HR", "GJ", "BD", "CD", "UCAC4", "2MASS", "ASAS",
                  "ASASSN-V", "ZTF", "WISE", "GAIA DR3", "KIC", "EPIC", "LHS", "LP", "ROSS", "WOLF")
_CONSTELLATIONS = (
    "And Ant Aps Aqr Aql Ara Ari Aur Boo Cae Cam Cnc CVn CMa CMi Cap Car Cas Cen Cep Cet Cha Cir Col Com CrA CrB Crv "
    "Crt Cru Cyg Del Dor Dra Equ Eri For Gem Gru Her Hor Hya Hyi Ind Lac Leo LMi Lep Lib Lup Lyn Lyr Men Mic Mon Mus "
    "Nor Oct Oph Ori Pav Peg Per Phe Pic Psc PsA Pup Pyx Ret Sge Sgr Sco Scl Sct Ser Sex Tau Tel Tri TrA Tuc UMa UMi "
    "Vel Vir Vol Vul").split()


def suggest_star_name(name: str) -> str | None:
    """How AAVSO and SIMBAD usually spell a star name, when the typed one differs only in spacing or capitals:
    'hd219134' -> 'HD 219134', 'rr lyr' -> 'RR Lyr', 'v1234cyg' -> 'V1234 Cyg'. None when it looks fine."""
    text = " ".join(str(name or "").split())
    if not text:
        return None
    low = text.lower()
    cons = {c.lower(): c for c in _CONSTELLATIONS}
    # Variable-star (GCVS) names: one or two letters or V + number, then the constellation.
    m = _re.match(r"^(v\d+|[a-z]{1,2})\s*([a-z]{3})$", low)
    # mu, nu, xi, pi are Greek letters (mu Cep), not the variable-star names MU Cep etc.: too ambiguous to suggest.
    if m and m.group(1) in ("mu", "nu", "xi", "pi"):
        return None
    if m and m.group(2) in cons and not (len(m.group(1)) == 2 and m.group(1)[0] > m.group(1)[1] and m.group(1)[0] != "v"):
        star = m.group(1).upper()
        good = f"{star} {cons[m.group(2)]}"
        return good if good != text else None
    for prefix in sorted(_NAME_PREFIXES, key=len, reverse=True):
        p = prefix.lower()
        if low.startswith(p):
            rest = text[len(prefix):].lstrip(" -_")
            if not rest or not (rest[0].isdigit() or rest[0] in "+-J"):
                continue
            if prefix in ("BD", "CD") and rest[0] not in "+-":
                continue
            shown = {"GAIA DR3": "Gaia DR3", "ROSS": "Ross", "WOLF": "Wolf"}.get(prefix, prefix)
            good = f"{shown}{rest}" if prefix in ("BD", "CD") else f"{shown} {rest}"
            return good if good != text else None
    return None


def sexagesimal_ra(ra_hours: float) -> str:
    total = (ra_hours % 24.0) * 3600.0
    h = int(total // 3600)
    m = int((total - h * 3600) // 60)
    s = total - h * 3600 - m * 60
    if s >= 59.95:
        s, m = 0.0, m + 1
    if m >= 60:
        m, h = 0, (h + 1) % 24
    return f"{h:02d}h {m:02d}m {s:04.1f}s"


def sexagesimal_dec(dec_deg: float) -> str:
    sign = "-" if dec_deg < 0 else "+"
    total = abs(dec_deg) * 3600.0
    d = int(total // 3600)
    m = int((total - d * 3600) // 60)
    s = total - d * 3600 - m * 60
    if s >= 59.5:
        s, m = 0.0, m + 1
    if m >= 60:
        m, d = 0, d + 1
    return f"{sign}{d:02d}° {m:02d}′ {s:02.0f}″"


def catalog_zero_point(inst_mag: np.ndarray, catalog_mag: np.ndarray, use: np.ndarray, clip: float = 3.0,
                       min_stars: int = 3) -> dict | None:
    """Ensemble zero point: median of (catalog - instrumental) over the usable catalog stars, sigma-clipped.
    Returns {'offset', 'n', 'scatter', 'err'} or None when too few stars agree."""
    diff = np.asarray(catalog_mag, dtype=float) - np.asarray(inst_mag, dtype=float)
    ok = np.asarray(use, dtype=bool) & np.isfinite(diff)
    if ok.sum() < min_stars:
        return None
    d = diff[ok]
    for _ in range(5):
        med = float(np.median(d))
        sig = robust_sigma(d)
        keep = np.abs(d - med) <= max(clip * sig, 0.02)
        if keep.all() or keep.sum() < min_stars:
            break
        d = d[keep]
    if len(d) < min_stars:
        return None
    sig = robust_sigma(d)
    return {"offset": float(np.median(d)), "n": int(len(d)), "scatter": float(sig),
            "err": float(1.2533 * sig / math.sqrt(len(d)))}


def suggest_comps_near(target_inst: float, candidates: list, max_dmag: float = 1.0, count: int = 4,
                       target_bv: float | None = None, max_dbv: float = 0.6, aim: float = 0.0,
                       window: tuple[float, float] | None = None) -> list[tuple[str, float]]:
    """Comparison candidates near the target in brightness and, when catalog colors are known, in color.
    candidates: (name, instrumental mag, x, y[, B-V]) measured on the same image as target_inst. Ranked by
    |delta mag - aim| + |delta B-V| (a star without a color counts 0.25 for it). Returns [(name, comp - target mag)].
    2.2.8: aim and window (comp - target, mag) let a faint target get brighter comps (aim -1, window -2..+0.3):
    a comp as faint as a faint target adds as much noise as the target itself (CoRoT-1 MicroObservatory run)."""
    if target_inst is None or not math.isfinite(target_inst):
        return []
    lo, hi = window if window is not None else (-max_dmag, max_dmag)
    rows = []
    for cand in candidates:
        name, m = cand[0], cand[1]
        bv = cand[4] if len(cand) > 4 else None
        if m is None or not math.isfinite(m) or not (lo <= m - target_inst <= hi):
            continue
        if target_bv is not None and bv is not None:
            dbv = abs(bv - target_bv)
            if dbv > max_dbv:
                continue
        else:
            dbv = 0.25
        rows.append((abs(m - target_inst - aim) + dbv, name, m - target_inst))
    rows.sort()
    return [(name, dm) for _a, name, dm in rows[:count]]


def comp_noise_warning(obs: list) -> str | None:
    """2.2.8: say so when the comparison stars, not the target, set the noise. Point-to-point scatter of
    target - comps, target - check and check - comps (instrumental). If the target is much steadier against the check
    than against the comps, and the check is as noisy as the target against the comps, the comps are the noise source
    (CoRoT-1 MicroObservatory run: 6.4% against a faint comp, 1.8% against the check)."""
    rows = [(o.target_inst if math.isfinite(getattr(o, "target_inst", float("nan"))) else o.mag + 0.0, o.cmag, o.kmag)
            for o in obs if o.kmag is not None and math.isfinite(o.kmag) and math.isfinite(o.cmag)]
    if len(rows) < 15:
        return None
    t = np.array([r[0] for r in rows], dtype=float)
    c = np.array([r[1] for r in rows], dtype=float)
    k = np.array([r[2] for r in rows], dtype=float)
    if not np.all(np.isfinite(t)):
        return None

    def ptp(a):
        d = np.diff(a)
        d = d[np.isfinite(d)]
        return float(1.4826 * np.median(np.abs(d - np.median(d))) / math.sqrt(2)) if d.size >= 10 else float("nan")

    s_tc, s_tk, s_kc = ptp(t - c), ptp(t - k), ptp(k - c)
    if not all(math.isfinite(v) for v in (s_tc, s_tk, s_kc)) or s_tc <= 0:
        return None
    if s_tk < 0.6 * s_tc and s_kc > 0.6 * s_tc:
        pct = lambda m: 100 * (10 ** (0.4 * m) - 1)
        return (f"The comparison stars set most of the noise: the target scatters {pct(s_tc):.1f}% per point against "
                f"the comps but {pct(s_tk):.1f}% against the check star (and the check {pct(s_kc):.1f}% against the "
                "comps). Pick brighter, steadier comps (the check star can be one), then run photometry again.")
    return None


def align_segments(inst: np.ndarray, seg: np.ndarray, min_frames: int = 5) -> np.ndarray:
    """Line up each star's light curve (columns of inst, frames in rows) across segments (a meridian flip, a rotator
    move): every later segment with at least min_frames measurements is shifted to the first segment's median, star
    by star. A real variable keeps its changes inside each segment."""
    out = np.array(inst, dtype=float, copy=True)
    seg = np.asarray(seg)
    first = seg == seg.min()
    with np.errstate(all="ignore"):
        base = np.nanmedian(out[first], axis=0)
        for g in np.unique(seg):
            rows = seg == g
            if g == seg.min() or rows.sum() < min_frames:
                continue
            shift = base - np.nanmedian(out[rows], axis=0)
            ok = np.isfinite(shift)
            out[np.ix_(rows, ok)] += shift[ok]
    return out


# ---- 2.2.2: field-scan cautions, scintillation -------------------------------------------------

def peak_values(img: np.ndarray, pos: np.ndarray, radius: float) -> np.ndarray:
    """Highest pixel inside a box of half-width radius around each position (NaN when off the frame)."""
    h, w = img.shape[:2]
    r = int(math.ceil(radius))
    out = np.full(len(pos), np.nan)
    for k, (x, y) in enumerate(np.asarray(pos, dtype=float)):
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        x0, y0 = int(round(x)), int(round(y))
        box = img[max(0, y0 - r):min(h, y0 + r + 1), max(0, x0 - r):min(w, x0 + r + 1)]
        if box.size:
            with np.errstate(all="ignore"):
                out[k] = float(np.nanmax(box))
    return out


def scan_boundaries(seg, jd) -> list[tuple[int, str]]:
    """Frames that start something new: a meridian flip or rotator move (a new segment), or the first frame
    after a long gap in time (more than 5x the usual cadence and at least 15 minutes)."""
    seg = np.asarray(seg)
    jd = np.asarray(jd, dtype=float)
    out = []
    dt = np.diff(jd) if len(jd) > 1 else np.array([])
    usual = float(np.median(dt)) if dt.size else 0.0
    gap = max(5.0 * usual, 15.0 / 1440.0)
    for k in range(1, len(jd)):
        if seg[k] != seg[k - 1]:
            out.append((k, "flip"))
        elif jd[k] - jd[k - 1] > gap:
            out.append((k, "gap"))
    return out


def noise_at_boundary(lc, boundaries: list[tuple[int, str]], ratio: float = 3.0, min_side: int = 6) -> str | None:
    """2.2.7: does the light curve go from quiet to noisy (or the reverse) at a flip, rotator move or gap? Robust
    scatter (of frame-to-frame differences, so a slow trend does not count) on each side of each boundary; returns
    that boundary's kind when one side scatters at least `ratio` times the other. Catches a star that lands on a bad
    spot after a flip, which a change-in-level test misses."""
    lc = np.asarray(lc, dtype=float)
    best = None
    for k, kind in boundaries:
        a, b = lc[:k], lc[k:]
        a, b = a[np.isfinite(a)], b[np.isfinite(b)]
        if a.size < min_side or b.size < min_side:
            continue
        sa = 1.4826 * float(np.median(np.abs(np.diff(a) - np.median(np.diff(a))))) / math.sqrt(2)
        sb = 1.4826 * float(np.median(np.abs(np.diff(b) - np.median(np.diff(b))))) / math.sqrt(2)
        lo, hi = min(sa, sb), max(sa, sb)
        if lo <= 0 and hi > 0:
            r = float("inf")
        elif lo <= 0:
            continue
        else:
            r = hi / lo
        if r >= ratio and (best is None or r > best[0]):
            best = (r, kind)
    return best[1] if best else None


def change_at_boundary(lc, boundaries: list[tuple[int, str]], before: int = 2, ratio: float = 1.5) -> str | None:
    """Does this light curve's biggest change start just at a flip, rotator move or gap? Returns that boundary's
    kind ("flip" or "gap") or None. The curve is smoothed with a 3-point running median; the biggest departure from
    the median is followed back to where it starts (the frames on either side still at least half as far out). That
    start must fall from `before` frames ahead of a boundary to ~10% of the frames after it, and the departure must
    be at least `ratio` times the largest one anywhere else, so a star that varies all night is not flagged."""
    lc = np.asarray(lc, dtype=float)
    n = len(lc)
    if n < 8 or not boundaries:
        return None
    padded = np.concatenate([lc[:1], lc, lc[-1:]])
    with np.errstate(all="ignore"):
        smooth = np.nanmedian(np.vstack([padded[:-2], padded[1:-1], padded[2:]]), axis=0)
        signed = smooth - np.nanmedian(lc)
    dev = np.abs(signed)
    if not np.isfinite(dev).any():
        return None
    k = int(np.nanargmax(dev))
    half = 0.5 * dev[k]
    same = lambda j: np.isfinite(dev[j]) and dev[j] >= half and np.sign(signed[j]) == np.sign(signed[k])
    lo = k
    while lo > 0 and same(lo - 1):
        lo -= 1
    hi = k
    while hi < n - 1 and same(hi + 1):
        hi += 1
    after = max(3, int(round(0.1 * n)))
    rest = np.concatenate([dev[:lo], dev[hi + 1:]])
    rest = rest[np.isfinite(rest)]
    if rest.size and dev[k] < ratio * float(rest.max()):
        return None
    for b, kind in boundaries:
        # The departure starts at the boundary, or (a short stretch before it) ends there.
        if b - before <= lo < b + after or b - after <= hi + 1 <= b + before:
            return kind
    return None


def scintillation_sigma(aperture_m: float, airmass: float, exptime_s: float, altitude_m: float = 0.0) -> float:
    """Scintillation noise (relative flux, 1 sigma) for one exposure: the modified Young approximation of Osborn et
    al. (2015, MNRAS 452, 1707, eq. 7; after Young 1967), sigma^2 = 1e-5 C_Y^2 D^(-4/3) t^-1 X^3 exp(-2h/H), with
    C_Y = 1.5 (their typical-site value) and scale height H = 8000 m. D in metres, h in metres, t in seconds.
    NaN when an input is missing."""
    try:
        d, x, t, h = float(aperture_m), float(airmass), float(exptime_s), float(altitude_m or 0.0)
    except (TypeError, ValueError):
        return float("nan")
    if not (d > 0 and x >= 1.0 and t > 0 and math.isfinite(d * x * t * h)):
        return float("nan")
    return math.sqrt(1e-5) * 1.5 * d ** (-2.0 / 3.0) * x ** 1.5 * math.exp(-h / 8000.0) / math.sqrt(t)


_ASCII_MAP = {
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-", "±": "+/-", "×": "x",
    "°": " deg", "′": "'", "″": '"', "‘": "'", "’": "'", "“": '"', "”": '"',
    "…": "...", "²": "2", "³": "3", "χ": "chi", "σ": "sigma", "Δ": "d", "δ": "d",
    "µ": "u", "μ": "u", "≈": "~", "≤": "<=", "≥": ">=", "→": "->", "⚠": "!",
    "•": "*", " ": " ", " ": " ", " ": " ", "▶": ">", "◀": "<",
}


def ascii_safe(text: str) -> str:
    """2.2.2: plain ASCII for files other programs open (Excel reads a .csv in the old Windows character set, so a
    UTF-8 en dash shows as junk). Common symbols become ASCII look-alikes; accents are dropped; anything else is '?'."""
    import unicodedata
    out = []
    for ch in str(text):
        if ord(ch) < 128:
            out.append(ch)
            continue
        if ch in _ASCII_MAP:
            out.append(_ASCII_MAP[ch])
            continue
        plain = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode("ascii")
        out.append(plain if plain else "?")
    return "".join(out)


class AsciiWriter:
    """2.2.2: a text file that writes everything through ascii_safe (for CSVs and reports)."""

    def __init__(self, path: str, newline: str | None = None):
        self._fh = open(path, "w", encoding="ascii", errors="replace", newline=newline)

    def write(self, text: str) -> int:
        return self._fh.write(ascii_safe(text))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._fh.close()
        return False


def aperture_mm_from_settings(settings: dict) -> str:
    """2.2.3: the telescope aperture in millimetres from the saved settings. 2.2.2 saved it in centimetres
    ("aperture_cm"); a value under 100 there was centimetres and is converted, a larger one was almost certainly
    typed in millimetres already (356 for a 14-inch) and is kept as it is."""
    mm = str(settings.get("aperture_mm", "") or "").strip()
    if mm:
        return mm
    cm = str(settings.get("aperture_cm", "") or "").strip()
    if not cm:
        return ""
    try:
        v = float(cm)
    except ValueError:
        return ""
    if not v > 0:
        return ""
    return f"{v * 10:g}" if v < 100 else f"{v:g}"


def aperture_mm_hint(text: str) -> str:
    """2.2.3: a warning when the aperture typed in millimetres looks like a slip (centimetres, inches or metres)."""
    try:
        v = float(str(text).strip())
    except ValueError:
        return "" if not str(text).strip() else "Aperture: type a number in millimetres (356 for a 14-inch)."
    if not v > 0:
        return "Aperture: type a number in millimetres (356 for a 14-inch)."
    if v < 40:
        return (f"{v:g} mm is tiny for a telescope. Centimetres or inches by mistake? (A 14-inch is 356 mm, "
                f"35.6 cm.)")
    if v > 2000:
        return f"{v:g} mm is a {v / 1000:g} m telescope. Did you mean {v / 10:g} mm?"
    return ""


# 2.2.16 (item 5): TELESCOP values that name the MOUNT, not the telescope (ASIAIR writes 'EQMod Mount'; NINA/ASCOM
# write the mount driver). Conservative: driver/brand words and mount model numbers (EQ6, HEQ5, CEM40, GEM45, AM5,
# AZ-GTi, CGEM, CGX, AVX…), case-insensitive. "Mount" only counts when not the first word (Mount Wilson 60-inch).
_MOUNT_WORDS = _re.compile(
    r"eqmod|eqascom|\bascom\b|simulator|ioptron|\bonstep\b|paramount|10\s?micron|\bgs\s?server\b|green\s?swamp"
    r"|\bcpwi\b|star\s?adventurer|skyguider|\bindi\b|\blosmandy\b|\bwarpastron\b|\bnyx[-\s]?\d|\basiair\b"
    r"|\b(?:eq|heq|neq|cem|gem|am|cgem|cgx|avx|zeq|rst|azeq|eqm|gm|sxp|sxd|sx)[-\s]?\d"
    r"|\bcgem\b|\bcgx\b|\bavx\b|\baz[-\s]?(?:gti|eq|\d)|\bmount\b"
    # 2.2.16 (review): Astro-Physics GTO mounts (1100GTO, Mach1/Mach2GTO), Avalon, Losmandy Gemini controller,
    # 10Micron GM1000/GM2000, Mesu, Fornax, Vixen SXP/SXD, AstroTrac, Benro Polaris
    r"|\d{3,4}\s?gto\b|\bmach\s?[12]\b|\bmach[12]\s?gto|\bavalon\b|\bgemini[-\s]?\d|\bmesu\b|\bfornax\b"
    r"|\bvixen\s+sx|\bastrotrac\b|\bbenro\b|\bap\s?\d{3,4}\b(?!\s?gtx)",
    _re.IGNORECASE)
# 2.2.16 (review): words and sizes that name a telescope (OTA): when present the value is a telescope, even with a
# driver tag ("LX200 (INDI)") or a mount after "on" ("C11 on CGEM").
_OTA_WORDS = _re.compile(
    r"\blx\s?\d{2,3}|\bc\s?(?:5|6|8|9\.25|11|14)\b|edge\s?hd|\brc\s?\d*\b|\bcdk\s?\d*|\bridk\b|newton|refractor|\bapo\b"
    r"|\bsct\b|\bacf\b|dobson|\bdob\b|maksutov|\bmak\s?\d|ritchey|cassegrain|astrograph|\brasa\b|hyperstar|\bed\s?\d"
    r"|\d+\s?ed\b|\d+(?:\.\d+)?\s?(?:mm|cm|in|inch|\")(?![a-z])|\bf/\s?\d|fsq|esprit|stellarvue|askar|takahashi"
    r"|william optics|evscope|seestar|\bcpc\b|nexstar|planewave|officina|lunt|apertura|explore scientific|\bgtx\b"
    r"|\d+\s?gtx|\b\d{2,4}/\d{3,5}\b|\b\d(?:\.\d+)?\s?m\b",
    _re.IGNORECASE)
_GENERIC_TELESCOPE = {"telescope", "main", "mount", "scope", "ota", "default", "none", "unknown", "n/a", "na"}


def clean_telescope_name(text) -> str:
    """2.2.16 (item 5; review): the telescope a TELESCOP value names, or '' when it names a mount, a driver or
    nothing in particular. A driver tag "(INDI)" / "(ASCOM)", a "Telescope:" prefix and an "on <mount>" tail are
    dropped ("Telescope: C11 on CGEM" -> "C11")."""
    t = str(text or "").strip()
    if not t:
        return ""
    t = _re.sub(r"^\s*telescope\s*:\s*", "", t, flags=_re.IGNORECASE)
    t = _re.sub(r"\s*\((?:indi|ascom|alpaca)\)\s*$", "", t, flags=_re.IGNORECASE)
    head = _re.split(r"\s+on\s+", t, maxsplit=1, flags=_re.IGNORECASE)[0].strip()
    if not head or head.lower() in _GENERIC_TELESCOPE:
        return ""
    if _OTA_WORDS.search(head):
        return head
    for m in _MOUNT_WORDS.finditer(head):
        if m.group(0).lower() == "mount" and m.start() == 0:
            continue   # "Mount Wilson 60-inch": a place, not a mount
        return ""
    return head


def is_mount_name(text) -> bool:
    """2.2.16 (item 5): True when a TELESCOP value names a mount, a mount driver or nothing in particular, not a
    telescope ("EQMod Mount", "ZWO AM5", "Telescope Simulator"); False for "LX200 (INDI)", "C11 on CGEM"."""
    return bool(str(text or "").strip()) and not clean_telescope_name(text)


def telescope_text(name: str = "", telescop: str = "", aperture_mm=None, focal_mm=None) -> str:
    """2.2.16 (item 5): the telescope as written in the ExoFOP file and the night setup. The Optics box's Telescope
    field wins (as typed); else the header's TELESCOP unless it names the mount, with the aperture and focal length
    ("0.36 m RC14, f 2900 mm"); else just "0.36 m, f 2900 mm". '' when nothing is known."""
    name = str(name or "").strip()
    if name:
        return name

    def num(v):
        try:
            f = float(v)
            return f if math.isfinite(f) and f > 0 else None
        except (TypeError, ValueError):
            return None
    tel = clean_telescope_name(telescop)   # 2.2.16 (review): mount/driver parts left out
    ap, focal = num(aperture_mm), num(focal_mm)
    scope = " ".join(p for p in (f"{ap / 1000.0:.2f} m" if ap else "", tel) if p)
    if focal:
        scope = (scope + ", " if scope else "") + f"f {focal:.0f} mm"
    return scope


def instrument_without_mount(text: str) -> str:
    """2.2.16 (item 5): a stored '<TELESCOP> / <INSTRUME>' with a mount-like TELESCOP part left out."""
    parts = [p.strip() for p in str(text or "").split(" / ")]
    if len(parts) >= 2:   # '<TELESCOP> / <INSTRUME>': only the telescope part is judged
        tel = clean_telescope_name(parts[0])
        return " / ".join(p for p in [tel] + parts[1:] if p)
    return " / ".join(p for p in parts if p and not is_mount_name(p))


def scint_mag(obs, setup: dict | None) -> float:
    """2.2.2: scintillation for one point, in magnitudes, target plus the comp ensemble (each star twinkles on its
    own, so the comps add sigma^2 / n_comps). NaN when switched off or the airmass or exposure is unknown."""
    if not setup:
        return float("nan")
    am = getattr(obs, "airmass", None)
    if am is None or not math.isfinite(am):
        return float("nan")
    sig = scintillation_sigma(setup.get("aperture_m"), am, getattr(obs, "exptime", float("nan")),
                              setup.get("altitude_m") or 0.0)
    if not math.isfinite(sig):
        return float("nan")
    # 2.2.3: the comps actually averaged for this point when recorded; otherwise the series' count.
    n = int(getattr(obs, "n_comps", 0) or 0) or max(1, int(setup.get("n_comps") or 1))
    return 1.0857362 * sig * math.sqrt(1.0 + 1.0 / n)


def point_err(obs, setup: dict | None) -> float:
    """2.2.2: a point's magnitude error, with scintillation added in quadrature when it is switched on and known."""
    e = obs.merr
    extra = getattr(obs, "haze_err", 0.0) or 0.0   # 2.2.14 (item 18): kept transmission-loss points
    if extra > 0 and math.isfinite(e):
        e = math.hypot(e, extra)
    s = scint_mag(obs, setup)
    if math.isfinite(s):
        return math.hypot(e, s) if math.isfinite(e) else s
    return e


# 2.2.16 (item 2): the network fetchers as shipped; the catalog cache is used only while these are in place.
_REAL_FETCHERS = {name: globals()[name] for name in (
    "fetch_comparison_stars", "_http_get", "_vizier_query", "fetch_gaia_dr3", "fetch_gaia_dr3_vizier", "_gaia_esa",
    "fetch_apass9", "fetch_tycho2", "fetch_aavso_chart")}
