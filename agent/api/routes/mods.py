"""Installed mods, search and install from Modrinth, Hangar and CurseForge,
add from a file or link, "Needs you", updates, rollback and dependencies."""

from __future__ import annotations

import asyncio
import time
from functools import partial
from itertools import zip_longest
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from ... import linkedpack, protection, suggestions
from ...jobs import JobConflict
from ...mods import copy as copy_module
from ...mods import fit, sides
from ...mods.addfrom import add_file, add_from_link, read_link
from ...mods.dependencies import DependencyError
from ...mods.sources import SourceError, split
from ...mods.updateall import update_all
from ...security.auth import AuthError, Principal
from ...security.permissions import (
    DEEP_SCAN,
    MODS_MANAGE,
    SERVER_VIEW,
    SETTINGS_SERVER,
    check,
    check_server,
    may_add_own_jar,
    on_server,
    require,
)
from ..deps import audit, get_core, get_server
from ..errors import DOMAIN_ERRORS, audit_failure, respond_as
from ..responses import (
    CopyCheck,
    CopyDone,
    CopyTargets,
    DeepScanReport,
    DependencyInstall,
    DependencyPlan,
    DependencyReport,
    JarScanReport,
    JarScanUndone,
    ModChanged,
    ModHistory,
    ModImpact,
    ModInstalled,
    ModLink,
    ModList,
    ModProject,
    ModRemoved,
    ModRolledBack,
    ModSearch,
    ModSideChanged,
    ModSides,
    ModUpdated,
    ModUpdates,
    ModUploaded,
    ModVersions,
    Ok,
    PlayerFileAdded,
    PlayerOnlyOptions,
    Suggestions,
    UpdateAllPlan,
    UpdateAllResult,
)
from .models import (
    CopyAddonsRequest,
    DeepScanOptionsRequest,
    DeepScanQuarantineRequest,
    DeepScanRequest,
    DependencyInstallRequest,
    JarScanOptionsRequest,
    JarScanUndoRequest,
    ModFileRequest,
    ModInstallRequest,
    ModLinkAddRequest,
    ModLinkRequest,
    ModProjectRequest,
    ModRollbackRequest,
    ModSideRequest,
    ModUpdateRequest,
    PlayerOnlyOptionsRequest,
    UpdateAllRequest,
)

router = APIRouter()
# Agent-wide: every server's mods at once.
global_router = APIRouter()


@global_router.get("/mod-sides", response_model=ModSides)
async def mod_sides(principal: Principal = Depends(require(SERVER_VIEW)), core=Depends(get_core)):
    """ "On all servers" on the Mods page: who needs each mod (Server,
    Players or Both) and on which servers, for the servers this account
    may see."""
    servers = [c for c in core.servers.values() if SERVER_VIEW in on_server(principal, c.server_id)]
    return await asyncio.to_thread(sides.on_all_servers, servers)


@router.get("/mods", response_model=ModList)
async def list_mods(principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)):
    # Opening the Mods page asks Modrinth about a linked modpack, at most
    # once an hour and never for long (CurseForge waits for Check).
    try:
        await asyncio.wait_for(
            linkedpack.check_quietly(ctx, relay=False, max_age=linkedpack.CHECK_EVERY), 5
        )
    except TimeoutError:
        pass
    mods = ctx.mods.list_installed()
    sides.add_marks(ctx, mods)
    checks = ctx.mods.check_all()
    return {
        "mods": mods,
        "problems": checks["problems"],
        "overlaps": checks["overlaps"],
        "claim": checks["claim"],
        "claim_note": checks["claim_note"],
        "limitations": checks["limitations"],
        "minecraft_version": checks["minecraft_version"],
        "fabric_loader": checks["fabric_loader"],
        "directory": str(ctx.config.mods_dir),
        "server_state": ctx.server.state.value,
        "updates_checked_at": ctx.db.get_setting("mod_updates_checked_at"),
        "needs_you": ctx.mods.needs_you(),
        "stored_separately": ctx.db.get_setting("mods_stored_separately") is True,
        "fit": ctx.db.get_setting(fit.SETTING),
        "modpack": linkedpack.card(ctx, mods),
        "player_only": sides.player_files(ctx),
        "player_only_options": sides.options(ctx),
        "odd_setups": sides.odd_setups(ctx, mods),
    }


@router.post("/mods/side", response_model=ModSideChanged)
async def set_mod_side(
    payload: ModSideRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """Server / Players / Both. Players moves the jar out of the mods
    folder into the player-only folder; leaving Players moves it back."""
    with audit_failure(ctx, request, "mod_side", target=payload.filename):
        result = await ctx.mod_change(
            f"Marking {payload.filename}",
            lambda: sides.set_side(ctx, payload.filename, payload.side, principal.user),
            principal.user,
        )
    audit(ctx, request, "mod_side", target=payload.filename, detail=payload.side)
    return {"ok": True, **result}


@router.post("/mods/player-only/upload", response_model=PlayerFileAdded)
async def add_player_file(
    request: Request,
    file: UploadFile = File(...),
    confirm: bool = False,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """A mod, resource pack or shader pack for players only. The server
    never loads it; it's kept for the modpack."""
    data = await file.read(320 * 1024 * 1024)
    with audit_failure(ctx, request, "mod_player_add", target=file.filename):
        result = await ctx.mod_change(
            f"Adding {file.filename} for players",
            lambda: sides.add(
                ctx,
                file.filename or "",
                data,
                principal.user,
                confirm=confirm,
                own_jar=may_add_own_jar(principal, ctx.server_id),
            ),
            principal.user,
        )
    audit(ctx, request, "mod_player_add", target=file.filename)
    return {"ok": True, **result}


@router.post("/mods/player-only/fetch", response_model=PlayerFileAdded)
async def fetch_player_file(
    payload: ModFileRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """Get a missing player-only file again from Modrinth, by its hash."""
    with audit_failure(ctx, request, "mod_player_fetch", target=payload.filename):
        result = await ctx.mod_change(
            f"Getting {payload.filename} again",
            lambda: sides.fetch_again(ctx, payload.filename, principal.user),
            principal.user,
        )
    audit(ctx, request, "mod_player_fetch", target=payload.filename)
    return {"ok": True, **result}


@router.post("/mods/player-only/options", response_model=PlayerOnlyOptions)
async def player_only_options(
    payload: PlayerOnlyOptionsRequest,
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    """Back up the player-only folder or not, and leave out the files
    Modrinth lists (got again by hash after a restore)."""
    result = sides.set_options(ctx, payload.backup, payload.skip_listed)
    audit(
        ctx,
        request,
        "mod_player_options",
        detail=f"backup={payload.backup} skip_listed={payload.skip_listed}",
    )
    return result


@router.post("/mods/player-only/remove", response_model=Ok)
async def remove_player_file(
    payload: ModFileRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    with audit_failure(ctx, request, "mod_player_remove", target=payload.filename):
        await ctx.mod_change(
            f"Removing {payload.filename} from Players",
            lambda: sides.remove(ctx, payload.filename, principal.user),
            principal.user,
        )
    audit(ctx, request, "mod_player_remove", target=payload.filename)
    return {"ok": True}


@router.post("/mods/fit/hide", response_model=Ok)
async def hide_fit(
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """Hide the "Still fit?" card until the next change checks again."""
    ctx.db.set_setting(fit.SETTING, None)
    audit(ctx, request, "mod_fit_hide")
    return {"ok": True}


@router.get("/mods/search", response_model=ModSearch)
async def search_mods(
    q: str = "",
    minecraft_version: str = "",
    limit: int = 20,
    offset: int = 0,
    principal: Principal = Depends(require(SERVER_VIEW)),
    ctx=Depends(get_server),
):
    version = minecraft_version or ctx.server.mc_version or None
    mods = ctx.mods
    other = mods.hangar if ctx.config.server_type.hangar_platform else None
    if mods.curseforge.loaders:
        other = mods.curseforge
    if other is None:
        found = await mods.modrinth.search(q, minecraft_version=version, limit=limit, offset=offset)
        return {**found, "hits": protection.flag_look_alikes(found["hits"])}
    # Modrinth and Hangar (plugin servers) or CurseForge (mod servers) side
    # by side, each named. Their files share no checksum, so the same add-on
    # can show twice; installing it twice is refused by its hash.
    sites = (mods.modrinth, other)
    return await side_by_side(
        [(site.label, partial(site.search, q, minecraft_version=version)) for site in sites],
        limit,
        offset,
    )


async def side_by_side(searches: list[tuple[str, Any]], limit: int, offset: int) -> dict:
    """Two sites' results taken in turn, each named. ``searches`` are
    (site name, search taking limit and offset). One site down is said in
    ``problems``; both down is the first one's error."""
    # shortcut: each site fills half the page, so once one runs out the
    # pages get shorter and the other's offset can repeat a few results.
    half, start = max(1, limit // 2), max(0, offset) // 2
    names = [name for name, _ in searches]
    answers = await asyncio.gather(
        *(search(limit=half, offset=start) for _, search in searches),
        return_exceptions=True,
    )
    for answer in answers:
        if isinstance(answer, BaseException) and not isinstance(answer, SourceError):
            raise answer
    found = [a for a in answers if isinstance(a, dict)]
    if not found:
        raise answers[0]
    problems = [
        f"{name} isn't available right now."
        for name, a in zip(names, answers, strict=True)
        if not isinstance(a, dict)
    ]
    for name, answer in zip(names, answers, strict=True):
        for hit in answer["hits"] if isinstance(answer, dict) else []:
            hit.setdefault("source_name", name)
    hits = [hit for pair in zip_longest(*(a["hits"] for a in found)) for hit in pair if hit]
    return {
        "hits": protection.flag_look_alikes(hits),
        "total": sum(a.get("total") or 0 for a in found),
        "problems": problems,
    }


@router.get("/mods/project/{project}", response_model=ModProject)
async def mod_project(
    project: str, principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    source, plain = split(project)
    if source not in ("modrinth", "hangar", "curseforge"):
        raise HTTPException(status_code=404, detail="That add-on source isn't known.")
    ctx.mods.site(source)  # refused on a server the site has nothing for
    site: Any = getattr(ctx.mods, source)  # .modrinth, .hangar or .curseforge
    info = await site.project(plain)
    versions = await site.versions(plain, ctx.server.mc_version)
    return {
        "project": info,
        "versions": versions[:25],
        "installed": ctx.mods.preview_install(info["slug"]),
    }


@router.post("/mods/install", response_model=ModInstalled)
async def install_mod(
    payload: ModInstallRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    try:
        result = await ctx.mod_change(
            f"Installing {payload.project}",
            lambda: ctx.mods.install_from_site(
                payload.project,
                user=principal.user,
                version_id=payload.version_id,
                allow_replace=payload.allow_replace,
                install_dependencies=payload.install_dependencies,
            ),
            principal.user,
        )
    except JobConflict:
        raise
    except DOMAIN_ERRORS as exc:
        audit(ctx, request, "mod_install", target=payload.project, result="failed", detail=str(exc))
        ctx.mods.record(
            "install", principal.user, mod_id=payload.project, result="failed", detail=str(exc)
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit(
        ctx, request, "mod_install", target=payload.project, detail=result["installed"]["version"]
    )
    return {"ok": True, **result}


@router.post("/mods/upload", response_model=ModUploaded)
async def upload_mod(
    request: Request,
    file: UploadFile = File(...),
    confirm: bool = False,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    data = await file.read(320 * 1024 * 1024)
    with audit_failure(ctx, request, "mod_upload", target=file.filename):
        result = await add_file(
            ctx,
            file.filename or "",
            data,
            principal.user,
            confirm=confirm,
            own_jar=may_add_own_jar(principal, ctx.server_id),
        )
    audit(ctx, request, "mod_upload", target=file.filename)
    return {"ok": True, **result}


@router.post("/mods/link", response_model=ModLink)
async def mod_link(
    payload: ModLinkRequest,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """What a pasted link offers: a GitHub release's files to pick from, or
    "download it in your browser". Nothing is downloaded."""
    with respond_as(400):
        return await read_link(ctx.mods, payload.url)


@router.post("/mods/link/add", response_model=ModUploaded)
async def mod_link_add(
    payload: ModLinkAddRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    with audit_failure(ctx, request, "mod_link_add", target=payload.version_id):
        result = await add_from_link(
            ctx,
            payload.version_id,
            principal.user,
            payload.confirm,
            own_jar=may_add_own_jar(principal, ctx.server_id),
        )
    audit(ctx, request, "mod_link_add", target=result["installed"]["filename"])
    return {"ok": True, **result}


@router.post("/mods/needs-you/dismiss", response_model=Ok)
async def dismiss_needs_you(
    payload: ModProjectRequest,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """Take a mod off "Needs you" without adding it."""
    if not ctx.mods.done_needing(payload.project):
        raise HTTPException(status_code=404, detail="That mod isn't under Needs you.")
    return {"ok": True}


@router.get("/mods/impact", response_model=ModImpact)
async def mod_impact(
    filename: str, principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    with respond_as(404):
        return ctx.mods.removal_impact(filename)


@router.post("/mods/remove", response_model=ModRemoved)
async def remove_mod(
    payload: ModFileRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    with audit_failure(ctx, request, "mod_remove", target=payload.filename):
        result = await ctx.mod_change(
            f"Removing {payload.filename}",
            lambda: ctx.mods.remove(payload.filename, user=principal.user),
            principal.user,
        )
    audit(ctx, request, "mod_remove", target=payload.filename)
    return {"ok": True, **result}


@router.post("/mods/enable", response_model=ModChanged)
async def enable_mod(
    payload: ModFileRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    result = await ctx.mod_change(
        f"Turning on {payload.filename}",
        lambda: ctx.mods.set_enabled(payload.filename, True, user=principal.user),
        principal.user,
    )
    audit(ctx, request, "mod_enable", target=payload.filename)
    return {"ok": True, **result}


@router.post("/mods/disable", response_model=ModChanged)
async def disable_mod(
    payload: ModFileRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    result = await ctx.mod_change(
        f"Turning off {payload.filename}",
        lambda: ctx.mods.set_enabled(payload.filename, False, user=principal.user),
        principal.user,
    )
    audit(ctx, request, "mod_disable", target=payload.filename)
    return {"ok": True, **result}


@router.get("/mods/updates", response_model=ModUpdates)
async def mod_updates(
    refresh: bool = False,
    principal: Principal = Depends(require(SERVER_VIEW)),
    ctx=Depends(get_server),
):
    if refresh:
        await linkedpack.check_quietly(ctx, relay=True)
        updates = await ctx.mods.check_updates()
        return {"updates": updates, "checked_at": time.time()}
    updates = []
    for mod in ctx.mods.scan():
        entry = ctx.db.get_setting(f"mod_update:{mod.mod_id}")
        if entry and entry.get("installed_version") == mod.version:
            updates.append(entry)
    return {"updates": updates, "checked_at": ctx.db.get_setting("mod_updates_checked_at")}


@router.post("/mods/update", response_model=ModUpdated)
async def update_mod(
    payload: ModUpdateRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    with respond_as(400), audit_failure(ctx, request, "mod_update", target=payload.filename):
        result = await ctx.mod_change(
            f"Updating {payload.filename}",
            lambda: ctx.mods.update(
                payload.filename, user=principal.user, version_id=payload.version_id
            ),
            principal.user,
        )
    audit(ctx, request, "mod_update", target=payload.filename)
    return {"ok": True, **result}


@router.get("/mods/update-all", response_model=UpdateAllPlan)
async def update_all_plan(
    principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    """What Update all would change: every mod or plugin with a newer
    version on Modrinth (turned-off ones marked), asked just now."""
    plan = await ctx.mods.update_plan()
    return {**plan, "server_running": ctx.server.running, "blocked_by": ctx.server.held_by}


@router.post("/mods/update-all", response_model=UpdateAllResult)
async def update_all_mods(
    payload: UpdateAllRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """Update the ticked ones as one change: one backup first, and all of
    them put back from it if any update fails."""
    with respond_as(400), audit_failure(ctx, request, "mod_update_all"):
        result = await update_all(
            ctx, [item.model_dump() for item in payload.mods], user=principal.user
        )
    audit(ctx, request, "mod_update_all", detail=", ".join(u["name"] for u in result["updated"]))
    return {"ok": True, **result}


@router.get("/suggestions", response_model=Suggestions)
async def addon_suggestions(
    technical: bool = False,
    principal: Principal = Depends(require(SERVER_VIEW)),
    ctx=Depends(get_server),
):
    """Good to have, often used with your mods, speed-ups (only with a
    measured reason), For your players and the live map link. Checked
    against this server; nothing is installed here."""
    return await suggestions.lists(ctx, technical)


def _may_change_mods(principal: Principal, server_id: str) -> bool:
    try:
        check_server(principal, server_id)
        check(principal, MODS_MANAGE, server_id)
    except AuthError:
        return False
    return True


def _target(core, principal: Principal, ctx, server_id: str):
    target = core.servers.get(server_id)
    if target is None or target is ctx:
        raise HTTPException(status_code=404, detail="Pick another server to copy to.")
    if not _may_change_mods(principal, server_id):
        raise HTTPException(
            status_code=403, detail=f"Your account can't change add-ons on {target.name}."
        )
    return target


@router.get("/mods/copy", response_model=CopyTargets)
async def copy_targets(
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
    core=Depends(get_core),
):
    """The servers this account may copy add-ons to: others with add-ons
    where it can change them too."""
    return {
        "targets": [
            {"id": other.server_id, "name": other.name, "type": other.config.server_type.id}
            for other in core.servers.values()
            if other is not ctx
            and other.config.server_type.has_content
            and _may_change_mods(principal, other.server_id)
        ]
    }


@router.post("/mods/copy/check", response_model=CopyCheck)
async def check_copy(
    payload: CopyAddonsRequest,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
    core=Depends(get_core),
):
    """What would come and what can't, and why. Changes nothing."""
    target = _target(core, principal, ctx, payload.target)
    with respond_as(400):
        return await copy_module.check(ctx, target, payload.filenames)


@router.post("/mods/copy", response_model=CopyDone)
async def copy_addons(
    payload: CopyAddonsRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
    core=Depends(get_core),
):
    """Copy the ticked add-ons and their settings to the other server, as
    one change there: one backup first, one Undo."""
    target = _target(core, principal, ctx, payload.target)
    with respond_as(400), audit_failure(target, request, "mod_copy"):
        result = await copy_module.copy(ctx, target, payload.filenames, user=principal.user)
    audit(
        target,
        request,
        "mod_copy",
        detail=f"from {ctx.name}: " + ", ".join(c["name"] for c in result["copied"]),
    )
    return {"ok": True, **result}


@router.get("/mods/versions/{mod_id}", response_model=ModVersions)
async def mod_versions(
    mod_id: str, principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    return {"versions": ctx.mods.versions_for(mod_id)}


@router.post("/mods/rollback", response_model=ModRolledBack)
async def rollback_mod(
    payload: ModRollbackRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    with audit_failure(ctx, request, "mod_rollback", target=payload.mod_id):
        result = await ctx.mod_change(
            f"Rolling back {payload.mod_id}",
            lambda: ctx.mods.rollback(payload.mod_id, payload.archive_path, user=principal.user),
            principal.user,
        )
    audit(ctx, request, "mod_rollback", target=payload.mod_id)
    return {"ok": True, **result}


@router.get("/mods/history", response_model=ModHistory)
async def mod_history(
    limit: int = 100, principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    return {"history": ctx.mods.history(max(1, min(limit, 500)))}


@router.get("/mods/scan", response_model=JarScanReport)
async def jar_scan(principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)):
    """The jar check's last results. Asks nothing."""
    return ctx.jarscan.report()


@router.post("/mods/scan", response_model=JarScanReport)
async def jar_scan_again(
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """Check every jar again, looking each one up on Modrinth and CurseForge."""
    report = await ctx.jarscan.scan(everything=True)
    audit(ctx, request, "jar_scan")
    return report


@router.post("/mods/scan/undo", response_model=JarScanUndone)
async def jar_scan_undo(
    payload: JarScanUndoRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    with audit_failure(ctx, request, "jar_scan_undo", target=payload.id):
        result = await ctx.mod_change(
            f"Putting back {payload.id}",
            lambda: ctx.jarscan.undo(payload.id, user=principal.user),
            principal.user,
        )
    audit(ctx, request, "jar_scan_undo", target=payload.id)
    return {"ok": True, **result}


@router.get("/mods/deepscan", response_model=DeepScanReport)
async def deep_scan(principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)):
    """The deep scan's scanners and its last results."""
    return ctx.deepscan.status()


@router.post("/mods/deepscan", response_model=DeepScanReport)
async def deep_scan_start(
    payload: DeepScanRequest,
    request: Request,
    principal: Principal = Depends(require(DEEP_SCAN)),
    ctx=Depends(get_server),
):
    """Start a deep scan. It runs as a job; poll GET for the results."""
    target = payload.filename or payload.scope
    with audit_failure(ctx, request, "deep_scan", target=target):
        report = ctx.deepscan.start(
            payload.scope, payload.scanners, payload.filename, user=principal.user
        )
    audit(ctx, request, "deep_scan", target=target, detail=",".join(payload.scanners))
    return report


@router.post("/mods/deepscan/quarantine", response_model=DeepScanReport)
async def deep_scan_quarantine(
    payload: DeepScanQuarantineRequest,
    request: Request,
    principal: Principal = Depends(require(DEEP_SCAN)),
    ctx=Depends(get_server),
):
    """Move a file the deep scan listed into quarantine."""
    with audit_failure(ctx, request, "deep_scan_quarantine", target=payload.path):
        report = await ctx.mod_change(
            f"Moving {payload.path} to quarantine",
            lambda: ctx.deepscan.quarantine(payload.root, payload.path, user=principal.user),
            principal.user,
        )
    audit(ctx, request, "deep_scan_quarantine", target=payload.path)
    return report


@router.post("/mods/scan/options", response_model=JarScanReport)
async def jar_scan_options(
    payload: JarScanOptionsRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    if payload.ask_first is not None and not principal.is_owner:
        raise HTTPException(403, "Only the owner can change Ask me first.")
    _set_options(
        ctx,
        rescan_weekly=payload.weekly,
        rescan_on_change=payload.on_change,
        check_before_start=payload.before_start,
        ask_before_stop=payload.ask_first,
    )
    audit(ctx, request, "jar_scan_options", detail=str(payload.model_dump(exclude_none=True)))
    return ctx.jarscan.report()


@router.post("/mods/deepscan/options", response_model=DeepScanReport)
async def deep_scan_options(
    payload: DeepScanOptionsRequest,
    request: Request,
    principal: Principal = Depends(require(DEEP_SCAN)),
    ctx=Depends(get_server),
):
    """Deep scan before each start, and the daily or weekly deep scan."""
    _set_options(
        ctx,
        deep_scan_before_start=payload.before_start,
        deep_scan_schedule=payload.schedule,
        deep_scan_time=payload.time,
        deep_scan_day=payload.day,
    )
    audit(ctx, request, "deep_scan_options", detail=str(payload.model_dump(exclude_none=True)))
    await ctx.deepscan.replan()  # works out the next run now, so the apps can show it
    return ctx.deepscan.status()


def _set_options(ctx, **values) -> None:
    for key, value in values.items():
        if value is not None:
            ctx.config.set(f"mods.{key}", value)
    ctx.config.save()


@router.get("/mods/dependencies", response_model=DependencyReport)
async def mod_dependencies(
    principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    """Every declared dependency, with readable names, ranges and status."""
    report = ctx.mods.deps.analyse()
    try:
        await ctx.mods.deps.describe(report)
    except (SourceError, DependencyError) as exc:
        report["lookup_error"] = f"Mod names could not be looked up on Modrinth: {exc}"
    return report


@router.post("/mods/dependencies/plan", response_model=DependencyPlan)
async def plan_dependencies(
    payload: DependencyInstallRequest,
    principal: Principal = Depends(require(SERVER_VIEW)),
    ctx=Depends(get_server),
):
    """What would be downloaded, including dependencies of dependencies. Downloads nothing."""
    return await ctx.mods.deps.plan(payload.mod_ids)


@router.post("/mods/dependencies/install", response_model=DependencyInstall)
async def install_dependencies(
    payload: DependencyInstallRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    with audit_failure(ctx, request, "dependencies_install"):
        result = await ctx.mod_change(
            "Installing dependencies",
            lambda: ctx.mods.deps.install(payload.mod_ids, user=principal.user),
            principal.user,
        )
    installed = [r["title"] for r in result["results"] if r["result"] == "installed"]
    audit(ctx, request, "dependencies_install", detail=", ".join(installed) or "nothing installed")
    return result


@router.get("/mods/check")
async def mod_check(principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)):
    return ctx.mods.check_all()
