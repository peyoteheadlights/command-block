"""Copy add-ons to another server: the owner picks mods or plugins on one
server, and the same projects go on another, with their settings files.

Nothing is copied jar for jar. Each add-on is looked up again on the site
the app installed it from, for the other server's type, loader and
Minecraft version, and installed through the normal install (checksum,
known bad files, what it needs). One that can't come is listed with why:
added by hand or from a file (no site to ask), only for players' games
(it stays in "For your players"), or no build for the other server.

Settings come along when both servers keep them in the same place: a
plugin's own folder in plugins/, or a mod's files in config/ whose names
start with its id. They're copied inside the server folders only, and
links are skipped (agent.security.paths rules).

Like Update all it is one change on the other server: it has to be
stopped, one backup of its add-on and config folders is made first, and
if any add-on fails the backup is put back. One Undo takes it all back.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..safechange import SafeChange, run_safe_change
from ..security.paths import is_inside
from .manager import ModError
from .sources import LABELS, SourceError, split
from .updateall import folder_in_server

if TYPE_CHECKING:
    from ..core import ServerContext
    from ..jobs import JobHandle
    from .jarinfo import ModInfo

TITLE = "Copy add-ons"


def settings_of(ctx: ServerContext, mod: ModInfo) -> list[Path]:
    """The settings files and folders that belong to ``mod``, inside the
    server folder and not links."""
    server_dir = ctx.config.server_dir
    if ctx.config.server_type.content == "plugins":
        found = [ctx.config.mods_dir / mod.mod_id]
    else:
        config = server_dir / "config"
        wanted = mod.mod_id.lower()
        found = sorted(
            p
            for p in (config.iterdir() if config.is_dir() else [])
            if p.name.lower().startswith(wanted)
        )
    return [p for p in found if p.exists() and not p.is_symlink() and is_inside(server_dir, p)]


async def check(
    source: ServerContext, target: ServerContext, filenames: list[str]
) -> dict[str, Any]:
    """What would come and what can't, and why. Changes nothing."""
    minecraft = target.server.mc_version or (target.server.pending_version or {}).get(
        "minecraft_version"
    )
    if not target.config.server_type.has_content:
        raise ModError(f"{target.name} is {target.config.server_type.name}, which has no add-ons.")
    if not minecraft:
        raise ModError(f"Start {target.name} once so the app knows its Minecraft version.")
    same_place = source.config.server_type.content == target.config.server_type.content
    here = {target.mods.origin(m) for m in target.mods.scan() if target.mods.source_of(m)}
    coming, cant = [], []
    for filename in dict.fromkeys(filenames):
        mod = source.mods.find(filename)
        site_name = source.mods.source_of(mod)
        project = source.mods.origin(mod)[1] if site_name else None

        def no(why: str, mod: ModInfo = mod) -> None:
            cant.append({"filename": mod.filename, "name": mod.name, "why": why})

        if mod.environment == "client":
            no("It's only for players' games, so it stays in For your players.")
            continue
        if not site_name or site_name in ("file", "github") or not project:
            no(
                "It was added by hand or from a file, so there's no site to get a "
                f"{target.config.server_type.name} version from. Copy it yourself if it fits."
            )
            continue
        if (site_name, project) in here:
            no(f"It's already on {target.name}.")
            continue
        try:
            version = await target.mods.site(site_name).latest_for(split(project)[1], minecraft)
        except ModError as exc:
            no(str(exc))
            continue
        except SourceError as exc:
            no(f"{LABELS[site_name]} couldn't be asked: {exc}")
            continue
        if not version or minecraft not in version["game_versions"]:
            no(
                f"{LABELS[site_name]} has no {target.config.server_type.name} version of it "
                f"for Minecraft {minecraft}."
            )
            continue
        settings = settings_of(source, mod) if same_place else []
        coming.append(
            {
                "filename": mod.filename,
                "name": mod.name,
                "project": project,
                "version_id": version["version_id"],
                "version": version["version_number"],
                "settings": [p.relative_to(source.config.server_dir).as_posix() for p in settings],
            }
        )
    return {
        "target": target.server_id,
        "minecraft_version": minecraft,
        "coming": coming,
        "cant_come": cant,
        "settings_note": None
        if same_place
        else f"{target.config.server_type.name} keeps settings in another place, so "
        "settings files don't come along.",
    }


def _copy_settings(source: ServerContext, target: ServerContext, relative: str) -> None:
    """One settings file or folder, to the same place in the other server.
    Links inside a folder are left out."""
    src = source.config.server_dir / relative
    dst = target.config.server_dir / relative
    if src.is_symlink() or not is_inside(source.config.server_dir, src):
        return
    if not is_inside(target.config.server_dir, dst):
        raise ModError(f"{relative} would land outside {target.name}'s folder.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(
            src,
            dst,
            dirs_exist_ok=True,
            ignore=lambda folder, names: [n for n in names if (Path(folder) / n).is_symlink()],
        )
    else:
        shutil.copy2(src, dst)


async def copy(
    source: ServerContext, target: ServerContext, filenames: list[str], user: str = "system"
) -> dict[str, Any]:
    """Copy what can come, as one change on ``target``. Raises before
    anything changes when ``target`` runs, is busy, or nothing can come."""
    target.require_free()
    target.mods.require_server_offline(f"copy {target.mods.word}s to it")
    plan = await check(source, target, filenames)
    if not plan["coming"]:
        reasons = "; ".join(f"{c['name']}: {c['why']}" for c in plan["cant_come"])
        raise ModError(f"Nothing can be copied. {reasons}")
    # server.properties so the backup holds a file even when both folders
    # are still empty (they're kept as empty folders, so Undo empties them).
    folders = [folder_in_server(target, target.config.mods_dir), "server.properties"]
    if any(s.startswith("config") for item in plan["coming"] for s in item["settings"]):
        (target.config.server_dir / "config").mkdir(exist_ok=True)
        folders.append("config")
    if None in folders:
        raise ModError(
            f"Copying needs {target.name}'s {target.mods.server_type.content} folder inside "
            "its server folder, so the backup can put it back."
        )

    async def change(job: JobHandle | None) -> dict[str, Any]:
        copied = []
        for number, item in enumerate(plan["coming"], start=1):
            if job:
                job.step(f"Adding {item['name']} ({number} of {len(plan['coming'])})")
            try:
                result = await target.mods.install_from_site(
                    item["project"], user, version_id=item["version_id"], install_dependencies=True
                )
                missing = result.get("dependencies_failed") or result["needs_you"]
                if missing:  # half an add-on is worse than none: the backup goes back
                    first = missing[0]
                    raise ModError(
                        f"it needs {first.get('title') or first.get('name')}, which couldn't be added"
                    )
                for relative in item["settings"]:
                    await asyncio.to_thread(_copy_settings, source, target, relative)
            except Exception as exc:
                raise ModError(f"Adding {item['name']} failed: {exc}") from exc
            copied.append({**item, "installed": result["installed"]["filename"]})
        return {"copied": copied}

    safe = SafeChange(
        title=f"{TITLE} from {source.name}",
        change=change,
        stop_server=True,  # already stopped; this holds it so nothing starts it meanwhile
        take_backup=True,
        backup_name="before-copy-add-ons",
        backup_note=f"Before copying add-ons from {source.name}",
        backup_includes=[f for f in folders if f],
    )

    async def run(job: JobHandle | None) -> dict[str, Any]:
        target.mods.require_server_offline(f"copy {target.mods.word}s to it")
        return await run_safe_change(target.server, target.backups, safe, job=job, user=user)

    job, outcome = await target.core.jobs.run(
        "mods",
        f"{TITLE} from {source.name} to {target.name}",
        run,
        server_id=target.server_id,
        risky=True,
        user=user,
    )
    target.mods.record("copy_add_ons", user, detail=f"from {source.name}"[:500])
    return {
        "job_id": job.id,
        "copied": outcome["copied"],
        "cant_come": plan["cant_come"],
        "settings_note": plan["settings_note"],
        "safety_backup": outcome["safety_backup"],
        "undo": outcome["undo"],
    }
