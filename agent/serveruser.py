"""Run servers as the separate Windows user (``CommandBlockServer``).

A Minecraft server runs every mod and plugin as a program. Started as the
owner, a harmful mod could read the owner's files and Command Block's keys.
With ``security.separate_windows_user`` on, every outside program the agent
starts (Java for a server, Chunker, the Forge installer) goes through
``start()`` here and runs as one hidden local account instead. The installer
makes that account and saves its password encrypted for the owner's account
(DPAPI); see installer/serveruser.py and ADR 0007.

There is no fallback: when the setting is on and the account or its password
is missing or broken, ``start()`` raises ``ServerUserMissing`` and nothing
runs. Starting the server as the owner instead would quietly drop the
protection.

How Windows is asked: ``CreateProcessWithLogonW`` is the one call that lets
a normal (not Administrator) program start another program as a different
account. Python's ``subprocess`` doesn't offer it, so on Windows the call
Python makes inside ``subprocess.Popen`` (``_winapi.CreateProcess``) is
wrapped once: while ``start()`` has a password in ``_as_user`` it uses
CreateProcessWithLogonW with the same pipes, otherwise it does exactly what
it did before. ``_as_user`` is a ContextVar, so only the asyncio task that
called ``start()`` sees it (like a ThreadLocal in Java, per task).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger("msc.serveruser")

ACCOUNT = "CommandBlockServer"
SECRET_FILE = "server-user.bin"  # in the agent's data folder
MISSING = (
    "The separate Windows user is missing, so the server wasn't started. "
    "Open Settings and select Repair."
)
# Windows' answers that mean the account or its password is gone or broken.
LOGON_ERRORS = {1317, 1326, 1327, 1330, 1331, 1385, 1907, 1909}
IS_WINDOWS = os.name == "nt"


class ServerUserMissing(RuntimeError):
    def __init__(self, detail: str = "") -> None:
        super().__init__(MISSING)
        self.detail = detail


class FolderRightsFailed(RuntimeError):
    pass


def is_on(config: Any) -> bool:
    return bool(config.security.separate_windows_user)


def secret_path(config: Any) -> Path:
    """In the agent's own data folder: a server's config (ServerConfig)
    points data_dir at that server's folder, so the shared one is asked."""
    return Path(getattr(config, "root", config).data_dir) / SECRET_FILE


# ------------------------------------------------------------------ password
def read_password(path: Path) -> str:
    """The account's password, decrypted. ServerUserMissing when there's no
    file or it can't be decrypted (another account's DPAPI, a damaged file)."""
    try:
        raw = path.read_bytes()
        if IS_WINDOWS:
            from .security.records import _dpapi

            raw = _dpapi(raw, protect=False)
        password = raw.decode("utf-8")
    except (OSError, ValueError) as exc:
        raise ServerUserMissing(f"password file: {type(exc).__name__}") from None
    if not password:
        raise ServerUserMissing("password file is empty")
    return password


def write_password(path: Path, password: str) -> None:
    """Save it encrypted for this Windows account, readable only by it,
    SYSTEM and Administrators. The installer calls this."""
    from .security.certs import _restrict_file

    raw = password.encode("utf-8")
    if IS_WINDOWS:
        from .security.records import _dpapi

        raw = _dpapi(raw, protect=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(raw)
    _restrict_file(tmp)
    tmp.replace(path)


def account_exists(name: str = ACCOUNT) -> bool:
    if not IS_WINDOWS:
        return False
    import ctypes

    buffer = ctypes.c_void_p()
    netapi = ctypes.windll.netapi32  # type: ignore[attr-defined]
    code = netapi.NetUserGetInfo(None, name, 0, ctypes.byref(buffer))
    if buffer:
        netapi.NetApiBufferFree(buffer)
    return code == 0


def status(config: Any) -> dict[str, Any]:
    """What Settings shows: is it on, and does the account work."""
    on = is_on(config)
    problem = None
    if IS_WINDOWS:
        try:
            read_password(secret_path(config))
            if not account_exists():
                problem = "account"
        except ServerUserMissing:
            problem = "password"
    else:
        problem = "windows_only"
    return {
        "on": on,
        "ok": problem is None,
        "problem": problem,
        "account": ACCOUNT,
        "available": IS_WINDOWS,
    }


# ------------------------------------------------------------------ folder rights
def _icacls(args: list[str]) -> tuple[int, str]:
    from .winproc import NO_WINDOW

    done = subprocess.run(
        ["icacls", *args],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=300,
        creationflags=NO_WINDOW,
    )
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def grant(folder: Path, rights: str = "M") -> None:
    """Let the account change (M) or read and run (RX) this folder and
    everything in it. The agent owns the folders it made, so this needs no
    Administrator. Looked at first, so a server start doesn't walk a big
    world folder again each time."""
    entry = f"{ACCOUNT}:(OI)(CI)({rights})"
    try:
        code, now = _icacls([str(folder)])
        if code == 0 and entry.lower() in now.lower():
            return
        code, out = _icacls([str(folder), "/grant", f"{ACCOUNT}:(OI)(CI){rights}"])
    except (OSError, subprocess.SubprocessError) as exc:
        code, out = -1, str(exc)
    if code != 0:
        raise FolderRightsFailed(
            f"Command Block couldn't give the separate Windows user access to {folder}. "
            f"{out.strip()[:300]}"
        )


def remove_grant(folder: Path) -> str | None:
    """Take the account's access off a folder (Turn off). Returns why it
    failed, or None."""
    if not IS_WINDOWS:
        return None
    try:
        code, out = _icacls([str(folder), "/remove", ACCOUNT])
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    return None if code == 0 else out.strip()[:300]


def java_home(java: str) -> Path | None:
    """The Java folder (the one holding bin\\java.exe), or None."""
    found = shutil.which(java) or java
    path = Path(found)
    if not path.is_absolute() or not path.is_file():
        return None
    home = path.resolve().parent.parent
    # Only a real Java folder (it has a "release" file), never whatever
    # folder a stray java.exe happens to sit two levels under.
    return home if (home / "release").is_file() else None


# ------------------------------------------------------------------ starting
# Holds [account, password] only while one start runs; emptied right after,
# so the tasks asyncio makes during the start (which copy this context) keep
# neither the password nor the switch.
_as_user: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar("as_user", default=None)


async def start(
    config: Any,
    cmd: list[str],
    *,
    cwd: Path,
    writable: list[Path] | None = None,
    **popen: Any,
) -> asyncio.subprocess.Process:
    """Start an outside program the way the setting says: as the owner when
    it's off, as the separate Windows user when it's on. ``writable`` are
    the folders the program may change (its working folder is always one)."""
    if not is_on(config):
        return await asyncio.create_subprocess_exec(*cmd, cwd=str(cwd), **popen)
    password = read_password(secret_path(config))
    folders = [Path(cwd), *(writable or [])]
    for folder in folders:
        await asyncio.to_thread(grant, folder)
    home = java_home(cmd[0])
    if home:
        try:
            await asyncio.to_thread(grant, home, "RX")
        except FolderRightsFailed as exc:
            # Java under Program Files is already readable and owned by
            # Administrators; if it really can't be read, the start below
            # fails and says so.
            log.info("java folder not granted: %s", exc)
    return await launch_as(ACCOUNT, password, cmd, cwd, popen)


async def launch_as(
    account: str, password: str, cmd: list[str], cwd: Path, popen: dict[str, Any]
) -> asyncio.subprocess.Process:
    """The one place that asks Windows to start a program as another
    account. Tests replace it with a recorder."""
    if not IS_WINDOWS:
        raise ServerUserMissing("only on Windows")
    _install_hook()
    holder = [account, password]
    token = _as_user.set(holder)
    try:
        return await asyncio.create_subprocess_exec(*cmd, cwd=str(cwd), **popen)
    except OSError as exc:
        code = getattr(exc, "winerror", None)
        if code in LOGON_ERRORS:
            raise ServerUserMissing(f"Windows error {code}") from None
        raise
    finally:
        holder.clear()
        _as_user.reset(token)


# ------------------------------------------------------------------ Windows calls
_hooked = False


def _install_hook() -> None:
    global _hooked
    if _hooked:
        return
    import _winapi

    original = _winapi.CreateProcess  # type: ignore[attr-defined]

    def create_process(executable, args, proc_attrs, thread_attrs, inherit, flags, env, cwd, info):
        who = _as_user.get()
        if not who:
            return original(
                executable, args, proc_attrs, thread_attrs, inherit, flags, env, cwd, info
            )
        return _create_with_logon(who, executable, args, flags, env, cwd, info)

    _winapi.CreateProcess = create_process  # type: ignore[attr-defined]
    _hooked = True


# Flags CreateProcessWithLogonW takes. CREATE_NO_WINDOW isn't one of them, so
# the console it gets is hidden with SW_HIDE instead.
_ALLOWED_FLAGS = 0x00000004 | 0x00000010 | 0x00000200 | 0x00000400 | 0x04000000 | 0x0000FFE0
_STARTF_USESHOWWINDOW = 0x1
_STARTF_USESTDHANDLES = 0x100
_LOGON_WITH_PROFILE = 0x1
# What the agent's own account may do to the server's process: stop it, set
# its CPU cores, read its memory and CPU use, wait for it.
_AGENT_RIGHTS = 0x0001 | 0x0010 | 0x0200 | 0x0400 | 0x1000 | 0x00100000


def _create_with_logon(who, executable, args, flags, env, cwd, info):
    import ctypes
    from ctypes import wintypes

    if env is not None:
        raise ValueError("A program started as the separate Windows user gets its own settings")

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(startup)
    startup.dwFlags = (info.dwFlags if info else 0) | _STARTF_USESHOWWINDOW
    startup.wShowWindow = 0  # SW_HIDE
    if info and info.dwFlags & _STARTF_USESTDHANDLES:
        startup.hStdInput = int(info.hStdInput or 0)
        startup.hStdOutput = int(info.hStdOutput or 0)
        startup.hStdError = int(info.hStdError or 0)
    found = PROCESS_INFORMATION()
    account, password = who
    desktop = _hidden_desktop(account)
    if desktop:
        startup.lpDesktop = desktop
    command_line = ctypes.create_unicode_buffer(args)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    ok = advapi.CreateProcessWithLogonW(
        wintypes.LPCWSTR(account),
        wintypes.LPCWSTR("."),
        wintypes.LPCWSTR(password),
        wintypes.DWORD(_LOGON_WITH_PROFILE),
        wintypes.LPCWSTR(executable) if executable else None,
        command_line,
        wintypes.DWORD(flags & _ALLOWED_FLAGS),
        None,
        wintypes.LPCWSTR(cwd) if cwd else None,
        ctypes.byref(startup),
        ctypes.byref(found),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
    _allow_agent(found.hProcess)
    return found.hProcess, found.hThread, found.dwProcessId, found.dwThreadId


def _allow_agent(process: int) -> None:
    """The new process belongs to the other account, so by default the
    agent couldn't read its memory use or set its CPU cores by its id. Add
    the agent's own account to the process's access list (the handle
    Windows gave back has the rights to do that)."""
    code = _add_to_access_list(process, 6, _AGENT_RIGHTS, "CURRENT_USER")  # SE_KERNEL_OBJECT
    if code != 0:
        log.warning("could not let the agent watch the server process: %s", code)


# What the server account may do on the agent's hidden desktop: the same as
# Microsoft's own sample for starting a program as another user. Without
# it Windows can't set the program up and it stops with 0xC0000142.
_STATION_RIGHTS = 0x037F  # WINSTA_ALL_ACCESS
_DESKTOP_RIGHTS = 0x01FF  # DESKTOP_ALL_ACCESS
_desktop_for: dict[str, str] = {}  # account -> "station\\desktop", granted once
desktop_note: dict[str, Any] = {}  # what the last try found, for the boot test


def _hidden_desktop(account: str) -> str | None:
    """At boot the agent runs from a Task Scheduler task with nobody signed
    in, on a hidden window station (not WinSta0). CreateProcessWithLogonW
    doesn't give the new account access to that one (the boot test showed
    0xC0000142), so here the server account is added to the agent's own
    hidden station and desktop, once.
    Returns "station\\desktop" to start the program on, or None when the
    agent is on the normal WinSta0 (then Windows handles it)."""
    if account in _desktop_for:
        return _desktop_for[account]
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    user32.GetProcessWindowStation.restype = wintypes.HANDLE
    user32.GetThreadDesktop.restype = wintypes.HANDLE

    def name_of(handle: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        ok = user32.GetUserObjectInformationW(
            wintypes.HANDLE(handle), 2, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)
        )  # 2 = UOI_NAME
        return buffer.value if ok else ""

    station = user32.GetProcessWindowStation()
    desktop = user32.GetThreadDesktop(kernel.GetCurrentThreadId())
    station_name, desktop_name = name_of(station), name_of(desktop)
    desktop_note["names"] = f"{station_name}\\{desktop_name}"
    if not station_name or not desktop_name or station_name.lower() == "winsta0":
        return None
    # The handles above can't change access lists, so open both again by
    # name asking for that right (READ_CONTROL | WRITE_DAC).
    change = 0x00020000 | 0x00040000
    user32.OpenWindowStationW.restype = wintypes.HANDLE
    user32.OpenDesktopW.restype = wintypes.HANDLE
    opened = [
        (user32.OpenWindowStationW(station_name, False, change), _STATION_RIGHTS, "station"),
        (user32.OpenDesktopW(desktop_name, 0, False, change), _DESKTOP_RIGHTS, "desktop"),
    ]
    try:
        for handle, rights, what in opened:
            code = _add_to_access_list(handle, 7, rights, account) if handle else -1
            desktop_note[what] = code if handle else "could not open it"
            if code != 0:
                # The start below then stops with 0xC0000142; this says why.
                log.warning("could not let %s use the hidden %s: %s", account, what, code)
                return None
    finally:
        for handle, _, what in opened:
            if handle:
                close = user32.CloseWindowStation if what == "station" else user32.CloseDesktop
                close(wintypes.HANDLE(handle))
    _desktop_for[account] = f"{station_name}\\{desktop_name}"
    return _desktop_for[account]


def _add_to_access_list(handle: int, object_type: int, rights: int, who: str) -> int:
    """Grant ``who`` (an account name, or CURRENT_USER) ``rights`` on a
    Windows object. Returns Windows' error code, 0 when it worked."""
    import ctypes
    from ctypes import wintypes

    class TRUSTEE(ctypes.Structure):
        _fields_ = [
            ("pMultipleTrustee", ctypes.c_void_p),
            ("MultipleTrusteeOperation", ctypes.c_int),
            ("TrusteeForm", ctypes.c_int),
            ("TrusteeType", ctypes.c_int),
            ("ptstrName", wintypes.LPWSTR),
        ]

    class EXPLICIT_ACCESS(ctypes.Structure):
        _fields_ = [
            ("grfAccessPermissions", wintypes.DWORD),
            ("grfAccessMode", ctypes.c_int),
            ("grfInheritance", wintypes.DWORD),
            ("Trustee", TRUSTEE),
        ]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    dacl_info = 4  # DACL_SECURITY_INFORMATION
    old, descriptor, new = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    code = advapi.GetSecurityInfo(
        wintypes.HANDLE(handle), object_type, dacl_info, None, None,
        ctypes.byref(old), None, ctypes.byref(descriptor),
    )  # fmt: skip
    if code != 0:
        return int(code)
    try:
        entry = EXPLICIT_ACCESS()
        entry.grfAccessPermissions = rights
        entry.grfAccessMode = 1  # GRANT_ACCESS
        entry.Trustee.TrusteeForm = 1  # TRUSTEE_IS_NAME
        entry.Trustee.TrusteeType = 1  # TRUSTEE_IS_USER
        entry.Trustee.ptstrName = who
        code = advapi.SetEntriesInAclW(1, ctypes.byref(entry), old, ctypes.byref(new))
        if code == 0:
            code = advapi.SetSecurityInfo(
                wintypes.HANDLE(handle), object_type, dacl_info, None, None, new, None
            )
        return int(code)
    finally:
        if new:
            kernel.LocalFree(new)
        kernel.LocalFree(descriptor)
