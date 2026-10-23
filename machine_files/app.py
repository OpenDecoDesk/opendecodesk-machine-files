"""The window: a folder on the left, a machine on the right, four copy buttons between them."""

import datetime
import logging
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from . import diagnostics
from . import files as fl
from . import protocol
from . import settings as st

log = logging.getLogger("machine_files.app")

APP_TITLE = "OpenDecoDesk Machine Files"
WATCH_MS = 2000  # how often the folder is checked for new, changed or removed files


class App:
    def __init__(self, root, settings_file=None, scan_on_start=True, proto=protocol, watch_ms=WATCH_MS):
        self.root = root
        self.proto = proto
        self.settings_file = settings_file
        self.settings = st.load(settings_file)
        self.local_files = []       # names in the folder
        self.machine_files = []     # the machine's raw listing [{name, size, modified}]
        self.machine_rows = []      # [{slot, name, stored}] as shown
        self.machines = []          # [{ip, mac, model, firmware, lastSeen?}]
        self.machine = None         # the one picked
        self.busy = False
        self._events = queue.Queue()
        self._folder_sig = None     # folder_signature() of what the list shows
        self._shown_folder = ""     # the folder the list shows (not what's half-typed in the box)
        self._watch_ms = watch_ms

        root.title(APP_TITLE)
        root.minsize(760, 420)
        self._build()
        self._poll_id = root.after(60, self._poll)
        self._watch_id = root.after(self._watch_ms, self._watch_folder) if self._watch_ms else None
        root.bind("<Destroy>", self._on_destroy, add="+")

        folder = self.settings.get("lastFolder") or ""
        if folder and os.path.isdir(folder):
            self.folder_var.set(folder)
            self.load_folder()
        self._update_buttons()
        if scan_on_start:
            self.scan()
        elif self.settings.get("lastMachine"):
            self._set_machines([], remembered=self.settings["lastMachine"])

    # ---- layout --------------------------------------------------------------------------
    def _build(self):
        outer = ttk.Frame(self.root, padding=10)
        outer.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(2, weight=1)
        outer.rowconfigure(1, weight=1)

        # Left: folder path + folder button, then the folder's .dst / .dsb files.
        top_left = ttk.Frame(outer)
        top_left.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        top_left.columnconfigure(0, weight=1)
        self.folder_var = tk.StringVar()
        self.folder_entry = ttk.Entry(top_left, textvariable=self.folder_var)
        self.folder_entry.grid(row=0, column=0, sticky="ew")
        self.folder_entry.bind("<Return>", lambda e: self.load_folder())
        # A drawn icon, not an emoji: older Tk (8.5) hangs drawing characters outside the BMP.
        self._folder_img = _folder_icon(self.root)
        self.folder_btn = ttk.Button(top_left, image=self._folder_img, command=self.pick_folder)
        self.folder_btn.grid(row=0, column=1, padx=(6, 0))
        self.folder_refresh_btn = ttk.Button(top_left, text="↻", width=3, command=self.refresh_folder)
        self.folder_refresh_btn.grid(row=0, column=2, padx=(4, 0))

        self.local_tree = self._tree(outer, [("name", "Name", 260, "w")])
        self.local_tree.master.grid(row=1, column=0, sticky="nsew")

        # Middle: the four copy buttons, stacked.
        mid = ttk.Frame(outer, padding=(10, 0))
        mid.grid(row=1, column=1, sticky="ns")
        mid.rowconfigure(0, weight=1)
        mid.rowconfigure(5, weight=1)
        self.btn_all_from = ttk.Button(mid, text="◀◀  Copy all", command=self.copy_all_from_machine)
        self.btn_sel_from = ttk.Button(mid, text="◀  Copy selected", command=self.copy_selected_from_machine)
        self.btn_sel_to = ttk.Button(mid, text="Copy selected  ▶", command=self.copy_selected_to_machine)
        self.btn_all_to = ttk.Button(mid, text="Copy all  ▶▶", command=self.copy_all_to_machine)
        for i, b in enumerate((self.btn_all_from, self.btn_sel_from, self.btn_sel_to, self.btn_all_to)):
            b.grid(row=i + 1, column=0, sticky="ew", pady=4)

        # Right: machine picker + rescan, then the machine's files (slot, name).
        top_right = ttk.Frame(outer)
        top_right.grid(row=0, column=2, sticky="ew", pady=(0, 6))
        top_right.columnconfigure(0, weight=1)
        self.machine_var = tk.StringVar()
        self.machine_combo = ttk.Combobox(top_right, textvariable=self.machine_var, state="readonly")
        self.machine_combo.grid(row=0, column=0, sticky="ew")
        self.machine_combo.bind("<<ComboboxSelected>>", lambda e: self._machine_picked())
        self.rescan_btn = ttk.Button(top_right, text="↻", width=3, command=self.scan)
        self.rescan_btn.grid(row=0, column=1, padx=(6, 0))

        self.machine_tree = self._tree(outer, [("slot", "Slot", 60, "e"), ("name", "Name", 220, "w")])
        self.machine_tree.master.grid(row=1, column=2, sticky="nsew")

        self.status_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.status_var, anchor="w").grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        foot = ttk.Frame(outer)
        foot.grid(row=2, column=2, sticky="e", pady=(8, 0))
        ttk.Label(foot, text=f"v{__version__}", foreground="#777").grid(row=0, column=0, padx=(0, 8))
        self.diag_btn = ttk.Button(foot, text="Save diagnostics…", command=self.save_diagnostics)
        self.diag_btn.grid(row=0, column=1)

        for tree in (self.local_tree, self.machine_tree):
            tree.bind("<<TreeviewSelect>>", lambda e: self._update_buttons())

    def _tree(self, parent, columns):
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings",
                            selectmode="extended")
        for key, title, width, anchor in columns:
            tree.heading(key, text=title, anchor=anchor)
            tree.column(key, width=width, anchor=anchor, stretch=(key == "name"))
        bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
        return tree

    # ---- background work -----------------------------------------------------------------
    def _run(self, work, done):
        """Run work(progress) on a thread; done(result, error) runs back on the Tk thread."""
        self.busy = True
        self._update_buttons()

        def target():
            try:
                result, error = work(lambda msg: self._events.put(("status", msg))), None
            except Exception as e:  # noqa: BLE001 — every failure is shown to the operator
                result, error = None, e
            self._events.put(("done", done, result, error))

        threading.Thread(target=target, daemon=True).start()

    def _poll(self):
        try:
            while True:
                ev = self._events.get_nowait()
                if ev[0] == "status":
                    self.status(ev[1])
                else:
                    _, done, result, error = ev
                    self.busy = False
                    done(result, error)
                    self._update_buttons()
        except queue.Empty:
            pass
        self._poll_id = self.root.after(60, self._poll)

    def _on_destroy(self, event):
        if event.widget is not self.root:
            return
        for attr in ("_poll_id", "_watch_id"):
            if getattr(self, attr):
                self.root.after_cancel(getattr(self, attr))
                setattr(self, attr, None)

    def status(self, msg):
        self.status_var.set(msg)

    # ---- diagnostics -----------------------------------------------------------------------
    def _state_report(self):
        """What the window is showing, for the diagnostics file."""
        machines = "\n".join(f"  {self._machine_label(m)}  MAC {m.get('mac') or '?'}  firmware {m.get('firmware') or '?'}"
                              for m in self.machines) or "  (none)"
        return [("This window", "\n".join([
            f"Folder shown: {self._shown_folder or '(none)'} — {len(self.local_files)} design file(s)",
            f"Machines in the list:\n{machines}",
            f"Machine picked: {self._machine_label(self.machine) if self.machine else '(none)'}",
            f"Files listed on it: {len(self.machine_rows)}",
            f"Status line: {self.status_var.get()}",
        ]))]

    def save_diagnostics(self, path=None):
        """Asks where to save, then writes the version, system / network details and the recent log
        to a text file the user can read and send on. `path` skips the question (tests)."""
        if self.busy:
            return
        if path is None:
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            path = filedialog.asksaveasfilename(
                parent=self.root, title="Save diagnostics", defaultextension=".txt",
                initialdir=desktop if os.path.isdir(desktop) else os.path.expanduser("~"),
                initialfile=f"machine-files-diagnostics-{datetime.datetime.now():%Y%m%d-%H%M}.txt",
                filetypes=[("Text file", "*.txt")])
            if not path:
                return
        log.info("diagnostics: saving to %s", path)
        state = self._state_report()
        self.status("Collecting diagnostics…")

        def work(progress):
            text = diagnostics.build_report(state)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            return path

        def done(saved, error):
            if error:
                self._error("Save diagnostics", error)
            else:
                self.status(f"Diagnostics saved to {saved}")
                messagebox.showinfo(APP_TITLE, f"Saved to:\n{saved}\n\nOpen it to check what's in it, then send it on.",
                                    parent=self.root)

        self._run(work, done)

    def _error(self, title, error):
        log.warning("%s: %s", title, error)
        self.status(str(error))
        messagebox.showerror(title, str(error), parent=self.root)

    # ---- folder side ---------------------------------------------------------------------
    def pick_folder(self):
        start = self.folder_var.get() if os.path.isdir(self.folder_var.get()) else os.path.expanduser("~")
        chosen = filedialog.askdirectory(parent=self.root, initialdir=start, title="Choose the designs folder")
        if chosen:
            self.folder_var.set(chosen)
            self.load_folder()

    def load_folder(self):
        """Show the folder typed or picked (and remember it)."""
        folder = self.folder_var.get().strip()
        if self._read_folder(folder) and folder:
            log.info("folder: %s (%d design files)", folder, len(self.local_files))
            self.settings["lastFolder"] = folder
            self._save_settings()
            self.status(f"{len(self.local_files)} design file(s) in the folder.")

    def refresh_folder(self):
        """The ↻ next to the folder: read it again now."""
        if self._read_folder(self.folder_var.get().strip()):
            self.status(f"Refreshed — {len(self.local_files)} design file(s) in the folder.")

    def _read_folder(self, folder):
        """Lists `folder` into the left pane, keeping the files that were selected selected. False if
        it couldn't be read (the list is then empty)."""
        keep = {self.local_files[int(i)] for i in self.local_tree.selection() if int(i) < len(self.local_files)}
        ok = True
        self.local_files = []
        if folder:
            try:
                self.local_files = fl.list_design_files(folder)
            except OSError as e:
                self.status(f"Can't read that folder: {e}")
                ok = False
        self._shown_folder = folder if ok else ""
        self._folder_sig = fl.folder_signature(folder) if folder and ok else None
        self._fill(self.local_tree, [(n,) for n in self.local_files])
        reselect = [str(i) for i, n in enumerate(self.local_files) if n in keep]
        if reselect:
            self.local_tree.selection_set(reselect)
        self._update_buttons()
        return ok

    def _watch_folder(self):
        """Every WATCH_MS: if the folder's design files changed (a file added, removed or replaced),
        show the new list. Quiet otherwise; doesn't touch the status line while a copy is running."""
        self._watch_id = None
        try:
            folder = self._shown_folder
            if folder and os.path.isdir(folder):
                sig = fl.folder_signature(folder)
                if sig is not None and sig != self._folder_sig:
                    before = len(self.local_files)
                    if self._read_folder(folder) and not self.busy and self._folder_sig is not None:
                        n = len(self.local_files)
                        change = "new file(s)" if n > before else "file(s) removed" if n < before else "files changed"
                        self.status(f"Folder updated ({change}) — {n} design file(s).")
        finally:
            if self._watch_ms:
                self._watch_id = self.root.after(self._watch_ms, self._watch_folder)

    # ---- machine side --------------------------------------------------------------------
    def scan(self):
        if self.busy:
            return
        self.status("Looking for machines on the network…")

        def done(found, error):
            if error:
                log.warning("scan failed: %s", error)
                self.status(f"Machine scan failed: {error}")
                found = []
            self._set_machines(found or [], remembered=self.settings.get("lastMachine"))

        self._run(lambda progress: self.proto.discover_machines(), done)

    def _set_machines(self, found, remembered=None):
        """Fill the picker. The remembered machine is matched by MAC (its IP can change); if it
        wasn't found, it's still offered at its last address so listing can be tried."""
        log.info("machines found: %s; remembered: %s", [f"{m.get('model')} {m.get('ip')}" for m in found],
                 f"{remembered.get('model')} {remembered.get('ip')} {remembered.get('mac')}" if remembered else "none")
        self.machines = sorted(found, key=lambda m: (m.get("model", ""), m.get("ip", "")))
        pick = None
        if remembered:
            pick = next((m for m in self.machines
                         if remembered.get("mac") and m.get("mac") == remembered["mac"]), None)
            if pick is None and not any(m.get("ip") == remembered.get("ip") for m in self.machines):
                self.machines.append({**remembered, "lastSeen": True})
        if pick is None and found:
            pick = self.machines[0]
        if pick is None and self.machines:
            pick = self.machines[0]
        self.machine_combo["values"] = [self._machine_label(m) for m in self.machines]
        if found:
            self.status(f"Found {len(found)} machine(s).")
        elif self.machines:
            self.status("No machines answered the scan — showing the last one used.")
        else:
            self.status("No machines found. Check this computer is on the machines' network, then ↻.")
        if pick is not None:
            self.machine_combo.current(self.machines.index(pick))
            self._machine_picked()
        else:
            self.machine_var.set("")
            self.machine = None
            self._show_machine_files([])

    @staticmethod
    def _machine_label(m):
        label = f"{m.get('model') or 'Machine'} — {m.get('ip')}"
        return label + " (last seen)" if m.get("lastSeen") else label

    def _machine_picked(self):
        log.info("machine picked: %s", self.machine_combo.get())
        i = self.machine_combo.current()
        if i < 0 or i >= len(self.machines):
            return
        self.machine = self.machines[i]
        self.settings["lastMachine"] = {k: self.machine.get(k, "") for k in ("mac", "ip", "model")}
        self._save_settings()
        self.refresh_machine()

    def refresh_machine(self, then=None):
        if not self.machine:
            return
        ip = self.machine["ip"]
        self.status(f"Reading the file list from {ip}…")

        def done(listing, error):
            if error:
                self._show_machine_files([])
                log.warning("listing %s failed: %s", ip, error)
                self.status(f"Couldn't list the machine's files: {error}")
            else:
                self._show_machine_files(listing)
                self.status(f"{len(listing)} file(s) on the machine.")
            if then:
                then(listing if not error else None)

        self._run(lambda progress: self.proto.list_machine_dir(ip), done)

    def _show_machine_files(self, listing):
        self.machine_files = listing
        self.machine_rows = fl.machine_rows(listing)
        self._fill(self.machine_tree, [(r["slot"], r["name"]) for r in self.machine_rows])
        self._update_buttons()

    # ---- the four buttons ----------------------------------------------------------------
    def _update_buttons(self):
        has_folder = bool(self.folder_var.get().strip()) and os.path.isdir(self.folder_var.get().strip())
        idle = not self.busy
        has_machine = self.machine is not None
        rules = (
            (self.btn_all_from, idle and has_folder and bool(self.machine_rows)),
            (self.btn_sel_from, idle and has_folder and bool(self.machine_tree.selection())),
            (self.btn_sel_to, idle and has_machine and bool(self.local_tree.selection())),
            (self.btn_all_to, idle and has_machine and bool(self.local_files)),
        )
        for btn, on in rules:
            btn.state(["!disabled"] if on else ["disabled"])
        self.rescan_btn.state(["!disabled"] if idle else ["disabled"])

    def _selected(self, tree, items):
        return [items[int(iid)] for iid in tree.selection()]

    def copy_all_to_machine(self):
        self._send(list(self.local_files))

    def copy_selected_to_machine(self):
        self._send(self._selected(self.local_tree, self.local_files))

    def copy_all_from_machine(self):
        self._receive([r["stored"] for r in self.machine_rows])

    def copy_selected_from_machine(self):
        self._receive([r["stored"] for r in self._selected(self.machine_tree, self.machine_rows)])

    def _send(self, names):
        if not names or not self.machine:
            return
        to_send, skipped = fl.missing_on_machine(names, self.machine_files)
        if not to_send:
            self.status(f"Nothing to copy — {len(skipped)} file(s) already on the machine.")
            return
        folder = self.folder_var.get().strip()
        ip = self.machine["ip"]
        log.info("copy to machine %s: %d file(s), %d already there", ip, len(to_send), len(skipped))
        note = f"\n\n{len(skipped)} already on the machine will be skipped." if skipped else ""
        if not messagebox.askyesno(APP_TITLE, f"Copy {len(to_send)} file(s) to "
                                   f"{self._machine_label(self.machine)}?{note}", parent=self.root):
            return

        def work(progress):
            sent = []
            for i, name in enumerate(to_send, 1):
                progress(f"Sending {name} to the machine ({i} of {len(to_send)})…")
                with open(os.path.join(folder, name), "rb") as fh:
                    data = fh.read()
                try:
                    self.proto.send_design_to_machine(ip, name, data)
                except Exception as e:
                    raise RuntimeError(f"{e}\n\nSent before this: {len(sent)} of {len(to_send)}.")
                sent.append(name)
            return sent

        def done(sent, error):
            def after_list(listing):
                if error:
                    self._error("Copy to machine", error)
                    return
                there = {fl.base_key(f.get("name", "")) for f in (listing or [])}
                missing = [n for n in sent if fl.base_key(n) not in there] if listing is not None else []
                msg = f"Copied {len(sent)} file(s) to the machine."
                if skipped:
                    msg += f" {len(skipped)} already there, skipped."
                self.status(msg)
                if missing:
                    messagebox.showwarning(APP_TITLE, "Sent, but not showing on the machine:\n"
                                           + "\n".join(missing)
                                           + "\n\nVery small files (one block) may not be saved.",
                                           parent=self.root)
            self.refresh_machine(then=after_list)

        self._run(work, done)

    def _receive(self, stored_names):
        folder = self.folder_var.get().strip()
        if not stored_names or not self.machine or not os.path.isdir(folder):
            return
        to_copy, skipped = fl.missing_in_folder(stored_names, self.local_files)
        if not to_copy:
            self.status(f"Nothing to copy — {len(skipped)} file(s) already in the folder.")
            return
        ip = self.machine["ip"]
        log.info("copy from machine %s: %d file(s), %d already in the folder", ip, len(to_copy), len(skipped))

        def work(progress):
            saved, taken = [], []
            for i, stored in enumerate(to_copy, 1):
                progress(f"Copying {stored} from the machine ({i} of {len(to_copy)})…")
                got = self.proto.download_file(ip, stored)
                # The name comes from the machine — free_local_path makes it safe and keeps it in `folder`.
                stem = fl.machine_slot_and_name(stored, 0)[1] or stored
                path = fl.free_local_path(folder, stem, got.get("ext") or "DSB")
                if path is None:
                    taken.append(stored)  # a file by that name is already there; never overwrite
                    continue
                with open(path, "xb") as fh:
                    fh.write(got["bytes"])
                saved.append(os.path.basename(path))
            return saved, taken

        def done(result, error):
            self.load_folder()
            if error:
                self._error("Copy from machine", error)
                return
            saved, taken = result
            msg = f"Copied {len(saved)} file(s) into the folder."
            if skipped or taken:
                msg += f" {len(skipped) + len(taken)} already there, skipped."
            self.status(msg)

        self._run(work, done)

    # ---- helpers -------------------------------------------------------------------------
    def _fill(self, tree, rows):
        tree.delete(*tree.get_children())
        for i, values in enumerate(rows):
            tree.insert("", "end", iid=str(i), values=values)

    def _save_settings(self):
        st.save(self.settings, self.settings_file)


def _folder_icon(root):
    """An 18×14 folder: a tab on top of a body, in manila with a darker outline."""
    w, h = 18, 14
    img = tk.PhotoImage(master=root, width=w, height=h)
    fill, edge = "#E8B54A", "#9A6B12"
    img.put(edge, to=(0, 0, 7, 3))           # tab outline
    img.put(fill, to=(1, 1, 6, 3))
    img.put(edge, to=(0, 2, w, h))           # body outline
    img.put(fill, to=(1, 3, w - 1, h - 1))
    return img


def main():
    diagnostics.setup()
    root = tk.Tk()
    App(root)
    root.mainloop()
