"""One server's Linked accounts (agent/discord/links.py): see and remove
them, and approve Bedrock players' link requests. Personal data, so it
takes the Players permission (players.manage)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ...discord.links import LinkError
from ...security.auth import Principal
from ...security.permissions import PLAYERS_MANAGE, require
from ..deps import audit, get_server
from ..responses import DiscordLinks
from .models import DiscordLinkAnswer

router = APIRouter(prefix="/discord-links")


def links_of(ctx) -> dict:
    from ...levels import all_xp, standing

    links = ctx.core.links
    xp = all_xp(ctx.db)
    return {
        # Each with the member's Level (across all their linked accounts).
        "links": [
            {**row, **standing(xp.get(row["discord_id"], 0))}
            for row in links.of_server(ctx.server_id)
        ],
        "requests": links.requests(ctx.server_id),
        "players": [
            {"name": p["username"], "uuid": p["uuid"]}
            for p in ctx.players.all_players(limit=50)
            if p.get("uuid")
        ],
        "reads_chat": ctx.config.server_type.reads_chat,
    }


@router.get("", response_model=DiscordLinks)
async def list_links(
    principal: Principal = Depends(require(PLAYERS_MANAGE)), ctx=Depends(get_server)
):
    return links_of(ctx)


@router.delete("/{uuid}", response_model=DiscordLinks)
async def remove_link(
    uuid: str,
    request: Request,
    principal: Principal = Depends(require(PLAYERS_MANAGE)),
    ctx=Depends(get_server),
):
    found = next((r for r in ctx.core.links.of_server(ctx.server_id) if r["uuid"] == uuid), None)
    if found is None:
        raise HTTPException(status_code=404, detail="That account isn't linked here.")
    ctx.core.links.unlink(uuid)
    audit(ctx, request, "discord_unlink", target=found["name"])
    return links_of(ctx)


@router.post("/requests/{request_id}", response_model=DiscordLinks)
async def answer_request(
    request_id: int,
    payload: DiscordLinkAnswer,
    request: Request,
    principal: Principal = Depends(require(PLAYERS_MANAGE)),
    ctx=Depends(get_server),
):
    links = ctx.core.links
    player = None
    if payload.answer == "approve":
        player = ctx.db.query_one(
            "SELECT username, uuid, edition FROM players WHERE server_id = ? AND uuid = ?",
            (ctx.server_id, payload.uuid or ""),
        )
        if player is None:
            raise HTTPException(
                status_code=400, detail="Pick the player from this server's players."
            )
    try:
        asked = links.find_request(ctx.server_id, request_id)
        if player is not None:
            links.link(
                player["uuid"],
                player["username"],
                player["edition"],
                ctx.server_id,
                asked["discord_id"],
                asked["discord_name"],
                principal.user,
            )
    except LinkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    links.drop_request(request_id)
    audit(
        ctx,
        request,
        "discord_link_" + payload.answer,
        target=player["username"] if player else asked["name"],
    )
    bot = ctx.core.discord
    if bot is not None and asked.get("channel_id"):
        words = (
            f"you're linked: you play as {player['username']} on {ctx.name}."
            if player
            else f"your link request on {ctx.name} was turned down."
        )
        bot.spawn(bot.tell(asked["channel_id"], asked["discord_id"], words))
    return links_of(ctx)
