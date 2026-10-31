"""Add from a file or a link: an add-on the owner already has, or one on a
GitHub release page.

A file is read before anything is written (ModManager.install_local_file):
a mod, a plugin or a datapack, checked against the server's type and the
safety notes' list of known bad files. A jar is looked up by its hash on
Modrinth and Hangar first, and on CurseForge by its fingerprint (a jar
dropped for a mod under "Needs you" is matched that way, never by its
name); one a site lists is treated as theirs, its updates included. Anything else stays "file" (or "github" with its repo and
tag): shown as "Not checked by Modrinth", and only a GitHub one gets updates.

Links are read only for GitHub releases. Every other link (Planet Minecraft,
SpigotMC, a creator's own page) gets "Download it in your browser and drop
the file here", so nothing is ever downloaded from a site the app doesn't
know. Planet Minecraft's pages couldn't be read reliably (the site turns
away programs), so it gets that answer too.

Each add is one change: one backup of the folder it writes to, one Undo.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from ..safechange import SafeChange, run_safe_change
from . import github
from .curseforge import MALWARE, fingerprint
from .manager import ModError, is_datapack
from .sources import SourceError
from .updateall import folder_in_server

if TYPE_CHECKING:
    from ..core import ServerContext
    from ..jobs import JobHandle
    from .manager import ModManager

BROWSER = "Download it in your browser and drop the file here."


async def known_source(mods: ModManager, data: bytes) -> tuple[str, dict[str, Any]] | None:
    """(the site, its project) when Modrinth, Hangar or CurseForge lists this
    very file."""
    try:
        project = await mods.modrinth.by_hash(hashlib.sha512(data).hexdigest())
    except SourceError:
        project = None  # still added, as a file nobody checked
    if project:
        return "modrinth", {"project_id": project}
    if mods.server_type.hangar_platform:
        try:
            project = await mods.hangar.by_hash(hashlib.sha256(data).hexdigest())
        except SourceError:
            project = None
        if project:
            return "hangar", {"project_id": f"hangar:{project}"}
    if mods.curseforge.loaders:
        try:
            found = await mods.curseforge.by_fingerprint([fingerprint(data)])
        except SourceError:
            found = {}  # still added, as a file nobody checked
        version = next(iter(found.values()), None)
        # The fingerprint is short, so the sha1 has to match too.
        if version and version["file"]["sha1"] == hashlib.sha1(data).hexdigest():
            if version["status"] == MALWARE:
                raise ModError("CurseForge found malware in this file, so it wasn't added.")
            return "curseforge", version
    return None


async def add_file(
    ctx: ServerContext,
    filename: str,
    data: bytes,
    user: str,
    source: str = "file",
    version: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add one file as a change of its own (backup first, one Undo)."""
    mods = ctx.mods
    ctx.require_free()
    mods.require_server_offline(f"add a {mods.word}")
    datapack = is_datapack(data)
    if not datapack:
        found = await known_source(mods, data)
        if found:
            source, version = found
    folder = folder_in_server(ctx, mods.datapacks_dir if datapack else ctx.config.mods_dir)
    title = f"Add {filename}"

    async def change(job: JobHandle | None) -> dict[str, Any]:
        return await mods.install_local_file(filename, data, user, source=source, version=version)

    top = ctx.config.server_dir / (folder or "")
    if folder is None or not any(p.is_file() for p in top.rglob("*")):
        # A backup holds only what is inside the server folder, and an empty
        # folder has nothing to put back: no Undo then (Remove takes it out).
        return await ctx.mod_change(f"Adding {filename}", lambda: change(None), user)
    plan = SafeChange(
        title=title,
        change=change,
        stop_server=True,  # already stopped; this holds it so nothing starts it meanwhile
        take_backup=True,
        backup_name="before-add",
        backup_note=f"Before {title}",
        backup_includes=[folder],
    )

    async def run(job: JobHandle | None) -> dict[str, Any]:
        mods.require_server_offline(f"add a {mods.word}")
        return await run_safe_change(ctx.server, ctx.backups, plan, job=job, user=user)

    _, outcome = await ctx.core.jobs.run(
        "mods", f"{title} on {ctx.name}", run, server_id=ctx.server_id, risky=True, user=user
    )
    return outcome


async def read_link(mods: ModManager, url: str) -> dict[str, Any]:
    """What a pasted link offers: a GitHub release's files to pick from,
    or the browser-and-drop answer. Nothing is downloaded."""
    found = github.read_link(url)
    if not found:
        return {"kind": "browser", "message": BROWSER}
    release = await mods.github.release(*found)
    return {
        "kind": "github",
        **{key: release[key] for key in ("repo", "tag", "name", "prerelease", "page")},
        "files": [
            {
                "version_id": f["version_id"],
                "filename": f["file"]["filename"],
                "size": f["file"]["size"],
                "checksum": bool(f["file"]["sha256"]),
            }
            for f in release["files"]
        ],
    }


async def add_from_link(ctx: ServerContext, version_id: str, user: str) -> dict[str, Any]:
    """Download the GitHub release file the owner picked, then add it."""
    ctx.require_free()
    ctx.mods.require_server_offline(f"add a {ctx.mods.word}")
    version = await ctx.mods.github.version(version_id)
    data, filename, _ = await ctx.mods.github.download(version)
    return await add_file(ctx, filename, data, user, source="github", version=version)
