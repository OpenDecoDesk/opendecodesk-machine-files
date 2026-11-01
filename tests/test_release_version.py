"""The release version (scripts/release_version.py): the UTC date and that day's build number."""

import datetime
import importlib.util
import os
import tempfile
import unittest

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "release_version.py")
_spec = importlib.util.spec_from_file_location("release_version", _PATH)
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

DAY = datetime.date(2026, 11, 1)


class NextVersionTests(unittest.TestCase):
    def test_first_release_of_the_day_is_01(self):
        self.assertEqual(rv.next_version([], DAY), "2026.11.01.01")
        self.assertEqual(rv.next_version(["v0.6.1", "v0.6.0"], DAY), "2026.11.01.01")

    def test_next_after_the_days_highest(self):
        tags = ["v2026.11.01.01", "v2026.11.01.03", "v2026.11.01.02", "v2026.10.31.07"]
        self.assertEqual(rv.next_version(tags, DAY), "2026.11.01.04")

    def test_other_days_and_other_tags_dont_count(self):
        tags = ["v2026.10.31.05", "v2026.11.02.01", "2026.11.01.04", "v2026.11.01.1", "v2026.11.01.02-test"]
        self.assertEqual(rv.next_version(tags, DAY), "2026.11.01.01")

    def test_past_nine_and_ninety_nine(self):
        self.assertEqual(rv.next_version(["v2026.11.01.09"], DAY), "2026.11.01.10")
        self.assertEqual(rv.next_version(["v2026.11.01.99"], DAY), "2026.11.01.100")

    def test_today_is_utc(self):
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y.%m.%d")
        self.assertEqual(rv.next_version([]), today + ".01")


class StampTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.init = os.path.join(self.tmp.name, "__init__.py")
        with open(self.init, "w", encoding="utf-8") as fh:
            fh.write('"""Doc."""\n\n# a note\n__version__ = "dev"\n')

    def tearDown(self):
        self.tmp.cleanup()

    def test_stamp_rewrites_only_the_version_line(self):
        rv.stamp("2026.11.01.02", self.init)
        with open(self.init, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), '"""Doc."""\n\n# a note\n__version__ = "2026.11.01.02"\n')
        self.assertEqual(rv.read(self.init), "2026.11.01.02")

    def test_stamp_refuses_anything_else(self):
        for bad in ("0.7.0", "v2026.11.01.01", "2026.11.1.1", "", "2026.11.01.01\n__x__"):
            with self.assertRaises(ValueError):
                rv.stamp(bad, self.init)
        self.assertEqual(rv.read(self.init), "dev")

    def test_the_source_says_dev(self):
        self.assertEqual(rv.read(), "dev", "the version is stamped by CI, not set in the source")


class MacVersionTests(unittest.TestCase):
    def test_at_most_three_numbers(self):
        self.assertEqual(rv.mac_versions("2026.11.01.01"), ("2026.11.01", "2026.11.0101"))
        self.assertEqual(rv.mac_versions("2026.11.01.12"), ("2026.11.01", "2026.11.0112"))
        self.assertEqual(rv.mac_versions("dev"), ("0.0.0", "0"))


if __name__ == "__main__":
    unittest.main()
