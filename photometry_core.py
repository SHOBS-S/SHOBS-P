"""Calibration, debayer, aperture photometry, period search, AAVSO export."""

from __future__ import annotations

import json
import re as _re
import math
import os
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


FITS_EXTS = (".fits", ".fit", ".fts", ".fits.gz", ".fit.gz")
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
    """A PixInsight XISF monolithic file: (2-D float32 image, header dict). The first channel of a color
    image is returned; Bayer (CFA) frames come back as the mosaic, like a FITS from the camera."""
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
        data = data.reshape(height, width, channels)[:, :, 0]
    else:
        data = data.reshape(channels, height, width)[0]
    data = data.astype(np.float32)
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
    if data.ndim == 3 and data.shape[0] in (1, 3, 4):
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
_SENSOR = {"mono": False}


def set_mono(flag: bool) -> None:
    _SENSOR["mono"] = bool(flag)


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
MASTER_KINDS = ("bias", "dark", "flat")


def master_file_name(kind: str, binning: int, exptime: float | None = None) -> str:
    """SHOBS-P_master_flat_bin2.fits, SHOBS-P_master_dark_bin2_0.75s.fits, SHOBS-P_master_bias_bin2.fits."""
    if kind == "dark":
        return f"{MASTER_PREFIX}dark_bin{int(binning)}_{float(exptime or 0):g}s.fits"
    return f"{MASTER_PREFIX}{kind}_bin{int(binning)}.fits"


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
    path = os.path.join(os.path.dirname(paths[0]), master_file_name(kind, binning, exptime))
    if not os.path.isfile(path):
        return None, "none saved yet"
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
                      binning: int) -> tuple[list[str], list[str]]:
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
            if fr is not None and lr is not None and rotator_difference(float(fr), float(lr)) > 2.0:
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
        folder = os.path.dirname(paths[0])
        path = os.path.join(folder, master_file_name(kind, binning, exptime))
        try:
            write_master(path, data, _master_cards(kind, binning, paths, headers, version, extra))
            masters.headers[kind] = read_header(path)
            log(f"Saved master {kind} in the {kind} folder: {os.path.basename(path)} (reused next time while these "
                "frames are unchanged)")
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
# I, U, and Sloan filters are not mapped: the comp catalogs here carry only B, V, and R magnitudes.


def filter_from_header(header: dict):
    """(AAVSO filter code, comp band) from FITS FILTER / FILTNAME for a monochrome camera, or None if unknown."""
    for key in ("FILTER", "FILTNAME", "FILTER1", "FILT"):
        value = header.get(key)
        if value is None or str(value).strip() == "":
            continue
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


def recenter_info(img: np.ndarray, x: float, y: float, radius: float, fwhm: float | None = None) -> dict:
    """2.2.8: centre on the star nearest (x, y), not on whatever is brightest nearby.

    1. Look for significant peaks (3x3-smoothed, above sky + 4 sigma of the smoothed noise) within a search radius
       (max(3, 1.2 x aperture radius), or 2.5 x FWHM when the FWHM is known).
    2. Take the peak NEAREST the start point, so a brighter neighbour a few pixels away cannot capture it
       (CoRoT-1, 10 Oct 2026: a star 2.5x brighter 5.8 px away pulled the old centre-of-mass 3.5 px off the target).
    3. Centre of mass of the sky-subtracted light inside about one FWHM of that peak, iterated three times.
    With no significant peak (a faint star lost in the noise), the start point is kept and found = False.
    Returns {"x", "y", "shift", "found"}."""
    from scipy import ndimage

    if fwhm is not None and math.isfinite(fwhm) and fwhm > 0:
        search = max(3.0, 2.5 * float(fwhm))
        window = max(1.5, float(fwhm))
    else:
        search = max(3.0, 1.2 * float(radius))
        window = min(max(1.5, 0.45 * float(radius)), 5.0)
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
    yy, xx = np.mgrid[0 : data.shape[0], 0 : data.shape[1]]
    all_py, all_px = np.nonzero(peaks)
    peaks &= np.hypot(xx - sx, yy - sy) <= search
    py, px = np.nonzero(peaks)
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
        # top's own connected patch counts, so a brighter neighbour whose half-maximum region touches this star's
        # is never taken for part of it (review, 2.2.8).
        raw_peak = float(data[py0, px0])
        tol = 1e-6 * max(1.0, abs(raw_peak))
        # Pixels EQUAL to the peak (a clipped, saturated top), not merely as bright: a brighter neighbour's core is
        # brighter than this star's peak and must not join it.
        tl, _m = ndimage.label(np.abs(data - raw_peak) <= tol)
        if tl[py0, px0]:
            top = tl == tl[py0, px0]
            if np.count_nonzero(top) > 1:
                peak_x, peak_y = float(xx[top].mean()), float(yy[top].mean())
                ux, uy = peak_x, peak_y
    except Exception:
        top = None
    # A star blended into a brighter neighbour's wing (closer than about 1.5 FWHM) has no peak of its own, so the
    # nearest peak can be the neighbour. When the click holds clearly more light than that peak's own profile
    # would put there, the click is on another star: keep it rather than jump to the neighbour.
    try:
        d_click = math.hypot(peak_x - sx, peak_y - sy)
        if d_click > 1.5:
            p_val = float(smooth[int(round(peak_y)), int(round(peak_x))] - sky)
            r_half = math.sqrt(max(area, 1) / math.pi)
            sig = math.sqrt((r_half / 1.1774) ** 2 + 0.67)
            expect = p_val * math.exp(-d_click ** 2 / (2.0 * sig * sig))
            here = float(smooth[int(round(sy)), int(round(sx))] - sky)
            if here > 3.0 * expect + 8.0 * noise / 3.0 and here < 0.5 * p_val:
                return {"x": float(x), "y": float(y), "shift": 0.0, "found": True, "blended": True}
    except Exception:
        pass
    # Other stars: peaks outside this star's half-maximum region, and much brighter peaks inside it (a neighbour
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
    # Pixels nearer another star's peak than this one's never count for this star's centre.
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
    # A centre that wandered far from its own peak was pulled by a neighbour's wings: use the peak's 3x3 centroid.
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
    return {"x": x_new, "y": y_new, "shift": float(math.hypot(x_new - x, y_new - y)), "found": True}


def recenter(img: np.ndarray, x: float, y: float, radius: float, fwhm: float | None = None) -> tuple[float, float, float]:
    """Centre on the nearest star (see recenter_info); returns (x, y, shift). Keeps (x, y) when no star is found."""
    r = recenter_info(img, x, y, radius, fwhm)
    return r["x"], r["y"], r["shift"]


def star_fwhm(img: np.ndarray, x: float, y: float) -> dict:
    """2.2.8: FWHM of one star from a circular 2-D Gaussian fit (amplitude, centre, sigma, sky) to a box that grows
    with the star. Replaces moments in a fixed 12 px radius, which on a faint or crowded star measured the noise and
    the neighbours (CoRoT-1 MicroObservatory frames: real stars 2.7-3.3 px, old method 10.5 px).
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
            continue  # a neighbour close enough to disturb the fit
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
) -> dict | None:
    """Find how the star field moved between the reference frame and this one.

    Step 1: every (reference star, frame star) pair votes for an offset; coarse 8 px bins
    tolerate a small rotation. Also tried with a 180-degree turn (a German-mount meridian flip).
    Step 2: take the stars that agree and fit shift plus rotation by least squares,
    tightening the match radius each pass. Slow field rotation from polar misalignment
    is absorbed here.
    Returns {"dx", "dy", "angle", "flipped", "votes", "of"} or None.
    """
    if len(ref_sources) < 3 or len(sources) < 3:
        return None
    ref = np.asarray(ref_sources, dtype=float)
    cur = np.asarray(sources, dtype=float)
    h, w = shape
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    best = None
    for flipped in (False, True):
        base = np.column_stack((w - 1 - ref[:, 0], h - 1 - ref[:, 1])) if flipped else ref
        diff = (cur[None, :, :] - base[:, None, :]).reshape(-1, 2)
        keys = np.round(diff / bin_px).astype(np.int64)
        uniq, counts = np.unique(keys, axis=0, return_counts=True)
        for key in uniq[np.argsort(counts)[::-1][:6]]:
            guess = key.astype(float) * bin_px
            near = diff[np.hypot(diff[:, 0] - guess[0], diff[:, 1] - guess[1]) < bin_px * 1.5]
            if near.size:
                guess = np.median(near, axis=0)
            # Model: cur = R(angle) (base - centre) + centre + shift
            angle, shift = 0.0, guess
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
    if best is None or best["votes"] < needed or abs(best["angle"]) > 10:
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
) -> Obs:
    """One calibrated, debayered frame -> one differential magnitude.

    With more than one comparison star this is a simple ensemble: the mean instrumental
    magnitude of the comps against the mean of their catalog magnitudes.
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
    jump_limit = max(3.0, 0.5 * radius) if aligned else float("inf")   # unaligned frames follow drift by centring
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
        comp_mag_var.append(_mag_variance(measured["flux"], measured["variance"]))
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
    merr = math.sqrt(_mag_variance(var["flux"], var["variance"]) + c_var + 0.005 ** 2)

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
        target_var=float(_mag_variance(var["flux"], var["variance"])),
        comp_vars=[float(v) for v in comp_mag_var],
        n_comps=len(comps),
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
        for key in ("period_days", "var_type", "auid"):
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
    }


def _apply_pm(ra_deg: float, dec_deg: float, pm_ra_cosdec: float, pm_dec: float) -> tuple[float, float]:
    """Move J2000 coordinates to now. Proper motions in mas/yr, RA component already times cos(dec)."""
    years = Time.now().decimalyear - 2000.0
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
    name = str(name or "").strip().upper()
    return {"V": "V", "B": "B", "R": "R", "RC": "R", "I": "I", "IC": "I"}.get(name)


def chart_stars(payload: dict, band: str = "V") -> list[dict]:
    """Comparison stars from an AAVSO VSP payload. 'mag' is in the requested band;
    'mags' keeps every band the chart lists (V, B, R from Rc, I from Ic)."""
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
        if band not in mags:
            continue
        ra = _angle(item["ra"], hours=True)
        dec = _angle(item["dec"], hours=False)
        stars.append({
            "auid": item.get("auid", ""),
            "label": str(item.get("label", "")),
            "ra": ra,
            "dec": dec,
            "mag": mags[band],
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
            # The exclusion radius grows with how much brighter the neighbour is.
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
    return {"V": "V", "R": "R", "B": "B"}.get(band.upper(), "V")


def _pm_to_now(ra_deg: float, dec_deg: float, pm_ra, pm_dec, epoch: float) -> tuple[float, float]:
    try:
        pm_ra = float(pm_ra)
        pm_dec = float(pm_dec)
    except (TypeError, ValueError):
        return ra_deg, dec_deg
    if not (math.isfinite(pm_ra) and math.isfinite(pm_dec)):
        return ra_deg, dec_deg
    years = Time.now().decimalyear - epoch
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


def _vizier_query(source: str, ra_deg: float, dec_deg: float, radius_deg: float, columns: list[str], sort: str, max_rows: int) -> list[dict]:
    import urllib.parse

    params = {
        "-source": source,
        "-c": f"{ra_deg:.6f} {dec_deg:+.6f}",
        "-c.rd": f"{radius_deg:.4f}",
        "-out": ",".join(columns),
        "-out.max": str(max_rows),
        "-sort": sort,
    }
    query = urllib.parse.urlencode(params)
    errors = []
    for host in ("https://vizier.cds.unistra.fr", "https://vizier.cfa.harvard.edu"):
        try:
            text = _http_get(f"{host}/viz-bin/asu-tsv?{query}", timeout=60).decode("utf-8", errors="replace")
            return parse_vizier_tsv(text)
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
        ra, dec = _pm_to_now(ra, dec, row.get("pmRA"), row.get("pmDE"), 2000.0)
        mags = {}
        if bt is not None:
            v = vt - 0.090 * (bt - vt)
            mags = {"V": v, "B": v + 0.850 * (bt - vt)}
        mag = mags.get(band)
        name = f"TYC {row.get('TYC1', '')}-{row.get('TYC2', '')}-{row.get('TYC3', '')}"
        stars.append({"auid": name, "label": f"{mag:.1f}" if mag is not None else "", "ra": ra, "dec": dec,
                      "mag": mag, "mags": mags, "band": band, "catalog": "Tycho-2", "bright": vt,
                      "service": "VizieR I/259"})
    return stars


def fetch_apass9(ra_deg: float, dec_deg: float, radius_deg: float, band: str = "V") -> list[dict]:
    """APASS DR9 (VizieR II/336/apass9). Johnson B and V, about V 10 to 17. No R band."""
    rows = _vizier_query(
        "II/336/apass9", ra_deg, dec_deg, radius_deg,
        ["RAJ2000", "DEJ2000", "Vmag", "e_Vmag", "Bmag"], "Vmag", 3000,
    )
    band = _band_for(band)
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
        mag = mags.get(band)
        stars.append({"auid": _position_name("APASS", ra, dec), "label": f"{mag:.1f}" if mag is not None else "",
                      "ra": ra, "dec": dec, "mag": mag, "mags": mags, "band": band, "catalog": "APASS DR9", "bright": v,
                      "service": "VizieR II/336"})
    return stars


def fetch_gaia_dr3(ra_deg: float, dec_deg: float, radius_deg: float, band: str = "V", glimit: float = 17.0) -> list[dict]:
    """Gaia DR3 from the ESA archive, with Johnson-Kron-Cousins synthetic photometry (GSPC) where it exists.
    Positions are epoch 2016.0 and are moved to now with Gaia proper motions."""
    import csv
    import io
    import urllib.parse

    band = _band_for(band)
    adql = (
        "SELECT TOP 3000 g.source_id, g.ra, g.dec, g.pmra, g.pmdec, g.phot_g_mean_mag, "
        "s.v_jkc_mag, s.b_jkc_mag, s.r_jkc_mag "
        "FROM gaiadr3.gaia_source AS g "
        "LEFT OUTER JOIN gaiadr3.synthetic_photometry_gspc AS s ON s.source_id = g.source_id "
        f"WHERE 1 = CONTAINS(POINT('ICRS', g.ra, g.dec), CIRCLE('ICRS', {ra_deg:.6f}, {dec_deg:.6f}, {radius_deg:.4f})) "
        f"AND g.phot_g_mean_mag < {glimit:.1f} "
        "ORDER BY g.phot_g_mean_mag"
    )
    query = urllib.parse.urlencode({"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": adql})
    try:
        text = _http_get("https://gea.esac.esa.int/tap-server/tap/sync?" + query, timeout=180).decode("utf-8", errors="replace")
    except Exception as exc:
        # ESA archive slow or down: same stars from the VizieR copy of Gaia DR3.
        try:
            return fetch_gaia_dr3_vizier(ra_deg, dec_deg, radius_deg, band, glimit)
        except Exception as exc2:
            raise ConnectionError(f"Gaia archive: {exc}\nVizieR Gaia DR3: {exc2}") from exc2
    column = {"V": "v_jkc_mag", "B": "b_jkc_mag", "R": "r_jkc_mag"}[band]
    stars = []
    for row in csv.DictReader(io.StringIO(text)):
        ra = _num(row.get("ra"))
        dec = _num(row.get("dec"))
        g = _num(row.get("phot_g_mean_mag"))
        if ra is None or dec is None or g is None:
            continue
        ra, dec = _pm_to_now(ra, dec, row.get("pmra"), row.get("pmdec"), 2016.0)
        mags = {k: _num(row.get(c)) for k, c in (("V", "v_jkc_mag"), ("B", "b_jkc_mag"), ("R", "r_jkc_mag"))}
        mags = {k: v for k, v in mags.items() if v is not None}
        mag = _num(row.get(column))
        stars.append({"auid": f"Gaia DR3 {row.get('source_id', '')}", "label": f"{mag:.1f}" if mag is not None else "",
                      "ra": ra, "dec": dec, "mag": mag, "mags": mags, "band": band, "catalog": "Gaia DR3", "bright": g,
                      "service": "ESA Gaia archive"})
    if not stars and "error" in text.lower():
        raise ValueError("Gaia archive: " + text.strip()[:300])
    return stars


def fetch_gaia_dr3_vizier(ra_deg: float, dec_deg: float, radius_deg: float, band: str = "V", glimit: float = 17.0) -> list[dict]:
    """Gaia DR3 from VizieR (I/355/gaiadr3). V comes from G and BP-RP with the Gaia DR3
    photometric relation (Riello et al. 2021): G - V = -0.02704 + 0.01424 C - 0.2156 C^2 + 0.01426 C^3,
    good to about 0.03 mag. Only V is given this way; B and R are left empty."""
    rows = _vizier_query(
        "I/355/gaiadr3", ra_deg, dec_deg, radius_deg,
        ["Source", "RA_ICRS", "DE_ICRS", "pmRA", "pmDE", "Gmag", "BP-RP"], "Gmag", 3000,
    )
    band = _band_for(band)
    stars = []
    for row in rows:
        ra = _num(row.get("RA_ICRS"))
        dec = _num(row.get("DE_ICRS"))
        g = _num(row.get("Gmag"))
        if ra is None or dec is None or g is None or g > glimit:
            continue
        ra, dec = _pm_to_now(ra, dec, row.get("pmRA"), row.get("pmDE"), 2016.0)
        c = _num(row.get("BP-RP"))
        mag = None
        if c is not None and -0.5 < c < 5.0:
            v_from_g = g + 0.02704 - 0.01424 * c + 0.2156 * c ** 2 - 0.01426 * c ** 3
            mag = v_from_g if band == "V" else None
        stars.append({"auid": f"Gaia DR3 {row.get('Source', '')}", "label": f"{mag:.1f}" if mag is not None else "",
                      "ra": ra, "dec": dec, "mag": mag, "mags": ({"V": v_from_g} if c is not None and -0.5 < c < 5.0 else {}),
                      "band": band, "catalog": "Gaia DR3", "bright": g,
                      "service": "VizieR I/355 (ESA archive unavailable; V from G only)"})
    return stars


def fetch_comparison_stars(catalog: str, ra_hours: float, dec_deg: float, fov_arcmin: float, band: str = "V") -> tuple[list[dict], str]:
    """Stars for chart matching and click-to-fill. Returns (stars, chart_id). chart_id is '' for non-AAVSO catalogs."""
    ra_deg = ra_hours * 15.0
    radius_deg = fov_arcmin / 60.0 / 2.0 * 1.15
    if catalog == "Gaia DR3":
        return fetch_gaia_dr3(ra_deg, dec_deg, radius_deg, band), ""
    if catalog == "APASS DR9":
        return fetch_apass9(ra_deg, dec_deg, radius_deg, band), ""
    if catalog == "Tycho-2":
        return fetch_tycho2(ra_deg, dec_deg, radius_deg, band), ""
    payload = fetch_aavso_chart(ra_hours, dec_deg, fov_arcmin, 16)
    stars = chart_stars(payload, band)
    for star in stars:
        star["catalog"] = "AAVSO"
        star["bright"] = star["mag"]
        star["service"] = "AAVSO VSP"
    return stars, str(payload.get("chartid") or "")


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
    """Triangles from each point and pairs of its k nearest neighbours: vertex indices ordered so that vertex 0 is
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


def solve_field(sources: list, stars: list[dict], ra_deg: float, dec_deg: float, scale_arcsec: float,
                tolerance_px: float = 3.0, scale_range: float = 0.3, n_src: int = 30, n_cat: int = 45) -> dict | None:
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
    return {"hits": int(hit.sum()), "angle": par["angle"], "mirrored": mirrored, "scale_factor": par["scale_factor"],
            "placed": placed, "target_xy": (par["tx"], par["ty"]), "score": (int(hit.sum()), 0.0),
            "match_count": len(cat)}


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
    return math.sqrt(obs.target_var + total / len(selection) ** 2 + 0.005 ** 2)


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


def catalog_bv(star: dict) -> float | None:
    mags = star.get("mags") or {}
    if mags.get("B") is not None and mags.get("V") is not None:
        return float(mags["B"]) - float(mags["V"])
    return None


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
                          "desc": f"VSX {v.get('type', '') or 'variable'}{per}", "ra": v["ra"], "dec": v["dec"]})
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
    for star in stars:
        if star.get("ra") is None or star.get("dec") is None:
            continue
        hits = [v for v in variables if sky_separation_arcsec(star["ra"], star["dec"], v["ra"], v["dec"]) <= tol_arcsec]
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
    """Aperture radius 1.8 x FWHM, sky ring starting at the larger of r + 3 and 3.2 x FWHM, and a sky ring
    with four times the aperture's area."""
    r = max(3.0, round(1.8 * fwhm, 1))
    sky_in = float(math.ceil(max(r + 3.0, 3.2 * fwhm)))
    sky_out = float(math.ceil(math.sqrt(sky_in ** 2 + 4.0 * r ** 2)))
    return {"radius": r, "sky_in": sky_in, "sky_out": sky_out}


def robust_sigma(values) -> float:
    v = np.asarray([x for x in values if x is not None and math.isfinite(x)], dtype=float)
    if v.size < 5:
        return float("nan")
    return float(1.4826 * np.median(np.abs(v - np.median(v))))


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

    for x, y, star in placed:
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
        crowded = False
        for x2, y2, other in placed:
            if other is star or math.hypot(x2 - x, y2 - y) >= sky_outer + radius:
                continue
            m2 = other.get("mag")
            if m2 is None or m2 < mag + 3.0:
                crowded = True
                break
        if crowded:
            turned_away("crowded (a neighbour in the sky ring)")
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


def segment_words(observations: list[Obs]) -> list[str]:
    """2.2.2: the segment column in words: "before flip" / "after flip" on a night with a meridian flip,
    "no flip" on a night without one (the stored value stays "" or "flip")."""
    flipped = {o.night for o in observations if o.segment == "flip"}
    return ["after flip" if o.segment == "flip" else ("before flip" if o.night in flipped else "no flip")
            for o in observations]


def write_curve_csv(path: str, observations: list[Obs], notes: dict | None = None, scint: dict | None = None) -> None:
    corrected = apply_night_zero_point(observations) if observations else []
    with AsciiWriter(path) as fh:
        fh.write(
            "jd,mag,merr,filter,zero_pointed_mag,comp_inst_mag,check_inst_mag,check_reduced_mag,"
            "airmass,exptime,fwhm_px,peak,raw_peak,sky,flag,night,segment,segment_normalized_mag,"
            "b_minus_v,v_minus_r,b_minus_v_diff,v_minus_r_diff,color_calibration,"
            "target_x,target_y,comp1_x,comp1_y,comp2_x,comp2_y,check_x,check_y,night_note,file,scint_mag,err_total\n"
        )
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

def apply_cloud_flags(observations: list[Obs], limit: float = 0.20) -> int:
    """Flag frames whose comparison-star light is more than `limit` (a fraction) below that
    night's median. Dew, cloud, haze, and twilight dim every star; the bright target and the
    fainter comps do not dim identically, so those frames drift. Re-applying replaces old
    'cloud' flags. Returns how many frames are flagged."""
    flagged = 0
    by_night: dict[str, list[Obs]] = {}
    for obs in observations:
        by_night.setdefault(obs.night, []).append(obs)
    for night_obs in by_night.values():
        rates = [o.flux_comp / o.exptime for o in night_obs if o.flux_comp > 0 and o.exptime > 0]
        # Series written before v1.3 stored a constant placeholder here; leave those alone.
        usable = len(rates) >= 5 and len({round(r, 6) for r in rates}) > 1
        median = float(np.median(rates)) if usable else 0.0
        for o in night_obs:
            words = [w for w in o.flag.split() if w != "cloud"]
            if usable and limit > 0 and o.exptime > 0 and o.flux_comp / o.exptime < (1.0 - limit) * median:
                words.append("cloud")
                flagged += 1
            o.flag = " ".join(words)
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
    """Mean catalog color (first - second) of the comps that have both bands, or None."""
    vals = [c[first] - c[second] for c in comps_cat if c and first in c and second in c]
    return float(np.mean(vals)) if vals else None


# ---- Field scan (discovery mode) -----------------------------------------------------

def measure_many(img: np.ndarray, xy: np.ndarray, radius: float, sky_inner: float, sky_outer: float) -> tuple[np.ndarray, np.ndarray]:
    """Aperture photometry of many stars at once (photutils). Returns (net flux, sky median)."""
    xy = np.asarray(xy, dtype=float)
    bad = ~np.isfinite(img)
    mask = bad if bad.any() else None
    apertures = CircularAperture(xy, r=radius)
    annuli = CircularAnnulus(xy, r_in=sky_inner, r_out=sky_outer)
    sky = np.atleast_1d(np.asarray(ApertureStats(img, annuli, mask=mask, sigma_clip=SigmaClip(sigma=3.0, maxiters=10)).median, dtype=float))
    sums = np.asarray(aperture_photometry(img, apertures, mask=mask)["aperture_sum"], dtype=float)
    return sums - sky * math.pi * radius ** 2, sky


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


def field_variability(inst: np.ndarray, min_fraction: float = 0.7) -> dict:
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
    # Expected scatter: running median over neighbours in brightness.
    expected = np.full(n_stars, np.nan)
    idx = np.where(usable & np.isfinite(scatter))[0]
    if idx.size:
        srt = idx[np.argsort(med[idx])]
        k = max(5, srt.size // 8)
        for pos, star in enumerate(srt):
            lo, hi = max(0, pos - k), min(srt.size, pos + k + 1)
            expected[star] = np.median(scatter[srt[lo:hi]])
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
    for x, y, star in placed:
        mags = star.get("mags") or {}
        if "B" not in mags or "V" not in mags:
            continue
        if star.get("variable"):
            continue
        if not (edge <= x < w - edge and edge <= y < h - edge):
            continue
        if target_xy is not None and math.hypot(x - target_xy[0], y - target_xy[1]) < 2 * radius:
            continue
        v = mags["V"]
        crowded = False
        for x2, y2, other in placed:
            if other is star or math.hypot(x2 - x, y2 - y) >= 1.5 * radius:
                continue
            m2 = (other.get("mags") or {}).get("V", other.get("mag"))
            if m2 is None or m2 < v + 3.0:
                crowded = True
                break
        if not crowded:
            out.append((x, y, star))
    out.sort(key=lambda item: item[2]["mags"]["V"])
    return out[:max_stars]


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
COLOR_FIT_MAX_STAR_ERR = 0.08  # mag; stars whose own colour is noisier than this are left out
COLOR_FIT_FLOOR = 0.03         # mag; catalogue colour errors and transformation mismatch


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

    Each star is weighted by 1 / (its colour error), where the error combines its own
    measured noise (inst_err, from frame-to-frame scatter) scaled by the slope, and a floor
    for catalogue errors. Faint, noisy stars therefore count little, and stars noisier than
    COLOR_FIT_MAX_STAR_ERR are left out. Outliers (variables, blends, bad catalogue colours)
    beyond clip x the normalised scatter are rejected. None if too few stars survive.

    Returns k, a, scatter (weighted rms about the line), k_err, n, n_rejected, range, and
    'problem' when the fit should not be trusted (narrow colour range, slope far from 1, a
    poorly determined slope, or scatter above max_scatter).
    """
    x = np.asarray(inst, dtype=float)
    y = np.asarray(catalog, dtype=float)
    err = np.zeros_like(x) if inst_err is None else np.asarray(inst_err, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y) & np.isfinite(err)
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
        k, a = np.polyfit(x[use], y[use], 1, w=1.0 / sig[use])
        sig = np.sqrt((k * err) ** 2 + floor ** 2)
        norm = (y - (a + k * x)) / sig
        spread = 1.4826 * float(np.median(np.abs(norm[use] - np.median(norm[use]))))
        new = ok & (np.abs(norm) < clip * max(spread, 1.0))
        if np.array_equal(new, use):
            break
        use = new
    if use.sum() < min_stars:
        return None
    k, a = np.polyfit(x[use], y[use], 1, w=1.0 / sig[use])
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
        if fit and not fit.get("problem") and offset is not None and math.isfinite(offset):
            method = "field"
        elif old and not old.get("problem") and cat_colors.get(key) is not None:
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
    calibrated = fit["a"] + fit["k"] * inst if fit and not fit["problem"] else None
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
    if i in set(result.get("near_sat") or []):
        out.append("near saturation: check linearity")
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


def write_scan_candidates(path: str, result: dict) -> None:
    """One row per star that stands out, ready to start a VSX new-variable submission."""
    with AsciiWriter(path) as fh:
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
                num(result["excess"][i], "{:.2f}"), num(per.get("period")), num(per.get("power"), "{:.3f}"),
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
                "yes" if i in sat else "", "yes" if result["usable"][i] else "no", num(per.get("period")),
                (vsx or {}).get("name", "").replace(",", " "), (vsx or {}).get("type", "").replace(",", " "),
                str(int(result["n_good"][i])), "; ".join(scan_cautions(result, i)).replace(",", ";"),
            ]) + "\n")


def write_scan_lightcurves(path: str, result: dict) -> None:
    """Every star's zero-pointed light curve, long format (one row per star per frame)."""
    corrected = result["light_curves"]
    with AsciiWriter(path) as fh:
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
        ("APASS (AAVSO)", "Comparison stars, Johnson B and V, field stars for B-V calibration.",
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
         "the same report format. No EXOTIC code is used.",
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
        ("Bootstrap resampling", "Uncertainties and the 'clear winner' test in the aperture test (frames resampled "
         "with replacement, the same frames for every aperture).",
         "Efron, B. 1979, Annals of Statistics 7, 1.", ""),
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


def split_lights(paths: list[str]) -> tuple[list[str], list[tuple[str, str]], str]:
    """(lights to use, [(skipped path, reason)], note for the log).

    Master frames are always skipped. Individual bias/dark/flat files mixed in with lights are
    skipped too. But when most of the folder is labelled bias/dark/flat, the night's lights were
    almost certainly saved with the wrong image type by the capture software, so they are all
    kept and the note says so.
    """
    keep, skipped, labelled = [], [], []
    for path in paths:
        reason = is_calibration_frame(path)
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
        note = (f"{len(labelled)} of {candidates} files in the Lights folder are labelled as calibration frames "
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


# ---- Comparison ensemble health -------------------------------------------------------

def comp_health(observations: list[Obs], names: list[str], check_name: str = "",
                spare_names: list[str] | None = None, aliases: dict | None = None) -> dict | None:
    """Measure each comp, spare comp, and the check star against the others, frame by frame.

    For every star, its instrumental magnitude minus the mean of the other stars in the same
    frame removes clouds and airmass. A steady star gives a flat line; a star that drifts
    from night to night, or scatters more than the rest, is the one to drop. The reference
    for each star uses only the stars present in most frames, so a spare that exists on a
    few nights cannot shift the others.
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
                        # Scatter with the step taken out, to judge whether the step is real.
                        flat = np.concatenate([resid[pre, j] - m_pre, resid[post, j] - m_post])
                        seg_scatter.append(1.4826 * float(np.median(np.abs(flat))))
            meds = np.array(list(nightly[lab].values()))
            spread = float(np.max(np.abs(meds - np.median(meds)))) if meds.size >= 2 else float("nan")
            summary.append({"label": lab, "kind": kinds[j], "night_spread": spread,
                            "scatter": float(np.median(within)) if within else float("nan"),
                            "flip_step": max(steps, key=abs) if steps else float("nan"),
                            "seg_scatter": float(np.median(seg_scatter)) if seg_scatter else float("nan"),
                            "nights": len(meds)})
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
    """RA, Dec (degrees) at the centre of a frame of shape (rows, cols) as saved."""
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
                       ("instrument", "telescope / camera"), ("observer", "observer")):
        mine = new.get(key)
        if mine in (None, "", []):
            continue
        theirs = collect(key)
        mine_t = _setup_text(key, mine)
        different = {t: n for t, n in theirs.items() if t != mine_t}
        if different:
            parts = "; ".join(f"{t} on {', '.join(n[:3])}{' …' if len(n) > 3 else ''}" for t, n in different.items())
            out.append(f"{label}: {mine_t} on this night, {parts}")
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
    return str(value)


def typical_rotator(angles: list[float]) -> float | None:
    """2.2.3: the most common camera position among rotator readings (180° apart counts as the same position),
    returned as one of the readings. None for an empty list."""
    if not angles:
        return None
    best, best_n = None, -1
    for a in angles:
        n = sum(1 for b in angles if rotator_difference(a, b) <= 2.0)
        if n > best_n:
            best, best_n = a, n
    return best


def rotator_spread(angles: list[float]) -> float | None:
    """2.2.3: the largest camera-position difference among rotator readings (flip-aware), or None."""
    if len(angles) < 2:
        return None if not angles else 0.0
    ref = typical_rotator(angles)
    return max(rotator_difference(ref, a) for a in angles)


def rotator_summary(angles: list[float]) -> str:
    """2.2.3: e.g. '30° (40 files), 2° (10 files)', grouping flip-equivalent readings."""
    groups: list[list[float]] = []
    for a in angles:
        for g in groups:
            if rotator_difference(g[0], a) <= 2.0:
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
    s = scint_mag(obs, setup)
    if math.isfinite(s):
        return math.hypot(e, s) if math.isfinite(e) else s
    return e
