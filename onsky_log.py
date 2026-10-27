"""SHOBS-P's on-sky log for SHOBS-FLUX (2.2.19.1; format approved by John 27 Oct 2026).

Each time a reduction finishes, SHOBS-P writes ONE small JSON file into the SHOBS data folder's onsky/ folder
(~/.shobs/onsky/, see shobs_profile.py), named <night>__<target>__<run id>.json. SHOBS-FLUX reads it later (its Sky
page) to learn the system's on-sky numbers (zero point, extinction, seeing by focus, sky, scintillation, cadence)
from ordinary reductions, with no extra imaging. Nothing in SHOBS-P reads it back, and nothing on screen changes.

What is in it: the setup key (camera, gain, offset, binning, focal length, band, debayer mode), the aperture, the
stars (name, role, catalog magnitude) and per frame: file, mid-exposure JD, airmass, exposure, focuser position, pier
side, sensor temperature, FWHM, sky, and for every measured star its flux (SHOBS-P image units in the aperture),
instrumental magnitude, brightest raw pixel (ADU) and position. Pre-calibrated frames are marked so FLUX skips them.
The site is never written (each app keeps its own).

Writing happens in a background thread after the night's points are made; any problem is logged and never stops or
slows the reduction. Set SHOBS_ONSKY_OFF=1 to turn it off."""
from __future__ import annotations

import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import shobs_profile as sp

# the shobs_profile.py this SHOBS-P was built against (its self-test checks the copy matches)
NEEDS_PROFILE_VERSION = "1.0.0"
FORMAT_VERSION = 1


def _f(v, nd=None):
    """A finite float (rounded) or None."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return round(x, nd) if nd is not None else x


def _header_extras(path: str) -> dict:
    """FOCUSPOS, PIERSIDE, CCD-TEMP and DATE-OBS from a frame's primary header, read card by card (a few kB per
    file, no pixels). {} for XISF or anything unreadable."""
    if not path or not path.lower().endswith((".fit", ".fits", ".fts")):
        return {}
    want = {"FOCUSPOS": None, "FOCPOS": None, "PIERSIDE": None, "CCD-TEMP": None, "DATE-OBS": None}
    try:
        with open(path, "rb") as fh:
            ended = False
            for _block in range(40):
                block = fh.read(2880)
                if not block or ended:
                    break
                for i in range(0, len(block) - 79, 80):
                    card = block[i:i + 80].decode("ascii", "replace")
                    key = card[:8].strip().upper()
                    if key == "END":
                        ended = True
                        break
                    if key in want and want[key] is None and card[8:10] == "= ":
                        raw = card[10:].strip()
                        if raw.startswith("'"):
                            want[key] = raw[1:].split("'", 1)[0].strip()
                        else:
                            v = raw.split("/", 1)[0].strip()
                            try:
                                want[key] = float(v)
                            except ValueError:
                                want[key] = v or None
    except OSError:
        return {}
    out = {"focuspos": want["FOCUSPOS"] if want["FOCUSPOS"] is not None else want["FOCPOS"],
           "pierside": want["PIERSIDE"], "ccd_temp_c": want["CCD-TEMP"]}
    return out


def night_of(jd: float) -> str:
    """The observing night's date: UTC date of the mid-exposure minus 12 hours (an evening's frames and the next
    morning's share one date anywhere west of about 100° E)."""
    t = datetime(2000, 1, 1, 12, tzinfo=timezone.utc) + timedelta(days=float(jd) - 2451545.0) - timedelta(hours=12)
    return t.date().isoformat()


def build(ctx: dict, points: list, app_version: str, header_reads: bool = True) -> dict:
    """The on-sky log document from the run's context (stars, aperture, setup) and its Obs points. Copies every
    value out of the points (plain numbers and strings), so SHOBS-P may change the Obs objects afterwards.
    header_reads=False leaves focuspos / pierside / ccd_temp_c empty (fill_headers adds them later, off the UI
    thread)."""
    hdr = ctx.get("header") or {}
    key = sp.setup_key(hdr)
    target = ctx.get("target")
    comps = list(ctx.get("comps") or [])
    spares = list(ctx.get("spares") or [])
    check = ctx.get("check")
    stars = []
    if target is not None:
        stars.append({"name": target.name, "role": "target", "catalog_mag": _f(target.catalog_mag, 4)})
    stars += [{"name": c.name, "role": "comp", "catalog_mag": _f(c.catalog_mag, 4)} for c in comps]
    if check is not None:
        stars.append({"name": check.name, "role": "check", "catalog_mag": _f(check.catalog_mag, 4)})
    stars += [{"name": s.name, "role": "spare", "catalog_mag": _f(s.catalog_mag, 4)} for s in spares]
    precal = bool(ctx.get("precalibrated"))
    frames_out = []
    for o in points:
        exp = _f(getattr(o, "exptime", None))
        pos = getattr(o, "pos", {}) or {}
        peaks = getattr(o, "raw_peaks", {}) or {}
        meas = {}

        def put(name, inst, xy):
            m = _f(inst)
            if m is None:
                return
            flux = exp * 10 ** (-0.4 * m) if exp and exp > 0 else None
            meas[name] = {"flux": _f(flux, 2), "inst_mag": round(m, 5), "peak_adu": _f(peaks.get(name), 1),
                          "x": _f(xy[0], 1) if xy else None, "y": _f(xy[1], 1) if xy else None}

        if target is not None:
            put(target.name, getattr(o, "target_inst", None), pos.get("t"))
        if check is not None:
            put(check.name, getattr(o, "kmag", None), pos.get("k"))     # Obs.kmag: the check's instrumental mag
        cpos = pos.get("c") or []
        for i, c in enumerate(comps):
            insts = getattr(o, "comp_insts", None) or []
            put(c.name, insts[i] if i < len(insts) else None, cpos[i] if i < len(cpos) else None)
        spos = pos.get("s") or {}
        for name, inst in list((getattr(o, "spare_insts", {}) or {}).items()):
            put(name, inst, spos.get(name))
        extra = _header_extras(getattr(o, "path", "")) if header_reads else {}
        frames_out.append({
            "_path": getattr(o, "path", "") or "",
            "file": os.path.basename(getattr(o, "path", "") or ""),
            "jd_mid": _f(getattr(o, "jd", None), 6),
            "airmass": _f(getattr(o, "airmass", None), 4),
            "exptime_s": exp,
            "focuspos": extra.get("focuspos"),
            "pierside": extra.get("pierside"),
            "ccd_temp_c": _f(extra.get("ccd_temp_c"), 2),
            "segment": getattr(o, "segment", "") or "",
            "fwhm_px": _f(getattr(o, "fwhm", None), 3),
            "sky": _f(getattr(o, "sky", None), 3),
            "flag": getattr(o, "flag", "") or "",
            "precalibrated": precal,
            "stars": meas,
        })
    jd0 = next((f["jd_mid"] for f in frames_out if f["jd_mid"]), None)
    pattern = ctx.get("pattern") or ""
    binning = int(ctx.get("binning") or 1)
    return {
        "format": sp.ONSKY_FORMAT,
        "format_version": FORMAT_VERSION,
        "run_id": ctx.get("run_id") or "",
        "written_by": f"SHOBS-P {app_version}",
        "written": sp.now_iso(),
        "night": night_of(jd0) if jd0 else None,
        "target": target.name if target is not None else None,
        "mode": ctx.get("mode_key"),
        "setup": {
            "instrume": key.get("instrume"),
            "gain_setting": key.get("gain_setting"),
            "offset": key.get("offset"),
            "camera_binning": key.get("binning"),
            "camera_id": key.get("camera_id"),
            "focal_header_mm": key.get("focal_mm"),
            "telescop_header": key.get("telescop_header"),
            "pixel_um": key.get("pixel_um"),
            "e_per_adu_header": key.get("e_per_adu"),
            "bayer_pattern": pattern if pattern and pattern != "MONO" else None,
        },
        "band": ctx.get("filt"),
        "catalog_band": ctx.get("band"),
        "measurement": {
            "debayer_mode": ctx.get("debayer") if pattern and pattern != "MONO" else None,
            "software_binning": binning,
            "raw_px_per_px": binning * (1 if (not pattern or pattern == "MONO") else 2),
            "aperture_radius_px": _f(ctx.get("radius"), 3),
            "sky_inner_px": _f(ctx.get("sky_in"), 3),
            "sky_outer_px": _f(ctx.get("sky_out"), 3),
            "flux_units": "SHOBS-P image units summed in the aperture, sky subtracted (calibrated frames)",
        },
        "precalibrated": precal,
        "stars": stars,
        "frames": frames_out,
    }


def fill_headers(doc: dict) -> dict:
    """Read each frame's FOCUSPOS / PIERSIDE / CCD-TEMP (file I/O: the background part) and drop the paths."""
    for f in doc.get("frames") or []:
        path = f.pop("_path", "")
        if f.get("focuspos") is None and f.get("pierside") is None:
            extra = _header_extras(path)
            f["focuspos"] = extra.get("focuspos")
            f["pierside"] = extra.get("pierside")
            f["ccd_temp_c"] = _f(extra.get("ccd_temp_c"), 2)
    return doc


def file_name(doc: dict) -> str:
    return (f"{doc.get('night') or 'night-unknown'}__{sp.slug(doc.get('target') or 'target')}__"
            f"{doc.get('run_id') or 'run'}.json")


def new_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S") + "-" + os.urandom(2).hex()


def write(ctx: dict, points: list, app_version: str) -> str:
    """Build and write the log; returns its path (raises on a problem: write_async catches)."""
    return write_doc(fill_headers(build(ctx, points, app_version, header_reads=False)))


def write_doc(doc: dict) -> str:
    path = os.path.join(sp.onsky_dir(), file_name(doc))
    sp.write_json(path, doc)
    return path


def write_async(ctx: dict, points: list, app_version: str, log=None) -> threading.Thread | None:
    """write() in a background thread. log(text) is called with one line when done or on a problem (from that
    thread: pass a thread-safe logger). Never raises; None when switched off or nothing to write."""
    if os.environ.get("SHOBS_ONSKY_OFF") or not points or not ctx:
        return None
    try:   # the values are copied out of the points HERE, on the caller's (UI) thread: SHOBS-P goes on to change them
        doc = build(ctx, list(points), app_version, header_reads=False)
    except Exception as exc:
        if log:
            log(f"On-sky log for SHOBS-FLUX not written ({type(exc).__name__}: {exc}); the reduction is not affected.")
        return None
    n = len(doc.get("frames") or [])

    def run():
        try:
            path = write_doc(fill_headers(doc))
            if log:
                log(f"On-sky log for SHOBS-FLUX written: {os.path.basename(path)} ({n} frames).")
        except Exception as exc:
            if log:
                log(f"On-sky log for SHOBS-FLUX not written ({type(exc).__name__}: {exc}); the reduction is "
                    "not affected.")

    try:
        t = threading.Thread(target=run, daemon=True)
        t.start()
    except RuntimeError:
        return None
    return t
