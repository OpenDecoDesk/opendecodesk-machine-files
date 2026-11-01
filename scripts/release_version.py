"""The app's version: the UTC date of the release and that day's build number, YYYY.MM.DD.NN (the first
release on 1 November 2026 is 2026.11.01.01, a second one that day 2026.11.01.02). Nobody sets it by hand:
the source says "dev", and the release run in CI (.github/workflows/ci.yml) works it out and stamps it in.

  git tag --list 'v*' | python scripts/release_version.py next    the next version, from the existing tags
  python scripts/release_version.py stamp 2026.11.01.01           writes it into machine_files/__init__.py
"""

import datetime
import os
import re
import sys

INIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "machine_files", "__init__.py")
VERSION_LINE = re.compile(r'^__version__ = "(.*)"$', re.M)
DATE_VERSION = re.compile(r"\d{4}\.\d{2}\.\d{2}\.\d{2,}")


def next_version(tags, today=None):
    """Today's date (UTC) and the next build number after today's tags (v<date>.NN); 01 if none."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    date = today.strftime("%Y.%m.%d")
    built = [int(m.group(1)) for m in (re.fullmatch(r"v" + re.escape(date) + r"\.(\d{2,})", t.strip())
                                       for t in tags) if m]
    return f"{date}.{max(built, default=0) + 1:02d}"


def read(path=INIT):
    """The version the source says ("dev" unless stamped)."""
    with open(path, encoding="utf-8") as fh:
        found = VERSION_LINE.findall(fh.read())
    if len(found) != 1:
        raise ValueError(f"expected one __version__ line in {path}")
    return found[0]


def stamp(version, path=INIT):
    """Rewrites the __version__ line, for the build."""
    if version != "dev" and not DATE_VERSION.fullmatch(version):
        raise ValueError(f"not a version: {version!r} (YYYY.MM.DD.NN or dev)")
    read(path)  # exactly one line to rewrite
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(VERSION_LINE.sub(f'__version__ = "{version}"', text))


def mac_versions(version):
    """The macOS app's (CFBundleShortVersionString, CFBundleVersion): Apple allows at most three numbers in
    each, so 2026.11.01.01 is shown as 2026.11.01 with build 2026.11.0101. A dev build is 0.0.0."""
    if not DATE_VERSION.fullmatch(version):
        return "0.0.0", "0"
    year, month, day, build = version.split(".")
    return f"{year}.{month}.{day}", f"{year}.{month}.{day}{build}"


def main(args):
    if args == ["next"]:
        print(next_version(sys.stdin.read().split()))
    elif len(args) == 2 and args[0] == "stamp":
        stamp(args[1])
        print(f"machine_files/__init__.py: __version__ = \"{read()}\"")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
