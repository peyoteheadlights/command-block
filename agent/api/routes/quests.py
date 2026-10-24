"""One server's quests (agent/quests.py): make and end them, approve
"done" claims, answer quest ideas, and see the rewards not given yet. All of
it takes "Quests and events" (quests.manage)."""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, HTTPException, Request

from ...quests import QuestError, allowed_kinds
from ...security.auth import Principal
from ...security.permissions import QUESTS_MANAGE, require
from ..deps import audit, get_server
from ..responses import QuestScreenshot, QuestsPage
from .models import NewQuest, QuestAnswer

router = APIRouter(prefix="/quests")


def page_of(ctx) -> dict:
    from ...discord import layout

    quests, bot = ctx.core.quests, ctx.core.discord
    connected = bot is not None and bot.gateway.state == "connected"
    guilds = []
    if connected:
        for gid, guild in bot.guilds.items():
            if ctx.server_id in layout.servers_of(ctx.db, gid):
                roles = (bot.reports.get(gid) or {}).get("roles") or []
                guilds.append(
                    {
                        **guild,
                        "roles": [
                            {"id": r["id"], "name": r["name"]} for r in roles if r["id"] != gid
                        ],
                        "levels": bot.levels_of(gid),
                    }
                )
    return {
        "quests": quests.board(ctx.server_id),
        "claims": [
            {k: v for k, v in c.items() if k not in ("channel_id", "discord_id")}
            for c in quests.claims(ctx.server_id)
        ],
        "suggestions": [
            {k: v for k, v in s.items() if k != "channel_id"}
            for s in quests.suggestions(ctx.server_id)
        ],
        "rewards": quests.rewards(ctx.server_id),
        "kinds": allowed_kinds(ctx),
        "bot": connected,
        "guilds": guilds,
    }


async def run(work):
    try:
        return await work
    except QuestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("", response_model=QuestsPage)
async def list_quests(
    principal: Principal = Depends(require(QUESTS_MANAGE)), ctx=Depends(get_server)
):
    return page_of(ctx)


@router.post("", response_model=QuestsPage)
async def make_quest(
    payload: NewQuest,
    request: Request,
    principal: Principal = Depends(require(QUESTS_MANAGE)),
    ctx=Depends(get_server),
):
    """Rewards only from the templates (ADR 0004); a Discord role must be
    one that gives no power in Discord, checked with Discord."""
    from ...discord.bot import Refused

    rewards = [r.model_dump(exclude_none=True) for r in payload.rewards]
    for reward in rewards:
        if reward["kind"] != "role":
            continue
        bot = ctx.core.discord
        if bot is None or bot.gateway.state != "connected":
            raise HTTPException(status_code=409, detail="The bot has to be connected first.")
        try:
            await bot.reward_role(ctx.server_id, reward.get("role_id", ""))
        except Refused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
    made = await run(
        ctx.core.quests.make(ctx, {**payload.model_dump(), "rewards": rewards}, principal.user)
    )
    audit(ctx, request, "quest_make", target=made["title"])
    return page_of(ctx)


@router.delete("/{quest_id}", response_model=QuestsPage)
async def end_quest(
    quest_id: int,
    request: Request,
    principal: Principal = Depends(require(QUESTS_MANAGE)),
    ctx=Depends(get_server),
):
    """Ends it now; who finished it still counts for stacked roles."""
    try:
        quest = ctx.core.quests.end(ctx.server_id, quest_id)
    except QuestError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    audit(ctx, request, "quest_end", target=quest["title"])
    return page_of(ctx)


@router.post("/claims/{claim_id}", response_model=QuestsPage)
async def answer_claim(
    claim_id: int,
    payload: QuestAnswer,
    principal: Principal = Depends(require(QUESTS_MANAGE)),
    ctx=Depends(get_server),
):
    await run(
        ctx.core.quests.answer_claim(ctx, claim_id, payload.answer == "approve", principal.user)
    )
    return page_of(ctx)


@router.get("/claims/{claim_id}/screenshot", response_model=QuestScreenshot)
async def claim_screenshot(
    claim_id: int,
    principal: Principal = Depends(require(QUESTS_MANAGE)),
    ctx=Depends(get_server),
):
    try:
        path = ctx.core.quests.screenshot(ctx.server_id, claim_id)
    except QuestError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    if path is None:
        raise HTTPException(status_code=404, detail="That claim has no screenshot.")
    return {"type": path.suffix[1:], "image": base64.b64encode(path.read_bytes()).decode()}


@router.delete("/suggestions/{suggestion_id}", response_model=QuestsPage)
async def turn_down(
    suggestion_id: int,
    request: Request,
    principal: Principal = Depends(require(QUESTS_MANAGE)),
    ctx=Depends(get_server),
):
    try:
        asked = ctx.core.quests.drop_suggestion(ctx.server_id, suggestion_id)
    except QuestError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    ctx.core.quests.tell(
        asked, f'thanks for the idea "{asked["title"]}". It isn\'t a quest this time.'
    )
    audit(ctx, request, "quest_idea_no", target=asked["title"])
    return page_of(ctx)
