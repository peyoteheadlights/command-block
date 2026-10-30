"""Start requests: someone who can't start a server asks the owner and
moderators to start it (Phase 11, `/request-start` in Discord).

Only while the server is stopped, one open request per server, 10 minutes
between requests on a server, 15 minutes to answer. The owner and moderators
get a "start_request" alert (desktop, phone, the Discord alerts channel) and
answer Start or Say no; Start runs the server's own start, the same as the
apps' Start button.

Nothing here knows about Discord: the bot keeps where to answer in
``reply`` and sets ``on_close`` to tell the requester (``TOLD``) and tidy its
alert messages. Open requests are saved, so an agent restart keeps them; a
request still ends 15 minutes after it was asked, by the clock.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .events import Event

if TYPE_CHECKING:
    from .core import AgentCore, ServerContext

log = logging.getLogger("msc.startrequests")

GAP = 600.0  # seconds between requests on one server
LIFE = 900.0  # seconds the owner and moderators have to answer
STOPPED = ("OFFLINE", "CRASHED")
SAVED_KEY = "start_requests"  # {"open": server id -> request, "last": server id -> when}

# What the requester hears, by how the request ended.
TOLD = {
    "started": "{server} is starting. Give it a minute or two.",
    "no": "Not now: the owner or a moderator said no to starting {server}.",
    "expired": "Nobody answered in 15 minutes, so {server} wasn't started. Try again later.",
    "online": "{server} is online now. Come and play!",
}


class StartRequestError(Exception):
    """A request that can't be made or answered, with the words to say why."""


@dataclass
class StartRequest:
    server_id: str
    who: str  # the requester's name, as shown in the alert
    asked_at: float
    # Where the requester hears the answer, kept by whoever asked (the bot:
    # its channel and member ids).
    reply: dict[str, str] | None = None
    # The Discord alert messages with Start / Say no buttons: [channel, message].
    posts: list[list[str]] = field(default_factory=list)

    def saved(self) -> dict[str, Any]:
        return {
            "who": self.who,
            "asked_at": self.asked_at,
            "reply": self.reply,
            "posts": self.posts,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "who": self.who,
            "asked_at": self.asked_at,
            "expires_at": self.asked_at + LIFE,
        }


class StartRequests:
    def __init__(self, core: AgentCore):
        self.core = core
        self.open: dict[str, StartRequest] = {}
        self.last: dict[str, float] = {}  # server id -> when it was last asked
        self._timers: dict[str, asyncio.Task] = {}
        self.on_close: Callable[[StartRequest, str], Awaitable[None]] | None = None

    def get(self, server_id: str) -> StartRequest | None:
        return self.open.get(server_id)

    def load(self) -> None:
        """The requests open when the agent stopped, each with its own clock:
        one asked 15 minutes ago or more ends now, as unanswered."""
        saved = self.core.db.get_setting(SAVED_KEY, {}) or {}
        now = time.time()
        self.last = {sid: at for sid, at in (saved.get("last") or {}).items() if at + GAP > now}
        for sid, row in (saved.get("open") or {}).items():
            if sid not in self.core.servers or sid in self.open:
                continue
            try:
                request = StartRequest(
                    sid,
                    str(row["who"]),
                    float(row["asked_at"]),
                    row.get("reply"),
                    [list(p) for p in row.get("posts") or []],
                )
            except (KeyError, TypeError, ValueError):
                continue  # a damaged entry is dropped, not guessed at
            self.open[sid] = request
            self._timers[sid] = asyncio.create_task(self._expire(sid), name=f"start-request-{sid}")
        self.save()

    def save(self) -> None:
        self.core.db.set_setting(
            SAVED_KEY,
            {"open": {sid: r.saved() for sid, r in self.open.items()}, "last": self.last},
        )

    async def ask(
        self, ctx: ServerContext, who: str, reply: dict[str, str] | None = None
    ) -> StartRequest:
        sid = ctx.server_id
        state = ctx.server.state.value
        if state not in STOPPED:
            raise StartRequestError(
                f"{ctx.name} isn't stopped (it's {state.lower().replace('_', ' ')}), "
                "so there's nothing to ask for."
            )
        if sid in self.open:
            raise StartRequestError(
                f"Someone already asked to start {ctx.name}. The owner and moderators know."
            )
        wait = self.last.get(sid, 0) + GAP - time.time()
        if wait > 0:
            raise StartRequestError(
                f"{ctx.name} was asked for a moment ago. Try again in {math.ceil(wait / 60)} min."
            )
        request = StartRequest(sid, who, time.time(), reply)
        self.open[sid] = request
        self.last[sid] = request.asked_at
        self.save()
        self._timers[sid] = asyncio.create_task(self._expire(sid), name=f"start-request-{sid}")
        await ctx.bus.publish(
            Event(
                type="start_request",
                message=f"{who} asks to start {ctx.name}",
                data={"who": who},
            )
        )
        return request

    async def answer(self, ctx: ServerContext, start: bool, by: str) -> str:
        """Start or Say no. A start the server refuses leaves the request
        open and raises the server's own error."""
        if ctx.server_id not in self.open:
            raise StartRequestError(f"There's no start request open for {ctx.name}.")
        if start:
            await ctx.server.start(actor=by)
        await self.close(ctx.server_id, "started" if start else "no")
        return "started" if start else "no"

    async def close(self, server_id: str, how: str) -> None:
        request = self.open.pop(server_id, None)
        timer = self._timers.pop(server_id, None)
        if timer and timer is not asyncio.current_task():
            timer.cancel()
        if request is None:
            return
        self.save()
        if self.on_close is None:
            return
        try:
            await self.on_close(request, how)
        except Exception:
            log.warning("couldn't tell how a start request ended", exc_info=True)

    async def _expire(self, server_id: str) -> None:
        request = self.open[server_id]
        await asyncio.sleep(max(0.0, request.asked_at + LIFE - time.time()))
        await self.close(server_id, "expired")

    async def handle(self, event: Event) -> None:
        """Event bus subscriber: a server started any other way ends its request."""
        if event.type == "server_started" and event.server_id in self.open:
            await self.close(event.server_id, "online")

    def stop(self) -> None:
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
