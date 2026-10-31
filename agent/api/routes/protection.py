"""The Protection card and its one-click fixes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ... import protection
from ...security.auth import Principal
from ...security.permissions import SERVER_VIEW, on_server, require
from ..deps import audit, get_server
from ..responses import ProtectionCard, ProtectionFixed
from .models import ProtectionFixRequest

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
