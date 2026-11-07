"""Rented servers on a host's Pterodactyl panel (agent/rented.py).

Connecting a panel and Remove key are the owner's (SERVERS_MANAGE). The
per-server routes check a moderator's permissions on that rented server
exactly like a home server's, and nothing here ever answers with the key.
A panel's own refusal comes back as 502 with plain words, never as 401,
which the apps would read as "you were signed out".
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...minecraft.commands import validate
from ...rented import KILL_WARNING, PanelError, RentedServer
from ...security.auth import Principal
from ...security.permissions import (
    BACKUPS_CREATE,
    CONSOLE_READ,
    CONSOLE_SEND,
    DISCORD_MANAGE,
    SERVER_CONTROL,
    SERVER_VIEW,
    SERVERS_MANAGE,
    can_see_server,
    require,
)
from ..deps import audit, get_core, server_access
from ..responses import Open

log = logging.getLogger("msc.rented")
router = APIRouter()
ONE = [Depends(server_access)]


class PanelForm(BaseModel):
    address: str = Field(max_length=300)
    key: str = Field(max_length=200)
    # The owner's click: "Let Command Block talk to this panel".
    allow_host: bool = False
    servers: list[str] = Field(default_factory=list, max_length=100)


class PowerForm(BaseModel):
    signal: str = Field(max_length=10)
    confirm: bool = False


class CommandForm(BaseModel):
    command: str = Field(max_length=1000)
    confirm: bool = False


class DiscordForm(BaseModel):
    choice: str = Field(max_length=10)


class RentedList(Open):
    servers: list[dict[str, Any]]
    panels: list[dict[str, Any]]


class RentedRow(Open):
    id: str
    kind: str
    name: str
    host: str
    state: str
    state_reason: str | None = None


class PanelCheck(Open):
    address: str
    host: str
    servers: list[dict[str, Any]]
    full_account: bool


class RentedAdded(Open):
    added: list[dict[str, Any]]


class KeyRemoved(Open):
    removed: list[str]
    host: str


class RentedDone(Open):
    result: str
    detail: str


class RentedConsoleLines(Open):
    lines: list[dict[str, Any]]
    last: int
    problem: str | None = None


class RentedBackups(Open):
    backups: list[dict[str, Any]]
    limit: int | None = None


@contextmanager
def panel_words():
    try:
        yield
    except PanelError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def rented(server_id: str, core=Depends(get_core)) -> RentedServer:
    try:
        return core.rented.get(server_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"There is no rented server with the id '{server_id}'"
        ) from None


@router.get("/rented", response_model=RentedList)
async def list_rented(principal: Principal = Depends(require(SERVER_VIEW)), core=Depends(get_core)):
    shown = [s for s in core.rented.servers.values() if can_see_server(principal, s.id)]
    owner = principal.access is None
    return {
        "servers": [core.rented.to_dict(s) for s in shown],
        # Which panels are connected, for Remove key; the key itself never.
        "panels": [
            {"id": pid, "host": p["host"], "address": p["address"]}
            for pid, p in core.rented.panels.items()
        ]
        if owner
        else [],
    }


def _allowed(form: PanelForm) -> None:
    if not form.allow_host:
        raise HTTPException(
            status_code=400,
            detail='Tick "Let Command Block talk to this panel" first.',
        )


@router.post("/rented/check", response_model=PanelCheck)
async def check_panel(
    form: PanelForm,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    core=Depends(get_core),
):
    """The panel's servers for this key, before anything is saved."""
    _allowed(form)
    with panel_words():
        return await core.rented.check_panel(form.address, form.key)


@router.post("/rented/panels", response_model=RentedAdded)
async def connect_panel(
    form: PanelForm,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    core=Depends(get_core),
):
    _allowed(form)
    with panel_words():
        added = await core.rented.connect(form.address, form.key, form.servers, principal.user)
    return {"added": added}


@router.delete("/rented/panels/{panel_id}", response_model=KeyRemoved)
async def remove_key(
    panel_id: str,
    principal: Principal = Depends(require(SERVERS_MANAGE)),
    core=Depends(get_core),
):
    """Remove key: the key is deleted and the panel's servers leave the list."""
    try:
        return await core.rented.remove_key(panel_id, principal.user)
    except KeyError:
        raise HTTPException(status_code=404, detail="That panel isn't connected.") from None


@router.get("/rented/{server_id}/status", response_model=RentedRow, dependencies=ONE)
async def rented_status(
    principal: Principal = Depends(require(SERVER_VIEW)),
    server: RentedServer = Depends(rented),
    core=Depends(get_core),
):
    return core.rented.to_dict(server)


@router.post("/rented/{server_id}/power", response_model=RentedDone, dependencies=ONE)
async def rented_power(
    form: PowerForm,
    request: Request,
    principal: Principal = Depends(require(SERVER_CONTROL)),
    server: RentedServer = Depends(rented),
    core=Depends(get_core),
):
    if form.signal == "kill" and not form.confirm:
        raise HTTPException(status_code=400, detail=KILL_WARNING)
    with panel_words():
        await core.rented.power(server, form.signal)
    audit(core, request, f"rented_{form.signal}", target=server.id)
    return {
        "result": "REQUESTED",
        "detail": "The panel was asked. The status changes once the host has done it.",
    }


@router.get("/rented/{server_id}/console", response_model=RentedConsoleLines, dependencies=ONE)
async def rented_console(
    after: int = 0,
    principal: Principal = Depends(require(CONSOLE_READ)),
    server: RentedServer = Depends(rented),
    core=Depends(get_core),
):
    """The console lines after ``after``. Reading it keeps the panel's
    console connection open; it closes a minute after nobody reads."""
    return core.rented.console_of(server).read(after)


@router.post("/rented/{server_id}/command", response_model=RentedDone, dependencies=ONE)
async def rented_command(
    form: CommandForm,
    request: Request,
    principal: Principal = Depends(require(CONSOLE_SEND)),
    server: RentedServer = Depends(rented),
    core=Depends(get_core),
):
    validated = validate(form.command, confirm=form.confirm)
    with panel_words():
        await core.rented.command(server, validated.raw)
    audit(core, request, "rented_command", target=server.id, detail=validated.name)
    return {"result": "SENT", "detail": "Sent to the server's console on the host."}


@router.get("/rented/{server_id}/backups", response_model=RentedBackups, dependencies=ONE)
async def rented_backups(
    principal: Principal = Depends(require(SERVER_VIEW)),
    server: RentedServer = Depends(rented),
    core=Depends(get_core),
):
    with panel_words():
        return await core.rented.backups(server)


@router.post("/rented/{server_id}/backups", response_model=RentedDone, dependencies=ONE)
async def rented_make_backup(
    request: Request,
    principal: Principal = Depends(require(BACKUPS_CREATE)),
    server: RentedServer = Depends(rented),
    core=Depends(get_core),
):
    with panel_words():
        await core.rented.make_backup(server)
    audit(core, request, "rented_backup", target=server.id)
    return {"result": "REQUESTED", "detail": "The host is making the backup."}


@router.put("/rented/{server_id}/discord", response_model=RentedRow, dependencies=ONE)
async def rented_discord(
    form: DiscordForm,
    request: Request,
    principal: Principal = Depends(require(DISCORD_MANAGE)),
    server: RentedServer = Depends(rented),
    core=Depends(get_core),
):
    """What the Discord bot may do with it: off, status, or control
    (status, start and stop for roles that may start servers)."""
    with panel_words():
        core.rented.set_discord(server, form.choice)
    audit(core, request, "rented_discord", target=server.id, detail=form.choice)
    # The /rented commands' list of servers changes with it.
    bot = core.discord
    if bot is not None and getattr(bot, "connected", False):
        for guild_id in list(bot.guilds):
            try:
                await bot.refresh(guild_id)
            except Exception:
                log.info("couldn't update the Discord commands", exc_info=True)
    return core.rented.to_dict(server)
