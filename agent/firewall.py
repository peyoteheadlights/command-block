"""Windows Firewall: let one game port in, for Diagnose server.

Only "Let port N through Windows Firewall", a risky change the owner picked
in a Diagnose run (house rule 2, its seventh exception). Adding or removing
the rule needs Administrator rights, so Windows asks first (the UAC prompt);
No there means the change wasn't made. Checking whether the rule is there
doesn't need them.

netsh.exe from System32 by its full path, with a fixed argument list and no
shell. The rule's name is made here from the server's id, the protocol and
the port, so nothing typed by anyone reaches netsh.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from .winproc import NO_WINDOW

IS_WINDOWS = os.name == "nt"
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ELEVATED_WAIT_MS = 120_000  # the UAC prompt waits for the owner


class FirewallError(Exception):
    pass


def netsh() -> str:
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "netsh.exe")


def rule_name(server_id: str, protocol: str, port: int) -> str:
    if not SAFE_ID.match(server_id) or protocol not in ("tcp", "udp") or not 0 < port < 65536:
        raise FirewallError("That isn't a rule Command Block makes.")
    return f"CommandBlock-{server_id}-{protocol.upper()}-{port}"


def has_rule(name: str) -> bool:
    """True when a rule with this name is there already."""
    if not IS_WINDOWS:
        return False
    try:
        found = subprocess.run(
            [netsh(), "advfirewall", "firewall", "show", "rule", f"name={name}"],
            capture_output=True,
            timeout=20,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return found.returncode == 0


def add(name: str, protocol: str, port: int) -> None:
    _elevated(
        [
            "advfirewall",
            "firewall",
            "add",
            "rule",
            f"name={name}",
            "dir=in",
            "action=allow",
            f"protocol={protocol.upper()}",
            f"localport={int(port)}",
        ]
    )


def remove(name: str) -> None:
    _elevated(["advfirewall", "firewall", "delete", "rule", f"name={name}"])


def _elevated(arguments: list[str]) -> None:  # pragma: no cover - Windows only
    """Run netsh as Administrator (Windows asks) and wait for it."""
    if sys.platform != "win32":  # also tells mypy the rest is Windows only
        raise FirewallError("The Windows Firewall is only on Windows.")
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS: keep a handle to wait on
    info.lpVerb = "runas"
    info.lpFile = netsh()
    info.lpParameters = subprocess.list2cmdline(arguments)
    info.nShow = 0  # SW_HIDE
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
        raise FirewallError("Windows didn't let it change the firewall (No at the prompt?).")
    kernel = ctypes.windll.kernel32
    try:
        kernel.WaitForSingleObject(info.hProcess, ELEVATED_WAIT_MS)
        code = wintypes.DWORD()
        kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    finally:
        kernel.CloseHandle(info.hProcess)
    if code.value != 0:
        raise FirewallError(f"netsh couldn't change the firewall (code {code.value}).")
