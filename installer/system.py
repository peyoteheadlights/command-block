"""Everything the installer asks of Windows and of the running app.

The install steps (``installer/engine.py``) call these methods and nothing
else, so the tests replace this class with a fake that records what would
have happened, and the steps themselves run on any system.

Every program started here gets an argument list, never a shell.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from agent import appinfo

from . import autostart, bootstrap, layout

IS_WINDOWS = os.name == "nt"


class SystemError_(RuntimeError):
    """A Windows step failed. ``message`` is one plain sentence; ``details``
    the real error, exit code and command."""

    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.details = details


LOOPBACK = "127.0.0.1"


def contact_host(network_host: str | None) -> str:
    """Where this PC reaches its own app: the address the app listens on
    (network.host), or 127.0.0.1 when it listens on every address. A
    Tailscale address works from the PC itself too."""
    host = str(network_host or "").strip()
    if host in ("", "0.0.0.0", "::", "localhost"):
        return LOOPBACK
    return f"[{host}]" if ":" in host else host


def read_address(config_path: Path) -> tuple[str, int, bool]:
    """(host to contact, port, HTTPS?) from a config.yaml, with the
    defaults when it can't be read."""
    host, port, tls = LOOPBACK, 8765, True
    try:
        import yaml

        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        network = data.get("network") or {}
        host = contact_host(network.get("host"))
        port = int(network.get("port") or port)
        tls = bool((data.get("tls") or {}).get("enabled", True))
    except Exception:
        pass
    return host, port, tls


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# The desktop shortcut, found where Windows says the desktop is (it can be
# moved to OneDrive), as a PowerShell expression.
DESKTOP_LINK = f"(Join-Path ([Environment]::GetFolderPath('Desktop')) '{layout.PRODUCT}.lnk')"


class System:
    """The real thing, on Windows."""

    # The address the app listens on (contact_host); callers set it from the
    # settings before asking the app anything.
    host: str = LOOPBACK

    # ------------------------------------------------------------ tools
    def run(self, args: list[str], timeout: float = 120) -> tuple[int, str, str]:
        return autostart.run_tool(args, timeout=timeout)

    def powershell(self, script: str, timeout: float = 120) -> tuple[int, str, str]:
        return self.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], timeout
        )

    def is_admin(self) -> bool | None:
        return autostart.is_admin()

    # ------------------------------------------------------------ the running app
    def _client(self, timeout: float = 10.0):
        import httpx

        # The PC talking to itself, on the address the app listens on (127.0.0.1
        # or its Tailscale address). The certificate names the PC's Tailscale
        # name, not the address, so it isn't checked here.
        return httpx.Client(verify=False, timeout=timeout)

    def agent_answers(self, port: int, tls: bool = True) -> bool:
        scheme = "https" if tls else "http"
        try:
            with self._client(5.0) as client:
                answer = client.get(f"{scheme}://{self.host}:{port}/api/health")
                return answer.status_code == 200 and bool(answer.json().get("ok"))
        except Exception:
            return False

    def wait_for_agent(self, port: int, tls: bool, timeout: float = 90.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.agent_answers(port, tls):
                return True
            time.sleep(2)
        return False

    def api(
        self, port: int, tls: bool, token: str, method: str, path: str, body: Any = None
    ) -> Any:
        scheme = "https" if tls else "http"
        with self._client(30.0) as client:
            answer = client.request(
                method,
                f"{scheme}://{self.host}:{port}/api{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
        if answer.status_code >= 400:
            raise SystemError_(
                "The server panel refused a request from the installer.",
                f"{method} {path}: HTTP {answer.status_code}",
            )
        return answer.json()

    def tailscale_address(self) -> str | None:
        """This PC's Tailscale IPv4 address, only when the Tailscale daemon
        itself says it is connected."""
        import ipaddress

        from agent.tailscale import connection_status

        report = connection_status()
        if not (report.get("connected") and report.get("verified")):
            return None
        for address in report.get("addresses") or []:
            try:
                ip = ipaddress.ip_address(str(address))
            except ValueError:
                continue
            if ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"):
                return str(ip)
        return None

    def remove_after_exit(self, setup_exe: Path, folder: Path) -> None:
        """Have a copy of the setup remove ``folder`` once the program
        folder's setup.exe, which runs until this uninstall ends, exits."""
        if IS_WINDOWS:
            bootstrap.start_leftover_remover(setup_exe, folder)

    def stop_agent(self, port: int, tls: bool) -> bool:
        """End the startup task's copy of the app and wait for the port to
        go quiet. True when nothing answers any more."""
        if IS_WINDOWS:
            # The old names too, for an install from before the rename.
            for task in (autostart.TASK_NAME, autostart.OLD_TASK_NAME):
                self.run(["schtasks.exe", "/End", "/TN", task])
            for exe in (appinfo.AGENT_EXE, appinfo.OLD_AGENT_EXE):
                self.run(["taskkill.exe", "/IM", exe, "/T", "/F"])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if not self.agent_answers(port, tls):
                return True
            time.sleep(1)
        return False

    def open_as_user(self, program: Path) -> None:
        """Start a program as the person signed in to Windows, not as this
        Administrator setup: Explorer starts it, and Explorer runs as them."""
        self.run(["explorer.exe", str(program)])  # Explorer's exit code means nothing

    def start_agent(self) -> None:
        """Start the app through its task, so it runs as the person's own
        account without Administrator rights, never as this installer."""
        code, out, err = self.run(["schtasks.exe", "/Run", "/TN", autostart.TASK_NAME])
        if code != 0:
            raise SystemError_(
                "Windows didn't start the server panel.",
                f"schtasks /Run exited {code}: {(err or out).strip()}",
            )

    # ------------------------------------------------------------ registration
    def register_startup(self, launch: dict[str, str], mode: str) -> dict[str, Any]:
        try:
            return autostart.enable(mode, pin_java=False, launch=launch)
        except autostart.AutostartError as exc:
            raise SystemError_(
                "Windows didn't accept the task that starts the server panel.", str(exc)
            ) from exc

    def previous_startup_launch(self) -> dict[str, str] | None:
        if not IS_WINDOWS:
            return None
        task = autostart.query_task() or autostart.query_task(autostart.OLD_TASK_NAME)
        if not task or "parse_error" in task:
            return None
        return {
            "command": task.get("command") or "",
            "arguments": task.get("arguments") or "",
            "working_directory": task.get("working_directory") or "",
            "mode": task.get("mode") or "manual",
        }

    def remove_startup(self) -> None:
        try:
            autostart.disable()
        except autostart.AutostartError:
            pass

    def remove_old_startup(self) -> None:
        """The startup task of an install from before the rename. Once the
        old copy is stopped it must not start again by itself."""
        if IS_WINDOWS:
            self.run(["schtasks.exe", "/Delete", "/TN", autostart.OLD_TASK_NAME, "/F"])

    def remove_old_names(self) -> None:
        """What an install from before the rename left in Windows: its
        updater task, firewall rules, Start menu folder and Installed apps
        entry. Removed once the new copy is running."""
        if not IS_WINDOWS:
            return
        self.run(["schtasks.exe", "/Delete", "/TN", layout.OLD_UPDATER_TASK, "/F"])
        names = ",".join(_ps_quote(name) for name in layout.OLD_FIREWALL_RULES)
        self.powershell(
            f"Remove-NetFirewallRule -DisplayName {names} -ErrorAction SilentlyContinue"
        )
        import shutil

        shutil.rmtree(layout.start_menu_dir(layout.OLD_PRODUCT), ignore_errors=True)
        try:
            import winreg

            winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, layout.OLD_UNINSTALL_KEY)
        except (OSError, ImportError):
            pass

    def firewall(self, script: Path, port: int, redirect_port: int, remove: bool = False) -> None:
        args = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ]
        args += ["-Remove"] if remove else ["-Port", str(port), "-RedirectPort", str(redirect_port)]
        code, out, err = self.run(args)
        if code != 0:
            raise SystemError_(
                f"Windows didn't let the installer open port {port} for your devices.",
                f"firewall.ps1 exited {code}: {(err or out).strip()[:2000]}",
            )

    def register_updater_task(self, program_dir: Path) -> None:
        """The task the in-app updater asks Windows to run. It runs one fixed
        program from the Program Files folder (which only Administrators can
        change) with one fixed argument, with the rights an update needs.
        Nothing the apps send becomes part of it. Also the task that
        opens the desktop app again after an update, in the person's own
        session (the update itself runs where no window can show)."""
        user = autostart.current_user()
        for name, xml in (
            (
                layout.UPDATER_TASK,
                updater_task_xml(user, str(program_dir / appinfo.CLI_EXE), str(program_dir)),
            ),
            (
                layout.REOPEN_TASK,
                updater_task_xml(
                    user,
                    str(program_dir / appinfo.DESKTOP_EXE),
                    str(program_dir),
                    name=layout.REOPEN_TASK,
                    description="Opens Command Block again after an update closed it.",
                    arguments="--after-update",
                    logon="InteractiveToken",
                    run_level="LeastPrivilege",
                ),
            ),
        ):
            self._create_task(name, xml)

    def _create_task(self, name: str, xml: str) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as fh:
            fh.write(xml)
            path = fh.name
        try:
            code, out, err = self.run(["schtasks.exe", "/Create", "/TN", name, "/XML", path, "/F"])
        finally:
            Path(path).unlink(missing_ok=True)
        if code != 0:
            raise SystemError_(
                f"Windows didn't accept the task {name!r}.",
                f"schtasks /Create {name} exited {code}: {(err or out).strip()}",
            )

    def remove_updater_task(self) -> None:
        for name in (layout.UPDATER_TASK, layout.REOPEN_TASK):
            self.run(["schtasks.exe", "/Delete", "/TN", name, "/F"])

    def close_app(self) -> None:
        """The desktop app runs from the program folder, so it must be shut
        before the folder is swapped. It was asked to close over the live
        feed already; this is for a copy that wasn't listening."""
        if IS_WINDOWS:
            self.run(["taskkill.exe", "/IM", appinfo.DESKTOP_EXE, "/F"])

    def reopen_app(self, program_dir: Path | None = None) -> None:
        """Open the desktop app again after an update from the app. The task
        runs it in the person's session only if they're signed in to Windows,
        and the app stays shut if it wasn't open before (--after-update).
        A copy for connecting only has no task: its update runs as the
        person already, so it starts the app itself (``program_dir``)."""
        if not IS_WINDOWS:
            return
        if program_dir is None:
            self.run(["schtasks.exe", "/Run", "/TN", layout.REOPEN_TASK])
            return
        from .apply_update import start

        start(program_dir / appinfo.DESKTOP_EXE, ["--after-update"])

    def relaunch_as_admin(self, program: Path, arguments: list[str]) -> bool:
        """Start the setup again with Administrator rights (Windows asks
        first). False when it couldn't, or the person said No."""
        if not IS_WINDOWS:
            return False
        import ctypes
        import subprocess

        shell = ctypes.windll.shell32
        code = shell.ShellExecuteW(
            None, "runas", str(program), subprocess.list2cmdline(arguments), None, 1
        )
        return int(code) > 32  # 32 or less is an error; 5 is "No" at the prompt

    def create_shortcuts(
        self, program_dir: Path, folder: Path | None = None, desktop: bool = False
    ) -> list[str]:
        """Start Menu: open the desktop app, and the setup (repair or remove).
        ``folder``: the person's own Start menu folder, for a copy for
        connecting only, which may also put the app on the desktop."""
        folder = folder or layout.start_menu_dir()
        folder.mkdir(parents=True, exist_ok=True)
        app = program_dir / appinfo.DESKTOP_EXE
        links = [
            (_ps_quote(str(folder / f"{layout.PRODUCT}.lnk")), app, "", "Open Command Block"),
            (
                _ps_quote(str(folder / f"{appinfo.SETUP_SHORTCUT}.lnk")),
                program_dir / layout.SETUP_COPY,
                "",
                "Repair, update or remove Command Block, or install Java",
            ),
        ]
        if desktop:
            links.append((DESKTOP_LINK, app, "", "Open Command Block"))
        made = []
        for link, target, arguments, description in links:
            script = (
                "$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
                f"{link}); "
                f"$s.TargetPath = {_ps_quote(str(target))}; "
                f"$s.Arguments = {_ps_quote(arguments)}; "
                f"$s.WorkingDirectory = {_ps_quote(str(program_dir))}; "
                f"$s.Description = {_ps_quote(description)}; "
                f"$s.IconLocation = {_ps_quote(str(target) + ',0')}; "
                "$s.Save()"
            )
            code, out, err = self.powershell(script)
            if code != 0:
                raise SystemError_(
                    "The Start menu shortcut couldn't be made.",
                    f"PowerShell exited {code}: {(err or out).strip()}",
                )
            made.append(link.strip("'"))
        return made

    def remove_shortcuts(self) -> None:
        import shutil

        shutil.rmtree(layout.start_menu_dir(), ignore_errors=True)

    def open_at_sign_in(self, program_dir: Path | None) -> None:
        """A copy for connecting only: "Open when Windows starts" (None: off)."""
        from desktop.pc import write_run_entry

        write_run_entry(str(program_dir / appinfo.DESKTOP_EXE) if program_dir else None)

    def remove_user_entries(self) -> None:
        """What a copy for connecting only added for the person: its Start
        menu folder, desktop shortcut, start-with-Windows entry, "Installed
        apps" entry and the name of its Windows alerts."""
        import shutil

        shutil.rmtree(layout.user_start_menu_dir(), ignore_errors=True)
        self.powershell(f"Remove-Item -LiteralPath {DESKTOP_LINK} -ErrorAction SilentlyContinue")
        self.open_at_sign_in(None)
        self.remove_uninstall_entry(per_user=True)
        self.remove_alerts_entry()

    def remove_alerts_entry(self) -> None:
        """The desktop app's Windows alerts entry (desktop/pc.py APP_ID)."""
        from desktop.pc import write_app_id

        write_app_id(None)

    def sign_out_here(self) -> tuple[list[str], list[str]]:
        """A copy for connecting only: sign this device out on the owner's PC
        when it answers, then forget the saved sign-in and the app's own
        settings. Returns (what was done, notes for the person)."""
        from PySide6.QtCore import QSettings

        from desktop.agent import AgentClient, AgentError
        from desktop.connect import read_link
        from desktop.pc import WindowsPC

        pc, done, notes = WindowsPC(), [], []
        try:
            found = read_link(str(pc.setting("pc_link") or ""))
        except AgentError:
            found = None
        token = pc.saved_sign_in(found["host"]) if found else None
        if found and token:
            agent = AgentClient.connect(found["host"], found["port"], found["fingerprint"])
            agent.token = token
            try:
                agent.sign_out()
                done.append(f"signed this device out on {found['host']}")
            except AgentError:
                notes.append(
                    f"{found['host']} didn't answer, so this device is still listed there. "
                    "Its owner can remove it in Signed-in devices."
                )
            pc.forget_sign_in(found["host"])
            done.append("removed the saved sign-in")
        QSettings("Command Block", "Command Block").clear()
        return done, notes

    def write_uninstall_entry(
        self, program_dir: Path, version: str, size_kb: int, per_user: bool = False
    ) -> None:
        import winreg

        values = {
            "DisplayName": layout.PRODUCT,
            "DisplayVersion": version,
            "Publisher": layout.PUBLISHER,
            "InstallLocation": str(program_dir),
            "DisplayIcon": str(program_dir / appinfo.AGENT_EXE),
            "UninstallString": f'"{program_dir / layout.SETUP_COPY}" --uninstall',
            "QuietUninstallString": f'"{program_dir / layout.SETUP_COPY}" --uninstall --quiet',
            "URLInfoAbout": "https://github.com/peyoteheadlights/command-block",
        }
        hive = winreg.HKEY_CURRENT_USER if per_user else winreg.HKEY_LOCAL_MACHINE
        with winreg.CreateKeyEx(hive, layout.UNINSTALL_KEY, 0, winreg.KEY_WRITE) as key:
            for name, value in values.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
            winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 0)

    def remove_uninstall_entry(self, per_user: bool = False) -> None:
        try:
            import winreg

            hive = winreg.HKEY_CURRENT_USER if per_user else winreg.HKEY_LOCAL_MACHINE
            winreg.DeleteKey(hive, layout.UNINSTALL_KEY)
        except (OSError, ImportError):
            pass

    def secure_data_folder(self, root: Path) -> None:
        """The data folder holds the private key and sessions: only this
        person's account, SYSTEM and Administrators may read it. The person
        keeps full access, so the app (which runs as them, without
        Administrator rights) can write there."""
        from agent.security.certs import secure_directory

        secure_directory(root)

    def install_msi(self, command: list[str]) -> None:
        code, out, err = self.run(command, timeout=900)
        if code not in (0, 3010):  # 3010: done, a restart finishes it
            raise SystemError_(
                "Java didn't install.",
                f"msiexec exited {code}: {(err or out).strip()[:2000]}",
            )


def updater_task_xml(
    user: str,
    command: str,
    working_directory: str,
    name: str = layout.UPDATER_TASK,
    description: str = "Installs a checked update of Command Block when the owner starts one "
    "from the app.",
    arguments: str = "apply-update",
    logon: str = "S4U",
    run_level: str = "HighestAvailable",
) -> str:
    """A task with no triggers: it runs only when asked, as the person who
    installed the app, with the highest rights their account has (an update
    replaces files under Program Files). S4U means no password is stored.
    The reopen task instead runs in their session (InteractiveToken), with
    normal rights."""
    from xml.sax.saxutils import escape

    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        f'<Task version="1.2" xmlns="{autostart.TASK_NS}">\n'
        "  <RegistrationInfo>\n"
        f"    <Author>{escape(user)}</Author>\n"
        f"    <Description>{escape(description)}</Description>\n"
        f"    <URI>\\{escape(name)}</URI>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers />\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"      <UserId>{escape(user)}</UserId>\n"
        f"      <LogonType>{logon}</LogonType>\n"
        f"      <RunLevel>{run_level}</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <AllowStartOnDemand>true</AllowStartOnDemand>\n"
        "    <Enabled>true</Enabled>\n"
        "    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{escape(command)}</Command>\n"
        f"      <Arguments>{escape(arguments)}</Arguments>\n"
        f"      <WorkingDirectory>{escape(working_directory)}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )
