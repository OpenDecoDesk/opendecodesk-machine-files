# PyInstaller build: `pyinstaller opendecodesk-machine-files.spec`
# macOS -> dist/OpenDecoDesk Machine Files.app (a folder bundle; zip it to share)
# Windows / Linux -> dist/OpenDecoDesk Machine Files(.exe), one portable file
import sys

NAME = "OpenDecoDesk Machine Files"

a = Analysis(["run.py"], pathex=["."])
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, console=False)
    coll = COLLECT(exe, a.binaries, a.datas, name=NAME)
    app = BUNDLE(
        coll,
        name=NAME + ".app",
        bundle_identifier="org.opendecodesk.machinefiles",
        info_plist={
            # macOS asks before an app talks to devices on the local network; this is the reason shown.
            "NSLocalNetworkUsageDescription":
                "Finds the embroidery machines on your network and copies design files to them.",
            "CFBundleShortVersionString": "0.2.0",
        },
    )
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=NAME, console=False)
