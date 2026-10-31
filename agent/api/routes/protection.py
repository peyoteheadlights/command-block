"""The Protection card and its one-click fixes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import joinguard, protection
from ...security.auth import Principal
from ...security.permissions import SERVER_VIEW, SETTINGS_SERVER, on_server, require
from ..deps import audit, get_server
from ..responses import JoinProtection, Ok, ProtectionCard, ProtectionFixed
from .models import JoinBlockRequest, JoinLaunchRequest, ProtectionFixRequest

router = APIRouter()


@router.get("/protection", response_model=ProtectionCard)
async def protection_card(
    principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    """How this server is set up, read from its own files, with the fixes
    this account may use."""
    return await protection.card(ctx, on_server(principal, ctx.server_id))


@router.post("/protection/fix", response_model=ProtectionFixed)
async def protection_fix(
    payload: ProtectionFixRequest,
    request: Request,
    principal: Principal = Depends(require(SERVER_VIEW)),
    ctx=Depends(get_server),
):
    """One fix from the card. Each fix needs its own permission, checked here."""
    body = {"port": payload.port} if payload.port else {}
    result = await protection.fix(
        ctx, payload.fix, on_server(principal, ctx.server_id), principal.user, body
    )
    audit(ctx, request, "protection_fix", target=payload.fix)
    return {"ok": True, "fix": payload.fix, **result}


# ---------------------------------------------------------------- Join protection
@router.get("/join-protection", response_model=JoinProtection)
async def join_protection(
    principal: Principal = Depends(require(SETTINGS_SERVER)), ctx=Depends(get_server)
):
    """What Join protection is doing, its block list and the name-to-address
    log (addresses masked). Turning it on and off is in Server settings."""
    hub = ctx.core.guard
    status = await asyncio.to_thread(hub.status, ctx)
    return {"status": status, **await asyncio.to_thread(hub.technical, ctx)}


@router.post("/join-protection/launch", response_model=Ok)
async def start_launch(
    payload: JoinLaunchRequest,
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    if not await asyncio.to_thread(joinguard.set_up, ctx):
        raise HTTPException(
            status_code=400,
            detail="Launch mode is for a server that's already set up. Start it once so "
            "its world is made first.",
        )
    await ctx.core.guard.start_launch(ctx, payload.hours, payload.players)
    audit(ctx, request, "launch_mode", detail=f"{payload.hours:g} h, {payload.players} players")
    return {"ok": True}


@router.delete("/join-protection/launch", response_model=Ok)
async def end_launch(
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    await ctx.core.guard.end_launch(ctx)
    audit(ctx, request, "launch_mode_end")
    return {"ok": True}


def _address(text: str) -> str:
    try:
        return joinguard.address_key(text)
    except ValueError:
        raise HTTPException(status_code=400, detail="That isn't an address.") from None


@router.post("/join-protection/blocks", response_model=Ok)
async def add_block(
    payload: JoinBlockRequest,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    """A block the owner adds: it stays until removed."""
    address = _address(payload.address)
    await asyncio.to_thread(ctx.core.guard.block, ctx, address, principal.user)
    return {"ok": True}


@router.delete("/join-protection/blocks", response_model=Ok)
async def remove_block(
    payload: JoinBlockRequest,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    address = _address(payload.address)
    await asyncio.to_thread(ctx.core.guard.unblock, ctx, address, principal.user)
    return {"ok": True}
