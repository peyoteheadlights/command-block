"""The Companion's title screen, set when making a modpack: a small join
button beside Realms (with the server's icon), a background picture and
one line of yellow flavor text. Each can be off; Minecraft's own stay then.

All of it travels in the modpack's players' half (``client-overrides/``):
the address and the flavor text in the players' file, the icon and the
picture as PNG files next to it. Nothing changes on the server.

The address is the one the owner types: the app never guesses a public
address (see agent/joininfo.py). When it changes, the modpack needs a new
version, or friends' Companions would think they're up to date.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .discord import layout
from .modpack import ModpackError
from .security.paths import is_inside

if TYPE_CHECKING:
    from .core import ServerContext

FOLDER = "config/commandblock-companion"
ICON = f"{FOLDER}/icon.png"
BACKGROUND = f"{FOLDER}/background.png"
BACKGROUND_FILE = "title-background.png"  # in the app's modpack folder for the server
MAX_BACKGROUND_BYTES = 4 * 1024 * 1024
MAX_ICON_BYTES = 256 * 1024
MAX_SIDE = 4096  # the Companion refuses bigger textures
MIN_SIDE = 16
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
FLAVOR_MAX = 60
# host or IPv4, optional port; the Java side checks the same.
ADDRESS_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})(?::(\d{1,5}))?$")
FLAVOR_RE = re.compile(rf"^[^\x00-\x1f\x7f§]{{1,{FLAVOR_MAX}}}$")  # § is Minecraft's colour code
OFF: dict[str, Any] = {
    "button": {"on": False, "address": ""},
    "flavor_text": {"on": False, "text": ""},
    "background": {"on": False},
}


def png_size(data: bytes) -> tuple[int, int] | None:
    """Width and height from a PNG's first chunk, or None if it isn't one."""
    if len(data) < 24 or not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def background_path(folder: Path) -> Path:
    return folder / BACKGROUND_FILE


def save_background(folder: Path, data: bytes) -> None:
    """The owner's picture, checked before it's kept."""
    size = png_size(data)
    if len(data) > MAX_BACKGROUND_BYTES:
        raise ModpackError("The picture is bigger than 4 MB. Make it smaller and try again.")
    if size is None:
        raise ModpackError("Choose a PNG picture.")
    if not all(MIN_SIDE <= side <= MAX_SIDE for side in size):
        raise ModpackError(
            f"The picture must be between {MIN_SIDE} and {MAX_SIDE} pixels wide and high."
        )
    temp = folder / f".{BACKGROUND_FILE}.part"
    temp.write_bytes(data)
    temp.replace(background_path(folder))


def check(settings: dict[str, Any] | None, folder: Path) -> dict[str, Any]:
    """The settings as sent, checked, with every part present."""
    got = {**OFF, **(settings or {})}
    button, flavor = got["button"], got["flavor_text"]
    if button["on"]:
        match = ADDRESS_RE.match(button["address"] or "")
        if not match or (match.group(1) and not 1 <= int(match.group(1)) <= 65535):
            raise ModpackError(
                "Type the address friends join with, like play.example.net or 203.0.113.5:25570."
            )
    if flavor["on"] and not FLAVOR_RE.match(flavor["text"] or ""):
        raise ModpackError(
            f"The flavor text is one line of up to {FLAVOR_MAX} characters, without colour codes."
        )
    if got["background"]["on"] and not background_path(folder).is_file():
        raise ModpackError("Pick the background picture first, or untick Background.")
    return got


def address_moved(last: dict[str, Any], settings: dict[str, Any], version: str) -> bool:
    """The button's address changed but the version didn't."""
    before = (last.get("title_screen") or OFF)["button"]
    now = settings["button"]
    return (
        bool(before.get("on") and now["on"])
        and before.get("address") != now["address"]
        and last.get("version") == version
    )


def options(ctx: ServerContext, last: dict[str, Any], folder: Path) -> dict[str, Any]:
    """For the Make a modpack screen: last time's settings, else the address
    the owner gave the Discord bot."""
    saved = last.get("title_screen") or {
        **OFF,
        "button": {"on": False, "address": layout.addresses(ctx.core.db).get(ctx.server_id, "")},
    }
    return {**saved, "background_set": background_path(folder).is_file()}


def players_part(name: str, settings: dict[str, Any]) -> dict[str, Any]:
    """The title_screen part of the players' file (agent/companion.py)."""
    return {
        "name": name[:64],
        "address": settings["button"]["address"] if settings["button"]["on"] else None,
        "flavor_text": settings["flavor_text"]["text"] if settings["flavor_text"]["on"] else None,
    }


def files(server_dir: Path, settings: dict[str, Any], folder: Path) -> dict[str, bytes]:
    """The PNGs that go in client-overrides/: the server's icon with the
    button, the picture with the background."""
    out: dict[str, bytes] = {}
    icon = server_dir / "server-icon.png"
    if (
        settings["button"]["on"]
        and icon.is_file()
        and not icon.is_symlink()
        and is_inside(server_dir, icon)
    ):
        data = icon.read_bytes() if icon.stat().st_size <= MAX_ICON_BYTES else b""
        size = png_size(data)
        if size and all(MIN_SIDE <= side <= 512 for side in size):
            out[ICON] = data
    if settings["background"]["on"]:
        out[BACKGROUND] = background_path(folder).read_bytes()
    return out
