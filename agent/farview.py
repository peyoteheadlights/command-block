"""Far view: land far past the normal view distance (the Mods page card).

Off, Distant Horizons or Voxy. The app adds the chosen mod and every mod it
needs, read from each version's own required dependencies on Modrinth
(never guessed), to the server and to the players' modpack:

- **Distant Horizons** (LGPL-3.0): the mod on a mods server, marked Both so
  it goes in the modpack. A plugin server gets the "Distant Horizons
  Support" plugin (GPL-3.0) and players get DH for Fabric in Players only.
- **Voxy** (ARR, Fabric, players only): Voxy goes in Players only, and the
  server gets "Voxy Server Side" (MIT; Fabric, NeoForge and Paper builds),
  which players need too. Picked over VoxyServer (LGPL, Fabric only) for
  its loaders and its upkeep (most used, updated 2026-11-13).

Only versions for this server's Minecraft version and loader are used; a
choice with none says so. Off takes away only the files the app added for
far view (to the mods trash, like any removal).

"Generate unvisited land" (DH only) is off by default: DH then builds far
land only from chunks that exist (its ``chunkGeneratorMode =
"PRE_EXISTING_ONLY"``, the plugin's ``generate_new_chunks: false``). DH
writes its config file on its first start, so it is set again before every
start (``before_start``).

"Load the world ahead" runs Chunky at night while nobody is on, pauses it
when someone joins and runs the server at a lower CPU priority meanwhile
(``NightLoad``). Backups leave far-view data out (``is_far_view_data``: it
is rebuilt from the world), and "Rebuild far view" deletes it with the
server stopped.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .minecraft.properties import world_folders
from .mods import sides
from .mods.manager import ModError
from .mods.modrinth import ModrinthNotFound
from .mods.sources import SourceError

if TYPE_CHECKING:
    from .core import ServerContext

log = logging.getLogger("msc.farview")

SETTING = "far_view"  # choice, the files added for it, the two settings
CHOICES: dict[str, dict[str, Any]] = {
    "distanthorizons": {
        "name": "Distant Horizons",
        "server": {"mods": "distanthorizons", "plugins": "distant-horizons-support"},
        "players": ("distanthorizons",),
    },
    "voxy": {
        "name": "Voxy",
        "server": {"mods": "voxy-server-side", "plugins": "voxy-server-side"},
        "players": ("voxy", "voxy-server-side"),
    },
}
PLUGIN_PLAYERS_LOADERS = ("fabric",)  # the modpack's loader on plugin servers (modpackmake)
MAX_DEPTH = 6
DH_CONFIG = "config/DistantHorizons.toml"
DH_SECTION = "[common.worldGenerator]"
DH_KEY = "chunkGeneratorMode"
DHS_CONFIG = "plugins/DHSupport/config.yml"
DHS_KEY_RE = re.compile(r"^\s*#?\s*generate_new_chunks\s*:.*$", re.M)
# Far-view data: rebuilt from the world, so backups leave it out.
DATA_FILE_RE = re.compile(r"^(DistantHorizons\.sqlite|data\.sqlite)(-wal|-shm|-journal)?$")
DATA_FOLDERS = {"vss-lod", "lss-lod"}  # Voxy Server Side's LOD store, in each world
NIGHT = (1, 6)  # Chunky runs from 1:00 to 6:00 at night, local time
CHECK_SECONDS = 30.0
WORLD_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
RADIUS = (100, 30000, 2000)  # the Chunky guide's range and default


class FarViewError(ModError):
    """Something about far view, in plain words."""


class NoVersion(FarViewError):
    """A mod it needs has no version for this Minecraft and loader."""


def saved(ctx: ServerContext) -> dict[str, Any]:
    found = ctx.db.get_setting(SETTING)
    found = found if isinstance(found, dict) else {}
    load = found.get("load_ahead") if isinstance(found.get("load_ahead"), dict) else {}
    return {
        "choice": found.get("choice") if found.get("choice") in CHOICES else "off",
        "added": found.get("added") if isinstance(found.get("added"), dict) else {},
        "unvisited": found.get("unvisited") is True,
        "load_ahead": {
            "on": load.get("on") is True,
            "radius": load.get("radius") if isinstance(load.get("radius"), int) else RADIUS[2],
            "world": load.get("world") if isinstance(load.get("world"), str) else "",
            "started": load.get("started") is True,
        },
    }


def _save(ctx: ServerContext, state: dict[str, Any]) -> None:
    ctx.db.set_setting(SETTING, state)


def main_world(ctx: ServerContext) -> str:
    """The world Chunky loads when none is picked: level-name's folder."""
    if not ctx.config.server_dir_configured:
        return "world"
    found = sorted(world_folders(ctx.config.server_dir, ctx.config.server_type))
    return found[0] if found else "world"


def supported(ctx: ServerContext) -> bool:
    return ctx.mods.server_type.content in ("mods", "plugins")


# ---------------------------------------------------------------- what each choice needs
async def _resolve(
    ctx: ServerContext, project: str, loaders: tuple[str, ...], seen: set[str], depth: int = 0
) -> list[dict[str, Any]]:
    """``project``'s version for this Minecraft and these loaders, after the
    ones its required dependencies need (each from Modrinth's own list)."""
    if project in seen:
        return []
    if depth > MAX_DEPTH:
        raise FarViewError("The chain of mods this needs is too long, so nothing was added.")
    site, minecraft = ctx.mods.modrinth, ctx.server.mc_version
    try:
        version = await site.latest_for(project, minecraft, loaders)
    except ModrinthNotFound:
        version = None
    if not version or minecraft not in version["game_versions"]:
        raise NoVersion(project)
    seen.update({project, str(version["project_id"])})
    found: list[dict[str, Any]] = []
    for need in version["dependencies"]:
        if need.get("type") != "required" or not need.get("project_id"):
            continue
        if need.get("version_id") and need["project_id"] not in seen:
            pinned = await site.version(need["version_id"])  # the exact version it names
            if minecraft not in pinned["game_versions"] or not set(pinned["loaders"]) & set(
                loaders
            ):
                raise NoVersion(need["project_id"])
            seen.add(need["project_id"])
            found += [
                *await _needs_of(ctx, pinned, loaders, seen, depth + 1),
                pinned,
            ]
        else:
            found += await _resolve(ctx, need["project_id"], loaders, seen, depth + 1)
    return found + [version]


async def _needs_of(ctx, version, loaders, seen, depth) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for need in version["dependencies"]:
        if need.get("type") == "required" and need.get("project_id"):
            found += await _resolve(ctx, need["project_id"], loaders, seen, depth)
    return found


def _loaders(ctx: ServerContext) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(the server's Modrinth loaders, the players' ones)."""
    kind = ctx.mods.server_type
    server = tuple(kind.modrinth_loaders)
    return server, server if kind.content == "mods" else PLUGIN_PLAYERS_LOADERS


async def plan(ctx: ServerContext, choice: str) -> dict[str, list[dict[str, Any]]]:
    """The Modrinth versions a choice adds: {"server": [...], "players": [...]},
    each list in install order. FarViewError names what has no fitting version."""
    about = CHOICES[choice]
    kind = ctx.mods.server_type
    server_loaders, players_loaders = _loaders(ctx)
    if not ctx.server.mc_version:
        raise FarViewError(f"Start {ctx.name} once so the app knows its Minecraft version.")
    seen: set[str] = set()
    try:
        server = await _resolve(ctx, about["server"][kind.content], server_loaders, seen)
        # A mods server's own jars go in the modpack already (marked Both).
        players_seen = seen if kind.content == "mods" else set()
        players: list[dict[str, Any]] = []
        for project in about["players"]:
            players += await _resolve(ctx, project, players_loaders, players_seen)
    except NoVersion as exc:
        raise FarViewError(
            f"{about['name']} has nothing for {kind.name} on Minecraft {ctx.server.mc_version} yet."
        ) from exc
    present = await asyncio.to_thread(_present, ctx)
    about_all = await _names(ctx, server + players)
    for version in server + players:
        found = about_all.get(str(version["project_id"])) or {}
        version["title"] = str(found.get("title") or version["project_id"])
        version["slug"] = str(found.get("slug") or "")
    return {
        side: [v for v in found if not {str(v["project_id"]), v["slug"]} & present]
        for side, found in (("server", server), ("players", players))
    }


def _present(ctx: ServerContext) -> set[str]:
    """Project ids and mod ids already on the server or in Players only."""
    mods = ctx.mods
    found: set[str] = set()
    for mod in mods.scan():
        found.add(mod.mod_id.lower())
        project = mods.origin(mod)[1]
        if project:
            found.add(str(project))
    found |= {m.mod_id.lower() for m in sides._player_mods(ctx) if m.mod_id}
    return found


async def _names(ctx: ServerContext, versions: list[dict[str, Any]]) -> dict[str, dict]:
    ids = [str(v["project_id"]) for v in versions]
    try:
        return await ctx.mods.modrinth.projects(ids) if ids else {}
    except SourceError:
        return {}


async def card(ctx: ServerContext, may_change: bool) -> dict[str, Any]:
    """What the Far view card shows."""
    state = saved(ctx)
    result: dict[str, Any] = {
        "supported": supported(ctx),
        "choice": state["choice"],
        "choices": [],
        "unvisited": state["unvisited"],
        "load_ahead": {
            **{k: v for k, v in state["load_ahead"].items() if k != "started"},
            "chunky": "chunky" in {m.mod_id.lower() for m in ctx.mods.scan(use_cache=True)},
            "running": ctx.night_load.running if hasattr(ctx, "night_load") else False,
            "night": f"{NIGHT[0]}:00-{NIGHT[1]}:00",
            "main_world": main_world(ctx),
        },
        "may_change": may_change,
    }
    if not result["supported"]:
        return result
    for choice, about in CHOICES.items():
        row: dict[str, Any] = {"id": choice, "name": about["name"], "available": True}
        try:
            found = await plan(ctx, choice)
            row["server"] = [v["title"] for v in found["server"]]
            row["players"] = [v["title"] for v in found["players"]]
        except FarViewError as exc:
            row.update(available=False, why=str(exc), server=[], players=[])
        except SourceError:
            row.update(available=False, why="Modrinth didn't answer. Try again in a minute.")
            row.update(server=[], players=[])
        result["choices"].append(row)
    return result


# ---------------------------------------------------------------- turning it on and off
async def choose(ctx: ServerContext, choice: str, user: str) -> dict[str, Any]:
    """Turn far view off or to a choice. The server must be stopped (each
    install and removal checks it)."""
    if choice != "off" and choice not in CHOICES:
        raise FarViewError("Pick Off, Distant Horizons or Voxy.")
    if not supported(ctx):
        raise FarViewError("Far view needs a server with mods or plugins.")
    ctx.mods.require_server_offline("change far view")
    state = saved(ctx)
    found = await plan(ctx, choice) if choice != "off" else None  # before anything changes
    removed = await _take_away(ctx, state, user)
    state = {**state, "choice": "off", "added": {}}
    _save(ctx, state)
    if found is None:
        return {"choice": "off", "removed": removed, "added": []}
    added: dict[str, list[str]] = {"server": [], "players": []}
    try:
        await _add(ctx, found, added, user)
    finally:
        _save(ctx, {**state, "choice": choice, "added": added})
    await asyncio.to_thread(before_start, ctx)
    return {"choice": choice, "removed": removed, "added": added["server"] + added["players"]}


async def _add(ctx, found, added, user) -> None:
    """Install in order (needs first), noting each file as it lands, so Off
    can take away a half-done choice too."""
    mods = ctx.mods
    for version in found["server"]:
        got = await mods.install_from_site(
            str(version["project_id"]), user, version_id=version["version_id"]
        )
        filename = got["installed"]["filename"]
        added["server"].append(filename)
        if mods.server_type.content == "mods":
            sides._save_mark(mods, mods.find(filename), "both")  # players need these too
    for version in found["players"]:
        data, filename, _ = await mods.modrinth.download(version)
        got = await sides.add(ctx, filename, data, user, confirm=True, own_jar=False)
        added["players"].append(got["added"])


async def _take_away(ctx: ServerContext, state: dict[str, Any], user: str) -> list[str]:
    """Only what the app added for far view, and only what is still there."""
    removed = []
    for filename in state["added"].get("server") or []:
        try:
            await ctx.mods.remove(str(filename), user)
            removed.append(str(filename))
        except ModError:
            continue  # gone already, or renamed by the owner: theirs now
    where = ctx.config.server_dir / sides.FOLDER
    for filename in state["added"].get("players") or []:
        if (where / str(filename)).is_file():
            await sides.remove(ctx, str(filename), user)
            removed.append(str(filename))
    return removed


def set_options(
    ctx: ServerContext, unvisited: bool, load_on: bool, radius: int, world: str
) -> dict[str, Any]:
    lo, hi, _ = RADIUS
    if not lo <= radius <= hi:
        raise FarViewError(f"Pick between {lo} and {hi} blocks.")
    if world and not WORLD_RE.match(world):
        raise FarViewError("That world name can't be used.")
    state = saved(ctx)
    load = state["load_ahead"]
    started = load["started"] and (load["radius"], load["world"]) == (radius, world)
    state["unvisited"] = unvisited
    state["load_ahead"] = {"on": load_on, "radius": radius, "world": world, "started": started}
    _save(ctx, state)
    before_start(ctx)
    return saved(ctx)


# ---------------------------------------------------------------- DH: unvisited land
def before_start(ctx: ServerContext) -> None:
    """Write "generate unvisited land" into DH's config files that exist.
    Files DH hasn't made yet are left alone (it would throw away one
    without its own version line)."""
    if not ctx.config.server_dir_configured:
        return
    state = saved(ctx)
    if state["choice"] != "distanthorizons":
        return
    base = ctx.config.server_dir
    _set_dh(base / DH_CONFIG, state["unvisited"])
    _set_dhs(base / DHS_CONFIG, state["unvisited"])


def _write(path: Path, text: str) -> None:
    temp = path.with_name(f".{path.name}.part")
    temp.write_text(text, "utf-8", newline="")
    temp.replace(path)


def _set_dh(path: Path, unvisited: bool) -> None:
    if not path.is_file() or path.is_symlink():
        return
    value = f'{DH_KEY} = "{"FEATURES" if unvisited else "PRE_EXISTING_ONLY"}"'
    lines = path.read_text("utf-8").splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.strip() == DH_SECTION), None)
    if start is None:
        lines += [f"\n{DH_SECTION}\n", f"\t{value}\n"]
    else:
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")),
            len(lines),
        )
        key = next(
            (i for i in range(start + 1, end) if lines[i].split("=")[0].strip() == DH_KEY), None
        )
        if key is None:
            lines.insert(start + 1, f"\t{value}\n")
        else:
            indent = lines[key][: len(lines[key]) - len(lines[key].lstrip())]
            lines[key] = f"{indent}{value}\n"
    _write(path, "".join(lines))


def _set_dhs(path: Path, unvisited: bool) -> None:
    if not path.is_file() or path.is_symlink():
        return
    text = path.read_text("utf-8")
    value = f"generate_new_chunks: {'true' if unvisited else 'false'}"
    text = (
        DHS_KEY_RE.sub(value, text, count=1) if DHS_KEY_RE.search(text) else text + f"\n{value}\n"
    )
    _write(path, text)


# ---------------------------------------------------------------- data: backups and rebuild
def is_far_view_data(relative: Path) -> bool:
    """A file (relative to the server folder) that is far-view data."""
    parts = relative.parts
    if DATA_FOLDERS & set(parts[:-1]):
        return True
    name = parts[-1]
    if not DATA_FILE_RE.match(name):
        return False
    if name.startswith("data."):  # the DH Support plugin's database
        return parts[:2] == ("plugins", "DHSupport")
    return True


def _data(ctx: ServerContext) -> list[Path]:
    base = ctx.config.server_dir
    roots = [base / w for w in world_folders(base, ctx.config.server_type)]
    roots.append(base / "plugins" / "DHSupport")
    found: list[Path] = []
    for root in roots:
        if not root.is_dir() or root.is_symlink():
            continue
        for path in root.rglob("*"):
            relative = path.relative_to(base)
            if path.is_symlink():
                continue
            if path.is_dir() and path.name in DATA_FOLDERS:
                found.append(path)
            elif (
                path.is_file()
                and is_far_view_data(relative)
                and not (DATA_FOLDERS & set(relative.parts[:-1]))
            ):
                found.append(path)
    return found


def rebuild(ctx: ServerContext, user: str) -> dict[str, Any]:
    """Delete the far-view data; it is built again from the world as players
    come by. The server must be stopped."""
    ctx.mods.require_server_offline("rebuild far view")
    size = files = 0
    for path in _data(ctx):
        if path.is_dir():
            for f in path.rglob("*"):
                if f.is_file():
                    size, files = size + f.stat().st_size, files + 1
            shutil.rmtree(path)
        else:
            size, files = size + path.stat().st_size, files + 1
            path.unlink()
    ctx.mods.record("far_view_rebuild", user, detail=f"Deleted {files} far-view files")
    return {"files": files, "size_bytes": size}


# ---------------------------------------------------------------- load the world ahead
class NightLoad:
    """Runs Chunky at night while nobody is on; pauses it when someone joins."""

    def __init__(self, ctx: ServerContext):
        self.ctx = ctx
        self.running = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name=f"night-load-{self.ctx.server_id}")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(CHECK_SECONDS)
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - never take the agent down
                log.exception("night load check failed for %s", self.ctx.server_id)

    async def check(self, hour: int | None = None) -> str:
        """One pass: "off", "not_online", "loading" or "paused"."""
        ctx = self.ctx
        hour = time.localtime().tm_hour if hour is None else hour
        state = saved(ctx)
        load = state["load_ahead"]
        online = ctx.server.running and ctx.server.state.value == "ONLINE"
        if not online:
            self.running = False
            return "not_online"
        chunky = "chunky" in {m.mod_id.lower() for m in ctx.mods.scan(use_cache=True)}
        wanted = (
            load["on"]
            and chunky
            and NIGHT[0] <= hour < NIGHT[1]
            and ctx.players.online_count() == 0  # a verified zero, never a guess
        )
        if wanted and not self.running:
            if load["started"]:
                await ctx.server.send_command("chunky continue", internal=True)
            else:
                world = load["world"] or main_world(ctx)
                await ctx.server.send_command(
                    f"chunky start {world} square 0 0 {load['radius']}", internal=True
                )
                state["load_ahead"]["started"] = True
                _save(ctx, state)
            self.running = True
            _priority(ctx, low=True)
        elif not wanted and self.running:
            await ctx.server.send_command("chunky pause", internal=True)
            self.running = False
            _priority(ctx, low=False)
        return "loading" if self.running else ("off" if not load["on"] else "paused")


def _priority(ctx: ServerContext, low: bool) -> None:
    """Below-normal CPU priority while Chunky runs, so the PC stays usable."""
    process = getattr(ctx.server, "process", None)
    pid = getattr(process, "pid", None)
    if not pid:
        return
    try:
        import psutil

        if os.name == "nt":
            value = psutil.BELOW_NORMAL_PRIORITY_CLASS if low else psutil.NORMAL_PRIORITY_CLASS
        else:
            value = 10 if low else 0
        psutil.Process(pid).nice(value)
    except Exception:  # a separate Windows user's process may refuse; Chunky still runs
        log.info("couldn't change the server's CPU priority", exc_info=True)
