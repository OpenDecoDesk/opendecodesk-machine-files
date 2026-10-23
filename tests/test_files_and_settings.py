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
        to_copy, skipped = fl.missing_in_folder(["LOGO.001", "NEW.002"], ["logo.dst"])
        self.assertEqual(to_copy, ["NEW.002"])
        self.assertEqual(skipped, ["LOGO.001"])

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
                    "lastMachine": {"mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.40", "model": "1501ultra"}}
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
