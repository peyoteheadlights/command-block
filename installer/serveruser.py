"""Make, repair and remove the separate Windows user (``CommandBlockServer``).

Servers run as this hidden local account so a harmful mod or plugin can't
read the owner's files (agent/serveruser.py, ADR 0007). Making an account
needs Administrator, which the desktop app never has, so this is the
installer's job:

* a new install makes it and the starter service (Engine.step_server_user),
* ``mcsc server-user on`` and ``mcsc server-user repair`` make it, or give
  it a new password when it's there already (Settings: Turn on, Repair),
* ``mcsc server-user off`` and uninstalling remove it (Settings: Turn off).

The desktop app starts ``mcsc server-user ...`` as Administrator (Windows
asks first), waits for it, then tells the agent. The password is made here,
handed only to Windows and to the starter service's encrypted file (which
only SYSTEM and Administrators can open), and never printed, logged or
shown. The agent never has it.
"""

from __future__ import annotations

import os
import secrets
import string
import sys
from pathlib import Path

from agent.starter import ACCOUNT

from .system import System, SystemError_

USAGE = "mcsc server-user on|repair|off [owner's Windows user name]"
NOT_THE_OWNER = 6  # the exit code the desktop app explains


def new_password() -> str:
    """40 random letters, digits and signs, with at least one of each kind
    so Windows' "complex password" rule is always met."""
    kinds = (string.ascii_lowercase, string.ascii_uppercase, string.digits, "-_.!#%")
    alphabet = "".join(kinds)
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(40))
        if all(any(c in kind for c in password) for kind in kinds):
            return password


def make(system: System, program_dir: Path) -> None:
    """Make the account (or give it a new password), then set up the
    starter service with that password, for the account running this (the
    owner's: the agent runs as it). Repair runs this again: the service is
    restarted so it reads the new password."""
    password = new_password()
    system.make_server_user(ACCOUNT, password)
    try:
        system.install_starter(program_dir, password, system.current_user_sid())
    except (SystemError_, OSError):
        # No account left behind that nothing can use.
        system.remove_server_user(ACCOUNT)
        raise


def remove(system: System) -> None:
    system.remove_starter()
    system.remove_server_user(ACCOUNT)


def main(argv: list[str], system: System | None = None) -> int:
    from agent import appinfo

    system = system or System()
    if len(argv) not in (1, 2) or argv[0] not in ("on", "repair", "off"):
        print(USAGE)
        return 2
    if not system.is_admin():
        print("This needs Administrator. Use Turn on, Turn off or Repair in Settings.")
        return 5
    if (
        argv[0] != "off"
        and len(argv) == 2
        and argv[1].lower() != os.environ.get("USERNAME", "").lower()
    ):
        # The starter service only takes requests from the account this runs
        # as. When a different administrator said Yes at Windows' prompt,
        # the owner's app couldn't use it, so nothing is made.
        print(
            "Windows asked another account's permission. Sign in to Windows with an "
            "administrator account that runs Command Block, then try again."
        )
        return NOT_THE_OWNER
    try:
        if argv[0] == "off":
            remove(system)
        else:
            make(system, appinfo.program_dir())
    except (SystemError_, OSError) as exc:
        print(f"The separate Windows user couldn't be changed: {exc}", file=sys.stderr)
        return 1
    return 0
