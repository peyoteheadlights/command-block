"""Agent-wide routes: the server list (adding and removing servers), the
port suggestion, and the Windows startup test."""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import colors
from ...config import PROJECT_ROOT, ConfigError
from ...minecraft.state import ExitReason
from ...ports import read_properties_port
from ...security.auth import Principal
from ...security.paths import PathSafetyError, check_server_folder
from ...security.permissions import (
    SERVER_CONTROL,
    SERVER_VIEW,
    SERVERS_MANAGE,
    SYSTEM_VIEW,
    can_see_server,
    on_server,
    require,
)
from ..deps import audit, get_core
from ..errors import DOMAIN_ERRORS
from ..responses import (
    AllServersAction,
    Ok,
    PortSuggestion,
    PowerStatus,
    ServerAdded,
    ServerList,
    ServerRemoved,
    StartupReport,
)
from .models import ServerAddRequest, ServerIdsRequest, ServerOrderRequest

router = APIRouter()

# Tried in this order when the add-server form leaves the jar empty.
DEFAULT_JARS = ("fabric-server-launch.jar", "server.jar")


@router.get("/servers", response_model=ServerList)
async def servers(principal: Principal = Depends(require(SERVER_VIEW)), core=Depends(get_core)):
    """Every registered server with its measured state, players and uptime."""
    rows = []
    for row in _ordered(core):
        if not can_see_server(principal, row["id"]):
            continue  # a helper limited to other servers
        row["current"] = row["default"]
        rows.append(row)
    return {
        "servers": rows,
        "default": core.config.default_server_id,
        "palette": colors.palette(),
        "next_color": colors.next_unused(ctx.color for ctx in core.servers.values()),
    }


def _ordered(core) -> list[dict]:
    """Every server's summary in the owner's order; servers never moved
    keep the order they were added in, after the moved ones."""
    rows = [ctx.summary() for ctx in core.servers.values()]
    return sorted(rows, key=lambda r: (r["position"] is None, r["position"] or 0))


@router.put("/servers/order", response_model=Ok)
async def set_order(
    payload: ServerOrderRequest,
    request: Request,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    core=Depends(get_core),
):
    """The order of All servers, dragged by the owner. The same on every app."""
    ids = list(dict.fromkeys(payload.ids))
    unknown = [i for i in ids if i not in core.servers]
    if unknown:
        raise HTTPException(
            status_code=404, detail=f"No server called {unknown[0]!r} is on this PC."
        )
    # Servers the app didn't send (added meanwhile) go after, in their old order.
    rest = [r["id"] for r in _ordered(core) if r["id"] not in ids]
    core.db.set_server_order(ids + rest)
    audit(core, request, "server_order", detail=", ".join(ids)[:300])
    return {"ok": True}


def _controlled(principal: Principal, core, ids: list[str] | None) -> list:
    """The servers this account may start and stop, of the ones asked for
    (all of them when none are named), in the list's order."""
    chosen = [core.servers[r["id"]] for r in _ordered(core) if ids is None or r["id"] in ids]
    return [ctx for ctx in chosen if SERVER_CONTROL in on_server(principal, ctx.server_id)]


@router.post("/servers/start-all", response_model=AllServersAction)
async def start_all(
    payload: ServerIdsRequest,
    request: Request,
    principal: Principal = Depends(require(SERVER_VIEW)),
    core=Depends(get_core),
):
    """Start every stopped server the account may start, one after another
    (each is only launched here; "Online" comes on the live feed later)."""
    results = []
    for ctx in _controlled(principal, core, payload.ids):
        row = {"id": ctx.server_id, "name": ctx.name}
        if ctx.server.running:
            results.append({**row, "result": "already"})
            continue
        try:
            await ctx.server.start(actor=principal.user)
        except DOMAIN_ERRORS as exc:
            audit(ctx, request, "server_start", result="failed", detail=str(exc))
            results.append({**row, "result": "failed", "detail": str(exc)})
            continue
        audit(ctx, request, "server_start", detail="start all")
        results.append({**row, "result": "started"})
    return {"servers": results}


@router.post("/servers/stop-all", response_model=AllServersAction)
async def stop_all(
    payload: ServerIdsRequest,
    request: Request,
    principal: Principal = Depends(require(SERVER_VIEW)),
    core=Depends(get_core),
):
    """Stop every running server the account may stop, all at once, and
    wait for each to save and exit."""
    running = [ctx for ctx in _controlled(principal, core, payload.ids) if ctx.server.running]

    async def stop(ctx) -> dict:
        row = {"id": ctx.server_id, "name": ctx.name}
        try:
            await ctx.server.stop(actor=principal.user, reason=ExitReason.USER_STOP)
        except DOMAIN_ERRORS as exc:
            audit(ctx, request, "server_stop", result="failed", detail=str(exc))
            return {**row, "result": "failed", "detail": str(exc)}
        audit(ctx, request, "server_stop", detail="stop all")
        stopped = ctx.server.state.value in ("OFFLINE", "CRASHED")
        return {**row, "result": "stopped" if stopped else "stopping"}

    return {"servers": list(await asyncio.gather(*(stop(ctx) for ctx in running)))}


def _new_id(name: str, taken: list[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "server"
    if not base[0].isalnum():
        base = f"server-{base}"
    candidate, number = base, 2
    lowered = {t.lower() for t in taken}
    while candidate in lowered or candidate in ("con", "prn", "aux", "nul"):
        candidate = f"{base}-{number}"
        number += 1
    return candidate


@router.post("/servers", response_model=ServerAdded)
async def add_server(
    payload: ServerAddRequest,
    request: Request,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    core=Depends(get_core),
):
    """Register an existing Minecraft server folder. Nothing in the folder is
    changed and Minecraft is not started."""
    color = None
    if payload.color:
        try:
            color = colors.normalise(payload.color)
        except colors.ColorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    from pathlib import Path

    from ... import servertypes
    from ...servertypes.install import find_args_file

    server_type = servertypes.get(payload.type)  # empty: Fabric, as before
    folder = Path(payload.directory.strip()).expanduser()
    args_file = ""
    jar = payload.jar.strip()
    if server_type.launch == "args_file":
        # Forge and NeoForge start from the argument file their installer
        # wrote, not from a jar.
        jar = ""
    elif not jar:
        names = (server_type.jar, *DEFAULT_JARS) if server_type.jar else DEFAULT_JARS
        jar = next((j for j in names if (folder / j).is_file()), names[0])
    registered = [(ctx.name, ctx.config.server_dir) for ctx in core.servers.values()]
    try:
        directory = check_server_folder(
            payload.directory,
            jar,
            protected=[PROJECT_ROOT, core.config.data_dir],
            registered=registered,
        )
        if server_type.launch == "args_file":
            args_file = find_args_file(directory, server_type.id) or ""
            if not args_file:
                raise PathSafetyError(
                    f"{directory} has no {server_type.name} start file (libraries/.../"
                    "win_args.txt), so it doesn't look like a set-up "
                    f"{server_type.name} server. Run {server_type.name}'s installer there first."
                )
    except PathSafetyError as exc:
        audit(core, request, "server_add", target=payload.name, result="refused", detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    server_id = payload.id.strip() if payload.id else _new_id(payload.name, core.config.server_ids)
    warnings = []
    port = read_properties_port(directory)
    if port is None:
        port = core.ports.suggest() or 25565
        warnings.append(
            "server.properties doesn't set a port yet, so Minecraft will use 25565 when it "
            f"first starts. Port {port} is free if you want to set server-port to it."
        )
    else:
        owner = core.ports.used().get(("tcp", port))
        if owner:
            warnings.append(
                f"server.properties uses port {port}, which the server "
                f"'{core.servers[owner].name}' also uses. Only one of them can run at a time "
                "until you change server-port in one of them."
            )
    entry = {
        "id": server_id,
        "name": payload.name.strip(),
        "directory": str(directory),
        "type": server_type.id,
        "jar": jar,
        "args_file": args_file,
        # The Java program is the agent's own setting, never the apps'.
        "java": core.config.server.java,
        "port": port,
    }
    try:
        ctx = await core.add_server(entry, user=principal.user)
    except ConfigError as exc:
        audit(core, request, "server_add", target=payload.name, result="failed", detail=str(exc))
        raise
    if color:
        core.db.set_server_color(ctx.server_id, color)
    return {"ok": True, "server": ctx.summary(), "warnings": warnings}


@router.delete("/servers/{server_id}", response_model=ServerRemoved)
async def remove_server(
    server_id: str,
    request: Request,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    core=Depends(get_core),
):
    """Take a server off the list. Its folder, world and backups stay."""
    result = await core.remove_server(server_id, user=principal.user)
    return {"ok": True, **result}


@router.get("/ports/suggest", response_model=PortSuggestion)
async def suggest_port(
    protocol: str = "tcp",
    principal: Principal = Depends(require(SERVER_VIEW)),
    core=Depends(get_core),
):
    if protocol not in ("tcp", "udp"):
        raise HTTPException(status_code=400, detail="Pick tcp or udp.")
    port = core.ports.suggest(protocol)
    used = [
        {"protocol": proto, "port": number, "server_id": owner}
        for (proto, number), owner in sorted(core.ports.used().items())
    ]
    return {"port": port, "protocol": protocol, "used": used}


@router.get("/system/startup", response_model=StartupReport)
async def windows_startup_test(principal: Principal = Depends(require(SYSTEM_VIEW))):
    """Test Windows Startup.

    Reads the registered scheduled task back from Windows, compares it with
    this installation, checks for the old service and duplicate entries, and
    includes the agent's own record of its last startup. Read-only.
    """
    import asyncio

    try:
        from installer.autostart import report
    except ImportError as exc:
        raise HTTPException(
            status_code=500, detail=f"The startup inspector could not be loaded: {exc}"
        ) from exc
    return await asyncio.to_thread(report)


@router.get("/power", response_model=PowerStatus)
async def power(principal: Principal = Depends(require(SYSTEM_VIEW)), core=Depends(get_core)):
    """Whether the PC is being kept awake, and what can still make it sleep."""
    import asyncio

    return await asyncio.to_thread(core.keepawake.status)
