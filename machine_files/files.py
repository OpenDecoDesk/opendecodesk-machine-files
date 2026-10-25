"""Pure file-list logic for the two panes — no Tk, no network."""

import os
import re

DESIGN_EXTS = (".dst", ".dsb")


def list_design_files(folder):
    """The .dst / .dsb files directly in `folder` (any case), sorted by name. Raises OSError if
    the folder can't be read."""
    names = []
    for entry in os.scandir(folder):
        if entry.is_file() and os.path.splitext(entry.name)[1].lower() in DESIGN_EXTS:
            names.append(entry.name)
    return sorted(names, key=str.lower)


# Folders that aren't somewhere to keep designs: hidden ones, and Windows' own on every drive.
_SKIP_FOLDERS = {"$recycle.bin", "system volume information", "recovery", "config.msi"}


def list_subfolders(folder):
    """The folders directly inside `folder`, sorted by name — not hidden ones (".git", "$RECYCLE.BIN")
    or Windows' system folders. Raises OSError if the folder can't be read."""
    names = []
    for entry in os.scandir(folder):
        name = entry.name
        if name.startswith((".", "$")) or name.lower() in _SKIP_FOLDERS:
            continue
        try:
            if entry.is_dir():
                names.append(name)
        except OSError:
            continue
    return sorted(names, key=str.lower)


def parent_folder(folder):
    """The folder one level up, or None at the top of a drive."""
    path = os.path.abspath(folder)
    drive, rest = os.path.splitdrive(path)
    if not rest.strip("\\/"):
        return None  # "/", "Z:\\", "\\\\server\\share" — the top
    parent = os.path.dirname(path.rstrip("\\/"))
    if not os.path.splitdrive(parent)[1]:
        parent = drive + os.sep  # dirname("Z:\\Caps") can come back as "Z:", which means "Z:'s current folder"
    return parent


def folder_signature(folder):
    """What the folder's design files and subfolders look like right now — each file's name, size and
    modified time, each subfolder's name — so a change (a file added, removed, replaced or still being
    written; a folder made or removed) shows as a different value. None if the folder can't be read."""
    try:
        sig = []
        for entry in os.scandir(folder):
            if os.path.splitext(entry.name)[1].lower() in DESIGN_EXTS and entry.is_file():
                st = entry.stat()
                sig.append((entry.name, st.st_size, st.st_mtime_ns))
        sig += [("/", name, 0) for name in list_subfolders(folder)]
        return frozenset(sig)
    except OSError:
        return None


def base_key(name):
    """A file's identity for "already there?" checks. The machine stores an upload of LOGO.DST as
    LOGO.<NNN> (its own sequence number), so both sides drop one trailing ".<ext-or-number>" and
    compare case-insensitively."""
    name = (name or "").strip()
    stem = re.sub(r"\.[^.]+$", "", name)
    return (stem or name).lower()


def machine_slot_and_name(stored_name, position):
    """("LOGO.012", 0) -> (12, "LOGO"). A name without a numeric suffix keeps its whole name and
    takes its 1-based place in the listing as the slot."""
    m = re.match(r"^(.*)\.(\d+)$", stored_name or "")
    if m and m.group(1):
        return int(m.group(2)), m.group(1)
    return position + 1, stored_name or ""


def machine_rows(files):
    """The machine listing as [{slot, name, stored}] sorted by slot."""
    rows = []
    for i, f in enumerate(files):
        slot, name = machine_slot_and_name(f.get("name", ""), i)
        rows.append({"slot": slot, "name": name, "stored": f.get("name", "")})
    return sorted(rows, key=lambda r: (r["slot"], r["name"].lower()))


def newest_stored_for(sent_name, machine_files):
    """The machine's name for a file just sent: same base name, highest sequence number (the machine
    numbers uploads in order). None if it isn't there."""
    key = base_key(sent_name)
    best, best_slot = None, -1
    for i, f in enumerate(machine_files):
        stored = f.get("name", "")
        if base_key(stored) == key:
            slot = machine_slot_and_name(stored, i)[0]
            if slot > best_slot:
                best, best_slot = stored, slot
    return best


def split_by_presence(candidates, present, key=base_key):
    """(to_copy, skipped): `candidates` whose key isn't among `present`'s keys, and those that
    are. Two candidates with the same key are copied once."""
    have = {key(p) for p in present}
    to_copy, skipped = [], []
    for c in candidates:
        k = key(c)
        if k in have:
            skipped.append(c)
        else:
            have.add(k)
            to_copy.append(c)
    return to_copy, skipped


def missing_on_machine(local_names, machine_files):
    """Local file names not already on the machine -> (to_send, skipped)."""
    return split_by_presence(local_names, [f.get("name", "") for f in machine_files])


def missing_in_folder(machine_stored_names, local_names, ext="DSB"):
    """Machine stored names not already in the folder as the file they'd be saved as (`ext`: .DSB as they
    come, or .DST when converting) -> (to_copy, skipped). LOGO.012 is "already there" only if LOGO.<ext>
    is — a LOGO.DSB doesn't stop a LOGO.DST being made."""
    ext = "." + ext.lstrip(".").lower()
    have = [n for n in local_names if os.path.splitext(n)[1].lower() == ext]

    def key(name):
        # A machine name becomes the stem it's saved under; a local name is its stem.
        if name in have:
            return base_key(name)
        return safe_local_stem(machine_slot_and_name(name, 0)[1] or name).lower()

    return split_by_presence(machine_stored_names, have, key=key)


# Names a file can't take on Windows, whatever its extension.
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
MAX_STEM_LENGTH = 120


def safe_local_stem(name):
    """A file name taken from the MACHINE (anything on the network can answer as one) made safe to
    save under: path separators, drive colons and characters Windows refuses become "_", control
    characters go, leading dots and trailing dots / spaces are dropped (so no "..", no hidden file,
    nothing Windows trims), a reserved Windows name gets a "_" in front, and it's kept short. Never
    empty."""
    stem = re.sub(r'[\x00-\x1f\x7f]', "", str(name or ""))
    stem = re.sub(r'[/\\:*?"<>|]', "_", stem)
    stem = stem.lstrip(". ").rstrip(". ")[:MAX_STEM_LENGTH].rstrip(". ")
    if not stem:
        return "design"
    if stem.split(".")[0].strip().lower() in _WINDOWS_RESERVED:
        stem = "_" + stem
    return stem


def free_local_path(folder, stem, ext):
    """Where to save `stem.ext` in `folder`: None if a file by that name is already there (never
    overwrite). The stem is made safe first (safe_local_stem), and the result is checked to sit
    directly in `folder` — a name from the machine can never write anywhere else."""
    ext = re.sub(r"[^A-Za-z0-9]", "", str(ext or "")) or "DSB"
    name = f"{safe_local_stem(stem)}.{ext}"
    path = os.path.join(folder, name)
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(folder) or os.path.basename(path) != name:
        raise ValueError(f"Refusing to save “{stem}” outside the chosen folder.")
    return None if os.path.lexists(path) else path
