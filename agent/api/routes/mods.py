"""Installed mods, search and install from Modrinth and Hangar, add from a
file or link, updates, rollback and dependencies."""

from __future__ import annotations

import asyncio
import time
from itertools import zip_longest

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from ...jobs import JobConflict
from ...mods.addfrom import add_file, add_from_link, read_link
from ...mods.dependencies import DependencyError
from ...mods.sources import SourceError, split
from ...mods.updateall import update_all
from ...security.auth import Principal
from ...security.permissions import MODS_MANAGE, SERVER_VIEW, require
from ..deps import audit, get_server
from ..errors import DOMAIN_ERRORS, audit_failure, respond_as
from ..responses import (
    DependencyInstall,
    DependencyPlan,
    DependencyReport,
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
    ModUpdated,
    ModUpdates,
    ModUploaded,
    ModVersions,
    UpdateAllPlan,
    UpdateAllResult,
)
from .models import (
    DependencyInstallRequest,
    ModFileRequest,
    ModInstallRequest,
    ModLinkAddRequest,
    ModLinkRequest,
    ModRollbackRequest,
    ModUpdateRequest,
    UpdateAllRequest,
)

router = APIRouter()


@router.get("/mods", response_model=ModList)
async def list_mods(principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)):
    mods = ctx.mods.list_installed()
    checks = ctx.mods.check_all()
    return {
        "mods": mods,
        "problems": checks["problems"],
        "claim": checks["claim"],
        "claim_note": checks["claim_note"],
        "limitations": checks["limitations"],
        "minecraft_version": checks["minecraft_version"],
        "fabric_loader": checks["fabric_loader"],
        "directory": str(ctx.config.mods_dir),
        "server_state": ctx.server.state.value,
        "updates_checked_at": ctx.db.get_setting("mod_updates_checked_at"),
    }


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
    if not ctx.config.server_type.hangar_platform:
        return await ctx.mods.modrinth.search(
            q, minecraft_version=version, limit=limit, offset=offset
        )
    # Plugin servers: Modrinth and Hangar side by side, each named. Their
    # files share no checksum (SHA-512 and SHA-256), so the same plugin can
    # show twice; installing it twice is refused by its hash.
    # shortcut: each site fills half the page, so once one runs out the
    # pages get shorter and the other's offset can repeat a few results.
    half, start = max(1, limit // 2), max(0, offset) // 2
    sites = (ctx.mods.modrinth, ctx.mods.hangar)
    answers = await asyncio.gather(
        *(site.search(q, minecraft_version=version, limit=half, offset=start) for site in sites),
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
        for name, a in zip(("Modrinth", "Hangar"), answers, strict=True)
        if not isinstance(a, dict)
    ]
    for name, answer in zip(("Modrinth", "Hangar"), answers, strict=True):
        for hit in answer["hits"] if isinstance(answer, dict) else []:
            hit.setdefault("source_name", name)
    hits = [hit for pair in zip_longest(*(a["hits"] for a in found)) for hit in pair if hit]
    return {"hits": hits, "total": sum(a.get("total") or 0 for a in found), "problems": problems}


@router.get("/mods/project/{project}", response_model=ModProject)
async def mod_project(
    project: str, principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    source, plain = split(project)
    site = ctx.mods.hangar if source == "hangar" else ctx.mods.modrinth
    if source == "hangar":
        ctx.mods.site("hangar")  # refused on a server Hangar has nothing for
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
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    data = await file.read(320 * 1024 * 1024)
    with audit_failure(ctx, request, "mod_upload", target=file.filename):
        result = await add_file(ctx, file.filename or "", data, user=principal.user)
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
        result = await add_from_link(ctx, payload.version_id, user=principal.user)
    audit(ctx, request, "mod_link_add", target=result["installed"]["filename"])
    return {"ok": True, **result}


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
