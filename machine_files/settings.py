"""Remembers the last folder and machine between runs, in a small JSON file in the user's
settings folder."""

import json
import os
import sys

APP_DIR_NAME = "opendecodesk-machine-files"


def settings_path():
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_DIR_NAME, "settings.json")


def defaults():
    return {"lastFolder": "", "lastMachine": None}


def load(path=None):
    """The saved settings, or the defaults when the file is missing or unreadable."""
    path = path or settings_path()
    out = defaults()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        return out
    if not isinstance(saved, dict):
        return out
    if isinstance(saved.get("lastFolder"), str):
        out["lastFolder"] = saved["lastFolder"]
    m = saved.get("lastMachine")
    if isinstance(m, dict) and isinstance(m.get("ip"), str):
        out["lastMachine"] = {k: str(m.get(k) or "") for k in ("mac", "ip", "model")}
    return out


def save(data, path=None):
    """Write the settings; a failure is ignored (remembering is a convenience)."""
    path = path or settings_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False
