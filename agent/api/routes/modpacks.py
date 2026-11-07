"""Importing a modpack (a Modrinth .mrpack, or a CurseForge pack from a
file or picked on CurseForge): as a new server (agent-wide) or into an
existing one (per server)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from ... import modpack
from ...mods.curseforge import MODPACKS, CurseForgeClient
from ...security.auth import Principal
from ...security.permissions import MODS_MANAGE, SERVERS_MANAGE, SETTINGS_SERVER, require
from ..deps import audit, get_core, get_server
from ..responses import ModpackImported, ModpackInspected, ModSearch, Ok, ServerCreated
from .models import CurseForgeModpackRequest, ModpackIntoRequest, ModpackNewRequest

# Agent-wide: reading a pack, and making a new server from it.
global_router = APIRouter()
# Per server: importing into this server.
router = APIRouter()

CHUNK = 1024 * 1024


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


@global_router.get("/modpacks/curseforge/search", response_model=ModSearch)
async def search_curseforge_modpacks(
    q: str = "",
    limit: int = 20,
    offset: int = 0,
    principal: Principal = Depends(require(MODS_MANAGE)),
    core=Depends(get_core),
):
    """CurseForge's modpacks, most popular first, through our relay."""
    client = CurseForgeClient(core.config)
    return await client.search(q, limit=limit, offset=offset, kind=MODPACKS)


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
    result = await modpack.import_into(ctx, payload.token, user=principal.user)
    audit(ctx, request, "modpack_import", detail=result["modpack"]["name"])
    return {"ok": True, **result}
