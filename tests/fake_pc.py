"""A pretend Windows for the desktop app's tests: it records what the app
asked Windows to do instead of doing it."""

from __future__ import annotations

from desktop.pc import PCError, WindowsPC
from desktop.wake import Helper


class FakePC(WindowsPC):
    def __init__(self) -> None:
        self.opened: list[str] = []
        self.files: dict[str, bytes] = {}  # saved with the save box, by name
        self.save_answer: str | None = "C:/Users/test/Downloads"  # None: Cancel
        self.save_to: str | None = None  # where the save box puts a big file; None: Cancel
        self.picked: str | None = None  # what a file or folder picker chooses; None: Cancel
        self.picker_titles: list[str] = []
        self.saved: dict[str, str] = {}  # the pretend Credential Manager
        self.scale = 1.0  # Windows' text size, 1.0 to 2.25
        self.alerts: list[tuple[str, str]] = []  # Windows alerts shown, as (title, body)
        self.alert_keys: list[tuple[str, tuple]] = []  # and each one's key and buttons
        self.alert_clicked = lambda key, action: None
        self.settings: dict = {}
        self.starts_with_windows = False  # the HKCU Run entry
        self.tray_menu = None
        self.tray_words = ""  # the icon's words; "crashed" stands for the red dot
        self.tray_shown = False
        self.agent_starts = 0  # "Start the agent" clicks that reached Windows
        self.setups_opened = 0  # "Install the right Java" clicks that reached Windows
        # The Tailscale step: what it ran, and what each step says went wrong.
        self.tailscale_installs = 0
        self.tailscale_ups = 0
        self.tailscale_logouts = 0
        self.install_problem: str | None = None
        self.up_problem: str | None = None
        self.sign_in_page = "https://login.tailscale.com/a/test"
        self.updates_run: list[tuple[str, str]] = []  # (setup, program folder)
        self.ts_state = {"cli_found": True, "connected": True, "error": None}
        # Wake-on-LAN: signals sent as (mac, broadcast), requests to the helping PC.
        self.wakes: list[tuple[str, str | None]] = []
        self.asked: list[tuple[str, int, str]] = []
        self.ts_address: str | None = "100.64.0.9"
        self.helping: str | None = None  # the address it listens on
        self.helper = Helper(lambda mac, broadcast: self.wakes.append((mac, broadcast)))

    def device_name(self) -> str:
        return "Desktop app on TEST-PC"

    def open_link(self, url: str) -> None:
        self.opened.append(url)

    def save_file(self, name: str, data: bytes) -> str | None:
        if self.save_answer is None:
            return None
        self.files[name] = data
        return f"{self.save_answer}/{name}"

    def save_path(self, name: str) -> str | None:
        return self.save_to

    def pick_file(self, title: str, kinds: str) -> str | None:
        self.picker_titles.append(title)
        return self.picked

    def pick_folder(self, title: str) -> str | None:
        self.picker_titles.append(title)
        return self.picked

    def saved_sign_in(self, place: str) -> str | None:
        return self.saved.get(place)

    def save_sign_in(self, place: str, token: str) -> None:
        self.saved[place] = token

    def forget_sign_in(self, place: str) -> None:
        self.saved.pop(place, None)

    def text_scale(self) -> float:
        return self.scale

    def show_alert(self, title: str, body: str, key: str = "", buttons=()) -> None:
        self.alerts.append((title, body))
        self.alert_keys.append((key, tuple(buttons)))

    def when_alert_clicked(self, callback) -> None:
        self.alert_clicked = callback

    def buttons(self, index: int = -1) -> list[str]:
        """The words on a Windows alert's buttons (the last one's by default)."""
        return [words for words, _action in self.alert_keys[index][1]]

    def click_alert(self, button: str = "", index: int = -1) -> None:
        """A person clicks a Windows alert (the last one), or its ``button``."""
        key, buttons = self.alert_keys[index]
        self.alert_clicked(key, next((a for w, a in buttons if w == button), ""))

    def setting(self, name: str):
        return self.settings.get(name)

    def set_setting(self, name: str, value) -> None:
        self.settings[name] = value

    def set_start_with_windows(self, on: bool) -> None:
        self.starts_with_windows = on

    def set_tray_look(self, icon, words: str) -> None:
        self.tray_words, self.tray_shown = words, True

    def set_tray_menu(self, menu) -> None:
        self.tray_menu = menu

    def when_tray_clicked(self, callback) -> None:
        pass

    def hide_tray(self) -> None:
        self.tray_shown = False

    def start_agent(self) -> str | None:
        self.agent_starts += 1
        return None

    def open_setup(self) -> None:
        self.setups_opened += 1

    def install_tailscale(self) -> None:
        self.tailscale_installs += 1
        if self.install_problem:
            raise PCError(self.install_problem)

    def tailscale_up(self) -> str | None:
        self.tailscale_ups += 1
        if self.up_problem:
            raise PCError(self.up_problem)
        return self.sign_in_page

    def tailscale_logout(self) -> None:
        self.tailscale_logouts += 1

    def send_wake(self, mac: str, broadcast: str | None) -> None:
        self.wakes.append((mac, broadcast))

    def ask_helper(self, address: str, port: int, key: str) -> None:
        self.asked.append((address, port, key))

    def run_update(self, setup, program_dir) -> None:
        self.updates_run.append((str(setup), str(program_dir)))

    def tailscale_state(self) -> dict:
        return dict(self.ts_state)

    def tailscale_address(self) -> str | None:
        return self.ts_address

    def help_wake(self, address: str | None) -> None:
        self.helping = address
