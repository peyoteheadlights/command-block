"""The "How friends join" card's addresses, and "Let friends join from the
internet" (agent/internet.py)."""

from __future__ import annotations

import asyncio
from functools import partial

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import ddns, internet, joininfo, playit, protection, waterdog
from ...config import ConfigError
from ...security.auth import Principal
from ...security.permissions import PC_SETTINGS, SERVER_VIEW, SETTINGS_SERVER, require
from ..deps import audit, get_core, get_server
from ..responses import DdnsState, InternetReach, JoinInfo, PlayitState
from .models import (
    DuckDnsRequest,
    InternetRequest,
    InternetTestRequest,
    NoIpRequest,
    PhoneTestRequest,
    WaterdogRequest,
)

router = APIRouter()
# DuckDNS and No-IP names are the PC's, not one server's (agent/ddns.py).
global_router = APIRouter()


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
    if payload.on and ctx.core.waterdog.of(ctx):
        raise HTTPException(
            status_code=409,
            detail="This server is behind WaterdogPE for playit.gg. Its own port lets players "
            "in without the Xbox sign-in check, so the router won't open it.",
        )
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


# ---------------------------------------------------------------- playit.gg
# One program for the whole PC; each server asks for its own tunnels.
async def _playit(ctx, principal: Principal) -> dict:
    try:
        await ctx.core.playit.refresh()
    except TimeoutError:
        pass  # the program hangs: the last answer stands
    found = ctx.core.playit.summary(ctx)
    if not principal.is_owner:
        # Whoever opens the claim page links the program to their own playit
        # account, and a guest account's login link opens it: owner only.
        found["claim"] = found["claim"] and {**found["claim"], "url": ""}
        found["new_tunnel"] = playit.NEW_TUNNEL
    return found


@router.get("/playit", response_model=PlayitState)
async def playit_status(
    principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    return await _playit(ctx, principal)


@router.post("/playit/{action}", response_model=PlayitState)
async def playit_action(
    action: str,
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    ctx=Depends(get_server),
):
    """use: link the installed program (or carry on if it is linked).
    reconnect: playit's own ``playit start``. stop: unlink it, its tunnels stop."""
    _owner(principal)
    found = ctx.core.playit
    if action not in ("use", "reconnect", "stop"):
        raise HTTPException(status_code=404, detail="No such action.")
    problem = None
    try:
        if action == "use":
            await found.use()
        elif action == "reconnect":
            await found.reconnect()
        else:
            problem = await found.stop()
    except playit.PlayitError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="playit didn't answer.") from exc
    audit(ctx.core, request, f"playit_{action}")
    return {**await _playit(ctx, principal), "stop_problem": problem}


@router.post("/playit-test", response_model=PlayitState)
async def playit_test(
    payload: InternetTestRequest,
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    """Test the tunnel from the internet through mcsrvstat.us (asked once, as above)."""
    _owner(principal)
    if payload.allow_mcsrvstat:
        ctx.core.db.set_setting(internet.MCSRVSTAT_OK, True)
    if not ctx.core.db.get_setting(internet.MCSRVSTAT_OK, False):
        raise HTTPException(status_code=409, detail="Say yes to asking mcsrvstat.us first.")
    if ctx.config.server_type.edition == "bedrock":
        # mcsrvstat.us asks the old Bedrock way (RakNet), which a NetherNet
        # tunnel doesn't answer: its "no" would be a guess.
        raise HTTPException(
            status_code=409,
            detail="mcsrvstat.us can't test Bedrock's new connection (NetherNet). Join from a "
            "phone on mobile data instead.",
        )
    await _playit(ctx, principal)
    address = ctx.core.playit.tunnel_address(ctx)
    if not address:
        raise HTTPException(status_code=409, detail="This server has no playit tunnel yet.")
    await playit.test_tunnel(ctx, address)
    audit(ctx, request, "playit_test")
    return ctx.core.playit.summary(ctx)


@router.post("/playit-bedrock", response_model=PlayitState)
async def playit_bedrock(
    payload: WaterdogRequest,
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    """A plain Bedrock server goes behind WaterdogPE for playit's NetherNet
    tunnel: downloads WaterdogPE and the Companion's allow list plugin (the
    owner's click, house rule 2) and moves the server to the box's port."""
    _owner(principal)
    ctx.require_free()
    try:
        done = await ctx.core.waterdog.use(ctx, payload.box, principal.user)
    except waterdog.WaterdogError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(ctx, request, "waterdog_on")
    return {**await _playit(ctx, principal), "internet_off": done["internet_off"]}


@router.delete("/playit-bedrock", response_model=PlayitState)
async def playit_bedrock_off(
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    """Back to the server's own port and allow list; WaterdogPE's folder goes."""
    _owner(principal)
    ctx.require_free()
    try:
        await ctx.core.waterdog.stop_using(ctx, principal.user)
    except waterdog.WaterdogError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(ctx, request, "waterdog_off")
    return await _playit(ctx, principal)


# ---------------------------------------------------------------- DuckDNS and No-IP
@global_router.get("/ddns", response_model=DdnsState)
async def ddns_status(principal: Principal = Depends(require(SERVER_VIEW)), core=Depends(get_core)):
    return core.ddns.status()


async def _ddns_save(core, request: Request, action: str, save):
    try:
        found = await save()
    except ddns.DdnsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    core.ddns.ensure_running()
    audit(core, request, action)
    return found


@global_router.put("/ddns/duckdns", response_model=DdnsState)
async def ddns_duckdns(
    payload: DuckDnsRequest,
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    core=Depends(get_core),
):
    """Saved only after DuckDNS said OK to the name and token."""
    _owner(principal)
    save = partial(core.ddns.save_duckdns, payload.name, payload.token)
    return await _ddns_save(core, request, "duckdns_saved", save)


@global_router.put("/ddns/noip", response_model=DdnsState)
async def ddns_noip(
    payload: NoIpRequest,
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    core=Depends(get_core),
):
    """Saved only after No-IP said good (or nochg) to the name and sign-in."""
    _owner(principal)
    save = partial(core.ddns.save_noip, payload.name, payload.username, payload.password)
    return await _ddns_save(core, request, "noip_saved", save)


@global_router.post("/ddns/noip/confirmed", response_model=DdnsState)
async def ddns_noip_confirmed(
    principal: Principal = Depends(require(PC_SETTINGS)), core=Depends(get_core)
):
    """The owner confirmed the name on noip.com: the 30 days start again."""
    _owner(principal)
    try:
        return core.ddns.noip_confirmed()
    except ddns.DdnsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@global_router.delete("/ddns/{kind}", response_model=DdnsState)
async def ddns_remove(
    kind: str,
    request: Request,
    principal: Principal = Depends(require(PC_SETTINGS)),
    core=Depends(get_core),
):
    """Forgets the name and its token or sign-in. The name stays on the site."""
    _owner(principal)
    if kind not in ("duckdns", "noip"):
        raise HTTPException(status_code=404, detail="No such name.")
    audit(core, request, f"{kind}_removed")
    return core.ddns.remove(kind)
