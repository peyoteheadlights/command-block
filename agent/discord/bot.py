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
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..config import PROJECT_ROOT
from ..events import Event
from ..security import permissions as P
from ..security.auth import AuthError, Principal, RateLimiter
from . import client, layout
from .links import CODE_LIFE, LinkError

if TYPE_CHECKING:
    from ..core import AgentCore, ServerContext

log = logging.getLogger("msc.discord")

DEFAULT_NAME = "Command Block"
LOGO = PROJECT_ROOT / "desktop" / "images" / "icon-192.png"
# Only guild events: the bot needs no privileged switch for what it does so
# far. The chat bridge (PR 4) adds the message intents.
INTENTS = client.GUILDS
EPHEMERAL = 64
# Minecraft names as the server prints them; Floodgate's "." is kept.
NAME_RE = re.compile(r"^\.?[A-Za-z0-9_]{1,16}$")
PROFILE_KEY = "discord_profile"


class Refused(Exception):
    """A command that can't go ahead, with the words to say why."""


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
        self.gateway = client.Gateway(self.rest, token, INTENTS, self._event)
        self.user: dict = {}
        self.app: dict = {}  # the application, with its switches (flags)
        self.guilds: dict[str, dict] = {}  # id -> {"id", "name"}
        self.reports: dict[str, dict] = {}  # id -> layout.inspect's answer
        # Per member, like the sign-in limiter: a burst is fine, a flood isn't.
        self.rate = RateLimiter(8, 60.0)
        self._task: asyncio.Task | None = None
        self._jobs: set[asyncio.Task] = set()

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
        for task in [self._task, *self._jobs]:
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

    async def _event(self, name: str, data: dict) -> None:
        if name == "READY":
            self.user = data.get("user") or {}
            self.app = {"id": str((data.get("application") or {}).get("id", ""))}
            self.spawn(self._after_ready())
        elif name in ("GUILD_CREATE", "GUILD_UPDATE"):
            self.guilds[data["id"]] = {"id": data["id"], "name": data.get("name", "")}
            if name == "GUILD_CREATE":
                self.spawn(self.refresh(data["id"]))
        elif name == "GUILD_DELETE" and not data.get("unavailable"):
            self.guilds.pop(data["id"], None)
            self.reports.pop(data["id"], None)
        elif name == "INTERACTION_CREATE":
            self.spawn(self.interaction(data))

    async def _after_ready(self) -> None:
        self.app = await self.rest.request("GET", "/applications/@me")
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
            "guilds": [
                {
                    **guild,
                    "servers": layout.servers_of(self.core.db, guild["id"]),
                    "grants": layout.grants(self.core.db, guild["id"]),
                    "report": self.reports.get(guild["id"]),
                }
                for guild in sorted(self.guilds.values(), key=lambda g: g["name"].lower())
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
        if data.get("type") != 2:  # only slash commands so far
            return
        command = COMMANDS.get(str((data.get("data") or {}).get("name")))
        member = data.get("member") or {}
        user = member.get("user") or {}
        guild_id = data.get("guild_id")
        if command is None or not guild_id or not user.get("id"):
            return
        call = Call(
            guild_id=str(guild_id),
            channel_id=data.get("channel_id"),
            user=user,
            principal=layout.member_principal(
                self.core.db, str(guild_id), user, [str(r) for r in member.get("roles") or []]
            ),
            options={
                o["name"]: o.get("value")
                for o in (data["data"].get("options") or [])
                if "name" in o
            },
            interaction=data,
        )
        result = "ok"
        try:
            self.rate.check(f"discord:{call.member_id}")
            server_id = self.pick_server(call) if command.needs_server else None
            P.check(call.principal, command.permission, server_id)
            if server_id:
                call.ctx = self.core.servers[server_id]
            words = await command.handler(self, call)
        except AuthError as exc:
            result = "refused"
            words = (
                f"Slow down a little: try again in {exc.retry_after} s."
                if exc.status == 429
                else "Your Discord roles don't allow that on this server."
            )
        except (Refused, LinkError) as exc:
            result = "refused"
            words = str(exc)
        except Exception:
            log.exception("/%s failed", command.name)
            result = "failed"
            words = "Something went wrong on the PC. The owner can find it in the agent's log."
        await self.reply(data, words)
        self.core.db.audit(
            "discord_command",
            user=call.principal.user,
            target=f"/{command.name}",
            result=result,
            detail=f"Discord member {call.member_id}",
            server_id=call.ctx.server_id if call.ctx else None,
        )

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

    async def reply(self, interaction: dict, words: str, ephemeral: bool = True) -> None:
        await self.rest.request(
            "POST",
            f"/interactions/{interaction['id']}/{interaction['token']}/callback",
            {
                "type": 4,
                "data": {
                    "content": words[:2000],
                    "flags": EPHEMERAL if ephemeral else 0,
                    "allowed_mentions": {"parse": []},
                },
            },
        )

    def edit_reply(self, interaction: dict) -> Callable[[str], None]:
        """Changes the first reply later (within the 15 minutes Discord allows)."""
        path = f"/webhooks/{self.app.get('id')}/{interaction['token']}/messages/@original"

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


# -- the commands -------------------------------------------------------------------
def _duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min"


async def status(bot: Bot, call: Call) -> str:
    """Only measured facts; anything not known says Unknown."""
    assert call.ctx is not None
    s = call.ctx.summary()
    name = s["name"]
    if s["state"] != "ONLINE":
        state = {"OFFLINE": "offline", "STARTING": "starting", "STOPPING": "stopping"}.get(
            s["state"], s["state"].lower()
        )
        return f"{name} is {state}."
    players = s["players_online"]
    playing = f"{players}/{s['max_players']} playing" if players is not None else "players: Unknown"
    version = s["minecraft_version"] or "version Unknown"
    uptime = f"up {_duration(s['uptime'])}" if s.get("uptime") else "uptime Unknown"
    return f"{name} is online. {playing}, {version}, {uptime}."


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
            "unlink",
            "Remove one of your linked Minecraft accounts",
            P.SERVER_VIEW,
            unlink,
            needs_server=False,
            options=[{**NAME_OPTION, "description": "The Minecraft name to unlink"}],
        ),
    )
}
