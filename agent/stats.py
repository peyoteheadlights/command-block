"""A player's stats, for /stats, Levels and (later) quests.

Java worlds keep each player's numbers in ``stats/<uuid>.json`` and their
advancements in ``advancements/<uuid>.json``, inside the world folder named
by ``level-name``. Minecraft only writes them every few minutes and when the
player leaves, so every answer carries the time the file was saved and is
never shown as live. Only what the files hold is returned: a stat that isn't
there is left out, not shown as 0, and a mod's own stats keep their own
names.

Server types without these files (Bedrock, ``ServerType.stats_files``) get
what the console showed instead: joins and playtime from Command Block's own
player sessions.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core import ServerContext

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_BYTES = 4 * 1024 * 1024  # a modded stats file is tens of KB
TICKS = 20  # per second
# minecraft:custom keys, newest name first (play_one_minute until 1.17,
# counted in ticks despite its name).
CUSTOM = {
    "playtime": ("minecraft:play_time", "minecraft:play_one_minute"),
    "deaths": ("minecraft:deaths",),
    "mobs": ("minecraft:mob_kills",),
}
OTHER_SHOWN = 5  # a mod's stats: the biggest few


def find_player(ctx: ServerContext, name: str) -> dict[str, Any] | None:
    return ctx.db.query_one(
        "SELECT username, uuid, total_seconds, session_started, sessions FROM players "
        "WHERE server_id = ? AND username = ? COLLATE NOCASE",
        (ctx.server_id, name),
    )


def _load(path: Path) -> dict | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def raw_files(world: Path, uuid: str) -> tuple[dict, dict, float | None]:
    """One player's files as Minecraft wrote them: the stats sections
    ("minecraft:mined" -> {"minecraft:stone": 12}), the advancements and
    the newest file's saved time. Empty when there's no file (yet)."""
    sections: dict = {}
    earned: dict = {}
    saved: list[float] = []
    if not UUID_RE.match(uuid):
        return sections, earned, None
    for folder in ("stats", "advancements"):
        path = world / folder / f"{uuid}.json"
        data = _load(path)
        if folder == "stats":
            data = (data or {}).get("stats")
        if isinstance(data, dict):
            saved.append(path.stat().st_mtime)
            if folder == "stats":
                sections = data
            else:
                earned = data
    return sections, earned, max(saved) if saved else None


def advancement_done(earned: dict, name: str) -> bool:
    found = earned.get(name)
    return isinstance(found, dict) and bool(found.get("done"))


def read_files(world: Path, uuid: str) -> dict[str, Any]:
    """The numbers in one player's files: ``values`` (playtime in seconds,
    deaths, mobs, blocks, advancements), ``other`` (a mod's stats by name)
    and ``saved_at`` (None when there's no file yet)."""
    values: dict[str, int] = {}
    other: list[dict[str, Any]] = []
    sections, earned, saved_at = raw_files(world, uuid)
    if sections:
        custom = sections.get("minecraft:custom")
        custom = custom if isinstance(custom, dict) else {}
        for key, names in CUSTOM.items():
            found = next((custom[n] for n in names if isinstance(custom.get(n), int)), None)
            if found is not None:
                values[key] = found // TICKS if key == "playtime" else found
        mined = sections.get("minecraft:mined")
        if isinstance(mined, dict):
            values["blocks"] = sum(v for v in mined.values() if isinstance(v, int))
        mods = [
            {"name": k, "value": v}
            for k, v in custom.items()
            if isinstance(v, int) and not k.startswith("minecraft:")
        ]
        other = sorted(mods, key=lambda x: -x["value"])[:OTHER_SHOWN]
    if earned:
        # Recipes are "advancements" too in the file; nobody counts those.
        values["advancements"] = sum(
            1 for k in earned if advancement_done(earned, k) and "recipes/" not in k
        )
    return {"values": values, "other": other, "saved_at": saved_at}


async def player_stats(ctx: ServerContext, name: str) -> dict[str, Any] | None:
    """One player's stats on a server, None when nobody of that name played
    there. ``source`` says where they came from: "files" or "console"."""
    player = find_player(ctx, name)
    if player is None:
        return None
    out: dict[str, Any] = {"player": player["username"], "server_id": ctx.server_id}
    if not ctx.config.server_type.stats_files:
        live = time.time() - player["session_started"] if player.get("session_started") else 0
        return {
            **out,
            "source": "console",
            "values": {
                "playtime": int(float(player["total_seconds"] or 0) + live),
                "joins": int(player["sessions"] or 0),
            },
            "other": [],
            "saved_at": None,
        }
    if not player.get("uuid"):
        return {**out, "source": "files", "values": {}, "other": [], "saved_at": None}
    from .addons import world_path

    found = await asyncio.to_thread(read_files, world_path(ctx), str(player["uuid"]))
    return {**out, "source": "files", **found}
