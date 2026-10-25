"""Builds the SHOBS-P release zip (2.2.19, #23 / #33).

    python3 tools/make_release_zip.py [OUT.zip] [--src REPO] [--top clearv_photometry]

Zips the app files of the repository into one top folder (clearv_photometry/, as every release so far) and stores
Unix permissions with each file, so the Linux and Mac launchers (*.sh, *.command) stay executable when the zip is
unpacked with Archive Utility on a Mac or unzip on Linux (0755 for those, 0644 for the rest). Windows ignores them.

What goes in: the files git tracks (git is required), minus what is for developers only:
docs/, tools/, .github/, .gitignore, .gitattributes, README.md (GitHub's page; README.txt is the one in the zip),
CITATION.cff, and run-time leftovers (__pycache__, *.pyc, selftest_log.txt, selftest_reports/, settings, logs).
The list of files is printed, so check it against the previous release before attaching the zip. It refuses to build
when an app file (*.py, *.sh, *.command, *.ps1, *.bat, requirements.txt) in the top folder is not tracked by git: a new
module left out of the zip would stop the shipped app with ModuleNotFoundError. Commit (or delete) it first.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
import time
import zipfile

TOP_DIRS_OUT = {"docs", "tools", ".github", ".git", "selftest_reports", "__pycache__"}
FILES_OUT = {".gitignore", ".gitattributes", "README.md", "CITATION.cff", "selftest_log.txt", ".shobs_p_settings.json"}
PATTERNS_OUT = ("*.pyc", "*.log", "*.zip", "*~", ".DS_Store")
EXECUTABLE = ("*.sh", "*.command")


APP_PATTERNS = ("*.py", "*.sh", "*.command", "*.ps1", "*.bat", "requirements.txt")


def git_files(src: str, *extra: str) -> list[str] | None:
    """git ls-files in src (paths with forward slashes), or None when src is not a git checkout / git is missing."""
    try:
        out = subprocess.run(["git", "-C", src, "ls-files", "-z"] + list(extra), capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [n for n in out.decode("utf-8").split("\0") if n]


def untracked_app_files(src: str) -> list[str]:
    """Top-level files that look like part of the app but that git does not track (so would be left out)."""
    others = git_files(src, "--others", "--exclude-standard") or []
    return sorted(n for n in others if "/" not in n and any(fnmatch.fnmatch(n, p) for p in APP_PATTERNS))


def wanted(rel: str) -> bool:
    parts = rel.split("/")
    if parts[0] in TOP_DIRS_OUT or "__pycache__" in parts:
        return False
    name = parts[-1]
    if len(parts) == 1 and name in FILES_OUT:
        return False
    return not any(fnmatch.fnmatch(name, p) for p in PATTERNS_OUT)


def main() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", nargs="?", default="SHOBS-P_release.zip", help="zip to write (default SHOBS-P_release.zip)")
    ap.add_argument("--src", default=here, help="the repository folder (default: the one this tool is in)")
    ap.add_argument("--top", default="clearv_photometry", help="top folder inside the zip")
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    tracked = git_files(src)
    if not tracked:
        print("%s is not a git checkout (or git is missing): the zip is built from git's list of files only." % src)
        return 2
    missing = untracked_app_files(src)
    if missing:
        for n in missing:
            print("PROBLEM: %s is not tracked by git, so it would be left out of the zip. Commit it (or delete it)." % n)
        return 1
    names = sorted(n for n in tracked if wanted(n) and os.path.isfile(os.path.join(src, n)))
    if "clearv_app.py" not in names:
        print("clearv_app.py is not in %s: is --src the SHOBS-P folder?" % src)
        return 2
    problems = []
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        top = zipfile.ZipInfo(args.top + "/", date_time=time.localtime()[:6])
        top.create_system = 3
        top.external_attr = (0o40755 << 16) | 0x10  # a folder, rwxr-xr-x (+ the MS-DOS folder flag)
        zf.writestr(top, b"")
        for rel in names:
            path = os.path.join(src, rel)
            with open(path, "rb") as fh:
                data = fh.read()
            executable = any(fnmatch.fnmatch(rel.split("/")[-1], p) for p in EXECUTABLE)
            if executable and b"\r\n" in data:
                problems.append(rel + " has Windows (CRLF) line endings: bash cannot run it")
            info = zipfile.ZipInfo(args.top + "/" + rel, date_time=time.localtime(os.path.getmtime(path))[:6])
            info.create_system = 3  # Unix, so unzip and Archive Utility apply the permissions below
            info.external_attr = (0o100000 | (0o755 if executable else 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
            print("  %s %s" % ("0755" if executable else "0644", rel))
    print("%d files -> %s (top folder %s/)" % (len(names), os.path.abspath(args.out), args.top))
    for p in problems:
        print("PROBLEM: " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
