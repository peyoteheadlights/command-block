"""The two programs in an installed copy.

``CommandBlockAgent.exe`` runs with no window. The startup task
runs it (``--launched-by task``) to start the agent. The Start Menu
shortcut opens the desktop app (``CommandBlock.exe``) instead.

``mcsc.exe`` is the same program for typed commands:

    mcsc check                 read-only health check (same as setup.ps1 --check)
    mcsc check --deep          ...also tries a real secure connection
    mcsc reset-password        choose a new owner password (signs everyone out)
    mcsc import FILE --to DIR  import an "Export everything" file
    mcsc autostart status      the Windows startup task (also: test, run)
    mcsc setup ...             the installer's screens (setup.exe runs this)
    mcsc apply-update          run by the update task only (see docs/security.md)
    mcsc version
"""

from __future__ import annotations

import sys


def agent_entry(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    from agent.main import main

    return main(args)


def cli_entry(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command, rest = (args[0], args[1:]) if args else ("help", [])
    if command == "check":
        from agent.main import main

        return main(["--check", *rest])
    if command == "reset-password":
        from . import reset_password

        return reset_password.main(rest)
    if command == "import":
        from . import import_from_pc

        return import_from_pc.main(rest)
    if command == "autostart":
        from . import autostart

        return autostart.main(rest)
    if command == "setup":
        from . import wizard

        return wizard.main(rest)
    if command == "apply-update":
        from . import apply_update

        return apply_update.main(rest)
    if command == "version":
        from agent import API_VERSION, __version__

        print(f"Command Block {__version__} (API {API_VERSION})")
        return 0
    print(__doc__)
    return 0 if command in ("help", "--help", "-h") else 2
