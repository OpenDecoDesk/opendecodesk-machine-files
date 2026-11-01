# PyInstaller build: `pyinstaller opendecodesk-machine-files.spec`
# macOS -> dist/OpenDecoDesk Machine Files.app (a folder bundle; zip it to share)
# Windows / Linux -> dist/OpenDecoDesk Machine Files(.exe), one portable file
import os
import sys

sys.path.insert(0, os.path.join(SPECPATH, "scripts"))
import release_version

NAME = "OpenDecoDesk Machine Files"
# "dev", or the release's version that CI stamped into machine_files/__init__.py before this build.
SHORT_VERSION, BUNDLE_VERSION = release_version.mac_versions(release_version.read())

# icons/icon.png is the window's own icon and mark-*.png the header's (app.py); the .icns / .ico are the app's.
a = Analysis(["run.py"], pathex=["."], datas=[("icons/icon.png", "icons"), ("icons/mark-24.png", "icons"),
                                              ("icons/mark-48.png", "icons")])
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, console=False)
    coll = COLLECT(exe, a.binaries, a.datas, name=NAME)
    app = BUNDLE(
        coll,
        name=NAME + ".app",
        icon="icons/icon.icns",
        bundle_identifier="org.opendecodesk.machinefiles",
        info_plist={
            # macOS asks before an app talks to devices on the local network; this is the reason shown.
            "NSLocalNetworkUsageDescription":
                "Finds the embroidery machines on your network and copies design files to them.",
            "CFBundleShortVersionString": SHORT_VERSION,
            "CFBundleVersion": BUNDLE_VERSION,
        },
    )
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=NAME, console=False, icon="icons/icon.ico")
