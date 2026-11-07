"""Run servers as the separate Windows user (``CommandBlockServer``).

A Minecraft server runs every mod and plugin as a program. Started as the
owner, a harmful mod could read the owner's files and Command Block's keys.
With ``security.separate_windows_user`` on, every outside program the agent
starts (Java for a server, Chunker, the Forge installer) goes through
``start()`` here and runs as one hidden local account instead. The installer
makes that account and the small starter service that starts programs as
it (agent/starter.py, installer/serveruser.py, ADR 0007).

There is no fallback: when the setting is on and the account or the
service is missing or broken, ``start()`` raises ``ServerUserMissing`` and
nothing runs. Starting the server as the owner instead would quietly drop
the protection.

How Windows is asked: the agent can't start a program as another account
at boot (nobody signed in), so it asks the starter service over its named
pipe. Python's ``subprocess`` doesn't know that way, so on Windows the call
Python makes inside ``subprocess.Popen`` (``_winapi.CreateProcess``) is
wrapped once: while ``start()`` has set ``_via_starter`` it asks the service
with the same console pipes, otherwise it does exactly what it did before.
``_via_starter`` is a ContextVar, so only the asyncio task that called
``start()`` sees it (like a ThreadLocal in Java, per task).
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .starter import ACCOUNT, pipe_name

log = logging.getLogger("msc.serveruser")

PIPE = pipe_name()  # the live test points this at its own copy of the service
MISSING = (
    "The separate Windows user is missing, so the server wasn't started. "
    "Open Settings and select Repair."
)
NOT_RUNNING = "the starter service isn't running"
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


def account_exists(name: str = ACCOUNT) -> bool:
    if not IS_WINDOWS:
        return False
    import ctypes

    buffer = ctypes.c_void_p()
    netapi = ctypes.windll.netapi32
    code = netapi.NetUserGetInfo(None, name, 0, ctypes.byref(buffer))
    if buffer:
        netapi.NetApiBufferFree(buffer)
    return code == 0


def status(config: Any) -> dict[str, Any]:
    """What Settings shows: is it on, and does the account work (the
    service signs in as it to answer)."""
    on = is_on(config)
    problem = None
    if not IS_WINDOWS:
        problem = "windows_only"
    elif not account_exists():
        problem = "account"
    else:
        try:
            ask({"do": "ping"})
        except ServerUserMissing as exc:
            problem = "service" if exc.detail == NOT_RUNNING else "password"
        except OSError:
            problem = "service"
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
_via_starter: contextvars.ContextVar[bool] = contextvars.ContextVar("via_starter", default=False)


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
    # At boot the agent can be up before Windows has started the service.
    await asyncio.to_thread(wait_for_starter)
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
    return await launch_as(cmd, cwd, popen)


async def launch_as(cmd: list[str], cwd: Path, popen: dict[str, Any]) -> asyncio.subprocess.Process:
    """The one place that starts a program as the account (through the
    starter service). Tests replace it with a recorder."""
    if not IS_WINDOWS:
        raise ServerUserMissing("only on Windows")
    _install_hook()
    token = _via_starter.set(True)
    try:
        return await asyncio.create_subprocess_exec(*cmd, cwd=str(cwd), **popen)
    finally:
        _via_starter.reset(token)


def wait_for_starter(seconds: float = 30) -> None:
    """Ask the service if it's there and can sign in as the account; while
    it isn't running yet, ask again for up to ``seconds``."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            ask({"do": "ping"})
            return
        except ServerUserMissing as exc:
            if exc.detail != NOT_RUNNING or time.monotonic() > deadline:
                raise
        time.sleep(1)


# ------------------------------------------------------------------ the service's pipe
# What the agent opens the pipe with: read, write, message mode, read who
# made it. And "identification only", so whoever is on the other end can
# see which account asks but can never act as it.
_PIPE_RIGHTS = 0x0001 | 0x0002 | 0x0100 | 0x00020000 | 0x00100000
_IDENTIFY_ONLY = 0x00100000 | 0x00010000  # SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION


def ask(request: dict[str, Any]) -> dict[str, Any]:
    """One request to the starter service, its answer back. ServerUserMissing
    when the service isn't there or can't sign in as the account."""
    if not IS_WINDOWS:
        raise ServerUserMissing("only on Windows")
    import _winapi

    deadline = time.monotonic() + 15
    while True:
        try:
            pipe = _winapi.CreateFile(
                PIPE, _PIPE_RIGHTS, 0, _winapi.NULL, _winapi.OPEN_EXISTING, _IDENTIFY_ONLY,
                _winapi.NULL,
            )  # fmt: skip
            break
        except OSError as exc:
            if exc.winerror == 231 and time.monotonic() < deadline:  # busy with another start
                time.sleep(0.2)
                continue
            if exc.winerror in (2, 231):
                raise ServerUserMissing(NOT_RUNNING) from None
            raise
    try:
        if not _made_by_system(pipe):
            # Someone else's program made a pipe with the service's name.
            log.warning("the starter pipe wasn't made by SYSTEM; not using it")
            raise ServerUserMissing(NOT_RUNNING)
        _winapi.SetNamedPipeHandleState(pipe, _winapi.PIPE_READMODE_MESSAGE, None, None)
        _winapi.WriteFile(pipe, json.dumps(request).encode("utf-8"))
        data, _ = _winapi.ReadFile(pipe, 65536)
    finally:
        _winapi.CloseHandle(pipe)
    answer = json.loads(data)
    if answer.get("ok"):
        return dict(answer)
    code = answer.get("winerror")
    if code in LOGON_ERRORS:
        raise ServerUserMissing(f"Windows error {code}")
    raise OSError(0, f"The starter service said: {answer.get('error')}", None, code)


def _made_by_system(pipe: int) -> bool:
    """Is the pipe's owner SYSTEM? Only SYSTEM (or an Administrator) can
    make an object owned by SYSTEM."""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    owner, descriptor = ctypes.c_void_p(), ctypes.c_void_p()
    code = advapi.GetSecurityInfo(
        wintypes.HANDLE(pipe), 6, 0x1, ctypes.byref(owner), None, None, None,
        ctypes.byref(descriptor),
    )  # SE_KERNEL_OBJECT, OWNER_SECURITY_INFORMATION  # fmt: skip
    if code != 0:
        return False
    try:
        return bool(advapi.IsWellKnownSid(owner, 22))  # WinLocalSystemSid
    finally:
        kernel.LocalFree(descriptor)


# ------------------------------------------------------------------ Windows calls
_hooked = False


def _install_hook() -> None:
    global _hooked
    if _hooked:
        return
    import _winapi

    original = _winapi.CreateProcess

    def create_process(executable, args, proc_attrs, thread_attrs, inherit, flags, env, cwd, info):
        if not _via_starter.get():
            return original(
                executable, args, proc_attrs, thread_attrs, inherit, flags, env, cwd, info
            )
        return _create_via_starter(executable, args, flags, env, cwd, info)

    _winapi.CreateProcess = create_process
    _hooked = True


def _create_via_starter(executable, args, flags, env, cwd, info):
    """What Popen expects back from CreateProcess: (process handle, thread
    handle, process id, thread id). The service puts both handles into this
    process, with only the rights the agent needs."""
    if env is not None:
        raise ValueError("A program started as the separate Windows user gets its own settings")
    handles = [0, 0, 0]
    if info and info.dwFlags & 0x100:  # STARTF_USESTDHANDLES
        handles = [int(h or 0) for h in (info.hStdInput, info.hStdOutput, info.hStdError)]
    answer = ask(
        {
            "do": "start",
            "application": executable,
            "command_line": args,
            "cwd": cwd,
            "flags": flags,
            "handles": handles,
        }
    )
    return answer["process"], answer["thread"], answer["pid"], answer["tid"]
