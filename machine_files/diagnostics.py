"""What the app did and what it's running on, for "Save diagnostics…": a log kept in memory and in
a small file beside the settings, plus the version, system and network details that explain why a
scan finds nothing (which network addresses this computer has, whether Windows treats the network
as Public, whether its firewall lets the app hear the machines' replies, a Remote Desktop session).

Nothing is sent anywhere: the report is saved to a file the user picks and can read before sharing.
"""

import collections
import datetime
import logging
import logging.handlers
import os
import platform
import socket
import subprocess
import sys

from . import __version__
from .protocol import directed_broadcasts, local_ipv4_addresses
from . import settings as st

LOG_NAME = "machine_files"
MEMORY_LINES = 2000
_memory = collections.deque(maxlen=MEMORY_LINES)
_configured = False


class _MemoryHandler(logging.Handler):
    def emit(self, record):
        try:
            _memory.append(self.format(record))
        except Exception:  # noqa: BLE001 — logging must never break the app
            pass


def log_file_path():
    return os.path.join(os.path.dirname(st.settings_path()), "app.log")


def setup(log_file=None):
    """Start logging (once): to memory always, and to `log_file` (default: app.log beside the
    settings, rotated at 512 KB) when it can be written."""
    global _configured
    logger = logging.getLogger(LOG_NAME)
    if _configured:
        return logger
    _configured = True
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    mem = _MemoryHandler()
    mem.setFormatter(fmt)
    logger.addHandler(mem)
    path = log_file or log_file_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=512 * 1024, backupCount=1, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    logger.info("OpenDecoDesk Machine Files %s starting on %s", __version__, platform.platform())
    return logger


def recent_log():
    return list(_memory)


def _run(cmd, timeout=8):
    """A command's output for the report, or what went wrong. Windows: no console window flashes."""
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)
        out = (p.stdout or b"") + (p.stderr or b"")
        for enc in ("utf-8", "mbcs" if os.name == "nt" else "latin-1", "latin-1"):
            try:
                return out.decode(enc).strip()
            except (UnicodeDecodeError, LookupError):
                continue
        return out.decode("latin-1", "replace").strip()
    except (OSError, subprocess.SubprocessError) as e:
        return f"(couldn't run {cmd[0]}: {e})"


def _firewall_rules_for_this_app(text, program):
    """The blocks of `netsh advfirewall firewall show rule ... verbose` that mention this program."""
    wanted = [program.lower(), "opendecodesk"]
    blocks, cur = [], []
    for line in text.splitlines():
        if not line.strip() and cur:
            blocks.append(cur)
            cur = []
        elif line.strip():
            cur.append(line)
    if cur:
        blocks.append(cur)
    keep = [b for b in blocks if any(w in "\n".join(b).lower() for w in wanted)]
    return "\n\n".join("\n".join(b) for b in keep) or "(no firewall rule mentions this app)"


def system_report():
    """Version, system and network details as (title, text) sections."""
    exe = sys.executable
    sections = [("App", "\n".join([
        f"OpenDecoDesk Machine Files {__version__}",
        f"Built app: {'yes' if getattr(sys, 'frozen', False) else 'no (running from source)'}",
        f"Program: {exe}",
        f"Python: {platform.python_version()} ({platform.architecture()[0]})",
        f"Settings: {st.settings_path()}",
        f"Log file: {log_file_path()}",
    ])), ("System", "\n".join([
        f"Platform: {platform.platform()}",
        f"Machine: {platform.machine()}",
        f"Host name: {socket.gethostname()}",
        f"Remote Desktop session: {_rdp_note()}",
    ])), ("This computer's IPv4 addresses", "\n".join(local_ipv4_addresses()) or "(none found)"),
        ("Where the scan is sent", "\n".join(["255.255.255.255"] + directed_broadcasts(local_ipv4_addresses())))]
    if os.name == "nt":
        sections += [
            ("Network category (Windows: Public networks block the scan replies unless allowed)",
             _run(["powershell", "-NoProfile", "-Command",
                   "Get-NetConnectionProfile | Format-List Name,InterfaceAlias,NetworkCategory,IPv4Connectivity"])),
            ("Firewall state", _run(["netsh", "advfirewall", "show", "allprofiles", "state"])),
            ("Firewall rules for this app (inbound)",
             _firewall_rules_for_this_app(_run(["netsh", "advfirewall", "firewall", "show", "rule",
                                                "name=all", "dir=in", "verbose"], timeout=20), exe)),
            ("Network adapters", _run(["ipconfig"])),
            ("Routes", _run(["route", "print", "-4"])),
        ]
    elif sys.platform == "darwin":
        sections += [("Network adapters", _run(["ifconfig"])),
                     ("Firewall", _run(["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"]))]
    else:
        sections += [("Network adapters", _run(["ip", "-4", "addr"]))]
    return sections


def _rdp_note():
    if os.name != "nt":
        return "n/a"
    name = os.environ.get("SESSIONNAME", "")
    return f"yes ({name})" if name.upper().startswith("RDP") else f"no ({name or 'unknown'})"


def build_report(extra=None):
    """The whole report as text: when, the app's own state (`extra`, a list of (title, text)), the
    system and network details, then the recent log."""
    parts = [f"OpenDecoDesk Machine Files {__version__} — diagnostics, "
             f"{datetime.datetime.now().astimezone().isoformat(timespec='seconds')}"]
    for title, text in list(extra or []) + system_report():
        parts.append(f"\n===== {title} =====\n{text}")
    lines = recent_log()
    parts.append(f"\n===== Log (last {len(lines)} lines) =====\n" + "\n".join(lines))
    return "\n".join(parts) + "\n"
