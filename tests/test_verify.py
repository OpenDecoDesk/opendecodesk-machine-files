import unittest

from machine_files import files as fl
from machine_files import stitches
from tests.designs import SAMPLE, make_dsb, make_dst


class StitchCompareTests(unittest.TestCase):
    def test_both_formats_read_the_same_design(self):
        a = stitches.parse_dst(make_dst(SAMPLE))
        b = stitches.parse_dsb(make_dsb(SAMPLE))
        self.assertEqual(a["stitches"], b["stitches"])
        self.assertEqual((a["stitchCount"], a["colors"]), (72, 3))

    def test_a_dst_the_machine_stored_as_dsb_matches(self):
        sent = make_dst(SAMPLE, label="LOGO")
        back = make_dsb(SAMPLE, machine_style=True)
        self.assertNotEqual(sent, back, "the bytes differ — it's been converted")
        r = stitches.compare(sent, "LOGO.DST", back, "returned.dsb")
        self.assertTrue(r["match"], r["summary"])
        self.assertIn("Same design: 72 stitches, 3 colour(s)", r["summary"])

    def test_a_dsb_reencoded_by_the_machine_matches(self):
        r = stitches.compare(make_dsb(SAMPLE), "LOGO.DSB", make_dsb(SAMPLE, machine_style=True), "returned.dsb")
        self.assertTrue(r["match"], r["summary"])

    def test_one_moved_stitch_is_caught(self):
        changed = list(SAMPLE)
        changed[30] = ("s", -7, 13)
        r = stitches.compare(make_dst(SAMPLE), "a.dst", make_dsb(changed, machine_style=True), "r.dsb")
        self.assertFalse(r["match"])
        self.assertIn("first difference at stitch 30", r["summary"])

    def test_a_missing_stitch_or_colour_change_is_caught(self):
        shorter = SAMPLE[:-1]
        self.assertFalse(stitches.compare(make_dst(SAMPLE), "a.dst", make_dsb(shorter), "r.dsb")["match"])
        one_colour_less = [m for i, m in enumerate(SAMPLE) if not (m[0] == "c" and i < 40)]
        r = stitches.compare(make_dst(SAMPLE), "a.dst", make_dsb(one_colour_less), "r.dsb")
        self.assertFalse(r["match"])
        self.assertEqual((r["sent"]["colors"], r["returned"]["colors"]), (3, 2))

    def test_unreadable_files_say_so(self):
        with self.assertRaises(ValueError):
            stitches.compare(b"short", "a.dst", make_dsb(SAMPLE), "r.dsb")
        with self.assertRaises(ValueError):
            stitches.parse(make_dsb(SAMPLE), "design.pes")


class NewestStoredTests(unittest.TestCase):
    def test_highest_sequence_number_of_the_same_base_name(self):
        listing = [{"name": "LOGO.004"}, {"name": "CAP.010"}, {"name": "LOGO.021"}, {"name": "LOGO SMALL.030"}]
        self.assertEqual(fl.newest_stored_for("logo.dst", listing), "LOGO.021")
        self.assertIsNone(fl.newest_stored_for("NEW.DST", listing))


if __name__ == "__main__":
    unittest.main()
