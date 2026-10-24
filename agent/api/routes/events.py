"""One server's events and polls (agent/game_events.py): make them, call
them off, approve and fix attendance, close polls early. Under /quests,
because /events is the Activity feed. All of it takes "Quests and events"
(quests.manage)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ...game_events import allowed_kinds
from ...quests import QuestError
from ...security.auth import Principal
from ...security.permissions import QUESTS_MANAGE, require
from ..deps import audit, get_server
from ..responses import EventsPage
from .models import EventFix, NewEvent, NewPoll
from .quests import checked_rewards, run

router = APIRouter(prefix="/quests")


def page_of(ctx, changed: bool = True) -> dict:
    found, bot = ctx.core.game_events, ctx.core.discord
    if bot is not None and changed:
        bot.spawn(bot.update_events())  # Discord's posts follow the change
    return {
        "events": found.page(ctx.server_id),
        "polls": found.poll_page(ctx.server_id),
        "kinds": allowed_kinds(ctx),
        "bot": bot is not None and bot.gateway.state == "connected",
    }


def sync(work, *args):
    try:
        return work(*args)
    except QuestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/events", response_model=EventsPage)
async def list_events(
    principal: Principal = Depends(require(QUESTS_MANAGE)), ctx=Depends(get_server)
):
    return page_of(ctx, changed=False)


@router.post("/events", response_model=EventsPage)
async def make_event(
    payload: NewEvent,
    request: Request,
    principal: Principal = Depends(require(QUESTS_MANAGE)),
    ctx=Depends(get_server),
):
    """Rewards only from the templates (ADR 0004)."""
    rewards = await checked_rewards(ctx, payload.rewards)
    made = await run(
        ctx.core.game_events.make(ctx, {**payload.model_dump(), "rewards": rewards}, principal.user)
    )
    audit(ctx, request, "event_make", target=made["title"])
    return page_of(ctx)


@router.delete("/events/{event_id}", response_model=EventsPage)
async def call_off(
    event_id: int,
    request: Request,
    principal: Principal = Depends(require(QUESTS_MANAGE)),
    ctx=Depends(get_server),
):
    event = sync(ctx.core.game_events.cancel, ctx.server_id, event_id)
    audit(ctx, request, "event_cancel", target=event["title"])
    return page_of(ctx)


@router.post("/events/{event_id}/approve", response_model=EventsPage)
async def approve(
    event_id: int,
    principal: Principal = Depends(require(QUESTS_MANAGE)),
    ctx=Depends(get_server),
):
    """The suggested list of an approval event is right: rewards go out."""
    sync(ctx.core.game_events.approve, ctx, event_id, principal.user)
    return page_of(ctx)


@router.put("/events/{event_id}/attendance/{discord_id}", response_model=EventsPage)
async def fix_attendance(
    event_id: int,
    discord_id: str,
    payload: EventFix,
    principal: Principal = Depends(require(QUESTS_MANAGE)),
    ctx=Depends(get_server),
):
    """Tick or untick one member, up to 7 days after the end (audited)."""
    sync(ctx.core.game_events.fix, ctx, event_id, discord_id, payload.attended, principal.user)
    return page_of(ctx)


@router.post("/polls", response_model=EventsPage)
async def make_poll(
    payload: NewPoll,
    request: Request,
    principal: Principal = Depends(require(QUESTS_MANAGE)),
    ctx=Depends(get_server),
):
    made = await run(ctx.core.game_events.make_poll(ctx, payload.model_dump(), principal.user))
    audit(ctx, request, "poll_make", target=made["question"])
    return page_of(ctx)


@router.delete("/polls/{poll_id}", response_model=EventsPage)
async def close_poll(
    poll_id: int,
    request: Request,
    principal: Principal = Depends(require(QUESTS_MANAGE)),
    ctx=Depends(get_server),
):
    """Closes it now; the result is posted."""
    poll = sync(ctx.core.game_events.close_poll, ctx.server_id, poll_id)
    audit(ctx, request, "poll_close", target=poll["question"])
    return page_of(ctx)
