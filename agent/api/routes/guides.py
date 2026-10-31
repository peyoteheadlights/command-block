"""Add-on guides and their command buttons."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ... import guides
from ...security.auth import Principal
from ...security.permissions import SERVER_VIEW, on_server, require
from ..deps import audit, get_server
from ..responses import GuideAnswer, GuideList
from .models import GuideButtonRequest

router = APIRouter()


@router.get("/guides", response_model=GuideList)
async def list_guides(
    principal: Principal = Depends(require(SERVER_VIEW)), ctx=Depends(get_server)
):
    """The guides for this server's installed add-ons, each with the buttons
    this account may press."""
    return {"guides": guides.for_server(ctx, on_server(principal, ctx.server_id))}


@router.post("/guides/{guide_id}/{button_id}", response_model=GuideAnswer)
async def press_button(
    guide_id: str,
    button_id: str,
    payload: GuideButtonRequest,
    request: Request,
    principal: Principal = Depends(require(SERVER_VIEW)),
    ctx=Depends(get_server),
):
    """Send one button's command. Each button needs its own permission
    (console, players or operators), checked here."""
    result = await guides.press(
        ctx,
        guide_id,
        button_id,
        payload.values,
        payload.confirm,
        on_server(principal, ctx.server_id),
    )
    audit(ctx, request, "guide_button", target=f"{guide_id}/{button_id}", detail=result["command"])
    return result
