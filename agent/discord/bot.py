"""The Discord bot: its connection, its commands and what it reports.

Made by AgentCore only when a bot token is set (MCSC_DISCORD_BOT_TOKEN in
.env). Every slash command becomes a sign-in of kind "discord" built from
the member's Discord roles (layout.member_principal) and is checked with
``permissions.check``, exactly like an API route, then rate-limited per
member and written to the audit log. Each command in COMMANDS declares the
permission it needs, and tests/test_discord.py walks them all.

What it says in Discord is plain English here: Discord shows one language
to everyone, and the apps' words table is for the apps.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import logging
import math
import re
import time
import urllib.parse
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .. import levels
from ..config import PROJECT_ROOT
from ..events import OWNER_ONLY_EVENTS, Event
from ..jobs import JobConflict
from ..minecraft.chat import ChatError, relay
from ..minecraft.commands import CommandError
from ..minecraft.playeractions import DONE, SENT, PlayerActionError
from ..minecraft.process import ServerError
from ..security import permissions as P
from ..security.auth import AuthError, Principal, RateLimiter
from ..startrequests import STOPPED, StartRequest, StartRequestError
from ..stats import player_stats
from . import bridge, client, layout, live, progress
from .links import CODE_LIFE, LinkError

if TYPE_CHECKING:
    from ..core import AgentCore, ServerContext

log = logging.getLogger("msc.discord")

DEFAULT_NAME = "Command Block"
LOGO = PROJECT_ROOT / "desktop" / "images" / "icon-192.png"
# Guild events, plus messages when the Message Content switch is on (the
# chat bridge's Discord to game half). Asking for a switch that's off would
# stop the bot, so it's only asked for once Discord says it's on.
INTENTS = client.GUILDS
MESSAGE_INTENTS = client.GUILD_MESSAGES | client.MESSAGE_CONTENT
EPHEMERAL = 64
# Minecraft names as the server prints them; Floodgate's "." is kept.
NAME_RE = re.compile(r"^\.?[A-Za-z0-9_]{1,16}$")
PROFILE_KEY = "discord_profile"
POSTS_KEY = "discord_status_posts"  # status channel id -> {"message", "words"}
LIVE_FIRST = 5.0  # seconds after logging in (the Discord servers arrive first)
LIVE_EVERY = 60.0  # seconds between status message and presence checks
START_COOLDOWN = 300.0  # seconds between starts of one server from Discord
BATCH = 1.0  # seconds game lines are gathered before they're posted
REFUSED = "\N{NO ENTRY SIGN}"  # the reaction on a message that didn't go into the game
SEEN_KEY = "discord_levels_seen"  # member id -> the Level their roles were last set for
BOARDS_KEY = "discord_leaderboards"  # guild id -> the day its leaderboard was last posted
BOARD_HOUR = 18  # on the PC's clock


class Refused(Exception):
    """A command that can't go ahead, with the words to say why."""


# Refusals whose words are safe to show in Discord as they are.
SAYS_WHY = (Refused, LinkError, StartRequestError, PlayerActionError, JobConflict)


@dataclass
class Call:
    """One slash command as it arrived."""

    guild_id: str
    channel_id: str | None
    user: dict
    principal: Principal
    options: dict[str, Any]
    interaction: dict
    ctx: ServerContext | None = None
    arg: str = ""  # a button's player name

    @property
    def member_id(self) -> str:
        return str(self.user["id"])

    @property
    def member_name(self) -> str:
        return str(self.user.get("global_name") or self.user.get("username") or "someone")


Handler = Callable[["Bot", Call], Awaitable[str]]


@dataclass
class Command:
    name: str
    description: str
    # Checked like a route's: on the chosen server when needs_server, else
    # on at least one server. SERVER_VIEW is what everyone in a Discord
    # server tied to that Minecraft server holds.
    permission: str
    handler: Handler
    needs_server: bool = True
    options: list[dict] = field(default_factory=list)
    # Answered "thinking..." first, for work that can take longer than the
    # 3 seconds Discord waits for an answer.
    slow: bool = False


@dataclass
class Button:
    """A button under an alert. Its id is "action:server:player"; one with
    ``sure`` asks "Are you sure?" (only the presser sees it) first."""

    action: str
    label: str
    permission: str
    handler: Handler
    sure: str | None = None  # the question; {name} and {server} filled in
    style: int = 2  # Discord's grey; 1 blue, 3 green, 4 red


NAME_OPTION = {
    "type": 3,
    "name": "name",
    "description": "Your Minecraft name",
    "required": True,
    "max_length": 17,
}


class Bot:
    def __init__(self, core: AgentCore, token: str):
        self.core = core
        self.rest = client.Rest(token)
        self.gateway = client.Gateway(self.rest, token, INTENTS, self._event, self._before_login)
        self.user: dict = {}
        self.app: dict = {}  # the application, with its switches (flags)
        self.guilds: dict[str, dict] = {}  # id -> {"id", "name"}
        self.reports: dict[str, dict] = {}  # id -> layout.inspect's answer
        # Per member, like the sign-in limiter: a burst is fine, a flood isn't.
        self.rate = RateLimiter(8, 60.0)
        self.chat_rate = RateLimiter(5, 10.0)  # the chat bridge, per member
        self._outbox: dict[str, list[str]] = {}  # chat channel id -> game lines to post
        self.started: dict[str, float] = {}  # server id -> last /start from Discord
        self._task: asyncio.Task | None = None
        self._live: asyncio.Task | None = None
        self._jobs: set[asyncio.Task] = set()
        self._levels_lock = asyncio.Lock()
        self._resync = False  # set when the Level roles changed: everyone's are set again
        core.start_requests.on_close = self.request_closed
        core.bus.subscribe(self.from_game)

    # -- running -------------------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="discord-bot")

    async def _run(self) -> None:
        await self.gateway.run()
        if self.gateway.state == "token_rejected":
            await self.core.bus.publish(
                Event(
                    type="discord_token_rejected",
                    level="error",
                    message="Discord refused the bot's token. Paste a new one on the Discord page.",
                )
            )

    async def stop(self) -> None:
        if self.core.start_requests.on_close == self.request_closed:
            self.core.start_requests.on_close = None
        self.core.bus.unsubscribe(self.from_game)
        for task in [self._task, self._live, *self._jobs]:
            if task:
                task.cancel()
        if self._task:
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        await self.rest.close()

    def spawn(self, work: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(work)
        self._jobs.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task) -> None:
        self._jobs.discard(task)
        if not task.cancelled() and task.exception():
            exc = task.exception()
            log.warning(
                "Discord work failed: %s",
                exc if isinstance(exc, client.DiscordError) else type(exc).__name__,
            )

    async def _before_login(self) -> None:
        """Before each fresh login: read the switches, ask for what's on."""
        self.app = await self.rest.request("GET", "/applications/@me")
        on = self.switches()["message_content"]
        self.gateway.intents = INTENTS | (MESSAGE_INTENTS if on else 0)

    async def _event(self, name: str, data: dict) -> None:
        if name == "READY":
            self.user = data.get("user") or {}
            self.spawn(self._after_ready())
            if self._live is None:
                self._live = asyncio.create_task(self._live_loop(), name="discord-live")
        elif name in ("GUILD_CREATE", "GUILD_UPDATE"):
            self.guilds[data["id"]] = {"id": data["id"], "name": data.get("name", "")}
            if name == "GUILD_CREATE":
                self.spawn(self.refresh(data["id"]))
        elif name == "GUILD_DELETE" and not data.get("unavailable"):
            self.guilds.pop(data["id"], None)
            self.reports.pop(data["id"], None)
        elif name == "INTERACTION_CREATE":
            self.spawn(self.interaction(data))
        elif name == "MESSAGE_CREATE":
            self.spawn(self.from_discord(data))

    # -- the status messages and the presence line ------------------------------
    async def _live_loop(self) -> None:
        """Once a minute: the status messages, Level roles and level-ups,
        and the weekly leaderboards. One failing doesn't stop the others."""
        await asyncio.sleep(LIVE_FIRST)
        while True:
            for work in (self.update_live, self.update_levels, self.post_leaderboards):
                try:
                    await work()
                except client.DiscordError as exc:
                    log.info("Discord %s didn't work: %s", work.__name__, exc)
                except Exception:
                    log.exception("Discord %s didn't work", work.__name__)
            await asyncio.sleep(LIVE_EVERY)

    async def update_live(self) -> None:
        """Edit what changed, and nothing else (weak mini PCs, and Discord's
        rate limits)."""
        tied = sorted({sid for gid in self.guilds for sid in self.server_names(gid)})
        line = live.presence([self.core.servers[sid] for sid in tied])
        if line and line != self.gateway.presence:
            await self.gateway.set_presence(line)
        shared = layout.addresses(self.core.db)
        posts = self.core.db.get_setting(POSTS_KEY, {}) or {}
        changed = False
        for gid in list(self.guilds):
            for row in layout.channels(self.core.db, gid):
                ctx = self.core.servers.get(row["server_id"])
                if row["kind"] != "status" or ctx is None:
                    continue
                words = live.status_message(ctx, shared.get(ctx.server_id))
                post = posts.get(row["channel_id"]) or {}
                if post.get("words") == words:
                    continue
                posts[row["channel_id"]] = {
                    "message": await self._post_or_edit(
                        row["channel_id"], post.get("message"), words
                    ),
                    "words": words,
                }
                changed = True
        if changed:
            self.core.db.set_setting(POSTS_KEY, posts)

    async def _post_or_edit(self, channel_id: str, message_id: str | None, words: str) -> str:
        body = {"content": words, "allowed_mentions": {"parse": []}}
        if message_id:
            try:
                await self.rest.request(
                    "PATCH", f"/channels/{channel_id}/messages/{message_id}", body
                )
                return message_id
            except client.DiscordError as exc:
                if exc.status != 404:  # deleted by someone: post a new one
                    raise
        made = await self.rest.request("POST", f"/channels/{channel_id}/messages", body)
        return str(made["id"])

    # -- Levels ------------------------------------------------------------------
    def resync_levels(self) -> None:
        """The Level roles changed: set everyone's roles again, quietly."""
        self._resync = True
        self.spawn(self.update_levels())

    async def update_levels(self) -> None:
        """Give each member whose Level changed the Level role that goes
        with it (and take the one before), and post their level-up. Discord
        is only called for a change, and a member seen for the first time
        gets their role without a message."""
        async with self._levels_lock:
            db = self.core.db
            resync, self._resync = self._resync, False
            if resync:
                db.set_setting(SEEN_KEY, {})
            seen: dict[str, int] = db.get_setting(SEEN_KEY, {}) or {}
            now = {m: levels.level_of(xp) for m, xp in levels.all_xp(db).items()}
            # Every account unlinked: back to no Level role, once.
            gone = {m for m in seen if m not in now}
            now.update({m: 0 for m in gone})
            changed = {m: lv for m, lv in now.items() if seen.get(m) != lv}
            if not changed:
                return
            for gid in list(self.guilds):
                roles = layout.level_roles(db, gid)
                retired = layout.level_settings(db, gid).get("retired") or []
                if not roles and not retired and not self._announces(gid):
                    continue
                found = {r["id"]: r for r in await self.rest.request("GET", f"/guilds/{gid}/roles")}
                for member, level in changed.items():
                    try:
                        role = await self._set_level_role(gid, member, level, roles, found, retired)
                    except client.DiscordError as exc:
                        if exc.status not in (403, 404):
                            # Discord or the network: try again next minute.
                            now[member] = seen.get(member, -1)
                        log.info("couldn't set a Level role: %s", exc)
                        continue
                    if role is False:
                        continue  # not in this Discord server
                    old = seen.get(member)
                    if old is not None and level > old and self._announces(gid):
                        name = found.get(role or "", {}).get("name") if role else None
                        channel = layout.shared_channel(db, gid, "quests")
                        await self.rest.request(
                            "POST",
                            f"/channels/{channel}/messages",
                            {
                                "content": progress.level_up(member, level, name),
                                "allowed_mentions": {"parse": []},
                            },
                        )
                if resync and retired:
                    layout.set_level_settings(db, gid, retired=[])
            # Kept: who's still linked, and (to try again) anyone Discord failed for.
            db.set_setting(
                SEEN_KEY,
                {m: lv for m, lv in now.items() if lv >= 0 and not (m in gone and lv == 0)},
            )

    def _announces(self, guild_id: str) -> bool:
        return bool(
            layout.level_settings(self.core.db, guild_id)["announce"]
            and layout.shared_channel(self.core.db, guild_id, "quests")
        )

    async def _set_level_role(
        self,
        guild_id: str,
        member_id: str,
        level: int,
        roles: list[tuple[int, str]],
        found: dict[str, dict],
        retired: Sequence[str] = (),
    ) -> str | None | bool:
        """The member gets the Level role for their Level and loses the other
        Level roles (and retired ones). Returns the role given (None for
        none), or False when they aren't in this Discord server."""
        try:
            member = await self.rest.request("GET", f"/guilds/{guild_id}/members/{member_id}")
        except client.DiscordError as exc:
            if exc.status == 404:
                return False
            raise
        held = set(member.get("roles") or [])
        ours = layout.our_roles(self.core.db, guild_id)
        wanted = layout.role_for(level, roles)
        # A role given powers in Discord since it was picked isn't handed out.
        if wanted and (
            wanted not in found or layout.level_role_refused(guild_id, found[wanted], ours)
        ):
            wanted = None
        path = f"/guilds/{guild_id}/members/{member_id}/roles"
        for role_id in [rid for _at, rid in roles] + list(retired):
            if role_id in held and role_id != wanted:
                await self.rest.request("DELETE", f"{path}/{role_id}")
        if wanted and wanted not in held:
            await self.rest.request("PUT", f"{path}/{wanted}")
        return wanted

    async def post_leaderboards(self, now: datetime.datetime | None = None) -> None:
        """Each Discord server's weekly leaderboard, on its day from 18:00,
        once (remembered, so a restart doesn't post it twice)."""
        now = now or datetime.datetime.now()
        if now.hour < BOARD_HOUR:
            return
        db = self.core.db
        posted = db.get_setting(BOARDS_KEY, {}) or {}
        today = now.date().isoformat()
        for gid in list(self.guilds):
            channel = layout.shared_channel(db, gid, "quests")
            day = layout.level_settings(db, gid)["leaderboard_day"]
            if day != now.weekday() or not channel or posted.get(gid) == today:
                continue
            names = self.server_names(gid)
            if not names:
                continue
            ids = sorted(names)
            words = progress.leaderboard(
                " and ".join(sorted(names.values())),
                levels.weekly_playtime(db, ids, now.timestamp()),
                levels.top_levels(db, ids),
            )
            await self.rest.request(
                "POST",
                f"/channels/{channel}/messages",
                {"content": words[:2000], "allowed_mentions": {"parse": []}},
            )
            posted[gid] = today
            db.set_setting(BOARDS_KEY, posted)

    # -- alerts ------------------------------------------------------------------
    def posts_alert(self, event: Event) -> bool:
        """Whether this alert goes to an alerts channel: only a Minecraft
        server's own alerts, never the ones about the PC (owner only)."""
        return (
            self.gateway.state == "connected"
            and event.server_id in self.core.servers
            and event.type not in OWNER_ONLY_EVENTS
            and bool(layout.alert_channels(self.core.db, event.server_id, self.guilds))
        )

    async def post_alert(self, event: Event, embed: dict) -> int:
        assert event.server_id is not None
        request = (
            self.core.start_requests.get(event.server_id) if event.type == "start_request" else None
        )
        body = {
            "embeds": [embed],
            "components": alert_buttons(event, self.core.servers[event.server_id]),
            "allowed_mentions": {"parse": []},
        }
        sent = 0
        for channel_id in layout.alert_channels(self.core.db, event.server_id, self.guilds):
            made = await self.rest.request("POST", f"/channels/{channel_id}/messages", body)
            sent += 1
            if request is not None:
                request.posts.append((channel_id, str(made["id"])))
        return sent

    async def request_closed(self, request: StartRequest, how: str) -> None:
        """A start request ended: its alerts lose their buttons and say how."""
        words = {
            "started": "Answered: started.",
            "no": "Answered: not now.",
            "expired": "Nobody answered.",
            "online": "Started another way.",
        }[how]
        for channel_id, message_id in request.posts:
            self.spawn(
                self.rest.request(
                    "PATCH",
                    f"/channels/{channel_id}/messages/{message_id}",
                    {"content": words, "components": []},
                )
            )

    # -- the chat bridge ------------------------------------------------------------
    def chat_channels(self, guild_ids) -> list[dict[str, Any]]:
        return [
            row
            for gid in guild_ids
            for row in layout.channels(self.core.db, gid)
            if row["kind"] == "chat"
        ]

    async def from_game(self, event: Event) -> None:
        """A bus event: chat and player lines go to the server's chat
        channels, gathered for a moment so a busy chat is one post."""
        if self.gateway.state != "connected" or event.server_id not in self.core.servers:
            return
        line = bridge.game_line(event)
        if line is None:
            return
        for row in self.chat_channels(list(self.guilds)):
            if row["server_id"] == event.server_id:
                waiting = self._outbox.setdefault(row["channel_id"], [])
                waiting.append(line)
                if len(waiting) == 1:
                    self.spawn(self._post_lines(row["channel_id"]))

    async def _post_lines(self, channel_id: str) -> None:
        await asyncio.sleep(BATCH)
        for words in bridge.batches(self._outbox.pop(channel_id, [])):
            await self.rest.request(
                "POST",
                f"/channels/{channel_id}/messages",
                {"content": words, "allowed_mentions": {"parse": []}},
            )

    async def from_discord(self, message: dict) -> None:
        """A message in a chat channel goes into the game, cleaned to one
        plain line. Bots and webhooks (the alerts webhook too) are left out."""
        guild_id, author = message.get("guild_id"), message.get("author") or {}
        if (
            not guild_id
            or author.get("bot")
            or message.get("webhook_id")
            or message.get("type") not in (0, 19)  # a plain message or a reply
        ):
            return
        channel_id = str(message.get("channel_id"))
        server_id = next(
            (
                r["server_id"]
                for r in self.chat_channels([str(guild_id)])
                if r["channel_id"] == channel_id
            ),
            None,
        )
        ctx = self.core.servers.get(server_id or "")
        if ctx is None or ctx.server.state.value != "ONLINE":
            return
        roles = [str(r) for r in (message.get("member") or {}).get("roles") or []]
        try:
            principal = layout.member_principal(self.core.db, str(guild_id), author, roles)
            P.check(principal, P.SERVER_VIEW, ctx.server_id)
            self.chat_rate.check(f"chat:{author.get('id')}")
        except AuthError:
            return  # too fast: dropped, quietly (a reaction each would be a flood too)
        report = self.reports.get(str(guild_id)) or {}
        names = {
            x["id"]: x["name"] for x in [*report.get("roles", []), *report.get("channels", [])]
        }
        text = bridge.to_game(message, names)
        if not text:
            return
        if bridge.looks_like_console(text):
            emoji = urllib.parse.quote(REFUSED)
            await self.rest.request(
                "PUT", f"/channels/{channel_id}/messages/{message['id']}/reactions/{emoji}/@me"
            )
            return
        dialect = ctx.config.server_type.dialect
        try:
            await relay(ctx.server, dialect, "Discord", bridge.display_name(message), text)
        except (ChatError, CommandError, ServerError) as exc:
            log.info("a Discord message didn't reach %s: %s", ctx.name, exc)

    async def _after_ready(self) -> None:
        saved = self.core.db.get_setting(PROFILE_KEY, {}) or {}
        if saved.get("user") != self.user.get("id"):
            # Named "Command Block" with the logo the first time, once per bot.
            self.core.db.set_setting(PROFILE_KEY, {"user": self.user.get("id")})
            try:
                await self.set_profile(DEFAULT_NAME, logo())
            except client.DiscordError as exc:
                log.info("couldn't set the bot's name and picture: %s", exc)

    async def refresh(self, guild_id: str) -> None:
        """Register the commands in a Discord server, then check it."""
        names = self.server_names(guild_id)
        await self.rest.request(
            "PUT",
            f"/applications/{self.app['id']}/guilds/{guild_id}/commands",
            command_list(names),
        )
        await self.check(guild_id)

    async def check(self, guild_id: str | None = None) -> None:
        names = {sid: ctx.name for sid, ctx in self.core.servers.items()}
        for gid in [guild_id] if guild_id else list(self.guilds):
            try:
                report = await layout.inspect(self.rest, self.core.db, gid, self.user["id"], names)
            except client.DiscordError as exc:
                report = {"problems": [{"id": "unreadable", "detail": str(exc)}]}
            self.reports[gid] = {**report, "checked_at": time.time()}

    def server_names(self, guild_id: str) -> dict[str, str]:
        """The Minecraft servers this Discord server is for, id -> name."""
        return {
            sid: self.core.servers[sid].name
            for sid in layout.servers_of(self.core.db, guild_id)
            if sid in self.core.servers
        }

    # -- what the apps see --------------------------------------------------------
    def switches(self) -> dict[str, bool | None]:
        flags = self.app.get("flags")
        if flags is None:
            return {"message_content": None, "members": None}
        return {
            "message_content": bool(flags & client.FLAG_MESSAGE_CONTENT),
            "members": bool(flags & client.FLAG_MEMBERS),
        }

    def status(self) -> dict[str, Any]:
        switches = self.switches()
        off = []
        if switches["message_content"] is False:
            off.append("chat_from_discord")
        if switches["members"] is False:
            off.append("remove_on_leave")
        app_id = self.app.get("id")
        return {
            "token_set": True,
            "state": self.gateway.state,
            "detail": self.gateway.detail,
            "connected_at": self.gateway.connected_at,
            "name": self.user.get("username"),
            "application_id": app_id,
            "invite_url": layout.invite_url(app_id) if app_id else None,
            "switches": switches,
            "features_off": off,
            "no_game_chat": sorted(
                sid
                for sid, ctx in self.core.servers.items()
                if not ctx.config.server_type.reads_chat
            ),
            "addresses": layout.addresses(self.core.db),
            "webhook_with_bot": self.core.config.notifications.webhook_with_bot,
            "guilds": [
                {
                    **guild,
                    "servers": layout.servers_of(self.core.db, guild["id"]),
                    "grants": layout.grants(self.core.db, guild["id"]),
                    "levels": self.levels_of(guild["id"]),
                    "report": self.reports.get(guild["id"]),
                }
                for guild in sorted(self.guilds.values(), key=lambda g: g["name"].lower())
            ],
        }

    def levels_of(self, guild_id: str) -> dict[str, Any]:
        """A Discord server's Level settings and Level roles, with names."""
        names = {r["id"]: r["name"] for r in (self.reports.get(guild_id) or {}).get("roles") or []}
        settings = layout.level_settings(self.core.db, guild_id)
        return {
            "announce": settings["announce"],
            "leaderboard_day": settings["leaderboard_day"],
            "roles": [
                {"level": at, "role_id": rid, "name": names.get(rid)}
                for at, rid in layout.level_roles(self.core.db, guild_id)
            ],
        }

    def problem(self) -> dict[str, Any] | None:
        """The first thing wrong, for the Recommendations card and the
        checklist; None when nothing is (or nothing is known yet)."""
        if self.gateway.state in ("token_rejected", "intents_refused"):
            return {"id": self.gateway.state}
        for gid, report in self.reports.items():
            for problem in report.get("problems") or []:
                if problem["id"] in ("manage_roles", "role_too_low", "channel_blocked"):
                    return {**problem, "guild": self.guilds.get(gid, {}).get("name", "")}
        return None

    async def set_profile(self, name: str | None = None, avatar: bytes | None = None) -> None:
        body: dict[str, Any] = {}
        if name:
            body["username"] = name
        if avatar:
            kind = "png" if avatar.startswith(b"\x89PNG") else "jpeg"
            body["avatar"] = f"data:image/{kind};base64,{base64.b64encode(avatar).decode()}"
        if body:
            self.user = await self.rest.request("PATCH", "/users/@me", body) or self.user

    async def set_up(self, guild_id: str, server_ids: list[str]) -> dict[str, Any]:
        servers = {sid: self.core.servers[sid].name for sid in server_ids}
        made = await layout.set_up(self.rest, self.core.db, guild_id, servers)
        await self.refresh(guild_id)
        return made

    async def tell(self, channel_id: str, member_id: str, words: str) -> None:
        """A message in a channel that pings only that member."""
        await self.rest.request(
            "POST",
            f"/channels/{channel_id}/messages",
            {"content": f"<@{member_id}> {words}", "allowed_mentions": {"users": [member_id]}},
        )

    # -- commands -------------------------------------------------------------------
    async def interaction(self, data: dict) -> None:
        """A slash command (type 2) or a button press (type 3): rate limit,
        pick the server, check the permission like a route, run, answer, audit."""
        kind = data.get("type")
        body = data.get("data") or {}
        member = data.get("member") or {}
        user = member.get("user") or {}
        guild_id = data.get("guild_id")
        if kind not in (2, 3) or not guild_id or not user.get("id"):
            return
        call = Call(
            guild_id=str(guild_id),
            channel_id=data.get("channel_id"),
            user=user,
            principal=layout.member_principal(
                self.core.db, str(guild_id), user, [str(r) for r in member.get("roles") or []]
            ),
            options={o["name"]: o.get("value") for o in body.get("options") or [] if "name" in o},
            interaction=data,
        )
        asking: Button | None = None  # a button that asks "Are you sure?" first
        on_prompt = False  # pressed on the "Are you sure?" message: edit it in place
        if kind == 2:
            command = COMMANDS.get(str(body.get("name")))
            if command is None:
                return
            name, permission, handler = f"/{command.name}", command.permission, command.handler
            needs_server, slow = command.needs_server, command.slow
        else:
            custom = str(body.get("custom_id") or "")
            if custom == "cancel":
                await self.respond(data, "Left as it was.", update=True)
                return
            on_prompt = custom.startswith("sure:")
            action, wanted, call.arg = (custom.removeprefix("sure:").split(":", 2) + ["", ""])[:3]
            button = BUTTONS.get(action)
            if button is None:
                return
            call.options = {"server": wanted}
            name, permission, handler = f"button {action}", button.permission, button.handler
            needs_server, slow = True, True
            asking = button if button.sure and not on_prompt else None
        result, deferred = "ok", False
        try:
            self.rate.check(f"discord:{call.member_id}")
            server_id = self.pick_server(call) if needs_server else None
            P.check(call.principal, permission, server_id)
            if server_id:
                call.ctx = self.core.servers[server_id]
            if asking and call.ctx is not None:
                question = str(asking.sure).format(name=call.arg, server=call.ctx.name)
                await self.respond(data, question, components=sure_buttons(asking, call))
                return
            if slow:
                await self.defer(data, update=on_prompt)
                deferred = True
            words = await handler(self, call)
        except AuthError as exc:
            result = "refused"
            words = (
                f"Slow down a little: try again in {exc.retry_after} s."
                if exc.status == 429
                else "Your Discord roles don't allow that on this server."
            )
        except SAYS_WHY as exc:
            result = "refused"
            words = str(exc)
        except Exception:
            log.exception("%s failed", name)
            result = "failed"
            words = "Something went wrong on the PC. The owner can find it in the agent's log."
        # Logged before answering, so it's there even if Discord can't be reached.
        self.core.db.audit(
            "discord_command",
            user=call.principal.user,
            target=f"{name} {call.arg}".strip(),
            result=result,
            detail=f"Discord member {call.member_id}",
            server_id=call.ctx.server_id if call.ctx else None,
        )
        if deferred:
            await self.rest.request(
                "PATCH", self.original(data), {"content": words[:2000], "components": []}
            )
        else:
            await self.respond(data, words, update=on_prompt)

    def pick_server(self, call: Call) -> str:
        """The option if given, else the channel's Minecraft server, else
        the only one; with several, ask which."""
        names = self.server_names(call.guild_id)
        wanted = call.options.get("server")
        if wanted:
            if wanted not in self.core.servers:
                raise Refused("That server isn't there any more.")
            return str(wanted)
        here = (
            layout.server_of_channel(self.core.db, call.guild_id, call.channel_id)
            if call.channel_id
            else None
        )
        if here in self.core.servers:
            return str(here)
        if len(names) == 1:
            return next(iter(names))
        if not names:
            raise Refused(
                "This Discord server isn't set up for a Minecraft server yet. "
                'The owner can press "Set up this Discord server" in Command Block.'
            )
        raise Refused("Which server? Add the server option: " + ", ".join(sorted(names.values())))

    async def respond(
        self,
        interaction: dict,
        words: str,
        update: bool = False,
        components: list[dict] | None = None,
    ) -> None:
        """Answer only the member sees, or (``update``) change the message
        the pressed button is on."""
        data: dict[str, Any] = {
            "content": words[:2000],
            "allowed_mentions": {"parse": []},
            "components": components or [],
        }
        if not update:
            data["flags"] = EPHEMERAL
        await self._callback(interaction, {"type": 7 if update else 4, "data": data})

    async def defer(self, interaction: dict, update: bool = False) -> None:
        """ "Thinking...", answered later through ``original``."""
        await self._callback(
            interaction, {"type": 6} if update else {"type": 5, "data": {"flags": EPHEMERAL}}
        )

    async def _callback(self, interaction: dict, body: dict) -> None:
        await self.rest.request(
            "POST", f"/interactions/{interaction['id']}/{interaction['token']}/callback", body
        )

    def original(self, interaction: dict) -> str:
        return f"/webhooks/{self.app.get('id')}/{interaction['token']}/messages/@original"

    def edit_reply(self, interaction: dict) -> Callable[[str], None]:
        """Changes the first reply later (within the 15 minutes Discord allows)."""
        path = self.original(interaction)

        def edit(words: str) -> None:
            self.spawn(self.rest.request("PATCH", path, {"content": words[:2000]}))

        return edit


def logo() -> bytes | None:
    try:
        return LOGO.read_bytes()
    except OSError:
        return None


def command_list(names: dict[str, str]) -> list[dict]:
    """The commands as Discord registers them in one Discord server. The
    server option lists that Discord server's own Minecraft servers."""
    server = {
        "type": 3,
        "name": "server",
        "description": "Which Minecraft server",
        "required": False,
        "choices": [{"name": name[:100], "value": sid} for sid, name in sorted(names.items())][:25],
    }
    out = []
    for command in COMMANDS.values():
        options = list(command.options)
        if command.needs_server and server["choices"]:
            options.append(server)
        out.append(
            {
                "name": command.name,
                "description": command.description,
                "type": 1,
                "options": options,
            }
        )
    return out


def alert_buttons(event: Event, ctx: ServerContext) -> list[dict]:
    """The buttons under an alert in the alerts channel."""
    name = str((event.data or {}).get("username") or "")
    wanted = {
        "player_joined": ("kick", "ban"),
        "player_left": ("ban",),
        "server_crashed": ("restart",),
        "crash_loop": ("restart",),
        "low_tps": ("restart",),
        "high_mspt": ("restart",),
        "backup_failed": ("backup",),
        "backup_copy_failed": ("backup",),
        "start_request": ("start_yes", "start_no"),
    }.get(event.type, ())
    if not ctx.config.server_type.bans:
        wanted = tuple(a for a in wanted if a != "ban")
    if event.type.startswith("player_") and not name:
        wanted = ()
    row = [
        {
            "type": 2,
            "style": BUTTONS[a].style,
            "label": BUTTONS[a].label,
            "custom_id": f"{a}:{ctx.server_id}:{name}"[:100],
        }
        for a in wanted
    ]
    return [{"type": 1, "components": row}] if row else []


def sure_buttons(button: Button, call: Call) -> list[dict]:
    assert call.ctx is not None
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 4,
                    "label": f"Yes, {button.label.lower()}",
                    "custom_id": f"sure:{button.action}:{call.ctx.server_id}:{call.arg}"[:100],
                },
                {"type": 2, "style": 2, "label": "Cancel", "custom_id": "cancel"},
            ],
        }
    ]


# -- the commands -------------------------------------------------------------------
async def status(bot: Bot, call: Call) -> str:
    assert call.ctx is not None
    return live.status(call.ctx)


async def players(bot: Bot, call: Call) -> str:
    assert call.ctx is not None
    return live.players(call.ctx)


async def ip(bot: Bot, call: Call) -> str:
    from ..joininfo import join_info

    assert call.ctx is not None
    shared = layout.addresses(bot.core.db).get(call.ctx.server_id)
    info = await asyncio.to_thread(join_info, call.ctx)  # asks Tailscale
    return live.ip(call.ctx, shared, info)


async def start_server(ctx: ServerContext, actor: str) -> None:
    """The apps' start path. Why a start failed stays on the PC: it can name
    folders and programs, which a Discord server shouldn't see."""
    try:
        await ctx.server.start(actor=actor)
    except ServerError as exc:
        log.info("a start from Discord failed: %s", exc)
        raise Refused(f"{ctx.name} didn't start. The owner can see why in Command Block.") from None


async def start(bot: Bot, call: Call) -> str:
    ctx = call.ctx
    assert ctx is not None
    state = ctx.server.state.value
    if state not in STOPPED:
        raise Refused(f"{ctx.name} is {live.state_words(state)} already.")
    wait = bot.started.get(ctx.server_id, 0) + START_COOLDOWN - time.time()
    if wait > 0:
        raise Refused(
            f"{ctx.name} was started from Discord a moment ago. "
            f"Try again in {math.ceil(wait / 60)} min."
        )
    await start_server(ctx, call.principal.user)
    bot.started[ctx.server_id] = time.time()
    return f"Starting {ctx.name}. It takes a minute or two; /status says when it's online."


async def request_start(bot: Bot, call: Call) -> str:
    ctx = call.ctx
    assert ctx is not None
    channel, member = call.channel_id, call.member_id

    async def tell(words: str) -> None:
        if channel:
            await bot.tell(channel, member, words)

    await bot.core.start_requests.ask(ctx, f"{call.member_name} (Discord)", tell)
    return (
        f"Asked. The owner and moderators of {ctx.name} have been told; "
        "you'll hear here within 15 minutes."
    )


# -- the buttons ---------------------------------------------------------------------
async def kick_or_ban(bot: Bot, call: Call) -> str:
    ctx = call.ctx
    assert ctx is not None
    action = str(call.interaction["data"]["custom_id"]).removeprefix("sure:").split(":")[0]
    pending = await ctx.player_actions.send(action, call.arg, confirm=True)
    # "Sent" until the server's console confirms it (like the Players page).
    for _ in range(50):
        if pending.state != SENT:
            break
        await asyncio.sleep(0.1)
    done = {"kick": "was kicked", "ban": "was banned"}[action]
    if pending.state == DONE:
        return f"{pending.name} {done} from {ctx.name}."
    if pending.state == SENT:
        return f"Sent to {ctx.name}; the server hasn't confirmed it yet."
    return f"{ctx.name} answered: {pending.message or 'it did nothing.'}"


async def restart(bot: Bot, call: Call) -> str:
    ctx = call.ctx
    assert ctx is not None
    try:
        result = await ctx.server.restart(actor=call.principal.user)
    except ServerError as exc:
        log.info("a restart from Discord failed: %s", exc)
        raise Refused(
            f"{ctx.name} didn't restart. The owner can see why in Command Block."
        ) from None
    if not result.get("restarted"):
        return (
            f"{ctx.name} stopped but didn't come back up. The owner can see why in Command Block."
        )
    return f"{ctx.name} is restarting."


async def backup(bot: Bot, call: Call) -> str:
    ctx = call.ctx
    assert ctx is not None
    ctx.require_free()
    made = await ctx.backups.create(user=call.principal.user)
    return f"Backup of {ctx.name} done: {made.get('name') or 'saved'}."


async def answer_request(bot: Bot, call: Call) -> str:
    ctx = call.ctx
    assert ctx is not None
    yes = str(call.interaction["data"]["custom_id"]).startswith("start_yes")
    try:
        await bot.core.start_requests.answer(ctx, yes, call.principal.user)
    except ServerError as exc:
        log.info("a start from a start request failed: %s", exc)
        raise Refused(f"{ctx.name} didn't start. The owner can see why in Command Block.") from None
    return f"Starting {ctx.name}." if yes else "Told them no."


async def link(bot: Bot, call: Call) -> str:
    assert call.ctx is not None
    name = str(call.options.get("name") or "").strip()
    if not NAME_RE.match(name):
        raise Refused("That isn't a Minecraft name: letters, numbers and _ only, up to 16.")
    links = bot.core.links
    if not call.ctx.config.server_type.reads_chat:
        links.request(call.ctx.server_id, name, call.member_id, call.member_name, call.channel_id)
        await call.ctx.bus.publish(
            Event(
                type="discord_link_request",
                message=f"{call.member_name} on Discord asks to link the player {name}",
                data={"discord_name": call.member_name, "name": name},
            )
        )
        return (
            f"Sent. {call.ctx.name} can't show its chat to Command Block, so the owner or a "
            "moderator will pick you from the server's players and approve it."
        )
    code = links.new_code(
        call.ctx.server_id,
        name,
        call.member_id,
        call.member_name,
        bot.edit_reply(call.interaction),
    )
    return (
        f"Join {call.ctx.name} as {name} and type this in the game chat within "
        f"{int(CODE_LIFE // 60)} minutes: {code}\nOnly you can see this message."
    )


async def show_stats(bot: Bot, call: Call) -> str:
    """/stats [player]: that player, or your own linked accounts here."""
    ctx = call.ctx
    assert ctx is not None
    name = str(call.options.get("player") or "").strip()
    if name:
        if not NAME_RE.match(name):
            raise Refused("That isn't a Minecraft name: letters, numbers and _ only, up to 16.")
        names = [name]
    else:
        names = [r["name"] for r in bot.core.links.of_member(call.member_id)]
        if not names:
            raise Refused("Add a player name, or link your account with /link first.")
    answers = []
    for wanted in names:
        found = await player_stats(ctx, wanted)
        if found is not None:
            answers.append(progress.stats(found, ctx.name))
    if not answers:
        who = names[0] if len(names) == 1 else "your linked accounts"
        raise Refused(f"Nobody called {who} has played on {ctx.name}.")
    return "\n\n".join(answers)


async def show_level(bot: Bot, call: Call) -> str:
    """/level: across every linked account, so no server is picked."""
    if not bot.core.links.of_member(call.member_id):
        raise Refused("Link your Minecraft account with /link first; Levels come from playing.")
    xp = levels.all_xp(bot.core.db).get(call.member_id, 0)
    roles = layout.level_roles(bot.core.db, call.guild_id)
    try:
        # Also sets their Level role here, for someone who joined this
        # Discord server after their Level last changed.
        found = {
            r["id"]: r for r in await bot.rest.request("GET", f"/guilds/{call.guild_id}/roles")
        }
        await bot._set_level_role(call.guild_id, call.member_id, levels.level_of(xp), roles, found)
    except client.DiscordError as exc:
        log.info("couldn't set a Level role: %s", exc)
        found = {}
    names = {rid: r["name"] for rid, r in found.items()}
    return progress.level(xp, roles, names)


async def my_links(bot: Bot, call: Call) -> str:
    rows = bot.core.links.of_member(call.member_id)
    if not rows:
        return "You have no linked Minecraft accounts. Use /link to add one."
    names = {sid: ctx.name for sid, ctx in bot.core.servers.items()}
    lines = [f"{r['name']} (linked on {names.get(r['server_id'], r['server_id'])})" for r in rows]
    return "Your linked Minecraft accounts:\n" + "\n".join(lines)


async def unlink(bot: Bot, call: Call) -> str:
    name = str(call.options.get("name") or "").strip()
    for row in bot.core.links.of_member(call.member_id):
        if row["name"].lower() == name.lower():
            bot.core.links.unlink(row["uuid"])
            return f"{row['name']} isn't linked to you any more."
    raise Refused(f"{name} isn't one of your linked accounts. /links lists them.")


COMMANDS: dict[str, Command] = {
    c.name: c
    for c in (
        Command(
            "status",
            "Is the Minecraft server online, and how many are playing",
            P.SERVER_VIEW,
            status,
        ),
        Command("players", "Who's playing right now", P.SERVER_VIEW, players),
        Command("ip", "How to join the Minecraft server", P.SERVER_VIEW, ip, slow=True),
        Command(
            "start",
            "Start the Minecraft server (if your Discord role may)",
            P.SERVER_CONTROL,
            start,
            slow=True,
        ),
        Command(
            "request-start",
            "Ask the owner and moderators to start the Minecraft server",
            P.SERVER_VIEW,
            request_start,
        ),
        Command(
            "link",
            "Link your Minecraft account to your Discord account",
            P.SERVER_VIEW,
            link,
            options=[NAME_OPTION],
        ),
        Command(
            "links", "Your linked Minecraft accounts", P.SERVER_VIEW, my_links, needs_server=False
        ),
        Command(
            "stats",
            "A player's stats: playtime, deaths, mobs, blocks, advancements",
            P.SERVER_VIEW,
            show_stats,
            options=[
                {
                    **NAME_OPTION,
                    "name": "player",
                    "description": "The Minecraft name (leave out for your own)",
                    "required": False,
                }
            ],
        ),
        Command(
            "level",
            "Your Level, your XP and your next Level role",
            P.SERVER_VIEW,
            show_level,
            needs_server=False,
            slow=True,
        ),
        Command(
            "unlink",
            "Remove one of your linked Minecraft accounts",
            P.SERVER_VIEW,
            unlink,
            needs_server=False,
            options=[{**NAME_OPTION, "description": "The Minecraft name to unlink"}],
        ),
    )
}

BUTTONS: dict[str, Button] = {
    b.action: b
    for b in (
        Button("kick", "Kick", P.PLAYERS_MANAGE, kick_or_ban, "Kick {name} from {server}?"),
        Button("ban", "Ban", P.PLAYERS_MANAGE, kick_or_ban, "Ban {name} from {server}?", 4),
        Button("restart", "Restart", P.SERVER_CONTROL, restart, "Restart {server} now?"),
        Button("backup", "Back up", P.BACKUPS_CREATE, backup, "Back up {server} now?"),
        Button("start_yes", "Start", P.SERVER_CONTROL, answer_request, style=3),
        Button("start_no", "Say no", P.SERVER_CONTROL, answer_request),
    )
}
