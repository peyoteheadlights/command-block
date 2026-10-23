# PyInstaller recipe for the one-file installer:
#   CommandBlock-Setup-<version>.exe
# It holds build/payload/payload.zip (the program folder) and its checksum
# list. It starts without Administrator rights: "Connect to someone else's
# PC" installs for one Windows user and needs none; anything else starts the
# setup again with them (installer/wizard.py relaunch_as_admin).
# Built by scripts/build_installer.py on Windows; see docs/developers.md.
# -*- mode: python -*-
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 (PyInstaller defines SPECPATH)
PAYLOAD = ROOT / "build" / "payload"
NAME = os.environ["MCSC_SETUP_NAME"]

a = Analysis(  # noqa: F821
    [str(ROOT / "installer" / "bootstrap.py")],
    pathex=[],
    datas=[(str(PAYLOAD / "payload.zip"), "."), (str(PAYLOAD / "payload.json"), ".")],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)  # noqa: F821
EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=NAME,
    icon=str(ROOT / "desktop" / "images" / "app.ico"),
    console=False,
    uac_admin=False,
    version=str(ROOT / "build" / "version-info.txt"),
)
