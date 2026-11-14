"""Importing a modpack (a Modrinth .mrpack, or a CurseForge pack from a
file or picked on CurseForge): as a new server (agent-wide) or into an
existing one (per server)."""

from __future__ import annotations

import asyncio
from functools import partial

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from ... import linkedpack, modpack
from ...mods.curseforge import MODPACKS, CurseForgeClient
from ...mods.modrinth import ModrinthClient
from ...security.auth import AuthError, Principal
from ...security.permissions import (
    MODS_MANAGE,
    SERVERS_MANAGE,
    SETTINGS_SERVER,
    may_add_own_jar,
    require,
)
from ..deps import audit, get_core, get_server
from ..responses import (
    ModpackImported,
    ModpackInspected,
    ModpackUpdated,
    ModpackUpdatePreview,
    ModSearch,
    Ok,
    ServerCreated,
)
from .models import (
    CurseForgeModpackRequest,
    ModpackIntoRequest,
    ModpackNewRequest,
    ModpackPreviewRequest,
    ModpackUpdateRequest,
    ModrinthModpackRequest,
)
from .mods import side_by_side

# Agent-wide: reading a pack, and making a new server from it.
global_router = APIRouter()
# Per server: importing into this server.
router = APIRouter()

CHUNK = 1024 * 1024


def _own_jars_allowed(principal: Principal, server_id: str | None, said_yes: bool) -> bool:
    """A yes to a pack's own jars counts only from an account holding "Add
    own .jar" on that server."""
    if said_yes and not may_add_own_jar(principal, server_id):
        raise AuthError(modpack.OWN_JARS_NEEDED, status=403)
    return said_yes


@global_router.post("/modpacks/inspect", response_model=ModpackInspected)
async def inspect_modpack(
    request: Request,
    file: UploadFile = File(...),
    server_id: str = "",
    principal: Principal = Depends(require(MODS_MANAGE)),
    core=Depends(get_core),
):
    """Upload a .mrpack or a CurseForge pack's .zip and say what it holds,
    before anything changes. With ``server_id``, also what importing it into
    that server would do."""
    if not (file.filename or "").lower().endswith((".mrpack", ".zip")):
        raise HTTPException(
            status_code=400, detail="Choose a modpack file (.mrpack, or a CurseForge pack's .zip)."
        )
    token, path = modpack.new_upload(core)
    size = 0
    try:
        with path.open("wb") as out:
            while chunk := await file.read(CHUNK):
                size += len(chunk)
                if size > modpack.MAX_PACK_BYTES:
                    raise HTTPException(
                        status_code=400, detail="That pack is bigger than 1 GB, so it wasn't read."
                    )
                await asyncio.to_thread(out.write, chunk)
        pack = await modpack.read(core, token, path)
    except BaseException:
        modpack.forget(core, token)
        raise
    audit(core, request, "modpack_inspect", target=pack.name)
    return _inspected(core, token, pack, server_id)


def _inspected(core, token: str, pack: modpack.Pack, server_id: str) -> dict:
    result: dict = {"token": token, "pack": pack.to_dict()}
    if server_id:
        result["into"] = modpack.plan_into(core.get_server(server_id), pack)
    return result


@global_router.get("/modpacks/search", response_model=ModSearch)
async def search_modpacks(
    q: str = "",
    limit: int = 20,
    offset: int = 0,
    principal: Principal = Depends(require(MODS_MANAGE)),
    core=Depends(get_core),
):
    """Modrinth's and CurseForge's modpacks side by side, each named. Picking
    one reads it (``POST /modpacks/modrinth`` or ``/modpacks/curseforge``)."""
    modrinth, curseforge = ModrinthClient(core.config), CurseForgeClient(core.config)
    try:
        return await side_by_side(
            [
                (modrinth.label, partial(modrinth.search, q, loaders=(), project_type="modpack")),
                (curseforge.label, partial(curseforge.search, q, kind=MODPACKS)),
            ],
            limit,
            offset,
        )
    finally:
        await modrinth.close()


@global_router.post("/modpacks/modrinth", response_model=ModpackInspected)
async def fetch_modrinth_modpack(
    payload: ModrinthModpackRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    core=Depends(get_core),
):
    """Download a Modrinth modpack's newest release and say what it holds,
    as for an uploaded pack. Nothing on a server changes yet."""
    token, pack = await modpack.from_modrinth(core, payload.project)
    audit(core, request, "modpack_inspect", target=pack.name)
    return _inspected(core, token, pack, payload.server_id)


@global_router.post("/modpacks/curseforge", response_model=ModpackInspected)
async def fetch_curseforge_modpack(
    payload: CurseForgeModpackRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    core=Depends(get_core),
):
    """Download a CurseForge modpack's newest release and say what it holds,
    as for an uploaded pack. Nothing on a server changes yet."""
    token, pack = await modpack.from_curseforge(core, payload.project)
    audit(core, request, "modpack_inspect", target=pack.name)
    return _inspected(core, token, pack, payload.server_id)


@global_router.delete("/modpacks/{token}", response_model=Ok)
async def forget_modpack(
    token: str, principal: Principal = Depends(require(MODS_MANAGE)), core=Depends(get_core)
):
    """Throw an uploaded pack away without importing it."""
    modpack.forget(core, token)
    return {"ok": True}


@global_router.post("/modpacks/{token}/new-server", response_model=ServerCreated)
async def modpack_new_server(
    token: str,
    payload: ModpackNewRequest,
    request: Request,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    core=Depends(get_core),
):
    """Make a new server from an uploaded pack. Minecraft is not started."""
    result = await modpack.import_new(
        core,
        token,
        name=payload.name,
        directory=payload.directory,
        memory_mb=payload.memory_mb,
        color=payload.color,
        eula_accepted=payload.eula_accepted,
        user=principal.user,
        own_jars=_own_jars_allowed(principal, None, payload.own_jars),
    )
    audit(core, request, "modpack_new_server", target=result["server_id"])
    return {"ok": True, **result}


@router.post("/modpack", response_model=ModpackImported)
async def modpack_into_server(
    payload: ModpackIntoRequest,
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    """Import an uploaded pack into this server: stopped, backed up, its old
    add-ons moved aside, then the pack's files put in place and checked."""
    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail="This needs to be confirmed, because the server is stopped for it.",
        )
    result = await modpack.import_into(
        ctx,
        payload.token,
        user=principal.user,
        own_jars=_own_jars_allowed(principal, ctx.server_id, payload.own_jars),
    )
    audit(ctx, request, "modpack_import", detail=result["modpack"]["name"])
    return {"ok": True, **result}


@router.post("/modpack/update/preview", response_model=ModpackUpdatePreview)
async def preview_modpack_update(
    payload: ModpackPreviewRequest,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """Download the linked modpack's newest version (or ``version_id``) and
    say what Modpack update would change. Nothing changes yet."""
    return await linkedpack.preview(ctx, payload.version_id)


@router.post("/modpack/update", response_model=ModpackUpdated)
async def update_modpack(
    payload: ModpackUpdateRequest,
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    """Modpack update: stopped, one backup, all or nothing, one Undo. Mods
    added by you are kept, and config files you changed unless
    ``use_pack_configs``."""
    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail="This needs to be confirmed, because the server is stopped for it.",
        )
    result = await linkedpack.update(
        ctx,
        payload.token,
        use_pack_configs=payload.use_pack_configs,
        user=principal.user,
        own_jars=_own_jars_allowed(principal, ctx.server_id, payload.own_jars),
    )
    audit(ctx, request, "modpack_update", detail=result["changes"]["to_version"])
    return {"ok": True, **result}


@router.delete("/modpack/link", response_model=Ok)
async def unlink_modpack(
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """Unlink: the server keeps its mods and stops following the modpack."""
    if not linkedpack.unlink(ctx):
        raise HTTPException(status_code=404, detail="This server isn't linked to a modpack.")
    audit(ctx, request, "modpack_unlink")
    return {"ok": True}
