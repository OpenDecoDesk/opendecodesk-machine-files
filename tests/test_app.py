"""The window, driven with a fake machine. Skipped where Tk or a display isn't available."""

import gc
import os
import tempfile
import time
import unittest
from unittest import mock

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAVE_TK = True
except Exception:  # no Tk in this Python, or no display
    HAVE_TK = False

from machine_files import protocol

MACHINE_A = {"ip": "10.0.0.5", "mac": "AA:AA:AA:AA:AA:01", "model": "1201plus", "firmware": "JD1"}
MACHINE_B = {"ip": "10.0.0.9", "mac": "AA:AA:AA:AA:AA:02", "model": "1501ultra", "firmware": "HH1"}


class FakeMachines:
    def __init__(self, found):
        self.found = found
        self.files = {m["ip"]: [] for m in found}
        self.sent = []
        self.stored = {}

    def discover_machines(self):
        return list(self.found)

    def find_machines(self, progress=None):
        return self.discover_machines()

    def list_machine_dir(self, ip):
        return list(self.files.setdefault(ip, []))

    def send_design_to_machine(self, ip, name, data):
        self.sent.append((ip, name, data))
        listing = self.files.setdefault(ip, [])
        stored = f"{name.rsplit('.', 1)[0]}.{len(listing) + 1:03d}"
        listing.append({"name": stored, "size": len(data)})
        self.stored[stored] = self.convert(data, name)

    stored_reads = 0

    def convert(self, data, name):
        """What the machine keeps: a real design comes back re-encoded as .DSB; anything else as-is."""
        from tests.designs import SAMPLE, make_dsb
        return make_dsb(SAMPLE, machine_style=True) if data.startswith(b"LA:") else data

    def download_file(self, ip, stored):
        self.stored_reads += 1
        if stored in self.stored:
            return {"bytes": self.stored[stored], "ext": "DSB"}
        if stored.startswith("BROKEN"):
            raise RuntimeError(f"The machine refused to send “{stored}”.")
        return {"bytes": b"LA:" + stored.encode(), "ext": "DSB"}


@unittest.skipUnless(HAVE_TK, "Tk or a display isn't available")
class AppTests(unittest.TestCase):
    def setUp(self):
        from machine_files import app as app_mod
        self.app_mod = app_mod
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self.tmp.name, "designs")
        os.mkdir(self.folder)
        for n in ("LOGO.DST", "CAP.dsb", "readme.txt"):
            with open(os.path.join(self.folder, n), "wb") as fh:
                fh.write(b"x" * 1500)
        self.settings_file = os.path.join(self.tmp.name, "settings.json")
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.close_window()
        self.tmp.cleanup()

    def close_window(self):
        """Close the window, then free what's left of it here on the Tk thread: left for later, the garbage
        collector can free Tk objects on a worker thread, which aborts the process (Tcl_AsyncDelete)."""
        self.root.destroy()
        gc.collect()

    def make(self, fake, **kw):
        app = self.app_mod.App(self.root, settings_file=self.settings_file, proto=fake, **kw)
        self.settle(app)
        return app

    def settle(self, app, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            self.root.update()
            if not app.busy and app._events.empty():
                self.root.update()
                if not app.busy:
                    return
            time.sleep(0.02)
        self.fail("the window stayed busy")

    def enabled(self, btn):
        return not btn.instate(["disabled"])

    def test_scan_lists_machines_and_buttons_follow_state(self):
        fake = FakeMachines([MACHINE_A, MACHINE_B])
        fake.files[MACHINE_A["ip"]] = [{"name": "LOGO.004"}, {"name": "OLD.002"}]
        app = self.make(fake)
        self.assertEqual(list(app.machine_combo["values"]),
                         ["1201plus — 10.0.0.5", "1501ultra — 10.0.0.9"])
        self.assertEqual([app.machine_tree.item(i)["values"] for i in app.machine_tree.get_children()],
                         [[2, "OLD"], [4, "LOGO"]])
        # No folder yet: nothing can come from the machine; nothing in the folder to send.
        self.assertFalse(self.enabled(app.btn_all_from))
        self.assertFalse(self.enabled(app.btn_all_to))

        app.folder_var.set(self.folder)
        app.load_folder()
        self.assertEqual(app.local_files, ["CAP.dsb", "LOGO.DST"])
        self.assertTrue(self.enabled(app.btn_all_from))
        self.assertTrue(self.enabled(app.btn_all_to))
        self.assertFalse(self.enabled(app.btn_sel_to))
        self.assertFalse(self.enabled(app.btn_sel_from))
        app.local_tree.selection_set("0")
        self.root.update()
        self.assertTrue(self.enabled(app.btn_sel_to))

    def names_shown(self, app):
        """The file rows of the left list (folder rows are "dir:<name>")."""
        return [app.local_tree.item(i)["text"] for i in app.local_tree.get_children() if not i.startswith("dir:")]

    def folders_shown(self, app):
        return [app.local_tree.item(i)["text"] for i in app.local_tree.get_children() if i.startswith("dir:")]

    def test_folders_listed_first_and_opened_by_double_click_or_enter(self):
        os.makedirs(os.path.join(self.folder, "Caps", "Winter"))
        os.mkdir(os.path.join(self.folder, "shirts"))
        with open(os.path.join(self.folder, "Caps", "CAP FRONT.DST"), "wb") as fh:
            fh.write(b"x")
        app = self.make(FakeMachines([MACHINE_A]), watch_ms=0)
        app.folder_var.set(self.folder)
        app.load_folder()
        kids = app.local_tree.get_children()
        self.assertEqual(list(kids[:2]), ["dir:Caps", "dir:shirts"], "folders first, sorted")
        self.assertEqual(self.names_shown(app), ["CAP.dsb", "LOGO.DST"])
        self.assertTrue(app.local_tree.item("dir:Caps")["image"], "folders have an icon")
        self.assertIn("2 folder(s), 2 design file(s)", app.status_var.get())
        # Selecting a folder isn't a file to send.
        app.local_tree.selection_set(["dir:Caps"])
        self.root.update()
        self.assertTrue(app.btn_sel_to.instate(["disabled"]))
        # Enter on the focused folder row opens it.
        app.local_tree.focus("dir:Caps")
        app._open_folder_row(None)
        caps = os.path.join(self.folder, "Caps")
        self.assertEqual(app.folder_var.get(), caps)
        self.assertEqual(self.folders_shown(app), ["Winter"])
        self.assertEqual(self.names_shown(app), ["CAP FRONT.DST"])
        from machine_files import settings as st
        self.assertEqual(st.load(self.settings_file)["lastFolder"], caps, "remembered")
        # A double-click on a row opens it.
        self.root.update()
        bbox = app.local_tree.bbox("dir:Winter")
        if bbox:  # only when drawn (not in every headless run)
            class Ev:
                keysym, y = "", bbox[1] + 2
            app._open_folder_row(Ev())
            self.assertEqual(app.folder_var.get(), os.path.join(caps, "Winter"))
            app.folder_up()

    def test_up_a_level(self):
        sub = os.path.join(self.folder, "Caps")
        os.mkdir(sub)
        app = self.make(FakeMachines([MACHINE_A]), watch_ms=0)
        app.folder_var.set(sub)
        app.load_folder()
        self.assertFalse(app.folder_up_btn.instate(["disabled"]))
        app.folder_up_btn.invoke()
        self.assertEqual(app.folder_var.get(), self.folder)
        self.assertIn("LOGO.DST", self.names_shown(app))
        self.assertEqual(self.folders_shown(app), ["Caps"])
        app.folder_var.set(os.path.abspath(os.sep))
        app.load_folder()
        self.assertTrue(app.folder_up_btn.instate(["disabled"]), "nothing above the top of the drive")

    def test_watcher_shows_a_new_folder(self):
        app = self.make(FakeMachines([MACHINE_A]), watch_ms=0)
        app.folder_var.set(self.folder)
        app.load_folder()
        os.mkdir(os.path.join(self.folder, "New jobs"))
        app._watch_folder()
        self.assertEqual(self.folders_shown(app), ["New jobs"])

    def test_folder_watcher_picks_up_new_and_removed_files(self):
        app = self.make(FakeMachines([MACHINE_A]), watch_ms=0)  # watcher driven by hand below
        app.folder_var.set(self.folder)
        app.load_folder()
        self.assertEqual(self.names_shown(app), ["CAP.dsb", "LOGO.DST"])
        app.local_tree.selection_set("1")  # LOGO.DST
        with open(os.path.join(self.folder, "BADGE.DST"), "wb") as fh:
            fh.write(b"y" * 1500)
        open(os.path.join(self.folder, "ignored.txt"), "w").close()
        app._watch_folder()
        self.root.update()
        self.assertEqual(self.names_shown(app), ["BADGE.DST", "CAP.dsb", "LOGO.DST"])
        self.assertEqual(app.local_files, ["BADGE.DST", "CAP.dsb", "LOGO.DST"])
        self.assertEqual([app.local_files[int(i)] for i in app.local_tree.selection()], ["LOGO.DST"],
                         "the selection follows the file, not the row number")
        self.assertIn("new file", app.status_var.get())
        os.remove(os.path.join(self.folder, "CAP.dsb"))
        app._watch_folder()
        self.assertEqual(self.names_shown(app), ["BADGE.DST", "LOGO.DST"])
        self.assertIn("removed", app.status_var.get())
        app.status("unchanged")
        app._watch_folder()
        self.assertEqual(app.status_var.get(), "unchanged", "no news when nothing changed")

    def test_watcher_follows_the_folder_shown_not_a_half_typed_path(self):
        app = self.make(FakeMachines([MACHINE_A]), watch_ms=0)
        app.folder_var.set(self.folder)
        app.load_folder()
        app.folder_var.set(self.tmp.name)  # typing a different path, not confirmed yet
        with open(os.path.join(self.folder, "NEW.DST"), "wb") as fh:
            fh.write(b"z")
        app._watch_folder()
        self.assertIn("NEW.DST", app.local_files)

    def test_refresh_button_reads_the_folder_again(self):
        app = self.make(FakeMachines([MACHINE_A]), watch_ms=0)
        app.folder_var.set(self.folder)
        app.load_folder()
        with open(os.path.join(self.folder, "LATE.DSB"), "wb") as fh:
            fh.write(b"z")
        app.folder_refresh_btn.invoke()
        self.assertIn("LATE.DSB", self.names_shown(app))
        self.assertIn("Refreshed", app.status_var.get())
        grid = app.folder_refresh_btn.grid_info()
        self.assertEqual((int(grid["row"]), int(grid["column"])), (0, 2), "to the right of the folder button")

    def test_watcher_runs_on_its_own(self):
        app = self.make(FakeMachines([MACHINE_A]), watch_ms=50)
        app.folder_var.set(self.folder)
        app.load_folder()
        with open(os.path.join(self.folder, "AUTO.DST"), "wb") as fh:
            fh.write(b"z")
        end = time.time() + 3
        while time.time() < end and "AUTO.DST" not in app.local_files:
            self.root.update()
            time.sleep(0.02)
        self.assertIn("AUTO.DST", app.local_files)

    def test_save_diagnostics_writes_version_state_and_log(self):
        from machine_files import __version__
        app = self.make(FakeMachines([MACHINE_A]), watch_ms=0)
        app.folder_var.set(self.folder)
        app.load_folder()
        self.assertTrue(any(w.cget("text") == f"v{__version__}" for w in app.diag_btn.master.winfo_children()
                            if w.winfo_class() == "TLabel"), "the version shows beside the button")
        out = os.path.join(self.tmp.name, "diag.txt")
        with mock.patch.object(self.app_mod.messagebox, "showinfo") as info:
            app.save_diagnostics(out)
            self.settle(app, timeout=60)  # the report runs a few system commands; CI machines can be slow
        with open(out, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(f"OpenDecoDesk Machine Files {__version__}", text)
        self.assertIn("1201plus — 10.0.0.5", text)
        self.assertIn(f"Folder shown: {self.folder} — 2 design file(s)", text)
        self.assertIn("===== Log", text)
        self.assertIn("Diagnostics saved", app.status_var.get())
        self.assertTrue(info.called)

    def test_machines_found_by_the_sweep_say_so(self):
        fake = FakeMachines([])
        fake.find_machines = lambda progress=None: [{**MACHINE_A, "foundBy": "port 6000"}]
        app = self.make(fake, watch_ms=0)
        self.assertEqual(list(app.machine_combo["values"]), ["1201plus — 10.0.0.5"])
        self.assertIn("firewall", app.status_var.get())

    def test_verify_after_copy_to_machine(self):
        from tests.designs import SAMPLE, make_dst
        with open(os.path.join(self.folder, "REAL.DST"), "wb") as fh:
            fh.write(make_dst(SAMPLE, label="REAL"))
        fake = FakeMachines([MACHINE_A])
        fake.files[MACHINE_A["ip"]] = [{"name": "LOGO.001"}, {"name": "CAP.002"}]
        app = self.make(fake, watch_ms=0)
        self.assertTrue(app.verify_var.get(), "on by default")
        app.folder_var.set(self.folder)
        app.load_folder()
        app.local_tree.selection_set([str(app.local_files.index("REAL.DST"))])
        with mock.patch.object(self.app_mod.messagebox, "askyesno", return_value=True), \
                mock.patch.object(self.app_mod.messagebox, "showwarning") as warn:
            app.copy_selected_to_machine()
            self.settle(app)
        self.assertFalse(warn.called)
        self.assertEqual(fake.stored_reads, 1, "the file was read back once")
        self.assertIn("Verified: each came back with the same stitches", app.status_var.get())

    def test_verify_reports_a_file_that_came_back_different(self):
        from tests.designs import SAMPLE, make_dsb, make_dst
        with open(os.path.join(self.folder, "REAL.DST"), "wb") as fh:
            fh.write(make_dst(SAMPLE))
        fake = FakeMachines([MACHINE_A])
        broken = list(SAMPLE)
        broken[5] = ("s", 99, 5)
        fake.convert = lambda data, name: make_dsb(broken, machine_style=True)
        app = self.make(fake, watch_ms=0)
        app.folder_var.set(self.folder)
        app.load_folder()
        app.local_tree.selection_set([str(app.local_files.index("REAL.DST"))])
        with mock.patch.object(self.app_mod.messagebox, "askyesno", return_value=True), \
                mock.patch.object(self.app_mod.messagebox, "showwarning") as warn:
            app.copy_selected_to_machine()
            self.settle(app)
        self.assertTrue(warn.called)
        self.assertIn("REAL.DST: Different", warn.call_args[0][1])
        self.assertIn("first difference at stitch 6", warn.call_args[0][1])
        self.assertIn("didn't match", app.status_var.get())

    def test_verify_off_reads_nothing_back_and_is_remembered(self):
        fake = FakeMachines([MACHINE_A])
        app = self.make(fake, watch_ms=0)
        app.verify_chk.invoke()
        self.assertFalse(app.verify_var.get())
        app.folder_var.set(self.folder)
        app.load_folder()
        with mock.patch.object(self.app_mod.messagebox, "askyesno", return_value=True), \
                mock.patch.object(self.app_mod.messagebox, "showwarning"):
            app.copy_all_to_machine()
            self.settle(app)
        self.assertEqual(fake.stored_reads, 0)
        self.assertNotIn("Verified", app.status_var.get())
        from machine_files import settings as st
        self.assertFalse(st.load(self.settings_file)["verifyAfterCopy"])

    def test_a_dsb_in_the_folder_doesnt_stop_saving_a_dst(self):
        from tests.designs import SAMPLE, make_dsb
        with open(os.path.join(self.folder, "CREST.DSB"), "wb") as fh:
            fh.write(make_dsb(SAMPLE))
        fake = FakeMachines([MACHINE_A])
        fake.files[MACHINE_A["ip"]] = [{"name": "CREST.007"}]
        fake.stored["CREST.007"] = make_dsb(SAMPLE, machine_style=True)
        app = self.make(fake, watch_ms=0)
        app.folder_var.set(self.folder)
        app.load_folder()
        app.copy_all_from_machine()  # Save as .DST off: CREST.DSB is already here
        self.settle(app)
        self.assertIn("Nothing to copy — 1 file(s) already in the folder", app.status_var.get())
        app.dst_chk.invoke()          # on: a .DST is a different file
        app.copy_all_from_machine()
        self.settle(app)
        self.assertIn("CREST.DST", os.listdir(self.folder))
        self.assertIn("CREST.DSB", os.listdir(self.folder))
        app.copy_all_from_machine()   # and now that the .DST is here too, nothing more to do
        self.settle(app)
        self.assertIn("Nothing to copy", app.status_var.get())

    def test_save_as_dst_from_the_machine(self):
        from machine_files import settings as st
        from machine_files import stitches
        from tests.designs import SAMPLE, make_dsb, make_dst
        fake = FakeMachines([MACHINE_A])
        fake.files[MACHINE_A["ip"]] = [{"name": "CREST.007"}, {"name": "JUNK.008"}]
        fake.stored["CREST.007"] = make_dsb(SAMPLE, machine_style=True)  # JUNK isn't a design
        app = self.make(fake, watch_ms=0)
        self.assertFalse(app.dst_var.get(), "off by default")
        app.dst_chk.invoke()
        self.assertTrue(st.load(self.settings_file)["saveAsDst"], "remembered")
        app.folder_var.set(self.folder)
        app.load_folder()
        with mock.patch.object(self.app_mod.messagebox, "showwarning") as warn:
            app.copy_all_from_machine()
            self.settle(app)
        saved = sorted(os.listdir(self.folder))
        self.assertIn("CREST.DST", saved)
        self.assertIn("JUNK.DSB", saved, "a file that can't be converted is kept as it came")
        with open(os.path.join(self.folder, "CREST.DST"), "rb") as fh:
            dst = fh.read()
        self.assertTrue(stitches.compare(make_dst(SAMPLE), "orig.dst", dst, "saved.dst")["match"])
        self.assertIn("JUNK.008", warn.call_args_list[0][0][1])
        self.assertIn("as .DST", app.status_var.get())

    def test_verify_after_copy_from_machine_reads_twice(self):
        fake = FakeMachines([MACHINE_A])
        fake.files[MACHINE_A["ip"]] = [{"name": "NEW DESIGN.003"}]
        app = self.make(fake, watch_ms=0)
        app.folder_var.set(self.folder)
        app.load_folder()
        reads = []
        real = fake.download_file
        fake.download_file = lambda ip, stored: reads.append(stored) or real(ip, stored)
        app.copy_all_from_machine()
        self.settle(app)
        self.assertEqual(reads, ["NEW DESIGN.003", "NEW DESIGN.003"])
        self.assertIn("Verified: each matches the machine's copy exactly", app.status_var.get())

    def test_copy_all_to_machine_skips_what_is_there(self):
        fake = FakeMachines([MACHINE_A])
        fake.files[MACHINE_A["ip"]] = [{"name": "LOGO.001"}]
        app = self.make(fake)
        app.folder_var.set(self.folder)
        app.load_folder()
        with mock.patch.object(self.app_mod.messagebox, "askyesno", return_value=True) as ask, \
                mock.patch.object(self.app_mod.messagebox, "showwarning"), \
                mock.patch.object(self.app_mod.messagebox, "showerror"):
            app.copy_all_to_machine()
            self.settle(app)
        self.assertIn("1 already on the machine", ask.call_args[0][1])
        self.assertEqual([s[1] for s in fake.sent], ["CAP.dsb"])
        self.assertEqual(len(app.machine_rows), 2)  # re-read after sending
        self.assertIn("Copied 1 file", app.status_var.get())

    def test_copy_all_from_machine_saves_dsb_and_skips_what_is_there(self):
        # The folder has LOGO.DST and CAP.dsb: CAP is already here as a .DSB and is skipped; LOGO isn't
        # (only as .DST), so LOGO.DSB is saved beside it.
        fake = FakeMachines([MACHINE_A])
        fake.files[MACHINE_A["ip"]] = [{"name": "LOGO.001"}, {"name": "CAP.002"}, {"name": "NEW DESIGN.003"},
                                        {"name": "马头机~1.008"}]
        app = self.make(fake)
        app.folder_var.set(self.folder)
        app.load_folder()
        app.copy_all_from_machine()
        self.settle(app)
        self.assertEqual(sorted(os.listdir(self.folder)),
                         ["CAP.dsb", "LOGO.DSB", "LOGO.DST", "NEW DESIGN.DSB", "readme.txt", "马头机~1.DSB"])
        with open(os.path.join(self.folder, "NEW DESIGN.DSB"), "rb") as fh:
            self.assertEqual(fh.read(), b"LA:NEW DESIGN.003")
        self.assertIn("Copied 3 file", app.status_var.get())
        self.assertIn("1 already there", app.status_var.get())
        self.assertIn("NEW DESIGN.DSB", app.local_files)  # the folder list was re-read

    def test_hostile_names_from_a_machine_stay_in_the_folder(self):
        fake = FakeMachines([MACHINE_A])
        fake.files[MACHINE_A["ip"]] = [{"name": "../../escaped.003"}, {"name": "/tmp/absolute.004"},
                                        {"name": "CON.005"}]
        app = self.make(fake)
        app.folder_var.set(self.folder)
        app.load_folder()
        app.copy_all_from_machine()
        self.settle(app)
        self.assertEqual(sorted(os.listdir(self.folder)),
                         ["CAP.dsb", "LOGO.DST", "_.._escaped.DSB", "_CON.DSB", "_tmp_absolute.DSB", "readme.txt"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "escaped.DSB")))
        self.assertIn("Copied 3 file", app.status_var.get())

    def test_copy_from_machine_reports_a_refusal(self):
        fake = FakeMachines([MACHINE_A])
        fake.files[MACHINE_A["ip"]] = [{"name": "BROKEN.004"}]
        app = self.make(fake)
        app.folder_var.set(self.folder)
        app.load_folder()
        with mock.patch.object(self.app_mod.messagebox, "showerror") as err:
            app.copy_all_from_machine()
            self.settle(app)
        self.assertIn("refused to send", err.call_args[0][1])
        self.assertEqual(sorted(os.listdir(self.folder)), ["CAP.dsb", "LOGO.DST", "readme.txt"])

    def test_remembers_folder_and_machine_by_mac(self):
        fake = FakeMachines([MACHINE_A, MACHINE_B])
        app = self.make(fake)
        app.folder_var.set(self.folder)
        app.load_folder()
        app.machine_combo.current(1)
        app._machine_picked()
        self.settle(app)
        self.close_window()

        # Next run: machine B has a new address; it's still the one picked.
        self.root = tk.Tk()
        self.root.withdraw()
        moved_b = {**MACHINE_B, "ip": "10.0.0.77"}
        app2 = self.make(FakeMachines([MACHINE_A, moved_b]))
        self.assertEqual(app2.folder_var.get(), self.folder)
        self.assertEqual(app2.machine["ip"], "10.0.0.77")

        # A run where no machine answers still offers the last one at its last address.
        self.close_window()
        self.root = tk.Tk()
        self.root.withdraw()
        app3 = self.make(FakeMachines([]))
        self.assertEqual(list(app3.machine_combo["values"]), ["1501ultra — 10.0.0.77 (last seen)"])
        self.assertEqual(app3.machine["mac"], MACHINE_B["mac"])


if __name__ == "__main__":
    unittest.main()
