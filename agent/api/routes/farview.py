"""Far view on the Mods page (agent/farview.py): Off / Distant Horizons /
Voxy, "generate unvisited land", "Load the world ahead" and "Rebuild far
view"."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from ... import farview
from ...security.auth import Principal
from ...security.permissions import MODS_MANAGE, SERVER_VIEW, SETTINGS_SERVER, on_server, require
from ..deps import audit, get_server
from ..responses import FarView, FarViewChanged, FarViewRebuilt
from .models import FarViewChoiceRequest, FarViewOptionsRequest

router = APIRouter()


@router.get("/far-view", response_model=FarView)
async def far_view(principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)):
    """Each choice with what it adds here, or why it can't."""
    return await farview.card(ctx, MODS_MANAGE in on_server(principal, ctx.server_id))


@router.post("/far-view", response_model=FarViewChanged)
async def far_view_choose(
    payload: FarViewChoiceRequest,
    request: Request,
    principal: Principal = Depends(require(MODS_MANAGE)),
    ctx=Depends(get_server),
):
    """Turn far view off or to a choice: the mods it needs on the server
    and in Players only. The server has to be stopped."""
    result = await ctx.mod_change(
        "Changing far view",
        lambda: farview.choose(ctx, payload.choice, principal.user),
        principal.user,
    )
    audit(ctx, request, "far_view", target=payload.choice, detail=", ".join(result["added"]))
    return {"ok": True, **result}


@router.post("/far-view/options", response_model=FarView)
async def far_view_options(
    payload: FarViewOptionsRequest,
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    await asyncio.to_thread(
        farview.set_options,
        ctx,
        payload.unvisited,
        payload.load_ahead,
        payload.radius,
        payload.world,
    )
    audit(ctx, request, "far_view_options", detail=f"load ahead {payload.load_ahead}")
    return await farview.card(ctx, MODS_MANAGE in on_server(principal, ctx.server_id))


@router.post("/far-view/rebuild", response_model=FarViewRebuilt)
async def far_view_rebuild(
    request: Request,
    principal: Principal = Depends(require(SETTINGS_SERVER)),
    ctx=Depends(get_server),
):
    """Delete the far-view data (it is built again from the world). The
    server has to be stopped."""
    result = await asyncio.to_thread(farview.rebuild, ctx, principal.user)
    audit(ctx, request, "far_view_rebuild", detail=f"{result['files']} files")
    return {"ok": True, **result}
