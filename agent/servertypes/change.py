"""Change server type: a Java server becomes another Java type, keeping its
world, settings and Minecraft version. Owner only, on a stopped server.

The download, the backup, the job and Roll back are the version change
routine's (install.change_version). What this module adds around it:

  * which types are offered: Simple mode only Vanilla to and from Fabric
    and Vanilla to and from Paper, Technical every Java type to every other
  * the Minecraft version is kept; a type with no build for it is refused
  * the Nether and the End move between the two world layouts (Paper keeps
    them in world_nether and world_the_end, everyone else inside world)
  * add-ons the new type can't load move into a "Not used by <type>" folder
    inside the server, never deleted, and move back on a later change back.
    A folder the new type ignores (plugins on Fabric) stays where it is.
  * afterwards, similar add-ons with Add buttons: the same project's build
    for the new type first, then the notes file's hand-checked look-alikes.
    Never a guess from a name search, and nothing is added for the owner.

A custom jar changes like the type it's based on (its ``server.type``).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import notes, servertypes
from ..minecraft.properties import FILENAME, PropertiesFile, world_folder
from . import ServerType
from .install import InstallError, change_version, preflight
from .versions import list_versions

if TYPE_CHECKING:
    from ..core import ServerContext

# The pairs Simple mode offers (either way round).
SIMPLE_PAIRS = {frozenset({"vanilla", "fabric"}), frozenset({"vanilla", "paper"})}
NOT_USED = "Not used by "
DIMENSIONS = {"DIM-1": "_nether", "DIM1": "_the_end"}


# ----------------------------------------------------------------------
# what can change to what
# ----------------------------------------------------------------------
def targets(current: ServerType, technical: bool) -> list[ServerType]:
    """The types this server can change to, in the table's order."""
    if current.edition != "java":
        return []
    return [
        t
        for t in servertypes.TYPES.values()
        if t.edition == "java"
        and t.id != current.id
        and (technical or frozenset({current.id, t.id}) in SIMPLE_PAIRS)
    ]


def check_target(current: ServerType, type_id: str, technical: bool) -> ServerType:
    target = servertypes.get(type_id)
    if target in targets(current, technical):
        return target
    if target.edition != current.edition:
        raise InstallError(
            f"A {current.name} server can't become a {target.name} server: their worlds are "
            "stored differently. Make a copy for the other edition instead (Technical mode)."
        )
    if target.id == current.id:
        raise InstallError(f"This server is already {target.name}.")
    raise InstallError(
        f"Changing {current.name} to {target.name} is only offered in Technical mode."
    )


def kept_version(ctx: ServerContext) -> str:
    """The Minecraft version the change keeps: what the console printed, or
    what this app itself installed and is waiting to see. Never a guess."""
    pending = ctx.server.pending_version or {}
    version = ctx.server.mc_version or pending.get("minecraft_version")
    if not version:
        raise InstallError(
            "Start the server once so the app can see its Minecraft version, then change its type."
        )
    return str(version)


async def check_build(target: ServerType, minecraft: str) -> None:
    if not any(v.minecraft == minecraft for v in await list_versions(target.id)):
        raise InstallError(
            f"{target.name} has no build for Minecraft {minecraft}, so the type wasn't "
            "changed. Change the version first if you want to move to a newer one."
        )


# ----------------------------------------------------------------------
# where the files go (pure reading: nothing moves here)
# ----------------------------------------------------------------------
def _jars(folder: Path) -> list[Path]:
    if not folder.is_dir() or folder.is_symlink():
        return []
    return [
        p
        for p in sorted(folder.iterdir())
        if p.is_file() and not p.is_symlink() and ".jar" in p.name
    ]


def _fits(path: Path, target: ServerType) -> bool:
    """Whether the new type loads this jar. A jar that says nothing about its
    loader can't come along (moved, never deleted)."""
    from ..mods.jarinfo import read_mod_jar

    info = read_mod_jar(path, compute_hash=False)
    return any(loader in target.accepts for loader in info.loaders or [info.loader])


def not_used_folder(target: ServerType) -> str:
    # A notes-file type's name is plain text, so only folder-safe characters.
    return NOT_USED + (re.sub(r"[^A-Za-z0-9 _-]", "", target.name).strip() or target.id)


def _world(directory: Path) -> str:
    path = directory / FILENAME
    values = PropertiesFile.read(path).values() if path.is_file() else {}
    return world_folder(directory, values).name


def plan_files(directory: Path, current: ServerType, target: ServerType) -> dict[str, Any]:
    """Where the add-ons and the Nether and End go for this change."""
    home = target.content_folder
    plan: dict[str, Any] = {
        "home": home,
        "folder": not_used_folder(target),
        "kept": [],  # in the new type's add-on folder, and it loads them
        "out": [],  # in that folder, and it can't: to "Not used by <type>"
        "back": [],  # [folder, name] in a "Not used by" folder that it loads
        # In the old type's folder, which the new type ignores: left there.
        "left": [],
        "dimensions": [],  # [from, to], relative to the server folder
    }
    if current.content_folder and current.content_folder != home:
        plan["left"] = [p.name for p in _jars(directory / current.content_folder)]
    if home:
        for path in _jars(directory / home):
            plan["kept" if _fits(path, target) else "out"].append(path.name)
        for folder in sorted(directory.glob(NOT_USED + "*")):
            for path in _jars(folder):
                if _fits(path, target) and not (directory / home / path.name).exists():
                    plan["back"].append([folder.name, path.name])
    if current.split_dimensions != target.split_dimensions:
        world = _world(directory)
        for dim, suffix in DIMENSIONS.items():
            inside, split = f"{world}/{dim}", f"{world}{suffix}/{dim}"
            source, dest = (inside, split) if target.split_dimensions else (split, inside)
            if not (directory / source).is_dir():
                continue
            if (directory / dest).exists():
                raise InstallError(
                    f"Both {source} and {dest} are in the server folder, so the app can't "
                    "tell which one to keep. Move one of them out of the folder first."
                )
            plan["dimensions"].append([source, dest])
    return plan


def _free(path: Path) -> Path:
    number = 2
    found = path
    while found.exists():
        found = path.with_name(f"{path.stem} ({number}){path.suffix}")
        number += 1
    return found


def _move(directory: Path, source: str, dest: str) -> None:
    (directory / dest).parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(directory / source), str(directory / dest))


def _tidy(directory: Path) -> None:
    for folder in directory.glob(NOT_USED + "*"):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()


def move_files(directory: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Make the moves ``plan_files`` worked out. Nothing is deleted. Each
    move is recorded, so ``undo_moves`` can put everything back."""
    moves = [list(pair) for pair in plan["dimensions"]]
    home = plan["home"] or ""
    for name in plan["out"]:
        dest = _free(directory / plan["folder"] / name).relative_to(directory)
        moves.append([f"{home}/{name}", dest.as_posix()])
    moves += [[f"{folder}/{name}", f"{home}/{name}"] for folder, name in plan["back"]]
    done: list[list[str]] = []
    try:
        for source, dest in moves:
            _move(directory, source, dest)
            done.append([source, dest])
    except OSError:
        undo_moves(directory, done)
        raise
    _tidy(directory)
    return {
        "moved": plan["out"],
        "kept": plan["kept"],
        "came_back": [name for _, name in plan["back"]],
        "left": plan["left"],
        "dimensions": plan["dimensions"],
        "folder": str(directory / plan["folder"]) if plan["out"] else None,
        "moves": done,
    }


def undo_moves(directory: Path, moves: list[list[str]]) -> list[str]:
    """Put files moved by ``move_files`` back, newest first. A file that has
    gone since, or whose old place is taken again, is left where it is."""
    back = []
    for source, dest in reversed(moves):
        if (directory / dest).exists() and not (directory / source).exists():
            _move(directory, dest, source)
            back.append(source)
    _tidy(directory)
    return back


def _projects(ctx: ServerContext, folder: Path, names: list[str]) -> list[dict[str, Any]]:
    """Where each add-on came from, from the app's own records only: a jar
    copied in by hand has none, so it gets no "same add-on" suggestion."""
    from ..mods.jarinfo import read_mod_jar

    found = []
    for name in names:
        info = read_mod_jar(folder / name, compute_hash=False)
        source = ctx.mods.source_of(info)
        project = ctx.mods.origin(info)[1] if source in notes.LOOK_ALIKE_SOURCES else None
        found.append({"filename": name, "name": info.name, "source": source, "project": project})
    return found


# ----------------------------------------------------------------------
# the preflight and the change
# ----------------------------------------------------------------------
def options(ctx: ServerContext, technical: bool) -> dict[str, Any]:
    """What the Change server type card offers."""
    current = ctx.config.server_type
    try:
        version, problem = kept_version(ctx), None
    except InstallError as exc:
        version, problem = None, str(exc)
    return {
        "type": current.id,
        "targets": [
            {"id": t.id, "name": t.name, "changes_farms": t.changes_farms}
            for t in targets(current, technical)
        ],
        "minecraft_version": version,
        "problem": problem,
        "running": ctx.server.running,
        "players_online": ctx.players.online_count() if ctx.server.running else 0,
    }


async def plan(ctx: ServerContext, type_id: str, technical: bool) -> dict[str, Any]:
    """What a type change would do, in plain words, before anything happens."""
    current = ctx.config.server_type
    target = check_target(current, type_id, technical)
    minecraft = kept_version(ctx)
    await check_build(target, minecraft)
    report = await preflight(ctx, target.id, minecraft)
    return {
        **report,
        "minecraft_version": minecraft,
        "running": ctx.server.running,
        "players_online": ctx.players.online_count() if ctx.server.running else 0,
    }


async def change_type(
    ctx: ServerContext,
    type_id: str,
    *,
    technical: bool,
    stop_first: bool = False,
    minecraft: str | None = None,
    user: str = "system",
) -> dict[str, Any]:
    """Change this server's type through the version change routine."""
    current = ctx.config.server_type
    target = check_target(current, type_id, technical)
    kept = kept_version(ctx)
    if minecraft and minecraft != kept:
        raise InstallError(
            f"Changing the type keeps Minecraft {kept}. Change the version as its own step."
        )
    if ctx.server.running and not stop_first:
        raise InstallError(f"Stop {ctx.name} first, or use Stop and change.")
    await check_build(target, kept)
    directory = ctx.config.server_dir
    # Refuses before the server stops; and which projects won't be used,
    # read now while the old type's records apply.
    unused = plan_files(directory, current, target)
    old_home = directory / (current.content_folder or "")
    projects = _projects(ctx, old_home, unused["left"]) + _projects(
        ctx, directory / (target.content_folder or ""), unused["out"]
    )
    result = await change_version(ctx, target.id, kept, user=user)
    record = ctx.db.get_setting("version_change") or {}
    record["content_moved_aside"] = {**record.get("content_moved_aside", {}), "projects": projects}
    ctx.db.set_setting("version_change", record)
    return result


async def similar(ctx: ServerContext) -> dict[str, Any]:
    """After a type change: add-ons like the ones the new type doesn't use.
    The same project's build for the new type first, then the notes file's
    look-alikes. Each is only offered, with its own Add button."""
    from ..mods.manager import ModError
    from ..mods.sources import SourceError, split

    record = ctx.db.get_setting("version_change") or {}
    moved = record.get("content_moved_aside") or {}
    server_type = ctx.config.server_type
    if record.get("type") != server_type.id or not server_type.has_content:
        return {"type": server_type.id, "suggestions": [], "problems": []}
    minecraft = (record.get("minecraft_version") or ctx.server.mc_version) or None
    suggestions: list[dict[str, Any]] = []
    problems: list[str] = []
    for item in moved.get("projects") or []:
        project, source = item.get("project"), item.get("source")
        if not project:
            continue
        same = None
        try:
            site = ctx.mods.site(source)
            same = await site.latest_for(split(project)[1], minecraft)
        except ModError:
            pass  # the new type can't use that site (Hangar on Fabric)
        except SourceError as exc:
            problems.append(str(exc))
        if same:
            suggestions.append(
                {
                    "kind": "same",
                    "for": item["name"],
                    "name": item["name"],
                    "project": project,
                }
            )
            continue
        for alike in notes.look_alikes(source, project, server_type.id):
            suggestions.append(
                {
                    "kind": "look_alike",
                    "for": item["name"],
                    "name": alike["name"],
                    "project": alike["project"],
                }
            )
    # Every "same add-on" first, then the look-alikes.
    suggestions.sort(key=lambda s: s["kind"] != "same")
    return {"type": server_type.id, "suggestions": suggestions, "problems": sorted(set(problems))}
