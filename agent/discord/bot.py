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
import json
import logging
import math
import re
import time
import urllib.parse
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from .. import levels
from ..config import PROJECT_ROOT
from ..events import OWNER_ONLY_EVENTS, Event
from ..game_events import ANSWERS
from ..jobs import JobConflict
from ..minecraft.chat import ChatError, relay
from ..minecraft.commands import CommandError
from ..minecraft.playeractions import DONE, SENT, PlayerActionError, check_name
from ..minecraft.process import ServerError
from ..quests import MAX_STACK, QuestError
from ..security import permissions as P
from ..security.auth import AuthError, Principal, RateLimiter
from ..startrequests import STOPPED, TOLD, StartRequest, StartRequestError
from ..stats import player_stats
from ..whitelist import WhitelistError, settled, settled_words
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
QUEST_BOARDS_KEY = "discord_quest_boards"  # quests channel id -> {"message", "words"}
CONSOLE_BATCH = 2.0  # seconds console lines are gathered before they're posted
CONSOLE_KEPT = 40  # console lines waiting per channel; older ones are left out
REPORT_LINES = 20  # chat lines in a report
REPORTS_AN_HOUR = 3  # per member
REPORT_LIFE = 30 * 86400.0
PRIVATE_THREAD = 12
# A quest's screenshot only comes from Discord's own image servers.
SCREENSHOT_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")
MAX_SCREENSHOT = 8 * 1024 * 1024


class Refused(Exception):
    """A command that can't go ahead, with the words to say why."""


# Refusals whose words are safe to show in Discord as they are.
SAYS_WHY = (
    Refused,
    LinkError,
    StartRequestError,
    PlayerActionError,
    JobConflict,
    QuestError,
    WhitelistError,
)


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
    # A pop-up form (Discord's modal) asks for these instead of command
    # options: text inputs, their values arriving as the options.
    form: list[dict] = field(default_factory=list)
    form_title: str = ""  # {server} filled in

    @property
    def group(self) -> str:
        """ "quest" for "quest join": Discord shows it as /quest join."""
        return self.name.split(" ")[0] if " " in self.name else ""


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
        # Event and poll posts: two quick presses can't leave older counts showing.
        self._posts_lock = asyncio.Lock()
        self._console_lock = asyncio.Lock()
        self._resync = False  # set when the Level roles changed: everyone's are set again
        # Each server's console channels, read from the database once a
        # minute and on a change, never per console line.
        self.consoles: dict[str, list[str]] = {}
        self._console_out: dict[str, list[str]] = {}  # channel id -> lines to post
        self._left_out: dict[str, int] = {}  # channel id -> lines dropped while busy
        core.start_requests.on_close = self.request_closed
        core.whitelist.on_answer = self.form_answered
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
        if self.core.whitelist.on_answer == self.form_answered:
            self.core.whitelist.on_answer = None
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
        self.gateway.intents = (
            INTENTS
            | (MESSAGE_INTENTS if on else 0)
            | (client.GUILD_MEMBERS if self.wants_members() else 0)
        )

    def wants_members(self) -> bool:
        """The Server Members switch is on and a Discord server takes people
        off the whitelist when they leave (only then is "left" asked for)."""
        options = self.core.db.get_setting(layout.OPTIONS_KEY, {}) or {}
        return bool(self.switches()["members"]) and any(
            o.get("remove_on_leave") for o in options.values()
        )

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
        elif name == "GUILD_MEMBER_REMOVE":
            self.spawn(self.member_left(data))

    # -- the status messages and the presence line ------------------------------
    async def _live_loop(self) -> None:
        """Once a minute: the status messages, Level roles and level-ups,
        and the weekly leaderboards. One failing doesn't stop the others."""
        await asyncio.sleep(LIVE_FIRST)
        while True:
            for work in (
                self.update_live,
                self.update_levels,
                self.post_leaderboards,
                self.update_quests,
                self.update_events,
            ):
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
        self.consoles = layout.consoles(self.core.db, set(self.guilds))
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

    async def _post_or_edit(
        self,
        channel_id: str,
        message_id: str | None,
        words: str,
        components: list[dict] | None = None,
    ) -> str:
        body: dict[str, Any] = {"content": words, "allowed_mentions": {"parse": []}}
        if components is not None:
            body["components"] = components
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
                        channel = layout.shared_channel(db, gid, "levels")
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
            and layout.shared_channel(self.core.db, guild_id, "levels")
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
            channel = layout.shared_channel(db, gid, "levels")
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

    # -- quests ------------------------------------------------------------------
    async def update_quests(self) -> None:
        """Everyone's quest progress, the Discord roles quests gave, then
        each Discord server's board, edited only when its words changed."""
        quests = self.core.quests
        await quests.check()
        await self.give_roles()
        db = self.core.db
        boards = db.get_setting(QUEST_BOARDS_KEY, {}) or {}
        changed = False
        for gid in list(self.guilds):
            channel = layout.shared_channel(db, gid, "quests")
            names = self.server_names(gid)
            if not channel or not names:
                continue
            words = progress.board([(names[sid], quests.board(sid)) for sid in sorted(names)])
            post = boards.get(channel) or {}
            if post.get("words") == words:
                continue
            boards[channel] = {
                "message": await self._post_or_edit(channel, post.get("message"), words),
                "words": words,
            }
            changed = True
        if changed:
            db.set_setting(QUEST_BOARDS_KEY, boards)

    async def reward_role(self, server_id: str, role_id: str) -> None:
        """A Discord role may be a quest reward: it's in a Discord server set
        up for this Minecraft server and gives no power in Discord."""
        db = self.core.db
        for gid in list(self.guilds):
            if server_id not in layout.servers_of(db, gid):
                continue
            roles = {r["id"]: r for r in await self.rest.request("GET", f"/guilds/{gid}/roles")}
            if role_id in roles:
                why = layout.level_role_refused(
                    gid, roles[role_id], layout.our_roles(db, gid), "a quest reward"
                )
                if why:
                    raise Refused(why)
                return
        raise Refused("That Discord role isn't in a Discord server set up for this server.")

    async def give_roles(self) -> None:
        """Discord roles owed for quests. Finishing a quest again stacks the
        role: "Dragon Slayer ×2" (same colour, made the first time it's
        needed) replaces "Dragon Slayer", up to ×10."""
        db, quests = self.core.db, self.core.quests
        owed = quests.rewards_everywhere("role")
        found: dict[str, dict[str, dict]] = {}  # guild -> its roles, read once
        for reward in owed:
            base = json.loads(reward["value"])["role_id"]
            gid = None
            for guild in list(self.guilds):
                if reward["server_id"] not in layout.servers_of(db, guild):
                    continue
                if guild not in found:
                    roles = await self.rest.request("GET", f"/guilds/{guild}/roles")
                    found[guild] = {r["id"]: r for r in roles}
                if base in found[guild]:
                    gid = guild
                    break
            if gid is None:
                quests.set_reward(reward["id"], "failed", "The Discord role isn't there any more.")
                continue
            ours = layout.our_roles(db, gid)
            why = layout.level_role_refused(gid, found[gid][base], ours, "a quest reward")
            if why:  # given powers in Discord since the quest was made
                quests.set_reward(reward["id"], "failed", why)
                continue
            times = (
                1  # an event's role doesn't stack
                if reward["event_id"]
                else min(quests.times_finished(reward["quest_id"], reward["discord_id"]), MAX_STACK)
            )
            try:
                wanted = await self._stack_role(gid, base, times, found[gid])
                held = await self.rest.request(
                    "GET", f"/guilds/{gid}/members/{reward['discord_id']}"
                )
                path = f"/guilds/{gid}/members/{reward['discord_id']}/roles"
                stack = {base, *(rid for k, rid in ours.items() if k.startswith(f"stack_{base}_"))}
                for role_id in set(held.get("roles") or []) & stack - {wanted}:
                    await self.rest.request("DELETE", f"{path}/{role_id}")
                await self.rest.request("PUT", f"{path}/{wanted}")
            except client.DiscordError as exc:
                if exc.status in (403, 404):
                    quests.set_reward(reward["id"], "failed", str(exc))
                # Anything else: Discord or the network, tried again next minute.
                log.info("couldn't give a quest's Discord role: %s", exc)
                continue
            quests.set_reward(reward["id"], "done")

    async def _stack_role(self, guild_id: str, base: str, times: int, roles: dict) -> str:
        if times <= 1:
            return base
        kind = f"stack_{base}_{times}"
        made = layout.our_roles(self.core.db, guild_id).get(kind)
        if made in roles:
            return str(made)
        role = roles[base]
        created = await self.rest.request(
            "POST",
            f"/guilds/{guild_id}/roles",
            {
                "name": f"{role['name']} ×{times}"[:100],
                "color": role.get("color") or 0,
                "permissions": "0",
                "mentionable": False,
            },
        )
        roles[created["id"]] = created
        self.core.db.execute(
            "INSERT INTO discord_roles (guild_id, kind, role_id) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, kind) DO UPDATE SET role_id = excluded.role_id",
            (guild_id, kind, created["id"]),
        )
        return str(created["id"])

    # -- events and polls -----------------------------------------------------------
    async def update_events(self) -> None:
        """Each event's and poll's message in the events channels (edited
        only when its words change), the reminder an hour before, and a
        poll's result when it closes."""
        async with self._posts_lock:
            found = self.core.game_events
            now = time.time()
            # recent() and polls() only list servers that are still there.
            servers = self.core.servers
            for event in found.recent():
                view = found.view(event)
                if view["state"] == "coming" and not event["reminded_at"]:
                    await self._remind(event, view, now)
                await self._show(
                    "game_events",
                    event,
                    progress.event_post(view, servers[event["server_id"]].name),
                    rsvp_buttons(view),
                    new=view["state"] in ("coming", "on"),
                )
            for poll in found.polls():
                view = found.poll_view(poll)
                await self._show(
                    "polls",
                    poll,
                    progress.poll_post(view, servers[poll["server_id"]].name),
                    vote_buttons(view),
                    None if view["open"] else progress.poll_result(view),
                    new=view["open"],
                )

    async def _show(
        self,
        table: str,
        row: dict[str, Any],
        words: str,
        buttons: list[dict],
        result: str | None = None,
        new: bool = True,
    ) -> None:
        """Post or edit one event's or poll's message in each events channel
        of the Discord servers set up for its server; ``result`` is posted
        once below it. With ``new`` False only an earlier message is edited
        (a Discord server set up later doesn't get last week's events)."""
        db = self.core.db
        posts = json.loads(row["posts"] or "{}")
        before = json.dumps(posts, sort_keys=True)
        for gid in list(self.guilds):
            channel = layout.shared_channel(db, gid, "events")
            if not channel or row["server_id"] not in layout.servers_of(db, gid):
                continue
            post = posts.get(channel) or {}
            if not post and not new:
                continue
            if post.get("words") != words:
                message = await self._post_or_edit(channel, post.get("message"), words, buttons)
                post = posts[channel] = {**post, "message": message, "words": words}
            if result and not post.get("result"):
                await self.rest.request(
                    "POST",
                    f"/channels/{channel}/messages",
                    {"content": result, "allowed_mentions": {"parse": []}},
                )
                post["result"] = True
        if json.dumps(posts, sort_keys=True) != before:
            db.execute(f"UPDATE {table} SET posts = ? WHERE id = ?", (json.dumps(posts), row["id"]))

    async def _remind(self, event: dict[str, Any], view: dict[str, Any], now: float) -> None:
        """An hour before: one message pinging only the members who said
        Going, or (the Discord server's choice) a private message to each;
        anyone who blocks those is pinged instead. An event made less than
        an hour ahead gets none."""
        from ..game_events import REMIND_BEFORE

        if now < event["starts_at"] - REMIND_BEFORE:
            return
        db = self.core.db
        if not db.execute(
            "UPDATE game_events SET reminded_at = ? WHERE id = ? AND reminded_at IS NULL",
            (now, event["id"]),
        ).rowcount:
            return
        going = [r["discord_id"] for r in view["going"]]
        if not going or event["created_at"] > event["starts_at"] - REMIND_BEFORE:
            return
        for gid in list(self.guilds):
            channel = layout.shared_channel(db, gid, "events")
            if not channel or event["server_id"] not in layout.servers_of(db, gid):
                continue
            pinged = going
            if layout.guild_options(db, gid)["reminders"] == "private":
                server = self.core.servers[event["server_id"]].name
                words = progress.private_reminder(event, server, self.guilds[gid]["name"])
                pinged = [m for m in going if not await self._private(m, words)]
            for i in range(0, len(pinged), 50):  # Discord pings at most 100 a message
                some = pinged[i : i + 50]
                await self.rest.request(
                    "POST",
                    f"/channels/{channel}/messages",
                    {
                        "content": progress.reminder(event, some),
                        "allowed_mentions": {"users": some},
                    },
                )

    async def _private(self, member_id: str, words: str) -> bool:
        """A private message; False when it couldn't be sent (the member
        blocks them, left, or Discord didn't answer): they're pinged then."""
        try:
            dm = await self.rest.request("POST", "/users/@me/channels", {"recipient_id": member_id})
            await self.rest.request(
                "POST",
                f"/channels/{dm['id']}/messages",
                {"content": words, "allowed_mentions": {"parse": []}},
            )
        except client.DiscordError as exc:
            log.info("couldn't send a private reminder: %s", exc)
            return False
        return True

    async def screenshot(self, attachment: dict) -> tuple[bytes, str]:
        """A screenshot sent with /quest done, from Discord's own image
        servers only, at most 8 MB, and only a real PNG or JPEG."""
        url = str(attachment.get("url") or "")
        where = urllib.parse.urlsplit(url)
        if where.scheme != "https" or where.hostname not in SCREENSHOT_HOSTS:
            raise Refused("That picture didn't come from Discord.")
        if int(attachment.get("size") or 0) > MAX_SCREENSHOT:
            raise Refused("The screenshot is too big: 8 MB at most.")
        data = bytearray()
        async with httpx.AsyncClient(transport=client.TRANSPORT, timeout=20) as http:
            async with http.stream("GET", url) as answer:
                if answer.status_code != 200:
                    raise Refused("Discord didn't hand over the screenshot. Try again.")
                async for chunk in answer.aiter_bytes():
                    data += chunk
                    if len(data) > MAX_SCREENSHOT:
                        raise Refused("The screenshot is too big: 8 MB at most.")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return bytes(data), "png"
        if data.startswith(b"\xff\xd8\xff"):
            return bytes(data), "jpg"
        raise Refused("The screenshot has to be a PNG or JPEG picture.")

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
                request.posts.append([channel_id, str(made["id"])])
        if request is not None:
            self.core.start_requests.save()
        return sent

    async def request_closed(self, request: StartRequest, how: str) -> None:
        """A start request ended: the asker hears it where they asked, and
        its alerts lose their buttons and say how."""
        reply = request.reply or {}
        if reply.get("channel_id") and reply.get("member_id"):
            ctx = self.core.servers.get(request.server_id)
            told = TOLD[how].format(server=ctx.name if ctx else request.server_id)
            self.spawn(self.tell(reply["channel_id"], reply["member_id"], told))
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
        channels, gathered for a moment so a busy chat is one post; console
        lines to its console channels."""
        if self.gateway.state != "connected" or event.server_id not in self.core.servers:
            return
        if event.type == "console":
            self._to_console(event)
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

    # -- the console channel ---------------------------------------------------------
    def _to_console(self, event: Event) -> None:
        """Read only: nothing typed in a console channel goes anywhere."""
        channels = self.consoles.get(event.server_id or "")
        if not channels:
            return
        line = bridge.console_line(str((event.data or {}).get("raw") or event.message))
        if line is None:
            return
        for channel_id in channels:
            waiting = self._console_out.setdefault(channel_id, [])
            waiting.append(line)
            if len(waiting) > CONSOLE_KEPT:
                del waiting[0]
                self._left_out[channel_id] = self._left_out.get(channel_id, 0) + 1
            if len(waiting) == 1:
                self.spawn(self._post_console(channel_id))

    async def _post_console(self, channel_id: str) -> None:
        await asyncio.sleep(CONSOLE_BATCH)
        async with self._console_lock:  # posts stay in the console's order
            lines = self._console_out.pop(channel_id, [])
            left_out = self._left_out.pop(channel_id, 0)
            for words in bridge.console_posts(lines, left_out):
                await self.rest.request(
                    "POST",
                    f"/channels/{channel_id}/messages",
                    {"content": words, "allowed_mentions": {"parse": []}},
                )

    # -- the whitelist ------------------------------------------------------------------
    async def member_left(self, data: dict) -> None:
        """Someone left a Discord server that takes people off the whitelist
        when they leave: each of their linked accounts on its Minecraft
        servers goes (once the server is online, if it's stopped)."""
        gid = str(data.get("guild_id") or "")
        member = str((data.get("user") or {}).get("id") or "")
        db = self.core.db
        if gid not in self.guilds or not layout.guild_options(db, gid)["remove_on_leave"]:
            return
        servers = set(layout.servers_of(db, gid))
        for row in self.core.links.of_member(member):
            ctx = self.core.servers.get(row["server_id"])
            if ctx is None or row["server_id"] not in servers:
                continue
            try:
                await self.core.whitelist.remove(
                    ctx, row["name"], f"{row['discord_name']} (left Discord)"
                )
            except PlayerActionError as exc:
                log.info("couldn't take someone who left Discord off the whitelist: %s", exc)

    async def form_answered(self, ctx: ServerContext, form: dict, pending: Any) -> None:
        """A whitelist request was answered (here or in the app): the asker
        hears it in the channel they asked in, once the server answered."""
        if form.get("channel_id"):
            self.spawn(self._tell_form(ctx, form, pending))

    async def _tell_form(self, ctx: ServerContext, form: dict, pending: Any) -> None:
        if pending is None:
            words = f"your request to join {ctx.name} was turned down."
        else:
            words = settled_words(ctx, form["name"], await settled(pending))
        await self.tell(str(form["channel_id"]), str(form["discord_id"]), words)

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
        self.consoles = layout.consoles(self.core.db, set(self.guilds))
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
                    "options": layout.guild_options(self.core.db, guild["id"]),
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
                if problem["id"] in (
                    "manage_roles",
                    "role_too_low",
                    "channel_blocked",
                    "console_public",
                ):
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
        """A slash command (type 2), a button press (type 3) or a filled-in
        pop-up form (type 5): rate limit, pick the server, check the
        permission like a route, run, answer, audit."""
        kind = data.get("type")
        body = data.get("data") or {}
        member = data.get("member") or {}
        user = member.get("user") or {}
        guild_id = data.get("guild_id")
        if kind not in (2, 3, 5) or not guild_id or not user.get("id"):
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
        form: Command | None = None  # a command whose pop-up form opens first
        if kind == 5:  # "form:<command>:<server>", as the form was opened
            _, wanted_command, wanted = (str(body.get("custom_id")).split(":", 2) + ["", ""])[:3]
            command = COMMANDS.get(wanted_command)
            if command is None or not command.form:
                return
            call.options = {**form_values(body), "server": wanted}
            name, permission, handler = f"/{command.name}", command.permission, command.handler
            needs_server, slow = True, command.slow
        elif kind == 2:
            name = str(body.get("name"))
            if name in GROUPS:  # "/quest join": the part after the group is an option
                sub: dict = next(iter(body.get("options") or []), {})
                name = f"{name} {sub.get('name')}"
                call.options = {
                    o["name"]: o.get("value") for o in sub.get("options") or [] if "name" in o
                }
            command = COMMANDS.get(name)
            if command is None:
                return
            name, permission, handler = f"/{command.name}", command.permission, command.handler
            needs_server, slow = command.needs_server, command.slow
            form = command if command.form else None
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
            if form and call.ctx is not None:
                await self._callback(data, {"type": 9, "data": form_of(form, call.ctx)})
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
    out: list[dict] = []
    for command in COMMANDS.values():
        options = list(command.options)
        if command.needs_server and server["choices"]:
            options.append(server)
        entry = {
            "name": command.name.split(" ")[-1],
            "description": command.description,
            "type": 1,
            "options": options,
        }
        if not command.group:
            out.append(entry)
            continue
        group = next((c for c in out if c["name"] == command.group), None)
        if group is None:
            group = {
                "name": command.group,
                "description": GROUPS[command.group],
                "type": 1,
                "options": [],
            }
            out.append(group)
        group["options"].append(entry)  # a subcommand: an option of type 1
    return out


def form_of(command: Command, ctx: ServerContext) -> dict:
    """A command's pop-up form, tied to the server it was opened for."""
    return {
        "custom_id": f"form:{command.name}:{ctx.server_id}",
        "title": command.form_title.format(server=ctx.name)[:45],
        "components": [{"type": 1, "components": [box]} for box in command.form],
    }


def form_values(body: dict) -> dict[str, str]:
    """What was typed in a pop-up form, by field. Only text, and the same
    checks as the command's options run on it afterwards."""
    out: dict[str, str] = {}
    for row in body.get("components") or []:
        for box in row.get("components") or [row.get("component") or {}]:
            if box.get("custom_id") and isinstance(box.get("value"), str):
                out[str(box["custom_id"])] = box["value"]
    return out


BUTTON_ARGS = ("username", "claim_id", "form_id", "address_id", "report_id")


def alert_buttons(event: Event, ctx: ServerContext) -> list[dict]:
    """The buttons under an alert in the alerts channel."""
    data = event.data or {}
    # The button's argument: a player, or the claim, request, address or report.
    name = str(
        next((data[k] for k in BUTTON_ARGS if data.get(k)), "")  # first one the alert has
    )
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
        "quest_claim": ("claim_yes", "claim_no"),
        "whitelist_form": ("form_yes", "form_no"),
        "new_address": ("address_allow", "address_ignore"),
        "player_report": ("report_see",),
    }.get(event.type, ())
    if not ctx.config.server_type.bans:
        wanted = tuple(a for a in wanted if a != "ban")
    if event.type.startswith("player_") and not name:
        wanted = ()
    if event.type == "new_address":
        name = str(data.get("address_id") or "")
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


def rsvp_buttons(event: dict[str, Any]) -> list[dict]:
    """Going / Maybe / Can't, until the event ends or is called off."""
    if event["state"] not in ("coming", "on"):
        return []
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": BUTTONS[f"rsvp_{a}"].style,
                    "label": BUTTONS[f"rsvp_{a}"].label,
                    "custom_id": f"rsvp_{a}:{event['server_id']}:{event['id']}",
                }
                for a in ANSWERS
            ],
        }
    ]


def vote_buttons(poll: dict[str, Any]) -> list[dict]:
    """One button per answer while the poll is open."""
    if not poll["open"]:
        return []
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 1,
                    "label": answer[:80],
                    "custom_id": f"vote:{poll['server_id']}:{poll['id']}.{i}",
                }
                for i, answer in enumerate(poll["answers"])
            ],
        }
    ]


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
    reply = (
        {"channel_id": call.channel_id, "member_id": call.member_id} if call.channel_id else None
    )
    await bot.core.start_requests.ask(ctx, f"{call.member_name} (Discord)", reply)
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
    await settled(pending, 5)
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


# -- quests ---------------------------------------------------------------------------
async def quest_join(bot: Bot, call: Call) -> str:
    assert call.ctx is not None
    quest = await bot.core.quests.join(call.ctx, call.options.get("quest"), call.member_id)
    return (
        f"You're on quest {quest['id']}, {quest['title']}: {progress.task(bot.core.quests.view(quest))}. "
        "It counts from now. /quest progress shows how far you are."
    )


async def quest_progress(bot: Bot, call: Call) -> str:
    assert call.ctx is not None
    found = bot.core.quests.of_member(call.ctx.server_id, call.member_id)
    wanted = call.options.get("quest")
    if wanted is not None:
        found = [q for q in found if str(q["id"]) == str(wanted)]
    return progress.my_quests(found, call.ctx.name)


async def quest_done(bot: Bot, call: Call) -> str:
    assert call.ctx is not None
    picture = None
    wanted = call.options.get("screenshot")
    if wanted:
        attachments = ((call.interaction.get("data") or {}).get("resolved") or {}).get(
            "attachments"
        ) or {}
        if str(wanted) not in attachments:
            raise Refused("That screenshot didn't come through. Try again.")
        picture = await bot.screenshot(attachments[str(wanted)])
    return await bot.core.quests.done(
        call.ctx,
        call.options.get("quest"),
        call.member_id,
        call.member_name,
        str(call.options.get("note") or ""),
        call.channel_id,
        picture,
    )


async def quest_suggest(bot: Bot, call: Call) -> str:
    assert call.ctx is not None
    bot.core.quests.suggest(
        call.ctx,
        call.member_id,
        call.member_name,
        str(call.options.get("title") or ""),
        str(call.options.get("details") or ""),
        call.channel_id,
    )
    await call.ctx.bus.publish(
        Event(
            type="quest_suggested",
            message=f"{call.member_name} suggested a quest on Discord",
            data={"discord_name": call.member_name, "title": call.options.get("title")},
        )
    )
    return "Thanks! The owner and moderators will see your idea in Command Block."


async def quest_create(bot: Bot, call: Call) -> str:
    """/quest create, for Discord roles given "Quests and events": the same
    checks as the apps, rewards only from the templates."""
    ctx = call.ctx
    assert ctx is not None
    o = call.options
    rewards = await rewards_from(bot, ctx.server_id, o)
    quest = await bot.core.quests.make(
        ctx,
        {
            "title": o.get("title"),
            "details": o.get("details"),
            "kind": o.get("kind"),
            "goal": o.get("goal"),
            "count": o.get("count"),
            "repeat": o.get("repeat"),
            "ends": o.get("ends"),
            "rewards": rewards,
        },
        call.principal.user,
    )
    return f"Made {progress.quest_line(bot.core.quests.view(quest))}. It's on the board within a minute."


async def rewards_from(bot: Bot, server_id: str, o: dict[str, Any]) -> list[dict[str, Any]]:
    """The reward options of /quest create and /event create, as templates."""
    rewards: list[dict[str, Any]] = []
    if o.get("role"):
        await bot.reward_role(server_id, str(o["role"]))
        rewards.append({"kind": "role", "role_id": str(o["role"])})
    if o.get("item"):
        rewards.append({"kind": "item", "item": o["item"], "count": o.get("item_count") or 1})
    if o.get("xp_levels"):
        rewards.append({"kind": "xp", "levels": o["xp_levels"]})
    if o.get("level_xp"):
        rewards.append({"kind": "level_xp", "amount": o["level_xp"]})
    return rewards


async def answer_claim(bot: Bot, call: Call) -> str:
    ctx = call.ctx
    assert ctx is not None
    yes = str(call.interaction["data"]["custom_id"]).removeprefix("sure:").startswith("claim_yes")
    claim = await bot.core.quests.answer_claim(ctx, call.arg, yes, call.principal.user)
    return f"{'Approved' if yes else 'Refused'}: {claim['discord_name']}, {claim['title']}."


# -- events and polls -------------------------------------------------------------------
async def event_create(bot: Bot, call: Call) -> str:
    """/event create: the same checks as the apps; the start is typed on the
    PC's clock."""
    from ..game_events import parse_time

    ctx = call.ctx
    assert ctx is not None
    o = call.options
    starts = parse_time(o.get("start"))
    made = await bot.core.game_events.make(
        ctx,
        {
            "title": o.get("title"),
            "details": o.get("details"),
            "kind": o.get("kind"),
            "goal": o.get("goal"),
            "starts_at": starts,
            "ends_at": starts + 60 * int(o.get("minutes") or 60),
            "start_server": bool(o.get("start_server")),
            "rewards": await rewards_from(bot, ctx.server_id, o),
        },
        call.principal.user,
    )
    bot.spawn(bot.update_events())
    return f"Made the event {made['title']}, <t:{int(starts)}:F>. It's in the events channel now."


async def poll_create(bot: Bot, call: Call) -> str:
    ctx = call.ctx
    assert ctx is not None
    o = call.options
    made = await bot.core.game_events.make_poll(
        ctx,
        {
            "question": o.get("question"),
            "answers": [o.get(f"answer{i}") for i in range(1, 6)],
            "closes_at": time.time() + 3600 * int(o.get("hours") or 24),
        },
        call.principal.user,
    )
    bot.spawn(bot.update_events())
    return f"Made the poll {made['question']}. It's in the events channel now."


def _button_id(call: Call) -> str:
    return str(call.interaction["data"]["custom_id"]).split(":", 1)[0]


async def rsvp(bot: Bot, call: Call) -> str:
    assert call.ctx is not None
    answer = _button_id(call).removeprefix("rsvp_")
    words = bot.core.game_events.rsvp(
        call.ctx.server_id, call.arg, call.member_id, call.member_name, answer
    )
    bot.spawn(bot.update_events())
    return words


async def vote(bot: Bot, call: Call) -> str:
    assert call.ctx is not None
    poll_id, _, answer = call.arg.partition(".")
    words = bot.core.game_events.vote(call.ctx.server_id, poll_id, call.member_id, answer)
    bot.spawn(bot.update_events())
    return words


# -- the whitelist and reports --------------------------------------------------------
async def whitelist_add(bot: Bot, call: Call) -> str:
    """/whitelist add, for Discord roles with Players: "Sent" until the
    server's console says it's done (the add-on's command where one is used)."""
    from ..whitelist import online_mode

    ctx = call.ctx
    assert ctx is not None
    call.arg = str(call.options.get("name") or "").strip()
    pending = await ctx.player_actions.send("whitelist_add", call.arg)
    await bot.rest.request(
        "PATCH",
        bot.original(call.interaction),
        {"content": f"Sent to {ctx.name}; waiting for the server to confirm it."},
    )
    words = settled_words(ctx, pending.name, await settled(pending))
    if online_mode(ctx) is False:
        words += (
            f"\n{ctx.name} doesn't check accounts (offline mode), so anyone can join "
            "with a name that's on the whitelist."
        )
    return words


async def whitelist_ask(bot: Bot, call: Call) -> str:
    """/whitelist ask: a request the owner and moderators answer."""
    ctx = call.ctx
    assert ctx is not None
    form = bot.core.whitelist.ask(
        ctx,
        str(call.options.get("name") or ""),
        str(call.options.get("why") or ""),
        call.member_id,
        call.member_name,
        call.channel_id,
    )
    call.arg = form["name"]
    await ctx.bus.publish(
        Event(
            type="whitelist_form",
            message=f"{call.member_name} on Discord asks to join as {form['name']}: {form['why']}",
            data={
                "form_id": form["id"],
                "name": form["name"],
                "discord_name": call.member_name,
                "why": form["why"],
            },
        )
    )
    return (
        f"Sent. The owner and moderators of {ctx.name} will see your request; "
        "you'll hear here when they answer."
    )


async def answer_form(bot: Bot, call: Call) -> str:
    ctx = call.ctx
    assert ctx is not None
    form, pending = await bot.core.whitelist.answer(ctx, call.arg, _button_id(call) == "form_yes")
    if pending is None:
        return f"Turned down: {form['name']}."
    return settled_words(ctx, form["name"], await settled(pending))


async def answer_address(bot: Bot, call: Call) -> str:
    ctx = call.ctx
    assert ctx is not None
    found = bot.core.whitelist
    if _button_id(call) == "address_ignore":
        found.ignore(ctx, call.arg, call.principal.user)
        return "Left as it was. Nobody will be asked about that address again."
    pending = await settled(await found.allow(ctx, call.arg, call.principal.user))
    if pending.state == DONE:
        return f"Allowed: {pending.name} can join {ctx.name} from the new address."
    if pending.state == SENT:
        return f"Sent to {ctx.name}; the add-on hasn't confirmed it yet."
    said = pending.to_dict()["message"]  # with the address masked
    return f"{ctx.name} answered: {said or 'it did nothing.'}"


async def report(bot: Bot, call: Call) -> str:
    """/report: a private thread off the alerts channel with the reason and
    the last 20 chat lines. The reporter is in it; the owner and moderators
    get an alert whose "See the report" button adds them."""
    from ..whitelist import one_line

    ctx = call.ctx
    assert ctx is not None
    try:
        player = check_name(str(call.options.get("player") or ""), ctx.config.server_type.dialect)
    except PlayerActionError as exc:
        raise Refused(str(exc)) from None
    reason = one_line(call.options.get("reason"), 300)
    if not reason:
        raise Refused("Say what happened.")
    db, now = bot.core.db, time.time()
    db.execute("DELETE FROM discord_reports WHERE created_at < ?", (now - REPORT_LIFE,))
    recent = db.query_one(
        "SELECT COUNT(*) AS n FROM discord_reports WHERE reporter_id = ? AND created_at > ?",
        (call.member_id, now - 3600),
    )
    if recent and recent["n"] >= REPORTS_AN_HOUR:
        raise Refused(f"You've sent {REPORTS_AN_HOUR} reports in the last hour. Try later.")
    parent = next(iter(layout.alert_channels(db, ctx.server_id, [call.guild_id])), None)
    if parent is None:
        raise Refused(
            f"There's no alerts channel for {ctx.name} here yet, so there's nowhere private "
            "to put a report. Ask the owner to pick one."
        )
    thread = await bot.rest.request(
        "POST",
        f"/channels/{parent}/threads",
        {
            "name": f"Report: {player}"[:100],
            "type": PRIVATE_THREAD,
            "invitable": False,
            "auto_archive_duration": 10080,
        },
    )
    thread_id = str(thread["id"])
    await bot.rest.request("PUT", f"/channels/{thread_id}/thread-members/{call.member_id}")
    lines = ctx.chat.tail(REPORT_LINES) if ctx.config.server_type.reads_chat else None
    await bot.rest.request(
        "POST",
        f"/channels/{thread_id}/messages",
        {
            "content": progress.report(ctx.name, player, call.member_id, reason, lines),
            "allowed_mentions": {"parse": []},
        },
    )
    made = db.execute(
        "INSERT INTO discord_reports (server_id, guild_id, thread_id, reporter_id, player, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (ctx.server_id, call.guild_id, thread_id, call.member_id, player, now),
    ).lastrowid
    call.arg = player
    await ctx.bus.publish(
        Event(
            type="player_report",
            level="warn",
            message=f"{call.member_name} reported {player} on Discord: {reason}",
            data={"report_id": made, "player": player, "reason": reason},
        )
    )
    return f"Thanks. Your report is in a private thread for you and the moderators: <#{thread_id}>"


async def see_report(bot: Bot, call: Call) -> str:
    """Adds whoever pressed it (with Players on that server) to the thread."""
    ctx = call.ctx
    assert ctx is not None
    try:
        wanted = int(call.arg)
    except ValueError:
        wanted = 0
    found = bot.core.db.query_one(
        "SELECT * FROM discord_reports WHERE id = ? AND server_id = ?", (wanted, ctx.server_id)
    )
    if found is None:
        raise Refused("That report isn't there any more (reports are kept 30 days).")
    if found["guild_id"] != call.guild_id:
        raise Refused("That report is in another Discord server.")
    await bot.rest.request("PUT", f"/channels/{found['thread_id']}/thread-members/{call.member_id}")
    return f"Added you to the report: <#{found['thread_id']}>"


# The quest kinds for /quest create (Discord's choices: name shown, value sent).
KIND_CHOICES = [
    {"name": "A stat count (goal: mined:diamond_ore)", "value": "stat"},
    {"name": "An advancement (goal: end/kill_dragon)", "value": "advancement"},
    {"name": "Hours played", "value": "playtime"},
    {"name": "Joins", "value": "joins"},
    {"name": "Bring items (goal: diamond)", "value": "items"},
    {"name": "Needs approval", "value": "approval"},
]
QUEST_OPTION = {
    "type": 4,
    "name": "quest",
    "description": "The quest's number on the board",
    "required": True,
    "min_value": 1,
}
GROUPS = {
    "quest": "Quests: join one, see your progress, say you're done",
    "event": "Events: make one (if your Discord role may)",
    "poll": "Polls: make one (if your Discord role may)",
    "whitelist": "Whitelist: add a player (if your Discord role may), or ask to join",
}
EVENT_KINDS = [
    {"name": "Online 15 minutes during it", "value": "online"},
    {"name": "Earn an advancement during it (goal: end/kill_dragon)", "value": "advancement"},
    {"name": "A moderator says who came", "value": "approval"},
]

REWARD_OPTIONS = [
    {"type": 8, "name": "role", "description": "Reward: a Discord role"},
    {
        "type": 3,
        "name": "item",
        "description": "Reward: an item, like diamond",
        "max_length": 100,
    },
    {
        "type": 4,
        "name": "item_count",
        "description": "How many of the item (1-64)",
        "min_value": 1,
        "max_value": 64,
    },
    {
        "type": 4,
        "name": "xp_levels",
        "description": "Reward: Minecraft XP levels (1-30)",
        "min_value": 1,
        "max_value": 30,
    },
    {
        "type": 4,
        "name": "level_xp",
        "description": "Reward: Level XP (1-500)",
        "min_value": 1,
        "max_value": 500,
    },
]

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
        Command("quest join", "Join a quest", P.SERVER_VIEW, quest_join, options=[QUEST_OPTION]),
        Command(
            "quest progress",
            "How far you are on your quests",
            P.SERVER_VIEW,
            quest_progress,
            options=[{**QUEST_OPTION, "required": False}],
        ),
        Command(
            "quest done",
            "Say you've done a quest (items are counted, never taken)",
            P.SERVER_VIEW,
            quest_done,
            slow=True,
            options=[
                QUEST_OPTION,
                {"type": 3, "name": "note", "description": "What you did", "max_length": 300},
                {"type": 11, "name": "screenshot", "description": "A screenshot, if it helps"},
            ],
        ),
        Command(
            "quest suggest",
            "Suggest a quest to the owner and moderators",
            P.SERVER_VIEW,
            quest_suggest,
            options=[
                {
                    "type": 3,
                    "name": "title",
                    "description": "The quest",
                    "required": True,
                    "max_length": 80,
                },
                {"type": 3, "name": "details", "description": "More about it", "max_length": 300},
            ],
        ),
        Command(
            "quest create",
            "Make a quest (if your Discord role may)",
            P.QUESTS_MANAGE,
            quest_create,
            slow=True,
            options=[
                {
                    "type": 3,
                    "name": "title",
                    "description": "The quest",
                    "required": True,
                    "max_length": 80,
                },
                {
                    "type": 3,
                    "name": "kind",
                    "description": "What it asks for",
                    "required": True,
                    "choices": KIND_CHOICES,
                },
                {
                    "type": 3,
                    "name": "goal",
                    "description": "The stat, advancement or item",
                    "max_length": 100,
                },
                {
                    "type": 4,
                    "name": "count",
                    "description": "How many (hours for playtime)",
                    "min_value": 1,
                },
                {
                    "type": 3,
                    "name": "repeat",
                    "description": "How often it comes back (00:00 on the PC's clock)",
                    "choices": [
                        {"name": "Once", "value": "once"},
                        {"name": "Every day", "value": "daily"},
                        {"name": "Every week (Monday)", "value": "weekly"},
                        {"name": "Every month", "value": "monthly"},
                        {"name": "Every year", "value": "yearly"},
                    ],
                },
                {
                    "type": 3,
                    "name": "ends",
                    "description": "Last day, like 2026-12-31",
                    "max_length": 10,
                },
                {"type": 3, "name": "details", "description": "More about it", "max_length": 300},
                *REWARD_OPTIONS,
            ],
        ),
        Command(
            "event create",
            "Make an event (if your Discord role may)",
            P.QUESTS_MANAGE,
            event_create,
            slow=True,
            options=[
                {
                    "type": 3,
                    "name": "title",
                    "description": "The event",
                    "required": True,
                    "max_length": 80,
                },
                {
                    "type": 3,
                    "name": "start",
                    "description": "When, on the PC's clock, like 2026-10-30 20:00",
                    "required": True,
                    "max_length": 16,
                },
                {
                    "type": 4,
                    "name": "minutes",
                    "description": "How long (15 to 1440 minutes, 60 if left out)",
                    "min_value": 15,
                    "max_value": 1440,
                },
                {
                    "type": 3,
                    "name": "kind",
                    "description": "How attendance is counted",
                    "choices": EVENT_KINDS,
                },
                {
                    "type": 3,
                    "name": "goal",
                    "description": "The advancement, for that kind",
                    "max_length": 100,
                },
                {
                    "type": 5,
                    "name": "start_server",
                    "description": "Start the server 10 minutes before",
                },
                {"type": 3, "name": "details", "description": "More about it", "max_length": 300},
                *REWARD_OPTIONS,
            ],
        ),
        Command(
            "poll create",
            "Make a poll (if your Discord role may)",
            P.QUESTS_MANAGE,
            poll_create,
            options=[
                {
                    "type": 3,
                    "name": "question",
                    "description": "The question",
                    "required": True,
                    "max_length": 150,
                },
                *(
                    {
                        "type": 3,
                        "name": f"answer{i}",
                        "description": f"Answer {i}",
                        "required": i <= 2,
                        "max_length": 55,
                    }
                    for i in range(1, 6)
                ),
                {
                    "type": 4,
                    "name": "hours",
                    "description": "Open for how many hours (1 to 168, 24 if left out)",
                    "min_value": 1,
                    "max_value": 168,
                },
            ],
        ),
        Command(
            "whitelist add",
            "Put a player on the whitelist (if your Discord role may)",
            P.PLAYERS_MANAGE,
            whitelist_add,
            slow=True,
            options=[{**NAME_OPTION, "description": "Their Minecraft name", "max_length": 20}],
        ),
        Command(
            "whitelist ask",
            "Ask the owner and moderators to let you in",
            P.SERVER_VIEW,
            whitelist_ask,
            form=[
                {
                    "type": 4,
                    "custom_id": "name",
                    "label": "Your Minecraft name",
                    "style": 1,
                    "min_length": 1,
                    "max_length": 20,
                    "required": True,
                },
                {
                    "type": 4,
                    "custom_id": "why",
                    "label": "Why you'd like to join",
                    "style": 2,
                    "min_length": 1,
                    "max_length": 300,
                    "required": True,
                },
            ],
            form_title="Ask to join {server}",
        ),
        Command(
            "report",
            "Report a player to the owner and moderators, privately",
            P.SERVER_VIEW,
            report,
            slow=True,
            options=[
                {
                    **NAME_OPTION,
                    "name": "player",
                    "description": "Who you're reporting",
                    "max_length": 20,
                },
                {
                    "type": 3,
                    "name": "reason",
                    "description": "What happened",
                    "required": True,
                    "max_length": 300,
                },
            ],
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
        Button("claim_yes", "Approve", P.QUESTS_MANAGE, answer_claim, style=3),
        Button("claim_no", "Refuse", P.QUESTS_MANAGE, answer_claim),
        Button("rsvp_going", "Going", P.SERVER_VIEW, rsvp, style=3),
        Button("rsvp_maybe", "Maybe", P.SERVER_VIEW, rsvp),
        Button("rsvp_no", "Can't", P.SERVER_VIEW, rsvp),
        Button("vote", "Vote", P.SERVER_VIEW, vote),
        Button("form_yes", "Approve", P.PLAYERS_MANAGE, answer_form, style=3),
        Button("form_no", "Say no", P.PLAYERS_MANAGE, answer_form),
        Button("address_allow", "Allow", P.PLAYERS_MANAGE, answer_address, style=3),
        Button("address_ignore", "Ignore", P.PLAYERS_MANAGE, answer_address),
        Button("report_see", "See the report", P.PLAYERS_MANAGE, see_report, style=1),
    )
}
