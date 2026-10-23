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


def missing_in_folder(machine_stored_names, local_names):
    """Machine stored names not already in the folder -> (to_copy, skipped)."""
    return split_by_presence(machine_stored_names, local_names)


def free_local_path(folder, stem, ext):
    """`folder/stem.ext`, or None if a file by that name is already there (never overwrite)."""
    path = os.path.join(folder, f"{stem}.{ext.lstrip('.')}")
    return None if os.path.exists(path) else path
