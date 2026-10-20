"""
Self-update for Mike's Canon Scanner.
Checks the latest GitHub release, downloads its Setup.exe and runs it silently.
The installer closes the app, replaces the files and starts the new version.
"""
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import webbrowser

from version import REPO, __version__

API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"


def _ver(s):
    s = s.strip().lstrip("vV")
    out = []
    for part in s.split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (3 - len(out)))


def is_newer(latest, current=__version__):
    return _ver(latest) > _ver(current)


def is_installed():
    """True when running as the installed .exe (not from source)."""
    return getattr(sys, "frozen", False)


def check(timeout=8):
    """Return dict(version, notes, url, setup_url) for the latest release, or None."""
    req = urllib.request.Request(API, headers={"User-Agent": "MikesCanonScanner/" + __version__,
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    setup = next((a["browser_download_url"] for a in data.get("assets", [])
                  if a["name"].lower().endswith("setup.exe")), None)
    return {"version": data.get("tag_name", "").lstrip("vV"), "notes": data.get("body") or "",
            "url": data.get("html_url") or RELEASES_PAGE, "setup_url": setup}


def download(url, progress=None):
    dest = os.path.join(tempfile.gettempdir(), "MikesCanonScanner-Setup.exe")
    req = urllib.request.Request(url, headers={"User-Agent": "MikesCanonScanner/" + __version__})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(f"Downloading update… {done * 100 // total}%")
    return dest


def run_installer(path):
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS", "/NORESTART"],
                     creationflags=flags, close_fds=True)


def open_releases():
    webbrowser.open(RELEASES_PAGE)
