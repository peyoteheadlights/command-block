"""Server status and control: start, stop, restart, console commands,
performance, health, worlds and TPS monitoring."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ... import joinguard
from ...minecraft.commands import CommandError, validate
from ...minecraft.java import memory_limit_mb
from ...minecraft.process import ServerError
from ...minecraft.state import ExitReason
from ...security.auth import Principal
from ...security.permissions import (
    CONSOLE_SEND,
    SERVER_CONTROL,
    SERVER_DIAGNOSE,
    SERVER_VIEW,
    SETTINGS_SERVER,
    require,
)
from ...startrequests import StartRequestError
from ..deps import audit, get_server
from ..errors import audit_failure
from ..responses import (
    CommandCheck,
    CommandSent,
    CrashBlockCleared,
    DiagnoseStarted,
    Diagnosis,
    Performance,
    Requested,
    ServerAction,
    ServerHealth,
    ServerStatus,
    StartRequestAnswered,
    StartRequestOpen,
    TpsStatus,
    WorldList,
)
from .models import CommandRequest, StartRequestAnswer, StopRequest, TpsCommandRequest

router = APIRouter()


@router.get("/status", response_model=ServerStatus)
async def status(principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)):
    return ctx.status()


@router.get("/info")
async def server_info(
    principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    import platform

    import psutil

    status = ctx.server.status()
    storage = await ctx.metrics.storage()
    return {
        "server": status,
        "java": ctx.server.java_info.to_dict() if ctx.server.java_info else None,
        "java_compatibility": ctx.server.java_compatibility(),
        "preflight": ctx.server.preflight().to_dict(),
        "command": ctx.server.build_command(),
        "storage": storage,
        "machine": {
            "os": f"{platform.system()} {platform.release()}",
            "hostname": platform.node(),
            "cpu": platform.processor() or "unknown",
            "cpu_cores": psutil.cpu_count(logical=True),
            "ram_total_gb": psutil.virtual_memory().total / 1024**3,
            "python": platform.python_version(),
        },
        "paths": {
            "server_directory": str(ctx.config.server_dir),
            "data_directory": str(ctx.config.data_dir),
            "mods_directory": str(ctx.config.mods_dir),
            "backup_directory": str(ctx.config.backup_dir),
            "log_directory": str(ctx.config.log_dir),
        },
    }


@router.post("/server/start", response_model=ServerAction)
async def server_start(
    request: Request,
    principal: Principal = Depends(require(SERVER_CONTROL)),
    ctx=Depends(get_server),
):
    with audit_failure(ctx, request, "server_start"):
        result = await ctx.server.start(actor=principal.user)
    audit(ctx, request, "server_start", detail=f"pid={result['pid']}")
    # The process was launched. That is not the same as the server being up:
    # "started successfully" is only reported once the startup line appears in
    # the console, which arrives as a server_started event.
    return {
        "result": "REQUESTED",
        "detail": "The Minecraft process was launched. Watch for the server_started event, "
        "or poll /api/status, to find out whether startup completes.",
        "pid": result["pid"],
        "command": result["command"],
        "state": ctx.server.state.value,
        "startup_confirmed": ctx.server.startup_confirmed,
    }


@router.get("/start-request", response_model=StartRequestOpen)
async def start_request(
    principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    """A start request someone sent from Discord, while it's open."""
    found = ctx.core.start_requests.get(ctx.server_id)
    return {"request": found.to_dict() if found else None}


@router.post("/start-request", response_model=StartRequestAnswered)
async def answer_start_request(
    payload: StartRequestAnswer,
    request: Request,
    principal: Principal = Depends(require(SERVER_CONTROL)),
    ctx=Depends(get_server),
):
    """Start (the same start as the Start button) or Say no."""
    try:
        answer = await ctx.core.start_requests.answer(
            ctx, payload.answer == "start", principal.user
        )
    except StartRequestError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ServerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    audit(ctx, request, "start_request", detail=answer)
    return {"answer": answer}


@router.post("/server/stop", response_model=ServerAction)
async def server_stop(
    payload: StopRequest,
    request: Request,
    principal: Principal = Depends(require(SERVER_CONTROL)),
    ctx=Depends(get_server),
):
    with audit_failure(ctx, request, "server_stop"):
        if payload.force:
            await ctx.server.kill(actor=principal.user)
            result = {"graceful": False, "forced": True}
        else:
            result = await ctx.server.stop(
                actor=principal.user, reason=ExitReason.USER_STOP, timeout=payload.timeout
            )
    audit(ctx, request, "server_stop", detail=str(result))
    stopped = ctx.server.state.value in ("OFFLINE", "CRASHED")
    return {
        "result": "VERIFIED" if stopped else "IN_PROGRESS",
        "detail": (
            "The process has exited and the agent observed it."
            if stopped
            else "The stop was requested but the process has not exited yet."
        ),
        **result,
        "state": ctx.server.state.value,
    }


@router.post("/server/restart", response_model=ServerAction)
async def server_restart(
    request: Request,
    principal: Principal = Depends(require(SERVER_CONTROL)),
    ctx=Depends(get_server),
):
    with audit_failure(ctx, request, "server_restart"):
        result = await ctx.server.restart(actor=principal.user)
    audit(ctx, request, "server_restart")
    online = ctx.server.startup_confirmed and ctx.server.state.value == "ONLINE"
    return {
        "result": "VERIFIED" if online else "IN_PROGRESS",
        "detail": (
            "The server stopped and completed startup again."
            if online
            else "The restart was requested. Startup has not been confirmed yet; watch for "
            "the server_started event."
        ),
        **result,
        "state": ctx.server.state.value,
        "startup_confirmed": ctx.server.startup_confirmed,
    }


class DiagnoseForm(BaseModel):
    # The owner said yes to "a join kicks you out for about a minute".
    go_ahead: bool = False
    # The join with all mods: yes or no. Not given on a mod server: ask_mods.
    mods: bool | None = None


@router.post("/server/diagnose", response_model=DiagnoseStarted)
async def server_diagnose(
    request: Request,
    form: DiagnoseForm | None = None,
    principal: Principal = Depends(require(SERVER_DIAGNOSE)),
    ctx=Depends(get_server),
):
    """Diagnose server: a test player joins and every step is listed. A
    stopped server is started. Follow it with GET /server/diagnose. When the
    owner is playing and a join uses their account, nothing starts and
    ``ask`` says so; POST again with go_ahead. On a mod server ``ask_mods``
    asks about the join with all mods first; POST again with mods."""
    from ... import diagnose, fullmods

    if (ctx.diagnosis or {}).get("running"):
        raise HTTPException(status_code=409, detail="Diagnose server is already running.")
    playing = diagnose.owner_playing(ctx)
    if playing and not (form and form.go_ahead):
        return {
            "job_id": None,
            "ask": f"You're playing on {ctx.name} as {playing}. The test joins with your "
            "account, so it kicks you out for about a minute. Go ahead?",
        }
    if (form is None or form.mods is None) and fullmods.wanted(ctx):
        return {"job_id": None, "ask_mods": fullmods.ASK}
    # running from now: a second click is refused
    job = diagnose.start(ctx, principal.user, mods=bool(form and form.mods))
    audit(ctx, request, "server_diagnose")
    return {"job_id": job.id}


class DiagnoseChangesForm(BaseModel):
    # "all", "safe", or "none" (Deny all).
    pick: Literal["all", "safe", "none"]


@router.post("/server/diagnose/changes", response_model=DiagnoseStarted)
async def server_diagnose_changes(
    form: DiagnoseChangesForm,
    request: Request,
    principal: Principal = Depends(require(SERVER_DIAGNOSE)),
    ctx=Depends(get_server),
):
    """Apply all, Apply safe or Deny all on the changes the last Diagnose
    offered. Applying makes them (one backup, one Undo), then the joins run
    again and a change that didn't help is put back. Follow it with GET
    /server/diagnose. Risky changes: owner only."""
    from ... import diagnose, diagnosefix

    result = ctx.diagnosis or {}
    if result.get("running"):
        raise HTTPException(status_code=409, detail="Diagnose server is still running.")
    if not result.get("changes"):
        raise HTTPException(status_code=409, detail="There are no changes waiting.")
    risky = any(not change["safe"] for change in result["changes"])
    if form.pick == "all" and risky and not principal.is_owner:
        raise HTTPException(
            status_code=403, detail="Only the owner can make risky changes. Apply safe instead."
        )
    audit(ctx, request, "server_diagnose_changes", detail=form.pick)
    if form.pick == "none":
        diagnosefix.deny(result)
        return {"job_id": None}
    fixes = diagnosefix.picked(ctx, result, form.pick)
    job = diagnose.start(ctx, principal.user, before=result, fixes=fixes)
    return {"job_id": job.id}


@router.get("/server/diagnose", response_model=Diagnosis | None)
async def server_diagnosis(
    principal: Principal = Depends(require(SERVER_DIAGNOSE)), ctx=Depends(get_server)
):
    """The last Diagnose server result, or the one running (its lines so far)."""
    return ctx.diagnosis


@router.post("/server/restart-now", response_model=ServerAction)
async def restart_now(
    request: Request,
    principal: Principal = Depends(require(SERVER_CONTROL)),
    ctx=Depends(get_server),
):
    """Skip the automatic-restart countdown and start immediately."""
    with audit_failure(ctx, request, "restart_now", result="refused"):
        await ctx.server.restart_now(actor=principal.user)
    audit(ctx, request, "restart_now")
    return {
        "result": "REQUESTED",
        "state": ctx.server.state.value,
        "detail": "The server is starting. It is online once startup completes.",
    }


@router.post("/server/cancel-restart", response_model=ServerAction)
async def cancel_restart(
    request: Request,
    principal: Principal = Depends(require(SERVER_CONTROL)),
    ctx=Depends(get_server),
):
    """Stop the automatic-restart countdown. The server stays stopped."""
    with audit_failure(ctx, request, "cancel_restart", result="refused"):
        await ctx.server.cancel_pending_restart(actor=principal.user)
    audit(ctx, request, "cancel_restart")
    return {
        "result": "VERIFIED",
        "state": ctx.server.state.value,
        "detail": "Automatic restart cancelled. The server will stay stopped.",
    }


@router.post("/server/clear-crash-block", response_model=CrashBlockCleared)
async def clear_crash_block(
    request: Request,
    principal: Principal = Depends(require(SERVER_CONTROL)),
    ctx=Depends(get_server),
):
    ctx.server.clear_crash_block()
    audit(ctx, request, "clear_crash_block")
    return {"ok": True, "auto_restart_blocked": ctx.server.auto_restart_blocked}


@router.post("/server/command", response_model=CommandSent)
async def server_command(
    payload: CommandRequest,
    request: Request,
    principal: Principal = Depends(require(CONSOLE_SEND)),
    ctx=Depends(get_server),
):
    validated = validate(payload.command, confirm=payload.confirm)
    if validated.name in ("ban-ip", "pardon-ip") and ctx.core.guard.fallback(ctx):
        return await _guard_ban(ctx, request, principal, validated)
    await ctx.server.send_command(validated.raw)
    audit(ctx, request, "console_command", target=validated.name, detail=validated.raw)
    return {
        "result": "SENT",
        "detail": "The command was written to the Minecraft console. Read the console "
        "output to see what the server did with it.",
        **validated.to_dict(),
    }


async def _guard_ban(ctx, request: Request, principal: Principal, validated) -> dict:
    """Behind Join protection without the proxy add-on the server sees every
    player as this PC, so "ban-ip" would shut everyone out. The guard
    blocks the player's real address instead."""
    words = validated.raw.split()
    target = words[1] if len(words) > 1 else ""
    hub = ctx.core.guard
    try:
        address = joinguard.address_key(target)
    except ValueError:
        address = hub.address_of(ctx, target)
    if not address:
        raise HTTPException(
            status_code=400,
            detail=f"Join protection hasn't seen {target or 'that player'} join, so it doesn't "
            "know their address. Use their address instead.",
        )
    if validated.name == "ban-ip":
        hub.block(ctx, address, principal.user)
        detail = f"Join protection now blocks {address} (the server only sees this PC)."
    else:
        hub.unblock(ctx, address, principal.user)
        detail = f"Join protection no longer blocks {address}."
    audit(ctx, request, "console_command", target=validated.name, detail=validated.raw)
    return {"result": "BLOCKED", "detail": detail, **validated.to_dict()}


@router.get("/server/command/check", response_model=CommandCheck)
async def check_command(command: str, principal: Principal = Depends(require(CONSOLE_SEND))):
    """Ask whether a command needs confirmation, before sending it."""
    from ...minecraft.commands import describe_danger

    try:
        validate(command, confirm=True)
        valid, error = True, None
    except CommandError as exc:
        valid, error = False, str(exc)
    return {"valid": valid, "error": error, "danger_reason": describe_danger(command)}


@router.get("/performance", response_model=Performance)
async def performance(
    hours: float = 6,
    storage: bool = True,
    principal: Principal = Depends(require(SERVER_VIEW)),
    ctx=Depends(get_server),
):
    # storage=false skips the disk-use breakdown, which walks the whole
    # server folder; the Overview's hover summaries don't need it.
    hours = max(0.1, min(hours, 168))
    return {
        "current": ctx.metrics.snapshot(),
        "history": ctx.metrics.history(hours=hours),
        "series": ctx.metrics.series(hours=hours),
        "sample_interval": ctx.config.monitor.sample_interval,
        "memory_limit_mb": memory_limit_mb(ctx.config.server.jvm_args),
        "thresholds": ctx.config.thresholds.to_dict(),
        "storage": await ctx.metrics.storage() if storage else None,
    }


@router.get("/health/server", response_model=ServerHealth)
async def server_health(
    principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    # online_count() returns None when the player list has not been
    # established, and the health page renders that as unknown.
    # The checks include `tailscale status` and a TCP connect, so they run
    # in a worker thread.
    return await asyncio.to_thread(ctx.metrics.health, player_count=ctx.players.online_count())


@router.get("/worlds", response_model=WorldList)
async def worlds(principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)):
    return {"worlds": ctx.backups.world_summary()}


@router.post("/worlds/save", response_model=Requested)
async def save_worlds(
    request: Request,
    principal: Principal = Depends(require(SERVER_CONTROL)),
    ctx=Depends(get_server),
):
    await ctx.server.send_command("save-all flush")
    audit(ctx, request, "world_save")
    return {
        "result": "REQUESTED",
        "detail": "save-all flush was written to the server console. Minecraft does not "
        "acknowledge it in a way the agent can verify.",
    }


@router.get("/tps", response_model=TpsStatus)
async def tps_status(principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)):
    return ctx.tps.status()


@router.post("/tps/detect", response_model=TpsStatus)
async def tps_detect(
    request: Request,
    principal: Principal = Depends(require(SERVER_CONTROL)),
    ctx=Depends(get_server),
):
    try:
        result = await ctx.tps.redetect()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(ctx, request, "tps_redetect")
    return result


@router.put("/tps", response_model=TpsStatus)
async def tps_set_command(
    payload: TpsCommandRequest,
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    try:
        result = await ctx.tps.set_command(payload.command)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit(ctx, request, "tps_command_set", detail=payload.command or "off")
    return result
