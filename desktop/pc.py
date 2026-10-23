"""Everything the desktop app asks of Windows, in one place.

The rest of the app only talks to the agent's API. What it needs from the PC
itself goes through this class, so a Mac version can add its own later and
the tests can use a pretend one (tests/fake_pc.py). House rule 2: these are
the only things the app does on the PC, each after a click.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QSettings, QStandardPaths, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from agent.winproc import NO_WINDOW

from . import icons, wake
from .agent import AgentError
from .words import text

log = logging.getLogger(__name__)

NOT_FOUND = 1168  # ERROR_NOT_FOUND: nothing saved under that name
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Windows' name for the app's alerts (an App User Model ID). Buttons on an
# alert need one Windows knows; an app that isn't from the Store registers
# it under HKCU, so it needs no admin and works for every Windows user.
APP_ID = "CommandBlock.Desktop"
APP_ID_KEY = rf"Software\Classes\AppUserModelId\{APP_ID}"
TAILSCALE_SIGNER = "Tailscale Inc."
CANCELLED = 1223  # ERROR_CANCELLED: the UAC prompt was answered No
DETACHED = 0x00000008 | 0x00000200 | NO_WINDOW  # on its own, in a new process group
SIGN_IN_PAGE = re.compile(r"https://login\.tailscale\.com/\S+")
# File paths reach PowerShell in an environment variable, never in its code.
POWERSHELL = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
SIGNER_SCRIPT = (
    "$s = Get-AuthenticodeSignature -LiteralPath $env:CB_FILE; "
    "if ($s.Status -eq 'Valid') { $s.SignerCertificate.GetNameInfo('SimpleName', $false) }"
)
INSTALL_SCRIPT = (
    "try { $p = Start-Process msiexec.exe -Verb RunAs -Wait -PassThru "
    "-ArgumentList ('/i \"' + $env:CB_FILE + '\" /quiet /norestart'); exit $p.ExitCode } "
    f"catch {{ exit {CANCELLED} }}"
)


# Opens a program the way Explorer does, so Windows asks UAC when it needs to.
OPEN_SCRIPT = f"try {{ Start-Process -FilePath $env:CB_FILE; exit 0 }} catch {{ exit {CANCELLED} }}"


class PCError(AgentError):
    """Something on this PC didn't work, in words a person can read."""


def write_run_entry(exe: str | None) -> None:
    """The HKCU Run entry that opens the app, hidden, when Windows starts
    (None removes it). The installer writes it too, on a moderator's PC."""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if exe:
            winreg.SetValueEx(key, "Command Block", 0, winreg.REG_SZ, f'"{exe}" --hidden')
        else:
            try:
                winreg.DeleteValue(key, "Command Block")
            except FileNotFoundError:
                pass


def write_app_id(icon: str | None) -> None:
    """The entry that names the app's Windows alerts "Command Block", with
    its icon (None removes it, when the app is removed)."""
    import winreg

    if icon is None:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, APP_ID_KEY)
        except FileNotFoundError:
            pass
        return
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, APP_ID_KEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "Command Block")
        winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon)


class _Clicks(QObject):
    # Windows calls back on a thread of its own; the signal brings the
    # click to the window's thread.
    clicked = Signal(str, str)


def _target(place: str) -> str:
    return f"Command Block sign-in ({place})"


class WindowsPC:
    def __init__(self) -> None:
        self._tray: QSystemTrayIcon | None = None
        self._toaster: Any = None  # windows-toasts once set up; False: tray messages
        self._clicks = _Clicks()
        self._last_key = ""  # what the last tray message was about
        self.helper = wake.Helper(self.send_wake)  # "Help wake <PC>" (desktop/wake.py)

    def device_name(self) -> str:
        """What Signed-in devices calls this sign-in."""
        return f"Desktop app on {platform.node() or 'this PC'}"

    def open_link(self, url: str) -> None:
        # Links can come from the agent (a suggestion's "Get Java"), maybe on
        # someone else's PC; on Windows a path here would run a program.
        if url.startswith("https://"):
            webbrowser.open(url)

    def save_path(self, name: str) -> str | None:
        """Windows' own save box, starting in Downloads with ``name``. The
        place chosen, or None when they cancelled."""
        from PySide6.QtWidgets import QFileDialog

        start = str(Path.home() / "Downloads" / name)
        path, _kind = QFileDialog.getSaveFileName(None, text("desktop.save_title"), start)
        return path or None

    def save_file(self, name: str, data: bytes) -> str | None:
        """The save box, then writes ``data`` where the person chose."""
        path = self.save_path(name)
        if not path:
            return None
        try:
            Path(path).write_bytes(data)
        except OSError as exc:
            raise PCError(text("desktop.save_failed", reason=exc.strerror or exc)) from exc
        return path

    # Windows' own pickers. They show this PC's files, which on a moderator's
    # PC aren't the server's PC's.
    def pick_file(self, title: str, kinds: str) -> str | None:
        """One file, like ``kinds="Worlds (*.zip *.mcworld)"``; None when cancelled."""
        from PySide6.QtWidgets import QFileDialog

        path, _kind = QFileDialog.getOpenFileName(None, title, str(Path.home()), kinds)
        return path or None

    def pick_folder(self, title: str) -> str | None:
        from PySide6.QtWidgets import QFileDialog

        return QFileDialog.getExistingDirectory(None, title, str(Path.home())) or None

    def text_scale(self) -> float:
        """Windows' "Text size" setting (Accessibility), 1.0 to 2.25."""
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Accessibility"
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "TextScaleFactor")
        except (ImportError, OSError):
            return 1.0
        return min(2.25, max(1.0, int(value) / 100))

    # "Keep me signed in" goes in Windows Credential Manager, never in a file.
    def saved_sign_in(self, place: str) -> str | None:
        from win32ctypes.pywin32 import pywintypes, win32cred

        try:
            found = win32cred.CredRead(_target(place), win32cred.CRED_TYPE_GENERIC)
        except pywintypes.error as exc:
            if exc.winerror == NOT_FOUND:
                return None
            raise
        return found["CredentialBlob"].decode("utf-16-le")

    def save_sign_in(self, place: str, token: str) -> None:
        from win32ctypes.pywin32 import win32cred

        win32cred.CredWrite(
            {
                "Type": win32cred.CRED_TYPE_GENERIC,
                "TargetName": _target(place),
                "UserName": "Command Block",
                "CredentialBlob": token,
                "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            },
            0,
        )

    def forget_sign_in(self, place: str) -> None:
        from win32ctypes.pywin32 import pywintypes, win32cred

        try:
            win32cred.CredDelete(_target(place), win32cred.CRED_TYPE_GENERIC)
        except pywintypes.error as exc:
            if exc.winerror != NOT_FOUND:
                raise

    # The app's own small settings (HKCU\Software\Command Block).
    def setting(self, name: str) -> Any:
        return QSettings("Command Block", "Command Block").value(name)

    def set_setting(self, name: str, value: Any) -> None:
        QSettings("Command Block", "Command Block").setValue(name, value)

    def set_start_with_windows(self, on: bool) -> None:
        if not getattr(sys, "frozen", False):
            return  # a checkout has no CommandBlock.exe to start
        write_run_entry(sys.executable if on else None)

    def run_update(self, setup: Path, program_dir: Path) -> None:
        """A moderator's PC: run the checked setup file (desktop/self_update.py)
        for this Windows user, on its own, so it outlives the app it replaces."""
        subprocess.Popen(
            [str(setup), "--update", "--quiet", "--from-app", "--program-dir", str(program_dir)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=DETACHED,
        )

    def start_agent(self) -> str | None:
        """Turns the agent's startup task back on if it was turned off, and
        runs it. Returns what went wrong, in words, or None."""
        from installer import autostart

        autostart.run_tool(["schtasks.exe", "/Change", "/TN", autostart.TASK_NAME, "/ENABLE"])
        try:
            autostart.run_now()
        except autostart.AutostartError as exc:
            return str(exc)
        return None

    # The Tailscale step (on the agent's PC only). Tailscale's own setup and
    # its own command, each with a fixed argument list.
    def install_tailscale(self) -> None:
        """Download Tailscale's setup, refuse it unless Windows says Tailscale
        Inc. signed it, then install it (one UAC prompt)."""
        folder = Path(tempfile.mkdtemp(prefix="command-block-"))
        try:
            msi = self.download_tailscale(folder)
            signer = self.msi_signer(msi)
            if signer != TAILSCALE_SIGNER:
                raise PCError(text("desktop.tailscale.wrong_signer", signer=signer or "-"))
            code = self.install_msi(msi)
            if code == CANCELLED:
                raise PCError(text("desktop.tailscale.uac_refused"))
            if code not in (0, 1641, 3010):  # 1641 and 3010: done, Windows wants a restart
                raise PCError(text("desktop.tailscale.install_failed", code=code))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def download_tailscale(self, folder: Path) -> Path:
        from agent import downloads

        arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
        spec = downloads.FileSpec(
            url=f"https://pkgs.tailscale.com/stable/tailscale-setup-latest-{arch}.msi",
            name="tailscale-setup.msi",  # checked by its signature, not a checksum
        )
        try:
            return asyncio.run(downloads.download(spec, folder)).path
        except (downloads.DownloadError, OSError) as exc:
            raise PCError(text("desktop.tailscale.download_failed", why=exc)) from exc

    def msi_signer(self, path: Path) -> str | None:
        """Who signed the file, when Windows says the signature is valid."""
        try:
            done = subprocess.run(
                [*POWERSHELL, SIGNER_SCRIPT],
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "CB_FILE": str(path)},
                creationflags=NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() or None

    def open_setup(self) -> None:
        """Opens Command Block setup from the program folder, where "Java for
        your servers" installs the Java a server needs. Only on the agent's
        PC, after a click; no arguments, so the person picks the step."""
        from agent import appinfo
        from installer import layout

        setup = appinfo.program_dir() / layout.SETUP_COPY
        if not setup.is_file():
            raise PCError(text("desktop.fix.no_setup"))
        done = subprocess.run(
            [*POWERSHELL, OPEN_SCRIPT],
            env={**os.environ, "CB_FILE": str(setup)},
            creationflags=NO_WINDOW,
        )
        if done.returncode == CANCELLED:
            raise PCError(text("desktop.fix.setup_refused"))

    def install_msi(self, path: Path) -> int:
        """Runs the setup as administrator and waits. Returns its exit code."""
        done = subprocess.run(
            [*POWERSHELL, INSTALL_SCRIPT],
            env={**os.environ, "CB_FILE": str(path)},
            creationflags=NO_WINDOW,
        )
        return done.returncode

    def tailscale_up(self) -> str | None:
        """Runs ``tailscale up --unattended``. Returns Tailscale's sign-in
        page, or None when it connected without one. Left running, it ends
        by itself once the person signs in."""
        from agent.tailscale import tailscale_binary

        binary = tailscale_binary()
        if binary is None:
            raise PCError(text("desktop.tailscale.up_failed", why="tailscale.exe wasn't found"))
        proc = subprocess.Popen(
            [binary, "up", "--unattended"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=NO_WINDOW,
        )
        assert proc.stdout is not None
        # No sign-in page within a minute: stop it rather than wait forever.
        watchdog = threading.Timer(60, proc.kill)
        watchdog.start()
        said = []
        try:
            for line in proc.stdout:
                found = SIGN_IN_PAGE.search(line)
                if found:
                    return found.group(0)
                said.append(line.strip())
        finally:
            watchdog.cancel()
        if proc.wait() == 0:
            return None
        why = " ".join(x for x in said if x) or f"it stopped with error {proc.returncode}"
        raise PCError(text("desktop.tailscale.up_failed", why=why[:300]))

    def tailscale_logout(self) -> None:
        """Before "Use a different account" signs in again."""
        from agent.tailscale import tailscale_binary

        binary = tailscale_binary()
        if binary is None:
            return
        try:
            subprocess.run(
                [binary, "logout"], capture_output=True, timeout=60, creationflags=NO_WINDOW
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PCError(text("desktop.tailscale.up_failed", why=exc)) from exc

    # Wake-on-LAN: the wake signal, only ever to the one PC the agent
    # recorded, and the helping PC's listener on its own Tailscale address.
    def send_wake(self, mac: str, broadcast: str | None) -> None:
        wake.send_wake(mac, broadcast)

    def ask_helper(self, address: str, port: int, key: str) -> None:
        wake.ask_helper(address, port, key)

    def tailscale_state(self) -> dict:
        """What Tailscale on this PC says (agent/tailscale.py): a moderator's
        PC has no agent to ask."""
        from agent.tailscale import tailscale_status

        return tailscale_status()

    def tailscale_address(self) -> str | None:
        """This PC's Tailscale address, while Tailscale says it's connected."""
        report = self.tailscale_state()
        if not report.get("connected"):
            return None
        return next((a for a in report.get("addresses") or [] if ":" not in a), None)

    def help_wake(self, address: str | None) -> None:
        """Listen for wake requests on ``address``; None stops."""
        if address:
            self.helper.listen(address)
        else:
            self.helper.stop()

    # The notification area icon.
    def tray(self) -> QSystemTrayIcon:
        if self._tray is None:
            self._tray = QSystemTrayIcon()
        return self._tray

    def set_tray_look(self, icon: QIcon, words: str) -> None:
        """The icon, and its words for Narrator and the mouse."""
        self.tray().setIcon(icon)
        self.tray().setToolTip(words)
        self.tray().show()

    def set_tray_menu(self, menu: QMenu) -> None:
        self.tray().setContextMenu(menu)

    def when_tray_clicked(self, callback: Callable[[], None]) -> None:
        self.tray().activated.connect(
            lambda why: callback() if why == QSystemTrayIcon.ActivationReason.Trigger else None
        )

    def hide_tray(self) -> None:
        if self._tray is not None:
            self._tray.hide()

    # Windows alerts: toasts with buttons (windows-toasts), or the plain
    # tray message where those can't be shown.
    def _toasts(self) -> Any:
        if self._toaster is None:
            self._toaster = False
            if sys.platform == "win32":
                try:
                    from windows_toasts import InteractableWindowsToaster

                    folder = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
                    folder.mkdir(parents=True, exist_ok=True)
                    icons.tray_icon().pixmap(64).save(str(folder / "alerts.png"))
                    write_app_id(str(folder / "alerts.png"))
                    self._toaster = InteractableWindowsToaster("Command Block", APP_ID)
                except Exception:  # an old Windows, or the entry couldn't be written
                    log.warning("Windows alerts with buttons aren't available", exc_info=True)
        return self._toaster or None

    def show_alert(
        self, title: str, body: str, key: str = "", buttons: tuple[tuple[str, str], ...] = ()
    ) -> None:
        """A Windows alert. Clicking it, or one of its ``buttons`` (words,
        action), calls the when_alert_clicked callback with ``key`` and the
        action ("" for the alert itself)."""
        toaster = self._toasts()
        if toaster is not None:
            from windows_toasts import Toast, ToastButton

            toast = Toast([title, body])
            for words, action in buttons:
                toast.AddAction(ToastButton(words, action))
            toast.on_activated = lambda found: self._clicks.clicked.emit(
                key, "" if found.arguments in (None, toast.tag) else found.arguments
            )
            try:
                toaster.show_toast(toast)
                return
            except Exception:
                log.warning("a Windows alert couldn't be shown", exc_info=True)
        self._last_key = key
        self.tray().showMessage(title, body)

    def when_alert_clicked(self, callback: Callable[[str, str], None]) -> None:
        self._clicks.clicked.connect(callback)
        self.tray().messageClicked.connect(lambda: callback(self._last_key, ""))
