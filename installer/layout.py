"""The fixed places an installed copy uses.

Program files go under Program Files and everything of yours (settings,
secrets, certificates, database, backups, crash evidence, logs) goes in one
app-data folder, so an update never depends on where anyone clicked.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from agent.config import default_data_root

PRODUCT = "Command Block"
PUBLISHER = "peyoteheadlights"
# The key under ...\CurrentVersion\Uninstall that lists the app in
# "Installed apps".
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CommandBlock"
UPDATER_TASK = "Command Block updater"
REOPEN_TASK = "Command Block reopen"  # opens the desktop app after an update
SETUP_COPY = "setup.exe"  # the installer kept in the program folder (repair and uninstall)
START_MENU_FOLDER = PRODUCT

# The names the app had before it became Command Block. An install of it is
# found under these names and upgraded (detect.py, engine.py).
OLD_PRODUCT = "Minecraft Server Controller"
OLD_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\MinecraftServerController"
OLD_UPDATER_TASK = "Minecraft Server Controller updater"
OLD_FIREWALL_RULES = (
    "Minecraft Server Control (HTTPS, Tailscale only)",
    "Minecraft Server Control (HTTP redirect, Tailscale only)",
)

RESTORE_POINTS = "restore-points"
UPDATES = "updates"
KEEP_RESTORE_POINTS = 3
KEEP_INSTALL_LOGS = 10


def default_program_dir(product: str = PRODUCT) -> Path:
    base = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files"
    return Path(base) / product


def data_root() -> Path:
    return default_data_root()


# "Connect to someone else's PC" installs for one Windows user, with no
# Administrator rights: the program, its install logs and its Start menu
# folder all live in that person's own profile.
def _local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def user_program_dir() -> Path:
    return _local_app_data() / "Programs" / PRODUCT


def user_data_root() -> Path:
    return _local_app_data() / PRODUCT


def user_start_menu_dir() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / START_MENU_FOLDER


def settings_dir(root: Path) -> Path:
    return root / "config"


def config_path(root: Path) -> Path:
    return settings_dir(root) / "config.yaml"


def env_path(root: Path) -> Path:
    return settings_dir(root) / ".env"


def logs_dir(root: Path) -> Path:
    return root / "logs"


def restore_points_dir(root: Path) -> Path:
    return root / RESTORE_POINTS


def updates_dir(root: Path) -> Path:
    return root / UPDATES


def start_menu_dir(folder: str = START_MENU_FOLDER) -> Path:
    base = os.environ.get("ProgramData") or r"C:\ProgramData"
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / folder


_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str | None) -> tuple[int, int, int] | None:
    """1.2.3 (anything after the third number is ignored), or None."""
    match = _VERSION_RE.match(text or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def compare_versions(new: str, old: str | None) -> str:
    """'newer', 'same', 'older' or 'unknown' (the old version can't be read)."""
    a, b = parse_version(new), parse_version(old)
    if a is None or b is None:
        return "unknown"
    return "newer" if a > b else "same" if a == b else "older"
