"""A linked modpack: the server remembers the modpack it came from, so the
app can offer "Modpack update".

The link is a small file in the server folder (``LINK_FILE``): the site,
the project and version, and each file the pack put in (by the pack's own
hash, plus the sha256 of what landed on disk). Being in the server folder,
it is in the backup taken before a Modpack update, so Undo puts the old
link back together with the old mods. From CurseForge only the project and
file ids are kept (the relay's rules), never its names or hashes.

Modpack update:
  * ``check`` asks the site for the pack's newest version (Modrinth in the
    background add-on check and when the Mods page opens; CurseForge only
    when someone presses Check, as for mods)
  * ``preview`` downloads that version like a picked pack and says what
    changes, comparing files by hash, never by name: added, removed, newer
    (same mod, another file), the mods added by you (on disk, not from the
    pack; they are kept) and the config files you changed (kept unless you
    tick "use the modpack's")
  * ``update`` makes the change through the safe-change routine like
    Update all: server stopped, one backup of what it touches, all or
    nothing, one Undo. Then the "Still fit?" check runs.

Unlink forgets the link; the server keeps its mods and becomes a normal one.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import modpack
from .events import Event
from .mods.jarinfo import DISABLED_SUFFIX, sha256_file
from .mods.sides import from_pack
from .security.paths import EXECUTABLE_EXTENSIONS, PathSafetyError, check_archive_member

if TYPE_CHECKING:
    from .core import AgentCore, ServerContext
    from .jobs import JobHandle

log = logging.getLogger("msc.linkedpack")

LINK_FILE = "command-block-modpack.json"
UPDATE_SETTING = "modpack_update"  # the newest version the site named, and when
CHECK_EVERY = 3600.0  # seconds between checks when the Mods page opens
SITES = {"modrinth": "Modrinth", "curseforge": "CurseForge"}
ID_RE = re.compile(r"^[\w:.-]{1,80}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
LOOKUP_MAX_BYTES = 64 * 1024 * 1024  # a CurseForge pack read whole to find it
TITLE = "Modpack update"


# ---------------------------------------------------------------- the link
def origin(site: str, version: dict[str, Any]) -> dict[str, Any]:
    """Where a pack came from, from the site's version (agent/mods/*.py)."""
    found = {
        "site": site,
        "project": str(version.get("project_id") or ""),
        "version_id": str(version.get("version_id") or ""),
    }
    # Nothing from CurseForge but its ids is kept: the version's name comes
    # from the pack's own file instead (_new_link).
    if site == "modrinth":
        found["version"] = str(version.get("version_number") or version.get("name") or "")[:60]
        if version.get("date_published"):
            found["published"] = str(version["date_published"])[:40]
    return found


def read_link(ctx: ServerContext) -> dict[str, Any] | None:
    """The server's link, or None. The file sits in the server folder, so
    it is read strictly: anything odd counts as not linked."""
    try:
        data = json.loads((ctx.config.server_dir / LINK_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if _valid(data) else None


def _valid(data: Any) -> bool:
    if not isinstance(data, dict) or data.get("site") not in SITES:
        return False
    if not all(ID_RE.match(str(data.get(key) or "")) for key in ("project", "version_id")):
        return False
    files, overrides = data.get("files"), data.get("overrides")
    if not isinstance(files, list) or not isinstance(overrides, dict):
        return False
    for entry in files:
        if not isinstance(entry, dict) or not _plain_path(entry.get("path")):
            return False
        if not isinstance(entry.get("key"), str) or not HASH_RE.match(str(entry.get("sha256"))):
            return False
    return all(_plain_path(k) and HASH_RE.match(str(v)) for k, v in overrides.items())


def _plain_path(path: Any) -> bool:
    """A path inside the server folder, written the way a pack writes one."""
    if not isinstance(path, str) or not path or "\\" in path or ":" in path:
        return False
    parts = path.split("/")
    return not path.startswith("/") and all(part not in ("", ".", "..") for part in parts)


def _save(ctx: ServerContext, link: dict[str, Any]) -> None:
    path = ctx.config.server_dir / LINK_FILE
    temp = path.with_name(f".{LINK_FILE}.part")
    temp.write_text(json.dumps(link, indent=1), "utf-8")
    temp.replace(path)


def _file_entry(folder: Path, item: modpack.PackFile) -> dict[str, Any] | None:
    path = folder / item.path
    if not item.key or not path.is_file():
        return None
    entry = {
        "path": item.path.replace("\\", "/"),
        "key": item.key,
        "project": item.project,
        "sha256": sha256_file(path),
    }
    # The pack's own Server / Players / Both for it (agent/mods/sides.py).
    side = from_pack({"client": item.client, "server": item.server})
    if side:
        entry["side"] = side
    return entry


def _override_hashes(pack: modpack.Pack, archive: Path) -> dict[str, str]:
    """The sha256 of each override file the pack puts on a server, as the
    pack has it (a server-only file wins over the shared one)."""
    found: dict[str, str] = {}
    with zipfile.ZipFile(archive) as zf:
        for member, relative in modpack._override_members(zf, pack.folders):
            top = relative.split("/", 1)[0]
            if top in modpack.CLIENT_ONLY or Path(relative).suffix.lower() in EXECUTABLE_EXTENSIONS:
                continue
            digest = hashlib.sha256()
            with zf.open(member) as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            found[relative] = digest.hexdigest()
    return found


def _new_link(
    found: dict[str, Any],
    pack: modpack.Pack,
    files: list[dict[str, Any]],
    overrides: dict[str, str],
) -> dict[str, Any]:
    return {
        **found,
        "name": pack.name,
        "version": found.get("version") or pack.version or "",
        "minecraft_version": pack.minecraft,
        "type": pack.type_id,
        "loader_version": pack.loader_version,
        "files": files,
        "overrides": overrides,
        "linked_at": time.time(),
    }


async def remember(ctx: ServerContext, pack: modpack.Pack, token: str, archive: Path) -> None:
    """After a pack went in: link the server to it. A pack picked on a site
    is known; an uploaded one is looked up by its hash. Never fails the
    import: a pack nobody can find just isn't linked."""
    try:
        found = modpack.ORIGIN.get(token) or await _look_up(ctx.core, pack, archive)
        if not found:
            return
        folder = ctx.config.server_dir

        def build() -> dict[str, Any]:
            files = [e for e in (_file_entry(folder, f) for f in pack.downloads) if e]
            return _new_link(found, pack, files, _override_hashes(pack, archive))

        _save(ctx, await asyncio.to_thread(build))
        ctx.db.set_setting(UPDATE_SETTING, None)
    except Exception:
        log.warning("couldn't link %s to its modpack", ctx.server_id, exc_info=True)


async def _look_up(core: AgentCore, pack: modpack.Pack, archive: Path) -> dict[str, Any] | None:
    """Which site version an uploaded pack file is, by its hash."""
    from .mods.curseforge import CurseForgeClient, fingerprint
    from .mods.modrinth import ModrinthClient

    if pack.site == "Modrinth":
        sha512 = await asyncio.to_thread(_sha512, archive)
        client = ModrinthClient(core.config)
        try:
            version = await client.version_by_hash(sha512)
        finally:
            await client.close()
        return origin("modrinth", version) if version and version.get("version_id") else None
    if archive.stat().st_size > LOOKUP_MAX_BYTES:
        return None
    data = await asyncio.to_thread(archive.read_bytes)
    print_ = fingerprint(data)
    version = (await CurseForgeClient(core.config).by_fingerprint([print_])).get(print_)
    return origin("curseforge", version) if version else None


def _sha512(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def unlink(ctx: ServerContext) -> bool:
    """Forget the link. The server keeps every mod and file."""
    path = ctx.config.server_dir / LINK_FILE
    had = path.is_file()
    path.unlink(missing_ok=True)
    ctx.db.set_setting(UPDATE_SETTING, None)
    return had


# ---------------------------------------------------------------- the card
def card(ctx: ServerContext, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The Mods page's modpack card, and each mod row marked
    ``added_by_you`` when the pack didn't put it there."""
    link = read_link(ctx)
    if not link:
        return None
    from_pack = {entry["sha256"] for entry in link["files"]}
    for row in rows:
        row["added_by_you"] = bool(row.get("sha256")) and row["sha256"] not in from_pack
    newest = ctx.db.get_setting(UPDATE_SETTING) or {}
    update = None
    if newest.get("for") == link["version_id"] and newest.get("version_id"):
        update = {"version_id": newest["version_id"], "version": newest.get("version") or ""}
    return {
        "name": link.get("name") or "",
        "site": SITES[link["site"]],
        "version": link.get("version") or "",
        "update": update,
        "checked_at": newest.get("checked_at"),
        "added_by_you": sum(1 for row in rows if row["added_by_you"]),
    }


async def check(ctx: ServerContext, relay: bool = True, max_age: float = 0.0) -> None:
    """Ask the site for the pack's newest version and save it for the card.
    ``relay`` False leaves CurseForge out (the background check); a check
    newer than ``max_age`` seconds is kept."""
    link = read_link(ctx)
    if not link or (link["site"] == "curseforge" and not relay):
        return
    saved = ctx.db.get_setting(UPDATE_SETTING) or {}
    if (
        max_age
        and saved.get("for") == link["version_id"]
        and time.time() - float(saved.get("checked_at") or 0) < max_age
    ):
        return
    newest = await _newest(ctx.core, link)
    found = {"for": link["version_id"], "checked_at": time.time()}
    if newest and newest.get("version_id") and _is_newer(link, newest):
        # CurseForge's name for it isn't kept; the card says "a new version".
        name = (
            newest.get("version_number") or newest.get("name") if link["site"] == "modrinth" else ""
        )
        found.update(version_id=str(newest["version_id"]), version=str(name or "")[:60])
    ctx.db.set_setting(UPDATE_SETTING, found)


def _is_newer(link: dict[str, Any], newest: dict[str, Any]) -> bool:
    if newest["version_id"] == link["version_id"]:
        return False
    # A test build installed on purpose isn't "updated" to an older release.
    published, theirs = link.get("published"), newest.get("date_published")
    return not (published and theirs and str(theirs) <= published)


async def _newest(core: AgentCore, link: dict[str, Any]) -> dict[str, Any] | None:
    from .mods.curseforge import MODPACKS, CurseForgeClient
    from .mods.modrinth import ModrinthClient

    if link["site"] == "curseforge":
        return await CurseForgeClient(core.config).latest_for(link["project"], None, kind=MODPACKS)
    client = ModrinthClient(core.config)
    try:
        return await client.latest_for(link["project"], None, loaders=())
    finally:
        await client.close()


async def check_quietly(ctx: ServerContext, relay: bool, max_age: float = 0.0) -> None:
    """``check``, where a site that can't be reached only means no news."""
    try:
        await check(ctx, relay=relay, max_age=max_age)
    except Exception as exc:  # the Mods page still opens
        log.info("modpack check for %s failed: %s", ctx.server_id, exc)


# ---------------------------------------------------------------- the update
def _need_link(ctx: ServerContext) -> dict[str, Any]:
    link = read_link(ctx)
    if not link:
        raise modpack.ModpackError("This server isn't linked to a modpack.")
    return link


async def preview(ctx: ServerContext, version_id: str | None = None) -> dict[str, Any]:
    """Download the pack's newest version (or ``version_id``) and say what
    Modpack update would change. Nothing on the server changes."""
    link = _need_link(ctx)
    fetch = modpack.from_modrinth if link["site"] == "modrinth" else modpack.from_curseforge
    token, pack = await fetch(ctx.core, link["project"], version_id=version_id)
    if modpack.ORIGIN[token]["version_id"] == link["version_id"]:
        modpack.forget(ctx.core, token)
        raise modpack.ModpackError("This server already has that version of the modpack.")
    archive = modpack.upload_path(ctx.core, token)
    plan = await asyncio.to_thread(_plan, ctx, link, pack, archive)
    return {"token": token, "pack": pack.to_dict(), "changes": plan}


def _plan(
    ctx: ServerContext, link: dict[str, Any], pack: modpack.Pack, archive: Path
) -> dict[str, Any]:
    """What changes, compared by hash (and by project for "newer")."""
    folder = ctx.config.server_dir
    old = {entry["key"]: entry for entry in link["files"]}
    new = {item.key: item for item in pack.downloads if item.key}
    added = [item for key, item in new.items() if key not in old]
    removed = [entry for key, entry in old.items() if key not in new]
    newer = []
    for item in list(added):
        same = next((e for e in removed if item.project and e.get("project") == item.project), None)
        if same:
            added.remove(item)
            removed.remove(same)
            newer.append({"name": item.name, "from": Path(same["path"]).name, "to": item.name})
    from_pack = {entry["sha256"] for entry in link["files"]}
    yours = [mod.filename for mod in ctx.mods.scan() if mod.sha256 and mod.sha256 not in from_pack]
    configs = _changed_by_you(folder, link, _override_hashes(pack, archive))
    return {
        "from_version": link.get("version") or "",
        "to_version": pack.version or "",
        "added": [item.name for item in added],
        "removed": [Path(entry["path"]).name for entry in removed],
        "newer": newer,
        "added_by_you": yours,
        "configs_changed_by_you": configs,
        "version_change": not modpack._matches(ctx, pack),
        "minecraft_version": pack.minecraft,
        "problems": pack.problems,
    }


def _changed_by_you(folder: Path, link: dict[str, Any], incoming: dict[str, str]) -> list[str]:
    """The pack's config files the owner changed since the pack put them
    in, that the new version would write over."""
    changed = []
    for relative, theirs in incoming.items():
        before = link["overrides"].get(relative)
        if not before or theirs == before:
            continue
        try:
            path = check_archive_member(folder, relative)
        except PathSafetyError:
            continue
        if path.is_file() and sha256_file(path) != before:
            changed.append(relative)
    return changed


async def update(
    ctx: ServerContext, token: str, use_pack_configs: bool = False, user: str = "system"
) -> dict[str, Any]:
    """Modpack update: the version in ``token`` (from ``preview``) put in
    place of the one the server has, as one change with one Undo. Mods
    added by you are kept, and so are config files you changed unless
    ``use_pack_configs``."""
    from .safechange import SafeChange, before, run_safe_change
    from .servertypes.install import change_version

    link = _need_link(ctx)
    found = modpack.ORIGIN.get(token)
    if not found or found["site"] != link["site"] or found["project"] != link["project"]:
        raise modpack.ModpackError("That isn't this server's modpack. Check for the update again.")
    archive = modpack.upload_path(ctx.core, token)
    pack = await modpack.read(ctx.core, token, archive)
    if pack.problems:
        raise modpack.ModpackError(pack.problems[0])
    plan = await asyncio.to_thread(_plan, ctx, link, pack, archive)
    incoming = await asyncio.to_thread(_override_hashes, pack, archive)
    keep = frozenset() if use_pack_configs else frozenset(plan["configs_changed_by_you"])
    old_keys = {entry["key"] for entry in link["files"]}
    new_keys = {item.key for item in pack.downloads}
    # Only the files that are new to the server are downloaded: one the
    # owner turned off stays off.
    adding = replace(
        pack, files=[f for f in pack.files if f.status != modpack.DOWNLOAD or f.key not in old_keys]
    )
    dropping = [entry for entry in link["files"] if entry["key"] not in new_keys]

    version_result = None
    if plan["version_change"]:
        version_result = await change_version(
            ctx, pack.type_id, pack.minecraft, pack.loader_version, user=user
        )
    folder = ctx.config.server_dir
    targets = [f.path.replace("\\", "/") for f in adding.downloads]
    targets += [rel for rel in incoming if rel not in keep]
    targets += [entry["path"] for entry in dropping] + [LINK_FILE]
    title = f"{TITLE} of {pack.name} to {pack.version or 'the newest version'}"

    async def change(job: JobHandle | None) -> dict[str, Any]:
        new_files = [rel for rel in targets if not (folder / rel).exists()]
        try:
            removed = await asyncio.to_thread(_remove, folder, dropping)
            result = await modpack.put_in_place(adding, archive, folder, job, ctx.mods, keep)
            ok, detail = await asyncio.to_thread(modpack.check_in_place, adding, folder, result)
            if not ok:
                raise modpack.ModpackError(f"The update couldn't be checked: {detail}")

            def relink() -> None:
                kept = [entry for entry in link["files"] if entry["key"] in new_keys]
                fresh = [e for e in (_file_entry(folder, f) for f in adding.downloads) if e]
                _save(ctx, _new_link(found, pack, kept + fresh, incoming))

            await asyncio.to_thread(relink)
        except BaseException:
            await asyncio.to_thread(modpack._remove_new_files, folder, new_files)
            raise
        return {**result, "removed": removed, "detail": detail}

    plan_change = SafeChange(
        title=title,
        change=change,
        check=None,  # the change checks itself, so it can tidy up first
        stop_server=True,
        take_backup=True,
        backup_name="before-modpack-update",
        backup_note=before(title),
        # Only what the update touches, not the world: one small backup.
        backup_includes=list(dict.fromkeys(rel.split("/", 1)[0] for rel in targets)),
    )

    async def run(job: JobHandle | None) -> dict[str, Any]:
        return await run_safe_change(ctx.server, ctx.backups, plan_change, job=job, user=user)

    job, outcome = await ctx.core.jobs.run(
        "modpack", title, run, server_id=ctx.server_id, risky=True, user=user
    )
    modpack.forget(ctx.core, token)
    modpack._list_needs(ctx, adding)
    ctx.core.db.audit(
        "modpack_update",
        user=user,
        target=ctx.server_id,
        detail=f"{pack.name} {plan['from_version']} -> {plan['to_version']}"[:500],
        server_id=ctx.server_id,
    )
    about = f"{pack.name} {pack.version or ''}".strip()
    await ctx.bus.publish(
        Event(
            type="modpack_updated",
            level="success",
            message=f"Updated the {pack.name} modpack on {ctx.name} to {pack.version or 'its newest version'}",
            # "about" starts the "Still fit?" check for the mods you added.
            data={"about": about, "changes": plan},
        )
    )
    return {
        "job_id": job.id,
        "changes": plan,
        "kept_configs": sorted(keep),
        "version_change": version_result is not None,
        "undo": outcome.get("undo"),
    }


def _remove(folder: Path, dropping: list[dict[str, Any]]) -> list[str]:
    """Take away the files the old version put in and the new one doesn't
    have, only while they are still the pack's own (a file the owner
    replaced is theirs now). Turned-off copies go too."""
    removed = []
    for entry in dropping:
        try:
            path = check_archive_member(folder, entry["path"])
        except PathSafetyError:
            continue
        for candidate in (path, path.with_name(path.name + DISABLED_SUFFIX)):
            if candidate.is_file() and not candidate.is_symlink():
                if sha256_file(candidate) == entry["sha256"]:
                    candidate.unlink()
                    removed.append(candidate.name)
    return removed
