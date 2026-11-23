"""Importing a modpack (a Modrinth .mrpack, or a CurseForge pack from a
file or picked on CurseForge): as a new server (agent-wide) or into an
existing one (per server)."""

from __future__ import annotations

import asyncio
from functools import partial

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ... import downloadlink, linkedpack, modpack, modpackmake, modrinthshare
from ...mods.curseforge import MODPACKS, CurseForgeClient
from ...mods.modrinth import ModrinthClient
from ...security.auth import AuthError, Principal
from ...security.permissions import (
    MODS_MANAGE,
    PC_SETTINGS,
    SERVERS_MANAGE,
    SETTINGS_GAME,
    SETTINGS_SERVER,
    may_add_own_jar,
    require,
)
from ..deps import audit, get_core, get_server
from ..responses import (
    DownloadLinkState,
    ModpackImported,
    ModpackInspected,
    ModpackMade,
    ModpackMakeOptions,
    ModpackPosted,
    ModpackUpdated,
    ModpackUpdatePreview,
    ModrinthAccount,
    ModrinthShare,
    ModrinthSignInStarted,
    ModSearch,
    Ok,
    ServerCreated,
)
from .models import (
    CurseForgeModpackRequest,
    DownloadLinkRequest,
    ModpackIntoRequest,
    ModpackMakeRequest,
    ModpackNewRequest,
    ModpackPostRequest,
    ModpackPreviewRequest,
    ModpackUpdateRequest,
    ModrinthFinishRequest,
    ModrinthJarRequest,
    ModrinthModpackRequest,
    ModrinthServerProjectRequest,
    ModrinthSignInRequest,
    ModrinthUploadRequest,
    ResourcePackRequest,
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


# ---------------------------------------------------------------- making one
@router.get("/modpack/make", response_model=ModpackMakeOptions)
async def make_modpack_options(
    principal: Principal = Depends(require(MODS_MANAGE)), ctx=Depends(get_server)
):
    """What Make a modpack offers: the name and next version, the config
    files that can be ticked, the players' loaders on a plugin server."""
    return await asyncio.to_thread(modpackmake.options, ctx)


@router.post("/modpack/make", response_model=ModpackMade)
async def make_modpack(
    payload: ModpackMakeRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """Make a modpack from this server, as a job. Nothing on the server
    changes. Save it with GET /modpack/made/{token}."""
    result = await modpackmake.make(
        ctx,
        payload.name,
        payload.version,
        leave_out_server_only=payload.leave_out_server_only,
        configs=payload.configs,
        players_loader=payload.players_loader,
        user=principal.user,
    )
    request.state.audited = True  # make writes its own audit row
    return {"ok": True, **result}


@router.get("/modpack/made/{token}")
async def download_modpack(
    token: str,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """The newest modpack's .mrpack file (a zip, so no JSON answer)."""
    try:
        path = modpackmake.file_path(ctx, token)
    except modpack.ModpackError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    made = modpackmake.made(ctx) or {}
    return FileResponse(
        path,
        # application/zip: the phones only save an answer of that type.
        media_type="application/zip",
        filename=made.get("filename") or "modpack.mrpack",
    )


@router.post("/modpack/made/discord", response_model=ModpackPosted)
async def post_modpack_to_discord(
    payload: ModpackPostRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """The Discord bot posts the newest modpack in this server's chat
    channels, when it fits Discord's 10 MB limit."""
    result = await modpackmake.post_to_discord(ctx, payload.token, user=principal.user)
    request.state.audited = True
    return {"ok": True, **result}


# ---------------------------------------------------------------- the download link
# Turning it on opens a port on this PC, so it is the owner's alone: PC
# settings, which no moderator holds (agent/security/permissions.py).
@router.get("/download-link", response_model=DownloadLinkState)
async def download_link_state(
    principal: Principal = Depends(require(MODS_MANAGE)), ctx=Depends(get_server)
):
    """The Download link card: on or off, the modpack's link, the resource pack."""
    return downloadlink.state(ctx)


@router.post("/download-link", response_model=DownloadLinkState)
async def download_link_switch(
    payload: DownloadLinkRequest,
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    ctx=Depends(get_server),
):
    """Turn the link on (a new hard-to-guess link) or off (it stops working)."""
    request.state.audited = True
    return await downloadlink.set_on(ctx, payload.on, user=principal.user)


@router.post("/download-link/new", response_model=DownloadLinkState)
async def download_link_new(
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    ctx=Depends(get_server),
):
    """A new link: the old one stops working at once."""
    request.state.audited = True
    return await downloadlink.new_link(ctx, user=principal.user)


@router.post("/resource-pack/upload", response_model=DownloadLinkState)
async def resource_pack_upload(
    request: Request,
    file: UploadFile = File(...),
    required: bool = False,
    principal: Principal = Depends(require(SETTINGS_GAME)),
    ctx=Depends(get_server),
):
    """Use this zip as the server resource pack, served by the download link."""
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Choose a resource pack (.zip).")
    folder = downloadlink.pack_file(ctx).parent
    folder.mkdir(parents=True, exist_ok=True)
    upload = folder / "upload.tmp"
    size = 0
    try:
        with upload.open("wb") as out:
            while chunk := await file.read(CHUNK):
                size += len(chunk)
                if size > downloadlink.MAX_PACK_BYTES:
                    raise HTTPException(
                        status_code=400,
                        detail="That resource pack is bigger than 250 MB, the most Minecraft takes.",
                    )
                await asyncio.to_thread(out.write, chunk)
        request.state.audited = True
        return await downloadlink.set_pack_file(
            ctx, upload, file.filename or "", required, user=principal.user
        )
    finally:
        upload.unlink(missing_ok=True)


@router.post("/resource-pack", response_model=DownloadLinkState)
async def resource_pack_set(
    payload: ResourcePackRequest,
    request: Request,
    principal: Principal = Depends(require(SETTINGS_GAME)),
    ctx=Depends(get_server),
):
    """An outside link for the resource pack, or only change "Players must
    accept the resource pack" (no ``url``)."""
    request.state.audited = True
    if payload.url is None:
        return await downloadlink.set_required(ctx, payload.required, user=principal.user)
    return await downloadlink.set_pack_link(
        ctx, payload.url, payload.sha1, payload.required, user=principal.user
    )


@router.delete("/resource-pack", response_model=DownloadLinkState)
async def resource_pack_remove(
    request: Request,
    principal: Principal = Depends(require(SETTINGS_GAME)),
    ctx=Depends(get_server),
):
    """No server resource pack any more."""
    request.state.audited = True
    return await downloadlink.remove_pack(ctx, user=principal.user)


# ---------------------------------------------------------------- Sign in with Modrinth
# One sign-in per PC. Signing in and out is the owner's (PC settings): what
# goes up goes up under their Modrinth account. Uploads are drafts, which
# only the owner can submit for review on Modrinth's site.
@global_router.post("/modrinth/sign-in", response_model=ModrinthSignInStarted)
async def modrinth_sign_in(
    payload: ModrinthSignInRequest,
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    core=Depends(get_core),
):
    """Modrinth's page to open. It sends the browser back to ``return_to``
    with a code, which goes to ``POST /modrinth/sign-in/finish``."""
    started = await core.modrinth.start(payload.return_to)
    audit(core, request, "modrinth_sign_in")
    return started


@global_router.post("/modrinth/sign-in/finish", response_model=ModrinthAccount)
async def modrinth_sign_in_finish(
    payload: ModrinthFinishRequest,
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    core=Depends(get_core),
):
    """Swap the code for the token and keep it on this PC."""
    request.state.audited = True
    try:
        status = await core.modrinth.finish(payload.code, payload.state)
    except modrinthshare.ModrinthShareError:
        audit(core, request, "modrinth_sign_in_finish", result="failed")
        raise
    audit(core, request, "modrinth_sign_in_finish")
    return status


@global_router.post("/modrinth/sign-out", response_model=ModrinthAccount)
async def modrinth_sign_out(
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    core=Depends(get_core),
):
    """Forget the Modrinth sign-in on this PC."""
    core.modrinth.sign_out()
    audit(core, request, "modrinth_sign_out")
    return core.modrinth.status()


@router.get("/modrinth", response_model=ModrinthShare)
async def modrinth_share_state(
    principal: Principal = Depends(require(MODS_MANAGE)), ctx=Depends(get_server)
):
    """The "Put it on Modrinth" card."""
    await modrinthshare.refresh(ctx)
    return modrinthshare.state(ctx)


@router.post("/modrinth/upload", response_model=ModrinthShare)
async def modrinth_upload(
    payload: ModrinthUploadRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """The newest made modpack as a new version (the project, a draft, is
    made the first time with ``slug`` and ``summary``)."""
    request.state.audited = True
    return await modrinthshare.upload_modpack(
        ctx, payload.slug, payload.summary, user=principal.user
    )


@router.post("/modrinth/own-jar", response_model=ModrinthShare)
async def modrinth_own_jar(
    payload: ModrinthJarRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """One own jar from the newest modpack as its own Modrinth mod (a draft).
    Needs "Add own .jar" too: it is a file no site checked."""
    _own_jars_allowed(principal, ctx.server_id, True)
    request.state.audited = True
    return await modrinthshare.upload_own_jar(
        ctx, payload.path, payload.slug, payload.summary, user=principal.user
    )


@router.post("/modrinth/server-project", response_model=ModrinthShare)
async def modrinth_server_project(
    payload: ModrinthServerProjectRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """Remember (or, empty, forget) this server's Modrinth Server Project."""
    request.state.audited = True
    return await modrinthshare.set_server_project(ctx, payload.link, user=principal.user)
