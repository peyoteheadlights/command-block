"""One server's whitelist help (agent/whitelist.py): requests to join, the
addresses an IP whitelist add-on turned players away from (masked), and
whether a whitelist add-on is used. Personal data, so it takes the Players
permission (players.manage), like Linked accounts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ... import whitelist
from ...security.auth import Principal
from ...security.permissions import PLAYERS_MANAGE, require
from ..deps import audit, get_server
from ..responses import WhitelistHelp
from .models import AddressAnswer, WhitelistAnswer

router = APIRouter(prefix="/whitelist")


def help_of(ctx, action=None) -> dict:
    return {
        **whitelist.state(ctx),
        "forms": ctx.core.whitelist.forms(ctx.server_id),
        "addresses": ctx.core.whitelist.addresses(ctx),
        "action": action.to_dict() if action is not None else None,
    }


@router.get("", response_model=WhitelistHelp)
async def whitelist_help(
    principal: Principal = Depends(require(PLAYERS_MANAGE)), ctx=Depends(get_server)
):
    return help_of(ctx)


@router.post("/forms/{form_id}", response_model=WhitelistHelp)
async def answer_form(
    form_id: int,
    payload: WhitelistAnswer,
    request: Request,
    principal: Principal = Depends(require(PLAYERS_MANAGE)),
    ctx=Depends(get_server),
):
    """Approve sends the whitelist command ("Sent" until the console says so)."""
    form, pending = await ctx.core.whitelist.answer(ctx, form_id, payload.answer == "approve")
    audit(ctx, request, "whitelist_request_" + payload.answer, target=form["name"])
    return help_of(ctx, pending)


@router.post("/addresses/{address_id}", response_model=WhitelistHelp)
async def answer_address(
    address_id: int,
    payload: AddressAnswer,
    request: Request,
    principal: Principal = Depends(require(PLAYERS_MANAGE)),
    ctx=Depends(get_server),
):
    """Allow adds the address to the IP whitelist add-on; Ignore stops asking."""
    found = ctx.core.whitelist
    pending = None
    if payload.answer == "allow":
        pending = await found.allow(ctx, address_id, principal.user)
    else:
        found.ignore(ctx, address_id, principal.user)
    audit(ctx, request, "ip_" + payload.answer, target=str(address_id))
    return help_of(ctx, pending)


@router.delete("/addresses/{address_id}", response_model=WhitelistHelp)
async def remove_address(
    address_id: int,
    request: Request,
    principal: Principal = Depends(require(PLAYERS_MANAGE)),
    ctx=Depends(get_server),
):
    """Off the add-on's list (if it was allowed) and deleted."""
    await ctx.core.whitelist.drop(ctx, address_id)
    audit(ctx, request, "ip_remove", target=str(address_id))
    return help_of(ctx)
