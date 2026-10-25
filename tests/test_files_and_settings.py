import json
import os
import tempfile
import unittest

from machine_files import files as fl
from machine_files import settings as st


class FolderListingTests(unittest.TestCase):
    def test_only_dst_and_dsb_any_case_sorted(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("b.DST", "A.dsb", "c.pes", "notes.txt", "d.Dst", "e.dst.bak"):
                open(os.path.join(d, n), "w").close()
            os.mkdir(os.path.join(d, "sub.dst"))  # a folder, not a file
            self.assertEqual(fl.list_design_files(d), ["A.dsb", "b.DST", "d.Dst"])


class FolderSignatureTests(unittest.TestCase):
    def test_changes_when_a_design_file_is_added_replaced_or_removed(self):
        with tempfile.TemporaryDirectory() as d:
            sig0 = fl.folder_signature(d)
            with open(os.path.join(d, "A.DST"), "wb") as fh:
                fh.write(b"x")
            sig1 = fl.folder_signature(d)
            self.assertNotEqual(sig0, sig1)
            self.assertEqual(sig1, fl.folder_signature(d))  # nothing changed, same value
            with open(os.path.join(d, "A.DST"), "wb") as fh:
                fh.write(b"longer")  # same name, new content (size differs)
            sig2 = fl.folder_signature(d)
            self.assertNotEqual(sig1, sig2)
            os.remove(os.path.join(d, "A.DST"))
            self.assertEqual(fl.folder_signature(d), sig0)

    def test_ignores_other_files_sees_folders_and_reports_an_unreadable_folder(self):
        with tempfile.TemporaryDirectory() as d:
            sig0 = fl.folder_signature(d)
            open(os.path.join(d, "notes.txt"), "w").close()
            os.mkdir(os.path.join(d, ".hidden"))
            self.assertEqual(fl.folder_signature(d), sig0, "other files and hidden folders don't count")
            os.mkdir(os.path.join(d, "Caps"))
            self.assertNotEqual(fl.folder_signature(d), sig0, "a new folder does (so it shows up)")
            self.assertIsNone(fl.folder_signature(os.path.join(d, "missing")))


class SubfolderTests(unittest.TestCase):
    def test_folders_sorted_without_hidden_or_system_ones(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("caps", "Shirts", ".git", "$RECYCLE.BIN", "System Volume Information"):
                os.mkdir(os.path.join(d, n))
            open(os.path.join(d, "LOGO.DST"), "w").close()
            self.assertEqual(fl.list_subfolders(d), ["caps", "Shirts"])

    def test_parent_folder(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "a", "b")
            os.makedirs(sub)
            self.assertEqual(fl.parent_folder(sub), os.path.join(d, "a"))
            self.assertEqual(fl.parent_folder(sub + os.sep), os.path.join(d, "a"))
        top = os.path.abspath(os.sep)
        self.assertIsNone(fl.parent_folder(top), "nothing above the top of the drive")


class MachineNameTests(unittest.TestCase):
    def test_slot_from_sequence_suffix(self):
        self.assertEqual(fl.machine_slot_and_name("LOGO.012", 0), (12, "LOGO"))
        self.assertEqual(fl.machine_slot_and_name("MY.NAME.003", 5), (3, "MY.NAME"))

    def test_no_numeric_suffix_uses_list_position(self):
        self.assertEqual(fl.machine_slot_and_name("LOGO.DST", 4), (5, "LOGO.DST"))
        self.assertEqual(fl.machine_slot_and_name("PLAIN", 0), (1, "PLAIN"))

    def test_rows_sorted_by_slot(self):
        rows = fl.machine_rows([{"name": "B.010"}, {"name": "A.002"}, {"name": "C.031"}])
        self.assertEqual([(r["slot"], r["name"], r["stored"]) for r in rows],
                         [(2, "A", "A.002"), (10, "B", "B.010"), (31, "C", "C.031")])


class PresenceTests(unittest.TestCase):
    def test_base_key_matches_upload_to_stored_name(self):
        self.assertEqual(fl.base_key("Logo.DST"), fl.base_key("LOGO.012"))
        self.assertEqual(fl.base_key("logo.dsb"), "logo")

    def test_to_machine_skips_files_already_there(self):
        machine = [{"name": "LOGO.001"}, {"name": "CAP.002"}]
        to_send, skipped = fl.missing_on_machine(["logo.dst", "NEW.DST", "Cap.dsb"], machine)
        self.assertEqual(to_send, ["NEW.DST"])
        self.assertEqual(skipped, ["logo.dst", "Cap.dsb"])

    def test_same_design_twice_in_folder_sent_once(self):
        to_send, skipped = fl.missing_on_machine(["LOGO.DST", "LOGO.DSB"], [])
        self.assertEqual(to_send, ["LOGO.DST"])
        self.assertEqual(skipped, ["LOGO.DSB"])

    def test_from_machine_skips_files_already_in_folder(self):
        to_copy, skipped = fl.missing_in_folder(["LOGO.001", "NEW.002"], ["logo.dsb"])
        self.assertEqual(to_copy, ["NEW.002"])
        self.assertEqual(skipped, ["LOGO.001"])

    def test_names_from_the_machine_never_leave_the_folder(self):
        # Anything on the network can answer as a machine, so its file names are untrusted.
        hostile = ["../../evil.003", "/etc/cron.d/x.004", "..\\..\\win.005", "C:/Windows/x.006", "a/b.007",
                   "..", ".", "", "  .hidden.010", "trail. .011", "tab\there.013", "x" * 300 + ".012"]
        with tempfile.TemporaryDirectory() as d:
            for stored in hostile:
                stem = fl.machine_slot_and_name(stored, 0)[1] or stored
                path = fl.free_local_path(d, stem, "DSB")
                self.assertEqual(os.path.dirname(path), d, stored)
                name = os.path.basename(path)
                self.assertTrue(name.endswith(".DSB") and not name.startswith("."), name)
                self.assertFalse(any(c in name for c in '/\\:*?"<>|\t'), name)
                self.assertLessEqual(len(name), fl.MAX_STEM_LENGTH + 4)

    def test_safe_stem_examples(self):
        self.assertEqual(fl.safe_local_stem("../../evil"), "_.._evil")
        self.assertEqual(fl.safe_local_stem("/etc/cron.d/x"), "_etc_cron.d_x")
        self.assertEqual(fl.safe_local_stem(".."), "design")
        self.assertEqual(fl.safe_local_stem("CON"), "_CON")
        self.assertEqual(fl.safe_local_stem("lpt1.part"), "_lpt1.part")
        self.assertEqual(fl.safe_local_stem("Cap Front"), "Cap Front")
        self.assertEqual(fl.safe_local_stem("马头机~1"), "马头机~1")

    def test_extension_is_made_safe_too(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(fl.free_local_path(d, "LOGO", "../D/S\\B"), os.path.join(d, "LOGO.DSB"))
            self.assertEqual(fl.free_local_path(d, "LOGO", ""), os.path.join(d, "LOGO.DSB"))

    def test_an_existing_symlink_counts_as_taken(self):
        if not hasattr(os, "symlink"):
            self.skipTest("no symlinks here")
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as elsewhere:
            try:
                os.symlink(os.path.join(elsewhere, "target"), os.path.join(d, "LOGO.DSB"))
            except OSError:
                self.skipTest("can't make symlinks here")
            self.assertIsNone(fl.free_local_path(d, "LOGO", "DSB"))

    def test_already_in_folder_means_in_the_format_being_saved(self):
        local = ["LOGO.DSB", "CAP.dst", "readme.txt"]
        self.assertEqual(fl.missing_in_folder(["LOGO.001", "CAP.002"], local, "DSB"), (["CAP.002"], ["LOGO.001"]))
        self.assertEqual(fl.missing_in_folder(["LOGO.001", "CAP.002"], local, "DST"), (["LOGO.001"], ["CAP.002"]))
        self.assertEqual(fl.missing_in_folder(["LOGO.001", "LOGO.017"], [], ".DST"), (["LOGO.001"], ["LOGO.017"]),
                         "two machine copies of one name are saved once")

    def test_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "LOGO.DST"), "w").close()
            self.assertIsNone(fl.free_local_path(d, "LOGO", "DST"))
            self.assertEqual(fl.free_local_path(d, "NEW", ".DST"), os.path.join(d, "NEW.DST"))


class SettingsTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "nested", "settings.json")
            data = {"lastFolder": "/designs",
                    "lastMachine": {"mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.40", "model": "1501ultra"},
                    "verifyAfterCopy": False, "saveAsDst": True}
            self.assertTrue(st.save(data, path))
            self.assertEqual(st.load(path), data)

    def test_missing_or_corrupt_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(st.load(os.path.join(d, "none.json")), st.defaults())
            bad = os.path.join(d, "bad.json")
            with open(bad, "w") as fh:
                fh.write("{not json")
            self.assertEqual(st.load(bad), st.defaults())
            with open(bad, "w") as fh:
                json.dump({"lastFolder": 5, "lastMachine": {"mac": "x"}}, fh)
            self.assertEqual(st.load(bad), st.defaults())


if __name__ == "__main__":
    unittest.main()
