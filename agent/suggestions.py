"""Add-on suggestions for one server: "Good to have", "Often used with your
mods", speed-ups when the server is measured to be slow, "For your
players" and the live map link.

The list is hand-checked and lives in the notes file (agent/notes.py,
``recommended_addons``). Before one is shown it is checked against this
server: its site must have a build for the server's type and Minecraft
version (the same call Install uses), it must not be here already, and
nothing installed may be listed as incompatible with it. Libraries are
never suggested; Install adds them as what an add-on needs.

Where one goes comes from the site's own side fields: one that can't run
on a server goes only in "For your players"; one players need too (Simple
Voice Chat) is installed on the server and listed for players once it's
there.

Nothing here installs anything. Download counts are the site's own, with
the site's name and when they were read (a saved copy keeps its date).
Speed-ups only show with a measured reason, never a guess (house rule 1).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import notes, savedlists, whitelist
from .minecraft.properties import FILENAME, PropertiesFile
from .mods.manager import ModError
from .mods.sources import LABELS, SourceError, split

if TYPE_CHECKING:
    from .core import ServerContext

log = logging.getLogger("msc.suggestions")

# A check asks the sites about every entry, so it is kept this long, or
# until the server's type, version or add-ons change.
KEEP_FOR = 6 * 3600
START_ONCE = "Start the server once so suggestions can be checked against its Minecraft version."
# Minecraft's own defaults when server.properties doesn't set them.
DISTANCE_DEFAULT = 10
# What the Paper speed-up lowers them to (only ever lowered).
PAPER_DISTANCES = {"view-distance": 8, "simulation-distance": 6}

# server id -> (when, what it was checked for, the check)
_checked: dict[str, tuple[float, tuple, dict[str, Any]]] = {}
_running: dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------- the live map
# Map add-ons the app knows: the jar's id, where its settings file is (mods
# servers first, then plugin servers), the key that holds its web port, and
# the port it uses when the file isn't written yet (its first start writes it).
MAPS = {
    "bluemap": (
        "BlueMap",
        ("config/bluemap/webserver.conf", "plugins/BlueMap/webserver.conf"),
        "port",
        8100,
    ),
    "squaremap": (
        "squaremap",
        ("config/squaremap/config.yml", "plugins/squaremap/config.yml"),
        "port",
        8080,
    ),
    "dynmap": (
        "Dynmap",
        ("dynmap/configuration.txt", "plugins/dynmap/configuration.txt"),
        "webserver-port",
        8123,
    ),
}


def map_ports(ctx: ServerContext) -> list[dict[str, Any]]:
    """The installed map add-ons and the web port each one uses, read from
    its own settings file. The port manager counts these; UPnP never opens
    them (they're for Tailscale only)."""
    found = []
    try:
        mods = ctx.mods.scan(use_cache=True)
    except ModError:  # no server folder set yet
        return []
    for mod in mods:
        known = MAPS.get(mod.mod_id.lower())
        if not known or not mod.enabled:
            continue
        name, files, key, default = known
        port: int | None = default
        source = f"{name}'s default (its settings file isn't written yet)"
        for relative in files:
            path = ctx.config.server_dir / relative
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            match = re.search(rf"^\s*{re.escape(key)}\s*[:=]\s*(\d+)\s*$", text, re.MULTILINE)
            if not match or not 0 < int(match.group(1)) < 65536:
                port, source = None, f"{name}'s settings file has no {key} this app can read"
            else:
                port, source = int(match.group(1)), f"{name}'s settings ({relative})"
            break
        found.append({"id": mod.mod_id.lower(), "name": name, "port": port, "source": source})
    return found


def map_links(ctx: ServerContext) -> list[dict[str, Any]]:
    """The "Map" link for each installed map: its page over this PC's
    Tailscale address, so only people on the tailnet can open it."""
    maps = map_ports(ctx)
    if not maps:
        return []
    from .joininfo import tailscale_address

    address = tailscale_address().get("address")
    for item in maps:
        item["link"] = f"http://{address}:{item['port']}/" if address and item["port"] else None
        item["problem"] = (
            None
            if item["link"]
            else "Tailscale isn't connected on this PC, so the map can't be opened from "
            "other devices yet."
            if item["port"]
            else item["source"]
        )
    return maps


# ---------------------------------------------------------------- measured reasons
def measured(samples: list[dict[str, Any]], tps_alert: float, ram_alert: float) -> dict | None:
    """Why this server would gain from a speed-up, from the last hour's
    readings, or None. Never from a value nobody measured."""
    from .recommendations import SLOW_MIN_SAMPLES, SLOW_SHARE

    read = [s for s in samples if s.get("tps") is not None]
    slow = [s for s in read if s["tps"] < tps_alert]
    if len(slow) >= SLOW_MIN_SAMPLES and len(slow) >= SLOW_SHARE * len(read):
        return {
            "why": "lag",
            "evidence": f"Game speed was below {tps_alert:g} of 20 in {len(slow)} of "
            f"{len(read)} readings in the last hour (lowest {min(s['tps'] for s in slow):.1f}).",
        }
    read = [s for s in samples if s.get("ram_percent") is not None]
    full = [s for s in read if s["ram_percent"] >= ram_alert]
    if len(full) >= SLOW_MIN_SAMPLES and len(full) >= SLOW_SHARE * len(read):
        return {
            "why": "memory",
            "evidence": f"The PC's memory was {ram_alert:g}% full or more in {len(full)} of "
            f"{len(read)} readings in the last hour.",
        }
    return None


# ---------------------------------------------------------------- checking the list
def minecraft_of(ctx: ServerContext) -> str | None:
    """What the console printed, or what the app installed and is waiting to see."""
    return ctx.server.mc_version or (ctx.server.pending_version or {}).get("minecraft_version")


def _here(ctx: ServerContext) -> tuple[set[str], list[dict[str, Any]]]:
    """Every project id and jar id on the server (lower case), and the
    add-ons the app installed from a site (source, project, name)."""
    have: set[str] = set()
    recorded = []
    for mod in ctx.mods.scan(use_cache=True):
        have.update({mod.mod_id.lower(), mod.mod_id.lower().replace("_", "-")})
        source, project = ctx.mods.origin(mod)
        if project and ctx.mods.source_of(mod):
            have.add(str(project).lower())
            recorded.append({"source": source, "project": project, "name": mod.name})
    return have, recorded


def _is_here(have: set[str], project: str, info: dict[str, Any] | None) -> bool:
    names = (
        {project, *(str(info.get(k) or "") for k in ("project_id", "slug"))} if info else {project}
    )
    return any(n and (n.lower() in have or n.lower().replace("-", "") in have) for n in names)


async def _infos(ctx: ServerContext, wanted: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """(source, project) -> the site's project page facts, in as few calls
    as possible: Modrinth's all at once."""
    found: dict[tuple[str, str], dict] = {}
    modrinth = [p for s, p in wanted if s == "modrinth"]
    if modrinth:
        many = await ctx.mods.modrinth.projects(modrinth)
        found.update({("modrinth", p): many[p] for p in modrinth if p in many})
    for source, project in wanted:
        if source != "modrinth":
            try:
                site: Any = ctx.mods.site(source)  # Hangar or CurseForge, both have project()
                found[(source, project)] = await site.project(split(project)[1])
            except (ModError, SourceError):
                continue
    return found


def _place(entry: dict[str, Any], info: dict[str, Any]) -> str:
    """server, both (on the server and in players' games) or players."""
    if info.get("server_side") == "unsupported":
        return "players"
    if info.get("client_side") == "required" or entry["players_need"] == "both":
        return "both"
    return "server"


async def _fit(ctx, entry, info, minecraft, have) -> dict[str, Any] | None:
    """The entry as the apps show it, or None when it doesn't fit here."""
    if "library" in (info.get("categories") or []) or _is_here(have, entry["project"], info):
        return None
    place = _place(entry, info)
    version = None
    if place != "players":  # what players install isn't checked against the server
        try:
            site = ctx.mods.site(entry["source"])
        except ModError:
            return None  # this type can't use that site (Hangar on Fabric)
        version = await site.latest_for(split(entry["project"])[1], minecraft)
        if (
            not version
            or minecraft not in version["game_versions"]
            or not ctx.config.server_type.accepts_any([x.lower() for x in version["loaders"]])
            or any(
                d["type"] == "incompatible" and str(d.get("project_id") or "").lower() in have
                for d in version.get("dependencies") or []
            )
        ):
            return None
    stable = version is None or version["release_type"] == "release"
    return {
        "project": entry["project"],
        "name": info.get("title") or entry["name"],
        "description": info.get("description"),
        "kind": entry["kind"],
        "group": entry["group"],
        "changes_gameplay": entry["changes_gameplay"],
        # Alpha and beta builds, and speed-ups that change gameplay: Technical only.
        "technical_only": entry["technical_only"]
        or not stable
        or (entry["kind"] == "performance" and entry["changes_gameplay"]),
        "release_type": version["release_type"] if version else None,
        "version": version["version_number"] if version else None,
        "place": place,
        "page": info.get("page"),
        "downloads": (
            {"count": info["downloads"], "source": LABELS[entry["source"]]}
            if isinstance(info.get("downloads"), int)
            else None
        ),
        "played_by": entry["played_by"],
        "with": entry["with"],
    }


async def check(ctx: ServerContext) -> dict[str, Any]:
    """Every entry for this server's type that fits it, in the notes
    file's order, plus the add-ons here that players need too."""
    server_type = ctx.config.server_type
    minecraft = minecraft_of(ctx)
    found: dict[str, Any] = {
        "type": server_type.id,
        "minecraft_version": minecraft,
        "items": [],
        "installed_for_players": [],
        "problem": None,
        "read_at": None,
        "stale": None,
        "to_fabric": None,
    }
    if not minecraft:
        return {**found, "problem": START_ONCE} if server_type.has_content else found
    if server_type.id == "vanilla" and _slow(ctx):  # only asked when it would be shown
        found["to_fabric"] = await to_fabric(ctx, minecraft)
    if not server_type.has_content:
        return found
    # A login add-on only for an offline-mode server (the Protection card
    # offers it too); an anti-bot add-on by who the server is for, as usual.
    offline = whitelist.online_mode(ctx) is False
    entries = [
        e
        for e in notes.RECOMMENDED
        if server_type.id in e["types"] and (e.get("role") != "login" or offline)
    ]
    if not entries:
        return found
    have, recorded = _here(ctx)
    # An "often used with" entry only counts when one of its add-ons is here.
    # shortcut: a jar copied in by hand is matched by its own id, which for
    # nearly every mod is its Modrinth slug; match by file hash if one is missed.
    entries = [
        e
        for e in entries
        if e["kind"] != "often_used_with" or any(p.lower() in have for p in e["with"])
    ]
    started = time.time()
    infos: dict[tuple[str, str], dict] = {}
    with savedlists.watch() as used:
        try:
            infos = await _infos(
                ctx,
                [(e["source"], e["project"]) for e in entries]
                + [(r["source"], r["project"]) for r in recorded],
            )
            for entry in entries:
                info = infos.get((entry["source"], entry["project"]))
                if info:
                    item = await _fit(ctx, entry, info, minecraft, have)
                    found["items"] += [item] if item else []
        except SourceError as exc:
            found["problem"] = str(exc)
    found["read_at"], found["stale"] = savedlists.problem(used)
    found["read_at"] = found["read_at"] or started
    for item in found["items"]:
        if item["downloads"]:
            item["downloads"]["read_at"] = found["read_at"]
    needed = {e["project"] for e in notes.RECOMMENDED if e["players_need"] == "both"}
    for row in recorded:
        info = infos.get((row["source"], row["project"])) or {}
        if (
            row["project"] in needed
            or info.get("slug") in needed
            or info.get("client_side") == "required"
        ):
            found["installed_for_players"].append({"name": row["name"], "page": info.get("page")})
    return found


def _slow(ctx: ServerContext) -> dict | None:
    return measured(
        ctx.metrics.history(hours=1),
        float(ctx.config.thresholds.tps_min),
        float(ctx.config.thresholds.ram_percent),
    )


def _key(ctx: ServerContext) -> tuple:
    return (
        ctx.config.server_type.id,
        minecraft_of(ctx),
        tuple(sorted(m.filename for m in ctx.mods.scan(use_cache=True))),
        bool(_slow(ctx)),
    )


async def checked(ctx: ServerContext) -> dict[str, Any]:
    """The check, from the kept copy while it still fits this server."""
    kept = _checked.get(ctx.server_id)
    key = _key(ctx)
    if kept and kept[1] == key and time.time() - kept[0] < KEEP_FOR:
        return kept[2]
    found = await check(ctx)
    if not found["problem"]:
        _checked[ctx.server_id] = (time.time(), key, found)
    return found


def kept(ctx: ServerContext) -> dict[str, Any] | None:
    """The last check if it still fits, without asking anyone (for the
    Recommendations rules). A fresh one is started in the background."""
    found = _checked.get(ctx.server_id)
    if not found or found[1] != _key(ctx) or time.time() - found[0] >= KEEP_FOR:
        if ctx.server_id not in _running:

            async def run() -> None:
                try:
                    await checked(ctx)
                except Exception:
                    log.debug("suggestions check failed", exc_info=True)
                finally:
                    _running.pop(ctx.server_id, None)

            _running[ctx.server_id] = asyncio.get_running_loop().create_task(run())
        return None
    return found[2]


# ---------------------------------------------------------------- what the apps get
def paper_settings(ctx: ServerContext) -> dict[str, Any] | None:
    """Paper's own speed-up: lower view and simulation distance, read from
    server.properties (Minecraft's default when it doesn't set one)."""
    path = Path(ctx.config.server_dir) / FILENAME
    values = PropertiesFile.read(path).values() if path.is_file() else {}
    now, lower = {}, {}
    for key, target in PAPER_DISTANCES.items():
        try:
            now[key] = int(values.get(key) or DISTANCE_DEFAULT)
        except ValueError:
            return None  # not a number: the game settings page shows that
        if now[key] > target:
            lower[key] = str(target)
    if not lower:
        return None
    return {
        "kind": "settings",
        "name": "Lower view and simulation distance",
        "now": now,
        "values": lower,
    }


async def to_fabric(ctx: ServerContext, minecraft: str) -> dict[str, Any] | None:
    """Vanilla's speed-up: Change server type to Fabric, then Lithium. Only
    offered when Lithium has a Fabric build for this version."""
    try:
        lithium = await ctx.mods.modrinth.latest_for("lithium", minecraft, loaders=("fabric",))
    except SourceError:
        return None
    if not lithium or lithium["release_type"] != "release":
        return None
    return {"kind": "change_type", "name": "Change to Fabric with Lithium", "type": "fabric"}


def speed_ups(ctx: ServerContext, found: dict[str, Any], technical: bool) -> dict[str, Any]:
    """The speed-ups for this type, only with a measured reason: Paper's own
    settings, Vanilla to Fabric, and the performance add-ons."""
    reason = _slow(ctx)
    if not reason:
        return {"reason": None, "items": []}
    items = [
        paper_settings(ctx) if ctx.config.server_type.content == "plugins" else None,
        found["to_fabric"],
        *(i for i in found["items"] if i["kind"] == "performance"),
    ]
    return {
        "reason": reason,
        "items": [i for i in items if i and (technical or not i.get("technical_only"))],
    }


async def lists(ctx: ServerContext, technical: bool) -> dict[str, Any]:
    """What the add-ons and performance pages show. Simple mode gets only
    what's safe to add without reading more (no alpha builds, nothing
    marked Technical)."""
    found = await checked(ctx)
    items = [i for i in found["items"] if technical or not i["technical_only"]]
    faster = speed_ups(ctx, found, technical)
    return {
        **{k: found[k] for k in ("type", "minecraft_version", "problem", "read_at", "stale")},
        "good_to_have": [
            i for i in items if i["kind"] == "good_to_have" and i["place"] != "players"
        ],
        "often_used_with": [
            i for i in items if i["kind"] == "often_used_with" and i["place"] != "players"
        ],
        "performance": faster["items"],
        "performance_reason": faster["reason"],
        "for_your_players": [
            {"name": i["name"], "page": i["page"], "also_on_server": False}
            for i in items
            if i["place"] == "players"
        ]
        + [{**i, "also_on_server": True} for i in found["installed_for_players"]],
        "maps": await asyncio.to_thread(map_links, ctx),
    }
