"""Server types, versions, version and type changes, and crossplay.

Agent-wide: the type comparison table and each type's version list, and
creating a new server. Per server: the preflight before a change, the
change itself, rolling it back, and the Bedrock crossplay switch.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import bringin as bringin_module
from ... import crossplay as crossplay_module
from ... import gamesettings, servertypes
from ...security.auth import Principal
from ...security.permissions import (
    SERVER_VIEW,
    SERVERS_MANAGE,
    SETTINGS_GAME,
    SETTINGS_SERVER,
    check,
    require,
)
from ...servertypes import bedrock as bedrock_module
from ...servertypes import change as change_module
from ...servertypes import chunker as chunker_module
from ...servertypes import create as create_module
from ...servertypes import custom as custom_module
from ...servertypes import install as install_module
from ...servertypes import versions as versions_module
from ..deps import audit, get_core, get_server
from ..responses import (
    BedrockTerms,
    BedrockVersions,
    BringInFound,
    Crossplay,
    CrossplaySaved,
    DuplicateStarted,
    EulaAccepted,
    GameSettingsSaved,
    NewServerOptions,
    ServerCreated,
    ServerTypes,
    ServerVersion,
    SimilarAddons,
    TypeAdvice,
    TypeChangeOptions,
    TypeVersions,
    VersionChanged,
    VersionPreflight,
    VersionRolledBack,
)
from .models import (
    BedrockTermsRequest,
    BringInReadRequest,
    BringInRequest,
    ConvertRequest,
    CreateServerRequest,
    CrossplayRequest,
    CustomServerRequest,
    ReplaceJarRequest,
    TypeChangeRequest,
    VersionChangeRequest,
)

# Agent-wide routes (the types, their versions, creating a server).
global_router = APIRouter()
# Per-server routes, served under /api/servers/{server_id}/.
router = APIRouter()


# ---------------------------------------------------------------- the types
@global_router.get("/server-types", response_model=ServerTypes)
async def list_types(principal: Principal = Depends(require(SERVER_VIEW))):
    """Every server type with its capabilities. The comparison
    table is generated from this, so it cannot drift from what the app does."""
    return {
        "types": servertypes.catalog(),
        "recommended": servertypes.RECOMMENDED,
        "default": servertypes.DEFAULT_TYPE,
    }


@global_router.get("/server-types/recommend", response_model=TypeAdvice)
async def recommend_types(
    goal: str,
    loaders: str = "",
    technical: bool = False,
    principal: Principal = Depends(require(SERVER_VIEW)),
):
    """Which types fit what someone wants (vanilla, plugins or mods), for
    the New server picker. ``loaders``: the loaders every picked mod
    supports, comma separated (Modrinth's names)."""
    picked = [name.strip() for name in loaders.split(",") if name.strip()][:10]
    try:
        return servertypes.recommend(goal, picked, technical)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@global_router.get("/server-types/{type_id}/versions", response_model=TypeVersions)
async def type_versions(type_id: str, principal: Principal = Depends(require(SERVER_VIEW))):
    """The Minecraft versions this type offers, from its official source."""
    return await versions_module.report(servertypes.get(type_id).id)


@global_router.get("/new-server/options", response_model=NewServerOptions)
async def new_server_options(
    principal: Principal = Depends(require(SERVERS_MANAGE)), core=Depends(get_core)
):
    """What the New server panel needs before anything is chosen."""
    return await create_module.options(core)


@global_router.post("/new-server", response_model=ServerCreated)
async def create_server(
    payload: CreateServerRequest,
    request: Request,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    core=Depends(get_core),
):
    """Create a brand-new server: folder, settings, download and set-up.
    Minecraft is not started; the person presses Start themselves."""
    result = await create_module.create_server(
        core,
        name=payload.name,
        directory=payload.directory,
        type_id=payload.type,
        minecraft=payload.minecraft_version,
        loader=payload.loader_version,
        memory_mb=payload.memory_mb,
        color=payload.color,
        eula_accepted=payload.eula_accepted,
        vanilla_spawns=payload.vanilla_spawns,
        user=principal.user,
    )
    return {"ok": True, **result}


@global_router.post("/new-server/custom-jar", response_model=ServerCreated)
async def create_custom_server(
    payload: CustomServerRequest,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    core=Depends(get_core),
):
    """A new server that runs the owner's own jar (owner only: no moderator
    can hold SERVERS_MANAGE). The app never checks or updates it."""
    result = await custom_module.create_custom(
        core,
        name=payload.name,
        directory=payload.directory,
        jar=payload.jar,
        based_on=payload.based_on,
        memory_mb=payload.memory_mb,
        color=payload.color,
        eula_accepted=payload.eula_accepted,
        trusted=payload.trusted,
        user=principal.user,
    )
    return {"ok": True, **result}


# ---------------------------------------------------------------- bringing a server in
@global_router.post("/bring-in/read", response_model=BringInFound)
async def read_server_to_bring_in(
    payload: BringInReadRequest,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    core=Depends(get_core),
):
    """What a server folder or zip on this PC holds. Nothing is copied or
    changed, and nothing in it is run."""
    found = await asyncio.to_thread(bringin_module.inspect, core, payload.path)
    return found.to_dict()


@global_router.post("/bring-in", response_model=DuplicateStarted)
async def bring_server_in(
    payload: BringInRequest,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    core=Depends(get_core),
):
    """Copy a server folder or zip into a new folder and add it, as a job.
    The original is never changed."""
    job = await bringin_module.bring_in(
        core,
        path=payload.path,
        name=payload.name,
        directory=payload.directory,
        based_on=payload.based_on,
        jar=payload.jar,
        trusted=payload.trusted,
        user=principal.user,
    )
    return {"ok": True, "job": job.to_dict()}


# ---------------------------------------------------------------- Bedrock
def _terms(core) -> dict:
    record = bedrock_module.terms(core.db) or {}
    return {
        "accepted": bool(record),
        "user": record.get("user"),
        "at": record.get("at"),
        "eula_url": bedrock_module.EULA_URL,
        "privacy_url": bedrock_module.PRIVACY_URL,
    }


@global_router.get("/bedrock/terms", response_model=BedrockTerms)
async def bedrock_terms(
    principal: Principal = Depends(require(SERVER_VIEW)), core=Depends(get_core)
):
    """Whether Mojang's EULA and Privacy Policy were accepted. The Bedrock
    server is never downloaded before they are."""
    return _terms(core)


@global_router.post("/bedrock/terms", response_model=BedrockTerms)
async def accept_bedrock_terms(
    payload: BedrockTermsRequest,
    request: Request,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    core=Depends(get_core),
):
    """Record that the person ticked the box accepting Mojang's EULA and
    Privacy Policy. Only ever sent from their own tick."""
    if not payload.accepted:
        raise HTTPException(status_code=400, detail="Tick the box to accept them.")
    bedrock_module.accept_terms(core.db, principal.user)
    return _terms(core)


@global_router.get("/bedrock/versions", response_model=BedrockVersions)
async def bedrock_kept_versions(principal: Principal = Depends(require(SERVER_VIEW))):
    """The Bedrock versions this app downloaded and kept."""
    kept = await asyncio.to_thread(bedrock_module.kept_versions)
    return {"kept": kept, "folder": str(bedrock_module.kept_root())}


# ---------------------------------------------------- one server's version
@router.get("/version", response_model=ServerVersion)
async def current_version(
    principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    """This server's type and version, as observed, plus what a change kept."""
    status = ctx.server.status()
    server_type = ctx.config.server_type
    record = ctx.db.get_setting("version_change") or {}
    return {
        "type": server_type.id,
        "capabilities": server_type.to_dict(),
        # The owner's own jar: never checked, updated or changed by the app.
        "custom_jar": ctx.config.server.custom_jar,
        # Only what the console reported. null means Unknown.
        "minecraft_version": status["minecraft_version"],
        "minecraft_version_source": status["minecraft_version_source"],
        "loader_version": status["loader_version"],
        "loader_name": status["loader_name"],
        "detected_type": status["server_type_detected"],
        "pending_version": status["pending_version"],
        "eula_required": status["eula_required"],
        "eula_url": create_module.EULA_URL,
        "last_change": {
            "at": record.get("at"),
            "type": record.get("type"),
            "previous_type": record.get("previous_type"),
            "minecraft_version": record.get("minecraft_version"),
            "can_roll_back": bool(record.get("previous")),
            "install_log": record.get("install_log"),
            "moved_aside": (record.get("content_moved_aside") or {}).get("moved", []),
        }
        if record
        else None,
    }


@router.post("/version/preflight", response_model=VersionPreflight)
async def version_preflight(
    payload: VersionChangeRequest,
    principal: Principal = Depends(require(SERVER_VIEW)),
    ctx=Depends(get_server),
):
    """What a change would do, in plain words, before anything happens."""
    return await install_module.preflight(
        ctx,
        payload.type or ctx.config.server.type,
        payload.minecraft_version,
        payload.loader_version,
    )


@router.post("/version", response_model=VersionChanged)
async def change_version(
    payload: VersionChangeRequest,
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    """Change this server's version, or its type. Stops the server, takes a
    verified backup, installs, checks, and keeps the old files to go back to."""
    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail="This change needs to be confirmed, because the server is stopped for it.",
        )
    if payload.type and payload.type != ctx.config.server.type:
        # A type change is the owner's, with its own rules (Change server type).
        check(principal, SERVERS_MANAGE, ctx.server_id)
        result = await change_module.change_type(
            ctx,
            payload.type,
            technical=True,
            stop_first=True,  # confirmed above; the change stops it
            minecraft=payload.minecraft_version,
            user=principal.user,
        )
        audit(ctx, request, "type_change", detail=payload.type)
        return {"ok": True, **result}
    result = await install_module.change_version(
        ctx,
        payload.type or ctx.config.server.type,
        payload.minecraft_version,
        payload.loader_version,
        user=principal.user,
        start_after=payload.start_after,
    )
    audit(ctx, request, "version_change", detail=payload.minecraft_version)
    return {"ok": True, **result}


@router.post("/version/roll-back", response_model=VersionRolledBack)
async def roll_back_version(
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    """Put the version that was there before the last change back."""
    result = await install_module.roll_back(ctx, user=principal.user)
    audit(ctx, request, "version_rollback")
    return {"ok": True, **result}


# ---------------------------------------------- Change server type (owner only)
@router.get("/type-change", response_model=TypeChangeOptions)
async def type_change_options(
    technical: bool = False,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    ctx=Depends(get_server),
):
    """The types this server can change to, and the version the change keeps."""
    return {
        **change_module.options(ctx, technical),
        "copy_to_other_edition": chunker_module.suggestion(ctx.core, ctx) if technical else None,
    }


@router.post("/type-change/preflight", response_model=VersionPreflight)
async def type_change_preflight(
    payload: TypeChangeRequest,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    ctx=Depends(get_server),
):
    """What comes along and what doesn't, before anything changes."""
    return await change_module.plan(ctx, payload.type, payload.technical)


@router.post("/type-change", response_model=VersionChanged)
async def change_type(
    payload: TypeChangeRequest,
    request: Request,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    ctx=Depends(get_server),
):
    """Change server type: a backup first, the version kept, Roll back after."""
    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail="This change needs to be confirmed, because the server is stopped for it.",
        )
    result = await change_module.change_type(
        ctx,
        payload.type,
        technical=payload.technical,
        stop_first=payload.stop_first,
        user=principal.user,
    )
    audit(ctx, request, "type_change", detail=payload.type)
    return {"ok": True, **result}


@router.get("/type-change/similar", response_model=SimilarAddons)
async def similar_addons(
    principal: Principal = Depends(require(SERVERS_MANAGE)), ctx=Depends(get_server)
):
    """After a type change: the same add-ons for the new type, then the notes
    file's look-alikes. Only offered; nothing is added here."""
    return await change_module.similar(ctx)


@router.post("/type-change/copy", response_model=ServerCreated)
async def copy_to_other_edition(
    payload: ConvertRequest,
    request: Request,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    ctx=Depends(get_server),
):
    """A new server of the other edition from a copy of this world (Chunker).
    The original isn't changed."""
    result = await chunker_module.convert(
        ctx.core,
        ctx,
        type_id=payload.type,
        minecraft=payload.minecraft_version,
        name=payload.name,
        directory=payload.directory,
        eula_accepted=payload.eula_accepted,
        understood=payload.understood,
        user=principal.user,
    )
    audit(ctx, request, "world_copy_other_edition", detail=payload.type)
    return {"ok": True, **result}


@router.post("/eula", response_model=EulaAccepted)
async def accept_eula(
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    """Record that the person accepted Minecraft's rules for this server.
    Only ever called from their own tick: nothing accepts them for them."""
    if not ctx.config.server_dir_configured:
        raise HTTPException(status_code=400, detail="This server's folder isn't set.")
    path = create_module.write_eula(ctx.config.server_dir, True)
    ctx.server.eula_required = False
    audit(ctx, request, "eula_accepted")
    return {"ok": True, "file": str(path), "url": create_module.EULA_URL}


@router.post("/custom-jar", response_model=VersionChanged)
async def replace_custom_jar(
    payload: ReplaceJarRequest,
    request: Request,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    ctx=Depends(get_server),
):
    """Put a new version of the owner's own jar in place, with a backup and
    one Undo. Owner only."""
    result = await custom_module.replace_jar(ctx, payload.jar, user=principal.user)
    audit(ctx, request, "custom_jar_replace")
    return {"ok": True, **result}


@router.post("/game-settings/vanilla-spawns", response_model=GameSettingsSaved)
async def use_vanilla_spawns(
    request: Request,
    principal: Principal = Depends(require(SETTINGS_GAME)),
    ctx=Depends(get_server),
):
    """Paper and its forks: the fixed spawn settings that keep vanilla farms
    working, as a safe change with one Undo."""
    result = await gamesettings.use_vanilla_spawns(ctx, user=principal.user)
    audit(ctx, request, "vanilla_spawns")
    return {"ok": True, **result}


# ---------------------------------------------------------------- crossplay
@router.get("/crossplay", response_model=Crossplay)
async def crossplay_status(
    principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    """Whether Bedrock players can join, the port they use, and what differs."""
    status = crossplay_module.status(ctx)
    port = ctx.core.ports.bedrock_port_of(ctx)
    return {
        **status,
        "address_port": port.port if port else None,
        "suggested_port": None if status["enabled"] else ctx.core.ports.suggest_bedrock(),
        # The address Bedrock players type. Only from a measured Tailscale
        # address: null with a reason when this PC's address isn't known.
        # Asking Tailscale runs its command, so not on the event loop.
        **(await asyncio.to_thread(crossplay_module.address, status)),
    }


@router.put("/crossplay", response_model=CrossplaySaved)
async def set_crossplay(
    payload: CrossplayRequest,
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    """Turn "Let Bedrock players join" on or off."""
    if payload.enabled:
        result = await crossplay_module.enable(ctx, payload.port, user=principal.user)
    else:
        result = await crossplay_module.disable(ctx, user=principal.user)
    audit(ctx, request, "crossplay", detail="on" if payload.enabled else "off")
    return {"ok": True, **result}
