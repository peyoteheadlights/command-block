"""The "How friends join" card's addresses, and "Let friends join from the
internet" (agent/internet.py)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import internet, joininfo, protection
from ...config import ConfigError
from ...security.auth import Principal
from ...security.permissions import SERVER_VIEW, SETTINGS_SERVER, require
from ..deps import audit, get_server
from ..responses import InternetReach, JoinInfo
from .models import InternetRequest, InternetTestRequest, PhoneTestRequest

router = APIRouter()


@router.get("/join", response_model=JoinInfo)
async def how_to_join(
    principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    """The addresses and port friends type into Minecraft, read from this
    PC. Asking Tailscale runs its command, so not on the event loop."""
    return await asyncio.to_thread(joininfo.join_info, ctx)


def _owner(principal: Principal) -> None:
    if not principal.is_owner:
        raise HTTPException(status_code=403, detail="Only the owner can open the router port.")


@router.get("/internet", response_model=InternetReach)
async def internet_status(
    principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    return await asyncio.to_thread(internet.summary, ctx)


@router.put("/internet", response_model=InternetReach)
async def internet_switch(
    payload: InternetRequest,
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    """On: the router opens the game ports now if the server runs, and on
    every start. Off: closed at once."""
    _owner(principal)
    previous = ctx.config.server.internet
    ctx.config.set("server.internet", payload.on)
    try:
        ctx.config.section("server")
        ctx.config.save()
    except (ConfigError, OSError) as exc:
        ctx.config.set("server.internet", previous)
        raise HTTPException(status_code=500, detail=f"Couldn't save the setting: {exc}") from exc
    audit(ctx, request, "internet_on" if payload.on else "internet_off")
    if payload.on and ctx.server.running:
        await protection.router_open(ctx)
    elif not payload.on:
        await protection.router_close(ctx)
    return await asyncio.to_thread(internet.summary, ctx)


@router.post("/internet/test", response_model=InternetReach)
async def internet_test(
    payload: InternetTestRequest,
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    """Test from the internet through mcsrvstat.us. Asked once: it sends the
    server's internet address to that site (PRIVACY.md)."""
    _owner(principal)
    if payload.allow_mcsrvstat:
        ctx.core.db.set_setting(internet.MCSRVSTAT_OK, True)
    if not ctx.core.db.get_setting(internet.MCSRVSTAT_OK, False):
        raise HTTPException(status_code=409, detail="Say yes to asking mcsrvstat.us first.")
    if not internet.public_address(ctx):
        raise HTTPException(
            status_code=409, detail="The router hasn't given an internet address yet."
        )
    await internet.test_with_mcsrvstat(ctx)
    audit(ctx, request, "internet_test")
    return await asyncio.to_thread(internet.summary, ctx)


@router.post("/internet/phone-test", response_model=InternetReach)
async def internet_phone_test(
    payload: PhoneTestRequest,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    """The phone app pinged the internet address over mobile data (Wi-Fi off)."""
    internet.phone_test(ctx, payload.address, payload.port, payload.works)
    return await asyncio.to_thread(internet.summary, ctx)
