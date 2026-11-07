"""The starter service: starts the servers' programs as CommandBlockServer.

Why it exists: at boot the agent runs from a Task Scheduler task with
nobody signed in, and from there Windows can't set up a program started
with CreateProcessWithLogonW (it stops at once with 0xC0000142). So the
installer adds this small Windows service (ADR 0007). It runs as SYSTEM,
Windows' own account, which is allowed to sign in as another account and
start a program as it at any time.

It does one thing. It waits on one named pipe (a local "socket" with an
access list), and for each request from the agent it:

* checks the request came from the owner's account (the one account the
  installer wrote down; the pipe's access list only lets that account and
  SYSTEM in, and never anyone over the network),
* signs in as CommandBlockServer with the password only it can read,
* starts the program on its own hidden desktop with the agent's console
  pipes, and hands the agent a handle to the new process.

It never starts anything as SYSTEM or as any other account, and the request
can't choose the account, the settings (environment) or which of the
service's own handles the program gets. The agent keeps running as the
owner with its normal rights; only this small part is SYSTEM.

Only the standard library: it ships inside mcsc.exe (``mcsc
starter-service``) and keeps a weak PC's memory use low while it waits.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

ACCOUNT = "CommandBlockServer"
SERVICE = "CommandBlockStarter"
IS_WINDOWS = os.name == "nt"
SETTINGS_FOLDER = "Command Block Starter"  # in Program Files, SYSTEM and Administrators only
MAX_MESSAGE = 65536


def pipe_name(service: str = SERVICE) -> str:
    return rf"\\.\pipe\{service}"


def settings_path() -> Path:
    """In Program Files, where only Administrators can make folders, so no
    other account can make it first and keep access to it."""
    base = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files"
    return Path(base) / SETTINGS_FOLDER / "starter.json"


# ------------------------------------------------------------------ settings
# {"agent_user": the owner's account as a SID (Windows' id for an account,
# like "S-1-5-21-..."), "password": CommandBlockServer's password,
# encrypted for this PC}. The installer writes it into a folder only SYSTEM
# and Administrators can open (installer/system.py install_service).
_SID = re.compile(r"^S-1(-\d+){1,14}$")


def write_settings(path: Path, password: str, agent_user: str) -> None:
    if not _SID.match(agent_user):
        raise ValueError("not an account id")
    secret = password.encode("utf-8")
    if IS_WINDOWS:
        from .security.records import _dpapi

        secret = _dpapi(secret, protect=True, machine=True)
    text = json.dumps({"agent_user": agent_user, "password": base64.b64encode(secret).decode()})
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)  # made new, so it gets the folder's access list
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def read_settings(path: Path) -> tuple[str, str]:
    """(password, agent_user). ValueError or OSError when broken."""
    data = json.loads(path.read_text(encoding="utf-8"))
    agent_user = str(data["agent_user"])
    if not _SID.match(agent_user):
        raise ValueError("not an account id")
    secret = base64.b64decode(data["password"])
    if IS_WINDOWS:
        from .security.records import _dpapi

        secret = _dpapi(secret, protect=False)
    return secret.decode("utf-8"), agent_user


# ------------------------------------------------------------------ requests
# Creation flags the agent may ask for: a new process group and a normal
# priority class. Never REALTIME: the service could grant it, and it can
# freeze a weak PC.
ALLOWED_FLAGS = 0x200 | 0x20 | 0x40 | 0x80 | 0x4000 | 0x8000


def check_request(request: Any) -> dict[str, Any]:
    """The request, checked, or ValueError. Nothing in it is trusted."""
    if not isinstance(request, dict):
        raise ValueError("not a request")
    if request.get("do") == "ping":
        return {"do": "ping"}
    if request.get("do") != "start":
        raise ValueError("unknown request")
    command_line, cwd = request.get("command_line"), request.get("cwd")
    application, flags, handles = (
        request.get("application"),
        request.get("flags"),
        request.get("handles"),
    )
    if not isinstance(command_line, str) or not 0 < len(command_line) < 32000:
        raise ValueError("bad command line")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        raise ValueError("the working folder isn't a full path")
    if application is not None and (
        not isinstance(application, str) or not os.path.isabs(application)
    ):
        raise ValueError("bad program path")
    if "\0" in command_line + cwd + (application or ""):
        raise ValueError("bad text")
    if not isinstance(flags, int) or isinstance(flags, bool):
        raise ValueError("bad flags")
    if (
        not isinstance(handles, list)
        or len(handles) != 3
        or not all(type(h) is int and 0 <= h < 2**31 for h in handles)
    ):
        raise ValueError("bad handles")
    return {
        "do": "start",
        "command_line": command_line,
        "cwd": cwd,
        "application": application,
        "flags": flags & ALLOWED_FLAGS,
        "handles": handles,
    }


# ------------------------------------------------------------------ the service
def run_service(argv: list[str]) -> int:
    """``mcsc starter-service [--name NAME --settings FILE]``: what Windows
    runs. The two options are for the live test's own copy only (only an
    Administrator can set a service's command)."""
    name, settings = SERVICE, settings_path()
    if len(argv) == 4 and argv[0] == "--name" and argv[2] == "--settings":
        name, settings = argv[1], Path(argv[3])
    elif argv:
        print("mcsc starter-service is started by Windows, not typed.")
        return 2
    if not IS_WINDOWS:
        print("The starter service is only on Windows.")
        return 2
    return _Service(name, settings).run()


class _Service:
    """Talks to Windows' service manager through ctypes (there's no pywin32
    here). Like a Java servlet container calling init and destroy: Windows
    calls ``main`` once when it starts the service and ``control`` when it
    wants it to stop."""

    def __init__(self, name: str, settings: Path) -> None:
        import ctypes
        from ctypes import wintypes as w

        self.name, self.settings = name, settings
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.status_handle = None

        class Status(ctypes.Structure):
            _fields_ = [(f, w.DWORD) for f in (
                "type", "state", "accepts", "exit_code", "own_exit_code", "check_point", "wait",
            )]  # fmt: skip

        self.Status = Status
        main_type = ctypes.WINFUNCTYPE(None, w.DWORD, ctypes.c_void_p)
        control_type = ctypes.WINFUNCTYPE(
            w.DWORD, w.DWORD, w.DWORD, ctypes.c_void_p, ctypes.c_void_p
        )
        # Kept on self: Windows calls them later, so they must stay alive.
        self.main_c = main_type(self.main)
        self.control_c = control_type(self.control)

        class Entry(ctypes.Structure):
            _fields_ = [("name", w.LPWSTR), ("main", main_type)]

        self.table = (Entry * 2)(Entry(name, self.main_c), Entry(None, main_type()))
        self.advapi.RegisterServiceCtrlHandlerExW.restype = ctypes.c_void_p
        self.advapi.RegisterServiceCtrlHandlerExW.argtypes = [
            w.LPCWSTR, control_type, ctypes.c_void_p,
        ]  # fmt: skip
        self.advapi.SetServiceStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(Status)]

    def run(self) -> int:
        import ctypes

        if not self.advapi.StartServiceCtrlDispatcherW(self.table):
            print(f"Not started by Windows (error {ctypes.get_last_error()}).")
            return 1
        return 0

    def report(self, state: int, exit_code: int = 0) -> None:
        stop_or_shutdown = 0x1 | 0x4
        status = self.Status(0x10, state, stop_or_shutdown if state == 4 else 0, exit_code, 0, 0, 0)
        self.advapi.SetServiceStatus(self.status_handle, status)

    def control(self, code: int, event: int, data: Any, context: Any) -> int:
        if code in (1, 5):  # STOP, SHUTDOWN
            # Nothing to tidy: servers it started keep running on their own.
            self.report(1)  # STOPPED
            os._exit(0)
        return 0 if code == 4 else 120  # INTERROGATE answers; others aren't handled

    def main(self, argc: int, argv: Any) -> None:
        self.status_handle = self.advapi.RegisterServiceCtrlHandlerExW(
            self.name, self.control_c, None
        )
        try:
            password, agent_user = read_settings(self.settings)
            starter = Starter(password, agent_user)
            pipe = starter.make_pipe(pipe_name(self.name))
        except (OSError, ValueError, KeyError) as exc:
            # Shown in Event Viewer as the service's exit code; the agent
            # says "missing" and Settings offers Repair.
            self.report(1, getattr(exc, "winerror", None) or 1064)
            return
        self.report(4)  # RUNNING
        starter.serve(pipe)


# ------------------------------------------------------------------ the work
class Starter:
    """One sign-in as the account (kept while the service runs: Repair
    restarts the service), one hidden desktop, one pipe, one request at a
    time."""

    def __init__(self, password: str, agent_user: str) -> None:
        self.password, self.agent_user = password, agent_user
        self.api = _Api()
        self.signed_in: tuple[int, str, str] | None = None  # token, account SID, desktop

    # ---------------------------------------------------------- pipe
    def make_pipe(self, name: str) -> int:
        """Owned by SYSTEM (the agent checks that, so nobody can pretend to
        be this service), open to SYSTEM and the owner's account only, never
        over the network, one at a time. FIRST_PIPE_INSTANCE: if someone
        made a pipe with this name first, the service stops instead of
        sharing it."""
        api = self.api
        # The owner's rights: read and write, message mode, read the owner.
        # Not "create pipe instance", so it can't serve this pipe itself.
        rights = 0x0012018B
        security = api.security(f"O:SYD:P(A;;GA;;;SY)(A;;0x{rights:x};;;{self.agent_user})")
        try:
            handle = api.k.CreateNamedPipeW(
                name,
                0x3 | 0x00080000,  # DUPLEX | FIRST_PIPE_INSTANCE
                0x4 | 0x2 | 0x0 | 0x8,  # MESSAGE | READMODE_MESSAGE | WAIT | REJECT_REMOTE
                1, MAX_MESSAGE, MAX_MESSAGE, 0, api.ctypes.byref(security),
            )  # fmt: skip
        finally:
            api.k.LocalFree(security.lpSecurityDescriptor)
        if not handle or handle == api.INVALID:
            raise api.error("CreateNamedPipeW")
        return int(handle)

    def serve(self, pipe: int) -> None:
        import _winapi

        while True:
            try:
                _winapi.ConnectNamedPipe(pipe)
            except OSError as exc:
                if exc.winerror != 535:  # ERROR_PIPE_CONNECTED: a client came first
                    self.api.k.DisconnectNamedPipe(pipe)
                    continue
            try:
                try:
                    answer = self.answer(pipe)
                except Exception as exc:  # a bug must not stop the service for good
                    answer = {"ok": False, "winerror": 1064, "error": type(exc).__name__}
                _winapi.WriteFile(pipe, json.dumps(answer).encode("utf-8"))
                self.api.k.FlushFileBuffers(pipe)
            except OSError:
                pass  # the agent went away; nothing to tell it
            self.api.k.DisconnectNamedPipe(pipe)

    def answer(self, pipe: int) -> dict[str, Any]:
        import _winapi

        api = self.api
        client = api.client_process(pipe)
        try:
            if api.process_user(client) != self.agent_user:
                return {"ok": False, "winerror": 5, "error": "not the owner's account"}
            data, more = _winapi.ReadFile(pipe, MAX_MESSAGE)
            if more:
                return {"ok": False, "winerror": 87, "error": "request too big"}
            request = check_request(json.loads(data))
            token, account_sid, desktop = self.sign_in()
            if request["do"] == "ping":
                return {"ok": True}
            return {"ok": True, **api.start(token, account_sid, desktop, self.agent_user, client,
                                            request)}  # fmt: skip
        except ValueError as exc:
            return {"ok": False, "winerror": 87, "error": str(exc)}
        except OSError as exc:
            return {"ok": False, "winerror": exc.winerror, "error": str(exc)}
        finally:
            _winapi.CloseHandle(client)

    def sign_in(self) -> tuple[int, str, str]:
        if self.signed_in is None:
            self.signed_in = self.api.sign_in(ACCOUNT, self.password)
        return self.signed_in


class _Api:
    """The Windows calls, declared once with their real types (a 64-bit
    handle would be cut in half otherwise)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as w

        self.ctypes, self.w = ctypes, w
        H, V, D, B = w.HANDLE, ctypes.c_void_p, w.DWORD, w.BOOL
        self.INVALID = ctypes.c_void_p(-1).value
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.a = ctypes.WinDLL("advapi32", use_last_error=True)
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.e = ctypes.WinDLL("userenv", use_last_error=True)

        def declare(dll: Any, name: str, restype: Any, *argtypes: Any) -> None:
            function = getattr(dll, name)
            function.restype, function.argtypes = restype, list(argtypes)

        P = ctypes.POINTER
        declare(self.k, "CreateNamedPipeW", H, w.LPCWSTR, D, D, D, D, D, D, V)
        declare(self.k, "FlushFileBuffers", B, H)
        declare(self.k, "DisconnectNamedPipe", B, H)
        declare(self.k, "GetNamedPipeClientProcessId", B, H, P(w.ULONG))
        declare(self.k, "LocalFree", V, V)
        declare(self.k, "CloseHandle", B, H)
        declare(self.k, "TerminateProcess", B, H, w.UINT)
        declare(self.k, "InitializeProcThreadAttributeList", B, V, D, D, P(ctypes.c_size_t))
        declare(self.k, "UpdateProcThreadAttribute", B,
                V, D, ctypes.c_size_t, V, ctypes.c_size_t, V, V)  # fmt: skip
        declare(self.k, "DeleteProcThreadAttributeList", None, V)
        declare(self.a, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                B, w.LPCWSTR, D, P(V), V)  # fmt: skip
        declare(self.a, "SetKernelObjectSecurity", B, H, D, V)
        declare(self.a, "OpenProcessToken", B, H, D, P(H))
        declare(self.a, "GetTokenInformation", B, H, ctypes.c_int, V, D, P(D))
        declare(self.a, "ConvertSidToStringSidW", B, V, P(w.LPWSTR))
        declare(self.a, "LogonUserW", B, w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, D, D, P(H))
        declare(self.a, "CreateProcessAsUserW", B,
                H, w.LPCWSTR, w.LPWSTR, V, V, B, D, V, w.LPCWSTR, V, V)  # fmt: skip
        declare(self.a, "LookupPrivilegeValueW", B, w.LPCWSTR, w.LPCWSTR, V)
        declare(self.a, "AdjustTokenPrivileges", B, H, B, V, D, V, V)
        declare(self.u, "CreateWindowStationW", H, w.LPCWSTR, D, D, V)
        declare(self.u, "GetProcessWindowStation", H)
        declare(self.u, "SetProcessWindowStation", B, H)
        declare(self.u, "CreateDesktopW", H, w.LPCWSTR, w.LPCWSTR, V, D, D, V)
        declare(self.e, "LoadUserProfileW", B, H, V)
        declare(self.e, "CreateEnvironmentBlock", B, P(V), H, B)
        declare(self.e, "DestroyEnvironmentBlock", B, V)

        class SecurityAttributes(ctypes.Structure):
            _fields_ = [("nLength", D), ("lpSecurityDescriptor", V), ("bInheritHandle", B)]

        self.SecurityAttributes = SecurityAttributes

    def error(self, where: str) -> OSError:
        code = self.ctypes.get_last_error()
        return OSError(0, f"{where} failed", None, code)

    def security(self, sddl: str) -> Any:
        """Security attributes from an SDDL string (Windows' text form of
        an owner and access list). The caller frees lpSecurityDescriptor."""
        ctypes = self.ctypes
        descriptor = ctypes.c_void_p()
        if not self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None
        ):
            raise self.error("ConvertStringSecurityDescriptorToSecurityDescriptorW")
        return self.SecurityAttributes(
            ctypes.sizeof(self.SecurityAttributes), descriptor.value, False
        )

    # ---------------------------------------------------------- who is asking
    def client_process(self, pipe: int) -> int:
        """The process on the other end of the pipe, opened so the service
        can copy the console pipe ends out of it and the process handle into
        it, and read its account."""
        import _winapi

        pid = self.w.ULONG()
        if not self.k.GetNamedPipeClientProcessId(pipe, self.ctypes.byref(pid)):
            raise self.error("GetNamedPipeClientProcessId")
        return int(
            _winapi.OpenProcess(0x0040 | 0x1000, False, pid.value)
        )  # DUP_HANDLE | QUERY_LIMITED

    def process_user(self, process: int) -> str:
        token = self.w.HANDLE()
        if not self.a.OpenProcessToken(process, 0x8, self.ctypes.byref(token)):  # TOKEN_QUERY
            raise self.error("OpenProcessToken")
        try:
            return self.token_user(int(token.value or 0))
        finally:
            self.k.CloseHandle(token)

    def token_user(self, token: int) -> str:
        ctypes = self.ctypes
        size = self.w.DWORD()
        self.a.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # TokenUser
        buffer = ctypes.create_string_buffer(size.value)
        if not self.a.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise self.error("GetTokenInformation")
        text = self.w.LPWSTR()
        sid = ctypes.c_void_p.from_buffer(buffer).value
        if not self.a.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise self.error("ConvertSidToStringSidW")
        try:
            return str(text.value)
        finally:
            self.k.LocalFree(ctypes.cast(text, ctypes.c_void_p))

    # ---------------------------------------------------------- signing in
    def sign_in(self, account: str, password: str) -> tuple[int, str, str]:
        """Sign in as the account (keyboard-style, the right it has), load
        its profile like a normal sign-in does, and make the hidden desktop
        its programs run on. Returns (token, account SID, desktop)."""
        import ctypes

        w = self.w
        self.enable_privileges()
        token = w.HANDLE()
        if not self.a.LogonUserW(account, ".", password, 2, 0, ctypes.byref(token)):
            raise self.error("LogonUserW")  # 1326 wrong password, 1331 off, ...

        class ProfileInfo(ctypes.Structure):
            _fields_ = [
                ("dwSize", w.DWORD), ("dwFlags", w.DWORD), ("lpUserName", w.LPWSTR),
                ("lpProfilePath", w.LPWSTR), ("lpDefaultPath", w.LPWSTR),
                ("lpServerName", w.LPWSTR), ("lpPolicyPath", w.LPWSTR), ("hProfile", w.HANDLE),
            ]  # fmt: skip

        profile = ProfileInfo(ctypes.sizeof(ProfileInfo), 1, account)  # PI_NOUI
        try:
            if not self.e.LoadUserProfileW(token, ctypes.byref(profile)):
                raise self.error("LoadUserProfileW")
            account_sid = self.token_user(int(token.value or 0))
            desktop = self.make_desktop(account_sid)
        except OSError:
            self.k.CloseHandle(token)
            raise
        # The token and the profile stay open while the service runs.
        return int(token.value), account_sid, desktop

    def enable_privileges(self) -> None:
        """SYSTEM has these but Windows leaves some switched off until asked:
        loading a profile and starting a program as another account need
        them."""
        import ctypes

        w = self.w
        names = ("SeBackupPrivilege", "SeRestorePrivilege",
                 "SeAssignPrimaryTokenPrivilege", "SeIncreaseQuotaPrivilege")  # fmt: skip

        class Privileges(ctypes.Structure):
            _fields_ = [("count", w.DWORD), ("entries", (w.DWORD * 3) * len(names))]

        wanted = Privileges(len(names))
        for i, name in enumerate(names):
            luid = (w.DWORD * 2)()
            if not self.a.LookupPrivilegeValueW(None, name, luid):
                raise self.error("LookupPrivilegeValueW")
            wanted.entries[i][0], wanted.entries[i][1], wanted.entries[i][2] = luid[0], luid[1], 2
        token = w.HANDLE()
        process = w.HANDLE(-1)  # this process
        if not self.a.OpenProcessToken(process, 0x20 | 0x8, ctypes.byref(token)):  # ADJUST|QUERY
            raise self.error("OpenProcessToken")
        try:
            self.a.AdjustTokenPrivileges(token, False, ctypes.byref(wanted), 0, None, None)
        finally:
            self.k.CloseHandle(token)

    def make_desktop(self, account_sid: str) -> str:
        """A window station and desktop of the service's own that only
        SYSTEM and the account may use. Windows sets every program up on a
        desktop; SYSTEM's shared one must not be opened to the account (a
        harmful mod could then send messages to other services' windows)."""
        station_name = "CommandBlockServers"
        security = self.security(f"D:P(A;;GA;;;SY)(A;;GA;;;{account_sid})")
        everything = 0x10000000  # GENERIC_ALL
        try:
            station = self.u.CreateWindowStationW(
                station_name, 0, everything, self.ctypes.byref(security)
            )
            if not station:
                raise self.error("CreateWindowStationW")
            self.set_access(station, security)  # it may be left from before
            # A desktop is made in the process's current window station.
            before = self.u.GetProcessWindowStation()
            if not self.u.SetProcessWindowStation(station):
                raise self.error("SetProcessWindowStation")
            try:
                desktop = self.u.CreateDesktopW(
                    "Default", None, None, 0, everything, self.ctypes.byref(security)
                )
                if not desktop:
                    raise self.error("CreateDesktopW")
                self.set_access(desktop, security)
            finally:
                self.u.SetProcessWindowStation(before)
        finally:
            self.k.LocalFree(security.lpSecurityDescriptor)
        # Both handles stay open while the service runs.
        return f"{station_name}\\Default"

    # ---------------------------------------------------------- starting
    def start(
        self,
        token: int,
        account_sid: str,
        desktop: str,
        agent_user: str,
        client: int,
        request: dict[str, Any],
    ) -> dict[str, int]:
        import _winapi

        ctypes, w = self.ctypes, self.w
        me = _winapi.GetCurrentProcess()
        copied: dict[int, int] = {}  # the agent's handle -> this service's copy
        environment = ctypes.c_void_p()
        attributes = None
        try:
            # The console pipe ends, copied from the agent's process (only
            # from the process that asked, so it can only hand over its own).
            for handle in request["handles"]:
                if handle and handle not in copied:
                    copied[handle] = _winapi.DuplicateHandle(client, handle, me, 0, True, 0x2)
            std = [copied.get(h, 0) for h in request["handles"]]
            if not self.e.CreateEnvironmentBlock(ctypes.byref(environment), token, False):
                raise self.error("CreateEnvironmentBlock")
            info = _startup_info()()
            info.cb = ctypes.sizeof(info)
            info.dwFlags = 0x1  # USESHOWWINDOW, hidden
            place = ctypes.create_unicode_buffer(desktop)
            info.lpDesktop = ctypes.cast(place, w.LPWSTR)
            inherit = bool(copied)
            if inherit:
                # Only these handles reach the program, none of the service's own.
                info.dwFlags |= 0x100  # USESTDHANDLES
                info.hStdInput, info.hStdOutput, info.hStdError = std
                listed = (w.HANDLE * len(copied))(*copied.values())
                size = ctypes.c_size_t()
                self.k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
                attributes = ctypes.create_string_buffer(size.value)
                if not self.k.InitializeProcThreadAttributeList(
                    attributes, 1, 0, ctypes.byref(size)
                ):
                    attributes = None
                    raise self.error("InitializeProcThreadAttributeList")
                if not self.k.UpdateProcThreadAttribute(
                    attributes, 0, 0x20002, listed, ctypes.sizeof(listed), None, None
                ):  # PROC_THREAD_ATTRIBUTE_HANDLE_LIST
                    raise self.error("UpdateProcThreadAttribute")
                info.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
            # NO_WINDOW | UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT
            flags = request["flags"] | 0x08000000 | 0x400 | 0x80000
            found = _process_information()()
            command_line = ctypes.create_unicode_buffer(request["command_line"])
            if not self.a.CreateProcessAsUserW(
                token, request["application"], command_line, None, None, inherit, flags,
                environment, request["cwd"], ctypes.byref(info), ctypes.byref(found),
            ):  # fmt: skip
                raise self.error("CreateProcessAsUserW")
            try:
                self.let_agent_watch(found.hProcess, account_sid, agent_user)
                process = _winapi.DuplicateHandle(
                    me, found.hProcess, client, AGENT_RIGHTS, False, 0
                )
                thread = _winapi.DuplicateHandle(me, found.hThread, client, 0x00100000, False, 0)
            except OSError:
                # Never leave a server running that the agent can't see.
                self.k.TerminateProcess(found.hProcess, 1)
                raise
            finally:
                self.k.CloseHandle(found.hProcess)
                self.k.CloseHandle(found.hThread)
            return {
                "process": int(process),
                "thread": int(thread),
                "pid": int(found.dwProcessId),
                "tid": int(found.dwThreadId),
            }
        finally:
            if attributes is not None:
                self.k.DeleteProcThreadAttributeList(attributes)
            if environment:
                self.e.DestroyEnvironmentBlock(environment)
            for mine in copied.values():
                _winapi.CloseHandle(mine)

    def let_agent_watch(self, process: int, account_sid: str, agent_user: str) -> None:
        """The process's access list: SYSTEM and the account itself fully,
        and the owner's account the rights the agent uses by the process id
        (stop it, set its CPU cores, read its memory and CPU use, wait)."""
        sddl = f"D:(A;;GA;;;SY)(A;;GA;;;{account_sid})(A;;0x{AGENT_RIGHTS:x};;;{agent_user})"
        security = self.security(sddl)
        try:
            self.set_access(process, security)
        finally:
            self.k.LocalFree(security.lpSecurityDescriptor)

    def set_access(self, handle: Any, security: Any) -> None:
        if not self.a.SetKernelObjectSecurity(handle, 0x4, security.lpSecurityDescriptor):
            raise self.error("SetKernelObjectSecurity")  # 0x4: the access list


# TERMINATE | VM_READ | SET_INFORMATION | QUERY_INFORMATION |
# QUERY_LIMITED_INFORMATION | SYNCHRONIZE
AGENT_RIGHTS = 0x0001 | 0x0010 | 0x0200 | 0x0400 | 0x1000 | 0x00100000


def _startup_info() -> Any:
    import ctypes
    from ctypes import wintypes as w

    class StartupInfoEx(ctypes.Structure):
        _fields_ = [
            ("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
            ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD),
            ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD),
            ("dwFillAttribute", w.DWORD), ("dwFlags", w.DWORD), ("wShowWindow", w.WORD),
            ("cbReserved2", w.WORD), ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE),
            ("lpAttributeList", ctypes.c_void_p),
        ]  # fmt: skip

    return StartupInfoEx


def _process_information() -> Any:
    import ctypes
    from ctypes import wintypes as w

    class ProcessInformation(ctypes.Structure):
        _fields_ = [
            ("hProcess", w.HANDLE), ("hThread", w.HANDLE),
            ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD),
        ]  # fmt: skip

    return ProcessInformation
