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


def _dst_pauses(dst):
    """Stitch numbers where a .DST colour change (a pause) falls."""
    out, n = [], 0
    for i in range(512, len(dst) - 2, 3):
        b2 = dst[i + 2]
        if b2 & 0xF3 == 0xF3:
            break
        if b2 & 0xC3 == 0xC3:
            out.append(n)
        elif b2 & 0x83 != 0x83:
            n += 1
    return out


class DsbToDstTests(unittest.TestCase):
    def test_the_machines_copy_becomes_the_original_design(self):
        dst = stitches.dsb_to_dst(make_dsb(SAMPLE, machine_style=True), label="LOGO")
        self.assertTrue(stitches.compare(make_dst(SAMPLE), "orig.dst", dst, "made.dst")["match"])
        self.assertTrue(stitches.compare(make_dsb(SAMPLE, machine_style=True), "m.dsb", dst, "made.dst")["match"])
        self.assertEqual(_dst_pauses(dst), [35, 60], "the two colour changes, after stitches 35 and 60")
        self.assertTrue(dst.startswith(b"LA:LOGO"))
        self.assertIn(b"CO:  2", dst[:512])
        self.assertTrue(dst.endswith(bytes([0, 0, 0xF3])), "ends with the .DST end record")

    def test_a_stop_becomes_a_pause_in_either_spelling(self):
        for stop in (0x88, 0xE8):  # the machines write 0x88; other software 0xE8
            dsb = bytearray(make_dsb([("s", 5, 5)] * 10 + [("s", 1, 1)] * 10))
            at = 512 + 3 * 10
            dsb[at:at] = bytes([stop, 0, 0])
            dst = stitches.dsb_to_dst(bytes(dsb))
            self.assertEqual(_dst_pauses(dst), [10], hex(stop))
            self.assertTrue(stitches.compare(bytes(dsb), "m.dsb", dst, "d.dst")["match"])
            lost = stitches.dsb_to_dst(make_dsb([("s", 5, 5)] * 10 + [("s", 1, 1)] * 10))
            self.assertFalse(stitches.compare(bytes(dsb), "m.dsb", lost, "d.dst")["match"],
                             "a .DST that lost the stop doesn't verify")

    def test_long_moves_are_split_into_jumps(self):
        dsb = make_dsb([("s", 10, 10), ("j", 200, -180), ("s", 3, 3), ("s", 3, 3)])
        dst = stitches.dsb_to_dst(dsb)
        self.assertTrue(stitches.compare(dsb, "m.dsb", dst, "d.dst")["match"])
        self.assertEqual(stitches.parse_dst(dst)["stitches"][1][:2], (213, -167))

    def test_nothing_to_convert(self):
        with self.assertRaises(ValueError):
            stitches.dsb_to_dst(b"LA:/local/b" + bytes(10))
        with self.assertRaises(ValueError):
            stitches.dsb_to_dst(make_dsb([("j", 5, 5)]))


class NewestStoredTests(unittest.TestCase):
    def test_highest_sequence_number_of_the_same_base_name(self):
        listing = [{"name": "LOGO.004"}, {"name": "CAP.010"}, {"name": "LOGO.021"}, {"name": "LOGO SMALL.030"}]
        self.assertEqual(fl.newest_stored_for("logo.dst", listing), "LOGO.021")
        self.assertIsNone(fl.newest_stored_for("NEW.DST", listing))


if __name__ == "__main__":
    unittest.main()
