# PyInstaller recipe for the installed program folder:
#   CommandBlock.exe       (the desktop app)
#   CommandBlockAgent.exe  (no window: the startup task)
#   mcsc.exe               (the agent's program, for typed commands)
# Built by scripts/build_installer.py on Windows; see docs/developers.md.
# -*- mode: python -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 (PyInstaller defines SPECPATH)
ICON = str(ROOT / "desktop" / "images" / "app.ico")

datas = [
    (str(ROOT / "desktop" / "images"), "desktop/images"),
    (str(ROOT / "shared"), "shared"),
    (str(ROOT / "installer" / "firewall.ps1"), "installer"),
    (str(ROOT / "config" / "config.example.yaml"), "config"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD-PARTY-NOTICES.md"), "."),
    (str(ROOT / "licenses"), "licenses"),
    (str(ROOT / "CHANGELOG.md"), "."),
]
# uvicorn picks its loop, protocol and lifespan parts by name at run time.
# The setup window's Qt parts (PySide6) are found by PyInstaller's own hook.
hidden = (
    collect_submodules("agent")
    + collect_submodules("desktop")
    + collect_submodules("installer")
    + collect_submodules("uvicorn")
    + ["websockets", "multipart", "segno"]
)

a = Analysis(  # noqa: F821
    [
        str(ROOT / "packaging" / "desktop_entry.py"),
        str(ROOT / "packaging" / "agent_entry.py"),
        str(ROOT / "packaging" / "cli_entry.py"),
    ],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "mypy", "ruff", "PySide6.QtNetwork", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.QtOpenGL", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

desktop_exe = EXE(  # noqa: F821
    pyz,
    [s for s in a.scripts if s[0] == "desktop_entry"],
    [],
    exclude_binaries=True,
    name="CommandBlock",
    icon=ICON,
    console=False,
    version=str(ROOT / "build" / "version-info.txt"),
)
agent_exe = EXE(  # noqa: F821
    pyz,
    [s for s in a.scripts if s[0] == "agent_entry"],
    [],
    exclude_binaries=True,
    name="CommandBlockAgent",
    icon=ICON,
    console=False,
    version=str(ROOT / "build" / "version-info.txt"),
)
cli_exe = EXE(  # noqa: F821
    pyz,
    [s for s in a.scripts if s[0] == "cli_entry"],
    [],
    exclude_binaries=True,
    name="mcsc",
    icon=ICON,
    console=True,
    version=str(ROOT / "build" / "version-info.txt"),
)
COLLECT(  # noqa: F821
    desktop_exe,
    agent_exe,
    cli_exe,
    a.binaries,
    a.datas,
    name="CommandBlock",
)
