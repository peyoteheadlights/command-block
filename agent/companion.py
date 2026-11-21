"""Settings for Command Block Companion, the small mod that checks a
player has the right modpack when they join (it never stops cheats: the
whitelist stays the real gate).

Each time a modpack is made, two small files come out of it:

  * on the server, the check's settings: the modpack's name and version,
    the SHA-256 and size of every mod jar players load from it (and where
    the server keeps its own copy, for spot checks), the download link,
    the chat line for players without it and how often to check
    (``config/commandblock-companion.json``, or the plugin's folder on a
    plugin server). The server-side Companion reads it
  * inside the modpack, for players: ``client-overrides/`` gets the
    modpack's name and version, so a player's Companion can say which
    version they have

Only hashes of the modpack's own files go in, never anything from a
player's PC. The Java side refuses unknown fields, so a new field means a
new ``format``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from .security.paths import is_inside

log = logging.getLogger("msc.companion")

FORMAT = 1
MOD_ID = "commandblock-companion"  # the mod's id in fabric.mod.json / neoforge.mods.toml
MODS_FILE = "config/commandblock-companion.json"
PLUGIN_FILE = "plugins/CommandBlockCompanion/config.json"
PLAYERS_FILE = "config/commandblock-companion-client.json"
MAX_FILES = 4000  # the Java side refuses more
CHECK = {
    "timeout_seconds": 10,
    "recheck_minutes": [10, 30],  # a new check at a random time in between
    "spot_checks": 3,  # random byte ranges of random files, fresh each time
    "handshakes_per_ip_per_minute": 6,
}
# The owner may reword it; {name}, {version} and {link} are filled in.
CHAT_LINE_DEFAULT = "This server uses the modpack {name} {version}. {get_it}"
GET_IT = "Get it here: {link}"
GET_IT_NO_LINK = "Get it from the server's owner."
CHAT_LINE_MAX = 300
CHAT_LINE_RE = re.compile(rf"[^\x00-\x1f\x7f]{{1,{CHAT_LINE_MAX}}}")
PLACEHOLDER_RE = re.compile(r"\{(\w*)\}")
PLACEHOLDERS = {"name", "version", "link", "get_it"}


def chat_line_ok(text: str) -> bool:
    """One line, not too long, and only our own {placeholders} (never
    Python's format fields, so nothing else can be read through it)."""
    if not CHAT_LINE_RE.fullmatch(text or ""):
        return False
    rest = PLACEHOLDER_RE.sub(lambda m: "" if m.group(1) in PLACEHOLDERS else "{", text)
    return "{" not in rest and "}" not in rest


def chat_text(template: str, name: str, version: str, link: str | None) -> str:
    """Fills in the placeholders. Plain replace, never str.format."""
    words = {
        "get_it": GET_IT.replace("{link}", link) if link else GET_IT_NO_LINK,
        "name": name,
        "version": version,
        "link": link or "",
    }
    # The Java side refuses more than 1024, which a link put in twice could pass.
    return PLACEHOLDER_RE.sub(lambda m: words[m.group(1)], template)[:1000]


def on_server(mods: Any) -> bool:
    """The Companion is an enabled mod on this server: then the modpack can
    be "Required to join"."""
    return any(m.enabled and m.mod_id == MOD_ID for m in mods.scan())


def players_file(name: str, version: str) -> str:
    """The players' half, put in the modpack's client-overrides/."""
    return json.dumps({"format": FORMAT, "modpack": {"name": name, "version": version}}, indent=1)


def write(
    server_dir: Path,
    content: str | None,
    name: str,
    version: str,
    files: list[dict[str, Any]],
    link: str | None,
    required: bool = False,
    chat_line: dict[str, Any] | None = None,
) -> Path | None:
    """Write (or replace) the server's settings. Returns where, or None
    when this server can't run the Companion or the list is too long."""
    relative = {"mods": MODS_FILE, "plugins": PLUGIN_FILE}.get(content or "")
    if relative is None:
        return None
    target = server_dir / relative
    if not is_inside(server_dir, target.parent):
        log.warning("Not writing %s: its folder leads outside the server folder", relative)
        return None
    if len(files) > MAX_FILES:
        # A cut list would refuse honest players, an old one too.
        log.warning("Not writing %s: %d mod files, more than %d", relative, len(files), MAX_FILES)
        target.unlink(missing_ok=True)
        return None
    chat_line = chat_line or {"on": True, "text": CHAT_LINE_DEFAULT}
    text = chat_text(chat_line["text"], name, version, link)
    data = {
        "format": FORMAT,
        "required": required,
        "modpack": {"name": name, "version": version},
        "files": files,
        "download_link": link,
        "chat_line": {"on": bool(chat_line["on"]), "text": text},
        "check": CHECK,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.part")
    temp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(temp, target)
    return target


def player_files(base: Path, items: list[Any]) -> list[dict[str, Any]]:
    """The mod jars players load: Players or Both, not plugins. ``on_server``
    is the server's own copy, which the check reads for spot checks."""
    files = []
    for i in items:
        if i.side == "server" or i.plugin or not i.path.startswith("mods/"):
            continue
        inside = is_inside(base, i.source) and not i.source.is_symlink()
        files.append(
            {
                "path": i.path,
                "name": ((i.need or {}).get("name") or i.name)[:200],
                "sha256": i.sha256,
                "size": i.size,
                "on_server": i.source.resolve().relative_to(base.resolve()).as_posix()
                if inside
                else None,
            }
        )
    return files
