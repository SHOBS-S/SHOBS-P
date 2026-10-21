#!/usr/bin/env python3
"""Count an observatory's own observations from FITS headers.

Walks one or more data folders, reads only the FITS headers (fast; no image data), and reports nights, target
runs, light frames, calibration frames and hours on target. A copy of the same frame in several folders
(Lights, Debayered, Science...) is counted once, keyed by DATE-OBS.

Only frames that look like your own are counted: IMAGETYP Light/Flat/Dark/Bias, and, when given, a matching
--creator and a site latitude within 0.01 deg of --lat. Others' data (MicroObservatory, other observers) is
listed separately so it can be cited as archival.

Usage:
  python tools/count_observations.py W:\\Exoplanet W:\\Variables --lat 35.303 --lon -81.94 --creator "ZWO 2600AIR"
  python tools/count_observations.py DATA --csv runs.csv

A "night" is the local evening date at the site, from --lon (solar time), so it needs no time-zone setting.
A "run" is one target on one night; its hours are first to last frame.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import os
import sys

FITS_EXT = (".fit", ".fits", ".fts")
KEYS = ("IMAGETYP", "DATE-OBS", "OBJECT", "EXPTIME", "CREATOR", "SITELAT", "SITELONG")


def read_header(path: str, max_blocks: int = 8) -> dict:
    """Primary-header cards as strings (enough for counting), without astropy."""
    out: dict = {}
    with open(path, "rb") as f:
        for _ in range(max_blocks):
            block = f.read(2880)
            if len(block) < 2880:
                break
            for i in range(0, 2880, 80):
                card = block[i:i + 80].decode("ascii", "replace")
                key = card[:8].strip()
                if key == "END":
                    return out
                if card[8:10] == "= " and key in KEYS:
                    out[key] = card[10:].split("/")[0].strip().strip("'").strip()
    return out


def kind(h: dict) -> str:
    t = (h.get("IMAGETYP") or "").strip().lower()
    for k in ("light", "flat", "dark", "bias"):
        if t.startswith(k) or t == k + " frame":
            return k
    return ""


def night_of(date_obs: str, lon: float) -> dt.date:
    t = dt.datetime.fromisoformat(date_obs[:19])
    local = t + dt.timedelta(hours=lon / 15.0)          # mean solar time at the site
    return (local - dt.timedelta(hours=12)).date()      # evening date: noon to noon


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--lat", type=float, help="site latitude; frames from elsewhere are listed as others' data")
    ap.add_argument("--lon", type=float, default=None, help="site longitude, east +; default from SITELONG")
    ap.add_argument("--creator", help="capture software CREATOR to count as yours, e.g. 'ZWO 2600AIR'")
    ap.add_argument("--csv", help="write one row per run (night, target, frames, hours) to this file")
    a = ap.parse_args(argv)

    own: dict = {}
    cal: dict = {}
    others = collections.Counter()
    scanned = 0
    for folder in a.folders:
        for dp, _dn, fn in os.walk(folder):
            for f in fn:
                if not f.lower().endswith(FITS_EXT):
                    continue
                p = os.path.join(dp, f)
                scanned += 1
                try:
                    h = read_header(p)
                except OSError:
                    continue
                k = kind(h)
                d = h.get("DATE-OBS")
                if not k or not d:
                    continue
                mine = True
                if a.creator and (h.get("CREATOR") or "") != a.creator:
                    mine = False
                if a.lat is not None:
                    try:
                        mine = mine and abs(float(h.get("SITELAT", "nan")) - a.lat) < 0.01
                    except ValueError:
                        mine = False if k == "light" else mine
                if not mine:
                    if k == "light":
                        others[os.path.relpath(dp, folder).split(os.sep)[0]] += 1
                    continue
                rel = os.path.relpath(p, folder)
                (own if k == "light" else cal).setdefault(d, (k, rel, h))

    runs = collections.defaultdict(list)
    exp = collections.Counter()
    for d, (_k, rel, h) in own.items():
        lon = a.lon if a.lon is not None else float(h.get("SITELONG") or 0)
        target = rel.split(os.sep)[0]
        n = night_of(d, lon)
        runs[(n, target)].append(dt.datetime.fromisoformat(d[:19]))
        exp[target] += float(h.get("EXPTIME") or 0)

    nights = sorted({n for n, _t in runs})
    hours = {r: (max(v) - min(v)).total_seconds() / 3600 for r, v in runs.items()}
    by_t = collections.defaultdict(lambda: [set(), 0, 0.0])
    for (n, t), v in runs.items():
        by_t[t][0].add(n); by_t[t][1] += len(v); by_t[t][2] += hours[(n, t)]

    print(f"Scanned {scanned} FITS files.")
    if nights:
        print(f"Your observations: {len(nights)} nights ({nights[0]} to {nights[-1]}), {len(runs)} target runs, "
              f"{len(own)} light frames, {sum(hours.values()):.1f} h on target.")
    cal_n = collections.Counter(k for k, _r, _h in cal.values())
    print("Calibration frames: " + ", ".join(f"{v} {k}" for k, v in sorted(cal_n.items())))
    print(f"\n{'Target':28s} {'Nights':>6s} {'Frames':>7s} {'Hours':>6s}")
    for t, (ns, fr, hr) in sorted(by_t.items()):
        print(f"{t[:28]:28s} {len(ns):6d} {fr:7d} {hr:6.1f}")
    if others:
        print("\nOthers' light frames (not counted above):")
        for t, v in others.most_common():
            print(f"  {t}: {v}")
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["night", "target", "frames", "hours"])
            for (n, t), v in sorted(runs.items()):
                w.writerow([n.isoformat(), t, len(v), f"{hours[(n, t)]:.2f}"])
        print(f"\nRuns written to {a.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
