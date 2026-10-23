"""The window, driven with a fake machine. Skipped where Tk or a display isn't available."""

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
    NotSupportedYet = protocol.NotSupportedYet

    def __init__(self, found):
        self.found = found
        self.files = {m["ip"]: [] for m in found}
        self.sent = []

    def discover_machines(self):
        return list(self.found)

    def list_machine_dir(self, ip):
        return list(self.files.setdefault(ip, []))

    def send_design_to_machine(self, ip, name, data):
        self.sent.append((ip, name, data))
        listing = self.files.setdefault(ip, [])
        listing.append({"name": f"{name.rsplit('.', 1)[0]}.{len(listing) + 1:03d}", "size": len(data)})

    def download_file(self, ip, stored):
        return protocol.download_file(ip, stored)


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
        self.root.destroy()
        self.tmp.cleanup()

    def make(self, fake):
        app = self.app_mod.App(self.root, settings_file=self.settings_file, proto=fake)
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

    def test_copy_from_machine_reports_not_captured(self):
        fake = FakeMachines([MACHINE_A])
        fake.files[MACHINE_A["ip"]] = [{"name": "NEW.003"}]
        app = self.make(fake)
        app.folder_var.set(self.folder)
        app.load_folder()
        with mock.patch.object(self.app_mod.messagebox, "showerror") as err:
            app.copy_all_from_machine()
            self.settle(app)
        self.assertIn("isn't supported yet", err.call_args[0][1])
        self.assertEqual(sorted(os.listdir(self.folder)), ["CAP.dsb", "LOGO.DST", "readme.txt"])

    def test_remembers_folder_and_machine_by_mac(self):
        fake = FakeMachines([MACHINE_A, MACHINE_B])
        app = self.make(fake)
        app.folder_var.set(self.folder)
        app.load_folder()
        app.machine_combo.current(1)
        app._machine_picked()
        self.settle(app)
        self.root.destroy()

        # Next run: machine B has a new address; it's still the one picked.
        self.root = tk.Tk()
        self.root.withdraw()
        moved_b = {**MACHINE_B, "ip": "10.0.0.77"}
        app2 = self.make(FakeMachines([MACHINE_A, moved_b]))
        self.assertEqual(app2.folder_var.get(), self.folder)
        self.assertEqual(app2.machine["ip"], "10.0.0.77")

        # A run where no machine answers still offers the last one at its last address.
        self.root.destroy()
        self.root = tk.Tk()
        self.root.withdraw()
        app3 = self.make(FakeMachines([]))
        self.assertEqual(list(app3.machine_combo["values"]), ["1501ultra — 10.0.0.77 (last seen)"])
        self.assertEqual(app3.machine["mac"], MACHINE_B["mac"])


if __name__ == "__main__":
    unittest.main()
