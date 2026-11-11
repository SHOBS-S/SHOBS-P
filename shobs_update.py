"""Is a newer release out? One small module shared by every SHOBS app (SHOBS-P, SHOBS-SCHED, SHOBS-FLUX): keep the
copies identical, like shobs_profile.py. GitHub cannot tell someone who downloaded a release zip that a new one is
out, so each app asks for itself (GitHub SHOBS-FLUX #2, SHOBS-P #57; John, 8 Nov 2026).

What it does, and what it never does:
  - at start, in the background (never slows the start, never blocks the window), asks GitHub's public API for the
    repository's latest release: GET https://api.github.com/repos/<owner>/<repo>/releases/latest. No account, no
    token, nothing about the user is sent (only the app's name and version as the User-Agent GitHub requires).
  - at most one question a day: the time and the answer are kept in the app's own settings; between checks the kept
    answer is used, so the note shows again at every start until the app is updated.
  - silent on no internet, a timeout (5 s), an HTTP error or the rate limit. Drafts and pre-releases are ignored.
  - says a plain note ("SHOBS-FLUX 1.0.1 is available — see the release notes"), with the release page's link.
  - never downloads or installs anything.

What's new (John, 10 Nov 2026): each release's notes carry a "Highlights:" section, up to HIGHLIGHTS_MAX one-line
bullets for users (new features, big interface changes; not bug-fix lists). They come in the same answer (the
release's "body"), so no extra request. The note then reads "SHOBS-SCHED 1.4.1 is available: <first highlight> —
click for details", and the app's "New version" window lists them with an Open release page button. No Highlights
section: the plain note. The notes are treated as plain text (Markdown marks stripped, nothing rendered or run).

The app supplies its settings as a dict: check_updates (on by default), update_checked_at (seconds), update_tag,
update_url, update_highlights (list), update_published (YYYY-MM-DD)."""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request

API = "https://api.github.com/repos/{repo}/releases/latest"
TIMEOUT_S = 5.0
EVERY_S = 24 * 3600.0            # one question a day at most
HIGHLIGHTS_MAX = 3               # bullets kept from the release notes' Highlights section
HIGHLIGHT_CHARS = 80             # each trimmed to this
NOTE_CHARS = 110                 # the status-strip note trimmed to this
STATE_KEYS = ("update_checked_at", "update_tag", "update_url", "update_highlights", "update_published")


def parse_version(text) -> tuple | None:
    """'v1.2.3', '1.2', 'SHOBS-FLUX 1.0.0' → (1, 2, 3) / (1, 2, 0) / (1, 0, 0); None when there is no version."""
    # 1–3 digits a part: a date-like tag (2026.11.09) is not a version (Fable 1.0.0)
    m = re.search(r"(?<![\d.])v?(\d{1,3})\.(\d{1,3})(?:\.(\d{1,3}))?(?:\.(\d{1,3}))?(?![\d.])", str(text or ""))
    if not m:
        return None
    return tuple(int(g) for g in m.groups(default="0"))


def is_newer(latest, current) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


_HEAD = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*highlights\s*:?\s*(?:\*\*|__)?\s*:?\s*$", re.IGNORECASE)
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d{1,2}[.)])\s+(.*\S)\s*$")


def _plain(text: str) -> str:
    """Markdown marks out: [words](link) → words, **bold** / `code` / _x_ → their words; spaces squeezed."""
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", str(text or ""))
    t = re.sub(r"<[^>]{1,200}>", "", t)
    t = t.replace("**", "").replace("__", "").replace("`", "")
    t = re.sub(r"(?<!\w)[*_](\S(?:.*?\S)?)[*_](?!\w)", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def _trim(text: str, n: int) -> str:
    return text if len(text) <= n else text[: max(1, n - 1)].rstrip(" ,;:—-") + "…"


def parse_highlights(body) -> list[str]:
    """The bullets under a "Highlights:" line or heading of the release notes (at most HIGHLIGHTS_MAX, each plain
    text trimmed to HIGHLIGHT_CHARS); [] when there is no such section."""
    lines = str(body or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    inside = False
    for ln in lines:
        if not inside:
            if _HEAD.match(ln):
                inside = True
            continue
        m = _BULLET.match(ln)
        if m:
            words = _plain(m.group(1))
            if words:
                out.append(_trim(words, HIGHLIGHT_CHARS))
            if len(out) >= HIGHLIGHTS_MAX:
                break
            continue
        if not ln.strip():
            continue                                    # blank lines (a "loose" list) do not end the section
        break                                           # any other line (the next heading, a paragraph) ends it
    return out


def note_text(app_name: str, version: str, highlights) -> str:
    """The status-strip words: with a highlight, "<app> <v> is available: <first> — click for details"."""
    if highlights:
        head = f"{app_name} {version} is available: "
        tail = " — click for details"
        room = max(10, NOTE_CHARS - len(head) - len(tail))
        return head + _trim(str(highlights[0]), room) + tail
    return f"{app_name} {version} is available — see the release notes"


def fetch_latest(repo: str, user_agent: str = "SHOBS", timeout: float = TIMEOUT_S, opener=None) -> dict | None:
    """{'tag', 'url', 'name', 'highlights', 'published'} of the repository's latest full release, or None (no internet, error, rate limit, a draft
    or pre-release, or an answer that makes no sense). Never raises."""
    req = urllib.request.Request(API.format(repo=repo), headers={"Accept": "application/vnd.github+json",
                                                                 "User-Agent": user_agent})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
            doc = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:                                   # offline, timeout, HTTP 403/404/5xx, bad JSON: say nothing
        return None
    if not isinstance(doc, dict) or doc.get("draft") or doc.get("prerelease"):
        return None
    tag = str(doc.get("tag_name") or "")
    url = str(doc.get("html_url") or "")
    if not parse_version(tag) or not url.startswith("https://github.com/"):
        return None
    pub = str(doc.get("published_at") or "")[:10]
    return {"tag": tag, "url": url, "name": str(doc.get("name") or tag),
            "highlights": parse_highlights(doc.get("body")),
            "published": pub if re.match(r"^\d{4}-\d\d-\d\d$", pub) else ""}


def check(app_name: str, repo: str, current: str, state: dict, now: float | None = None, fetch=None) -> dict | None:
    """The note to show, or None. state (the app's settings) is updated in place with the time and the answer when
    GitHub was asked; a failed question keeps the old answer and asks again at the next start."""
    if not state.get("check_updates", True):
        return None
    now = time.time() if now is None else now
    last = state.get("update_checked_at")
    if not isinstance(last, (int, float)) or not 0 <= now - last < EVERY_S:
        got = (fetch or (lambda: fetch_latest(repo, f"{app_name}/{current}")))()
        if got:
            state["update_checked_at"] = now
            state["update_tag"] = got["tag"]
            state["update_url"] = got["url"]
            state["update_highlights"] = list(got.get("highlights") or [])
            state["update_published"] = got.get("published") or ""
    tag, url = state.get("update_tag"), state.get("update_url")
    if tag and url and is_newer(tag, current):
        v = ".".join(str(x) for x in parse_version(tag)[:3])
        hl = [str(h) for h in (state.get("update_highlights") or []) if str(h).strip()][:HIGHLIGHTS_MAX]
        return {"text": note_text(app_name, v, hl), "url": url, "tag": tag, "version": v, "highlights": hl,
                "published": str(state.get("update_published") or ""), "app": app_name}
    return None


def start(root, app_name: str, repo: str, current: str, state: dict, save, show) -> None:
    """Run check() on a background thread and, on the window's own thread, save(state) when it changed and show(note)
    when there is one. Tk is touched only from root.after."""
    if not state.get("check_updates", True):
        return
    box: dict = {}
    before = tuple(repr(state.get(k)) for k in STATE_KEYS)
    work = dict(state)

    def run():
        try:
            box["note"] = check(app_name, repo, current, work)
        except Exception:
            box["note"] = None
        box["done"] = True

    threading.Thread(target=run, daemon=True).start()
    tries = [0]

    def poll():
        if not box.get("done"):
            tries[0] += 1
            if tries[0] < 100:                          # ~20 s at most, then give up quietly
                root.after(200, poll)
            return
        for k in STATE_KEYS:
            if k in work:
                state[k] = work[k]
        if tuple(repr(state.get(k)) for k in STATE_KEYS) != before:
            try:
                save(state)
            except Exception:
                pass
        if box.get("note"):
            show(box["note"])

    root.after(200, poll)
