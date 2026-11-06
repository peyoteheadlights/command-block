"""Editable settings, maintenance mode and notifications."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import serveruser
from ...config import ConfigError
from ...events import Event
from ...security.auth import Principal
from ...security.permissions import PC_SETTINGS, require
from ..deps import audit, get_core
from ..responses import (
    AlertHistory,
    MaintenanceSet,
    SeparateWindowsUser,
    SettingsSaved,
    SettingsView,
    TestAlert,
)
from .models import MaintenanceRequest, SeparateWindowsUserRequest, SettingsRequest

router = APIRouter()
log = logging.getLogger("msc.settings")


SETTABLE_PREFIXES = (
    "monitor.",
    "thresholds.",
    "notifications.events.",
    "notifications.discord_enabled",
    "notifications.webhook_with_bot",
    "notifications.email_enabled",
    "notifications.push_enabled",
    "notifications.email.",
    "notifications.min_interval_seconds",
    "backups.keep_",
    "backups.include",
    "backups.stop_server_for_backup",
    "mods.backup_before_install",
    "mods.update_check_hours",
    "maintenance.",
    "server.max_players",
    "server.stop_timeout",
    "server.start_timeout",
    "server.autostart_minecraft",
    "server.jvm_args",
    "security.session_hours",
    "security.chat_spam_",
    "power.keep_awake",
    "updates.check",
)


@router.get("/settings", response_model=SettingsView)
async def get_settings(
    principal: Principal = Depends(require(PC_SETTINGS)), core=Depends(get_core)
):
    """The PC's own settings, the owner's alone. A moderator's screens read
    each server's settings (/api/servers/{id}/settings) instead."""
    return {
        "config": core.config.as_dict(redact_secrets=True),
        "editable": list(SETTABLE_PREFIXES),
        "secrets": {
            "discord_webhook_configured": bool(core.config.discord_webhook),
            "smtp_configured": bool(core.config.smtp_password),
            "api_token_configured": bool(core.config.api_token),
        },
    }


@router.put("/settings", response_model=SettingsSaved)
async def update_settings(
    payload: SettingsRequest,
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    core=Depends(get_core),
):
    applied, rejected = {}, {}
    for key, value in payload.updates.items():
        if not any(key == p or key.startswith(p) for p in SETTABLE_PREFIXES):
            rejected[key] = "This setting can't be changed here."
            continue
        previous = core.config.get(key)
        core.config.set(key, value)
        try:
            core.config.section(key.split(".", 1)[0])
        except ConfigError as exc:
            core.config.set(key, previous)
            rejected[key] = str(exc)
            continue
        applied[key] = value
    if applied:
        try:
            core.config.save()
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Settings could not be written to disk: {exc}"
            ) from exc
        audit(core, request, "settings_update", detail=", ".join(applied))
        if "power.keep_awake" in applied:
            # Turned off or on while a server runs: Windows is told now, not
            # at the next start or stop.
            core.keepawake.update()
    return {"ok": True, "applied": applied, "rejected": rejected}


# Restarts started by a switch of the separate Windows user, kept so they
# aren't dropped half way.
_restarts: set[asyncio.Future] = set()


@router.get("/settings/separate-windows-user", response_model=SeparateWindowsUser)
async def separate_windows_user(
    principal: Principal = Depends(require(PC_SETTINGS)), core=Depends(get_core)
):
    return await asyncio.to_thread(serveruser.status, core.config)


@router.post("/settings/separate-windows-user", response_model=SeparateWindowsUser)
async def set_separate_windows_user(
    payload: SeparateWindowsUserRequest,
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    core=Depends(get_core),
):
    """Called by the desktop app after the installer made (Turn on, Repair)
    the account, and before it removes it (Turn off). Running servers
    restart, so they run as the account the setting now names."""
    now = await asyncio.to_thread(serveruser.status, core.config)
    if payload.on and not now["ok"]:
        raise HTTPException(status_code=409, detail=serveruser.MISSING)
    was = serveruser.is_on(core.config)
    changed = payload.on != was
    core.config.set("security.separate_windows_user", payload.on)
    try:
        core.config.save()
    except OSError as exc:
        core.config.set("security.separate_windows_user", was)
        raise HTTPException(
            status_code=500, detail=f"Settings could not be written to disk: {exc}"
        ) from exc
    audit(core, request, "separate_windows_user", detail="on" if payload.on else "off")

    async def switch(ctx) -> None:
        # Restart first: a server's folder access is only taken away once
        # it runs as the owner again, never while it's writing its world.
        if ctx.server.running:
            done = await ctx.server.restart(actor=principal.user)
            if not done.get("restarted", True):
                raise RuntimeError(done.get("error") or "it didn't come back up")
        if payload.on:
            return
        folders = [ctx.config.server_dir]
        home = serveruser.java_home(ctx.config.server.java)
        if home:
            folders.append(home)
        for folder in folders:
            problem = await asyncio.to_thread(serveruser.remove_grant, folder)
            if problem:
                core.bus.publish_soon(
                    Event(
                        type="server_user_rights_left",
                        level="warn",
                        server_id=ctx.server_id,
                        message=f"The separate Windows user's access to {folder} "
                        f"couldn't be removed: {problem}",
                    )
                )

    servers = list(core.servers.values())
    restarting = [ctx.server_id for ctx in servers if changed and ctx.server.running]
    if changed:
        work = asyncio.gather(*(switch(ctx) for ctx in servers), return_exceptions=True)
        if payload.on:
            task = asyncio.ensure_future(work)
            task.add_done_callback(lambda done: _log_failures(servers, done.result()))
            _restarts.add(task)
            task.add_done_callback(_restarts.discard)
        else:
            # Turn off waits, so the installer only removes an account
            # nothing runs as any more.
            failed = _log_failures(servers, await work)
            if failed:
                raise HTTPException(
                    status_code=500,
                    detail=f"{', '.join(failed)} didn't restart, so the separate Windows "
                    "user was kept. Restart it, then select Turn off again.",
                )
    return {**await asyncio.to_thread(serveruser.status, core.config), "restarting": restarting}


def _log_failures(servers, results) -> list[str]:
    failed = []
    for ctx, result in zip(servers, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("separate Windows user switch: %s: %s", ctx.server_id, result)
            failed.append(ctx.name)
    return failed


@router.post("/maintenance", response_model=MaintenanceSet)
async def set_maintenance(
    payload: MaintenanceRequest,
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    core=Depends(get_core),
):
    result = core.set_maintenance(payload.enabled, user=principal.user)
    audit(core, request, "maintenance_mode", detail="on" if payload.enabled else "off")
    return {"ok": True, **result}


@router.post("/notifications/test", response_model=TestAlert)
async def test_notification(
    channel: str,
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    core=Depends(get_core),
):
    try:
        result = await core.notifier.test(channel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit(core, request, "notification_test", target=channel, detail=str(result["sent"]))
    return result


@router.get("/notifications/history", response_model=AlertHistory)
async def notification_history(
    limit: int = 50, principal: Principal = Depends(require(PC_SETTINGS)), core=Depends(get_core)
):
    return {"history": core.notifier.history(max(1, min(limit, 200)))}
