"""Headless check of the step 5 comp survey on a real night (no Tk): the same frame reads as clearv_app's worker,
then comp_survey's stats for named candidates. Usage: survey_real.py REPO LIGHTS BIAS DARK FLAT CANDS.json R SI SO"""
import json, math, os, sys, time, glob
repo = sys.argv[1]; sys.path.insert(0, repo)
import numpy as np
import photometry_core as core
import comp_survey as cs
lights_dir, bias, dark, flat, cand_file = sys.argv[2:7]
r, si, so = (float(v) for v in sys.argv[7:10])
lights = sorted(p for p in glob.glob(os.path.join(lights_dir, "*.fit*")))
def entries(folder):
    return sorted(glob.glob(os.path.join(folder, "*.fit*")))
t0 = time.time()
masters = core.prepare_masters(entries(bias), entries(dark), entries(flat), binning=1, save=False,
                               log=lambda *a: None)
print("masters", round(time.time() - t0, 1), "s", masters.used, flush=True)
cands = json.load(open(cand_file))   # [{"name","ra","dec"}] or [{"name","x","y"}] on the reference frame
pattern, mode = "RGGB", "green"
raw0, h0 = core.read_fits(lights[0])
pattern = core.bayer_pattern(h0) if hasattr(core, "bayer_pattern") else pattern
ref = core.debayer(core.calibrate_frame(core.bin_image(raw0, 1), h0, masters), pattern, mode)
xy0 = np.asarray([(c["x"], c["y"]) for c in cands], dtype=float)
ref_sources = core.detect_sources(ref, max_sources=80)
t_def = core.field_defocus(ref); t_donut = bool(t_def.get("donut"))
fwhm = core.field_fwhm(ref)["fwhm"]
radii = cs.trial_radii(fwhm, r)
rings = {rr: ((si, so) if abs(rr - r) <= 0.06 else (core.sky_inner_radius(rr, fwhm),
         float(math.ceil(math.sqrt(core.sky_inner_radius(rr, fwhm) ** 2 + 4 * rr ** 2))))) for rr in radii}
rmax = max(radii)
ref_pts = core.refine_points(ref, ref_sources, rmax, t_donut)
print("lights", len(lights), "fwhm", round(fwhm, 2), "radii", radii, "pattern", pattern, flush=True)
frames = {}
last = {"s": None, "r": None}
def read(idx):
    for i in idx:
        t = time.time()
        raw, h = core.read_fits(lights[i])
        img = core.debayer(core.calibrate_frame(core.bin_image(raw, 1), h, masters), pattern, mode)
        if i == 0:
            reg = {"dx": 0.0, "dy": 0.0, "angle": 0.0, "flipped": False}
        else:
            src = core.detect_sources(img, max_sources=80)
            reg = core.register_frame(ref_sources, src, img.shape)
            if reg is None and last["s"] is not None:
                st = core.register_frame(last["s"], src, img.shape)
                reg = core.compose_registration(last["r"], st, img.shape) if st else None
            if reg is None:
                print("  skip", i); continue
            if reg.get("flipped"):
                core.seed_field_defocus(img, t_def if t_donut else False)
                reg = core.refine_registration(img, reg, ref_pts, rmax, t_donut)
            last["s"], last["r"] = src, reg
        pos = core.move_points(xy0, reg, img.shape)
        exp = core.header_exptime(h) or 1.0
        mags = {}
        for rr in radii:
            f, _ = core.measure_many(img, pos, rr, *rings[rr])
            with np.errstate(all="ignore"):
                mags[rr] = np.where(f > 0, -2.5 * np.log10(f / exp), np.nan)
        tp = pos[0]
        fw = core.field_fwhm(img[max(0, int(tp[1]) - 300):int(tp[1]) + 300, max(0, int(tp[0]) - 300):int(tp[0]) + 300], n=6)["fwhm"]
        peaks = core.raw_peaks_many(raw, pos, r, 2)
        frames[i] = {"mags": mags, "peaks": peaks, "fwhm": fw, "flipped": bool(reg.get("flipped"))}
        print(f"  frame {i + 1}: {time.time() - t:.1f} s fwhm {fw:.2f} flipped {frames[i]['flipped']}", flush=True)
first = cs.sample_indices(len(lights)); read(first)
more = cs.second_pass(len(lights), sorted(frames), {i: f["fwhm"] for i, f in frames.items()},
                      {i: f["flipped"] for i, f in frames.items()})
read(more)
idx = sorted(frames)
out = {"idx": idx, "radii": radii, "names": [c["name"] for c in cands], "flipped": [frames[i]["flipped"] for i in idx],
       "peaks": [np.asarray(frames[i]["peaks"]).tolist() for i in idx], "fwhm": [frames[i]["fwhm"] for i in idx],
       "mags": {str(rr): [np.asarray(frames[i]["mags"][rr]).tolist() for i in idx] for rr in radii},
       "elapsed": time.time() - t0, "rings": {str(k): v for k, v in rings.items()}}
json.dump(out, open(os.path.join(os.path.dirname(cand_file), "survey_out.json"), "w"))
print("done", round(time.time() - t0, 1), "s", len(idx), "frames")
