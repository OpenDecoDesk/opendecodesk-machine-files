# OpenDecoDesk Machine Files

A small desktop app for copying embroidery designs between a folder on your computer and a
**SmartStitch** embroidery machine on your network — no vendor software, no website, nothing to
install besides the app itself.

```
┌ Folder ───────────────────┐               ┌ Machine ──────────────────────┐
│ [/path/to/designs][▤][↻]│  ◀◀ Copy all   │ [1501ultra — 192.168.1.40 ▾][↻]│
│ LOGO.DST                  │  ◀ Copy sel.   │ Slot  Name                      │
│ NAME_CAP.DSB              │  Copy sel. ▶   │   12  LOGO                      │
│                           │  Copy all ▶▶   │   13  CREST                     │
└───────────────────────────┘               └───────────────────────────────┘
```

- **Left:** a folder (type a path and press Enter, or click the folder button). Only `.dst` and `.dsb` files are
  listed. The list updates by itself when files are added, changed or removed (checked every 2 seconds);
  ↻ beside the folder button reads it again straight away.
- **Right:** the machines found on the network (↻ scans again) and the files on the one picked —
  its slot number and name.
- **The four buttons:** copy everything, or just the selected files, in either direction. A file
  that's already on the other side (same name, ignoring the extension and the machine's slot
  number) is skipped, never overwritten.
- It remembers the last folder and machine. The machine is remembered by its network card's MAC
  address, so it's still picked when its IP address changes.

### What works today
| | |
|---|---|
| Finding machines (UDP scan) | ✅ |
| Listing a machine's files | ✅ |
| Copying files **to** a machine | ✅ |
| Copying files **from** a machine | ✅ saved as `.DSB` — the machine keeps every design in its own form (a DST-style header with Barudan stitch records), whatever was sent |
| Deleting files on a machine | ❌ disabled in the machines' firmware ([PROTOCOL.md §6](PROTOCOL.md)); clear files at the touchscreen |

Tested against the `1201plus` and `1501ultra` models.

### If no machines are found
When the network scan gets no reply, the app tries TCP port 6000 (the machines' file port) on every
address of the computer's networks and keeps the ones that answer like a machine. That gets through a
firewall that drops the scan's replies; it takes a few seconds. If that finds nothing either:
Click **Save diagnostics…** (bottom right) and send the file it saves. It holds the app's version, this
computer's network addresses, where the scan was sent and what came back, and on Windows the network
type (Public / Private), the firewall's state and any firewall rule for this app — nothing is sent
anywhere by the app itself. The usual causes: the computer isn't on the machines' network (e.g. a remote
PC reached over Remote Desktop), or a firewall blocks the machines' replies. The vendor's app can stay
open: this one uses its own port for the replies.

## Download
From the [Releases](../../releases) page:
- **Windows:** `OpenDecoDesk-Machine-Files-Windows.exe` — one portable file, no install. Windows Firewall
  will ask to allow it on private networks the first time it scans; allow it.
- **macOS (Apple Silicon):** unzip `OpenDecoDesk-Machine-Files-macOS-AppleSilicon.zip`. The app isn't
  signed yet, so the first time, right-click it → Open. macOS then asks to let it find devices on your
  local network; allow it. On an Intel Mac, run it from source (below).

## Run from source
Python 3.9 or newer with Tk (standard library only — nothing to `pip install`):
```bash
python3 -m machine_files
```
On macOS with Homebrew's Python, Tk is a separate package (`brew install python-tk`); the
python.org installer and `/usr/bin/python3` include it.

## Tests
```bash
python3 -m unittest discover -s tests -t . -v
```
The protocol tests run against stub machines on loopback that speak the real frame shapes
(`tests/stubs.py`); the window tests drive the real window with a fake machine and are skipped
where Tk or a display isn't available.

## Making a release
Push a tag like `v0.6.0` (and set the same version in `machine_files/__init__.py` and the spec file). CI
runs the tests, builds both apps and publishes them as a release.

## Build the apps yourself
```bash
python3 -m pip install pyinstaller
pyinstaller --noconfirm opendecodesk-machine-files.spec
```

## How it talks to the machines
Discovery is a UDP broadcast to port 23; everything else is framed TCP on port 6000
(`FA 01 <len> <body> <xor>`). [PROTOCOL.md](PROTOCOL.md) has the full, reverse-engineered
reference; `machine_files/protocol.py` is the implementation.

SmartStitch is the machine vendor's brand; this project isn't affiliated with or endorsed by them.

## Licence
[GNU AGPL-3.0](LICENSE).
