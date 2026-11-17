"""Updates from the admin panel (Admin → Settings → Updates).

PingScope can't update itself: the code and Docker run on the server. deploy.sh (installed by install.sh as a
systemd service) does the update, with a backup, a health check and a rollback. The admin panel and deploy.sh talk
through files in PingScope's data directory (next to the database):

  update-request       written here when an admin clicks "Update now"; a systemd path unit (pingscope-update.path,
                       set up by deploy.sh) starts deploy.sh right away, which removes the file
  deploy-status.json   written by deploy.sh after every check: result, newest version, whether the button works
  last-deploy.json     written by deploy.sh after a successful update: from which version to which

Without deploy.sh (for example the Docker Hub image with docker run), the panel shows how to update instead.
"""
import json
import os
import re
import threading
import time
import urllib.request

VERSION_URL = os.environ.get("PINGSCOPE_VERSION_URL",
                             "https://raw.githubusercontent.com/joebafra/PingScope/main/pingscope/__init__.py")
CHECK_EVERY = 3600  # seconds between two looks at GitHub
_lock = threading.Lock()
_latest = {"version": None, "checked": 0, "error": None}


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def version_key(v):
    """'0.025' -> (0, 25), for comparing versions."""
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "0")))


def latest_on_github(enabled=True):
    """The newest version on GitHub's main branch, looked up at most once an hour. Nothing about this installation
    is sent: it is a plain download of pingscope/__init__.py."""
    if not enabled:
        return None
    with _lock:
        if time.time() - _latest["checked"] < CHECK_EVERY:
            return dict(_latest)
        _latest["checked"] = time.time()
    try:
        req = urllib.request.Request(VERSION_URL, headers={"User-Agent": "PingScope"})
        with urllib.request.urlopen(req, timeout=5) as r:
            text = r.read(20000).decode("utf-8", "replace")
        m = re.search(r'^__version__ = "([^"]+)"', text, re.M)
        with _lock:
            _latest.update(version=m.group(1) if m else None, error=None if m else "no version found")
    except (OSError, ValueError) as e:
        with _lock:
            _latest.update(error=f"GitHub not reachable ({getattr(e, 'reason', e)})")
    return dict(_latest)


def status(data_dir, version, check_github=True):
    deploy = _read(os.path.join(data_dir, "deploy-status.json"))
    last = _read(os.path.join(data_dir, "last-deploy.json"))
    requested = os.path.exists(os.path.join(data_dir, "update-request"))
    # The newest version: what deploy.sh found in the installation's own git remote (also right for a fork),
    # otherwise GitHub
    latest = None
    if deploy and deploy.get("available_version"):
        latest = {"version": deploy["available_version"], "checked": deploy.get("checked"), "error": None,
                  "source": "deploy"}
    elif check_github:
        latest = {**latest_on_github(True), "source": "github"}
    newer = bool(latest and latest.get("version") and version_key(latest["version"]) > version_key(version))
    return {"deploy": deploy, "last_deploy": last, "requested": requested, "latest": latest,
            "update_available": newer, "can_update": bool(deploy and deploy.get("button"))}


def request(data_dir, user):
    path = os.path.join(data_dir, "update-request")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"at": int(time.time()), "by": user}, f)
    os.replace(tmp, path)  # appears at once, complete
