"""2.4.0 step 7b (#92): the Registry file, one per observatory.

campaign_registry.json holds every Candidate and every star with an outcome, keyed on its Gaia DR3 source_id (a star
with no Gaia ID yet is keyed on its position, "pos:RA,Dec" to 0.0001 deg, until one is found). Suspects are never
written here (they live on their night's Discovery results) and Watch stars live in their series.

Record (design doc "SHOBS-P: Suspect -> Watch -> Candidate -> Campaign", Registry record section):
  key, gaia_dr3, display_name, aliases, coords {ra_deg, dec_deg}, kind ("variable"; "transit" reserved),
  status (candidate | confirmed_new | known | rejected), campaign (true/false) + campaign_history [{utc, on, by}],
  history [{utc, from, to, by, note}], evidence {series_files, parent_target, nights, baseline_days, n_points,
  period_days, semi_amplitude, sigma_amp, amp_snr, red_noise_beta, fap_single, m_trials, fap_eff, gates [...]},
  quiet_nights [...], neighbors (Check neighbors result, step 7c), observing_plan, sched {...},
  rejection {code, note}, fails {count, last_utc} (a Campaign star failing a hard gate twice in a row -> Rejected).

The file lives in the shared SHOBS data folder (~/.shobs, or $SHOBS_DATA_HOME), beside the instrument profiles,
so SHOBS-SCHED can read it too. It is written atomically (temporary file, then replace), and a copy of the previous
file is kept as campaign_registry_prev.json.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone

REGISTRY_KIND = "SHOBS campaign registry"
REGISTRY_VERSION = 1
REGISTRY_NAME = "campaign_registry.json"
STATUSES = ("candidate", "confirmed_new", "known", "rejected")
NEXT_STEPS = ("more_nights", "confirm_dedicated", "multi_period", "vsx_package", "aavso_submit", "none")
REJECTION_CODES = ("alias_1d", "single_night", "comp_signal", "flip", "saturation", "edge", "cloud_corr",
                   "airmass_corr", "duplicate_of")
STATUS_WORDS = {"candidate": "Candidate", "confirmed_new": "Confirmed new", "known": "Known", "rejected": "Rejected"}
HARD_FAILS_TO_REJECT = 2
SCHED_EXPORT_KIND = "SHOBS-P campaign export"


def data_home() -> str:
    return os.environ.get("SHOBS_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".shobs")


def registry_path() -> str:
    return os.path.join(data_home(), REGISTRY_NAME)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_GAIA_ID = re.compile(r"(\d{6,})")


def gaia_source_id(text) -> str:
    """'Gaia DR3 440531844915790208' or '440531844915790208' -> '440531844915790208'; '' when there is none."""
    s = str(text or "").strip()
    if not s:
        return ""
    if s.lower().startswith("gaia") and "dr3" not in s.lower():
        return ""   # a DR2 or DR4 name is not a DR3 source_id
    m = _GAIA_ID.search(s)
    return m.group(1) if m else ""


def star_key(gaia="", ra=None, dec=None) -> str:
    gid = gaia_source_id(gaia)
    if gid:
        return gid
    try:
        ra, dec = float(ra), float(dec)
    except (TypeError, ValueError):
        return ""
    if not (math.isfinite(ra) and math.isfinite(dec)):
        return ""
    return f"pos:{ra:.4f},{dec:+.4f}"


def empty() -> dict:
    return {"kind": REGISTRY_KIND, "version": REGISTRY_VERSION, "updated": utc_now(), "stars": {}}


def load(path: str | None = None) -> dict:
    """The registry, or an empty one when there is no file yet. A file that cannot be read raises ValueError (it is
    never silently replaced by an empty one)."""
    path = path or registry_path()
    if not os.path.exists(path):
        return empty()
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ValueError(f"The Registry file {path} could not be read ({exc}).") from exc
    if not isinstance(doc, dict) or doc.get("kind") != REGISTRY_KIND or not isinstance(doc.get("stars"), dict):
        raise ValueError(f"{path} is not a SHOBS campaign registry.")
    if not all(isinstance(r, dict) for r in doc["stars"].values()):
        raise ValueError(f"{path} holds a damaged star record; this version does not change it.")
    try:
        if int(doc.get("version", 1)) > REGISTRY_VERSION:
            raise ValueError(f"{path} was written by a newer version; this one does not change it.")
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    return doc


def save(doc: dict, path: str | None = None) -> str:
    path = path or registry_path()
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    doc["updated"] = utc_now()
    if os.path.exists(path):
        prev = os.path.join(folder, REGISTRY_NAME.replace(".json", "_prev.json")) if path.endswith(REGISTRY_NAME) \
            else path + ".prev"
        try:
            with open(path, "rb") as src, open(prev, "wb") as dst:
                dst.write(src.read())
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(prefix=".registry_", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(clean_json(doc), fh, indent=2, ensure_ascii=False, allow_nan=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return path


def clean_json(v):
    """NaN / inf -> None, numpy numbers -> Python (Fable nit: strict JSON readers such as SHOBS-SCHED)."""
    if isinstance(v, dict):
        return {k: clean_json(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean_json(x) for x in v]
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            v = v.item()
        except Exception:
            pass
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def find(doc: dict, gaia="", ra=None, dec=None, tol_arcsec: float = 3.0):
    """The record of a star: by Gaia DR3 source_id, else by position within tol_arcsec. None when not there."""
    stars = doc.get("stars") or {}
    gid = gaia_source_id(gaia)
    if gid and gid in stars:
        return stars[gid]
    try:
        ra, dec = float(ra), float(dec)
    except (TypeError, ValueError):
        return None
    for rec in stars.values():
        c = rec.get("coords") or {}
        try:
            r2, d2 = float(c["ra_deg"]), float(c["dec_deg"])
        except (KeyError, TypeError, ValueError):
            continue
        if gid and rec.get("gaia_dr3") and gaia_source_id(rec["gaia_dr3"]) != gid:
            continue
        if _sep_arcsec(ra, dec, r2, d2) <= tol_arcsec:
            return rec
    return None


def _sep_arcsec(ra1, de1, ra2, de2) -> float:
    r1, d1, r2, d2 = (math.radians(v) for v in (ra1, de1, ra2, de2))
    s = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(s)))) * 3600.0


def _num(v, nd=6):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def evidence_from(report: dict, series_file: str = "", parent_target: str = "") -> dict:
    """The evidence block from a promotion.gate_report."""
    return {
        "series_files": [os.path.basename(series_file)] if series_file else [],
        "parent_target": parent_target,
        "nights": list(report.get("nights") or []),
        "gates_version": int(report.get("version") or 1),
        "baseline_days": _num(report.get("span_days"), 3), "cycles": _num(report.get("cycles"), 1),
        "n_points": int(report.get("n_points") or 0),
        "period_days": _num(report.get("period_days"), 7), "power": _num(report.get("power"), 4),
        "semi_amplitude": _num(report.get("amp"), 5), "sigma_point": _num(report.get("sigma_point"), 5),
        "sigma_amp": _num(report.get("sigma_amp"), 6), "amp_snr": _num(report.get("amp_snr"), 2),
        "red_noise_beta": _num(report.get("beta"), 2),
        "fap_single": report.get("fap") if isinstance(report.get("fap"), float) and math.isfinite(report["fap"]) else None,
        "m_trials": int(report.get("m_trials") or 1),
        "fap_eff": report.get("fap_eff") if isinstance(report.get("fap_eff"), float)
        and math.isfinite(report["fap_eff"]) else None,
        "gates": [{k: g.get(k) for k in ("key", "label", "set", "value", "need", "pass", "cleared_by", "why")}
                  for g in report.get("gates") or []],
        "dropped_nights": list(report.get("dropped_nights") or []),
        "nulls": list(report.get("nulls") or []),
        "gate_run_utc": utc_now(),
    }


def new_record(key: str, name: str, gaia="", ra=None, dec=None, aliases=(), kind: str = "variable") -> dict:
    return {
        "key": key, "gaia_dr3": (f"Gaia DR3 {gaia_source_id(gaia)}" if gaia_source_id(gaia) else ""),
        "display_name": name, "aliases": [a for a in dict.fromkeys([name, *aliases]) if a],
        "coords": {"ra_deg": _num(ra, 6), "dec_deg": _num(dec, 6), "epoch": 2016.0},
        "kind": kind, "status": "candidate", "campaign": False, "campaign_history": [],
        "dormant": False, "history": [], "evidence": {}, "quiet_nights": [], "neighbors": None,
        "observing_plan": {}, "sched": {"priority": 3, "next_step": "more_nights", "time_critical": [],
                                        "last_observed_utc": None, "deadline_utc": None},
        "rejection": {"code": None, "note": None}, "fails": {"count": 0, "last_utc": None},
    }


def upsert_from_report(doc: dict, report: dict, name: str, gaia="", ra=None, dec=None, aliases=(),
                       series_file: str = "", parent_target: str = "", by: str = "auto") -> tuple[dict | None, str]:
    """After a gate run on a Watch star (or a Registry star): add it as a Candidate when the Candidate gates pass and
    it is not in the Registry yet, or refresh the evidence of a star already there. Returns (record or None, event):
    event "new" (just became a Candidate), "updated", "campaign_fail" (a Campaign star failed a hard gate on new data:
    its campaign is switched off and it stays a Candidate), "rejected" (the second consecutive failure), "" (nothing:
    not a Candidate and not in the Registry)."""
    rec = find(doc, gaia, ra, dec)
    if rec is None and not report.get("candidate"):
        return None, ""
    event = "updated"
    if rec is None:
        key = star_key(gaia, ra, dec) or f"name:{name}"
        rec = new_record(key, name, gaia, ra, dec, aliases)
        rec["history"].append({"utc": utc_now(), "from": "watching", "to": "candidate", "by": by,
                               "note": report.get("summary", "")})
        doc["stars"][key] = rec
        event = "new"
    else:
        for a in [name, *aliases]:
            if a and a not in rec["aliases"]:
                rec["aliases"].append(a)
        if gaia_source_id(gaia) and not rec.get("gaia_dr3"):
            rec["gaia_dr3"] = f"Gaia DR3 {gaia_source_id(gaia)}"
    old_ev = rec.get("evidence") or {}
    if event != "new" and not report.get("period_days"):
        return rec, ""   # Fable S3: a run too thin to judge never replaces the record's evidence
    old_files = list(old_ev.get("series_files") or [])
    old_gates = old_ev.get("gates") or []
    old_p = old_ev.get("period_days")
    # Fable M2: a failure counts toward demotion only on NEW data (other nights or points than last time)
    data_changed = (list(report.get("nights") or []) != list(old_ev.get("nights") or [])
                    or int(report.get("n_points") or 0) != int(old_ev.get("n_points") or 0))
    # a NEWER gate rule set judging the same data (21 Nov 2026: saturation rule) may switch a campaign off or clear
    # a failure count, but never counts toward auto-Rejection (that needs new data: Fable review)
    rule_changed = int(report.get("version") or 1) > int(old_ev.get("gates_version") or 1)
    new_data = data_changed or rule_changed
    rec["evidence"] = evidence_from(report, series_file, parent_target)
    rec["evidence"]["series_files"] = list(dict.fromkeys(old_files + rec["evidence"]["series_files"]))
    # keep a gate cleared by outside data (decision 10) when our own run still fails it
    same_p = bool(old_p and report.get("period_days")
                  and abs(float(report["period_days"]) - float(old_p)) / float(old_p) <= 0.01)   # Fable S6
    cleared = {g["key"]: g.get("cleared_by") for g in old_gates
               if same_p and g.get("cleared_by") and g.get("cleared_by") != "ours" and g.get("pass")}
    for g in rec["evidence"]["gates"]:
        if g["key"] in cleared and g["key"] in ("alias_pm1", "span_days", "volume") and not g["pass"]:
            g["pass"] = True
            g["cleared_by"] = cleared[g["key"]]
    rec["quiet_nights"] = list(report.get("quiet_nights") or [])
    hard = hard_failures(rec["evidence"]["gates"])
    if rec.get("campaign") and hard and (new_data or event == "new"):
        rec["fails"]["count"] = int(rec["fails"].get("count") or 0) + 1
        rec["fails"]["last_utc"] = utc_now()
        set_campaign(rec, False, by="auto", note="failed " + ", ".join(hard) + " on new data")
        event = "campaign_fail"
    elif rec.get("status") == "candidate" and hard and data_changed and int(rec["fails"].get("count") or 0) >= 1:
        rec["fails"]["count"] = int(rec["fails"]["count"]) + 1
        rec["fails"]["last_utc"] = utc_now()
        if rec["fails"]["count"] >= HARD_FAILS_TO_REJECT:
            set_status(rec, "rejected", by="auto", code=rejection_from(rec["evidence"]["gates"]),
                       note="failed " + ", ".join(hard) + " twice in a row")
            event = "rejected"
    elif not hard and new_data:
        rec["fails"]["count"] = 0
    rec["sched"]["next_step"] = next_step(rec)
    return rec, event


HARD_GATES = ("nulls", "flip", "linear", "edge", "corr", "alias_1d", "loo")


def hard_failures(gates) -> list:
    return [g["key"] for g in gates or [] if g.get("key") in HARD_GATES and g.get("pass") is False]


def rejection_from(gates) -> str:
    for g in gates or []:
        if g.get("key") in HARD_GATES and g.get("pass") is False:
            code = g.get("why") or {"nulls": "comp_signal", "flip": "flip", "linear": "saturation", "edge": "edge",
                                    "corr": "cloud_corr", "alias_1d": "alias_1d", "loo": "single_night"}[g["key"]]
            return code if code in REJECTION_CODES else "cloud_corr"
    return ""


def campaign_ready(rec: dict) -> bool:
    """The Promote to campaign button lights when every gate passes (our data, or outside data where allowed), or
    any time once the star is Confirmed (decision 11)."""
    if rec.get("status") == "confirmed_new":
        return True
    if rec.get("status") != "candidate":
        return rec.get("status") == "known"
    gates = (rec.get("evidence") or {}).get("gates") or []
    return bool(gates) and all(g.get("pass") is True for g in gates)


def set_campaign(rec: dict, on: bool, by: str = "John", note: str = "") -> None:
    if bool(rec.get("campaign")) == bool(on):
        return
    rec["campaign"] = bool(on)
    rec.setdefault("campaign_history", []).append({"utc": utc_now(), "on": bool(on), "by": by, "note": note})
    rec["sched"]["next_step"] = next_step(rec)


def set_status(rec: dict, status: str, by: str = "John", code: str = "", note: str = "") -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status}")
    old = rec.get("status")
    if old == status:
        return
    rec["status"] = status
    rec.setdefault("history", []).append({"utc": utc_now(), "from": old, "to": status, "by": by, "note": note})
    if status == "rejected":
        rec["rejection"] = {"code": code or None, "note": note or None}
        if rec.get("campaign"):
            set_campaign(rec, False, by=by, note="rejected")
    elif old == "rejected":
        rec["rejection"] = {"code": None, "note": None}
        rec["fails"] = {"count": 0, "last_utc": None}
    rec["sched"]["next_step"] = next_step(rec)


def next_step(rec: dict) -> str:
    st = rec.get("status")
    if st == "rejected":
        return "none"
    if st == "confirmed_new":
        return "vsx_package" if not rec.get("campaign") else "confirm_dedicated"
    if st == "known":
        return "aavso_submit"
    gates = (rec.get("evidence") or {}).get("gates") or []
    if rec.get("campaign"):
        return "confirm_dedicated"
    if any(g.get("key") in ("volume", "span", "span_days", "single") and g.get("pass") is not True for g in gates):
        return "more_nights"
    return "confirm_dedicated" if gates and all(g.get("pass") is True for g in gates) else "more_nights"


def status_text(rec: dict) -> str:
    w = STATUS_WORDS.get(rec.get("status"), str(rec.get("status")))
    if rec.get("campaign"):
        w += " · Campaign on"
    if rec.get("status") == "rejected" and (rec.get("rejection") or {}).get("code"):
        w += f" ({rec['rejection']['code']})"
    return w


def sched_targets(doc: dict) -> list[dict]:
    """The Campaign stars (campaign on, not Rejected) as SHOBS-SCHED Target rows (SCHED's own targets-file shape:
    kind campaign, mode V, elements with the period). SHOBS-SCHED imports this file; SHOBS-P never writes SCHED's
    own files."""
    rows = []
    for rec in (doc.get("stars") or {}).values():
        if not rec.get("campaign") or rec.get("status") == "rejected":
            continue
        ev = rec.get("evidence") or {}
        c = rec.get("coords") or {}
        plan = rec.get("observing_plan") or {}
        rows.append({
            "name": rec.get("display_name") or rec.get("key"), "kind": "campaign", "mode": "V",
            "ra": c.get("ra_deg"), "dec": c.get("dec_deg"), "vmag": (rec.get("catalog") or {}).get("vmag"),
            "priority": "High" if int((rec.get("sched") or {}).get("priority") or 3) <= 2 else "Normal",
            "enabled": True, "suggested": False, "source": "SHOBS-P Registry",
            "elements": {"vtype": "", "period": ev.get("period_days"), "epoch": ev.get("epoch_max_jd"),
                         "epoch_kind": "max", "any_time": True},
            "exposure_s": plan.get("exposure_s_rec"), "goal_per_week": int(plan.get("nights_per_week") or 2),
            "notes": (f"SHOBS-P Registry {STATUS_WORDS.get(rec.get('status'), '')}; next step "
                      f"{(rec.get('sched') or {}).get('next_step', '')}; Gaia {rec.get('gaia_dr3') or 'none'}"),
            "shobs_p_series": "", "registry_key": rec.get("key"),
            "sched": dict(rec.get("sched") or {}),
        })
    return rows


def write_sched_export(doc: dict, path: str, app_version: str = "") -> int:
    rows = sched_targets(doc)
    out = {"kind": SCHED_EXPORT_KIND, "version": 1, "app": f"SHOBS-P {app_version}".strip(), "created": utc_now(),
           "registry": registry_path(), "targets": rows}
    folder = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".sched_export_", suffix=".tmp", dir=folder)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return len(rows)
